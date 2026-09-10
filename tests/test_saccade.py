"""Saccade task logic + paradigm: synthetic proband end-to-end (headless)."""

import math

import pytest

from capture.base_capture import FacePose, TrackingFrame
from capture.mock_capture import SimulationSource
from paradigms.saccade_logic import POINTS, POINT_ORDER, Phase, SaccadeTask
from paradigms.saccade_test import SaccadeTest

FS = 30.0
# Synthetic gaze geometry: screen positions map linearly to gaze offsets of
# ±0.10 IPD horizontally / ±0.06 vertically (plausible webcam magnitudes).
def _gaze_for(point: str, points: dict | None = None) -> tuple[float, float]:
    nx, ny = (points or POINTS)[point]
    return ((nx - 0.5) * 0.20, (ny - 0.5) * 0.12)


def _task(**over) -> SaccadeTask:
    kw = dict(calib_per_point_s=1.0, calib_settle_s=0.3, calib_min_samples=5,
              duration_s=30.0, dwell_s=0.2, rng_seed=7)
    kw.update(over)
    return SaccadeTask(**kw)


def _run_proband(task: SaccadeTask, reaction_s: float = 0.25,
                 noise: float = 0.004, seconds: float = 42.0) -> SaccadeTask:
    """Simulate a proband: fixates calibration points, then jumps to each
    target after `reaction_s` (deterministic pseudo-noise)."""
    task.start(0.0)
    look_at = task.calib_order[0]
    target_since = 0.0
    current_target = None
    n = int(seconds * FS)
    for i in range(n):
        t = i / FS
        if task.phase is Phase.CALIBRATING:
            look_at = task.calib_point or look_at
        elif task.phase is Phase.TESTING:
            if task.current_target != current_target:
                current_target = task.current_target
                target_since = t
            if current_target and t - target_since >= reaction_s:
                look_at = current_target
        else:
            break
        gx, gy = _gaze_for(look_at, task.points)
        gx += noise * math.sin(2 * math.pi * 3.1 * t)
        gy += noise * math.sin(2 * math.pi * 4.3 * t + 1.0)
        task.update(t, (gx, gy), ear=0.30, roll_deg=1.0, ipd_px=50.0,
                    nose_shift=0.0)
    return task


def test_calibration_produces_separable_references():
    task = _run_proband(_task(), seconds=6.0)
    assert task.phase in (Phase.TESTING, Phase.DONE)
    assert set(task.references) == set(POINT_ORDER)
    # references reproduce the synthetic geometry (median ≈ exact)
    for p in POINT_ORDER:
        gx, gy = _gaze_for(p)
        rx, ry = task.references[p]
        assert math.hypot(rx - gx, ry - gy) < 0.01


def test_full_run_hits_targets_and_measures_latency():
    task = _run_proband(_task(), reaction_s=0.25)
    assert task.phase is Phase.DONE
    feats = task.features()
    # Latency = stimulus → gaze ARRIVAL (dwell excluded): reaction 0.25 s
    # plus sample quantization → ~300 ms.
    assert feats["n_targets_acquired"] >= 20
    assert 250 <= feats["median_latency_ms"] <= 450
    assert feats["direction_error_rate"] <= 0.1
    assert feats["calibration_ok"] == 1.0
    assert feats["head_invalid_pct"] == 0.0
    # consecutive targets never repeat
    seq = [h.target for h in task.hits]
    assert all(a != b for a, b in zip(seq, seq[1:]))


def test_slow_proband_scores_fewer_targets():
    fast = _run_proband(_task(), reaction_s=0.2).features()
    slow = _run_proband(_task(), reaction_s=0.9).features()
    assert slow["n_targets_acquired"] < fast["n_targets_acquired"]
    assert slow["median_latency_ms"] > fast["median_latency_ms"]


def test_unstable_calibration_fails():
    task = _task(calib_max_spread=0.01)
    _run_proband(task, noise=0.02, seconds=6.5)
    assert task.phase is Phase.FAILED
    assert "unruhig" in task.fail_reason


def test_head_movement_invalidates_samples():
    task = _run_proband(_task(), seconds=6.0)   # through calibration
    assert task.phase is Phase.TESTING
    t0 = 6.0
    for i in range(30):   # 1 s with the head turned (nose shifted)
        task.update(t0 + i / FS, _gaze_for("MI"), ear=0.30,
                    roll_deg=1.0, ipd_px=50.0, nose_shift=0.4)
    assert task.samples_head_invalid == 30


def test_blinks_are_gated():
    task = _run_proband(_task(), seconds=6.0)
    before = task.samples_blink
    task.update(6.0, (0.0, 0.0), ear=0.05, roll_deg=1.0, ipd_px=50.0)
    assert task.samples_blink == before + 1


def test_classifier_returns_none_when_ambiguous():
    task = _run_proband(_task(), seconds=6.0)
    mi, ro = task.references["MI"], task.references["RO"]
    midpoint = ((mi[0] + ro[0]) / 2, (mi[1] + ro[1]) / 2)  # equidistant MI/RO
    assert task.classify(midpoint) is None
    assert task.classify(task.references["MI"]) == "MI"


