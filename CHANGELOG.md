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
- **Ein Bildschirm pro Patient** (`ui/patient_workbench.py`): Patient
  anklicken zeigt alle Sitzungen mit Inhalt (links) und den Arbeitsbereich zum
  gewählten Element (rechts) — Aufnahme, Take-Sichtung, Video-Schnitt,
  Messungs-Details. Ersetzt die Patienten-Detailliste *und* den separaten
  Sitzungsbildschirm. Protokoll, einzelnes Paradigma, Video-Import und
  Live-Messung sind Einträge im Menü „Hinzufügen".
- **Ergebnisse landen automatisch in der Akte**: ein bestätigter, ausgewerteter
  Take wird sofort als Messung gespeichert (📋 im Baum). **Neu auswerten**
  aktualisiert dieselbe Messung (`update_measurement`), **Paradigma/Seite
  ändern** entfernt das alte Ergebnis und wertet neu aus.
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
- **Video-Archiv**: ein bestätigter Take wird nach der Auswertung über den
  Segment-Extraktor zu einem kompakten, nach `privacy.deface` anonymisierten
  Clip archiviert (`video/archive.py`); der Roh-Take wird danach entfernt
  (`archive.keep_raw_take`). Neu im Extraktor: ffmpeg-Nachlauf mit x264-CRF
  (`segments.crf`, via `imageio-ffmpeg` im Sidecar-venv) — ohne ihn blieb ein
  640×480-Clip so groß wie die Quelle, da cv2 keine Bitrate setzen kann; jetzt
  ~35× kleiner. Gilt ebenso für Import-Segmente.

### Tests
- **UI-Integrationstests** (`tests/ui/`) fahren das echte Hauptfenster
  offscreen durch den Untersucher-Weg — genau die Schicht, in der alle Fehler
  dieser Iteration saßen. Dazu Unit-Tests für Recorder/Importer,
  `session_store`, `pinch_detector`, Hanoi/SRT/TMT-Logik, und eine
  Sidecar-Suite unter dem Sidecar-Python (`mediapipe_sidecar/tests/`), die
  die Hauptsuite mit dem Marker `sidecar` startet. Dazu Rauchtests für jeden
  Bildschirm und Dialog und der Live-Messweg bis zum Ergebnis. Zeilenabdeckung
  21 % → 63 % (Domänenschichten 75–100 %, neue UI-Module 71–93 %).

### Hinweise
- Leap Motion ist auf diesem Stand standardmäßig deaktiviert
  (`MOTRYX_ENABLE_LEAP=1` bzw. `start.ps1 --leap` aktiviert es wieder).
- Der alte Live-Weg über das Paradigmen-Dashboard bleibt für interaktive
  Paradigmen (Hanoi, SRT, TMT, Sakkaden): „Einzelnes Paradigma…" startet sie
  direkt am Bildschirm statt einen Video-Schritt anzulegen; ein Protokoll mit
  einer interaktiven Aufgabe wird beim Laden abgelehnt.
- Aufnahme-Optionen: **„Gesicht unkenntlich machen"** pro Take (Vorgabe aus
  `privacy.deface`), **„Tracking-Overlay"** beim Ansehen eines bestätigten
  Takes: die Analyse legt ihre Landmarken pro Frame als `seg_XXX.track.json`
  ab, das Overlay zeichnet **diese** über den vom Sidecar nur dekodierten Clip
  (`track:false`) — was gemessen wurde, nicht eine neue Erkennung. Ohne
  gespeicherte Analyse (ältere Takes) wird live nachgerechnet, mit Hinweis.
- Vorschau-Quellen (Live-Kamera, Overlay, Analyse) sind getrennt; vorher
  sprang die Anzeige zwischen Kamerabild und abgespieltem Take hin und her.
- **Details zur Auswertung**: ein ausgewerteter Schritt zeigt eine
  Zusammenfassung (Zeitpunkt, MPI, erste Kennwerte, Quelle) und **„Details…"**
  (auch im Rechtsklick-Menü) öffnet den Messungs-Dialog mit Kennwert-Tabelle
  und Kurven. Dafür schreibt jede Video-Auswertung die Rohdaten-JSON nach
  `data/samples/` (`save_raw_data`, wie die Live-Paradigmen); die Messung
  verweist darauf statt auf den Clip. Der Dialog stürzt bei älteren
  Video-Messungen (Clip als `raw_data_path`) nicht mehr ab, sondern sagt, dass
  Kurven erst nach „Neu auswerten" da sind.
