"""VideoLab analysis timing: media time end to end, and a watchdog that never
shortens a segment.

The watchdog tests drive AnalysisRunner with a scripted source (fast, no
sidecar). The last test runs the real sidecar process — its real replay loop,
transport and timing — with a fake hand landmarker whose thumb-index distance
is the frame's grey level: synthetic video gives MediaPipe no hand, but the
time axis is what is under test, and the clip's content decides the signal.
"""

import math
import subprocess
import time

import pytest

from capture.base_capture import FingerData, HandFrame, TrackingFrame
from tests.ui.conftest import sidecar_available


def _tap_frame(i, fps=30.0, f_hz=2.0):
    d = 45.0 + 25.0 * math.sin(2 * math.pi * f_hz * i / fps)
    return HandFrame(timestamp_us=round(i * 1e6 / fps), hand_type="right",
                     palm_position=(0.0, 0.0, 0.0), palm_velocity=(0.0, 0.0, 0.0),
                     fingers=[FingerData(0, (d, 0.0, 0.0), True),
                              FingerData(1, (0.0, 0.0, 0.0), True)],
                     confidence=0.95, frame_index=i)


class _ScriptedReplay:
    """Stands in for the webcam source of a VideoLab run; the test delivers
    frames and `done` itself, as the sidecar's reader thread would."""

    sample_rate = 30.0
    time_base = "media"

    def __init__(self, end_frame):
        self.video_info = {"fps": 30.0, "frames": 900, "start_frame": 0, "end_frame": end_frame}
        self.last_done = {}
        self.frames_seen = 0
        self.last_frame_index = None
        self.last_activity = time.monotonic()
        self.sidecar_info = {}
        self._sensor_issues = []
        self.replay_mirror = False
        self.connected = True
        self.stopped = False
        self._cb = self._done_cb = None

    # what AnalysisRunner.start calls
    def configure(self, **kw): pass
    def play_range(self, video, start_s, end_s, realtime=True): self._realtime = realtime
    def set_preview_callback(self, cb): pass
    def set_done_callback(self, cb): self._done_cb = cb
    def enable_preview(self, on): pass
    def enable_face(self, on, full_rate=False): pass
    def start_tracking(self, cb): self._cb = cb; self.last_activity = time.monotonic()
    def stop_recording(self): self.stopped = True
    def disconnect(self): pass
    def is_connected(self): return self.connected

    def frame(self, i):
        f = _tap_frame(i)
        self.frames_seen += 1
        self.last_frame_index = i
        self.last_activity = time.monotonic()
        self._cb(TrackingFrame(timestamp_us=f.timestamp_us, hands=[f]))

    def done(self):
        self.last_done = {"frames": self.frames_seen, "first_frame": 0,
                          "last_frame": self.last_frame_index,
                          "end_frame": self.video_info["end_frame"], "eof": False,
                          "pts_drift_ms": 0.0}
        self._done_cb()


@pytest.fixture
def quick_watchdog(monkeypatch):
    """Hang timeout 0.6 s / grace 0.2 s, so the watchdog is testable quickly."""
    import video.config as vc
    orig = vc.cfg
    over = {("analysis", "hang_timeout_s"): 0.6, ("analysis", "done_grace_s"): 0.2}
    monkeypatch.setattr(vc, "cfg", lambda *k, default=None: over.get(k, orig(*k, default=default)))


def _run(src, end_s):
    from ui.analysis_runner import AnalysisRunner
    runner = AnalysisRunner()
    runner._src = src
    out = {"finished": [], "failed": []}
    runner.finished.connect(lambda test, feats: out["finished"].append(feats))
    runner.failed.connect(lambda msg: out["failed"].append(msg))
    runner.start("clip.mp4", 0.0, end_s, "finger_tapping", hand="right")
    return runner, out


def _pump(qapp, seconds, until=lambda: False):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not until():
        qapp.processEvents()
        time.sleep(0.01)


def test_a_slow_analysis_is_not_cut_short(qapp, quick_watchdog):
    """Frames keep coming, slowly, for 2.5 hang timeouts in a row: the run
    waits for `done` and uses every frame (the old budget of range + margin
    computed the features on whatever had arrived by then)."""
    src = _ScriptedReplay(end_frame=90)
    runner, out = _run(src, 3.0)
    for i in range(90):
        src.frame(i)
        _pump(qapp, 0.017)
    assert not out["finished"] and not out["failed"]     # 1.5 s > 0.6 s, still running
    src.done()
    _pump(qapp, 0.5, lambda: out["finished"])

    assert len(out["finished"]) == 1 and not out["failed"]
    t = runner.timing()
    assert t["frames_processed"] == t["frames_expected"] == 90
    assert t["time_base"] == "media" and t["fps"] == 30.0 and t["realtime"] is False
    assert out["finished"][0]["tap_frequency_hz"] == pytest.approx(2.0, rel=0.02)
    runner.teardown()


