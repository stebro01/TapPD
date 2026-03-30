"""Pure Tower of Hanoi game logic — no UI, no Leap dependencies."""

from dataclasses import dataclass, field


@dataclass
class HanoiMove:
    from_peg: int
    to_peg: int
    disc: int  # disc size (1 = smallest)
    timestamp_s: float
    valid: bool


class HanoiGameState:
    """Tracks the 3-peg Tower of Hanoi state."""

    def __init__(self, n_discs: int = 3) -> None:
        self.n_discs = n_discs
        # Pegs: list index 0=left, 1=center, 2=right
        # Discs: integers where n_discs=largest, 1=smallest (bottom to top)
        self.pegs: list[list[int]] = [
            list(range(n_discs, 0, -1)),  # all discs on peg 0
            [],
            [],
        ]
        self.move_history: list[HanoiMove] = []

    @property
    def move_count(self) -> int:
        return sum(1 for m in self.move_history if m.valid)

    @property
    def error_count(self) -> int:
        return sum(1 for m in self.move_history if not m.valid)

    @staticmethod
    def optimal_moves(n_discs: int) -> int:
        return (1 << n_discs) - 1  # 2^n - 1

    def top_disc(self, peg: int) -> int | None:
        if 0 <= peg <= 2 and self.pegs[peg]:
            return self.pegs[peg][-1]
        return None

    def can_move(self, from_peg: int, to_peg: int) -> bool:
        if from_peg == to_peg:
            return False
        src = self.top_disc(from_peg)
        if src is None:
            return False
        dst = self.top_disc(to_peg)
        return dst is None or src < dst

    def move(self, from_peg: int, to_peg: int, timestamp_s: float = 0.0) -> bool:
        src = self.top_disc(from_peg)
        valid = self.can_move(from_peg, to_peg)
        disc = src if src is not None else 0
        self.move_history.append(HanoiMove(from_peg, to_peg, disc, timestamp_s, valid))
        if valid:
            self.pegs[to_peg].append(self.pegs[from_peg].pop())
        return valid

    def is_solved(self) -> bool:
        return len(self.pegs[2]) == self.n_discs

    def reset(self) -> None:
        self.__init__(self.n_discs)
