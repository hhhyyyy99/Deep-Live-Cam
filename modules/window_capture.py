"""Window capture via PipeWire + xdg-desktop-portal ScreenCast (Wayland).

Provides WindowCapturer with the same start/read/release interface as
VideoCapturer, so it can be used as a drop-in replacement in the live
preview pipeline.
"""

from __future__ import annotations

import os
import uuid
from typing import Callable, Optional, Tuple

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


class _PortalCaller:
    """Non-blocking portal D-Bus caller designed to run on the Qt main thread.

    Uses Gio.DBusConnection.call() (async) so the GLib default main context
    stays responsive — both the portal dialog (rendered by GNOME Shell) and
    Qt events are processed while we wait for each Response signal.
    """

    def __init__(self):
        self._conn: Gio.DBusConnection = Gio.bus_get_sync(
            Gio.BusType.SESSION, None
        )
        self._escaped = (
            self._conn.get_unique_name().replace(".", "_").lstrip(":")
        )
        self._sub_id: Optional[int] = None
        self._timeout_id: Optional[int] = None

    def call(
        self,
        method: str,
        params: GLib.Variant,
        callback: Callable[[dict, Optional[Gio.UnixFDList]], None],
        need_fd: bool = False,
    ) -> None:
        """Call a portal method asynchronously.  ``callback(details, fd_list)``
        is invoked on the Qt main thread when the Response signal arrives."""
        handle_token = _extract_handle_token(params)
        if handle_token is None:
            raise RuntimeError(f"{method}: no handle_token in parameters")

        req_path = _request_path(self._conn, handle_token)

        def on_response(_c, _s, _o, _i, _sig, resp_params):
            self._cleanup()
            result = resp_params[0]
            raw_details = resp_params[1] if len(resp_params) > 1 else {}
            details = {k: raw_details[k] for k in raw_details.keys()}
            if result != 0:
                raise RuntimeError(f"Portal {method} failed: {result}")
            callback(details, self._pending_fd_list)

        self._sub_id = self._conn.signal_subscribe(
            None,
            "org.freedesktop.portal.Request",
            "Response",
            req_path,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
        )

        self._pending_fd_list: Optional[Gio.UnixFDList] = None

        def on_reply(conn, result, _user_data):
            try:
                if need_fd:
                    _, self._pending_fd_list = (
                        conn.call_with_unix_fd_list_finish(result)
                    )
                else:
                    conn.call_finish(result)
            except Exception as e:
                self._cleanup()
                raise RuntimeError(f"Portal {method} D-Bus error: {e}")
            # fd_list stored; now wait for Response signal.
            # Set a 120s timeout for user interaction.
            self._timeout_id = GLib.timeout_add(
                120_000, self._on_timeout,
            )

        if need_fd:
            self._conn.call_with_unix_fd_list(
                _PORTAL_BUS_NAME,
                _PORTAL_OBJECT_PATH,
                _PORTAL_IFACE,
                method,
                params,
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                on_reply,
                None,
            )
        else:
            self._conn.call(
                _PORTAL_BUS_NAME,
                _PORTAL_OBJECT_PATH,
                _PORTAL_IFACE,
                method,
                params,
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                on_reply,
                None,
            )

    def _on_timeout(self) -> bool:
        self._cleanup()
        return False

    def _cleanup(self) -> None:
        if self._sub_id is not None:
            self._conn.signal_unsubscribe(self._sub_id)
            self._sub_id = None
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None


