"""Pinch gesture state machine with hysteresis for grab/release detection."""

from enum import Enum, auto
from capture.base_capture import HandFrame


class PinchEvent(Enum):
    GRAB = auto()
    RELEASE = auto()


class PinchDetector:
    """Detects pinch-grab and pinch-release transitions from HandFrame stream.

    Uses hysteresis (two thresholds) to prevent chattering.
    """

    def __init__(
        self,
        grab_threshold_mm: float = 25.0,
        release_threshold_mm: float = 40.0,
        debounce_frames: int = 3,
    ) -> None:
        self.grab_threshold = grab_threshold_mm
        self.release_threshold = release_threshold_mm
        self.debounce_frames = debounce_frames

        self._is_pinching = False
        self._consecutive = 0
        self._last_below = False

    def update(self, frame: HandFrame) -> PinchEvent | None:
        dist = frame.pinch_distance
        below = dist < self.grab_threshold
        above = dist > self.release_threshold

        if not self._is_pinching:
            # Looking for grab
            if below:
                self._consecutive += 1
                if self._consecutive >= self.debounce_frames:
                    self._is_pinching = True
                    self._consecutive = 0
                    return PinchEvent.GRAB
            else:
                self._consecutive = 0
        else:
            # Looking for release
            if above:
                self._consecutive += 1
                if self._consecutive >= self.debounce_frames:
                    self._is_pinching = False
                    self._consecutive = 0
                    return PinchEvent.RELEASE
            else:
                self._consecutive = 0

        return None

    @property
    def is_pinching(self) -> bool:
        return self._is_pinching

    def reset(self) -> None:
        self._is_pinching = False
        self._consecutive = 0
