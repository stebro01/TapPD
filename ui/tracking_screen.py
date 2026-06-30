"""Tracking screen: choose the capture source (Leap Motion / Webcam), pick a
camera, and watch a live hand-tracking preview before committing.

Conflict-free preview model
---------------------------
On entering, the currently active capture device is disconnected to free the
hardware.  The screen then drives a *candidate* device for the selected source:

* Webcam  → a :class:`WebcamSource` (spawns the Python-3.12 sidecar);
  preview shows the live RGB frame with a landmark overlay plus the 2D skeleton.
* Leap    → a ``LeapSource``; preview shows the 2D skeleton only.

"Übernehmen" adopts the candidate as the app's active device (via
``main_window.switch_capture_device``).  "Zurück" without applying reconnects the
original device.

Frames arrive on the device's background reader thread, so they are stashed and
rendered from a QTimer on the GUI thread (same pattern as the gesture lab).

The layout reserves space for a future "Gesicht & Augen" tab (MediaPipe Face
Landmarker) — see the plan's conceptual section.
"""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import Qt, QTimer, QPointF
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)

from capture.base_capture import HandFrame
from capture.config import cfg as capture_cfg
from capture.mediapipe_capture import WebcamSource
from ui.hand_visualization import HandVisualizationWidget
from ui.widgets.webcam_preview import WebcamPreview

HAND_STALE_S = float(capture_cfg("preview", "hand_stale_s", default=0.3))


