"""Auto-export of analysed takes, and re-analysis updating the same record."""

import pytest

from storage import database as dbmod
from storage.database import get_measurements, get_sessions
from video import store
from video.export import export_or_update
from video.protocol import protocol_for_paradigm
from video.store import VideoSession


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, conn):
    monkeypatch.setattr(store, "VIDEO_SESSIONS_DIR", tmp_path)
    # route get_db() to the in-memory test connection
    monkeypatch.setattr(dbmod, "get_db", lambda: _NonClosing(conn))
    monkeypatch.setattr("video.export.get_db", lambda: _NonClosing(conn))
    return conn


class _NonClosing:
    """Proxy so code that calls conn.close() does not kill the shared test DB."""

    def __init__(self, conn):
        self._c = conn

    def __getattr__(self, name):
        return getattr(self._c, name)

    def close(self):
        pass


def _analysed_session(conn):
    from tests.conftest import make_patient_row, make_session_row
    pid = make_patient_row(conn, code="PX1")
    sid = make_session_row(conn, pid)
    v = VideoSession.create_recording(pid, "PX1", protocol_for_paradigm("finger_tapping"),
                                      mirrored=True)
    v.db_session_id = sid
    v.mark_recorded("finger_tapping", "/x/take.mp4")
    seg = v.confirm_step("finger_tapping")
    seg.results["finger_tapping"] = {"features": {"mpi": 0.61, "tap_frequency_hz": 4.0},
                                     "recorded_at": "2026-09-08T10:00:00",
                                     "raw_path": "", "source_kind": "video"}
    return pid, sid, v, seg


def test_first_export_creates_a_measurement(conn):
    pid, sid, v, seg = _analysed_session(conn)

    m = export_or_update(v, seg, "finger_tapping")

    ms = get_measurements(conn, pid)
    assert [x.id for x in ms] == [m.id]
    assert ms[0].session_id == sid and ms[0].test_type == "finger_tapping"
    assert ms[0].features["mpi"] == 0.61
    assert seg.results["finger_tapping"]["measurement_id"] == m.id


def test_reanalysis_updates_the_same_measurement(conn):
    pid, sid, v, seg = _analysed_session(conn)
    first = export_or_update(v, seg, "finger_tapping")

    seg.results["finger_tapping"]["features"] = {"mpi": 0.72, "tap_frequency_hz": 4.4}
    second = export_or_update(v, seg, "finger_tapping")

    ms = get_measurements(conn, pid)
    assert second.id == first.id
    assert len(ms) == 1                      # no duplicate
    assert ms[0].features["mpi"] == 0.72


def test_relabel_drops_old_results():
    v = VideoSession.create_recording(1, "PX2", protocol_for_paradigm("finger_tapping"),
                                      mirrored=True)
    v.mark_recorded("finger_tapping", "/x/t.mp4")
    seg = v.confirm_step("finger_tapping")
    seg.results["finger_tapping"] = {"features": {"mpi": 0.5}}

    st = v.relabel_step("finger_tapping", "hand_open_close", "left")

    assert (st.paradigm, st.hand) == ("hand_open_close", "left")
    assert (seg.paradigm, seg.hand) == ("hand_open_close", "left")
    assert seg.results == {}
    assert seg.clip_path == "/x/t.mp4"        # the take itself is untouched
