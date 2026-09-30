#!/bin/bash
# Motryx launcher (macOS / Linux) — counterpart of start.ps1.
#
#   ./start.sh            webcam tracking (default on this branch)
#   ./start.sh --mock     simulation source, no camera needed
#   ./start.sh --leap     also set up and enable the Leap Motion path
#
# First run: creates the app venv (Python 3.12+), then the MediaPipe sidecar
# venv (a separate Python 3.12 — MediaPipe has no 3.13/3.14 wheels) plus the
# landmark models (~200 MB download). Idempotent — safe to re-run.
set -e
cd "$(dirname "$0")"

# -- flags handled here must not reach main.py ---------------------
USE_LEAP=0
APP_ARGS=()
for a in "$@"; do
    case "$a" in
        --leap) USE_LEAP=1 ;;
        *) APP_ARGS+=("$a") ;;
    esac
done

# -- main app venv (Python 3.12+) ----------------------------------
find_python() {
    for cand in python3.14 python3.13 python3.12 \
                /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
                python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
                echo "$cand"; return 0
            fi
        fi
    done
    return 1
}

if [ ! -d .venv ]; then
    PY="$(find_python)" || {
        echo "ERROR: Python 3.12+ nicht gefunden.  Installieren mit:  brew install python@3.12"
        exit 1
    }
    echo "Erstelle virtuelle Umgebung (.venv) mit $("$PY" --version) ..."
    "$PY" -m venv .venv
    export COPYFILE_DISABLE=1          # no AppleDouble ._* files on non-native volumes
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
    find .venv -name '._*' -delete 2>/dev/null || true
fi

# -- MediaPipe sidecar (separate Python 3.12 venv) -----------------
# Webcam tracking runs out-of-process; without this there is no camera source.
if [ ! -x mediapipe_sidecar/.venv/bin/python3 ] || [ ! -f mediapipe_sidecar/models/hand_landmarker.task ] \
   || [ ! -f mediapipe_sidecar/models/face_landmarker.task ]; then
    echo "Richte MediaPipe-Sidecar ein (einmalig, laedt ~200 MB) ..."
    if ! bash mediapipe_sidecar/setup_sidecar.sh; then
        echo "WARNUNG: Sidecar-Setup fehlgeschlagen - Webcam-Tracking steht nicht zur Verfuegung."
    fi
fi

# -- Leap Motion (opt-in) ------------------------------------------
# Off by default on this branch; capture/__init__.py reads MOTRYX_ENABLE_LEAP.
if [ "$USE_LEAP" = 1 ]; then
    export MOTRYX_ENABLE_LEAP=1
    if [ ! -d leapc_cffi ]; then
        SDK_PATH="/Applications/Ultraleap Hand Tracking.app/Contents/LeapSDK/leapc_cffi"
        if [ -d "$SDK_PATH" ]; then
            echo "Kopiere LeapC-Bindings aus dem SDK ..."
            cp -r "$SDK_PATH" leapc_cffi
        else
            echo "WARNUNG: leapc_cffi nicht gefunden. Ultraleap Tracking installieren:"
            echo "  https://www.ultraleap.com/downloads/leap-controller/"
        fi
    fi
    # The SDK ships one .so per Python version; copy it to the name ours wants.
    if [ -d leapc_cffi ]; then
        PYVER=$(.venv/bin/python -c "import sys; print(f'cpython-{sys.version_info.major}{sys.version_info.minor}')")
        if ! ls leapc_cffi/_leapc_cffi.${PYVER}-darwin.so >/dev/null 2>&1; then
            SOURCE=$(ls leapc_cffi/_leapc_cffi.cpython-*-darwin.so 2>/dev/null | head -1)
            if [ -n "$SOURCE" ]; then
                echo "Kopiere $(basename "$SOURCE") -> _leapc_cffi.${PYVER}-darwin.so"
                cp "$SOURCE" "leapc_cffi/_leapc_cffi.${PYVER}-darwin.so"
            fi
        fi
        export DYLD_LIBRARY_PATH="$(pwd)/leapc_cffi${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
    fi
fi

exec .venv/bin/python main.py "${APP_ARGS[@]}"
