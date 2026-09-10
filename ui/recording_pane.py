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

from PyQt6.QtCore import QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.segment_pipeline import SegmentPipeline, _ArchiveWorker, needs_eye_reference  # noqa: F401
from ui.widgets.live_metric_plot import LiveMetricPlot
from ui.widgets.meta_panel import MetaPanel
from ui.widgets.video_view import VideoView
from ui.widgets.webcam_preview import WebcamPreview
from video.store import STEP_CONFIRMED, STEP_RECORDED, VideoSession

log = logging.getLogger(__name__)

# Pane phases (distinct from the *step* states persisted on the session).
IDLE = "idle"
COUNTDOWN = "countdown"
RECORDING = "recording"
REVIEW = "review"


class RecordingPane(QWidget):
    contentChanged = pyqtSignal()          # the session's steps/segments changed
    statusChanged = pyqtSignal(str, bool)  # (text, is_error) for the footer
    busyChanged = pyqtSignal(bool)         # True while a take is in progress
    detailsRequested = pyqtSignal(str)     # step id: open the measurement's details
    _overlayDone = pyqtSignal()            # the overlay replay reached the end of the clip

    _overlay_src = None                    # sidecar replaying the take with landmarks
    extra_rows = None                      # host hook: step → [(label, value)] for the info panel
    _take_meta: dict = {}                  # provenance of the take being filmed

    def __init__(self, parent=None, pipeline: SegmentPipeline | None = None) -> None:
        super().__init__(parent)
        self.session: VideoSession | None = None
        self._device = None
        self._phase = IDLE
        self._current_id = ""
        self._t_left = 0.0
        self._recorded_path: str | None = None   # set from the reader thread

        # The analyse → archive → cleanup pipeline is shared with the cut
        # screen when the host passes one in; the pane only shows its state.
        self._pipeline = pipeline or SegmentPipeline(self)
        self._pipeline.previewReady.connect(lambda m: self._on_preview(m, "analysis"))
        self._pipeline.stageText.connect(self._on_stage_text)
        self._pipeline.analysisStarted.connect(self._on_analysis_started)
        self._pipeline.analysisFinished.connect(self._on_pipeline_finished)
        self._pipeline.analysisFailed.connect(self._on_pipeline_failed)
        self._pipeline.jobDone.connect(self._on_job_done)
        self._pipeline.rawReleased.connect(self._on_raw_released)
        self._pipeline.contentChanged.connect(self.contentChanged.emit)
        # One slot per preview source. They used to share one field, and the
        # picture flipped between the live camera and the replayed take,
        # whichever had written last.
        self._previews: dict = {"live": None, "overlay": None, "analysis": None}
        self._overlay_track: dict | None = None    # stored track being replayed
        self._overlay_t = 0.0                      # seconds into the clip the overlay shows
        self._overlayDone.connect(self._on_overlay_done)
        self._build()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(33)
        self._tick_timer.timeout.connect(self._tick)

    # ── layout ───────────────────────────────────────────────────
    def _build(self) -> None:
        # Everything lives on one scrollable page: the video keeps its height,
        # and when summary, info panel and plot together need more room than
        # the window has, the page scrolls instead of clipping rows.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; }")
        page = QWidget()
        page.setObjectName("scrollPage")
        page.setStyleSheet("#scrollPage { background: transparent; }")
        self._scroll.setWidget(page)
        outer.addWidget(self._scroll)
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 8, 0)
        root.setSpacing(8)

        # Live preview while filming, the recorded take while reviewing.
        self._view = QStackedWidget()
        self._preview = WebcamPreview()
        self._preview.setMinimumHeight(300)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        # Not a plain QVideoWidget: our takes are stored raw (unmirrored camera
        # view) and have to be shown under the webcam mirror setting, like the
        # live preview and the overlay — otherwise left and right differ
        # between the player and the overlay.
        self._video = VideoView()
        self._player.setVideoSink(self._video.sink)
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
        # The analysed take is a measurement in the record: open its detail
        # view (feature table + curves) straight from here.
        self._details_btn = QPushButton("Details…")
        self._details_btn.setToolTip("Kennwerte und Kurven dieser Auswertung")
        self._details_btn.clicked.connect(
            lambda: self._current_id and self.detailsRequested.emit(self._current_id))
        for b in (self._record_btn, self._cancel_btn, self._keep_btn,
                  self._details_btn, self._retake_btn):
            actions.addWidget(b)
        root.addLayout(actions)

        self._analysis_lbl = QLabel()
        self._analysis_lbl.setWordWrap(True)
        self._analysis_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        root.addWidget(self._analysis_lbl)
        # When / how / with what this take was filmed, archived and analysed —
        # and whether all of that still lines up (video.meta).
        self._meta = MetaPanel("Aufnahme-Info")
        self._meta.setVisible(False)
        root.addWidget(self._meta)
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
        if self._pipeline.parent() is self:      # own pipeline: nothing else uses it
            self._pipeline.teardown()
        self._plot.setVisible(False)
        self._analysis_lbl.setText("")
        self._meta.clear()
        self._meta.setVisible(False)
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
            if step.state == STEP_CONFIRMED and self._job is None:
                self._analysis_lbl.setText(self._result_summary(step))
        else:
            self._set_phase(IDLE)
            if not self._analysing:
                # An open step has no result yet — nothing of the previously
                # viewed take may linger under it.
                self._analysis_lbl.setText("")
                self._plot.setVisible(False)
        self._refresh_meta()

    def _refresh_meta(self) -> None:
        step = self._step
        seg = self._segment_of(step)
        if seg is None or self.session is None:
            self._meta.clear()
            self._meta.setVisible(False)
            return
        from video.meta import describe_segment, segment_issues
        rows = describe_segment(self.session, seg, step)
        if self.extra_rows is not None:
            try:
                rows = list(self.extra_rows(step)) + rows
            except Exception:
                log.debug("extra_rows fehlgeschlagen", exc_info=True)
        self._meta.set_content(rows, segment_issues(self.session, seg, step))
        self._meta.setVisible(True)

    def _segment_of(self, step):
        if step is None or self.session is None or not step.segment_id:
            return None
        return next((s for s in self.session.segments if s.id == step.segment_id), None)

    def _has_result(self, step) -> bool:
        seg = self._segment_of(step)
        return bool(seg and any((r or {}).get("features") for r in seg.results.values()))

    def _result_summary(self, step) -> str:
        """One line about the stored analysis of a confirmed step."""
        from video.meta import result_summary
        seg = self._segment_of(step)
        if seg is None or not seg.results:
            return "" if step.is_documentation else "Noch nicht ausgewertet."
        return result_summary(seg)

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
        self._details_btn.setVisible(phase == REVIEW and confirmed and self._has_result(step))
        self._record_btn.setEnabled(self._device is not None and step is not None)
        self._auto_cb.setVisible(bool(step) and not step.is_documentation)

        overlay = phase == REVIEW and self._overlay_cb.isChecked()
        if self._analysing or overlay:
            self._view.setCurrentWidget(self._preview)       # landmarks on top
        else:
            self._view.setCurrentWidget(self._video if phase == REVIEW else self._preview)
        self._bar.setVisible(phase in (COUNTDOWN, RECORDING) or bool(self._analysing))
        self._overlay_cb.setVisible(phase == REVIEW)
        # The privacy choice is made *for this recording*: visible from the
        # moment the step is open until the take is confirmed.
        self._deface_cb.setVisible(bool(step) and phase in (IDLE, REVIEW) and not confirmed)
        if phase != REVIEW:
            self._stop_overlay()

        if phase == IDLE and step is not None:
            self._status("Bereit — Aufnahme starten, wenn der Proband bereit ist."
                         if self._device is not None else
                         "Keine Kamera — Aufnahme nicht möglich.", self._device is None)
        elif phase == REVIEW:
            self._status("Aufnahme prüfen: übernehmen oder wiederholen."
                         if not confirmed else "Bestätigt und als Segment übernommen.")
        if was_busy != self.busy:
            self.busyChanged.emit(self.busy)

    def _enter_review(self, clip_path: str) -> None:
        from capture.config import source_mirrored
        self._video.set_mirrored(source_mirrored("webcam"))   # own take → webcam setting
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
            pos_s = max(0.0, self._player.position() / 1000.0)
            self._set_phase(self._phase)          # switch the view first …
            self._start_overlay(step.clip_path, start_s=pos_s)   # … keeps the status note
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

    def _start_overlay(self, clip_path: str, start_s: float = 0.0) -> None:
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
            # Pick up where the plain player was; the clip then runs to its
            # end once and continues looping from the top (_on_overlay_done).
            if start_s > 0.2:
                src.play_from(str(clip_path), start_s)
            else:
                start_s = 0.0
                src.play_loop(str(clip_path))
            src.set_done_callback(lambda: self._overlayDone.emit())
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
        self._overlay_t = start_s
        self._player.pause()
        self._previews["overlay"] = None
        self._preview.set_placeholder("Wiedergabe mit Tracking wird gestartet …")
        self._preview.clear()
        where = f" (ab {start_s:.1f} s)".replace(".", ",") if start_s else ""
        self._status(f"Overlay: gespeicherte Analyse{where}" if track is not None else
                     f"Overlay: neu berechnet{where} — für diesen Take liegt keine "
                     "gespeicherte Analyse vor")

    def _on_overlay_done(self) -> None:
        """A replay that started mid-clip reached the end: loop from the top."""
        src = self._overlay_src
        if src is None:
            return
        try:
            src.play_loop()
            src.start_tracking(lambda _f: None)
        except Exception:
            log.debug("Overlay konnte nicht von vorn weiterlaufen", exc_info=True)

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
            # Hand the position back: the plain player continues where the
            # overlay replay was, instead of jumping to wherever it paused.
            self._player.setPosition(int(self._overlay_t * 1000))
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
        from video.meta import capture_meta
        self._take_meta = capture_meta(self._device, take=step.takes, take_file=str(path))
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

    # ── post-confirm pipeline (ui/segment_pipeline.py) ───────────
    @property
    def _job(self):
        return self._pipeline.job

    @_job.setter
    def _job(self, value) -> None:
        self._pipeline.job = value

    @property
    def _runner(self):
        return self._pipeline.runner

    def _job_step(self):
        return self._pipeline._job_step()

    @property
    def _analysis_t0(self) -> float:
        return self._pipeline.analysis_t0

    @property
    def _analysing(self) -> str:
        """The step id being analysed right now ("" otherwise)."""
        return self._pipeline.analysing_step_id()

    def enqueue(self, session: VideoSession, step_id: str, segment, analyse: bool) -> None:
        """Public entry for the host: (re)run the pipeline on a confirmed step."""
        self._enqueue_step(session, step_id, segment, analyse, deface=None)

    def _enqueue_step(self, session: VideoSession, step_id: str, segment,
                      analyse: bool, deface: str | None) -> None:
        self._pipeline.enqueue(session, segment, step_id=step_id, analyse=analyse, deface=deface)

    def _on_analysis_finished(self, test, features: dict) -> None:
        """Kept for callers/tests that feed a result directly."""
        self._pipeline._on_analysis_finished(test, features)

    def _on_analysis_failed(self, msg: str) -> None:
        self._pipeline._on_analysis_failed(msg)

    _needs_eye_reference = staticmethod(needs_eye_reference)

    def _on_stage_text(self, text: str) -> None:
        self._analysis_lbl.setText(text)

    def _on_analysis_started(self, session, seg) -> None:
        # Show the analysis, not the review player: the sidecar's preview
        # carries the frame with the tracked landmarks, and a progress bar
        # says how far the replay has come — otherwise this looked like a
        # video playing with nothing happening and no end in sight.
        self._plot.clear_plot()
        self._plot.setVisible(True)
        self._player.pause()
        self._previews["analysis"] = None
        self._preview.set_placeholder("Auswertung wird gestartet …")
        self._preview.clear()
        self._view.setCurrentWidget(self._preview)
        self._bar.setValue(0)
        self._bar.setVisible(True)

    def _on_pipeline_finished(self, session, seg, features: dict) -> None:
        self._refresh_plot(force=True)
        self._end_analysis_view()

    def _on_pipeline_failed(self, session, seg, msg: str) -> None:
        self._end_analysis_view()

    def _on_job_done(self, session, seg) -> None:
        self._refresh_meta()

    def _on_raw_released(self, raw: str) -> None:
        # The review player may hold the raw take open; Windows then refuses
        # the delete. QUrl.toLocalFile() comes back with forward slashes, so
        # compare normalised — or the file stays locked for the session.
        norm = lambda p: os.path.normcase(os.path.abspath(p)) if p else ""
        if raw and norm(self._player.source().toLocalFile()) == norm(raw):
            self._player.stop()
            step = self._step
            clip = step.clip_path if step and step.clip_path and norm(step.clip_path) != norm(raw) else ""
            self._player.setSource(QUrl.fromLocalFile(clip) if clip else QUrl())

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
        if source == "overlay" and msg.get("t") is not None:
            self._overlay_t = float(msg["t"])

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
        from video.meta import note_recorded
        step.meta = note_recorded(dict(self._take_meta),
                                  getattr(self._device, "last_recorded", None))
        self.session.save()
        self.contentChanged.emit()
        self._enter_review(path)
