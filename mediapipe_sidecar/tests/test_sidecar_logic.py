"""Sidecar logic, run under the sidecar's own Python (cv2 + mediapipe present).

    mediapipe_sidecar/.venv/Scripts/python -m pytest mediapipe_sidecar/tests

The main suite launches this via tests/test_sidecar_suite.py.
"""

import os
import subprocess
import sys

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
