#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-13000}"

if ! command -v node >/dev/null 2>&1; then
	echo "Node.js (>=22) is required. Install Node.js and run this script again." >&2
	exit 1
fi

cd "$APP_DIR"

if [[ ! -d "$APP_DIR/node_modules" ]]; then
	if ! command -v npm >/dev/null 2>&1; then
		echo "npm is required to install dependencies." >&2
		exit 1
	fi
	echo "Installing dependencies..." >&2
	npm install
fi

echo "Starting GPS Monitoring at http://${HOST}:${PORT}" >&2
exec node "$APP_DIR/server.js"
