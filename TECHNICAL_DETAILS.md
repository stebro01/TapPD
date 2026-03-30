# TapPD – Technische Dokumentation

## 1. Setup & Installation

### Systemvoraussetzungen

- **Betriebssystem**: macOS (getestet unter macOS 26 Tahoe / Darwin 25.3.0)
- **Python**: 3.12 oder hoeher
- **Sensor**: Leap Motion Controller LM-010 (Original, 2013)
- **SDK**: Ultraleap Gemini V5 Hand Tracking Software

### Installation

```bash
# 1. Repository klonen
git clone <repo-url>
cd TapPD

# 2. Virtual Environment erstellen
python3 -m venv .venv
source .venv/bin/activate

# 3. Abhaengigkeiten installieren
pip install -r requirements.txt

# 4. Ultraleap Gemini V5 installieren
#    Download: https://developer.leapmotion.com/tracking-software-download
#    Installation: /Applications/Ultraleap Hand Tracking.app

# 5. LeapC Python-Bindings kopieren (aus dem SDK)
cp -r "/Applications/Ultraleap Hand Tracking.app/Contents/LeapSDK/leapc_cffi/" ./leapc_cffi/
```

### Starten

```bash
# Mit echtem Sensor (Auto-Detection)
./start.sh

# Im Simulationsmodus (ohne Sensor)
./start.sh --mock

# Alternativ direkt
source .venv/bin/activate
export DYLD_LIBRARY_PATH="$(pwd)/leapc_cffi"
python main.py --mock
```

### Sensor-Konfiguration

Der Leap Motion Controller LM-010 wird ueber USB angeschlossen und im Desktop-Modus betrieben
(Sensor zeigt nach oben, Haende darueber). Die Kommunikation erfolgt ueber die native LeapC API
via CFFI Python-Bindings (nicht per WebSocket wie beim Legacy-SDK 2.x).

**Wichtig**: Die CFFI-Bindings aus dem Gemini V5 SDK sind fuer Python 3.12 kompiliert
(`_leapc_cffi.cpython-312-darwin.so`). Fuer Python 3.14 muss die .so-Datei umbenannt werden
zu `cpython-314-darwin.so` – die C-ABI ist kompatibel.

`DYLD_LIBRARY_PATH` muss auf das Verzeichnis mit `libLeapC.dylib` zeigen, da macOS die
Umgebungsvariable beim Exec nicht weitergibt. Dies wird in `main.py` und `start.sh` gesetzt.

---

## 2. Projektstruktur

