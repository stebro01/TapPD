"""End-to-end contract tests for the Source↔Paradigm layering.

For every paradigm in the registry, drive a real SimulationSource through the
paradigm's own recording path (no Qt, no UI) and assert the contract holds:
frames flow, get_live_metric works, and motor paradigms compute a numeric
feature dict. This is the robustness proof that the abstraction is sound — any
new paradigm or source that breaks the contract fails here.
"""

import math
import time

import pytest

from capture.mock_capture import SimulationSource
from capture.contracts import MotionSourceProtocol
from paradigms import registry

RECORD_S = 0.7  # ~84 frames at 120 Hz — enough for min_frames on every paradigm


def _run(spec, hand="right"):
    """Construct the paradigm on a SimulationSource, record briefly, return it."""
    source = SimulationSource(mode=spec.sim_scenario)
    test = spec.load_class()(source, duration=5.0, hand=hand, **spec.cls_kwargs)
    test._start_time_s = time.perf_counter()  # spatial tasks read this
    test.start()
    time.sleep(RECORD_S)
    test.stop()
    return test


def test_simulation_source_satisfies_protocol():
    assert isinstance(SimulationSource(), MotionSourceProtocol)


@pytest.mark.parametrize("spec", registry.PARADIGMS, ids=lambda s: s.key)
def test_paradigm_records_frames_from_simulation(spec):
    test = _run(spec)
    if spec.bilateral:
        assert test.left_frames and test.right_frames, \
            f"{spec.key}: bilateral paradigm got no left/right frames"
    else:
        assert test.frames, f"{spec.key}: no frames collected"


@pytest.mark.parametrize("spec", registry.PARADIGMS, ids=lambda s: s.key)
def test_live_metric_is_finite(spec):
    test = _run(spec)
    frames = test.right_frames if spec.bilateral else test.frames
    assert frames
    value = test.get_live_metric(frames[-1])
    assert isinstance(value, (int, float))
    assert math.isfinite(value), f"{spec.key}: non-finite live metric {value}"


# The 5 motor paradigms compute features purely from frames; the spatial ones
# derive their scores from game events produced by their UI screen, so we only
# assert their feature call doesn't crash on a headless frame stream.
_MOTOR_KEYS = [s.key for s in registry.PARADIGMS if s.category is registry.Category.MOTOR]


@pytest.mark.parametrize("key", _MOTOR_KEYS)
def test_motor_paradigm_computes_numeric_features(key):
    spec = registry.get(key)
    test = _run(spec)
    features = test.compute_features()
    assert isinstance(features, dict) and features, f"{key}: empty feature dict"
    assert all(isinstance(v, (int, float)) for v in features.values()), \
        f"{key}: non-numeric feature values: {features}"
