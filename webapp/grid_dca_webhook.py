"""TradingView webhook integration for GRID DCA strategies."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import ghost_webhook
import httpx
from pymysql.err import IntegrityError

from . import settings
from .cryptorg_monitor import open_orders, positions
from .db import execute, fetch_all, fetch_one
from .launch_guard import release_pair_launch, release_strategy_side_launch, reserve_pair_launch, reserve_strategy_side_launch
from .security import decrypt_secret
from .trade_stats import record_sent_webhook
from .trading_controls import active_side_block


logger = logging.getLogger(__name__)
DEFAULT_STRATEGY_CODE = "grid_dca_v2"
GRID_DCA_STRATEGY_CODES = {"grid_dca_v2"}
GRID_DCA_TAKE_PROFIT_MULTIPLIER = 1.0
TRADINGVIEW_EVENT_PROCESSING_TIMEOUT_MINUTES = 10
TRADINGVIEW_EVENT_MAX_AGE_MINUTES = 15
GRID_DCA_OPEN_RETRY_WINDOW_SECONDS = 10


async def handle_tradingview_grid_dca(payload: dict) -> dict:
    queued = enqueue_tradingview_grid_dca(payload)
    event_id = queued.get("event_id")
    if not event_id or not queued.get("queued"):
        return queued
    return await process_tradingview_grid_dca_event(int(event_id))


def enqueue_tradingview_grid_dca(payload: dict) -> dict:
    event = parse_tradingview_payload(payload)
    if not event:
        return {"ok": True, "queued": False, "processed": False, "reason": "not a grid dca signal"}
    side_block = active_side_block(event["side"])
    if side_block:
        return {
            "ok": True,
            "queued": False,
            "processed": False,
            "reason": f"global_{event['side']}_block",
            "blocked_until": str(side_block.get("blocked_until") or ""),
        }
    source_message_id = _source_message_id(payload, event)
    existing = fetch_one(
        "SELECT id, processed_at FROM ai_tradingview_events WHERE source=%s AND source_message_id=%s",
        ("tradingview", source_message_id),
    )
    if existing:
        if existing.get("processed_at"):
            return {"ok": True, "queued": False, "processed": False, "reason": "duplicate", "event_id": int(existing["id"])}
        return {"ok": True, "queued": True, "processed": False, "reason": "already_queued", "event_id": int(existing["id"])}
    try:
        event_id = execute(
            """
            INSERT INTO ai_tradingview_events
            (source, source_message_id, strategy_code, pair, side, confidence, raw_payload)
            VALUES ('tradingview', %s, %s, %s, %s, %s, %s)
            """,
            (
                source_message_id,
                event["strategy_code"],
                event["pair"],
                event["side"],
                event["confidence"],
                json.dumps(payload, ensure_ascii=False),
            )
        )
    except IntegrityError:
        existing = fetch_one(
            "SELECT id, processed_at FROM ai_tradingview_events WHERE source=%s AND source_message_id=%s",
            ("tradingview", source_message_id),
        )
        if existing and existing.get("processed_at"):
            return {"ok": True, "queued": False, "processed": False, "reason": "duplicate", "event_id": int(existing["id"])}
        if not existing:
            raise
        return {"ok": True, "queued": True, "processed": False, "reason": "already_queued", "event_id": int(existing["id"])}
    return {"ok": True, "queued": True, "processed": False, "reason": "queued", "event_id": int(event_id)}


async def process_tradingview_grid_dca_event(event_id: int) -> dict:
    if not _claim_tradingview_event(event_id):
        return {"ok": True, "processed": False, "reason": "already_processing", "event_id": event_id}
    row = fetch_one("SELECT * FROM ai_tradingview_events WHERE id=%s", (event_id,))
    if not row:
        return {"ok": False, "processed": False, "reason": "event_not_found", "event_id": event_id}
    if row.get("processed_at"):
        return {"ok": True, "processed": False, "reason": "duplicate", "event_id": event_id}
    if _event_is_stale(row.get("created_at")):
        execute(
            "UPDATE ai_tradingview_events SET processed_at=NOW(), processing_error=%s WHERE id=%s",
            (f"stale TradingView event skipped after {TRADINGVIEW_EVENT_MAX_AGE_MINUTES} minutes", event_id),
        )
        return {"ok": True, "processed": False, "reason": "stale_skipped", "event_id": event_id}
    try:
        payload = json.loads(row.get("raw_payload") or "{}")
        event = parse_tradingview_payload(payload)
        if not event:
            execute(
                "UPDATE ai_tradingview_events SET processed_at=NOW(), processing_error=NULL WHERE id=%s",
                (event_id,),
            )
            return {"ok": True, "processed": False, "reason": "not a grid dca signal", "event_id": event_id}
        side_block = active_side_block(event["side"])
        if side_block:
            execute(
                "UPDATE ai_tradingview_events SET processed_at=NOW(), processing_error=%s WHERE id=%s",
                (f"global {event['side']} block is active", event_id),
            )
            return {
                "ok": True,
                "processed": False,
                "reason": f"global_{event['side']}_block",
                "event_id": event_id,
            }
        event["received_at"] = row.get("created_at")
        created = await create_grid_dca_signals(event, event_id)
        execute(
            "UPDATE ai_tradingview_events SET processed_at=NOW(), processing_error=NULL WHERE id=%s",
            (event_id,),
        )
        return {"ok": True, "processed": True, "event": event, "signals_created": created, "event_id": event_id}
    except Exception as exc:
        execute(
            "UPDATE ai_tradingview_events SET processing_error=%s WHERE id=%s",
            (str(exc), event_id),
        )
        raise


def next_pending_tradingview_grid_event_id() -> int | None:
    row = fetch_one(
        """
        SELECT id
        FROM ai_tradingview_events
        WHERE processed_at IS NULL
          AND (
            processing_started_at IS NULL
            OR processing_started_at < DATE_SUB(NOW(), INTERVAL %s MINUTE)
          )
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (TRADINGVIEW_EVENT_PROCESSING_TIMEOUT_MINUTES,),
    )
    return int(row["id"]) if row else None


def _claim_tradingview_event(event_id: int) -> bool:
    changed = execute(
        """
        UPDATE ai_tradingview_events
        SET processing_started_at=NOW(), processing_error=NULL
        WHERE id=%s
          AND processed_at IS NULL
          AND (
            processing_started_at IS NULL
            OR processing_started_at < DATE_SUB(NOW(), INTERVAL %s MINUTE)
          )
        """,
        (event_id, TRADINGVIEW_EVENT_PROCESSING_TIMEOUT_MINUTES),
    )
    return changed > 0


def _source_message_id(payload: dict, event: dict) -> str:
    raw_source_message_id = str(
        payload.get("signal_id")
        or payload.get("id")
        or f"{event['pair']}:{event['side']}:{payload.get('time') or payload.get('bar_time') or ''}:{payload.get('bar_index') or ''}"
    )
    return f"{event['strategy_code']}:{raw_source_message_id}"


def _event_is_stale(value) -> bool:
    if not value:
        return False
    if isinstance(value, datetime):
        created_at = value
    else:
        try:
            created_at = datetime.fromisoformat(str(value))
        except ValueError:
            return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds()
    return age_seconds > TRADINGVIEW_EVENT_MAX_AGE_MINUTES * 60


