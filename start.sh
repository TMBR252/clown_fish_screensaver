#!/usr/bin/env bash
# Start the tank. Options are passed straight through, e.g. ./start.sh --fps 20
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "The virtual environment is missing. Run ./setup.sh first."
    exit 1
fi

# macOS wants its UI on the main thread, so this runs in the foreground and draws
# every screen from one process. Background it with & if you prefer.
exec .venv/bin/python run.py "$@"
