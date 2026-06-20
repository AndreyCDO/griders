"""Responsive web MVP for AI Cryptorg."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import ghost_webhook

from . import settings
from .agent import agent_loop, risk_pause_status, scan_once
from .cryptorg_monitor import closed_pnl_history_page, extract_usdt_balance, positions, wallet_balance
from .db import execute, fetch_all, fetch_one, init_db
from .grid_dca_webhook import handle_tradingview_grid_dca
from .i18n import LANG_COOKIE, normalize_lang, ui
from .launch_guard import release_pair_launch, release_strategy_side_launch, reserve_pair_launch, reserve_strategy_side_launch
from .mailer import send_email_verification, send_password_reset, smtp_configured
from .market_shock import handle_telegram_update
from .tariff_bot import handle_tariff_bot_update, sync_user_tariff, tariff_sync_loop, telegram_verify_url
from .trade_stats import process_closed_rows_for_counter, refresh_recent_daily_site_trade_stats, site_totals, trade_analysis_summary
from .security import (
    decrypt_secret,
    encrypt_secret,
    hash_password,
    hash_reset_token,
    make_reset_token,
    make_session,
    make_pending_2fa,
    make_totp_secret,
    mask_secret,
    parse_pending_2fa,
    parse_session,
    totp_uri,
    verify_totp,
    verify_password,
)
from .strategies import STRATEGIES

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
TWOFA_PENDING_COOKIE = "griders_2fa_pending"
SELECTED_CONNECTION_COOKIE = "griders_connection_id"
DEFAULT_TIMEZONE = "Europe/Moscow"
CONNECTION_VIDEO_RUTUBE_URL = "https://rutube.ru/video/private/40c07f768175b6710b6425150e3b0c86/?p=X1U8JjmvHkPrJIx5tV5VGw"
CONNECTION_VIDEO_YOUTUBE_URL = "https://youtu.be/hLUOPT37f2M"
PREFERRED_TIMEZONES = [
    "Europe/Moscow",
    "Europe/Kaliningrad",
    "Europe/Samara",
    "Asia/Yekaterinburg",
    "Asia/Omsk",
    "Asia/Novosibirsk",
    "Asia/Krasnoyarsk",
    "Asia/Irkutsk",
    "Asia/Yakutsk",
    "Asia/Vladivostok",
    "Asia/Magadan",
    "Asia/Kamchatka",
    "Asia/Dubai",
    "Asia/Tbilisi",
    "Asia/Almaty",
    "Europe/Istanbul",
    "Europe/Berlin",
    "Europe/London",
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "UTC",
]
TRADINGVIEW_QUEUE_MAXSIZE = 500
MARKET_SHOCK_QUEUE_MAXSIZE = 1000
MARKET_SHOCK_QUEUE_TRIM_AT = 500
MARKET_SHOCK_QUEUE_KEEP = 100
MARKET_SHOCK_WORKER_PAUSE_SECONDS = 0.25
MARKET_SHOCK_PROCESSING_ENABLED = False
ADMIN_STATS_BACKGROUND_ENABLED = True
ADMIN_STATS_MANUAL_USER_PAUSE_SECONDS = 3.0
PUBLIC_HTTPS_HOSTS = {"griders.ru", "www.griders.ru"}
tradingview_grid_queue: asyncio.Queue[dict] | None = None
market_shock_queue: asyncio.Queue[dict] | None = None
admin_stats_refresh_task: asyncio.Task | None = None

PLAN_LIMITS = {
    "free": {
        "code": "free",
        "name_ru": "Бесплатный",
        "name_en": "Free",
        "max_active_deals": 4,
        "max_long_deals": 2,
        "max_short_deals": 2,
        "max_first_order": 6.0,
        "recommended_balance": 50.0,
        "can_use_deposit_pct": False,
        "max_risk_pct": settings.MAX_STRATEGY_RISK_PCT,
        "disabled_pairs": settings.FREE_PLAN_DISABLED_PAIRS,
    },
    "start": {
        "code": "start",
        "name_ru": "Старт",
        "name_en": "Start",
        "max_active_deals": 8,
        "max_long_deals": 4,
        "max_short_deals": 4,
        "max_first_order": 60.0,
        "recommended_balance": 500.0,
        "can_use_deposit_pct": True,
        "max_risk_pct": settings.MAX_STRATEGY_RISK_PCT,
        "disabled_pairs": settings.START_PLAN_DISABLED_PAIRS,
    },
    "premium": {
        "code": "premium",
        "name_ru": "Премиум",
        "name_en": "Premium",
        "max_active_deals": 16,
        "max_long_deals": 8,
        "max_short_deals": 8,
        "max_first_order": 600.0,
        "recommended_balance": None,
        "can_use_deposit_pct": True,
        "max_risk_pct": settings.MAX_STRATEGY_RISK_PCT,
        "disabled_pairs": set(),
    },
}
ADMIN_PLAN_LIMITS = {
    **PLAN_LIMITS["premium"],
    "code": "admin",
    "name_ru": "Админ",
    "name_en": "Admin",
    "max_active_deals": 100,
    "max_long_deals": 100,
    "max_short_deals": 100,
    "max_first_order": 100000.0,
    "recommended_balance": None,
    "can_use_deposit_pct": True,
    "max_risk_pct": 15.0,
    "disabled_pairs": set(),
}

app = FastAPI(title=settings.APP_NAME, docs_url="/api/docs", redoc_url="/api/redoc")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.middleware("http")
async def force_https_for_public_domain(request: Request, call_next):
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    scheme = forwarded_proto or request.url.scheme
    if host in PUBLIC_HTTPS_HOSTS and scheme != "https":
        url = request.url.replace(scheme="https")
        response = RedirectResponse(str(url), status_code=301)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
    response = await call_next(request)
    if host in PUBLIC_HTTPS_HOSTS and scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.middleware("http")
async def refresh_idle_session(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in {"/login", "/login/2fa", "/logout"}:
        return response
    token = request.cookies.get(settings.SESSION_COOKIE)
    if not token:
        return response
    uid = parse_session(token)
    if uid:
        response.set_cookie(
            settings.SESSION_COOKIE,
            make_session(uid),
            max_age=settings.SESSION_IDLE_TIMEOUT_SECONDS,
            httponly=True,
            samesite="lax",
        )
    else:
        response.delete_cookie(settings.SESSION_COOKIE)
    return response


@app.on_event("startup")
async def on_startup() -> None:
    global tradingview_grid_queue, market_shock_queue
    init_db()
    tradingview_grid_queue = asyncio.Queue(maxsize=TRADINGVIEW_QUEUE_MAXSIZE)
    market_shock_queue = asyncio.Queue(maxsize=MARKET_SHOCK_QUEUE_MAXSIZE)
    asyncio.create_task(_tradingview_grid_worker())
    if MARKET_SHOCK_PROCESSING_ENABLED:
        asyncio.create_task(_market_shock_worker())
    if settings.AGENT_ENABLED:
        asyncio.create_task(agent_loop())
    if ADMIN_STATS_BACKGROUND_ENABLED:
        asyncio.create_task(_admin_stats_loop())
    asyncio.create_task(_daily_trade_stats_loop())
    asyncio.create_task(tariff_sync_loop())


async def _tradingview_grid_worker() -> None:
    while True:
        if tradingview_grid_queue is None:
            await asyncio.sleep(1)
            continue
        payload = await tradingview_grid_queue.get()
        try:
            await handle_tradingview_grid_dca(payload)
        except Exception:
            logger.exception("TradingView GRID DCA queue item failed")
        finally:
            tradingview_grid_queue.task_done()


async def _market_shock_worker() -> None:
    while True:
        if market_shock_queue is None:
            await asyncio.sleep(1)
            continue
        update = await market_shock_queue.get()
        try:
            await handle_telegram_update(update)
        except Exception:
            logger.exception("Telegram MarketShock queue item failed")
        finally:
            market_shock_queue.task_done()
            await asyncio.sleep(MARKET_SHOCK_WORKER_PAUSE_SECONDS)


def _drop_old_queue_items(queue: asyncio.Queue, keep: int) -> int:
    dropped = 0
    while queue.qsize() > keep:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        queue.task_done()
        dropped += 1
    return dropped


def _trim_market_shock_queue_if_needed() -> int:
    if market_shock_queue is None or market_shock_queue.qsize() < MARKET_SHOCK_QUEUE_TRIM_AT:
        return 0
    dropped = _drop_old_queue_items(market_shock_queue, MARKET_SHOCK_QUEUE_KEEP)
    if dropped:
        logger.warning("Dropped %s stale Telegram MarketShock queue items", dropped)
    return dropped


def current_user(request: Request) -> dict | None:
    uid = parse_session(request.cookies.get(settings.SESSION_COOKIE))
    if not uid:
        return None
    return fetch_one("SELECT * FROM ai_users WHERE id=%s", (uid,))


def require_user(request: Request) -> dict | RedirectResponse:
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    _apply_user_plan_constraints(user)
    return user


def render(request: Request, template: str, context: dict | None = None, status_code: int = 200) -> HTMLResponse:
    lang = _lang(request)
    user = current_user(request)
    data = {
        "request": request,
        "app_name": settings.APP_NAME,
        "user": user,
        "user_access": _user_access(user),
        "user_timezone": _user_timezone_name(user),
        "lang": lang,
        "ui": ui(lang),
    }
    data.update(context or {})
    return templates.TemplateResponse(request, template, data, status_code=status_code)


def _lang(request: Request) -> str:
    return normalize_lang(request.cookies.get(LANG_COOKIE) or request.headers.get("accept-language"))


def _request_country_code(request: Request) -> str:
    for header in ("cf-ipcountry", "x-country-code", "x-geoip-country-code"):
        value = str(request.headers.get(header) or "").strip().upper()
        if len(value) == 2 and value.isalpha():
            return value
    return ""


def _timezone_options() -> list[str]:
    all_names = sorted(available_timezones())
    preferred = [name for name in PREFERRED_TIMEZONES if name in all_names or name == "UTC"]
    return [*preferred, *[name for name in all_names if name not in preferred]]


def _user_timezone_name(user: dict | None) -> str:
    value = str((user or {}).get("timezone") or DEFAULT_TIMEZONE).strip()
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError:
        return DEFAULT_TIMEZONE
    return value


def _user_zone(user: dict | None) -> ZoneInfo:
    return ZoneInfo(_user_timezone_name(user))


def _as_utc_datetime(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_user_datetime(value, user: dict | None, seconds: bool = False) -> str:
    dt = _as_utc_datetime(value).astimezone(_user_zone(user))
    fmt = "%d.%m.%y, %H:%M:%S" if seconds else "%d.%m.%y, %H:%M"
    return dt.strftime(fmt)


def _local_date_from_utc(value, user: dict | None):
    return _as_utc_datetime(value).astimezone(_user_zone(user)).date()


def _t(request: Request, section: str, key: str) -> str:
    return ui(_lang(request))[section][key]


def _normalized_plan(plan: str | None) -> str:
    value = str(plan or "free").strip().lower()
    return value if value in PLAN_LIMITS else "free"


def _plan_limits(user: dict | None) -> dict:
    if str((user or {}).get("role") or "user") == "admin":
        return dict(ADMIN_PLAN_LIMITS)
    return dict(PLAN_LIMITS[_normalized_plan((user or {}).get("plan"))])


def _plan_disabled_pairs(user: dict | None) -> set[str]:
    return {str(item).upper() for item in _plan_limits(user).get("disabled_pairs", set())}


def _manual_first_order_cap(user: dict | None, conn: dict | None) -> float:
    limits = _plan_limits(user)
    if str((user or {}).get("role") or "user") == "admin":
        return float(limits.get("max_first_order") or 100000.0)
    if limits.get("code") == "free" and str((user or {}).get("role") or "user") != "admin":
        return settings.MIN_FIRST_ORDER_VOLUME
    plan_cap = float(limits.get("max_first_order") or settings.MIN_FIRST_ORDER_VOLUME)
    balance = float((conn or {}).get("last_balance") or 0)
    if balance <= 0:
        return plan_cap
    deposit_cap = balance * (settings.MANUAL_FIRST_ORDER_MAX_DEPOSIT_PCT / 100.0)
    return min(plan_cap, deposit_cap)


def _plan_label(plan: str | None, lang: str = "ru") -> str:
    limits = PLAN_LIMITS.get(_normalized_plan(plan), PLAN_LIMITS["free"])
    return limits["name_ru"] if normalize_lang(lang) == "ru" else limits["name_en"]


def _normalize_telegram_username(value: str | None) -> str:
    username = str(value or "").strip().lstrip("@").lower()
    allowed = []
    for ch in username:
        if ch.isalnum() or ch == "_":
            allowed.append(ch)
    return "".join(allowed)[:80]


def _user_access(user: dict | None) -> dict:
    role = str((user or {}).get("role") or "user")
    plan = _normalized_plan((user or {}).get("plan"))
    is_admin = role == "admin"
    limits = _plan_limits(user)
    return {
        "role": role,
        "plan": plan,
        "plan_label": limits["name_ru"],
        "plan_limits": limits,
        "is_admin": is_admin,
        "is_premium": is_admin or plan == "premium",
        "is_start": bool(user) and plan == "start",
        "is_free": bool(user) and plan == "free" and not is_admin,
        "is_paid": bool(user) and plan in {"start", "premium"},
        "can_use_market_shock": is_admin,
    }


def _available_strategies(user: dict | None) -> dict:
    access = _user_access(user)
    if access["is_admin"]:
        return dict(STRATEGIES)
    return {settings.DEFAULT_STRATEGY_CODE: STRATEGIES[settings.DEFAULT_STRATEGY_CODE]}


def _strategy_allowed_for_user(user: dict | None, strategy_code: str) -> bool:
    return strategy_code in _available_strategies(user)


def _can_create_connection(user: dict | None, connections: list[dict] | None = None) -> bool:
    if _user_access(user)["is_admin"]:
        return True
    uid = int((user or {}).get("id") or 0)
    if not uid:
        return False
    existing = connections if connections is not None else _connections(uid)
    return len(existing) == 0


def _is_free_user(user: dict | None) -> bool:
    return bool(_user_access(user)["is_free"])


def _apply_user_plan_constraints(user: dict | None) -> None:
    if not user or _user_access(user)["is_admin"]:
        return
    uid = int(user["id"])
    limits = _plan_limits(user)
    max_first_order = float(limits["max_first_order"])
    execute(
        """
        UPDATE ai_user_connections
        SET strategy_code=%s
        WHERE user_id=%s AND strategy_code<>%s
        """,
        (settings.DEFAULT_STRATEGY_CODE, uid, settings.DEFAULT_STRATEGY_CODE),
    )
    if limits["code"] == "free":
        risk_sql = "risk_pct=%s,"
        risk_params = (settings.DEFAULT_RISK_PCT,)
        min_order_sql = "min_order_volume=%s, first_order_mode='manual',"
        min_order_params = (settings.MIN_FIRST_ORDER_VOLUME,)
        extra_where = "OR risk_pct<>%s OR min_order_volume<>%s OR first_order_mode<>'manual'"
        extra_params = (settings.DEFAULT_RISK_PCT, settings.MIN_FIRST_ORDER_VOLUME)
    else:
        risk_sql = "risk_pct=LEAST(GREATEST(risk_pct, 1.000), %s),"
        risk_params = (float(limits.get("max_risk_pct") or settings.MAX_STRATEGY_RISK_PCT),)
        min_order_sql = "min_order_volume=LEAST(GREATEST(min_order_volume, %s), %s),"
        min_order_params = (settings.MIN_FIRST_ORDER_VOLUME, max_first_order)
        extra_where = "OR risk_pct<1.000 OR risk_pct>%s OR min_order_volume<%s OR min_order_volume>%s"
        extra_params = (float(limits.get("max_risk_pct") or settings.MAX_STRATEGY_RISK_PCT), settings.MIN_FIRST_ORDER_VOLUME, max_first_order)
    execute(
        f"""
        UPDATE ai_user_strategy_settings
        SET strategy_code=%s,
            {risk_sql}
            {min_order_sql}
            leverage=%s,
            max_active_deals=LEAST(GREATEST(max_active_deals, 0), %s),
            max_long_deals=LEAST(GREATEST(max_long_deals, 0), %s),
            max_short_deals=LEAST(GREATEST(max_short_deals, 0), %s)
        WHERE user_id=%s
          AND (
            strategy_code<>%s {extra_where} OR leverage<>%s
            OR max_active_deals<0 OR max_active_deals>%s
            OR max_long_deals<0 OR max_long_deals>%s
            OR max_short_deals<0 OR max_short_deals>%s
          )
        """,
        (
            settings.DEFAULT_STRATEGY_CODE,
            *risk_params,
            *min_order_params,
            10,
            int(limits["max_active_deals"]),
            int(limits["max_long_deals"]),
            int(limits["max_short_deals"]),
            uid,
            settings.DEFAULT_STRATEGY_CODE,
            *extra_params,
            10,
            int(limits["max_active_deals"]),
            int(limits["max_long_deals"]),
            int(limits["max_short_deals"]),
        ),
    )
    _sanitize_user_watchlists(uid, _plan_disabled_pairs(user))


def _sanitize_user_watchlists(user_id: int, disabled_pairs: set[str]) -> None:
    if not disabled_pairs:
        return
    rows = fetch_all("SELECT id, watchlist FROM ai_user_strategy_settings WHERE user_id=%s", (user_id,))
    for row in rows:
        current = _watchlist(row.get("watchlist") or "")
        sanitized = _normalize_pair_selection(current, excluded=disabled_pairs)
        if sanitized != current:
            execute(
                "UPDATE ai_user_strategy_settings SET watchlist=%s WHERE id=%s",
                (",".join(sanitized), int(row["id"])),
            )


def _plan_message(lang: str, key: str) -> str:
    if key == "market":
        return (
            "Для выбранного тарифа сейчас доступна только стратегия GRID DCA."
            if normalize_lang(lang) == "ru"
            else "Only the GRID DCA strategy is currently available on this plan."
        )
    if key == "free_limits":
        return (
            "Ограничение бесплатного использования."
            if normalize_lang(lang) == "ru"
            else "Free plan limitation."
        )
    return ""


def _profile_context(user: dict, success: str = "", error: str = "", totp_setup: dict | None = None) -> dict:
    profile_user = dict(user)
    profile_user["timezone"] = _user_timezone_name(user)
    profile_user["created_at_display"] = _format_user_datetime(user.get("created_at"), user)
    profile_user["twofa_enabled"] = _twofa_enabled(user)
    return {
        "profile_user": profile_user,
        "success": success,
        "error": error,
        "totp_setup": totp_setup,
        "telegram_verify_url": telegram_verify_url(int(user["id"])) if profile_user.get("telegram_username") else "",
        "timezone_options": _timezone_options(),
        "admin_users": _admin_user_views(user),
        "admin_site_stats": _admin_site_stats(user),
        "admin_trade_analysis": _admin_trade_analysis(user),
    }


def _request_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded[:64]
    return (request.client.host if request.client else "")[:64]


def _registration_challenge() -> dict:
    left = secrets.randbelow(8) + 2
    right = secrets.randbelow(8) + 2
    issued_at = int(time.time())
    payload = json.dumps(
        {"a": left, "b": right, "answer": left + right, "iat": issued_at, "nonce": secrets.token_hex(8)},
        separators=(",", ":"),
    )
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    signature = hmac.new(settings.APP_SECRET.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return {
        "question": f"{left} + {right}",
        "token": f"{encoded}.{signature}",
        "started_at": str(issued_at),
    }


def _registration_context(request: Request, error: str = "", email: str = "") -> dict:
    return {"error": error, "email": email, "registration_challenge": _registration_challenge()}


def _parse_registration_token(token: str | None, max_age: int = 1800) -> dict | None:
    if not token or "." not in token:
        return None
    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(settings.APP_SECRET.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(encoded.encode()).decode())
    except Exception:
        return None
    if int(time.time()) - int(data.get("iat", 0)) > max_age:
        return None
    return data


def _record_registration_attempt(request: Request, email: str, success: bool, reason: str) -> None:
    execute(
        """
        INSERT INTO ai_registration_attempts (email, ip_address, user_agent, success, reason)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            email[:190],
            _request_ip(request),
            (request.headers.get("user-agent") or "")[:255],
            1 if success else 0,
            reason[:80],
        ),
    )


