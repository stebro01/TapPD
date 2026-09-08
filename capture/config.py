"""Capture-layer config (MediaPipe sidecar settings), loaded from capture.yaml.

Read by the main app; the values are pushed to the sidecar over the socket on
connect (the sidecar venv has no pyyaml).
"""

from __future__ import annotations

from pathlib import Path

from config_loader import load_config, nested_get

_PATH = Path(__file__).parent / "capture.yaml"

_DEFAULTS = {
    "sidecar": {
        "preview_fps": 15,
        "preview_max_width": 640,
        "jpeg_quality": 70,
        "hand_confidence": 0.5,
        "tracking_confidence": 0.5,
        "num_hands": 2,
        "record_fps": 30,
        "record_codec": "avc1",
        "camera_width": 0,
        "camera_height": 0,
        "camera_fps": 0,
    },
    "readiness": {"ready_y_mm": 120.0, "min_confidence": 0.5},
    "preview": {"hand_stale_s": 0.3},
}

_CONFIG = load_config(_PATH, _DEFAULTS)


def cfg(*keys, default=None):
    return nested_get(_CONFIG, keys, default)


def sidecar_settings() -> dict:
    """The sidecar settings dict to push over the socket."""
    return dict(_CONFIG.get("sidecar", {}))
