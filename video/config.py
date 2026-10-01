"""Load VideoLab settings from video.yaml (with safe defaults).

Keeps storage format, resolution/fps, segment limits and analysis knobs out of
the code. Access via `cfg("import", "max_width")` etc.
"""

from __future__ import annotations

from pathlib import Path

from config_loader import load_config, nested_get

_PATH = Path(__file__).parent / "video.yaml"

_DEFAULTS = {
    "import": {
        "transcode": True,
        "container": "mp4",
        "codec": "avc1",
        "max_width": 1280,
        "max_height": 1280,
        "target_fps": 30,
        "transcode_timeout_s": 900,
        "fallback_to_copy": True,
    },
    "record": {"default_seconds": 10, "min_seconds": 1, "max_seconds": 120},
    "segments": {"min_length_s": 0.3, "extract": True, "max_width": 1080,
                 "max_height": 1080, "target_fps": 30, "capture_eyeref": True,
                 "crf": 23},
    "privacy": {"deface": "blur", "blur_strength": 41},
    # Confirmed takes → compact clips (see video.yaml archive:).
    "archive": {"compact_takes": True, "keep_raw_take": False},
    "analysis": {
        "default_hand": "right",
        "with_face": False,
        "hang_timeout_s": 20.0,
        "done_grace_s": 2.0,
        "live_plot_window_points": 300,
    },
    "ui": {"video_extensions": ["*.mp4", "*.mov", "*.m4v", "*.avi", "*.mkv", "*.webm"]},
}


_CONFIG = load_config(_PATH, _DEFAULTS)


def cfg(*keys, default=None):
    """Nested lookup, e.g. cfg('import', 'max_width')."""
    return nested_get(_CONFIG, keys, default)


def video_filter() -> str:
    """Qt file-dialog filter string from the configured extensions."""
    exts = " ".join(cfg("ui", "video_extensions", default=["*.mp4"]))
    return f"Videos ({exts});;Alle Dateien (*)"
