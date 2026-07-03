# Improve window capture frame rate

## Goal

Bring live face swap through Window Capture closer to OBS-mode smoothness.
The reported symptom is that OBS mode can hold about 30 FPS, while the
current Window Capture path is about 6 FPS.

The immediate value is a usable low-latency window-capture live preview and
enough runtime diagnostics to tell whether the bottleneck is the capture
backend or the face-swap processing pipeline.

## Confirmed Facts

- `modules/window_capture.py` is the current window-capture implementation.
- The current implementation is Windows-only:
  - `list_windows()` enumerates visible Win32 windows by `hwnd`.
  - `WindowCapturer.start()` tries `windows_capture.WindowsCapture` first.
  - If Windows Graphics Capture fails, it falls back to a pywin32 window DC
    path.
- `requirements.txt` includes `windows-capture>=2.0.0` and `pywin32` on
  Windows.
- `README.md` says Window Capture uses Windows Graphics Capture for modern
  hardware-accelerated windows, with pywin32 as a fallback.
- `WebcamPreviewWindow` reuses the same live preview pipeline for webcams and
  window capture:
  - `_CaptureWorker` continuously calls `cap.read()` into a bounded queue.
  - `_ProcessingWorker` performs detection, face swap, optional enhancement,
    and optional FPS overlay.
  - The visible FPS overlay is calculated after processing, not at the raw
    capture source.
- `WindowCapturer.actual_fps` is currently set to the requested FPS, not
  measured from captured frames.
- The WGC path currently stores the latest frame in an async callback, but
  `read()` can return that same frame repeatedly without waiting for a new
  capture frame. This differs from `cv2.VideoCapture.read()`, which naturally
  blocks on the camera frame source.
- The WGC path copies frame data in the callback and copies again in `read()`.
- The pywin32 fallback allocates DC/bitmap resources and calls
  `PrintWindow`/`BitBlt` per read, which is a plausible low-FPS path.
- There is no user-visible or log-visible separation between:
  - active backend (`wgc` vs `pywin32`)
  - raw capture FPS
  - processed/display FPS
- User confirmed the low-FPS Window Capture runtime is Windows.

## Requirements

1. Preserve the existing user flow: select a source face, click Window
   Capture, choose a window, and see live swapped output.
2. Preserve webcam live mode behavior and the `VideoCapturer`-compatible
   `start()` / `read()` / `release()` interface.
3. Make the active window-capture backend observable in logs/status so slow
   fallback behavior can be identified.
4. Measure or expose raw capture FPS separately from processed preview FPS.
5. Avoid processing duplicate WGC frames when no new source frame has arrived.
6. Reduce unnecessary frame copies in the WGC path where safe.
7. Improve the pywin32 fallback enough that it remains usable, while making it
   clear that WGC is the intended high-performance backend.
8. Keep behavior stable when the selected window is resized, minimized, closed,
   or temporarily fails to provide frames.

## Acceptance Criteria

- [ ] Window Capture logs the selected backend (`wgc` or `pywin32`) and capture
      dimensions at startup.
- [ ] Raw capture FPS can be distinguished from processed FPS during live
      preview or via logs.
- [ ] WGC `read()` yields new source frames instead of repeatedly feeding the
      processing pipeline with the same frame.
- [ ] WGC no longer performs an avoidable second full-frame copy on every
      `read()`.
- [ ] pywin32 fallback still captures frames and handles window resize without
      crashing.
- [ ] If WGC is unavailable and pywin32 fallback is used, the user/developer can
      tell from logs/status.
- [ ] Existing tests still pass.
- [ ] Modified Python files pass syntax/import-level validation appropriate for
      the current platform.

## Out of Scope

- Replacing the live pipeline with OBS/virtual-camera integration.
- Adding audio capture.
- Multi-window simultaneous capture.
- Rewriting non-Windows window capture backends.
- Changing the face-swap model or quality settings unless profiling proves
  processing, not capture, is the dominant bottleneck.

## Open Questions

- None blocking.

## Notes

The older `06-26-window-capture` Trellis task describes a Linux/Wayland
PipeWire portal design, but the current repository implementation is
Windows-only. Treat that older artifact as historical context unless the user
confirms this low-FPS report is from a Linux/Wayland runtime.
