# Session-Konzept — Erfassungsarten, Aufnahmeprotokoll, Session-Baum

> Entwurf, noch nicht implementiert. Ergänzt [BLUEPRINT.md](BLUEPRINT.md)
> (Komponentenkarte) und [DB_KONZEPT.md](DB_KONZEPT.md) (Datenmodell).

## 1. Ausgangslage

Heute gibt es **zwei parallele Session-Begriffe**, die nur lose verbunden sind:

| | Wo | Schlüssel | Inhalt |
|---|---|---|---|
| Klinische Session | SQLite `VISIT_DIMENSION` | pro **Besuch** | Measurements |
| `VideoSession` | JSON, `data/video_sessions/` | pro **Patient** | Video + Segmente |

Die Verbindung entsteht erst nachträglich über `db_session_id` beim Export in
die Akte.

Daraus folgen zwei Probleme:

1. **Die Video-Session hängt am Patienten, nicht am Besuch.** Ein zweiter
   Termin arbeitet auf derselben Video-Session weiter — es gibt keine
   Möglichkeit, „das Video vom Januar" und „das Video vom März" nebeneinander
   zu halten.
2. **Der Einstieg kennt nur einen Weg.** „Neue Session" springt direkt ins
   Paradigmen-Dashboard. Video ist ein Nebeneingang, kein gleichwertiger
   Erfassungsweg.

## 2. Die Session als einziger Container

Die klinische Session bleibt der einzige Behälter. Die Erfassungsart wird ihr
**Attribut**, nicht ein eigener Behälter daneben:

```
Session (Besuch)
├── acquisition_mode: live | video_recorded | video_imported
├── Measurements          ← aus Live-Paradigmen ODER aus Video-Segmenten
└── VideoSession          ← optional, künftig pro Session statt pro Patient
```

**Umsetzung ohne Schema-Migration:** `VISIT_DIMENSION` hat bereits ein
`VISIT_BLOB` mit JSON (heute `{"notes": ...}`). Dort kommt
`acquisition_mode` hinein. Die `VideoSession` wird über die Session-ID
verschlüsselt statt über den Patientencode; bestehende Dateien lassen sich
beim ersten Öffnen der jeweils jüngsten Session zuordnen.

### Der Modus beschreibt, er sperrt nicht

`acquisition_mode` sagt, wie eine Session **primär** entstanden ist — für
Anzeige, Gruppierung und Filterung. Er verbietet nichts: wer eine
Video-Session angelegt hat und danach zusätzlich live misst, soll das können.
Ein Modus als harte Einschränkung würde nur Sackgassen erzeugen.

### Einstieg

```
[+ Neue Session]
   ├─ Live-Paradigmen          → Dashboard          (heutiger Weg, unverändert)
   ├─ Video aufnehmen          → Protokoll  ODER  einzelnes Paradigma   (neu)
   └─ Video importieren        → VideoLab, Import   (existiert)
```

Die zweite Zeile ist der eigentliche Hebel: **ein einzelnes Paradigma ist ein
Protokoll mit genau einem Schritt.** Beide Wege erzeugen dieselbe Struktur —
eine Liste abzuarbeitender Schritte — und brauchen deshalb keine getrennte
Mechanik, nur eine unterschiedliche Herkunft der Liste:

```
Protokoll gewählt      →  Schritte aus  video/protocols/<id>.yaml
Einzelnes Paradigma    →  Schritt ad hoc gebaut (Paradigma + Hand + Dauer)
                                   ↓
                      identische Aufnahme-, Sichtungs- und Analysestrecke
```

Das nimmt dem Aufnahmeweg die Schwere: wer nur schnell ein Finger-Tapping auf
Video braucht, muss dafür kein Protokoll anlegen.

## 3. Protokollgeführte Aufnahme

### Kernidee: ein Protokollschritt **ist** ein Segment

Das vorhandene Modell beschreibt einen Protokollschritt bereits vollständig:

```python
Segment(name, start_s, end_s, paradigm, hand, clip_path, note, ...)
```

Der Unterschied zwischen den beiden VideoLab-Eingängen ist deshalb nur, **wer
die Segmente erzeugt**:

```
Import     →  Untersucher schneidet Segmente von Hand
Aufnahme   →  Protokoll erzeugt sie Schritt für Schritt
                          ↓
       ab hier identisch: Defacing → Analyse → Export in die Akte
```

Ein neuer Eingang, **kein neuer Auswertungspfad**.

### Protokoll-YAML

