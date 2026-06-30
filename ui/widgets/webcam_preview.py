"""Webcam preview widget: the latest JPEG frame with a MediaPipe landmark overlay.

Shared by the Eingabequelle (tracking) screen and VideoLab (it previously lived
as a private class in tracking_screen and was imported across modules).
"""

from __future__ import annotations

import base64

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor, QPen
from PyQt6.QtWidgets import QWidget

# MediaPipe hand-skeleton connections (landmark index pairs) for the overlay.
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),            # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),            # index
    (5, 9), (9, 10), (10, 11), (11, 12),       # middle
    (9, 13), (13, 14), (14, 15), (15, 16),     # ring
    (13, 17), (17, 18), (18, 19), (19, 20),    # pinky
    (0, 17),                                   # palm base
]


class WebcamPreview(QWidget):
    """Shows the latest webcam JPEG with the MediaPipe landmark overlay."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(360, 300)
        self._pixmap: QPixmap | None = None
        self._landmarks: list = []  # list of hands, each a list of [x,y] normalized
        self._face: list = []       # 478 [x,y] normalized face landmarks (incl. iris)
        self._placeholder = "Warte auf Kamerabild …"

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        if self._pixmap is None:
            self.update()

    def set_frame(self, jpeg_b64: str, landmarks: list, face: list | None = None) -> None:
        if jpeg_b64:
            img = QImage.fromData(base64.b64decode(jpeg_b64), "JPG")
            self._pixmap = QPixmap.fromImage(img) if not img.isNull() else None
        self._landmarks = landmarks or []
        self._face = face or []
        self.update()

    def clear(self) -> None:
        self._pixmap = None
        self._landmarks = []
        self._face = []
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#212121"))

        if self._pixmap is None:
            p.setPen(QColor("#9E9E9E"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
            p.end()
            return

        # Fit pixmap into the widget, keeping aspect ratio (letterboxed).
        scaled = self._pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        ox = (w - scaled.width()) // 2
        oy = (h - scaled.height()) // 2
        p.drawPixmap(ox, oy, scaled)

        sw, sh = scaled.width(), scaled.height()

        def pt(lm):
            return QPointF(ox + lm[0] * sw, oy + lm[1] * sh)

        for hand in self._landmarks:
            if not hand:
                continue
            p.setPen(QPen(QColor("#00E5FF"), 2))
            for a, b in HAND_CONNECTIONS:
                if a < len(hand) and b < len(hand):
                    p.drawLine(pt(hand[a]), pt(hand[b]))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FFEB3B"))
            for lm in hand:
                p.drawEllipse(pt(lm), 3, 3)

        # Face mesh (478 points; indices 468-477 are the iris/eyes).
        if self._face:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(120, 230, 140, 150))
            for lm in self._face:
                p.drawEllipse(pt(lm), 1.0, 1.0)
            p.setBrush(QColor("#FF4081"))  # iris / eyes
            for i in range(468, min(478, len(self._face))):
                p.drawEllipse(pt(self._face[i]), 2.5, 2.5)
        p.end()
