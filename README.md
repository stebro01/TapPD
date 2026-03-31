# TapPD

Kontaktlose Motorik-Analyse bei Morbus Parkinson mittels Leap Motion Controller.

TapPD digitalisiert die motorischen Handtests der MDS-UPDRS Part III. Handbewegungen
werden kontaktlos per Infrarot-Sensor erfasst und quantitative Parameter automatisch berechnet.

## Features

- 5 klinische Motorik-Tests (MDS-UPDRS 3.4, 3.5, 3.6, 3.15, 3.17)
- 3 kognitiv-motorische Paradigmen (Tuerme von Hanoi, Spatial SRT, Trail Making Test)
- Echtzeit-Visualisierung waehrend der Aufnahme
- Automatische Feature-Berechnung mit Artefakt-Korrektur
- YAML-konfigurierbare Analyse-Pipeline (test_config.yaml)
- Auto-Onset/Offset-Detection fuer Bewegungsaufgaben
- Bilaterale Tremor-Analyse (Translation + Rotation, Asymmetrie)
- Patientenverwaltung mit SQLite-Datenbank
- Daten-Browser mit Detail-Ansicht inkl. Analyse-Plots
- Rohdaten-Speicherung als JSON (optional)
- CSV-Export fuer statistische Auswertung
- Simulationsmodus fuer Entwicklung ohne Sensor
- Zentrales Logging-System mit tageweiser Rotation (data/logs/)
- Log Viewer im GUI (Statusleiste → "Log"-Button)

## Voraussetzungen

