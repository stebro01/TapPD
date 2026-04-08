"""Database operations for gesture templates."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from gesture_lab.models import GestureTemplate


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS GESTURE_TEMPLATE (
    TEMPLATE_ID         INTEGER PRIMARY KEY AUTOINCREMENT,
    NAME                TEXT NOT NULL,
    DESCRIPTION         TEXT,
    CLINICAL_SOURCE     TEXT,
    GESTURE_TYPE        TEXT NOT NULL DEFAULT 'static',
    HAND_TYPE           TEXT DEFAULT 'any',
    POSE_NUMBER         INTEGER DEFAULT 0,
    SCORING_THRESHOLD_GOOD    REAL DEFAULT 0.85,
    SCORING_THRESHOLD_PARTIAL REAL DEFAULT 0.60,
    SCORING_CRITERIA    TEXT,
    THUMBNAIL_PATH      TEXT,
    TEMPLATE_BLOB       TEXT NOT NULL,
    CREATED_AT          TEXT DEFAULT (datetime('now')),
    UPDATED_AT          TEXT DEFAULT (datetime('now'))
);
"""

_CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_gesture_name ON GESTURE_TEMPLATE(NAME);
CREATE INDEX IF NOT EXISTS idx_gesture_type ON GESTURE_TEMPLATE(GESTURE_TYPE);
CREATE INDEX IF NOT EXISTS idx_gesture_pose ON GESTURE_TEMPLATE(POSE_NUMBER);
"""


def ensure_gesture_table(conn: sqlite3.Connection) -> None:
    """Create the GESTURE_TEMPLATE table if it does not exist."""
    conn.executescript(_CREATE_TABLE + _CREATE_INDEXES)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _template_to_blob(t: GestureTemplate) -> str:
    data = {
        "pose_vector": t.pose_vector,
        "pose_variance": t.pose_variance,
        "finger_weights": t.finger_weights,
        "expected_extensions": t.expected_extensions,
        "raw_frames": t.raw_frames,
        "dynamic_frames": t.dynamic_frames,
        "dynamic_duration_s": t.dynamic_duration_s,
        "dynamic_sample_rate": t.dynamic_sample_rate,
    }
    return json.dumps(data)


def _blob_to_template_fields(blob: str) -> dict:
    return json.loads(blob)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def save_template(conn: sqlite3.Connection, t: GestureTemplate) -> int:
    """Insert a new template and return its id."""
    ensure_gesture_table(conn)
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO GESTURE_TEMPLATE
           (NAME, DESCRIPTION, CLINICAL_SOURCE, GESTURE_TYPE, HAND_TYPE,
            POSE_NUMBER, SCORING_THRESHOLD_GOOD, SCORING_THRESHOLD_PARTIAL,
            SCORING_CRITERIA, THUMBNAIL_PATH, TEMPLATE_BLOB, CREATED_AT, UPDATED_AT)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            t.name, t.description, t.clinical_source, t.gesture_type,
            t.hand_type, t.pose_number, t.threshold_good, t.threshold_partial,
            t.scoring_criteria, t.thumbnail_path, _template_to_blob(t), now, now,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def update_template(conn: sqlite3.Connection, t: GestureTemplate) -> None:
    """Update an existing template (by id)."""
    ensure_gesture_table(conn)
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """UPDATE GESTURE_TEMPLATE SET
           NAME=?, DESCRIPTION=?, CLINICAL_SOURCE=?, GESTURE_TYPE=?,
           HAND_TYPE=?, POSE_NUMBER=?, SCORING_THRESHOLD_GOOD=?,
           SCORING_THRESHOLD_PARTIAL=?, SCORING_CRITERIA=?,
           THUMBNAIL_PATH=?, TEMPLATE_BLOB=?, UPDATED_AT=?
           WHERE TEMPLATE_ID=?""",
        (
            t.name, t.description, t.clinical_source, t.gesture_type,
            t.hand_type, t.pose_number, t.threshold_good, t.threshold_partial,
            t.scoring_criteria, t.thumbnail_path, _template_to_blob(t), now,
            t.id,
        ),
    )
    conn.commit()


def delete_template(conn: sqlite3.Connection, template_id: int) -> None:
    ensure_gesture_table(conn)
    conn.execute("DELETE FROM GESTURE_TEMPLATE WHERE TEMPLATE_ID=?", (template_id,))
    conn.commit()


def load_template(conn: sqlite3.Connection, template_id: int) -> GestureTemplate | None:
    ensure_gesture_table(conn)
    row = conn.execute(
        "SELECT * FROM GESTURE_TEMPLATE WHERE TEMPLATE_ID=?", (template_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_template(row)


def list_templates(conn: sqlite3.Connection) -> list[GestureTemplate]:
    ensure_gesture_table(conn)
    rows = conn.execute(
        "SELECT * FROM GESTURE_TEMPLATE ORDER BY POSE_NUMBER, NAME"
    ).fetchall()
    return [_row_to_template(r) for r in rows]


def list_templates_for_pose(conn: sqlite3.Connection, pose_number: int) -> list[GestureTemplate]:
    """Return all templates for a given pose number, newest first."""
    ensure_gesture_table(conn)
    rows = conn.execute(
        "SELECT * FROM GESTURE_TEMPLATE WHERE POSE_NUMBER=? ORDER BY CREATED_AT DESC",
        (pose_number,),
    ).fetchall()
    return [_row_to_template(r) for r in rows]


def find_template_by_pose(conn: sqlite3.Connection, pose_number: int) -> GestureTemplate | None:
    ensure_gesture_table(conn)
    row = conn.execute(
        "SELECT * FROM GESTURE_TEMPLATE WHERE POSE_NUMBER=? ORDER BY UPDATED_AT DESC LIMIT 1",
        (pose_number,),
    ).fetchone()
    return _row_to_template(row) if row else None


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _row_to_template(row: sqlite3.Row | tuple) -> GestureTemplate:
    # Column order matches CREATE TABLE
    r = row if isinstance(row, (list, tuple)) else list(row)
    blob_data = _blob_to_template_fields(r[11])  # TEMPLATE_BLOB
    return GestureTemplate(
        id=r[0],
        name=r[1],
        description=r[2] or "",
        clinical_source=r[3] or "",
        gesture_type=r[4],
        hand_type=r[5] or "any",
        pose_number=r[6] or 0,
        threshold_good=r[7] or 0.85,
        threshold_partial=r[8] or 0.60,
        scoring_criteria=r[9] or "",
        thumbnail_path=r[10] or "",
        pose_vector=blob_data.get("pose_vector", []),
        pose_variance=blob_data.get("pose_variance", []),
        finger_weights=blob_data.get("finger_weights", [1.0] * 5),
        expected_extensions=blob_data.get("expected_extensions", [True] * 5),
        raw_frames=blob_data.get("raw_frames", []),
        dynamic_frames=blob_data.get("dynamic_frames", []),
        dynamic_duration_s=blob_data.get("dynamic_duration_s", 0.0),
        dynamic_sample_rate=blob_data.get("dynamic_sample_rate", 50.0),
        created_at=r[12] or "",
    )
