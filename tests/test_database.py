"""Unit tests for storage/database.py – star schema CRUD, helpers, and edge cases."""

import json

import pytest

from storage.database import (
    Measurement,
    Patient,
    Session,
    create_session,
    delete_measurement,
    delete_session,
    find_patients,
    get_all_measurements,
    get_last_measurement_dates,
    get_measurements,
    get_patient,
    get_session_measurements,
    get_sessions,
    save_measurement,
    save_patient,
    update_raw_data_path,
    _category_for_test,
    _concept_cd_to_test_type,
    _marshal_observation_blob,
    _marshal_patient_blob,
    _test_type_to_concept_cd,
    _unmarshal_observation_blob,
    _unmarshal_patient_blob,
)

from tests.conftest import make_measurement_row, make_patient_row, make_session_row


# ═══════════════════════════════════════════════════════════════════
# Schema & Seed Data
# ═══════════════════════════════════════════════════════════════════

class TestSchemaCreation:
    """Verify all tables, indices, and seed data are created correctly."""

    def test_all_tables_exist(self, conn):
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()}
        expected = {
            "PATIENT_DIMENSION", "VISIT_DIMENSION", "OBSERVATION_FACT",
            "CONCEPT_DIMENSION", "CODE_LOOKUP", "NOTE_FACT",
        }
        assert expected.issubset(tables)

    def test_indices_exist(self, conn):
        indices = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()}
        assert "idx_patient_patient_cd" in indices
        assert "idx_visit_patient_num" in indices
        assert "idx_observation_concept_cd" in indices
        assert "idx_observation_patient_num" in indices
        assert "idx_observation_start_date" in indices

    def test_concepts_seeded(self, conn):
        rows = conn.execute("SELECT * FROM CONCEPT_DIMENSION").fetchall()
        assert len(rows) == 9
        codes = {r["CONCEPT_CD"] for r in rows}
        assert "TAPPD:FINGER_TAPPING" in codes
        assert "TAPPD:REST_TREMOR" in codes
        assert "TAPPD:TOWER_OF_HANOI" in codes
        assert "TAPPD:TRAIL_MAKING_B" in codes

    def test_concepts_have_correct_categories(self, conn):
        motor = conn.execute(
            "SELECT COUNT(*) FROM CONCEPT_DIMENSION WHERE CATEGORY_CHAR='MOTOR_TEST'"
        ).fetchone()[0]
        cognitive = conn.execute(
            "SELECT COUNT(*) FROM CONCEPT_DIMENSION WHERE CATEGORY_CHAR='COGNITIVE_TEST'"
        ).fetchone()[0]
        assert motor == 5
        assert cognitive == 4

    def test_code_lookup_seeded(self, conn):
        rows = conn.execute("SELECT * FROM CODE_LOOKUP").fetchall()
        assert len(rows) == 8
        codes = {r["CODE_CD"] for r in rows}
        assert "SCTID: 248153007" in codes  # Male
        assert "SCTID: 248152002" in codes  # Female
        assert "O" in codes                  # Outpatient

    def test_seed_is_idempotent(self, conn):
        """Re-seeding should not create duplicates (INSERT OR IGNORE)."""
        from storage.database import _seed_concepts, _seed_code_lookup
        _seed_concepts(conn)
        _seed_concepts(conn)
        _seed_code_lookup(conn)
        assert conn.execute("SELECT COUNT(*) FROM CONCEPT_DIMENSION").fetchone()[0] == 9
        assert conn.execute("SELECT COUNT(*) FROM CODE_LOOKUP").fetchone()[0] == 8

    def test_empty_conn_has_no_seeds(self, empty_conn):
        assert empty_conn.execute("SELECT COUNT(*) FROM CONCEPT_DIMENSION").fetchone()[0] == 0
        assert empty_conn.execute("SELECT COUNT(*) FROM CODE_LOOKUP").fetchone()[0] == 0


# ═══════════════════════════════════════════════════════════════════
# Marshal / Unmarshal Helpers
# ═══════════════════════════════════════════════════════════════════

