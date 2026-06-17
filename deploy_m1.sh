#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/var/www/fastuser/data/cryptorg-trader"
SESSION_NAME="griders_m1_cpp"
PORT="${M1_PORT:-8010}"

cd "$APP_DIR/m1"
make

mkdir -p "$APP_DIR/m1_data"
chmod 700 "$APP_DIR/m1_data"

if [[ ! -f "$APP_DIR/.m1_env" ]]; then
  umask 077
  password="$(openssl rand -base64 24 | tr -d '\n')"
  cat > "$APP_DIR/.m1_env" <<EOF
export M1_PORT="$PORT"
export M1_DATA_DIR="$APP_DIR/m1_data"
export M1_ADMIN_USER="admin"
export M1_ADMIN_PASSWORD="$password"
EOF
fi

if screen -list | grep -q "[.]${SESSION_NAME}[[:space:]]"; then
  screen -S "$SESSION_NAME" -X quit || true
  sleep 1
fi

screen -dmS "$SESSION_NAME" bash -lc \
  "cd '$APP_DIR/m1' && source '$APP_DIR/.m1_env' && ./m1_server >> '$APP_DIR/m1.log' 2>&1"

sleep 1
curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null
echo "M1 C++ service is running on 127.0.0.1:${PORT}"
