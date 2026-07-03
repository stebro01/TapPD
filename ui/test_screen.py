"""Test recording screen with hand detection, countdown, live plot."""

import logging

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.live_metric_plot import LiveMetricPlot

from paradigms.base_test import BaseParadigm
from paradigms.config import get_test_config
from paradigms.recorder import extract_metric
from capture.source import profile_for
from ui.hand_visualization import HandVisualizationWidget
from ui.pretest_gate import ReadinessGate
from ui.theme import SZ, ACCENT
from ui import theme

log = logging.getLogger(__name__)


class TestScreen(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.test: BaseParadigm | None = None
        self.patient_id = ""
        self._recording = False
        self._runner = None   # ParadigmRunner (shared frame-pump), set per test

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 16)
        layout.setSpacing(10)

        # Top: text + image
        top = QHBoxLayout()
        top.setSpacing(20)

        self.instructions_label = QLabel()
        self.instructions_label.setWordWrap(True)
        self.instructions_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.instructions_label.setStyleSheet("font-size: 14px; line-height: 1.5;")
        self.instructions_label.setMinimumWidth(280)
        top.addWidget(self.instructions_label, stretch=1)

        self.instruction_image = QLabel()
        self.instruction_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.instruction_image.setFixedSize(330, 200)
        self.instruction_image.setScaledContents(True)
        self.instruction_image.setStyleSheet(f"border: 1px solid {theme.BORDER}; border-radius: 10px;")
        top.addWidget(self.instruction_image)

        # Small live hand window, shown during recording (source-agnostic)
        self.live_hand = HandVisualizationWidget()
        self.live_hand.setFixedSize(200, 200)
        self.live_hand.setVisible(False)
        top.addWidget(self.live_hand)

        layout.addLayout(top)

        # Status (hand detection / countdown / recording)
        self.status_label = QLabel()
        self.status_label.setProperty("cssClass", "countdown")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedHeight(8)
        layout.addWidget(self.progress_bar)

        # Live plot (shared widget)
        self.plot = LiveMetricPlot(figsize=(8, 2.5))
        layout.addWidget(self.plot)

        # Cancel
        self.cancel_button = QPushButton("Abbrechen")
        self.cancel_button.setFixedWidth(180)
        self.cancel_button.setFixedHeight(SZ.BTN_H)
        self.cancel_button.clicked.connect(self._on_cancel)
        layout.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignCenter)

        self._ui_timer = QTimer()
        self._ui_timer.timeout.connect(self._update_ui)

        # Unified, source-agnostic pre-test readiness gate (hand model + 1-2-3).
        self.readiness_gate = ReadinessGate(self)
        self.readiness_gate.ready.connect(self._on_gate_ready)
        self.readiness_gate.cancelled.connect(self._on_gate_cancelled)

    def start_test(self, test: BaseParadigm, patient_id: str) -> None:
        self.test = test
        self.patient_id = patient_id
        self._runner = None
        self.progress_bar.setValue(0)
        self.instructions_label.setText(test.get_instructions())

        img_path = test.get_instruction_figure_path()
        if img_path:
            self.instruction_image.setPixmap(QPixmap(str(img_path)))
            self.instruction_image.setVisible(True)
        else:
            self.instruction_image.setVisible(False)

        self.plot.clear_plot()
        self.cancel_button.setEnabled(True)

        # Unified pre-test gate: hand model + 1-2-3 countdown, source-agnostic.
        self.status_label.setText("")
        from capture.source import CAP_FACE_LANDMARKS
        from paradigms.config import get_task_requirements
        if get_task_requirements(test.test_type()) == {CAP_FACE_LANDMARKS}:
            # Ocular paradigm: no hand to wait for — face stream on, short
            # countdown, then record.
            enable = getattr(test.capture, "enable_face", None)
            if enable is not None:
                enable(True, full_rate=True)
            self.status_label.setText("Bitte in die Kamera schauen …")
            QTimer.singleShot(3000, lambda: self._on_gate_ready("both"))
            return
        require_hand = "both" if test.bilateral else test.hand
        profile = profile_for(test.capture)
        self.readiness_gate.begin(test.capture, profile, require_hand=require_hand)

    def _on_gate_ready(self, hand: str) -> None:
        """Hand detected + countdown finished — begin recording."""
        self.status_label.setProperty("cssClass", "recording")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setText("Aufnahme läuft...")
        self.live_hand.setVisible(True)
        self._start_recording()

    def _on_gate_cancelled(self) -> None:
        """Gate timed out or was cancelled — return to the dashboard."""
        self.main_window.show_start()

    # SETTLE: discard frames the LeapC SDK buffered during the detection/countdown
    # phase (delivered instantly from the buffer); genuine frames arrive at ~120 Hz.
    SETTLE_S = 0.25

    def _start_recording(self) -> None:
        """Start recording via the shared ParadigmRunner (SETTLE + duration gated)."""
        from paradigms.runner import ParadigmRunner
        self._recording = True
        self._runner = ParadigmRunner(self.test, settle_s=self.SETTLE_S,
                                      duration_s=self.test.duration)
        self._runner.begin()
        self.test.capture.start_tracking(self._runner.feed)
        self._ui_timer.start(33)
        # Safety timeout: settle + duration + 3s margin.
        QTimer.singleShot(int((self.SETTLE_S + self.test.duration + 3) * 1000), self._on_done)

    def _update_ui(self) -> None:
        if not self._recording or self._runner is None:
            return
        r = self._runner
        # Live hand window: the most recent frame across hands.
        last = None
        for f in r.last_frame.values():
            if f is not None and (last is None or f.timestamp_us > last.timestamp_us):
                last = f
        if last is not None:
            self.live_hand.update_frame(last)
        if r.duration_reached:
            self._on_done()
            return
        live = r.live_snapshot()
        last_t = max([d[-1][0] for d in live.values() if d], default=0.0)
        self.progress_bar.setValue(min(100, int(last_t / self.test.duration * 100)))
        self.plot.update_plot(live, self.test.get_live_metric_label(), window_s=5.0)

    def _on_done(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self._ui_timer.stop()
        self.live_hand.setVisible(False)
        self.test.stop()
        self.status_label.setProperty("cssClass", "done")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setText("Fertig!")
        self.cancel_button.setEnabled(False)

        def show():
            try:
                self.main_window.show_results(self.test, self.patient_id)
            except Exception:
                log.exception("Error showing results")
                self.cancel_button.setEnabled(True)
                self.status_label.setText("Fehler bei der Auswertung")

        QTimer.singleShot(600, show)

    def _on_cancel(self) -> None:
        self.readiness_gate.cancel()
        self._ui_timer.stop()
        self.live_hand.setVisible(False)
        if self._recording:
            self._recording = False
            self.test.capture.stop_recording()
            self.test.stop()
        self.main_window.show_start()