Ein Protokoll je Datei unter `video/protocols/<id>.yaml`:

```yaml
id: updrs_hand_basis
name: "MDS-UPDRS Hand – Basisprotokoll"
description: "Ruhe, Kopfdrehung, Finger-Tapping beidseits."
defaults:
  duration_s: 20
  countdown_s: 3
steps:
  - id: rest
    title: "Ruheaufnahme"
    instruction: "Entspannt sitzen, Hände auf den Oberschenkeln, ruhig atmen."
    duration_s: 30
    paradigm: rest_tremor
    hand: both

  - id: head_turn
    title: "Kopfdrehung"
    instruction: "Kopf langsam nach rechts, dann nach links drehen."
    paradigm: ""                    # reiner Dokumentationsschritt
                                    # (aufnehmen und archivieren, nicht auswerten)

  - id: tap_right
    title: "Finger-Tapping rechts"
    instruction: "Daumen und Zeigefinger schnell öffnen und schließen."
    paradigm: finger_tapping
    hand: right

  - id: tap_left
    title: "Finger-Tapping links"
    instruction: "Daumen und Zeigefinger schnell öffnen und schließen."
    paradigm: finger_tapping
    hand: left
```

Zwei bewusste Festlegungen:

- **`paradigm: ""` = Dokumentationsschritt.** Die Kopfdrehung hat kein
  Paradigma, soll aber aufgenommen und archiviert werden. Ohne dieses Konzept
  müsste man sie künstlich einem Test zuordnen.
- **Validierung beim Laden.** Jeder `paradigm`-Key muss in der Registry
  existieren *und* seine `requires:`-Capabilities müssen von einer Kamera
  erfüllbar sein. Sonst verspricht ein Protokoll eine Auswertung, die das
  Capability-Gating später sperrt — und das fällt erst am Patienten auf.

**Ein Protokoll pro Session.** Es wird beim Anlegen gewählt und ist danach die
Struktur dieser Session.

### Einzelnes Paradigma = Protokoll der Länge 1

Wählt der Untersucher statt eines Protokolls ein einzelnes Paradigma, wird
daraus im Speicher ein einschrittiges Protokoll gebaut:

```python
Step(id=paradigm_key, title=spec.label, instruction=spec.instruction,
     duration_s=gewählte_dauer, paradigm=paradigm_key, hand=gewählte_hand)
```

