import sys
import types
import unittest
from unittest.mock import patch

sys.modules.setdefault(
    "cv2",
    types.SimpleNamespace(
        IMREAD_COLOR=1,
        imdecode=lambda *_args, **_kwargs: None,
        imencode=lambda *_args, **_kwargs: (
            True,
            types.SimpleNamespace(tofile=lambda *_args, **_kwargs: None),
        ),
    ),
)
sys.modules.setdefault(
    "numpy",
    types.SimpleNamespace(uint8=object, fromfile=lambda *_args, **_kwargs: b""),
)

from modules import window_capture


class _FakeFrame:
    shape = (2, 3, 3)


class WindowCaptureWgcReadTests(unittest.TestCase):
    def setUp(self):
        self.capturer = window_capture.WindowCapturer(123)
        self.capturer.is_running = True
        self.capturer._capture_backend = "wgc"

    def _push_wgc_frame(self, frame):
        with self.capturer._wgc_condition:
            self.capturer._wgc_frame = frame
            self.capturer._wgc_frame_sequence += 1
            self.capturer._wgc_condition.notify_all()

    def test_wgc_read_hands_off_each_frame_once(self):
        first = _FakeFrame()
        self._push_wgc_frame(first)

        ok, frame = self.capturer.read()

        self.assertTrue(ok)
        self.assertIs(frame, first)
        self.assertIsNone(self.capturer._wgc_frame)
        self.assertEqual(self.capturer._wgc_last_read_sequence, 1)
        self.assertFalse(self.capturer.transient_no_frame)

        with patch.object(window_capture, "_WGC_READ_TIMEOUT_SECONDS", 0.001):
            ok, frame = self.capturer.read()

        self.assertFalse(ok)
        self.assertIsNone(frame)
        self.assertTrue(self.capturer.transient_no_frame)
        self.assertEqual(self.capturer.last_error, "")

        second = _FakeFrame()
        self._push_wgc_frame(second)

        ok, frame = self.capturer.read()

        self.assertTrue(ok)
        self.assertIs(frame, second)
        self.assertIsNone(self.capturer._wgc_frame)
        self.assertEqual(self.capturer._wgc_last_read_sequence, 2)
        self.assertFalse(self.capturer.transient_no_frame)

    def test_capture_fps_updates_actual_fps(self):
        self.capturer._capture_backend = "wgc"
        self.capturer._fps_window_started_at = 10.0
        self.capturer._fps_last_log_at = 10.0
        self.capturer._fps_window_frames = 14

        self.capturer._record_capture_frame(now=10.5)

        self.assertEqual(self.capturer.capture_fps, 30.0)
        self.assertEqual(self.capturer.actual_fps, 30.0)


if __name__ == "__main__":
    unittest.main()
