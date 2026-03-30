"""Simulated capture device generating realistic hand movement data at 120 Hz."""

import math
import threading
import time
from typing import Callable

import numpy as np

from capture.base_capture import BaseCaptureDevice, BoneData, FingerData, HandFrame


class MockCaptureDevice(BaseCaptureDevice):
    """Generates simulated HandFrame data in a background thread.

    Modes: "tapping", "open_close", "pronation_supination",
           "postural_tremor", "rest_tremor", "idle"

    Bilateral modes (postural_tremor, rest_tremor) emit frames
    for BOTH left and right hands each tick.
    """

    SAMPLE_RATE = 120.0

    # Bilateral modes emit both hands per frame
    BILATERAL_MODES = {"postural_tremor", "rest_tremor"}

    def __init__(self, mode: str = "idle"):
        self._mode = mode
        self._connected = False
        self._recording = False
        self._callback: Callable[[HandFrame], None] | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        self._mode = value

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self.stop_recording()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    @property
    def sample_rate(self) -> float:
        return self.SAMPLE_RATE

    def start_recording(self, callback: Callable[[HandFrame], None]) -> None:
        if not self._connected:
            self.connect()
        self._callback = callback
        self._recording = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._generate_loop, daemon=True)
        self._thread.start()

    def stop_recording(self) -> None:
        self._recording = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _generate_loop(self) -> None:
        interval = 1.0 / self.SAMPLE_RATE
        start_time = time.perf_counter()
        frame_index = 0

        while not self._stop_event.is_set():
            t = frame_index * interval
            timestamp_us = int((start_time + t) * 1_000_000)

            frames = self._build_frames(t, timestamp_us)
            for frame in frames:
                if self._callback:
                    self._callback(frame)

            frame_index += 1
            next_time = start_time + frame_index * interval
            sleep_dur = next_time - time.perf_counter()
            if sleep_dur > 0:
                time.sleep(sleep_dur)

    def _build_frames(self, t: float, timestamp_us: int) -> list[HandFrame]:
        """Build one or two frames depending on mode."""
        if self._mode == "tapping":
            return [self._build_tapping_frame(t, timestamp_us)]
        elif self._mode == "open_close":
            return [self._build_open_close_frame(t, timestamp_us)]
        elif self._mode == "pronation_supination":
            return [self._build_pronation_supination_frame(t, timestamp_us)]
        elif self._mode == "postural_tremor":
            return self._build_bilateral_tremor(t, timestamp_us, rest=False)
        elif self._mode == "rest_tremor":
            return self._build_bilateral_tremor(t, timestamp_us, rest=True)
        elif self._mode == "tower_of_hanoi":
            return self._build_hanoi_frames(t, timestamp_us)
        elif self._mode == "spatial_srt":
            return [self._build_srt_frame(t, timestamp_us)]
        elif self._mode == "trail_making":
            return [self._build_tmt_frame(t, timestamp_us)]
        else:
            return [self._build_idle_frame(t, timestamp_us)]

    # ── Finger Tapping ──────────────────────────────────────────────

    def _build_tapping_frame(self, t: float, timestamp_us: int) -> HandFrame:
        freq = 3.0
        base_amplitude = 40.0
        decrement = max(0.3, 1.0 - 0.05 * t)
        amplitude = base_amplitude * decrement
        noise = np.random.normal(0, 0.5)
        distance = amplitude * (0.5 + 0.5 * math.sin(2 * math.pi * freq * t)) + noise

        palm = (0.0, 200.0, 0.0)
        thumb_tip = (distance * 0.5, 200.0, 30.0)
        index_tip = (-distance * 0.5, 200.0, 30.0)

        return HandFrame(
            timestamp_us=timestamp_us,
            hand_type="right",
            palm_position=palm,
            palm_velocity=(0.0, 0.0, 0.0),
            palm_normal=(0.0, -1.0, 0.0),
            fingers=self._make_fingers(thumb_tip, index_tip, 200.0, 40.0),
            pinch_distance=distance,
            confidence=1.0,
        )

    # ── Hand Open/Close ─────────────────────────────────────────────

    def _build_open_close_frame(self, t: float, timestamp_us: int) -> HandFrame:
        freq = 1.5
        decrement = max(0.4, 1.0 - 0.04 * t)
        # open_frac: 0.0 = fist closed, 1.0 = fully open
        open_frac = decrement * (0.5 + 0.5 * math.sin(2 * math.pi * freq * t))
        noise = np.random.normal(0, 0.5)

        # Fingertip distances from palm: ~30mm closed → ~100mm open
        min_ext = 30.0  # fist: tips near palm
        max_ext = 100.0  # open: tips far from palm
        extension = min_ext + (max_ext - min_ext) * open_frac + noise
        extension = max(min_ext, extension)

        palm = (0.0, 200.0, 0.0)
        # Fingers extend primarily in Z, with lateral spread when open
        lateral = extension * 0.12 * open_frac  # lateral spread only when open
        fingers = self._make_open_close_fingers(palm, extension, lateral)

        return HandFrame(
            timestamp_us=timestamp_us,
            hand_type="right",
            palm_position=palm,
            palm_velocity=(0.0, 0.0, 0.0),
            palm_normal=(0.0, -1.0, 0.0),
            fingers=fingers,
            pinch_distance=extension,
            grab_strength=max(0.0, 1.0 - open_frac),
            confidence=1.0,
        )

    @staticmethod
    def _make_open_close_fingers(
        palm: tuple[float, float, float],
        extension: float,
        lateral: float,
    ) -> list[FingerData]:
        """Create fingers whose tip distance from palm matches extension."""
        px, py, pz = palm
        # Each finger extends in Z with slight lateral offset
        offsets = [
            (lateral * 2.0, 0.0),   # thumb: lateral
            (lateral * 0.8, 0.0),   # index
            (0.0, 0.0),             # middle: straight
            (-lateral * 0.8, 0.0),  # ring
            (-lateral * 1.5, 0.0),  # pinky
        ]
        # Extension factors per finger (pinky slightly shorter)
        ext_factors = [0.85, 1.0, 1.0, 0.95, 0.85]
        fingers = []
        for i, ((dx, dy), ef) in enumerate(zip(offsets, ext_factors)):
            ext = extension * ef
            tip = (px + dx, py + dy, pz + ext)
            bones = [BoneData(prev_joint=palm, next_joint=tip)]
            fingers.append(FingerData(
                finger_id=i,
                tip_position=tip,
                is_extended=extension > 50.0,
                bones=bones,
            ))
        return fingers

    # ── Pronation / Supination ──────────────────────────────────────

    def _build_pronation_supination_frame(self, t: float, timestamp_us: int) -> HandFrame:
        """Palm normal rotates around the Z-axis at ~1.5 Hz, simulating forearm rotation."""
        freq = 1.5
        base_angle = math.pi / 3  # ±60 degrees
        decrement = max(0.4, 1.0 - 0.04 * t)
        angle = base_angle * decrement * math.sin(2 * math.pi * freq * t)
        angle += np.random.normal(0, 0.02)

        # Palm normal rotates in the XY plane
        nx = math.sin(angle)
        ny = -math.cos(angle)

        # Finger tips rotate accordingly
        palm_y = 200.0
        r = 60.0  # hand radius
        thumb_tip = (r * 0.5 * math.cos(angle + 0.3), palm_y, r * 0.5 * math.sin(angle + 0.3) + 40)
        index_tip = (r * 0.3 * math.cos(angle - 0.2), palm_y, r * 0.8 * math.sin(angle - 0.2) + 60)

        return HandFrame(
            timestamp_us=timestamp_us,
            hand_type="right",
            palm_position=(0.0, palm_y, 0.0),
            palm_velocity=(0.0, 0.0, 0.0),
            palm_normal=(nx, ny, 0.0),
            fingers=self._make_fingers(thumb_tip, index_tip, palm_y, 50.0),
            pinch_distance=40.0,
            confidence=1.0,
        )

    # ── Bilateral Tremor ────────────────────────────────────────────

    def _build_bilateral_tremor(
        self, t: float, timestamp_us: int, rest: bool
    ) -> list[HandFrame]:
        """Generate tremor frames for both hands.

        Right hand: stronger tremor (affected side simulation).
        Left hand:  weaker tremor.
        rest=True:  lower baseline, pure rest tremor (4-5 Hz)
        rest=False: postural position, slightly higher freq (5-7 Hz)
        """
        frames = []
        for hand_type in ("right", "left"):
            is_right = hand_type == "right"

            if rest:
                freq = 4.5 if is_right else 4.8
                amp = 2.0 if is_right else 0.5  # asymmetry!
                base_y = 150.0  # hands lower (resting)
            else:
                freq = 5.5 if is_right else 5.8
                amp = 1.5 if is_right else 0.4
                base_y = 200.0  # hands extended

            # Translational tremor (flexion-extension)
            noise = np.random.normal(0, 0.2)
            px = amp * math.sin(2 * math.pi * freq * t) + noise
            py = base_y + amp * 0.3 * math.sin(2 * math.pi * freq * t + 0.5) + noise * 0.3
            pz = amp * 0.2 * math.cos(2 * math.pi * freq * t) + noise * 0.2

            # Rotational tremor (pronation-supination component)
            rot_amp = 0.15 if is_right else 0.04  # radians
            rot_angle = rot_amp * math.sin(2 * math.pi * freq * t + 0.8)
            nx = math.sin(rot_angle)
            ny = -math.cos(rot_angle)

            # Offset left hand spatially
            x_offset = -80.0 if hand_type == "left" else 80.0
            px += x_offset

            frames.append(HandFrame(
                timestamp_us=timestamp_us,
                hand_type=hand_type,
                palm_position=(px, py, pz),
                palm_velocity=(
                    amp * 2 * math.pi * freq * math.cos(2 * math.pi * freq * t),
                    0.0, 0.0,
                ),
                palm_normal=(nx, ny, 0.0),
                fingers=self._make_fingers(
                    (px + 30, py, pz + 40),
                    (px + 10, py, pz + 80),
                    py, 60.0,
                ),
                pinch_distance=30.0,
                confidence=1.0,
            ))
        return frames

    # ── Tower of Hanoi ─────────────────────────────────────────────

    def _build_hanoi_frames(self, t: float, timestamp_us: int) -> list[HandFrame]:
        """Simulate a right hand sweeping between pegs with periodic pinch.

        Scripted: 3-disc optimal solution (7 moves).
        Each move takes ~3s: 1s grab, 1s move, 1s release.
        """
        # Peg X positions in mm (Leap coord space)
        PEG_X = [-100.0, 0.0, 100.0]

        # Optimal 3-disc moves: (from_peg, to_peg)
        MOVES = [(0, 2), (0, 1), (2, 1), (0, 2), (1, 0), (1, 2), (0, 2)]
        MOVE_DURATION = 3.0  # seconds per move
        PLANNING = 2.0  # initial planning pause

        total_move_time = len(MOVES) * MOVE_DURATION + PLANNING

        # Right hand: interactive hand
        if t < PLANNING:
            # Planning: hover over peg 0
            hand_x = PEG_X[0]
            pinch_dist = 50.0
        elif t < total_move_time:
            move_t = t - PLANNING
            move_idx = min(int(move_t / MOVE_DURATION), len(MOVES) - 1)
            phase = (move_t % MOVE_DURATION) / MOVE_DURATION  # 0-1
            src_x = PEG_X[MOVES[move_idx][0]]
            dst_x = PEG_X[MOVES[move_idx][1]]

            if phase < 0.3:
                # Approaching & grabbing
                hand_x = src_x
                pinch_dist = 50.0 - 40.0 * (phase / 0.3)  # 50→10
            elif phase < 0.7:
                # Moving with disc
                lerp = (phase - 0.3) / 0.4
                hand_x = src_x + (dst_x - src_x) * lerp
                pinch_dist = 10.0  # holding
            else:
                # Releasing
                hand_x = dst_x
                pinch_dist = 10.0 + 40.0 * ((phase - 0.7) / 0.3)  # 10→50
        else:
            # Done: hover
            hand_x = PEG_X[2]
            pinch_dist = 50.0

        noise = np.random.normal(0, 0.3)
        hand_x += noise

        noise_y = np.random.normal(0, 0.2)
        noise_z = np.random.normal(0, 0.2)
        return [HandFrame(
            timestamp_us=timestamp_us,
            hand_type="right",
            palm_position=(hand_x, 200.0 + noise_y, noise_z),
            palm_velocity=(0.0, 0.0, 0.0),
            palm_normal=(0.0, -1.0, 0.0),
            fingers=self._make_fingers(
                (hand_x + 20, 200.0, 30.0),
                (hand_x + 5, 200.0, 70.0),
                200.0, 60.0,
            ),
            pinch_distance=pinch_dist,
            confidence=1.0,
        )]

    # ── Spatial SRT ─────────────────────────────────────────────────

    def _build_srt_frame(self, t: float, timestamp_us: int) -> HandFrame:
        """Simulate hand reaching between 4 spatial targets."""
        # Target positions in Leap coords (X, Z) — Y is height
        TARGETS = [(0.0, -80.0), (160.0, 0.0), (0.0, 80.0), (-160.0, 0.0)]
        # Scripted target sequence (repeating)
        SEQ = [0, 2, 1, 3, 0, 3, 2, 1, 0, 2, 3, 1]
        TRIAL_DUR = 1.8  # seconds per trial

        trial_idx = int(t / TRIAL_DUR) % len(SEQ)
        phase = (t % TRIAL_DUR) / TRIAL_DUR  # 0..1

        src = TARGETS[SEQ[(trial_idx - 1) % len(SEQ)]]
        dst = TARGETS[SEQ[trial_idx]]

        if phase < 0.2:
            # Reaction time — stay at previous target
            hand_x, hand_z = src
            vel_x, vel_z = 0.0, 0.0
        elif phase < 0.7:
            # Movement phase — smooth interpolation
            lerp = (phase - 0.2) / 0.5
            smooth = 0.5 - 0.5 * np.cos(np.pi * lerp)  # ease-in-out
            hand_x = src[0] + (dst[0] - src[0]) * smooth
            hand_z = src[1] + (dst[1] - src[1]) * smooth
            vel_x = (dst[0] - src[0]) * 2.0
            vel_z = (dst[1] - src[1]) * 2.0
        else:
            # Dwell at target
            hand_x, hand_z = dst
            vel_x, vel_z = 0.0, 0.0

        hand_x += np.random.normal(0, 0.5)
        hand_z += np.random.normal(0, 0.5)

        return HandFrame(
            timestamp_us=timestamp_us,
            hand_type="right",
            palm_position=(hand_x, 200.0 + np.random.normal(0, 0.2), hand_z),
            palm_velocity=(vel_x, 0.0, vel_z),
            palm_normal=(0.0, -1.0, 0.0),
            fingers=self._make_fingers(
                (hand_x + 20, 200.0, hand_z + 30),
                (hand_x + 5, 200.0, hand_z + 70),
                200.0, 60.0,
            ),
            pinch_distance=50.0,
            confidence=1.0,
        )

    # ── Trail Making ─────────────────────────────────────────────

    def _build_tmt_frame(self, t: float, timestamp_us: int) -> HandFrame:
        """Simulate hand moving between TMT target positions."""
        # Scripted path through positions in normalized coords, mapped to Leap mm
        POSITIONS = [
            (0.3, 0.2), (0.7, 0.3), (0.5, 0.5), (0.2, 0.7),
            (0.8, 0.6), (0.4, 0.8), (0.6, 0.2), (0.3, 0.4),
        ]
        SEGMENT_DUR = 2.0

        seg_idx = int(t / SEGMENT_DUR) % len(POSITIONS)
        phase = (t % SEGMENT_DUR) / SEGMENT_DUR

        src = POSITIONS[(seg_idx - 1) % len(POSITIONS)]
        dst = POSITIONS[seg_idx]

        if phase < 0.15:
            nx, ny = src
            vel_x, vel_z = 0.0, 0.0
        elif phase < 0.7:
            lerp = (phase - 0.15) / 0.55
            smooth = 0.5 - 0.5 * np.cos(np.pi * lerp)
            nx = src[0] + (dst[0] - src[0]) * smooth
            ny = src[1] + (dst[1] - src[1]) * smooth
            vel_x = (dst[0] - src[0]) * 400.0 * 1.5
            vel_z = (dst[1] - src[1]) * 200.0 * 1.5
        else:
            nx, ny = dst
            vel_x, vel_z = 0.0, 0.0

        # Normalized to Leap mm: X: 0..1 → -200..+200, Z: 0..1 → -100..+100
        hand_x = (nx - 0.5) * 400.0 + np.random.normal(0, 0.5)
        hand_z = (ny - 0.5) * 200.0 + np.random.normal(0, 0.5)

        return HandFrame(
            timestamp_us=timestamp_us,
            hand_type="right",
            palm_position=(hand_x, 200.0 + np.random.normal(0, 0.2), hand_z),
            palm_velocity=(vel_x, 0.0, vel_z),
            palm_normal=(0.0, -1.0, 0.0),
            fingers=self._make_fingers(
                (hand_x + 20, 200.0, hand_z + 30),
                (hand_x + 5, 200.0, hand_z + 70),
                200.0, 60.0,
            ),
            pinch_distance=50.0,
            confidence=1.0,
        )

    # ── Idle ────────────────────────────────────────────────────────

    def _build_idle_frame(self, t: float, timestamp_us: int) -> HandFrame:
        noise = np.random.normal(0, 0.1, 3)
        return HandFrame(
            timestamp_us=timestamp_us,
            hand_type="right",
            palm_position=(noise[0], 200.0 + noise[1], noise[2]),
            palm_velocity=(0.0, 0.0, 0.0),
            palm_normal=(0.0, -1.0, 0.0),
            fingers=self._make_fingers((30.0, 200.0, 40.0), (10.0, 200.0, 80.0), 200.0, 60.0),
            pinch_distance=30.0,
            confidence=1.0,
        )

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _make_fingers(
        thumb_tip: tuple[float, float, float],
        index_tip: tuple[float, float, float],
        palm_y: float,
        spread: float,
    ) -> list[FingerData]:
        base_z = 30.0
        tips = [
            thumb_tip, index_tip,
            (-spread * 0.1, palm_y, base_z + 80),
            (-spread * 0.3, palm_y, base_z + 70),
            (-spread * 0.5, palm_y, base_z + 55),
        ]
        fingers = []
        for i, tip in enumerate(tips):
            is_extended = spread > 20.0 if i > 0 else True
            bones = [BoneData(prev_joint=(0.0, palm_y, 0.0), next_joint=tip)]
            fingers.append(FingerData(finger_id=i, tip_position=tip, is_extended=is_extended, bones=bones))
        return fingers
