"""TapPD – Contactless Motor Analysis for Parkinson's Disease."""

import logging
import os
import sys

# Ensure LeapC shared library can be found
_LEAPC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leapc_cffi")
if os.path.isdir(_LEAPC_DIR):
    if sys.platform == "win32":
        os.environ["PATH"] = _LEAPC_DIR + os.pathsep + os.environ.get("PATH", "")
    else:
        os.environ["DYLD_LIBRARY_PATH"] = _LEAPC_DIR
    if _LEAPC_DIR not in sys.path:
        sys.path.insert(0, _LEAPC_DIR)

from PyQt6.QtWidgets import QApplication

from logging_config import setup_logging
from capture import create_capture_device
from ui.main_window import TapPDMainWindow
from ui.theme import APP_STYLESHEET

log = logging.getLogger(__name__)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("TapPD")
    app.setStyleSheet(APP_STYLESHEET)

    setup_logging()
    log.info("TapPD wird gestartet (Python %s, Plattform: %s)", sys.version.split()[0], sys.platform)

    mode = "mock" if "--mock" in sys.argv else "auto"
    log.info("Capture-Modus: %s", mode)
    device = create_capture_device(mode)
    log.info("Capture-Device erstellt: %s", type(device).__name__)

    window = TapPDMainWindow(device)
    window.show()
    log.info("Hauptfenster angezeigt")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
