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
    assert rows[0][1].startswith("Sitzung 1") and "Protokoll" in rows[0][2]
    assert rows[0][2] == "Protokoll 0/4"
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
    assert rows[0][2] == "Protokoll 1/4"
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

    assert labels(("step", s.id, "rest")) == ["● Aufnehmen", "📝 Notiz…", "Schritt entfernen…"]

    _fake_take(app, v, "rest"); v.confirm_step("rest"); v.save(); wb.refresh(); app.pump()
    assert labels(("step", s.id, "rest")) == [
        "↻ Erneut aufnehmen", "⟳ Neu auswerten", "Paradigma / Seite ändern…", "📝 Notiz…",
        "Schritt entfernen…"]

    _fake_take(app, v, "head_turn"); v.confirm_step("head_turn"); v.save(); wb.refresh(); app.pump()
    # a documentation step is never analysed → no analysis actions
    assert labels(("step", s.id, "head_turn")) == ["↻ Erneut aufnehmen", "📝 Notiz…",
                                                    "Schritt entfernen…"]

    assert labels(("session", s.id)) == [
        "● Aufnahme fortsetzen", "＋ Protokoll aufnehmen…", "＋ Einzelnes Paradigma…",
        "＋ Video importieren…", "📝 Notiz…", "Sitzung löschen…"]


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


def test_retake_asks_and_removes_the_measurement_of_the_old_take(protocol_session, app,
                                                                 monkeypatch):
    """A retake used to strand the exported measurement in the record without
    a video behind it; now it asks and removes the measurement."""
    from PyQt6.QtWidgets import QMessageBox
    from storage.database import Measurement, get_db, get_measurements, save_measurement
    wb, s, v = protocol_session
    _fake_take(app, v, "tap_right")
    seg = v.confirm_step("tap_right")
    conn = get_db()
    m = Measurement(patient_id=app.patient.id, session_id=s.id, test_type="finger_tapping",
                    hand="right", duration_s=20.0)
    m.features = {"mpi": 0.6}
    m = save_measurement(conn, m)
    conn.close()
    seg.results["finger_tapping"] = {"features": {"mpi": 0.6}, "measurement_id": m.id}
    v.save(); wb.refresh(); app.pump()

    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No))
    wb._retake(v, v.step("tap_right"))
    assert asked and f"Messung #{m.id}" in asked[0]
    assert v.step("tap_right").state == "confirmed"          # No → nothing happened

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    wb._retake(v, v.step("tap_right")); app.pump()
    assert v.step("tap_right").state == "pending" and v.segments == []
    conn = get_db()
    assert [x.id for x in get_measurements(conn, app.patient.id)] == []
    conn.close()


