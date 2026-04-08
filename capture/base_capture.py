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
class HandFrame:
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
    def from_dict(cls, d: dict) -> "HandFrame":
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


class BaseCaptureDevice(ABC):
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
