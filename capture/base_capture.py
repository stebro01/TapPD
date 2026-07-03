"""Central data structures and abstract base for capture devices."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class BoneData:
    prev_joint: tuple[float, float, float]  # mm
    next_joint: tuple[float, float, float]

    def to_dict(self) -> dict:
        return {"pj": list(self.prev_joint), "nj": list(self.next_joint)}

    @classmethod
    def from_dict(cls, d: dict) -> "BoneData":
        return cls(prev_joint=tuple(d["pj"]), next_joint=tuple(d["nj"]))


@dataclass
class FingerData:
    finger_id: int  # 0=thumb, 1=index, 2=middle, 3=ring, 4=pinky
    tip_position: tuple[float, float, float]
    is_extended: bool
    bones: list[BoneData] = field(default_factory=list)  # up to 4 bones

    def to_dict(self) -> dict:
        return {
            "id": self.finger_id,
            "tp": list(self.tip_position),
            "ex": self.is_extended,
            "bn": [b.to_dict() for b in self.bones],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FingerData":
        return cls(
            finger_id=d["id"],
            tip_position=tuple(d["tp"]),
            is_extended=d["ex"],
            bones=[BoneData.from_dict(b) for b in d.get("bn", [])],
        )


@dataclass
class HandPose:
    """One hand at one timestamp. Canonical name; ``HandFrame`` is an alias.

    Stage-2 note: a multimodal ``TrackingFrame`` envelope (below) carries a list
    of these plus optional face/gaze, so paradigms can consume any modality
    through one callback. The per-hand callback is still used today.
    """
    timestamp_us: int
    hand_type: str  # "left" / "right"
    palm_position: tuple[float, float, float]
    palm_velocity: tuple[float, float, float]
    palm_normal: tuple[float, float, float] = (0.0, -1.0, 0.0)  # palm facing direction
    fingers: list[FingerData] = field(default_factory=list)  # 5 fingers
    pinch_distance: float = 0.0
    grab_strength: float = 0.0
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "ts": self.timestamp_us,
            "ht": self.hand_type,
            "pp": list(self.palm_position),
            "pv": list(self.palm_velocity),
            "pn": list(self.palm_normal),
            "fg": [f.to_dict() for f in self.fingers],
            "pd": self.pinch_distance,
            "gs": self.grab_strength,
            "cf": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HandPose":
        return cls(
            timestamp_us=d["ts"],
            hand_type=d["ht"],
            palm_position=tuple(d["pp"]),
            palm_velocity=tuple(d["pv"]),
            palm_normal=tuple(d.get("pn", [0.0, -1.0, 0.0])),
            fingers=[FingerData.from_dict(f) for f in d.get("fg", [])],
            pinch_distance=d.get("pd", 0.0),
            grab_strength=d.get("gs", 0.0),
            confidence=d.get("cf", 1.0),
        )


# Backward-compatible alias (pre-consolidation name). Stage 2 finishes migration.
HandFrame = HandPose


@dataclass
class FacePose:
    """Eye-centric face state of one camera frame (from the sidecar's
    ``face`` message): iris centres + eye corners in image PIXELS and the
    eye-aspect-ratio per eye. Enough for fixation stability, saccadic
    intrusions and blink metrics without shipping 478 landmarks."""
    timestamp_us: int
    iris_left: tuple[float, float]
    iris_right: tuple[float, float]
    corners_left: tuple[tuple[float, float], tuple[float, float]]   # outer, inner
    corners_right: tuple[tuple[float, float], tuple[float, float]]
    ear_left: float = 0.0     # eye aspect ratio (small = closed/blink)
    ear_right: float = 0.0
    nose: tuple[float, float] | None = None   # nose tip px (head-yaw proxy)

    @property
    def ipd_px(self) -> float:
        dx = self.iris_right[0] - self.iris_left[0]
        dy = self.iris_right[1] - self.iris_left[1]
        return (dx * dx + dy * dy) ** 0.5

    @property
    def ear(self) -> float:
        return (self.ear_left + self.ear_right) / 2.0

    @property
    def gaze_offset_ipd(self) -> tuple[float, float]:
        """Iris midpoint relative to the eye-corner midpoint, in IPD units.

        Head-motion-robust fixation proxy: corners move with the head, so the
        offset isolates eye-in-head movement; dividing by the IPD makes it
        scale-(distance-)invariant."""
        ipd = self.ipd_px
        if ipd < 1e-6:
            return (0.0, 0.0)
        cx = (self.corners_left[0][0] + self.corners_left[1][0]
              + self.corners_right[0][0] + self.corners_right[1][0]) / 4.0
        cy = (self.corners_left[0][1] + self.corners_left[1][1]
              + self.corners_right[0][1] + self.corners_right[1][1]) / 4.0
        ix = (self.iris_left[0] + self.iris_right[0]) / 2.0
        iy = (self.iris_left[1] + self.iris_right[1]) / 2.0
        return ((ix - cx) / ipd, (iy - cy) / ipd)

    @property
    def eye_roll_deg(self) -> float:
        """Tilt of the inter-iris line (head roll proxy)."""
        import math
        dx = self.iris_right[0] - self.iris_left[0]
        dy = self.iris_right[1] - self.iris_left[1]
        return math.degrees(math.atan2(dy, dx)) if abs(dx) > 1e-6 else 0.0

    @property
    def nose_shift_ipd(self) -> float | None:
        """Horizontal nose offset from the iris midpoint, in IPD units
        (head-yaw proxy; None when the source sends no nose point)."""
        if self.nose is None:
            return None
        ipd = self.ipd_px
        if ipd < 1e-6:
            return None
        ix = (self.iris_left[0] + self.iris_right[0]) / 2.0
        return (self.nose[0] - ix) / ipd


@dataclass
class TrackingFrame:
    """Multimodal envelope for one sensor frame: all hands of that instant,
    plus face/gaze on capable sources. Delivered via
    ``MotionSource.start_tracking``."""
    timestamp_us: int
    hands: list[HandPose] = field(default_factory=list)
    face: FacePose | None = None
    gaze: object | None = None   # future: GazePose (calibrated gaze ray)

    @property
    def left(self) -> HandPose | None:
        return next((h for h in self.hands if h.hand_type == "left"), None)

    @property
    def right(self) -> HandPose | None:
        return next((h for h in self.hands if h.hand_type == "right"), None)

    def to_dict(self) -> dict:
        return {"ts": self.timestamp_us, "hands": [h.to_dict() for h in self.hands]}

    @classmethod
    def from_dict(cls, d: dict) -> "TrackingFrame":
        return cls(timestamp_us=d.get("ts", 0),
                   hands=[HandPose.from_dict(h) for h in d.get("hands", [])])


class MotionSource(ABC):
    """Abstract motion-tracking source (Leap, webcam, simulation, …).

    Contract:
      * ``connect()`` establishes the underlying connection (may block on device
        discovery / sidecar spawn); ``disconnect()`` tears it down and stops any
        background threads. ``is_connected()`` is a cheap health check.
      * ``start_recording(callback)`` begins streaming; ``callback`` is invoked
        **once per hand per frame** on a background thread — consumers must be
        thread-safe (stash + poll from a QTimer, as the UI does). One frame today
        carries a single hand; bilateral tests receive two callbacks per tick.
      * ``stop_recording()`` halts the stream. ``sample_rate`` is the live Hz.
      * ``start_tracking(callback)`` is the **multimodal envelope** variant:
        ``callback`` receives one ``TrackingFrame`` per sensor frame carrying
        ALL hands of that instant (and, on capable sources, face/gaze). The
        concrete sources emit envelopes natively at their per-sensor-frame
        point; this base class provides a single-hand fallback wrapper.

    Naming note: this is the canonical name; ``BaseCaptureDevice`` is a
    backward-compatible alias.
    """

    # Multimodal consumer (set by start_tracking). Native sources check this
    # at their emission point; their stop_recording() MUST reset it so a later
    # plain start_recording() gets per-hand frames again.
    _tracking_callback: Callable[["TrackingFrame"], None] | None = None

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def start_recording(self, callback: Callable[[HandFrame], None]) -> None: ...

    @abstractmethod
    def stop_recording(self) -> None: ...

    @property
    @abstractmethod
    def sample_rate(self) -> float: ...

    # ── multimodal envelope stream ─────────────────────────────────
    def start_tracking(self, callback: Callable[["TrackingFrame"], None]) -> None:
        """Stream TrackingFrames (all hands of one sensor frame, + face/gaze
        where available). Same threading rules as ``start_recording``."""
        self._tracking_callback = callback
        self.start_recording(self._fallback_hand_to_tracking)

    def stop_tracking(self) -> None:
        self._tracking_callback = None
        self.stop_recording()

    def _fallback_hand_to_tracking(self, frame: HandFrame) -> None:
        """Per-hand → envelope fallback for sources without a native emitter."""
        cb = self._tracking_callback
        if cb is not None:
            cb(TrackingFrame(timestamp_us=frame.timestamp_us, hands=[frame]))


# Backward-compatible alias (pre-consolidation name). Stage 2 removes it.
BaseCaptureDevice = MotionSource
