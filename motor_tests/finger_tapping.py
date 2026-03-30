"""Finger Tapping test (MDS-UPDRS 3.4): repeated thumb-index tapping."""

import math

from capture.base_capture import BaseCaptureDevice, HandFrame
from motor_tests.base_test import BaseMotorTest
from motor_tests.recorder import compute_features_from_config


class FingerTappingTest(BaseMotorTest):
    bilateral = False

    def __init__(self, capture: BaseCaptureDevice, duration: float = 10.0, hand: str = "right") -> None:
        super().__init__(capture, duration, hand)

    def test_type(self) -> str:
        return "finger_tapping"

    def get_instructions(self) -> str:
        side = "rechte" if self.hand == "right" else "linke"
        return (
            f"Finger Tapping – {side} Hand (MDS-UPDRS 3.4)\n\n"
            f"Tippen Sie mit Daumen und Zeigefinger der {side}n Hand "
            "so schnell und gleichmäßig wie möglich zusammen.\n\n"
            "Wichtig:\n"
            "- Hand flach / planar über dem Sensor halten\n"
            "- Finger bewegen sich senkrecht zum Sensor (auf/ab)\n"
            "- Restliche Finger möglichst still halten\n"
            "- Volle Amplitude: Finger weit öffnen, dann zusammen"
        )

    def get_live_metric(self, frame: HandFrame) -> float:
        if len(frame.fingers) >= 2:
            t = frame.fingers[0].tip_position
            i = frame.fingers[1].tip_position
            return math.sqrt(sum((a - b) ** 2 for a, b in zip(t, i)))
        return 0.0

    def get_live_metric_label(self) -> str:
        return "Daumen-Zeigefinger Distanz (mm)"

    def compute_features(self) -> dict[str, float]:
        return compute_features_from_config(
            "finger_tapping", self.get_frames(), self.capture.sample_rate
        )
