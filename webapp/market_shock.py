"""Telegram Market Shock integration."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone

import ghost_webhook
from market import analyze_indicators, get_candles, get_orderbook, get_price, get_recent_trades

from . import settings
from .cryptorg_monitor import closed_pnl_history, extract_usdt_balance, open_orders, positions, wallet_balance
from .db import execute, fetch_all, fetch_one
from .launch_guard import release_pair_launch, reserve_pair_launch
from .security import decrypt_secret
from .trade_stats import record_sent_webhook


STRATEGY_CODE = "market_shock_impulse_v1"
REVERSAL_DCA_STRATEGY_CODE = "market_shock_reversal_dca_v21"
MARKET_SHOCK_STRATEGY_CODES = {STRATEGY_CODE, REVERSAL_DCA_STRATEGY_CODE}
REVERSAL_DCA_DENY_PAIRS = {"LABUSDT", "SKYAI1USDT", "HUSDT", "MYXUSDT"}
_SHOCK_RE = re.compile(r"USDT[-\s_/]*([A-Z0-9]+)\s+([+-]?\d+(?:[.,]\d+)?)%\s*/", re.IGNORECASE)


async def handle_telegram_update(update: dict) -> dict:
    post = update.get("channel_post") or update.get("message") or {}
    text = post.get("text") or post.get("caption") or ""
    chat = post.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    message_id = str(post.get("message_id") or update.get("update_id") or "")
    source_message_id = f"{chat_id}:{message_id}" if chat_id and message_id else message_id
    if not text or not source_message_id:
        return {"ok": True, "processed": False, "reason": "empty update"}
    return await process_market_shock_text(text, source="telegram_bot", source_message_id=source_message_id)


async def process_market_shock_text(text: str, source: str, source_message_id: str) -> dict:
    event = parse_market_shock_text(text)
    if not event:
        return {"ok": True, "processed": False, "reason": "not a market shock signal"}
    return await process_market_shock_event(event, source, source_message_id, text)


async def process_market_shock_event(
    event: dict,
    source: str,
    source_message_id: str,
    raw_text: str,
    analysis_override: dict | None = None,
) -> dict:
    existing = fetch_one(
        "SELECT id FROM ai_market_shock_events WHERE source=%s AND source_message_id=%s",
        (source, source_message_id),
    )
    if existing:
        return {"ok": True, "processed": False, "reason": "duplicate"}
    event_id = execute(
        """
        INSERT INTO ai_market_shock_events (source, source_message_id, pair, side, move_pct, shock_type, raw_text)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (source, source_message_id, event["pair"], event["side"], event["move_pct"], event["shock_type"], raw_text),
    )
    created = await create_market_shock_signals(event, event_id, analysis_override=analysis_override)
    execute("UPDATE ai_market_shock_events SET processed_at=NOW() WHERE id=%s", (event_id,))
    return {"ok": True, "processed": True, "event": event, "signals_created": created}


def parse_market_shock_text(text: str) -> dict | None:
    match = _SHOCK_RE.search(text.upper().replace(",", "."))
    if not match:
        return None
    base = match.group(1).upper()
    move_pct = float(match.group(2).replace(",", "."))
    side = "long" if move_pct > 0 else "short"
    shock_type = "SHOCK" if "SHOCK" in text.upper() else ("SLOW" if "SLOW" in text.upper() else "")
    return {
        "pair": f"{base}USDT",
        "side": side,
        "move_pct": move_pct,
        "shock_type": shock_type,
    }


