"""Shared YAML config helper: defaults deep-merged with an optional yaml file.

Used by the per-domain configs (capture/config.py, video/config.py) so the
load/merge/lookup logic lives in one place.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path, defaults: dict) -> dict:
    """Return `defaults` deep-merged with the yaml at `path` (defaults if absent)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
        return deep_merge(defaults, user)
    except FileNotFoundError:
        return dict(defaults)
    except Exception:
        log.exception("Config %s konnte nicht gelesen werden – nutze Defaults", path)
        return dict(defaults)


def nested_get(config: dict, keys: tuple, default=None):
    node = config
    for k in keys:
        if isinstance(node, dict) and k in node:
            node = node[k]
        else:
            return default
    return node
