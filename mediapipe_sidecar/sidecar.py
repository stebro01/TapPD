"""MediaPipe hand-tracking sidecar (runs on Python 3.12).

The main TapPD app runs on Python 3.14, where MediaPipe has no wheels.  This
standalone process does the camera capture + MediaPipe inference and streams
results back to the main app over a local TCP socket as newline-delimited JSON.

It is *pure perception*: it emits raw landmarks (image + world), handedness and
(throttled) preview JPEGs.  All domain mapping to TapPD's ``HandFrame`` happens
in the main app (``capture/mediapipe_mapping.py``), so this file has no
knowledge of TapPD data structures.

Protocol: see ``PROTOCOL.md``.  The main app is the socket server and passes its
port via ``--port``; this process connects back to it.

Run standalone for debugging::

    python3 sidecar.py --stdio          # read commands from stdin, JSON to stdout
    python3 sidecar.py --port 5599      # connect to 127.0.0.1:5599
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import threading
import time

import cv2
import numpy as np


def _strip_appledouble_stylelib() -> None:
    """Delete macOS AppleDouble (._*) files from matplotlib's stylelib.

    This repo can live on a non-native volume where macOS spawns ._* metadata
    files.  matplotlib globs the stylelib directory on import and tries to parse
    any ._*.mplstyle as a stylesheet, raising UnicodeDecodeError and breaking the
    whole ``import mediapipe`` chain.  Cleaning first makes the sidecar
    self-healing even if a reinstall recreated them.
    """
    try:
        import matplotlib
        styledir = os.path.join(os.path.dirname(matplotlib.__file__),
                                "mpl-data", "stylelib")
        for name in os.listdir(styledir):
            if name.startswith("._"):
                try:
                    os.remove(os.path.join(styledir, name))
                except OSError:
                    pass
    except Exception:
        pass


_strip_appledouble_stylelib()

import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_MODEL = os.path.join(_HERE, "models", "hand_landmarker.task")
_FACE_MODEL = os.path.join(_HERE, "models", "face_landmarker.task")

PREVIEW_FPS = 15.0          # throttle for the (heavy) JPEG preview stream
PREVIEW_MAX_W = 640         # downscale preview frames to at most this width
PREVIEW_JPEG_QUALITY = 70


# ---------------------------------------------------------------------------
# Camera enumeration
# ---------------------------------------------------------------------------

def list_cameras() -> list[dict]:
    """Return [{"index": int, "name": str}] for available cameras.

    Prefers the named-device library ``cv2-enumerate-cameras``; falls back to
    probing numeric indices (names unknown).
    """
    try:
        from cv2_enumerate_cameras import enumerate_cameras
        backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
        cams = []
        for info in enumerate_cameras(backend):
            cams.append({"index": int(info.index), "name": str(info.name)})
        if cams:
            return cams
    except Exception:
        pass

    # Fallback: probe indices 0..7
    cams = []
    for i in range(8):
        cap = cv2.VideoCapture(i, cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY)
        if cap is not None and cap.isOpened():
            cams.append({"index": i, "name": f"Camera {i}"})
            cap.release()
    return cams


# ---------------------------------------------------------------------------
# Sidecar
# ---------------------------------------------------------------------------

class Sidecar:
    def __init__(self, send_fn, model_path: str = _DEFAULT_MODEL):
        self._send = send_fn                 # callable(dict) -> None, thread-safe
        self._model_path = model_path
        self._landmarker = None
        self._face_landmarker = None
        self._capture_thread: threading.Thread | None = None
        self._closing = threading.Event()   # tells the camera thread to exit
        self._quit = threading.Event()      # process should terminate
        self._streaming = False             # emit hand/preview frames?
        self._preview_on = False
        self._face_on = True                # run the face landmarker? (optional)
        self._cam_index = 0
        self._video_path = None             # if set, loop this video instead of the camera
        # Video recording (writes the live frames to a clip for later replay).
        self._record_path = None
        self._record_until = 0.0
        self._writer = None
        # VIDEO-mode HandLandmarker requires strictly increasing timestamps, and
        # the landmarker instance is reused across start/stop — so this must keep
        # increasing across restarts (a per-loop t0 would reset and break it).
        self._last_ts_ms = -1

    # ── lifecycle ────────────────────────────────────────────────
    def _ensure_landmarker(self) -> None:
        if self._landmarker is not None:
            return
        if not os.path.isfile(self._model_path):
            raise FileNotFoundError(
                f"Hand-Landmarker-Modell fehlt: {self._model_path}. "
                "Bitte setup_sidecar.sh ausführen."
            )
        base = mp_python.BaseOptions(model_asset_path=self._model_path)
        opts = mp_vision.HandLandmarkerOptions(
            base_options=base,
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=2,
            min_hand_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._landmarker = mp_vision.HandLandmarker.create_from_options(opts)

    def _ensure_face_landmarker(self):
        """Lazily create the Face Landmarker (returns None if the model is absent)."""
        if self._face_landmarker is not None:
            return self._face_landmarker
        if not os.path.isfile(_FACE_MODEL):
            return None
        base = mp_python.BaseOptions(model_asset_path=_FACE_MODEL)
        opts = mp_vision.FaceLandmarkerOptions(
            base_options=base,
            running_mode=mp_vision.RunningMode.VIDEO,
            num_faces=1,
        )
        self._face_landmarker = mp_vision.FaceLandmarker.create_from_options(opts)
        return self._face_landmarker

    # ── command dispatch ─────────────────────────────────────────
    def handle(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        if cmd == "list_cameras":
            self._send({"type": "cameras", "items": list_cameras()})
        elif cmd == "start":
            self.start(int(msg.get("index", 0)), msg.get("video") or None)
        elif cmd == "stop":
            self.stop()
        elif cmd == "preview":
            self._preview_on = bool(msg.get("on", False))
        elif cmd == "face":
            self._face_on = bool(msg.get("on", True))
        elif cmd == "record":
            # Record the live frames to a clip for ~`seconds`, for later replay.
            self._record_path = msg.get("path")
            self._record_until = time.perf_counter() + float(msg.get("seconds", 10))
        elif cmd == "quit":
            self._stop_camera()
            self._quit.set()
        else:
            self._send({"type": "error", "msg": f"unknown command: {cmd!r}"})

    def start(self, index: int, video: str | None = None) -> None:
        # Open the source once and keep it warm; (re)start only if it isn't
        # running or the selected source changed (macOS AVFoundation hangs on a
        # rapid camera close/reopen, so we toggle streaming instead).
        alive = self._capture_thread is not None and self._capture_thread.is_alive()
        if not alive or index != self._cam_index or video != self._video_path:
            self._stop_camera()
            self._cam_index = index
            self._video_path = video
            self._closing.clear()
            self._capture_thread = threading.Thread(target=self._loop, daemon=True)
            self._capture_thread.start()
        self._streaming = True

    def stop(self) -> None:
        # Stop emitting frames but keep the camera open (warm) for a fast restart.
        self._streaming = False

    def _stop_camera(self) -> None:
        if self._capture_thread is not None:
            self._closing.set()
            self._capture_thread.join(timeout=2.0)
            self._capture_thread = None

    # ── capture loop ─────────────────────────────────────────────
    def _loop(self) -> None:
        try:
            self._ensure_landmarker()
        except Exception as e:
            self._send({"type": "error", "msg": str(e)})
            return

        is_video = bool(self._video_path)
        if is_video:
            cap = cv2.VideoCapture(self._video_path)
            vid_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            frame_interval = 1.0 / (vid_fps if vid_fps > 0 else 30.0)
        else:
            backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
            cap = cv2.VideoCapture(self._cam_index, backend)
            frame_interval = 0.0
        try:
            if not cap.isOpened():
                what = f"Video {self._video_path}" if is_video else f"Kamera {self._cam_index}"
                self._send({"type": "error", "msg": f"{what} konnte nicht geöffnet werden"})
                return

            last_preview = 0.0
            last_frame_t = 0.0
            while not self._closing.is_set():
                if not self._streaming:
                    if is_video:
                        time.sleep(0.03)
                        continue
                    if not cap.grab():   # keep the live camera warm while paused
                        self._send({"type": "error", "msg": "Kamera lieferte kein Bild"})
                        break
                    continue

                if is_video and frame_interval:   # throttle replay to the clip's fps
                    dt = time.perf_counter() - last_frame_t
                    if dt < frame_interval:
                        time.sleep(frame_interval - dt)
                    last_frame_t = time.perf_counter()

                ok, frame_bgr = cap.read()
                if not ok:
                    if is_video:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # loop the clip
                        continue
                    self._send({"type": "error", "msg": "Kamera lieferte kein Bild"})
                    break

                self._maybe_record(frame_bgr)   # write live frames to a clip if requested

                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
                # Absolute monotonic ms; strictly greater than the last value so
                # detect_for_video stays valid even after a stop/start restart.
                ts_ms = max(self._last_ts_ms + 1, int(time.perf_counter() * 1000))
                self._last_ts_ms = ts_ms
                try:
                    result = self._landmarker.detect_for_video(mp_image, ts_ms)
                except Exception as e:
                    self._send({"type": "error", "msg": f"detect_for_video: {e}"})
                    continue

                hands = self._hands_payload(result)
                self._send({"type": "hand", "ts": int(time.time() * 1_000_000), "hands": hands})

                now = time.perf_counter()
                if self._preview_on and (now - last_preview) >= (1.0 / PREVIEW_FPS):
                    last_preview = now
                    # Face landmarks are display-only for now → compute only for
                    # the (throttled) preview, not the full-rate hand stream.
                    face_result = None
                    if self._face_on:
                        fl = self._ensure_face_landmarker()
                        if fl is not None:
                            try:
                                face_result = fl.detect_for_video(mp_image, ts_ms)
                            except Exception:
                                face_result = None
                    self._send(self._preview_payload(frame_bgr, result, face_result))
        finally:
            cap.release()
            if self._writer is not None:
                self._writer.release()
                self._writer = None

    def _maybe_record(self, frame_bgr) -> None:
        """Write live frames to an mp4 clip for ~`record_seconds`, then notify."""
        if not self._record_path:
            return
        if self._writer is None:
            h, w = frame_bgr.shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._writer = cv2.VideoWriter(self._record_path, fourcc, 30.0, (w, h))
        if time.perf_counter() < self._record_until:
            self._writer.write(frame_bgr)
        else:
            self._writer.release()
            self._writer = None
            path, self._record_path = self._record_path, None
            self._send({"type": "recorded", "path": path})

    # ── serialization ────────────────────────────────────────────
    @staticmethod
    def _hands_payload(result) -> list[dict]:
        hands = []
        world = getattr(result, "hand_world_landmarks", None) or []
        handed = getattr(result, "handedness", None) or []
        for i, hand_world in enumerate(world):
            cat = handed[i][0] if i < len(handed) and handed[i] else None
            hands.append({
                "handedness": cat.category_name if cat else "Right",
                "score": float(cat.score) if cat else 1.0,
                "world": [[lm.x, lm.y, lm.z] for lm in hand_world],
            })
        return hands

    @staticmethod
    def _preview_payload(frame_bgr, result, face_result=None) -> dict:
        h, w = frame_bgr.shape[:2]
        if w > PREVIEW_MAX_W:
            scale = PREVIEW_MAX_W / w
            frame_bgr = cv2.resize(frame_bgr, (PREVIEW_MAX_W, int(h * scale)))
            h, w = frame_bgr.shape[:2]
        ok, buf = cv2.imencode(".jpg", frame_bgr,
                               [int(cv2.IMWRITE_JPEG_QUALITY), PREVIEW_JPEG_QUALITY])
        jpeg = base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""

        # Normalized image landmarks per hand for the overlay, + handedness so the
        # main app can attribute an eye-referenced position to the L/R panel.
        image_lms = getattr(result, "hand_landmarks", None) or []
        landmarks = [[[lm.x, lm.y] for lm in hand] for hand in image_lms]
        handed = getattr(result, "handedness", None) or []
        hand_handedness = [(h[0].category_name if h else "Right") for h in handed]

        # Normalized image landmarks of the first face (478 points incl. iris).
        face = []
        face_lms = getattr(face_result, "face_landmarks", None) or [] if face_result else []
        if face_lms:
            face = [[lm.x, lm.y] for lm in face_lms[0]]

        return {"type": "preview", "jpeg": jpeg, "w": w, "h": h,
                "landmarks": landmarks, "hand_handedness": hand_handedness, "face": face}


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _run(reader, writer, model_path: str) -> None:
    lock = threading.Lock()

    def send(obj: dict) -> None:
        line = json.dumps(obj) + "\n"
        with lock:
            try:
                writer.write(line)
                writer.flush()
            except (BrokenPipeError, ValueError):
                pass

    sidecar = Sidecar(send, model_path)
    try:
        for line in reader:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                send({"type": "error", "msg": f"invalid JSON: {line[:80]!r}"})
                continue
            sidecar.handle(msg)
            if sidecar._quit.is_set():
                break
    finally:
        sidecar._stop_camera()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0, help="TCP port of the main app to connect to")
    ap.add_argument("--stdio", action="store_true", help="use stdin/stdout instead of a socket")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    args = ap.parse_args()

    if args.stdio:
        _run(sys.stdin, sys.stdout, args.model)
        return

    if not args.port:
        ap.error("either --port or --stdio is required")

    sock = socket.create_connection(("127.0.0.1", args.port))
    try:
        reader = sock.makefile("r", encoding="utf-8")
        writer = sock.makefile("w", encoding="utf-8")
        _run(reader, writer, args.model)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
