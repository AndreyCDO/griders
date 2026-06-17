"""Compare GRID DCA stop-loss pause policies.

Research script only. It reuses the GRID DCA 2.5 signal and trade simulator,
then applies portfolio-level pause policies to the generated trade stream.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx

from tools.backtest_grid_dca_v25 import PAIRS, fetch_klines, indicators, metrics, rounded, run_backtest


INTERVAL_MS = 15 * 60 * 1000


def _utc_floor_15m(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc).replace(second=0, microsecond=0)
    minute = value.minute - value.minute % 15
    return value.replace(minute=minute)


def _simulate_policy(trades: list[dict], *, global_pause_hours: float, pair_pause_hours: float) -> dict:
    global_pause_until = 0
    pair_pause_until: dict[str, int] = defaultdict(int)
    accepted: list[dict] = []
    skipped_global = 0
    skipped_pair = 0

    for trade in sorted(trades, key=lambda item: (item["entry_time"], item["symbol"])):
        entry_time = int(trade["entry_time"])
        symbol = str(trade["symbol"])
        if entry_time < global_pause_until:
            skipped_global += 1
            continue
        if entry_time < pair_pause_until[symbol]:
            skipped_pair += 1
            continue

        accepted.append(trade)
        if trade["reason"] == "sl":
            exit_time = int(trade["exit_time"])
            if global_pause_hours > 0:
                global_pause_until = max(global_pause_until, exit_time + int(global_pause_hours * 60 * 60 * 1000))
            if pair_pause_hours > 0:
                pair_pause_until[symbol] = max(pair_pause_until[symbol], exit_time + int(pair_pause_hours * 60 * 60 * 1000))

    data = rounded(metrics(accepted))
    data["skipped_global_pause"] = skipped_global
    data["skipped_pair_pause"] = skipped_pair
    data["skipped_total"] = skipped_global + skipped_pair
    return data


async def build_trade_stream(days: int, end: datetime) -> tuple[datetime, datetime, list[dict]]:
    end = _utc_floor_15m(end)
    start = end - timedelta(days=days)
    warmup_start = start - timedelta(days=3)
    start_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    async with httpx.AsyncClient(timeout=25) as client:
        all_rows = {}
        for symbol in ["BTCUSDT", "ETHUSDT", *PAIRS]:
            if symbol in all_rows:
                continue
            for attempt in range(6):
                try:
                    all_rows[symbol] = await fetch_klines(client, symbol, start_ms, end_ms)
                    break
                except RuntimeError as exc:
                    if "Too many visits" not in str(exc) or attempt == 5:
                        raise
                    wait_seconds = 8 + attempt * 6
                    print("rate_limit_wait", symbol, wait_seconds)
                    await asyncio.sleep(wait_seconds)
            print("fetched", symbol, len(all_rows[symbol]))
            await asyncio.sleep(0.75)

    btc = indicators(all_rows["BTCUSDT"])
    eth = indicators(all_rows["ETHUSDT"])
    trades = []
    start_trade_ms = int(start.timestamp() * 1000)
    for symbol in PAIRS:
        rows = all_rows[symbol]
        symbol_trades = run_backtest(symbol, rows, indicators(rows), btc, eth)
        trades.extend(trade for trade in symbol_trades if trade["entry_time"] >= start_trade_ms)
    return start, end, sorted(trades, key=lambda item: (item["entry_time"], item["symbol"]))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    start, end, trades = await build_trade_stream(args.days, datetime.now(timezone.utc))
    policies = {
        "baseline_no_pause": {"global_pause_hours": 0, "pair_pause_hours": 0},
        "current_global_3h_after_sl": {"global_pause_hours": 3, "pair_pause_hours": 0},
        "pair_only_3h_after_sl": {"global_pause_hours": 0, "pair_pause_hours": 3},
        "global_3h_plus_pair_3h": {"global_pause_hours": 3, "pair_pause_hours": 3},
    }

    print("\n=== GRID DCA 2.5 STOP-LOSS PAUSE POLICY TEST ===")
    print("period_utc", start.isoformat(), end.isoformat())
    print("pairs", len(PAIRS))
    print("trade_candidates", len(trades))
    print("assumptions", "15m_candles; next_bar_open_entry; pessimistic_intrabar; taker_fee_0.05%; no_funding_no_slippage; daily_loss_pause_ignored")
    for name, params in policies.items():
        print(name, _simulate_policy(trades, **params))

    by_pair = defaultdict(list)
    for trade in trades:
        by_pair[trade["symbol"]].append(trade)
    print("\nSTOPS_BY_PAIR")
    for symbol, symbol_trades in sorted(by_pair.items(), key=lambda item: sum(1 for trade in item[1] if trade["reason"] == "sl"), reverse=True):
        symbol_metrics = metrics(symbol_trades)
        if symbol_metrics["sl"]:
            print(symbol, rounded(symbol_metrics))


if __name__ == "__main__":
    asyncio.run(main())
