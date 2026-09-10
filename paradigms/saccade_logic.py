"""Pure saccade-task logic — no Qt, no capture dependencies.

Zonen-Klassifikation statt Gaze-Tracking: 5 Bildschirmpunkte (Ecken + Mitte)
werden pro Durchlauf geeicht (Median-Blickversatz je Punkt), danach läuft eine
gaze-contingente Zufallsfolge: sobald der klassifizierte Blick für ``dwell_s``
stabil auf dem Zielpunkt liegt, zählt ein Treffer und der nächste Punkt
erscheint. Ein Kopfpose-Wächter (Roll / IPD-Änderung / Nasen-Versatz relativ
zur Eichung) markiert Abschnitte mit bewegtem Kopf als ungültig.

Der Zustandsautomat konsumiert nur (t_s, gaze_offset_ipd, ear, roll, ipd,
nose_shift) — headless testbar; die FacePose-Anbindung liegt im Paradigma.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum, auto

# Normalized screen positions (x, y in 0..1) of the five targets.
POINTS: dict[str, tuple[float, float]] = {
    "LO": (0.08, 0.10),   # links oben
    "RO": (0.92, 0.10),   # rechts oben
    "LU": (0.08, 0.90),   # links unten
    "RU": (0.92, 0.90),   # rechts unten
    "MI": (0.50, 0.50),   # Mitte
}
POINT_ORDER = ["LO", "RO", "MI", "LU", "RU"]   # calibration sequence

# Target layouts. The webcam gaze feature (iris vs. eye-corner midpoint)
# resolves horizontal shifts well (3–5 px at 60 cm) but vertical ones hardly
# at all (~1 px: small movement, lid occlusion) — so the clinical default is
# the horizontal layout; "vertical" is prepared for a lid-based feature.
LAYOUTS: dict[str, tuple[dict[str, tuple[float, float]], list[str]]] = {
    "five_point": (POINTS, POINT_ORDER),
    "horizontal": ({"L": (0.08, 0.50), "M": (0.50, 0.50), "R": (0.92, 0.50)}, ["L", "R", "M"]),
    "vertical": ({"O": (0.50, 0.10), "M": (0.50, 0.50), "U": (0.50, 0.90)}, ["O", "U", "M"]),
}
SEQUENCES = ("random", "alternate")   # alternate: outer points in fixed turn (L, R, L, R …)


class Phase(Enum):
    IDLE = auto()
    CALIBRATING = auto()
    TESTING = auto()
    DONE = auto()
    FAILED = auto()        # calibration invalid


@dataclass
class Hit:
    target: str
    shown_at_s: float
    acquired_at_s: float
    first_move_correct: bool

    @property
    def latency_ms(self) -> float:
        return (self.acquired_at_s - self.shown_at_s) * 1000.0


@dataclass
class SaccadeTask:
    # config (mirrors test_config.yaml saccade_test)
    calib_per_point_s: float = 2.0
    calib_settle_s: float = 0.6
    calib_min_samples: int = 12
    calib_max_spread: float = 0.06
    duration_s: float = 30.0
    dwell_s: float = 0.2
    confidence_margin: float = 1.3
    min_separation: float = 0.04
    prefer_far_targets: bool = True
    blink_ear: float = 0.18
    guard_max_roll_deg: float = 8.0
    guard_max_ipd_change: float = 0.12
    guard_max_nose_shift: float = 0.15
    rng_seed: int | None = None
    layout: str = "five_point"
    sequence: str = "random"

    # state
    phase: Phase = Phase.IDLE
    calib_index: int = 0                     # index into POINT_ORDER
    references: dict[str, tuple[float, float]] = field(default_factory=dict)
    current_target: str | None = None
    hits: list[Hit] = field(default_factory=list)
    direction_errors: int = 0
    fail_reason: str = ""

    def __post_init__(self) -> None:
        if self.layout not in LAYOUTS:
            raise ValueError(f"Unbekanntes Sakkaden-Layout '{self.layout}' "
                             f"(erlaubt: {', '.join(LAYOUTS)})")
        if self.sequence not in SEQUENCES:
            raise ValueError(f"Unbekannte Zielfolge '{self.sequence}' (erlaubt: random, alternate)")
        self.points, self.calib_order = LAYOUTS[self.layout]
        self.points = dict(self.points)
        self.calib_order = list(self.calib_order)
        self._outer = [p for p in self.calib_order if p not in ("M", "MI")]
        self._seq_i = 0
        self._rng = random.Random(self.rng_seed)
        self._calib_samples: dict[str, list[tuple[float, float]]] = {}
        self._calib_point_started: float | None = None
        self._test_started: float | None = None
        self._target_shown_at: float | None = None
        self._dwell_started: float | None = None
        self._first_move_evaluated = False
        self._first_move_correct = True
        self._prev_offset: tuple[float, float] | None = None
        self._baseline_roll: float | None = None
        self._baseline_ipd: float | None = None
        self._baseline_nose: float | None = None
        self._roll_sum = 0.0
        self._ipd_sum = 0.0
        self._nose_sum = 0.0
        self._nose_n = 0
        self._baseline_n = 0
        self.samples_total = 0
        self.samples_blink = 0
        self.samples_head_invalid = 0

    # ── lifecycle ─────────────────────────────────────────────────
    def start(self, t_s: float) -> None:
        self.phase = Phase.CALIBRATING
        self.calib_index = 0
        self._calib_point_started = t_s

    @property
    def calib_point(self) -> str | None:
        if self.phase is Phase.CALIBRATING and self.calib_index < len(self.calib_order):
            return self.calib_order[self.calib_index]
        return None

    # ── per-frame update ──────────────────────────────────────────
    def update(self, t_s: float, offset: tuple[float, float], ear: float,
               roll_deg: float = 0.0, ipd_px: float = 0.0,
               nose_shift: float | None = None) -> None:
        """Feed one face sample (offset = gaze_offset_ipd)."""
        if self.phase not in (Phase.CALIBRATING, Phase.TESTING):
            return
        self.samples_total += 1
        if ear < self.blink_ear:
            self.samples_blink += 1
            self._dwell_started = None    # blink interrupts a dwell
            return

        if self.phase is Phase.CALIBRATING:
            self._update_calibration(t_s, offset, roll_deg, ipd_px, nose_shift)
        else:
            self._update_test(t_s, offset, roll_deg, ipd_px, nose_shift)

    # ── calibration ───────────────────────────────────────────────
    def _update_calibration(self, t_s, offset, roll_deg, ipd_px, nose_shift) -> None:
        point = self.calib_point
        if point is None or self._calib_point_started is None:
            return
        elapsed = t_s - self._calib_point_started
        if elapsed >= self.calib_settle_s:
            self._calib_samples.setdefault(point, []).append(offset)
            # head-pose baseline accumulates over ALL calibration samples
            self._roll_sum += roll_deg
            self._ipd_sum += ipd_px
            if nose_shift is not None:
                self._nose_sum += nose_shift
                self._nose_n += 1
            self._baseline_n += 1
        if elapsed >= self.calib_per_point_s:
            self.calib_index += 1
            self._calib_point_started = t_s
            if self.calib_index >= len(self.calib_order):
                self._finish_calibration(t_s)

    def _finish_calibration(self, t_s: float) -> None:
        for point in self.calib_order:
            samples = self._calib_samples.get(point, [])
            if len(samples) < self.calib_min_samples:
                self._fail(f"Eichpunkt {point}: zu wenige Samples ({len(samples)})")
                return
            xs = sorted(s[0] for s in samples)
            ys = sorted(s[1] for s in samples)
            med = (xs[len(xs) // 2], ys[len(ys) // 2])
            spread = max(math.hypot(s[0] - med[0], s[1] - med[1]) for s in samples)
            if spread > self.calib_max_spread:
                self._fail(f"Eichpunkt {point}: Blick zu unruhig ({spread:.3f} IPD)")
                return
            self.references[point] = med
        # references must be separable
        pts = list(self.references.items())
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                d = math.hypot(pts[i][1][0] - pts[j][1][0],
                               pts[i][1][1] - pts[j][1][1])
                if d < self.min_separation:
                    self._fail(f"Eichpunkte {pts[i][0]}/{pts[j][0]} nicht trennbar "
                               f"({d:.3f} IPD) — näher an den Bildschirm?")
                    return
        if self._baseline_n:
            self._baseline_roll = self._roll_sum / self._baseline_n
            self._baseline_ipd = self._ipd_sum / self._baseline_n
            self._baseline_nose = (self._nose_sum / self._nose_n) if self._nose_n else None
        self.phase = Phase.TESTING
        self._test_started = t_s
        self._next_target(t_s)

    def _fail(self, reason: str) -> None:
        self.phase = Phase.FAILED
        self.fail_reason = reason

    # ── test phase ────────────────────────────────────────────────
    def _head_moved(self, roll_deg, ipd_px, nose_shift) -> bool:
        if self._baseline_roll is not None and \
                abs(roll_deg - self._baseline_roll) > self.guard_max_roll_deg:
            return True
        if self._baseline_ipd and ipd_px > 0 and \
                abs(ipd_px - self._baseline_ipd) / self._baseline_ipd > self.guard_max_ipd_change:
            return True
        if self._baseline_nose is not None and nose_shift is not None and \
                abs(nose_shift - self._baseline_nose) > self.guard_max_nose_shift:
            return True
        return False

    def classify(self, offset: tuple[float, float]) -> str | None:
        """Nearest reference point, None when ambiguous (margin not met)."""
        if not self.references:
            return None
        dists = sorted(
            (math.hypot(offset[0] - ref[0], offset[1] - ref[1]), point)
            for point, ref in self.references.items())
        best_d, best = dists[0]
        if len(dists) > 1 and best_d > 1e-9:
            if dists[1][0] / best_d < self.confidence_margin:
                return None       # too close to call
        return best

    def _update_test(self, t_s, offset, roll_deg, ipd_px, nose_shift) -> None:
        assert self._test_started is not None
        if t_s - self._test_started >= self.duration_s:
            self.phase = Phase.DONE
            return
        if self._head_moved(roll_deg, ipd_px, nose_shift):
            self.samples_head_invalid += 1
            self._dwell_started = None
            self._prev_offset = offset
            return

        # First-movement direction check (once per target).
        if not self._first_move_evaluated and self._prev_offset is not None \
                and self.current_target is not None:
            step = math.hypot(offset[0] - self._prev_offset[0],
                              offset[1] - self._prev_offset[1])
            if step > 0.02:   # a real jump, not jitter
                ref = self.references[self.current_target]
                before = math.hypot(self._prev_offset[0] - ref[0],
                                    self._prev_offset[1] - ref[1])
                after = math.hypot(offset[0] - ref[0], offset[1] - ref[1])
                self._first_move_correct = after < before
                self._first_move_evaluated = True
                if not self._first_move_correct:
                    self.direction_errors += 1
        self._prev_offset = offset

        zone = self.classify(offset)
        if zone == self.current_target:
            if self._dwell_started is None:
                self._dwell_started = t_s
            elif t_s - self._dwell_started >= self.dwell_s:
                self.hits.append(Hit(
                    target=self.current_target,
                    shown_at_s=self._target_shown_at,
                    acquired_at_s=self._dwell_started,
                    first_move_correct=self._first_move_correct,
                ))
                self._next_target(t_s)
        else:
            self._dwell_started = None

    def _next_target(self, t_s: float) -> None:
        pts = self.points
        if self.sequence == "alternate" and self._outer:
            # fixed turn over the outer points: L, R, L, R … (a classic
            # pro-saccade sequence, every jump a full-width one)
            self.current_target = self._outer[self._seq_i % len(self._outer)]
            self._seq_i += 1
        else:
            candidates = [p for p in pts if p != self.current_target]
            if self.prefer_far_targets and self.current_target is not None:
                cur = pts[self.current_target]
                # weight by squared screen distance → diagonals dominate
                weights = [(pts[p][0] - cur[0]) ** 2 + (pts[p][1] - cur[1]) ** 2
                           for p in candidates]
                self.current_target = self._rng.choices(candidates, weights=weights)[0]
            else:
                self.current_target = self._rng.choice(candidates)
        self._target_shown_at = t_s
        self._dwell_started = None
        self._first_move_evaluated = False
        self._first_move_correct = True

    # ── results ───────────────────────────────────────────────────
    def features(self) -> dict[str, float]:
        n = len(self.hits)
        lat = sorted(h.latency_ms for h in self.hits)
        median_lat = lat[n // 2] if n else 0.0
        mean_lat = (sum(lat) / n) if n else 0.0
        attempted = n + (1 if self.current_target is not None
                         and self.phase is Phase.DONE else 0)
        span_min = self.duration_s / 60.0
        valid = max(1, self.samples_total - self.samples_blink)
        return {
            "n_targets_acquired": float(n),
            "targets_per_min": round(n / span_min, 2) if span_min > 0 else 0.0,
            "median_latency_ms": round(median_lat, 1),
            "mean_latency_ms": round(mean_lat, 1),
            "direction_error_rate": round(self.direction_errors / attempted, 3)
                if attempted else 0.0,
            "head_invalid_pct": round(self.samples_head_invalid / valid, 3),
            "blink_pct": round(self.samples_blink / self.samples_total, 3)
                if self.samples_total else 0.0,
            "calibration_ok": 1.0 if self.phase is not Phase.FAILED else 0.0,
        }
