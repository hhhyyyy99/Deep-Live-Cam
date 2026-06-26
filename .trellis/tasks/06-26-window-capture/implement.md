# Implementation Plan: Window Capture

## Checklist

### Phase 1: Clean up previous commit
- [ ] Delete `modules/screen_capture.py`
- [ ] Remove `RegionSelector` class from `modules/ui.py`
- [ ] Remove `_on_screen_capture`, `_on_region_selected` from `MainWindow`
- [ ] Remove `_open_screen_preview`, `_open_screen_live_mapper_dialog` from `modules/ui.py`
- [ ] Remove `ScreenCapturer`/`get_screen_monitors` imports from `modules/ui.py`
- [ ] Remove `screen_capture_region` from `modules/globals.py`
- [ ] Remove `mss>=9.0.0` from `requirements.txt`
- [ ] Remove `screen_region` param from `LiveMapperDialog`
- [ ] Update `locales/zh.json`: remove old screen capture strings

### Phase 2: Implement window_capture.py
- [ ] Create `modules/window_capture.py` with `_ScreenCastPortal` class
  - D-Bus session creation via `org.freedesktop.portal.ScreenCast`
  - `CreateSession`, `SelectSources(type=window)`, `Start` calls
  - Signal handling for async `Response` on session path
  - Returns `(pipe_wire_fd, node_id)`
- [ ] Create `WindowCapturer` class in same file
  - `start()`: call portal → get fd/node_id → build GStreamer pipeline
  - `read()`: pull sample from appsink → extract buffer → reshape to BGR numpy
  - `release()`: stop pipeline, close session
  - Handle window close / capture failure gracefully
- [ ] Add Wayland session detection (`os.environ.get("XDG_SESSION_TYPE")`)

### Phase 3: Wire up UI
- [ ] Update `_build_camera_card()`: "Screen Capture" → "Window Capture"
- [ ] Add `_on_window_capture()` to `MainWindow` — validates source_path, calls `_open_window_preview()`
- [ ] Add `_open_window_preview()` — creates `WindowCapturer()`, opens `WebcamPreviewWindow`
- [ ] Update `LiveMapperDialog` for window capture in map_faces mode
- [ ] Update `locales/zh.json`: add "Window Capture" / "窗口捕获"

### Phase 4: Verify
- [ ] Syntax check all modified files
- [ ] Test: click "Window Capture" → system dialog appears
- [ ] Test: select a window → live face-swap preview works
- [ ] Test: window resize handled gracefully
- [ ] Test: window close stops capture cleanly
- [ ] Test: map_faces mode with window capture

## Key files

| File | Action |
|---|---|
| `modules/screen_capture.py` | DELETE |
| `modules/window_capture.py` | CREATE |
| `modules/ui.py` | MODIFY (remove RegionSelector, add window capture) |
| `modules/globals.py` | MODIFY (remove screen_capture_region) |
| `requirements.txt` | MODIFY (remove mss) |
| `locales/zh.json` | MODIFY |

## Rollback

If window capture doesn't work:
1. The previous webcam-only functionality is untouched
2. Revert the commit to restore screen capture as fallback
