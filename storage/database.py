"""SQLite database for TapPD – i2b2-inspired star schema.

Tables: PATIENT_DIMENSION, VISIT_DIMENSION, OBSERVATION_FACT,
        CONCEPT_DIMENSION, CODE_LOOKUP, NOTE_FACT

Dataclass API (Patient, Session, Measurement) remains unchanged
so that UI code needs no modifications.
"""

import json
import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "tappd.db"

# ── Code lookup cache (loaded from CODE_LOOKUP table on first use) ─
_code_cache: dict[str, dict] | None = None  # {CODE_CD: {TABLE_CD, COLUMN_CD, NAME_CHAR, LOOKUP_BLOB}}


def _get_code_cache(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return cached CODE_LOOKUP entries, loading from DB on first call."""
    global _code_cache
    if _code_cache is None:
        _code_cache = {}
        for r in conn.execute("SELECT * FROM CODE_LOOKUP").fetchall():
            d = dict(r)
            blob_str = d.get("LOOKUP_BLOB")
            blob = {}
            if blob_str:
                try:
                    blob = json.loads(blob_str)
                except (json.JSONDecodeError, TypeError):
                    pass
            _code_cache[d["CODE_CD"]] = {**d, "_blob": blob}
    return _code_cache


def _invalidate_code_cache() -> None:
    global _code_cache
    _code_cache = None


def _gender_to_snomed(conn: sqlite3.Connection, gender: str) -> str | None:
    """Resolve app gender code ('m','f','d') → SNOMED CODE_CD via CODE_LOOKUP."""
    if not gender:
        return None
    cache = _get_code_cache(conn)
    for code_cd, entry in cache.items():
        if entry.get("COLUMN_CD") == "SEX_CD" and entry.get("_blob", {}).get("app_code") == gender:
            return code_cd
    return None


def _snomed_to_gender(conn: sqlite3.Connection, sex_cd: str) -> str:
    """Resolve SNOMED CODE_CD → app gender code ('m','f','d') via CODE_LOOKUP."""
    if not sex_cd:
        return ""
    cache = _get_code_cache(conn)
    entry = cache.get(sex_cd)
    if entry and entry.get("COLUMN_CD") == "SEX_CD":
        return entry.get("_blob", {}).get("app_code", "")
    return ""


# ── Dataclasses (public API – unchanged) ───────────────────────────

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


# ── Marshal / unmarshal helpers ────────────────────────────────────

def _marshal_patient_blob(first_name: str, last_name: str, notes: str) -> str:
    return json.dumps({"first_name": first_name, "last_name": last_name, "notes": notes})


def _unmarshal_patient_blob(blob: str | None) -> dict:
    if not blob:
        return {"first_name": "", "last_name": "", "notes": ""}
    try:
        return json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return {"first_name": "", "last_name": "", "notes": ""}


def _marshal_observation_blob(hand: str, duration_s: float, raw_data_path: str, features: dict) -> str:
    return json.dumps({
        "hand": hand,
        "duration_s": duration_s,
        "raw_data_path": raw_data_path,
        "features": features,
    }, default=str)


def _unmarshal_observation_blob(blob: str | None) -> dict:
    if not blob:
        return {"hand": "", "duration_s": 0.0, "raw_data_path": "", "features": {}}
    try:
        return json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return {"hand": "", "duration_s": 0.0, "raw_data_path": "", "features": {}}


def _test_type_to_concept_cd(test_type: str) -> str:
    return "TAPPD:" + test_type.upper()


def _concept_cd_to_test_type(concept_cd: str) -> str:
    if concept_cd and concept_cd.startswith("TAPPD:"):
        return concept_cd[6:].lower()
    return concept_cd or ""


def _category_for_test(test_type: str) -> str:
    if test_type in ("tower_of_hanoi", "spatial_srt", "trail_making_a", "trail_making_b"):
        return "COGNITIVE_TEST"
    return "MOTOR_TEST"


# ── Database connection ────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    """Get a database connection, creating/migrating tables if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    log.debug("Datenbankverbindung geoeffnet: %s", DB_PATH)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create star schema tables, migrate from v1 if needed."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    needs_migration = "patients" in tables and "PATIENT_DIMENSION" not in tables

    if needs_migration:
        _migrate_from_v1(conn)
    elif "PATIENT_DIMENSION" not in tables:
        _create_tables(conn)
        _seed_concepts(conn)
        _seed_code_lookup(conn)
    else:
        # Inline migrations for existing v2 databases
        _migrate_v2(conn)


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """Inline migrations for existing v2 star schema databases."""
    # Add LOOKUP_BLOB column to CODE_LOOKUP if missing
    cols = {r[1] for r in conn.execute("PRAGMA table_info(CODE_LOOKUP)").fetchall()}
    if "LOOKUP_BLOB" not in cols:
        conn.execute("ALTER TABLE CODE_LOOKUP ADD COLUMN LOOKUP_BLOB TEXT")
        conn.commit()
        log.info("CODE_LOOKUP: LOOKUP_BLOB Spalte hinzugefuegt")

    # Ensure LOOKUP_BLOB is populated for SEX_CD entries
    gender_blobs = {
        "SCTID: 248153007": '{"app_code":"m"}',
        "SCTID: 248152002": '{"app_code":"f"}',
        "SCTID: 32570681000036106": '{"app_code":"d"}',
    }
    for code_cd, blob in gender_blobs.items():
        conn.execute(
            "UPDATE CODE_LOOKUP SET LOOKUP_BLOB=? WHERE CODE_CD=? AND (LOOKUP_BLOB IS NULL OR LOOKUP_BLOB='')",
            (blob, code_cd),
        )
    conn.commit()
    _invalidate_code_cache()


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS PATIENT_DIMENSION (
            PATIENT_NUM INTEGER PRIMARY KEY AUTOINCREMENT,
            PATIENT_CD TEXT UNIQUE NOT NULL,
            VITAL_STATUS_CD TEXT DEFAULT 'SCTID: 438949009',
            BIRTH_DATE TEXT,
            AGE_IN_YEARS NUMERIC,
            SEX_CD TEXT,
            PATIENT_BLOB TEXT,
            UPDATE_DATE TEXT,
            SOURCESYSTEM_CD TEXT DEFAULT 'TAPPD',
            CREATED_AT TEXT DEFAULT (datetime('now')),
            UPDATED_AT TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_patient_patient_cd ON PATIENT_DIMENSION(PATIENT_CD);
        CREATE INDEX IF NOT EXISTS idx_patient_sex ON PATIENT_DIMENSION(SEX_CD);
        CREATE INDEX IF NOT EXISTS idx_patient_birth_date ON PATIENT_DIMENSION(BIRTH_DATE);

        CREATE TABLE IF NOT EXISTS VISIT_DIMENSION (
            ENCOUNTER_NUM INTEGER PRIMARY KEY AUTOINCREMENT,
            PATIENT_NUM INTEGER NOT NULL REFERENCES PATIENT_DIMENSION(PATIENT_NUM) ON DELETE CASCADE,
            ACTIVE_STATUS_CD TEXT DEFAULT 'SCTID: 55561003',
            START_DATE TEXT,
            END_DATE TEXT,
            INOUT_CD TEXT DEFAULT 'O',
            LOCATION_CD TEXT DEFAULT 'TAPPD/LOCAL',
            VISIT_BLOB TEXT,
            UPDATE_DATE TEXT,
            SOURCESYSTEM_CD TEXT DEFAULT 'TAPPD',
            CREATED_AT TEXT DEFAULT (datetime('now')),
            UPDATED_AT TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_visit_patient_num ON VISIT_DIMENSION(PATIENT_NUM);
        CREATE INDEX IF NOT EXISTS idx_visit_start_date ON VISIT_DIMENSION(START_DATE);

        CREATE TABLE IF NOT EXISTS CONCEPT_DIMENSION (
            CONCEPT_CD TEXT PRIMARY KEY,
            CONCEPT_PATH TEXT,
            NAME_CHAR TEXT NOT NULL,
            VALTYPE_CD TEXT,
            UNIT_CD TEXT,
            CATEGORY_CHAR TEXT,
            CONCEPT_BLOB TEXT,
            UPDATE_DATE TEXT,
            SOURCESYSTEM_CD TEXT DEFAULT 'TAPPD'
        );
        CREATE INDEX IF NOT EXISTS idx_concept_category ON CONCEPT_DIMENSION(CATEGORY_CHAR);

        CREATE TABLE IF NOT EXISTS OBSERVATION_FACT (
            OBSERVATION_ID INTEGER PRIMARY KEY AUTOINCREMENT,
            ENCOUNTER_NUM INTEGER REFERENCES VISIT_DIMENSION(ENCOUNTER_NUM) ON DELETE CASCADE,
            PATIENT_NUM INTEGER NOT NULL REFERENCES PATIENT_DIMENSION(PATIENT_NUM) ON DELETE CASCADE,
            CATEGORY_CHAR TEXT,
            CONCEPT_CD TEXT REFERENCES CONCEPT_DIMENSION(CONCEPT_CD),
            PROVIDER_ID TEXT DEFAULT 'TAPPD_LOCAL',
            START_DATE TEXT,
            INSTANCE_NUM NUMERIC DEFAULT 1,
            VALTYPE_CD TEXT DEFAULT 'B',
            TVAL_CHAR TEXT,
            NVAL_NUM NUMERIC,
            VALUEFLAG_CD TEXT,
            UNIT_CD TEXT,
            OBSERVATION_BLOB TEXT,
            UPDATE_DATE TEXT,
            SOURCESYSTEM_CD TEXT DEFAULT 'TAPPD',
            CREATED_AT TEXT DEFAULT (datetime('now')),
            UPDATED_AT TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_observation_patient_num ON OBSERVATION_FACT(PATIENT_NUM);
        CREATE INDEX IF NOT EXISTS idx_observation_encounter_num ON OBSERVATION_FACT(ENCOUNTER_NUM);
        CREATE INDEX IF NOT EXISTS idx_observation_concept_cd ON OBSERVATION_FACT(CONCEPT_CD);
        CREATE INDEX IF NOT EXISTS idx_observation_start_date ON OBSERVATION_FACT(START_DATE);

        CREATE TABLE IF NOT EXISTS CODE_LOOKUP (
            CODE_CD TEXT PRIMARY KEY,
            TABLE_CD TEXT,
            COLUMN_CD TEXT,
            NAME_CHAR TEXT,
            LOOKUP_BLOB TEXT,
            UPDATE_DATE TEXT,
            SOURCESYSTEM_CD TEXT DEFAULT 'TAPPD'
        );

        CREATE TABLE IF NOT EXISTS NOTE_FACT (
            NOTE_ID INTEGER PRIMARY KEY AUTOINCREMENT,
            CATEGORY_CHAR TEXT,
            NAME_CHAR TEXT,
            NOTE_TEXT TEXT,
            PATIENT_NUM INTEGER REFERENCES PATIENT_DIMENSION(PATIENT_NUM) ON DELETE CASCADE,
            ENCOUNTER_NUM INTEGER REFERENCES VISIT_DIMENSION(ENCOUNTER_NUM) ON DELETE CASCADE,
            UPDATE_DATE TEXT,
            SOURCESYSTEM_CD TEXT DEFAULT 'TAPPD',
            CREATED_AT TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_note_patient_num ON NOTE_FACT(PATIENT_NUM);
        CREATE INDEX IF NOT EXISTS idx_note_encounter_num ON NOTE_FACT(ENCOUNTER_NUM);
    """)
    conn.commit()


# ── Seed data ──────────────────────────────────────────────────────

def _seed_concepts(conn: sqlite3.Connection) -> None:
    concepts = [
        ("TAPPD:FINGER_TAPPING", "/TapPD/Motor/Finger Tapping/", "Finger Tapping (UPDRS 3.4)", "B", None, "MOTOR_TEST"),
        ("TAPPD:HAND_OPEN_CLOSE", "/TapPD/Motor/Hand Open Close/", "Hand Open/Close (UPDRS 3.5)", "B", None, "MOTOR_TEST"),
        ("TAPPD:PRONATION_SUPINATION", "/TapPD/Motor/Pronation Supination/", "Pronation/Supination (UPDRS 3.6)", "B", None, "MOTOR_TEST"),
        ("TAPPD:POSTURAL_TREMOR", "/TapPD/Motor/Postural Tremor/", "Postural Tremor (UPDRS 3.15)", "B", None, "MOTOR_TEST"),
        ("TAPPD:REST_TREMOR", "/TapPD/Motor/Rest Tremor/", "Rest Tremor (UPDRS 3.17)", "B", None, "MOTOR_TEST"),
        ("TAPPD:TOWER_OF_HANOI", "/TapPD/Cognitive/Tower of Hanoi/", "Tower of Hanoi", "B", None, "COGNITIVE_TEST"),
        ("TAPPD:SPATIAL_SRT", "/TapPD/Cognitive/Spatial SRT/", "Spatial Serial Reaction Time", "B", None, "COGNITIVE_TEST"),
        ("TAPPD:TRAIL_MAKING_A", "/TapPD/Cognitive/Trail Making A/", "Trail Making Test Part A", "B", None, "COGNITIVE_TEST"),
        ("TAPPD:TRAIL_MAKING_B", "/TapPD/Cognitive/Trail Making B/", "Trail Making Test Part B", "B", None, "COGNITIVE_TEST"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO CONCEPT_DIMENSION "
        "(CONCEPT_CD, CONCEPT_PATH, NAME_CHAR, VALTYPE_CD, UNIT_CD, CATEGORY_CHAR) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        concepts,
    )
    conn.commit()


def _seed_code_lookup(conn: sqlite3.Connection) -> None:
    codes = [
        ("SCTID: 248153007", "PATIENT_DIMENSION", "SEX_CD", "Maennlich", '{"app_code":"m"}'),
        ("SCTID: 248152002", "PATIENT_DIMENSION", "SEX_CD", "Weiblich", '{"app_code":"f"}'),
        ("SCTID: 32570681000036106", "PATIENT_DIMENSION", "SEX_CD", "Divers", '{"app_code":"d"}'),
        ("SCTID: 438949009", "PATIENT_DIMENSION", "VITAL_STATUS_CD", "Lebendig", None),
        ("SCTID: 55561003", "VISIT_DIMENSION", "ACTIVE_STATUS_CD", "Aktiv", None),
        ("O", "VISIT_DIMENSION", "INOUT_CD", "Ambulant", None),
        ("I", "VISIT_DIMENSION", "INOUT_CD", "Stationaer", None),
        ("E", "VISIT_DIMENSION", "INOUT_CD", "Notfall", None),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO CODE_LOOKUP (CODE_CD, TABLE_CD, COLUMN_CD, NAME_CHAR, LOOKUP_BLOB) "
        "VALUES (?, ?, ?, ?, ?)",
        codes,
    )
    conn.commit()
    _invalidate_code_cache()


# ── V1 → V2 migration ─────────────────────────────────────────────

def _migrate_from_v1(conn: sqlite3.Connection) -> None:
    """Migrate from old 3-table schema to star schema."""
    # Backup
    backup_path = DB_PATH.with_name("tappd_v1_backup.db")
    if not backup_path.exists():
        shutil.copy2(DB_PATH, backup_path)
        log.info("Backup erstellt: %s", backup_path)

    log.info("Starte Migration von v1 (3-Tabellen) auf v2 (Star Schema)...")

    # Create new tables
    _create_tables(conn)
    _seed_concepts(conn)
    _seed_code_lookup(conn)

    # Migrate patients → PATIENT_DIMENSION
    conn.execute("""
        INSERT INTO PATIENT_DIMENSION (PATIENT_NUM, PATIENT_CD, BIRTH_DATE, SEX_CD, PATIENT_BLOB,
                                       CREATED_AT, SOURCESYSTEM_CD)
        SELECT
            id,
            patient_code,
            CASE WHEN birth_date != '' THEN birth_date ELSE NULL END,
            CASE gender
                WHEN 'm' THEN 'SCTID: 248153007'
                WHEN 'f' THEN 'SCTID: 248152002'
                WHEN 'd' THEN 'SCTID: 32570681000036106'
                ELSE NULL
            END,
            json_object('first_name', first_name, 'last_name', last_name, 'notes', notes),
            created_at,
            'TAPPD_MIGRATION'
        FROM patients
    """)

    # Migrate sessions → VISIT_DIMENSION
    conn.execute("""
        INSERT INTO VISIT_DIMENSION (ENCOUNTER_NUM, PATIENT_NUM, START_DATE, VISIT_BLOB, SOURCESYSTEM_CD)
        SELECT id, patient_id, started_at, json_object('notes', notes), 'TAPPD_MIGRATION'
        FROM sessions
    """)

    # Migrate measurements → OBSERVATION_FACT
    conn.execute("""
        INSERT INTO OBSERVATION_FACT (
            OBSERVATION_ID, ENCOUNTER_NUM, PATIENT_NUM, CATEGORY_CHAR,
            CONCEPT_CD, START_DATE, VALTYPE_CD, TVAL_CHAR, NVAL_NUM,
            OBSERVATION_BLOB, SOURCESYSTEM_CD
        )
        SELECT
            m.id,
            m.session_id,
            m.patient_id,
            CASE
                WHEN m.test_type IN ('tower_of_hanoi','spatial_srt','trail_making_a','trail_making_b')
                THEN 'COGNITIVE_TEST'
                ELSE 'MOTOR_TEST'
            END,
            'TAPPD:' || UPPER(m.test_type),
            m.recorded_at,
            'B',
            m.hand,
            CASE WHEN m.features_json IS NULL OR m.features_json = '' THEN NULL
                 ELSE json_extract(m.features_json, '$.mpi') END,
            json_object(
                'hand', m.hand,
                'duration_s', m.duration_s,
                'raw_data_path', COALESCE(m.raw_data_path, ''),
                'features', CASE
                    WHEN m.features_json IS NULL OR m.features_json = '' THEN json('{}')
                    ELSE json(m.features_json)
                END
            ),
            'TAPPD_MIGRATION'
        FROM measurements m
    """)

    # Drop old tables
    conn.executescript("""
        DROP TABLE IF EXISTS measurements;
        DROP TABLE IF EXISTS sessions;
        DROP TABLE IF EXISTS patients;
    """)
    conn.commit()

    migrated = conn.execute("SELECT COUNT(*) FROM PATIENT_DIMENSION").fetchone()[0]
    log.info("Migration abgeschlossen: %d Patienten migriert", migrated)


# ── Row → Dataclass helpers ────────────────────────────────────────

def _row_to_patient(conn: sqlite3.Connection, r) -> Patient:
    d = dict(r)
    blob = _unmarshal_patient_blob(d.get("PATIENT_BLOB"))
    return Patient(
        id=d["PATIENT_NUM"],
        patient_code=d["PATIENT_CD"],
        first_name=blob.get("first_name", ""),
        last_name=blob.get("last_name", ""),
        birth_date=d.get("BIRTH_DATE") or "",
        gender=_snomed_to_gender(conn, d.get("SEX_CD") or ""),
        notes=blob.get("notes", ""),
        created_at=d.get("CREATED_AT") or "",
    )


def _row_to_measurement(r) -> Measurement:
    d = dict(r)
    blob = _unmarshal_observation_blob(d.get("OBSERVATION_BLOB"))
    features = blob.get("features", {})
    return Measurement(
        id=d["OBSERVATION_ID"],
        patient_id=d["PATIENT_NUM"],
        session_id=d.get("ENCOUNTER_NUM"),
        test_type=_concept_cd_to_test_type(d.get("CONCEPT_CD", "")),
        hand=blob.get("hand", d.get("TVAL_CHAR", "")),
        duration_s=blob.get("duration_s", 0.0),
        features_json=json.dumps(features, default=str),
        recorded_at=d.get("START_DATE") or "",
        raw_data_path=blob.get("raw_data_path", ""),
    )


def _row_to_session(r) -> Session:
    d = dict(r)
    blob_str = d.get("VISIT_BLOB")
    notes = ""
    if blob_str:
        try:
            notes = json.loads(blob_str).get("notes", "")
        except (json.JSONDecodeError, TypeError):
            pass
    return Session(
        id=d["ENCOUNTER_NUM"],
        patient_id=d["PATIENT_NUM"],
        started_at=d.get("START_DATE") or "",
        notes=notes,
    )


# ── Patient CRUD ────────────────────────────────────────────────────

def save_patient(conn: sqlite3.Connection, patient: Patient) -> Patient:
    blob = _marshal_patient_blob(patient.first_name, patient.last_name, patient.notes)
    sex_cd = _gender_to_snomed(conn, patient.gender)
    now = datetime.now().isoformat()

    if patient.id is not None:
        conn.execute(
            "UPDATE PATIENT_DIMENSION SET PATIENT_CD=?, BIRTH_DATE=?, SEX_CD=?, "
            "PATIENT_BLOB=?, UPDATE_DATE=?, UPDATED_AT=? WHERE PATIENT_NUM=?",
            (patient.patient_code, patient.birth_date or None, sex_cd,
             blob, now, now, patient.id),
        )
        log.info("Patient aktualisiert: %s (ID %d)", patient.patient_code, patient.id)
    else:
        cur = conn.execute(
            "INSERT INTO PATIENT_DIMENSION (PATIENT_CD, BIRTH_DATE, SEX_CD, PATIENT_BLOB) "
            "VALUES (?, ?, ?, ?)",
            (patient.patient_code, patient.birth_date or None, sex_cd, blob),
        )
        patient.id = cur.lastrowid
        log.info("Neuer Patient angelegt: %s (ID %d)", patient.patient_code, patient.id)
    conn.commit()
    return patient


def find_patients(conn: sqlite3.Connection, query: str = "") -> list[Patient]:
    if query:
        rows = conn.execute(
            "SELECT * FROM PATIENT_DIMENSION WHERE PATIENT_CD LIKE ? OR PATIENT_BLOB LIKE ? "
            "ORDER BY PATIENT_CD",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM PATIENT_DIMENSION ORDER BY PATIENT_CD").fetchall()
    return [_row_to_patient(conn, r) for r in rows]


def get_patient(conn: sqlite3.Connection, patient_id: int) -> Patient | None:
    row = conn.execute(
        "SELECT * FROM PATIENT_DIMENSION WHERE PATIENT_NUM=?", (patient_id,)
    ).fetchone()
    return _row_to_patient(conn, row) if row else None


# ── Session CRUD ───────────────────────────────────────────────────

def create_session(conn: sqlite3.Connection, patient_id: int) -> Session:
    s = Session(patient_id=patient_id, started_at=datetime.now().isoformat())
    blob = json.dumps({"notes": s.notes})
    cur = conn.execute(
        "INSERT INTO VISIT_DIMENSION (PATIENT_NUM, START_DATE, VISIT_BLOB) VALUES (?, ?, ?)",
        (s.patient_id, s.started_at, blob),
    )
    s.id = cur.lastrowid
    conn.commit()
    log.info("Neue Session erstellt: ID %d fuer Patient %d", s.id, patient_id)
    return s


def get_sessions(conn: sqlite3.Connection, patient_id: int) -> list[Session]:
    rows = conn.execute(
        "SELECT * FROM VISIT_DIMENSION WHERE PATIENT_NUM=? ORDER BY START_DATE DESC",
        (patient_id,),
    ).fetchall()
    return [_row_to_session(r) for r in rows]


def delete_session(conn: sqlite3.Connection, session_id: int) -> None:
    """Delete a session and all its observations (cascade)."""
    conn.execute("DELETE FROM OBSERVATION_FACT WHERE ENCOUNTER_NUM=?", (session_id,))
    conn.execute("DELETE FROM VISIT_DIMENSION WHERE ENCOUNTER_NUM=?", (session_id,))
    conn.commit()
    log.info("Session geloescht: ID %d (inkl. zugehoeriger Messungen)", session_id)


def get_session_measurements(conn: sqlite3.Connection, session_id: int) -> list[Measurement]:
    rows = conn.execute(
        "SELECT * FROM OBSERVATION_FACT WHERE ENCOUNTER_NUM=? ORDER BY START_DATE",
        (session_id,),
    ).fetchall()
    return [_row_to_measurement(r) for r in rows]


# ── Measurement CRUD ────────────────────────────────────────────────

def save_measurement(conn: sqlite3.Connection, m: Measurement) -> Measurement:
    if not m.recorded_at:
        m.recorded_at = datetime.now().isoformat()

    concept_cd = _test_type_to_concept_cd(m.test_type)
    category = _category_for_test(m.test_type)
    features = m.features
    obs_blob = _marshal_observation_blob(m.hand, m.duration_s, m.raw_data_path, features)
    mpi = features.get("mpi")

    cur = conn.execute(
        "INSERT INTO OBSERVATION_FACT "
        "(ENCOUNTER_NUM, PATIENT_NUM, CATEGORY_CHAR, CONCEPT_CD, START_DATE, "
        " VALTYPE_CD, TVAL_CHAR, NVAL_NUM, OBSERVATION_BLOB) "
        "VALUES (?, ?, ?, ?, ?, 'B', ?, ?, ?)",
        (m.session_id, m.patient_id, category, concept_cd, m.recorded_at,
         m.hand, mpi, obs_blob),
    )
    m.id = cur.lastrowid
    conn.commit()
    log.info("Messung gespeichert: %s %s (ID %d, Patient %d, %.1fs)",
             m.test_type, m.hand, m.id, m.patient_id, m.duration_s)
    return m


def get_measurements(conn: sqlite3.Connection, patient_id: int) -> list[Measurement]:
    rows = conn.execute(
        "SELECT * FROM OBSERVATION_FACT WHERE PATIENT_NUM=? ORDER BY START_DATE DESC",
        (patient_id,),
    ).fetchall()
    return [_row_to_measurement(r) for r in rows]


def delete_measurement(conn: sqlite3.Connection, measurement_id: int) -> None:
    conn.execute("DELETE FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (measurement_id,))
    conn.commit()
    log.info("Messung geloescht: ID %d", measurement_id)


def get_last_measurement_dates(conn: sqlite3.Connection) -> dict[int, str]:
    """Return {patient_id: last_recorded_at} for all patients with measurements."""
    rows = conn.execute(
        "SELECT PATIENT_NUM, MAX(START_DATE) as last_date "
        "FROM OBSERVATION_FACT GROUP BY PATIENT_NUM"
    ).fetchall()
    return {r["PATIENT_NUM"]: r["last_date"] for r in rows}


def get_all_measurements(conn: sqlite3.Connection) -> list[tuple[Patient, Measurement]]:
    rows = conn.execute(
        "SELECT o.OBSERVATION_ID, o.ENCOUNTER_NUM, o.PATIENT_NUM, o.CATEGORY_CHAR, "
        "  o.CONCEPT_CD, o.START_DATE, o.INSTANCE_NUM, o.VALTYPE_CD, "
        "  o.TVAL_CHAR, o.NVAL_NUM, o.OBSERVATION_BLOB, "
        "  p.PATIENT_CD, p.BIRTH_DATE, p.SEX_CD, p.PATIENT_BLOB, "
        "  p.CREATED_AT AS P_CREATED_AT "
        "FROM OBSERVATION_FACT o JOIN PATIENT_DIMENSION p ON o.PATIENT_NUM = p.PATIENT_NUM "
        "ORDER BY o.START_DATE DESC"
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        blob = _unmarshal_patient_blob(d.get("PATIENT_BLOB"))
        p = Patient(
            id=d["PATIENT_NUM"],
            patient_code=d["PATIENT_CD"],
            first_name=blob.get("first_name", ""),
            last_name=blob.get("last_name", ""),
            birth_date=d.get("BIRTH_DATE") or "",
            gender=_snomed_to_gender(conn, d.get("SEX_CD") or ""),
            notes=blob.get("notes", ""),
            created_at=d.get("P_CREATED_AT") or "",
        )
        m = _row_to_measurement(r)
        results.append((p, m))
    return results


def update_raw_data_path(conn: sqlite3.Connection, observation_id: int, path: str) -> None:
    """Update the raw_data_path inside OBSERVATION_BLOB for a given observation."""
    row = conn.execute(
        "SELECT OBSERVATION_BLOB FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?",
        (observation_id,),
    ).fetchone()
    if not row:
        log.warning("Observation %d nicht gefunden fuer raw_data_path Update", observation_id)
        return

    raw_blob = row["OBSERVATION_BLOB"]
    if raw_blob:
        try:
            blob = json.loads(raw_blob)
        except (json.JSONDecodeError, TypeError):
            log.warning("OBSERVATION_BLOB korrupt fuer ID %d; raw_data_path wird nicht aktualisiert", observation_id)
            return
    else:
        blob = {"hand": "", "duration_s": 0.0, "raw_data_path": "", "features": {}}

    blob["raw_data_path"] = path
    conn.execute(
        "UPDATE OBSERVATION_FACT SET OBSERVATION_BLOB=?, UPDATE_DATE=? WHERE OBSERVATION_ID=?",
        (json.dumps(blob, default=str), datetime.now().isoformat(), observation_id),
    )
    conn.commit()
    log.debug("raw_data_path aktualisiert fuer Observation %d", observation_id)
