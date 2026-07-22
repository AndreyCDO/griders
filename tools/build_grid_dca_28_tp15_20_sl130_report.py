"""Build a public-ready GRID DCA 2.9 yearly tariff report.

Variant:
- range TP unchanged;
- trend TP +15%;
- pullback TP +20%;
- SL x1.3 for all stages.

The script writes a standalone report file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_27_ema20_no_side_cooldown_base_tp import (
    all_signal_candidates_base_tp_with_server_guard,
)
from tools.backtest_grid_dca_28_tp15_20_sl_combo import ComboVariant, apply_combo
from tools import build_grid_dca_28_year_all_tariffs_report as report


JSON_PATH = ROOT / "webapp/static/reports/grid-dca-29-year-all-tariffs.json"
HTML_PATH = ROOT / "webapp/static/reports/grid-dca-29-year-all-tariffs.html"
STRATEGY_LABEL = "GRID DCA 2.9"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    parser.add_argument("--html-out", default=str(HTML_PATH))
    parser.add_argument("--json-out", default=str(JSON_PATH))
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    start, end, rows = await base.fetch_all(args.days, end)
    candidates, candidate_skipped = all_signal_candidates_base_tp_with_server_guard(start, rows)
    variant = ComboVariant("tp15_20_sl_130", 1.30, 1.30, 1.30)
    adjusted_candidates = apply_combo(candidates, variant)

    original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
    base.SIDE_WEBHOOK_COOLDOWN_MS = 0
    try:
        results = [base.run_portfolio(tariff, adjusted_candidates, rows) for tariff in report.all_tariffs()]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown

    data = base.result_to_json(start, end, adjusted_candidates, results)
    data["strategy_label"] = STRATEGY_LABEL
    data["report_variant"] = {
        "code": "grid_dca_29_year_all_tariffs",
        "description": "GRID DCA 2.9: TP range unchanged, trend +15%, pullback +20%; SL x1.3 for all stages.",
        "range_tp_multiplier": 1.0,
        "trend_tp_multiplier": 1.15,
        "pullback_tp_multiplier": 1.20,
        "sl_multiplier": 1.30,
        "side_webhook_cooldown_ms": 0,
        "candidate_skipped": candidate_skipped,
        "production_changed": True,
    }
    data["assumptions"]["cooldowns"] = "3h GRID DCA pause after SL; no same-side 5m cooldown, as in current GRID DCA 2.9 backtests"
    data["assumptions"]["grid_dca_29"] = "TP: range 1.00x, trend 1.15x, pullback 1.20x; SL: 1.30x for every stage"
    for tariff, result in zip(data["tariffs"], results):
        tariff["daily_chart"] = report.day_chart(start, end, result["trades"])

    json_path = Path(args.json_out)
    html_path = Path(args.html_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    original_label = report.STRATEGY_LABEL
    report.STRATEGY_LABEL = STRATEGY_LABEL
    try:
        html = report.build_html(data)
    finally:
        report.STRATEGY_LABEL = original_label
    html_path.write_text(html, encoding="utf-8")

    print(json.dumps(
        {
            "html": str(html_path.resolve()),
            "json": str(json_path.resolve()),
            "period": data["period"],
            "signal_candidates": data["signal_candidates"],
            "metrics": [
                {
                    tariff["code"]: {
                        "trades": tariff["metrics"]["trades"],
                        "pnl": tariff["metrics"]["pnl"],
                        "return_pct": tariff["metrics"]["return_pct"],
                        "profit_factor": tariff["metrics"]["profit_factor"],
                        "stops": tariff["metrics"]["stops"],
                        "max_drawdown": tariff["metrics"]["max_drawdown"],
                    }
                }
                for tariff in data["tariffs"]
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
