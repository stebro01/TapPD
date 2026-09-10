"""The one pipeline every segment goes through: analyse → archive → clean up.

Shared by the recording pane (own takes) and the cut screen (segments of an
imported video) — the source differs, everything after it is the same:

* **analyse**: replay through the sidecar (raw take, or the imported video's
  range, or as a fallback the archived clip), store the result on the
  segment with raw-data JSON, per-frame track and analysis meta, and put it
  into the record (``export_or_update``: re-analysis updates, never copies);
* **compact**: archive the take as a compact (defaced) clip — a cut of an
  imported video is already extracted at creation, so nothing to do there;
* **cleanup**: drop the raw take once the archive exists (if configured).

Jobs are queued; the host may switch views while one is running. The UI
only listens: ``stageText`` for the label, ``previewReady`` for the frames,
``analysisFinished`` / ``analysisFailed`` / ``jobDone`` to refresh.
"""

from __future__ import annotations

import logging
import os
import time

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from ui.analysis_runner import AnalysisRunner
from video.store import VideoSession

log = logging.getLogger(__name__)


class _ArchiveWorker(QThread):
    """Runs the sidecar extractor off the GUI thread (it blocks for seconds)."""

    done = pyqtSignal(bool)

    def __init__(self, session: VideoSession, segment, deface: str | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._session, self._segment, self._deface = session, segment, deface

    def run(self) -> None:
        try:
            from video.archive import compact_take
            self.done.emit(bool(compact_take(self._session, self._segment,
                                             deface=self._deface)))
        except Exception:
            log.exception("Take-Archivierung fehlgeschlagen")
            self.done.emit(False)


def analysis_source(session: VideoSession, seg) -> tuple[str, float, float, bool, str]:
    """(path, start_s, end_s, mirrored, analysed_on) for a segment.

    Own take → the raw file while it exists (full quality), else the archived
    clip; replays under the webcam mirror setting. Cut of an imported video →
    the imported file with the segment's range, under the video's own flag.
    """
    from capture.config import source_mirrored
    norm = lambda p: os.path.normcase(os.path.abspath(p)) if p else ""
    if seg.recorded:
        path = seg.analysis_path
        # Before archiving, clip_path still *is* the raw take — only a clip
        # that differs from the source counts as the archive.
        on_clip = (bool(seg.clip_path) and norm(path) == norm(seg.clip_path)
                   and norm(seg.clip_path) != norm(seg.source_path))
        return path, 0.0, float(seg.duration_s), source_mirrored("webcam"), \
            ("clip" if on_clip else "raw")
    if session.video_path and os.path.isfile(session.video_path):
        return session.video_path, float(seg.start_s), float(seg.end_s), bool(session.mirrored), "import"
    if seg.clip_path and os.path.isfile(seg.clip_path):
        return seg.clip_path, 0.0, float(seg.duration_s), bool(session.mirrored), "clip"
    return "", 0.0, 0.0, bool(session.mirrored), ""


def needs_eye_reference(paradigm: str) -> bool:
    try:
        from capture.source import CAP_ABS_POSITION
        from paradigms.config import get_task_requirements
        return CAP_ABS_POSITION in get_task_requirements(paradigm)
    except Exception:
        return False


class SegmentPipeline(QObject):
    previewReady = pyqtSignal(dict)                    # sidecar preview of the analysis
    stageText = pyqtSignal(str)                        # what is happening, for a label
    analysisStarted = pyqtSignal(object, object)       # session, segment
    analysisFinished = pyqtSignal(object, object, dict)  # session, segment, features
    analysisFailed = pyqtSignal(object, object, str)   # session, segment, message
    jobDone = pyqtSignal(object, object)               # session, segment — after cleanup
    rawReleased = pyqtSignal(str)                      # raw take about to be removed
    contentChanged = pyqtSignal()

    _DISCARD_DELAYS_MS = (500, 1500, 4000, 10000, 30000)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.runner = AnalysisRunner(self)
        self.runner.finished.connect(self._on_analysis_finished)
        self.runner.failed.connect(self._on_analysis_failed)
        self.runner.previewReady.connect(self.previewReady.emit)
        self._queue: list = []
        self._job: dict | None = None
        self._worker: _ArchiveWorker | None = None
        self.analysis_t0 = 0.0

    # ── state ─────────────────────────────────────────────────────
    @property
    def job(self) -> dict | None:
        return self._job

    @job.setter
    def job(self, value: dict | None) -> None:
        self._job = value

    @property
    def busy(self) -> bool:
        return self._job is not None

    @property
    def analysing(self) -> bool:
        return bool(self._job) and self._job["stage"] == "analyse"

    def analysing_segment(self):
        return self._job["segment"] if self.analysing else None

    def analysing_step_id(self) -> str:
        return self._job.get("step_id", "") if self.analysing else ""

    # ── queue ─────────────────────────────────────────────────────
    def enqueue(self, session: VideoSession, segment, *, step_id: str = "",
                analyse: bool = True, deface: str | None = None) -> None:
        # The job carries its own session: the host may switch to another
        # session while this one is still being processed.
        self._queue.append((session, step_id, segment, analyse, deface))
        self._next_job()

    def clear(self) -> None:
        self._queue.clear()
        self._job = None

    def teardown(self) -> None:
        self.clear()
        self.runner.teardown()

    def _next_job(self) -> None:
        if self._job is not None or not self._queue:
            return
        session, step_id, segment, analyse, deface = self._queue.pop(0)
        if step_id and session.step(step_id) is None:
            self._next_job()
            return
        self._job = {"session": session, "step_id": step_id, "segment": segment,
                     "stage": "", "deface": deface}
        self._run_stage("analyse" if analyse else "compact")

    def _job_step(self):
        j = self._job
        return j["session"].step(j["step_id"]) if j and j.get("step_id") else None

    def _title(self) -> str:
        step = self._job_step()
        return step.title if step else (self._job["segment"].name or self._job["segment"].id)

    # ── stages ────────────────────────────────────────────────────
    def _run_stage(self, stage: str) -> None:
        if self._job is None:
            return
        self._job["stage"] = stage
        session, seg = self._job["session"], self._job["segment"]
        title = self._title()

        if stage == "analyse":
            # A protocol step is the plan the take was filmed for: its
            # paradigm and side win over the segment's copy (relabel keeps
            # both in sync; an import cut has no step and uses its own).
            step = self._job_step()
            paradigm = step.paradigm if step is not None else seg.paradigm
            hand = step.hand if step is not None else seg.hand
            path, start_s, end_s, mirrored, analysed_on = analysis_source(session, seg)
            if not path or not paradigm:
                self._run_stage("compact")
                return
            self._job["analysed_on"] = analysed_on
            note = ""
            if analysed_on == "clip" and seg.deidentified:
                note = (" — auf dem archivierten Clip (Gesicht unkenntlich, "
                        "Roh-Take nicht mehr vorhanden)")
                if needs_eye_reference(paradigm):
                    note += "; ohne Gesicht fehlt die Augenreferenz für Absolutwerte"
            elif analysed_on == "import":
                note = " — auf dem importierten Original"
            self.stageText.emit(f"Auswertung läuft: {title}{note} …")
            self.analysis_t0 = time.time()
            self.analysisStarted.emit(session, seg)
            hand = hand if hand in ("left", "right", "both") else "right"
            self.runner.start(path, start_s, end_s, paradigm, hand=hand,
                              with_face=False, mirrored=mirrored)

        elif stage == "compact":
            from video.archive import compact_enabled
            already = bool(seg.clip_path) and (not seg.source_path
                                               or seg.clip_path != seg.source_path)
            if not seg.recorded or not compact_enabled() or already:
                # Import cuts are extracted at creation; a re-analysis of an
                # archived take has nothing left to compact.
                self._run_stage("cleanup")
                return
            self.stageText.emit(f"Clip wird archiviert: {title} …")
            self._worker = _ArchiveWorker(session, seg, self._job.get("deface"), self)
            self._worker.done.connect(self._on_compact_done)
            self._worker.start()

        elif stage == "cleanup":
            from video.archive import keep_raw_take
            if seg.recorded and not keep_raw_take():
                # Whoever still holds the raw file open has to let go first,
                # or Windows refuses the delete (review player, analysis
                # sidecar). Both release asynchronously; the delete is retried.
                if seg.source_path:
                    self.rawReleased.emit(seg.source_path)
                self.runner.teardown()
                self._schedule_discard(session, seg)
            self._job = None
            self.contentChanged.emit()
            self.jobDone.emit(session, seg)
            self._next_job()

    def _schedule_discard(self, session, seg, attempt: int = 0) -> None:
        if attempt >= len(self._DISCARD_DELAYS_MS):
            log.info("Roh-Take bleibt (weiterhin in Benutzung): %s", seg.source_path)
            return
        QTimer.singleShot(self._DISCARD_DELAYS_MS[attempt],
                          lambda: self._try_discard(session, seg, attempt))

    def _try_discard(self, session, seg, attempt: int) -> None:
        from video.archive import discard_raw_take
        if not seg.source_path:
            return
        if discard_raw_take(session, seg):
            session.save()
            self.contentChanged.emit()
            return
        self._schedule_discard(session, seg, attempt + 1)

    def _on_compact_done(self, ok: bool) -> None:
        self._worker = None
        if self._job is None:
            return
        session, seg = self._job["session"], self._job["segment"]
        title = self._title()
        if ok:
            session.save()
            self.stageText.emit(f"Archiviert: {title}"
                                + (" (Gesicht unkenntlich)" if seg.deidentified else ""))
        else:
            self.stageText.emit(f"Archivierung übersprungen: {title} — Roh-Take bleibt.")
        self._run_stage("cleanup")

    # ── analysis result ───────────────────────────────────────────
    def _on_analysis_finished(self, test, features: dict) -> None:
        if self._job is None or self._job["stage"] != "analyse":
            return
        session, seg = self._job["session"], self._job["segment"]
        if seg is not None:
            from datetime import datetime
            from video.meta import analysis_meta
            key = test.test_type()
            previous = seg.results.get(key) or {}
            result = {"features": features, "recorded_at": datetime.now().isoformat(),
                      "raw_path": "", "source_kind": "video"}
            if self.runner.needs_abs_position:
                result["eye_ref_coverage"] = round(self.runner.eye_ref_coverage(), 3)
            if previous.get("measurement_id"):
                result["measurement_id"] = previous["measurement_id"]
            result["analysed_on"] = self._job.get("analysed_on", "raw")
            _, _, _, mirrored, _ = analysis_source(session, seg)
            result["analysis"] = analysis_meta(self.runner, mirrored=mirrored)
            result["raw_path"] = self._save_raw(test, session, features,
                                                previous.get("raw_path", ""))
            seg.results[key] = result
            self._save_track(session, seg)
            session.save()
            # Into the record straight away — an analysed segment is a
            # measurement; a re-analysis updates the same one, never a copy.
            try:
                from video.export import export_or_update
                export_or_update(session, seg, key)
            except Exception as e:
                log.warning("Übernahme in die Akte fehlgeschlagen: %s", e)
        mpi = (features or {}).get("mpi")
        summary = f"MPI {mpi:.2f}" if isinstance(mpi, (int, float)) else "fertig"
        self.stageText.emit(f"✔ Ausgewertet: {self._title()} — {summary}")
        self.contentChanged.emit()
        # Leave the analyse stage first: while the job is still in it, hosts
        # (rightly) keep the analysis preview on screen.
        self._run_stage("compact")
        self.analysisFinished.emit(session, seg, features or {})

    def _on_analysis_failed(self, msg: str) -> None:
        if self._job is None or self._job["stage"] != "analyse":
            return
        session, seg = self._job["session"], self._job["segment"]
        log.warning("Auswertung von '%s' fehlgeschlagen: %s", self._title(), msg)
        self.stageText.emit(f"Auswertung fehlgeschlagen: {msg}")
        self._run_stage("compact")   # still archive the take
        self.analysisFailed.emit(session, seg, msg)

    def _save_raw(self, test, session, features: dict, previous: str = "") -> str:
        """The per-frame raw data as JSON in data/samples — the same file every
        live paradigm writes, so the detail dialog plots a video analysis the
        same way. A re-analysis replaces the previous file."""
        try:
            from ui.results_screen import save_raw_data
            code = session.patient_code or f"patient_{session.patient_id}"
            path = save_raw_data(test, code, features)
            if path and previous and os.path.isfile(previous) \
                    and os.path.abspath(previous) != os.path.abspath(str(path)):
                try:
                    os.remove(previous)
                except OSError:
                    pass
            return str(path) if path else ""
        except Exception:
            log.warning("Rohdaten der Auswertung konnten nicht gespeichert werden",
                        exc_info=True)
            return ""

    def _save_track(self, session, seg) -> None:
        """Write the analysis' per-frame landmarks next to the segment's clip."""
        track = self.runner.track_data()
        if not track:
            return
        try:
            import json
            dest = str(session.segment_clip_path(seg.id)).rsplit(".", 1)[0] + ".track.json"
            with open(dest, "w", encoding="utf-8") as f:
                json.dump({"frames": {str(k): v for k, v in sorted(track.items())}},
                          f, separators=(",", ":"))
            seg.track_path = dest
        except Exception:
            log.warning("Overlay-Track konnte nicht gespeichert werden", exc_info=True)
