"""Tests for video.protocol — recording protocols and their validation.

Pure data + validation, so everything here runs without a camera, MediaPipe or
Qt. The shipped protocol is checked too: a broken file in video/protocols/
should fail the suite, not the clinician mid-session.
"""

import pytest

from video import protocol as P


def _steps(*raw):
    return {"id": "t", "name": "Test", "steps": list(raw)}


def _valid_step(**over):
    step = {"id": "tap", "title": "Tapping", "duration_s": 20,
            "paradigm": "finger_tapping", "hand": "right"}
    step.update(over)
    return step


# ── building ────────────────────────────────────────────────────────

def test_defaults_fill_missing_step_fields():
    data = {"id": "p", "name": "P",
            "defaults": {"duration_s": 42, "countdown_s": 1, "hand": "left"},
            "steps": [{"id": "a", "title": "A", "paradigm": "finger_tapping"}]}
    step = P.protocol_from_dict(data).steps[0]

    assert step.duration_s == 42
    assert step.countdown_s == 1
    assert step.hand == "left"


def test_step_overrides_beat_defaults():
    data = {"id": "p", "name": "P",
            "defaults": {"duration_s": 42},
            "steps": [_valid_step(duration_s=7)]}

    assert P.protocol_from_dict(data).steps[0].duration_s == 7


def test_missing_ids_and_titles_get_fallbacks():
    step = P.protocol_from_dict(_steps({"paradigm": "finger_tapping"})).steps[0]

    assert step.id == "step_1"
    assert step.title == "step_1"


def test_empty_paradigm_is_a_documentation_step():
    step = P.protocol_from_dict(_steps({"id": "turn", "title": "Kopfdrehung"})).steps[0]

    assert step.is_documentation
    assert P.validate(P.protocol_from_dict(_steps(
        {"id": "turn", "title": "Kopfdrehung"}))) == []


def test_total_duration_sums_steps():
    p = P.protocol_from_dict(_steps(_valid_step(id="a", duration_s=10),
                                    _valid_step(id="b", duration_s=5)))

    assert p.total_duration_s == 15


def test_malformed_input_raises():
    with pytest.raises(P.ProtocolError):
        P.protocol_from_dict({"steps": "keine Liste"})
    with pytest.raises(P.ProtocolError):
        P.protocol_from_dict(_steps("kein Mapping"))
    with pytest.raises(P.ProtocolError):
        P.protocol_from_dict(_steps(_valid_step(duration_s="zwanzig")))


# ── validation ──────────────────────────────────────────────────────

def _errors(data):
    return [i for i in P.validate(P.protocol_from_dict(data)) if i.is_error]


def test_valid_protocol_has_no_errors():
    assert _errors(_steps(_valid_step())) == []


def test_unknown_paradigm_is_an_error():
    errors = _errors(_steps(_valid_step(paradigm="gibt_es_nicht")))

    assert len(errors) == 1
    assert "gibt_es_nicht" in errors[0].message


def test_duplicate_step_ids_are_an_error():
    errors = _errors(_steps(_valid_step(id="x"), _valid_step(id="x")))

    assert any("Doppelte" in e.message for e in errors)


def test_bad_duration_and_hand_are_errors():
    assert any("Dauer" in e.message for e in _errors(_steps(_valid_step(duration_s=0))))
    assert any("Hand" in e.message for e in _errors(_steps(_valid_step(hand="oben"))))


def test_empty_protocol_is_an_error():
    assert _errors({"id": "p", "name": "P", "steps": []})


def test_face_dependent_paradigm_is_a_note_not_an_error():
    """Tremor needs absolute position, which a webcam only claims while the eye
    reference runs. That is a condition to point out, not a broken protocol —
    rejecting it would lock the tremor paradigms out of every camera protocol.
    """
    issues = P.validate(P.protocol_from_dict(
        _steps(_valid_step(id="rest", paradigm="rest_tremor", hand="both"))))

    assert [i for i in issues if i.is_error] == []
    notes = [i for i in issues if not i.is_error]
    assert len(notes) == 1
    assert "Gesichts-Tracking" in notes[0].message


def test_unilateral_paradigm_with_both_hands_is_a_note():
    issues = P.validate(P.protocol_from_dict(_steps(_valid_step(hand="both"))))

    assert [i for i in issues if i.is_error] == []
    assert any("einseitig" in i.message for i in issues)


# ── loading ─────────────────────────────────────────────────────────

def test_load_rejects_a_broken_file(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: bad\nname: Bad\nsteps:\n  - id: a\n    paradigm: nope\n",
                   encoding="utf-8")

    with pytest.raises(P.ProtocolError) as exc:
        P.load_protocol_file(bad)
    assert "nope" in str(exc.value)


def test_load_reports_a_missing_file():
    with pytest.raises(P.ProtocolError):
        P.load_protocol_file("gibt/es/nicht.yaml")


def test_notes_survive_loading(tmp_path):
    ok = tmp_path / "ok.yaml"
    ok.write_text("id: ok\nname: OK\nsteps:\n"
                  "  - id: rest\n    title: Ruhe\n    paradigm: rest_tremor\n"
                  "    hand: both\n", encoding="utf-8")

    loaded = P.load_protocol_file(ok)

    assert loaded.notes and not any(n.is_error for n in loaded.notes)


def test_shipped_protocols_are_valid():
    """Every file under video/protocols/ must load — a protocol that only
    breaks when a clinician selects it is exactly the failure mode to avoid."""
    for path in sorted(P.PROTOCOLS_DIR.glob("*.yaml")):
        P.load_protocol_file(path)   # raises on error

    assert P.list_protocols(), "kein einziges Protokoll gefunden"


def test_list_skips_broken_files_without_dying(tmp_path, monkeypatch):
    (tmp_path / "good.yaml").write_text(
        "id: good\nname: Good\nsteps:\n  - id: a\n    title: A\n", encoding="utf-8")
    (tmp_path / "broken.yaml").write_text(
        "id: broken\nname: Broken\nsteps:\n  - id: a\n    paradigm: nope\n",
        encoding="utf-8")
    monkeypatch.setattr(P, "PROTOCOLS_DIR", tmp_path)

    assert [p.id for p in P.list_protocols()] == ["good"]


# ── single paradigm == protocol of length 1 ─────────────────────────

def test_single_paradigm_builds_a_one_step_protocol():
    p = P.protocol_for_paradigm("finger_tapping", hand="left", duration_s=15)

    assert p.is_ad_hoc
    assert len(p.steps) == 1
    step = p.steps[0]
    assert (step.paradigm, step.hand, step.duration_s) == ("finger_tapping", "left", 15)
    assert not any(n.is_error for n in p.notes)


def test_single_paradigm_rejects_an_unknown_key():
    with pytest.raises(Exception):
        P.protocol_for_paradigm("gibt_es_nicht")


def test_single_paradigm_uses_the_default_duration_when_none_given():
    assert P.protocol_for_paradigm("finger_tapping").steps[0].duration_s \
        == P.DEFAULT_DURATION_S
