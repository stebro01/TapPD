"""One screen per session: its content on the left, a workbench on the right.

Import, protocol and single paradigm are not three screens but three ways to
*add* content to the session (the "Hinzufügen" menu). Everything else —
record, review, cut, analyse, export, delete — acts on items of one list, and
the workbench follows the selection:

    recording step      → RecordingPane  (live preview, record / review cycle)
    imported video      → CutPane        (the whole video with its timeline)
    nothing / new       → EmptyPane      (three cards: what to add)

The screen also owns the live camera and the footer (camera picker, face
toggle, status), so the panes stay free of device lifecycle.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from storage.database import Session
from ui import theme
from ui.recording_pane import RecordingPane
from ui.theme import SZ
from video.store import STEP_CONFIRMED, STEP_PENDING, STEP_RECORDED, VideoSession

log = logging.getLogger(__name__)

_MARK = {STEP_PENDING: "○", STEP_RECORDED: "◐", STEP_CONFIRMED: "✔"}
_STATE = {STEP_PENDING: "offen", STEP_RECORDED: "aufgenommen, ungesichtet",
          STEP_CONFIRMED: "Video ✔"}


def _paradigm_label(key: str) -> str:
    try:
        from paradigms import registry
        return (registry.get(key).label or key).replace("\n", " ")
    except Exception:
        return key


class _Card(QFrame):
    """A big clickable card for the empty state."""

    def __init__(self, icon: str, title: str, text: str, on_click, primary=False) -> None:
        super().__init__()
        self._on_click = on_click
        border = theme.PRIMARY if primary else theme.BORDER
        self.setStyleSheet(
            f"QFrame {{ background: {theme.CARD_BG}; border: 2px solid {border};"
            f" border-radius: 12px; }}"
            f"QFrame:hover {{ background: {theme.HOVER_BG}; border-color: {theme.PRIMARY}; }}"
            "QLabel { border: none; background: transparent; }")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMinimumSize(220, 150)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(6)
        i = QLabel(icon)
        i.setStyleSheet("font-size: 30px;")
        i.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(i)
        t = QLabel(title)
        t.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {theme.TEXT};")
        t.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(t)
        d = QLabel(text)
        d.setWordWrap(True)
        d.setStyleSheet(f"font-size: 12px; color: {theme.TEXT_SECONDARY};")
        d.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(d)

    def mousePressEvent(self, event) -> None:
        self._on_click()


class SessionScreen(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.session: Session | None = None
        self.video: VideoSession | None = None
        self._device = None
        self._owns_device = False
        self._prev = {}
        self._started_stream = False
        self._node: dict[int, dict] = {}
        self._build()

    # ── layout ───────────────────────────────────────────────────
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 12)
        root.setSpacing(12)

        # header
        head = QHBoxLayout()
        back = QPushButton("← Patient")
        back.setProperty("cssClass", "flat")
        back.setFixedHeight(SZ.BTN_H)
        back.clicked.connect(self._on_back)
        head.addWidget(back)
        self._title = QLabel()
        self._title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {theme.TEXT};")
        head.addWidget(self._title, 1)
        self._subtitle = QLabel()
        self._subtitle.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        head.addWidget(self._subtitle)
        self._add_btn = QToolButton()
        self._add_btn.setText("＋ Hinzufügen")
        self._add_btn.setProperty("cssClass", "accent")
        self._add_btn.setFixedHeight(SZ.BTN_H)
        self._add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self._add_btn)
        for label, cb in (("Protokoll aufnehmen…", self._add_protocol),
                          ("Einzelnes Paradigma…", self._add_single),
                          ("Video importieren…", self._add_import)):
            act = QAction(label, self)
            act.triggered.connect(lambda _c=False, f=cb: f())
            menu.addAction(act)
        self._add_btn.setMenu(menu)
        head.addWidget(self._add_btn)
        root.addLayout(head)

        # body
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        sec = QLabel("Inhalt")
        sec.setProperty("cssClass", "section")
        ll.addWidget(sec)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Element", "Ergebnis"])
        # Titles get the room; the result column only what it needs.
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(False)
        self.tree.setIndentation(16)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.currentItemChanged.connect(self._on_selection)
        self.tree.setStyleSheet("QTreeWidget { border-radius: 8px; }"
                                "QTreeWidget::item { padding: 6px 4px; }")
        ll.addWidget(self.tree, 1)
        left.setMinimumWidth(320)
        left.setMaximumWidth(520)
        split.addWidget(left)

        self._work = QStackedWidget()
        self._empty = self._build_empty()
        self._rec = RecordingPane()
        self._rec.contentChanged.connect(self._refresh)
        self._rec.statusChanged.connect(self._set_status)
        self._rec.busyChanged.connect(self._on_busy)
        from ui.video_lab_screen import VideoLabScreen
        self._cut = VideoLabScreen(self.main_window, embedded=True)
        self._cut.contentChanged.connect(self._refresh)
        for w in (self._empty, self._rec, self._cut):
            self._work.addWidget(w)
        split.addWidget(self._work)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setHandleWidth(8)
        self._split = split
        root.addWidget(split, 1)

        # footer
        foot = QHBoxLayout()
        cam_lbl = QLabel("Kamera:")
        cam_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        foot.addWidget(cam_lbl)
        self._cam_combo = QComboBox()
        self._cam_combo.setMinimumWidth(220)
        self._cam_combo.currentIndexChanged.connect(self._on_camera_changed)
        foot.addWidget(self._cam_combo)
        self._face_cb = QCheckBox("Gesicht erkennen")
        self._face_cb.setChecked(True)
        self._face_cb.toggled.connect(self._on_face_toggled)
        foot.addWidget(self._face_cb)
        foot.addStretch()
        self._status = QLabel()
        self._status.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        foot.addWidget(self._status)
        root.addLayout(foot)

    def _build_empty(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch()
        h = QLabel("Was soll in dieser Sitzung aufgezeichnet werden?")
        h.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        h.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {theme.TEXT};")
        lay.addWidget(h)
        lay.addSpacing(12)
        cards = QHBoxLayout()
        cards.setSpacing(16)
        cards.addStretch()
        cards.addWidget(_Card("●", "Protokoll aufnehmen",
                              "Mehrere Schritte nacheinander filmen — z. B. Ruhe, "
                              "Kopfdrehung, Finger-Tapping beidseits.",
                              self._add_protocol, primary=True))
        cards.addWidget(_Card("▶", "Einzelnes Paradigma",
                              "Nur eine motorische Aufgabe auf Video, sofort auswertbar.",
                              self._add_single))
        cards.addWidget(_Card("🎬", "Video importieren",
                              "Ein vorhandenes Video laden, Bereiche schneiden und auswerten.",
                              self._add_import))
        cards.addStretch()
        lay.addLayout(cards)
        lay.addStretch()
        return w

    # ── lifecycle ────────────────────────────────────────────────
    def open(self, session: Session, video: VideoSession) -> None:
        self.session, self.video = session, video
        p = self.main_window.current_patient
        code = getattr(p, "patient_code", "") if p else ""
        self._title.setText(f"Sitzung  ·  {_format_datetime(session.started_at)}")
        self._subtitle.setText(code)
        # Splitter sizes only stick once the widget has a real size.
        self._split.setSizes([360, max(600, self.width() - 360)])
        self._acquire_device()
        self._populate_cameras()
        self._rec.set_session(video)
        self._rec.set_device(self._device)
        self._cut.load_session(p, video)
        self._refresh()
        # Land on the first open step; otherwise on the empty state / last item.
        nxt = video.next_open_step()
        if nxt is not None:
            self._select(("step", nxt.id))
        elif video.video_path:
            self._select(("import", ""))
        elif video.steps:
            self._select(("step", video.steps[-1].id))
        else:
            self._work.setCurrentWidget(self._empty)

    def close(self) -> None:
        self._rec.stop()
        self._cut.on_leave()
        if self.video is not None:
            self.video.save()
        self._release_device()
        self.session, self.video = None, None
        self.tree.clear()

    # ── content list ─────────────────────────────────────────────
    def _refresh(self) -> None:
        if self.video is None:
            return
        current = self._current_key()
        self.tree.blockSignals(True)
        self.tree.clear()
        self._node.clear()
        v = self.video

        if v.steps:
            done, total = v.progress
            grp = QTreeWidgetItem([f"{v.protocol_name or 'Aufnahme'}",
                                   f"{done}/{total}"])
            f = grp.font(0); f.setBold(True); grp.setFont(0, f)
            self._tag(grp, ("group", ""))
            self.tree.addTopLevelItem(grp)
            for i, st in enumerate(v.steps, start=1):
                seg = next((s for s in v.segments if s.id == st.segment_id), None)
                res = self._result(seg) if seg else ""
                if st.is_documentation and st.state == STEP_CONFIRMED:
                    res = "Dokumentation"
                label = f"{_MARK.get(st.state, '○')}  {i}. {st.title}"
                if not st.is_documentation:
                    label += f"  ({st.hand})"
                it = QTreeWidgetItem([label, res])
                it.setToolTip(0, _STATE.get(st.state, ""))
                if st.state == STEP_CONFIRMED:
                    it.setForeground(0, QColor(theme.ACCENT_DARK))
                self._tag(it, ("step", st.id))
                grp.addChild(it)
            grp.setExpanded(True)

        if v.video_path:
            n = len(v.segments) - sum(1 for s in v.steps if s.segment_id)
            it = QTreeWidgetItem([f"🎬  Import: {v.video_name or 'Video'}",
                                  f"{n} Segmente"])
            f = it.font(0); f.setBold(True); it.setFont(0, f)
            self._tag(it, ("import", ""))
            self.tree.addTopLevelItem(it)

        self.tree.blockSignals(False)
        if current:
            self._select(current)
        if not v.steps and not v.video_path:
            self._work.setCurrentWidget(self._empty)

    @staticmethod
    def _result(seg) -> str:
        parts = []
        for res in (seg.results or {}).values():
            mpi = ((res or {}).get("features") or {}).get("mpi")
            parts.append(f"MPI {mpi:.2f}" if isinstance(mpi, (int, float)) else "✔")
        return ", ".join(parts)

    def _tag(self, item: QTreeWidgetItem, key: tuple) -> None:
        self._node[id(item)] = {"key": key}
        item.setData(0, Qt.ItemDataRole.UserRole, id(item))

    def _key(self, item) -> tuple | None:
        if item is None:
            return None
        return self._node.get(item.data(0, Qt.ItemDataRole.UserRole), {}).get("key")

    def _current_key(self) -> tuple | None:
        return self._key(self.tree.currentItem())

    def _select(self, key: tuple) -> None:
        def walk(item):
            for i in range(item.childCount()):
                c = item.child(i)
                if self._key(c) == key:
                    return c
                r = walk(c)
                if r:
                    return r
            return None
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            hit = top if self._key(top) == key else walk(top)
            if hit:
                self.tree.setCurrentItem(hit)
                self._on_selection(hit, None)
                return

    def _on_selection(self, item, _prev) -> None:
        key = self._key(item)
        if key is None or self._rec.busy:
            return
        kind, ident = key
        if kind == "step":
            self._work.setCurrentWidget(self._rec)
            self._rec.show_step(ident)
        elif kind == "import":
            self._work.setCurrentWidget(self._cut)
        elif kind == "group":
            nxt = self.video.next_open_step() if self.video else None
            if nxt:
                self._select(("step", nxt.id))

    # ── adding content ───────────────────────────────────────────
    def _add_protocol(self) -> None:
        self._add_via_chooser(single_only=False)

    def _add_single(self) -> None:
        self._add_via_chooser(single_only=True)

    def _add_via_chooser(self, *, single_only: bool) -> None:
        from ui.protocol_chooser import ProtocolChooser
        if self.video is None:
            return
        dlg = ProtocolChooser(self, single_only=single_only)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        try:
            protocol = dlg.protocol()
        except Exception as e:
            QMessageBox.warning(self, "Protokoll", f"Nicht nutzbar:\n{e}")
            return
        added = self.video.add_steps(protocol)
        self.video.save()
        self._refresh()
        if added:
            self._select(("step", added[0].id))
        self._set_status(f"{len(added)} Schritt{'' if len(added) == 1 else 'e'} hinzugefügt.")

    def _add_import(self) -> None:
        if self.video is None:
            return
        self._work.setCurrentWidget(self._cut)
        self._cut.import_video()

    # ── context menu ─────────────────────────────────────────────
    def _actions_for(self, key: tuple | None) -> list:
        if key is None or self.video is None:
            return []
        kind, ident = key
        out = []
        if kind == "step":
            st = self.video.step(ident)
            if st is None:
                return []
            if st.state == STEP_CONFIRMED:
                out.append(("↻ Erneut aufnehmen", lambda: self._retake(ident)))
            else:
                out.append(("● Aufnehmen", lambda: self._select(("step", ident))))
            out.append(("Schritt entfernen", lambda: self._remove_step(ident)))
        elif kind == "group":
            out.append(("Alle Schritte entfernen", self._remove_all_steps))
        elif kind == "import":
            out.append(("Video entfernen", self._remove_import))
        return out

    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        actions = self._actions_for(self._key(item))
        if not actions:
            return
        menu = QMenu(self)
        for label, cb in actions:
            a = menu.addAction(label)
            a.triggered.connect(lambda _c=False, f=cb: f())
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _retake(self, step_id: str) -> None:
        self.video.retake_step(step_id)
        self.video.save()
        self._refresh()
        self._select(("step", step_id))

    def _remove_step(self, step_id: str) -> None:
        st = self.video.step(step_id)
        if st is None:
            return
        if QMessageBox.question(self, "Schritt entfernen",
                                f"„{st.title}“ aus der Sitzung entfernen?\n"
                                "Aufgenommene Dateien bleiben auf der Platte.") \
                != QMessageBox.StandardButton.Yes:
            return
        self.video.remove_step(step_id)
        self.video.save()
        self._refresh()

    def _remove_all_steps(self) -> None:
        if QMessageBox.question(self, "Schritte entfernen",
                                "Alle Aufnahme-Schritte aus der Sitzung entfernen?\n"
                                "Aufgenommene Dateien bleiben auf der Platte.") \
                != QMessageBox.StandardButton.Yes:
            return
        for st in list(self.video.steps):
            self.video.remove_step(st.id)
        self.video.save()
        self._refresh()

    def _remove_import(self) -> None:
        if QMessageBox.question(self, "Video entfernen",
                                "Importiertes Video samt Segmenten aus der Sitzung "
                                "entfernen?\nDie Datei bleibt auf der Platte.") \
                != QMessageBox.StandardButton.Yes:
            return
        keep = {s.segment_id for s in self.video.steps if s.segment_id}
        self.video.segments = [s for s in self.video.segments if s.id in keep]
        self.video.video_path = ""
        self.video.video_name = ""
        self.video.save()
        self._cut.load_session(self.main_window.current_patient, self.video)
        self._refresh()

    # ── camera ───────────────────────────────────────────────────
    def _acquire_device(self) -> None:
        from capture.mediapipe_capture import WebcamSource
        dev = getattr(self.main_window, "capture_device", None)
        if isinstance(dev, WebcamSource) and dev.is_connected() and not dev.replay_path:
            self._device, self._owns_device = dev, False
        else:
            ok, issues = WebcamSource.sidecar_ready()
            if not ok:
                self._set_status(issues[0], True)
                self._device = None
                return
            try:
                dev = WebcamSource()
                dev.connect()
                dev.start_tracking(lambda _f: None)
                self._device, self._owns_device = dev, True
            except Exception as e:
                log.warning("Webcam nicht verfügbar: %s", e)
                self._set_status(f"Kamera nicht verfügbar: {e}", True)
                self._device = None
                return
        d = self._device
        self._prev = {"preview": getattr(d, "_preview_callback", None),
                      "recorded": getattr(d, "_recorded_callback", None),
                      "face": bool(getattr(d, "_face_on", False))}
        self._started_stream = False
        if not getattr(d, "_recording", False):
            d.start_tracking(lambda _f: None)
            self._started_stream = True
        d.configure(num_hands=2)
        d.enable_face(self._face_cb.isChecked())
        d.enable_preview(True)

    def _release_device(self) -> None:
        d = self._device
        if d is None:
            return
        try:
            d.set_recorded_callback(self._prev.get("recorded"))
            d.set_preview_callback(self._prev.get("preview"))
            d.enable_face(self._prev.get("face", False))
            if self._started_stream:
                d.stop_tracking()
            if self._owns_device:
                d.disconnect()
        except Exception:
            log.debug("Kamera-Aufräumen fehlgeschlagen", exc_info=True)
        self._device, self._owns_device, self._started_stream = None, False, False

    def _populate_cameras(self) -> None:
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
        pos = self._cam_combo.findData(getattr(self._device, "camera_index", 0))
        if pos >= 0:
            self._cam_combo.setCurrentIndex(pos)
        self._cam_combo.setEnabled(bool(cams))
        self._cam_combo.blockSignals(False)

    def _on_camera_changed(self, row: int) -> None:
        d = self._device
        if d is None or row < 0:
            return
        cam = self._cam_combo.currentData()
        if cam is None or cam < 0 or cam == getattr(d, "camera_index", 0):
            return
        streaming = bool(getattr(d, "_recording", False))
        if streaming:
            d.stop_tracking()
        d.camera_index = int(cam)
        if streaming:
            d.start_tracking(lambda _f: None)
        d.enable_preview(True)
        try:
            from app_settings import app_settings
            app_settings().setValue("camera_index", int(cam))
        except Exception:
            pass
        self._set_status(f"Kamera: {self._cam_combo.currentText()}")

    def _on_face_toggled(self, on: bool) -> None:
        if self._device is not None:
            self._device.enable_face(bool(on))

    def _on_busy(self, busy: bool) -> None:
        self._cam_combo.setEnabled(not busy and self._cam_combo.count() > 0)
        self._add_btn.setEnabled(not busy)
        self.tree.setEnabled(not busy)

    # ── misc ─────────────────────────────────────────────────────
    def _set_status(self, text: str, error: bool = False) -> None:
        self._status.setStyleSheet(
            f"color: {theme.DANGER if error else theme.TEXT_SECONDARY};")
        self._status.setText(text)

    def _on_back(self) -> None:
        self.main_window.close_session()


def _format_datetime(iso: str) -> str:
    if not iso:
        return "–"
    try:
        d, t = iso[:10].split("-"), iso[11:16]
        return f"{d[2]}.{d[1]}.{d[0]}" + (f"  {t}" if t else "")
    except (IndexError, ValueError):
        return iso[:16]