```
TapPD/
├── main.py                              # Entry Point
├── start.sh                             # Start-Script (aktiviert venv + DYLD)
├── pyproject.toml                       # Projekt-Metadaten
├── requirements.txt                     # Python-Abhaengigkeiten
├── ABOUT.md                             # Info-Dialog Inhalt
├── TECHNICAL_DETAILS.md                 # Diese Datei
├── OPTIMIZATION_PLAN.md                 # Geplante Verbesserungen
│
├── capture/                             # Sensor-Abstraktionsschicht
│   ├── __init__.py                      # Factory: create_capture_device() + Diagnostik
│   ├── base_capture.py                  # HandFrame/FingerData/BoneData Dataclasses + ABC
│   ├── mock_capture.py                  # Simulierte Daten (120 Hz, 8 Modi)
│   ├── leap_capture.py                  # Echtes LeapC SDK via CFFI
│   └── websocket_capture.py             # WebSocket-Fallback (Platzhalter)
│
├── motor_tests/                         # Klinische Motorik-Tests
│   ├── base_test.py                     # BaseMotorTest ABC (uni-/bilateral)
│   ├── config.py                        # YAML-Config-Loader + Cache
│   ├── test_config.yaml                 # Zentrale Test-Konfiguration
│   ├── recorder.py                      # Config-getriebene Feature-Berechnung
│   ├── finger_tapping.py               # MDS-UPDRS 3.4
│   ├── hand_open_close.py              # MDS-UPDRS 3.5
│   ├── pronation_supination.py         # MDS-UPDRS 3.6
│   ├── tremor.py                        # MDS-UPDRS 3.15 (Postural Tremor)
│   ├── rest_tremor.py                   # MDS-UPDRS 3.17 (Ruhetremor)
│   ├── tower_of_hanoi.py               # Tuerme von Hanoi (kognitiv-motorisch)
│   ├── hanoi_logic.py                   # Hanoi-Spiellogik (pure)
│   ├── pinch_detector.py               # Pinzettengriff-Zustandsautomat
│   ├── spatial_srt.py                   # Raeumliche Reaktionszeit (S-SRT)
│   ├── srt_logic.py                     # SRT Block-/Trial-Struktur
│   ├── trail_making.py                  # Trail Making Test (dTMT)
│   └── tmt_logic.py                     # TMT-Zielgenerierung + Segmentverfolgung
│
├── analysis/                            # Signalverarbeitung
│   └── signal_processing.py             # Filter, FFT, Peak-Detection, Onset-Detection
│
├── storage/                             # Datenhaltung
│   ├── database.py                      # SQLite (Patienten + Messungen + raw_data_path)
│   └── session_store.py                 # CSV-Export
│
├── ui/                                  # PyQt6 GUI
│   ├── theme.py                         # Globales Stylesheet, Farbpalette
│   ├── feature_meta.py                  # Feature-Anzeigenamen + Einheiten (shared)
│   ├── main_window.py                   # QMainWindow + Navigation + Auto-Save
│   ├── patient_screen.py               # Patientenauswahl/-erstellung
│   ├── patient_detail_screen.py        # Session-Matrix + Kontext-Menues
│   ├── test_dashboard.py               # Testuebersicht (8 Karten)
│   ├── test_screen.py                  # Hand-Detection + Countdown + Live-Aufnahme
│   ├── hanoi_screen.py                 # Tuerme von Hanoi (QPainter, Pinch-Interaktion)
│   ├── srt_screen.py                   # S-SRT (QPainter, Dwell-Aktivierung)
│   ├── tmt_screen.py                   # dTMT (QPainter, Trail-Linien, Fehler-Feedback)
│   ├── results_screen.py               # Ergebnisanzeige + Plots + Rohdaten-Speicherung
│   ├── data_browser.py                 # Historische Messungen (Filter, Delete, Export)
│   └── detail_dialog.py                # Detail-Ansicht mit Analyse-Plots
│
├── assets/                              # Instruktionsbilder
│   ├── generate_instructions.py         # Generiert PNG-Bilder
│   └── instr_*.png                      # 5 Instruktionsbilder
│
├── leapc_cffi/                          # LeapC SDK Bindings (nicht im Repo)
│   ├── _leapc_cffi.cpython-314-darwin.so
│   ├── libLeapC.dylib
│   └── __init__.py
│
└── data/                                # Laufzeitdaten (nicht im Repo)
    ├── tappd.db                         # SQLite-Datenbank
    └── samples/                         # JSON-Rohdaten
```

---

## 3. Architektur-Ueberblick

### Config-getriebene Feature-Berechnung

Die gesamte Analyse-Pipeline wird durch `motor_tests/test_config.yaml` gesteuert.
Jeder Test definiert:
- **capture**: Welche Metrik aus dem HandFrame extrahiert wird
- **analysis**: Signal-Processing-Parameter (Trimming, Detrend, Onset-Detection, Peak-Detection)
- **features**: Welche Features berechnet werden (Methoden-Name → Berechnung)

`motor_tests/recorder.py` implementiert `compute_features_from_config()`, das anhand
des YAML-Configs die richtige Pipeline ausfuehrt. Die Test-Klassen delegieren `compute_features()`
an diese Funktion.

### Datenfluss

```
Sensor (120 Hz) → HandFrame → BaseMotorTest.frames[]
                                    │
                                    ▼
                        recorder.py: compute_features_from_config()
                                    │
                        ┌───────────┤
                        │           ▼
                        │  _prepare_signal()  →  Resample, Clean, Onset-Detection
                        │           │
                        │           ▼
                        │  _compute_unilateral()  →  Detrend, Peak-Detection, Features
                        │           │
                        │           ▼
                        │  features dict  →  DB (features_json) + UI
                        │
                        │  [bilateral tests]
                        │           ▼
                        │  _compute_bilateral()  →  Per-Hand Tremor + Asymmetrie
                        │           │
                        │           ▼
                        └──→  features dict  →  DB (features_json) + UI
```

---

## 4. Python-Abhaengigkeiten

