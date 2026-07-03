"""Rest Tremor test (MDS-UPDRS 3.17): both hands relaxed, bilateral analysis."""

import math

from capture.base_capture import BaseCaptureDevice, HandFrame
from paradigms.base_test import BaseParadigm
from paradigms.config import get_test_config
from paradigms.recorder import compute_features_from_config


class RestTremorTest(BaseParadigm):
    bilateral = True

    def __init__(self, capture: BaseCaptureDevice, duration: float = 10.0, hand: str = "both") -> None:
        super().__init__(capture, duration, hand)
        # Same baseline the feature computation uses (test_config.yaml capture.base_y).
        self._base_y = float(get_test_config(self.test_type()).get("capture", {}).get("base_y", 150.0))

    def test_type(self) -> str:
        return "rest_tremor"

    def get_instructions(self) -> str:
        return (
            "Ruhetremor – beide Hände (MDS-UPDRS 3.17)\n\n"
            "Legen Sie beide Hände entspannt auf die Oberschenkel, "
            "Handflächen nach unten. "
            "Halten Sie die Hände über dem Sensor.\n\n"
            "Wichtig:\n"
            "- Beide Hände nebeneinander über dem Sensor\n"
            "- Hände komplett entspannt, nicht anspannen\n"
            "- Arme locker, Schultern entspannt\n"
            "- Nicht versuchen, den Tremor zu unterdrücken"
        )

    def get_live_metric(self, frame: HandFrame) -> float:
        px, py, pz = frame.palm_position
        return math.sqrt(px**2 + (py - self._base_y) ** 2 + pz**2)

    def get_live_metric_label(self) -> str:
        return "Handposition Abweichung (mm)"

    def compute_features(self) -> dict[str, float]:
        return compute_features_from_config(
            "rest_tremor",
            self.get_frames(),
            self.capture.sample_rate,
            left_frames=self.get_frames("left"),
            right_frames=self.get_frames("right"),
        )
