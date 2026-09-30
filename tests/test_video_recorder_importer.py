"""VideoRecorder / VideoImporter: the sidecar transcode is stubbed, the
metadata contract is what is checked."""

import json
from pathlib import Path

import pytest

from video import importer as imp_mod
from video import transcode as tc
from video.importer import VideoImporter
from video.recorder import VideoRecorder


def test_recorder_moves_the_pending_file_and_writes_metadata(tmp_path):
    pending = tmp_path / "pending.mp4"
    pending.write_bytes(b"video")
    dest = tmp_path / "clips" / "default.mp4"
    dest.parent.mkdir()

    clip = VideoRecorder.finalize(str(pending), str(dest), seconds=10.0)

    assert not pending.exists() and dest.read_bytes() == b"video"
    assert clip.duration_s == 10.0 and clip.origin == "recorded"
    meta = json.loads(Path(str(dest) + ".meta.json").read_text(encoding="utf-8")) \
        if Path(str(dest) + ".meta.json").exists() else None
    assert meta is None or meta.get("origin") == "recorded"


def test_importer_transcodes_when_available(tmp_path, monkeypatch):
    src = tmp_path / "phone.mov"
    src.write_bytes(b"src")
    calls = []

    def fake_transcode(s, d):
        calls.append((s, d))
        Path(d).write_bytes(b"normalized")
        return {"w": 640, "h": 480, "fps": 30.0, "frames": 90,
                "src_w": 1920, "src_h": 1080, "src_fps": 60, "src_duration_s": 3.0}

    monkeypatch.setattr(tc, "transcode_available", lambda: True)
    monkeypatch.setattr(tc, "transcode_video", fake_transcode)

    clip = VideoImporter().import_file(str(src), lambda ext: tmp_path / f"video.{ext}")

    assert calls and calls[0][0] == str(src)
    assert clip is not None and clip.origin == "imported"
    assert (clip.width, clip.height, clip.fps) == (640, 480, 30.0)
    assert clip.duration_s == pytest.approx(3.0)
    assert clip.extra["src_w"] == 1920
    assert Path(clip.path).read_bytes() == b"normalized"


def test_importer_falls_back_to_a_copy(tmp_path, monkeypatch):
    src = tmp_path / "phone.mp4"
    src.write_bytes(b"src")
    monkeypatch.setattr(tc, "transcode_available", lambda: False)

    clip = VideoImporter().import_file(str(src), lambda ext: tmp_path / f"video.{ext}")

    assert clip is not None and clip.origin == "imported"
    assert Path(clip.path).read_bytes() == b"src" and Path(clip.path).suffix == ".mp4"


def test_importer_gives_up_when_transcode_fails_and_copy_is_disabled(tmp_path, monkeypatch):
    src = tmp_path / "phone.mp4"
    src.write_bytes(b"src")
    monkeypatch.setattr(tc, "transcode_available", lambda: True)
    monkeypatch.setattr(tc, "transcode_video", lambda s, d: None)
    monkeypatch.setattr(imp_mod, "cfg",
                        lambda *k, default=None: False if k[-1] == "fallback_to_copy"
                        else (True if k[-1] == "transcode" else default))

    assert VideoImporter().import_file(str(src), lambda ext: tmp_path / f"v.{ext}") is None
