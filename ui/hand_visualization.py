"""Reusable QPainter-based 2D hand skeleton visualization widget."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPolygonF
from PyQt6.QtWidgets import QWidget

if TYPE_CHECKING:
    from capture.base_capture import HandFrame
    from gesture_lab.models import GestureTemplate

# Finger colors (thumb through pinky)
FINGER_COLORS = [
    QColor("#E91E63"),  # thumb - pink
    QColor("#2196F3"),  # index - blue
    QColor("#4CAF50"),  # middle - green
    QColor("#FF9800"),  # ring - orange
    QColor("#9C27B0"),  # pinky - purple
]

FINGER_COLORS_LIGHT = [c.lighter(160) for c in FINGER_COLORS]

PALM_COLOR = QColor("#ECEFF1")
PALM_BORDER = QColor("#90A4AE")
BONE_COLOR = QColor("#455A64")
JOINT_COLOR = QColor("#37474F")

GHOST_ALPHA = 60  # transparency for template overlay


class HandVisualizationWidget(QWidget):
    """2D dorsal (top-down) hand skeleton renderer.

    Feed live HandFrame data via ``update_frame()`` and optionally set a
    ghost template via ``set_ghost_template()``.  Rendering is triggered by
    calling ``update()`` (usually from a QTimer at 30 fps).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 400)

        self._frame: HandFrame | None = None
        self._ghost_frame: HandFrame | None = None
        self._ghost_template: GestureTemplate | None = None
        self._projection: str = "topdown"  # or "frontal" (webcam)

        # Per-finger error highlight (finger_id -> color)
        self._finger_highlights: dict[int, QColor] = {}

        # Status text shown bottom-center
        self._status_text: str = ""
        self._status_color: QColor = QColor("#757575")

    # ── Public API ────────────────────────────────────────────────

    def update_frame(self, frame: HandFrame | None) -> None:
        self._frame = frame
        self.update()

    def set_ghost_frame(self, frame: HandFrame | None) -> None:
        """Set a ghost hand to render as semi-transparent overlay."""
        self._ghost_frame = frame

    def set_ghost_template(self, template: GestureTemplate | None) -> None:
        self._ghost_template = template

    def set_finger_highlights(self, highlights: dict[int, QColor]) -> None:
        """Set per-finger color highlights for error display."""
        self._finger_highlights = highlights

    def set_status(self, text: str, color: QColor | None = None) -> None:
        self._status_text = text
        self._status_color = color or QColor("#757575")

    # ── Coordinate mapping ────────────────────────────────────────

    def _map_point(self, x_mm: float, y_mm: float, z_mm: float,
                   w: int, h: int) -> QPointF:
        """Map 3D hand coordinates to widget 2D, auto-centered on the palm.

        Projection:
          * "topdown" (Leap): hand held *over* a flat sensor → X-Z plane.
          * "frontal" (webcam): hand held *facing* the camera → X-Y plane, so a
            stop-hand renders upright/facing as it actually appears.
        """
        rx = x_mm - self._center_x

        margin = 20
        usable = min(w - 2 * margin, h - 2 * margin)
        scale = usable / 180.0  # 180mm range → hand fills widget

        if self._projection == "frontal":
            rb = y_mm - self._center_y
            # Mirror X so it reads like a selfie/mirror view (webcams are mirrored),
            # which is the intuitive orientation when facing the camera.
            px = w / 2 - rx * scale
            # Anchor the palm below centre: fingers point up, so this keeps the
            # fingertips in frame and uses the empty space below the wrist.
            py = h * 0.62 + rb * scale
        else:
            rb = z_mm - self._center_z
            px = w / 2 + rx * scale
            py = h / 2 + rb * scale
        return QPointF(px, py)

    def set_projection(self, mode: str) -> None:
        """'topdown' (Leap, X-Z) or 'frontal' (webcam, X-Y)."""
        self._projection = mode
        self.update()

    # Palm center for auto-centering (updated per draw call)
    _center_x: float = 0.0
    _center_y: float = 0.0
    _center_z: float = 0.0

    # ── Paint ─────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        p.fillRect(0, 0, w, h, QColor("#FAFAFA"))

        # Border
        p.setPen(QPen(QColor("#E0E0E0"), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        # Ghost template overlay (semi-transparent)
        if self._ghost_frame:
            self._draw_hand(p, self._ghost_frame, w, h, ghost=True)

        # Live hand
        if self._frame:
            self._draw_hand(p, self._frame, w, h, ghost=False)
        else:
            # No hand detected
            p.setPen(QColor("#BDBDBD"))
            p.setFont(QFont("Helvetica Neue", 14))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                       "Keine Hand erkannt")

        # Status text
        if self._status_text:
            p.setPen(self._status_color)
            p.setFont(QFont("Helvetica Neue", 11, QFont.Weight.Bold))
            p.drawText(QRectF(0, h - 35, w, 30),
                       Qt.AlignmentFlag.AlignCenter, self._status_text)

        # Confidence indicator
        if self._frame:
            self._draw_confidence(p, self._frame.confidence, w)

        p.end()

    def _draw_hand(self, p: QPainter, frame: HandFrame, w: int, h: int,
                   ghost: bool = False) -> None:
        alpha = GHOST_ALPHA if ghost else 255

        # Center on palm position
        self._center_x = frame.palm_position[0]
        self._center_y = frame.palm_position[1]
        self._center_z = frame.palm_position[2]

        palm_pt = self._map_point(*frame.palm_position, w, h)

        # Draw palm polygon from finger base positions
        if len(frame.fingers) >= 5:
            palm_pts = QPolygonF()
            base_points: list[QPointF] = []
            for finger in frame.fingers:
                if finger.bones:
                    base = finger.bones[0].prev_joint
                else:
                    # Approximate base as midpoint between palm and tip
                    tip = finger.tip_position
                    base = (
                        (frame.palm_position[0] + tip[0]) / 2,
                        (frame.palm_position[1] + tip[1]) / 2,
                        (frame.palm_position[2] + tip[2]) / 2,
                    )
                bp = self._map_point(*base, w, h)
                base_points.append(bp)
                palm_pts.append(bp)

            # Close palm polygon
            palm_color = QColor(PALM_COLOR)
            palm_color.setAlpha(alpha)
            border = QColor(PALM_BORDER)
            border.setAlpha(alpha)

            p.setPen(QPen(border, 1.5))
            p.setBrush(QBrush(palm_color))
            p.drawPolygon(palm_pts)

            # Draw palm center
            pc = QColor(JOINT_COLOR)
            pc.setAlpha(alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(pc))
            p.drawEllipse(palm_pt, 4, 4)

        # Draw fingers (bones + joints + tips)
        for i, finger in enumerate(frame.fingers):
            # Color for this finger
            if not ghost and i in self._finger_highlights:
                color = self._finger_highlights[i]
            else:
                color = QColor(FINGER_COLORS[i])
            color.setAlpha(alpha)

            light = QColor(FINGER_COLORS_LIGHT[i])
            light.setAlpha(alpha)

            bone_c = QColor(BONE_COLOR)
            bone_c.setAlpha(alpha)

            if finger.bones:
                # Draw each bone as a line
                for bi, bone in enumerate(finger.bones):
                    p1 = self._map_point(*bone.prev_joint, w, h)
                    p2 = self._map_point(*bone.next_joint, w, h)

                    # Bone line
                    pen_width = 3.0 if not ghost else 2.0
                    p.setPen(QPen(bone_c, pen_width))
                    p.drawLine(p1, p2)

                    # Joint circle
                    jc = QColor(color)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QBrush(jc))
                    r = 3 if not ghost else 2
                    p.drawEllipse(p1, r, r)

            # Draw tip circle
            tip_pt = self._map_point(*finger.tip_position, w, h)
            tip_r = 5 if not ghost else 3
            p.setPen(QPen(color.darker(120), 1.0))
            p.setBrush(QBrush(light if finger.is_extended else color))
            p.drawEllipse(tip_pt, tip_r, tip_r)

            # Finger label (only for non-ghost, if widget is large enough)
            if not ghost and w > 400:
                label_c = QColor(color)
                label_c.setAlpha(180)
                p.setPen(label_c)
                p.setFont(QFont("Helvetica Neue", 8))
                labels = ["D", "Z", "M", "R", "K"]
                p.drawText(QRectF(tip_pt.x() - 6, tip_pt.y() - 20, 12, 14),
                           Qt.AlignmentFlag.AlignCenter, labels[i])

    def _draw_confidence(self, p: QPainter, confidence: float, w: int) -> None:
        """Draw a small confidence bar at the top."""
        bar_w = 60
        bar_h = 4
        x = w - bar_w - 10
        y = 8

        # Background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#E0E0E0")))
        p.drawRoundedRect(x, y, bar_w, bar_h, 2, 2)

        # Fill
        if confidence > 0.7:
            fill_color = QColor("#4CAF50")
        elif confidence > 0.4:
            fill_color = QColor("#FF9800")
        else:
            fill_color = QColor("#E53935")

        fill_w = int(bar_w * min(confidence, 1.0))
        p.setBrush(QBrush(fill_color))
        p.drawRoundedRect(x, y, fill_w, bar_h, 2, 2)

        # Label
        p.setPen(QColor("#757575"))
        p.setFont(QFont("Helvetica Neue", 8))
        p.drawText(x - 30, y, 28, bar_h + 8, Qt.AlignmentFlag.AlignRight, "Conf")
