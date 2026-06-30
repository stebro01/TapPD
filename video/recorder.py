"""Finalize a live webcam recording into a VideoClip (Sim default clip).

The sidecar writes the mp4 (via the `record` command); this moves it into place
and writes the `VideoClip` metadata — the same record `make_clip` the importer
produces, so recorded and imported clips are interchangeable and auditable.
"""

from __future__ import annotations

import os

from capture.config import cfg as capture_cfg
from video.clip import VideoClip, make_clip


class VideoRecorder:
    @staticmethod
    def finalize(pending_path: str, dest_path: str, *, seconds: float,
                 source_kind: str = "webcam") -> VideoClip:
        os.replace(pending_path, dest_path)
        return make_clip(dest_path,
                         fps=float(capture_cfg("sidecar", "record_fps", default=30.0)),
                         duration_s=float(seconds),
                         source_kind=source_kind, origin="recorded")
