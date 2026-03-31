"""Leap Motion capture device using LeapC API via CFFI bindings."""

import logging
import os
import sys
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

from capture.base_capture import BaseCaptureDevice, BoneData, FingerData, HandFrame

# Add leapc_cffi to path and set library path
_LEAPC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "leapc_cffi")
if _LEAPC_DIR not in sys.path:
    sys.path.insert(0, _LEAPC_DIR)
if sys.platform == "win32":
    os.environ["PATH"] = _LEAPC_DIR + os.pathsep + os.environ.get("PATH", "")
else:
    os.environ.setdefault("DYLD_LIBRARY_PATH", _LEAPC_DIR)

from _leapc_cffi import ffi, lib as libleapc  # noqa: E402


class LeapCaptureDevice(BaseCaptureDevice):
    """Capture device using the native LeapC API (Ultraleap Gemini V5)."""

    def __init__(self) -> None:
        self._conn = None
        self._connected = False
        self._recording = False
        self._callback: Callable[[HandFrame], None] | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._current_fps = 120.0

    def connect(self) -> None:
        log.info("Verbinde mit Leap Motion Controller...")
        ppconn = ffi.new("LEAP_CONNECTION*")
        result = libleapc.LeapCreateConnection(ffi.NULL, ppconn)
        if result != 0:
            log.error("LeapCreateConnection fehlgeschlagen: %d", result)
            raise RuntimeError(f"LeapCreateConnection failed: {result}")
        self._conn = ppconn[0]

        result = libleapc.LeapOpenConnection(self._conn)
        if result != 0:
            log.error("LeapOpenConnection fehlgeschlagen: %d", result)
            raise RuntimeError(f"LeapOpenConnection failed: {result}")

        # Wait for connection + device
        event = ffi.new("LEAP_CONNECTION_MESSAGE*")
        deadline = time.time() + 5.0
        device_found = False
        while time.time() < deadline:
            result = libleapc.LeapPollConnection(self._conn, 500, event)
            if result == 0:
                if event.type == libleapc.eLeapEventType_Device:
                    device_found = True
                    break
                if event.type == libleapc.eLeapEventType_Tracking:
                    device_found = True
                    break
        if not device_found:
            log.error("Kein Leap Motion Geraet innerhalb von 5 Sekunden gefunden")
            raise RuntimeError("No Leap Motion device found within 5 seconds.")
        self._connected = True
        log.info("Leap Motion Controller verbunden")

    def disconnect(self) -> None:
        log.info("Trenne Leap Motion Controller...")
        self.stop_recording()
        if self._conn is not None:
            libleapc.LeapCloseConnection(self._conn)
            libleapc.LeapDestroyConnection(self._conn)
            self._conn = None
        self._connected = False
        log.info("Leap Motion Controller getrennt")

    def is_connected(self) -> bool:
        return self._connected

    @property
    def sample_rate(self) -> float:
        return self._current_fps

    def start_recording(self, callback: Callable[[HandFrame], None]) -> None:
        if not self._connected:
            self.connect()
        self._callback = callback
        self._recording = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        log.debug("Leap-Aufnahme gestartet (%.0f Hz)", self._current_fps)

    def stop_recording(self) -> None:
        self._recording = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        log.debug("Leap-Aufnahme gestoppt")

    def _poll_loop(self) -> None:
        event = ffi.new("LEAP_CONNECTION_MESSAGE*")
        while not self._stop_event.is_set():
            result = libleapc.LeapPollConnection(self._conn, 100, event)
            if result != 0:
                continue
            if event.type == libleapc.eLeapEventType_Tracking:
                self._handle_tracking(event.tracking_event)

    def _handle_tracking(self, tracking) -> None:
        self._current_fps = tracking.framerate
        for i in range(tracking.nHands):
            hand = tracking.pHands[i]
            frame = self._convert_hand(hand, tracking.info.timestamp)
            if self._callback:
                self._callback(frame)

    @staticmethod
    def _convert_hand(hand, timestamp_us: int) -> HandFrame:
        palm = hand.palm
        hand_type = "left" if hand.type == libleapc.eLeapHandType_Left else "right"

        fingers = []
        for f_idx in range(5):
            digit = hand.digits[f_idx]
            # Get tip position from distal bone next_joint
            tip = digit.distal.next_joint
            tip_pos = (tip.x, tip.y, tip.z)

            # Check if finger is extended
            is_extended = bool(digit.is_extended)

            # Collect bones (metacarpal, proximal, intermediate, distal)
            bones = []
            for bone in [digit.metacarpal, digit.proximal, digit.intermediate, digit.distal]:
                prev = bone.prev_joint
                nxt = bone.next_joint
                bones.append(BoneData(
                    prev_joint=(prev.x, prev.y, prev.z),
                    next_joint=(nxt.x, nxt.y, nxt.z),
                ))

            fingers.append(FingerData(
                finger_id=f_idx,
                tip_position=tip_pos,
                is_extended=is_extended,
                bones=bones,
            ))

        normal = palm.normal
        return HandFrame(
            timestamp_us=timestamp_us,
            hand_type=hand_type,
            palm_position=(palm.position.x, palm.position.y, palm.position.z),
            palm_velocity=(palm.velocity.x, palm.velocity.y, palm.velocity.z),
            palm_normal=(normal.x, normal.y, normal.z),
            fingers=fingers,
            pinch_distance=hand.pinch_distance,
            grab_strength=hand.grab_strength,
            confidence=hand.confidence,
        )
