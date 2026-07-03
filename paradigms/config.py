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


def get_hand_detection_messages(kind: str = "leap") -> dict[str, str]:
    """Source-aware hand-detection prompts.

    ``kind`` is "leap" / "webcam" / "mock" (see capture.source.source_kind).
    The flat ``message_*`` keys are the default (Leap, "over the sensor"); a
    per-source sub-block (e.g. ``hand_detection.webcam``) overrides them so the
    webcam path can say "in front of the camera" instead.
    """
    cfg = get_hand_detection_config()
    messages = {
        "waiting": cfg.get("message_waiting", "Bitte Hände über den Sensor halten..."),
        "detected": cfg.get("message_detected", "Hände erkannt!"),
        "timeout": cfg.get("message_timeout", "Keine Hände erkannt."),
    }
    override = cfg.get(kind, {}) if isinstance(cfg.get(kind), dict) else {}
    for short, long in (("waiting", "message_waiting"),
                        ("detected", "message_detected"),
                        ("timeout", "message_timeout")):
        if long in override:
            messages[short] = override[long]
    return messages


def get_task_requirements(test_key: str) -> set[str]:
    """Capabilities a task needs from the capture source.

    Explicit via a ``requires:`` list in the task config, otherwise derived from
    the declarative fields the task already has (so existing tasks need no
    change): finger metrics -> fingertips, ``use_palm_normal`` -> hand pose,
    ``use_palm_position`` -> absolute position.  See capture.source for tokens.
    """
    from capture.source import (
        CAP_FINGERTIPS, CAP_HAND_POSE, CAP_ABS_POSITION, CAP_FACE_LANDMARKS,
    )
    # Spatial / cognitive tasks track the hand's position in space and live
    # outside test_config.yaml — they fundamentally need absolute position.
    from paradigms.registry import BY_KEY, Category, is_cognitive
    if is_cognitive(test_key):
        return {CAP_ABS_POSITION}
    # Ocular paradigms need the face/iris stream, no hand capabilities.
    spec = BY_KEY.get(test_key)
    if spec is not None and spec.category is Category.OCULAR:
        return {CAP_FACE_LANDMARKS}

    cfg = get_test_config(test_key)
    explicit = cfg.get("requires")
    if explicit:
        return set(explicit)

    capture = cfg.get("capture", {})
    reqs: set[str] = set()
    if capture.get("required_fingers"):
        reqs.add(CAP_FINGERTIPS)
    if capture.get("use_palm_normal"):
        reqs.add(CAP_HAND_POSE)
    if capture.get("use_palm_position"):
        reqs.add(CAP_ABS_POSITION)
    if not reqs:
        reqs.add(CAP_FINGERTIPS)
    return reqs


def get_unmet_capabilities(test_key: str, source) -> set[str]:
    """Capabilities a task needs that the given source (kind or device) lacks.

    Empty set means the task is fully supported on that source.
    """
    from capture.source import source_capabilities
    return get_task_requirements(test_key) - source_capabilities(source)


def get_all_test_keys() -> list[str]:
    """Return list of test keys (excluding non-test entries like hand_detection)."""
    return [k for k in get_config() if k != "hand_detection"]


def reload_config() -> None:
    """Force reload from disk (useful for debugging)."""
    global _config
    _config = None
