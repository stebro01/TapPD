#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi
source .venv/bin/activate

# Copy leapc_cffi from Ultraleap SDK if not present locally
if [ ! -d leapc_cffi ]; then
    SDK_PATH="/Applications/Ultraleap Hand Tracking.app/Contents/LeapSDK/leapc_cffi"
    if [ -d "$SDK_PATH" ]; then
        echo "Copying LeapC bindings from SDK..."
        cp -r "$SDK_PATH" leapc_cffi
    else
        echo "WARNING: leapc_cffi not found. Install Ultraleap Tracking from:"
        echo "  https://www.ultraleap.com/downloads/leap-controller/"
    fi
fi

# Rename .so for current Python version if needed
if [ -d leapc_cffi ]; then
    PYVER=$(python3 -c "import sys; print(f'cpython-{sys.version_info.major}{sys.version_info.minor}')")
    if ! ls leapc_cffi/_leapc_cffi.${PYVER}-darwin.so &>/dev/null; then
        SOURCE=$(ls leapc_cffi/_leapc_cffi.cpython-*-darwin.so 2>/dev/null | head -1)
        if [ -n "$SOURCE" ]; then
            TARGET="leapc_cffi/_leapc_cffi.${PYVER}-darwin.so"
            echo "Copying $(basename "$SOURCE") -> $(basename "$TARGET")"
            cp "$SOURCE" "$TARGET"
        fi
    fi
fi

export DYLD_LIBRARY_PATH="$(pwd)/leapc_cffi"
python main.py "$@"
