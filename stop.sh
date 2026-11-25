#!/usr/bin/env bash
# Stop any running tank.
cd "$(dirname "$0")"
if pkill -f "[r]un\.py" 2>/dev/null; then
    echo "stopped."
else
    echo "nothing was running."
fi
