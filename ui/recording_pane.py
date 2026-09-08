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

    def __init__(self, session: VideoSession, segment, parent=None) -> None:
        super().__init__(parent)
        self._session, self._segment = session, segment

    def run(self) -> None:
        try:
            from video.archive import compact_take
            self.done.emit(bool(compact_take(self._session, self._segment)))
        except Exception:
            log.exception("Take-Archivierung fehlgeschlagen")
            self.done.emit(False)


class RecordingPane(QWidget):
    contentChanged = pyqtSignal()          # the session's steps/segments changed
    statusChanged = pyqtSignal(str, bool)  # (text, is_error) for the footer
    busyChanged = pyqtSignal(bool)         # True while a take is in progress

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.session: VideoSession | None = None
        self._device = None
        self._phase = IDLE
        self._current_id = ""
        self._t_left = 0.0
        self._latest_preview = None
        self._recorded_path: str | None = None   # set from the reader thread

        self._runner = AnalysisRunner(self)
        self._runner.finished.connect(self._on_analysis_finished)
        self._runner.failed.connect(self._on_analysis_failed)
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
        self._plot = LiveMetricPlot(figsize=(8, 1.6))
        self._plot.setMaximumHeight(140)
        self._plot.setVisible(False)
        root.addWidget(self._plot)

    # ── wiring from the session screen ───────────────────────────
    def set_device(self, device) -> None:
        """Use this live camera. The session screen owns its lifecycle."""
        self._device = device
        if device is not None:
            device.set_preview_callback(self._on_preview)
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

        self._view.setCurrentWidget(self._video if phase == REVIEW else self._preview)
        self._bar.setVisible(phase in (COUNTDOWN, RECORDING))

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

    def _status(self, text: str, error: bool = False) -> None:
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
        self._enqueue_step(step.id, segment, analyse)
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

    def _enqueue_step(self, step_id: str, segment, analyse: bool) -> None:
        self._queue.append((step_id, segment, analyse))
        self._next_job()

    def _next_job(self) -> None:
        if self._job is not None or not self._queue or self.session is None:
            return
        step_id, segment, analyse = self._queue.pop(0)
        if self.session.step(step_id) is None:
            self._next_job()
            return
        self._job = {"step_id": step_id, "segment": segment, "stage": ""}
        self._run_stage("analyse" if analyse else "compact")

    def _job_step(self):
        return self.session.step(self._job["step_id"]) if (self.session and self._job) else None

    def _run_stage(self, stage: str) -> None:
        if self._job is None or self.session is None:
            return
        self._job["stage"] = stage
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
            # Identical call to VideoLab's segment analysis, on the raw take
            # (full quality, no blur). A take is our own capture, so it replays
            # under the *webcam* mirror setting, never the import flag.
            self._runner.start(path, 0.0, step.duration_s, step.paradigm,
                               hand=step.hand, with_face=False,
                               mirrored=source_mirrored("webcam"))

        elif stage == "compact":
            from video.archive import compact_enabled
            if not compact_enabled():
                self._run_stage("cleanup")
                return
            self._analysis_lbl.setText(f"Clip wird archiviert: {title} …")
            self._worker = _ArchiveWorker(self.session, seg, self)
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
                self._schedule_discard(seg)
            self._job = None
            self.contentChanged.emit()
            self._next_job()

    # Raw takes still open elsewhere cannot be deleted right away on Windows;
    # retry a few times with growing delays, then leave the file (nothing lost).
    _DISCARD_DELAYS_MS = (500, 1500, 4000, 10000, 30000)

    def _schedule_discard(self, seg, attempt: int = 0) -> None:
        if attempt >= len(self._DISCARD_DELAYS_MS):
            log.info("Roh-Take bleibt (weiterhin in Benutzung): %s", seg.source_path)
            return
        QTimer.singleShot(self._DISCARD_DELAYS_MS[attempt],
                          lambda: self._try_discard(seg, attempt))

    def _try_discard(self, seg, attempt: int) -> None:
        from video.archive import discard_raw_take
        if self.session is None or not seg.source_path:
            return
        if discard_raw_take(self.session, seg):
            self.session.save()
            self.contentChanged.emit()
            return
        self._schedule_discard(seg, attempt + 1)

    def _on_compact_done(self, ok: bool) -> None:
        self._worker = None
        if self._job is None or self.session is None:
            return
        step = self._job_step()
        title = step.title if step else self._job["step_id"]
        seg = self._job["segment"]
        if ok:
            self.session.save()
            self._analysis_lbl.setText(
                f"Archiviert: {title}" + (" (Gesicht unkenntlich)" if seg.deidentified else ""))
        else:
            self._analysis_lbl.setText(f"Archivierung übersprungen: {title} — Roh-Take bleibt.")
        self._run_stage("cleanup")

    def _on_analysis_finished(self, test, features: dict) -> None:
        if self._job is None or self._job["stage"] != "analyse":
            return
        step, seg = self._job_step(), self._job["segment"]
        if seg is not None:
            from datetime import datetime
            result = {"features": features, "recorded_at": datetime.now().isoformat(),
                      "raw_path": "", "source_kind": "video"}
            if self._runner.needs_abs_position:
                result["eye_ref_coverage"] = round(self._runner.eye_ref_coverage(), 3)
            seg.results[test.test_type()] = result
            self.session.save()
        self._refresh_plot(force=True)
        mpi = (features or {}).get("mpi")
        summary = f"MPI {mpi:.2f}" if isinstance(mpi, (int, float)) else "fertig"
        self._analysis_lbl.setText(
            f"Ausgewertet: {step.title if step else self._job['step_id']} — {summary}")
        self.contentChanged.emit()
        self._run_stage("compact")

    def _on_analysis_failed(self, msg: str) -> None:
        if self._job is None or self._job["stage"] != "analyse":
            return
        log.warning("Auto-Auswertung von '%s' fehlgeschlagen: %s", self._job["step_id"], msg)
        self._analysis_lbl.setText(f"Auswertung fehlgeschlagen: {msg}")
        self._run_stage("compact")   # still archive the take

    def _refresh_plot(self, force: bool = False) -> None:
        if force or self._analysing:
            self._plot.update_plot(self._runner.live, self._runner.metric_label())

    # ── frame loop ───────────────────────────────────────────────
    def _on_preview(self, msg: dict) -> None:
        self._latest_preview = msg      # reader thread: only store, never draw

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
            if self._phase != REVIEW and self._latest_preview is not None:
                msg = self._latest_preview
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
