"""Replay a recorded clip as a motion source.

A clip is a short real recording (e.g. 10 s from the webcam) saved as JSON. The
ReplaySource re-emits its HandPose frames at the original timing, looping — so a
real session becomes a deterministic simulation source, usable anywhere a live
source is (preview, paradigms, tests).

Clip format (see video.clip):
    {"meta": {"source_kind", "duration_s", "fps", "recorded_at"},
     "frames": [{"dt": <seconds from start>, "hand": <HandPose.to_dict()>}, ...]}
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable

from capture.base_capture import MotionSource, HandPose, HandFrame


class ReplaySource(MotionSource):
    def __init__(self, clip_path: str) -> None:
        self.clip_path = clip_path
        with open(clip_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        meta = data.get("meta", {})
        self._frames = data.get("frames", [])
        self._duration = float(meta.get("duration_s", 0.0))
        self._sample_rate = float(meta.get("fps", 30.0)) or 30.0
        self.source_kind_origin = meta.get("source_kind", "webcam")

        self._connected = False
        self._callback: Callable[[HandFrame], None] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ── MotionSource contract ─────────────────────────────────────
    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self.stop_recording()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    def start_recording(self, callback: Callable[[HandFrame], None]) -> None:
        if not self._connected:
            self.connect()
        self._callback = callback
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop_recording(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ── replay loop (loops the clip) ──────────────────────────────
    def _loop(self) -> None:
        if not self._frames:
            return
        while not self._stop.is_set():
            t0 = time.perf_counter()
            for entry in self._frames:
                if self._stop.is_set():
                    return
                target = t0 + float(entry.get("dt", 0.0))
                while not self._stop.is_set():
                    remaining = target - time.perf_counter()
                    if remaining <= 0:
                        break
                    time.sleep(min(remaining, 0.02))
                frame = HandPose.from_dict(entry["hand"])
                # Re-stamp with a continuous monotonic clock so timestamps keep
                # increasing across loop boundaries (paradigms read timestamp_us).
                frame.timestamp_us = int(time.perf_counter() * 1_000_000)
                if self._callback:
                    self._callback(frame)
