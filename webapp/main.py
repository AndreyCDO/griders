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
from starlette.requests import ClientDisconnect

import ghost_webhook

from . import settings
from .cryptorg_monitor import closed_pnl_history, closed_pnl_history_page, extract_usdt_balance, open_orders, positions, wallet_balance
from .db import execute, fetch_all, fetch_one, init_db
from .grid_dca_webhook import enqueue_tradingview_grid_dca, next_pending_tradingview_grid_event_id, process_tradingview_grid_dca_event
from .i18n import LANG_COOKIE, normalize_lang, ui
from .launch_guard import release_pair_launch, release_strategy_side_launch, reserve_pair_launch, reserve_strategy_side_launch
from .mailer import send_email_verification, send_password_reset, smtp_configured
from .tariff_bot import handle_tariff_bot_update, sync_user_tariff, tariff_sync_loop, telegram_verify_url
from .trade_stats import process_closed_rows_for_counter, refresh_recent_daily_site_trade_stats, site_totals, trade_analysis_summary
from .trading_controls import clear_side_block, set_side_block, side_block_statuses
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
ADMIN_VIEW_USER_COOKIE = "griders_admin_view_user_id"
DEFAULT_TIMEZONE = "Europe/Moscow"
CONNECTION_VIDEO_RUTUBE_URL = "https://rutube.ru/video/private/40c07f768175b6710b6425150e3b0c86/?p=X1U8JjmvHkPrJIx5tV5VGw"
CONNECTION_VIDEO_YOUTUBE_URL = "https://youtu.be/hLUOPT37f2M"
PUBLIC_ANALYTICS_CACHE_KEY = "public_analytics_v1"
PUBLIC_ANALYTICS_CACHE_SECONDS = 3600
FREE_PLAN_DAYS = 14
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
TRADINGVIEW_GRID_WORKERS = 3
ADMIN_STATS_BACKGROUND_ENABLED = settings.ADMIN_STATS_BACKGROUND_ENABLED
ADMIN_STATS_MANUAL_USER_PAUSE_SECONDS = 3.0
GRIDERS_OPEN_DEAL_SYNC_ENABLED = True
GRIDERS_OPEN_DEAL_SYNC_SECONDS = 900
GRIDERS_OPEN_DEAL_CLEANUP_GRACE_MINUTES = 90
MONITORING_ACCOUNT_CACHE_SECONDS = 180
OPEN_ORDERS_ALERT_CACHE_SECONDS = 300
MONITORING_DATA_START_DATE = datetime(2026, 6, 7).date()
ADMIN_MONITORING_DATA_START_DATE = datetime(2026, 6, 25).date()
PUBLIC_HTTPS_HOSTS = {"griders.ru", "www.griders.ru"}
tradingview_grid_queue: asyncio.Queue[dict] | None = None
admin_stats_refresh_task: asyncio.Task | None = None

PLAN_LIMITS = {
    "free": {
        "code": "free",
        "name_ru": "Бесплатный",
        "name_en": "Free",
        "max_active_deals": 4,
        "max_long_deals": 4,
        "max_short_deals": 4,
        "max_first_order": 6.0,
        "recommended_balance": 50.0,
        "can_use_deposit_pct": False,
        "max_risk_pct": settings.MAX_STRATEGY_RISK_PCT,
        "disabled_pairs": settings.FREE_PLAN_DISABLED_PAIRS,
    },
    "free_plus": {
        "code": "free_plus",
        "name_ru": "Бесплатный Плюс",
        "name_en": "Free Plus",
        "max_active_deals": 6,
        "max_long_deals": 6,
        "max_short_deals": 6,
        "max_first_order": 12.0,
        "recommended_balance": 100.0,
        "can_use_deposit_pct": True,
        "max_risk_pct": 10.0,
        "disabled_pairs": settings.FREE_PLAN_DISABLED_PAIRS,
    },
    "start": {
        "code": "start",
        "name_ru": "Старт",
        "name_en": "Start",
        "max_active_deals": 8,
        "max_long_deals": 8,
        "max_short_deals": 8,
        "max_first_order": 60.0,
        "recommended_balance": 500.0,
        "can_use_deposit_pct": True,
        "max_risk_pct": settings.MAX_STRATEGY_RISK_PCT,
        "disabled_pairs": settings.START_PLAN_DISABLED_PAIRS,
    },
    "start_plus": {
        "code": "start_plus",
        "name_ru": "Старт Плюс",
        "name_en": "Start Plus",
        "max_active_deals": 10,
        "max_long_deals": 10,
        "max_short_deals": 10,
        "max_first_order": 120.0,
        "recommended_balance": 1000.0,
        "can_use_deposit_pct": True,
        "max_risk_pct": settings.MAX_STRATEGY_RISK_PCT,
        "disabled_pairs": set(),
    },
    "premium": {
        "code": "premium",
        "name_ru": "Премиум",
        "name_en": "Premium",
        "max_active_deals": 12,
        "max_long_deals": 12,
        "max_short_deals": 12,
        "max_first_order": 600.0,
        "recommended_balance": None,
        "can_use_deposit_pct": True,
        "max_risk_pct": settings.MAX_STRATEGY_RISK_PCT,
        "disabled_pairs": set(),
    },
    "premium_plus": {
        "code": "premium_plus",
        "name_ru": "Премиум Плюс",
        "name_en": "Premium Plus",
        "max_active_deals": 40,
        "max_long_deals": 20,
        "max_short_deals": 20,
        "max_first_order": 2000.0,
        "recommended_balance": None,
        "can_use_deposit_pct": True,
        "max_risk_pct": 15.0,
        "disabled_pairs": set(),
    },
}
BASE_PLAN_OPTIONS = ("free", "start", "premium")
PLUS_PLAN_BY_BASE = {
    "free": "free_plus",
    "start": "start_plus",
    "premium": "premium_plus",
}
BASE_PLAN_BY_PLUS = {plus: base for base, plus in PLUS_PLAN_BY_BASE.items()}
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


@app.middleware("http")
async def block_admin_view_writes(request: Request, call_next):
    allowed_paths = {"/admin/view/exit", "/logout"}
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path not in allowed_paths and _admin_view_active(request):
        redirect_to = request.headers.get("referer") or "/dashboard"
        return RedirectResponse(redirect_to, status_code=303)
    return await call_next(request)


@app.middleware("http")
async def block_expired_free_plan_writes(request: Request, call_next):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return await call_next(request)
    allowed_paths = {"/logout", "/language"}
    if request.url.path in allowed_paths or request.url.path.startswith("/integrations/"):
        return await call_next(request)
    user = current_user(request)
    if user and _free_plan_status(user)["expired"] and not _user_access(user)["is_admin"]:
        _disable_expired_free_user_strategies(int(user["id"]))
        return RedirectResponse("/tariffs?free_expired=1", status_code=303)
    return await call_next(request)


@app.on_event("startup")
async def on_startup() -> None:
    global tradingview_grid_queue
    init_db()
    _disable_all_expired_free_strategies()
    tradingview_grid_queue = asyncio.Queue(maxsize=TRADINGVIEW_QUEUE_MAXSIZE)
    for worker_id in range(TRADINGVIEW_GRID_WORKERS):
        asyncio.create_task(_tradingview_grid_worker(worker_id + 1))
    if ADMIN_STATS_BACKGROUND_ENABLED:
        asyncio.create_task(_admin_stats_loop())
    if GRIDERS_OPEN_DEAL_SYNC_ENABLED:
        asyncio.create_task(_griders_open_deal_sync_loop())
    if settings.GRID_DCA_ACCOUNT_CACHE_ENABLED:
        asyncio.create_task(_grid_dca_account_cache_loop())
    asyncio.create_task(_daily_trade_stats_loop())
    asyncio.create_task(_free_plan_expiry_loop())
    asyncio.create_task(tariff_sync_loop())


async def _tradingview_grid_worker(worker_id: int) -> None:
    while True:
        if tradingview_grid_queue is None:
            await asyncio.sleep(1)
            continue
        queued_item = False
        try:
            event_id = await asyncio.wait_for(tradingview_grid_queue.get(), timeout=2)
            queued_item = True
        except asyncio.TimeoutError:
            try:
                event_id = next_pending_tradingview_grid_event_id()
            except Exception:
                logger.exception("TradingView GRID DCA worker %s failed to poll pending events", worker_id)
                await asyncio.sleep(2)
                continue
            if event_id is None:
                continue
        try:
            result = await process_tradingview_grid_dca_event(int(event_id))
            logger.info("TradingView GRID DCA queue item processed by worker %s: %s", worker_id, result)
        except Exception:
            logger.exception("TradingView GRID DCA queue item failed in worker %s", worker_id)
        finally:
            if queued_item:
                tradingview_grid_queue.task_done()

async def _grid_dca_account_cache_loop() -> None:
    await asyncio.sleep(10)
    while True:
        try:
            updated = await _refresh_grid_dca_account_cache_once()
            if updated:
                logger.info("Updated GRID DCA account cache for %s connections", updated)
        except Exception:
            logger.exception("GRID DCA account cache refresh failed")
        await asyncio.sleep(max(30, int(settings.GRID_DCA_ACCOUNT_CACHE_SYNC_SECONDS)))


async def _free_plan_expiry_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            _disable_all_expired_free_strategies()
        except Exception:
            logger.exception("Free plan expiry check failed")
        await asyncio.sleep(24 * 60 * 60)


async def _refresh_grid_dca_account_cache_once() -> int:
    rows = fetch_all(
        """
        SELECT DISTINCT c.id, c.user_id, c.bybit_api_key, c.bybit_api_secret_encrypted,
               c.last_risk_pause_snapshot,
               c.last_risk_pause_checked_at,
               c.last_open_orders_snapshot,
               c.last_open_orders_checked_at
        FROM ai_user_connections c
        JOIN ai_user_strategy_settings s ON s.connection_id = c.id
        WHERE c.is_active = 1
          AND COALESCE(c.approval_status, 'approved') = 'approved'
          AND s.enabled = 1
          AND s.auto_trade = 1
          AND s.strategy_code = 'grid_dca_v2'
          AND c.bybit_api_key <> ''
          AND c.bybit_api_secret_encrypted IS NOT NULL
        ORDER BY COALESCE(c.last_positions_checked_at, CAST('1970-01-01 00:00:00' AS DATETIME)) ASC, c.id ASC
        LIMIT %s
        """,
        (max(1, int(settings.GRID_DCA_ACCOUNT_CACHE_BATCH_SIZE)),),
    )
    if not rows:
        return 0
    semaphore = asyncio.Semaphore(max(1, int(settings.GRID_DCA_ACCOUNT_CACHE_CONCURRENCY)))
    updated = 0

    async def refresh(row: dict) -> bool:
        async with semaphore:
            return await _refresh_grid_dca_connection_cache(row)

    results = await asyncio.gather(*(refresh(row) for row in rows), return_exceptions=True)
    for result in results:
        if result is True:
            updated += 1
        elif isinstance(result, Exception):
            logger.warning("GRID DCA account cache worker failed: %s", result)
    return updated


