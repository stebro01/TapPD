"""Import an external video into the library/session as a normalized VideoClip.

Wraps the sidecar transcode (downscale + fps-cap + re-encode → mp4) and writes a
`VideoClip` metadata record. Falls back to a plain copy when transcode is
unavailable/disabled. Used by VideoLab; the same `make_clip` metadata the Sim
recorder writes, so both stacks produce identical, auditable clips.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable

from video import transcode as tc
from video.clip import VideoClip, make_clip
from video.config import cfg

log = logging.getLogger(__name__)


class VideoImporter:
    def import_file(self, src: str, new_path: Callable[[str], Path]) -> VideoClip | None:
        """Import `src`; `new_path(ext)` yields a destination path. Returns the
        stored VideoClip (with metadata) or None on failure."""
        src = str(src)
        if cfg("import", "transcode", default=True) and tc.transcode_available():
            dest = str(new_path(cfg("import", "container", default="mp4")))
            out = tc.transcode_video(src, dest)
            if out:
                fps = float(out.get("fps") or 0.0)
                frames = int(out.get("frames") or 0)
                clip = make_clip(dest, width=out.get("w", 0), height=out.get("h", 0),
                                 fps=fps, duration_s=(frames / fps if fps else 0.0),
                                 source_kind="webcam", origin="imported")
                clip.extra = {"src_w": out.get("src_w", 0), "src_h": out.get("src_h", 0),
                              "src_fps": out.get("src_fps", 0),
                              "src_duration_s": out.get("src_duration_s", 0)}
                return clip.save()
            if not cfg("import", "fallback_to_copy", default=True):
                log.warning("Import abgebrochen: Transcode fehlgeschlagen, kein Copy-Fallback")
                return None
        # copy fallback (no cv2 in the main app → no probing; metadata stays minimal)
        dest = str(new_path(Path(src).suffix.lstrip(".") or "mp4"))
        shutil.copy2(src, dest)
        return make_clip(dest, origin="imported")
