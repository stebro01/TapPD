"""Pure digital Trail Making Test logic — no UI, no Leap dependencies."""

import random
from dataclasses import dataclass, field


@dataclass
class TMTTarget:
    """A single target circle in the TMT."""
    index: int          # order in which it should be visited (0-based)
    label: str          # display label ("1", "A", etc.)
    x: float            # normalized screen position 0..1
    y: float            # normalized screen position 0..1
    visited: bool = False


@dataclass
class TMTSegmentResult:
    """Result for one segment (movement between consecutive targets)."""
    from_index: int
    to_index: int
    start_s: float          # time when previous target was completed
    movement_onset_s: float  # time when hand started moving
    arrival_s: float         # time when hand entered target zone
    dwell_complete_s: float  # time when dwell threshold met
    reaction_time_ms: float
    movement_time_ms: float
    path_length_mm: float
    straight_distance_mm: float
    peak_velocity_mm_s: float
    n_wrong_approaches: int  # how many wrong targets were approached


TARGET_ZONE_RADIUS = 0.06  # normalized distance for hit detection
MIN_SPACING = 0.15  # minimum distance between targets (normalized)


def _generate_positions(n: int) -> list[tuple[float, float]]:
    """Generate n well-spaced random positions within safe screen area."""
    margin = 0.15
    positions: list[tuple[float, float]] = []
    max_attempts = 500

    for _ in range(n):
        for attempt in range(max_attempts):
            x = margin + random.random() * (1.0 - 2 * margin)
            y = margin + random.random() * (1.0 - 2 * margin)
            # Check minimum distance to all existing positions
            ok = all(
                ((x - px) ** 2 + (y - py) ** 2) ** 0.5 >= MIN_SPACING
                for px, py in positions
            )
            if ok:
                positions.append((x, y))
                break
        else:
            # Fallback: place with reduced spacing
            x = margin + random.random() * (1.0 - 2 * margin)
            y = margin + random.random() * (1.0 - 2 * margin)
            positions.append((x, y))

    return positions


class TMTTaskState:
    """Manages the Trail Making Test state."""

    def __init__(self, part: str = "A", n_targets: int = 15) -> None:
        self.part = part  # "A" or "B"
        self.n_targets = n_targets
        self.targets = self._build_targets()
        self.segment_results: list[TMTSegmentResult] = []
        self.current_index = 0  # next target to visit
        self._start_time_s: float | None = None
        self._end_time_s: float | None = None
        self._completed = False
        self.wrong_approaches: list[tuple[float, int, int]] = []  # (time, approached_idx, expected_idx)

    def _build_targets(self) -> list[TMTTarget]:
        positions = _generate_positions(self.n_targets)
        labels = self._generate_labels()
        return [
            TMTTarget(index=i, label=labels[i], x=positions[i][0], y=positions[i][1])
            for i in range(self.n_targets)
        ]

    def _generate_labels(self) -> list[str]:
        if self.part == "A":
            return [str(i + 1) for i in range(self.n_targets)]
        else:
            # Part B: alternating numbers and letters: 1-A-2-B-3-C-...
            labels = []
            num = 1
            letter_idx = 0
            for i in range(self.n_targets):
                if i % 2 == 0:
                    labels.append(str(num))
                    num += 1
                else:
                    labels.append(chr(ord("A") + letter_idx))
                    letter_idx += 1
            return labels

    @property
    def current_target(self) -> TMTTarget | None:
        if self.current_index < len(self.targets):
            return self.targets[self.current_index]
        return None

    @property
    def next_label(self) -> str:
        t = self.current_target
        return t.label if t else ""

    def visit_target(self, index: int) -> bool:
        """Mark target as visited if it's the correct next target.
        Returns True if correct, False if wrong target."""
        if index == self.current_index:
            self.targets[index].visited = True
            self.current_index += 1
            return True
        return False

    def record_wrong_approach(self, time_s: float, approached_idx: int) -> None:
        self.wrong_approaches.append((time_s, approached_idx, self.current_index))

    def record_segment(self, result: TMTSegmentResult) -> None:
        self.segment_results.append(result)

    def is_complete(self) -> bool:
        return self.current_index >= len(self.targets)

    @property
    def total_time_s(self) -> float:
        if self._start_time_s is None or self._end_time_s is None:
            return 0.0
        return self._end_time_s - self._start_time_s

    @property
    def progress(self) -> float:
        return self.current_index / len(self.targets)
