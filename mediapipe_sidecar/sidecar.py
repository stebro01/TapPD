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

# How long a live camera may fail to deliver before the stream is given up:
# ~2 s at 20 ms per retry.  Long enough to cover a slow-starting USB camera and
# the odd dropped frame, short enough that a genuinely dead device is reported
# quickly rather than hanging the tracking screen.
_MAX_CONSECUTIVE_MISSES = 100
_MISS_RETRY_S = 0.02


def _camera_backend() -> int:
    """Preferred VideoCapture backend for live cameras."""
    if sys.platform == "darwin":
        return cv2.CAP_AVFOUNDATION
    if sys.platform == "win32":
        # DirectShow rather than Media Foundation: MSMF takes seconds just to
        # open a device (8.5 s for an OBSBOT Tiny 2, 3.3 s for a built-in
        # webcam), DShow returns in well under one.  CAP_ANY picks MSMF, and
        # that delay is what made switching cameras look like it had silently
        # failed -- the preview only appeared long after the user gave up.
        return cv2.CAP_DSHOW
    return cv2.CAP_ANY


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
_IRIS_L, _IRIS_R = 468, 473        # MediaPipe iris-centre landmark indices
_PALM_ANCHORS = (0, 1, 5, 9, 13, 17)   # wrist + thumb-CMC + four MCPs
# FaceMesh eye landmarks (image person's left eye = viewer right side etc. —
# we keep MediaPipe's own left/right naming, matching the iris indices):
# per eye: (outer corner, inner corner, upper lid, lower lid)
_EYE_L = (33, 133, 159, 145)
_EYE_R = (263, 362, 386, 374)

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
        # Enumerate through the same backend we capture with, so the indices
        # returned here are the ones VideoCapture will accept.  Passing CAP_ANY
        # instead lists every device once per Windows backend (MSMF *and*
        # DSHOW), disambiguated by adding the backend offset to the index
        # (1400 + n, 700 + n) — each camera twice, under unusable indices.
        backend = _camera_backend()
        cams = []
        for info in enumerate_cameras(backend):
            cams.append({"index": int(info.index), "name": str(info.name)})
        if cams:
            return cams
    except ImportError as e:
        # On Windows this is almost always the missing Visual C++ runtime, not a
        # missing package — the extension links against MSVCP140/VCRUNTIME140.
        # Say so, because the fallback below is slow enough to look like a hang.
        print(f"list_cameras: named enumeration unavailable ({e}); "
              "falling back to index probing. On Windows this usually means the "
              "Visual C++ runtime is missing: "
              "winget install --id Microsoft.VCRedist.2015+.x64",
              file=sys.stderr, flush=True)
    except Exception as e:
        print(f"list_cameras: named enumeration failed ({e!r})",
              file=sys.stderr, flush=True)

    # Fallback: probe indices 0..7
    cams = []
    for i in range(8):
        cap = cv2.VideoCapture(i, _camera_backend())
        if cap is not None and cap.isOpened():
            cams.append({"index": i, "name": f"Camera {i}"})
        if cap is not None:
            cap.release()   # also release the misses — they hold the device open
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
        # Tunables (overridable via the {"cmd":"config"} message; defaults below).
        self._preview_fps = PREVIEW_FPS
        self._preview_max_w = PREVIEW_MAX_W
        self._jpeg_quality = PREVIEW_JPEG_QUALITY
        self._hand_confidence = 0.5
        self._tracking_confidence = 0.5
        self._num_hands = 2
        self._record_fps = 30.0
        self._record_codec = "avc1"   # matches capture.yaml record_codec (config push overrides)
        # Requested capture format (0 = whatever the driver picks). Cameras that
        # offer several formats otherwise negotiate one on their own, which is
        # not always the one we want — and on Windows not always one that works.
        self._camera_width = 0
        self._camera_height = 0
        self._camera_fps = 0.0
        # Mirror live camera frames (selfie view).  A webcam delivers the scene
        # as it sees it, so the patient's left hand appears on the right of the
        # picture -- confusing to sit in front of, and the wrong way round for
        # MediaPipe, whose handedness output assumes a mirrored input image.
        # Flipping here fixes the view and the labels in one place.  Video
        # replay is never mirrored: a clip is not a live self-view.
        self._mirror = True
        self._capture_thread: threading.Thread | None = None
        self._closing = threading.Event()   # tells the camera thread to exit
        self._quit = threading.Event()      # process should terminate
        self._streaming = False             # emit hand/preview frames?
        self._preview_on = False
        self._face_on = True                # run the face landmarker? (optional)
        self._cam_index = 0
        self._video_path = None             # if set, loop this video instead of the camera
        # Optional bounded play range (VideoLab): play [start_s, end_s] once.
        self._range_start_s = None
        self._range_end_s = None
        self._loop_video = True             # False = play the range once, then emit "done"
        self._done_sent = False
        # Video recording (writes the live frames to a clip for later replay).
        self._record_path = None
        self._record_until = 0.0
        self._writer = None
        # VIDEO-mode HandLandmarker requires strictly increasing timestamps, and
        # the landmarker instance is reused across start/stop — so this must keep
        # increasing across restarts (a per-loop t0 would reset and break it).
        self._last_ts_ms = -1
        # Eye reference (iris centres, PIXELS) for absolute hand position: the
        # face runs at its own low cadence (the head barely moves) and the last
        # result is attached to every full-rate hand message.
        self._last_iris_px = None            # [[xL,yL],[xR,yR]] or None
        self._last_iris_ts_ms = -1
        self._last_face_result = None        # reused by the preview payload
        self._last_face_t = 0.0
        self._face_interval = 0.2            # s between face detections (0 = full rate)

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
            num_hands=self._num_hands,
            min_hand_detection_confidence=self._hand_confidence,
            min_tracking_confidence=self._tracking_confidence,
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
            ss = msg.get("start_s")
            ee = msg.get("end_s")
            self.start(int(msg.get("index", 0)), msg.get("video") or None,
                       None if ss is None else float(ss),
                       None if ee is None else float(ee),
                       bool(msg.get("loop", True)))
        elif cmd == "stop":
            self.stop()
        elif cmd == "config":
            self._apply_config(msg)
        elif cmd == "preview":
            self._preview_on = bool(msg.get("on", False))
        elif cmd == "face":
            self._face_on = bool(msg.get("on", True))
            # "eco" (~5 Hz, eye reference for tremor) or "full" (every frame,
            # dedicated {"type":"face"} stream for ocular paradigms).
            self._face_interval = 0.0 if msg.get("rate") == "full" else 0.2
        elif cmd == "record":
            # Record the live frames to a clip for ~`seconds`, for later replay.
            self._record_path = msg.get("path")
            self._record_until = time.perf_counter() + float(msg.get("seconds", 10))
        elif cmd == "quit":
            self._stop_camera()
            self._quit.set()
        else:
            self._send({"type": "error", "msg": f"unknown command: {cmd!r}"})

    def _apply_config(self, msg: dict) -> None:
        """Apply tunables pushed from the main app (preview/confidence/record)."""
        self._preview_fps = float(msg.get("preview_fps", self._preview_fps))
        self._preview_max_w = int(msg.get("preview_max_width", self._preview_max_w))
        self._jpeg_quality = int(msg.get("jpeg_quality", self._jpeg_quality))
        self._hand_confidence = float(msg.get("hand_confidence", self._hand_confidence))
        self._tracking_confidence = float(msg.get("tracking_confidence", self._tracking_confidence))
        new_n = int(msg.get("num_hands", self._num_hands))
        if new_n != self._num_hands:
            self._num_hands = new_n
            self._landmarker = None   # recreate with the new hand count
        self._record_fps = float(msg.get("record_fps", self._record_fps))
        self._record_codec = str(msg.get("record_codec", self._record_codec))
        # Requested capture format; 0 means "leave it to the driver".
        self._camera_width = int(msg.get("camera_width", self._camera_width))
        self._camera_height = int(msg.get("camera_height", self._camera_height))
        self._camera_fps = float(msg.get("camera_fps", self._camera_fps))
        self._mirror = bool(msg.get("mirror", self._mirror))

    def start(self, index: int, video: str | None = None,
              start_s: float | None = None, end_s: float | None = None,
              loop: bool = True) -> None:
        # Open the source once and keep it warm; (re)start only if it isn't
        # running or the selected source changed (macOS AVFoundation hangs on a
        # rapid camera close/reopen, so we toggle streaming instead). A new play
        # range or loop flag also forces a restart so the seek/bounds re-apply.
        alive = self._capture_thread is not None and self._capture_thread.is_alive()
        changed = (index != self._cam_index or video != self._video_path
                   or start_s != self._range_start_s or end_s != self._range_end_s
                   or loop != self._loop_video)
        # Play-once (loop=False) always restarts so a re-run of the SAME range
        # re-seeks to the onset; otherwise the thread idles at the offset with
        # _done_sent set and a re-run would emit nothing.
        if not alive or changed or not loop:
            self._stop_camera()
            self._cam_index = index
            self._video_path = video
            self._range_start_s = start_s
            self._range_end_s = end_s
            self._loop_video = loop
            self._done_sent = False
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
        start_frame = 0
        end_frame = None
        if is_video:
            cap = cv2.VideoCapture(self._video_path)
            vid_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            if vid_fps <= 0:
                vid_fps = 30.0
            frame_interval = 1.0 / vid_fps
            # Optional bounded play range → frame bounds (seconds×fps). The
            # `hand` timestamp is wall-clock, so the offset is enforced here by
            # frame count, and "done" (loop=False) is the authoritative stop.
            if self._range_start_s is not None:
                start_frame = max(0, round(self._range_start_s * vid_fps))
            if self._range_end_s is not None:
                end_frame = round(self._range_end_s * vid_fps)
            if start_frame:
                cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        else:
            backend = _camera_backend()
            print(f"opening camera {self._cam_index}...", file=sys.stderr, flush=True)
            _t_open = time.time()
            cap = cv2.VideoCapture(self._cam_index, backend)
            print(f"camera {self._cam_index}: open returned after "
                  f"{time.time() - _t_open:.1f}s, isOpened={cap.isOpened()}",
                  file=sys.stderr, flush=True)
            frame_interval = 0.0
            # Pin the capture format when one is configured. Drivers may refuse
            # and fall back to a nearby mode, so report what we actually got --
            # a camera silently running at a different resolution than intended
            # is otherwise invisible until the numbers look wrong.
            if cap.isOpened():
                if self._camera_width and self._camera_height:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._camera_width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._camera_height)
                if self._camera_fps:
                    cap.set(cv2.CAP_PROP_FPS, self._camera_fps)
                got_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                got_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                got_fps = cap.get(cv2.CAP_PROP_FPS)   # DirectShow often reports 0
                print(f"camera {self._cam_index}: {got_w}x{got_h}"
                      + (f" @ {got_fps:.0f} fps" if got_fps > 0 else "")
                      + (f" (requested {self._camera_width}x{self._camera_height})"
                         if self._camera_width and self._camera_height
                         and (got_w, got_h) != (self._camera_width, self._camera_height)
                         else ""),
                      file=sys.stderr, flush=True)
        try:
            if not cap.isOpened():
                what = f"Video {self._video_path}" if is_video else f"Kamera {self._cam_index}"
                self._send({"type": "error", "msg": f"{what} konnte nicht geöffnet werden"})
                return

            last_preview = 0.0
            last_frame_t = 0.0
            frame_idx = start_frame   # local count; CAP_PROP_POS_FRAMES is unreliable
            # A single miss is normal: USB cameras drop frames, and some need a
            # moment before the first one arrives (an OBSBOT Tiny 2 takes ~1.6 s,
            # against ~0.3 s for a built-in webcam).  Tearing the stream down on
            # the first failed read turned that startup delay into "no image at
            # all", so only give up once the misses persist.
            misses = 0
            while not self._closing.is_set():
                if not self._streaming:
                    if is_video:
                        time.sleep(0.03)
                        continue
                    if cap.grab():   # keep the live camera warm while paused
                        misses = 0
                    else:
                        misses += 1
                        if misses > _MAX_CONSECUTIVE_MISSES:
                            self._send({"type": "error", "msg": "Kamera lieferte kein Bild"})
                            break
                        time.sleep(_MISS_RETRY_S)
                    continue

                if is_video and frame_interval:   # throttle replay to the clip's fps
                    dt = time.perf_counter() - last_frame_t
                    if dt < frame_interval:
                        time.sleep(frame_interval - dt)
                    last_frame_t = time.perf_counter()

                # Reached the offset of a bounded range → end of this pass.
                at_end = end_frame is not None and frame_idx >= end_frame
                ok, frame_bgr = (False, None) if at_end else cap.read()
                if at_end or not ok:
                    if is_video and self._loop_video:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)   # loop the range
                        frame_idx = start_frame
                        continue
                    if is_video:
                        # Play-once: emit "done" once, then idle warm (keep the file
                        # open so a re-run / new range restarts cheaply).
                        if not self._done_sent:
                            self._done_sent = True
                            self._send({"type": "done"})
                        self._streaming = False
                        continue
                    misses += 1
                    if misses > _MAX_CONSECUTIVE_MISSES:
                        self._send({"type": "error", "msg": "Kamera lieferte kein Bild"})
                        break
                    time.sleep(_MISS_RETRY_S)
                    continue
                misses = 0
                frame_idx += 1

                # Mirror before anything else looks at the frame, so preview,
                # landmarks, handedness and recorded clips all share one
                # orientation.
                if self._mirror and not is_video:
                    frame_bgr = cv2.flip(frame_bgr, 1)

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

                now = time.perf_counter()
                # Face at its own cadence: "eco" ~5 Hz (eye reference for the
                # hand stream) or "full" (every frame → dedicated face stream
                # for ocular paradigms).
                if self._face_on and (now - self._last_face_t) >= self._face_interval:
                    self._last_face_t = now
                    fl = self._ensure_face_landmarker()
                    if fl is not None:
                        try:
                            fres = fl.detect_for_video(mp_image, ts_ms)
                        except Exception:
                            fres = None
                        self._last_face_result = fres
                        h0, w0 = frame_bgr.shape[:2]
                        lms = fres.face_landmarks[0] if (fres and fres.face_landmarks) else None
                        if lms is not None and len(lms) > _IRIS_R:
                            self._last_iris_px = [
                                [lms[_IRIS_L].x * w0, lms[_IRIS_L].y * h0],
                                [lms[_IRIS_R].x * w0, lms[_IRIS_R].y * h0]]
                            self._last_iris_ts_ms = ts_ms
                            self._send(self._face_payload(lms, w0, h0))

                h0, w0 = frame_bgr.shape[:2]
                hands = self._hands_payload(result, w0, h0)
                msg = {"type": "hand", "ts": int(time.time() * 1_000_000), "hands": hands}
                if self._face_on and self._last_iris_px is not None:
                    msg["iris_px"] = self._last_iris_px
                    msg["iris_age_ms"] = int(ts_ms - self._last_iris_ts_ms)
                self._send(msg)

                if self._preview_on and (now - last_preview) >= (1.0 / self._preview_fps):
                    last_preview = now
                    self._send(self._preview_payload(frame_bgr, result,
                                                     self._last_face_result if self._face_on else None))
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
            fourcc = cv2.VideoWriter_fourcc(*self._record_codec)
            self._writer = cv2.VideoWriter(self._record_path, fourcc, self._record_fps, (w, h))
        if time.perf_counter() < self._record_until:
            self._writer.write(frame_bgr)
        else:
            self._writer.release()
            self._writer = None
            path, self._record_path = self._record_path, None
            self._send({"type": "recorded", "path": path})

    # ── serialization ────────────────────────────────────────────
    @staticmethod
    def _hands_payload(result, w: int = 0, h: int = 0) -> list[dict]:
        hands = []
        world = getattr(result, "hand_world_landmarks", None) or []
        image = getattr(result, "hand_landmarks", None) or []
        handed = getattr(result, "handedness", None) or []
        for i, hand_world in enumerate(world):
            cat = handed[i][0] if i < len(handed) and handed[i] else None
            entry = {
                "handedness": cat.category_name if cat else "Right",
                "score": float(cat.score) if cat else 1.0,
                "world": [[lm.x, lm.y, lm.z] for lm in hand_world],
            }
            # Palm centre in image PIXELS — with the iris reference this yields
            # an absolute (eye-referenced) hand position in the main app.
            if w and h and i < len(image) and len(image[i]) >= 21:
                lms = image[i]
                entry["palm_px"] = [
                    sum(lms[j].x for j in _PALM_ANCHORS) / len(_PALM_ANCHORS) * w,
                    sum(lms[j].y for j in _PALM_ANCHORS) / len(_PALM_ANCHORS) * h,
                ]
            hands.append(entry)
        return hands

    @staticmethod
    def _face_payload(lms, w: int, h: int) -> dict:
        """Dedicated face message: iris centres, eye corners (PIXELS) and the
        eye-aspect-ratio per eye (blink detection) — enough for fixation/
        blink paradigms without shipping all 478 landmarks at full rate."""
        def px(i):
            return [lms[i].x * w, lms[i].y * h]

        def ear(eye):
            outer, inner, top, bottom = (px(i) for i in eye)
            hx, hy = inner[0] - outer[0], inner[1] - outer[1]
            vx, vy = bottom[0] - top[0], bottom[1] - top[1]
            horiz = (hx * hx + hy * hy) ** 0.5
            vert = (vx * vx + vy * vy) ** 0.5
            return round(vert / horiz, 4) if horiz > 1e-6 else 0.0

        return {
            "type": "face",
            "ts": int(time.time() * 1_000_000),
            "iris_px": [px(_IRIS_L), px(_IRIS_R)],
            "corners_px": [[px(_EYE_L[0]), px(_EYE_L[1])],
                           [px(_EYE_R[0]), px(_EYE_R[1])]],
            "ear": [ear(_EYE_L), ear(_EYE_R)],
            "nose_px": px(1),   # nose tip — head-yaw proxy for the head guard
            "w": w, "h": h,
        }

    def _preview_payload(self, frame_bgr, result, face_result=None) -> dict:
        h, w = frame_bgr.shape[:2]
        if w > self._preview_max_w:
            scale = self._preview_max_w / w
            frame_bgr = cv2.resize(frame_bgr, (self._preview_max_w, int(h * scale)))
            h, w = frame_bgr.shape[:2]
        ok, buf = cv2.imencode(".jpg", frame_bgr,
                               [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality])
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
