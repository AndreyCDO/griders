"""GRID DCA 2.7 EMA20 guard backtest without same-side 5m cooldown and without TP+5.

Research-only script. It does not change production strategy code.
Comparison baseline: .private_reports/grid-dca-27-wide-limits-tp-plus5-ema20-no-side-cooldown-summary.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_26_vs_31 import _signal_at, hourly_rsi_by_15m, indicators
from tools.backtest_grid_dca_27_wide_limits_tp5_ema20_guard import TARIFFS, compact_tariff


OUT_JSON = Path(".private_reports/grid-dca-27-wide-limits-base-tp-ema20-no-side-cooldown-summary.json")
OUT_HTML = Path(".private_reports/grid-dca-27-wide-limits-base-tp-ema20-no-side-cooldown-comparison.html")
BASELINE_JSON = Path(".private_reports/grid-dca-27-wide-limits-tp-plus5-ema20-no-side-cooldown-summary.json")


def all_signal_candidates_base_tp_with_server_guard(start: datetime, all_rows: dict[str, list]) -> tuple[list[dict], dict]:
    btc = indicators(all_rows["BTCUSDT"])
    eth = indicators(all_rows["ETHUSDT"])
    trend_context = base._daily_trend_context(all_rows["BTCUSDT"], all_rows["ETHUSDT"])
    start_ms = int(start.timestamp() * 1000)
    candidates: list[dict] = []
    skipped = {
        "tradingview_daily_regime_long": 0,
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
            if side == "long" and trend.get("regime") == "downtrend":
                skipped["tradingview_daily_regime_long"] += 1
                continue
            if side == "short" and trend.get("regime") == "uptrend":
                skipped["tradingview_daily_regime_short"] += 1
                continue
            if side == "long" and not btc_above and not eth_above:
                skipped["server_ema20_long"] += 1
                continue
            if side == "short" and btc_above and eth_above:
                skipped["server_ema20_short"] += 1
                continue
            signal = base._with_dca_max(signal)
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
            })
    return sorted(candidates, key=lambda item: (item["entry_time"], item["symbol"], item["side"])), skipped


def render_comparison(data: dict) -> str:
    baseline_by_code = {row["code"]: row for row in data["baseline_tp5_no_side_cooldown"]["tariffs"]}
    rows = []
    for row in data["base_tp_no_side_cooldown"]["tariffs"]:
        old = baseline_by_code[row["code"]]
        rows.append(f"""
        <tr>
          <td><strong>{row['name']}</strong></td>
          <td>{old['trades']} → {row['trades']}</td>
          <td>{old['stops']} → {row['stops']}</td>
          <td>{old['pnl']:.2f} → {row['pnl']:.2f}</td>
          <td>{row['pnl'] - old['pnl']:+.2f}</td>
          <td>{old['profit_factor']:.2f} → {row['profit_factor']:.2f}</td>
          <td>{old['max_drawdown']:.2f} → {row['max_drawdown']:.2f}</td>
        </tr>
        """)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>GRID DCA 2.7 EMA20 без 5m side cooldown и без TP+5</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #10202b; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #d9e1e6; padding: 10px; text-align: left; }}
    th {{ color: #607080; font-size: 12px; text-transform: uppercase; }}
    .note {{ color: #607080; max-width: 980px; line-height: 1.45; }}
    code {{ background: #eef3f5; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>GRID DCA 2.7 EMA20: без 5 минут по стороне и без TP+5</h1>
  <p class="note">Период: {data['period']['start']} — {data['period']['end']}. Исследовательский прогон: рабочая стратегия не изменялась. Сравнение с предыдущим бэктестом без 5m side cooldown, но с TP+5.</p>
  <table>
    <thead><tr><th>Тариф</th><th>Сделки</th><th>Стопы</th><th>PnL</th><th>Δ PnL</th><th>PF</th><th>Max DD</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
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
    candidates, candidate_skipped = all_signal_candidates_base_tp_with_server_guard(start, rows)

    original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
    base.SIDE_WEBHOOK_COOLDOWN_MS = 0
    try:
        results = [base.run_portfolio(tariff, candidates, rows) for tariff in TARIFFS]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown

    result_json = base.result_to_json(start, end, candidates, results)
    baseline = json.loads(BASELINE_JSON.read_text(encoding="utf-8-sig"))
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": result_json["period"],
        "variant": {
            "code": "wide_limits_base_tp_ema20_no_side_cooldown",
            "take_profit_multiplier": 1.0,
            "side_webhook_cooldown_ms": 0,
            "baseline_take_profit_multiplier": 1.05,
        },
        "baseline_tp5_no_side_cooldown": {
            "variant": baseline["variant"],
            "signal_candidates": baseline["no_side_cooldown"]["signal_candidates"],
            "tariffs": baseline["no_side_cooldown"]["tariffs"],
        },
        "base_tp_no_side_cooldown": {
            "signal_candidates": result_json["signal_candidates"],
            "candidate_skipped": candidate_skipped,
            "tariffs": [compact_tariff(row) for row in result_json["tariffs"]],
        },
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    OUT_HTML.write_text(render_comparison(data), encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON.resolve()),
        "html": str(OUT_HTML.resolve()),
        "period": data["period"],
        "signal_candidates": data["base_tp_no_side_cooldown"]["signal_candidates"],
        "metrics": [{row["code"]: {
            "trades": row["trades"],
            "pnl": row["pnl"],
            "stops": row["stops"],
            "profit_factor": row["profit_factor"],
            "max_drawdown": row["max_drawdown"],
        }} for row in data["base_tp_no_side_cooldown"]["tariffs"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
