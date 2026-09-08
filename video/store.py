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
from dataclasses import asdict, dataclass, field, fields
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
    # True for a take we filmed ourselves (stored raw → replays under the
    # webcam mirror setting); False for a cut of an imported video (uses the
    # session's per-video flag).
    recorded: bool = False
    # For a recorded take: the raw file it was cut from, kept (or not — see
    # video.yaml archive:) beside the compact clip in ``clip_path``. Analysis
    # prefers this while it exists: full quality, no deface blur.
    source_path: str = ""
    # analysis results keyed by paradigm key → {features, recorded_at, raw_path}
    results: dict = field(default_factory=dict)

    @property
    def analysis_path(self) -> str:
        """Best file to analyse: the raw source if still there, else the clip."""
        import os
        if self.source_path and os.path.isfile(self.source_path):
            return self.source_path
        return self.clip_path

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    @property
    def analyzed(self) -> bool:
        return bool(self.results)


# ── protocol-guided recording ───────────────────────────────────────
# States a step moves through. The clinician confirms every step, and analysis
# happens only afterwards — so "recorded" and "confirmed" are distinct: a take
# exists but has not been looked at yet.
STEP_PENDING = "pending"        # not filmed yet
STEP_RECORDED = "recorded"      # a take exists, awaiting review
STEP_CONFIRMED = "confirmed"    # reviewed and kept → a Segment exists


