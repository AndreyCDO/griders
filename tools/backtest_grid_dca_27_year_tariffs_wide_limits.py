"""Run GRID DCA 2.7 yearly tariff backtest with wider side limits."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from tools import backtest_grid_dca_26_year_tariffs as base


REPORT_PATH = base.REPORT_DIR / "grid-dca-27-year-tariffs-wide-limits.html"
JSON_PATH = base.REPORT_DIR / "grid-dca-27-year-tariffs-wide-limits.json"


TARIFFS = [
    base.Tariff(
        code="free",
        name="Бесплатный",
        initial_deposit=50.0,
        pairs=[pair for pair in base.ALL_PAIRS if pair not in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}],
        max_total=4,
        max_long=4,
        max_short=4,
        first_order_mode="manual",
        manual_first_order=6.0,
        max_first_order=6.0,
    ),
    base.Tariff(
        code="start",
        name="Старт",
        initial_deposit=500.0,
        pairs=[pair for pair in base.ALL_PAIRS if pair != "BTCUSDT"],
        max_total=8,
        max_long=8,
        max_short=8,
        first_order_mode="deposit_pct",
        risk_pct=5.0,
        max_first_order=60.0,
    ),
    base.Tariff(
        code="premium",
        name="Премиум",
        initial_deposit=5000.0,
        pairs=base.ALL_PAIRS[:],
        max_total=12,
        max_long=12,
        max_short=12,
        first_order_mode="deposit_pct",
        risk_pct=5.0,
        max_first_order=600.0,
    ),
]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="")
    args = parser.parse_args()
    end = datetime.fromisoformat(args.end).astimezone(timezone.utc) if args.end else datetime.now(timezone.utc)
    start, end, rows = await base.fetch_all(args.days, end)
    candidates = base.all_signal_candidates(start, rows)
    results = [base.run_portfolio(tariff, candidates, rows) for tariff in TARIFFS]
    data = base.result_to_json(start, end, candidates, results)
    data["report_variant"] = {
        "code": "wide_limits",
        "description": "Free 4/4/4, Start 8/8/8, Premium 12/12/12 open trade limits",
    }
    base.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    REPORT_PATH.write_text(base.render_report(data), encoding="utf-8")
    print(json.dumps({
        "report": str(REPORT_PATH.resolve()),
        "json": str(JSON_PATH.resolve()),
        "period": data["period"],
        "signal_candidates": data["signal_candidates"],
        "metrics": [{item["code"]: item["metrics"]} for item in data["tariffs"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
