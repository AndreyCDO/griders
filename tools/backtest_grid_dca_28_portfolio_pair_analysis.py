"""Portfolio backtest for current GRID DCA 2.8 with detailed per-pair analysis."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_27_ema20_no_side_cooldown_base_tp import (
    all_signal_candidates_base_tp_with_server_guard,
)
from tools.backtest_grid_dca_27_wide_limits_tp5_ema20_guard import TARIFFS


OUT_JSON = Path(".private_reports/grid-dca-28-portfolio-pair-analysis.json")
OUT_HTML = Path(".private_reports/grid-dca-28-portfolio-pair-analysis.html")


def _fmt_pf(value: float | None) -> str:
    if value is None:
        return "∞"
    return f"{value:.3f}"


def _max_drawdown(trades: list[dict]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for trade in sorted(trades, key=lambda item: int(item["exit_time"])):
        equity += float(trade["pnl"])
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _pair_rows(trades: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade["symbol"])].append(trade)
    rows = []
    for symbol in base.ALL_PAIRS:
        subset = grouped.get(symbol, [])
        if not subset:
            rows.append({
                "symbol": symbol,
                "trades": 0,
                "pnl": 0.0,
                "profit_factor": None,
                "win_rate": 0.0,
                "stops": 0,
                "stop_rate": 0.0,
                "max_drawdown": 0.0,
                "avg_pnl": 0.0,
                "long_trades": 0,
                "long_pnl": 0.0,
                "long_stops": 0,
                "short_trades": 0,
                "short_pnl": 0.0,
                "short_stops": 0,
            })
            continue
        pnl = sum(float(trade["pnl"]) for trade in subset)
        gross_profit = sum(float(trade["pnl"]) for trade in subset if float(trade["pnl"]) > 0)
        gross_loss = -sum(float(trade["pnl"]) for trade in subset if float(trade["pnl"]) < 0)
        wins = sum(1 for trade in subset if float(trade["pnl"]) > 0)
        stops = sum(1 for trade in subset if trade["exit_reason"] == "sl")
        long_trades = [trade for trade in subset if trade["side"] == "long"]
        short_trades = [trade for trade in subset if trade["side"] == "short"]
        rows.append({
            "symbol": symbol,
            "trades": len(subset),
            "pnl": pnl,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
            "win_rate": wins / len(subset) * 100,
            "stops": stops,
            "stop_rate": stops / len(subset) * 100,
            "max_drawdown": _max_drawdown(subset),
            "avg_pnl": pnl / len(subset),
            "long_trades": len(long_trades),
            "long_pnl": sum(float(trade["pnl"]) for trade in long_trades),
            "long_stops": sum(1 for trade in long_trades if trade["exit_reason"] == "sl"),
            "short_trades": len(short_trades),
            "short_pnl": sum(float(trade["pnl"]) for trade in short_trades),
            "short_stops": sum(1 for trade in short_trades if trade["exit_reason"] == "sl"),
        })
    return sorted(rows, key=lambda item: (item["pnl"], item["profit_factor"] or 0), reverse=True)


def _category(row: dict) -> str:
    if row["trades"] < 20:
        return "мало данных"
    if row["pnl"] > 0 and (row["profit_factor"] or 0) >= 1.8 and row["stop_rate"] <= 2.0:
        return "сильная"
    if row["pnl"] > 0 and (row["profit_factor"] or 0) >= 1.3:
        return "оставить"
    if row["pnl"] > 0:
        return "нейтральная"
    return "кандидат на отключение"


def _render_table(rows: list[dict]) -> str:
    html = []
    for index, row in enumerate(rows, start=1):
        html.append(
            "<tr>"
            f"<td>{index}</td>"
            f"<td><strong>{row['symbol']}</strong></td>"
            f"<td>{row['category']}</td>"
            f"<td>{row['trades']}</td>"
            f"<td>{row['pnl']:.2f}</td>"
            f"<td>{_fmt_pf(row['profit_factor'])}</td>"
            f"<td>{row['stops']}</td>"
            f"<td>{row['stop_rate']:.2f}%</td>"
            f"<td>{row['win_rate']:.2f}%</td>"
            f"<td>{row['max_drawdown']:.2f}</td>"
            f"<td>{row['long_pnl']:.2f} / {row['short_pnl']:.2f}</td>"
            "</tr>"
        )
    return "".join(html)


def render_html(data: dict) -> str:
    sections = []
    for tariff in data["tariffs"]:
        sections.append(f"""
        <h2>{tariff['name']}</h2>
        <p class="note">Сделки: {tariff['metrics']['trades']}, PnL: {tariff['metrics']['pnl']:.2f}, PF: {_fmt_pf(tariff['metrics']['profit_factor'])}, стопы: {tariff['metrics']['stops']}, Max DD: {tariff['metrics']['max_drawdown']:.2f}</p>
        <table>
          <thead><tr><th>#</th><th>Пара</th><th>Вывод</th><th>Сделки</th><th>PnL</th><th>PF</th><th>Стопы</th><th>Стопы %</th><th>Winrate</th><th>Pair DD</th><th>Long / Short PnL</th></tr></thead>
          <tbody>{_render_table(tariff['pairs'])}</tbody>
        </table>
        """)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>GRID DCA 2.8 portfolio pair analysis</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #10202b; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0 32px; }}
    th, td {{ border-bottom: 1px solid #d9e1e6; padding: 9px; text-align: left; }}
    th {{ color: #607080; font-size: 12px; text-transform: uppercase; }}
    .note {{ color: #607080; max-width: 1080px; line-height: 1.45; }}
  </style>
</head>
<body>
  <h1>GRID DCA 2.8: пакетный прогон с анализом по монетам</h1>
  <p class="note">Период: {data['period']['start']} - {data['period']['end']}. Это портфельная модель: монеты конкурируют за лимиты тарифа, действует пауза после стопа, запрет второй сделки по той же паре и текущие фильтры стратегии.</p>
  {''.join(sections)}
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

    tariffs = []
    for result in results:
        metrics = base.metric(result)
        metrics["profit_factor"] = None if math.isinf(metrics["profit_factor"]) else float(metrics["profit_factor"])
        pairs = _pair_rows(result["trades"])
        for row in pairs:
            row["category"] = _category(row)
        tariffs.append({
            "code": result["tariff"].code,
            "name": result["tariff"].name,
            "settings": {
                "initial_deposit": result["tariff"].initial_deposit,
                "pairs": result["tariff"].pairs,
                "max_total": result["tariff"].max_total,
                "max_long": result["tariff"].max_long,
                "max_short": result["tariff"].max_short,
                "first_order_mode": result["tariff"].first_order_mode,
                "risk_pct": result["tariff"].risk_pct,
                "max_first_order": result["tariff"].max_first_order,
            },
            "metrics": metrics,
            "pairs": pairs,
        })

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days},
        "variant": {
            "code": "grid_dca_28_portfolio_pair_analysis",
            "strategy": "GRID DCA 2.8 current, base TP, EMA20/global trend guard, no side cooldown",
            "pairs": base.ALL_PAIRS,
        },
        "signal_candidates": len(candidates),
        "candidate_skipped": candidate_skipped,
        "tariffs": tariffs,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    OUT_HTML.write_text(render_html(data), encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON.resolve()),
        "html": str(OUT_HTML.resolve()),
        "period": data["period"],
        "tariffs": [
            {
                "code": tariff["code"],
                "metrics": {
                    "trades": tariff["metrics"]["trades"],
                    "pnl": tariff["metrics"]["pnl"],
                    "profit_factor": tariff["metrics"]["profit_factor"],
                    "stops": tariff["metrics"]["stops"],
                    "max_drawdown": tariff["metrics"]["max_drawdown"],
                },
                "pairs": [
                    {
                        "symbol": row["symbol"],
                        "category": row["category"],
                        "trades": row["trades"],
                        "pnl": row["pnl"],
                        "profit_factor": row["profit_factor"],
                        "stops": row["stops"],
                        "stop_rate": row["stop_rate"],
                        "long_pnl": row["long_pnl"],
                        "short_pnl": row["short_pnl"],
                    }
                    for row in tariff["pairs"]
                ],
            }
            for tariff in tariffs
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
