# BEST Scientific DB Manager - Datenbank-Layout & HL7 Export/Import

> Detaillierte Dokumentation zur Uebertragung auf andere Projekte.
> Stand: 2026-03-31

---

## 1. Technologie-Stack

| Komponente | Technologie |
|---|---|
| **Datenbank** | SQLite3 |
| **Connection** | ElectronConnection (Desktop) / SQLiteConnection (Browser-Fallback) |
| **ORM-Pattern** | Repository Pattern mit BaseRepository |
| **State Management** | Pinia (Vue 3) |
| **HL7-Format** | FHIR R4 (JSON-basiert, NICHT HL7 2.x pipe-delimited) |

### Datei-Struktur

```
src/core/database/
├── migrations/
│   ├── 001-core-schema.js         # 12 Tabellen
│   ├── 002-views.js               # 2 Views
│   ├── 003-triggers.js            # 11 Trigger
│   ├── 004-study-tables.js        # 2 Tabellen (Studien)
│   └── migration-manager.js
├── repositories/
│   ├── base-repository.js         # Generisches CRUD
│   ├── patient-repository.js
│   ├── visit-repository.js
│   ├── observation-repository.js
│   ├── concept-repository.js
│   ├── user-repository.js
│   ├── cql-repository.js
│   ├── study-repository.js
│   ├── note-repository.js
│   └── provider-repository.js
├── sqlite/
│   ├── connection.js
│   ├── electron-connection.js
│   └── real-connection.js
└── seeds/
    └── seed-manager.js

src/core/services/
├── database-service.js            # Haupt-DB-Service
├── hl7-service.js                 # HL7 Export (1144 Zeilen)
├── export-service.js              # Export-Orchestrator
└── imports/
    ├── import-hl7-service.js      # HL7 Import (462 Zeilen)
    ├── import-structure.js        # Standard-Import-Struktur
    └── README_IMPORTS.md          # Import-Dokumentation
```

---

## 2. Datenbank-Schema (alle Tabellen)

### 2.1 PATIENT_DIMENSION

Primaere Patienten-Stammdaten.

| Feld | Typ | Beschreibung |
|---|---|---|
| **PATIENT_NUM** | INTEGER PK AUTOINCREMENT | Eindeutige Patienten-ID |
| **PATIENT_CD** | TEXT UNIQUE | Patientencode (z.B. "DEMO_PATIENT_01") |
| VITAL_STATUS_CD | TEXT | Vitalstatus (z.B. "SCTID: 438949009" = lebendig) |
| BIRTH_DATE | NUMERIC | Geburtsdatum |
| DEATH_DATE | NUMERIC | Sterbedatum |
| AGE_IN_YEARS | NUMERIC | Alter in Jahren |
| SEX_CD | TEXT | Geschlecht (SNOMED-Code, z.B. "SCTID: 407374003" = weiblich) |
| LANGUAGE_CD | TEXT | Sprache |
| RACE_CD | TEXT | Ethnische Zuordnung |
| MARITAL_STATUS_CD | TEXT | Familienstand |
| RELIGION_CD | TEXT | Religion |
| STATECITYZIP_PATH | TEXT | Standort-Pfad |
| PATIENT_BLOB | BLOB | Erweiterte Patientendaten (JSON) |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| SOURCESYSTEM_CD | TEXT | Quellsystem-Code |
| UPLOAD_ID | NUMERIC | Upload-Batch-ID |
| CREATED_AT | TEXT | Erstellungszeitpunkt |
| UPDATED_AT | TEXT | Letzter Aenderungszeitpunkt |

**Indizes:** `idx_patient_patient_cd`, `idx_patient_vital_status`, `idx_patient_sex`, `idx_patient_age`, `idx_patient_birth_date`

---

### 2.2 VISIT_DIMENSION

Patientenbesuche / Encounters.

| Feld | Typ | Beschreibung |
|---|---|---|
| **ENCOUNTER_NUM** | INTEGER PK AUTOINCREMENT | Eindeutige Besuchs-ID |
| **PATIENT_NUM** | NUMERIC FK → PATIENT_DIMENSION | Verweis auf Patient |
| ACTIVE_STATUS_CD | TEXT | Besuchsstatus (z.B. "SCTID: 55561003") |
| START_DATE | NUMERIC | Startdatum |
| END_DATE | NUMERIC | Enddatum |
| INOUT_CD | TEXT | I=stationaer, O=ambulant, E=Notfall |
| LOCATION_CD | TEXT | Standort (z.B. "DEMO_HOSPITAL/INTERNAL") |
| VISIT_BLOB | BLOB | Erweiterte Besuchsdaten |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| SOURCESYSTEM_CD | TEXT | Quellsystem-Code |
| UPLOAD_ID | NUMERIC | Upload-Batch-ID |
| CREATED_AT | TEXT | Erstellungszeitpunkt |
| UPDATED_AT | TEXT | Letzter Aenderungszeitpunkt |

**Indizes:** `idx_visit_patient_num`, `idx_visit_start_date`, `idx_visit_location`

**Kaskadierung:** CASCADE DELETE von PATIENT_DIMENSION; Kaskadiert zu OBSERVATION_FACT und NOTE_FACT

---

### 2.3 OBSERVATION_FACT

Klinische Beobachtungen und Messwerte. **Zentrale Datentabelle.**

