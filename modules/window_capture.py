"""Window capture via Win32 API (Windows only).

Provides WindowCapturer with the same start/read/release interface as
VideoCapturer, so it can be used as a drop-in replacement in the live
preview pipeline.

Uses win32gui.PrintWindow to capture a specific window's content, even
when partially occluded. EnumWindows lists available windows for the
picker dialog.
"""

from __future__ import annotations

import sys
import time
from typing import List, Optional, Tuple

import cv2
import numpy as np

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import win32gui
    import win32ui
    import win32con
    import win32process


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
        # Skip tiny windows (tray icons, tooltips, etc.)
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
        return True

    win32gui.EnumWindows(_enum_cb, None)
    return windows


class WindowCapturer:
    """Captures a specific window by HWND using PrintWindow.

    Same interface as VideoCapturer: start() / read() / release().
    """

    def __init__(self, hwnd: int):
        self._hwnd = hwnd
        self.is_running = False
        self.actual_width: int = 0
        self.actual_height: int = 0
        self.actual_fps: float = 30.0
        self.frame_callback = None

    def start(self, width: int = 0, height: int = 0, fps: int = 30) -> bool:
        if not _IS_WINDOWS:
            print("Window capture is only supported on Windows.")
            return False
        if not win32gui.IsWindow(self._hwnd):
            print(f"Invalid window handle: {self._hwnd}")
            return False

        # Measure initial dimensions
        try:
            rect = win32gui.GetWindowRect(self._hwnd)
            self.actual_width = rect[2] - rect[0]
            self.actual_height = rect[3] - rect[1]
        except Exception:
            self.actual_width = width or 640
            self.actual_height = height or 480

        self.actual_fps = float(fps)
        self.is_running = True
        return True

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self.is_running:
            return False, None

        try:
            if not win32gui.IsWindow(self._hwnd):
                return False, None

            # Get current window client area dimensions
            left, top, right, bottom = win32gui.GetClientRect(self._hwnd)
            w = right - left
            h = bottom - top
            if w <= 0 or h <= 0:
                return False, None

            hwnd_dc = win32gui.GetDC(self._hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)

            # Use BitBlt for reliable color reproduction
            save_dc.BitBlt(
                (0, 0), (w, h), mfc_dc, (0, 0), win32con.SRCCOPY,
            )

            bmp_bits = bitmap.GetBitmapBits(False)
            # Bitmap is 32-bit BGRA with possible padding per row
            stride = w * 4
            frame = np.frombuffer(bmp_bits, dtype=np.uint8)[:stride * h].reshape((h, w, 4))
            # Extract BGR channels only (drop alpha)
            bgr = frame[:, :, :3].copy()

            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(self._hwnd, hwnd_dc)
            win32gui.DeleteObject(bitmap.GetHandle())

            if w != self.actual_width or h != self.actual_height:
                self.actual_width = w
                self.actual_height = h

            if self.frame_callback:
                self.frame_callback(bgr)
            return True, bgr

        except Exception:
            return False, None

    def release(self) -> None:
        self.is_running = False

    def set_frame_callback(self, callback) -> None:
        self.frame_callback = callback
