# TapPD

**Kontaktlose Motorik-Analyse bei Morbus Parkinson**

## Beschreibung

TapPD digitalisiert die motorischen Handtests der MDS-UPDRS Part III mithilfe eines Leap Motion Controllers. Handbewegungen werden kontaktlos erfasst und quantitative Parameter automatisch berechnet.

### Unterstutzte Tests

**Motorik (MDS-UPDRS Part III):**
- **Finger Tapping** (3.4) – Daumen-Zeigefinger-Tapping
- **Hand Offnen/Schliessen** (3.5) – Repetitives Offnen und Schliessen
- **Pronation/Supination** (3.6) – Unterarm-Rotation
- **Posturaler Tremor** (3.15) – Haltetremor, bilateral
- **Ruhetremor** (3.17) – Ruhetremor, bilateral

**Kognitiv-motorisch:**
- **Tuerme von Hanoi** – Scheiben verschieben per Pinzettengriff
- **Raeumliche Reaktionszeit (S-SRT)** – Implizites Sequenz-Lernen
- **Trail Making Test (dTMT)** – Verarbeitungsgeschwindigkeit & Set-Shifting

## Entwickler

**Stefan Brodoehl**

## Technologie

- Python 3.12+ / PyQt6
- Leap Motion Controller (LM-010) mit Ultraleap Tracking SDK (Hyperion v6 / Gemini v5)
- SQLite-Datenbank fur Patienten und Messungen
- Echtzeit-Signalverarbeitung (NumPy, SciPy)

## Hinweis

Dieses Werkzeug ist ein Forschungsprototyp und nicht fur den klinischen Einsatz zugelassen. Es ersetzt keine arztliche Untersuchung.

---

Version 0.1.0