def parse_tradingview_payload(payload: dict) -> dict | None:
    strategy = str(payload.get("strategy") or "").lower()
    if not strategy:
        strategy = DEFAULT_STRATEGY_CODE
    if strategy not in GRID_DCA_STRATEGY_CODES:
        return None
    pair = _normalize_pair(str(payload.get("pair") or payload.get("symbol") or ""))
    side = str(payload.get("side") or "").lower()
    if side not in {"long", "short"} or not pair:
        return None
    confidence = _float_payload(payload, "confidence", 0.7)
    rsi = _float_payload(payload, "rsi", 50.0)
    return {
        "pair": pair,
        "strategy_code": strategy,
        "side": side,
        "confidence": max(0.0, min(confidence, 0.95)),
        "market_stage": str(payload.get("market_stage") or "range").lower(),
        "atr_pct": _float_payload(payload, "atr_pct", 1.0),
        "volume_ratio": _float_payload(payload, "volume_ratio", 1.0),
        "bb_position": _float_payload(payload, "bb_position", 50.0),
        "bb_width_pct": _float_payload(payload, "bb_width_pct", 0.0),
        "rsi": rsi,
        "rsi_15m": _float_payload(payload, "rsi_15m", rsi),
        "rsi_60m": _float_payload(payload, "rsi_60m", 50.0),
        "candle_pct": _float_payload(payload, "candle_pct", 0.0),
        "bar_move_pct": _float_payload(payload, "bar_move_pct", 0.0),
        "btc_move_1": _float_payload(payload, "btc_move_1", 0.0),
        "btc_move_3": _float_payload(payload, "btc_move_3", 0.0),
        "eth_move_1": _float_payload(payload, "eth_move_1", 0.0),
        "eth_move_3": _float_payload(payload, "eth_move_3", 0.0),
        "global_market_regime": str(payload.get("global_market_regime") or "").lower(),
        "trend_filter_passed": _bool_payload(payload, "trend_filter_passed", True),
        "trend_filter_reason": str(payload.get("trend_filter_reason") or ""),
        "has_global_trend_diagnostics": any(
            payload.get(key) is not None
            for key in (
                "global_market_regime",
                "trend_filter_passed",
                "trend_filter_reason",
                "btc_daily_above_ema20",
                "eth_daily_above_ema20",
            )
        ),
        "btc_daily_above_ema20": _bool_payload(payload, "btc_daily_above_ema20", True),
        "eth_daily_above_ema20": _bool_payload(payload, "eth_daily_above_ema20", True),
        "has_daily_ema_diagnostics": payload.get("btc_daily_above_ema20") is not None and payload.get("eth_daily_above_ema20") is not None,
        "btc_daily_move_3": _float_payload(payload, "btc_daily_move_3", 0.0),
        "eth_daily_move_3": _float_payload(payload, "eth_daily_move_3", 0.0),
        "global_daily_move_3": _float_payload(payload, "global_daily_move_3", 0.0),
        "dca_percent": _float_payload(payload, "dca_percent", 0.0),
        "take_profit": _float_payload(payload, "take_profit", 0.0),
        "take_profit_adjusted": _bool_payload(payload, "take_profit_adjusted", False),
        "stop_loss": _float_payload(payload, "stop_loss", 0.0),
        "reasons": _payload_reasons(payload),
    }


async def create_grid_dca_signals(event: dict, event_id: int) -> int:
    strategy_code = event.get("strategy_code") or DEFAULT_STRATEGY_CODE
    rows = fetch_all(
        """
        SELECT s.*, u.role, u.plan, u.free_plan_started_at, u.created_at AS user_created_at,
               COALESCE(u.referral_verified, 0) AS referral_verified,
               c.id AS connection_id,
               c.bybit_api_key,
               c.bybit_api_secret_encrypted,
               c.webhook_url_encrypted,
               c.last_balance,
               c.last_checked_at,
               c.last_positions_snapshot,
               c.last_positions_checked_at,
               c.last_risk_pause_reason,
               c.last_risk_pause_checked_at
        FROM ai_user_strategy_settings s
        JOIN ai_user_connections c ON c.id = s.connection_id
          AND c.is_active = 1
          AND COALESCE(c.approval_status, 'approved') = 'approved'
        JOIN ai_users u ON u.id = s.user_id
        WHERE s.enabled = 1 AND s.auto_trade = 1 AND s.strategy_code = %s
          AND NOT (
              u.role <> 'admin'
              AND u.plan = 'free'
              AND COALESCE(u.referral_verified, 0) = 0
              AND COALESCE(u.free_plan_started_at, u.created_at) <= DATE_SUB(NOW(), INTERVAL 14 DAY)
          )
        """,
        (strategy_code,),
    )
    target_rows = [row for row in rows if event["pair"] in _watchlist(row.get("watchlist") or "")]
    if not target_rows:
        return 0
    concurrency = max(1, int(settings.GRID_DCA_USER_FANOUT_CONCURRENCY))
    semaphore = asyncio.Semaphore(concurrency)

    async def run_row(row: dict) -> None:
        async with semaphore:
            try:
                await _create_signal_for_row(row, event, event_id)
            except Exception:
                logger.exception(
                    "GRID DCA signal fanout failed for event %s user %s connection %s",
                    event_id,
                    row.get("user_id"),
                    row.get("connection_id"),
                )

    await asyncio.gather(*(run_row(row) for row in target_rows))
    return len(target_rows)