def test_a_stalled_sidecar_fails_instead_of_giving_a_shortened_result(qapp, quick_watchdog):
    src = _ScriptedReplay(end_frame=90)
    runner, out = _run(src, 3.0)
    for i in range(40):
        src.frame(i)
    _pump(qapp, 2.0, lambda: out["failed"])

    assert out["failed"] and not out["finished"]
    assert "keine Frames" in out["failed"][0] and "bis Frame 39 von 89" in out["failed"][0]
    assert src.stopped
    src.done()                                    # a late `done` changes nothing
    _pump(qapp, 0.3)
    assert not out["finished"]
    runner.teardown()


def test_a_lost_done_finishes_once_the_range_is_complete(qapp, quick_watchdog):
    src = _ScriptedReplay(end_frame=90)
    runner, out = _run(src, 3.0)
    for i in range(90):
        src.frame(i)
    _pump(qapp, 2.0, lambda: out["finished"] or out["failed"])
    assert len(out["finished"]) == 1 and not out["failed"]
    runner.teardown()


def test_a_sidecar_error_before_any_frame_fails_with_its_message(qapp, quick_watchdog):
    src = _ScriptedReplay(end_frame=90)
    runner, out = _run(src, 3.0)
    src._sensor_issues = ["Video /x.mp4 konnte nicht geöffnet werden"]
    _pump(qapp, 2.0, lambda: out["failed"])
    assert out["failed"] and "konnte nicht geöffnet werden" in out["failed"][0]
    assert not out["finished"]
    runner.teardown()


def test_a_dead_sidecar_fails_at_once(qapp, quick_watchdog, monkeypatch):
    import video.config as vc
    orig = vc.cfg
    monkeypatch.setattr(vc, "cfg", lambda *k, default=None:
                        30.0 if k == ("analysis", "hang_timeout_s") else orig(*k, default=default))
    src = _ScriptedReplay(end_frame=90)
    runner, out = _run(src, 3.0)
    src.frame(0)
    src.connected = False
    _pump(qapp, 2.0, lambda: out["failed"])
    assert out["failed"] and "Sidecar beendet" in out["failed"][0]
    runner.teardown()


def test_range_completion_ignores_an_unknown_frame_count(qapp):
    """Some backends report no frame count (-1/0): then only the range's own
    end counts — never 'complete' after the first frame."""
    from ui.analysis_runner import AnalysisRunner
    runner = AnalysisRunner()
    runner._src = src = _ScriptedReplay(end_frame=90)
    src.video_info["frames"] = -1
    src.last_frame_index = 10
    assert not runner._range_complete()
    src.last_frame_index = 89
    assert runner._range_complete()


# ── the real sidecar process, with a fake hand landmarker ──────────

_GREY_CLIP = """
import sys, cv2, numpy as np
path, fps, n, f_hz = sys.argv[1], float(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 48))
for i in range(n):
    g = int(round(128 + 60 * np.sin(2 * np.pi * f_hz * i / fps)))
    w.write(np.full((48, 64, 3), g, np.uint8))
w.release()
"""

_SIDECAR_WRAPPER = '''
"""The real sidecar, but its hand landmarker reads the thumb-index distance
off the frame's grey level and takes {lo}-{hi} s per frame."""
import random, sys, time
from types import SimpleNamespace
sys.path.insert(0, {sidecar_dir!r})
import sidecar


class P:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z


class Hands:
    rng = random.Random(11)

    def detect_for_video(self, image, ts_ms):
        time.sleep(self.rng.uniform({lo!r}, {hi!r}))
        world = [P(0.001 * i, 0.004 * i) for i in range(21)]
        world[4] = P(float(image.numpy_view().mean()) / 1000.0, 0.08)
        world[8] = P(0.0, 0.08)
        return SimpleNamespace(hand_world_landmarks=[world],
                               hand_landmarks=[[P(0.5, 0.5)] * 21],
                               handedness=[[SimpleNamespace(category_name="Right", score=0.95)]])


def _ensure(self):
    if self._landmarker is None:
        self._landmarker = Hands()


sidecar.Sidecar._ensure_landmarker = _ensure
sidecar.Sidecar._ensure_face_landmarker = lambda self: None
sidecar.main()
'''


