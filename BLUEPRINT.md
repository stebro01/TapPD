# BLUEPRINT — Motryx (ehem. TapPD)

> Multimodales Bewegungs-Assessment-Labor: Hand-Tracking heute, Gesicht/Okulomotorik
> vorbereitet. Dieses Dokument analysiert **alle Komponenten** und ihre Wirkbeziehungen.
> Stand: Juli 2026, Branch `development`. (Ergänzt ARCHITECTURE.md um die Gesamtsicht;
> DB-Details in DB_KONZEPT.md, Sidecar-Protokoll in mediapipe_sidecar/PROTOCOL.md.)

---

## 1. Gesamtbild — Schichten und Wirkungswege

```
                                   ┌────────────────────────────────────────────────┐
                                   │                    UI  (ui/)                   │
                                   │  PatientScreen → PatientDetail ─┬─ 📈 Verlauf  │
                                   │        │                        ├─ DetailDialog│
                                   │        ▼                        └─ CSV-Export  │
                                   │  TestDashboard → TestScreen / Hanoi/SRT/TMT    │
                                   │  VideoLab │ GestureLab │ Eingabequelle │ Results│
                                   └───────┬──────────┬─────────────┬───────────────┘
                                           │ frames   │ clips/play  │ save/load
                 SOURCE-LAYER              ▼          ▼             ▼
┌───────────────────────────────┐   ┌────────────────────┐   ┌──────────────────────┐
│        capture/  (Source)     │   │   video/ (Service)  │   │   storage/ + video/  │
│                               │   │                     │   │      (Persistenz)    │
│  MotionSource (ABC/Protocol)  │   │ record / import /   │   │ tappd.db (i2b2-Stern)│
│  ├─ LeapSource      (USB/CFFI)│◄──┤ transcode / extract │   │ VideoSession (JSON)  │
│  ├─ WebcamSource ───┐ (Socket)│   │ (+Deface, EyeRef,   │   │ Clips (data/clips)   │
│  ├─ SimulationSource│         │   │  Rotate) / store /  │   │ Raw-JSON (sessions/) │
│  └─ ReplaySource    │         │   │ export → DB         │   └──────────▲───────────┘
│  SourceProfile      │         │   └─────────┬───────────┘              │
│  (Caps/Ready/adapt) │         │             │ VideoClip / play_range   │ Measurement
└─────────────────────┼─────────┘             ▼                          │
                      │            ┌────────────────────┐                │
                      └───────────►│ mediapipe_sidecar/ │                │
                        JSON/TCP   │  (Py 3.12 + cv2 +  │                │
                        loopback   │   MediaPipe)       │                │
                                   │ Hand- & FaceLandm. │                │
                                   └────────────────────┘                │
                 PARADIGM-LAYER                                          │
┌────────────────────────────────────────────────────────────┐          │
│                    paradigms/  (Paradigm)                │          │
│  registry.py (SINGLE SOURCE OF TRUTH: 9 ParadigmSpecs)     │          │
│  BaseParadigm ── ParadigmRunner (geteilter Frame-Pump)    │──────────┘
│  recorder.py (config-getriebene Feature-Berechnung + MPI)  │  features
│       │  nutzt                                             │
│       ▼                                                    │
│  analysis/signal_processing.py (DSP: Bandpass, FFT, Peaks) │
└────────────────────────────────────────────────────────────┘
        parallel:  gesture_lab/  (eigene Feature-Pipeline, teilt HandFrame + tappd.db)
```

### Frame-Fluss eines Live-Tests

```
Sensor ──► MotionSource ──callback──► ParadigmRunner.feed()
                                        │  SETTLE-Gate, Dauer-Gate
                                        │  adapt_frame (SourceProfile-Seam:
                                        │   Webcam+EyeRef → absolute ≈mm-Position)
                                        ├─► BaseParadigm._on_frame  → frames[]
                                        └─► get_live_metric → live{}  → LiveMetricPlot
Ende (Dauer erreicht / done):
  compute_features_from_config(test_config.yaml) ──► features{} + MPI
        │
        ├─► ResultsScreen (Tabelle, ≈mm-Kennzeichnung, Plots)
        └─► save_measurement → OBSERVATION_FACT  (+ Raw-JSON nach data/sessions/)
```

### VideoLab-Fluss (Telemedizin: Handy-Video → Befund)

