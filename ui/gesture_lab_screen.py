"""Main Gesture Lab screen with sidebar mode navigation."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.theme import SZ


class GestureLabScreen(QWidget):
    """Top-level Gesture Lab screen.

    Left sidebar with 3 mode buttons, right content area as QStackedWidget.
    """

    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ───────────────────────────────────────────────
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(12, 8, 12, 8)

        back_btn = QPushButton("← Zurück")
        back_btn.setFixedHeight(SZ.BTN_H)
        back_btn.setFixedWidth(120)
        back_btn.clicked.connect(self._on_back)
        top_bar.addWidget(back_btn)

        top_bar.addStretch()

        title = QLabel("✋ Gesture Lab")
        title.setFont(QFont("Helvetica Neue", 18, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_bar.addWidget(title)

        top_bar.addStretch()

        # Patient/source context (right side, mirrors the back button width)
        self._context_lbl = QLabel("")
        self._context_lbl.setFixedWidth(260)
        self._context_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._context_lbl.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        top_bar.addWidget(self._context_lbl)

        root.addLayout(top_bar)

        # ── Separator ─────────────────────────────────────────────
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {theme.BORDER};")
        root.addWidget(sep)

        # ── Body: sidebar + content ──────────────────────────────
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Sidebar
        sidebar = QVBoxLayout()
        sidebar.setContentsMargins(8, 12, 8, 12)
        sidebar.setSpacing(6)

        sidebar_widget = QWidget()
        sidebar_widget.setFixedWidth(130)
        sidebar_widget.setStyleSheet(
            f"background-color: {theme.CARD_BG}; border-right: 1px solid {theme.BORDER};"
        )

        self._mode_buttons: list[QPushButton] = []
        self._mode_labels = ["GESTEN", "ANALYSE", "ERKENNUNG"]

        for i, label in enumerate(self._mode_labels):
            btn = QPushButton(label)
            btn.setFixedHeight(SZ.BTN_H + 4)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, idx=i: self._switch_mode(idx))
            self._mode_buttons.append(btn)
            sidebar.addWidget(btn)

        sidebar.addStretch()
        sidebar_widget.setLayout(sidebar)
        body.addWidget(sidebar_widget)

        # Vertical separator
        vsep = QWidget()
        vsep.setFixedWidth(1)
        vsep.setStyleSheet(f"background-color: {theme.BORDER};")
        body.addWidget(vsep)

        # Content stack
        self.content_stack = QStackedWidget()
        body.addWidget(self.content_stack, stretch=1)

        root.addLayout(body, stretch=1)

        self._panels_built = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._panels_built:
            self._build_panels()
            self._panels_built = True
        self._update_context_label()

    def _update_context_label(self) -> None:
        from capture.source import source_kind
        src = {"leap": "Leap", "webcam": "Kamera", "mock": "Simulation"}.get(
            source_kind(self.capture), "?")
        p = self.patient
        who = p.display_name if p is not None else "kein Patient (nur Bibliothek)"
        self._context_lbl.setText(f"{who}  ·  Quelle: {src}")

    def _build_panels(self) -> None:
        from ui.gesture_lab_gesten import GestenPanel
        from ui.gesture_lab_analysis import AnalysisPanel
        from ui.gesture_lab_detect import DetectPanel

        self.gesten_panel = GestenPanel(self)
        self.analysis_panel = AnalysisPanel(self)
        self.detect_panel = DetectPanel(self)

        self.content_stack.addWidget(self.gesten_panel)
        self.content_stack.addWidget(self.analysis_panel)
        self.content_stack.addWidget(self.detect_panel)

        self._switch_mode(0)

    def _switch_mode(self, index: int) -> None:
        for i, btn in enumerate(self._mode_buttons):
            btn.setChecked(i == index)
            if i == index:
                btn.setStyleSheet(
                    f"background-color: {theme.PRIMARY}; color: white; "
                    f"font-weight: bold; border-radius: 4px;"
                )
            else:
                btn.setStyleSheet("")
        if self._panels_built:
            self.content_stack.setCurrentIndex(index)

    def _on_back(self) -> None:
        if self._panels_built and hasattr(self.gesten_panel, 'stop_recording'):
            self.gesten_panel.stop_recording()
        if self._panels_built and hasattr(self.detect_panel, 'stop_detection'):
            self.detect_panel.stop_detection()
        if getattr(self, "return_screen", "patients") == "detail" \
                and self.main_window.current_patient is not None:
            self.main_window.select_patient(self.main_window.current_patient)
        else:
            self.main_window.show_patient_screen()

    @property
    def capture(self):
        return self.main_window.capture_device

    @property
    def patient(self):
        """Currently selected patient (None → library work only, no saving)."""
        return getattr(self.main_window, "current_patient", None)
