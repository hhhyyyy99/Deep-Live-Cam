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


class _ScreenCastPortal:
    """Manages an xdg-desktop-portal ScreenCast session via GDBus.

    The portal API uses two distinct tokens:
    - ``handle_token``  – for the Request object path (Response signal routing).
    - ``session_handle_token`` – for the Session object path
      (CreateSession only).

    The request path format is::

        /org/freedesktop/portal/desktop/request/{escaped_sender}/{handle_token}

    We compute this path from our own handle_token *before* calling the portal
    method, then subscribe to the Response signal at that path so we never
    miss it.
    """

    def __init__(self):
        if not _HAS_GST:
            raise RuntimeError("GStreamer (gi.repository.Gst) is not available")
        self._conn: Gio.DBusConnection = Gio.bus_get_sync(
            Gio.BusType.SESSION, None
        )
        self._escaped_sender = (
            self._conn.get_unique_name().replace(".", "_").lstrip(":")
        )
        self._session_handle: Optional[str] = None
        self._fd: Optional[int] = None
        self._node_id: Optional[int] = None

    # ── D-Bus helpers ──────────────────────────────────────────────────

    def _request_path(self, handle_token: str) -> str:
        return (
            f"{_PORTAL_OBJECT_PATH}/request/"
            f"{self._escaped_sender}/{handle_token}"
        )

    def _call_portal(
        self,
        method: str,
        parameters: GLib.Variant,
        need_fd: bool = False,
    ) -> Optional[Tuple[GLib.Variant, Optional[Gio.UnixFDList]]]:
        """Call a portal method and block until its Response signal arrives.

        Must be called from a thread that runs a GLib main loop.
        """
        # Extract handle_token from the variant so we know the request path.
        handle_token = self._extract_handle_token(parameters)
        if handle_token is None:
            raise RuntimeError(f"{method}: no handle_token in parameters")

        req_path = self._request_path(handle_token)

        # State shared with the signal callback
        state = {"done": False, "result": -1, "details": {}}
        loop = GLib.MainLoop()

        def on_response(_conn, _sender, _path, _iface, _sig, params):
            state["result"] = params[0]
            details = params[1] if len(params) > 1 else {}
            state["details"] = {k: details[k] for k in details.keys()}
            state["done"] = True
            loop.quit()

        # Subscribe BEFORE calling so we never miss the signal.
        sub_id = self._conn.signal_subscribe(
            None,
            "org.freedesktop.portal.Request",
            "Response",
            req_path,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
        )

        try:
            if need_fd:
                _result, out_fd_list = self._conn.call_with_unix_fd_list_sync(
                    _PORTAL_BUS_NAME,
                    _PORTAL_OBJECT_PATH,
                    _PORTAL_IFACE,
                    method,
                    parameters,
                    None,
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                    None,
                )
            else:
                self._conn.call_sync(
                    _PORTAL_BUS_NAME,
                    _PORTAL_OBJECT_PATH,
                    _PORTAL_IFACE,
                    method,
                    parameters,
                    None,
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                )
                out_fd_list = None

            # Run GLib main loop until the Response signal arrives.
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
            return None, out_fd_list

        finally:
            self._conn.signal_unsubscribe(sub_id)

    @staticmethod
    def _extract_handle_token(params: GLib.Variant) -> Optional[str]:
        """Walk the variant tuple to find the 'handle_token' entry in the
        embedded dict (works for (a{sv}), (oa{sv}), (osa{sv}))."""
        outer = params
        for i in range(outer.n_children()):
            child = outer.get_child_value(i)
            if child.get_type_string() == "a{sv}":
                d = child
                for j in range(d.n_children()):
                    entry = d.get_child_value(j)
                    if entry.get_child_value(0).get_string() == "handle_token":
                        return entry.get_child_value(1).get_variant().get_string()
        return None

    # ── Portal API methods ─────────────────────────────────────────────

    def create_session(self) -> None:
        session_token = _token()
        handle_token = _token()
        params = GLib.Variant(
            "(a{sv})",
            ({
                "session_handle_token": GLib.Variant("s", session_token),
                "handle_token": GLib.Variant("s", handle_token),
                "app_id": GLib.Variant("s", "deep-live-cam"),
            },),
        )
        self._call_portal("CreateSession", params)

        self._session_handle = (
            f"{_PORTAL_OBJECT_PATH}/session/"
            f"{self._escaped_sender}/{session_token}"
        )

    def select_sources(self) -> None:
        if self._session_handle is None:
            raise RuntimeError("No session created")
        handle_token = _token()
        params = GLib.Variant(
            "(oa{sv})",
            (self._session_handle, {
                "handle_token": GLib.Variant("s", handle_token),
                "types": GLib.Variant("u", 2),       # windows only
                "multiple": GLib.Variant("b", False),
                "cursor_mode": GLib.Variant("u", 2),  # embedded
            }),
        )
        self._call_portal("SelectSources", params)

    def start(self) -> Tuple[int, int]:
        """Start the session.  Returns (pipe_wire_fd, node_id)."""
        if self._session_handle is None:
            raise RuntimeError("No session created")
        handle_token = _token()
        params = GLib.Variant(
            "(osa{sv})",
            (self._session_handle, "", {
                "handle_token": GLib.Variant("s", handle_token),
            }),
        )
        _, out_fd_list = self._call_portal("Start", params, need_fd=True)

        # The Response signal details contain 'streams'.
        # Re-read from the last _call_portal state via a fresh signal.
        # Actually we stored details in state but returned them differently.
        # Let's refactor to return details too.
        raise NotImplementedError("See run_portal_session()")

    def start_and_get_stream(self) -> Tuple[int, int]:
        """Start and return (fd, node_id) from the portal response."""
        if self._session_handle is None:
            raise RuntimeError("No session created")
        handle_token = _token()

        state = {"done": False, "result": -1, "details": {}}
        loop = GLib.MainLoop()

        req_path = self._request_path(handle_token)

        def on_response(_c, _s, _o, _i, _sig, params):
            state["result"] = params[0]
            d = params[1] if len(params) > 1 else {}
            state["details"] = {k: d[k] for k in d.keys()}
            state["done"] = True
            loop.quit()

        sub_id = self._conn.signal_subscribe(
            None,
            "org.freedesktop.portal.Request",
            "Response",
            req_path,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
        )

        try:
            params = GLib.Variant(
                "(osa{sv})",
                (self._session_handle, "", {
                    "handle_token": GLib.Variant("s", handle_token),
                }),
            )
            _, out_fd_list = self._conn.call_with_unix_fd_list_sync(
                _PORTAL_BUS_NAME,
                _PORTAL_OBJECT_PATH,
                _PORTAL_IFACE,
                "Start",
                params,
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                None,
            )

            GLib.timeout_add(120_000, lambda: loop.quit() or False)
            loop.run()

            if not state["done"]:
                raise TimeoutError("Portal Start timed out")
            if state["result"] != 0:
                raise RuntimeError(f"Portal Start failed: {state['result']}")

            streams = state["details"].get("streams")
            if streams is None:
                raise RuntimeError("No streams returned — no window selected?")

            node_id = int(streams[0][0])
            fd = out_fd_list.get(0) if out_fd_list else -1
            if fd < 0:
                raise RuntimeError("Invalid PipeWire fd")

            self._fd = fd
            self._node_id = node_id
            return fd, node_id

        finally:
            self._conn.signal_unsubscribe(sub_id)


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
        fd, node_id = portal.start_and_get_stream()
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
