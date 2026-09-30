"""Clinical form entries in OBSERVATION_FACT (clinical/store.py)."""

import json

from clinical.schema import load_form
from clinical.store import (delete_form_entry, get_form_entries, get_form_entry, latest_entry,
                            prefill, save_form_entry)
from storage.database import (Measurement, get_all_measurements, get_last_measurement_dates,
                              get_measurements, get_session_measurements, save_measurement)
from tests.conftest import make_patient_row, make_session_row

ANSWERS = {"diagnosis_year": 2019, "hoehn_yahr": "2", "updrs3_total": 28, "med_state": "off",
           "nms": ["hyposmia", "rbd"], "dbs": True, "family_pd": "no",
           "medication": [{"substance": "levodopa", "dose_mg": 100, "per_day": 4},
                          {"substance": "rasagiline", "dose_mg": 1, "per_day": 1}]}


def _rows(conn, entry_id):
    return conn.execute(
        "SELECT CONCEPT_CD, VALTYPE_CD, TVAL_CHAR, NVAL_NUM, INSTANCE_NUM, OBSERVATION_BLOB "
        "FROM OBSERVATION_FACT WHERE json_extract(OBSERVATION_BLOB, '$.form_entry') = ? "
        "ORDER BY OBSERVATION_ID", (entry_id,)).fetchall()


def test_save_writes_q_row_item_rows_repeat_rows_and_computed(conn):
    pid = make_patient_row(conn, code="C001")
    sid = make_session_row(conn, pid)
    form = load_form("pd_anamnese")
    e = save_form_entry(conn, pid, sid, form, ANSWERS, recorded_at="2026-09-10T09:00:00")
    assert e.id and e.computed["ledd_mg"] == 500.0

    q = conn.execute("SELECT * FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (e.id,)).fetchone()
    assert q["VALTYPE_CD"] == "Q" and q["CONCEPT_CD"] == "TAPPD:FORM_PD_ANAMNESE"
    assert q["CATEGORY_CHAR"] == "CLINICAL" and q["ENCOUNTER_NUM"] == sid
    blob = json.loads(q["OBSERVATION_BLOB"])
    assert blob["answers"] == ANSWERS and blob["computed"]["ledd_mg"] == 500.0

    rows = {(r["CONCEPT_CD"], r["INSTANCE_NUM"]): r for r in _rows(conn, e.id)}
    assert rows[("TAPPD:HOEHN_YAHR", 1)]["NVAL_NUM"] == 2.0          # choice stored as number
    assert rows[("TAPPD:UPDRS3_TOTAL", 1)]["VALTYPE_CD"] == "N"
    assert rows[("TAPPD:MED_STATE", 1)]["TVAL_CHAR"] == "off"
    assert rows[("TAPPD:NMS", 1)]["TVAL_CHAR"] == "hyposmia,rbd"
    assert rows[("TAPPD:DBS", 1)]["NVAL_NUM"] == 1.0
    assert rows[("TAPPD:MEDICATION", 1)]["VALTYPE_CD"] == "B"
    assert rows[("TAPPD:MEDICATION", 2)]["TVAL_CHAR"] == "Rasagilin"
    assert json.loads(rows[("TAPPD:MEDICATION", 1)]["OBSERVATION_BLOB"])["row"]["dose_mg"] == 100
    assert rows[("TAPPD:LEDD", 1)]["NVAL_NUM"] == 500.0
    assert ("TAPPD:FALLS_12M", 1) not in rows                        # unanswered → no row

    concepts = {r[0] for r in conn.execute(
        "SELECT CONCEPT_CD FROM CONCEPT_DIMENSION WHERE CATEGORY_CHAR='CLINICAL'")}
    assert {"TAPPD:FORM_PD_ANAMNESE", "TAPPD:LEDD", "LOINC:72172-0", "TAPPD:MEDICATION"} <= concepts

    (back,) = get_form_entries(conn, pid)
    assert back.id == e.id and back.answers == ANSWERS and back.form_id == "pd_anamnese"
    assert get_form_entry(conn, e.id).recorded_at == "2026-09-10T09:00:00"


def test_clinical_rows_are_not_measurements(conn):
    pid = make_patient_row(conn, code="C002")
    sid = make_session_row(conn, pid)
    save_form_entry(conn, pid, sid, load_form("pd_anamnese"), ANSWERS)
    m = save_measurement(conn, Measurement(patient_id=pid, session_id=sid,
                                           test_type="finger_tapping", hand="right"))
    assert [x.id for x in get_measurements(conn, pid)] == [m.id]
    assert [x.id for x in get_session_measurements(conn, sid)] == [m.id]
    assert [x[1].id for x in get_all_measurements(conn) if x[0].id == pid] == [m.id]
    assert get_last_measurement_dates(conn)[pid] == m.recorded_at


def test_update_replaces_rows_and_delete_removes_everything(conn):
    pid = make_patient_row(conn, code="C003")
    sid = make_session_row(conn, pid)
    form = load_form("pd_anamnese")
    e = save_form_entry(conn, pid, sid, form, ANSWERS)
    n_before = len(_rows(conn, e.id))

    changed = {**ANSWERS, "hoehn_yahr": "3", "medication": []}
    e2 = save_form_entry(conn, pid, sid, form, changed, entry_id=e.id)
    assert e2.id == e.id and len(get_form_entries(conn, pid)) == 1
    rows = _rows(conn, e.id)
    assert len(rows) < n_before
    assert next(r["NVAL_NUM"] for r in rows if r["CONCEPT_CD"] == "TAPPD:HOEHN_YAHR") == 3.0
    assert not [r for r in rows if r["CONCEPT_CD"] in ("TAPPD:MEDICATION", "TAPPD:LEDD")]

    delete_form_entry(conn, e.id)
    assert get_form_entries(conn, pid) == [] and _rows(conn, e.id) == []
    assert conn.execute("SELECT COUNT(*) FROM OBSERVATION_FACT WHERE PATIENT_NUM=?",
                        (pid,)).fetchone()[0] == 0


def test_prefill_carries_the_last_entry_forward(conn):
    pid = make_patient_row(conn, code="C004")
    form = load_form("pd_anamnese")
    assert prefill(conn, pid, form) == {}
    s1 = make_session_row(conn, pid)
    save_form_entry(conn, pid, s1, form, ANSWERS, recorded_at="2026-01-01T10:00:00")
    s2 = make_session_row(conn, pid)
    save_form_entry(conn, pid, s2, form, {**ANSWERS, "hoehn_yahr": "2.5"},
                    recorded_at="2026-06-01T10:00:00")
    assert latest_entry(conn, pid, "pd_anamnese").answers["hoehn_yahr"] == "2.5"
    assert prefill(conn, pid, form)["hoehn_yahr"] == "2.5"
    form.carry_forward = False
    assert prefill(conn, pid, form) == {}


def test_move_form_entry_moves_all_of_its_rows(conn):
    from clinical.store import move_form_entry
    pid = make_patient_row(conn, code="C005")
    s1, s2 = make_session_row(conn, pid), make_session_row(conn, pid)
    e = save_form_entry(conn, pid, s1, load_form("pd_anamnese"), ANSWERS)
    move_form_entry(conn, e.id, s2)
    (back,) = get_form_entries(conn, pid)
    assert back.session_id == s2
    rows = conn.execute("SELECT DISTINCT ENCOUNTER_NUM FROM OBSERVATION_FACT WHERE PATIENT_NUM=?",
                        (pid,)).fetchall()
    assert [r[0] for r in rows] == [s2]                     # Q row and every item row
