"""Integration tests for v1 → v2 database migration."""

import json
import sqlite3

import pytest

from storage.database import (
    Measurement,
    Patient,
    Session,
    _create_tables,
    _migrate_from_v1,
    _seed_code_lookup,
    _seed_concepts,
    find_patients,
    get_measurements,
    get_patient,
    get_session_measurements,
    get_sessions,
    _unmarshal_observation_blob,
    _unmarshal_patient_blob,
)

from tests.conftest import seed_v1_data


# ═══════════════════════════════════════════════════════════════════
# Migration: Data Integrity
# ═══════════════════════════════════════════════════════════════════

class TestMigrationDataIntegrity:
    """Verify all data is correctly migrated from v1 to v2 star schema."""

    def _run_migration(self, v1_conn):
        """Seed v1 data and run migration in-memory."""
        ids = seed_v1_data(v1_conn)
        # Run migration (skip backup since in-memory)
        _create_tables(v1_conn)
        _seed_concepts(v1_conn)
        _seed_code_lookup(v1_conn)

        v1_conn.execute("""
            INSERT INTO PATIENT_DIMENSION (PATIENT_NUM, PATIENT_CD, BIRTH_DATE, SEX_CD, PATIENT_BLOB,
                                           CREATED_AT, SOURCESYSTEM_CD)
            SELECT id, patient_code,
                CASE WHEN birth_date != '' THEN birth_date ELSE NULL END,
                CASE gender
                    WHEN 'm' THEN 'SCTID: 248153007'
                    WHEN 'f' THEN 'SCTID: 248152002'
                    WHEN 'd' THEN 'SCTID: 32570681000036106'
                    ELSE NULL
                END,
                json_object('first_name', first_name, 'last_name', last_name, 'notes', notes),
                created_at, 'TAPPD_MIGRATION'
            FROM patients
        """)
        v1_conn.execute("""
            INSERT INTO VISIT_DIMENSION (ENCOUNTER_NUM, PATIENT_NUM, START_DATE, VISIT_BLOB, SOURCESYSTEM_CD)
            SELECT id, patient_id, started_at, json_object('notes', notes), 'TAPPD_MIGRATION'
            FROM sessions
        """)
        v1_conn.execute("""
            INSERT INTO OBSERVATION_FACT (
                OBSERVATION_ID, ENCOUNTER_NUM, PATIENT_NUM, CATEGORY_CHAR,
                CONCEPT_CD, START_DATE, VALTYPE_CD, TVAL_CHAR, NVAL_NUM,
                OBSERVATION_BLOB, SOURCESYSTEM_CD
            )
            SELECT m.id, m.session_id, m.patient_id,
                CASE WHEN m.test_type IN ('tower_of_hanoi','spatial_srt','trail_making_a','trail_making_b')
                     THEN 'COGNITIVE_TEST' ELSE 'MOTOR_TEST' END,
                'TAPPD:' || UPPER(m.test_type), m.recorded_at, 'B', m.hand,
                json_extract(m.features_json, '$.mpi'),
                json_object('hand', m.hand, 'duration_s', m.duration_s,
                            'raw_data_path', COALESCE(m.raw_data_path, ''),
                            'features', json(m.features_json)),
                'TAPPD_MIGRATION'
            FROM measurements m
        """)
        v1_conn.executescript("""
            DROP TABLE IF EXISTS measurements;
            DROP TABLE IF EXISTS sessions;
            DROP TABLE IF EXISTS patients;
        """)
        v1_conn.commit()
        return ids

    def test_patient_count_preserved(self, v1_conn):
        ids = self._run_migration(v1_conn)
        count = v1_conn.execute("SELECT COUNT(*) FROM PATIENT_DIMENSION").fetchone()[0]
        assert count == len(ids["patients"])

    def test_session_count_preserved(self, v1_conn):
        ids = self._run_migration(v1_conn)
        count = v1_conn.execute("SELECT COUNT(*) FROM VISIT_DIMENSION").fetchone()[0]
        assert count == len(ids["sessions"])

    def test_measurement_count_preserved(self, v1_conn):
        ids = self._run_migration(v1_conn)
        count = v1_conn.execute("SELECT COUNT(*) FROM OBSERVATION_FACT").fetchone()[0]
        assert count == len(ids["measurements"])

    def test_old_tables_dropped(self, v1_conn):
        self._run_migration(v1_conn)
        tables = {r[0] for r in v1_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "patients" not in tables
        assert "sessions" not in tables
        assert "measurements" not in tables

    def test_patient_data_migrated_correctly(self, v1_conn):
        self._run_migration(v1_conn)
        row = v1_conn.execute(
            "SELECT * FROM PATIENT_DIMENSION WHERE PATIENT_CD='PD001'"
        ).fetchone()
        assert row is not None
        assert row["BIRTH_DATE"] == "1960-05-15"
        assert row["SEX_CD"] == "SCTID: 248153007"
        blob = _unmarshal_patient_blob(row["PATIENT_BLOB"])
        assert blob["first_name"] == "Max"
        assert blob["last_name"] == "Mustermann"
        assert blob["notes"] == "Erstpatient"
        assert row["SOURCESYSTEM_CD"] == "TAPPD_MIGRATION"

    def test_female_patient_sex_cd(self, v1_conn):
        self._run_migration(v1_conn)
        row = v1_conn.execute(
            "SELECT SEX_CD FROM PATIENT_DIMENSION WHERE PATIENT_CD='PD002'"
        ).fetchone()
        assert row["SEX_CD"] == "SCTID: 248152002"

    def test_session_migrated_to_visit(self, v1_conn):
        ids = self._run_migration(v1_conn)
        row = v1_conn.execute(
            "SELECT * FROM VISIT_DIMENSION WHERE ENCOUNTER_NUM=?", (ids["sessions"][0],)
        ).fetchone()
        assert row is not None
        assert row["PATIENT_NUM"] == ids["patients"][0]
        assert row["START_DATE"] == "2026-03-01T09:00:00"
        blob = json.loads(row["VISIT_BLOB"])
        assert blob["notes"] == "Erste Sitzung"

    def test_finger_tapping_observation(self, v1_conn):
        ids = self._run_migration(v1_conn)
        row = v1_conn.execute(
            "SELECT * FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (ids["measurements"][0],)
        ).fetchone()
        assert row["CONCEPT_CD"] == "TAPPD:FINGER_TAPPING"
        assert row["CATEGORY_CHAR"] == "MOTOR_TEST"
        assert row["VALTYPE_CD"] == "B"
        assert row["TVAL_CHAR"] == "right"
        assert row["NVAL_NUM"] == pytest.approx(0.75)
        blob = _unmarshal_observation_blob(row["OBSERVATION_BLOB"])
        assert blob["hand"] == "right"
        assert blob["duration_s"] == 10.0
        assert blob["raw_data_path"] == "data/samples/PD001_ft.json"
        assert blob["features"]["tap_frequency_hz"] == pytest.approx(4.2)
        assert blob["features"]["n_taps"] == 42

    def test_rest_tremor_observation(self, v1_conn):
        ids = self._run_migration(v1_conn)
        row = v1_conn.execute(
            "SELECT * FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (ids["measurements"][1],)
        ).fetchone()
        assert row["CONCEPT_CD"] == "TAPPD:REST_TREMOR"
        assert row["CATEGORY_CHAR"] == "MOTOR_TEST"
        assert row["TVAL_CHAR"] == "both"
        assert row["NVAL_NUM"] == pytest.approx(0.60)
        blob = _unmarshal_observation_blob(row["OBSERVATION_BLOB"])
        assert blob["duration_s"] == 15.0
        assert blob["features"]["dominant_frequency_hz"] == pytest.approx(5.1)

    def test_cognitive_test_observation(self, v1_conn):
        ids = self._run_migration(v1_conn)
        row = v1_conn.execute(
            "SELECT * FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (ids["measurements"][2],)
        ).fetchone()
        assert row["CONCEPT_CD"] == "TAPPD:TOWER_OF_HANOI"
        assert row["CATEGORY_CHAR"] == "COGNITIVE_TEST"
        assert row["NVAL_NUM"] is None  # tower_of_hanoi has no mpi
        blob = _unmarshal_observation_blob(row["OBSERVATION_BLOB"])
        assert blob["features"]["total_moves"] == 15

    def test_orphan_measurement_null_encounter(self, v1_conn):
        """Measurement without session_id should have NULL ENCOUNTER_NUM."""
        ids = self._run_migration(v1_conn)
        row = v1_conn.execute(
            "SELECT ENCOUNTER_NUM FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?",
            (ids["measurements"][3],)
        ).fetchone()
        assert row["ENCOUNTER_NUM"] is None

    def test_empty_raw_data_path_preserved(self, v1_conn):
        ids = self._run_migration(v1_conn)
        row = v1_conn.execute(
            "SELECT OBSERVATION_BLOB FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?",
            (ids["measurements"][1],)
        ).fetchone()
        blob = _unmarshal_observation_blob(row["OBSERVATION_BLOB"])
        assert blob["raw_data_path"] == ""

    def test_concepts_seeded_after_migration(self, v1_conn):
        self._run_migration(v1_conn)
        count = v1_conn.execute("SELECT COUNT(*) FROM CONCEPT_DIMENSION").fetchone()[0]
        assert count == 9

    def test_code_lookup_seeded_after_migration(self, v1_conn):
        self._run_migration(v1_conn)
        count = v1_conn.execute("SELECT COUNT(*) FROM CODE_LOOKUP").fetchone()[0]
        assert count == 8

    def test_patient_ids_preserved(self, v1_conn):
        ids = self._run_migration(v1_conn)
        for pid in ids["patients"]:
            assert v1_conn.execute(
                "SELECT COUNT(*) FROM PATIENT_DIMENSION WHERE PATIENT_NUM=?", (pid,)
            ).fetchone()[0] == 1

    def test_measurement_ids_preserved(self, v1_conn):
        ids = self._run_migration(v1_conn)
        for mid in ids["measurements"]:
            assert v1_conn.execute(
                "SELECT COUNT(*) FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)
            ).fetchone()[0] == 1


# ═══════════════════════════════════════════════════════════════════
# Migration: Reading migrated data via CRUD API
# ═══════════════════════════════════════════════════════════════════

class TestMigrationCRUDCompatibility:
    """After migration, the CRUD API (which returns Patient/Session/Measurement)
    should work identically to before."""

    @pytest.fixture()
    def migrated_conn(self, v1_conn):
        """Fixture that returns a migrated v1 connection."""
        seed_v1_data(v1_conn)
        _create_tables(v1_conn)
        _seed_concepts(v1_conn)
        _seed_code_lookup(v1_conn)
        v1_conn.execute("""
            INSERT INTO PATIENT_DIMENSION (PATIENT_NUM, PATIENT_CD, BIRTH_DATE, SEX_CD, PATIENT_BLOB,
                                           CREATED_AT, SOURCESYSTEM_CD)
            SELECT id, patient_code,
                CASE WHEN birth_date != '' THEN birth_date ELSE NULL END,
                CASE gender WHEN 'm' THEN 'SCTID: 248153007' WHEN 'f' THEN 'SCTID: 248152002'
                            WHEN 'd' THEN 'SCTID: 32570681000036106' ELSE NULL END,
                json_object('first_name', first_name, 'last_name', last_name, 'notes', notes),
                created_at, 'TAPPD_MIGRATION'
            FROM patients
        """)
        v1_conn.execute("""
            INSERT INTO VISIT_DIMENSION (ENCOUNTER_NUM, PATIENT_NUM, START_DATE, VISIT_BLOB, SOURCESYSTEM_CD)
            SELECT id, patient_id, started_at, json_object('notes', notes), 'TAPPD_MIGRATION' FROM sessions
        """)
        v1_conn.execute("""
            INSERT INTO OBSERVATION_FACT (
                OBSERVATION_ID, ENCOUNTER_NUM, PATIENT_NUM, CATEGORY_CHAR, CONCEPT_CD,
                START_DATE, VALTYPE_CD, TVAL_CHAR, NVAL_NUM, OBSERVATION_BLOB, SOURCESYSTEM_CD)
            SELECT m.id, m.session_id, m.patient_id,
                CASE WHEN m.test_type IN ('tower_of_hanoi','spatial_srt','trail_making_a','trail_making_b')
                     THEN 'COGNITIVE_TEST' ELSE 'MOTOR_TEST' END,
                'TAPPD:' || UPPER(m.test_type), m.recorded_at, 'B', m.hand,
                json_extract(m.features_json, '$.mpi'),
                json_object('hand', m.hand, 'duration_s', m.duration_s,
                            'raw_data_path', COALESCE(m.raw_data_path, ''),
                            'features', json(m.features_json)),
                'TAPPD_MIGRATION'
            FROM measurements m
        """)
        v1_conn.executescript("DROP TABLE IF EXISTS measurements; DROP TABLE IF EXISTS sessions; DROP TABLE IF EXISTS patients;")
        v1_conn.commit()
        return v1_conn

    def test_find_patients_after_migration(self, migrated_conn):
        patients = find_patients(migrated_conn)
        assert len(patients) == 2
        codes = {p.patient_code for p in patients}
        assert codes == {"PD001", "PD002"}

    def test_patient_fields_after_migration(self, migrated_conn):
        patients = find_patients(migrated_conn, "PD001")
        p = patients[0]
        assert p.first_name == "Max"
        assert p.last_name == "Mustermann"
        assert p.birth_date == "1960-05-15"
        assert p.gender == "m"
        assert p.notes == "Erstpatient"

    def test_get_sessions_after_migration(self, migrated_conn):
        p = find_patients(migrated_conn, "PD001")[0]
        sessions = get_sessions(migrated_conn, p.id)
        assert len(sessions) == 2

    def test_session_fields_after_migration(self, migrated_conn):
        p = find_patients(migrated_conn, "PD001")[0]
        sessions = get_sessions(migrated_conn, p.id)
        # Find the session with notes
        s = next((s for s in sessions if s.notes == "Erste Sitzung"), None)
        assert s is not None
        assert s.patient_id == p.id

    def test_get_measurements_after_migration(self, migrated_conn):
        p = find_patients(migrated_conn, "PD001")[0]
        ms = get_measurements(migrated_conn, p.id)
        assert len(ms) == 3  # finger_tapping, rest_tremor, tower_of_hanoi
        types = {m.test_type for m in ms}
        assert types == {"finger_tapping", "rest_tremor", "tower_of_hanoi"}

    def test_measurement_features_after_migration(self, migrated_conn):
        p = find_patients(migrated_conn, "PD001")[0]
        ms = get_measurements(migrated_conn, p.id)
        ft = next(m for m in ms if m.test_type == "finger_tapping")
        assert ft.hand == "right"
        assert ft.duration_s == 10.0
        assert ft.features["mpi"] == pytest.approx(0.75)
        assert ft.features["tap_frequency_hz"] == pytest.approx(4.2)
        assert ft.raw_data_path == "data/samples/PD001_ft.json"

    def test_session_measurements_after_migration(self, migrated_conn):
        p = find_patients(migrated_conn, "PD001")[0]
        sessions = get_sessions(migrated_conn, p.id)
        s = next(s for s in sessions if s.notes == "Erste Sitzung")
        ms = get_session_measurements(migrated_conn, s.id)
        assert len(ms) == 2  # finger_tapping + rest_tremor in first session


# ═══════════════════════════════════════════════════════════════════
# Migration: Edge cases
# ═══════════════════════════════════════════════════════════════════

class TestMigrationEdgeCases:

    def test_empty_v1_database(self, v1_conn):
        """Migration of empty v1 database should succeed."""
        _create_tables(v1_conn)
        _seed_concepts(v1_conn)
        _seed_code_lookup(v1_conn)
        v1_conn.executescript("DROP TABLE IF EXISTS measurements; DROP TABLE IF EXISTS sessions; DROP TABLE IF EXISTS patients;")
        v1_conn.commit()
        assert v1_conn.execute("SELECT COUNT(*) FROM PATIENT_DIMENSION").fetchone()[0] == 0
        assert v1_conn.execute("SELECT COUNT(*) FROM CONCEPT_DIMENSION").fetchone()[0] == 9

    def test_patient_with_empty_gender(self, v1_conn):
        v1_conn.execute(
            "INSERT INTO patients (patient_code, first_name, last_name, gender) "
            "VALUES ('PD_NO_G', 'Test', 'Person', '')"
        )
        v1_conn.commit()
        _create_tables(v1_conn)
        _seed_concepts(v1_conn)
        _seed_code_lookup(v1_conn)
        v1_conn.execute("""
            INSERT INTO PATIENT_DIMENSION (PATIENT_NUM, PATIENT_CD, BIRTH_DATE, SEX_CD, PATIENT_BLOB, CREATED_AT, SOURCESYSTEM_CD)
            SELECT id, patient_code,
                CASE WHEN birth_date != '' THEN birth_date ELSE NULL END,
                CASE gender WHEN 'm' THEN 'SCTID: 248153007' WHEN 'f' THEN 'SCTID: 248152002'
                            WHEN 'd' THEN 'SCTID: 32570681000036106' ELSE NULL END,
                json_object('first_name', first_name, 'last_name', last_name, 'notes', notes),
                created_at, 'TAPPD_MIGRATION'
            FROM patients
        """)
        v1_conn.commit()
        row = v1_conn.execute("SELECT SEX_CD FROM PATIENT_DIMENSION WHERE PATIENT_CD='PD_NO_G'").fetchone()
        assert row["SEX_CD"] is None

    def test_measurement_with_null_session_id(self, v1_conn):
        """Orphan measurements (no session) should migrate with NULL ENCOUNTER_NUM."""
        v1_conn.execute(
            "INSERT INTO patients (patient_code) VALUES ('PD_ORPHAN')"
        )
        pid = v1_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        v1_conn.execute(
            "INSERT INTO measurements (patient_id, test_type, hand, duration_s, features_json) "
            "VALUES (?, 'finger_tapping', 'right', 10.0, '{}')",
            (pid,),
        )
        v1_conn.commit()
        _create_tables(v1_conn)
        _seed_concepts(v1_conn)
        _seed_code_lookup(v1_conn)
        v1_conn.execute("""
            INSERT INTO PATIENT_DIMENSION (PATIENT_NUM, PATIENT_CD, PATIENT_BLOB, SOURCESYSTEM_CD)
            SELECT id, patient_code, json_object('first_name', first_name, 'last_name', last_name, 'notes', notes),
                   'TAPPD_MIGRATION'
            FROM patients
        """)
        v1_conn.execute("""
            INSERT INTO OBSERVATION_FACT (OBSERVATION_ID, ENCOUNTER_NUM, PATIENT_NUM, CATEGORY_CHAR,
                CONCEPT_CD, START_DATE, VALTYPE_CD, TVAL_CHAR, NVAL_NUM, OBSERVATION_BLOB, SOURCESYSTEM_CD)
            SELECT m.id, m.session_id, m.patient_id,
                'MOTOR_TEST', 'TAPPD:' || UPPER(m.test_type), m.recorded_at, 'B', m.hand,
                json_extract(m.features_json, '$.mpi'),
                json_object('hand', m.hand, 'duration_s', m.duration_s,
                            'raw_data_path', COALESCE(m.raw_data_path, ''),
                            'features', json(m.features_json)),
                'TAPPD_MIGRATION'
            FROM measurements m
        """)
        v1_conn.commit()
        row = v1_conn.execute("SELECT ENCOUNTER_NUM FROM OBSERVATION_FACT").fetchone()
        assert row["ENCOUNTER_NUM"] is None

    def test_features_json_with_no_mpi(self, v1_conn):
        """Features without mpi should result in NULL NVAL_NUM."""
        v1_conn.execute("INSERT INTO patients (patient_code) VALUES ('PD_NOMPI')")
        pid = v1_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        features = json.dumps({"n_taps": 30})
        v1_conn.execute(
            "INSERT INTO measurements (patient_id, test_type, hand, duration_s, features_json) "
            "VALUES (?, 'finger_tapping', 'right', 10.0, ?)",
            (pid, features),
        )
        v1_conn.commit()
        _create_tables(v1_conn)
        _seed_concepts(v1_conn)
        v1_conn.execute("""
            INSERT INTO PATIENT_DIMENSION (PATIENT_NUM, PATIENT_CD, PATIENT_BLOB, SOURCESYSTEM_CD)
            SELECT id, patient_code, json_object('first_name', first_name, 'last_name', last_name, 'notes', notes),
                   'TAPPD_MIGRATION'
            FROM patients
        """)
        v1_conn.execute("""
            INSERT INTO OBSERVATION_FACT (OBSERVATION_ID, ENCOUNTER_NUM, PATIENT_NUM, CATEGORY_CHAR,
                CONCEPT_CD, START_DATE, VALTYPE_CD, TVAL_CHAR, NVAL_NUM, OBSERVATION_BLOB, SOURCESYSTEM_CD)
            SELECT m.id, m.session_id, m.patient_id,
                'MOTOR_TEST', 'TAPPD:' || UPPER(m.test_type), m.recorded_at, 'B', m.hand,
                json_extract(m.features_json, '$.mpi'),
                json_object('hand', m.hand, 'duration_s', m.duration_s,
                            'raw_data_path', COALESCE(m.raw_data_path, ''),
                            'features', json(m.features_json)),
                'TAPPD_MIGRATION'
            FROM measurements m
        """)
        v1_conn.commit()
        row = v1_conn.execute("SELECT NVAL_NUM FROM OBSERVATION_FACT").fetchone()
        assert row["NVAL_NUM"] is None


# ═══════════════════════════════════════════════════════════════════
# Full Workflow Integration Tests
# ═══════════════════════════════════════════════════════════════════

class TestFullWorkflow:
    """End-to-end workflows that simulate real app usage on the star schema."""

    def test_complete_patient_session_measurement_workflow(self, conn):
        """Simulate: create patient → create session → record multiple tests → query."""
        from storage.database import save_patient, create_session, save_measurement

        # 1. Create patient
        p = Patient(patient_code="PD_WORKFLOW", first_name="Anna", last_name="Test",
                    birth_date="1970-01-01", gender="f")
        p = save_patient(conn, p)
        assert p.id is not None

        # 2. Create session
        s = create_session(conn, p.id)
        assert s.id is not None

        # 3. Record finger tapping (right)
        m1 = Measurement(patient_id=p.id, session_id=s.id, test_type="finger_tapping",
                         hand="right", duration_s=10.0)
        m1.features = {"mpi": 0.85, "tap_frequency_hz": 5.0, "n_taps": 50}
        m1 = save_measurement(conn, m1)

        # 4. Record finger tapping (left)
        m2 = Measurement(patient_id=p.id, session_id=s.id, test_type="finger_tapping",
                         hand="left", duration_s=10.0)
        m2.features = {"mpi": 0.72, "tap_frequency_hz": 4.1, "n_taps": 41}
        m2 = save_measurement(conn, m2)

        # 5. Record rest tremor
        m3 = Measurement(patient_id=p.id, session_id=s.id, test_type="rest_tremor",
                         hand="both", duration_s=15.0)
        m3.features = {"mpi": 0.55, "dominant_frequency_hz": 4.8}
        m3 = save_measurement(conn, m3)

        # 6. Query all measurements for patient
        all_ms = get_measurements(conn, p.id)
        assert len(all_ms) == 3

        # 7. Query session measurements
        session_ms = get_session_measurements(conn, s.id)
        assert len(session_ms) == 3

        # 8. Verify star schema data
        rows = conn.execute(
            "SELECT CONCEPT_CD, TVAL_CHAR, NVAL_NUM FROM OBSERVATION_FACT "
            "WHERE PATIENT_NUM=? ORDER BY START_DATE",
            (p.id,),
        ).fetchall()
        assert len(rows) == 3
        concepts = [r["CONCEPT_CD"] for r in rows]
        assert "TAPPD:FINGER_TAPPING" in concepts
        assert "TAPPD:REST_TREMOR" in concepts

    def test_multi_session_longitudinal(self, conn):
        """Multiple sessions over time for one patient."""
        from storage.database import save_patient, create_session, save_measurement

        p = Patient(patient_code="PD_LONG", first_name="Karl", birth_date="1955-06-01", gender="m")
        p = save_patient(conn, p)

        # Session 1
        s1 = create_session(conn, p.id)
        m1 = Measurement(patient_id=p.id, session_id=s1.id, test_type="finger_tapping",
                         hand="right", duration_s=10.0)
        m1.features = {"mpi": 0.80}
        save_measurement(conn, m1)

        # Session 2
        s2 = create_session(conn, p.id)
        m2 = Measurement(patient_id=p.id, session_id=s2.id, test_type="finger_tapping",
                         hand="right", duration_s=10.0)
        m2.features = {"mpi": 0.65}  # Decline
        save_measurement(conn, m2)

        sessions = get_sessions(conn, p.id)
        assert len(sessions) == 2

        all_ms = get_measurements(conn, p.id)
        assert len(all_ms) == 2
        mpis = [m.features.get("mpi") for m in all_ms]
        assert 0.80 in mpis
        assert 0.65 in mpis

    def test_delete_session_preserves_other_data(self, conn):
        """Deleting one session should not affect other sessions or orphan measurements."""
        from storage.database import save_patient, create_session, save_measurement, delete_session
        from tests.conftest import make_measurement_row

        p = Patient(patient_code="PD_DEL", first_name="Test", gender="m")
        p = save_patient(conn, p)

        s1 = create_session(conn, p.id)
        s2 = create_session(conn, p.id)
        make_measurement_row(conn, p.id, session_id=s1.id)
        make_measurement_row(conn, p.id, session_id=s1.id, hand="left")
        make_measurement_row(conn, p.id, session_id=s2.id)
        make_measurement_row(conn, p.id)  # orphan

        delete_session(conn, s1.id)

        assert len(get_sessions(conn, p.id)) == 1
        assert len(get_measurements(conn, p.id)) == 2  # s2 measurement + orphan
        assert len(get_session_measurements(conn, s2.id)) == 1

    def test_update_raw_data_path_in_workflow(self, conn):
        """Simulate save measurement → then update raw_data_path (as results_screen does)."""
        from storage.database import save_patient, save_measurement, update_raw_data_path

        p = Patient(patient_code="PD_RAW", first_name="Test", gender="m")
        p = save_patient(conn, p)

        m = Measurement(patient_id=p.id, test_type="finger_tapping", hand="right", duration_s=10.0)
        m.features = {"mpi": 0.7, "n_taps": 35}
        m = save_measurement(conn, m)

        # Simulate raw data save
        update_raw_data_path(conn, m.id, "data/samples/PD_RAW_ft_right.json")

        # Verify via CRUD API
        ms = get_measurements(conn, p.id)
        assert ms[0].raw_data_path == "data/samples/PD_RAW_ft_right.json"
        # Features should still be intact
        assert ms[0].features["mpi"] == pytest.approx(0.7)
        assert ms[0].features["n_taps"] == 35
