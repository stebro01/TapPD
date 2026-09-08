"""Video sessions keyed per clinical session, with legacy adoption."""

import pytest

from video import store
from video.store import VideoSession, load_for_session


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VIDEO_SESSIONS_DIR", tmp_path)
    return tmp_path


def test_sessions_get_their_own_directory(_isolated):
    a = VideoSession.create(1, "P001"); a.db_session_id = 10
    b = VideoSession.create(1, "P001"); b.db_session_id = 11
    pa, pb = a.save(), b.save()

    assert pa != pb
    assert pa.parent.name == "session_10" and pb.parent.name == "session_11"
    assert load_for_session(1, "P001", 10).db_session_id == 10
    assert load_for_session(1, "P001", 11).db_session_id == 11


def test_missing_session_is_none():
    assert load_for_session(1, "P001", 99) is None


def test_legacy_file_is_adopted_by_the_newest_session(_isolated):
    legacy = VideoSession.create(1, "P001")     # no db_session_id → old layout
    legacy.set_video("/x/v.mp4", "v.mp4")
    path = legacy.save()
    assert path.parent.name == "P001"           # per-patient, as before

    assert load_for_session(1, "P001", 5, newest_session_id=7) is None
    adopted = load_for_session(1, "P001", 7, newest_session_id=7)

    assert adopted is not None and adopted.video_name == "v.mp4"
    assert adopted.db_session_id == 7           # stamped …
    assert VideoSession.load(str(path)).db_session_id == 7   # … and saved


def test_legacy_file_with_an_id_belongs_only_to_that_session(_isolated):
    legacy = VideoSession.create(1, "P001")
    legacy.db_session_id = 3
    # simulate the old layout: force the per-patient path
    legacy.path = str(_isolated / "P001" / "session.json")
    (_isolated / "P001").mkdir(parents=True)
    legacy.save()

    assert load_for_session(1, "P001", 3, newest_session_id=9) is not None
    assert load_for_session(1, "P001", 9, newest_session_id=9) is None


def test_new_clips_of_a_keyed_session_land_in_its_directory(_isolated):
    from video.protocol import protocol_for_paradigm
    s = VideoSession.create_recording(1, "P001", protocol_for_paradigm("finger_tapping"),
                                      mirrored=True)
    s.db_session_id = 4

    take = s.begin_take("finger_tapping")

    assert take.parent.name == "session_4"
