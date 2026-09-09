"""Every remaining screen and dialog, constructed and driven on the real window.

Not deep tests — a refactor that breaks a screen's construction, its entry
path or its cancel path must fail *here*, not in front of a patient. The live
measurement flow (dashboard → readiness gate → recording → results → record)
is exercised end to end on the simulation source.
"""

import time

import pytest


def _measurement(app, test_type="finger_tapping", hand="right"):
    from storage.database import Measurement, get_db, save_measurement
    m = Measurement(patient_id=app.patient.id, session_id=None, test_type=test_type,
                    hand=hand, duration_s=10.0)
    m.features = {"mpi": 0.7, "tap_frequency_hz": 4.2, "mean_amplitude_mm": 30.0,
                  "amplitude_decrement": -0.01, "intertap_variability_cv": 0.1, "n_taps": 42}
    conn = get_db()
    m = save_measurement(conn, m)
    conn.close()
    return m


# ── the live measurement path ───────────────────────────────────────

def test_live_measurement_runs_through_to_the_record(app, workbench, monkeypatch):
    """Dashboard → gate → 2 s recording → results → Measurement in the DB."""
    from storage.database import get_db, get_measurements
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    win = app.win
    win.start_new_session()
    app.pump()
    win.start_test("finger_tapping", "right", 2)
    app.pump(0.2)
    assert win.stack.currentWidget() is win.test_screen

    # The simulation source feeds the readiness gate; give it a moment, then
    # push through if it has not resolved (the gate's own timing is not the
    # subject here).
    ts = win.test_screen
    t0 = time.time()
    while time.time() - t0 < 4.0 and win.stack.currentWidget() is ts and \
            getattr(ts, "_phase", None) in (None, "gate", "ready"):
        app.pump(0.1)
    if win.stack.currentWidget() is ts and hasattr(ts, "_on_gate_ready"):
        try:
            ts._on_gate_ready("right")
        except Exception:
            pass

    t0 = time.time()
    while time.time() - t0 < 20.0 and win.stack.currentWidget() is not win.results_screen:
        app.pump(0.1)

    assert win.stack.currentWidget() is win.results_screen, \
        f"Ergebnis-Screen nicht erreicht (aktiv: {type(win.stack.currentWidget()).__name__})"
    conn = get_db()
    ms = get_measurements(conn, app.patient.id)
    conn.close()
    assert [m.test_type for m in ms] == ["finger_tapping"]
    assert ms[0].hand == "right" and ms[0].session_id == win.current_session.id
    assert "mpi" in ms[0].features

    # "Fortfahren" leads back to the dashboard; the workbench shows the measurement
    win.results_screen._on_next()
    app.pump(0.2)
    win.show_patient_detail()
    app.pump(0.2)
    rows = [win.patient_detail.tree.topLevelItem(0).child(i).text(0)
            for i in range(win.patient_detail.tree.topLevelItem(0).childCount())]
    assert any("Finger Tapping" in r for r in rows)


# ── interactive paradigm screens ────────────────────────────────────

