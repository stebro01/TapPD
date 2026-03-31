"""Unified recording module: hand detection → countdown → recording → features.

This module is driven by test_config.yaml. It replaces the per-test
compute_features() logic with a config-driven pipeline.
"""

import math
import logging
import threading
import time
from typing import Any

import numpy as np
from numpy.typing import NDArray

from capture.base_capture import BaseCaptureDevice, HandFrame
from motor_tests.config import get_test_config, get_hand_detection_config
from analysis.signal_processing import (
    bandpass_filter,
    compute_amplitude_decrement,
    compute_fft,
    detect_onset_offset,
    detrend,
    detect_peaks,
    peak_to_trough_amplitudes,
    remove_outliers,
    resample_to_uniform,
)

log = logging.getLogger(__name__)


# ── Metric extraction from HandFrame ────────────────────────────────

def extract_metric(frame: HandFrame, metric_type: str, **kwargs) -> float:
    """Extract a single metric value from a HandFrame based on config."""
    if metric_type == "thumb_index_distance":
        if len(frame.fingers) >= 2:
            t = frame.fingers[0].tip_position
            i = frame.fingers[1].tip_position
            return math.sqrt(sum((a - b) ** 2 for a, b in zip(t, i)))
        return 0.0

    elif metric_type == "mean_finger_spread":
        if not frame.fingers:
            return 0.0
        return float(np.mean([
            math.sqrt(sum((ft - fp) ** 2
                          for ft, fp in zip(f.tip_position, frame.palm_position)))
            for f in frame.fingers
        ]))

    elif metric_type == "palm_roll_angle":
        nx, ny = frame.palm_normal[0], frame.palm_normal[1]
        return math.degrees(math.atan2(nx, -ny))

    elif metric_type == "grab_strength":
        return frame.grab_strength

    elif metric_type == "palm_displacement":
        px, py, pz = frame.palm_position
        base_y = kwargs.get("base_y", 200.0)
        return math.sqrt(px ** 2 + (py - base_y) ** 2 + pz ** 2)

    else:
        raise ValueError(f"Unknown metric type: {metric_type}")


# ── Hand Detection ──────────────────────────────────────────────────

class HandDetector:
    """Detects hand presence above the sensor before starting a test."""

    def __init__(self, capture: BaseCaptureDevice, bilateral: bool, hand: str = "right"):
        self.capture = capture
        self.bilateral = bilateral
        self.hand = hand
        self._cfg = get_hand_detection_config()
        self._detected = False
        self._stable_count = 0
        self._lock = threading.Lock()
        self._last_frame_time = 0.0

    @property
    def min_confidence(self) -> float:
        return self._cfg.get("min_confidence", 0.5)

    @property
    def require_stable(self) -> int:
        return self._cfg.get("require_stable_frames", 10)

    def check_frame(self, frame: HandFrame) -> bool:
        """Process a frame and return True if hand detection criteria met."""
        if self._detected:
            return True

        # Check hand type matches what we need
        if not self.bilateral and frame.hand_type != self.hand:
            return False

        # Check confidence
        if frame.confidence < self.min_confidence:
            with self._lock:
                self._stable_count = 0
            return False

        with self._lock:
            self._stable_count += 1
            if self._stable_count >= self.require_stable:
                self._detected = True
                return True
        return False

    @property
    def is_detected(self) -> bool:
        return self._detected

    @property
    def progress(self) -> float:
        """0.0 to 1.0 detection progress."""
        with self._lock:
            return min(1.0, self._stable_count / max(1, self.require_stable))

    def reset(self) -> None:
        with self._lock:
            self._detected = False
            self._stable_count = 0


# ── Config-Driven Feature Computation ───────────────────────────────

def compute_features_from_config(
    test_key: str,
    frames: list[HandFrame],
    fs: float,
    left_frames: list[HandFrame] | None = None,
    right_frames: list[HandFrame] | None = None,
) -> dict[str, float]:
    """Compute features for any test type using YAML configuration.

    For bilateral tests, pass left_frames and right_frames.
    For unilateral tests, pass frames.
    """
    log.info("Feature-Berechnung: %s (bilateral=%s, frames=%d, fs=%.1f)",
             test_key, cfg.get("bilateral", False) if (cfg := get_test_config(test_key)) else False,
             len(frames), fs)
    cfg = get_test_config(test_key)
    analysis = cfg.get("analysis", {})
    is_bilateral = cfg.get("bilateral", False)

    if is_bilateral:
        log.debug("Bilateral: L=%d R=%d Frames", len(left_frames or []), len(right_frames or []))
        return _compute_bilateral(cfg, left_frames or [], right_frames or [], fs)
    else:
        return _compute_unilateral(cfg, frames, fs)


