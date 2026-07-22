"""GRID DCA 2.8 TP trend+15/pullback+20 combined with SL variants.

Research-only script. It compares the current GRID DCA 2.8 baseline with
the remembered TP candidate and several stop-loss profiles.
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


OUT_JSON = Path(".private_reports/grid-dca-28-tp15-20-sl-combo.json")
TREND_TP_MULT = 1.15
PULLBACK_TP_MULT = 1.20


@dataclass(frozen=True)
class ComboVariant:
    code: str
    range_sl: float
    trend_sl: float
    pullback_sl: float


COMBO_VARIANTS = [
    ComboVariant("tp15_20_sl_110", 1.10, 1.10, 1.10),
    ComboVariant("tp15_20_sl_120", 1.20, 1.20, 1.20),
    ComboVariant("tp15_20_sl_130", 1.30, 1.30, 1.30),
    ComboVariant("tp15_20_sl_stage_110_130_130", 1.10, 1.30, 1.30),
    ComboVariant("tp15_20_sl_stage_110_140_140", 1.10, 1.40, 1.40),
]


def apply_combo(candidates: list[dict], variant: ComboVariant) -> list[dict]:
    adjusted = []
    for candidate in candidates:
        item = copy.deepcopy(candidate)
        stage = item.get("stage")
        tp_multiplier = TREND_TP_MULT if stage == "trend" else PULLBACK_TP_MULT if stage == "pullback" else 1.0
        sl_multiplier = (
            variant.trend_sl
            if stage == "trend"
            else variant.pullback_sl
            if stage == "pullback"
            else variant.range_sl
        )
        item["grid"]["tp"] = min(1.0, float(item["grid"]["tp"]) * tp_multiplier)
        item["grid"]["sl"] = float(item["grid"]["sl"]) * sl_multiplier
        item["tp_multiplier"] = tp_multiplier
        item["sl_multiplier"] = sl_multiplier
        adjusted.append(item)
    return adjusted


def run_combo_variant(variant: ComboVariant, candidates: list[dict], rows: dict[str, list]) -> dict:
    adjusted_candidates = apply_combo(candidates, variant)
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
            "range_tp_multiplier": 1.0,
            "trend_tp_multiplier": TREND_TP_MULT,
            "pullback_tp_multiplier": PULLBACK_TP_MULT,
            "range_sl_multiplier": variant.range_sl,
            "trend_sl_multiplier": variant.trend_sl,
            "pullback_sl_multiplier": variant.pullback_sl,
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
    variants = [run_combo_variant(variant, candidates, rows) for variant in COMBO_VARIANTS]
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
                    "sl": [
                        item["variant"]["range_sl_multiplier"],
                        item["variant"]["trend_sl_multiplier"],
                        item["variant"]["pullback_sl_multiplier"],
                    ],
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
