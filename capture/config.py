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
    # Orientation per input source — see the commentary in capture.yaml.
    "sources": {
        "leap": {"mirror": False, "swap_handedness": False},
        "webcam": {"mirror": True, "swap_handedness": False},
        "video": {"mirror": False, "swap_handedness": False},
    },
}

_CONFIG = load_config(_PATH, _DEFAULTS)


def cfg(*keys, default=None):
    return nested_get(_CONFIG, keys, default)


def source_mirrored(kind: str = "webcam") -> bool:
    """Is this input source shown (and analysed) mirrored?

    The matching left/right label correction is NOT a separate setting — the
    sidecar derives it from the same flag, so the two cannot disagree.
    """
    return bool(cfg("sources", kind, "mirror", default=False))


def source_swap_handedness(kind: str = "webcam") -> bool:
    """Extra left/right swap on top of the mirror-coupled correction.

    Only for cameras that already mirror in hardware; this is the initial state
    of the tracking screen's "Händigkeit vertauschen" checkbox.
    """
    return bool(cfg("sources", kind, "swap_handedness", default=False))


def sidecar_settings() -> dict:
    """The sidecar settings dict to push over the socket.

    Carries the live-camera mirror flag along, so the sidecar learns it through
    the same channel as the other tunables. Video replay is not covered here —
    a clip brings its own flag with the start command.
    """
    settings = dict(_CONFIG.get("sidecar", {}))
    settings["mirror"] = source_mirrored("webcam")
    return settings