def _filter_and_trim(
    frames: list[HandFrame], fs: float, analysis: dict
) -> list[HandFrame]:
    """Apply confidence filter and warmup/cooldown trimming."""
    # Confidence filter
    frames = [f for f in frames if f.confidence >= 0.3]

    if len(frames) < analysis.get("min_frames", 20):
        return frames

    # Warmup trim
    warmup_s = analysis.get("warmup_trim_s", 0.0)
    cooldown_s = analysis.get("cooldown_trim_s", 0.0)

    if warmup_s > 0 or cooldown_s > 0:
        t0 = frames[0].timestamp_us
        t_end = frames[-1].timestamp_us
        duration_us = t_end - t0
        warmup_us = int(warmup_s * 1_000_000)
        cooldown_us = int(cooldown_s * 1_000_000)

        if warmup_us + cooldown_us < duration_us:
            frames = [
                f for f in frames
                if f.timestamp_us >= t0 + warmup_us
                and f.timestamp_us <= t_end - cooldown_us
            ]
            log.debug(
                "Trimmed: warmup=%.1fs cooldown=%.1fs → %d frames",
                warmup_s, cooldown_s, len(frames),
            )

    return frames


def _prepare_signal(
    frames: list[HandFrame],
    cfg: dict,
    fs: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]] | None:
    """Extract metric, resample, clean, return (time, signal) or None."""
    analysis = cfg.get("analysis", {})
    capture_cfg = cfg.get("capture", {})
    metric_type = capture_cfg.get("primary_metric", "thumb_index_distance")
    metric_kwargs = {}
    if "base_y" in capture_cfg:
        metric_kwargs["base_y"] = capture_cfg["base_y"]

    frames = _filter_and_trim(frames, fs, analysis)

    min_frames = analysis.get("min_frames", 20)
    if len(frames) < min_frames:
        log.warning("Too few frames: %d < %d", len(frames), min_frames)
        return None

    timestamps = np.array([f.timestamp_us for f in frames], dtype=np.int64)
    values = np.array([extract_metric(f, metric_type, **metric_kwargs) for f in frames])

    t_u, v_u = resample_to_uniform(timestamps, values, fs)
    if len(v_u) < min_frames:
        return None

    # Outlier removal
    if analysis.get("outlier_removal", True):
        v_u = remove_outliers(v_u)

    # Auto onset/offset detection
    onset_s = 0.0
    offset_s = t_u[-1] if len(t_u) > 0 else 0.0
    onset_cfg = analysis.get("onset_detection", {})
    if onset_cfg.get("enabled", True):
        window_s = onset_cfg.get("window_s", 0.5)
        threshold_pct = onset_cfg.get("threshold_pct", 20.0)
        onset_idx, offset_idx = detect_onset_offset(v_u, fs, window_s, threshold_pct)
        onset_s = t_u[onset_idx]
        offset_s = t_u[min(offset_idx, len(t_u) - 1)]
        if onset_idx > 0 or offset_idx < len(v_u):
            log.debug(
                "Onset detection: %.2fs–%.2fs (of %.2fs)",
                onset_s, offset_s, t_u[-1],
            )
            t_u = t_u[onset_idx:offset_idx]
            v_u = v_u[onset_idx:offset_idx]

    return t_u, v_u, onset_s, offset_s


def _compute_unilateral(
    cfg: dict, frames: list[HandFrame], fs: float
) -> dict[str, float]:
    """Compute features for a unilateral (single-hand) test."""
    result = _prepare_signal(frames, cfg, fs)
    if result is None:
        return _empty_features(cfg)

    t_u, v_u, onset_s, offset_s = result
    analysis = cfg.get("analysis", {})

    # Detrend for peak detection
    if analysis.get("detrend", True):
        v_detrended = detrend(v_u)
    else:
        v_detrended = v_u

    # Peak detection
    peak_cfg = analysis.get("peak_detection", {})
    min_dist_factor = peak_cfg.get("min_distance_factor", 6)
    min_prom_pct = peak_cfg.get("min_prominence_pct", 15)
    min_dist = max(1, int(fs / min_dist_factor))
    signal_range = float(np.max(v_detrended) - np.min(v_detrended))
    min_prominence = signal_range * min_prom_pct / 100.0

    peaks, _ = detect_peaks(v_detrended, min_distance=min_dist, prominence=min_prominence)
    troughs, _ = detect_peaks(-v_detrended, min_distance=min_dist, prominence=min_prominence)

    # Compute each feature
    features: dict[str, float] = {}
    for feat_def in cfg.get("features", []):
        key = feat_def["key"]
        method = feat_def["method"]
        features[key] = _compute_single_feature(
            method, t_u, v_u, v_detrended, peaks, troughs, fs
        )

    # Store onset/offset times for display in detail dialog
    features["_onset_s"] = onset_s
    features["_offset_s"] = offset_s

    # Compute MPI if configured
    mpi_cfg = cfg.get("mpi")
    if mpi_cfg:
        features = _compute_mpi(mpi_cfg, features)

    return features