async def _refresh_grid_dca_connection_cache(row: dict) -> bool:
    connection_id = int(row["id"])
    user_id = int(row["user_id"])
    api_key = row.get("bybit_api_key") or ""
    try:
        api_secret = decrypt_secret(row.get("bybit_api_secret_encrypted"))
    except Exception as exc:
        execute(
            "UPDATE ai_user_connections SET last_error=%s, last_checked_at=NOW() WHERE id=%s",
            (str(exc), connection_id),
        )
        return False
    if not api_key or not api_secret:
        return False
    try:
        wallet, position_rows = await asyncio.wait_for(
            asyncio.gather(wallet_balance(api_key, api_secret), positions(api_key, api_secret)),
            timeout=15,
        )
        balance = extract_usdt_balance(wallet)
        open_orders_snapshot = _json_loads(row.get("last_open_orders_snapshot"), {}) or {}
        open_orders_checked_at = _as_utc_datetime(row.get("last_open_orders_checked_at"))
        open_position_pairs = {
            str(position.get("symbol") or "").upper()
            for position in (position_rows or [])
            if _dashboard_position_is_open(position) and str(position.get("symbol") or "").strip()
        }
        cached_order_pairs = set(open_orders_snapshot.keys()) if isinstance(open_orders_snapshot, dict) else set()
        open_orders_checked_now = False
        open_orders_cache_age = (
            (datetime.now(timezone.utc) - open_orders_checked_at).total_seconds()
            if open_orders_checked_at is not None
            else 999999
        )
        if open_orders_cache_age > OPEN_ORDERS_ALERT_CACHE_SECONDS or not open_position_pairs.issubset(cached_order_pairs):
            open_orders_checked_now = True
            open_orders_snapshot = await _open_orders_snapshot_for_positions(api_key, api_secret, position_rows)
        risk_status = _json_loads(row.get("last_risk_pause_snapshot"), {}) or None
        risk_checked_at = _as_utc_datetime(row.get("last_risk_pause_checked_at"))
        risk_cache_stale = risk_checked_at is None or (datetime.now(timezone.utc) - risk_checked_at).total_seconds() > 1800
        risk_checked_now = False
        if risk_cache_stale:
            risk_checked_now = True
            if risk_checked_at is not None:
                try:
                    pause = await asyncio.wait_for(risk_pause_status(api_key, api_secret, balance), timeout=15)
                    risk_status = pause if pause and not _risk_pause_override_active(user_id) else None
                except Exception as exc:
                    logger.warning("GRID DCA risk cache failed for user %s connection %s: %s", user_id, connection_id, exc)
        _store_connection_account_cache(
            connection_id,
            balance,
            position_rows,
            open_orders_snapshot,
            risk_status,
            open_orders_checked=open_orders_checked_now,
            risk_checked=risk_checked_now,
        )
        return True
    except Exception as exc:
        execute(
            "UPDATE ai_user_connections SET last_error=%s, last_checked_at=NOW() WHERE id=%s",
            (str(exc), connection_id),
        )
        return False


async def _open_orders_snapshot_for_positions(api_key: str, api_secret: str, position_rows: list[dict]) -> dict[str, list[dict]]:
    pairs = sorted({
        str(position.get("symbol") or "").upper()
        for position in (position_rows or [])
        if _dashboard_position_is_open(position) and str(position.get("symbol") or "").strip()
    })
    if not pairs:
        return {}

    async def fetch_pair(pair: str) -> tuple[str, list[dict]]:
        try:
            orders = await asyncio.wait_for(open_orders(api_key, api_secret, pair), timeout=8)
            return pair, orders or []
        except Exception as exc:
            logger.warning("GRID DCA open-orders cache failed for %s: %s", pair, exc)
            return pair, []

    results = await asyncio.gather(*(fetch_pair(pair) for pair in pairs))
    return {pair: orders for pair, orders in results}


def _store_connection_account_cache(
    connection_id: int,
    balance: float,
    position_rows: list[dict],
    open_orders_snapshot: dict[str, list[dict]] | None = None,
    risk_status: dict | None = None,
    open_orders_checked: bool = True,
    risk_checked: bool = True,
) -> None:
    execute(
        """
        UPDATE ai_user_connections
        SET last_balance=%s,
            last_error=NULL,
            last_checked_at=NOW(),
            last_positions_snapshot=%s,
            last_positions_checked_at=NOW()
        WHERE id=%s
        """,
        (
            balance,
            json.dumps(position_rows or [], ensure_ascii=False, default=str),
            connection_id,
        ),
    )
    if open_orders_checked:
        execute(
            """
            UPDATE ai_user_connections
            SET last_open_orders_snapshot=%s,
                last_open_orders_checked_at=NOW()
            WHERE id=%s
            """,
            (
                json.dumps(open_orders_snapshot or {}, ensure_ascii=False, default=str),
                connection_id,
            ),
        )
    if risk_checked:
        execute(
            """
            UPDATE ai_user_connections
            SET last_risk_pause_reason=%s,
                last_risk_pause_snapshot=%s,
                last_risk_pause_checked_at=NOW()
            WHERE id=%s
            """,
            (
                (risk_status or {}).get("reason"),
                json.dumps(risk_status or {}, ensure_ascii=False, default=str),
                connection_id,
            ),
        )


def _store_connection_positions_cache(connection_id: int, position_rows: list[dict]) -> None:
    execute(
        """
        UPDATE ai_user_connections
        SET last_positions_snapshot=%s,
            last_positions_checked_at=NOW()
        WHERE id=%s
        """,
        (
            json.dumps(position_rows or [], ensure_ascii=False, default=str),
            connection_id,
        ),
    )


def _risk_pause_override_active(user_id: int) -> bool:
    return bool(fetch_one(
        "SELECT user_id FROM ai_risk_pause_overrides WHERE user_id=%s AND override_until > NOW()",
        (user_id,),
    ))


