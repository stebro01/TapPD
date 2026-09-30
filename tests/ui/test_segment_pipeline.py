"""The shared analyse → archive → cleanup pipeline (ui/segment_pipeline.py):
where a segment's analysis reads from, and that own takes and import cuts
go through the same stages."""

import pytest

from video import store
from video.protocol import protocol_for_paradigm
from video.store import VideoSession


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VIDEO_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr("capture.config.source_mirrored", lambda kind="webcam": kind == "webcam")
    return tmp_path


def test_analysis_source_for_takes_and_import_cuts(tmp_path):
    from ui.segment_pipeline import analysis_source
    # own take: the raw file while it exists …
    v = VideoSession.create_recording(1, "P", protocol_for_paradigm("finger_tapping"), mirrored=True)
    raw = v.begin_take("finger_tapping"); raw.write_bytes(b"r")
    v.mark_recorded("finger_tapping", raw)
    seg = v.confirm_step("finger_tapping")
    assert analysis_source(v, seg) == (str(raw), 0.0, 20.0, True, "raw")
    # … the archive clip once the raw take is gone
    clip = tmp_path / "seg_001.mp4"; clip.write_bytes(b"c")
    seg.clip_path = str(clip); raw.unlink(); seg.source_path = ""
    assert analysis_source(v, seg)[0] == str(clip) and analysis_source(v, seg)[4] == "clip"

    # import cut: the imported file with the segment's range, under its own flag
    vi = VideoSession.create(1, "P")
    video = tmp_path / "import.mp4"; video.write_bytes(b"v")
    vi.set_video(str(video), "handy.mp4"); vi.mirrored = False
    cut = vi.add_segment("Tapping", 2.5, 9.0, paradigm="finger_tapping", hand="left")
    assert analysis_source(vi, cut) == (str(video), 2.5, 9.0, False, "import")
    # fallback: the extracted clip
    video.unlink(); cut.clip_path = str(clip)
    assert analysis_source(vi, cut) == (str(clip), 0.0, 6.5, False, "clip")
    cut.clip_path = ""
    assert analysis_source(vi, cut)[0] == ""


def test_import_cut_runs_through_the_same_pipeline(qapp, tmp_path, monkeypatch):
    """A cut with a paradigm is analysed on the imported original, gets raw
    JSON + result + analysed_on='import', and lands in the record; the
    compact/cleanup stages leave an import alone."""
    from ui.segment_pipeline import SegmentPipeline
    exported, started = [], []
    monkeypatch.setattr("video.export.export_or_update", lambda s, seg, key: exported.append(key))
    monkeypatch.setattr("ui.results_screen.save_raw_data",
                        lambda test, code, features: tmp_path / "raw.json")
    (tmp_path / "raw.json").write_text("{}")
    vi = VideoSession.create(1, "P")
    video = tmp_path / "import.mp4"; video.write_bytes(b"v")
    vi.set_video(str(video), "handy.mp4"); vi.mirrored = True
    cut = vi.add_segment("Tapping", 1.0, 4.0, paradigm="finger_tapping", hand="right")
    clip = tmp_path / "seg_001.mp4"; clip.write_bytes(b"c"); cut.clip_path = str(clip)

    pl = SegmentPipeline()
    monkeypatch.setattr(pl.runner, "start", lambda *a, **k: started.append((a, k)))
    texts, finished, done = [], [], []
    pl.stageText.connect(texts.append)
    pl.analysisFinished.connect(lambda s, seg, f: finished.append((seg.id, f)))
    pl.jobDone.connect(lambda s, seg: done.append(seg.id))
    pl.enqueue(vi, cut, analyse=True)
    assert pl.analysing and pl.analysing_segment() is cut and pl.analysing_step_id() == ""
    (a, k), = started
    assert a == (str(video), 1.0, 4.0, "finger_tapping") and k["hand"] == "right" and k["mirrored"] is True
    assert "auf dem importierten Original" in texts[-1]

    class FakeTest:
        def test_type(self): return "finger_tapping"
    pl._on_analysis_finished(FakeTest(), {"mpi": 0.66})
    res = cut.results["finger_tapping"]
    assert res["analysed_on"] == "import" and res["features"] == {"mpi": 0.66}
    assert res["raw_path"].endswith("raw.json") and res["analysis"]["mirror"] is True
    assert exported == ["finger_tapping"] and finished == [("seg_001", {"mpi": 0.66})]
    assert done == ["seg_001"] and not pl.busy            # no compact, no cleanup for imports
    assert clip.is_file() and video.is_file()


def test_steps_paradigm_wins_over_the_segment_copy(qapp, tmp_path, monkeypatch):
    from ui.segment_pipeline import SegmentPipeline
    v = VideoSession.create_recording(1, "P", protocol_for_paradigm("finger_tapping"), mirrored=True)
    raw = v.begin_take("finger_tapping"); raw.write_bytes(b"r")
    v.mark_recorded("finger_tapping", raw)
    seg = v.confirm_step("finger_tapping")
    v.step("finger_tapping").paradigm = "hand_open_close"
    pl = SegmentPipeline()
    started = []
    monkeypatch.setattr(pl.runner, "start", lambda *a, **k: started.append(a))
    pl.enqueue(v, seg, step_id="finger_tapping", analyse=True)
    assert started[0][3] == "hand_open_close" and pl.analysing_step_id() == "finger_tapping"