class TestMarshalHelpers:

    def test_patient_blob_roundtrip(self):
        blob = _marshal_patient_blob("Max", "Mustermann", "some notes")
        result = _unmarshal_patient_blob(blob)
        assert result == {"first_name": "Max", "last_name": "Mustermann", "notes": "some notes"}

    def test_patient_blob_empty(self):
        result = _unmarshal_patient_blob(None)
        assert result == {"first_name": "", "last_name": "", "notes": ""}

    def test_patient_blob_invalid_json(self):
        result = _unmarshal_patient_blob("not json{{{")
        assert result == {"first_name": "", "last_name": "", "notes": ""}

    def test_observation_blob_roundtrip(self):
        features = {"mpi": 0.8, "tap_frequency_hz": 4.5}
        blob = _marshal_observation_blob("right", 10.0, "/path/to/raw.json", features)
        result = _unmarshal_observation_blob(blob)
        assert result["hand"] == "right"
        assert result["duration_s"] == 10.0
        assert result["raw_data_path"] == "/path/to/raw.json"
        assert result["features"]["mpi"] == 0.8

    def test_observation_blob_empty(self):
        result = _unmarshal_observation_blob(None)
        assert result["hand"] == ""
        assert result["features"] == {}

    def test_observation_blob_invalid_json(self):
        result = _unmarshal_observation_blob("{broken")
        assert result["duration_s"] == 0.0

    def test_concept_cd_roundtrip(self):
        assert _test_type_to_concept_cd("finger_tapping") == "TAPPD:FINGER_TAPPING"
        assert _concept_cd_to_test_type("TAPPD:FINGER_TAPPING") == "finger_tapping"

    def test_concept_cd_edge_cases(self):
        assert _concept_cd_to_test_type("") == ""
        assert _concept_cd_to_test_type(None) == ""
        assert _concept_cd_to_test_type("NO_PREFIX") == "NO_PREFIX"

    def test_category_for_test(self):
        assert _category_for_test("finger_tapping") == "MOTOR_TEST"
        assert _category_for_test("hand_open_close") == "MOTOR_TEST"
        assert _category_for_test("rest_tremor") == "MOTOR_TEST"
        assert _category_for_test("tower_of_hanoi") == "COGNITIVE_TEST"
        assert _category_for_test("trail_making_a") == "COGNITIVE_TEST"
        assert _category_for_test("spatial_srt") == "COGNITIVE_TEST"


# ═══════════════════════════════════════════════════════════════════
# Patient CRUD
# ═══════════════════════════════════════════════════════════════════