def _compute_single_feature(
    method: str,
    t: NDArray, signal: NDArray, detrended: NDArray,
    peaks: NDArray, troughs: NDArray,
    fs: float,
) -> float:
    """Compute one feature value by method name."""
    if method == "peak_frequency":
        if len(peaks) < 2:
            return 0.0
        iti = np.diff(t[peaks])
        iti = iti[iti > 0]
        return float(1.0 / np.mean(iti)) if len(iti) > 0 else 0.0

    elif method == "peak_to_trough_mean":
        amps = peak_to_trough_amplitudes(signal, peaks, troughs)
        return float(np.mean(amps)) if len(amps) > 0 else 0.0

    elif method == "peak_to_trough_mean_x2":
        amps = peak_to_trough_amplitudes(detrended, peaks, troughs)
        return float(np.mean(amps) * 2.0) if len(amps) > 0 else 0.0

    elif method == "amplitude_slope_normalized":
        if len(peaks) < 3:
            return 0.0
        peak_vals = signal[peaks]
        return float(compute_amplitude_decrement(peak_vals))

    elif method == "interval_cv":
        if len(peaks) < 2:
            return 0.0
        iti = np.diff(t[peaks])
        iti = iti[iti > 0]
        if len(iti) == 0 or np.mean(iti) == 0:
            return 0.0
        return float(np.std(iti) / np.mean(iti))

    elif method == "mean_abs_velocity":
        v = np.abs(np.gradient(signal, 1.0 / fs))
        return float(np.mean(v))

    elif method == "mean_cycle_duration":
        if len(peaks) < 2:
            return 0.0
        durations = np.diff(t[peaks])
        durations = durations[durations > 0]
        return float(np.mean(durations)) if len(durations) > 0 else 0.0

    elif method == "peak_count":
        return int(len(peaks))

    else:
        log.warning("Unknown feature method: %s", method)
        return 0.0


def _compute_mpi(mpi_cfg: dict, features: dict[str, float]) -> dict[str, float]:
    """Compute Motor Performance Index from existing features.

    Returns a new dict with 'mpi' as the first key, preserving all others.
    MPI is a weighted composite score from 0.0 (severely impaired) to 1.0 (healthy).
    """
    weights = mpi_cfg.get("weights", {})
    components = mpi_cfg.get("components", {})
    weighted_sum = 0.0
    total_weight = 0.0

    for comp_name, weight in weights.items():
        comp = components.get(comp_name)
        if not comp:
            continue
        raw = features.get(comp["feature_key"], 0.0)
        min_v = comp["min_val"]
        max_v = comp["max_val"]

        if max_v == min_v:
            sub_score = 0.5
        else:
            sub_score = (raw - min_v) / (max_v - min_v)

        if comp.get("invert", False):
            sub_score = 1.0 - sub_score

        sub_score = max(0.0, min(1.0, sub_score))
        weighted_sum += weight * sub_score
        total_weight += weight

    mpi = weighted_sum / total_weight if total_weight > 0 else 0.0
    mpi = round(float(np.clip(mpi, 0.0, 1.0)), 3)

    log.info("MPI berechnet: %.3f (Komponenten: %s)",
             mpi, {k: f"{features.get(components[k]['feature_key'], 0.0):.2f}"
                    for k in weights if k in components})

    return {"mpi": mpi, **features}



def _compute_bilateral(
    cfg: dict,
    left_frames: list[HandFrame],
    right_frames: list[HandFrame],
    fs: float,
) -> dict[str, float]:
    """Compute features for a bilateral tremor test."""
    analysis = cfg.get("analysis", {})
    features: dict[str, float] = {}

    for prefix, hand_frames in [("R", right_frames), ("L", left_frames)]:
        hand_result = _compute_tremor_hand(cfg, hand_frames, fs)
        for key, val in hand_result.items():
            features[f"{prefix}_{key}"] = val

    # Asymmetry indices
    for asym_def in cfg.get("features", {}).get("asymmetry", []):
        r_key = asym_def["right_key"]
        l_key = asym_def["left_key"]
        r_val = features.get(r_key, 0.0)
        l_val = features.get(l_key, 0.0)
        total = r_val + l_val
        features[asym_def["key"]] = (r_val - l_val) / total if total > 1e-6 else 0.0

    return features


