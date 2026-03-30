"""Interactive Tower of Hanoi game screen with hand tracking."""

import logging
import threading
import time
from enum import Enum, auto

from PyQt6.QtCore import QTimer, Qt, QRectF
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
from motor_tests.pinch_detector import PinchDetector, PinchEvent
from motor_tests.tower_of_hanoi import TowerOfHanoiTest
from ui.theme import ACCENT, DANGER, PRIMARY, PRIMARY_LIGHT, TEXT_SECONDARY

log = logging.getLogger(__name__)

# Disc colors
DISC_COLORS = ["#E53935", "#FB8C00", "#FDD835", "#43A047", "#1E88E5", "#8E24AA", "#00ACC1"]

# Peg X positions in Leap coordinate space (mm)
PEG_X_MM = [-100.0, 0.0, 100.0]
PEG_ZONE_HALF = 65.0

# Hand colors
COLOR_RIGHT = QColor(PRIMARY)
COLOR_LEFT = QColor("#E53935")


def _x_to_norm(x_mm: float) -> float:
    """Map Leap X (-200..+200mm) to normalized 0..1."""
    return max(0.0, min(1.0, (x_mm + 200.0) / 400.0))


def _x_to_peg(x_mm: float) -> int:
    """Which peg is the hand over? -1 if none."""
    for i, px in enumerate(PEG_X_MM):
        if abs(x_mm - px) < PEG_ZONE_HALF:
            return i
    return -1


class GamePhase(Enum):
    POSITIONING = auto()
    COUNTDOWN = auto()
    PLAYING = auto()
    SOLVED = auto()
    ABORTED = auto()


