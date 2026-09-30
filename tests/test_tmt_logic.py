"""Digital Trail Making Test: target layout, labels and visiting order."""

import math
import random

import pytest

from paradigms import tmt_logic
from paradigms.tmt_logic import MIN_SPACING, TMTSegmentResult, TMTTaskState


@pytest.fixture(autouse=True)
def _seed():
    random.seed(3)


def test_part_a_is_numbered_and_part_b_alternates_numbers_and_letters():
    assert [t.label for t in TMTTaskState("A", 5).targets] == ["1", "2", "3", "4", "5"]
    assert [t.label for t in TMTTaskState("B", 6).targets] == ["1", "A", "2", "B", "3", "C"]


def test_targets_are_spaced_and_inside_the_safe_area():
    for _ in range(10):
        pts = tmt_logic._generate_positions(12)
        assert len(pts) == 12
        assert all(0.15 <= x <= 0.85 and 0.15 <= y <= 0.85 for x, y in pts)
        for i, (x1, y1) in enumerate(pts):
            for x2, y2 in pts[i + 1:]:
                assert math.hypot(x1 - x2, y1 - y2) >= MIN_SPACING - 1e-9


def test_only_the_next_target_counts_as_a_visit():
    t = TMTTaskState("A", 4)
    assert t.next_label == "1" and t.progress == 0.0
    assert not t.visit_target(2)                    # skipping ahead is wrong
    assert t.visit_target(0) and t.targets[0].visited
    assert t.next_label == "2" and t.progress == 0.25
    t.record_wrong_approach(1.2, approached_idx=3)
    assert t.wrong_approaches == [(1.2, 3, 1)]        # (time, approached, expected)


def test_visiting_all_targets_completes_the_trail():
    t = TMTTaskState("B", 6)
    for i in range(6):
        assert t.visit_target(i)
    assert t.is_complete() and t.current_target is None and t.next_label == ""
    assert t.progress == 1.0


def test_total_time_needs_both_timestamps():
    t = TMTTaskState("A", 3)
    assert t.total_time_s == 0.0
    t._start_time_s, t._end_time_s = 10.0, 42.5
    assert t.total_time_s == 32.5


def test_segments_are_recorded():
    t = TMTTaskState("A", 3)
    t.record_segment(TMTSegmentResult(0, 1, 0, 0.2, 1.0, 1.3, 200, 800, 120, 100, 300, 0))
    assert len(t.segment_results) == 1 and t.segment_results[0].to_index == 1
