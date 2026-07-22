"""GRID DCA 2.8 stage-dependent stop-loss grid backtest.

Research-only script. It keeps current GRID DCA 2.8 signals and take-profit
logic, changing only stop-loss multipliers by market stage.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from dataclasses import dataclass
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


OUT_JSON = Path(".private_reports/grid-dca-28-stage-sl-grid.json")


@dataclass(frozen=True)
class StageSlVariant:
    code: str
    range_mult: float
    trend_mult: float
    pullback_mult: float


STAGE_SL_VARIANTS = [
    StageSlVariant("sl_stage_cautious", 1.00, 1.10, 0.90),
    StageSlVariant("sl_stage_trend_wide", 1.00, 1.20, 1.00),
    StageSlVariant("sl_stage_wide", 1.10, 1.20, 1.10),
    StageSlVariant("sl_stage_tight", 0.90, 1.00, 0.90),
    StageSlVariant("sl_stage_wider_pullback", 1.10, 1.30, 1.20),
    StageSlVariant("sl_stage_uniform_130", 1.30, 1.30, 1.30),
    StageSlVariant("sl_stage_trend_pullback_130", 1.10, 1.30, 1.30),
    StageSlVariant("sl_stage_range_soft_trend_pullback_140", 1.10, 1.40, 1.40),
]


def apply_stage_sl(candidates: list[dict], variant: StageSlVariant) -> list[dict]:
    adjusted = []
    for candidate in candidates:
        item = copy.deepcopy(candidate)
        stage = item.get("stage")
        multiplier = (
            variant.trend_mult
            if stage == "trend"
            else variant.pullback_mult
            if stage == "pullback"
            else variant.range_mult
        )
        item["grid"]["sl"] = float(item["grid"]["sl"]) * multiplier
        item["sl_multiplier"] = multiplier
        adjusted.append(item)
    return adjusted


def run_stage_sl_variant(variant: StageSlVariant, candidates: list[dict], rows: dict[str, list]) -> dict:
    adjusted_candidates = apply_stage_sl(candidates, variant)
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
            "code": variant.code,
            "range_sl_multiplier": variant.range_mult,
            "trend_sl_multiplier": variant.trend_mult,
            "pullback_sl_multiplier": variant.pullback_mult,
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
    variants = [run_stage_sl_variant(variant, candidates, rows) for variant in STAGE_SL_VARIANTS]
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
                    "range_sl": item["variant"]["range_sl_multiplier"],
                    "trend_sl": item["variant"]["trend_sl_multiplier"],
                    "pullback_sl": item["variant"]["pullback_sl_multiplier"],
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
