"""Where the record's files live on *this* machine.

The DB, the video-session JSONs and note attachments store absolute paths —
written on the machine that recorded them. When the project folder moves
(another disk, another user name, another OS) those paths point nowhere,
although the files travelled along inside ``data/``. ``resolve`` is the one
place that bridges this: a stored path that exists is returned as is; one
that does not is re-rooted under this project's ``data/`` by the part of the
path after the last ``data/`` segment, whichever slash style it came with.
Loaders (Measurement rows, VideoSession.load, attachments) run every stored
path through it, so the rest of the app keeps seeing paths that exist.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

_DATA_TAIL = re.compile(r"(?:^|[\\/])data[\\/](.+)$")


def resolve(path: str | None) -> str:
    """The stored path if it exists here, else the same file under this
    project's ``data/`` if that exists, else the stored path unchanged (so
    "missing" stays visibly missing)."""
    if not path:
        return path or ""
    if os.path.exists(path):
        return path
    m = _DATA_TAIL.search(str(path))
    if not m:
        return path
    tail = re.split(r"[\\/]", m.group(1))
    cand = DATA.joinpath(*tail)
    return str(cand) if cand.exists() else path


def resolve_keys(d: dict | None, keys: tuple[str, ...]) -> dict | None:
    """``resolve`` the given keys of a dict in place (missing keys ignored)."""
    if d:
        for k in keys:
            v = d.get(k)
            if isinstance(v, str) and v:
                d[k] = resolve(v)
    return d
