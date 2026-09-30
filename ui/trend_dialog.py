"""Verlauf (longitudinal trend): one feature of one test type over time.

Closes the "analysis is strictly per-measurement" gap: all of a patient's
measurements for a chosen paradigm are plotted as a time series, split by hand,
with camera-sourced points (webcam/video — estimated mm scale) drawn hollow.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from storage.database import Measurement, Patient
from ui.feature_meta import FEATURE_META, unit_label
from ui.theme import SZ, TEXT_SECONDARY
from ui import theme

_HAND_STYLE = {                      # colour per hand series
    "right": (f"{theme.PRIMARY}", "Rechts"),
    "left": ("#C62828", "Links"),
    "both": ("#6A1B9A", "Bilateral"),
}
_ESTIMATED_KINDS = {"webcam", "video"}


def _parse_dt(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None


class TrendDialog(QDialog):
    def __init__(self, patient: Patient, measurements: list[Measurement], parent=None):
        super().__init__(parent)
        self._patient = patient
        # test_type -> chronologically sorted measurements
        self._by_test: dict[str, list[Measurement]] = {}
        for m in measurements:
            if _parse_dt(m.recorded_at) is not None:
                self._by_test.setdefault(m.test_type, []).append(m)
        for ms in self._by_test.values():
            ms.sort(key=lambda m: m.recorded_at)

        self.setWindowTitle(f"Verlauf – {patient.display_name}")
        self.setMinimumSize(900, 600)
        root = QVBoxLayout(self)

        picker = QHBoxLayout()
        picker.addWidget(QLabel("Test:"))
        self._test_combo = QComboBox()
        for tt in sorted(self._by_test):
            self._test_combo.addItem(f"{tt} ({len(self._by_test[tt])})", tt)
        self._test_combo.setMinimumWidth(260)
        self._test_combo.setFixedHeight(SZ.BTN_H)
        picker.addWidget(self._test_combo)
        picker.addSpacing(16)
        picker.addWidget(QLabel("Merkmal:"))
        self._feature_combo = QComboBox()
        self._feature_combo.setMinimumWidth(260)
        self._feature_combo.setFixedHeight(SZ.BTN_H)
        picker.addWidget(self._feature_combo)
        picker.addStretch()
        root.addLayout(picker)

        self._figure = Figure(figsize=(8, 5), facecolor=f"{theme.BG}")
        self._canvas = FigureCanvasQTAgg(self._figure)
        root.addWidget(self._canvas, stretch=1)

        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY};")
        root.addWidget(self._note)

        self._test_combo.currentIndexChanged.connect(self._on_test_changed)
        self._feature_combo.currentIndexChanged.connect(lambda _i: self._redraw())
        if self._by_test:
            self._on_test_changed()

    # ── selection ─────────────────────────────────────────────────
    def _current_measurements(self) -> list[Measurement]:
        return self._by_test.get(self._test_combo.currentData() or "", [])

    def _on_test_changed(self, _i: int = 0) -> None:
        ms = self._current_measurements()
        keys: list[str] = []
        for m in ms:
            for k, v in m.features.items():
                if not k.startswith("_") and isinstance(v, (int, float)) and k not in keys:
                    keys.append(k)
        self._feature_combo.blockSignals(True)
        self._feature_combo.clear()
        for k in keys:
            self._feature_combo.addItem(FEATURE_META.get(k, (k, ""))[0], k)
        if "mpi" in keys:
            self._feature_combo.setCurrentIndex(keys.index("mpi"))
        self._feature_combo.blockSignals(False)
        self._redraw()

    # ── plot ──────────────────────────────────────────────────────
    def _redraw(self) -> None:
        self._figure.clear()
        key = self._feature_combo.currentData()
        ms = self._current_measurements()
        ax = self._figure.add_subplot(111)
        ax.set_facecolor("#FFFFFF")
        if not key or not ms:
            ax.set_axis_off()
            self._canvas.draw_idle()
            return

        any_estimated = False
        for hand, (color, label) in _HAND_STYLE.items():
            pts = [(m, m.features.get(key)) for m in ms
                   if m.hand == hand and isinstance(m.features.get(key), (int, float))]
            if not pts:
                continue
            xs = [_parse_dt(m.recorded_at) for m, _v in pts]
            ys = [float(v) for _m, v in pts]
            ax.plot(xs, ys, "-", color=color, linewidth=1.2, alpha=0.6)
            # Camera-sourced points hollow (estimated mm scale), sensor filled.
            for x, y, (m, _v) in zip(xs, ys, pts):
                est = m.source_kind in _ESTIMATED_KINDS
                any_estimated = any_estimated or est
                ax.plot([x], [y], "o", markersize=7, markeredgewidth=1.6,
                        markeredgecolor=color,
                        markerfacecolor="none" if est else color)
            ax.plot([], [], "o-", color=color, label=label)

        unit = unit_label(key, "")
        ax.set_ylabel(f"{FEATURE_META.get(key, (key, ''))[0]}"
                      + (f" ({unit})" if unit else ""), fontsize=10)
        ax.grid(True, alpha=0.25)
        ax.tick_params(labelsize=8)
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(30)
            lbl.set_ha("right")
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=9, loc="best")
        self._figure.tight_layout()
        self._canvas.draw_idle()

        n = len(ms)
        note = f"{n} Messung{'en' if n != 1 else ''}."
        if any_estimated:
            note += ("  Hohle Punkte: Kamera-Quelle (Webcam/Video) — mm-Werte sind "
                     "Modellschätzungen, innerhalb desselben Probanden vergleichbar.")
        self._note.setText(note)
