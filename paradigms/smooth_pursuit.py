"""Okulomotorik: Blickfolge (smooth pursuit) auf den Finger des Untersuchers.

Der Untersucher bewegt einen Finger langsam vor dem Patienten; der Patient
folgt ihm **nur mit den Augen**. Ausgewertet wird, wie gut der Blick dem Ziel
folgt — glatt oder in Nachsetz-Sakkaden ("sakkadierte Blickfolge", bei
Parkinson häufig, bei atypischen Syndromen oft ausgeprägter).

Das Besondere gegenüber dem Sakkadentest: **Ziel und Antwort stehen im selben
Bild.** Die Hand des Untersuchers wird vom Hand-Tracker verfolgt, der Blick vom
Face-Tracker — beide aus derselben Kamera, derselben Zeitbasis. Es gibt nichts
zu synchronisieren und keine Eichung, weil nicht die absolute Blickrichtung
zählt, sondern das Verhältnis zweier gleichzeitig gemessener Bewegungen.

Genau deshalb wäre dieses Paradigma grundsätzlich auch aus einem Video
auswertbar — anders als der gaze-contingente Sakkadentest, dessen Reiz davon
abhängt, wohin der Patient schaut. Im aktuellen Stand läuft es wie alle
Augen-Tests nur live (``registry.is_live_only``, Kategorie OCULAR).

Beide Signale werden in **IPD-Einheiten** gerechnet (Vielfache des
Pupillenabstands), damit Kamera-Abstand und Auflösung herausfallen:

- Blick: ``FacePose.gaze_offset_ipd`` — Iris relativ zu den Augenwinkeln.
- Ziel:  die eye-referenzierte Handposition (``adapt_frame`` promotet sie auf
  ``palm_position``), geteilt durch den mittleren Pupillenabstand.

Kennwerte:

- **pursuit_gain** — Steigung der Regression Blickgeschwindigkeit über
  Zielgeschwindigkeit. Relativmaß (siehe Hinweis zur Tiefe unten).
- **pursuit_r2** — wie gut diese Regression überhaupt trägt; niedrig heißt
  "der Blick folgt dem Ziel nicht".
- **catchup_saccades_per_s** — Nachsetz-Sakkaden: Blicksprünge, die im Ziel
  keine Entsprechung haben. Das ist der eigentliche klinische Befund.
- **pursuit_lag_ms** — Nachlauf des Blicks gegenüber dem Ziel.

Zur Tiefe: Der Zielabstand wird über den Pupillenabstand *auf Gesichtstiefe*
skaliert. Der Finger ist in aller Regel näher an der Kamera, sein Bildversatz
also überschätzt. Der Gain trägt dadurch einen konstanten Skalenfehler und ist
als **Relativmaß zwischen Gruppen** zu lesen, nicht als absoluter
Verstärkungsfaktor. ``catchup_saccades_per_s`` und ``pursuit_lag_ms`` sind von
der Tiefe unabhängig und deshalb die belastbareren Größen.
"""

from __future__ import annotations

import math

from capture.base_capture import BaseCaptureDevice, FacePose, HandFrame, TrackingFrame
from capture.mediapipe_mapping import AVG_IPD_MM
from paradigms.base_test import BaseParadigm
from paradigms.config import get_test_config

_EMPTY = {
    "pursuit_gain": 0.0,
    "pursuit_r2": 0.0,
    "catchup_saccades_per_s": 0.0,
    "pursuit_lag_ms": 0.0,
    "pursuit_axis_vertical": 0.0,
    "target_excursion_ipd": 0.0,
    "n_pursuit_samples": 0.0,
    "pursuit_coverage": 0.0,
}


