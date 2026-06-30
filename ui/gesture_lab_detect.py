"""Detection mode panel for the Gesture Lab – live gesture recognition."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from capture.base_capture import HandFrame
from gesture_lab.battery import get_battery_definitions
from gesture_lab.error_analysis import classify_finger_errors, classify_palm_error
from gesture_lab.feature_extraction import extract_pose_vector
from gesture_lab.gesture_db import list_templates, list_templates_for_pose
from gesture_lab.matching import static_similarity, classify_static_result, compute_mean_template
from gesture_lab.models import (
    GestureTemplate,
    FINGER_NAMES,
    ErrorType,
)
from storage.database import get_db
from ui import theme
from ui.theme import SZ
from ui.hand_visualization import HandVisualizationWidget, FINGER_COLORS

if TYPE_CHECKING:
    from ui.gesture_lab_screen import GestureLabScreen

log = logging.getLogger(__name__)

# Hold timer: pose must be correct for this duration to count
HOLD_DURATION_S = 1.5
# Scoring update interval (100ms = 10 Hz)
SCORE_INTERVAL_MS = 100


class DetectPanel(QWidget):
    """Live gesture detection with scoring and per-finger error feedback."""

    def __init__(self, lab_screen: GestureLabScreen) -> None:
        super().__init__()
        self.lab_screen = lab_screen

        self._templates: list[GestureTemplate] = []
        self._current_template: GestureTemplate | None = None
        self._detecting = False
        self._lock = threading.Lock()
        self._latest_frame: HandFrame | None = None

        # Hold timer state
        self._hold_start: float | None = None
        self._hold_recognized = False

        # Battery mode
        self._battery_mode = False
        self._battery_index = 0
        self._battery_results: list[tuple[int, str, float]] = []  # (pose_num, result, score)

        self._build_ui()

        # Timers
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(33)  # 30 fps for viz
        self._live_timer.timeout.connect(self._poll_live)

        self._score_timer = QTimer(self)
        self._score_timer.setInterval(SCORE_INTERVAL_MS)
        self._score_timer.timeout.connect(self._update_scoring)

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Left: Pose list with instructions ─────────────────────
        left_widget = QWidget()
        left_widget.setFixedWidth(240)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(6)

        header = QHBoxLayout()
        header.addWidget(QLabel("Geste wählen"))
        header.addStretch()
        self.mode_combo = QComboBox()
        self.mode_combo.setFixedHeight(SZ.BTN_H)
        self.mode_combo.addItems(["Einzeln", "Batterie"])
        self.mode_combo.setFixedWidth(90)
        header.addWidget(self.mode_combo)
        left_layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._pose_list_widget = QWidget()
        self._pose_list_layout = QVBoxLayout(self._pose_list_widget)
        self._pose_list_layout.setContentsMargins(0, 0, 0, 0)
        self._pose_list_layout.setSpacing(4)
        scroll.setWidget(self._pose_list_widget)
        left_layout.addWidget(scroll, stretch=1)

        self.start_btn = QPushButton("Erkennung starten")
        self.start_btn.setProperty("cssClass", "accent")
        self.start_btn.setFixedHeight(SZ.BTN_H)
        self.start_btn.clicked.connect(self._toggle_detection)
        left_layout.addWidget(self.start_btn)

        self.next_btn = QPushButton("Nächste Pose →")
        self.next_btn.setProperty("cssClass", "primary")
        self.next_btn.setFixedHeight(SZ.BTN_H)
        self.next_btn.setVisible(False)
        self.next_btn.clicked.connect(self._next_battery_pose)
        left_layout.addWidget(self.next_btn)

        layout.addWidget(left_widget)

        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background-color: {theme.BORDER};")
        layout.addWidget(sep)

        # ── Right: hand viz + results ─────────────────────────────
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)

        # Hand visualization
        self.hand_viz = HandVisualizationWidget()
        right_layout.addWidget(self.hand_viz, stretch=2)

        # Scoring area
        right = QVBoxLayout()
        right.setSpacing(6)

        # Right: scoring results
        right = QVBoxLayout()
        right.setSpacing(10)

        # Similarity gauge
        self.sim_label = QLabel("Ähnlichkeit")
        self.sim_label.setFont(QFont("Helvetica Neue", 11))
        right.addWidget(self.sim_label)

        self.sim_bar = QProgressBar()
        self.sim_bar.setRange(0, 100)
        self.sim_bar.setValue(0)
        self.sim_bar.setFixedHeight(24)
        self.sim_bar.setTextVisible(True)
        self.sim_bar.setFormat("%v%")
        right.addWidget(self.sim_bar)

        # Status
        self.result_label = QLabel("–")
        self.result_label.setFont(QFont("Helvetica Neue", 20, QFont.Weight.Bold))
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right.addWidget(self.result_label)

        # Hold progress
        self.hold_label = QLabel("")
        self.hold_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hold_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 11px;")
        right.addWidget(self.hold_label)

        self.hold_bar = QProgressBar()
        self.hold_bar.setRange(0, 100)
        self.hold_bar.setValue(0)
        self.hold_bar.setFixedHeight(8)
        self.hold_bar.setTextVisible(False)
        right.addWidget(self.hold_bar)

        # Per-finger breakdown
        right.addSpacing(8)
        right.addWidget(self._section_label("Finger-Analyse"))

        self._finger_labels: list[QLabel] = []
        for i in range(5):
            lbl = QLabel(f"{FINGER_NAMES[i]}: –")
            lbl.setStyleSheet(
                f"background-color: {theme.CARD_BG}; border: 1px solid {theme.BORDER}; "
                f"border-radius: 3px; padding: 4px 8px; font-size: 11px;"
            )
            right.addWidget(lbl)
            self._finger_labels.append(lbl)

        # Palm error
        self.palm_label = QLabel("Handfläche: –")
        self.palm_label.setStyleSheet(
            f"background-color: {theme.CARD_BG}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 3px; padding: 4px 8px; font-size: 11px;"
        )
        right.addWidget(self.palm_label)

        right.addStretch()
        right_layout.addLayout(right, stretch=1)

        # Battery summary area (hidden initially)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            f"background-color: {theme.CARD_BG}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 4px; padding: 8px; font-size: 11px;"
        )
        self.summary_label.setVisible(False)
        right_layout.addWidget(self.summary_label)

        layout.addWidget(right_widget, stretch=1)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-weight: bold; "
            f"font-size: 11px; text-transform: uppercase;"
        )
        return lbl

    # ── Template management ───────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._build_pose_list()

    def _build_pose_list(self) -> None:
        """Build the pose list with instruction text."""
        while self._pose_list_layout.count():
            item = self._pose_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._pose_cards: dict[int, QFrame] = {}

        # Check which poses have templates
        conn = get_db()
        try:
            self._templates = list_templates(conn)
        finally:
            conn.close()
        has_template = {t.pose_number for t in self._templates}

        for defn in get_battery_definitions():
            card = QFrame()
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setFrameShape(QFrame.Shape.NoFrame)
            has = defn.pose_number in has_template

            cl = QVBoxLayout(card)
            cl.setContentsMargins(6, 4, 6, 4)
            cl.setSpacing(2)

            row1 = QHBoxLayout()
            name_lbl = QLabel(f"#{defn.pose_number} {defn.name}")
            name_lbl.setFont(QFont("Helvetica Neue", 11, QFont.Weight.Bold))
            row1.addWidget(name_lbl)
            row1.addStretch()
            if has:
                check = QLabel("✓")
                check.setStyleSheet(f"color: {theme.ACCENT}; font-weight: bold;")
                row1.addWidget(check)
            cl.addLayout(row1)

            desc = QLabel(defn.description)
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 10px;")
            cl.addWidget(desc)

            bg = "#E8F5E9" if has else theme.CARD_BG
            border = theme.ACCENT if has else theme.BORDER
            card.setStyleSheet(
                f"QFrame {{ background-color: {bg}; border: 1px solid {border}; "
                f"border-radius: 4px; }}"
            )
            card.mousePressEvent = lambda e, pn=defn.pose_number: self._on_pose_clicked(pn)
            self._pose_list_layout.addWidget(card)
            self._pose_cards[defn.pose_number] = card

        self._pose_list_layout.addStretch()

    def _on_pose_clicked(self, pose_number: int) -> None:
        """Select a pose and load its most recent template."""
        # Highlight selected
        for pn, card in self._pose_cards.items():
            if pn == pose_number:
                card.setStyleSheet(
                    f"QFrame {{ background-color: {theme.PRIMARY_LIGHT}; "
                    f"border: 2px solid {theme.PRIMARY}; border-radius: 4px; }}"
                )
            else:
                has = any(t.pose_number == pn for t in self._templates)
                bg = "#E8F5E9" if has else theme.CARD_BG
                border = theme.ACCENT if has else theme.BORDER
                card.setStyleSheet(
                    f"QFrame {{ background-color: {bg}; border: 1px solid {border}; "
                    f"border-radius: 4px; }}"
                )

        # Load mean template from all recordings of this pose
        conn = get_db()
        try:
            pose_recs = list_templates_for_pose(conn, pose_number)
        finally:
            conn.close()
        mean = compute_mean_template(pose_recs)
        if mean:
            self._current_template = mean
        self._reset_hold()

    def select_pose(self, pose_number: int) -> None:
        """Select a pose (called from gesten panel)."""
        self._on_pose_clicked(pose_number)

    # ── Detection control ─────────────────────────────────────────

    def _toggle_detection(self) -> None:
        if self._detecting:
            self.stop_detection()
        else:
            self._start_detection()

    def _start_detection(self) -> None:
        if not self._current_template:
            self.result_label.setText("Keine Vorlage")
            self.result_label.setStyleSheet(f"color: {theme.DANGER};")
            return

        capture = self.lab_screen.capture
        if capture is None or not capture.is_connected():
            self.result_label.setText("Kein Sensor")
            self.result_label.setStyleSheet(f"color: {theme.DANGER};")
            return

        self._detecting = True
        self._reset_hold()

        # Battery mode setup
        self._battery_mode = self.mode_combo.currentIndex() == 1
        if self._battery_mode:
            self._battery_index = 0
            self._battery_results.clear()
            self._setup_battery_pose()
            self.next_btn.setVisible(True)
            self.summary_label.setVisible(False)

        self.start_btn.setText("⏹  Erkennung stoppen")
        self.start_btn.setStyleSheet(
            f"background-color: {theme.DANGER}; color: white; font-weight: bold;"
        )

        capture.start_recording(self._on_frame)
        self._live_timer.start()
        self._score_timer.start()

    def stop_detection(self) -> None:
        if not self._detecting:
            return
        self._detecting = False
        self._live_timer.stop()
        self._score_timer.stop()

        capture = self.lab_screen.capture
        if capture:
            try:
                capture.stop_recording()
            except Exception:
                pass

        self.start_btn.setText("▶  Erkennung starten")
        self.start_btn.setStyleSheet("")
        self.next_btn.setVisible(False)

        # Show battery summary if applicable
        if self._battery_mode and self._battery_results:
            self._show_battery_summary()

    def _on_frame(self, frame: HandFrame) -> None:
        self._latest_frame = frame

    def _poll_live(self) -> None:
        frame = self._latest_frame
        self.hand_viz.update_frame(frame)

    # ── Scoring loop (10 Hz) ──────────────────────────────────────

    def _update_scoring(self) -> None:
        frame = self._latest_frame
        template = self._current_template

        if not frame or not template:
            return

        # Confidence gating
        if frame.confidence < 0.5:
            self.result_label.setText("Tracking verloren")
            self.result_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
            self.hand_viz.set_status("Tracking verloren", QColor(theme.TEXT_SECONDARY))
            self._reset_hold()
            return

        # Extract pose vector
        live_vec, conf_mask = extract_pose_vector(frame)

        # Compute similarity (with hand mirroring + extension check)
        live_ext = [f.is_extended for f in frame.fingers] if len(frame.fingers) >= 5 else None
        sim = static_similarity(live_vec, template, live_hand=frame.hand_type, live_extensions=live_ext)

        sim_pct = int(sim * 100)
        self.sim_bar.setValue(sim_pct)
        self._color_sim_bar(sim_pct)

        # Classify result
        result = classify_static_result(sim, template)
        self.result_label.setText(result)

        if result == "KORREKT":
            self.result_label.setStyleSheet(f"color: {theme.ACCENT}; font-size: 20px;")
            self.hand_viz.set_status(f"KORREKT ({sim_pct}%)", QColor(theme.ACCENT))
        elif result == "TEILWEISE":
            self.result_label.setStyleSheet(f"color: {theme.WARN}; font-size: 20px;")
            self.hand_viz.set_status(f"TEILWEISE ({sim_pct}%)", QColor(theme.WARN))
        else:
            self.result_label.setStyleSheet(f"color: {theme.DANGER}; font-size: 20px;")
            self.hand_viz.set_status(f"FALSCH ({sim_pct}%)", QColor(theme.DANGER))

        # Per-finger errors
        extensions = [f.is_extended for f in frame.fingers] if len(frame.fingers) >= 5 else [True]*5
        finger_errors = classify_finger_errors(live_vec, extensions, template)
        palm_error = classify_palm_error(live_vec, template)

        # Update finger labels
        error_by_finger: dict[int, str] = {}
        highlights: dict[int, QColor] = {}
        for fe in finger_errors:
            error_by_finger[fe.finger_id] = fe.detail
            highlights[fe.finger_id] = QColor(theme.DANGER)

        for i in range(5):
            if i in error_by_finger:
                self._finger_labels[i].setText(f"{FINGER_NAMES[i]}: ❌ {error_by_finger[i]}")
                self._finger_labels[i].setStyleSheet(
                    f"background-color: #FFEBEE; border: 1px solid {theme.DANGER}; "
                    f"border-radius: 3px; padding: 4px 8px; font-size: 11px;"
                )
            else:
                self._finger_labels[i].setText(f"{FINGER_NAMES[i]}: ✓ OK")
                self._finger_labels[i].setStyleSheet(
                    f"background-color: #E8F5E9; border: 1px solid {theme.ACCENT}; "
                    f"border-radius: 3px; padding: 4px 8px; font-size: 11px;"
                )

        self.hand_viz.set_finger_highlights(highlights)

        # Palm error
        if palm_error:
            self.palm_label.setText(f"Handfläche: ❌ {palm_error.detail}")
            self.palm_label.setStyleSheet(
                f"background-color: #FFEBEE; border: 1px solid {theme.DANGER}; "
                f"border-radius: 3px; padding: 4px 8px; font-size: 11px;"
            )
        else:
            self.palm_label.setText("Handfläche: ✓ OK")
            self.palm_label.setStyleSheet(
                f"background-color: #E8F5E9; border: 1px solid {theme.ACCENT}; "
                f"border-radius: 3px; padding: 4px 8px; font-size: 11px;"
            )

        # Hold timer logic
        if result == "KORREKT":
            if self._hold_start is None:
                self._hold_start = time.monotonic()
            elapsed = time.monotonic() - self._hold_start
            progress = min(100, int(elapsed / HOLD_DURATION_S * 100))
            self.hold_bar.setValue(progress)
            self.hold_label.setText(f"Halten... {elapsed:.1f}s / {HOLD_DURATION_S}s")

            if elapsed >= HOLD_DURATION_S and not self._hold_recognized:
                self._hold_recognized = True
                self.hold_label.setText("✓ Pose erkannt!")
                self.hold_label.setStyleSheet(
                    f"color: {theme.ACCENT}; font-weight: bold; font-size: 12px;"
                )
                if self._battery_mode:
                    self._record_battery_result(sim)
        else:
            self._reset_hold()

    def _color_sim_bar(self, pct: int) -> None:
        if pct >= 85:
            color = theme.ACCENT
        elif pct >= 60:
            color = theme.WARN
        else:
            color = theme.DANGER
        self.sim_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {color}; border-radius: 4px; }}"
        )

    def _reset_hold(self) -> None:
        self._hold_start = None
        self._hold_recognized = False
        self.hold_bar.setValue(0)
        self.hold_label.setText("")
        self.hold_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 11px;")

    # ── Battery mode ──────────────────────────────────────────────

    def _setup_battery_pose(self) -> None:
        """Set up the current battery pose."""
        definitions = get_battery_definitions()
        if self._battery_index >= len(definitions):
            self.stop_detection()
            return

        defn = definitions[self._battery_index]
        pose_num = defn.pose_number

        # Compute mean template from all recordings
        conn = get_db()
        try:
            pose_recs = list_templates_for_pose(conn, pose_num)
        finally:
            conn.close()

        mean = compute_mean_template(pose_recs)
        if mean:
            self._current_template = mean
            self._on_pose_clicked(pose_num)
        else:
            # No template for this pose – skip
            self._battery_results.append((pose_num, "ÜBERSPRUNGEN", 0.0))
            self._battery_index += 1
            self._setup_battery_pose()
            return

        self._reset_hold()
        self.next_btn.setText(
            f"Nächste Pose → ({self._battery_index + 1}/{len(definitions)})"
        )

    def _record_battery_result(self, score: float) -> None:
        if not self._current_template:
            return
        result = classify_static_result(score, self._current_template)
        self._battery_results.append(
            (self._current_template.pose_number, result, score)
        )

    def _next_battery_pose(self) -> None:
        # If current pose wasn't recognized, record as current state
        if not self._hold_recognized and self._current_template:
            frame = self._latest_frame
            if frame:
                vec, _ = extract_pose_vector(frame)
                ext = [f.is_extended for f in frame.fingers] if len(frame.fingers) >= 5 else None
                sim = static_similarity(vec, self._current_template, live_hand=frame.hand_type, live_extensions=ext)
                result = classify_static_result(sim, self._current_template)
                self._battery_results.append(
                    (self._current_template.pose_number, result, sim)
                )
            else:
                self._battery_results.append(
                    (self._current_template.pose_number, "ÜBERSPRUNGEN", 0.0)
                )

        self._battery_index += 1
        definitions = get_battery_definitions()
        if self._battery_index >= len(definitions):
            self.stop_detection()
        else:
            self._setup_battery_pose()

    def _show_battery_summary(self) -> None:
        lines = ["Batterie-Ergebnis:\n"]
        definitions = get_battery_definitions()
        name_map = {d.pose_number: d.name for d in definitions}

        correct = 0
        for pose_num, result, score in self._battery_results:
            name = name_map.get(pose_num, f"Pose {pose_num}")
            icon = {"KORREKT": "🟢", "TEILWEISE": "🟡", "FALSCH": "🔴"}.get(result, "⚪")
            lines.append(f"{icon} #{pose_num} {name}: {result} ({score*100:.0f}%)")
            if result == "KORREKT":
                correct += 1

        total = len(self._battery_results)
        lines.append(f"\nGesamt: {correct}/{total} korrekt")

        self.summary_label.setText("\n".join(lines))
        self.summary_label.setVisible(True)

    # ── Cleanup ───────────────────────────────────────────────────

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self._detecting:
            self.stop_detection()
