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
class TrackingFrame:
    """Multimodal envelope for one timestamp: the unit a future multimodal
    callback delivers. Today sources still push per-hand ``HandPose`` objects;
    this type is the target that makes hands/face/gaze uniform (see ARCHITECTURE.md).
    """
    timestamp_us: int
    hands: list[HandPose] = field(default_factory=list)
    face: object | None = None   # Stage-2: FacePose
    gaze: object | None = None   # Stage-2: GazePose

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

    Naming note: this is the canonical name; ``BaseCaptureDevice`` is a
    backward-compatible alias. The hand-only callback signature is the Stage-2
    migration point toward a multimodal ``TrackingFrame`` envelope (see
    ARCHITECTURE.md).
    """

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


# Backward-compatible alias (pre-consolidation name). Stage 2 removes it.
BaseCaptureDevice = MotionSource
