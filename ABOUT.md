# Motryx

**Movement Lab – Kontaktlose Bewegungsanalyse**

## Beschreibung

Motryx digitalisiert klinische Bewegungstests mithilfe kontaktloser Tracking-Quellen. Handbewegungen werden erfasst und quantitative Parameter automatisch berechnet — live am Gerät oder nachträglich aus Handy-Videos. Die Eingabequelle ist zur Laufzeit umschaltbar:

- **Leap Motion Controller** – höchste Präzision (absolute 3D-Position).
- **Webcam** (Google MediaPipe) – Standard-Hardware; Tremor über Augen-Referenz (Iris-Skala), mm-Werte als Modellschätzung gekennzeichnet (≈mm).
- **Video** – Handy-Videos im VideoLab schneiden, anonymisieren (hand-schonendes Defacing) und auswerten; Ergebnisse wandern in die Patientenakte.
- **Simulation** – synthetische Daten für Entwicklung und Demonstration.

Die Quelle wird mit jeder Messung gespeichert (Simulationsdaten sind klar als solche markiert) und ist in der Datenbank filterbar.

### Unterstützte Tests

**Motorik (MDS-UPDRS Part III):**
- **Finger Tapping** (3.4) – Daumen-Zeigefinger-Tapping
- **Hand Öffnen/Schließen** (3.5) – Repetitives Öffnen und Schließen
- **Pronation/Supination** (3.6) – Unterarm-Rotation
- **Posturaler Tremor** (3.15) – Haltetremor, bilateral
- **Ruhetremor** (3.17) – Ruhetremor, bilateral

**Kognitiv-motorisch:**
- **Türme von Hanoi** – Scheiben verschieben per Pinzettengriff
- **Räumliche Reaktionszeit (S-SRT)** – Implizites Sequenz-Lernen
- **Trail Making Test (dTMT)** – Verarbeitungsgeschwindigkeit & Set-Shifting

**Okulomotorik (Kamera):**
- **Fixation & Blinzeln** – Blinkrate, Fixationsstabilität, sakkadische Intrusionen
- **Sakkaden-Test** – 5-Punkt-Eichung, dann gaze-contingente Zufallsziele (Latenz, Ziele/min, Richtungsfehler)

**Gesten:**
- **Gesten-Batterie (Gesture Lab)** – 12 klinische Handposen mit Referenz-Bibliothek, Ähnlichkeits-Scoring und Fehleranalyse pro Finger

### Auswertung

- Automatische Feature-Berechnung (YAML-konfigurierbar) mit **Motor Performance Index** als Komposit-Verlaufsmarker
- **Verlaufsansicht**: jedes Merkmal über die Zeit, pro Hand, quellenbewusst
- Patientenverwaltung mit i2b2-Sternschema (SQLite), CSV-Export, JSON-Rohdaten

## Entwickler

**Stefan Brodoehl**

## Technologie

- Python 3.14 / PyQt6; CV-Sidecar (Python 3.12) mit MediaPipe Hand- + Face-Landmarker und OpenCV
- Tracking-Quellen: Leap Motion (Ultraleap Gemini v5) · Webcam/Video (MediaPipe)
- SQLite-Datenbank (i2b2-Sternschema) für Patienten, Messungen und Gesten-Vorlagen
- Echtzeit-Signalverarbeitung (NumPy, SciPy)

## Dokumentation

Technischer Einstieg: [BLUEPRINT.md](BLUEPRINT.md) (Komponenten-Gesamtkarte) ·
[README.md](README.md) (Installation & Bedienung).

## Hinweis

Dieses Werkzeug ist ein Forschungsprototyp und nicht für den klinischen Einsatz zugelassen. Es ersetzt keine ärztliche Untersuchung.
