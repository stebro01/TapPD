"""Shared real-time metric plot (matplotlib on a Qt canvas).

Both the live TestScreen and VideoLab plot a per-hand live metric over time; this
is the one implementation. Feed it the runner's ``live`` dict
(``{hand: [(t_s, value), ...]}``) plus the y-axis label.
"""

from __future__ import annotations

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

_COLORS = {"right": "#1976D2", "left": "#E53935"}
_LABELS = {"right": "Rechts", "left": "Links"}


class LiveMetricPlot(FigureCanvasQTAgg):
    def __init__(self, parent=None, figsize=(8, 2.5)) -> None:
        self.figure = Figure(figsize=figsize, tight_layout=True)
        super().__init__(self.figure)
        if parent is not None:
            self.setParent(parent)
        self._ax = self.figure.add_subplot(111)

    def clear_plot(self) -> None:
        self._ax.clear()
        self.draw_idle()

    def update_plot(self, live: dict, metric_label: str = "", *,
                    window_s: float | None = None, window_points: int | None = None) -> None:
        """Redraw from `live` ({hand: [(t_s, value)]}). Window by seconds or points."""
        self._ax.clear()
        present = [h for h in ("right", "left") if live.get(h)]
        for hand in present:
            data = live[hand]
            if window_s and data and data[-1][0] > window_s:
                t0 = data[-1][0] - window_s
                data = [d for d in data if d[0] >= t0]
            elif window_points:
                data = data[-window_points:]
            xs = [t for t, _ in data]
            ys = [v for _, v in data]
            label = _LABELS[hand] if len(present) > 1 else None
            self._ax.plot(xs, ys, color=_COLORS[hand], linewidth=1.2, label=label)
        if present:
            self._ax.set_xlabel("Zeit (s)", fontsize=8)
            self._ax.set_ylabel(metric_label, fontsize=8)
            if len(present) > 1:
                self._ax.legend(fontsize=7, loc="upper right")
        self._ax.tick_params(labelsize=7)
        self.draw_idle()
