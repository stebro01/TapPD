"""Notes with attachments on items of the record (NOTE_FACT + data/attachments)."""

import json

import pytest

from storage import attachments as att
from storage.database import (
    Measurement, Note, delete_measurement, delete_note, get_notes, get_notes_for,
    save_measurement, save_note,
)
from tests.conftest import make_patient_row, make_session_row


@pytest.fixture(autouse=True)
def _att_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(att, "ATTACHMENTS_DIR", tmp_path / "attachments")
    return tmp_path


def test_note_round_trip_per_item(conn):
    pid = make_patient_row(conn, code="N001")
    sid = make_session_row(conn, pid)
    n = save_note(conn, Note(patient_id=pid, session_id=sid, kind="step", ref=f"{sid}:tap_right",
                             text="Patient war müde", attachments=[{"name": "a.jpg", "path": "/x/a.jpg",
                                                                     "size": 3, "added_at": "t"}]))
    assert n.id and n.created_at
    (back,) = get_notes(conn, pid)
    assert back.kind == "step" and back.ref == f"{sid}:tap_right" and back.text == "Patient war müde"
    assert back.attachments[0]["name"] == "a.jpg" and back.session_id == sid
    row = conn.execute("SELECT CATEGORY_CHAR, NAME_CHAR, NOTE_BLOB FROM NOTE_FACT").fetchone()
    assert row[0] == "STEP" and row[1] == f"{sid}:tap_right"
    assert json.loads(row[2])["attachments"][0]["name"] == "a.jpg"

    back.text = "geändert"
    save_note(conn, back)
    assert get_notes(conn, pid)[0].text == "geändert" and len(get_notes(conn, pid)) == 1
    assert get_notes_for(conn, "step", f"{sid}:tap_right")[0].id == n.id

    delete_note(conn, n.id)
    assert get_notes(conn, pid) == []


def test_unknown_kind_is_rejected(conn):
    pid = make_patient_row(conn)
    with pytest.raises(ValueError):
        save_note(conn, Note(patient_id=pid, kind="thing", ref="1", text="x"))


def test_deleting_a_measurement_or_session_takes_its_notes_along(conn, tmp_path):
    from storage.database import delete_session
    pid = make_patient_row(conn, code="N002")
    sid = make_session_row(conn, pid)
    m = save_measurement(conn, Measurement(patient_id=pid, session_id=sid,
                                           test_type="finger_tapping", hand="right"))
    f = tmp_path / "foto.jpg"; f.write_bytes(b"jpg")
    entry = att.add_attachment("N002", "measurement", str(m.id), str(f))
    save_note(conn, Note(patient_id=pid, session_id=sid, kind="measurement", ref=str(m.id),
                         text="n", attachments=[entry]))
    save_note(conn, Note(patient_id=pid, session_id=sid, kind="session", ref=str(sid), text="s"))
    delete_measurement(conn, m.id)
    assert [n.kind for n in get_notes(conn, pid)] == ["session"]
    assert not (tmp_path / "attachments" / "N002" / f"measurement_{m.id}" / "foto.jpg").exists()
    delete_session(conn, sid)                       # FK cascade
    assert get_notes(conn, pid) == []


def test_attachments_are_copied_deduplicated_and_removed(tmp_path):
    src = tmp_path / "bild.jpg"; src.write_bytes(b"x" * 1500)
    a = att.add_attachment("P/1", "step", "6:tap_left", str(src))
    b = att.add_attachment("P/1", "step", "6:tap_left", str(src))
    assert a["name"] == "bild.jpg" and b["name"] == "bild (2).jpg" and a["size"] == 1500
    assert att.target_dir("P/1", "step", "6:tap_left").name == "step_6_tap_left"
    assert att.size_label(1500) == "2 kB" and att.size_label(2_500_000) == "2.5 MB"
    assert att.remove_attachment(a) and att.remove_attachment(b)
    assert not att.target_dir("P/1", "step", "6:tap_left").exists()   # emptied folder gone
    with pytest.raises(FileNotFoundError):
        att.add_attachment("P", "step", "x", str(tmp_path / "nope.png"))


def test_note_blob_column_is_added_to_older_databases(tmp_path):
    import sqlite3
    from storage.database import _create_tables, _migrate_v2
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    _create_tables(c)
    c.execute("ALTER TABLE NOTE_FACT RENAME TO NOTE_OLD")
    c.execute("CREATE TABLE NOTE_FACT (NOTE_ID INTEGER PRIMARY KEY AUTOINCREMENT, CATEGORY_CHAR TEXT, "
              "NAME_CHAR TEXT, NOTE_TEXT TEXT, PATIENT_NUM INTEGER, ENCOUNTER_NUM INTEGER, "
              "UPDATE_DATE TEXT, SOURCESYSTEM_CD TEXT, CREATED_AT TEXT)")
    _migrate_v2(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(NOTE_FACT)")}
    assert "NOTE_BLOB" in cols
