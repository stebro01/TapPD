# TapPD – Recording & Analysis Optimization Plan

## Status: TEILWEISE UMGESETZT

---

## 1. Aktuelle Datenfluss-Uebersicht

```
Sensor (120 Hz)
   │
   ▼
HandFrame (pro Frame: ~800 Bytes)
   │  ├─ timestamp_us
   │  ├─ hand_type
   │  ├─ palm_position (x,y,z)
   │  ├─ palm_velocity (vx,vy,vz)
   │  ├─ palm_normal (nx,ny,nz)
   │  ├─ 5x FingerData (tip_position, is_extended, 4x bones)
   │  ├─ pinch_distance, grab_strength
   │  └─ confidence
   │
   ▼
BaseMotorTest.frames[] (akkumuliert im RAM)
   │
   ▼
feature_extraction → dict (6-12 Zahlen)
   │
   ▼
DB: features_json   ← NUR DAS WIRD GESPEICHERT
   │
   ▼
Alles andere: VERWORFEN
```

**Problem**: Nach der Messung existieren nur noch 6-12 aggregierte Zahlen.
Kein Rueckweg zu den Rohdaten. Keine Nachberechnung moeglich.

---

## 2. Was wir speichern SOLLTEN

### 2.1 Rohdaten-Optionen

| Option | Datenvolumen (10s, 120 Hz, 1 Hand) | Pro | Contra |
|--------|-------------------------------------|-----|--------|
| **A: Volle HandFrames** | ~1200 Frames × ~800 B ≈ **960 KB** | Vollstaendig, re-analysierbar | Gross, viele ungenutzte Felder |
| **B: Reduzierte Zeitreihen** | ~1200 × 5 Floats ≈ **24 KB** | Kompakt, reicht fuer Re-Analyse | Finger-Bones verloren |
| **C: Nur extrahierte Metrik** | ~1200 × 1 Float ≈ **10 KB** | Minimal | Kein Zugriff auf andere Dimensionen |
| **D: Wie bisher (nur Features)** | ~200 Bytes | Kleinst | Keine Re-Analyse moeglich |

**Empfehlung: Option B** – Pro Frame speichern wir nur die testspezifisch relevanten Signale:

#### Finger Tapping:
```
timestamp_s, thumb_index_distance_mm, confidence
```

#### Hand Open/Close:
```
timestamp_s, mean_finger_spread_mm, confidence
```

#### Pronation/Supination:
```
timestamp_s, roll_angle_deg, confidence
```

#### Tremor (pro Hand):
```
timestamp_s, palm_x, palm_y, palm_z, roll_angle_rad, confidence
```

### 2.2 Qualitaetsmetriken (neu)

Aktuell fehlen komplett. Fuer jede Messung sollten wir speichern:

```
quality_metrics = {
    "total_frames_received": 1200,
    "frames_rejected_confidence": 15,        # confidence < 0.3
    "frames_rejected_outlier": 3,            # MAD-basiert entfernt
    "mean_confidence": 0.92,
    "min_confidence": 0.45,
    "effective_sample_rate_hz": 118.5,       # tatsaechlich empfangen
    "signal_noise_ratio_db": 22.4,           # geschaetzt
    "detrend_slope": 0.03,                   # wie stark war der Drift?
    "is_mock": false,                        # Simulation oder echt?
    "sensor_type": "leap_motion_lm010",
}
```

### 2.3 Erweiterte Features (derzeit fehlend)

| Test | Fehlendes Feature | Klinische Relevanz |
|------|-------------------|--------------------|
| Alle | **Hesitations** (Pausen > 2× mittlere Zyklusdauer) | MDS-UPDRS bewertet Unterbrechungen separat |
| Alle | **Sequenzdekrement** (erste Haelfte vs zweite Haelfte) | Einfacher und robuster als linearer Fit |
| Finger Tapping | **Opening velocity** vs **Closing velocity** | Asymmetrie oeffnen/schliessen ist PD-spezifisch |
| Finger Tapping | **Freezing-Events** (Amplitude < 20% fuer > 0.5s) | Wichtiges PD-Symptom |
| Hand Open/Close | **Max vs Min Spread pro Zyklus** | Unterscheidet inkomplettes Oeffnen von inkomplettem Schliessen |
| Pronation/Sup. | **Sequenzeffekt** (Speed-Accuracy-Tradeoff) | Typisch fuer Bradykinesie |
| Tremor | **Frequency Stability** (SD der Momentanfrequenz) | PD-Tremor ist frequenzstabil, essentieller Tremor nicht |
| Tremor | **Harmonics** (Anteil 2f, 3f Oberwellen) | Unterscheidet Tremor-Typen |
| Tremor | **Intermittency** (% Zeit mit Tremor > Schwelle) | Nicht alle PD-Patienten tremoren durchgehend |