| Paket | Version | Zweck |
|-------|---------|-------|
| `numpy` | >= 1.26 | Numerische Arrays, Signalverarbeitung |
| `scipy` | >= 1.12 | Butterworth-Filter, FFT, Peak-Detection, Detrending |
| `matplotlib` | >= 3.8 | Echtzeit-Plots, Ergebnis-Diagramme |
| `PyQt6` | >= 6.6 | GUI-Framework |
| `pyyaml` | >= 6.0 | YAML-Konfiguration laden |
| `websockets` | >= 12.0 | WebSocket-Fallback (reserviert) |

---

## 5. Sensor & Datenerfassung

### HandFrame-Datenstruktur

Jeder Frame wird in eine SDK-unabhaengige `HandFrame`-Dataclass konvertiert:

```
HandFrame
├── timestamp_us: int            # Zeitstempel (Mikrosekunden)
├── hand_type: str               # "left" | "right"
├── palm_position: (x, y, z)    # Handflaeche in mm
├── palm_velocity: (x, y, z)    # Geschwindigkeit in mm/s
├── palm_normal: (nx, ny, nz)   # Normalenvektor der Handflaeche
├── fingers: [FingerData x 5]   # Daumen bis kleiner Finger
│   ├── finger_id: 0-4
│   ├── tip_position: (x, y, z)
│   ├── is_extended: bool
│   └── bones: [BoneData x 4]
├── pinch_distance: float        # Daumen-Zeigefinger Abstand
├── grab_strength: float         # Greifstaerke (0.0-1.0)
└── confidence: float            # Tracking-Konfidenz (0.0-1.0)
```

### Mock-Modus

Fuer Entwicklung ohne Sensor generiert `MockCaptureDevice` synthetische Daten bei 120 Hz:

| Modus | Simulation |
|-------|-----------|
| `tapping` | Sinusfoermige Daumen-Index-Distanz, 3 Hz, Amplituden-Dekrement |
| `open_close` | grab_strength oszilliert bei 1.5 Hz mit Fatigue |
| `pronation_supination` | Palm-Normal rotiert bei 1.5 Hz, ±60° |
| `postural_tremor` | Bilateral, 5-6 Hz Sinusoide, R > L Asymmetrie |
| `rest_tremor` | Bilateral, 4-5 Hz, niedrigere Handposition |
| `tower_of_hanoi` | Einhand, Peg-zu-Peg-Bewegung mit Pinch-Zyklen |
| `spatial_srt` | Einhand, Ziel-zu-Ziel-Bewegung (4 Positionen) |
| `trail_making` | Einhand, Pfad durch zufaellig platzierte Ziele |

---

## 6. Klinische Tests (MDS-UPDRS Part III) & Kognitiv-Motorische Paradigmen

### 6.1 Finger Tapping (MDS-UPDRS 3.4)

**Primaermetrik**: Euklidische Distanz Daumen-Zeigefinger (mm)

**Features**: tap_frequency_hz, mean_amplitude_mm, amplitude_decrement,
intertap_variability_cv, mean_velocity_mm_s, n_taps

### 6.2 Hand Oeffnen/Schliessen (MDS-UPDRS 3.5)

**Primaermetrik**: `grab_strength` (0.0 = offen, 1.0 = Faust) — robust auch bei
Finger-Okklusion beim Faustschluss.

**Features**: mean_amplitude, cycle_frequency_hz, mean_velocity_per_s,
amplitude_decrement, n_cycles

### 6.3 Pronation/Supination (MDS-UPDRS 3.6)

**Primaermetrik**: Roll-Winkel aus Palm-Normal-Vektor: `roll(t) = atan2(nx, -ny)` in Grad

**Features**: rotation_frequency_hz, range_of_motion_deg,
mean_angular_velocity_deg_s, amplitude_decrement, n_cycles

### 6.4/6.5 Posturaler Tremor (3.15) / Ruhetremor (3.17) – bilateral

**Primaermetrik**: Palm-Position 3D + Palm-Normal (Roll)

**Pro-Hand Features** (R_/L_ Praefix): dominant_frequency_hz,
translational_amplitude_mm, rotational_amplitude_deg, spectral_power

**Asymmetrie**: asymmetry_index, rotation_asymmetry_index

### 6.6 Tuerme von Hanoi – kognitiv-motorisch (Einhand)

**Paradigma**: Interaktives Tower-of-Hanoi-Spiel mit 3 Scheiben und 3 Staeben.
Alle Scheiben von links nach rechts verschieben, ohne eine groessere auf eine kleinere zu legen.

