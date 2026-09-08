"""The patient workbench: everything about one patient on one screen.

Left, every session with what it holds — recording steps with their state and
result, an imported video, measurements taken directly. Right, a workbench
that follows the selection: a recording step opens the RecordingPane, an
imported video the cut pane, a fresh session three cards saying what can be
added. "Neue Sitzung" adds a group to the list; "Hinzufügen" puts content into
the selected one. There is no separate session list any more — clicking the
patient *is* seeing, recording and playing.

The screen owns the live camera and the footer (camera picker, face toggle,
status), and re-binds the panes to whichever session's video the selection
belongs to. A pipeline running on another session keeps going; the recording
pane's jobs carry their own session.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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

from storage.database import (
    Measurement,
    Patient,
    Session,
    create_session,
    delete_measurement,
    delete_patient,
    delete_session,
    get_db,
    get_measurements,
    get_session_measurements,
    get_sessions,
    save_patient,
)
from ui import theme
from ui.detail_dialog import DetailDialog
from ui.patient_screen import NewPatientDialog
from ui.recording_pane import RecordingPane
from ui.theme import SZ
from video.store import (
    STEP_CONFIRMED,
    STEP_PENDING,
    STEP_RECORDED,
    VideoSession,
    load_for_session,
)

log = logging.getLogger(__name__)

_MARK = {STEP_PENDING: "○", STEP_RECORDED: "◐", STEP_CONFIRMED: "✔"}
_STATE = {STEP_PENDING: "offen", STEP_RECORDED: "aufgenommen, ungesichtet",
          STEP_CONFIRMED: "bestätigt"}


def _paradigm_label(key: str) -> str:
    try:
        from paradigms import registry
        return (registry.get(key).label or key).replace("\n", " ")
    except Exception:
        return key


def _fmt_dt(iso: str) -> str:
    if not iso:
        return "–"
    try:
        d, t = iso[:10].split("-"), iso[11:16]
        return f"{d[2]}.{d[1]}.{d[0]}" + (f"  {t}" if t else "")
    except (IndexError, ValueError):
        return iso[:16]


class _Card(QFrame):
    """A big clickable card for a session's empty state."""

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
        for txt, style in ((icon, "font-size: 30px;"),
                           (title, f"font-size: 15px; font-weight: 700; color: {theme.TEXT};"),
                           (text, f"font-size: 12px; color: {theme.TEXT_SECONDARY};")):
            lbl = QLabel(txt)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(style)
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lay.addWidget(lbl)

    def mousePressEvent(self, event) -> None:
        self._on_click()


