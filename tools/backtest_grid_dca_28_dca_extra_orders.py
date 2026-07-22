"""GRID DCA 2.8 extra safety-order backtest.

Research-only script. It checks adding 1-2 safety orders to the current
GRID DCA 2.8 grid, both alone and together with the remembered TP+SL variant:
- TP range unchanged, trend +15%, pullback +20%;
- SL x1.3 for all stages.
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


OUT_JSON = Path(".private_reports/grid-dca-28-dca-extra-orders.json")
TREND_TP_MULT = 1.15
PULLBACK_TP_MULT = 1.20
SL_MULT = 1.30


@dataclass(frozen=True)
class Variant:
    code: str
    extra_dca: int
    use_tp_sl_candidate: bool = False


VARIANTS = [
    Variant("dca_plus_1", 1, False),
    Variant("dca_plus_2", 2, False),
    Variant("tp15_20_sl130_dca_plus_1", 1, True),
    Variant("tp15_20_sl130_dca_plus_2", 2, True),
]


def apply_variant(candidates: list[dict], variant: Variant) -> list[dict]:
    adjusted = []
    for candidate in candidates:
        item = copy.deepcopy(candidate)
        grid = item["grid"]
        dca_max = int(grid.get("dca_max") or grid.get("dca_active") or 0)
        grid["dca_max"] = dca_max + variant.extra_dca
        if variant.use_tp_sl_candidate:
            stage = item.get("stage")
            tp_multiplier = TREND_TP_MULT if stage == "trend" else PULLBACK_TP_MULT if stage == "pullback" else 1.0
            grid["tp"] = min(1.0, float(grid["tp"]) * tp_multiplier)
            grid["sl"] = float(grid["sl"]) * SL_MULT
            item["tp_multiplier"] = tp_multiplier
            item["sl_multiplier"] = SL_MULT
        item["extra_dca_orders"] = variant.extra_dca
        adjusted.append(item)
    return adjusted


def run_variant(variant: Variant, candidates: list[dict], rows: dict[str, list]) -> dict:
    adjusted_candidates = apply_variant(candidates, variant)
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
            "extra_dca_orders": variant.extra_dca,
            "tp_sl_candidate": variant.use_tp_sl_candidate,
            "range_tp_multiplier": 1.0,
            "trend_tp_multiplier": TREND_TP_MULT if variant.use_tp_sl_candidate else 1.0,
            "pullback_tp_multiplier": PULLBACK_TP_MULT if variant.use_tp_sl_candidate else 1.0,
            "sl_multiplier": SL_MULT if variant.use_tp_sl_candidate else 1.0,
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
    variants = [run_variant(variant, candidates, rows) for variant in VARIANTS]
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
                    "extra_dca_orders": item["variant"]["extra_dca_orders"],
                    "tp_sl_candidate": item["variant"]["tp_sl_candidate"],
                    "comparison": [
                        {
                            "tariff": row["code"],
                            "pnl_delta": row["pnl_delta"],
                            "profit_factor_delta": row["profit_factor_delta"],
                            "stops_delta": row["stops_delta"],
                            "max_drawdown_delta": row["max_drawdown_delta"],
                            "trades_delta": row["trades_delta"],
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
