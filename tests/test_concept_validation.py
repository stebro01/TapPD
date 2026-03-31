"""Concept validation tests – verifies that the star schema design is
consistent, the OBSERVATION_BLOB contract holds across all code paths,
and edge cases are handled correctly.

Organised in three layers:
  1. Konzept-Tests:    Does the data model make sense?
  2. Implementierung:  Are the code paths consistent?
  3. Fehler / Härte:   Corruption, edge cases, atomicity
"""

import json
import sqlite3

import pytest

from storage.database import (
    Measurement,
    Patient,
    Session,
    _create_tables,
    _marshal_observation_blob,
    _marshal_patient_blob,
    _seed_code_lookup,
    _seed_concepts,
    _unmarshal_observation_blob,
    _unmarshal_patient_blob,
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
)

from tests.conftest import (
    make_measurement_row,
    make_patient_row,
    make_session_row,
    seed_v1_data,
)


# ═══════════════════════════════════════════════════════════════════
# 1. KONZEPT – Ist das Datenmodell plausibel?
# ═══════════════════════════════════════════════════════════════════

class TestObservationBlobContract:
    """The OBSERVATION_BLOB is the central storage contract.
    Every code path that creates or modifies it must produce the same
    4-field JSON structure: hand, duration_s, raw_data_path, features."""

    REQUIRED_KEYS = {"hand", "duration_s", "raw_data_path", "features"}

    def test_marshal_produces_all_required_keys(self):
        blob = _marshal_observation_blob("right", 10.0, "/path.json", {"mpi": 0.5})
        parsed = json.loads(blob)
        assert set(parsed.keys()) == self.REQUIRED_KEYS

    def test_marshal_produces_no_extra_keys(self):
        blob = _marshal_observation_blob("left", 5.0, "", {"a": 1, "b": 2})
        parsed = json.loads(blob)
        assert set(parsed.keys()) == self.REQUIRED_KEYS

    def test_unmarshal_default_has_all_required_keys(self):
        defaults = _unmarshal_observation_blob(None)
        assert set(defaults.keys()) == self.REQUIRED_KEYS

    def test_save_measurement_blob_has_all_keys(self, conn):
        pid = make_patient_row(conn)
        features = {"mpi": 0.8, "tap_frequency_hz": 4.0}
        mid = make_measurement_row(conn, pid, hand="right", duration_s=10.0, features=features)
        row = conn.execute(
            "SELECT OBSERVATION_BLOB FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)
        ).fetchone()
        parsed = json.loads(row["OBSERVATION_BLOB"])
        assert set(parsed.keys()) == self.REQUIRED_KEYS
        assert parsed["hand"] == "right"
        assert parsed["duration_s"] == 10.0
        assert parsed["features"]["mpi"] == 0.8

    def test_update_raw_data_path_preserves_blob_contract(self, conn):
        """After update_raw_data_path, blob must still have all 4 keys with original values."""
        pid = make_patient_row(conn)
        features = {"mpi": 0.9, "n_taps": 50, "mean_amplitude_mm": 25.0}
        mid = make_measurement_row(conn, pid, hand="left", duration_s=15.0, features=features)
        update_raw_data_path(conn, mid, "/updated/path.json")
        row = conn.execute(
            "SELECT OBSERVATION_BLOB FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)
        ).fetchone()
        parsed = json.loads(row["OBSERVATION_BLOB"])
        assert set(parsed.keys()) == self.REQUIRED_KEYS
        assert parsed["hand"] == "left"
        assert parsed["duration_s"] == 15.0
        assert parsed["raw_data_path"] == "/updated/path.json"
        assert parsed["features"]["mpi"] == 0.9
        assert parsed["features"]["n_taps"] == 50

    def test_migrated_blob_has_all_keys(self, v1_conn):
        """Migration from v1 must produce blobs with the same contract."""
        seed_v1_data(v1_conn)
        _create_tables(v1_conn)
        _seed_concepts(v1_conn)
        # Must migrate patients and sessions first (FK constraints)
        v1_conn.execute("""
            INSERT INTO PATIENT_DIMENSION (PATIENT_NUM, PATIENT_CD, PATIENT_BLOB, SOURCESYSTEM_CD)
            SELECT id, patient_code,
                json_object('first_name', first_name, 'last_name', last_name, 'notes', notes),
                'TAPPD_MIGRATION'
            FROM patients
        """)
        v1_conn.execute("""
            INSERT INTO VISIT_DIMENSION (ENCOUNTER_NUM, PATIENT_NUM, START_DATE, SOURCESYSTEM_CD)
            SELECT id, patient_id, started_at, 'TAPPD_MIGRATION' FROM sessions
        """)
        v1_conn.execute("""
            INSERT INTO OBSERVATION_FACT (OBSERVATION_ID, ENCOUNTER_NUM, PATIENT_NUM,
                CATEGORY_CHAR, CONCEPT_CD, START_DATE, VALTYPE_CD, TVAL_CHAR, NVAL_NUM,
                OBSERVATION_BLOB, SOURCESYSTEM_CD)
            SELECT m.id, m.session_id, m.patient_id,
                CASE WHEN m.test_type IN ('tower_of_hanoi','spatial_srt','trail_making_a','trail_making_b')
                     THEN 'COGNITIVE_TEST' ELSE 'MOTOR_TEST' END,
                'TAPPD:' || UPPER(m.test_type), m.recorded_at, 'B', m.hand,
                CASE WHEN m.features_json IS NULL OR m.features_json = '' THEN NULL
                     ELSE json_extract(m.features_json, '$.mpi') END,
                json_object('hand', m.hand, 'duration_s', m.duration_s,
                    'raw_data_path', COALESCE(m.raw_data_path, ''),
                    'features', CASE WHEN m.features_json IS NULL OR m.features_json = ''
                        THEN json('{}') ELSE json(m.features_json) END),
                'TAPPD_MIGRATION'
            FROM measurements m
        """)
        v1_conn.commit()
        rows = v1_conn.execute("SELECT OBSERVATION_BLOB FROM OBSERVATION_FACT").fetchall()
        for row in rows:
            parsed = json.loads(row["OBSERVATION_BLOB"])
            assert set(parsed.keys()) == self.REQUIRED_KEYS
            assert isinstance(parsed["features"], dict)


