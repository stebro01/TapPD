"""Interactive Spatial Serial Reaction Time (S-SRT) screen with hand tracking."""

import logging
import math
import threading
import time
from enum import Enum, auto

from PyQt6.QtCore import QRectF, QTimer, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from capture.base_capture import HandFrame
from motor_tests.srt_logic import SRTTaskState, SRTTrialResult, TARGET_POSITIONS, TARGET_ZONE_RADIUS
from motor_tests.spatial_srt import SpatialSRTTest
from ui.theme import ACCENT, DANGER, PRIMARY, TEXT_SECONDARY

log = logging.getLogger(__name__)

# Target colors
TARGET_COLORS = ["#E53935", "#1E88E5", "#43A047", "#FB8C00"]  # top, right, bottom, left
TARGET_ACTIVE_COLOR = "#FFD600"  # bright yellow for active target
COLOR_HAND = QColor(PRIMARY)

# Movement detection threshold (mm/s)
MOVEMENT_THRESHOLD = 50.0
# Dwell time to register hit (seconds)
DWELL_TIME_S = 0.30
# Inter-stimulus interval (seconds)
ISI_S = 0.40


def _palm_to_screen_norm(frame: HandFrame) -> tuple[float, float]:
    """Map Leap palm position to normalized screen coords.
    X: -200..+200mm -> 0..1, Z: -100..+100mm -> 0..1."""
    nx = max(0.0, min(1.0, (frame.palm_position[0] + 200.0) / 400.0))
    ny = max(0.0, min(1.0, (frame.palm_position[2] + 100.0) / 200.0))
    return nx, ny


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


class SRTPhase(Enum):
    POSITIONING = auto()
    COUNTDOWN = auto()
    PLAYING = auto()
    DONE = auto()
    ABORTED = auto()


class TrialState(Enum):
    ISI = auto()              # inter-stimulus interval
    STIMULUS_ON = auto()      # target visible, waiting for movement
    MOVING = auto()           # movement detected, tracking path
    IN_TARGET = auto()        # hand in target zone, dwelling


