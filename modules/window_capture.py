"""Window capture via PipeWire + xdg-desktop-portal ScreenCast (Wayland).

Provides WindowCapturer with the same start/read/release interface as
VideoCapturer, so it can be used as a drop-in replacement in the live
preview pipeline.
"""

from __future__ import annotations

import os
import time
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


class _ScreenCastPortal:
    """Manages an xdg-desktop-portal ScreenCast session via GDBus.

    Uses Gio.DBusConnection.call_with_unix_fd_list_sync for the Start call
    so that the PipeWire file descriptor is properly received.
    """

    def __init__(self):
        if not _HAS_GST:
            raise RuntimeError("GStreamer (gi.repository.Gst) is not available")
        self._conn: Gio.DBusConnection = Gio.bus_get_sync(
            Gio.BusType.SESSION, None
        )
        self._session_handle: Optional[str] = None
        self._fd: Optional[int] = None
        self._node_id: Optional[int] = None
        self._loop: Optional[GLib.MainLoop] = None
        self._response_received = False
        self._response_result: int = -1
        self._response_details: dict = {}

    @staticmethod
    def _token() -> str:
        import uuid

        return "dlc_" + uuid.uuid4().hex[:12]

    def _on_response(
        self, connection, sender_name, object_path, interface_name, signal_name, parameters
    ):
        result = parameters[0]
        details = parameters[1] if len(parameters) > 1 else {}
        self._response_result = result
        self._response_details = {}
        if details:
            for key in details.keys():
                self._response_details[key] = details[key]
        self._response_received = True
        if self._loop is not None:
            self._loop.quit()

    def _compute_request_path(self, handle_token: str) -> str:
        escaped = self._conn.get_unique_name().replace(".", "_").lstrip(":")
        return f"{_PORTAL_OBJECT_PATH}/request/{escaped}_{handle_token}"

    def _call_portal(
        self, method: str, parameters: GLib.Variant, need_fd: bool = False
    ) -> Optional[Tuple[GLib.Variant, Optional[Gio.UnixFDList]]]:
        handle_token = self._token()
        self._response_received = False
        self._response_result = -1
        self._response_details = {}

        request_path = self._compute_request_path(handle_token)
        sub_id = self._conn.signal_subscribe(
            None,  # sender
            "org.freedesktop.portal.Request",
            "Response",
            request_path,
            None,  # arg0
            Gio.DBusSignalFlags.NONE,
            self._on_response,
        )

        try:
            if need_fd:
                result, out_fd_list = self._conn.call_with_unix_fd_list_sync(
                    _PORTAL_BUS_NAME,
                    _PORTAL_OBJECT_PATH,
                    _PORTAL_IFACE,
                    method,
                    parameters,
                    None,  # reply_type
                    Gio.DBusCallFlags.NONE,
                    -1,  # timeout (use default)
                    None,  # fd_list
                    None,  # cancellable
                )
            else:
                result = self._conn.call_sync(
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

            # Wait for Response signal
            self._loop = GLib.MainLoop()
            timeout_id = GLib.timeout_add(120_000, self._on_timeout)
            self._loop.run()
            GLib.source_remove(timeout_id)
            self._loop = None

            if not self._response_received:
                raise TimeoutError(
                    f"Portal {method} timed out — user didn't respond?"
                )

            if self._response_result != 0:
                raise RuntimeError(
                    f"Portal {method} failed with code {self._response_result}"
                )

            return result, out_fd_list

        finally:
            self._conn.signal_unsubscribe(sub_id)

    def _on_timeout(self) -> bool:
        if self._loop is not None:
            self._loop.quit()
        return False  # don't repeat

    def create_session(self) -> None:
        token = self._token()
        params = GLib.Variant("(a{sv})", ({"handle_token": GLib.Variant("s", token),
                                            "app_id": GLib.Variant("s", "deep-live-cam")},))
        self._call_portal("CreateSession", params)
        escaped = self._conn.get_unique_name().replace(".", "_").lstrip(":")
        self._session_handle = (
            self._response_details.get("session_handle")
            or f"{_PORTAL_OBJECT_PATH}/session/{escaped}_{token}"
        )

    def select_sources(self) -> None:
        if self._session_handle is None:
            raise RuntimeError("No session created")
        token = self._token()
        params = GLib.Variant(
            "(oa{sv})",
            (
                self._session_handle,
                {
                    "handle_token": GLib.Variant("s", token),
                    "types": GLib.Variant("u", 2),  # windows only
                    "multiple": GLib.Variant("b", False),
                    "cursor_mode": GLib.Variant("u", 2),  # embedded
                },
            ),
        )
        self._call_portal("SelectSources", params)

    def start(self) -> Tuple[int, int]:
        """Start the session. Returns (pipe_wire_fd, node_id)."""
        if self._session_handle is None:
            raise RuntimeError("No session created")
        token = self._token()
        params = GLib.Variant(
            "(oa{sv})",
            (
                self._session_handle,
                {
                    "handle_token": GLib.Variant("s", token),
                },
            ),
        )
        result, out_fd_list = self._call_portal("Start", params, need_fd=True)

        # Extract streams from response
        details = self._response_details
        streams = details.get("streams")
        if streams is None:
            raise RuntimeError(
                "No streams returned from portal — no window selected?"
            )

        # streams is a variant of type a(ua{sv})
        # Get the first stream: (node_id, properties)
        stream = streams[0]
        node_id = int(stream[0])

        # Get the file descriptor from the out_fd_list
        if out_fd_list is None:
            raise RuntimeError("No file descriptor list returned from portal")

        # The fd index in the stream's properties, or default to index 0
        fd_index = 0
        props = stream[1] if len(stream) > 1 else {}
        if "fd" in props:
            fd_index = int(props["fd"])

        fd = out_fd_list.get(fd_index)
        if fd < 0:
            raise RuntimeError(f"Invalid fd at index {fd_index}")

        self._fd = fd
        self._node_id = node_id
        return fd, node_id

    @property
    def fd(self) -> Optional[int]:
        return self._fd

    @property
    def node_id(self) -> Optional[int]:
        return self._node_id


class WindowCapturer:
    """Captures a window via PipeWire + xdg-desktop-portal ScreenCast.

    Same interface as VideoCapturer: start() / read() / release().
    On start(), the system's native window picker dialog appears.
    """

    def __init__(self):
        self._portal: Optional[_ScreenCastPortal] = None
        self._pipeline: Optional[Gst.Pipeline] = None
        self._appsink = None
        self.is_running = False
        self.actual_width: int = 0
        self.actual_height: int = 0
        self.actual_fps: float = 30.0
        self.frame_callback = None

    def start(self, width: int = 0, height: int = 0, fps: int = 30) -> bool:
        if not _HAS_GST:
            print("GStreamer (gi.repository.Gst) is not available.")
            return False
        if os.environ.get("XDG_SESSION_TYPE") != "wayland":
            print("Window capture requires a Wayland session.")
            return False

        try:
            self._portal = _ScreenCastPortal()
            self._portal.create_session()
            self._portal.select_sources()
            fd, node_id = self._portal.start()

            # Build GStreamer pipeline
            pipeline_str = (
                f"pipewiresrc fd={fd} path={node_id} ! "
                f"videoconvert ! video/x-raw,format=BGRx ! "
                f"appsink name=sink emit-signals=false "
                f"sync=false max-buffers=2 drop=true"
            )
            self._pipeline = Gst.parse_launch(pipeline_str)
            self._appsink = self._pipeline.get_by_name("sink")

            self._pipeline.set_state(Gst.State.PLAYING)

            # Wait for pipeline to negotiate caps or error
            bus = self._pipeline.get_bus()
            msg = bus.timed_pop_filtered(
                Gst.SECOND * 5,
                Gst.MessageType.ERROR | Gst.MessageType.STATE_CHANGED,
            )
            if msg and msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                print(f"GStreamer error: {err.message}")
                self.release()
                return False

            # Pull first frame to determine dimensions
            sample = self._appsink.emit("pull-sample")
            if sample:
                caps = sample.get_caps()
                structure = caps.get_structure(0)
                self.actual_width = structure.get_value("width")
                self.actual_height = structure.get_value("height")

            if self.actual_width == 0:
                self.actual_width = width or 640
                self.actual_height = height or 480

            self.actual_fps = float(fps)
            self.is_running = True
            return True

        except TimeoutError as e:
            print(f"Window capture: {e}")
            return False
        except Exception as e:
            print(f"Failed to start window capture: {e}")
            self.release()
            return False

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

            # BGRx (4 bytes/pixel) → BGR
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
        self._portal = None
        self.is_running = False

    def set_frame_callback(self, callback) -> None:
        self.frame_callback = callback