class TestDenormalizedFields:
    """TVAL_CHAR (hand) and NVAL_NUM (MPI) are denormalized copies of blob data.
    They must stay consistent with the blob."""

    def test_tval_char_matches_blob_hand(self, conn):
        pid = make_patient_row(conn)
        for hand in ("right", "left", "both"):
            mid = make_measurement_row(conn, pid, hand=hand, test_type="finger_tapping")
            row = conn.execute(
                "SELECT TVAL_CHAR, OBSERVATION_BLOB FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?",
                (mid,),
            ).fetchone()
            blob = json.loads(row["OBSERVATION_BLOB"])
            assert row["TVAL_CHAR"] == blob["hand"] == hand

    def test_nval_num_matches_blob_mpi(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid, features={"mpi": 0.72, "n_taps": 40})
        row = conn.execute(
            "SELECT NVAL_NUM, OBSERVATION_BLOB FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?",
            (mid,),
        ).fetchone()
        blob = json.loads(row["OBSERVATION_BLOB"])
        assert row["NVAL_NUM"] == pytest.approx(blob["features"]["mpi"])

    def test_nval_num_null_when_no_mpi(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid, features={"n_taps": 40})
        row = conn.execute(
            "SELECT NVAL_NUM FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)
        ).fetchone()
        assert row["NVAL_NUM"] is None

    def test_concept_cd_matches_test_type(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid, test_type="pronation_supination")
        row = conn.execute(
            "SELECT CONCEPT_CD FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)
        ).fetchone()
        assert row["CONCEPT_CD"] == "TAPPD:PRONATION_SUPINATION"

    def test_category_char_motor_vs_cognitive(self, conn):
        pid = make_patient_row(conn)
        m_motor = make_measurement_row(conn, pid, test_type="finger_tapping")
        m_cog = make_measurement_row(conn, pid, test_type="tower_of_hanoi")
        r_motor = conn.execute(
            "SELECT CATEGORY_CHAR FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (m_motor,)
        ).fetchone()
        r_cog = conn.execute(
            "SELECT CATEGORY_CHAR FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (m_cog,)
        ).fetchone()
        assert r_motor["CATEGORY_CHAR"] == "MOTOR_TEST"
        assert r_cog["CATEGORY_CHAR"] == "COGNITIVE_TEST"


