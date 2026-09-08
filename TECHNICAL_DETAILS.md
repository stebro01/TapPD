# Motryx (ehem. TapPD) – Technische Dokumentation

> Inhalt: Setup, klinische Tests + Features, Signalverarbeitung, MPI,
> Rohdaten-Format, Logging. Die **Komponenten-Gesamtkarte** steht in
> [BLUEPRINT.md](BLUEPRINT.md), die Layer-Contracts in [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. Setup & Installation

### Systemvoraussetzungen

- **Betriebssystem**: Windows 10/11 oder macOS (getestet: Windows 10 Pro, macOS 26 Tahoe)
- **Python**: 3.12 oder hoeher
- **Sensor**: Leap Motion Controller LM-010 (Original, 2013)
- **SDK**: [Ultraleap Tracking Software](https://www.ultraleap.com/downloads/leap-controller/) (Hyperion v6 oder Gemini v5)

### Installation

#### Windows (PowerShell)

```powershell
# 1. Repository klonen
git clone <repo-url>
cd TapPD

# 2. Start-Script erledigt alles automatisch:
#    - Erstellt venv + installiert Abhaengigkeiten
#    - Kopiert LeapC-Bindings aus dem SDK
#    - Benennt .pyd fuer aktuelle Python-Version um
.\start.ps1
```

Alternativ `start.bat` fuer cmd.exe.

#### macOS

```bash
# 1. Repository klonen
git clone <repo-url>
cd TapPD

# 2. Virtual Environment erstellen
python3 -m venv .venv
source .venv/bin/activate

# 3. Abhaengigkeiten installieren
pip install -r requirements.txt

# 4. Ultraleap Tracking Software installieren
#    Download: https://www.ultraleap.com/downloads/leap-controller/
#    Installation: /Applications/Ultraleap Hand Tracking.app

# 5. LeapC Python-Bindings kopieren (aus dem SDK)
cp -r "/Applications/Ultraleap Hand Tracking.app/Contents/LeapSDK/leapc_cffi/" ./leapc_cffi/
```

### Starten

#### Windows

```powershell
.\start.ps1              # Mit Sensor (Auto-Detection)
.\start.ps1 --mock       # Simulationsmodus
```

#### macOS

```bash
./start.sh               # Mit Sensor (Auto-Detection)
./start.sh --mock         # Simulationsmodus
```

### Sensor-Konfiguration

Der Leap Motion Controller LM-010 wird ueber USB angeschlossen und im Desktop-Modus betrieben
(Sensor zeigt nach oben, Haende darueber). Die Kommunikation erfolgt ueber die native LeapC API
via CFFI Python-Bindings (nicht per WebSocket wie beim Legacy-SDK 2.x).

**Wichtig**: Die CFFI-Bindings aus dem SDK sind fuer Python 3.12 kompiliert. Bei neueren
Python-Versionen muss die Binding-Datei kopiert/umbenannt werden (C-ABI ist kompatibel):

- **Windows**: `_leapc_cffi.cp312-win_amd64.pyd` → `_leapc_cffi.cp3XX-win_amd64.pyd`
  (wird von `start.ps1`/`start.bat` automatisch erledigt)
- **macOS**: `_leapc_cffi.cpython-312-darwin.so` → `_leapc_cffi.cpython-3XX-darwin.so`

**Shared Library Pfad**:
- **Windows**: `LeapC.dll` muss im `PATH` liegen. Die Start-Scripts setzen `PATH` auf `leapc_cffi/`.
- **macOS**: `DYLD_LIBRARY_PATH` muss auf das Verzeichnis mit `libLeapC.dylib` zeigen.
  macOS gibt diese Variable nicht an Kind-Prozesse weiter — sie wird in `main.py` und `start.sh` gesetzt.

---

## 2. Projektstruktur

Die Komponenten-Gesamtkarte (Verzeichnisse, Layer, Datenfluesse,
Speicher-Topologie und eine Analyse jeder Komponente) steht in
**[BLUEPRINT.md](BLUEPRINT.md)** — hier nicht dupliziert.

---

## 3. Architektur-Ueberblick

> System-Architektur (Layer, Komponenten, Datenfluesse zwischen ihnen):
> **[BLUEPRINT.md](BLUEPRINT.md)**; Layer-Contracts/APIs: [ARCHITECTURE.md](ARCHITECTURE.md).
> Dieser Abschnitt beschreibt nur das Innenleben der Analyse-Pipeline.

### Config-getriebene Feature-Berechnung

Die gesamte Analyse-Pipeline wird durch `paradigms/test_config.yaml` gesteuert.
Jeder Test definiert:
- **capture**: Welche Metrik aus dem HandFrame extrahiert wird
- **analysis**: Signal-Processing-Parameter (Trimming, Detrend, Onset-Detection, Peak-Detection)
- **features**: Welche Features berechnet werden (Methoden-Name → Berechnung)

`paradigms/recorder.py` implementiert `compute_features_from_config()`, das anhand
des YAML-Configs die richtige Pipeline ausfuehrt. Die Test-Klassen delegieren `compute_features()`
an diese Funktion.

### Datenfluss

```
Sensor (120 Hz) → HandFrame → BaseParadigm.frames[]
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
| `cffi` | >= 1.0 | LeapC Python-Bindings (Backend fuer leapc_cffi) |

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

### Spiegelung & Haendigkeit (Kamera-Quellen)

Zwei Dinge haengen zusammen und werden **an einer einzigen Stelle** entschieden —
im Sidecar, direkt nachdem ein Frame gelesen wurde:

1. **wie das Bild aussieht** (Vorschau) und
2. **welche Hand MediaPipe "links" nennt**.

#### Warum ueberhaupt spiegeln

Eine Webcam liefert die Szene so, wie die Kamera sie sieht. Wer davorsitzt, hat
seine **linke Hand auf der rechten Bildseite** — der Patient sieht sich nicht
wie im Spiegel und greift beim Nachmachen intuitiv falsch.

**Spiegeln allein genuegt aber nicht.** MediaPipe vergibt `Left`/`Right` aus der
Sicht *des Bildes, das es bekommen hat*. Dreht man das Bild um, dreht sich das
Label mit — die gespiegelte Ansicht waere richtig, die Haendigkeit dafuer
falsch. Beides gehoert zusammen:

```
cap.read() ─► cv2.flip(frame, 1) ─► MediaPipe ─► _handedness(label, mirrored=True)
   (roh)      │                                        │
              ├─► Vorschau-JPEG                        └─► Label zurueckgedreht
              └─► Clip-Aufnahme
```

`_handedness()` in `sidecar.py` ist die einzige Stelle, die das Label
korrigiert, und sie liest dasselbe Flag, das auch die Spiegelung steuert
(`self._frames_mirrored`) — die beiden koennen nicht auseinanderlaufen.

| | Bild | Linke Hand erscheint | Label |
|---|---|---|---|
| ohne Spiegelung | Kamerasicht | rechts | `Left` → falsch |
| nur spiegeln | Spiegelsicht | links | `Right` → falsch |
| **spiegeln + Label drehen (Standard)** | Spiegelsicht | **links** | **`Left` → richtig** |

Abschaltbar ueber `capture/capture.yaml` → `sidecar.mirror: false`; dann
entfaellt auch die Label-Korrektur.

#### `flip_handedness` ist etwas anderes

Der Schalter *„Haendigkeit vertauschen"* auf dem Tracking-Screen spiegelt **kein
Bild**. Er vertauscht nur das Etikett `left`/`right` nachtraeglich in
`mediapipe_mapping.hand_from_world()`. Er ist der Notnagel fuer Kameras, die
**selbst schon** spiegeln (manche tun das in Hardware) — dann waere die
Haendigkeit durch die doppelte Spiegelung wieder falsch. Im Normalfall bleibt er
aus.

#### Video-Import (VideoLab): Flag **pro Video**

Was bei einem importierten Video "richtig herum" ist, haengt vom Aufnahmegeraet
ab: eine Frontkamera-Aufnahme zeigt den Patienten oft seitenverkehrt, eine vom
Untersucher gefilmte Rueckkamera-Aufnahme nicht. Global laesst sich das nicht
entscheiden, deshalb entscheidet es **jedes Video fuer sich**:

```
VideoLab, Checkbox „Gespiegelt"
  └─ VideoSession.mirrored          (in session.json gespeichert)
       └─ AnalysisRunner.start(..., mirrored=…)
            └─ WebcamSource.replay_mirror
                 └─ {"cmd":"start", "mirror": …}
                      └─ Sidecar._video_mirror  ─► spiegelt + dreht das Label
```

Die Live-Kamera nutzt weiterhin das globale `sidecar.mirror`; ein Video nutzt
ausschliesslich sein eigenes Flag:

```python
mirror_frames = bool(self._video_mirror) if is_video else bool(self._mirror)
```

Der Standard fuer Videos ist **aus** — ein unveraendert uebernommenes Video
bleibt, wie es ist.

Unabhaengig davon waehlt VideoLab die ausgewertete Hand nicht ueber das
MediaPipe-Label, sondern ueber die **am Segment vermerkte Seite**: es trackt
immer beide Haende und wertet die aus, die sich tatsaechlich bewegt hat. Die
Seitenangabe laesst sich per *„Umbenennen"* korrigieren. Das Spiegel-Flag ist
also die Korrektur fuer *Ansicht und Label*, die Segment-Seite bleibt die
verbindliche klinische Angabe.

#### Auswirkung auf die Okulomotorik: keine

Die Blick-Paradigmen sind gegen eine globale Spiegelung unempfindlich:

- **Sakkaden** arbeiten kalibrierungsbasiert — die 5-Punkt-Eichung lernt die
  Referenz-Offsets in genau der Orientierung, in der auch getestet wird, und
  klassifiziert per naechstem Nachbarn. Ein Vorzeichenwechsel wird absorbiert.
- **Fixation** misst Streuung, also orientierungsunabhaengig.
- Die Kopfpose-Waechter (`eye_roll_deg`, `nose_shift_ipd`) vergleichen jeweils
  gegen eine eigene Baseline aus derselben Sitzung.

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

### Okulomotorik (Kamera-Quellen, FacePose-Stream)

Beide Tests brauchen keine Hand — sie konsumieren den Face-Stream des Sidecars
(Iris-Zentren, Augenwinkel, Eye-Aspect-Ratio, Nasenspitze; volle Framerate).
Auf dem Leap-Sensor sind sie gesperrt (Capability `face_landmarks`).

**Fixation & Blinzeln** (`ocular_fixation`) — Proband schaut ruhig in die
Kamera (Gesichts-Gate: Start erst bei erkanntem Gesicht). Keine Eichung
nötig: gemessen wird die Stabilität relativ zu den Augenwinkeln.

| Feature | Beschreibung | Einheit |
|---------|-------------|---------|
| blink_rate_per_min | Blinzelrate (bei M. Parkinson reduziert) | /min |
| gaze_dispersion_pct_ipd | Fixationsstreuung (RMS, blink-bereinigt) | %IPD |
| saccadic_intrusions_per_min | Abrupte Blicksprünge waehrend Fixation | /min |
| mean_ear | Mittlere Lidspalte (Eye-Aspect-Ratio) | – |
| face_coverage | Anteil Frames mit erkanntem Gesicht | – |

**Sakkaden-Test** (`saccade_test`) — Phase 1: 5-Punkt-Eichung (Ecken + Mitte,
Median-Blickversatz je Punkt als Referenz, Validierung auf Ruhe/Trennbarkeit).
Phase 2 (30 s): gaze-contingente Zufallsziele mit Praeferenz fuer grosse
Spruenge; Treffer = klassifizierte Zone haelt `dwell_s`. Kopfpose-Waechter
(Roll/IPD/Nase relativ zur Eichung) markiert Kopfbewegung als ungueltig.
Alle Schwellen: `paradigms/test_config.yaml` → `saccade_test`.

| Feature | Beschreibung | Einheit |
|---------|-------------|---------|
| n_targets_acquired / targets_per_min | Erreichte Ziele | – bzw. /min |
| median/mean_latency_ms | Sakkaden-Latenz (Stimulus → Blick-Ankunft) | ms |
| direction_error_rate | Erster Blicksprung in falsche Richtung | – |
| head_invalid_pct / blink_pct | Ungueltige Anteile | – |
| calibration_ok | Eichung gueltig | 0/1 |

Bewusst NICHT ausgewiesen: Spitzengeschwindigkeit in °/s — bei 30-Hz-Kamera
nicht messbar (Sakkadendauer 30–80 ms); Latenz-/Zaehlmetriken sind valide.

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

Die Datenbank ist ein **i2b2-Sternschema** (`PATIENT_DIMENSION`,
`VISIT_DIMENSION`, `OBSERVATION_FACT`, `CONCEPT_DIMENSION`, `CODE_LOOKUP`,
`NOTE_FACT`, `GESTURE_TEMPLATE`) — implementiert in `storage/database.py`,
das eine `Patient`/`Session`/`Measurement`-Fassade darueberlegt. Kompakte
Uebersicht mit Spalten-Wirkungen (MPI in `NVAL_NUM`, Provenienz in
`SOURCESYSTEM_CD='TAPPD:<kind>'`, Features im `OBSERVATION_BLOB`):
**[BLUEPRINT.md §3.7](BLUEPRINT.md)**; das ausfuehrliche Referenz-Konzept:
[DB_KONZEPT.md](DB_KONZEPT.md).

Ein frueheres 2-Tabellen-Schema (v1: `patients`/`measurements`) wird beim
ersten Oeffnen automatisch migriert (Backup: `tappd_v1_backup.db`).

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

Der Pfad wird in der Messung als `raw_data_path` vermerkt (im `OBSERVATION_BLOB`).

---

## 9. GUI-Architektur

Screen-Navigationsgraph und Rollen aller UI-Bausteine (11 Screens, Dialoge,
geteilte Widgets): **[BLUEPRINT.md §3.8](BLUEPRINT.md)** — hier nicht dupliziert.

---

## 10. Motor Performance Index (MPI)

### Konzept

Der MPI ist ein normalisierter Composite-Score von **0.0** (schwer betroffen) bis **1.0** (gesund),
der als Verlaufsmarker fuer die drei repetitiven Motorik-Tests dient (3.4, 3.5, 3.6).
Er aggregiert vier klinisch relevante Subdomaenen mit konfigurierbaren Gewichten.

### Formel

```
MPI = w_speed * norm(frequency) + w_amp * norm(amplitude) + w_dec * norm(decrement) + w_reg * norm(regularity)
```

Normalisierung: Lineares Min-Max-Mapping mit Clipping auf [0.0, 1.0]:
```
norm(x) = clamp((x - min_val) / (max_val - min_val), 0.0, 1.0)
```

Fuer invertierte Metriken (z.B. CV, wo niedriger = besser): `norm(x) = 1.0 - norm(x)`

### Gewichte (default)

| Subdomaene | Gewicht | Begruendung |
|---|---|---|
| Speed (Frequenz) | 0.30 | MDS-UPDRS Primaerkriterium |
| Amplitude | 0.30 | MDS-UPDRS Primaerkriterium |
| Decrement (Ermuedung) | 0.20 | Sekundaerkriterium |
| Regularity (CV/Geschw.) | 0.20 | Sekundaerkriterium |

### Referenzwerte

| Test | Speed | Amplitude | Decrement | Regularity |
|---|---|---|---|---|
| Finger Tapping | 1.0-5.0 Hz | 5-30 mm | -0.15 bis 0.0 /Zyklus | CV 0.05-0.40 (inv.) |
| Hand Oeffnen/Schliessen | 0.5-3.5 Hz | 10-60 mm | -0.15 bis 0.0 /Zyklus | 30-300 mm/s |
| Pronation/Supination | 0.5-3.0 Hz | 20-120 Grad | -0.15 bis 0.0 /Zyklus | 40-400 Grad/s |

Referenzwerte sind in `paradigms/test_config.yaml` unter der `mpi:`-Sektion jedes Tests konfigurierbar
und sollten mit klinischen Daten kalibriert werden.

### Validierungsbeispiele

| Profil | Freq | Amp | Dec | CV/Vel | MPI |
|---|---|---|---|---|---|
| Gesunde Kontrolle | 4.5 Hz | 25 mm | 0.00 | 0.08 | **0.885** |
| Moderate PD | 2.5 Hz | 15 mm | -0.08 | 0.25 | **0.412** |
| Schwere PD | 1.2 Hz | 6 mm | -0.14 | 0.38 | **0.052** |

### UI-Darstellung

Der MPI erscheint als **erste Zeile** in der Ergebnistabelle (fett, farbcodiert):
- **Gruen** (>= 0.7): Gering betroffen / normal
- **Gelb** (0.4-0.7): Moderat betroffen
- **Rot** (< 0.4): Schwer betroffen

### Implementation

- Konfiguration: `paradigms/test_config.yaml` (pro Test: `mpi:` Sektion)
- Berechnung: `paradigms/recorder.py` → `_compute_mpi()`
- Wird am Ende von `_compute_unilateral()` aufgerufen
- Propagiert automatisch in DB, CSV-Export, Detail-Dialog, Data-Browser

---

## 11. Logging & Audit

### Architektur

TapPD verwendet Pythons `logging`-Modul mit zentraler Konfiguration in `logging_config.py`.
Beim App-Start wird `setup_logging()` aufgerufen, das drei Handler konfiguriert:

| Handler | Level | Ziel |
|---------|-------|------|
| `TimedRotatingFileHandler` | DEBUG | `data/logs/tappd.log` (tageweise Rotation) |
| `StreamHandler` | INFO | Konsole (stdout) |
| `QtLogHandler` | DEBUG | Qt-Signal → Log Viewer UI |

### Log-Format

```
2026-03-31 09:42:18 | INFO     | capture.leap_capture         | Leap Motion Controller verbunden
```

### Automatische Bereinigung

Log-Dateien aelter als 7 Tage werden beim App-Start automatisch geloescht.
Die Rotation erfolgt taeglich um Mitternacht mit maximal 7 Backup-Dateien.

### Instrumentierte Module

Alle relevanten Module loggen Ereignisse auf passenden Levels:

| Modul | Beispiel-Events |
|-------|----------------|
| `main` | App-Start, Python-Version, Capture-Modus |
| `capture` | Sensor-Diagnose, Device-Erstellung, Verbindung |
| `capture.leap_capture` | Connect/Disconnect, Aufnahme Start/Stop |
| `capture.mock_capture` | Mock-Modus, Aufnahme Start/Stop |
| `paradigms.base_test` | Test Start/Stop, Frame-Anzahl |
| `paradigms.recorder` | Feature-Berechnung, Bilateral-Infos |
| `storage.database` | CRUD-Operationen (Patient, Session, Measurement) |
| `storage.session_store` | Session-Speicherung, CSV-Export |
| `analysis.signal_processing` | Resampling, Bandpass-Skip |
| `ui.main_window` | Navigation, Session-Start, Test-Start |
| `ui.test_screen` | Aufnahme-Callbacks, Fehler |
| `ui.results_screen` | Rohdaten-Speicherung, DB-Updates |

### Log Viewer (GUI)

Ueber den "Log"-Button in der Statusleiste (rechts unten) oeffnet sich ein
Echtzeit-Log-Viewer mit:

- Dunklem Terminal-Design (Consolas/Menlo)
- Farbcodierung nach Level (blau=INFO, orange=WARNING, rot=ERROR)
- Level-Filter (DEBUG/INFO/WARNING/ERROR)
- Auto-Scroll (umschaltbar)
- Laedt die letzten 200 Zeilen aus der aktuellen Log-Datei beim Oeffnen

---

## 12. Bekannte Einschraenkungen

- **Kein klinisches Medizinprodukt**: Forschungsprototyp, nicht fuer diagnostische Entscheidungen
- **Sensor-Limitierungen**: Leap Motion LM-010 hat begrenztes Sichtfeld; schnelle Bewegungen
  und Faust-Schluss koennen Tracking-Verlust verursachen
- **grab_strength fuer Hand Open/Close**: Robuster als Fingertip-Distanz bei Faust, aber
  binaeres Signal (0/1) statt kontinuierlich → Detrend-Artefakte moeglich
- **Einzelplatz**: Keine Multi-User-Faehigkeit, lokale SQLite-Datenbank
- **Plattform-Support**: Windows 10/11 und macOS. Plattformspezifische Unterschiede bei
  Library-Pfaden (PATH vs. DYLD_LIBRARY_PATH) und Sensor-Diagnostik sind im Code abstrahiert.
