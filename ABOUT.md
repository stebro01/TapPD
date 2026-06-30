# Motryx

**Movement Lab – Kontaktlose Bewegungsanalyse**

## Beschreibung

Motryx digitalisiert klinische Bewegungstests mithilfe kontaktloser Hand-Tracking-Quellen. Handbewegungen werden erfasst und quantitative Parameter automatisch berechnet. Die Eingabequelle ist zur Laufzeit umschaltbar:

- **Leap Motion Controller** – höchste Präzision (absolute 3D-Position).
- **Webcam** (Google MediaPipe) – Standard-Hardware, ohne Spezialsensor.
- **Simulation** – synthetische Daten für Entwicklung und Demonstration.

Die erfasste Quelle wird mit jeder Messung gespeichert (Simulationsdaten sind klar als solche markiert).

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

## Entwickler

**Stefan Brodoehl**

## Technologie

- Python 3.14 / PyQt6
- Tracking-Quellen: Leap Motion (Ultraleap Gemini v5) · Webcam (MediaPipe Hand Landmarker, via Python-3.12-Sidecar)
- SQLite-Datenbank (i2b2-Sternschema) für Patienten und Messungen
- Echtzeit-Signalverarbeitung (NumPy, SciPy)

## Hinweis

Dieses Werkzeug ist ein Forschungsprototyp und nicht für den klinischen Einsatz zugelassen. Es ersetzt keine ärztliche Untersuchung.

---

Version 0.1.0
