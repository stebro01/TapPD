"""Debug aids for the saccade test: live preview + tracking quality, and a
recording (video + sample log) of the run so it can be replayed offline.

* ``quality(faces, task, ...)`` — the numbers that decide whether a
  calibration can succeed: face rate, IPD in pixels (resolution of the gaze
  signal), gaze spread over the last window vs. ``max_spread_ipd``, EAR,
  head roll / nose shift, plus plain-language warnings.
* ``SaccadeDebugLog`` — every face sample with phase and calibration point,
  written as JSON next to a video the sidecar records in parallel; the
  calibration references, fail reason and hits go into the file at the end.
* ``replay_log(path)`` — feed a log back into a fresh ``SaccadeTask`` (the
  headless logic) — what "nachbauen" needs: same samples, same decisions.
"""

from __future__ import annotations

import json
import logging
import math
import os
import statistics
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ui import theme
from ui.widgets.webcam_preview import WebcamPreview

log = logging.getLogger(__name__)


def debug_dir() -> Path:
    from storage.database import DB_PATH
    return Path(DB_PATH).parent / "debug" / "saccade"


# ── quality numbers ────────────────────────────────────────────────

def quality(faces: list, task, window_s: float = 1.4, min_ipd_px: float = 45.0) -> dict:
    """Tracking quality over the last ``window_s`` seconds of face samples."""
    out = {"n": len(faces), "rate_hz": 0.0, "ipd_px": 0.0, "spread": None, "ear": 0.0,
           "roll_deg": 0.0, "nose": None, "warnings": []}
    if not faces:
        out["warnings"].append("Kein Gesicht erkannt — frontal, gut beleuchtet, ganz im Bild?")
        return out
    t_last = faces[-1].timestamp_us
    win = [f for f in faces if t_last - f.timestamp_us <= window_s * 1e6]
    if len(win) >= 2:
        span = (win[-1].timestamp_us - win[0].timestamp_us) / 1e6
        out["rate_hz"] = (len(win) - 1) / span if span > 0 else 0.0
    out["ipd_px"] = statistics.median(f.ipd_px for f in win)
    out["ear"] = statistics.median(f.ear for f in win)
    out["roll_deg"] = statistics.median(f.eye_roll_deg for f in win)
    noses = [f.nose_shift_ipd for f in win if f.nose_shift_ipd is not None]
    out["nose"] = statistics.median(noses) if noses else None
    offs = [f.gaze_offset_ipd for f in win]
    if len(offs) >= 5:
        cx = sum(o[0] for o in offs) / len(offs)
        cy = sum(o[1] for o in offs) / len(offs)
        out["spread"] = max(math.hypot(o[0] - cx, o[1] - cy) for o in offs)
    thr = float(getattr(task, "calib_max_spread", 0.06))
    if out["ipd_px"] and out["ipd_px"] < min_ipd_px:
        out["warnings"].append(
            f"IPD nur {out['ipd_px']:.0f} px — näher an die Kamera oder höhere Auflösung "
            "(capture.yaml camera_width 1280): 1 px Iris-Versatz sind hier "
            f"{100 / out['ipd_px']:.1f} % IPD, Eich-Trennschwelle ist 4 %.")
    if out["rate_hz"] and out["rate_hz"] < 15:
        out["warnings"].append(f"Nur {out['rate_hz']:.0f} Gesichts-Samples/s — CPU-Last?")
    if out["spread"] is not None and out["spread"] > thr:
        out["warnings"].append(f"Blick unruhig: Streuung {out['spread']:.3f} > {thr:.2f} IPD "
                               "(Kopf still, Punkt ruhig fixieren)")
    if out["ear"] and out["ear"] < 0.2:
        out["warnings"].append("Lidspalte klein (EAR < 0.20) — Blinzeln/zusammengekniffen?")
    return out


def quality_text(q: dict, phase: str = "", point: str | None = None) -> str:
    head = f"Phase: {phase or '–'}" + (f"  ·  Punkt {point}" if point else "")
    if not q["n"]:
        return head + "\n" + "\n".join(f"⚠ {w}" for w in q["warnings"])
    spread = "–" if q["spread"] is None else f"{q['spread']:.3f}"
    nose = "–" if q["nose"] is None else f"{q['nose']:+.2f}"
    lines = [head,
             f"Gesicht {q['rate_hz']:.0f} Hz  ·  IPD {q['ipd_px']:.0f} px  ·  EAR {q['ear']:.2f}",
             f"Blick-Streuung {spread} IPD  ·  Roll {q['roll_deg']:+.1f}°  ·  Nase {nose} IPD"]
    lines += [f"⚠ {w}" for w in q["warnings"]]
    return "\n".join(lines)


# ── the panel ──────────────────────────────────────────────────────

class SaccadeDebugPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.preview = WebcamPreview()
        self.preview.setFixedSize(300, 225)
        self.preview.set_placeholder("Vorschau folgt mit dem Start …")
        lay.addWidget(self.preview)
        self.text = QLabel("")
        self.text.setWordWrap(True)
        self.text.setStyleSheet(f"font-size: 11px; color: {theme.TEXT_SECONDARY};")
        self.text.setFixedWidth(300)
        lay.addWidget(self.text)
        self.files = QLabel("")
        self.files.setWordWrap(True)
        self.files.setStyleSheet(f"font-size: 10px; color: {theme.TEXT_SECONDARY};")
        self.files.setFixedWidth(300)
        lay.addWidget(self.files)
        lay.addStretch()
        self._latest = None

    def on_preview(self, msg: dict) -> None:      # reader thread: store only
        self._latest = msg

    def draw(self) -> None:
        msg, self._latest = self._latest, None
        if msg is not None:
            self.preview.set_frame(msg.get("jpeg", ""), msg.get("landmarks", []),
                                   msg.get("face", []))

    def show_quality(self, q: dict, phase: str, point: str | None) -> None:
        self.text.setText(quality_text(q, phase, point))
        self.text.setStyleSheet(
            f"font-size: 11px; color: {theme.WARN_DARK if q['warnings'] else theme.TEXT_SECONDARY};")


# ── the log ────────────────────────────────────────────────────────

class SaccadeDebugLog:
    """Samples + decisions of one run; video recorded by the sidecar alongside."""

    def __init__(self, patient_code: str, capture, record_seconds: float) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        d = debug_dir()
        d.mkdir(parents=True, exist_ok=True)
        stem = d / f"{patient_code or 'proband'}_{stamp}"
        self.json_path = str(stem) + ".json"
        self.video_path = str(stem) + ".mp4"
        self.samples: list[dict] = []
        self._n_seen = 0
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.video_requested = False
        rec = getattr(capture, "record_clip", None)
        if rec is not None:
            try:
                rec(self.video_path, float(record_seconds))
                self.video_requested = True
            except Exception:
                log.debug("Debug-Video konnte nicht gestartet werden", exc_info=True)

    def collect(self, test) -> None:
        """Take the face samples that arrived since the last call."""
        with test._lock:
            new = test.face_frames[self._n_seen:]
            self._n_seen = len(test.face_frames)
            t0 = test._t0_us
        task = test.task
        for f in new:
            ox, oy = f.gaze_offset_ipd
            self.samples.append({
                "t": round(((f.timestamp_us - t0) / 1e6) if t0 is not None else 0.0, 4),
                "ox": round(ox, 5), "oy": round(oy, 5), "ear": round(f.ear, 4),
                "ipd_px": round(f.ipd_px, 2), "roll": round(f.eye_roll_deg, 3),
                "nose": None if f.nose_shift_ipd is None else round(f.nose_shift_ipd, 4),
                "phase": task.phase.name, "point": task.calib_point,
            })

    def finish(self, test, features: dict | None = None) -> str:
        task = test.task
        data = {
            "format": "tappd-saccade-debug", "version": 1, "started_at": self.started_at,
            "video": os.path.basename(self.video_path) if self.video_requested else "",
            "config": {k: getattr(task, k) for k in (
                "calib_per_point_s", "calib_settle_s", "calib_min_samples", "calib_max_spread",
                "duration_s", "dwell_s", "confidence_margin", "min_separation", "blink_ear",
                "guard_max_roll_deg", "guard_max_ipd_change", "guard_max_nose_shift",
                "layout", "sequence", "calib_visits", "min_separation_snr")
                if hasattr(task, k)},
            "result": {"phase": task.phase.name, "fail_reason": getattr(task, "fail_reason", ""),
                       "references": {k: list(v) for k, v in getattr(task, "references", {}).items()},
                       "noise": dict(getattr(task, "noise", {})),
                       "hits": len(getattr(task, "hits", [])),
                       "samples_total": getattr(task, "samples_total", 0),
                       "samples_blink": getattr(task, "samples_blink", 0)},
            "features": features or {},
            "samples": self.samples,
        }
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=0, default=str)
        return self.json_path


def replay_log(path: str, **task_overrides):
    """Run the logged samples through a fresh SaccadeTask (headless) and
    return it — same decisions as live, or different ones with overrides
    (e.g. ``calib_max_spread=0.1``) to test a threshold change."""
    from paradigms.saccade_logic import SaccadeTask
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cfg = dict(data.get("config") or {})
    cfg.update(task_overrides)
    task = SaccadeTask(**cfg)
    samples = data.get("samples") or []
    if samples:
        task.start(samples[0]["t"])
    for s in samples:
        task.update(s["t"], (s["ox"], s["oy"]), s["ear"], roll_deg=s.get("roll", 0.0),
                    ipd_px=s.get("ipd_px", 0.0), nose_shift=s.get("nose"))
    return task
