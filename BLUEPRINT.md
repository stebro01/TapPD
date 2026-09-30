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
                                   │  PatientScreen ─► PatientWorkbench (1 Screen je  │
                                   │   Proband: Baum links, Arbeitsbereich rechts)    │
                                   │   ├ RecordingPane  (Protokoll-Schritte filmen)   │
                                   │   ├ VideoLabScreen (Import schneiden)            │
                                   │   ├ FormDialog / NoteDialog / DetailDialog       │
                                   │   └ ExportDialog  ─┐ beide über SegmentPipeline  │
                                   │  TestDashboard → TestScreen / Hanoi/SRT/TMT/Sakk.│
                                   │  GestureLab │ Eingabequelle │ Results │ Verlauf  │
                                   └───────┬──────────┬─────────────┬───────────────┘
                                           │ frames   │ clips/play  │ save/load
                 SOURCE-LAYER              ▼          ▼             ▼
┌───────────────────────────────┐   ┌────────────────────┐   ┌──────────────────────┐
│        capture/  (Source)     │   │   video/ (Service)  │   │   storage/ + video/  │
│                               │   │                     │   │      (Persistenz)    │
│  MotionSource (ABC/Protocol)  │   │ record / import /   │   │ tappd.db (i2b2-Stern:│
│  ├─ LeapSource      (USB/CFFI)│◄──┤ transcode / extract │   │  Messungen+provenance│
│  ├─ WebcamSource ───┐ (Socket)│   │ archive / meta /    │   │  klinische Zeilen,   │
│  ├─ SimulationSource│         │   │ protocol / store /  │   │  Notizen) · Video-   │
│  └─ ReplaySource    │         │   │ export → DB         │   │  Session (JSON) ·    │
│                     │         │   │ clinical/ · export/ │   │  Clips · Spuren ·    │
│                     │         │   │ (Masken, Berichte)  │   │  Rohdaten · Anhänge  │
│                     │         │   │                     │   └──────────▲───────────┘
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
│  registry.py (SINGLE SOURCE OF TRUTH: 11 ParadigmSpecs)     │          │
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
Sensor ──► MotionSource ──start_tracking──► ParadigmRunner.feed(TrackingFrame)
                       (Envelope: alle Hände eines Sensorframes + FacePose)
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

### Video-Lab-Fluss (Aufnahme und Import → eine Pipeline)

```
EIGENE AUFNAHME                                   IMPORT
Protokoll (video/protocols/*.yaml)                Video-Datei ─► VideoImporter
  └► RecordingStep: Countdown → Sidecar record         └► Sidecar transcode (H.264, Caps, ↻)
     (+ Live-Kurve: ParadigmRunner am Kamera-Stream)   └► VideoSession.video_path, Flag „Gespiegelt"
  └► Take sichten → Übernehmen                     Bereich auf der Timeline → Segment
  └► confirm_step → Segment (recorded=True,          └► VideoSegmentExtractor (Schnitt, DEFACE,
     meta: Kamera, Auflösung, Spiegelung, Take)          .eyeref.json, Thumb; meta["archive"])
            │                                                   │
            └──────────────────► SegmentPipeline ◄──────────────┘   (ui/segment_pipeline.py)
                                   │  analysis_source(): Roh-Take | Archiv-Clip | Original+Bereich
                                   ▼
                     analyse: AnalysisRunner → ParadigmRunner → features
                              + Rohdaten-JSON (data/samples) + seg_XXX.track.json
                              + results[paradigma] {analysed_on, analysis, eye_ref_coverage}
                              + export_or_update → Measurement (provenance = Herkunft)
                     compact: (nur Takes) compact_take → seg_XXX.mp4 + meta["archive"]
                     cleanup: (nur Takes) Roh-Take löschen, sobald Archiv liegt
                                   │
                                   ▼
            Baum: Schritt / ✂ Segment mit Ergebnis ·📋   Panel: Zusammenfassung,
            Details…, Aufnahme-Info (video/meta.describe_segment + segment_issues)
```

