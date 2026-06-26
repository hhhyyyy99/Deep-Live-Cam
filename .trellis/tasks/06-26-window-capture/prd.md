# Add true window capture for real-time face swap

## Goal

Replace the current screen-region capture with true window capture (like OBS Window Capture). The user selects a specific open window (browser, media player, video call, etc.) and the system continuously captures that window's content — even when occluded by other windows — as the target for real-time face swapping.

## Confirmed Facts

- Current `ScreenCapturer` (mss-based) only captures a fixed screen region, not a specific window
- `mss` and `ScreenCapturer` are only used in the code we just added — safe to remove/replace
- User's system is **Wayland** (`XDG_SESSION_TYPE=wayland`) with XWayland (`DISPLAY=:0`)
- On XWayland, `python-xlib` + XComposite can only see X11-protocol windows, NOT native Wayland windows (GTK4, some Electron apps, etc.)
- For Wayland-native window capture, the **only** reliable method is `org.freedesktop.portal.ScreenCast` D-Bus API + PipeWire — this is what OBS uses on Wayland
- The existing `VideoCapturer` / `ScreenCapturer` interface (`start`/`read`/`release`) should be preserved for the new `WindowCapturer`
- BGRA→BGR conversion needed for OpenCV compatibility (`cv2.COLOR_BGRA2BGR`)

## Requirements

1. **Window enumeration** — `list_windows()` returns open windows with id, title, and dimensions, for the UI picker
2. **Window capture by ID** — `WindowCapturer(window_id)` captures that specific window's content frame-by-frame
3. **Occlusion-safe** — Uses XComposite to capture window content even when partially or fully behind other windows
4. **Resize handling** — Detects window size changes each frame and adapts the capture buffer
5. **Same interface** — `start()`/`read()`/`release()` matching `VideoCapturer` and `ScreenCapturer`
6. **UI window picker** — Replace the current "Screen Capture" button with a window selection dialog that lists open windows by title
7. **Replace `ScreenCapturer`** — The mss-based `ScreenCapturer` in `modules/screen_capture.py` should be replaced with the new `WindowCapturer`; the `RegionSelector` overlay can be removed
8. **Platform guard** — On non-X11 systems (Wayland, macOS, Windows), show a clear error or fall back gracefully

## Acceptance Criteria

- [ ] `list_windows()` returns a list of open windows with title and id on X11
- [ ] `WindowCapturer` captures a selected window's content as BGR numpy arrays
- [ ] Capture works even when the target window is partially/fully occluded
- [ ] Window resize is handled gracefully (no crash, adapts buffer)
- [ ] The UI shows a window picker dialog (not a screen region overlay)
- [ ] Selecting a window starts the live face-swap preview on that window's content
- [ ] map_faces mode works with window capture (LiveMapperDialog integration)
- [ ] `mss` dependency can be removed if no longer needed

## Out of Scope

- Wayland window capture (PipeWire/portal approach)
- macOS / Windows window capture (would use different APIs)
- Audio capture from windows
- Multi-window simultaneous capture

## Open Questions

1. **Capture backend**: **DECIDED — PipeWire + xdg-desktop-portal ScreenCast**
2. **mss removal**: **DECIDED — yes, remove all screen-region capture code**
3. **RegionSelector removal**: **DECIDED — yes**
4. **Window picker UI**: **DECIDED — system native dialog via xdg-desktop-portal**

## System Environment (confirmed)

- GStreamer 1.24.2 with `pipewiresrc` element available
- `dbus-python` available
- PipeWire 1.0.5 installed with GStreamer plugin
- `gi.repository.Gst` (GObject introspection) available