class TestPatientCRUD:

    def test_create_patient(self, conn):
        p = Patient(patient_code="PD001", first_name="Max", last_name="Mustermann",
                    birth_date="1960-05-15", gender="m", notes="Test")
        p = save_patient(conn, p)
        assert p.id is not None
        assert p.id > 0

    def test_create_patient_stores_in_star_schema(self, conn):
        p = Patient(patient_code="PD001", first_name="Max", last_name="Mustermann",
                    birth_date="1960-05-15", gender="m", notes="Notiz")
        save_patient(conn, p)
        row = conn.execute("SELECT * FROM PATIENT_DIMENSION WHERE PATIENT_CD='PD001'").fetchone()
        assert row is not None
        assert row["SEX_CD"] == "SCTID: 248153007"
        assert row["BIRTH_DATE"] == "1960-05-15"
        blob = json.loads(row["PATIENT_BLOB"])
        assert blob["first_name"] == "Max"
        assert blob["last_name"] == "Mustermann"
        assert blob["notes"] == "Notiz"

    def test_update_patient(self, conn):
        pid = make_patient_row(conn, code="PD001")
        p = get_patient(conn, pid)
        p.first_name = "Moritz"
        p.gender = "m"
        save_patient(conn, p)
        p2 = get_patient(conn, pid)
        assert p2.first_name == "Moritz"

    def test_get_patient_returns_none_for_missing(self, conn):
        assert get_patient(conn, 9999) is None

    def test_find_patients_all(self, conn):
        make_patient_row(conn, code="PD001")
        make_patient_row(conn, code="PD002", first_name="Erika", last_name="Musterfrau")
        patients = find_patients(conn)
        assert len(patients) == 2
        assert patients[0].patient_code == "PD001"

    def test_find_patients_by_code(self, conn):
        make_patient_row(conn, code="PD001")
        make_patient_row(conn, code="PD002")
        patients = find_patients(conn, "PD002")
        assert len(patients) == 1
        assert patients[0].patient_code == "PD002"

    def test_find_patients_by_name_in_blob(self, conn):
        make_patient_row(conn, code="PD001", first_name="Max", last_name="Mustermann")
        make_patient_row(conn, code="PD002", first_name="Erika", last_name="Schmidt")
        patients = find_patients(conn, "Schmidt")
        assert len(patients) == 1
        assert patients[0].last_name == "Schmidt"

    def test_patient_unique_code_constraint(self, conn):
        make_patient_row(conn, code="PD001")
        with pytest.raises(Exception):
            make_patient_row(conn, code="PD001")

    def test_gender_mapping_female(self, conn):
        pid = make_patient_row(conn, code="PD_F", gender="f")
        p = get_patient(conn, pid)
        assert p.gender == "f"
        row = conn.execute("SELECT SEX_CD FROM PATIENT_DIMENSION WHERE PATIENT_NUM=?", (pid,)).fetchone()
        assert row["SEX_CD"] == "SCTID: 248152002"

    def test_gender_mapping_diverse(self, conn):
        pid = make_patient_row(conn, code="PD_D", gender="d")
        p = get_patient(conn, pid)
        assert p.gender == "d"

    def test_gender_mapping_empty(self, conn):
        pid = make_patient_row(conn, code="PD_E", gender="")
        p = get_patient(conn, pid)
        assert p.gender == ""
        row = conn.execute("SELECT SEX_CD FROM PATIENT_DIMENSION WHERE PATIENT_NUM=?", (pid,)).fetchone()
        assert row["SEX_CD"] is None

    def test_patient_display_name(self):
        p = Patient(patient_code="PD001", first_name="Max", last_name="Mustermann")
        assert p.display_name == "PD001 – Mustermann, Max"

    def test_patient_display_name_code_only(self):
        p = Patient(patient_code="PD001")
        assert p.display_name == "PD001"

    def test_patient_age(self):
        from datetime import date
        today = date.today()
        bd = date(today.year - 65, today.month, today.day)
        p = Patient(birth_date=bd.isoformat())
        assert p.age == 65

    def test_patient_age_none_when_empty(self):
        p = Patient(birth_date="")
        assert p.age is None

    def test_patient_empty_birth_date_stored_as_null(self, conn):
        pid = make_patient_row(conn, code="PD_NB", birth_date="")
        row = conn.execute("SELECT BIRTH_DATE FROM PATIENT_DIMENSION WHERE PATIENT_NUM=?", (pid,)).fetchone()
        assert row["BIRTH_DATE"] is None


# ═══════════════════════════════════════════════════════════════════
# Session CRUD
# ═══════════════════════════════════════════════════════════════════

