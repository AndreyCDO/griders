"""GRID DCA 2.7 TP+5 EMA20 guard backtest without same-side 5m cooldown.

Research-only script. It does not change production strategy code.
Baseline: .private_reports/grid-dca-27-wide-limits-tp-plus5-ema20-guard-summary.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_27_wide_limits_tp5_ema20_guard import (
    BASELINE_JSON,
    TARIFFS,
    all_signal_candidates_with_server_guard,
    compact_tariff,
)


OUT_JSON = Path(".private_reports/grid-dca-27-wide-limits-tp-plus5-ema20-no-side-cooldown-summary.json")
OUT_HTML = Path(".private_reports/grid-dca-27-wide-limits-tp-plus5-ema20-no-side-cooldown-comparison.html")
EMA20_BASELINE_JSON = Path(".private_reports/grid-dca-27-wide-limits-tp-plus5-ema20-guard-summary.json")


def render_comparison(data: dict) -> str:
    baseline_by_code = {row["code"]: row for row in data["baseline"]["tariffs"]}
    rows = []
    for row in data["no_side_cooldown"]["tariffs"]:
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
          <td>{old['skipped'].get('side_cooldown', 0)} → {row['skipped'].get('side_cooldown', 0)}</td>
        </tr>
        """)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>GRID DCA 2.7 TP+5 EMA20 без 5m side cooldown</title>
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
  <h1>GRID DCA 2.7 TP+5 EMA20: без ограничения 5 минут по стороне</h1>
  <p class="note">Период: {data['period']['start']} — {data['period']['end']}. Исследовательский прогон: рабочая стратегия не изменялась. Отличие от baseline только одно: отключён 5-минутный cooldown между webhook/позициями одного направления.</p>
  <p class="note">Кандидатов сигналов: {data['no_side_cooldown']['signal_candidates']}; baseline: {data['baseline']['signal_candidates']}.</p>
  <table>
    <thead><tr><th>Тариф</th><th>Сделки</th><th>Стопы</th><th>PnL</th><th>Δ PnL</th><th>PF</th><th>Max DD</th><th>Side cooldown skipped</th></tr></thead>
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

    original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
    base.SIDE_WEBHOOK_COOLDOWN_MS = 0
    try:
        results = [base.run_portfolio(tariff, candidates, rows) for tariff in TARIFFS]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown

    result_json = base.result_to_json(start, end, candidates, results)
    baseline = json.loads(EMA20_BASELINE_JSON.read_text(encoding="utf-8-sig"))
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": result_json["period"],
        "variant": {
            "code": "wide_limits_tp_plus5_ema20_no_side_cooldown",
            "take_profit_multiplier": 1.05,
            "side_webhook_cooldown_ms": 0,
            "baseline_side_webhook_cooldown_ms": original_side_cooldown,
        },
        "baseline": {
            "variant": baseline.get("variant"),
            "signal_candidates": baseline["ema20_guard"]["signal_candidates"],
            "tariffs": baseline["ema20_guard"]["tariffs"],
        },
        "no_side_cooldown": {
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
        "signal_candidates": data["no_side_cooldown"]["signal_candidates"],
        "metrics": [{row["code"]: {
            "trades": row["trades"],
            "pnl": row["pnl"],
            "stops": row["stops"],
            "profit_factor": row["profit_factor"],
            "max_drawdown": row["max_drawdown"],
            "skipped": row["skipped"],
        }} for row in data["no_side_cooldown"]["tariffs"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
