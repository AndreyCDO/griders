"""Screen replacement symbols for GRID DCA 2.6.

Uses current Bybit linear tickers for liquidity pre-filtering, then backtests
candidate symbols with the same simulator as the 2.6/3.1 comparison.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from tools.backtest_grid_dca_26_vs_31 import _apply_global_pause, fetch_klines, indicators, run_backtest
from tools.backtest_grid_dca_v25 import PAIRS, metrics, rounded


REMOVE = {"TIAUSDT", "DOTUSDT", "DOGEUSDT"}
EXISTING_AFTER_REMOVE = set(PAIRS) - REMOVE
EXCLUDED_BASES = {
    "USDC", "USDE", "USD1", "USDD", "DAI", "FDUSD", "TUSD", "USDP", "EUR", "TRY",
    "BTC", "ETH", "SOL", "HYPE", "NEAR", "ZEC", "ONDO", "XRP", "SUI", "TAO",
    "RENDER", "ADA", "INJ", "ENA", "LINK", "AVAX", "ARB",
}
EXCLUDED_SYMBOL_PARTS = {"1000", "10000", "LUNA", "UST", "DEFI"}


def _base(symbol: str) -> str:
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def _is_candidate_symbol(symbol: str, turnover: float, min_turnover: float) -> bool:
    if not symbol.endswith("USDT"):
        return False
    if symbol in EXISTING_AFTER_REMOVE or symbol in REMOVE:
        return False
    base = _base(symbol)
    if base in EXCLUDED_BASES:
        return False
    if any(part in symbol for part in EXCLUDED_SYMBOL_PARTS):
        return False
    if turnover < min_turnover:
        return False
    return True


async def fetch_top_candidates(limit: int, min_turnover: float) -> list[dict]:
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get("https://api.bybit.com/v5/market/tickers", params={"category": "linear"})
        response.raise_for_status()
        data = response.json()
    if data.get("retCode") != 0:
        raise RuntimeError(data.get("retMsg"))
    rows = []
    for item in data["result"]["list"]:
        symbol = str(item.get("symbol") or "")
        try:
            turnover = float(item.get("turnover24h") or 0)
        except (TypeError, ValueError):
            turnover = 0.0
        if _is_candidate_symbol(symbol, turnover, min_turnover):
            rows.append({"symbol": symbol, "turnover24h": turnover})
    rows.sort(key=lambda item: item["turnover24h"], reverse=True)
    return rows[:limit]


def _period_trades(trades: list[dict], start: datetime) -> list[dict]:
    start_ms = int(start.timestamp() * 1000)
    return [trade for trade in trades if trade["entry_time"] >= start_ms]


def _with_pause_metrics(trades: list[dict]) -> dict:
    accepted, skipped = _apply_global_pause(trades)
    data = rounded(metrics(accepted))
    data["skipped_global_3h"] = skipped
    return data


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=36)
    parser.add_argument("--min-turnover", type=float, default=15_000_000)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--symbols", default="")
    args = parser.parse_args()

    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=args.days)
    warmup_start = start - timedelta(days=5)
    start_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    if args.symbols.strip():
        candidates = [{"symbol": item.strip().upper(), "turnover24h": 0.0} for item in args.symbols.split(",") if item.strip()]
    else:
        candidates = await fetch_top_candidates(args.limit, args.min_turnover)
    print("candidate_universe", len(candidates), "min_turnover", args.min_turnover)
    print("candidates", [item["symbol"] for item in candidates])

    async with httpx.AsyncClient(timeout=35) as client:
        btc_rows = await fetch_klines(client, "BTCUSDT", start_ms, end_ms)
        await asyncio.sleep(0.5)
        eth_rows = await fetch_klines(client, "ETHUSDT", start_ms, end_ms)
        btc = indicators(btc_rows)
        eth = indicators(eth_rows)
        results = []
        for item in candidates:
            symbol = item["symbol"]
            for attempt in range(6):
                try:
                    rows = await fetch_klines(client, symbol, start_ms, end_ms)
                    break
                except (RuntimeError, httpx.HTTPError) as exc:
                    if attempt == 5:
                        print("skip_fetch_failed", symbol, type(exc).__name__, str(exc))
                        rows = []
                        break
                    wait = 5 + attempt * 5
                    print("retry_wait", symbol, wait, type(exc).__name__)
                    await asyncio.sleep(wait)
            if len(rows) < args.days * 80:
                print("skip_short_history", symbol, len(rows))
                continue
            trades = run_backtest("2.6", symbol, rows, indicators(rows), btc, eth)
            m14 = _with_pause_metrics(_period_trades(trades, end - timedelta(days=14)))
            m30 = _with_pause_metrics(_period_trades(trades, end - timedelta(days=30)))
            m60 = _with_pause_metrics(_period_trades(trades, start))
            score = float(m30["pnl"]) + float(m60["pnl"]) * 0.7 - float(m60["sl"]) * 0.35
            results.append({
                "symbol": symbol,
                "turnover24h": round(item["turnover24h"], 0),
                "score": round(score, 4),
                "m14": m14,
                "m30": m30,
                "m60": m60,
            })
            print("tested", symbol, "30d", m30, "60d", m60)
            await asyncio.sleep(0.55)

    results.sort(key=lambda item: item["score"], reverse=True)
    print("\n=== REPLACEMENT CANDIDATES GRID DCA 2.6 ===")
    print("period_end_utc", end.isoformat())
    print("removed", sorted(REMOVE))
    print("assumptions", "Bybit linear turnover24h prefilter; 15m candles; taker 0.05%; first order 6 USDT; global 3h pause after SL")
    for item in results:
        print(item)


if __name__ == "__main__":
    asyncio.run(main())
