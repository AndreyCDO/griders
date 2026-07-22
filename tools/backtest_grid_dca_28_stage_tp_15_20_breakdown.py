"""Monthly and pair breakdown for GRID DCA 2.8 vs TP trend+15/pullback+20.

Research-only script. It compares the current GRID DCA 2.8 backtest against
the stage-dependent take-profit variant:
- range: unchanged;
- trend: +15%;
- pullback: +20%.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_27_ema20_no_side_cooldown_base_tp import (
    all_signal_candidates_base_tp_with_server_guard,
)
from tools.backtest_grid_dca_28_pine_research_filters import compact
from tools.build_grid_dca_28_year_all_tariffs_report import all_tariffs


OUT_JSON = Path(".private_reports/grid-dca-28-stage-tp-15-20-breakdown.json")
TREND_MULT = 1.15
PULLBACK_MULT = 1.20


def apply_stage_tp(candidates: list[dict]) -> list[dict]:
    adjusted = []
    for candidate in candidates:
        item = copy.deepcopy(candidate)
        stage = item.get("stage")
        multiplier = TREND_MULT if stage == "trend" else PULLBACK_MULT if stage == "pullback" else 1.0
        item["grid"]["tp"] = min(1.0, float(item["grid"]["tp"]) * multiplier)
        item["tp_multiplier"] = multiplier
        adjusted.append(item)
    return adjusted


def profit_factor(trades: list[dict]) -> float | None:
    gross_profit = sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) > 0)
    gross_loss = -sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) < 0)
    if gross_loss <= 0:
        return None
    return gross_profit / gross_loss


def grouped(trades: list[dict], key_fn) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        buckets[key_fn(trade)].append(trade)
    rows = []
    for key, subset in buckets.items():
        pnl = sum(float(trade["pnl"]) for trade in subset)
        rows.append(
            {
                "key": key,
                "trades": len(subset),
                "pnl": pnl,
                "stops": sum(1 for trade in subset if trade["exit_reason"] == "sl"),
                "tp": sum(1 for trade in subset if trade["exit_reason"] == "tp"),
                "win_rate": sum(1 for trade in subset if float(trade["pnl"]) > 0) / len(subset) * 100,
                "profit_factor": profit_factor(subset),
            }
        )
    return rows


def month_key(trade: dict) -> str:
    return datetime.fromtimestamp(int(trade["exit_time"]) / 1000, tz=timezone.utc).strftime("%Y-%m")


def compare_rows(current: list[dict], variant: list[dict]) -> list[dict]:
    by_key_current = {row["key"]: row for row in current}
    by_key_variant = {row["key"]: row for row in variant}
    keys = sorted(set(by_key_current) | set(by_key_variant))
    rows = []
    for key in keys:
        old = by_key_current.get(key, {"trades": 0, "pnl": 0.0, "stops": 0, "tp": 0, "win_rate": 0.0, "profit_factor": None})
        new = by_key_variant.get(key, {"trades": 0, "pnl": 0.0, "stops": 0, "tp": 0, "win_rate": 0.0, "profit_factor": None})
        rows.append(
            {
                "key": key,
                "current": old,
                "variant": new,
                "trades_delta": new["trades"] - old["trades"],
                "pnl_delta": new["pnl"] - old["pnl"],
                "stops_delta": new["stops"] - old["stops"],
                "tp_delta": new["tp"] - old["tp"],
                "win_rate_delta": new["win_rate"] - old["win_rate"],
                "profit_factor_delta": (
                    new["profit_factor"] - old["profit_factor"]
                    if new["profit_factor"] is not None and old["profit_factor"] is not None
                    else None
                ),
            }
        )
    return rows


def run_portfolios(candidates: list[dict], rows: dict[str, list]) -> list[dict]:
    original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
    base.SIDE_WEBHOOK_COOLDOWN_MS = 0
    try:
        return [base.run_portfolio(tariff, candidates, rows) for tariff in all_tariffs()]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown


def compare_result(current: dict, variant: dict) -> dict:
    current_months = sorted(grouped(current["trades"], month_key), key=lambda row: row["key"])
    variant_months = sorted(grouped(variant["trades"], month_key), key=lambda row: row["key"])
    current_pairs = sorted(grouped(current["trades"], lambda trade: trade["symbol"]), key=lambda row: row["key"])
    variant_pairs = sorted(grouped(variant["trades"], lambda trade: trade["symbol"]), key=lambda row: row["key"])
    return {
        "tariff": {
            "code": current["tariff"].code,
            "name": current["tariff"].name,
        },
        "current_metrics": compact(current),
        "variant_metrics": compact(variant),
        "metrics_delta": {
            "trades": len(variant["trades"]) - len(current["trades"]),
            "pnl": sum(float(trade["pnl"]) for trade in variant["trades"]) - sum(float(trade["pnl"]) for trade in current["trades"]),
            "stops": sum(1 for trade in variant["trades"] if trade["exit_reason"] == "sl")
            - sum(1 for trade in current["trades"] if trade["exit_reason"] == "sl"),
        },
        "monthly": compare_rows(current_months, variant_months),
        "pairs": compare_rows(current_pairs, variant_pairs),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    start, end, rows = await base.fetch_all(args.days, end)
    current_candidates, candidate_skipped = all_signal_candidates_base_tp_with_server_guard(start, rows)
    variant_candidates = apply_stage_tp(current_candidates)
    current_results = run_portfolios(current_candidates, rows)
    variant_results = run_portfolios(variant_candidates, rows)
    comparisons = [compare_result(current, variant) for current, variant in zip(current_results, variant_results)]
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days},
        "signal_candidates": len(current_candidates),
        "candidate_skipped": candidate_skipped,
        "variant": {
            "code": "tp_trend_15_pullback_20",
            "range_tp_multiplier": 1.0,
            "trend_tp_multiplier": TREND_MULT,
            "pullback_tp_multiplier": PULLBACK_MULT,
            "production_changed": False,
        },
        "tariffs": comparisons,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(
        {
            "json": str(OUT_JSON.resolve()),
            "period": data["period"],
            "summary": [
                {
                    "tariff": item["tariff"]["code"],
                    "current_pnl": item["current_metrics"]["pnl"],
                    "variant_pnl": item["variant_metrics"]["pnl"],
                    "pnl_delta": item["metrics_delta"]["pnl"],
                    "stops_delta": item["metrics_delta"]["stops"],
                    "best_months": sorted(
                        [{"month": row["key"], "pnl_delta": row["pnl_delta"], "stops_delta": row["stops_delta"]} for row in item["monthly"]],
                        key=lambda row: row["pnl_delta"],
                        reverse=True,
                    )[:3],
                    "worst_months": sorted(
                        [{"month": row["key"], "pnl_delta": row["pnl_delta"], "stops_delta": row["stops_delta"]} for row in item["monthly"]],
                        key=lambda row: row["pnl_delta"],
                    )[:3],
                    "best_pairs": sorted(
                        [{"pair": row["key"], "pnl_delta": row["pnl_delta"], "stops_delta": row["stops_delta"]} for row in item["pairs"]],
                        key=lambda row: row["pnl_delta"],
                        reverse=True,
                    )[:5],
                    "worst_pairs": sorted(
                        [{"pair": row["key"], "pnl_delta": row["pnl_delta"], "stops_delta": row["stops_delta"]} for row in item["pairs"]],
                        key=lambda row: row["pnl_delta"],
                    )[:5],
                }
                for item in comparisons
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
