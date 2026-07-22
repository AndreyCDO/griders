"""Read-only live trade history diagnostics for Griders.

Run this on the server where the webapp database environment is configured:

    ./venv/bin/python tools/analyze_live_trade_history.py --days 45

The script prints anonymized aggregates only. It does not read or print API
keys, emails, encrypted secrets, raw payloads, or user identifiers.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.db import fetch_all  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze anonymized Griders live trade history.")
    parser.add_argument("--days", type=int, default=45, help="Lookback window ending at --end.")
    parser.add_argument("--end", default="", help="UTC ISO timestamp. Default: now.")
    parser.add_argument("--strategy", default="grid_dca_v2", help="Strategy code to include.")
    parser.add_argument("--json-out", default="", help="Optional output JSON path.")
    args = parser.parse_args()

    end_dt = _parse_end(args.end)
    start_dt = end_dt - timedelta(days=max(1, int(args.days)))
    params = {
        "start": _mysql_dt(start_dt),
        "end": _mysql_dt(end_dt),
        "strategy": args.strategy,
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "days": max(1, int(args.days)),
        },
        "strategy_code": args.strategy,
        "coverage": _one(COVERAGE_SQL, params),
        "signal_status": _rows(SIGNAL_STATUS_SQL, params),
        "event_status": _rows(EVENT_STATUS_SQL, params),
        "closed_summary": _one(CLOSED_SUMMARY_SQL, params),
        "by_pair": _rows(BY_PAIR_SQL, params),
        "by_side_reason": _rows(BY_SIDE_REASON_SQL, params),
        "by_stage_reason": _rows(BY_STAGE_REASON_SQL, params),
        "by_day": _rows(BY_DAY_SQL, params),
        "latency": _one(LATENCY_SQL, params),
        "data_quality": _one(DATA_QUALITY_SQL, params),
        "model_calibration": {},
    }
    report["model_calibration"] = _calibration(report)

    text = json.dumps(_json_safe(report), ensure_ascii=False, indent=2)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)


COVERAGE_SQL = """
SELECT
  COUNT(*) AS total_rows,
  SUM(status='open') AS open_rows,
  SUM(status='closed') AS closed_rows,
  SUM(status='canceled') AS canceled_rows,
  MIN(sent_at) AS min_sent_at,
  MAX(sent_at) AS max_sent_at,
  MIN(closed_at) AS min_closed_at,
  MAX(closed_at) AS max_closed_at,
  SUM(closed_pnl IS NOT NULL) AS rows_with_pnl,
  SUM(raw_closed_pnl IS NOT NULL) AS rows_with_raw_closed,
  SUM(signal_id IS NOT NULL) AS rows_with_signal,
  SUM(grid_snapshot IS NOT NULL) AS rows_with_grid,
  SUM(strategy_snapshot IS NOT NULL) AS rows_with_strategy_snapshot,
  SUM(hold_seconds IS NOT NULL) AS rows_with_hold_seconds
FROM ai_site_trade_deals
WHERE strategy_code=%(strategy)s
  AND sent_at >= %(start)s
  AND sent_at < %(end)s
"""

SIGNAL_STATUS_SQL = """
SELECT
  status,
  COUNT(*) AS signals,
  SUM(webhook_response_ms IS NOT NULL) AS with_response_ms,
  AVG(webhook_response_ms) AS avg_response_ms,
  SUM(position_confirmed_at IS NOT NULL) AS position_confirmed,
  SUM(protective_orders_confirmed_at IS NOT NULL) AS protective_confirmed,
  SUM(confirmation_status <> '') AS with_confirmation_status
FROM ai_signals
WHERE strategy_code=%(strategy)s
  AND created_at >= %(start)s
  AND created_at < %(end)s
GROUP BY status
ORDER BY status
"""

EVENT_STATUS_SQL = """
SELECT
  side,
  COUNT(*) AS events,
  SUM(processed_at IS NOT NULL) AS processed,
  SUM(processing_error IS NOT NULL AND processing_error <> '') AS errors
FROM ai_tradingview_events
WHERE strategy_code=%(strategy)s
  AND created_at >= %(start)s
  AND created_at < %(end)s
