"""Research backtest for current GRID DCA 2.8 excluding selected pairs.

This script does not change production strategy settings or public reports.
It reuses current GRID DCA 2.8 yearly all-tariff settings and removes the
configured pairs from both signal generation and tariff-available pair lists.
"""

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
from tools.build_grid_dca_28_year_all_tariffs_report import all_tariffs


EXCLUDED_PAIRS = {
    "HYPEUSDT",
    "NEARUSDT",
    "ZECUSDT",
    "ONDOUSDT",
    "XRPUSDT",
    "RENDERUSDT",
}
BASELINE_JSON = Path("webapp/static/reports/grid-dca-28-year-all-tariffs.json")
OUT_JSON = Path(".private_reports/grid-dca-28-exclude-hype-near-zec-ondo-xrp-render.json")


def _compact(result: dict) -> dict:
    metrics = base.metric(result)
    return {
        "code": result["tariff"].code,
        "name": result["tariff"].name,
        "pairs": result["tariff"].pairs,
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
        rows.append(
            {
                "code": tariff["code"],
                "name": tariff["name"],
                "pairs": tariff["settings"]["pairs"],
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
        )
    return rows


def _comparison(baseline: list[dict], variant: list[dict]) -> list[dict]:
    baseline_by_code = {row["code"]: row for row in baseline}
    rows = []
    for row in variant:
        old = baseline_by_code[row["code"]]
        rows.append(
            {
                "code": row["code"],
                "name": row["name"],
                "pairs": len(row["pairs"]),
                "pairs_delta": len(row["pairs"]) - len(old["pairs"]),
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
                "win_rate": row["win_rate"],
                "win_rate_delta": row["win_rate"] - old["win_rate"],
                "max_drawdown": row["max_drawdown"],
                "max_drawdown_delta": row["max_drawdown"] - old["max_drawdown"],
                "max_drawdown_pct": row["max_drawdown_pct"],
                "max_drawdown_pct_delta": row["max_drawdown_pct"] - old["max_drawdown_pct"],
                "margin_skipped": row["skipped"].get("margin", 0),
                "margin_skipped_delta": row["skipped"].get("margin", 0) - old["skipped"].get("margin", 0),
            }
        )
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    original_pairs = base.ALL_PAIRS
    base.ALL_PAIRS = [pair for pair in base.ALL_PAIRS if pair not in EXCLUDED_PAIRS]
    try:
        start, end, market_rows = await base.fetch_all(args.days, end)
        candidates, candidate_skipped = all_signal_candidates_base_tp_with_server_guard(start, market_rows)
        tariffs = all_tariffs()

        original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
        base.SIDE_WEBHOOK_COOLDOWN_MS = 0
        try:
            results = [base.run_portfolio(tariff, candidates, market_rows) for tariff in tariffs]
        finally:
            base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown
    finally:
        base.ALL_PAIRS = original_pairs

    baseline = _baseline_rows()
    variant = [_compact(result) for result in results]
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days},
        "variant": {
            "code": "grid_dca_28_exclude_hype_near_zec_ondo_xrp_render",
            "strategy": "GRID DCA 2.8 current settings with selected pairs excluded",
            "excluded_pairs": sorted(EXCLUDED_PAIRS),
            "included_pairs": [pair for pair in original_pairs if pair not in EXCLUDED_PAIRS],
            "side_webhook_cooldown_ms": 0,
            "production_changed": False,
        },
        "signal_candidates": len(candidates),
        "candidate_skipped": candidate_skipped,
        "baseline": baseline,
        "variant_results": variant,
        "comparison": _comparison(baseline, variant),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(OUT_JSON.resolve()),
                "period": data["period"],
                "excluded_pairs": data["variant"]["excluded_pairs"],
                "included_pairs": data["variant"]["included_pairs"],
                "signal_candidates": data["signal_candidates"],
                "comparison": data["comparison"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
