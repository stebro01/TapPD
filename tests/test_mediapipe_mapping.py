"""Tests for capture.mediapipe_mapping — converting sidecar MediaPipe
landmark messages into TapPD HandFrame objects.

Runs in the main app's Python 3.14 env (no MediaPipe needed): the sidecar
speaks JSON, so we feed synthetic messages matching mediapipe_sidecar/PROTOCOL.md.
"""

import math

from capture.base_capture import HandFrame
from capture.mediapipe_mapping import frames_from_message, hand_from_world


def _open_hand_world():
    """21 world landmarks (metres) for a roughly flat, open right hand.

    Layout in the x/y plane (z≈0): wrist at origin, fingers fanning out +y,
    thumb off to -x.  Index order matches PROTOCOL.md.
    """
    pts = [[0.0, 0.0, 0.0]]                      # 0 wrist
    # thumb 1-4 (CMC, MCP, IP, TIP) angled to -x
    pts += [[-0.02 * i, 0.02 * i, 0.0] for i in range(1, 5)]
    # four fingers, each MCP/PIP/DIP/TIP marching in +y at distinct x columns
    for col, base_x in zip(range(4), (-0.02, 0.0, 0.02, 0.04)):
        for j in range(1, 5):                    # 4 joints
            pts.append([base_x, 0.03 + 0.02 * j, 0.0])
    assert len(pts) == 21
    return pts


def test_hand_from_world_basic_shape():
    frame = hand_from_world(_open_hand_world(), "Right", 0.97, timestamp_us=1000)
    assert isinstance(frame, HandFrame)
    assert frame.hand_type == "right"
    assert frame.confidence == 0.97
    assert frame.timestamp_us == 1000
    # Exactly 5 fingers, 4 bones each
    assert len(frame.fingers) == 5
    for f in frame.fingers:
        assert len(f.bones) == 4
        assert all(len(b.prev_joint) == 3 and len(b.next_joint) == 3 for b in f.bones)
    # Finger ids 0..4 in order
    assert [f.finger_id for f in frame.fingers] == [0, 1, 2, 3, 4]


def test_scaling_metres_to_mm():
    # A tip at y=0.11 m should map to 110 mm.
    frame = hand_from_world(_open_hand_world(), "Right", 1.0, timestamp_us=0)
    index_tip = frame.fingers[1].tip_position
    assert math.isclose(index_tip[1], 110.0, rel_tol=1e-6)


def test_extended_fingers_detected_on_open_hand():
    frame = hand_from_world(_open_hand_world(), "Right", 1.0, timestamp_us=0)
    # The four fingers are straight columns → extended.
    assert all(frame.fingers[i].is_extended for i in (1, 2, 3, 4))
    # Open hand → low grab strength.
    assert frame.grab_strength < 0.25


def test_flip_handedness():
    frame = hand_from_world(_open_hand_world(), "Right", 1.0, 0, flip_handedness=True)
    assert frame.hand_type == "left"


def test_palm_velocity_from_previous_frame():
    w = _open_hand_world()
    f0 = hand_from_world(w, "Right", 1.0, timestamp_us=0)
    # Shift whole hand +x by 0.01 m (=10 mm); dt = 0.1 s → vx = 100 mm/s.
    w2 = [[p[0] + 0.01, p[1], p[2]] for p in w]
    f1 = hand_from_world(w2, "Right", 1.0, 0, prev=f0, dt=0.1)
    assert math.isclose(f1.palm_velocity[0], 100.0, rel_tol=1e-3)


def test_frames_from_message_two_hands_and_prev_tracking():
    prev: dict = {}
    msg = {
        "type": "hand", "ts": 5,
        "hands": [
            {"handedness": "Right", "score": 0.9, "world": _open_hand_world()},
            {"handedness": "Left", "score": 0.8, "world": _open_hand_world()},
        ],
    }
    frames = frames_from_message(msg, prev_by_hand=prev)
    assert len(frames) == 2
    assert {f.hand_type for f in frames} == {"left", "right"}
    # prev_by_hand populated for velocity on the next message
    assert set(prev.keys()) == {"left", "right"}


def test_malformed_inputs_are_skipped():
    assert hand_from_world([], "Right", 1.0, 0) is None
    assert hand_from_world([[0, 0, 0]] * 10, "Right", 1.0, 0) is None
    assert frames_from_message({"type": "preview"}) == []


def test_velocity_uses_real_frame_dt_not_fixed_30fps():
    """dt must come from the message timestamps — a 20 fps video would
    otherwise get velocities scaled by 30/20."""
    prev: dict = {}
    world = _open_hand_world()
    msg0 = {"type": "hand", "ts": 0,
            "hands": [{"handedness": "Right", "score": 0.9, "world": world}]}
    frames_from_message(msg0, prev_by_hand=prev)

    moved = [[x + 0.010, y, z] for x, y, z in world]   # +10 mm in x
    ts1 = 50_000                                        # 50 ms later → 20 fps
    msg1 = {"type": "hand", "ts": ts1,
            "hands": [{"handedness": "Right", "score": 0.9, "world": moved}]}
    (f1,) = frames_from_message(msg1, prev_by_hand=prev)
    # 10 mm / 0.05 s = 200 mm/s (with fixed 1/30 it would be 300 mm/s)
    assert math.isclose(f1.palm_velocity[0], 200.0, rel_tol=1e-6)
