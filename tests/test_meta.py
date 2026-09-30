"""Provenance of a take (video/meta.py): what is written where, how it is
described, and which inconsistencies the check catches."""

import json
import os

import pytest

from video import store
from video.protocol import protocol_for_paradigm
from video.store import VideoSession
from video import meta as vm


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VIDEO_SESSIONS_DIR", tmp_path)
    return tmp_path


class _Device:
    camera_index = 1
    camera_name = "OBSBOT Tiny 2"
    _face_on = True
    sidecar_info = {"mediapipe": "1.0.1", "opencv": "4.12.0"}
    last_recorded = {"path": "x", "w": 640, "h": 480, "fps": 30.0, "frames": 600, "codec": "avc1"}


def _recorded_session(tmp_path):
    v = VideoSession.create_recording(1, "P001", protocol_for_paradigm("finger_tapping"),
                                      mirrored=True)
    take = v.begin_take("finger_tapping")
    take.write_bytes(b"x" * 100)
    step = v.mark_recorded("finger_tapping", take)
    m = vm.capture_meta(_Device(), take=step.takes, take_file=str(take))
    step.meta = vm.note_recorded(m, _Device.last_recorded)
    return v, step, take


def test_capture_meta_names_camera_settings_and_the_take(tmp_path):
    v, step, take = _recorded_session(tmp_path)
    m = step.meta
    assert m["kind"] == "recording" and m["camera"] == {"index": 1, "name": "OBSBOT Tiny 2"}
    assert m["take"] == 1 and m["take_file"] == take.name
    assert m["video"] == {"width": 640, "height": 480, "fps": 30.0, "frames": 600, "codec": "avc1"}
    assert isinstance(m["mirror"], bool) and isinstance(m["swap_handedness"], bool)
    assert m["face_tracking"] is True and m["sidecar"]["mediapipe"] == "1.0.1"
    assert m["recorded_at"][:4].isdigit()


def test_confirm_carries_the_meta_onto_the_segment_and_retake_drops_it(tmp_path):
    v, step, take = _recorded_session(tmp_path)
    seg = v.confirm_step("finger_tapping")
    assert seg.meta["camera"]["name"] == "OBSBOT Tiny 2"

    back = VideoSession.load(str(v.save()))
    assert back.segments[0].meta == seg.meta and back.steps[0].meta == step.meta

    v.retake_step("finger_tapping")
    assert v.steps[0].meta == {} and v.segments == []


def test_old_session_files_without_meta_still_load(tmp_path):
    v, step, take = _recorded_session(tmp_path)
    v.confirm_step("finger_tapping")
    path = v.save()
    d = json.load(open(path, encoding="utf-8"))
    for s in d["segments"] + d["steps"]:
        s.pop("meta", None)
    d["segments"][0]["from_the_future"] = 1
    json.dump(d, open(path, "w", encoding="utf-8"))
    back = VideoSession.load(str(path))
    assert back.segments[0].meta == {} and back.steps[0].meta == {}


def test_import_and_archive_meta():
    v = VideoSession.create(1, "P001")
    v.set_video("/vid/clip.mp4", "phone_left.mp4")
    v.mirrored = True
    seg = v.add_segment("Tapping", 2.0, 9.5, paradigm="finger_tapping", hand="left")
    seg.meta = vm.import_meta(v, seg)
    assert seg.meta["kind"] == "import" and seg.meta["original_name"] == "phone_left.mp4"
    assert seg.meta["mirror"] is True and seg.meta["cut"] == {"start_s": 2.0, "end_s": 9.5}

    class Clip:
        width, height, fps = 608, 1080, 30.0
        extra = {"eyeref": True}
    seg.clip_path = __file__            # any existing file, for the size
    seg.deidentified = True
    arch = vm.note_archive(seg, Clip(), "blur")
    assert arch["deface"] == "blur" and arch["width"] == 608 and arch["size_bytes"] > 0
    assert arch["eyeref"] is True and arch["crf"] >= 0


