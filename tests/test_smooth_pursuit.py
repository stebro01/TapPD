"""Smooth-pursuit paradigm: features from the synthetic pursuit scenario."""

import math

import pytest

from capture.base_capture import FacePose, HandFrame, TrackingFrame
from capture.mock_capture import SimulationSource
from capture.source import CAP_FACE_LANDMARKS
from paradigms.config import get_task_requirements, get_unmet_capabilities
from paradigms.smooth_pursuit import SmoothPursuitTest


def _run_synthetic(seconds=40.0, fs=60.0) -> SmoothPursuitTest:
    """Feed the mock's deterministic pursuit scenario directly (no threads)."""
    src = SimulationSource(mode="smooth_pursuit")
    test = SmoothPursuitTest(capture=src, duration=seconds)
    for i in range(int(seconds * fs)):
        t = i / fs
        ts = int(t * 1e6)
        hands, face = src._build_pursuit(t, ts)
        test._on_tracking(TrackingFrame(timestamp_us=ts, hands=hands, face=face))
    return test


def test_gain_matches_scripted_scenario():
    """Mock follows the target at gain 0.90 — the regression must find it."""
    feats = _run_synthetic().compute_features()
    assert feats["pursuit_gain"] == pytest.approx(0.90, abs=0.12)
    assert feats["pursuit_r2"] > 0.8


def test_catchup_saccades_are_detected():
    """One scripted catch-up step every 2 s → about 0.5 per second."""
    feats = _run_synthetic().compute_features()
    assert feats["catchup_saccades_per_s"] == pytest.approx(0.5, abs=0.25)


def test_horizontal_sweep_is_reported_as_horizontal():
    feats = _run_synthetic().compute_features()
    assert feats["pursuit_axis_vertical"] == 0.0
    assert feats["target_excursion_ipd"] > 0.5     # ±0.45 IPD sweep
    assert feats["n_pursuit_samples"] > 2000       # 40 s minus blinks


def test_motionless_target_yields_no_gain():
    """A finger that never moves must not produce a pursuit gain — otherwise
    the metric is noise divided by noise."""
    test = SmoothPursuitTest(capture=SimulationSource(mode="smooth_pursuit"))
    for i in range(600):
        ts = int(i / 60.0 * 1e6)
        hand = HandFrame(timestamp_us=ts, hand_type="right",
                         palm_position=(0.0, 0.0, 0.0),
                         palm_velocity=(0.0, 0.0, 0.0))
        face = FacePose(timestamp_us=ts, iris_left=(315.0, 200.0),
                        iris_right=(365.0, 200.0),
                        corners_left=((300.0, 200.0), (330.0, 200.0)),
                        corners_right=((350.0, 200.0), (380.0, 200.0)),
                        ear_left=0.30, ear_right=0.30)
        test._on_tracking(TrackingFrame(timestamp_us=ts, hands=[hand], face=face))
    feats = test.compute_features()
    assert feats["pursuit_gain"] == 0.0
    assert feats["target_excursion_ipd"] == 0.0


def test_resting_patient_hand_does_not_become_the_target():
    """Both hands in frame: the moving one is the examiner's. Picking the
    resting hand would silently report gain 0 for a healthy pursuit."""
    test = SmoothPursuitTest(capture=SimulationSource(mode="smooth_pursuit"))
    src = SimulationSource(mode="smooth_pursuit")
    for i in range(1800):
        t = i / 60.0
        ts = int(t * 1e6)
        hands, face = src._build_pursuit(t, ts)
        resting = HandFrame(timestamp_us=ts, hand_type="left",
                            palm_position=(-200.0, -150.0, 0.0),
                            palm_velocity=(0.0, 0.0, 0.0))
        test._on_tracking(TrackingFrame(timestamp_us=ts,
                                        hands=hands + [resting], face=face))
    feats = test.compute_features()
    assert feats["pursuit_gain"] == pytest.approx(0.90, abs=0.15)


def test_vertical_sweep_is_detected_with_positive_gain():
    """The palm's y counts up, the iris offset counts down — without the sign
    flip in the paradigm this gain would come out negative."""
    test = SmoothPursuitTest(capture=SimulationSource(mode="smooth_pursuit"))
    for i in range(1800):
        t = i / 60.0
        ts = int(t * 1e6)
        target = 0.45 * math.sin(2 * math.pi * 0.25 * t)
        hand = HandFrame(timestamp_us=ts, hand_type="right",
                         palm_position=(0.0, target * 63.0, 0.0),
                         palm_velocity=(0.0, 0.0, 0.0))
        # Gaze follows downward in image coordinates when the target goes up.
        iy = 200.0 - 50.0 * 0.9 * target
        face = FacePose(timestamp_us=ts, iris_left=(315.0, iy),
                        iris_right=(365.0, iy),
                        corners_left=((300.0, 200.0), (330.0, 200.0)),
                        corners_right=((350.0, 200.0), (380.0, 200.0)),
                        ear_left=0.30, ear_right=0.30)
        test._on_tracking(TrackingFrame(timestamp_us=ts, hands=[hand], face=face))
    feats = test.compute_features()
    assert feats["pursuit_axis_vertical"] == 1.0
    assert feats["pursuit_gain"] == pytest.approx(0.90, abs=0.12)


def test_too_few_frames_yields_empty_result():
    feats = _run_synthetic(seconds=0.5, fs=60.0).compute_features()
    assert feats["pursuit_gain"] == 0.0
    assert feats["pursuit_coverage"] == 0.0


def test_pursuit_requirements_and_gating():
    """Ocular category → needs the face stream; the Leap cannot serve it."""
    assert get_task_requirements("smooth_pursuit") == {CAP_FACE_LANDMARKS}
    assert get_unmet_capabilities("smooth_pursuit", "webcam") == set()
    assert get_unmet_capabilities("smooth_pursuit", "mock") == set()
    assert get_unmet_capabilities("smooth_pursuit", "leap") == {CAP_FACE_LANDMARKS}


def test_registered_in_paradigm_registry():
    from paradigms.registry import BY_KEY, Category
    spec = BY_KEY["smooth_pursuit"]
    assert spec.category is Category.OCULAR
    assert spec.load_class() is SmoothPursuitTest