```
Video-Datei ─► VideoImporter ─► Sidecar transcode (H.264, ≤1280, 30fps, [↻90°])
                 │                                   │
                 ▼                                   ▼
          VideoSession(JSON) ◄─── Segment wählen (VideoTimeline: Onset/Offset)
                 │                        │
                 │                        ▼
                 │              VideoSegmentExtractor ─► Sidecar extract:
                 │                Schnitt + DEFACE (hand-aware!) + .eyeref.json + Thumb
                 ▼
        AnalysisRunner.start(ORIGINAL-Video, start, end, Paradigma)
                 │   WebcamSource.play_range → Sidecar (einmalig, "done")
                 │   Face erzwungen bei abs_position-Paradigmen (Tremor)
                 ▼
        ParadigmRunner → Features → Segment.results  ──"→ In Patientenakte"──►
                                                   video/export.py → Measurement(DB)
```

### Speicher-Topologie

```
data/
├── tappd.db ──────────── i2b2-Sternschema (EINE SQLite-Datei)
│     PATIENT_DIMENSION ◄─┐            CONCEPT_DIMENSION (TAPPD:* Konzepte)
│     VISIT_DIMENSION ◄───┤                 ▲
│     OBSERVATION_FACT ───┴─ CONCEPT_CD ────┘   ┌ NVAL_NUM = MPI
│       │  OBSERVATION_BLOB (JSON: features,     ├ TVAL_CHAR = Hand
│       │  hand, duration, raw_path, source_kind)├ SOURCESYSTEM_CD = TAPPD:<kind>
│       │                                        └ ENCOUNTER_NUM = Session
│     CODE_LOOKUP, NOTE_FACT, GESTURE_TEMPLATE (GestureLab, zentral registriert)
├── video_sessions/<code>/session.json  ─ VideoLab (Segmente, Ergebnisse,
│         │                                db_session_id, measurement_id-Stempel)
│         └── video_*.mp4, seg_*.mp4 (+.thumb.jpg, +.eyeref.json)
├── clips/          ─ Sim-Quelle (default.mp4 + Landmark-JSON-Clips für Replay)
├── sessions/       ─ Raw-Frame-JSON pro Messung (session_store)
└── logs/           ─ zentrales Logging (logging_config, GUI-LogViewer)
```

---

## 2. Kernkonzepte

| Konzept | Bedeutung |
|---|---|
| **Source** | Austauschbare Datenquelle (`MotionSource`-Contract: connect/disconnect/is_connected/start_recording(cb)/stop_recording/sample_rate). Hot-swap zur Laufzeit über die Eingabequelle. |
| **Paradigm** | Klinische/kognitive Aufgabe (`BaseParadigm`), konsumiert Frames, liefert `compute_features()`. Einmalig deklariert in der **Registry** — Dashboard, Routing, Storage-Kategorie und Gating leiten sich daraus ab. |
| **Capability-Gating** | Sources deklarieren Fähigkeiten (`fingertips`, `finger_flexion`, `hand_pose`, `abs_position`), Paradigmen ihren Bedarf (`test_config.yaml → requires`). UI sperrt Unerfülltes (🔒). **Dynamisch:** Webcam meldet `abs_position` nur bei aktivem Face-Tracking (`extra_capabilities`). |
| **adapt_frame-Seam** | `SourceProfile.adapt_frame` ist DIE Stelle für Quell-Normalisierung. Aktiv: Webcam ersetzt `palm_position` durch die Augen-referenzierte Absolutposition (Kopie, idempotent). |
| **Eye-Referenz** | Iris-Zentren (IPD Ø 63 mm) liefern mm-pro-Pixel + Ursprung → absolute Handposition aus RGB. Schaltet Tremor auf Kamera-Quellen frei; Coverage wird gespeichert, <50 % ⇒ Warnung. |
| **≈mm-Skala** | MediaPipe-World-Landmarks sind Modellschätzungen (unkalibriert). Frequenzen/Zeiten/Winkel exakt; mm-Werte bei `source_kind ∈ {webcam, video}` als „≈mm" gekennzeichnet (`ui/feature_meta.py`), im Verlauf hohle Punkte. |
| **Provenienz** | Jede Messung trägt `source_kind` (leap/webcam/mock/video) — im Blob UND SQL-filterbar als `SOURCESYSTEM_CD='TAPPD:<kind>'`. |
| **MPI** | Motor Performance Index: gewichteter Komposit-Score (0–1) aus normierten Features (`test_config.yaml → mpi`), gespeichert in `NVAL_NUM`, Ampel-Farben in der UI, Default-Merkmal im Verlauf. |
| **Sidecar-Trennung** | Haupt-App = Python 3.14 **ohne cv2**; alles cv2/MediaPipe läuft im Py-3.12-venv: streamend (Socket) für Live/Loop, als One-Shot-Subprozess für Transcode/Extract. |

