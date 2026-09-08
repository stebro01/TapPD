"""Protocol-guided recording: film a session step by step.

Walks the clinician through a ``video.protocol.Protocol`` one step at a time —
countdown, record, review the take, then keep or repeat it. Confirming a step
turns it into a ``Segment``, from which point the existing VideoLab analysis
and export path takes over.

Nothing is analysed here. The clinician confirms every step, and analysis stays
a separate, deliberate action afterwards, so the person who saw the patient
move decides what is worth measuring.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.widgets.webcam_preview import WebcamPreview
from video.store import STEP_CONFIRMED, STEP_PENDING, STEP_RECORDED, VideoSession

log = logging.getLogger(__name__)

# Screen phases (distinct from the *step* states persisted on the session).
IDLE = "idle"
COUNTDOWN = "countdown"
RECORDING = "recording"
REVIEW = "review"

_MARKERS = {STEP_PENDING: "○", STEP_RECORDED: "◐", STEP_CONFIRMED: "✔"}


class RecordingScreen(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.session: VideoSession | None = None
        self._device = None
        self._owns_device = False
        self._phase = IDLE
        self._current_id = ""
        self._t_left = 0.0
        self._latest_preview = None
        self._recorded_path: str | None = None   # set from the reader thread
        # Saved state of a shared capture device, restored on leave.
        self._prev_preview_cb = None
        self._prev_recorded_cb = None
        self._prev_face_on = False
        self._started_stream = False
        self._build()

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(33)
        self._tick_timer.timeout.connect(self._tick)

    # ── layout ───────────────────────────────────────────────────
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        head = QHBoxLayout()
        back = QPushButton("← Zurück")
        back.clicked.connect(self._on_back)
        head.addWidget(back)
        self._title = QLabel()
        self._title.setStyleSheet("font-size: 16px; font-weight: 600;")
        head.addWidget(self._title, 1)
        self._progress_lbl = QLabel()
        self._progress_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        head.addWidget(self._progress_lbl)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)

        self._steps = QListWidget()
        self._steps.setFixedWidth(260)
        self._steps.currentRowChanged.connect(self._on_step_selected)
        body.addWidget(self._steps)

        right = QVBoxLayout()
        right.setSpacing(8)

        # Live preview while filming, the recorded take while reviewing.
        self._view = QStackedWidget()
        self._preview = WebcamPreview()
        self._preview.setMinimumHeight(320)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._video = QVideoWidget()
        self._video.setStyleSheet("background:#000;")
        self._player.setVideoOutput(self._video)
        self._view.addWidget(self._preview)
        self._view.addWidget(self._video)
        right.addWidget(self._view, 1)

        self._step_title = QLabel()
        self._step_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        right.addWidget(self._step_title)

        self._instruction = QLabel()
        self._instruction.setWordWrap(True)
        self._instruction.setStyleSheet("font-size: 14px;")
        self._instruction.setMinimumHeight(48)
        right.addWidget(self._instruction)

        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        right.addWidget(self._bar)

        # Live detection readout: proof the tracking works *before* filming,
        # rather than finding out afterwards that nothing was recognised.
        self._detect_lbl = QLabel()
        self._detect_lbl.setStyleSheet("font-size: 12px;")
        right.addWidget(self._detect_lbl)


        actions = QHBoxLayout()
        actions.addStretch()
        self._record_btn = QPushButton("● Aufnahme starten")
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
        right.addLayout(actions)

        body.addLayout(right, 1)
        root.addLayout(body)

        # ── status footer ────────────────────────────────────────
        footer = QHBoxLayout()
        cam_lbl = QLabel("Kamera:")
        cam_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        footer.addWidget(cam_lbl)
        self._cam_combo = QComboBox()
        self._cam_combo.setMinimumWidth(220)
        self._cam_combo.currentIndexChanged.connect(self._on_camera_changed)
        footer.addWidget(self._cam_combo)
        footer.addStretch()
        self._status = QLabel()
        self._status.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        footer.addWidget(self._status)
        root.addLayout(footer)

    # ── lifecycle ────────────────────────────────────────────────
    def on_enter(self, session: VideoSession) -> None:
        self.session = session
        self._title.setText(session.protocol_name or "Aufnahme")
        self._acquire_device()
        self._populate_cameras()
        self._refresh_steps()
        # Resume where the protocol was left off rather than at the top.
        nxt = session.next_open_step()
        self._select(nxt.id if nxt else (session.steps[-1].id if session.steps else ""))
        self._tick_timer.start()

    def on_leave(self) -> None:
        self._tick_timer.stop()
        self._player.stop()
        self._release_device()
        self.session = None

    def _acquire_device(self) -> None:
        """Prefer the app's live webcam; only spin up our own if there is none.

        Two processes cannot hold the same camera — an OBSBOT refuses outright —
        so sharing the existing device is not just tidier, it is necessary.
        """
        from capture.mediapipe_capture import WebcamSource

        dev = getattr(self.main_window, "capture_device", None)
        if isinstance(dev, WebcamSource) and dev.is_connected() and not dev.replay_path:
            self._device, self._owns_device = dev, False
        else:
            ok, issues = WebcamSource.sidecar_ready()
            if not ok:
                self._set_status(issues[0], error=True)
                self._device = None
                return
            try:
                dev = WebcamSource()
                dev.connect()
                dev.start_tracking(lambda _f: None)
                self._device, self._owns_device = dev, True
            except Exception as e:
                log.warning("Aufnahme-Webcam nicht verfügbar: %s", e)
                self._set_status(f"Kamera nicht verfügbar: {e}", error=True)
                self._device = None
                return

        # Remember what we are about to change on a *shared* device, so leaving
        # this screen hands it back the way we found it.
        self._prev_preview_cb = getattr(self._device, "_preview_callback", None)
        self._prev_recorded_cb = getattr(self._device, "_recorded_callback", None)
        self._prev_face_on = bool(getattr(self._device, "_face_on", False))
        self._started_stream = False

        self._device.set_preview_callback(self._on_preview)
        self._device.set_recorded_callback(
            lambda p: setattr(self, "_recorded_path", p))

        # A connected device is not necessarily a streaming one — the sidecar
        # only sends preview frames while a capture is running. Without this the
        # screen sat on "Warte auf Kamerabild" forever.
        if not getattr(self._device, "_recording", False):
            self._device.start_tracking(lambda _f: None)
            self._started_stream = True

        self._device.configure(num_hands=2)   # both hands, so the overlay shows both
        self._device.enable_face(True)        # face + iris as visible proof it works
        self._device.enable_preview(True)
        self._preview.set_placeholder("Kamera wird gestartet …")

    def _populate_cameras(self) -> None:
        """Fill the footer's camera picker from the sidecar's enumeration."""
        self._cam_combo.blockSignals(True)
        self._cam_combo.clear()
        cams = []
        if self._device is not None:
            try:
                cams = self._device.list_cameras()
            except Exception:
                log.debug("Kameraliste nicht abrufbar", exc_info=True)
        for idx, name in cams:
            self._cam_combo.addItem(name, idx)
        if not cams:
            self._cam_combo.addItem("Keine Kamera gefunden", -1)
        current = getattr(self._device, "camera_index", 0)
        pos = self._cam_combo.findData(current)
        if pos >= 0:
            self._cam_combo.setCurrentIndex(pos)
        self._cam_combo.setEnabled(bool(cams) and self._device is not None)
        self._cam_combo.blockSignals(False)

    def _on_camera_changed(self, row: int) -> None:
        """Switch the live camera without leaving the screen."""
        if self._device is None or row < 0:
            return
        cam = self._cam_combo.currentData()
        if cam is None or cam < 0 or cam == getattr(self._device, "camera_index", 0):
            return

        # The sidecar opens the device on `start`, so the stream has to be
        # cycled for the new index to take effect.
        streaming = bool(getattr(self._device, "_recording", False))
        if streaming:
            self._device.stop_tracking()
        self._device.camera_index = int(cam)
        if streaming:
            self._device.start_tracking(lambda _f: None)
        self._device.enable_preview(True)
        self._preview.set_placeholder("Kamera wird gewechselt …")
        self._preview.clear()

        # Remember it like the tracking screen does, so the choice outlives
        # this screen instead of silently reverting.
        try:
            from app_settings import app_settings
            app_settings().setValue("camera_index", int(cam))
        except Exception:
            log.debug("Kameraindex konnte nicht gespeichert werden", exc_info=True)
        self._set_status(f"Kamera gewechselt: {self._cam_combo.currentText()}")

    def _release_device(self) -> None:
        if self._device is None:
            return
        try:
            self._device.set_recorded_callback(self._prev_recorded_cb)
            self._device.set_preview_callback(self._prev_preview_cb)
            self._device.enable_face(self._prev_face_on)
            if self._started_stream:
                self._device.stop_tracking()
            if self._owns_device:
                self._device.disconnect()
        except Exception:
            log.debug("Aufräumen der Aufnahme-Kamera fehlgeschlagen", exc_info=True)
        self._device = None
        self._owns_device = False
        self._started_stream = False

    # ── step list ────────────────────────────────────────────────
    def _refresh_steps(self) -> None:
        if self.session is None:
            return
        keep = self._current_id
        self._steps.blockSignals(True)
        self._steps.clear()
        for i, st in enumerate(self.session.steps, start=1):
            mark = _MARKERS.get(st.state, "○")
            suffix = "" if st.is_documentation else f" · {st.hand}"
            item = QListWidgetItem(f"{mark}  {i}. {st.title}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, st.id)
            self._steps.addItem(item)
        self._steps.blockSignals(False)
        done, total = self.session.progress
        self._progress_lbl.setText(f"{done} / {total} bestätigt")
        self._current_id = keep

    def _select(self, step_id: str) -> None:
        for row in range(self._steps.count()):
            if self._steps.item(row).data(Qt.ItemDataRole.UserRole) == step_id:
                self._steps.setCurrentRow(row)
                return
        self._current_id = step_id
        self._show_step()

    def _on_step_selected(self, row: int) -> None:
        if row < 0 or self._phase in (COUNTDOWN, RECORDING):
            return      # don't switch away mid-take
        item = self._steps.item(row)
        self._current_id = item.data(Qt.ItemDataRole.UserRole) if item else ""
        self._show_step()

    @property
    def _step(self):
        return self.session.step(self._current_id) if self.session else None

    def _show_step(self) -> None:
        step = self._step
        if step is None:
            self._step_title.setText("")
            self._instruction.setText("")
            self._set_phase(IDLE)
            return
        kind = "Dokumentation" if step.is_documentation else step.paradigm
        self._step_title.setText(f"{step.title}  ·  {step.duration_s:g}s  ·  {kind}")
        self._instruction.setText(step.instruction or "—")
        # A confirmed or filmed step opens in review, so the take can be watched.
        if step.state in (STEP_RECORDED, STEP_CONFIRMED) and step.clip_path:
            self._enter_review(step.clip_path)
        else:
            self._set_phase(IDLE)

    # ── phases ───────────────────────────────────────────────────
    def _set_phase(self, phase: str) -> None:
        self._phase = phase
        step = self._step
        confirmed = bool(step and step.state == STEP_CONFIRMED)

        self._record_btn.setVisible(phase == IDLE)
        self._cancel_btn.setVisible(phase in (COUNTDOWN, RECORDING))
        self._keep_btn.setVisible(phase == REVIEW and not confirmed)
        self._retake_btn.setVisible(phase == REVIEW)
        self._retake_btn.setText("↻ Neu aufnehmen" if confirmed else "↻ Wiederholen")
        self._record_btn.setEnabled(self._device is not None and step is not None)

        self._view.setCurrentWidget(self._video if phase == REVIEW else self._preview)
        self._bar.setVisible(phase in (COUNTDOWN, RECORDING))
        # Swapping the camera mid-take would cut the recording in half.
        self._cam_combo.setEnabled(phase not in (COUNTDOWN, RECORDING)
                                   and self._cam_combo.count() > 0
                                   and self._device is not None)

        if phase == IDLE and step is not None:
            self._set_status("Bereit — Aufnahme starten, wenn der Patient bereit ist.")
        elif phase == REVIEW:
            self._set_status("Aufnahme prüfen: übernehmen oder wiederholen."
                             if not confirmed else "Bestätigt und als Segment übernommen.")

    def _enter_review(self, clip_path: str) -> None:
        self._player.setSource(QUrl.fromLocalFile(str(clip_path)))
        self._player.play()
        self._set_phase(REVIEW)

    def _set_status(self, text: str, error: bool = False) -> None:
        color = theme.DANGER if error else theme.TEXT_SECONDARY
        self._status.setStyleSheet(f"color: {color};")
        self._status.setText(text)

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
        self._set_status(f"Aufnahme läuft — {step.duration_s:g}s")
        log.info("Protokollschritt '%s' wird aufgenommen: %s", step.id, path)

    def _on_cancel(self) -> None:
        """Abort the current take. The sidecar finishes writing its file; we
        simply do not adopt it, so the step stays open and the take counter has
        already moved on — the aborted file cannot be mistaken for the good one."""
        self._t_left = 0.0
        self._recorded_path = None
        self._set_phase(IDLE)
        self._set_status("Aufnahme abgebrochen.")

    def _on_keep(self) -> None:
        step = self._step
        if step is None or self.session is None:
            return
        try:
            self.session.confirm_step(step.id)
        except ValueError as e:
            self._set_status(str(e), error=True)
            return
        self.session.save()
        self._refresh_steps()
        nxt = self.session.next_open_step()
        if nxt is not None:
            self._select(nxt.id)
        else:
            self._show_step()
            self._set_status("Protokoll vollständig — alle Schritte bestätigt.")

    def _on_retake(self) -> None:
        step = self._step
        if step is None or self.session is None:
            return
        self._player.stop()
        self.session.retake_step(step.id)
        self.session.save()
        self._refresh_steps()
        self._show_step()
        self._set_status("Schritt wieder offen — erneut aufnehmen.")

    def _on_back(self) -> None:
        if self._phase in (COUNTDOWN, RECORDING):
            self._on_cancel()
        if self.session is not None:
            self.session.save()
        self.main_window.close_recording()

    # ── frame loop ───────────────────────────────────────────────
    def _on_preview(self, msg: dict) -> None:
        self._latest_preview = msg      # reader thread: only store, never draw

    def _update_detection(self, msg: dict) -> None:
        """One line saying what is currently being recognised.

        The labels are the corrected ones from the sidecar, so "links" here
        means the patient's left hand — the same side the analysis will use.
        """
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
        ok = bool(hands) and face
        color = theme.ACCENT if ok else theme.TEXT_SECONDARY
        self._detect_lbl.setStyleSheet(f"font-size: 12px; color: {color};")
        self._detect_lbl.setText("   ·   ".join(parts))

    def _tick(self) -> None:
        try:
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
                self._set_status(f"Startet in {max(0, int(self._t_left) + 1)} …")
                if self._t_left <= 0:
                    self._begin_capture()

            elif self._phase == RECORDING:
                self._t_left -= self._tick_timer.interval() / 1000.0
                step = self._step
                total = max(0.01, step.duration_s if step else 1.0)
                self._bar.setValue(int(100 * (1 - max(0.0, self._t_left) / total)))
                # The sidecar's "recorded" message is authoritative for the end,
                # not our countdown — the file must exist before we adopt it.
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
            self._set_status("Aufnahme fehlgeschlagen — keine Datei entstanden.",
                             error=True)
            return
        self.session.mark_recorded(step.id, path)
        self.session.save()
        self._refresh_steps()
        self._enter_review(path)


class ProtocolChooser(QDialog):
    """Pick what to film: a stored protocol, or a single paradigm.

    Both answers come back as a ``Protocol`` — a single paradigm is simply one
    of length 1 — so the caller has no second case to handle.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Aufnahme starten")
        self.setMinimumWidth(460)

        from paradigms import registry
        from video.protocol import list_protocols

        self._protocols = list_protocols()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self._rb_protocol = QRadioButton("Protokoll")
        self._rb_protocol.setChecked(True)
        layout.addWidget(self._rb_protocol)
        self._proto_combo = QComboBox()
        for p in self._protocols:
            self._proto_combo.addItem(
                f"{p.name}  ({len(p.steps)} Schritte, {p.total_duration_s:g}s)", p.id)
        if not self._protocols:
            self._proto_combo.addItem("Kein Protokoll gefunden", "")
            self._proto_combo.setEnabled(False)
            self._rb_protocol.setEnabled(False)
        layout.addWidget(self._proto_combo)

        self._rb_single = QRadioButton("Einzelnes Paradigma")
        self._rb_single.setChecked(not self._protocols)
        layout.addWidget(self._rb_single)

        row = QHBoxLayout()
        self._para_combo = QComboBox()
        for key in registry.all_keys():
            spec = registry.get(key)
            self._para_combo.addItem((spec.label or key).replace("\n", " "), key)
        row.addWidget(self._para_combo, 1)
        self._hand_combo = QComboBox()
        for label, value in (("rechts", "right"), ("links", "left"), ("beide", "both")):
            self._hand_combo.addItem(label, value)
        row.addWidget(self._hand_combo)
        self._dur = QSpinBox()
        self._dur.setRange(5, 120)
        self._dur.setValue(20)
        self._dur.setSuffix(" s")
        row.addWidget(self._dur)
        layout.addLayout(row)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self._hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for w in (self._rb_protocol, self._rb_single):
            w.toggled.connect(self._sync)
        self._proto_combo.currentIndexChanged.connect(self._sync)
        self._para_combo.currentIndexChanged.connect(self._sync)
        self._hand_combo.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self, *_) -> None:
        use_protocol = self._rb_protocol.isChecked()
        self._proto_combo.setEnabled(use_protocol and bool(self._protocols))
        for w in (self._para_combo, self._hand_combo, self._dur):
            w.setEnabled(not use_protocol)
        # Surface the loader's notes here rather than mid-session: "needs face
        # tracking" is something to know before the patient is sitting down.
        try:
            notes = [n.message for n in self.protocol().notes]
        except Exception as e:
            notes = [str(e)]
        self._hint.setText("Hinweis: " + "  ".join(notes) if notes else "")

    def protocol(self):
        """The chosen protocol (raises if it cannot be built)."""
        from video.protocol import load_protocol, protocol_for_paradigm

        if self._rb_protocol.isChecked() and self._protocols:
            return load_protocol(self._proto_combo.currentData())
        return protocol_for_paradigm(self._para_combo.currentData(),
                                     hand=self._hand_combo.currentData(),
                                     duration_s=float(self._dur.value()))
