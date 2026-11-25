#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MONITOR_PORT="${1:-/dev/cu.usbserial-0001}"
MONITOR_WEB_PORT="${MONITOR_WEB_PORT:-3100}"
MONITOR_API_PORT="${MONITOR_API_PORT:-3101}"
SQLITE_DB_PATH="${SQLITE_DB_PATH:-$APP_DIR/data/esp32_power_monitor.db}"
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

cd "$APP_DIR"

python3 "$APP_DIR/host/frontend_server.py" --port "$MONITOR_WEB_PORT" \
    --api-base "http://127.0.0.1:$MONITOR_API_PORT" &
FRONTEND_PID=$!
cleanup() {
    kill "$FRONTEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Running local MicroPython code on $MONITOR_PORT (no device files copied)." >&2
echo "Keep USB connected. Press Ctrl+C to stop; press board Reset to run its original code." >&2
echo "Open http://127.0.0.1:$MONITOR_WEB_PORT" >&2
python3 "$APP_DIR/host/serial_monitor.py" --mpremote "$MPREMOTE_BIN" --port "$MONITOR_PORT" \
    --api-port "$MONITOR_API_PORT" \
    --db "$SQLITE_DB_PATH"
