"""Abstract base class for motor tests."""

import logging
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from capture.base_capture import BaseCaptureDevice, HandFrame

log = logging.getLogger(__name__)


class BaseMotorTest(ABC):
    """Base class for all motor tests.

    For unilateral tests (finger_tapping, hand_open_close, pronation_supination),
    only frames matching self.hand are collected.

    For bilateral tests (postural_tremor, rest_tremor), frames from both hands
    are collected and stored separately.
    """

    bilateral: bool = False  # Override in bilateral subclasses

    def __init__(
        self,
        capture: BaseCaptureDevice,
        duration: float = 10.0,
        hand: str = "right",
    ) -> None:
        self.capture = capture
        self.duration = duration
        self.hand = hand
        self.frames: list[HandFrame] = []
        self.left_frames: list[HandFrame] = []
        self.right_frames: list[HandFrame] = []
        self._lock = threading.Lock()

    def _on_frame(self, frame: HandFrame) -> None:
        """Callback invoked by capture device for each frame."""
        if self.bilateral:
            with self._lock:
                if frame.hand_type == "left":
                    self.left_frames.append(frame)
                elif frame.hand_type == "right":
                    self.right_frames.append(frame)
        else:
            if frame.hand_type != self.hand:
                return
            with self._lock:
                self.frames.append(frame)

    def start(self) -> None:
        """Start recording frames from the capture device."""
        self.frames.clear()
        self.left_frames.clear()
        self.right_frames.clear()
        self.capture.start_recording(self._on_frame)
        log.info("Aufnahme gestartet: %s (Hand: %s, Dauer: %.1fs, bilateral: %s)",
                 self.test_type(), self.hand, self.duration, self.bilateral)

    def stop(self) -> None:
        """Stop recording."""
        self.capture.stop_recording()
        n = len(self.frames) + len(self.left_frames) + len(self.right_frames)
        log.info("Aufnahme gestoppt: %s – %d Frames aufgenommen", self.test_type(), n)

    def get_frames(self, hand: str | None = None) -> list[HandFrame]:
        """Thread-safe copy of recorded frames.

        For bilateral tests, pass hand="left"/"right" to get specific hand.
        """
        with self._lock:
            if self.bilateral:
                if hand == "left":
                    return list(self.left_frames)
                elif hand == "right":
                    return list(self.right_frames)
                # Return all frames interleaved (for live metric)
                return sorted(
                    self.left_frames + self.right_frames,
                    key=lambda f: f.timestamp_us,
                )
            return list(self.frames)

    @abstractmethod
    def compute_features(self) -> dict[str, float]:
        """Compute test-specific features from recorded frames."""
        ...

    @abstractmethod
    def get_instructions(self) -> str:
        """Return instruction text for the patient."""
        ...

    @abstractmethod
    def get_live_metric(self, frame: HandFrame) -> float:
        """Return a single metric value for real-time plotting."""
        ...

    @abstractmethod
    def get_live_metric_label(self) -> str:
        """Label for the Y-axis of the live plot."""
        ...

    @abstractmethod
    def test_type(self) -> str:
        """Identifier string for this test type."""
        ...

    def get_instruction_figure_path(self) -> Path | None:
        """Path to instruction image, or None."""
        p = Path(__file__).parent.parent / "assets" / f"instr_{self.test_type()}.png"
        return p if p.exists() else None
