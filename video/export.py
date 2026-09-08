"""Export VideoLab segment results into the clinical DB as Measurements.

Bridges the write-isolated VideoLab JSON store (video/store.py) and the i2b2
star schema (storage/database.py). All exports of one video session land in a
single dedicated DB Session (created lazily, remembered as
``VideoSession.db_session_id``). Each exported result is stamped with its
``measurement_id`` so it cannot be exported twice; re-running the analysis
replaces the result dict and thereby clears the stamp again.

Provenance: measurements carry ``source_kind="video"`` (a camera source — the
mm scale is a MediaPipe model estimate, see ui/feature_meta.SCALE_NOTE).
"""

from __future__ import annotations

import logging

from storage.database import Measurement, create_session, get_db, save_measurement
from video.store import Segment, VideoSession

log = logging.getLogger(__name__)


class AlreadyExported(Exception):
    """The segment result was already exported (carries the measurement id)."""

    def __init__(self, measurement_id: int) -> None:
        super().__init__(f"Bereits als Messung {measurement_id} übernommen")
        self.measurement_id = measurement_id


def export_or_update(session: VideoSession, seg: Segment, paradigm_key: str) -> Measurement:
    """Put a segment result into the record; re-exports update the same row.

    A confirmed, analysed take is a measurement — so the recording pane calls
    this right after analysis. When the result already carries a
    ``measurement_id`` (a re-analysis, or a relabel that kept the id), that
    measurement is overwritten in place instead of a duplicate being created.
    """
    from storage.database import update_measurement

    result = seg.results.get(paradigm_key)
    if not result or not result.get("features"):
        raise ValueError(f"Kein Analyse-Ergebnis für '{paradigm_key}' in Segment {seg.id}")
    existing = result.get("measurement_id")
    if not existing:
        return export_result(session, seg, paradigm_key)

    conn = get_db()
    try:
        m = Measurement(
            id=int(existing),
            patient_id=session.patient_id,
            session_id=session.db_session_id,
            test_type=paradigm_key,
            hand=seg.hand if seg.hand in ("left", "right", "both") else "right",
            duration_s=seg.duration_s,
            recorded_at=result.get("recorded_at", ""),
            raw_data_path=seg.clip_path or "",
            source_kind=result.get("source_kind", "video"),
        )
        m.features = result.get("features", {})
        update_measurement(conn, m)
    finally:
        conn.close()
    session.save()
    log.info("VideoLab-Export: Segment %s/%s → Messung %d aktualisiert",
             seg.id, paradigm_key, m.id)
    return m


def export_result(session: VideoSession, seg: Segment, paradigm_key: str) -> Measurement:
    """Save one segment result as a Measurement; returns the saved Measurement.

    Raises ValueError if there is no result / no patient, AlreadyExported if
    this result was exported before.
    """
    result = seg.results.get(paradigm_key)
    if not result or not result.get("features"):
        raise ValueError(f"Kein Analyse-Ergebnis für '{paradigm_key}' in Segment {seg.id}")
    if not session.patient_id:
        raise ValueError("Video-Session hat keinen Patienten")
    existing = result.get("measurement_id")
    if existing:
        raise AlreadyExported(int(existing))

    conn = get_db()
    try:
        if not session.db_session_id:
            db_session = create_session(conn, session.patient_id)
            session.db_session_id = db_session.id
        m = Measurement(
            patient_id=session.patient_id,
            session_id=session.db_session_id,
            test_type=paradigm_key,
            hand=seg.hand if seg.hand in ("left", "right", "both") else "right",
            duration_s=seg.duration_s,
            recorded_at=result.get("recorded_at", ""),
            # The archived (defaced) segment clip is the raw artifact.
            raw_data_path=seg.clip_path or "",
            source_kind=result.get("source_kind", "video"),
        )
        m.features = result.get("features", {})
        save_measurement(conn, m)
    finally:
        conn.close()

    result["measurement_id"] = m.id
    session.save()
    log.info("VideoLab-Export: Segment %s/%s → Messung %d (Session %d)",
             seg.id, paradigm_key, m.id, session.db_session_id)
    return m
