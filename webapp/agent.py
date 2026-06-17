"""Background strategy scanner."""

import asyncio
import json
import time
from datetime import datetime, timezone

import ghost_webhook

from . import settings
from .cryptorg_monitor import closed_pnl_history, extract_usdt_balance, order_history, positions, wallet_balance
from .db import execute, fetch_all, fetch_one
from .security import decrypt_secret
from .strategies import analyze_pair
from .trade_stats import record_sent_webhook


EVENT_DRIVEN_STRATEGIES = {"grid_dca_v2", "grid_dca_v3", "market_shock_impulse_v1", "market_shock_reversal_dca_v21"}


def _watchlist(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _calculate_order_volume(balance: float, risk_pct: float, leverage: int, minimum: float, grid: dict | None = None, mode: str = "manual", maximum: float | None = None) -> float:
    risk_pct = max(1.0, float(risk_pct))
    maximum = max(float(maximum or 0), settings.MIN_FIRST_ORDER_VOLUME) if maximum else None
    minimum = max(float(minimum), settings.MIN_FIRST_ORDER_VOLUME)
    if maximum is not None:
        minimum = min(minimum, maximum)
    if mode != "deposit_pct":
        return round(minimum, 2)
    target_margin = balance * (risk_pct / 100.0)
    factor = _planned_grid_factor(grid or {})
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


def _max_first_order_for_row(row: dict) -> float:
    if str(row.get("role") or "user") == "admin":
        return 100000.0
    plan = str(row.get("plan") or "free").lower()
    if plan == "premium":
        return 600.0
    if plan == "start":
        return 60.0
    return settings.MIN_FIRST_ORDER_VOLUME


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
    cap = min(
        _max_first_order_for_row(row),
        balance * (settings.MANUAL_FIRST_ORDER_MAX_DEPOSIT_PCT / 100.0),
    )
    if current <= cap:
        return None
    return f"первый ордер {current:.2f} USDT превышает лимит ручного ввода {cap:.2f} USDT: максимум {settings.MANUAL_FIRST_ORDER_MAX_DEPOSIT_PCT:g}% от текущего депозита подключения"


def _planned_grid_factor(grid: dict) -> float:
    dca_max = int(grid.get("dca_max") or 0)
    multiplier = float(grid.get("dca_multiplier_volume") or 1)
    factor = 1.0
    leg = 1.0
    for _ in range(dca_max):
        factor += leg
        leg *= multiplier
    return factor


async def scan_once() -> None:
    settings_rows = fetch_all(
        """
        SELECT s.*, u.email, u.role, u.plan, c.id AS connection_id, c.bybit_api_key, c.bybit_api_secret_encrypted, c.webhook_url_encrypted
        FROM ai_user_strategy_settings s
        JOIN ai_users u ON u.id = s.user_id
        JOIN ai_user_connections c ON c.id = s.connection_id AND c.is_active = 1
        WHERE s.enabled = 1 AND s.auto_trade = 1
        """
    )

    for row in settings_rows:
        await _scan_user_strategy(row)


async def _scan_user_strategy(row: dict) -> None:
    await _cleanup_after_take_profit(row)
    if row.get("strategy_code") in EVENT_DRIVEN_STRATEGIES:
        return

    user_id = int(row["user_id"])
    connection_id = int(row["connection_id"])
    bybit_key = row.get("bybit_api_key") or ""
    bybit_secret = decrypt_secret(row.get("bybit_api_secret_encrypted"))
    webhook_url = decrypt_secret(row.get("webhook_url_encrypted"))

    balance = 0.0
    risk_pause_reason = None
    active_counts = {"total": 0, "long": 0, "short": 0}
    if bybit_key and bybit_secret:
        try:
            wallet = await wallet_balance(bybit_key, bybit_secret)
            balance = extract_usdt_balance(wallet)
            active_counts = _active_position_counts(await positions(bybit_key, bybit_secret))
            pause_status = await risk_pause_status(bybit_key, bybit_secret, balance)
            if pause_status and not _risk_pause_override_active(user_id):
                risk_pause_reason = pause_status["reason"]
            execute(
                "UPDATE ai_user_connections SET last_balance=%s, last_error=NULL, last_checked_at=NOW() WHERE id=%s",
                (balance, connection_id),
            )
        except Exception as exc:
            execute(
                "UPDATE ai_user_connections SET last_error=%s, last_checked_at=NOW() WHERE id=%s",
                (str(exc), connection_id),
            )

    if risk_pause_reason:
        _insert_risk_pause_signal(user_id, connection_id, row["strategy_code"], risk_pause_reason)
        return

    pairs = _watchlist(row.get("watchlist") or "")
    planned_counts = dict(active_counts)
    for pair in pairs:
        try:
            signal = await analyze_pair(pair, row["strategy_code"])
        except Exception as exc:
            _insert_signal(
                user_id=user_id,
                connection_id=connection_id,
                strategy=row["strategy_code"],
                pair=pair,
                side="wait",
                confidence=0,
                status="failed",
                reasons=[f"ошибка анализа: {exc}"],
                payload={},
                error=str(exc),
            )
            continue

        if signal["side"] == "wait":
            continue

        limit_reason = _deal_limit_reason(row, planned_counts, signal["side"])
        if limit_reason:
            _insert_signal(
                user_id=user_id,
                connection_id=connection_id,
                strategy=row["strategy_code"],
                pair=pair,
                side=signal["side"],
                confidence=float(signal["confidence"]),
                status="skipped",
                reasons=[limit_reason, *signal["reasons"]],
                payload={},
            )
            continue

        grid = signal.get("grid") or {}
        manual_cap_reason = _manual_first_order_cap_reason(row, balance)
        if manual_cap_reason:
            _insert_signal(
                user_id=user_id,
                connection_id=connection_id,
                strategy=row["strategy_code"],
                pair=pair,
                side=signal["side"],
                confidence=float(signal["confidence"]),
                status="skipped",
                reasons=[manual_cap_reason, *signal["reasons"]],
                payload={},
            )
            continue
        volume = _calculate_order_volume(
            balance=balance,
            risk_pct=_risk_pct_for_row(row),
            leverage=10,
            minimum=float(row["min_order_volume"]),
            grid=grid,
            mode=row.get("first_order_mode") or "manual",
            maximum=_max_first_order_for_row(row),
        )
        payload = ghost_webhook.build_open_payload(
            pair=pair,
            strategy=signal["side"],
            order_volume=str(volume),
            leverage=10,
            dca_enabled=True,
            dca_max=int(grid.get("dca_max") or 3),
            dca_active=int(grid.get("dca_active") or 2),
            dca_volume=str(volume),
            dca_percent=str(grid.get("dca_percent") or "1"),
            dca_multiplier_volume=str(grid.get("dca_multiplier_volume") or "1.2"),
            dca_multiplier_price=str(grid.get("dca_multiplier_price") or "1.1"),
            close_value=str(grid.get("take_profit") or "0.5"),
            stop_enabled=True,
            stop_value=str(grid.get("stop_loss") or "3"),
            stop_delay=int(grid.get("stop_delay") or 3),
        )
        status = "new"
        response = None
        error = None

        if int(row["auto_trade"]) == 1 and webhook_url:
            try:
                response = await ghost_webhook.send_payload(payload, webhook_url=webhook_url, confirm=True)
                status = "sent" if response.get("ok") else "failed"
                if status == "failed":
                    error = ghost_webhook.failure_message(response)
                if status == "sent":
                    opened = await _confirm_position_opened(bybit_key, bybit_secret, pair, signal["side"])
                    if opened:
                        _increment_counts(planned_counts, signal["side"])
                    else:
                        status = "failed"
                        error = "Cryptorg принял webhook, но позиция не появилась в read-only API"
            except Exception as exc:
                status = "failed"
                error = str(exc)

        _insert_signal(
            user_id=user_id,
            connection_id=connection_id,
            strategy=row["strategy_code"],
            pair=pair,
            side=signal["side"],
            confidence=float(signal["confidence"]),
            status=status,
            reasons=signal["reasons"],
            payload=payload,
            response=response,
            error=error,
            order_volume=volume,
            leverage=int(row["leverage"]),
            strategy_settings=row,
        )


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


async def _confirm_position_opened(api_key: str, api_secret: str, pair: str, side: str) -> bool:
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
        if size > 0 and _position_side(row) == side:
            return True
    return False


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


def _insert_signal(
    user_id: int,
    connection_id: int | None,
    strategy: str,
    pair: str,
    side: str,
    confidence: float,
    status: str,
    reasons: list[str],
    payload: dict,
    response: dict | None = None,
    error: str | None = None,
    order_volume: float | None = None,
    leverage: int | None = None,
    strategy_settings: dict | None = None,
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
            strategy,
            pair,
            side,
            status,
            confidence,
            order_volume,
            leverage,
            json.dumps(reasons, ensure_ascii=False),
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
            strategy,
            pair,
            side,
            order_volume,
            leverage,
            payload=payload,
            signal_id=signal_id,
            sent_at=sent_at,
            signal_reasons=reasons,
            signal_confidence=confidence,
            strategy_settings=strategy_settings,
        )
    _prune_user_signals(user_id)


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


def _insert_risk_pause_signal(user_id: int, connection_id: int | None, strategy: str, reason: str) -> None:
    recent = fetch_one(
        """
        SELECT id FROM ai_signals
        WHERE user_id=%s AND connection_id <=> %s AND strategy_code=%s AND pair='RISK' AND status='skipped'
          AND created_at > DATE_SUB(NOW(), INTERVAL 30 MINUTE)
        LIMIT 1
        """,
        (user_id, connection_id, strategy),
    )
    if recent:
        return
    _insert_signal(
        user_id=user_id,
        connection_id=connection_id,
        strategy=strategy,
        pair="RISK",
        side="wait",
        confidence=0,
        status="skipped",
        reasons=[reason],
        payload={},
    )


async def _cleanup_after_take_profit(row: dict) -> None:
    if not settings.TP_DCA_CLEANUP_ENABLED or int(row.get("auto_trade") or 0) != 1:
        return
    user_id = int(row["user_id"])
    connection_id = int(row["connection_id"])
    api_key = row.get("bybit_api_key") or ""
    api_secret = decrypt_secret(row.get("bybit_api_secret_encrypted"))
    webhook_url = decrypt_secret(row.get("webhook_url_encrypted"))
    if not api_key or not api_secret or not webhook_url:
        return

    recent_signals = fetch_all(
        """
        SELECT pair, side, MAX(created_at) AS last_signal_at, UNIX_TIMESTAMP(MAX(created_at)) * 1000 AS last_signal_ms
        FROM ai_signals
        WHERE user_id=%s AND connection_id <=> %s AND strategy_code=%s
          AND (status='sent' OR response IS NOT NULL)
          AND pair <> 'RISK'
        GROUP BY pair, side
        """,
        (user_id, connection_id, row["strategy_code"]),
    )
    if not recent_signals:
        return

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - int(settings.TP_DCA_CLEANUP_LOOKBACK_MINUTES * 60 * 1000)
    try:
        active_positions = await positions(api_key, api_secret)
    except Exception as exc:
        execute(
            "UPDATE ai_user_connections SET last_error=%s, last_checked_at=NOW() WHERE id=%s",
            (f"TP cleanup position check error: {exc}", connection_id),
        )
        return
    cleanup_attempted: set[tuple[str, str]] = set()
    for signal in recent_signals:
        pair = str(signal.get("pair") or "").upper()
        side = str(signal.get("side") or "").lower()
        if side not in {"long", "short"} or not pair:
            continue
        cleanup_key = (pair, side)
        if cleanup_key in cleanup_attempted:
            continue
        try:
            orders = await order_history(api_key, api_secret, pair, start_ms, now_ms, limit=50)
        except Exception as exc:
            execute(
                "UPDATE ai_user_connections SET last_error=%s, last_checked_at=NOW() WHERE id=%s",
                (f"TP cleanup order history error: {exc}", connection_id),
            )
            continue

        for order in sorted(orders, key=lambda item: int(item.get("updatedTime") or item.get("createdTime") or 0)):
            if not _looks_like_take_profit(order):
                continue
            if not _take_profit_matches_position_side(order, side):
                continue
            tp_ms = int(order.get("updatedTime") or order.get("createdTime") or 0)
            if _has_newer_signal_after_tp(signal, tp_ms):
                continue
            if not _has_cleanup_target_position(active_positions, pair, side, tp_ms):
                continue
            source_ref = f"{pair}:{order.get('orderId') or order.get('orderLinkId')}:{order.get('updatedTime') or order.get('createdTime')}"
            if fetch_one(
                "SELECT id FROM ai_tp_cleanup_events WHERE user_id=%s AND connection_id <=> %s AND source_ref=%s LIMIT 1",
                (user_id, connection_id, source_ref),
            ):
                continue
            cleanup_attempted.add(cleanup_key)

            payload = ghost_webhook.build_close_payload(pair=pair, strategy=side, close_position=True)
            response = None
            status = "failed"
            error = None
            try:
                response = await ghost_webhook.send_payload(payload, webhook_url=webhook_url, confirm=True)
                status = "sent" if response.get("ok") else "failed"
                if status == "failed":
                    error = ghost_webhook.failure_message(response) or json.dumps(response.get("response"), ensure_ascii=False)
            except Exception as exc:
                error = str(exc)

            execute(
                """
                INSERT IGNORE INTO ai_tp_cleanup_events
                (user_id, connection_id, strategy_code, pair, side, source_ref, payload, response, status, error_message)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    connection_id,
                    row["strategy_code"],
                    pair,
                    side,
                    source_ref,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(response, ensure_ascii=False) if response else None,
                    status,
                    error,
                ),
            )


def _looks_like_take_profit(order: dict) -> bool:
    if str(order.get("orderStatus") or "").lower() != "filled":
        return False
    if str(order.get("reduceOnly") or "").lower() != "true":
        return False
    link_id = str(order.get("orderLinkId") or "")
    if link_id.startswith("crzyT"):
        return True
    stop_type = str(order.get("stopOrderType") or "").lower()
    return "take" in stop_type and "profit" in stop_type


def _take_profit_matches_position_side(order: dict, side: str) -> bool:
    order_side = str(order.get("side") or "").lower()
    if not order_side:
        return True
    if side == "long":
        return order_side == "sell"
    if side == "short":
        return order_side == "buy"
    return False


def _has_newer_signal_after_tp(signal: dict, tp_ms: int) -> bool:
    try:
        last_signal_ms = int(float(signal.get("last_signal_ms") or 0))
    except (TypeError, ValueError):
        last_signal_ms = 0
    grace_ms = int(settings.TP_DCA_CLEANUP_NEW_SIGNAL_GRACE_SECONDS * 1000)
    return bool(tp_ms and last_signal_ms and last_signal_ms > tp_ms + grace_ms)


def _has_cleanup_target_position(active_positions: list[dict], pair: str, side: str, tp_ms: int) -> bool:
    for position in active_positions:
        if str(position.get("symbol") or "").upper() != pair:
            continue
        if _position_side(position) != side:
            continue
        try:
            size = abs(float(position.get("size") or 0))
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            continue
        created_ms = _position_created_ms(position)
        if tp_ms and not created_ms:
            return False
        if tp_ms and created_ms and created_ms > tp_ms + int(settings.TP_DCA_CLEANUP_NEW_SIGNAL_GRACE_SECONDS * 1000):
            return False
        return True
    return False


def _position_created_ms(position: dict) -> int:
    for key in ("createdTime", "createdAt", "created_time"):
        try:
            value = int(position.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 0


async def agent_loop() -> None:
    while True:
        try:
            await scan_once()
        except Exception:
            pass
        await asyncio.sleep(settings.AGENT_INTERVAL_SECONDS)


async def _risk_pause_reason(api_key: str, api_secret: str, balance: float) -> str | None:
    status = await risk_pause_status(api_key, api_secret, balance)
    return status["reason"] if status else None


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


def _risk_pause_override_active(user_id: int) -> bool:
    return bool(fetch_one(
        "SELECT user_id FROM ai_risk_pause_overrides WHERE user_id=%s AND override_until > NOW()",
        (user_id,),
    ))
