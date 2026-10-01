"""Sidecar logic, run under the sidecar's own Python (cv2 + mediapipe present).

    mediapipe_sidecar/.venv/Scripts/python -m pytest mediapipe_sidecar/tests

The main suite launches this via tests/test_sidecar_suite.py.
"""

import os
import random
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import cv2                         # noqa: E402
import extract                     # noqa: E402
import sidecar                     # noqa: E402


# ── handedness under mirroring ─────────────────────────────────────

@pytest.mark.parametrize("name,mirrored,expected", [
    ("Left", False, "Left"), ("Right", False, "Right"),
    ("Left", True, "Right"), ("Right", True, "Left"),
    (None, False, "Right"), (None, True, "Right"),      # fixed fallback, never flipped
    ("Weird", True, "Weird"),
])
def test_handedness_label(name, mirrored, expected):
    assert sidecar._handedness(name, mirrored) == expected


# ── backend choice ─────────────────────────────────────────────────

def test_camera_backend_per_platform():
    be = sidecar._camera_backend()
    if sys.platform == "win32":
        assert be == cv2.CAP_DSHOW          # MSMF takes seconds to open a device
    elif sys.platform == "darwin":
        assert be == cv2.CAP_AVFOUNDATION
    else:
        assert be == cv2.CAP_ANY


# ── enumeration ────────────────────────────────────────────────────

def test_list_cameras_uses_one_backend_and_plain_indices(monkeypatch):
    class Info:
        def __init__(self, i, n): self.index, self.name = i, n

    seen = {}

    def fake_enum(backend):
        seen["backend"] = backend
        return [Info(0, "USB Video Device"), Info(1, "OBSBOT Tiny 2")]

    import cv2_enumerate_cameras
    monkeypatch.setattr(cv2_enumerate_cameras, "enumerate_cameras", fake_enum)

    cams = sidecar.list_cameras()

    assert seen["backend"] == sidecar._camera_backend()
    assert cams == [{"index": 0, "name": "USB Video Device"},
                    {"index": 1, "name": "OBSBOT Tiny 2"}]


def test_list_cameras_falls_back_to_probing_when_enumeration_is_unavailable(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_enum(name, *a, **k):
        if name == "cv2_enumerate_cameras":
            raise ImportError("DLL load failed")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_enum)

    opened = []

    class Cap:
        def __init__(self, i, be): self.i = i; opened.append(i)
        def isOpened(self): return self.i in (0, 2)
        def release(self): opened.append(("released", self.i))
    monkeypatch.setattr(sidecar.cv2, "VideoCapture", Cap)

    cams = sidecar.list_cameras()

    assert cams == [{"index": 0, "name": "Camera 0"}, {"index": 2, "name": "Camera 2"}]
    # misses are released too — they hold the device otherwise
    assert ("released", 1) in opened and ("released", 7) in opened


# ── extract: ffmpeg re-encode pass ─────────────────────────────────

def _noise_clip(path, frames=45, size=(320, 240)):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"avc1"), 30, size)
    for _ in range(frames):
        w.write(np.random.randint(0, 255, (size[1], size[0], 3), np.uint8))
    w.release()


def test_reencode_shrinks_and_reports(tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    clip = tmp_path / "c.mp4"
    _noise_clip(clip)
    before = clip.stat().st_size

    info = extract._reencode(str(clip), 28)

    assert info["reencoded"] is True
    assert info["bytes_before"] == before
    assert info["bytes_after"] == clip.stat().st_size
    assert cv2.VideoCapture(str(clip)).isOpened()        # still a playable file


def test_reencode_is_skipped_for_crf_zero(tmp_path):
    clip = tmp_path / "c.mp4"
    _noise_clip(clip)
    info = extract._reencode(str(clip), 0)
    assert info["reencoded"] is False and info["bytes_after"] == clip.stat().st_size


def test_extract_end_to_end_without_defacing(tmp_path):
    src, dest = tmp_path / "src.mp4", tmp_path / "seg.mp4"
    _noise_clip(src, frames=60)
    r = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(HERE), "extract.py"),
         str(src), str(dest), "0.5", "1.5", "160", "160", "30", "avc1", "off", "41", "0", "0"],
        capture_output=True, text=True, timeout=120)
    import json
    out = json.loads(r.stdout.strip().splitlines()[-1])

    assert out["ok"] is True
    assert (out["w"], out["h"]) == (160, 120)              # square cap on the longer side
    assert 25 <= out["frames"] <= 32                        # ~1 s at 30 fps
    assert out["deidentified"] is False and out["eyeref"] is False
    assert dest.is_file() and os.path.isfile(out["thumb"])


# ── media time: video frames are stamped by position, not by processing ──

