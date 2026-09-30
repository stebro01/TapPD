"""Hand-over bundle: one ZIP with report, videos, tracks, raw data, manifest.

    <code>_<date>.zip
    ├─ manifest.json      contents, app version, SHA-256 per file
    ├─ report.json        the record (export.record)
    ├─ report.html        the report, self-contained
    ├─ report.pdf         same, when a Qt application is running
    ├─ videos/<session>/<seg>.mp4
    ├─ tracks/<session>/<seg>.track.json
    ├─ raw/m<id>.json
    └─ attachments/<target>/<file>
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from storage.database import Patient


@dataclass
class BundleOptions:
    pseudonymise: bool = False
    include_videos: bool = True
    only_defaced: bool = True
    include_tracks: bool = True
    include_raw: bool = True
    include_attachments: bool = True
    include_notes: bool = True
    pdf: bool = True


@dataclass
class BundleResult:
    path: str
    files: list[str] = field(default_factory=list)
    skipped_videos: int = 0
    pdf_written: bool = False

    @property
    def n_videos(self) -> int:
        return sum(1 for f in self.files if f.startswith("videos/"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _session_dir(m: dict) -> str:
    return f"s{m['session_id']}" if m.get("session_id") else "ohne_sitzung"


def export_bundle(conn: sqlite3.Connection, patient: Patient, dest_zip: str | Path,
                  options: BundleOptions | None = None) -> BundleResult:
    from export.curves import curve_png
    from export.record import build_record
    from export.report import render_html, render_pdf
    from app_settings import APP_VERSION

    opt = options or BundleOptions()
    record = build_record(conn, patient, pseudonymise=opt.pseudonymise,
                          include_notes=opt.include_notes, include_paths=True)
    dest_zip = Path(dest_zip)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    result = BundleResult(path=str(dest_zip))
    hashes: dict[str, str] = {}

    all_measurements = [m for s in record["sessions"] for m in s["measurements"]] \
        + list(record["unassigned_measurements"])

    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        def put(arcname: str, data: bytes) -> None:
            zf.writestr(arcname, data)
            hashes[arcname] = _sha256(data)
            result.files.append(arcname)

        def put_file(arcname: str, path: str) -> None:
            with open(path, "rb") as f:
                data = f.read()
            put(arcname, data)

        # media and data files — rewrite the paths inside the record to the
        # archive names, so report.json is self-contained
        for m in all_measurements:
            sd = _session_dir(m)
            clip = m.pop("clip_path", "")
            track = m.pop("track_path", "")
            raw = m.pop("raw_data_path", "")
            m["files"] = {}
            if opt.include_videos and clip and os.path.isfile(clip):
                if opt.only_defaced and not m.get("deidentified"):
                    result.skipped_videos += 1
                else:
                    arc = f"videos/{sd}/{os.path.basename(clip)}"
                    if arc not in hashes:
                        put_file(arc, clip)
                    m["files"]["video"] = arc
            if opt.include_tracks and track and os.path.isfile(track):
                arc = f"tracks/{sd}/{os.path.basename(track)}"
                if arc not in hashes:
                    put_file(arc, track)
                m["files"]["track"] = arc
            if opt.include_raw and raw and os.path.isfile(raw) and raw.lower().endswith(".json"):
                arc = f"raw/m{m['id']}.json"
                put_file(arc, raw)
                m["files"]["raw"] = arc
            m["_raw_local"] = raw          # for the curves, dropped below
            note = m.get("note")
            if note:
                _attach(zf, put_file, note, f"attachments/messung_{m['id']}", opt)
        for s in record["sessions"]:
            if s.get("note"):
                _attach(zf, put_file, s["note"], f"attachments/sitzung_{s['id']}", opt)

        curves = {}
        for m in all_measurements:
            png = curve_png(m["test_type"], m.pop("_raw_local", ""))
            if png:
                curves[m["id"]] = png

        html_text = render_html(record, curves)
        put("report.html", html_text.encode("utf-8"))
        put("report.json", json.dumps(record, indent=1, ensure_ascii=False, default=str).encode("utf-8"))
        if opt.pdf:
            tmp = dest_zip.with_suffix(".tmp.pdf")
            if render_pdf(html_text, str(tmp)) and tmp.is_file():
                put_file("report.pdf", str(tmp))
                result.pdf_written = True
                try:
                    tmp.unlink()
                except OSError:
                    pass
        manifest = {
            "format": "tappd-bundle", "format_version": 1,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "app_version": APP_VERSION, "patient": record["patient"]["id"],
            "pseudonymised": opt.pseudonymise, "only_defaced_videos": opt.only_defaced,
            "skipped_videos": result.skipped_videos, "files": hashes,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False))
        result.files.append("manifest.json")
    return result


def _attach(zf, put_file, note: dict, folder: str, opt: BundleOptions) -> None:
    if not opt.include_attachments:
        return
    for a in note.get("attachments", []):
        p = a.get("path", "")
        if p and os.path.isfile(p):
            arc = f"{folder}/{a.get('name') or os.path.basename(p)}"
            put_file(arc, p)
            a["file"] = arc
        a.pop("path", None)


def verify_bundle(path: str | Path) -> list[str]:
    """Names of files whose SHA-256 does not match the manifest (empty = intact)."""
    bad = []
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        for name, digest in manifest.get("files", {}).items():
            try:
                if _sha256(zf.read(name)) != digest:
                    bad.append(name)
            except KeyError:
                bad.append(name)
    return bad