def _compute_tremor_hand(
    cfg: dict, frames: list[HandFrame], fs: float
) -> dict[str, float]:
    """Compute tremor features for a single hand."""
    analysis = cfg.get("analysis", {})
    capture_cfg = cfg.get("capture", {})
    per_hand_defs = cfg.get("features", {}).get("per_hand", [])

    frames = _filter_and_trim(frames, fs, analysis)
    min_frames = analysis.get("min_frames", 30)

    if len(frames) < min_frames:
        return {d["key"]: 0.0 for d in per_hand_defs}

    timestamps = np.array([f.timestamp_us for f in frames], dtype=np.int64)
    palm_x = np.array([f.palm_position[0] for f in frames])
    palm_y = np.array([f.palm_position[1] for f in frames])
    palm_z = np.array([f.palm_position[2] for f in frames])
    roll = np.array([math.atan2(f.palm_normal[0], -f.palm_normal[1]) for f in frames])

    _, px = resample_to_uniform(timestamps, palm_x, fs)
    _, py = resample_to_uniform(timestamps, palm_y, fs)
    _, pz = resample_to_uniform(timestamps, palm_z, fs)
    _, roll_u = resample_to_uniform(timestamps, roll, fs)

    if len(px) < min_frames:
        return {d["key"]: 0.0 for d in per_hand_defs}

    # Clean
    if analysis.get("outlier_removal", True):
        px, py, pz, roll_u = (remove_outliers(s) for s in (px, py, pz, roll_u))

    if analysis.get("detrend", True):
        px, py, pz, roll_u = (detrend(s) for s in (px, py, pz, roll_u))

    # Bandpass filter
    bp = analysis.get("bandpass", {})
    low = bp.get("low_hz", 3.0)
    high = bp.get("high_hz", 12.0)
    try:
        px_f = bandpass_filter(px, fs, low, high)
        py_f = bandpass_filter(py, fs, low, high)
        pz_f = bandpass_filter(pz, fs, low, high)
        roll_f = bandpass_filter(roll_u, fs, low, high)
    except ValueError:
        return {d["key"]: 0.0 for d in per_hand_defs}

    # Compute each per-hand feature
    result: dict[str, float] = {}
    for feat_def in per_hand_defs:
        key = feat_def["key"]
        method = feat_def["method"]

        if method == "fft_dominant_frequency":
            freqs, mx = compute_fft(px_f, fs)
            _, my = compute_fft(py_f, fs)
            _, mz = compute_fft(pz_f, fs)
            combined = mx ** 2 + my ** 2 + mz ** 2
            band = analysis.get("fft", {}).get("dominant_freq_band", [low, high])
            mask = (freqs >= band[0]) & (freqs <= band[1])
            if np.any(mask):
                result[key] = float(freqs[mask][np.argmax(combined[mask])])
            else:
                result[key] = 0.0

        elif method == "rms_3d_amplitude":
            mag = np.sqrt(px_f ** 2 + py_f ** 2 + pz_f ** 2)
            result[key] = float(np.sqrt(np.mean(mag ** 2)))

        elif method == "rms_roll_amplitude":
            result[key] = float(np.sqrt(np.mean(np.degrees(roll_f) ** 2)))

        elif method == "spectral_power_integral":
            freqs, mx = compute_fft(px_f, fs)
            _, my = compute_fft(py_f, fs)
            _, mz = compute_fft(pz_f, fs)
            combined = mx ** 2 + my ** 2 + mz ** 2
            band = analysis.get("fft", {}).get("dominant_freq_band", [low, high])
            mask = (freqs >= band[0]) & (freqs <= band[1])
            df = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0
            result[key] = float(np.sum(combined[mask] * df)) if np.any(mask) else 0.0

        else:
            log.warning("Unknown tremor feature method: %s", method)
            result[key] = 0.0

    return result


def _empty_features(cfg: dict) -> dict[str, float]:
    """Return zero-valued features dict based on config."""
    features = {}
    if cfg.get("mpi"):
        features["mpi"] = 0.0
    if cfg.get("bilateral"):
        for prefix in ("R", "L"):
            for d in cfg.get("features", {}).get("per_hand", []):
                features[f"{prefix}_{d['key']}"] = 0.0
        for d in cfg.get("features", {}).get("asymmetry", []):
            features[d["key"]] = 0.0
    else:
        for d in cfg.get("features", []):
            features[d["key"]] = 0.0
    return features
