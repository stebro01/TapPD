"""Classify the active capture source.

Lets the UI adapt prompts to how the patient actually presents their hands —
*over* a Leap Motion sensor lying on the desk, versus *in front of* a webcam.
Kept separate from the device classes so any layer can ask "what kind of source
is this?" without importing the concrete implementations everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from capture.base_capture import BaseCaptureDevice, HandFrame

LEAP = "leap"
WEBCAM = "webcam"
MOCK = "mock"

# Leap: a hand counts as "in position" once it is held this far above the
# sensor (mm).  This is what the spatial screens checked individually as
# ``palm_position[1] > 120``.  Webcams have no such absolute reference.
READY_Y_MM = 120.0
READY_MIN_CONFIDENCE = 0.5

# ── Capability tokens ──────────────────────────────────────────────
# What a source can deliver / what a task needs.  Tasks declare their needs in
# test_config.yaml (`requires:`); sources declare what they provide below.
CAP_FINGERTIPS = "fingertips"        # relative fingertip positions
CAP_FINGER_FLEXION = "finger_flexion"  # per-joint flexion angles
CAP_HAND_POSE = "hand_pose"          # palm orientation (roll/pitch/yaw)
CAP_ABS_POSITION = "abs_position"    # absolute hand position in space (mm)
CAP_FOREARM = "forearm"              # forearm/elbow — no source provides this yet

# MediaPipe-Hand world landmarks are hand-relative (origin at the hand centre),
# so the webcam source cannot deliver absolute position; the Leap (and the mock,
# for development) can.  Forearm is provided by nobody until Pose/`hand.arm` lands.
_SOURCE_CAPS = {
    LEAP: {CAP_FINGERTIPS, CAP_FINGER_FLEXION, CAP_HAND_POSE, CAP_ABS_POSITION},
    WEBCAM: {CAP_FINGERTIPS, CAP_FINGER_FLEXION, CAP_HAND_POSE},
    MOCK: {CAP_FINGERTIPS, CAP_FINGER_FLEXION, CAP_HAND_POSE, CAP_ABS_POSITION},
}

# Human-readable reason shown when a capability is missing.
CAP_LABELS = {
    CAP_ABS_POSITION: "absolute Handposition im Raum",
    CAP_FOREARM: "Unterarm-/Ellbogen-Tracking",
    CAP_HAND_POSE: "Hand-Orientierung",
    CAP_FINGERTIPS: "Fingerspitzen-Tracking",
    CAP_FINGER_FLEXION: "Fingergelenk-Winkel",
}


def source_kind(device: BaseCaptureDevice | None) -> str:
    """Return "leap", "webcam" or "mock" for the given capture device."""
    # Imported lazily to avoid import cycles / loading the Leap binding early.
    from capture.mediapipe_capture import MediaPipeCaptureDevice
    from capture.mock_capture import MockCaptureDevice

    if isinstance(device, MediaPipeCaptureDevice):
        return WEBCAM
    if isinstance(device, MockCaptureDevice):
        return MOCK
    return LEAP


def source_capabilities(kind_or_device) -> set[str]:
    """Capabilities a source provides. Accepts a kind string or a device."""
    kind = kind_or_device if isinstance(kind_or_device, str) else source_kind(kind_or_device)
    return set(_SOURCE_CAPS.get(kind, _SOURCE_CAPS[LEAP]))


# ── Source abstraction layer ───────────────────────────────────────

@dataclass
class SourceProfile:
    """Everything paradigm code needs to know about *this* capture source.

    One object bundles the source identity, its capabilities, the per-source
    frame transform (the re-mapping seam), and the "is the hand ready to start?"
    policy — so the pre-test gate and the tests stay source-agnostic.
    """

    kind: str
    capabilities: set[str]
    min_confidence: float = READY_MIN_CONFIDENCE

    # ── frame re-mapping seam ──────────────────────────────────────
    def adapt_frame(self, frame: HandFrame) -> HandFrame:
        """Normalize a raw frame into the form paradigms expect.

        Leap/mock already deliver absolute mm, so this is identity.  Webcam
        frames are hand-relative; today this is also identity (the absolute
        position is simply absent, which is why tremor/spatial tasks are gated).

        This is the single place a future webcam absolute-position proxy
        (synthesised from MediaPipe image landmarks) would plug in — no
        paradigm code would need to change.
        """
        if self.kind == WEBCAM:
            # TODO: absolute-position proxy from image landmarks (deferred).
            return frame
        return frame

    # ── pre-test readiness policy ──────────────────────────────────
    def hand_ready(self, frame: HandFrame | None) -> bool:
        """Whether a frame shows a hand presented well enough to start.

        Leap: hand held above the sensor (absolute Y).  Webcam/mock: a hand is
        simply present and tracked confidently.
        """
        if frame is None or frame.confidence < self.min_confidence:
            return False
        if self.kind == LEAP:
            return frame.palm_position[1] > READY_Y_MM
        return True

    def prompts(self) -> dict[str, str]:
        """Source-aware hand-detection prompts (waiting / detected / timeout)."""
        from motor_tests.config import get_hand_detection_messages
        return get_hand_detection_messages(self.kind)


def profile_for(device: BaseCaptureDevice | None) -> SourceProfile:
    """Build the SourceProfile for a capture device."""
    kind = source_kind(device)
    return SourceProfile(kind=kind, capabilities=source_capabilities(kind))
