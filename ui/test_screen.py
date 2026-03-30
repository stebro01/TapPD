"""Test recording screen with hand detection, countdown, live plot."""

import logging
import threading
import time

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

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from motor_tests.base_test import BaseMotorTest
from motor_tests.config import get_test_config, get_hand_detection_config
from motor_tests.recorder import HandDetector, extract_metric
from ui.theme import PRIMARY, ACCENT, TEXT_SECONDARY

log = logging.getLogger(__name__)


class TestScreen(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.test: BaseMotorTest | None = None
        self.patient_id = ""
        self._recording = False
        self._detecting = False
        self._duration_reached = False
        self._last_frame_t: float = 0.0
        self._live_data_right: list[tuple[float, float]] = []
        self._live_data_left: list[tuple[float, float]] = []
        self._lock = threading.Lock()
        self._hand_detector: HandDetector | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 16)
        layout.setSpacing(10)

        # Top: text + image
        top = QHBoxLayout()
        top.setSpacing(20)

        self.instructions_label = QLabel()
        self.instructions_label.setWordWrap(True)
        self.instructions_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.instructions_label.setStyleSheet("font-size: 13px; line-height: 1.5;")
        self.instructions_label.setMinimumWidth(280)
        top.addWidget(self.instructions_label, stretch=1)

        self.instruction_image = QLabel()
        self.instruction_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.instruction_image.setFixedSize(330, 200)
        self.instruction_image.setScaledContents(True)
        self.instruction_image.setStyleSheet("border: 1px solid #E0E0E0; border-radius: 10px;")
        top.addWidget(self.instruction_image)

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

        # Live plot
        self.figure = Figure(figsize=(8, 2.5), facecolor="#FAFAFA")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor("#FAFAFA")
        layout.addWidget(self.canvas)

        # Cancel
        self.cancel_button = QPushButton("Abbrechen")
        self.cancel_button.setFixedWidth(140)
        self.cancel_button.clicked.connect(self._on_cancel)
        layout.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignCenter)

        self._ui_timer = QTimer()
        self._ui_timer.timeout.connect(self._update_ui)
        self._countdown_timer = QTimer()
        self._countdown_timer.timeout.connect(self._countdown_tick)
        self._detect_timer = QTimer()
        self._detect_timer.timeout.connect(self._detect_tick)
        self._countdown_value = 3
        self._detect_start_time = 0.0

    def start_test(self, test: BaseMotorTest, patient_id: str) -> None:
        self.test = test
        self.patient_id = patient_id
        self._live_data_right.clear()
        self._live_data_left.clear()
        self.progress_bar.setValue(0)
        self.instructions_label.setText(test.get_instructions())

        img_path = test.get_instruction_figure_path()
        if img_path:
            self.instruction_image.setPixmap(QPixmap(str(img_path)))
            self.instruction_image.setVisible(True)
        else:
            self.instruction_image.setVisible(False)

        self.ax.clear()
        self.ax.set_facecolor("#FAFAFA")
        self.canvas.draw()
        self.cancel_button.setEnabled(True)

        # Start hand detection phase (or skip for mock)
        from capture.mock_capture import MockCaptureDevice
        if isinstance(test.capture, MockCaptureDevice):
            # Skip hand detection for mock
            self._start_countdown()
        else:
            self._start_hand_detection()

    def _start_hand_detection(self) -> None:
        """Wait for hands to be detected above the sensor."""
        self._detecting = True
        self._hand_detector = HandDetector(
            self.test.capture,
            bilateral=self.test.bilateral,
            hand=self.test.hand,
        )

        cfg = get_hand_detection_config()
        self.status_label.setProperty("cssClass", "countdown")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setText(cfg.get("message_waiting", "Bitte Haende ueber den Sensor halten..."))
        self.progress_bar.setValue(0)

        self._detect_start_time = time.perf_counter()

        def detect_callback(frame):
            if self._hand_detector and not self._hand_detector.is_detected:
                self._hand_detector.check_frame(frame)

        self.test.capture.start_recording(detect_callback)
        poll_ms = cfg.get("poll_interval_ms", 100)
        self._detect_timer.start(poll_ms)

    def _detect_tick(self) -> None:
        if not self._detecting:
            return

        cfg = get_hand_detection_config()
        timeout = cfg.get("timeout_s", 30)
        elapsed = time.perf_counter() - self._detect_start_time

        if self._hand_detector and self._hand_detector.is_detected:
            # Hands found
            self._detecting = False
            self._detect_timer.stop()
            self.test.capture.stop_recording()
            self.status_label.setText(cfg.get("message_detected", "Haende erkannt!"))
            QTimer.singleShot(600, self._start_countdown)
            return

        # Update progress
        if self._hand_detector:
            progress = self._hand_detector.progress
            self.progress_bar.setValue(int(progress * 100))

        if elapsed > timeout:
            # Timeout
            self._detecting = False
            self._detect_timer.stop()
            self.test.capture.stop_recording()
            self.status_label.setText(cfg.get("message_timeout", "Keine Haende erkannt."))
            QTimer.singleShot(2000, lambda: self.main_window.show_start())

    def _start_countdown(self) -> None:
        self._countdown_value = 3
        self.status_label.setProperty("cssClass", "countdown")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setText(str(self._countdown_value))
        self._countdown_timer.start(1000)

    def _countdown_tick(self) -> None:
        self._countdown_value -= 1
        if self._countdown_value > 0:
            self.status_label.setText(str(self._countdown_value))
        else:
            self._countdown_timer.stop()
            self.status_label.setProperty("cssClass", "recording")
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
            self.status_label.setText("Aufnahme laeuft...")
            self._start_recording()

    def _start_recording(self) -> None:
        """Start the actual data recording.

        Uses wall-clock gating to discard stale frames that the LeapC SDK
        may have buffered internally during the detection/countdown phases.
        Frames arriving within SETTLE_S of start_recording() are discarded —
        they are delivered almost instantly from the SDK buffer, while genuine
        new frames arrive at the sensor's natural ~120 Hz cadence.
        """
        SETTLE_S = 0.25  # 250ms — discard stale buffered frames

        self._recording = True
        self._first_frame_us: int | None = None
        self._duration_reached = False
        self._last_frame_t: float = 0.0
        is_bilateral = self.test.bilateral
        duration_s = self.test.duration
        original_on_frame = self.test._on_frame
        record_wall_start = time.perf_counter()

        def callback(frame):
            try:
                # Wall-clock gate: discard stale LeapC buffer remnants
                if time.perf_counter() - record_wall_start < SETTLE_S:
                    return

                # Track first accepted frame as time origin
                if self._first_frame_us is None:
                    self._first_frame_us = frame.timestamp_us

                # Stop accepting frames beyond the configured duration
                elapsed_us = frame.timestamp_us - self._first_frame_us
                if elapsed_us > duration_s * 1_000_000:
                    self._duration_reached = True
                    return

                original_on_frame(frame)
                t = elapsed_us / 1_000_000
                self._last_frame_t = t
                metric = self.test.get_live_metric(frame)
                with self._lock:
                    if is_bilateral:
                        (self._live_data_right if frame.hand_type == "right"
                         else self._live_data_left).append((t, metric))
                    elif frame.hand_type == self.test.hand:
                        self._live_data_right.append((t, metric))
            except Exception:
                log.exception("Error in recording callback")

        self.test.capture.start_recording(callback)
        self._ui_timer.start(33)
        # Safety timeout: settle + duration + 3s margin
        QTimer.singleShot(int((SETTLE_S + duration_s + 3) * 1000), self._on_done)

    def _update_ui(self) -> None:
        if not self._recording:
            return

        # Check if recording duration reached (detected by frame callback)
        if self._duration_reached:
            self._on_done()
            return

        # Progress based on actual frame timestamps, not wall clock
        self.progress_bar.setValue(min(100, int(self._last_frame_t / self.test.duration * 100)))

        with self._lock:
            dr = list(self._live_data_right)
            dl = list(self._live_data_left)

        self.ax.clear()
        self.ax.set_facecolor("#FAFAFA")
        window = 5.0

        def windowed(data):
            if not data:
                return [], []
            t = [d[0] for d in data]
            v = [d[1] for d in data]
            if t[-1] > window:
                i = next((j for j, x in enumerate(t) if x > t[-1] - window), 0)
                return t[i:], v[i:]
            return t, v

        if dr:
            t, v = windowed(dr)
            label = "Rechts" if self.test.bilateral else None
            self.ax.plot(t, v, color=PRIMARY, linewidth=1.2, label=label)
        if dl:
            t, v = windowed(dl)
            self.ax.plot(t, v, color="#E53935", linewidth=1.2, label="Links")

        self.ax.set_ylabel(self.test.get_live_metric_label(), fontsize=9, color=TEXT_SECONDARY)
        self.ax.set_xlabel("Zeit (s)", fontsize=9, color=TEXT_SECONDARY)
        self.ax.tick_params(labelsize=8, colors=TEXT_SECONDARY)
        for spine in self.ax.spines.values():
            spine.set_color("#E0E0E0")
        if self.test.bilateral and (dr or dl):
            self.ax.legend(fontsize=8, frameon=False)
        self.figure.tight_layout()
        self.canvas.draw()

    def _on_done(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self._ui_timer.stop()
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
        self._countdown_timer.stop()
        self._detect_timer.stop()
        self._ui_timer.stop()
        if self._detecting:
            self._detecting = False
            self.test.capture.stop_recording()
        if self._recording:
            self._recording = False
            self.test.stop()
        self.main_window.show_start()
