"""Exports: record serializer, research tables + codebook, report, bundle."""

import csv
import json
import os
import zipfile

import pytest

from clinical.schema import load_form
from clinical.store import save_form_entry
from storage import database as db
from storage.database import (Measurement, Note, Patient, create_session, get_db,
                              save_measurement, save_note, save_patient)

ANSWERS = {"diagnosis_year": 2019, "hoehn_yahr": "2", "updrs3_total": 28, "med_state": "off",
           "onset_side": "right", "nms": ["hyposmia"], "dbs": False,
           "medication": [{"substance": "levodopa", "dose_mg": 100, "per_day": 4},
                          {"substance": "rasagiline", "dose_mg": 1, "per_day": 1}]}


@pytest.fixture()
def qapp():
    """A Qt application for the PDF renderer (this file lives outside tests/ui)."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Temp DB, attachments and pseudonym map; one patient with a full record."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    import storage.attachments as att
    monkeypatch.setattr(att, "ATTACHMENTS_DIR", tmp_path / "attachments")
    conn = get_db()
    p = save_patient(conn, Patient(patient_code="EXP01", first_name="Erika", last_name="Muster",
                                   birth_date="1955-03-02", gender="f"))
    s = create_session(conn, p.id)
    save_form_entry(conn, p.id, s.id, load_form("pd_anamnese"), ANSWERS,
                    recorded_at="2026-09-10T09:00:00")
    # a video measurement with raw JSON, clip, track and provenance
    raw = tmp_path / "raw.json"
    frames = [{"timestamp_us": i * 33333, "hand_type": "right", "palm_position": [0, 0, 0],
               "palm_velocity": [0, 0, 0], "palm_normal": [0, -1, 0],
               "fingers": [{"tip_position": [0, 0, 0]}, {"tip_position": [40 + 30 * (i % 2), 0, 0]}]}
              for i in range(120)]
    raw.write_text(json.dumps({"sample_rate": 30.0, "frames": frames}), encoding="utf-8")
    clip = tmp_path / "seg_001.mp4"; clip.write_bytes(b"\x00" * 100)
    track = tmp_path / "seg_001.track.json"; track.write_text('{"frames": {}}')
    m = Measurement(patient_id=p.id, session_id=s.id, test_type="finger_tapping", hand="right",
                    duration_s=20.0, source_kind="video", raw_data_path=str(raw),
                    recorded_at="2026-09-10T09:30:00")
    m.features = {"mpi": 0.81, "tap_frequency_hz": 3.0, "mean_amplitude_mm": 60.0}
    m.provenance = {"clip_path": str(clip), "track_path": str(track), "deidentified": True,
                    "analysed_on": "raw", "eye_ref_coverage": 0.9,
                    "capture": {"kind": "recording", "mirror": True, "swap_handedness": False,
                                "camera": {"index": 1, "name": "OBSBOT"},
                                "video": {"width": 640, "height": 480, "fps": 30.0},
                                "sidecar": {"mediapipe": "1.0.1"}}}
    m = save_measurement(conn, m)
    m2 = Measurement(patient_id=p.id, session_id=s.id, test_type="finger_tapping", hand="left",
                     duration_s=20.0, source_kind="video", recorded_at="2026-09-10T09:40:00")
    m2.features = {"mpi": 0.7}
    m2.provenance = {"clip_path": str(clip), "deidentified": False, "analysed_on": "clip"}
    m2 = save_measurement(conn, m2)
    photo = tmp_path / "foto.jpg"; photo.write_bytes(b"jpg")
    entry = att.add_attachment("EXP01", "measurement", str(m.id), str(photo))
    save_note(conn, Note(patient_id=p.id, session_id=s.id, kind="measurement", ref=str(m.id),
                         text="Patientin nervös", attachments=[entry]))
    save_note(conn, Note(patient_id=p.id, session_id=s.id, kind="session", ref=str(s.id),
                         text="OFF-Messung"))
    conn.close()
    return {"tmp": tmp_path, "patient": p, "session": s, "m": m, "m2": m2, "raw": raw}


def test_record_serializer_with_and_without_pseudonym(env):
    from export.record import build_record, files_of
    conn = get_db()
    rec = build_record(conn, env["patient"])
    pseudo = build_record(conn, env["patient"], pseudonymise=True)
    conn.close()
    assert rec["patient"]["id"] == "EXP01" and rec["patient"]["last_name"] == "Muster"
    assert pseudo["patient"]["id"] == "P-0001" and "last_name" not in pseudo["patient"]
    assert pseudo["patient"]["birth_year"] == "1955" and "birth_date" not in pseudo["patient"]
    (s,) = rec["sessions"]
    assert s["forms"][0]["summary"] == "H&Y 2  ·  UPDRS III 28  ·  LEDD 500 mg  ·  OFF"
    assert s["note"]["text"] == "OFF-Messung"
    m = next(x for x in s["measurements"] if x["id"] == env["m"].id)
    assert m["analysed_on"] == "raw" and m["capture"]["camera"]["name"] == "OBSBOT"
    assert m["note"]["attachments"][0]["name"] == "foto.jpg"
    kinds = sorted(k for k, _ in files_of(rec))
    assert kinds == ["attachment", "clip_path", "clip_path", "raw_data_path", "track_path"]


def test_pseudonyms_are_stable_and_stay_local(env):
    from export.pseudonyms import load_mapping, mapping_path, pseudonym_for
    assert pseudonym_for("EXP01") == "P-0001" and pseudonym_for("EXP01") == "P-0001"
    assert pseudonym_for("ZZ") == "P-0002"
    assert mapping_path().parent == env["tmp"] and load_mapping() == {"EXP01": "P-0001", "ZZ": "P-0002"}


def _read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_research_export_writes_long_tables_and_codebook(env):
    from export.research import export_research
    conn = get_db()
    manifest = export_research(conn, env["tmp"] / "research", include_notes=True,
                               include_signals=True)
    conn.close()
    d = env["tmp"] / "research"
    assert manifest["pseudonymised"] and manifest["tables"]["patients"] == 1

    (pat,) = _read(d / "patients.csv")
    assert pat["pseudonym"] == "P-0001" and pat["diagnosis_year"] == "2019" and pat["onset_side"] == "right"
    assert "Muster" not in (d / "patients.csv").read_text(encoding="utf-8")

    (vis,) = _read(d / "visits.csv")
    assert vis["hoehn_yahr"] == "2" and vis["ledd_mg"] == "500.0" and vis["med_state"] == "off"
    assert vis["n_measurements"] == "2" and vis["dbs"] == "0"

    meas = {r["measurement_id"]: r for r in _read(d / "measurements.csv")}
    r = meas[str(env["m"].id)]
    assert r["analysed_on"] == "raw" and r["deidentified"] == "1" and r["camera"] == "OBSBOT"
    assert r["mediapipe_version"] == "1.0.1" and r["has_raw"] == "1" and r["has_track"] == "1"
    assert r["mirror"] == "1" and r["video_width"] == "640"

    feats = _read(d / "features_long.csv")
    amp = next(x for x in feats if x["feature"] == "mean_amplitude_mm")
    assert amp["value"] == "60.0" and amp["unit"] == "mm" and amp["estimated_scale"] == "1"
    assert next(x for x in feats if x["feature"] == "tap_frequency_hz")["estimated_scale"] == "0"

    clin = {r["key"]: r for r in _read(d / "clinical_long.csv")}
    assert clin["hoehn_yahr"]["concept"] == "TAPPD:HOEHN_YAHR" and clin["nms"]["value"] == "hyposmia"
    assert clin["ledd_mg"]["type"] == "computed" and clin["ledd_mg"]["value"] == "500.0"

    med = _read(d / "medication.csv")
    assert [m["substance"] for m in med] == ["levodopa", "rasagiline"]
    assert med[0]["daily_mg"] == "400.0" and med[0]["atc"] == "N04BA02" and med[1]["ledd_factor"] == "100"

    notes = _read(d / "notes.csv")
    assert {n["target"] for n in notes} == {"session", f"measurement:{env['m'].id}"}
    assert (d / "signals" / "P-0001" / f"m{env['m'].id}_raw.json").is_file()
    assert (d / "signals" / "P-0001" / f"m{env['m'].id}_track.json").is_file()

    book = (d / "codebook.md").read_text(encoding="utf-8")
    assert "| tap_frequency_hz | Tapping-Frequenz | Hz |" in book
    assert "### Maske `pd_anamnese`" in book and "| medication.substance |" in book
    assert json.loads((d / "manifest.json").read_text(encoding="utf-8"))["tables"]["medication"] == 2


def test_curve_png_from_raw_json(env):
    from export.curves import curve_png
    png = curve_png("finger_tapping", str(env["raw"]))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert curve_png("finger_tapping", str(env["tmp"] / "missing.json")) is None
    assert curve_png("tower_of_hanoi", str(env["raw"])) is None


def test_report_html_contains_clinic_measurements_and_notes(env):
    from export.record import build_record
    from export.report import render_html
    conn = get_db(); rec = build_record(conn, env["patient"]); conn.close()
    html = render_html(rec, {env["m"].id: b"\x89PNG\r\n\x1a\nxx"})
    assert "Motryx-Bericht: EXP01 – Muster, Erika" in html
    assert "Parkinson-Anamnese" in html and "LEDD (mg/Tag)" in html and "500 mg" in html
    assert "MPI 0.81" in html and "Tapping-Frequenz" in html and "≈mm" in html
    assert "Patientin nervös" in html and "foto.jpg" in html and "data:image/png;base64" in html
    assert "ausgewertet auf Roh-Take" in html and "Clip anonymisiert" in html


def test_bundle_zip_has_report_media_and_verifiable_manifest(env, qapp):
    from export.bundle import BundleOptions, export_bundle, verify_bundle
    conn = get_db()
    dest = env["tmp"] / "out" / "EXP01.zip"
    r = export_bundle(conn, env["patient"], dest, BundleOptions(only_defaced=True, pdf=True))
    conn.close()
    assert r.path == str(dest) and dest.is_file()
    names = set(r.files)
    assert {"manifest.json", "report.json", "report.html"} <= names
    assert r.pdf_written and "report.pdf" in names
    assert r.n_videos == 1 and r.skipped_videos == 1          # the un-defaced clip stays out
    assert f"raw/m{env['m'].id}.json" in names
    assert any(n.startswith("tracks/") for n in names)
    assert f"attachments/messung_{env['m'].id}/foto.jpg" in names
    assert verify_bundle(dest) == []
    with zipfile.ZipFile(dest) as zf:
        rec = json.loads(zf.read("report.json"))
        m = next(x for s in rec["sessions"] for x in s["measurements"] if x["id"] == env["m"].id)
        assert m["files"]["video"].startswith("videos/") and "clip_path" not in m
        assert m["note"]["attachments"][0]["file"].startswith("attachments/")
        assert "path" not in m["note"]["attachments"][0]
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["patient"] == "EXP01" and manifest["skipped_videos"] == 1
        assert b"Muster" in zf.read("report.html")

    # pseudonymised, without videos
    conn = get_db()
    r2 = export_bundle(conn, env["patient"], env["tmp"] / "out" / "p.zip",
                       BundleOptions(pseudonymise=True, include_videos=False, pdf=False))
    conn.close()
    with zipfile.ZipFile(r2.path) as zf:
        assert b"Muster" not in zf.read("report.html") and b"P-0001" in zf.read("report.html")
        assert not any(n.startswith("videos/") for n in zf.namelist())
        assert "report.pdf" not in zf.namelist()
