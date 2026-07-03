"""Unified, source-agnostic pre-test readiness gate.

Every paradigm (motor tests + spatial tasks) starts the same way: show a live
hand model, wait until a hand is presented well enough for *this* source (held
over the Leap sensor, or simply visible to the webcam), then run a 1-2-3
countdown and hand control back to the screen.

It is rendered as a translucent **overlay** on top of the host screen, so it
needs no layout surgery in the screens it serves — they just create one, call
``begin(...)`` and connect ``ready``.

Frames arrive on the capture device's background thread, so the latest frame is
stashed and everything else (readiness counting, viz refresh, countdown) runs on
the GUI thread from a QTimer — the same pattern the gesture lab uses.
"""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import Qt, QTimer, QEvent, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

log = logging.getLogger(__name__)

from capture.base_capture import HandFrame
from capture.source import SourceProfile
from ui.hand_visualization import HandVisualizationWidget
from ui import theme

STABLE_S = 0.6          # hand must stay "ready" this long before the countdown
COUNTDOWN_FROM = 3      # 3-2-1
DEFAULT_TIMEOUT_S = 30.0


class ReadinessGate(QWidget):
    """Overlay that gates the start of a paradigm on hand readiness + countdown.

    Signals:
        ready(str)   -- emitted with the detected hand ("left"/"right"/"both")
        cancelled()  -- emitted on timeout or cancel
    """

    ready = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._capture = None
        self._profile: SourceProfile | None = None
        self._require_hand: str | None = None   # "left"/"right"/"both"/None(auto)
        self._timeout_s = DEFAULT_TIMEOUT_S

        self._latest_frame: HandFrame | None = None
        self._ready_since: float | None = None
        self._detected_hand: str | None = None
        self._start_t = 0.0
        self._countdown = COUNTDOWN_FROM
        self._phase = "idle"  # idle | detect | countdown

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("ReadinessGate { background-color: rgba(15,20,25,0.82); }")

        self._build_ui()

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(33)
        self._tick_timer.timeout.connect(self._tick)
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._countdown_tick)

        parent.installEventFilter(self)
        self.hide()

    # ── UI ────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        panel = QFrame()
        panel.setStyleSheet(
            "QFrame { background: #FFFFFF; border-radius: 16px; }"
        )
        panel.setFixedWidth(420)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(24, 24, 24, 24)
        pl.setSpacing(12)
        pl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._hand_viz = HandVisualizationWidget()
        self._hand_viz.setFixedSize(360, 320)
        pl.addWidget(self._hand_viz, alignment=Qt.AlignmentFlag.AlignCenter)

        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        self._status.setStyleSheet("font-size: 16px; color: #37474F; font-weight: 600;")
        pl.addWidget(self._status)

        self._countdown_label = QLabel("")
        self._countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._countdown_label.setStyleSheet(
            f"font-size: 64px; color: {theme.PRIMARY}; font-weight: 800;"
        )
        self._countdown_label.setMinimumHeight(80)
        pl.addWidget(self._countdown_label)

        outer.addWidget(panel)

    # keep the overlay covering the host screen
    def eventFilter(self, obj, event) -> bool:
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.parent().rect())
        return False

    # ── public API ────────────────────────────────────────────────
    def begin(self, capture, profile: SourceProfile, require_hand: str | None = None,
              timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        """Show the gate and wait for hand readiness, then countdown.

        require_hand: "left"/"right" (motor tests), "both" (bilateral) or None
        (spatial tasks — auto-detect whichever hand appears).
        """
        self._capture = capture
        self._profile = profile
        self._require_hand = require_hand
        self._timeout_s = timeout_s

        self._latest_frame = None
        self._ready_since = None
        self._detected_hand = None
        self._countdown = COUNTDOWN_FROM
        self._countdown_label.setText("")
        self._phase = "detect"
        self._start_t = time.perf_counter()
        self._status.setText(profile.prompts().get("waiting", "Hand bereithalten..."))
        self._hand_viz.update_frame(None)

        self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()

        capture.start_recording(self._on_frame)
        self._tick_timer.start()

    def cancel(self) -> None:
        self._teardown()
        self.hide()

    # ── frame intake (device thread) → stash ──────────────────────
    def _on_frame(self, frame: HandFrame) -> None:
        self._latest_frame = frame

    # ── readiness loop (GUI thread) ───────────────────────────────
    def _tick(self) -> None:
        try:
            frame = self._latest_frame
            self._hand_viz.update_frame(frame)
            if self._phase != "detect":
                return

            if self._frame_is_ready(frame):
                now = time.perf_counter()
                if self._ready_since is None:
                    self._ready_since = now
                    self._detected_hand = self._resolve_hand(frame)
                if now - self._ready_since >= STABLE_S:
                    self._begin_countdown()
                    return
            else:
                self._ready_since = None

            if time.perf_counter() - self._start_t > self._timeout_s:
                self._on_timeout()
        except Exception:
            log.exception("Fehler im Readiness-Gate")

    def _frame_is_ready(self, frame: HandFrame | None) -> bool:
        if frame is None or self._profile is None:
            return False
        if not self._profile.hand_ready(frame):
            return False
        if self._require_hand in ("left", "right"):
            return frame.hand_type == self._require_hand
        return True

    def _resolve_hand(self, frame: HandFrame) -> str:
        if self._require_hand in ("left", "right", "both"):
            return self._require_hand
        return frame.hand_type  # auto (spatial tasks)

    def _on_timeout(self) -> None:
        self._phase = "idle"
        self._tick_timer.stop()
        self._stop_capture()
        msg = self._profile.prompts().get("timeout", "Keine Hand erkannt.") if self._profile else ""
        self._status.setText(msg)
        QTimer.singleShot(1800, self._emit_cancelled)

    # ── countdown ─────────────────────────────────────────────────
    def _begin_countdown(self) -> None:
        self._phase = "countdown"
        detected_msg = self._profile.prompts().get("detected", "Hand erkannt!") if self._profile else ""
        self._status.setText(detected_msg)
        self._countdown = COUNTDOWN_FROM
        self._countdown_label.setText(str(self._countdown))
        self._countdown_timer.start()

    def _countdown_tick(self) -> None:
        self._countdown -= 1
        if self._countdown > 0:
            self._countdown_label.setText(str(self._countdown))
            return
        self._countdown_timer.stop()
        self._countdown_label.setText("")
        hand = self._detected_hand or "right"
        self._teardown()
        self.hide()
        self.ready.emit(hand)

    # ── cleanup ───────────────────────────────────────────────────
    def _emit_cancelled(self) -> None:
        self.hide()
        self.cancelled.emit()

    def _stop_capture(self) -> None:
        if self._capture is not None:
            try:
                self._capture.stop_recording()
            except Exception:
                pass

    def _teardown(self) -> None:
        self._phase = "idle"
        self._tick_timer.stop()
        self._countdown_timer.stop()
        self._stop_capture()