async def risk_pause_status(api_key: str, api_secret: str, balance: float) -> dict | None:
    if not settings.RISK_GUARD_ENABLED:
        return None
    now_ms = int(time.time() * 1000)
    day_start_ms = now_ms - 24 * 60 * 60 * 1000
    cooldown_start_ms = now_ms - int(settings.STOP_LOSS_COOLDOWN_HOURS * 60 * 60 * 1000)
    daily_rows = await closed_pnl_history(api_key, api_secret, day_start_ms, now_ms)
    pauses: list[dict] = []

    daily_pnl = sum(float(row.get("closedPnl") or 0) for row in daily_rows)
    max_daily_loss = balance * settings.DAILY_LOSS_STOP_PCT / 100.0 if balance > 0 else 0
    if max_daily_loss > 0 and daily_pnl <= -max_daily_loss:
        loss_rows = [row for row in daily_rows if float(row.get("closedPnl") or 0) < 0]
        trigger_ms = max(
            int(row.get("updatedTime") or row.get("createdTime") or now_ms)
            for row in (loss_rows or daily_rows)
        )
        pauses.append({
            "type": "daily_loss",
            "reason": f"пауза риска: суточный закрытый PnL {daily_pnl:.2f} USDT достиг лимита убытка {settings.DAILY_LOSS_STOP_PCT}%",
            "ends_at_ms": trigger_ms + int(settings.DAILY_LOSS_COOLDOWN_HOURS * 60 * 60 * 1000),
        })

    stop_rows = [
        row for row in daily_rows
        if int(row.get("updatedTime") or row.get("createdTime") or 0) >= cooldown_start_ms
        and _looks_like_stop_loss(row)
    ]
    if stop_rows:
        newest = max(int(row.get("updatedTime") or row.get("createdTime") or now_ms) for row in stop_rows)
        pauses.append({
            "type": "stop_loss",
            "reason": f"пауза риска: за последние {settings.STOP_LOSS_COOLDOWN_HOURS:g}ч найдено закрытие по стоп-лоссу",
            "ends_at_ms": newest + int(settings.STOP_LOSS_COOLDOWN_HOURS * 60 * 60 * 1000),
        })

    active = [pause for pause in pauses if pause["ends_at_ms"] > now_ms]
    if not active:
        return None
    status = max(active, key=lambda item: item["ends_at_ms"])
    status["remaining_seconds"] = max(0, int((status["ends_at_ms"] - now_ms) / 1000))
    status["ends_at"] = datetime.fromtimestamp(status["ends_at_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return status


def _looks_like_stop_loss(row: dict) -> bool:
    closed_pnl = float(row.get("closedPnl") or 0)
    order_type = str(row.get("orderType") or "").lower()
    return closed_pnl < 0 and order_type == "market"


def _session_user(request: Request) -> dict | None:
    uid = parse_session(request.cookies.get(settings.SESSION_COOKIE))
    if not uid:
        return None
    return fetch_one("SELECT * FROM ai_users WHERE id=%s", (uid,))


def _admin_view_target_id(request: Request) -> int:
    try:
        return int(request.cookies.get(ADMIN_VIEW_USER_COOKIE) or 0)
    except (TypeError, ValueError):
        return 0


def current_user(request: Request) -> dict | None:
    viewer = _session_user(request)
    if not viewer:
        return None
    if _user_access(viewer)["is_admin"]:
        target_id = _admin_view_target_id(request)
        if target_id and target_id != int(viewer["id"]):
            target = fetch_one("SELECT * FROM ai_users WHERE id=%s", (target_id,))
            if target:
                return target
    return viewer


def _admin_view_context(request: Request) -> dict:
    viewer = _session_user(request)
    target = current_user(request)
    active = bool(
        viewer
        and target
        and _user_access(viewer)["is_admin"]
        and int(viewer["id"]) != int(target["id"])
        and _admin_view_target_id(request) == int(target["id"])
    )
    return {"active": active, "viewer": viewer, "target": target}


def _admin_view_active(request: Request) -> bool:
    return bool(_admin_view_context(request)["active"])


def require_user(request: Request) -> dict | RedirectResponse:
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not _admin_view_active(request):
        _apply_user_plan_constraints(user)
        if _free_plan_status(user)["expired"]:
            _disable_expired_free_user_strategies(int(user["id"]))
    return user


def render(request: Request, template: str, context: dict | None = None, status_code: int = 200) -> HTMLResponse:
    lang = _lang(request)
    viewer = _session_user(request)
    user = current_user(request)
    admin_view = _admin_view_context(request)
    data = {
        "request": request,
        "app_name": settings.APP_NAME,
        "user": user,
        "user_access": _user_access(user),
        "free_plan_status": _free_plan_status(user),
        "account_readonly": bool(_free_plan_status(user)["expired"] or admin_view["active"]),
        "viewer_user": viewer,
        "viewer_access": _user_access(viewer),
        "admin_view": admin_view,
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


def _base_plan(plan: str | None) -> str:
    plan_code = _normalized_plan(plan)
    return BASE_PLAN_BY_PLUS.get(plan_code, plan_code)


def _referral_verified(user: dict | None) -> bool:
    try:
        return bool(int((user or {}).get("referral_verified") or 0))
    except (TypeError, ValueError):
        return False


def _effective_plan(user: dict | None) -> str:
    if str((user or {}).get("role") or "user") == "admin":
        return "premium_plus"
    plan = _base_plan((user or {}).get("plan"))
    if _referral_verified(user):
        return PLUS_PLAN_BY_BASE.get(plan, plan)
    return plan


def _free_plan_status(user: dict | None) -> dict:
    if not user or str((user or {}).get("role") or "user") == "admin" or _effective_plan(user) != "free":
        return {"applies": False, "expired": False, "days_left": None, "seconds_left": None, "expires_at": None}
    started_at = _as_utc_datetime((user or {}).get("free_plan_started_at") or (user or {}).get("created_at"))
    expires_at = started_at + timedelta(days=FREE_PLAN_DAYS)
    now = datetime.now(timezone.utc)
    seconds_left = max(0, int((expires_at - now).total_seconds()))
    days_left = 0 if seconds_left <= 0 else max(1, (seconds_left + 86399) // 86400)
    return {
        "applies": True,
        "expired": seconds_left <= 0,
        "days_left": int(days_left),
        "seconds_left": seconds_left,
        "expires_at": expires_at,
    }


def _disable_expired_free_user_strategies(user_id: int) -> None:
    execute(
        """
        UPDATE ai_user_strategy_settings
        SET enabled=0, auto_trade=0
        WHERE user_id=%s AND (enabled<>0 OR auto_trade<>0)
        """,
        (int(user_id),),
    )


def _disable_all_expired_free_strategies() -> None:
    execute(
        """
        UPDATE ai_user_strategy_settings s
        JOIN ai_users u ON u.id = s.user_id
        SET s.enabled=0, s.auto_trade=0
        WHERE u.role<>'admin'
          AND u.plan='free'
          AND COALESCE(u.referral_verified, 0)=0
          AND COALESCE(u.free_plan_started_at, u.created_at) <= DATE_SUB(NOW(), INTERVAL 14 DAY)
          AND (s.enabled<>0 OR s.auto_trade<>0)
        """
    )


def _plan_limits(user: dict | None) -> dict:
    if str((user or {}).get("role") or "user") == "admin":
        return dict(ADMIN_PLAN_LIMITS)
    return dict(PLAN_LIMITS[_effective_plan(user)])


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


def _is_free_plus_plan(user: dict | None) -> bool:
    return _effective_plan(user) == "free_plus"


def _free_plus_first_order_cap(user: dict | None, conn: dict | None) -> float:
    if not _is_free_plus_plan(user):
        return float(_plan_limits(user).get("max_first_order") or settings.MIN_FIRST_ORDER_VOLUME)
    balance = float((conn or {}).get("last_balance") or 0)
    if balance <= 0:
        return settings.MIN_FIRST_ORDER_VOLUME
    deposit_cap = balance * (settings.MANUAL_FIRST_ORDER_MAX_DEPOSIT_PCT / 100.0)
    return max(settings.MIN_FIRST_ORDER_VOLUME, min(float(_plan_limits(user)["max_first_order"]), deposit_cap))


def _strategy_minimum_margin_status(user: dict | None, conn: dict | None, strategy: dict | None) -> dict:
    balance = max(0.0, float((conn or {}).get("last_balance") or 0))
    leverage = max(1, int((strategy or {}).get("leverage") or 10))
    limits = _plan_limits(user)
    is_admin = str((user or {}).get("role") or "user") == "admin"
    max_first_order = float(limits.get("max_first_order") or settings.MIN_FIRST_ORDER_VOLUME)
    first_order = max(float((strategy or {}).get("min_order_volume") or settings.MIN_FIRST_ORDER_VOLUME), settings.MIN_FIRST_ORDER_VOLUME)
    if not is_admin:
        first_order = min(first_order, max_first_order)
    required_margin = first_order / leverage
    return {
        "can_open": bool(balance > 0 and required_margin > 0 and balance >= required_margin),
        "balance": balance,
        "first_order": first_order,
        "required_margin": required_margin,
    }


def _plan_label(plan: str | None, lang: str = "ru") -> str:
    limits = PLAN_LIMITS.get(_normalized_plan(plan), PLAN_LIMITS["free"])
    return limits["name_ru"] if normalize_lang(lang) == "ru" else limits["name_en"]


def _user_plan_label(user: dict | None, lang: str = "ru") -> str:
    limits = PLAN_LIMITS.get(_effective_plan(user), PLAN_LIMITS["free"])
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
    plan = _effective_plan(user)
    base_plan = _base_plan((user or {}).get("plan"))
    referral_verified = _referral_verified(user)
    is_admin = role == "admin"
    limits = _plan_limits(user)
    free_plan_status = _free_plan_status(user)
    return {
        "role": role,
        "plan": plan,
        "base_plan": base_plan,
        "plan_label": limits["name_ru"],
        "plan_limits": limits,
        "referral_verified": referral_verified,
        "is_admin": is_admin,
        "is_premium": is_admin or plan in {"premium", "premium_plus"},
        "is_start": bool(user) and plan in {"start", "start_plus"},
        "is_free": bool(user) and plan == "free" and not is_admin,
        "is_paid": bool(user) and plan in {"start", "start_plus", "premium", "premium_plus"},
        "free_plan_status": free_plan_status,
        "free_plan_expired": bool(free_plan_status["expired"]),
    }


def _available_strategies(user: dict | None) -> dict:
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
    return len(existing) < _connection_limit(user)


def _connection_limit(user: dict | None) -> int:
    if _user_access(user)["is_admin"]:
        return 100
    return 3 if _effective_plan(user) == "premium_plus" else 1


def _premium_plus_connections_unlocked(user: dict | None) -> bool:
    return bool(user) and _effective_plan(user) == "premium_plus"


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
    profile_user["plan_label_ru"] = _user_plan_label(user, "ru")
    profile_user["plan_label_en"] = _user_plan_label(user, "en")
    return {
        "profile_user": profile_user,
        "success": success,
        "error": error,
        "totp_setup": totp_setup,
        "telegram_verify_url": telegram_verify_url(int(user["id"])) if profile_user.get("telegram_username") else "",
        "timezone_options": _timezone_options(),
    }


def _admin_analytics_context(user: dict, success: str = "", error: str = "") -> dict:
    profile_user = dict(user)
    profile_user["timezone"] = _user_timezone_name(user)
    profile_user["created_at_display"] = _format_user_datetime(user.get("created_at"), user)
    profile_user["twofa_enabled"] = _twofa_enabled(user)
    profile_user["plan_label_ru"] = _user_plan_label(user, "ru")
    profile_user["plan_label_en"] = _user_plan_label(user, "en")
    admin_users = _admin_user_views(user)
    admin_trade_analysis = _admin_trade_analysis(user)
    admin_griders_chart_rows = _griders_trade_chart_rows()
    return {
        "profile_user": profile_user,
        "success": success,
        "error": error,
        "admin_users": admin_users,
        "admin_users_totals": _admin_user_totals(admin_users),
        "admin_pending_connections": _admin_pending_connection_views(),
        "admin_site_stats": _site_stats_view(),
        "admin_trade_analysis": admin_trade_analysis,
        "admin_trade_analysis_totals": _admin_trade_analysis_totals(admin_trade_analysis),
        "admin_griders_trades_totals": _griders_trade_totals_from_db(),
        "admin_griders_trades_chart_json": _griders_trade_chart_json(admin_griders_chart_rows, user, start_date=ADMIN_MONITORING_DATA_START_DATE),
        "tariffs": PLAN_LIMITS,
        "base_tariff_codes": BASE_PLAN_OPTIONS,
    }


def _admin_controls_context(user: dict, success: str = "", error: str = "") -> dict:
    statuses = []
    now = datetime.now(timezone.utc)
    for row in side_block_statuses():
        blocked_until = _as_utc_datetime(row.get("blocked_until"))
        remaining = int(row.get("remaining_seconds") or 0)
        statuses.append(
            {
                **row,
                "label": "Лонги" if row["side"] == "long" else "Шорты",
                "action_label": "Не открывать лонги" if row["side"] == "long" else "Не открывать шорты",
                "active": bool(row.get("active")) and blocked_until is not None and blocked_until > now,
                "blocked_until_display": _format_user_datetime(blocked_until, user) if blocked_until else "—",
                "remaining_label": _format_duration(remaining),
            }
        )
    return {
        "profile_user": user,
        "success": success,
        "error": error,
        "side_blocks": statuses,
    }


def _public_analytics_data() -> dict:
    row = fetch_one(
        """
        SELECT cache_value
        FROM ai_public_cache
        WHERE cache_key=%s
          AND TIMESTAMPDIFF(SECOND, updated_at, UTC_TIMESTAMP()) < %s
        """,
        (PUBLIC_ANALYTICS_CACHE_KEY, PUBLIC_ANALYTICS_CACHE_SECONDS),
    )
    if row and row.get("cache_value"):
        try:
            cached = json.loads(str(row["cache_value"]))
            if isinstance(cached, dict):
                return cached
        except Exception:
            logger.warning("Invalid public analytics cache payload; rebuilding")

    trade_analysis = _trade_analysis_view()
    data = {
        "public_site_stats": _site_stats_view(),
        "public_trade_analysis": trade_analysis,
        "public_trade_analysis_totals": _admin_trade_analysis_totals(trade_analysis),
        "public_griders_trades_totals": _griders_trade_totals_from_db(),
        "public_griders_trades_chart_json": _griders_trade_chart_json(_griders_trade_chart_rows(), None),
    }
    execute(
        """
        INSERT INTO ai_public_cache (cache_key, cache_value, updated_at)
        VALUES (%s, %s, UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE
            cache_value=VALUES(cache_value),
            updated_at=UTC_TIMESTAMP()
        """,
        (PUBLIC_ANALYTICS_CACHE_KEY, json.dumps(data, ensure_ascii=False, default=str)),
    )
    return data


def _public_analytics_context(user: dict | None = None) -> dict:
    data = _public_analytics_data()
    return {
        "public_site_stats": data["public_site_stats"],
        "public_trade_analysis": data["public_trade_analysis"],
        "public_trade_analysis_totals": data["public_trade_analysis_totals"],
        "public_griders_trades_totals": data["public_griders_trades_totals"],
        "public_griders_trades_chart_json": data["public_griders_trades_chart_json"],
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


def _site_stats_view() -> dict:
    totals = site_totals()
    return {
        "users_count": totals["users_count"],
        "active_users_count": totals["active_users_count"],
        "deals_count": totals["deals_count"],
        "traded_volume_raw": totals["traded_volume"],
        "traded_volume": _fmt_money(totals["traded_volume"]),
        "counted_from": "08.06.2026",
    }


def _admin_site_stats(user: dict) -> dict:
    if not _user_access(user)["is_admin"]:
        return {}
    return _site_stats_view()


def _trade_analysis_view() -> list[dict]:
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
            "side_label": _trade_side_label(row["side"]),
            "close_reason_label": _trade_close_reason_label(row["close_reason"]),
        })
    return rows


def _admin_trade_analysis(user: dict) -> list[dict]:
    if not _user_access(user)["is_admin"]:
        return []
    return _trade_analysis_view()


def _admin_trade_analysis_totals(rows: list[dict]) -> dict:
    trades = sum(int(row.get("trades_count") or 0) for row in rows)
    wins = sum(int(row.get("wins_count") or 0) for row in rows)
    total_pnl = sum(_float(row.get("total_pnl")) for row in rows)
    avg_pnl = total_pnl / trades if trades else 0.0
    avg_roi = (
        sum(_float(row.get("avg_roi_pct")) * int(row.get("trades_count") or 0) for row in rows) / trades
        if trades else 0.0
    )
    avg_r = (
        sum(_float(row.get("avg_r_multiple")) * int(row.get("trades_count") or 0) for row in rows) / trades
        if trades else 0.0
    )
    avg_hold = (
        sum(int(row.get("avg_hold_seconds") or 0) * int(row.get("trades_count") or 0) for row in rows) / trades
        if trades else 0.0
    )
    return {
        "trades_count": trades,
        "win_rate_text": _fmt_percent((wins / trades * 100) if trades else 0.0),
        "total_pnl": total_pnl,
        "total_pnl_text": _fmt_money(total_pnl),
        "avg_pnl_text": _fmt_money(avg_pnl),
        "avg_roi_text": _fmt_percent(avg_roi),
        "avg_r_text": f"{avg_r:.2f}",
        "avg_hold_text": _fmt_duration(int(avg_hold)),
        "pnl_class": "positive" if total_pnl > 0 else ("negative" if total_pnl < 0 else ""),
    }


def _trade_side_label(side: str) -> str:
    return {"long": "Лонг", "short": "Шорт"}.get(str(side or "").lower(), str(side or "—"))


def _trade_close_reason_label(reason: str) -> str:
    return {
        "take_profit": "Тейк-профит",
        "stop_loss": "Стоп-лосс",
    }.get(str(reason or "").lower(), str(reason or "—"))


def _admin_user_views(user: dict) -> list[dict]:
    if not _user_access(user)["is_admin"]:
        return []
    rows = fetch_all(
        """
        SELECT u.id, u.email, u.nickname, u.telegram_username, u.telegram_user_id, u.role, u.plan,
               u.referral_verified, u.created_at,
               COALESCE(g.cumulative_pnl, 0) AS cumulative_pnl,
               COALESCE(g.closed_trades_count, 0) AS closed_trades_count,
               COALESCE(g.closed_entry_volume, 0) AS closed_entry_volume,
               COALESCE(v.traded_volume_30d, 0) AS traded_volume_30d,
               COALESCE(s.connection_status, 'missing') AS connection_status,
               s.pnl_calculated_at, s.status_checked_at
        FROM ai_users u
        LEFT JOIN ai_user_admin_stats s ON s.user_id = u.id
        LEFT JOIN (
            SELECT user_id,
                   COALESCE(SUM(closed_pnl), 0) AS cumulative_pnl,
                   COUNT(*) AS closed_trades_count,
                   COALESCE(SUM(
                       COALESCE(api_entry_value, 0)
                       + CASE
                           WHEN ABS(COALESCE(qty, 0) * COALESCE(avg_exit_price, 0)) > 0
                             THEN ABS(COALESCE(qty, 0) * COALESCE(avg_exit_price, 0))
                           WHEN COALESCE(api_entry_value, 0) > 0
                             THEN COALESCE(api_entry_value, 0)
                           ELSE 0
                         END
                   ), 0) AS closed_entry_volume
            FROM ai_site_trade_deals
            WHERE status='closed'
            GROUP BY user_id
        ) g ON g.user_id = u.id
        LEFT JOIN (
            SELECT user_id,
                   COALESCE(SUM(
                       COALESCE(api_entry_value, 0)
                       + CASE
                           WHEN ABS(COALESCE(qty, 0) * COALESCE(avg_exit_price, 0)) > 0
                             THEN ABS(COALESCE(qty, 0) * COALESCE(avg_exit_price, 0))
                           WHEN COALESCE(api_entry_value, 0) > 0
                             THEN COALESCE(api_entry_value, 0)
                           ELSE 0
                         END
                   ), 0) AS traded_volume_30d
            FROM ai_site_trade_deals
            WHERE status='closed'
              AND closed_at >= UTC_TIMESTAMP() - INTERVAL 30 DAY
            GROUP BY user_id
        ) v ON v.user_id = u.id
        ORDER BY u.created_at ASC, u.id ASC
        """
    )
    result = []
    for row in rows:
        status = row.get("connection_status") or "missing"
        pnl = _float(row.get("cumulative_pnl"))
        row_user = dict(row)
        result.append({
            **row,
            "created_at_display": _format_user_datetime(row.get("created_at"), user),
            "created_date_display": _local_date_from_utc(row.get("created_at"), user).strftime("%d.%m.%y"),
            "plan": _base_plan(row.get("plan")),
            "effective_plan": _effective_plan(row_user),
            "plan_label": _user_plan_label(row_user, "ru"),
            "referral_verified": _referral_verified(row_user),
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
            "traded_volume_30d_raw": _float(row.get("traded_volume_30d")),
            "traded_volume_30d": _fmt_money(row.get("traded_volume_30d")),
            "pnl_class": "positive" if pnl > 0 else ("negative" if pnl < 0 else ""),
        })
    return result


def _admin_user_totals(rows: list[dict]) -> dict:
    active_users = sum(1 for row in rows if row.get("connection_status") == "active")
    total_pnl = sum(_float(row.get("cumulative_pnl_raw")) for row in rows)
    traded_volume_30d = sum(_float(row.get("traded_volume_30d_raw")) for row in rows)
    return {
        "users_count": len(rows),
        "active_users_count": active_users,
        "total_pnl": total_pnl,
        "total_pnl_text": _fmt_money(total_pnl),
        "traded_volume_30d": traded_volume_30d,
        "traded_volume_30d_text": _fmt_money(traded_volume_30d),
        "pnl_class": "positive" if total_pnl > 0 else ("negative" if total_pnl < 0 else ""),
    }


def _admin_pending_connection_views() -> list[dict]:
    rows = fetch_all(
        """
        SELECT c.id, c.user_id, c.label, c.strategy_code, c.bybit_api_key, c.created_at,
               u.email, u.nickname, u.telegram_username, u.plan, COALESCE(u.referral_verified, 0) AS referral_verified
        FROM ai_user_connections c
        JOIN ai_users u ON u.id = c.user_id
        WHERE c.is_active=1
          AND COALESCE(c.approval_status, 'approved') = 'pending'
        ORDER BY c.created_at ASC, c.id ASC
        """
    )
    return [
        {
            **row,
            "api_key_masked": mask_secret(row.get("bybit_api_key")),
            "strategy_name": STRATEGIES.get(row.get("strategy_code") or settings.DEFAULT_STRATEGY_CODE, STRATEGIES[settings.DEFAULT_STRATEGY_CODE]).name,
            "created_at_display": _format_user_datetime(row.get("created_at"), None),
        }
        for row in rows
    ]


def _admin_griders_trade_rows(user: dict) -> list[dict]:
    if not _user_access(user)["is_admin"]:
        return []
    rows = fetch_all(
        """
        SELECT d.id, d.closed_at, d.pair, d.side, d.closed_pnl, d.strategy_code,
               u.email, u.nickname
        FROM ai_site_trade_deals d
        JOIN ai_users u ON u.id = d.user_id
        WHERE d.status='closed' AND d.closed_at IS NOT NULL
        ORDER BY d.closed_at DESC, d.id DESC
        """
    )
    result = []
    for row in rows:
        pnl = _float(row.get("closed_pnl"))
        closed_at = _as_utc_datetime(row.get("closed_at"))
        result.append({
            "id": int(row.get("id") or 0),
            "closed_at": closed_at,
            "date_display": _format_user_datetime(closed_at, user),
            "user_display": row.get("nickname") or row.get("email") or "—",
            "pair": str(row.get("pair") or "").upper(),
            "side": str(row.get("side") or "").lower(),
            "side_label": _trade_side_label(str(row.get("side") or "")),
            "pnl": pnl,
            "pnl_text": _fmt_money(pnl),
            "pnl_class": "positive" if pnl > 0 else ("negative" if pnl < 0 else ""),
            "strategy": _griders_trade_strategy_label(str(row.get("strategy_code") or "")),
        })
    return result


def _admin_griders_trade_totals(rows: list[dict]) -> dict:
    total_pnl = sum(_float(row.get("pnl")) for row in rows)
    return {
        "trades_count": len(rows),
        "total_pnl": total_pnl,
        "total_pnl_text": _fmt_money(total_pnl),
        "pnl_class": "positive" if total_pnl > 0 else ("negative" if total_pnl < 0 else ""),
    }


def _griders_trade_totals_from_db() -> dict:
    row = fetch_one(
        """
        SELECT COUNT(*) AS trades_count, COALESCE(SUM(closed_pnl), 0) AS total_pnl
        FROM ai_site_trade_deals
        WHERE status='closed' AND closed_at IS NOT NULL
        """
    ) or {}
    total_pnl = _float(row.get("total_pnl"))
    return {
        "trades_count": int(row.get("trades_count") or 0),
        "total_pnl": total_pnl,
        "total_pnl_text": _fmt_money(total_pnl),
        "pnl_class": "positive" if total_pnl > 0 else ("negative" if total_pnl < 0 else ""),
    }


def _admin_griders_trade_totals_from_db(user: dict) -> dict:
    if not _user_access(user)["is_admin"]:
        return _admin_griders_trade_totals([])
    return _griders_trade_totals_from_db()


def _griders_trade_chart_rows() -> list[dict]:
    rows = fetch_all(
        """
        SELECT closed_at, closed_pnl AS pnl
        FROM ai_site_trade_deals
        WHERE status='closed' AND closed_at IS NOT NULL
        ORDER BY closed_at ASC, id ASC
        """
    )
    return [
        {
            "closed_at": _as_utc_datetime(row.get("closed_at")),
            "pnl": _float(row.get("pnl")),
        }
        for row in rows
    ]


def _admin_griders_trade_chart_rows(user: dict) -> list[dict]:
    if not _user_access(user)["is_admin"]:
        return []
    return _griders_trade_chart_rows()


def _griders_trade_chart_json(rows: list[dict], user: dict | None, start_date=None) -> str:
    user_tz = _user_zone(user)
    sorted_rows = sorted(rows, key=lambda item: item["closed_at"])
    start = start_date or datetime(2026, 6, 7).date()
    end = max(datetime.now(user_tz).date(), sorted_rows[-1]["closed_at"].astimezone(user_tz).date()) if sorted_rows else datetime.now(user_tz).date()
    days = {}
    current = start
    while current <= end:
        days[current.isoformat()] = {
            "date": current.isoformat(),
            "label": current.strftime("%d.%m"),
            "pnl": 0.0,
            "trades": 0,
        }
        current += timedelta(days=1)
    for row in sorted_rows:
        key = row["closed_at"].astimezone(user_tz).date().isoformat()
        if key not in days:
            continue
        days[key]["pnl"] += _float(row.get("pnl"))
        days[key]["trades"] += 1
    cumulative = 0.0
    chart = []
    for item in days.values():
        cumulative += item["pnl"]
        chart.append({
            "date": item["date"],
            "label": item["label"],
            "pnl": round(float(item["pnl"]), 6),
            "pnlText": _fmt_money(item["pnl"]) + " USDT",
            "cumulative": round(float(cumulative), 6),
            "cumulativeText": _fmt_money(cumulative) + " USDT",
            "trades": int(item["trades"]),
        })
    return json.dumps(chart, ensure_ascii=False)


def _admin_griders_trade_chart_json(rows: list[dict], user: dict) -> str:
    return _griders_trade_chart_json(rows, user)


def _griders_trade_strategy_label(strategy_code: str) -> str:
    return "GRID DCA 2.9"


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
        SELECT u.id, u.created_at, u.role, u.plan, COALESCE(u.referral_verified, 0) AS referral_verified,
               s.pnl_calculated_at, s.status_checked_at
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
    await asyncio.sleep(900)
    while True:
        try:
            await _refresh_admin_user_stats()
        except Exception as exc:
            logger.warning("admin stats loop failed: %s", exc)
        await asyncio.sleep(max(300, int(settings.ADMIN_STATS_REFRESH_SECONDS)))


async def _daily_trade_stats_loop() -> None:
    await asyncio.sleep(600)
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
               COALESCE(s.auto_trade, 0) AS auto_trade,
               COALESCE(s.risk_pct, %s) AS risk_pct,
               COALESCE(s.min_order_volume, %s) AS min_order_volume,
               COALESCE(s.first_order_mode, 'manual') AS first_order_mode,
               COALESCE(s.leverage, 10) AS leverage,
               COALESCE(s.max_active_deals, 0) AS max_active_deals,
               COALESCE(s.max_long_deals, 0) AS max_long_deals,
               COALESCE(s.max_short_deals, 0) AS max_short_deals,
               COALESCE(s.watchlist, '') AS watchlist
        FROM ai_user_connections c
        LEFT JOIN ai_user_strategy_settings s ON s.connection_id = c.id
        WHERE c.user_id=%s AND c.is_active=1
          AND COALESCE(c.approval_status, 'approved') = 'approved'
        ORDER BY c.id
        """,
        (settings.DEFAULT_RISK_PCT, settings.DEFAULT_MIN_ORDER_VOLUME, user_id),
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
        conn_with_balance = {**conn, "last_balance": balance}
        margin_status = _strategy_minimum_margin_status(user_row, conn_with_balance, conn)
        strategy_is_running = int(conn.get("strategy_enabled") or 0) == 1 and int(conn.get("auto_trade") or 0) == 1
        if strategy_is_running and margin_status["can_open"]:
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
    return render(request, "landing.html", _public_analytics_context())


@app.get("/healthz")
async def healthz():
    return {"ok": True}


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
    try:
        payload = await request.json()
    except ClientDisconnect:
        logger.warning("TradingView GRID DCA webhook client disconnected before payload was read")
        return Response(status_code=499)
    if tradingview_grid_queue is None:
        logger.error("TradingView GRID DCA queue is not initialized")
        return Response(status_code=503)
    result = enqueue_tradingview_grid_dca(payload)
    event_id = result.get("event_id")
    queued_in_memory = False
    try:
        if event_id and result.get("queued"):
            tradingview_grid_queue.put_nowait(int(event_id))
            queued_in_memory = True
    except asyncio.QueueFull:
        logger.warning("TradingView GRID DCA in-memory queue is full; persisted event %s will be picked up by polling", event_id)
    return {
        "ok": True,
        "queued": bool(result.get("queued")),
        "persisted": bool(event_id),
        "memory_queued": queued_in_memory,
        "queue_size": tradingview_grid_queue.qsize(),
        "queue_maxsize": TRADINGVIEW_QUEUE_MAXSIZE,
        "reason": result.get("reason"),
    }


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
        (email, password_hash, role, plan, free_plan_started_at, personal_data_consent_at, terms_accepted_at)
        VALUES (%s, %s, 'user', 'free', NOW(), NOW(), NOW())
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
    response.delete_cookie(ADMIN_VIEW_USER_COOKIE)
    return response


@app.get("/login/2fa", response_class=HTMLResponse)
async def login_2fa_page(request: Request):
    if current_user(request):
        response = RedirectResponse("/dashboard", status_code=303)
        response.delete_cookie(TWOFA_PENDING_COOKIE)
        return response
    uid = parse_pending_2fa(request.cookies.get(TWOFA_PENDING_COOKIE))
    if not uid:
        return RedirectResponse("/login", status_code=303)
    user = fetch_one("SELECT * FROM ai_users WHERE id=%s", (uid,))
    if not user or not _twofa_enabled(user):
        return RedirectResponse("/login", status_code=303)
    return render(request, "login_2fa.html", {"method": user.get("twofa_method")})


@app.post("/login/2fa")
async def login_2fa(request: Request, code: str = Form(...)):
    if current_user(request):
        response = RedirectResponse("/dashboard", status_code=303)
        response.delete_cookie(TWOFA_PENDING_COOKIE)
        return response
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
    response.delete_cookie(ADMIN_VIEW_USER_COOKIE)
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(settings.SESSION_COOKIE)
    response.delete_cookie(TWOFA_PENDING_COOKIE)
    response.delete_cookie(ADMIN_VIEW_USER_COOKIE)
    response.delete_cookie(SELECTED_CONNECTION_COOKIE)
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


@app.get("/admin/analytics", response_class=HTMLResponse)
async def admin_analytics_page(request: Request):
    user = _session_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not _user_access(user)["is_admin"]:
        return RedirectResponse("/dashboard", status_code=303)
    success = request.query_params.get("success") or ""
    return render(request, "admin_analytics.html", _admin_analytics_context(user, success=success))


@app.get("/admin/controls", response_class=HTMLResponse)
async def admin_controls_page(request: Request):
    user = _session_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not _user_access(user)["is_admin"]:
        return RedirectResponse("/dashboard", status_code=303)
    success = request.query_params.get("success") or ""
    error = request.query_params.get("error") or ""
    return render(request, "admin_controls.html", _admin_controls_context(user, success=success, error=error))


@app.post("/admin/controls/side-block")
async def admin_set_side_block(request: Request, side: str = Form(...), hours: str = Form(...)):
    user = _session_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not _user_access(user)["is_admin"]:
        return RedirectResponse("/dashboard", status_code=303)
    try:
        value = float(str(hours).replace(",", "."))
        if value <= 0:
            raise ValueError
        set_side_block(side, value, int(user["id"]))
    except Exception:
        return RedirectResponse("/admin/controls?error=bad_hours", status_code=303)
    return RedirectResponse(f"/admin/controls?success={side}_blocked", status_code=303)


@app.post("/admin/controls/side-block/clear")
async def admin_clear_side_block(request: Request, side: str = Form(...)):
    user = _session_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not _user_access(user)["is_admin"]:
        return RedirectResponse("/dashboard", status_code=303)
    try:
        clear_side_block(side)
    except Exception:
        return RedirectResponse("/admin/controls?error=bad_side", status_code=303)
    return RedirectResponse(f"/admin/controls?success={side}_cleared", status_code=303)


@app.get("/admin/users/{target_user_id}/view")
async def admin_view_user_profile(request: Request, target_user_id: int):
    viewer = _session_user(request)
    if not viewer:
        return RedirectResponse("/login", status_code=303)
    if not _user_access(viewer)["is_admin"]:
        return RedirectResponse("/dashboard", status_code=303)
    target = fetch_one("SELECT id FROM ai_users WHERE id=%s", (int(target_user_id),))
    if not target:
        return RedirectResponse("/admin/analytics", status_code=303)
    response = RedirectResponse("/dashboard", status_code=303)
    if int(target["id"]) == int(viewer["id"]):
        response.delete_cookie(ADMIN_VIEW_USER_COOKIE)
        return response
    response.set_cookie(
        ADMIN_VIEW_USER_COOKIE,
        str(int(target["id"])),
        max_age=settings.SESSION_IDLE_TIMEOUT_SECONDS,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(SELECTED_CONNECTION_COOKIE)
    return response


@app.post("/admin/view/exit")
async def admin_view_exit(request: Request):
    viewer = _session_user(request)
    if not viewer:
        response = RedirectResponse("/login", status_code=303)
    elif not _user_access(viewer)["is_admin"]:
        response = RedirectResponse("/dashboard", status_code=303)
    else:
        response = RedirectResponse("/profile", status_code=303)
    response.delete_cookie(ADMIN_VIEW_USER_COOKIE)
    response.delete_cookie(SELECTED_CONNECTION_COOKIE)
    return response


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
            free_plan_started_at=IF(role='admin', free_plan_started_at, NOW()),
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
    rows = fetch_all("SELECT id, role, plan FROM ai_users ORDER BY created_at DESC, id DESC")
    for row in rows:
        user_id = int(row["id"])
        if row.get("role") == "admin":
            execute("UPDATE ai_users SET plan='premium', referral_verified=1 WHERE id=%s", (user_id,))
            continue
        values = form.getlist(f"plan_{user_id}")
        submitted = next((value for value in values if value in BASE_PLAN_OPTIONS), "free")
        plan = _base_plan(submitted)
        referral_verified = 1 if form.get(f"referral_{user_id}") == "1" else 0
        reset_free_started = plan == "free" and str(row.get("plan") or "free") != "free" and not referral_verified
        execute(
            """
            UPDATE ai_users
            SET plan=%s,
                referral_verified=%s,
                free_plan_started_at=IF(%s=1, NOW(), free_plan_started_at)
            WHERE id=%s
            """,
            (plan, referral_verified, 1 if reset_free_started else 0, user_id),
        )
    return RedirectResponse("/admin/analytics?success=plans", status_code=303)


@app.post("/admin/connections/approve")
async def admin_approve_connection(request: Request, connection_id: int = Form(...)):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not _user_access(user)["is_admin"]:
        return RedirectResponse("/dashboard", status_code=303)
    execute(
        """
        UPDATE ai_user_connections
        SET approval_status='approved',
            approved_at=UTC_TIMESTAMP(),
            last_error=NULL
        WHERE id=%s AND is_active=1
        """,
        (int(connection_id),),
    )
    return RedirectResponse("/admin/analytics?success=connection_approved", status_code=303)


@app.post("/profile/admin/users/delete")
async def admin_delete_user(request: Request, delete_user_id: int = Form(...)):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not _user_access(user)["is_admin"]:
        return RedirectResponse("/profile", status_code=303)
    target_id = int(delete_user_id)
    if target_id == int(user["id"]):
        return render(request, "admin_analytics.html", _admin_analytics_context(user, error="Нельзя удалить текущий админский аккаунт."), 422)
    target = fetch_one("SELECT id, role FROM ai_users WHERE id=%s", (target_id,))
    if not target:
        return RedirectResponse("/admin/analytics", status_code=303)
    if target.get("role") == "admin":
        return render(request, "admin_analytics.html", _admin_analytics_context(user, error="Админские аккаунты нельзя удалять из этой таблицы."), 422)
    execute("DELETE FROM ai_users WHERE id=%s", (target_id,))
    return RedirectResponse("/admin/analytics?success=user_deleted", status_code=303)


@app.post("/profile/admin/users/refresh")
async def admin_refresh_user_stats(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not _user_access(user)["is_admin"]:
        return RedirectResponse("/profile", status_code=303)
    if _start_admin_stats_refresh(force=True):
        return RedirectResponse("/admin/analytics?success=stats_queued", status_code=303)
    return RedirectResponse("/admin/analytics?success=stats_running", status_code=303)


@app.post("/admin/analytics/griders-trades/refresh")
async def admin_refresh_griders_trades(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not _user_access(user)["is_admin"]:
        return RedirectResponse("/profile", status_code=303)
    asyncio.create_task(_sync_open_griders_deals_once(limit_connections=100))
    return RedirectResponse("/admin/analytics?success=griders_sync_queued", status_code=303)


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
    unprotected_positions = await _dashboard_unprotected_position_alerts(connections)
    override_success = request.query_params.get("override") == "1"
    ai_signals = []
    if connection:
        ai_signals = fetch_all(
            "SELECT * FROM ai_signals WHERE user_id=%s AND connection_id <=> %s ORDER BY created_at DESC, id DESC LIMIT 100",
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
        "unprotected_positions": unprotected_positions,
        "override_success": override_success,
        "signals": [_signal_view(row, _lang(request), user) for row in ai_signals],
        **_public_analytics_context(user),
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
        success = (
            "Подключение сохранено. Теперь откройте раздел «Стратегии», выберите пары и включите автоторговлю."
            if _lang(request) == "ru"
            else "Connection saved. Now open Strategies, choose pairs, and enable auto trading."
        )
    if request.query_params.get("saved") == "pending":
        success = (
            "Подключение создано. Для запуска подключения отправьте администратору скриншот из Cryptorg, где видно API ключ и почту вашего аккаунта."
            if _lang(request) == "ru"
            else "Connection created. To launch it, send the administrator a Cryptorg screenshot showing the API key and your account email."
        )
    if request.query_params.get("deleted") == "1":
        success = "Подключение удалено." if _lang(request) == "ru" else "Connection deleted."
    error = None
    if request.query_params.get("plan_error") == "market":
        error = _plan_message(_lang(request), "market")
    if request.query_params.get("limit_error") == "connection":
        error = "Для вашего тарифа доступно только одно подключение." if _lang(request) == "ru" else "Your plan allows only one connection."
    if request.query_params.get("locked") == "referral":
        error = "Подключен Плюс Тариф. Смена подключения только по запросу." if _lang(request) == "ru" else "Plus plan is connected. Connection changes are available by request only."
    if request.query_params.get("delete_error") == "missing":
        error = "Подключение не найдено." if _lang(request) == "ru" else "Connection not found."
    if request.query_params.get("delete_error") == "2fa_required":
        error = "Для удаления подключения сначала включите 2FA в профиле." if _lang(request) == "ru" else "Enable 2FA in your profile before deleting a connection."
    if request.query_params.get("delete_error") == "bad_2fa":
        error = "Неверный код 2FA. Подключение не удалено." if _lang(request) == "ru" else "Invalid 2FA code. Connection was not deleted."
    connection_id = int(request.query_params.get("connection_id") or 0)
    creating_new = request.query_params.get("new") == "1"
    uid = int(user["id"])
    connections = _connections(uid)
    connection_locked = _referral_verified(user) and not _user_access(user)["is_admin"] and not _premium_plus_connections_unlocked(user)
    can_create_connection = False if connection_locked else _can_create_connection(user, connections)
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
            "connection_locked": connection_locked,
            "connection_limit": _connection_limit(user),
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
    if _referral_verified(user) and not _user_access(user)["is_admin"] and not _premium_plus_connections_unlocked(user):
        return RedirectResponse("/connections?locked=referral", status_code=303)
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
    duplicate_connection = None
    if new_api_key:
        duplicate_connection = fetch_one(
            """
            SELECT c.id, c.user_id
            FROM ai_user_connections c
            WHERE c.bybit_api_key=%s
              AND c.is_active=1
              AND c.user_id<>%s
            LIMIT 1
            """,
            (new_api_key, uid),
        )
    if duplicate_connection:
        return render(
            request,
            "connections.html",
            {
                "connection": _connection_view(current),
                "connections": [_connection_view(item) for item in _connections(uid)],
                "creating_new": not bool(current),
                "can_create_connection": _can_create_connection(user),
                "connection_locked": False,
                "connection_limit": _connection_limit(user),
                "strategies": _available_strategies(user),
                "error": _t(request, "connections", "api_key_in_use"),
            },
            422,
        )
    if current and new_api_key != previous_api_key and not secret_value:
        return render(
            request,
            "connections.html",
            {
                "connection": _connection_view(current),
                "connections": [_connection_view(item) for item in _connections(uid)],
                "creating_new": False,
                "can_create_connection": _can_create_connection(user),
                "connection_locked": False,
                "connection_limit": _connection_limit(user),
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
        saved_pending = str(current.get("approval_status") or "approved") == "pending"
    else:
        existing_connections = _connections(uid)
        approval_status = "approved"
        if not _user_access(user)["is_admin"] and _effective_plan(user) == "premium_plus" and len(existing_connections) >= 1:
            approval_status = "pending"
        saved_connection_id = execute(
            """
            INSERT INTO ai_user_connections
            (user_id, label, strategy_code, bybit_api_key, bybit_api_secret_encrypted, webhook_url_encrypted, approval_status, approved_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s='approved' THEN UTC_TIMESTAMP() ELSE NULL END)
            """,
            (uid, label.strip() or "Main account", strategy_code, new_api_key, encrypted_secret, encrypted_webhook, approval_status, approval_status),
        )
        saved_pending = approval_status == "pending"
    _ensure_strategy_defaults(uid, saved_connection_id, strategy_code)
    execute(
        "UPDATE ai_user_strategy_settings SET strategy_code=%s WHERE user_id=%s AND connection_id=%s",
        (strategy_code, uid, saved_connection_id),
    )
    saved_value = "pending" if saved_pending else "1"
    response = RedirectResponse(f"/connections?connection_id={saved_connection_id}&saved={saved_value}", status_code=303)
    _remember_connection(response, saved_connection_id)
    return response


@app.post("/connections/delete")
async def delete_connection(
    request: Request,
    connection_id: int = Form(...),
    twofa_code: str = Form(""),
):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if _referral_verified(user) and not _user_access(user)["is_admin"]:
        return RedirectResponse("/connections?locked=referral", status_code=303)
    uid = int(user["id"])
    conn = _connection(uid, connection_id)
    if not conn:
        return RedirectResponse("/connections?delete_error=missing", status_code=303)
    target = f"/connections?connection_id={int(conn['id'])}"
    if not _twofa_enabled(user):
        return RedirectResponse(f"{target}&delete_error=2fa_required", status_code=303)
    if not _verify_user_2fa(user, twofa_code):
        return RedirectResponse(f"{target}&delete_error=bad_2fa", status_code=303)
    execute(
        "UPDATE ai_user_connections SET is_active=0 WHERE id=%s AND user_id=%s",
        (int(conn["id"]), uid),
    )
    response = RedirectResponse("/connections?deleted=1", status_code=303)
    response.delete_cookie(SELECTED_CONNECTION_COOKIE)
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
            if _is_free_plus_plan(user):
                first_order_volume = settings.MIN_FIRST_ORDER_VOLUME
                first_order_mode = "manual"
            else:
                response = RedirectResponse(f"/strategies?connection_id={int(connection['id'])}&order_error=no_balance", status_code=303)
                _remember_connection(response, int(connection["id"]))
                return response
        if first_order_mode == "deposit_pct":
            factor = _typical_safety_factor(settings.TYPICAL_SAFETY_ORDERS, settings.TYPICAL_MARTINGALE_MULTIPLIER)
            calculated_first_order = round((balance * (risk_pct / 100.0) * leverage) / factor, 2)
            if _is_free_plus_plan(user):
                free_plus_cap = _free_plus_first_order_cap(user, connection)
                if calculated_first_order < settings.MIN_FIRST_ORDER_VOLUME:
                    first_order_volume = settings.MIN_FIRST_ORDER_VOLUME
                    first_order_mode = "manual"
                else:
                    first_order_volume = min(calculated_first_order, free_plus_cap)
            else:
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
            manual_cap = _free_plus_first_order_cap(user, connection) if _is_free_plus_plan(user) else _manual_first_order_cap(user, connection)
            if _is_free_plus_plan(user) and manual_cap < settings.MIN_FIRST_ORDER_VOLUME:
                first_order_volume = settings.MIN_FIRST_ORDER_VOLUME
                first_order_mode = "manual"
                manual_cap = settings.MIN_FIRST_ORDER_VOLUME
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
            LIMIT 100
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
        if (row.get("strategy_code") or "") == settings.DEFAULT_STRATEGY_CODE and (row.get("side") or "") in {"long", "short"}:
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
            if (row.get("strategy_code") or "") == settings.DEFAULT_STRATEGY_CODE:
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
    start_date, end_date = _monitoring_dates(request, user, selected_connection)
    error = None
    monitor = None
    if selected_connection:
        try:
            force_refresh = request.query_params.get("refresh") == "1"
            monitor = await _monitoring_snapshot(selected_connection, start_date, end_date, user, force_refresh=force_refresh)
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


def _account_cache_max_age_seconds() -> int:
    return max(60, int(settings.GRID_DCA_ACCOUNT_CACHE_MAX_AGE_SECONDS))


async def _dashboard_unprotected_position_alerts(connections: list[dict]) -> list[dict]:
    alerts: list[dict] = []
    for conn in connections:
        checked_at = _as_utc_datetime(conn.get("last_positions_checked_at"))
        if not checked_at:
            continue
        if (datetime.now(timezone.utc) - checked_at).total_seconds() > _account_cache_max_age_seconds():
            continue
        rows = _json_loads(conn.get("last_positions_snapshot"), [])
        orders_by_pair = _json_loads(conn.get("last_open_orders_snapshot"), {})
        orders_checked_at = _as_utc_datetime(conn.get("last_open_orders_checked_at"))
        if not orders_checked_at:
            continue
        if (datetime.now(timezone.utc) - orders_checked_at).total_seconds() > OPEN_ORDERS_ALERT_CACHE_SECONDS:
            continue
        for position in rows:
            if not _dashboard_position_is_open(position):
                continue
            pair = str(position.get("symbol") or "").upper()
            if not pair:
                continue
            side = _dashboard_position_side(position)
            has_take_profit = bool(str(position.get("takeProfit") or "").strip())
            orders = orders_by_pair.get(pair) if isinstance(orders_by_pair, dict) else []
            if not isinstance(orders, list):
                orders = []
            has_close_order = _dashboard_has_close_order(orders, side)
            if has_take_profit or has_close_order:
                continue
            alerts.append({
                "connection_id": int(conn.get("id") or 0),
                "connection_label": conn.get("label") or f"Подключение {conn.get('id')}",
                "pair": pair,
                "side": side,
                "side_label": "лонг" if side == "long" else "шорт" if side == "short" else side,
                "size": _fmt_number(_float(position.get("size"))),
                "avg_price": _fmt_number(_float(position.get("avgPrice"))),
                "mark_price": _fmt_number(_float(position.get("markPrice"))),
                "unrealised_pnl": _fmt_money(_float(position.get("unrealisedPnl"))),
            })
    return alerts


def _dashboard_position_is_open(position: dict) -> bool:
    return abs(_float(position.get("size"))) > 0


def _dashboard_position_side(position: dict) -> str:
    side = str(position.get("side") or "").lower()
    if side == "buy":
        return "long"
    if side == "sell":
        return "short"
    return side


def _dashboard_order_is_active(order: dict) -> bool:
    status = str(order.get("orderStatus") or "").lower()
    return not status or status in {"new", "partiallyfilled", "untriggered", "triggered"}


def _dashboard_has_close_order(orders: list[dict], position_side: str) -> bool:
    close_side = "sell" if position_side == "long" else "buy"
    for order in orders:
        if not _dashboard_order_is_active(order):
            continue
        side = str(order.get("side") or "").lower()
        reduce_only = str(order.get("reduceOnly") or "").lower() == "true"
        close_on_trigger = str(order.get("closeOnTrigger") or "").lower() == "true"
        if side == close_side and (reduce_only or close_on_trigger):
            return True
    return False


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


def _monitoring_dates(request: Request, user: dict | None = None, conn: dict | None = None) -> tuple[str, str]:
    today = datetime.now(_user_zone(user)).date()
    default_start = _monitoring_saved_start_date(user, conn)
    if default_start is None:
        registered = _user_registered_date(user) or today
        first_trade_start = _monitoring_first_trade_start(user, conn)
        default_start = first_trade_start or max(registered, MONITORING_DATA_START_DATE)
    requested_start = request.query_params.get("start_date")
    start = _parse_date(requested_start, default_start)
    end = _parse_date(request.query_params.get("end_date"), today)
    if start > end:
        start, end = end, start
    if requested_start and user and conn:
        _save_monitoring_start_date(int(user["id"]), int(conn["id"]), start)
    return start.isoformat(), end.isoformat()


def _monitoring_saved_start_date(user: dict | None, conn: dict | None) -> object | None:
    if not user or not conn:
        return None
    row = fetch_one(
        """
        SELECT start_date
        FROM ai_monitoring_preferences
        WHERE user_id=%s AND connection_id=%s
        """,
        (int(user["id"]), int(conn["id"])),
    )
    return row.get("start_date") if row else None


def _save_monitoring_start_date(user_id: int, connection_id: int, start_date) -> None:
    execute(
        """
        INSERT INTO ai_monitoring_preferences (user_id, connection_id, start_date)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE start_date=VALUES(start_date)
        """,
        (user_id, connection_id, start_date.isoformat()),
    )


def _monitoring_first_trade_start(user: dict | None, conn: dict | None) -> object | None:
    if not user or not conn:
        return None
    row = fetch_one(
        """
        SELECT MIN(closed_at) AS first_closed_at
        FROM ai_site_trade_deals
        WHERE user_id=%s
          AND connection_id <=> %s
          AND status='closed'
          AND closed_at IS NOT NULL
        """,
        (int(user["id"]), int(conn["id"])),
    )
    if not row or not row.get("first_closed_at"):
        return None
    first_date = _as_utc_datetime(row.get("first_closed_at")).astimezone(_user_zone(user)).date()
    return max(MONITORING_DATA_START_DATE, first_date - timedelta(days=1))


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


async def _monitoring_snapshot(conn: dict, start_date: str, end_date: str, user: dict | None, force_refresh: bool = False) -> dict:
    api_key = conn.get("bybit_api_key") or ""
    api_secret = decrypt_secret(conn.get("bybit_api_secret_encrypted"))
    if not api_key or not api_secret:
        raise ValueError("Для мониторинга нужно сохранить read-only API ключ и секрет Cryptorg.")

    user_tz = _user_zone(user)
    chart_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    start_dt = datetime.combine(chart_start, datetime.min.time()).replace(tzinfo=user_tz).astimezone(timezone.utc)
    end_dt = (
        (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1))
        .replace(tzinfo=user_tz)
        .astimezone(timezone.utc)
        - timedelta(milliseconds=1)
    )
    account_cache = None if force_refresh else _monitoring_account_cache(conn)
    if account_cache is None:
        wallet, position_rows = await asyncio.wait_for(
            asyncio.gather(
                wallet_balance(api_key, api_secret),
                positions(api_key, api_secret),
            ),
            timeout=15,
        )
        balance = extract_usdt_balance(wallet)
        _store_connection_account_cache(int(conn["id"]), balance, position_rows)
    else:
        balance = float(account_cache["balance"])
        position_rows = list(account_cache["positions"])
    asyncio.create_task(_sync_recent_griders_closed_deals_for_monitoring(
        int(conn["user_id"]),
        int(conn["id"]),
        api_key,
        api_secret,
        end_dt,
    ))
    active_positions = [_position_monitor_view(row) for row in position_rows if abs(_float(row.get("size"))) > 0]
    closed = _griders_closed_pnl_views(int(conn["user_id"]), int(conn["id"]), start_dt, end_dt, user, limit=100)
    daily, summary = _griders_daily_summary_pnl(int(conn["user_id"]), int(conn["id"]), start_dt, end_dt, start_date, end_date, user)
    total_pnl = summary["total_pnl"]
    total_trades = summary["total_trades"]
    traded_volume = summary["traded_volume"]
    wins = summary["wins"]
    start_balance = balance - total_pnl
    if start_balance <= 0:
        start_balance = balance
    period_return = (total_pnl / start_balance * 100) if start_balance > 0 else 0.0
    days = max(1, (datetime.strptime(end_date, "%Y-%m-%d").date() - chart_start).days + 1)
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
            "traded_volume": _fmt_money(traded_volume),
            "traded_volume_raw": traded_volume,
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


def _monitoring_account_cache(conn: dict) -> dict | None:
    checked_at = conn.get("last_positions_checked_at") or conn.get("last_checked_at")
    if not checked_at:
        return None
    checked_dt = _as_utc_datetime(checked_at)
    if datetime.now(timezone.utc) - checked_dt > timedelta(seconds=MONITORING_ACCOUNT_CACHE_SECONDS):
        return None
    try:
        positions_snapshot = json.loads(conn.get("last_positions_snapshot") or "[]")
    except Exception:
        positions_snapshot = []
    if not isinstance(positions_snapshot, list):
        positions_snapshot = []
    return {
        "balance": _float(conn.get("last_balance")),
        "positions": positions_snapshot,
    }


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


async def _sync_recent_griders_closed_deals_for_monitoring(
    user_id: int,
    connection_id: int,
    api_key: str,
    api_secret: str,
    end_dt: datetime,
) -> None:
    default_start = end_dt - timedelta(days=3)
    oldest_open = _oldest_open_griders_deal_sent_at(user_id, connection_id)
    start_dt = min(default_start, oldest_open) if oldest_open else default_start
    start_dt = max(start_dt, end_dt - timedelta(days=14))
    try:
        closed_rows = await asyncio.wait_for(
            _closed_pnl_period(
                api_key,
                api_secret,
                int(start_dt.timestamp() * 1000),
                int(end_dt.timestamp() * 1000),
            ),
            timeout=18,
        )
        process_closed_rows_for_counter(user_id, connection_id, closed_rows)
    except Exception as exc:
        logger.warning("monitoring recent Griders closed-deal sync failed for user %s connection %s: %s", user_id, connection_id, exc)


async def _griders_open_deal_sync_loop() -> None:
    await asyncio.sleep(900)
    while True:
        try:
            await _sync_open_griders_deals_once()
        except Exception:
            logger.exception("Griders open-deal sync loop failed")
        await asyncio.sleep(max(60, GRIDERS_OPEN_DEAL_SYNC_SECONDS))


async def _sync_open_griders_deals_once(limit_connections: int = 5) -> int:
    rows = fetch_all(
        """
        SELECT
            c.id AS connection_id,
            c.user_id,
            c.bybit_api_key,
            c.bybit_api_secret_encrypted,
            MIN(d.sent_at) AS oldest_sent_at,
            COUNT(*) AS open_deals
        FROM ai_site_trade_deals d
        JOIN ai_user_connections c ON c.id=d.connection_id AND c.is_active=1
          AND COALESCE(c.approval_status, 'approved') = 'approved'
        WHERE (
            d.status='open'
            OR (
                d.status='canceled'
                AND d.close_order_type='phantom_open_cleanup'
                AND d.closed_at IS NULL
                AND d.sent_at >= UTC_TIMESTAMP() - INTERVAL 14 DAY
            )
          )
          AND d.sent_at <= UTC_TIMESTAMP() - INTERVAL 2 MINUTE
          AND c.bybit_api_key <> ''
          AND c.bybit_api_secret_encrypted IS NOT NULL
          AND c.bybit_api_secret_encrypted <> ''
        GROUP BY c.id, c.user_id, c.bybit_api_key, c.bybit_api_secret_encrypted
        ORDER BY oldest_sent_at ASC
        LIMIT %s
        """,
        (max(1, int(limit_connections)),),
    )
    synced = 0
    end_dt = datetime.now(timezone.utc)
    for row in rows:
        connection_id = int(row["connection_id"])
        user_id = int(row["user_id"])
        api_key = row.get("bybit_api_key") or ""
        try:
            api_secret = decrypt_secret(row.get("bybit_api_secret_encrypted"))
        except Exception as exc:
            logger.warning("Griders open-deal sync secret decrypt failed for connection %s: %s", connection_id, exc)
            continue
        if not api_key or not api_secret:
            continue
        oldest_sent = _as_utc_datetime(row.get("oldest_sent_at")) or (end_dt - timedelta(days=3))
        start_dt = max(oldest_sent - timedelta(minutes=30), end_dt - timedelta(days=30))
        try:
            closed_rows = await asyncio.wait_for(
                _closed_pnl_period(
                    api_key,
                    api_secret,
                    int(start_dt.timestamp() * 1000),
                    int(end_dt.timestamp() * 1000),
                ),
                timeout=24,
            )
            process_closed_rows_for_counter(user_id, connection_id, closed_rows)
            active_rows = await asyncio.wait_for(positions(api_key, api_secret), timeout=12)
            _store_connection_positions_cache(connection_id, active_rows)
            _cleanup_phantom_open_griders_deals(user_id, connection_id, active_rows)
            synced += 1
        except Exception as exc:
            logger.warning("Griders open-deal sync failed for user %s connection %s: %s", user_id, connection_id, exc)
        if synced < len(rows):
            await asyncio.sleep(0.25)
    return synced


def _cleanup_phantom_open_griders_deals(user_id: int, connection_id: int, active_rows: list[dict]) -> int:
    active_keys = {
        key
        for key in (_position_key(row) for row in active_rows)
        if key is not None
    }
    open_rows = fetch_all(
        """
        SELECT id, pair, side, sent_at
        FROM ai_site_trade_deals
        WHERE user_id=%s
          AND connection_id <=> %s
          AND status='open'
          AND sent_at <= UTC_TIMESTAMP() - INTERVAL %s MINUTE
        ORDER BY sent_at DESC, id DESC
        """,
        (user_id, connection_id, GRIDERS_OPEN_DEAL_CLEANUP_GRACE_MINUTES),
    )
    kept_active_keys: set[tuple[str, str]] = set()
    canceled = 0
    for row in open_rows:
        pair = str(row.get("pair") or "").upper()
        side = str(row.get("side") or "").lower()
        key = (pair, side)
        if key in active_keys and key not in kept_active_keys:
            kept_active_keys.add(key)
            continue
        canceled += execute(
            """
            UPDATE ai_site_trade_deals
            SET status='canceled',
                close_order_type='phantom_open_cleanup',
                updated_at=NOW()
            WHERE id=%s AND status='open'
            """,
            (int(row["id"]),),
        )
    if canceled:
        logger.info(
            "Canceled %s phantom Griders open deals for user %s connection %s",
            canceled,
            user_id,
            connection_id,
        )
    return canceled


def _position_key(row: dict) -> tuple[str, str] | None:
    try:
        size = abs(float(row.get("size") or 0))
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        return None
    pair = str(row.get("symbol") or "").upper()
    raw_side = str(row.get("side") or "").lower()
    side = "long" if raw_side == "buy" else ("short" if raw_side == "sell" else "")
    if not pair or side not in {"long", "short"}:
        return None
    return pair, side


def _oldest_open_griders_deal_sent_at(user_id: int, connection_id: int) -> datetime | None:
    row = fetch_one(
        """
        SELECT MIN(sent_at) AS sent_at
        FROM ai_site_trade_deals
        WHERE user_id=%s
          AND connection_id <=> %s
          AND status='open'
        """,
        (user_id, connection_id),
    )
    if not row or not row.get("sent_at"):
        return None
    return _as_utc_datetime(row.get("sent_at"))


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


def _griders_closed_pnl_views(
    user_id: int,
    connection_id: int,
    start_dt: datetime,
    end_dt: datetime,
    user: dict | None,
    limit: int | None = None,
) -> list[dict]:
    limit_clause = "LIMIT %s" if limit else ""
    params: tuple = (
        user_id,
        connection_id,
        start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        end_dt.strftime("%Y-%m-%d %H:%M:%S"),
    )
    if limit:
        params = (*params, int(limit))
    rows = fetch_all(
        f"""
        SELECT pair, side, qty, avg_entry_price, avg_exit_price, closed_pnl, closed_at
        FROM ai_site_trade_deals
        WHERE user_id=%s
          AND connection_id <=> %s
          AND status='closed'
          AND closed_at >= %s
          AND closed_at <= %s
        ORDER BY closed_at DESC, id DESC
        {limit_clause}
        """,
        params,
    )
    return [_griders_closed_pnl_view(row, user) for row in rows]


def _griders_monitoring_summary(user_id: int, connection_id: int, start_dt: datetime, end_dt: datetime) -> dict:
    row = fetch_one(
        """
        SELECT COUNT(*) AS total_trades,
               COALESCE(SUM(closed_pnl), 0) AS total_pnl,
               COALESCE(SUM(
                   COALESCE(api_entry_value, 0)
                   + CASE
                       WHEN ABS(COALESCE(qty, 0) * COALESCE(avg_exit_price, 0)) > 0
                         THEN ABS(COALESCE(qty, 0) * COALESCE(avg_exit_price, 0))
                       WHEN COALESCE(api_entry_value, 0) > 0
                         THEN COALESCE(api_entry_value, 0)
                       ELSE 0
                     END
               ), 0) AS traded_volume,
               SUM(CASE WHEN closed_pnl > 0 THEN 1 ELSE 0 END) AS wins
        FROM ai_site_trade_deals
        WHERE user_id=%s
          AND connection_id <=> %s
          AND status='closed'
          AND closed_at >= %s
          AND closed_at <= %s
        """,
        (
            user_id,
            connection_id,
            start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    ) or {}
    return {
        "total_trades": int(row.get("total_trades") or 0),
        "total_pnl": _float(row.get("total_pnl")),
        "traded_volume": _float(row.get("traded_volume")),
        "wins": int(row.get("wins") or 0),
    }


def _griders_daily_summary_pnl(
    user_id: int,
    connection_id: int,
    start_dt: datetime,
    end_dt: datetime,
    start_date: str,
    end_date: str,
    user: dict | None,
) -> tuple[list[dict], dict]:
    rows = fetch_all(
        """
        SELECT closed_at, closed_pnl, api_entry_value, qty, avg_exit_price
        FROM ai_site_trade_deals
        WHERE user_id=%s
          AND connection_id <=> %s
          AND status='closed'
          AND closed_at >= %s
          AND closed_at <= %s
        ORDER BY closed_at ASC
        """,
        (
            user_id,
            connection_id,
            start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    closed = []
    total_pnl = 0.0
    traded_volume = 0.0
    wins = 0
    for row in rows:
        closed_at = _as_utc_datetime(row.get("closed_at"))
        pnl = _float(row.get("closed_pnl"))
        traded_volume += _closed_trade_volume(row)
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        closed.append({
            "time_ms": int(closed_at.timestamp() * 1000),
            "pnl": pnl,
        })
    return _daily_pnl(closed, start_date, end_date, user), {
        "total_trades": len(rows),
        "total_pnl": total_pnl,
        "traded_volume": traded_volume,
        "wins": wins,
    }


def _griders_daily_pnl(
    user_id: int,
    connection_id: int,
    start_dt: datetime,
    end_dt: datetime,
    start_date: str,
    end_date: str,
    user: dict | None,
) -> list[dict]:
    rows = fetch_all(
        """
        SELECT closed_at, closed_pnl
        FROM ai_site_trade_deals
        WHERE user_id=%s
          AND connection_id <=> %s
          AND status='closed'
          AND closed_at >= %s
          AND closed_at <= %s
        ORDER BY closed_at ASC
        """,
        (
            user_id,
            connection_id,
            start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    closed = []
    for row in rows:
        closed_at = _as_utc_datetime(row.get("closed_at"))
        closed.append({
            "time_ms": int(closed_at.timestamp() * 1000),
            "pnl": _float(row.get("closed_pnl")),
        })
    return _daily_pnl(closed, start_date, end_date, user)


def _griders_closed_pnl_view(row: dict, user: dict | None) -> dict:
    pnl = _float(row.get("closed_pnl"))
    closed_at = _as_utc_datetime(row.get("closed_at"))
    time_ms = int(closed_at.timestamp() * 1000)
    return {
        "symbol": str(row.get("pair") or "").upper(),
        "side": str(row.get("side") or "").lower(),
        "qty": _fmt_monitor_number(_float(row.get("qty")), 4),
        "entry": _fmt_monitor_number(_float(row.get("avg_entry_price")), 6),
        "exit": _fmt_monitor_number(_float(row.get("avg_exit_price")), 6),
        "pnl": pnl,
        "pnl_text": _fmt_money(pnl),
        "pnl_class": "positive" if pnl > 0 else ("negative" if pnl < 0 else ""),
        "time_ms": time_ms,
        "time": closed_at.astimezone(_user_zone(user)).strftime("%Y-%m-%d %H:%M"),
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


def _closed_trade_volume(row: dict) -> float:
    entry_value = _float(row.get("api_entry_value"))
    exit_value = abs(_float(row.get("qty")) * _float(row.get("avg_exit_price")))
    if entry_value > 0 and exit_value > 0:
        return entry_value + exit_value
    if entry_value > 0:
        return entry_value * 2
    return exit_value * 2 if exit_value > 0 else 0.0


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
    pauses: list[dict] = []
    generic = await _generic_risk_pause_view(user_id, conn, ignore_override=ignore_override)
    if generic:
        pauses.append(generic)
    strategy_pauses = _strategy_pause_views(user_id, conn, ignore_override=ignore_override)
    grid_stop_pauses = _grid_dca_stop_pause_views(user_id, conn, ignore_override=ignore_override)
    pauses.extend(strategy_pauses)
    pauses.extend(grid_stop_pauses)
    return pauses


async def _generic_risk_pause_view(user_id: int, conn: dict | None, ignore_override: bool = False) -> dict | None:
    if not conn or not conn.get("bybit_api_key") or not conn.get("bybit_api_secret_encrypted"):
        return None
    if not ignore_override and fetch_one(
        "SELECT user_id FROM ai_risk_pause_overrides WHERE user_id=%s AND override_until > NOW()",
        (user_id,),
    ):
        return None

    status = _json_loads(conn.get("last_risk_pause_snapshot"), {})
    checked_at = _as_utc_datetime(conn.get("last_risk_pause_checked_at"))
    if not status or not checked_at:
        return None
    if (datetime.now(timezone.utc) - checked_at).total_seconds() > _account_cache_max_age_seconds():
        return None
    ends_at_ms = int(status.get("ends_at_ms") or 0)
    now_ms = int(time.time() * 1000)
    if ends_at_ms <= now_ms:
        return None
    remaining = max(0, int((ends_at_ms - now_ms) / 1000))
    return {
        **status,
        "type": status.get("type") or "risk_pause",
        "title": "Пауза риска",
        "button_label": "Запустить стратегию сейчас",
        "remaining_seconds": remaining,
        "remaining_label": _format_duration(remaining),
    }


def _grid_dca_stop_pause_views(user_id: int, conn: dict, ignore_override: bool = False) -> list[dict]:
    strategy_code = conn.get("strategy_code") or settings.DEFAULT_STRATEGY_CODE
    if strategy_code != settings.DEFAULT_STRATEGY_CODE:
        return []
    connection_id = int(conn["id"])
    watchlist = set(_watchlist((_strategy(user_id, connection_id) or {}).get("watchlist") or ""))
    now_ts = datetime.now(timezone.utc).timestamp()
    pauses: list[dict] = []
    user_stop_pairs: set[str] = set()

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
        side_pause_text = f"в направлении {side} " if side in {"long", "short"} else ""
        remaining = max(0, int(ends_at_ts - now_ts))
        pauses.append({
            "type": "strategy_pause",
            "scope": "user_pair_stop",
            "tone": "danger",
            "pair": pair,
            "title": f"Пауза пары {pair}",
            "button_label": f"Запустить пару {pair}",
            "reason": (
                f"GRID DCA: по {pair} {side} недавно был пробой сетки. "
                f"Новые входы по этой паре {side_pause_text}временно остановлены."
            ),
            "ends_at_ms": int(ends_at_ts * 1000),
            "ends_at": datetime.fromtimestamp(ends_at_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "remaining_seconds": remaining,
            "remaining_label": _format_duration(remaining),
        })
        user_stop_pairs.add(pair)

    for row in _grid_dca_global_pair_stop_rows(strategy_code):
        pair = str(row.get("pair") or "").upper()
        if pair in user_stop_pairs:
            continue
        if watchlist and pair not in watchlist:
            continue
        if not ignore_override and _strategy_pause_override_exists(user_id, connection_id, strategy_code, pair):
            continue
        ends_at_ts = float(row.get("last_closed_ts") or 0) + settings.GRID_DCA_GLOBAL_PAIR_STOP_COOLDOWN_HOURS * 60 * 60
        if ends_at_ts <= now_ts:
            continue
        side = str(row.get("side") or "").lower()
        side_pause_text = f"в направлении {side} " if side in {"long", "short"} else ""
        remaining = max(0, int(ends_at_ts - now_ts))
        pauses.append({
            "type": "strategy_pause",
            "scope": "global_pair_stop",
            "tone": "warning",
            "pair": pair,
            "title": f"Системная пауза {pair}",
            "button_label": f"Запустить пару {pair}",
            "reason": (
                f"GRID DCA: по {pair} {side} был пробой сетки. "
                f"Новые входы по этой паре {side_pause_text}временно остановлены."
            ),
            "ends_at_ms": int(ends_at_ts * 1000),
            "ends_at": datetime.fromtimestamp(ends_at_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "remaining_seconds": remaining,
            "remaining_label": _format_duration(remaining),
        })

    return sorted(
        pauses,
        key=lambda item: (
            0 if item.get("scope") == "user_pair_stop" else 1,
            -int(item.get("ends_at_ms") or 0),
        ),
    )


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
        "approval_status": conn.get("approval_status") or "approved",
        "is_pending_approval": (conn.get("approval_status") or "approved") == "pending",
    }


def _strategy_meta_view(strategy_code: str, lang: str) -> dict:
    code = settings.DEFAULT_STRATEGY_CODE
    description = (
        "Стратегия стадии рынка: адаптивная сетка усреднения по ATR, тейк-профит, стоп-лосс и лимиты активных сделок."
        if normalize_lang(lang) == "ru"
        else STRATEGIES[code].description
    )
    return {"code": code, "name": STRATEGIES[code].name, "description": description}


def _strategy_card_view(strategy_code: str, lang: str) -> dict:
    code = settings.DEFAULT_STRATEGY_CODE
    if normalize_lang(lang) == "ru":
        text = {
            "name": "GRID DCA 2.9",
            "description": "Стратегия стадии рынка: адаптивная сетка усреднения по ATR, тейк-профит по стадии рынка, расширенный стоп-лосс и лимиты активных сделок.",
            "simple": "Подходит для спокойного рынка, откатов и участков, где цена ходит волнами. Сделка открывается с сеткой страховочных ордеров, чтобы усреднять вход.",
            "profit": "Потенциал: умеренный, рассчитан на частые небольшие сделки.",
            "risk": "Риск: средний. Главная опасность — сильный тренд против сетки.",
        }
    else:
        text = {
            "name": "GRID DCA 2.9",
            "description": STRATEGIES[code].description,
            "simple": "Best for calmer markets, pullbacks, and wave-like price action. It opens a deal with safety orders to average the entry.",
            "profit": "Potential: moderate, focused on frequent smaller deals.",
            "risk": "Risk: medium. The main danger is a strong trend against the grid.",
        }
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
    manual_first_order_cap = _free_plus_first_order_cap(user, conn) if _is_free_plus_plan(user) else _manual_first_order_cap(user, conn)
    if _is_free_plus_plan(user) and manual_first_order_cap < settings.MIN_FIRST_ORDER_VOLUME:
        manual_first_order_cap = settings.MIN_FIRST_ORDER_VOLUME
    if limits["code"] != "free" and not is_admin:
        first_order = min(first_order, manual_first_order_cap)
    leverage = 10
    risk_pct = min(float(limits.get("max_risk_pct") or settings.MAX_STRATEGY_RISK_PCT), max(1.0, float(strategy.get("risk_pct") or settings.DEFAULT_RISK_PCT)))
    factor = _typical_safety_factor(settings.TYPICAL_SAFETY_ORDERS, settings.TYPICAL_MARTINGALE_MULTIPLIER)
    balance = float((conn or {}).get("last_balance") or 0)
    calculated_first_order = (balance * (risk_pct / 100.0) * leverage / factor) if balance > 0 and factor > 0 else 0.0
    if _is_free_plus_plan(user):
        deposit_first_order_cap = _free_plus_first_order_cap(user, conn)
    else:
        deposit_first_order_cap = max_first_order
    capped_calculated_first_order = min(calculated_first_order, deposit_first_order_cap) if calculated_first_order > 0 else 0.0
    if _is_free_plus_plan(user) and capped_calculated_first_order < settings.MIN_FIRST_ORDER_VOLUME:
        capped_calculated_first_order = settings.MIN_FIRST_ORDER_VOLUME
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
        "deposit_first_order_cap": _fmt_fixed(deposit_first_order_cap),
        "manual_first_order_cap_pct": _fmt_number(settings.MANUAL_FIRST_ORDER_MAX_DEPOSIT_PCT),
        "manual_first_order_cap_has_balance": bool(balance > 0 and not is_admin),
        "manual_first_order_unlimited": is_admin,
        "free_plus_default_first_order": _is_free_plus_plan(user),
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


def _signal_error_message(row: dict, response: dict | None) -> str | None:
    message = row.get("error_message")
    response = response if isinstance(response, dict) else {}
    error_type = str(response.get("error_type") or response.get("error") or "")
    error_text = str(response.get("error") or error_type or "").strip()
    if error_type in {"ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout", "TimeoutException"} or "timeout" in error_text.lower():
        return f"Cryptorg не ответил вовремя: {error_type or error_text}"
    if response.get("exception") and error_text:
        return f"Ошибка соединения с Cryptorg: {error_text}"
    return str(message) if message else None


def _signal_view(row: dict, lang: str = "ru", user: dict | None = None) -> dict:
    def loads(value, default):
        try:
            return json.loads(value) if value else default
        except Exception:
            return default
    reasons = loads(row.get("reasons"), [])
    response_obj = loads(row.get("response"), {})
    error_message = _signal_error_message(row, response_obj)
    if error_message:
        if row.get("status") == "failed":
            reasons = [error_message, *reasons]
        else:
            reasons = [*reasons, error_message]
    if normalize_lang(lang) == "ru":
        reasons = [_translate_reason(reason) for reason in reasons]
    return {
        **row,
        "error_message": error_message,
        "created_at_iso": _datetime_to_utc_iso(row.get("created_at")),
        "created_at": _format_user_datetime(row.get("created_at"), user),
        "reasons_list": reasons,
        "payload_obj": loads(row.get("payload"), {}),
        "response_obj": response_obj,
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


def _json_loads(value, default):
    try:
        if not value:
            return default
        return json.loads(value)
    except Exception:
        return default


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
                  LIMIT 500
                ) keep_rows
              )
              AND id NOT IN (
                SELECT signal_id FROM (
                  SELECT signal_id
                  FROM ai_site_trade_deals
                  WHERE signal_id IS NOT NULL
                ) linked_deals
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
