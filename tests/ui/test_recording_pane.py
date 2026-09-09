"""RecordingPane, ProtocolChooser and the camera footer, with a fake device."""

import pytest

from video.protocol import load_protocol
from video.store import VideoSession


class FakeDevice:
    """Enough of WebcamSource for the pane and the footer."""

    def __init__(self):
        self.camera_index = 0
        self.replay_path = ""
        self._recording = True
        self._face_on = True
        self._preview_callback = None
        self._recorded_callback = None
        self.recorded: list = []
        self.face_calls: list = []

    def is_connected(self): return True
    def list_cameras(self): return [(0, "USB Video Device"), (1, "OBSBOT Tiny 2")]
    def set_preview_callback(self, cb): self._preview_callback = cb
    def set_recorded_callback(self, cb): self._recorded_callback = cb
    def enable_preview(self, on): pass
    def enable_face(self, on, *a): self.face_calls.append(bool(on))
    def configure(self, **k): pass
    def start_tracking(self, cb): self._recording = True
    def stop_tracking(self): self._recording = False
    def record_clip(self, path, seconds): self.recorded.append((path, seconds))


@pytest.fixture
def pane(qapp, isolated_data):
    from ui.recording_pane import RecordingPane
    p = RecordingPane()
    v = VideoSession.create_recording(1, "T001", load_protocol("updrs_hand_basis"),
                                      mirrored=True)
    p.set_session(v)
    p.set_device(FakeDevice())
    p.show_step("rest")
    yield p, v
    p.stop()


def test_detection_readout_uses_corrected_labels(pane):
    p, _ = pane
    p._update_detection({"hand_handedness": ["Left", "Right"],
                         "landmarks": [[[0, 0]], [[0, 0]]], "face": [[0, 0]]})
    assert p._detect_lbl.text() == "✔ 2 Hände (links, rechts)   ·   ✔ Gesicht"

    p._update_detection({"hand_handedness": ["Right"], "landmarks": [[[0, 0]]], "face": []})
    assert p._detect_lbl.text() == "✔ 1 Hand (rechts)   ·   ○ kein Gesicht"

    p._update_detection({"hand_handedness": [], "landmarks": [], "face": []})
    assert p._detect_lbl.text() == "○ keine Hand   ·   ○ kein Gesicht"


def test_record_runs_countdown_then_asks_the_device_for_a_clip(pane, qapp):
    p, v = pane
    dev = p._device
    p._on_record()
    assert p._phase == "countdown" and p.busy

    p._t_left = 0.0
    p._tick()                                         # countdown elapsed → capture
    assert p._phase == "recording"
    assert len(dev.recorded) == 1
    path, seconds = dev.recorded[0]
    assert path.endswith("step_rest_take01.mp4") and seconds == 30
    assert v.step("rest").takes == 1

    # the sidecar's "recorded" message ends the take
    import pathlib
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_bytes(b"x")
    dev._recorded_callback(path)
    p._tick()
    assert p._phase == "review"
    assert v.step("rest").state == "recorded"
    assert v.step("rest").clip_path == path


def test_cancel_leaves_the_step_open_and_the_take_number_used(pane):
    p, v = pane
    p._on_record(); p._t_left = 0.0; p._tick()
    p._on_cancel()
    assert p._phase == "idle" and not p.busy
    assert v.step("rest").state == "pending"
    assert v.step("rest").takes == 1                  # the aborted file keeps its number


def test_documentation_step_hides_the_analysis_toggle(pane):
    p, _ = pane
    p.show_step("head_turn")
    assert not p._auto_cb.isVisible() or not p._auto_cb.isVisibleTo(p)
    p.show_step("tap_right")
    assert p._auto_cb.isVisibleTo(p)