class TestConceptDimensionIntegrity:
    """CONCEPT_DIMENSION must cover all test types and have consistent metadata."""

    def test_every_test_type_has_concept(self, conn):
        all_types = [
            "finger_tapping", "hand_open_close", "pronation_supination",
            "postural_tremor", "rest_tremor",
            "tower_of_hanoi", "spatial_srt", "trail_making_a", "trail_making_b",
        ]
        for tt in all_types:
            cd = "TAPPD:" + tt.upper()
            row = conn.execute(
                "SELECT * FROM CONCEPT_DIMENSION WHERE CONCEPT_CD=?", (cd,)
            ).fetchone()
            assert row is not None, f"Missing concept for {tt}"
            assert row["VALTYPE_CD"] == "B"
            assert row["CATEGORY_CHAR"] in ("MOTOR_TEST", "COGNITIVE_TEST")

    def test_concept_paths_follow_convention(self, conn):
        rows = conn.execute("SELECT CONCEPT_PATH FROM CONCEPT_DIMENSION").fetchall()
        for row in rows:
            path = row["CONCEPT_PATH"]
            assert path.startswith("/TapPD/")
            assert path.endswith("/")

    def test_concept_fk_enforced(self, conn):
        """Inserting observation with unknown concept_cd should fail."""
        pid = make_patient_row(conn)
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO OBSERVATION_FACT (PATIENT_NUM, CONCEPT_CD, VALTYPE_CD) "
                "VALUES (?, 'UNKNOWN:CONCEPT', 'B')",
                (pid,),
            )
            conn.commit()


class TestStarSchemaRelationships:
    """Verify dimensional relationships: patient → visit → observation."""

    def test_observation_references_valid_visit(self, conn):
        pid = make_patient_row(conn)
        sid = make_session_row(conn, pid)
        mid = make_measurement_row(conn, pid, session_id=sid)
        row = conn.execute(
            "SELECT v.ENCOUNTER_NUM FROM OBSERVATION_FACT o "
            "JOIN VISIT_DIMENSION v ON o.ENCOUNTER_NUM = v.ENCOUNTER_NUM "
            "WHERE o.OBSERVATION_ID=?", (mid,)
        ).fetchone()
        assert row is not None
        assert row["ENCOUNTER_NUM"] == sid

    def test_observation_without_visit_allowed(self, conn):
        """Orphan observations (no session) must be supported."""
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid, session_id=None)
        row = conn.execute(
            "SELECT ENCOUNTER_NUM FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)
        ).fetchone()
        assert row["ENCOUNTER_NUM"] is None

    def test_visit_references_valid_patient(self, conn):
        pid = make_patient_row(conn)
        sid = make_session_row(conn, pid)
        row = conn.execute(
            "SELECT p.PATIENT_CD FROM VISIT_DIMENSION v "
            "JOIN PATIENT_DIMENSION p ON v.PATIENT_NUM = p.PATIENT_NUM "
            "WHERE v.ENCOUNTER_NUM=?", (sid,)
        ).fetchone()
        assert row is not None

    def test_cascade_patient_removes_all_descendants(self, conn):
        pid = make_patient_row(conn)
        sid = make_session_row(conn, pid)
        make_measurement_row(conn, pid, session_id=sid)
        make_measurement_row(conn, pid, session_id=sid)
        make_measurement_row(conn, pid)  # orphan

        conn.execute("DELETE FROM PATIENT_DIMENSION WHERE PATIENT_NUM=?", (pid,))
        conn.commit()

        assert conn.execute("SELECT COUNT(*) FROM VISIT_DIMENSION").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM OBSERVATION_FACT").fetchone()[0] == 0


# ═══════════════════════════════════════════════════════════════════
# 2. IMPLEMENTIERUNG – Sind die Code-Pfade konsistent?
# ═══════════════════════════════════════════════════════════════════