**Interaktion**: Pinzettengriff (Daumen + Zeigefinger, erkannt via PinchDetector mit
Hysterese: grab < 25mm, release > 40mm, 3-Frame-Debounce). Hand wird in der
Positionierungsphase automatisch erkannt (bei 2 Haenden → rechte Hand).

**Architektur**:
- `hanoi_logic.py`: Reiner Spielzustand (HanoiGameState), Zugvalidierung, Loesungserkennung
- `pinch_detector.py`: Zustandsautomat fuer Greifgesten (OPEN → GRABBING → HOLDING → RELEASING)
- `tower_of_hanoi.py`: Test-Klasse, berechnet 14 Features inkl. Pinch-Metriken und Hand-Jitter
- `hanoi_screen.py`: QPainter-Canvas mit Scheiben, Staeben, Hand-Cursor, Peg-Highlighting

**Features**: completed, total_time_s, n_moves, optimal_moves, move_efficiency,
planning_time_s, mean_move_time_s, move_time_cv, mean_pinch_duration_s,
mean_pinch_depth_mm, pinch_accuracy, mean_trajectory_mm, trajectory_efficiency, hand_jitter_mm

**Detail-Plots**: Handposition-Trajektorie, Greifverhalten (Pinch-Distanz), Zugzeiten, Hand-Jitter

### 6.7 Raeumliche Reaktionszeit (S-SRT) – kognitiv-motorisch (Einhand)

**Paradigma**: Spatial Serial Reaction Time Task. Misst implizites prozedurales Lernen,
das selektiv bei Morbus Parkinson (Basalganglien-Pathologie) beeintraechtigt ist.

**Ablauf**: 4 raeumliche Ziele (oben, rechts, unten, links) auf dem Bildschirm.
Ein Ziel leuchtet auf → Patient bewegt Hand dorthin → 300ms Verweilen → naechstes Ziel.
In Sequenz-Bloecken folgen die Ziele einer versteckten 10-Element-Sequenz;
in Zufalls-Bloecken ist die Reihenfolge pseudozufaellig (keine Wiederholungen).

**Blockstruktur**: 10 Uebungstrials → R1(20) → S1(20) → R2 → S2 → ... → R5 = 190 Trials

**Koordinaten-Mapping**: Leap X (-200..+200mm) → Screen X (0..1),
Leap Z (-100..+100mm) → Screen Y (0..1). Zielradius: 10% normalisiert.

**Trial-Zustandsautomat**: ISI (400ms) → STIMULUS_ON → MOVING (vel > 50mm/s) → IN_TARGET → DWELL (300ms)

**Architektur**:
- `srt_logic.py`: Block-/Trial-Generierung, Sequenzerzeugung (keine aufeinanderfolgenden Wiederholungen)
- `spatial_srt.py`: Test-Klasse, berechnet 17 Features inkl. Lernindex und Ermuedung
- `srt_screen.py`: QPainter-Canvas mit 4 Ziel-Kreisen, Glow-Effekt, Fadenkreuz-Cursor

**Features**: total_time_s, reaction_time_ms, movement_time_ms, total_response_time_ms,
learning_index, rt_sequence_mean_ms, rt_random_mean_ms, sequence_rt_slope, path_efficiency,
peak_velocity_mm_s, velocity_variability_cv, error_rate, fatigue_index, dwell_time_ms,
n_trials, n_sequence_trials, n_random_trials

**Detail-Plots**: RT nach Block (Zufall rot / Sequenz gruen), Lernkurve, Geschwindigkeit, Pfad-Effizienz

### 6.8 Trail Making Test (dTMT) – kognitiv-motorisch (Einhand)

**Paradigma**: Digitaler Trail Making Test. Standardtest der Neuropsychologie,
hier kontaktlos mit kinematischer Analyse.

**Teil A**: 15 zufaellig platzierte Zahlen (1-15) in aufsteigender Reihenfolge verbinden.
Misst Verarbeitungsgeschwindigkeit und visuomotorische Koordination.

**Teil B**: 15 Ziele alternierend Zahlen/Buchstaben (1→A→2→B→3→C→...).
Misst kognitive Flexibilitaet und Set-Shifting (Executive Function).
Die B-A-Differenz in der Gesamtzeit isoliert die kognitive Komponente.

**Zielgenerierung**: Positionen werden per Zufall im Bildschirmbereich (12% Rand)
platziert mit Mindestabstand 15% normalisiert zueinander. Treffradius: 6% normalisiert.