Der Schritt eines Protokolls bleibt für Paradigma und Seite maßgeblich; ein
Import-Segment nutzt seine eigenen. Beide Wege enden in demselben Artefakt:
kompakter, ggf. anonymisierter Clip + Spur + Rohdaten + Messung in der Akte.

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
│     OBSERVATION_BLOB einer Messung: hand, duration_s, raw_data_path, source_kind,
│       features, provenance (Kamera/Import, Spiegel-Flags, Clip, Spur, Auswertungs-Quelle)
│     Klinische Masken: CATEGORY_CHAR='CLINICAL' — Q-Zeile (ganze Maske) + kodierte
│       Zeilen je Antwort (N/T/D), Medikamente als B mit INSTANCE_NUM, LEDD als N
│     NOTE_FACT: eine Notiz je Eintrag (CATEGORY_CHAR=Art, NAME_CHAR=Bezug, NOTE_BLOB=Anhänge)
├── video_sessions/<code>/session_<id>/session.json ─ je klinischer Sitzung: Schritte
│         │      (RecordingStep + meta), Segmente (meta, results, measurement_id-Stempel)
│         └── step_*_takeNN.mp4 (Roh-Take, bis archiviert), seg_*.mp4 (+.meta.json,
│             +.thumb.jpg, +.eyeref.json), seg_*.track.json (Landmarken je Frame)
├── samples/        ─ Rohdaten-JSON je Auswertung (Live-Paradigmen und Video)
├── attachments/<code>/<Art_Bezug>/ ─ Dateien an Notizen
├── pseudonyms.json ─ Patientencode → P-0001 (nur lokal; Forschungsexport)
├── clips/          ─ Sim-Quelle (default.mp4 + Landmark-JSON-Clips für Replay)
├── sessions/       ─ Raw-Frame-JSON älterer Live-Messungen (session_store)
└── logs/           ─ zentrales Logging (logging_config, GUI-LogViewer, sidecar.log)
```

---

## 2. Kernkonzepte

| Konzept | Bedeutung |
|---|---|
| **Source** | Austauschbare Datenquelle (`MotionSource`-Contract: connect/disconnect/is_connected/start_recording(cb)/**start_tracking(cb)**/stop/sample_rate). `start_tracking` liefert `TrackingFrame`-Envelopes (alle Hände eines Sensorframes + `FacePose`). Hot-swap zur Laufzeit. |
| **Paradigm** | Klinische/kognitive Aufgabe (`BaseParadigm`), konsumiert Frames, liefert `compute_features()`. Einmalig deklariert in der **Registry** — Dashboard, Routing, Storage-Kategorie und Gating leiten sich daraus ab. |
| **Capability-Gating** | Sources deklarieren Fähigkeiten (`fingertips`, `finger_flexion`, `hand_pose`, `abs_position`), Paradigmen ihren Bedarf (`test_config.yaml → requires`). UI sperrt Unerfülltes (🔒). **Dynamisch:** Webcam meldet `abs_position` nur bei aktivem Face-Tracking (`extra_capabilities`). |
| **adapt_frame-Seam** | `SourceProfile.adapt_frame` ist DIE Stelle für Quell-Normalisierung. Aktiv: Webcam ersetzt `palm_position` durch die Augen-referenzierte Absolutposition (Kopie, idempotent). |
| **Eye-Referenz** | Iris-Zentren (IPD Ø 63 mm) liefern mm-pro-Pixel + Ursprung → absolute Handposition aus RGB. Schaltet Tremor auf Kamera-Quellen frei; Coverage wird gespeichert, <50 % ⇒ Warnung. |
| **≈mm-Skala** | MediaPipe-World-Landmarks sind Modellschätzungen (unkalibriert). Frequenzen/Zeiten/Winkel exakt; mm-Werte bei `source_kind ∈ {webcam, video}` als „≈mm" gekennzeichnet (`ui/feature_meta.py`), im Verlauf hohle Punkte. |
| **Provenienz** | Jede Messung trägt `source_kind` (leap/webcam/mock/video) — im Blob UND SQL-filterbar als `SOURCESYSTEM_CD='TAPPD:<kind>'`. |
| **MPI** | Motor Performance Index: gewichteter Komposit-Score (0–1) aus normierten Features (`test_config.yaml → mpi`), gespeichert in `NVAL_NUM`, Ampel-Farben in der UI, Default-Merkmal im Verlauf. |
| **Sidecar-Trennung** | Haupt-App = Python 3.12+ **ohne cv2**; alles cv2/MediaPipe läuft im Py-3.12-venv: streamend (Socket) für Live/Loop, als One-Shot-Subprozess für Transcode/Extract. |

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

**Bewertung:** Klare Prozess-Grenze löst das cv2/Python-Versions-Problem. Face ist implementiert
(Preview + Eye-Ref); ein dedizierter Full-Rate-`face`-Stream (+Blendshapes) ist der
vorbereitete nächste Ausbauschritt.

### 3.3 `video/` — Video-Service (Medien, Schnitt, Persistenz, Export)

| Datei | Rolle |
|---|---|
| `clip.py` | `VideoClip` (mp4 + `.meta.json`: Dauer/FPS/Auflösung/Herkunft/`deidentified`), `VideoLibrary`, Landmark-Clip-I/O (`save_clip` für Replay). |
| `recorder.py` | Finalisiert Sidecar-Aufnahmen → `VideoClip` (Sim-Quelle „record→loop"). |
| `importer.py` | Import mit Transcode, Copy-Fallback (`fallback_to_copy`). |
| `transcode.py` / `extractor.py` | Subprozess-Wrapper um die Sidecar-One-Shots (JSON-Ergebniszeile, Timeout, Rotate-Param). |
| `store.py` | `VideoSession` (JSON je klinischer Sitzung): `RecordingStep` (Protokoll-Schritte mit Zustand, Takes, `meta`), `Segment` (Herkunft `meta`, `recorded`, `source_path`, `clip_path`, `track_path`, `results` je Paradigma mit `measurement_id`-Stempel), `db_session_id`; `load_for_session` adoptiert Altbestände. |
| `protocol.py` + `protocols/*.yaml` | Aufnahmeprotokolle: Schema, Loader mit Validierung (`Issue`), `protocol_for_paradigm` (Einzelparadigma = Protokoll der Länge 1); interaktive Paradigmen werden abgewiesen (laufen live). |
| `archive.py` | `compact_take` (Roh-Take → kompakter, ggf. anonymisierter Clip über den Extraktor), `discard_raw_take` (nur wenn Archiv als eigene Datei liegt). |
| `meta.py` | **Herkunft**: `capture_meta`/`note_recorded`/`import_meta`/`note_archive`/`analysis_meta` schreiben, `build_provenance` für die Messung, `describe_segment`/`describe_measurement` + `segment_issues`/`measurement_issues` für Panel und Konsistenzprüfung, `result_summary`. |
| `export.py` | **Brücke JSON-Store → Sternschema**: `export_or_update` (Re-Analyse aktualisiert dieselbe Messung), `Measurement` mit `source_kind="video"`, `raw_data_path` = Rohdaten-JSON, `provenance` aus `meta.build_provenance`. |
| `video.yaml` + `config.py` | Import-/Segment-/Privacy-/Archiv-/Analyse-Knobs (Codec avc1, Caps, Deface blur, eyeref an, `archive.compact_takes`, `keep_raw_take`). |

**Bewertung:** Sim-Quelle, eigene Aufnahme und Import teilen denselben Medien-Unterbau.
Analyse läuft auf dem **Original** (Roh-Take bzw. importierte Datei, ungeblurrt); der
Deface-Clip ist Archiv/Review und Fallback (`analysed_on` sagt es). Die Ablaufsteuerung
liegt bewusst nicht hier, sondern in `ui/segment_pipeline.py` (Qt-Threads, Queue).

### 3.3b `clinical/` — Klinische Daten per YAML-Maske

| Datei | Rolle |
|---|---|
| `forms/pd_anamnese.yaml` | Parkinson-Anamnese: Abschnitte, Items (integer/decimal/scale/choice/multichoice/bool/text/date), Kataloge (Wirkstoffe mit LEDD-Faktoren), Wiederholgruppe Medikation, berechnete Felder (Erkrankungsdauer, LEDD). |
| `schema.py` | Loader mit Validierung (`FormError`), `validate` (Bereiche, Auswahlen, Typen), `compute` (`years_since`, `sum`, `ledd`), `summary_line`, `describe`. |
| `store.py` | Eintrag = Q-Zeile (ganze Maske) + kodierte Zeilen je Antwort in `OBSERVATION_FACT` (`CATEGORY_CHAR='CLINICAL'`), Konzepte in `CONCEPT_DIMENSION`, `prefill` (Fortschreiben), `delete_form_entry`. |

### 3.3c `export/` — Berichte und Forschungsexport

| Datei | Rolle |
|---|---|
| `record.py` | Ein Serializer: die Akte eines Probanden als Dict (Patient, Sitzungen mit Masken/Messungen/Notizen), optional pseudonymisiert. |
| `research.py` | Pseudonymisierte Langtabellen (patients, visits, measurements, features_long, clinical_long, medication, optional notes/signals) + `codebook.md` + `manifest.json`. |
| `report.py` / `curves.py` | HTML-Bericht (Übersicht, Anamnese, Messungen mit Kennwerten und Kurvenbild), PDF über Qt; Kurven aus der Rohdaten-JSON (Matplotlib Agg). |
| `bundle.py` | ZIP-Paket: report.html/.pdf/.json, Videos (wahlweise nur anonymisiert), Spuren, Rohdaten, Anhänge, Manifest mit SHA-256, `verify_bundle`. |
| `pseudonyms.py` | Stabile Pseudonyme `P-0001`, Zuordnung nur lokal (`data/pseudonyms.json`). |

### 3.4 `paradigms/` — Paradigm-Layer (Motorik + Kognition + Okulomotorik)

```
registry.py ─ 11 ParadigmSpecs (Key, Label, UPDRS, Kategorie, bilateral, Screen, Klasse)
   MOTOR:     finger_tapping(3.4) hand_open_close(3.5) pronation_supination(3.6)
              postural_tremor(3.15,bilat) rest_tremor(3.17,bilat)
   COGNITIVE: tower_of_hanoi  spatial_srt  trail_making_a/_b
   OCULAR:    ocular_fixation (Blinkrate/Fixationsstreuung)
              saccade_test (5-Punkt-Eichung → gaze-contingente Ziele)
```

| Datei | Rolle |
|---|---|
| `base_test.py` | `BaseParadigm`: Frame-Sammlung (uni/bilateral, Lock), `_on_frame` → adapt_frame-Seam, Contract (`compute_features`, `get_live_metric(_label)`, `get_instructions`, `test_type`). |
| `runner.py` | `ParadigmRunner` — geteilter Frame-Pump (TestScreen **und** VideoLab): SETTLE-/Dauer-Gate bzw. `sidecar_bounded`, adaptiert einmal pro Frame, thread-sichere Live-Puffer (`live_snapshot`/`replace_live`). |
| `recorder.py` | Config-getriebene Auswertung: Konfidenzfilter → Trim → Resampling → Detrend/Outlier → Bandpass (Ordnung aus YAML) → Peaks (Prominenz, `max_frequency_hz`-Cap) → Feature-Methoden (Frequenz, Amplitude, CV, Dekrement, Geschwindigkeit …) → **MPI**. Bilateral: pro Hand + Asymmetrie-Indizes (FFT-Band). |
| `test_config.yaml` | Pro Paradigma: capture (Metrik, base_y, requires), analysis (Filter/Peaks/FFT), features, mpi-Gewichte. Metadaten-Duplikate zur Registry wurden entfernt. |
| Einzelparadigmen | `finger_tapping`, `hand_open_close`, `pronation_supination`, `tremor`/`rest_tremor` (base_y aus Config), `tower_of_hanoi` (+`hanoi_logic`, `pinch_detector`), `spatial_srt` (+`srt_logic`), `trail_making` (+`tmt_logic`, Teil A/B via `cls_kwargs`), `ocular_fixation` (FacePose-Konsument), `saccade_test` (+`saccade_logic`: Eichung/Klassifikator/Statemachine, headless). |
| `config.py` | YAML-Loader, Task-Requirements → `get_unmet_capabilities`, quellabhängige Hand-Prompts. |

**Bewertung:** Registry-zentriert und config-getrieben — neue Paradigmen sind additiv
(Spec + Klasse + YAML-Block), bewiesen durch die OCULAR-Kategorie: zwei Augen-
Paradigmen ohne Anfassen bestehender Tests. Historische Namensschuld
(`motor_tests`, `BaseMotorTest`, `is_spatial`) ist bereinigt.

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
| `session_store.py` | Raw-Frame-JSON älterer Live-Messungen (`data/sessions/`) + CSV-Export; heute schreiben Live- wie Video-Auswertungen `data/samples/` (`ui/results_screen.save_raw_data`). |
| `database.py` (Notizen) | `Note` + `save_note`/`get_notes`/`delete_note` auf `NOTE_FACT` (eine Notiz je Eintrag: Sitzung, Schritt, Import, Segment, Messung, Maske; `NOTE_BLOB` = Anhänge). Löschen einer Messung/Sitzung nimmt Notizen mit. |
| `attachments.py` | Dateien an Notizen unter `data/attachments/<code>/<Art_Bezug>/` (Kopie, Dedupe, Entfernen). |
| `Measurement.provenance` | Herkunft einer Video-Messung im `OBSERVATION_BLOB` (Kamera/Import, Spiegel-Flags, Clip, Spur, Auswertungs-Quelle); Messungs-Abfragen lassen `CATEGORY_CHAR='CLINICAL'` aus. |

**Bewertung:** Solide für den lokalen Einsatzzweck; DB_KONZEPT.md beschreibt ein größeres
Zielbild (PROVIDER_DIMENSION, CQL, Trigger …) — bewusst Teilmenge. Keine generische
Schema-Versionstabelle (Migrationen sind Presence-basiert).

### 3.8 `ui/` — Präsentationsschicht (PyQt6)

```
PatientScreen ─► PatientWorkbench (ein Screen je Proband)
   │   ┌ Baum: Sitzungen → Schritte ○◐✔ / 🎬 Import → ✂ Segmente / 📋 Anamnese / Messungen
   │   │        Ergebnis-Spalte: MPI, 📋 in der Akte, 📝📎 Notiz; Rechtsklick je Knotentyp
   │   └ Arbeitsbereich (QStackedWidget):
   │        RecordingPane  ─ Aufnahme-Zyklus, Sichtung, Overlay, Zusammenfassung, Aufnahme-Info
   │        VideoLabScreen ─ Import, Timeline, Segment schneiden, Auswertung, Aufnahme-Info
   │        Messungs-Ansicht ─ Herkunft der Messung → DetailDialog
   │        Masken-Ansicht  ─ Antworten (→ FormDialog)
   │        beide Panes teilen SegmentPipeline; Footer: Kamera, Gesichtserkennung, Status
   ├─► TestDashboard ─► TestScreen ─► ResultsScreen (Live-Paradigmen, ReadinessGate)
   ├─► Hanoi / SRT / TMT / Sakkaden (interaktiv, aus „Einzelnes Paradigma")
   ├─► TrendDialog 📈 · ExportDialog 📦 · GestureLabScreen · TrackingScreen (Eingabequelle)
   └─► Startbildschirm: 🔬 Forschungsexport, 📖 Anleitung, Über
```

| Baustein | Rolle |
|---|---|
| `main_window.py` | Router (registry-getrieben), Source-Hot-Swap, Sensor-Check-Worker, speichert Live-Messungen (+Provenienz). |
| `patient_workbench.py` | Der Arbeitsplatz: Baum, Kontextmenüs, Kamera-Footer, Notizen, Masken, Exporte; erzeugt die geteilte `SegmentPipeline`. |
| `recording_pane.py` | Protokoll-Schritt filmen: Countdown → Sidecar `record` (+ Live-Kurve) → Sichtung (`VideoView`, Overlay aus gespeicherter Spur) → Übernehmen → Pipeline; Zusammenfassung, Details, `MetaPanel`. |
| `segment_pipeline.py` | **Eine** Warteschlange analysieren → archivieren → aufräumen für Takes und Import-Segmente (`analysis_source`); schreibt Rohdaten, Spur, Analyse-Meta, exportiert in die Akte. |
| `video_lab_screen.py` | Schnitt-Bereich (eingebettet): Import/Transcode/Rotate, Timeline, Segment mit Paradigma/Seite, Defacing, automatische Auswertung über die Pipeline, Zusammenfassung/Details/`MetaPanel`. |
| `analysis_runner.py` | Sidecar-Wiedergabe ohne Gate: `play_range` + `done`; wählt die **bewegte** Hand; sammelt Spur (`track_data`) und Eye-Ref-Coverage. |
| `protocol_chooser.py` / `form_dialog.py` / `note_dialog.py` / `export_dialog.py` | Protokoll oder Einzelparadigma wählen · generische YAML-Maske · Notiz mit Anhängen · Export-Paket / Forschungsexport. |
| `test_screen.py`, `results_screen.py`, `detail_dialog.py`, `trend_dialog.py` | Live-Metrik-Tests, Befund, gespeicherte Messung mit Plots + Herkunft, Längsschnitt. |
| `tracking_screen.py` | Eingabequelle: Leap/Webcam/Sim, Kamera-Preview, Sim-Clip aufnehmen. |
| `feature_meta.py` | Anzeige-Namen + Einheiten aller Features; ≈mm-Logik. |
| `widgets/` | `WebcamPreview` (JPEG + Landmarken + Iris), `LiveMetricPlot`, `VideoView` (Player-Frames, spiegelbar), `MetaPanel` (aufklappbare Zeilen + ⚠-Hinweise). |
| `theme.py` | Farben/Größen/Profile (dense/touch), Stylesheet inkl. Menü-Buttons. |
| `pretest_gate.py`, `hand_visualization.py`, `video_timeline.py`, `test_dashboard.py`, `log_viewer.py`, `hanoi_/srt_/tmt_/saccade_screen.py`, `gesture_lab_*.py` | Gate-Overlay, 3D-Hand, Zwei-Griff-Timeline, Kachel-Dashboard, Log-GUI, Task-Screens. |

**Bewertung:** Aufnahme und Import laufen seit 09/2026 über dieselbe Pipeline und
dieselben Panels (Zusammenfassung, Details, Aufnahme-Info); die Pipeline-Logik ist aus
den Panes herausgelöst und testbar. Schwächste Stelle bleibt der Schnitt-Bereich
(dichter Aufbau, Standalone-Reste wie `close_video_lab`).

### 3.9 Querschnitt: Konfiguration, Infrastruktur, Tests

| Baustein | Rolle |
|---|---|
| `config_loader.py` | Ein Loader für alle Domänen-YAMLs: Code-Defaults ⊕ Datei (Deep-Merge) — fehlende Datei ist sicher. |
| YAML-Landschaft | `capture/capture.yaml` (Sidecar/Readiness) · `video/video.yaml` (Import/Segment/Privacy/Analyse) · `paradigms/test_config.yaml` (Klinik-Parameter) · `gesture_lab/gesture_config.yaml`. |
| `app_settings.py` | QSettings (ui_mode, capture_mode, camera_index, flip_handedness) inkl. TapPD→Motryx-Migration. |
| `logging_config.py` | Zentrales Logging → `data/logs/` + GUI-Viewer; Unhandled-Exception-Hook hält die App am Leben. |
| `main.py` | Bootstrap: Settings → Source (auto/persistiert) → Logging → MainWindow. |
| `tests/` (435) | Contract-Tests aller Paradigmen über die Sim-Quelle, DB/Migration/Blob-Vertrag, Mapping, Runner, SourceProfile, Replay, Video-Store/Protokolle/Archiv/Meta, Pipeline (Takes + Import), Masken (Schema/Store/Dialog), Notizen, Exporte, Offscreen-UI-Flüsse (Arbeitsplatz, Aufnahme-Panel, Dialoge) und Sidecar-Integration (`-m sidecar`: echte Pipeline auf Take und Import). |
| `start.sh` / `start.ps1` (+ `start.bat`-Wrapper), `setup_sidecar.sh` / `setup_sidecar.ps1` | Start + Einrichtung der zwei venvs (App 3.12+ / Sidecar 3.12). Windows und macOS haben je ein eigenes Skriptpaar. |

---

## 4. Status & bekannte Lücken (ehrliche Restliste)

**Trägt:** Source-Abstraktion mit dynamischem Gating · registry-getriebene Paradigmen
(11, inkl. zwei OCULAR) · **TrackingFrame-Envelope** durch die ganze Pipeline
(alle Hände eines Sensorframes + FacePose; bilateral konstruktionsbedingt
symmetrisch) · Face-Stream im Sidecar (eco/full, Iris + Augenwinkel + EAR +
Nase) · config-getriebene Auswertung + MPI · Video-Pipeline inkl. hand-aware
Defacing und DB-Export · Provenienz durchgängig (Blob + SQL, `provenance` je
Video-Messung) · ≈mm-Ehrlichkeit in allen Anzeigen · Verlaufsansicht · Theme
zentralisiert · **Video-Lab 09/2026**: ein Arbeitsplatz je Proband, YAML-Protokolle,
eine Pipeline für Takes und Import, Overlay aus gespeicherter Spur, Metadaten +
Konsistenzprüfung, Notizen mit Anhängen, YAML-Anamnese mit LEDD, Export-Paket und
Forschungsexport · 435 grüne Tests inkl. echter Sidecar-Integration.

**Offen (geplant):**
1. Klinische **Validierung an realem Material**: Eye-Ref-Tremor-Amplituden
   (z-Achse prinzipbedingt nicht erfassbar; In-Plane-Messung), Schwellen der
   Augen-Tests (`saccade_test`-Block: dwell/confidence_margin/Kopf-Toleranzen),
   Referenzmessungen Leap vs. Webcam.
2. **Mimik-Batterie** über Blendshapes (Hypomimie) und **Smooth Pursuit**;
   kalibrierte `GazePose` — Sidecar-Protokoll ist vorbereitet (additiv).
3. **GestureLab-Reste**: dynamische Gesten (Posen 9–12) live scoren
   (DTW existiert, wird nicht aufgerufen); Gesten direkt auf VideoLab-
   Segmenten; ANALYSE-Raster entzerren.
4. Optionale **Handlängen-Kalibrierung** für echte mm auf Kamera-Quellen.
5. Analyse-Ideen aus OPTIMIZATION_PLAN Phase 2–3 (Welch-PSD, Hesitation-/
   Freezing-Erkennung, Qualitätsmetriken) — zusammen mit 1. validieren.
6. Aus KLINIK_KONZEPT offen: Studien-Kohorten, `analysis_version` mit
   Batch-Neuauswertung, pandas-Lademodul, FHIR-Composition.
7. Kleineres: `.eyeref.json` ohne Konsumenten (Archiv-Fallback);
   `CAP_FOREARM` ohne Anbieter; Hanoi-Magic-Numbers noch nicht in YAML;
   keine Schema-Versionstabelle; `HandFrame`/`BaseCaptureDevice`-Aliase
   bleiben als bequeme per-Hand-API.
