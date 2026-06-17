#!/usr/bin/env bash
# deploy.sh — установка Cryptorg MCP Trader на Ubuntu/Debian VPS
# Запуск: sudo bash deploy.sh
set -euo pipefail

# ── Цвета ─────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
fail() { echo -e "${RED}❌ $*${NC}"; exit 1; }
info() { echo -e "   $*"; }

INSTALL_DIR="/opt/cryptorg-trader"
SERVICE_USER="cryptorg"
PYTHON_MIN="3.11"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  Cryptorg AI Trader — установка на сервер   ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Проверка прав ──────────────────────────────────────────────
[[ $EUID -ne 0 ]] && fail "Запусти с sudo: sudo bash deploy.sh"

# ── Python ────────────────────────────────────────────────────
info "Проверяем Python..."
if ! command -v python3 &>/dev/null; then
    apt-get update -q && apt-get install -y python3 python3-pip python3-venv
fi
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PY_VERSION обнаружен"
python3 -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null || \
    fail "Требуется Python 3.11+. Текущая версия: $PY_VERSION"
ok "Python $PY_VERSION"

# ── Системный пользователь ────────────────────────────────────
info "Создаём пользователя $SERVICE_USER..."
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /bin/false "$SERVICE_USER"
    ok "Пользователь $SERVICE_USER создан"
else
    ok "Пользователь $SERVICE_USER уже существует"
fi

# ── Директория ────────────────────────────────────────────────
info "Создаём директорию $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR/logs"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Копируем файлы проекта
cp -r "$SCRIPT_DIR"/. "$INSTALL_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
ok "Файлы скопированы в $INSTALL_DIR"

# ── Virtual environment ───────────────────────────────────────
info "Создаём виртуальное окружение..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
ok "Зависимости установлены"

# ── .env файл ─────────────────────────────────────────────────
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/.env"
    warn ".env создан из шаблона — заполни API ключи!"
    info "   nano $INSTALL_DIR/.env"
else
    ok ".env уже существует"
fi

# ── systemd сервис ────────────────────────────────────────────
info "Устанавливаем systemd сервис..."
cp "$INSTALL_DIR/cryptorg-trader.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable cryptorg-trader
ok "Сервис зарегистрирован (cryptorg-trader)"

# ── Проверка конфигурации ─────────────────────────────────────
info "Проверяем конфигурацию..."
if grep -q "your_cryptorg_api_key_here" "$INSTALL_DIR/.env" 2>/dev/null; then
    warn "API ключи не заполнены! Заполни .env перед запуском:"
    info "   nano $INSTALL_DIR/.env"
    info "   sudo systemctl start cryptorg-trader"
else
    info "Запускаем сервис..."
    systemctl start cryptorg-trader
    sleep 2
    if systemctl is-active --quiet cryptorg-trader; then
        ok "Сервис запущен"
    else
        warn "Сервис не запустился. Проверь логи:"
        info "   journalctl -u cryptorg-trader -n 50"
    fi
fi

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║           Установка завершена!               ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
info "Полезные команды:"
info "  Статус:  systemctl status cryptorg-trader"
info "  Логи:    journalctl -u cryptorg-trader -f"
info "  Стоп:    systemctl stop cryptorg-trader"
info "  Рестарт: systemctl restart cryptorg-trader"
info "  .env:    nano $INSTALL_DIR/.env"
echo ""
info "Для Claude Desktop добавь в claude_desktop_config.json:"
echo ""
cat << 'JSON'
{
  "mcpServers": {
    "cryptorg-bybit-trader": {
      "command": "/opt/cryptorg-trader/venv/bin/python",
      "args": ["/opt/cryptorg-trader/server.py"]
    }
  }
}
JSON
echo ""
