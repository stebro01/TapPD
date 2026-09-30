"""Fixtures for driving the real Qt application offscreen.

These tests build the actual MotryxMainWindow against a simulation source and
a throw-away database, then click through it the way a clinician would. They
found every UI bug of the last refactors — the camera picker snapping back,
the tree rebuilt under a selection, results that never reached the record —
which is exactly why they live in the repo and not in a scratch folder.

No camera, no sidecar by default: the workbench's device acquisition is
stubbed out, so the suite stays fast and runs on any machine. Tests that need
the MediaPipe sidecar carry the ``sidecar`` marker and skip when it is not set
up.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

# Must be set before the QApplication exists. Offscreen renders without system
# fonts on Windows (boxes instead of glyphs) — irrelevant for tests.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 nicht installiert")


def sidecar_available() -> bool:
    try:
        from capture.mediapipe_capture import WebcamSource
        return WebcamSource.sidecar_ready()[0]
    except Exception:
        return False


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    """Every store the app writes to, redirected into tmp_path."""
    import video.store as store
    import storage.database as db
    import logging_config as lc

    monkeypatch.setattr(store, "VIDEO_SESSIONS_DIR", tmp_path / "video_sessions")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(lc, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(lc, "LOG_FILE", tmp_path / "logs" / "test.log")
    return tmp_path


@pytest.fixture
def no_camera(monkeypatch):
    """Keep the workbench from opening a real webcam / sidecar."""
    from ui import patient_workbench as pw
    monkeypatch.setattr(pw.PatientWorkbench, "_acquire_device", lambda self: None)


@pytest.fixture
def yes_to_everything(monkeypatch):
    """Confirmation dialogs answer Yes, so destructive flows can be tested."""
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))


@pytest.fixture
def app(qapp, isolated_data, no_camera):
    """The real main window on a simulation source, with one patient."""
    from PyQt6.QtCore import Qt
    from capture.mock_capture import SimulationSource
    from storage.database import Patient, get_db, save_patient
    from ui.main_window import MotryxMainWindow

    conn = get_db()
    patient = save_patient(conn, Patient(patient_code="T001", first_name="Test",
                                         last_name="Person", birth_date="1960-01-01",
                                         gender="f"))
    conn.close()

    win = MotryxMainWindow(SimulationSource())
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(1280, 800)
    win.show()
    qapp.processEvents()

    def pump(seconds: float = 0.0) -> None:
        import time
        end = time.time() + seconds
        qapp.processEvents()
        while time.time() < end:
            qapp.processEvents()
            time.sleep(0.01)

    yield SimpleNamespace(qapp=qapp, win=win, patient=patient, tmp=isolated_data, pump=pump)

    try:
        if win.stack.currentWidget() is win.patient_detail:
            win.patient_detail.leave()
    except Exception:
        pass
    win.close()
    win.deleteLater()
    qapp.processEvents()


@pytest.fixture
def workbench(app):
    """The patient opened in the workbench, no sessions yet."""
    app.win.select_patient(app.patient)
    app.pump()
    return app.win.patient_detail