def test_deface_choice_reaches_the_archive_step(pane, monkeypatch):
    """Off on the checkbox → an explicit "off" for this take; on → the
    configured default (None)."""
    from ui import recording_pane as rp
    seen = []

    class FakeWorker:
        def __init__(self, session, seg, deface=None, parent=None):
            seen.append(deface)
            self.done = type("S", (), {"connect": lambda self, cb: None})()
        def start(self): pass
    monkeypatch.setattr(rp, "_ArchiveWorker", FakeWorker)
    monkeypatch.setattr("video.archive.compact_enabled", lambda: True)

    p, v = pane
    for step_id, deface_on in (("rest", False), ("head_turn", True)):
        p.show_step(step_id)
        take = v.begin_take(step_id)
        take.parent.mkdir(parents=True, exist_ok=True); take.write_bytes(b"x")
        v.mark_recorded(step_id, str(take)); p.show_step(step_id)
        p._auto_cb.setChecked(False)
        p._deface_cb.setChecked(deface_on)
        p._on_keep()
        p._job = None                         # the fake worker never finishes
    assert seen == ["off", None]


def test_overlay_toggle_replays_the_take_through_a_sidecar(pane, monkeypatch):
    import capture.mediapipe_capture as mc
    made = []

    class FakeSrc:
        def __init__(self, camera_index=0, flip_handedness=False, replay_path=""):
            self.replay_path = replay_path; self.replay_mirror = None
            self.replay_track = True
            self.calls = []; made.append(self)
        def connect(self): self.calls.append("connect")
        def configure(self, **k): self.calls.append(("configure", k))
        def set_preview_callback(self, cb): self.calls.append("preview_cb")
        def enable_preview(self, on): self.calls.append(("preview", on))
        def enable_face(self, on, *a): self.calls.append(("face", on))
        def start_tracking(self, cb): self.calls.append("start")
        def stop_tracking(self): self.calls.append("stop")
        def disconnect(self): self.calls.append("disconnect")
    monkeypatch.setattr(mc, "WebcamSource", FakeSrc)

    p, v = pane
    take = v.begin_take("rest"); take.parent.mkdir(parents=True, exist_ok=True)
    take.write_bytes(b"x"); v.mark_recorded("rest", str(take)); p.show_step("rest")
    assert p._phase == "review" and p._overlay_cb.isVisibleTo(p)
    assert type(p._view.currentWidget()).__name__ == "QVideoWidget"

    p._overlay_cb.setChecked(True)
    assert p.overlay_active and made[0].replay_path == str(take)
    assert made[0].replay_mirror is True                 # own take → webcam setting
    assert made[0].replay_track is True                  # no stored track → re-track
    assert ("face", True) in made[0].calls
    assert "start" in made[0].calls and ("preview", True) in made[0].calls
    assert type(p._view.currentWidget()).__name__ == "WebcamPreview"
    assert "neu berechnet" in p._last_status

    p._overlay_cb.setChecked(False)
    assert not p.overlay_active and "disconnect" in made[0].calls
    assert type(p._view.currentWidget()).__name__ == "QVideoWidget"

    # leaving review stops it too
    p._overlay_cb.setChecked(True)
    p.show_step("head_turn")                              # pending → idle
    assert not p.overlay_active