def _grey(i, fps, f_hz=2.0):
    """Grey level of frame i: a sine at f_hz in the clip's own (media) time."""
    return int(round(128 + 60 * np.sin(2 * np.pi * f_hz * i / fps)))


def _grey_clip(path, fps, n):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 48))
    for i in range(n):
        w.write(np.full((48, 64, 3), _grey(i, fps), np.uint8))
    w.release()


class _Pt:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z


class _FakeHands:
    """HandLandmarker stand-in: one right hand whose thumb-index distance is
    the frame's grey level (mm) — the clip's content, not the processing, sets
    the signal. ``delay`` (s, or a callable returning s) plays a busy CPU."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.ts = []

    def detect_for_video(self, image, ts_ms):
        self.ts.append(ts_ms)
        d = self.delay() if callable(self.delay) else self.delay
        if d:
            time.sleep(d)
        world = [_Pt(0.001 * i, 0.004 * i) for i in range(21)]
        world[4] = _Pt(float(image.numpy_view().mean()) / 1000.0, 0.08)   # thumb tip
        world[8] = _Pt(0.0, 0.08)                                           # index tip
        return SimpleNamespace(hand_world_landmarks=[world],
                               hand_landmarks=[[_Pt(0.5, 0.5)] * 21],
                               handedness=[[SimpleNamespace(category_name="Right", score=0.95)]])


class _FakeFace:
    """FaceLandmarker stand-in: irises at fixed image positions; ``seen()``
    decides per call whether a face is found."""

    def __init__(self, seen=lambda: True):
        self.seen = seen

    def detect_for_video(self, image, ts_ms):
        if not self.seen():
            return SimpleNamespace(face_landmarks=[])
        lms = [_Pt(0.5, 0.5)] * 478
        lms[sidecar._IRIS_L], lms[sidecar._IRIS_R] = _Pt(0.4, 0.4), _Pt(0.6, 0.4)
        return SimpleNamespace(face_landmarks=[lms])


def _sidecar(hands, face=None, face_rate="full"):
    """A Sidecar on fake landmarkers; returns (sidecar, message list)."""
    msgs, lock = [], threading.Lock()

    def send(m):
        with lock:
            msgs.append(m)

    sc = sidecar.Sidecar(send)
    sc._landmarker = hands
    sc._ensure_landmarker = lambda: None
    sc._ensure_face_landmarker = lambda: face
    sc.handle({"cmd": "face", "on": face is not None, "rate": face_rate})
    return sc, msgs


def _play(sc, msgs, clip, *, start_s=None, end_s=None, realtime=False, loop=False,
          until=None, timeout=30.0):
    """Start a pass and wait for its `done` (or ``until(new messages)``)."""
    n0 = len(msgs)
    sc.handle({"cmd": "start", "video": str(clip), "start_s": start_s, "end_s": end_s,
               "loop": loop, "realtime": realtime})
    deadline = time.time() + timeout
    while time.time() < deadline:
        new = msgs[n0:]
        if any(m["type"] == "done" for m in new) or (until and until(new)):
            break
        time.sleep(0.01)
    return msgs[n0:]


def _of(msgs, kind):
    return [m for m in msgs if m["type"] == kind]


def _signature(msgs):
    """What the analysis sees of a pass: frame, time and signal of each hand."""
    return [(m["frame"], m["ts"], round(m["hands"][0]["world"][4][0], 6))
            for m in _of(msgs, "hand")]


def test_media_time_us():
    assert sidecar.media_time_us(0, 30.0) == 0
    assert sidecar.media_time_us(150, 30.0) == 5_000_000
    assert sidecar.media_time_us(25, 25.0) == 1_000_000
    assert sidecar.media_time_us(1, 29.97) == 33_367


def test_video_frames_carry_media_time_hand_and_face_alike(tmp_path):
    clip = tmp_path / "c25.mp4"
    _grey_clip(clip, fps=25, n=60)                         # 2.4 s at 25 fps
    hands = _FakeHands()
    sc, msgs = _sidecar(hands, _FakeFace(), face_rate="full")
    try:
        out = _play(sc, msgs, clip, start_s=1.0, end_s=1.8)
    finally:
        sc._stop_camera()

    video = _of(out, "video")[0]
    assert video["fps"] == pytest.approx(25.0)
    assert (video["start_frame"], video["end_frame"], video["frames"]) == (25, 45, 60)
    assert out.index(video) < out.index(_of(out, "hand")[0])   # the rate before any frame

    hand = _of(out, "hand")
    assert [m["frame"] for m in hand] == list(range(25, 45))
    assert [m["ts"] for m in hand] == [f * 40_000 for f in range(25, 45)]   # position in the file
    for m in hand:     # the index matches the content: the seek landed where it says
        assert abs(m["hands"][0]["world"][4][0] * 1000 - _grey(m["frame"], 25)) <= 4
    # the face of a frame carries that frame's time, so its reference is fresh
    assert [m["ts"] for m in _of(out, "face")] == [m["ts"] for m in hand]
    assert all(m["iris_age_ms"] == 0 for m in hand)
    # MediaPipe's own timestamps advance by media time as well
    assert all(b - a == 40 for a, b in zip(hands.ts, hands.ts[1:]))

    done = _of(out, "done")[0]
    assert (done["frames"], done["first_frame"], done["last_frame"], done["eof"]) == (20, 25, 44, False)
    assert done["pts_drift_ms"] < 1.0                       # constant-rate clip


def test_media_time_does_not_depend_on_processing_speed(tmp_path):
    """As fast as possible, in real time, and slower than real time with
    spikes: the same frames with the same timestamps — the axis is the clip's."""
    clip = tmp_path / "c25.mp4"
    _grey_clip(clip, fps=25, n=60)
    rng = random.Random(3)
    runs = {}
    for name, realtime, delay in (("fast", False, 0.0),
                                  ("realtime", True, 0.0),
                                  ("slow", False, lambda: rng.uniform(0.05, 0.09))):  # 40 ms frames
        sc, msgs = _sidecar(_FakeHands(delay))
        try:
            t0 = time.perf_counter()
            out = _play(sc, msgs, clip, start_s=0.4, end_s=1.6, realtime=realtime)
            runs[name] = (_signature(out), time.perf_counter() - t0)
        finally:
            sc._stop_camera()
    ref = runs["fast"][0]
    assert len(ref) == 30 and ref[0][:2] == (10, 400_000)
    assert runs["realtime"][0] == ref and runs["slow"][0] == ref
    assert runs["fast"][1] < 1.2 < runs["slow"][1]          # really processed at different speeds


