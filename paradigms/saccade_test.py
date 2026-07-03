"""Sakkaden-Test: 5-Punkt-Eichung, dann gaze-contingente Zufallsziele.

Misst mit der Webcam (FacePose-Stream), wie schnell und zuverlässig der Blick
zwischen weit auseinanderliegenden Bildschirmpunkten springt:
Ziele/min, Sakkaden-Latenz (Stimulus → Ankunft), Richtungsfehler.

Ehrlichkeitsgrenze (30-Hz-Kamera): eine echte Spitzengeschwindigkeit in °/s
ist NICHT messbar (Sakkaden dauern 30–80 ms); die Latenz- und Zählmetriken
sind es. Der Kopfpose-Wächter (Roll/IPD/Nase relativ zur Eichung) markiert
Abschnitte mit bewegtem Kopf als ungültig.

Die Task-Logik (Eichung, Klassifikator, Zustandsautomat) lebt headless in
``saccade_logic.py``; der Stimulus-Screen ist ``ui/saccade_screen.py``.
"""

from __future__ import annotations

import math

from capture.base_capture import BaseCaptureDevice, FacePose, HandFrame, TrackingFrame
from paradigms.base_test import BaseParadigm
from paradigms.config import get_test_config
from paradigms.saccade_logic import Phase, SaccadeTask


class SaccadeTest(BaseParadigm):
    bilateral = False

    def __init__(self, capture: BaseCaptureDevice, duration: float = 30.0,
                 hand: str = "both") -> None:
        super().__init__(capture, duration, hand)
        self.face_frames: list[FacePose] = []
        cfg = get_test_config(self.test_type())
        calib = cfg.get("calibration", {})
        test = cfg.get("test", {})
        guard = cfg.get("head_guard", {})
        analysis = cfg.get("analysis", {})
        self._min_hits = int(analysis.get("min_hits", 3))
        self.task = SaccadeTask(
            calib_per_point_s=float(calib.get("per_point_s", 2.0)),
            calib_settle_s=float(calib.get("settle_s", 0.6)),
            calib_min_samples=int(calib.get("min_samples", 12)),
            calib_max_spread=float(calib.get("max_spread_ipd", 0.06)),
            duration_s=float(duration or test.get("duration_s", 30)),
            dwell_s=float(test.get("dwell_s", 0.2)),
            confidence_margin=float(test.get("confidence_margin", 1.3)),
            min_separation=float(test.get("min_separation_ipd", 0.04)),
            prefer_far_targets=bool(test.get("prefer_far_targets", True)),
            blink_ear=float(analysis.get("blink_ear_threshold", 0.18)),
            guard_max_roll_deg=float(guard.get("max_roll_deg", 8.0)),
            guard_max_ipd_change=float(guard.get("max_ipd_change", 0.12)),
            guard_max_nose_shift=float(guard.get("max_nose_shift_ipd", 0.15)),
        )
        self._t0_us: int | None = None

    def test_type(self) -> str:
        return "saccade_test"

    def get_instructions(self) -> str:
        return (
            "Sakkaden-Test (Okulomotorik)\n\n"
            "Phase 1 – Eichung: Schauen Sie ruhig auf den jeweils "
            "angezeigten Punkt (5 Positionen).\n\n"
            "Phase 2 – Test: Schauen Sie SO SCHNELL WIE MÖGLICH auf den "
            "aufleuchtenden Punkt. Sobald Ihr Blick erkannt wird, springt "
            "der Punkt weiter.\n\n"
            "Wichtig: Kopf möglichst still halten — nur die Augen bewegen!"
        )

    # ── frame intake ───────────────────────────────────────────────
    def start(self) -> None:
        self.face_frames.clear()
        enable = getattr(self.capture, "enable_face", None)
        if enable is not None:
            enable(True, full_rate=True)
        super().start()

    def _on_tracking(self, tf: TrackingFrame) -> None:
        face = tf.face
        if face is None:
            return
        with self._lock:
            self.face_frames.append(face)
            if self._t0_us is None:
                self._t0_us = face.timestamp_us
                self.task.start(0.0)
            t_s = (face.timestamp_us - self._t0_us) / 1e6
            nose = face.nose_shift_ipd
            self.task.update(t_s, face.gaze_offset_ipd, face.ear,
                             roll_deg=face.eye_roll_deg, ipd_px=face.ipd_px,
                             nose_shift=nose)

    # ── live metric ────────────────────────────────────────────────
    def get_live_metric(self, frame: HandFrame) -> float:
        return 0.0

    def get_face_metric(self, face: FacePose) -> float:
        """Blickversatz vom Zentrum in %IPD (Live-Plot)."""
        ox, oy = face.gaze_offset_ipd
        return math.hypot(ox, oy) * 100.0

    def get_live_metric_label(self) -> str:
        return "Blickversatz (%IPD)"

    # ── features ───────────────────────────────────────────────────
    def compute_features(self) -> dict[str, float]:
        feats = self.task.features()
        feats["n_face_frames"] = float(len(self.face_frames))
        if self.task.phase is Phase.FAILED:
            feats["_fail_reason"] = self.task.fail_reason
        elif len(self.task.hits) < self._min_hits:
            feats["_fail_reason"] = (
                f"Nur {len(self.task.hits)} Ziele erreicht (min. {self._min_hits})")
        return feats
