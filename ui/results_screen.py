"""Results screen: feature table, plots, auto-saved."""

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from motor_tests.base_test import BaseMotorTest
from storage.session_store import SessionResult, export_csv
from storage.database import get_db, delete_measurement
from analysis.signal_processing import (
    bandpass_filter,
    compute_fft,
    detrend,
    detect_peaks,
    remove_outliers,
    resample_to_uniform,
)
from ui.feature_meta import FEATURE_META
from ui.theme import PRIMARY, TEXT_SECONDARY

log = logging.getLogger(__name__)

SAMPLES_DIR = Path(__file__).parent.parent / "data" / "samples"

COLOR_RIGHT = QColor(227, 242, 253)  # light blue
COLOR_LEFT = QColor(255, 235, 238)   # light red


def save_raw_data(test: BaseMotorTest, patient_id: str, features: dict | None = None) -> Path | None:
    """Save raw HandFrame + test-specific data to JSON. Returns file path or None."""
    try:
        SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{patient_id}_{test.test_type()}_{test.hand}_{ts}.json"
        filepath = SAMPLES_DIR / filename

        data = {
            "patient_id": patient_id,
            "test_type": test.test_type(),
            "hand": test.hand,
            "duration_s": test.duration,
            "sample_rate": test.capture.sample_rate,
            "recorded_at": datetime.now().isoformat(),
            "features": features or {},
        }

        if test.bilateral:
            data["left_frames"] = [asdict(f) for f in test.get_frames("left")]
            data["right_frames"] = [asdict(f) for f in test.get_frames("right")]
        else:
            data["frames"] = [asdict(f) for f in test.get_frames()]

        if test.test_type() == "tower_of_hanoi":
            from motor_tests.tower_of_hanoi import TowerOfHanoiTest
            if isinstance(test, TowerOfHanoiTest):
                data["move_history"] = [
                    {"from_peg": m.from_peg, "to_peg": m.to_peg,
                     "disc": m.disc, "timestamp_s": m.timestamp_s, "valid": m.valid}
                    for m in test.game.move_history
                ]
                data["n_discs"] = test.n_discs

        if test.test_type() == "spatial_srt":
            from dataclasses import asdict as _asdict
            from motor_tests.spatial_srt import SpatialSRTTest
            if isinstance(test, SpatialSRTTest):
                data["trial_results"] = [_asdict(r) for r in test.task.trial_results]
                data["blocks"] = [
                    {"block_index": b.block_index, "block_type": b.block_type,
                     "n_trials": b.n_trials, "targets": b.targets}
                    for b in test.task.blocks
                ]
                data["sequence"] = test.task.sequence

        if test.test_type().startswith("trail_making"):
            from dataclasses import asdict as _asdict
            from motor_tests.trail_making import TrailMakingTest
            if isinstance(test, TrailMakingTest):
                data["segment_results"] = [_asdict(r) for r in test.task.segment_results]
                data["targets"] = [
                    {"index": t.index, "label": t.label, "x": t.x, "y": t.y}
                    for t in test.task.targets
                ]
                data["part"] = test.part
                data["wrong_approaches"] = test.task.wrong_approaches

        with open(filepath, "w") as f:
            json.dump(data, f, indent=1, default=str)

        n = len(data.get("frames", [])) + len(data.get("left_frames", [])) + len(data.get("right_frames", []))
        log.info("Raw data saved: %s (%d frames)", filepath, n)
        return filepath
    except Exception:
        log.exception("Failed to save raw data")
        return None


