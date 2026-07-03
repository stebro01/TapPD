"""Application identity + persistent settings, with a one-time migration.

The app was renamed TapPD → Motryx. ``app_settings()`` returns the Motryx
QSettings and, on first use, copies the existing TapPD settings over so the
user's preferences (capture source, UI mode, …) survive the rename.

Note: on macOS ``QSettings.allKeys()`` also returns global NSUserDefaults keys,
so "is this domain empty?" is unreliable — we migrate a known key list guarded
by a sentinel instead.
"""

from __future__ import annotations

APP_ORG = "Motryx"
APP_NAME = "Motryx"
APP_TITLE = "Motryx – Movement Lab"
# Single source of truth for the app version (pyproject.toml mirrors this;
# shown in the Über-Dialog, the about button and the startup log).
APP_VERSION = "0.2.0"
_LEGACY_ORG = "TapPD"

# App-owned settings keys to carry across the rename.
_APP_KEYS = ["ui_mode", "capture_mode", "camera_index", "flip_handedness"]
_MIGRATED_FLAG = "_migrated_from_tappd"

_migrated = False


def app_settings():
    from PyQt6.QtCore import QSettings
    global _migrated
    s = QSettings(APP_ORG, APP_NAME)
    if not _migrated:
        _migrated = True
        if not s.contains(_MIGRATED_FLAG):
            legacy = QSettings(_LEGACY_ORG, _LEGACY_ORG)
            for key in _APP_KEYS:
                if legacy.contains(key) and not s.contains(key):
                    s.setValue(key, legacy.value(key))
            s.setValue(_MIGRATED_FLAG, True)
            s.sync()
    return s
