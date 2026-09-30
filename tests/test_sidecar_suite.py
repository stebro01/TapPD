"""Run the sidecar's own tests under the sidecar venv, as part of this suite.

The sidecar modules import cv2/mediapipe and cannot be imported here; their
logic (handedness under mirroring, backend choice, camera enumeration, the
ffmpeg pass) is tested in mediapipe_sidecar/tests with the sidecar's Python.
Skipped when the sidecar venv (or pytest inside it) is missing.
"""

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.sidecar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sidecar_python():
    from capture.mediapipe_capture import _SIDECAR_PY
    return _SIDECAR_PY if os.path.isfile(_SIDECAR_PY) else None


def _has_pytest(py):
    try:
        return subprocess.run([py, "-c", "import pytest"], capture_output=True,
                              timeout=60).returncode == 0
    except Exception:
        return False


def test_sidecar_suite_passes():
    py = _sidecar_python()
    if not py:
        pytest.skip("MediaPipe-Sidecar-venv nicht eingerichtet")
    if not _has_pytest(py):
        pytest.skip("pytest fehlt im Sidecar-venv (setup_sidecar erneut ausführen)")

    r = subprocess.run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        os.path.join(ROOT, "mediapipe_sidecar", "tests")],
                       capture_output=True, text=True, timeout=600, cwd=ROOT)
    assert r.returncode == 0, "Sidecar-Tests fehlgeschlagen:\n" + r.stdout[-3000:] + r.stderr[-1500:]