Kein Sonderfall im weiteren Ablauf — Aufnahme, Sichtung, Wiederholung und
Analyse laufen über denselben Code. Auch nachträgliches Ergänzen („noch ein
Tapping links") ist damit nur ein weiterer Schritt in derselben Session,
statt eines zweiten Mechanismus.

### Ablauf: ein Clip je Schritt

Jeder Schritt wird in eine **eigene Datei** aufgenommen, nicht als Zeitbereich
eines durchgehenden Videos. Begründung:

- Das Sidecar-Kommando `{"cmd":"record","path":…,"seconds":N}` nimmt bereits
  genau **einen Clip fester Dauer** auf — die Primitive passt exakt.
- `Segment.clip_path` existiert bereits, ebenso der Analyse-Fallback auf diesen
  Pfad, wenn kein Elternvideo vorhanden ist.
- **Wiederholen = diesen einen Clip neu aufnehmen.** Kein Nachschneiden, keine
  toten Zwischenzeiten, kein Verschnitt misslungener Takes im Archiv.
- Plattenbedarf proportional zum tatsächlichen Inhalt.

Preis: der VideoLab-Player geht heute von *einem* Elternvideo aus
(`session.video_path`). Für aufgenommene Sessions bleibt das leer, und die
Segmentliste wird zur primären Ansicht. Hier liegt der eigentliche UI-Aufwand.

### Zustände eines Schritts

Der Untersucher bestätigt **jeden** Schritt; nichts läuft automatisch durch:

```
offen ──► Countdown ──► Aufnahme ──► Sichtung ──┬──► bestätigt
  ▲                                             │
  └──────────── erneut aufnehmen ◄──────────────┘
```

Ausgewertet wird **erst nach Sichtung**, und zwar ausdrücklich angestoßen —
nicht automatisch beim Abschluss der Aufnahme. Ein bestätigtes Segment ist
damit „aufgenommen und für gut befunden", die Analyse ein getrennter zweiter
Schritt. So bleibt der Untersucher an der Stelle, an der Bewegungsartefakte
auffallen, in der Verantwortung.

### Falle: Spiegelung

Ein protokollaufgenommenes Video ist eine **Eigenaufnahme** und liegt damit
**roh** auf Platte (siehe TECHNICAL_DETAILS → *Spiegelung & Haendigkeit*). Es
muss folglich mit der **Webcam**-Einstellung (`sources.webcam.mirror`)
abgespielt werden, nicht mit der Video-Vorgabe. `VideoSession.mirrored` ist
beim Anlegen einer Aufnahme-Session entsprechend zu setzen.

Das ist genau der Fehler, der beim Sim-Clip schon zweimal aufgetreten ist —
hier kommt er strukturell wieder.

## 4. Der Session-Baum

Die heutige flache Tabelle (eine Zeile je Session) trägt die neuen
Erfassungsarten nicht mehr. Vorschlag: ein aufklappbarer Baum, der alle Arten
von Messungen gleichwertig aufnimmt.

```
▼ Session 3 · 08.09.2026 · Video (Protokoll: UPDRS Hand Basis)
   ▼ 📹 Aufnahme
      ├ 1  Ruheaufnahme           30 s   ✔ bestätigt   → Ruhetremor      ✔ in Akte
      ├ 2  Kopfdrehung            20 s   ✔ bestätigt     (Dokumentation)
      ├ 3  Finger-Tapping rechts  20 s   ✔ bestätigt   → nicht ausgewertet
      └ 4  Finger-Tapping links   20 s   ○ offen
▼ Session 2 · 01.09.2026 · Live-Paradigmen
   ├ Finger-Tapping rechts    ✔ in Akte
   └ Ruhetremor bilateral     ✔ in Akte
▼ Session 1 · 25.08.2026 · Video (einzelnes Paradigma)
   ▼ 📹 Aufnahme
      └ 1  Finger-Tapping rechts  20 s   ✔ bestätigt   → Finger-Tapping  ✔ in Akte
▶ Session 0 · 12.08.2026 · Video (Import)
▶ Ohne Session · 04.08.2026
```

Session 1 zeigt den Fall „einzelnes Paradigma": strukturell identisch zu einer
Protokoll-Session, nur mit einem Schritt. Der Baum braucht dafür keinen
Sonderfall.

Drei Ebenen: **Session → Erfassungsgruppe → Element** (Segment oder Messung).
Die Gruppenebene entfällt bei reinen Live-Sessions, damit der Baum dort nicht
künstlich tief wird.

### Kontextaktionen je Knotentyp

| Knoten | Aktionen |
|---|---|
| **Session** | Messung hinzufügen (live) · Video aufnehmen · Video importieren · Notiz bearbeiten · Exportieren · Löschen |
| **Segment** | Erneut aufnehmen · Umbenennen / Seite ändern · Auswerten · In Akte übernehmen · Clip ansehen · Löschen |
| **Messung** | Details · Verlauf · Aus Akte entfernen · Löschen |
| **Ohne Session** | Einer Session zuordnen · Löschen |

„Messung hinzufügen" auf Session-Ebene ist der Weg, eine bestehende Session
nachträglich um eine Live-Messung zu ergänzen — unabhängig davon, wie sie
ursprünglich entstanden ist.

### Touch: zurückgestellt

Das heutige Kontextmenü ist kein `QMenu`, sondern ein Touch-Aktionspanel mit
Long-Press-Auslösung (`cellPressed` + Timer), passend zum `ui_mode: touch`.

**Touch wird vorerst zurückgestellt**; der Baum bekommt zunächst ein normales
Rechtsklick-Kontextmenü. Damit das später nachrüstbar bleibt, sollten die
Aktionen je Knotentyp **als Datenstruktur** definiert sein (Liste aus Label,
Bedingung, Callback) und nicht direkt beim Menüaufbau verdrahtet — dann kann
ein Touch-Panel dieselbe Liste rendern, ohne dass die Logik doppelt entsteht.

## 5. Auswirkungen auf Bestehendes

| Bereich | Änderung |
|---|---|
| `VISIT_BLOB` | zusätzlich `acquisition_mode` — kein Schema-Eingriff |
| `VideoSession` | Schlüssel Patient → Session; `mirrored` bei Aufnahme aus der Webcam-Einstellung |
| `video/protocols/` | neu: Protokolldateien + Loader mit Validierung |
| VideoLab | zweiter Eingang „Aufnahme"; Segmentliste als primäre Ansicht ohne Elternvideo |
| Patientenscreen | Tabelle → Baum, Aktionen je Knotentyp |
| Analyse / Export | **unverändert** — Segmente laufen durch den bestehenden Pfad |

## 6. Offene Punkte

- **Zuordnung bestehender Video-Sessions** beim Wechsel von Patient- auf
  Session-Schlüssel: automatisch der jüngsten Session zuordnen oder den
  Untersucher fragen?
## 6b. Ein Bildschirm für die Sitzung — die drei Fälle als *ein* Layout

Entscheidung: **Video ist die Primärquelle** für alles Messende. Import,
Protokoll und einzelnes Paradigma sind keine drei Bildschirme, sondern drei
Arten, einer Sitzung **Inhalt hinzuzufügen**. Alles Weitere — aufnehmen,
sichten, schneiden, auswerten, exportieren, löschen — wirkt auf Elemente
*einer* Liste.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ← Patient   Sitzung 3 · 08.09.2026 · P001                   [＋ Hinzufügen ▾] │
│                                                          Protokoll…           │
│                                                          Einzelnes Paradigma… │
│                                                          Video importieren…   │
├────────────────────────────┬─────────────────────────────────────────────────┤
│ INHALT                     │ ARBEITSBEREICH — folgt der Auswahl links        │
│                            │                                                 │
│ Protokoll: UPDRS Hand      │ ┌─────────────────────────────────────────────┐ │
│  ✔ 1 Ruhe        MPI 0.62  │ │  Vorschau (live)  ·  Take  ·  Video+Timeline│ │
│  ◐ 2 Kopfdrehung           │ └─────────────────────────────────────────────┘ │
│  ○ 3 Tapping re            │ Titel · Instruktion · Zustand                   │
│  ○ 4 Tapping li            │ [● Aufnehmen] [✔ Übernehmen] [↻ Wiederholen]    │
│                            │ ☑ automatisch auswerten      ── Live-Plot ──    │
│ Import: handy_video.mp4    │                                                 │
│  ├ seg_001 Tapping li      │ Ergebnis: Finger Tapping — MPI 0.70   [→ Akte]  │
│  └ seg_002 Tremor          │                                                 │
├────────────────────────────┴─────────────────────────────────────────────────┤
│ Kamera: [OBSBOT ▾]   ☑ Gesicht                                     Status …  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Links: der Inhalt

Eine Liste aller Elemente der Sitzung, gleich welcher Herkunft. Ein Protokoll
erscheint als Gruppe mit seinen Schritten, ein einzelnes Paradigma als Gruppe
mit einem Schritt, ein Import als Video-Knoten mit den geschnittenen Segmenten
darunter. Jedes Element zeigt Zustand (○ ◐ ✔) und — falls vorhanden — sein
Ergebnis. Das ist dieselbe Struktur wie im Sitzungsbaum der Patientenseite,
nur für *eine* Sitzung und mit Arbeitsbereich daneben.

### Rechts: der Arbeitsbereich

Er hat **drei Modi**, gewählt durch das, was links markiert ist — nicht durch
einen eigenen Navigationsschritt:

| markiert ist … | Modus | zeigt |
|---|---|---|
| offener / ungesichteter Schritt | **Aufnahme** | Live-Vorschau mit Erkennung, Instruktion, Countdown, Aufnehmen / Übernehmen / Wiederholen, „automatisch auswerten" |
| bestätigter Schritt oder Segment | **Take** | Wiedergabe des Clips, Ergebnis, Auswerten, → Akte, Erneut aufnehmen |
| importiertes Video | **Schnitt** | das ganze Video mit Timeline, Bereich markieren → Segment anlegen |

Der Schnitt-Modus ist der heutige VideoLab-Videobereich; der Aufnahme-Modus die
rechte Spalte des heutigen Aufnahme-Screens. Beide werden zu **Panes** eines
`QStackedWidget` statt eigener Bildschirme.

### Der Einstieg

Auf der Patientenseite gibt es nur noch **einen** Knopf: *Neue Sitzung*. Er
legt die DB-Sitzung an und öffnet diesen Bildschirm leer, mit dem
„Hinzufügen"-Menü als einzig sinnvoller Aktion (Leerzustand: drei große
Karten statt eines Menüs). Eine bestehende Sitzung öffnet sich aus dem Baum
in genau demselben Bildschirm — es gibt keinen Unterschied zwischen „neu" und
„weitermachen".