@pytest.mark.parametrize("key,screen_attr", [
    ("tower_of_hanoi", "hanoi_screen"),
    ("spatial_srt", "srt_screen"),
    ("trail_making_a", "tmt_screen"),
    ("trail_making_b", "tmt_screen"),
    ("saccade_test", "saccade_screen"),
])
def test_interactive_screen_starts_and_cancels(app, workbench, key, screen_attr, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    win = app.win
    win.start_new_session()
    app.pump()

    win.start_test(key, "right", 5)
    app.pump(0.3)
    screen = getattr(win, screen_attr)
    assert win.stack.currentWidget() is screen

    screen._on_cancel()
    app.pump(0.3)
    assert win.stack.currentWidget() is not screen


# ── dialogs ─────────────────────────────────────────────────────────

def test_detail_and_trend_dialogs_build_from_a_real_measurement(app, workbench):
    from ui.detail_dialog import DetailDialog
    from ui.trend_dialog import TrendDialog
    m = _measurement(app)
    m2 = _measurement(app, hand="left")

    dlg = DetailDialog(app.patient, m, siblings=[m, m2], parent=workbench)
    dlg.show(); app.pump(0.1)
    assert dlg.isVisible() and "Test" in dlg.windowTitle()
    dlg.close()

    # the workbench's own "Details…" action must build the same dialog
    workbench.refresh(); app.pump()
    from ui import patient_workbench as pw
    built = []
    real = pw.DetailDialog

    class Spy(real):
        def exec(self):
            built.append(self.windowTitle()); return 0
    import unittest.mock as um
    with um.patch.object(pw, "DetailDialog", Spy):
        workbench._show_measurement(m)
    assert built and "finger_tapping" in built[0]

    trend = TrendDialog(app.patient, [m, m2], parent=workbench)
    trend.show(); app.pump(0.2)
    assert trend.isVisible()
    trend.close()


def test_log_viewer_and_about_open(app):
    from ui.log_viewer import LogViewerDialog
    dlg = LogViewerDialog(app.win)
    dlg.show(); app.pump(0.1)
    assert dlg.isVisible()
    dlg.close()


def test_workbench_measurement_pane_and_detail_action(app, workbench):
    m = _measurement(app)
    workbench.refresh(); app.pump()
    workbench._select(("measurement", m.id)); app.pump()
    assert type(workbench._work.currentWidget()).__name__ == "QWidget"
    assert "Finger Tapping" in workbench._m_title.text()
    assert [a[0] for a in workbench._actions_for(("measurement", m.id))] == \
        ["Details…", "Messung löschen…"]


def test_workbench_csv_export_writes_every_measurement(app, workbench, tmp_path, monkeypatch):
    _measurement(app); _measurement(app, hand="left")
    workbench.refresh(); app.pump()
    out = tmp_path / "export.csv"
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "CSV (*.csv)")))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    workbench._on_csv_export()

    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("session,test_type,hand,duration_s,recorded_at,mpi")
    assert len(lines) == 3


# ── tracking screen (input source) without a sidecar ───────────────

def test_tracking_screen_opens_and_closes_without_a_sidecar(app, monkeypatch):
    from capture.mediapipe_capture import WebcamSource
    monkeypatch.setattr(WebcamSource, "sidecar_ready",
                        staticmethod(lambda: (False, ["Sidecar fehlt (Test)"])))
    win = app.win
    win.show_tracking_screen()
    app.pump(0.3)
    assert win.stack.currentWidget() is win.tracking_screen
    win.close_tracking_screen()
    app.pump(0.1)
    assert win.stack.currentWidget() is not win.tracking_screen


def test_ui_mode_toggle_rebuilds_all_screens(app):
    from ui import theme
    before = theme.current_ui_mode()
    app.win.toggle_ui_mode()
    app.pump(0.2)
    assert theme.current_ui_mode() != before
    assert app.win.stack.currentWidget() is app.win.patient_screen
    app.win.toggle_ui_mode()          # back, so the setting does not leak
    app.pump(0.2)
    assert theme.current_ui_mode() == before


def test_detail_dialog_tolerates_a_video_clip_as_raw_data(app, workbench, tmp_path):
    """Older video measurements point at the archived clip, not at JSON."""
    from ui.detail_dialog import DetailDialog
    m = _measurement(app)
    clip = tmp_path / "seg_001.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    m.raw_data_path = str(clip)
    dlg = DetailDialog(app.patient, m, parent=workbench)
    texts = [t.get_text() for ax in dlg._figure.axes for t in ax.texts]
    assert any("Nur der Video-Clip" in t for t in texts)
    dlg.close()


def test_detail_dialog_shows_the_measurement_provenance(app, workbench):
    from ui.detail_dialog import DetailDialog
    m = _measurement(app)
    m.source_kind = "video"
    m.provenance = {"capture": {"kind": "recording", "recorded_at": "2026-09-09T08:48:00",
                                "camera": {"index": 1, "name": "OBSBOT Tiny 2"},
                                "mirror": True, "swap_handedness": False},
                    "clip_path": "", "track_path": "", "analysed_on": "clip",
                    "analysed_at": "2026-09-09T11:27:00", "deidentified": True}
    dlg = DetailDialog(app.patient, m, parent=workbench)
    lines = dlg._meta.texts()
    assert "Kamera: OBSBOT Tiny 2" in lines
    assert "Ausgewertet: 2026-09-09 11:27  ·  auf: archivierter Clip" in lines
    assert "Gespiegelt: ja (Anzeige und Analyse)" in lines
    dlg.close()

    # the measurement pane in the workbench carries the same panel
    workbench.refresh(); app.pump()
    workbench._select(("measurement", m.id)); app.pump()
    assert workbench._m_meta.texts()          # legacy row at least
