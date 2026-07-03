"""A minimal Audacity-style timeline: scrub + select an onset/offset region.

Drives off the QMediaPlayer clock (set via setDuration/setPosition). Emits
rangeChanged(start_s, end_s) as the handles move and seekRequested(ms) on click.
Deliberately simple — a single track bar, two draggable handles, one playhead.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QRectF
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen
from PyQt6.QtWidgets import QWidget
from ui import theme

_HANDLE_HIT = 7   # px grab tolerance around a handle
_PAD = 10         # horizontal padding so end handles stay clickable


class VideoTimeline(QWidget):
    rangeChanged = pyqtSignal(float, float)   # (start_s, end_s)
    seekRequested = pyqtSignal(int)           # ms

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(56)
        self.setMinimumWidth(320)
        self._dur_ms = 0
        self._pos_ms = 0
        self._onset_ms = 0
        self._offset_ms = 0
        self._drag = None   # "onset" | "offset" | "seek" | None

    # ── external clock ────────────────────────────────────────────
    def setDuration(self, ms: int) -> None:
        self._dur_ms = max(0, int(ms))
        self._onset_ms = 0
        self._offset_ms = self._dur_ms
        self._emit_range()
        self.update()

    def setPosition(self, ms: int) -> None:
        self._pos_ms = max(0, min(int(ms), self._dur_ms))
        self.update()

    def range_s(self) -> tuple[float, float]:
        return self._onset_ms / 1000.0, self._offset_ms / 1000.0

    # ── geometry helpers ──────────────────────────────────────────
    def _track_w(self) -> int:
        return max(1, self.width() - 2 * _PAD)

    def _ms_to_x(self, ms: int) -> float:
        if self._dur_ms <= 0:
            return _PAD
        return _PAD + self._track_w() * (ms / self._dur_ms)

    def _x_to_ms(self, x: float) -> int:
        if self._dur_ms <= 0:
            return 0
        frac = (x - _PAD) / self._track_w()
        return int(max(0, min(1.0, frac)) * self._dur_ms)

    def _emit_range(self) -> None:
        self.rangeChanged.emit(self._onset_ms / 1000.0, self._offset_ms / 1000.0)

    # ── interaction ───────────────────────────────────────────────
    def mousePressEvent(self, e) -> None:
        if self._dur_ms <= 0:
            return
        x = e.position().x()
        if abs(x - self._ms_to_x(self._onset_ms)) <= _HANDLE_HIT:
            self._drag = "onset"
        elif abs(x - self._ms_to_x(self._offset_ms)) <= _HANDLE_HIT:
            self._drag = "offset"
        else:
            self._drag = "seek"
            self.seekRequested.emit(self._x_to_ms(x))
        self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is None or self._dur_ms <= 0:
            return
        ms = self._x_to_ms(e.position().x())
        if self._drag == "onset":
            self._onset_ms = min(ms, self._offset_ms - 1)
            self._emit_range()
        elif self._drag == "offset":
            self._offset_ms = max(ms, self._onset_ms + 1)
            self._emit_range()
        elif self._drag == "seek":
            self.seekRequested.emit(ms)
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        self._drag = None

    # ── paint ─────────────────────────────────────────────────────
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cy = h / 2
        # track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#ECEFF1")))
        p.drawRoundedRect(QRectF(_PAD, cy - 9, self._track_w(), 18), 4, 4)
        if self._dur_ms <= 0:
            p.setPen(QColor("#90A4AE"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Kein Video geladen")
            return
        x0, x1 = self._ms_to_x(self._onset_ms), self._ms_to_x(self._offset_ms)
        # selected region
        p.setBrush(QBrush(QColor(25, 118, 210, 70)))
        p.drawRect(QRectF(x0, cy - 9, x1 - x0, 18))
        # onset/offset handles
        p.setBrush(QBrush(QColor(f"{theme.PRIMARY}")))
        for x in (x0, x1):
            p.drawRoundedRect(QRectF(x - 3, cy - 14, 6, 28), 2, 2)
        # playhead
        px = self._ms_to_x(self._pos_ms)
        p.setPen(QPen(QColor(f"{theme.DANGER}"), 2))
        p.drawLine(int(px), int(cy - 16), int(px), int(cy + 16))
