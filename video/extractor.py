"""Extract a segment [start_s, end_s] from a video into its own compact clip,
optionally de-identifying faces (privacy). Wraps mediapipe_sidecar/extract.py
(cv2 + MediaPipe in the sidecar venv) and writes VideoClip metadata.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess

from capture.mediapipe_capture import _SIDECAR_DIR, _SIDECAR_PY
from video.clip import VideoClip, make_clip
from video.config import cfg

log = logging.getLogger(__name__)

_SCRIPT = os.path.join(_SIDECAR_DIR, "extract.py")


def extract_available() -> bool:
    return os.path.isfile(_SIDECAR_PY) and os.path.isfile(_SCRIPT)


class VideoSegmentExtractor:
    def extract(self, src: str, start_s: float, end_s: float, dest: str,
                deface: str | None = None) -> VideoClip | None:
        """Cut [start_s, end_s] of `src` → `dest` (mp4). `deface` overrides the
        configured mode (off | blur | mesh); None = use config."""
        if not extract_available():
            log.warning("Segment-Extraktion nicht verfügbar (Sidecar-venv fehlt)")
            return None
        if deface is None:
            deface = str(cfg("privacy", "deface", default="off"))
        args = [
            _SIDECAR_PY, _SCRIPT, str(src), str(dest),
            str(start_s), str(end_s),
            str(cfg("segments", "max_width", default=960)),
            str(cfg("segments", "max_height", default=540)),
            str(cfg("segments", "target_fps", default=30)),
            str(cfg("import", "codec", default="mp4v")),
            deface,
            str(cfg("privacy", "blur_strength", default=41)),
            "1" if cfg("segments", "capture_eyeref", default=True) else "0",
        ]
        try:
            r = subprocess.run(args, capture_output=True, text=True,
                               timeout=float(cfg("import", "transcode_timeout_s", default=900)))
        except Exception:
            log.exception("Segment-Extraktion fehlgeschlagen")
            return None
        line = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else ""
        try:
            out = json.loads(line) if line else {}
        except json.JSONDecodeError:
            out = {}
        if not out.get("ok"):
            log.warning("Segment-Extraktion fehlgeschlagen: %s", out.get("error") or r.stderr[-200:])
            return None
        fps = float(out.get("fps") or 0.0)
        frames = int(out.get("frames") or 0)
        log.info("Segment extrahiert: %dx%d @%.0ffps, %d Frames, deface=%s, eyeref=%s → %s",
                 out.get("w", 0), out.get("h", 0), fps, frames, deface,
                 out.get("eyeref"), dest)
        clip = make_clip(dest, width=out.get("w", 0), height=out.get("h", 0), fps=fps,
                         duration_s=(frames / fps if fps else max(0.0, end_s - start_s)),
                         source_kind="webcam", origin="segment",
                         deidentified=bool(out.get("deidentified")))
        # Privacy-preserving eye-reference track for (future) abs-position / tremor.
        clip.extra = {"eyeref": bool(out.get("eyeref")),
                      "eyeref_path": (dest + ".eyeref.json") if out.get("eyeref") else "",
                      "thumb": out.get("thumb", "")}
        return clip.save()