class TestRoundtripConsistency:
    """Data written via save_*() must be identical when read back via get_*()."""

    def test_patient_roundtrip_all_fields(self, conn):
        p_in = Patient(patient_code="RT001", first_name="Max", last_name="Muster",
                       birth_date="1960-05-15", gender="m", notes="Testnotiz mit Sönderzeichen")
        p_in = save_patient(conn, p_in)
        p_out = get_patient(conn, p_in.id)
        assert p_out.patient_code == p_in.patient_code
        assert p_out.first_name == p_in.first_name
        assert p_out.last_name == p_in.last_name
        assert p_out.birth_date == p_in.birth_date
        assert p_out.gender == p_in.gender
        assert p_out.notes == p_in.notes

    def test_measurement_roundtrip_all_fields(self, conn):
        pid = make_patient_row(conn)
        sid = make_session_row(conn, pid)
        features = {"mpi": 0.88, "tap_frequency_hz": 5.1, "n_taps": 51,
                     "amplitude_decrement": -0.02, "intertap_variability_cv": 0.08}
        m_in = Measurement(patient_id=pid, session_id=sid, test_type="finger_tapping",
                           hand="right", duration_s=10.0)
        m_in.features = features
        m_in = save_measurement(conn, m_in)

        ms = get_measurements(conn, pid)
        m_out = ms[0]
        assert m_out.id == m_in.id
        assert m_out.patient_id == pid
        assert m_out.session_id == sid
        assert m_out.test_type == "finger_tapping"
        assert m_out.hand == "right"
        assert m_out.duration_s == 10.0
        assert m_out.features["mpi"] == pytest.approx(0.88)
        assert m_out.features["tap_frequency_hz"] == pytest.approx(5.1)
        assert m_out.features["n_taps"] == 51
        assert m_out.recorded_at == m_in.recorded_at

    def test_measurement_via_get_all_matches_get_single(self, conn):
        """get_all_measurements() must return same Measurement data as get_measurements()."""
        pid = make_patient_row(conn, code="RT002")
        features = {"mpi": 0.7, "n_taps": 35}
        make_measurement_row(conn, pid, features=features)

        ms_single = get_measurements(conn, pid)
        ms_all = get_all_measurements(conn)
        m_s = ms_single[0]
        m_a = ms_all[0][1]

        assert m_s.id == m_a.id
        assert m_s.test_type == m_a.test_type
        assert m_s.hand == m_a.hand
        assert m_s.duration_s == m_a.duration_s
        assert m_s.features == m_a.features
        assert m_s.raw_data_path == m_a.raw_data_path

    def test_get_all_measurements_patient_data_correct(self, conn):
        """Patient data in get_all_measurements JOIN must be accurate."""
        pid = make_patient_row(conn, code="RT003", first_name="Erika", last_name="Schmidt",
                               gender="f", birth_date="1980-01-01")
        make_measurement_row(conn, pid)
        results = get_all_measurements(conn)
        p = results[0][0]
        assert p.patient_code == "RT003"
        assert p.first_name == "Erika"
        assert p.last_name == "Schmidt"
        assert p.gender == "f"
        assert p.birth_date == "1980-01-01"

    def test_session_roundtrip(self, conn):
        pid = make_patient_row(conn)
        s_in = create_session(conn, pid)
        sessions = get_sessions(conn, pid)
        s_out = sessions[0]
        assert s_out.id == s_in.id
        assert s_out.patient_id == pid
        assert s_out.started_at == s_in.started_at


class TestRawDataPathWorkflow:
    """The two-step save workflow: save_measurement (no path) → update_raw_data_path."""

    def test_initial_save_has_empty_raw_data_path(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid)
        m = get_measurements(conn, pid)[0]
        assert m.raw_data_path == ""

    def test_update_sets_raw_data_path(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid)
        update_raw_data_path(conn, mid, "data/samples/test.json")
        m = get_measurements(conn, pid)[0]
        assert m.raw_data_path == "data/samples/test.json"

    def test_update_is_idempotent(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid)
        update_raw_data_path(conn, mid, "/path1.json")
        update_raw_data_path(conn, mid, "/path2.json")
        m = get_measurements(conn, pid)[0]
        assert m.raw_data_path == "/path2.json"

    def test_update_does_not_change_features(self, conn):
        pid = make_patient_row(conn)
        features = {"mpi": 0.77, "tap_frequency_hz": 4.5, "n_taps": 45}
        mid = make_measurement_row(conn, pid, features=features)
        update_raw_data_path(conn, mid, "/raw.json")

        m = get_measurements(conn, pid)[0]
        assert m.features["mpi"] == pytest.approx(0.77)
        assert m.features["tap_frequency_hz"] == pytest.approx(4.5)
        assert m.features["n_taps"] == 45

    def test_update_does_not_change_hand_or_duration(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid, hand="left", duration_s=12.5)
        update_raw_data_path(conn, mid, "/raw.json")

        m = get_measurements(conn, pid)[0]
        assert m.hand == "left"
        assert m.duration_s == 12.5

    def test_multiple_measurements_independent_raw_paths(self, conn):
        pid = make_patient_row(conn)
        m1 = make_measurement_row(conn, pid, hand="right")
        m2 = make_measurement_row(conn, pid, hand="left")
        update_raw_data_path(conn, m1, "/right.json")
        update_raw_data_path(conn, m2, "/left.json")

        ms = get_measurements(conn, pid)
        paths = {m.hand: m.raw_data_path for m in ms}
        assert paths["right"] == "/right.json"
        assert paths["left"] == "/left.json"


