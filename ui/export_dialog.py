"""Export dialogs: the hand-over bundle of one patient, the research tables.

Both collect options and a destination, run the export with a busy cursor
and report what was written. The work itself lives in ``export/``.
"""

from __future__ import annotations

import os
from datetime import date

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ui import theme


def _head(layout, title: str, sub: str) -> None:
    h = QLabel(title)
    h.setStyleSheet("font-size: 15px; font-weight: 600;")
    layout.addWidget(h)
    s = QLabel(sub)
    s.setWordWrap(True)
    s.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
    layout.addWidget(s)


def _dest_row(layout, label: str, default: str, pick) -> QLineEdit:
    row = QHBoxLayout()
    row.addWidget(QLabel(label))
    edit = QLineEdit(default)
    row.addWidget(edit, 1)
    btn = QPushButton("…")
    btn.setFixedWidth(44)
    btn.clicked.connect(lambda: pick(edit))
    row.addWidget(btn)
    layout.addLayout(row)
    return edit


class BundleExportDialog(QDialog):
    """Report + videos + data of one patient as a ZIP."""

    def __init__(self, parent, patient, conn_factory) -> None:
        super().__init__(parent)
        self._patient = patient
        self._conn_factory = conn_factory
        self.result_info = None
        self.setWindowTitle("Export-Paket")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        _head(lay, f"Export-Paket für {patient.display_name}",
              "Ein ZIP mit Bericht (HTML, PDF, JSON), den Archiv-Clips, Tracking-Spuren, "
              "Rohdaten und Notiz-Anhängen — zur Übergabe, für ein Gutachten oder das Archiv.")
        self.cb_pseudo = QCheckBox("Pseudonymisieren (kein Name, kein Geburtsdatum)")
        self.cb_videos = QCheckBox("Videos beilegen")
        self.cb_videos.setChecked(True)
        self.cb_defaced = QCheckBox("nur anonymisierte Clips (Gesicht unkenntlich)")
        self.cb_defaced.setChecked(True)
        self.cb_videos.toggled.connect(self.cb_defaced.setEnabled)
        self.cb_tracks = QCheckBox("Tracking-Spuren beilegen")
        self.cb_tracks.setChecked(True)
        self.cb_raw = QCheckBox("Rohdaten der Auswertungen beilegen (JSON)")
        self.cb_raw.setChecked(True)
        self.cb_att = QCheckBox("Notizen und Anhänge beilegen")
        self.cb_att.setChecked(True)
        self.cb_pdf = QCheckBox("PDF-Bericht erzeugen")
        self.cb_pdf.setChecked(True)
        for cb in (self.cb_pseudo, self.cb_videos, self.cb_defaced, self.cb_tracks, self.cb_raw,
                   self.cb_att, self.cb_pdf):
            lay.addWidget(cb)
        default = os.path.join(os.path.expanduser("~"),
                               f"{patient.patient_code}_{date.today().isoformat()}.zip")
        self.dest = _dest_row(lay, "Datei:", default, self._pick)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        lay.addWidget(self._status)
        buttons = QDialogButtonBox()
        ok = buttons.addButton("Exportieren", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setProperty("cssClass", "primary")
        buttons.addButton("Schließen", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._run)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _pick(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export-Paket speichern", edit.text(),
                                              "ZIP (*.zip)")
        if path:
            edit.setText(path)

    def options(self):
        from export.bundle import BundleOptions
        return BundleOptions(pseudonymise=self.cb_pseudo.isChecked(),
                             include_videos=self.cb_videos.isChecked(),
                             only_defaced=self.cb_defaced.isChecked(),
                             include_tracks=self.cb_tracks.isChecked(),
                             include_raw=self.cb_raw.isChecked(),
                             include_attachments=self.cb_att.isChecked(),
                             include_notes=self.cb_att.isChecked(),
                             pdf=self.cb_pdf.isChecked())

    def _run(self) -> None:
        from export.bundle import export_bundle
        dest = self.dest.text().strip()
        if not dest:
            return
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        conn = self._conn_factory()
        try:
            self.result_info = export_bundle(conn, self._patient, dest, self.options())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Export", f"Export fehlgeschlagen:\n{e}")
            return
        finally:
            conn.close()
            QApplication.restoreOverrideCursor()
        r = self.result_info
        txt = (f"✔ {os.path.basename(r.path)}: {len(r.files)} Dateien, {r.n_videos} Videos"
               + (f" ({r.skipped_videos} nicht anonymisierte übersprungen)" if r.skipped_videos else "")
               + (", PDF" if r.pdf_written else ", kein PDF"))
        self._status.setText(txt)
        self.accept()


class ResearchExportDialog(QDialog):
    """Pseudonymised long tables across all patients."""

    def __init__(self, parent, conn_factory) -> None:
        super().__init__(parent)
        self._conn_factory = conn_factory
        self.result_info = None
        self.setWindowTitle("Forschungsexport")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        _head(lay, "Forschungsexport (alle Patienten)",
              "Pseudonymisierte Langtabellen für R/pandas: Patienten, Sitzungen mit klinischen "
              "Werten, Messungen mit Herkunft, Kennwerte, klinische Items, Medikation — "
              "dazu ein Codebuch. Die Zuordnung Pseudonym ↔ Patient bleibt lokal "
              "(data/pseudonyms.json).")
        self.cb_notes = QCheckBox("Notizen beilegen (Freitext, kann Namen enthalten)")
        self.cb_signals = QCheckBox("Rohdaten und Tracking-Spuren beilegen (signals/)")
        lay.addWidget(self.cb_notes)
        lay.addWidget(self.cb_signals)
        default = os.path.join(os.path.expanduser("~"),
                               f"motryx_forschungsexport_{date.today().isoformat()}")
        self.dest = _dest_row(lay, "Ordner:", default, self._pick)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        lay.addWidget(self._status)
        buttons = QDialogButtonBox()
        ok = buttons.addButton("Exportieren", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setProperty("cssClass", "primary")
        buttons.addButton("Schließen", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._run)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _pick(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Zielordner", edit.text())
        if path:
            edit.setText(path)

    def _run(self) -> None:
        from export.research import export_research
        dest = self.dest.text().strip()
        if not dest:
            return
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        conn = self._conn_factory()
        try:
            self.result_info = export_research(conn, dest, include_notes=self.cb_notes.isChecked(),
                                               include_signals=self.cb_signals.isChecked())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Export", f"Export fehlgeschlagen:\n{e}")
            return
        finally:
            conn.close()
            QApplication.restoreOverrideCursor()
        t = self.result_info["tables"]
        self._status.setText(f"✔ {t['patients']} Patienten, {t['visits']} Sitzungen, "
                             f"{t['measurements']} Messungen, {t['features_long']} Kennwerte, "
                             f"{t['clinical_long']} klinische Werte → {dest}")
        self.accept()
