"""Interactive digital Trail Making Test (dTMT) screen with hand tracking."""

import logging
import math
import threading
import time
from enum import Enum, auto

from PyQt6.QtCore import QRectF, QTimer, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from capture.base_capture import HandFrame
from motor_tests.tmt_logic import TMTTaskState, TMTSegmentResult, TARGET_ZONE_RADIUS
from motor_tests.trail_making import TrailMakingTest
from ui.theme import SZ, ACCENT, DANGER, PRIMARY, TEXT_SECONDARY

log = logging.getLogger(__name__)

COLOR_HAND = QColor(PRIMARY)
COLOR_VISITED = QColor("#43A047")
COLOR_ERROR = QColor(DANGER)
COLOR_TRAIL = QColor(ACCENT)

MOVEMENT_THRESHOLD = 40.0
DWELL_TIME_S = 1.0  # seconds to hold in target zone to confirm


def _to_screen_norm(frame: HandFrame) -> tuple[float, float]:
    nx = max(0.0, min(1.0, (frame.palm_position[0] + 150.0) / 300.0))
    ny = max(0.0, min(1.0, (frame.palm_position[2] + 80.0) / 160.0))
    return nx, ny


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


class TMTPhase(Enum):
    POSITIONING = auto()
    COUNTDOWN = auto()
    PLAYING = auto()
    DONE = auto()
    ABORTED = auto()


class SegmentState(Enum):
    WAITING = auto()       # waiting at completed target for movement
    MOVING = auto()        # tracking movement to next target
    IN_TARGET = auto()     # dwelling in correct target (filling up)


