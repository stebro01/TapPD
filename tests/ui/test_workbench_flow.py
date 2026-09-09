"""The clinician's path through the patient workbench, on the real window."""

import pytest

from video.protocol import load_protocol, protocol_for_paradigm
from video.store import STEP_CONFIRMED, STEP_PENDING, STEP_RECORDED


def _pane(wb) -> str:
    return type(wb._work.currentWidget()).__name__


def _rows(wb):
    """(text, result) of every visible row, depth-first."""
    out = []

    def walk(item, depth):
        out.append((depth, item.text(0), item.text(1)))
        for i in range(item.childCount()):
            walk(item.child(i), depth + 1)

    for i in range(wb.tree.topLevelItemCount()):
        walk(wb.tree.topLevelItem(i), 0)
    return out


def _fake_take(app, video, step_id: str):
    """Pretend a take was filmed: a file exists and the step is 'recorded'."""
    take = video.begin_take(step_id)
    take.write_bytes(b"\x00" * 1024)
    video.mark_recorded(step_id, str(take))
    return take


# ── entering ────────────────────────────────────────────────────────

def test_clicking_a_patient_opens_the_workbench(app):
    app.win.select_patient(app.patient)
    app.pump()

    wb = app.win.patient_detail
    assert app.win.stack.currentWidget() is wb
    assert "Test" in wb._name.text()
    assert wb._count.text().startswith("0 Sitzungen")
    assert _pane(wb) == "QWidget"                     # empty-state prompt
    assert "Neue Sitzung" in wb._empty_title.text()


def test_new_session_becomes_a_selected_group_with_the_add_prompt(workbench, app):
    app.win.start_new_session()
    app.pump()

    assert len(workbench._sessions) == 1
    assert workbench._current_key() == ("session", workbench._sessions[0].id)
    assert _pane(workbench) == "QWidget"
    assert "aufgezeichnet" in workbench._empty_title.text()
    assert workbench._count.text().startswith("1 Sitzung ·")


# ── protocol → steps ────────────────────────────────────────────────

@pytest.fixture
def protocol_session(workbench, app):
    app.win.start_new_session()
    app.pump()
    s = workbench._sessions[0]
    v = workbench._video_for(s, create=True)
    v.add_steps(load_protocol("updrs_hand_basis"))
    v.save()
    workbench.refresh()
    app.pump()
    return workbench, s, v


def test_adding_a_protocol_lists_its_steps_and_opens_the_recording_pane(protocol_session, app):
    wb, s, v = protocol_session
    wb._select(("step", s.id, v.steps[0].id))
    app.pump()

    rows = _rows(wb)
    assert rows[0][1].startswith("Sitzung 1") and "Protokoll" in rows[0][1]
    assert rows[0][2] == "0/4"
    assert [r[1] for r in rows[1:5]] == [
        "○  1. Ruheaufnahme  (both)", "○  2. Kopfdrehung",
        "○  3. Finger-Tapping rechts  (right)", "○  4. Finger-Tapping links  (left)"]
    assert _pane(wb) == "RecordingPane"
    assert wb._rec._step.id == "rest"
    assert "Ruheaufnahme" in wb._rec._step_title.text()


def test_confirming_a_take_advances_and_updates_the_tree(protocol_session, app):
    wb, s, v = protocol_session
    take = _fake_take(app, v, "rest")
    wb.refresh(); wb._select(("step", s.id, "rest")); app.pump()
    assert wb._rec._phase == "review"                 # a filmed step opens in review

    wb._rec._auto_cb.setChecked(False)                # no sidecar in this test
    wb._rec._on_keep()
    app.pump(0.1)                                     # deferred refresh

    assert v.step("rest").state == STEP_CONFIRMED
    assert v.progress == (1, 4)
    assert wb._rec._step.id == "head_turn"            # moved on to the next open step
    rows = _rows(wb)
    assert rows[0][2] == "1/4"
    assert rows[1][1].startswith("✔  1. Ruheaufnahme")
    assert v.segments[0].recorded and v.segments[0].source_path == str(take)


