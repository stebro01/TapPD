"""Motryx – Movement Lab (contactless motor & movement analysis)."""

import logging
import os
import sys

# Set Windows AppUserModelID so taskbar groups correctly and shows our icon
if sys.platform == "win32":
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("tappd.motor.analysis")

# Ensure LeapC shared library can be found
_LEAPC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leapc_cffi")
if os.path.isdir(_LEAPC_DIR):
    if sys.platform == "win32":
        os.environ["PATH"] = _LEAPC_DIR + os.pathsep + os.environ.get("PATH", "")
    else:
        os.environ["DYLD_LIBRARY_PATH"] = _LEAPC_DIR
    if _LEAPC_DIR not in sys.path:
        sys.path.insert(0, _LEAPC_DIR)

from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from logging_config import setup_logging
from capture import create_capture_device
from ui.main_window import MotryxMainWindow
from ui import theme

ICON_PATH = Path(__file__).parent / "assets" / "tappd.png"

log = logging.getLogger(__name__)


def _install_excepthook() -> None:
    """Log unhandled exceptions instead of letting PyQt6 abort the process.

    PyQt terminates the application when a Python exception propagates out of a
    Qt slot.  A custom sys.excepthook is called instead and keeps the app alive,
    turning a hard crash into a logged, recoverable error.
    """
    def handler(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.error("Unbehandelte Ausnahme", exc_info=(exc_type, exc, tb))

    sys.excepthook = handler


def main() -> None:
    _install_excepthook()
    from app_settings import APP_NAME, app_settings
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    # Restore saved UI mode (dense/touch) — migrates legacy TapPD settings once.
    settings = app_settings()
    ui_mode = settings.value("ui_mode", "touch")
    theme.set_ui_mode(ui_mode)
    app.setStyleSheet(theme.APP_STYLESHEET)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    setup_logging()
    log.info("%s wird gestartet (Python %s, Plattform: %s)", APP_NAME, sys.version.split()[0], sys.platform)

    # Capture mode: --mock wins; else the last source the user picked on the
    # Tracking screen (persisted in QSettings); else auto (Leap -> mock).
    if "--mock" in sys.argv:
        mode = "mock"
    else:
        mode = settings.value("capture_mode", "auto") or "auto"
    log.info("Capture-Modus: %s", mode)

    if mode == "mediapipe":
        cam_idx = int(settings.value("camera_index", 0) or 0)
        flip = settings.value("flip_handedness", False)
        flip = (flip in (True, "true", "True", 1, "1"))
        try:
            device = create_capture_device("mediapipe", camera_index=cam_idx,
                                           flip_handedness=flip)
            device.connect()
        except Exception as e:
            log.warning("Webcam-Tracking-Start fehlgeschlagen (%s) – Fallback auf auto", e)
            device = create_capture_device("auto")
    else:
        device = create_capture_device(mode)
    log.info("Capture-Device erstellt: %s", type(device).__name__)

    window = MotryxMainWindow(device)
    window.show()
    log.info("Hauptfenster angezeigt")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
