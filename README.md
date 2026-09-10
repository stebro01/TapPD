# Motryx (ehem. TapPD)

Kontaktlose Bewegungsanalyse bei Morbus Parkinson — Movement Lab mit
umschaltbaren Tracking-Quellen (Leap Motion, Webcam, Video, Simulation).

Motryx digitalisiert die motorischen Handtests der MDS-UPDRS Part III und
kognitiv-motorische Paradigmen. Handbewegungen werden kontaktlos erfasst und
quantitative Parameter automatisch berechnet; Handy-Videos lassen sich im
VideoLab schneiden, anonymisieren und auswerten.

## Features

- 5 klinische Motorik-Tests (MDS-UPDRS 3.4, 3.5, 3.6, 3.15, 3.17) + 3
  kognitiv-motorische Paradigmen (Tuerme von Hanoi, Spatial SRT, Trail Making Test)
- **Okulomotorik per Webcam**: Fixation & Blinzeln (Blinkrate, Fixationsstreuung)
  und Sakkaden-Test (5-Punkt-Eichung → gaze-contingente Ziele, Latenz,
  Richtungsfehler, Kopfpose-Waechter)
- **Vier Tracking-Quellen**, zur Laufzeit umschaltbar: Leap Motion (praeziseste
  3D-Position), Webcam (MediaPipe), Video-Replay, Simulation — mit
  Capability-Gating (Tests, die eine Quelle nicht unterstuetzt, sind gesperrt)
- **VideoLab**: Handy-Video importieren, Segmente schneiden (Onset/Offset),
  Gesicht anonymisieren (hand-aware Defacing), Paradigma auf dem Segment
  auswerten, Ergebnis in die Patientenakte exportieren
- **Tremor auf Kamera-Quellen** ueber Augen-Referenz (Iris-Skala → absolute
  Handposition); mm-Werte von Kamera-Quellen sind als Modellschaetzung (≈mm)
  gekennzeichnet
- **Gesture Lab**: klinische Handposen aufnehmen, matchen (statisch/DTW),
  Fehleranalyse pro Finger
- Echtzeit-Visualisierung, YAML-konfigurierbare Analyse-Pipeline,
  Auto-Onset/Offset-Detection, bilaterale Tremor-Analyse (+ Asymmetrie)
- Patientenverwaltung (SQLite, i2b2-Sternschema) mit Provenienz pro Messung
  (`source_kind`), **📈 Verlaufsansicht** (Merkmale ueber Zeit), Detail-Plots,
  CSV-Export, optionale JSON-Rohdaten
- Motor Performance Index (MPI) als Komposit-Verlaufsmarker
- Zentrales Logging (data/logs/) mit GUI-Log-Viewer

## Voraussetzungen

