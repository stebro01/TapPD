"""SQLite database for patients, sessions, and measurements."""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "tappd.db"


@dataclass
class Patient:
    id: int | None = None
    patient_code: str = ""
    first_name: str = ""
    last_name: str = ""
    birth_date: str = ""  # ISO format YYYY-MM-DD
    gender: str = ""  # "m", "f", "d", ""
    notes: str = ""
    created_at: str = ""

    @property
    def display_name(self) -> str:
        parts = []
        if self.last_name:
            parts.append(self.last_name)
        if self.first_name:
            parts.append(self.first_name)
        name = ", ".join(parts) if parts else self.patient_code
        return f"{self.patient_code} – {name}" if parts else self.patient_code

    @property
    def age(self) -> int | None:
        if not self.birth_date:
            return None
        try:
            bd = date.fromisoformat(self.birth_date)
            today = date.today()
            return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        except ValueError:
            return None


@dataclass
class Session:
    id: int | None = None
    patient_id: int = 0
    started_at: str = ""
    notes: str = ""


@dataclass
class Measurement:
    id: int | None = None
    patient_id: int = 0
    session_id: int | None = None
    test_type: str = ""
    hand: str = ""  # "left", "right", "both"
    duration_s: float = 0.0
    features_json: str = "{}"
    recorded_at: str = ""
    raw_data_path: str = ""

    @property
    def features(self) -> dict:
        return json.loads(self.features_json)

    @features.setter
    def features(self, val: dict) -> None:
        self.features_json = json.dumps(val, default=str)


def get_db() -> sqlite3.Connection:
    """Get a database connection, creating tables if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _create_tables(conn)
    log.debug("Datenbankverbindung geoeffnet: %s", DB_PATH)
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_code TEXT UNIQUE NOT NULL,
            first_name TEXT DEFAULT '',
            last_name TEXT DEFAULT '',
            birth_date TEXT DEFAULT '',
            gender TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL REFERENCES patients(id),
            started_at TEXT DEFAULT (datetime('now')),
            notes TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_patient
            ON sessions(patient_id);
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL REFERENCES patients(id),
            test_type TEXT NOT NULL,
            hand TEXT NOT NULL,
            duration_s REAL NOT NULL,
            features_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_measurements_patient
            ON measurements(patient_id);
    """)
    # Migrations
    m_cols = [r[1] for r in conn.execute("PRAGMA table_info(measurements)").fetchall()]
    if "raw_data_path" not in m_cols:
        conn.execute("ALTER TABLE measurements ADD COLUMN raw_data_path TEXT DEFAULT ''")
        conn.commit()
    if "session_id" not in m_cols:
        conn.execute("ALTER TABLE measurements ADD COLUMN session_id INTEGER REFERENCES sessions(id)")
        conn.commit()


# ── Patient CRUD ────────────────────────────────────────────────────

def save_patient(conn: sqlite3.Connection, patient: Patient) -> Patient:
    if patient.id is not None:
        conn.execute(
            "UPDATE patients SET patient_code=?, first_name=?, last_name=?, "
            "birth_date=?, gender=?, notes=? WHERE id=?",
            (patient.patient_code, patient.first_name, patient.last_name,
             patient.birth_date, patient.gender, patient.notes, patient.id),
        )
        log.info("Patient aktualisiert: %s (ID %d)", patient.patient_code, patient.id)
    else:
        cur = conn.execute(
            "INSERT INTO patients (patient_code, first_name, last_name, birth_date, gender, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (patient.patient_code, patient.first_name, patient.last_name,
             patient.birth_date, patient.gender, patient.notes),
        )
        patient.id = cur.lastrowid
        log.info("Neuer Patient angelegt: %s (ID %d)", patient.patient_code, patient.id)
    conn.commit()
    return patient