class ResultsScreen(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.current_result: SessionResult | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 16)
        layout.setSpacing(12)

        self.title_label = QLabel("Ergebnisse")
        self.title_label.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        self.saved_label = QLabel("Automatisch gespeichert")
        self.saved_label.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; font-style: italic;")
        self.saved_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.saved_label)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Parameter", "Wert", "Einheit"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setMaximumHeight(280)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        # Plots
        self.figure = Figure(figsize=(8, 3.5), facecolor="#FAFAFA")
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        discard_btn = QPushButton("Verwerfen")
        discard_btn.setStyleSheet("color: #E53935;")
        discard_btn.clicked.connect(self._on_discard)
        btn_row.addWidget(discard_btn)

        retry_btn = QPushButton("Neu aufnehmen")
        retry_btn.clicked.connect(self._on_retry)
        btn_row.addWidget(retry_btn)

        csv_btn = QPushButton("CSV Export")
        csv_btn.clicked.connect(self._on_csv_export)
        btn_row.addWidget(csv_btn)

        next_btn = QPushButton("Fortfahren")
        next_btn.setProperty("cssClass", "primary")
        next_btn.clicked.connect(self._on_next)
        btn_row.addWidget(next_btn)

        self.save_raw_cb = QCheckBox("Rohdaten speichern")
        self.save_raw_cb.setChecked(True)
        self.save_raw_cb.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY};")
        btn_row.addWidget(self.save_raw_cb)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def show_results(
        self, test: BaseMotorTest, patient_id: str,
        measurement_id: int | None = None,
        features: dict[str, float] | None = None,
    ) -> None:
        self._current_test = test
        self._current_patient_id = patient_id
        self._measurement_id = measurement_id
        self._raw_file_path: Path | None = None

        self.saved_label.setText("In Datenbank gespeichert" if measurement_id else "")

        if features is None:
            features = test.compute_features()

        self.current_result = SessionResult(
            patient_id=patient_id,
            test_type=test.test_type(),
            hand=test.hand,
            duration_s=test.duration,
            features=features,
        )

        hand_label = "bilateral" if test.bilateral else test.hand
        self.title_label.setText(f"{test.test_type()}  –  {hand_label}")

        # Table (filter internal keys starting with _)
        display_features = {k: v for k, v in features.items() if not k.startswith("_")}
        self.table.setRowCount(len(display_features))
        for row, (key, value) in enumerate(display_features.items()):
            meta = FEATURE_META.get(key, (key, ""))
            name_item = QTableWidgetItem(meta[0])
            if key == "mpi":
                val_item = QTableWidgetItem(f"{value:.3f}" if isinstance(value, float) else str(value))
            else:
                val_item = QTableWidgetItem(f"{value:.4f}" if isinstance(value, float) else str(value))
            unit_item = QTableWidgetItem(meta[1])

            val_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            if key == "mpi":
                bold = QFont()
                bold.setBold(True)
                bold.setPointSize(bold.pointSize() + 1)
                for item in (name_item, val_item, unit_item):
                    item.setFont(bold)
                v = value if isinstance(value, (int, float)) else 0.0
                if v >= 0.7:
                    bg = QColor(232, 245, 233)   # green-50
                elif v >= 0.4:
                    bg = QColor(255, 249, 196)   # yellow-50
                else:
                    bg = QColor(255, 235, 238)   # red-50
                for item in (name_item, val_item, unit_item):
                    item.setBackground(bg)
            elif key.startswith("R_"):
                for item in (name_item, val_item, unit_item):
                    item.setBackground(COLOR_RIGHT)
            elif key.startswith("L_"):
                for item in (name_item, val_item, unit_item):
                    item.setBackground(COLOR_LEFT)

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, val_item)
            self.table.setItem(row, 2, unit_item)

        self._plot(test)

    def _plot(self, test: BaseMotorTest) -> None:
        self.figure.clear()
        fs = test.capture.sample_rate

        if test.bilateral:
            self._plot_bilateral(test, fs)
        elif test.test_type() == "pronation_supination":
            self._plot_timeseries(test, fs, color="#7B1FA2", ylabel="Rotationswinkel (°)")
        else:
            self._plot_timeseries(test, fs)

        self.figure.tight_layout()
        self.canvas.draw()

    def _style_ax(self, ax):
        ax.set_facecolor("#FAFAFA")
        ax.tick_params(labelsize=8, colors=TEXT_SECONDARY)
        for spine in ax.spines.values():
            spine.set_color("#E0E0E0")

    def _plot_timeseries(self, test, fs, color=PRIMARY, ylabel=None):
        frames = test.get_frames()
        if not frames:
            return
        ts = np.array([f.timestamp_us for f in frames], dtype=np.int64)
        metrics = np.array([test.get_live_metric(f) for f in frames])
        t_u, m_u = resample_to_uniform(ts, metrics, fs)

        ax1 = self.figure.add_subplot(1, 2, 1)
        self._style_ax(ax1)
        ax1.plot(t_u, m_u, color=color, linewidth=0.9)
        ax1.set_xlabel("Zeit (s)", fontsize=9, color=TEXT_SECONDARY)
        ax1.set_ylabel(ylabel or test.get_live_metric_label(), fontsize=9, color=TEXT_SECONDARY)
        ax1.set_title("Zeitverlauf", fontsize=10, fontweight="bold")

        ax2 = self.figure.add_subplot(1, 2, 2)
        self._style_ax(ax2)
        peaks, peak_vals = detect_peaks(m_u, min_distance=max(1, int(fs / 6)))
        if len(peaks) > 1:
            ax2.plot(t_u[peaks], peak_vals, "o-", color="#E53935", markersize=4, linewidth=1)
            z = np.polyfit(np.arange(len(peak_vals)), peak_vals, 1)
            ax2.plot(t_u[peaks], np.polyval(z, np.arange(len(peak_vals))), "--", color="#BDBDBD")
            ax2.set_xlabel("Zeit (s)", fontsize=9, color=TEXT_SECONDARY)
            ax2.set_ylabel("Amplitude", fontsize=9, color=TEXT_SECONDARY)
            ax2.set_title("Dekrement", fontsize=10, fontweight="bold")

    def _plot_bilateral(self, test, fs):
        left_frames = test.get_frames("left")
        right_frames = test.get_frames("right")

        # Left plot: bandpass-filtered tremor magnitude over time
        ax1 = self.figure.add_subplot(1, 2, 1)
        self._style_ax(ax1)
        for frames, color, label in [(right_frames, PRIMARY, "Rechts"), (left_frames, "#E53935", "Links")]:
            if len(frames) < 30:
                continue
            t_u, mag_f = self._tremor_magnitude(frames, fs)
            if mag_f is not None:
                ax1.plot(t_u, mag_f, color=color, linewidth=0.8, label=label)
        ax1.legend(fontsize=8, frameon=False)
        ax1.set_xlabel("Zeit (s)", fontsize=9, color=TEXT_SECONDARY)
        ax1.set_ylabel("Tremor-Amplitude (mm)", fontsize=9, color=TEXT_SECONDARY)
        ax1.set_title("Tremor 3-12 Hz (bandpassgefiltert)", fontsize=10, fontweight="bold")

        # Right plot: 3D combined FFT spectrum
        ax2 = self.figure.add_subplot(1, 2, 2)
        self._style_ax(ax2)
        for frames, color, label in [(right_frames, PRIMARY, "Rechts"), (left_frames, "#E53935", "Links")]:
            if len(frames) < 30:
                continue
            freqs, combined = self._tremor_spectrum(frames, fs)
            if freqs is not None:
                mask = (freqs >= 1.0) & (freqs <= 15.0)
                ax2.plot(freqs[mask], combined[mask], color=color, linewidth=1, label=label)
        ax2.axvspan(3, 12, alpha=0.06, color="red")
        ax2.legend(fontsize=8, frameon=False)
        ax2.set_xlabel("Frequenz (Hz)", fontsize=9, color=TEXT_SECONDARY)
        ax2.set_ylabel("Amplitude (mm)", fontsize=9, color=TEXT_SECONDARY)
        ax2.set_title("Frequenzspektrum (3D)", fontsize=10, fontweight="bold")

    def _tremor_prepare_axes(self, frames, fs):
        """Resample, clean, detrend palm XYZ for tremor frames."""
        ts = np.array([f.timestamp_us for f in frames], dtype=np.int64)
        px = np.array([f.palm_position[0] for f in frames])
        py = np.array([f.palm_position[1] for f in frames])
        pz = np.array([f.palm_position[2] for f in frames])
        t_u, px_u = resample_to_uniform(ts, px, fs)
        _, py_u = resample_to_uniform(ts, py, fs)
        _, pz_u = resample_to_uniform(ts, pz, fs)
        px_u = detrend(remove_outliers(px_u))
        py_u = detrend(remove_outliers(py_u))
        pz_u = detrend(remove_outliers(pz_u))
        return t_u, px_u, py_u, pz_u

    def _tremor_magnitude(self, frames, fs):
        """Return (time, bandpass-filtered 3D magnitude) or (None, None)."""
        try:
            t_u, px, py, pz = self._tremor_prepare_axes(frames, fs)
            mag = np.sqrt(
                bandpass_filter(px, fs, 3.0, 12.0) ** 2
                + bandpass_filter(py, fs, 3.0, 12.0) ** 2
                + bandpass_filter(pz, fs, 3.0, 12.0) ** 2
            )
            return t_u, mag
        except (ValueError, Exception):
            return None, None

    def _tremor_spectrum(self, frames, fs):
        """Return (freqs, combined_amplitude) from 3D FFT or (None, None)."""
        try:
            t_u, px, py, pz = self._tremor_prepare_axes(frames, fs)
            px_f = bandpass_filter(px, fs, 3.0, 12.0)
            py_f = bandpass_filter(py, fs, 3.0, 12.0)
            pz_f = bandpass_filter(pz, fs, 3.0, 12.0)
            freqs, mx = compute_fft(px_f, fs)
            _, my = compute_fft(py_f, fs)
            _, mz = compute_fft(pz_f, fs)
            combined = np.sqrt(mx ** 2 + my ** 2 + mz ** 2)
            return freqs, combined
        except (ValueError, Exception):
            return None, None

    def _save_raw_frames(self) -> None:
        """Save raw HandFrame data to JSON for offline analysis."""
        test = self._current_test
        if test is None:
            return
        try:
            features = self.current_result.features if self.current_result else {}
            filepath = save_raw_data(test, self._current_patient_id, features)
            if filepath is None:
                return
            self._raw_file_path = filepath
            n_frames = 0
            with open(filepath) as f:
                d = json.load(f)
                n_frames = len(d.get("frames", [])) + len(d.get("left_frames", [])) + len(d.get("right_frames", []))

            # Update DB entry with raw data path
            if self._measurement_id:
                try:
                    conn = get_db()
                    conn.execute(
                        "UPDATE measurements SET raw_data_path=? WHERE id=?",
                        (str(filepath), self._measurement_id),
                    )
                    conn.commit()
                    conn.close()
                except Exception:
                    log.exception("Failed to update raw_data_path in DB")

            self.saved_label.setText(f"Rohdaten: {filepath.name} ({n_frames} Frames)")
        except Exception:
            log.exception("Failed to save raw frames")
            QMessageBox.warning(self, "Fehler", "Rohdaten konnten nicht gespeichert werden.")

    def _on_csv_export(self) -> None:
        if self.current_result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "CSV exportieren", "", "CSV (*.csv)")
        if path:
            export_csv(self.current_result, path)
            QMessageBox.information(self, "Export", f"CSV exportiert:\n{path}")

    def _on_discard(self) -> None:
        """Delete the measurement from DB and any saved raw file."""
        reply = QMessageBox.question(
            self, "Verwerfen",
            "Aufnahme wirklich verwerfen?\nDer Eintrag wird aus der Datenbank gelöscht.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Delete from DB
        if self._measurement_id:
            try:
                conn = get_db()
                delete_measurement(conn, self._measurement_id)
                conn.close()
                log.info("Measurement %d deleted", self._measurement_id)
            except Exception:
                log.exception("Failed to delete measurement")

        # Delete raw file if saved
        if self._raw_file_path and self._raw_file_path.exists():
            try:
                self._raw_file_path.unlink()
                log.info("Raw file deleted: %s", self._raw_file_path)
            except Exception:
                log.exception("Failed to delete raw file")

        self.main_window.show_start()

    def _on_retry(self) -> None:
        """Discard current and re-run same test."""
        # Delete current measurement from DB
        if self._measurement_id:
            try:
                conn = get_db()
                delete_measurement(conn, self._measurement_id)
                conn.close()
            except Exception:
                log.exception("Failed to delete measurement on retry")

        # Delete raw file if saved
        if self._raw_file_path and self._raw_file_path.exists():
            try:
                self._raw_file_path.unlink()
            except Exception:
                pass

        test = self._current_test
        if test:
            self.main_window.repeat_test(
                test.test_type(), test.hand, int(test.duration)
            )
        else:
            self.main_window.show_start()

    def _on_next(self) -> None:
        if self.save_raw_cb.isChecked() and self._raw_file_path is None:
            self._save_raw_frames()
        self.main_window.show_start()