def _registration_rate_limit_reason(request: Request, email: str) -> str:
    ip = _request_ip(request)
    ip_row = fetch_one(
        """
        SELECT COUNT(*) AS attempts
        FROM ai_registration_attempts
        WHERE ip_address=%s AND created_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
        """,
        (ip,),
    ) or {}
    if int(ip_row.get("attempts") or 0) >= 8:
        return "ip_rate"
    email_row = fetch_one(
        """
        SELECT COUNT(*) AS attempts
        FROM ai_registration_attempts
        WHERE email=%s AND created_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
        """,
        (email,),
    ) or {}
    if int(email_row.get("attempts") or 0) >= 3:
        return "email_rate"
    return ""


def _registration_bot_error(request: Request) -> str:
    return (
        "Не удалось подтвердить, что регистрацию выполняет человек. Обновите страницу и попробуйте ещё раз."
        if _lang(request) == "ru"
        else "We could not verify that a human is registering. Refresh the page and try again."
    )


def _admin_site_stats(user: dict) -> dict:
    if not _user_access(user)["is_admin"]:
        return {}
    totals = site_totals()
    return {
        "users_count": totals["users_count"],
        "deals_count": totals["deals_count"],
        "traded_volume_raw": totals["traded_volume"],
        "traded_volume": _fmt_money(totals["traded_volume"]),
        "counted_from": "08.06.2026",
    }


def _admin_trade_analysis(user: dict) -> list[dict]:
    if not _user_access(user)["is_admin"]:
        return []
    rows = []
    for row in trade_analysis_summary():
        rows.append({
            **row,
            "total_pnl_text": _fmt_money(row["total_pnl"]),
            "avg_pnl_text": _fmt_money(row["avg_pnl"]),
            "win_rate_text": _fmt_percent(row["win_rate"]),
            "avg_roi_text": _fmt_percent(row["avg_roi_pct"]),
            "avg_r_text": f"{row['avg_r_multiple']:.2f}",
            "avg_hold_text": _fmt_duration(row["avg_hold_seconds"]),
            "pnl_class": "positive" if row["total_pnl"] > 0 else ("negative" if row["total_pnl"] < 0 else ""),
        })
    return rows


def _admin_user_views(user: dict) -> list[dict]:
    if not _user_access(user)["is_admin"]:
        return []
    rows = fetch_all(
        """
        SELECT u.id, u.email, u.nickname, u.telegram_username, u.telegram_user_id, u.role, u.plan, u.created_at,
               COALESCE(s.cumulative_pnl, 0) AS cumulative_pnl,
               COALESCE(s.closed_trades_count, 0) AS closed_trades_count,
               COALESCE(s.closed_entry_volume, 0) AS closed_entry_volume,
               COALESCE(s.connection_status, 'missing') AS connection_status,
               s.pnl_calculated_at, s.status_checked_at
        FROM ai_users u
        LEFT JOIN ai_user_admin_stats s ON s.user_id = u.id
        ORDER BY u.created_at ASC, u.id ASC
        """
    )
    result = []
    for row in rows:
        status = row.get("connection_status") or "missing"
        pnl = _float(row.get("cumulative_pnl"))
        result.append({
            **row,
            "created_at_display": _format_user_datetime(row.get("created_at"), user),
            "plan": _normalized_plan(row.get("plan")),
            "plan_label": _plan_label(row.get("plan"), "ru"),
            "role": row.get("role") or "user",
            "connection_status": status,
            "connection_status_class": {
                "active": "admin-email-active",
                "ready": "admin-email-ready",
                "missing": "admin-email-missing",
            }.get(status, "admin-email-missing"),
            "connection_status_label": {
                "active": "Автоторговля активна",
                "ready": "API и webhook подключены, автоторговля выключена",
                "missing": "Нет рабочего подключения",
            }.get(status, "Нет рабочего подключения"),
            "cumulative_pnl_raw": pnl,
            "cumulative_pnl": _fmt_money(pnl),
            "pnl_class": "positive" if pnl > 0 else ("negative" if pnl < 0 else ""),
        })
    return result


async def _refresh_admin_user_stats(
    force: bool = False,
    pause_seconds: float = 0.0,
    include_pnl_history: bool = True,
) -> None:
    refresh_seconds = max(60, int(settings.ADMIN_STATS_REFRESH_SECONDS))
    where_clause = "" if force else f"""
        WHERE s.user_id IS NULL
           OR s.status_checked_at IS NULL
           OR TIMESTAMPDIFF(SECOND, s.status_checked_at, UTC_TIMESTAMP()) >= {refresh_seconds}
           OR EXISTS (
                SELECT 1
                FROM ai_site_trade_deals d
                WHERE d.user_id = u.id
                  AND d.status = 'open'
                  AND d.sent_at <= UTC_TIMESTAMP() - INTERVAL 2 MINUTE
           )
    """
    rows = fetch_all(
        f"""
        SELECT u.id, u.created_at, s.pnl_calculated_at, s.status_checked_at
        FROM ai_users u
        LEFT JOIN ai_user_admin_stats s ON s.user_id = u.id
        {where_clause}
        ORDER BY u.id
        """
    )
    for index, row in enumerate(rows):
        try:
            await _refresh_one_admin_user_stats(row, include_pnl_history=include_pnl_history)
        except Exception as exc:
            logger.warning("admin user stats refresh failed for user %s: %s", row.get("id"), exc)
            execute(
                """
                INSERT INTO ai_user_admin_stats (user_id, connection_status, status_checked_at)
                VALUES (%s, 'missing', UTC_TIMESTAMP())
                ON DUPLICATE KEY UPDATE status_checked_at=UTC_TIMESTAMP()
                """,
                (int(row["id"]),),
            )
        if pause_seconds > 0 and index < len(rows) - 1:
            await asyncio.sleep(pause_seconds)


def _admin_stats_refresh_running() -> bool:
    return admin_stats_refresh_task is not None and not admin_stats_refresh_task.done()


def _start_admin_stats_refresh(force: bool = True, include_pnl_history: bool = True) -> bool:
    global admin_stats_refresh_task
    if _admin_stats_refresh_running():
        return False
    admin_stats_refresh_task = asyncio.create_task(
        _run_admin_stats_refresh(force=force, include_pnl_history=include_pnl_history)
    )
    return True


async def _run_admin_stats_refresh(force: bool = True, include_pnl_history: bool = True) -> None:
    try:
        await _refresh_admin_user_stats(
            force=force,
            pause_seconds=ADMIN_STATS_MANUAL_USER_PAUSE_SECONDS,
            include_pnl_history=include_pnl_history,
        )
    except Exception:
        logger.exception("manual admin stats refresh failed")


async def _admin_stats_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await _refresh_admin_user_stats()
        except Exception as exc:
            logger.warning("admin stats loop failed: %s", exc)
        await asyncio.sleep(max(300, int(settings.ADMIN_STATS_REFRESH_SECONDS)))


async def _daily_trade_stats_loop() -> None:
    await asyncio.sleep(45)
    while True:
        try:
            refresh_recent_daily_site_trade_stats()
        except Exception as exc:
            logger.warning("daily trade stats refresh failed: %s", exc)
        await asyncio.sleep(3600)


async def _refresh_one_admin_user_stats(user_row: dict, include_pnl_history: bool = True) -> None:
    user_id = int(user_row["id"])
    end_dt = datetime.now(timezone.utc)
    existing_stats = fetch_one(
        """
        SELECT cumulative_pnl, closed_trades_count, closed_entry_volume, pnl_calculated_at
        FROM ai_user_admin_stats
        WHERE user_id=%s
        """,
        (user_id,),
    ) or {}
    connections = fetch_all(
        """
        SELECT c.id, c.bybit_api_key, c.bybit_api_secret_encrypted, c.webhook_url_encrypted,
               c.last_admin_closed_sync_at,
               COALESCE(s.enabled, 0) AS strategy_enabled,
               COALESCE(s.auto_trade, 0) AS auto_trade
        FROM ai_user_connections c
        LEFT JOIN ai_user_strategy_settings s ON s.connection_id = c.id
        WHERE c.user_id=%s AND c.is_active=1
        ORDER BY c.id
        """,
        (user_id,),
    )
    cumulative_pnl = _float(existing_stats.get("cumulative_pnl"))
    closed_trades_count = int(existing_stats.get("closed_trades_count") or 0)
    closed_entry_volume = _float(existing_stats.get("closed_entry_volume"))
    has_working_connection = False
    has_active_autotrade = False
    seen_pnl_api_keys: set[str] = set()
    for conn in connections:
        api_key = conn.get("bybit_api_key") or ""
        api_secret_encrypted = conn.get("bybit_api_secret_encrypted") or ""
        webhook_url = decrypt_secret(conn.get("webhook_url_encrypted"))
        if not api_key or not api_secret_encrypted or not webhook_url:
            continue
        api_secret = decrypt_secret(api_secret_encrypted)
        if not api_secret:
            continue
        try:
            wallet = await wallet_balance(api_key, api_secret)
            balance = extract_usdt_balance(wallet)
            execute(
                "UPDATE ai_user_connections SET last_balance=%s, last_error=NULL, last_checked_at=NOW() WHERE id=%s",
                (balance, int(conn["id"])),
            )
        except Exception as exc:
            execute(
                "UPDATE ai_user_connections SET last_error=%s, last_checked_at=NOW() WHERE id=%s",
                (str(exc), int(conn["id"])),
            )
            continue

        has_working_connection = True
        if balance > 0 and int(conn.get("strategy_enabled") or 0) == 1 and int(conn.get("auto_trade") or 0) == 1:
            has_active_autotrade = True

        if include_pnl_history:
            try:
                api_key_marker = api_key.strip()
                credit_admin = bool(api_key_marker and api_key_marker not in seen_pnl_api_keys)
                if api_key_marker:
                    seen_pnl_api_keys.add(api_key_marker)
                delta = await _sync_connection_closed_pnl(
                    user_row,
                    existing_stats,
                    conn,
                    api_key,
                    api_secret,
                    end_dt,
                    credit_admin=credit_admin,
                )
                cumulative_pnl += delta["pnl"]
                closed_trades_count += delta["trades"]
                closed_entry_volume += delta["entry_volume"]
            except Exception as exc:
                logger.warning("admin pnl refresh failed for user %s connection %s: %s", user_id, conn.get("id"), exc)

    status = "active" if has_active_autotrade else ("ready" if has_working_connection else "missing")
    pnl_calculated_at = datetime.now(timezone.utc) if include_pnl_history else existing_stats.get("pnl_calculated_at")
    execute(
        """
        INSERT INTO ai_user_admin_stats
        (user_id, cumulative_pnl, closed_trades_count, closed_entry_volume, connection_status, pnl_calculated_at, status_checked_at)
        VALUES (%s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE
            cumulative_pnl=VALUES(cumulative_pnl),
            closed_trades_count=VALUES(closed_trades_count),
            closed_entry_volume=VALUES(closed_entry_volume),
            connection_status=VALUES(connection_status),
            pnl_calculated_at=VALUES(pnl_calculated_at),
            status_checked_at=VALUES(status_checked_at)
        """,
        (user_id, cumulative_pnl, closed_trades_count, closed_entry_volume, status, pnl_calculated_at),
    )


