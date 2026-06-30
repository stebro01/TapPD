"""Map MediaPipe hand landmarks to TapPD ``HandFrame`` objects.

Pure functions, no MediaPipe/OpenCV import — runs in the main app's Python 3.14
environment and is unit-testable with plain dicts (the sidecar speaks JSON, see
``mediapipe_sidecar/PROTOCOL.md``).

MediaPipe gives 21 ``hand_world_landmarks`` in metres (origin at the hand's
geometric centre).  TapPD's ``HandFrame`` expects millimetres and a per-finger
bone structure (metacarpal, proximal, intermediate, distal).  The gesture-lab
feature extraction is scale/position-invariant (joint angles + normalized
distances), so the absolute origin/scale do not matter for recognition — we keep
mm purely so the existing visualization widget renders at a sensible size.

21-landmark index order (MediaPipe):
    0 wrist
    1-4   thumb : CMC, MCP, IP, TIP
    5-8   index : MCP, PIP, DIP, TIP
    9-12  middle: MCP, PIP, DIP, TIP
    13-16 ring  : MCP, PIP, DIP, TIP
    17-20 pinky : MCP, PIP, DIP, TIP
"""

from __future__ import annotations

import math

from capture.base_capture import BoneData, FingerData, HandFrame

M_TO_MM = 1000.0

WRIST = 0
# Per finger: the 4 landmark indices [MCP-ish, PIP/IP, DIP/TIP-1, TIP].
# Thumb has no PIP/DIP; we use CMC, MCP, IP, TIP which still yields 3 segments.
_FINGER_LANDMARKS = {
    0: [1, 2, 3, 4],      # thumb : CMC, MCP, IP, TIP
    1: [5, 6, 7, 8],      # index : MCP, PIP, DIP, TIP
    2: [9, 10, 11, 12],   # middle
    3: [13, 14, 15, 16],  # ring
    4: [17, 18, 19, 20],  # pinky
}
_MCP_INDICES = [1, 5, 9, 13, 17]  # thumb CMC + four finger MCPs


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _angle(a, b) -> float:
    na, nb = _norm(a), _norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    dot = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (na * nb)
    return math.acos(max(-1.0, min(1.0, dot)))


def _build_finger(finger_id: int, pts_mm: list[tuple[float, float, float]]) -> FingerData:
    idxs = _FINGER_LANDMARKS[finger_id]
    wrist = pts_mm[WRIST]
    mcp, pip, dip, tip = (pts_mm[i] for i in idxs)

    # 4 bones: metacarpal (wrist->MCP), proximal (MCP->PIP), intermediate
    # (PIP->DIP), distal (DIP->TIP).
    bones = [
        BoneData(prev_joint=wrist, next_joint=mcp),
        BoneData(prev_joint=mcp, next_joint=pip),
        BoneData(prev_joint=pip, next_joint=dip),
        BoneData(prev_joint=dip, next_joint=tip),
    ]

    # Extended heuristic: total flexion across the finger is small.
    flex = (_angle(_sub(mcp, wrist), _sub(pip, mcp))
            + _angle(_sub(pip, mcp), _sub(dip, pip))
            + _angle(_sub(dip, pip), _sub(tip, dip)))
    is_extended = flex < 1.2  # radians (~69°) total curl

    return FingerData(
        finger_id=finger_id,
        tip_position=tip,
        is_extended=is_extended,
        bones=bones,
    )


def hand_from_world(world: list, handedness: str, score: float,
                    timestamp_us: int, flip_handedness: bool = False,
                    prev: HandFrame | None = None,
                    dt: float = 1.0 / 30.0) -> HandFrame | None:
    """Build a HandFrame from 21 world landmarks (metres) of one hand.

    ``prev`` (the previous frame for this hand) is used for palm velocity.
    Returns None if the landmark list is malformed.
    """
    if not world or len(world) < 21:
        return None

    pts = [(p[0] * M_TO_MM, p[1] * M_TO_MM, p[2] * M_TO_MM) for p in world]

    hand_type = (handedness or "Right").lower()
    if flip_handedness:
        hand_type = "left" if hand_type == "right" else "right"
    if hand_type not in ("left", "right"):
        hand_type = "right"

    fingers = [_build_finger(fid, pts) for fid in range(5)]

    # Palm position: centroid of wrist + the five MCP-ish joints.
    anchor = [pts[WRIST]] + [pts[i] for i in _MCP_INDICES]
    palm = (sum(p[0] for p in anchor) / len(anchor),
            sum(p[1] for p in anchor) / len(anchor),
            sum(p[2] for p in anchor) / len(anchor))

    # Palm normal: plane through wrist, index MCP (5), pinky MCP (17).
    n = _cross(_sub(pts[5], pts[WRIST]), _sub(pts[17], pts[WRIST]))
    nn = _norm(n)
    palm_normal = (n[0] / nn, n[1] / nn, n[2] / nn) if nn > 1e-9 else (0.0, -1.0, 0.0)

    # Palm velocity from previous frame (mm/s).
    if prev is not None and dt > 1e-6:
        pv = ((palm[0] - prev.palm_position[0]) / dt,
              (palm[1] - prev.palm_position[1]) / dt,
              (palm[2] - prev.palm_position[2]) / dt)
    else:
        pv = (0.0, 0.0, 0.0)

    # Grab strength: fraction of non-thumb fingers that are curled.
    curled = sum(0 if f.is_extended else 1 for f in fingers[1:])
    grab_strength = curled / 4.0

    # Pinch distance: thumb tip to index tip (mm).
    pinch = _norm(_sub(fingers[0].tip_position, fingers[1].tip_position))

    return HandFrame(
        timestamp_us=timestamp_us,
        hand_type=hand_type,
        palm_position=palm,
        palm_velocity=pv,
        palm_normal=palm_normal,
        fingers=fingers,
        pinch_distance=pinch,
        grab_strength=grab_strength,
        confidence=float(score),
    )


def frames_from_message(msg: dict, flip_handedness: bool = False,
                        prev_by_hand: dict | None = None) -> list[HandFrame]:
    """Convert one ``{"type":"hand", ...}`` sidecar message to HandFrames.

    ``prev_by_hand`` maps hand_type -> previous HandFrame and is updated in place
    so successive calls produce palm velocities.
    """
    if msg.get("type") != "hand":
        return []
    ts = int(msg.get("ts", 0))
    out: list[HandFrame] = []
    for hand in msg.get("hands", []):
        prev = (prev_by_hand or {}).get((hand.get("handedness") or "Right").lower())
        frame = hand_from_world(
            hand.get("world", []),
            hand.get("handedness", "Right"),
            float(hand.get("score", 1.0)),
            ts,
            flip_handedness=flip_handedness,
            prev=prev,
        )
        if frame is not None:
            out.append(frame)
            if prev_by_hand is not None:
                prev_by_hand[frame.hand_type] = frame
    return out
