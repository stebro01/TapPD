"""Tower of Hanoi rules, move accounting and the optimal-solution bound."""

from paradigms.hanoi_logic import HanoiGameState


def test_initial_state_has_all_discs_on_the_left():
    g = HanoiGameState(3)
    assert g.pegs == [[3, 2, 1], [], []]
    assert g.top_disc(0) == 1 and g.top_disc(1) is None
    assert not g.is_solved()


def test_optimal_moves_is_two_to_the_n_minus_one():
    assert [HanoiGameState.optimal_moves(n) for n in (1, 2, 3, 4)] == [1, 3, 7, 15]


def test_legal_and_illegal_moves_are_both_recorded():
    g = HanoiGameState(3)
    assert g.move(0, 2, 0.5)                    # smallest disc → right: legal
    assert g.move(0, 1, 1.0)                    # disc 2 → centre: legal
    assert not g.move(1, 2, 1.5)                # 2 onto 1: illegal
    assert not g.move(0, 0, 2.0)                # same peg: illegal
    assert g.move(2, 0, 2.2)                    # 1 onto 3: legal again

    assert g.move_count == 3 and g.error_count == 2
    assert g.pegs == [[3, 1], [2], []]
    illegal = [m for m in g.move_history if not m.valid]
    assert illegal[0].disc == 2 and illegal[0].timestamp_s == 1.5
    assert illegal[1].from_peg == illegal[1].to_peg == 0


def test_moving_from_an_empty_peg_is_an_error_with_disc_zero():
    g = HanoiGameState(2)
    assert not g.move(1, 2)
    assert g.move_history[-1].disc == 0 and g.error_count == 1


def test_optimal_solution_solves_in_exactly_optimal_moves():
    g = HanoiGameState(3)

    def solve(n, src, dst, via):
        if n == 0:
            return
        solve(n - 1, src, via, dst)
        assert g.move(src, dst)
        solve(n - 1, via, dst, src)

    solve(3, 0, 2, 1)
    assert g.is_solved()
    assert g.move_count == HanoiGameState.optimal_moves(3) and g.error_count == 0


def test_reset_restores_the_start_position_and_clears_history():
    g = HanoiGameState(3)
    g.move(0, 2); g.move(0, 0)
    g.reset()
    assert g.pegs == [[3, 2, 1], [], []] and g.move_history == []
