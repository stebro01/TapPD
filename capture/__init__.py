"""Capture device factory with sensor diagnostics."""

import os
import subprocess
import shutil

from capture.base_capture import BaseCaptureDevice


def diagnose_sensor() -> list[str]:
    """Check SDK, USB device, and tracking service. Returns list of issues found."""
    issues = []

    # 1. Check if Ultraleap Tracking software is installed
    app_path = "/Applications/Ultraleap Hand Tracking.app"
    if not os.path.isdir(app_path):
        issues.append(
            "Ultraleap Hand Tracking Software nicht gefunden.\n"
            "  -> Bitte installieren: https://developer.leapmotion.com/tracking-software-download"
        )
    else:
        # Check if tracking server is running
        try:
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
        issues.append(
            "LeapC Python-Bindings nicht gefunden (leapc_cffi/ Verzeichnis fehlt).\n"
            "  -> Kopiere aus dem SDK: /Applications/Ultraleap Hand Tracking.app/Contents/LeapSDK/leapc_cffi/"
        )

    # 3. Check USB device
    try:
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
        from capture.mock_capture import MockCaptureDevice
        return MockCaptureDevice()

    if mode == "leap":
        from capture.leap_capture import LeapCaptureDevice
        return LeapCaptureDevice()

    if mode == "websocket":
        from capture.websocket_capture import WebSocketCaptureDevice
        return WebSocketCaptureDevice()

    # auto: try leap -> mock fallback with diagnostics
    try:
        from capture.leap_capture import LeapCaptureDevice
        device = LeapCaptureDevice()
        device.connect()
        return device
    except Exception as leap_err:
        pass

    # Sensor not found — run diagnostics
    issues = diagnose_sensor()
    from capture.mock_capture import MockCaptureDevice
    device = MockCaptureDevice()
    device._sensor_issues = issues  # attach diagnostics for the UI to display
    return device
