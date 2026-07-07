"""Research backtest for GRID DCA 2.8 with Cryptorg bot cycles=2.

This does not change production strategy code.

Model: an accepted signal opens cycle 1. If cycle 1 closes by take profit,
cycle 2 starts on the next candle open with the same side/grid/first order.
If cycle 1 stops out or reaches end-of-data, there is no second cycle. The
portfolio position remains active until the final cycle closes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_27_tp5_ema20_no_side_cooldown import (
    OUT_JSON as CURRENT_BASELINE_JSON,
)
from tools.backtest_grid_dca_27_wide_limits_tp5_ema20_guard import (
    TARIFFS,
    all_signal_candidates_with_server_guard,
    compact_tariff,
)


OUT_JSON = Path(".private_reports/grid-dca-28-cycles2-summary.json")
OUT_HTML = Path(".private_reports/grid-dca-28-cycles2-comparison.html")


def _simulate_one_cycle(rows: list, candidate: dict, first_order: float, entry_index: int) -> dict:
    local = {**candidate, "entry_index": entry_index, "entry_time": int(rows[entry_index][0])}
    return base.simulate_trade(rows, local, first_order)


def simulate_trade_cycles2(rows: list, candidate: dict, first_order: float) -> dict:
    cycles: list[dict] = []
    current_entry_index = int(candidate["entry_index"])
    total_pnl = 0.0
    total_gross = 0.0
    total_fees = 0.0
    total_entry_value = 0.0
    final_trade = None

    for cycle_number in (1, 2):
        if current_entry_index >= len(rows) - 1:
            break
        trade = _simulate_one_cycle(rows, candidate, first_order, current_entry_index)
        trade["cycle"] = cycle_number
        cycles.append(trade)
        final_trade = trade
        total_pnl += float(trade["pnl"])
        total_gross += float(trade["gross"])
        total_fees += float(trade["fees"])
        total_entry_value += float(trade["entry_value"])
        if trade["exit_reason"] != "tp":
            break
        current_entry_index = int(trade["exit_index"]) + 1

    if final_trade is None:
        final_trade = base.simulate_trade(rows, candidate, first_order)
        cycles.append(final_trade)
        total_pnl = float(final_trade["pnl"])
        total_gross = float(final_trade["gross"])
        total_fees = float(final_trade["fees"])
        total_entry_value = float(final_trade["entry_value"])

    return {
        **candidate,
        "first_order": first_order,
        "pnl": total_pnl,
        "gross": total_gross,
        "fees": total_fees,
        "fills": sum(int(trade["fills"]) for trade in cycles),
        "dca_fills": sum(int(trade["dca_fills"]) for trade in cycles),
        "planned_factor": base.planned_grid_factor(candidate["grid"]),
        "entry_value": total_entry_value,
        "planned_entry_value": first_order * base.planned_grid_factor(candidate["grid"]) * len(cycles),
        "exit_time": int(final_trade["exit_time"]),
        "exit_index": int(final_trade["exit_index"]),
        "exit_reason": final_trade["exit_reason"],
        "duration_bars": int(final_trade["exit_index"]) - int(candidate["entry_index"]),
        "cycles_completed": len(cycles),
        "cycle_results": [
            {
                "cycle": int(trade.get("cycle") or index + 1),
                "pnl": float(trade["pnl"]),
                "exit_reason": trade["exit_reason"],
                "exit_time": int(trade["exit_time"]),
                "dca_fills": int(trade["dca_fills"]),
            }
            for index, trade in enumerate(cycles)
        ],
    }


def run_portfolio_cycles2(tariff: base.Tariff, candidates: list[dict], all_rows: dict[str, list]) -> dict:
    deposit = tariff.initial_deposit
    trades: list[dict] = []
    open_trades: list[dict] = []
    pause_until = 0
    last_pair_launch: dict[str, int] = {}
    side_lock_until = {"long": 0, "short": 0}
    skipped = {
        "pair": 0,
        "limit": 0,
        "pause": 0,
        "same_pair_active": 0,
        "pair_cooldown": 0,
        "side_cooldown": 0,
    }

    for candidate in candidates:
        entry_time = int(candidate["entry_time"])
        open_trades = [trade for trade in open_trades if int(trade["exit_time"]) > entry_time]
        symbol = candidate["symbol"]
        side = candidate["side"]
        if symbol not in tariff.pairs:
            skipped["pair"] += 1
            continue
        if entry_time < pause_until:
            skipped["pause"] += 1
            continue
        if entry_time < side_lock_until[side]:
            skipped["side_cooldown"] += 1
            continue
        if entry_time < last_pair_launch.get(symbol, 0) + base.PAIR_LAUNCH_COOLDOWN_MS:
            skipped["pair_cooldown"] += 1
            continue
        if any(trade["symbol"] == symbol for trade in open_trades):
            skipped["same_pair_active"] += 1
            continue
        counts = base.active_counts(open_trades)
        if not base.can_open(tariff, counts, side):
            skipped["limit"] += 1
            continue
        first_order = base.first_order_for(tariff, deposit, candidate["grid"])
        trade = simulate_trade_cycles2(all_rows[symbol], candidate, first_order)
        deposit += float(trade["pnl"])
        trade["deposit_after"] = deposit
        trades.append(trade)
        open_trades.append(trade)
        last_pair_launch[symbol] = entry_time
        side_lock_until[side] = entry_time + base.SIDE_WEBHOOK_COOLDOWN_MS
        if trade["exit_reason"] == "sl":
            pause_until = max(pause_until, int(trade["exit_time"]) + base.STOP_LOSS_PAUSE_MS)

    return {
        "tariff": tariff,
        "trades": trades,
        "skipped": skipped,
        "final_deposit": deposit,
        "pnl": deposit - tariff.initial_deposit,
    }


def _comparison_rows(baseline_rows: list[dict], cycles_rows: list[dict]) -> list[dict]:
    baseline_by_code = {row["code"]: row for row in baseline_rows}
    result = []
    for row in cycles_rows:
        old = baseline_by_code[row["code"]]
        result.append({
            "code": row["code"],
            "name": row["name"],
            "trades": row["trades"],
            "trades_delta": row["trades"] - old["trades"],
            "pnl": row["pnl"],
            "pnl_delta": row["pnl"] - old["pnl"],
            "return_pct": row["return_pct"],
            "return_pct_delta": row["return_pct"] - old["return_pct"],
            "stops": row["stops"],
            "stops_delta": row["stops"] - old["stops"],
            "profit_factor": row["profit_factor"],
            "profit_factor_delta": row["profit_factor"] - old["profit_factor"],
            "max_drawdown": row["max_drawdown"],
            "max_drawdown_delta": row["max_drawdown"] - old["max_drawdown"],
        })
    return result


def _cycle_stats(trades: list[dict]) -> dict:
    second_cycle = [trade for trade in trades if int(trade.get("cycles_completed") or 1) >= 2]
    return {
        "trades_with_second_cycle": len(second_cycle),
        "second_cycle_share_pct": len(second_cycle) / len(trades) * 100 if trades else 0,
        "second_cycle_final_stops": sum(1 for trade in second_cycle if trade["exit_reason"] == "sl"),
    }


def render_html(data: dict) -> str:
    rows = []
    for row in data["comparison"]:
        rows.append(
            "<tr>"
            f"<td><strong>{row['code']}</strong></td>"
            f"<td>{row['trades']} ({row['trades_delta']:+d})</td>"
            f"<td>{row['pnl']:.2f} ({row['pnl_delta']:+.2f})</td>"
            f"<td>{row['return_pct']:.2f}% ({row['return_pct_delta']:+.2f}%)</td>"
            f"<td>{row['stops']} ({row['stops_delta']:+d})</td>"
            f"<td>{row['profit_factor']:.3f} ({row['profit_factor_delta']:+.3f})</td>"
            f"<td>{row['max_drawdown']:.2f} ({row['max_drawdown_delta']:+.2f})</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>GRID DCA 2.8 cycles=2 comparison</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #10202b; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #d9e1e6; padding: 10px; text-align: left; }}
    th {{ color: #607080; font-size: 12px; text-transform: uppercase; }}
    .note {{ color: #607080; max-width: 980px; line-height: 1.45; }}
  </style>
</head>
<body>
  <h1>GRID DCA 2.8: cycles=2</h1>
  <p class="note">Period: {data['period']['start']} - {data['period']['end']}. Baseline: current GRID DCA 2.8, TP+5, EMA20 guard, no same-side cooldown. Variant changes only Cryptorg cycles from 1 to 2.</p>
  <table>
    <thead><tr><th>Tariff</th><th>Trades</th><th>PnL</th><th>Return</th><th>Stops</th><th>PF</th><th>Max DD</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>"""


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    start, end, rows = await base.fetch_all(args.days, end)
    candidates, candidate_skipped = all_signal_candidates_with_server_guard(start, rows)

    original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
    base.SIDE_WEBHOOK_COOLDOWN_MS = 0
    try:
        results = [run_portfolio_cycles2(tariff, candidates, rows) for tariff in TARIFFS]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown

    result_json = base.result_to_json(start, end, candidates, results)
    baseline = json.loads(CURRENT_BASELINE_JSON.read_text(encoding="utf-8-sig"))
    baseline_section = baseline["no_side_cooldown"]
    cycles_tariffs = [compact_tariff(row) for row in result_json["tariffs"]]
    for compact, full in zip(cycles_tariffs, results):
        compact["cycle_stats"] = _cycle_stats(full["trades"])

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": result_json["period"],
        "variant": {
            "code": "grid_dca_28_cycles_2",
            "cycles": 2,
            "model": "second cycle starts on next candle open only if first cycle closes by take profit",
        },
        "baseline": {
            "variant": baseline["variant"],
            "signal_candidates": baseline_section["signal_candidates"],
            "tariffs": baseline_section["tariffs"],
        },
        "cycles2": {
            "signal_candidates": result_json["signal_candidates"],
            "candidate_skipped": candidate_skipped,
            "tariffs": cycles_tariffs,
        },
        "comparison": _comparison_rows(baseline_section["tariffs"], cycles_tariffs),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    OUT_HTML.write_text(render_html(data), encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON.resolve()),
        "html": str(OUT_HTML.resolve()),
        "period": data["period"],
        "signal_candidates": data["cycles2"]["signal_candidates"],
        "comparison": data["comparison"],
        "cycle_stats": {row["code"]: row["cycle_stats"] for row in cycles_tariffs},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
