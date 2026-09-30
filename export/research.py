"""Research export: analysis-ready long tables across patients + codebook.

Always pseudonymised (export.pseudonyms). One folder with:

    patients.csv        one row per patient
    visits.csv          one row per session (with the clinical summary values)
    measurements.csv    one row per measurement (provenance, quality flags)
    features_long.csv   one row per feature value
    clinical_long.csv   one row per answered clinical item (all forms)
    medication.csv      one row per medication line
    notes.csv           optional
    signals/            optional: raw JSON + tracks per measurement
    codebook.md         variables, labels, units, sources — generated
    manifest.json       what was exported, when, by which version
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from storage.database import Patient, find_patients

_HAND = {"left": "left", "right": "right", "both": "both"}

# Session-level clinical values that get their own column in visits.csv.
_VISIT_KEYS = ("hoehn_yahr", "updrs3_total", "updrs3_state", "med_state", "last_dose_minutes",
               "ledd_mg", "disease_duration_y", "moca", "falls_12m", "freezing", "dbs")
_PATIENT_KEYS = ("diagnosis_year", "onset_year", "onset_side", "dominant_hand", "subtype",
                 "family_pd")


def _w(path: Path, header: list[str], rows: list[dict]) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        wr.writeheader()
        for r in rows:
            wr.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return len(rows)


def _fmt(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, list):
        return ";".join(str(x) for x in v)
    return v


def export_research(conn: sqlite3.Connection, dest: str | Path, *, patient_ids=None,
                    include_notes: bool = False, include_signals: bool = False) -> dict:
    """Write the tables into ``dest``; returns a summary of what was written."""
    from clinical.schema import list_forms
    from export.record import build_record
    from ui.feature_meta import FEATURE_META
    from app_settings import APP_VERSION

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    patients = [p for p in find_patients(conn, "") if patient_ids is None or p.id in patient_ids]
    forms = {f.id: f for f in list_forms()}

    t_pat, t_vis, t_meas, t_feat, t_clin, t_med, t_notes = [], [], [], [], [], [], []
    signals_dir = dest / "signals"
    for p in patients:
        rec = build_record(conn, p, pseudonymise=True, include_notes=include_notes)
        pid = rec["patient"]["id"]
        latest_patient_items: dict = {}
        for s in rec["sessions"]:
            vis = {"pseudonym": pid, "visit_id": s["id"], "date": s["date"][:19],
                   "n_measurements": len(s["measurements"]), "n_forms": len(s["forms"])}
            for f in s["forms"]:
                form = forms.get(f["form"])
                for key, value in f["answers"].items():
                    if key in _VISIT_KEYS:
                        vis[key] = _fmt(value)
                    if key in _PATIENT_KEYS:
                        latest_patient_items[key] = _fmt(value)
                for key, value in (f["computed"] or {}).items():
                    if key in _VISIT_KEYS:
                        vis[key] = _fmt(value)
                # long table: every answered item, repeat rows, computed
                if form is not None:
                    for it in form.items:
                        v = f["answers"].get(it.key)
                        if v in (None, "", []):
                            continue
                        t_clin.append({"pseudonym": pid, "visit_id": s["id"], "date": s["date"][:10],
                                       "form": f["form"], "form_version": f["version"],
                                       "key": it.key, "concept": it.concept, "value": _fmt(v),
                                       "label": it.label, "unit": it.unit, "type": it.type})
                    for c in form.computed:
                        v = (f["computed"] or {}).get(c.key)
                        if v in (None, ""):
                            continue
                        t_clin.append({"pseudonym": pid, "visit_id": s["id"], "date": s["date"][:10],
                                       "form": f["form"], "form_version": f["version"],
                                       "key": c.key, "concept": c.concept, "value": _fmt(v),
                                       "label": c.label, "unit": c.unit, "type": "computed"})
                    for rep in form.repeats:
                        cat = {c.code: c for c in form.catalogs.get("substances", [])}
                        for i, row in enumerate(f["answers"].get(rep.key) or [], start=1):
                            if rep.key != "medication":
                                continue
                            sub = cat.get(str(row.get("substance", "")))
                            dose = row.get("dose_mg")
                            per_day = row.get("per_day")
                            daily = (float(dose) * float(per_day)) if dose not in (None, "") and per_day not in (None, "") else None
                            t_med.append({"pseudonym": pid, "visit_id": s["id"], "date": s["date"][:10],
                                          "line": i, "substance": row.get("substance", ""),
                                          "label": sub.label if sub else "", "atc": (sub.extra.get("atc") if sub else ""),
                                          "dose_mg": dose, "per_day": per_day, "daily_mg": daily,
                                          "ledd_factor": (sub.extra.get("ledd_factor") if sub else ""),
                                          "times": row.get("times", "")})
            t_vis.append(vis)
            for m in s["measurements"]:
                t_meas.append(_measurement_row(pid, s["id"], m))
                for k, v in (m["features"] or {}).items():
                    label, unit = FEATURE_META.get(k, (k, ""))
                    t_feat.append({"measurement_id": m["id"], "pseudonym": pid, "visit_id": s["id"],
                                   "test_type": m["test_type"], "hand": m["hand"], "feature": k,
                                   "value": _fmt(v), "unit": unit,
                                   "estimated_scale": int(unit in ("mm", "mm/s", "mm²") and m["source_kind"] in ("webcam", "video"))})
                if include_signals:
                    _copy_signals(signals_dir, pid, m)
                if include_notes and m.get("note"):
                    t_notes.append({"pseudonym": pid, "visit_id": s["id"], "target": f"measurement:{m['id']}",
                                    "text": m["note"].get("text", "")})
            if include_notes and s.get("note"):
                t_notes.append({"pseudonym": pid, "visit_id": s["id"], "target": "session",
                                "text": s["note"].get("text", "")})
        for m in rec["unassigned_measurements"]:
            t_meas.append(_measurement_row(pid, None, m))
            for k, v in (m["features"] or {}).items():
                label, unit = FEATURE_META.get(k, (k, ""))
                t_feat.append({"measurement_id": m["id"], "pseudonym": pid, "visit_id": None,
                               "test_type": m["test_type"], "hand": m["hand"], "feature": k,
                               "value": _fmt(v), "unit": unit,
                               "estimated_scale": int(unit in ("mm", "mm/s", "mm²") and m["source_kind"] in ("webcam", "video"))})
        row = {"pseudonym": pid, "sex": rec["patient"]["sex"], "birth_year": rec["patient"]["birth_year"],
               "age_at_export": rec["patient"]["age"], "n_visits": len(rec["sessions"]),
               "n_measurements": sum(len(s["measurements"]) for s in rec["sessions"]) + len(rec["unassigned_measurements"])}
        row.update({k: latest_patient_items.get(k) for k in _PATIENT_KEYS})
        t_pat.append(row)

    counts = {
        "patients": _w(dest / "patients.csv", ["pseudonym", "sex", "birth_year", "age_at_export",
                                               "n_visits", "n_measurements", *_PATIENT_KEYS], t_pat),
        "visits": _w(dest / "visits.csv", ["pseudonym", "visit_id", "date", "n_measurements", "n_forms",
                                           *_VISIT_KEYS], t_vis),
        "measurements": _w(dest / "measurements.csv", list(_MEAS_COLS), t_meas),
        "features_long": _w(dest / "features_long.csv", ["measurement_id", "pseudonym", "visit_id",
                                                         "test_type", "hand", "feature", "value",
                                                         "unit", "estimated_scale"], t_feat),
        "clinical_long": _w(dest / "clinical_long.csv", ["pseudonym", "visit_id", "date", "form",
                                                         "form_version", "key", "concept", "value",
                                                         "label", "unit", "type"], t_clin),
        "medication": _w(dest / "medication.csv", ["pseudonym", "visit_id", "date", "line", "substance",
                                                   "label", "atc", "dose_mg", "per_day", "daily_mg",
                                                   "ledd_factor", "times"], t_med),
    }
    if include_notes:
        counts["notes"] = _w(dest / "notes.csv", ["pseudonym", "visit_id", "target", "text"], t_notes)
    (dest / "codebook.md").write_text(codebook(forms.values()), encoding="utf-8")
    manifest = {"format": "tappd-research", "format_version": 1,
                "exported_at": datetime.now().isoformat(timespec="seconds"),
                "app_version": APP_VERSION, "pseudonymised": True, "tables": counts,
                "include_notes": include_notes, "include_signals": include_signals}
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False),
                                        encoding="utf-8")
    return manifest


_MEAS_COLS = ("measurement_id", "pseudonym", "visit_id", "recorded_at", "test_type", "hand",
              "duration_s", "source_kind", "capture_kind", "analysed_on", "deidentified",
              "eye_ref_coverage", "mirror", "swap_handedness", "camera", "video_width",
              "video_height", "video_fps", "mediapipe_version", "has_raw", "has_track", "has_clip")


def _measurement_row(pid: str, visit_id, m: dict) -> dict:
    cap = m.get("capture") or {}
    vid = cap.get("video") or {}
    sc = (cap.get("sidecar") or {}) or ((m.get("analysis") or {}).get("sidecar") or {})
    return {
        "measurement_id": m["id"], "pseudonym": pid, "visit_id": visit_id,
        "recorded_at": m["recorded_at"][:19], "test_type": m["test_type"], "hand": m["hand"],
        "duration_s": m["duration_s"], "source_kind": m["source_kind"],
        "capture_kind": cap.get("kind", ""), "analysed_on": m.get("analysed_on", ""),
        "deidentified": int(bool(m.get("deidentified"))), "eye_ref_coverage": m.get("eye_ref_coverage"),
        "mirror": _fmt(cap.get("mirror")) if "mirror" in cap else "",
        "swap_handedness": _fmt(cap.get("swap_handedness")) if "swap_handedness" in cap else "",
        "camera": (cap.get("camera") or {}).get("name", ""),
        "video_width": vid.get("width"), "video_height": vid.get("height"), "video_fps": vid.get("fps"),
        "mediapipe_version": sc.get("mediapipe", ""),
        "has_raw": int(bool(m.get("raw_data_path")) and os.path.isfile(m.get("raw_data_path", ""))),
        "has_track": int(bool(m.get("track_path")) and os.path.isfile(m.get("track_path", ""))),
        "has_clip": int(bool(m.get("clip_path")) and os.path.isfile(m.get("clip_path", ""))),
    }


def _copy_signals(signals_dir: Path, pid: str, m: dict) -> None:
    d = signals_dir / pid
    for kind, suffix in (("raw_data_path", "raw.json"), ("track_path", "track.json")):
        p = m.get(kind, "")
        if p and os.path.isfile(p) and p.lower().endswith(".json"):
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, d / f"m{m['id']}_{suffix}")


def codebook(forms) -> str:
    from ui.feature_meta import FEATURE_META
    from app_settings import APP_VERSION
    lines = [f"# Codebuch — Motryx Forschungsexport (App {APP_VERSION})", "",
             "Alle Tabellen sind pseudonymisiert: `pseudonym` ist die stabile Kennung eines Probanden "
             "(Zuordnung nur lokal in `data/pseudonyms.json`). Zeiten ISO 8601, Dezimalpunkt.", "",
             "## patients.csv", "",
             "| Variable | Bedeutung |", "|---|---|",
             "| sex | m / f / d |", "| birth_year | Geburtsjahr |",
             "| age_at_export | Alter zum Exportzeitpunkt |",
             "| n_visits, n_measurements | Anzahl Sitzungen / Messungen |",
             "| diagnosis_year … family_pd | letzter Stand aus der Anamnese (siehe clinical_long) |", "",
             "## visits.csv", "",
             "| Variable | Bedeutung |", "|---|---|",
             "| visit_id, date | Sitzung |",
             "| hoehn_yahr, updrs3_total, updrs3_state | Stadium, MDS-UPDRS III Gesamt, erhoben im ON/OFF |",
             "| med_state, last_dose_minutes | Zustand bei der Messung, Minuten seit letzter Einnahme |",
             "| ledd_mg | Levodopa-Äquivalenzdosis mg/Tag (Tomlinson 2010, Faktoren in der Masken-YAML) |",
             "| disease_duration_y, moca, falls_12m, freezing, dbs | Erkrankungsdauer, MoCA, Stürze/12 Mon., FoG, THS |", "",
             "## measurements.csv", "",
             "| Variable | Bedeutung |", "|---|---|",
             "| test_type, hand | Paradigma (Schlüssel wie in paradigms/registry), Seite |",
             "| source_kind | leap / webcam / video / mock |",
             "| capture_kind | recording (eigene Aufnahme) / import |",
             "| analysed_on | raw (Roh-Take) / clip (archivierter, ggf. anonymisierter Clip) |",
             "| deidentified | 1 = Gesicht im Archiv-Clip unkenntlich |",
             "| eye_ref_coverage | Anteil Frames mit Augenreferenz (Tremor auf Video) |",
             "| mirror, swap_handedness | Spiegelung / Händigkeits-Flag bei der Aufnahme |",
             "| camera, video_* | Kamera und Aufnahmeformat |",
             "| mediapipe_version | Tracking-Software der Auswertung |",
             "| has_raw, has_track, has_clip | Dateien vorhanden (signals/ bei include_signals) |", "",
             "## features_long.csv", "",
             "`estimated_scale` = 1: mm-Werte aus Kamera-Quellen sind Modellschätzungen (unkalibriert).", "",
             "| feature | Label | Einheit |", "|---|---|---|"]
    for k, (label, unit) in FEATURE_META.items():
        lines.append(f"| {k} | {label} | {unit} |")
    lines += ["", "## clinical_long.csv / medication.csv", "",
              "Eine Zeile je beantwortetem Item (`key`, `concept`, `value`, `label`, `unit`, `type`); "
              "`type` = computed für berechnete Werte. `medication.csv`: eine Zeile je Präparat "
              "(`daily_mg` = Einzeldosis × Einnahmen/Tag).", ""]
    for f in forms:
        lines += [f"### Maske `{f.id}` — {f.name} (Version {f.version})", "",
                  "| key | Label | Typ | Einheit | Bereich / Auswahl | Konzept |", "|---|---|---|---|---|---|"]
        for it in f.items:
            rng = ""
            if it.range:
                rng = f"{it.range[0]:g}–{it.range[1]:g}"
            elif it.choices:
                rng = ", ".join(c.code for c in it.choices)
            lines.append(f"| {it.key} | {it.label} | {it.type} | {it.unit} | {rng} | {it.concept} |")
        for rep in f.repeats:
            for it in rep.items:
                rng = f"{it.range[0]:g}–{it.range[1]:g}" if it.range else (f"Katalog {it.catalog}" if it.catalog else "")
                lines.append(f"| {rep.key}.{it.key} | {it.label} | {it.type} | {it.unit} | {rng} | {rep.concept} |")
        for c in f.computed:
            lines.append(f"| {c.key} | {c.label} | computed ({c.expr}) | {c.unit} | | {c.concept} |")
        lines.append("")
    return "\n".join(lines)
