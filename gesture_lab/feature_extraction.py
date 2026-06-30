"""Extract normalized pose vectors from HandFrame data."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from gesture_lab.models import (
    STATIC_DIM,
    N_JOINT_ANGLES,
    N_ABDUCTION,
    N_TIP_DISTANCES,
    N_PALM_ORIENT,
)

if TYPE_CHECKING:
    from capture.base_capture import BoneData, FingerData, HandFrame


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _bone_direction(bone: BoneData) -> NDArray[np.float64]:
    return np.array(bone.next_joint) - np.array(bone.prev_joint)


def _angle_between(v1: NDArray, v2: NDArray) -> float:
    """Angle in radians between two vectors."""
    n1 = np.linalg.norm(v1)
    n2 = np.linalg.norm(v2)
    if n1 < 1e-8 or n2 < 1e-8:
        return 0.0
    cos_a = np.dot(v1, v2) / (n1 * n2)
    return float(np.arccos(np.clip(cos_a, -1.0, 1.0)))


def _vec3(t: tuple[float, float, float]) -> NDArray[np.float64]:
    return np.array(t, dtype=np.float64)


# ---------------------------------------------------------------------------
# Joint angle computation
# ---------------------------------------------------------------------------

def compute_joint_angles(finger: FingerData) -> list[float]:
    """Return up to 4 flexion angles for a finger's bones.

    When full bone data is available (4 bones), computes the angle between
    consecutive bone direction vectors.  When only 1 bone is present
    (SimulationSource), falls back to a simplified estimate based on
    ``is_extended`` and tip-to-palm geometry.
    """
    if len(finger.bones) >= 2:
        angles: list[float] = []
        for i in range(len(finger.bones) - 1):
            d1 = _bone_direction(finger.bones[i])
            d2 = _bone_direction(finger.bones[i + 1])
            angles.append(_angle_between(d1, d2))
        # Pad to 4 if fewer bones
        while len(angles) < 4:
            angles.append(0.0)
        return angles[:4]

    # Fallback: single-bone or no-bone – estimate from is_extended
    if finger.is_extended:
        return [0.05, 0.05, 0.05, 0.05]   # nearly straight
    else:
        return [0.8, 1.2, 1.0, 0.3]        # moderately flexed


def compute_abduction_angles(fingers: list[FingerData]) -> list[float]:
    """Return 5 inter-finger abduction/splay angles.

    Indices: thumb-index, index-middle, middle-ring, ring-pinky, thumb-opposition.
    Uses metacarpal bone directions when available, else tip positions.
    """
    tips = [_vec3(f.tip_position) for f in fingers]
    angles: list[float] = []

    # Use metacarpal directions if full bone data available
    has_bones = all(len(f.bones) >= 2 for f in fingers)

    for i in range(4):  # 4 adjacent pairs
        if has_bones:
            d1 = _bone_direction(fingers[i].bones[0])
            d2 = _bone_direction(fingers[i + 1].bones[0])
            angles.append(_angle_between(d1, d2))
        else:
            # Fallback: angle at palm between tip vectors
            v1 = tips[i] - tips[2]  # relative to middle finger
            v2 = tips[i + 1] - tips[2]
            a = _angle_between(v1, v2) if np.linalg.norm(v1) > 1e-6 else 0.0
            angles.append(a)

    # Thumb opposition: angle between thumb tip-palm and index tip-palm vectors
    if has_bones:
        d_thumb = _bone_direction(fingers[0].bones[0])
        d_index = _bone_direction(fingers[1].bones[0])
        angles.append(_angle_between(d_thumb, d_index))
    else:
        v_thumb = tips[0] - tips[2]
        v_index = tips[1] - tips[2]
        angles.append(_angle_between(v_thumb, v_index) if np.linalg.norm(v_thumb) > 1e-6 else 0.0)

    return angles


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _reference_length(frame: HandFrame) -> float:
    """Hand-size reference: distance from palm to middle fingertip."""
    palm = _vec3(frame.palm_position)
    mid_tip = _vec3(frame.fingers[2].tip_position) if len(frame.fingers) > 2 else palm
    length = float(np.linalg.norm(mid_tip - palm))
    return max(length, 1.0)  # avoid division by zero


def compute_normalized_tip_distances(frame: HandFrame) -> list[float]:
    """Tip-to-palm distance for each finger, divided by reference length."""
    palm = _vec3(frame.palm_position)
    ref = _reference_length(frame)
    return [float(np.linalg.norm(_vec3(f.tip_position) - palm) / ref)
            for f in frame.fingers]


def palm_to_euler(palm_normal: tuple[float, float, float]) -> tuple[float, float, float]:
    """Convert palm normal to (roll, pitch, yaw) in radians."""
    nx, ny, nz = palm_normal
    roll = math.atan2(nx, -ny)
    pitch = math.atan2(nz, -ny)
    yaw = math.atan2(nx, nz) if abs(nz) > 1e-8 else 0.0
    return (roll, pitch, yaw)


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract_pose_vector(frame: HandFrame) -> tuple[NDArray[np.float64], list[bool]]:
    """Extract a 35-dim hand-size-invariant pose vector from a single HandFrame.

    Returns
    -------
    vector : ndarray of shape (35,)
        [20 joint angles | 5 abduction | 5 tip dists | 3 palm euler | 2 derived]
    confidence_mask : list[bool]
        Per-finger flag indicating whether full bone data was available.
    """
    if len(frame.fingers) < 5:
        # Incomplete frame – return zeros with all-False mask
        return np.zeros(STATIC_DIM), [False] * 5

    # Per-finger confidence: True if >= 2 bones available
    conf_mask = [len(f.bones) >= 2 for f in frame.fingers]

    # 1) Joint flexion angles (20)
    joint_angles: list[float] = []
    for f in frame.fingers:
        joint_angles.extend(compute_joint_angles(f))

    # 2) Abduction / splay angles (5)
    abduction = compute_abduction_angles(frame.fingers)

    # 3) Normalized tip distances (5)
    tip_dists = compute_normalized_tip_distances(frame)

    # 4) Palm orientation (3)
    euler = palm_to_euler(frame.palm_normal)

    # 5) Derived features (2)
    grab = frame.grab_strength
    # Spread ratio: max inter-tip distance / reference length
    tips = [_vec3(f.tip_position) for f in frame.fingers]
    max_spread = 0.0
    for i in range(5):
        for j in range(i + 1, 5):
            d = float(np.linalg.norm(tips[i] - tips[j]))
            if d > max_spread:
                max_spread = d
    ref = _reference_length(frame)
    spread_ratio = max_spread / ref

    vec = np.array(
        joint_angles + abduction + tip_dists + list(euler) + [grab, spread_ratio],
        dtype=np.float64,
    )
    assert vec.shape == (STATIC_DIM,), f"Expected {STATIC_DIM}, got {vec.shape}"
    return vec, conf_mask


def extract_dynamic_vector(
    frame: HandFrame,
    prev_frame: HandFrame | None = None,
    dt: float = 0.02,
) -> NDArray[np.float64]:
    """Extract a 41-dim vector for dynamic gesture frames.

    Appends 3 palm velocity + 3 angular velocity to the static 35-dim vector.
    """
    static_vec, _ = extract_pose_vector(frame)

    if prev_frame is None:
        return np.concatenate([static_vec, np.zeros(6)])

    # Palm velocity (normalized by reference length)
    ref = _reference_length(frame)
    dp = _vec3(frame.palm_position) - _vec3(prev_frame.palm_position)
    palm_vel = dp / (ref * max(dt, 1e-6))

    # Angular velocity from palm_normal change
    e_cur = np.array(palm_to_euler(frame.palm_normal))
    e_prev = np.array(palm_to_euler(prev_frame.palm_normal))
    ang_vel = (e_cur - e_prev) / max(dt, 1e-6)

    return np.concatenate([static_vec, palm_vel, ang_vel])


# ---------------------------------------------------------------------------
# Template creation from multiple frames
# ---------------------------------------------------------------------------

def compute_template_from_frames(
    frames: list[HandFrame],
    min_confidence: float = 0.5,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Compute a median pose vector and per-dim variance from a list of frames.

    Parameters
    ----------
    frames : list[HandFrame]
        Typically ~2 seconds of recording (~240 frames at 120 Hz).
    min_confidence : float
        Skip frames below this confidence threshold.

    Returns
    -------
    median_vector : ndarray (35,)
    variance_vector : ndarray (35,)
    """
    vectors: list[NDArray] = []
    for f in frames:
        if f.confidence < min_confidence:
            continue
        vec, _ = extract_pose_vector(f)
        vectors.append(vec)

    if not vectors:
        return np.zeros(STATIC_DIM), np.ones(STATIC_DIM)

    mat = np.stack(vectors)  # (N, 35)
    median_vec = np.median(mat, axis=0)
    variance_vec = np.var(mat, axis=0)
    return median_vec, variance_vec