async def create_market_shock_signals(event: dict, event_id: int, analysis_override: dict | None = None) -> int:
    rows = fetch_all(
        """
        SELECT s.*, u.role, u.plan, c.id AS connection_id, c.bybit_api_key, c.bybit_api_secret_encrypted, c.webhook_url_encrypted
        FROM ai_user_strategy_settings s
        JOIN ai_user_connections c ON c.id = s.connection_id AND c.is_active = 1
        JOIN ai_users u ON u.id = s.user_id
        WHERE s.enabled = 1 AND s.auto_trade = 1
          AND s.strategy_code IN (%s, %s)
          AND u.role = 'admin'
          AND (s.strategy_code <> %s OR u.role = 'admin')
        """,
        (STRATEGY_CODE, REVERSAL_DCA_STRATEGY_CODE, REVERSAL_DCA_STRATEGY_CODE),
    )
    if not rows:
        return 0

    pair_block = None
    analysis = None
    pair_state = _market_shock_pair_list_state(event["pair"])
    if pair_state and pair_state["list_type"] == "black":
        pair_block = pair_state.get("reason") or "MarketShok pair is in the blacklist"
    else:
        if analysis_override:
            analysis = analysis_override
        else:
            try:
                analysis = await _analyze_event_context(event)
            except Exception as exc:
                pair_block = _pair_error_blacklist_reason(exc)
                if pair_block:
                    _upsert_market_shock_pair_list(event["pair"], "black", pair_block, {}, event_id)
                analysis = {"tradable": False, "block_reason": f"ошибка анализа рынка: {exc}", "decisions": [], "reasons": [], "atr_pct": 0.0}

        if analysis and not pair_state and not pair_block:
            quality_block = analysis.get("pair_block_reason")
            if quality_block:
                pair_block = quality_block
                _upsert_market_shock_pair_list(event["pair"], "black", quality_block, analysis.get("metrics") or {}, event_id)
            elif analysis.get("metrics"):
                _upsert_market_shock_pair_list(
                    event["pair"],
                    "white",
                    "passed MarketShok liquidity and spread filters",
                    analysis.get("metrics") or {},
                    event_id,
                )

    created = 0
    for row in rows:
        if pair_block:
            _insert_signal_for_event(row, event, event_id, "skipped", [pair_block, _event_reason(event)], {})
            created += 1
            continue
        if analysis and not analysis["tradable"]:
            _insert_signal_for_event(row, event, event_id, "skipped", [analysis["block_reason"], *analysis["reasons"]], {})
            created += 1
            continue
        if not analysis:
            continue
        created += await _create_signals_for_row(row, event, event_id, analysis)
    return created


