"""Fast unit tests for VideoLab: session/segment store + paradigm gating."""

from motor_tests.config import get_unmet_capabilities
from video.store import VideoSession, Segment, load_for_patient


def test_video_session_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("video.store.VIDEO_SESSIONS_DIR", tmp_path)
    vs = VideoSession.create(1, "P001")
    vs.video_path = "/some/video.mp4"
    vs.video_name = "video.mp4"
    seg = vs.add_segment("Tremoranalyse links", 1.0, 3.5)
    seg.clip_path = "/some/seg_001.mp4"
    seg.deidentified = True
    assert seg.id == "seg_001"
    assert abs(seg.duration_s - 2.5) < 1e-9
    assert not seg.analyzed
    seg.results["finger_tapping"] = {"features": {"n_taps": 5}, "recorded_at": "t", "raw_path": ""}
    assert seg.analyzed

    p = vs.save()
    assert p.exists()

    loaded = VideoSession.load(str(p))
    assert loaded.patient_code == "P001"
    assert loaded.video_name == "video.mp4"
    assert len(loaded.segments) == 1
    s0 = loaded.segments[0]
    assert isinstance(s0, Segment)
    assert s0.name == "Tremoranalyse links"
    assert s0.clip_path == "/some/seg_001.mp4"
    assert s0.deidentified is True
    assert s0.analyzed
    assert s0.results["finger_tapping"]["features"]["n_taps"] == 5

    assert load_for_patient(1, "P001") is not None
    assert load_for_patient(2, "P999") is None


def test_segment_id_increments(tmp_path, monkeypatch):
    monkeypatch.setattr("video.store.VIDEO_SESSIONS_DIR", tmp_path)
    vs = VideoSession.create(1, "P001")
    a = vs.add_segment("a", 0, 1)
    b = vs.add_segment("b", 1, 2)
    assert (a.id, b.id) == ("seg_001", "seg_002")
    vs.remove_segment("seg_001")
    assert [s.id for s in vs.segments] == ["seg_002"]


def test_video_config_loads_with_defaults():
    from video.config import cfg, video_filter
    assert cfg("import", "max_width") >= 1
    assert cfg("import", "container") == "mp4"
    assert isinstance(cfg("segments", "min_length_s"), (int, float))
    assert cfg("analysis", "default_hand") in ("left", "right")
    assert cfg("nope", "missing", default="x") == "x"
    assert "Videos (" in video_filter()


def test_videolab_capability_gating():
    """Paradigms VideoLab offers on a webcam/video source."""
    # webcam-class sources lack absolute position
    assert not get_unmet_capabilities("finger_tapping", "webcam")
    assert not get_unmet_capabilities("hand_open_close", "webcam")
    assert not get_unmet_capabilities("pronation_supination", "webcam")
    # tremor needs absolute palm position → gated on video
    assert get_unmet_capabilities("postural_tremor", "webcam")
    assert get_unmet_capabilities("rest_tremor", "webcam")
