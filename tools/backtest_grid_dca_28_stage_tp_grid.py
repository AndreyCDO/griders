"""GRID DCA 2.8 stage-dependent take-profit grid backtest.

Research-only script. It keeps the current signal logic and changes only
take-profit multipliers by market stage:
- range stays at 1.00x;
- trend and pullback are tested across several multiplier combinations.
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


OUT_JSON = Path(".private_reports/grid-dca-28-stage-tp-grid.json")


TP_VARIANTS = [
    ("tp_trend_2_pullback_3", 1.02, 1.03),
    ("tp_trend_3_pullback_5", 1.03, 1.05),
    ("tp_trend_5_pullback_8", 1.05, 1.08),
    ("tp_trend_8_pullback_5", 1.08, 1.05),
    ("tp_trend_8_pullback_12", 1.08, 1.12),
    ("tp_trend_10_pullback_15", 1.10, 1.15),
    ("tp_trend_10_pullback_20", 1.10, 1.20),
    ("tp_trend_15_pullback_20", 1.15, 1.20),
    ("tp_trend_20_pullback_10", 1.20, 1.10),
]


def apply_stage_tp(candidates: list[dict], trend_mult: float, pullback_mult: float) -> list[dict]:
    adjusted = []
    for candidate in candidates:
        item = copy.deepcopy(candidate)
        stage = item.get("stage")
        if stage == "trend":
            multiplier = trend_mult
        elif stage == "pullback":
            multiplier = pullback_mult
        else:
            multiplier = 1.0
        item["grid"]["tp"] = min(1.0, float(item["grid"]["tp"]) * multiplier)
        item["tp_multiplier"] = multiplier
        adjusted.append(item)
    return adjusted


def run_tp_variant(code: str, trend_mult: float, pullback_mult: float, candidates: list[dict], rows: dict[str, list]) -> dict:
    adjusted_candidates = apply_stage_tp(candidates, trend_mult, pullback_mult)
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
            "code": code,
            "range_tp_multiplier": 1.0,
            "trend_tp_multiplier": trend_mult,
            "pullback_tp_multiplier": pullback_mult,
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
    variants = [
        run_tp_variant(code, trend_mult, pullback_mult, candidates, rows)
        for code, trend_mult, pullback_mult in TP_VARIANTS
    ]
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
                    "trend_tp_multiplier": item["variant"]["trend_tp_multiplier"],
                    "pullback_tp_multiplier": item["variant"]["pullback_tp_multiplier"],
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
