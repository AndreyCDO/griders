"""GRID DCA 2.7 wide-limits backtest with current server EMA20 guard.

Research-only script. It mirrors the current chosen public settings:
- tariff limits: free 4/4/4, start 8/8/8, premium 12/12/12
- take profit: base GRID DCA 2.7 TP multiplied by 1.05
- TradingView daily regime filter
- extra server guard: block long when BTC and ETH are both below daily EMA20,
  block short when BTC and ETH are both above daily EMA20
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_26_vs_31 import _signal_at, hourly_rsi_by_15m, indicators


OUT_JSON = Path(".private_reports/grid-dca-27-wide-limits-tp-plus5-ema20-guard-summary.json")
OUT_HTML = Path(".private_reports/grid-dca-27-wide-limits-tp-plus5-ema20-guard-comparison.html")
BASELINE_JSON = Path(".private_reports/grid-dca-27-wide-limits-tp-plus5-summary.json")
TP_MULTIPLIER = 1.05


TARIFFS = [
    base.Tariff(
        code="free",
        name="Бесплатный",
        initial_deposit=50.0,
        pairs=[pair for pair in base.ALL_PAIRS if pair not in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}],
        max_total=4,
        max_long=4,
        max_short=4,
        first_order_mode="manual",
        manual_first_order=6.0,
        max_first_order=6.0,
    ),
    base.Tariff(
        code="start",
        name="Старт",
        initial_deposit=500.0,
        pairs=[pair for pair in base.ALL_PAIRS if pair != "BTCUSDT"],
        max_total=8,
        max_long=8,
        max_short=8,
        first_order_mode="deposit_pct",
        risk_pct=5.0,
        max_first_order=60.0,
    ),
    base.Tariff(
        code="premium",
        name="Премиум",
        initial_deposit=5000.0,
        pairs=base.ALL_PAIRS[:],
        max_total=12,
        max_long=12,
        max_short=12,
        first_order_mode="deposit_pct",
        risk_pct=5.0,
        max_first_order=600.0,
    ),
]


def all_signal_candidates_with_server_guard(start: datetime, all_rows: dict[str, list]) -> tuple[list[dict], dict]:
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
            })
    return sorted(candidates, key=lambda item: (item["entry_time"], item["symbol"], item["side"])), skipped


def compact_tariff(row: dict) -> dict:
    m = row["metrics"]
    return {
        "code": row["code"],
        "name": row["name"],
        "trades": m["trades"],
        "pnl": m["pnl"],
        "return_pct": m["return_pct"],
        "profit_factor": m["profit_factor"],
        "stops": m["stops"],
        "win_rate": m["win_rate"],
        "max_drawdown": m["max_drawdown"],
        "max_drawdown_pct": m["max_drawdown_pct"],
        "skipped": m["skipped"],
        "by_pair": row.get("by_pair", []),
        "worst_trades": row.get("worst_trades", []),
    }


def render_comparison(data: dict) -> str:
    baseline_by_code = {row["code"]: row for row in data["baseline"]["tariffs"]}
    rows = []
    for row in data["ema20_guard"]["tariffs"]:
        base_row = baseline_by_code[row["code"]]
        rows.append(f"""
        <tr>
          <td><strong>{row['name']}</strong></td>
          <td>{base_row['trades']} → {row['trades']}</td>
          <td>{base_row['stops']} → {row['stops']}</td>
          <td>{base_row['pnl']:.2f} → {row['pnl']:.2f}</td>
          <td>{row['pnl'] - base_row['pnl']:+.2f}</td>
          <td>{base_row['profit_factor']:.2f} → {row['profit_factor']:.2f}</td>
          <td>{base_row['max_drawdown']:.2f} → {row['max_drawdown']:.2f}</td>
        </tr>
        """)
    skipped = data["ema20_guard"]["candidate_skipped"]
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>GRID DCA 2.7 TP+5 EMA20 guard backtest</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #10202b; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #d9e1e6; padding: 10px; text-align: left; }}
    th {{ color: #607080; font-size: 12px; text-transform: uppercase; }}
    .note {{ color: #607080; max-width: 960px; line-height: 1.45; }}
    code {{ background: #eef3f5; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>GRID DCA 2.7 TP+5 с серверным EMA20-фильтром</h1>
  <p class="note">Период: {data['period']['start']} — {data['period']['end']}. Сравнение с сохранённым baseline <code>wide_limits_tp_plus5</code>. Новый фильтр: не открывать long, если BTC и ETH оба ниже дневной EMA20; не открывать short, если BTC и ETH оба выше дневной EMA20.</p>
  <p class="note">Кандидатов после фильтра: {data['ema20_guard']['signal_candidates']} из baseline {data['baseline']['signal_candidates']}. Дополнительно отфильтровано: long EMA20 {skipped['server_ema20_long']}, short EMA20 {skipped['server_ema20_short']}.</p>
  <table>
    <thead><tr><th>Тариф</th><th>Сделки</th><th>Стопы</th><th>PnL</th><th>Разница PnL</th><th>PF</th><th>Max DD</th></tr></thead>
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
    candidates, candidate_skipped = all_signal_candidates_with_server_guard(start, rows)
    results = [base.run_portfolio(tariff, candidates, rows) for tariff in TARIFFS]
    result_json = base.result_to_json(start, end, candidates, results)
    baseline = json.loads(BASELINE_JSON.read_text(encoding="utf-8-sig"))
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": result_json["period"],
        "variant": {
            "code": "wide_limits_tp_plus5_ema20_guard",
            "take_profit_multiplier": TP_MULTIPLIER,
            "server_filter": "block long when BTC and ETH are both below daily EMA20; block short when both are above daily EMA20",
        },
        "baseline": {
            "variant": baseline.get("variant") or baseline.get("report_variant"),
            "signal_candidates": baseline.get("signal_candidates"),
            "tariffs": [compact_tariff(row) for row in baseline["tariffs"]],
        },
        "ema20_guard": {
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
        "baseline_candidates": data["baseline"]["signal_candidates"],
        "ema20_candidates": data["ema20_guard"]["signal_candidates"],
        "candidate_skipped": candidate_skipped,
        "metrics": [
            {
                row["code"]: {
                    "trades": row["trades"],
                    "pnl": row["pnl"],
                    "stops": row["stops"],
                    "profit_factor": row["profit_factor"],
                    "max_drawdown": row["max_drawdown"],
                }
            }
            for row in data["ema20_guard"]["tariffs"]
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
