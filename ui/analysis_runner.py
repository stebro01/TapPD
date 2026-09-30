"""Drive a motor paradigm over a bounded video range (VideoLab).

A lean controller (NOT TestScreen): no readiness gate, no SETTLE flush — the
sidecar bounds the range and its `done` message is the authoritative stop.

Handedness: a clinical video usually shows BOTH hands (one tested = moving, one
at rest). MediaPipe's left/right label is from the image perspective and may be
mirrored on phone video, so it can't be trusted to pick the tested hand. Instead
we track both hands, then at the end analyse the hand that actually MOVED (the
one being tested) and attribute it to the clinician's chosen side.
"""

from __future__ import annotations

import logging
import threading

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)


class AnalysisRunner(QObject):
    previewReady = pyqtSignal(dict)        # sidecar preview msg → overlay
    finished = pyqtSignal(object, dict)    # (test, features)
    failed = pyqtSignal(str)
    _doneSignal = pyqtSignal()             # reader thread → GUI marshal

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._src = None
        self._pr = None                    # ParadigmRunner (shared frame-pump, live plot)
        self._raw: dict[str, list] = {"left": [], "right": []}
        self._raw_lock = threading.Lock()  # _feed (reader thread) vs _on_done (GUI)
        self._spec = None
        self._dur = 0.1
        self._chosen = "right"
        self._finished = False
        self._needs_abs = False
        self._frames_total = 0
        self._frames_eyeref = 0
        self._track: dict[int, dict] = {}   # frame index → hands / iris (normalized)
        self._fallback = QTimer(self)
        self._fallback.setSingleShot(True)
        self._fallback.timeout.connect(self._on_done)
        self._doneSignal.connect(self._on_done)

    # ── the screen reads these ────────────────────────────────────
    @property
    def live(self) -> dict:
        return self._pr.live_snapshot() if self._pr else {"left": [], "right": []}

    def metric_label(self) -> str:
        return self._pr.metric_label if self._pr else ""

    @property
    def needs_abs_position(self) -> bool:
        return self._needs_abs

    # ── lifecycle ─────────────────────────────────────────────────
    def start(self, video_path: str, start_s: float, end_s: float,
              paradigm_key: str, hand: str = "right", with_face: bool = False,
              mirrored: bool = False) -> None:
        from capture.mediapipe_capture import WebcamSource
        from capture.source import CAP_ABS_POSITION
        from paradigms import registry
        from paradigms.config import get_task_requirements
        from paradigms.runner import ParadigmRunner
        try:
            if self._src is None:
                self._src = WebcamSource()
                self._src.connect()
            self._spec = registry.get(paradigm_key)
            self._dur = max(0.1, end_s - start_s)
            self._chosen = hand
            # Tremor & co need absolute position → the eye reference (face
            # tracking) is mandatory, regardless of the with_face setting.
            self._needs_abs = CAP_ABS_POSITION in get_task_requirements(paradigm_key)
            with_face = with_face or self._needs_abs
            self._frames_total = 0
            self._frames_eyeref = 0
            self._track = {}
            test = self._spec.load_class()(capture=self._src, duration=self._dur,
                                           hand=hand, **(self._spec.cls_kwargs or {}))
            self._pr = ParadigmRunner(test, sidecar_bounded=True)
            self._pr.begin()
            with self._raw_lock:
                self._raw = {"left": [], "right": []}
            self._finished = False
        except Exception as e:
            log.exception("VideoLab-Analyse konnte nicht gestartet werden")
            self.failed.emit(str(e))
            return

        src = self._src
        # Both hands → pick the moving one. A slower preview stream during
        # analysis: MediaPipe is the bottleneck on a laptop CPU, and every JPEG
        # encode competes with it — the overlay only needs a few frames a second.
        src.configure(num_hands=2, preview_fps=6)
        # Per-clip mirror flag (set at import); the sidecar reads it with
        # the next start and swaps the handedness label to match.
        src.replay_mirror = bool(mirrored)
        # Faster than real time: a 20 s take no longer costs 20 s of waiting.
        src.play_range(video_path, start_s, end_s, realtime=False)
        src.set_preview_callback(lambda m: self.previewReady.emit(m))
        src.set_done_callback(lambda: self._doneSignal.emit())
        src.enable_preview(True)
        src.enable_face(with_face)
        src.start_tracking(self._feed)
        from video.config import cfg
        margin = float(cfg("analysis", "done_fallback_margin_s", default=4.0))
        self._fallback.start(int((end_s - start_s + margin) * 1000))

    def _feed(self, tf) -> None:           # reader thread (TrackingFrame envelope)
        with self._raw_lock:
            for frame in tf.hands:
                self._raw.setdefault(frame.hand_type, []).append(frame)
                self._frames_total += 1
                if getattr(frame, "eye_ref_mm", None) is not None:
                    self._frames_eyeref += 1
                # Per-frame track for the overlay: exactly what the analysis
                # saw, so a review never shows something it did not measure.
                idx = getattr(frame, "frame_index", None)
                lms = getattr(frame, "image_landmarks", None)
                if idx is not None and lms:
                    entry = self._track.setdefault(int(idx), {"hands": [], "iris": None})
                    entry["hands"].append([frame.hand_type, lms])
                    iris = getattr(frame, "iris_norm", None)
                    if iris:
                        entry["iris"] = iris
        if self._pr is not None:
            self._pr.feed(tf)              # live metric for both hands (the plot)

    def track_data(self) -> dict:
        """The per-frame overlay track of the last run: {frame: {hands, iris}}."""
        with self._raw_lock:
            return {k: v for k, v in self._track.items()}

    def eye_ref_coverage(self) -> float:
        """Fraction of frames that carried an eye reference (0..1)."""
        with self._raw_lock:
            return (self._frames_eyeref / self._frames_total) if self._frames_total else 0.0

    def _dominant_hand(self, live: dict) -> str | None:
        """The hand that moved most (largest live-metric range) = the tested one."""

        def span(data):
            vals = [v for _, v in data]
            return (max(vals) - min(vals)) if len(vals) > 2 else 0.0

        scores = {h: span(live.get(h, [])) for h in ("left", "right")}
        if max(scores.values(), default=0.0) <= 0.0:
            return None
        return max(scores, key=scores.get)

    def _on_done(self) -> None:            # GUI thread
        self._fallback.stop()
        if self._pr is None or self._finished:
            return
        self._finished = True
        try:
            if self._spec.bilateral:
                test = self._pr.test       # bilateral keeps both hands as recorded
                test.stop()
            else:
                # Recompute on the MOVING hand, relabelled to the chosen side.
                live = self._pr.live_snapshot()
                moving = self._dominant_hand(live)
                with self._raw_lock:
                    frames = list(self._raw.get(moving, [])) if moving else []
                for f in frames:
                    f.hand_type = self._chosen
                test = self._spec.load_class()(capture=self._src, duration=self._dur,
                                               hand=self._chosen, **(self._spec.cls_kwargs or {}))
                for f in frames:
                    test._on_frame(f)
                # Final plot: show the moving hand under the chosen side.
                if moving:
                    other = "left" if self._chosen == "right" else "right"
                    self._pr.replace_live({self._chosen: live.get(moving, []), other: []})
            features = test.compute_features()
        except Exception as e:
            log.exception("compute_features fehlgeschlagen")
            self.failed.emit(str(e))
            return
        self.finished.emit(test, features)

    def teardown(self) -> None:
        self._fallback.stop()
        self._pr = None
        self._raw = {"left": [], "right": []}
        if self._src is not None:
            try:
                self._src.stop_recording()
                self._src.disconnect()
            except Exception:
                pass
            self._src = None