class _FaceView(QWidget):
    """Renders the 478-point face mesh (incl. iris) on its own dark panel,
    auto-fit to the face and mirrored in X (selfie view)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(200, 160)
        self._face: list = []

    def set_face(self, face: list | None) -> None:
        self._face = face or []
        self.update()

    def clear(self) -> None:
        self._face = []
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#212121"))
        if not self._face:
            p.setPen(QColor("#9E9E9E"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Kein Gesicht erkannt")
            p.end()
            return

        xs = [lm[0] for lm in self._face]
        ys = [lm[1] for lm in self._face]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        margin = 18
        scale = min((w - 2 * margin) / max(maxx - minx, 1e-3),
                    (h - 2 * margin) / max(maxy - miny, 1e-3))

        def pt(lm):
            return QPointF(w / 2 - (lm[0] - cx) * scale,  # mirror X (selfie)
                           h / 2 + (lm[1] - cy) * scale)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(120, 230, 140, 160))
        for lm in self._face:
            p.drawEllipse(pt(lm), 1.2, 1.2)
        p.setBrush(QColor("#FF4081"))  # iris / eyes
        for i in range(468, min(478, len(self._face))):
            p.drawEllipse(pt(self._face[i]), 2.6, 2.6)
        p.end()


class TrackingScreen(QWidget):
    SOURCE_LEAP = "leap"
    SOURCE_WEBCAM = "mediapipe"
    SOURCE_SIM = "sim"   # a recorded clip looped through MediaPipe (hand + face)

    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window

        self._candidate = None              # primary hand source being previewed
        self._owns_candidate = False
        self._face_candidate = None         # webcam running only for camera + face
        # (used when the hand source is Leap → Leap hands + webcam face in parallel)
        self._building = False              # re-entrancy guard for _rebuild_candidate
        self._recorded_path = None          # set by reader thread when a clip finishes
        self._record_webcam = None          # temp live webcam used only for recording
        self._recording_clip = False
        # Latest frame per hand: hand_type -> (HandFrame, perf_counter timestamp).
        # Timestamped so a hand that leaves the view is cleared (no stale model /
        # confidence) — the webcam sends empty hand-lists that never reach _on_frame.
        self._latest_by_hand: dict[str, tuple] = {}
        self._latest_preview: tuple | None = None  # (jpeg_b64, landmarks)
        self._restore = None                # (mode, idx, flip) to reconnect on Back
        self._applied = False

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 fps GUI refresh
        self._timer.timeout.connect(self._tick)

    # ── UI ────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # Header
        header = QHBoxLayout()
        back = QPushButton("←  Zurück")
        back.setProperty("cssClass", "flat")
        back.clicked.connect(self._on_back)
        header.addWidget(back)
        title = QLabel("Eingabequelle")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #263238;")
        header.addWidget(title, 1, Qt.AlignmentFlag.AlignCenter)
        header.addSpacing(90)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(20)
        root.addLayout(body, 1)

        # Left: controls
        controls = QVBoxLayout()
        controls.setSpacing(12)
        body.addLayout(controls, 0)

        controls.addWidget(self._section_label("Quelle"))
        self._group = QButtonGroup(self)
        self._rb_webcam = QRadioButton("Webcam")
        self._rb_leap = QRadioButton("Leap Motion")
        self._rb_sim = QRadioButton("Sim (Loop)")
        for rb in (self._rb_webcam, self._rb_leap, self._rb_sim):
            rb.setStyleSheet("font-size: 15px; padding: 4px;")
            self._group.addButton(rb)
        controls.addWidget(self._rb_webcam)
        controls.addWidget(self._rb_leap)
        # Sim row: the radio + a Save button + a duration spinbox. Recording only
        # makes sense from a live webcam, so these are enabled only in Webcam mode
        # (record now → switch to Sim to loop it as the default).
        sim_row = QHBoxLayout()
        sim_row.addWidget(self._rb_sim)
        sim_row.addStretch()
        self._record_btn = QPushButton("Save")
        self._record_btn.setFixedHeight(18)
        self._record_btn.setStyleSheet("QPushButton { min-height: 0px; padding: 0px 12px; }")
        self._record_btn.setToolTip(
            "Nimmt für die gewählte Dauer von der Live-Webcam auf und speichert "
            "als Sim-Default (überschreibt den bestehenden Clip). Nur im Webcam-Modus.")
        self._record_btn.clicked.connect(self._on_record_clicked)
        sim_row.addWidget(self._record_btn)
        from video.config import cfg as video_cfg
        self._dur_spin = QSpinBox()
        self._dur_spin.setRange(int(video_cfg("record", "min_seconds", default=1)),
                                int(video_cfg("record", "max_seconds", default=120)))
        self._dur_spin.setValue(int(video_cfg("record", "default_seconds", default=10)))
        self._dur_spin.setSuffix(" s")
        self._dur_spin.setFixedSize(64, 18)         # narrow (~1/3) and low
        # The global theme forces min-height/padding on inputs → reset it locally.
        self._dur_spin.setStyleSheet("QSpinBox { min-height: 0px; padding: 0px 2px; }")
        self._dur_spin.setToolTip("Aufnahmedauer in Sekunden")
        sim_row.addWidget(self._dur_spin)
        controls.addLayout(sim_row)
        self._rb_webcam.toggled.connect(lambda on: on and self._select_source(self.SOURCE_WEBCAM))
        self._rb_leap.toggled.connect(lambda on: on and self._select_source(self.SOURCE_LEAP))
        self._rb_sim.toggled.connect(lambda on: on and self._select_source(self.SOURCE_SIM))

        controls.addSpacing(8)
        controls.addWidget(self._section_label("Kamera"))
        self._cam_combo = QComboBox()
        self._cam_combo.setMinimumWidth(220)
        self._cam_combo.currentIndexChanged.connect(self._on_camera_changed)
        controls.addWidget(self._cam_combo)

        self._flip_cb = QCheckBox("Links/Rechts spiegeln")
        self._flip_cb.setToolTip("Webcams spiegeln das Bild – bei vertauschter Händigkeit aktivieren")
        self._flip_cb.stateChanged.connect(self._on_flip_changed)
        controls.addWidget(self._flip_cb)

        self._face_cb = QCheckBox("Gesicht & Augen erkennen")
        self._face_cb.setChecked(True)
        self._face_cb.setToolTip("Optional – nur in der Vorschau aktiv; ausschalten spart Rechenleistung")
        self._face_cb.stateChanged.connect(self._on_face_toggled)
        controls.addWidget(self._face_cb)

        controls.addSpacing(12)
        self._apply_btn = QPushButton("Übernehmen")
        self._apply_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; border: none; border-radius: 6px; "
            "padding: 12px 18px; font-size: 15px; font-weight: 600; }"
            "QPushButton:hover { background: #1565C0; }"
        )
        self._apply_btn.clicked.connect(self._on_apply)
        controls.addWidget(self._apply_btn)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("font-size: 12px; color: #757575;")
        controls.addWidget(self._status)
        controls.addStretch(1)

        # Right: preview — camera+overlay (left) | [face on top, hands below] (right)
        preview = QHBoxLayout()
        preview.setSpacing(12)
        body.addLayout(preview, 1)
        hdr = "font-size: 12px; font-weight: 700; color: #546E7A;"

        # Left: camera image with face + hand overlay.
        self._cam_panel = QWidget()
        cam_col = QVBoxLayout(self._cam_panel)
        cam_col.setContentsMargins(0, 0, 0, 0)
        cam_col.setSpacing(4)
        cam_lbl = QLabel("Kamera (Gesicht + Hände)")
        cam_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cam_lbl.setStyleSheet(hdr)
        self._webcam_preview = WebcamPreview()
        cam_col.addWidget(cam_lbl)
        cam_col.addWidget(self._webcam_preview, 1)
        preview.addWidget(self._cam_panel, 1)

        # Right column: face mesh on top, hands below.
        right_col = QVBoxLayout()
        right_col.setSpacing(12)

        self._face_panel = QWidget()
        face_col = QVBoxLayout(self._face_panel)
        face_col.setContentsMargins(0, 0, 0, 0)
        face_col.setSpacing(4)
        face_lbl = QLabel("Gesicht & Augen")
        face_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        face_lbl.setStyleSheet(hdr)
        self._face_view = _FaceView()
        face_col.addWidget(face_lbl)
        face_col.addWidget(self._face_view, 1)
        right_col.addWidget(self._face_panel, 1)

        hands_row = QHBoxLayout()
        hands_row.setSpacing(12)
        self._skeletons: dict[str, HandVisualizationWidget] = {}
        self._hand_pos: dict[str, QLabel] = {}
        for hand, title in (("left", "Linke Hand (L)"), ("right", "Rechte Hand (R)")):
            col = QVBoxLayout()
            col.setSpacing(4)
            lbl = QLabel(title)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(hdr)
            sk = HandVisualizationWidget()
            sk.setMinimumSize(180, 180)
            pos = QLabel("")  # eye-referenced absolute position
            pos.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pos.setStyleSheet("font-size: 11px; color: #607D8B;")
            col.addWidget(lbl)
            col.addWidget(sk, 1)
            col.addWidget(pos)
            self._skeletons[hand] = sk
            self._hand_pos[hand] = pos
            hands_row.addLayout(col, 1)
        right_col.addLayout(hands_row, 1)

        preview.addLayout(right_col, 1)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #90A4AE; "
                          "letter-spacing: 1px;")
        return lbl

    # ── lifecycle ─────────────────────────────────────────────────
    def on_enter(self) -> None:
        """Called by main_window before showing the screen."""
        try:
            self._on_enter_impl()
        except Exception as e:
            log.exception("Fehler beim Öffnen des Tracking-Bildschirms")
            self._set_status(f"Fehler: {e}", error=True)

    def _on_enter_impl(self) -> None:
        from capture.source import source_kind
        self._applied = False
        active = self.main_window.capture_device
        # Remember how to restore the EXACT active source on Back (no re-probing).
        kind = source_kind(active)
        if kind == "webcam":
            self._restore = (self.SOURCE_WEBCAM, active.camera_index, active.flip_handedness)
            initial = self.SOURCE_WEBCAM
        elif kind == "leap":
            self._restore = (self.SOURCE_LEAP, 0, False)
            initial = self.SOURCE_LEAP
        else:  # mock → restore mock deterministically (don't re-probe Leap via "auto")
            self._restore = ("mock", 0, False)
            initial = self.SOURCE_WEBCAM  # default offer

        # Free the hardware so the candidate can use it.
        try:
            active.stop_recording()
            active.disconnect()
        except Exception:
            pass

        self._timer.start()
        # Set the radio without relying on toggled (block to drive explicitly).
        rb = self._rb_webcam if initial == self.SOURCE_WEBCAM else self._rb_leap
        if rb.isChecked():
            self._select_source(initial)
        else:
            rb.setChecked(True)  # triggers toggled -> _select_source

    def hideEvent(self, event) -> None:
        # Leaving the screen by any means: stop preview + GUI timer.
        self._timer.stop()
        self._teardown_candidate()
        super().hideEvent(event)

    # ── source / camera selection ─────────────────────────────────
    def _select_source(self, source: str) -> None:
        is_leap = source == self.SOURCE_LEAP
        self._cam_combo.setEnabled(source != self.SOURCE_SIM)  # Sim loops the default clip
        self._flip_cb.setEnabled(True)
        self._face_cb.setEnabled(True)
        # Recording a Sim clip only makes sense from the live webcam.
        can_record = (source == self.SOURCE_WEBCAM) and not self._recording_clip
        self._record_btn.setEnabled(can_record)
        self._dur_spin.setEnabled(can_record)
        # Hand projection: Leap is top-down (X-Z); webcam & sim are frontal (X-Y).
        projection = "topdown" if is_leap else "frontal"
        for sk in self._skeletons.values():
            sk.set_projection(projection)
        self._rebuild_candidate()

    def _on_camera_changed(self, _idx: int) -> None:
        if self._cam_combo.isEnabled():
            self._rebuild_candidate()

    # ── record (re)the Sim-default clip from the live webcam ──────
    def _live_webcam(self):
        """A live (non-replay) webcam to record from, or None."""
        if isinstance(self._candidate, WebcamSource) and not self._candidate.replay_path:
            return self._candidate
        if isinstance(self._face_candidate, WebcamSource):
            return self._face_candidate
        return None

    def _on_record_clicked(self) -> None:
        if self._recording_clip:
            return
        live = self._live_webcam()
        if live is None:
            # Sim/elsewhere: spin up a temporary live webcam just for recording.
            ok, issues = WebcamSource.sidecar_ready()
            if not ok:
                self._set_status(issues[0], error=True)
                return
            try:
                live = WebcamSource(camera_index=self._selected_camera_index())
                live.connect()
                live.start_recording(lambda _f: None)
                self._record_webcam = live
            except Exception as e:
                self._set_status(f"Keine Webcam zum Aufnehmen: {e}", error=True)
                return
        from video.clip import CLIPS_DIR
        CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        secs = float(self._dur_spin.value())
        self._record_seconds = secs
        self._pending_clip = str(CLIPS_DIR / "_pending.mp4")
        live.set_recorded_callback(lambda p: setattr(self, "_recorded_path", p))
        live.record_clip(self._pending_clip, seconds=secs)
        self._recording_clip = True
        self._record_btn.setEnabled(False)
        self._dur_spin.setEnabled(False)
        self._set_status(f"Aufnahme läuft ({secs:.0f} s) … Hand + Gesicht in die Kamera halten.")

    def _finish_recording(self, tmp_path: str) -> None:
        import os
        from PyQt6.QtWidgets import QMessageBox
        from video.clip import DEFAULT_CLIP
        from video.recorder import VideoRecorder
        self._recording_clip = False
        # Tear down the temporary record-only webcam if we started one.
        if self._record_webcam is not None:
            self._teardown_device(self._record_webcam, True)
            self._record_webcam = None
        can_record = self._current_source() == self.SOURCE_WEBCAM
        self._record_btn.setEnabled(can_record)
        self._dur_spin.setEnabled(can_record)
        try:
            ans = QMessageBox.question(
                self, "Aufnahme speichern?",
                "Aufnahme als Sim-Default speichern?\n"
                "Der bestehende Sim-Clip wird überschrieben.")
            if ans == QMessageBox.StandardButton.Yes:
                VideoRecorder.finalize(tmp_path, str(DEFAULT_CLIP),
                                       seconds=getattr(self, "_record_seconds", 0.0))
                self._set_status("Sim-Default aktualisiert.")
                if self._current_source() == self.SOURCE_SIM:
                    self._rebuild_candidate()  # loop the new default
            else:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                self._set_status("Aufnahme verworfen.")
        except Exception:
            log.exception("Fehler beim Speichern der Aufnahme")

    def _on_flip_changed(self, _state: int) -> None:
        for dev in (self._candidate, self._face_candidate):
            if isinstance(dev, WebcamSource):
                dev.flip_handedness = self._flip_cb.isChecked()

    def _on_face_toggled(self, _state: int) -> None:
        on = self._face_cb.isChecked()
        for dev in (self._candidate, self._face_candidate):
            if isinstance(dev, WebcamSource):
                dev.enable_face(on)
        if not on:
            self._face_view.clear()

    def _current_source(self) -> str:
        if self._rb_sim.isChecked():
            return self.SOURCE_SIM
        if self._rb_leap.isChecked():
            return self.SOURCE_LEAP
        return self.SOURCE_WEBCAM

    def _selected_camera_index(self) -> int:
        data = self._cam_combo.currentData()
        return int(data) if data is not None else 0

    # ── candidate device management ───────────────────────────────
    def _rebuild_candidate(self) -> None:
        if self._building:
            return  # re-entrancy guard: device construction blocks the GUI thread
        self._building = True
        try:
            self._rebuild_candidate_impl()
        finally:
            self._building = False

    def _rebuild_candidate_impl(self) -> None:
        self._teardown_candidate()
        self._latest_by_hand.clear()
        self._latest_preview = None
        self._webcam_preview.clear()
        self._face_view.clear()
        for sk in self._skeletons.values():
            sk.update_frame(None)
        for lbl in self._hand_pos.values():
            lbl.setText("")

        source = self._current_source()
        try:
            if source == self.SOURCE_WEBCAM:
                self._start_webcam_candidate()
            elif source == self.SOURCE_SIM:
                self._start_sim_candidate()
            else:
                self._start_leap_candidate()
        except Exception as e:
            log.warning("Vorschau konnte nicht gestartet werden: %s", e)
            self._set_status(f"Vorschau nicht möglich: {e}", error=True)

    def _start_webcam_candidate(self) -> None:
        ok, issues = WebcamSource.sidecar_ready()
        if not ok:
            self._set_status(issues[0], error=True)
            return

        # Build + connect so we can enumerate cameras, then (re)pick the index.
        dev = WebcamSource(flip_handedness=self._flip_cb.isChecked())
        dev.connect()
        self._candidate = dev
        self._owns_candidate = True

        self._populate_cameras(dev)
        dev.camera_index = self._selected_camera_index()
        dev.replay_path = ""  # live camera (clip replay is the Sim source)

        dev.set_preview_callback(self._on_preview)
        dev.start_recording(self._on_frame)
        dev.enable_preview(True)
        dev.enable_face(self._face_cb.isChecked())
        self._cam_panel.setVisible(True)
        self._face_panel.setVisible(True)
        self._set_status("Webcam-Vorschau aktiv.")

    def _start_sim_candidate(self) -> None:
        """Loop the default clip through MediaPipe (deterministic Sim source)."""
        ok, issues = WebcamSource.sidecar_ready()
        if not ok:
            self._set_status(issues[0], error=True)
            return
        from video.clip import default_clip_path
        clip = default_clip_path()
        if not clip:
            self._cam_panel.setVisible(False)
            self._face_panel.setVisible(False)
            self._set_status("Kein Sim-Clip – erst über „● 10 s“ aufnehmen.", error=True)
            return

        dev = WebcamSource(flip_handedness=self._flip_cb.isChecked(),
                                     replay_path=clip)
        dev.connect()
        self._candidate = dev
        self._owns_candidate = True
        dev.set_preview_callback(self._on_preview)
        dev.start_recording(self._on_frame)
        dev.enable_preview(True)
        dev.enable_face(self._face_cb.isChecked())
        self._cam_panel.setVisible(True)
        self._face_panel.setVisible(True)
        self._set_status("Sim-Loop aktiv (Clip wird wiederholt).")

    def _start_leap_candidate(self) -> None:
        # Webcam (camera + face) first — independent of Leap, so it shows even
        # while Leap is connecting.
        self._start_face_webcam()
        from capture.leap_capture import LeapSource
        dev = LeapSource()
        dev.connect()
        self._candidate = dev
        self._owns_candidate = True
        dev.start_recording(self._on_frame)
        self._set_status("Leap (Hände) + Webcam (Gesicht) aktiv.")

    def _start_face_webcam(self) -> None:
        """Start a webcam purely for the camera image + face mesh (its hand
        detection feeds the camera overlay + eye reference, but the hand skeletons
        come from the primary source)."""
        ok, _ = WebcamSource.sidecar_ready()
        if not ok:
            self._cam_panel.setVisible(False)
            self._face_panel.setVisible(False)
            return
        try:
            dev = WebcamSource(flip_handedness=self._flip_cb.isChecked())
            dev.connect()
        except Exception as e:
            log.warning("Webcam für Gesicht nicht verfügbar: %s", e)
            self._cam_panel.setVisible(False)
            self._face_panel.setVisible(False)
            return
        self._face_candidate = dev
        self._populate_cameras(dev)
        dev.camera_index = self._selected_camera_index()
        dev.set_preview_callback(self._on_preview)
        dev.start_recording(lambda _f: None)  # hands ignored; Leap provides them
        dev.enable_preview(True)
        dev.enable_face(self._face_cb.isChecked())
        self._cam_panel.setVisible(True)
        self._face_panel.setVisible(True)

    def _populate_cameras(self, dev: WebcamSource) -> None:
        cams = dev.list_cameras()
        self._cam_combo.blockSignals(True)
        self._cam_combo.clear()
        if cams:
            for idx, name in cams:
                self._cam_combo.addItem(f"{name}", idx)
        else:
            self._cam_combo.addItem("Keine Kamera gefunden", 0)
        self._cam_combo.blockSignals(False)

    @staticmethod
    def _teardown_device(dev, owns: bool) -> None:
        if dev is None:
            return
        try:
            dev.stop_recording()
            if isinstance(dev, WebcamSource):
                dev.set_preview_callback(None)
            if owns:
                dev.disconnect()
        except Exception:
            pass

    def _teardown_candidate(self) -> None:
        self._teardown_device(self._candidate, self._owns_candidate)
        self._teardown_device(self._face_candidate, True)  # always owned
        self._candidate = None
        self._face_candidate = None
        self._owns_candidate = False

    # ── frame intake (reader thread) → stash; GUI timer renders ────
    def _on_frame(self, frame: HandFrame) -> None:
        self._latest_by_hand[frame.hand_type] = (frame, time.perf_counter())

    def _on_preview(self, msg: dict) -> None:
        self._latest_preview = msg

    def _tick(self) -> None:
        try:
            # A recording finished (signalled from the reader thread) → ask to save.
            if self._recorded_path is not None:
                self._finish_recording(self._recorded_path)
                self._recorded_path = None

            now = time.perf_counter()
            source = self._current_source()
            eyeref = {}
            msg = self._latest_preview
            if msg is not None and self._cam_panel.isVisible():
                face = msg.get("face", []) if self._face_cb.isChecked() else []
                self._webcam_preview.set_frame(msg.get("jpeg", ""),
                                               msg.get("landmarks", []), face)
                self._face_view.set_face(face)
                eyeref = self._compute_eye_ref(msg)
            for hand, sk in self._skeletons.items():
                entry = self._latest_by_hand.get(hand)
                # Clear the panel when the hand has been gone for >0.3s, so the
                # model and the green confidence bar don't linger.
                fresh = entry is not None and (now - entry[1]) < HAND_STALE_S
                sk.update_frame(entry[0] if fresh else None)
                self._hand_pos[hand].setText(
                    self._hand_pos_text(hand, source, entry if fresh else None, eyeref))
        except Exception:
            log.exception("Fehler beim Aktualisieren der Vorschau")

    def _hand_pos_text(self, hand: str, source: str, entry, eyeref: dict) -> str:
        """Source + position per hand: Leap → absolute x-y-z, webcam → eye-relative."""
        if source == self.SOURCE_LEAP:
            if entry is None:
                return "Quelle: Leap Motion\nHand über den Sensor halten"
            x, y, z = entry[0].palm_position
            return f"Quelle: Leap Motion\nabs.  x={x:+.0f}  y={y:+.0f}  z={z:+.0f} mm"
        e = eyeref.get(hand)
        if e is None:
            return "Quelle: Cam"
        return f"Quelle: Cam\nrel. re. Auge  x={e[0]:+.0f}  y={e[1]:+.0f} mm"

    # ── eye-referenced absolute position (IPD-scaled) ─────────────
    # MediaPipe iris centres (478-pt face mesh) + average inter-pupillary dist.
    _IRIS_A, _IRIS_B = 468, 473
    _AVG_IPD_MM = 63.0

    def _eye_reference(self, face: list, w: int, h: int):
        """Return (right_eye_px, mm_per_px) from the iris centres, or None."""
        if not face or len(face) < 478 or w < 1 or h < 1:
            return None
        ax, ay = face[self._IRIS_A][0] * w, face[self._IRIS_A][1] * h
        bx, by = face[self._IRIS_B][0] * w, face[self._IRIS_B][1] * h
        ipd_px = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
        if ipd_px < 1:
            return None
        mm_per_px = self._AVG_IPD_MM / ipd_px
        # Subject's right eye = smaller x in the raw (non-mirrored) image.
        eye = (ax, ay) if ax < bx else (bx, by)
        return eye, mm_per_px

    def _compute_eye_ref(self, msg: dict) -> dict:
        """Per-hand eye-referenced position {hand: (dx_mm, dy_mm)} from webcam preview."""
        if not self._face_cb.isChecked():
            return {}
        w, h = int(msg.get("w", 1)), int(msg.get("h", 1))
        ref = self._eye_reference(msg.get("face", []), w, h)
        if ref is None:
            return {}
        (ex, ey), mm_per_px = ref
        out: dict[str, tuple] = {}
        hands_lms = msg.get("landmarks", [])
        handed = msg.get("hand_handedness", [])
        for i, hand_lm in enumerate(hands_lms):
            if not hand_lm:
                continue
            name = (handed[i] if i < len(handed) else "Right").lower()
            if self._flip_cb.isChecked():
                name = "left" if name == "right" else "right"
            if name in out:
                continue
            wx, wy = hand_lm[0][0] * w, hand_lm[0][1] * h   # wrist (landmark 0)
            out[name] = ((wx - ex) * mm_per_px, (wy - ey) * mm_per_px)
        return out

    # ── apply / back ──────────────────────────────────────────────
    def _on_apply(self) -> None:
        try:
            self._on_apply_impl()
        except Exception as e:
            log.exception("Fehler beim Übernehmen der Tracking-Quelle")
            self._set_status(f"Fehler: {e}", error=True)

    def _on_apply_impl(self) -> None:
        source = self._current_source()
        is_cam = source in (self.SOURCE_WEBCAM, self.SOURCE_SIM)
        idx = self._selected_camera_index() if source == self.SOURCE_WEBCAM else 0
        flip = self._flip_cb.isChecked() if is_cam else False
        # Sim adopts a webcam device (replaying a clip) → persist/gate it as webcam.
        persist_mode = self.SOURCE_WEBCAM if is_cam else self.SOURCE_LEAP

        # The parallel webcam-face device (Leap mode) is preview-only — drop it.
        self._teardown_device(self._face_candidate, True)
        self._face_candidate = None

        candidate = self._candidate
        # Hand the live, connected candidate straight to the app.
        adopt = candidate if (candidate is not None and self._owns_candidate) else None
        if adopt is not None:
            adopt.stop_recording()
            if isinstance(adopt, WebcamSource):
                adopt.set_preview_callback(None)
                # Persisted camera/flip must match the device actually adopted.
                adopt.camera_index = idx
                adopt.flip_handedness = flip
            self._owns_candidate = False   # ownership transfers to main_window
            self._candidate = None

        ok = self.main_window.switch_capture_device(
            persist_mode, camera_index=idx, flip_handedness=flip, device=adopt)
        if ok:
            self._applied = True
            self.main_window.close_tracking_screen()

    def _on_back(self) -> None:
        try:
            self._on_back_impl()
        except Exception as e:
            log.exception("Fehler beim Verlassen des Tracking-Bildschirms")
            self.main_window.close_tracking_screen()

    def _on_back_impl(self) -> None:
        self._teardown_candidate()
        # Reconnect the device that was active before we opened the screen.
        if not self._applied and self._restore is not None:
            mode, idx, flip = self._restore
            try:
                self.main_window.switch_capture_device(mode, camera_index=idx,
                                                        flip_handedness=flip)
            except Exception as e:
                log.warning("Konnte vorheriges Gerät nicht wiederherstellen: %s", e)
        self.main_window.close_tracking_screen()

    # ── helpers ───────────────────────────────────────────────────
    def _set_status(self, text: str, error: bool = False) -> None:
        color = "#E53935" if error else "#757575"
        self._status.setStyleSheet(f"font-size: 12px; color: {color};")
        self._status.setText(text)
