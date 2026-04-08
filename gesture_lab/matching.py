"""Similarity scoring for gesture templates."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from gesture_lab.models import (
    GestureTemplate,
    N_JOINT_ANGLES,
    N_ABDUCTION,
    N_TIP_DISTANCES,
    N_PALM_ORIENT,
    STATIC_DIM,
)


# ---------------------------------------------------------------------------
# Static pose similarity
# ---------------------------------------------------------------------------

def _cosine_similarity(a: NDArray, b: NDArray) -> float:
    """Cosine similarity in [0, 1]."""
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na < 1e-8 or nb < 1e-8:
        return 0.0
    return float(np.clip(np.dot(a, b) / (na * nb), 0.0, 1.0))


def _orientation_similarity(euler_a: NDArray, euler_b: NDArray) -> float:
    """Similarity based on angular distance between two Euler triplets.

    Returns value in [0, 1] where 1 = identical orientation.
    """
    diff = euler_a - euler_b
    # Wrap to [-pi, pi]
    diff = (diff + math.pi) % (2 * math.pi) - math.pi
    angular_dist = float(np.linalg.norm(diff))
    return max(0.0, 1.0 - angular_dist / math.pi)


def _mirror_orientation(vec: NDArray[np.float64]) -> NDArray[np.float64]:
    """Mirror palm orientation for left↔right hand comparison.

    Roll and Yaw are negated (mirrored across sagittal plane).
    Pitch stays the same.
    Layout at orient indices: [roll, pitch, yaw]
    """
    v = vec.copy()
    orient_start = N_JOINT_ANGLES + N_ABDUCTION + N_TIP_DISTANCES
    v[orient_start] = -v[orient_start]      # roll
    v[orient_start + 2] = -v[orient_start + 2]  # yaw
    return v


def static_similarity(
    live_vec: NDArray[np.float64],
    template: GestureTemplate,
    *,
    finger_weights: list[float] | None = None,
    orient_weight: float | None = None,
    live_hand: str | None = None,
    live_extensions: list[bool] | None = None,
) -> float:
    """Compute weighted similarity between a live pose vector and a template.

    Parameters
    ----------
    live_vec : ndarray (35,)
    template : GestureTemplate with pose_vector populated
    finger_weights : per-finger importance (5 values, 0-1). 0 = ignore.
    orient_weight : palm orientation importance (0-1). Default 0.3.
    live_hand : 'left' or 'right' – if different from template.hand_type,
        orientation is mirrored before comparison.
    live_extensions : per-finger is_extended flags from current frame.
    """
    if not template.pose_vector or live_vec.shape[0] < STATIC_DIM:
        return 0.0

    t_vec = np.array(template.pose_vector, dtype=np.float64)
    from gesture_lab.config import get_scoring_config
    sc = get_scoring_config()

    fw = finger_weights if finger_weights is not None else template.finger_weights
    ow = orient_weight if orient_weight is not None else sc["orient_weight_default"]

    needs_mirror = (
        live_hand is not None
        and template.hand_type not in ("any", live_hand)
    )
    compare_vec = _mirror_orientation(live_vec) if needs_mirror else live_vec

    detail = compute_parameter_scores(
        compare_vec, t_vec, fw, ow,
        expected_ext=template.expected_extensions,
        live_ext=live_extensions,
    )
    return detail["total"]


def compute_parameter_scores(
    live_vec: NDArray[np.float64],
    tmpl_vec: NDArray[np.float64],
    finger_weights: list[float],
    orient_weight: float = 0.3,
    live_hand: str | None = None,
    tmpl_hand: str | None = None,
    expected_ext: list[bool] | None = None,
    live_ext: list[bool] | None = None,
) -> dict[str, float]:
    """Compute per-parameter similarity scores.

    Returns dict with keys:
        'finger_0' .. 'finger_4': per-finger similarity (0-1)
        'orientation': palm orientation similarity (0-1)
        'total': weighted total score (0-1)
        'grab': grab strength similarity (0-1)
    """
    from gesture_lab.config import get_scoring_config
    sc = get_scoring_config()
    fs = sc["finger_scoring"]

    scores: dict[str, float] = {}
    fw = finger_weights if len(finger_weights) == 5 else [1.0] * 5

    # Mirror if hands differ
    needs_mirror = (
        live_hand is not None and tmpl_hand is not None
        and tmpl_hand != "any" and live_hand != tmpl_hand
    )
    compare = _mirror_orientation(live_vec) if needs_mirror else live_vec

    # Per-finger scores (joint angles + tip distance + extension check)
    angle_norm = fs["angle_normalization"]
    td_norm = fs["tip_distance_normalization"]
    a_weight = fs["angle_weight"]
    td_weight = fs["tip_distance_weight"]

    finger_sims: list[float] = []
    for i in range(5):
        a_start = i * 4
        a_end = a_start + 4
        live_a = compare[a_start:a_end]
        tmpl_a = tmpl_vec[a_start:a_end]
        angle_diff = float(np.linalg.norm(live_a - tmpl_a))
        angle_sim = max(0.0, 1.0 - angle_diff / angle_norm)

        td_idx = N_JOINT_ANGLES + N_ABDUCTION + i
        td_diff = abs(compare[td_idx] - tmpl_vec[td_idx])
        td_sim = max(0.0, 1.0 - td_diff / td_norm)

        finger_sim = a_weight * angle_sim + td_weight * td_sim

        # Extension mismatch penalty: if finger has wrong extension state,
        # set its score to 0. This ensures wrong-finger poses are clearly rejected.
        if expected_ext and live_ext and len(expected_ext) > i and len(live_ext) > i:
            if expected_ext[i] != live_ext[i]:
                finger_sim = 0.0

        scores[f"finger_{i}"] = finger_sim
        finger_sims.append(finger_sim)

    # Orientation (already mirrored in compare if needed)
    orient_start = N_JOINT_ANGLES + N_ABDUCTION + N_TIP_DISTANCES
    orient_end = orient_start + N_PALM_ORIENT
    orient_sim = _orientation_similarity(
        compare[orient_start:orient_end],
        tmpl_vec[orient_start:orient_end],
    )
    scores["orientation"] = orient_sim

    # Grab strength
    grab_idx = orient_end
    if grab_idx < len(compare) and grab_idx < len(tmpl_vec):
        grab_diff = abs(compare[grab_idx] - tmpl_vec[grab_idx])
        scores["grab"] = max(0.0, 1.0 - grab_diff)
    else:
        scores["grab"] = 1.0

    # Weighted total
    active_weight = sum(fw)
    if active_weight > 0:
        geo_score = sum(fw[i] * finger_sims[i] for i in range(5)) / active_weight
    else:
        geo_score = 1.0

    total = (1.0 - orient_weight) * geo_score + orient_weight * orient_sim

    # Extension violation penalty on total score
    if expected_ext and live_ext:
        penalty = sc["extension_violation_penalty"]
        n_violations = sum(
            1 for i in range(min(5, len(expected_ext), len(live_ext)))
            if expected_ext[i] != live_ext[i] and fw[i] > 0
        )
        total -= n_violations * penalty

    scores["total"] = float(np.clip(total, 0.0, 1.0))

    return scores


def compute_mean_template(templates: list[GestureTemplate]) -> GestureTemplate | None:
    """Compute a mean template from multiple recordings, merging L/R hands.

    Left-hand recordings have their orientation mirrored before averaging,
    so the mean template is always in 'right-hand space'.
    """
    valid = [t for t in templates if t.pose_vector]
    if not valid:
        return None

    # Normalize all to right-hand space before averaging
    vecs: list[NDArray] = []
    for t in valid:
        v = np.array(t.pose_vector, dtype=np.float64)
        if t.hand_type == "left":
            v = _mirror_orientation(v)
        vecs.append(v)

    mat = np.stack(vecs)
    mean_vec = np.mean(mat, axis=0)
    var_vec = np.var(mat, axis=0) if len(valid) > 1 else np.zeros_like(mean_vec)

    base = valid[0]
    return GestureTemplate(
        name=base.name,
        description=base.description,
        clinical_source=base.clinical_source,
        gesture_type=base.gesture_type,
        hand_type="any",  # merged template works for both hands
        pose_number=base.pose_number,
        pose_vector=mean_vec.tolist(),
        pose_variance=var_vec.tolist(),
        finger_weights=base.finger_weights[:],
        expected_extensions=base.expected_extensions[:],
        threshold_good=base.threshold_good,
        threshold_partial=base.threshold_partial,
        scoring_criteria=base.scoring_criteria,
    )


def classify_static_result(similarity: float, template: GestureTemplate) -> str:
    """Return 'KORREKT', 'TEILWEISE', or 'FALSCH' based on thresholds."""
    if similarity >= template.threshold_good:
        return "KORREKT"
    elif similarity >= template.threshold_partial:
        return "TEILWEISE"
    else:
        return "FALSCH"


# ---------------------------------------------------------------------------
# Dynamic gesture similarity (DTW)
# ---------------------------------------------------------------------------

def dtw_distance(s1: NDArray, s2: NDArray) -> float:
    """Minimal DTW implementation with Sakoe-Chiba band.

    Parameters
    ----------
    s1, s2 : ndarray of shape (N, D) and (M, D)

    Returns
    -------
    distance : float
    """
    n, m = s1.shape[0], s2.shape[0]
    if n == 0 or m == 0:
        return float("inf")

    # Sakoe-Chiba band width: 20% of max length
    band = max(1, int(0.2 * max(n, m)))

    # Cost matrix
    cost = np.full((n + 1, m + 1), np.inf)
    cost[0, 0] = 0.0

    for i in range(1, n + 1):
        j_start = max(1, i - band)
        j_end = min(m, i + band)
        for j in range(j_start, j_end + 1):
            d = float(np.linalg.norm(s1[i - 1] - s2[j - 1]))
            cost[i, j] = d + min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])

    return float(cost[n, m])


def dynamic_similarity(
    live_series: NDArray[np.float64],
    template: GestureTemplate,
) -> float:
    """DTW-based similarity between live recording and dynamic template.

    Parameters
    ----------
    live_series : ndarray (N, 41) – resampled to template's sample rate
    template : GestureTemplate with dynamic_frames populated

    Returns
    -------
    similarity : float in [0, 1]
    """
    if not template.dynamic_frames or live_series.shape[0] < 2:
        return 0.0

    tmpl = np.array(template.dynamic_frames, dtype=np.float64)
    dist = dtw_distance(live_series, tmpl)

    # Normalize: use template's own DTW self-distance as reference
    # Since template vs itself = 0, use a heuristic based on dimensionality
    dim = tmpl.shape[1] if tmpl.ndim == 2 else 1
    norm_factor = math.sqrt(dim) * max(tmpl.shape[0], live_series.shape[0])

    return float(np.clip(1.0 / (1.0 + dist / norm_factor), 0.0, 1.0))
