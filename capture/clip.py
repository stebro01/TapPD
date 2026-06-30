"""Save / list recorded motion clips (used by ReplaySource)."""

from __future__ import annotations

import json
from pathlib import Path

CLIPS_DIR = Path(__file__).parent.parent / "data" / "clips"
DEFAULT_CLIP = CLIPS_DIR / "default.mp4"   # the Sim source loops this


def default_clip_path() -> str:
    """Path the Sim source replays: the default clip if present, else the newest
    recorded clip (so a just-recorded clip becomes the default), else ""."""
    if DEFAULT_CLIP.is_file():
        return str(DEFAULT_CLIP)
    clips = list_clips()
    return str(clips[0]) if clips else ""


def save_clip(frames: list[dict], duration_s: float, label: str,
              source_kind: str = "webcam", fps: float = 30.0,
              recorded_at: str = "") -> Path:
    """Persist a clip. ``frames`` is [{"dt": secs_from_start, "hand": HandPose.to_dict()}]."""
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    path = CLIPS_DIR / f"clip_{label}.json"
    payload = {
        "meta": {
            "source_kind": source_kind,
            "duration_s": duration_s,
            "fps": fps,
            "recorded_at": recorded_at,
        },
        "frames": frames,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return path


def list_clips() -> list[Path]:
    """Recorded video clips (mp4) for the Sim source, newest first."""
    if not CLIPS_DIR.is_dir():
        return []
    return sorted(CLIPS_DIR.glob("clip_*.mp4"), reverse=True)


def list_landmark_clips() -> list[Path]:
    """Lightweight landmark-only clips (json) for the ReplaySource."""
    if not CLIPS_DIR.is_dir():
        return []
    return sorted(CLIPS_DIR.glob("clip_*.json"), reverse=True)