async def _create_signals_for_row(row: dict, event: dict, event_id: int, analysis: dict) -> int:
    user_id = int(row["user_id"])
    connection_id = int(row["connection_id"])
    api_key = row.get("bybit_api_key") or ""
    api_secret = decrypt_secret(row.get("bybit_api_secret_encrypted"))
    webhook_url = decrypt_secret(row.get("webhook_url_encrypted"))
    balance = 0.0
    active_positions: list[dict] = []
    if api_key and api_secret:
        try:
            wallet = await wallet_balance(api_key, api_secret)
            balance = extract_usdt_balance(wallet)
            active_positions = await positions(api_key, api_secret)
            await _sync_stop_loss_pauses(row, api_key, api_secret)
            execute(
                "UPDATE ai_user_connections SET last_balance=%s, last_error=NULL, last_checked_at=NOW() WHERE id=%s",
                (balance, connection_id),
            )
        except Exception as exc:
            execute(
                "UPDATE ai_user_connections SET last_error=%s, last_checked_at=NOW() WHERE id=%s",
                (str(exc), connection_id),
            )

    strategy_code = row.get("strategy_code") or STRATEGY_CODE
    if strategy_code == REVERSAL_DCA_STRATEGY_CODE and event["pair"] in REVERSAL_DCA_DENY_PAIRS:
        reason = f"MarketShok Reversal 3.0: {event['pair']} excluded by short-continuation backtest filter"
        _insert_signal_for_event(row, event, event_id, "skipped", [reason, _event_reason(event)], {})
        return 1
    pause = _active_market_shock_pause(user_id, connection_id, event["pair"], strategy_code)
    if pause:
        reason = f"пауза Market Shock: {pause['reason']}"
        _insert_signal_for_event(row, event, event_id, "skipped", [reason, _event_reason(event)], {})
        return 1

    planned_counts = _active_position_counts(active_positions)
    created = 0
    for decision in _decisions_for_strategy(strategy_code, event, analysis):
        side = decision["side"]
        trade_event = {**event, "side": side}
        limit_reason = _deal_limit_reason(row, planned_counts, side)
        if limit_reason:
            _insert_signal_for_event(row, trade_event, event_id, "skipped", [limit_reason, *decision["reasons"]], {})
            created += 1
            continue

        grid = _grid_for_strategy(strategy_code, abs(float(event["move_pct"])), float(analysis["atr_pct"]))
        volume = _calculate_order_volume(
            balance,
            float(row["risk_pct"]),
            10,
            float(row["min_order_volume"]),
            grid,
            row.get("first_order_mode") or "manual",
            _max_first_order_for_row(row),
        )
        payload = ghost_webhook.build_open_payload(
            pair=event["pair"],
            strategy=side,
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
            stop_delay=grid["stop_delay"],
        )
        status = "new"
        response = None
        error = None
        if int(row["auto_trade"]) == 1 and webhook_url:
            cooldown_reason = reserve_pair_launch(user_id, connection_id, event["pair"], f"{strategy_code}:{event_id}:{side}")
            if cooldown_reason:
                _insert_signal_for_event(
                    row,
                    trade_event,
                    event_id,
                    "skipped",
                    [cooldown_reason, *decision["reasons"]],
                    payload,
                    order_volume=volume,
                    leverage=10,
                    confidence=decision["confidence"],
                )
                created += 1
                continue
            try:
                response = await ghost_webhook.send_payload(payload, webhook_url=webhook_url, confirm=True)
                status = "sent" if response.get("ok") else "failed"
                if status == "failed":
                    error = ghost_webhook.failure_message(response)
                    release_pair_launch(user_id, connection_id, event["pair"])
                if status == "sent" and not await _confirm_position_opened(api_key, api_secret, event["pair"], side):
                    status = "failed"
                    error = "Cryptorg принял webhook, но позиция не появилась в read-only API"
                if status == "failed":
                    if error and "read-only API" in error:
                        error = "Cryptorg принял webhook, но позиция не появилась в read-only API после повторной проверки. Возможные причины: Ghost Bot не открыл пару, недостаточно маржи, уже есть конфликтующая позиция или Cryptorg обработал команду с задержкой."
                    release_pair_launch(user_id, connection_id, event["pair"])
                if status == "sent" and not await _confirm_protective_orders(api_key, api_secret, event["pair"], side):
                    close_payload = ghost_webhook.build_close_payload(pair=event["pair"], strategy=side, close_position=True)
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
                if status == "sent":
                    _increment_counts(planned_counts, side)
            except Exception as exc:
                status = "failed"
                error = str(exc)
                release_pair_launch(user_id, connection_id, event["pair"])
        _insert_signal_for_event(
            row,
            trade_event,
            event_id,
            status,
            [*decision["reasons"], f"источник: {settings.TELEGRAM_MARKET_SHOCK_CHANNEL}"],
            payload,
            response=response,
            error=error,
            order_volume=volume,
            leverage=10,
            confidence=decision["confidence"],
        )
        created += 1
    return created


