"""Edit the note (text + attached files) of one item in the patient's record.

One note per item — a session, a recording step, an imported video or a
measurement. Attachments are copied into ``data/attachments`` when added
(storage.attachments); cancelling the dialog removes the copies made in it.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from storage.attachments import add_attachment, remove_attachment, size_label
from storage.database import Note
from ui import theme


class NoteDialog(QDialog):
    def __init__(self, parent, title: str, note: Note, patient_code: str) -> None:
        super().__init__(parent)
        self._note = note
        self._code = patient_code
        self._added: list[dict] = []        # copies made in this dialog (undone on cancel)
        self._removed: list[dict] = []      # existing files to delete on OK
        self.deleted = False
        self.setWindowTitle(f"Notiz – {title}")
        self.setMinimumSize(560, 460)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        head = QLabel(title)
        head.setStyleSheet("font-size: 14px; font-weight: 600;")
        lay.addWidget(head)

        self._text = QTextEdit()
        self._text.setPlaceholderText("Notiz zu diesem Eintrag — Beobachtungen, Besonderheiten, "
                                      "Hinweise für die Auswertung …")
        self._text.setPlainText(note.text)
        lay.addWidget(self._text, 1)

        att_lbl = QLabel("Anhänge")
        att_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        lay.addWidget(att_lbl)
        self._list = QListWidget()
        self._list.setMaximumHeight(140)
        self._list.itemDoubleClicked.connect(lambda _i: self._open())
        lay.addWidget(self._list)
        row = QHBoxLayout()
        self._add_btn = QPushButton("＋ Datei hinzufügen…")
        self._add_btn.clicked.connect(self._pick)
        self._open_btn = QPushButton("Öffnen")
        self._open_btn.clicked.connect(self._open)
        self._rm_btn = QPushButton("Entfernen")
        self._rm_btn.clicked.connect(self._remove)
        for b in (self._add_btn, self._open_btn, self._rm_btn):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)

        self._attachments: list[dict] = [dict(a) for a in note.attachments]
        self._fill()

        buttons = QDialogButtonBox()
        self._delete_btn = buttons.addButton("Notiz löschen",
                                             QDialogButtonBox.ButtonRole.DestructiveRole)
        self._delete_btn.setVisible(bool(note.id))
        self._delete_btn.clicked.connect(self._delete)
        ok = buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        ok.setProperty("cssClass", "primary")
        ok.setText("Speichern")
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    # ── attachments ───────────────────────────────────────────────
    def _fill(self) -> None:
        self._list.clear()
        for a in self._attachments:
            it = QListWidgetItem(f"{a.get('name', '?')}   ({size_label(int(a.get('size') or 0))})")
            it.setToolTip(a.get("path", ""))
            self._list.addItem(it)
        has = self._list.count() > 0
        self._open_btn.setEnabled(has)
        self._rm_btn.setEnabled(has)

    def _pick(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Datei anhängen", "",
            "Alle Dateien (*);;Bilder (*.png *.jpg *.jpeg);;Dokumente (*.pdf *.txt *.md)")
        for p in paths:
            self.add_file(p)

    def add_file(self, path: str) -> dict | None:
        """Copy ``path`` in as an attachment (the file dialog's work, callable directly)."""
        try:
            entry = add_attachment(self._code, self._note.kind, self._note.ref, path)
        except Exception as e:
            QMessageBox.warning(self, "Anhang", f"Datei konnte nicht übernommen werden:\n{e}")
            return None
        self._attachments.append(entry)
        self._added.append(entry)
        self._fill()
        self._list.setCurrentRow(self._list.count() - 1)
        return entry

    def _current(self) -> dict | None:
        row = self._list.currentRow()
        return self._attachments[row] if 0 <= row < len(self._attachments) else None

    def _open(self) -> None:
        a = self._current()
        from storage.attachments import attachment_path
        path = attachment_path(a) if a else ""
        if path and os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _remove(self) -> None:
        a = self._current()
        if a is None:
            return
        self._attachments.remove(a)
        if a in self._added:
            self._added.remove(a)
            remove_attachment(a)
        else:
            self._removed.append(a)
        self._fill()

    # ── result ────────────────────────────────────────────────────
    def _delete(self) -> None:
        if QMessageBox.question(self, "Notiz löschen",
                                "Notiz und alle Anhänge dieses Eintrags löschen?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.deleted = True
        self.accept()

    def accept(self) -> None:
        if self.deleted:
            for a in self._attachments:
                remove_attachment(a)
            self._attachments = []
        else:
            for a in self._removed:
                remove_attachment(a)
        self._note.text = self._text.toPlainText().strip()
        self._note.attachments = self._attachments
        super().accept()

    def reject(self) -> None:
        for a in self._added:                # undo the copies made here
            remove_attachment(a)
        super().reject()

    @property
    def note(self) -> Note:
        return self._note

    @property
    def is_empty(self) -> bool:
        return not self._note.text and not self._note.attachments