def _median_smooth(xs: list[float], window: int) -> list[float]:
    """Running median — kills single-frame landmark outliers without the
    smearing a mean filter would apply to a real saccade."""
    if window < 3 or len(xs) < window:
        return list(xs)
    half = window // 2
    out = []
    for i in range(len(xs)):
        lo, hi = max(0, i - half), min(len(xs), i + half + 1)
        chunk = sorted(xs[lo:hi])
        out.append(chunk[len(chunk) // 2])
    return out


class SmoothPursuitTest(BaseParadigm):
    """Blickfolge auf ein bewegtes Ziel (die Hand des Untersuchers)."""

    bilateral = False

    def __init__(self, capture: BaseCaptureDevice, duration: float = 40.0,
                 hand: str = "both") -> None:
        super().__init__(capture, duration, hand)
        # One entry per camera frame that carried BOTH a face and a hand.
        self.samples: list[tuple[int, str, float, float, float, float, float]] = []
        # The raw face stream is kept as well — every OCULAR paradigm exposes it
        # (paradigm contract, tests/test_paradigm_contracts.py), and it keeps the
        # recording re-analysable if the pairing rules ever change.
        self.face_frames: list[FacePose] = []
        cfg = get_test_config(self.test_type()).get("analysis", {})
        self._blink_ear = float(cfg.get("blink_ear_threshold", 0.18))
        self._min_samples = int(cfg.get("min_paired_frames", 60))
        self._min_excursion = float(cfg.get("min_target_excursion_ipd", 0.15))
        self._catchup_ipd_s = float(cfg.get("catchup_velocity_ipd_s", 0.6))
        self._catchup_ratio = float(cfg.get("catchup_target_ratio", 3.0))
        self._max_lag_ms = float(cfg.get("max_lag_ms", 500.0))
        self._smooth_window = int(cfg.get("smooth_window", 5))

    def test_type(self) -> str:
        return "smooth_pursuit"

    def get_instructions(self) -> str:
        return (
            "Blickfolge (Okulomotorik)\n\n"
            "Der Untersucher bewegt einen Finger langsam vor dem Patienten,\n"
            "etwa 60 cm entfernt, rund 3 Sekunden je Strecke.\n\n"
            "Ansage an den Patienten:\n"
            "„Folgen Sie bitte meinem Finger nur mit den Augen.\n"
            "Der Kopf bleibt still.“\n\n"
            "Wichtig:\n"
            "- Erst 3 Durchgänge waagerecht, dann 3 senkrecht\n"
            "- Gleichmäßig bewegen, nicht ruckartig\n"
            "- Finger UND Gesicht müssen im Bild sein\n"
            "- Die Hände des Patienten bleiben im Schoß (außerhalb des Bildes),\n"
            "  damit nur die Hand des Untersuchers verfolgt wird\n\n"
            "Hinweis: Keine Eichung nötig — gemessen wird das Verhältnis von\n"
            "Blick- zu Zielbewegung, beide aus demselben Kamerabild."
        )

    # ── frame intake ───────────────────────────────────────────────
    def start(self) -> None:
        self.samples.clear()
        self.face_frames.clear()
        enable = getattr(self.capture, "enable_face", None)
        if enable is not None:
            enable(True, full_rate=True)
        super().start()

    def _on_tracking(self, tf: TrackingFrame) -> None:
        """Pair each frame's gaze with the target position of every hand.

        Which hand is the examiner's is decided at feature time (the one that
        actually moved) — at intake we simply keep both, because a patient who
        forgets to drop their hands would otherwise silently pick the wrong one.
        """
        face = tf.face
        if face is not None:
            with self._lock:
                self.face_frames.append(face)
        if face is not None and tf.hands:
            gx, gy = face.gaze_offset_ipd
            with self._lock:
                for h in tf.hands:
                    adapted = self._profile.adapt_frame(h)
                    px, py, _ = adapted.palm_position
                    # Target into the gaze's coordinate frame: same IPD scale,
                    # and y flipped because palm y counts UP while the iris
                    # offset counts DOWN (image coordinates). Without the flip
                    # the vertical gain comes out negative.
                    self.samples.append((
                        face.timestamp_us, h.hand_type,
                        px / AVG_IPD_MM, -py / AVG_IPD_MM,
                        gx, gy, face.ear,
                    ))
        super()._on_tracking(tf)

    # ── live metric ────────────────────────────────────────────────
    def get_live_metric(self, frame: HandFrame) -> float:
        return 0.0

    def get_face_metric(self, face: FacePose) -> float:
        """Blickauslenkung in %IPD — zeigt live, ob der Blick mitgeht."""
        ox, oy = face.gaze_offset_ipd
        return math.hypot(ox, oy) * 100.0

    def get_live_metric_label(self) -> str:
        return "Blickauslenkung (%IPD)"

    # ── features ───────────────────────────────────────────────────
    def compute_features(self) -> dict[str, float]:
        with self._lock:
            samples = list(self.samples)
        if not samples:
            return dict(_EMPTY)

        # 1. The examiner's hand is the one that moved. A patient's resting
        #    hand in frame would otherwise be taken as a motionless target and
        #    drag the gain to zero.
        by_hand: dict[str, list] = {}
        for s in samples:
            by_hand.setdefault(s[1], []).append(s)
        target_hand = max(
            by_hand,
            key=lambda h: max(
                max(s[2] for s in by_hand[h]) - min(s[2] for s in by_hand[h]),
                max(s[3] for s in by_hand[h]) - min(s[3] for s in by_hand[h]),
            ),
        )
        rows = [s for s in by_hand[target_hand] if s[6] >= self._blink_ear]
        if len(rows) < self._min_samples:
            return dict(_EMPTY, n_pursuit_samples=float(len(rows)))

        ts = [r[0] / 1e6 for r in rows]
        # 2. Active axis: whichever the examiner actually swept.
        span_x = max(r[2] for r in rows) - min(r[2] for r in rows)
        span_y = max(r[3] for r in rows) - min(r[3] for r in rows)
        vertical = span_y > span_x
        excursion = max(span_x, span_y)
        tgt = _median_smooth([r[3] if vertical else r[2] for r in rows], self._smooth_window)
        gaze = _median_smooth([r[5] if vertical else r[4] for r in rows], self._smooth_window)

        base = dict(
            pursuit_axis_vertical=1.0 if vertical else 0.0,
            target_excursion_ipd=round(excursion, 4),
            n_pursuit_samples=float(len(rows)),
            pursuit_coverage=round(min(1.0, len(rows) / max(
                1.0, (ts[-1] - ts[0]) * max(1.0, self.capture.sample_rate))), 3),
        )
        if excursion < self._min_excursion:
            # The target never really moved — reporting a gain here would be
            # dividing noise by noise.
            return dict(_EMPTY, **base)

        # 3. Velocities from real timestamps (cameras are not exactly 30 fps).
        tv, gv = [], []
        for i in range(1, len(ts)):
            dt = ts[i] - ts[i - 1]
            if 1e-4 < dt < 0.5:
                tv.append((tgt[i] - tgt[i - 1]) / dt)
                gv.append((gaze[i] - gaze[i - 1]) / dt)
        if len(tv) < 10:
            return dict(_EMPTY, **base)

        # 4. Catch-up saccades: the gaze jumps while the target does not.
        #    Counted as episodes, not frames — one saccade spans 2-4 frames.
        spikes = [abs(a) > self._catchup_ipd_s and abs(a) > self._catchup_ratio * abs(b)
                  for a, b in zip(gv, tv)]
        catchup, inside = 0, False
        for spike in spikes:
            if spike and not inside:
                catchup += 1
            inside = spike
        span_s = max(1e-3, ts[-1] - ts[0])

        # 5. Gain + R² on the DESACCADED signal: least squares of gaze velocity
        #    on target velocity, saccadic samples removed (one frame of margin
        #    either side, because a saccade smears across neighbours).
        #
        #    Excluding them is not cosmetic. Pursuit gain describes the *smooth*
        #    component; a catch-up saccade contributes a velocity an order of
        #    magnitude larger, and depending on where in the sweep it lands it
        #    drags the slope either way. Leaving them in made the scripted
        #    0.90 scenario read 0.69. The saccades are counted above — they are
        #    a finding of their own, not part of the gain.
        keep = [not (spikes[max(0, i - 1)] or spikes[i] or spikes[min(len(spikes) - 1, i + 1)])
                for i in range(len(spikes))]
        tvs = [v for v, k in zip(tv, keep) if k]
        gvs = [v for v, k in zip(gv, keep) if k]
        if len(tvs) < 10:
            return dict(_EMPTY, **base,
                        catchup_saccades_per_s=round(catchup / span_s, 3))
        n = len(tvs)
        mt, mg = sum(tvs) / n, sum(gvs) / n
        sxx = sum((v - mt) ** 2 for v in tvs)
        sxy = sum((a - mt) * (b - mg) for a, b in zip(tvs, gvs))
        syy = sum((v - mg) ** 2 for v in gvs)
        gain = sxy / sxx if sxx > 1e-9 else 0.0
        r2 = (sxy * sxy) / (sxx * syy) if sxx > 1e-9 and syy > 1e-9 else 0.0

        return dict(base,
                    pursuit_gain=round(gain, 3),
                    pursuit_r2=round(max(0.0, min(1.0, r2)), 3),
                    catchup_saccades_per_s=round(catchup / span_s, 3),
                    pursuit_lag_ms=round(self._lag_ms(ts, tgt, gaze), 1))

    def _lag_ms(self, ts: list[float], tgt: list[float], gaze: list[float]) -> float:
        """Lag of gaze behind target, by best cross-correlation over shifts.

        Positive = the eye trails the finger, which is the physiological case.
        """
        n = len(ts)
        fs = n / max(1e-3, ts[-1] - ts[0])
        max_shift = int(self._max_lag_ms / 1000.0 * fs)
        if max_shift < 1 or n < 2 * max_shift + 10:
            return 0.0
        mt = sum(tgt) / n
        mg = sum(gaze) / n
        t0 = [v - mt for v in tgt]
        g0 = [v - mg for v in gaze]
        best, best_shift = -2.0, 0
        for shift in range(0, max_shift + 1):
            a = t0[:n - shift]
            b = g0[shift:]
            na = math.sqrt(sum(v * v for v in a))
            nb = math.sqrt(sum(v * v for v in b))
            if na < 1e-9 or nb < 1e-9:
                continue
            corr = sum(x * y for x, y in zip(a, b)) / (na * nb)
            if corr > best:
                best, best_shift = corr, shift
        return best_shift / fs * 1000.0
