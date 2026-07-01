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
        self._spec = None
        self._dur = 0.1
        self._chosen = "right"
        self._finished = False
        self._fallback = QTimer(self)
        self._fallback.setSingleShot(True)
        self._fallback.timeout.connect(self._on_done)
        self._doneSignal.connect(self._on_done)

    # ── the screen reads these ────────────────────────────────────
    @property
    def live(self) -> dict:
        return self._pr.live if self._pr else {"left": [], "right": []}

    def metric_label(self) -> str:
        return self._pr.metric_label if self._pr else ""

    # ── lifecycle ─────────────────────────────────────────────────
    def start(self, video_path: str, start_s: float, end_s: float,
              paradigm_key: str, hand: str = "right", with_face: bool = False) -> None:
        from capture.mediapipe_capture import WebcamSource
        from motor_tests import registry
        from motor_tests.runner import ParadigmRunner
        try:
            if self._src is None:
                self._src = WebcamSource()
                self._src.connect()
            self._spec = registry.get(paradigm_key)
            self._dur = max(0.1, end_s - start_s)
            self._chosen = hand
            test = self._spec.load_class()(capture=self._src, duration=self._dur,
                                           hand=hand, **(self._spec.cls_kwargs or {}))
            self._pr = ParadigmRunner(test, sidecar_bounded=True)
            self._pr.begin()
            self._raw = {"left": [], "right": []}
            self._finished = False
        except Exception as e:
            log.exception("VideoLab-Analyse konnte nicht gestartet werden")
            self.failed.emit(str(e))
            return

        src = self._src
        src.configure(num_hands=2)          # track both hands → pick the moving one
        src.play_range(video_path, start_s, end_s)
        src.set_preview_callback(lambda m: self.previewReady.emit(m))
        src.set_done_callback(lambda: self._doneSignal.emit())
        src.enable_preview(True)
        src.enable_face(with_face)
        src.start_recording(self._feed)
        from video.config import cfg
        margin = float(cfg("analysis", "done_fallback_margin_s", default=4.0))
        self._fallback.start(int((end_s - start_s + margin) * 1000))

    def _feed(self, frame) -> None:        # reader thread
        self._raw.setdefault(frame.hand_type, []).append(frame)
        if self._pr is not None:
            self._pr.feed(frame)           # live metric for both hands (the plot)

    def _dominant_hand(self) -> str | None:
        """The hand that moved most (largest live-metric range) = the tested one."""
        live = self._pr.live if self._pr else {}

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
                moving = self._dominant_hand()
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
                    self._pr.live = {self._chosen: self._pr.live.get(moving, []), other: []}
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