GROUP BY side
ORDER BY side
"""

CLOSED_SUMMARY_SQL = """
SELECT
  COUNT(*) AS trades,
  SUM(outcome='win') AS wins,
  SUM(outcome='loss') AS losses,
  SUM(close_reason='take_profit') AS take_profit,
  SUM(close_reason='stop_loss') AS stop_loss,
  SUM(close_reason='manual') AS manual,
  SUM(close_reason='unknown') AS unknown_close,
  SUM(closed_pnl) AS pnl,
  AVG(closed_pnl) AS avg_pnl,
  MIN(closed_pnl) AS worst_pnl,
  MAX(closed_pnl) AS best_pnl,
  AVG(roi_pct) AS avg_roi_pct,
  AVG(r_multiple) AS avg_r_multiple,
  AVG(hold_seconds) AS avg_hold_seconds,
  AVG(matched_safety_orders) AS avg_safety_orders
FROM ai_site_trade_deals
WHERE strategy_code=%(strategy)s
  AND status='closed'
  AND closed_at >= %(start)s
  AND closed_at < %(end)s
"""

BY_PAIR_SQL = """
SELECT
  pair,
  COUNT(*) AS trades,
  SUM(outcome='win') AS wins,
  SUM(outcome='loss') AS losses,
  SUM(close_reason='stop_loss') AS stops,
  SUM(closed_pnl) AS pnl,
  AVG(closed_pnl) AS avg_pnl,
  MIN(closed_pnl) AS worst_pnl,
  MAX(closed_pnl) AS best_pnl,
  AVG(roi_pct) AS avg_roi_pct,
  AVG(r_multiple) AS avg_r_multiple
FROM ai_site_trade_deals
WHERE strategy_code=%(strategy)s
  AND status='closed'
  AND closed_at >= %(start)s
  AND closed_at < %(end)s
GROUP BY pair
ORDER BY pnl ASC, trades DESC
LIMIT 100
"""

BY_SIDE_REASON_SQL = """
SELECT
  side,
  close_reason,
  COUNT(*) AS trades,
  SUM(closed_pnl) AS pnl,
  AVG(closed_pnl) AS avg_pnl,
  AVG(roi_pct) AS avg_roi_pct,
  AVG(r_multiple) AS avg_r_multiple
FROM ai_site_trade_deals
WHERE strategy_code=%(strategy)s
  AND status='closed'
  AND closed_at >= %(start)s
  AND closed_at < %(end)s
GROUP BY side, close_reason
ORDER BY side, close_reason
"""

BY_STAGE_REASON_SQL = """
SELECT
  JSON_UNQUOTE(JSON_EXTRACT(signal_reasons, '$[1]')) AS first_reason,
  JSON_UNQUOTE(JSON_EXTRACT(payload, '$.params.open.strategy')) AS payload_side,
  JSON_UNQUOTE(JSON_EXTRACT(grid_snapshot, '$.close.value')) AS tp_value,
  close_reason,
  COUNT(*) AS trades,
  SUM(closed_pnl) AS pnl,
  AVG(closed_pnl) AS avg_pnl,
  AVG(matched_safety_orders) AS avg_safety_orders
FROM ai_site_trade_deals
WHERE strategy_code=%(strategy)s
  AND status='closed'
  AND closed_at >= %(start)s
  AND closed_at < %(end)s
GROUP BY first_reason, payload_side, tp_value, close_reason
ORDER BY pnl ASC, trades DESC
LIMIT 100
"""

BY_DAY_SQL = """
SELECT
  DATE(closed_at) AS day,
  COUNT(*) AS trades,
  SUM(closed_pnl) AS pnl,
  SUM(close_reason='stop_loss') AS stops,
  SUM(outcome='win') AS wins,
  SUM(outcome='loss') AS losses
FROM ai_site_trade_deals
WHERE strategy_code=%(strategy)s
  AND status='closed'
  AND closed_at >= %(start)s
  AND closed_at < %(end)s
GROUP BY DATE(closed_at)
ORDER BY day ASC
"""

LATENCY_SQL = """
SELECT
  COUNT(*) AS sent_signals,
  AVG(webhook_response_ms) AS avg_webhook_response_ms,
  MAX(webhook_response_ms) AS max_webhook_response_ms,
  AVG(TIMESTAMPDIFF(SECOND, webhook_response_at, position_confirmed_at)) AS avg_position_confirm_seconds,
  AVG(TIMESTAMPDIFF(SECOND, webhook_response_at, protective_orders_confirmed_at)) AS avg_protective_confirm_seconds,
  SUM(confirmation_status='confirmed') AS confirmed,
  SUM(confirmation_status='protective_repaired') AS protective_repaired,
  SUM(confirmation_status LIKE 'fast_%%') AS fast_failures,
  SUM(confirmation_status='confirmation_error') AS confirmation_errors