def test_note_on_a_step_is_saved_marked_and_shown(protocol_session, app, monkeypatch, tmp_path):
    """Right-click → Notiz…: text + attachment land in NOTE_FACT, the tree
    marks the item, the recording pane's info panel shows the note."""
    from storage import attachments as att
    from storage.database import get_db, get_notes
    from ui import note_dialog as nd
    from ui import patient_workbench as pw
    monkeypatch.setattr(att, "ATTACHMENTS_DIR", tmp_path / "attachments")
    wb, s, v = protocol_session
    labels = [a[0] for a in wb._actions_for(("step", s.id, "tap_right"))]
    assert "📝 Notiz…" in labels and labels[-1] == "Schritt entfernen…"

    photo = tmp_path / "hand.jpg"; photo.write_bytes(b"jpg")

    class AutoDialog(nd.NoteDialog):
        def exec(self):
            self._text.setPlainText("Tremor sichtbar, Patient nervös")
            self.add_file(str(photo))
            self.accept()
            return 1
    monkeypatch.setattr(nd, "NoteDialog", AutoDialog)

    next(a for a in wb._actions_for(("step", s.id, "tap_right")) if a[0].startswith("📝"))[1]()
    app.pump()
    conn = get_db(); (n,) = get_notes(conn, app.patient.id); conn.close()
    assert n.kind == "step" and n.ref == f"{s.id}:tap_right" and n.text.startswith("Tremor")
    assert n.attachments[0]["name"] == "hand.jpg" and (tmp_path / "attachments").exists()

    rows = _rows(wb)
    assert any("Finger-Tapping rechts" in r[1] and "📝📎1" in r[2] for r in rows)
    assert [a[0] for a in wb._actions_for(("step", s.id, "tap_right"))][-2] == "📝 Notiz bearbeiten…"

    _fake_take(app, v, "tap_right"); v.confirm_step("tap_right"); v.save(); wb.refresh(); app.pump()
    wb._select(("step", s.id, "tap_right")); app.pump()
    lines = wb._rec._meta.texts()
    assert "Notiz: Tremor sichtbar, Patient nervös" in lines and "Anhänge: hand.jpg" in lines

    # deleting through the dialog removes note and file
    class DeleteDialog(nd.NoteDialog):
        def exec(self):
            self.deleted = True
            self.accept()
            return 1
    monkeypatch.setattr(nd, "NoteDialog", DeleteDialog)
    next(a for a in wb._actions_for(("step", s.id, "tap_right")) if a[0].startswith("📝"))[1]()
    app.pump()
    conn = get_db(); assert get_notes(conn, app.patient.id) == []; conn.close()
    assert not (tmp_path / "attachments" / "T001" / f"step_{s.id}_tap_right").exists()


def test_note_actions_exist_for_session_import_and_measurement(protocol_session, app):
    wb, s, v = protocol_session
    from tests.ui.test_screens_smoke import _measurement
    m = _measurement(app)
    wb.refresh(); app.pump()
    assert "📝 Notiz…" in [a[0] for a in wb._actions_for(("session", s.id))]
    assert "📝 Notiz…" in [a[0] for a in wb._actions_for(("measurement", m.id))]


def test_anamnesis_form_is_added_listed_shown_edited_and_deleted(protocol_session, app,
                                                                 monkeypatch, yes_to_everything):
    """„＋ Hinzufügen → Anamnese": the form dialog fills, the session shows a
    📋 node with the summary, selecting it opens the read-only view, editing
    updates in place, deleting removes all rows."""
    from clinical.store import get_form_entries
    from storage.database import get_db, get_measurements
    from ui import form_dialog as fd
    wb, s, v = protocol_session

    class AutoDialog(fd.FormDialog):
        def exec(self):
            self.set_answers({"diagnosis_year": 2018, "hoehn_yahr": "2", "updrs3_total": 31,
                              "med_state": "on",
                              "medication": [{"substance": "levodopa", "dose_mg": 100, "per_day": 3}]})
            self.accept()
            return 1
    monkeypatch.setattr(fd, "FormDialog", AutoDialog)

    wb._add_anamnesis(); app.pump()
    conn = get_db(); (e,) = get_form_entries(conn, app.patient.id)
    assert get_measurements(conn, app.patient.id) == []          # not a measurement
    conn.close()
    assert e.session_id == s.id and e.computed["ledd_mg"] == 300.0

    rows = _rows(wb)
    node = next(r for r in rows if "Parkinson-Anamnese" in r[1])
    assert node[2] == "H&Y 2  ·  UPDRS III 31  ·  LEDD 300 mg  ·  ON"
    assert wb._current_key() == ("form", e.id)
    assert wb._work.currentWidget() is wb._form_view
    lines = wb._f_meta.texts()
    assert "Hoehn & Yahr: 2 – beidseitig, keine Gleichgewichtsstörung" in lines
    assert "LEDD (mg/Tag): 300 mg" in lines
    assert [a[0] for a in wb._actions_for(("form", e.id))] == ["Bearbeiten…", "📝 Notiz…", "Löschen…"]

    class EditDialog(fd.FormDialog):
        def exec(self):
            a = self.answers(); a["hoehn_yahr"] = "3"
            self.set_answers(a); self.accept(); return 1
    monkeypatch.setattr(fd, "FormDialog", EditDialog)
    wb._edit_form(e); app.pump()
    conn = get_db(); (e2,) = get_form_entries(conn, app.patient.id); conn.close()
    assert e2.id == e.id and e2.answers["hoehn_yahr"] == "3"
    assert any(r[2].startswith("H&Y 3") for r in _rows(wb))

    # a second form on a new session starts with the last answers (carry forward)
    seen = []

    class PeekDialog(fd.FormDialog):
        def exec(self):
            seen.append(self.answers()); return 0
    monkeypatch.setattr(fd, "FormDialog", PeekDialog)
    s2 = wb.new_session(); app.pump()
    wb._select(("session", s2.id)); app.pump()
    wb._add_anamnesis(); app.pump()
    assert seen and seen[0]["hoehn_yahr"] == "3" and seen[0]["diagnosis_year"] == 2018

    wb._delete_form(e2); app.pump()
    conn = get_db(); assert get_form_entries(conn, app.patient.id) == []; conn.close()
    assert not any("Parkinson-Anamnese" in r[1] for r in _rows(wb))


