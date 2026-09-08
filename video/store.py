"""VideoLab persistence: a self-contained "video session" per patient.

A video session is a JSON document holding the uploaded video + a list of named
onset/offset segments, each with its analysis results (one per paradigm run).
Kept deliberately separate from the i2b2 star schema (storage/database.py);
chosen results are exported into a real patient Session as Measurements via
video/export.py (the DB session id is remembered here as ``db_session_id``,
and each exported result carries its ``measurement_id``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

VIDEO_SESSIONS_DIR = Path(__file__).parent.parent / "data" / "video_sessions"


@dataclass
class Segment:
    id: str                       # stable id (e.g. "seg_001")
    name: str                     # clinician label, e.g. "Finger Tapping"
    start_s: float
    end_s: float
    paradigm: str = ""            # chosen analysis paradigm key
    hand: str = "right"           # "left" | "right" | "both"
    note: str = ""                # free-text note (added via rename)
    created_at: str = ""          # ISO timestamp when the segment was added
    clip_path: str = ""           # extracted, compressed (defaced) segment clip
    thumb_path: str = ""          # first-frame thumbnail (jpg) of the segment
    deidentified: bool = False    # face blurred/meshed in the segment clip
    # analysis results keyed by paradigm key → {features, recorded_at, raw_path}
    results: dict = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    @property
    def analyzed(self) -> bool:
        return bool(self.results)


@dataclass
class VideoSession:
    patient_id: int
    patient_code: str
    video_path: str = ""          # path to the copied video inside the session dir
    video_name: str = ""          # original filename (display only)
    mirrored: bool = False        # mirror this clip on playback/analysis
    created_at: str = ""
    segments: list[Segment] = field(default_factory=list)
    path: str = ""                # JSON file location (set on save)
    db_session_id: int | None = None   # clinical-DB Session all exports go into

    # ── factory / io ─────────────────────────────────────────────
    @classmethod
    def create(cls, patient_id: int, patient_code: str) -> "VideoSession":
        return cls(patient_id=patient_id, patient_code=patient_code,
                   created_at=datetime.now().isoformat())

    def _dir(self) -> Path:
        return VIDEO_SESSIONS_DIR / (self.patient_code or f"patient_{self.patient_id}")

    def new_video_path(self, ext: str) -> Path:
        """Destination path for an imported video (creates the session dir)."""
        d = self._dir()
        d.mkdir(parents=True, exist_ok=True)
        if not ext.startswith("."):
            ext = "." + ext
        return d / f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"

    def segment_clip_path(self, seg_id: str) -> Path:
        """Destination path for a segment's extracted clip."""
        d = self._dir()
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{seg_id}.mp4"

    def set_video(self, path: str, original_name: str) -> None:
        self.video_path = str(path)
        self.video_name = original_name

    def add_segment(self, name: str, start_s: float, end_s: float,
                    paradigm: str = "", hand: str = "right", note: str = "") -> Segment:
        # Highest existing index + 1 (stable even after deletions).
        n = max((int(s.id.split("_")[-1]) for s in self.segments), default=0) + 1
        seg = Segment(id=f"seg_{n:03d}", name=name,
                      start_s=float(start_s), end_s=float(end_s),
                      paradigm=paradigm, hand=hand, note=note,
                      created_at=datetime.now().isoformat())
        self.segments.append(seg)
        return seg

    def remove_segment(self, seg_id: str) -> None:
        self.segments = [s for s in self.segments if s.id != seg_id]

    def save(self) -> Path:
        d = self._dir()
        d.mkdir(parents=True, exist_ok=True)
        if not self.path:
            self.path = str(d / "session.json")
        payload = {
            "patient_id": self.patient_id,
            "patient_code": self.patient_code,
            "video_path": self.video_path,
            "video_name": self.video_name,
            "mirrored": self.mirrored,
            "created_at": self.created_at,
            "db_session_id": self.db_session_id,
            "segments": [asdict(s) for s in self.segments],
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
        return Path(self.path)

    @classmethod
    def load(cls, path: str) -> "VideoSession":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        vs = cls(patient_id=d["patient_id"], patient_code=d.get("patient_code", ""),
                 video_path=d.get("video_path", ""), video_name=d.get("video_name", ""),
                 mirrored=bool(d.get("mirrored", False)),
                 created_at=d.get("created_at", ""), path=path,
                 db_session_id=d.get("db_session_id"))
        vs.segments = [Segment(**s) for s in d.get("segments", [])]
        return vs


def load_for_patient(patient_id: int, patient_code: str) -> "VideoSession | None":
    """Return the existing video session for a patient, or None."""
    d = VIDEO_SESSIONS_DIR / (patient_code or f"patient_{patient_id}")
    f = d / "session.json"
    if f.is_file():
        try:
            return VideoSession.load(str(f))
        except Exception:
            return None
    return None