- **Windows 10/11** oder **macOS** (getestet: Windows 10 Pro, macOS 26 Tahoe)
- Python 3.12+
- Leap Motion Controller LM-010 (Original, 2013)
- [Ultraleap Tracking Software](https://www.ultraleap.com/downloads/leap-controller/) (Hyperion v6 oder Gemini v5)

## Installation

```bash
# Repository klonen
git clone <repo-url>
cd TapPD
```

### Windows (empfohlen: PowerShell)

```powershell
# Start-Script erstellt venv, installiert Abhaengigkeiten,
# kopiert LeapC-Bindings aus dem SDK und startet die App:
.\start.ps1
```

Alternativ mit `start.bat` (cmd.exe).

### macOS

```bash
# Virtual Environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Sensor-Setup

1. **Ultraleap Tracking Software** installieren (aus dem Link oben)
2. Tracking-Service starten:
   - **Windows**: Laeuft automatisch als Dienst (LeapSvc.exe)
   - **macOS**: `/Applications/Ultraleap Hand Tracking.app` oeffnen
3. LeapC Python-Bindings ins Projekt kopieren:
   - **Windows**: `start.ps1` / `start.bat` erledigt dies automatisch aus `C:\Program Files\Ultraleap\LeapSDK\leapc_cffi\`
   - **macOS**: `cp -r "/Applications/Ultraleap Hand Tracking.app/Contents/LeapSDK/leapc_cffi/" ./leapc_cffi/`
4. Falls die Python-Version nicht mit den SDK-Bindings uebereinstimmt (SDK liefert 3.12):
   - **Windows**: `start.ps1` / `start.bat` benennt die `.pyd`-Datei automatisch um
   - **macOS**: `cp leapc_cffi/_leapc_cffi.cpython-312-darwin.so leapc_cffi/_leapc_cffi.cpython-3XX-darwin.so`
5. Leap Motion Controller per USB anschliessen (LED sollte gruen leuchten)

## Quickstart

### Windows

```powershell
# Mit Sensor (Auto-Detection)
.\start.ps1

# Ohne Sensor (Simulationsmodus)
.\start.ps1 --mock
```

### macOS

```bash
# Mit Sensor (Auto-Detection)
./start.sh

# Ohne Sensor (Simulationsmodus)
./start.sh --mock
```

### Bedienung

1. **Patient waehlen** oder neuen anlegen
2. **Test anklicken** im Dashboard (8 Test-Karten)
3. **Hand waehlen** (L/R) bei unilateralen Tests, Auto-Detection bei kognitiven Tests
4. **Hand-Detection** → 3-2-1 Countdown → Aufnahme mit Live-Plot / interaktive Aufgabe
5. **Ergebnisse** werden automatisch gespeichert
6. **Verwerfen** / **Neu aufnehmen** / **Fortfahren** (mit optionaler Rohdaten-Speicherung)
7. Weitere Tests durchfuehren oder Session beenden

## Projektstruktur

```
TapPD/
├── main.py                     # Entry Point
├── logging_config.py           # Zentrales Logging (File + Console + Qt)
├── start.sh                    # Start-Script (macOS)
├── start.ps1                   # Start-Script (Windows PowerShell)
├── start.bat                   # Start-Script (Windows cmd)
├── requirements.txt            # Python-Abhaengigkeiten
├── pyproject.toml              # Projekt-Metadaten
│
├── capture/                    # Sensor-Abstraktionsschicht
│   ├── __init__.py             #   Factory: create_capture_device()
│   ├── base_capture.py         #   HandFrame Dataclass + ABC
│   ├── mock_capture.py         #   Simulierte Daten (120 Hz)
│   ├── leap_capture.py         #   Echtes LeapC SDK
│   └── websocket_capture.py    #   WebSocket-Fallback (Platzhalter)
│
├── motor_tests/                # Klinische Tests
│   ├── base_test.py            #   BaseMotorTest ABC
│   ├── config.py               #   YAML-Config-Loader
│   ├── test_config.yaml        #   Zentrale Test-Konfiguration
│   ├── recorder.py             #   Config-getriebene Feature-Berechnung
│   ├── finger_tapping.py       #   MDS-UPDRS 3.4
│   ├── hand_open_close.py      #   MDS-UPDRS 3.5
│   ├── pronation_supination.py #   MDS-UPDRS 3.6
│   ├── tremor.py               #   MDS-UPDRS 3.15 (Posturaler Tremor)
│   ├── rest_tremor.py          #   MDS-UPDRS 3.17 (Ruhetremor)
│   ├── tower_of_hanoi.py       #   Tuerme von Hanoi (kognitiv-motorisch)
│   ├── hanoi_logic.py          #   Hanoi-Spiellogik
│   ├── pinch_detector.py       #   Pinzettengriff-Erkennung
│   ├── spatial_srt.py          #   Raeumliche Reaktionszeit (S-SRT)
│   ├── srt_logic.py            #   SRT-Aufgabenlogik
│   ├── trail_making.py         #   Trail Making Test (dTMT)
│   └── tmt_logic.py            #   TMT-Aufgabenlogik
│
├── analysis/                   # Signalverarbeitung
│   └── signal_processing.py    #   Filter, FFT, Peak-Detection, Onset-Detection
│
├── storage/                    # Datenhaltung
│   ├── database.py             #   SQLite (Patienten + Messungen)
│   └── session_store.py        #   CSV-Export
│
├── ui/                         # PyQt6 GUI
│   ├── theme.py                #   Stylesheet + Farbpalette
│   ├── feature_meta.py         #   Feature-Anzeigenamen + Einheiten
│   ├── main_window.py          #   Hauptfenster + Navigation
│   ├── patient_screen.py       #   Patientenauswahl
│   ├── patient_detail_screen.py #  Session-Matrix mit Kontext-Menues
│   ├── test_dashboard.py       #   Test-Uebersicht (8 Karten)
│   ├── test_screen.py          #   Live-Aufnahme (Motorik-Tests)
│   ├── hanoi_screen.py         #   Tuerme von Hanoi (interaktiv)
│   ├── srt_screen.py           #   Raeumliche Reaktionszeit (S-SRT)
│   ├── tmt_screen.py           #   Trail Making Test (dTMT)
│   ├── results_screen.py       #   Ergebnis-Anzeige
│   ├── data_browser.py         #   Daten-Browser
│   ├── detail_dialog.py        #   Detail-Ansicht mit Analyse-Plots
│   └── log_viewer.py           #   Log Viewer Dialog (Live-Logs)
│
├── assets/                     # Instruktionsbilder
│   └── instr_*.png             #   5 Instruktionsbilder
│
├── leapc_cffi/                 # LeapC SDK (nicht im Repo)
└── data/                       # SQLite DB + Rohdaten (nicht im Repo)
    ├── tappd.db
    ├── samples/                #   JSON-Rohdaten
    └── logs/                   #   Log-Dateien (tageweise, 7 Tage)
```

## Tests & Berechnete Features

### Finger Tapping (3.4) – unilateral

| Feature | Beschreibung | Einheit |
|---------|-------------|---------|
| tap_frequency_hz | Tapping-Frequenz | Hz |
| mean_amplitude_mm | Mittlere Oeffnungsamplitude | mm |
| amplitude_decrement | Ermuedungs-Dekrement (normalisiert) | /Zyklus |
| intertap_variability_cv | Rhythmus-Variabilitaet (CV) | – |
| mean_velocity_mm_s | Mittlere Geschwindigkeit | mm/s |
| n_taps | Anzahl Taps | – |

### Hand Oeffnen/Schliessen (3.5) – unilateral

| Feature | Beschreibung | Einheit |
|---------|-------------|---------|
| mean_amplitude | Mittlere Greifstaerke-Amplitude | – |
| cycle_frequency_hz | Zyklusfrequenz | Hz |
| mean_velocity_per_s | Mittlere Geschwindigkeit | /s |
| amplitude_decrement | Ermuedungs-Dekrement | /Zyklus |
| n_cycles | Anzahl Zyklen | – |

### Pronation/Supination (3.6) – unilateral

| Feature | Beschreibung | Einheit |
|---------|-------------|---------|
| rotation_frequency_hz | Rotationsfrequenz | Hz |
| range_of_motion_deg | Bewegungsumfang | ° |
| mean_angular_velocity_deg_s | Mittlere Winkelgeschwindigkeit | °/s |
| amplitude_decrement | Ermuedungs-Dekrement | /Zyklus |
| n_cycles | Anzahl Zyklen | – |

### Tremor (3.15, 3.17) – bilateral

Pro Hand (Praefix `R_` / `L_`):

| Feature | Beschreibung | Einheit |
|---------|-------------|---------|
| dominant_frequency_hz | Dominante Tremor-Frequenz | Hz |
| translational_amplitude_mm | RMS translationaler Tremor | mm |
| rotational_amplitude_deg | RMS rotationaler Tremor | ° |
| spectral_power | Spektrale Leistung (3-12 Hz) | mm² |

Plus Asymmetrie-Indizes:

| Feature | Berechnung | Bereich |
|---------|-----------|---------|
| asymmetry_index | (R - L) / (R + L) Translation | -1.0 bis +1.0 |
| rotation_asymmetry_index | (R - L) / (R + L) Rotation | -1.0 bis +1.0 |

### Tuerme von Hanoi – Einhand, kognitiv-motorisch

Interaktives Scheiben-Verschieben (3 Scheiben, 3 Staebe). Pinzettengriff zum Greifen,
Hand ueber Stab bewegen, Loslassen zum Ablegen. Hand wird automatisch erkannt.

| Feature | Beschreibung | Einheit |
|---------|-------------|---------|
| completed | Aufgabe geloest (1) oder aufgegeben (0) | – |
| total_time_s | Gesamtzeit | s |
| n_moves | Anzahl gueltige Zuege | – |
| optimal_moves | Optimale Zuege (2^n - 1) | – |
| move_efficiency | optimal / tatsaechlich | – |
| planning_time_s | Zeit vor erstem Zug | s |
| mean_move_time_s | Mittlere Zugzeit | s |
| move_time_cv | Zugzeit-Variabilitaet (CV) | – |
| mean_pinch_duration_s | Mittlere Greifzeit | s |
| mean_pinch_depth_mm | Mittlere Greiftiefe | mm |
| pinch_accuracy | Erfolgreiche Griffe / Greif-Episoden | – |
| mean_trajectory_mm | Mittlere Pfadlaenge pro Zug | mm |
| trajectory_efficiency | Geradeaus-Distanz / Pfadlaenge | – |
| hand_jitter_mm | Hochfrequenter Hand-Jitter (Tremor-Proxy) | mm |

### Raeumliche Reaktionszeit (S-SRT) – Einhand, kognitiv-motorisch

Misst implizites prozedurales Lernen (Basalganglien-abhaengig). 4 raeumliche Ziele
auf dem Bildschirm leuchten nacheinander auf. Der Patient bewegt die Hand zum leuchtenden
Ziel und haelt kurz (300 ms Dwell). In Sequenz-Bloecken folgen die Ziele einer versteckten
10-Element-Sequenz; in Zufalls-Bloecken ist die Reihenfolge zufaellig. Lerneffekt wird
als RT-Differenz zwischen Zufall- und Sequenz-Bloecken gemessen.

**Aufbau**: 10 Uebungstrials → 9 Bloecke (abwechselnd Zufall/Sequenz, je 20 Trials) = 190 Trials

| Feature | Beschreibung | Einheit |
|---------|-------------|---------|
| total_time_s | Gesamtdauer | s |
| reaction_time_ms | Stimulus → Bewegungsbeginn | ms |
| movement_time_ms | Bewegungsbeginn → Zielankunft | ms |
| total_response_time_ms | Stimulus → Dwell abgeschlossen | ms |
| learning_index | (RT_Zufall - RT_Sequenz) / RT_Zufall | – |
| rt_sequence_mean_ms | Mittlere RT in Sequenz-Bloecken | ms |
| rt_random_mean_ms | Mittlere RT in Zufalls-Bloecken | ms |
| sequence_rt_slope | Steigung der RT ueber Sequenz-Bloecke (Lernkurve) | ms/Block |
| path_efficiency | Geradeaus / tatsaechlicher Pfad | – |
| peak_velocity_mm_s | Mittlere Spitzengeschwindigkeit | mm/s |
| velocity_variability_cv | Geschwindigkeits-Variationskoeffizient | – |
| error_rate | Anteil falscher Ziel-Anfahrten | – |
| fatigue_index | RT-Aenderung erster vs. letzter Block | – |
| dwell_time_ms | Mittlere Verweilzeit am Ziel | ms |

**Detail-Plots**: RT nach Block (Zufall rot / Sequenz gruen), Lernkurve, Geschwindigkeit, Pfad-Effizienz

### Trail Making Test (dTMT) – Einhand, kognitiv-motorisch

Digitaler Trail Making Test. Misst Verarbeitungsgeschwindigkeit (Teil A)
und kognitive Flexibilitaet / Set-Shifting (Teil B).

- **Teil A**: 15 Zahlen (1-15) in aufsteigender Reihenfolge verbinden
- **Teil B**: Abwechselnd Zahlen und Buchstaben (1→A→2→B→3→C→...)

Ziele werden zufaellig auf dem Bildschirm platziert. Der Patient bewegt die Hand
zum naechsten Ziel und haelt kurz. Fehlerhafte Anfahrten (falsches Ziel) werden
rot markiert und gezaehlt. Verbindungslinien zeigen den zurueckgelegten Pfad.

| Feature | Beschreibung | Einheit |
|---------|-------------|---------|
| tmt_part | Teil A (1) oder B (2) | – |
| completed | Erfolgreich abgeschlossen | – |
| total_time_s | Gesamtzeit | s |
| n_targets_completed | Erreichte Ziele | – |
| mean_reaction_time_ms | Mittlere Reaktionszeit | ms |
| mean_movement_time_ms | Mittlere Bewegungszeit | ms |
| movement_time_cv | Bewegungszeit-Variabilitaet (CV) | – |
| path_efficiency | Geradeaus / tatsaechlicher Pfad | – |
| mean_peak_velocity_mm_s | Mittlere Spitzengeschwindigkeit | mm/s |
| n_errors | Gesamtzahl falscher Anfahrten | – |
| error_rate_per_target | Fehler pro Ziel | – |
| mean_dwell_time_ms | Mittlere Verweilzeit | ms |
| fatigue_index | Bewegungszeit-Aenderung Anfang vs. Ende | – |

**Detail-Plots**: Pfadkarte, Segmentzeiten, Pfad-Effizienz, Fehler pro Segment

## Motor Performance Index (MPI)

Fuer die drei repetitiven Motorik-Tests wird ein zusammengefasster **Motor Performance Index**
als normalisierter Verlaufsmarker berechnet (0.0 = schwer betroffen, 1.0 = gesund).

Der MPI aggregiert vier Subdomaenen, angelehnt an die MDS-UPDRS Bewertungskriterien:

| Subdomaene | Gewicht | Was wird gemessen? |
|---|---|---|
| Speed | 30% | Bewegungsfrequenz (Hz) |
| Amplitude | 30% | Oeffnungsweite / ROM |
| Decrement | 20% | Ermuedung ueber die Aufnahmedauer |
| Regularity | 20% | Rhythmusstabilitaet (CV) oder Geschwindigkeit |

Jede Komponente wird gegen konfigurierbare Referenzwerte (Literatur: Butt 2018, Heldman 2014)
linear normalisiert. Die Referenzwerte koennen in `motor_tests/test_config.yaml` angepasst werden.

Der MPI wird als **erste Zeile** in der Ergebnistabelle angezeigt, farbcodiert:
gruen (>0.7), gelb (0.4-0.7), rot (<0.4).

## Ausgabeformate

### Automatische Speicherung

Jede Messung wird automatisch in der SQLite-Datenbank gespeichert (`data/tappd.db`).
Optional koennen Rohdaten als JSON in `data/samples/` gespeichert werden (Checkbox auf dem Ergebnis-Screen).

### CSV-Export

- **Einzelmessung**: Ueber "CSV Export" auf dem Ergebnis-Screen oder im Daten-Browser
- **Alle Messungen**: Ueber "Alle als CSV" im Daten-Browser

### JSON-Rohdaten

Enthaelt alle HandFrame-Daten fuer Offline-Analyse:
- Unilateral: `frames[]` mit Zeitstempeln, Fingerpositionen, Greifstaerke etc.
- Bilateral: `left_frames[]` + `right_frames[]` mit Palm-Positionen, Normalen etc.
- Hanoi: zusaetzlich `move_history[]` (Zuege mit Zeitstempeln) + `n_discs`
- S-SRT: zusaetzlich `trial_results[]` (pro Trial: RT, Pfad, Geschwindigkeit), `blocks[]`, `sequence`
- dTMT: zusaetzlich `segment_results[]`, `targets[]` (Layout), `wrong_approaches[]`

### SQLite-Datenbank

Direkter Zugriff:

```bash
sqlite3 data/tappd.db "SELECT * FROM measurements ORDER BY recorded_at DESC"
```

## Troubleshooting

### "Sensor nicht erkannt" beim Start

1. **Ultraleap Software installiert?**
   - **Windows**: `C:\Program Files\Ultraleap\` muss vorhanden sein
   - **macOS**: `/Applications/Ultraleap Hand Tracking.app` muss vorhanden sein

2. **Tracking-Service laeuft?**
   - **Windows**: `tasklist /FI "IMAGENAME eq LeapSvc.exe"` (startet normalerweise automatisch)
   - **macOS**: `pgrep -f libtrack_server` (Ultraleap Hand Tracking App oeffnen falls leer)

3. **Controller per USB angeschlossen?**
   LED am Controller sollte gruen leuchten. Anderes USB-Kabel oder anderen Port versuchen.
   - **Windows**: Geraete-Manager pruefen (Ultraleap / Leap Motion unter USB-Geraete)
   - **macOS**: `ioreg -p IOUSB -l | grep -i leap`

4. **LeapC-Bindings vorhanden?**
   Das Verzeichnis `leapc_cffi/` muss vorhanden sein mit:
   - **Windows**: `_leapc_cffi.cp3XX-win_amd64.pyd` + `LeapC.dll`
   - **macOS**: `_leapc_cffi.cpython-3XX-darwin.so` + `libLeapC.dylib`

   Auf Windows kopiert `start.ps1`/`start.bat` diese automatisch aus dem SDK.

5. **Nur eine App-Instanz gleichzeitig**
   LeapC erlaubt nur eine aktive Verbindung. Falls eine alte Instanz laeuft,
   diese zuerst schliessen.

### Import-Fehler `_leapc_cffi`

Die Bindings aus dem SDK sind fuer Python 3.12 kompiliert. Bei neueren Python-Versionen
muss die Datei kopiert/umbenannt werden (C-ABI ist kompatibel):

- **Windows**: `start.ps1`/`start.bat` erledigt dies automatisch
- **macOS**: `cp leapc_cffi/_leapc_cffi.cpython-312-darwin.so leapc_cffi/_leapc_cffi.cpython-3XX-darwin.so`

(XX durch die eigene Minor-Version ersetzen, z.B. 314 fuer Python 3.14)

### App startet, aber kein Live-Plot

- **Windows**: Am einfachsten `start.ps1` verwenden (setzt PATH automatisch)
- **macOS**: `DYLD_LIBRARY_PATH` muss gesetzt sein. Am einfachsten `./start.sh` verwenden.
  macOS gibt `DYLD_LIBRARY_PATH` nicht an Kind-Prozesse weiter. Die App setzt die
  Variable intern in `main.py`.

### "No module named PyQt6"

```bash
pip install -r requirements.txt
```

Auf Windows erledigen `start.ps1`/`start.bat` dies automatisch beim ersten Start.

## Weitergehende Dokumentation

- [ABOUT.md](ABOUT.md) – Kurzinfo zum Projekt
- [TECHNICAL_DETAILS.md](TECHNICAL_DETAILS.md) – Ausfuehrliche technische Dokumentation
- [OPTIMIZATION_PLAN.md](OPTIMIZATION_PLAN.md) – Geplante Verbesserungen