class TestQueryFiltering:
    """Verify that denormalized fields (TVAL_CHAR, NVAL_NUM, CONCEPT_CD)
    can be used for SQL filtering without blob parsing."""

    def test_filter_by_hand_via_tval(self, conn):
        pid = make_patient_row(conn)
        make_measurement_row(conn, pid, hand="right")
        make_measurement_row(conn, pid, hand="left")
        make_measurement_row(conn, pid, hand="both")

        rows = conn.execute(
            "SELECT COUNT(*) FROM OBSERVATION_FACT WHERE PATIENT_NUM=? AND TVAL_CHAR='right'",
            (pid,),
        ).fetchone()
        assert rows[0] == 1

    def test_filter_by_mpi_via_nval(self, conn):
        pid = make_patient_row(conn)
        make_measurement_row(conn, pid, features={"mpi": 0.9})
        make_measurement_row(conn, pid, features={"mpi": 0.5})
        make_measurement_row(conn, pid, features={"mpi": 0.3})

        rows = conn.execute(
            "SELECT COUNT(*) FROM OBSERVATION_FACT WHERE PATIENT_NUM=? AND NVAL_NUM >= 0.5",
            (pid,),
        ).fetchone()
        assert rows[0] == 2

    def test_filter_by_concept_cd(self, conn):
        pid = make_patient_row(conn)
        make_measurement_row(conn, pid, test_type="finger_tapping")
        make_measurement_row(conn, pid, test_type="rest_tremor")
        make_measurement_row(conn, pid, test_type="finger_tapping")

        rows = conn.execute(
            "SELECT COUNT(*) FROM OBSERVATION_FACT "
            "WHERE PATIENT_NUM=? AND CONCEPT_CD='TAPPD:FINGER_TAPPING'",
            (pid,),
        ).fetchone()
        assert rows[0] == 2

    def test_filter_by_category(self, conn):
        pid = make_patient_row(conn)
        make_measurement_row(conn, pid, test_type="finger_tapping")
        make_measurement_row(conn, pid, test_type="tower_of_hanoi")
        make_measurement_row(conn, pid, test_type="rest_tremor")

        motor = conn.execute(
            "SELECT COUNT(*) FROM OBSERVATION_FACT WHERE PATIENT_NUM=? AND CATEGORY_CHAR='MOTOR_TEST'",
            (pid,),
        ).fetchone()[0]
        cognitive = conn.execute(
            "SELECT COUNT(*) FROM OBSERVATION_FACT WHERE PATIENT_NUM=? AND CATEGORY_CHAR='COGNITIVE_TEST'",
            (pid,),
        ).fetchone()[0]
        assert motor == 2
        assert cognitive == 1


# ═══════════════════════════════════════════════════════════════════
# 3. FEHLER / HÄRTE – Corruption, Edge Cases, Robustheit
# ═══════════════════════════════════════════════════════════════════

class TestBlobCorruptionSafety:
    """update_raw_data_path must NOT destroy data when blob is corrupted."""

    def test_corrupted_blob_not_overwritten(self, conn):
        """If OBSERVATION_BLOB is malformed JSON, update_raw_data_path must refuse to write."""
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid, features={"mpi": 0.8, "n_taps": 40})

        # Manually corrupt the blob
        conn.execute(
            "UPDATE OBSERVATION_FACT SET OBSERVATION_BLOB='{{not valid json' WHERE OBSERVATION_ID=?",
            (mid,),
        )
        conn.commit()

        # This must NOT overwrite with defaults
        update_raw_data_path(conn, mid, "/new_path.json")

        # Verify blob is still the corrupted string (not overwritten with defaults)
        row = conn.execute(
            "SELECT OBSERVATION_BLOB FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)
        ).fetchone()
        assert row["OBSERVATION_BLOB"] == "{{not valid json"

    def test_null_blob_gets_path_added(self, conn):
        """NULL blob should get a valid structure with the new path."""
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid)
        conn.execute(
            "UPDATE OBSERVATION_FACT SET OBSERVATION_BLOB=NULL WHERE OBSERVATION_ID=?", (mid,)
        )
        conn.commit()

        update_raw_data_path(conn, mid, "/path.json")
        row = conn.execute(
            "SELECT OBSERVATION_BLOB FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)
        ).fetchone()
        blob = json.loads(row["OBSERVATION_BLOB"])
        assert blob["raw_data_path"] == "/path.json"

    def test_empty_string_blob_gets_path_added(self, conn):
        """Empty string blob should get a valid structure."""
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid)
        conn.execute(
            "UPDATE OBSERVATION_FACT SET OBSERVATION_BLOB='' WHERE OBSERVATION_ID=?", (mid,)
        )
        conn.commit()

        update_raw_data_path(conn, mid, "/path.json")
        row = conn.execute(
            "SELECT OBSERVATION_BLOB FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)
        ).fetchone()
        blob = json.loads(row["OBSERVATION_BLOB"])
        assert blob["raw_data_path"] == "/path.json"

    def test_valid_blob_features_survive_update(self, conn):
        """The most important safety check: existing features must never be lost."""
        pid = make_patient_row(conn)
        complex_features = {
            "mpi": 0.85,
            "tap_frequency_hz": 5.0,
            "mean_amplitude_mm": 28.5,
            "amplitude_decrement": -0.015,
            "intertap_variability_cv": 0.09,
            "mean_velocity_mm_s": 200.0,
            "n_taps": 50,
        }
        mid = make_measurement_row(conn, pid, hand="right", duration_s=10.0, features=complex_features)

        update_raw_data_path(conn, mid, "/data/samples/test.json")

        row = conn.execute(
            "SELECT OBSERVATION_BLOB FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)
        ).fetchone()
        blob = json.loads(row["OBSERVATION_BLOB"])

        # ALL original fields must be intact
        assert blob["hand"] == "right"
        assert blob["duration_s"] == 10.0
        assert blob["raw_data_path"] == "/data/samples/test.json"
        for key, expected in complex_features.items():
            assert blob["features"][key] == pytest.approx(expected), f"Feature {key} lost or changed"