| Feld | Typ | Beschreibung |
|---|---|---|
| **OBSERVATION_ID** | INTEGER PK AUTOINCREMENT | Eindeutige Beobachtungs-ID |
| **ENCOUNTER_NUM** | INTEGER FK → VISIT_DIMENSION | Verweis auf Besuch |
| **PATIENT_NUM** | INTEGER FK → PATIENT_DIMENSION | Verweis auf Patient |
| CATEGORY_CHAR | TEXT | Kategorie (z.B. "surveyBEST", "DIAGNOSIS", "LAB") |
| CONCEPT_CD | TEXT FK → CONCEPT_DIMENSION | Konzept-Code (SNOMED/LOINC) |
| PROVIDER_ID | TEXT | Erfassender Arzt/Provider |
| START_DATE | TEXT | Beobachtungsdatum |
| INSTANCE_NUM | NUMERIC | Instanz-Nummer (mehrere pro Konzept) |
| **VALTYPE_CD** | TEXT | **Werttyp: N=numerisch, T=Text, B=BLOB, D=Datum, Q=Fragebogen** |
| TVAL_CHAR | TEXT | Textwert (wenn VALTYPE_CD='T') |
| NVAL_NUM | NUMERIC | Numerischer Wert (wenn VALTYPE_CD='N') |
| VALUEFLAG_CD | TEXT | Wert-Flag/Qualifier |
| QUANTITY_NUM | TEXT | Menge |
| UNIT_CD | TEXT | Einheitscode |
| END_DATE | NUMERIC | Enddatum (fuer Zeitraum-Beobachtungen) |
| LOCATION_CD | TEXT | Ort der Beobachtung |
| CONFIDENCE_NUM | TEXT | Konfidenzniveau |
| OBSERVATION_BLOB | BLOB | Erweiterte Daten (z.B. Fragebogen-JSON) |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| SOURCESYSTEM_CD | TEXT | Quellsystem-Code |
| UPLOAD_ID | NUMERIC | Upload-Batch-ID |
| CREATED_AT | TEXT | Erstellungszeitpunkt |
| UPDATED_AT | TEXT | Letzter Aenderungszeitpunkt |

**Indizes:** `idx_observation_patient_num`, `idx_observation_encounter_num`, `idx_observation_concept_cd`, `idx_observation_start_date`

#### Werttyp-Logik (VALTYPE_CD)

| VALTYPE_CD | Quellfeld | Beispiel |
|---|---|---|
| N | NVAL_NUM | 73 (MoCA Score) |
| T | TVAL_CHAR | "Metformin 2x500mg" |
| B | OBSERVATION_BLOB | JSON-Objekt (z.B. Fragebogen-Ergebnisse) |
| D | START_DATE | "2024-11-29" |
| Q | OBSERVATION_BLOB | Fragebogen-Antworten |

---

### 2.4 CONCEPT_DIMENSION

Medizinische Konzepte und Terminologie (SNOMED-CT, LOINC).

| Feld | Typ | Beschreibung |
|---|---|---|
| CONCEPT_PATH | TEXT | Hierarchischer Konzeptpfad |
| **CONCEPT_CD** | TEXT PK UNIQUE | Konzept-Code (z.B. "LOINC:8302-2", "SCTID: 60621009") |
| **NAME_CHAR** | TEXT NOT NULL | Konzeptname |
| VALTYPE_CD | TEXT | Standard-Werttyp fuer dieses Konzept |
| UNIT_CD | TEXT | Standard-Einheit |
| RELATED_CONCEPT | TEXT | Verwandte Konzepte |
| CONCEPT_BLOB | BLOB | Erweiterte Metadaten |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| SOURCESYSTEM_CD | TEXT | Quellsystem-Code |
| UPLOAD_ID | NUMERIC | Upload-Batch-ID |
| CATEGORY_CHAR | TEXT | Konzept-Kategorie |

**Indizes:** `idx_concept_concept_cd`, `idx_concept_path`, `idx_concept_category`

---

### 2.5 PROVIDER_DIMENSION

Gesundheitsdienstleister / Aerzte.

| Feld | Typ | Beschreibung |
|---|---|---|
| **PROVIDER_ID** | TEXT PK | Provider-Kennung |
| PROVIDER_PATH | TEXT | Organisationshierarchie |
| NAME_CHAR | TEXT | Name |
| CONCEPT_BLOB | BLOB | Erweiterte Daten |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| SOURCESYSTEM_CD | TEXT | Quellsystem-Code |
| UPLOAD_ID | NUMERIC | Upload-Batch-ID |

---

### 2.6 CODE_LOOKUP

Referenzdaten und Nachschlagetabellen.

| Feld | Typ | Beschreibung |
|---|---|---|
| TABLE_CD | TEXT | Referenztabelle |
| COLUMN_CD | TEXT | Referenzspalte |
| **CODE_CD** | TEXT PK UNIQUE | Code |
| NAME_CHAR | TEXT | Beschreibung |
| LOOKUP_BLOB | BLOB | Erweiterte Daten |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| SOURCESYSTEM_CD | TEXT | Quellsystem-Code |
| UPLOAD_ID | NUMERIC | Upload-Batch-ID |

---

### 2.7 USER_MANAGEMENT

Benutzer-Authentifizierung und Rollen.

| Feld | Typ | Beschreibung |
|---|---|---|
| **USER_ID** | INTEGER PK AUTOINCREMENT UNIQUE | Benutzer-ID |
| COLUMN_CD | TEXT | Benutzertyp/Rolle |
| USER_CD | TEXT UNIQUE | Benutzername |
| NAME_CHAR | TEXT | Anzeigename |
| PASSWORD_CHAR | TEXT | Gehashtes Passwort |
| USER_BLOB | TEXT | Erweiterte Benutzerdaten |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| UPLOAD_ID | NUMERIC | Upload-Batch-ID |

**Index:** `idx_user_user_cd`

---

### 2.8 USER_PATIENT_LOOKUP

Zugriffskontrolle: Welcher Benutzer darf welchen Patienten sehen.

| Feld | Typ | Beschreibung |
|---|---|---|
| **USER_PATIENT_ID** | INTEGER PK AUTOINCREMENT UNIQUE | Datensatz-ID |
| **USER_ID** | INTEGER FK → USER_MANAGEMENT | Verweis auf Benutzer |
| **PATIENT_NUM** | INTEGER FK → PATIENT_DIMENSION | Verweis auf Patient |
| NAME_CHAR | TEXT | Anzeigename |
| USER_PATIENT_BLOB | TEXT | Erweiterte Daten |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| UPLOAD_ID | NUMERIC | Upload-Batch-ID |

**Indizes:** `idx_user_patient_user_id`, `idx_user_patient_patient_num`

---

### 2.9 NOTE_FACT

Klinische Notizen und Dokumentation.

