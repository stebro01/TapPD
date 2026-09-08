"""Patient detail screen: session history as matrix + new session button."""

from collections import defaultdict
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtWidgets import (
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from storage.database import (
    Measurement,
    Patient,
    Session,
    delete_patient,
    delete_session,
    get_db,
    get_session_measurements,
    get_sessions,
    get_measurements,
    save_patient,
)
from video.store import (
    STEP_CONFIRMED,
    STEP_PENDING,
    STEP_RECORDED,
    load_for_patient,
)
from ui.detail_dialog import DetailDialog
from ui.patient_screen import NewPatientDialog
from ui.theme import (SZ, 
    ACCENT, BORDER, CARD_BG, DANGER, PRIMARY, PRIMARY_LIGHT, TEXT, TEXT_SECONDARY)

class PatientDetailScreen(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self._patient: Patient | None = None
        self._sessions: list[Session] = []
        self._session_measurements: dict[int, list[Measurement]] = {}
        # Orphan measurements (no session_id, from before sessions were introduced)
        self._orphan_measurements: list[Measurement] = []
        # Map (row, col) -> list of Measurement for click handling
        # Node payloads keyed by item identity (see _tag).
        self._node_data: dict[int, dict] = {}
        self._video_session = None
        # Map row -> Session (None for orphan rows)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 24, 40, 20)
        layout.setSpacing(16)

        # ── Top bar: back button ──
        top = QHBoxLayout()
        back_btn = QPushButton("← Patienten")
        back_btn.setProperty("cssClass", "flat")
        back_btn.setFixedWidth(140)
        back_btn.setFixedHeight(SZ.BTN_H)
        back_btn.clicked.connect(lambda: self.main_window.show_patient_screen())
        top.addWidget(back_btn)
        top.addStretch()
        layout.addLayout(top)

        # ── Patient info card ──
        self.info_card = QFrame()
        self.info_card.setStyleSheet(
            f"QFrame {{ background: {PRIMARY_LIGHT}; border-radius: 10px; }}"
        )
        card_layout = QHBoxLayout(self.info_card)
        card_layout.setContentsMargins(20, 14, 20, 14)
        card_layout.setSpacing(16)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        self.name_label = QLabel()
        self.name_label.setStyleSheet(
            f"font-size: 17px; font-weight: 700; color: {PRIMARY}; background: transparent;"
        )
        info_col.addWidget(self.name_label)

        self.detail_label = QLabel()
        self.detail_label.setStyleSheet(
            f"font-size: 12px; color: {TEXT_SECONDARY}; background: transparent;"
        )
        info_col.addWidget(self.detail_label)
        card_layout.addLayout(info_col, stretch=1)

        edit_btn = QPushButton("Bearbeiten")
        edit_btn.setFixedWidth(130)
        edit_btn.setFixedHeight(SZ.BTN_H)
        edit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        edit_btn.clicked.connect(self._on_edit_patient)
        card_layout.addWidget(edit_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(self.info_card)

        # ── New session + VideoLab buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()
        self.new_btn = QPushButton("+ Neue Session")
        self.new_btn.setProperty("cssClass", "accent")
        self.new_btn.setFixedHeight(SZ.BTN_H)
        self.new_btn.setFixedWidth(260)
        self.new_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.new_btn.clicked.connect(self._on_new_session)
        btn_row.addWidget(self.new_btn)
        self.video_lab_btn = QPushButton("🎬 VideoLab")
        self.video_lab_btn.setProperty("cssClass", "primary")
        self.video_lab_btn.setFixedHeight(SZ.BTN_H)
        self.video_lab_btn.setFixedWidth(200)
        self.video_lab_btn.setToolTip("Handy-Video hochladen, Bereich wählen und analysieren")
        self.video_lab_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.video_lab_btn.clicked.connect(self._on_video_lab)
        btn_row.addWidget(self.video_lab_btn)
        self.gesture_btn = QPushButton("✋ Gesture Lab")
        self.gesture_btn.setProperty("cssClass", "primary")
        self.gesture_btn.setFixedHeight(SZ.BTN_H)
        self.gesture_btn.setFixedWidth(170)
        self.gesture_btn.setToolTip(
            "Gesten-Batterie für diesen Patienten durchführen und speichern")
        self.gesture_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.gesture_btn.clicked.connect(self._on_gesture_lab)
        btn_row.addWidget(self.gesture_btn)
        self.trend_btn = QPushButton("📈 Verlauf")
        self.trend_btn.setFixedHeight(SZ.BTN_H)
        self.trend_btn.setFixedWidth(160)
        self.trend_btn.setToolTip("Merkmale über die Zeit (alle Messungen dieses Patienten)")
        self.trend_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.trend_btn.clicked.connect(self._on_trend)
        btn_row.addWidget(self.trend_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addSpacing(4)

        # ── Section header ──
        section = QLabel("Sessions")
        section.setProperty("cssClass", "section")
        layout.addWidget(section)

        # ── Session tree ──
        # A tree rather than the old test-per-column matrix: a session's
        # content is no longer a fixed set of test types but whatever it
        # actually holds — recorded protocol steps, their analyses, and
        # measurements taken directly.
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Sitzung / Schritt", "Status", "Ergebnis"])
        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 190)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_tree_double_click)
        self.tree.setStyleSheet(
            "QTreeWidget { border-radius: 8px; }"
            "QTreeWidget::item { padding: 6px 4px; }"
        )
        layout.addWidget(self.tree)

        # ── Count + actions ──
        self.count_label = QLabel()
        self.count_label.setStyleSheet(f"font-size: 12px; color: {TEXT_SECONDARY};")
        layout.addWidget(self.count_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        del_patient_btn = QPushButton("Patient löschen")
        del_patient_btn.setProperty("cssClass", "danger")
        del_patient_btn.setFixedHeight(SZ.BTN_H)
        del_patient_btn.clicked.connect(self._on_delete_patient)
        btn_row.addWidget(del_patient_btn)

        csv_btn = QPushButton("CSV Export")
        csv_btn.setFixedHeight(SZ.BTN_H)
        csv_btn.clicked.connect(self._on_csv_export)
        btn_row.addWidget(csv_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    # ── Patient card ──────────────────────────────────────────────

    def set_patient(self, patient: Patient) -> None:
        self._patient = patient
        self._update_patient_card()
        self.refresh()

    def _update_patient_card(self) -> None:
        p = self._patient
        if not p:
            return
        self.name_label.setText(p.display_name)

        details = []
        if p.age is not None:
            details.append(f"{p.age} Jahre")
        gender_str = {"m": "Männlich", "f": "Weiblich", "d": "Divers"}.get(p.gender)
        if gender_str:
            details.append(gender_str)
        if p.birth_date:
            try:
                parts = p.birth_date.split("-")
                details.append(f"geb. {parts[2]}.{parts[1]}.{parts[0]}")
            except (IndexError, ValueError):
                pass
        if p.notes:
            details.append(p.notes)
        self.detail_label.setText("  ·  ".join(details) if details else "")

    def _on_edit_patient(self) -> None:
        if not self._patient:
            return
        dlg = NewPatientDialog(self, patient=self._patient)
        dlg.code_input.setReadOnly(True)
        dlg.code_input.setStyleSheet("background-color: #F0F0F0; color: #999;")
        dlg.setWindowTitle("Patient bearbeiten")

        if dlg.exec() == QDialog.DialogCode.Accepted:
            conn = get_db()
            save_patient(conn, dlg.patient)
            conn.close()
            self._patient = dlg.patient
            self._update_patient_card()

    # ── Data loading ──────────────────────────────────────────────

    def refresh(self) -> None:
        if not self._patient or not self._patient.id:
            return
        conn = get_db()
        self._sessions = get_sessions(conn, self._patient.id)
        self._session_measurements.clear()
        for s in self._sessions:
            self._session_measurements[s.id] = get_session_measurements(conn, s.id)

        # Orphan measurements (no session_id)
        all_ms = get_measurements(conn, self._patient.id)
        self._orphan_measurements = [m for m in all_ms if m.session_id is None]
        conn.close()
        # The recording attached to this patient, if one exists.
        self._video_session = load_for_patient(self._patient.id,
                                               self._patient.patient_code)
        self._populate()

    # ── Table population ──────────────────────────────────────────

    # -- Tree population -------------------------------------------

    def _video_session_for(self, session_id: int):
        """The recording attached to a session, if any.

        Video sessions are still keyed per patient, so there is at most one.
        It belongs to the session it was exported into; an older one that never
        recorded a `db_session_id` is shown under the newest session -- the
        assignment agreed for the migration.
        """
        vs = self._video_session
        if vs is None or not self._sessions:
            return None
        if vs.db_session_id is not None:
            return vs if vs.db_session_id == session_id else None
        return vs if session_id == self._sessions[0].id else None

    def _populate(self) -> None:
        self.tree.clear()
        self._node_data.clear()

        total = sum(len(ms) for ms in self._session_measurements.values())
        total += len(self._orphan_measurements)

        if not self._sessions and not self._orphan_measurements:
            empty = QTreeWidgetItem(["Noch keine Sitzungen vorhanden", "", ""])
            empty.setForeground(0, QColor(TEXT_SECONDARY))
            self.tree.addTopLevelItem(empty)
            self.count_label.setText("")
            return

        for i, session in enumerate(self._sessions):
            self._add_session_node(session, len(self._sessions) - i)

        if self._orphan_measurements:
            node = QTreeWidgetItem(
                [f"Ohne Sitzung ({len(self._orphan_measurements)})", "", ""])
            node.setForeground(0, QColor(TEXT_SECONDARY))
            self.tree.addTopLevelItem(node)
            for m in self._orphan_measurements:
                node.addChild(self._measurement_node(m))

        n = len(self._sessions)
        self.count_label.setText(
            f"{n} Sitzung{'' if n == 1 else 'en'} \u00b7 "
            f"{total} Messung{'' if total == 1 else 'en'}")

    def _add_session_node(self, session: Session, number: int) -> None:
        measurements = self._session_measurements.get(session.id, [])
        vs = self._video_session_for(session.id)

        if vs is not None and vs.steps:
            done, count = vs.progress
            kind = f"Protokoll: {vs.protocol_name}"
            status = f"{done}/{count} best\u00e4tigt" if done < count else "vollst\u00e4ndig"
        elif vs is not None and vs.video_path:
            kind = "Video (Import)"
            status = f"{len(vs.segments)} Segmente"
        else:
            kind = "Messungen"
            status = f"{len(measurements)} Messungen"

        title = (f"Sitzung {number}  \u00b7  {_format_datetime(session.started_at)}"
                 f"  \u00b7  {kind}")
        node = QTreeWidgetItem([title, status, ""])
        font = node.font(0)
        font.setBold(True)
        node.setFont(0, font)
        self._tag(node, "session", session=session, video_session=vs)
        self.tree.addTopLevelItem(node)

        # Recording steps first -- they are the session's structure.
        used_ids: set = set()
        if vs is not None:
            for i, step in enumerate(vs.steps, start=1):
                node.addChild(self._step_node(i, step, vs, used_ids))

        # Anything measured outside the protocol still belongs to the session.
        for m in measurements:
            if m.id not in used_ids:
                node.addChild(self._measurement_node(m))

        node.setExpanded(True)

    def _step_node(self, number: int, step, vs, used: set) -> QTreeWidgetItem:
        marker = {STEP_PENDING: "\u25cb", STEP_RECORDED: "\u25d0",
                  STEP_CONFIRMED: "\u2714"}
        state_text = {STEP_PENDING: "offen",
                      STEP_RECORDED: "aufgenommen, ungesichtet",
                      STEP_CONFIRMED: "Video \u2714"}

        # A confirmed step owns a segment; its analysis (if run) lives there.
        segment = next((sg for sg in vs.segments if sg.id == step.segment_id), None)
        if step.is_documentation:
            result = "Dokumentation"
        elif segment is None or not segment.analyzed:
            result = "\u2014" if step.state == STEP_PENDING else "nicht ausgewertet"
        else:
            result = self._result_summary(segment, used)

        label = f"{marker.get(step.state, chr(9675))}  {number}. {step.title}"
        if not step.is_documentation:
            label += f"  ({step.hand})"
        item = QTreeWidgetItem([label, state_text.get(step.state, step.state), result])
        if step.state == STEP_CONFIRMED:
            item.setForeground(1, QColor(ACCENT))
        self._tag(item, "step", video_session=vs, step=step, segment=segment)
        return item

    def _result_summary(self, segment, used: set) -> str:
        """Short readout of a segment's analysis, marking its measurement used."""
        parts = []
        for key, res in (segment.results or {}).items():
            features = (res or {}).get("features") or {}
            mpi = features.get("mpi")
            label = _paradigm_label(key)
            parts.append(f"{label}: MPI {mpi:.2f}"
                         if isinstance(mpi, (int, float)) else label)
            mid = (res or {}).get("measurement_id")
            if mid is not None:
                used.add(mid)
        return "  \u00b7  ".join(parts) or "ausgewertet"

    def _measurement_node(self, m: Measurement) -> QTreeWidgetItem:
        mpi = (m.features or {}).get("mpi")
        result = f"MPI {mpi:.2f}" if isinstance(mpi, (int, float)) else ""
        item = QTreeWidgetItem(
            [f"    {_paradigm_label(m.test_type)}  ({m.hand})", "Messung", result])
        self._tag(item, "measurement", measurement=m)
        return item

    def _tag(self, item: QTreeWidgetItem, kind: str, **data) -> None:
        """Attach node data by identity -- dataclasses do not survive QVariant."""
        key = id(item)
        self._node_data[key] = {"kind": kind, **data}
        item.setData(0, Qt.ItemDataRole.UserRole, key)

    def _node(self, item) -> dict:
        if item is None:
            return {}
        return self._node_data.get(item.data(0, Qt.ItemDataRole.UserRole), {})

    # -- Context actions -------------------------------------------

    def _actions_for(self, node: dict) -> list:
        """Actions for a node as data: (label, callback, css).

        Deliberately a list rather than menu-building code, so the touch action
        panel can render exactly this when it returns, instead of the logic
        existing twice.
        """
        kind = node.get("kind")
        out = []

        if kind == "session":
            session = node.get("session")
            vs = node.get("video_session")
            if vs is not None and vs.steps and not vs.is_complete:
                out.append(("\u25cf Aufnahme fortsetzen",
                            lambda: self._open_recording(vs), "primary"))
            else:
                out.append(("\u25cf Aufnahme starten\u2026",
                            self._on_video_lab, "primary"))
            out.append(("+ Messung hinzuf\u00fcgen\u2026",
                        lambda: self._add_measurement_dialog(session), ""))
            out.append(("Im VideoLab \u00f6ffnen", self._on_video_lab, ""))
            out.append(("Sitzung l\u00f6schen",
                        lambda: self._delete_session(session), "danger"))

        elif kind == "step":
            vs, step = node.get("video_session"), node.get("step")
            segment = node.get("segment")
            if step.state == STEP_CONFIRMED:
                out.append(("\u21bb Erneut aufnehmen",
                            lambda: self._retake(vs, step), ""))
            else:
                out.append(("\u25cf Aufnehmen",
                            lambda: self._open_recording(vs), "primary"))
            if segment is not None and not step.is_documentation:
                out.append(("Auswerten / im VideoLab \u00f6ffnen",
                            self._on_video_lab, ""))

        elif kind == "measurement":
            m = node.get("measurement")
            out.append(("Details\u2026", lambda: self._show_measurement(m), ""))

        return out

    def _on_tree_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        actions = self._actions_for(self._node(item))
        if not actions:
            return
        menu = QMenu(self)
        for label, callback, _css in actions:
            act = menu.addAction(label)
            act.triggered.connect(lambda _checked=False, cb=callback: cb())
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _on_tree_double_click(self, item, _column: int) -> None:
        """Double-click runs a node's first (primary) action."""
        actions = self._actions_for(self._node(item))
        if actions:
            actions[0][1]()

    # -- Action helpers --------------------------------------------

    def _open_recording(self, vs) -> None:
        if vs is None:
            self._on_video_lab()
            return
        self.main_window.show_recording(vs)

    def _retake(self, vs, step) -> None:
        vs.retake_step(step.id)
        vs.save()
        self.refresh()
        self.main_window.show_recording(vs)

    def _show_measurement(self, m: Measurement) -> None:
        DetailDialog(self, m).exec()

    def _add_measurement_dialog(self, session: Session) -> None:
        """Pick a paradigm + hand and record it into this session."""
        from paradigms import registry

        dlg = QDialog(self)
        dlg.setWindowTitle("Messung hinzuf\u00fcgen")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Paradigma und Seite w\u00e4hlen:"))
        row = QHBoxLayout()
        para = QComboBox()
        for key in registry.all_keys():
            para.addItem(registry.get(key).label.replace(chr(10), " "), key)
        row.addWidget(para, 1)
        hand = QComboBox()
        for label, value in (("rechts", "right"), ("links", "left"),
                             ("beide", "both")):
            hand.addItem(label, value)
        row.addWidget(hand)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._add_measurement_to_session(session, para.currentData(),
                                         hand.currentData())

    def _add_measurement_to_session(self, session: Session, test_key: str, hand: str) -> None:
        """Resume the session and start the test."""
        self.main_window.resume_session(session)
        self.main_window.dashboard.set_patient(self._patient)
        self.main_window.start_test(test_key, hand, self.main_window.dashboard.duration_spin.value())


    # ── Actions ───────────────────────────────────────────────────

    def _on_new_session(self) -> None:
        if self._patient:
            self.main_window.start_new_session()

    def _on_video_lab(self) -> None:
        if self._patient:
            self.main_window.current_patient = self._patient
            self.main_window.show_video_lab()

    def _on_gesture_lab(self) -> None:
        if self._patient:
            self.main_window.current_patient = self._patient
            self.main_window.show_gesture_lab("detail")

    def _on_trend(self) -> None:
        if not self._patient or not self._patient.id:
            return
        conn = get_db()
        try:
            measurements = get_measurements(conn, self._patient.id)
        finally:
            conn.close()
        if not measurements:
            QMessageBox.information(self, "Verlauf", "Noch keine Messungen vorhanden.")
            return
        from ui.trend_dialog import TrendDialog
        TrendDialog(self._patient, measurements, parent=self).exec()

    def _on_delete_patient(self) -> None:
        """Delete the entire patient with all sessions and measurements."""
        if not self._patient or not self._patient.id:
            return
        total = sum(len(ms) for ms in self._session_measurements.values())
        total += len(self._orphan_measurements)
        reply = QMessageBox.question(
            self,
            "Patient löschen",
            f"Patient '{self._patient.display_name}' wirklich löschen?\n\n"
            f"{len(self._sessions)} Session(s), {total} Messung(en) und "
            f"alle zugehörigen Rohdaten werden unwiderruflich gelöscht.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Delete raw JSON files
        for ms in self._session_measurements.values():
            for m in ms:
                if m.raw_data_path:
                    raw = Path(m.raw_data_path)
                    if raw.exists():
                        raw.unlink()
        for m in self._orphan_measurements:
            if m.raw_data_path:
                raw = Path(m.raw_data_path)
                if raw.exists():
                    raw.unlink()
        conn = get_db()
        delete_patient(conn, self._patient.id)
        conn.close()
        self.main_window.show_patient_screen()

    def _delete_session(self, session: Session) -> None:
        """Delete a specific session with confirmation."""
        ms = self._session_measurements.get(session.id, [])
        n = len(ms)
        date_str = _format_datetime(session.started_at)

        reply = QMessageBox.question(
            self,
            "Session löschen",
            f"Session vom {date_str} löschen?\n\n"
            f"Enthält {n} Messung{'en' if n != 1 else ''}.\n"
            "Alle Messungen und Rohdaten dieser Session werden gelöscht.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Delete raw JSON files
        for m in ms:
            if m.raw_data_path:
                raw = Path(m.raw_data_path)
                if raw.exists():
                    raw.unlink()

        conn = get_db()
        delete_session(conn, session.id)
        conn.close()
        self.refresh()

    def _on_csv_export(self) -> None:
        all_ms = []
        for ms in self._session_measurements.values():
            all_ms.extend(ms)
        all_ms.extend(self._orphan_measurements)

        if not all_ms:
            return

        from PyQt6.QtWidgets import QFileDialog
        import csv

        default = f"{self._patient.patient_code}_messungen.csv"
        path, _ = QFileDialog.getSaveFileName(self, "CSV Export", default, "CSV (*.csv)")
        if not path:
            return

        all_keys: list[str] = []
        for m in all_ms:
            for k in m.features:
                if k not in all_keys:
                    all_keys.append(k)

        with open(path, "w", newline="") as f:
            header = ["session", "test_type", "hand", "duration_s", "recorded_at"] + all_keys
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            for m in sorted(all_ms, key=lambda x: x.recorded_at):
                row = {
                    "session": m.session_id or "",
                    "test_type": m.test_type,
                    "hand": m.hand,
                    "duration_s": m.duration_s,
                    "recorded_at": m.recorded_at,
                }
                row.update(m.features)
                writer.writerow(row)

        QMessageBox.information(
            self, "Export", f"{len(all_ms)} Messungen exportiert."
        )


# ── Helpers ───────────────────────────────────────────────────────

def _format_datetime(iso: str) -> str:
    """'2026-03-10T14:30:00' → '10.03.2026  14:30'"""
    if not iso:
        return "–"
    try:
        parts = iso[:10].split("-")
        time_part = iso[11:16] if len(iso) > 16 else ""
        nice = f"{parts[2]}.{parts[1]}.{parts[0]}"
        if time_part:
            nice += f"  {time_part}"
        return nice
    except (IndexError, ValueError):
        return iso[:16]


def _format_date(iso_date: str) -> str:
    """'2026-03-10' → '10.03.2026'"""
    try:
        parts = iso_date.split("-")
        return f"{parts[2]}.{parts[1]}.{parts[0]}"
    except (IndexError, ValueError):
        return iso_date


def _paradigm_label(key: str) -> str:
    """Human-readable name for a paradigm key, falling back to the key."""
    try:
        from paradigms import registry
        return (registry.get(key).label or key).replace("\n", " ")
    except Exception:
        return key