class TestMigrationEdgeCasesExtended:
    """Edge cases specific to the v1→v2 migration that could cause data corruption."""

    def test_empty_string_features_json(self, v1_conn):
        """features_json='' (not '{}') must result in empty dict, not None."""
        v1_conn.execute("INSERT INTO patients (patient_code) VALUES ('PD_EMPTY')")
        pid = v1_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        v1_conn.execute(
            "INSERT INTO measurements (patient_id, test_type, hand, duration_s, features_json) "
            "VALUES (?, 'finger_tapping', 'right', 10.0, '')",
            (pid,),
        )
        v1_conn.commit()

        _create_tables(v1_conn)
        _seed_concepts(v1_conn)
        v1_conn.execute("""
            INSERT INTO PATIENT_DIMENSION (PATIENT_NUM, PATIENT_CD, PATIENT_BLOB, SOURCESYSTEM_CD)
            SELECT id, patient_code,
                json_object('first_name', first_name, 'last_name', last_name, 'notes', notes),
                'TAPPD_MIGRATION'
            FROM patients
        """)
        v1_conn.execute("""
            INSERT INTO OBSERVATION_FACT (OBSERVATION_ID, PATIENT_NUM, CATEGORY_CHAR,
                CONCEPT_CD, START_DATE, VALTYPE_CD, TVAL_CHAR, NVAL_NUM, OBSERVATION_BLOB, SOURCESYSTEM_CD)
            SELECT m.id, m.patient_id, 'MOTOR_TEST',
                'TAPPD:' || UPPER(m.test_type), m.recorded_at, 'B', m.hand,
                CASE WHEN m.features_json IS NULL OR m.features_json = '' THEN NULL
                     ELSE json_extract(m.features_json, '$.mpi') END,
                json_object('hand', m.hand, 'duration_s', m.duration_s,
                    'raw_data_path', COALESCE(m.raw_data_path, ''),
                    'features', CASE WHEN m.features_json IS NULL OR m.features_json = ''
                        THEN json('{}') ELSE json(m.features_json) END),
                'TAPPD_MIGRATION'
            FROM measurements m
        """)
        v1_conn.commit()

        row = v1_conn.execute("SELECT OBSERVATION_BLOB, NVAL_NUM FROM OBSERVATION_FACT").fetchone()
        blob = json.loads(row["OBSERVATION_BLOB"])
        assert blob["features"] == {}  # Must be empty dict, NOT None
        assert isinstance(blob["features"], dict)
        assert row["NVAL_NUM"] is None

    def test_default_features_json_empty_object(self, v1_conn):
        """features_json='{}' (the v1 default) must result in empty dict in blob."""
        v1_conn.execute("INSERT INTO patients (patient_code) VALUES ('PD_DEFAULT')")
        pid = v1_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        v1_conn.execute(
            "INSERT INTO measurements (patient_id, test_type, hand, duration_s, features_json) "
            "VALUES (?, 'finger_tapping', 'right', 10.0, '{}')",
            (pid,),
        )
        v1_conn.commit()

        _create_tables(v1_conn)
        _seed_concepts(v1_conn)
        v1_conn.execute("""
            INSERT INTO PATIENT_DIMENSION (PATIENT_NUM, PATIENT_CD, PATIENT_BLOB, SOURCESYSTEM_CD)
            SELECT id, patient_code,
                json_object('first_name', first_name, 'last_name', last_name, 'notes', notes),
                'TAPPD_MIGRATION'
            FROM patients
        """)
        v1_conn.execute("""
            INSERT INTO OBSERVATION_FACT (OBSERVATION_ID, PATIENT_NUM, CATEGORY_CHAR,
                CONCEPT_CD, START_DATE, VALTYPE_CD, TVAL_CHAR, NVAL_NUM, OBSERVATION_BLOB, SOURCESYSTEM_CD)
            SELECT m.id, m.patient_id, 'MOTOR_TEST',
                'TAPPD:' || UPPER(m.test_type), m.recorded_at, 'B', m.hand,
                CASE WHEN m.features_json IS NULL OR m.features_json = '' THEN NULL
                     ELSE json_extract(m.features_json, '$.mpi') END,
                json_object('hand', m.hand, 'duration_s', m.duration_s,
                    'raw_data_path', COALESCE(m.raw_data_path, ''),
                    'features', CASE WHEN m.features_json IS NULL OR m.features_json = ''
                        THEN json('{}') ELSE json(m.features_json) END),
                'TAPPD_MIGRATION'
            FROM measurements m
        """)
        v1_conn.commit()

        row = v1_conn.execute("SELECT OBSERVATION_BLOB, NVAL_NUM FROM OBSERVATION_FACT").fetchone()
        blob = json.loads(row["OBSERVATION_BLOB"])
        assert blob["features"] == {}
        assert isinstance(blob["features"], dict)
        assert row["NVAL_NUM"] is None

    def test_null_raw_data_path_in_v1(self, v1_conn):
        """raw_data_path=NULL must become '' in blob."""
        v1_conn.execute("INSERT INTO patients (patient_code) VALUES ('PD_NRP')")
        pid = v1_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        v1_conn.execute(
            "INSERT INTO measurements (patient_id, test_type, hand, duration_s, features_json, raw_data_path) "
            "VALUES (?, 'finger_tapping', 'right', 10.0, '{}', NULL)",
            (pid,),
        )
        v1_conn.commit()

        _create_tables(v1_conn)
        _seed_concepts(v1_conn)
        v1_conn.execute("""
            INSERT INTO PATIENT_DIMENSION (PATIENT_NUM, PATIENT_CD, PATIENT_BLOB, SOURCESYSTEM_CD)
            SELECT id, patient_code,
                json_object('first_name', first_name, 'last_name', last_name, 'notes', notes),
                'TAPPD_MIGRATION'
            FROM patients
        """)
        v1_conn.execute("""
            INSERT INTO OBSERVATION_FACT (OBSERVATION_ID, PATIENT_NUM, CATEGORY_CHAR,
                CONCEPT_CD, START_DATE, VALTYPE_CD, TVAL_CHAR, OBSERVATION_BLOB, SOURCESYSTEM_CD)
            SELECT m.id, m.patient_id, 'MOTOR_TEST',
                'TAPPD:' || UPPER(m.test_type), m.recorded_at, 'B', m.hand,
                json_object('hand', m.hand, 'duration_s', m.duration_s,
                    'raw_data_path', COALESCE(m.raw_data_path, ''),
                    'features', CASE WHEN m.features_json IS NULL OR m.features_json = ''
                        THEN json('{}') ELSE json(m.features_json) END),
                'TAPPD_MIGRATION'
            FROM measurements m
        """)
        v1_conn.commit()

        row = v1_conn.execute("SELECT OBSERVATION_BLOB FROM OBSERVATION_FACT").fetchone()
        blob = json.loads(row["OBSERVATION_BLOB"])
        assert blob["raw_data_path"] == ""

    def test_special_characters_in_v1_notes(self, v1_conn):
        """Unicode and quotes in v1 notes must survive migration into PATIENT_BLOB."""
        notes = 'Tremor "rechts" stärker als links — Kälte-Trigger? 日本語テスト'
        v1_conn.execute(
            "INSERT INTO patients (patient_code, first_name, last_name, notes) "
            "VALUES ('PD_SPEC', 'Ünü', 'Östermann', ?)",
            (notes,),
        )
        v1_conn.commit()

        _create_tables(v1_conn)
        v1_conn.execute("""
            INSERT INTO PATIENT_DIMENSION (PATIENT_NUM, PATIENT_CD, PATIENT_BLOB, SOURCESYSTEM_CD)
            SELECT id, patient_code,
                json_object('first_name', first_name, 'last_name', last_name, 'notes', notes),
                'TAPPD_MIGRATION'
            FROM patients
        """)
        v1_conn.commit()

        row = v1_conn.execute("SELECT PATIENT_BLOB FROM PATIENT_DIMENSION").fetchone()
        blob = json.loads(row["PATIENT_BLOB"])
        assert blob["first_name"] == "Ünü"
        assert blob["last_name"] == "Östermann"
        assert '"rechts"' in blob["notes"]
        assert "日本語テスト" in blob["notes"]


