"""Provenance of a recording: what was filmed how, and what became of it.

Every video that ends up as a measurement passes three stations, and each one
leaves its trace on the ``Segment``:

* **capture** — ``Segment.meta``: own recording (which camera, resolution,
  mirror / handedness settings at that moment, take number) or import
  (original file, per-video mirror flag, the cut range);
* **archive** — ``Segment.meta["archive"]``: the compact clip (deface mode,
  codec/CRF, size) plus ``Segment.deidentified``;
* **analysis** — ``Segment.results[paradigm]``: when, on which file
  (``analysed_on``: raw take or archived clip), the raw-data JSON, the
  per-frame track, the measurement it was exported to.

The measurement in the clinical DB carries a copy of all this as
``Measurement.provenance`` (see :func:`build_provenance`), so the record
explains itself without the video session at hand.

The ``describe_*`` functions turn that into label/value rows for the info
panels; the ``*_issues`` functions are the consistency check the panels show
as warnings — the place to look when a clip, track or measurement does not
line up with the rest.
"""

from __future__ import annotations

import os
from datetime import datetime

KIND_RECORDING = "recording"
KIND_IMPORT = "import"

_HAND = {"left": "links", "right": "rechts", "both": "beide"}
_YES_NO = {True: "ja", False: "nein"}


# ── writing ────────────────────────────────────────────────────────

def capture_meta(device, *, take: int, take_file: str) -> dict:
    """What we know about a take the moment it starts (own recording)."""
    from capture.config import source_mirrored, source_swap_handedness
    cams = {}
    try:
        cams = dict(getattr(device, "_cameras", []) or [])
    except Exception:
        cams = {}
    index = getattr(device, "camera_index", None)
    name = getattr(device, "camera_name", "") or cams.get(index, "")
    return {
        "kind": KIND_RECORDING,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "source_kind": "webcam",
        "camera": {"index": index, "name": name},
        "mirror": source_mirrored("webcam"),
        "swap_handedness": source_swap_handedness("webcam"),
        "face_tracking": bool(getattr(device, "_face_on", False)),
        "take": int(take),
        "take_file": os.path.basename(take_file) if take_file else "",
        "sidecar": dict(getattr(device, "sidecar_info", {}) or {}),
    }


def note_recorded(meta: dict, info: dict | None) -> dict:
    """Add what the sidecar reports when the take file is closed."""
    info = info or {}
    if info.get("w") or info.get("frames"):
        meta["video"] = {"width": int(info.get("w") or 0), "height": int(info.get("h") or 0),
                         "fps": float(info.get("fps") or 0.0),
                         "frames": int(info.get("frames") or 0),
                         "codec": str(info.get("codec") or "")}
    return meta


def import_meta(session, seg) -> dict:
    """Provenance of a segment cut from an imported video."""
    return {
        "kind": KIND_IMPORT,
        "imported_at": session.created_at,
        "original_name": session.video_name,
        "source_file": os.path.basename(session.video_path or ""),
        "mirror": bool(session.mirrored),
        "cut": {"start_s": float(seg.start_s), "end_s": float(seg.end_s)},
        "source_kind": "video",
    }


def note_archive(seg, clip, deface: str | None) -> dict:
    """Record the compact clip's facts on the segment (after extraction)."""
    from video.config import cfg
    mode = deface if deface is not None else str(cfg("privacy", "deface", default="blur"))
    size = 0
    try:
        size = os.path.getsize(seg.clip_path) if seg.clip_path else 0
    except OSError:
        pass
    seg.meta["archive"] = {
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "deface": mode if seg.deidentified else "off",
        "codec": str(cfg("import", "codec", default="avc1")),
        "crf": int(cfg("segments", "crf", default=23)),
        "width": int(getattr(clip, "width", 0) or 0),
        "height": int(getattr(clip, "height", 0) or 0),
        "fps": float(getattr(clip, "fps", 0.0) or 0.0),
        "size_bytes": size,
        "eyeref": bool((getattr(clip, "extra", None) or {}).get("eyeref")),
    }
    return seg.meta["archive"]


def analysis_meta(runner, *, mirrored: bool) -> dict:
    """Software and settings the analysis ran with."""
    src = getattr(runner, "_src", None)
    meta = {
        "mirror": bool(mirrored),
        "num_hands": 2,
        "sidecar": dict(getattr(src, "sidecar_info", {}) or {}),
    }
    # Time base (media time at the clip's fps) and frames expected vs. processed
    # — what makes a result auditable for a truncated or odd-rate clip.
    timing = getattr(runner, "timing", None)
    timing = timing() if callable(timing) else None
    if isinstance(timing, dict) and timing:
        meta["timing"] = timing
    return meta