async def _analyze_event_context(event: dict) -> dict:
    pair = event["pair"]
    price, candles_1m, indicators_5m, indicators_15m, orderbook, trades = await asyncio.gather(
        get_price(pair),
        get_candles(pair, interval="1", limit=60),
        analyze_indicators(pair, interval="5"),
        analyze_indicators(pair, interval="15"),
        get_orderbook(pair),
        get_recent_trades(pair, limit=100),
    )
    one_minute = candles_1m.get("candles", [])
    ind_5m = indicators_5m.get("indicators", {})
    ind_15m = indicators_15m.get("indicators", {})
    trend_5m = _trend_from_indicators(ind_5m, indicators_5m.get("signals", {}))
    trend_15m = _trend_from_indicators(ind_15m, indicators_15m.get("signals", {}))
    last_price = float(price.get("price") or 0)
    atr = float(ind_5m.get("atr") or ind_15m.get("atr") or 0)
    atr_pct = (atr / last_price * 100) if last_price else 0.0
    spread_pct = float(orderbook.get("spread_pct") or 99)
    volume_ratio = _recent_volume_ratio(one_minute, window=5, lookback=30)
    turnover_24h = float(price.get("turnover_24h") or 0)
    aggression = str(trades.get("aggression") or "BALANCED")
    move_abs = abs(float(event["move_pct"]))
    metrics = {
        "turnover_24h": turnover_24h,
        "spread_pct": spread_pct,
        "volume_ratio": volume_ratio,
        "atr_pct": atr_pct,
        "last_price": last_price,
    }

    reasons = [
        _event_reason(event),
        f"объём 24ч: {turnover_24h / 1_000_000:.1f} млн USDT",
        f"спред: {spread_pct:.3f}%",
        f"объём импульса: x{volume_ratio:.2f}",
        f"тренд 5м: {trend_5m}",
        f"тренд 15м: {trend_15m}",
        f"лента сделок: {aggression}",
    ]

    pair_block_reason = _market_pair_quality_block_reason(
        event["pair"],
        turnover_24h=turnover_24h,
        spread_pct=spread_pct,
    )
    block_reason = pair_block_reason or _market_context_block_reason(
        move_abs=move_abs,
        turnover_24h=turnover_24h,
        spread_pct=spread_pct,
        volume_ratio=volume_ratio,
        atr_pct=atr_pct,
    )
    if block_reason:
        return {
            "tradable": False,
            "block_reason": block_reason,
            "reasons": reasons,
            "decisions": [],
            "atr_pct": atr_pct,
            "metrics": metrics,
            "pair_block_reason": pair_block_reason,
        }

    decisions = _entry_decisions(event, trend_5m, trend_15m, aggression, volume_ratio)
    if not decisions:
        return {
            "tradable": False,
            "block_reason": "сигнал есть, но подтверждения для входа недостаточно",
            "reasons": reasons,
            "decisions": [],
            "atr_pct": atr_pct,
            "metrics": metrics,
            "pair_block_reason": pair_block_reason,
        }
    for decision in decisions:
        decision["reasons"] = [*reasons, *decision["reasons"]]
    return {
        "tradable": True,
        "block_reason": "",
        "reasons": reasons,
        "decisions": decisions,
        "atr_pct": atr_pct,
        "metrics": metrics,
        "pair_block_reason": pair_block_reason,
    }


def _market_shock_pair_list_state(pair: str) -> dict | None:
    pair = pair.upper()
    row = fetch_one(
        "SELECT pair, list_type, reason, metrics, checked_at FROM ai_market_shock_pair_lists WHERE pair=%s",
        (pair,),
    )
    if row:
        return row
    if pair in set(settings.MARKET_SHOCK_DENY_PAIRS):
        reason = "pair is in the configured MarketShok deny list"
        _upsert_market_shock_pair_list(pair, "black", reason, {}, None)
        return {"pair": pair, "list_type": "black", "reason": reason, "metrics": None}
    if pair in set(settings.MARKET_SHOCK_ALLOWED_PAIRS):
        reason = "pair is in the configured MarketShok allow list"
        _upsert_market_shock_pair_list(pair, "white", reason, {}, None)
        return {"pair": pair, "list_type": "white", "reason": reason, "metrics": None}
    return None


def _upsert_market_shock_pair_list(
    pair: str,
    list_type: str,
    reason: str,
    metrics: dict | None,
    event_id: int | None,
) -> None:
    execute(
        """
        INSERT INTO ai_market_shock_pair_lists (pair, list_type, reason, metrics, source_event_id, checked_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            list_type=VALUES(list_type),
            reason=VALUES(reason),
            metrics=VALUES(metrics),
            source_event_id=VALUES(source_event_id),
            checked_at=NOW()
        """,
        (
            pair.upper(),
            list_type,
            reason,
            json.dumps(metrics or {}, ensure_ascii=False),
            event_id,
        ),
    )


