"""TrackingFrame envelope migration: native emission, runner, fallbacks."""

import time

from capture.base_capture import HandPose, FingerData, BoneData, TrackingFrame
from capture.mock_capture import SimulationSource
from paradigms.runner import ParadigmRunner
from paradigms.finger_tapping import FingerTappingTest
from paradigms.tremor import PosturalTremorTest


def _hand(t_us, hand="right", x=0.0):
    f = FingerData(0, (x, 0.0, 0.0), True, [BoneData((0, 0, 0), (0, 10, 0))])
    return HandPose(timestamp_us=t_us, hand_type=hand, palm_position=(x, 200.0, 0.0),
                    palm_velocity=(0, 0, 0), fingers=[f] * 5, confidence=0.9)


def test_mock_emits_native_envelopes_with_both_hands():
    """Bilateral scenario → ONE TrackingFrame per tick carrying both hands."""
    src = SimulationSource()
    src.mode = "postural_tremor"   # bilateral scenario (both hands)
    src.connect()
    got: list[TrackingFrame] = []
    src.start_tracking(got.append)
    time.sleep(0.2)
    src.stop_tracking()
    src.disconnect()

    assert got, "no envelopes emitted"
    tf = got[len(got) // 2]
    assert isinstance(tf, TrackingFrame)
    assert {h.hand_type for h in tf.hands} == {"left", "right"}
    assert tf.left is not None and tf.right is not None


def test_stop_recording_clears_tracking_mode():
    """After tracking, a plain start_recording must deliver per-hand frames."""
    src = SimulationSource()
    src.connect()
    src.start_tracking(lambda tf: None)
    src.stop_recording()          # e.g. via BaseParadigm.stop()
    assert src._tracking_callback is None
    hands = []
    src.start_recording(hands.append)
    time.sleep(0.1)
    src.stop_recording()
    src.disconnect()
    assert hands and isinstance(hands[0], HandPose)


def test_runner_accepts_envelope_and_single_frame():
    test = FingerTappingTest(capture=SimulationSource(), duration=5.0, hand="right")
    r = ParadigmRunner(test, settle_s=0.0, duration_s=5.0)
    r.begin()
    # Envelope with both hands
    tf = TrackingFrame(timestamp_us=1000,
                       hands=[_hand(1000, "right"), _hand(1000, "left")])
    r.feed(tf)
    # Legacy single frame still accepted
    r.feed(_hand(34_000, "right", x=5.0))
    assert len(r.live["right"]) == 2
    assert len(r.live["left"]) == 1
    # Unilateral paradigm collected only the chosen hand
    assert len(test.get_frames()) == 2


def test_bilateral_paradigm_gets_both_hands_from_one_envelope():
    test = PosturalTremorTest(capture=SimulationSource(), duration=5.0, hand="both")
    r = ParadigmRunner(test, settle_s=0.0, duration_s=5.0)
    r.begin()
    for i in range(5):
        ts = 1000 + i * 33_000
        r.feed(TrackingFrame(timestamp_us=ts,
                             hands=[_hand(ts, "left"), _hand(ts, "right")]))
    assert len(test.get_frames("left")) == 5
    assert len(test.get_frames("right")) == 5


def test_paradigm_start_uses_tracking_stream():
    """BaseParadigm.start() subscribes via start_tracking (envelope path)."""
    src = SimulationSource()
    src.mode = "postural_tremor"
    src.connect()
    test = PosturalTremorTest(capture=src, duration=5.0, hand="both")
    test.start()
    time.sleep(0.25)
    test.stop()
    src.disconnect()
    assert len(test.get_frames("left")) > 3
    assert len(test.get_frames("right")) > 3