| Feld | Typ | Beschreibung |
|---|---|---|
| **NOTE_ID** | INTEGER PK AUTOINCREMENT UNIQUE | Notiz-ID |
| CATEGORY_CHAR | TEXT | Notiz-Kategorie |
| NAME_CHAR | TEXT | Titel |
| NOTE_TEXT | TEXT | Notizinhalt |
| NOTE_BLOB | TEXT | Erweiterte Notizdaten |
| **PATIENT_NUM** | INTEGER FK → PATIENT_DIMENSION | Verweis auf Patient |
| **ENCOUNTER_NUM** | INTEGER FK → VISIT_DIMENSION | Verweis auf Besuch |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| SOURCESYSTEM_CD | TEXT | Quellsystem-Code |
| UPLOAD_ID | NUMERIC | Upload-Batch-ID |

**Indizes:** `idx_note_patient_num`, `idx_note_encounter_num`, `idx_note_category`

---

### 2.10 CQL_FACT

Clinical Quality Language (CQL) Regeln.

| Feld | Typ | Beschreibung |
|---|---|---|
| **CQL_ID** | INTEGER PK AUTOINCREMENT UNIQUE | Regel-ID |
| CODE_CD | TEXT | CQL-Code |
| NAME_CHAR | TEXT | Regelname |
| CQL_CHAR | BLOB | CQL-Daten |
| JSON_CHAR | BLOB | JSON-Repraesentation |
| CQL_BLOB | BLOB | Erweiterte CQL-Daten |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| UPLOAD_ID | NUMERIC | Upload-Batch-ID |

---

### 2.11 CONCEPT_CQL_LOOKUP

Verknuepfung Konzepte ↔ CQL-Regeln.

| Feld | Typ | Beschreibung |
|---|---|---|
| **CONCEPT_CQL_ID** | INTEGER PK AUTOINCREMENT | Datensatz-ID |
| **CONCEPT_CD** | TEXT FK → CONCEPT_DIMENSION | Verweis auf Konzept |
| **CQL_ID** | INTEGER FK → CQL_FACT | Verweis auf CQL-Regel |
| NAME_CHAR | TEXT | Anzeigename |
| RULE_BLOB | TEXT | Regel-Metadaten |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| UPLOAD_ID | NUMERIC | Upload-Batch-ID |

---

### 2.12 STUDY_DIMENSION

Forschungsstudien-Verwaltung.

| Feld | Typ | Beschreibung |
|---|---|---|
| **STUDY_NUM** | INTEGER PK AUTOINCREMENT | Studien-ID |
| **STUDY_CD** | TEXT UNIQUE NOT NULL | Studiencode |
| **NAME_CHAR** | TEXT NOT NULL | Studienname |
| CATEGORY_CHAR | TEXT | Kategorie |
| DESCRIPTION_CHAR | TEXT | Beschreibung |
| STATUS_CD | TEXT DEFAULT 'planning' | Status (planning/active/completed/...) |
| PRINCIPAL_INVESTIGATOR | TEXT | Hauptpruefer |
| TARGET_PATIENT_COUNT | INTEGER | Ziel-Patientenzahl |
| FUNDING_CD | TEXT | Finanzierungscode |
| START_DATE | TEXT | Studienbeginn |
| END_DATE | TEXT | Studienende |
| STUDY_BLOB | TEXT | Erweiterte Studiendaten |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| SOURCESYSTEM_CD | TEXT | Quellsystem-Code |
| UPLOAD_ID | INTEGER | Upload-Batch-ID |
| CREATED_AT | DATETIME DEFAULT CURRENT_TIMESTAMP | Erstellungszeitpunkt |
| UPDATED_AT | DATETIME DEFAULT CURRENT_TIMESTAMP | Letzter Aenderungszeitpunkt |

**Indizes:** `idx_study_study_cd`, `idx_study_category`, `idx_study_status`, `idx_study_principal_investigator`

---

### 2.13 STUDY_PATIENT_LOOKUP

Patienten-Enrollment in Studien.

| Feld | Typ | Beschreibung |
|---|---|---|
| **STUDY_PATIENT_ID** | INTEGER PK AUTOINCREMENT | Datensatz-ID |
| **STUDY_NUM** | INTEGER FK → STUDY_DIMENSION | Verweis auf Studie |
| **PATIENT_NUM** | INTEGER FK → PATIENT_DIMENSION | Verweis auf Patient |
| ENROLLMENT_DATE | TEXT | Einschlussdatum |
| WITHDRAWAL_DATE | TEXT | Ausschlussdatum |
| ENROLLMENT_STATUS_CD | TEXT DEFAULT 'active' | Enrollment-Status |
| STUDY_PATIENT_BLOB | TEXT | Erweiterte Daten |
| UPDATE_DATE | TEXT | Letzte Aenderung |
| DOWNLOAD_DATE | TEXT | Download-Zeitstempel |
| IMPORT_DATE | TEXT | Import-Zeitstempel |
| UPLOAD_ID | INTEGER | Upload-Batch-ID |
| CREATED_AT | DATETIME DEFAULT CURRENT_TIMESTAMP | Erstellungszeitpunkt |
| **UNIQUE(STUDY_NUM, PATIENT_NUM)** | Constraint | Verhindert Duplikate |

**Indizes:** `idx_study_patient_study_num`, `idx_study_patient_patient_num`, `idx_study_patient_status`

---

## 3. Entity-Relationship Diagramm

```
PATIENT_DIMENSION (1) ──────── (M) VISIT_DIMENSION
    │                                   │
    │                                   ├──── (M) OBSERVATION_FACT ──→ (1) CONCEPT_DIMENSION
    │                                   │
    │                                   └──── (M) NOTE_FACT
    │
    ├──── (M) USER_PATIENT_LOOKUP ──→ (1) USER_MANAGEMENT
    │
    └──── (M) STUDY_PATIENT_LOOKUP ──→ (1) STUDY_DIMENSION

CONCEPT_DIMENSION (1) ──── (M) CONCEPT_CQL_LOOKUP ──→ (1) CQL_FACT
```

### Kaskadierungs-Verhalten (Trigger-basiert)

