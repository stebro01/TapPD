# Klinische Daten, Reports und Forschungsexport — Konzept

Stand: 2026-09-10. Prüfung dreier Erweiterungen auf Machbarkeit im heutigen
Stand (Video-Lab, i2b2-Sternschema, YAML-Protokolle) und Vorschlag, wie sie
gebaut würden.

**Umsetzungsstand:** Punkt 1 ist als eine Maske „Parkinson-Anamnese"
umgesetzt (`clinical/`, `ui/form_dialog.py`, Abbildung wie unten
beschrieben; Abweichung: statt mehrerer Masken eine gemeinsame je Sitzung,
mit Fortschreiben der letzten Antworten). Punkt 2 ist als Export-Paket
(ZIP mit HTML/PDF/JSON-Bericht, Videos, Spuren, Rohdaten, Anhängen,
Manifest) umgesetzt, FHIR bleibt offen. Punkt 3 ist als Forschungsexport
(Langtabellen + Codebuch + Pseudonymisierung) umgesetzt; offen sind
Studien-Kohorten, `analysis_version` mit Batch-Neuauswertung und das
pandas-Lademodul.

1. YAML-gesteuerte Eingabemasken für klinische Daten (inkl. Medikation)
2. Export strukturierter Reports und der Videos
3. Wissenschaftliche Analysen über Probanden hinweg

## 0. Was heute schon da ist

