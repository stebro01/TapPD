"""Webcam capture device backed by the MediaPipe sidecar.

MediaPipe has no wheels for the app's Python (3.14), so the actual camera +
inference runs in ``mediapipe_sidecar/`` on Python 3.12.  This device is the
*client*: it spawns the sidecar, acts as the socket server it connects back to,
sends commands, and converts the sidecar's raw-landmark JSON into ``HandFrame``
objects via :mod:`capture.mediapipe_mapping`.

See ``mediapipe_sidecar/PROTOCOL.md`` for the wire format.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
from typing import Callable

log = logging.getLogger(__name__)

from capture.base_capture import BaseCaptureDevice, HandFrame
from capture import mediapipe_mapping

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SIDECAR_DIR = os.path.join(_REPO_ROOT, "mediapipe_sidecar")
_SIDECAR_SCRIPT = os.path.join(_SIDECAR_DIR, "sidecar.py")
_SIDECAR_PY = os.path.join(_SIDECAR_DIR, ".venv", "bin", "python3")
_SIDECAR_MODEL = os.path.join(_SIDECAR_DIR, "models", "hand_landmarker.task")

# Preview callback receives the raw preview message dict (jpeg, w, h, landmarks,
# hand_handedness, face) — see mediapipe_sidecar/PROTOCOL.md.
PreviewCallback = Callable[[dict], None]


class SidecarError(RuntimeError):
    """Raised when the MediaPipe sidecar cannot be started."""


class WebcamSource(BaseCaptureDevice):
    def __init__(self, camera_index: int = 0, flip_handedness: bool = False,
                 replay_path: str = "") -> None:
        self.camera_index = camera_index
        self.flip_handedness = flip_handedness
        self.replay_path = replay_path  # if set, sidecar loops this video clip
        self._range: tuple[float, float] | None = None  # play-once [start_s, end_s]
        self._loop = True               # False = play the range once, then "done"
        self._recorded_callback = None  # called(path) when a record finishes
        self._done_callback = None      # called() when a play-once range finishes

        self._proc: subprocess.Popen | None = None
        self._srv: socket.socket | None = None
        self._sock: socket.socket | None = None
        self._writer = None
        self._reader = None
        self._reader_thread: threading.Thread | None = None
        self._send_lock = threading.Lock()

        self._connected = False
        self._recording = False
        self._frame_callback: Callable[[HandFrame], None] | None = None
        self._preview_callback: PreviewCallback | None = None
        self._prev_by_hand: dict[str, HandFrame] = {}

        # list_cameras() request/response
        self._cameras: list[tuple[int, str]] = []
        self._cameras_event = threading.Event()

        self._sample_rate = 30.0
        self._sensor_issues: list[str] = []
        self._face_on = False   # face tracking supplies the eye reference

    # ── preconditions ─────────────────────────────────────────────
    @staticmethod
    def sidecar_ready() -> tuple[bool, list[str]]:
        """Return (ok, issues). ``issues`` is empty when the sidecar can run."""
        issues = []
        if not os.path.isfile(_SIDECAR_PY):
            issues.append(
                "MediaPipe-Sidecar nicht eingerichtet (Python-3.12-venv fehlt).\n"
                "  -> Einrichten:  bash mediapipe_sidecar/setup_sidecar.sh"
            )
        if not os.path.isfile(_SIDECAR_MODEL):
            issues.append(
                "Hand-Landmarker-Modell fehlt.\n"
                "  -> Einrichten:  bash mediapipe_sidecar/setup_sidecar.sh"
            )
        return (not issues, issues)

    # ── lifecycle ─────────────────────────────────────────────────
    def connect(self) -> None:
        ok, issues = self.sidecar_ready()
        if not ok:
            self._sensor_issues = issues
            raise SidecarError(issues[0])

        # Idempotent: tear down any prior/stale state before re-binding, so a
        # re-connect (or start_recording after the sidecar died) never orphans a
        # socket, subprocess or reader thread.
        self.disconnect()

        # We are the server; the sidecar connects back to this ephemeral port.
        try:
            self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._srv.bind(("127.0.0.1", 0))
            self._srv.listen(1)
            port = self._srv.getsockname()[1]

            log.info("Starte MediaPipe-Sidecar (Port %d)...", port)
            self._proc = subprocess.Popen(
                [_SIDECAR_PY, _SIDECAR_SCRIPT, "--port", str(port)],
                cwd=_SIDECAR_DIR,
                stderr=subprocess.DEVNULL,
            )

            self._srv.settimeout(10.0)
            try:
                self._sock, _ = self._srv.accept()
            except socket.timeout:
                raise SidecarError("Sidecar hat sich nicht innerhalb von 10 s verbunden")

            # The single client is connected — the listening socket is no longer needed.
            self._srv.close()
            self._srv = None

            self._reader = self._sock.makefile("r", encoding="utf-8")
            self._writer = self._sock.makefile("w", encoding="utf-8")
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()
            self._connected = True
            # Push tunables (preview/confidence/record) from capture.yaml.
            try:
                from capture.config import sidecar_settings
                self._send({"cmd": "config", **sidecar_settings()})
            except Exception:
                log.debug("Sidecar-Config konnte nicht gesendet werden", exc_info=True)
            log.info("MediaPipe-Sidecar verbunden")
        except Exception:
            # Any partial failure → full cleanup, then re-raise.
            self.disconnect()
            raise

    def disconnect(self) -> None:
        was_connected = self._connected
        if was_connected:
            log.info("Trenne MediaPipe-Sidecar...")
            self._recording = False
            self._send({"cmd": "quit"})
        self._connected = False
        self._frame_callback = None
        self._preview_callback = None
        self._terminate_proc()
        for c in (self._reader, self._writer, self._sock, self._srv):
            try:
                if c is not None:
                    c.close()
            except OSError:
                pass
        self._reader = self._writer = self._sock = self._srv = None
        t = self._reader_thread
        self._reader_thread = None
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.0)
        if was_connected:
            log.info("MediaPipe-Sidecar getrennt")

    def _terminate_proc(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def is_connected(self) -> bool:
        return self._connected and self._proc is not None and self._proc.poll() is None

    def check_device_present(self) -> bool:
        """For the status-bar sensor check: a connected sidecar with a camera."""
        if not self.is_connected():
            return False
        return bool(self.list_cameras())

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    # ── camera enumeration ────────────────────────────────────────
    def list_cameras(self, timeout: float = 5.0) -> list[tuple[int, str]]:
        """Return [(index, name)] from the sidecar (blocks briefly)."""
        if not self.is_connected():
            return []
        self._cameras_event.clear()
        self._send({"cmd": "list_cameras"})
        if self._cameras_event.wait(timeout):
            return list(self._cameras)
        return []

    # ── recording ─────────────────────────────────────────────────
    def play_range(self, video: str, start_s: float, end_s: float) -> None:
        """Configure a one-shot bounded playback of `video[start_s:end_s]`
        (VideoLab). The next start_recording() plays it once and fires the
        done-callback at the offset. Leaves the looping `replay_path` path alone."""
        self.replay_path = video
        self._range = (float(start_s), float(end_s))
        self._loop = False

    def start_recording(self, callback: Callable[[HandFrame], None]) -> None:
        if not self.is_connected():
            self.connect()
        self._frame_callback = callback
        self._prev_by_hand.clear()
        self._recording = True
        s, e = self._range or (None, None)
        self._send({"cmd": "start", "index": self.camera_index,
                    "video": self.replay_path or None,
                    "start_s": s, "end_s": e, "loop": self._loop})
        log.debug("MediaPipe-Aufnahme gestartet (Kamera %d, replay=%s, range=%s)",
                  self.camera_index, self.replay_path or "-", self._range)

    def stop_recording(self) -> None:
        self._recording = False
        if self.is_connected():
            self._send({"cmd": "stop"})
        log.debug("MediaPipe-Aufnahme gestoppt")

    # ── preview ───────────────────────────────────────────────────
    def set_preview_callback(self, cb: PreviewCallback | None) -> None:
        self._preview_callback = cb

    def enable_preview(self, on: bool) -> None:
        if self.is_connected():
            self._send({"cmd": "preview", "on": bool(on)})

    def enable_face(self, on: bool) -> None:
        """Toggle the (optional) face landmarker in the sidecar."""
        self._face_on = bool(on)
        if self.is_connected():
            self._send({"cmd": "face", "on": bool(on)})

    @property
    def extra_capabilities(self) -> set[str]:
        """State-dependent capabilities: with face tracking on, the eye
        reference makes the hand position absolute (unlocks tremor)."""
        from capture.source import CAP_ABS_POSITION
        return {CAP_ABS_POSITION} if self._face_on else set()

    def configure(self, **settings) -> None:
        """Push sidecar tunables (e.g. num_hands) — apply before start_recording."""
        if self.is_connected():
            self._send({"cmd": "config", **settings})

    def record_clip(self, path: str, seconds: float = 10.0) -> None:
        """Record the live camera to an mp4 clip for `seconds` (for later replay)."""
        if self.is_connected():
            self._send({"cmd": "record", "path": path, "seconds": seconds})

    def set_recorded_callback(self, cb) -> None:
        self._recorded_callback = cb

    def set_done_callback(self, cb) -> None:
        """Called (on the reader thread) when a play-once range reaches its end."""
        self._done_callback = cb

    # ── transport ─────────────────────────────────────────────────
    def _send(self, obj: dict) -> None:
        if self._writer is None:
            return
        line = json.dumps(obj) + "\n"
        with self._send_lock:
            try:
                self._writer.write(line)
                self._writer.flush()
            except (BrokenPipeError, ValueError, OSError):
                pass

    def _read_loop(self) -> None:
        try:
            for line in self._reader:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._dispatch(msg)
        except (OSError, ValueError):
            pass
        finally:
            # Reader ended: clean disconnect, or the sidecar died. If we still
            # thought we were connected, mark the source down so is_connected()
            # is honest and consumers can re-connect / surface an error.
            if self._connected:
                self._connected = False
                self._recording = False
                self._sensor_issues = ["Webcam-Verbindung verloren (Sidecar beendet)"]
                log.warning("MediaPipe-Sidecar-Verbindung verloren")

    def _dispatch(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "hand":
            if not self._recording or self._frame_callback is None:
                return
            frames = mediapipe_mapping.frames_from_message(
                msg, flip_handedness=self.flip_handedness,
                prev_by_hand=self._prev_by_hand,
            )
            for frame in frames:
                self._frame_callback(frame)
        elif mtype == "preview":
            if self._preview_callback is not None:
                self._preview_callback(msg)
        elif mtype == "cameras":
            self._cameras = [(int(c["index"]), str(c["name"]))
                             for c in msg.get("items", [])]
            self._cameras_event.set()
        elif mtype == "recorded":
            if self._recorded_callback is not None:
                self._recorded_callback(msg.get("path", ""))
        elif mtype == "done":
            self._recording = False   # drop any late frames
            if self._done_callback is not None:
                self._done_callback()
        elif mtype == "error":
            log.warning("Sidecar-Fehler: %s", msg.get("msg"))
            self._sensor_issues = [str(msg.get("msg"))]


