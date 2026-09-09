"""A video surface that can show the clip mirrored.

``QVideoWidget`` paints frames exactly as stored, but our own takes are
recorded raw (the unmirrored camera view) and imported clips carry their own
flag — so the plain player and the sidecar overlay disagreed about left and
right. This widget takes the player's frames through a ``QVideoSink`` and
flips them when asked, so review and overlay show the same side.
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtMultimedia import QVideoFrame, QVideoSink
from PyQt6.QtWidgets import QWidget


class VideoView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._image: QImage | None = None
        self._mirrored = False
        self.setMinimumHeight(240)
        self.setAutoFillBackground(False)

    @property
    def sink(self) -> QVideoSink:
        return self._sink

    @property
    def mirrored(self) -> bool:
        return self._mirrored

    def set_mirrored(self, on: bool) -> None:
        if self._mirrored != bool(on):
            self._mirrored = bool(on)
            self.update()

    def show_image(self, image: QImage | None) -> None:
        """Show a still (also what the sink does per frame)."""
        self._image = image
        self.update()

    def clear(self) -> None:
        self.show_image(None)

    # ── frames ────────────────────────────────────────────────────
    def _on_frame(self, frame: QVideoFrame) -> None:
        if frame.isValid():
            self._image = frame.toImage()
            self.update()

    # ── painting ──────────────────────────────────────────────────
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000"))
        img = self._image
        if img is None or img.isNull():
            p.end()
            return
        if self._mirrored:
            img = img.mirrored(True, False)
        target = img.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - target.width()) // 2
        y = (self.height() - target.height()) // 2
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(QRect(x, y, target.width(), target.height()), img)
        p.end()
