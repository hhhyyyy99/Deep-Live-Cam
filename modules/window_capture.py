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
import traceback
from typing import List, Optional, Tuple

import cv2
import numpy as np

_IS_WINDOWS = sys.platform == "win32"

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
        self.frame_callback = None
        self._last_frame: Optional[np.ndarray] = None
        self.last_error: str = ""
        self._capture_backend: str = "pywin32"
        self._wgc_capture = None
        self._wgc_control = None
        self._wgc_lock = threading.Lock()
        self._wgc_frame: Optional[np.ndarray] = None
        self._wgc_closed = threading.Event()
        self._wgc_frame_ready = threading.Event()

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

        self.actual_fps = float(fps)
        self.is_running = True
        if self._start_windows_graphics_capture():
            print(f"[WindowCapturer] Started with Windows Graphics Capture, "
                  f"hwnd={self._hwnd:#x}", flush=True)
            return True

        print(f"[WindowCapturer] Started, hwnd={self._hwnd:#x}, "
              f"{self.actual_width}x{self.actual_height}", flush=True)
        return True

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
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
                with self._wgc_lock:
                    self._wgc_frame = bgr
                self._wgc_frame_ready.set()

            @capture.event
            def on_closed():
                self._wgc_closed.set()

            self._wgc_capture = capture
            self._wgc_control = capture.start_free_threaded()
            self._capture_backend = "wgc"
            return True
        except Exception as exc:
            self._set_error(f"Windows Graphics Capture start failed: {exc}")
            self._stop_windows_graphics_capture()
            return False

    def _read_windows_graphics_capture(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._wgc_closed.is_set():
            self._set_error("Windows Graphics Capture session closed.")
            self.is_running = False
            return self._return_last()

        if self._wgc_frame is None:
            self._wgc_frame_ready.wait(timeout=1.0)

        with self._wgc_lock:
            bgr = None if self._wgc_frame is None else self._wgc_frame.copy()
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
        self._wgc_closed.set()
        self._wgc_frame_ready.clear()
        with self._wgc_lock:
            self._wgc_frame = None
        if control is None:
            return
        try:
            control.stop()
        except Exception:
            pass

    def _capture_from_window_dc(self, w: int, h: int) -> Optional[np.ndarray]:
        hwnd_dc = None
        try:
            hwnd_dc = win32gui.GetDC(self._hwnd)
            if not hwnd_dc:
                self._set_error("GetDC returned 0")
                return None
            return self._copy_dc_region(
                hwnd_dc, w, h, 0, 0, print_window_fallback=True
            )
        except Exception as exc:
            self._set_error(f"window DC capture failed: {exc}")
            return None
        finally:
            try:
                if hwnd_dc:
                    win32gui.ReleaseDC(self._hwnd, hwnd_dc)
            except Exception:
                pass

    def _copy_dc_region(
        self,
        source_dc_handle,
        w: int,
        h: int,
        src_x: int,
        src_y: int,
        print_window_fallback: bool = False,
    ) -> Optional[np.ndarray]:
        mfc_dc = None
        save_dc = None
        bitmap = None
        try:
            mfc_dc = win32ui.CreateDCFromHandle(source_dc_handle)
            save_dc = mfc_dc.CreateCompatibleDC()

            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)

            copied = False
            if print_window_fallback:
                copied = self._print_window(save_dc)
            if not copied:
                save_dc.BitBlt(
                    (0, 0),
                    (w, h),
                    mfc_dc,
                    (src_x, src_y),
                    win32con.SRCCOPY | getattr(win32con, "CAPTUREBLT", 0),
                )

            return self._bitmap_to_bgr(bitmap, w, h)
        finally:
            try:
                if save_dc:
                    save_dc.DeleteDC()
            except Exception:
                pass
            try:
                if mfc_dc:
                    mfc_dc.DeleteDC()
            except Exception:
                pass
            try:
                if bitmap:
                    win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:
                pass

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

    def _set_error(self, message: str) -> None:
        self.last_error = message
        print(f"[WindowCapturer] {message}", flush=True)
