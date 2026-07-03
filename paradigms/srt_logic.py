"""Pure Spatial Serial Reaction Time task logic — no UI, no Leap dependencies."""

import random
from dataclasses import dataclass, field


@dataclass
class SRTTrialResult:
    trial_index: int
    block_index: int
    block_type: str  # "random" | "sequence" | "practice"
    target_id: int  # 0-3
    stimulus_onset_s: float
    movement_onset_s: float
    arrival_s: float
    dwell_complete_s: float
    reaction_time_ms: float
    movement_time_ms: float
    total_response_time_ms: float
    path_length_mm: float
    straight_distance_mm: float
    peak_velocity_mm_s: float
    correct: bool
    sequence_position: int  # -1 for random/practice


@dataclass
class SRTBlockDef:
    block_index: int
    block_type: str  # "random" | "sequence" | "practice"
    n_trials: int
    targets: list[int] = field(default_factory=list)


# Target positions in normalized screen coords (0..1)
TARGET_POSITIONS = [
    (0.5, 0.15),   # top
    (0.85, 0.5),   # right
    (0.5, 0.85),   # bottom
    (0.15, 0.5),   # left
]

# Target positions in Leap coordinate space (mm)
TARGET_POSITIONS_MM = [
    (0.0, -80.0),    # top: center X, near Z
    (160.0, 0.0),    # right: +X, center Z
    (0.0, 80.0),     # bottom: center X, far Z
    (-160.0, 0.0),   # left: -X, center Z
]

TARGET_ZONE_RADIUS = 0.10  # normalized distance for hit detection


class SRTTaskState:
    """Manages the S-SRT block/trial structure and results."""

    def __init__(
        self,
        sequence: list[int] | None = None,
        n_sequence_blocks: int = 4,
        n_random_blocks: int = 5,
        trials_per_block: int = 20,
        practice_trials: int = 10,
    ) -> None:
        self.n_sequence_blocks = n_sequence_blocks
        self.n_random_blocks = n_random_blocks
        self.trials_per_block = trials_per_block
        self.practice_trials = practice_trials

        self.sequence = sequence or self.generate_sequence(10)
        self.blocks = self._build_blocks()
        self.trial_results: list[SRTTrialResult] = []

        self.current_block_idx = 0
        self.current_trial_in_block = 0
        self._global_trial_index = 0

    @staticmethod
    def generate_sequence(n: int = 10) -> list[int]:
        """Generate a random n-element sequence from [0,1,2,3] with no consecutive repeats."""
        seq = []
        for _ in range(n):
            choices = [t for t in range(4) if not seq or t != seq[-1]]
            seq.append(random.choice(choices))
        return seq

    @staticmethod
    def generate_random_targets(n: int) -> list[int]:
        """Generate n random targets with no consecutive repeats."""
        targets = []
        for _ in range(n):
            choices = [t for t in range(4) if not targets or t != targets[-1]]
            targets.append(random.choice(choices))
        return targets

    def _build_blocks(self) -> list[SRTBlockDef]:
        blocks: list[SRTBlockDef] = []
        idx = 0

        # Practice block
        blocks.append(SRTBlockDef(
            block_index=idx,
            block_type="practice",
            n_trials=self.practice_trials,
            targets=self.generate_random_targets(self.practice_trials),
        ))
        idx += 1

        # Alternating R/S blocks: R1, S1, R2, S2, ..., R_last
        for i in range(max(self.n_random_blocks, self.n_sequence_blocks)):
            if i < self.n_random_blocks:
                blocks.append(SRTBlockDef(
                    block_index=idx,
                    block_type="random",
                    n_trials=self.trials_per_block,
                    targets=self.generate_random_targets(self.trials_per_block),
                ))
                idx += 1
            if i < self.n_sequence_blocks:
                # Repeat the sequence to fill the block
                reps = self.trials_per_block // len(self.sequence)
                remainder = self.trials_per_block % len(self.sequence)
                targets = self.sequence * reps + self.sequence[:remainder]
                blocks.append(SRTBlockDef(
                    block_index=idx,
                    block_type="sequence",
                    n_trials=self.trials_per_block,
                    targets=targets,
                ))
                idx += 1

        return blocks

    @property
    def current_block(self) -> SRTBlockDef | None:
        if self.current_block_idx < len(self.blocks):
            return self.blocks[self.current_block_idx]
        return None

    def current_target(self) -> int | None:
        block = self.current_block
        if block and self.current_trial_in_block < block.n_trials:
            return block.targets[self.current_trial_in_block]
        return None

    def current_sequence_position(self) -> int:
        block = self.current_block
        if block and block.block_type == "sequence":
            return self.current_trial_in_block % len(self.sequence)
        return -1

    def advance_trial(self) -> bool:
        """Move to next trial. Returns False if task is complete."""
        self.current_trial_in_block += 1
        self._global_trial_index += 1
        block = self.current_block
        if block and self.current_trial_in_block >= block.n_trials:
            self.current_block_idx += 1
            self.current_trial_in_block = 0
        return not self.is_complete()

    def record_trial(self, result: SRTTrialResult) -> None:
        self.trial_results.append(result)

    def is_complete(self) -> bool:
        return self.current_block_idx >= len(self.blocks)

    def is_practice(self) -> bool:
        block = self.current_block
        return block is not None and block.block_type == "practice"

    @property
    def total_trials(self) -> int:
        return sum(b.n_trials for b in self.blocks)

    @property
    def completed_trials(self) -> int:
        return self._global_trial_index

    def block_label(self) -> str:
        block = self.current_block
        if not block:
            return ""
        if block.block_type == "practice":
            return "Übung"
        # Count only non-practice blocks
        non_practice = [b for b in self.blocks if b.block_type != "practice"]
        pos = next((i for i, b in enumerate(non_practice) if b.block_index == block.block_index), 0)
        return f"Block {pos + 1}/{len(non_practice)}"
