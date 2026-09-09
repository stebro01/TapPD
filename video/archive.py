"""Archive a confirmed take as a compact clip — the "video database".

A take comes off the recorder at roughly 1 MB/s. What the measurement should
reference is not that, but the same kind of compact, optionally de-identified
clip an imported segment gets: same extractor, same size caps, same privacy
setting. So an own recording and an import end up as the same artefact.

Two steps, deliberately separate, because the caller decides *when*:

- :func:`compact_take` re-encodes the raw take into the segment's clip and
  repoints ``clip_path`` at it (``source_path`` keeps the raw file).
- :func:`discard_raw_take` removes the raw file — only if a compact clip exists
  and only when configured. The recording pane calls it after the analysis has
  run, since that analyses the raw take at full quality first.
"""

from __future__ import annotations

import logging
import os

from video.config import cfg
from video.store import Segment, VideoSession

log = logging.getLogger(__name__)


def compact_enabled() -> bool:
    return bool(cfg("archive", "compact_takes", default=True))


def keep_raw_take() -> bool:
    return bool(cfg("archive", "keep_raw_take", default=False))


def compact_take(session: VideoSession, seg: Segment, deface: str | None = None) -> bool:
    """Re-encode a recorded segment's raw take into its compact clip.

    Returns True when ``seg.clip_path`` now points at the compact clip. On any
    failure the segment is left untouched — still referencing the raw take —
    so nothing is lost, just not compressed. Runs the sidecar extractor, so
    call it off the GUI thread.

    ``deface`` overrides the configured privacy mode for this take
    (``"off" | "blur" | "mesh"``); None keeps ``privacy.deface`` from
    video.yaml — the clinician's per-recording choice on the recording pane.
    """
    if not seg.recorded:
        return False
    raw = seg.source_path or seg.clip_path
    if not raw or not os.path.isfile(raw):
        log.warning("Take für Segment %s nicht gefunden: %s", seg.id, raw)
        return False

    dest = str(session.segment_clip_path(seg.id))
    if os.path.abspath(dest) == os.path.abspath(raw):
        return False    # would overwrite the source

    from video.extractor import VideoSegmentExtractor
    clip = VideoSegmentExtractor().extract(raw, 0.0, seg.duration_s, dest, deface=deface)
    if clip is None or not os.path.isfile(dest):
        return False

    seg.source_path = raw
    seg.clip_path = dest
    seg.deidentified = bool(clip.deidentified)
    seg.thumb_path = str((clip.extra or {}).get("thumb") or seg.thumb_path)
    from video.meta import note_archive
    note_archive(seg, clip, deface)
    # Review plays the archive clip from now on. Leaving the step on the raw
    # take would have every re-render load the raw file into the player, which
    # then can never be deleted — the archive clip is what is kept anyway.
    for step in session.steps:
        if step.segment_id == seg.id:
            step.clip_path = dest
    try:
        ratio = os.path.getsize(raw) / max(1, os.path.getsize(dest))
        log.info("Take archiviert: %s → %s (%.1fx kleiner, deface=%s)",
                 os.path.basename(raw), os.path.basename(dest), ratio, seg.deidentified)
    except OSError:
        pass
    return True


def discard_raw_take(session: VideoSession, seg: Segment) -> bool:
    """Delete the raw take behind a segment, if configured and safe.

    Safe means: a compact clip exists as a *different* file. The step that
    produced the segment keeps pointing at the compact clip afterwards, so a
    review still has something to play.
    """
    if keep_raw_take():
        return False
    raw = seg.source_path
    if not raw or not os.path.isfile(raw):
        return False
    if not seg.clip_path or os.path.abspath(seg.clip_path) == os.path.abspath(raw):
        return False
    if not os.path.isfile(seg.clip_path):
        return False
    try:
        os.remove(raw)
    except PermissionError:
        # Still open elsewhere (review player, analysis sidecar) — the caller
        # retries later; not an error worth a traceback.
        log.debug("Roh-Take noch in Benutzung, später erneut: %s", raw)
        return False
    except OSError:
        log.warning("Roh-Take konnte nicht gelöscht werden: %s", raw, exc_info=True)
        return False
    seg.source_path = ""
    for step in session.steps:
        if step.segment_id == seg.id and step.clip_path == raw:
            step.clip_path = seg.clip_path
    log.info("Roh-Take entfernt: %s", os.path.basename(raw))
    return True
