"""PinchDetector: hysteresis + debounce, so a noisy distance never chatters."""

from capture.base_capture import HandFrame
from paradigms.pinch_detector import PinchDetector, PinchEvent


def _frame(pinch_mm: float) -> HandFrame:
    return HandFrame(timestamp_us=0, hand_type="right", palm_position=(0, 0, 0),
                     palm_velocity=(0, 0, 0), palm_normal=(0, -1, 0), fingers=[],
                     pinch_distance=pinch_mm, grab_strength=0.0, confidence=1.0)


def _feed(det, distances):
    return [det.update(_frame(d)) for d in distances]


def test_grab_needs_debounce_frames_below_threshold():
    det = PinchDetector(grab_threshold_mm=25, release_threshold_mm=40, debounce_frames=3)
    events = _feed(det, [20, 20, 20])
    assert events == [None, None, PinchEvent.GRAB]
    assert det.is_pinching


def test_a_single_noisy_frame_resets_the_debounce():
    det = PinchDetector(grab_threshold_mm=25, release_threshold_mm=40, debounce_frames=3)
    assert _feed(det, [20, 20, 30, 20, 20]) == [None] * 5      # never three in a row
    assert not det.is_pinching
    assert _feed(det, [20]) == [PinchEvent.GRAB]


def test_release_uses_the_higher_threshold():
    det = PinchDetector(grab_threshold_mm=25, release_threshold_mm=40, debounce_frames=2)
    _feed(det, [10, 10])
    assert det.is_pinching
    # between the two thresholds: neither grab nor release
    assert _feed(det, [30, 35, 38, 30]) == [None] * 4 and det.is_pinching
    assert _feed(det, [45, 45]) == [None, PinchEvent.RELEASE]
    assert not det.is_pinching


def test_grab_release_grab_cycle():
    det = PinchDetector(debounce_frames=1)
    assert _feed(det, [10, 50, 10]) == [PinchEvent.GRAB, PinchEvent.RELEASE, PinchEvent.GRAB]


def test_reset_clears_state_and_counter():
    det = PinchDetector(debounce_frames=3)
    _feed(det, [10, 10])
    det.reset()
    assert not det.is_pinching
    assert _feed(det, [10]) == [None]                           # counter started over
