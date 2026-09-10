"""Clinical forms in the record (OBSERVATION_FACT).

One filled form = one *entry*:

* a **Q row** (``VALTYPE_CD='Q'``, ``CONCEPT_CD='TAPPD:FORM_<ID>'``) whose blob
  holds the whole thing — ``{form, version, answers, computed}`` — for
  round trips, versioning and pre-filling the next form;
* one **coded row per answered item** (``VALTYPE_CD`` N / T / D, the item's
  ``concept``), one **B row per repeat-group line** (``INSTANCE_NUM`` = line
  number, e.g. each medication) and one N row per computed value (LEDD,
  disease duration). These are what SQL and the research export read.

All rows carry ``CATEGORY_CHAR='CLINICAL'`` — the measurement queries skip
that category — and the item rows point back at their entry through
``OBSERVATION_BLOB.form_entry``. Concepts are registered in
``CONCEPT_DIMENSION`` from the form definition the first time it is saved.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from clinical.schema import Form, Item, compute, is_empty, load_form

log = logging.getLogger(__name__)

CATEGORY = "CLINICAL"


@dataclass
class FormEntry:
    id: int | None = None
    patient_id: int = 0
    session_id: int | None = None
    form_id: str = ""
    version: int = 1
    recorded_at: str = ""
    answers: dict = field(default_factory=dict)
    computed: dict = field(default_factory=dict)


# ── concepts ───────────────────────────────────────────────────────

def _valtype(it: Item) -> str:
    if it.is_numeric or it.type == "bool":
        return "N"
    if it.type == "date":
        return "D"
    return "T"


def ensure_concepts(conn: sqlite3.Connection, form: Form) -> None:
    rows = [(form.concept_cd, f"/TapPD/Clinical/{form.id}/", form.name, "Q", None, CATEGORY)]
    for s in form.sections:
        for it in s.items:
            if it.concept:
                rows.append((it.concept, f"/TapPD/Clinical/{form.id}/{it.key}/", it.label,
                             _valtype(it), it.unit or None, CATEGORY))
        if s.repeat is not None and s.repeat.concept:
            rows.append((s.repeat.concept, f"/TapPD/Clinical/{form.id}/{s.repeat.key}/",
                         s.repeat.label, "B", None, CATEGORY))
    for c in form.computed:
        if c.concept:
            rows.append((c.concept, f"/TapPD/Clinical/{form.id}/{c.key}/", c.label, "N",
                         c.unit or None, CATEGORY))
    conn.executemany(
        "INSERT OR IGNORE INTO CONCEPT_DIMENSION "
        "(CONCEPT_CD, CONCEPT_PATH, NAME_CHAR, VALTYPE_CD, UNIT_CD, CATEGORY_CHAR) "
        "VALUES (?, ?, ?, ?, ?, ?)", rows)


# ── save / load ────────────────────────────────────────────────────

def _item_row(it: Item, value):
    """(VALTYPE_CD, TVAL_CHAR, NVAL_NUM) for one answered item."""
    if it.type == "bool":
        return "N", "ja" if value else "nein", 1.0 if value else 0.0
    if it.is_numeric:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return "T", str(value), None
        return "N", (it.choice_label(value) if it.type == "choice" else None), num
    if it.type == "choice":
        return "T", str(value), None
    if it.type == "multichoice":
        return "T", ",".join(str(v) for v in value), None
    if it.type == "date":
        return "D", str(value), None
    return "T", str(value), None


def save_form_entry(conn: sqlite3.Connection, patient_id: int, session_id: int | None,
                    form: Form, answers: dict, entry_id: int | None = None,
                    recorded_at: str = "") -> FormEntry:
    """Write (or overwrite, with ``entry_id``) one filled form."""
    ensure_concepts(conn, form)
    computed = compute(form, answers)
    now = recorded_at or datetime.now().isoformat()
    blob = json.dumps({"form": form.id, "version": form.version, "answers": answers,
                       "computed": computed}, default=str)
    if entry_id:
        conn.execute("DELETE FROM OBSERVATION_FACT WHERE CATEGORY_CHAR=? AND "
                     "json_extract(OBSERVATION_BLOB, '$.form_entry') = ?", (CATEGORY, entry_id))
        conn.execute("UPDATE OBSERVATION_FACT SET ENCOUNTER_NUM=?, OBSERVATION_BLOB=?, "
                     "TVAL_CHAR=?, UPDATE_DATE=? WHERE OBSERVATION_ID=?",
                     (session_id, blob, form.name, datetime.now().isoformat(), entry_id))
        row = conn.execute("SELECT START_DATE FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?",
                           (entry_id,)).fetchone()
        now = row[0] if row else now
    else:
        cur = conn.execute(
            "INSERT INTO OBSERVATION_FACT (ENCOUNTER_NUM, PATIENT_NUM, CATEGORY_CHAR, CONCEPT_CD, "
            " START_DATE, VALTYPE_CD, TVAL_CHAR, NVAL_NUM, OBSERVATION_BLOB, SOURCESYSTEM_CD) "
            "VALUES (?, ?, ?, ?, ?, 'Q', ?, NULL, ?, 'TAPPD')",
            (session_id, patient_id, CATEGORY, form.concept_cd, now, form.name, blob))
        entry_id = cur.lastrowid

    def add(concept, valtype, tval, nval, extra: dict, instance: int = 1):
        conn.execute(
            "INSERT INTO OBSERVATION_FACT (ENCOUNTER_NUM, PATIENT_NUM, CATEGORY_CHAR, CONCEPT_CD, "
            " START_DATE, INSTANCE_NUM, VALTYPE_CD, TVAL_CHAR, NVAL_NUM, OBSERVATION_BLOB, "
            " SOURCESYSTEM_CD) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'TAPPD')",
            (session_id, patient_id, CATEGORY, concept, now, instance, valtype, tval, nval,
             json.dumps({"form_entry": entry_id, **extra}, default=str)))

    for it in form.items:
        v = answers.get(it.key)
        if it.concept and not is_empty(v):
            valtype, tval, nval = _item_row(it, v)
            add(it.concept, valtype, tval, nval, {"key": it.key})
    for rep in form.repeats:
        if not rep.concept:
            continue
        for i, row in enumerate(answers.get(rep.key) or [], start=1):
            if not row or all(is_empty(x) for x in row.values()):
                continue
            label = next((it.choice_label(row.get(it.key)) for it in rep.items
                          if it.type == "choice" and not is_empty(row.get(it.key))), rep.label)
            add(rep.concept, "B", label, None, {"key": rep.key, "row": row}, instance=i)
    for c in form.computed:
        v = computed.get(c.key)
        if c.concept and not is_empty(v):
            add(c.concept, "N", None, float(v), {"key": c.key})
    conn.commit()
    log.info("Klinische Maske gespeichert: %s (Eintrag %d, Patient %d, Sitzung %s)",
             form.id, entry_id, patient_id, session_id)
    return FormEntry(id=entry_id, patient_id=patient_id, session_id=session_id,
                     form_id=form.id, version=form.version, recorded_at=now,
                     answers=answers, computed=computed)


def _row_to_entry(r) -> FormEntry:
    d = dict(r)
    try:
        blob = json.loads(d.get("OBSERVATION_BLOB") or "{}")
    except (json.JSONDecodeError, TypeError):
        blob = {}
    return FormEntry(id=d["OBSERVATION_ID"], patient_id=d["PATIENT_NUM"],
                     session_id=d.get("ENCOUNTER_NUM"), form_id=str(blob.get("form", "")),
                     version=int(blob.get("version", 1) or 1), recorded_at=d.get("START_DATE") or "",
                     answers=dict(blob.get("answers") or {}), computed=dict(blob.get("computed") or {}))


def get_form_entries(conn: sqlite3.Connection, patient_id: int,
                     form_id: str | None = None) -> list[FormEntry]:
    rows = conn.execute(
        "SELECT * FROM OBSERVATION_FACT WHERE PATIENT_NUM=? AND VALTYPE_CD='Q' "
        "AND CATEGORY_CHAR=? ORDER BY START_DATE, OBSERVATION_ID", (patient_id, CATEGORY)).fetchall()
    out = [_row_to_entry(r) for r in rows]
    return [e for e in out if form_id is None or e.form_id == form_id]


def get_form_entry(conn: sqlite3.Connection, entry_id: int) -> FormEntry | None:
    r = conn.execute("SELECT * FROM OBSERVATION_FACT WHERE OBSERVATION_ID=? AND VALTYPE_CD='Q'",
                     (entry_id,)).fetchone()
    return _row_to_entry(r) if r else None


def latest_entry(conn: sqlite3.Connection, patient_id: int, form_id: str) -> FormEntry | None:
    entries = get_form_entries(conn, patient_id, form_id)
    return entries[-1] if entries else None


def move_form_entry(conn: sqlite3.Connection, entry_id: int, session_id: int | None) -> None:
    """Put a filled form (Q row + its item rows + note) into another session."""
    now = datetime.now().isoformat()
    conn.execute("UPDATE OBSERVATION_FACT SET ENCOUNTER_NUM=?, UPDATE_DATE=? WHERE OBSERVATION_ID=?",
                 (session_id, now, entry_id))
    conn.execute("UPDATE OBSERVATION_FACT SET ENCOUNTER_NUM=? WHERE CATEGORY_CHAR=? AND "
                 "json_extract(OBSERVATION_BLOB, '$.form_entry') = ?",
                 (session_id, CATEGORY, entry_id))
    conn.execute("UPDATE NOTE_FACT SET ENCOUNTER_NUM=? WHERE CATEGORY_CHAR='FORM' AND NAME_CHAR=?",
                 (session_id, str(entry_id)))
    conn.commit()
    log.info("Klinische Maske %d → Sitzung %s", entry_id, session_id)


def delete_form_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute("DELETE FROM OBSERVATION_FACT WHERE CATEGORY_CHAR=? AND "
                 "json_extract(OBSERVATION_BLOB, '$.form_entry') = ?", (CATEGORY, entry_id))
    conn.execute("DELETE FROM OBSERVATION_FACT WHERE OBSERVATION_ID=?", (entry_id,))
    conn.commit()
    log.info("Klinische Maske gelöscht: Eintrag %d", entry_id)


def prefill(conn: sqlite3.Connection, patient_id: int, form: Form) -> dict:
    """Answers a new form starts with: the previous entry's, if carry_forward."""
    if not form.carry_forward:
        return {}
    last = latest_entry(conn, patient_id, form.id)
    return dict(last.answers) if last else {}


def form_for_entry(entry: FormEntry) -> Form:
    return load_form(entry.form_id)
