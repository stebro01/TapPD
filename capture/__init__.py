"""Capture device factory with sensor diagnostics."""

import logging
import os
import sys
import subprocess
import shutil

from capture.base_capture import BaseCaptureDevice

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


def create_capture_device(mode: str = "auto") -> BaseCaptureDevice:
    """Create a capture device.

    Args:
        mode: "mock", "leap", "websocket", or "auto" (tries leap -> websocket -> mock).

    Returns:
        BaseCaptureDevice instance.

    Raises:
        SensorError: When mode is "auto" and no sensor is found (contains diagnostic info).
    """
    if mode == "mock":
        log.info("Mock-Modus angefordert")
        from capture.mock_capture import MockCaptureDevice
        return MockCaptureDevice()

    if mode == "leap":
        log.info("Leap-Modus angefordert")
        from capture.leap_capture import LeapCaptureDevice
        return LeapCaptureDevice()

    if mode == "websocket":
        log.info("WebSocket-Modus angefordert")
        from capture.websocket_capture import WebSocketCaptureDevice
        return WebSocketCaptureDevice()

    # auto: try leap -> mock fallback with diagnostics
    try:
        from capture.leap_capture import LeapCaptureDevice
        device = LeapCaptureDevice()
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
    from capture.mock_capture import MockCaptureDevice
    device = MockCaptureDevice()
    device._sensor_issues = issues  # attach diagnostics for the UI to display
    log.info("Fallback auf Simulationsmodus (MockCaptureDevice)")
    return device