def build_provenance(session, seg, result: dict) -> dict:
    """The copy of the segment's provenance a Measurement carries."""
    return {
        "video_session": session.path or "",
        "db_session_id": session.db_session_id,
        "segment_id": seg.id,
        "clip_path": seg.clip_path or "",
        "track_path": seg.track_path or "",
        "deidentified": bool(seg.deidentified),
        "capture": dict(seg.meta or {}),
        "analysed_at": result.get("recorded_at", ""),
        "analysed_on": result.get("analysed_on", ""),
        "analysis": dict(result.get("analysis") or {}),
        "eye_ref_coverage": result.get("eye_ref_coverage"),
    }


# ── formatting helpers ─────────────────────────────────────────────

def _dt(s: str) -> str:
    return (s or "")[:16].replace("T", " ") or "–"


def _size(n: int) -> str:
    if not n:
        return "–"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    return f"{n / 1000:.0f} kB"


def _video_line(v: dict) -> str:
    if not v:
        return ""
    parts = []
    if v.get("width"):
        parts.append(f"{v['width']}×{v['height']}")
    if v.get("fps"):
        parts.append(f"{v['fps']:g} fps")
    if v.get("frames"):
        parts.append(f"{v['frames']} Frames")
    if v.get("codec"):
        parts.append(v["codec"])
    return "  ·  ".join(parts)


def _file(path: str) -> str:
    if not path:
        return "–"
    name = os.path.basename(path)
    return name if os.path.isfile(path) else f"{name} (fehlt)"


def _track_frames(path: str) -> int | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        import json
        with open(path, encoding="utf-8") as f:
            return len(json.load(f).get("frames", {}))
    except Exception:
        return None


# ── describing ─────────────────────────────────────────────────────

def describe_capture(meta: dict) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if not meta:
        return [("Aufnahme", "keine Metadaten (älterer Stand)")]
    kind = meta.get("kind")
    if kind == KIND_RECORDING:
        cam = meta.get("camera") or {}
        cam_txt = cam.get("name") or (f"Index {cam['index']}" if cam.get("index") is not None else "–")
        rows.append(("Art", "eigene Aufnahme (Kamera-Stream)"))
        rows.append(("Aufgenommen", _dt(meta.get("recorded_at", ""))))
        rows.append(("Kamera", cam_txt))
        if meta.get("video"):
            rows.append(("Video", _video_line(meta["video"])))
        if meta.get("take"):
            rows.append(("Take", f"Nr. {meta['take']}" + (f"  ·  {meta['take_file']}" if meta.get("take_file") else "")))
    elif kind == KIND_IMPORT:
        rows.append(("Art", "Import"))
        rows.append(("Importiert", _dt(meta.get("imported_at", ""))))
        rows.append(("Originaldatei", meta.get("original_name") or "–"))
        cut = meta.get("cut") or {}
        if cut:
            rows.append(("Ausschnitt", f"{cut.get('start_s', 0):.1f} s – {cut.get('end_s', 0):.1f} s"))
    else:
        rows.append(("Art", str(kind or "unbekannt")))
    if "mirror" in meta:
        rows.append(("Gespiegelt", _YES_NO[bool(meta["mirror"])] + " (Anzeige und Analyse)"))
    if "swap_handedness" in meta:
        rows.append(("Händigkeit vertauscht", _YES_NO[bool(meta["swap_handedness"])]))
    if "face_tracking" in meta:
        rows.append(("Gesichts-Tracking bei Aufnahme", _YES_NO[bool(meta["face_tracking"])]))
    sc = meta.get("sidecar") or {}
    if sc.get("mediapipe"):
        rows.append(("Software", f"MediaPipe {sc['mediapipe']}  ·  OpenCV {sc.get('opencv', '?')}"))
    return rows