async def _sync_connection_closed_pnl(
    user_row: dict,
    existing_stats: dict,
    conn: dict,
    api_key: str,
    api_secret: str,
    end_dt: datetime,
    credit_admin: bool = True,
) -> dict:
    user_id = int(user_row["id"])
    connection_id = int(conn["id"])
    baseline_dt = _admin_sync_baseline(user_row, existing_stats, conn, end_dt)
    overlap_minutes = max(0, int(settings.ADMIN_STATS_SYNC_OVERLAP_MINUTES))
    start_dt = baseline_dt - timedelta(minutes=overlap_minutes)
    closed_rows = await _closed_pnl_period(
        api_key,
        api_secret,
        int(start_dt.timestamp() * 1000),
        int(end_dt.timestamp() * 1000),
    )
    process_closed_rows_for_counter(user_id, connection_id, closed_rows)

    pnl_delta = 0.0
    trades_delta = 0
    entry_delta = 0.0
    for row in closed_rows:
        closed_ref = _admin_closed_ref(row)
        closed_at_dt = _admin_closed_at(row)
        if not closed_ref or closed_at_dt is None:
            continue
        pnl = _float(row.get("closedPnl"))
        entry_value = _closed_entry_value(row)
        inserted = execute(
            """
            INSERT IGNORE INTO ai_admin_closed_pnl_rows
                (user_id, connection_id, closed_ref, symbol, side, closed_at, closed_pnl, entry_volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                connection_id,
                closed_ref,
                str(row.get("symbol") or "").upper(),
                _admin_closed_side(row),
                closed_at_dt.strftime("%Y-%m-%d %H:%M:%S"),
                pnl,
                entry_value,
            ),
        )
        if credit_admin and inserted and closed_at_dt > baseline_dt:
            pnl_delta += pnl
            trades_delta += 1
            entry_delta += entry_value
            _add_user_daily_trade_stats(user_id, closed_at_dt, pnl, entry_value)

    execute(
        "UPDATE ai_user_connections SET last_admin_closed_sync_at=%s WHERE id=%s",
        (end_dt.strftime("%Y-%m-%d %H:%M:%S"), connection_id),
    )
    return {"pnl": pnl_delta, "trades": trades_delta, "entry_volume": entry_delta}


def _admin_sync_baseline(user_row: dict, existing_stats: dict, conn: dict, end_dt: datetime) -> datetime:
    for value in (conn.get("last_admin_closed_sync_at"), existing_stats.get("pnl_calculated_at")):
        if value:
            return _as_utc_datetime(value)
    if _float(existing_stats.get("cumulative_pnl")) or int(existing_stats.get("closed_trades_count") or 0):
        return end_dt
    created_at = _as_utc_datetime(user_row.get("created_at"))
    initial_start = end_dt - timedelta(days=max(1, int(settings.ADMIN_STATS_INITIAL_LOOKBACK_DAYS)))
    return max(created_at, initial_start)


def _admin_closed_ref(row: dict) -> str:
    symbol = str(row.get("symbol") or "").upper()
    order_id = str(row.get("orderId") or row.get("execId") or "")
    updated = str(row.get("updatedTime") or row.get("createdTime") or "")
    return f"{symbol}:{order_id}:{updated}" if symbol and updated else ""


def _admin_closed_at(row: dict) -> datetime | None:
    ms = _float(row.get("updatedTime") or row.get("createdTime"))
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _admin_closed_side(row: dict) -> str:
    raw_side = str(row.get("side") or "").lower()
    return "short" if raw_side == "buy" else ("long" if raw_side == "sell" else "")


def _add_user_daily_trade_stats(user_id: int, closed_at: datetime, pnl: float, entry_value: float) -> None:
    execute(
        """
        INSERT INTO ai_user_trade_daily_stats
            (user_id, stat_date, closed_trades_count, closed_pnl, entry_volume, calculated_at)
        VALUES (%s, %s, 1, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            closed_trades_count=closed_trades_count+1,
            closed_pnl=closed_pnl+VALUES(closed_pnl),
            entry_volume=entry_volume+VALUES(entry_volume),
            calculated_at=NOW()
        """,
        (user_id, closed_at.astimezone(timezone.utc).date().isoformat(), pnl, entry_value),
    )

@app.get("/language/{lang}")
async def set_language(request: Request, lang: str):
    selected = normalize_lang(lang)
    referer = request.headers.get("referer") or "/"
    parsed = urlparse(referer)
    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(LANG_COOKIE, selected, max_age=settings.SESSION_MAX_AGE, httponly=False, samesite="lax")
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "landing.html")


@app.get("/docs", response_class=HTMLResponse)
async def docs(request: Request):
    return render(request, "docs.html")


@app.get("/tariffs", response_class=HTMLResponse)
async def tariffs(request: Request):
    return render(request, "tariffs.html", {"tariffs": PLAN_LIMITS})


@app.get("/connection-video")
async def connection_video(request: Request):
    country = _request_country_code(request)
    target = CONNECTION_VIDEO_YOUTUBE_URL if country and country not in {"RU", "BY"} else CONNECTION_VIDEO_RUTUBE_URL
    return RedirectResponse(target, status_code=302)


@app.get("/terms", response_class=HTMLResponse)
async def terms(request: Request):
    return render(request, "terms.html")


@app.get("/personal-data-consent", response_class=HTMLResponse)
async def personal_data_consent(request: Request):
    return render(request, "personal_data_consent.html")


@app.post("/integrations/telegram/market-shock/{secret}", include_in_schema=False)
async def telegram_market_shock(secret: str, request: Request):
    if not settings.TELEGRAM_WEBHOOK_SECRET or secret != settings.TELEGRAM_WEBHOOK_SECRET:
        return Response(status_code=404)
    if not MARKET_SHOCK_PROCESSING_ENABLED:
        return {"ok": True, "processed": False, "reason": "MarketShock processing is temporarily disabled"}
    update = await request.json()
    if market_shock_queue is None:
        asyncio.create_task(handle_telegram_update(update))
        return {"ok": True, "queued": True, "fallback": "task"}
    dropped = _trim_market_shock_queue_if_needed()
    try:
        market_shock_queue.put_nowait(update)
    except asyncio.QueueFull:
        logger.warning("Telegram MarketShock queue is full")
        dropped += _drop_old_queue_items(market_shock_queue, MARKET_SHOCK_QUEUE_KEEP)
        market_shock_queue.put_nowait(update)
    return {"ok": True, "queued": True, "dropped_stale": dropped}


@app.post("/integrations/telegram/tariffs/{secret}", include_in_schema=False)
async def telegram_tariffs(secret: str, request: Request):
    if not settings.TELEGRAM_TARIFF_WEBHOOK_SECRET or secret != settings.TELEGRAM_TARIFF_WEBHOOK_SECRET:
        return Response(status_code=404)
    update = await request.json()
    return await handle_tariff_bot_update(update)


@app.post("/integrations/tradingview/grid-dca/{secret}", include_in_schema=False)
async def tradingview_grid_dca(secret: str, request: Request):
    if not settings.TRADINGVIEW_WEBHOOK_SECRET or secret != settings.TRADINGVIEW_WEBHOOK_SECRET:
        return Response(status_code=404)
    payload = await request.json()
    if tradingview_grid_queue is None:
        asyncio.create_task(handle_tradingview_grid_dca(payload))
        return {"ok": True, "queued": True, "fallback": "task"}
    try:
        tradingview_grid_queue.put_nowait(payload)
    except asyncio.QueueFull:
        logger.warning("TradingView GRID DCA queue is full")
        return Response(status_code=503)
    return {"ok": True, "queued": True}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(BASE_DIR / "static" / "favicon" / "favicon.ico", media_type="image/x-icon")


@app.get("/site.webmanifest", include_in_schema=False)
async def webmanifest():
    return FileResponse(BASE_DIR / "static" / "favicon" / "site.webmanifest", media_type="application/manifest+json")


@app.get("/yandex_ca2df78873266bd8.html", include_in_schema=False)
async def yandex_webmaster_verification():
    return FileResponse(
        BASE_DIR / "static" / "verification" / "yandex_ca2df78873266bd8.html",
        media_type="text/html; charset=utf-8",
    )


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /dashboard",
        "Disallow: /connections",
        "Disallow: /strategies",
        "Disallow: /signals",
        "Disallow: /monitoring",
        "Disallow: /profile",
        "Disallow: /password",
        "Sitemap: https://griders.ru/sitemap.xml",
        "",
    ])
    return PlainTextResponse(body)


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    urls = [
        ("https://griders.ru/", "daily", "1.0"),
        ("https://griders.ru/tariffs", "weekly", "0.8"),
        ("https://griders.ru/docs", "weekly", "0.8"),
        ("https://griders.ru/login", "monthly", "0.3"),
        ("https://griders.ru/register", "monthly", "0.5"),
    ]
    items = "\n".join(
        f"  <url><loc>{loc}</loc><changefreq>{changefreq}</changefreq><priority>{priority}</priority></url>"
        for loc, changefreq, priority in urls
    )
    body = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{items}\n</urlset>\n'
    return Response(content=body, media_type="application/xml")


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return render(request, "register.html", _registration_context(request))


@app.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    personal_data_consent: str | None = Form(None),
    terms_acceptance: str | None = Form(None),
    registration_token: str = Form(""),
    registration_answer: str = Form(""),
    registration_started_at: str = Form(""),
    company_website: str = Form(""),
):
    email = email.strip().lower()
    challenge = _parse_registration_token(registration_token)
    reason = _registration_rate_limit_reason(request, email)
    if reason:
        _record_registration_attempt(request, email, False, reason)
        return render(request, "register.html", _registration_context(request, _registration_bot_error(request), email), 429)
    try:
        started_at = int(registration_started_at or "0")
    except ValueError:
        started_at = 0
    elapsed = int(time.time()) - started_at
    if company_website.strip() or not challenge or elapsed < 3:
        _record_registration_attempt(request, email, False, "bot_check")
        return render(request, "register.html", _registration_context(request, _registration_bot_error(request), email), 422)
    if "".join(ch for ch in registration_answer if ch.isdigit()) != str(challenge.get("answer")):
        _record_registration_attempt(request, email, False, "captcha")
        return render(request, "register.html", _registration_context(request, _registration_bot_error(request), email), 422)
    if len(password) < 8:
        _record_registration_attempt(request, email, False, "password_short")
        return render(request, "register.html", _registration_context(request, _t(request, "auth", "password_short"), email), 422)
    if personal_data_consent != "yes" or terms_acceptance != "yes":
        error = (
            "Для регистрации нужно дать согласие на обработку персональных данных и принять пользовательское соглашение."
            if _lang(request) == "ru"
            else "To register, you must consent to personal data processing and accept the user agreement."
        )
        _record_registration_attempt(request, email, False, "legal")
        return render(request, "register.html", _registration_context(request, error, email), 422)
    if fetch_one("SELECT id FROM ai_users WHERE email=%s", (email,)):
        _record_registration_attempt(request, email, False, "email_exists")
        return render(request, "register.html", _registration_context(request, _t(request, "auth", "email_exists"), email), 422)
    user_id = execute(
        """
        INSERT INTO ai_users
        (email, password_hash, role, plan, personal_data_consent_at, terms_accepted_at)
        VALUES (%s, %s, 'user', 'free', NOW(), NOW())
        """,
        (email, hash_password(password)),
    )
    _record_registration_attempt(request, email, True, "created")
    _ensure_strategy_defaults(user_id)
    sent = _create_email_verification(request, user_id, email)
    target = "/login?verify_sent=1" if sent else "/login?verify_warning=1"
    return RedirectResponse(target, status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    success = _t(request, "auth", "reset_complete") if request.query_params.get("reset") == "1" else None
    if request.query_params.get("verify_sent") == "1":
        success = "Регистрация создана. Мы отправили ссылку подтверждения на вашу почту."
    elif request.query_params.get("verified") == "1":
        success = "Почта подтверждена. Теперь можно войти."
    warning = None
    if request.query_params.get("verify_warning") == "1":
        warning = "Регистрация создана, но письмо подтверждения не удалось отправить. Попробуйте войти позже или обратитесь в поддержку."
    return render(request, "login.html", {"success": success, "warning": warning})


@app.get("/email/verify", response_class=HTMLResponse)
async def verify_email(request: Request, token: str = ""):
    row = _email_verification_row(token)
    if not row:
        return render(request, "login.html", {"error": "Ссылка подтверждения недействительна."}, 422)
    if row.get("is_expired"):
        return render(request, "login.html", {"error": "Срок действия ссылки подтверждения истёк. Войдите в аккаунт, чтобы получить новую ссылку."}, 422)
    execute("UPDATE ai_users SET email_verified_at=COALESCE(email_verified_at, NOW()) WHERE id=%s", (int(row["user_id"]),))
    execute("UPDATE ai_email_verifications SET used_at=NOW() WHERE id=%s", (int(row["id"]),))
    return RedirectResponse("/login?verified=1", status_code=303)


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = fetch_one("SELECT * FROM ai_users WHERE email=%s", (email.strip().lower(),))
    if not user or not verify_password(password, user["password_hash"]):
        return render(request, "login.html", {"error": _t(request, "auth", "invalid_login")}, 422)
    if not user.get("email_verified_at"):
        sent = _create_email_verification(request, int(user["id"]), user["email"])
        warning = "Мы отправили новую ссылку подтверждения на вашу почту." if sent else None
        return render(
            request,
            "login.html",
            {
                "error": "Сначала подтвердите регистрацию по ссылке из письма.",
                "warning": warning,
            },
            422,
        )
    _ensure_strategy_defaults(int(user["id"]))
    if _twofa_enabled(user):
        response = RedirectResponse("/login/2fa", status_code=303)
        response.set_cookie(TWOFA_PENDING_COOKIE, make_pending_2fa(int(user["id"])), max_age=600, httponly=True, samesite="lax")
        return response
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(settings.SESSION_COOKIE, make_session(int(user["id"])), max_age=settings.SESSION_IDLE_TIMEOUT_SECONDS, httponly=True, samesite="lax")
    return response


@app.get("/login/2fa", response_class=HTMLResponse)
async def login_2fa_page(request: Request):
    uid = parse_pending_2fa(request.cookies.get(TWOFA_PENDING_COOKIE))
    if not uid:
        return RedirectResponse("/login", status_code=303)
    user = fetch_one("SELECT * FROM ai_users WHERE id=%s", (uid,))
    if not user or not _twofa_enabled(user):
        return RedirectResponse("/login", status_code=303)
    return render(request, "login_2fa.html", {"method": user.get("twofa_method")})


@app.post("/login/2fa")
async def login_2fa(request: Request, code: str = Form(...)):
    uid = parse_pending_2fa(request.cookies.get(TWOFA_PENDING_COOKIE))
    if not uid:
        return RedirectResponse("/login", status_code=303)
    user = fetch_one("SELECT * FROM ai_users WHERE id=%s", (uid,))
    if not user or not _twofa_enabled(user):
        return RedirectResponse("/login", status_code=303)
    if not _verify_user_2fa(user, code):
        return render(request, "login_2fa.html", {"method": user.get("twofa_method"), "error": "Неверный код подтверждения."}, 422)
    _ensure_strategy_defaults(int(user["id"]))
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(settings.SESSION_COOKIE, make_session(int(user["id"])), max_age=settings.SESSION_IDLE_TIMEOUT_SECONDS, httponly=True, samesite="lax")
    response.delete_cookie(TWOFA_PENDING_COOKIE)
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(settings.SESSION_COOKIE)
    response.delete_cookie(TWOFA_PENDING_COOKIE)
    return response


@app.get("/password/forgot", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return render(request, "forgot_password.html")


@app.post("/password/forgot", response_class=HTMLResponse)
async def forgot_password(request: Request, email: str = Form(...)):
    email = email.strip().lower()
    warning = None
    user = fetch_one("SELECT id, email FROM ai_users WHERE email=%s", (email,))
    if user:
        token = make_reset_token()
        token_hash = hash_reset_token(token)
        execute(
            """
            INSERT INTO ai_password_resets (user_id, token_hash, expires_at, request_ip, user_agent)
            VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL %s MINUTE), %s, %s)
            """,
            (
                int(user["id"]),
                token_hash,
                settings.PASSWORD_RESET_TTL_MINUTES,
                request.client.host if request.client else "",
                (request.headers.get("user-agent") or "")[:255],
            ),
        )
        reset_url = f"{settings.APP_BASE_URL.rstrip('/')}/password/reset?token={token}"
        if not smtp_configured():
            warning = _t(request, "auth", "reset_email_not_configured")
        else:
            try:
                sent = send_password_reset(email, reset_url, _lang(request))
            except Exception:
                logger.exception("Failed to send password reset email")
                sent = False
            if not sent:
                warning = _t(request, "auth", "reset_email_failed")
    return render(
        request,
        "forgot_password.html",
        {"success": _t(request, "auth", "reset_sent"), "warning": warning},
    )


@app.get("/password/reset", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = ""):
    row = _password_reset_row(token)
    if not row:
        return render(request, "forgot_password.html", {"error": _t(request, "auth", "reset_invalid")}, 422)
    if row.get("is_expired"):
        return render(request, "forgot_password.html", {"error": _t(request, "auth", "reset_expired")}, 422)
    return render(request, "reset_password.html", {"token": token})


@app.post("/password/reset")
async def reset_password(request: Request, token: str = Form(...), password: str = Form(...)):
    if len(password) < 8:
        return render(request, "reset_password.html", {"token": token, "error": _t(request, "auth", "password_short")}, 422)
    row = _password_reset_row(token)
    if not row:
        return render(request, "forgot_password.html", {"error": _t(request, "auth", "reset_invalid")}, 422)
    if row.get("is_expired"):
        return render(request, "forgot_password.html", {"error": _t(request, "auth", "reset_expired")}, 422)
    execute("UPDATE ai_users SET password_hash=%s WHERE id=%s", (hash_password(password), int(row["user_id"])))
    execute("UPDATE ai_password_resets SET used_at=NOW() WHERE id=%s", (int(row["id"]),))
    execute("UPDATE ai_password_resets SET used_at=NOW() WHERE user_id=%s AND used_at IS NULL", (int(row["user_id"]),))
    return RedirectResponse("/login?reset=1", status_code=303)


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    success = request.query_params.get("success") or ""
    return render(request, "profile.html", _profile_context(user, success=success))


@app.post("/profile")
async def save_profile(
    request: Request,
    nickname: str = Form(""),
    email: str = Form(...),
    timezone_name: str = Form(DEFAULT_TIMEZONE),
):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    uid = int(user["id"])
    email = email.strip().lower()
    nickname = nickname.strip()[:80]
    if not email:
        return render(request, "profile.html", _profile_context(user, error="Укажите электронную почту."), 422)
    existing = fetch_one("SELECT id FROM ai_users WHERE email=%s AND id<>%s", (email, uid))
    if existing:
        return render(request, "profile.html", _profile_context(user, error="Эта почта уже используется другим аккаунтом."), 422)
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = DEFAULT_TIMEZONE
    execute(
        "UPDATE ai_users SET nickname=%s, email=%s, timezone=%s WHERE id=%s",
        (nickname, email, timezone_name, uid),
    )
    return RedirectResponse("/profile?success=profile", status_code=303)


@app.post("/profile/telegram")
async def change_profile_telegram(request: Request, telegram_username: str = Form("")):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    uid = int(user["id"])
    telegram_username = _normalize_telegram_username(telegram_username)
    if not telegram_username:
        return render(request, "profile.html", _profile_context(user, error="Укажите телеграм аккаунт без знака @."), 422)
    existing_telegram = fetch_one("SELECT id FROM ai_users WHERE telegram_username=%s AND id<>%s", (telegram_username, uid))
    if existing_telegram:
        return render(request, "profile.html", _profile_context(user, error="Этот телеграм аккаунт уже указан в другом аккаунте."), 422)
    if telegram_username == _normalize_telegram_username(user.get("telegram_username")):
        return RedirectResponse("/profile?success=telegram", status_code=303)
    execute(
        """
        UPDATE ai_users
        SET telegram_username=%s, telegram_user_id=NULL, telegram_verified_at=NULL,
            telegram_last_checked_at=NULL,
            plan=IF(role='admin', plan, 'free')
        WHERE id=%s
        """,
        (telegram_username, uid),
    )
    return RedirectResponse("/profile?success=telegram", status_code=303)


@app.post("/profile/tariff-sync")
async def profile_tariff_sync(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not user.get("telegram_user_id"):
        return render(request, "profile.html", _profile_context(user, error="Сначала подтвердите Telegram аккаунт через бота."), 422)
    result = await sync_user_tariff(user)
    if not result.get("ok"):
        return render(request, "profile.html", _profile_context(user, error="Не удалось проверить тариф. Попробуйте позже."), 422)
    return RedirectResponse("/profile?success=tariff_sync", status_code=303)


@app.post("/profile/admin/users")
async def admin_update_user_plans(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not _user_access(user)["is_admin"]:
        return RedirectResponse("/profile", status_code=303)
    form = await request.form()
    rows = fetch_all("SELECT id, role FROM ai_users ORDER BY created_at DESC, id DESC")
    for row in rows:
        user_id = int(row["id"])
        if row.get("role") == "admin":
            execute("UPDATE ai_users SET plan='premium' WHERE id=%s", (user_id,))
            continue
        values = form.getlist(f"plan_{user_id}")
        submitted = next((value for value in values if value in PLAN_LIMITS), "free")
        plan = _normalized_plan(submitted)
        execute("UPDATE ai_users SET plan=%s WHERE id=%s", (plan, user_id))
    return RedirectResponse("/profile?success=plans", status_code=303)


@app.post("/profile/admin/users/delete")
async def admin_delete_user(request: Request, delete_user_id: int = Form(...)):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not _user_access(user)["is_admin"]:
        return RedirectResponse("/profile", status_code=303)
    target_id = int(delete_user_id)
    if target_id == int(user["id"]):
        return render(request, "profile.html", _profile_context(user, error="Нельзя удалить текущий админский аккаунт."), 422)
    target = fetch_one("SELECT id, role FROM ai_users WHERE id=%s", (target_id,))
    if not target:
        return RedirectResponse("/profile", status_code=303)
    if target.get("role") == "admin":
        return render(request, "profile.html", _profile_context(user, error="Админские аккаунты нельзя удалять из этой таблицы."), 422)
    execute("DELETE FROM ai_users WHERE id=%s", (target_id,))
    return RedirectResponse("/profile?success=user_deleted", status_code=303)


@app.post("/profile/admin/users/refresh")
async def admin_refresh_user_stats(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not _user_access(user)["is_admin"]:
        return RedirectResponse("/profile", status_code=303)
    if _start_admin_stats_refresh(force=True):
        return RedirectResponse("/profile?success=stats_queued", status_code=303)
    return RedirectResponse("/profile?success=stats_running", status_code=303)


@app.post("/profile/password-reset")
async def profile_password_reset(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    warning = _create_password_reset(request, int(user["id"]), user["email"])
    suffix = "password_warning" if warning else "password"
    return RedirectResponse(f"/profile?success={suffix}", status_code=303)


@app.post("/profile/delete-account")
async def delete_profile_account(request: Request, twofa_code: str = Form("")):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if _user_access(user)["is_admin"]:
        return render(request, "profile.html", _profile_context(user, error="Админский аккаунт нельзя удалить через профиль."), 422)
    if not _twofa_enabled(user):
        return render(request, "profile.html", _profile_context(user, error="Для удаления аккаунта сначала включите 2FA."), 422)
    if not _verify_user_2fa(user, twofa_code):
        return render(request, "profile.html", _profile_context(user, error="Неверный код 2FA. Аккаунт не удалён."), 422)
    execute("DELETE FROM ai_users WHERE id=%s", (int(user["id"]),))
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(settings.SESSION_COOKIE)
    response.delete_cookie(TWOFA_PENDING_COOKIE)
    return response


@app.post("/profile/2fa/pin")
async def enable_pin_2fa(
    request: Request,
    pin_code: str = Form(...),
    pin_code_confirm: str = Form(...),
    current_twofa_code: str = Form(""),
):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if _twofa_enabled(user) and not _verify_user_2fa(user, current_twofa_code):
        return render(
            request,
            "profile.html",
            _profile_context(user, error="Неверный текущий PIN или код 2FA."),
            422,
        )
    pin = "".join(ch for ch in pin_code if ch.isdigit())
    confirm = "".join(ch for ch in pin_code_confirm if ch.isdigit())
    if pin != confirm or len(pin) < 4 or len(pin) > 8:
        return render(
            request,
            "profile.html",
            _profile_context(user, error="PIN должен состоять из 4-8 цифр и совпадать в обоих полях."),
            422,
        )
    execute(
        """
        UPDATE ai_users
        SET twofa_method='pin', twofa_pin_hash=%s, twofa_totp_secret_encrypted=NULL,
            twofa_totp_pending_secret_encrypted=NULL, twofa_enabled_at=NOW()
        WHERE id=%s
        """,
        (hash_password(pin), int(user["id"])),
    )
    return RedirectResponse("/profile?success=2fa_pin", status_code=303)


@app.post("/profile/2fa/totp/start")
async def start_totp_2fa(request: Request, current_twofa_code: str = Form("")):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if _twofa_enabled(user) and not _verify_user_2fa(user, current_twofa_code):
        return render(
            request,
            "profile.html",
            _profile_context(user, error="Неверный текущий PIN или код 2FA."),
            422,
        )
    secret = make_totp_secret()
    execute("UPDATE ai_users SET twofa_totp_pending_secret_encrypted=%s WHERE id=%s", (encrypt_secret(secret), int(user["id"])))
    setup = _totp_setup_view(user["email"], secret)
    refreshed = fetch_one("SELECT * FROM ai_users WHERE id=%s", (int(user["id"]),)) or user
    return render(request, "profile.html", _profile_context(refreshed, success="totp_start", totp_setup=setup))


@app.post("/profile/2fa/totp/verify")
async def verify_totp_2fa(request: Request, code: str = Form(...)):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    pending = decrypt_secret(user.get("twofa_totp_pending_secret_encrypted"))
    if not pending:
        return RedirectResponse("/profile", status_code=303)
    if not verify_totp(pending, code):
        setup = _totp_setup_view(user["email"], pending)
        return render(
            request,
            "profile.html",
            _profile_context(user, error="Неверный код Google Authenticator.", totp_setup=setup),
            422,
        )
    execute(
        """
        UPDATE ai_users
        SET twofa_method='totp', twofa_pin_hash=NULL, twofa_totp_secret_encrypted=%s,
            twofa_totp_pending_secret_encrypted=NULL, twofa_enabled_at=NOW()
        WHERE id=%s
        """,
        (encrypt_secret(pending), int(user["id"])),
    )
    return RedirectResponse("/profile?success=2fa_totp", status_code=303)


@app.post("/profile/2fa/disable")
async def disable_2fa(request: Request, current_twofa_code: str = Form(...)):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not _twofa_enabled(user):
        return RedirectResponse("/profile", status_code=303)
    if not _verify_user_2fa(user, current_twofa_code):
        return render(request, "profile.html", _profile_context(user, error="Неверный текущий PIN или код 2FA."), 422)
    execute(
        """
        UPDATE ai_users
        SET twofa_method='none', twofa_pin_hash=NULL, twofa_totp_secret_encrypted=NULL,
            twofa_totp_pending_secret_encrypted=NULL, twofa_enabled_at=NULL
        WHERE id=%s
        """,
        (int(user["id"]),),
    )
    return RedirectResponse("/profile?success=2fa_off", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    uid = int(user["id"])
    connections = _connections(uid)
    selected_id = _selected_connection_id(request, connections)
    connection = _connection(uid, selected_id) if selected_id else None
    strategy = _strategy(uid, int(connection["id"])) if connection else _strategy(uid)
    risk_pauses = await _risk_pause_views(uid, connection)
    risk_pause = risk_pauses[0] if risk_pauses else None
    override_success = request.query_params.get("override") == "1"
    ai_signals = []
    if connection:
        ai_signals = fetch_all(
            "SELECT * FROM ai_signals WHERE user_id=%s AND connection_id <=> %s ORDER BY created_at DESC, id DESC LIMIT 20",
            (uid, int(connection["id"])),
        )
    _prune_user_signals(uid)
    response = render(request, "dashboard.html", {
        "connection": _connection_view(connection),
        "connections": [_connection_view(item) for item in connections],
        "selected_connection_id": selected_id,
        "strategy": strategy,
        "watchlist_pairs": _watchlist(strategy.get("watchlist") or ""),
        "strategy_meta": _strategy_meta_view(strategy["strategy_code"], _lang(request)),
        "risk_pause": risk_pause,
        "risk_pauses": risk_pauses,
        "override_success": override_success,
        "signals": [_signal_view(row, _lang(request), user) for row in ai_signals],
    })
    _remember_connection(response, selected_id)
    return response


@app.get("/connections", response_class=HTMLResponse)
async def connections_page(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    success = None
    if request.query_params.get("tested") == "1":
        success = _t(request, "connections", "api_valid")
    if request.query_params.get("saved") == "1":
        success = _t(request, "common", "saved")
    error = None
    if request.query_params.get("plan_error") == "market":
        error = _plan_message(_lang(request), "market")
    if request.query_params.get("limit_error") == "connection":
        error = "Для вашего тарифа доступно только одно подключение." if _lang(request) == "ru" else "Your plan allows only one connection."
    connection_id = int(request.query_params.get("connection_id") or 0)
    creating_new = request.query_params.get("new") == "1"
    uid = int(user["id"])
    connections = _connections(uid)
    can_create_connection = _can_create_connection(user, connections)
    if creating_new and not can_create_connection:
        creating_new = False
    selected = None if creating_new else (_connection(uid, connection_id) if connection_id else _connection(uid, _selected_connection_id(request, connections)))
    response = render(
        request,
        "connections.html",
        {
            "connection": _connection_view(selected),
            "connections": [_connection_view(item) for item in connections],
            "creating_new": creating_new or not selected,
            "can_create_connection": can_create_connection,
            "strategies": _available_strategies(user),
            "success": success,
            "error": error,
        },
    )
    if selected:
        _remember_connection(response, int(selected["id"]))
    return response


@app.post("/connections")
async def save_connections(
    request: Request,
    connection_id: int = Form(0),
    label: str = Form("Main account"),
    strategy_code: str = Form(settings.DEFAULT_STRATEGY_CODE),
    bybit_api_key: str = Form(""),
    bybit_api_secret: str = Form(""),
    webhook_url: str = Form(""),
):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    uid = int(user["id"])
    current = _connection(uid, connection_id) if connection_id else None
    if not current and not _can_create_connection(user):
        return RedirectResponse("/connections?limit_error=connection", status_code=303)
    strategy_code = strategy_code if strategy_code in STRATEGIES else settings.DEFAULT_STRATEGY_CODE
    if not _strategy_allowed_for_user(user, strategy_code):
        target = f"/connections?connection_id={int(current['id'])}" if current else "/connections?new=1"
        return RedirectResponse(f"{target}&plan_error=market" if "?" in target else f"{target}?plan_error=market", status_code=303)
    new_api_key = bybit_api_key.strip()
    previous_api_key = ((current or {}).get("bybit_api_key") or "").strip()
    secret_value = bybit_api_secret.strip()
    if current and new_api_key != previous_api_key and not secret_value:
        return render(
            request,
            "connections.html",
            {
                "connection": _connection_view(current),
                "connections": [_connection_view(item) for item in _connections(uid)],
                "creating_new": False,
                "can_create_connection": _can_create_connection(user),
                "strategies": _available_strategies(user),
                "error": _t(request, "connections", "secret_required"),
            },
            422,
        )
    encrypted_secret = encrypt_secret(secret_value) if secret_value else (current or {}).get("bybit_api_secret_encrypted")
    normalized_webhook = ghost_webhook.normalize_webhook_url(webhook_url)
    encrypted_webhook = encrypt_secret(normalized_webhook) if normalized_webhook else (current or {}).get("webhook_url_encrypted")
    if current:
        execute(
            """
            UPDATE ai_user_connections
            SET label=%s, strategy_code=%s, bybit_api_key=%s, bybit_api_secret_encrypted=%s, webhook_url_encrypted=%s, is_active=1
            WHERE id=%s AND user_id=%s
            """,
            (label.strip() or "Main account", strategy_code, new_api_key, encrypted_secret, encrypted_webhook, int(current["id"]), uid),
        )
        saved_connection_id = int(current["id"])
    else:
        saved_connection_id = execute(
            """
            INSERT INTO ai_user_connections (user_id, label, strategy_code, bybit_api_key, bybit_api_secret_encrypted, webhook_url_encrypted)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (uid, label.strip() or "Main account", strategy_code, new_api_key, encrypted_secret, encrypted_webhook),
        )
    _ensure_strategy_defaults(uid, saved_connection_id, strategy_code)
    execute(
        "UPDATE ai_user_strategy_settings SET strategy_code=%s WHERE user_id=%s AND connection_id=%s",
        (strategy_code, uid, saved_connection_id),
    )
    response = RedirectResponse(f"/connections?connection_id={saved_connection_id}&saved=1", status_code=303)
    _remember_connection(response, saved_connection_id)
    return response


