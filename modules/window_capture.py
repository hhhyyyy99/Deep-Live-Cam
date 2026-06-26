"""Window capture via Win32 API (Windows only).

Provides WindowCapturer with the same start/read/release interface as
VideoCapturer, so it can be used as a drop-in replacement in the live
preview pipeline.

Uses BitBlt to capture a specific window's client area content.
EnumWindows lists available windows for the picker dialog.
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

    def start(self, width: int = 0, height: int = 0, fps: int = 30) -> bool:
        if not _IS_WINDOWS:
            print("[WindowCapturer] Only supported on Windows.")
            return False
        if not win32gui.IsWindow(self._hwnd):
            print(f"[WindowCapturer] Invalid window handle: {self._hwnd:#x}")
            return False

        try:
            rect = win32gui.GetWindowRect(self._hwnd)
            self.actual_width = rect[2] - rect[0]
            self.actual_height = rect[3] - rect[1]
        except Exception:
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

        hwnd_dc = None
        mfc_dc = None
        save_dc = None
        bitmap = None

        try:
            if not win32gui.IsWindow(self._hwnd):
                print(f"[WindowCapturer] Window {self._hwnd:#x} no longer exists")
                self.is_running = False
                return False, None

            left, top, right, bottom = win32gui.GetClientRect(self._hwnd)
            w = right - left
            h = bottom - top
            if w <= 0 or h <= 0:
                # Window might be minimized — return last frame
                if self._last_frame is not None:
                    return True, self._last_frame
                return False, None

            hwnd_dc = win32gui.GetDC(self._hwnd)
            if not hwnd_dc:
                print("[WindowCapturer] GetDC returned 0")
                return self._return_last()

            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)

            save_dc.BitBlt(
                (0, 0), (w, h), mfc_dc, (0, 0), win32con.SRCCOPY,
            )

            bmp_bits = bitmap.GetBitmapBits(False)
            stride = w * 4
            expected_size = stride * h
            if len(bmp_bits) < expected_size:
                print(f"[WindowCapturer] Bitmap too small: {len(bmp_bits)} < {expected_size}")
                return self._return_last()

            frame = np.frombuffer(bmp_bits, dtype=np.uint8)[:expected_size].reshape((h, w, 4))
            bgr = frame[:, :, :3].copy()

            self._last_frame = bgr
            if w != self.actual_width or h != self.actual_height:
                self.actual_width = w
                self.actual_height = h

            if self.frame_callback:
                self.frame_callback(bgr)
            return True, bgr

        except Exception as e:
            print(f"[WindowCapturer] read error: {e}")
            traceback.print_exc()
            return self._return_last()

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
                if hwnd_dc:
                    win32gui.ReleaseDC(self._hwnd, hwnd_dc)
            except Exception:
                pass
            try:
                if bitmap:
                    win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:
                pass

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
