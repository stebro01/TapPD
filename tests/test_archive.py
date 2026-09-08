"""Compact archive clips for confirmed takes (video/archive.py).

The sidecar extractor is replaced by a stub that writes the destination file,
so these run without MediaPipe. What is tested is the decision logic: when the
segment is repointed, when the raw take may be removed, and that nothing is
lost when compaction fails.
"""

import pytest

from video import archive, store
from video.protocol import protocol_for_paradigm
from video.store import VideoSession


class _Clip:
    deidentified = True
    extra = {"thumb": "t.jpg"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VIDEO_SESSIONS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def stub_extractor(monkeypatch):
    """Extractor that 'compresses' by writing a tiny file; records its calls."""
    calls = []

    class Stub:
        def extract(self, src, start_s, end_s, dest, deface=None):
            calls.append((src, start_s, end_s, dest))
            with open(dest, "wb") as f:
                f.write(b"compact")
            return _Clip()

    import video.extractor as ex
    monkeypatch.setattr(ex, "VideoSegmentExtractor", Stub)
    return calls


def _confirmed_session():
    s = VideoSession.create_recording(1, "P001", protocol_for_paradigm("finger_tapping"),
                                      mirrored=True)
    s.db_session_id = 3
    raw = s.begin_take("finger_tapping")
    raw.write_bytes(b"x" * 1000)             # the "raw take"
    s.mark_recorded("finger_tapping", raw)
    seg = s.confirm_step("finger_tapping")
    return s, seg, raw


def test_confirm_records_the_raw_take_as_source():
    s, seg, raw = _confirmed_session()

    assert seg.recorded
    assert seg.source_path == str(raw)
    assert seg.clip_path == str(raw)          # nothing compact yet
    assert seg.analysis_path == str(raw)


def test_compact_repoints_clip_and_keeps_source(stub_extractor):
    s, seg, raw = _confirmed_session()

    assert archive.compact_take(s, seg)

    assert seg.clip_path != str(raw) and seg.clip_path.endswith(f"{seg.id}.mp4")
    assert seg.source_path == str(raw)
    assert seg.deidentified is True
    assert seg.thumb_path == "t.jpg"
    assert seg.analysis_path == str(raw)     # analysis still prefers the raw take
    src, start, end, dest = stub_extractor[0]
    assert (src, start, end) == (str(raw), 0.0, seg.duration_s)


def test_compact_failure_leaves_the_segment_untouched(monkeypatch):
    class Broken:
        def extract(self, *a, **k):
            return None
    import video.extractor as ex
    monkeypatch.setattr(ex, "VideoSegmentExtractor", Broken)
    s, seg, raw = _confirmed_session()

    assert not archive.compact_take(s, seg)
    assert seg.clip_path == str(raw)
    assert raw.is_file()


def test_compact_ignores_imported_segments(stub_extractor):
    s = VideoSession.create(1, "P002")
    seg = s.add_segment("Import", 1.0, 3.0)

    assert not archive.compact_take(s, seg)
    assert stub_extractor == []


def test_discard_only_after_a_distinct_compact_clip_exists(monkeypatch, stub_extractor):
    monkeypatch.setattr(archive, "cfg", lambda *k, default=None: False)   # keep_raw_take False
    s, seg, raw = _confirmed_session()

    assert not archive.discard_raw_take(s, seg)   # not compacted yet → keep
    assert raw.is_file()

    archive.compact_take(s, seg)
    assert archive.discard_raw_take(s, seg)

    assert not raw.is_file()
    assert seg.source_path == ""
    assert seg.analysis_path == seg.clip_path      # falls back to the compact clip
    # the step now points at the compact clip, so review still has a file
    assert s.step("finger_tapping").clip_path == seg.clip_path


def test_discard_respects_keep_raw_take(monkeypatch, stub_extractor):
    monkeypatch.setattr(archive, "cfg", lambda *k, default=None: True)    # keep_raw_take True
    s, seg, raw = _confirmed_session()
    archive.compact_take(s, seg)

    assert not archive.discard_raw_take(s, seg)
    assert raw.is_file()
    assert seg.source_path == str(raw)


def test_source_path_survives_a_round_trip(stub_extractor):
    s, seg, raw = _confirmed_session()
    archive.compact_take(s, seg)
    back = VideoSession.load(str(s.save()))

    b = back.segments[0]
    assert b.recorded and b.source_path == str(raw) and b.clip_path == seg.clip_path
