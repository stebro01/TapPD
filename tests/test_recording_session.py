"""Tests for protocol-guided recording state in video.store.

Covers the cycle a clinician walks through — film, review, confirm or repeat —
and that a half-finished session survives being closed and reopened, which is
the whole point of persisting the step state.
"""

import json

import pytest

from video import store
from video.protocol import Protocol, ProtocolStep
from video.store import (
    STEP_CONFIRMED,
    STEP_PENDING,
    STEP_RECORDED,
    VideoSession,
)


@pytest.fixture(autouse=True)
def _isolated_sessions_dir(tmp_path, monkeypatch):
    """Never write into the real data/video_sessions/ during tests."""
    monkeypatch.setattr(store, "VIDEO_SESSIONS_DIR", tmp_path)


def _protocol():
    return Protocol(
        id="test_proto", name="Testprotokoll",
        steps=(
            ProtocolStep(id="rest", title="Ruhe", instruction="Ruhig sitzen.",
                         duration_s=30, paradigm="rest_tremor", hand="both"),
            ProtocolStep(id="turn", title="Kopfdrehung", duration_s=20),
            ProtocolStep(id="tap_r", title="Tapping rechts", duration_s=20,
                         paradigm="finger_tapping", hand="right"),
        ),
        source_path="/irgendwo/test_proto.yaml")


def _session():
    return VideoSession.create_recording(1, "P001", _protocol(), mirrored=True)


# ── setting up ──────────────────────────────────────────────────────

def test_protocol_becomes_pending_steps():
    s = _session()

    assert s.protocol_id == "test_proto"
    assert s.protocol_name == "Testprotokoll"
    assert [st.id for st in s.steps] == ["rest", "turn", "tap_r"]
    assert all(st.state == STEP_PENDING for st in s.steps)
    assert s.progress == (0, 3)


def test_steps_snapshot_the_protocol_not_reference_it():
    """Editing the protocol file later must not rewrite what a patient did."""
    protocol = _protocol()
    s = VideoSession.create_recording(1, "P001", protocol, mirrored=False)

    assert s.step("rest").instruction == "Ruhig sitzen."
    assert s.step("rest").duration_s == 30
    assert s.step("turn").is_documentation


def test_mirrored_must_be_stated_explicitly():
    """A recording is our own capture and replays under the webcam setting;
    the API refuses to guess it."""
    with pytest.raises(TypeError):
        VideoSession.create_recording(1, "P001", _protocol())


# ── the record / review / confirm cycle ─────────────────────────────

def test_take_paths_are_numbered_so_nothing_is_overwritten():
    s = _session()

    first = s.begin_take("rest")
    second = s.begin_take("rest")

    assert first != second
    assert "take01" in first.name and "take02" in second.name
    assert s.step("rest").takes == 2


def test_recording_then_confirming_produces_a_segment():
    s = _session()
    clip = s.begin_take("tap_r")

    s.mark_recorded("tap_r", clip)
    assert s.step("tap_r").state == STEP_RECORDED
    assert s.segments == []            # nothing analysable before review

    seg = s.confirm_step("tap_r")

    assert s.step("tap_r").state == STEP_CONFIRMED
    assert s.step("tap_r").segment_id == seg.id
    assert (seg.paradigm, seg.hand) == ("finger_tapping", "right")
    assert seg.clip_path == str(clip)
    assert seg.duration_s == 20


def test_confirming_without_a_take_is_refused():
    s = _session()

    with pytest.raises(ValueError):
        s.confirm_step("rest")


def test_retake_reopens_the_step_and_drops_its_segment():
    s = _session()
    s.mark_recorded("tap_r", s.begin_take("tap_r"))
    seg = s.confirm_step("tap_r")

    s.retake_step("tap_r")

    assert s.step("tap_r").state == STEP_PENDING
    assert s.step("tap_r").segment_id == ""
    assert seg.id not in [x.id for x in s.segments]
    # the take counter keeps climbing, so the next file cannot collide
    assert "take02" in s.begin_take("tap_r").name


def test_unknown_step_raises():
    s = _session()

    with pytest.raises(KeyError):
        s.mark_recorded("gibt_es_nicht", "x.mp4")


# ── resuming ────────────────────────────────────────────────────────

def test_next_open_step_walks_the_protocol():
    s = _session()
    assert s.next_open_step().id == "rest"

    s.mark_recorded("rest", s.begin_take("rest"))
    s.confirm_step("rest")

    assert s.next_open_step().id == "turn"
    assert s.progress == (1, 3)
    assert not s.is_complete


def test_complete_when_every_step_is_confirmed():
    s = _session()
    for st in list(s.steps):
        s.mark_recorded(st.id, s.begin_take(st.id))
        s.confirm_step(st.id)

    assert s.is_complete
    assert s.next_open_step() is None
    assert s.progress == (3, 3)


def test_half_finished_session_survives_a_round_trip():
    """The point of persisting step state: close the app mid-protocol, come
    back, and carry on where the patient left off."""
    s = _session()
    s.mark_recorded("rest", s.begin_take("rest"))
    s.confirm_step("rest")
    s.mark_recorded("turn", s.begin_take("turn"))     # filmed, not yet reviewed
    path = s.save()

    back = VideoSession.load(str(path))

    assert back.protocol_id == "test_proto"
    assert back.mirrored is True
    assert back.progress == (1, 3)
    assert back.step("rest").state == STEP_CONFIRMED
    assert back.step("turn").state == STEP_RECORDED
    assert back.step("turn").takes == 1
    assert back.step("tap_r").state == STEP_PENDING
    assert back.next_open_step().id == "turn"
    assert len(back.segments) == 1


# ── compatibility ───────────────────────────────────────────────────

def test_session_without_a_protocol_still_works():
    """An imported video has no steps — the recording fields stay empty."""
    s = VideoSession.create(1, "P002")
    s.set_video("/x/video.mp4", "video.mp4")
    back = VideoSession.load(str(s.save()))

    assert back.steps == []
    assert back.protocol_id == ""
    assert back.progress == (0, 0)
    assert back.next_open_step() is None
    assert not back.is_complete       # no steps is not "done"


def test_older_session_json_without_step_fields_loads(tmp_path):
    old = tmp_path / "old.json"
    old.write_text(json.dumps({
        "patient_id": 7, "patient_code": "P007",
        "video_path": "/x/v.mp4", "video_name": "v.mp4",
        "created_at": "2026-01-01T00:00:00", "db_session_id": None,
        "segments": [],
    }), encoding="utf-8")

    vs = VideoSession.load(str(old))

    assert vs.patient_code == "P007"
    assert vs.steps == [] and vs.protocol_id == ""
    assert vs.mirrored is False


def test_unknown_step_keys_are_ignored(tmp_path):
    """A session written by a newer version must still open here."""
    newer = tmp_path / "newer.json"
    newer.write_text(json.dumps({
        "patient_id": 7, "patient_code": "P007", "segments": [],
        "protocol_id": "p", "protocol_name": "P",
        "steps": [{"id": "a", "title": "A", "state": "pending",
                   "irgendwas_neues": 42}],
    }), encoding="utf-8")

    vs = VideoSession.load(str(newer))

    assert [s.id for s in vs.steps] == ["a"]
