"""Internal GRID DCA 2.6 one-year portfolio backtest for admin-only settings."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from tools.backtest_grid_dca_26_year_tariffs import (
    ALL_PAIRS,
    LEVERAGE,
    Tariff,
    all_signal_candidates,
    fetch_all,
    first_order_for,
    metric,
    run_portfolio,
)


class SkipTrade(Exception):
    pass


def admin_first_order(tariff: Tariff, deposit: float, grid: dict, symbol: str) -> float:
    value = first_order_for(tariff, deposit, grid)
    if symbol == "BTCUSDT" and value < 61.0:
        raise SkipTrade("BTC first order below 61 USDT")
    return value


def run_admin_portfolio(tariff: Tariff, candidates: list[dict], all_rows: dict[str, list]) -> dict:
    from tools import backtest_grid_dca_26_year_tariffs as base

    original = base.first_order_for
    skipped_btc_min = 0

    def patched(row_tariff: Tariff, deposit: float, grid: dict) -> float:
        nonlocal skipped_btc_min
        symbol = current_symbol["value"]
        try:
            return admin_first_order(row_tariff, deposit, grid, symbol)
        except SkipTrade:
            skipped_btc_min += 1
            return -1.0

    def simulate_with_skip(*args, **kwargs):
        first_order = args[2] if len(args) >= 3 else kwargs.get("first_order")
        if first_order is not None and float(first_order) < 0:
            raise SkipTrade("skip")
        return original_simulate(*args, **kwargs)

    current_symbol = {"value": ""}
    original_simulate = base.simulate_trade

    def patched_run_portfolio() -> dict:
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
            "btc_min_first_order": 0,
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
            current_symbol["value"] = symbol
            try:
                first_order = admin_first_order(tariff, deposit, candidate["grid"], symbol)
            except SkipTrade:
                skipped["btc_min_first_order"] += 1
                continue
            trade = base.simulate_trade(all_rows[symbol], candidate, first_order)
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

    return patched_run_portfolio()


def by_pair(trades: list[dict]) -> list[dict]:
    rows = []
    for symbol in ALL_PAIRS:
        subset = [trade for trade in trades if trade["symbol"] == symbol]
        if not subset:
            continue
        pnl = sum(float(trade["pnl"]) for trade in subset)
        rows.append({
            "symbol": symbol,
            "trades": len(subset),
            "pnl": pnl,
            "stops": sum(1 for trade in subset if trade["exit_reason"] == "sl"),
            "win_rate": sum(1 for trade in subset if float(trade["pnl"]) > 0) / len(subset) * 100,
        })
    return sorted(rows, key=lambda item: item["pnl"], reverse=True)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    parser.add_argument("--risk-pct", type=float, default=15.0)
    args = parser.parse_args()
    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    start, end, rows = await fetch_all(args.days, end)
    candidates = all_signal_candidates(start, rows)
    tariff = Tariff(
        code="admin_internal_15pct_100",
        name="Admin internal 15%",
        initial_deposit=100.0,
        pairs=ALL_PAIRS[:],
        max_total=6,
        max_long=3,
        max_short=3,
        first_order_mode="deposit_pct",
        risk_pct=float(args.risk_pct),
        max_first_order=None,
    )
    result = run_admin_portfolio(tariff, candidates, rows)
    metrics = metric(result)
    pairs = by_pair(result["trades"])
    output = {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days},
        "leverage": LEVERAGE,
        "signal_candidates": len(candidates),
        "settings": {
            "initial_deposit": tariff.initial_deposit,
            "risk_pct": tariff.risk_pct,
            "max_total": tariff.max_total,
            "max_long": tariff.max_long,
            "max_short": tariff.max_short,
            "pairs": tariff.pairs,
            "btc_min_first_order": 61.0,
        },
        "metrics": metrics,
        "top_pairs": pairs[:8],
        "worst_pairs": sorted(pairs, key=lambda item: item["pnl"])[:8],
        "skipped": result["skipped"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