def test_import_segments_are_listed_and_analysed_like_steps(protocol_session, app, monkeypatch,
                                                            yes_to_everything):
    """A cut of an imported video shows up under the import node with its
    result, can be re-analysed through the shared pipeline and deleted
    together with its measurement."""
    from storage.database import Measurement, get_db, get_measurements, save_measurement
    wb, s, v = protocol_session
    video = app.tmp / "handy.mp4"; video.write_bytes(b"v")
    v.set_video(str(video), "handy.mp4")
    seg = v.add_segment("Tapping links", 1.0, 5.0, paradigm="finger_tapping", hand="left")
    from video.meta import import_meta
    seg.meta = import_meta(v, seg)                           # what _on_add_segment does
    conn = get_db()
    m = Measurement(patient_id=app.patient.id, session_id=s.id, test_type="finger_tapping",
                    hand="left", duration_s=4.0)
    m.features = {"mpi": 0.55}
    m = save_measurement(conn, m); conn.close()
    seg.results["finger_tapping"] = {"features": {"mpi": 0.55}, "measurement_id": m.id,
                                     "recorded_at": "2026-09-10T10:00:00", "analysed_on": "import"}
    v.save(); wb.refresh(); app.pump()

    rows = _rows(wb)
    node = next(r for r in rows if r[1].startswith("✂ Tapping links"))
    assert node[2] == "MPI 0.55 ·📋" and node[0] == 2                  # child of the import node
    assert not any("Finger Tapping  (left)" in r[1] and r[0] == 1 for r in rows)  # not listed twice
    assert [a[0] for a in wb._actions_for(("segment", s.id, seg.id))] == \
        ["Details…", "⟳ Neu auswerten", "📝 Notiz…", "Segment löschen…"]

    wb._select(("segment", s.id, seg.id)); app.pump()
    assert wb._work.currentWidget() is wb._cut and wb._cut.current_segment is seg
    assert "MPI 0.55" in wb._cut._result_lbl.text() and wb._cut._details_btn.isEnabled()
    assert "Art: Import" in wb._cut._meta.texts()

    started = []
    monkeypatch.setattr(wb._pipeline.runner, "start", lambda *a, **k: started.append(a))
    wb._reanalyse_segment(v, seg); app.pump()
    assert started and started[0][0] == str(video) and started[0][1:3] == (1.0, 5.0)
    assert wb._cut._running and not wb._cut._run_btn.isEnabled()
    wb._pipeline.job = None; wb._cut._running = False

    wb._delete_segment(v, seg); app.pump()
    assert v.segments == []
    conn = get_db(); assert get_measurements(conn, app.patient.id) == []; conn.close()
    assert not any(r[1].startswith("✂") for r in _rows(wb))    # the step "Tapping links" stays
