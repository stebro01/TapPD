"""Pronation/Supination test (MDS-UPDRS 3.6): forearm rotation."""

import math

from capture.base_capture import BaseCaptureDevice, HandFrame
from motor_tests.base_test import BaseMotorTest
from motor_tests.recorder import compute_features_from_config


class PronationSupinationTest(BaseMotorTest):
    bilateral = False

    def __init__(self, capture: BaseCaptureDevice, duration: float = 10.0, hand: str = "right") -> None:
        super().__init__(capture, duration, hand)

    def test_type(self) -> str:
        return "pronation_supination"

    def get_instructions(self) -> str:
        side = "rechte" if self.hand == "right" else "linke"
        return (
            f"Pronation/Supination – {side} Hand (MDS-UPDRS 3.6)\n\n"
            f"Drehen Sie den {side}n Unterarm so schnell und "
            "vollständig wie möglich hin und her.\n\n"
            "Wichtig:\n"
            "- Hand über dem Sensor, Unterarm waagerecht\n"
            "- Handfläche dreht abwechselnd nach oben und unten\n"
            "- Wie 'Glühbirne einschrauben'\n"
            "- Volle Drehbewegung, gleichmäßiger Rhythmus"
        )

    def get_live_metric(self, frame: HandFrame) -> float:
        nx, ny = frame.palm_normal[0], frame.palm_normal[1]
        return math.degrees(math.atan2(nx, -ny))

    def get_live_metric_label(self) -> str:
        return "Rotationswinkel (°)"

    def compute_features(self) -> dict[str, float]:
        return compute_features_from_config(
            "pronation_supination", self.get_frames(), self.capture.sample_rate
        )