@app.post("/connections/test")
async def test_connection(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    form = await request.form()
    connection_id = int(form.get("connection_id") or 0)
    conn = _connection(int(user["id"]), connection_id)
    if not conn:
        return RedirectResponse("/connections?error=missing", status_code=303)
    try:
        secret = decrypt_secret(conn.get("bybit_api_secret_encrypted"))
        wallet = await wallet_balance(conn.get("bybit_api_key") or "", secret)
        balance = extract_usdt_balance(wallet)
        execute(
            "UPDATE ai_user_connections SET last_balance=%s, last_error=NULL, last_checked_at=NOW() WHERE id=%s",
            (balance, int(conn["id"])),
        )
        response = RedirectResponse(f"/connections?connection_id={int(conn['id'])}&tested=1", status_code=303)
        _remember_connection(response, int(conn["id"]))
        return response
    except Exception as exc:
        execute(
            "UPDATE ai_user_connections SET last_error=%s, last_checked_at=NOW() WHERE id=%s",
            (str(exc), int(conn["id"])),
        )
    response = RedirectResponse(f"/connections?connection_id={int(conn['id'])}", status_code=303)
    _remember_connection(response, int(conn["id"]))
    return response


@app.get("/strategies", response_class=HTMLResponse)
async def strategies_page(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    uid = int(user["id"])
    connections = _connections(uid)
    selected_id = _selected_connection_id(request, connections)
    selected_connection = _connection(uid, selected_id) if selected_id else None
    strategy = _strategy(uid, selected_id) if selected_id else _strategy(uid)
    lang = _lang(request)
    error = _plan_message(lang, "market") if request.query_params.get("plan_error") == "market" else None
    if request.query_params.get("order_error") == "no_balance":
        error = "Не удалось определить текущий депозит подключения. Проверьте read-only API и обновите баланс." if lang == "ru" else "Could not determine the current connection deposit. Check the read-only API and refresh the balance."
    elif request.query_params.get("order_error") == "min_first_order":
        calculated = request.query_params.get("calculated") or "0.00"
        error = (
            f"При таком проценте от депозита объём первого ордера получается {calculated} USDT, это меньше минимально допустимых 6 USDT."
            if lang == "ru"
            else f"With this deposit percentage, the first order is {calculated} USDT, below the required minimum of 6 USDT."
        )
    elif request.query_params.get("order_error") == "manual_first_order_max":
        manual_max = request.query_params.get("max_manual") or "0.00"
        error = (
            f"Первый ордер при ручном вводе не может быть больше {manual_max} USDT: это 8% от текущего депозита подключения с учётом лимита тарифа."
            if lang == "ru"
            else f"The manual first order cannot exceed {manual_max} USDT: this is 8% of the current connection deposit with the plan limit applied."
        )
    available_strategies = _available_strategies(user)
    if strategy["strategy_code"] not in available_strategies:
        strategy["strategy_code"] = settings.DEFAULT_STRATEGY_CODE
    plan_limits = _plan_limits(user)
    disabled_pairs = _plan_disabled_pairs(user)
    response = render(request, "strategies.html", {
        "strategies": available_strategies,
        "strategy_cards": [_strategy_card_view(code, lang) for code in available_strategies],
        "connections": [_connection_view(item) for item in connections],
        "selected_connection_id": selected_id,
        "selected_connection": _connection_view(selected_connection),
        "strategy": strategy,
        "strategy_meta": _strategy_card_view(strategy["strategy_code"], lang),
        "volume_hint": _volume_hint(strategy, selected_connection, user),
        "plan_limits": plan_limits,
        "pair_options": settings.RECOMMENDED_PAIR_OPTIONS,
        "plan_disabled_pairs": disabled_pairs,
        "selected_pairs": set(_watchlist(strategy.get("watchlist") or "")),
        "error": error,
    })
    _remember_connection(response, selected_id)
    return response


@app.post("/strategies")
async def save_strategy(
    request: Request,
    connection_id: int = Form(0),
    strategy_code: str = Form(settings.DEFAULT_STRATEGY_CODE),
    enabled: str = Form("0"),
    risk_pct: float = Form(settings.DEFAULT_RISK_PCT),
    min_order_volume: float = Form(settings.DEFAULT_MIN_ORDER_VOLUME),
    first_order_mode: str = Form("manual"),
    leverage: int = Form(settings.DEFAULT_LEVERAGE),
    max_active_deals: int = Form(0),
    max_long_deals: int = Form(0),
    max_short_deals: int = Form(0),
    watchlist: list[str] = Form([]),
):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    uid = int(user["id"])
    connection = _connection(uid, connection_id)
    if not connection:
        return RedirectResponse("/strategies", status_code=303)
    strategy_code = strategy_code if strategy_code in STRATEGIES else settings.DEFAULT_STRATEGY_CODE
    access = _user_access(user)
    limits = _plan_limits(user)
    if not access["is_admin"]:
        strategy_code = settings.DEFAULT_STRATEGY_CODE
    if not _strategy_allowed_for_user(user, strategy_code):
        response = RedirectResponse(f"/strategies?connection_id={int(connection['id'])}&plan_error=market", status_code=303)
        _remember_connection(response, int(connection["id"]))
        return response
    _ensure_strategy_defaults(uid, int(connection["id"]), strategy_code)
    if strategy_code in {"market_shock_impulse_v1", "market_shock_reversal_dca_v21"}:
        selected_pairs = list(settings.MARKET_SHOCK_ALLOWED_PAIRS)
    else:
        selected_pairs = _normalize_pair_selection(watchlist)
    max_risk_pct = float(limits.get("max_risk_pct") or settings.MAX_STRATEGY_RISK_PCT)
    risk_pct = min(max_risk_pct, max(1.0, float(risk_pct)))
    leverage = 10
    first_order_mode = first_order_mode if first_order_mode in {"manual", "deposit_pct"} else "manual"
    if not limits["can_use_deposit_pct"]:
        first_order_mode = "manual"
    if first_order_mode == "deposit_pct":
        balance = float(connection.get("last_balance") or 0)
        if balance <= 0:
            response = RedirectResponse(f"/strategies?connection_id={int(connection['id'])}&order_error=no_balance", status_code=303)
            _remember_connection(response, int(connection["id"]))
            return response
        factor = _typical_safety_factor(settings.TYPICAL_SAFETY_ORDERS, settings.TYPICAL_MARTINGALE_MULTIPLIER)
        calculated_first_order = round((balance * (risk_pct / 100.0) * leverage) / factor, 2)
        if calculated_first_order < settings.MIN_FIRST_ORDER_VOLUME:
            response = RedirectResponse(
                f"/strategies?connection_id={int(connection['id'])}&order_error=min_first_order&calculated={_fmt_fixed(calculated_first_order)}",
                status_code=303,
            )
            _remember_connection(response, int(connection["id"]))
            return response
        first_order_volume = min(calculated_first_order, float(limits["max_first_order"]))
    else:
        first_order_volume = max(float(min_order_volume), settings.MIN_FIRST_ORDER_VOLUME)
        if not access["is_admin"]:
            first_order_volume = min(first_order_volume, float(limits["max_first_order"]))
        if limits["code"] != "free" and not access["is_admin"]:
            manual_cap = _manual_first_order_cap(user, connection)
            if first_order_volume > manual_cap:
                response = RedirectResponse(
                    f"/strategies?connection_id={int(connection['id'])}&order_error=manual_first_order_max&max_manual={_fmt_fixed(manual_cap)}",
                    status_code=303,
                )
                _remember_connection(response, int(connection["id"]))
                return response
            first_order_volume = min(first_order_volume, manual_cap)
    max_active_deals = max(0, max_active_deals)
    max_long_deals = max(0, max_long_deals)
    max_short_deals = max(0, max_short_deals)
    if not access["is_admin"]:
        strategy_code = settings.DEFAULT_STRATEGY_CODE
        selected_pairs = _normalize_pair_selection(watchlist, excluded=_plan_disabled_pairs(user))
        leverage = 10
        max_active_deals = min(max_active_deals, int(limits["max_active_deals"]))
        max_long_deals = min(max_long_deals, int(limits["max_long_deals"]))
        max_short_deals = min(max_short_deals, int(limits["max_short_deals"]))
        if limits["code"] == "free":
            first_order_volume = settings.MIN_FIRST_ORDER_VOLUME
            first_order_mode = "manual"
            risk_pct = settings.DEFAULT_RISK_PCT
    strategy_enabled = 1 if enabled == "1" else 0
    execute(
        """
        UPDATE ai_user_strategy_settings
        SET enabled=%s, auto_trade=%s, risk_pct=%s, min_order_volume=%s, first_order_mode=%s, leverage=%s,
            max_active_deals=%s, max_long_deals=%s, max_short_deals=%s, watchlist=%s
        WHERE user_id=%s AND connection_id=%s
        """,
        (
            strategy_enabled,
            strategy_enabled,
            risk_pct,
            first_order_volume,
            first_order_mode,
            leverage,
            max_active_deals,
            max_long_deals,
            max_short_deals,
            ",".join(selected_pairs),
            uid,
            int(connection["id"]),
        ),
    )
    execute(
        "UPDATE ai_user_connections SET strategy_code=%s WHERE id=%s AND user_id=%s",
        (strategy_code, int(connection["id"]), uid),
    )
    execute(
        "UPDATE ai_user_strategy_settings SET strategy_code=%s WHERE user_id=%s AND connection_id=%s",
        (strategy_code, uid, int(connection["id"])),
    )
    response = RedirectResponse(f"/strategies?connection_id={int(connection['id'])}&saved=1", status_code=303)
    _remember_connection(response, int(connection["id"]))
    return response


@app.get("/signals", response_class=HTMLResponse)
async def ai_signals_page(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    uid = int(user["id"])
    connections = _connections(uid)
    selected_id = _selected_connection_id(request, connections)
    selected_connection = _connection(uid, selected_id) if selected_id else None
    rows = []
    if selected_connection:
        rows = fetch_all(
            """
            SELECT * FROM ai_signals
            WHERE user_id=%s AND connection_id <=> %s AND strategy_code=%s
            ORDER BY created_at DESC, id DESC
            LIMIT 50
            """,
            (uid, selected_id, selected_connection.get("strategy_code") or settings.DEFAULT_STRATEGY_CODE),
        )
    _prune_user_signals(uid)
    response = render(request, "signals.html", {
        "signals": [_signal_view(row, _lang(request), user) for row in rows],
        "connections": [_connection_view(item) for item in connections],
        "selected_connection_id": selected_id,
        "selected_connection": _connection_view(selected_connection),
    })
    _remember_connection(response, selected_id)
    return response


@app.post("/signals/scan")
async def manual_scan(request: Request):
    return Response(status_code=404)
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    await scan_once()
    return RedirectResponse("/signals", status_code=303)


@app.post("/risk/override")
async def override_risk_pause(request: Request, pause_type: str = Form(""), pair: str = Form("")):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    uid = int(user["id"])
    connections = _connections(uid)
    selected_id = _selected_connection_id(request, connections)
    conn = _connection(uid, selected_id) if selected_id else None
    if pause_type == "strategy_pause" and conn:
        pauses = await _risk_pause_views(uid, conn, ignore_override=True)
        target = next((item for item in pauses if item.get("pair") == (pair or "*").upper()), None)
        if target and target.get("type") == "strategy_pause":
            execute(
                """
                INSERT INTO ai_strategy_pause_overrides
                (user_id, connection_id, strategy_code, pair, override_until, reason)
                VALUES (%s, %s, %s, %s, FROM_UNIXTIME(%s), %s)
                ON DUPLICATE KEY UPDATE
                    override_until=VALUES(override_until),
                    reason=VALUES(reason),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    uid,
                    int(conn["id"]),
                    conn.get("strategy_code") or settings.DEFAULT_STRATEGY_CODE,
                    target.get("pair") or "*",
                    int(int(target["ends_at_ms"]) / 1000),
                    target["reason"],
                ),
            )
            return RedirectResponse("/dashboard?override=1", status_code=303)
        return RedirectResponse("/dashboard", status_code=303)

    pause = await _generic_risk_pause_view(uid, conn, ignore_override=True)
    if pause:
        ends_at_ms = int(pause.get("ends_at_ms") or 0)
        if ends_at_ms <= 0:
            ends_at_ms = int((datetime.now(timezone.utc) + timedelta(seconds=int(pause.get("remaining_seconds") or 0))).timestamp() * 1000)
        execute(
            """
            INSERT INTO ai_risk_pause_overrides (user_id, override_until, reason)
            VALUES (%s, FROM_UNIXTIME(%s), %s)
            ON DUPLICATE KEY UPDATE
                override_until=VALUES(override_until),
                reason=VALUES(reason),
                updated_at=CURRENT_TIMESTAMP
            """,
            (uid, int(ends_at_ms / 1000), pause["reason"]),
        )
        await scan_once()
        return RedirectResponse("/dashboard?override=1", status_code=303)
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/signals/{signal_id}/send")
async def send_signal(request: Request, signal_id: int):
    return Response(status_code=404)
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    row = fetch_one("SELECT * FROM ai_signals WHERE id=%s AND user_id=%s", (signal_id, int(user["id"])))
    conn = _connection(int(user["id"]), int(row["connection_id"] or 0)) if row else None
    if not row or not conn:
        return RedirectResponse("/signals", status_code=303)
    try:
        if _is_free_user(user) and (row.get("strategy_code") or "") != settings.DEFAULT_STRATEGY_CODE:
            reason = _plan_message(_lang(request), "market")
            execute(
                "UPDATE ai_signals SET status='skipped', reasons=%s, error_message=%s WHERE id=%s",
                (json.dumps([reason], ensure_ascii=False), reason, signal_id),
            )
            return RedirectResponse("/signals", status_code=303)
        limit_reason = await _manual_launch_limit_reason(int(user["id"]), conn, row)
        if limit_reason:
            execute(
                "UPDATE ai_signals SET status='skipped', reasons=%s, error_message=%s WHERE id=%s",
                (json.dumps([limit_reason], ensure_ascii=False), limit_reason, signal_id),
            )
            return RedirectResponse("/signals", status_code=303)
        cooldown_reason = reserve_pair_launch(
            int(user["id"]),
            int(row["connection_id"] or 0),
            row.get("pair") or "",
            f"manual:{signal_id}:{row.get('side') or ''}",
        )
        if cooldown_reason:
            execute(
                "UPDATE ai_signals SET status='skipped', reasons=%s, error_message=%s WHERE id=%s",
                (json.dumps([cooldown_reason], ensure_ascii=False), cooldown_reason, signal_id),
            )
            return RedirectResponse("/signals", status_code=303)
        if (row.get("strategy_code") or "") in {"grid_dca_v2", "grid_dca_v3"} and (row.get("side") or "") in {"long", "short"}:
            cooldown_reason = reserve_strategy_side_launch(
                int(user["id"]),
                int(row["connection_id"] or 0),
                row.get("strategy_code") or settings.DEFAULT_STRATEGY_CODE,
                row.get("side") or "",
                settings.GRID_DCA_SIDE_WEBHOOK_COOLDOWN_SECONDS,
                f"manual:{signal_id}:{row.get('side') or ''}:{row.get('pair') or ''}",
            )
            if cooldown_reason:
                release_pair_launch(int(user["id"]), int(row["connection_id"] or 0), row.get("pair") or "")
                execute(
                    "UPDATE ai_signals SET status='skipped', reasons=%s, error_message=%s WHERE id=%s",
                    (json.dumps([cooldown_reason], ensure_ascii=False), cooldown_reason, signal_id),
                )
                return RedirectResponse("/signals", status_code=303)
        payload = json.loads(row.get("payload") or "{}")
        webhook = decrypt_secret(conn.get("webhook_url_encrypted"))
        result = await ghost_webhook.send_payload(payload, webhook_url=webhook, confirm=True)
        status = "sent" if result.get("ok") else "failed"
        error_message = ghost_webhook.failure_message(result) if status == "failed" else None
        if status == "failed":
            release_pair_launch(int(user["id"]), int(conn["id"]), row.get("pair") or "")
            if (row.get("strategy_code") or "") in {"grid_dca_v2", "grid_dca_v3"}:
                release_strategy_side_launch(
                    int(user["id"]),
                    int(row["connection_id"] or 0),
                    row.get("strategy_code") or "",
                    row.get("side") or "",
                )
        if status == "sent":
            opened = await _confirm_position_opened(conn, row.get("pair") or "", row.get("side") or "")
            if not opened:
                status = "failed"
                error_message = "Cryptorg принял webhook, но позиция не появилась в read-only API"
        execute(
            "UPDATE ai_signals SET status=%s, response=%s, error_message=%s, sent_at=%s WHERE id=%s",
            (
                status,
                json.dumps(result, ensure_ascii=False),
                error_message,
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if status == "sent" else None,
                signal_id,
            ),
        )
    except Exception as exc:
        execute("UPDATE ai_signals SET status='failed', error_message=%s WHERE id=%s", (str(exc), signal_id))
    return RedirectResponse("/signals", status_code=303)


@app.get("/monitoring", response_class=HTMLResponse)
async def monitoring_page(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    uid = int(user["id"])
    connections = _connections(uid)
    selected_id = _selected_connection_id(request, connections)
    selected_connection = _connection(uid, selected_id) if selected_id else None
    start_date, end_date = _monitoring_dates(request, user)
    error = None
    monitor = None
    if selected_connection:
        try:
            monitor = await _monitoring_snapshot(selected_connection, start_date, end_date, user)
        except Exception as exc:
            error = str(exc)
            execute(
                "UPDATE ai_user_connections SET last_error=%s, last_checked_at=NOW() WHERE id=%s AND user_id=%s",
                (error, int(selected_connection["id"]), uid),
            )
    response = render(request, "monitoring.html", {
        "connections": [_connection_view(item) for item in connections],
        "selected_connection_id": selected_id,
        "selected_connection": _connection_view(selected_connection),
        "start_date": start_date,
        "end_date": end_date,
        "monitor": monitor,
        "error": error,
    })
    _remember_connection(response, selected_id)
    return response


def _ensure_strategy_defaults(user_id: int, connection_id: int | None = None, strategy_code: str | None = None) -> None:
    strategy_code = strategy_code if strategy_code in STRATEGIES else settings.DEFAULT_STRATEGY_CODE
    if connection_id:
        existing = fetch_one(
            "SELECT id FROM ai_user_strategy_settings WHERE user_id=%s AND connection_id=%s",
            (user_id, connection_id),
        )
    else:
        existing = fetch_one(
            "SELECT id FROM ai_user_strategy_settings WHERE user_id=%s AND connection_id IS NULL",
            (user_id,),
        )
    if existing:
        return
    execute(
        """
        INSERT INTO ai_user_strategy_settings
        (user_id, connection_id, strategy_code, risk_pct, min_order_volume, leverage, max_active_deals, max_long_deals, max_short_deals, watchlist)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id,
            connection_id,
            strategy_code,
            settings.DEFAULT_RISK_PCT,
            max(settings.DEFAULT_MIN_ORDER_VOLUME, settings.MIN_FIRST_ORDER_VOLUME),
            10,
            settings.DEFAULT_MAX_ACTIVE_DEALS,
            settings.DEFAULT_MAX_LONG_DEALS,
            settings.DEFAULT_MAX_SHORT_DEALS,
            ",".join(settings.DEFAULT_WATCHLIST),
        ),
    )


def _connections(user_id: int) -> list[dict]:
    return fetch_all(
        "SELECT * FROM ai_user_connections WHERE user_id=%s AND is_active=1 ORDER BY id DESC",
        (user_id,),
    )


def _connection(user_id: int, connection_id: int | None = None) -> dict | None:
    if connection_id:
        return fetch_one(
            "SELECT * FROM ai_user_connections WHERE user_id=%s AND id=%s AND is_active=1",
            (user_id, connection_id),
        )
    return fetch_one("SELECT * FROM ai_user_connections WHERE user_id=%s AND is_active=1 ORDER BY id DESC LIMIT 1", (user_id,))


def _selected_connection_id(request: Request, connections: list[dict]) -> int:
    if not connections:
        return 0
    available = {int(item["id"]) for item in connections}
    raw_values = [
        request.query_params.get("connection_id"),
        request.cookies.get(SELECTED_CONNECTION_COOKIE),
    ]
    for raw in raw_values:
        try:
            selected = int(raw or 0)
        except (TypeError, ValueError):
            selected = 0
        if selected in available:
            return selected
    return int(connections[0]["id"])


def _remember_connection(response: Response, connection_id: int | None) -> None:
    if connection_id:
        response.set_cookie(SELECTED_CONNECTION_COOKIE, str(int(connection_id)), max_age=settings.SESSION_MAX_AGE, httponly=False, samesite="lax")


def _strategy(user_id: int, connection_id: int | None = None) -> dict:
    conn = _connection(user_id, connection_id) if connection_id else None
    strategy_code = (conn or {}).get("strategy_code") or settings.DEFAULT_STRATEGY_CODE
    _ensure_strategy_defaults(user_id, connection_id, strategy_code)
    if connection_id:
        return fetch_one(
            "SELECT * FROM ai_user_strategy_settings WHERE user_id=%s AND connection_id=%s",
            (user_id, connection_id),
        )
    return fetch_one("SELECT * FROM ai_user_strategy_settings WHERE user_id=%s AND connection_id IS NULL", (user_id,))


def _watchlist(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _normalize_pair_selection(values: list[str], excluded: set[str] | None = None) -> list[str]:
    allowed = {pair["symbol"] for pair in settings.RECOMMENDED_PAIR_OPTIONS}
    blocked = {item.upper() for item in (excluded or set())}
    selected = []
    for value in values:
        symbol = value.strip().upper()
        if symbol in allowed and symbol not in blocked and symbol not in selected:
            selected.append(symbol)
    defaults = [symbol for symbol in settings.DEFAULT_WATCHLIST if symbol not in blocked]
    return selected or defaults


async def _manual_launch_limit_reason(user_id: int, conn: dict, signal: dict) -> str | None:
    strategy = _strategy(user_id, int(conn["id"]))
    api_key = conn.get("bybit_api_key") or ""
    api_secret = decrypt_secret(conn.get("bybit_api_secret_encrypted"))
    if not api_key or not api_secret:
        return "не подключён read-only API: нельзя безопасно проверить лимиты открытых сделок"
    try:
        rows = await positions(api_key, api_secret)
        counts = _active_position_counts(rows)
        if (signal.get("strategy_code") or "") == settings.DEFAULT_STRATEGY_CODE:
            counts = _capacity_position_counts(user_id, int(conn["id"]), signal.get("strategy_code") or "", rows, counts)
    except Exception as exc:
        return f"не удалось проверить открытые позиции перед запуском: {exc}"
    return _deal_limit_reason(strategy, counts, signal.get("side") or "")


def _active_position_counts(rows: list[dict]) -> dict:
    counts = {"total": 0, "long": 0, "short": 0}
    for row in rows:
        try:
            size = abs(float(row.get("size") or 0))
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            continue
        side = str(row.get("side") or "").lower()
        counts["total"] += 1
        if side == "buy":
            counts["long"] += 1
        elif side == "sell":
            counts["short"] += 1
    return counts


def _capacity_position_counts(user_id: int, connection_id: int, strategy_code: str, rows: list[dict], counts: dict) -> dict:
    lookback = max(0, int(settings.GRID_DCA_LAUNCH_SAFETY_LOOKBACK_MINUTES))
    if lookback <= 0:
        return counts
    result = dict(counts)
    active_keys: set[tuple[str, str]] = set()
    for row in rows:
        try:
            size = abs(float(row.get("size") or 0))
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            continue
        pair = str(row.get("symbol") or "").upper()
        raw_side = str(row.get("side") or "").lower()
        side = "long" if raw_side == "buy" else ("short" if raw_side == "sell" else "")
        if pair and side:
            active_keys.add((pair, side))
    recent = fetch_all(
        """
        SELECT pair, side
        FROM ai_signals
        WHERE user_id=%s AND connection_id <=> %s AND strategy_code=%s
          AND side IN ('long','short')
          AND (status='sent' OR response IS NOT NULL)
          AND created_at > DATE_SUB(NOW(), INTERVAL %s MINUTE)
        GROUP BY pair, side
        """,
        (user_id, connection_id, strategy_code, lookback),
    )
    seen_virtual: set[tuple[str, str]] = set()
    for row in recent:
        pair = str(row.get("pair") or "").upper()
        side = str(row.get("side") or "").lower()
        key = (pair, side)
        if not pair or side not in {"long", "short"} or key in active_keys or key in seen_virtual:
            continue
        seen_virtual.add(key)
        result["total"] += 1
        result[side] += 1
    return result


async def _confirm_position_opened(conn: dict, pair: str, side: str) -> bool:
    api_key = conn.get("bybit_api_key") or ""
    api_secret = decrypt_secret(conn.get("bybit_api_secret_encrypted"))
    if not api_key or not api_secret:
        return True
    await asyncio.sleep(5)
    try:
        rows = await positions(api_key, api_secret)
    except Exception:
        return True
    target = pair.upper()
    for row in rows:
        if str(row.get("symbol") or "").upper() != target:
            continue
        try:
            size = abs(float(row.get("size") or 0))
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            continue
        row_side = str(row.get("side") or "").lower()
        if side == "long" and row_side == "buy":
            return True
        if side == "short" and row_side == "sell":
            return True
    return False


def _monitoring_dates(request: Request, user: dict | None = None) -> tuple[str, str]:
    today = datetime.now(_user_zone(user)).date()
    default_start = _user_registered_date(user) or today
    start = _parse_date(request.query_params.get("start_date"), default_start)
    end = _parse_date(request.query_params.get("end_date"), today)
    if start > end:
        start, end = end, start
    return start.isoformat(), end.isoformat()


def _user_registered_date(user: dict | None) -> object | None:
    if not user:
        return None
    return _local_date_from_utc(user.get("created_at"), user)


def _parse_date(value: str | None, fallback) -> object:
    if not value:
        return fallback
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return fallback


async def _monitoring_snapshot(conn: dict, start_date: str, end_date: str, user: dict | None) -> dict:
    api_key = conn.get("bybit_api_key") or ""
    api_secret = decrypt_secret(conn.get("bybit_api_secret_encrypted"))
    if not api_key or not api_secret:
        raise ValueError("Для мониторинга нужно сохранить read-only API ключ и секрет Cryptorg.")

    user_tz = _user_zone(user)
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=user_tz).astimezone(timezone.utc)
    end_dt = (
        (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1))
        .replace(tzinfo=user_tz)
        .astimezone(timezone.utc)
        - timedelta(milliseconds=1)
    )
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    wallet, position_rows, closed_rows = await asyncio.gather(
        wallet_balance(api_key, api_secret),
        positions(api_key, api_secret),
        _closed_pnl_period(api_key, api_secret, start_ms, end_ms),
    )
    balance = extract_usdt_balance(wallet)
    active_positions = [_position_monitor_view(row) for row in position_rows if abs(_float(row.get("size"))) > 0]
    closed = [_closed_pnl_view(row, user) for row in closed_rows]
    closed.sort(key=lambda item: item["time_ms"], reverse=True)
    daily = _daily_pnl(closed, start_date, end_date, user)
    total_pnl = sum(item["pnl"] for item in closed)
    total_trades = len(closed)
    wins = sum(1 for item in closed if item["pnl"] > 0)
    start_balance = balance - total_pnl
    if start_balance <= 0:
        start_balance = balance
    period_return = (total_pnl / start_balance * 100) if start_balance > 0 else 0.0
    days = max(1, (datetime.strptime(end_date, "%Y-%m-%d").date() - datetime.strptime(start_date, "%Y-%m-%d").date()).days + 1)
    execute(
        "UPDATE ai_user_connections SET last_balance=%s, last_error=NULL, last_checked_at=NOW() WHERE id=%s",
        (balance, int(conn["id"])),
    )
    return {
        "balance": _fmt_money(balance),
        "positions": active_positions,
        "closed": closed[:100],
        "daily": daily,
        "chart_json": json.dumps([
            {
                "date": item["date"],
                "label": item["label"],
                "pnl": round(float(item["pnl"]), 6),
                "pnlText": item["pnl_text"],
                "cumulative": round(float(item["cumulative"]), 6),
                "cumulativeText": item["cumulative_text"],
                "trades": int(item["trades"]),
            }
            for item in daily
        ], ensure_ascii=False),
        "summary": {
            "total_pnl": _fmt_money(total_pnl),
            "total_pnl_raw": total_pnl,
            "total_trades": total_trades,
            "win_rate": _fmt_percent((wins / total_trades * 100) if total_trades else 0),
            "avg_day": _fmt_money(total_pnl / days),
            "period_return": _fmt_percent(period_return),
            "positions_count": len(active_positions),
            "unrealised_pnl": _fmt_money(sum(item["unrealised_pnl_raw"] for item in active_positions)),
            "margin_used": _fmt_money(sum(item["margin_raw"] for item in active_positions)),
        },
    }


async def _closed_pnl_period(api_key: str, api_secret: str, start_ms: int, end_ms: int) -> list[dict]:
    rows: list[dict] = []
    chunk_start = start_ms
    week_ms = 7 * 24 * 60 * 60 * 1000
    while chunk_start <= end_ms:
        chunk_end = min(end_ms, chunk_start + week_ms - 1)
        cursor = None
        seen_cursors: set[str] = set()
        while True:
            page = await closed_pnl_history_page(api_key, api_secret, chunk_start, chunk_end, limit=100, cursor=cursor)
            rows.extend(page.get("list") or [])
            cursor = str(page.get("nextPageCursor") or "")
            if not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
        chunk_start = chunk_end + 1
    return rows


def _position_monitor_view(row: dict) -> dict:
    size = abs(_float(row.get("size")))
    side_raw = str(row.get("side") or "").lower()
    side = "long" if side_raw == "buy" else ("short" if side_raw == "sell" else side_raw)
    entry = _float(row.get("avgPrice") or row.get("entryPrice"))
    mark = _float(row.get("markPrice"))
    leverage = _float(row.get("leverage"))
    margin = _float(row.get("positionIM") or row.get("positionBalance"))
    pnl = _float(row.get("unrealisedPnl"))
    return {
        "symbol": str(row.get("symbol") or "").upper(),
        "side": side,
        "size": _fmt_monitor_number(size, 4),
        "entry": _fmt_monitor_number(entry, 6),
        "mark": _fmt_monitor_number(mark, 6),
        "leverage": _fmt_monitor_number(leverage, 0),
        "margin": _fmt_money(margin),
        "margin_raw": margin,
        "unrealised_pnl": _fmt_money(pnl),
        "unrealised_pnl_raw": pnl,
        "pnl_class": "positive" if pnl > 0 else ("negative" if pnl < 0 else ""),
    }


def _closed_pnl_view(row: dict, user: dict | None) -> dict:
    pnl = _float(row.get("closedPnl"))
    qty = _float(row.get("qty"))
    entry = _float(row.get("avgEntryPrice"))
    exit_price = _float(row.get("avgExitPrice"))
    time_ms = int(_float(row.get("updatedTime") or row.get("createdTime")))
    raw_side = str(row.get("side") or "").lower()
    # Closed PnL side is the closing order side: Sell closes a long, Buy closes a short.
    side = "short" if raw_side == "buy" else ("long" if raw_side == "sell" else raw_side)
    return {
        "symbol": str(row.get("symbol") or "").upper(),
        "side": side,
        "qty": _fmt_monitor_number(qty, 4),
        "entry": _fmt_monitor_number(entry, 6),
        "exit": _fmt_monitor_number(exit_price, 6),
        "pnl": pnl,
        "pnl_text": _fmt_money(pnl),
        "pnl_class": "positive" if pnl > 0 else ("negative" if pnl < 0 else ""),
        "time_ms": time_ms,
        "time": datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).astimezone(_user_zone(user)).strftime("%Y-%m-%d %H:%M"),
    }


