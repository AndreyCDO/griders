"""Research backtest for GRID DCA 2.8 with DCA volume multiplier 1.5.

This script does not change production strategy settings or public reports.
It reuses the current GRID DCA 2.8 signal set, tariff limits, margin check,
and date range, changing only grid["mult_vol"] to 1.5 for every signal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_27_ema20_no_side_cooldown_base_tp import (
    all_signal_candidates_base_tp_with_server_guard,
)
from tools.build_grid_dca_28_year_all_tariffs_report import all_tariffs


OUT_JSON = Path(".private_reports/grid-dca-28-martingale-15-summary.json")
BASELINE_JSON = Path("webapp/static/reports/grid-dca-28-year-all-tariffs.json")
DCA_MULTIPLIER = 1.5


def _with_dca_multiplier(candidates: list[dict], multiplier: float) -> list[dict]:
    adjusted = []
    for candidate in candidates:
        item = deepcopy(candidate)
        item["grid"] = {**item["grid"], "mult_vol": multiplier}
        adjusted.append(item)
    return adjusted


def _compact(result: dict) -> dict:
    metrics = base.metric(result)
    return {
        "code": result["tariff"].code,
        "name": result["tariff"].name,
        "trades": metrics["trades"],
        "pnl": metrics["pnl"],
        "return_pct": metrics["return_pct"],
        "profit_factor": metrics["profit_factor"],
        "stops": metrics["stops"],
        "win_rate": metrics["win_rate"],
        "max_drawdown": metrics["max_drawdown"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "avg_first_order": metrics["avg_first_order"],
        "avg_entry_value": metrics["avg_entry_value"],
        "avg_planned_entry_value": metrics["avg_planned_entry_value"],
        "skipped": metrics["skipped"],
    }


def _baseline_rows() -> list[dict]:
    data = json.loads(BASELINE_JSON.read_text(encoding="utf-8-sig"))
    rows = []
    for tariff in data["tariffs"]:
        metrics = tariff["metrics"]
        rows.append({
            "code": tariff["code"],
            "name": tariff["name"],
            "trades": metrics["trades"],
            "pnl": metrics["pnl"],
            "return_pct": metrics["return_pct"],
            "profit_factor": metrics["profit_factor"],
            "stops": metrics["stops"],
            "win_rate": metrics["win_rate"],
            "max_drawdown": metrics["max_drawdown"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "avg_first_order": metrics["avg_first_order"],
            "avg_entry_value": metrics["avg_entry_value"],
            "avg_planned_entry_value": metrics["avg_planned_entry_value"],
            "skipped": metrics["skipped"],
        })
    return rows


def _comparison(baseline: list[dict], variant: list[dict]) -> list[dict]:
    baseline_by_code = {row["code"]: row for row in baseline}
    rows = []
    for row in variant:
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
            "profit_factor": row["profit_factor"],
            "profit_factor_delta": row["profit_factor"] - old["profit_factor"],
            "stops": row["stops"],
            "stops_delta": row["stops"] - old["stops"],
            "max_drawdown": row["max_drawdown"],
            "max_drawdown_delta": row["max_drawdown"] - old["max_drawdown"],
            "avg_first_order": row["avg_first_order"],
            "avg_first_order_delta": row["avg_first_order"] - old["avg_first_order"],
            "avg_planned_entry_value": row["avg_planned_entry_value"],
            "avg_planned_entry_value_delta": row["avg_planned_entry_value"] - old["avg_planned_entry_value"],
            "margin_skipped": row["skipped"].get("margin", 0),
            "margin_skipped_delta": row["skipped"].get("margin", 0) - old["skipped"].get("margin", 0),
        })
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    start, end, market_rows = await base.fetch_all(args.days, end)
    candidates, candidate_skipped = all_signal_candidates_base_tp_with_server_guard(start, market_rows)
    adjusted_candidates = _with_dca_multiplier(candidates, DCA_MULTIPLIER)

    original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
    base.SIDE_WEBHOOK_COOLDOWN_MS = 0
    try:
        results = [
            base.run_portfolio(tariff, adjusted_candidates, market_rows)
            for tariff in all_tariffs()
        ]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown

    baseline = _baseline_rows()
    variant = [_compact(result) for result in results]
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days},
        "variant": {
            "code": "grid_dca_28_martingale_15",
            "strategy": "GRID DCA 2.8 current signals, DCA volume multiplier changed from current values to 1.5",
            "dca_multiplier_volume": DCA_MULTIPLIER,
            "side_webhook_cooldown_ms": 0,
            "production_changed": False,
        },
        "signal_candidates": len(adjusted_candidates),
        "candidate_skipped": candidate_skipped,
        "baseline": baseline,
        "martingale_15": variant,
        "comparison": _comparison(baseline, variant),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON.resolve()),
        "period": data["period"],
        "signal_candidates": data["signal_candidates"],
        "comparison": data["comparison"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