@dataclass
class RecordingStep:
    """One protocol step as it lives in a session: the plan plus its state.

    A snapshot of the protocol step rather than a reference to it, so a session
    stays self-explanatory after the protocol file has been edited (the file is
    a template, this is what was actually asked of *this* patient).

    Kept separate from ``Segment`` on purpose: instruction, countdown and take
    counter are recording concerns that have no meaning for an imported
    segment. A confirmed step *produces* a Segment; it is not one.
    """

    id: str                       # protocol step id
    title: str
    instruction: str = ""
    duration_s: float = 20.0
    countdown_s: float = 3.0
    paradigm: str = ""            # "" = documentation only, never analysed
    hand: str = "both"
    state: str = STEP_PENDING
    clip_path: str = ""           # the take currently kept for this step
    recorded_at: str = ""
    segment_id: str = ""          # set once confirmed
    takes: int = 0                # how often it was filmed (older takes are kept)

    @property
    def is_documentation(self) -> bool:
        return not self.paradigm

    @property
    def is_confirmed(self) -> bool:
        return self.state == STEP_CONFIRMED


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
    # Protocol-guided recording (empty for an imported video).
    protocol_id: str = ""
    protocol_name: str = ""
    steps: list[RecordingStep] = field(default_factory=list)

    # ── factory / io ─────────────────────────────────────────────
    @classmethod
    def create(cls, patient_id: int, patient_code: str) -> "VideoSession":
        return cls(patient_id=patient_id, patient_code=patient_code,
                   created_at=datetime.now().isoformat())

    def _patient_dir(self) -> Path:
        return VIDEO_SESSIONS_DIR / (self.patient_code or f"patient_{self.patient_id}")

    def _dir(self) -> Path:
        """Where this session's files live.

        Keyed per clinical session (``session_<id>/`` under the patient) so two
        visits can each have their own recording. A session without a
        ``db_session_id`` is a legacy per-patient one and keeps the old layout.
        """
        if self.db_session_id is not None:
            return self._patient_dir() / f"session_{self.db_session_id}"
        return self._patient_dir()

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

    # ── protocol-guided recording ────────────────────────────────
    @classmethod
    def create_recording(cls, patient_id: int, patient_code: str, protocol,
                         *, mirrored: bool) -> "VideoSession":
        """Start a session that films ``protocol`` step by step.

        ``mirrored`` is keyword-only and has no default on purpose. A protocol
        recording is our *own* capture, so it is stored raw and has to replay
        under the **webcam** mirror setting (``sources.webcam.mirror``), not the
        video default — getting this wrong shows a correct picture with swapped
        handedness, which has bitten this project twice. The caller passes it in
        rather than store.py reaching into the capture config, keeping this
        module free of capture dependencies.
        """
        session = cls.create(patient_id, patient_code)
        session.mirrored = mirrored
        session.attach_protocol(protocol)
        return session

    def attach_protocol(self, protocol) -> None:
        """Materialise a ``video.protocol.Protocol`` into pending steps."""
        self.protocol_id = protocol.id
        self.protocol_name = protocol.name
        self.steps = []
        self.add_steps(protocol)

    def add_steps(self, protocol) -> list[RecordingStep]:
        """Append a protocol's steps (ids made unique against existing ones).

        Lets a session grow: a single paradigm added after a protocol is just
        one more step in the same list, not a second mechanism.
        """
        if not self.steps:
            self.protocol_id = protocol.id
            self.protocol_name = protocol.name
        elif protocol.name and protocol.name not in (self.protocol_name or ""):
            self.protocol_name = f"{self.protocol_name} + {protocol.name}" \
                if self.protocol_name else protocol.name
        taken = {s.id for s in self.steps}
        added = []
        for s in protocol.steps:
            sid, n = s.id, 2
            while sid in taken:
                sid, n = f"{s.id}_{n}", n + 1
            taken.add(sid)
            step = RecordingStep(id=sid, title=s.title, instruction=s.instruction,
                                 duration_s=s.duration_s, countdown_s=s.countdown_s,
                                 paradigm=s.paradigm, hand=s.hand)
            self.steps.append(step)
            added.append(step)
        return added

    def remove_step(self, step_id: str) -> None:
        """Drop a step and any segment made from it (files stay on disk)."""
        step = self.step(step_id)
        if step is None:
            return
        if step.segment_id:
            self.remove_segment(step.segment_id)
        self.steps = [s for s in self.steps if s.id != step_id]
        if not self.steps:
            self.protocol_id = ""
            self.protocol_name = ""

    def step(self, step_id: str) -> RecordingStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def _require_step(self, step_id: str) -> RecordingStep:
        step = self.step(step_id)
        if step is None:
            raise KeyError(f"Unbekannter Schritt: {step_id}")
        return step

    def next_open_step(self) -> RecordingStep | None:
        """The first step still to be dealt with, for resuming after a break."""
        return next((s for s in self.steps if not s.is_confirmed), None)

    @property
    def progress(self) -> tuple[int, int]:
        """(confirmed, total) — total is 0 when no protocol is attached."""
        return (sum(1 for s in self.steps if s.is_confirmed), len(self.steps))

    @property
    def is_complete(self) -> bool:
        return bool(self.steps) and all(s.is_confirmed for s in self.steps)

    def begin_take(self, step_id: str) -> Path:
        """Reserve the file for the next take and return its path.

        Every take gets its own filename, so re-recording never overwrites what
        was filmed before — nothing the patient did is silently destroyed.
        """
        step = self._require_step(step_id)
        step.takes += 1
        d = self._dir()
        d.mkdir(parents=True, exist_ok=True)
        return d / f"step_{step.id}_take{step.takes:02d}.mp4"

    def mark_recorded(self, step_id: str, clip_path: str | Path) -> RecordingStep:
        """A take finished — it now awaits review."""
        step = self._require_step(step_id)
        step.clip_path = str(clip_path)
        step.recorded_at = datetime.now().isoformat()
        step.state = STEP_RECORDED
        return step

    def confirm_step(self, step_id: str) -> Segment:
        """Accept the current take: the step becomes a Segment.

        This is the point where a recording enters the normal VideoLab world —
        analysis and export then run through the existing segment path, with no
        knowledge that a protocol was involved.
        """
        step = self._require_step(step_id)
        if not step.clip_path:
            raise ValueError(f"Schritt '{step_id}' hat keine Aufnahme.")

        seg = self.add_segment(name=step.title, start_s=0.0, end_s=step.duration_s,
                               paradigm=step.paradigm, hand=step.hand)
        seg.clip_path = step.clip_path      # replaced by the compact clip once archived
        seg.source_path = step.clip_path    # the raw take itself
        seg.recorded = True
        step.segment_id = seg.id
        step.state = STEP_CONFIRMED
        return seg

    def retake_step(self, step_id: str) -> RecordingStep:
        """Discard the current take's *result* and film the step again.

        The clip file itself stays on disk (takes are numbered); only the
        session's reference to it and any Segment made from it are dropped, so
        the step is open again without losing recorded material.
        """
        step = self._require_step(step_id)
        if step.segment_id:
            self.remove_segment(step.segment_id)
            step.segment_id = ""
        step.clip_path = ""
        step.recorded_at = ""
        step.state = STEP_PENDING
        return step

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
            "protocol_id": self.protocol_id,
            "protocol_name": self.protocol_name,
            "steps": [asdict(s) for s in self.steps],
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
                 db_session_id=d.get("db_session_id"),
                 protocol_id=d.get("protocol_id", ""),
                 protocol_name=d.get("protocol_name", ""))
        vs.segments = [Segment(**s) for s in d.get("segments", [])]
        # Unknown keys are dropped rather than raising: a session written by a
        # newer version must still open here, only without what it cannot know.
        known = {f.name for f in fields(RecordingStep)}
        vs.steps = [RecordingStep(**{k: v for k, v in s.items() if k in known})
                    for s in d.get("steps", [])]
        return vs


def load_for_session(patient_id: int, patient_code: str, session_id: int,
                     newest_session_id: int | None = None) -> "VideoSession | None":
    """The video session belonging to one clinical session, or None.

    Looks in the per-session directory first. Failing that, a legacy
    per-patient ``session.json`` is adopted if it belongs here: either it was
    exported into this session, or it never recorded a session and this is the
    patient's newest one — the assignment agreed for the migration. Adoption
    stamps the id and saves, so it happens once.
    """
    patient_dir = VIDEO_SESSIONS_DIR / (patient_code or f"patient_{patient_id}")
    own = patient_dir / f"session_{session_id}" / "session.json"
    if own.is_file():
        try:
            return VideoSession.load(str(own))
        except Exception:
            return None

    legacy = load_for_patient(patient_id, patient_code)
    if legacy is None:
        return None
    belongs = (legacy.db_session_id == session_id
               or (legacy.db_session_id is None and session_id == newest_session_id))
    if not belongs:
        return None
    if legacy.db_session_id is None:
        legacy.db_session_id = session_id
        try:
            legacy.save()
        except Exception:
            pass
    return legacy


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
