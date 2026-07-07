"""Research backtest for GRID DCA 2.8 with a downtrend long rebound exception.

This script does not change production strategy code. It compares against the
current internal baseline: TP+5, EMA20 guard, no same-side cooldown.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_26_vs_31 import _signal_at, hourly_rsi_by_15m, indicators
from tools.backtest_grid_dca_27_wide_limits_tp5_ema20_guard import (
    TARIFFS,
    TP_MULTIPLIER,
    compact_tariff,
)


BASELINE_JSON = Path(".private_reports/grid-dca-27-wide-limits-tp-plus5-ema20-no-side-cooldown-summary.json")
OUT_JSON = Path(".private_reports/grid-dca-28-pullback-long-exception-summary.json")
OUT_HTML = Path(".private_reports/grid-dca-28-pullback-long-exception-comparison.html")


def _pct_change(values: list[float], index: int, lookback: int) -> float:
    if index < lookback or not values[index - lookback]:
        return 0.0
    return (values[index] - values[index - lookback]) / values[index - lookback] * 100.0


def _downtrend_long_exception(signal: dict, ind: dict, btc: dict, eth: dict, index: int) -> bool:
    if signal["side"] != "long":
        return False
    open_price = ind["o"][index]
    close_price = ind["c"][index]
    candle_pct = (close_price - open_price) / open_price * 100.0 if open_price else 0.0
    btc_move_3 = _pct_change(btc["c"], index, 3)
    eth_move_3 = _pct_change(eth["c"], index, 3)
    return (
        signal["stage"] == "pullback"
        and candle_pct >= 0.20
        and btc_move_3 > -0.6
        and eth_move_3 > -0.6
        and float(signal["volratio"]) >= 0.7
        and float(signal["rsi60"]) >= 45.0
    )


def all_signal_candidates_rebound_exception(start: datetime, all_rows: dict[str, list]) -> tuple[list[dict], dict]:
    btc = indicators(all_rows["BTCUSDT"])
    eth = indicators(all_rows["ETHUSDT"])
    trend_context = base._daily_trend_context(all_rows["BTCUSDT"], all_rows["ETHUSDT"])
    start_ms = int(start.timestamp() * 1000)
    candidates: list[dict] = []
    skipped = {
        "tradingview_daily_regime_long": 0,
        "tradingview_daily_regime_long_allowed_exception": 0,
        "tradingview_daily_regime_short": 0,
        "server_ema20_long": 0,
        "server_ema20_short": 0,
    }
    for symbol in base.ALL_PAIRS:
        rows = all_rows[symbol]
        ind = indicators(rows)
        rsi60 = hourly_rsi_by_15m(rows)
        for index in range(max(50, 30, 20, 14, 3), len(rows) - 1):
            signal = _signal_at(base.SIGNAL_VERSION, ind, index, btc, eth, rsi60)
            if not signal:
                continue
            trend = base._trend_for_bar(trend_context, int(rows[index][0]))
            btc_above = bool(trend.get("btc_daily_above_ema20", True))
            eth_above = bool(trend.get("eth_daily_above_ema20", True))
            side = signal["side"]
            trend_exception = ""
            if side == "long" and trend.get("regime") == "downtrend":
                if not _downtrend_long_exception(signal, ind, btc, eth, index):
                    skipped["tradingview_daily_regime_long"] += 1
                    continue
                skipped["tradingview_daily_regime_long_allowed_exception"] += 1
                trend_exception = "downtrend_rebound_long"
            if side == "short" and trend.get("regime") == "uptrend":
                skipped["tradingview_daily_regime_short"] += 1
                continue
            if side == "long" and not btc_above and not eth_above and not trend_exception:
                skipped["server_ema20_long"] += 1
                continue
            if side == "short" and btc_above and eth_above:
                skipped["server_ema20_short"] += 1
                continue

            signal = base._with_dca_max(signal)
            signal["grid"] = {**signal["grid"], "tp": float(signal["grid"]["tp"]) * TP_MULTIPLIER}
            entry_index = index + 1
            entry_time = int(rows[entry_index][0])
            if entry_time < start_ms:
                continue
            candidates.append({
                "symbol": symbol,
                "side": side,
                "stage": signal["stage"],
                "grid": signal["grid"],
                "entry_index": entry_index,
                "entry_time": entry_time,
                "atr": signal["atr"],
                "volratio": signal["volratio"],
                "rsi15": signal["rsi15"],
                "rsi60": signal["rsi60"],
                "bbpos": signal["bbpos"],
                "bbwidth": signal["bbwidth"],
                "global_market_regime": trend.get("regime", "neutral"),
                "btc_daily_move_3": trend.get("btc_daily_move_3"),
                "eth_daily_move_3": trend.get("eth_daily_move_3"),
                "global_daily_move_3": trend.get("global_daily_move_3"),
                "btc_daily_above_ema20": btc_above,
                "eth_daily_above_ema20": eth_above,
                "trend_exception": trend_exception,
            })
    return sorted(candidates, key=lambda item: (item["entry_time"], item["symbol"], item["side"])), skipped


def _side_counts(candidates: list[dict]) -> dict:
    return dict(Counter(item["side"] for item in candidates))


def _comparison_rows(baseline_rows: list[dict], variant_rows: list[dict]) -> list[dict]:
    baseline_by_code = {row["code"]: row for row in baseline_rows}
    rows = []
    for row in variant_rows:
        old = baseline_by_code[row["code"]]
        rows.append({
            "code": row["code"],
            "name": row["name"],
            "trades": row["trades"],
            "trades_delta": row["trades"] - old["trades"],
            "pnl": row["pnl"],
            "pnl_delta": row["pnl"] - old["pnl"],
            "stops": row["stops"],
            "stops_delta": row["stops"] - old["stops"],
            "profit_factor": row["profit_factor"],
            "profit_factor_delta": row["profit_factor"] - old["profit_factor"],
            "max_drawdown": row["max_drawdown"],
            "max_drawdown_delta": row["max_drawdown"] - old["max_drawdown"],
        })
    return rows


def render_html(data: dict) -> str:
    body = []
    for row in data["comparison"]:
        body.append(
            "<tr>"
            f"<td><strong>{row['code']}</strong></td>"
            f"<td>{row['trades']} ({row['trades_delta']:+d})</td>"
            f"<td>{row['pnl']:.2f} ({row['pnl_delta']:+.2f})</td>"
            f"<td>{row['stops']} ({row['stops_delta']:+d})</td>"
            f"<td>{row['profit_factor']:.3f} ({row['profit_factor_delta']:+.3f})</td>"
            f"<td>{row['max_drawdown']:.2f} ({row['max_drawdown_delta']:+.2f})</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>GRID DCA 2.8 rebound long exception</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #10202b; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #d9e1e6; padding: 10px; text-align: left; }}
    th {{ color: #607080; font-size: 12px; text-transform: uppercase; }}
    .note {{ color: #607080; max-width: 980px; line-height: 1.45; }}
  </style>
</head>
<body>
  <h1>GRID DCA 2.8: downtrend rebound long exception</h1>
  <p class="note">Period: {data['period']['start']} - {data['period']['end']}. Baseline: TP+5, EMA20 guard, no same-side cooldown. Variant adds only one exception: selected long rebound signals are allowed during daily downtrend.</p>
  <p class="note">Signals: baseline {data['baseline']['signal_candidates']} / variant {data['variant_result']['signal_candidates']}. Side counts: baseline {data['baseline'].get('side_counts')} / variant {data['variant_result'].get('side_counts')}. Allowed long exceptions: {data['variant_result']['candidate_skipped']['tradingview_daily_regime_long_allowed_exception']}.</p>
  <table>
    <thead><tr><th>Tariff</th><th>Trades</th><th>PnL</th><th>Stops</th><th>PF</th><th>Max DD</th></tr></thead>
    <tbody>{''.join(body)}</tbody>
  </table>
</body>
</html>"""


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    start, end, rows = await base.fetch_all(args.days, end)
    candidates, candidate_skipped = all_signal_candidates_rebound_exception(start, rows)

    original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
    base.SIDE_WEBHOOK_COOLDOWN_MS = 0
    try:
        results = [base.run_portfolio(tariff, candidates, rows) for tariff in TARIFFS]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown

    result_json = base.result_to_json(start, end, candidates, results)
    baseline = json.loads(BASELINE_JSON.read_text(encoding="utf-8-sig"))
    baseline_section = baseline["no_side_cooldown"]
    variant_tariffs = [compact_tariff(row) for row in result_json["tariffs"]]
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": result_json["period"],
        "variant": {
            "code": "grid_dca_28_tp5_no_side_cooldown_pullback_long_exception",
            "take_profit_multiplier": TP_MULTIPLIER,
            "side_webhook_cooldown_ms": 0,
            "exception": "allow existing pullback-long in daily downtrend only when green candle>=0.20%, BTC/ETH 3-bar moves > -0.6%, volume ratio>=0.7, RSI60>=45",
        },
        "baseline": {
            "variant": baseline["variant"],
            "signal_candidates": baseline_section["signal_candidates"],
            "tariffs": baseline_section["tariffs"],
            "side_counts": baseline_section.get("side_counts"),
        },
        "variant_result": {
            "signal_candidates": result_json["signal_candidates"],
            "side_counts": _side_counts(candidates),
            "candidate_skipped": candidate_skipped,
            "tariffs": variant_tariffs,
        },
        "comparison": _comparison_rows(baseline_section["tariffs"], variant_tariffs),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    OUT_HTML.write_text(render_html(data), encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON.resolve()),
        "html": str(OUT_HTML.resolve()),
        "period": data["period"],
        "signal_candidates": data["variant_result"]["signal_candidates"],
        "side_counts": data["variant_result"]["side_counts"],
        "candidate_skipped": candidate_skipped,
        "comparison": data["comparison"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