class TestColumnCollisionSafety:
    """get_all_measurements JOIN must not confuse columns between tables."""

    def test_patient_created_at_not_overwritten_by_observation(self, conn):
        """Patient.created_at in get_all_measurements must come from PATIENT_DIMENSION."""
        import time
        pid = make_patient_row(conn, code="COL001")

        # Get the patient's actual created_at
        p_row = conn.execute(
            "SELECT CREATED_AT FROM PATIENT_DIMENSION WHERE PATIENT_NUM=?", (pid,)
        ).fetchone()
        patient_created_at = p_row["CREATED_AT"]

        time.sleep(0.01)  # Ensure different timestamp
        make_measurement_row(conn, pid)

        results = get_all_measurements(conn)
        p = results[0][0]
        assert p.created_at == patient_created_at

    def test_get_all_measurements_with_multiple_patients(self, conn):
        """Multiple patients should each get their own created_at."""
        p1 = make_patient_row(conn, code="COL_A", first_name="Anna")
        p2 = make_patient_row(conn, code="COL_B", first_name="Bert")
        make_measurement_row(conn, p1)
        make_measurement_row(conn, p2)

        results = get_all_measurements(conn)
        names = {r[0].first_name for r in results}
        assert names == {"Anna", "Bert"}
        # Each patient's data is distinct
        for p, m in results:
            assert m.patient_id == p.id