def _market_pair_quality_block_reason(pair: str, turnover_24h: float, spread_pct: float) -> str | None:
    pair = pair.upper()
    if pair in set(settings.MARKET_SHOCK_DENY_PAIRS):
        return "MarketShok blacklist: pair is in the configured deny list"
    if turnover_24h < settings.MARKET_SHOCK_MIN_24H_TURNOVER_USDT:
        return (
            "MarketShok blacklist: 24h turnover is below "
            f"{settings.MARKET_SHOCK_MIN_24H_TURNOVER_USDT / 1_000_000:.1f}M USDT"
        )
    if spread_pct > settings.MARKET_SHOCK_MAX_SPREAD_PCT:
        return (
            "MarketShok blacklist: spread is wider than "
            f"{settings.MARKET_SHOCK_MAX_SPREAD_PCT:.3f}%"
        )
    return None


def _pair_error_blacklist_reason(exc: Exception) -> str | None:
    message = str(exc)
    lowered = message.lower()
    if "too many visits" in lowered or "rate limit" in lowered:
        return None
    if "symbol invalid" in lowered or "list index out of range" in lowered:
        return f"MarketShok blacklist: instrument is unavailable in Bybit API ({message[:160]})"
    return None


def _market_context_block_reason(
    move_abs: float,
    turnover_24h: float,
    spread_pct: float,
    volume_ratio: float,
    atr_pct: float,
) -> str | None:
    if turnover_24h < settings.MARKET_SHOCK_MIN_24H_TURNOVER_USDT:
        return "низкая ликвидность/капитализация: 24ч оборот ниже минимального порога"
    if spread_pct > settings.MARKET_SHOCK_MAX_SPREAD_PCT:
        return "слишком широкий спред для импульсного входа"
    if move_abs < settings.MARKET_SHOCK_MIN_MOVE_PCT:
        return "импульс слабее минимального порога Market Shock"
    if move_abs > settings.MARKET_SHOCK_MAX_MOVE_PCT:
        return "импульс слишком растянут, риск входа после выноса повышен"
    if volume_ratio < settings.MARKET_SHOCK_MIN_VOLUME_RATIO:
        return "нет подтверждения объёмом"
    if atr_pct > 8.0:
        return "волатильность слишком высокая для контролируемого стопа"
    return None


def _entry_decisions(event: dict, trend_5m: str, trend_15m: str, aggression: str, volume_ratio: float) -> list[dict]:
    side = event["side"]
    opposite = "short" if side == "long" else "long"
    move_abs = abs(float(event["move_pct"]))
    aligned_trend = (side == "long" and trend_5m == "bullish") or (side == "short" and trend_5m == "bearish")
    aligned_higher = (side == "long" and trend_15m != "bearish") or (side == "short" and trend_15m != "bullish")
    aligned_flow = (side == "long" and aggression != "SELLERS") or (side == "short" and aggression != "BUYERS")
    continuation_score = sum([aligned_trend, aligned_higher, aligned_flow, volume_ratio >= 1.4])

    contrary_flow = (side == "long" and aggression == "SELLERS") or (side == "short" and aggression == "BUYERS")
    contrary_trend = (side == "long" and trend_5m == "bearish") or (side == "short" and trend_5m == "bullish")
    reversal_score = sum([move_abs >= 7.0, contrary_flow, contrary_trend, trend_15m == "neutral"])

    decisions: list[dict] = []
    if continuation_score >= 2:
        decisions.append({
            "side": side,
            "confidence": round(min(0.88, 0.66 + continuation_score * 0.04 + min(move_abs, 8.0) * 0.01), 2),
            "reasons": ["решение: вход по продолжению импульса"],
        })

    if reversal_score >= 3:
        reversal = {
            "side": opposite,
            "confidence": round(min(0.82, 0.62 + reversal_score * 0.04), 2),
            "reasons": ["решение: контр-импульс после растянутого движения"],
        }
        if settings.MARKET_SHOCK_ALLOW_DUAL_SIDE and decisions:
            decisions.append(reversal)
        elif not decisions or continuation_score < 3:
            decisions = [reversal]

    return decisions[:2]