def test_paradigm_wraps_logic_and_computes_features():
    src = SimulationSource(mode="ocular_fixation")
    test = SaccadeTest(capture=src, duration=30.0)
    # Feed synthetic FacePoses through the envelope path (like the runner).
    task = test.task
    look = task.calib_order[0]              # layout comes from test_config.yaml
    for i in range(int(40 * FS)):
        t = i / FS
        if task.phase is Phase.CALIBRATING:
            look = task.calib_point or look
        elif task.phase is Phase.TESTING and task.current_target:
            look = task.current_target
        elif task.phase in (Phase.DONE, Phase.FAILED):
            break
        gx, gy = _gaze_for(look, task.points)
        ipd = 50.0
        cx, cy = 340.0, 200.0
        face = FacePose(
            timestamp_us=int(t * 1e6),
            iris_left=(cx - ipd / 2 + gx * ipd, cy + gy * ipd),
            iris_right=(cx + ipd / 2 + gx * ipd, cy + gy * ipd),
            corners_left=((cx - 40, cy), (cx - 10, cy)),
            corners_right=((cx + 10, cy), (cx + 40, cy)),
            ear_left=0.3, ear_right=0.3, nose=(cx, cy + 30),
        )
        test._on_tracking(TrackingFrame(timestamp_us=int(t * 1e6), face=face))
    feats = test.compute_features()
    assert feats["calibration_ok"] == 1.0
    assert feats["n_targets_acquired"] > 10
    assert feats["n_face_frames"] > 100
    assert "_fail_reason" not in feats


def test_horizontal_layout_alternates_left_right():
    """The clinical default: three points L / M / R, calibration starts in the
    centre and visits L and R three times each, test targets in fixed turn
    L, R, L, R … (every jump full width)."""
    task = _task(layout="horizontal", sequence="alternate")
    assert task.calib_visits == ["M", "L", "R", "L", "R", "L", "R"]
    assert task.calib_order == ["M", "L", "R"] and set(task.points) == {"L", "M", "R"}
    run = _run_proband(task, seconds=20.0)
    assert run.phase in (Phase.TESTING, Phase.DONE), run.fail_reason
    assert set(run.references) == {"L", "M", "R"}
    seq = [h.target for h in run.hits]
    assert len(seq) >= 6 and all(a != b for a, b in zip(seq, seq[1:]))
    assert set(seq) <= {"L", "R"}                       # the centre is never a target
    import pytest as _pt
    with _pt.raises(ValueError):
        _task(layout="diagonal")
    with _pt.raises(ValueError):
        _task(sequence="zigzag")


def test_repeated_visits_ignore_a_late_first_arrival():
    """Why the points are visited three times: on the first visit of L the eyes
    arrive late (the offset drifts through the whole window), on the later
    visits they sit still. The reference is the median over the visits, so it
    lands where the eyes really rest — a single visit would put it halfway."""
    task = _task(layout="horizontal", sequence="alternate")
    true = {k: _gaze_for(k, task.points) for k in task.points}
    task.start(0.0)
    visit_of = {}
    t = 0.0
    while task.phase is Phase.CALIBRATING and t < 20:
        key = task.calib_point
        idx = task.calib_index
        visit_of.setdefault(key, []).append(idx) if idx not in visit_of.get(key, []) else None
        gx, gy = true[key]
        if key == "L" and visit_of["L"][0] == idx:        # first L visit: slow drift in
            frac = min(1.0, (t - task._calib_point_started) / task.calib_per_point_s)
            gx = true["M"][0] + (gx - true["M"][0]) * frac
        task.update(t, (gx, gy), ear=0.30, roll_deg=0.0, ipd_px=50.0, nose_shift=0.0)
        t += 1 / FS
    assert task.phase is Phase.TESTING, task.fail_reason
    rx, _ = task.references["L"]
    assert abs(rx - true["L"][0]) < 0.005            # not dragged towards the centre
    assert task.noise["L"] < 0.01 and task.noise["M"] < 0.002
    # explicit visit lists are validated against the layout
    with pytest.raises(ValueError):
        _task(layout="horizontal", calib_visits=["M", "X"])


def test_separation_is_judged_against_noise():
    """A 0.03-IPD gap is fine with 0.002 noise and hopeless with 0.012 noise."""
    def run(noise):
        task = _task(layout="horizontal", min_separation=0.012, min_separation_snr=4.0)
        task.start(0.0)
        t = 0.0
        i = 0
        while task.phase is Phase.CALIBRATING and t < 20:
            key = task.calib_point
            gx = {"L": -0.03, "M": 0.0, "R": 0.03}[key] + noise * (1 if i % 2 else -1)
            task.update(t, (gx, 0.0), ear=0.30, roll_deg=0.0, ipd_px=50.0, nose_shift=0.0)
            t += 1 / FS
            i += 1
        return task
    assert run(0.002).phase is Phase.TESTING
    bad = run(0.012)
    assert bad.phase is Phase.FAILED and "Rauschen" in bad.fail_reason