def test_retake_reopens_a_confirmed_step(protocol_session, app):
    wb, s, v = protocol_session
    _fake_take(app, v, "rest")
    v.confirm_step("rest"); v.save()
    wb.refresh(); app.pump()

    wb._retake(v, v.step("rest"))
    app.pump(0.1)

    assert v.step("rest").state == STEP_PENDING
    assert v.segments == []
    assert wb._current_key() == ("step", s.id, "rest")
    assert wb._rec._phase == "idle"


def test_relabel_changes_step_and_segment_and_drops_results(protocol_session, app, monkeypatch):
    wb, s, v = protocol_session
    _fake_take(app, v, "tap_right")
    seg = v.confirm_step("tap_right")
    seg.results["finger_tapping"] = {"features": {"mpi": 0.5}}
    v.save(); wb.refresh(); app.pump()

    # answer the dialog without showing it
    from ui import patient_workbench as pw

    class _Dlg:
        def __init__(self, *a, **k):
            from PyQt6.QtWidgets import QComboBox
            self.para = QComboBox(); self.para.addItem("x", "hand_open_close")
            self.hand = QComboBox(); self.hand.addItem("x", "left")

        def exec(self):
            return 1
    monkeypatch.setattr(pw, "_LabelDialog", _Dlg)
    monkeypatch.setattr(wb, "_reanalyse", lambda *a, **k: None)   # no sidecar here

    wb._relabel(v, v.step("tap_right"))
    app.pump(0.1)

    st = v.step("tap_right")
    assert (st.paradigm, st.hand) == ("hand_open_close", "left")
    assert (seg.paradigm, seg.hand) == ("hand_open_close", "left")
    assert seg.results == {}
    assert any("Finger-Tapping rechts  (left)" in r[1] for r in _rows(wb))


def test_single_paradigm_appends_a_step(protocol_session, app):
    wb, s, v = protocol_session
    added = v.add_steps(protocol_for_paradigm("finger_tapping", hand="left", duration_s=10))
    v.save(); wb.refresh(); app.pump()

    assert len(v.steps) == 5 and added[0].id == "finger_tapping"
    assert any("5. Finger Tapping  (left)" in r[1] for r in _rows(wb))


def test_removing_a_step(protocol_session, app, yes_to_everything):
    wb, s, v = protocol_session
    wb._remove_step(v, v.step("head_turn"))
    app.pump(0.1)

    assert [st.id for st in v.steps] == ["rest", "tap_right", "tap_left"]
    assert not any("Kopfdrehung" in r[1] for r in _rows(wb))


# ── context actions ─────────────────────────────────────────────────

def test_actions_follow_the_step_state(protocol_session, app):
    wb, s, v = protocol_session
    labels = lambda key: [a[0] for a in wb._actions_for(key)]

    assert labels(("step", s.id, "rest")) == ["● Aufnehmen", "Schritt entfernen…"]

    _fake_take(app, v, "rest"); v.confirm_step("rest"); v.save(); wb.refresh(); app.pump()
    assert labels(("step", s.id, "rest")) == [
        "↻ Erneut aufnehmen", "⟳ Neu auswerten", "Paradigma / Seite ändern…", "Schritt entfernen…"]

    _fake_take(app, v, "head_turn"); v.confirm_step("head_turn"); v.save(); wb.refresh(); app.pump()
    # a documentation step is never analysed → no analysis actions
    assert labels(("step", s.id, "head_turn")) == ["↻ Erneut aufnehmen", "Schritt entfernen…"]

    assert labels(("session", s.id)) == [
        "● Aufnahme fortsetzen", "＋ Protokoll aufnehmen…", "＋ Einzelnes Paradigma…",
        "＋ Video importieren…", "Sitzung löschen…"]


