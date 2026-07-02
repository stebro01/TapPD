"""Shared feature metadata: display names and units for all computed features."""

FEATURE_META: dict[str, tuple[str, str]] = {
    # Motor Performance Index (composite score)
    "mpi": ("Motor Performance Index", ""),

    # Finger Tapping (3.4)
    "tap_frequency_hz": ("Tapping-Frequenz", "Hz"),
    "mean_amplitude_mm": ("Mittlere Amplitude", "mm"),
    "intertap_variability_cv": ("Variabilität (CV)", ""),
    "mean_velocity_mm_s": ("Mittlere Geschwindigkeit", "mm/s"),
    "n_taps": ("Anzahl Taps", ""),

    # Hand Open/Close (3.5)
    "mean_amplitude_mm": ("Mittlere Amplitude", "mm"),
    "cycle_frequency_hz": ("Zyklusfrequenz", "Hz"),
    "mean_velocity_mm_s": ("Mittlere Geschwindigkeit", "mm/s"),
    "n_cycles": ("Anzahl Zyklen", ""),

    # Pronation/Supination (3.6)
    "rotation_frequency_hz": ("Rotationsfrequenz", "Hz"),
    "range_of_motion_deg": ("Bewegungsumfang", "°"),
    "mean_angular_velocity_deg_s": ("Mittl. Winkelgeschw.", "°/s"),

    # Shared
    "amplitude_decrement": ("Amplituden-Dekrement", "/Zyklus"),

    # Tremor (3.15, 3.17) — per hand (R_/L_ prefix)
    "R_dominant_frequency_hz": ("R: Dominante Frequenz", "Hz"),
    "R_translational_amplitude_mm": ("R: Translation-Amplitude", "mm"),
    "R_rotational_amplitude_deg": ("R: Rotations-Amplitude", "°"),
    "R_spectral_power": ("R: Spektrale Leistung", "mm²"),
    "L_dominant_frequency_hz": ("L: Dominante Frequenz", "Hz"),
    "L_translational_amplitude_mm": ("L: Translation-Amplitude", "mm"),
    "L_rotational_amplitude_deg": ("L: Rotations-Amplitude", "°"),
    "L_spectral_power": ("L: Spektrale Leistung", "mm²"),
    "asymmetry_index": ("Asymmetrie-Index (Transl.)", ""),
    "rotation_asymmetry_index": ("Asymmetrie-Index (Rot.)", ""),

    # Legacy keys (single-hand tremor)
    "dominant_frequency_hz": ("Dominante Frequenz", "Hz"),
    "tremor_amplitude_mm": ("Tremor-Amplitude", "mm"),
    "spectral_power": ("Spektrale Leistung", "mm²"),
    "rotational_amplitude_deg": ("Rotations-Amplitude", "°"),

    # Tower of Hanoi
    "completed": ("Gelöst", ""),
    "total_time_s": ("Gesamtzeit", "s"),
    "n_moves": ("Anzahl Züge", ""),
    "optimal_moves": ("Optimale Züge", ""),
    "move_efficiency": ("Zug-Effizienz", ""),
    "planning_time_s": ("Planungszeit", "s"),
    "mean_move_time_s": ("Mittlere Zugzeit", "s"),
    "move_time_cv": ("Zugzeit-Variabilität (CV)", ""),
    "mean_pinch_duration_s": ("Mittlere Greifzeit", "s"),
    "mean_pinch_depth_mm": ("Mittlere Greiftiefe", "mm"),
    "pinch_accuracy": ("Greif-Genauigkeit", ""),
    "mean_trajectory_mm": ("Mittlere Trajektorie", "mm"),
    "trajectory_efficiency": ("Trajektorien-Effizienz", ""),
    "hand_jitter_mm": ("Hand-Jitter", "mm"),

    # Spatial SRT
    "reaction_time_ms": ("Reaktionszeit", "ms"),
    "movement_time_ms": ("Bewegungszeit", "ms"),
    "total_response_time_ms": ("Gesamte Antwortzeit", "ms"),
    "learning_index": ("Lernindex", ""),
    "rt_sequence_mean_ms": ("RT Sequenz (Mittel)", "ms"),
    "rt_random_mean_ms": ("RT Zufall (Mittel)", "ms"),
    "sequence_rt_slope": ("Sequenz-RT Steigung", "ms/Block"),
    "path_efficiency": ("Pfad-Effizienz", ""),
    "peak_velocity_mm_s": ("Spitzengeschwindigkeit", "mm/s"),
    "velocity_variability_cv": ("Geschwindigkeits-CV", ""),
    "error_rate": ("Fehlerrate", ""),
    "fatigue_index": ("Ermüdungsindex", ""),
    "dwell_time_ms": ("Verweilzeit", "ms"),
    "n_trials": ("Anzahl Trials", ""),
    "n_sequence_trials": ("Sequenz-Trials", ""),
    "n_random_trials": ("Zufall-Trials", ""),

    # Trail Making Test
    "tmt_part": ("TMT Teil", ""),
    "n_targets_completed": ("Ziele erreicht", ""),
    "n_targets_total": ("Ziele gesamt", ""),
    "mean_reaction_time_ms": ("Mittlere Reaktionszeit", "ms"),
    "mean_movement_time_ms": ("Mittlere Bewegungszeit", "ms"),
    "movement_time_cv": ("Bewegungszeit-CV", ""),
    "mean_peak_velocity_mm_s": ("Mittlere Spitzengeschw.", "mm/s"),
    "n_errors": ("Anzahl Fehler", ""),
    "error_rate_per_target": ("Fehler pro Ziel", ""),
    "mean_dwell_time_ms": ("Mittlere Verweilzeit", "ms"),

    # Gesten-Batterie (Gesture Lab)
    "battery_score": ("Batterie-Score (korrekt/getestet)", ""),
    "mean_similarity": ("Mittlere Ähnlichkeit", ""),
    "n_poses_tested": ("Getestete Posen", ""),
    "n_correct": ("Korrekt", ""),
    "n_partial": ("Teilweise", ""),
    "n_incorrect": ("Falsch", ""),
    "n_skipped": ("Übersprungen", ""),
}

