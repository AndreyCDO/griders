#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/var/www/fastuser/data/cryptorg-trader"
SESSION_NAME="ai_cryptorg_web"
MODE="${1:-restart}"

cd "$APP_DIR"

if [ "$MODE" = "health" ]; then
  if curl -fsS --max-time 3 http://127.0.0.1:8000/ >/dev/null; then
    exit 0
  fi
fi

if screen -list | grep -q "[.]${SESSION_NAME}[[:space:]]"; then
  screen -S "$SESSION_NAME" -X quit || true
  sleep 1
fi

screen -dmS "$SESSION_NAME" bash -lc \
  "cd '$APP_DIR' && ./venv/bin/uvicorn web:app --host 127.0.0.1 --port 8000 >> webapp.log 2>&1"
