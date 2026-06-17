"""Web application settings."""

import os

from dotenv import load_dotenv

load_dotenv()

APP_NAME = os.getenv("WEBAPP_NAME", "Griders")
APP_SECRET = os.getenv("WEBAPP_SECRET", os.getenv("APP_SECRET_KEY", "change-me-now"))
APP_BASE_URL = os.getenv("WEBAPP_BASE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.getenv("WEBAPP_ADMIN_EMAIL", "")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "aicryptorg")
DB_USER = os.getenv("DB_USER", "aicryptorg")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_CHARSET = "utf8mb4"

SESSION_COOKIE = "aicryptorg_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30
SESSION_IDLE_TIMEOUT_SECONDS = int(os.getenv("SESSION_IDLE_TIMEOUT_SECONDS", str(60 * 60 * 4)))

PASSWORD_RESET_TTL_MINUTES = int(os.getenv("PASSWORD_RESET_TTL_MINUTES", "60"))
EMAIL_VERIFICATION_TTL_MINUTES = int(os.getenv("EMAIL_VERIFICATION_TTL_MINUTES", "1440"))

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or f"no-reply@{APP_BASE_URL.replace('https://', '').replace('http://', '').split('/')[0]}")
SMTP_TLS = os.getenv("SMTP_TLS", "true").lower() == "true"

AGENT_INTERVAL_SECONDS = int(os.getenv("AGENT_INTERVAL_SECONDS", "300"))
AGENT_ENABLED = os.getenv("AGENT_ENABLED", "true").lower() == "true"

RECOMMENDED_PAIR_OPTIONS = [
    {"symbol": "BTCUSDT", "name": "Bitcoin"},
    {"symbol": "ETHUSDT", "name": "Ethereum"},
    {"symbol": "SOLUSDT", "name": "Solana"},
    {"symbol": "HYPEUSDT", "name": "Hyperliquid"},
    {"symbol": "NEARUSDT", "name": "NEAR Protocol"},
    {"symbol": "ZECUSDT", "name": "Zcash"},
    {"symbol": "TONUSDT", "name": "Toncoin"},
    {"symbol": "XRPUSDT", "name": "XRP"},
    {"symbol": "SUIUSDT", "name": "Sui"},
    {"symbol": "FILUSDT", "name": "Filecoin"},
    {"symbol": "TAOUSDT", "name": "Bittensor"},
    {"symbol": "RENDERUSDT", "name": "Render"},
    {"symbol": "ADAUSDT", "name": "Cardano"},
    {"symbol": "INJUSDT", "name": "Injective"},
    {"symbol": "LITUSDT", "name": "Litentry"},
    {"symbol": "ENAUSDT", "name": "Ethena"},
    {"symbol": "LINKUSDT", "name": "Chainlink"},
    {"symbol": "AVAXUSDT", "name": "Avalanche"},
    {"symbol": "JUPUSDT", "name": "Jupiter"},
    {"symbol": "ARBUSDT", "name": "Arbitrum"},
]

FREE_PLAN_DISABLED_PAIRS = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
START_PLAN_DISABLED_PAIRS = {"BTCUSDT"}

DEFAULT_WATCHLIST = [
    item.strip().upper()
    for item in os.getenv(
        "DEFAULT_WATCHLIST",
        ",".join(pair["symbol"] for pair in RECOMMENDED_PAIR_OPTIONS),
    ).split(",")
    if item.strip()
]

DEFAULT_RISK_PCT = float(os.getenv("DEFAULT_RISK_PCT", "2.0"))
DEFAULT_MIN_ORDER_VOLUME = float(os.getenv("DEFAULT_MIN_ORDER_VOLUME", "6"))
MIN_FIRST_ORDER_VOLUME = float(os.getenv("MIN_FIRST_ORDER_VOLUME", "6"))
DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "10"))
DEFAULT_STRATEGY_CODE = os.getenv("DEFAULT_STRATEGY_CODE", "grid_dca_v2")
DEFAULT_MAX_ACTIVE_DEALS = int(os.getenv("DEFAULT_MAX_ACTIVE_DEALS", "2"))
DEFAULT_MAX_LONG_DEALS = int(os.getenv("DEFAULT_MAX_LONG_DEALS", "1"))
DEFAULT_MAX_SHORT_DEALS = int(os.getenv("DEFAULT_MAX_SHORT_DEALS", "1"))
TYPICAL_SAFETY_ORDERS = int(os.getenv("TYPICAL_SAFETY_ORDERS", "4"))
TYPICAL_MARTINGALE_MULTIPLIER = float(os.getenv("TYPICAL_MARTINGALE_MULTIPLIER", "1.15"))
RECOMMENDED_DEAL_DEPOSIT_PCT = float(os.getenv("RECOMMENDED_DEAL_DEPOSIT_PCT", "2"))
MAX_STRATEGY_RISK_PCT = float(os.getenv("MAX_STRATEGY_RISK_PCT", "7"))
MANUAL_FIRST_ORDER_MAX_DEPOSIT_PCT = float(os.getenv("MANUAL_FIRST_ORDER_MAX_DEPOSIT_PCT", "8"))