---

## 3. Analyse der aktuellen Signal-Pipeline: Probleme & Verbesserungen

### 3.1 Resampling

**Aktuell**: `np.interp` (lineare Interpolation)

**Problem**: Bei schnellen Bewegungen (Tapping ~3 Hz bei 120 Hz = 40 Samples/Zyklus)
ist linear OK. Aber bei Tremor-Analyse (5 Hz bei 120 Hz = 24 Samples/Zyklus) kann
lineare Interpolation Hochfrequenzanteile daempfen.

**Verbesserung**: Cubic Spline Interpolation (`scipy.interpolate.CubicSpline`)
fuer bessere Erhaltung der Signalform. Oder: Pruefe ob Resampling ueberhaupt noetig ist –
die meisten Algorithmen (Peak-Detection, FFT) koennen auch mit leicht ungleichmaessigen
Zeitstempeln arbeiten, wenn wir Lomb-Scargle statt FFT verwenden.

**Prioritaet**: MITTEL – Unterschied ist klein bei 120 Hz Abtastrate.

### 3.2 Outlier-Removal

**Aktuell**: MAD-basiert, Z > 3.5, Ersetzung durch Interpolation.

**Problem**: Globaler Schwellwert. Ein langer Tracking-Verlust (z.B. 0.5s Hand weg)
wird als viele Einzeloutlier behandelt statt als zusammenhaengende Luecke.

**Verbesserung**:
1. Luecken-Erkennung: Zusammenhaengende Abschnitte mit confidence < Schwelle markieren
2. Kurze Luecken (<50ms): Interpolieren
3. Lange Luecken (>200ms): Segment-Grenzen setzen, Features nur auf gueltigen Segmenten
4. Anzahl und Dauer der Luecken als Qualitaetsmetrik speichern

**Prioritaet**: HOCH – Beeinflusst alle Tests, besonders mit echtem Sensor.

### 3.3 Bandpass-Filter (Tremor)

**Aktuell**: Butterworth 4. Ordnung, 3-12 Hz, bidirektional.

**Bewertung**: Gut. Bidirektional (sosfiltfilt) garantiert Null-Phasenverschiebung.
3-12 Hz deckt PD-Tremor (4-6 Hz) und essentiellen Tremor (8-12 Hz) ab.

**Moegliche Verbesserung**: Schmalbandige Analyse zusaetzlich:
- 3-7 Hz (PD-Ruhetremor-Band)
- 7-12 Hz (Aktionstremor/essentieller Tremor)
- Power-Ratio zwischen Baendern als diagnostisches Feature

**Prioritaet**: NIEDRIG – Aktuelle Loesung ist fuer Forschungsprototyp ausreichend.

### 3.4 Peak-Detection

**Aktuell**: `scipy.signal.find_peaks` mit Prominence-Schwelle (15% des Signalhubs).

**Problem**:
- Feste 15%-Schwelle ist willkuerlich
- Am Anfang der Messung (hohe Amplitude) und am Ende (niedrig wg. Dekrement)
  gelten unterschiedliche Bedingungen
- Keine Validierung ob detektierte Peaks physiologisch plausibel sind

**Verbesserung**:
1. Adaptive Prominenz: Fenstergrösse 5 Zyklen, Schwelle = 20% des lokalen Hubs
2. Physiologische Plausibilitaet: Finger Tapping max 7 Hz, Pronation max 4 Hz
3. Template-Matching: Einen "idealen" Zyklus als Referenz, Korrelation statt Peak-Detection

**Prioritaet**: MITTEL – Besonders relevant bei echten Daten mit Artefakten.

### 3.5 Amplituden-Berechnung

**Aktuell**: Peak-to-Trough auf originalem (nicht detrended) Signal.