class SRTCanvas(QWidget):
    """Renders the S-SRT task: 4 targets + hand cursor."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(600, 500)
        self.active_target: int | None = None
        self.hand_x = 0.5
        self.hand_y = 0.5
        self.show_positioning = False
        self.hand_ok = False
        self.detected_hand: str | None = None
        self.block_label = ""
        self.trial_label = ""
        self.show_isi = False  # blank between trials

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        p.fillRect(0, 0, w, h, QColor("#FAFAFA"))

        if self.show_positioning:
            self._draw_positioning(p, w, h)
            p.end()
            return

        # Block/trial info
        p.setPen(QColor(TEXT_SECONDARY))
        p.setFont(QFont("Helvetica Neue", 10))
        p.drawText(QRectF(10, 5, 200, 20), Qt.AlignmentFlag.AlignLeft, self.block_label)
        p.drawText(QRectF(w - 120, 5, 110, 20), Qt.AlignmentFlag.AlignRight, self.trial_label)

        if self.show_isi:
            # Show nothing during ISI, just background + cursor
            self._draw_cursor(p, w, h)
            p.end()
            return

        # Draw target circles
        for i, (tx, ty) in enumerate(TARGET_POSITIONS):
            cx, cy = int(tx * w), int(ty * h)
            is_active = (i == self.active_target)
            r = 36 if is_active else 28

            if is_active:
                # Glow effect
                glow = QColor(TARGET_ACTIVE_COLOR)
                glow.setAlpha(40)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(glow))
                p.drawEllipse(cx - r - 12, cy - r - 12, (r + 12) * 2, (r + 12) * 2)
                # Main circle
                p.setBrush(QBrush(QColor(TARGET_ACTIVE_COLOR)))
                p.setPen(QPen(QColor("#F57F17"), 3))
            else:
                color = QColor(TARGET_COLORS[i])
                color.setAlpha(80)
                p.setBrush(QBrush(color))
                p.setPen(QPen(QColor(TARGET_COLORS[i]), 2))

            p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

            # Target label
            p.setPen(QColor("white") if is_active else QColor(TARGET_COLORS[i]))
            p.setFont(QFont("Helvetica Neue", 14 if is_active else 11, QFont.Weight.Bold))
            p.drawText(QRectF(cx - r, cy - r, r * 2, r * 2),
                       Qt.AlignmentFlag.AlignCenter, str(i + 1))

        # Hand cursor
        self._draw_cursor(p, w, h)
        p.end()

    def _draw_cursor(self, p: QPainter, w: int, h: int) -> None:
        hx, hy = int(self.hand_x * w), int(self.hand_y * h)
        r = 10
        p.setPen(QPen(COLOR_HAND, 2))
        p.setBrush(QBrush(QColor(COLOR_HAND.red(), COLOR_HAND.green(), COLOR_HAND.blue(), 80)))
        p.drawEllipse(hx - r, hy - r, r * 2, r * 2)
        # Crosshair
        p.setPen(QPen(COLOR_HAND, 1))
        p.drawLine(hx - 14, hy, hx + 14, hy)
        p.drawLine(hx, hy - 14, hx, hy + 14)

    def _draw_positioning(self, p: QPainter, w: int, h: int) -> None:
        p.setPen(QColor("#333333"))
        p.setFont(QFont("Helvetica Neue", 18, QFont.Weight.Bold))
        p.drawText(QRectF(0, h * 0.04, w, 32),
                   Qt.AlignmentFlag.AlignCenter, "Raeumliche Reaktionszeit (S-SRT)")

        # Instructions
        p.setFont(QFont("Helvetica Neue", 11))
        p.setPen(QColor(TEXT_SECONDARY))
        instr_x = int(w * 0.10)
        instr_w = int(w * 0.80)
        instructions = [
            "Aufgabe:",
            "Auf dem Bildschirm erscheinen 4 Ziele (oben, rechts, unten, links).",
            "Eines der Ziele leuchtet gelb auf.",
            "",
            "Bewegen Sie Ihre Hand so schnell wie moeglich zum",
            "leuchtenden Ziel und halten Sie dort kurz an.",
            "",
            "Danach leuchtet das naechste Ziel auf.",
            "Versuchen Sie, moeglichst schnell und genau zu reagieren.",
            "",
            "Es beginnt mit einer kurzen Uebungsphase.",
        ]
        line_y = h * 0.12
        for line in instructions:
            if line == "Aufgabe:":
                p.setFont(QFont("Helvetica Neue", 11, QFont.Weight.DemiBold))
                p.setPen(QColor("#333333"))
            elif line == "":
                line_y += 6
                continue
            else:
                p.setFont(QFont("Helvetica Neue", 11))
                p.setPen(QColor(TEXT_SECONDARY))
            p.drawText(QRectF(instr_x, line_y, instr_w, 20),
                       Qt.AlignmentFlag.AlignCenter, line)
            line_y += 18

        # Hand detection zone (compact, below instructions)
        zone_w, zone_h = int(w * 0.22), int(h * 0.18)
        zone_x = int(w * 0.39)
        zone_y = int(line_y + 16)

        if self.hand_ok:
            p.setBrush(QBrush(QColor(ACCENT + "22")))
            p.setPen(QPen(QColor(ACCENT), 3))
        else:
            p.setBrush(QBrush(QColor("#F5F5F5")))
            p.setPen(QPen(QColor("#BDBDBD"), 2, Qt.PenStyle.DashLine))
        p.drawRoundedRect(zone_x, zone_y, zone_w, zone_h, 12, 12)

        label = ("Rechte Hand" if self.detected_hand == "right" else "Linke Hand") if self.hand_ok and self.detected_hand else "Hand"
        status = "Erkannt" if self.hand_ok else "Nicht erkannt"
        p.setPen(QColor(ACCENT) if self.hand_ok else QColor(TEXT_SECONDARY))
        p.setFont(QFont("Helvetica Neue", 12, QFont.Weight.DemiBold))
        p.drawText(QRectF(zone_x, zone_y + zone_h + 6, zone_w, 20),
                   Qt.AlignmentFlag.AlignCenter, label)
        p.setFont(QFont("Helvetica Neue", 10))
        p.drawText(QRectF(zone_x, zone_y + zone_h + 24, zone_w, 18),
                   Qt.AlignmentFlag.AlignCenter, status)

        # Hint at bottom
        p.setFont(QFont("Helvetica Neue", 10))
        p.setPen(QColor("#BDBDBD"))
        p.drawText(QRectF(0, h - 30, w, 20),
                   Qt.AlignmentFlag.AlignCenter,
                   "Hand flach ueber den Sensor halten – startet automatisch")

        if self.hand_ok:
            hx = int(self.hand_x * w)
            hy = zone_y + zone_h // 2
            p.setPen(QPen(COLOR_HAND, 2))
            p.setBrush(QBrush(QColor(COLOR_HAND.red(), COLOR_HAND.green(), COLOR_HAND.blue(), 100)))
            p.drawEllipse(hx - 10, hy - 10, 20, 20)


class SRTScreen(QWidget):
    """Spatial SRT task with single-hand tracking."""

    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.test: SpatialSRTTest | None = None
        self._phase = SRTPhase.POSITIONING
        self._lock = threading.Lock()
        self._last_frame: HandFrame | None = None
        self._active_hand: str = "right"
        self._start_time: float = 0.0
        self._countdown_value = 3
        self._hand_ok_since: float | None = None

        # Trial tracking
        self._trial_state = TrialState.ISI
        self._stimulus_onset: float = 0.0
        self._movement_onset: float = 0.0
        self._dwell_start: float = 0.0
        self._isi_start: float = 0.0
        self._path_length: float = 0.0
        self._straight_start: tuple[float, float] = (0.0, 0.0)
        self._peak_velocity: float = 0.0
        self._prev_pos: tuple[float, float] | None = None
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
        self.canvas = SRTCanvas()
        layout.addWidget(self.canvas, stretch=1)

        # Hint
        self.hint_label = QLabel("Hand zum leuchtenden Ziel bewegen und kurz halten")
        self.hint_label.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY};")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint_label)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Timers
        self._ui_timer = QTimer()
        self._ui_timer.timeout.connect(self._update_ui)
        self._countdown_timer = QTimer()
        self._countdown_timer.timeout.connect(self._countdown_tick)
        self._position_timer = QTimer()
        self._position_timer.timeout.connect(self._position_tick)

    # ── Start ──────────────────────────────────────────────────────

    def start_test(self, test: SpatialSRTTest, patient_code: str) -> None:
        self.test = test
        self._patient_code = patient_code
        self._last_frame = None
        self._active_hand = "right"
        self._last_right_pos = None
        self._last_left_pos = None

        self.canvas.active_target = None
        self.canvas.show_positioning = True
        self.canvas.hand_ok = False
        self.canvas.detected_hand = None
        self.canvas.show_isi = False
        self.canvas.block_label = ""
        self.canvas.trial_label = ""
        self.progress_label.setText("")
        self.time_label.setText("Zeit: 0s")
        self.cancel_btn.setEnabled(True)
        self.countdown_label.setVisible(False)
        self.hint_label.setVisible(False)
        self.status_label.setText("Hand positionieren")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: 700;")

        self._phase = SRTPhase.POSITIONING
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
        if self._phase != SRTPhase.POSITIONING:
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
            nx, ny = _palm_to_screen_norm(frame)
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
        self._phase = SRTPhase.COUNTDOWN
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
        self._phase = SRTPhase.PLAYING
        self._start_time = time.perf_counter()
        self.test._start_time_s = self._start_time
        self.status_label.setText("Aufgabe laeuft")
        self.hint_label.setVisible(True)
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
                log.exception("Error in SRT callback")

        self.test.capture.start_recording(callback)
        self._ui_timer.start(33)

        # Start first trial
        self._begin_trial()

    def _begin_trial(self) -> None:
        """Start a new trial: show target after brief ISI."""
        target = self.test.task.current_target()
        if target is None:
            self._on_complete()
            return

        self._trial_state = TrialState.ISI
        self._isi_start = time.perf_counter()
        self.canvas.active_target = None
        self.canvas.show_isi = True

    def _activate_stimulus(self) -> None:
        """Light up the current target."""
        target = self.test.task.current_target()
        if target is None:
            return
        self._trial_state = TrialState.STIMULUS_ON
        self._stimulus_onset = time.perf_counter()
        self._movement_onset = 0.0
        self._path_length = 0.0
        self._peak_velocity = 0.0
        self._prev_pos = None
        self._dwell_start = 0.0
        self.canvas.active_target = target
        self.canvas.show_isi = False

        # Record starting hand position
        with self._lock:
            f = self._last_frame
        if f:
            self._straight_start = _palm_to_screen_norm(f)

    def _update_ui(self) -> None:
        if self._phase != SRTPhase.PLAYING:
            return

        with self._lock:
            frame = self._last_frame

        if frame is None:
            return

        now = time.perf_counter()
        hand_pos = _palm_to_screen_norm(frame)
        self.canvas.hand_x, self.canvas.hand_y = hand_pos

        # Velocity magnitude from palm_velocity
        vel = math.sqrt(
            frame.palm_velocity[0] ** 2 +
            frame.palm_velocity[1] ** 2 +
            frame.palm_velocity[2] ** 2
        )

        # ISI phase
        if self._trial_state == TrialState.ISI:
            if now - self._isi_start >= ISI_S:
                self._activate_stimulus()

        # Stimulus on — waiting for movement
        elif self._trial_state == TrialState.STIMULUS_ON:
            if vel > MOVEMENT_THRESHOLD:
                self._trial_state = TrialState.MOVING
                self._movement_onset = now
                self._prev_pos = hand_pos
                self._straight_start = hand_pos

        # Moving — tracking path
        elif self._trial_state == TrialState.MOVING:
            # Accumulate path length (in mm, approximate from normalized coords)
            if self._prev_pos:
                dx_mm = (hand_pos[0] - self._prev_pos[0]) * 400.0  # 400mm X range
                dy_mm = (hand_pos[1] - self._prev_pos[1]) * 200.0  # 200mm Z range
                self._path_length += math.sqrt(dx_mm * dx_mm + dy_mm * dy_mm)
            self._prev_pos = hand_pos
            if vel > self._peak_velocity:
                self._peak_velocity = vel

            # Check if hand entered correct target zone
            target_id = self.test.task.current_target()
            if target_id is not None:
                target_pos = TARGET_POSITIONS[target_id]
                dist = _dist(hand_pos, target_pos)
                if dist < TARGET_ZONE_RADIUS:
                    self._trial_state = TrialState.IN_TARGET
                    self._dwell_start = now

        # In target — dwelling
        elif self._trial_state == TrialState.IN_TARGET:
            target_id = self.test.task.current_target()
            if target_id is not None:
                target_pos = TARGET_POSITIONS[target_id]
                dist = _dist(hand_pos, target_pos)
                if dist >= TARGET_ZONE_RADIUS * 1.3:  # hysteresis
                    # Left target zone, go back to moving
                    self._trial_state = TrialState.MOVING
                elif now - self._dwell_start >= DWELL_TIME_S:
                    # Dwell complete — trial success
                    self._complete_trial(now, hand_pos, target_id, correct=True)

        # Update labels
        task = self.test.task
        self.canvas.block_label = task.block_label()
        trial_in_block = task.current_trial_in_block + 1
        block = task.current_block
        total_in_block = block.n_trials if block else 0
        self.canvas.trial_label = f"Trial {trial_in_block}/{total_in_block}"

        elapsed = now - self._start_time
        self.time_label.setText(f"Zeit: {int(elapsed)}s")
        done = task.completed_trials
        total = task.total_trials
        self.progress_label.setText(f"{done}/{total}")

        self.canvas.update()

    def _complete_trial(self, now: float, hand_pos: tuple[float, float],
                        target_id: int, correct: bool) -> None:
        """Record trial result and advance."""
        task = self.test.task
        block = task.current_block

        # Straight-line distance
        dx_mm = (hand_pos[0] - self._straight_start[0]) * 400.0
        dy_mm = (hand_pos[1] - self._straight_start[1]) * 200.0
        straight_dist = math.sqrt(dx_mm * dx_mm + dy_mm * dy_mm)

        react_ms = (self._movement_onset - self._stimulus_onset) * 1000 if self._movement_onset > 0 else 0
        move_ms = (now - self._dwell_start + DWELL_TIME_S - (self._movement_onset if self._movement_onset > 0 else self._stimulus_onset)) * 1000
        # More precise: arrival = dwell_start, movement_time = arrival - movement_onset
        arrival = self._dwell_start
        move_ms = (arrival - self._movement_onset) * 1000 if self._movement_onset > 0 else 0
        total_ms = (now - self._stimulus_onset) * 1000

        result = SRTTrialResult(
            trial_index=task._global_trial_index,
            block_index=block.block_index if block else 0,
            block_type=block.block_type if block else "random",
            target_id=target_id,
            stimulus_onset_s=self._stimulus_onset - self._start_time,
            movement_onset_s=(self._movement_onset - self._start_time) if self._movement_onset > 0 else 0,
            arrival_s=arrival - self._start_time,
            dwell_complete_s=now - self._start_time,
            reaction_time_ms=react_ms,
            movement_time_ms=move_ms,
            total_response_time_ms=total_ms,
            path_length_mm=self._path_length,
            straight_distance_mm=straight_dist,
            peak_velocity_mm_s=self._peak_velocity,
            correct=correct,
            sequence_position=task.current_sequence_position(),
        )
        task.record_trial(result)

        has_more = task.advance_trial()
        if not has_more:
            self._on_complete()
        else:
            self._begin_trial()

    # ── End states ─────────────────────────────────────────────────

    def _on_complete(self) -> None:
        self._phase = SRTPhase.DONE
        self._ui_timer.stop()
        self.test.capture.stop_recording()
        self.test.mark_completed(time.perf_counter())

        self.canvas.active_target = None
        self.canvas.show_isi = True
        self.canvas.update()

        self.status_label.setText("Abgeschlossen!")
        self.status_label.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {ACCENT};")
        self.cancel_btn.setEnabled(False)

        QTimer.singleShot(1000, self._show_results)

    def _on_cancel(self) -> None:
        self._countdown_timer.stop()
        self._ui_timer.stop()
        self._position_timer.stop()
        if self._phase in (SRTPhase.PLAYING, SRTPhase.POSITIONING):
            self.test.capture.stop_recording()
        self._phase = SRTPhase.ABORTED
        self.test.mark_aborted(time.perf_counter())
        self.main_window.show_start()

    def _show_results(self) -> None:
        self.status_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.main_window.show_results(self.test, self._patient_code)
