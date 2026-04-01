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
}