def test_overlay_uses_the_stored_track_and_keeps_sources_apart(pane, monkeypatch, tmp_path):
    """With a track file the sidecar only streams frames and the pane draws
    the stored landmarks per frame; live-camera previews never bleed in."""
    import json
    import capture.mediapipe_capture as mc

    class FakeSrc:
        def __init__(self, camera_index=0, flip_handedness=False, replay_path=""):
            self.replay_path = replay_path; self.replay_track = True; self.cb = None
            self.calls = []
        def connect(self): pass
        def configure(self, **k): pass
        def set_preview_callback(self, cb): self.cb = cb
        def enable_preview(self, on): pass
        def enable_face(self, on, *a): self.calls.append(("face", on))
        def start_tracking(self, cb): pass
        def stop_tracking(self): pass
        def disconnect(self): pass
    monkeypatch.setattr(mc, "WebcamSource", FakeSrc)

    p, v = pane
    take = v.begin_take("rest"); take.parent.mkdir(parents=True, exist_ok=True)
    take.write_bytes(b"x"); v.mark_recorded("rest", str(take))
    seg = v.confirm_step("rest")
    track = tmp_path / "seg.track.json"
    hand = [[0.1 * i, 0.2] for i in range(21)]
    track.write_text(json.dumps({"frames": {"7": {"hands": [["left", hand]],
                                                   "iris": [[0.4, 0.3], [0.6, 0.3]]}}}))
    seg.track_path = str(track)
    p.show_step("rest")

    p._overlay_cb.setChecked(True)
    src = p._overlay_src
    assert src.replay_track is False and ("face", False) in src.calls
    assert "gespeicherte" in p._last_status

    drawn = []
    monkeypatch.setattr(p._preview, "set_frame",
                        lambda jpeg, lms, face=None, iris=None: drawn.append((lms, face, iris)))
    # a live-camera preview arrives too — it must not be shown
    p._on_preview({"jpeg": "", "landmarks": [[[0, 0]]], "face": [[1, 1]], "hand_handedness": ["Right"]}, "live")
    src.cb({"jpeg": "", "frame": 7, "landmarks": [], "face": []})
    p._tick()

    assert drawn[-1][0] == [hand] and drawn[-1][1] == []
    assert drawn[-1][2] == [[0.4, 0.3], [0.6, 0.3]]
    assert "1 Hand (links)" in p._detect_lbl.text()

    src.cb({"jpeg": "", "frame": 99, "landmarks": [], "face": []})   # no entry → bare frame
    p._tick()
    assert drawn[-1][0] == [] and "keine Hand" in p._detect_lbl.text()


def test_switching_steps_is_refused_mid_take(pane):
    p, _ = pane
    p._on_record()
    p.show_step("tap_left")
    assert p._step.id == "rest"


# ── camera footer on the workbench ─────────────────────────────────

def test_camera_switch_cycles_the_stream_and_is_locked_while_recording(workbench, app, monkeypatch):
    wb = workbench
    dev = FakeDevice()
    wb._device = dev
    wb._populate_cameras()
    assert [wb._cam_combo.itemText(i) for i in range(wb._cam_combo.count())] == \
        ["USB Video Device", "OBSBOT Tiny 2"]

    saved = {}
    monkeypatch.setattr("app_settings.app_settings",
                        lambda: type("S", (), {"setValue": lambda self, k, v: saved.__setitem__(k, v)})())
    wb._cam_combo.setCurrentIndex(1)
    assert dev.camera_index == 1 and dev._recording          # stopped and restarted
    assert saved.get("camera_index") == 1
    assert "OBSBOT" in wb._status.text()

    wb._on_busy(True)
    assert not wb._cam_combo.isEnabled() and not wb._add_btn.isEnabled()
    wb._on_busy(False)
    assert wb._cam_combo.isEnabled()


def test_face_toggle_reaches_the_device(workbench):
    dev = FakeDevice()
    workbench._device = dev
    workbench._face_cb.setChecked(False)
    assert dev.face_calls[-1] is False


# ── protocol chooser ────────────────────────────────────────────────

def test_chooser_returns_a_protocol_either_way(qapp):
    from ui.protocol_chooser import ProtocolChooser

    full = ProtocolChooser()
    assert full._rb_protocol.isChecked()
    p = full.protocol()
    assert p.id == "updrs_hand_basis" and len(p.steps) == 4
    assert "Gesichts-Tracking" in full._hint.text()        # the loader's note, up front

    single = ProtocolChooser(single_only=True)
    single._para_combo.setCurrentIndex(single._para_combo.findData("finger_tapping"))
    single._hand_combo.setCurrentIndex(single._hand_combo.findData("left"))
    single._dur.setValue(12)
    assert not single.is_interactive
    q = single.protocol()
    assert q.is_ad_hoc and len(q.steps) == 1
    assert (q.steps[0].paradigm, q.steps[0].hand, q.steps[0].duration_s) == \
        ("finger_tapping", "left", 12)

    # an interactive paradigm is flagged and routed live, never filmed
    single._para_combo.setCurrentIndex(single._para_combo.findData("tower_of_hanoi"))
    assert single.is_interactive
    assert "live am Bildschirm" in single._para_combo.currentText()
    assert "live" in single._hint.text() and not single._dur.isEnabled()
    assert single.single_choice()[:2] == ("tower_of_hanoi", "left")