def test_describe_segment_reads_like_a_record(tmp_path):
    v, step, take = _recorded_session(tmp_path)
    seg = v.confirm_step("finger_tapping")
    seg.results["finger_tapping"] = {"features": {"mpi": 0.7}, "recorded_at": "2026-09-09T10:00:00",
                                     "analysed_on": "raw", "measurement_id": 7,
                                     "raw_path": "", "eye_ref_coverage": 0.95}
    rows = dict(vm.describe_segment(v, seg, step))
    assert rows["Art"].startswith("eigene Aufnahme")
    assert rows["Kamera"] == "OBSBOT Tiny 2"
    assert rows["Video"].startswith("640×480  ·  30 fps  ·  600 Frames")
    assert rows["Take"].startswith("Nr. 1")
    assert rows["Roh-Take"].startswith(take.name)
    assert rows["Tracking-Spur"] == "fehlt"
    assert "Messung #7" in rows["Auswertung finger_tapping (rechts)"]
    assert "auf: Roh-Take" in rows["Auswertung finger_tapping (rechts)"]
    assert "Augenreferenz 95 %" in rows["Auswertung finger_tapping (rechts)"]


def test_issues_flag_missing_files_track_export_and_label_mismatch(tmp_path):
    v, step, take = _recorded_session(tmp_path)
    seg = v.confirm_step("finger_tapping")
    assert vm.segment_issues(v, seg, step) == [
        "Take ist nicht archiviert (nur der Roh-Take liegt vor)."]

    seg.results["finger_tapping"] = {"features": {}, "recorded_at": "", "raw_path": "/nope.json"}
    seg.hand = "left"
    seg.clip_path = str(tmp_path / "gone.mp4")
    issues = vm.segment_issues(v, seg, step, known_measurements={7})
    assert "Clip-Datei fehlt auf der Platte." in issues
    assert "Seite weicht ab: Schritt right, Segment left." in issues
    assert "Keine Tracking-Spur zur Auswertung — das Overlay würde neu rechnen." in issues
    assert "Auswertung finger_tapping ohne Kennwerte." in issues
    assert "Auswertung finger_tapping ist nicht in der Akte." in issues
    assert "Rohdaten-Datei der Auswertung finger_tapping fehlt." in issues

    seg.results["finger_tapping"]["measurement_id"] = 99
    assert "Messung #99 existiert nicht mehr in der Akte." in \
        vm.segment_issues(v, seg, step, known_measurements={7})


def test_legacy_segment_without_meta_is_called_out(tmp_path):
    v = VideoSession.create(1, "P001")
    seg = v.add_segment("Alt", 0, 5, paradigm="finger_tapping")
    assert vm.segment_issues(v, seg)[0].startswith("Keine Aufnahme-Metadaten")
    assert vm.describe_segment(v, seg)[0] == ("Aufnahme", "keine Metadaten (älterer Stand)")


def test_tremor_on_a_defaced_clip_is_flagged(tmp_path):
    v, step, take = _recorded_session(tmp_path)
    seg = v.confirm_step("finger_tapping")
    seg.paradigm = step.paradigm = "rest_tremor"
    seg.deidentified = True
    seg.results["rest_tremor"] = {"features": {"mpi": 0.5}, "analysed_on": "clip",
                                  "measurement_id": 1}
    seg.track_path = __file__
    assert any("Augenreferenz fehlt" in i for i in vm.segment_issues(v, seg, step))


def test_measurement_provenance_round_trip_and_description(tmp_path):
    from storage.database import Measurement
    v, step, take = _recorded_session(tmp_path)
    seg = v.confirm_step("finger_tapping")
    seg.track_path = ""
    result = {"features": {"mpi": 0.7}, "recorded_at": "2026-09-09T10:00:00",
              "analysed_on": "raw", "eye_ref_coverage": 0.5, "analysis": {"mirror": True}}
    prov = vm.build_provenance(v, seg, result)
    assert prov["segment_id"] == seg.id and prov["capture"]["camera"]["name"] == "OBSBOT Tiny 2"
    assert prov["analysed_on"] == "raw" and prov["analysis"] == {"mirror": True}

    m = Measurement(test_type="finger_tapping", hand="right", source_kind="video",
                    raw_data_path=str(take), provenance=prov)
    rows = dict(vm.describe_measurement(m))
    assert rows["Kamera"] == "OBSBOT Tiny 2" and rows["Ausgewertet"].endswith("auf: Roh-Take")
    assert rows["Augenreferenz"] == "50 % der Frames"
    issues = vm.measurement_issues(m)
    assert "Nur der Video-Clip ist hinterlegt" in issues[0]

    legacy = Measurement(test_type="finger_tapping", hand="left", source_kind="video",
                         raw_data_path="")
    assert vm.describe_measurement(legacy)[0][0] == "Herkunft"
    assert vm.measurement_issues(legacy) == ["Video-Messung ohne Herkunftsdaten (älterer Stand)."]
