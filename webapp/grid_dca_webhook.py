"""TradingView webhook integration for GRID DCA strategies."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

import ghost_webhook

from . import settings
from .agent import risk_pause_status
from .cryptorg_monitor import extract_usdt_balance, open_orders, positions, wallet_balance
from .db import execute, fetch_all, fetch_one
from .launch_guard import release_pair_launch, release_strategy_side_launch, reserve_pair_launch, reserve_strategy_side_launch
from .security import decrypt_secret
from .trade_stats import record_sent_webhook


DEFAULT_STRATEGY_CODE = "grid_dca_v2"
GRID_DCA_STRATEGY_CODES = {"grid_dca_v2", "grid_dca_v3"}


async def handle_tradingview_grid_dca(payload: dict) -> dict:
    event = parse_tradingview_payload(payload)
    if not event:
        return {"ok": True, "processed": False, "reason": "not a grid dca signal"}
    raw_source_message_id = str(
        payload.get("signal_id")
        or payload.get("id")
        or f"{event['pair']}:{event['side']}:{payload.get('time') or payload.get('bar_time') or ''}:{payload.get('bar_index') or ''}"
    )
    source_message_id = f"{event['strategy_code']}:{raw_source_message_id}"
    existing = fetch_one(
        "SELECT id, processed_at FROM ai_tradingview_events WHERE source=%s AND source_message_id=%s",
        ("tradingview", source_message_id),
    )
    if existing:
        if existing.get("processed_at"):
            return {"ok": True, "processed": False, "reason": "duplicate"}
        event_id = int(existing["id"])
    else:
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
            ),
        )
    created = await create_grid_dca_signals(event, event_id)
    execute("UPDATE ai_tradingview_events SET processed_at=NOW() WHERE id=%s", (event_id,))
    return {"ok": True, "processed": True, "event": event, "signals_created": created}


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
        "dca_percent": _float_payload(payload, "dca_percent", 0.0),
        "take_profit": _float_payload(payload, "take_profit", 0.0),
        "stop_loss": _float_payload(payload, "stop_loss", 0.0),
        "reasons": _payload_reasons(payload),
    }


async def create_grid_dca_signals(event: dict, event_id: int) -> int:
    strategy_code = event.get("strategy_code") or DEFAULT_STRATEGY_CODE
    rows = fetch_all(
        """
        SELECT s.*, u.role, u.plan, c.id AS connection_id, c.bybit_api_key, c.bybit_api_secret_encrypted, c.webhook_url_encrypted
        FROM ai_user_strategy_settings s
        JOIN ai_user_connections c ON c.id = s.connection_id AND c.is_active = 1
        JOIN ai_users u ON u.id = s.user_id
        WHERE s.enabled = 1 AND s.auto_trade = 1 AND s.strategy_code = %s
          AND (s.strategy_code <> 'grid_dca_v3' OR u.role = 'admin')
        """,
        (strategy_code,),
    )
    created = 0
    for row in rows:
        if event["pair"] not in _watchlist(row.get("watchlist") or ""):
            continue
        await _create_signal_for_row(row, event, event_id)
        created += 1
    return created


async def _create_signal_for_row(row: dict, event: dict, event_id: int) -> None:
    user_id = int(row["user_id"])
    connection_id = int(row["connection_id"])
    strategy_code = event.get("strategy_code") or DEFAULT_STRATEGY_CODE
    if _signal_exists_for_event(user_id, connection_id, strategy_code, event_id):
        return
    api_key = row.get("bybit_api_key") or ""
    api_secret = decrypt_secret(row.get("bybit_api_secret_encrypted"))
    webhook_url = decrypt_secret(row.get("webhook_url_encrypted"))
    balance = 0.0
    active_positions: list[dict] = []
    risk_pause_reason = None
    monitor_error_reason = None
    if api_key and api_secret:
        try:
            wallet = await wallet_balance(api_key, api_secret)
            balance = extract_usdt_balance(wallet)
            active_positions = await positions(api_key, api_secret)
            pause = await risk_pause_status(api_key, api_secret, balance)
            if pause and not _risk_pause_override_active(user_id):
                risk_pause_reason = pause["reason"]
            execute(
                "UPDATE ai_user_connections SET last_balance=%s, last_error=NULL, last_checked_at=NOW() WHERE id=%s",
                (balance, connection_id),
            )
        except Exception as exc:
            monitor_error_reason = f"не удалось проверить баланс и открытые позиции перед запуском: {exc}"
            execute(
                "UPDATE ai_user_connections SET last_error=%s, last_checked_at=NOW() WHERE id=%s",
                (str(exc), connection_id),
            )
    elif int(row["auto_trade"]) == 1:
        monitor_error_reason = "не подключён read-only API: нельзя безопасно проверить лимиты открытых сделок"
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
            response = await ghost_webhook.send_payload(payload, webhook_url=webhook_url, confirm=True)
            status = "sent" if response.get("ok") else "failed"
            if status == "failed":
                error = ghost_webhook.failure_message(response)
                release_pair_launch(user_id, connection_id, event["pair"])
                release_strategy_side_launch(user_id, connection_id, strategy_code, event["side"])
            if status == "sent" and not await _confirm_position_opened(api_key, api_secret, event["pair"], event["side"]):
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
            if status == "sent" and not await _confirm_protective_orders(
                api_key,
                api_secret,
                event["pair"],
                event["side"],
                int(grid["dca_active"]),
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
                    "Cryptorg открыл позицию, но защитные ордера DCA/TP/SL не появились в read-only API. "
                    "Griders отправил аварийную команду закрытия позиции, чтобы она не оставалась без защиты."
                )
                release_pair_launch(user_id, connection_id, event["pair"])
                release_strategy_side_launch(user_id, connection_id, strategy_code, event["side"])
        except Exception as exc:
            status = "failed"
            error = str(exc)
            release_pair_launch(user_id, connection_id, event["pair"])
            release_strategy_side_launch(user_id, connection_id, strategy_code, event["side"])
    _insert_signal(
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
) -> None:
    sent_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if status == "sent" else None
    signal_id = execute(
        """
        INSERT INTO ai_signals
        (user_id, connection_id, strategy_code, pair, side, status, confidence, order_volume, leverage, reasons, payload, response, error_message, sent_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
        "range": {"dca_max": 4, "dca_active": 3, "mult_vol": "1.15", "mult_price": "1.05", "min_step": 0.45, "max_step": 1.8, "min_tp": 0.35, "max_tp": 0.75, "min_stop": 3.0, "max_stop": 6.0},
        "trend": {"dca_max": 3, "dca_active": 2, "mult_vol": "1.2", "mult_price": "1.15", "min_step": 0.75, "max_step": 2.4, "min_tp": 0.45, "max_tp": 1.0, "min_stop": 3.0, "max_stop": 6.5},
        "pullback": {"dca_max": 5, "dca_active": 3, "mult_vol": "1.2", "mult_price": "1.1", "min_step": 0.55, "max_step": 2.0, "min_tp": 0.4, "max_tp": 0.85, "min_stop": 3.5, "max_stop": 6.5},
    }
    preset = presets[stage]
    step = event["dca_percent"] or _clamp(event["atr_pct"] * 0.85, preset["min_step"], preset["max_step"])
    take_profit = event["take_profit"] or _clamp(step * 0.55, preset["min_tp"], preset["max_tp"])
    coverage = _grid_coverage(step, preset["dca_active"], float(preset["mult_price"]))
    stop_loss = event["stop_loss"] or _clamp(coverage * 1.25, preset["min_stop"], preset["max_stop"])
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
    if str(row.get("role") or "user") != "admin" and str(row.get("plan") or "free").lower() == "free":
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
    plan = str(row.get("plan") or "free").lower()
    if plan == "premium":
        return 600.0
    if plan == "start":
        return 60.0
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
        f"({stop.get('closed_pnl')} USDT, {stop.get('closed_at')}); новые входы по этой паре поставлены на паузу "
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
        f"системная пауза GRID DCA: по {event['pair']} {event['side']} за последние "
        f"{settings.GRID_DCA_GLOBAL_PAIR_STOP_COOLDOWN_HOURS:g}ч было {stops} пробоя сетки у "
        f"{int(stats.get('users') or 0)} пользователей, суммарный PnL {float(stats.get('pnl') or 0):.2f} USDT; "
        "новые входы по этой паре временно остановлены"
    )


def _grid_dca_guard_reason(event: dict) -> str | None:
    strategy_code = event.get("strategy_code") or DEFAULT_STRATEGY_CODE
    label = "GRID DCA 3.1" if strategy_code == "grid_dca_v3" else "GRID DCA 2.6"
    macro_1 = 0.55 if strategy_code == "grid_dca_v3" else 0.8
    macro_3 = 0.9 if strategy_code == "grid_dca_v3" else 1.2
    adverse_candle = 0.5 if strategy_code == "grid_dca_v3" else 0.7
    adverse_volume = 1.2 if strategy_code == "grid_dca_v3" else 1.4
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
                  LIMIT 50
                ) keep_rows
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
