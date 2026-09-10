"""VideoLab: upload a video, select an onset/offset range, label it, and run a
motor paradigm on that range through MediaPipe (overlay + realtime + result)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PyQt6.QtCore import pyqtSignal, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QFrame,
    QScrollArea,
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton, QSizePolicy,
    QStyle, QVBoxLayout, QWidget,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from ui.widgets.video_view import VideoView

from paradigms import registry
from paradigms.config import get_unmet_capabilities
from ui.segment_pipeline import SegmentPipeline
from ui.widgets.meta_panel import MetaPanel
from ui.widgets.live_metric_plot import LiveMetricPlot
from ui.widgets.webcam_preview import WebcamPreview
from ui.video_timeline import VideoTimeline
from video.config import cfg, video_filter
from video.store import VideoSession
from ui import theme

log = logging.getLogger(__name__)


class _ImportWorker(QThread):
    """Normalize (transcode) or copy an imported video off the GUI thread."""
    done = pyqtSignal(str, str)   # (stored_path, original_name)
    failed = pyqtSignal(str)

    def __init__(self, session: VideoSession, src: str) -> None:
        super().__init__()
        self._session = session
        self._src = src

    def run(self) -> None:
        try:
            from video.importer import VideoImporter
            name = Path(self._src).name
            clip = VideoImporter().import_file(self._src, self._session.new_video_path)
            if clip is None:
                self.failed.emit("Video-Normalisierung fehlgeschlagen.")
                return
            self.done.emit(clip.path, name)
        except Exception as e:   # noqa: BLE001
            self.failed.emit(str(e))


class _RotateWorker(QThread):
    """Re-transcode the session video with a manual rotation off the GUI thread."""
    done = pyqtSignal(str)        # new stored path
    failed = pyqtSignal(str)

    def __init__(self, session: VideoSession, degrees: int) -> None:
        super().__init__()
        self._session = session
        self._deg = degrees

    def run(self) -> None:
        try:
            from video.transcode import transcode_video
            dest = str(self._session.new_video_path(".mp4"))
            out = transcode_video(self._session.video_path, dest, rotate_deg=self._deg)
            if not out:
                self.failed.emit("Drehen fehlgeschlagen (Transcode).")
                return
            self.done.emit(dest)
        except Exception as e:   # noqa: BLE001
            self.failed.emit(str(e))


class _SegmentExtractWorker(QThread):
    """Cut a segment into its own compact, defaced clip off the GUI thread."""
    done = pyqtSignal(str, str, bool)   # (seg_id, clip_path, deidentified)
    failed = pyqtSignal(str, str)       # (seg_id, message)

    def __init__(self, src: str, start_s: float, end_s: float, dest: str,
                 seg_id: str, deface: str) -> None:
        super().__init__()
        self._src, self._start, self._end, self._dest, self._seg_id = \
            src, start_s, end_s, dest, seg_id
        self._deface = deface

    def run(self) -> None:
        try:
            from video.extractor import VideoSegmentExtractor
            clip = VideoSegmentExtractor().extract(self._src, self._start, self._end,
                                                   self._dest, deface=self._deface)
            if clip is None:
                self.failed.emit(self._seg_id, "Extraktion fehlgeschlagen")
                return
            self.done.emit(self._seg_id, clip.path, clip.deidentified)
        except Exception as e:   # noqa: BLE001
            self.failed.emit(self._seg_id, str(e))


class _SegmentDialog(QDialog):
    """Pick the analysis paradigm + hand when creating a segment."""

    def __init__(self, parent, options: list) -> None:
        # options: [(key, label, enabled, bilateral), ...]
        super().__init__(parent)
        self.setWindowTitle("Segment – Auswertung wählen")
        form = QFormLayout(self)
        self._para = QComboBox()
        for key, label, enabled, _bil in options:
            self._para.addItem(label, key)
            if not enabled:
                self._para.model().item(self._para.count() - 1).setEnabled(False)
        for i in range(self._para.count()):
            if self._para.model().item(i).isEnabled():
                self._para.setCurrentIndex(i)
                break
        self._para.currentIndexChanged.connect(self._sync_hand)
        self._hand = QComboBox()
        self._hand.addItem("Rechte Hand", "right")
        self._hand.addItem("Linke Hand", "left")
        self._hand.addItem("Beide Hände", "both")
        form.addRow("Auswertung:", self._para)
        form.addRow("Hand:", self._hand)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self._bilateral = {k: bil for k, _l, _e, bil in options}
        self._sync_hand()

    def _sync_hand(self) -> None:
        # "Beide Hände" only for bilateral paradigms (e.g. tremor).
        bil = self._bilateral.get(self._para.currentData(), False)
        idx = self._hand.findData("both")
        self._hand.model().item(idx).setEnabled(bil)
        if not bil and self._hand.currentData() == "both":
            self._hand.setCurrentIndex(0)

    def values(self):
        return self._para.currentData(), self._hand.currentData()


class _RenameDialog(QDialog):
    """Edit a segment's name, note, and analysed hand."""

    def __init__(self, parent, name: str, note: str, hand: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Segment bearbeiten")
        form = QFormLayout(self)
        self._name = QLineEdit(name)
        self._hand = QComboBox()
        for label, val in (("Rechte Hand", "right"), ("Linke Hand", "left"),
                           ("Beide Hände", "both")):
            self._hand.addItem(label, val)
        idx = self._hand.findData(hand)
        self._hand.setCurrentIndex(idx if idx >= 0 else 0)
        self._note = QPlainTextEdit(note)
        self._note.setMinimumHeight(70)
        form.addRow("Name:", self._name)
        form.addRow("Hand:", self._hand)
        form.addRow("Notiz:", self._note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def values(self):
        return (self._name.text().strip(), self._note.toPlainText().strip(),
                self._hand.currentData())


class VideoLabScreen(QWidget):
    """Import a video, cut segments, analyse and export them.

    Lives inside the session screen as its "cut" pane (``embedded=True``): the
    header row is hidden there and the session is handed in by the host rather
    than looked up per patient.
    """

    contentChanged = pyqtSignal()   # segments/video changed → host refreshes
    detailsRequested = pyqtSignal(int)   # measurement id: open its details

    def __init__(self, main_window, embedded: bool = False,
                 pipeline: SegmentPipeline | None = None) -> None:
        super().__init__()
        self.main_window = main_window
        self._embedded = embedded
        self.session: VideoSession | None = None
        self.current_range = (0.0, 0.0)
        self.current_segment = None
        self._running = False

        # The same analyse → archive → cleanup pipeline the recording pane
        # runs its takes through; a cut of an imported video is just another
        # segment to it (source: the imported file, the segment's range).
        self._pipeline = pipeline or SegmentPipeline(self)
        self._pipeline.previewReady.connect(self._on_preview)
        self._pipeline.analysisStarted.connect(self._on_started)
        self._pipeline.analysisFinished.connect(self._on_finished)
        self._pipeline.analysisFailed.connect(self._on_failed)
        self._pipeline.stageText.connect(self._on_stage_text)
        self._pipeline.jobDone.connect(self._on_job_done)
        self._pipeline.contentChanged.connect(self.contentChanged.emit)

        self._plot_timer = QTimer(self)
        self._plot_timer.setInterval(120)
        self._plot_timer.timeout.connect(self._refresh_plot)

        self._build_ui()

    # ── construction ──────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # Header
        header = QHBoxLayout()
        back = QPushButton("← Zurück")
        back.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        back.clicked.connect(lambda: self.main_window.close_video_lab())
        header.addWidget(back)
        self._title = QLabel("VideoLab")
        self._title.setStyleSheet("font-size: 20px; font-weight: 700; color: #263238;")
        header.addWidget(self._title, 1)
        self._load_btn = QPushButton("🎬 Video laden…")
        self._load_btn.clicked.connect(self._on_load_video)
        header.addWidget(self._load_btn)
        self._save_btn = QPushButton("💾 Video-Session speichern")
        self._save_btn.clicked.connect(self._on_save_session)
        header.addWidget(self._save_btn)
        root.addLayout(header)
        if self._embedded:
            # The host screen carries title, back button and the "add" menu.
            for w in (back, self._title, self._load_btn, self._save_btn):
                w.setVisible(False)

        # Video row: original info (left) | video (center) | normalized info (right)
        vrow = QHBoxLayout()
        vrow.setSpacing(12)
        self._orig_info = self._info_label()
        vrow.addLayout(self._info_col("Original", self._orig_info))

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        # Shows the clip under its own mirror flag (see the "Gespiegelt" box),
        # so the player agrees with the analysis about left and right.
        self._video_widget = VideoView()
        self._video_widget.setMinimumHeight(240)
        self._player.setVideoSink(self._video_widget.sink)
        self._player.durationChanged.connect(self._timeline_duration)
        self._player.positionChanged.connect(lambda ms: self._timeline.setPosition(ms))
        vrow.addWidget(self._video_widget, 1)

        self._norm_info = self._info_label()
        vrow.addLayout(self._info_col("Nach Import", self._norm_info))
        root.addLayout(vrow, 2)

        ctl = QHBoxLayout()
        self._play_btn = QPushButton()
        self._play_btn.setFixedWidth(44)
        self._play_btn.clicked.connect(self._toggle_play)
        self._player.playbackStateChanged.connect(self._update_play_icon)
        self._update_play_icon()
        ctl.addWidget(self._play_btn)
        self._timeline = VideoTimeline()
        self._timeline.seekRequested.connect(self._player.setPosition)
        self._timeline.rangeChanged.connect(self._on_range_changed)
        ctl.addWidget(self._timeline, 1)
        self._range_lbl = QLabel("–")
        self._range_lbl.setStyleSheet("font-size:12px; color:#607D8B;")
        self._range_lbl.setFixedWidth(120)
        ctl.addWidget(self._range_lbl)
        self._rotate_btn = QPushButton("↻ 90°")
        self._rotate_btn.setFixedWidth(70)
        self._rotate_btn.setToolTip(
            "Video um 90° im Uhrzeigersinn drehen (falls die automatische "
            "Orientierung falsch ist)")
        self._rotate_btn.clicked.connect(self._on_rotate)
        ctl.addWidget(self._rotate_btn)
        self._mirror_cb = QCheckBox("Gespiegelt")
        self._mirror_cb.setToolTip(
            "Dieses Video horizontal spiegeln.\n"
            "Nötig, wenn es mit einer Frontkamera aufgenommen wurde und der "
            "Proband seitenverkehrt erscheint — sonst wird die linke Hand als "
            "rechte erkannt.\n"
            "Gilt für dieses Video und wird mit der Video-Session gespeichert.")
        self._mirror_cb.stateChanged.connect(self._on_mirror_changed)
        ctl.addWidget(self._mirror_cb)
        self._add_seg_btn = QPushButton("Bereich übernehmen →")
        self._add_seg_btn.setProperty("cssClass", "primary")
        self._add_seg_btn.setToolTip("Markierten Bereich als benanntes Segment speichern")
        self._add_seg_btn.clicked.connect(self._on_add_segment)
        ctl.addWidget(self._add_seg_btn)
        root.addLayout(ctl)

        # Options for the next segment — same wording as under a take.
        opts = QHBoxLayout()
        self._deface_cb = QCheckBox("Gesicht unkenntlich machen")
        self._deface_cb.setToolTip("Gesicht im Segment-Clip anonymisieren (Datenschutz). "
                                   "Die Analyse läuft immer auf dem Original.")
        self._deface_cb.setChecked(cfg("privacy", "deface", default="blur") in ("blur", "mesh"))
        opts.addWidget(self._deface_cb)
        self._auto_cb = QCheckBox("Nach Anlegen automatisch auswerten")
        self._auto_cb.setChecked(True)
        self._auto_cb.setToolTip("Ein neues Segment wird nach dem Zuschnitt sofort ausgewertet "
                                 "und landet als Messung in der Akte — wie ein bestätigter Take.")
        opts.addWidget(self._auto_cb)
        opts.addStretch()
        root.addLayout(opts)

        # Bottom: segments (left) | work area (right)
        bottom = QHBoxLayout()
        bottom.setSpacing(12)

        left = QVBoxLayout()
        left.addWidget(self._section("Segmente"))
        self._seg_list = QListWidget()
        self._seg_list.setMinimumWidth(220)
        self._seg_list.currentItemChanged.connect(self._on_segment_selected)
        self._seg_list.itemDoubleClicked.connect(lambda _i: self._on_rename_segment())
        left.addWidget(self._seg_list, 1)
        seg_btns = QHBoxLayout()
        self._rename_seg_btn = QPushButton("✎ Umbenennen")
        self._rename_seg_btn.setToolTip("Segment umbenennen (oder Doppelklick)")
        self._rename_seg_btn.clicked.connect(self._on_rename_segment)
        seg_btns.addWidget(self._rename_seg_btn)
        self._del_seg_btn = QPushButton("🗑 Löschen")
        self._del_seg_btn.clicked.connect(self._on_delete_segment)
        seg_btns.addWidget(self._del_seg_btn)
        left.addLayout(seg_btns)
        bottom.addLayout(left, 1)

        page = QWidget()
        page.setObjectName("scrollPage")
        page.setStyleSheet("#scrollPage { background: transparent; }")
        right = QVBoxLayout(page)
        right.setContentsMargins(0, 0, 8, 0)
        right.addWidget(self._section("Auswertung"))
        self._seg_info = QLabel("Kein Segment gewählt.")
        self._seg_info.setWordWrap(True)
        self._seg_info.setStyleSheet("font-size:13px; color:#37474F;")
        right.addWidget(self._seg_info)

        # Paradigm + hand come from the segment (chosen at creation), so the work
        # area just runs the analysis.
        pick = QHBoxLayout()
        self._run_btn = QPushButton("▶ Analyse starten")
        self._run_btn.setProperty("cssClass", "accent")
        self._run_btn.clicked.connect(self._on_run)
        pick.addWidget(self._run_btn)
        # An analysed segment is a measurement in the record (the pipeline
        # exports it); this opens the same detail view as for any measurement.
        self._details_btn = QPushButton("Details…")
        self._details_btn.setToolTip("Kennwerte und Kurven dieser Auswertung")
        self._details_btn.clicked.connect(self._on_details)
        self._details_btn.setEnabled(False)
        pick.addWidget(self._details_btn)
        pick.addStretch()
        right.addLayout(pick)

        viz = QHBoxLayout()
        self._overlay = WebcamPreview()
        self._overlay.setMinimumSize(260, 200)
        self._overlay.setMaximumHeight(260)
        self._overlay.set_placeholder("Vorschau erscheint beim Start der Analyse")
        viz.addWidget(self._overlay, 1)
        self._plot = LiveMetricPlot(figsize=(3, 2))
        self._plot.setMinimumWidth(260)
        self._plot.setMinimumHeight(200)
        self._plot.setMaximumHeight(260)
        viz.addWidget(self._plot, 1)
        right.addLayout(viz)

        self._result_lbl = QLabel("")
        self._result_lbl.setWordWrap(True)
        self._result_lbl.setStyleSheet("font-size:12px; color:#263238;")
        self._result_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        right.addWidget(self._result_lbl)

        self._status = QLabel("")
        self._status.setStyleSheet(f"font-size:12px; color:{theme.TEXT_SECONDARY};")
        right.addWidget(self._status)
        # Same provenance panel as under a take: source, cut, archive, analysis.
        self._meta = MetaPanel("Aufnahme-Info")
        self._meta.setVisible(False)
        right.addWidget(self._meta)
        right.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll.setWidget(page)
        bottom.addWidget(scroll, 2)

        root.addLayout(bottom, 3)
        self._set_controls_enabled(False)

    @staticmethod
    def _section(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size:12px; font-weight:700; color:#90A4AE; letter-spacing:1px;")
        return lbl

    @staticmethod
    def _info_label() -> QLabel:
        lbl = QLabel("—")
        lbl.setStyleSheet("font-size:12px; color:#546E7A;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
        lbl.setFixedWidth(150)
        lbl.setWordWrap(True)
        return lbl

    def _info_col(self, title: str, label: QLabel) -> QVBoxLayout:
        col = QVBoxLayout()
        col.addWidget(self._section(title))
        col.addWidget(label, 1)
        return col

    def _populate_info(self, clip) -> None:
        import os
        if clip is None:
            self._orig_info.setText("—")
            self._norm_info.setText("—")
            return
        ex = clip.extra or {}
        title = (self.session.video_name if self.session else "") or "—"
        self._orig_info.setText(
            f"<b>{title}</b><br><br>"
            f"Auflösung:<br>&nbsp;&nbsp;{ex.get('src_w', '?')}×{ex.get('src_h', '?')}<br>"
            f"FPS: {ex.get('src_fps', '?')}<br>"
            f"Dauer: {ex.get('src_duration_s', '?')} s")
        size_kb = os.path.getsize(clip.path) // 1024 if os.path.exists(clip.path) else 0
        anon = "<br>Gesicht: anonymisiert" if clip.deidentified else ""
        self._norm_info.setText(
            f"Auflösung:<br>&nbsp;&nbsp;{clip.width}×{clip.height}<br>"
            f"FPS: {clip.fps:.0f}<br>"
            f"Dauer: {clip.duration_s:.1f} s<br>"
            f"Größe: {size_kb} KB{anon}")

    # ── enter / leave ─────────────────────────────────────────────
    def on_enter(self, patient) -> None:
        self.current_segment = None
        self._result_lbl.setText("")
        self._overlay.clear()
        if patient is None:
            self._title.setText("VideoLab")
            self.session = None
            return
        code = getattr(patient, "patient_code", "") or str(getattr(patient, "id", ""))
        self._title.setText(f"VideoLab – {code}")
        self.session = (load_for_patient(getattr(patient, "id", 0), code)
                        or VideoSession.create(getattr(patient, "id", 0), code))
        # Reflect the stored per-video mirror flag without re-saving it.
        self._mirror_cb.blockSignals(True)
        self._mirror_cb.setChecked(bool(self.session.mirrored))
        self._mirror_cb.blockSignals(False)
        if self.session.video_path:
            from video.clip import VideoClip
            self._player.setSource(QUrl.fromLocalFile(self.session.video_path))
            self._show_first_frame()
            self._populate_info(VideoClip.load(self.session.video_path))
            self._set_controls_enabled(True)
        else:
            self._populate_info(None)
            self._set_controls_enabled(False)
        self._refresh_segment_list()

    def load_session(self, patient, video: VideoSession) -> None:
        """Embedded entry: show *this* video session (no per-patient lookup)."""
        self.current_segment = None
        self._result_lbl.setText("")
        self._overlay.clear()
        self.session = video
        code = getattr(patient, "patient_code", "") if patient else ""
        self._title.setText(f"VideoLab – {code}")
        self._mirror_cb.blockSignals(True)
        self._mirror_cb.setChecked(bool(video.mirrored))
        self._mirror_cb.blockSignals(False)
        self._video_widget.set_mirrored(bool(video.mirrored))
        if video.video_path:
            from video.clip import VideoClip
            self._player.setSource(QUrl.fromLocalFile(video.video_path))
            self._show_first_frame()
            try:
                self._populate_info(VideoClip.load(video.video_path))
            except Exception:
                self._populate_info(None)
            self._set_controls_enabled(True)
        else:
            try:
                self._player.setSource(QUrl())
            except Exception:
                pass
            self._populate_info(None)
            self._set_controls_enabled(False)
        self._refresh_segment_list()

    def import_video(self) -> None:
        """Host-triggered import (the "Video importieren…" menu entry)."""
        self._on_load_video()

    def on_leave(self) -> None:
        self._plot_timer.stop()
        try:
            self._player.stop()
        except Exception:
            pass
        if self._pipeline.parent() is self:
            self._pipeline.teardown()

    def select_segment(self, seg_id: str) -> None:
        """Host entry: show this segment (the tree lists them)."""
        for i in range(self._seg_list.count()):
            it = self._seg_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == seg_id:
                self._seg_list.setCurrentItem(it)
                self._on_segment_selected(it)
                return

    # ── video loading ─────────────────────────────────────────────
    def _on_load_video(self) -> None:
        if self.session is None:
            QMessageBox.warning(self, "VideoLab", "Kein Proband ausgewählt.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Video auswählen", "", video_filter())
        if not path:
            return
        self._load_btn.setEnabled(False)
        self._status.setText("Video wird importiert/optimiert …")
        self._show_busy("Video wird importiert und für die Analyse optimiert …\n"
                        "(Auflösung/Framerate werden angepasst)")
        self._import_worker = _ImportWorker(self.session, path)
        self._import_worker.done.connect(self._on_import_done)
        self._import_worker.failed.connect(self._on_import_failed)
        self._import_worker.start()

    def _show_busy(self, text: str) -> None:
        self._busy = QProgressDialog(text, None, 0, 0, self)  # 0,0 = indeterminate
        self._busy.setWindowTitle("VideoLab")
        self._busy.setCancelButton(None)
        self._busy.setMinimumDuration(0)
        self._busy.setWindowModality(Qt.WindowModality.WindowModal)
        self._busy.show()

    def _hide_busy(self) -> None:
        busy = getattr(self, "_busy", None)
        if busy is not None:
            busy.close()
            self._busy = None

    def _on_mirror_changed(self, _state: int) -> None:
        """Persist the per-video mirror flag on the session."""
        if self.session is None:
            return
        mirrored = self._mirror_cb.isChecked()
        self._video_widget.set_mirrored(mirrored)
        if mirrored == self.session.mirrored:
            return
        self.session.mirrored = mirrored
        self.session.save()
        self._status.setText(
            "Video wird gespiegelt analysiert." if mirrored
            else "Video wird unverändert analysiert.")

    def _on_import_done(self, dest: str, name: str) -> None:
        self._hide_busy()
        self._load_btn.setEnabled(True)
        if self.session is None:
            return
        from video.clip import VideoClip
        from capture.config import source_mirrored
        self.session.set_video(dest, name)
        # Starting point for a freshly imported clip (capture.yaml sources.video);
        # the clinician corrects it per video with the "Gespiegelt" checkbox.
        self.session.mirrored = source_mirrored("video")
        self._mirror_cb.blockSignals(True)
        self._mirror_cb.setChecked(self.session.mirrored)
        self._mirror_cb.blockSignals(False)
        self.session.save()
        self._player.setSource(QUrl.fromLocalFile(dest))
        self._show_first_frame()
        self._populate_info(VideoClip.load(dest))
        self._set_controls_enabled(True)
        self._status.setText(f"Video geladen: {name}")
        self.contentChanged.emit()

    def _on_import_failed(self, msg: str) -> None:
        self._hide_busy()
        self._load_btn.setEnabled(True)
        QMessageBox.critical(self, "VideoLab", f"Konnte Video nicht laden:\n{msg}")
        self._status.setText("")

    # ── rotate (Videoschnitt) ─────────────────────────────────────
    def _on_rotate(self) -> None:
        if self.session is None or not self.session.video_path or self._running:
            return
        import os
        if not os.path.exists(self.session.video_path):
            return
        if self.session.segments:
            r = QMessageBox.question(
                self, "Video drehen",
                "Bereits extrahierte Segment-Clips behalten die alte Orientierung "
                "und Analysen sollten neu ausgeführt werden. Trotzdem drehen?")
            if r != QMessageBox.StandardButton.Yes:
                return
        self._player.stop()
        self._show_busy("Video wird um 90° gedreht …")
        self._rotate_worker = _RotateWorker(self.session, 90)
        self._rotate_worker.done.connect(self._on_rotate_done)
        self._rotate_worker.failed.connect(self._on_rotate_failed)
        self._rotate_worker.start()

    def _on_rotate_done(self, dest: str) -> None:
        self._hide_busy()
        if self.session is None:
            return
        import os
        old = self.session.video_path
        from video.clip import VideoClip
        self.session.set_video(dest, self.session.video_name)
        self.session.save()
        try:
            if old and os.path.exists(old) and old != dest:
                os.remove(old)
        except OSError:
            log.warning("Altes Video konnte nicht gelöscht werden: %s", old)
        self._player.setSource(QUrl.fromLocalFile(dest))
        self._show_first_frame()
        self._populate_info(VideoClip.load(dest))
        self._status.setText("Video um 90° gedreht.")

    def _on_rotate_failed(self, msg: str) -> None:
        self._hide_busy()
        QMessageBox.critical(self, "VideoLab", f"Drehen fehlgeschlagen:\n{msg}")
        self._status.setText("")

    def _timeline_duration(self, ms: int) -> None:
        self._timeline.setDuration(ms)

    def _show_first_frame(self) -> None:
        # QMediaPlayer shows black until it plays — nudge it to render frame 0.
        self._player.play()
        QTimer.singleShot(120, self._player.pause)

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _update_play_icon(self, *_a) -> None:
        playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        pix = (QStyle.StandardPixmap.SP_MediaPause if playing
               else QStyle.StandardPixmap.SP_MediaPlay)
        self._play_btn.setIcon(self.style().standardIcon(pix))

    def _on_range_changed(self, start_s: float, end_s: float) -> None:
        self.current_range = (start_s, end_s)
        self._range_lbl.setText(f"{start_s:.1f}–{end_s:.1f}s  ({end_s-start_s:.1f}s)")

    # ── segments ──────────────────────────────────────────────────
    def _on_add_segment(self) -> None:
        if self.session is None or not self.session.video_path:
            return
        s, e = self.current_range
        min_len = float(cfg("segments", "min_length_s", default=0.3))
        if e - s < min_len:
            QMessageBox.information(self, "VideoLab",
                                    f"Bereich ist zu kurz (min. {min_len:.1f} s).")
            return
        options = self._paradigm_options()
        if not any(o[2] for o in options):
            QMessageBox.information(self, "VideoLab", "Keine Auswertung verfügbar.")
            return
        dlg = _SegmentDialog(self, options)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        para, hand = dlg.values()
        if not para:
            return
        name = registry.get(para).label.replace("\n", " ")
        seg = self.session.add_segment(name, s, e, paradigm=para, hand=hand)
        from video.meta import import_meta
        seg.meta = import_meta(self.session, seg)
        self.session.save()
        self._refresh_segment_list()
        self._extract_segment(seg)

    def _paradigm_options(self) -> list:
        """[(key, label, enabled, bilateral)] of motor paradigms, capability-gated.

        The analysis run enables face tracking itself, so the eye reference
        provides absolute position on video — abs_position is not a blocker
        here (tremor runs, marked as eye-referenced)."""
        from capture.source import CAP_ABS_POSITION
        opts = []
        for key in registry.all_keys():
            spec = registry.get(key)
            if spec.category != registry.Category.MOTOR:
                continue
            unmet = get_unmet_capabilities(key, "webcam")
            eye_ref = CAP_ABS_POSITION in unmet
            unmet -= {CAP_ABS_POSITION}
            label = spec.label.replace("\n", " ")
            if unmet:
                label += " 🔒"
            elif eye_ref:
                label += " (Augen-Referenz)"
            opts.append((key, label, not unmet, spec.bilateral))
        return opts

    def _extract_segment(self, seg) -> None:
        from video.extractor import extract_available
        if not cfg("segments", "extract", default=True) or not extract_available():
            return
        dest = str(self.session.segment_clip_path(seg.id))
        # Checkbox is the master switch; mode comes from config (blur/mesh).
        mode = cfg("privacy", "deface", default="blur")
        if not self._deface_cb.isChecked():
            mode = "off"
        elif mode == "off":
            mode = "blur"
        suffix = " und anonymisiert" if mode in ("blur", "mesh") else ""
        self._show_busy(f'Segment „{seg.name}" wird extrahiert{suffix} …')
        self._seg_worker = _SegmentExtractWorker(
            self.session.video_path, seg.start_s, seg.end_s, dest, seg.id, mode)
        self._seg_worker.done.connect(self._on_segment_extracted)
        self._seg_worker.failed.connect(self._on_segment_extract_failed)
        self._seg_worker.start()

    def _on_segment_extracted(self, seg_id: str, clip_path: str, deid: bool) -> None:
        self._hide_busy()
        if self.session is None:
            return
        seg = next((s for s in self.session.segments if s.id == seg_id), None)
        if seg is not None:
            from video.clip import VideoClip
            seg.clip_path = clip_path
            seg.deidentified = deid
            clip = VideoClip.load(clip_path)
            seg.thumb_path = (clip.extra or {}).get("thumb", "") if clip else ""
            if clip is not None:
                from video.meta import note_archive
                note_archive(seg, clip, None)
            self.session.save()
            if self._auto_cb.isChecked() and seg.paradigm:
                self._pipeline.enqueue(self.session, seg, analyse=True)
            self._refresh_segment_list()
            if self.current_segment and self.current_segment.id == seg_id:
                self._load_thumb(seg)
        self._status.setText("Segment-Clip gespeichert" + (" (anonymisiert)" if deid else ""))

    def _on_segment_extract_failed(self, seg_id: str, msg: str) -> None:
        self._hide_busy()
        self._status.setText(f"Segment-Extraktion fehlgeschlagen: {msg} "
                             "(Analyse nutzt das Originalvideo)")

    def _on_delete_segment(self) -> None:
        if self.session is None or self.current_segment is None:
            return
        if QMessageBox.question(
                self, "Segment löschen",
                f'Segment „{self.current_segment.name}" wirklich löschen?'
                ) != QMessageBox.StandardButton.Yes:
            return
        seg = self.current_segment
        mids = [r.get("measurement_id") for r in seg.results.values()
                if isinstance(r, dict) and r.get("measurement_id")]
        if mids:
            from storage.database import delete_measurement, get_db
            conn = get_db()
            try:
                for mid in mids:
                    delete_measurement(conn, int(mid))
            finally:
                conn.close()
        self.session.remove_segment(seg.id)
        self.session.save()
        self.current_segment = None
        self._refresh_segment_list()
        self._seg_info.setText("Kein Segment gewählt.")
        self._overlay.clear()
        self._meta.setVisible(False)

    def _on_rename_segment(self) -> None:
        if self.session is None or self.current_segment is None:
            return
        seg = self.current_segment
        dlg = _RenameDialog(self, seg.name, seg.note, seg.hand)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, note, hand = dlg.values()
        if name:
            seg.name = name
        seg.note = note
        seg.hand = hand
        self.session.save()
        self._refresh_segment_list()
        self._reselect_current_segment()
        self._render_seg_details(seg)

    _HAND_SHORT = {"right": "R", "left": "L", "both": "R+L"}
    _HAND_LONG = {"right": "rechts", "left": "links", "both": "beide"}

    def _refresh_segment_list(self) -> None:
        self._seg_list.blockSignals(True)
        self._seg_list.clear()
        if self.session:
            for seg in self.session.segments:
                exported = any(r.get("measurement_id") for r in seg.results.values()
                               if isinstance(r, dict))
                mark = " 📋" if exported else (" ✓" if seg.analyzed else "")
                priv = " 🔒" if seg.deidentified else ""
                t = seg.created_at[11:16] if len(seg.created_at) >= 16 else ""
                hand = self._HAND_SHORT.get(seg.hand, "")
                item = QListWidgetItem(
                    f"{seg.name} ({hand})  {seg.start_s:.1f}–{seg.end_s:.1f}s  {t}{priv}{mark}")
                item.setData(Qt.ItemDataRole.UserRole, seg.id)
                self._seg_list.addItem(item)
        self._seg_list.blockSignals(False)
        self.contentChanged.emit()

    def _on_segment_selected(self, item, _prev=None) -> None:
        if item is None or self.session is None:
            self.current_segment = None
            self._update_run_enabled()
            return
        seg_id = item.data(Qt.ItemDataRole.UserRole)
        self.current_segment = next((s for s in self.session.segments if s.id == seg_id), None)
        if self.current_segment is None:
            return
        # Paradigm + hand come from the segment; show its first frame + any results.
        self._render_seg_details(self.current_segment)
        self._load_thumb(self.current_segment)
        self._show_existing_result()
        self._update_run_enabled()

    def _render_seg_details(self, seg) -> None:
        para = registry.get(seg.paradigm).label.replace("\n", " ") if seg.paradigm else "—"
        hand = self._HAND_LONG.get(seg.hand, seg.hand)
        when = seg.created_at[:16].replace("T", "  ") if seg.created_at else "—"
        note = f"<br><i>Notiz:</i> {seg.note}" if seg.note else ""
        self._seg_info.setText(
            f"<b>{seg.name}</b><br>"
            f"Auswertung: {para} · Hand: {hand}<br>"
            f"Bereich: {seg.start_s:.1f}–{seg.end_s:.1f}s ({seg.duration_s:.1f}s)<br>"
            f"Erstellt: {when}{note}")

    def _load_thumb(self, seg) -> None:
        import base64
        import os
        if seg.thumb_path and os.path.exists(seg.thumb_path):
            with open(seg.thumb_path, "rb") as f:
                self._overlay.set_frame(base64.b64encode(f.read()).decode(), [])
        else:
            self._overlay.clear()
            self._overlay.set_placeholder("Erster Frame folgt nach Extraktion …"
                                          if not seg.clip_path else "Vorschau")

    def _show_existing_result(self) -> None:
        seg = self.current_segment
        from video.meta import describe_segment, result_summary, segment_issues
        if seg is None or self.session is None:
            self._result_lbl.setText("")
            self._meta.setVisible(False)
            return
        self._result_lbl.setText(result_summary(seg) if seg.results else
                                 ("Noch nicht ausgewertet." if seg.paradigm else ""))
        self._meta.set_content(describe_segment(self.session, seg),
                               segment_issues(self.session, seg))
        self._meta.setVisible(True)

    def _measurement_id(self, seg) -> int | None:
        for r in (seg.results.values() if seg else []):
            if isinstance(r, dict) and r.get("measurement_id"):
                return int(r["measurement_id"])
        return None

    def _update_run_enabled(self) -> None:
        seg = self.current_segment
        ok = not self._running and seg is not None and bool(seg.paradigm)
        self._run_btn.setEnabled(ok)
        if seg is not None and seg.paradigm:
            done = seg.paradigm in seg.results
            self._run_btn.setText("⟳ Neu auswerten" if done else "▶ Analyse starten")
        self._details_btn.setEnabled(self._measurement_id(seg) is not None)

    def _on_details(self) -> None:
        mid = self._measurement_id(self.current_segment)
        if mid is not None:
            self.detailsRequested.emit(mid)

    # ── analysis run ──────────────────────────────────────────────
    def _on_run(self) -> None:
        seg = self.current_segment
        if seg is None or self.session is None or not seg.paradigm:
            return
        self._pipeline.enqueue(self.session, seg, analyse=True)

    def _mine(self, session) -> bool:
        return session is self.session

    def _on_preview(self, msg: dict) -> None:
        job = self._pipeline.job
        if job is None or not self._mine(job["session"]):
            return
        self._overlay.set_frame(msg.get("jpeg", ""), msg.get("landmarks", []),
                                msg.get("face"))

    def _refresh_plot(self) -> None:
        win = int(cfg("analysis", "live_plot_window_points", default=300))
        runner = self._pipeline.runner
        # Show both hands live (so the moving one is visible); the result uses the
        # moving hand, attributed to the chosen side.
        self._plot.update_plot(runner.live, runner.metric_label(), window_points=win)

    def _on_started(self, session, seg) -> None:
        if not self._mine(session):
            return
        self._running = True
        self._run_btn.setEnabled(False)
        self._result_lbl.setText("")
        self._overlay.set_placeholder("Analyse läuft …")
        self._overlay.clear()
        self._plot.clear_plot()
        self._plot_timer.start()

    def _on_stage_text(self, text: str) -> None:
        job = self._pipeline.job
        if job is not None and self._mine(job["session"]):
            self._status.setText(text)

    def _on_job_done(self, session, seg) -> None:
        if not self._mine(session):
            return
        self._running = False
        self._refresh_segment_list()
        self._reselect_current_segment()
        if self.current_segment is not None:
            self._show_existing_result()
        self._update_run_enabled()

    def _on_finished(self, session, seg, features: dict) -> None:
        if not self._mine(session):
            return
        self._plot_timer.stop()
        self._refresh_plot()
        self._running = False
        self._refresh_segment_list()
        self._reselect_current_segment()
        if self.current_segment is seg:
            self._show_existing_result()
        cov = (seg.results.get(seg.paradigm) or {}).get("eye_ref_coverage") if seg.paradigm else None
        if isinstance(cov, (int, float)) and cov < 0.5:
            self._status.setText(
                f"⚠ Augen-Referenz nur in {cov:.0%} der Frames gefunden — "
                "Amplituden unzuverlässig (Gesicht im Video sichtbar?).")
        elif not features:
            hand = self._HAND_LONG.get(seg.hand, seg.hand)
            self._status.setText(f"Keine {hand}e Hand im Segment erkannt — evtl. andere "
                                 "Seite wählen (Umbenennen → Seite).")
        self._update_run_enabled()

    def _on_failed(self, session, seg, msg: str) -> None:
        if not self._mine(session):
            return
        self._plot_timer.stop()
        self._running = False
        self._status.setText(f"Fehler: {msg}")
        self._update_run_enabled()

    def _reselect_current_segment(self) -> None:
        if self.current_segment is None:
            return
        for i in range(self._seg_list.count()):
            it = self._seg_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == self.current_segment.id:
                self._seg_list.blockSignals(True)
                self._seg_list.setCurrentItem(it)
                self._seg_list.blockSignals(False)
                break

    # ── enable/disable ────────────────────────────────────────────
    def _set_controls_enabled(self, has_video: bool) -> None:
        for w in (self._play_btn, self._timeline, self._add_seg_btn, self._rotate_btn):
            w.setEnabled(has_video)
        self._update_run_enabled()

    def _on_save_session(self) -> None:
        if self.session is None:
            return
        try:
            p = self.session.save()
            self._status.setText(f"Video-Session gespeichert: {p.name}")
        except Exception as e:
            QMessageBox.critical(self, "VideoLab", f"Speichern fehlgeschlagen:\n{e}")