---

## 3. Komponenten im Detail

### 3.1 `capture/` — Source-Layer

| Datei | Rolle |
|---|---|
| `base_capture.py` | Datenmodell: `HandPose` (Alias `HandFrame`), `FingerData`, `BoneData`; `TrackingFrame`-Envelope (hands+face+gaze — Stage-2-Gerüst, noch ohne Konsument); ABC `MotionSource` (Alias `BaseCaptureDevice`). |
| `contracts.py` | Strukturelle Sicht: `MotionSourceProtocol`, `FrameConsumer` — contract-getestet. |
| `__init__.py` | Factory `create_source(kind)` mit Alias-Normalisierung (`mediapipe→webcam`, `sim→mock`), `auto`-Modus (Leap → Mock-Fallback mit USB-Diagnose). |
| `leap_capture.py` | `LeapSource`: LeapC-SDK via CFFI (`leapc_cffi/`, vendored dylibs). Live-`sample_rate` aus dem Tracking, Geräte-Präsenz-Check. |
| `mediapipe_capture.py` | `WebcamSource`: startet den Sidecar, TCP-Loopback (App = Server). Zusatz-API: Preview, `enable_face` (→ `extra_capabilities`), `record_clip`, `play_range`, `configure`, Kamera-Enumeration. |
| `mediapipe_mapping.py` | Pure-Python-Mapping Sidecar-JSON → `HandFrame`: Meter→mm, 4-Knochen-Finger, Palm-Zentroid/-Normale, Velocity mit echtem dt aus Timestamps, `eye_ref_position_mm` (IPD-Skala). |
| `mock_capture.py` | `SimulationSource` (120 Hz, 8 Szenarien) — first-class, treibt die Contract-Tests. |
| `replay_source.py` | `ReplaySource`: Landmark-JSON-Clips zeitgetreu loopen; klassifiziert nach Clip-Herkunft. |
| `source.py` | `SourceProfile` (kind, capabilities, `hand_ready`, `adapt_frame`, Prompts), Capability-Tokens, `source_kind()`/`profile_for()`. |
| `capture.yaml` + `config.py` | Sidecar-Tunables (Preview-FPS/JPEG/Konfidenzen/Record), Readiness-Schwellen; wird beim Connect **über den Socket in den Sidecar gepusht**. |

**Bewertung:** Sauberster Layer des Systems. Contract + Protocol + Factory + Profil trennen
Implementierung und Politik. Rest-Schuld: `HandFrame`/`BaseCaptureDevice`-Aliase bis zur
`TrackingFrame`-Migration; UI instanziiert an 5 Stellen konkrete Klassen statt der Factory
(bewusst: Kandidaten-/Preview-Geräte).

### 3.2 `mediapipe_sidecar/` — CV-Prozess (Python 3.12)

| Datei | Rolle |
|---|---|
| `sidecar.py` | Streaming-Prozess: HandLandmarker (VIDEO-Modus, volle Rate) + FaceLandmarker (~5 Hz eigene Kadenz). Sendet `hand`-Messages (World-Landmarks + `palm_px` + `iris_px`), gedrosselte `preview` (JPEG + Landmarks + 478 Face-Punkte), `recorded`, `done`, `error`. Kommandos: config/start(Kamera|Video|Range)/stop/preview/face/record/quit. |
| `transcode.py` | One-Shot-Normalisierer: Downscale (Quadrat-Cap), FPS-Cap, H.264, Auto-Orientierung + manuelle Rotation (VideoLab ↻). |
| `extract.py` | One-Shot-Segmentschnitt: Bereich → kompakter Clip. **Hand-aware Defacing** (Blur/Mesh spart dilatierte Hand-Konvexhüllen aus), Iris-only `.eyeref.json`, Thumbnail. |
| `PROTOCOL.md` | Message-typisiertes JSON-über-TCP-Protokoll — neue Modalitäten additiv. |

**Bewertung:** Klare Prozess-Grenze löst das cv2/Py3.14-Problem. Face ist implementiert
(Preview + Eye-Ref); ein dedizierter Full-Rate-`face`-Stream (+Blendshapes) ist der
vorbereitete nächste Ausbauschritt.