- **Windows 10/11** oder **macOS** (getestet: Windows 10 Pro, macOS 26 Tahoe)
- Python 3.12+ (Haupt-App laeuft auch unter 3.14)
- **Optional** fuer den Leap-Modus: Leap Motion Controller LM-010 +
  [Ultraleap Tracking Software](https://www.ultraleap.com/downloads/leap-controller/)
  (Hyperion v6 oder Gemini v5) — ohne Sensor funktionieren Webcam-, Video- und
  Simulationsmodus
- Fuer Webcam/VideoLab: das MediaPipe-Sidecar-venv — ein **separates Python 3.12**,
  weil MediaPipe keine Wheels fuer 3.13/3.14 liefert
  - **Windows**: `powershell mediapipe_sidecar\setup_sidecar.ps1` (`start.ps1` ruft
    es beim ersten Start selbst auf)
  - **macOS**: `bash mediapipe_sidecar/setup_sidecar.sh`

### Zusaetzlich unter Windows

- **Visual C++ Redistributable 2015–2022 (x64)** —
  `winget install --id Microsoft.VCRedist.2015+.x64` (braucht Adminrechte).
  Auf einer frischen Windows-Installation fehlt es. Ohne die Runtime bleibt die
  **Kameraauswahl leer**: die Bibliothek fuer die Kamera-Enumeration laedt nicht,
  und der Fallback (Indizes einzeln durchprobieren) laeuft in einen Timeout.
- **Kamerazugriff freigeben** unter *Einstellungen → Datenschutz → Kamera*.
  Es sind **zwei** Schalter noetig, sonst meldet OpenCV nur
  `Failed to activate media source`:
  1. „Kamerazugriff fuer dieses Gerät" (geraeteweit, **braucht Adminrechte**)
  2. „Zulassen, dass Apps auf Ihre Kamera zugreifen" (pro Benutzer)

  Der dritte Schalter („Desktop-Apps") bleibt wirkungslos, solange Nr. 2 aus ist.

## Installation

```bash
# Repository klonen
git clone <repo-url>
cd TapPD
```

### Windows (empfohlen: PowerShell)

```powershell
# Start-Script erstellt das venv, installiert Abhaengigkeiten, richtet beim
# ersten Start das MediaPipe-Sidecar ein (Download ~200 MB) und startet die App:
.\start.ps1
```

Alternativ mit `start.bat` (cmd.exe) — das ist ein duenner Wrapper um
`start.ps1` und laeuft auch, wenn die PowerShell-ExecutionPolicy noch auf dem
Windows-Standard `Restricted` steht.

Ruft man `start.ps1` direkt auf und wird es blockiert, einmalig:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

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
   - **Windows**: `start.ps1 --leap` / `start.bat --leap` holt sie aus `C:\Program Files\Ultraleap\LeapSDK\leapc_cffi\` (ohne `--leap` bleibt der Leap-Pfad aus)
   - **macOS**: `cp -r "/Applications/Ultraleap Hand Tracking.app/Contents/LeapSDK/leapc_cffi/" ./leapc_cffi/`
4. Falls die Python-Version nicht mit den SDK-Bindings uebereinstimmt (SDK liefert 3.12):
   - **Windows**: `start.ps1 --leap` / `start.bat --leap` benennt die `.pyd`-Datei automatisch um
   - **macOS**: `cp leapc_cffi/_leapc_cffi.cpython-312-darwin.so leapc_cffi/_leapc_cffi.cpython-3XX-darwin.so`
5. Leap Motion Controller per USB anschliessen (LED sollte gruen leuchten)

## Quickstart

### Windows

```powershell
# Webcam-Tracking (Standard)
.\start.ps1

# Ohne Kamera (Simulationsmodus)
.\start.ps1 --mock

# Leap Motion zusaetzlich einrichten und aktivieren
.\start.ps1 --leap
```

> **Hinweis:** Auf diesem Branch ist der Leap-Pfad standardmaessig **aus** —
> `auto` geht direkt zur Webcam. Der Leap-Code ist unveraendert; `--leap`
> (bzw. `MOTRYX_ENABLE_LEAP=1`) schaltet ihn wieder ein, ebenso die manuelle
> Auswahl „Leap" auf dem Tracking-Screen.

### macOS

```bash
# Mit Sensor (Auto-Detection)
./start.sh

# Ohne Sensor (Simulationsmodus)
./start.sh --mock
```

### Bedienung

1. **Patient anklicken** — das ist bereits der Arbeitsplatz: links alle
   Sitzungen mit ihrem Inhalt, rechts der Arbeitsbereich, der der Auswahl folgt
2. **„＋ Neue Sitzung"** legt eine Gruppe an; **„＋ Hinzufuegen"** fuellt die
   gewaehlte Sitzung: *Protokoll aufnehmen*, *Einzelnes Paradigma* oder
   *Video importieren*. Das Paradigma entscheidet selbst: motorische Aufgaben
   werden als Video-Schritt gefilmt, interaktive (Hanoi, SRT, TMT, Sakkaden)
   laufen live am Bildschirm — in der Auswahl entsprechend markiert
3. **Aufnehmen** je Schritt: Anweisung lesen → Countdown → Aufnahme →
   Take ansehen → **Uebernehmen** oder **Wiederholen**. Die Checkbox
   **„Gesicht unkenntlich machen"** steht schon vor der Aufnahme unter dem Bild
   (Vorgabe aus `video.yaml`, `privacy.deface`) und bleibt bis zum Uebernehmen
   aenderbar — pro Take, also lassen sich einzelne Aufnahmen bewusst ohne
   Defacing archivieren
4. Nach dem Uebernehmen laeuft automatisch: **Auswerten** (Ergebnis landet
   sofort in der Akte, 📋 im Baum) → **Archivieren** (kompakter Clip) →
   Roh-Take aufraeumen. Vor dem Uebernehmen entscheidet die Checkbox
   **„Gesicht unkenntlich machen"** pro Take ueber das Defacing des Archivs
   (Vorgabe aus `video.yaml`); die Analyse laeuft immer auf dem unveraenderten
   Take. **„Tracking-Overlay"** spielt einen bestaetigten Take mit den
   Landmarken ab, die die Analyse **gemessen** hat (gespeichert als
   `seg_XXX.track.json` neben dem Clip) — nicht neu berechnet, was auf einem
   verwischten Archiv auch nicht ginge. Nur fuer Takes ohne gespeicherte
   Analyse wird live nachgerechnet; die Statuszeile sagt, welcher Fall vorliegt.
   Das Overlay ist eine zweite Wiedergabe ueber den Sidecar (er dekodiert den
   Clip), kein Zeichnen ueber den Player. Es setzt an der aktuellen Stelle des
   Players ein (Statuszeile: „ab 12,3 s"), laeuft bis zum Ende und dann von
   vorn; beim Ausschalten springt der Player an die Stelle, an der das Overlay
   stand. Player und Overlay zeigen dieselbe Seite: der Player
   spiegelt eigene Takes unter der Webcam-Einstellung, importierte Videos
   unter ihrem eigenen Flag (`ui/widgets/video_view.py`).
   Ein ausgewerteter Schritt zeigt unter dem Player eine Zusammenfassung
   (Zeitpunkt, MPI, erste Kennwerte, worauf ausgewertet wurde) und den Button
   **„Details…"**: Kennwert-Tabelle und Kurven der Messung, derselbe Dialog wie
   fuer jede andere Messung. Dafuer schreibt jede Video-Auswertung die
   Rohdaten als JSON nach `data/samples/` (siehe unten), die Messung verweist
   darauf. Darunter das aufklappbare **„Aufnahme-Info"**: wann und mit welcher
   Kamera gefilmt wurde (Aufloesung, fps, Frames, Take-Nr.), ob gespiegelt und
   ob die Haendigkeit vertauscht war, ob der Archiv-Clip anonymisiert ist
   (Modus, Groesse, Codec/CRF), ob der Roh-Take noch liegt, ob eine
   Tracking-Spur existiert und welche Auswertung wann auf welcher Datei lief
   (Messung #, Rohdaten, Augenreferenz, MediaPipe-Version). Passt etwas nicht
   zusammen — Clip fehlt, Auswertung ohne Spur oder nicht in der Akte, Seite
   des Schritts ungleich Segment, Take aus einer aelteren Version ohne
   Metadaten — steht das als ⚠-Hinweis im Kopf des Panels. Dieselbe
   Herkunft zeigt der Details-Dialog einer Messung („Herkunft der Messung")
   und die Messungs-Ansicht im Baum, gespeist aus `provenance` in der Akte
5. **Rechtsklick** auf einen Schritt: **Details…**, erneut aufnehmen, **neu
   auswerten** (aktualisiert dieselbe Messung), **Paradigma/Seite aendern**
   (loescht das alte Ergebnis, wertet neu aus), entfernen. Auf eine Sitzung:
   Inhalt hinzufuegen, loeschen. **Neu auswerten** nimmt den Roh-Take, solange
   er noch da ist; ist er schon aufgeraeumt (Standard nach dem Archivieren),
   laeuft die Analyse auf dem archivierten, ggf. verwischten Clip — das Label
   sagt es. Fuer Hand-Aufgaben (Tapping, Oeffnen/Schliessen, Pro-/Supination)
   spielt das verwischte Gesicht keine Rolle, die Zahlen weichen nur durch die
   Kompression minimal ab; fuer Tremor (Absolutposition) fehlt dann die
   Augenreferenz, auch das steht im Label. Der Clip wird dabei weder erneut
   komprimiert noch erneut verwischt. **Erneut aufnehmen** bei einem bereits
   ausgewerteten Schritt fragt nach und entfernt die zugehoerige Messung aus der
   Akte — sonst bliebe eine Messung ohne Video zurueck (die Datei des alten
   Takes bleibt). **Notiz…** (auf Sitzung, Schritt, Import und Messung):
   Freitext plus angehaengte Dateien (Fotos, PDFs …) zu genau diesem Eintrag;
   ein 📝 in der Ergebnis-Spalte (📎n = Anzahl Anhaenge, Tooltip = Text)
   zeigt, wo eine Notiz liegt, die Aufnahme-Info und die Messungs-Ansicht
   fuehren sie mit auf. Notizen liegen in `NOTE_FACT`, die Dateien unter
   `data/attachments/<Patient>/<Eintrag>/`. Waehrend einer Auswertung zeigt der Arbeitsbereich
   den Take mit Hand-Overlay, einen Fortschrittsbalken und die Messkurve;
   die Dauer entspricht etwa der Clip-Laenge (MediaPipe ist auf einer
   Laptop-CPU der Engpass)
6. Ein importiertes Video erscheint als Eintrag der Sitzung, seine Segmente
   darunter wie die Schritte eines Protokolls (✂ Name, Bereich, Seite,
   Ergebnis, 📋). Angeklickt oeffnet es den Schnitt-Bereich: Bereich auf der
   Zeitleiste markieren → **„Bereich uebernehmen"** → Paradigma und Seite
   waehlen. Das Segment wird zugeschnitten (Groesse, Codec, „Gesicht
   unkenntlich machen") und — mit **„Nach Anlegen automatisch auswerten"** —
   sofort auf dem importierten Original analysiert und als Messung in die
   Akte uebernommen. Ab hier gibt es keinen Unterschied mehr zu einem
   eigenen Take: dieselbe Pipeline (`ui/segment_pipeline.py`) schreibt
   Ergebnis, Rohdaten-JSON, Tracking-Spur und Herkunft, unter dem Segment
   stehen Zusammenfassung, **„Details…"** und die Aufnahme-Info, Rechtsklick
   im Baum bietet Details, Neu auswerten, Notiz und Loeschen (nimmt die
   Messung mit). Einzige Unterschiede: Quelle der Analyse ist das Original
   mit dem Bereich des Segments (Herkunft: „importiertes Original"), die
   Spiegelung kommt vom Flag „Gespiegelt" des Videos, und aufgeraeumt wird
   nichts — das importierte Video bleibt
7. **Anamnese / klinische Daten** (Menue „＋ Hinzufuegen"): eine Maske je
   Sitzung mit Diagnose und Verlauf (Diagnosejahr, Seite, Subtyp, Hoehn &
   Yahr, MDS-UPDRS III), Familienanamnese, Stuerzen und Gang, nicht-motorischen
   Symptomen, MoCA und der Medikation (eine Zeile je Praeparat, Zustand
   ON/OFF, Minuten seit letzter Einnahme, THS). Erkrankungsdauer und
   **LEDD** (Levodopa-Aequivalenzdosis, Faktoren nach Tomlinson 2010) werden
   live berechnet. Die Maske ist in `clinical/forms/pd_anamnese.yaml`
   beschrieben und laesst sich dort erweitern (Item-Typen integer, decimal,
   scale, choice, multichoice, bool, text, date; Wiederholgruppen; berechnete
   Felder). Eine neue Maske beginnt mit den Antworten der letzten. Im Baum
   erscheint sie als 📋-Eintrag mit Kurzzeile („H&Y 2 · UPDRS III 28 · LEDD
   842 mg · OFF"); Rechtsklick: Bearbeiten, Notiz, Loeschen. Jede Antwort
   liegt als eigene kodierte Beobachtung in der Datenbank (siehe
   DB_KONZEPT.md), Messungs-Abfragen lassen diese Zeilen aus
8. Menue **„Patient ▾"**: Bearbeiten, 📈 Verlauf, CSV-Export, **📦 Export-Paket**,
   Patient loeschen. Das Export-Paket ist ein ZIP zur Uebergabe: Bericht als
   HTML, PDF und JSON (Stammdaten, Anamnese, Medikation mit LEDD, alle
   Messungen mit Kennwerten, Kurven und Herkunft, Notizen), die Archiv-Clips
   (wahlweise nur anonymisierte), Tracking-Spuren, Rohdaten-JSON, Anhaenge und
   ein Manifest mit SHA-256 je Datei (`export/bundle.py`, `verify_bundle`).
   Optional pseudonymisiert (kein Name, kein Geburtsdatum)
9. **🔬 Forschungsexport** (Startbildschirm, unten): pseudonymisierte
   Langtabellen aller Patienten als CSV — `patients`, `visits` (mit H&Y,
   UPDRS III, LEDD, ON/OFF), `measurements` (Herkunft, Qualitaetsflags),
   `features_long`, `clinical_long`, `medication`, optional `notes` und
   `signals/` (Rohdaten, Spuren) — plus automatisch erzeugtes `codebook.md`
   und `manifest.json`. Die Zuordnung Pseudonym ↔ Patient liegt nur lokal in
   `data/pseudonyms.json` (`export/research.py`, `export/pseudonyms.py`)

## Projektstruktur

Die vollstaendige Komponenten-Karte (Layer, Datenfluesse, Speicher-Topologie und
eine Analyse jeder Komponente) steht in **[BLUEPRINT.md](BLUEPRINT.md)**. Kurzfassung:

| Verzeichnis | Inhalt |
|---|---|
| `capture/` | Source-Layer: Leap / Webcam(MediaPipe) / Simulation / Replay, Factory, Capabilities |
| `mediapipe_sidecar/` | Python-3.12-Prozess fuer cv2/MediaPipe (Hand- + Face-Tracking, Transcode, Extract) |
| `video/` | Video-Service: Import, Schnitt, Defacing, Clip-Store, DB-Export (VideoLab + Sim-Quelle) |
| `paradigms/` | Paradigmen (Registry, Runner, config-getriebene Feature-Berechnung) |
| `analysis/` | Signalverarbeitung (Filter, FFT, Peaks, Onset) |
| `gesture_lab/` | Gesten-Pipeline (Posen-Templates, Matching, Fehleranalyse) |
| `storage/` | SQLite (i2b2-Sternschema) + Raw-JSON-Store |
| `ui/` | PyQt6-Screens und geteilte Widgets |
| `data/` | DB, Clips, Video-Sessions, Rohdaten, Logs (nicht im Repo) |

## Tests & Berechnete Features

| Paradigma | UPDRS | Kern-Features |
|---|---|---|
| Finger Tapping | 3.4 | Frequenz, Amplitude, Dekrement, CV, Geschwindigkeit |
| Hand Oeffnen/Schliessen | 3.5 | Amplitude, Zyklusfrequenz, Dekrement |
| Pronation/Supination | 3.6 | Rotationsfrequenz, ROM, Winkelgeschwindigkeit |
| Posturaler Tremor (bilateral) | 3.15 | Dominante Frequenz, Translations-/Rotations-RMS, Spektralleistung, Asymmetrie |
| Ruhetremor (bilateral) | 3.17 | wie 3.15, niedrigere Handposition |
| Tuerme von Hanoi | – | Zuege/Effizienz, Planungszeit, Greif-Metriken, Trajektorie, Jitter |
| Spatial SRT | – | RT/Bewegungszeit, Lernindex (Sequenz vs. Zufall), Pfad-Effizienz |
| Trail Making A/B | – | Gesamtzeit, RT, Fehler, Pfad-Effizienz, Fatigue |
| Fixation & Blinzeln | Okulo. | Blinkrate, Fixationsstreuung (%IPD), Intrusionen |
| Sakkaden-Test | Okulo. | Ziele/min, Latenz (ms), Richtungsfehler, Kopf-Waechter |

Die vollstaendigen Feature-Tabellen mit Beschreibungen, Einheiten und
Aufgaben-Details stehen in
**[TECHNICAL_DETAILS.md §6](TECHNICAL_DETAILS.md)**; der **Motor Performance
Index** (Komposit-Score 0–1, farbcodiert, Default-Merkmal der Verlaufsansicht)
ist dort in §10 beschrieben.

## Testsuite

```bash
.\.venv\Scripts\python.exe -m pytest -q                 # alles (~2 min, Sidecar-Tests inklusive)
.\.venv\Scripts\python.exe -m pytest -q -m "not sidecar" # schnell (~1 min), ohne Sidecar
```

Drei Ebenen:

- **Unit-Tests** (`tests/test_*.py`): Eingabe-Mapping, Quellen, Protokolle,
  Video-Store/-Archiv/-Export, Datenbank, Paradigmen-Logik.
- **UI-Integration** (`tests/ui/`): das echte `MotryxMainWindow` offscreen auf
  der Simulationsquelle — Patient → Sitzung → Protokoll → Take → Bestaetigen →
  Akte, Umlabeln, Kamerawechsel, Live-Messung bis zum Ergebnis, alle
  Bildschirme und Dialoge. Keine Kamera noetig.
- **Sidecar** (Marker `sidecar`): die Pipeline mit echtem MediaPipe
  (Analyse → Archiv → Aufraeumen) und `mediapipe_sidecar/tests/` unter dem
  Sidecar-Python (Haendigkeit, Backend-Wahl, Enumeration, ffmpeg-Nachlauf).
  Werden uebersprungen, wenn das Sidecar-venv fehlt.

## Ausgabeformate

### Automatische Speicherung

Jede Messung wird automatisch in der SQLite-Datenbank gespeichert (`data/tappd.db`).
Optional koennen Rohdaten als JSON in `data/samples/` gespeichert werden (Checkbox auf dem Ergebnis-Screen).
Video-Auswertungen (bestaetigte Takes, importierte Segmente ueber die Aufnahme-Pipeline)
schreiben diese JSON immer; die Messung verweist in `raw_data_path` darauf, damit der
Details-Dialog die Kurven zeichnet. Aeltere Video-Messungen verweisen nur auf den Clip —
nach **Neu auswerten** ist die JSON da.

### CSV-Export

- **Einzelmessung**: ueber "CSV Export" auf dem Ergebnis-Screen
- **Alle Messungen eines Patienten**: ueber "CSV Export" in der Patienten-Detailansicht

### JSON-Rohdaten

Enthaelt alle HandFrame-Daten fuer Offline-Analyse:
- Unilateral: `frames[]` mit Zeitstempeln, Fingerpositionen, Greifstaerke etc.
- Bilateral: `left_frames[]` + `right_frames[]` mit Palm-Positionen, Normalen etc.
- Hanoi: zusaetzlich `move_history[]` (Zuege mit Zeitstempeln) + `n_discs`
- S-SRT: zusaetzlich `trial_results[]` (pro Trial: RT, Pfad, Geschwindigkeit), `blocks[]`, `sequence`
- dTMT: zusaetzlich `segment_results[]`, `targets[]` (Layout), `wrong_approaches[]`

### SQLite-Datenbank

i2b2-Sternschema (siehe [BLUEPRINT.md §3.7](BLUEPRINT.md) und
[DB_KONZEPT.md](DB_KONZEPT.md)). Direkter Zugriff:

```bash
sqlite3 data/tappd.db \
  "SELECT CONCEPT_CD, TVAL_CHAR, NVAL_NUM, START_DATE, SOURCESYSTEM_CD
   FROM OBSERVATION_FACT ORDER BY START_DATE DESC LIMIT 20"
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

   Auf Windows kopiert `start.ps1 --leap` / `start.bat --leap` diese aus dem SDK.

5. **Nur eine App-Instanz gleichzeitig**
   LeapC erlaubt nur eine aktive Verbindung. Falls eine alte Instanz laeuft,
   diese zuerst schliessen.

### Spiegelung & Haendigkeit

Das **Live-Kamerabild wird gespiegelt** (Selfie-Ansicht): die linke Hand des
Patienten erscheint links im Bild — **und wird auch als links erkannt**. Beides
gehoert zusammen, denn MediaPipe vergibt links/rechts aus Sicht des Bildes, das
es bekommt; wird das Bild gedreht, muss das Label mitgedreht werden. Der Sidecar
erledigt das in einem Schritt, ohne Zutun.

Welche Quelle gespiegelt wird, steht gesammelt in `capture/capture.yaml`:

```yaml
sources:
  leap:    { mirror: false, swap_handedness: false }   # Sensor, kein Bild
  webcam:  { mirror: true,  swap_handedness: false }   # Live + Sim-Clip
  video:   { mirror: false, swap_handedness: false }   # Vorgabe pro Import
```

`swap_handedness` ist nur der **Zusatztausch** — die zur Spiegelung gehoerende
Label-Korrektur passiert automatisch und ist bewusst kein eigener Schalter.

Der Schalter **„Haendigkeit vertauschen"** auf dem Tracking-Screen ist etwas
anderes: er spiegelt kein Bild, sondern tauscht nur das Etikett nachtraeglich.
Er bleibt normalerweise **aus** und ist der Notnagel fuer Kameras, die selbst
schon spiegeln.

**Importierte Videos** haben ein **eigenes** Spiegel-Flag, weil nicht global
entscheidbar ist, ob eine Aufnahme seitenverkehrt ist — das haengt vom
Aufnahmegeraet ab. Im VideoLab neben der Drehen-Schaltflaeche: Checkbox
**„Gespiegelt"**, standardmaessig aus, pro Video mit der Video-Session
gespeichert. Details in [TECHNICAL_DETAILS.md](TECHNICAL_DETAILS.md) →
*Spiegelung & Haendigkeit*.

### Video-Archiv: was von einem Take bleibt

Der Recorder schreibt ~1 MB/s. Nach dem Uebernehmen durchlaeuft jeder Take
automatisch: **Auswerten** (auf dem Roh-Take, volle Qualitaet) → **Archivieren**
(derselbe Extraktor wie bei Import-Segmenten: Groessenbegrenzung, H.264 mit
x264-CRF, Defacing nach `privacy.deface`, Iris-Spur) → **Aufraeumen** (Roh-Take
loeschen, sobald der Archiv-Clip da ist). Ein 640x480-Take wird ~35x kleiner;
die Messung verweist auf den Archiv-Clip (`raw_data_path`).

```yaml
# video/video.yaml
archive:
  compact_takes: true    # Takes zu Archiv-Clips komprimieren
  keep_raw_take: false   # Roh-Take zusaetzlich behalten
segments:
  crf: 23                # x264-Qualitaet (18 nahezu verlustfrei … 28 klein; 0 = aus)
```

Der CRF-Nachlauf braucht `imageio-ffmpeg` im Sidecar-venv (`setup_sidecar`
installiert es); fehlt es, bleibt der cv2-Clip — groesser, aber vorhanden.

### Kamera-Aufloesung einstellen

Standardmaessig waehlt der Treiber das Format. Fest vorgeben in
`capture/capture.yaml`:

```yaml
sidecar:
  camera_width: 1280     # 720p
  camera_height: 720
  camera_fps: 30
```

Nicht jede Kamera kann jeden Modus: eine Anforderung, die sie nicht erfuellt,
wird stillschweigend auf den naechstliegenden Modus zurueckgesetzt. Was
tatsaechlich ausgehandelt wurde, steht in `data/logs/sidecar.log`:

```
camera 1: 1280x720 (requested 1920x1080)
```

Fuer Hand-Landmarken bringt mehr als 720p wenig, kostet aber spuerbar CPU in
MediaPipe — auf aelterer Hardware eher bei 640x480 bleiben.

### Kameraauswahl bleibt leer (Windows)

Die Enumeration braucht die **Visual C++ Runtime**; fehlt sie, laedt
`cv2_enumerate_cameras` nicht und der Fallback laeuft in einen Timeout.

```powershell
# Pruefen — leer heisst: Runtime fehlt
Get-ChildItem $env:WINDIR\System32\MSVCP140.dll, $env:WINDIR\System32\VCRUNTIME140.dll

# Installieren (Adminrechte)
winget install --id Microsoft.VCRedist.2015+.x64
```

Danach muss `.venv\Scripts\python.exe -c "import cv2_enumerate_cameras"` fehlerfrei
durchlaufen. `setup_sidecar.ps1` prueft das und warnt.

### Kamera liefert kein Bild / `Failed to activate media source`

Fast immer der Windows-Datenschutz, nicht die Kamera. Beide Schalter unter
*Einstellungen → Datenschutz → Kamera* muessen an sein (siehe
[Voraussetzungen](#zusaetzlich-unter-windows)). Zum Nachpruefen:

```powershell
$k = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
(Get-ItemProperty $k).Value                     # muss Allow sein
(Get-ItemProperty "$k\NonPackaged").Value       # muss Allow sein
```

Der geraeteweite Schalter liegt unter demselben Pfad in `HKLM:` und braucht
Adminrechte.

Sind beide auf `Allow` und es kommt trotzdem kein Bild: der Sidecar schreibt
seine Fehler nach `data/logs/sidecar.log` — dort steht, woran das Oeffnen
scheitert.

### Import-Fehler `_leapc_cffi`

Die Bindings aus dem SDK sind fuer Python 3.12 kompiliert. Bei neueren Python-Versionen
muss die Datei kopiert/umbenannt werden (C-ABI ist kompatibel):

- **Windows**: `start.ps1 --leap` / `start.bat --leap` erledigt dies automatisch
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

- **[BLUEPRINT.md](BLUEPRINT.md)** – Einstiegspunkt: Komponenten-Gesamtkarte,
  Datenfluesse, Konzepte, Status
- **[docs/manual.html](docs/manual.html)** – Nutzerhandbuch mit Screenshots
  (auch in der App: Startbildschirm → „📖 Anleitung")
- [CHANGELOG.md](CHANGELOG.md) – Versionshistorie
- [ARCHITECTURE.md](ARCHITECTURE.md) – Layer-Contracts (APIs), Naming-Schema,
  Ausbaustufen-Historie
- [TECHNICAL_DETAILS.md](TECHNICAL_DETAILS.md) – Klinische Tests + Features,
  Signalverarbeitung, MPI, Rohdaten-Format, Logging
- [DB_KONZEPT.md](DB_KONZEPT.md) – Referenz-Konzept des i2b2-Schemas (Zielbild)
- [mediapipe_sidecar/PROTOCOL.md](mediapipe_sidecar/PROTOCOL.md) – Sidecar-Protokoll
- [ABOUT.md](ABOUT.md) – Kurzinfo zum Projekt
- [OPTIMIZATION_PLAN.md](OPTIMIZATION_PLAN.md) – historischer Optimierungsplan
  (Maerz 2026; offene Analyse-Ideen)
