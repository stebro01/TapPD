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
    q = single.protocol()
    assert q.is_ad_hoc and len(q.steps) == 1
    assert (q.steps[0].paradigm, q.steps[0].hand, q.steps[0].duration_s) == \
        ("finger_tapping", "left", 12)
