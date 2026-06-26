"""Screen/window region capturer using mss.

Provides a ScreenCapturer class with the same interface as VideoCapturer
(start/read/release) so it can be used as a drop-in replacement in the
live preview pipeline.
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

import cv2
import numpy as np

try:
    import mss
    import mss.tools
except ImportError:
    mss = None


class ScreenCapturer:
    """Captures a fixed screen region at a target FPS."""

    def __init__(self, region: Tuple[int, int, int, int]):
        """region = (left, top, width, height) in screen pixels."""
        self.region = {
            "left": region[0],
            "top": region[1],
            "width": region[2],
            "height": region[3],
        }
        self._sct: Optional[mss.mss] = None
        self.is_running = False
        self.actual_width: int = region[2]
        self.actual_height: int = region[3]
        self.actual_fps: float = 0.0
        self.frame_callback = None

    def start(self, width: int = 0, height: int = 0, fps: int = 30) -> bool:
        if mss is None:
            print("mss is not installed. Run: pip install mss")
            return False
        try:
            self._sct = mss.mss()
            # Measure actual capture FPS
            self.actual_fps = self._measure_fps(fallback=float(fps))
            self.is_running = True
            return True
        except Exception as e:
            print(f"Failed to start screen capture: {e}")
            return False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self.is_running or self._sct is None:
            return False, None
        try:
            sct_img = self._sct.grab(self.region)
            # mss returns BGRA; convert to BGR for OpenCV compatibility
            frame = np.array(sct_img)
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            if self.frame_callback:
                self.frame_callback(bgr)
            return True, bgr
        except Exception:
            return False, None

    def release(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None
        self.is_running = False

    def _measure_fps(self, warmup: int = 5, sample: int = 20,
                     fallback: float = 30.0) -> float:
        if self._sct is None:
            return fallback
        try:
            for _ in range(warmup):
                self._sct.grab(self.region)
            t0 = time.perf_counter()
            for _ in range(sample):
                self._sct.grab(self.region)
            elapsed = time.perf_counter() - t0
            if elapsed <= 0:
                return fallback
            return sample / elapsed
        except Exception:
            return fallback

    def set_frame_callback(self, callback) -> None:
        self.frame_callback = callback


def get_screen_monitors():
    """Return list of available monitors (dicts with left/top/width/height)."""
    if mss is None:
        return []
    with mss.mss() as sct:
        # sct.monitors[0] is the "all monitors" virtual screen
        # sct.monitors[1..N] are individual monitors
        return [dict(m) for m in sct.monitors[1:]]
