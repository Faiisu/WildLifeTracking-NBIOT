#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MONITOR_PORT="${1:-/dev/cu.usbserial-0001}"
MPREMOTE_BIN="${MPREMOTE_BIN:-$HOME/.local/share/esp32-power-monitor-tools/bin/mpremote}"

if [[ ! -x "$MPREMOTE_BIN" ]]; then
    if command -v mpremote >/dev/null 2>&1; then
        MPREMOTE_BIN="$(command -v mpremote)"
    else
        echo "mpremote not found. Install it or set MPREMOTE_BIN to its executable." >&2
        exit 1
    fi
fi

if [[ ! -e "$MONITOR_PORT" ]]; then
    echo "Serial port not found: $MONITOR_PORT" >&2
    "$MPREMOTE_BIN" connect list
    exit 1
fi

echo "Running local MicroPython code on $MONITOR_PORT (no device files copied)."
echo "Keep USB connected. Press Ctrl+C to stop; press board Reset to run its original code."
exec "$MPREMOTE_BIN" connect "$MONITOR_PORT" mount "$APP_DIR" \
    exec "import sys; sys.path.insert(0, '/remote'); import main"
