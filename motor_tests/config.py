"""Load and access test configuration from YAML."""

from pathlib import Path
from typing import Any

import yaml


_CONFIG_PATH = Path(__file__).parent / "test_config.yaml"
_config: dict[str, Any] | None = None


def get_config() -> dict[str, Any]:
    """Load and cache the test configuration."""
    global _config
    if _config is None:
        with open(_CONFIG_PATH) as f:
            _config = yaml.safe_load(f)
    return _config


def get_test_config(test_key: str) -> dict[str, Any]:
    """Get configuration for a specific test."""
    cfg = get_config()
    if test_key not in cfg:
        raise KeyError(f"Unknown test: {test_key}. Available: {list(cfg.keys())}")
    return cfg[test_key]


def get_hand_detection_config() -> dict[str, Any]:
    """Get hand detection settings."""
    return get_config().get("hand_detection", {})


def get_all_test_keys() -> list[str]:
    """Return list of test keys (excluding non-test entries like hand_detection)."""
    return [k for k in get_config() if k != "hand_detection"]


def reload_config() -> None:
    """Force reload from disk (useful for debugging)."""
    global _config
    _config = None