def test_single_interactive_paradigm_runs_live_not_as_a_step(workbench, app, monkeypatch):
    """One "Paradigma" entry: Hanoi is a screen task, so choosing it starts the
    live test instead of adding a video step."""
    from ui import patient_workbench as pw
    app.win.start_new_session(); app.pump()
    s = workbench._sessions[0]

    class _Chooser:
        def __init__(self, *a, **k): pass
        def exec(self): return 1
        is_interactive = True
        def single_choice(self): return ("tower_of_hanoi", "right", 20.0)
    monkeypatch.setattr("ui.protocol_chooser.ProtocolChooser", _Chooser)
    started = []
    monkeypatch.setattr(app.win, "start_test", lambda k, h, d: started.append((k, h)))

    workbench._add_single()

    assert started == [("tower_of_hanoi", "right")]
    assert workbench._videos.get(s.id) is None or not workbench._videos[s.id].steps


# ── leaving and coming back ─────────────────────────────────────────

def test_reopening_resumes_at_the_next_open_step(protocol_session, app):
    wb, s, v = protocol_session
    _fake_take(app, v, "rest"); v.confirm_step("rest"); v.save()

    app.win.show_patient_screen()
    app.pump()
    app.win.select_patient(app.patient)
    app.pump()

    wb = app.win.patient_detail
    assert wb._current_key() == ("step", s.id, "head_turn")
    assert _pane(wb) == "RecordingPane"


def test_two_sessions_bind_their_own_video(workbench, app):
    app.win.start_new_session(); app.pump()
    app.win.start_new_session(); app.pump()
    s_new, s_old = workbench._sessions[0], workbench._sessions[1]
    v_old = workbench._video_for(s_old, create=True)
    v_old.add_steps(protocol_for_paradigm("finger_tapping")); v_old.save()
    v_new = workbench._video_for(s_new, create=True)
    v_new.add_steps(protocol_for_paradigm("rest_tremor", hand="both")); v_new.save()
    workbench.refresh(); app.pump()

    workbench._select(("step", s_old.id, "finger_tapping")); app.pump()
    assert workbench._rec.session is v_old
    workbench._select(("step", s_new.id, "rest_tremor")); app.pump()
    assert workbench._rec.session is v_new


def test_deleting_a_session_removes_it_and_keeps_the_screen_usable(protocol_session, app,
                                                                   yes_to_everything):
    wb, s, v = protocol_session
    wb._delete_session(s)
    app.pump(0.1)

    assert wb._sessions == []
    assert wb._count.text().startswith("0 Sitzungen")
    assert _pane(wb) == "QWidget"


def test_refresh_during_selection_does_not_crash(protocol_session, app):
    """The re-entrancy case: a pane emitting contentChanged while an item is
    being selected used to rebuild the tree under that item."""
    wb, s, v = protocol_session
    for _ in range(3):
        wb._select(("step", s.id, "rest"))
        wb._refresh()                      # deferred, must not delete the item
        wb._select(("step", s.id, "tap_left"))
        app.pump(0.05)
    assert wb._current_key() == ("step", s.id, "tap_left")


def test_analysed_step_offers_details_from_the_menu_and_the_pane(protocol_session, app,
                                                                 monkeypatch):
    """An analysed step is a measurement in the record: "Details…" leads to
    the same detail dialog as the measurement itself."""
    from storage.database import Measurement, get_db, save_measurement
    wb, s, v = protocol_session
    _fake_take(app, v, "tap_right")
    seg = v.confirm_step("tap_right")
    conn = get_db()
    m = Measurement(patient_id=app.patient.id, session_id=s.id, test_type="finger_tapping",
                    hand="right", duration_s=20.0)
    m.features = {"mpi": 0.6}
    m = save_measurement(conn, m)
    conn.close()
    seg.results["finger_tapping"] = {"features": {"mpi": 0.6}, "measurement_id": m.id,
                                     "recorded_at": "2026-09-09T10:00:00"}
    v.save(); wb.refresh(); app.pump()

    labels = [a[0] for a in wb._actions_for(("step", s.id, "tap_right"))]
    assert labels[0] == "Details…"
    built = []
    monkeypatch.setattr(wb, "_show_measurement", lambda mm: built.append(mm.id))
    wb._select(("step", s.id, "tap_right")); app.pump()
    assert wb._rec._details_btn.isVisibleTo(wb._rec)
    wb._rec._details_btn.click()
    assert built == [m.id]
    wb._actions_for(("step", s.id, "tap_right"))[0][1]()
    assert built == [m.id, m.id]