def compute_dynamic_template(
    frames: list[HandFrame],
    target_rate: float = 50.0,
    min_confidence: float = 0.5,
) -> tuple[list[list[float]], float]:
    """Build a dynamic template (time series of 41-dim vectors).

    Returns
    -------
    series : list of lists (N x 41)
    duration_s : float
    """
    good = [f for f in frames if f.confidence >= min_confidence]
    if len(good) < 2:
        return [], 0.0

    duration_s = (good[-1].timestamp_us - good[0].timestamp_us) / 1e6

    # Extract vectors
    series: list[NDArray] = []
    prev: HandFrame | None = None
    for f in good:
        dt = (f.timestamp_us - prev.timestamp_us) / 1e6 if prev else 0.02
        series.append(extract_dynamic_vector(f, prev, dt))
        prev = f

    # Resample to target_rate
    if len(series) < 2:
        return [s.tolist() for s in series], duration_s

    mat = np.stack(series)  # (N, 41)
    n_orig = mat.shape[0]
    n_target = max(2, int(duration_s * target_rate))

    # Linear interpolation per dimension
    x_orig = np.linspace(0, 1, n_orig)
    x_target = np.linspace(0, 1, n_target)
    resampled = np.zeros((n_target, mat.shape[1]))
    for d in range(mat.shape[1]):
        resampled[:, d] = np.interp(x_target, x_orig, mat[:, d])

    return resampled.tolist(), duration_s