async def _create_signal_for_row(row: dict, event: dict, event_id: int) -> None:
    user_id = int(row["user_id"])
    connection_id = int(row["connection_id"])
    strategy_code = event.get("strategy_code") or DEFAULT_STRATEGY_CODE
    if _signal_exists_for_event(user_id, connection_id, strategy_code, event_id):
        return
    api_key = row.get("bybit_api_key") or ""
    api_secret = decrypt_secret(row.get("bybit_api_secret_encrypted"))
    webhook_url = decrypt_secret(row.get("webhook_url_encrypted"))
    balance, active_positions, risk_pause_reason, monitor_error_reason = _account_cache_for_launch(row)
    if monitor_error_reason and int(row["auto_trade"]) == 1:
        _insert_signal(row, event, event_id, "skipped", [monitor_error_reason, *event["reasons"]], {})
        return
    if risk_pause_reason:
        _insert_signal(row, event, event_id, "skipped", [risk_pause_reason, *event["reasons"]], {})
        return
    guard_reason = _grid_dca_guard_reason(event)
    if guard_reason:
        _insert_signal(row, event, event_id, "skipped", [guard_reason, *event["reasons"]], {})
        return
    stop_guard_reason = _recent_stop_guard_reason(row, event)
    if stop_guard_reason:
        _insert_signal(row, event, event_id, "skipped", [stop_guard_reason, *event["reasons"]], {})
        return

    active_pair_reason = _active_pair_position_reason(active_positions, event["pair"])
    if active_pair_reason:
        _insert_signal(row, event, event_id, "skipped", [active_pair_reason, *event["reasons"]], {})
        return

    counts = _capacity_position_counts(user_id, connection_id, strategy_code, active_positions)
    limit_reason = _deal_limit_reason(row, counts, event["side"])
    if limit_reason:
        _insert_signal(row, event, event_id, "skipped", [limit_reason, *event["reasons"]], {})
        return

    grid = _grid_from_event(event)
    manual_cap_reason = _manual_first_order_cap_reason(row, balance)
    if manual_cap_reason:
        _insert_signal(row, event, event_id, "skipped", [manual_cap_reason, *event["reasons"]], {})
        return
    margin_reason = _insufficient_balance_reason(row, balance, grid)
    if margin_reason:
        _insert_signal(row, event, event_id, "skipped", [margin_reason, *event["reasons"]], {})
        return
    volume = _calculate_order_volume(
        balance,
        _risk_pct_for_row(row),
        10,
        float(row["min_order_volume"]),
        grid,
        row.get("first_order_mode") or "manual",
        _max_first_order_for_row(row),
    )
    payload = ghost_webhook.build_open_payload(
        pair=event["pair"],
        strategy=event["side"],
        order_volume=_fmt_volume(volume),
        leverage=10,
        dca_enabled=True,
        dca_max=grid["dca_max"],
        dca_active=grid["dca_active"],
        dca_volume=_fmt_volume(volume),
        dca_percent=grid["dca_percent"],
        dca_multiplier_volume=grid["dca_multiplier_volume"],
        dca_multiplier_price=grid["dca_multiplier_price"],
        close_value=grid["take_profit"],
        stop_enabled=True,
        stop_value=grid["stop_loss"],
        stop_delay=0,
    )
    status = "new"
    response = None
    error = None
    webhook_send_started_at = None
    webhook_response_at = None
    webhook_response_ms = None
    if int(row["auto_trade"]) == 1 and webhook_url:
        cooldown_reason = reserve_pair_launch(user_id, connection_id, event["pair"], f"{strategy_code}:{event_id}:{event['side']}")
        if cooldown_reason:
            _insert_signal(row, event, event_id, "skipped", [cooldown_reason, *event["reasons"]], payload)
            return
        cooldown_reason = reserve_strategy_side_launch(
            user_id,
            connection_id,
            strategy_code,
            event["side"],
            settings.GRID_DCA_SIDE_WEBHOOK_COOLDOWN_SECONDS,
            f"{strategy_code}:{event_id}:{event['side']}:{event['pair']}",
        )
        if cooldown_reason:
            release_pair_launch(user_id, connection_id, event["pair"])
            _insert_signal(row, event, event_id, "skipped", [cooldown_reason, *event["reasons"]], payload)
            return
        try:
            send_result = await _send_open_payload_with_safe_retry(
                payload,
                webhook_url,
                api_key,
                api_secret,
                event,
                event_id,
                user_id,
                connection_id,
            )
            webhook_send_started_at = send_result["started_at"]
            webhook_response_at = send_result["response_at"]
            webhook_response_ms = send_result["elapsed_ms"]
            response = send_result["response"]
            status = "sent" if response.get("ok") else "failed"
            if status == "failed":
                error = ghost_webhook.failure_message(response) or response.get("error") or "Cryptorg webhook failed"
                release_pair_launch(user_id, connection_id, event["pair"])
                release_strategy_side_launch(user_id, connection_id, strategy_code, event["side"])
            if (
                status == "sent"
                and not settings.GRID_DCA_FAST_WEBHOOK_CONFIRMATION
                and not await _confirm_position_opened(api_key, api_secret, event["pair"], event["side"])
            ):
                close_payload = ghost_webhook.build_close_payload(pair=event["pair"], strategy=event["side"], close_position=True)
                close_response = None
                try:
                    close_response = await ghost_webhook.send_payload(close_payload, webhook_url=webhook_url, confirm=True)
                except Exception as close_exc:
                    close_response = {"ok": False, "error": str(close_exc), "payload": close_payload}
                status = "failed"
                response = {"open": response, "emergency_close": close_response}
                error = (
                    "Cryptorg принял webhook, но позиция не появилась в read-only API за 45 сек. "
                    "Griders отправил аварийную команду закрытия/отмены сделки, чтобы позднее открытие "
                    "не осталось без DCA/TP/SL. Возможные причины: Ghost Bot не открыл сделку, недостаточно "
                    "свободной маржи, ограничение Cryptorg/биржи или задержка обработки webhook."
                )
                release_pair_launch(user_id, connection_id, event["pair"])
                release_strategy_side_launch(user_id, connection_id, strategy_code, event["side"])
            if (
                status == "sent"
                and not settings.GRID_DCA_FAST_WEBHOOK_CONFIRMATION
                and not await _confirm_protective_orders(
                api_key,
                api_secret,
                event["pair"],
                event["side"],
                int(grid["dca_active"]),
                )
            ):
                repair_payload = ghost_webhook.build_modify_payload(
                    pair=event["pair"],
                    strategy=event["side"],
                    dca_enabled=True,
                    dca_max=grid["dca_max"],
                    dca_active=grid["dca_active"],
                    dca_volume=_fmt_volume(volume),
                    dca_percent=grid["dca_percent"],
                    dca_multiplier_volume=grid["dca_multiplier_volume"],
                    dca_multiplier_price=grid["dca_multiplier_price"],
                    close_enabled=True,
                    close_value=grid["take_profit"],
                    stop_enabled=True,
                    stop_value=grid["stop_loss"],
                    stop_delay=0,
                )
                repair_response = None
                try:
                    repair_response = await ghost_webhook.send_payload(repair_payload, webhook_url=webhook_url, confirm=True)
                except Exception as repair_exc:
                    repair_response = {"ok": False, "error": str(repair_exc), "payload": repair_payload}
                if await _confirm_protective_orders(
                    api_key,
                    api_secret,
                    event["pair"],
                    event["side"],
                    int(grid["dca_active"]),
                ):
                    response = {"open": response, "protective_update": repair_response}
                else:
                    close_payload = ghost_webhook.build_close_payload(pair=event["pair"], strategy=event["side"], close_position=True)
                    close_response = None
                    try:
                        close_response = await ghost_webhook.send_payload(close_payload, webhook_url=webhook_url, confirm=True)
                    except Exception as close_exc:
                        close_response = {"ok": False, "error": str(close_exc), "payload": close_payload}
                    status = "failed"
                    response = {"open": response, "protective_update": repair_response, "emergency_close": close_response}
                    error = (
                        "Cryptorg открыл позицию, но защитные ордера DCA/TP/SL не появились в read-only API "
                        "даже после повторной отправки настроек. Griders отправил аварийную команду закрытия "
                        "позиции, чтобы она не оставалась без защиты."
                    )
                    release_pair_launch(user_id, connection_id, event["pair"])
                    release_strategy_side_launch(user_id, connection_id, strategy_code, event["side"])
        except Exception as exc:
            if webhook_send_started_at and webhook_response_at is None:
                webhook_response_at = datetime.now(timezone.utc)
            status = "failed"
            error = str(exc)
            release_pair_launch(user_id, connection_id, event["pair"])
            release_strategy_side_launch(user_id, connection_id, strategy_code, event["side"])
    signal_id = _insert_signal(
        row,
        event,
        event_id,
        status,
        [*event["reasons"], "источник: TradingView GRID DCA"],
        payload,
        response=response,
        error=error,
        order_volume=volume,
        leverage=10,
        webhook_send_started_at=webhook_send_started_at,
        webhook_response_at=webhook_response_at,
        webhook_response_ms=webhook_response_ms,
    )
    if status == "sent" and settings.GRID_DCA_FAST_WEBHOOK_CONFIRMATION and api_key and api_secret:
        asyncio.create_task(
            _verify_sent_signal_background(
                signal_id,
                row,
                event,
                grid,
                payload,
                response,
                api_key,
                api_secret,
                webhook_url,
                volume,
            )
        )