| Loeschen von | Kaskadiert zu |
|---|---|
| PATIENT_DIMENSION | → VISIT_DIMENSION, USER_PATIENT_LOOKUP |
| VISIT_DIMENSION | → OBSERVATION_FACT, NOTE_FACT |
| CONCEPT_DIMENSION | → OBSERVATION_FACT, CONCEPT_CQL_LOOKUP |
| USER_MANAGEMENT | → USER_PATIENT_LOOKUP |
| STUDY_DIMENSION | → STUDY_PATIENT_LOOKUP |

---

## 4. Views

### 4.1 patient_list

Denormalisierte Patientenliste mit aufgeloesten Codes und berechnetem Alter.

- Joined PATIENT_DIMENSION mit CODE_LOOKUP
- Loest SEX_CD, VITAL_STATUS_CD, LANGUAGE_CD, RACE_CD, MARITAL_STATUS_CD, RELIGION_CD auf
- Berechnet Alter aus Beobachtungen (Fallback: AGE_IN_YEARS oder BIRTH_DATE)
- Felder: SEX_RESOLVED, VITAL_STATUS_RESOLVED, etc.

### 4.2 patient_observations

Denormalisierte Beobachtungen mit aufgeloesten Konzept- und Einheitsnamen.

- Joined OBSERVATION_FACT mit CONCEPT_DIMENSION
- Loest UNIT_RESOLVED und TVAL_RESOLVED auf
- Sortiert nach Patient, Encounter, Datum, Konzeptname

---

## 5. Trigger

### Update-Tracking Trigger

Aktualisieren automatisch `UPDATE_DATE` in PATIENT_DIMENSION wenn:
- Patient selbst geaendert wird
- Visit eingefuegt/geaendert/geloescht wird
- Observation eingefuegt/geaendert/geloescht wird

### Cascade-Delete Trigger

Loeschen verknuepfte Datensaetze (siehe Kaskadierungs-Tabelle oben).

---

## 6. HL7 FHIR R4 Export/Import Format

### 6.1 Ueberblick

- **Standard:** FHIR R4 (NICHT HL7 2.x)
- **Format:** JSON (nicht XML, nicht pipe-delimited)
- **Ressource:** `Composition` (klinisches Dokument)
- **Dateiendung:** `.hl7`
- **Sprache:** `de-DE`
- **Digitale Signatur:** SHA-256 Hash zur Integritaetspruefung

### 6.2 Dokument-Struktur

```json
{
  "resourceType": "Composition",
  "id": "dbBEST-[UUID]",
  "meta": {
    "versionId": "1.0",
    "lastUpdated": "2025-09-01T08:32:30.793Z",
    "source": "BEST Medical System",
    "profile": ["http://hl7.org/fhir/StructureDefinition/DocumentReference"]
  },
  "language": "de-DE",
  "text": { "status": "generated", "div": "<div>HTML-Narrativ</div>" },
  "identifier": { "system": "urn:oid:1.2.276.0.76.3.1.131.1.5.1", "value": "[UUID]" },
  "status": "preliminary",
  "type": {
    "coding": [{
      "system": "http://loinc.org",
      "code": "...",
      "display": "..."
    }]
  },
  "subject": { "display": "Patient Name" },
  "date": "ISO-Timestamp",
  "author": [{ "display": "Author" }],
  "title": "Dokumenttitel",
  "section": [ /* siehe Sektionen */ ]
}
```

### 6.3 Sektionen

#### Patient Information Section
```json
{
  "title": "Patient Information",
  "code": { "coding": [{ "system": "http://snomed.info/sct", "code": "422549004" }] },
  "entry": [
    { "title": "Patient: DEMO_PATIENT_01", "value": "DEMO_PATIENT_01" },
    { "title": "Gender", "code": [{"coding": [{"code": "263495000"}]}], "value": "SCTID: 407374003" },
    { "title": "Age", "code": [{"coding": [{"code": "63900-5"}]}], "value": 32 }
  ]
}
```

#### Visit Section
```json
{
  "title": "Visit 1",
  "code": { "coding": [{ "system": "http://snomed.info/sct", "code": "308335008" }] },
  "entry": [
    { "title": "Visit Date", "code": [{"coding": [{"code": "184099003"}]}], "value": "2024-11-29" },
    { "title": "Location", "code": [{"coding": [{"code": "442724003"}]}], "value": "DEMO_HOSPITAL/INTERNAL" }
  ]
}
```

#### Observation Section
```json
{
  "title": "Konzeptname",
  "code": { "coding": [{ "system": "http://snomed.info/sct", "code": "404684003" }] },
  "entry": [
    {
      "title": "Konzeptname",
      "code": [{ "coding": [{ "system": "http://snomed.info/sct", "code": "CONCEPT_CD", "display": "Name" }] }],
      "value": 73,
      "text": { "status": "generated", "div": "HTML" }
    }
  ]
}
```

### 6.4 Feld-Mapping: DB → HL7

#### Patient

| DB-Feld | HL7-Feld |
|---|---|
| PATIENT_CD | entry.title = "Patient: [PATIENT_CD]", entry.value |
| SEX_CD | entry mit SNOMED 263495000, value = SNOMED-Code |
| AGE_IN_YEARS | entry mit SNOMED 63900-5, value = numerisch |
| VITAL_STATUS_CD | entry.value (Default: "SCTID: 438949009" = lebendig) |

#### Visit

| DB-Feld | HL7-Feld |
|---|---|
| ENCOUNTER_NUM | section.title = "Visit [N]" |
| START_DATE | entry mit SNOMED 184099003 |
| LOCATION_CD | entry mit SNOMED 442724003 |
| INOUT_CD | I=stationaer, O=ambulant, E=Notfall |

#### Observation

| DB-Feld | HL7-Feld |
|---|---|
| CONCEPT_CD | entry.code[0].coding[0].code |
| VALTYPE_CD='N' | entry.value = NVAL_NUM |
| VALTYPE_CD='T' | entry.value = TVAL_CHAR |
| VALTYPE_CD='B' | entry.value = OBSERVATION_BLOB (JSON) |
| VALTYPE_CD='D' | entry.value = START_DATE |
| UNIT_CD | in Coding-Struktur |

### 6.5 Konzept-Mapping (LOINC → SNOMED)

