"""12-pose clinical battery definitions loaded from gesture_config.yaml."""

from __future__ import annotations

from gesture_lab.config import get_all_poses, get_scoring_config
from gesture_lab.models import GestureTemplate


def get_battery_definitions() -> list[GestureTemplate]:
    """Return metadata for all poses defined in gesture_config.yaml.

    These are *definitions only* – they contain expected extension patterns,
    finger weights, and clinical metadata but no recorded pose_vector.
    A pose_vector is populated when the reference pose is recorded.
    """
    poses = get_all_poses()
    scoring = get_scoring_config()
    templates: list[GestureTemplate] = []

    for pose_num, cfg in poses.items():
        templates.append(GestureTemplate(
            pose_number=int(pose_num),
            name=cfg["name"],
            description=cfg["description"],
            clinical_source=cfg.get("clinical_source", ""),
            gesture_type=cfg.get("gesture_type", "static"),
            expected_extensions=cfg.get("expected_extensions", [True] * 5),
            finger_weights=cfg.get("finger_weights", [1.0] * 5),
            scoring_criteria=cfg.get("scoring_criteria", ""),
            dynamic_duration_s=cfg.get("dynamic_duration_s", 0.0),
            threshold_good=scoring["thresholds"]["good"],
            threshold_partial=scoring["thresholds"]["partial"],
        ))

    return sorted(templates, key=lambda t: t.pose_number)


# Quick lookup
BATTERY_BY_NUMBER: dict[int, GestureTemplate] = {
    t.pose_number: t for t in get_battery_definitions()
}