class PortalSession:
    """Runs the full CreateSession → SelectSources → Start flow
    non-blockingly on the Qt main thread.

    Usage::

        def on_done(fd, node_id):
            # start GStreamer pipeline with fd, node_id
            ...

        def on_error(msg):
            # show error to user
            ...

        session = PortalSession()
        session.start(on_done, on_error)
    """

    def __init__(self):
        self._caller = _PortalCaller()
        self._escaped = self._caller._escaped
        self._session_handle: Optional[str] = None
        self._on_done: Optional[Callable] = None
        self._on_error: Optional[Callable] = None

    def start(
        self,
        on_done: Callable[[int, int], None],
        on_error: Callable[[str], None],
    ) -> None:
        self._on_done = on_done
        self._on_error = on_error
        try:
            self._create_session()
        except Exception as e:
            on_error(str(e))

    def _create_session(self) -> None:
        session_token = _token()
        params = GLib.Variant(
            "(a{sv})",
            ({
                "session_handle_token": GLib.Variant("s", session_token),
                "handle_token": GLib.Variant("s", _token()),
                "app_id": GLib.Variant("s", "deep-live-cam"),
            },),
        )
        self._session_handle = (
            f"{_PORTAL_OBJECT_PATH}/session/"
            f"{self._escaped}/{session_token}"
        )
        self._caller.call("CreateSession", params, self._on_session_created)

    def _on_session_created(self, details: dict, _fd_list) -> None:
        try:
            self._select_sources()
        except Exception as e:
            self._on_error(str(e))

    def _select_sources(self) -> None:
        params = GLib.Variant(
            "(oa{sv})",
            (self._session_handle, {
                "handle_token": GLib.Variant("s", _token()),
                "types": GLib.Variant("u", 2),       # windows only
                "multiple": GLib.Variant("b", False),
                "cursor_mode": GLib.Variant("u", 2),  # embedded
            }),
        )
        self._caller.call(
            "SelectSources", params, self._on_sources_selected,
        )

    def _on_sources_selected(self, details: dict, _fd_list) -> None:
        try:
            self._start()
        except Exception as e:
            self._on_error(str(e))

    def _start(self) -> None:
        params = GLib.Variant(
            "(osa{sv})",
            (self._session_handle, "", {
                "handle_token": GLib.Variant("s", _token()),
            }),
        )
        self._caller.call(
            "Start", params, self._on_started, need_fd=True,
        )

    def _on_started(self, details: dict, fd_list) -> None:
        try:
            streams = details.get("streams")
            if streams is None:
                self._on_error("No window selected")
                return

            node_id = int(streams[0][0])
            fd = fd_list.get(0) if fd_list else -1
            if fd < 0:
                self._on_error("Invalid PipeWire fd")
                return

            self._on_done(fd, node_id)
        except Exception as e:
            self._on_error(str(e))


# ─── synchronous wrapper (for background threads) ───────────────────────


def run_portal_session() -> Optional[Tuple[int, int]]:
    """Synchronous portal session — for use in background threads.

    Runs the GLib main loop on the calling thread until the portal flow
    completes. Returns (fd, node_id) or None on failure.
    """
    if os.environ.get("XDG_SESSION_TYPE") != "wayland":
        print("Window capture requires a Wayland session.")
        return None
    if not _HAS_GST:
        print("GStreamer is not available.")
        return None

    result = {}
    loop = GLib.MainLoop()

    def on_done(fd, node_id):
        result["fd"] = fd
        result["node_id"] = node_id
        loop.quit()

    def on_error(msg):
        result["error"] = msg
        loop.quit()

    # Use GLib.idle_add to kick off the portal flow on the default context.
    GLib.idle_add(lambda: _run_portal_inline(on_done, on_error) or False)

    GLib.timeout_add(120_000, lambda: loop.quit() or False)
    loop.run()

    if "error" in result:
        print(f"Portal error: {result['error']}")
        return None
    if "fd" in result:
        return result["fd"], result["node_id"]
    return None


def _run_portal_inline(on_done, on_error):
    """Run the portal flow inline (called from GLib.idle_add)."""
    try:
        session = PortalSession()
        session.start(on_done, on_error)
    except Exception as e:
        on_error(str(e))
    return False  # don't repeat


# ─── WindowCapturer ─────────────────────────────────────────────────────


class WindowCapturer:
    """Captures a window via PipeWire + GStreamer."""

    def __init__(self):
        self._pipeline: Optional[Gst.Pipeline] = None
        self._appsink = None
        self.is_running = False
        self.actual_width: int = 0
        self.actual_height: int = 0
        self.actual_fps: float = 30.0
        self.frame_callback = None

    def start_with_fd(self, fd: int, node_id: int, fps: int = 30) -> bool:
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

    def start(self, width: int = 0, height: int = 0, fps: int = 30) -> bool:
        raise NotImplementedError(
            "Use PortalSession + start_with_fd() instead"
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
