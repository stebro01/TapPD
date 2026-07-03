"""Sakkaden-Screen: Vollflächen-Stimulus für den gaze-contingenten Test.

Phase 1: 5-Punkt-Eichung (Punkt wandert LO→RO→MI→LU→RU).
Phase 2: Zufallsziele — sobald der Blick das Ziel hält, springt es weiter.
Die gesamte Task-Logik lebt in paradigms/saccade_logic.py; dieser Screen
zeichnet nur den Zustand und startet/stoppt die Aufnahme.
"""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from paradigms.saccade_logic import POINTS, Phase
from paradigms.saccade_test import SaccadeTest
from ui import theme
from ui.theme import SZ

log = logging.getLogger(__name__)


class _StimulusCanvas(QWidget):
    """Draws the current target dot + status overlays."""

    def __init__(self) -> None:
        super().__init__()
        self.point_key: str | None = None
        self.is_calibration = False
        self.status_text = ""
        self.sub_text = ""
        self.face_ok = True
        self.setMinimumHeight(420)
        self.setStyleSheet("background-color: #202124;")

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#202124"))

        # status line (top centre)
        p.setPen(QPen(QColor("#E8EAED")))
        p.setFont(QFont("Helvetica Neue", 16, QFont.Weight.Bold))
        p.drawText(0, 16, w, 30, Qt.AlignmentFlag.AlignHCenter, self.status_text)
        p.setFont(QFont("Helvetica Neue", 11))
        p.setPen(QPen(QColor("#9AA0A6")))
        p.drawText(0, 46, w, 24, Qt.AlignmentFlag.AlignHCenter, self.sub_text)

        if not self.face_ok:
            p.setPen(QPen(QColor(theme.WARN)))
            p.setFont(QFont("Helvetica Neue", 12, QFont.Weight.Bold))
            p.drawText(0, h - 40, w, 24, Qt.AlignmentFlag.AlignHCenter,
                       "⚠ Gesicht nicht erkannt — Position/Beleuchtung prüfen")

        if self.point_key is None:
            return
        nx, ny = POINTS[self.point_key]
        # margin so corner dots stay fully visible
        x = int(30 + nx * (w - 60))
        y = int(70 + ny * (h - 110))
        color = QColor(theme.PRIMARY) if self.is_calibration else QColor(theme.ACCENT)
        p.setBrush(color)
        p.setPen(QPen(QColor("white"), 2))
        p.drawEllipse(x - 18, y - 18, 36, 36)
        p.setBrush(QColor("white"))
        p.drawEllipse(x - 4, y - 4, 8, 8)


class SaccadeScreen(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.test: SaccadeTest | None = None
        self._patient_code = ""
        self._running = False
        self._test_wall_start: float | None = None
        self._finished = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        self.instructions = QLabel("")
        self.instructions.setWordWrap(True)
        self.instructions.setStyleSheet("font-size: 14px;")
        layout.addWidget(self.instructions)

        self.canvas = _StimulusCanvas()
        layout.addWidget(self.canvas, stretch=1)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.setFixedHeight(SZ.BTN_H)
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch()
        self.start_btn = QPushButton("▶ Eichung starten")
        self.start_btn.setProperty("cssClass", "accent")
        self.start_btn.setFixedHeight(SZ.BTN_H)
        self.start_btn.setFixedWidth(240)
        self.start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self.start_btn)
        layout.addLayout(btn_row)

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(33)
        self._ui_timer.timeout.connect(self._tick)

    # ── entry (main_window routing contract) ──────────────────────
    def start_test(self, test: SaccadeTest, patient_code: str) -> None:
        self.test = test
        self._patient_code = patient_code
        self._running = False
        self._finished = False
        self._test_wall_start = None
        self.instructions.setText(test.get_instructions())
        self.instructions.setVisible(True)
        self.start_btn.setVisible(True)
        self.canvas.point_key = None
        self.canvas.status_text = "Bereit — »Eichung starten« drücken"
        self.canvas.sub_text = ""
        self.canvas.update()

    def _on_start(self) -> None:
        if self.test is None or self._running:
            return
        self._running = True
        self.instructions.setVisible(False)
        self.start_btn.setVisible(False)
        self.test.start()          # face stream on + recording (envelope path)
        self._ui_timer.start()

    # ── UI poll loop ──────────────────────────────────────────────
    def _tick(self) -> None:
        test = self.test
        if test is None:
            return
        task = test.task
        with test._lock:
            n_faces = len(test.face_frames)
            last_face = test.face_frames[-1] if test.face_frames else None
        self.canvas.face_ok = last_face is not None and n_faces > 3

        if task.phase is Phase.CALIBRATING:
            self.canvas.is_calibration = True
            self.canvas.point_key = task.calib_point
            idx = min(task.calib_index + 1, 5)
            self.canvas.status_text = f"Eichung — Punkt {idx}/5 fixieren"
            self.canvas.sub_text = "Kopf still halten, nur die Augen bewegen"
        elif task.phase is Phase.TESTING:
            if self._test_wall_start is None:
                self._test_wall_start = time.monotonic()
            self.canvas.is_calibration = False
            self.canvas.point_key = task.current_target
            remaining = max(0.0, task.duration_s -
                            (time.monotonic() - self._test_wall_start))
            self.canvas.status_text = f"Ziele: {len(task.hits)}"
            self.canvas.sub_text = f"Restzeit {remaining:.0f} s — so schnell wie möglich hinschauen"
        elif task.phase in (Phase.DONE, Phase.FAILED) and not self._finished:
            self._finish()
        self.canvas.update()

    def _finish(self) -> None:
        self._finished = True
        self._ui_timer.stop()
        self._running = False
        test = self.test
        try:
            test.stop()
        except Exception:
            log.exception("Sakkaden-Test: stop fehlgeschlagen")
        if test.task.phase is Phase.FAILED:
            self.canvas.point_key = None
            self.canvas.status_text = "Eichung fehlgeschlagen"
            self.canvas.sub_text = (test.task.fail_reason
                                    + " — »Eichung starten« für neuen Versuch")
            self.instructions.setVisible(True)
            self.start_btn.setVisible(True)
            # fresh paradigm instance for the retry
            self.test = SaccadeTest(test.capture, duration=test.duration,
                                    hand=test.hand)
            self._finished = False
            return
        self.main_window.show_results(test, self._patient_code)

    def _on_cancel(self) -> None:
        self._ui_timer.stop()
        self._running = False
        if self.test is not None:
            try:
                self.test.stop()
            except Exception:
                pass
        self.main_window.show_start()
