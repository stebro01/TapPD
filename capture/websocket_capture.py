"""WebSocket-based capture device (placeholder for ws://localhost:6437)."""

from typing import Callable

from capture.base_capture import BaseCaptureDevice, HandFrame


class WebSocketCaptureDevice(BaseCaptureDevice):
    """Connects to the Leap Motion WebSocket API at ws://localhost:6437/v7.json."""

    def __init__(self) -> None:
        self._connected = False

    def connect(self) -> None:
        raise NotImplementedError(
            "WebSocket capture not yet implemented. "
            "Use --mock flag for development."
        )

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def start_recording(self, callback: Callable[[HandFrame], None]) -> None:
        raise NotImplementedError("WebSocket capture not available.")

    def stop_recording(self) -> None:
        pass

    @property
    def sample_rate(self) -> float:
        return 120.0