def describe_segment(session, seg, step=None) -> list[tuple[str, str]]:
    """Label/value rows for the info panel of one take / segment."""
    rows = describe_capture(seg.meta or {})
    arch = (seg.meta or {}).get("archive") or {}
    if seg.clip_path:
        txt = _file(seg.clip_path)
        if arch:
            extra = _video_line({"width": arch.get("width"), "height": arch.get("height"),
                                 "fps": arch.get("fps"), "codec": arch.get("codec")})
            txt += f"  ·  {_size(arch.get('size_bytes', 0))}"
            if extra:
                txt += f"  ·  {extra}"
            if arch.get("crf"):
                txt += f"  ·  CRF {arch['crf']}"
        rows.append(("Archiv-Clip", txt))
    if seg.recorded:
        raw = seg.source_path
        rows.append(("Roh-Take", (_file(raw) if raw and os.path.isfile(raw)
                                   else "aufgeräumt (nur Archiv-Clip)")))
    if arch:
        mode = arch.get("deface", "off")
        rows.append(("Anonymisiert", {"blur": "ja (Gesicht verwischt)", "mesh": "ja (Gesichtsmaske)",
                                      "off": "nein"}.get(mode, mode)))
    elif seg.clip_path:
        rows.append(("Anonymisiert", "ja" if seg.deidentified else "nein"))
    n = _track_frames(seg.track_path)
    rows.append(("Tracking-Spur", f"{n} Frames  ·  {os.path.basename(seg.track_path)}"
                 if n is not None else ("fehlt" if seg.results else "noch keine (erst nach Auswertung)")))
    if seg.results:
        for key, res in seg.results.items():
            res = res or {}
            on = {"raw": "Roh-Take", "clip": "archivierter Clip",
                  "import": "importiertes Original"}.get(res.get("analysed_on"), "–")
            line = f"{_dt(res.get('recorded_at', ''))}  ·  auf: {on}"
            if res.get("measurement_id"):
                line += f"  ·  Messung #{res['measurement_id']}"
            else:
                line += "  ·  nicht in der Akte"
            if res.get("raw_path"):
                line += f"  ·  Rohdaten: {_file(res['raw_path'])}"
            cov = res.get("eye_ref_coverage")
            if isinstance(cov, (int, float)):
                line += f"  ·  Augenreferenz {cov * 100:.0f} %"
            an = res.get("analysis") or {}
            if an.get("sidecar", {}).get("mediapipe"):
                line += f"  ·  MediaPipe {an['sidecar']['mediapipe']}"
            rows.append((f"Auswertung {key} ({_HAND.get(seg.hand, seg.hand)})", line))
    else:
        rows.append(("Auswertung", "noch keine"))
    return rows


def describe_measurement(m) -> list[tuple[str, str]]:
    """Rows for the detail dialog of a measurement, from its provenance."""
    prov = getattr(m, "provenance", None) or {}
    rows: list[tuple[str, str]] = []
    if not prov:
        rows.append(("Herkunft", f"Quelle: {m.source_kind or '–'}  ·  keine Video-Metadaten"
                     + ("" if m.source_kind in ("webcam", "leap", "mock") else " (älterer Stand)")))
        if str(getattr(m, "test_type", "")) in ("saccade_test", "ocular_fixation"):
            rows.append(("Video", "Live-Okulomotorik-Test: es wird kein Video gespeichert, nur "
                                  "die Gesichts-Samples (Rohdaten). Ein Kamerabild zeichnet "
                                  "der Debug-Modus des Sakkaden-Tests auf (data/debug/saccade)."))
        rows.append(("Rohdaten", _file(m.raw_data_path)))
        return rows
    rows += describe_capture(prov.get("capture") or {})
    if prov.get("clip_path"):
        rows.append(("Archiv-Clip", _file(prov["clip_path"])
                     + ("  ·  anonymisiert" if prov.get("deidentified") else "")))
    on = {"raw": "Roh-Take", "clip": "archivierter Clip",
          "import": "importiertes Original"}.get(prov.get("analysed_on"), "–")
    rows.append(("Ausgewertet", f"{_dt(prov.get('analysed_at', ''))}  ·  auf: {on}"))
    n = _track_frames(prov.get("track_path", ""))
    rows.append(("Tracking-Spur", f"{n} Frames" if n is not None else "fehlt"))
    rows.append(("Rohdaten", _file(m.raw_data_path)))
    cov = prov.get("eye_ref_coverage")
    if isinstance(cov, (int, float)):
        rows.append(("Augenreferenz", f"{cov * 100:.0f} % der Frames"))
    if prov.get("video_session"):
        rows.append(("Video-Session", f"{os.path.basename(os.path.dirname(prov['video_session']))}"
                     f"  ·  Segment {prov.get('segment_id', '–')}"))
    return rows


