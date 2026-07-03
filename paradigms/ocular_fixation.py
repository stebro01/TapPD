"""Okulomotorik: Fixationsstabilität & Blinzeln (erste OCULAR-Kategorie).

Der Patient fixiert die Kamera; ausgewertet wird der FacePose-Stream
(Iris + Augenwinkel + Eye-Aspect-Ratio) des Sidecars:

- **Blinkrate** (Blinzeln/min) — bei Parkinson typischerweise reduziert.
- **Fixationsstabilität** — RMS des Blickversatzes (Iris relativ zu den
  Augenwinkeln, in IPD-Einheiten → kopfbewegungs- und abstandsrobust).
- **Sakkadische Intrusionen** — abrupte Blicksprünge während der Fixation.
- **Mittlere Lidspalte** (EAR) als Hypomimie-Hinweis.

Kein Hand-Tracking nötig: das Paradigma konsumiert ``TrackingFrame.face``.
"""

from __future__ import annotations

import math

from capture.base_capture import BaseCaptureDevice, FacePose, HandFrame, TrackingFrame
from paradigms.base_test import BaseParadigm
from paradigms.config import get_test_config


class OcularFixationTest(BaseParadigm):
    bilateral = False

    def __init__(self, capture: BaseCaptureDevice, duration: float = 20.0,
                 hand: str = "both") -> None:
        super().__init__(capture, duration, hand)
        self.face_frames: list[FacePose] = []
        cfg = get_test_config(self.test_type()).get("analysis", {})
        self._blink_ear = float(cfg.get("blink_ear_threshold", 0.18))
        self._intrusion_ipd = float(cfg.get("intrusion_threshold_ipd", 0.05))
        self._min_face_frames = int(cfg.get("min_face_frames", 30))

    def test_type(self) -> str:
        return "ocular_fixation"

    def get_instructions(self) -> str:
        return (
            "Fixation & Blinzeln (Okulomotorik)\n\n"
            "Schauen Sie ruhig und entspannt direkt in die Kamera.\n\n"
            "Wichtig:\n"
            "- Kopf möglichst still halten\n"
            "- Blick auf die Kamera gerichtet lassen\n"
            "- Normal blinzeln, nichts unterdrücken\n"
            "- Gesicht gut ausgeleuchtet und vollständig im Bild"
        )

    # ── frame intake ───────────────────────────────────────────────
    def start(self) -> None:
        self.face_frames.clear()
        # Ocular paradigms need the full-rate face stream.
        enable = getattr(self.capture, "enable_face", None)
        if enable is not None:
            enable(True, full_rate=True)
        super().start()

    def _on_tracking(self, tf: TrackingFrame) -> None:
        if tf.face is not None:
            with self._lock:
                self.face_frames.append(tf.face)
        super()._on_tracking(tf)   # hands (if any) are irrelevant but harmless

    # ── live metric ────────────────────────────────────────────────
    def get_live_metric(self, frame: HandFrame) -> float:
        return 0.0   # hand frames carry no signal for this paradigm

    def get_face_metric(self, face: FacePose) -> float:
        """Blickversatz vom Fixationspunkt in %IPD (Live-Plot)."""
        ox, oy = face.gaze_offset_ipd
        return math.hypot(ox, oy) * 100.0

    def get_live_metric_label(self) -> str:
        return "Blickversatz (%IPD)"

    # ── features ───────────────────────────────────────────────────
    def compute_features(self) -> dict[str, float]:
        with self._lock:
            faces = list(self.face_frames)
        if len(faces) < self._min_face_frames:
            return {"n_face_frames": float(len(faces)), "face_coverage": 0.0,
                    "blink_rate_per_min": 0.0, "gaze_dispersion_pct_ipd": 0.0,
                    "saccadic_intrusions_per_min": 0.0, "mean_ear": 0.0}

        t0, t1 = faces[0].timestamp_us, faces[-1].timestamp_us
        span_s = max(1e-3, (t1 - t0) / 1e6)
        minutes = span_s / 60.0

        # Blinks: EAR below threshold, with a refractory period (one blink =
        # one below-threshold episode).
        blinks = 0
        below = False
        for f in faces:
            if f.ear < self._blink_ear:
                if not below:
                    blinks += 1
                below = True
            else:
                below = False

        # Gaze offsets while the eyes are OPEN (blinks corrupt the iris fit).
        offsets = [f.gaze_offset_ipd for f in faces if f.ear >= self._blink_ear]
        if len(offsets) >= 2:
            mx = sum(o[0] for o in offsets) / len(offsets)
            my = sum(o[1] for o in offsets) / len(offsets)
            rms = math.sqrt(sum((o[0] - mx) ** 2 + (o[1] - my) ** 2
                                for o in offsets) / len(offsets))
            intrusions = sum(
                1 for a, b in zip(offsets, offsets[1:])
                if math.hypot(b[0] - a[0], b[1] - a[1]) > self._intrusion_ipd)
        else:
            rms, intrusions = 0.0, 0

        open_faces = [f for f in faces if f.ear >= self._blink_ear]
        mean_ear = (sum(f.ear for f in open_faces) / len(open_faces)) if open_faces else 0.0

        expected = span_s * max(1.0, self.capture.sample_rate)
        return {
            "blink_rate_per_min": round(blinks / minutes, 2) if minutes > 0 else 0.0,
            "gaze_dispersion_pct_ipd": round(rms * 100.0, 3),
            "saccadic_intrusions_per_min": round(intrusions / minutes, 2) if minutes > 0 else 0.0,
            "mean_ear": round(mean_ear, 3),
            "n_face_frames": float(len(faces)),
            "face_coverage": round(min(1.0, len(faces) / expected), 3),
        }
