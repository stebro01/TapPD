"""ANALYSE tab – mean template, editable weights, live recognition, recordings."""

from __future__ import annotations

import math
import threading
import time

import numpy as np
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from capture.base_capture import HandFrame
from gesture_lab.battery import get_battery_definitions
from gesture_lab.feature_extraction import extract_pose_vector
from gesture_lab.gesture_db import (
    delete_template,
    list_templates,
    list_templates_for_pose,
    update_template,
)
from gesture_lab.matching import (
    classify_static_result,
    compute_mean_template,
    static_similarity,
)
from gesture_lab.models import (
    GestureTemplate,
    FINGER_NAMES,
    FINGER_NAMES_SHORT,
    N_JOINT_ANGLES,
    N_ABDUCTION,
    N_TIP_DISTANCES,
    N_PALM_ORIENT,
)
from storage.database import get_db
from ui import theme
from ui.theme import SZ
from ui.hand_visualization import HandVisualizationWidget

if TYPE_CHECKING:
    from ui.gesture_lab_screen import GestureLabScreen


class AnalysisPanel(QWidget):
    """Tab 2: Mean template analysis with editable weights and live recognition."""

    def __init__(self, lab_screen: GestureLabScreen) -> None:
        super().__init__()
        self.lab_screen = lab_screen

        self._all_templates: list[GestureTemplate] = []
        self._pose_recordings: list[GestureTemplate] = []
        self._mean_template: GestureTemplate | None = None
        self._selected_pose: int = 1

        # Live detection
        self._detecting = False
        self._latest_frame: HandFrame | None = None

        # Playback
        self._playback_frames: list[HandFrame] = []
        self._playback_index: int = 0
        self._playback_start: float = 0.0
        self._playing = False

        self._live_timer = QTimer(self)
        self._live_timer.setInterval(33)
        self._live_timer.timeout.connect(self._live_tick)

        self._score_timer = QTimer(self)
        self._score_timer.setInterval(100)
        self._score_timer.timeout.connect(self._score_tick)

        self._playback_timer = QTimer(self)
        self._playback_timer.setInterval(33)
        self._playback_timer.timeout.connect(self._playback_tick)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left: Pose selector + recordings ──────────────────────
        left_w = QWidget()
        left_w.setFixedWidth(250)
        left = QVBoxLayout(left_w)
        left.setContentsMargins(8, 8, 8, 8)
        left.setSpacing(6)

        left.addWidget(self._section_label("Pose"))

        # Pose list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._pose_list_w = QWidget()
        self._pose_list_l = QVBoxLayout(self._pose_list_w)
        self._pose_list_l.setContentsMargins(0, 0, 0, 0)
        self._pose_list_l.setSpacing(2)
        scroll.setWidget(self._pose_list_w)
        left.addWidget(scroll, stretch=1)

        left.addSpacing(4)
        left.addWidget(self._section_label("Aufzeichnungen"))

        self.rec_table = QTableWidget()
        self.rec_table.setColumnCount(4)
        self.rec_table.setHorizontalHeaderLabels(["ID", "Hand", "Frames", "Erstellt"])
        self.rec_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.rec_table.setColumnWidth(0, 35)
        self.rec_table.setColumnWidth(1, 40)
        self.rec_table.setColumnWidth(2, 45)
        self.rec_table.verticalHeader().setVisible(False)
        self.rec_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rec_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rec_table.verticalHeader().setDefaultSectionSize(SZ.ROW_H)
        self.rec_table.clicked.connect(self._on_rec_clicked)
        left.addWidget(self.rec_table, stretch=2)

        # Mean info
        self.mean_label = QLabel("")
        self.mean_label.setWordWrap(True)
        self.mean_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 10px;")
        left.addWidget(self.mean_label)

        # Add / Delete buttons
        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("+ Aufzeichnung")
        self.add_btn.setProperty("cssClass", "accent")
        self.add_btn.setFixedHeight(SZ.BTN_H)
        self.add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(self.add_btn)

        self.delete_btn = QPushButton("Löschen")
        self.delete_btn.setFixedHeight(SZ.BTN_H)
        self.delete_btn.setStyleSheet(f"color: {theme.DANGER};")
        self.delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self.delete_btn)
        left.addLayout(btn_row)

        root.addWidget(left_w)

        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background-color: {theme.BORDER};")
        root.addWidget(sep)

        # ── Right: Viz + weights + score + features ───────────────
        right_w = QWidget()
        right = QVBoxLayout(right_w)
        right.setContentsMargins(12, 8, 12, 8)
        right.setSpacing(6)

        # Hand viz + controls
        viz_row = QHBoxLayout()
        self.hand_viz = HandVisualizationWidget()
        self.hand_viz.setMinimumHeight(180)
        viz_row.addWidget(self.hand_viz, stretch=1)

        # Control buttons (vertical)
        ctrl = QVBoxLayout()
        ctrl.setSpacing(4)

        self.play_btn = QPushButton("Abspielen")
        self.play_btn.setFixedHeight(SZ.BTN_H)
        self.play_btn.clicked.connect(self._toggle_playback)
        ctrl.addWidget(self.play_btn)

        self.live_btn = QPushButton("Live")
        self.live_btn.setProperty("cssClass", "accent")
        self.live_btn.setFixedHeight(SZ.BTN_H)
        self.live_btn.clicked.connect(self._toggle_live)
        ctrl.addWidget(self.live_btn)

        ctrl.addStretch()
        viz_row.addLayout(ctrl)

        right.addLayout(viz_row, stretch=2)

        # Score bar
        score_row = QHBoxLayout()
        self.score_label = QLabel("Score: –")
        self.score_label.setFont(QFont("Helvetica Neue", 12, QFont.Weight.Bold))
        score_row.addWidget(self.score_label)

        self.score_bar = QProgressBar()
        self.score_bar.setRange(0, 100)
        self.score_bar.setValue(0)
        self.score_bar.setFixedHeight(16)
        self.score_bar.setTextVisible(True)
        self.score_bar.setFormat("%v%")
        score_row.addWidget(self.score_bar, stretch=1)

        self.result_label = QLabel("")
        self.result_label.setFont(QFont("Helvetica Neue", 12, QFont.Weight.Bold))
        score_row.addWidget(self.result_label)
        right.addLayout(score_row)

        # ── Gewichte + Parameter-Scores (scrollable) ─────────────
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        detail_inner = QWidget()
        detail_layout = QVBoxLayout(detail_inner)
        detail_layout.setContentsMargins(0, 0, 4, 0)
        detail_layout.setSpacing(6)

        detail_layout.addWidget(self._section_label("Gewichte (Matching)"))
        weights_grid = QGridLayout()
        weights_grid.setSpacing(3)

        # Header
        for col, h in enumerate(["Parameter", "Gewicht", "", "Score"]):
            lbl = QLabel(h)
            lbl.setStyleSheet("font-weight: bold; font-size: 9px;")
            weights_grid.addWidget(lbl, 0, col)

        self._weight_sliders: list[QSlider] = []
        self._weight_labels: list[QLabel] = []
        self._score_cells: list[QLabel] = []

        weight_names = FINGER_NAMES + ["Orientierung"]
        for i, fn in enumerate(weight_names):
            name_lbl = QLabel(fn)
            name_lbl.setStyleSheet("font-size: 10px;")
            weights_grid.addWidget(name_lbl, i + 1, 0)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(100 if i < 5 else 30)
            slider.setFixedHeight(18)
            slider.valueChanged.connect(self._on_weight_changed)
            weights_grid.addWidget(slider, i + 1, 1)
            self._weight_sliders.append(slider)

            val_lbl = QLabel("1.00" if i < 5 else "0.30")
            val_lbl.setFixedWidth(30)
            val_lbl.setStyleSheet("font-size: 9px;")
            weights_grid.addWidget(val_lbl, i + 1, 2)
            self._weight_labels.append(val_lbl)

            score_lbl = QLabel("–")
            score_lbl.setFixedWidth(40)
            score_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            score_lbl.setStyleSheet(
                f"background-color: {theme.CARD_BG}; border: 1px solid {theme.BORDER}; "
                f"border-radius: 2px; font-size: 9px;"
            )
            weights_grid.addWidget(score_lbl, i + 1, 3)
            self._score_cells.append(score_lbl)

        # Grab strength score (read-only, no slider)
        grab_lbl = QLabel("GrabStrength")
        grab_lbl.setStyleSheet("font-size: 10px;")
        weights_grid.addWidget(grab_lbl, 7, 0)
        self._grab_score = QLabel("–")
        self._grab_score.setFixedWidth(40)
        self._grab_score.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._grab_score.setStyleSheet(
            f"background-color: {theme.CARD_BG}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 2px; font-size: 9px;"
        )
        weights_grid.addWidget(self._grab_score, 7, 3)

        self.save_weights_btn = QPushButton("Gewichte speichern")
        self.save_weights_btn.setFixedHeight(SZ.BTN_H)
        self.save_weights_btn.clicked.connect(self._save_weights)
        weights_grid.addWidget(self.save_weights_btn, 8, 0, 1, 4)

        detail_layout.addLayout(weights_grid)

        # ── Bottom: Metriken (links) + Gelenkwinkel (rechts) ─────
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)

        # Left: Metriken
        metrics_col = QVBoxLayout()
        metrics_col.setSpacing(4)
        metrics_col.addWidget(self._section_label("Metriken (Mean-Template)"))

        # Finger extension table
        ext_grid = QGridLayout()
        ext_grid.setSpacing(2)
        ext_grid.addWidget(QLabel("Finger"), 0, 0)
        h_soll = QLabel("Soll")
        h_soll.setStyleSheet("font-weight: bold; font-size: 9px;")
        ext_grid.addWidget(h_soll, 0, 1)

        self._ext_labels: list[QLabel] = []
        for i, fn in enumerate(FINGER_NAMES):
            name_lbl = QLabel(fn)
            name_lbl.setStyleSheet("font-size: 10px;")
            ext_grid.addWidget(name_lbl, i + 1, 0)

            ext_lbl = QLabel("–")
            ext_lbl.setFixedHeight(20)
            ext_lbl.setStyleSheet("font-size: 10px;")
            ext_grid.addWidget(ext_lbl, i + 1, 1)
            self._ext_labels.append(ext_lbl)

        metrics_col.addLayout(ext_grid)

        metrics_col.addSpacing(4)

        # Orientation
        self.orient_label = QLabel("Orientierung:\n  Roll: –\n  Pitch: –\n  Yaw: –")
        self.orient_label.setStyleSheet(f"font-size: 10px; color: {theme.TEXT_SECONDARY};")
        metrics_col.addWidget(self.orient_label)

        # GrabStrength / Spread
        self.derived_label = QLabel("GrabStrength: –\nSpreadRatio: –")
        self.derived_label.setStyleSheet(f"font-size: 10px; color: {theme.TEXT_SECONDARY};")
        metrics_col.addWidget(self.derived_label)

        metrics_col.addStretch()
        bottom_row.addLayout(metrics_col)

        # Right: Gelenkwinkel Heatmap
        angles_col = QVBoxLayout()
        angles_col.setSpacing(4)
        angles_col.addWidget(self._section_label("Gelenkwinkel (Mean-Template)"))

        self.angle_grid = QGridLayout()
        self.angle_grid.setSpacing(2)
        self._angle_cells: list[list[QLabel]] = []

        for j, jn in enumerate(["MCP", "PIP", "DIP", "Tip"]):
            lbl = QLabel(jn)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-weight: bold; font-size: 9px;")
            self.angle_grid.addWidget(lbl, 0, j + 1)

        for i, fn in enumerate(FINGER_NAMES_SHORT):
            lbl = QLabel(fn)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl.setStyleSheet("font-weight: bold; font-size: 10px;")
            self.angle_grid.addWidget(lbl, i + 1, 0)
            row_cells: list[QLabel] = []
            for j in range(4):
                cell = QLabel("–")
                cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setFixedSize(44, 22)
                cell.setStyleSheet(
                    f"background-color: {theme.CARD_BG}; border: 1px solid {theme.BORDER}; "
                    f"border-radius: 2px; font-size: 9px;"
                )
                self.angle_grid.addWidget(cell, i + 1, j + 1)
                row_cells.append(cell)
            self._angle_cells.append(row_cells)
        angles_col.addLayout(self.angle_grid)
        angles_col.addStretch()
        bottom_row.addLayout(angles_col)

        detail_layout.addLayout(bottom_row)
        detail_scroll.setWidget(detail_inner)

        right.addWidget(detail_scroll, stretch=3)

        root.addWidget(right_w, stretch=1)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-weight: bold; font-size: 10px;"
        )
        return lbl

    # ── Data loading ──────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._stop_live()
        self._stop_playback()

    def _refresh(self) -> None:
        conn = get_db()
        try:
            self._all_templates = list_templates(conn)
        finally:
            conn.close()
        self._build_pose_list()
        self._load_pose(self._selected_pose)

    def _build_pose_list(self) -> None:
        while self._pose_list_l.count():
            item = self._pose_list_l.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._pose_btns: dict[int, QPushButton] = {}

        # Count recordings per pose
        counts: dict[int, int] = {}
        for t in self._all_templates:
            counts[t.pose_number] = counts.get(t.pose_number, 0) + 1

        for defn in get_battery_definitions():
            pn = defn.pose_number
            c = counts.get(pn, 0)
            label = f"#{pn} {defn.name}"
            if c:
                label += f"  ({c})"
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setCheckable(True)
            btn.setChecked(pn == self._selected_pose)
            btn.clicked.connect(lambda _, p=pn: self._on_pose_selected(p))
            if pn == self._selected_pose:
                btn.setStyleSheet(
                    f"background-color: {theme.PRIMARY}; color: white; "
                    f"font-weight: bold; border-radius: 3px; text-align: left; padding-left: 6px;"
                )
            else:
                btn.setStyleSheet("text-align: left; padding-left: 6px;")
            self._pose_list_l.addWidget(btn)
            self._pose_btns[pn] = btn

    def _on_pose_selected(self, pose_number: int) -> None:
        self._stop_live()
        self._stop_playback()
        self._selected_pose = pose_number
        for pn, btn in self._pose_btns.items():
            btn.setChecked(pn == pose_number)
            if pn == pose_number:
                btn.setStyleSheet(
                    f"background-color: {theme.PRIMARY}; color: white; "
                    f"font-weight: bold; border-radius: 3px; text-align: left; padding-left: 6px;"
                )
            else:
                btn.setStyleSheet("text-align: left; padding-left: 6px;")
        self._load_pose(pose_number)

    def _load_pose(self, pose_number: int) -> None:
        conn = get_db()
        try:
            self._pose_recordings = list_templates_for_pose(conn, pose_number)
        finally:
            conn.close()

        # Recordings table
        hand_map = {"right": "R", "left": "L", "any": "B"}
        self.rec_table.setRowCount(len(self._pose_recordings))
        for row, t in enumerate(self._pose_recordings):
            id_item = QTableWidgetItem()
            id_item.setData(Qt.ItemDataRole.DisplayRole, t.id)
            self.rec_table.setItem(row, 0, id_item)
            self.rec_table.setItem(row, 1, QTableWidgetItem(
                hand_map.get(t.hand_type, t.hand_type)
            ))
            n_frames = len(t.raw_frames) if t.raw_frames else 0
            fr_item = QTableWidgetItem()
            fr_item.setData(Qt.ItemDataRole.DisplayRole, n_frames)
            self.rec_table.setItem(row, 2, fr_item)
            self.rec_table.setItem(row, 3, QTableWidgetItem(t.created_at))

        # Compute mean template
        self._mean_template = compute_mean_template(self._pose_recordings)
        if self._mean_template:
            self.mean_label.setText(
                f"Mean aus {len(self._pose_recordings)} Aufzeichnung(en)"
            )
            self._display_template(self._mean_template)
            self._set_weight_sliders(self._mean_template.finger_weights)
        else:
            self.mean_label.setText("Keine Aufzeichnungen")
            self.hand_viz.update_frame(None)

    def select_by_id(self, template_id: int) -> None:
        """Select a specific recording (called from Gesten tab)."""
        self._refresh()
        # Find pose for this template
        for t in self._all_templates:
            if t.id == template_id:
                self._selected_pose = t.pose_number
                self._build_pose_list()
                self._load_pose(t.pose_number)
                # Select the row
                for row in range(self.rec_table.rowCount()):
                    id_item = self.rec_table.item(row, 0)
                    if id_item and id_item.data(Qt.ItemDataRole.DisplayRole) == template_id:
                        self.rec_table.selectRow(row)
                        self._show_recording(t)
                        break
                return

    # ── Recordings ────────────────────────────────────────────────

    def _on_rec_clicked(self, index) -> None:
        row = index.row()
        if row < 0 or row >= len(self._pose_recordings):
            return
        id_item = self.rec_table.item(row, 0)
        if not id_item:
            return
        tid = id_item.data(Qt.ItemDataRole.DisplayRole)
        t = next((t for t in self._pose_recordings if t.id == tid), None)
        if t:
            self._show_recording(t)

    def _show_recording(self, t: GestureTemplate) -> None:
        """Prepare playback for a specific recording. Heatmap stays on Mean."""
        self._stop_playback()
        # Don't call _display_template(t) – heatmap/metriken stay on Mean
        if t.raw_frames:
            self._playback_frames = [HandFrame.from_dict(d) for d in t.raw_frames]
            self.play_btn.setEnabled(True)
            if self._playback_frames:
                self.hand_viz.update_frame(self._playback_frames[0])
        else:
            self._playback_frames = []
            self.play_btn.setEnabled(False)

    # ── Weights ───────────────────────────────────────────────────

    def _get_finger_weights(self) -> list[float]:
        return [self._weight_sliders[i].value() / 100.0 for i in range(5)]

    def _get_orient_weight(self) -> float:
        return self._weight_sliders[5].value() / 100.0

    def _set_weight_sliders(self, weights: list[float]) -> None:
        for i, w in enumerate(weights[:5]):
            self._weight_sliders[i].blockSignals(True)
            self._weight_sliders[i].setValue(int(w * 100))
            self._weight_sliders[i].blockSignals(False)
            self._weight_labels[i].setText(f"{w:.2f}")

    def _on_weight_changed(self) -> None:
        for i, s in enumerate(self._weight_sliders):
            self._weight_labels[i].setText(f"{s.value() / 100:.2f}")

    def _save_weights(self) -> None:
        """Save current finger weights to all recordings of this pose."""
        fw = self._get_finger_weights()
        conn = get_db()
        try:
            for t in self._pose_recordings:
                t.finger_weights = fw[:]
                update_template(conn, t)
        finally:
            conn.close()
        if self._mean_template:
            self._mean_template.finger_weights = fw[:]
        QMessageBox.information(
            self, "Gespeichert",
            f"Finger-Gewichte für {len(self._pose_recordings)} Aufzeichnung(en) aktualisiert."
        )

    # ── Live detection ────────────────────────────────────────────

    def _toggle_live(self) -> None:
        if self._detecting:
            self._stop_live()
        else:
            self._start_live()

    def _start_live(self) -> None:
        if not self._mean_template:
            return
        capture = self.lab_screen.capture
        if not capture or not capture.is_connected():
            return

        self._stop_playback()
        self._detecting = True
        self.live_btn.setText("Live Stop")
        self.live_btn.setStyleSheet(
            f"background-color: {theme.DANGER}; color: white; font-weight: bold;"
        )
        capture.start_recording(self._on_live_frame)
        self._live_timer.start()
        self._score_timer.start()

    def _stop_live(self) -> None:
        if not self._detecting:
            return
        self._detecting = False
        self._live_timer.stop()
        self._score_timer.stop()
        self.live_btn.setText("Live")
        self.live_btn.setStyleSheet("")
        capture = self.lab_screen.capture
        if capture:
            try:
                capture.stop_recording()
            except Exception:
                pass

    def _on_live_frame(self, frame: HandFrame) -> None:
        self._latest_frame = frame

    def _live_tick(self) -> None:
        if self._latest_frame:
            self.hand_viz.update_frame(self._latest_frame)

    def _score_tick(self) -> None:
        frame = self._latest_frame
        tmpl = self._mean_template
        if not frame or not tmpl or not tmpl.pose_vector:
            return
        if frame.confidence < 0.5:
            self.score_label.setText("Score: –")
            self.result_label.setText("Tracking verloren")
            self.result_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
            return

        vec, _ = extract_pose_vector(frame)
        fw = self._get_finger_weights()
        ow = self._get_orient_weight()

        from gesture_lab.matching import compute_parameter_scores
        t_vec = np.array(tmpl.pose_vector, dtype=np.float64)
        live_ext = [f.is_extended for f in frame.fingers] if len(frame.fingers) >= 5 else None
        detail = compute_parameter_scores(
            vec, t_vec, fw, ow,
            live_hand=frame.hand_type,
            tmpl_hand=tmpl.hand_type,
            expected_ext=tmpl.expected_extensions,
            live_ext=live_ext,
        )

        pct = int(detail["total"] * 100)
        self.score_bar.setValue(pct)
        result = classify_static_result(detail["total"], tmpl)
        self.score_label.setText(f"Score: {pct}%")

        colors = {"KORREKT": theme.ACCENT, "TEILWEISE": theme.WARN, "FALSCH": theme.DANGER}
        c = colors.get(result, theme.TEXT_SECONDARY)
        self.result_label.setText(result)
        self.result_label.setStyleSheet(f"color: {c};")
        self.score_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {c}; border-radius: 3px; }}"
        )

        # Update per-parameter score cells
        for i in range(5):
            s = detail.get(f"finger_{i}", 0.0)
            self._update_score_cell(self._score_cells[i], s)
        self._update_score_cell(self._score_cells[5], detail.get("orientation", 0.0))
        self._update_score_cell(self._grab_score, detail.get("grab", 0.0))

    def _update_score_cell(self, cell: QLabel, score: float) -> None:
        pct = int(score * 100)
        cell.setText(f"{pct}%")
        if score >= 0.85:
            bg = "#E8F5E9"
            border = theme.ACCENT
        elif score >= 0.60:
            bg = "#FFF8E1"
            border = theme.WARN
        else:
            bg = "#FFEBEE"
            border = theme.DANGER
        cell.setStyleSheet(
            f"background-color: {bg}; border: 1px solid {border}; "
            f"border-radius: 2px; font-size: 9px; font-weight: bold;"
        )

    # ── Playback ──────────────────────────────────────────────────

    def _toggle_playback(self) -> None:
        if self._playing:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self) -> None:
        if not self._playback_frames:
            return
        self._stop_live()
        self._playing = True
        self._playback_index = 0
        self._playback_start = time.monotonic()
        self.play_btn.setText("Stop")
        self._playback_timer.start()

    def _stop_playback(self) -> None:
        if not self._playing:
            return
        self._playing = False
        self._playback_timer.stop()
        self.play_btn.setText("Abspielen")

    def _playback_tick(self) -> None:
        if not self._playback_frames:
            self._stop_playback()
            return
        elapsed = time.monotonic() - self._playback_start
        t0 = self._playback_frames[0].timestamp_us
        target_us = t0 + int(elapsed * 1_000_000)
        idx = self._playback_index
        while idx < len(self._playback_frames) - 1:
            if self._playback_frames[idx + 1].timestamp_us <= target_us:
                idx += 1
            else:
                break
        self._playback_index = idx
        self.hand_viz.update_frame(self._playback_frames[idx])
        if idx >= len(self._playback_frames) - 1:
            self._stop_playback()

    # ── Feature display ───────────────────────────────────────────

    def _display_template(self, t: GestureTemplate) -> None:
        vec = np.array(t.pose_vector) if t.pose_vector else np.zeros(35)

        # Gelenkwinkel Heatmap
        for i in range(5):
            for j in range(4):
                idx = i * 4 + j
                if idx < len(vec):
                    val = vec[idx]
                    self._angle_cells[i][j].setText(f"{val:.2f}")
                    intensity = min(255, int(val / 1.5 * 255))
                    bg = QColor(255, 255 - intensity, 255 - intensity)
                    self._angle_cells[i][j].setStyleSheet(
                        f"background-color: {bg.name()}; border: 1px solid {theme.BORDER}; "
                        f"border-radius: 2px; font-size: 9px;"
                    )

        # Finger extension (Soll-Zustand)
        for i in range(5):
            ext = t.expected_extensions[i] if i < len(t.expected_extensions) else True
            if ext:
                text = "gestreckt"
                bg = "#E8F5E9"
                c = theme.ACCENT
            else:
                text = "gebeugt"
                bg = "#FFEBEE"
                c = theme.DANGER
            self._ext_labels[i].setText(text)
            self._ext_labels[i].setStyleSheet(
                f"color: {c}; font-size: 10px; font-weight: bold; "
                f"background-color: {bg}; padding: 1px 4px; border-radius: 2px;"
            )

        # Orientierung
        o = N_JOINT_ANGLES + N_ABDUCTION + N_TIP_DISTANCES
        if len(vec) > o + 2:
            r, p, y = (math.degrees(vec[o + k]) for k in range(3))
            self.orient_label.setText(
                f"Orientierung:\n  Roll:  {r:+.1f}°\n  Pitch: {p:+.1f}°\n  Yaw:   {y:+.1f}°"
            )

        # GrabStrength + SpreadRatio
        d = o + N_PALM_ORIENT
        if len(vec) > d + 1:
            self.derived_label.setText(
                f"GrabStrength: {vec[d]:.3f}\nSpreadRatio:  {vec[d + 1]:.3f}"
            )

    # ── Delete ────────────────────────────────────────────────────

    def _on_add(self) -> None:
        """Switch to Gesten tab and start recording for the current pose."""
        self._stop_live()
        self._stop_playback()
        if hasattr(self.lab_screen, 'gesten_panel'):
            gp = self.lab_screen.gesten_panel
            gp._on_pose_clicked(self._selected_pose)
            self.lab_screen._switch_mode(0)
            gp._start_recording()

    def _on_delete(self) -> None:
        rows = self.rec_table.selectedIndexes()
        if not rows:
            return
        row = rows[0].row()
        id_item = self.rec_table.item(row, 0)
        if not id_item:
            return
        tid = id_item.data(Qt.ItemDataRole.DisplayRole)
        reply = QMessageBox.question(
            self, "Löschen", f"Aufzeichnung (ID {tid}) löschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            conn = get_db()
            try:
                delete_template(conn, tid)
            finally:
                conn.close()
            self._stop_playback()
            self._refresh()
