# Changelog — Motryx (ehem. TapPD)

Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/);
Versionierung: SemVer-artig (0.x = Forschungsprototyp).

## [Unveröffentlicht]

**Windows-Portierung der Kamera-Quellen und Klärung der Bildorientierung.**

### Hinzugefügt
- **`mediapipe_sidecar/setup_sidecar.ps1`** — Windows-Pendant zu
  `setup_sidecar.sh`: findet Python 3.12 über den `py`-Launcher, baut das venv
  und lädt beide Landmarker-Modelle. Prüft zusätzlich die Visual-C++-Runtime,
  deren Fehlen sich sonst nur als leere Kameraauswahl äußert.
- **Kamera-Aufnahmeformat konfigurierbar** (`capture.yaml` →
  `sidecar.camera_width/height/fps`, `0` = Treiberwahl). Das tatsächlich
  ausgehandelte Format wird protokolliert, da Kameras stillschweigend auf einen
  benachbarten Modus zurückfallen.
- **Spiegel-Flag pro Video** (VideoLab, Checkbox „Gespiegelt"), gespeichert an
  der `VideoSession`. Ältere `session.json` laden unverändert.
- **`sources:`-Block in `capture.yaml`** — deklariert je Eingangsquelle
  (leap/webcam/video), ob gespiegelt wird und ob die Händigkeit zusätzlich zu
  tauschen ist.
- **Sidecar-stderr** landet in `data/logs/sidecar.log` statt in `DEVNULL`.

### Geändert
- **Kameras werden unter Windows über DirectShow geöffnet**, nicht über Media
  Foundation: MSMF braucht dort Sekunden zum Öffnen (8,5 s für eine OBSBOT
  Tiny 2, 3,3 s für eine interne Webcam), DirectShow 0,2 bzw. 0,7 s.
- **Das Live-Kamerabild wird gespiegelt** (Selfie-Ansicht); die zugehörige
  Korrektur des Händigkeits-Labels leitet der Sidecar aus demselben Flag ab.
- **Aufgenommene Clips speichern das Rohbild.** Ein bereits gespiegelt
  abgelegter Clip ergab beim Abspielen ein korrektes Bild bei vertauschter
  Händigkeit.
  ⚠️ Vor dieser Änderung aufgenommene Sim-Clips einmal neu aufnehmen.
- `start.ps1` richtet das Sidecar mit ein; die Leap-Schritte laufen nur noch
  mit `--leap`. `start.bat` ist ein Wrapper darauf statt einer zweiten
  Implementierung.
- Checkbox „Links/Rechts spiegeln" heißt jetzt „Händigkeit vertauschen" — sie
  hat nie ein Bild gespiegelt, sondern nur das Etikett getauscht.

### Behoben
- **Webcam-Tracking startete unter Windows nie**: der Sidecar-Interpreter war
  fest auf `.venv/bin/python3` verdrahtet.
- **Kameraauswahl sprang auf die erste Kamera zurück** — das Neubefüllen der
  Liste setzte die Auswahl zurück, die der Aufrufer direkt danach auslas.
- **Kamera-Enumeration** listete unter Windows jedes Gerät doppelt (je
  Backend) unter Indizes, die der Capture-Pfad nicht öffnen kann.
- Ein einzelner verlorener Frame beendete den gesamten Stream.
- Fehlgeschlagene `VideoCapture`-Versuche wurden nicht freigegeben.
- Der Sim-Modus blendete ohne vorhandenen Clip die Vorschau aus, statt den
  Grund dort anzuzeigen.

### Sitzungen: Video als Primärquelle
- **Ein Bildschirm pro Sitzung** (`ui/session_screen.py`): links der Inhalt
  (Protokollschritte, importiertes Video), rechts der Arbeitsbereich, der der
  Auswahl folgt — Aufnahme, Take-Sichtung oder Video-Schnitt. Protokoll,
  einzelnes Paradigma und Video-Import sind drei Einträge im Menü
  „Hinzufügen", keine drei Bildschirme mehr.
- **Aufnahmeprotokolle** (`video/protocols/*.yaml`, Loader mit Validierung):
  jeder Schritt wird einzeln gefilmt, gesichtet und bestätigt; ein einzelnes
  Paradigma ist ein Protokoll der Länge 1. Zustand und Takes werden an der
  Video-Session persistiert, eine unterbrochene Aufnahme lässt sich fortsetzen.
- **Zuschaltbare Analyse**: ein bestätigter Take wird über denselben
  `AnalysisRunner` ausgewertet wie ein importiertes Segment — gleiche Zahlen.
- **Sitzungsübersicht als Baum** statt Test-Matrix: Schritte mit Zustand
  (offen / aufgenommen / bestätigt) und Ergebnis, Kontextmenü je Knotentyp.
- **Ein Einstieg**: „Neue Sitzung" öffnet direkt den Sitzungsbildschirm;
  die Knöpfe „VideoLab" und „Gesture Lab" auf der Patientenseite entfallen.
- **Video-Sessions pro Sitzung** (`session_<id>/`) statt pro Patient;
  bestehende Dateien werden der jüngsten Sitzung zugeordnet.
- Aufnahme-Bildschirm mit Live-Erkennungsanzeige, Kamerawechsel im Footer.

### Hinweise
- Leap Motion ist auf diesem Stand standardmäßig deaktiviert
  (`MOTRYX_ENABLE_LEAP=1` bzw. `start.ps1 --leap` aktiviert es wieder).
- Der alte Live-Weg über das Paradigmen-Dashboard bleibt für interaktive
  Paradigmen (Hanoi, SRT, TMT, Sakkaden) und ist aus dem Sitzungsbaum über
  „Live-Messung hinzufügen…" erreichbar.

## [0.3.0] — 2026-07-15

**Multimodal-Release: TrackingFrame-Envelope, Face-Stream, Okulomotorik.**

### Hinzugefügt
- **Nutzerhandbuch** (`docs/manual.html`, deutsch, mit App-Screenshots aus
  `docs/make_screenshots.py`); in der App erreichbar über „📖 Anleitung" auf
  dem Startbildschirm.
- **Okulomotorik-Kategorie (OCULAR)** mit zwei Paradigmen, beide per Webcam
  (auf Leap gesperrt via Capability `face_landmarks`):
  - **Fixation & Blinzeln** (`ocular_fixation`): Blinkrate/min (PD:
    reduziert), Fixationsstreuung (%IPD, blink-bereinigt), sakkadische
    Intrusionen, mittlere Lidspalte (EAR); eigenes Fixations-/Blink-Plotpaar
    im Ergebnis-Screen.
  - **Sakkaden-Test** (`saccade_test`): 5-Punkt-Eichung (Median-Referenz je
    Punkt, Validierung auf Ruhe/Trennbarkeit) → 30 s gaze-contingente
    Zufallsziele mit Präferenz für große Sprünge. Features: Ziele/min,
    Sakkaden-Latenz (Stimulus→Blick-Ankunft), Richtungsfehler-Rate,
    Kopfbewegungs-/Blink-Anteil. Eigener dunkler Stimulus-Screen; alle
    Schwellen in `paradigms/test_config.yaml`. Bewusst nicht ausgewiesen:
    °/s-Spitzengeschwindigkeit (bei 30-Hz-Kamera nicht messbar).
- **Sidecar-Face-Stream**: `{"cmd":"face"}` mit `rate: eco|full`; dedizierte
  `{"type":"face"}`-Messages (Iris-Zentren, Augenwinkel, EAR pro Auge,
  Nasenspitze) statt 478 Landmarks in voller Rate.
- **`TrackingFrame`-Envelope durch die ganze Pipeline**: `start_tracking(cb)`
  liefert pro Sensorframe alle Hände + `FacePose`; native Emission in allen
  vier Quellen; bilaterale Tests erhalten beide Hände aus EINEM Envelope
  (konstruktionsbedingt symmetrisch). Per-Hand-API bleibt erhalten.
- **Gesichts-Gate** für Augen-Tests: Start erst bei tatsächlich erkanntem
  Gesicht („Gesicht erkannt ✓ — Start in 3…"), Countdown setzt bei
  Gesichtsverlust zurück; Augen-Kacheln ohne Hand-Abfrage (👁-Chip).

### Geändert
- Paket **`motor_tests/` → `paradigms/`**; `BaseMotorTest` → `BaseParadigm`
  (Alias bleibt); `is_spatial` → `is_cognitive`.
- **Theme zentralisiert**: ~100 Inline-Hexfarben in `ui/` auf
  `theme.*`-Konstanten; neue semantische Konstanten (SUCCESS_BG, DANGER_BG,
  WARN_DARK, DISABLED).
- `start.sh` startet den venv-Python direkt (robust in nicht-interaktiven
  Shells).

### Behoben
- Face-Frames wurden bei „Neu aufnehmen" nicht geleert (Alt-Daten in der
  Wiederholung).

## [0.2.0] — 2026-07-03

**Telemedizin-Release: VideoLab-Vollausbau, Kamera-Tremor, Verlauf,
GestureLab-Integration.**

### Hinzugefügt
- **VideoLab → Patientenakte**: Segment-Ergebnisse als `Measurement`
  exportierbar (eigene DB-Session pro Video, Doppel-Export-Schutz).
- **Tremor auf Kamera-Quellen** über Augen-Referenz: Iris-Skala (IPD Ø 63 mm)
  macht die Handposition absolut (≈mm); dynamisches Capability-Gating,
  Coverage-Warnung <50 %.
- **Verlaufsansicht 📈**: jedes numerische Merkmal über die Zeit, pro Hand;
  Kamera-Messpunkte hohl (Modellskala).
- **Videoschnitt**: ↻ 90°-Rotation; **hand-schonendes Defacing** (Hand vor
  dem Gesicht bleibt scharf); Analyse läuft immer auf dem Original.
- **GestureLab patientenbezogen**: Batterie-Läufe als Messung
  (`gesture_battery`, Kategorie GESTURE_TEST), Bibliothek-Export/Import
  (JSON), Einstieg aus der Patienten-Detailansicht, quellen-korrekte
  Skelett-Projektion (Webcam frontal).
- **Provenienz SQL-filterbar**: `SOURCESYSTEM_CD='TAPPD:<kind>'` inkl.
  Backfill-Migration.
- **≈mm-Kennzeichnung**: mm-Werte von Kamera-Quellen in allen Anzeigen als
  Modellschätzung markiert.
- **BLUEPRINT.md** (Komponenten-Gesamtkarte) als Doku-Einstieg; Alt-Dokus
  konsolidiert; CHANGELOG/Version im „Über"-Dialog und -Button.

### Geändert
- Versionsnummer zentral (`app_settings.APP_VERSION`).
- Peak-Detection respektiert `max_frequency_hz`; `bandpass.order` aus YAML
  wirksam; Konfidenzfilter konfigurierbar.

### Behoben
- `ReplaySource` wurde als Leap klassifiziert (falsche Capabilities).
- Thread-Safety in VideoLab-Analyse (ungeschützte Live-Puffer).
- `palm_velocity` mit festem dt=1/30 s (falsche Skalierung bei ≠30 fps).
- Tremor-Livemetrik `base_y` doppelt gepflegt (jetzt aus Config).
- Diverse Config-Default-Divergenzen (Codec, Segment-Caps, Deface).
- GestureLab-Absturz durch gemischten Modul-Zustand (Alt-Prozess) —
  dokumentiert: nach Updates App neu starten.

## [0.1.0] — 2026-03

Erstversion (TapPD): 5 MDS-UPDRS-Motoriktests + 3 kognitiv-motorische
Paradigmen auf Leap Motion, i2b2-Sternschema (SQLite), MPI-Kompositscore,
Rohdaten-JSON, CSV-Export, Simulationsmodus, zentrales Logging. Später
ergänzt: Webcam-Tracking (MediaPipe-Sidecar), Source-Abstraktion,
Gesture Lab, VideoLab-Grundausbau, Rename → Motryx.