async def _verify_sent_signal_background(
    signal_id: int,
    row: dict,
    event: dict,
    grid: dict,
    payload: dict,
    open_response: dict | None,
    api_key: str,
    api_secret: str,
    webhook_url: str,
    order_volume: float,
) -> None:
    user_id = int(row["user_id"])
    connection_id = int(row["connection_id"])
    strategy_code = event.get("strategy_code") or DEFAULT_STRATEGY_CODE
    try:
        if not await _confirm_position_opened(api_key, api_secret, event["pair"], event["side"]):
            close_payload = ghost_webhook.build_close_payload(pair=event["pair"], strategy=event["side"], close_position=True)
            close_response = None
            try:
                close_response = await ghost_webhook.send_payload(close_payload, webhook_url=webhook_url, confirm=True)
            except Exception as close_exc:
                close_response = {"ok": False, "error": str(close_exc), "payload": close_payload}
            error = (
                "Cryptorg принял webhook, но позиция не появилась в read-only API за 45 сек. "
                "Griders отправил аварийную команду закрытия/отмены сделки, чтобы позднее открытие "
                "не осталось без DCA/TP/SL. Возможные причины: Ghost Bot не открыл сделку, недостаточно "
                "свободной маржи, ограничение Cryptorg/биржи или задержка обработки webhook."
            )
            _mark_signal_failed_after_fast_send(
                signal_id,
                {"open": open_response, "emergency_close": close_response},
                error,
                close_order_type="fast_open_confirm_failed",
            )
            release_pair_launch(user_id, connection_id, event["pair"])
            release_strategy_side_launch(user_id, connection_id, strategy_code, event["side"])
            return
        execute(
            "UPDATE ai_signals SET position_confirmed_at=NOW(), confirmation_status=%s WHERE id=%s",
            ("position_confirmed", signal_id),
        )
        if not await _confirm_protective_orders(
            api_key,
            api_secret,
            event["pair"],
            event["side"],
            int(grid["dca_active"]),
        ):
            repair_payload = ghost_webhook.build_modify_payload(
                pair=event["pair"],
                strategy=event["side"],
                dca_enabled=True,
                dca_max=grid["dca_max"],
                dca_active=grid["dca_active"],
                dca_volume=_fmt_volume(order_volume),
                dca_percent=grid["dca_percent"],
                dca_multiplier_volume=grid["dca_multiplier_volume"],
                dca_multiplier_price=grid["dca_multiplier_price"],
                close_enabled=True,
                close_value=grid["take_profit"],
                stop_enabled=True,
                stop_value=grid["stop_loss"],
                stop_delay=0,
            )
            repair_response = None
            try:
                repair_response = await ghost_webhook.send_payload(repair_payload, webhook_url=webhook_url, confirm=True)
            except Exception as repair_exc:
                repair_response = {"ok": False, "error": str(repair_exc), "payload": repair_payload}
            if await _confirm_protective_orders(
                api_key,
                api_secret,
                event["pair"],
                event["side"],
                int(grid["dca_active"]),
            ):
                execute(
                    """
                    UPDATE ai_signals
                    SET response=%s,
                        protective_orders_confirmed_at=NOW(),
                        confirmation_finished_at=NOW(),
                        confirmation_status=%s
                    WHERE id=%s
                    """,
                    (
                        json.dumps({"open": open_response, "protective_update": repair_response}, ensure_ascii=False),
                        "protective_repaired",
                        signal_id,
                    ),
                )
                return
            close_payload = ghost_webhook.build_close_payload(pair=event["pair"], strategy=event["side"], close_position=True)
            close_response = None
            try:
                close_response = await ghost_webhook.send_payload(close_payload, webhook_url=webhook_url, confirm=True)
            except Exception as close_exc:
                close_response = {"ok": False, "error": str(close_exc), "payload": close_payload}
            error = (
                "Cryptorg открыл позицию, но защитные ордера DCA/TP/SL не появились в read-only API "
                "даже после повторной отправки настроек. Griders отправил аварийную команду закрытия "
                "позиции, чтобы она не оставалась без защиты."
            )
            _mark_signal_failed_after_fast_send(
                signal_id,
                {"open": open_response, "protective_update": repair_response, "emergency_close": close_response},
                error,
                close_order_type="fast_protective_confirm_failed",
            )
            release_pair_launch(user_id, connection_id, event["pair"])
            release_strategy_side_launch(user_id, connection_id, strategy_code, event["side"])
            return
        execute(
            """
            UPDATE ai_signals
            SET protective_orders_confirmed_at=NOW(),
                confirmation_finished_at=NOW(),
                confirmation_status=%s
            WHERE id=%s
            """,
            ("confirmed", signal_id),
        )
    except Exception:
        logger.exception("GRID DCA background confirmation failed for signal %s", signal_id)
        execute(
            "UPDATE ai_signals SET confirmation_finished_at=NOW(), confirmation_status=%s WHERE id=%s",
            ("confirmation_error", signal_id),
        )


def _mark_signal_failed_after_fast_send(signal_id: int, response: dict, error: str, close_order_type: str) -> None:
    execute(
        """
        UPDATE ai_signals
        SET status='failed',
            response=%s,
            error_message=%s,
            confirmation_finished_at=NOW(),
            confirmation_status=%s
        WHERE id=%s
        """,
        (json.dumps(response, ensure_ascii=False), error, close_order_type, signal_id),
    )
    execute(
        """
        UPDATE ai_site_trade_deals
        SET status='canceled', close_order_type=%s, updated_at=NOW()
        WHERE signal_id=%s AND status='open'
        """,
        (close_order_type, signal_id),
    )


