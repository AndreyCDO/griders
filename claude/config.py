"""
config.py — централизованная конфигурация проекта.
Все настройки читаются из переменных окружения / .env файла.
"""

import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()


# ── API ключи ─────────────────────────────────────────────────────────────────

CRYPTORG_API_KEY    = os.getenv("CRYPTORG_API_KEY", "")
CRYPTORG_API_SECRET = os.getenv("CRYPTORG_API_SECRET", "")
CRYPTOPANIC_KEY     = os.getenv("CRYPTOPANIC_API_KEY", "")

# ── Эндпоинты ─────────────────────────────────────────────────────────────────

# Cryptorg Futures API (Binance-совместимый)
CRYPTORG_BASE = os.getenv("CRYPTORG_BASE_URL", "https://api2.cryptorg.net")

# Bybit V5 публичный API — рыночные данные
BYBIT_BASE = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com")

# CryptoPanic — новости
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"

# Fear & Greed
FEAR_GREED_URL = "https://api.alternative.me/fng/"

# ── Торговые параметры по умолчанию ──────────────────────────────────────────

DEFAULT_CATEGORY    = os.getenv("DEFAULT_CATEGORY", "linear")   # linear = USDT-M фьючерсы
DEFAULT_INTERVAL    = os.getenv("DEFAULT_INTERVAL", "1")        # 1m свечи
DEFAULT_LEVERAGE    = int(os.getenv("DEFAULT_LEVERAGE", "1"))

# ── Риск-менеджмент ───────────────────────────────────────────────────────────

RISK_PER_TRADE_PCT   = float(os.getenv("RISK_PER_TRADE_PCT", "1.5"))  # % от баланса
MAX_DAILY_LOSS_PCT   = float(os.getenv("MAX_DAILY_LOSS_PCT", "5.0"))
MAX_OPEN_POSITIONS   = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
STOP_LOSS_PCT        = float(os.getenv("STOP_LOSS_PCT", "0.5"))       # дефолтный SL
TAKE_PROFIT_RR       = float(os.getenv("TAKE_PROFIT_RR", "1.5"))      # Risk/Reward
MAX_SPREAD_PCT       = float(os.getenv("MAX_SPREAD_PCT", "0.05"))      # макс спред для скальпинга

# ── HTTP настройки ────────────────────────────────────────────────────────────

HTTP_TIMEOUT    = float(os.getenv("HTTP_TIMEOUT", "15"))
RECV_WINDOW_MS  = int(os.getenv("RECV_WINDOW_MS", "10000"))

# ── Логирование ───────────────────────────────────────────────────────────────

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE  = os.getenv("LOG_FILE", "cryptorg_mcp.log")


def setup_logging() -> logging.Logger:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if LOG_FILE:
        handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )
    return logging.getLogger("cryptorg-mcp")


def validate_config(log: logging.Logger) -> bool:
    """Проверяет наличие обязательных ключей. Возвращает False если критические ключи отсутствуют."""
    ok = True
    if not CRYPTORG_API_KEY or not CRYPTORG_API_SECRET:
        log.error("❌ CRYPTORG_API_KEY / CRYPTORG_API_SECRET не заданы — торговля невозможна")
        ok = False
    else:
        log.info("✅ Cryptorg API ключи загружены")
    if not CRYPTOPANIC_KEY:
        log.warning("⚠️  CRYPTOPANIC_API_KEY не задан — инструмент get_news недоступен")
    else:
        log.info("✅ CryptoPanic API ключ загружен")
    log.info(f"✅ Bybit V5 Public API: {BYBIT_BASE}")
    log.info(f"✅ Cryptorg API: {CRYPTORG_BASE}")
    log.info(f"📊 Риск на сделку: {RISK_PER_TRADE_PCT}% | Дневной лимит: {MAX_DAILY_LOSS_PCT}%")
    return ok
