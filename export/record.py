"""One patient's record as a plain dict — the shape every export reads.

Patient, sessions (with clinical forms, measurements, notes), measurements
without a session. Pseudonymised on request: the code becomes the pseudonym,
name and birth date are dropped, the birth year stays.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime

from storage.database import (Measurement, Patient, get_measurements, get_notes,
                              get_sessions)


def _age(patient: Patient) -> int | None:
    try:
        return patient.age
    except Exception:
        return None


def patient_block(patient: Patient, pseudonymise: bool) -> dict:
    if pseudonymise:
        from export.pseudonyms import pseudonym_for
        code = pseudonym_for(patient.patient_code)
        return {"id": code, "pseudonymised": True, "sex": patient.gender or "",
                "birth_year": (patient.birth_date or "")[:4] or None, "age": _age(patient)}
    return {"id": patient.patient_code, "pseudonymised": False,
            "first_name": patient.first_name, "last_name": patient.last_name,
            "sex": patient.gender or "", "birth_date": patient.birth_date or "",
            "birth_year": (patient.birth_date or "")[:4] or None, "age": _age(patient),
            "notes": patient.notes or ""}


def measurement_block(m: Measurement, include_paths: bool = True) -> dict:
    prov = dict(m.provenance or {})
    out = {
        "id": m.id, "session_id": m.session_id, "test_type": m.test_type, "hand": m.hand,
        "duration_s": m.duration_s, "recorded_at": m.recorded_at,
        "source_kind": m.source_kind or "",
        "features": {k: v for k, v in (m.features or {}).items() if not k.startswith("_")},
        "analysed_on": prov.get("analysed_on", ""),
        "deidentified": bool(prov.get("deidentified", False)),
        "eye_ref_coverage": prov.get("eye_ref_coverage"),
        "capture": dict(prov.get("capture") or {}),
        "analysis": dict(prov.get("analysis") or {}),
    }
    if include_paths:
        out["raw_data_path"] = m.raw_data_path or ""
        out["clip_path"] = prov.get("clip_path", "")
        out["track_path"] = prov.get("track_path", "")
    return out


def form_block(entry, form) -> dict:
    from clinical.schema import describe, summary_line
    return {"id": entry.id, "form": entry.form_id, "name": form.name if form else entry.form_id,
            "version": entry.version, "recorded_at": entry.recorded_at,
            "answers": entry.answers, "computed": entry.computed,
            "summary": summary_line(form, entry.answers, entry.computed) if form else "",
            "rows": describe(form, entry.answers, entry.computed) if form else []}


def build_record(conn: sqlite3.Connection, patient: Patient, *, pseudonymise: bool = False,
                 include_notes: bool = True, include_paths: bool = True) -> dict:
    from clinical.store import get_form_entries, form_for_entry
    from app_settings import APP_VERSION

    sessions = get_sessions(conn, patient.id)
    measurements = get_measurements(conn, patient.id)
    notes = get_notes(conn, patient.id) if include_notes else []
    entries = get_form_entries(conn, patient.id)
    forms_cache: dict = {}

    def form_of(e):
        if e.form_id not in forms_cache:
            try:
                forms_cache[e.form_id] = form_for_entry(e)
            except Exception:
                forms_cache[e.form_id] = None
        return forms_cache[e.form_id]

    def note_for(kind: str, ref: str) -> dict | None:
        n = next((x for x in notes if x.kind == kind and x.ref == ref), None)
        if n is None:
            return None
        return {"text": n.text, "updated_at": n.updated_at,
                "attachments": [dict(a) for a in n.attachments]}

    out_sessions = []
    for s in sorted(sessions, key=lambda x: x.started_at):
        ms = [m for m in measurements if m.session_id == s.id]
        block = {
            "id": s.id, "date": s.started_at, "notes": s.notes or "",
            "note": note_for("session", str(s.id)),
            "forms": [form_block(e, form_of(e)) for e in entries if e.session_id == s.id],
            "measurements": [],
        }
        for m in sorted(ms, key=lambda x: x.recorded_at):
            mb = measurement_block(m, include_paths)
            mb["note"] = note_for("measurement", str(m.id))
            block["measurements"].append(mb)
        out_sessions.append(block)
    orphans = [measurement_block(m, include_paths)
               for m in measurements if m.session_id is None]
    return {
        "format": "tappd-record", "format_version": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "app_version": APP_VERSION,
        "patient": patient_block(patient, pseudonymise),
        "sessions": out_sessions,
        "unassigned_measurements": orphans,
        "forms_without_session": [form_block(e, form_of(e)) for e in entries
                                  if e.session_id is None],
    }


def files_of(record: dict) -> list[tuple[str, str]]:
    """(kind, path) of every file the record refers to and that exists."""
    out = []
    for s in record.get("sessions", []) + [{"measurements": record.get("unassigned_measurements", [])}]:
        for m in s.get("measurements", []):
            for kind in ("clip_path", "track_path", "raw_data_path"):
                p = m.get(kind, "")
                if p and os.path.isfile(p):
                    out.append((kind, p))
            for a in (m.get("note") or {}).get("attachments", []):
                if os.path.isfile(a.get("path", "")):
                    out.append(("attachment", a["path"]))
        for a in (s.get("note") or {}).get("attachments", []):
            if os.path.isfile(a.get("path", "")):
                out.append(("attachment", a["path"]))
    return out