class TestSessionCRUD:

    def test_create_session(self, conn):
        pid = make_patient_row(conn)
        s = create_session(conn, pid)
        assert s.id is not None
        assert s.patient_id == pid
        assert s.started_at != ""

    def test_create_session_stored_in_visit_dimension(self, conn):
        pid = make_patient_row(conn)
        s = create_session(conn, pid)
        row = conn.execute("SELECT * FROM VISIT_DIMENSION WHERE ENCOUNTER_NUM=?", (s.id,)).fetchone()
        assert row is not None
        assert row["PATIENT_NUM"] == pid
        assert row["INOUT_CD"] == "O"

    def test_get_sessions(self, conn):
        pid = make_patient_row(conn)
        create_session(conn, pid)
        create_session(conn, pid)
        sessions = get_sessions(conn, pid)
        assert len(sessions) == 2

    def test_get_sessions_ordered_desc(self, conn):
        pid = make_patient_row(conn)
        s1 = create_session(conn, pid)
        s2 = create_session(conn, pid)
        sessions = get_sessions(conn, pid)
        # Most recent first
        assert sessions[0].id == s2.id

    def test_get_sessions_empty(self, conn):
        pid = make_patient_row(conn)
        assert get_sessions(conn, pid) == []

    def test_delete_session_cascades_measurements(self, conn):
        pid = make_patient_row(conn)
        sid = make_session_row(conn, pid)
        make_measurement_row(conn, pid, session_id=sid)
        make_measurement_row(conn, pid, session_id=sid, hand="left")
        assert len(get_session_measurements(conn, sid)) == 2
        delete_session(conn, sid)
        assert len(get_session_measurements(conn, sid)) == 0
        assert len(get_sessions(conn, pid)) == 0

    def test_delete_session_does_not_affect_other_sessions(self, conn):
        pid = make_patient_row(conn)
        s1 = make_session_row(conn, pid)
        s2 = make_session_row(conn, pid)
        make_measurement_row(conn, pid, session_id=s1)
        make_measurement_row(conn, pid, session_id=s2)
        delete_session(conn, s1)
        assert len(get_session_measurements(conn, s2)) == 1

    def test_session_notes_stored_in_visit_blob(self, conn):
        pid = make_patient_row(conn)
        s = create_session(conn, pid)
        row = conn.execute("SELECT VISIT_BLOB FROM VISIT_DIMENSION WHERE ENCOUNTER_NUM=?", (s.id,)).fetchone()
        blob = json.loads(row["VISIT_BLOB"])
        assert "notes" in blob


# ═══════════════════════════════════════════════════════════════════
# Measurement CRUD
# ═══════════════════════════════════════════════════════════════════

