"""Video analyses are timed in media time: frame index / clip fps.

The sidecar stamps video frames with their position in the clip and announces
the clip's fps (mediapipe_sidecar/PROTOCOL.md, "Time base"); the webcam source
takes that fps as its sample rate. These tests cover the main-app half with
synthetic messages: the recorder finds the right frequency on a media axis at
any clip rate, the message path carries axis and rate through to the result,
and the live camera stays on the wall clock at its old rate.
"""

import math

import pytest

from capture.base_capture import FingerData, HandFrame
from capture.mediapipe_capture import WebcamSource
from paradigms.recorder import compute_features_from_config


def _tap_frame(i, fps, f_hz, hand="right"):
    """Frame i of a clip at fps: thumb-index distance 45 ± 25 mm at f_hz."""
    d = 45.0 + 25.0 * math.sin(2 * math.pi * f_hz * i / fps)
    return HandFrame(timestamp_us=round(i * 1e6 / fps), hand_type=hand,
                     palm_position=(0.0, 0.0, 0.0), palm_velocity=(0.0, 0.0, 0.0),
                     fingers=[FingerData(0, (d, 0.0, 0.0), True),
                              FingerData(1, (0.0, 0.0, 0.0), True)],
                     frame_index=i)


def _tremor_frame(i, fps, f_hz, hand):
    """Frame i: palm oscillating ±3 mm in x at f_hz (absolute position, mm)."""
    x = 3.0 * math.sin(2 * math.pi * f_hz * i / fps)
    return HandFrame(timestamp_us=round(i * 1e6 / fps), hand_type=hand,
                     palm_position=(x, 200.0, 0.0), palm_velocity=(0.0, 0.0, 0.0),
                     frame_index=i)


# ── recorder: resampling at the clip's rate on a media axis ────────

@pytest.mark.parametrize("fps", [25.0, 30.0, 60.0])
@pytest.mark.parametrize("f_hz", [2.0, 5.0])
def test_tapping_frequency_on_a_media_axis(fps, f_hz):
    frames = [_tap_frame(i, fps, f_hz) for i in range(int(6 * fps))]
    feats = compute_features_from_config("finger_tapping", frames, fps)
    assert feats["tap_frequency_hz"] == pytest.approx(f_hz, rel=0.02)


@pytest.mark.parametrize("fps", [30.0, 60.0])
def test_tremor_frequency_on_a_media_axis(fps):
    n = int(10.7 * fps)                  # 10 s after warmup/cooldown → 0.1 Hz bins
    left = [_tremor_frame(i, fps, 5.0, "left") for i in range(n)]
    right = [_tremor_frame(i, fps, 5.0, "right") for i in range(n)]
    feats = compute_features_from_config("postural_tremor", left + right, fps,
                                         left_frames=left, right_frames=right)
    assert feats["R_dominant_frequency_hz"] == pytest.approx(5.0, abs=0.1)
    assert feats["L_dominant_frequency_hz"] == pytest.approx(5.0, abs=0.1)


# ── message path: sidecar messages → HandFrames → paradigm ─────────

def _hand_msg(i, fps, f_hz):
    """The sidecar's `hand` message for frame i of a clip at fps (media time)."""
    d = (45.0 + 25.0 * math.sin(2 * math.pi * f_hz * i / fps)) / 1000.0
    world = [[0.001 * k, 0.004 * k, 0.0] for k in range(21)]
    world[4] = [d, 0.08, 0.0]            # thumb tip
    world[8] = [0.0, 0.08, 0.0]          # index tip
    return {"type": "hand", "ts": round(i * 1e6 / fps), "frame": i, "w": 640, "h": 480,
            "hands": [{"handedness": "Right", "score": 0.95, "world": world}]}


def _replaying(fps, start_frame, end_frame):
    """A webcam source mid-replay, right after the sidecar's `video` message."""
    src = WebcamSource(replay_path="clip.mp4")
    got = []
    src._frame_callback, src._recording = got.append, True
    src._dispatch({"type": "video", "fps": fps, "frames": end_frame + 10,
                   "start_frame": start_frame, "end_frame": end_frame})
    return src, got


@pytest.mark.parametrize("fps", [30.0, 60.0])
def test_video_messages_set_media_axis_and_rate(fps):
    from paradigms.finger_tapping import FingerTappingTest
    start, end = int(1.5 * fps), int(7.5 * fps)       # a range 1.5 … 7.5 s into the clip
    src, got = _replaying(fps, start, end)
    for i in range(start, end):
        src._dispatch(_hand_msg(i, fps, 2.0))

    assert src.sample_rate == fps and src.time_base == "media"
    assert src.frames_seen == end - start and src.last_frame_index == end - 1
    assert got[0].timestamp_us == round(start * 1e6 / fps) and got[0].frame_index == start
    test = FingerTappingTest(capture=src, duration=6.0, hand="right")
    for f in got:
        test._on_frame(f)
    assert test.compute_features()["tap_frequency_hz"] == pytest.approx(2.0, rel=0.02)


def test_done_keeps_what_the_sidecar_delivered():
    src, _ = _replaying(30.0, 0, 90)
    calls = []
    src.set_done_callback(lambda: calls.append(1))
    src._dispatch({"type": "done", "frames": 90, "first_frame": 0, "last_frame": 89,
                   "end_frame": 90, "eof": False, "pts_drift_ms": 0.0})
    assert src.last_done["frames"] == 90 and src.last_done["eof"] is False
    assert calls == [1] and not src._recording


def test_live_camera_keeps_wall_clock_and_its_rate(monkeypatch):
    src = WebcamSource()
    assert src.time_base == "wallclock" and src.sample_rate == 30.0
    monkeypatch.setattr(src, "is_connected", lambda: True)
    monkeypatch.setattr(src, "_send", lambda obj: None)

    # a replay on the same source announced 60 fps; a warm restart of that
    # clip (no new `video` message) keeps it …
    src.replay_path = "clip.mp4"
    src._dispatch({"type": "video", "fps": 60.0, "frames": 600,
                   "start_frame": 0, "end_frame": None})
    src.start_recording(lambda f: None)
    assert src.sample_rate == 60.0 and src.time_base == "media"

    # … and back on the live camera it is wall clock at the live rate again
    src.replay_path = ""
    src.start_recording(lambda f: None)
    assert src.time_base == "wallclock" and src.sample_rate == 30.0
    assert src.video_info == {}


def test_a_malformed_video_message_keeps_the_rate():
    src = WebcamSource(replay_path="clip.mp4")
    src._dispatch({"type": "video", "fps": None})
    src._dispatch({"type": "video", "fps": "x"})
    assert src.sample_rate == 30.0