class TestAtomicityAndConsistency:
    """Verify that operations are atomic and consistent."""

    def test_failed_measurement_save_does_not_pollute_db(self, conn):
        """If save_measurement raises, no partial data should remain."""
        initial_count = conn.execute("SELECT COUNT(*) FROM OBSERVATION_FACT").fetchone()[0]
        m = Measurement(patient_id=99999, test_type="finger_tapping",
                        hand="right", duration_s=10.0)
        m.features = {"mpi": 0.5}
        with pytest.raises(Exception):
            save_measurement(conn, m)
        final_count = conn.execute("SELECT COUNT(*) FROM OBSERVATION_FACT").fetchone()[0]
        assert final_count == initial_count

    def test_delete_measurement_is_permanent(self, conn):
        pid = make_patient_row(conn)
        mid = make_measurement_row(conn, pid)
        delete_measurement(conn, mid)
        row = conn.execute(
            "SELECT COUNT(*) FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (mid,)
        ).fetchone()
        assert row[0] == 0

    def test_concurrent_measurement_ids_unique(self, conn):
        """Multiple inserts must get unique IDs."""
        pid = make_patient_row(conn)
        ids = set()
        for i in range(20):
            mid = make_measurement_row(conn, pid, hand="right" if i % 2 == 0 else "left")
            ids.add(mid)
        assert len(ids) == 20

    def test_patient_update_does_not_affect_measurements(self, conn):
        """Updating patient data must not alter existing measurements."""
        pid = make_patient_row(conn, code="ATOM01", first_name="Old")
        features = {"mpi": 0.7}
        mid = make_measurement_row(conn, pid, features=features)

        # Update patient
        p = get_patient(conn, pid)
        p.first_name = "New"
        save_patient(conn, p)

        # Measurement must be unchanged
        m = get_measurements(conn, pid)[0]
        assert m.features["mpi"] == pytest.approx(0.7)

    def test_delete_session_does_not_touch_orphan_measurements(self, conn):
        """Deleting a session must not affect measurements without session."""
        pid = make_patient_row(conn)
        sid = make_session_row(conn, pid)
        m_session = make_measurement_row(conn, pid, session_id=sid)
        m_orphan = make_measurement_row(conn, pid, session_id=None)

        delete_session(conn, sid)

        ms = get_measurements(conn, pid)
        assert len(ms) == 1
        assert ms[0].id == m_orphan