def find_patients(conn: sqlite3.Connection, query: str = "") -> list[Patient]:
    if query:
        rows = conn.execute(
            "SELECT * FROM patients WHERE patient_code LIKE ? OR last_name LIKE ? "
            "OR first_name LIKE ? ORDER BY patient_code",
            (f"%{query}%", f"%{query}%", f"%{query}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM patients ORDER BY patient_code").fetchall()
    return [Patient(**dict(r)) for r in rows]


def get_patient(conn: sqlite3.Connection, patient_id: int) -> Patient | None:
    row = conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
    return Patient(**dict(row)) if row else None


# ── Session CRUD ───────────────────────────────────────────────────

def create_session(conn: sqlite3.Connection, patient_id: int) -> Session:
    s = Session(patient_id=patient_id, started_at=datetime.now().isoformat())
    cur = conn.execute(
        "INSERT INTO sessions (patient_id, started_at) VALUES (?, ?)",
        (s.patient_id, s.started_at),
    )
    s.id = cur.lastrowid
    conn.commit()
    log.info("Neue Session erstellt: ID %d fuer Patient %d", s.id, patient_id)
    return s


def get_sessions(conn: sqlite3.Connection, patient_id: int) -> list[Session]:
    rows = conn.execute(
        "SELECT * FROM sessions WHERE patient_id=? ORDER BY started_at DESC",
        (patient_id,),
    ).fetchall()
    return [Session(**dict(r)) for r in rows]


def delete_session(conn: sqlite3.Connection, session_id: int) -> None:
    """Delete a session and all its measurements."""
    conn.execute("DELETE FROM measurements WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    conn.commit()
    log.info("Session geloescht: ID %d (inkl. zugehoeriger Messungen)", session_id)


def get_session_measurements(conn: sqlite3.Connection, session_id: int) -> list[Measurement]:
    rows = conn.execute(
        "SELECT * FROM measurements WHERE session_id=? ORDER BY recorded_at",
        (session_id,),
    ).fetchall()
    return [_row_to_measurement(r) for r in rows]


# ── Measurement CRUD ────────────────────────────────────────────────

def save_measurement(conn: sqlite3.Connection, m: Measurement) -> Measurement:
    if not m.recorded_at:
        m.recorded_at = datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO measurements "
        "(patient_id, session_id, test_type, hand, duration_s, features_json, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (m.patient_id, m.session_id, m.test_type, m.hand,
         m.duration_s, m.features_json, m.recorded_at),
    )
    m.id = cur.lastrowid
    conn.commit()
    log.info("Messung gespeichert: %s %s (ID %d, Patient %d, %.1fs)",
             m.test_type, m.hand, m.id, m.patient_id, m.duration_s)
    return m


def get_measurements(conn: sqlite3.Connection, patient_id: int) -> list[Measurement]:
    rows = conn.execute(
        "SELECT * FROM measurements WHERE patient_id=? ORDER BY recorded_at DESC",
        (patient_id,),
    ).fetchall()
    return [_row_to_measurement(r) for r in rows]


def delete_measurement(conn: sqlite3.Connection, measurement_id: int) -> None:
    conn.execute("DELETE FROM measurements WHERE id=?", (measurement_id,))
    conn.commit()
    log.info("Messung geloescht: ID %d", measurement_id)


def get_last_measurement_dates(conn: sqlite3.Connection) -> dict[int, str]:
    """Return {patient_id: last_recorded_at} for all patients with measurements."""
    rows = conn.execute(
        "SELECT patient_id, MAX(recorded_at) as last_date "
        "FROM measurements GROUP BY patient_id"
    ).fetchall()
    return {r["patient_id"]: r["last_date"] for r in rows}


def get_all_measurements(conn: sqlite3.Connection) -> list[tuple[Patient, Measurement]]:
    rows = conn.execute(
        "SELECT m.*, p.patient_code, p.first_name, p.last_name, p.birth_date, p.gender, p.notes, p.created_at "
        "FROM measurements m JOIN patients p ON m.patient_id = p.id "
        "ORDER BY m.recorded_at DESC"
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        p = Patient(
            id=d["patient_id"], patient_code=d["patient_code"],
            first_name=d["first_name"], last_name=d["last_name"],
            birth_date=d["birth_date"], gender=d["gender"],
            notes=d["notes"], created_at=d["created_at"],
        )
        m = _row_to_measurement(r)
        results.append((p, m))
    return results


def _row_to_measurement(r) -> Measurement:
    d = dict(r)
    return Measurement(
        id=d["id"], patient_id=d["patient_id"],
        session_id=d.get("session_id"),
        test_type=d["test_type"], hand=d["hand"],
        duration_s=d["duration_s"], features_json=d["features_json"],
        recorded_at=d["recorded_at"],
        raw_data_path=d.get("raw_data_path", ""),
    )
