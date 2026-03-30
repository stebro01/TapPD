"""Spatial Serial Reaction Time (S-SRT) cognitive-motor test."""

import math

import numpy as np

from capture.base_capture import BaseCaptureDevice, HandFrame
from motor_tests.base_test import BaseMotorTest
from motor_tests.srt_logic import SRTTaskState


class SpatialSRTTest(BaseMotorTest):
    """Spatial reaching SRT with embedded sequence learning."""

    bilateral = False

    def __init__(
        self,
        capture: BaseCaptureDevice,
        duration: float = 9999.0,
        hand: str = "right",
        n_sequence_blocks: int = 4,
        n_random_blocks: int = 5,
        trials_per_block: int = 20,
    ) -> None:
        super().__init__(capture, duration=duration, hand=hand)
        self.task = SRTTaskState(
            n_sequence_blocks=n_sequence_blocks,
            n_random_blocks=n_random_blocks,
            trials_per_block=trials_per_block,
        )
        self._start_time_s: float | None = None
        self._end_time_s: float | None = None
        self._completed = False

    def test_type(self) -> str:
        return "spatial_srt"

    def get_instructions(self) -> str:
        return (
            "Raeumliche Reaktionszeit (S-SRT)\n\n"
            "Vier Ziele erscheinen auf dem Bildschirm.\n"
            "Bewegen Sie Ihre Hand zum leuchtenden Ziel\n"
            "und halten Sie kurz am Ziel.\n\n"
            "Es folgt zuerst eine kurze Uebungsphase."
        )

    def get_live_metric(self, frame: HandFrame) -> float:
        v = frame.palm_velocity
        return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)

    def get_live_metric_label(self) -> str:
        return "Geschwindigkeit (mm/s)"

    def mark_completed(self, end_time_s: float) -> None:
        self._completed = True
        self._end_time_s = end_time_s

    def mark_aborted(self, end_time_s: float) -> None:
        self._completed = False
        self._end_time_s = end_time_s

    def compute_features(self) -> dict[str, float]:
        results = [r for r in self.task.trial_results if r.block_type != "practice"]
        if not results:
            return self._empty_features()

        seq_results = [r for r in results if r.block_type == "sequence"]
        rnd_results = [r for r in results if r.block_type == "random"]

        rts = [r.total_response_time_ms for r in results if r.correct]
        seq_rts = [r.total_response_time_ms for r in seq_results if r.correct]
        rnd_rts = [r.total_response_time_ms for r in rnd_results if r.correct]

        mean_rt = float(np.mean(rts)) if rts else 0.0
        mean_seq_rt = float(np.mean(seq_rts)) if seq_rts else 0.0
        mean_rnd_rt = float(np.mean(rnd_rts)) if rnd_rts else 0.0

        # Learning index
        learning_idx = 0.0
        if mean_rnd_rt > 0:
            learning_idx = (mean_rnd_rt - mean_seq_rt) / mean_rnd_rt

        # Reaction time (stimulus to movement onset)
        react_times = [r.reaction_time_ms for r in results if r.correct and r.reaction_time_ms > 0]
        mean_react = float(np.mean(react_times)) if react_times else 0.0

        # Movement time (onset to arrival)
        move_times = [r.movement_time_ms for r in results if r.correct and r.movement_time_ms > 0]
        mean_move = float(np.mean(move_times)) if move_times else 0.0

        # Sequence RT slope (learning curve across sequence blocks)
        seq_rt_slope = self._compute_seq_slope(seq_results)

        # Path efficiency
        efficiencies = []
        for r in results:
            if r.correct and r.path_length_mm > 0:
                efficiencies.append(r.straight_distance_mm / r.path_length_mm)
        mean_eff = float(np.mean(efficiencies)) if efficiencies else 0.0

        # Peak velocity
        velocities = [r.peak_velocity_mm_s for r in results if r.correct]
        mean_vel = float(np.mean(velocities)) if velocities else 0.0
        vel_cv = float(np.std(velocities) / np.mean(velocities)) if velocities and np.mean(velocities) > 0 else 0.0

        # Error rate
        n_correct = sum(1 for r in results if r.correct)
        error_rate = 1.0 - (n_correct / len(results)) if results else 0.0

        # Fatigue index (first block vs last block)
        fatigue = self._compute_fatigue(results)

        # Dwell time
        dwells = [(r.dwell_complete_s - r.arrival_s) * 1000 for r in results if r.correct and r.arrival_s > 0]
        mean_dwell = float(np.mean(dwells)) if dwells else 0.0

        total_time = 0.0
        if self._start_time_s and self._end_time_s:
            total_time = self._end_time_s - self._start_time_s

        return {
            "total_time_s": round(total_time, 1),
            "reaction_time_ms": round(mean_react, 1),
            "movement_time_ms": round(mean_move, 1),
            "total_response_time_ms": round(mean_rt, 1),
            "learning_index": round(learning_idx, 3),
            "rt_sequence_mean_ms": round(mean_seq_rt, 1),
            "rt_random_mean_ms": round(mean_rnd_rt, 1),
            "sequence_rt_slope": round(seq_rt_slope, 2),
            "path_efficiency": round(mean_eff, 3),
            "peak_velocity_mm_s": round(mean_vel, 1),
            "velocity_variability_cv": round(vel_cv, 3),
            "error_rate": round(error_rate, 3),
            "fatigue_index": round(fatigue, 3),
            "dwell_time_ms": round(mean_dwell, 1),
            "n_trials": float(len(results)),
            "n_sequence_trials": float(len(seq_results)),
            "n_random_trials": float(len(rnd_results)),
        }

    def _empty_features(self) -> dict[str, float]:
        keys = [
            "total_time_s", "reaction_time_ms", "movement_time_ms",
            "total_response_time_ms", "learning_index", "rt_sequence_mean_ms",
            "rt_random_mean_ms", "sequence_rt_slope", "path_efficiency",
            "peak_velocity_mm_s", "velocity_variability_cv", "error_rate",
            "fatigue_index", "dwell_time_ms", "n_trials",
            "n_sequence_trials", "n_random_trials",
        ]
        return {k: 0.0 for k in keys}

    def _compute_seq_slope(self, seq_results: list) -> float:
        """Linear regression slope of mean RT across sequence blocks."""
        # Group by block_index
        block_rts: dict[int, list[float]] = {}
        for r in seq_results:
            if r.correct:
                block_rts.setdefault(r.block_index, []).append(r.total_response_time_ms)
        if len(block_rts) < 2:
            return 0.0
        sorted_blocks = sorted(block_rts.keys())
        x = np.arange(len(sorted_blocks), dtype=float)
        y = np.array([np.mean(block_rts[b]) for b in sorted_blocks])
        slope = float(np.polyfit(x, y, 1)[0])
        return slope

    def _compute_fatigue(self, results: list) -> float:
        """RT increase from first to last non-practice block."""
        block_rts: dict[int, list[float]] = {}
        for r in results:
            if r.correct:
                block_rts.setdefault(r.block_index, []).append(r.total_response_time_ms)
        if len(block_rts) < 2:
            return 0.0
        sorted_blocks = sorted(block_rts.keys())
        first_rt = float(np.mean(block_rts[sorted_blocks[0]]))
        last_rt = float(np.mean(block_rts[sorted_blocks[-1]]))
        if first_rt > 0:
            return (last_rt - first_rt) / first_rt
        return 0.0