def _decisions_for_strategy(strategy_code: str, event: dict, analysis: dict) -> list[dict]:
    if strategy_code == REVERSAL_DCA_STRATEGY_CODE:
        side = "short"
        move_abs = abs(float(event["move_pct"]))
        impulse_mode = (
            "продолжение импульса вниз"
            if event["side"] == "short"
            else "контр-импульс после резкого роста"
        )
        confidence = round(min(0.88, 0.70 + min(move_abs, 10.0) * 0.015), 2)
        return [{
            "side": side,
            "confidence": confidence,
            "reasons": [
                *analysis.get("reasons", []),
                f"решение: {impulse_mode}, short с адаптивной DCA-сеткой MarketShok Reversal 3.0",
                "параметры: short-biased логика, адаптивные TP/SL и DCA-сетка по силе импульса",
            ],
        }]
    return list(analysis.get("decisions") or [])


async def _sync_stop_loss_pauses(row: dict, api_key: str, api_secret: str) -> None:
    now_ms = int(time.time() * 1000)
    lookback_ms = int(settings.MARKET_SHOCK_PAIR_STOP_COOLDOWN_HOURS * 60 * 60 * 1000)
    try:
        pnl_rows = await closed_pnl_history(api_key, api_secret, now_ms - lookback_ms, now_ms)
    except Exception:
        return
    for pnl in pnl_rows:
        if not _looks_like_stop_loss(pnl):
            continue
        symbol = str(pnl.get("symbol") or "").upper()
        if not symbol:
            continue
        closed_ms = int(pnl.get("updatedTime") or pnl.get("createdTime") or now_ms)
        source_ref = f"{symbol}:{closed_ms}:{pnl.get('closedPnl')}"
        _insert_pause(
            row,
            pair=symbol,
            hours=settings.MARKET_SHOCK_PAIR_STOP_COOLDOWN_HOURS,
            closed_ms=closed_ms,
            reason=f"стоп-лосс по {symbol}: пауза пары на {settings.MARKET_SHOCK_PAIR_STOP_COOLDOWN_HOURS:g}ч",
            source_ref=source_ref,
        )
        if (row.get("strategy_code") or STRATEGY_CODE) != REVERSAL_DCA_STRATEGY_CODE:
            _insert_pause(
                row,
                pair="*",
                hours=settings.MARKET_SHOCK_STRATEGY_STOP_COOLDOWN_HOURS,
                closed_ms=closed_ms,
                reason=f"стоп-лосс по {symbol}: пауза стратегии на {settings.MARKET_SHOCK_STRATEGY_STOP_COOLDOWN_HOURS:g}ч",
                source_ref=source_ref,
            )


def _insert_pause(row: dict, pair: str, hours: float, closed_ms: int, reason: str, source_ref: str) -> None:
    ends_at = datetime.fromtimestamp((closed_ms + int(hours * 60 * 60 * 1000)) / 1000, tz=timezone.utc)
    if ends_at.timestamp() <= time.time():
        return
    execute(
        """
        INSERT IGNORE INTO ai_strategy_pauses
        (user_id, connection_id, strategy_code, pair, reason, source, source_ref, starts_at, ends_at)
        VALUES (%s, %s, %s, %s, %s, 'closed_pnl_stop', %s, FROM_UNIXTIME(%s), FROM_UNIXTIME(%s))
        """,
        (
            int(row["user_id"]),
            int(row["connection_id"]),
            row.get("strategy_code") or STRATEGY_CODE,
            pair,
            reason,
            source_ref,
            int(closed_ms / 1000),
            int(ends_at.timestamp()),
        ),
    )


