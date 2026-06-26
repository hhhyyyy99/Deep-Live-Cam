"""Window capture via PipeWire + xdg-desktop-portal ScreenCast (Wayland).

Provides WindowCapturer with the same start/read/release interface as
VideoCapturer, so it can be used as a drop-in replacement in the live
preview pipeline.

The portal session (which shows the system window-picker dialog) must be
started in a background thread so it doesn't block the Qt event loop.
The GStreamer pipeline is then created on the caller's thread.
"""

from __future__ import annotations

import os
import uuid
from typing import Optional, Tuple

import cv2
import numpy as np

try:
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib, Gst

    Gst.init(None)
    _HAS_GST = True
except (ImportError, ValueError):
    _HAS_GST = False

_PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
_PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
_PORTAL_IFACE = "org.freedesktop.portal.ScreenCast"


def _token() -> str:
    return "dlc" + uuid.uuid4().hex[:8]


def _request_path(conn: Gio.DBusConnection, handle_token: str) -> str:
    escaped = conn.get_unique_name().replace(".", "_").lstrip(":")
    return f"{_PORTAL_OBJECT_PATH}/request/{escaped}/{handle_token}"


def _extract_handle_token(params: GLib.Variant) -> Optional[str]:
    """Walk the variant tuple to find 'handle_token' in the embedded dict."""
    for i in range(params.n_children()):
        child = params.get_child_value(i)
        if child.get_type_string() == "a{sv}":
            d = child
            for j in range(d.n_children()):
                entry = d.get_child_value(j)
                if entry.get_child_value(0).get_string() == "handle_token":
                    return entry.get_child_value(1).get_variant().get_string()
    return None


def _portal_call(
    conn: Gio.DBusConnection,
    method: str,
    params: GLib.Variant,
    need_fd: bool = False,
) -> Tuple[dict, Optional[Gio.UnixFDList]]:
    """Call a portal method and wait for its Response signal.

    Uses conn.call_sync() for the D-Bus call (fast, non-blocking for the
    portal UI) and GLib.MainLoop.run() to dispatch the Response signal.
    Must be called from a thread where the GLib default main context is
    available (i.e. any thread — it's thread-safe).
    """
    handle_token = _extract_handle_token(params)
    if handle_token is None:
        raise RuntimeError(f"{method}: no handle_token in parameters")

    req_path = _request_path(conn, handle_token)

    state = {"done": False, "result": -1, "details": {}, "fd_list": None}
    loop = GLib.MainLoop()

    def on_response(_c, _s, _o, _i, _sig, resp_params):
        state["result"] = resp_params[0]
        d = resp_params[1] if len(resp_params) > 1 else {}
        state["details"] = {k: d[k] for k in d.keys()}
        state["done"] = True
        loop.quit()

    # Subscribe BEFORE calling so we never miss the signal.
    sub_id = conn.signal_subscribe(
        None,
        "org.freedesktop.portal.Request",
        "Response",
        req_path,
        None,
        Gio.DBusSignalFlags.NONE,
        on_response,
    )

    try:
        # call_sync is fast — the D-Bus method reply comes back in <1ms.
        # The portal UI (window picker) is rendered by GNOME Shell, not us.
        if need_fd:
            _, fd_list = conn.call_with_unix_fd_list_sync(
                _PORTAL_BUS_NAME,
                _PORTAL_OBJECT_PATH,
                _PORTAL_IFACE,
                method,
                params,
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                None,
            )
            state["fd_list"] = fd_list
        else:
            conn.call_sync(
                _PORTAL_BUS_NAME,
                _PORTAL_OBJECT_PATH,
                _PORTAL_IFACE,
                method,
                params,
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )

        # Run the GLib main loop to dispatch the Response signal.
        GLib.timeout_add(120_000, lambda: loop.quit() or False)
        loop.run()

        if not state["done"]:
            raise TimeoutError(
                f"Portal {method} timed out — user didn't respond?"
            )
        if state["result"] != 0:
            raise RuntimeError(
                f"Portal {method} failed with code {state['result']}"
            )
        return state["details"], state["fd_list"]

    finally:
        conn.signal_unsubscribe(sub_id)


class _ScreenCastPortal:
    """Manages an xdg-desktop-portal ScreenCast session via GDBus."""

    def __init__(self):
        if not _HAS_GST:
            raise RuntimeError("GStreamer (gi.repository.Gst) is not available")
        self._conn: Gio.DBusConnection = Gio.bus_get_sync(
            Gio.BusType.SESSION, None
        )
        self._escaped = (
            self._conn.get_unique_name().replace(".", "_").lstrip(":")
        )
        self._session_handle: Optional[str] = None

    def create_session(self) -> None:
        session_token = _token()
        params = GLib.Variant(
            "(a{sv})",
            ({
                "session_handle_token": GLib.Variant("s", session_token),
                "handle_token": GLib.Variant("s", _token()),
                "app_id": GLib.Variant("s", "deep-live-cam"),
            },),
        )
        _portal_call(self._conn, "CreateSession", params)
        self._session_handle = (
            f"{_PORTAL_OBJECT_PATH}/session/"
            f"{self._escaped}/{session_token}"
        )

    def select_sources(self) -> None:
        if self._session_handle is None:
            raise RuntimeError("No session created")
        params = GLib.Variant(
            "(oa{sv})",
            (self._session_handle, {
                "handle_token": GLib.Variant("s", _token()),
                "types": GLib.Variant("u", 2),       # windows only
                "multiple": GLib.Variant("b", False),
                "cursor_mode": GLib.Variant("u", 2),  # embedded
            }),
        )
        _portal_call(self._conn, "SelectSources", params)

    def start(self) -> Tuple[int, int]:
        """Start the session. Returns (pipe_wire_fd, node_id)."""
        if self._session_handle is None:
            raise RuntimeError("No session created")
        params = GLib.Variant(
            "(osa{sv})",
            (self._session_handle, "", {
                "handle_token": GLib.Variant("s", _token()),
            }),
        )
        details, fd_list = _portal_call(
            self._conn, "Start", params, need_fd=True,
        )

        streams = details.get("streams")
        if streams is None:
            raise RuntimeError("No streams returned — no window selected?")

        node_id = int(streams[0][0])
        fd = fd_list.get(0) if fd_list else -1
        if fd < 0:
            raise RuntimeError("Invalid PipeWire fd")

        return fd, node_id