def _insert_signal(
    row: dict,
    event: dict,
    event_id: int,
    status: str,
    reasons: list[str],
    payload: dict,
    response: dict | None = None,
    error: str | None = None,
    order_volume: float | None = None,
    leverage: int | None = None,
    webhook_send_started_at: datetime | None = None,
    webhook_response_at: datetime | None = None,
    webhook_response_ms: int | None = None,
) -> int:
    sent_at = _mysql_utc(webhook_response_at or datetime.now(timezone.utc)) if status == "sent" else None
    signal_id = execute(
        """
        INSERT INTO ai_signals
        (user_id, connection_id, strategy_code, pair, side, status, confidence, order_volume, leverage,
         reasons, payload, response, error_message, sent_at, webhook_send_started_at, webhook_response_at,
         webhook_response_ms, confirmation_status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            int(row["user_id"]),
            int(row["connection_id"]),
            event.get("strategy_code") or DEFAULT_STRATEGY_CODE,
            event["pair"],
            event["side"],
            status,
            event["confidence"],
            order_volume,
            leverage,
            json.dumps([f"TradingView GRID event #{event_id}", *reasons], ensure_ascii=False),
            json.dumps(payload, ensure_ascii=False),
            json.dumps(response, ensure_ascii=False) if response else None,
            error,
            sent_at,
            _mysql_utc(webhook_send_started_at),
            _mysql_utc(webhook_response_at),
            webhook_response_ms,
            "cryptorg_accepted" if status == "sent" else "cryptorg_failed" if status == "failed" else "",
        ),
    )
    if status == "sent":
        record_sent_webhook(
            int(row["user_id"]),
            int(row["connection_id"]),
            event.get("strategy_code") or DEFAULT_STRATEGY_CODE,
            event["pair"],
            event["side"],
            order_volume,
            leverage,
            payload=payload,
            signal_id=signal_id,
            sent_at=sent_at,
            signal_reasons=[f"TradingView GRID event #{event_id}", *reasons],
            signal_confidence=event["confidence"],
            strategy_settings=row,
        )
    _prune_user_signals(int(row["user_id"]))
    return int(signal_id)


def _account_cache_for_launch(row: dict) -> tuple[float, list[dict], str | None, str | None]:
    if not row.get("bybit_api_key") or not row.get("bybit_api_secret_encrypted"):
        return 0.0, [], None, "не подключён read-only API: нельзя безопасно проверить лимиты открытых сделок"
    max_age = max(1, int(settings.GRID_DCA_ACCOUNT_CACHE_MAX_AGE_SECONDS))
    balance = float(row.get("last_balance") or 0)
    checked_at = _as_utc_datetime(row.get("last_checked_at"))
    positions_checked_at = _as_utc_datetime(row.get("last_positions_checked_at"))
    fresh_balance = checked_at is not None and (datetime.now(timezone.utc) - checked_at).total_seconds() <= max_age
    fresh_positions = positions_checked_at is not None and (datetime.now(timezone.utc) - positions_checked_at).total_seconds() <= max_age
    if not fresh_balance or not fresh_positions:
        return (
            balance,
            [],
            None,
            f"кэш аккаунта старше {max_age} сек: Griders пропустил запуск, чтобы не открывать сделку без актуального баланса и позиций",
        )
    active_positions = _cached_positions(row.get("last_positions_snapshot"))
    risk_reason = None
    risk_checked_at = _as_utc_datetime(row.get("last_risk_pause_checked_at"))
    if (
        row.get("last_risk_pause_reason")
        and risk_checked_at is not None
        and (datetime.now(timezone.utc) - risk_checked_at).total_seconds() <= max_age
        and not _risk_pause_override_active(int(row["user_id"]))
    ):
        risk_reason = str(row.get("last_risk_pause_reason") or "")
    return balance, active_positions, risk_reason, None


def _cached_positions(value) -> list[dict]:
    if not value:
        return []
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [row for row in parsed if isinstance(row, dict)]


def _as_utc_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _mysql_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _signal_exists_for_event(user_id: int, connection_id: int | None, strategy_code: str, event_id: int) -> bool:
    row = fetch_one(
        """
        SELECT id
        FROM ai_signals
        WHERE user_id=%s
          AND connection_id <=> %s
          AND strategy_code=%s
          AND CAST(reasons AS CHAR) LIKE %s
        LIMIT 1
        """,
        (user_id, connection_id, strategy_code, f"%TradingView GRID event #{event_id}%"),
    )
    return bool(row)


def _grid_from_event(event: dict) -> dict:
    stage = event["market_stage"] if event["market_stage"] in {"range", "trend", "pullback"} else "range"
    presets = {
        "range": {"dca_max": 4, "dca_active": 3, "mult_vol": "1.15", "mult_price": "1.05", "atr_step_mult": 0.85, "min_step": 0.45, "max_step": 1.8, "min_tp": 0.35, "max_tp": 0.75, "tp_multiplier": 1.0, "min_stop": 3.0, "max_stop": 6.0, "sl_multiplier": 1.3},
        "trend": {"dca_max": 3, "dca_active": 2, "mult_vol": "1.2", "mult_price": "1.15", "atr_step_mult": 1.1, "min_step": 0.75, "max_step": 2.4, "min_tp": 0.45, "max_tp": 1.0, "tp_multiplier": 1.15, "min_stop": 3.0, "max_stop": 6.5, "sl_multiplier": 1.3},
        "pullback": {"dca_max": 5, "dca_active": 3, "mult_vol": "1.2", "mult_price": "1.1", "atr_step_mult": 0.75, "min_step": 0.55, "max_step": 2.0, "min_tp": 0.4, "max_tp": 0.85, "tp_multiplier": 1.2, "min_stop": 3.5, "max_stop": 6.5, "sl_multiplier": 1.3},
    }
    preset = presets[stage]
    step = event["dca_percent"] or _clamp(event["atr_pct"] * preset["atr_step_mult"], preset["min_step"], preset["max_step"])
    if event["take_profit"]:
        take_profit = event["take_profit"]
        if not event.get("take_profit_adjusted"):
            take_profit *= GRID_DCA_TAKE_PROFIT_MULTIPLIER
    else:
        base_take_profit = _clamp(step * 0.55, preset["min_tp"], preset["max_tp"])
        take_profit = min(1.0, base_take_profit * preset["tp_multiplier"])
    coverage = _grid_coverage(step, preset["dca_active"], float(preset["mult_price"]))
    stop_loss = event["stop_loss"] or (_clamp(coverage * 1.25, preset["min_stop"], preset["max_stop"]) * preset["sl_multiplier"])
    return {
        "dca_max": preset["dca_max"],
        "dca_active": preset["dca_active"],
        "dca_percent": _fmt_pct(step),
        "dca_multiplier_volume": preset["mult_vol"],
        "dca_multiplier_price": preset["mult_price"],
        "take_profit": _fmt_pct(take_profit),
        "stop_loss": _fmt_pct(stop_loss),
    }


def _calculate_order_volume(balance: float, risk_pct: float, leverage: int, minimum: float, grid: dict, mode: str = "manual", maximum: float | None = None) -> float:
    risk_pct = max(1.0, float(risk_pct))
    maximum = max(float(maximum or 0), settings.MIN_FIRST_ORDER_VOLUME) if maximum else None
    minimum = max(float(minimum), settings.MIN_FIRST_ORDER_VOLUME)
    if maximum is not None:
        minimum = min(minimum, maximum)
    if mode != "deposit_pct":
        return round(minimum, 2)
    target_margin = balance * (risk_pct / 100.0)
    factor = _planned_grid_factor(grid)
    raw = target_margin * max(int(leverage), 1) / factor if factor > 0 else target_margin * max(int(leverage), 1)
    volume = max(raw, minimum)
    if maximum is not None:
        volume = min(volume, maximum)
    return round(volume, 2)


def _effective_plan_for_row(row: dict) -> str:
    plan = str(row.get("plan") or "free").lower()
    if plan.endswith("_plus"):
        return plan
    if str(row.get("referral_verified") or "0") in {"1", "true", "True"}:
        return {"free": "free_plus", "start": "start_plus", "premium": "premium_plus"}.get(plan, plan)
    return plan


def _insufficient_balance_reason(row: dict, balance: float, grid: dict) -> str | None:
    if str(row.get("role") or "user") == "admin":
        return None
    volume = _calculate_order_volume(
        balance,
        _risk_pct_for_row(row),
        10,
        float(row.get("min_order_volume") or settings.MIN_FIRST_ORDER_VOLUME),
        grid,
        row.get("first_order_mode") or "manual",
        _max_first_order_for_row(row),
    )
    required_margin = volume / 10
    if balance >= required_margin and required_margin > 0:
        return None
    return (
        f"баланс подключения {balance:.2f} USDT меньше маржи первого ордера {required_margin:.2f} USDT "
        "для минимальной сделки. Webhook не отправлен."
    )


def _risk_pct_for_row(row: dict) -> float:
    value = max(1.0, float(row.get("risk_pct") or settings.DEFAULT_RISK_PCT))
    if str(row.get("role") or "user") == "admin":
        return min(15.0, value)
    return min(settings.MAX_STRATEGY_RISK_PCT, value)


def _manual_first_order_cap_reason(row: dict, balance: float) -> str | None:
    if str(row.get("first_order_mode") or "manual") != "manual":
        return None
    if str(row.get("role") or "user") == "admin":
        return None
    if str(row.get("role") or "user") != "admin" and _effective_plan_for_row(row) == "free":
        return None
    if balance <= 0:
        return None
    current = float(row.get("min_order_volume") or settings.MIN_FIRST_ORDER_VOLUME)
    cap = balance * (settings.MANUAL_FIRST_ORDER_MAX_DEPOSIT_PCT / 100.0)
    plan_cap = _max_first_order_for_row(row)
    cap = min(cap, plan_cap)
    if current <= cap:
        return None
    return f"первый ордер {current:.2f} USDT превышает лимит ручного ввода {cap:.2f} USDT: максимум {settings.MANUAL_FIRST_ORDER_MAX_DEPOSIT_PCT:g}% от текущего депозита подключения"


def _max_first_order_for_row(row: dict) -> float:
    if str(row.get("role") or "user") == "admin":
        return 100000.0
    plan = _effective_plan_for_row(row)
    if plan == "premium_plus":
        return 2000.0
    if plan == "premium":
        return 600.0
    if plan == "start_plus":
        return 120.0
    if plan == "start":
        return 60.0
    if plan == "free_plus":
        return 12.0
    return settings.MIN_FIRST_ORDER_VOLUME


def _planned_grid_factor(grid: dict) -> float:
    dca_max = int(grid.get("dca_max") or 0)
    multiplier = float(grid.get("dca_multiplier_volume") or 1)
    factor = 1.0
    leg = 1.0
    for _ in range(dca_max):
        factor += leg
        leg *= multiplier
    return factor


def _active_position_counts(rows: list[dict]) -> dict:
    counts = {"total": 0, "long": 0, "short": 0}
    for row in rows:
        try:
            size = abs(float(row.get("size") or 0))
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            continue
        side = _position_side(row)
        counts["total"] += 1
        if side in {"long", "short"}:
            counts[side] += 1
    return counts


def _capacity_position_counts(user_id: int, connection_id: int, strategy_code: str, rows: list[dict]) -> dict:
    counts = _active_position_counts(rows)
    active_keys: set[tuple[str, str]] = set()
    for row in rows:
        try:
            size = abs(float(row.get("size") or 0))
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            continue
        pair = str(row.get("symbol") or "").upper()
        side = _position_side(row)
        if pair and side in {"long", "short"}:
            active_keys.add((pair, side))

    lookback = max(0, int(settings.GRID_DCA_LAUNCH_SAFETY_LOOKBACK_MINUTES))
    if lookback <= 0:
        return counts
    recent = fetch_all(
        """
        SELECT pair, side, MAX(created_at) AS last_created_at
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
        counts["total"] += 1
        counts[side] += 1
    return counts


def _position_side(row: dict) -> str:
    side = str(row.get("side") or "").lower()
    if side == "buy":
        return "long"
    if side == "sell":
        return "short"
    return ""


def _active_pair_position_reason(rows: list[dict], pair: str) -> str | None:
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
        side = _position_side(row)
        side_label = "лонг" if side == "long" else "шорт" if side == "short" else "неизвестная сторона"
        return (
            f"защита GRID DCA: по {target} уже есть открытая позиция ({side_label}); "
            "новый webhook не отправлен, чтобы не добавлять объём в существующую сделку и не сбивать TP/DCA."
        )
    return None


def _deal_limit_reason(row: dict, counts: dict, side: str) -> str | None:
    total_limit = int(row.get("max_active_deals") or 0)
    long_limit = int(row.get("max_long_deals") or 0)
    short_limit = int(row.get("max_short_deals") or 0)
    if total_limit <= 0:
        return "лимит активных сделок стратегии: значение лимита равно 0"
    if side == "long" and long_limit <= 0:
        return "лимит лонг-сделок стратегии: значение лимита равно 0"
    if side == "short" and short_limit <= 0:
        return "лимит шорт-сделок стратегии: значение лимита равно 0"
    if counts["total"] >= total_limit:
        return f"лимит активных сделок достигнут: {counts['total']}/{total_limit}"
    if side == "long" and counts["long"] >= long_limit:
        return f"лимит лонг-сделок достигнут: {counts['long']}/{long_limit}"
    if side == "short" and counts["short"] >= short_limit:
        return f"лимит шорт-сделок достигнут: {counts['short']}/{short_limit}"
    return None


def _recent_stop_guard_reason(row: dict, event: dict) -> str | None:
    if _strategy_pause_override_active(row, event):
        return None
    pair_reason = _user_pair_stop_guard_reason(row, event)
    if pair_reason:
        return pair_reason
    side_reason = _user_side_stop_guard_reason(row, event)
    if side_reason:
        return side_reason
    return _global_pair_stop_guard_reason(event)


def _strategy_pause_override_active(row: dict, event: dict) -> bool:
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
        (
            int(row["user_id"]),
            int(row["connection_id"]),
            event.get("strategy_code") or DEFAULT_STRATEGY_CODE,
            event["pair"],
        ),
    ))


