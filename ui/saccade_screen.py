"""Sakkaden-Screen: Vollflächen-Stimulus für den gaze-contingenten Test.

Phase 1: 5-Punkt-Eichung (Punkt wandert LO→RO→MI→LU→RU).
Phase 2: Zufallsziele — sobald der Blick das Ziel hält, springt es weiter.
Die gesamte Task-Logik lebt in paradigms/saccade_logic.py; dieser Screen
zeichnet nur den Zustand und startet/stoppt die Aufnahme.
"""

from __future__ import annotations

import logging
import os
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
        self.points = dict(POINTS)     # replaced by the task's layout on start
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
        nx, ny = self.points.get(self.point_key, (0.5, 0.5))
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

        # Stimulus canvas, with the debug panel (preview + tracking quality)
        # beside it when switched on.
        from ui.saccade_debug import SaccadeDebugPanel
        row = QHBoxLayout()
        self.canvas = _StimulusCanvas()
        row.addWidget(self.canvas, stretch=1)
        self.debug_panel = SaccadeDebugPanel()
        self.debug_panel.setVisible(False)
        row.addWidget(self.debug_panel)
        layout.addLayout(row, stretch=1)
        self._debug_log = None

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.setFixedHeight(SZ.BTN_H)
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addSpacing(16)
        from PyQt6.QtWidgets import QCheckBox
        self.debug_cb = QCheckBox("🔧 Debug: Vorschau, Tracking-Qualität, Video + Log aufzeichnen")
        self.debug_cb.setToolTip(
            "Zeigt das Kamerabild mit Augenpunkten und die Qualität des Eye-Trackings "
            "(Rate, IPD in Pixeln, Blick-Streuung) neben dem Stimulus. Zeichnet den Lauf "
            "als Video und alle Blick-Samples als JSON nach data/debug/saccade/ auf — "
            "die Eichung lässt sich damit offline nachbauen (ui/saccade_debug.replay_log).")
        try:
            from app_settings import app_settings
            self.debug_cb.setChecked(app_settings().value("saccade_debug", False, type=bool))
        except Exception:
            pass
        self.debug_cb.toggled.connect(self._on_debug_toggled)
        btn_row.addWidget(self.debug_cb)
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
        self.canvas.points = dict(test.task.points)
        self.instructions.setVisible(True)
        self.start_btn.setVisible(True)
        self.canvas.point_key = None
        self.canvas.status_text = "Bereit — »Eichung starten« drücken"
        self.canvas.sub_text = ""
        self.canvas.update()

    def _on_debug_toggled(self, on: bool) -> None:
        try:
            from app_settings import app_settings
            app_settings().setValue("saccade_debug", bool(on))
        except Exception:
            pass
        if not self._running:
            self.debug_panel.setVisible(bool(on))

    @property
    def debug_on(self) -> bool:
        return self.debug_cb.isChecked()

    def _on_start(self) -> None:
        if self.test is None or self._running:
            return
        self._running = True
        self.instructions.setVisible(False)
        self.start_btn.setVisible(False)
        if self.debug_on:
            self._start_debug()
        self.test.start()          # face stream on + recording (envelope path)
        self._ui_timer.start()

    # ── debug: preview + quality + recording ──────────────────────
    def _start_debug(self) -> None:
        from ui.saccade_debug import SaccadeDebugLog
        test = self.test
        cap = test.capture
        self.debug_panel.setVisible(True)
        self.debug_panel.files.setText("")
        try:
            setter = getattr(cap, "set_preview_callback", None)
            if setter is not None:
                setter(self.debug_panel.on_preview)
            en = getattr(cap, "enable_preview", None)
            if en is not None:
                en(True)
        except Exception:
            log.debug("Debug-Vorschau nicht möglich", exc_info=True)
        task = test.task
        seconds = (task.calib_per_point_s * 5) + task.duration_s + 5.0
        self._debug_log = SaccadeDebugLog(self._patient_code, cap, seconds)
        self.debug_panel.files.setText(
            "Aufzeichnung: " + os.path.basename(self._debug_log.json_path)
            + (" + Video" if self._debug_log.video_requested else " (kein Video)"))

    def _debug_tick(self) -> None:
        from ui.saccade_debug import quality
        test, dlog = self.test, self._debug_log
        if test is None or dlog is None:
            return
        dlog.collect(test)
        self.debug_panel.draw()
        with test._lock:
            faces = list(test.face_frames[-60:])
        task = test.task
        q = quality(faces, task)
        self.debug_panel.show_quality(q, task.phase.name, task.calib_point)

    def _end_debug(self, features: dict | None = None) -> None:
        dlog, self._debug_log = self._debug_log, None
        if dlog is None:
            return
        test = self.test
        try:
            path = dlog.finish(test, features)
            self.debug_panel.files.setText(
                f"Gespeichert: {os.path.basename(path)}"
                + (f" + {os.path.basename(dlog.video_path)}" if dlog.video_requested else "")
                + f"  ({len(dlog.samples)} Samples) → data/debug/saccade/")
            log.info("Sakkaden-Debug-Log: %s", path)
        except Exception:
            log.warning("Sakkaden-Debug-Log konnte nicht geschrieben werden", exc_info=True)
        try:
            cap = test.capture
            setter = getattr(cap, "set_preview_callback", None)
            if setter is not None:
                setter(None)
            en = getattr(cap, "enable_preview", None)
            if en is not None:
                en(False)
        except Exception:
            pass

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
        if self._debug_log is not None:
            self._debug_tick()

        if task.phase is Phase.CALIBRATING:
            self.canvas.is_calibration = True
            self.canvas.point_key = task.calib_point
            n = len(task.calib_order)
            idx = min(task.calib_index + 1, n)
            self.canvas.status_text = f"Eichung — Punkt {idx}/{n} fixieren"
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
        if self._debug_log is not None:
            try:
                feats = test.compute_features()
            except Exception:
                feats = {}
            self._end_debug(feats)
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
        if self._debug_log is not None:
            self._end_debug()
        self.main_window.show_start()