def _daily_pnl(closed: list[dict], start_date: str, end_date: str, user: dict | None) -> list[dict]:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    days = {}
    current = start
    while current <= end:
        days[current.isoformat()] = {"date": current.isoformat(), "label": current.strftime("%d.%m"), "pnl": 0.0, "trades": 0}
        current += timedelta(days=1)
    for item in closed:
        key = datetime.fromtimestamp(item["time_ms"] / 1000, tz=timezone.utc).astimezone(_user_zone(user)).date().isoformat()
        if key in days:
            days[key]["pnl"] += item["pnl"]
            days[key]["trades"] += 1
    max_abs = max((abs(item["pnl"]) for item in days.values()), default=0.0) or 1.0
    cumulative = 0.0
    result = []
    for item in days.values():
        cumulative += item["pnl"]
        result.append({
            **item,
            "cumulative": cumulative,
            "pnl_text": _fmt_money(item["pnl"]),
            "cumulative_text": _fmt_money(cumulative),
            "bar_pct": max(5, abs(item["pnl"]) / max_abs * 100) if item["pnl"] else 0,
            "pnl_class": "positive" if item["pnl"] > 0 else ("negative" if item["pnl"] < 0 else ""),
        })
    return result


def _float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _closed_entry_value(row: dict) -> float:
    value = _float(row.get("cumEntryValue"))
    if value > 0:
        return value
    return abs(_float(row.get("qty")) * _float(row.get("avgEntryPrice")))