def _stop_guard_minutes(hours: float) -> int:
    return max(1, int(float(hours) * 60))


def _stop_like_sql() -> str:
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


def _user_pair_stop_guard_reason(row: dict, event: dict) -> str | None:
    minutes = _stop_guard_minutes(settings.GRID_DCA_PAIR_STOP_COOLDOWN_HOURS)
    stop = fetch_one(
        f"""
        SELECT closed_at, closed_pnl
        FROM ai_site_trade_deals
        WHERE user_id=%s
          AND connection_id <=> %s
          AND strategy_code=%s
          AND pair=%s
          AND side=%s
          AND status='closed'
          AND closed_at >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
          AND {_stop_like_sql()}
        ORDER BY closed_at DESC
        LIMIT 1
        """,
        (
            int(row["user_id"]),
            int(row["connection_id"]),
            event.get("strategy_code") or DEFAULT_STRATEGY_CODE,
            event["pair"],
            event["side"],
            minutes,
        ),
    )
    if not stop:
        return None
    return (
        f"защитный фильтр GRID DCA: по {event['pair']} {event['side']} недавно был пробой сетки "
        f"({stop.get('closed_at')}); новые входы по этой паре поставлены на паузу "
        f"на {settings.GRID_DCA_PAIR_STOP_COOLDOWN_HOURS:g}ч"
    )


def _user_side_stop_guard_reason(row: dict, event: dict) -> str | None:
    minutes = _stop_guard_minutes(settings.GRID_DCA_SIDE_STOP_COOLDOWN_HOURS)
    threshold = max(1, int(settings.GRID_DCA_SIDE_STOP_THRESHOLD))
    stats = fetch_one(
        f"""
        SELECT COUNT(*) AS stops, SUM(closed_pnl) AS pnl
        FROM ai_site_trade_deals
        WHERE user_id=%s
          AND connection_id <=> %s
          AND strategy_code=%s
          AND side=%s
          AND status='closed'
          AND closed_at >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
          AND {_stop_like_sql()}
        """,
        (
            int(row["user_id"]),
            int(row["connection_id"]),
            event.get("strategy_code") or DEFAULT_STRATEGY_CODE,
            event["side"],
            minutes,
        ),
    ) or {}
    stops = int(stats.get("stops") or 0)
    if stops < threshold:
        return None
    return (
        f"защитный фильтр GRID DCA: за последние {settings.GRID_DCA_SIDE_STOP_COOLDOWN_HOURS:g}ч "
        f"по стороне {event['side']} было {stops} пробоя сетки, суммарный PnL {float(stats.get('pnl') or 0):.2f} USDT; "
        "новые входы по этой стороне временно остановлены"
    )


def _global_pair_stop_guard_reason(event: dict) -> str | None:
    minutes = _stop_guard_minutes(settings.GRID_DCA_GLOBAL_PAIR_STOP_COOLDOWN_HOURS)
    threshold = max(1, int(settings.GRID_DCA_GLOBAL_PAIR_STOP_THRESHOLD))
    stats = fetch_one(
        f"""
        SELECT COUNT(*) AS stops, COUNT(DISTINCT user_id) AS users, SUM(closed_pnl) AS pnl
        FROM ai_site_trade_deals
        WHERE strategy_code=%s
          AND pair=%s
          AND side=%s
          AND status='closed'
          AND closed_at >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
          AND {_stop_like_sql()}
        """,
        (
            event.get("strategy_code") or DEFAULT_STRATEGY_CODE,
            event["pair"],
            event["side"],
            minutes,
        ),
    ) or {}
    stops = int(stats.get("stops") or 0)
    if stops < threshold:
        return None
    return (
        f"системная пауза GRID DCA: по {event['pair']} {event['side']} был пробой сетки; "
        f"новые входы по этой паре в направлении {event['side']} временно остановлены"
    )


