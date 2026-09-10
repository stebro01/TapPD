"""Detail dialog for the ocular paradigms: the raw JSON of a saccade run carries
the targets/hits, and the dialog draws gaze + targets + latencies from it;
a fixation run gets gaze + blink plots; an older saccade file without the
event block still draws the gaze trace."""

import json

from capture.base_capture import FacePose, TrackingFrame
from capture.mock_capture import SimulationSource
from paradigms.saccade_logic import Phase
from paradigms.saccade_test import SaccadeTest
from storage.database import Measurement, Patient

FS = 30.0


def _face(t: float, gx: float, gy: float) -> FacePose:
    ipd, cx, cy = 50.0, 340.0, 200.0
    return FacePose(timestamp_us=int(t * 1e6),
                    iris_left=(cx - ipd / 2 + gx * ipd, cy + gy * ipd),
                    iris_right=(cx + ipd / 2 + gx * ipd, cy + gy * ipd),
                    corners_left=((cx - 40, cy), (cx - 10, cy)),
                    corners_right=((cx + 10, cy), (cx + 40, cy)),
                    ear_left=0.3, ear_right=0.3, nose=(cx, cy + 30))


def _run_saccade(duration: float = 12.0) -> SaccadeTest:
    test = SaccadeTest(capture=SimulationSource(mode="ocular_fixation"), duration=duration)
    task = test.task
    look = task.calib_visits[0]
    for i in range(int((duration + 25) * FS)):
        t = i / FS
        if task.phase is Phase.CALIBRATING:
            look = task.calib_point or look
        elif task.phase is Phase.TESTING and task.current_target:
            look = task.current_target
        elif task.phase in (Phase.DONE, Phase.FAILED):
            break
        nx, ny = task.points[look]
        test._on_tracking(TrackingFrame(timestamp_us=int(t * 1e6),
                                        face=_face(t, (nx - 0.5) * 0.2, (ny - 0.5) * 0.12)))
    return test


def _dialog(qapp, path, test_type):
    from ui.detail_dialog import DetailDialog
    m = Measurement(patient_id=1, test_type=test_type, hand="both", duration_s=12.0,
                    raw_data_path=str(path), source_kind="webcam",
                    features_json=json.dumps({"n_face_frames": 100.0}))
    return DetailDialog(Patient(id=1, patient_code="T1"), m)


def test_saccade_raw_carries_targets_and_dialog_plots_them(qapp, tmp_path, monkeypatch):
    import ui.results_screen as rs
    monkeypatch.setattr(rs, "SAMPLES_DIR", tmp_path)
    test = _run_saccade()
    feats = test.compute_features()
    assert "_fail_reason" not in feats, feats
    path = rs.save_raw_data(test, "T1", feats)
    data = json.loads(path.read_text(encoding="utf-8"))
    sac = data["saccade"]
    assert sac["layout"] == test.task.layout and sac["calib_visits"] == test.task.calib_visits
    assert set(sac["references"]) == set(test.task.calib_order)
    assert len(sac["hits"]) == len(test.task.hits) >= 3
    assert sac["test_started_s"] is not None and sac["phase"] == "DONE"
    assert {"target", "shown_at_s", "acquired_at_s", "first_move_correct"} <= set(sac["hits"][0])

    dlg = _dialog(qapp, path, "saccade_test")
    axes = dlg._figure.axes
    assert len(axes) == 2
    assert axes[0].get_title().startswith("Sakkaden — Blick und Ziele")
    assert "Latenz" in axes[1].get_title()
    assert len(axes[1].patches) == len(sac["hits"])            # one bar per target
    assert any(t.get_text() == "Eichung" for t in axes[0].texts)
    assert not any("Keine Rohdaten" in t.get_text() for a in axes for t in a.texts)


def test_saccade_raw_without_events_still_plots_gaze(qapp, tmp_path):
    faces = [_face(i / FS, 0.05 * (i % 2), 0.0) for i in range(60)]
    from dataclasses import asdict
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"test_type": "saccade_test", "sample_rate": 30.0,
                                "face_frames": [asdict(f) for f in faces]}), encoding="utf-8")
    dlg = _dialog(qapp, path, "saccade_test")
    axes = dlg._figure.axes
    assert len(axes) == 2 and "ältere Aufnahme" in axes[0].get_title()
    assert axes[1].get_title() == "Blinzeln"


def test_fixation_dialog_plots_gaze_and_blinks(qapp, tmp_path):
    faces = [_face(i / FS, 0.01, 0.0) for i in range(60)]
    from dataclasses import asdict
    path = tmp_path / "fix.json"
    path.write_text(json.dumps({"test_type": "ocular_fixation", "sample_rate": 30.0,
                                "face_frames": [asdict(f) for f in faces]}), encoding="utf-8")
    dlg = _dialog(qapp, path, "ocular_fixation")
    titles = [a.get_title() for a in dlg._figure.axes]
    assert titles == ["Fixation", "Blinzeln"]
