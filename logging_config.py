"""Centralized logging configuration for TapPD.

- Logs to data/logs/ with daily rotation
- Auto-deletes logs older than 7 days
- Provides a QHandler that emits signals for the UI log viewer
"""

import logging
import os
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

# ── Paths ──────────────────────────────────────────────────────────────

LOG_DIR = Path(__file__).parent / "data" / "logs"
LOG_FILE = LOG_DIR / "tappd.log"

# ── Format ─────────────────────────────────────────────────────────────

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_LOG_AGE_DAYS = 7


# ── Qt Signal Bridge ──────────────────────────────────────────────────

class _LogSignalBridge(QObject):
    """Bridge between Python logging and Qt signal system."""
    log_emitted = pyqtSignal(str)  # formatted log line


_bridge = _LogSignalBridge()


class QtLogHandler(logging.Handler):
    """Logging handler that emits log records as Qt signals."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            _bridge.log_emitted.emit(msg)
        except Exception:
            self.handleError(record)

    @staticmethod
    def get_signal():
        return _bridge.log_emitted


# ── Cleanup ────────────────────────────────────────────────────────────

def _cleanup_old_logs() -> None:
    """Delete log files older than MAX_LOG_AGE_DAYS."""
    if not LOG_DIR.exists():
        return
    cutoff = time.time() - MAX_LOG_AGE_DAYS * 86400
    for f in LOG_DIR.iterdir():
        if f.suffix == ".log" or ".log." in f.name:
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


# ── Setup ──────────────────────────────────────────────────────────────

def setup_logging(level: int = logging.DEBUG) -> None:
    """Configure root logger with file + console + Qt handlers."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_old_logs()

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on re-init
    if any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers):
        return

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # File handler: daily rotation, keep 7 days
    file_handler = TimedRotatingFileHandler(
        str(LOG_FILE),
        when="midnight",
        interval=1,
        backupCount=MAX_LOG_AGE_DAYS,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Console handler: INFO and above
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # Qt handler for UI log viewer
    qt_handler = QtLogHandler()
    qt_handler.setLevel(logging.DEBUG)
    root.addHandler(qt_handler)

    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)

    logging.info("TapPD Logging initialisiert – Log-Verzeichnis: %s", LOG_DIR)
