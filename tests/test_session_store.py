"""storage.session_store: JSON round trip and the flat CSV export."""

import csv
import json

import pytest

from storage import session_store as ss
from storage.session_store import SessionResult, export_csv, load_session, save_session


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "DATA_DIR", tmp_path / "sessions")
    return tmp_path


def _result(**over):
    kw = dict(patient_id="P001", test_type="finger_tapping", hand="right", duration_s=10.0,
              timestamp="2026-09-08T10:00:00", features={"mpi": 0.7, "n_taps": 42},
              raw_data=[{"t": 0.0, "d": 12.5}])
    kw.update(over)
    return SessionResult(**kw)


def test_timestamp_is_filled_when_missing():
    r = SessionResult(patient_id="P", test_type="t", hand="left", duration_s=1.0)
    assert r.timestamp and "T" in r.timestamp


def test_save_names_the_file_by_patient_test_and_time():
    path = save_session(_result())
    assert path.name == "P001_finger_tapping_20260908_100000.json"
    assert path.parent == ss.DATA_DIR


def test_raw_frames_are_dropped_unless_asked_for():
    slim = json.loads(save_session(_result()).read_text())
    assert "raw_data" not in slim

    full = json.loads(save_session(_result(patient_id="P002"), include_raw=True).read_text())
    assert full["raw_data"] == [{"t": 0.0, "d": 12.5}]


def test_round_trip():
    path = save_session(_result(), include_raw=True)
    back = load_session(path)
    assert back == _result()


def test_csv_export_appends_rows_with_one_header(tmp_path):
    out = tmp_path / "export" / "features.csv"
    export_csv(_result(), out)
    export_csv(_result(hand="left", features={"mpi": 0.6, "n_taps": 30}), out)

    rows = list(csv.DictReader(out.open(newline="")))
    assert len(rows) == 2
    assert rows[0]["hand"] == "right" and rows[0]["mpi"] == "0.7"
    assert rows[1]["hand"] == "left" and rows[1]["n_taps"] == "30"
    assert out.read_text().count("patient_id") == 1        # header written once