Die Knöpfe *VideoLab* und *Gesture Lab* verschwinden von der Patientenseite;
das VideoLab geht in diesem Bildschirm auf, das Gesture Lab bleibt als
experimentelle Funktion über das Hauptmenü erreichbar.

### Umsetzungspfad (risikoarm, in dieser Reihenfolge)

1. `SessionScreen`-Hülle: Kopf, Inhaltsliste (Logik aus dem Sitzungsbaum),
   Fußzeile, leerer gestapelter Arbeitsbereich.
2. Rechte Spalte des `RecordingScreen` → `RecordingPane`; `SessionScreen`
   nutzt sie. `RecordingScreen` entfällt.
3. Videobereich + Timeline + Segmentbearbeitung des `VideoLabScreen` →
   `CutPane`; `VideoLabScreen` entfällt.
4. Patientenseite: ein Knopf, Baum-Aktionen öffnen den `SessionScreen`.

Nach Schritt 2 ist die Hauptfunktion (motorische Aufgabe aufnehmen, sofort
auswerten) bereits vollständig im neuen Bildschirm; Schritt 3 holt den Import
nach.

## 7. Umsetzungsstand

| Baustein | Stand |
|---|---|
| Protokoll-Schema, Loader, Validierung (`video/protocol.py`) | **erledigt** |
| Schrittzustände + Persistenz (`video/store.py`) | **erledigt** |
| Aufnahme-Ablauf (Countdown, Sichtung, Wiederholen) | **erledigt** (`ui/recording_pane.py`) |
| Zuschaltbare Analyse nach dem Bestätigen | **erledigt** (gleicher Weg wie Import) |
| Ein Bildschirm pro Sitzung (§6b) | **überholt → ein Bildschirm pro Patient** (`ui/patient_workbench.py`): Sitzungsliste und Arbeitsbereich sind *ein* Screen, der Patientenklick ist der Einstieg |
| Ergebnis automatisch in die Akte, Neu auswerten / Umlabeln | **erledigt** (`export_or_update`, `relabel_step`) |
| `VideoSession` von Patient auf Session umschlüsseln | **erledigt** (`load_for_session`, Altbestand → jüngste Sitzung) |
| Session-Baum im Patientenscreen | **erledigt** |
| Ein Einstieg „Neue Sitzung", VideoLab/Gesture Lab als Knöpfe entfernt | **erledigt** |
| `acquisition_mode` im `VISIT_BLOB` | offen — der Baum leitet die Art bislang aus dem Inhalt ab |
| Interaktive Paradigmen als Schritt-Typ im Protokoll | offen (experimentell, siehe §6) |
| Komprimierte Clips je Messung ablegen („Video-Datenbank") | **erledigt** (`video/archive.py`, siehe §8) |

## 8. Video-Archiv

Ein bestätigter Take durchläuft eine kleine Pipeline (`RecordingPane`), ein
Schritt nach dem anderen, in einer Warteschlange:

```
Übernehmen ─► Auswerten (Roh-Take, volle Qualität, kein Blur)
           ─► Archivieren: Segment-Extraktor → <session>/<seg_id>.mp4
                (Größencaps, Defacing, Iris-Spur, dann ffmpeg x264-CRF)
           ─► Aufräumen: Roh-Take löschen, sobald Archiv-Clip vorliegt
```

Damit ist eine Eigenaufnahme am Ende **dasselbe Artefakt wie ein
Import-Segment** — gleicher Extraktor, gleiche Caps, gleiche Datenschutz-
einstellung — und die Messung verweist auf den Archiv-Clip.

Zwei Entscheidungen:

- **Auswertung vor Archivierung**, auf dem Roh-Take. Der Archiv-Clip ist
  Review/Archiv; `Segment.analysis_path` bevorzugt `source_path`, solange die
  Datei existiert, danach den Clip.
- **Löschen ist nachgelagert und wiederholt**: Player und Analyse-Sidecar
  halten die Datei kurz offen; das Löschen wird mit wachsenden Abständen
  erneut versucht und gibt notfalls auf (Roh-Take bleibt — nichts geht
  verloren).

Messwerte von heute: 640×480-Take 6,3 MB → 0,17 MB (37×, CRF 23, 0,8 s
Encode); ein 90-s-Basisprotokoll ≈ 90 MB roh → ≈ 2–3 MB archiviert.

**Protokoll-Versionierung** und **Abbruch mittendrin** sind mit der Persistenz
gelöst: `VideoSession.steps` ist eine *Kopie* der Protokollschritte (spätere
Änderungen an der Datei verändern eine laufende Aufnahme nicht), und der
Zustand jedes Schritts liegt im JSON — eine halb abgearbeitete Session lässt
sich über `next_open_step()` fortsetzen.
