#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/var/www/fastuser/data/cryptorg-trader"
SESSION_NAME="ai_cryptorg_web"
MODE="${1:-restart}"
LOCK_DIR="$APP_DIR/.deploy_webapp.lock"
HEALTH_URL="http://127.0.0.1:8000/healthz"
HEALTH_FAIL_FILE="$APP_DIR/.health_failures"
WATCHDOG_MAX_FAILURES="${WATCHDOG_MAX_FAILURES:-5}"
MAINTENANCE_LOG="$APP_DIR/webapp_maintenance.log"

cd "$APP_DIR"

if [ "$MODE" = "health" ]; then
  if curl -fsS --connect-timeout 2 --max-time 15 "$HEALTH_URL" >/dev/null; then
    exit 0
  fi
  exit 1
fi

if [ "$MODE" = "watchdog" ]; then
  if "$0" health >/dev/null 2>&1; then
    rm -f "$HEALTH_FAIL_FILE"
    exit 0
  fi

  failures=0
  if [ -f "$HEALTH_FAIL_FILE" ]; then
    failures="$(cat "$HEALTH_FAIL_FILE" 2>/dev/null || echo 0)"
  fi
  failures=$((failures + 1))
  echo "$failures" > "$HEALTH_FAIL_FILE"

  if [ "$failures" -lt "$WATCHDOG_MAX_FAILURES" ]; then
    exit 1
  fi

  echo "$(date -Is) watchdog restarting app after $failures failed health checks" >> "$APP_DIR/webapp_watchdog.log"
  rm -f "$HEALTH_FAIL_FILE"
  exec "$0" restart
fi

if [ "$MODE" = "maintenance" ]; then
  {
    echo "$(date -Is) maintenance started"
    find "$APP_DIR" \
      \( -path "$APP_DIR/.cache/backtests" -o -path "$APP_DIR/venv" -o -path "$APP_DIR/.backup" -o -path "$APP_DIR/.git" \) -prune \
      -o -type d -name "__pycache__" -print -exec rm -rf {} + 2>/dev/null || true
    find "$APP_DIR" -maxdepth 1 -type f -name ".tmp_*.json" -mtime +7 -print -delete 2>/dev/null || true
    if [ -d "$APP_DIR/.cache" ]; then
      find "$APP_DIR/.cache" -mindepth 1 -maxdepth 1 ! -name "backtests" -mtime +1 -print -exec rm -rf {} + 2>/dev/null || true
    fi
    if [ -f "$APP_DIR/webapp.log" ]; then
      log_size="$(stat -c%s "$APP_DIR/webapp.log" 2>/dev/null || echo 0)"
      if [ "$log_size" -gt 104857600 ]; then
        rotated="$APP_DIR/webapp.log.$(date +%Y%m%d%H%M%S)"
        mv "$APP_DIR/webapp.log" "$rotated"
        gzip -f "$rotated" 2>/dev/null || true
        echo "$(date -Is) rotated webapp.log size=$log_size"
      fi
    fi
    echo "$(date -Is) maintenance restarting app"
  } >> "$MAINTENANCE_LOG" 2>&1
  exec "$0" restart
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Deploy is already running: $LOCK_DIR" >&2
  exit 1
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

screen -ls 2>/dev/null \
  | awk -v name="$SESSION_NAME" '$1 ~ ("\\." name "$") {print $1}' \
  | while read -r session; do
      screen -S "$session" -X quit || true
    done

sleep 2

ps -u "$(id -u)" -o pid=,args= \
  | awk -v app="$APP_DIR" '
      $0 ~ app "/venv/bin/python" && $0 ~ "uvicorn web:app" {print $1}
      $0 ~ app "/venv/bin/uvicorn" && $0 ~ "web:app" {print $1}
    ' \
  | while read -r pid; do
      kill "$pid" 2>/dev/null || true
    done

sleep 1

ps -u "$(id -u)" -o pid=,args= \
  | awk -v app="$APP_DIR" '
      $0 ~ app "/venv/bin/python" && $0 ~ "uvicorn web:app" {print $1}
      $0 ~ app "/venv/bin/uvicorn" && $0 ~ "web:app" {print $1}
    ' \
  | while read -r pid; do
      kill -9 "$pid" 2>/dev/null || true
    done

screen -wipe >/dev/null 2>&1 || true

screen -dmS "$SESSION_NAME" bash -lc \
  "cd '$APP_DIR' && ./venv/bin/uvicorn web:app --host 127.0.0.1 --port 8000 --no-access-log >> webapp.log 2>&1"