**Visuelles Feedback**: Besuchte Ziele werden gruen; aktives Ziel gelb mit Glow-Effekt;
Verbindungslinien zwischen besuchten Zielen; falsches Ziel → roter Flash (0.5s).
"Naechstes Ziel"-Hinweis am unteren Bildschirmrand.

**Architektur**:
- `tmt_logic.py`: Zielgenerierung (gut verteilt), Label-Erzeugung (A/B), Segment-Tracking
- `trail_making.py`: Test-Klasse, berechnet 14 Features inkl. Fehlerrate und Ermuedung
- `tmt_screen.py`: QPainter-Canvas mit Trail-Linien, Fehler-Feedback, Segment-Zustandsautomat

**Features**: tmt_part, completed, total_time_s, n_targets_completed, n_targets_total,
mean_reaction_time_ms, mean_movement_time_ms, movement_time_cv, path_efficiency,
mean_peak_velocity_mm_s, n_errors, error_rate_per_target, mean_dwell_time_ms, fatigue_index

**Detail-Plots**: Pfadkarte (Ziellayout + Verbindungen), Segmentzeiten, Pfad-Effizienz, Fehler pro Segment

---

## 7. Signalverarbeitung (analysis/signal_processing.py)

### Pipeline-Reihenfolge

```
Rohdaten (HandFrames, ~120 Hz, ungleichmaessig)
    │
    ▼
1. Konfidenz-Filter  (frames mit confidence < 0.3 entfernt)
    │
    ▼
2. Warmup/Cooldown-Trimming  (konfigurierbar per Test)
    │
    ▼
3. Metriken extrahieren  (Distanz, Winkel, Greifstaerke, Position)
    │
    ▼
4. Resampling  (lineare Interpolation auf uniforme ~120 Hz)
    │
    ▼
5. Outlier-Removal  (MAD-basiert, Modified Z-Score > 3.5 → Interpolation)
    │
    ▼
6. Onset/Offset-Detection  (Rolling-Std, nur Bewegungsaufgaben)
    │
    ▼
7. Detrending  (linearer Trend entfernt → Drift-Korrektur)
    │
    ▼
8. [Tremor] Bandpass-Filter  (Butterworth, 3-12 Hz, 4. Ordnung, sosfiltfilt)
    │
    ▼
9. Peak-Detection  (scipy.signal.find_peaks mit Prominence-Schwelle)
    │
    ▼
10. Feature-Berechnung  (config-getrieben)
```

### Onset/Offset-Detection (Bewegungsaufgaben)

Fuer Finger Tapping, Hand Open/Close und Pronation/Supination wird automatisch
der Bewegungsbeginn und das Bewegungsende erkannt:

1. Rolling-Standardabweichung ueber ein konfigurierbares Fenster (default 0.5s)
2. Schwellwert: Prozentsatz der Gesamt-Standardabweichung (default 20%)
3. Onset: Erster Zeitpunkt, an dem die Rolling-Std den Schwellwert ueberschreitet
4. Offset: Letzter Zeitpunkt ueber dem Schwellwert
5. Sicherheit: Mindestens 50% des Signals bleiben erhalten

Onset/Offset-Zeiten werden als `_onset_s` / `_offset_s` in den Features gespeichert
und im Detail-Dialog als vertikale Markierungen angezeigt.

### Verfuegbare Funktionen

| Funktion | Zweck |
|----------|-------|
| `bandpass_filter()` | Butterworth-Bandpass (bidirektional, Null-Phase) |
| `detrend()` | Linearer Trend entfernen |
| `compute_fft()` | Hann-gefensterte FFT → (Frequenzen, Magnituden) |
| `detect_peaks()` | Peak-Detection mit Distance + Prominence |
| `compute_amplitude_decrement()` | Normalisierte Steigung der Peak-Amplituden |
| `remove_outliers()` | MAD-basierte Outlier-Ersetzung |
| `detect_onset_offset()` | Bewegungsbeginn/-ende via Rolling-Std |
| `peak_to_trough_amplitudes()` | Peak-to-Trough pro Zyklus |
| `resample_to_uniform()` | Irregulare Zeitreihe → uniforme Rate |

---

## 8. Datenbank-Design

### Schema

