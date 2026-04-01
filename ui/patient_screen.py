"""Patient selection/creation screen."""

from pathlib import Path

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from storage.database import Patient, find_patients, get_db, get_last_measurement_dates, save_patient
from ui.theme import SZ


class NewPatientDialog(QDialog):
    def __init__(self, parent=None, patient: Patient | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Neuer Patient" if patient is None else "Patient bearbeiten")
        self.setMinimumWidth(460)
        self.patient = patient or Patient()

        layout = QFormLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        self.code_input = QLineEdit(self.patient.patient_code)
        self.code_input.setPlaceholderText("z.B. PD001 (Pflichtfeld)")
        layout.addRow("Patienten-ID", self.code_input)

        self.last_name_input = QLineEdit(self.patient.last_name)
        layout.addRow("Nachname", self.last_name_input)

        self.first_name_input = QLineEdit(self.patient.first_name)
        layout.addRow("Vorname", self.first_name_input)

        self.birth_date_input = QDateEdit()
        self.birth_date_input.setCalendarPopup(True)
        self.birth_date_input.setDisplayFormat("dd.MM.yyyy")
        self.birth_date_input.setSpecialValueText("–")
        if self.patient.birth_date:
            self.birth_date_input.setDate(QDate.fromString(self.patient.birth_date, "yyyy-MM-dd"))
        else:
            self.birth_date_input.setDate(QDate(1950, 1, 1))
        layout.addRow("Geburtsdatum", self.birth_date_input)

        self.gender_combo = QComboBox()
        self.gender_combo.addItems(["–", "Maennlich", "Weiblich", "Divers"])
        gender_map = {"m": 1, "f": 2, "d": 3}
        self.gender_combo.setCurrentIndex(gender_map.get(self.patient.gender, 0))
        layout.addRow("Geschlecht", self.gender_combo)

        self.notes_input = QLineEdit(self.patient.notes)
        self.notes_input.setPlaceholderText("Optional")
        layout.addRow("Notizen", self.notes_input)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        for btn in buttons.buttons():
            btn.setFixedHeight(SZ.BTN_H)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _on_accept(self) -> None:
        code = self.code_input.text().strip()
        if not code:
            self.code_input.setFocus()
            return
        self.patient.patient_code = code
        self.patient.first_name = self.first_name_input.text().strip()
        self.patient.last_name = self.last_name_input.text().strip()
        self.patient.birth_date = self.birth_date_input.date().toString("yyyy-MM-dd")
        gender_map = {1: "m", 2: "f", 3: "d"}
        self.patient.gender = gender_map.get(self.gender_combo.currentIndex(), "")
        self.patient.notes = self.notes_input.text().strip()
        self.accept()


class PatientScreen(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(50, 24, 50, 24)
        layout.setSpacing(0)

        # ── Top bar: Beenden (left) + Ueber TapPD (right) ──
        top_bar = QHBoxLayout()
        quit_btn = QPushButton("Beenden")
        quit_btn.setFixedWidth(120)
        quit_btn.setFixedHeight(SZ.BTN_H)
        quit_btn.clicked.connect(self.main_window.close)
        top_bar.addWidget(quit_btn)

        top_bar.addStretch()

        about_btn = QPushButton("Ueber TapPD")
        about_btn.setFixedWidth(160)
        about_btn.setFixedHeight(SZ.BTN_H)
        about_btn.clicked.connect(self._on_about)
        top_bar.addWidget(about_btn)
        layout.addLayout(top_bar)

        # ── Upper 1/3: Title + subtitle ──
        layout.addStretch(1)

        title = QLabel("TapPD")
        title.setProperty("cssClass", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Kontaktlose Motorik-Analyse")
        subtitle.setProperty("cssClass", "subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(30)

        # ── Lower 2/3: Search + list ──
        # Search row
        search_row = QHBoxLayout()
        search_row.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Patient suchen...")
        self.search_input.setFixedHeight(SZ.INPUT_H)
        self.search_input.textChanged.connect(self._on_search)
        search_row.addWidget(self.search_input)

        self.new_button = QPushButton("+ Neuer Patient")
        self.new_button.setProperty("cssClass", "accent")
        self.new_button.setFixedWidth(180)
        self.new_button.setFixedHeight(SZ.BTN_H)
        self.new_button.clicked.connect(self._on_new_patient)
        search_row.addWidget(self.new_button)

        layout.addLayout(search_row)
        layout.addSpacing(10)

        # Patient table — sortable columns
        self.patient_table = QTableWidget()
        self.patient_table.setColumnCount(6)
        self.patient_table.setHorizontalHeaderLabels(
            ["ID", "Nachname", "Vorname", "Alter", "Geschlecht", "Letzte Messung"]
        )
        self.patient_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.patient_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.patient_table.setColumnWidth(0, 80)
        self.patient_table.setColumnWidth(3, 60)
        self.patient_table.setColumnWidth(4, 90)
        self.patient_table.setColumnWidth(5, 150)
        self.patient_table.verticalHeader().setVisible(False)
        self.patient_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.patient_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.patient_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.patient_table.setSortingEnabled(True)
        self.patient_table.setMinimumHeight(280)
        self.patient_table.verticalHeader().setDefaultSectionSize(SZ.ROW_H)
        self.patient_table.clicked.connect(self._on_select)
        layout.addWidget(self.patient_table, stretch=3)

        layout.addSpacing(10)

        # Select button
        select_btn = QPushButton("Auswaehlen")
        select_btn.setProperty("cssClass", "primary")
        select_btn.setFixedWidth(180)
        select_btn.setFixedHeight(SZ.BTN_H)
        select_btn.clicked.connect(self._on_select)
        layout.addWidget(select_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch(1)

        # Copyright
        copy_label = QLabel("\u00a9 Stefan Brodoehl 2026")
        copy_label.setStyleSheet("font-size: 10px; color: #BDBDBD;")
        layout.addWidget(copy_label, alignment=Qt.AlignmentFlag.AlignRight)

        self.refresh_list()

    def refresh_list(self, query: str = "") -> None:
        self.patient_table.setSortingEnabled(False)
        self.patient_table.setRowCount(0)

        conn = get_db()
        patients = find_patients(conn, query)
        last_dates = get_last_measurement_dates(conn)
        conn.close()

        self.patient_table.setRowCount(len(patients))
        for row, p in enumerate(patients):
            # ID
            id_item = QTableWidgetItem(p.patient_code)
            id_item.setData(Qt.ItemDataRole.UserRole, p.id)
            self.patient_table.setItem(row, 0, id_item)

            # Nachname
            self.patient_table.setItem(row, 1, QTableWidgetItem(p.last_name))

            # Vorname
            self.patient_table.setItem(row, 2, QTableWidgetItem(p.first_name))

            # Alter — numeric sort via SortRole
            age_item = QTableWidgetItem()
            if p.age is not None:
                age_item.setText(str(p.age))
                age_item.setData(Qt.ItemDataRole.UserRole + 1, p.age)
            else:
                age_item.setText("–")
                age_item.setData(Qt.ItemDataRole.UserRole + 1, -1)
            age_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.patient_table.setItem(row, 3, age_item)

            # Geschlecht
            gender_str = {"m": "M", "f": "F", "d": "D"}.get(p.gender, "–")
            g_item = QTableWidgetItem(gender_str)
            g_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.patient_table.setItem(row, 4, g_item)

            # Letzte Messung — sort by ISO string (newest first)
            last = last_dates.get(p.id, "")
            date_item = _SortableDateItem(last)
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.patient_table.setItem(row, 5, date_item)

        self.patient_table.setSortingEnabled(True)
        # Default sort: last measurement descending (newest first)
        self.patient_table.sortItems(5, Qt.SortOrder.DescendingOrder)

    def _on_search(self, text: str) -> None:
        self.refresh_list(text)

    def _on_new_patient(self) -> None:
        dialog = NewPatientDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            conn = get_db()
            save_patient(conn, dialog.patient)
            conn.close()
            self.refresh_list()
            self.main_window.select_patient(dialog.patient)

    def _on_about(self) -> None:
        about_path = Path(__file__).parent.parent / "ABOUT.md"
        try:
            md_text = about_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            md_text = "# TapPD\n\nKontaktlose Motorik-Analyse"

        dlg = QDialog(self)
        dlg.setWindowTitle("Ueber TapPD")
        dlg.setMinimumSize(560, 480)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 24, 24, 24)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setMarkdown(md_text)
        browser.setStyleSheet(
            "QTextBrowser {"
            "  background-color: #FAFAFA;"
            "  border: 1px solid #E0E0E0;"
            "  border-radius: 8px;"
            "  padding: 16px;"
            "  font-size: 13px;"
            "}"
        )
        layout.addWidget(browser)

        close_btn = QPushButton("Schliessen")
        close_btn.setProperty("cssClass", "primary")
        close_btn.setFixedHeight(SZ.BTN_H)
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        dlg.exec()

    def _on_select(self) -> None:
        row = self.patient_table.currentRow()
        if row < 0:
            return
        id_item = self.patient_table.item(row, 0)
        if id_item is None:
            return
        patient_id = id_item.data(Qt.ItemDataRole.UserRole)
        conn = get_db()
        from storage.database import get_patient
        patient = get_patient(conn, patient_id)
        conn.close()
        if patient:
            self.main_window.select_patient(patient)


class _SortableDateItem(QTableWidgetItem):
    """Table item that displays a formatted date but sorts by ISO string."""

    def __init__(self, iso_date: str) -> None:
        super().__init__()
        self._sort_key = iso_date or ""
        if iso_date:
            # Display as "DD.MM.YYYY HH:MM"
            try:
                parts = iso_date[:10].split("-")
                time_part = iso_date[11:16] if len(iso_date) > 16 else ""
                nice = f"{parts[2]}.{parts[1]}.{parts[0]}"
                if time_part:
                    nice += f"  {time_part}"
                self.setText(nice)
            except (IndexError, ValueError):
                self.setText(iso_date[:16])
        else:
            self.setText("–")

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _SortableDateItem):
            return self._sort_key < other._sort_key
        return super().__lt__(other)