# Per-pose scores (pose_01_score …) get readable names lazily.
for _n in range(1, 13):
    FEATURE_META[f"pose_{_n:02d}_score"] = (f"Pose #{_n} Ähnlichkeit", "")
del _n


# mm-based units are METRIC ESTIMATES on camera sources: MediaPipe world
# landmarks are model-regressed to an average metric hand, not calibrated to
# the patient. Frequencies, times, angles and ratios are exact everywhere.
_SCALE_ESTIMATED_UNITS = {"mm", "mm/s", "mm/s²", "mm²"}
_SCALE_ESTIMATED_KINDS = {"webcam", "video"}

SCALE_NOTE = ("≈ mm-Skala ist bei Kamera-Quellen eine Modellschätzung (MediaPipe, "
              "unkalibriert) — für Verlauf/Vergleich derselben Hand geeignet; "
              "Frequenzen, Zeiten, Winkel und Verhältnisse sind exakt.")


def unit_label(key: str, source_kind: str = "") -> str:
    """Display unit for a feature; '≈'-prefixed when the mm scale is estimated."""
    unit = FEATURE_META.get(key, (key, ""))[1]
    if unit in _SCALE_ESTIMATED_UNITS and source_kind in _SCALE_ESTIMATED_KINDS:
        return f"≈{unit}"
    return unit


def has_estimated_scale(features: dict, source_kind: str = "") -> bool:
    """Whether any displayed feature of this measurement carries an estimated mm scale."""
    if source_kind not in _SCALE_ESTIMATED_KINDS:
        return False
    return any(FEATURE_META.get(k, (k, ""))[1] in _SCALE_ESTIMATED_UNITS
               for k in features if not k.startswith("_"))
