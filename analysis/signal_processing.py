"""Signal processing utilities: filtering, FFT, peak detection, resampling."""

import logging

import numpy as np
from numpy.typing import NDArray
from scipy import signal as sp_signal
from scipy.stats import linregress

log = logging.getLogger(__name__)


def bandpass_filter(
    data: NDArray[np.float64],
    fs: float,
    low: float,
    high: float,
    order: int = 4,
) -> NDArray[np.float64]:
    """Apply a Butterworth bandpass filter."""
    nyq = fs / 2.0
    low_n = low / nyq
    high_n = high / nyq
    low_n = max(low_n, 0.001)
    high_n = min(high_n, 0.999)
    if len(data) < 3 * order:
        log.debug("Bandpass uebersprungen: zu wenig Datenpunkte (%d < %d)", len(data), 3 * order)
        return data
    sos = sp_signal.butter(order, [low_n, high_n], btype="band", output="sos")
    return sp_signal.sosfiltfilt(sos, data)


def detrend(data: NDArray[np.float64]) -> NDArray[np.float64]:
    """Remove linear trend from signal (drift correction)."""
    return sp_signal.detrend(data, type="linear")


def compute_fft(
    data: NDArray[np.float64], fs: float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Compute windowed FFT and return (frequencies, magnitudes)."""
    n = len(data)
    # Hann window reduces spectral leakage
    window = np.hanning(n)
    windowed = data * window
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    fft_vals = np.fft.rfft(windowed)
    # Correct for window amplitude loss (factor of 2 for single-sided)
    magnitudes = np.abs(fft_vals) * 2.0 / np.sum(window)
    return freqs, magnitudes


def detect_peaks(
    data: NDArray[np.float64],
    min_distance: int = 10,
    threshold: float | None = None,
    prominence: float | None = None,
) -> tuple[NDArray[np.intp], NDArray[np.float64]]:
    """Find peaks in signal. Returns (peak_indices, peak_values)."""
    kwargs: dict = {"distance": min_distance}
    if threshold is not None:
        kwargs["height"] = threshold
    if prominence is not None:
        kwargs["prominence"] = prominence
    peaks, properties = sp_signal.find_peaks(data, **kwargs)
    return peaks, data[peaks]


def compute_amplitude_decrement(amplitudes: NDArray[np.float64]) -> float:
    """Linear regression slope of peak amplitudes (negative = decrement).

    Returns slope normalized by mean amplitude (relative decrement per cycle).
    """
    if len(amplitudes) < 3:
        return 0.0
    x = np.arange(len(amplitudes))
    result = linregress(x, amplitudes)
    mean_amp = np.mean(amplitudes)
    if mean_amp > 0:
        return float(result.slope / mean_amp)
    return float(result.slope)


def remove_outliers(
    values: NDArray[np.float64], z_threshold: float = 3.5
) -> NDArray[np.float64]:
    """Replace outliers (|z-score| > threshold) with interpolated values."""
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    if mad < 1e-10:
        return values
    modified_z = 0.6745 * (values - median) / mad
    mask = np.abs(modified_z) > z_threshold
    if not np.any(mask):
        return values
    cleaned = values.copy()
    good_idx = np.where(~mask)[0]
    bad_idx = np.where(mask)[0]
    if len(good_idx) >= 2:
        cleaned[bad_idx] = np.interp(bad_idx, good_idx, values[good_idx])
    return cleaned


def detect_onset_offset(
    signal: NDArray[np.float64],
    fs: float,
    window_s: float = 0.5,
    threshold_pct: float = 20.0,
) -> tuple[int, int]:
    """Detect movement onset and offset in a signal.

    Uses rolling standard deviation to find when meaningful movement starts/ends.
    Returns (onset_idx, offset_idx) — indices into the signal array.

    Args:
        signal: Uniformly sampled signal.
        fs: Sample rate.
        window_s: Rolling window size in seconds.
        threshold_pct: Percentage of overall signal std to use as threshold.
    """
    n = len(signal)
    win = max(3, int(window_s * fs))
    if n < win * 2:
        return 0, n

    # Rolling std (using convolution for efficiency)
    sig = signal - np.mean(signal)
    sq = sig ** 2
    kernel = np.ones(win) / win
    rolling_var = np.convolve(sq, kernel, mode="same")
    rolling_std = np.sqrt(np.maximum(rolling_var, 0))

    overall_std = np.std(signal)
    if overall_std < 1e-10:
        return 0, n

    threshold = overall_std * threshold_pct / 100.0

    # Onset: first index where rolling_std exceeds threshold
    above = rolling_std > threshold
    onset = 0
    for i in range(n):
        if above[i]:
            # Step back half a window to not cut into the first movement
            onset = max(0, i - win // 2)
            break

    # Offset: last index where rolling_std exceeds threshold
    offset = n
    for i in range(n - 1, -1, -1):
        if above[i]:
            offset = min(n, i + win // 2)
            break

    # Safety: ensure we keep at least 50% of the signal
    if (offset - onset) < n * 0.5:
        return 0, n

    return onset, offset


def peak_to_trough_amplitudes(
    signal: NDArray[np.float64],
    peaks: NDArray[np.intp],
    troughs: NDArray[np.intp],
) -> NDArray[np.float64]:
    """Compute peak-to-nearest-trough amplitude for each peak.

    For each peak, finds the nearest trough before and after, takes their mean,
    and returns the difference (peak - mean_trough). This gives the true
    movement amplitude per cycle.
    """
    if len(peaks) == 0 or len(troughs) == 0:
        return np.array([])
    amplitudes = []
    for p in peaks:
        before = troughs[troughs < p]
        after = troughs[troughs > p]
        vals = []
        if len(before) > 0:
            vals.append(signal[before[-1]])
        if len(after) > 0:
            vals.append(signal[after[0]])
        if vals:
            amplitudes.append(signal[p] - np.mean(vals))
    return np.array(amplitudes) if amplitudes else np.array([])


def resample_to_uniform(
    timestamps_us: NDArray[np.int64],
    values: NDArray[np.float64],
    target_fs: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Resample irregularly-spaced data to a uniform sample rate.

    Returns (uniform_timestamps_s, resampled_values).
    """
    times_s = (timestamps_us - timestamps_us[0]) / 1_000_000.0
    duration = times_s[-1]
    n_samples = int(duration * target_fs)
    if n_samples < 2:
        log.debug("Resampling uebersprungen: zu kurze Dauer (%.3fs)", duration)
        return times_s, values
    uniform_t = np.linspace(0, duration, n_samples)
    resampled = np.interp(uniform_t, times_s, values)
    log.debug("Resampled: %d -> %d Samples (%.1f Hz, %.2fs)", len(values), n_samples, target_fs, duration)
    return uniform_t, resampled
