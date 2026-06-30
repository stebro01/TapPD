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
