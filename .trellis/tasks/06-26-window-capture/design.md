# Design: Window Capture via PipeWire + xdg-desktop-portal

## Architecture

```
User clicks "Window Capture"
        |
        v
D-Bus: org.freedesktop.portal.ScreenCast
  CreateSession → SelectSources(type=window) → Start
        |
        v  (system dialog: user picks a window)
        |
  portal returns: PipeWire fd + node_id
        |
        v
GStreamer pipeline:
  pipewiresrc fd=X path=Y ! videoconvert ! video/x-raw,format=BGRx ! appsink
        |
        v  (numpy BGR frames)
        |
WindowCapturer.read() → (True, bgr_frame)
        |
        v
CaptureWorker → ProcessingWorker (existing face swap pipeline)
        |
        v
WebcamPreviewWindow displays result
```

## Components

### 1. `modules/window_capture.py` (new, replaces `screen_capture.py`)

**`WindowCapturer`** class — same interface as `VideoCapturer`:
- `__init__()` — no args; session is created on `start()`
- `start()` — calls xdg-desktop-portal ScreenCast via D-Bus, starts GStreamer pipeline
- `read()` → `(bool, Optional[np.ndarray])` — pulls one BGR frame from appsink
- `release()` — stops GStreamer pipeline, closes D-Bus session
- `set_frame_callback()` — compatible with existing code

**`_ScreenCastPortal`** internal helper:
- Uses `Gio.DBusConnection` (GDBus) directly for D-Bus communication
- `call_with_unix_fd_list_sync()` for the `Start` call to properly receive PipeWire fd
- Methods: `create_session()`, `select_sources()`, `start()` → returns `(fd, node_id)`
- Handles `Response` signal via `signal_subscribe` + `GLib.MainLoop` with 120s timeout

**GStreamer pipeline** (inside `WindowCapturer`):
- `pipewiresrc fd={fd} path={node_id} ! videoconvert ! video/x-raw,format=BGRx ! appsink`
- `appsink` configured with `emit-signals=False`, synchronous pull via `emit("pull-sample")`
- Frame extraction: `sample.get_buffer()`, `buffer.extract_dup(0, size)`, reshape to `(h, w, 4)`, convert BGRx→BGR

### 2. `modules/ui.py` changes

**Remove:**
- `RegionSelector` class (fullscreen drag overlay)
- `_on_screen_capture` method (region selection flow)
- `_on_region_selected` method
- `ScreenCapturer` import and `_open_screen_preview` function
- `get_screen_monitors` import

**Modify:**
- `_build_camera_card()`: rename button from "Screen Capture" to "Window Capture"
- `_on_window_capture()`: new handler — calls `_open_window_preview()` directly (system dialog handles window selection)
- `WebcamPreviewWindow.__init__()`: already supports generic `capturer` param — no change needed
- `_open_window_preview()`: creates `WindowCapturer()`, passes to `WebcamPreviewWindow`
- `LiveMapperDialog`: change `screen_region` param to use `WindowCapturer` for map_faces mode

### 3. `modules/globals.py` changes

- Remove `screen_capture_region` variable
- No new globals needed (window selection is ephemeral, managed by D-Bus session)

### 4. `requirements.txt` changes

- Remove `mss>=9.0.0`
- No new Python deps needed (dbus-python and gi.repository.Gst are system packages)

### 5. `locales/zh.json` changes

- Remove "Screen Capture" translation
- Add "Window Capture" / "窗口捕获" translation

## Error Handling

- D-Bus call timeout (user doesn't select a window) → show status message, no crash
- PipeWire fd invalid → GStreamer pipeline fails to start → `start()` returns False
- Window closes mid-capture → `read()` returns `(False, None)` → `_CaptureWorker` sets stop_event
- Non-Wayland session detected → show error message "Window capture requires Wayland session"

## Dependencies (system-level, not pip)

All already installed on user's system:
- `gi.repository.Gio` / `gi.repository.GLib` — GDBus for D-Bus communication with fd passing
- `gi.repository.Gst` — GStreamer GObject introspection
- `gstreamer1.0-pipewire` — PipeWire GStreamer plugin
- `xdg-desktop-portal` — portal service (running on GNOME/KDE by default)
