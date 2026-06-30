"""Standard API contracts for the capture/source layer.

This module documents — as runtime-checkable ``Protocol``s and type aliases —
the interfaces every layer agrees on, so the hardware→paradigm flow has one
written contract. The concrete ABC lives in ``capture.base_capture.MotionSource``;
this is the structural (duck-typed) view plus the shared callback type.
"""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from capture.base_capture import HandFrame

# The per-frame consumer a source pushes to. One call per hand per frame, on a
# background thread (consumers must be thread-safe).
#
# Stage-2 target: ``Callable[[TrackingFrame], None]`` where a single TrackingFrame
# carries hands[] plus optional face/gaze — see ARCHITECTURE.md.
FrameConsumer = Callable[[HandFrame], None]


@runtime_checkable
class MotionSourceProtocol(Protocol):
    """Structural contract for a motion-tracking source."""

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...
    def start_recording(self, callback: FrameConsumer) -> None: ...
    def stop_recording(self) -> None: ...

    @property
    def sample_rate(self) -> float: ...
