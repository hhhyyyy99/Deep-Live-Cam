"""Window capture via Win32 API (Windows only).

Provides WindowCapturer with the same start/read/release interface as
VideoCapturer, so it can be used as a drop-in replacement in the live
preview pipeline.

Captures the selected window's client area. It first tries PrintWindow/window
DC capture, then falls back to desktop-composited pixels when the window path
returns a blank frame. That fallback works better for hardware-accelerated apps
such as browsers. EnumWindows lists available windows for the picker dialog.
"""

from __future__ import annotations

import sys
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
        print(f"[WindowCapturer] Started, hwnd={self._hwnd:#x}, "
              f"{self.actual_width}x{self.actual_height}", flush=True)
        return True

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self.is_running:
            return False, None

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
            if bgr is None or self._is_probably_blank_frame(bgr):
                desktop_bgr = self._capture_from_desktop(w, h)
                if desktop_bgr is not None:
                    bgr = desktop_bgr
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

    def set_frame_callback(self, callback) -> None:
        self.frame_callback = callback

    def _get_capture_size(self) -> Tuple[int, int]:
        left, top, right, bottom = win32gui.GetClientRect(self._hwnd)
        return right - left, bottom - top

    def _capture_from_desktop(self, w: int, h: int) -> Optional[np.ndarray]:
        if win32gui.IsIconic(self._hwnd):
            self._set_error("Window is minimized.")
            return None

        desktop_dc = None
        try:
            x, y = win32gui.ClientToScreen(self._hwnd, (0, 0))
            desktop_dc = win32gui.GetDC(0)
            if not desktop_dc:
                self._set_error("Desktop GetDC returned 0")
                return None
            return self._copy_dc_region(desktop_dc, w, h, x, y)
        except Exception as exc:
            self._set_error(f"desktop capture failed: {exc}")
            return None
        finally:
            try:
                if desktop_dc:
                    win32gui.ReleaseDC(0, desktop_dc)
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

    def _is_probably_blank_frame(self, bgr: np.ndarray) -> bool:
        if bgr.size == 0:
            return True
        sample = bgr[:: max(1, bgr.shape[0] // 120), :: max(1, bgr.shape[1] // 120)]
        return float(sample.mean()) < 2.0 and float(sample.std()) < 2.0

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
