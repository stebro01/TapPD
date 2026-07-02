"""Normalize an imported video to a compact mp4 via the sidecar venv (cv2).

The main app (Py3.14) has no cv2, so transcoding runs as a one-shot subprocess
under mediapipe_sidecar/.venv. Returns the result dict on success, else None
(callers fall back to a plain copy).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess

from capture.mediapipe_capture import _SIDECAR_DIR, _SIDECAR_PY
from video.config import cfg

log = logging.getLogger(__name__)

_SCRIPT = os.path.join(_SIDECAR_DIR, "transcode.py")


def transcode_available() -> bool:
    return os.path.isfile(_SIDECAR_PY) and os.path.isfile(_SCRIPT)


def transcode_video(src: str, dest: str) -> dict | None:
    """Downscale + fps-cap + re-encode `src` → `dest` (mp4). None on failure."""
    if not transcode_available():
        log.warning("Transcode nicht verfügbar (Sidecar-venv fehlt)")
        return None
    args = [
        _SIDECAR_PY, _SCRIPT, src, dest,
        str(cfg("import", "max_width", default=1280)),
        str(cfg("import", "max_height", default=1280)),
        str(cfg("import", "target_fps", default=30)),
        str(cfg("import", "codec", default="avc1")),
    ]
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=float(cfg("import", "transcode_timeout_s", default=900)))
    except Exception:
        log.exception("Transcode-Subprozess fehlgeschlagen")
        return None
    line = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else ""
    try:
        out = json.loads(line) if line else {}
    except json.JSONDecodeError:
        out = {}
    if not out.get("ok"):
        log.warning("Transcode fehlgeschlagen: %s", out.get("error") or r.stderr[-200:])
        return None
    log.info("Video normalisiert: %dx%d @%.0ffps, %d Frames → %s",
             out.get("w", 0), out.get("h", 0), out.get("fps", 0), out.get("frames", 0), dest)
    return out