class TMTCanvas(QWidget):
    """Renders the Trail Making Test: numbered/lettered circles + trails + cursor."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(600, 500)
        self.task: TMTTaskState | None = None
        self.hand_x = 0.5
        self.hand_y = 0.5
        self.show_positioning = False
        self.hand_ok = False
        self.detected_hand: str | None = None
        self.trail_points: list[tuple[float, float]] = []  # visited target positions
        self.error_flash_target: int | None = None  # index of wrong target (brief flash)
        self.dwell_target_index: int | None = None  # target currently being dwelled on
        self.dwell_progress: float = 0.0            # 0..1 fill progress

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#FAFAFA"))

        if self.show_positioning:
            self._draw_positioning(p, w, h)
            p.end()
            return

        if not self.task:
            p.end()
            return

        # Draw trail lines between visited targets
        if len(self.trail_points) >= 2:
            p.setPen(QPen(COLOR_TRAIL, 2, Qt.PenStyle.SolidLine))
            for i in range(1, len(self.trail_points)):
                x1 = int(self.trail_points[i - 1][0] * w)
                y1 = int(self.trail_points[i - 1][1] * h)
                x2 = int(self.trail_points[i][0] * w)
                y2 = int(self.trail_points[i][1] * h)
                p.drawLine(x1, y1, x2, y2)

        # Draw target circles
        for t in self.task.targets:
            cx, cy = int(t.x * w), int(t.y * h)
            r = 22

            if t.visited:
                p.setPen(QPen(COLOR_VISITED, 2))
                p.setBrush(QBrush(QColor(200, 230, 201, 150)))
            elif self.error_flash_target == t.index:
                p.setPen(QPen(COLOR_ERROR, 3))
                p.setBrush(QBrush(QColor(255, 205, 210, 180)))
            else:
                p.setPen(QPen(QColor("#424242"), 1))
                p.setBrush(QBrush(QColor("#FFFFFF")))

            p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

            # Dwell progress — blue arc filling up clockwise
            if self.dwell_target_index == t.index and self.dwell_progress > 0:
                prog = max(0.0, min(1.0, self.dwell_progress))
                span = int(prog * 360 * 16)  # Qt uses 1/16th degree
                p.setPen(Qt.PenStyle.NoPen)
                alpha = int(60 + prog * 120)  # gets more opaque as it fills
                p.setBrush(QBrush(QColor(33, 150, 243, alpha)))  # blue fill
                p.drawPie(cx - r, cy - r, r * 2, r * 2, 90 * 16, -span)
                # Redraw border on top
                p.setPen(QPen(QColor(33, 150, 243), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

            # Label
            if t.visited:
                p.setPen(COLOR_VISITED)
            else:
                p.setPen(QColor("#333333"))
            p.setFont(QFont("Helvetica Neue", 13, QFont.Weight.Bold))
            p.drawText(QRectF(cx - r, cy - r, r * 2, r * 2),
                       Qt.AlignmentFlag.AlignCenter, t.label)

        # Hand cursor
        hx, hy = int(self.hand_x * w), int(self.hand_y * h)
        cr = 10
        p.setPen(QPen(COLOR_HAND, 2))
        p.setBrush(QBrush(QColor(COLOR_HAND.red(), COLOR_HAND.green(), COLOR_HAND.blue(), 80)))
        p.drawEllipse(hx - cr, hy - cr, cr * 2, cr * 2)
        p.setPen(QPen(COLOR_HAND, 1))
        p.drawLine(hx - 14, hy, hx + 14, hy)
        p.drawLine(hx, hy - 14, hx, hy + 14)

        p.end()

    def _draw_positioning(self, p: QPainter, w: int, h: int) -> None:
        p.setPen(QColor("#333333"))
        p.setFont(QFont("Helvetica Neue", 18, QFont.Weight.Bold))
        part = getattr(self.task, "part", "A") if self.task else "A"
        title = f"Trail Making Test — Teil {part}"
        p.drawText(QRectF(0, h * 0.04, w, 36),
                   Qt.AlignmentFlag.AlignCenter, title)

        # Task instructions
        if part == "A":
            instructions = [
                "Aufgabe:",
                "Auf dem Bildschirm erscheinen nummerierte Kreise (1, 2, 3, ...).",
                "Verbinden Sie die Zahlen in aufsteigender Reihenfolge:",
                "1 → 2 → 3 → 4 → ... und so weiter.",
                "",
                "Bewegen Sie Ihre Hand zum nächsten Kreis und",
                "verweilen Sie dort kurz, um ihn zu bestätigen.",
                "",
                "Arbeiten Sie so schnell und genau wie möglich.",
            ]
        else:
            instructions = [
                "Aufgabe:",
                "Auf dem Bildschirm erscheinen Kreise mit Zahlen und Buchstaben.",
                "Verbinden Sie diese abwechselnd in aufsteigender Reihenfolge:",
                "1 → A → 2 → B → 3 → C → ... und so weiter.",
                "",
                "Bewegen Sie Ihre Hand zum nächsten Kreis und",
                "verweilen Sie dort kurz, um ihn zu bestätigen.",
                "",
                "Arbeiten Sie so schnell und genau wie möglich.",
                "Bei einem Fehler wird der falsche Kreis rot markiert.",
            ]

        p.setFont(QFont("Helvetica Neue", 12))
        p.setPen(QColor("#333333"))
        line_h = 22
        start_y = h * 0.12
        for i, line in enumerate(instructions):
            if line == "":
                continue
            weight = QFont.Weight.Bold if line == "Aufgabe:" else QFont.Weight.Normal
            p.setFont(QFont("Helvetica Neue", 12, weight))
            p.drawText(QRectF(w * 0.1, start_y + i * line_h, w * 0.8, line_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       line)

        # Hand detection zone — compact, below instructions
        zone_w, zone_h = int(w * 0.26), int(h * 0.18)
        zone_x, zone_y = int(w * 0.37), int(h * 0.58)

        if self.hand_ok:
            p.setBrush(QBrush(QColor(ACCENT + "22")))
            p.setPen(QPen(QColor(ACCENT), 3))
        else:
            p.setBrush(QBrush(QColor("#F5F5F5")))
            p.setPen(QPen(QColor("#BDBDBD"), 2, Qt.PenStyle.DashLine))
        p.drawRoundedRect(zone_x, zone_y, zone_w, zone_h, 16, 16)

        label = ("Rechte Hand" if self.detected_hand == "right" else "Linke Hand") if self.hand_ok and self.detected_hand else "Hand"
        status = "Erkannt" if self.hand_ok else "Nicht erkannt"
        p.setPen(QColor(ACCENT) if self.hand_ok else QColor(TEXT_SECONDARY))
        p.setFont(QFont("Helvetica Neue", 13, QFont.Weight.DemiBold))
        p.drawText(QRectF(zone_x, zone_y + zone_h + 8, zone_w, 24),
                   Qt.AlignmentFlag.AlignCenter, label)
        p.setFont(QFont("Helvetica Neue", 11))
        p.drawText(QRectF(zone_x, zone_y + zone_h + 30, zone_w, 20),
                   Qt.AlignmentFlag.AlignCenter, status)

        if self.hand_ok:
            hx = int(self.hand_x * w)
            hy = zone_y + zone_h // 2
            p.setPen(QPen(COLOR_HAND, 2))
            p.setBrush(QBrush(QColor(COLOR_HAND.red(), COLOR_HAND.green(), COLOR_HAND.blue(), 100)))
            p.drawEllipse(hx - 12, hy - 12, 24, 24)

        # Bottom hint
        p.setPen(QColor(TEXT_SECONDARY))
        p.setFont(QFont("Helvetica Neue", 11))
        p.drawText(QRectF(0, h - 40, w, 25),
                   Qt.AlignmentFlag.AlignCenter,
                   "Hand flach über den Sensor halten – startet automatisch")


class TMTScreen(QWidget):
    """Digital Trail Making Test with single-hand tracking."""

    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.test: TrailMakingTest | None = None
        self._phase = TMTPhase.POSITIONING
        self._lock = threading.Lock()
        self._last_frame: HandFrame | None = None
        self._active_hand: str = "right"
        self._start_time: float = 0.0
        self._countdown_value = 3
        self._hand_ok_since: float | None = None

        self._dwell_start: float = 0.0
        self._dwell_target_idx: int | None = None  # which target we're dwelling on

        # Segment tracking
        self._seg_state = SegmentState.WAITING
        self._seg_start: float = 0.0
        self._movement_onset: float = 0.0
        self._path_length: float = 0.0
        self._peak_velocity: float = 0.0
        self._prev_pos: tuple[float, float] | None = None
        self._straight_start: tuple[float, float] = (0.5, 0.5)
        self._n_wrong_this_seg: int = 0
        self._error_flash_until: float = 0.0
        self._last_right_pos: HandFrame | None = None
        self._last_left_pos: HandFrame | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 16, 30, 16)
        layout.setSpacing(8)

        # Status bar
        status_row = QHBoxLayout()
        self.status_label = QLabel("Bereit")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"font-size: 13px; color: {TEXT_SECONDARY};")
        status_row.addWidget(self.progress_label)
        self.time_label = QLabel("Zeit: 0s")
        self.time_label.setStyleSheet(f"font-size: 13px; color: {TEXT_SECONDARY};")
        status_row.addWidget(self.time_label)
        layout.addLayout(status_row)

        # Countdown
        self.countdown_label = QLabel()
        self.countdown_label.setProperty("cssClass", "countdown")
        self.countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.countdown_label)

        # Canvas
        self.canvas = TMTCanvas()
        layout.addWidget(self.canvas, stretch=1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.setFixedHeight(SZ.BTN_H)
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._ui_timer = QTimer()
        self._ui_timer.timeout.connect(self._update_ui)
        self._countdown_timer = QTimer()
        self._countdown_timer.timeout.connect(self._countdown_tick)
        self._position_timer = QTimer()
        self._position_timer.timeout.connect(self._position_tick)

    # ── Start ──────────────────────────────────────────────────────

    def start_test(self, test: TrailMakingTest, patient_code: str) -> None:
        self.test = test
        self._patient_code = patient_code
        self._last_frame = None
        self._active_hand = "right"
        self._last_right_pos = None
        self._last_left_pos = None

        self.canvas.task = test.task
        self.canvas.show_positioning = True
        self.canvas.hand_ok = False
        self.canvas.detected_hand = None
        self.canvas.trail_points = []
        self.canvas.error_flash_target = None
        self.progress_label.setText("")
        self.time_label.setText("Zeit: 0s")
        self.cancel_btn.setEnabled(True)
        self.countdown_label.setVisible(False)
        self.status_label.setText("Hand positionieren")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: 700;")

        self._phase = TMTPhase.POSITIONING
        self._hand_ok_since = None

        def pos_callback(frame: HandFrame) -> None:
            with self._lock:
                if frame.hand_type == "right":
                    self._last_right_pos = frame
                elif frame.hand_type == "left":
                    self._last_left_pos = frame

        self.test.capture.start_recording(pos_callback)
        self._position_timer.start(100)

    def _position_tick(self) -> None:
        if self._phase != TMTPhase.POSITIONING:
            return
        with self._lock:
            right = self._last_right_pos
            left = self._last_left_pos

        r_ok = right is not None and right.palm_position[1] > 120 and right.confidence > 0
        l_ok = left is not None and left.palm_position[1] > 120 and left.confidence > 0

        if r_ok and l_ok:
            chosen, frame = "right", right
        elif r_ok:
            chosen, frame = "right", right
        elif l_ok:
            chosen, frame = "left", left
        else:
            chosen, frame = None, None

        hand_ok = chosen is not None
        self.canvas.hand_ok = hand_ok
        self.canvas.detected_hand = chosen
        if frame:
            nx, ny = _to_screen_norm(frame)
            self.canvas.hand_x = nx
            self.canvas.hand_y = ny
        self.canvas.update()

        if hand_ok:
            if self._hand_ok_since is None:
                self._hand_ok_since = time.perf_counter()
                self._active_hand = chosen
            elif time.perf_counter() - self._hand_ok_since > 1.0:
                self._active_hand = chosen
                self._position_timer.stop()
                self.test.capture.stop_recording()
                self.canvas.show_positioning = False
                self.test.hand = self._active_hand
                self._start_countdown()
        else:
            self._hand_ok_since = None

    # ── Countdown ──────────────────────────────────────────────────

    def _start_countdown(self) -> None:
        self._phase = TMTPhase.COUNTDOWN
        self._countdown_value = 3
        self.countdown_label.setVisible(True)
        self.countdown_label.setText(str(self._countdown_value))
        self.status_label.setText("Vorbereitung...")
        self._countdown_timer.start(1000)

    def _countdown_tick(self) -> None:
        self._countdown_value -= 1
        if self._countdown_value > 0:
            self.countdown_label.setText(str(self._countdown_value))
        else:
            self._countdown_timer.stop()
            self.countdown_label.setVisible(False)
            self._start_task()

    # ── Task ───────────────────────────────────────────────────────

    def _start_task(self) -> None:
        self._phase = TMTPhase.PLAYING
        self._start_time = time.perf_counter()
        self.test._start_time_s = self._start_time
        self.test.task._start_time_s = self._start_time
        self.status_label.setText(
            f"Trail Making — Teil {self.test.part}")
        self.canvas.update()

        active = self._active_hand

        def callback(frame: HandFrame) -> None:
            try:
                if frame.hand_type != active:
                    return
                self.test._on_frame(frame)
                with self._lock:
                    self._last_frame = frame
            except Exception:
                log.exception("Error in TMT callback")

        self.test.capture.start_recording(callback)
        self._ui_timer.start(33)

        # Begin first segment
        self._begin_segment()

    def _begin_segment(self) -> None:
        self._seg_state = SegmentState.WAITING
        self._seg_start = time.perf_counter()
        self._movement_onset = 0.0
        self._path_length = 0.0
        self._peak_velocity = 0.0
        self._prev_pos = None
        self._n_wrong_this_seg = 0

        # Record straight-line start position
        with self._lock:
            f = self._last_frame
        if f:
            self._straight_start = _to_screen_norm(f)

    def _update_ui(self) -> None:
        if self._phase != TMTPhase.PLAYING:
            return

        with self._lock:
            frame = self._last_frame

        if frame is None:
            return

        now = time.perf_counter()
        hand_pos = _to_screen_norm(frame)
        self.canvas.hand_x, self.canvas.hand_y = hand_pos

        vel = math.sqrt(
            frame.palm_velocity[0] ** 2 +
            frame.palm_velocity[1] ** 2 +
            frame.palm_velocity[2] ** 2
        )

        task = self.test.task
        current = task.current_target

        # Clear error flash
        if self._error_flash_until and now > self._error_flash_until:
            self.canvas.error_flash_target = None
            self._error_flash_until = 0.0

        if current is None:
            self._on_complete()
            return

        # Waiting for movement
        if self._seg_state == SegmentState.WAITING:
            if vel > MOVEMENT_THRESHOLD:
                self._seg_state = SegmentState.MOVING
                self._movement_onset = now
                self._prev_pos = hand_pos
                self._straight_start = hand_pos

        # Moving — track path, check for dwell in target zone
        elif self._seg_state == SegmentState.MOVING:
            if self._prev_pos:
                dx_mm = (hand_pos[0] - self._prev_pos[0]) * 400.0
                dy_mm = (hand_pos[1] - self._prev_pos[1]) * 200.0
                self._path_length += math.sqrt(dx_mm * dx_mm + dy_mm * dy_mm)
            self._prev_pos = hand_pos
            if vel > self._peak_velocity:
                self._peak_velocity = vel

            # Check if cursor entered any unvisited target zone
            for t in task.targets:
                if t.visited:
                    continue
                dist = _dist(hand_pos, (t.x, t.y))
                if dist < TARGET_ZONE_RADIUS:
                    self._seg_state = SegmentState.IN_TARGET
                    self._dwell_start = now
                    self._dwell_target_idx = t.index
                    self.canvas.dwell_target_index = t.index
                    self.canvas.dwell_progress = 0.0
                    break

        # In target — dwell progress (same for correct and wrong)
        elif self._seg_state == SegmentState.IN_TARGET:
            t = task.targets[self._dwell_target_idx]
            dist = _dist(hand_pos, (t.x, t.y))
            if dist >= TARGET_ZONE_RADIUS * 1.3:
                # Left the zone — reset, no error
                self._seg_state = SegmentState.MOVING
                self.canvas.dwell_target_index = None
                self.canvas.dwell_progress = 0.0
            else:
                progress = (now - self._dwell_start) / DWELL_TIME_S
                self.canvas.dwell_progress = progress
                if progress >= 1.0:
                    self.canvas.dwell_target_index = None
                    self.canvas.dwell_progress = 0.0
                    if t.index == task.current_index:
                        # Correct target — accept
                        self._complete_segment(now, hand_pos)
                    else:
                        # Wrong target — error
                        self._n_wrong_this_seg += 1
                        task.record_wrong_approach(now - self._start_time, t.index)
                        self.canvas.error_flash_target = t.index
                        self._error_flash_until = now + 0.5
                        self._seg_state = SegmentState.MOVING

        # Update labels
        elapsed = now - self._start_time
        self.time_label.setText(f"Zeit: {int(elapsed)}s")
        self.progress_label.setText(f"{task.current_index}/{len(task.targets)}")

        self.canvas.update()

    def _complete_segment(self, now: float, hand_pos: tuple[float, float]) -> None:
        task = self.test.task
        current = task.current_target

        dx_mm = (hand_pos[0] - self._straight_start[0]) * 400.0
        dy_mm = (hand_pos[1] - self._straight_start[1]) * 200.0
        straight_dist = math.sqrt(dx_mm * dx_mm + dy_mm * dy_mm)

        react_ms = (self._movement_onset - self._seg_start) * 1000 if self._movement_onset > 0 else 0
        arrival = self._dwell_start
        move_ms = (arrival - self._movement_onset) * 1000 if self._movement_onset > 0 else 0

        result = TMTSegmentResult(
            from_index=max(0, task.current_index - 1),
            to_index=task.current_index,
            start_s=self._seg_start - self._start_time,
            movement_onset_s=(self._movement_onset - self._start_time) if self._movement_onset > 0 else 0,
            arrival_s=arrival - self._start_time,
            dwell_complete_s=now - self._start_time,
            reaction_time_ms=react_ms,
            movement_time_ms=move_ms,
            path_length_mm=self._path_length,
            straight_distance_mm=straight_dist,
            peak_velocity_mm_s=self._peak_velocity,
            n_wrong_approaches=self._n_wrong_this_seg,
        )
        task.record_segment(result)

        # Mark target visited and add trail point
        task.visit_target(task.current_index)
        self.canvas.trail_points.append((current.x, current.y))

        if task.is_complete():
            self._on_complete()
        else:
            self._begin_segment()

    # ── End states ─────────────────────────────────────────────────

    def _on_complete(self) -> None:
        self._phase = TMTPhase.DONE
        self._ui_timer.stop()
        self.test.capture.stop_recording()
        self.test.mark_completed(time.perf_counter())
        self.canvas.update()

        elapsed = time.perf_counter() - self._start_time
        n_errors = len(self.test.task.wrong_approaches)

        self.status_label.setText("Abgeschlossen!")
        self.status_label.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {ACCENT};")
        self.cancel_btn.setEnabled(False)

        QTimer.singleShot(800, lambda: self._show_done_dialog(elapsed, n_errors))

    def _show_done_dialog(self, elapsed: float, n_errors: int) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Trail Making abgeschlossen")
        dlg.setFixedSize(380, 260)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        title = QLabel(f"Teil {self.test.part} abgeschlossen!")
        title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {ACCENT};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        info = QLabel(
            f"Zeit: {elapsed:.1f}s\n"
            f"Fehler: {n_errors}"
        )
        info.setStyleSheet(f"font-size: 13px; color: {TEXT_SECONDARY};")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(info)

        lay.addSpacing(8)
        save_btn = QPushButton("Speichern")
        save_btn.setProperty("cssClass", "primary")
        save_btn.setFixedHeight(SZ.DIALOG_BTN_H)
        save_btn.clicked.connect(lambda: (dlg.accept(), self._show_results()))
        lay.addWidget(save_btn)

        dlg.exec()

    def _on_cancel(self) -> None:
        self._countdown_timer.stop()
        self._ui_timer.stop()
        self._position_timer.stop()
        if self._phase in (TMTPhase.PLAYING, TMTPhase.POSITIONING):
            self.test.capture.stop_recording()
        self._phase = TMTPhase.ABORTED
        self.test.mark_aborted(time.perf_counter())
        self.main_window.show_start()

    def _show_results(self) -> None:
        self.status_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.main_window.show_results(self.test, self._patient_code)
