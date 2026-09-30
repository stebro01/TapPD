"""VideoLab → clinical DB export (video/export.py)."""

import pytest

import storage.database as db
from storage.database import Patient, get_measurements, get_sessions, save_patient
from video.export import AlreadyExported, export_result
from video.store import VideoSession


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Temp DB file + temp video-session dir; returns a saved patient."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("video.store.VIDEO_SESSIONS_DIR", tmp_path / "vs")
    conn = db.get_db()
    try:
        patient = save_patient(conn, Patient(patient_code="VTEST01"))
    finally:
        conn.close()
    return patient


def _session_with_result(patient) -> tuple[VideoSession, object]:
    vs = VideoSession.create(patient.id, patient.patient_code)
    seg = vs.add_segment("Tapping re", 2.0, 9.5, paradigm="finger_tapping", hand="right")
    seg.results["finger_tapping"] = {
        "features": {"tap_frequency_hz": 3.2, "mean_amplitude_mm": 41.0},
        "recorded_at": "2026-07-02T12:00:00",
        "raw_path": "",
        "source_kind": "video",
    }
    vs.save()
    return vs, seg


def test_export_creates_session_and_measurement(env):
    vs, seg = _session_with_result(env)
    m = export_result(vs, seg, "finger_tapping")

    assert m.id and vs.db_session_id
    assert seg.results["finger_tapping"]["measurement_id"] == m.id

    conn = db.get_db()
    try:
        stored = get_measurements(conn, env.id)
        sessions = get_sessions(conn, env.id)
    finally:
        conn.close()
    assert len(stored) == 1 and len(sessions) == 1
    got = stored[0]
    assert got.test_type == "finger_tapping"
    assert got.hand == "right"
    assert got.source_kind == "video"
    assert got.session_id == vs.db_session_id
    assert got.features["tap_frequency_hz"] == 3.2
    assert got.duration_s == pytest.approx(7.5)


def test_double_export_is_rejected_and_reuses_db_session(env):
    vs, seg = _session_with_result(env)
    m1 = export_result(vs, seg, "finger_tapping")
    with pytest.raises(AlreadyExported):
        export_result(vs, seg, "finger_tapping")

    # A second segment goes into the SAME db session.
    seg2 = vs.add_segment("Tapping li", 12.0, 20.0, paradigm="finger_tapping", hand="left")
    seg2.results["finger_tapping"] = {"features": {"tap_frequency_hz": 2.1},
                                      "recorded_at": "", "source_kind": "video"}
    m2 = export_result(vs, seg2, "finger_tapping")
    assert m2.session_id == m1.session_id

    conn = db.get_db()
    try:
        assert len(get_sessions(conn, env.id)) == 1
        assert len(get_measurements(conn, env.id)) == 2
    finally:
        conn.close()


def test_export_state_survives_reload(env):
    vs, seg = _session_with_result(env)
    export_result(vs, seg, "finger_tapping")

    loaded = VideoSession.load(vs.path)
    assert loaded.db_session_id == vs.db_session_id
    assert loaded.segments[0].results["finger_tapping"]["measurement_id"]


def test_export_without_result_raises(env):
    vs = VideoSession.create(env.id, env.patient_code)
    seg = vs.add_segment("leer", 0.0, 5.0, paradigm="finger_tapping")
    with pytest.raises(ValueError):
        export_result(vs, seg, "finger_tapping")


def test_export_points_at_the_raw_json_when_the_analysis_wrote_one(env, tmp_path):
    from video.export import export_or_update
    vs, seg = _session_with_result(env)
    seg.clip_path = str(tmp_path / "seg_001.mp4")
    m = export_result(vs, seg, "finger_tapping")
    assert m.raw_data_path == seg.clip_path            # no JSON yet: the clip

    raw = tmp_path / "raw.json"
    raw.write_text("{}")
    seg.results["finger_tapping"]["raw_path"] = str(raw)
    m2 = export_or_update(vs, seg, "finger_tapping")
    assert m2.id == m.id and m2.raw_data_path == str(raw)