def _active_market_shock_pause(user_id: int, connection_id: int, pair: str, strategy_code: str = STRATEGY_CODE) -> dict | None:
    return fetch_one(
        """
        SELECT p.pair, p.reason, p.ends_at
        FROM ai_strategy_pauses p
        WHERE p.user_id=%s AND p.connection_id <=> %s AND p.strategy_code=%s
          AND p.pair IN (%s, '*') AND p.ends_at > NOW()
          AND NOT EXISTS (
              SELECT 1
              FROM ai_strategy_pause_overrides o
              WHERE o.user_id=p.user_id
                AND o.connection_id <=> p.connection_id
                AND o.strategy_code=p.strategy_code
                AND o.pair=p.pair
                AND o.override_until > NOW()
          )
        ORDER BY p.ends_at DESC
        LIMIT 1
        """,
        (user_id, connection_id, strategy_code, pair),
    )


def _insert_signal_for_event(
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
    confidence: float | None = None,
) -> None:
    _insert_signal(
        int(row["user_id"]),
        int(row["connection_id"]),
        row.get("strategy_code") or STRATEGY_CODE,
        event,
        event_id,
        status,
        reasons,
        payload,
        response=response,
        error=error,
        order_volume=order_volume,
        leverage=leverage,
        confidence=confidence,
    )