| Baustein | Stand | Bedeutung für die drei Punkte |
|---|---|---|
| `OBSERVATION_FACT` mit `VALTYPE_CD` N/T/D/B/**Q** | Schema vorhanden, Q („Fragebogen") ungenutzt | Fragebogen-Items passen ohne Schemaänderung |
| `CONCEPT_DIMENSION` (Pfad, Code, Werttyp, Einheit) | 12 TapPD-Konzepte (Paradigmen) | Klinische Items werden weitere Konzepte, LOINC/SNOMED-Codes sind vorgesehen |
| `CODE_LOOKUP` | Geschlecht, Besuchsart | Kodierte Antworten (z. B. Hoehn & Yahr-Stadien) |
| `STUDY_DIMENSION`, `STUDY_PATIENT_LOOKUP` | im Konzept, nicht angelegt | Kohorten für den Forschungsexport |
| `NOTE_FACT` + Anhänge | fertig | Freitext und Dokumente je Eintrag |
| `Measurement.provenance`, `Segment.meta` | fertig | Herkunft jeder Video-Messung (Kamera, Spiegelung, Clip, Spur, MediaPipe-Version) |
| YAML-Protokolle (`video/protocols`), `paradigms/test_config.yaml` | fertig, mit Loader + Validierung | Muster für deklarative Masken: Schema → Loader → Validierung → UI |
| CSV-Export je Proband | fertig (Kennwerte breit, eine Zeile je Messung) | Basis, aber ohne klinische Variablen, Herkunft, Codebuch |
| FHIR-R4-`Composition` | in DB_KONZEPT §6 spezifiziert, kein Code | Zielformat für den klinischen Report |

Fazit vorab: Alle drei Punkte sind ohne Schemabruch machbar. Die Reihenfolge
sollte 1 → 3 → 2 sein — ohne klinische Kovariaten (1) hat der
Forschungsexport (3) keinen Wert, und der Report (2) ist die Darstellung
dessen, was 1 und 3 sammeln.

## 1. YAML-gesteuerte Eingabemasken

### Idee

Ein Instrument = eine YAML-Datei in `forms/`. Die App rendert daraus eine
Maske, validiert die Eingaben und schreibt jedes Item als eigene kodierte
Beobachtung. Gleiches Muster wie die Protokolle: Schema → Loader mit
Validierung (`Issue` error/note) → generische Qt-Maske.

```yaml
# forms/pd_anamnese.yaml
id: pd_anamnese
name: "Parkinson – Anamnese"
version: 1
scope: patient            # patient = einmal je Patient, fortschreibbar
                          # visit   = je Sitzung (Zustand am Messtag)
sections:
  - title: "Diagnose"
    items:
      - key: diagnosis_year
        label: "Diagnosejahr"
        type: integer
        range: [1950, 2100]
        concept: "TAPPD:PD_DIAGNOSIS_YEAR"
      - key: onset_side
        label: "Erstsymptom-Seite"
        type: choice
        choices: [{code: left, label: "links"}, {code: right, label: "rechts"},
                  {code: bilateral, label: "beidseits"}]
        concept: "SCTID:..."          # kodierte Antwort → CODE_LOOKUP
      - key: dominant_hand
        label: "Dominante Hand"
        type: choice
        choices: [{code: right, label: "rechts"}, {code: left, label: "links"}]
      - key: hoehn_yahr
        label: "Hoehn & Yahr"
        type: choice
        choices: [{code: "1"}, {code: "1.5"}, {code: "2"}, {code: "2.5"},
                  {code: "3"}, {code: "4"}, {code: "5"}]
        concept: "TAPPD:HOEHN_YAHR"
      - key: moca
        label: "MoCA"
        type: integer
        range: [0, 30]
        concept: "LOINC:72172-0"
```

```yaml
# forms/mds_updrs_3.yaml — Items als Skala 0–4, Summe berechnet
id: mds_updrs_3
name: "MDS-UPDRS Teil III (Motorik)"
version: 1
scope: visit
item_defaults: {type: scale, range: [0, 4]}
sections:
  - title: "3.4 Finger Tapping"
    items:
      - {key: u3_4_r, label: "rechts", concept: "TAPPD:UPDRS3_4_R"}
      - {key: u3_4_l, label: "links",  concept: "TAPPD:UPDRS3_4_L"}
  # … 3.1–3.18
computed:
  - key: u3_total
    label: "Summe Teil III"
    expr: "sum(u3_*)"
    concept: "TAPPD:UPDRS3_TOTAL"
```

```yaml
# forms/medikation.yaml — wiederholbare Gruppe + LEDD
id: medication
name: "Parkinson-Medikation"
version: 1
scope: visit
repeat:                    # eine Zeile je Präparat
  key: med
  label: "Präparat"
  items:
    - {key: substance, label: "Wirkstoff", type: choice, catalog: substances}
    - {key: dose_mg, label: "Einzeldosis", type: decimal, unit: mg}
    - {key: per_day, label: "Einnahmen/Tag", type: integer, range: [1, 12]}
    - {key: times, label: "Uhrzeiten", type: text, help: "z. B. 8, 12, 16, 20"}
    - {key: since, label: "seit", type: date, required: false}
items:
  - key: state
    label: "Zustand bei der Messung"
    type: choice
    choices: [{code: on, label: "ON"}, {code: off, label: "OFF"},
              {code: unknown, label: "unklar"}]
    concept: "TAPPD:MED_STATE"
  - key: last_dose_minutes
    label: "Minuten seit letzter Einnahme"
    type: integer
    range: [0, 1440]
    concept: "TAPPD:MIN_SINCE_DOSE"
computed:
  - key: ledd_mg
    label: "LEDD (mg/Tag)"
    expr: "ledd(med)"                 # Faktor-Tabelle unten
    concept: "TAPPD:LEDD"
    unit: mg
catalogs:
  substances:                         # Umrechnungsfaktoren nach Tomlinson et al. 2010
    - {code: levodopa, label: "Levodopa (Standard)", atc: N04BA02, ledd_factor: 1.0}
    - {code: levodopa_cr, label: "Levodopa retard", atc: N04BA02, ledd_factor: 0.75}
    - {code: entacapone, label: "Entacapon", atc: N04BX02, ledd_factor: 0.33, of: levodopa}
    - {code: pramipexole, label: "Pramipexol", atc: N04BC05, ledd_factor: 100}
    - {code: ropinirole, label: "Ropinirol", atc: N04BC04, ledd_factor: 20}
    - {code: rotigotine, label: "Rotigotin", atc: N04BC09, ledd_factor: 30}
    - {code: rasagiline, label: "Rasagilin", atc: N04BD02, ledd_factor: 100}
    - {code: selegiline, label: "Selegilin (oral)", atc: N04BD01, ledd_factor: 10}
    - {code: amantadine, label: "Amantadin", atc: N04BB01, ledd_factor: 1.0}
    - {code: safinamide, label: "Safinamid", atc: N04BD03, ledd_factor: 1.0}
```

Die Faktoren sind Literaturwerte und gehören sichtbar in die YAML, nicht in
den Code — sie werden diskutiert und angepasst.

### Speicherung (ohne Schemaänderung)

| Was | Wo | Wie |
|---|---|---|
| Jedes Item | `OBSERVATION_FACT` | `CONCEPT_CD` aus der YAML, `VALTYPE_CD` N (Zahl/Skala), T (Text/kodiert), D (Datum); `NVAL_NUM`/`TVAL_CHAR`, `UNIT_CD`; `START_DATE` = Sitzungsdatum (scope visit) bzw. Erfassungsdatum (scope patient); `ENCOUNTER_NUM` = Sitzung oder NULL |
| Wiederholte Gruppe (Medikament) | `OBSERVATION_FACT`, `VALTYPE_CD` B, `INSTANCE_NUM` = laufende Nr. | Blob `{substance, atc, dose_mg, per_day, times, since, ledd_mg}` unter `TAPPD:MED` |
| Berechnete Werte (Summen, LEDD) | eigene N-Zeile | `TAPPD:UPDRS3_TOTAL`, `TAPPD:LEDD` — abfragbar ohne Nachrechnen |
| Die ganze Maske | eine Q-Zeile | `TAPPD:FORM:<id>` mit Blob `{form, version, answers}` — Rundreise, Versionierung, Wiedervorlage |
| Konzepte | `CONCEPT_DIMENSION` | beim Laden der YAML registriert (Pfad `/TapPD/Clinical/<form>/<key>/`, Werttyp, Einheit) |
| Kodierte Antworten | `CODE_LOOKUP` | aus `choices` mit `code` — SNOMED/LOINC, wo es einen Code gibt |

Damit beantwortet SQL direkt „alle Tapping-Messungen im OFF mit LEDD > 600":
Messungen (`TAPPD:FINGER_TAPPING`) und klinische Zeilen hängen an derselben
`ENCOUNTER_NUM`.

Verknüpfung mit den Messungen: Der Medikationszustand gehört zur Sitzung;
zusätzlich soll jede Messung beim Speichern `med_state` und
`minutes_since_dose` in ihre `provenance` übernehmen, damit die Zeile im
Forschungsexport ohne Join vollständig ist.

### Oberfläche

* `ui/form_pane.py`: generischer Renderer (Abschnitte, Items nach `type`,
  wiederholbare Gruppen als Tabelle mit „+ Zeile", berechnete Felder
  schreibgeschützt, Pflichtfelder und Bereiche aus der YAML, Fehler inline).
* Einstieg: Menü „Proband ▾ → Klinische Daten…" (scope patient) und im
  Sitzungsbaum „＋ Hinzufügen → Klinische Daten / Medikation" (scope visit).
  Ausgefüllte Masken erscheinen als Knoten der Sitzung („📋 MDS-UPDRS III ·
  32 Punkte", „💊 Medikation · LEDD 620 mg · OFF"), Doppelklick öffnet sie.
* Tests wie bei den Protokollen: Loader + Validierung, Speicher-Rundreise,
  eine Offscreen-Maske je Item-Typ, LEDD-Rechnung gegen Beispiele.

Aufwand grob: Schema + Loader + LEDD 1 Tag, Renderer 1–2 Tage,
DB-Abbildung + Baum + Tests 1 Tag.

## 2. Export strukturierter Reports und Videos

### Heute

CSV je Proband (Kennwerte breit). Kein Report, kein Video-Export, FHIR nur
als Spezifikation.

### Vorschlag: ein `export/`-Paket mit drei Ausgaben

**a) Report (PDF + HTML)** je Proband oder je Sitzung, ohne neue
Abhängigkeit über `QTextDocument` → `QPrinter`: Stammdaten, klinische Daten
(alle Masken, aktuellster Stand), Medikation mit LEDD und Zustand, Messungen
mit Kennwert-Tabelle, MPI und Kurvenbild (Matplotlib-PNG wie im
Details-Dialog), Notizen, Herkunft je Video-Messung (Kamera, Clip,
anonymisiert, Auswertungsquelle). Vorlage als HTML-Template, damit Layout
ohne Python-Änderung anpassbar ist.

**b) Strukturiertes Paket (ZIP)** — das „Übergabeformat":

```
DEMO01_2026-09-10.zip
├─ manifest.json          # Inhalt, Erzeugungszeit, App-/MediaPipe-Version, SHA-256 je Datei
├─ report.json            # Patient, Sitzungen, Masken (answers + Codes), Messungen
│                         #   (features, provenance, Notiz-Verweise), Notizen
├─ report.pdf
├─ videos/<sitzung>/<seg_id>.mp4       # Archiv-Clips (anonymisiert, wenn so aufgenommen)
├─ tracks/<sitzung>/<seg_id>.track.json
├─ raw/<messung>.json                  # Roh-Frames der Auswertung
└─ attachments/<eintrag>/…             # Notiz-Anhänge
```

Optionen beim Export: Videos ja/nein, nur anonymisierte Clips, Roh-Takes
(falls behalten), Pseudonymisierung (Probandencode → Studien-ID, Name und
Geburtsdatum weg, Geburtsjahr bleibt). `report.json` ist dieselbe Struktur,
die auch der Forschungsexport liest — ein Serializer, zwei Verwender.

**c) FHIR R4 `Composition`** nach DB_KONZEPT §6: Proband-, Visit- und
Observation-Sektionen; klinische Items mit LOINC/SNOMED, wo kodiert, sonst
TapPD-Codes; Messungen als Observation mit `valueQuantity` je Kennwert
(oder ein Observation-Bundle je Messung). Sinnvoll erst, wenn ein Empfänger
(KIS, Register) feststeht — bis dahin reicht b).

Aufwand: Serializer + ZIP + Manifest 1 Tag, PDF-Report 1–2 Tage, FHIR 1–2
Tage (bei Bedarf).

## 3. Wissenschaftliche Analysen

### Ziel

Analysefertige Tabellen über alle Probanden (oder eine Studie), langformatig,
mit Codebuch und Herkunft — so, dass R/pandas sie ohne Aufbereitung lesen.

### Forschungsexport (Menü Startbildschirm → „Forschungsexport…")

| Datei | eine Zeile je | Spalten (Auszug) |
|---|---|---|
| `patients.csv` | Proband | pseudonym, sex, birth_year, diagnosis_year, onset_side, dominant_hand, hoehn_yahr, moca |
| `visits.csv` | Sitzung | pseudonym, visit_id, date, med_state, minutes_since_dose, ledd_mg, updrs3_total |
| `measurements.csv` | Messung | measurement_id, pseudonym, visit_id, recorded_at, test_type, hand, source_kind, analysed_on, deidentified, duration_s, eye_ref_coverage, mediapipe_version, camera, mirror |
| `features_long.csv` | Kennwert | measurement_id, feature, value, unit, estimated_scale |
| `clinical_long.csv` | klinisches Item | pseudonym, visit_id, form, version, key, concept, value, unit |
| `medication.csv` | Präparat | pseudonym, visit_id, substance, atc, dose_mg, per_day, ledd_mg |
| `notes.csv` (optional) | Notiz | pseudonym, visit_id, target, text |
| `codebook.md` | Variable | Name, Label, Einheit, Bereich, Quelle (FEATURE_META, Form-YAML) — automatisch erzeugt |
| `signals/` (optional) | Messung | Roh-Frames und Spuren als JSON für Signalanalysen |

Parquet zusätzlich, wenn `pyarrow` installiert ist. Pseudonymisierung
Pflicht; die Zuordnung Probandencode → Pseudonym bleibt lokal
(`data/pseudonyms.json`, nicht im Export).

### Was dafür sonst noch fehlt

* **Kohorte**: `STUDY_DIMENSION`/`STUDY_PATIENT_LOOKUP` anlegen, Probanden
  einer Studie zuordnen, Export je Studie.
* **Versionierung der Auswertung**: Kennwerte hängen an Code und
  MediaPipe-Stand. `provenance` trägt schon die MediaPipe-Version; zusätzlich
  eine `analysis_version` (Git-Hash oder Semver der Feature-Berechnung) an
  jeder Messung, plus ein Kommandozeilen-Lauf „alle Messungen neu
  auswerten" (die Bausteine gibt es: Pipeline, gespeicherte Clips, Spuren).
* **Qualitätsflags** je Messung im Export: Augenreferenz-Abdeckung,
  Hand-Erkennungsquote (Frames mit Hand / Frames), Ausreißer-Anteil —
  Erkennungsquote fällt bei der Analyse ohnehin an und muss nur mitgeschrieben
  werden.
* **Lade-Modul** `research/load.py` (pandas): `load_export(zip_or_dir)` gibt
  die Tabellen als DataFrames zurück, dazu ein Beispiel-Notebook
  (Verlauf MPI über Sitzungen, ON vs. OFF, Korrelation mit UPDRS 3.4).

Aufwand: Export + Codebuch + Pseudonymisierung 1–2 Tage, Studien 0,5 Tag,
Versionierung + Batch-Neuauswertung 1 Tag, Lade-Modul + Notebook 0,5 Tag.

## 4. Empfohlene Reihenfolge

1. **Masken** (Anamnese, MDS-UPDRS III, Medikation mit LEDD) — liefern die
   Kovariaten; ohne sie ist jede Analyse nur Verlaufsbeschreibung.
2. **Forschungsexport** mit Codebuch und Pseudonymisierung — sofort nutzbar,
   auch für die eigenen bisherigen Messungen.
3. **Report + ZIP-Paket** mit Videos — Übergabe an Kollegen, Archiv, Gutachten.
4. **FHIR**, wenn ein Empfänger feststeht.

Offene Entscheidungen: Welche Instrumente zuerst (nur UPDRS III oder I–IV,
NMSS, PDQ-8)? Kodierung mit LOINC/SNOMED von Anfang an oder TapPD-Codes und
Mapping später? Videos im Paket nur anonymisiert oder wahlweise roh
(Einwilligung)?
