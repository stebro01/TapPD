"""Shared fixtures for TapPD database tests."""

import json
import sqlite3

import pytest

from storage.database import (
    _create_tables,
    _seed_code_lookup,
    _seed_concepts,
)


# ── In-memory database fixtures ────────────────────────────────────

@pytest.fixture()
def conn():
    """Fresh in-memory SQLite connection with star schema + seed data."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    _create_tables(c)
    _seed_concepts(c)
    _seed_code_lookup(c)
    yield c
    c.close()


@pytest.fixture()
def empty_conn():
    """Fresh in-memory SQLite connection with star schema, NO seed data."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    _create_tables(c)
    yield c
    c.close()


@pytest.fixture()
def v1_conn():
    """In-memory SQLite connection with the OLD v1 schema (patients/sessions/measurements).

    Used for migration tests.
    """
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript("""
        CREATE TABLE patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_code TEXT UNIQUE NOT NULL,
            first_name TEXT DEFAULT '',
            last_name TEXT DEFAULT '',
            birth_date TEXT DEFAULT '',
            gender TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL REFERENCES patients(id),
            started_at TEXT DEFAULT (datetime('now')),
            notes TEXT DEFAULT ''
        );
        CREATE INDEX idx_sessions_patient ON sessions(patient_id);
        CREATE TABLE measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL REFERENCES patients(id),
            session_id INTEGER REFERENCES sessions(id),
            test_type TEXT NOT NULL,
            hand TEXT NOT NULL,
            duration_s REAL NOT NULL,
            features_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT DEFAULT (datetime('now')),
            raw_data_path TEXT DEFAULT ''
        );
        CREATE INDEX idx_measurements_patient ON measurements(patient_id);
    """)
    yield c
    c.close()


# ── Sample data factories ──────────────────────────────────────────

def make_patient_row(conn: sqlite3.Connection, *,
                     code: str = "PD001",
                     first_name: str = "Max",
                     last_name: str = "Mustermann",
                     birth_date: str = "1960-05-15",
                     gender: str = "m",
                     notes: str = "") -> int:
    """Insert a patient via the star schema and return PATIENT_NUM."""
    from storage.database import Patient, save_patient
    p = Patient(patient_code=code, first_name=first_name, last_name=last_name,
                birth_date=birth_date, gender=gender, notes=notes)
    p = save_patient(conn, p)
    return p.id


def make_session_row(conn: sqlite3.Connection, patient_id: int, **kwargs) -> int:
    """Insert a session/visit and return ENCOUNTER_NUM."""
    from storage.database import create_session
    s = create_session(conn, patient_id)
    return s.id


def make_measurement_row(conn: sqlite3.Connection, patient_id: int, *,
                         session_id: int | None = None,
                         test_type: str = "finger_tapping",
                         hand: str = "right",
                         duration_s: float = 10.0,
                         features: dict | None = None) -> int:
    """Insert a measurement/observation and return OBSERVATION_ID."""
    from storage.database import Measurement, save_measurement
    m = Measurement(
        patient_id=patient_id,
        session_id=session_id,
        test_type=test_type,
        hand=hand,
        duration_s=duration_s,
    )
    if features is None:
        features = {
            "mpi": 0.75,
            "tap_frequency_hz": 4.2,
            "mean_amplitude_mm": 30.0,
            "amplitude_decrement": -0.01,
            "intertap_variability_cv": 0.1,
            "n_taps": 42,
        }
    m.features = features
    m = save_measurement(conn, m)
    return m.id


def seed_v1_data(conn: sqlite3.Connection) -> dict:
    """Insert sample data into v1 schema. Returns dict of inserted IDs."""
    cur = conn.execute(
        "INSERT INTO patients (patient_code, first_name, last_name, birth_date, gender, notes) "
        "VALUES ('PD001', 'Max', 'Mustermann', '1960-05-15', 'm', 'Erstpatient')"
    )
    p1 = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO patients (patient_code, first_name, last_name, birth_date, gender, notes) "
        "VALUES ('PD002', 'Erika', 'Musterfrau', '1975-11-20', 'f', '')"
    )
    p2 = cur.lastrowid

    cur = conn.execute(
        "INSERT INTO sessions (patient_id, started_at, notes) VALUES (?, '2026-03-01T09:00:00', 'Erste Sitzung')",
        (p1,),
    )
    s1 = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO sessions (patient_id, started_at, notes) VALUES (?, '2026-03-15T10:00:00', '')",
        (p1,),
    )
    s2 = cur.lastrowid

    features_ft = json.dumps({"mpi": 0.75, "tap_frequency_hz": 4.2, "n_taps": 42})
    features_tremor = json.dumps({"mpi": 0.60, "dominant_frequency_hz": 5.1})
    features_hanoi = json.dumps({"total_moves": 15, "optimal_moves": 7})

    cur = conn.execute(
        "INSERT INTO measurements (patient_id, session_id, test_type, hand, duration_s, features_json, recorded_at, raw_data_path) "
        "VALUES (?, ?, 'finger_tapping', 'right', 10.0, ?, '2026-03-01T09:05:00', 'data/samples/PD001_ft.json')",
        (p1, s1, features_ft),
    )
    m1 = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO measurements (patient_id, session_id, test_type, hand, duration_s, features_json, recorded_at, raw_data_path) "
        "VALUES (?, ?, 'rest_tremor', 'both', 15.0, ?, '2026-03-01T09:10:00', '')",
        (p1, s1, features_tremor),
    )
    m2 = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO measurements (patient_id, session_id, test_type, hand, duration_s, features_json, recorded_at) "
        "VALUES (?, ?, 'tower_of_hanoi', 'both', 120.0, ?, '2026-03-15T10:05:00')",
        (p1, s2, features_hanoi),
    )
    m3 = cur.lastrowid
    # Measurement without session (orphan)
    cur = conn.execute(
        "INSERT INTO measurements (patient_id, test_type, hand, duration_s, features_json, recorded_at) "
        "VALUES (?, 'finger_tapping', 'left', 10.0, ?, '2026-02-15T08:00:00')",
        (p2, features_ft),
    )
    m4 = cur.lastrowid

    conn.commit()
    return {
        "patients": [p1, p2],
        "sessions": [s1, s2],
        "measurements": [m1, m2, m3, m4],
    }