@pytest.mark.sidecar
@pytest.mark.skipif(not sidecar_available(), reason="MediaPipe-Sidecar nicht eingerichtet")
def test_realtime_or_not_the_analysis_measures_the_clip(qapp, tmp_path, monkeypatch):
    """The whole VideoLab path on a 60 fps clip whose content taps at 2.0 Hz:
    once as fast as possible, once in real time on a CPU slower than real time
    (per-frame delay above the 16.7 ms frame interval, with spikes, and longer
    in total than the old range + 4 s budget). Same frames, same media-time
    stamps, same result — 2.0 Hz — and no frame lost."""
    import capture.mediapipe_capture as mc
    from ui.analysis_runner import AnalysisRunner

    clip = tmp_path / "tap60.mp4"
    subprocess.run([mc._SIDECAR_PY, "-c", _GREY_CLIP, str(clip), "60", "210", "2.0"],
                   check=True, timeout=120)
    orig_play_range = mc.WebcamSource.play_range

    def analyse(realtime, delay_s):
        wrapper = tmp_path / f"sidecar_rt{int(realtime)}.py"
        wrapper.write_text(_SIDECAR_WRAPPER.format(sidecar_dir=mc._SIDECAR_DIR,
                                                   lo=delay_s[0], hi=delay_s[1]))
        monkeypatch.setattr(mc, "_SIDECAR_SCRIPT", str(wrapper))
        forced = realtime       # AnalysisRunner asks for realtime=False; override
        monkeypatch.setattr(mc.WebcamSource, "play_range",
                            lambda self, v, s, e, realtime=True:
                            orig_play_range(self, v, s, e, realtime=forced))
        runner = AnalysisRunner()
        out = {}
        runner.finished.connect(lambda test, feats: out.update(test=test, feats=feats))
        runner.failed.connect(lambda msg: out.update(error=msg))
        t0 = time.monotonic()
        runner.start(str(clip), 0.5, 3.5, "finger_tapping", hand="right")
        while not out and time.monotonic() - t0 < 90:
            qapp.processEvents()
            time.sleep(0.01)
        wall = time.monotonic() - t0
        try:
            assert "error" not in out, out.get("error")
            assert "test" in out, "Auswertung lief nicht zu Ende"
            frames = out["test"].get_frames()
            return {"fps": runner._src.sample_rate, "timing": runner.timing(), "wall": wall,
                    "stamps": [(f.frame_index, f.timestamp_us) for f in frames],
                    "freq": out["feats"]["tap_frequency_hz"]}
        finally:
            runner.teardown()

    fast = analyse(False, (0.0, 0.002))
    slow = analyse(True, (0.045, 0.07))

    for res in (fast, slow):
        assert res["fps"] == 60.0
        assert res["timing"]["frames_processed"] == res["timing"]["frames_expected"] == 180
        assert res["stamps"][0] == (30, 500_000)
        assert all(ts == round(i * 1e6 / 60) for i, ts in res["stamps"])
        assert res["freq"] == pytest.approx(2.0, rel=0.02)
    assert slow["stamps"] == fast["stamps"] and slow["freq"] == fast["freq"]
    assert slow["wall"] > (3.5 - 0.5) + 4.0 > fast["wall"]   # past the old time budget


def test_raw_json_keeps_the_time_base_and_frame_indices(tmp_path, monkeypatch):
    """A video analysis' raw data can be re-timed later: time base, the clip's
    facts and every frame's index are stored next to the timestamps."""
    import json
    import ui.results_screen as rs
    from capture.mediapipe_capture import WebcamSource
    from paradigms.finger_tapping import FingerTappingTest
    monkeypatch.setattr(rs, "SAMPLES_DIR", tmp_path)
    src = WebcamSource(replay_path="clip.mp4")
    src._dispatch({"type": "video", "fps": 60.0, "frames": 600,
                   "start_frame": 90, "end_frame": 450})
    test = FingerTappingTest(capture=src, duration=6.0, hand="right")
    for i in range(90, 100):
        test._on_frame(_tap_frame(i, fps=60.0))

    data = json.loads(rs.save_raw_data(test, "X", {"tap_frequency_hz": 2.0}).read_text())

    assert data["sample_rate"] == 60.0 and data["time_base"] == "media"
    assert data["video"] == {"fps": 60.0, "frames": 600, "start_frame": 90, "end_frame": 450}
    assert data["frames"][0]["frame_index"] == 90
    assert data["frames"][0]["timestamp_us"] == 1_500_000


def test_raw_json_of_a_live_camera_says_wallclock(tmp_path, monkeypatch):
    import json
    import ui.results_screen as rs
    from capture.mediapipe_capture import WebcamSource
    from paradigms.finger_tapping import FingerTappingTest
    monkeypatch.setattr(rs, "SAMPLES_DIR", tmp_path)
    test = FingerTappingTest(capture=WebcamSource(), duration=6.0, hand="right")
    test._on_frame(_tap_frame(0))

    data = json.loads(rs.save_raw_data(test, "X", {}).read_text())

    assert data["time_base"] == "wallclock" and data["sample_rate"] == 30.0
    assert "video" not in data
