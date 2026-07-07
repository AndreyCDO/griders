"""Research backtest for current GRID DCA 2.8 using selected trading pairs only."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_27_ema20_no_side_cooldown_base_tp import (
    all_signal_candidates_base_tp_with_server_guard,
)
from tools.backtest_grid_dca_27_wide_limits_tp5_ema20_guard import TARIFFS, compact_tariff


SELECTED_PAIRS = [
    "ETHUSDT",
    "ZECUSDT",
    "XRPUSDT",
    "FILUSDT",
    "RENDERUSDT",
    "LITUSDT",
    "LINKUSDT",
    "SOLUSDT",
    "ONDOUSDT",
    "SUIUSDT",
    "TAOUSDT",
    "ADAUSDT",
    "ENAUSDT",
    "AVAXUSDT",
    "ARBUSDT",
]

BASELINE_JSON = Path(".private_reports/grid-dca-27-wide-limits-base-tp-ema20-no-side-cooldown-summary.json")
OUT_JSON = Path(".private_reports/grid-dca-28-selected-pairs-summary.json")
OUT_HTML = Path(".private_reports/grid-dca-28-selected-pairs-comparison.html")


def _filtered_tariffs() -> list[base.Tariff]:
    selected = set(SELECTED_PAIRS)
    result: list[base.Tariff] = []
    for tariff in TARIFFS:
        result.append(
            base.Tariff(
                code=tariff.code,
                name=tariff.name,
                initial_deposit=tariff.initial_deposit,
                pairs=[pair for pair in tariff.pairs if pair in selected],
                max_total=tariff.max_total,
                max_long=tariff.max_long,
                max_short=tariff.max_short,
                first_order_mode=tariff.first_order_mode,
                manual_first_order=tariff.manual_first_order,
                risk_pct=tariff.risk_pct,
                max_first_order=tariff.max_first_order,
            )
        )
    return result


def _comparison_rows(baseline_rows: list[dict], selected_rows: list[dict]) -> list[dict]:
    baseline_by_code = {row["code"]: row for row in baseline_rows}
    rows = []
    for row in selected_rows:
        old = baseline_by_code[row["code"]]
        rows.append({
            "code": row["code"],
            "name": row["name"],
            "trades": row["trades"],
            "trades_delta": row["trades"] - old["trades"],
            "pnl": row["pnl"],
            "pnl_delta": row["pnl"] - old["pnl"],
            "return_pct": row["return_pct"],
            "return_pct_delta": row["return_pct"] - old["return_pct"],
            "stops": row["stops"],
            "stops_delta": row["stops"] - old["stops"],
            "profit_factor": row["profit_factor"],
            "profit_factor_delta": row["profit_factor"] - old["profit_factor"],
            "max_drawdown": row["max_drawdown"],
            "max_drawdown_delta": row["max_drawdown"] - old["max_drawdown"],
        })
    return rows


def render_html(data: dict) -> str:
    rows_html = []
    for row in data["comparison"]:
        rows_html.append(
            "<tr>"
            f"<td><strong>{row['name']}</strong></td>"
            f"<td>{row['trades']} ({row['trades_delta']:+d})</td>"
            f"<td>{row['pnl']:.2f} ({row['pnl_delta']:+.2f})</td>"
            f"<td>{row['return_pct']:.2f}% ({row['return_pct_delta']:+.2f}%)</td>"
            f"<td>{row['stops']} ({row['stops_delta']:+d})</td>"
            f"<td>{row['profit_factor']:.3f} ({row['profit_factor_delta']:+.3f})</td>"
            f"<td>{row['max_drawdown']:.2f} ({row['max_drawdown_delta']:+.2f})</td>"
            "</tr>"
        )
    pair_rows = []
    for tariff in data["selected_pairs"]["tariffs"]:
        by_pair = sorted(tariff["by_pair"], key=lambda item: item["pnl"], reverse=True)
        pair_rows.append(
            f"<h2>{tariff['name']}</h2>"
            "<table><thead><tr><th>Пара</th><th>Сделки</th><th>PnL</th><th>Стопы</th><th>Winrate</th></tr></thead><tbody>"
            + "".join(
                "<tr>"
                f"<td>{item['symbol']}</td>"
                f"<td>{item['trades']}</td>"
                f"<td>{item['pnl']:.2f}</td>"
                f"<td>{item['stops']}</td>"
                f"<td>{item['win_rate']:.2f}%</td>"
                "</tr>"
                for item in by_pair
            )
            + "</tbody></table>"
        )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>GRID DCA 2.8 selected pairs backtest</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #10202b; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0 28px; }}
    th, td {{ border-bottom: 1px solid #d9e1e6; padding: 10px; text-align: left; }}
    th {{ color: #607080; font-size: 12px; text-transform: uppercase; }}
    .note {{ color: #607080; max-width: 980px; line-height: 1.45; }}
    code {{ background: #eef3f5; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>GRID DCA 2.8: только выбранные пары</h1>
  <p class="note">Период: {data['period']['start']} - {data['period']['end']}. Исследовательский прогон без изменения рабочей стратегии. Сравнение с текущей baseline-версией: base TP, EMA20/global trend guard, без 5m ограничения по стороне.</p>
  <p class="note">Выбранные пары: <code>{', '.join(data['variant']['selected_pairs'])}</code>. Для каждого тарифа применено пересечение с доступными ему парами.</p>
  <table>
    <thead><tr><th>Тариф</th><th>Сделки</th><th>PnL</th><th>Доходность</th><th>Стопы</th><th>PF</th><th>Max DD</th></tr></thead>
    <tbody>{''.join(rows_html)}</tbody>
  </table>
  {''.join(pair_rows)}
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
        results = [base.run_portfolio(tariff, candidates, rows) for tariff in _filtered_tariffs()]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown

    result_json = base.result_to_json(start, end, candidates, results)
    selected_rows = [compact_tariff(row) for row in result_json["tariffs"]]
    baseline = json.loads(BASELINE_JSON.read_text(encoding="utf-8-sig"))
    baseline_rows = baseline["base_tp_no_side_cooldown"]["tariffs"]
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": result_json["period"],
        "variant": {
            "code": "grid_dca_28_selected_pairs",
            "selected_pairs": SELECTED_PAIRS,
            "take_profit_multiplier": 1.0,
            "side_webhook_cooldown_ms": 0,
            "tariff_pair_policy": "intersection of selected pairs and tariff-available pairs",
        },
        "baseline": {
            "signal_candidates": baseline["base_tp_no_side_cooldown"]["signal_candidates"],
            "tariffs": baseline_rows,
        },
        "selected_pairs": {
            "signal_candidates": result_json["signal_candidates"],
            "candidate_skipped": candidate_skipped,
            "tariffs": selected_rows,
        },
        "comparison": _comparison_rows(baseline_rows, selected_rows),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    OUT_HTML.write_text(render_html(data), encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON.resolve()),
        "html": str(OUT_HTML.resolve()),
        "period": data["period"],
        "selected_pairs": SELECTED_PAIRS,
        "metrics": [
            {
                row["code"]: {
                    "trades": row["trades"],
                    "pnl": row["pnl"],
                    "return_pct": row["return_pct"],
                    "stops": row["stops"],
                    "profit_factor": row["profit_factor"],
                    "max_drawdown": row["max_drawdown"],
                }
            }
            for row in selected_rows
        ],
        "comparison": data["comparison"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
