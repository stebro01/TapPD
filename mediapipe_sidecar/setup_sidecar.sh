#!/bin/bash
# Bootstrap the MediaPipe sidecar: a Python 3.12 venv (MediaPipe has no 3.13/3.14
# wheels) plus the hand_landmarker.task model.  Idempotent — safe to re-run.
set -e
cd "$(dirname "$0")"

# ── locate a Python 3.12 interpreter ─────────────────────────────
PY312=""
for cand in python3.12 /opt/homebrew/opt/python@3.12/bin/python3.12 /usr/local/opt/python@3.12/bin/python3.12; do
    if command -v "$cand" >/dev/null 2>&1; then PY312="$cand"; break; fi
done
if [ -z "$PY312" ]; then
    echo "ERROR: Python 3.12 nicht gefunden."
    echo "  MediaPipe unterstützt kein Python 3.13/3.14 – die Sidecar braucht 3.12."
    echo "  Installieren mit:  brew install python@3.12"
    exit 1
fi
echo "Verwende Python: $($PY312 --version) ($PY312)"

# ── venv ─────────────────────────────────────────────────────────
if [ ! -d .venv ]; then
    echo "Erstelle virtuelle Umgebung (.venv)..."
    "$PY312" -m venv .venv
fi
echo "Installiere Abhängigkeiten..."
# Prevent macOS from writing AppleDouble (._*) metadata files when extracting
# onto a non-native volume (this repo lives on /Volumes/KB).  matplotlib globs
# the stylelib dir and chokes on stray ._*.mplstyle files, breaking the
# `import mediapipe` chain.
export COPYFILE_DISABLE=1
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo "Entferne AppleDouble-Metadaten (._*)..."
find .venv -name '._*' -delete 2>/dev/null || true

# ── model ────────────────────────────────────────────────────────
MODEL=models/hand_landmarker.task
MODEL_URL="https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
FACE_MODEL=models/face_landmarker.task
FACE_MODEL_URL="https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
mkdir -p models
if [ ! -f "$MODEL" ]; then
    echo "Lade Hand-Landmarker-Modell..."
    curl -fsSL "$MODEL_URL" -o "$MODEL"
fi
if [ ! -f "$FACE_MODEL" ]; then
    echo "Lade Face-Landmarker-Modell..."
    curl -fsSL "$FACE_MODEL_URL" -o "$FACE_MODEL"
fi

echo "Sidecar-Setup abgeschlossen."
echo "Test:  .venv/bin/python3 sidecar.py --stdio"