**Bewertung**: Korrekt implementiert. Detrending nur fuer Peak-Position, nicht fuer
Amplitudenwerte – das ist richtig.

**Problem**: Nur der MITTLERE Trough (vor+nach) wird verwendet. Besser waere
die individuelle Peak-to-Trough-Differenz pro Halbzyklus.

**Prioritaet**: NIEDRIG – Aktuelle Methode ist fuer klinische Zwecke ausreichend.

### 3.6 Velocity-Berechnung

**Aktuell**: `np.gradient(signal, 1/fs)` → mittlere absolute Ableitung.

**Problem**: Numerische Differentiation verstaerkt Rauschen. Bei 120 Hz und einer
3 Hz Tapping-Frequenz ist das akzeptabel, aber nicht optimal.

**Verbesserung**:
1. Savitzky-Golay-Ableitung (glaettende Differentiation)
2. Oder: Velocity direkt aus `palm_velocity` des Sensors (bereits vorhanden, aber ungenutzt!)

**Prioritaet**: MITTEL – palm_velocity ist kostenlos verfuegbar und praeziser.

### 3.7 FFT (Tremor)

**Aktuell**: Hann-Window, einseitig, ueber 3 Achsen kombiniert.

**Bewertung**: Solide Implementierung.

**Verbesserung**:
1. **Welch-Methode** statt einfacher FFT (segmentierte, gemittelte PSD → rauscharm)
2. **Kurzzeitspektrum** (STFT): Frequenz-Stabilitaet ueber Zeit analysieren
3. **Hilbert-Transformation**: Momentanfrequenz und -amplitude ohne FFT-Fensterung

**Prioritaet**: MITTEL – Welch waere deutlich robuster.

---

## 4. Experiment-Plan: Reale Messungen analysieren

### 4.1 Referenz-Messungen aufnehmen

Folgende Messungen mit echtem Sensor durchfuehren und Rohdaten speichern:

| # | Test | Szenario | Erwartung | Dauer |
|---|------|----------|-----------|-------|
| 1 | Finger Tapping R | Normales Tapping, gesund | ~3-4 Hz, gleichmaessig | 10s |
| 2 | Finger Tapping R | Simuliertes PD (langsam, Dekrement) | ~1-2 Hz, abnehmend | 10s |
| 3 | Finger Tapping R | Absichtliche Pausen (Freezing) | Luecken im Signal | 10s |
| 4 | Hand Open/Close R | Normal | ~2 Hz | 10s |
| 5 | Pronation/Sup. R | Normal | ~1.5 Hz | 10s |
| 6 | Postural Tremor | Haende still halten (kein Tremor) | Amplitude < 0.5mm | 10s |
| 7 | Postural Tremor | Absichtliches Zittern | ~5 Hz, 2-5mm | 10s |
| 8 | Rest Tremor | Haende auf Knien, still | Amplitude < 0.3mm | 10s |

### 4.2 Analyse-Protokoll pro Messung

Fuer jede Referenzmessung dokumentieren:

```
1. ROHDATEN-INSPEKTION
   - Frames empfangen: ___
   - Effektive Abtastrate: ___ Hz
   - Confidence: min=___ mean=___ Frames < 0.3: ___
   - Tracking-Luecken: Anzahl=___ Max. Dauer=___ms
   - Zeitstempel-Jitter: SD=___ms

2. SIGNAL-QUALITAET
   - Visuell: Zeitserie plotten, Artefakte markieren
   - SNR: geschaetzt aus Signal vs. Ruhephase
   - Drift: linearer Trend in mm oder Grad

3. FEATURE-PLAUSIBILITAET
   - Feature-Werte vs. Erwartung (Tabelle oben)
   - Stimmt n_taps mit visueller Zaehlung ueberein?
   - Ist amplitude_decrement-Vorzeichen korrekt?
   - Tremor: Ist dominante Frequenz plausibel?

4. VERGLEICH MOCK vs REAL
   - Gleiche Features mit Mock-Daten berechnen
   - Unterschiede in Rausch-Niveau, Peak-Form, Drift
```

### 4.3 Diagnostik-Script (zu implementieren)

Ein Script, das eine Messung aufzeichnet und ausfuehrliche Diagnostik ausgibt:

```bash
python -m tools.diagnose_recording --test finger_tapping --hand right --duration 10
```

Output:
- Rohdaten als CSV (timestamp, alle relevanten Signale, confidence)
- Signalverarbeitungs-Schritte als Plots (roh → resampled → outlier-removed → detrended → peaks)
- Feature-Werte mit Konfidenzintervall
- Qualitaetsreport

---

## 5. Geplante Aenderungen (Priorisiert)

### Phase 1: Rohdaten-Speicherung & Qualitaet (HOCH)

- [x] **Rohdaten-Speicherung**: Volle HandFrames als JSON in `data/samples/`
      (gesteuert per Checkbox im Ergebnis-Screen, Pfad in `measurements.raw_data_path`)
- [x] **Onset/Offset-Detection**: Auto-Erkennung des Bewegungsbeginns fuer Tapping/Open-Close/Pronation
      (Rolling-Std-basiert, konfigurierbar in test_config.yaml)
- [ ] **Qualitaetsmetriken**: Berechnen und in `features_json` einfuegen
- [ ] **Mock-Flag**: Feld `is_mock` in `measurements` Tabelle
- [ ] **Verbesserte Luecken-Behandlung**: Zusammenhaengende Luecken erkennen,
      segmentweise Analyse

### Phase 2: Erweiterte Features (MITTEL)

- [ ] **Hesitations/Freezing-Erkennung** fuer Tapping und Open/Close
- [ ] **Opening vs Closing Velocity** fuer Finger Tapping
- [ ] **Sequenzdekrement** (Haelfte 1 vs Haelfte 2) als robustere Alternative
- [ ] **Tremor-Intermittenz** (% der Zeit mit Tremor ueber Schwelle)
- [ ] **Frequenz-Stabilitaet** (SD der Momentanfrequenz via Hilbert)

### Phase 3: Algorithmus-Verbesserungen (MITTEL)

- [ ] **Welch-PSD** statt einfacher FFT fuer Tremor
- [ ] **palm_velocity** nutzen statt numerische Differentiation
- [ ] **Adaptive Peak-Prominenz** (lokaler Signalhub statt global)
- [ ] **Savitzky-Golay** fuer Velocity-Berechnung

### Phase 4: Diagnostik & Validierung (NIEDRIG)

- [ ] **diagnose_recording** Script implementieren
- [ ] **Referenz-Messungen** aufnehmen und dokumentieren
- [ ] **Unit-Tests** mit realen Signaldaten

---

## 6. Datenbank-Erweiterungen

### Neue Tabelle: raw_signals

```sql
CREATE TABLE raw_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    measurement_id INTEGER NOT NULL REFERENCES measurements(id) ON DELETE CASCADE,
    hand TEXT NOT NULL,                    -- "left", "right"
    signal_type TEXT NOT NULL,             -- "thumb_index_dist", "finger_spread", etc.
    sample_rate_hz REAL NOT NULL,
    timestamps_ms BLOB NOT NULL,           -- Float32 Array (ms relative to start)
    values BLOB NOT NULL,                  -- Float32 Array (signal values)
    confidence BLOB NOT NULL               -- Float32 Array (per-sample confidence)
);
```

Speicherbedarf: ~30 KB pro 10s Messung (Float32 statt Float64, nur relevante Signale).

### Erweitertes measurements-Schema

```sql
ALTER TABLE measurements ADD COLUMN is_mock BOOLEAN DEFAULT FALSE;
ALTER TABLE measurements ADD COLUMN quality_json TEXT DEFAULT '{}';
```

`quality_json` enthaelt die Qualitaetsmetriken aus Abschnitt 2.2.

---

## 7. Naechste Schritte

1. **Dieses Dokument reviewen und priorisieren**
2. **Referenz-Messungen mit echtem Sensor aufnehmen** (Abschnitt 4.1)
3. **Phase 1 implementieren** (Rohdaten-Speicherung, Qualitaet)
4. **Referenz-Messungen mit neuer Pipeline wiederholen und vergleichen**
5. **Phase 2-3 iterativ basierend auf realen Daten**

---

*Erstellt: 2026-03-10*
*Aktualisiert: 2026-03-10*
*Status: Phase 1 teilweise umgesetzt (Rohdaten + Onset-Detection), Phasen 2-4 offen*
