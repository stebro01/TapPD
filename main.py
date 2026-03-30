"""TapPD – Contactless Motor Analysis for Parkinson's Disease."""

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

from capture import create_capture_device
from ui.main_window import TapPDMainWindow
from ui.theme import APP_STYLESHEET


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("TapPD")
    app.setStyleSheet(APP_STYLESHEET)

    mode = "mock" if "--mock" in sys.argv else "auto"
    device = create_capture_device(mode)

    window = TapPDMainWindow(device)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
