"""Ocular fixation paradigm: features from the synthetic face stream."""

import pytest

from capture.base_capture import TrackingFrame
from capture.mock_capture import SimulationSource
from capture.source import CAP_FACE_LANDMARKS
from paradigms.config import get_task_requirements
from paradigms.ocular_fixation import OcularFixationTest


def _run_synthetic(seconds=60.0, fs=60.0) -> OcularFixationTest:
    """Feed the mock's deterministic face scenario directly (no threads)."""
    src = SimulationSource(mode="ocular_fixation")
    test = OcularFixationTest(capture=src, duration=seconds)
    n = int(seconds * fs)
    for i in range(n):
        t = i / fs
        face = src._build_face(t, int(t * 1e6))
        test._on_tracking(TrackingFrame(timestamp_us=int(t * 1e6), face=face))
    return test


def test_blink_rate_matches_scenario():
    """Mock blinks every 4 s → ~15 blinks/min."""
    feats = _run_synthetic().compute_features()
    assert feats["blink_rate_per_min"] == pytest.approx(15.0, abs=2.0)


def test_fixation_and_intrusion_features_plausible():
    feats = _run_synthetic().compute_features()
    # Micro-jitter of <1 px on a 50 px IPD → dispersion around 1 %IPD.
    assert 0.2 < feats["gaze_dispersion_pct_ipd"] < 5.0
    # One intrusion pulse every 5 s (enter + leave) → clearly nonzero rate.
    assert feats["saccadic_intrusions_per_min"] > 5.0
    assert feats["mean_ear"] == pytest.approx(0.30, abs=0.05)
    assert feats["n_face_frames"] == 3600.0


def test_too_few_face_frames_yields_empty_result():
    test = _run_synthetic(seconds=0.2, fs=60.0)   # 12 frames < min 30
    feats = test.compute_features()
    assert feats["blink_rate_per_min"] == 0.0
    assert feats["face_coverage"] == 0.0


def test_ocular_requirements_and_gating():
    assert get_task_requirements("ocular_fixation") == {CAP_FACE_LANDMARKS}
    from paradigms.config import get_unmet_capabilities
    assert get_unmet_capabilities("ocular_fixation", "webcam") == set()
    assert get_unmet_capabilities("ocular_fixation", "mock") == set()
    assert get_unmet_capabilities("ocular_fixation", "leap") == {CAP_FACE_LANDMARKS}


def test_face_metric_is_live_plot_ready():
    src = SimulationSource(mode="ocular_fixation")
    test = OcularFixationTest(capture=src)
    face = src._build_face(1.0, 1_000_000)
    m = test.get_face_metric(face)
    assert isinstance(m, float) and m >= 0.0