### 3.3 `video/` — Video-Service (Medien, Schnitt, Persistenz, Export)

| Datei | Rolle |
|---|---|
| `clip.py` | `VideoClip` (mp4 + `.meta.json`: Dauer/FPS/Auflösung/Herkunft/`deidentified`), `VideoLibrary`, Landmark-Clip-I/O (`save_clip` für Replay). |
| `recorder.py` | Finalisiert Sidecar-Aufnahmen → `VideoClip` (Sim-Quelle „record→loop"). |
| `importer.py` | Import mit Transcode, Copy-Fallback (`fallback_to_copy`). |
| `transcode.py` / `extractor.py` | Subprozess-Wrapper um die Sidecar-One-Shots (JSON-Ergebniszeile, Timeout, Rotate-Param). |
| `store.py` | `VideoSession`/`Segment` (JSON pro Patient): Segmente, Ergebnisse je Paradigma, `db_session_id`, `measurement_id`-Stempel. |
| `export.py` | **Brücke JSON-Store → Sternschema**: ein DB-Session pro Video (lazy), `Measurement` mit `source_kind="video"`, Doppel-Export-Schutz; Re-Analyse löscht den Stempel. |
| `video.yaml` + `config.py` | Import-/Segment-/Privacy-/Analyse-Knobs (Codec avc1, Caps 1280/1080, Deface blur, eyeref an). |

**Bewertung:** Sim-Quelle und VideoLab teilen denselben Medien-Unterbau — die Konsolidierung
trägt. Analyse läuft grundsätzlich auf dem **Original** (voller Qualität, ungeblurrt); der
Deface-Clip ist Archiv/Review und Fallback.

### 3.4 `paradigms/` — Paradigm-Layer (Motorik + Kognition)

```
registry.py ─ 9 ParadigmSpecs (Key, Label, UPDRS, Kategorie, bilateral, Screen, Klasse)
   MOTOR:     finger_tapping(3.4) hand_open_close(3.5) pronation_supination(3.6)
              postural_tremor(3.15,bilat) rest_tremor(3.17,bilat)
   COGNITIVE: tower_of_hanoi  spatial_srt  trail_making_a/_b
```

| Datei | Rolle |
|---|---|
| `base_test.py` | `BaseParadigm`: Frame-Sammlung (uni/bilateral, Lock), `_on_frame` → adapt_frame-Seam, Contract (`compute_features`, `get_live_metric(_label)`, `get_instructions`, `test_type`). |
| `runner.py` | `ParadigmRunner` — geteilter Frame-Pump (TestScreen **und** VideoLab): SETTLE-/Dauer-Gate bzw. `sidecar_bounded`, adaptiert einmal pro Frame, thread-sichere Live-Puffer (`live_snapshot`/`replace_live`). |
| `recorder.py` | Config-getriebene Auswertung: Konfidenzfilter → Trim → Resampling → Detrend/Outlier → Bandpass (Ordnung aus YAML) → Peaks (Prominenz, `max_frequency_hz`-Cap) → Feature-Methoden (Frequenz, Amplitude, CV, Dekrement, Geschwindigkeit …) → **MPI**. Bilateral: pro Hand + Asymmetrie-Indizes (FFT-Band). |
| `test_config.yaml` | Pro Paradigma: capture (Metrik, base_y, requires), analysis (Filter/Peaks/FFT), features, mpi-Gewichte. Metadaten-Duplikate zur Registry wurden entfernt. |
| Einzelparadigmen | `finger_tapping`, `hand_open_close`, `pronation_supination`, `tremor`/`rest_tremor` (base_y aus Config), `tower_of_hanoi` (+`hanoi_logic`, `pinch_detector`), `spatial_srt` (+`srt_logic`), `trail_making` (+`tmt_logic`, Teil A/B via `cls_kwargs`). |
| `config.py` | YAML-Loader, Task-Requirements → `get_unmet_capabilities`, quellabhängige Hand-Prompts. |

**Bewertung:** Registry-zentriert und config-getrieben — neue Paradigmen sind additiv
(Spec + Klasse + YAML-Block). Bekannte Namensschuld: Paket heißt `paradigms`, enthält
aber Kognition; `BaseParadigm`/`is_spatial` analog (Rename → `paradigms/` geplant).

### 3.5 `analysis/` — DSP-Kern

`signal_processing.py`: Butterworth-Bandpass, Detrend, Hann-FFT, Peak-Detection
(Prominenz/Abstand), Amplituden-Dekrement, Outlier-Entfernung, Onset/Offset-Erkennung,
Peak-to-Trough, Resampling auf uniformes Raster. Pure NumPy/SciPy, kein UI-/Storage-Wissen;
konsumiert von `recorder.py` und (für Plots) Results/Detail-Dialog.

### 3.6 `gesture_lab/` — Klinische Gesten (Parallel-Pipeline)

| Datei | Rolle |
|---|---|
| `models.py` / `gesture_db.py` | `GestureTemplate` + eigene Tabelle `GESTURE_TEMPLATE` in tappd.db (zentral in `_ensure_schema` registriert). |
| `feature_extraction.py` | 35-dim statischer / 41-dim dynamischer Pose-Vektor aus `HandFrame` (skalen-/positionsinvariant: Gelenkwinkel, normierte Distanzen). |
| `matching.py` | Statisch: Cosine-Similarity; dynamisch: DTW. |
| `battery.py` | 12-Posen-Klinik-Batterie. |
| `error_analysis.py` | Per-Finger-Fehlerklassifikation fürs Feedback. |

**Bewertung:** Bewusst eigenständige Pipeline (Erkennen statt Messen), teilt Capture-Layer
und DB. **Patientenbezug integriert:** Batterie-Läufe werden als `Measurement`
(`gesture_battery`, Kategorie `GESTURE_TEST`, eigene Konzept-Zeile) gespeichert —
inkl. Provenienz und Verlaufs-/Matrix-Anbindung; die Referenz-Bibliothek ist als
JSON exportier-/importierbar; Skelett-Projektion folgt der Quelle (Leap top-down,
Kamera frontal). Rest-Duplikate (Euler-Winkel, Config-Loader-Muster) bekannt;
dynamische Gesten (Posen 9–12) werden aufgenommen, aber noch nicht live gescort.

### 3.7 `storage/` — Klinische Persistenz

| Datei | Rolle |
|---|---|
| `database.py` | i2b2-Sternschema in SQLite (WAL, FK-Kaskaden). Fassade `Patient`/`Session`/`Measurement` entkoppelt die UI von den physischen Tabellen. Migrationen: v1→v2 (mit Backup), inkrementell `_migrate_v2` (LOOKUP_BLOB, SOURCESYSTEM_CD-Backfill). Konzept-Seeds `TAPPD:*`. MPI in `NVAL_NUM` (sortier-/filterbar). |
| `session_store.py` | Raw-Frame-JSON pro Messung (`data/sessions/`) + CSV-Export; `raw_data_path` verknüpft zurück zur Messung. |

**Bewertung:** Solide für den lokalen Einsatzzweck; DB_KONZEPT.md beschreibt ein größeres
Zielbild (PROVIDER_DIMENSION, CQL, Trigger …) — bewusst Teilmenge. Keine generische
Schema-Versionstabelle (Migrationen sind Presence-basiert).

### 3.8 `ui/` — Präsentationsschicht (PyQt6, 11 Screens im Stack)

```
PatientScreen ─► PatientDetailScreen ─┬─► TestDashboard ─► TestScreen ─► ResultsScreen
   │  (Matrix Sessions × Tests,       ├─► VideoLabScreen        ▲  (ReadinessGate 1-2-3)
   │   Klick → DetailDialog,          ├─► TrendDialog 📈        │
   │   Long-Press/Kontext)            └─► Hanoi/SRT/TMT ────────┘
   └─► GestureLabScreen (Gesten/Analyse/Detect)      TrackingScreen („Eingabequelle")
```

| Baustein | Rolle |
|---|---|
| `main_window.py` | Router (registry-getrieben), Source-Hot-Swap, Sensor-Check-Worker, Session-Verwaltung, speichert Messungen (+Provenienz). |
| `test_screen.py` | Live-Metrik-Tests: ReadinessGate → ParadigmRunner → Plot → Ergebnis. |
| `analysis_runner.py` | VideoLab-Pendant ohne Gate: `play_range` + `done`; wählt die **bewegte** Hand und attribuiert auf die gewählte Seite; Eye-Ref-Coverage. |
| `video_lab_screen.py` | Import/Timeline/Segmente/Rotate/Deface/Analyse/Export — kompletter Video-Workflow. |
| `tracking_screen.py` | Eingabequelle: Leap/Webcam/Sim wählen, Kamera-Preview, Sim-Clip aufnehmen, Eye-Ref-Overlay. |
| `results_screen.py` / `detail_dialog.py` / `trend_dialog.py` | Befund einer Messung / gespeicherte Messung mit Plots / Längsschnitt über alle Messungen. |
| `feature_meta.py` | Anzeige-Namen + Einheiten aller Features; ≈mm-Logik (`unit_label`, `SCALE_NOTE`). |
| `widgets/` | Geteilt: `WebcamPreview` (JPEG + Landmark-Overlay), `LiveMetricPlot`. |
| `pretest_gate.py`, `hand_visualization.py`, `video_timeline.py`, `test_dashboard.py`, `log_viewer.py`, `theme.py` | Gate-Overlay, 3D-Hand, Zwei-Griff-Timeline, Kachel-Dashboard mit Gating, Log-GUI, Theme (Farben/Größen — teils noch inline dupliziert). |
| `hanoi_screen.py`, `srt_screen.py`, `tmt_screen.py`, `gesture_lab_*.py` | Task-spezifische Screens (Spiel-Logik in `paradigms/*_logic.py` gehalten). |

**Bewertung:** Die frühere TestScreen↔VideoLab-Duplikation ist über Runner + geteilte
Widgets beseitigt. Schwächste Stelle: Inline-Stylesheets mit hartkodierten Farben an
mehreren Orten statt konsequent `theme.py`.

### 3.9 Querschnitt: Konfiguration, Infrastruktur, Tests

| Baustein | Rolle |
|---|---|
| `config_loader.py` | Ein Loader für alle Domänen-YAMLs: Code-Defaults ⊕ Datei (Deep-Merge) — fehlende Datei ist sicher. |
| YAML-Landschaft | `capture/capture.yaml` (Sidecar/Readiness) · `video/video.yaml` (Import/Segment/Privacy/Analyse) · `paradigms/test_config.yaml` (Klinik-Parameter) · `gesture_lab/gesture_config.yaml`. |
| `app_settings.py` | QSettings (ui_mode, capture_mode, camera_index, flip_handedness) inkl. TapPD→Motryx-Migration. |
| `logging_config.py` | Zentrales Logging → `data/logs/` + GUI-Viewer; Unhandled-Exception-Hook hält die App am Leben. |
| `main.py` | Bootstrap: Settings → Source (auto/persistiert) → Logging → MainWindow. |
| `tests/` (225) | Contract-Tests aller Paradigmen über die Sim-Quelle, DB/Migration, Mapping, Runner, SourceProfile, Replay, VideoLab-Store, DB-Export, Sidecar-Integration (echtes Video durch MediaPipe: Loop, Range+done, Extract+Deface, Transcode, Rotate). |
| `start.sh/.bat/.ps1`, `setup_sidecar.sh` | Start + Einrichtung der zwei venvs (App 3.14 / Sidecar 3.12). |

---

## 4. Status & bekannte Lücken (ehrliche Restliste)

**Trägt:** Source-Abstraktion mit dynamischem Gating · registry-getriebene Paradigmen ·
config-getriebene Auswertung + MPI · Video-Pipeline inkl. hand-aware Defacing und
DB-Export · Provenienz durchgängig (Blob + SQL) · ≈mm-Ehrlichkeit in allen Anzeigen ·
Verlaufsansicht · 225 grüne Tests inkl. echter Sidecar-Integration.

**Offen (geplant):**
1. **TrackingFrame-Migration** — Envelope-Callback (hands+face+gaze) statt `HandFrame`
   pro Hand; danach fallen die Aliase; Voraussetzung für Face-/Okular-Paradigmen.
2. **Face-/Okulomotorik-Paradigmen** (Sakkaden, Pursuit, Mimik) — Sidecar-Unterbau steht.
3. **Rename** `paradigms/` → `paradigms/` (+ `BaseParadigm`, `is_spatial`).
4. **Theme-Zentralisierung** (Inline-Farben → `theme.py`/YAML); `mock_capture`-Parameter → YAML.
5. Klinische **Validierung** der Eye-Ref-Tremor-Amplituden an realem Videomaterial
   (z-Achse prinzipbedingt nicht erfassbar; In-Plane-Messung).
6. Kleineres: `.eyeref.json` hat (nach der Live-Eye-Ref-Lösung) keinen Konsumenten;
   `CAP_FOREARM` ohne Anbieter; Hanoi-Magic-Numbers (Pinch-Schwelle, Zeitfenster) noch
   nicht in YAML; keine Schema-Versionstabelle.
