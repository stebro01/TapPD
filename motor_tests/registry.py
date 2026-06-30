"""Single source of truth for the assessment paradigms.

Previously a paradigm was declared in three places (main_window.TEST_CLASSES +
MOCK_MODES, test_dashboard.TESTS, and an implicit spatial set in config/storage),
which drifted. Everything now derives from ``PARADIGMS`` here.

The concrete test class is referenced by ``cls_path`` and imported lazily via
``load_class`` — so this module stays import-light (no numpy/Qt at import time),
letting storage/config import it cheaply without cycles.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from enum import Enum


class Category(Enum):
    """Paradigm category. The value is the DB CATEGORY_CHAR string (unchanged)."""
    MOTOR = "MOTOR_TEST"
    COGNITIVE = "COGNITIVE_TEST"


# Which screen handles a paradigm.
SCREEN_METRIC = "metric"   # ui/test_screen.py (live-metric motor tests)
SCREEN_HANOI = "hanoi"
SCREEN_SRT = "srt"
SCREEN_TMT = "tmt"


@dataclass(frozen=True)
class ParadigmSpec:
    key: str                       # canonical test_type (e.g. "finger_tapping")
    label: str                     # dashboard card label (may contain "\n")
    updrs: str                     # UPDRS item ("3.4") or "Kogn."
    description: str               # short dashboard subtitle
    category: Category
    bilateral: bool
    sim_scenario: str              # SimulationSource mode
    screen: str                    # SCREEN_*
    cls_path: str                  # "module:ClassName"
    cls_kwargs: dict = field(default_factory=dict)

    def load_class(self) -> type:
        module_name, class_name = self.cls_path.split(":")
        return getattr(importlib.import_module(module_name), class_name)


PARADIGMS: list[ParadigmSpec] = [
    ParadigmSpec("finger_tapping", "Finger Tapping", "3.4", "Daumen-Zeigefinger",
                 Category.MOTOR, False, "tapping", SCREEN_METRIC,
                 "motor_tests.finger_tapping:FingerTappingTest"),
    ParadigmSpec("hand_open_close", "Hand Öffnen/\nSchließen", "3.5", "Öffnen & Schließen",
                 Category.MOTOR, False, "open_close", SCREEN_METRIC,
                 "motor_tests.hand_open_close:HandOpenCloseTest"),
    ParadigmSpec("pronation_supination", "Pronation/\nSupination", "3.6", "Unterarm drehen",
                 Category.MOTOR, False, "pronation_supination", SCREEN_METRIC,
                 "motor_tests.pronation_supination:PronationSupinationTest"),
    ParadigmSpec("postural_tremor", "Posturaler\nTremor", "3.15", "Hände vorgestreckt",
                 Category.MOTOR, True, "postural_tremor", SCREEN_METRIC,
                 "motor_tests.tremor:PosturalTremorTest"),
    ParadigmSpec("rest_tremor", "Ruhetremor", "3.17", "Hände entspannt",
                 Category.MOTOR, True, "rest_tremor", SCREEN_METRIC,
                 "motor_tests.rest_tremor:RestTremorTest"),
    ParadigmSpec("tower_of_hanoi", "Türme von\nHanoi", "Kogn.", "Scheiben verschieben",
                 Category.COGNITIVE, False, "tower_of_hanoi", SCREEN_HANOI,
                 "motor_tests.tower_of_hanoi:TowerOfHanoiTest"),
    ParadigmSpec("spatial_srt", "Räumliche\nReaktionszeit", "Kogn.", "Sequenz-Lernen",
                 Category.COGNITIVE, False, "spatial_srt", SCREEN_SRT,
                 "motor_tests.spatial_srt:SpatialSRTTest"),
    ParadigmSpec("trail_making_a", "Trail Making\nTeil A", "Kogn.", "Zahlen verbinden",
                 Category.COGNITIVE, False, "trail_making", SCREEN_TMT,
                 "motor_tests.trail_making:TrailMakingTest", {"part": "A"}),
    ParadigmSpec("trail_making_b", "Trail Making\nTeil B", "Kogn.", "Zahlen & Buchstaben",
                 Category.COGNITIVE, False, "trail_making", SCREEN_TMT,
                 "motor_tests.trail_making:TrailMakingTest", {"part": "B"}),
]

BY_KEY: dict[str, ParadigmSpec] = {p.key: p for p in PARADIGMS}


def get(key: str) -> ParadigmSpec:
    return BY_KEY[key]


def all_keys() -> list[str]:
    return [p.key for p in PARADIGMS]


def category_str(key: str) -> str:
    """DB category string ("MOTOR_TEST"/"COGNITIVE_TEST") for a paradigm key."""
    spec = BY_KEY.get(key)
    return (spec.category if spec else Category.MOTOR).value


def is_spatial(key: str) -> bool:
    """Spatial/cognitive paradigms need absolute position (capability gating)."""
    spec = BY_KEY.get(key)
    return bool(spec and spec.category == Category.COGNITIVE)
