"""Fast unit tests for the replay/clip machinery (no MediaPipe / no camera)."""

import time

import pytest

from capture.base_capture import HandPose, FingerData, BoneData
from capture.clip import save_clip, list_clips, list_landmark_clips, CLIPS_DIR
from capture.replay_source import ReplaySource
from capture import create_source, normalize_source_kind
from capture.contracts import MotionSourceProtocol


def _landmark_frames(n=15):
    frames = []
    for i in range(n):
        f = FingerData(0, (float(i), 0.0, 0.0), True, [BoneData((0, 0, 0), (0, 10, 0))])
        hp = HandPose(timestamp_us=i * 33000, hand_type="right",
                      palm_position=(float(i), 200.0, 0.0), palm_velocity=(0, 0, 0),
                      fingers=[f] * 5, confidence=0.9)
        frames.append({"dt": i / 30.0, "hand": hp.to_dict()})
    return frames


@pytest.fixture
def landmark_clip(tmp_path, monkeypatch):
    # save_clip writes into CLIPS_DIR; redirect to a temp dir for isolation.
    monkeypatch.setattr("capture.clip.CLIPS_DIR", tmp_path)
    path = save_clip(_landmark_frames(), duration_s=0.5, label="utest", fps=30.0)
    return path


def test_normalize_source_kind_aliases():
    assert normalize_source_kind("mediapipe") == "webcam"
    assert normalize_source_kind("sim") == "mock"
    assert normalize_source_kind("leap") == "leap"


def test_replaysource_satisfies_protocol(landmark_clip):
    assert isinstance(ReplaySource(str(landmark_clip)), MotionSourceProtocol)


def test_factory_replay_returns_replaysource(landmark_clip):
    src = create_source("replay", clip_path=str(landmark_clip))
    assert isinstance(src, ReplaySource)
    assert src.sample_rate == 30.0


def test_replaysource_replays_and_loops(landmark_clip):
    src = ReplaySource(str(landmark_clip))
    got = []
    src.connect()
    assert src.is_connected()
    src.start_recording(lambda f: got.append(f))
    time.sleep(0.9)  # 0.5 s clip → should loop ~1.8×
    src.stop_recording()
    src.disconnect()
    assert not src.is_connected()
    assert len(got) > 15, "clip did not loop"
    ts = [g.timestamp_us for g in got]
    assert all(ts[i] < ts[i + 1] for i in range(len(ts) - 1)), "timestamps not monotonic"


def test_default_clip_path_prefers_default_then_newest(tmp_path, monkeypatch):
    monkeypatch.setattr("capture.clip.CLIPS_DIR", tmp_path)
    monkeypatch.setattr("capture.clip.DEFAULT_CLIP", tmp_path / "default.mp4")
    from capture.clip import default_clip_path
    assert default_clip_path() == ""                       # nothing yet
    (tmp_path / "clip_a.mp4").write_bytes(b"x")
    assert default_clip_path().endswith("clip_a.mp4")      # newest clip as fallback
    (tmp_path / "default.mp4").write_bytes(b"y")
    assert default_clip_path().endswith("default.mp4")     # explicit default wins


def test_list_clips_separates_video_and_landmark(tmp_path, monkeypatch):
    monkeypatch.setattr("capture.clip.CLIPS_DIR", tmp_path)
    (tmp_path / "clip_a.mp4").write_bytes(b"x")
    (tmp_path / "clip_b.json").write_text("{}")
    vids = [p.name for p in list_clips()]
    lms = [p.name for p in list_landmark_clips()]
    assert vids == ["clip_a.mp4"]
    assert lms == ["clip_b.json"]
