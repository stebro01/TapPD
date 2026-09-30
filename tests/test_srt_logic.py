"""Spatial SRT block/trial structure — the part the screen relies on."""

import random

import pytest

from paradigms.srt_logic import SRTTaskState, SRTTrialResult


@pytest.fixture(autouse=True)
def _seed():
    random.seed(7)


def test_generated_sequences_never_repeat_a_target_consecutively():
    for _ in range(50):
        seq = SRTTaskState.generate_sequence(10)
        assert len(seq) == 10 and set(seq) <= {0, 1, 2, 3}
        assert all(a != b for a, b in zip(seq, seq[1:]))
        rnd = SRTTaskState.generate_random_targets(20)
        assert all(a != b for a, b in zip(rnd, rnd[1:]))


def test_blocks_alternate_random_and_sequence_after_practice():
    t = SRTTaskState(sequence=[0, 1, 2, 3, 0, 2, 1, 3, 2, 0], n_sequence_blocks=2,
                     n_random_blocks=3, trials_per_block=20, practice_trials=5)
    kinds = [b.block_type for b in t.blocks]
    assert kinds == ["practice", "random", "sequence", "random", "sequence", "random"]
    assert [b.n_trials for b in t.blocks] == [5, 20, 20, 20, 20, 20]
    assert t.total_trials == 105
    # a sequence block repeats the 10-element sequence to fill 20 trials
    assert t.blocks[2].targets == t.sequence * 2


def test_sequence_position_is_tracked_only_inside_sequence_blocks():
    t = SRTTaskState(sequence=[0, 1, 2, 3, 0, 2, 1, 3, 2, 0], n_sequence_blocks=1,
                     n_random_blocks=1, trials_per_block=12, practice_trials=2)
    assert t.is_practice() and t.current_sequence_position() == -1
    assert t.block_label() == "Übung"

    for _ in range(2 + 12):            # through practice and the random block
        t.advance_trial()
    assert t.current_block.block_type == "sequence"
    assert t.block_label() == "Block 2/2"
    assert t.current_target() == 0 and t.current_sequence_position() == 0
    t.advance_trial(); t.advance_trial()
    assert t.current_sequence_position() == 2 and t.current_target() == 2
    for _ in range(8):                             # trial 10 of 12: the sequence wrapped
        t.advance_trial()
    assert t.current_sequence_position() == 0 and t.current_target() == 0
    t.advance_trial(); t.advance_trial()           # block ends → task complete
    assert t.is_complete() and t.current_sequence_position() == -1


def test_advance_walks_to_completion_and_counts_trials():
    t = SRTTaskState(n_sequence_blocks=1, n_random_blocks=1, trials_per_block=3,
                     practice_trials=2)
    steps = 0
    while t.advance_trial():
        steps += 1
    assert t.is_complete()
    assert t.completed_trials == t.total_trials == 8
    assert t.current_target() is None and t.block_label() == ""


def test_results_are_kept_in_order():
    t = SRTTaskState(n_sequence_blocks=1, n_random_blocks=1, trials_per_block=2, practice_trials=1)
    for i in range(3):
        t.record_trial(SRTTrialResult(i, 0, "random", 1, 0, 0, 0, 0, 300 + i, 200, 500, 100, 90,
                                      400, True, -1))
    assert [r.reaction_time_ms for r in t.trial_results] == [300, 301, 302]
