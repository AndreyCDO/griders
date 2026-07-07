"""Research backtest for current GRID DCA 2.8, one pair at a time."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_27_ema20_no_side_cooldown_base_tp import (
    all_signal_candidates_base_tp_with_server_guard,
)


OUT_JSON = Path(".private_reports/grid-dca-28-each-pair-summary.json")
OUT_HTML = Path(".private_reports/grid-dca-28-each-pair-comparison.html")


def _pair_tariff(pair: str) -> base.Tariff:
    return base.Tariff(
        code=pair,
        name=pair,
        initial_deposit=500.0,
        pairs=[pair],
        max_total=1,
        max_long=1,
        max_short=1,
        first_order_mode="deposit_pct",
        risk_pct=5.0,
        max_first_order=60.0,
    )


def _metric_for_pair(result: dict) -> dict:
    metrics = base.metric(result)
    trades = result["trades"]
    pnl = float(metrics["pnl"])
    gross_profit = sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) > 0)
    gross_loss = -sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) < 0)
    long_trades = [trade for trade in trades if trade["side"] == "long"]
    short_trades = [trade for trade in trades if trade["side"] == "short"]
    stop_rate = metrics["stops"] / metrics["trades"] * 100 if metrics["trades"] else 0.0
    avg_pnl = pnl / metrics["trades"] if metrics["trades"] else 0.0
    dd = float(metrics["max_drawdown"])
    return {
        "symbol": result["tariff"].code,
        "trades": metrics["trades"],
        "pnl": pnl,
        "return_pct": float(metrics["return_pct"]),
        "profit_factor": float(metrics["profit_factor"]) if not math.isinf(metrics["profit_factor"]) else None,
        "win_rate": float(metrics["win_rate"]),
        "stops": int(metrics["stops"]),
        "stop_rate": stop_rate,
        "max_drawdown": dd,
        "max_drawdown_pct": float(metrics["max_drawdown_pct"]),
        "avg_pnl": avg_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "long_trades": len(long_trades),
        "long_pnl": sum(float(trade["pnl"]) for trade in long_trades),
        "long_stops": sum(1 for trade in long_trades if trade["exit_reason"] == "sl"),
        "short_trades": len(short_trades),
        "short_pnl": sum(float(trade["pnl"]) for trade in short_trades),
        "short_stops": sum(1 for trade in short_trades if trade["exit_reason"] == "sl"),
        "skipped": result["skipped"],
    }


def _score(row: dict) -> float:
    pf = row["profit_factor"] or 10.0
    return (
        row["pnl"]
        + min(pf, 3.0) * 60
        - row["stops"] * 12
        - row["max_drawdown"] * 0.6
    )


def _category(row: dict) -> str:
    if row["pnl"] > 100 and (row["profit_factor"] or 0) >= 1.7 and row["stop_rate"] <= 1.5:
        return "оставить в приоритете"
    if row["pnl"] > 0 and (row["profit_factor"] or 0) >= 1.35 and row["stop_rate"] <= 2.5:
        return "оставить"
    if row["pnl"] > 0:
        return "тестировать осторожно"
    return "кандидат на отключение"


def render_html(data: dict) -> str:
    rows = []
    for row in data["pairs"]:
        pf = "∞" if row["profit_factor"] is None else f"{row['profit_factor']:.3f}"
        rows.append(
            "<tr>"
            f"<td><strong>{row['rank']}</strong></td>"
            f"<td><strong>{row['symbol']}</strong></td>"
            f"<td>{row['category']}</td>"
            f"<td>{row['trades']}</td>"
            f"<td>{row['pnl']:.2f}</td>"
            f"<td>{row['avg_pnl']:.4f}</td>"
            f"<td>{pf}</td>"
            f"<td>{row['stops']}</td>"
            f"<td>{row['stop_rate']:.2f}%</td>"
            f"<td>{row['win_rate']:.2f}%</td>"
            f"<td>{row['max_drawdown']:.2f}</td>"
            f"<td>{row['long_pnl']:.2f} / {row['short_pnl']:.2f}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>GRID DCA 2.8 each pair backtest</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #10202b; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #d9e1e6; padding: 9px; text-align: left; }}
    th {{ color: #607080; font-size: 12px; text-transform: uppercase; }}
    .note {{ color: #607080; max-width: 1080px; line-height: 1.45; }}
    code {{ background: #eef3f5; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>GRID DCA 2.8: прогон каждой пары отдельно</h1>
  <p class="note">Период: {data['period']['start']} - {data['period']['end']}. Условия: стартовый депозит 500 USDT, первый ордер по логике Start 5% от депозита с максимумом 60 USDT, текущие фильтры GRID DCA 2.8, base TP, без 5m side cooldown, после стопа пауза 3ч. TON не используется, вместо неё ONDO.</p>
  <table>
    <thead>
      <tr>
        <th>#</th><th>Пара</th><th>Вывод</th><th>Сделки</th><th>PnL</th><th>Avg PnL</th><th>PF</th><th>Стопы</th><th>Стопы %</th><th>Winrate</th><th>Max DD</th><th>Long / Short PnL</th>
      </tr>
    </thead>
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
        pair_results = [base.run_portfolio(_pair_tariff(pair), candidates, rows) for pair in base.ALL_PAIRS]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown

    pair_rows = [_metric_for_pair(result) for result in pair_results]
    for row in pair_rows:
        row["score"] = _score(row)
        row["category"] = _category(row)
    pair_rows.sort(key=lambda item: item["score"], reverse=True)
    for index, row in enumerate(pair_rows, start=1):
        row["rank"] = index

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days},
        "variant": {
            "code": "grid_dca_28_each_pair",
            "strategy": "GRID DCA 2.8 current, base TP, EMA20/global trend guard, no side cooldown",
            "initial_deposit": 500.0,
            "first_order_mode": "deposit_pct",
            "risk_pct": 5.0,
            "max_first_order": 60.0,
            "pairs": base.ALL_PAIRS,
        },
        "signal_candidates": len(candidates),
        "candidate_skipped": candidate_skipped,
        "pairs": pair_rows,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    OUT_HTML.write_text(render_html(data), encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON.resolve()),
        "html": str(OUT_HTML.resolve()),
        "period": data["period"],
        "pairs": [
            {
                "rank": row["rank"],
                "symbol": row["symbol"],
                "category": row["category"],
                "trades": row["trades"],
                "pnl": row["pnl"],
                "profit_factor": row["profit_factor"],
                "stops": row["stops"],
                "stop_rate": row["stop_rate"],
                "max_drawdown": row["max_drawdown"],
                "long_pnl": row["long_pnl"],
                "short_pnl": row["short_pnl"],
            }
            for row in pair_rows
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
