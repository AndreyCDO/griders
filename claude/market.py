"""
tools/market.py — инструменты рыночных данных.
Источник: Bybit V5 Public API (ключи не нужны).
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

import bybit_client as bybit
import indicators as ind


async def get_price(symbol: str, category: str = "linear") -> dict:
    sym  = bybit.to_symbol(symbol)
    data = await bybit.get("/v5/market/tickers", {"category": category, "symbol": sym})
    t    = data["list"][0]
    return {
        "symbol":       t["symbol"],
        "price":        float(t["lastPrice"]),
        "mark_price":   float(t.get("markPrice",  t["lastPrice"])),
        "index_price":  float(t.get("indexPrice", t["lastPrice"])),
        "change_24h":   round(float(t.get("price24hPcnt", 0)) * 100, 3),
        "high_24h":     float(t["highPrice24h"]),
        "low_24h":      float(t["lowPrice24h"]),
        "volume_24h":   float(t["volume24h"]),
        "turnover_24h": float(t.get("turnover24h", 0)),
        "funding_rate": float(t.get("fundingRate", 0)),
        "next_funding": t.get("nextFundingTime"),
        "bid":          float(t.get("bid1Price", 0)),
        "ask":          float(t.get("ask1Price", 0)),
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }


async def get_candles(symbol: str, interval: str = "1", limit: int = 200, category: str = "linear") -> dict:
    sym  = bybit.to_symbol(symbol)
    data = await bybit.get(
        "/v5/market/kline",
        {"category": category, "symbol": sym, "interval": interval, "limit": min(limit, 200)},
    )
    # Bybit возвращает [startTime, open, high, low, close, volume, turnover], новые первые
    candles = [
        {
            "time":     int(r[0]),
            "open":     float(r[1]),
            "high":     float(r[2]),
            "low":      float(r[3]),
            "close":    float(r[4]),
            "volume":   float(r[5]),
            "turnover": float(r[6]),
        }
        for r in reversed(data["list"])
    ]
    return {"symbol": sym, "interval": interval, "candles": candles, "count": len(candles)}


async def get_orderbook(symbol: str, depth: int = 25, category: str = "linear") -> dict:
    sym  = bybit.to_symbol(symbol)
    data = await bybit.get("/v5/market/orderbook", {"category": category, "symbol": sym, "limit": depth})
    bids = [[float(p), float(q)] for p, q in data["b"]]
    asks = [[float(p), float(q)] for p, q in data["a"]]
    bid_vol = sum(q for _, q in bids)
    ask_vol = sum(q for _, q in asks)
    spread  = round(asks[0][0] - bids[0][0], 8) if bids and asks else 0.0
    spread_pct = round(spread / bids[0][0] * 100, 4) if bids else 0.0
    return {
        "symbol":        sym,
        "best_bid":      bids[0][0] if bids else 0,
        "best_ask":      asks[0][0] if asks else 0,
        "spread":        spread,
        "spread_pct":    spread_pct,
        "bid_volume":    round(bid_vol, 4),
        "ask_volume":    round(ask_vol, 4),
        "buy_pressure":  round(bid_vol / (bid_vol + ask_vol) * 100, 1) if (bid_vol + ask_vol) else 50.0,
        "bids":          bids[:8],
        "asks":          asks[:8],
        "scalping_ok":   spread_pct < 0.05,
    }


async def get_recent_trades(symbol: str, limit: int = 60, category: str = "linear") -> dict:
    sym  = bybit.to_symbol(symbol)
    data = await bybit.get("/v5/market/recent-trade", {"category": category, "symbol": sym, "limit": min(limit, 500)})
    trades = [{"price": float(t["price"]), "qty": float(t["size"]), "side": t["side"], "time": t["time"]}
              for t in data["list"]]
    buy_vol  = sum(t["qty"] for t in trades if t["side"] == "Buy")
    sell_vol = sum(t["qty"] for t in trades if t["side"] == "Sell")
    total    = buy_vol + sell_vol
    return {
        "symbol":     sym,
        "trades":     trades[:20],
        "buy_vol":    round(buy_vol,  4),
        "sell_vol":   round(sell_vol, 4),
        "buy_pct":    round(buy_vol  / total * 100, 1) if total else 50.0,
        "aggression": "BUYERS" if buy_vol > sell_vol * 1.3 else ("SELLERS" if sell_vol > buy_vol * 1.3 else "BALANCED"),
    }


async def get_funding_rate(symbol: str) -> dict:
    sym  = bybit.to_symbol(symbol)
    data = await bybit.get("/v5/market/funding/history", {"category": "linear", "symbol": sym, "limit": 3})
    rates = [{"rate": float(r["fundingRate"]), "time": r["fundingRateTimestamp"]} for r in data["list"]]
    rate  = rates[0]["rate"] if rates else 0.0
    return {
        "symbol":          sym,
        "current_rate":    rate,
        "rate_pct":        round(rate * 100, 4),
        "rate_annualized": round(rate * 3 * 365 * 100, 1),
        "interpretation": (
            "HIGH_POSITIVE: рынок перегрет вверх, давление на SHORT" if rate > 0.001
            else "HIGH_NEGATIVE: рынок перегрет вниз, давление на LONG" if rate < -0.001
            else "NEUTRAL"
        ),
        "history": rates,
    }


async def get_open_interest(symbol: str, interval_time: str = "5min", limit: int = 20) -> dict:
    sym  = bybit.to_symbol(symbol)
    data = await bybit.get(
        "/v5/market/open-interest",
        {"category": "linear", "symbol": sym, "intervalTime": interval_time, "limit": limit},
    )
    items = [{"oi": float(r["openInterest"]), "time": r["timestamp"]} for r in data["list"]]
    change = round((items[0]["oi"] - items[-1]["oi"]) / items[-1]["oi"] * 100, 2) if len(items) >= 2 else 0.0
    return {
        "symbol":     sym,
        "current_oi": items[0]["oi"] if items else 0,
        "change_pct": change,
        "trend":      "INCREASING" if change > 1 else ("DECREASING" if change < -1 else "FLAT"),
        "history":    items[:10],
    }


async def analyze_indicators(symbol: str, interval: str = "1", limit: int = 200, category: str = "linear") -> dict:
    sym  = bybit.to_symbol(symbol)
    data = await bybit.get(
        "/v5/market/kline",
        {"category": category, "symbol": sym, "interval": interval, "limit": min(limit, 200)},
    )
    candles: list[ind.Candle] = [
        ind.Candle(
            time=int(r[0]), open=float(r[1]), high=float(r[2]),
            low=float(r[3]), close=float(r[4]), volume=float(r[5]),
        )
        for r in reversed(data["list"])
    ]
    result = ind.full_analysis(candles)
    return {
        "symbol":    sym,
        "interval":  f"{interval}m",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **result,
    }


async def get_market_overview(symbols: list[str] | None = None, category: str = "linear") -> dict:
    syms    = [bybit.to_symbol(s) for s in (symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"])]
    tasks   = [bybit.get("/v5/market/tickers", {"category": category, "symbol": s}) for s in syms]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    overview = []
    for sym, res in zip(syms, results):
        if isinstance(res, Exception):
            overview.append({"symbol": sym, "error": str(res)})
        else:
            t = res["list"][0]
            overview.append({
                "symbol":       t["symbol"],
                "price":        float(t["lastPrice"]),
                "change_24h":   round(float(t.get("price24hPcnt", 0)) * 100, 2),
                "volume_usdt_m": round(float(t.get("turnover24h", 0)) / 1e6, 1),
                "funding_rate": round(float(t.get("fundingRate", 0)) * 100, 4),
                "high_24h":     float(t["highPrice24h"]),
                "low_24h":      float(t["lowPrice24h"]),
            })
    overview.sort(key=lambda x: x.get("volume_usdt_m", 0), reverse=True)
    return {
        "pairs":             overview,
        "best_for_scalping": overview[0] if overview else None,
        "note":              "Отсортировано по объёму (млн USDT/24h). Больший объём = лучше ликвидность.",
    }
