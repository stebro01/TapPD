"""Tests for the source abstraction layer (capture.source.SourceProfile)."""

from capture.base_capture import HandFrame
from capture.source import (
    SourceProfile, profile_for, READY_Y_MM,
    CAP_ABS_POSITION, CAP_FINGERTIPS, LEAP, WEBCAM, MOCK,
)
from capture.mock_capture import SimulationSource
from capture.mediapipe_capture import WebcamSource


def _frame(y=200.0, conf=0.9, hand="right"):
    return HandFrame(timestamp_us=0, hand_type=hand, palm_position=(0.0, y, 0.0),
                     palm_velocity=(0.0, 0.0, 0.0), fingers=[], confidence=conf)


def test_profile_for_classifies_devices():
    assert profile_for(SimulationSource()).kind == MOCK
    assert profile_for(WebcamSource()).kind == WEBCAM


def test_leap_readiness_requires_hand_above_sensor():
    leap = SourceProfile(LEAP, set())
    assert leap.hand_ready(_frame(y=READY_Y_MM + 10)) is True
    assert leap.hand_ready(_frame(y=READY_Y_MM - 10)) is False   # too low
    assert leap.hand_ready(_frame(y=300, conf=0.1)) is False      # low confidence
    assert leap.hand_ready(None) is False


def test_webcam_readiness_is_presence_only():
    web = SourceProfile(WEBCAM, set())
    # No absolute-Y requirement — a confident hand anywhere counts.
    assert web.hand_ready(_frame(y=0.0)) is True
    assert web.hand_ready(_frame(y=0.0, conf=0.1)) is False


def test_adapt_frame_is_identity_without_eye_ref():
    f = _frame()
    for kind in (LEAP, WEBCAM, MOCK):
        assert SourceProfile(kind, set()).adapt_frame(f) is f


def test_adapt_frame_promotes_eye_ref_on_webcam_only():
    f = _frame()
    f.eye_ref_mm = (10.0, -250.0, 0.0)
    web = SourceProfile(WEBCAM, set())
    adapted = web.adapt_frame(f)
    assert adapted is not f                      # copy, raw frame untouched
    assert adapted.palm_position == (10.0, -250.0, 0.0)
    assert f.palm_position != adapted.palm_position
    # Idempotent: the copy carries no eye_ref → second adapt is identity.
    assert web.adapt_frame(adapted) is adapted
    # Leap/mock ignore the attribute.
    assert SourceProfile(LEAP, set()).adapt_frame(f) is f


def test_webcam_lacks_absolute_position_capability():
    web = profile_for(WebcamSource())
    assert CAP_FINGERTIPS in web.capabilities
    assert CAP_ABS_POSITION not in web.capabilities


def test_webcam_gains_abs_position_with_face_tracking():
    src = WebcamSource()
    src.enable_face(True)      # not connected → just flips the local flag
    assert CAP_ABS_POSITION in profile_for(src).capabilities
    src.enable_face(False)
    assert CAP_ABS_POSITION not in profile_for(src).capabilities


def test_prompts_are_source_aware():
    assert "Kamera" in profile_for(WebcamSource()).prompts()["waiting"]
