"""Generic dialog that renders a clinical form (clinical/schema.py).

Sections become titled blocks, items become widgets by type, a repeat group
becomes a small table with add/remove; computed values (LEDD, disease
duration) update live and are read-only. Saving validates against the form
and shows the issues inline instead of closing.
"""

from __future__ import annotations

from datetime import date

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from clinical.schema import Form, Item, Repeat, compute, is_empty, validate
from ui import theme

_NONE = "— keine Angabe —"


class FormDialog(QDialog):
    def __init__(self, parent, form: Form, *, patient=None, session_label: str = "",
                 answers: dict | None = None) -> None:
        super().__init__(parent)
        self.form = form
        self._widgets: dict[str, object] = {}
        self._repeat_tables: dict[str, "_RepeatTable"] = {}
        self._computed_lbls: dict[str, QLabel] = {}
        self.setWindowTitle(form.name)
        self.setMinimumSize(760, 640)
        self.resize(860, 760)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        head = QLabel(form.name)
        head.setStyleSheet("font-size: 16px; font-weight: 600;")
        root.addWidget(head)
        parts = []
        if patient is not None:
            parts.append(patient.display_name)
            if getattr(patient, "age", None) is not None:
                parts.append(f"{patient.age} Jahre")
            sex = {"m": "männlich", "f": "weiblich", "d": "divers"}.get(
                getattr(patient, "gender", ""), "")
            if sex:
                parts.append(sex)
        if session_label:
            parts.append(session_label)
        sub = QLabel("  ·  ".join(parts) + ("  ·  " if parts else "")
                     + "Alter und Geschlecht kommen aus den Stammdaten.")
        sub.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        sub.setWordWrap(True)
        root.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(14)
        for section in form.sections:
            lay.addWidget(self._build_section(section))
        if form.computed:
            box = QFrame()
            box.setStyleSheet(f"QFrame {{ background: {theme.SUCCESS_BG}; border-radius: 6px; }}")
            g = QGridLayout(box)
            g.setContentsMargins(12, 8, 12, 8)
            for r, c in enumerate(form.computed):
                k = QLabel(c.label)
                k.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
                v = QLabel("–")
                v.setStyleSheet("font-weight: 600;")
                self._computed_lbls[c.key] = v
                g.addWidget(k, r, 0)
                g.addWidget(v, r, 1)
            g.setColumnStretch(1, 1)
            lay.addWidget(box)
        lay.addStretch()
        scroll.setWidget(page)
        root.addWidget(scroll, 1)

        self._issues_lbl = QLabel()
        self._issues_lbl.setWordWrap(True)
        self._issues_lbl.setStyleSheet(f"color: {theme.DANGER}; font-size: 12px;")
        self._issues_lbl.setVisible(False)
        root.addWidget(self._issues_lbl)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Speichern")
        ok.setProperty("cssClass", "primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.set_answers(answers or {})
        self._recompute()

    # ── building ──────────────────────────────────────────────────
    def _build_section(self, section) -> QWidget:
        box = QFrame()
        box.setStyleSheet(f"QFrame {{ background: {theme.CARD_BG}; border: 1px solid {theme.BORDER}; "
                          f"border-radius: 8px; }} QLabel {{ border: none; }}")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 10, 14, 12)
        title = QLabel(section.title)
        title.setStyleSheet("font-size: 13px; font-weight: 600; letter-spacing: 0.5px;")
        lay.addWidget(title)
        if section.repeat is not None:
            table = _RepeatTable(section.repeat, self._recompute)
            self._repeat_tables[section.repeat.key] = table
            lay.addWidget(table)
        if section.items:
            form = QFormLayout()
            form.setHorizontalSpacing(14)
            form.setVerticalSpacing(8)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            for it in section.items:
                w = self._make_widget(it)
                self._widgets[it.key] = w
                # QFormLayout turns "&" into a mnemonic — "Hoehn & Yahr" needs it.
                label = it.label.replace("&", "&&") + (" *" if it.required else "")
                if it.help:
                    w.setToolTip(it.help)
                form.addRow(label, w)
            lay.addLayout(form)
        return box

    def _make_widget(self, it: Item) -> QWidget:
        if it.type in ("integer", "scale"):
            w = QSpinBox()
            lo, hi = it.range or (0, 1_000_000)
            w.setRange(int(lo) - 1, int(hi))          # one below = "not answered"
            w.setSpecialValueText(_NONE)
            w.setValue(int(lo) - 1)
            if it.unit:
                w.setSuffix(f" {it.unit}")
            w.valueChanged.connect(self._recompute)
            w.setMaximumWidth(220)
            return w
        if it.type == "decimal":
            w = QDoubleSpinBox()
            lo, hi = it.range or (0, 1_000_000)
            w.setDecimals(2)
            w.setRange(lo - 1, hi)
            w.setSpecialValueText(_NONE)
            w.setValue(lo - 1)
            if it.unit:
                w.setSuffix(f" {it.unit}")
            w.valueChanged.connect(self._recompute)
            w.setMaximumWidth(220)
            return w
        if it.type == "choice":
            w = QComboBox()
            w.addItem(_NONE, None)
            for c in it.choices:
                w.addItem(c.label, c.code)
            w.currentIndexChanged.connect(self._recompute)
            return w
        if it.type == "multichoice":
            w = QWidget()
            g = QGridLayout(w)
            g.setContentsMargins(0, 0, 0, 0)
            g.setHorizontalSpacing(18)
            w._boxes = []                                    # type: ignore[attr-defined]
            cols = 3
            for i, c in enumerate(it.choices):
                cb = QCheckBox(c.label)
                cb.setProperty("code", c.code)
                cb.toggled.connect(self._recompute)
                g.addWidget(cb, i // cols, i % cols)
                w._boxes.append(cb)                          # type: ignore[attr-defined]
            return w
        if it.type == "bool":
            w = QCheckBox("ja")
            w.toggled.connect(self._recompute)
            return w
        if it.type == "date":
            w = QDateEdit()
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd.MM.yyyy")
            w.setSpecialValueText(_NONE)
            w.setMinimumDate(QDate(1900, 1, 1))
            w.setDate(QDate(1900, 1, 1))
            return w
        if it.multiline:
            w = QTextEdit()
            w.setMaximumHeight(90)
            return w
        return QLineEdit()

    # ── values ────────────────────────────────────────────────────
    def _get(self, it: Item):
        w = self._widgets[it.key]
        if it.type in ("integer", "scale"):
            v = w.value()
            return None if v == w.minimum() else int(v)
        if it.type == "decimal":
            v = w.value()
            return None if v == w.minimum() else float(v)
        if it.type == "choice":
            return w.currentData()
        if it.type == "multichoice":
            return [cb.property("code") for cb in w._boxes if cb.isChecked()]
        if it.type == "bool":
            return bool(w.isChecked())
        if it.type == "date":
            d = w.date()
            return None if d == w.minimumDate() else d.toString("yyyy-MM-dd")
        if isinstance(w, QTextEdit):
            return w.toPlainText().strip()
        return w.text().strip()

    def _set(self, it: Item, value) -> None:
        w = self._widgets[it.key]
        w.blockSignals(True)
        try:
            if it.type in ("integer", "scale"):
                w.setValue(w.minimum() if is_empty(value) else int(float(value)))
            elif it.type == "decimal":
                w.setValue(w.minimum() if is_empty(value) else float(value))
            elif it.type == "choice":
                idx = w.findData(None if is_empty(value) else str(value))
                w.setCurrentIndex(max(0, idx))
            elif it.type == "multichoice":
                codes = {str(v) for v in (value or [])}
                for cb in w._boxes:
                    cb.setChecked(cb.property("code") in codes)
            elif it.type == "bool":
                w.setChecked(bool(value))
            elif it.type == "date":
                w.setDate(w.minimumDate() if is_empty(value)
                          else QDate.fromString(str(value), "yyyy-MM-dd"))
            elif isinstance(w, QTextEdit):
                w.setPlainText("" if is_empty(value) else str(value))
            else:
                w.setText("" if is_empty(value) else str(value))
        finally:
            w.blockSignals(False)

    def answers(self) -> dict:
        out: dict = {}
        for it in self.form.items:
            v = self._get(it)
            if not is_empty(v):
                out[it.key] = v
        for key, table in self._repeat_tables.items():
            rows = table.rows()
            if rows:
                out[key] = rows
        return out

    def set_answers(self, answers: dict) -> None:
        for it in self.form.items:
            self._set(it, answers.get(it.key))
        for key, table in self._repeat_tables.items():
            table.set_rows(answers.get(key) or [])
        self._recompute()

    def _recompute(self, *_) -> None:
        vals = compute(self.form, self.answers(), today=date.today())
        for c in self.form.computed:
            v = vals.get(c.key)
            self._computed_lbls[c.key].setText(
                "–" if is_empty(v) else f"{v:g}{(' ' + c.unit) if c.unit else ''}")

    # ── result ────────────────────────────────────────────────────
    def accept(self) -> None:
        issues = [i for i in validate(self.form, self.answers()) if i.level == "error"]
        if issues:
            self._issues_lbl.setText("\n".join(f"⚠ {i.message}" for i in issues))
            self._issues_lbl.setVisible(True)
            return
        super().accept()


class _RepeatTable(QWidget):
    """A repeat group as a table: one row per entry, widgets per cell."""

    def __init__(self, rep: Repeat, on_change, parent=None) -> None:
        super().__init__(parent)
        self.rep = rep
        self._on_change = on_change
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        self.table = QTableWidget(0, len(rep.items))
        self.table.setHorizontalHeaderLabels([it.label for it in rep.items])
        hdr = self.table.horizontalHeader()
        # Text columns take the room, numbers stay compact but readable.
        for c, it in enumerate(rep.items):
            if it.type in ("choice", "text"):
                hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
            else:
                hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
                self.table.setColumnWidth(c, 150)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(self._ROW_H)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        lay.addWidget(self.table)
        self._fit_height()
        row = QHBoxLayout()
        add = QPushButton(f"＋ {rep.label}")
        add.clicked.connect(lambda: self.add_row({}))
        rm = QPushButton("– Zeile entfernen")
        rm.clicked.connect(self._remove_current)
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch()
        lay.addLayout(row)

    _ROW_H = 46

    def _fit_height(self) -> None:
        """Show every row (up to six) instead of a two-row peephole."""
        n = max(1, min(6, self.table.rowCount()))
        h = self.table.horizontalHeader().height() + n * self._ROW_H + 14
        self.table.setMinimumHeight(h)
        self.table.setMaximumHeight(h)

    def _cell_widget(self, it: Item, value):
        if it.type == "choice":
            w = QComboBox()
            w.addItem(_NONE, None)
            for c in it.choices:
                w.addItem(c.label, c.code)
            idx = w.findData(None if is_empty(value) else str(value))
            w.setCurrentIndex(max(0, idx))
            w.currentIndexChanged.connect(self._on_change)
            return w
        if it.type in ("integer", "scale"):
            w = QSpinBox()
            lo, hi = it.range or (0, 1_000_000)
            w.setRange(int(lo) - 1, int(hi))
            w.setSpecialValueText(_NONE)
            w.setValue(int(lo) - 1 if is_empty(value) else int(float(value)))
            w.valueChanged.connect(self._on_change)
            return w
        if it.type == "decimal":
            w = QDoubleSpinBox()
            lo, hi = it.range or (0, 1_000_000)
            w.setDecimals(2)
            w.setRange(lo - 1, hi)
            w.setSpecialValueText(_NONE)
            w.setValue(lo - 1 if is_empty(value) else float(value))
            w.valueChanged.connect(self._on_change)
            return w
        w = QLineEdit("" if is_empty(value) else str(value))
        w.editingFinished.connect(self._on_change)
        return w

    def _cell_value(self, it: Item, w):
        if it.type == "choice":
            return w.currentData()
        if it.type in ("integer", "scale"):
            return None if w.value() == w.minimum() else int(w.value())
        if it.type == "decimal":
            return None if w.value() == w.minimum() else float(w.value())
        return w.text().strip()

    def add_row(self, values: dict) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, it in enumerate(self.rep.items):
            self.table.setCellWidget(r, c, self._cell_widget(it, (values or {}).get(it.key)))
            self.table.setItem(r, c, QTableWidgetItem())
        self.table.setRowHeight(r, self._ROW_H)
        self._fit_height()
        self._on_change()

    def _remove_current(self) -> None:
        r = self.table.currentRow()
        if r < 0 and self.table.rowCount():
            r = self.table.rowCount() - 1
        if r >= 0:
            self.table.removeRow(r)
            self._fit_height()
            self._on_change()

    def rows(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            row = {}
            for c, it in enumerate(self.rep.items):
                v = self._cell_value(it, self.table.cellWidget(r, c))
                if not is_empty(v):
                    row[it.key] = v
            if row:
                out.append(row)
        return out

    def set_rows(self, rows: list[dict]) -> None:
        self.table.setRowCount(0)
        for row in rows:
            self.add_row(row)