def result_summary(seg) -> str:
    """One line per stored analysis: when, in the record?, on what, MPI and
    the first feature values — the same text under a take and a cut segment."""
    from ui.feature_meta import FEATURE_META
    parts = []
    for key, res in (seg.results or {}).items():
        feats = (res or {}).get("features") or {}
        if not feats:
            continue
        shown = []
        mpi = feats.get("mpi")
        if isinstance(mpi, (int, float)):
            shown.append(f"MPI {mpi:.2f}")
        for k, v in feats.items():
            if k == "mpi" or k.startswith("_") or not isinstance(v, (int, float)):
                continue
            label, unit = FEATURE_META.get(k, (k, ""))
            shown.append(f"{label} {v:.2f}{(' ' + unit) if unit else ''}")
            if len(shown) >= 4:
                break
        when = (res.get("recorded_at") or "")[:16].replace("T", " ")
        src = {"clip": " · auf dem archivierten Clip" + (" (Gesicht unkenntlich)" if seg.deidentified else ""),
               "import": " · auf dem importierten Original"}.get(res.get("analysed_on"), "")
        where = " · in der Akte" if res.get("measurement_id") else ""
        parts.append(f"✔ Ausgewertet {when}{where}{src}  —  " + "  ·  ".join(shown))
    return "\n".join(parts)


# ── consistency ────────────────────────────────────────────────────

def segment_issues(session, seg, step=None, known_measurements: set | None = None) -> list[str]:
    """Things that do not line up for this take. Empty = consistent."""
    out: list[str] = []
    meta = seg.meta or {}
    if not meta:
        out.append("Keine Aufnahme-Metadaten (vor dieser Version aufgenommen).")
    if seg.clip_path and not os.path.isfile(seg.clip_path):
        out.append("Clip-Datei fehlt auf der Platte.")
    if seg.recorded and not meta.get("archive") and seg.clip_path \
            and seg.source_path and seg.clip_path == seg.source_path:
        out.append("Take ist nicht archiviert (nur der Roh-Take liegt vor).")
    if seg.recorded and meta.get("archive") and not seg.deidentified \
            and (meta["archive"].get("deface") or "off") != "off":
        out.append("Archiv sollte anonymisiert sein, ist aber nicht markiert.")
    if step is not None:
        if step.hand != seg.hand:
            out.append(f"Seite weicht ab: Schritt {step.hand}, Segment {seg.hand}.")
        if step.paradigm and step.paradigm != seg.paradigm:
            out.append(f"Paradigma weicht ab: Schritt {step.paradigm}, Segment {seg.paradigm}.")
    if seg.results:
        if not (seg.track_path and os.path.isfile(seg.track_path)):
            out.append("Keine Tracking-Spur zur Auswertung — das Overlay würde neu rechnen.")
        for key, res in seg.results.items():
            res = res or {}
            if not res.get("features"):
                out.append(f"Auswertung {key} ohne Kennwerte.")
            if not res.get("measurement_id"):
                out.append(f"Auswertung {key} ist nicht in der Akte.")
            elif known_measurements is not None and int(res["measurement_id"]) not in known_measurements:
                out.append(f"Messung #{res['measurement_id']} existiert nicht mehr in der Akte.")
            if res.get("raw_path") and not os.path.isfile(res["raw_path"]):
                out.append(f"Rohdaten-Datei der Auswertung {key} fehlt.")
            if res.get("analysed_on") == "clip" and seg.deidentified \
                    and _needs_eye_reference(key):
                out.append("Auf dem anonymisierten Clip ausgewertet — Augenreferenz fehlt "
                           "(Absolutwerte nur geschätzt).")
    return out


def measurement_issues(m) -> list[str]:
    out: list[str] = []
    prov = getattr(m, "provenance", None) or {}
    if m.raw_data_path and not os.path.isfile(m.raw_data_path):
        out.append("Rohdaten-Datei fehlt.")
    elif m.raw_data_path and not m.raw_data_path.lower().endswith(".json"):
        out.append("Nur der Video-Clip ist hinterlegt, keine Rohdaten — nach „Neu auswerten“ vorhanden.")
    if prov:
        if prov.get("clip_path") and not os.path.isfile(prov["clip_path"]):
            out.append("Archiv-Clip fehlt auf der Platte.")
        if prov.get("track_path") and not os.path.isfile(prov["track_path"]):
            out.append("Tracking-Spur fehlt.")
        cap = prov.get("capture") or {}
        if cap.get("kind") == KIND_RECORDING and cap.get("mirror") is False:
            out.append("Ungespiegelt aufgenommen — Seitenzuordnung prüfen.")
    elif m.source_kind == "video":
        out.append("Video-Messung ohne Herkunftsdaten (älterer Stand).")
    return out


def _needs_eye_reference(paradigm: str) -> bool:
    try:
        from capture.source import CAP_ABS_POSITION
        from paradigms.config import get_task_requirements
        return CAP_ABS_POSITION in get_task_requirements(paradigm)
    except Exception:
        return False