def _grid_dca_guard_reason(event: dict) -> str | None:
    strategy_code = event.get("strategy_code") or DEFAULT_STRATEGY_CODE
    label = "GRID DCA 2.9"
    macro_1 = 0.8
    macro_3 = 1.2
    adverse_candle = 0.7
    adverse_volume = 1.4
    side = event["side"]
    bb_position = float(event.get("bb_position") or 50)
    volume_ratio = float(event.get("volume_ratio") or 1)
    candle_pct = float(event.get("candle_pct") or 0)
    bar_move_pct = float(event.get("bar_move_pct") or 0)
    btc_move_1 = float(event.get("btc_move_1") or 0)
    btc_move_3 = float(event.get("btc_move_3") or 0)
    eth_move_1 = float(event.get("eth_move_1") or 0)
    eth_move_3 = float(event.get("eth_move_3") or 0)
    rsi_60m = float(event.get("rsi_60m") or 50)
    stage = str(event.get("market_stage") or "")

    trend_guard = _global_trend_guard_reason(event, label)
    if trend_guard:
        return trend_guard

    if side == "long":
        if btc_move_1 <= -macro_1 or eth_move_1 <= -macro_1 or btc_move_3 <= -macro_3 or eth_move_3 <= -macro_3:
            return f"защитный фильтр {label}: BTC/ETH резко падают, новые лонги временно не открываются"
        if stage == "pullback" and bb_position < 42:
            return f"защитный фильтр {label}: откат слишком глубокий для лонга, есть риск пробоя сетки"
        if stage == "pullback" and bb_position > 52 and rsi_60m >= 58:
            return f"защитный фильтр {label}: лонг на откате пропущен, потому что цена уже в верхней части диапазона и RSI 1h высокий"
        if bb_position < 0 or candle_pct <= -adverse_candle or (bar_move_pct <= -adverse_candle and volume_ratio >= adverse_volume):
            return f"защитный фильтр {label}: свеча падает против лонга на повышенном объёме"
    if side == "short":
        if btc_move_1 >= macro_1 or eth_move_1 >= macro_1 or btc_move_3 >= macro_3 or eth_move_3 >= macro_3:
            return f"защитный фильтр {label}: BTC/ETH резко растут, новые шорты временно не открываются"
        if stage == "pullback" and bb_position > 78:
            return f"защитный фильтр {label}: откат слишком глубокий для шорта, есть риск пробоя сетки"
        if stage == "pullback" and bb_position < 48 and rsi_60m <= 42:
            return f"защитный фильтр {label}: шорт на откате пропущен, потому что цена уже в нижней части диапазона и RSI 1h низкий"
        if bb_position > 100 or candle_pct >= adverse_candle or (bar_move_pct >= adverse_candle and volume_ratio >= adverse_volume):
            return f"защитный фильтр {label}: свеча растёт против шорта на повышенном объёме"
    return None


def _global_trend_guard_reason(event: dict, label: str) -> str | None:
    side = str(event.get("side") or "")
    regime = str(event.get("global_market_regime") or "").lower()
    trend_reason = str(event.get("trend_filter_reason") or "")
    trend_passed = bool(event.get("trend_filter_passed", True))
    has_daily_ema = bool(event.get("has_daily_ema_diagnostics"))
    btc_above = bool(event.get("btc_daily_above_ema20", True))
    eth_above = bool(event.get("eth_daily_above_ema20", True))

    if not trend_passed:
        return f"защитный фильтр {label}: TradingView заблокировал направление по дневному тренду ({trend_reason or 'trend filter'})"
    if side == "long":
        if not event.get("has_global_trend_diagnostics"):
            return f"защитный фильтр {label}: в webhook нет дневной диагностики тренда BTC/ETH, лонг пропущен"
        if regime == "downtrend":
            return f"защитный фильтр {label}: дневной рынок BTC/ETH в нисходящем тренде, новые лонги не открываются"
        if has_daily_ema and not btc_above and not eth_above:
            return f"защитный фильтр {label}: BTC и ETH ниже дневной EMA20, новые лонги не открываются"
    if side == "short":
        if regime == "uptrend":
            return f"защитный фильтр {label}: дневной рынок BTC/ETH в восходящем тренде, новые шорты не открываются"
        if has_daily_ema and btc_above and eth_above:
            return f"защитный фильтр {label}: BTC и ETH выше дневной EMA20, новые шорты не открываются"
    return None


async def _send_open_payload_with_safe_retry(
    payload: dict,
    webhook_url: str,
    api_key: str,
    api_secret: str,
    event: dict,
    event_id: int,
    user_id: int,
    connection_id: int,
) -> dict:
    attempts: list[dict] = []
    first_started_at = datetime.now(timezone.utc)
    started_monotonic = time.perf_counter()

    response = await _send_payload_attempt(payload, webhook_url, 1)
    attempts.append(response)
    if response.get("ok"):
        return {
            "response": {**response, "attempts": attempts, "retry_count": 0},
            "started_at": first_started_at,
            "response_at": datetime.now(timezone.utc),
            "elapsed_ms": int((time.perf_counter() - started_monotonic) * 1000),
        }
    if not response.get("exception"):
        return {
            "response": {**response, "attempts": attempts, "retry_count": 0},
            "started_at": first_started_at,
            "response_at": datetime.now(timezone.utc),
            "elapsed_ms": int((time.perf_counter() - started_monotonic) * 1000),
        }
    if not response.get("retryable"):
        return {
            "response": {
                **response,
                "attempts": attempts,
                "retry_count": 0,
                "retry_skipped_reason": "webhook exception is not retryable",
            },
            "started_at": first_started_at,
            "response_at": datetime.now(timezone.utc),
            "elapsed_ms": int((time.perf_counter() - started_monotonic) * 1000),
        }

    retry_decision = await _safe_open_retry_decision(api_key, api_secret, event)
    if retry_decision["allowed"]:
        logger.warning(
            "GRID DCA webhook retry event=%s user=%s connection=%s pair=%s side=%s first_error=%s age=%.3fs",
            event_id,
            user_id,
            connection_id,
            event.get("pair"),
            event.get("side"),
            response.get("error"),
            retry_decision["event_age_seconds"],
        )
        second = await _send_payload_attempt(payload, webhook_url, 2)
        attempts.append(second)
        return {
            "response": {
                **second,
                "attempts": attempts,
                "retry_count": 1,
                "retry_reason": retry_decision["reason"],
            },
            "started_at": first_started_at,
            "response_at": datetime.now(timezone.utc),
            "elapsed_ms": int((time.perf_counter() - started_monotonic) * 1000),
        }

    logger.warning(
        "GRID DCA webhook retry skipped event=%s user=%s connection=%s pair=%s side=%s reason=%s error=%s",
        event_id,
        user_id,
        connection_id,
        event.get("pair"),
        event.get("side"),
        retry_decision["reason"],
        response.get("error"),
    )
    return {
        "response": {
            **response,
            "attempts": attempts,
            "retry_count": 0,
            "retry_skipped_reason": retry_decision["reason"],
        },
        "started_at": first_started_at,
        "response_at": datetime.now(timezone.utc),
        "elapsed_ms": int((time.perf_counter() - started_monotonic) * 1000),
    }


async def _send_payload_attempt(payload: dict, webhook_url: str, attempt: int) -> dict:
    started = datetime.now(timezone.utc)
    started_monotonic = time.perf_counter()
    try:
        response = await ghost_webhook.send_payload(payload, webhook_url=webhook_url, confirm=True)
        return {
            **response,
            "attempt": attempt,
            "exception": False,
            "started_at": _mysql_utc(started),
            "elapsed_ms": int((time.perf_counter() - started_monotonic) * 1000),
        }
    except Exception as exc:
        return {
            "sent": False,
            "ok": False,
            "attempt": attempt,
            "exception": True,
            "retryable": _is_retryable_webhook_exception(exc),
            "error": str(exc) or type(exc).__name__,
            "error_type": type(exc).__name__,
            "started_at": _mysql_utc(started),
            "elapsed_ms": int((time.perf_counter() - started_monotonic) * 1000),
            "payload": payload,
        }


