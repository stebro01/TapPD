"""The post-confirm pipeline with the real sidecar: analyse → archive → cleanup.

Slow (~30–60 s) and needs the MediaPipe sidecar venv; skipped otherwise. A
clip is generated through the sidecar's own cv2, so no fixture file is needed.
Analysis results are asserted only when a real hand clip is available
(data/clips/default.mp4 — the developer's Sim clip); on synthetic noise the
analysis may legitimately find no hand, and then only the archive is checked.
"""

import os
import pathlib
import shutil
import subprocess
import time

import pytest

from tests.ui.conftest import sidecar_available

pytestmark = pytest.mark.sidecar

REAL_CLIP = pathlib.Path(__file__).resolve().parents[2] / "data" / "clips" / "default.mp4"


def _make_noise_clip(dest: pathlib.Path, seconds: int = 3) -> None:
    from capture.mediapipe_capture import _SIDECAR_PY
    code = (
        "import cv2, numpy as np, sys\n"
        f"w = cv2.VideoWriter(r'{dest}', cv2.VideoWriter_fourcc(*'avc1'), 30, (320, 240))\n"
        f"for _ in range(30 * {seconds}):\n"
        "    w.write(np.random.randint(0, 255, (240, 320, 3), np.uint8))\n"
        "w.release()\n")
    subprocess.run([_SIDECAR_PY, "-c", code], check=True, timeout=120)


@pytest.mark.skipif(not sidecar_available(), reason="MediaPipe-Sidecar nicht eingerichtet")
def test_confirmed_take_is_analysed_archived_and_cleaned_up(qapp, isolated_data, monkeypatch):
    from video.protocol import protocol_for_paradigm
    from video.store import VideoSession
    from ui.recording_pane import RecordingPane

    real = REAL_CLIP.is_file()
    v = VideoSession.create_recording(1, "T001", protocol_for_paradigm("finger_tapping",
                                                                       duration_s=3),
                                      mirrored=True)
    v.db_session_id = 1
    take = v.begin_take("finger_tapping")
    if real:
        shutil.copy(REAL_CLIP, take)
        v.steps[0].duration_s = 6
    else:
        _make_noise_clip(take)
    raw_size = take.stat().st_size
    v.mark_recorded("finger_tapping", str(take))

    # no clinical DB in this test: the export step is stubbed
    exported = []
    monkeypatch.setattr("video.export.export_or_update",
                        lambda session, seg, key: exported.append(key))

    pane = RecordingPane()
    pane.set_session(v)
    pane.show_step("finger_tapping")
    pane._auto_cb.setChecked(True)
    pane._on_keep()

    t0 = time.time()
    while (pane._job is not None or take.is_file()) and time.time() - t0 < 180:
        qapp.processEvents()
        time.sleep(0.05)
    pane.stop()

    seg = v.segments[0]
    assert os.path.isfile(seg.clip_path) and seg.clip_path != str(take)
    assert seg.clip_path.endswith(f"{seg.id}.mp4")
    assert seg.deidentified is True                       # privacy.deface: blur
    assert not take.is_file()                             # raw take cleaned up
    assert v.step("finger_tapping").clip_path == seg.clip_path
    assert seg.clip_path and os.path.getsize(seg.clip_path) < raw_size / 5   # the ffmpeg pass
    if real:
        assert "finger_tapping" in seg.results
        assert exported == ["finger_tapping"]
    back = VideoSession.load(str(v.save()))
    assert back.segments[0].recorded and back.segments[0].clip_path == seg.clip_path
