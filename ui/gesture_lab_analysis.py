"""ANALYSE tab – Soll-vs-Ist comparison with transparent scoring."""

from __future__ import annotations

import logging
import math
import threading
import time

import numpy as np
from typing import TYPE_CHECKING

log = logging.getLogger(__name__)

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
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
    QStyle,
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
    compute_parameter_scores,
    format_score_breakdown,
)
from gesture_lab.models import (
    GestureTemplate,
    FINGER_NAMES,
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

_FN_SHORT = ["Daumen", "Zeigef.", "Mittelf.", "Ringf.", "Kleiner"]


class AnalysisPanel(QWidget):
    """Tab 2: Soll-vs-Ist comparison with transparent score breakdown."""

    def __init__(self, lab_screen: GestureLabScreen) -> None:
        super().__init__()
        self.lab_screen = lab_screen

        self._all_templates: list[GestureTemplate] = []
        self._pose_recordings: list[GestureTemplate] = []
        self._mean_template: GestureTemplate | None = None
        self._soll_frame: HandFrame | None = None  # median frame for SOLL viz
        self._selected_pose: int = 1

        # Live / playback
        self._detecting = False
        self._latest_frame: HandFrame | None = None
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

        # ── Left panel: Pose + Recordings ─────────────────────────
        left_w = QWidget()
        left_w.setFixedWidth(250)
        left = QVBoxLayout(left_w)
        left.setContentsMargins(8, 8, 8, 8)
        left.setSpacing(6)

        left.addWidget(self._section_label("Pose"))
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
        self.rec_table.setHorizontalHeaderLabels(["ID", "Hand", "Fr.", "Erstellt"])
        self.rec_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.rec_table.setColumnWidth(0, 30)
        self.rec_table.setColumnWidth(1, 35)
        self.rec_table.setColumnWidth(2, 30)
        self.rec_table.verticalHeader().setVisible(False)
        self.rec_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rec_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rec_table.verticalHeader().setDefaultSectionSize(SZ.ROW_H)
        self.rec_table.clicked.connect(self._on_rec_clicked)
        left.addWidget(self.rec_table, stretch=2)

        self.mean_label = QLabel("")
        self.mean_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 10px;")
        left.addWidget(self.mean_label)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Aufz.")
        add_btn.setProperty("cssClass", "accent")
        add_btn.setFixedHeight(SZ.BTN_H)
        add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(add_btn)
        del_btn = QPushButton("Löschen")
        del_btn.setFixedHeight(SZ.BTN_H)
        del_btn.setStyleSheet(f"color: {theme.DANGER};")
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(del_btn)
        left.addLayout(btn_row)

        root.addWidget(left_w)

        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background-color: {theme.BORDER};")
        root.addWidget(sep)

        # ── Right panel: Soll-vs-Ist ──────────────────────────────
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_inner = QWidget()
        right = QVBoxLayout(right_inner)
        right.setContentsMargins(10, 8, 10, 8)
        right.setSpacing(6)

        # ── Two hand skeletons ────────────────────────────────────
        viz_row = QHBoxLayout()
        viz_row.setSpacing(8)

        # SOLL column with play button
        soll_col = QVBoxLayout()
        soll_header = QHBoxLayout()
        soll_lbl = QLabel("SOLL (Template)")
        soll_lbl.setFont(QFont("Helvetica Neue", 10, QFont.Weight.Bold))
        soll_header.addWidget(soll_lbl)
        soll_header.addStretch()
        self._play_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self._stop_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop)
        self.play_btn = QPushButton(self._play_icon, "")
        self.play_btn.setFixedSize(32, 24)
        self.play_btn.setToolTip("Aufzeichnung abspielen")
        self.play_btn.clicked.connect(self._toggle_playback)
        soll_header.addWidget(self.play_btn)
        soll_col.addLayout(soll_header)
        self.soll_viz = HandVisualizationWidget()
        self.soll_viz.setMinimumHeight(160)
        soll_col.addWidget(self.soll_viz)
        viz_row.addLayout(soll_col)

        # IST column with live toggle
        ist_col = QVBoxLayout()
        ist_header = QHBoxLayout()
        ist_lbl = QLabel("IST (Live)")
        ist_lbl.setFont(QFont("Helvetica Neue", 10, QFont.Weight.Bold))
        ist_header.addWidget(ist_lbl)
        ist_header.addStretch()
        self.live_btn = QPushButton("Live")
        self.live_btn.setFixedSize(50, 24)
        self.live_btn.setStyleSheet(
            f"background-color: {theme.ACCENT}; color: white; "
            f"font-weight: bold; border-radius: 3px; font-size: 10px;"
        )
        self.live_btn.clicked.connect(self._toggle_live)
        ist_header.addWidget(self.live_btn)
        ist_col.addLayout(ist_header)
        self.ist_viz = HandVisualizationWidget()
        self.ist_viz.setMinimumHeight(160)
        ist_col.addWidget(self.ist_viz)
        viz_row.addLayout(ist_col)

        right.addLayout(viz_row)

        # Save weights button (compact row)
        save_row = QHBoxLayout()
        save_row.addStretch()
        save_btn = QPushButton("Gewichte speichern")
        save_btn.setFixedHeight(SZ.BTN_H)
        save_btn.clicked.connect(self._save_weights)
        save_row.addWidget(save_btn)
        right.addLayout(save_row)

        # ── Soll-vs-Ist comparison table ──────────────────────────
        right.addWidget(self._section_label("Soll-vs-Ist Vergleich"))

        self.compare_grid = QGridLayout()
        self.compare_grid.setSpacing(3)

        headers = ["", "Parameter", "Soll", "Gewicht", "Toleranz", "Ist", "Score"]
        for c, h in enumerate(headers):
            lbl = QLabel(h)
            lbl.setStyleSheet("font-weight: bold; font-size: 9px;")
            if c >= 5:
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.compare_grid.addWidget(lbl, 0, c)

        from PyQt6.QtWidgets import QCheckBox

        self._enable_checks: list[QCheckBox] = []
        self._weight_sliders: list[QSlider] = []
        self._weight_labels: list[QLabel] = []
        self._tol_sliders: list[QSlider] = []
        self._tol_labels: list[QLabel] = []
        self._soll_cells: list[QLabel] = []
        self._ist_cells: list[QLabel] = []
        self._score_cells: list[QLabel] = []

        row_names = _FN_SHORT + ["Orientierung", "GrabStrength", "SpreadRatio"]
        for i, name in enumerate(row_names):
            # Enable checkbox (5 fingers + orientation = first 6)
            if i < 6:
                cb = QCheckBox()
                cb.setChecked(True)
                cb.setToolTip("Aktiv: wird im Score berücksichtigt")
                self.compare_grid.addWidget(cb, i + 1, 0, Qt.AlignmentFlag.AlignCenter)
                self._enable_checks.append(cb)
            else:
                spacer = QLabel("")
                self.compare_grid.addWidget(spacer, i + 1, 0)

            # Name
            n_lbl = QLabel(name)
            n_lbl.setStyleSheet("font-size: 10px;")
            self.compare_grid.addWidget(n_lbl, i + 1, 1)

            # Soll
            soll_cell = QLabel("–")
            soll_cell.setStyleSheet("font-size: 10px;")
            self.compare_grid.addWidget(soll_cell, i + 1, 2)
            self._soll_cells.append(soll_cell)

            # Weight slider (only for first 6: 5 fingers + orientation)
            if i < 6:
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(0, 100)
                slider.setValue(100 if i < 5 else 30)
                slider.setFixedHeight(18)
                slider.setFixedWidth(70)
                slider.valueChanged.connect(self._on_weight_changed)
                self.compare_grid.addWidget(slider, i + 1, 3)
                self._weight_sliders.append(slider)

                w_lbl = QLabel("1.00" if i < 5 else "0.30")
                w_lbl.setFixedWidth(28)
                w_lbl.setStyleSheet("font-size: 9px;")
                self._weight_labels.append(w_lbl)
            else:
                dash = QLabel("–")
                dash.setStyleSheet("font-size: 9px; color: #BDBDBD;")
                self.compare_grid.addWidget(dash, i + 1, 3)

            # Tolerance slider (only for 5 fingers)
            if i < 5:
                tol_slider = QSlider(Qt.Orientation.Horizontal)
                tol_slider.setRange(0, 100)  # 0..1.0 rad in 0.01 steps
                tol_slider.setValue(0)
                tol_slider.setFixedHeight(18)
                tol_slider.setFixedWidth(70)
                tol_slider.valueChanged.connect(self._on_weight_changed)
                self.compare_grid.addWidget(tol_slider, i + 1, 4)
                self._tol_sliders.append(tol_slider)

                t_lbl = QLabel("0.00")
                t_lbl.setFixedWidth(28)
                t_lbl.setStyleSheet("font-size: 9px;")
                self._tol_labels.append(t_lbl)
            else:
                dash2 = QLabel("–")
                dash2.setStyleSheet("font-size: 9px; color: #BDBDBD;")
                self.compare_grid.addWidget(dash2, i + 1, 4)

            # Ist
            ist_cell = QLabel("–")
            ist_cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ist_cell.setStyleSheet("font-size: 10px;")
            self.compare_grid.addWidget(ist_cell, i + 1, 5)
            self._ist_cells.append(ist_cell)

            # Score
            score_cell = QLabel("–")
            score_cell.setFixedWidth(45)
            score_cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
            score_cell.setStyleSheet(
                f"background-color: {theme.CARD_BG}; border: 1px solid {theme.BORDER}; "
                f"border-radius: 2px; font-size: 9px;"
            )
            self.compare_grid.addWidget(score_cell, i + 1, 6)
            self._score_cells.append(score_cell)

        right.addLayout(self.compare_grid)

        # ── Score total + formula ─────────────────────────────────
        right.addWidget(self._section_label("Score-Aufschlüsselung"))

        score_row = QHBoxLayout()
        self.total_label = QLabel("SCORE: –")
        self.total_label.setFont(QFont("Helvetica Neue", 14, QFont.Weight.Bold))
        score_row.addWidget(self.total_label)

        self.total_bar = QProgressBar()
        self.total_bar.setRange(0, 100)
        self.total_bar.setValue(0)
        self.total_bar.setFixedHeight(18)
        self.total_bar.setTextVisible(True)
        self.total_bar.setFormat("%v%")
        score_row.addWidget(self.total_bar, stretch=1)

        self.result_label = QLabel("")
        self.result_label.setFont(QFont("Helvetica Neue", 14, QFont.Weight.Bold))
        score_row.addWidget(self.result_label)
        right.addLayout(score_row)

        self.formula_label = QLabel("")
        self.formula_label.setWordWrap(True)
        self.formula_label.setStyleSheet(
            f"background-color: #F5F5F5; border: 1px solid {theme.BORDER}; "
            f"border-radius: 4px; padding: 6px; font-family: monospace; font-size: 10px;"
        )
        right.addWidget(self.formula_label)

        right_scroll.setWidget(right_inner)
        root.addWidget(right_scroll, stretch=1)

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
            btn.setFixedHeight(26)
            btn.setCheckable(True)
            btn.setChecked(pn == self._selected_pose)
            btn.clicked.connect(lambda _, p=pn: self._on_pose_selected(p))
            self._style_pose_btn(btn, pn == self._selected_pose)
            self._pose_list_l.addWidget(btn)
            self._pose_btns[pn] = btn

    def _style_pose_btn(self, btn: QPushButton, selected: bool) -> None:
        if selected:
            btn.setStyleSheet(
                f"background-color: {theme.PRIMARY}; color: white; font-weight: bold; "
                f"border-radius: 3px; text-align: left; padding-left: 6px; font-size: 10px;"
            )
        else:
            btn.setStyleSheet("text-align: left; padding-left: 6px; font-size: 10px;")

    def _on_pose_selected(self, pose_number: int) -> None:
        self._stop_live()
        self._stop_playback()
        self._selected_pose = pose_number
        for pn, btn in self._pose_btns.items():
            btn.setChecked(pn == pose_number)
            self._style_pose_btn(btn, pn == pose_number)
        self._load_pose(pose_number)

    def _load_pose(self, pose_number: int) -> None:
        conn = get_db()
        try:
            self._pose_recordings = list_templates_for_pose(conn, pose_number)
        finally:
            conn.close()

        hand_map = {"right": "R", "left": "L", "any": "B"}
        self.rec_table.setRowCount(len(self._pose_recordings))
        for row, t in enumerate(self._pose_recordings):
            id_item = QTableWidgetItem()
            id_item.setData(Qt.ItemDataRole.DisplayRole, t.id)
            self.rec_table.setItem(row, 0, id_item)
            self.rec_table.setItem(row, 1, QTableWidgetItem(hand_map.get(t.hand_type, t.hand_type)))
            n_fr = len(t.raw_frames) if t.raw_frames else 0
            fr_item = QTableWidgetItem()
            fr_item.setData(Qt.ItemDataRole.DisplayRole, n_fr)
            self.rec_table.setItem(row, 2, fr_item)
            self.rec_table.setItem(row, 3, QTableWidgetItem(t.created_at))

        # Mean template
        self._mean_template = compute_mean_template(self._pose_recordings)
        if self._mean_template:
            self.mean_label.setText(f"Mean aus {len(self._pose_recordings)} Aufz.")
            self._set_weight_sliders(self._mean_template.finger_weights)
            self._set_tol_sliders(self._mean_template.finger_tolerances)
            self._set_enable_checks(self._mean_template.param_enabled)
            self._update_soll_display()
            # SOLL hand: median frame from all recordings
            self._soll_frame = self._find_median_frame()
            self.soll_viz.update_frame(self._soll_frame)
            # Auto-select first recording and prepare playback
            if self._pose_recordings:
                self.rec_table.selectRow(0)
                self._prepare_playback(self._pose_recordings[0])
        else:
            self.mean_label.setText("Keine Aufzeichnungen")
            self.soll_viz.update_frame(None)
            self._clear_soll_display()
            self._playback_frames = []
            self.play_btn.setEnabled(False)

    def _find_median_frame(self) -> HandFrame | None:
        """Find the temporal median frame across all recordings."""
        for t in self._pose_recordings:
            if t.raw_frames:
                frames = [HandFrame.from_dict(d) for d in t.raw_frames]
                mid = len(frames) // 2
                return frames[mid]
        return None

    def select_by_id(self, template_id: int) -> None:
        self._refresh()
        for t in self._all_templates:
            if t.id == template_id:
                self._selected_pose = t.pose_number
                self._build_pose_list()
                self._load_pose(t.pose_number)
                for row in range(self.rec_table.rowCount()):
                    id_item = self.rec_table.item(row, 0)
                    if id_item and id_item.data(Qt.ItemDataRole.DisplayRole) == template_id:
                        self.rec_table.selectRow(row)
                        self._prepare_playback(t)
                        break
                return

    # ── Soll display ──────────────────────────────────────────────

    def _update_soll_display(self) -> None:
        t = self._mean_template
        if not t or not t.pose_vector:
            self._clear_soll_display()
            return
        vec = np.array(t.pose_vector)

        for i in range(5):
            # Finger: extension state + mean joint angle
            ext = t.expected_extensions[i] if i < len(t.expected_extensions) else True
            ext_str = "GESTRECKT" if ext else "gebeugt"
            angles = vec[i * 4:(i + 1) * 4]
            mean_angle = float(np.mean(angles))
            self._soll_cells[i].setText(f"{ext_str} ({mean_angle:.2f})")
            bg = "#E8F5E9" if ext else "#FFEBEE"
            self._soll_cells[i].setStyleSheet(f"font-size: 10px; background-color: {bg}; padding: 1px 3px; border-radius: 2px;")

        # Orientation
        o = N_JOINT_ANGLES + N_ABDUCTION + N_TIP_DISTANCES
        if len(vec) > o + 2:
            r, p, y = (math.degrees(vec[o + k]) for k in range(3))
            self._soll_cells[5].setText(f"R:{r:+.0f}° P:{p:+.0f}° Y:{y:+.0f}°")
        # Grab
        d = o + N_PALM_ORIENT
        if len(vec) > d:
            self._soll_cells[6].setText(f"{vec[d]:.3f}")
        if len(vec) > d + 1:
            self._soll_cells[7].setText(f"{vec[d + 1]:.3f}")

    def _clear_soll_display(self) -> None:
        for cell in self._soll_cells:
            cell.setText("–")
            cell.setStyleSheet("font-size: 10px;")

    # ── Ist display (called from scoring) ─────────────────────────

    def _update_ist_display(self, frame: HandFrame, vec: NDArray) -> None:
        for i in range(5):
            ext = frame.fingers[i].is_extended if i < len(frame.fingers) else True
            ext_str = "GESTRECKT" if ext else "gebeugt"
            angles = vec[i * 4:(i + 1) * 4]
            mean_angle = float(np.mean(angles))
            self._ist_cells[i].setText(f"{ext_str} ({mean_angle:.2f})")
            bg = "#E8F5E9" if ext else "#FFEBEE"
            self._ist_cells[i].setStyleSheet(f"font-size: 10px; background-color: {bg}; padding: 1px 3px; border-radius: 2px;")

        o = N_JOINT_ANGLES + N_ABDUCTION + N_TIP_DISTANCES
        if len(vec) > o + 2:
            r, p, y = (math.degrees(vec[o + k]) for k in range(3))
            self._ist_cells[5].setText(f"R:{r:+.0f}° P:{p:+.0f}° Y:{y:+.0f}°")
        d = o + N_PALM_ORIENT
        if len(vec) > d:
            self._ist_cells[6].setText(f"{vec[d]:.3f}")
        if len(vec) > d + 1:
            self._ist_cells[7].setText(f"{vec[d + 1]:.3f}")

    # ── Recordings ────────────────────────────────────────────────

    def _on_rec_clicked(self, index) -> None:
        row = index.row()
        id_item = self.rec_table.item(row, 0)
        if not id_item:
            return
        tid = id_item.data(Qt.ItemDataRole.DisplayRole)
        t = next((t for t in self._pose_recordings if t.id == tid), None)
        if t:
            self._prepare_playback(t)

    def _prepare_playback(self, t: GestureTemplate) -> None:
        self._stop_playback()
        if t.raw_frames:
            self._playback_frames = [HandFrame.from_dict(d) for d in t.raw_frames]
            self.play_btn.setEnabled(True)
            if self._playback_frames:
                self.soll_viz.update_frame(self._playback_frames[0])
        else:
            self._playback_frames = []
            self.play_btn.setEnabled(False)

    # ── Weights ───────────────────────────────────────────────────

    def _get_finger_weights(self) -> list[float]:
        return [
            self._weight_sliders[i].value() / 100.0 if self._enable_checks[i].isChecked() else 0.0
            for i in range(5)
        ]

    def _get_orient_weight(self) -> float:
        if not self._enable_checks[5].isChecked():
            return 0.0
        return self._weight_sliders[5].value() / 100.0

    def _get_finger_tolerances(self) -> list[float]:
        return [self._tol_sliders[i].value() / 100.0 for i in range(5)]

    def _set_weight_sliders(self, weights: list[float]) -> None:
        for i, w in enumerate(weights[:5]):
            self._weight_sliders[i].blockSignals(True)
            self._weight_sliders[i].setValue(int(w * 100))
            self._weight_sliders[i].blockSignals(False)
            self._weight_labels[i].setText(f"{w:.2f}")

    def _set_tol_sliders(self, tolerances: list[float]) -> None:
        for i, t in enumerate(tolerances[:5]):
            self._tol_sliders[i].blockSignals(True)
            self._tol_sliders[i].setValue(int(t * 100))
            self._tol_sliders[i].blockSignals(False)
            self._tol_labels[i].setText(f"{t:.2f}")

    def _set_enable_checks(self, enabled: list[bool]) -> None:
        for i, e in enumerate(enabled[:6]):
            if i < len(self._enable_checks):
                self._enable_checks[i].setChecked(e)

    def _on_weight_changed(self) -> None:
        for i, s in enumerate(self._weight_sliders):
            self._weight_labels[i].setText(f"{s.value() / 100:.2f}")
        for i, s in enumerate(self._tol_sliders):
            self._tol_labels[i].setText(f"{s.value() / 100:.2f}")

    def _save_weights(self) -> None:
        # Read raw slider values (not affected by checkbox)
        fw = [self._weight_sliders[i].value() / 100.0 for i in range(5)]
        ft = self._get_finger_tolerances()
        pe = [self._enable_checks[i].isChecked() for i in range(6)]
        conn = get_db()
        try:
            for t in self._pose_recordings:
                t.finger_weights = fw[:]
                t.finger_tolerances = ft[:]
                t.param_enabled = pe[:]
                update_template(conn, t)
        finally:
            conn.close()
        if self._mean_template:
            self._mean_template.finger_weights = fw[:]
            self._mean_template.finger_tolerances = ft[:]
            self._mean_template.param_enabled = pe[:]
        QMessageBox.information(self, "Gespeichert",
            f"Einstellungen für {len(self._pose_recordings)} Aufzeichnung(en) aktualisiert.")

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
        # Source-aware skeleton projection (Leap = top-down, Kamera = frontal).
        from capture.source import source_kind
        self.ist_viz.set_projection(
            "frontal" if source_kind(capture) == "webcam" else "topdown")
        self._stop_playback()
        self._detecting = True
        self.live_btn.setText("Stop")
        self.live_btn.setStyleSheet(
            f"background-color: {theme.DANGER}; color: white; "
            f"font-weight: bold; border-radius: 3px; font-size: 10px;"
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
        self.live_btn.setStyleSheet(
            f"background-color: {theme.ACCENT}; color: white; "
            f"font-weight: bold; border-radius: 3px; font-size: 10px;"
        )
        self.ist_viz.update_frame(None)
        self._latest_frame = None
        # Reset Ist cells
        for cell in self._ist_cells:
            cell.setText("–")
            cell.setStyleSheet("font-size: 10px;")
        for cell in self._score_cells:
            cell.setText("–")
            cell.setStyleSheet(
                f"background-color: {theme.CARD_BG}; border: 1px solid {theme.BORDER}; "
                f"border-radius: 2px; font-size: 9px;"
            )
        self.total_label.setText("SCORE: –")
        self.total_bar.setValue(0)
        self.result_label.setText("")
        self.formula_label.setText("")
        capture = self.lab_screen.capture
        if capture:
            try:
                capture.stop_recording()
            except Exception:
                pass

    _NO_HAND_STREAK: int = 0  # consecutive ticks without valid frame

    def _on_live_frame(self, frame: HandFrame) -> None:
        self._latest_frame = frame

    def _live_tick(self) -> None:
        try:
            frame = self._latest_frame
            if frame and len(frame.fingers) >= 5:
                self.ist_viz.update_frame(frame)
                self._NO_HAND_STREAK = 0
            else:
                self._NO_HAND_STREAK += 1
                # Only clear after ~0.5s (15 ticks at 33ms) to avoid flicker
                if self._NO_HAND_STREAK > 15:
                    self.ist_viz.update_frame(None)
        except Exception:
            log.exception("live_tick error")

    def _score_tick(self) -> None:
        try:
            self._score_tick_inner()
        except Exception:
            log.exception("score_tick error")

    def _score_tick_inner(self) -> None:
        frame = self._latest_frame
        tmpl = self._mean_template
        if not tmpl or not tmpl.pose_vector:
            return

        # No frame or incomplete frame → show "no hand" after debounce
        if not frame or len(frame.fingers) < 5 or frame.confidence < 0.3:
            self._NO_HAND_STREAK += 1
            if self._NO_HAND_STREAK > 5:  # ~0.5s at 100ms score tick
                self.total_label.setText("SCORE: –")
                self.result_label.setText("Keine Hand erkannt")
                self.result_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
                self.formula_label.setText("")
            return

        # Low confidence → show warning but keep last scores visible
        if frame.confidence < 0.5:
            self.result_label.setText("Tracking unsicher")
            self.result_label.setStyleSheet(f"color: {theme.WARN};")

        vec, _ = extract_pose_vector(frame)
        fw = self._get_finger_weights()
        ow = self._get_orient_weight()
        ft = self._get_finger_tolerances()
        live_ext = [f.is_extended for f in frame.fingers]

        t_vec = np.array(tmpl.pose_vector, dtype=np.float64)
        detail = compute_parameter_scores(
            vec, t_vec, fw, ow,
            live_hand=frame.hand_type, tmpl_hand=tmpl.hand_type,
            expected_ext=tmpl.expected_extensions, live_ext=live_ext,
            finger_tolerances=ft,
        )

        # Update Ist column
        self._update_ist_display(frame, vec)

        # Update score cells
        for i in range(5):
            self._color_score_cell(self._score_cells[i], detail.get(f"finger_{i}", 0.0))
        self._color_score_cell(self._score_cells[5], detail.get("orientation", 0.0))
        self._color_score_cell(self._score_cells[6], detail.get("grab", 0.0))
        self._score_cells[7].setText("–")

        # Total
        total = detail["total"]
        pct = int(total * 100)
        self.total_bar.setValue(pct)
        result = classify_static_result(total, tmpl)
        self.total_label.setText(f"SCORE: {pct}%")

        colors = {"KORREKT": theme.ACCENT, "TEILWEISE": theme.WARN, "FALSCH": theme.DANGER}
        c = colors.get(result, theme.TEXT_SECONDARY)
        if frame.confidence >= 0.5:
            self.result_label.setText(result)
            self.result_label.setStyleSheet(f"color: {c};")
        self.total_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {c}; border-radius: 3px; }}"
        )

        # Formula breakdown
        breakdown = format_score_breakdown(
            detail, fw, _FN_SHORT,
            expected_ext=tmpl.expected_extensions, live_ext=live_ext,
        )
        self.formula_label.setText(breakdown)

    def _color_score_cell(self, cell: QLabel, score: float) -> None:
        pct = int(score * 100)
        cell.setText(f"{pct}%")
        if score >= 0.85:
            bg, border = "#E8F5E9", theme.ACCENT
        elif score >= 0.60:
            bg, border = "#FFF8E1", theme.WARN
        else:
            bg, border = "#FFEBEE", theme.DANGER
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
        self.play_btn.setIcon(self._stop_icon)
        self._playback_timer.start()

    def _stop_playback(self) -> None:
        if not self._playing:
            return
        self._playing = False
        self._playback_timer.stop()
        if not self._detecting:
            self._score_timer.stop()
        self.play_btn.setIcon(self._play_icon)

    def _playback_tick(self) -> None:
        try:
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
            frame = self._playback_frames[idx]
            self.soll_viz.update_frame(frame)
            if idx >= len(self._playback_frames) - 1:
                self._stop_playback()
        except Exception:
            log.exception("playback_tick error")
            self._stop_playback()

    # ── Actions ───────────────────────────────────────────────────

    def _on_add(self) -> None:
        try:
            self._stop_live()
            self._stop_playback()
            if hasattr(self.lab_screen, 'gesten_panel'):
                gp = self.lab_screen.gesten_panel
                gp._on_pose_clicked(self._selected_pose)
                self.lab_screen._switch_mode(0)
                gp._start_recording()
        except Exception:
            log.exception("on_add error")

    def _on_delete(self) -> None:
        rows = self.rec_table.selectedIndexes()
        if not rows:
            return
        id_item = self.rec_table.item(rows[0].row(), 0)
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
