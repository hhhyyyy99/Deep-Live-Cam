# Design: Window Capture FPS Improvements

## Architecture And Boundaries

The live preview architecture stays intact:

```text
WindowCapturer.read()
  -> _CaptureWorker raw queue
  -> _ProcessingWorker face processing
  -> WebcamPreviewWindow display timer
```

The change is scoped to `modules/window_capture.py` and small observability
hooks in `modules/ui.py`. Face-swap processors should remain unchanged unless
instrumentation proves the bottleneck is not capture.

## Capture Backend Observability

`WindowCapturer` should expose/log:

- active backend: `wgc` or `pywin32`
- capture dimensions
- measured raw capture FPS
- last capture error

The existing overlay FPS in `_ProcessingWorker` is processed FPS. It must not
be treated as raw capture FPS. If UI text is added, it should be concise and
avoid changing normal workflows.

## WGC Data Flow

Windows Graphics Capture is event-driven:

```text
WGC callback receives frame
  -> copy/own one BGR ndarray
  -> increment frame sequence
  -> signal frame_ready

read()
  -> wait briefly for sequence > last_read_sequence
  -> return latest frame once
```

The key behavior change is that `read()` should not return the same WGC frame
as if it were a fresh capture. This keeps `_CaptureWorker` from flooding the
processing queue with duplicate frames and better matches camera `read()`
semantics.

The callback still needs to own frame memory because the lifetime of
`frame.frame_buffer` is controlled by the capture library. After that, `read()`
can safely return the stored ndarray reference without making another full
copy, because each callback replaces `_wgc_frame` rather than mutating the
previous ndarray.

## pywin32 Fallback Data Flow

The pywin32 backend is fallback-only and may be slower than WGC for modern
hardware-accelerated windows. It should still:

- report that it is the active backend
- reuse capture resources where practical
- rebuild resources on resize
- release Win32 resources cleanly
- avoid crashing when `PrintWindow` or `BitBlt` fails

If a trade-off is required, prefer reliability and clear diagnostics over
promising OBS-equivalent FPS from the fallback path.

## Compatibility

- Preserve `WindowCapturer.start(width=0, height=0, fps=30) -> bool`.
- Preserve `WindowCapturer.read() -> (bool, Optional[np.ndarray])`.
- Preserve `WindowCapturer.release()`.
- Preserve `set_frame_callback()`.
- Preserve `VideoCapturer` behavior.

## Error Handling

- If the selected window closes, `read()` returns failure after setting
  `last_error`.
- If the window is minimized or has no client area, return the last valid frame
  when available.
- If WGC starts but produces no frames, report a clear error and avoid blocking
  indefinitely.
- If pywin32 resources fail, clean up partial resources and keep the existing
  last-frame fallback.

## Rollback

Changes are local. Reverting `modules/window_capture.py` and any small
`modules/ui.py` observability edits should restore the current behavior.