class _LabelDialog(QDialog):
    """Paradigm + side, for adding a live measurement or relabelling a step."""

    def __init__(self, parent, title: str, paradigm: str = "", hand: str = "right") -> None:
        super().__init__(parent)
        from paradigms import registry
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Paradigma und Seite:"))
        row = QHBoxLayout()
        self.para = QComboBox()
        for key in registry.all_keys():
            self.para.addItem(_paradigm_label(key), key)
        if paradigm:
            i = self.para.findData(paradigm)
            if i >= 0:
                self.para.setCurrentIndex(i)
        row.addWidget(self.para, 1)
        self.hand = QComboBox()
        for label, value in (("rechts", "right"), ("links", "left"), ("beide", "both")):
            self.hand.addItem(label, value)
        i = self.hand.findData(hand)
        if i >= 0:
            self.hand.setCurrentIndex(i)
        row.addWidget(self.hand)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class PatientWorkbench(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self._patient: Patient | None = None
        self._sessions: list[Session] = []
        self._measurements: dict[int, list[Measurement]] = {}
        self._orphans: list[Measurement] = []
        self._videos: dict[int, VideoSession] = {}
        self._bound: VideoSession | None = None      # video the panes show
        self._binding = False                        # re-entrancy guard, see _bind
        self._node: dict[int, dict] = {}
        self._device = None
        self._owns_device = False
        self._prev = {}
        self._started_stream = False
        self._build()

    # ── layout ───────────────────────────────────────────────────
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 14, 24, 10)
        root.setSpacing(10)

        # header: back · patient · actions
        head = QHBoxLayout()
        back = QPushButton("← Patienten")
        back.setProperty("cssClass", "flat")
        back.setFixedHeight(SZ.BTN_H)
        back.clicked.connect(lambda: self.main_window.show_patient_screen())
        head.addWidget(back)
        head.addSpacing(8)
        name_col = QVBoxLayout()
        name_col.setSpacing(0)
        self._name = QLabel()
        self._name.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {theme.PRIMARY};")
        self._details = QLabel()
        self._details.setStyleSheet(f"font-size: 12px; color: {theme.TEXT_SECONDARY};")
        name_col.addWidget(self._name)
        name_col.addWidget(self._details)
        head.addLayout(name_col, 1)

        self._patient_btn = QToolButton()
        self._patient_btn.setText("Patient ▾")
        self._patient_btn.setFixedHeight(SZ.BTN_H)
        self._patient_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        pm = QMenu(self._patient_btn)
        for label, cb in (("Bearbeiten…", self._on_edit_patient),
                          ("📈 Verlauf", self._on_trend),
                          ("CSV-Export…", self._on_csv_export)):
            a = QAction(label, self)
            a.triggered.connect(lambda _c=False, f=cb: f())
            pm.addAction(a)
        pm.addSeparator()
        a = QAction("Patient löschen…", self)
        a.triggered.connect(lambda _c=False: self._on_delete_patient())
        pm.addAction(a)
        self._patient_btn.setMenu(pm)
        head.addWidget(self._patient_btn)

        self._new_btn = QPushButton("＋ Neue Sitzung")
        self._new_btn.setProperty("cssClass", "accent")
        self._new_btn.setFixedHeight(SZ.BTN_H)
        self._new_btn.clicked.connect(self.new_session)
        head.addWidget(self._new_btn)

        self._add_btn = QToolButton()
        self._add_btn.setText("＋ Hinzufügen ▾")
        self._add_btn.setFixedHeight(SZ.BTN_H)
        self._add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        am = QMenu(self._add_btn)
        for label, cb in (("Protokoll aufnehmen…", self._add_protocol),
                          ("Einzelnes Paradigma…", self._add_single),
                          ("Video importieren…", self._add_import),
                          ("Live-Messung (Sensor/Bildschirm)…", self._add_live)):
            a = QAction(label, self)
            a.triggered.connect(lambda _c=False, f=cb: f())
            am.addAction(a)
        self._add_btn.setMenu(am)
        head.addWidget(self._add_btn)
        root.addLayout(head)

        # body
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(8)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        sec = QLabel("Sitzungen")
        sec.setProperty("cssClass", "section")
        ll.addWidget(sec)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Sitzung / Element", "Ergebnis"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(False)
        self.tree.setIndentation(16)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.currentItemChanged.connect(self._on_selection)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.setStyleSheet("QTreeWidget { border-radius: 8px; }"
                                "QTreeWidget::item { padding: 6px 4px; }")
        ll.addWidget(self.tree, 1)
        self._count = QLabel()
        self._count.setStyleSheet(f"font-size: 12px; color: {theme.TEXT_SECONDARY};")
        ll.addWidget(self._count)
        left.setMinimumWidth(340)
        left.setMaximumWidth(560)
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
        self._detail = self._build_measurement_pane()
        for w in (self._empty, self._rec, self._cut, self._detail):
            self._work.addWidget(w)
        split.addWidget(self._work)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
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
        self._empty_title = QLabel()
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._empty_title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {theme.TEXT};")
        lay.addWidget(self._empty_title)
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

    def _build_measurement_pane(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch()
        self._m_title = QLabel()
        self._m_title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {theme.TEXT};")
        self._m_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self._m_title)
        self._m_text = QLabel()
        self._m_text.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._m_text.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        lay.addWidget(self._m_text)
        row = QHBoxLayout()
        row.addStretch()
        self._m_btn = QPushButton("Details öffnen")
        self._m_btn.setProperty("cssClass", "primary")
        self._m_btn.clicked.connect(lambda: self._show_measurement(self._m_current))
        row.addWidget(self._m_btn)
        row.addStretch()
        lay.addLayout(row)
        lay.addStretch()
        self._m_current = None
        return w

    # ── lifecycle ────────────────────────────────────────────────
    def set_patient(self, patient: Patient) -> None:
        self._patient = patient
        self._update_patient_card()
        self._split.setSizes([400, max(600, self.width() - 400)])
        self._acquire_device()
        self._populate_cameras()
        self.refresh()
        self._select_first_open()

    def leave(self) -> None:
        self._rec.stop()
        self._cut.on_leave()
        for v in self._videos.values():
            try:
                v.save()
            except Exception:
                pass
        self._release_device()
        self._bound = None

    def _update_patient_card(self) -> None:
        p = self._patient
        if not p:
            return
        self._name.setText(p.display_name)
        parts = []
        if p.age is not None:
            parts.append(f"{p.age} Jahre")
        g = {"m": "Männlich", "f": "Weiblich", "d": "Divers"}.get(p.gender)
        if g:
            parts.append(g)
        if p.birth_date:
            try:
                y, m, d = p.birth_date.split("-")
                parts.append(f"geb. {d}.{m}.{y}")
            except ValueError:
                pass
        if p.notes:
            parts.append(p.notes)
        self._details.setText("  ·  ".join(parts))

    # ── data ─────────────────────────────────────────────────────
    def refresh(self) -> None:
        if not self._patient or not self._patient.id:
            return
        conn = get_db()
        self._sessions = get_sessions(conn, self._patient.id)
        self._measurements = {s.id: get_session_measurements(conn, s.id) for s in self._sessions}
        self._orphans = [m for m in get_measurements(conn, self._patient.id) if m.session_id is None]
        conn.close()
        newest = self._sessions[0].id if self._sessions else None
        videos = {}
        for s in self._sessions:
            v = self._videos.get(s.id)         # keep live objects the panes hold
            if v is None:
                v = load_for_session(self._patient.id, self._patient.patient_code,
                                     s.id, newest_session_id=newest)
            if v is not None:
                videos[s.id] = v
        self._videos = videos
        self._populate()

    def _refresh(self) -> None:
        """Content changed inside a pane: rebuild the list, keep the selection.

        Deferred to the event loop and ignored while a pane is being bound —
        binding the cut pane emits contentChanged synchronously, and rebuilding
        the tree in the middle of a selection deletes the very item that is
        being selected.
        """
        if self._binding:
            return
        QTimer.singleShot(0, self.refresh)

    def _video_for(self, session: Session, create: bool = False) -> VideoSession | None:
        v = self._videos.get(session.id)
        if v is None and create and self._patient:
            v = VideoSession.create(self._patient.id, self._patient.patient_code)
            v.db_session_id = session.id
            self._videos[session.id] = v
        return v

    # ── tree ─────────────────────────────────────────────────────
    def _populate(self) -> None:
        current = self._current_key()
        self.tree.blockSignals(True)
        self.tree.clear()
        self._node.clear()

        for number, s in zip(range(len(self._sessions), 0, -1), self._sessions):
            self._add_session_node(s, number)
        if self._orphans:
            grp = QTreeWidgetItem([f"Ohne Sitzung ({len(self._orphans)})", ""])
            grp.setForeground(0, QColor(theme.TEXT_SECONDARY))
            self._tag(grp, ("orphans", 0))
            self.tree.addTopLevelItem(grp)
            for m in self._orphans:
                grp.addChild(self._measurement_node(m))

        total = sum(len(v) for v in self._measurements.values()) + len(self._orphans)
        n = len(self._sessions)
        self._count.setText(f"{n} Sitzung{'' if n == 1 else 'en'} · "
                            f"{total} Messung{'' if total == 1 else 'en'}")
        self.tree.blockSignals(False)
        if current is not None:
            self._select(current, silent=True)
        if self.tree.topLevelItemCount() == 0:
            self._show_empty(None)

    def _add_session_node(self, s: Session, number: int) -> None:
        v = self._videos.get(s.id)
        ms = self._measurements.get(s.id, [])
        if v is not None and v.steps:
            done, count = v.progress
            kind, status = "Protokoll", (f"{done}/{count}" if done < count else "✔")
        elif v is not None and v.video_path:
            kind, status = "Import", f"{len(v.segments)} Seg."
        elif ms:
            kind, status = "Messungen", str(len(ms))
        else:
            kind, status = "leer", ""
        node = QTreeWidgetItem([f"Sitzung {number}  ·  {_fmt_dt(s.started_at)}  ·  {kind}", status])
        if v is not None and v.protocol_name:
            node.setToolTip(0, v.protocol_name)
        f = node.font(0); f.setBold(True); node.setFont(0, f)
        self._tag(node, ("session", s.id))
        self.tree.addTopLevelItem(node)

        used = set()
        if v is not None:
            for i, st in enumerate(v.steps, start=1):
                node.addChild(self._step_node(s.id, v, i, st, used))
            if v.video_path:
                n_imp = sum(1 for sg in v.segments if not sg.recorded)
                it = QTreeWidgetItem([f"🎬  Import: {v.video_name or 'Video'}", f"{n_imp} Seg."])
                self._tag(it, ("import", s.id))
                node.addChild(it)
        for m in ms:
            if m.id not in used:
                node.addChild(self._measurement_node(m))
        node.setExpanded(True)

    def _step_node(self, sid: int, v: VideoSession, i: int, st, used: set) -> QTreeWidgetItem:
        seg = next((x for x in v.segments if x.id == st.segment_id), None)
        if st.is_documentation:
            result = "Doku" if st.state == STEP_CONFIRMED else ""
        elif seg is None or not seg.results:
            result = "" if st.state == STEP_PENDING else "nicht ausgewertet"
        else:
            parts = []
            for res in seg.results.values():
                mpi = ((res or {}).get("features") or {}).get("mpi")
                txt = f"MPI {mpi:.2f}" if isinstance(mpi, (int, float)) else "✔"
                if (res or {}).get("measurement_id"):
                    used.add(res["measurement_id"])
                    txt += " ·📋"
                parts.append(txt)
            result = ", ".join(parts)
        label = f"{_MARK.get(st.state, '○')}  {i}. {st.title}"
        if not st.is_documentation:
            label += f"  ({st.hand})"
        it = QTreeWidgetItem([label, result])
        it.setToolTip(0, _STATE.get(st.state, ""))
        it.setToolTip(1, "📋 = in der Akte" if "📋" in result else "")
        if st.state == STEP_CONFIRMED:
            it.setForeground(0, QColor(theme.ACCENT_DARK))
        self._tag(it, ("step", sid, st.id))
        return it

    def _measurement_node(self, m: Measurement) -> QTreeWidgetItem:
        mpi = (m.features or {}).get("mpi")
        it = QTreeWidgetItem([f"    {_paradigm_label(m.test_type)}  ({m.hand})",
                              f"MPI {mpi:.2f}" if isinstance(mpi, (int, float)) else "✔"])
        it.setForeground(0, QColor(theme.TEXT_SECONDARY))
        self._tag(it, ("measurement", m.id))
        return it

    def _tag(self, item, key: tuple) -> None:
        self._node[id(item)] = {"key": key}
        item.setData(0, Qt.ItemDataRole.UserRole, id(item))

    def _key(self, item):
        if item is None:
            return None
        return self._node.get(item.data(0, Qt.ItemDataRole.UserRole), {}).get("key")

    def _current_key(self):
        return self._key(self.tree.currentItem())

    def _find(self, key):
        def walk(it):
            for i in range(it.childCount()):
                c = it.child(i)
                if self._key(c) == key:
                    return c
                r = walk(c)
                if r:
                    return r
            return None
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if self._key(top) == key:
                return top
            r = walk(top)
            if r:
                return r
        return None

    def _select(self, key, silent: bool = False) -> None:
        it = self._find(key)
        if it is None:
            return
        was_current = self.tree.currentItem() is it
        self.tree.blockSignals(silent)
        self.tree.setCurrentItem(it)       # emits currentItemChanged unless silent
        self.tree.blockSignals(False)
        # Only an already-current item gets no signal; call the handler for it
        # ourselves. Calling it unconditionally ran the handler twice, and the
        # first run may already have rebuilt the tree under this item.
        if not silent and was_current:
            self._on_selection(it, None)

    def _select_first_open(self) -> None:
        for s in self._sessions:
            v = self._videos.get(s.id)
            if v is not None and v.next_open_step() is not None:
                self._select(("step", s.id, v.next_open_step().id))
                return
        if self._sessions:
            self._select(("session", self._sessions[0].id))
        else:
            self._show_empty(None)

    def _session_by_id(self, sid: int) -> Session | None:
        return next((s for s in self._sessions if s.id == sid), None)

    # ── selection → workbench ────────────────────────────────────
    def _bind(self, v: VideoSession | None) -> None:
        if v is self._bound:
            return
        self._binding = True
        try:
            self._bound = v
            self._rec.set_session(v)
            self._rec.set_device(self._device)
            if v is not None:
                self._cut.load_session(self._patient, v)
        finally:
            self._binding = False

    def _show_empty(self, session: Session | None) -> None:
        self._empty_title.setText(
            "Was soll in dieser Sitzung aufgezeichnet werden?" if session
            else "Noch keine Sitzung — mit „＋ Neue Sitzung“ beginnen.")
        self._work.setCurrentWidget(self._empty)

    def _on_selection(self, item, _prev) -> None:
        key = self._key(item)
        if key is None or self._rec.busy:
            return
        kind = key[0]
        if kind == "session":
            s = self._session_by_id(key[1])
            v = self._videos.get(key[1])
            if v is not None and v.next_open_step() is not None:
                self._select(("step", key[1], v.next_open_step().id))
            elif v is not None and v.video_path:
                self._bind(v)
                self._work.setCurrentWidget(self._cut)
            elif v is not None and v.steps:
                self._select(("step", key[1], v.steps[-1].id))
            else:
                self._show_empty(s)
        elif kind == "step":
            v = self._videos.get(key[1])
            if v is None:
                return
            self._bind(v)
            self._work.setCurrentWidget(self._rec)
            self._rec.show_step(key[2])
        elif kind == "import":
            v = self._videos.get(key[1])
            if v is None:
                return
            self._bind(v)
            self._work.setCurrentWidget(self._cut)
        elif kind == "measurement":
            m = self._measurement_by_id(key[1])
            if m is None:
                return
            self._m_current = m
            self._m_title.setText(f"{_paradigm_label(m.test_type)}  ({m.hand})")
            mpi = (m.features or {}).get("mpi")
            self._m_text.setText(
                f"{_fmt_dt(m.recorded_at)}  ·  {m.duration_s:g} s  ·  Quelle: {m.source_kind or '–'}"
                + (f"  ·  MPI {mpi:.2f}" if isinstance(mpi, (int, float)) else ""))
            self._work.setCurrentWidget(self._detail)

    def _measurement_by_id(self, mid: int) -> Measurement | None:
        for ms in list(self._measurements.values()) + [self._orphans]:
            for m in ms:
                if m.id == mid:
                    return m
        return None

    def _on_double_click(self, item, _col: int) -> None:
        actions = self._actions_for(self._key(item))
        if actions:
            actions[0][1]()

    # ── sessions ─────────────────────────────────────────────────
    def _target_session(self) -> Session | None:
        """The session content gets added to: the selected one, else the newest."""
        key = self._current_key()
        if key and key[0] in ("session", "step", "import"):
            s = self._session_by_id(key[1])
            if s is not None:
                return s
        return self._sessions[0] if self._sessions else None

    def new_session(self) -> Session | None:
        if not self._patient or not self._patient.id:
            return None
        conn = get_db()
        s = create_session(conn, self._patient.id)
        conn.close()
        self.main_window.current_session = s
        self.refresh()
        self._select(("session", s.id), silent=True)
        self._show_empty(s)
        self._set_status("Neue Sitzung angelegt — Inhalt über „Hinzufügen“.")
        return s

    def open_session(self, session: Session, step_id: str = "") -> None:
        """External entry (e.g. main window): show a session, optionally a step."""
        self.refresh()
        if step_id:
            self._select(("step", session.id, step_id))
        else:
            self._select(("session", session.id))

    # ── adding content ───────────────────────────────────────────
    def _add_protocol(self) -> None:
        self._add_via_chooser(single_only=False)

    def _add_single(self) -> None:
        self._add_via_chooser(single_only=True)

    def _add_via_chooser(self, *, single_only: bool) -> None:
        from ui.protocol_chooser import ProtocolChooser
        s = self._target_session() or self.new_session()
        if s is None:
            return
        dlg = ProtocolChooser(self, single_only=single_only)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            protocol = dlg.protocol()
        except Exception as e:
            QMessageBox.warning(self, "Protokoll", f"Nicht nutzbar:\n{e}")
            return
        v = self._video_for(s, create=True)
        added = v.add_steps(protocol)
        v.save()
        self.refresh()
        if added:
            self._select(("step", s.id, added[0].id))
        self._set_status(f"{len(added)} Schritt{'' if len(added) == 1 else 'e'} hinzugefügt.")

    def _add_import(self) -> None:
        s = self._target_session() or self.new_session()
        if s is None:
            return
        v = self._video_for(s, create=True)
        self._bind(v)
        self._work.setCurrentWidget(self._cut)
        self._cut.import_video()

    def _add_live(self) -> None:
        s = self._target_session() or self.new_session()
        if s is None:
            return
        dlg = _LabelDialog(self, "Live-Messung hinzufügen")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.main_window.resume_session(s)
        self.main_window.dashboard.set_patient(self._patient)
        self.main_window.start_test(dlg.para.currentData(), dlg.hand.currentData(),
                                    self.main_window.dashboard.duration_spin.value())

    # ── context actions ──────────────────────────────────────────
    def _actions_for(self, key) -> list:
        if key is None:
            return []
        kind = key[0]
        out = []
        if kind == "session":
            s = self._session_by_id(key[1])
            v = self._videos.get(key[1])
            if v is not None and v.steps and not v.is_complete:
                out.append(("● Aufnahme fortsetzen", lambda: self._select(("session", key[1]))))
            out.append(("＋ Protokoll aufnehmen…", self._add_protocol))
            out.append(("＋ Einzelnes Paradigma…", self._add_single))
            out.append(("＋ Video importieren…", self._add_import))
            out.append(("＋ Live-Messung…", self._add_live))
            out.append(("Sitzung löschen…", lambda: self._delete_session(s)))
        elif kind == "step":
            v = self._videos.get(key[1]); st = v.step(key[2]) if v else None
            if st is None:
                return []
            if st.state == STEP_CONFIRMED:
                out.append(("↻ Erneut aufnehmen", lambda: self._retake(v, st)))
                if not st.is_documentation:
                    out.append(("⟳ Neu auswerten", lambda: self._reanalyse(v, st)))
                    out.append(("Paradigma / Seite ändern…", lambda: self._relabel(v, st)))
            else:
                out.append(("● Aufnehmen", lambda: self._select(key)))
            out.append(("Schritt entfernen…", lambda: self._remove_step(v, st)))
        elif kind == "import":
            v = self._videos.get(key[1])
            out.append(("Im Schnitt-Bereich öffnen", lambda: self._select(key)))
            out.append(("Video entfernen…", lambda: self._remove_import(v)))
        elif kind == "measurement":
            m = self._measurement_by_id(key[1])
            out.append(("Details…", lambda: self._show_measurement(m)))
            out.append(("Messung löschen…", lambda: self._delete_measurement(m)))
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

    def _retake(self, v: VideoSession, st) -> None:
        v.retake_step(st.id)
        v.save()
        self.refresh()
        self._select(("step", v.db_session_id, st.id))

    def _reanalyse(self, v: VideoSession, st) -> None:
        seg = next((x for x in v.segments if x.id == st.segment_id), None)
        if seg is None:
            return
        self._bind(v)
        self._work.setCurrentWidget(self._rec)
        self._rec.show_step(st.id)
        self._rec.enqueue(v, st.id, seg, analyse=True)
        self._set_status(f"Neu auswerten: {st.title} …")

    def _relabel(self, v: VideoSession, st) -> None:
        dlg = _LabelDialog(self, "Paradigma / Seite ändern", st.paradigm, st.hand)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        seg = next((x for x in v.segments if x.id == st.segment_id), None)
        # A measurement exported under the old label would now be wrong: drop it,
        # the re-analysis creates a fresh one under the new label.
        old_ids = [r.get("measurement_id") for r in (seg.results.values() if seg else [])
                   if r and r.get("measurement_id")]
        if old_ids:
            conn = get_db()
            for mid in old_ids:
                delete_measurement(conn, int(mid))
            conn.close()
        v.relabel_step(st.id, dlg.para.currentData(), dlg.hand.currentData())
        v.save()
        self.refresh()
        if seg is not None and not st.is_documentation:
            self._reanalyse(v, st)

    def _remove_step(self, v: VideoSession, st) -> None:
        if QMessageBox.question(self, "Schritt entfernen",
                                f"„{st.title}“ aus der Sitzung entfernen?\n"
                                "Aufgenommene Dateien bleiben auf der Platte.") \
                != QMessageBox.StandardButton.Yes:
            return
        v.remove_step(st.id)
        v.save()
        self.refresh()

    def _remove_import(self, v: VideoSession) -> None:
        if v is None or QMessageBox.question(
                self, "Video entfernen",
                "Importiertes Video samt Segmenten aus der Sitzung entfernen?\n"
                "Die Datei bleibt auf der Platte.") != QMessageBox.StandardButton.Yes:
            return
        keep = {s.segment_id for s in v.steps if s.segment_id}
        v.segments = [s for s in v.segments if s.id in keep]
        v.video_path, v.video_name = "", ""
        v.save()
        if self._bound is v:
            self._cut.load_session(self._patient, v)
        self.refresh()

    def _show_measurement(self, m: Measurement | None) -> None:
        if m is not None:
            DetailDialog(self, m).exec()

    def _delete_measurement(self, m: Measurement | None) -> None:
        if m is None or QMessageBox.question(
                self, "Messung löschen",
                f"{_paradigm_label(m.test_type)} ({m.hand}) vom {_fmt_dt(m.recorded_at)} "
                "löschen?") != QMessageBox.StandardButton.Yes:
            return
        conn = get_db()
        delete_measurement(conn, m.id)
        conn.close()
        self.refresh()

    def _delete_session(self, s: Session | None) -> None:
        if s is None:
            return
        ms = self._measurements.get(s.id, [])
        v = self._videos.get(s.id)
        extra = f", {len(v.steps)} Aufnahme-Schritte" if v and v.steps else ""
        if QMessageBox.question(
                self, "Sitzung löschen",
                f"Sitzung vom {_fmt_dt(s.started_at)} löschen?\n\n"
                f"Enthält {len(ms)} Messung{'en' if len(ms) != 1 else ''}{extra}.\n"
                "Messungen werden aus der Akte entfernt; Videodateien bleiben auf der Platte.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return
        if self._bound is v:
            self._rec.stop()
            self._rec.set_device(self._device)
            self._bound = None
        conn = get_db()
        delete_session(conn, s.id)
        conn.close()
        self._videos.pop(s.id, None)
        self.refresh()
        self._select_first_open()

    # ── patient menu ─────────────────────────────────────────────
    def _on_edit_patient(self) -> None:
        if not self._patient:
            return
        dlg = NewPatientDialog(self, patient=self._patient)
        dlg.code_input.setReadOnly(True)
        dlg.setWindowTitle("Patient bearbeiten")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            conn = get_db()
            save_patient(conn, dlg.patient)
            conn.close()
            self._patient = dlg.patient
            self._update_patient_card()

    def _on_trend(self) -> None:
        if not self._patient or not self._patient.id:
            return
        conn = get_db()
        try:
            ms = get_measurements(conn, self._patient.id)
        finally:
            conn.close()
        if not ms:
            QMessageBox.information(self, "Verlauf", "Noch keine Messungen vorhanden.")
            return
        from ui.trend_dialog import TrendDialog
        TrendDialog(self._patient, ms, parent=self).exec()

    def _on_csv_export(self) -> None:
        all_ms = [m for ms in self._measurements.values() for m in ms] + list(self._orphans)
        if not all_ms:
            QMessageBox.information(self, "Export", "Noch keine Messungen vorhanden.")
            return
        import csv
        from PyQt6.QtWidgets import QFileDialog
        default = f"{self._patient.patient_code}_messungen.csv"
        path, _ = QFileDialog.getSaveFileName(self, "CSV Export", default, "CSV (*.csv)")
        if not path:
            return
        keys: list[str] = []
        for m in all_ms:
            for k in m.features:
                if k not in keys:
                    keys.append(k)
        with open(path, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=["session", "test_type", "hand", "duration_s",
                                               "recorded_at"] + keys)
            wr.writeheader()
            for m in sorted(all_ms, key=lambda x: x.recorded_at):
                row = {"session": m.session_id or "", "test_type": m.test_type, "hand": m.hand,
                       "duration_s": m.duration_s, "recorded_at": m.recorded_at}
                row.update(m.features)
                wr.writerow(row)
        QMessageBox.information(self, "Export", f"{len(all_ms)} Messungen exportiert.")

    def _on_delete_patient(self) -> None:
        if not self._patient or not self._patient.id:
            return
        total = sum(len(v) for v in self._measurements.values()) + len(self._orphans)
        if QMessageBox.question(
                self, "Patient löschen",
                f"Patient '{self._patient.display_name}' wirklich löschen?\n\n"
                f"{len(self._sessions)} Sitzung(en), {total} Messung(en) und alle "
                "zugehörigen Rohdaten werden unwiderruflich gelöscht.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return
        for ms in list(self._measurements.values()) + [self._orphans]:
            for m in ms:
                if m.raw_data_path and Path(m.raw_data_path).exists():
                    try:
                        Path(m.raw_data_path).unlink()
                    except OSError:
                        pass
        conn = get_db()
        delete_patient(conn, self._patient.id)
        conn.close()
        self.main_window.show_patient_screen()

    # ── camera ───────────────────────────────────────────────────
    def _acquire_device(self) -> None:
        if self._device is not None:
            return
        from capture.mediapipe_capture import WebcamSource
        dev = getattr(self.main_window, "capture_device", None)
        if isinstance(dev, WebcamSource) and dev.is_connected() and not dev.replay_path:
            self._device, self._owns_device = dev, False
        else:
            ok, issues = WebcamSource.sidecar_ready()
            if not ok:
                self._set_status(issues[0], True)
                return
            try:
                dev = WebcamSource()
                dev.connect()
                dev.start_tracking(lambda _f: None)
                self._device, self._owns_device = dev, True
            except Exception as e:
                log.warning("Webcam nicht verfügbar: %s", e)
                self._set_status(f"Kamera nicht verfügbar: {e}", True)
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
        self._new_btn.setEnabled(not busy)
        self.tree.setEnabled(not busy)

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status.setStyleSheet(
            f"color: {theme.DANGER if error else theme.TEXT_SECONDARY};")
        self._status.setText(text)