class TestMeasurementCRUD:

    def test_save_measurement(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid, test_type="finger_tapping", hand="right")
        assert mid is not None
        assert mid > 0

    def test_save_measurement_stored_in_observation_fact(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid, test_type="finger_tapping", hand="right")
        row = conn.execute("SELECT * FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)).fetchone()
        assert row["CONCEPT_CD"] == "TAPPD:FINGER_TAPPING"
        assert row["VALTYPE_CD"] == "B"
        assert row["CATEGORY_CHAR"] == "MOTOR_TEST"
        assert row["TVAL_CHAR"] == "right"

    def test_save_measurement_mpi_in_nval(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid, features={"mpi": 0.82, "n_taps": 40})
        row = conn.execute("SELECT NVAL_NUM FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)).fetchone()
        assert row["NVAL_NUM"] == pytest.approx(0.82)

    def test_save_measurement_no_mpi_nval_null(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid, features={"n_taps": 40})
        row = conn.execute("SELECT NVAL_NUM FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)).fetchone()
        assert row["NVAL_NUM"] is None

    def test_observation_blob_structure(self, conn):
        pid = make_patient_row(conn)
        features = {"mpi": 0.75, "tap_frequency_hz": 4.2}
        mid = make_measurement_row(conn, pid, hand="left", duration_s=12.5, features=features)
        row = conn.execute("SELECT OBSERVATION_BLOB FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)).fetchone()
        blob = json.loads(row["OBSERVATION_BLOB"])
        assert blob["hand"] == "left"
        assert blob["duration_s"] == 12.5
        assert blob["features"]["mpi"] == 0.75
        assert blob["features"]["tap_frequency_hz"] == 4.2

    def test_get_measurements(self, conn):
        pid = make_patient_row(conn)
        make_measurement_row(conn, pid, test_type="finger_tapping")
        make_measurement_row(conn, pid, test_type="rest_tremor")
        ms = get_measurements(conn, pid)
        assert len(ms) == 2
        assert all(isinstance(m, Measurement) for m in ms)

    def test_get_measurements_returns_correct_fields(self, conn):
        pid = make_patient_row(conn)
        features = {"mpi": 0.65, "n_taps": 35}
        make_measurement_row(conn, pid, test_type="finger_tapping", hand="right",
                             duration_s=10.0, features=features)
        ms = get_measurements(conn, pid)
        m = ms[0]
        assert m.test_type == "finger_tapping"
        assert m.hand == "right"
        assert m.duration_s == 10.0
        assert m.features["mpi"] == 0.65
        assert m.patient_id == pid

    def test_get_measurements_empty(self, conn):
        pid = make_patient_row(conn)
        assert get_measurements(conn, pid) == []

    def test_delete_measurement(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid)
        delete_measurement(conn, mid)
        assert get_measurements(conn, pid) == []

    def test_delete_measurement_does_not_affect_others(self, conn):
        pid = make_patient_row(conn)
        m1 = make_measurement_row(conn, pid, hand="right")
        m2 = make_measurement_row(conn, pid, hand="left")
        delete_measurement(conn, m1)
        ms = get_measurements(conn, pid)
        assert len(ms) == 1
        assert ms[0].hand == "left"

    def test_get_session_measurements(self, conn):
        pid = make_patient_row(conn)
        sid = make_session_row(conn, pid)
        make_measurement_row(conn, pid, session_id=sid, test_type="finger_tapping")
        make_measurement_row(conn, pid, session_id=sid, test_type="hand_open_close")
        make_measurement_row(conn, pid)  # no session
        ms = get_session_measurements(conn, sid)
        assert len(ms) == 2

    def test_get_last_measurement_dates(self, conn):
        p1 = make_patient_row(conn, code="PD001")
        p2 = make_patient_row(conn, code="PD002")
        make_measurement_row(conn, p1)
        make_measurement_row(conn, p2)
        dates = get_last_measurement_dates(conn)
        assert p1 in dates
        assert p2 in dates

    def test_get_all_measurements(self, conn):
        p1 = make_patient_row(conn, code="PD001")
        p2 = make_patient_row(conn, code="PD002", first_name="Erika")
        make_measurement_row(conn, p1)
        make_measurement_row(conn, p2)
        results = get_all_measurements(conn)
        assert len(results) == 2
        assert all(isinstance(r[0], Patient) and isinstance(r[1], Measurement) for r in results)
        # Check patient data is correctly joined
        codes = {r[0].patient_code for r in results}
        assert codes == {"PD001", "PD002"}

    def test_measurement_with_session(self, conn):
        pid = make_patient_row(conn)
        sid = make_session_row(conn, pid)
        mid = make_measurement_row(conn, pid, session_id=sid)
        m = get_measurements(conn, pid)[0]
        assert m.session_id == sid

    def test_measurement_without_session(self, conn):
        pid = make_patient_row(conn)
        make_measurement_row(conn, pid, session_id=None)
        m = get_measurements(conn, pid)[0]
        assert m.session_id is None

    def test_auto_recorded_at(self, conn):
        pid = make_patient_row(conn)
        m = Measurement(patient_id=pid, test_type="finger_tapping", hand="right", duration_s=10.0)
        m.features = {"mpi": 0.5}
        m = save_measurement(conn, m)
        assert m.recorded_at != ""


# ═══════════════════════════════════════════════════════════════════
# update_raw_data_path
# ═══════════════════════════════════════════════════════════════════

class TestUpdateRawDataPath:

    def test_update_raw_data_path(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid)
        update_raw_data_path(conn, mid, "/new/path/raw.json")
        m = get_measurements(conn, pid)[0]
        assert m.raw_data_path == "/new/path/raw.json"

    def test_update_raw_data_path_preserves_other_blob_fields(self, conn):
        pid = make_patient_row(conn)
        features = {"mpi": 0.9, "n_taps": 50}
        mid = make_measurement_row(conn, pid, hand="left", duration_s=15.0, features=features)
        update_raw_data_path(conn, mid, "/updated.json")
        m = get_measurements(conn, pid)[0]
        assert m.hand == "left"
        assert m.duration_s == 15.0
        assert m.features["mpi"] == 0.9
        assert m.raw_data_path == "/updated.json"

    def test_update_raw_data_path_nonexistent_observation(self, conn):
        # Should not raise, just log warning
        update_raw_data_path(conn, 99999, "/path.json")

    def test_update_raw_data_path_sets_update_date(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid)
        update_raw_data_path(conn, mid, "/path.json")
        row = conn.execute(
            "SELECT UPDATE_DATE FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)
        ).fetchone()
        assert row["UPDATE_DATE"] is not None


# ═══════════════════════════════════════════════════════════════════
# Measurement Dataclass
# ═══════════════════════════════════════════════════════════════════

class TestMeasurementDataclass:

    def test_features_property_getter(self):
        m = Measurement(features_json='{"mpi": 0.5}')
        assert m.features == {"mpi": 0.5}

    def test_features_property_setter(self):
        m = Measurement()
        m.features = {"key": "value"}
        assert json.loads(m.features_json) == {"key": "value"}

    def test_features_default_empty(self):
        m = Measurement()
        assert m.features == {}


# ═══════════════════════════════════════════════════════════════════
# All test types
# ═══════════════════════════════════════════════════════════════════

class TestAllTestTypes:
    """Verify all 9 test types can be stored and retrieved correctly."""

    @pytest.mark.parametrize("test_type,expected_category", [
        ("finger_tapping", "MOTOR_TEST"),
        ("hand_open_close", "MOTOR_TEST"),
        ("pronation_supination", "MOTOR_TEST"),
        ("postural_tremor", "MOTOR_TEST"),
        ("rest_tremor", "MOTOR_TEST"),
        ("tower_of_hanoi", "COGNITIVE_TEST"),
        ("spatial_srt", "COGNITIVE_TEST"),
        ("trail_making_a", "COGNITIVE_TEST"),
        ("trail_making_b", "COGNITIVE_TEST"),
    ])
    def test_store_and_retrieve(self, conn, test_type, expected_category):
        pid = make_patient_row(conn, code=f"P_{test_type[:8]}")
        mid = make_measurement_row(conn, pid, test_type=test_type)
        row = conn.execute(
            "SELECT CONCEPT_CD, CATEGORY_CHAR FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?",
            (mid,),
        ).fetchone()
        assert row["CONCEPT_CD"] == f"TAPPD:{test_type.upper()}"
        assert row["CATEGORY_CHAR"] == expected_category
        # Verify roundtrip through get_measurements
        m = get_measurements(conn, pid)[0]
        assert m.test_type == test_type


# ═══════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_patient_with_special_characters_in_notes(self, conn):
        pid = make_patient_row(conn, code="PD_SPEC", notes='Notes with "quotes" and üöä')
        p = get_patient(conn, pid)
        assert '"quotes"' in p.notes
        assert "üöä" in p.notes

    def test_empty_features(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid, features={})
        m = get_measurements(conn, pid)[0]
        assert m.features == {}

    def test_large_features_dict(self, conn):
        pid = make_patient_row(conn)
        features = {f"feature_{i}": float(i) for i in range(100)}
        mid = make_measurement_row(conn, pid, features=features)
        m = get_measurements(conn, pid)[0]
        assert len(m.features) == 100
        assert m.features["feature_50"] == 50.0

    def test_multiple_patients_isolation(self, conn):
        p1 = make_patient_row(conn, code="PD001")
        p2 = make_patient_row(conn, code="PD002")
        make_measurement_row(conn, p1, test_type="finger_tapping")
        make_measurement_row(conn, p1, test_type="rest_tremor")
        make_measurement_row(conn, p2, test_type="finger_tapping")
        assert len(get_measurements(conn, p1)) == 2
        assert len(get_measurements(conn, p2)) == 1

    def test_foreign_key_enforcement(self, conn):
        """Inserting observation for non-existent patient should fail."""
        m = Measurement(patient_id=9999, test_type="finger_tapping", hand="right", duration_s=10.0)
        m.features = {"mpi": 0.5}
        with pytest.raises(Exception):
            save_measurement(conn, m)

    def test_cascade_delete_patient_deletes_visits_and_observations(self, conn):
        pid = make_patient_row(conn)
        sid = make_session_row(conn, pid)
        make_measurement_row(conn, pid, session_id=sid)
        # Direct delete from PATIENT_DIMENSION
        conn.execute("DELETE FROM PATIENT_DIMENSION WHERE PATIENT_NUM=?", (pid,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM VISIT_DIMENSION WHERE PATIENT_NUM=?", (pid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM OBSERVATION_FACT WHERE PATIENT_NUM=?", (pid,)).fetchone()[0] == 0
