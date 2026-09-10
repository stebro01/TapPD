"""Erzeugt die Handbuch-Screenshots (docs/img/) aus der ECHTEN App.

Rendert die Qt-Screens offscreen (QWidget.grab) gegen eine Demo-Datenbank —
die echte data/tappd.db bleibt unberührt. Nach UI-Änderungen neu ausführen:

    QT_QPA_PLATFORM=offscreen .venv/bin/python docs/make_screenshots.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

# Offscreen renders without system fonts on Windows (every glyph a box), so use
# the native platform there and keep the window off the desktop instead.
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

IMG = ROOT / "docs" / "img"
IMG.mkdir(parents=True, exist_ok=True)

WIN_W, WIN_H = 1280, 820


def grab(widget, name: str) -> None:
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    pm = widget.grab()
    pm.save(str(IMG / f"{name}.png"), "PNG")
    print(f"  {name}.png  ({pm.width()}x{pm.height()})")


def seed_demo_db() -> None:
    """Demo-Patient mit realistischen Messungen in der Temp-DB."""
    from datetime import datetime, timedelta
    from storage.database import Measurement, Patient, create_session, get_db, save_measurement, save_patient

    conn = get_db()
    try:
        p = save_patient(conn, Patient(patient_code="DEMO01", first_name="Maria",
                                       last_name="Muster", birth_date="1958-04-12",
                                       gender="f"))
        base = datetime(2026, 3, 5, 10, 0)
        runs = [
            (0, "leap", 0.82, 4.6, 42.0),
            (35, "leap", 0.74, 4.2, 39.0),
            (70, "webcam", 0.66, 3.9, 36.5),
            (100, "webcam", 0.61, 3.6, 34.0),
        ]
        for days, kind, mpi, freq, amp in runs:
            s = create_session(conn, p.id)
            for hand, f_mod in (("right", 1.0), ("left", 0.92)):
                m = Measurement(patient_id=p.id, session_id=s.id,
                                test_type="finger_tapping", hand=hand,
                                duration_s=10.0, source_kind=kind,
                                recorded_at=(base + timedelta(days=days)).isoformat())
                m.features = {"mpi": round(mpi * f_mod, 3),
                              "tap_frequency_hz": round(freq * f_mod, 2),
                              "mean_amplitude_mm": round(amp * f_mod, 1),
                              "amplitude_decrement": -0.012,
                              "intertap_variability_cv": 0.14, "n_taps": 44}
                save_measurement(conn, m)
            m = Measurement(patient_id=p.id, session_id=s.id,
                            test_type="ocular_fixation", hand="both",
                            duration_s=20.0, source_kind="webcam",
                            recorded_at=(base + timedelta(days=days, hours=1)).isoformat())
            m.features = {"blink_rate_per_min": round(11 + days * 0.03, 1),
                          "gaze_dispersion_pct_ipd": 1.4, "mean_ear": 0.29,
                          "saccadic_intrusions_per_min": 3.2,
                          "n_face_frames": 590.0, "face_coverage": 0.98}
            save_measurement(conn, m)
        return p
    finally:
        conn.close()


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="motryx_docs_"))
    import storage.database as db
    db.DB_PATH = tmp / "demo.db"
    import video.store as vstore
    vstore.VIDEO_SESSIONS_DIR = tmp / "video_sessions"

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer
    app = QApplication([])
    from ui import theme
    theme.set_ui_mode("dense")
    app.setStyleSheet(theme.APP_STYLESHEET)

    patient = seed_demo_db()

    from capture import create_source
    src = create_source("mock")
    src.connect()

    from ui.main_window import MotryxMainWindow
    w = MotryxMainWindow(src)
    from PyQt6.QtCore import Qt
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)   # grab() still renders
    w.resize(WIN_W, WIN_H)
    w.show()
    app.processEvents()

    # 1) Patientenliste
    w.patient_screen.refresh_list()
    app.processEvents()
    grab(w, "01_patienten")

    # 2) Patienten-Detail (Matrix, Buttons)
    from storage.database import get_db, get_patient
    conn = get_db()
    p = get_patient(conn, patient.id)
    conn.close()
    w.select_patient(p)
    app.processEvents()
    grab(w, "02_patient_detail")

    # 3) Verlauf
    from storage.database import get_measurements
    conn = get_db()
    ms = get_measurements(conn, p.id)
    conn.close()
    from ui.trend_dialog import TrendDialog
    dlg = TrendDialog(p, ms, parent=w)
    dlg.resize(980, 620)
    dlg.show()
    app.processEvents()
    grab(dlg, "03_verlauf")
    dlg.close()

    # 4) Dashboard (11 Kacheln)
    w.start_new_session()
    app.processEvents()
    grab(w, "04_dashboard")

    # 5) Fixationstest: Instruktionen + Gesichts-Gate
    src.mode = "ocular_fixation"
    w.start_test("ocular_fixation", "both", 20)
    app.processEvents()
    time.sleep(1.2)          # Gate erkennt Mock-Gesicht → Countdown
    for _ in range(10):
        app.processEvents()
        time.sleep(0.05)
    grab(w, "05_fixation_gate")
    w.test_screen._on_cancel()
    app.processEvents()

    # 6) Ergebnis-Screen: Fixation (synthetische Messung)
    from capture.base_capture import TrackingFrame
    from paradigms.ocular_fixation import OcularFixationTest
    test = OcularFixationTest(capture=src, duration=20.0)
    for i in range(1200):
        t = i / 60.0
        test._on_tracking(TrackingFrame(timestamp_us=int(t * 1e6),
                                        face=src._build_face(t, int(t * 1e6))))
    w.current_patient = p
    w.results_screen.show_results(test, p.patient_code,
                                  features=test.compute_features())
    w.stack.setCurrentWidget(w.results_screen)
    app.processEvents()
    grab(w, "06_ergebnis_fixation")

    # 7) Ergebnis-Screen: Finger Tapping (echter Sim-Lauf)
    from paradigms.finger_tapping import FingerTappingTest
    src.mode = "tapping"
    ft = FingerTappingTest(capture=src, duration=5.0, hand="right")
    ft.start()
    time.sleep(3.0)
    ft.stop()
    w.results_screen.show_results(ft, p.patient_code,
                                  features=ft.compute_features())
    app.processEvents()
    grab(w, "07_ergebnis_tapping")

    # 8) Sakkaden-Screen mitten in der Eichung
    src.mode = "ocular_fixation"
    w.start_test("saccade_test", "both", 30)
    app.processEvents()
    grab(w, "08_sakkaden_start")
    w.saccade_screen._on_start()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.03)
    grab(w, "09_sakkaden_eichung")
    w.saccade_screen._on_cancel()
    app.processEvents()

    # 10) Eingabequelle (Tracking-Screen)
    w.show_tracking_screen() if hasattr(w, "show_tracking_screen") else \
        w.stack.setCurrentWidget(w.tracking_screen)
    app.processEvents()
    grab(w, "10_eingabequelle")

    # 11) Sitzung (Aufnahme / Import / Auswertung in einem Bildschirm)
    w.current_patient = p
    from storage.database import create_session, get_db
    _c = get_db()
    _s = create_session(_c, p.id)
    _c.close()
    w.show_session(_s)
    app.processEvents()
    grab(w, "11_sitzung")

    # 11b) Protokoll in der Sitzung: ein Schritt offen (Aufnahme-Bereich, Kamera-Vorschau)
    import shutil
    from video.protocol import load_protocol
    wb = w.patient_detail
    v = wb._video_for(_s, create=True)
    v.add_steps(load_protocol("updrs_hand_basis"))
    v.save()
    wb.refresh(); app.processEvents()
    wb._select(("step", _s.id, "tap_right")); app.processEvents()
    wb._rec._detect_lbl.setText("✔ 1 Hand (rechts)   ·   ✔ Gesicht")
    grab(w, "11b_aufnahme")

    # 11c) Bestätigter, ausgewerteter Take mit Zusammenfassung und Aufnahme-Info.
    # Mit Sidecar läuft die echte Pipeline auf einem Demo-Clip; ohne bleibt ein
    # synthetischer Stand (Take-Datei ohne Bild).
    from tests.ui.conftest import sidecar_available
    # A real tapping take (defaced archive) when the developer machine has one,
    # else the Sim clip shipped with the repo.
    _cands = [ROOT / "data" / "video_sessions" / "232" / "session_6" / "seg_006.mp4",
              ROOT / "data" / "clips" / "default.mp4"]
    demo_clip = next((c for c in _cands if c.is_file()), _cands[-1])
    take = v.begin_take("tap_right")
    if demo_clip.is_file():
        shutil.copy(demo_clip, take)
    else:
        take.write_bytes(b"\x00" * 2048)
    v.mark_recorded("tap_right", take)
    from video import meta as vmeta
    st = v.step("tap_right")
    st.meta = vmeta.note_recorded(
        vmeta.capture_meta(type("Cam", (), {"camera_index": 1, "camera_name": "OBSBOT Tiny 2",
                                             "_face_on": True,
                                             "sidecar_info": {"mediapipe": "1.0.1", "opencv": "4.12.0"}})(),
                           take=1, take_file=str(take)),
        {"w": 640, "h": 480, "fps": 30.0, "frames": 600, "codec": "avc1"})
    seg = v.confirm_step("tap_right")
    v.save()
    wb.refresh(); app.processEvents()
    wb._select(("step", _s.id, "tap_right")); app.processEvents()
    if sidecar_available() and demo_clip.is_file():
        wb._rec.enqueue(v, "tap_right", seg, analyse=True)
        deadline = time.monotonic() + 240
        while (wb._rec._job is not None or take.is_file()) and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.05)
    else:
        seg.results["finger_tapping"] = {
            "features": {"mpi": 0.81, "tap_frequency_hz": 3.04, "mean_amplitude_mm": 63.9,
                         "amplitude_decrement": -0.008, "intertap_variability_cv": 0.10,
                         "n_taps": 50},
            "recorded_at": "2026-09-09T11:27:04", "analysed_on": "raw", "raw_path": ""}
        v.save()
    wb.refresh(); app.processEvents()
    wb._select(("step", _s.id, "tap_right")); app.processEvents()
    wb._rec._meta.set_expanded(True)
    for _ in range(20):
        app.processEvents(); time.sleep(0.05)
    grab(w, "11c_take_info")

    # 11d) Notiz zum Schritt
    from storage.database import Note
    from ui.note_dialog import NoteDialog
    note = Note(patient_id=p.id, session_id=_s.id, kind="step", ref=f"{_s.id}:tap_right",
                text="Patientin berichtet morgens stärkeres Zittern; Medikation um 8 Uhr.")
    dlg = NoteDialog(w, "Finger-Tapping rechts", note, p.patient_code)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dlg.show(); app.processEvents()
    grab(dlg, "11d_notiz")
    dlg.reject()

    # 11d2) Anamnese-Maske
    from clinical.schema import load_form
    from ui.form_dialog import FormDialog
    fdlg = FormDialog(w, load_form("pd_anamnese"), patient=p,
                      session_label=f"Sitzung vom {_s.started_at[:10]}",
                      answers={"diagnosis_year": 2019, "onset_year": 2017, "onset_side": "right",
                               "dominant_hand": "right", "subtype": "tremor_dominant",
                               "hoehn_yahr": "2", "updrs3_total": 28, "updrs3_state": "off",
                               "family_pd": "no", "falls_12m": 1, "freezing": "no",
                               "walking_aid": "none", "nms": ["hyposmia", "rbd", "constipation"],
                               "moca": 27, "med_state": "off", "last_dose_minutes": 780,
                               "medication": [
                                   {"substance": "levodopa", "dose_mg": 100, "per_day": 4,
                                    "times": "7, 11, 15, 19"},
                                   {"substance": "pramipexole", "dose_mg": 0.7, "per_day": 3},
                                   {"substance": "rasagiline", "dose_mg": 1, "per_day": 1}]})
    fdlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    fdlg.resize(900, 1000); fdlg.show(); app.processEvents()
    from PyQt6.QtWidgets import QScrollArea as _QSA
    _sa = fdlg.findChild(_QSA)
    _sa.verticalScrollBar().setValue(_sa.verticalScrollBar().maximum() // 2)
    app.processEvents()
    grab(fdlg, "11h_anamnese")
    fdlg.reject()

    # 11e) Details einer Video-Messung (Kennwerte, Kurven, Herkunft)
    from storage.database import get_measurements
    _c = get_db(); ms = get_measurements(_c, p.id); _c.close()
    m = next((x for x in ms if x.source_kind == "video"), ms[0])
    from ui.detail_dialog import DetailDialog
    dd = DetailDialog(p, m, siblings=[x for x in ms if x.test_type == m.test_type], parent=w)
    dd.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dd.show(); dd._meta.set_expanded(True); app.processEvents()
    grab(dd, "11e_details")
    dd.close()

    # 11f) Auswahl beim Hinzufügen: Protokoll oder einzelnes Paradigma
    from ui.protocol_chooser import ProtocolChooser
    pc = ProtocolChooser(w, single_only=True)
    pc.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    pc.show(); app.processEvents()
    grab(pc, "11f_paradigma_wahl")
    pc.reject()

    # 11g) Schnitt-Bereich eines importierten Videos
    if demo_clip.is_file():
        _c = get_db(); _s2 = create_session(_c, p.id); _c.close()
        wb.refresh(); app.processEvents()
        v2 = wb._video_for(_s2, create=True)
        dest = v2._dir() / "import.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(demo_clip, dest)
        v2.set_video(str(dest), "handy_tapping.mp4")
        v2.save()
        wb.refresh(); app.processEvents()
        wb._select(("import", _s2.id))
        for _ in range(30):
            app.processEvents(); time.sleep(0.05)
        grab(w, "11g_schnitt")
    w.close_session()

    # 12) Gesture Lab
    w.show_gesture_lab("detail")
    app.processEvents()
    grab(w, "12_gesturelab")

    # 13) Über-Dialog
    w.show_patient_screen()
    app.processEvents()
    QTimer.singleShot(600, lambda: [grab(d, "13_ueber") or d.accept()
                                    for d in w.patient_screen.findChildren(
                                        __import__("PyQt6.QtWidgets", fromlist=["QDialog"]).QDialog)
                                    if d.isVisible()])
    w.patient_screen._on_about()
    app.processEvents()

    src.disconnect()
    print("fertig →", IMG)


if __name__ == "__main__":
    main()
