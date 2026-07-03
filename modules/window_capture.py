"""Window capture backends (Windows only).

Provides WindowCapturer with the same start/read/release interface as
VideoCapturer, so it can be used as a drop-in replacement in the live
preview pipeline.

Captures the selected window's content. Windows Graphics Capture is used first
because it captures hardware-accelerated apps such as browsers by window handle
without reading unrelated screen pixels. If that backend is unavailable, the
legacy pywin32 PrintWindow/window DC path remains as a fallback.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from typing import Any, List, Optional, Tuple

import numpy as np

_IS_WINDOWS = sys.platform == "win32"
_WGC_INITIAL_FRAME_TIMEOUT_SECONDS = 1.0
_WGC_READ_TIMEOUT_SECONDS = 0.2
_FPS_UPDATE_INTERVAL_SECONDS = 0.5
_FPS_LOG_INTERVAL_SECONDS = 5.0

if _IS_WINDOWS:
    import win32gui
    import win32ui
    import win32con


def list_windows() -> List[dict]:
    """Return visible windows with their hwnd, title, and dimensions."""
    if not _IS_WINDOWS:
        return []

    windows: List[dict] = []

    def _enum_cb(hwnd, _extra):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title or not title.strip():
            return True
        try:
            rect = win32gui.GetWindowRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if w < 100 or h < 100:
                return True
            windows.append({
                "hwnd": hwnd,
                "title": title,
                "x": rect[0],
                "y": rect[1],
                "width": w,
                "height": h,
            })
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_enum_cb, None)
    return windows


class WindowCapturer:
    """Captures a specific window by HWND using BitBlt.

    Same interface as VideoCapturer: start() / read() / release().
    Returns the last successful frame on transient failures instead of
    reporting failure immediately.
    """

    def __init__(self, hwnd: int):
        self._hwnd = hwnd
        self.is_running = False
        self.actual_width: int = 0
        self.actual_height: int = 0
        self.actual_fps: float = 30.0
        self.capture_fps: float = 0.0
        self.frame_callback = None
        self._last_frame: Optional[np.ndarray] = None
        self.last_error: str = ""
        self.transient_no_frame: bool = False
        self._capture_backend: str = "pywin32"
        self._wgc_capture = None
        self._wgc_control = None
        self._wgc_lock = threading.Lock()
        self._wgc_condition = threading.Condition(self._wgc_lock)
        self._wgc_frame: Optional[np.ndarray] = None
        self._wgc_frame_sequence: int = 0
        self._wgc_last_read_sequence: int = 0
        self._wgc_closed = threading.Event()
        self._wgc_frame_ready = threading.Event()
        self._requested_fps: float = 30.0
        self._fps_window_started_at: float = 0.0
        self._fps_window_frames: int = 0
        self._fps_last_log_at: float = 0.0
        self._pywin32_hwnd_dc: Any = None
        self._pywin32_mfc_dc: Any = None
        self._pywin32_save_dc: Any = None
        self._pywin32_bitmap: Any = None
        self._pywin32_old_bitmap: Any = None
        self._pywin32_size: Tuple[int, int] = (0, 0)

    @property
    def capture_backend(self) -> str:
        return self._capture_backend

    def start(self, width: int = 0, height: int = 0, fps: int = 30) -> bool:
        if not _IS_WINDOWS:
            self._set_error("Only supported on Windows.")
            return False
        if not win32gui.IsWindow(self._hwnd):
            self._set_error(f"Invalid window handle: {self._hwnd:#x}")
            return False

        try:
            self.actual_width, self.actual_height = self._get_capture_size()
        except Exception as exc:
            self._set_error(f"Failed to read window size: {exc}")
            self.actual_width = width or 640
            self.actual_height = height or 480

        self._requested_fps = max(float(fps), 1.0)
        self.actual_fps = self._requested_fps
        self.capture_fps = 0.0
        self.transient_no_frame = False
        self._reset_fps_stats()
        self.is_running = True
        if self._start_windows_graphics_capture():
            print(
                f"[WindowCapturer] Started backend=wgc hwnd={self._hwnd:#x} "
                f"size={self.actual_width}x{self.actual_height} "
                f"requested={self._requested_fps:.0f}fps",
                flush=True,
            )
            return True

        fallback_reason = self.last_error or "Windows Graphics Capture unavailable"
        self._capture_backend = "pywin32"
        print(
            f"[WindowCapturer] Started backend=pywin32 hwnd={self._hwnd:#x} "
            f"size={self.actual_width}x{self.actual_height} "
            f"requested={self._requested_fps:.0f}fps "
            f"fallback_reason={fallback_reason}",
            flush=True,
        )
        return True

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        self.transient_no_frame = False
        if not self.is_running:
            return False, None
        if self._capture_backend == "wgc":
            return self._read_windows_graphics_capture()

        try:
            if not win32gui.IsWindow(self._hwnd):
                self._set_error(f"Window {self._hwnd:#x} no longer exists")
                self.is_running = False
                return False, None

            w, h = self._get_capture_size()
            if w <= 0 or h <= 0:
                self._set_error("Window has no capturable client area; it may be minimized.")
                if self._last_frame is not None:
                    return True, self._last_frame
                return False, None

            bgr = self._capture_from_window_dc(w, h)
            if bgr is None:
                return self._return_last()

            self._last_frame = bgr
            self.last_error = ""
            self._record_capture_frame()
            if w != self.actual_width or h != self.actual_height:
                self.actual_width = w
                self.actual_height = h

            if self.frame_callback:
                self.frame_callback(bgr)
            return True, bgr

        except Exception as e:
            self._set_error(f"read error: {e}")
            traceback.print_exc()
            return self._return_last()

    def _return_last(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Return the last captured frame if available."""
        if self._last_frame is not None:
            return True, self._last_frame
        return False, None

    def release(self) -> None:
        self.is_running = False
        self._last_frame = None
        self._stop_windows_graphics_capture()
        self._release_pywin32_resources()

    def set_frame_callback(self, callback) -> None:
        self.frame_callback = callback

    def _get_capture_size(self) -> Tuple[int, int]:
        left, top, right, bottom = win32gui.GetClientRect(self._hwnd)
        return right - left, bottom - top

    def _start_windows_graphics_capture(self) -> bool:
        try:
            from windows_capture import WindowsCapture
        except Exception as exc:
            self._set_error(f"Windows Graphics Capture unavailable: {exc}")
            return False

        try:
            with self._wgc_condition:
                self._wgc_frame = None
                self._wgc_frame_sequence = 0
                self._wgc_last_read_sequence = 0
                self._wgc_closed.clear()
                self._wgc_frame_ready.clear()

            capture = WindowsCapture(
                cursor_capture=False,
                draw_border=False,
                monitor_index=None,
                window_name=None,
                window_hwnd=int(self._hwnd),
            )

            @capture.event
            def on_frame_arrived(frame, _capture_control):
                bgr = frame.frame_buffer[:, :, :3].copy()
                now = time.perf_counter()
                with self._wgc_condition:
                    self._wgc_frame = bgr
                    self._wgc_frame_sequence += 1
                    self._wgc_frame_ready.set()
                    self._wgc_condition.notify_all()
                self._record_capture_frame(now)

            @capture.event
            def on_closed():
                with self._wgc_condition:
                    self._wgc_closed.set()
                    self._wgc_condition.notify_all()

            self._wgc_capture = capture
            self._wgc_control = capture.start_free_threaded()
            self._capture_backend = "wgc"
            self.last_error = ""
            return True
        except Exception as exc:
            self._set_error(f"Windows Graphics Capture start failed: {exc}")
            self._stop_windows_graphics_capture()
            return False

    def _read_windows_graphics_capture(self) -> Tuple[bool, Optional[np.ndarray]]:
        timeout = _WGC_READ_TIMEOUT_SECONDS
        if self._wgc_last_read_sequence == 0 and self._last_frame is None:
            timeout = _WGC_INITIAL_FRAME_TIMEOUT_SECONDS

        deadline = time.perf_counter() + timeout
        with self._wgc_condition:
            while (
                self.is_running
                and not self._wgc_closed.is_set()
                and (
                    self._wgc_frame is None
                    or self._wgc_frame_sequence <= self._wgc_last_read_sequence
                )
            ):
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    self.transient_no_frame = True
                    return False, None
                self._wgc_condition.wait(timeout=remaining)

            if self._wgc_closed.is_set():
                self._set_error("Windows Graphics Capture session closed.")
                self.is_running = False
                return self._return_last()

            bgr = self._wgc_frame
            sequence = self._wgc_frame_sequence
            self._wgc_frame = None
            self._wgc_last_read_sequence = sequence
            self._wgc_frame_ready.clear()

        if bgr is None:
            self._set_error("Windows Graphics Capture returned no frames yet.")
            return self._return_last()

        self._last_frame = bgr
        self.last_error = ""
        self.actual_height, self.actual_width = bgr.shape[:2]
        if self.frame_callback:
            self.frame_callback(bgr)
        return True, bgr

    def _stop_windows_graphics_capture(self) -> None:
        control = self._wgc_control
        self._wgc_control = None
        self._wgc_capture = None
        self._capture_backend = "pywin32"
        with self._wgc_condition:
            self._wgc_closed.set()
            self._wgc_frame_ready.clear()
            self._wgc_frame = None
            self._wgc_frame_sequence = 0
            self._wgc_last_read_sequence = 0
            self._wgc_condition.notify_all()
        if control is None:
            return
        try:
            control.stop()
        except Exception:
            pass

    def _capture_from_window_dc(self, w: int, h: int) -> Optional[np.ndarray]:
        if not self._ensure_pywin32_resources(w, h):
            return None
        try:
            copied = self._print_window(self._pywin32_save_dc)
            if not copied:
                self._pywin32_save_dc.BitBlt(
                    (0, 0),
                    (w, h),
                    self._pywin32_mfc_dc,
                    (0, 0),
                    win32con.SRCCOPY | getattr(win32con, "CAPTUREBLT", 0),
                )
            return self._bitmap_to_bgr(self._pywin32_bitmap, w, h)
        except Exception as exc:
            self._set_error(f"window DC capture failed: {exc}")
            self._release_pywin32_resources()
            return None

    def _ensure_pywin32_resources(self, w: int, h: int) -> bool:
        if (
            self._pywin32_size == (w, h)
            and self._pywin32_hwnd_dc is not None
            and self._pywin32_mfc_dc is not None
            and self._pywin32_save_dc is not None
            and self._pywin32_bitmap is not None
        ):
            return True

        self._release_pywin32_resources()
        try:
            self._pywin32_hwnd_dc = win32gui.GetDC(self._hwnd)
            if not self._pywin32_hwnd_dc:
                self._set_error("GetDC returned 0")
                return False

            self._pywin32_mfc_dc = win32ui.CreateDCFromHandle(self._pywin32_hwnd_dc)
            self._pywin32_save_dc = self._pywin32_mfc_dc.CreateCompatibleDC()
            self._pywin32_bitmap = win32ui.CreateBitmap()
            self._pywin32_bitmap.CreateCompatibleBitmap(self._pywin32_mfc_dc, w, h)
            self._pywin32_old_bitmap = self._pywin32_save_dc.SelectObject(
                self._pywin32_bitmap
            )
            self._pywin32_size = (w, h)
            return True
        except Exception as exc:
            self._set_error(f"pywin32 resource setup failed: {exc}")
            self._release_pywin32_resources()
            return False

    def _release_pywin32_resources(self) -> None:
        try:
            if self._pywin32_save_dc is not None and self._pywin32_old_bitmap is not None:
                self._pywin32_save_dc.SelectObject(self._pywin32_old_bitmap)
        except Exception:
            pass
        try:
            if self._pywin32_bitmap is not None:
                win32gui.DeleteObject(self._pywin32_bitmap.GetHandle())
        except Exception:
            pass
        try:
            if self._pywin32_save_dc is not None:
                self._pywin32_save_dc.DeleteDC()
        except Exception:
            pass
        try:
            if self._pywin32_mfc_dc is not None:
                self._pywin32_mfc_dc.DeleteDC()
        except Exception:
            pass
        try:
            if self._pywin32_hwnd_dc is not None:
                win32gui.ReleaseDC(self._hwnd, self._pywin32_hwnd_dc)
        except Exception:
            pass

        self._pywin32_hwnd_dc = None
        self._pywin32_mfc_dc = None
        self._pywin32_save_dc = None
        self._pywin32_bitmap = None
        self._pywin32_old_bitmap = None
        self._pywin32_size = (0, 0)

    def _bitmap_to_bgr(self, bitmap, w: int, h: int) -> Optional[np.ndarray]:
        bmp_bits = bitmap.GetBitmapBits(True)
        stride = w * 4
        expected_size = stride * h
        if len(bmp_bits) < expected_size:
            self._set_error(f"Bitmap too small: {len(bmp_bits)} < {expected_size}")
            return None

        if isinstance(bmp_bits, (tuple, list)):
            frame_data = np.asarray(bmp_bits, dtype=np.uint8)
        else:
            frame_data = np.frombuffer(bmp_bits, dtype=np.uint8)
        frame = frame_data[:expected_size].reshape((h, w, 4))
        return frame[:, :, :3].copy()

    def _print_window(self, save_dc) -> bool:
        try:
            # PW_CLIENTONLY | PW_RENDERFULLCONTENT captures more reliably than
            # BitBlt for several hardware-accelerated Windows applications.
            return bool(win32gui.PrintWindow(self._hwnd, save_dc.GetSafeHdc(), 3))
        except Exception:
            return False

    def _reset_fps_stats(self) -> None:
        now = time.perf_counter()
        self._fps_window_started_at = now
        self._fps_window_frames = 0
        self._fps_last_log_at = now

    def _record_capture_frame(self, now: Optional[float] = None) -> None:
        now = time.perf_counter() if now is None else now
        if self._fps_window_started_at <= 0:
            self._fps_window_started_at = now
        self._fps_window_frames += 1
        elapsed = now - self._fps_window_started_at
        if elapsed < _FPS_UPDATE_INTERVAL_SECONDS:
            return

        measured_fps = self._fps_window_frames / elapsed
        self.capture_fps = measured_fps
        self.actual_fps = measured_fps
        self._fps_window_started_at = now
        self._fps_window_frames = 0

        if now - self._fps_last_log_at >= _FPS_LOG_INTERVAL_SECONDS:
            print(
                f"[WindowCapturer] backend={self._capture_backend} "
                f"capture_fps={measured_fps:.1f} "
                f"size={self.actual_width}x{self.actual_height}",
                flush=True,
            )
            self._fps_last_log_at = now

    def _set_error(self, message: str) -> None:
        self.transient_no_frame = False
        self.last_error = message
        print(f"[WindowCapturer] {message}", flush=True)
