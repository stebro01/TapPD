"""Curve images for reports: the measurement's time course as PNG bytes.

Reads the raw-data JSON a measurement points to (same file the detail dialog
plots) and renders one compact panel with Matplotlib's Agg backend — no Qt
needed, so this also runs in a batch export.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os

import numpy as np

log = logging.getLogger(__name__)

_LABELS = {
    "finger_tapping": "Daumen-Zeigefinger-Distanz (mm)",
    "hand_open_close": "Fingerspreizung (mm)",
    "pronation_supination": "Rotationswinkel (°)",
}


def _thumb_index(frame: dict) -> float:
    fingers = frame.get("fingers", [])
    if len(fingers) >= 2:
        t, i = fingers[0]["tip_position"], fingers[1]["tip_position"]
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(t, i)))
    return 0.0


def _spread(frame: dict) -> float:
    fingers = frame.get("fingers", [])
    palm = frame.get("palm_position", [0, 0, 0])
    if not fingers:
        return 0.0
    d = [math.sqrt(sum((a - b) ** 2 for a, b in zip(f.get("tip_position", palm), palm)))
         for f in fingers]
    return sum(d) / len(d)


def _roll(frame: dict) -> float:
    pn = frame.get("palm_normal", [0, -1, 0])
    return math.degrees(math.atan2(pn[0], -pn[1]))


_METRIC = {"finger_tapping": _thumb_index, "hand_open_close": _spread,
           "pronation_supination": _roll}


def _series(test_type: str, data: dict):
    """[(label, t, values)] for the paradigm, or [] when nothing plots."""
    from analysis.signal_processing import remove_outliers, resample_to_uniform
    fs = float(data.get("sample_rate") or 30.0)
    out = []
    if test_type in _METRIC:
        frames = data.get("frames") or []
        if len(frames) < 5:
            return []
        ts = np.array([f["timestamp_us"] for f in frames], dtype=np.int64)
        vals = np.array([_METRIC[test_type](f) for f in frames], dtype=float)
        t, v = resample_to_uniform(ts, vals, fs)
        out.append((_LABELS[test_type], t, remove_outliers(v)))
    elif test_type in ("postural_tremor", "rest_tremor"):
        from analysis.signal_processing import bandpass_filter, detrend
        for side, key in (("rechts", "right_frames"), ("links", "left_frames")):
            frames = data.get(key) or []
            if len(frames) < 30:
                continue
            ts = np.array([f["timestamp_us"] for f in frames], dtype=np.int64)
            comps = []
            t = None
            for axis in range(3):
                vals = np.array([f["palm_position"][axis] for f in frames], dtype=float)
                t, v = resample_to_uniform(ts, vals, fs)
                try:
                    comps.append(bandpass_filter(detrend(remove_outliers(v)), fs, 3.0, 12.0) ** 2)
                except ValueError:
                    comps = []
                    break
            if comps and t is not None:
                out.append((f"Tremor-Amplitude {side} (mm)", t, np.sqrt(sum(comps))))
    return out


def curve_png(test_type: str, raw_path: str, width_in: float = 6.4, height_in: float = 2.2) -> bytes | None:
    """PNG of the time course, or None when the raw file is missing/unplottable."""
    if not raw_path or not os.path.isfile(raw_path) or not raw_path.lower().endswith(".json"):
        return None
    try:
        with open(raw_path, encoding="utf-8") as f:
            data = json.load(f)
        series = _series(test_type, data)
        if not series:
            return None
        import matplotlib
        matplotlib.use("Agg", force=False)
        from matplotlib.figure import Figure
        fig = Figure(figsize=(width_in, height_in), dpi=110)
        ax = fig.add_subplot(111)
        colors = ["#1976D2", "#E53935"]
        for i, (label, t, v) in enumerate(series):
            ax.plot(t, v, color=colors[i % 2], linewidth=0.9, label=label)
        ax.set_xlabel("Zeit (s)", fontsize=8)
        ax.set_ylabel(series[0][0] if len(series) == 1 else "Amplitude (mm)", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3)
        if len(series) > 1:
            ax.legend(fontsize=7)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        return buf.getvalue()
    except Exception:
        log.debug("Kurve konnte nicht gezeichnet werden: %s", raw_path, exc_info=True)
        return None
