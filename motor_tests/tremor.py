"""Postural Tremor test (MDS-UPDRS 3.15): both hands extended, bilateral analysis."""

import math

from capture.base_capture import BaseCaptureDevice, HandFrame
from motor_tests.base_test import BaseMotorTest
from motor_tests.recorder import compute_features_from_config


class PosturalTremorTest(BaseMotorTest):
    bilateral = True

    def __init__(self, capture: BaseCaptureDevice, duration: float = 10.0, hand: str = "both") -> None:
        super().__init__(capture, duration, hand)

    def test_type(self) -> str:
        return "postural_tremor"

    def get_instructions(self) -> str:
        return (
            "Posturaler Tremor – beide Hände (MDS-UPDRS 3.15)\n\n"
            "Strecken Sie beide Hände vor sich aus, "
            "Handflächen nach unten, Finger gespreizt.\n\n"
            "Wichtig:\n"
            "- Beide Hände über dem Sensor\n"
            "- Arme frei (nicht auf Lehne abstützen)\n"
            "- Hände so still wie möglich halten\n"
            "- Finger nicht verkrampfen, natürliche Haltung"
        )

    def get_live_metric(self, frame: HandFrame) -> float:
        """Palm displacement magnitude."""
        px, py, pz = frame.palm_position
        base_y = 200.0
        return math.sqrt(px**2 + (py - base_y) ** 2 + pz**2)

    def get_live_metric_label(self) -> str:
        return "Handposition Abweichung (mm)"

    def compute_features(self) -> dict[str, float]:
        return compute_features_from_config(
            "postural_tremor",
            self.get_frames(),
            self.capture.sample_rate,
            left_frames=self.get_frames("left"),
            right_frames=self.get_frames("right"),
        )
