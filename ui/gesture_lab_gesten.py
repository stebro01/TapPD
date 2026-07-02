"""GESTEN tab – pose overview with inline recording and auto hand detection."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from capture.base_capture import HandFrame
from gesture_lab.battery import get_battery_definitions
from gesture_lab.feature_extraction import (
    compute_template_from_frames,
    compute_dynamic_template,
)
from gesture_lab.gesture_db import (
    list_templates_for_pose,
    save_template,
)
from gesture_lab.models import GestureTemplate
from storage.database import get_db
from ui import theme
from ui.theme import SZ
from ui.hand_visualization import HandVisualizationWidget

if TYPE_CHECKING:
    from ui.gesture_lab_screen import GestureLabScreen

log = logging.getLogger(__name__)


# ── Pose card (reused pattern) ────────────────────────────────────

class _PoseCard(QFrame):
    """Compact card for one battery pose in the left list."""

    def __init__(self, definition: GestureTemplate, count: int = 0,
                 hands: str = "", on_click=None) -> None:
        super().__init__()
        self.pose_number = definition.pose_number
        self._on_click = on_click
        self.selected = False

        self.setFixedHeight(64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # Pose number
        num = QLabel(f"#{definition.pose_number}")
        num.setFont(QFont("Helvetica Neue", 10, QFont.Weight.Bold))
        num.setStyleSheet(f"color: {theme.PRIMARY};")
        num.setFixedWidth(24)
        layout.addWidget(num)

        # Name + type
        info = QVBoxLayout()
        info.setSpacing(1)
        name = QLabel(definition.name)
        name.setFont(QFont("Helvetica Neue", 11, QFont.Weight.Bold))
        info.addWidget(name)

        sub = QLabel(f"{definition.gesture_type} | {definition.clinical_source}")
        sub.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 10px;")
        info.addWidget(sub)
        layout.addLayout(info, stretch=1)

        # Count + hands
        right_col = QVBoxLayout()
        right_col.setSpacing(1)
        if count > 0:
            cnt = QLabel(f"{count}")
            cnt.setFont(QFont("Helvetica Neue", 12, QFont.Weight.Bold))
            cnt.setStyleSheet(f"color: {theme.ACCENT};")
            cnt.setAlignment(Qt.AlignmentFlag.AlignRight)
            right_col.addWidget(cnt)
            if hands:
                h_lbl = QLabel(hands)
                h_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 9px;")
                h_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
                right_col.addWidget(h_lbl)
        else:
            empty = QLabel("–")
            empty.setStyleSheet(f"color: {theme.BORDER}; font-size: 14px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignRight)
            right_col.addWidget(empty)
        layout.addLayout(right_col)

    def _update_style(self) -> None:
        if self.selected:
            bg = theme.PRIMARY_LIGHT
            border = theme.PRIMARY
        else:
            bg = theme.CARD_BG
            border = theme.BORDER
        self.setStyleSheet(
            f"_PoseCard {{ background-color: {bg}; border: 1px solid {border}; "
            f"border-radius: 6px; }}"
        )

    def set_selected(self, sel: bool) -> None:
        self.selected = sel
        self._update_style()

    def mousePressEvent(self, event) -> None:
        if self._on_click:
            self._on_click(self.pose_number)


# ── Main panel ────────────────────────────────────────────────────

class GestenPanel(QWidget):
    """Tab 1: Pose overview with inline recording."""

    def __init__(self, lab_screen: GestureLabScreen) -> None:
        super().__init__()
        self.lab_screen = lab_screen
        self._cards: dict[int, _PoseCard] = {}
        self._selected_pose: int = 1
        self._recording = False
        self._detected_hand: str | None = None
        self._frames: list[HandFrame] = []
        self._lock = threading.Lock()
        self._latest_frame: HandFrame | None = None

        self._build_ui()

        self._live_timer = QTimer(self)
        self._live_timer.setInterval(33)
        self._live_timer.timeout.connect(self._poll_live)

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left: pose list ───────────────────────────────────────
        left_widget = QWidget()
        left_widget.setFixedWidth(260)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(6)

        header = QLabel("Verfügbare Gesten")
        header.setFont(QFont("Helvetica Neue", 12, QFont.Weight.Bold))
        left_layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        scroll.setWidget(self._list_widget)
        left_layout.addWidget(scroll, stretch=1)

        # Library export/import (share recorded reference poses between installs)
        lib_row = QHBoxLayout()
        lib_row.setSpacing(4)
        self.export_btn = QPushButton("⬆ Export")
        self.export_btn.setToolTip("Alle aufgenommenen Referenzposen als JSON-Bibliothek exportieren")
        self.export_btn.setFixedHeight(SZ.BTN_H)
        self.export_btn.clicked.connect(self._on_export_library)
        lib_row.addWidget(self.export_btn)
        self.import_btn = QPushButton("⬇ Import")
        self.import_btn.setToolTip("Gesten-Bibliothek (JSON) importieren")
        self.import_btn.setFixedHeight(SZ.BTN_H)
        self.import_btn.clicked.connect(self._on_import_library)
        lib_row.addWidget(self.import_btn)
        left_layout.addLayout(lib_row)

        root.addWidget(left_widget)

        # Separator
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background-color: {theme.BORDER};")
        root.addWidget(sep)

        # ── Right: detail / recording (stacked) ──────────────────
        self.right_stack = QStackedWidget()

        # Page 0: Detail view
        self.detail_page = QWidget()
        self._build_detail_page()
        self.right_stack.addWidget(self.detail_page)

        # Page 1: Recording view
        self.record_page = QWidget()
        self._build_record_page()
        self.right_stack.addWidget(self.record_page)

        root.addWidget(self.right_stack, stretch=1)

    # ── Detail page ───────────────────────────────────────────────

    def _build_detail_page(self) -> None:
        layout = QVBoxLayout(self.detail_page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.detail_title = QLabel("")
        self.detail_title.setFont(QFont("Helvetica Neue", 16, QFont.Weight.Bold))
        layout.addWidget(self.detail_title)

        self.detail_desc = QLabel("")
        self.detail_desc.setWordWrap(True)
        self.detail_desc.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self.detail_desc)

        # Extension pattern
        self.detail_ext = QLabel("")
        self.detail_ext.setStyleSheet("font-size: 14px; letter-spacing: 3px;")
        layout.addWidget(self.detail_ext)

        # Meta
        self.detail_meta = QLabel("")
        self.detail_meta.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 11px;")
        layout.addWidget(self.detail_meta)

        layout.addSpacing(8)

        # Recordings table
        rec_label = QLabel("Aufnahmen")
        rec_label.setFont(QFont("Helvetica Neue", 12, QFont.Weight.Bold))
        layout.addWidget(rec_label)

        self.rec_table = QTableWidget()
        self.rec_table.setColumnCount(4)
        self.rec_table.setHorizontalHeaderLabels(["ID", "Hand", "Typ", "Erstellt"])
        self.rec_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.rec_table.setColumnWidth(0, 45)
        self.rec_table.setColumnWidth(1, 60)
        self.rec_table.setColumnWidth(2, 60)
        self.rec_table.verticalHeader().setVisible(False)
        self.rec_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rec_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rec_table.verticalHeader().setDefaultSectionSize(SZ.ROW_H)
        self.rec_table.clicked.connect(self._on_rec_clicked)
        self.rec_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.rec_table.customContextMenuRequested.connect(self._on_rec_context_menu)
        layout.addWidget(self.rec_table, stretch=1)

        # Buttons
        btn_row = QHBoxLayout()

        self.record_btn = QPushButton("Aufnahme starten")
        self.record_btn.setProperty("cssClass", "accent")
        self.record_btn.setFixedHeight(SZ.BTN_H)
        self.record_btn.clicked.connect(self._start_recording)
        btn_row.addWidget(self.record_btn)

        self.detect_btn = QPushButton("Erkennung starten")
        self.detect_btn.setProperty("cssClass", "primary")
        self.detect_btn.setFixedHeight(SZ.BTN_H)
        self.detect_btn.clicked.connect(self._go_detect)
        btn_row.addWidget(self.detect_btn)

        layout.addLayout(btn_row)

    # ── Record page ───────────────────────────────────────────────

    def _build_record_page(self) -> None:
        layout = QVBoxLayout(self.record_page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.rec_title = QLabel("")
        self.rec_title.setFont(QFont("Helvetica Neue", 14, QFont.Weight.Bold))
        layout.addWidget(self.rec_title)

        self.rec_desc = QLabel("")
        self.rec_desc.setWordWrap(True)
        self.rec_desc.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self.rec_desc)

        self.hand_viz = HandVisualizationWidget()
        layout.addWidget(self.hand_viz, stretch=1)

        # Status
        self.rec_status = QLabel("Hand über den Sensor halten")
        self.rec_status.setFont(QFont("Helvetica Neue", 14, QFont.Weight.Bold))
        self.rec_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.rec_status)

        self.rec_hand_label = QLabel("")
        self.rec_hand_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rec_hand_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self.rec_hand_label)

        self.rec_frame_label = QLabel("")
        self.rec_frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rec_frame_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        layout.addWidget(self.rec_frame_label)

        # Buttons
        btn_row = QHBoxLayout()

        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.setFixedHeight(SZ.BTN_H)
        self.cancel_btn.clicked.connect(self._cancel_recording)
        btn_row.addWidget(self.cancel_btn)

        self.start_rec_btn = QPushButton("Aufnahme starten")
        self.start_rec_btn.setProperty("cssClass", "accent")
        self.start_rec_btn.setFixedHeight(SZ.BTN_H)
        self.start_rec_btn.clicked.connect(self._begin_capture)
        btn_row.addWidget(self.start_rec_btn)

        self.stop_btn = QPushButton("Aufnahme stoppen")
        self.stop_btn.setFixedHeight(SZ.BTN_H)
        self.stop_btn.setStyleSheet(
            f"background-color: {theme.DANGER}; color: white; font-weight: bold;"
        )
        self.stop_btn.clicked.connect(self._stop_and_save)
        self.stop_btn.setVisible(False)
        btn_row.addWidget(self.stop_btn)

        layout.addLayout(btn_row)

    # ── Pose list ─────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_list()

    def _refresh_list(self) -> None:
        # Clear list
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()

        conn = get_db()
        try:
            from gesture_lab.gesture_db import list_templates
            all_templates = list_templates(conn)
        finally:
            conn.close()

        # Group by pose
        counts: dict[int, int] = {}
        hands: dict[int, set[str]] = {}
        for t in all_templates:
            counts[t.pose_number] = counts.get(t.pose_number, 0) + 1
            hands.setdefault(t.pose_number, set()).add(t.hand_type)

        hand_map = {"right": "R", "left": "L", "any": "B"}

        for defn in get_battery_definitions():
            pn = defn.pose_number
            c = counts.get(pn, 0)
            h_set = hands.get(pn, set())
            h_str = " ".join(sorted(hand_map.get(h, h) for h in h_set)) if h_set else ""

            card = _PoseCard(defn, count=c, hands=h_str, on_click=self._on_pose_clicked)
            self._list_layout.addWidget(card)
            self._cards[pn] = card

        self._list_layout.addStretch()

        # Select first or previously selected
        if self._selected_pose in self._cards:
            self._on_pose_clicked(self._selected_pose)
        elif self._cards:
            first = next(iter(self._cards))
            self._on_pose_clicked(first)

    def _on_pose_clicked(self, pose_number: int) -> None:
        self._selected_pose = pose_number
        for pn, card in self._cards.items():
            card.set_selected(pn == pose_number)
        self._update_detail(pose_number)

    def _update_detail(self, pose_number: int) -> None:
        battery = get_battery_definitions()
        defn = next((d for d in battery if d.pose_number == pose_number), None)
        if not defn:
            return

        self.detail_title.setText(f"#{defn.pose_number} {defn.name}")
        self.detail_desc.setText(defn.description)

        ext_str = "  ".join(
            f"{'●' if e else '○'}" for e in defn.expected_extensions
        )
        finger_labels = "D  Z   M  R  K"
        self.detail_ext.setText(f"{ext_str}\n{finger_labels}")

        self.detail_meta.setText(
            f"Quelle: {defn.clinical_source} | Typ: {defn.gesture_type}\n"
            f"Scoring: {defn.scoring_criteria}"
        )

        # Load recordings
        conn = get_db()
        try:
            recordings = list_templates_for_pose(conn, pose_number)
        finally:
            conn.close()

        hand_map = {"right": "R", "left": "L", "any": "B"}
        self.rec_table.setRowCount(len(recordings))
        for row, t in enumerate(recordings):
            id_item = QTableWidgetItem()
            id_item.setData(Qt.ItemDataRole.DisplayRole, t.id)
            self.rec_table.setItem(row, 0, id_item)
            self.rec_table.setItem(row, 1, QTableWidgetItem(
                hand_map.get(t.hand_type, t.hand_type)
            ))
            self.rec_table.setItem(row, 2, QTableWidgetItem(t.gesture_type))
            self.rec_table.setItem(row, 3, QTableWidgetItem(t.created_at))

    # ── Recording ─────────────────────────────────────────────────

    def _start_recording(self) -> None:
        """Step 1: Switch to preview page with live hand visualization."""
        capture = self.lab_screen.capture
        if capture is None or not capture.is_connected():
            self.rec_status.setText("Kein Sensor verbunden")
            self.rec_status.setStyleSheet(f"color: {theme.DANGER};")
            return

        # Source-aware skeleton projection (Leap = top-down, Kamera = frontal).
        from capture.source import source_kind
        self.hand_viz.set_projection(
            "frontal" if source_kind(capture) == "webcam" else "topdown")

        battery = get_battery_definitions()
        defn = next((d for d in battery if d.pose_number == self._selected_pose), None)
        if defn:
            self.rec_title.setText(f"#{defn.pose_number} {defn.name}")
            self.rec_desc.setText(f"{defn.description}\nScoring: {defn.scoring_criteria}")

        # Reset UI to preview state
        self.rec_status.setText("Hand über den Sensor halten")
        self.rec_status.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        self.rec_hand_label.setText("")
        self.rec_frame_label.setText("")
        self.start_rec_btn.setVisible(True)
        self.stop_btn.setVisible(False)

        # Switch to record page
        self.right_stack.setCurrentIndex(1)

        # Start live preview (not recording yet)
        self._recording = False
        capture.start_recording(self._on_preview_frame)
        self._live_timer.start()

    def _on_preview_frame(self, frame: HandFrame) -> None:
        """Receive frames for live preview (before actual recording)."""
        self._latest_frame = frame

    def _begin_capture(self) -> None:
        """Step 2: Start actual recording (user clicked 'Aufnahme starten')."""
        capture = self.lab_screen.capture
        if capture is None:
            return

        # Stop preview, start recording
        try:
            capture.stop_recording()
        except Exception:
            pass

        with self._lock:
            self._frames.clear()
        self._detected_hand = None
        self._other_hand_count = 0
        self._recording = True

        self.rec_status.setText("Aufnahme läuft...")
        self.rec_status.setStyleSheet(f"color: {theme.DANGER};")
        self.rec_hand_label.setText("Hand wird erkannt...")
        self.rec_frame_label.setText("")
        self.start_rec_btn.setVisible(False)

        battery = get_battery_definitions()
        defn = next((d for d in battery if d.pose_number == self._selected_pose), None)
        is_dynamic = defn and defn.gesture_type == "dynamic"
        self.stop_btn.setVisible(is_dynamic)

        capture.start_recording(self._on_frame)

        # Auto-stop for static after 3 seconds
        if not is_dynamic:
            QTimer.singleShot(3000, self._auto_stop_static)

    def _auto_stop_static(self) -> None:
        if self._recording:
            self._stop_and_save()

    def _on_frame(self, frame: HandFrame) -> None:
        if not self._recording:
            return

        # Auto hand detection: track which hands appear
        if self._detected_hand is None:
            # First frame determines the hand
            self._detected_hand = frame.hand_type
            self._other_hand_count = 0
        elif frame.hand_type != self._detected_hand:
            # Other hand appeared – count occurrences but don't abort immediately
            self._other_hand_count = getattr(self, '_other_hand_count', 0) + 1
            # Only show warning, don't record this frame
            if self._other_hand_count > 20:
                # Persistent second hand – warn but keep recording the primary hand
                self.rec_status.setText("Nur eine Hand verwenden!")
                self.rec_status.setStyleSheet(f"color: {theme.WARN};")
            return  # skip frames from the other hand

        with self._lock:
            self._frames.append(frame)
        self._latest_frame = frame

    def _poll_live(self) -> None:
        frame = self._latest_frame
        self.hand_viz.update_frame(frame)
        if self._recording:
            with self._lock:
                n = len(self._frames)
            self.rec_frame_label.setText(f"{n} Frames")
            if self._detected_hand:
                hand_name = {"right": "Rechts", "left": "Links"}.get(
                    self._detected_hand, self._detected_hand
                )
                self.rec_hand_label.setText(f"Erkannte Hand: {hand_name}")

    def _stop_and_save(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self._live_timer.stop()

        capture = self.lab_screen.capture
        if capture:
            try:
                capture.stop_recording()
            except Exception:
                pass

        with self._lock:
            frames = list(self._frames)

        if len(frames) < 10:
            self.rec_status.setText("Zu wenige Frames – nochmal versuchen")
            self.rec_status.setStyleSheet(f"color: {theme.WARN};")
            return

        # Build template
        battery = get_battery_definitions()
        defn = next((d for d in battery if d.pose_number == self._selected_pose), None)

        hand_type = self._detected_hand or "any"
        name = defn.name if defn else "Unbenannt"
        gtype = defn.gesture_type if defn else "static"

        template = GestureTemplate(
            name=name,
            description=defn.description if defn else "",
            clinical_source=defn.clinical_source if defn else "",
            gesture_type=gtype,
            hand_type=hand_type,
            pose_number=self._selected_pose,
            expected_extensions=defn.expected_extensions if defn else [True] * 5,
            finger_weights=defn.finger_weights if defn else [1.0] * 5,
            scoring_criteria=defn.scoring_criteria if defn else "",
            raw_frames=[f.to_dict() for f in frames],
        )

        # Compute pose vector
        median_vec, var_vec = compute_template_from_frames(frames)
        template.pose_vector = median_vec.tolist()
        template.pose_variance = var_vec.tolist()

        if gtype == "dynamic":
            dyn_frames, duration = compute_dynamic_template(frames)
            template.dynamic_frames = dyn_frames
            template.dynamic_duration_s = duration

        # Save
        conn = get_db()
        try:
            tid = save_template(conn, template)
            hand_name = {"right": "Rechts", "left": "Links"}.get(hand_type, hand_type)
            self.rec_status.setText(
                f"Gespeichert (ID {tid}, {hand_name}, {len(frames)} Frames)"
            )
            self.rec_status.setStyleSheet(f"color: {theme.ACCENT};")
            log.info("Template saved: %s (id=%d, hand=%s, frames=%d)",
                     name, tid, hand_type, len(frames))
        except Exception as e:
            self.rec_status.setText(f"Fehler: {e}")
            self.rec_status.setStyleSheet(f"color: {theme.DANGER};")
            log.error("Failed to save template: %s", e)
        finally:
            conn.close()

        # Return to detail view after short delay
        QTimer.singleShot(1500, self._return_to_detail)

    def _cancel_recording(self) -> None:
        self._recording = False
        self._live_timer.stop()
        capture = self.lab_screen.capture
        if capture:
            try:
                capture.stop_recording()
            except Exception:
                pass
        self.right_stack.setCurrentIndex(0)

    def _return_to_detail(self) -> None:
        self.right_stack.setCurrentIndex(0)
        self._refresh_list()

    def stop_recording(self) -> None:
        """Called externally (e.g. when leaving the lab)."""
        if self._recording:
            self._cancel_recording()

    # ── Recordings table interaction ─────────────────────────────

    def _get_rec_id(self, row: int) -> int | None:
        item = self.rec_table.item(row, 0)
        return item.data(Qt.ItemDataRole.DisplayRole) if item else None

    def _on_rec_clicked(self, index) -> None:
        """Click on recording → go to Analyse tab with this recording selected."""
        tid = self._get_rec_id(index.row())
        if tid is not None:
            self._go_analyse(tid)

    def _on_rec_context_menu(self, pos) -> None:
        row = self.rec_table.rowAt(pos.y())
        if row < 0:
            return
        tid = self._get_rec_id(row)
        if tid is None:
            return

        menu = QMenu(self)
        analyse_action = menu.addAction("Zur Analyse")
        menu.addSeparator()
        delete_action = menu.addAction("Löschen")
        delete_action.setIcon(self.style().standardIcon(
            self.style().StandardPixmap.SP_TrashIcon
        ))

        action = menu.exec(self.rec_table.viewport().mapToGlobal(pos))
        if action == analyse_action:
            self._go_analyse(tid)
        elif action == delete_action:
            self._delete_recording(tid)

    def _go_analyse(self, template_id: int) -> None:
        if hasattr(self.lab_screen, 'analysis_panel'):
            self.lab_screen.analysis_panel.select_by_id(template_id)
            self.lab_screen._switch_mode(1)

    def _delete_recording(self, template_id: int) -> None:
        reply = QMessageBox.question(
            self, "Aufzeichnung löschen",
            f"Aufzeichnung (ID {template_id}) wirklich löschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            from gesture_lab.gesture_db import delete_template
            conn = get_db()
            try:
                delete_template(conn, template_id)
            finally:
                conn.close()
            self._update_detail(self._selected_pose)
            self._refresh_list()

    # ── Navigation ────────────────────────────────────────────────

    def _go_detect(self) -> None:
        if hasattr(self.lab_screen, 'detect_panel'):
            self.lab_screen.detect_panel.select_pose(self._selected_pose)
            self.lab_screen._switch_mode(2)

    # ── Library export / import ───────────────────────────────────

    def _on_export_library(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        from gesture_lab.gesture_db import export_library
        path, _ = QFileDialog.getSaveFileName(
            self, "Gesten-Bibliothek exportieren", "gesten_bibliothek.json",
            "Gesten-Bibliothek (*.json)")
        if not path:
            return
        conn = get_db()
        try:
            n = export_library(conn, path)
        except Exception as e:   # noqa: BLE001
            log.exception("Bibliothek-Export fehlgeschlagen")
            QMessageBox.critical(self, "Gesture Lab", f"Export fehlgeschlagen:\n{e}")
            return
        finally:
            conn.close()
        QMessageBox.information(self, "Gesture Lab",
                                f"{n} Referenzaufnahme(n) exportiert nach:\n{path}")

    def _on_import_library(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        from gesture_lab.gesture_db import import_library
        path, _ = QFileDialog.getOpenFileName(
            self, "Gesten-Bibliothek importieren", "",
            "Gesten-Bibliothek (*.json);;Alle Dateien (*)")
        if not path:
            return
        replace = QMessageBox.question(
            self, "Gesten-Bibliothek importieren",
            "Bestehende Referenzaufnahmen vorher LÖSCHEN?\n\n"
            "Ja = Bibliothek ersetzen · Nein = zusätzlich importieren",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No)
        if replace == QMessageBox.StandardButton.Cancel:
            return
        conn = get_db()
        try:
            n = import_library(conn, path,
                               replace=replace == QMessageBox.StandardButton.Yes)
        except Exception as e:   # noqa: BLE001
            log.exception("Bibliothek-Import fehlgeschlagen")
            QMessageBox.critical(self, "Gesture Lab", f"Import fehlgeschlagen:\n{e}")
            return
        finally:
            conn.close()
        self._refresh_list()
        if self._selected_pose:
            self._update_detail(self._selected_pose)
        QMessageBox.information(self, "Gesture Lab",
                                f"{n} Referenzaufnahme(n) importiert.")
