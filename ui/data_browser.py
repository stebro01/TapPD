"""Data browser: view, filter, delete, and export past measurements."""

import csv
import shutil
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from storage.database import delete_measurement, get_all_measurements, get_db, Measurement, Patient
from ui.detail_dialog import DetailDialog
from ui.theme import SZ, TEXT_SECONDARY


class DataBrowser(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self._data: list[tuple[Patient, Measurement]] = []
        self._filtered: list[tuple[Patient, Measurement]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 24, 40, 20)
        layout.setSpacing(14)

        title = QLabel("Daten-Browser")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filtern nach Patient, Test, Datum...")
        self.filter_input.setMaximumWidth(500)
        self.filter_input.setFixedHeight(SZ.INPUT_H)
        self.filter_input.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_input, alignment=Qt.AlignmentFlag.AlignCenter)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["Patient", "Test", "Hand", "Dauer", "Datum", "Features", "JSON", ""]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(6, 50)
        self.table.setColumnHidden(7, True)  # hidden ID column
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(SZ.ROW_H)
        self.table.itemClicked.connect(self._on_detail)
        layout.addWidget(self.table)

        # Count
        self.count_label = QLabel()
        self.count_label.setStyleSheet(f"font-size: 12px; color: {TEXT_SECONDARY};")
        layout.addWidget(self.count_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        self.delete_btn = QPushButton("Eintrag löschen")
        self.delete_btn.setFixedHeight(SZ.BTN_H)
        self.delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self.delete_btn)

        self.detail_btn = QPushButton("Details")
        self.detail_btn.setFixedHeight(SZ.BTN_H)
        self.detail_btn.clicked.connect(self._on_detail)
        btn_row.addWidget(self.detail_btn)

        self.csv_single_btn = QPushButton("CSV exportieren")
        self.csv_single_btn.setFixedHeight(SZ.BTN_H)
        self.csv_single_btn.clicked.connect(self._on_export_single_csv)
        btn_row.addWidget(self.csv_single_btn)

        self.json_btn = QPushButton("JSON exportieren")
        self.json_btn.setFixedHeight(SZ.BTN_H)
        self.json_btn.clicked.connect(self._on_export_json)
        btn_row.addWidget(self.json_btn)

        self._selection_buttons = [self.delete_btn, self.detail_btn, self.csv_single_btn, self.json_btn]
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._update_buttons()

        csv_all_btn = QPushButton("Alle als CSV")
        csv_all_btn.setFixedHeight(SZ.BTN_H)
        csv_all_btn.clicked.connect(self._on_export_all)
        btn_row.addWidget(csv_all_btn)

        back_btn = QPushButton("Zurück")
        back_btn.setProperty("cssClass", "primary")
        back_btn.setFixedHeight(SZ.BTN_H)
        back_btn.clicked.connect(lambda: self.main_window.show_patient_screen())
        btn_row.addWidget(back_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _update_buttons(self) -> None:
        has_sel = self.table.currentRow() >= 0
        for btn in self._selection_buttons:
            btn.setEnabled(has_sel)

    def refresh(self) -> None:
        conn = get_db()
        self._data = get_all_measurements(conn)
        conn.close()
        self._filtered = list(self._data)
        self._populate(self._filtered)

    def _populate(self, data: list[tuple[Patient, Measurement]]) -> None:
        self.table.setRowCount(len(data))
        for row, (p, m) in enumerate(data):
            self.table.setItem(row, 0, QTableWidgetItem(p.display_name))
            self.table.setItem(row, 1, QTableWidgetItem(m.test_type))
            self.table.setItem(row, 2, QTableWidgetItem(m.hand))
            self.table.setItem(row, 3, QTableWidgetItem(f"{m.duration_s:.0f}s"))
            self.table.setItem(row, 4, QTableWidgetItem(m.recorded_at[:16]))
            features = m.features
            summary = ", ".join(f"{k}: {v:.2f}" for k, v in list(features.items())[:3])
            if len(features) > 3:
                summary += " ..."
            self.table.setItem(row, 5, QTableWidgetItem(summary))

            # JSON indicator
            has_json = bool(m.raw_data_path and Path(m.raw_data_path).exists())
            json_item = QTableWidgetItem("J" if has_json else "")
            json_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if has_json:
                json_item.setForeground(QColor("#4CAF50"))
                json_item.setToolTip(m.raw_data_path)
            else:
                json_item.setForeground(QColor("#BDBDBD"))
                json_item.setToolTip("Keine Rohdaten")
            self.table.setItem(row, 6, json_item)

            self.table.setItem(row, 7, QTableWidgetItem(str(m.id)))
        self.count_label.setText(f"{len(data)} Messungen")
        self._update_buttons()

    def _apply_filter(self, text: str) -> None:
        q = text.lower()
        if not q:
            self._filtered = list(self._data)
        else:
            self._filtered = [
                (p, m) for p, m in self._data
                if q in p.display_name.lower() or q in m.test_type.lower()
                or q in m.recorded_at.lower() or q in m.hand.lower()
            ]
        self._populate(self._filtered)

    def _selected_measurement(self) -> tuple[Patient, Measurement] | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        mid = int(self.table.item(row, 7).text())
        for p, m in self._data:
            if m.id == mid:
                return p, m
        return None

    def _on_detail(self) -> None:
        sel = self._selected_measurement()
        if not sel:
            return
        p, m = sel
        dlg = DetailDialog(p, m, parent=self)
        dlg.exec()

    def _on_delete(self) -> None:
        sel = self._selected_measurement()
        if not sel:
            return
        p, m = sel
        reply = QMessageBox.question(
            self, "Eintrag löschen",
            f"Messung wirklich löschen?\n\n{p.display_name} – {m.test_type} ({m.hand})\n{m.recorded_at}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        conn = get_db()
        delete_measurement(conn, m.id)
        conn.close()
        # Also delete raw JSON if exists
        if m.raw_data_path:
            raw = Path(m.raw_data_path)
            if raw.exists():
                raw.unlink()
        self.refresh()

    def _on_export_single_csv(self) -> None:
        sel = self._selected_measurement()
        if not sel:
            return
        p, m = sel
        default_name = f"{p.patient_code}_{m.test_type}_{m.hand}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "CSV exportieren", default_name, "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            keys = list(m.features.keys())
            header = ["patient_code", "patient_name", "test_type", "hand", "duration_s", "recorded_at"] + keys
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            row = {
                "patient_code": p.patient_code, "patient_name": p.display_name,
                "test_type": m.test_type, "hand": m.hand,
                "duration_s": m.duration_s, "recorded_at": m.recorded_at,
            }
            row.update(m.features)
            writer.writerow(row)
        QMessageBox.information(self, "Export", f"CSV exportiert:\n{path}")

    def _on_export_json(self) -> None:
        sel = self._selected_measurement()
        if not sel:
            return
        p, m = sel
        if not m.raw_data_path or not Path(m.raw_data_path).exists():
            QMessageBox.information(self, "JSON", "Keine Rohdaten für diese Messung vorhanden.")
            return
        src = Path(m.raw_data_path)
        path, _ = QFileDialog.getSaveFileName(self, "JSON exportieren", src.name, "JSON (*.json)")
        if not path:
            return
        shutil.copy2(src, path)
        QMessageBox.information(self, "Export", f"JSON exportiert:\n{path}")

    def _on_export_all(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "CSV exportieren", "tappd_export.csv", "CSV (*.csv)")
        if not path:
            return
        all_keys: list[str] = []
        for _, m in self._data:
            for k in m.features:
                if k not in all_keys:
                    all_keys.append(k)
        with open(path, "w", newline="") as f:
            header = ["patient_code", "patient_name", "test_type", "hand", "duration_s", "recorded_at"] + all_keys
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            for p, m in self._data:
                row = {
                    "patient_code": p.patient_code, "patient_name": p.display_name,
                    "test_type": m.test_type, "hand": m.hand,
                    "duration_s": m.duration_s, "recorded_at": m.recorded_at,
                }
                row.update(m.features)
                writer.writerow(row)
        QMessageBox.information(self, "Export", f"{len(self._data)} Messungen exportiert.")
