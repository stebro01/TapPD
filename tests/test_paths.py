"""storage.paths: a record recorded on another machine (other root, other OS,
other slash style) still finds its files under this project's data/."""

import json

import storage.paths as paths


def test_resolve_keeps_existing_and_reroots_alien_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    here = tmp_path / "data" / "samples" / "232_finger_tapping_left.json"
    here.parent.mkdir(parents=True)
    here.write_text("{}", encoding="utf-8")

    assert paths.resolve(str(here)) == str(here)                       # exists: untouched
    win = r"C:\Users\park\MyProjects\TapPD\data\samples\232_finger_tapping_left.json"
    assert paths.resolve(win) == str(here)                             # Windows root → here
    mac = "/Volumes/KB/TapPD/data/samples/232_finger_tapping_left.json"
    assert paths.resolve(mac) == str(here)                             # macOS root → here
    nested = r"D:\x\data\video_sessions\232\session_6\seg_001.mp4"
    assert paths.resolve(nested) == nested                             # missing stays missing
    assert paths.resolve("") == "" and paths.resolve(None) == ""
    assert paths.resolve("/tmp/no-data-segment.json") == "/tmp/no-data-segment.json"

    d = {"clip_path": win, "other": 1, "track_path": ""}
    paths.resolve_keys(d, ("clip_path", "track_path", "missing"))
    assert d == {"clip_path": str(here), "other": 1, "track_path": ""}


def test_measurements_and_video_sessions_load_rerooted(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    import storage.database as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "t.db")
    raw = tmp_path / "data" / "samples" / "raw.json"
    raw.parent.mkdir(parents=True)
    raw.write_text("{}", encoding="utf-8")
    clip = tmp_path / "data" / "video_sessions" / "P1" / "session_1" / "seg_001.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"0")

    conn = db.get_db()
    p = db.Patient(patient_code="P1")
    saved = db.save_patient(conn, p)
    pid = saved.id if hasattr(saved, "id") else int(saved)
    m = db.Measurement(patient_id=pid, test_type="finger_tapping", hand="left", duration_s=5.0,
                       raw_data_path=r"C:\elsewhere\TapPD\data\samples\raw.json",
                       provenance={"clip_path": r"C:\elsewhere\TapPD\data\video_sessions\P1\session_1\seg_001.mp4",
                                   "track_path": r"C:\elsewhere\TapPD\data\video_sessions\P1\session_1\seg_001.track.json",
                                   "video_session": ""})
    db.save_measurement(conn, m)
    got = db.get_measurements(conn, pid)[0]
    assert got.raw_data_path == str(raw)
    assert got.provenance["clip_path"] == str(clip)
    assert got.provenance["track_path"].endswith("seg_001.track.json")     # missing: unchanged
    conn.close()

    from video.store import VideoSession
    sess = tmp_path / "data" / "video_sessions" / "P1" / "session.json"
    sess.write_text(json.dumps({
        "patient_id": pid, "patient_code": "P1",
        "video_path": r"C:\elsewhere\TapPD\data\video_sessions\P1\session_1\seg_001.mp4",
        "segments": [{"id": "seg_001", "name": "a", "start_s": 0.0, "end_s": 1.0,
                      "clip_path": r"C:\elsewhere\TapPD\data\video_sessions\P1\session_1\seg_001.mp4",
                      "results": {"finger_tapping": {"raw_path": "/Volumes/KB/TapPD/data/samples/raw.json"}}}],
        "steps": [{"id": "step_1", "title": "t", "instruction": "i",
                   "clip_path": r"C:\elsewhere\TapPD\data\video_sessions\P1\session_1\seg_001.mp4"}],
    }), encoding="utf-8")
    vs = VideoSession.load(str(sess))
    assert vs.video_path == str(clip)
    assert vs.segments[0].clip_path == str(clip)
    assert vs.segments[0].results["finger_tapping"]["raw_path"] == str(raw)
    assert vs.steps[0].clip_path == str(clip)
