"""A collapsible "info" panel: label/value rows plus consistency warnings.

Used under a take in the recording pane and under a measurement in the detail
dialog, so the clinician can check *when, how and with what* something was
recorded without leaving the view. Collapsed by default; the header always
shows whether there is anything to worry about.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QGridLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout,
                             QWidget)

from ui import theme


class MetaPanel(QWidget):
    def __init__(self, title: str = "Aufnahme-Info", parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._rows: list[tuple[str, str]] = []
        self._issues: list[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._toggle = QToolButton()
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(False)
        self._toggle.setStyleSheet(
            f"QToolButton {{ border: none; color: {theme.TEXT_SECONDARY}; font-size: 12px; }}")
        self._toggle.toggled.connect(self._on_toggled)
        root.addWidget(self._toggle)

        self._body = QWidget()
        self._body.setStyleSheet(
            f"QWidget {{ background: {theme.CARD_BG}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 6px; }} QLabel {{ border: none; font-size: 12px; }}")
        self._grid = QGridLayout(self._body)
        self._grid.setContentsMargins(12, 8, 12, 8)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(3)
        # Never squeezed by neighbours: a crowded pane shrinks the video, not
        # the rows (they would overlap otherwise).
        self._body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._body.setVisible(False)
        root.addWidget(self._body)
        self._update_header()

    # ── API ───────────────────────────────────────────────────────
    def set_content(self, rows: list[tuple[str, str]], issues: list[str] | None = None) -> None:
        self._rows = list(rows)
        self._issues = list(issues or [])
        self._rebuild()
        self._update_header()

    def clear(self) -> None:
        self.set_content([], [])

    @property
    def expanded(self) -> bool:
        return self._toggle.isChecked()

    def set_expanded(self, on: bool) -> None:
        self._toggle.setChecked(bool(on))

    @property
    def issues(self) -> list[str]:
        return list(self._issues)

    # ── internals ─────────────────────────────────────────────────
    def _on_toggled(self, on: bool) -> None:
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self._body.setVisible(on)

    def _update_header(self) -> None:
        n = len(self._issues)
        if n:
            self._toggle.setText(f"{self._title}   ⚠ {n} Hinweis{'' if n == 1 else 'e'}")
            self._toggle.setStyleSheet(
                f"QToolButton {{ border: none; color: {theme.WARN_DARK}; font-size: 12px; }}")
        else:
            self._toggle.setText(self._title)
            self._toggle.setStyleSheet(
                f"QToolButton {{ border: none; color: {theme.TEXT_SECONDARY}; font-size: 12px; }}")
        self._toggle.setEnabled(bool(self._rows or self._issues))

    def _rebuild(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                # Detach right away: a widget waiting for deleteLater still
                # paints, and the old rows would ghost under the new ones.
                w.setParent(None)
                w.deleteLater()
        r = 0
        for text in self._issues:
            lbl = QLabel(f"⚠ {text}")
            lbl.setStyleSheet(f"color: {theme.WARN_DARK}; border: none; font-size: 12px;")
            lbl.setWordWrap(True)
            self._grid.addWidget(lbl, r, 0, 1, 2)
            r += 1
        for label, value in self._rows:
            k = QLabel(label)
            k.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; border: none; font-size: 12px;")
            k.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            v = QLabel(value)
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._grid.addWidget(k, r, 0)
            self._grid.addWidget(v, r, 1)
            r += 1
        self._grid.setColumnStretch(1, 1)
        self._body.setMinimumHeight(self._body.sizeHint().height())

    def texts(self) -> list[str]:
        """Rendered lines (for tests): issues first, then 'label: value'."""
        return [f"⚠ {t}" for t in self._issues] + [f"{k}: {v}" for k, v in self._rows]
