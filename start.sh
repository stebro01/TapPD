#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
export DYLD_LIBRARY_PATH="$(pwd)/leapc_cffi"
python main.py "$@"
