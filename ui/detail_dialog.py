"""Detail dialog: feature table + analysis plots for a single measurement."""

import json
import math
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from analysis.signal_processing import (
    bandpass_filter,
    compute_fft,
    detrend,
    detect_peaks,
    remove_outliers,
    resample_to_uniform,
)
from storage.database import Measurement, Patient
from ui.feature_meta import FEATURE_META
from ui.theme import ACCENT, PRIMARY, TEXT_SECONDARY

COLOR_RIGHT = QColor(227, 242, 253)
COLOR_LEFT = QColor(255, 235, 238)


class DetailDialog(QDialog):
    def __init__(self, patient: Patient, measurement: Measurement,
                 siblings: list[Measurement] | None = None, parent=None):
        super().__init__(parent)
        self._patient = patient
        self._measurements = siblings if siblings and len(siblings) > 1 else [measurement]
        self._current_idx = self._measurements.index(measurement) if measurement in self._measurements else 0

        self.setWindowTitle(f"Details – {patient.display_name} – {measurement.test_type}")
        self.setMinimumSize(1100, 720)
        self.resize(1200, 780)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(20, 16, 20, 16)
        self._root_layout.setSpacing(10)

        # Header row with hand selector
        header_row = QHBoxLayout()
        self._hand_combo: QComboBox | None = None
        if len(self._measurements) > 1:
            # Determine label type: TMT uses Part A/B, others use hand L/R
            is_tmt = measurement.test_type.startswith("trail_making")
            lbl = QLabel("Teil:" if is_tmt else "Hand:")
            lbl.setStyleSheet("font-weight: 600;")
            header_row.addWidget(lbl)
            self._hand_combo = QComboBox()
            self._hand_combo.setMinimumWidth(120)
            for m in self._measurements:
                if is_tmt:
                    part_val = m.features.get("tmt_part", 1.0)
                    combo_str = "Teil A" if part_val == 1.0 else "Teil B"
                else:
                    combo_str = {"left": "Links", "right": "Rechts", "both": "Bilateral"}.get(m.hand, m.hand)
                self._hand_combo.addItem(combo_str)
            self._hand_combo.setCurrentIndex(self._current_idx)
            header_row.addWidget(self._hand_combo)
        header_row.addStretch()
        self._header_label = QLabel()
        self._header_label.setStyleSheet("font-size: 15px; font-weight: 700;")
        header_row.addWidget(self._header_label)
        header_row.addStretch()
        self._root_layout.addLayout(header_row)

        # Feature table
        self._feature_table = QTableWidget()
        self._feature_table.setColumnCount(3)
        self._feature_table.setHorizontalHeaderLabels(["Parameter", "Wert", "Einheit"])
        self._feature_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._feature_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._feature_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._feature_table.verticalHeader().setVisible(False)
        self._feature_table.setMinimumWidth(380)
        self._feature_table.setMaximumWidth(450)

        # Plot canvas
        self._figure = Figure(figsize=(7, 5), facecolor="#FAFAFA")
        self._canvas = FigureCanvasQTAgg(self._figure)

        content = QHBoxLayout()
        content.setSpacing(16)
        content.addWidget(self._feature_table)
        content.addWidget(self._canvas, stretch=1)
        self._root_layout.addLayout(content, stretch=1)

        # Show initial measurement
        self._show_measurement(self._measurements[self._current_idx])

        # Connect combo AFTER initial display to avoid double-trigger
        if self._hand_combo:
            self._hand_combo.currentIndexChanged.connect(self._switch_to)

    def _switch_to(self, idx: int) -> None:
        self._current_idx = idx
        self._show_measurement(self._measurements[idx])

    def _show_measurement(self, measurement: Measurement) -> None:
        self._onset_s = measurement.features.get("_onset_s")
        self._offset_s = measurement.features.get("_offset_s")

        # Update header
        if measurement.test_type.startswith("trail_making"):
            part_val = measurement.features.get("tmt_part", 1.0)
            detail_str = "Teil A" if part_val == 1.0 else "Teil B"
        else:
            detail_str = {"left": "Links", "right": "Rechts", "both": "Bilateral"}.get(measurement.hand, measurement.hand)
        self._header_label.setText(
            f"{self._patient.display_name}  –  {measurement.test_type} ({detail_str})  –  {measurement.recorded_at[:16]}"
        )

        # Update feature table
        features = {k: v for k, v in measurement.features.items() if not k.startswith("_")}
        self._feature_table.setRowCount(len(features))
        for row, (key, value) in enumerate(features.items()):
            meta = FEATURE_META.get(key, (key, ""))
            name_item = QTableWidgetItem(meta[0])
            val_item = QTableWidgetItem(f"{value:.4f}" if isinstance(value, float) else str(value))
            unit_item = QTableWidgetItem(meta[1])
            val_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if key.startswith("R_"):
                for item in (name_item, val_item, unit_item):
                    item.setBackground(COLOR_RIGHT)
            elif key.startswith("L_"):
                for item in (name_item, val_item, unit_item):
                    item.setBackground(COLOR_LEFT)
            self._feature_table.setItem(row, 0, name_item)
            self._feature_table.setItem(row, 1, val_item)
            self._feature_table.setItem(row, 2, unit_item)

        # Update plots
        self._figure.clear()
        self.figure = self._figure  # for _plot_from_json methods
        raw_path = measurement.raw_data_path
        if raw_path and Path(raw_path).exists():
            self._plot_from_json(raw_path, measurement.test_type)
        else:
            ax = self._figure.add_subplot(111)
            ax.text(0.5, 0.5, "Keine Rohdaten vorhanden",
                    ha="center", va="center", fontsize=12, color="#999")
            ax.set_facecolor("#FAFAFA")
            ax.axis("off")

        self._figure.tight_layout()
        self._canvas.draw()

    def _style_ax(self, ax):
        ax.set_facecolor("#FAFAFA")
        ax.tick_params(labelsize=8, colors=TEXT_SECONDARY)
        for spine in ax.spines.values():
            spine.set_color("#E0E0E0")
        ax.grid(True, alpha=0.2)

    def _plot_from_json(self, path: str, test_type: str) -> None:
        with open(path) as f:
            data = json.load(f)

        fs = data.get("sample_rate", 110.0)

        if test_type in ("postural_tremor", "rest_tremor"):
            self._plot_tremor(data, fs)
        elif test_type == "finger_tapping":
            self._plot_unilateral(data, fs, metric_fn=self._thumb_index_dist,
                                  ylabel="Daumen-Zeigefinger Distanz (mm)")
        elif test_type == "hand_open_close":
            self._plot_unilateral(data, fs, metric_fn=self._mean_finger_spread,
                                  ylabel="Fingerspreizung (mm)")
        elif test_type == "pronation_supination":
            self._plot_unilateral(data, fs, metric_fn=self._palm_roll_angle,
                                  ylabel="Rotationswinkel (°)", color="#7B1FA2")
        elif test_type == "tower_of_hanoi":
            self._plot_hanoi(data, fs)
        elif test_type == "spatial_srt":
            self._plot_srt(data, fs)
        elif test_type.startswith("trail_making"):
            self._plot_tmt(data, fs)

    # ── Spatial SRT plots ────────────────────────────────────────

    def _plot_srt(self, data, fs):
        trial_results = data.get("trial_results", [])
        if not trial_results:
            return

        # Group by block
        blocks: dict[int, list] = {}
        for r in trial_results:
            blocks.setdefault(r["block_index"], []).append(r)

        sorted_block_ids = sorted(blocks.keys())
        block_types = {bid: blocks[bid][0]["block_type"] for bid in sorted_block_ids}

        # Top left: RT by Block
        ax1 = self.figure.add_subplot(2, 2, 1)
        self._style_ax(ax1)
        x_vals, y_vals, colors = [], [], []
        for i, bid in enumerate(sorted_block_ids):
            if block_types[bid] == "practice":
                continue
            rts = [r["total_response_time_ms"] for r in blocks[bid] if r.get("correct", True)]
            if rts:
                mean_rt = np.mean(rts)
                x_vals.append(i)
                y_vals.append(mean_rt)
                colors.append("#43A047" if block_types[bid] == "sequence" else "#E53935")
        if x_vals:
            ax1.bar(range(len(x_vals)), y_vals, color=colors, alpha=0.7)
            ax1.set_xlabel("Block", fontsize=8)
            ax1.set_ylabel("RT (ms)", fontsize=8)
        ax1.set_title("RT nach Block", fontsize=10, fontweight="bold")

        # Top right: Learning curve (sequence blocks only)
        ax2 = self.figure.add_subplot(2, 2, 2)
        self._style_ax(ax2)
        seq_means = []
        for bid in sorted_block_ids:
            if block_types[bid] == "sequence":
                rts = [r["total_response_time_ms"] for r in blocks[bid] if r.get("correct", True)]
                if rts:
                    seq_means.append(np.mean(rts))
        if seq_means:
            ax2.plot(range(1, len(seq_means) + 1), seq_means, "o-",
                     color="#43A047", linewidth=1.5, markersize=5)
            ax2.set_xlabel("Sequenz-Block", fontsize=8)
            ax2.set_ylabel("RT (ms)", fontsize=8)
        ax2.set_title("Lernkurve", fontsize=10, fontweight="bold")

        # Bottom left: Peak velocity per trial
        ax3 = self.figure.add_subplot(2, 2, 3)
        self._style_ax(ax3)
        non_practice = [r for r in trial_results if r["block_type"] != "practice"]
        if non_practice:
            vels = [r["peak_velocity_mm_s"] for r in non_practice]
            ax3.plot(vels, color=PRIMARY, linewidth=0.6, alpha=0.7)
            ax3.set_xlabel("Trial", fontsize=8)
            ax3.set_ylabel("Spitzengeschw. (mm/s)", fontsize=8)
        ax3.set_title("Geschwindigkeit", fontsize=10, fontweight="bold")

        # Bottom right: Path efficiency per block
        ax4 = self.figure.add_subplot(2, 2, 4)
        self._style_ax(ax4)
        eff_vals, eff_colors = [], []
        for bid in sorted_block_ids:
            if block_types[bid] == "practice":
                continue
            effs = []
            for r in blocks[bid]:
                if r.get("correct", True) and r["path_length_mm"] > 0:
                    effs.append(r["straight_distance_mm"] / r["path_length_mm"])
            if effs:
                eff_vals.append(np.mean(effs))
                eff_colors.append("#43A047" if block_types[bid] == "sequence" else "#E53935")
        if eff_vals:
            ax4.bar(range(len(eff_vals)), eff_vals, color=eff_colors, alpha=0.7)
            ax4.set_xlabel("Block", fontsize=8)
            ax4.set_ylabel("Pfad-Effizienz", fontsize=8)
            ax4.set_ylim(0, 1.1)
        ax4.set_title("Pfad-Effizienz", fontsize=10, fontweight="bold")

    # ── Trail Making plots ───────────────────────────────────────

    def _plot_tmt(self, data, fs):
        segments = data.get("segment_results", [])
        targets = data.get("targets", [])
        frames = data.get("frames", [])

        # Top left: Trail map (target layout + path)
        ax1 = self.figure.add_subplot(2, 2, 1)
        self._style_ax(ax1)
        if targets:
            xs = [t["x"] for t in targets]
            ys = [t["y"] for t in targets]
            ax1.plot(xs, ys, "-", color=ACCENT, linewidth=1, alpha=0.5)
            ax1.scatter(xs, ys, c=ACCENT, s=40, zorder=5)
            for t in targets:
                ax1.annotate(t["label"], (t["x"], t["y"]), fontsize=7,
                             ha="center", va="bottom", textcoords="offset points",
                             xytext=(0, 6))
            ax1.set_xlim(-0.05, 1.05)
            ax1.set_ylim(1.05, -0.05)  # invert Y
        ax1.set_title("Pfad", fontsize=10, fontweight="bold")
        ax1.set_aspect("equal")

        # Top right: Movement time per segment
        ax2 = self.figure.add_subplot(2, 2, 2)
        self._style_ax(ax2)
        if segments:
            mts = [s["movement_time_ms"] for s in segments]
            ax2.bar(range(1, len(mts) + 1), mts, color=PRIMARY, alpha=0.7)
            if len(mts) > 1:
                mean_mt = np.mean(mts)
                ax2.axhline(y=mean_mt, color="#E53935", linestyle="--", linewidth=1,
                            label=f"Mittel: {mean_mt:.0f}ms")
                ax2.legend(fontsize=7, frameon=False)
            ax2.set_xlabel("Segment", fontsize=8)
            ax2.set_ylabel("Bewegungszeit (ms)", fontsize=8)
        ax2.set_title("Segmentzeiten", fontsize=10, fontweight="bold")

        # Bottom left: Path efficiency per segment
        ax3 = self.figure.add_subplot(2, 2, 3)
        self._style_ax(ax3)
        if segments:
            effs = []
            for s in segments:
                if s["path_length_mm"] > 0:
                    effs.append(s["straight_distance_mm"] / s["path_length_mm"])
                else:
                    effs.append(0)
            ax3.bar(range(1, len(effs) + 1), effs, color="#43A047", alpha=0.7)
            ax3.set_ylim(0, 1.1)
            ax3.set_xlabel("Segment", fontsize=8)
            ax3.set_ylabel("Pfad-Effizienz", fontsize=8)
        ax3.set_title("Pfad-Effizienz", fontsize=10, fontweight="bold")

        # Bottom right: Errors over time
        ax4 = self.figure.add_subplot(2, 2, 4)
        self._style_ax(ax4)
        if segments:
            errors = [s["n_wrong_approaches"] for s in segments]
            ax4.bar(range(1, len(errors) + 1), errors, color="#E53935", alpha=0.7)
            ax4.set_xlabel("Segment", fontsize=8)
            ax4.set_ylabel("Fehler", fontsize=8)
        ax4.set_title("Fehler pro Segment", fontsize=10, fontweight="bold")

    # ── Tower of Hanoi plots ──────────────────────────────────────

    def _plot_hanoi(self, data, fs):
        moves = data.get("move_history", [])
        # Support both bilateral (old) and single-hand (new) format
        all_frames = data.get("frames", [])
        right_frames = data.get("right_frames", all_frames)
        left_frames = data.get("left_frames", [])

        # Top left: Hand X trajectory over time with move markers
        ax1 = self.figure.add_subplot(2, 2, 1)
        self._style_ax(ax1)
        for frames, color, label in [
            (right_frames, PRIMARY, "Rechts"),
            (left_frames, "#E53935", "Links"),
        ]:
            if frames:
                ts = np.array([f["timestamp_us"] for f in frames], dtype=np.int64)
                if len(ts) > 1:
                    t = (ts - ts[0]) / 1e6
                    px = np.array([f["palm_position"][0] for f in frames])
                    ax1.plot(t, px, color=color, linewidth=0.6, alpha=0.7, label=label)

        # Mark valid moves
        valid_moves = [m for m in moves if m["valid"]]
        if valid_moves and right_frames:
            t0 = right_frames[0]["timestamp_us"] / 1e6
            for m in valid_moves:
                ax1.axvline(x=m["timestamp_s"] - t0 if t0 < m["timestamp_s"] else m["timestamp_s"],
                            color="#43A047", linewidth=0.5, alpha=0.4)
        ax1.set_ylabel("Hand X (mm)", fontsize=8)
        ax1.set_xlabel("Zeit (s)", fontsize=8)
        ax1.set_title("Handposition", fontsize=10, fontweight="bold")
        ax1.legend(fontsize=7, frameon=False)

        # Top right: Pinch distance over time
        ax2 = self.figure.add_subplot(2, 2, 2)
        self._style_ax(ax2)
        for frames, color, label in [
            (right_frames, PRIMARY, "Rechts"),
            (left_frames, "#E53935", "Links"),
        ]:
            if frames:
                ts = np.array([f["timestamp_us"] for f in frames], dtype=np.int64)
                if len(ts) > 1:
                    t = (ts - ts[0]) / 1e6
                    pd = np.array([f.get("pinch_distance", 50) for f in frames])
                    ax2.plot(t, pd, color=color, linewidth=0.6, alpha=0.7, label=label)
        ax2.axhline(y=25, color="#BDBDBD", linestyle="--", linewidth=1, alpha=0.5)
        ax2.set_ylabel("Pinch-Distanz (mm)", fontsize=8)
        ax2.set_xlabel("Zeit (s)", fontsize=8)
        ax2.set_title("Greifverhalten", fontsize=10, fontweight="bold")
        ax2.legend(fontsize=7, frameon=False)

        # Bottom left: Move timing (bar chart)
        ax3 = self.figure.add_subplot(2, 2, 3)
        self._style_ax(ax3)
        if len(valid_moves) > 1:
            intervals = []
            for i in range(1, len(valid_moves)):
                intervals.append(valid_moves[i]["timestamp_s"] - valid_moves[i - 1]["timestamp_s"])
            bars = ax3.bar(range(1, len(intervals) + 1), intervals, color=PRIMARY, alpha=0.7)
            mean_int = np.mean(intervals)
            ax3.axhline(y=mean_int, color="#E53935", linestyle="--", linewidth=1,
                        label=f"Mittel: {mean_int:.1f}s")
            ax3.set_xlabel("Zug #", fontsize=8)
            ax3.set_ylabel("Zeit (s)", fontsize=8)
            ax3.legend(fontsize=7, frameon=False)
        ax3.set_title("Zugzeiten", fontsize=10, fontweight="bold")

        # Bottom right: Hand jitter (frame-to-frame displacement)
        ax4 = self.figure.add_subplot(2, 2, 4)
        self._style_ax(ax4)
        for frames, color, label in [
            (right_frames, PRIMARY, "R Jitter"),
            (left_frames, "#E53935", "L Jitter"),
        ]:
            if len(frames) > 10:
                ts = np.array([f["timestamp_us"] for f in frames], dtype=np.int64)
                t = (ts - ts[0]) / 1e6
                disps = [0.0]
                for i in range(1, len(frames)):
                    dx = frames[i]["palm_position"][0] - frames[i - 1]["palm_position"][0]
                    dy = frames[i]["palm_position"][1] - frames[i - 1]["palm_position"][1]
                    dz = frames[i]["palm_position"][2] - frames[i - 1]["palm_position"][2]
                    disps.append(math.sqrt(dx * dx + dy * dy + dz * dz))
                # Rolling median for smoothing
                window = min(15, len(disps) // 4) or 1
                arr = np.array(disps)
                smoothed = np.convolve(arr, np.ones(window) / window, mode="same")
                ax4.plot(t, smoothed, color=color, linewidth=0.6, alpha=0.7, label=label)
        ax4.set_ylabel("Jitter (mm/Frame)", fontsize=8)
        ax4.set_xlabel("Zeit (s)", fontsize=8)
        ax4.set_title("Hand-Jitter", fontsize=10, fontweight="bold")
        ax4.legend(fontsize=7, frameon=False)

    # ── Metric extractors ──────────────────────────────────────────

    @staticmethod
    def _thumb_index_dist(frame: dict) -> float:
        fingers = frame.get("fingers", [])
        if len(fingers) >= 2:
            t = fingers[0]["tip_position"]
            i = fingers[1]["tip_position"]
            return math.sqrt(sum((a - b) ** 2 for a, b in zip(t, i)))
        return 0.0

    @staticmethod
    def _mean_finger_spread(frame: dict) -> float:
        """Mean fingertip-to-palm distance in mm."""
        fingers = frame.get("fingers", [])
        palm = frame.get("palm_position", [0, 0, 0])
        if not fingers:
            return 0.0
        dists = []
        for f in fingers:
            tip = f.get("tip_position", palm)
            dists.append(math.sqrt(sum((a - b) ** 2 for a, b in zip(tip, palm))))
        return sum(dists) / len(dists)

    @staticmethod
    def _palm_roll_angle(frame: dict) -> float:
        pn = frame.get("palm_normal", [0, -1, 0])
        return math.degrees(math.atan2(pn[0], -pn[1]))

    # ── Onset/offset markers ─────────────────────────────────────

    def _draw_onset_offset(self, ax):
        """Draw vertical onset/offset lines if available."""
        if self._onset_s is not None and self._onset_s > 0:
            ax.axvline(x=self._onset_s, color="#4CAF50", linestyle="--",
                       linewidth=1, alpha=0.7, label="Onset")
        if self._offset_s is not None:
            ax.axvline(x=self._offset_s, color="#FF9800", linestyle="--",
                       linewidth=1, alpha=0.7, label="Offset")

    # ── Unilateral plot (tapping, open/close, pronation) ───────────

    def _plot_unilateral(self, data, fs, metric_fn, ylabel, color=PRIMARY):
        frames = data.get("frames", [])
        if not frames:
            return

        ts_raw = np.array([f["timestamp_us"] for f in frames], dtype=np.int64)
        values = np.array([metric_fn(f) for f in frames])
        t_u, v_u = resample_to_uniform(ts_raw, values, fs)
        v_clean = remove_outliers(v_u)

        # Row 1: Raw + cleaned signal
        ax1 = self.figure.add_subplot(2, 2, 1)
        self._style_ax(ax1)
        ax1.plot(t_u, v_u, color=color, linewidth=0.5, alpha=0.5, label="raw")
        ax1.plot(t_u, v_clean, color="#E53935", linewidth=0.8, label="cleaned")
        self._draw_onset_offset(ax1)
        ax1.set_ylabel(ylabel, fontsize=8)
        ax1.set_xlabel("Zeit (s)", fontsize=8)
        ax1.set_title("Zeitverlauf", fontsize=10, fontweight="bold")
        ax1.legend(fontsize=7)

        # Row 1 right: Detrended + peaks
        ax2 = self.figure.add_subplot(2, 2, 2)
        self._style_ax(ax2)
        v_det = detrend(v_clean)
        min_dist = max(1, int(fs / 6))
        sig_range = float(np.max(v_det) - np.min(v_det))
        min_prom = sig_range * 0.15
        peaks, _ = detect_peaks(v_det, min_distance=min_dist, prominence=min_prom)
        troughs, _ = detect_peaks(-v_det, min_distance=min_dist, prominence=min_prom)

        ax2.plot(t_u, v_det, color=color, linewidth=0.6)
        if len(peaks):
            ax2.plot(t_u[peaks], v_det[peaks], "rv", markersize=4, label=f"peaks ({len(peaks)})")
        if len(troughs):
            ax2.plot(t_u[troughs], v_det[troughs], "g^", markersize=4, label=f"troughs ({len(troughs)})")
        self._draw_onset_offset(ax2)
        ax2.set_ylabel(ylabel, fontsize=8)
        ax2.set_xlabel("Zeit (s)", fontsize=8)
        ax2.set_title("Detrended + Peaks", fontsize=10, fontweight="bold")
        ax2.legend(fontsize=7)

        # Row 2 left: FFT (on detrended signal for consistency with peak detection)
        ax3 = self.figure.add_subplot(2, 2, 3)
        self._style_ax(ax3)
        freqs, mags = compute_fft(v_det, fs)
        mask = (freqs > 0.5) & (freqs <= 10)
        ax3.plot(freqs[mask], mags[mask], color="#7B1FA2", linewidth=1)
        # Mark actual FFT peak (not inter-peak frequency)
        if np.any(mask) and np.max(mags[mask]) > 0:
            fft_peak_idx = np.argmax(mags[mask])
            fft_peak_freq = freqs[mask][fft_peak_idx]
            ax3.axvline(x=fft_peak_freq, color="red", linestyle="--", alpha=0.5,
                        label=f"Peak: {fft_peak_freq:.1f} Hz")
            ax3.legend(fontsize=7)
        ax3.set_xlabel("Frequenz (Hz)", fontsize=8)
        ax3.set_ylabel("Amplitude", fontsize=8)
        ax3.set_title("Spektrum", fontsize=10, fontweight="bold")

        # Row 2 right: Amplitude decrement
        ax4 = self.figure.add_subplot(2, 2, 4)
        self._style_ax(ax4)
        if len(peaks) > 2:
            peak_vals = v_clean[peaks]
            ax4.plot(t_u[peaks], peak_vals, "o-", color="#E53935", markersize=4, linewidth=1)
            z = np.polyfit(np.arange(len(peak_vals)), peak_vals, 1)
            ax4.plot(t_u[peaks], np.polyval(z, np.arange(len(peak_vals))),
                     "--", color="#BDBDBD", linewidth=1.5)
            ax4.set_xlabel("Zeit (s)", fontsize=8)
            ax4.set_ylabel("Peak-Amplitude", fontsize=8)
            ax4.set_title("Amplituden-Dekrement", fontsize=10, fontweight="bold")
        else:
            ax4.text(0.5, 0.5, "Zu wenige Peaks", ha="center", va="center",
                     fontsize=10, color="#999")
            ax4.axis("off")

    # ── Bilateral tremor plot ──────────────────────────────────────

    def _plot_tremor(self, data, fs):
        right_frames = data.get("right_frames", [])
        left_frames = data.get("left_frames", [])

        # Top left: Bandpassed magnitude over time (both hands)
        ax1 = self.figure.add_subplot(2, 2, 1)
        self._style_ax(ax1)
        for frames, color, label in [(right_frames, PRIMARY, "Rechts"), (left_frames, "#E53935", "Links")]:
            t_u, mag = self._tremor_magnitude(frames, fs)
            if mag is not None:
                ax1.plot(t_u, mag, color=color, linewidth=0.7, label=label)
        ax1.legend(fontsize=7, frameon=False)
        ax1.set_xlabel("Zeit (s)", fontsize=8)
        ax1.set_ylabel("Tremor-Amplitude (mm)", fontsize=8)
        ax1.set_title("Tremor 3-12 Hz", fontsize=10, fontweight="bold")

        # Top right: 3D FFT spectrum
        ax2 = self.figure.add_subplot(2, 2, 2)
        self._style_ax(ax2)
        for frames, color, label in [(right_frames, PRIMARY, "Rechts"), (left_frames, "#E53935", "Links")]:
            freqs, combined = self._tremor_spectrum(frames, fs)
            if freqs is not None:
                mask = (freqs >= 1.0) & (freqs <= 15.0)
                ax2.plot(freqs[mask], combined[mask], color=color, linewidth=1, label=label)
        ax2.axvspan(3, 12, alpha=0.06, color="red")
        ax2.legend(fontsize=7, frameon=False)
        ax2.set_xlabel("Frequenz (Hz)", fontsize=8)
        ax2.set_ylabel("Amplitude (mm)", fontsize=8)
        ax2.set_title("Spektrum (3D)", fontsize=10, fontweight="bold")

        # Bottom left: Raw detrended XYZ (right hand)
        ax3 = self.figure.add_subplot(2, 2, 3)
        self._style_ax(ax3)
        if right_frames:
            self._plot_xyz_detrended(ax3, right_frames, fs, "Rechts – detrended")

        # Bottom right: Raw detrended XYZ (left hand)
        ax4 = self.figure.add_subplot(2, 2, 4)
        self._style_ax(ax4)
        if left_frames:
            self._plot_xyz_detrended(ax4, left_frames, fs, "Links – detrended")

    def _plot_xyz_detrended(self, ax, frames, fs, title):
        ts = np.array([f["timestamp_us"] for f in frames], dtype=np.int64)
        px = np.array([f["palm_position"][0] for f in frames])
        py = np.array([f["palm_position"][1] for f in frames])
        pz = np.array([f["palm_position"][2] for f in frames])
        t_u, px_u = resample_to_uniform(ts, px, fs)
        _, py_u = resample_to_uniform(ts, py, fs)
        _, pz_u = resample_to_uniform(ts, pz, fs)
        px_u = detrend(remove_outliers(px_u))
        py_u = detrend(remove_outliers(py_u))
        pz_u = detrend(remove_outliers(pz_u))
        ax.plot(t_u, px_u, linewidth=0.5, alpha=0.8, label="X")
        ax.plot(t_u, py_u, linewidth=0.5, alpha=0.8, label="Y")
        ax.plot(t_u, pz_u, linewidth=0.5, alpha=0.8, label="Z")
        ax.set_title(title, fontsize=10, fontweight="bold")
        ax.set_ylabel("Position (mm)", fontsize=8)
        ax.set_xlabel("Zeit (s)", fontsize=8)
        ax.legend(fontsize=7)

    def _tremor_prepare(self, frames, fs):
        if len(frames) < 30:
            return None
        ts = np.array([f["timestamp_us"] for f in frames], dtype=np.int64)
        px = np.array([f["palm_position"][0] for f in frames])
        py = np.array([f["palm_position"][1] for f in frames])
        pz = np.array([f["palm_position"][2] for f in frames])
        t_u, px_u = resample_to_uniform(ts, px, fs)
        _, py_u = resample_to_uniform(ts, py, fs)
        _, pz_u = resample_to_uniform(ts, pz, fs)
        px_u = detrend(remove_outliers(px_u))
        py_u = detrend(remove_outliers(py_u))
        pz_u = detrend(remove_outliers(pz_u))
        return t_u, px_u, py_u, pz_u

    def _tremor_magnitude(self, frames, fs):
        result = self._tremor_prepare(frames, fs)
        if result is None:
            return None, None
        t_u, px, py, pz = result
        try:
            mag = np.sqrt(
                bandpass_filter(px, fs, 3.0, 12.0) ** 2
                + bandpass_filter(py, fs, 3.0, 12.0) ** 2
                + bandpass_filter(pz, fs, 3.0, 12.0) ** 2
            )
            return t_u, mag
        except ValueError:
            return None, None

    def _tremor_spectrum(self, frames, fs):
        result = self._tremor_prepare(frames, fs)
        if result is None:
            return None, None
        _, px, py, pz = result
        try:
            px_f = bandpass_filter(px, fs, 3.0, 12.0)
            py_f = bandpass_filter(py, fs, 3.0, 12.0)
            pz_f = bandpass_filter(pz, fs, 3.0, 12.0)
            freqs, mx = compute_fft(px_f, fs)
            _, my = compute_fft(py_f, fs)
            _, mz = compute_fft(pz_f, fs)
            combined = np.sqrt(mx ** 2 + my ** 2 + mz ** 2)
            return freqs, combined
        except ValueError:
            return None, None