def _fmt_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _fmt_monitor_number(value: float, decimals: int = 2) -> str:
    return f"{value:,.{decimals}f}".replace(",", " ")


def _fmt_percent(value: float) -> str:
    return f"{value:.2f}%"


def _fmt_duration(seconds: int | float | None) -> str:
    total = int(seconds or 0)
    if total <= 0:
        return "—"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _deal_limit_reason(strategy: dict, counts: dict, side: str) -> str | None:
    total_limit = int(strategy.get("max_active_deals") or 0)
    long_limit = int(strategy.get("max_long_deals") or 0)
    short_limit = int(strategy.get("max_short_deals") or 0)
    if total_limit <= 0:
        return "открытие сделок отключено: максимум активных сделок = 0"
    if side == "long" and long_limit <= 0:
        return "открытие лонг-сделок отключено: максимум лонг-сделок = 0"
    if side == "short" and short_limit <= 0:
        return "открытие шорт-сделок отключено: максимум шорт-сделок = 0"
    if counts["total"] >= total_limit:
        return f"лимит активных сделок достигнут: {counts['total']}/{total_limit}"
    if side == "long" and counts["long"] >= long_limit:
        return f"лимит лонг-сделок достигнут: {counts['long']}/{long_limit}"
    if side == "short" and counts["short"] >= short_limit:
        return f"лимит шорт-сделок достигнут: {counts['short']}/{short_limit}"
    return None


