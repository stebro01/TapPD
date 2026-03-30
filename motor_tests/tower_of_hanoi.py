"""Tower of Hanoi cognitive-motor test."""

import math
import numpy as np

from capture.base_capture import BaseCaptureDevice, HandFrame
from motor_tests.base_test import BaseMotorTest
from motor_tests.hanoi_logic import HanoiGameState


class TowerOfHanoiTest(BaseMotorTest):
    """Interactive Tower of Hanoi task with pinch-based single-hand disc manipulation."""

    bilateral = False

    def __init__(
        self,
        capture: BaseCaptureDevice,
        duration: float = 9999.0,  # no hard time limit
        hand: str = "right",
        n_discs: int = 3,
    ) -> None:
        super().__init__(capture, duration=duration, hand=hand)
        self.n_discs = n_discs
        self.game = HanoiGameState(n_discs)
        self._start_time_s: float | None = None
        self._end_time_s: float | None = None
        self._completed = False

    def test_type(self) -> str:
        return "tower_of_hanoi"

    def get_instructions(self) -> str:
        return (
            "Tuerme von Hanoi\n\n"
            "Bewegen Sie alle Scheiben vom linken Stab zum rechten Stab.\n"
            "Regeln:\n"
            "- Nur eine Scheibe gleichzeitig bewegen\n"
            "- Nie eine groessere auf eine kleinere Scheibe legen\n\n"
            "Pinzettengriff (Daumen + Zeigefinger) zum Greifen und Loslassen."
        )

    def get_live_metric(self, frame: HandFrame) -> float:
        return frame.pinch_distance

    def get_live_metric_label(self) -> str:
        return "Pinch-Distanz (mm)"

    def mark_completed(self, end_time_s: float) -> None:
        self._completed = True
        self._end_time_s = end_time_s

    def mark_aborted(self, end_time_s: float) -> None:
        self._completed = False
        self._end_time_s = end_time_s

    def compute_features(self) -> dict[str, float]:
        history = self.game.move_history
        valid_moves = [m for m in history if m.valid]

        total_time = 0.0
        if self._start_time_s is not None and self._end_time_s is not None:
            total_time = self._end_time_s - self._start_time_s

        # Planning time: time before first move
        planning_time = 0.0
        if valid_moves and self._start_time_s is not None:
            planning_time = valid_moves[0].timestamp_s - self._start_time_s

        # Inter-move intervals
        intervals = []
        for i in range(1, len(valid_moves)):
            intervals.append(valid_moves[i].timestamp_s - valid_moves[i - 1].timestamp_s)

        mean_move_time = float(np.mean(intervals)) if intervals else 0.0
        move_time_cv = float(np.std(intervals) / np.mean(intervals)) if intervals and np.mean(intervals) > 0 else 0.0

        optimal = HanoiGameState.optimal_moves(self.n_discs)
        n_valid = self.game.move_count
        efficiency = optimal / n_valid if n_valid > 0 else 0.0

        # Pinch metrics
        pinch_stats = self._compute_pinch_metrics()

        # Hand tremor (high-freq palm jitter during task)
        all_frames = self.right_frames + self.left_frames
        hand_jitter = self._compute_hand_tremor(all_frames)

        # Trajectory smoothness (mean path length per move)
        mean_traj, traj_efficiency = self._compute_trajectory_metrics(valid_moves)

        features = {
            "completed": 1.0 if self._completed else 0.0,
            "total_time_s": round(total_time, 2),
            "n_moves": float(n_valid),
            "optimal_moves": float(optimal),
            "move_efficiency": round(efficiency, 3),
            "planning_time_s": round(planning_time, 2),
            "mean_move_time_s": round(mean_move_time, 2),
            "move_time_cv": round(move_time_cv, 3),
            "mean_pinch_duration_s": round(pinch_stats["mean_pinch_s"], 2),
            "mean_pinch_depth_mm": round(pinch_stats["mean_pinch_depth"], 1),
            "pinch_accuracy": round(pinch_stats["pinch_accuracy"], 3),
            "mean_trajectory_mm": round(mean_traj, 1),
            "trajectory_efficiency": round(traj_efficiency, 3),
            "hand_jitter_mm": round(hand_jitter, 2),
        }
        return features

    def _compute_pinch_metrics(self) -> dict[str, float]:
        """Analyze pinch grip quality from frame data."""
        all_frames = sorted(
            self.left_frames + self.right_frames,
            key=lambda f: f.timestamp_us,
        )
        if not all_frames:
            return {"mean_pinch_s": 0, "mean_pinch_depth": 0, "pinch_accuracy": 0}

        # Find pinch episodes (continuous periods where pinch_distance < 30mm)
        PINCH_THRESH = 30.0
        pinch_durations = []
        pinch_depths = []
        in_pinch = False
        pinch_start_us = 0
        episode_depths: list[float] = []

        for f in all_frames:
            if f.pinch_distance < PINCH_THRESH:
                if not in_pinch:
                    in_pinch = True
                    pinch_start_us = f.timestamp_us
                    episode_depths = []
                episode_depths.append(f.pinch_distance)
            else:
                if in_pinch:
                    dur = (f.timestamp_us - pinch_start_us) / 1e6
                    if dur > 0.1:  # ignore very brief pinches
                        pinch_durations.append(dur)
                        pinch_depths.append(float(np.mean(episode_depths)))
                    in_pinch = False

        mean_pinch_s = float(np.mean(pinch_durations)) if pinch_durations else 0.0
        mean_pinch_depth = float(np.mean(pinch_depths)) if pinch_depths else 0.0

        # Pinch accuracy: ratio of successful grabs to total pinch episodes
        n_valid_moves = self.game.move_count
        n_episodes = len(pinch_durations)
        accuracy = n_valid_moves / n_episodes if n_episodes > 0 else 0.0

        return {
            "mean_pinch_s": mean_pinch_s,
            "mean_pinch_depth": mean_pinch_depth,
            "pinch_accuracy": min(1.0, accuracy),
        }

    def _compute_hand_tremor(self, frames: list[HandFrame]) -> float:
        """Compute high-frequency jitter (mm) as proxy for tremor during task."""
        if len(frames) < 20:
            return 0.0

        # Compute frame-to-frame palm displacement
        displacements = []
        for i in range(1, len(frames)):
            dx = frames[i].palm_position[0] - frames[i - 1].palm_position[0]
            dy = frames[i].palm_position[1] - frames[i - 1].palm_position[1]
            dz = frames[i].palm_position[2] - frames[i - 1].palm_position[2]
            displacements.append(math.sqrt(dx * dx + dy * dy + dz * dz))

        # Use median absolute deviation (robust to large intentional movements)
        arr = np.array(displacements)
        median = float(np.median(arr))
        mad = float(np.median(np.abs(arr - median)))
        return mad

    def _compute_trajectory_metrics(self, valid_moves) -> tuple[float, float]:
        """Compute mean trajectory length and trajectory efficiency per move."""
        if len(valid_moves) < 1:
            return 0.0, 0.0

        all_frames = sorted(
            self.left_frames + self.right_frames,
            key=lambda f: f.timestamp_us,
        )
        if len(all_frames) < 2:
            return 0.0, 0.0

        # Build time-indexed frame list
        t0_us = all_frames[0].timestamp_us
        trajectories = []
        efficiencies = []

        for move in valid_moves:
            # Frames around this move (±2s window before the move timestamp)
            move_t_us = int(move.timestamp_s * 1e6)
            move_frames = [
                f for f in all_frames
                if (move_t_us - 3_000_000) < (f.timestamp_us - t0_us) < move_t_us
            ]
            if len(move_frames) < 2:
                continue

            # Total path length
            path_len = 0.0
            for j in range(1, len(move_frames)):
                dx = move_frames[j].palm_position[0] - move_frames[j - 1].palm_position[0]
                dz = move_frames[j].palm_position[2] - move_frames[j - 1].palm_position[2]
                path_len += math.sqrt(dx * dx + dz * dz)

            # Straight-line distance (start to end)
            dx = move_frames[-1].palm_position[0] - move_frames[0].palm_position[0]
            dz = move_frames[-1].palm_position[2] - move_frames[0].palm_position[2]
            straight = math.sqrt(dx * dx + dz * dz)

            trajectories.append(path_len)
            if path_len > 0:
                efficiencies.append(straight / path_len)

        mean_traj = float(np.mean(trajectories)) if trajectories else 0.0
        traj_eff = float(np.mean(efficiencies)) if efficiencies else 0.0
        return mean_traj, traj_eff
