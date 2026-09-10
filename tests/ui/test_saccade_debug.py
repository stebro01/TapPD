"""Saccade debug mode: quality numbers, the preview/log hooks on the screen,
and replaying a logged run through the headless task logic."""

import json

import pytest

from capture.base_capture import FacePose, TrackingFrame


def _face(t_s: float, ox: float, oy: float, ipd: float = 60.0, ear: float = 0.3) -> FacePose:
    """A face whose iris midpoint sits at (ox, oy) IPD units from the corner midpoint."""
    cx, cy = 320.0, 240.0
    corners_l = ((cx - ipd, cy), (cx - ipd * 0.3, cy))
    corners_r = ((cx + ipd, cy), (cx + ipd * 0.3, cy))
    ix = cx + ox * ipd
    iy = cy + oy * ipd
    return FacePose(timestamp_us=int(t_s * 1e6), iris_left=(ix - ipd / 2, iy),
                    iris_right=(ix + ipd / 2, iy), corners_left=corners_l,
                    corners_right=corners_r, ear_left=ear, ear_right=ear, nose=(cx, cy + 20))


class FakeCapture:
    sample_rate = 30.0

    def __init__(self):
        self.calls, self.cb, self.preview_cb = [], None, None
    def is_connected(self): return True
    def start_tracking(self, cb): self.cb = cb; self.calls.append("start")
    def stop_tracking(self): self.calls.append("stop")
    def stop_recording(self): self.calls.append("stop")
    def start_recording(self, cb): self.start_tracking(cb)
    def enable_face(self, on, full_rate=False): self.calls.append(("face", on, full_rate))
    def set_preview_callback(self, cb): self.preview_cb = cb; self.calls.append("preview_cb")
    def enable_preview(self, on): self.calls.append(("preview", on))
    def record_clip(self, path, seconds): self.calls.append(("record", path, seconds))


def test_quality_reports_rate_ipd_spread_and_warnings():
    from ui.saccade_debug import quality, quality_text
    from paradigms.saccade_logic import SaccadeTask
    task = SaccadeTask()
    steady = [_face(i / 30, 0.01 * (i % 2), 0.0, ipd=60) for i in range(45)]
    q = quality(steady, task)
    assert 25 <= q["rate_hz"] <= 31 and q["ipd_px"] == 60.0
    assert q["spread"] < 0.01 and q["warnings"] == []
    txt = quality_text(q, "CALIBRATING", "LO")
    assert "Punkt LO" in txt and "IPD 60 px" in txt

    small_far = [_face(i / 30, 0.1 * (i % 3), 0.05 * (i % 2), ipd=30, ear=0.15) for i in range(45)]
    q2 = quality(small_far, task)
    joined = " ".join(q2["warnings"])
    assert "IPD nur 30 px" in joined and "Blick unruhig" in joined and "Lidspalte" in joined
    assert quality([], task)["warnings"][0].startswith("Kein Gesicht")


def _screen(qapp, tmp_path, monkeypatch):
    import storage.database as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    from paradigms.saccade_test import SaccadeTest
    from ui.saccade_screen import SaccadeScreen
    shown = []
    mw = type("MW", (), {"show_results": lambda self, t, c: shown.append(("results", t)),
                         "show_start": lambda self: shown.append(("start",))})()
    scr = SaccadeScreen(mw)
    cap = FakeCapture()
    test = SaccadeTest(cap, duration=5.0)
    scr.start_test(test, "T001")
    return scr, cap, test, shown


def test_debug_mode_records_video_and_log_and_can_be_replayed(qapp, tmp_path, monkeypatch):
    from paradigms.saccade_logic import POINT_ORDER, Phase
    from ui.saccade_debug import replay_log
    scr, cap, test, shown = _screen(qapp, tmp_path, monkeypatch)
    scr.debug_cb.setChecked(True)
    scr._on_start()
    assert scr.debug_panel.isVisibleTo(scr) and cap.preview_cb is not None
    rec = next(c for c in cap.calls if isinstance(c, tuple) and c[0] == "record")
    assert rec[1].endswith(".mp4") and rec[2] > 15
    assert ("preview", True) in cap.calls

    # a full calibration: 2 s per point, distinct gaze offsets per point
    targets = {"LO": (-0.1, -0.08), "RO": (0.1, -0.08), "MI": (0.0, 0.0),
               "LU": (-0.1, 0.08), "RU": (0.1, 0.08),
               "L": (-0.1, 0.0), "M": (0.0, 0.0), "R": (0.1, 0.0)}      # any layout
    order = list(test.task.calib_order)
    t = 0.0
    for i in range(303):                    # 10 s of calibration + a little
        key = test.task.calib_point or order[0]   # look where the task shows the point
        ox, oy = targets[key]
        test._on_tracking(TrackingFrame(timestamp_us=int(t * 1e6),
                                        face=_face(t, ox + 0.002 * (i % 2), oy)))
        t += 1 / 30
        if i % 60 == 59:
            scr._tick()
    scr._tick()
    assert test.task.phase is Phase.TESTING, (test.task.phase, test.task.fail_reason, test.task.references)
    assert "Phase: TESTING" in scr.debug_panel.text.text() or "CALIBRATING" in scr.debug_panel.text.text()
    cap.preview_cb({"jpeg": "", "landmarks": [], "face": [[1, 2]]})
    scr._tick()

    scr._on_cancel()
    assert shown[-1] == ("start",) and cap.preview_cb is None
    log_path = tmp_path / "debug" / "saccade"
    files = sorted(log_path.glob("T001_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["format"] == "tappd-saccade-debug" and data["video"].endswith(".mp4")
    assert data["result"]["phase"] == "TESTING" and set(data["result"]["references"]) == set(order)
    assert len(data["samples"]) == 303 and data["samples"][0]["point"] == order[0]
    assert "Gespeichert" in scr.debug_panel.files.text()

    # replaying the log takes the same decisions — and shows what a stricter
    # threshold would have done
    task = replay_log(str(files[0]))
    assert task.phase is Phase.TESTING and set(task.references) == set(order)
    strict = replay_log(str(files[0]), min_separation=0.5)
    assert strict.phase is Phase.FAILED and "trennbar" in strict.fail_reason.lower() or strict.phase is Phase.FAILED


def test_debug_off_leaves_the_capture_alone(qapp, tmp_path, monkeypatch):
    scr, cap, test, shown = _screen(qapp, tmp_path, monkeypatch)
    scr.debug_cb.setChecked(False)
    scr._on_start()
    assert not scr.debug_panel.isVisibleTo(scr)
    assert not any(isinstance(c, tuple) and c[0] == "record" for c in cap.calls)
    scr._on_cancel()
    assert not list((tmp_path / "debug").rglob("*.json")) if (tmp_path / "debug").exists() else True
