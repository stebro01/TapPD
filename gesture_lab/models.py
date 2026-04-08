"""Data models for the Gesture Lab."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class GestureType(Enum):
    STATIC = "static"
    DYNAMIC = "dynamic"


class ErrorType(Enum):
    WRONG_EXTENSION = auto()       # finger extended when should be flexed (or vice versa)
    INCOMPLETE_EXTENSION = auto()  # partial extension when full expected
    EXTRA_EXTENSION = auto()       # irrelevant finger incorrectly extended
    SPATIAL_ERROR = auto()         # palm orientation wrong
    SEARCH_MOVEMENT = auto()       # excessive adjustment before settling


FINGER_NAMES = ["Daumen", "Zeigefinger", "Mittelfinger", "Ringfinger", "Kleiner Finger"]
FINGER_NAMES_SHORT = ["D", "Z", "M", "R", "K"]


@dataclass
class FingerError:
    finger_id: int          # 0-4
    finger_name: str        # from FINGER_NAMES
    error_type: ErrorType
    severity: float         # 0.0-1.0
    detail: str             # human-readable description


@dataclass
class GestureTemplate:
    id: int | None = None
    name: str = ""
    description: str = ""
    clinical_source: str = ""          # "TULIA" / "AST" / "Goldenberg"
    gesture_type: str = "static"       # "static" / "dynamic"
    hand_type: str = "any"             # "left" / "right" / "any"
    pose_number: int = 0               # 1-12 from battery

    # Static: 35-dim pose vector (median over recording window)
    # Layout: [20 joint angles, 5 abduction angles, 5 norm tip dists, 3 palm euler, 2 derived]
    pose_vector: list[float] = field(default_factory=list)
    pose_variance: list[float] = field(default_factory=list)

    # Per-finger importance weights (len 5)
    finger_weights: list[float] = field(default_factory=lambda: [1.0] * 5)

    # Expected extension pattern (len 5, True = extended)
    expected_extensions: list[bool] = field(default_factory=lambda: [True] * 5)

    # Raw recorded frames (serialized HandFrame dicts for playback)
    raw_frames: list[dict] = field(default_factory=list)

    # Dynamic: time series of 41-dim vectors at 50 Hz
    dynamic_frames: list[list[float]] = field(default_factory=list)
    dynamic_duration_s: float = 0.0
    dynamic_sample_rate: float = 50.0

    # Scoring thresholds
    threshold_good: float = 0.85
    threshold_partial: float = 0.60

    # Metadata
    scoring_criteria: str = ""
    thumbnail_path: str = ""
    created_at: str = ""


# Dimension constants
N_JOINT_ANGLES = 20       # 5 fingers x 4 joints
N_ABDUCTION = 5           # 5 inter-finger angles (including thumb opposition)
N_TIP_DISTANCES = 5       # 5 normalized tip distances
N_PALM_ORIENT = 3         # roll, pitch, yaw
N_DERIVED = 2             # grab_strength, spread_ratio
STATIC_DIM = N_JOINT_ANGLES + N_ABDUCTION + N_TIP_DISTANCES + N_PALM_ORIENT + N_DERIVED  # 35
DYNAMIC_EXTRA = 6         # 3 palm velocity + 3 angular velocity
DYNAMIC_DIM = STATIC_DIM + DYNAMIC_EXTRA  # 41