FROM ai_signals
WHERE strategy_code=%(strategy)s
  AND status='sent'
  AND created_at >= %(start)s
  AND created_at < %(end)s
"""

DATA_QUALITY_SQL = """
SELECT
  SUM(status='closed' AND closed_pnl IS NULL) AS closed_without_pnl,
  SUM(status='closed' AND closed_at IS NULL) AS closed_without_closed_at,
  SUM(status='closed' AND raw_closed_pnl IS NULL) AS closed_without_raw_row,
  SUM(status='closed' AND avg_entry_price IS NULL) AS closed_without_entry_price,
  SUM(status='closed' AND avg_exit_price IS NULL) AS closed_without_exit_price,
  SUM(status='closed' AND matched_safety_orders IS NULL) AS closed_without_safety_match,
  SUM(signal_id IS NULL) AS rows_without_signal_id,
  SUM(grid_snapshot IS NULL) AS rows_without_grid_snapshot,
  SUM(strategy_snapshot IS NULL) AS rows_without_strategy_snapshot
FROM ai_site_trade_deals
WHERE strategy_code=%(strategy)s
  AND sent_at >= %(start)s
  AND sent_at < %(end)s
"""


def _rows(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return list(fetch_all(sql, params))


def _one(sql: str, params: dict[str, Any]) -> dict[str, Any]:
    rows = _rows(sql, params)
    return rows[0] if rows else {}


def _calibration(report: dict[str, Any]) -> dict[str, Any]:
    coverage = report.get("coverage") or {}
    closed = report.get("closed_summary") or {}
    signal_rows = report.get("signal_status") or []
    signal_total = sum(_int(row.get("signals")) for row in signal_rows)
    sent = sum(_int(row.get("signals")) for row in signal_rows if row.get("status") == "sent")
    failed = sum(_int(row.get("signals")) for row in signal_rows if row.get("status") == "failed")
    skipped = sum(_int(row.get("signals")) for row in signal_rows if row.get("status") == "skipped")
    trades = _int(closed.get("trades"))
    wins = _int(closed.get("wins"))
    losses = _int(closed.get("losses"))
    stop_loss = _int(closed.get("stop_loss"))
    return {
        "signal_to_sent_rate": _ratio(sent, signal_total),
        "signal_skip_rate": _ratio(skipped, signal_total),
        "signal_fail_rate": _ratio(failed, signal_total),
        "sent_to_closed_rate": _ratio(_int(coverage.get("closed_rows")), sent),
        "open_still_pending_rate": _ratio(_int(coverage.get("open_rows")), sent),
        "live_win_rate": _ratio(wins, trades),
        "live_loss_rate": _ratio(losses, trades),
        "live_stop_rate": _ratio(stop_loss, trades),
        "avg_live_pnl": _float(closed.get("avg_pnl")),
        "avg_live_roi_pct": _float(closed.get("avg_roi_pct")),
        "avg_live_r_multiple": _float(closed.get("avg_r_multiple")),
        "avg_live_safety_orders": _float(closed.get("avg_safety_orders")),
        "suggested_backtest_adjustments": [
            "Filter simulated entries by observed signal_to_sent_rate and real skip reasons.",
            "Replace candle-based TP/SL PnL with empirical distributions by pair, side, close_reason and safety-order depth.",
            "Apply observed open-still-pending, failed webhook, confirmation repair and manual-close rates.",
            "Calibrate fees/slippage/funding from raw_closed_pnl, avg_entry_price, avg_exit_price and closed_pnl.",
            "Report live-calibrated confidence separately from pure candle backtest.",
        ],
    }


def _parse_end(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mysql_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        json.dumps(value)
        return value
    except TypeError:
        return float(value) if isinstance(value, (int, float)) else str(value)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _ratio(part: int, total: int) -> float:
    return (float(part) / float(total)) if total else 0.0


if __name__ == "__main__":
    main()
