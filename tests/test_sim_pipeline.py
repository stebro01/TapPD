"""Integration tests for the Sim source (recorded video looped through MediaPipe).

These spawn the Python-3.12 sidecar and need the sidecar venv + a generated
video, so they are skipped when the sidecar isn't set up (e.g. plain CI).
"""

import os
import subprocess
import time

import pytest

from capture.mediapipe_capture import WebcamSource, _SIDECAR_PY

pytestmark = pytest.mark.skipif(
    not WebcamSource.sidecar_ready()[0],
    reason="MediaPipe sidecar not set up (run mediapipe_sidecar/setup_sidecar.sh)",
)

_GEN_VIDEO = """
import cv2, numpy as np, sys
out, w, h, fps, sec = sys.argv[1], 320, 240, 30, 2
vw = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
for i in range(fps * sec):
    img = np.full((h, w, 3), 20, np.uint8)
    cx = int(w * 0.5 + 0.3 * w * np.sin(i / 12))
    cv2.circle(img, (cx, int(h * 0.6)), 30, (200, 220, 200), -1)
    cv2.ellipse(img, (int(w*0.5), int(h*0.35)), (45, 60), 0, 0, 360, (180, 170, 160), -1)
    cv2.circle(img, (int(w*0.43), int(h*0.32)), 6, (40, 40, 40), -1)
    cv2.circle(img, (int(w*0.57), int(h*0.32)), 6, (40, 40, 40), -1)
    vw.write(img)
vw.release()
"""


@pytest.fixture
def fake_video(tmp_path):
    path = str(tmp_path / "clip_fake.mp4")
    subprocess.run([_SIDECAR_PY, "-c", _GEN_VIDEO, path], check=True)
    assert os.path.getsize(path) > 0
    return path


def test_webcamsource_loops_video_through_mediapipe(fake_video):
    """The recorded clip is looped through the sidecar; hand+face landmarkers run."""
    dev = WebcamSource(replay_path=fake_video)
    previews = {"n": 0, "face": 0}
    errors = []
    orig = dev._dispatch

    def wrap(m):
        if m.get("type") == "preview":
            previews["n"] += 1
            if "face" in m:            # the face path is wired (field always present)
                previews["face"] += 1
        elif m.get("type") == "error":
            errors.append(m.get("msg"))
        orig(m)

    dev._dispatch = wrap
    dev.connect()
    assert dev.is_connected()
    dev.set_preview_callback(lambda m: None)
    dev.start_recording(lambda f: None)
    dev.enable_preview(True)
    dev.enable_face(True)
    time.sleep(4)            # 2 s clip → must loop
    dev.stop_recording()
    dev.disconnect()

    assert not dev.is_connected()
    assert not errors, f"sidecar errors: {errors}"
    # Plumbing assertions (deterministic). Actual hand/face *detection* on
    # synthetic content is not reliable, so we don't assert detection counts —
    # only that the looped video streams through the hand+face path cleanly.
    assert previews["n"] > 30, "video loop did not stream enough preview frames"
    assert previews["face"] == previews["n"], "face path not wired into every preview"


def test_segment_extract_and_deface(fake_video, tmp_path):
    """Extract a range → compact segment clip; defacing flag set (deface=blur)."""
    from video.extractor import VideoSegmentExtractor, extract_available
    assert extract_available()
    dest = str(tmp_path / "seg.mp4")
    clip = VideoSegmentExtractor().extract(fake_video, 0.5, 1.5, dest)  # 1 s range
    assert clip is not None and os.path.exists(dest)
    assert clip.origin == "segment"
    from video.config import cfg as video_cfg
    max_w = int(video_cfg("segments", "max_width", default=1080))
    max_h = int(video_cfg("segments", "max_height", default=1080))
    assert clip.width <= max_w and clip.height <= max_h   # segment caps from config
    assert clip.deidentified is True                  # deface=blur (config default)
    assert clip.duration_s > 0


def test_transcode_normalizes_resolution(fake_video, tmp_path):
    """Import normalization downscales + re-encodes to mp4 (via the sidecar venv)."""
    from video import transcode as tc
    assert tc.transcode_available()
    dest = str(tmp_path / "norm.mp4")
    out = tc.transcode_video(fake_video, dest)   # fake clip is 320x240
    assert out and out["ok"]
    assert os.path.getsize(dest) > 0
    # within the configured cap (default 1280x720); 320x240 stays as-is
    assert out["w"] <= 1280 and out["h"] <= 720
    assert out["frames"] > 0


def test_play_range_once_emits_done(fake_video):
    """VideoLab: play a bounded range once → frames stream, exactly one `done`,
    no looping; a warm re-run of a different range works too."""
    dev = WebcamSource()
    done = {"n": 0}
    msgs = {"hand": 0}
    dev.set_done_callback(lambda: done.__setitem__("n", done["n"] + 1))
    orig = dev._dispatch
    dev._dispatch = lambda m: (msgs.__setitem__(
        "hand", msgs["hand"] + (1 if m.get("type") == "hand" else 0)), orig(m))
    dev.connect()
    dev.play_range(fake_video, 0.5, 1.5)   # fake clip is 2 s → range is 1 s
    dev.start_recording(lambda f: None)
    time.sleep(3)                          # well past the 1 s range
    assert msgs["hand"] > 0, "range did not stream frames"
    assert done["n"] == 1, f"expected exactly one done, got {done['n']}"

    # warm re-run, different range, same connected source
    done["n"] = 0
    dev.play_range(fake_video, 0.0, 0.5)
    dev.start_recording(lambda f: None)
    time.sleep(2.5)
    assert done["n"] == 1, "warm re-run did not emit done"
    dev.stop_recording()
    dev.disconnect()


def test_replay_survives_stop_start_handoff(fake_video):
    """Gate→record style stop/start must keep streaming from the looped clip."""
    dev = WebcamSource(replay_path=fake_video)
    counts = {"phase": "a", "a": 0, "b": 0}
    orig = dev._dispatch
    dev._dispatch = lambda m: (counts.__setitem__(
        counts["phase"], counts[counts["phase"]] + (1 if m.get("type") == "hand" else 0)
    ), orig(m))
    dev.connect()
    dev.start_recording(lambda f: None)
    time.sleep(1.0)
    dev.stop_recording()
    time.sleep(0.3)
    counts["phase"] = "b"
    dev.start_recording(lambda f: None)
    time.sleep(1.0)
    dev.stop_recording()
    dev.disconnect()
    assert counts["a"] > 0 and counts["b"] > 0, "stream did not resume after stop/start"