| Interner Code | SNOMED-Code | Bezeichnung |
|---|---|---|
| LOINC:8302-2 | 271649006 | Koerpergroesse |
| LOINC:29463-7 | 27113001 | Koerpergewicht |
| LOINC:85354-9 | 271649006 | Blutdruck |
| SCTID:273249006 | 273249006 | Bewertungsskala |
| CUSTOM: RAW_DATA | 404684003 | Rohdaten |

### 6.6 Kategorie-Erkennung beim Import

| Bedingung | Kategorie |
|---|---|
| Titel enthaelt "questionnaire" oder "custom" | SURVEY_BEST |
| Code enthaelt LID: 72172 | SURVEY_BEST (MoCA) |
| Code enthaelt SCTID: 47965005 | DIAGNOSIS |
| Code enthaelt LID: 2947 oder 6298 | LAB |
| Code enthaelt SCTID: 399423000 | ADMINISTRATIVE |
| Code enthaelt SCTID: 60621009 | VITAL_SIGNS |
| Code enthaelt LID: 52418 | MEDICATION |
| Code enthaelt LID: 74287 | SOCIAL_HISTORY |
| Code enthaelt SCTID: 262188008 | ASSESSMENT |
| Sonst | CLINICAL |

### 6.7 Standardisierte Import-Struktur

Nach dem HL7-Import werden die Daten in diese Struktur transformiert:

```json
{
  "metadata": {
    "importDate": "2025-09-02T10:30:00Z",
    "source": "dateiname.hl7",
    "format": "hl7",
    "patientCount": 2,
    "visitCount": 4,
    "observationCount": 46
  },
  "exportInfo": {
    "exportDate": "2025-09-01T...",
    "source": "BEST Medical System",
    "version": "1.0"
  },
  "data": {
    "patients": [{
      "PATIENT_CD": "DEMO_PATIENT_01",
      "SEX_CD": "SCTID: 407374003",
      "AGE_IN_YEARS": 32,
      "SOURCESYSTEM_CD": "HL7_IMPORT",
      "UPLOAD_ID": 1
    }],
    "visits": [{
      "PATIENT_NUM": 1,
      "ENCOUNTER_NUM": 1,
      "START_DATE": "2024-11-29",
      "LOCATION_CD": "DEMO_HOSPITAL/INTERNAL",
      "INOUT_CD": "E",
      "SOURCESYSTEM_CD": "HL7_IMPORT"
    }],
    "observations": [{
      "OBSERVATION_ID": 1,
      "ENCOUNTER_NUM": 1,
      "PATIENT_NUM": 1,
      "CONCEPT_CD": "LID: 72172-0",
      "VALTYPE_CD": "N",
      "NVAL_NUM": 73,
      "TVAL_CHAR": "MOCA Total Score",
      "SOURCESYSTEM_CD": "HL7_IMPORT"
    }]
  }
}
```

---

## 7. Export/Import Konfiguration

### Export

```javascript
{
  maxPatientsPerExport: 1000,
  includeVisits: true,
  includeObservations: true,
  includeNotes: false
}
```

### Import

```javascript
{
  maxFileSize: '50MB',
  supportedFormats: ['csv', 'json', 'hl7', 'html'],
  validationLevel: 'strict',
  duplicateHandling: 'skip',
  batchSize: 1000,
  transactionMode: 'single'
}
```

---

## 8. Design-Patterns

### Repository Pattern
- Jede Entitaet hat eine eigene Repository-Klasse
- Erbt von BaseRepository fuer generisches CRUD
- Parameterisierte Queries verhindern SQL-Injection
- Spezifische Methoden fuer entitaetsspezifische Abfragen

### View-basierte Abfragen
- `patient_list` und `patient_observations` reduzieren N+1-Queries
- Vorab-Joins fuer UI-Rendering optimiert

### Audit-Tracking
- CREATED_AT, UPDATED_AT auf Haupttabellen
- Trigger pflegen UPDATE_DATE kaskadierend
- IMPORT_DATE trackt Datenherkunft
- UPLOAD_ID gruppiert Batch-Operationen

### Zugriffskontrolle
- USER_PATIENT_LOOKUP steuert, welche Patienten ein Benutzer sehen darf
- Pagination mit Access-Control-Filterung
- Admin-Bypass moeglich

---

## 9. Standard-Funktionen & Repository-API

### 9.1 BaseRepository (Generisches CRUD)

Alle Entity-Repositories erben von `BaseRepository` und haben damit folgende Standardmethoden:

```javascript
class BaseRepository {
  constructor(connection, tableName, primaryKey)

  // === LESEN ===
  findById(id)                          // → Object|null
  findAll({ limit, offset, orderBy, orderDirection })  // → Array
  findByCriteria(criteria, options)     // → Array  (dynamische WHERE-Klausel)
  countByCriteria(criteria)             // → number
  exists(criteria)                      // → boolean

  // === SCHREIBEN ===
  create(entity)                        // → Object (mit generierter ID)
  update(id, entity)                    // → boolean
  updateByCriteria(criteria, updateData) // → number (Anzahl geaenderter Zeilen)

  // === LOESCHEN ===
  delete(id)                            // → boolean
  deleteByCriteria(criteria)            // → number (Anzahl geloeschter Zeilen)

  // === RAW SQL ===
  executeRawQuery(sql, params)          // → { success, data }
  executeRawCommand(sql, params)        // → { success, lastID, changes }
}
```

#### Criteria-Objekt Syntax

Das `criteria`-Objekt unterstuetzt verschiedene Filtertypen:

```javascript
// Einfache Gleichheit
{ SEX_CD: 'SCTID: 407374003' }

// Array → IN-Klausel
{ PATIENT_NUM: [1, 2, 3] }

// Custom Operatoren
{ AGE_IN_YEARS: { operator: 'BETWEEN', value: [18, 65] } }
{ NAME_CHAR: { operator: 'LIKE', value: '%Smith%' } }
{ AGE_IN_YEARS: { operator: '>', value: 30 } }

// Kombination
{
  SEX_CD: 'SCTID: 407374003',
  AGE_IN_YEARS: { operator: 'BETWEEN', value: [18, 65] },
  SOURCESYSTEM_CD: 'HL7_IMPORT'
}
```

#### Options-Objekt Syntax

