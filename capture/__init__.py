"""Capture device factory with sensor diagnostics."""

import logging
import os
import sys
import subprocess
import shutil

from capture.base_capture import MotionSource, BaseCaptureDevice

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"
_IS_MACOS = sys.platform == "darwin"


def diagnose_sensor() -> list[str]:
    """Check SDK, USB device, and tracking service. Returns list of issues found."""
    log.debug("Starte Sensor-Diagnose...")
    issues = []

    # 1. Check if Ultraleap Tracking software is installed
    if _IS_WINDOWS:
        service_installed = os.path.isdir(r"C:\Program Files\Ultraleap")
    else:
        service_installed = os.path.isdir("/Applications/Ultraleap Hand Tracking.app")

    if not service_installed:
        issues.append(
            "Ultraleap Hand Tracking Software nicht gefunden.\n"
            "  -> Bitte installieren: https://www.ultraleap.com/downloads/leap-controller/"
        )
    else:
        # Check if tracking service is running
        try:
            if _IS_WINDOWS:
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq LeapSvc.exe"],
                    capture_output=True, text=True, timeout=5,
                )
                if "LeapSvc.exe" not in result.stdout:
                    issues.append(
                        "Ultraleap Tracking-Service (LeapSvc) läuft nicht.\n"
                        "  -> Starte den Ultraleap Tracking Service oder das Control Panel"
                    )
            else:
                result = subprocess.run(
                    ["pgrep", "-f", "libtrack_server"],
                    capture_output=True, timeout=3,
                )
                if result.returncode != 0:
                    issues.append(
                        "Ultraleap Tracking-Service (libtrack_server) läuft nicht.\n"
                        '  -> Starte "Ultraleap Hand Tracking" aus /Applications/'
                    )
        except Exception:
            issues.append("Konnte Tracking-Service-Status nicht prüfen.")

    # 2. Check if LeapC CFFI bindings are available
    leapc_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "leapc_cffi")
    if not os.path.isdir(leapc_dir):
        if _IS_WINDOWS:
            issues.append(
                "LeapC Python-Bindings nicht gefunden (leapc_cffi/ Verzeichnis fehlt).\n"
                r"  -> start.ps1 oder start.bat kopiert diese automatisch aus dem SDK"
            )
        else:
            issues.append(
                "LeapC Python-Bindings nicht gefunden (leapc_cffi/ Verzeichnis fehlt).\n"
                "  -> Kopiere aus dem SDK: /Applications/Ultraleap Hand Tracking.app/Contents/LeapSDK/leapc_cffi/"
            )

    # 3. Check USB device
    try:
        if _IS_WINDOWS:
            result = subprocess.run(
                ["pnputil", "/enum-devices", "/connected"],
                capture_output=True, text=True, timeout=5,
            )
            if "Leap" not in result.stdout and "Ultraleap" not in result.stdout:
                issues.append(
                    "Kein Leap Motion Controller als USB-Gerät erkannt.\n"
                    "  -> Prüfe USB-Kabel und Verbindung\n"
                    "  -> Anderen USB-Port versuchen\n"
                    "  -> Controller-LED sollte grün leuchten"
                )
        else:
            result = subprocess.run(
                ["ioreg", "-p", "IOUSB", "-l"],
                capture_output=True, text=True, timeout=5,
            )
            if "Leap" not in result.stdout:
                issues.append(
                    "Kein Leap Motion Controller als USB-Gerät erkannt.\n"
                    "  -> Prüfe USB-Kabel und Verbindung\n"
                    "  -> Anderen USB-Port versuchen\n"
                    "  -> Controller-LED sollte grün leuchten"
                )
    except Exception:
        issues.append("Konnte USB-Geräte nicht abfragen.")

    return issues


# Canonical source-kind strings, with accepted aliases normalized to them.
_KIND_ALIASES = {
    "mediapipe": "webcam",
    "sim": "mock",
    "simulation": "mock",
}


def normalize_source_kind(kind: str) -> str:
    """Map accepted aliases to canonical kind strings (webcam/leap/mock/auto/websocket)."""
    return _KIND_ALIASES.get(kind, kind)


def create_source(kind: str = "auto", camera_index: int = 0,
                  flip_handedness: bool = False, clip_path: str = "") -> "MotionSource":
    """Create a motion-tracking source.

    Args:
        kind: "leap", "webcam" (alias "mediapipe"), "mock" (alias "sim"),
            "websocket", or "auto" (tries leap -> mock).
        camera_index: webcam index for the "webcam" kind.
        flip_handedness: swap left/right for "webcam" (webcams mirror).

    Returns:
        MotionSource instance.

    Raises:
        SensorError: When kind is "auto" and no source is found (contains diagnostics).
    """
    mode = normalize_source_kind(kind)
    if mode == "replay":
        log.info("Replay-Modus angefordert (%s)", clip_path)
        from capture.replay_source import ReplaySource
        return ReplaySource(clip_path)

    if mode == "mock":
        log.info("Mock-Modus angefordert")
        from capture.mock_capture import SimulationSource
        return SimulationSource()

    if mode == "webcam":
        log.info("Webcam-Modus angefordert (Kamera %d)", camera_index)
        from capture.mediapipe_capture import WebcamSource
        return WebcamSource(camera_index=camera_index,
                                      flip_handedness=flip_handedness)

    if mode == "leap":
        log.info("Leap-Modus angefordert")
        from capture.leap_capture import LeapSource
        return LeapSource()

    if mode == "websocket":
        log.info("WebSocket-Modus angefordert")
        from capture.websocket_capture import WebSocketCaptureDevice
        return WebSocketCaptureDevice()

    # auto: try leap -> mock fallback with diagnostics
    try:
        from capture.leap_capture import LeapSource
        device = LeapSource()
        device.connect()
        log.info("Leap Motion Controller erfolgreich verbunden")
        return device
    except Exception as leap_err:
        log.warning("Leap-Verbindung fehlgeschlagen: %s: %s", type(leap_err).__name__, leap_err)

    # Sensor not found — run diagnostics
    issues = diagnose_sensor()
    if issues:
        for issue in issues:
            log.warning("Sensor-Problem: %s", issue.split('\n')[0])
    from capture.mock_capture import SimulationSource
    device = SimulationSource()
    device._sensor_issues = issues  # attach diagnostics for the UI to display
    log.info("Fallback auf Simulationsmodus (SimulationSource)")
    return device


