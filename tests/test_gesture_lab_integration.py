"""Gesture Lab integration: battery results as Measurements + library I/O."""

import sqlite3

import pytest

from storage.database import Measurement, Patient, get_measurements, save_measurement, save_patient
from gesture_lab.gesture_db import (
    ensure_gesture_table,
    export_library,
    import_library,
    list_templates,
    save_template,
)
from gesture_lab.models import GestureTemplate


def test_gesture_battery_measurement_roundtrip(conn):
    """gesture_battery has a seeded concept + GESTURE_TEST category and
    round-trips through the star schema like any other measurement."""
    p = save_patient(conn, Patient(patient_code="GEST01"))
    m = Measurement(patient_id=p.id, test_type="gesture_battery", hand="right",
                    duration_s=95.0, source_kind="webcam")
    m.features = {"battery_score": 0.75, "n_poses_tested": 8.0, "n_correct": 6.0,
                  "pose_01_score": 0.91}
    save_measurement(conn, m)

    got = get_measurements(conn, p.id)[0]
    assert got.test_type == "gesture_battery"
    assert got.source_kind == "webcam"
    assert got.features["battery_score"] == 0.75

    cat = conn.execute(
        "SELECT CATEGORY_CHAR, CONCEPT_CD FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?",
        (m.id,)).fetchone()
    assert cat[0] == "GESTURE_TEST"
    assert cat[1] == "TAPPD:GESTURE_BATTERY"
    # Seeded concept exists (FK satisfied)
    assert conn.execute(
        "SELECT COUNT(*) FROM CONCEPT_DIMENSION WHERE CONCEPT_CD='TAPPD:GESTURE_BATTERY'"
    ).fetchone()[0] == 1


def test_migrate_v2_seeds_gesture_concept(conn):
    """Existing v2 DBs gain the new concept via the idempotent migration."""
    from storage.database import _migrate_v2
    conn.execute("DELETE FROM CONCEPT_DIMENSION WHERE CONCEPT_CD='TAPPD:GESTURE_BATTERY'")
    conn.commit()
    _migrate_v2(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM CONCEPT_DIMENSION WHERE CONCEPT_CD='TAPPD:GESTURE_BATTERY'"
    ).fetchone()[0] == 1


@pytest.fixture()
def gesture_conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_gesture_table(c)
    yield c
    c.close()


def _template(name="Faust", pose=1):
    return GestureTemplate(
        name=name, pose_number=pose, gesture_type="static", hand_type="right",
        pose_vector=[0.1] * 35, expected_extensions=[False] * 5,
        finger_weights={"0": 1.0}, raw_frames=[{"hand_type": "right"}])


def test_library_export_import_roundtrip(gesture_conn, tmp_path):
    save_template(gesture_conn, _template())
    save_template(gesture_conn, _template("Zeigen", 2))
    path = str(tmp_path / "lib.json")
    assert export_library(gesture_conn, path) == 2

    dest = sqlite3.connect(":memory:")
    dest.row_factory = sqlite3.Row
    ensure_gesture_table(dest)
    assert import_library(dest, path) == 2
    got = list_templates(dest)
    assert {t.name for t in got} == {"Faust", "Zeigen"}
    faust = next(t for t in got if t.name == "Faust")
    assert faust.pose_vector == [0.1] * 35
    assert faust.raw_frames == [{"hand_type": "right"}]

    # replace vs additive
    assert import_library(dest, path, replace=True) == 2
    assert len(list_templates(dest)) == 2
    import_library(dest, path, replace=False)
    assert len(list_templates(dest)) == 4
    dest.close()


def test_library_import_rejects_foreign_json(gesture_conn, tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text('{"foo": 1}')
    with pytest.raises(ValueError):
        import_library(gesture_conn, str(bad))