```javascript
{
  limit: 20,            // Max Ergebnisse
  offset: 40,           // Offset (fuer Pagination)
  orderBy: 'PATIENT_CD', // Sortierfeld
  orderDirection: 'ASC'  // ASC oder DESC
}
```

---

### 9.2 PatientRepository

Erbt alle BaseRepository-Methoden. Tabelle: `PATIENT_DIMENSION`, PK: `PATIENT_NUM`

```javascript
class PatientRepository extends BaseRepository {
  // --- Suche ---
  findByPatientCode(patientCode)              // → Object|null (exakter Code)
  findByPatientCodeWithConcepts(patientCode)  // → Object|null (ueber patient_list View)
  findByVitalStatus(vitalStatus)              // → Array
  findBySex(sex)                              // → Array
  findByAgeRange(minAge, maxAge)              // → Array (BETWEEN)
  findByBirthDateRange(startDate, endDate)    // → Array
  findBySourceSystem(sourceSystem)            // → Array
  searchPatients(searchTerm)                  // → Array (LIKE auf PATIENT_CD, BLOB, Standort)
  searchPatientsWithConcepts(searchTerm, userAccess) // → Array (LIKE auf View-Felder inkl. aufgeloefte Codes)

  // --- Erweiterte Suche mit Konzept-Aufloesung ---
  findPatientsByCriteria(criteria)            // → Array (Multi-Filter, kombiniert mit Textsuche)
  findPatientsByCriteriaWithConcepts(criteria) // → Array (ueber patient_list View, mit Zugriffskontrolle)
  findByCriteriaFromView(searchCriteria, options) // → Array (View-Query mit User-Access-Filter)

  // --- Pagination ---
  getPatientsPaginated(page, pageSize, criteria, currentUserId, isAdmin)
  // → { patients: [], pagination: { currentPage, pageSize, totalCount, totalPages, hasNextPage, hasPreviousPage } }

  // --- Statistiken ---
  getPatientStatistics()
  // → { totalPatients, byVitalStatus: [{VITAL_STATUS_CD, count}], bySex: [{SEX_CD, count}], averageAge }

  // --- Erstellen/Aktualisieren ---
  createPatient(patientData)   // Validiert PATIENT_CD, prueft Duplikate, setzt Audit-Felder
  updatePatient(patientId, updateData)  // Setzt UPDATE_DATE und UPDATED_AT automatisch
}
```

#### Zugriffskontrolle-Logik

```
Wenn userAccess.isAdmin == true:
  → Alle Patienten sichtbar

Wenn userAccess.userId vorhanden:
  → INNER JOIN USER_PATIENT_LOOKUP
  → WHERE (USER_ID = currentUserId OR USER_ID = 0)
  → USER_ID = 0 ist der "oeffentliche" Benutzer (alle duerfen sehen)

Kein userAccess:
  → Alle Patienten sichtbar (kein Filter)
```

---

### 9.3 VisitRepository

Tabelle: `VISIT_DIMENSION`, PK: `ENCOUNTER_NUM`

```javascript
class VisitRepository extends BaseRepository {
  // --- Erstellen ---
  createVisit(visitData)
  // Validiert: PATIENT_NUM erforderlich
  // Defaults: ACTIVE_STATUS_CD='SCTID: 55561003', START_DATE=heute, INOUT_CD='O', SOURCESYSTEM_CD='SYSTEM'

  // --- Suche ---
  findByPatientNum(patientNum)      // → Array (sortiert nach START_DATE DESC)
  findByDateRange(startDate, endDate) // → Array
  findByLocationCode(locationCode)  // → Array
  findByActiveStatus(activeStatus)  // → Array
  findByInoutCode(inoutCode)        // → Array ('I'=stationaer, 'O'=ambulant, 'E'=Notfall)
  findBySourceSystem(sourceSystem)  // → Array
  findActiveVisits()                // → Array (WHERE ACTIVE_STATUS_CD = 'A')
  findVisitsWithObservations()      // → Array (JOIN mit OBSERVATION_FACT)
  searchVisits(searchTerm)          // → Array (LIKE auf Location, Status, PatientNum)

  // --- Pagination ---
  getVisitsPaginated(page, pageSize, { patientNum, activeStatus, locationCode, inoutCode, startDate, endDate })
  // → { visits: [], pagination: { page, pageSize, totalCount, totalPages, hasNext, hasPrev } }

  // --- Statistiken ---
  getVisitStatistics()
  // → { totalVisits, byStatus, byLocation, byInout, byMonth (letzte 12 Monate) }

  // --- Aktionen ---
  updateVisit(encounterNum, updateData)  // Validiert Existenz, gibt aktualisierten Datensatz zurueck
  closeVisit(encounterNum, endDate)      // Setzt END_DATE und ACTIVE_STATUS_CD='I'
  getPatientVisitTimeline(patientNum)    // → Array mit observationCount pro Visit
}
```

---

### 9.4 ObservationRepository

Tabelle: `OBSERVATION_FACT`, PK: `OBSERVATION_ID`