async def _safe_open_retry_decision(api_key: str, api_secret: str, event: dict) -> dict:
    age = _event_age_seconds(event.get("received_at"))
    if age is None:
        return {"allowed": False, "reason": "event age is unknown", "event_age_seconds": -1.0}
    if age > GRID_DCA_OPEN_RETRY_WINDOW_SECONDS:
        return {
            "allowed": False,
            "reason": f"event is older than {GRID_DCA_OPEN_RETRY_WINDOW_SECONDS} sec",
            "event_age_seconds": age,
        }
    if not api_key or not api_secret:
        return {"allowed": False, "reason": "read-only API is missing, cannot prove position is absent", "event_age_seconds": age}
    try:
        remaining = GRID_DCA_OPEN_RETRY_WINDOW_SECONDS - age
        if remaining <= 0:
            return {
                "allowed": False,
                "reason": f"event is older than {GRID_DCA_OPEN_RETRY_WINDOW_SECONDS} sec",
                "event_age_seconds": age,
            }
        rows = await asyncio.wait_for(positions(api_key, api_secret), timeout=max(0.5, remaining))
    except Exception as exc:
        return {
            "allowed": False,
            "reason": f"read-only position check failed before retry: {type(exc).__name__}: {exc}",
            "event_age_seconds": age,
        }
    age_after_check = _event_age_seconds(event.get("received_at"))
    if age_after_check is None or age_after_check > GRID_DCA_OPEN_RETRY_WINDOW_SECONDS:
        return {
            "allowed": False,
            "reason": f"event is older than {GRID_DCA_OPEN_RETRY_WINDOW_SECONDS} sec after position check",
            "event_age_seconds": age_after_check if age_after_check is not None else -1.0,
        }
    if _matching_position_is_open(rows, event["pair"], event["side"]):
        return {"allowed": False, "reason": "position already appeared after first attempt", "event_age_seconds": age_after_check}
    return {"allowed": True, "reason": "retryable network error and no matching position in read-only API", "event_age_seconds": age_after_check}


def _event_age_seconds(value) -> float | None:
    created_at = _as_utc_datetime(value)
    if not created_at:
        return None
    return max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds())


def _is_retryable_webhook_exception(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    retry_tokens = (
        "tls/ssl",
        "eof",
        "connection reset",
        "connection closed",
        "connect timeout",
        "read timeout",
        "write timeout",
        "network is unreachable",
        "temporarily unavailable",
    )
    return any(token in text for token in retry_tokens)


async def _confirm_position_opened(api_key: str, api_secret: str, pair: str, side: str) -> bool:
    if not api_key or not api_secret:
        return True
    target = pair.upper()
    deadline = time.monotonic() + 45
    while True:
        await asyncio.sleep(5)
        try:
            rows = await positions(api_key, api_secret)
        except Exception:
            return True
        for row in rows:
            if str(row.get("symbol") or "").upper() != target:
                continue
            try:
                size = abs(float(row.get("size") or 0))
            except (TypeError, ValueError):
                size = 0
            if size > 0 and _position_side(row) == side:
                return True
        if time.monotonic() >= deadline:
            break
    return False


async def _confirm_protective_orders(
    api_key: str,
    api_secret: str,
    pair: str,
    side: str,
    expected_dca_active: int = 0,
) -> bool:
    if not api_key or not api_secret:
        return True
    deadline = time.monotonic() + 60
    while True:
        await asyncio.sleep(5)
        try:
            active_rows = await positions(api_key, api_secret)
            if not _matching_position_is_open(active_rows, pair, side):
                return True
            orders = await open_orders(api_key, api_secret, pair)
        except Exception:
            return True
        if _has_closing_order(orders, side) and (expected_dca_active <= 0 or _has_dca_order(orders, side)):
            return True
        if time.monotonic() >= deadline:
            return False


def _matching_position_is_open(rows: list[dict], pair: str, side: str) -> bool:
    target = pair.upper()
    for row in rows:
        if str(row.get("symbol") or "").upper() != target:
            continue
        try:
            size = abs(float(row.get("size") or 0))
        except (TypeError, ValueError):
            size = 0
        if size > 0 and _position_side(row) == side:
            return True
    return False


def _order_is_active(row: dict) -> bool:
    active_statuses = {"new", "partiallyfilled", "untriggered", "triggered"}
    status = str(row.get("orderStatus") or "").lower()
    return not status or status in active_statuses


def _has_closing_order(rows: list[dict], position_side: str) -> bool:
    close_side = "sell" if position_side == "long" else "buy"
    for row in rows:
        if not _order_is_active(row):
            continue
        side = str(row.get("side") or "").lower()
        reduce_only = str(row.get("reduceOnly") or "").lower() == "true"
        close_on_trigger = str(row.get("closeOnTrigger") or "").lower() == "true"
        if side == close_side and (reduce_only or close_on_trigger):
            return True
    return False


def _has_dca_order(rows: list[dict], position_side: str) -> bool:
    dca_side = "buy" if position_side == "long" else "sell"
    for row in rows:
        if not _order_is_active(row):
            continue
        side = str(row.get("side") or "").lower()
        reduce_only = str(row.get("reduceOnly") or "").lower() == "true"
        close_on_trigger = str(row.get("closeOnTrigger") or "").lower() == "true"
        if side == dca_side and not reduce_only and not close_on_trigger:
            return True
    return False


def _payload_reasons(payload: dict) -> list[str]:
    reasons = payload.get("reasons")
    if isinstance(reasons, list):
        return [str(item) for item in reasons]
    result = [
        f"стадия рынка: {payload.get('market_stage') or 'range'}",
        f"ATR: {_float_payload(payload, 'atr_pct', 0):.2f}%",
        f"объём: x{_float_payload(payload, 'volume_ratio', 1):.2f}",
    ]
    if payload.get("rsi_15m") is not None or payload.get("rsi_60m") is not None:
        result.append(
            f"RSI 15m/1h: {_float_payload(payload, 'rsi_15m', _float_payload(payload, 'rsi', 50)):.1f} / "
            f"{_float_payload(payload, 'rsi_60m', 50):.1f}"
        )
    elif payload.get("rsi") is not None:
        result.append(f"RSI: {_float_payload(payload, 'rsi', 50):.1f}")
    if payload.get("bb_position") is not None:
        result.append(f"BB position: {_float_payload(payload, 'bb_position', 50):.1f}")
    if payload.get("btc_move_3") is not None or payload.get("eth_move_3") is not None:
        result.append(f"BTC/ETH 3 bars: {_float_payload(payload, 'btc_move_3', 0):.2f}% / {_float_payload(payload, 'eth_move_3', 0):.2f}%")
    if payload.get("global_market_regime") is not None:
        result.append(f"дневной режим BTC/ETH: {payload.get('global_market_regime')}")
    if payload.get("btc_daily_move_3") is not None or payload.get("eth_daily_move_3") is not None:
        result.append(
            f"BTC/ETH 3 days: {_float_payload(payload, 'btc_daily_move_3', 0):.2f}% / "
            f"{_float_payload(payload, 'eth_daily_move_3', 0):.2f}%"
        )
    if payload.get("btc_daily_above_ema20") is not None or payload.get("eth_daily_above_ema20") is not None:
        btc_state = "выше EMA20" if _bool_payload(payload, "btc_daily_above_ema20", False) else "ниже EMA20"
        eth_state = "выше EMA20" if _bool_payload(payload, "eth_daily_above_ema20", False) else "ниже EMA20"
        result.append(f"BTC/ETH daily EMA20: BTC {btc_state}, ETH {eth_state}")
    return result


def _watchlist(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _normalize_pair(value: str) -> str:
    symbol = value.upper().split(":")[-1].replace(".P", "").replace("PERP", "")
    symbol = symbol.replace("-", "").replace("/", "").replace("_", "")
    return symbol if symbol.endswith("USDT") else f"{symbol}USDT"


def _float_payload(payload: dict, key: str, default: float) -> float:
    try:
        return float(payload.get(key) or default)
    except (TypeError, ValueError):
        return default


def _bool_payload(payload: dict, key: str, default: bool = False) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _risk_pause_override_active(user_id: int) -> bool:
    return bool(fetch_one(
        "SELECT user_id FROM ai_risk_pause_overrides WHERE user_id=%s AND override_until > NOW()",
        (user_id,),
    ))


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


def _grid_coverage(step: float, active: int, multiplier_price: float) -> float:
    coverage = 0.0
    leg_step = step
    for _ in range(max(active, 1)):
        coverage += leg_step
        leg_step *= multiplier_price
    return coverage


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def _fmt_pct(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _fmt_volume(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")
