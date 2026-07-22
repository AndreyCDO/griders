"""GRID DCA 2.8 stop-loss multiplier grid backtest.

Research-only script. It keeps current GRID DCA 2.8 signals and take-profit
logic, changing only the stop-loss multiplier for every candidate.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
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
from tools.backtest_grid_dca_28_pine_research_filters import baseline_rows, compact, comparison
from tools.build_grid_dca_28_year_all_tariffs_report import all_tariffs


OUT_JSON = Path(".private_reports/grid-dca-28-sl-grid.json")

SL_MULTIPLIERS = [0.80, 0.90, 1.00, 1.10, 1.20, 1.30]


def apply_sl_multiplier(candidates: list[dict], multiplier: float) -> list[dict]:
    adjusted = []
    for candidate in candidates:
        item = copy.deepcopy(candidate)
        item["grid"]["sl"] = float(item["grid"]["sl"]) * multiplier
        item["sl_multiplier"] = multiplier
        adjusted.append(item)
    return adjusted


def run_sl_variant(multiplier: float, candidates: list[dict], rows: dict[str, list]) -> dict:
    adjusted_candidates = apply_sl_multiplier(candidates, multiplier)
    original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
    base.SIDE_WEBHOOK_COOLDOWN_MS = 0
    try:
        results = [base.run_portfolio(tariff, adjusted_candidates, rows) for tariff in all_tariffs()]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown
    variant_rows = [compact(result) for result in results]
    baseline = baseline_rows()
    return {
        "variant": {
            "code": f"sl_x_{multiplier:.2f}".replace(".", "_"),
            "sl_multiplier": multiplier,
            "production_changed": False,
        },
        "results": variant_rows,
        "comparison": comparison(baseline, variant_rows),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    start, end, rows = await base.fetch_all(args.days, end)
    candidates, candidate_skipped = all_signal_candidates_base_tp_with_server_guard(start, rows)
    variants = [run_sl_variant(multiplier, candidates, rows) for multiplier in SL_MULTIPLIERS]
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days},
        "signal_candidates": len(candidates),
        "candidate_skipped": candidate_skipped,
        "baseline": baseline_rows(),
        "variants": variants,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(
        {
            "json": str(OUT_JSON.resolve()),
            "period": data["period"],
            "signal_candidates": data["signal_candidates"],
            "summary": [
                {
                    "code": item["variant"]["code"],
                    "sl_multiplier": item["variant"]["sl_multiplier"],
                    "comparison": [
                        {
                            "tariff": row["code"],
                            "pnl_delta": row["pnl_delta"],
                            "profit_factor_delta": row["profit_factor_delta"],
                            "stops_delta": row["stops_delta"],
                            "max_drawdown_delta": row["max_drawdown_delta"],
                        }
                        for row in item["comparison"]
                    ],
                }
                for item in variants
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
