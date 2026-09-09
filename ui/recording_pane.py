"""The recording workbench: film one protocol step, review it, keep or repeat.

The right-hand pane of the session screen whenever a recording step is
selected. It owns the record → review → confirm cycle for *one* step at a time
and the auto-analysis of confirmed takes; the session screen owns the content
list, the camera device and the footer.

Confirming a step turns it into a ``Segment``; with auto-analysis on, that
segment is then handed to the same ``AnalysisRunner`` VideoLab uses for an
imported clip — identical call, identical numbers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.analysis_runner import AnalysisRunner
from ui.widgets.live_metric_plot import LiveMetricPlot
from ui.widgets.webcam_preview import WebcamPreview
from video.store import STEP_CONFIRMED, STEP_RECORDED, VideoSession

log = logging.getLogger(__name__)

# Pane phases (distinct from the *step* states persisted on the session).
IDLE = "idle"
COUNTDOWN = "countdown"
RECORDING = "recording"
REVIEW = "review"


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


class RecordingPane(QWidget):
    contentChanged = pyqtSignal()          # the session's steps/segments changed
    statusChanged = pyqtSignal(str, bool)  # (text, is_error) for the footer
    busyChanged = pyqtSignal(bool)         # True while a take is in progress

    _overlay_src = None                    # sidecar replaying the take with landmarks

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.session: VideoSession | None = None
        self._device = None
        self._phase = IDLE
        self._current_id = ""
        self._t_left = 0.0
        self._recorded_path: str | None = None   # set from the reader thread

        self._runner = AnalysisRunner(self)
        self._runner.finished.connect(self._on_analysis_finished)
        self._runner.failed.connect(self._on_analysis_failed)
        # The analysis replays through its own sidecar; its preview (frame +
        # landmarks) is what the clinician sees while it runs.
        self._runner.previewReady.connect(lambda m: self._on_preview(m, "analysis"))
        self._analysis_t0 = 0.0
        # One slot per preview source. They used to share one field, and the
        # picture flipped between the live camera and the replayed take,
        # whichever had written last.
        self._previews: dict = {"live": None, "overlay": None, "analysis": None}
        self._overlay_track: dict | None = None    # stored track being replayed
        # After a take is confirmed it goes through a small pipeline, one step
        # at a time: analyse (on the raw take, full quality) → compact it into
        # the archive clip → drop the raw file if so configured. Queued, so the
        # clinician can keep filming while the previous step is processed.
        self._queue: list = []          # (step_id, segment, analyse)
        self._job: dict | None = None   # {"step_id", "segment", "stage"}
        self._worker: _ArchiveWorker | None = None

        self._build()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(33)
        self._tick_timer.timeout.connect(self._tick)

    # ── layout ───────────────────────────────────────────────────
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # Live preview while filming, the recorded take while reviewing.
        self._view = QStackedWidget()
        self._preview = WebcamPreview()
        self._preview.setMinimumHeight(300)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._video = QVideoWidget()
        self._video.setStyleSheet("background:#000;")
        self._player.setVideoOutput(self._video)
        self._view.addWidget(self._preview)
        self._view.addWidget(self._video)
        root.addWidget(self._view, 1)

        self._step_title = QLabel()
        self._step_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        root.addWidget(self._step_title)

        self._instruction = QLabel()
        self._instruction.setWordWrap(True)
        self._instruction.setStyleSheet(f"font-size: 14px; color: {theme.TEXT};")
        self._instruction.setMinimumHeight(40)
        root.addWidget(self._instruction)

        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        root.addWidget(self._bar)

        # Live detection readout: proof the tracking works *before* filming.
        self._detect_lbl = QLabel()
        self._detect_lbl.setStyleSheet("font-size: 12px;")
        root.addWidget(self._detect_lbl)

        actions = QHBoxLayout()
        self._auto_cb = QCheckBox("Nach Übernehmen automatisch auswerten")
        self._auto_cb.setChecked(True)
        self._auto_cb.setToolTip(
            "Läuft die Analyse direkt auf dem bestätigten Take — derselbe Weg "
            "wie beim Video-Import, also dieselben Zahlen.")
        actions.addWidget(self._auto_cb)
        # Per-recording privacy choice; the default comes from video.yaml.
        from video.config import cfg as _vcfg
        self._deface_cb = QCheckBox("Gesicht unkenntlich machen")
        self._deface_cb.setChecked(str(_vcfg("privacy", "deface", default="blur")) != "off")
        self._deface_cb.setToolTip(
            "Im archivierten Clip das Gesicht verwischen (Datenschutz). Die Analyse "
            "läuft immer auf dem unveränderten Take.")
        actions.addWidget(self._deface_cb)
        # Review with landmarks: replays the clip through the sidecar instead of
        # the plain player, so the tracking can be checked after the fact.
        self._overlay_cb = QCheckBox("Tracking-Overlay")
        self._overlay_cb.setToolTip("Den Take mit erkannten Hand-/Gesichtspunkten ansehen")
        self._overlay_cb.toggled.connect(self._on_overlay_toggled)
        actions.addWidget(self._overlay_cb)
        actions.addStretch()
        self._record_btn = QPushButton("● Aufnahme starten")
        self._record_btn.setProperty("cssClass", "accent")
        self._record_btn.clicked.connect(self._on_record)
        self._cancel_btn = QPushButton("Abbrechen")
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._keep_btn = QPushButton("✔ Übernehmen")
        self._keep_btn.setProperty("cssClass", "primary")
        self._keep_btn.clicked.connect(self._on_keep)
        self._retake_btn = QPushButton("↻ Wiederholen")
        self._retake_btn.clicked.connect(self._on_retake)
        for b in (self._record_btn, self._cancel_btn, self._keep_btn, self._retake_btn):
            actions.addWidget(b)
        root.addLayout(actions)

        self._analysis_lbl = QLabel()
        self._analysis_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        root.addWidget(self._analysis_lbl)
        self._plot = LiveMetricPlot(figsize=(8, 2.4))
        self._plot.setMinimumHeight(200)
        self._plot.setMaximumHeight(260)
        self._plot.setVisible(False)
        root.addWidget(self._plot)

    # ── wiring from the session screen ───────────────────────────
    def set_device(self, device) -> None:
        """Use this live camera. The session screen owns its lifecycle."""
        self._device = device
        if device is not None:
            device.set_preview_callback(lambda m: self._on_preview(m, "live"))
            device.set_recorded_callback(lambda p: setattr(self, "_recorded_path", p))
            self._preview.set_placeholder("Kamera wird gestartet …")
        else:
            self._preview.set_placeholder("Keine Kamera verfügbar")
        self._set_phase(self._phase)

    def set_session(self, session: VideoSession | None) -> None:
        self.session = session
        self._current_id = ""
        if session is not None and not self._tick_timer.isActive():
            self._tick_timer.start()

    def show_step(self, step_id: str) -> None:
        if self._phase in (COUNTDOWN, RECORDING):
            return      # never switch away mid-take
        self._current_id = step_id
        self._render_step()

    def stop(self) -> None:
        """Leaving the session: stop everything that is running."""
        if self._phase in (COUNTDOWN, RECORDING):
            self._on_cancel()
        self._tick_timer.stop()
        self._player.stop()
        self._stop_overlay()
        self._queue.clear()
        self._job = None
        self._runner.teardown()
        self._plot.setVisible(False)
        self._analysis_lbl.setText("")
        self.session = None
        self._device = None

    @property
    def busy(self) -> bool:
        return self._phase in (COUNTDOWN, RECORDING)

    @property
    def _step(self):
        return self.session.step(self._current_id) if self.session else None

    # ── rendering ────────────────────────────────────────────────
    def _render_step(self) -> None:
        step = self._step
        if step is None:
            self._step_title.setText("")
            self._instruction.setText("")
            self._set_phase(IDLE)
            return
        kind = "Dokumentation" if step.is_documentation else step.paradigm
        hand = "" if step.is_documentation else f"  ·  {step.hand}"
        self._step_title.setText(f"{step.title}  ·  {step.duration_s:g}s  ·  {kind}{hand}")
        self._instruction.setText(step.instruction or "—")
        if step.state in (STEP_RECORDED, STEP_CONFIRMED) and step.clip_path:
            self._enter_review(step.clip_path)
        else:
            self._set_phase(IDLE)

    def _set_phase(self, phase: str) -> None:
        was_busy = self.busy
        self._phase = phase
        step = self._step
        confirmed = bool(step and step.state == STEP_CONFIRMED)

        self._record_btn.setVisible(phase == IDLE)
        self._cancel_btn.setVisible(phase in (COUNTDOWN, RECORDING))
        self._keep_btn.setVisible(phase == REVIEW and not confirmed)
        self._retake_btn.setVisible(phase == REVIEW)
        self._retake_btn.setText("↻ Neu aufnehmen" if confirmed else "↻ Wiederholen")
        self._record_btn.setEnabled(self._device is not None and step is not None)
        self._auto_cb.setVisible(bool(step) and not step.is_documentation)

        overlay = phase == REVIEW and self._overlay_cb.isChecked()
        if self._analysing or overlay:
            self._view.setCurrentWidget(self._preview)       # landmarks on top
        else:
            self._view.setCurrentWidget(self._video if phase == REVIEW else self._preview)
        self._bar.setVisible(phase in (COUNTDOWN, RECORDING) or bool(self._analysing))
        self._overlay_cb.setVisible(phase == REVIEW)
        self._deface_cb.setVisible(phase == REVIEW and not confirmed)
        if phase != REVIEW:
            self._stop_overlay()

        if phase == IDLE and step is not None:
            self._status("Bereit — Aufnahme starten, wenn der Patient bereit ist."
                         if self._device is not None else
                         "Keine Kamera — Aufnahme nicht möglich.", self._device is None)
        elif phase == REVIEW:
            self._status("Aufnahme prüfen: übernehmen oder wiederholen."
                         if not confirmed else "Bestätigt und als Segment übernommen.")
        if was_busy != self.busy:
            self.busyChanged.emit(self.busy)

    def _enter_review(self, clip_path: str) -> None:
        self._player.setSource(QUrl.fromLocalFile(str(clip_path)))
        self._player.play()
        self._set_phase(REVIEW)
        if self._overlay_cb.isChecked():
            self._start_overlay(clip_path)

    # ── review with landmarks ────────────────────────────────────
    def _on_overlay_toggled(self, on: bool) -> None:
        if self._phase != REVIEW:
            return
        step = self._step
        if on and step is not None and step.clip_path:
            self._set_phase(self._phase)          # switch the view first …
            self._start_overlay(step.clip_path)   # … so the overlay's status note survives
        else:
            self._stop_overlay()
            self._set_phase(self._phase)

    def _load_track(self):
        """The stored analysis track of the current step's segment, or None."""
        step = self._step
        if step is None or self.session is None or not step.segment_id:
            return None
        seg = next((x for x in self.session.segments if x.id == step.segment_id), None)
        if seg is None or not seg.track_path or not os.path.isfile(seg.track_path):
            return None
        try:
            import json
            with open(seg.track_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {int(k): v for k, v in (data.get("frames") or {}).items()}
        except Exception:
            log.warning("Overlay-Track nicht lesbar: %s", seg.track_path, exc_info=True)
            return None

    def _start_overlay(self, clip_path: str) -> None:
        """Play the take with its landmarks — the stored ones where they exist.

        With a stored track the sidecar only decodes and streams frames
        (``track=False``) and the pane draws what the analysis measured; the
        clinician sees exactly the tracking behind the numbers, and a defaced
        archive works because nothing is re-detected. Without a track (takes
        from before this existed) the sidecar re-tracks live, and the label
        says so. Our own take replays under the webcam mirror setting."""
        from capture.mediapipe_capture import WebcamSource
        from capture.config import source_mirrored
        self._stop_overlay()
        track = self._load_track()
        try:
            src = WebcamSource(replay_path=str(clip_path))
            src.replay_mirror = source_mirrored("webcam")
            src.replay_track = track is None
            src.connect()
            src.configure(num_hands=2, preview_fps=15)
            src.set_preview_callback(lambda m: self._on_preview(m, "overlay"))
            src.enable_preview(True)
            src.enable_face(track is None)
            src.start_tracking(lambda _f: None)
        except Exception as e:
            log.warning("Overlay-Wiedergabe nicht möglich: %s", e)
            self._overlay_cb.blockSignals(True)
            self._overlay_cb.setChecked(False)
            self._overlay_cb.blockSignals(False)
            self._status(f"Overlay nicht möglich: {e}", error=True)
            return
        self._overlay_src = src
        self._overlay_track = track
        self._player.pause()
        self._previews["overlay"] = None
        self._preview.set_placeholder("Wiedergabe mit Tracking wird gestartet …")
        self._preview.clear()
        self._status("Overlay: gespeicherte Analyse" if track is not None else
                     "Overlay: neu berechnet — für diesen Take liegt keine gespeicherte "
                     "Analyse vor")

    def _stop_overlay(self) -> None:
        src, self._overlay_src = self._overlay_src, None
        self._overlay_track = None
        if src is None:
            return
        try:
            src.stop_tracking()
            src.disconnect()
        except Exception:
            log.debug("Overlay-Sidecar konnte nicht sauber beendet werden", exc_info=True)
        if self._phase == REVIEW and self._player.source().isValid():
            self._player.play()

    @property
    def overlay_active(self) -> bool:
        return self._overlay_src is not None

    def _status(self, text: str, error: bool = False) -> None:
        self._last_status = text
        self.statusChanged.emit(text, error)

    # ── actions ──────────────────────────────────────────────────
    def _on_record(self) -> None:
        step = self._step
        if step is None or self._device is None:
            return
        self._player.stop()
        self._t_left = max(0.0, step.countdown_s)
        self._set_phase(COUNTDOWN if self._t_left > 0 else RECORDING)
        if self._t_left <= 0:
            self._begin_capture()

    def _begin_capture(self) -> None:
        step = self._step
        if step is None or self.session is None or self._device is None:
            return
        path = self.session.begin_take(step.id)
        self._recorded_path = None
        self._t_left = step.duration_s
        self._device.record_clip(str(path), step.duration_s)
        self._set_phase(RECORDING)
        self._status(f"Aufnahme läuft — {step.duration_s:g}s")
        log.info("Protokollschritt '%s' wird aufgenommen: %s", step.id, path)

    def _on_cancel(self) -> None:
        """Abort the current take. The sidecar finishes writing its file; we
        simply do not adopt it — the take counter has already moved on, so the
        aborted file cannot be mistaken for the good one."""
        self._t_left = 0.0
        self._recorded_path = None
        self._set_phase(IDLE)
        self._status("Aufnahme abgebrochen.")

    def _on_keep(self) -> None:
        step = self._step
        if step is None or self.session is None:
            return
        try:
            segment = self.session.confirm_step(step.id)
        except ValueError as e:
            self._status(str(e), error=True)
            return
        self.session.save()
        analyse = self._auto_cb.isChecked() and not step.is_documentation
        # The checkbox is the clinician's word for *this* take; "off" bypasses
        # the configured default explicitly, "on" uses the configured mode.
        deface = None if self._deface_cb.isChecked() else "off"
        self._enqueue_step(self.session, step.id, segment, analyse, deface)
        self.contentChanged.emit()
        nxt = self.session.next_open_step()
        if nxt is not None:
            self.show_step(nxt.id)
        else:
            self._render_step()
            self._status("Protokoll vollständig — alle Schritte bestätigt.")

    def _on_retake(self) -> None:
        step = self._step
        if step is None or self.session is None:
            return
        self._player.stop()
        self.session.retake_step(step.id)
        self.session.save()
        self.contentChanged.emit()
        self._render_step()
        self._status("Schritt wieder offen — erneut aufnehmen.")

    # ── post-confirm pipeline: analyse → compact → cleanup ───────
    @property
    def _analysing(self) -> str:
        return self._job["step_id"] if self._job and self._job["stage"] == "analyse" else ""

    def enqueue(self, session: VideoSession, step_id: str, segment, analyse: bool) -> None:
        """Public entry for the host: (re)run the pipeline on a confirmed step."""
        self._enqueue_step(session, step_id, segment, analyse, deface=None)

    def _enqueue_step(self, session: VideoSession, step_id: str, segment,
                      analyse: bool, deface: str | None) -> None:
        # The job carries its own session: the host may switch the pane to
        # another session while this one is still being processed.
        self._queue.append((session, step_id, segment, analyse, deface))
        self._next_job()

    def _next_job(self) -> None:
        if self._job is not None or not self._queue:
            return
        session, step_id, segment, analyse, deface = self._queue.pop(0)
        if session.step(step_id) is None:
            self._next_job()
            return
        self._job = {"session": session, "step_id": step_id, "segment": segment,
                     "stage": "", "deface": deface}
        self._run_stage("analyse" if analyse else "compact")

    def _job_step(self):
        return self._job["session"].step(self._job["step_id"]) if self._job else None

    def _run_stage(self, stage: str) -> None:
        if self._job is None:
            return
        self._job["stage"] = stage
        session = self._job["session"]
        step, seg = self._job_step(), self._job["segment"]
        title = step.title if step else self._job["step_id"]

        if stage == "analyse":
            from capture.config import source_mirrored
            path = seg.analysis_path
            if step is None or not path:
                self._run_stage("compact")
                return
            self._plot.clear_plot()
            self._plot.setVisible(True)
            self._analysis_lbl.setText(f"Auswertung läuft: {title} …")
            # Show the analysis, not the review player: the sidecar's preview
            # carries the frame with the tracked landmarks, and a progress bar
            # says how far the replay has come — otherwise this looked like a
            # video playing with nothing happening and no end in sight.
            self._player.pause()
            self._previews["analysis"] = None
            self._preview.set_placeholder("Auswertung wird gestartet …")
            self._preview.clear()
            self._view.setCurrentWidget(self._preview)
            self._bar.setValue(0)
            self._bar.setVisible(True)
            import time as _t
            self._analysis_t0 = _t.time()
            # Identical call to VideoLab's segment analysis, on the raw take
            # (full quality, no blur). A take is our own capture, so it replays
            # under the *webcam* mirror setting, never the import flag.
            self._runner.start(path, 0.0, step.duration_s, step.paradigm,
                               hand=step.hand, with_face=False,
                               mirrored=source_mirrored("webcam"))

        elif stage == "compact":
            from video.archive import compact_enabled
            already = bool(seg.clip_path) and (not seg.source_path
                                               or seg.clip_path != seg.source_path)
            if not compact_enabled() or already:
                # A re-analysis of an archived take: nothing left to compact.
                self._run_stage("cleanup")
                return
            self._analysis_lbl.setText(f"Clip wird archiviert: {title} …")
            self._worker = _ArchiveWorker(session, seg, self._job.get("deface"), self)
            self._worker.done.connect(self._on_compact_done)
            self._worker.start()

        elif stage == "cleanup":
            from video.archive import keep_raw_take
            if not keep_raw_take():
                # Whoever still holds the raw file open has to let go first,
                # or Windows refuses the delete: the review player (it was
                # loaded for the take) and the analysis sidecar (keeps its
                # source warm after a play-once). Both release asynchronously,
                # so the delete itself is retried in the background.
                raw = seg.source_path
                # QUrl.toLocalFile() comes back with forward slashes; compare
                # normalised, or the player is never switched and keeps the
                # raw file locked for the rest of the session.
                norm = lambda p: os.path.normcase(os.path.abspath(p)) if p else ""
                if raw and norm(self._player.source().toLocalFile()) == norm(raw):
                    self._player.stop()
                    self._player.setSource(QUrl.fromLocalFile(seg.clip_path)
                                           if seg.clip_path else QUrl())
                self._runner.teardown()
                self._schedule_discard(session, seg)
            self._job = None
            self.contentChanged.emit()
            self._next_job()

    # Raw takes still open elsewhere cannot be deleted right away on Windows;
    # retry a few times with growing delays, then leave the file (nothing lost).
    _DISCARD_DELAYS_MS = (500, 1500, 4000, 10000, 30000)

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
        session = self._job["session"]
        step = self._job_step()
        title = step.title if step else self._job["step_id"]
        seg = self._job["segment"]
        if ok:
            session.save()
            self._analysis_lbl.setText(
                f"Archiviert: {title}" + (" (Gesicht unkenntlich)" if seg.deidentified else ""))
        else:
            self._analysis_lbl.setText(f"Archivierung übersprungen: {title} — Roh-Take bleibt.")
        self._run_stage("cleanup")

    def _on_analysis_finished(self, test, features: dict) -> None:
        if self._job is None or self._job["stage"] != "analyse":
            return
        session = self._job["session"]
        step, seg = self._job_step(), self._job["segment"]
        if seg is not None:
            from datetime import datetime
            key = test.test_type()
            previous = seg.results.get(key) or {}
            result = {"features": features, "recorded_at": datetime.now().isoformat(),
                      "raw_path": "", "source_kind": "video"}
            if self._runner.needs_abs_position:
                result["eye_ref_coverage"] = round(self._runner.eye_ref_coverage(), 3)
            if previous.get("measurement_id"):
                result["measurement_id"] = previous["measurement_id"]
            seg.results[key] = result
            self._save_track(session, seg)
            session.save()
            # Into the record straight away — a confirmed, analysed take is a
            # measurement; a re-analysis updates the same one, never a copy.
            try:
                from video.export import export_or_update
                export_or_update(session, seg, key)
            except Exception as e:
                log.warning("Übernahme in die Akte fehlgeschlagen: %s", e)
        self._refresh_plot(force=True)
        mpi = (features or {}).get("mpi")
        summary = f"MPI {mpi:.2f}" if isinstance(mpi, (int, float)) else "fertig"
        self._analysis_lbl.setText(
            f"✔ Ausgewertet: {step.title if step else self._job['step_id']} — {summary}")
        self.contentChanged.emit()
        # Leave the analyse stage first: while the job is still in it, the
        # view logic (rightly) keeps the analysis preview on screen.
        self._run_stage("compact")
        self._end_analysis_view()

    def _save_track(self, session, seg) -> None:
        """Write the analysis' per-frame landmarks next to the segment's clip."""
        track = self._runner.track_data()
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

    def _on_analysis_failed(self, msg: str) -> None:
        if self._job is None or self._job["stage"] != "analyse":
            return
        log.warning("Auto-Auswertung von '%s' fehlgeschlagen: %s", self._job["step_id"], msg)
        self._analysis_lbl.setText(f"Auswertung fehlgeschlagen: {msg}")
        self._run_stage("compact")   # still archive the take
        self._end_analysis_view()

    def _end_analysis_view(self) -> None:
        """Back to whatever the selected step shows (usually the review player)."""
        self._bar.setValue(100)
        self._bar.setVisible(self.busy)
        self._set_phase(self._phase)
        if self._phase == REVIEW:
            self._player.play()

    def _refresh_plot(self, force: bool = False) -> None:
        if force or self._analysing:
            self._plot.update_plot(self._runner.live, self._runner.metric_label())

    # ── frame loop ───────────────────────────────────────────────
    def _on_preview(self, msg: dict, source: str = "live") -> None:
        self._previews[source] = msg    # reader thread: only store, never draw

    def _current_preview(self):
        """(message, source) of whatever the pane is showing right now."""
        if self._analysing:
            return self._previews["analysis"], "analysis"
        if self._overlay_src is not None:
            return self._previews["overlay"], "overlay"
        if self._phase != REVIEW:
            return self._previews["live"], "live"
        return None, ""

    def _update_detection(self, msg: dict) -> None:
        names = {"left": "links", "right": "rechts"}
        hands = [names.get(str(h).lower(), str(h))
                 for h, lm in zip(msg.get("hand_handedness", []),
                                  msg.get("landmarks", [])) if lm]
        face = bool(msg.get("face"))
        parts = []
        if hands:
            noun = "Hand" if len(hands) == 1 else "Hände"
            parts.append(f"✔ {len(hands)} {noun} ({', '.join(hands)})")
        else:
            parts.append("○ keine Hand")
        parts.append("✔ Gesicht" if face else "○ kein Gesicht")
        color = theme.ACCENT if (hands and face) else theme.TEXT_SECONDARY
        self._detect_lbl.setStyleSheet(f"font-size: 12px; color: {color};")
        self._detect_lbl.setText("   ·   ".join(parts))

    def _tick(self) -> None:
        try:
            if self._analysing:
                self._refresh_plot()
                step = self._job_step()
                total = max(0.5, step.duration_s if step else 1.0)
                import time as _t
                # Replay runs faster than real time; the bar is a lower bound
                # that jumps to 100 on completion.
                elapsed = _t.time() - self._analysis_t0
                self._bar.setValue(min(95, int(100 * elapsed / total)))
                self._status(f"Auswertung läuft … {min(elapsed, total):.0f} / {total:.0f} s")
            msg, source = self._current_preview()
            if msg is not None:
                if source == "overlay" and self._overlay_track is not None:
                    # Frames come bare; the landmarks are the stored analysis.
                    entry = self._overlay_track.get(int(msg.get("frame", -1)), {})
                    hands = entry.get("hands") or []
                    msg = dict(msg, landmarks=[lms for _t, lms in hands],
                               hand_handedness=[t for t, _l in hands], face=[])
                    self._preview.set_frame(msg["jpeg"], msg["landmarks"], [],
                                            iris=entry.get("iris"))
                else:
                    self._preview.set_frame(msg.get("jpeg", ""), msg.get("landmarks", []),
                                            msg.get("face", []))
                self._update_detection(msg)
            if self._phase == COUNTDOWN:
                self._t_left -= self._tick_timer.interval() / 1000.0
                step = self._step
                total = max(0.01, step.countdown_s if step else 1.0)
                self._bar.setValue(int(100 * (1 - self._t_left / total)))
                self._status(f"Startet in {max(0, int(self._t_left) + 1)} …")
                if self._t_left <= 0:
                    self._begin_capture()
            elif self._phase == RECORDING:
                self._t_left -= self._tick_timer.interval() / 1000.0
                step = self._step
                total = max(0.01, step.duration_s if step else 1.0)
                self._bar.setValue(int(100 * (1 - max(0.0, self._t_left) / total)))
                # The sidecar's "recorded" message ends a take, not our countdown.
                if self._recorded_path:
                    self._finish_capture(self._recorded_path)
                    self._recorded_path = None
        except Exception:
            log.exception("Fehler im Aufnahme-Tick")

    def _finish_capture(self, path: str) -> None:
        step = self._step
        if step is None or self.session is None:
            return
        if not path or not Path(path).is_file():
            self._set_phase(IDLE)
            self._status("Aufnahme fehlgeschlagen — keine Datei entstanden.", error=True)
            return
        self.session.mark_recorded(step.id, path)
        self.session.save()
        self.contentChanged.emit()
        self._enter_review(path)
