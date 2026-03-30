"""Digital Trail Making Test (dTMT) cognitive-motor test."""

import math

import numpy as np

from capture.base_capture import BaseCaptureDevice, HandFrame
from motor_tests.base_test import BaseMotorTest
from motor_tests.tmt_logic import TMTTaskState


class TrailMakingTest(BaseMotorTest):
    """Digital Trail Making Test with contactless hand pointing."""

    bilateral = False

    def __init__(
        self,
        capture: BaseCaptureDevice,
        duration: float = 9999.0,
        hand: str = "right",
        part: str = "A",
        n_targets: int = 15,
    ) -> None:
        super().__init__(capture, duration=duration, hand=hand)
        self.part = part
        self.n_targets = n_targets
        self.task = TMTTaskState(part=part, n_targets=n_targets)
        self._start_time_s: float | None = None
        self._end_time_s: float | None = None
        self._completed = False

    def test_type(self) -> str:
        return f"trail_making_{self.part.lower()}"

    def get_instructions(self) -> str:
        if self.part == "A":
            return (
                "Trail Making Test — Teil A\n\n"
                "Verbinden Sie die Zahlen in aufsteigender Reihenfolge:\n"
                "1 → 2 → 3 → ...\n\n"
                "Bewegen Sie Ihre Hand zum naechsten Ziel."
            )
        return (
            "Trail Making Test — Teil B\n\n"
            "Verbinden Sie abwechselnd Zahlen und Buchstaben:\n"
            "1 → A → 2 → B → 3 → C → ...\n\n"
            "Bewegen Sie Ihre Hand zum naechsten Ziel."
        )

    def get_live_metric(self, frame: HandFrame) -> float:
        v = frame.palm_velocity
        return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)

    def get_live_metric_label(self) -> str:
        return "Geschwindigkeit (mm/s)"

    def mark_completed(self, end_time_s: float) -> None:
        self._completed = True
        self._end_time_s = end_time_s
        self.task._end_time_s = end_time_s

    def mark_aborted(self, end_time_s: float) -> None:
        self._completed = False
        self._end_time_s = end_time_s
        self.task._end_time_s = end_time_s

    def compute_features(self) -> dict[str, float]:
        segments = self.task.segment_results
        if not segments:
            return self._empty_features()

        total_time = self.task.total_time_s

        # Reaction times (time before movement starts)
        react_times = [s.reaction_time_ms for s in segments if s.reaction_time_ms > 0]
        mean_react = float(np.mean(react_times)) if react_times else 0.0

        # Movement times
        move_times = [s.movement_time_ms for s in segments if s.movement_time_ms > 0]
        mean_move = float(np.mean(move_times)) if move_times else 0.0
        move_cv = float(np.std(move_times) / np.mean(move_times)) if move_times and np.mean(move_times) > 0 else 0.0

        # Path efficiency
        efficiencies = []
        for s in segments:
            if s.path_length_mm > 0:
                efficiencies.append(s.straight_distance_mm / s.path_length_mm)
        mean_eff = float(np.mean(efficiencies)) if efficiencies else 0.0

        # Peak velocity
        velocities = [s.peak_velocity_mm_s for s in segments]
        mean_vel = float(np.mean(velocities)) if velocities else 0.0

        # Errors
        n_errors = sum(s.n_wrong_approaches for s in segments)
        total_wrong = len(self.task.wrong_approaches)

        # Dwell times
        dwells = [(s.dwell_complete_s - s.arrival_s) * 1000 for s in segments if s.arrival_s > 0]
        mean_dwell = float(np.mean(dwells)) if dwells else 0.0

        # Fatigue: compare first third vs last third of segments
        fatigue = 0.0
        if len(move_times) >= 6:
            third = len(move_times) // 3
            first_third = float(np.mean(move_times[:third]))
            last_third = float(np.mean(move_times[-third:]))
            if first_third > 0:
                fatigue = (last_third - first_third) / first_third

        completed_targets = self.task.current_index

        return {
            "tmt_part": 1.0 if self.part == "A" else 2.0,
            "completed": 1.0 if self._completed else 0.0,
            "total_time_s": round(total_time, 1),
            "n_targets_completed": float(completed_targets),
            "n_targets_total": float(self.n_targets),
            "mean_reaction_time_ms": round(mean_react, 1),
            "mean_movement_time_ms": round(mean_move, 1),
            "movement_time_cv": round(move_cv, 3),
            "path_efficiency": round(mean_eff, 3),
            "mean_peak_velocity_mm_s": round(mean_vel, 1),
            "n_errors": float(n_errors),
            "error_rate_per_target": round(total_wrong / max(1, completed_targets), 2),
            "mean_dwell_time_ms": round(mean_dwell, 1),
            "fatigue_index": round(fatigue, 3),
        }

    def _empty_features(self) -> dict[str, float]:
        keys = [
            "tmt_part", "completed", "total_time_s", "n_targets_completed",
            "n_targets_total", "mean_reaction_time_ms", "mean_movement_time_ms",
            "movement_time_cv", "path_efficiency", "mean_peak_velocity_mm_s",
            "n_errors", "error_rate_per_target", "mean_dwell_time_ms",
            "fatigue_index",
        ]
        return {k: 0.0 for k in keys}
