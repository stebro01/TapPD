"""Per-finger error classification for gesture recognition."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from gesture_lab.models import (
    ErrorType,
    FingerError,
    GestureTemplate,
    FINGER_NAMES,
    N_JOINT_ANGLES,
)


def classify_finger_errors(
    live_vec: NDArray[np.float64],
    frame_extensions: list[bool],
    template: GestureTemplate,
    *,
    extension_threshold: float | None = None,
    position_threshold: float | None = None,
) -> list[FingerError]:
    """Classify per-finger errors comparing live pose to template.

    Parameters
    ----------
    live_vec : ndarray (35,)
        Current pose vector from extract_pose_vector.
    frame_extensions : list[bool]
        is_extended flags from the current HandFrame (5 values).
    template : GestureTemplate
        Template to compare against.
    extension_threshold : float
        Mean joint angle threshold to distinguish extended vs flexed.
    position_threshold : float
        Normalized distance threshold for positional deviations.

    Returns
    -------
    errors : list[FingerError]
        Empty if no errors detected.
    """
    errors: list[FingerError] = []

    if not template.expected_extensions or len(frame_extensions) < 5:
        return errors

    # Load thresholds from config if not explicitly provided
    if extension_threshold is None or position_threshold is None:
        from gesture_lab.config import get_scoring_config
        ea = get_scoring_config()["error_analysis"]
        if extension_threshold is None:
            extension_threshold = ea["extension_threshold"]
        if position_threshold is None:
            position_threshold = ea["position_threshold"]

    t_vec = np.array(template.pose_vector) if template.pose_vector else None

    for fid in range(5):
        expected_ext = template.expected_extensions[fid]
        actual_ext = frame_extensions[fid]
        weight = template.finger_weights[fid] if len(template.finger_weights) > fid else 1.0

        # Skip low-weight fingers
        if weight < 0.3:
            continue

        # --- Extension errors ---
        if expected_ext and not actual_ext:
            # Should be extended but isn't
            # Check if partially extended via joint angles
            angle_start = fid * 4
            angle_end = angle_start + 4
            if angle_end <= len(live_vec):
                mean_flex = float(np.mean(live_vec[angle_start:angle_end]))
                if mean_flex < extension_threshold:
                    # Only slightly flexed → incomplete
                    errors.append(FingerError(
                        finger_id=fid,
                        finger_name=FINGER_NAMES[fid],
                        error_type=ErrorType.INCOMPLETE_EXTENSION,
                        severity=min(1.0, mean_flex / extension_threshold) * weight,
                        detail=f"{FINGER_NAMES[fid]}: nicht vollständig gestreckt",
                    ))
                else:
                    errors.append(FingerError(
                        finger_id=fid,
                        finger_name=FINGER_NAMES[fid],
                        error_type=ErrorType.WRONG_EXTENSION,
                        severity=weight,
                        detail=f"{FINGER_NAMES[fid]}: sollte gestreckt sein, ist gebeugt",
                    ))

        elif not expected_ext and actual_ext:
            # Should be flexed but is extended
            errors.append(FingerError(
                finger_id=fid,
                finger_name=FINGER_NAMES[fid],
                error_type=ErrorType.EXTRA_EXTENSION,
                severity=0.7 * weight,
                detail=f"{FINGER_NAMES[fid]}: sollte gebeugt sein, ist gestreckt",
            ))

        # --- Positional error (if template vector available) ---
        if t_vec is not None and len(t_vec) >= N_JOINT_ANGLES:
            angle_start = fid * 4
            angle_end = angle_start + 4
            if angle_end <= len(live_vec) and angle_end <= len(t_vec):
                live_angles = live_vec[angle_start:angle_end]
                tmpl_angles = t_vec[angle_start:angle_end]
                diff = float(np.linalg.norm(live_angles - tmpl_angles))
                if diff > position_threshold and expected_ext == actual_ext:
                    # Extension state correct but angles differ
                    errors.append(FingerError(
                        finger_id=fid,
                        finger_name=FINGER_NAMES[fid],
                        error_type=ErrorType.SPATIAL_ERROR,
                        severity=min(1.0, diff / (position_threshold * 2)) * weight,
                        detail=f"{FINGER_NAMES[fid]}: Winkelabweichung ({diff:.2f} rad)",
                    ))

    return errors


def classify_palm_error(
    live_vec: NDArray[np.float64],
    template: GestureTemplate,
    *,
    orientation_threshold: float | None = None,
) -> FingerError | None:
    """Check palm orientation error."""
    if not template.pose_vector:
        return None

    if orientation_threshold is None:
        from gesture_lab.config import get_scoring_config
        orientation_threshold = get_scoring_config()["error_analysis"]["orientation_threshold"]

    t_vec = np.array(template.pose_vector)
    # Palm euler at indices 30-32
    orient_start = N_JOINT_ANGLES + 5 + 5  # 30
    orient_end = orient_start + 3

    if orient_end > len(live_vec) or orient_end > len(t_vec):
        return None

    diff = live_vec[orient_start:orient_end] - t_vec[orient_start:orient_end]
    diff = (diff + math.pi) % (2 * math.pi) - math.pi
    angular_dist = float(np.linalg.norm(diff))

    if angular_dist > orientation_threshold:
        return FingerError(
            finger_id=-1,
            finger_name="Handfläche",
            error_type=ErrorType.SPATIAL_ERROR,
            severity=min(1.0, angular_dist / math.pi),
            detail=f"Handorientierung abweichend ({math.degrees(angular_dist):.0f}°)",
        )
    return None
