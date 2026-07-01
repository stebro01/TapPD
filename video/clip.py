"""Video clips + metadata for the video service.

A `VideoClip` is an mp4 on disk with a sidecar `<name>.meta.json` describing it
(duration, fps, resolution, provenance, whether it was de-identified). The
`VideoLibrary` manages the global/Sim clip directory (``data/clips``); VideoLab
videos live under per-patient session dirs (see ``video/store.py``) but use the
same `VideoClip` metadata.

Also keeps the lightweight landmark-clip helpers (``clip_*.json``) used by the
ReplaySource for deterministic, MediaPipe-free replay in tests.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

CLIPS_DIR = Path(__file__).parent.parent / "data" / "clips"
DEFAULT_CLIP = CLIPS_DIR / "default.mp4"   # the Sim source loops this


@dataclass
class VideoClip:
    path: str
    duration_s: float = 0.0
    fps: float = 0.0
    width: int = 0
    height: int = 0
    source_kind: str = "webcam"     # provenance of the underlying footage
    origin: str = "recorded"        # "recorded" | "imported" | "segment"
    created_at: str = ""
    deidentified: bool = False      # face blurred/meshed for privacy
    extra: dict = field(default_factory=dict)

    def meta_path(self) -> Path:
        return Path(self.path).with_suffix(".meta.json")

    def save(self) -> "VideoClip":
        p = self.meta_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=1)
        return self

    @classmethod
    def load(cls, video_path: str) -> "VideoClip | None":
        mp = Path(video_path).with_suffix(".meta.json")
        if not mp.is_file():
            return None
        try:
            with open(mp, "r", encoding="utf-8") as f:
                return cls(**json.load(f))
        except Exception:
            log.warning("Clip-Metadaten unlesbar: %s", mp)
            return None


def make_clip(path, *, width=0, height=0, fps=0.0, duration_s=0.0,
              source_kind="webcam", origin="recorded", deidentified=False) -> VideoClip:
    """Build + persist a VideoClip metadata record next to the mp4."""
    return VideoClip(path=str(path), width=int(width), height=int(height),
                     fps=float(fps), duration_s=float(duration_s),
                     source_kind=source_kind, origin=origin,
                     deidentified=deidentified,
                     created_at=datetime.now().isoformat()).save()


class VideoLibrary:
    """The global/Sim clip collection under ``data/clips``."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else CLIPS_DIR

    def list(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(self.root.glob("clip_*.mp4"), reverse=True)

    def default_path(self) -> str:
        """The clip the Sim source loops: the default if present, else newest."""
        if DEFAULT_CLIP.is_file():
            return str(DEFAULT_CLIP)
        clips = self.list()
        return str(clips[0]) if clips else ""

    def new_clip_path(self, ext: str = "mp4") -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        if not ext.startswith("."):
            ext = "." + ext
        return self.root / f"clip_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"


# ── module-level convenience (read the current globals → monkeypatchable) ──
def list_clips() -> list[Path]:
    if not CLIPS_DIR.is_dir():
        return []
    return sorted(CLIPS_DIR.glob("clip_*.mp4"), reverse=True)


def default_clip_path() -> str:
    if DEFAULT_CLIP.is_file():
        return str(DEFAULT_CLIP)
    clips = list_clips()
    return str(clips[0]) if clips else ""


# ── landmark clips (json) for ReplaySource — MediaPipe-free replay ──
def save_clip(frames: list[dict], duration_s: float, label: str,
              source_kind: str = "webcam", fps: float = 30.0,
              recorded_at: str = "") -> Path:
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    path = CLIPS_DIR / f"clip_{label}.json"
    payload = {"meta": {"source_kind": source_kind, "duration_s": duration_s,
                        "fps": fps, "recorded_at": recorded_at}, "frames": frames}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return path


def list_landmark_clips() -> list[Path]:
    if not CLIPS_DIR.is_dir():
        return []
    return [p for p in sorted(CLIPS_DIR.glob("clip_*.json"), reverse=True)
            if not p.name.endswith(".meta.json")]