- **Overlay an der Player-Position**: das Tracking-Overlay setzt dort ein,
  wo der Abspiel-Player gerade steht (Sidecar-`start` mit `start_s`, läuft
  einmal bis zum Ende, dann Schleife von vorn); beim Ausschalten springt der
  Player an die Stelle des Overlays. `preview`-Meldungen einer Wiedergabe
  tragen dafür `t` (Sekunden im Clip).
- Die Checkbox **„Gesicht unkenntlich machen"** ist schon vor der Aufnahme
  sichtbar, nicht erst bei der Sichtung — die Wahl gilt je Take.
- **Notizen mit Anhängen** an Sitzung, Aufnahme-Schritt, Import und Messung
  (Rechtsklick → „Notiz…", `ui/note_dialog.py`): eine Notiz je Eintrag in
  `NOTE_FACT` (CATEGORY_CHAR = Art, NAME_CHAR = Bezug, NOTE_TEXT, NOTE_BLOB
  mit Anhang-Liste; neue Spalte `NOTE_BLOB` per Inline-Migration), Dateien
  kopiert nach `data/attachments/<Patient>/<Art_Bezug>/`
  (`storage/attachments.py`). Baum markiert Einträge mit 📝/📎n, Tooltip
  zeigt den Text; Aufnahme-Info und Messungs-Ansicht führen die Notiz auf.
  Löschen einer Messung oder Sitzung räumt ihre Notizen mit weg.
- **Rechts/Links im Player**: der Abspiel-Player zeigte eigene Takes so, wie
  sie gespeichert sind (roh, ungespiegelt), das Overlay aber gespiegelt.
  Neuer `VideoView` (QVideoSink) spiegelt eigene Takes unter der
  Webcam-Einstellung und importierte Videos unter ihrem Flag — Player,
  Vorschau und Overlay zeigen dieselbe Seite.
- **Metadaten je Aufnahme** (`video/meta.py`, `Segment.meta`): beim Start
  eines Takes werden Kamera (Index + Name), Spiegelung und Händigkeits-Flag,
  Gesichts-Tracking, Take-Nr. und Sidecar-Versionen notiert; das Sidecar
  meldet mit `recorded` jetzt Auflösung, fps, Frames und Codec der Datei, beim
  Verbinden ein `hello` mit MediaPipe/OpenCV/Python-Version. Das Archivieren
  ergänzt Deface-Modus, Codec/CRF, Größe, Augen-Spur; ein Import-Segment
  trägt Originaldatei, Import-Zeitpunkt, Spiegel-Flag und Ausschnitt. Die
  Auswertung hält `analysed_on`, Spiegelung und Software fest. Die Messung
  in der Akte bekommt all das als `provenance` im `OBSERVATION_BLOB`
  (`Measurement.provenance`) — sie erklärt sich ohne die Video-Session.
- **Aufklappbares Info-Panel** (`ui/widgets/meta_panel.py`) unter jedem Take
  („Aufnahme-Info") und unter jeder Messung („Herkunft der Messung", im
  Details-Dialog und in der Messungs-Ansicht des Baums) — mit
  Konsistenz-Hinweisen (`segment_issues`/`measurement_issues`): fehlende
  Clip-/Spur-/Rohdaten-Dateien, Auswertung nicht in der Akte, Seite oder
  Paradigma zwischen Schritt und Segment verschieden, Tremor auf
  anonymisiertem Clip, Takes ohne Metadaten aus älteren Ständen.
- **Erneut aufnehmen** eines ausgewerteten Schritts fragt nach und entfernt
  die zugehörige Messung; vorher blieb sie ohne Video als Waise in der Akte
  (so entstand Messung 6 im Testdatensatz).
- Alte Sessions ohne `meta` laden weiterhin; unbekannte Felder in
  `segments` werden wie bei `steps` ignoriert.
- **Neu auswerten** sagt, worauf es rechnet: Roh-Take oder — nach dem
  Aufräumen — der archivierte Clip (Gesicht unkenntlich); bei Tremor der
  Hinweis auf die fehlende Augenreferenz. `analysed_on` steht am Ergebnis.
  Der Clip wird nicht erneut komprimiert oder verwischt.
- Während einer (Neu-)Auswertung zeigt der Arbeitsbereich den Take mit
  Landmarken-Overlay, Fortschritt und großer Messkurve statt des stummen
  Abspiel-Players; Clips werden für die Analyse nicht mehr in Echtzeit
  gedrosselt (auf schnellen Rechnern kürzer, auf dieser CPU ≈ Clip-Länge).

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
