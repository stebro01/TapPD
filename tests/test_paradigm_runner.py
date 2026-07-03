"""Unit tests for the shared ParadigmRunner frame-pump (no Qt / no sidecar)."""

from capture.base_capture import HandPose, FingerData, BoneData
from capture.mock_capture import SimulationSource
from paradigms.finger_tapping import FingerTappingTest
from paradigms.runner import ParadigmRunner


def _frame(t_us, hand="right", x=0.0):
    f = FingerData(0, (x, 0.0, 0.0), True, [BoneData((0, 0, 0), (0, 10, 0))])
    return HandPose(timestamp_us=t_us, hand_type=hand, palm_position=(x, 200.0, 0.0),
                    palm_velocity=(0, 0, 0), fingers=[f] * 5, confidence=0.9)


def test_runner_accumulates_and_gates_duration():
    test = FingerTappingTest(capture=SimulationSource(), duration=1.0, hand="right")
    r = ParadigmRunner(test, settle_s=0.0, duration_s=1.0)
    r.begin()
    # frames within [0,1]s plus one past the duration
    for i in range(0, 1200, 33):       # ~36 frames over 1.2 s (us)
        r.feed(_frame(i * 1000, "right", x=float(i)))
    # live metric buffer filled for the right hand
    assert len(r.live["right"]) > 10
    # the > 1.0 s frame triggered the duration gate
    assert r.duration_reached
    # the paradigm accumulated frames (only up to the duration)
    assert len(test.get_frames()) > 0
    assert r.metric_label  # non-empty label


def test_runner_settle_discards_early_frames():
    test = FingerTappingTest(capture=SimulationSource(), duration=5.0, hand="right")
    r = ParadigmRunner(test, settle_s=10.0)   # huge settle → everything discarded
    r.begin()
    for i in range(20):
        r.feed(_frame(i * 33000, "right"))
    assert sum(len(v) for v in r.live.values()) == 0
    assert len(test.get_frames()) == 0
