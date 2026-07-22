#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/var/www/fastuser/data/cryptorg-trader"
SESSION_NAME="griders_support_bot"
MODE="${1:-restart}"
LOG_FILE="$APP_DIR/support_bot.log"

cd "$APP_DIR"

if [ "$MODE" = "stop" ]; then
  screen -ls 2>/dev/null \
    | awk -v name="$SESSION_NAME" '$1 ~ ("\\." name "$") {print $1}' \
    | while read -r session; do
        screen -S "$session" -X quit || true
      done
  exit 0
fi

if [ "$MODE" = "status" ]; then
  screen -ls 2>/dev/null | grep -F ".$SESSION_NAME" || true
  exit 0
fi

if [ "$MODE" = "install-autostart" ]; then
  marker="griders_support_bot_autostart"
  job="@reboot cd $APP_DIR && ./deploy_support_bot.sh restart # $marker"
  current="$(crontab -l 2>/dev/null | grep -v "$marker" || true)"
  {
    if [ -n "$current" ]; then
      printf '%s\n' "$current"
    fi
    printf '%s\n' "$job"
  } | crontab -
  echo "Installed crontab @reboot autostart for $SESSION_NAME"
  exit 0
fi

"$0" stop
sleep 1
screen -wipe >/dev/null 2>&1 || true

screen -dmS "$SESSION_NAME" bash -lc \
  "cd '$APP_DIR' && ./venv/bin/python webapp/support_bot.py >> '$LOG_FILE' 2>&1"