RISK_GUARD_ENABLED = os.getenv("RISK_GUARD_ENABLED", "true").lower() == "true"
STOP_LOSS_COOLDOWN_HOURS = float(os.getenv("STOP_LOSS_COOLDOWN_HOURS", "3"))
DAILY_LOSS_STOP_PCT = float(os.getenv("DAILY_LOSS_STOP_PCT", "2.0"))
DAILY_LOSS_COOLDOWN_HOURS = float(os.getenv("DAILY_LOSS_COOLDOWN_HOURS", "12"))
TP_DCA_CLEANUP_ENABLED = os.getenv("TP_DCA_CLEANUP_ENABLED", "true").lower() == "true"
TP_DCA_CLEANUP_LOOKBACK_MINUTES = int(os.getenv("TP_DCA_CLEANUP_LOOKBACK_MINUTES", "20"))
TP_DCA_CLEANUP_NEW_SIGNAL_GRACE_SECONDS = int(os.getenv("TP_DCA_CLEANUP_NEW_SIGNAL_GRACE_SECONDS", "30"))
PAIR_LAUNCH_COOLDOWN_SECONDS = int(os.getenv("PAIR_LAUNCH_COOLDOWN_SECONDS", "60"))
GRID_DCA_SIDE_WEBHOOK_COOLDOWN_SECONDS = int(os.getenv("GRID_DCA_SIDE_WEBHOOK_COOLDOWN_SECONDS", "300"))
GRID_DCA_LAUNCH_SAFETY_LOOKBACK_MINUTES = int(os.getenv("GRID_DCA_LAUNCH_SAFETY_LOOKBACK_MINUTES", "5"))
ADMIN_STATS_REFRESH_SECONDS = int(os.getenv("ADMIN_STATS_REFRESH_SECONDS", str(4 * 60 * 60)))
ADMIN_STATS_INITIAL_LOOKBACK_DAYS = int(os.getenv("ADMIN_STATS_INITIAL_LOOKBACK_DAYS", "30"))
ADMIN_STATS_SYNC_OVERLAP_MINUTES = int(os.getenv("ADMIN_STATS_SYNC_OVERLAP_MINUTES", "30"))

CRYPTORG_TAKER_FEE_PCT = float(os.getenv("CRYPTORG_TAKER_FEE_PCT", "0.05"))
CRYPTORG_MAKER_FEE_PCT = float(os.getenv("CRYPTORG_MAKER_FEE_PCT", "0.04"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
TELEGRAM_MARKET_SHOCK_CHANNEL = os.getenv("TELEGRAM_MARKET_SHOCK_CHANNEL", "market_shock_bybit")
TELEGRAM_TARIFF_BOT_TOKEN = os.getenv("TELEGRAM_TARIFF_BOT_TOKEN", "")
TELEGRAM_TARIFF_WEBHOOK_SECRET = os.getenv("TELEGRAM_TARIFF_WEBHOOK_SECRET", "")
TELEGRAM_TARIFF_BOT_USERNAME = os.getenv("TELEGRAM_TARIFF_BOT_USERNAME", "griders_tarif_bot")
TELEGRAM_TARIFF_START_CHANNEL_ID = os.getenv("TELEGRAM_TARIFF_START_CHANNEL_ID", "")
TELEGRAM_TARIFF_PREMIUM_CHANNEL_ID = os.getenv("TELEGRAM_TARIFF_PREMIUM_CHANNEL_ID", "")
TELEGRAM_TARIFF_SYNC_INTERVAL_SECONDS = int(os.getenv("TELEGRAM_TARIFF_SYNC_INTERVAL_SECONDS", "3600"))
TRADINGVIEW_WEBHOOK_SECRET = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "")

MARKET_SHOCK_ALLOWED_PAIRS = [
    item.strip().upper()
    for item in os.getenv("MARKET_SHOCK_ALLOWED_PAIRS", ",".join(DEFAULT_WATCHLIST)).split(",")
    if item.strip()
]
MARKET_SHOCK_DENY_PAIRS = [
    item.strip().upper()
    for item in os.getenv(
        "MARKET_SHOCK_DENY_PAIRS",
        "GUAUSDT,HPOS10IUSDT,PLAYSOUTUSDT,FARTCOINUSDT,MELANIAUSDT,USELESSUSDT,JELLYJELLYUSDT,MOODENGUSDT,PNUTUSDT,POPCATUSDT,TRUMPUSDT,DOGUSDT,BABYUSDT",
    ).split(",")
    if item.strip()
]
MARKET_SHOCK_MIN_24H_TURNOVER_USDT = float(os.getenv("MARKET_SHOCK_MIN_24H_TURNOVER_USDT", "8000000"))
MARKET_SHOCK_MAX_SPREAD_PCT = float(os.getenv("MARKET_SHOCK_MAX_SPREAD_PCT", "0.20"))
MARKET_SHOCK_MIN_MOVE_PCT = float(os.getenv("MARKET_SHOCK_MIN_MOVE_PCT", "2.8"))
MARKET_SHOCK_MAX_MOVE_PCT = float(os.getenv("MARKET_SHOCK_MAX_MOVE_PCT", "18.0"))
MARKET_SHOCK_MIN_VOLUME_RATIO = float(os.getenv("MARKET_SHOCK_MIN_VOLUME_RATIO", "1.2"))
MARKET_SHOCK_ALLOW_DUAL_SIDE = os.getenv("MARKET_SHOCK_ALLOW_DUAL_SIDE", "false").lower() == "true"
MARKET_SHOCK_PAIR_STOP_COOLDOWN_HOURS = float(os.getenv("MARKET_SHOCK_PAIR_STOP_COOLDOWN_HOURS", "6"))
MARKET_SHOCK_STRATEGY_STOP_COOLDOWN_HOURS = float(os.getenv("MARKET_SHOCK_STRATEGY_STOP_COOLDOWN_HOURS", "1"))
