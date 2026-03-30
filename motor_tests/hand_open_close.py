"""Hand Opening/Closing test (MDS-UPDRS 3.5): repeated hand open/close cycles."""

from capture.base_capture import BaseCaptureDevice, HandFrame
from motor_tests.base_test import BaseMotorTest
from motor_tests.recorder import compute_features_from_config, extract_metric


class HandOpenCloseTest(BaseMotorTest):
    bilateral = False

    def __init__(self, capture: BaseCaptureDevice, duration: float = 10.0, hand: str = "right") -> None:
        super().__init__(capture, duration, hand)

    def test_type(self) -> str:
        return "hand_open_close"

    def get_instructions(self) -> str:
        side = "rechte" if self.hand == "right" else "linke"
        return (
            f"Hand Oeffnen/Schliessen – {side} Hand (MDS-UPDRS 3.5)\n\n"
            f"Oeffnen und schliessen Sie die {side} Hand "
            "so schnell und vollstaendig wie moeglich.\n\n"
            "Wichtig:\n"
            "- Hand flach ueber dem Sensor, Handflaeche nach unten\n"
            "- Finger beim Oeffnen weit spreizen\n"
            "- Beim Schliessen Faust machen\n"
            "- Gleichmaessiger Rhythmus"
        )

    def get_live_metric(self, frame: HandFrame) -> float:
        return extract_metric(frame, "mean_finger_spread")

    def get_live_metric_label(self) -> str:
        return "Fingerspreizung (mm)"

    def compute_features(self) -> dict[str, float]:
        return compute_features_from_config(
            "hand_open_close", self.get_frames(), self.capture.sample_rate
        )
