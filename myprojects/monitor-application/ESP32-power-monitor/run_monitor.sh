#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MONITOR_PORT="${1:-/dev/cu.usbserial-0001}"
MONITOR_WEB_PORT="${MONITOR_WEB_PORT:-3100}"
MONITOR_API_PORT="${MONITOR_API_PORT:-3101}"
INFLUXDB_URL="${INFLUXDB_URL:-http://127.0.0.1:8181}"
INFLUXDB_DATABASE="${INFLUXDB_DATABASE:-esp32_power_monitor}"
MPREMOTE_BIN="${MPREMOTE_BIN:-$HOME/.local/share/esp32-power-monitor-tools/bin/mpremote}"

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo "Docker Desktop with Docker Compose is required. Install and start Docker, then run this script again." >&2
    exit 1
fi

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
docker compose up -d influxdb

INFLUX_HOST="${INFLUXDB_URL#http://}"
INFLUX_HOST="${INFLUX_HOST%%:*}"
INFLUX_PORT="${INFLUXDB_URL##*:}"
INFLUX_PORT="${INFLUX_PORT%%/*}"
ready=0
for _ in {1..30}; do
    if python3 -c 'import socket,sys; s=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=1); s.close()' "$INFLUX_HOST" "$INFLUX_PORT" 2>/dev/null; then
        ready=1
        break
    fi
    sleep 1
done
if [[ "$ready" != 1 ]]; then
    echo "InfluxDB did not become ready at $INFLUXDB_URL. Check: docker compose logs influxdb" >&2
    exit 1
fi

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
    --influx-url "$INFLUXDB_URL" --database "$INFLUXDB_DATABASE"
