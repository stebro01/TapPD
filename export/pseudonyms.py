"""Stable pseudonyms for exports.

``patient_code → P-0001`` lives next to the database (``data/pseudonyms.json``)
and never leaves the machine; every export that pseudonymises uses the same
id for the same patient, so exports can be joined later without re-identifying
anyone. A patient gets an id the first time they are exported.
"""

from __future__ import annotations

import json
from pathlib import Path


def mapping_path() -> Path:
    from storage.database import DB_PATH
    return Path(DB_PATH).parent / "pseudonyms.json"


def load_mapping() -> dict[str, str]:
    p = mapping_path()
    if not p.is_file():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in (data or {}).items()}
    except (json.JSONDecodeError, OSError):
        return {}


def pseudonym_for(patient_code: str, mapping: dict[str, str] | None = None,
                  save: bool = True) -> str:
    """The patient's pseudonym, minted (and saved) when there is none yet."""
    m = mapping if mapping is not None else load_mapping()
    if patient_code in m:
        return m[patient_code]
    used = set(m.values())
    n = len(m) + 1
    while f"P-{n:04d}" in used:
        n += 1
    m[patient_code] = f"P-{n:04d}"
    if save:
        p = mapping_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=1, ensure_ascii=False)
    return m[patient_code]