def test_eco_face_cadence_and_iris_age_run_in_media_time(tmp_path):
    """The eco eye reference is taken every round(0.2 s · fps) frames of the
    clip and its age is media time — however fast the frames are processed."""
    clip = tmp_path / "c25.mp4"
    _grey_clip(clip, fps=25, n=60)
    rng = random.Random(5)
    seen = []
    for delay in (0.0, lambda: rng.uniform(0.0, 0.06)):
        sc, msgs = _sidecar(_FakeHands(delay), _FakeFace(), face_rate="eco")
        try:
            out = _play(sc, msgs, clip, start_s=0.0, end_s=0.8)
        finally:
            sc._stop_camera()
        seen.append(([m["ts"] for m in _of(out, "face")],
                     [m["iris_age_ms"] for m in _of(out, "hand")]))
    faces, ages = seen[0]
    assert faces == [0, 200_000, 400_000, 600_000]          # frames 0, 5, 10, 15 at 25 fps
    assert ages == [0, 40, 80, 120, 160] * 4
    assert seen[1] == seen[0]


def test_a_new_pass_does_not_inherit_the_eye_reference(tmp_path):
    """A second range starts without the first range's iris: MediaPipe's
    clock runs on across passes, so an inherited reference would look fresh."""
    clip = tmp_path / "c25.mp4"
    _grey_clip(clip, fps=25, n=60)
    face_found = [True]
    hands = _FakeHands()
    sc, msgs = _sidecar(hands, _FakeFace(seen=lambda: face_found[0]), face_rate="eco")
    try:
        first = _play(sc, msgs, clip, start_s=0.0, end_s=0.4)
        face_found[0] = False            # e.g. a range where the face is turned away
        second = _play(sc, msgs, clip, start_s=1.0, end_s=1.4)
    finally:
        sc._stop_camera()
    assert all("iris_px" in m for m in _of(first, "hand"))
    assert _of(second, "hand") and not any("iris_px" in m for m in _of(second, "hand"))
    assert [m["ts"] for m in _of(second, "hand")][:2] == [1_000_000, 1_040_000]
    assert all(b > a for a, b in zip(hands.ts, hands.ts[1:]))   # valid for VIDEO mode


def test_looping_replay_keeps_counting_media_time(tmp_path):
    """The Sim source loops a clip: `frame` restarts, `ts` runs on at the clip's
    rate, so time never goes backwards over the wrap."""
    clip = tmp_path / "c25.mp4"
    _grey_clip(clip, fps=25, n=10)
    sc, msgs = _sidecar(_FakeHands())
    try:
        out = _play(sc, msgs, clip, loop=True, until=lambda new: len(_of(new, "hand")) >= 25)
    finally:
        sc._stop_camera()
    hand = _of(out, "hand")[:25]
    assert [m["frame"] for m in hand] == list(range(10)) * 2 + list(range(5))
    assert [m["ts"] for m in hand] == [k * 40_000 for k in range(25)]
    assert not _of(out, "done")
