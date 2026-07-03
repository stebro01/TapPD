"""Drive a paradigm over a frame stream — the shared frame-pump.

Both the live TestScreen and VideoLab feed HandFrames to a paradigm and read a
per-hand live metric for plotting. This owns that pump (gating + accumulation +
live buffers + last-frame stash) so the two screens don't duplicate it.

- Live test: a SETTLE delay discards stale buffered frames, and a wall-clock
  duration bounds the take (`settle_s`, `duration_s`).
- VideoLab: the sidecar bounds the range and signals `done`, so no gating
  (`sidecar_bounded=True`).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace

from capture.base_capture import TrackingFrame

log = logging.getLogger(__name__)


class ParadigmRunner:
    def __init__(self, test, *, settle_s: float = 0.0, duration_s: float | None = None,
                 sidecar_bounded: bool = False) -> None:
        self.test = test
        self._settle_s = settle_s
        self._duration_s = duration_s
        self._sidecar_bounded = sidecar_bounded
        self.live: dict[str, list[tuple[float, float]]] = {"left": [], "right": []}
        self.last_frame: dict = {}
        self._t0_us: int | None = None
        self._wall_start = 0.0
        self.duration_reached = False
        self._lock = threading.Lock()

    @property
    def metric_label(self) -> str:
        try:
            return self.test.get_live_metric_label()
        except Exception:
            return ""

    def begin(self) -> None:
        """Reset paradigm + live state and start the SETTLE clock."""
        self.test.frames.clear()
        self.test.left_frames.clear()
        self.test.right_frames.clear()
        self.live = {"left": [], "right": []}
        self.last_frame = {}
        self._t0_us = None
        self._wall_start = time.perf_counter()
        self.duration_reached = False

    def feed(self, obj) -> None:
        """Frame callback: gate, accumulate into the paradigm, extract live metric.

        Accepts a multimodal ``TrackingFrame`` envelope (all hands of one
        sensor frame, + face/gaze) or — backward compatible — a single
        ``HandFrame``."""
        try:
            tf = obj if isinstance(obj, TrackingFrame) else \
                TrackingFrame(timestamp_us=obj.timestamp_us, hands=[obj])
            if self._settle_s and (time.perf_counter() - self._wall_start) < self._settle_s:
                return   # discard stale frames buffered before recording began
            if self._t0_us is None:
                self._t0_us = tf.timestamp_us
            if not self._sidecar_bounded and self._duration_s is not None:
                if (tf.timestamp_us - self._t0_us) > self._duration_s * 1_000_000:
                    self.duration_reached = True
                    return
            # Adapt once (source seam, e.g. webcam eye-referenced position) so
            # paradigm AND live metric see the same view. adapt_frame is
            # idempotent, so the paradigm's own per-hand adapt is a no-op.
            adapted = [self.test._profile.adapt_frame(h) for h in tf.hands]
            self.test._on_tracking(replace(tf, hands=adapted))
            t = (tf.timestamp_us - self._t0_us) / 1e6
            for frame in adapted:
                try:
                    m = float(self.test.get_live_metric(frame))
                except Exception:
                    m = 0.0
                with self._lock:
                    self.live.setdefault(frame.hand_type, []).append((t, m))
                    self.last_frame[frame.hand_type] = frame
            # Face live metric (ocular paradigms expose get_face_metric).
            if tf.face is not None:
                gfm = getattr(self.test, "get_face_metric", None)
                if gfm is not None:
                    try:
                        m = float(gfm(tf.face))
                    except Exception:
                        m = 0.0
                    with self._lock:
                        self.live.setdefault("face", []).append((t, m))
        except Exception:
            log.exception("Fehler im Paradigma-Frame-Pump")

    def live_snapshot(self) -> dict:
        with self._lock:
            return {k: list(v) for k, v in self.live.items()}

    def replace_live(self, live: dict[str, list[tuple[float, float]]]) -> None:
        """Swap the live buffers (e.g. relabel hands after analysis) under the lock."""
        with self._lock:
            self.live = live