```javascript
class ObservationRepository extends BaseRepository {
  // --- Erstellen ---
  createObservation(observationData)
  // Validiert: ENCOUNTER_NUM, PATIENT_NUM, CONCEPT_CD erforderlich
  // Defaults: CATEGORY_CHAR='CLINICAL', START_DATE=heute, VALTYPE_CD='T', SOURCESYSTEM_CD='SYSTEM'

  // --- Suche ---
  findByPatientNum(patientNum)          // → Array
  findByVisitNum(encounterNum)          // → Array (alias: findByEncounterNum)
  findByConceptCode(conceptCode)        // → Array
  findByCategory(category)              // → Array
  findByDateRange(startDate, endDate)   // → Array
  findByValueType(valueType)            // → Array ('N', 'T', 'B')
  findByProvider(providerId)            // → Array
  findByNumericValueRange(min, max)     // → Array (nur VALTYPE_CD='N')
  findByTextValue(textPattern)          // → Array (LIKE, nur VALTYPE_CD='T')
  findBySourceSystem(sourceSystem)      // → Array
  findWithBlobData()                    // → Array (WHERE OBSERVATION_BLOB IS NOT NULL)
  searchObservations(searchTerm)        // → Array (LIKE auf Category, Concept, TextValue, IDs)

  // --- Kontextuelle Abfragen ---
  getObservationsWithContext(patientNum)
  // → Array (JOIN mit PATIENT_DIMENSION + VISIT_DIMENSION, inkl. PATIENT_CD, LOCATION_CD)

  getObservationsWithResolvedConcepts(patientNum)
  // → Array (ueber patient_observations View, aufgeloeste Konzeptnamen)

  // --- Pagination ---
  getObservationsPaginated(page, pageSize, { patientNum, encounterNum, conceptCode, category, valueType, providerId, startDate, endDate })

  // --- Statistiken ---
  getObservationStatistics()
  // → { totalObservations, byCategory, byValueType, byConcept (Top 10), byMonth (12 Monate), byProvider (Top 10) }

  // --- Hilfsfunktionen ---
  getObservationValue(observation)       // Gibt typisierten Wert zurueck (N→number, T→string, B→parsed JSON)
  getSurveyObservations(surveyCode)      // Fragebogen-Observations nach Code
  getPatientNumericSummary(patientNum)   // → Array { CONCEPT_CD, count, average, minimum, maximum, total }
  updateObservation(observationId, data) // Validiert Existenz vor Update
}
```

#### Werttyp-Aufloesung (getObservationValue)

```javascript
switch (observation.VALTYPE_CD) {
  case 'N': return observation.NVAL_NUM           // Numerisch
  case 'T': return observation.TVAL_CHAR          // Text
  case 'B': return JSON.parse(observation.OBSERVATION_BLOB) // JSON-Objekt
  default:  return observation.TVAL_CHAR || observation.NVAL_NUM  // Fallback
}
```

---

### 9.5 StudyRepository

Tabelle: `STUDY_DIMENSION`, PK: `STUDY_NUM`

```javascript
class StudyRepository extends BaseRepository {
  // --- CRUD ---
  create(studyData)    // Generiert STUDY_CD='STUDY_[timestamp]', transformiert Ergebnis, enriched mit patientCount
  findById(studyId)    // Override: enriched mit patientCount
  findAll(options)     // Override: enriched mit patientCount
  update(studyId, data) // Schuetzt STUDY_NUM und CREATED_AT, setzt UPDATED_AT automatisch
  delete(studyId)

  // --- Suche ---
  findByCode(studyCode)     // → Object|null (nach STUDY_CD)
  findByCategory(category)  // → Array
  findByStatus(status)      // → Array
  search({ name, category, status, principalInvestigator }) // → Array (Multi-Kriterien)

  // --- Patienten-Enrollment ---
  enrollPatient(studyId, patientId, enrollmentData)
  // INSERT OR REPLACE in STUDY_PATIENT_LOOKUP, Default: ENROLLMENT_DATE=heute, STATUS='active'

  withdrawPatient(studyId, patientId, withdrawalDate)
  // Setzt ENROLLMENT_STATUS_CD='withdrawn' und WITHDRAWAL_DATE

  getEnrolledPatients(studyId)
  // → Array (JOIN PATIENT_DIMENSION + STUDY_PATIENT_LOOKUP, inkl. Enrollment-Felder)

  getPatientStudies(patientId)
  // → Array (JOIN STUDY_DIMENSION + STUDY_PATIENT_LOOKUP)

  // --- Statistiken ---
  getStatistics()
  // → { totalStudies, studiesByStatus, studiesByCategory, totalEnrolledPatients (aktive) }

  // --- Daten-Transformation ---
  transformStudyData(rawStudy, patientCount)
  // Mapped DB-Felder auf App-Felder: STUDY_NUM→id, NAME_CHAR→name, STATUS_CD→status, etc.
  // Parst STUDY_BLOB als JSON (Notizen)

  enrichStudiesWithPatientCounts(studies)
  // Fuegt patientCount hinzu (Batch-Query ueber STUDY_PATIENT_LOOKUP WHERE status='active')
}
```

---

### 9.6 DatabaseService (Singleton)

Zentraler Einstiegspunkt fuer alle DB-Operationen.

```javascript
// Initialisierung (einmalig beim App-Start)
await databaseService.initialize(databasePath)
// 1. Erstellt Connection (Electron oder Browser-Fallback)
// 2. Testet Verbindung
// 3. Registriert + fuehrt Migrationen aus
// 4. Seed-Daten bei leerer DB (Benutzer + Konzepte)
// 5. Initialisiert alle Repositories

// Repository-Zugriff
const patientRepo = databaseService.getRepository('patient')
const visitRepo   = databaseService.getRepository('visit')
const obsRepo     = databaseService.getRepository('observation')
const studyRepo   = databaseService.getRepository('study')
const conceptRepo = databaseService.getRepository('concept')
const userRepo    = databaseService.getRepository('user')
const cqlRepo     = databaseService.getRepository('cql')

// Direkte SQL-Ausfuehrung
await databaseService.executeQuery(sql, params)    // SELECT
await databaseService.executeCommand(sql, params)   // INSERT/UPDATE/DELETE
await databaseService.executeTransaction(commands)  // Array von {sql, params}

// Verwaltung
await databaseService.getMigrationStatus()
await databaseService.validateDatabase()
await databaseService.resetDatabase()               // DROP ALL + Re-Migration
await databaseService.getDatabaseStatistics()
// → { PATIENT_DIMENSION: count, VISIT_DIMENSION: count, ..., databasePath, isConnected }
await databaseService.close()
```

---

## 10. Business-Logik & Workflows

### 10.1 Patient anlegen

```
1. PatientRepository.createPatient(data)
   ├── Validierung: PATIENT_CD erforderlich
   ├── Duplikat-Pruefung: findByPatientCode()
   ├── Audit-Felder setzen: IMPORT_DATE, UPDATE_DATE, CREATED_AT, UPDATED_AT
   └── BaseRepository.create() → INSERT mit generierter PATIENT_NUM
```

### 10.2 Beobachtung erfassen

