"""Patient detail screen: session history as matrix + new session button."""

from collections import defaultdict
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QCursor, QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from storage.database import (
    Measurement,
    Patient,
    Session,
    delete_session,
    get_db,
    get_session_measurements,
    get_sessions,
    get_measurements,
    save_patient,
)
from ui.detail_dialog import DetailDialog
from ui.patient_screen import NewPatientDialog
from ui.theme import ACCENT, BORDER, PRIMARY, PRIMARY_LIGHT, TEXT_SECONDARY

# Column definitions for the session matrix
_TEST_COLS = [
    ("finger_tapping", "Finger\nTapping", False),
    ("hand_open_close", "Hand\nauf/zu", False),
    ("pronation_supination", "Pro-/Supi-\nnation", False),
    ("postural_tremor", "Posturaler\nTremor", True),
    ("rest_tremor", "Ruhe-\ntremor", True),
    ("tower_of_hanoi", "Tuerme v.\nHanoi", False),
    ("spatial_srt", "Raeumliche\nReaktion", False),
    ("trail_making_a", "TMT\nTeil A", False),
    ("trail_making_b", "TMT\nTeil B", False),
]


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
        self._cell_map: dict[tuple[int, int], list[Measurement]] = {}
        # Map row -> Session (None for orphan rows)
        self._row_session: dict[int, Session | None] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 24, 40, 20)
        layout.setSpacing(16)

        # ── Top bar: back button ──
        top = QHBoxLayout()
        back_btn = QPushButton("← Patienten")
        back_btn.setProperty("cssClass", "flat")
        back_btn.setFixedWidth(130)
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
        edit_btn.setFixedWidth(110)
        edit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        edit_btn.clicked.connect(self._on_edit_patient)
        card_layout.addWidget(edit_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(self.info_card)

        # ── New session button ──
        self.new_btn = QPushButton("+ Neue Session")
        self.new_btn.setProperty("cssClass", "accent")
        self.new_btn.setFixedHeight(44)
        self.new_btn.setFixedWidth(260)
        self.new_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.new_btn.clicked.connect(self._on_new_session)
        layout.addWidget(self.new_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addSpacing(4)

        # ── Section header ──
        section = QLabel("Sessions")
        section.setProperty("cssClass", "section")
        layout.addWidget(section)

        # ── Session matrix table ──
        self.table = QTableWidget()
        self.table.setColumnCount(2 + len(_TEST_COLS))  # # + Datum + test columns
        headers = ["#", "Datum"] + [label for _, label, _ in _TEST_COLS]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setColumnWidth(0, 32)
        self.table.setColumnWidth(1, 180)  # Datum
        for i in range(2, self.table.columnCount()):
            self.table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeMode.Stretch
            )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self._on_cell_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.setStyleSheet(
            "QTableWidget { border-radius: 8px; }"
            "QTableWidget::item { padding: 8px; }"
        )
        layout.addWidget(self.table)

        # ── Count + actions ──
        self.count_label = QLabel()
        self.count_label.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY};")
        layout.addWidget(self.count_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        self.delete_btn = QPushButton("Session loeschen")
        self.delete_btn.clicked.connect(self._on_delete_session)
        btn_row.addWidget(self.delete_btn)

        csv_btn = QPushButton("CSV Export")
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
        gender_str = {"m": "Maennlich", "f": "Weiblich", "d": "Divers"}.get(p.gender)
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
        self._populate()

    # ── Table population ──────────────────────────────────────────

    def _populate(self) -> None:
        self._cell_map.clear()
        self._row_session.clear()
        self.table.setRowCount(0)

        total_measurements = sum(len(ms) for ms in self._session_measurements.values())
        total_measurements += len(self._orphan_measurements)

        if not self._sessions and not self._orphan_measurements:
            self.table.setRowCount(1)
            empty = QTableWidgetItem("Noch keine Messungen vorhanden")
            empty.setForeground(QColor(TEXT_SECONDARY))
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(0, 0, empty)
            self.table.setSpan(0, 0, 1, self.table.columnCount())
            self.count_label.setText("")
            self.delete_btn.setEnabled(False)
            return

        self.delete_btn.setEnabled(True)

        # Build rows: sessions first, then orphan groups
        rows_data: list[tuple[str, str, list[Measurement], Session | None]] = []

        # Sessions (already sorted DESC by started_at)
        for i, s in enumerate(self._sessions):
            ms = self._session_measurements.get(s.id, [])
            num = str(len(self._sessions) - i)
            date_str = _format_datetime(s.started_at)
            rows_data.append((num, date_str, ms, s))

        # Orphan measurements grouped by date
        if self._orphan_measurements:
            orphan_by_date: dict[str, list[Measurement]] = defaultdict(list)
            for m in self._orphan_measurements:
                orphan_by_date[m.recorded_at[:10]].append(m)
            for date_key in sorted(orphan_by_date.keys(), reverse=True):
                ms = orphan_by_date[date_key]
                date_str = _format_date(date_key)
                rows_data.append(("–", date_str, ms, None))

        self.table.setRowCount(len(rows_data))

        for row, (num, date_str, ms_list, session) in enumerate(rows_data):
            self._row_session[row] = session

            # Session number
            num_item = QTableWidgetItem(num)
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            num_item.setForeground(QColor(TEXT_SECONDARY))
            self.table.setItem(row, 0, num_item)

            # Date
            date_item = QTableWidgetItem(date_str)
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            font = date_item.font()
            font.setWeight(QFont.Weight.DemiBold)
            date_item.setFont(font)
            self.table.setItem(row, 1, date_item)

            # Index measurements by test_type
            by_test: dict[str, list[Measurement]] = defaultdict(list)
            for m in ms_list:
                by_test[m.test_type].append(m)

            # Fill test columns
            for col_idx, (test_key, _, bilateral) in enumerate(_TEST_COLS):
                col = col_idx + 2  # offset by # and date columns
                ms_for_cell = by_test.get(test_key, [])

                if not ms_for_cell:
                    item = QTableWidgetItem("·")
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setForeground(QColor("#D0D0D0"))
                    if session:
                        item.setToolTip("Rechtsklick um Messung hinzuzufuegen")
                    self.table.setItem(row, col, item)
                    continue

                # Build cell text
                if bilateral:
                    cell_text = "✓"
                else:
                    hands = sorted(set(m.hand for m in ms_for_cell))
                    parts = ["R" if h == "right" else "L" for h in hands]
                    cell_text = " ".join(parts)

                item = QTableWidgetItem(cell_text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(QColor(ACCENT))
                font = item.font()
                font.setWeight(QFont.Weight.Bold)
                item.setFont(font)

                # For unilateral tests: tooltip if only one hand done
                if session and not bilateral:
                    existing_hands = set(m.hand for m in ms_for_cell)
                    if len(existing_hands) < 2:
                        missing = "Links" if "left" not in existing_hands else "Rechts"
                        item.setToolTip(f"Rechtsklick um {missing} hinzuzufuegen")
                    else:
                        item.setToolTip("Klicken fuer Details")
                else:
                    item.setToolTip("Klicken fuer Details")

                self.table.setItem(row, col, item)
                self._cell_map[(row, col)] = ms_for_cell

        n_sessions = len(self._sessions)
        self.count_label.setText(
            f"{n_sessions} Session{'s' if n_sessions != 1 else ''}, "
            f"{total_measurements} Messung{'en' if total_measurements != 1 else ''}"
        )

    # ── Cell click → detail ───────────────────────────────────────

    def _on_cell_click(self, row: int, col: int) -> None:
        ms = self._cell_map.get((row, col))
        if not ms:
            return
        # Open detail with all siblings — L/R switcher if multiple
        dlg = DetailDialog(self._patient, ms[0], siblings=ms, parent=self)
        dlg.exec()

    def _on_context_menu(self, pos) -> None:
        """Right-click context menu: add measurement or delete session."""
        item = self.table.itemAt(pos)
        if not item:
            return
        row = item.row()
        col = item.column()

        session = self._row_session.get(row)
        if not session:
            return

        menu = QMenu(self)

        # On # or Datum columns (or any column): offer session delete
        if col < 2:
            del_action = menu.addAction("Session loeschen")
            del_action.triggered.connect(lambda: self._delete_session(session))
            menu.exec(self.table.viewport().mapToGlobal(pos))
            return

        # On test columns: offer add measurement + session delete
        col_idx = col - 2
        if col_idx < 0 or col_idx >= len(_TEST_COLS):
            return
        test_key, _, bilateral = _TEST_COLS[col_idx]

        existing = self._cell_map.get((row, col), [])
        existing_hands = set(m.hand for m in existing)

        if bilateral:
            if "both" not in existing_hands:
                a = menu.addAction("Messung hinzufuegen")
                a.triggered.connect(
                    lambda: self._add_measurement_to_session(session, test_key, "both"))
        else:
            if "left" not in existing_hands:
                a = menu.addAction("Links hinzufuegen")
                a.triggered.connect(
                    lambda: self._add_measurement_to_session(session, test_key, "left"))
            if "right" not in existing_hands:
                a = menu.addAction("Rechts hinzufuegen")
                a.triggered.connect(
                    lambda: self._add_measurement_to_session(session, test_key, "right"))

        if not menu.isEmpty():
            menu.addSeparator()
        del_action = menu.addAction("Session loeschen")
        del_action.triggered.connect(lambda: self._delete_session(session))

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _add_measurement_to_session(self, session: Session, test_key: str, hand: str) -> None:
        """Resume the session and start the test."""
        self.main_window.resume_session(session)
        self.main_window.dashboard.set_patient(self._patient)
        self.main_window.start_test(test_key, hand, self.main_window.dashboard.duration_spin.value())


    # ── Actions ───────────────────────────────────────────────────

    def _on_new_session(self) -> None:
        if self._patient:
            self.main_window.start_new_session()

    def _on_delete_session(self) -> None:
        """Delete the most recent session (button action)."""
        if not self._sessions:
            return
        self._delete_session(self._sessions[0])

    def _delete_session(self, session: Session) -> None:
        """Delete a specific session with confirmation."""
        ms = self._session_measurements.get(session.id, [])
        n = len(ms)
        date_str = _format_datetime(session.started_at)

        reply = QMessageBox.question(
            self,
            "Session loeschen",
            f"Session vom {date_str} loeschen?\n\n"
            f"Enthaelt {n} Messung{'en' if n != 1 else ''}.\n"
            "Alle Messungen und Rohdaten dieser Session werden geloescht.",
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