# ─── public API ──────────────────────────────────────────────────────────


def run_portal_session() -> Optional[Tuple[int, int]]:
    """Run the portal dialog and return (fd, node_id) on success.

    This function BLOCKS while the system window-picker dialog is shown.
    Call it from a background thread so the Qt event loop stays responsive.
    Returns None if the user cancels or an error occurs.
    """
    try:
        if os.environ.get("XDG_SESSION_TYPE") != "wayland":
            print("Window capture requires a Wayland session.")
            return None
        if not _HAS_GST:
            print("GStreamer (gi.repository.Gst) is not available.")
            return None
        portal = _ScreenCastPortal()
        portal.create_session()
        portal.select_sources()
        fd, node_id = portal.start()
        return fd, node_id
    except TimeoutError as e:
        print(f"Window capture: {e}")
        return None
    except Exception as e:
        print(f"Portal session error: {e}")
        return None


class WindowCapturer:
    """Captures a window via PipeWire + GStreamer.

    Typical usage (from the UI):
        1. In a background thread: call run_portal_session() to show the
           system window picker and get (fd, node_id).
        2. On the main thread: create WindowCapturer() and call
           start_with_fd(fd, node_id) to begin capturing.
        3. Use read() / release() as with VideoCapturer.
    """

    def __init__(self):
        self._pipeline: Optional[Gst.Pipeline] = None
        self._appsink = None
        self.is_running = False
        self.actual_width: int = 0
        self.actual_height: int = 0
        self.actual_fps: float = 30.0
        self.frame_callback = None

    def start_with_fd(self, fd: int, node_id: int, fps: int = 30) -> bool:
        """Build and start the GStreamer pipeline from a PipeWire fd."""
        if not _HAS_GST:
            return False
        try:
            pipeline_str = (
                f"pipewiresrc fd={fd} path={node_id} ! "
                f"videoconvert ! video/x-raw,format=BGRx ! "
                f"appsink name=sink emit-signals=false "
                f"sync=false max-buffers=2 drop=true"
            )
            self._pipeline = Gst.parse_launch(pipeline_str)
            self._appsink = self._pipeline.get_by_name("sink")
            self._pipeline.set_state(Gst.State.PLAYING)

            bus = self._pipeline.get_bus()
            msg = bus.timed_pop_filtered(
                Gst.SECOND * 5,
                Gst.MessageType.ERROR | Gst.MessageType.STATE_CHANGED,
            )
            if msg and msg.type == Gst.MessageType.ERROR:
                err, _ = msg.parse_error()
                print(f"GStreamer error: {err.message}")
                self.release()
                return False

            sample = self._appsink.emit("pull-sample")
            if sample:
                caps = sample.get_caps()
                structure = caps.get_structure(0)
                self.actual_width = structure.get_value("width")
                self.actual_height = structure.get_value("height")

            if self.actual_width == 0:
                self.actual_width = 640
                self.actual_height = 480

            self.actual_fps = float(fps)
            self.is_running = True
            return True

        except Exception as e:
            print(f"Failed to start window capture pipeline: {e}")
            self.release()
            return False

    # ── VideoCapturer-compatible interface ─────────────────────────────

    def start(self, width: int = 0, height: int = 0, fps: int = 30) -> bool:
        raise NotImplementedError(
            "Use run_portal_session() + start_with_fd() instead"
        )

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self.is_running or self._appsink is None:
            return False, None
        try:
            sample = self._appsink.emit("pull-sample")
            if sample is None:
                return False, None

            buf = sample.get_buffer()
            caps = sample.get_caps()
            structure = caps.get_structure(0)
            w = structure.get_value("width")
            h = structure.get_value("height")

            success, map_info = buf.map(Gst.MapFlags.READ)
            if not success:
                return False, None

            frame = np.frombuffer(map_info.data, dtype=np.uint8).reshape((h, w, 4))
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            buf.unmap(map_info)

            if w != self.actual_width or h != self.actual_height:
                self.actual_width = w
                self.actual_height = h

            if self.frame_callback:
                self.frame_callback(bgr)
            return True, bgr

        except Exception:
            return False, None

    def release(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        self._appsink = None
        self.is_running = False

    def set_frame_callback(self, callback) -> None:
        self.frame_callback = callback
