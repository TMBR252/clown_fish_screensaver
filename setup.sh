#!/usr/bin/env bash
# One-step setup on macOS (and Linux, as far as it goes).
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "Clownfish wallpaper - setup"
echo "  project: $PWD"
echo

PY=""
for c in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
        v=$("$c" -c 'import sys;print("%d%02d" % sys.version_info[:2])' 2>/dev/null || echo 0)
        if [ "$v" -ge 309 ]; then PY="$c"; break; fi
    fi
done
if [ -z "$PY" ]; then
    echo "No Python 3.9 or newer found. Install it from https://www.python.org/downloads/"
    exit 1
fi
echo "  python : $($PY -c 'import sys;print(sys.executable)')  ($($PY -c 'import platform;print(platform.python_version())'))"

if [ ! -x .venv/bin/python ]; then
    echo "  making a virtual environment in .venv ..."
    "$PY" -m venv .venv
else
    echo "  .venv already there"
fi

echo "  installing dependencies ..."
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

echo
echo "  checking this machine ..."
.venv/bin/python tools/selfcheck.py || true

echo
echo "Start it with:   ./start.sh"
echo "Stop it with:    ./stop.sh   (or Ctrl+C in the window it runs in)"