def _twofa_enabled(user: dict) -> bool:
    method = user.get("twofa_method") or "none"
    if method == "pin":
        return bool(user.get("twofa_pin_hash"))
    if method == "totp":
        return bool(user.get("twofa_totp_secret_encrypted"))
    return False


def _verify_user_2fa(user: dict, code: str) -> bool:
    method = user.get("twofa_method") or "none"
    if method == "pin":
        return verify_password("".join(ch for ch in code if ch.isdigit()), user.get("twofa_pin_hash") or "")
    if method == "totp":
        secret = decrypt_secret(user.get("twofa_totp_secret_encrypted"))
        return verify_totp(secret, code) if secret else False
    return True


def _create_password_reset(request: Request, user_id: int, email: str) -> str | None:
    token = make_reset_token()
    token_hash = hash_reset_token(token)
    execute(
        """
        INSERT INTO ai_password_resets (user_id, token_hash, expires_at, request_ip, user_agent)
        VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL %s MINUTE), %s, %s)
        """,
        (
            user_id,
            token_hash,
            settings.PASSWORD_RESET_TTL_MINUTES,
            request.client.host if request.client else "",
            (request.headers.get("user-agent") or "")[:255],
        ),
    )
    reset_url = f"{settings.APP_BASE_URL.rstrip('/')}/password/reset?token={token}"
    if not smtp_configured():
        logger.warning("Password reset requested but SMTP is not configured. URL: %s", reset_url)
        return "smtp"
    try:
        sent = send_password_reset(email, reset_url, _lang(request))
    except Exception:
        logger.exception("Failed to send password reset email")
        sent = False
    return None if sent else "smtp"


def _create_email_verification(request: Request, user_id: int, email: str) -> bool:
    token = make_reset_token()
    token_hash = hash_reset_token(token)
    execute(
        """
        INSERT INTO ai_email_verifications (user_id, token_hash, expires_at, request_ip, user_agent)
        VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL %s MINUTE), %s, %s)
        """,
        (
            user_id,
            token_hash,
            settings.EMAIL_VERIFICATION_TTL_MINUTES,
            request.client.host if request.client else "",
            (request.headers.get("user-agent") or "")[:255],
        ),
    )
    verify_url = f"{settings.APP_BASE_URL.rstrip('/')}/email/verify?token={token}"
    if not smtp_configured():
        logger.warning("Email verification requested but SMTP is not configured. URL: %s", verify_url)
        return False
    try:
        return bool(send_email_verification(email, verify_url, _lang(request)))
    except Exception:
        logger.exception("Failed to send email verification")
        return False


def _totp_setup_view(email: str, secret: str) -> dict:
    uri = totp_uri(email, secret)
    try:
        import qrcode

        image = qrcode.make(uri)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        qr_data = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    except Exception:
        logger.exception("Failed to build TOTP QR code")
        qr_data = ""
    return {"secret": secret, "uri": uri, "qr_data": qr_data}


def _password_reset_row(token: str) -> dict | None:
    if not token:
        return None
    return fetch_one(
        """
        SELECT id, user_id, expires_at < NOW() AS is_expired
        FROM ai_password_resets
        WHERE token_hash=%s AND used_at IS NULL
        LIMIT 1
        """,
        (hash_reset_token(token),),
    )


def _email_verification_row(token: str) -> dict | None:
    if not token:
        return None
    return fetch_one(
        """
        SELECT id, user_id, expires_at < NOW() AS is_expired
        FROM ai_email_verifications
        WHERE token_hash=%s AND used_at IS NULL
        LIMIT 1
        """,
        (hash_reset_token(token),),
    )


async def _risk_pause_view(user_id: int, conn: dict | None, ignore_override: bool = False) -> dict | None:
    pauses = await _risk_pause_views(user_id, conn, ignore_override=ignore_override)
    return pauses[0] if pauses else None


async def _risk_pause_views(user_id: int, conn: dict | None, ignore_override: bool = False) -> list[dict]:
    if not conn or not conn.get("bybit_api_key") or not conn.get("bybit_api_secret_encrypted"):
        return []
    strategy_pauses = _strategy_pause_views(user_id, conn, ignore_override=ignore_override)
    grid_stop_pauses = _grid_dca_stop_pause_views(user_id, conn, ignore_override=ignore_override)
    if strategy_pauses or grid_stop_pauses:
        return [*strategy_pauses, *grid_stop_pauses]
    if (conn.get("strategy_code") or settings.DEFAULT_STRATEGY_CODE) in {"market_shock_impulse_v1", "market_shock_reversal_dca_v21"}:
        return []
    generic = await _generic_risk_pause_view(user_id, conn, ignore_override=ignore_override)
    return [generic] if generic else []


async def _generic_risk_pause_view(user_id: int, conn: dict | None, ignore_override: bool = False) -> dict | None:
    if not conn or not conn.get("bybit_api_key") or not conn.get("bybit_api_secret_encrypted"):
        return None
    if not ignore_override and fetch_one(
        "SELECT user_id FROM ai_risk_pause_overrides WHERE user_id=%s AND override_until > NOW()",
        (user_id,),
    ):
        return None

    try:
        secret = decrypt_secret(conn.get("bybit_api_secret_encrypted"))
        wallet = await wallet_balance(conn.get("bybit_api_key") or "", secret)
        balance = extract_usdt_balance(wallet)
        status = await risk_pause_status(conn.get("bybit_api_key") or "", secret, balance)
    except Exception:
        return None
    if not status:
        return None
    return {
        **status,
        "type": status.get("type") or "risk_pause",
        "title": "Пауза риска",
        "button_label": "Запустить стратегию сейчас",
        "remaining_label": _format_duration(int(status.get("remaining_seconds") or 0)),
    }


