#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/var/www/fastuser/data/cryptorg-trader"
SESSION_NAME="ai_cryptorg_web"

cd "$APP_DIR"

if curl -fsS http://127.0.0.1:8000/ >/dev/null 2>&1; then
  exit 0
fi

if screen -list | grep -q "[.]${SESSION_NAME}[[:space:]]"; then
  screen -S "$SESSION_NAME" -X quit || true
  sleep 1
fi

screen -dmS "$SESSION_NAME" bash -lc \
  "cd '$APP_DIR' && ./venv/bin/uvicorn web:app --host 127.0.0.1 --port 8000 >> webapp.log 2>&1"
