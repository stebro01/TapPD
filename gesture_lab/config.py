"""Load gesture lab configuration from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).parent / "gesture_config.yaml"
_config: dict[str, Any] | None = None


def get_config() -> dict[str, Any]:
    """Load and cache the gesture config. Reloads if file changed."""
    global _config
    if _config is None:
        with open(_CONFIG_PATH) as f:
            _config = yaml.safe_load(f)
    return _config


def reload_config() -> dict[str, Any]:
    """Force reload from disk."""
    global _config
    _config = None
    return get_config()


def get_scoring_config() -> dict[str, Any]:
    return get_config()["scoring"]


def get_dynamic_config() -> dict[str, Any]:
    return get_config()["dynamic"]


def get_pose_config(pose_number: int) -> dict[str, Any] | None:
    return get_config()["poses"].get(pose_number)


def get_all_poses() -> dict[int, dict[str, Any]]:
    return get_config()["poses"]
