"""Central data structures and abstract base for capture devices."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class BoneData:
    prev_joint: tuple[float, float, float]  # mm
    next_joint: tuple[float, float, float]


@dataclass
class FingerData:
    finger_id: int  # 0=thumb, 1=index, 2=middle, 3=ring, 4=pinky
    tip_position: tuple[float, float, float]
    is_extended: bool
    bones: list[BoneData] = field(default_factory=list)  # up to 4 bones


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