```sql
CREATE TABLE patients (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_code TEXT UNIQUE NOT NULL,
    first_name   TEXT DEFAULT '',
    last_name    TEXT DEFAULT '',
    birth_date   TEXT DEFAULT '',             -- ISO 8601: "YYYY-MM-DD"
    gender       TEXT DEFAULT '',             -- "m", "f", "d", ""
    notes        TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE measurements (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    INTEGER NOT NULL REFERENCES patients(id),
    test_type     TEXT NOT NULL,              -- z.B. "finger_tapping"
    hand          TEXT NOT NULL,              -- "left", "right", "both"
    duration_s    REAL NOT NULL,
    features_json TEXT NOT NULL DEFAULT '{}', -- Berechnete Features als JSON
    recorded_at   TEXT DEFAULT (datetime('now')),
    raw_data_path TEXT DEFAULT ''             -- Pfad zur JSON-Rohdatendatei
);
```

### Rohdaten-Speicherung

JSON-Dateien in `data/samples/` mit Namenskonvention:
`{patient_code}_{test_type}_{hand}_{timestamp}.json`

Inhalt:
- Metadaten (patient_id, test_type, sample_rate, etc.)
- `frames[]` (unilateral) oder `left_frames[]` + `right_frames[]` (bilateral)
- Jeder Frame enthaelt alle HandFrame-Felder als Dict
- Tuerme von Hanoi: zusaetzlich `move_history[]` (from/to_peg, disc, timestamp_s, valid) + `n_discs`
- S-SRT: zusaetzlich `trial_results[]` (17 Felder pro Trial), `blocks[]` (Blockstruktur), `sequence` (versteckte Sequenz)
- dTMT: zusaetzlich `segment_results[]` (11 Felder pro Segment), `targets[]` (Label + Position), `wrong_approaches[]`

Der Pfad wird in `measurements.raw_data_path` vermerkt.

---

## 9. GUI-Architektur

### Screen-Flow

```
PatientScreen → PatientDetailScreen → TestDashboard → TestScreen      → ResultsScreen
                 (Session-Matrix)       (8 Karten)    HanoiScreen       (Auto-Save)
                                                      SRTScreen
                                                      TMTScreen
                                                         │
                                                         └→ DataBrowser → DetailDialog
```

### Screens

1. **PatientScreen**: Patientensuche, -erstellung, -auswahl.
2. **PatientDetailScreen**: Session-Matrix (Zeilen = Sessions, Spalten = Tests).
   Rechtsklick-Kontext-Menues zum Hinzufuegen/Loeschen. L/R-Zellinhalte.
3. **TestDashboard**: 8 Test-Karten in 3-Spalten-Grid. L/R-Indikatoren, Dauer-Spinner.
4. **TestScreen**: Hand-Detection → 3s Countdown → Echtzeit-Aufnahme mit Live-Plot (Motorik-Tests).
5. **HanoiScreen**: Interaktives Hanoi-Spiel. Pinzettengriff, Peg-Highlighting, Erfolgs-Dialog.
6. **SRTScreen**: 4 raeumliche Ziele, Block-basierte Aufgabe, ISI-Steuerung, Trial-Zustandsautomat.
7. **TMTScreen**: Nummerierte/beschriftete Ziele, Trail-Linien, Fehler-Feedback, Teil-A/B-Dialog.
8. **ResultsScreen**: Feature-Tabelle + Plots. Auto-Save in DB.
9. **DataBrowser**: Historische Messungen mit Filter, Loeschen, CSV-/JSON-Export.
10. **DetailDialog**: Feature-Tabelle + 4 Analyse-Plots aus JSON-Rohdaten. L/R-Umschaltung.

---

## 10. Bekannte Einschraenkungen

- **Kein klinisches Medizinprodukt**: Forschungsprototyp, nicht fuer diagnostische Entscheidungen
- **Sensor-Limitierungen**: Leap Motion LM-010 hat begrenztes Sichtfeld; schnelle Bewegungen
  und Faust-Schluss koennen Tracking-Verlust verursachen
- **grab_strength fuer Hand Open/Close**: Robuster als Fingertip-Distanz bei Faust, aber
  binaeres Signal (0/1) statt kontinuierlich → Detrend-Artefakte moeglich
- **Einzelplatz**: Keine Multi-User-Faehigkeit, lokale SQLite-Datenbank
- **macOS-only**: DYLD_LIBRARY_PATH-Handling und LeapC-Bindings sind macOS-spezifisch