```
1. ObservationRepository.createObservation(data)
   ├── Validierung: ENCOUNTER_NUM, PATIENT_NUM, CONCEPT_CD erforderlich
   ├── Defaults setzen: CATEGORY_CHAR='CLINICAL', VALTYPE_CD='T', START_DATE=heute
   ├── BaseRepository.create() → INSERT
   └── Trigger: UPDATE_DATE auf PATIENT_DIMENSION wird automatisch aktualisiert
```

### 10.3 Visit-Lebenszyklus

```
1. Erstellen:  visitRepo.createVisit({ PATIENT_NUM, LOCATION_CD, INOUT_CD })
               → Default: ACTIVE_STATUS_CD='SCTID: 55561003' (aktiv), INOUT_CD='O' (ambulant)

2. Beobachtungen hinzufuegen: obsRepo.createObservation({ ENCOUNTER_NUM, ... })

3. Abschliessen: visitRepo.closeVisit(encounterNum)
                 → Setzt END_DATE=heute, ACTIVE_STATUS_CD='I' (inaktiv)
```

### 10.4 Studien-Enrollment

```
1. Studie erstellen:   studyRepo.create({ name, category, status:'planning', ... })
2. Patient einschliessen: studyRepo.enrollPatient(studyId, patientId)
                          → STUDY_PATIENT_LOOKUP mit ENROLLMENT_STATUS_CD='active'
3. Patient ausschliessen: studyRepo.withdrawPatient(studyId, patientId)
                          → ENROLLMENT_STATUS_CD='withdrawn', WITHDRAWAL_DATE gesetzt
4. Patienten auflisten:   studyRepo.getEnrolledPatients(studyId)
```

### 10.5 HL7 Export-Workflow

```
1. hl7Service.exportToHl7()
   ├── Lade Patientendaten (inkl. Visits + Observations)
   ├── createCdaDocument()
   │   ├── prepareDocumentMetadata()  → ID, Version, Zeitstempel
   │   ├── prepareDocumentText()      → HTML-Narrativ
   │   ├── preparePatientSections()   → Patient-Info als FHIR-Sektionen
   │   ├── prepareVisitSections()     → Visits + zugehoerige Observations
   │   └── prepareObservationSections() → Observations gruppiert nach Konzept
   ├── signDocument()                 → SHA-256 Signatur
   └── Return: FHIR Composition JSON
```

### 10.6 HL7 Import-Workflow

```
1. importHl7Service.importFromHl7(fileContent)
   ├── parseHl7Content()              → JSON parsen
   ├── validateHl7Document()          → Strukturvalidierung (resourceType, sections)
   ├── transformToImportStructure()
   │   ├── extractDataFromSections()
   │   │   ├── extractPatientsFromSection()    → Patient-Daten extrahieren
   │   │   ├── extractVisitFromSection()       → Visit-Daten extrahieren
   │   │   └── extractObservationsFromSection() → Observations extrahieren
   │   ├── determineVisitType()        → Location → INOUT_CD Mapping
   │   └── determineCategory()         → Concept-Code → Kategorie Mapping
   ├── validateImportStructure()       → Ergebnis-Validierung
   └── Return: Standardisierte Import-Struktur
       → Weiterverarbeitung durch Import-Service (Batch-Insert in DB)
```

### 10.7 Pagination-Pattern

Alle Repositories verwenden dasselbe Pagination-Muster:

```javascript
// Aufruf
const result = await patientRepo.getPatientsPaginated(
  page,         // 1-basiert
  pageSize,     // z.B. 20
  criteria,     // Filter-Objekt
  currentUserId, // fuer Zugriffskontrolle
  isAdmin        // Admin-Bypass
)

// Ergebnis-Struktur
{
  patients: [...],    // (oder visits, observations, etc.)
  pagination: {
    currentPage: 1,
    pageSize: 20,
    totalCount: 150,
    totalPages: 8,
    hasNextPage: true,
    hasPreviousPage: false
  }
}
```

### 10.8 Statistik-Pattern

Jedes Repository bietet ein `getXxxStatistics()`-Methode:

```javascript
// Patient-Statistiken
{ totalPatients, byVitalStatus, bySex, averageAge }

// Visit-Statistiken
{ totalVisits, byStatus, byLocation, byInout, byMonth }

// Observation-Statistiken
{ totalObservations, byCategory, byValueType, byConcept (Top10), byMonth, byProvider (Top10) }

// Study-Statistiken
{ totalStudies, studiesByStatus, studiesByCategory, totalEnrolledPatients }

// Gesamt-DB-Statistiken (via DatabaseService)
{ PATIENT_DIMENSION: count, VISIT_DIMENSION: count, OBSERVATION_FACT: count, ... }
```

---

## 11. Migrations-System

### Ablauf

```
1. DatabaseService.initialize()
   ├── MigrationManager erstellen
   ├── Migrationen registrieren (001, 002, 003, 004)
   └── initializeDatabase()
       ├── Erstellt _migrations Tabelle (falls nicht vorhanden)
       ├── Prueft bereits ausgefuehrte Migrationen
       └── Fuehrt ausstehende Migrationen in Reihenfolge aus

Jede Migration hat:
  { version: '001', name: 'core-schema', up: async (connection) => { ... } }
```

### Seed-Daten

Bei leerer Datenbank (0 User) werden automatisch eingefuegt:
- Standard-Benutzer (Admin, etc.)
- Basis-Konzepte (SNOMED/LOINC-Codes)

---

## 12. Haeufig verwendete SNOMED/LOINC-Codes

| Code | System | Bedeutung |
|---|---|---|
| 438949009 | SNOMED | Lebendig |
| 407374003 | SNOMED | Weiblich |
| 32570691000036108 | SNOMED | Weiblich (regional) |
| 422549004 | SNOMED | Patienteninformation |
| 308335008 | SNOMED | Besuch/Visit |
| 263495000 | SNOMED | Geschlecht |
| 404684003 | SNOMED | Klinische Beobachtung |
| 184099003 | SNOMED | Besuchsdatum |
| 442724003 | SNOMED | Standort |
| 55561003 | SNOMED | Aktiv |
| 8302-2 | LOINC | Koerpergroesse |
| 29463-7 | LOINC | Koerpergewicht |
| 72172-0 | LOINC | MoCA-Score |
| 63900-5 | LOINC | Alter |
