"""Files attached to a note: copied into ``data/attachments/<patient>/<target>/``.

The clinical DB keeps only the reference (name, path, size, when added) in
the note's ``NOTE_BLOB``; the bytes live on disk next to the other patient
data so backups and deletes stay simple. Names are kept human-readable and
deduplicated inside the target folder (``bild.jpg`` → ``bild (2).jpg``).
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

ATTACHMENTS_DIR = Path(__file__).parent.parent / "data" / "attachments"


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s)).strip("_") or "x"


def target_dir(patient_code: str, kind: str, ref: str) -> Path:
    return ATTACHMENTS_DIR / _slug(patient_code) / f"{_slug(kind)}_{_slug(ref)}"


def add_attachment(patient_code: str, kind: str, ref: str, src: str) -> dict:
    """Copy ``src`` into the target folder; returns the reference to store."""
    src_p = Path(src)
    if not src_p.is_file():
        raise FileNotFoundError(src)
    dest_dir = target_dir(patient_code, kind, ref)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src_p.name
    n = 2
    while dest.exists():
        dest = dest_dir / f"{src_p.stem} ({n}){src_p.suffix}"
        n += 1
    shutil.copy2(src_p, dest)
    return {"name": dest.name, "path": str(dest), "size": dest.stat().st_size,
            "added_at": datetime.now().isoformat(timespec="seconds")}


def attachment_path(entry: dict) -> str:
    """The attachment's file on this machine (re-rooted if the record moved)."""
    from storage.paths import resolve
    return resolve(entry.get("path", ""))


def remove_attachment(entry: dict) -> bool:
    """Delete the copied file; True when it is gone afterwards."""
    path = attachment_path(entry)
    try:
        if path and os.path.isfile(path):
            os.remove(path)
        d = Path(path).parent if path else None
        if d is not None and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    except OSError:
        return not os.path.isfile(path)
    return True


def size_label(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1000:
        return f"{n / 1000:.0f} kB"
    return f"{n} B"