class HanoiCanvas(QWidget):
    """Custom widget that renders the Tower of Hanoi game board."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(700, 400)
        self.game = None
        self.held_disc = None
        self.held_from_peg = 0
        # Active hand position (normalized 0..1)
        self.hand_x = 0.5
        self.hand_pinching = False
        self.hand_peg = -1  # which peg the hand is over
        self.active_hand = "right"  # which hand is being used
        self.flash_color = None
        self.flash_timer = 0.0
        # Positioning mode overlay
        self.show_positioning = False
        self.hand_ok = False
        self.detected_hand: str | None = None  # which hand was detected

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        if self.show_positioning:
            self._draw_positioning(p, w, h)
            p.end()
            return

        if not self.game:
            p.end()
            return

        # Flash feedback
        if self.flash_color and self.flash_timer > 0:
            p.fillRect(0, 0, w, h, QColor(
                self.flash_color.red(), self.flash_color.green(),
                self.flash_color.blue(), 30))

        # Base
        base_y = int(h * 0.82)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#9E9E9E")))
        p.drawRoundedRect(int(w * 0.08), base_y, int(w * 0.84), 6, 3, 3)

        # Pegs
        peg_xs = [int(w * 0.22), int(w * 0.50), int(w * 0.78)]
        peg_w = 4
        peg_h = int(h * 0.45)
        peg_top = base_y - peg_h

        for i, px in enumerate(peg_xs):
            # Determine highlight for this peg
            highlight = self._peg_highlight(i)
            if highlight:
                h_border, h_fill = highlight
                p.setBrush(QBrush(h_fill))
                p.setPen(QPen(h_border, 2, Qt.PenStyle.DashLine))
                p.drawRoundedRect(px - 55, peg_top - 10, 110, peg_h + 20, 8, 8)

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor("#BDBDBD")))
            p.drawRect(px - peg_w // 2, peg_top, peg_w, peg_h)

            p.setPen(QColor(TEXT_SECONDARY))
            p.setFont(QFont("Helvetica Neue", 10))
            p.drawText(QRectF(px - 20, base_y + 10, 40, 20),
                       Qt.AlignmentFlag.AlignCenter, ["A", "B", "C"][i])

        # Discs on pegs
        disc_h = min(28, int(peg_h / (self.game.n_discs + 1)))
        max_disc_w = 100

        for peg_idx, peg_discs in enumerate(self.game.pegs):
            px = peg_xs[peg_idx]
            for slot, disc in enumerate(peg_discs):
                self._draw_disc(p, px, base_y - (slot + 1) * disc_h,
                                disc, disc_h, max_disc_w)

        # Held disc (floating)
        if self.held_disc is not None:
            hx = int(self.hand_x * w)
            self._draw_disc(p, hx, peg_top - 10, self.held_disc, disc_h, max_disc_w,
                            alpha=200, outline=True)

        # Single hand cursor
        cursor_y = int(h * 0.10)
        color = COLOR_RIGHT if self.active_hand == "right" else COLOR_LEFT
        label = "R" if self.active_hand == "right" else "L"
        self._draw_hand_cursor(p, int(self.hand_x * w), cursor_y,
                               self.hand_pinching, color, label)

        p.end()

    def _draw_disc(self, p: QPainter, cx: int, y: int, disc: int,
                   disc_h: int, max_w: int, alpha: int = 255, outline: bool = False):
        n = self.game.n_discs if self.game else 3
        disc_w = int(30 + (max_w - 30) * disc / n)
        color = QColor(DISC_COLORS[disc % len(DISC_COLORS)])
        color.setAlpha(alpha)
        if outline:
            p.setPen(QPen(QColor("white"), 2))
        else:
            p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        p.drawRoundedRect(cx - disc_w // 2, y, disc_w, disc_h - 2, 6, 6)
        p.setPen(QColor("white"))
        p.setFont(QFont("Helvetica Neue", 10, QFont.Weight.Bold))
        p.drawText(QRectF(cx - disc_w // 2, y, disc_w, disc_h - 2),
                   Qt.AlignmentFlag.AlignCenter, str(disc))

    def _draw_hand_cursor(self, p: QPainter, x: int, y: int,
                          pinching: bool, color: QColor, label: str):
        r = 8 if pinching else 14
        p.setPen(QPen(color, 2))
        p.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 80)))
        p.drawEllipse(x - r, y - r, r * 2, r * 2)
        p.setPen(color)
        p.setFont(QFont("Helvetica Neue", 9, QFont.Weight.Bold))
        p.drawText(QRectF(x - 20, y + r + 2, 40, 16),
                   Qt.AlignmentFlag.AlignCenter, label)

    def _peg_highlight(self, peg: int) -> tuple[QColor, QColor] | None:
        """Return (border_color, fill_color) for peg highlight, or None."""
        hand_over = (self.hand_peg == peg)
        if not hand_over or not self.game:
            return None

        green_border = QColor(ACCENT)
        green_fill = QColor(200, 230, 201, 60)  # light green
        red_border = QColor(DANGER)
        red_fill = QColor(255, 205, 210, 80)  # light red
        blue_border = QColor(PRIMARY)
        blue_fill = QColor(187, 222, 251, 40)  # light blue

        if self.held_disc is not None:
            top = self.game.top_disc(peg)
            if top is None or self.held_disc < top:
                return green_border, green_fill
            else:
                return red_border, red_fill
        else:
            if self.game.top_disc(peg) is not None:
                return green_border, green_fill
            else:
                return blue_border, blue_fill

    def _draw_positioning(self, p: QPainter, w: int, h: int):
        """Draw hand positioning guide – single hand detection."""
        p.fillRect(0, 0, w, h, QColor("#FAFAFA"))

        # Title
        p.setPen(QColor("#333333"))
        p.setFont(QFont("Helvetica Neue", 18, QFont.Weight.Bold))
        p.drawText(QRectF(0, h * 0.08, w, 40),
                   Qt.AlignmentFlag.AlignCenter, "Hand positionieren")

        p.setFont(QFont("Helvetica Neue", 12))
        p.setPen(QColor(TEXT_SECONDARY))
        p.drawText(QRectF(0, h * 0.16, w, 30),
                   Qt.AlignmentFlag.AlignCenter,
                   "Eine Hand flach ueber den Sensor halten")

        # Single target zone (centered)
        zone_w = int(w * 0.30)
        zone_h = int(h * 0.3)
        zone_y = int(h * 0.35)
        zone_x = int(w * 0.35)

        ok = self.hand_ok
        if ok:
            p.setBrush(QBrush(QColor(ACCENT + "22")))
            p.setPen(QPen(QColor(ACCENT), 3))
        else:
            p.setBrush(QBrush(QColor("#F5F5F5")))
            p.setPen(QPen(QColor("#BDBDBD"), 2, Qt.PenStyle.DashLine))
        p.drawRoundedRect(zone_x, zone_y, zone_w, zone_h, 16, 16)

        # Label
        if ok and self.detected_hand:
            label = "Rechte Hand" if self.detected_hand == "right" else "Linke Hand"
            status = "Erkannt"
        else:
            label = "Hand"
            status = "Nicht erkannt"

        p.setPen(QColor(ACCENT) if ok else QColor(TEXT_SECONDARY))
        p.setFont(QFont("Helvetica Neue", 13, QFont.Weight.DemiBold))
        p.drawText(QRectF(zone_x, zone_y + zone_h + 8, zone_w, 24),
                   Qt.AlignmentFlag.AlignCenter, label)
        p.setFont(QFont("Helvetica Neue", 11))
        p.drawText(QRectF(zone_x, zone_y + zone_h + 30, zone_w, 20),
                   Qt.AlignmentFlag.AlignCenter, status)

        # Hand cursor inside zone if detected
        if ok:
            color = COLOR_RIGHT if self.detected_hand == "right" else COLOR_LEFT
            hx = int(self.hand_x * w)
            hy = zone_y + zone_h // 2
            p.setPen(QPen(color, 2))
            p.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 100)))
            p.drawEllipse(hx - 12, hy - 12, 24, 24)


class HanoiScreen(QWidget):
    """Tower of Hanoi game with single-hand tracking."""

    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.test: TowerOfHanoiTest | None = None
        self._phase = GamePhase.POSITIONING
        self._lock = threading.Lock()
        self._last_frame: HandFrame | None = None
        self._active_hand: str = "right"  # determined during positioning
        self._pinch = PinchDetector()
        self._start_time: float = 0.0
        self._countdown_value = 3
        self._held_disc: int | None = None
        self._held_from_peg: int = 0
        self._positioning_start: float = 0.0
        self._hand_ok_since: float | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 16, 30, 16)
        layout.setSpacing(8)

        # Status bar (only moves + time, no errors)
        status_row = QHBoxLayout()
        self.status_label = QLabel("Bereit")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        self.moves_label = QLabel("Zuege: 0")
        self.moves_label.setStyleSheet(f"font-size: 13px; color: {TEXT_SECONDARY};")
        status_row.addWidget(self.moves_label)
        self.time_label = QLabel("Zeit: 0s")
        self.time_label.setStyleSheet(f"font-size: 13px; color: {TEXT_SECONDARY};")
        status_row.addWidget(self.time_label)
        layout.addLayout(status_row)

        # Countdown label
        self.countdown_label = QLabel()
        self.countdown_label.setProperty("cssClass", "countdown")
        self.countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.countdown_label)

        # Canvas
        self.canvas = HanoiCanvas()
        layout.addWidget(self.canvas, stretch=1)

        # Hint
        self.hint_label = QLabel(
            "Pinzettengriff zum Greifen  ·  Hand ueber Stab bewegen  ·  Loslassen zum Ablegen"
        )
        self.hint_label.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY};")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint_label)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.cancel_btn)
        self.giveup_btn = QPushButton("Aufgeben")
        self.giveup_btn.setProperty("cssClass", "danger")
        self.giveup_btn.clicked.connect(self._on_give_up)
        btn_row.addWidget(self.giveup_btn)
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

    def start_test(self, test: TowerOfHanoiTest, patient_code: str) -> None:
        self.test = test
        self._patient_code = patient_code
        self._pinch.reset()
        self._last_frame = None
        self._held_disc = None
        self._held_from_peg = 0
        self._active_hand = "right"
        self._last_right_pos: HandFrame | None = None
        self._last_left_pos: HandFrame | None = None

        self.canvas.game = test.game
        self.canvas.held_disc = None
        self.canvas.flash_color = None
        self.canvas.show_positioning = True
        self.canvas.hand_ok = False
        self.canvas.detected_hand = None
        self.canvas.active_hand = "right"
        self.moves_label.setText("Zuege: 0")
        self.time_label.setText("Zeit: 0s")
        self.giveup_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.countdown_label.setVisible(False)
        self.hint_label.setVisible(False)
        self.status_label.setText("Hand positionieren")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: 700;")

        # Start positioning phase
        self._phase = GamePhase.POSITIONING
        self._hand_ok_since = None
        self._positioning_start = time.perf_counter()

        # Start capture for positioning detection
        def pos_callback(frame: HandFrame) -> None:
            with self._lock:
                if frame.hand_type == "right":
                    self._last_right_pos = frame
                elif frame.hand_type == "left":
                    self._last_left_pos = frame

        self.test.capture.start_recording(pos_callback)
        self._position_timer.start(100)  # 10 Hz check

    def _position_tick(self) -> None:
        if self._phase != GamePhase.POSITIONING:
            return

        with self._lock:
            right = self._last_right_pos
            left = self._last_left_pos

        # Check if hands are detected and roughly in position
        # (palm Y > 120mm = above sensor, confidence > 0)
        r_ok = right is not None and right.palm_position[1] > 120 and right.confidence > 0
        l_ok = left is not None and left.palm_position[1] > 120 and left.confidence > 0

        # Determine which hand to use: both → right, otherwise first detected
        if r_ok and l_ok:
            chosen = "right"
            chosen_frame = right
        elif r_ok:
            chosen = "right"
            chosen_frame = right
        elif l_ok:
            chosen = "left"
            chosen_frame = left
        else:
            chosen = None
            chosen_frame = None

        hand_ok = chosen is not None
        self.canvas.hand_ok = hand_ok
        self.canvas.detected_hand = chosen

        if chosen_frame:
            self.canvas.hand_x = _x_to_norm(chosen_frame.palm_position[0])

        self.canvas.update()

        if hand_ok:
            if self._hand_ok_since is None:
                self._hand_ok_since = time.perf_counter()
                self._active_hand = chosen
            elif time.perf_counter() - self._hand_ok_since > 1.0:
                # Hand stable for 1s → proceed
                self._active_hand = chosen
                self._position_timer.stop()
                self.test.capture.stop_recording()
                self.canvas.show_positioning = False
                self.canvas.active_hand = self._active_hand
                # Update test hand setting
                self.test.hand = self._active_hand
                self._start_countdown()
        else:
            self._hand_ok_since = None

    # ── Countdown ──────────────────────────────────────────────────

    def _start_countdown(self) -> None:
        self._phase = GamePhase.COUNTDOWN
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
            self._start_game()

    # ── Game ───────────────────────────────────────────────────────

    def _start_game(self) -> None:
        self._phase = GamePhase.PLAYING
        self._start_time = time.perf_counter()
        self.test._start_time_s = self._start_time
        self.status_label.setText("Spiel laeuft")
        self.hint_label.setVisible(True)
        self.canvas.update()

        SETTLE_S = 0.25
        record_wall_start = time.perf_counter()
        original_on_frame = self.test._on_frame
        active = self._active_hand

        def callback(frame: HandFrame) -> None:
            try:
                if time.perf_counter() - record_wall_start < SETTLE_S:
                    return
                # Only process frames from the active hand
                if frame.hand_type != active:
                    return
                original_on_frame(frame)
                with self._lock:
                    self._last_frame = frame
            except Exception:
                log.exception("Error in hanoi callback")

        self.test.capture.start_recording(callback)
        self._ui_timer.start(33)

    def _update_ui(self) -> None:
        if self._phase != GamePhase.PLAYING:
            return

        with self._lock:
            frame = self._last_frame

        if frame is None:
            return

        # Update hand position on canvas
        self.canvas.hand_x = _x_to_norm(frame.palm_position[0])
        self.canvas.hand_pinching = self._pinch.is_pinching

        # Process pinch events from active hand
        event = self._pinch.update(frame)
        hand_peg = _x_to_peg(frame.palm_position[0])

        if event == PinchEvent.GRAB and self._held_disc is None:
            if hand_peg >= 0:
                disc = self.test.game.top_disc(hand_peg)
                if disc is not None:
                    self._held_disc = disc
                    self._held_from_peg = hand_peg
                    self.test.game.pegs[hand_peg].pop()

        elif event == PinchEvent.RELEASE and self._held_disc is not None:
            target = hand_peg if hand_peg >= 0 else self._held_from_peg
            self.test.game.pegs[self._held_from_peg].append(self._held_disc)

            elapsed = time.perf_counter() - self._start_time
            valid = self.test.game.move(self._held_from_peg, target, elapsed)
            self._flash(QColor(ACCENT) if valid else QColor(DANGER))
            self._held_disc = None

            if self.test.game.is_solved():
                self.canvas.held_disc = None
                self._on_solved()
                return

        # Update canvas state
        self.canvas.hand_peg = hand_peg
        self.canvas.held_disc = self._held_disc
        self.canvas.hand_pinching = self._pinch.is_pinching

        # Decay flash
        if self.canvas.flash_timer > 0:
            self.canvas.flash_timer -= 0.033
            if self.canvas.flash_timer <= 0:
                self.canvas.flash_color = None

        # Labels (no time limit)
        elapsed = time.perf_counter() - self._start_time
        self.moves_label.setText(f"Zuege: {self.test.game.move_count}")
        self.time_label.setText(f"Zeit: {int(elapsed)}s")

        self.canvas.update()

    def _flash(self, color: QColor) -> None:
        self.canvas.flash_color = color
        self.canvas.flash_timer = 0.4

    # ── End states ─────────────────────────────────────────────────

    def _on_solved(self) -> None:
        self._phase = GamePhase.SOLVED
        self._ui_timer.stop()
        self.test.capture.stop_recording()
        self.test.mark_completed(time.perf_counter())

        elapsed = time.perf_counter() - self._start_time
        n_moves = self.test.game.move_count
        optimal = self.test.game.optimal_moves(self.test.n_discs)

        self.status_label.setText("Geloest!")
        self.status_label.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {ACCENT};")
        self.giveup_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.canvas.update()

        # Show success dialog after short delay
        QTimer.singleShot(800, lambda: self._show_success_dialog(elapsed, n_moves, optimal))

    def _show_success_dialog(self, elapsed: float, n_moves: int, optimal: int) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Aufgabe geloest")
        dlg.setFixedSize(340, 220)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        title = QLabel("Erfolgreich geloest!")
        title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {ACCENT};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        info = QLabel(
            f"Zeit: {int(elapsed)}s\n"
            f"Zuege: {n_moves}  (optimal: {optimal})"
        )
        info.setStyleSheet(f"font-size: 13px; color: {TEXT_SECONDARY};")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(info)

        lay.addSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        repeat_btn = QPushButton("Wiederholen")
        repeat_btn.setFixedHeight(38)
        repeat_btn.clicked.connect(lambda: (dlg.accept(), self._repeat()))
        btn_row.addWidget(repeat_btn)

        save_btn = QPushButton("Speichern")
        save_btn.setProperty("cssClass", "primary")
        save_btn.setFixedHeight(38)
        save_btn.clicked.connect(lambda: (dlg.accept(), self._show_results()))
        btn_row.addWidget(save_btn)

        lay.addLayout(btn_row)
        dlg.exec()

    def _repeat(self) -> None:
        """Repeat the test with a fresh game."""
        self.status_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.main_window.repeat_test("tower_of_hanoi", self._active_hand,
                                     self.main_window.dashboard.duration_spin.value())

    def _on_give_up(self) -> None:
        self._phase = GamePhase.ABORTED
        self._ui_timer.stop()
        self._position_timer.stop()
        self.test.capture.stop_recording()
        self.test.mark_aborted(time.perf_counter())

        if self._held_disc is not None:
            self.test.game.pegs[self._held_from_peg].append(self._held_disc)
            self._held_disc = None
            self.canvas.held_disc = None

        self.status_label.setText("Aufgegeben")
        self.status_label.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {DANGER};")
        self.giveup_btn.setEnabled(False)
        self.canvas.update()
        QTimer.singleShot(1500, self._show_results)

    def _on_cancel(self) -> None:
        self._countdown_timer.stop()
        self._ui_timer.stop()
        self._position_timer.stop()
        if self._phase in (GamePhase.PLAYING, GamePhase.POSITIONING):
            self.test.capture.stop_recording()
        self._phase = GamePhase.ABORTED
        self.main_window.show_start()

    def _show_results(self) -> None:
        self.status_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.main_window.show_results(self.test, self._patient_code)