def _grid_dca_stop_pause_views(user_id: int, conn: dict, ignore_override: bool = False) -> list[dict]:
    strategy_code = conn.get("strategy_code") or settings.DEFAULT_STRATEGY_CODE
    if strategy_code not in {"grid_dca_v2", "grid_dca_v3"}:
        return []
    connection_id = int(conn["id"])
    watchlist = set(_watchlist((_strategy(user_id, connection_id) or {}).get("watchlist") or ""))
    now_ts = datetime.now(timezone.utc).timestamp()
    pauses: list[dict] = []

    for row in _grid_dca_user_pair_stop_rows(user_id, connection_id, strategy_code):
        pair = str(row.get("pair") or "").upper()
        if watchlist and pair not in watchlist:
            continue
        if not ignore_override and _strategy_pause_override_exists(user_id, connection_id, strategy_code, pair):
            continue
        ends_at_ts = float(row.get("last_closed_ts") or 0) + settings.GRID_DCA_PAIR_STOP_COOLDOWN_HOURS * 60 * 60
        if ends_at_ts <= now_ts:
            continue
        side = str(row.get("side") or "").lower()
        remaining = max(0, int(ends_at_ts - now_ts))
        pauses.append({
            "type": "strategy_pause",
            "scope": "pair",
            "pair": pair,
            "title": f"Пауза пары {pair}",
            "button_label": f"Запустить пару {pair}",
            "reason": (
                f"GRID DCA: по {pair} {side} недавно был пробой сетки. "
                "Новые входы по этой паре временно остановлены."
            ),
            "ends_at_ms": int(ends_at_ts * 1000),
            "ends_at": datetime.fromtimestamp(ends_at_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "remaining_seconds": remaining,
            "remaining_label": _format_duration(remaining),
        })

    for row in _grid_dca_global_pair_stop_rows(strategy_code):
        pair = str(row.get("pair") or "").upper()
        if watchlist and pair not in watchlist:
            continue
        if not ignore_override and _strategy_pause_override_exists(user_id, connection_id, strategy_code, pair):
            continue
        ends_at_ts = float(row.get("last_closed_ts") or 0) + settings.GRID_DCA_GLOBAL_PAIR_STOP_COOLDOWN_HOURS * 60 * 60
        if ends_at_ts <= now_ts:
            continue
        side = str(row.get("side") or "").lower()
        remaining = max(0, int(ends_at_ts - now_ts))
        pauses.append({
            "type": "strategy_pause",
            "scope": "pair",
            "pair": pair,
            "title": f"Системная пауза {pair}",
            "button_label": f"Запустить пару {pair}",
            "reason": (
                f"GRID DCA: по {pair} {side} был пробой сетки. "
                "Новые входы по этой паре временно остановлены."
            ),
            "ends_at_ms": int(ends_at_ts * 1000),
            "ends_at": datetime.fromtimestamp(ends_at_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "remaining_seconds": remaining,
            "remaining_label": _format_duration(remaining),
        })

    seen: set[tuple[str, str]] = set()
    unique = []
    for pause in sorted(pauses, key=lambda item: int(item.get("ends_at_ms") or 0), reverse=True):
        key = (pause.get("scope") or "", pause.get("pair") or "")
        if key in seen:
            continue
        seen.add(key)
        unique.append(pause)
    return unique


def _grid_dca_stop_like_sql() -> str:
    return """
        (
          close_reason='stop_loss'
          OR (
            closed_pnl < 0
            AND active_safety_orders > 0
            AND matched_safety_orders >= active_safety_orders
          )
        )
    """


def _grid_dca_user_pair_stop_rows(user_id: int, connection_id: int, strategy_code: str) -> list[dict]:
    minutes = max(1, int(settings.GRID_DCA_PAIR_STOP_COOLDOWN_HOURS * 60))
    return fetch_all(
        f"""
        SELECT pair, side, closed_pnl, UNIX_TIMESTAMP(closed_at) AS last_closed_ts
        FROM ai_site_trade_deals
        WHERE user_id=%s
          AND connection_id <=> %s
          AND strategy_code=%s
          AND status='closed'
          AND closed_at >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
          AND {_grid_dca_stop_like_sql()}
        ORDER BY closed_at DESC
        """,
        (user_id, connection_id, strategy_code, minutes),
    )


def _grid_dca_global_pair_stop_rows(strategy_code: str) -> list[dict]:
    minutes = max(1, int(settings.GRID_DCA_GLOBAL_PAIR_STOP_COOLDOWN_HOURS * 60))
    threshold = max(1, int(settings.GRID_DCA_GLOBAL_PAIR_STOP_THRESHOLD))
    return fetch_all(
        f"""
        SELECT pair, side, COUNT(*) AS stops, COUNT(DISTINCT user_id) AS users,
               SUM(closed_pnl) AS pnl, UNIX_TIMESTAMP(MAX(closed_at)) AS last_closed_ts
        FROM ai_site_trade_deals
        WHERE strategy_code=%s
          AND status='closed'
          AND closed_at >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
          AND {_grid_dca_stop_like_sql()}
        GROUP BY pair, side
        HAVING stops >= %s
        ORDER BY last_closed_ts DESC
        """,
        (strategy_code, minutes, threshold),
    )


def _strategy_pause_override_exists(user_id: int, connection_id: int, strategy_code: str, pair: str) -> bool:
    return bool(fetch_one(
        """
        SELECT id
        FROM ai_strategy_pause_overrides
        WHERE user_id=%s
          AND connection_id <=> %s
          AND strategy_code=%s
          AND pair IN (%s, '*')
          AND override_until > NOW()
        LIMIT 1
        """,
        (user_id, connection_id, strategy_code, pair),
    ))


def _strategy_pause_views(user_id: int, conn: dict, ignore_override: bool = False) -> list[dict]:
    strategy_code = conn.get("strategy_code") or settings.DEFAULT_STRATEGY_CODE
    params: list[object] = [user_id, int(conn["id"]), strategy_code]
    override_filter = ""
    if not ignore_override:
        override_filter = """
          AND NOT EXISTS (
              SELECT 1
              FROM ai_strategy_pause_overrides o
              WHERE o.user_id=p.user_id
                AND o.connection_id <=> p.connection_id
                AND o.strategy_code=p.strategy_code
                AND o.pair=p.pair
                AND o.override_until > NOW()
          )
        """
    rows = fetch_all(
        """
        SELECT p.pair, p.reason, UNIX_TIMESTAMP(p.ends_at) AS ends_at_ts
        FROM ai_strategy_pauses p
        WHERE p.user_id=%s AND p.connection_id <=> %s AND p.strategy_code=%s AND p.ends_at > NOW()
        {override_filter}
        ORDER BY (p.pair='*') DESC, p.ends_at DESC, p.pair ASC
        """.format(override_filter=override_filter),
        tuple(params),
    )
    pauses = []
    for row in rows:
        pair = str(row.get("pair") or "*").upper()
        ends_at_ts = float(row.get("ends_at_ts") or 0)
        remaining = max(0, int(ends_at_ts - datetime.now(timezone.utc).timestamp()))
        is_strategy = pair == "*"
        pauses.append({
            "type": "strategy_pause",
            "scope": "strategy" if is_strategy else "pair",
            "pair": pair,
            "title": "Пауза всей стратегии" if is_strategy else f"Пауза пары {pair}",
            "button_label": "Запустить всю стратегию" if is_strategy else f"Запустить пару {pair}",
            "reason": row.get("reason") or "стратегия временно на паузе",
            "ends_at_ms": int(ends_at_ts * 1000),
            "ends_at": datetime.fromtimestamp(ends_at_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if ends_at_ts else "",
            "remaining_seconds": remaining,
            "remaining_label": _format_duration(remaining),
        })
    return pauses


def _format_duration(seconds: int) -> str:
    minutes = max(1, int((seconds + 59) / 60))
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}ч {mins:02d}м"
    return f"{mins}м"


def _connection_view(conn: dict | None) -> dict | None:
    if not conn:
        return None
    strategy_code = conn.get("strategy_code") or settings.DEFAULT_STRATEGY_CODE
    return {
        **conn,
        "strategy_code": strategy_code,
        "strategy_name": STRATEGIES.get(strategy_code, STRATEGIES[settings.DEFAULT_STRATEGY_CODE]).name,
        "api_key_masked": mask_secret(conn.get("bybit_api_key")),
        "has_secret": bool(conn.get("bybit_api_secret_encrypted")),
        "has_webhook": bool(conn.get("webhook_url_encrypted")),
    }


def _strategy_meta_view(strategy_code: str, lang: str) -> dict:
    code = strategy_code if strategy_code in STRATEGIES else settings.DEFAULT_STRATEGY_CODE
    texts = {
        "ru": {
            "grid_dca_v2": {
                "name": "GRID DCA 2.6",
                "description": "Стратегия стадии рынка: адаптивная сетка усреднения по ATR, тейк-профит, стоп-лосс и лимиты активных сделок.",
            },
            "grid_dca_v3": {
                "name": "GRID DCA 3.1",
                "description": "Админская тестовая версия GRID DCA: более строгие входы, усиленный фильтр BTC/ETH, контроль импульсных свечей против сетки и адаптивная DCA-сетка.",
            },
            "market_shock_impulse_v1": {
                "name": "MarketShok Impulse 2.0",
                "description": "Пробойная стратегия по аномальному импульсу: движение от 3%, всплеск объёма, фильтр спреда и подтверждение продолжения.",
            },
            "market_shock_reversal_dca_v21": {
                "name": "MarketShok Reversal DCA 3.0",
                "description": "Экспериментальная стратегия для администратора: short после резкого роста как контр-импульс и short после резкого падения по продолжению, с адаптивными DCA-сеткой, TP и SL.",
            },
        },
        "en": {
            "grid_dca_v2": {
                "name": "GRID DCA 2.6",
                "description": STRATEGIES["grid_dca_v2"].description,
            },
            "grid_dca_v3": {
                "name": "GRID DCA 3.1",
                "description": STRATEGIES["grid_dca_v3"].description,
            },
            "market_shock_impulse_v1": {
                "name": "MarketShok Impulse 2.0",
                "description": STRATEGIES["market_shock_impulse_v1"].description,
            },
            "market_shock_reversal_dca_v21": {
                "name": "MarketShok Reversal DCA 3.0",
                "description": STRATEGIES["market_shock_reversal_dca_v21"].description,
            },
        },
    }
    text = texts.get(normalize_lang(lang), texts["ru"]).get(code, {})
    return {"code": code, "name": text.get("name", STRATEGIES[code].name), "description": text.get("description", STRATEGIES[code].description)}


def _strategy_card_view(strategy_code: str, lang: str) -> dict:
    code = strategy_code if strategy_code in STRATEGIES else settings.DEFAULT_STRATEGY_CODE
    texts = {
        "ru": {
            "grid_dca_v2": {
                "name": "GRID DCA 2.6",
                "description": "Стратегия стадии рынка: адаптивная сетка усреднения по ATR, тейк-профит, стоп-лосс и лимиты активных сделок.",
                "simple": "Подходит для спокойного рынка, откатов и участков, где цена ходит волнами. Сделка открывается с сеткой страховочных ордеров, чтобы усреднять вход.",
                "profit": "Потенциал: умеренный, рассчитан на частые небольшие сделки.",
                "risk": "Риск: средний. Главная опасность — сильный тренд против сетки.",
            },
            "grid_dca_v3": {
                "name": "GRID DCA 3.1",
                "description": "Более строгая тестовая версия GRID DCA. Доступна только администратору.",
                "simple": "Использует те же пары и общий подход GRID DCA 2.6, но пропускает больше слабых входов: сильнее фильтрует BTC/ETH, объём, ATR, положение Bollinger Bands и импульсные свечи против сетки.",
                "profit": "Потенциал: выше за счёт меньшего количества плохих входов, но сигналов будет меньше.",
                "risk": "Риск: средний. Стратегия всё равно использует DCA и стоп-лосс, поэтому при сильном рынке против сетки возможны убытки.",
            },
            "market_shock_impulse_v1": {
                "name": "MarketShok Impulse 2.0",
                "description": "Пробойная стратегия по аномальному импульсу: движение от 3%, всплеск объёма, фильтр спреда и подтверждение продолжения.",
                "simple": "Ищет резкий импульс на фьючерсах и пытается войти по направлению движения, если объём и лента сделок подтверждают продолжение.",
                "profit": "Потенциал: высокий, но сигналов меньше и они резче.",
                "risk": "Риск: высокий. Главная опасность — ложный пробой и быстрый откат.",
            },
            "market_shock_reversal_dca_v21": {
                "name": "MarketShok Reversal DCA 3.0",
                "description": "Short-biased DCA-стратегия по Market Shock. Доступна только администратору.",
                "simple": "После резкого роста открывает short против перегрева, а после резкого падения открывает short по продолжению импульса. TP, SL и DCA-сетка адаптируются к силе движения.",
                "profit": "Потенциал: высокий по бэктесту, но стратегия экспериментальная и требует отдельного аккаунта.",
                "risk": "Риск: высокий. Главная опасность — резкий отскок против short-позиции или продолжение перегретого движения без отката.",
            },
        },
        "en": {
            "grid_dca_v2": {
                "name": "GRID DCA 2.6",
                "description": STRATEGIES["grid_dca_v2"].description,
                "simple": "Best for calmer markets, pullbacks, and wave-like price action. It opens a deal with safety orders to average the entry.",
                "profit": "Potential: moderate, focused on frequent smaller deals.",
                "risk": "Risk: medium. The main danger is a strong trend against the grid.",
            },
            "grid_dca_v3": {
                "name": "GRID DCA 3.1",
                "description": STRATEGIES["grid_dca_v3"].description,
                "simple": "Uses the same pairs and base idea as GRID DCA 2.6, but applies stricter BTC/ETH, volume, ATR, Bollinger, and impulse-candle filters.",
                "profit": "Potential: higher filtering quality, but fewer signals.",
                "risk": "Risk: medium. It still uses DCA and stop loss, so strong moves against the grid can produce losses.",
            },
            "market_shock_impulse_v1": {
                "name": "MarketShok Impulse 2.0",
                "description": STRATEGIES["market_shock_impulse_v1"].description,
                "simple": "Looks for a sharp futures impulse and enters in the direction of the move when volume and trade flow confirm continuation.",
                "profit": "Potential: high, but signals are rarer and sharper.",
                "risk": "Risk: high. The main danger is a false breakout and fast reversal.",
            },
            "market_shock_reversal_dca_v21": {
                "name": "MarketShok Reversal DCA 3.0",
                "description": STRATEGIES["market_shock_reversal_dca_v21"].description,
                "simple": "Shorts upward overextensions as a counter-impulse setup and shorts downward shocks as continuation. TP, SL, and the DCA grid adapt to impulse strength.",
                "profit": "Potential: high in the backtest, but experimental and best used on a separate account.",
                "risk": "Risk: high. The main danger is a sharp bounce against the short or an overheated move continuing without reversal.",
            },
        },
    }
    text = texts.get(normalize_lang(lang), texts["ru"]).get(code, {})
    return {
        "code": code,
        "name": text.get("name", STRATEGIES[code].name),
        "description": text.get("description", STRATEGIES[code].description),
        "simple": text.get("simple", ""),
        "profit": text.get("profit", ""),
        "risk": text.get("risk", ""),
    }


def _volume_hint(strategy: dict, conn: dict | None, user: dict | None = None) -> dict:
    limits = _plan_limits(user)
    is_admin = str((user or {}).get("role") or "user") == "admin"
    max_first_order = float(limits["max_first_order"])
    first_order = max(float(strategy.get("min_order_volume") or settings.MIN_FIRST_ORDER_VOLUME), settings.MIN_FIRST_ORDER_VOLUME)
    if not is_admin:
        first_order = min(first_order, max_first_order)
    manual_first_order_cap = _manual_first_order_cap(user, conn)
    if limits["code"] != "free" and not is_admin:
        first_order = min(first_order, manual_first_order_cap)
    leverage = 10
    risk_pct = min(float(limits.get("max_risk_pct") or settings.MAX_STRATEGY_RISK_PCT), max(1.0, float(strategy.get("risk_pct") or settings.DEFAULT_RISK_PCT)))
    factor = _typical_safety_factor(settings.TYPICAL_SAFETY_ORDERS, settings.TYPICAL_MARTINGALE_MULTIPLIER)
    balance = float((conn or {}).get("last_balance") or 0)
    calculated_first_order = (balance * (risk_pct / 100.0) * leverage / factor) if balance > 0 and factor > 0 else 0.0
    capped_calculated_first_order = min(calculated_first_order, max_first_order) if calculated_first_order > 0 else 0.0
    total_notional = first_order * factor
    required_margin = total_notional / leverage
    deposit_pct = (required_margin / balance * 100) if balance > 0 else None
    recommended_deposit = required_margin / (risk_pct / 100.0)
    return {
        "first_order": _fmt_fixed(first_order),
        "balance": _fmt_fixed(balance) if balance > 0 else "",
        "safety_orders": settings.TYPICAL_SAFETY_ORDERS,
        "martingale": _fmt_number(settings.TYPICAL_MARTINGALE_MULTIPLIER),
        "martingale_raw": settings.TYPICAL_MARTINGALE_MULTIPLIER,
        "factor": _fmt_fixed(factor),
        "total_notional": _fmt_fixed(total_notional),
        "required_margin": _fmt_fixed(required_margin),
        "deposit_pct": _fmt_fixed(deposit_pct) if deposit_pct is not None else None,
        "recommended_deposit": _fmt_fixed(recommended_deposit),
        "recommended_pct": _fmt_fixed(risk_pct),
        "needs_more_deposit": bool(balance > 0 and recommended_deposit > balance),
        "leverage": leverage,
        "first_order_mode": strategy.get("first_order_mode") or "manual",
        "max_first_order": _fmt_fixed(max_first_order),
        "manual_first_order_cap": _fmt_fixed(manual_first_order_cap),
        "manual_first_order_cap_pct": _fmt_number(settings.MANUAL_FIRST_ORDER_MAX_DEPOSIT_PCT),
        "manual_first_order_cap_has_balance": bool(balance > 0 and not is_admin),
        "manual_first_order_unlimited": is_admin,
        "recommended_balance": _fmt_fixed(float(limits["recommended_balance"])) if limits.get("recommended_balance") else None,
        "calculated_first_order": _fmt_fixed(capped_calculated_first_order),
        "calculated_first_order_raw": calculated_first_order,
        "calculated_first_order_too_low": bool(balance > 0 and calculated_first_order < settings.MIN_FIRST_ORDER_VOLUME),
    }


def _typical_safety_factor(safety_orders: int, multiplier: float) -> float:
    factor = 1.0
    leg = 1.0
    for _ in range(max(safety_orders, 0)):
        factor += leg
        leg *= multiplier
    return factor


def _fmt_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _fmt_fixed(value: float) -> str:
    return f"{float(value):.2f}"


def _signal_view(row: dict, lang: str = "ru", user: dict | None = None) -> dict:
    def loads(value, default):
        try:
            return json.loads(value) if value else default
        except Exception:
            return default
    reasons = loads(row.get("reasons"), [])
    if row.get("error_message"):
        if row.get("status") == "failed":
            reasons = [row["error_message"], *reasons]
        else:
            reasons = [*reasons, row["error_message"]]
    if normalize_lang(lang) == "ru":
        reasons = [_translate_reason(reason) for reason in reasons]
    return {
        **row,
        "created_at_iso": _datetime_to_utc_iso(row.get("created_at")),
        "created_at": _format_user_datetime(row.get("created_at"), user),
        "reasons_list": reasons,
        "payload_obj": loads(row.get("payload"), {}),
        "response_obj": loads(row.get("response"), {}),
    }


def _datetime_to_utc_iso(value) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _prune_user_signals(user_id: int) -> None:
    buckets = fetch_all(
        "SELECT DISTINCT connection_id FROM ai_signals WHERE user_id=%s",
        (user_id,),
    )
    for bucket in buckets:
        connection_id = bucket.get("connection_id")
        execute(
            """
            DELETE FROM ai_signals
            WHERE user_id=%s AND connection_id <=> %s
              AND id NOT IN (
                SELECT id FROM (
                  SELECT id
                  FROM ai_signals
                  WHERE user_id=%s AND connection_id <=> %s
                  ORDER BY created_at DESC, id DESC
                  LIMIT 50
                ) keep_rows
              )
            """,
            (user_id, connection_id, user_id, connection_id),
        )


def _translate_reason(reason: str) -> str:
    replacements = {
        "risk pause: stop-loss close detected in the last 6h": "пауза риска: за последние 6ч найдено закрытие по стоп-лоссу",
        "risk pause: negative closed PnL detected in the last 6h": "пауза риска: за последние 6ч была закрытая убыточная сделка",
        "no GRID DCA setup": "нет условий для сетки DCA",
        "stage:": "стадия:",
        "15m trend:": "тренд 15м:",
        "60m trend:": "тренд 60м:",
        "spread too wide": "слишком широкий спред",
        "volume too low": "слишком низкий объём",
        "volatility too high for DCA grid": "волатильность слишком высокая для сетки DCA",
        "trend continuation short": "продолжение нисходящего тренда",
        "filtered pullback entry": "вход после отката",
        "RSI is not exhausted": "RSI не перепродан",
        "range upper band": "верхняя граница боковика",
        "mean reversion short": "возврат к среднему в шорт",
        "deep mean reversion short": "возврат к среднему в шорт",
        "bull trend pullback": "откат в восходящем тренде",
        "bear trend pullback": "откат в нисходящем тренде",
        "grid can average near support": "сетка может усредняться у поддержки",
        "grid can average near resistance": "сетка может усредняться у сопротивления",
        "ATR grid step:": "шаг сетки по ATR:",
        "TP:": "тейк-профит:",
        "SL:": "стоп-лосс:",
        "uptrend long disabled until pullback": "лонг в тренде отключён до отката",
        "bullish": "бычий",
        "bearish": "медвежий",
        "neutral": "нейтральный",
        "uptrend": "восходящий тренд",
        "downtrend": "нисходящий тренд",
        "range": "боковик",
        "pullback_up": "откат вверх",
        "pullback_down": "откат вниз",
        "quiet": "тихий рынок",
        "mixed": "смешанная картина",
    }
    text = str(reason)
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text
