"""Persistent site-wide deal counter based on confirmed Griders webhooks."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from . import settings
from .db import execute, fetch_all, fetch_one

COUNTER_KEY = "site_totals_v2"
COUNTER_START_DATE = "2026-06-08"
COUNTER_START_UTC = "2026-06-08 00:00:00"
TAKER_ROUNDTRIP_FEE_RATE = 0.001
REPORT_TIMEZONE = ZoneInfo("Europe/Moscow")


def site_totals() -> dict:
    users_row = fetch_one("SELECT COUNT(*) AS users_count FROM ai_users") or {}
    active_users_row = fetch_one(
        """
        SELECT COUNT(*) AS active_users_count
        FROM ai_user_admin_stats
        WHERE connection_status='active'
        """
    ) or {}
    deals_row = fetch_one(
        """
        SELECT COUNT(*) AS deals_count,
               COALESCE(SUM(
                   COALESCE(api_entry_value, 0)
                   + CASE
                       WHEN ABS(COALESCE(qty, 0) * COALESCE(avg_exit_price, 0)) > 0
                         THEN ABS(COALESCE(qty, 0) * COALESCE(avg_exit_price, 0))
                       WHEN COALESCE(api_entry_value, 0) > 0
                         THEN COALESCE(api_entry_value, 0)
                       ELSE 0
                     END
               ), 0) AS traded_volume
        FROM ai_site_trade_deals
        WHERE status='closed' AND closed_at IS NOT NULL
        """
    ) or {}
    return {
        "users_count": int(users_row.get("users_count") or 0),
        "active_users_count": int(active_users_row.get("active_users_count") or 0),
        "deals_count": int(deals_row.get("deals_count") or 0),
        "traded_volume": _float(deals_row.get("traded_volume")),
        "counted_from": COUNTER_START_DATE,
    }


def trade_analysis_summary() -> list[dict]:
    rows = fetch_all(
        """
        SELECT
            strategy_code,
            side,
            CASE
                WHEN close_reason IN ('unknown', 'manual') AND closed_pnl <= 0 THEN 'stop_loss'
                WHEN close_reason IN ('unknown', 'manual') AND closed_pnl > 0 THEN 'take_profit'
                ELSE close_reason
            END AS close_reason,
            COUNT(*) AS trades_count,
            SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins_count,
            SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) AS losses_count,
            COALESCE(SUM(closed_pnl), 0) AS total_pnl,
            COALESCE(AVG(closed_pnl), 0) AS avg_pnl,
            COALESCE(AVG(roi_pct), 0) AS avg_roi_pct,
            COALESCE(AVG(r_multiple), 0) AS avg_r_multiple,
            COALESCE(AVG(hold_seconds), 0) AS avg_hold_seconds
        FROM ai_site_trade_deals
        WHERE status='closed'
        GROUP BY
            strategy_code,
            side,
            CASE
                WHEN close_reason IN ('unknown', 'manual') AND closed_pnl <= 0 THEN 'stop_loss'
                WHEN close_reason IN ('unknown', 'manual') AND closed_pnl > 0 THEN 'take_profit'
                ELSE close_reason
            END
        ORDER BY total_pnl ASC, trades_count DESC
        """
    )
    return [
        {
            "strategy_code": row.get("strategy_code") or "",
            "side": row.get("side") or "",
            "close_reason": row.get("close_reason") or "unknown",
            "trades_count": int(row.get("trades_count") or 0),
            "wins_count": int(row.get("wins_count") or 0),
            "losses_count": int(row.get("losses_count") or 0),
            "win_rate": _safe_pct(row.get("wins_count"), row.get("trades_count")),
            "total_pnl": _float(row.get("total_pnl")),
            "avg_pnl": _float(row.get("avg_pnl")),
            "avg_roi_pct": _float(row.get("avg_roi_pct")),
            "avg_r_multiple": _float(row.get("avg_r_multiple")),
            "avg_hold_seconds": int(_float(row.get("avg_hold_seconds"))),
        }
        for row in rows
    ]


def record_sent_webhook(
    user_id: int,
    connection_id: int | None,
    strategy_code: str,
    pair: str,
    side: str,
    order_volume: float | None,
    leverage: int | None,
    payload: dict | None = None,
    signal_id: int | None = None,
    sent_at: str | None = None,
    signal_reasons: list[str] | None = None,
    signal_confidence: float | None = None,
    strategy_settings: dict | None = None,
) -> None:
    if side not in {"long", "short"}:
        return
    sent_at = sent_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if sent_at < COUNTER_START_UTC:
        return
    _ensure_counter()
    payload = payload or {}
    plan = _deal_plan(payload, order_volume, leverage)
    signal = _signal_snapshot(signal_id)
    if signal:
        if signal_reasons is None:
            signal_reasons = _json_text_list(signal.get("reasons"))
        if signal_confidence is None:
            signal_confidence = _optional_float(signal.get("confidence"))
    inserted = execute(
        """
        INSERT IGNORE INTO ai_site_trade_deals
        (signal_id, user_id, connection_id, strategy_code, pair, side, signal_confidence,
         signal_reasons, strategy_snapshot, grid_snapshot, status, sent_at, payload,
         expected_profits, planned_volumes, full_volume, active_safety_orders)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', %s, %s, %s, %s, %s, %s)
        """,
        (
            signal_id,
            user_id,
            connection_id,
            strategy_code,
            pair.upper(),
            side,
            signal_confidence,
            _json_dumps(signal_reasons or []),
            _json_dumps(_strategy_snapshot(strategy_settings)),
            _json_dumps(_grid_snapshot(payload)),
            sent_at,
            _json_dumps(payload),
            _json_dumps(plan["expected_profits"]),
            _json_dumps(plan["planned_volumes"]),
            plan["full_volume"],
            plan["active_safety_orders"],
        ),
    )
    if inserted:
        execute(
            """
            UPDATE ai_site_trade_counter
            SET deals_count=deals_count+1, updated_at=NOW()
            WHERE counter_key=%s
            """,
            (COUNTER_KEY,),
        )
        refresh_daily_site_trade_stats(_local_date_from_utc_string(sent_at))


def process_closed_rows_for_counter(user_id: int, connection_id: int | None, closed_rows: list[dict]) -> None:
    for row in sorted(closed_rows, key=lambda item: int(_float(item.get("updatedTime") or item.get("createdTime")))):
        pair = str(row.get("symbol") or "").upper()
        side = _position_side_from_closed_row(row)
        if not pair or side not in {"long", "short"}:
            continue
        closed_ref = _closed_ref(row)
        if not closed_ref or _closed_ref_used(closed_ref):
            continue
        closed_at = _ms_to_mysql_datetime(row.get("updatedTime") or row.get("createdTime"))
        if not closed_at:
            continue
        deal = fetch_one(
            """
            SELECT *
            FROM ai_site_trade_deals
            WHERE user_id=%s
              AND connection_id <=> %s
              AND pair=%s
              AND side=%s
              AND (
                  status='open'
                  OR (status='canceled' AND close_order_type='phantom_open_cleanup' AND closed_at IS NULL)
              )
              AND sent_at >= DATE_SUB(%s, INTERVAL 14 DAY)
              AND sent_at <= %s
            ORDER BY
                CASE WHEN status='open' THEN 0 ELSE 1 END,
                sent_at DESC,
                id DESC
            LIMIT 1
            """,
            (user_id, connection_id, pair, side, closed_at, closed_at),
        )
        if not deal:
            continue
        pnl = _effective_cryptorg_pnl(row, side)
        safety_orders, volume = _infer_safety_orders(deal, pnl)
        entry_value = _closed_entry_value(row)
        closed_dt = _parse_mysql_datetime(closed_at)
        sent_dt = _parse_mysql_datetime(str(deal.get("sent_at") or ""))
        hold_seconds = _hold_seconds(sent_dt, closed_dt)
        r_multiple = _r_multiple(deal, safety_orders, pnl)
        updated = execute(
            """
            UPDATE ai_site_trade_deals
            SET status='closed',
                closed_at=%s,
                closed_ref=%s,
                closed_pnl=%s,
                api_entry_value=%s,
                qty=%s,
                avg_entry_price=%s,
                avg_exit_price=%s,
                roi_pct=%s,
                r_multiple=%s,
                outcome=%s,
                close_reason=%s,
                close_order_type=%s,
                hold_seconds=%s,
                raw_closed_pnl=%s,
                matched_safety_orders=%s,
                credited_volume=%s,
                updated_at=NOW()
            WHERE id=%s
              AND (
                  status='open'
                  OR (status='canceled' AND close_order_type='phantom_open_cleanup' AND closed_at IS NULL)
              )
            """,
            (
                closed_at,
                closed_ref,
                pnl,
                entry_value,
                _optional_float(row.get("qty")),
                _optional_float(row.get("avgEntryPrice")),
                _optional_float(row.get("avgExitPrice")),
                (pnl / entry_value * 100.0) if entry_value > 0 else None,
                r_multiple,
                _outcome(pnl),
                _close_reason(row, pnl),
                str(row.get("orderType") or row.get("stopOrderType") or "")[:80],
                hold_seconds,
                json.dumps(row, ensure_ascii=False, default=str),
                safety_orders,
                volume,
                int(deal["id"]),
            ),
        )
        if updated:
            execute(
                """
                UPDATE ai_site_trade_counter
                SET traded_volume=traded_volume+%s, updated_at=NOW()
                WHERE counter_key=%s
                """,
                (volume, COUNTER_KEY),
            )
            refresh_daily_site_trade_stats(_local_date_from_utc_string(closed_at))


def refresh_recent_daily_site_trade_stats() -> None:
    today = datetime.now(REPORT_TIMEZONE).date()
    refresh_daily_site_trade_stats(today - timedelta(days=1))
    refresh_daily_site_trade_stats(today)


def refresh_daily_site_trade_stats(stat_date: date | str | None = None) -> dict:
    day = _coerce_date(stat_date) or datetime.now(REPORT_TIMEZONE).date()
    start_utc, end_utc = _day_bounds_utc(day)
    row = fetch_one(
        """
        SELECT
            (SELECT COUNT(*)
             FROM ai_site_trade_deals
             WHERE sent_at >= %s AND sent_at < %s) AS sent_deals_count,
            (SELECT COUNT(*)
             FROM ai_site_trade_deals
             WHERE status='closed' AND closed_at >= %s AND closed_at < %s) AS closed_deals_count,
            (SELECT COALESCE(SUM(
                COALESCE(api_entry_value, 0)
                + CASE
                    WHEN ABS(COALESCE(qty, 0) * COALESCE(avg_exit_price, 0)) > 0
                      THEN ABS(COALESCE(qty, 0) * COALESCE(avg_exit_price, 0))
                    WHEN COALESCE(api_entry_value, 0) > 0
                      THEN COALESCE(api_entry_value, 0)
                    ELSE 0
                  END
             ), 0)
             FROM ai_site_trade_deals
             WHERE status='closed' AND closed_at >= %s AND closed_at < %s) AS traded_volume
        """,
        (
            start_utc,
            end_utc,
            start_utc,
            end_utc,
            start_utc,
            end_utc,
        ),
    ) or {}
    execute(
        """
        INSERT INTO ai_site_trade_daily_stats
            (stat_date, sent_deals_count, closed_deals_count, traded_volume, calculated_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            sent_deals_count=VALUES(sent_deals_count),
            closed_deals_count=VALUES(closed_deals_count),
            traded_volume=VALUES(traded_volume),
            calculated_at=VALUES(calculated_at)
        """,
        (
            day.isoformat(),
            int(row.get("sent_deals_count") or 0),
            int(row.get("closed_deals_count") or 0),
            _float(row.get("traded_volume")),
        ),
    )
    return {
        "stat_date": day.isoformat(),
        "sent_deals_count": int(row.get("sent_deals_count") or 0),
        "closed_deals_count": int(row.get("closed_deals_count") or 0),
        "traded_volume": _float(row.get("traded_volume")),
    }


def _ensure_counter() -> None:
    execute(
        """
        INSERT IGNORE INTO ai_site_trade_counter
        (counter_key, counted_from, deals_count, traded_volume)
        VALUES (%s, %s, 0, 0)
        """,
        (COUNTER_KEY, COUNTER_START_DATE),
    )


def _deal_plan(payload: dict, order_volume: float | None, leverage: int | None) -> dict:
    params = payload.get("params") if isinstance(payload, dict) else {}
    params = params if isinstance(params, dict) else {}
    open_params = params.get("open") if isinstance(params.get("open"), dict) else {}
    dca_params = params.get("dca") if isinstance(params.get("dca"), dict) else {}
    close_params = params.get("close") if isinstance(params.get("close"), dict) else {}

    lev = max(1, int(_float(open_params.get("leverage") or leverage or 10)))
    first_margin = _float(open_params.get("orderVolume") or order_volume)
    dca_enabled = bool(dca_params.get("enabled", False))
    dca_active = int(_float(dca_params.get("active") or 0)) if dca_enabled else 0
    dca_margin = _float(dca_params.get("volume") or first_margin)
    multiplier = _float(dca_params.get("multiplierVolume") or 1.0) or 1.0
    tp_pct = _float(close_params.get("value") or 0)

    margins = [max(0.0, first_margin)]
    leg = max(0.0, dca_margin)
    for _ in range(max(0, dca_active)):
        margins.append(leg)
        leg *= multiplier

    planned_volumes = []
    expected_profits = []
    cumulative_margin = 0.0
    for margin in margins:
        cumulative_margin += margin
        notional = cumulative_margin * lev
        planned_volumes.append(round(notional, 8))
        effective_tp = max(0.0, tp_pct / 100.0 - TAKER_ROUNDTRIP_FEE_RATE)
        expected_profits.append(round(notional * effective_tp, 8))
    if not planned_volumes:
        planned_volumes = [0.0]
        expected_profits = [0.0]
    return {
        "planned_volumes": planned_volumes,
        "expected_profits": expected_profits,
        "full_volume": planned_volumes[-1],
        "active_safety_orders": max(0, dca_active),
    }


def _signal_snapshot(signal_id: int | None) -> dict | None:
    if not signal_id:
        return None
    return fetch_one(
        "SELECT confidence, reasons FROM ai_signals WHERE id=%s LIMIT 1",
        (signal_id,),
    )


def _strategy_snapshot(settings_row: dict | None) -> dict:
    if not settings_row:
        return {}
    keys = [
        "strategy_code",
        "enabled",
        "auto_trade",
        "risk_pct",
        "min_order_volume",
        "first_order_mode",
        "leverage",
        "max_active_deals",
        "max_long_deals",
        "max_short_deals",
        "watchlist",
    ]
    return {key: _json_safe_value(settings_row.get(key)) for key in keys if key in settings_row}


def _grid_snapshot(payload: dict) -> dict:
    params = payload.get("params") if isinstance(payload, dict) else {}
    params = params if isinstance(params, dict) else {}
    return {
        "open": params.get("open") if isinstance(params.get("open"), dict) else {},
        "dca": params.get("dca") if isinstance(params.get("dca"), dict) else {},
        "close": params.get("close") if isinstance(params.get("close"), dict) else {},
    }


def _infer_safety_orders(deal: dict, pnl: float) -> tuple[int, float]:
    planned_volumes = _json_list(deal.get("planned_volumes"))
    expected_profits = _json_list(deal.get("expected_profits"))
    if not planned_volumes:
        full = _float(deal.get("full_volume"))
        return 0, full
    if pnl < 0:
        idx = len(planned_volumes) - 1
        return idx, float(planned_volumes[idx])
    if not expected_profits:
        return 0, float(planned_volumes[0])
    size = min(len(expected_profits), len(planned_volumes))
    idx = min(range(size), key=lambda index: abs(float(expected_profits[index]) - pnl))
    return idx, float(planned_volumes[idx])


def _r_multiple(deal: dict, safety_orders: int, pnl: float) -> float | None:
    expected_profits = _json_list(deal.get("expected_profits"))
    if not expected_profits:
        return None
    idx = min(max(0, safety_orders), len(expected_profits) - 1)
    expected = abs(float(expected_profits[idx]))
    return pnl / expected if expected > 0 else None


def _outcome(pnl: float) -> str:
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "breakeven"


def _close_reason(row: dict, pnl: float) -> str:
    stop_type = str(row.get("stopOrderType") or "").lower()
    order_type = str(row.get("orderType") or "").lower()
    if "take" in stop_type and "profit" in stop_type:
        return "take_profit"
    if "stop" in stop_type or "loss" in stop_type:
        return "stop_loss"
    if pnl > 0 and order_type == "limit":
        return "take_profit"
    if pnl < 0 and order_type == "market":
        return "stop_loss"
    return "manual" if order_type else "unknown"


def _position_side_from_closed_row(row: dict) -> str:
    raw_side = str(row.get("side") or "").lower()
    return "short" if raw_side == "buy" else ("long" if raw_side == "sell" else raw_side)


def _closed_ref(row: dict) -> str:
    symbol = str(row.get("symbol") or "").upper()
    order_id = str(row.get("orderId") or "")
    updated = str(row.get("updatedTime") or row.get("createdTime") or "")
    return f"{symbol}:{order_id}:{updated}" if symbol and updated else ""


def _closed_ref_used(closed_ref: str) -> bool:
    return bool(fetch_one("SELECT id FROM ai_site_trade_deals WHERE closed_ref=%s LIMIT 1", (closed_ref,)))


def _ms_to_mysql_datetime(value) -> str:
    ms = _float(value)
    if ms <= 0:
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_mysql_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _hold_seconds(sent_at: datetime | None, closed_at: datetime | None) -> int | None:
    if not sent_at or not closed_at:
        return None
    seconds = int((closed_at - sent_at).total_seconds())
    return seconds if seconds >= 0 else None


def _closed_entry_value(row: dict) -> float:
    calculated = abs(_float(row.get("qty")) * _float(row.get("avgEntryPrice")))
    if calculated > 0:
        return calculated
    return _float(row.get("cumEntryValue"))


def _cryptorg_display_pnl(row: dict, position_side: str) -> float | None:
    qty = abs(_float(row.get("qty")))
    entry = _float(row.get("avgEntryPrice"))
    exit_price = _float(row.get("avgExitPrice"))
    if qty <= 0 or entry <= 0 or exit_price <= 0:
        return None
    if position_side == "long":
        gross = (exit_price - entry) * qty
    elif position_side == "short":
        gross = (entry - exit_price) * qty
    else:
        return None
    entry_value = qty * entry
    exit_value = qty * exit_price
    entry_fee_rate = max(0.0, float(settings.CRYPTORG_TAKER_FEE_PCT)) / 100.0
    close_type = str(row.get("orderType") or row.get("stopOrderType") or "").lower()
    close_fee_pct = settings.CRYPTORG_MAKER_FEE_PCT if close_type == "limit" else settings.CRYPTORG_TAKER_FEE_PCT
    close_fee_rate = max(0.0, float(close_fee_pct)) / 100.0
    return gross - (entry_value * entry_fee_rate) - (exit_value * close_fee_rate)


def _effective_cryptorg_pnl(row: dict, position_side: str) -> float:
    api_pnl = _float(row.get("closedPnl"))
    if not _closed_entry_value_anomalous(row):
        return api_pnl
    calculated = _cryptorg_display_pnl(row, position_side)
    return calculated if calculated is not None else api_pnl


def _closed_entry_value_anomalous(row: dict) -> bool:
    reported = _float(row.get("cumEntryValue"))
    calculated = abs(_float(row.get("qty")) * _float(row.get("avgEntryPrice")))
    if reported <= 0 or calculated <= 0:
        return False
    return abs(reported - calculated) > max(1.0, calculated * 0.05)


def _day_bounds_utc(day: date) -> tuple[str, str]:
    start_local = datetime(day.year, day.month, day.day, tzinfo=REPORT_TIMEZONE)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        end_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )


def _coerce_date(value: date | str | None) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.astimezone(REPORT_TIMEZONE).date()
    if value:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return None


def _local_date_from_utc_string(value: str) -> date:
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.now(REPORT_TIMEZONE).date()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(REPORT_TIMEZONE).date()


def _json_list(value) -> list[float]:
    try:
        items = json.loads(value or "[]") if isinstance(value, str) else (value or [])
        return [float(item) for item in items]
    except Exception:
        return []


def _json_text_list(value) -> list[str]:
    try:
        items = json.loads(value or "[]") if isinstance(value, str) else (value or [])
        return [str(item) for item in items]
    except Exception:
        return []


def _json_dumps(value) -> str:
    return json.dumps(_json_safe_value(value), ensure_ascii=False)


def _json_safe_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    return value


def _optional_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_pct(part: object, total: object) -> float:
    total_float = _float(total)
    return (_float(part) / total_float * 100.0) if total_float > 0 else 0.0


def _float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