def _insert_signal(
    user_id: int,
    connection_id: int,
    strategy_code: str,
    event: dict,
    event_id: int,
    status: str,
    reasons: list[str],
    payload: dict,
    response: dict | None = None,
    error: str | None = None,
    order_volume: float | None = None,
    leverage: int | None = None,
    confidence: float | None = None,
) -> None:
    sent_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if status == "sent" else None
    signal_id = execute(
        """
        INSERT INTO ai_signals
        (user_id, connection_id, strategy_code, pair, side, status, confidence, order_volume, leverage, reasons, payload, response, error_message, sent_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id,
            connection_id,
            strategy_code,
            event["pair"],
            event["side"],
            status,
            confidence if confidence is not None else (0.78 if abs(float(event["move_pct"])) >= 5 else 0.7),
            order_volume,
            leverage,
            json.dumps([f"MarketShok event #{event_id}", *reasons], ensure_ascii=False),
            json.dumps(payload, ensure_ascii=False),
            json.dumps(response, ensure_ascii=False) if response else None,
            error,
            sent_at,
        ),
    )
    if status == "sent":
        record_sent_webhook(
            user_id,
            connection_id,
            strategy_code,
            event["pair"],
            event["side"],
            order_volume,
            leverage,
            payload=payload,
            signal_id=signal_id,
            sent_at=sent_at,
        )
    _prune_user_signals(user_id)


def _event_reason(event: dict) -> str:
    label = event["shock_type"] or "MarketShok"
    return f"{label}: {event['pair']} {event['move_pct']:.2f}%"


def _grid_for_strategy(strategy_code: str, move_abs: float, atr_pct: float = 0.0) -> dict:
    return _adaptive_market_shock_grid(move_abs, atr_pct)


def _adaptive_market_shock_grid(move_abs: float, atr_pct: float = 0.0) -> dict:
    if move_abs < 5:
        dca_max = 1
        dca_active = 1
        step = _clamp(max(move_abs * 0.42, atr_pct * 0.90), 1.2, 1.8)
        stop_min, stop_max = 2.6, 3.8
        multiplier_volume = "1.1"
    elif move_abs < 8:
        dca_max = 2
        dca_active = 2
        step = _clamp(max(move_abs * 0.36, atr_pct * 0.95), 1.8, 2.6)
        stop_min, stop_max = 3.6, 5.2
        multiplier_volume = "1.12"
    else:
        dca_max = 2
        dca_active = 2
        step = _clamp(max(move_abs * 0.30, atr_pct), 2.4, 3.2)
        stop_min, stop_max = 5.0, 6.0
        multiplier_volume = "1.1"
    multiplier_price = 1.15
    coverage = _grid_coverage(step, dca_active, multiplier_price)
    take_profit = _clamp(max(move_abs * 0.34, atr_pct * 0.90), 1.0, 2.6)
    stop_loss = _clamp(max(move_abs * 0.65, atr_pct * 1.4, coverage * 1.05, take_profit * 1.9), stop_min, stop_max)
    return {
        "dca_max": dca_max,
        "dca_active": dca_active,
        "dca_percent": _fmt_pct(step),
        "dca_multiplier_volume": multiplier_volume,
        "dca_multiplier_price": _fmt_pct(multiplier_price),
        "take_profit": _fmt_pct(take_profit),
        "stop_loss": _fmt_pct(stop_loss),
        "stop_delay": 2,
    }


def _calculate_order_volume(balance: float, risk_pct: float, leverage: int, minimum: float, grid: dict, mode: str = "manual", maximum: float | None = None) -> float:
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


def _grid_coverage(step: float, active: int, multiplier_price: float) -> float:
    coverage = 0.0
    leg_step = step
    for _ in range(max(active, 1)):
        coverage += leg_step
        leg_step *= multiplier_price
    return coverage


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


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


def _position_side(row: dict) -> str:
    side = str(row.get("side") or "").lower()
    if side == "buy":
        return "long"
    if side == "sell":
        return "short"
    return ""


def _deal_limit_reason(row: dict, counts: dict, side: str) -> str | None:
    total_limit = int(row.get("max_active_deals") or 0)
    long_limit = int(row.get("max_long_deals") or 0)
    short_limit = int(row.get("max_short_deals") or 0)
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


def _increment_counts(counts: dict, side: str) -> None:
    counts["total"] = int(counts.get("total") or 0) + 1
    if side in {"long", "short"}:
        counts[side] = int(counts.get(side) or 0) + 1


async def _confirm_position_opened(api_key: str, api_secret: str, pair: str, side: str) -> bool:
    if not api_key or not api_secret:
        return True
    for delay in (4, 6, 10):
        await asyncio.sleep(delay)
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
            if size > 0 and _position_side(row) == side:
                return True
    return False


async def _confirm_protective_orders(api_key: str, api_secret: str, pair: str, side: str) -> bool:
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
        if _has_active_order(orders):
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


def _has_active_order(rows: list[dict]) -> bool:
    active_statuses = {"new", "partiallyfilled", "untriggered", "triggered"}
    for row in rows:
        status = str(row.get("orderStatus") or "").lower()
        if not status or status in active_statuses:
            return True
    return False


def _looks_like_stop_loss(row: dict) -> bool:
    closed_pnl = float(row.get("closedPnl") or 0)
    order_type = str(row.get("orderType") or "").lower()
    return closed_pnl < 0 and order_type == "market"


def _trend_from_indicators(indicators: dict, signals: dict) -> str:
    ema9 = float(indicators.get("ema9") or 0)
    ema21 = float(indicators.get("ema21") or 0)
    ema50 = float(indicators.get("ema50") or 0)
    macd = signals.get("macd")
    vwap = signals.get("vwap")
    bullish_score = 0
    bearish_score = 0
    if ema9 > ema21 > ema50:
        bullish_score += 2
    elif ema9 < ema21 < ema50:
        bearish_score += 2
    if macd == "BULLISH":
        bullish_score += 1
    elif macd == "BEARISH":
        bearish_score += 1
    if vwap == "BULLISH":
        bullish_score += 1
    elif vwap == "BEARISH":
        bearish_score += 1
    if bullish_score >= 3:
        return "bullish"
    if bearish_score >= 3:
        return "bearish"
    return "neutral"


def _recent_volume_ratio(candles: list[dict], window: int = 5, lookback: int = 30) -> float:
    if len(candles) < window + 2:
        return 1.0
    recent = candles[-window:]
    history = candles[-lookback - window:-window] if len(candles) >= lookback + window else candles[:-window]
    recent_avg = sum(float(item.get("volume") or 0) for item in recent) / max(len(recent), 1)
    history_avg = sum(float(item.get("volume") or 0) for item in history) / max(len(history), 1)
    return recent_avg / history_avg if history_avg > 0 else 1.0


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


def _fmt_pct(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _fmt_volume(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")
