import asyncio
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone

import httpx

from webapp import settings
from webapp.db import fetch_all


BYBIT_BASE = "https://api.bybit.com"
TAKER_ROUND_TRIP_PCT = 0.10


def ts_ms(dt) -> int:
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace(" ", "T")).replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def opposite(side: str) -> str:
    return "short" if side == "long" else "long"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def adaptive_tp_sl(move_abs: float) -> tuple[float, float]:
    if move_abs < 5:
        step = clamp(move_abs * 0.42, 1.2, 1.8)
        stop_min, stop_max = 2.6, 3.8
    elif move_abs < 8:
        step = clamp(move_abs * 0.36, 1.8, 2.6)
        stop_min, stop_max = 3.6, 5.2
    else:
        step = clamp(move_abs * 0.30, 2.4, 3.2)
        stop_min, stop_max = 5.0, 6.0
    grid_coverage = step + step * 1.15
    tp = clamp(move_abs * 0.34, 1.0, 2.6)
    sl = clamp(max(move_abs * 0.65, grid_coverage * 1.05, tp * 1.9), stop_min, stop_max)
    return tp, sl


def adaptive_dca_grid(move_abs: float) -> dict:
    if move_abs < 5:
        return {
            "active": 1,
            "step": clamp(move_abs * 0.42, 1.2, 1.8),
            "price_multiplier": 1.15,
            "volume_multiplier": 1.10,
        }
    if move_abs < 8:
        return {
            "active": 2,
            "step": clamp(move_abs * 0.36, 1.8, 2.6),
            "price_multiplier": 1.15,
            "volume_multiplier": 1.12,
        }
    return {
        "active": 2,
        "step": clamp(move_abs * 0.30, 2.4, 3.2),
        "price_multiplier": 1.15,
        "volume_multiplier": 1.10,
    }


def adaptive_tp_sl_v22(move_abs: float, atr_pct: float = 0.0) -> tuple[float, float]:
    grid = adaptive_dca_grid_v22(move_abs, atr_pct)
    coverage = 0.0
    step = grid["step"]
    for _ in range(int(grid["active"])):
        coverage += step
        step *= grid["price_multiplier"]
    tp = clamp(max(move_abs * 0.34, atr_pct * 1.05), 1.3, 4.0)
    sl = clamp(max(coverage * 1.18, tp * 1.85, move_abs * 0.82), 3.2, 11.0)
    return tp, sl


def adaptive_dca_grid_v22(move_abs: float, atr_pct: float = 0.0) -> dict:
    if move_abs < 5:
        return {
            "active": 1,
            "step": clamp(max(move_abs * 0.50, atr_pct * 1.0), 1.3, 2.0),
            "price_multiplier": 1.15,
            "volume_multiplier": 1.10,
        }
    if move_abs < 8:
        return {
            "active": 2,
            "step": clamp(max(move_abs * 0.42, atr_pct * 1.05), 2.0, 3.0),
            "price_multiplier": 1.15,
            "volume_multiplier": 1.12,
        }
    return {
        "active": 3,
        "step": clamp(max(move_abs * 0.34, atr_pct * 1.10), 2.8, 4.2),
        "price_multiplier": 1.15,
        "volume_multiplier": 1.08,
    }


async def bybit_get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    response = await client.get(f"{BYBIT_BASE}{path}", params=params)
    response.raise_for_status()
    data = response.json()
    if int(data.get("retCode", -1)) != 0:
        raise RuntimeError(f"Bybit {data.get('retCode')}: {data.get('retMsg')}")
    return data.get("result") or {}


async def fetch_klines(client: httpx.AsyncClient, symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    rows: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        result = await bybit_get(
            client,
            "/v5/market/kline",
            {
                "category": "linear",
                "symbol": symbol,
                "interval": "1",
                "start": cursor,
                "end": end_ms,
                "limit": 1000,
            },
        )
        batch = list(result.get("list") or [])
        if not batch:
            break
        candles = [
            {
                "time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            }
            for row in reversed(batch)
        ]
        rows.extend(candles)
        next_cursor = candles[-1]["time"] + 60_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        await asyncio.sleep(0.03)
    dedup = {row["time"]: row for row in rows}
    return [dedup[key] for key in sorted(dedup)]


def find_entry_index(candles: list[dict], event_ms: int) -> int | None:
    # Conservative fill: next full minute after the Telegram message was stored.
    for i, candle in enumerate(candles):
        if candle["time"] > event_ms:
            return i
    return None


def simulate(candles: list[dict], entry_index: int, side: str, tp_pct: float, sl_pct: float, horizon_minutes: int) -> dict:
    entry = candles[entry_index]["open"]
    if entry <= 0:
        return {"result": "bad_entry", "pnl": 0.0, "bars": 0}
    end_index = min(len(candles) - 1, entry_index + horizon_minutes)
    for idx in range(entry_index, end_index + 1):
        candle = candles[idx]
        if side == "long":
            tp_hit = candle["high"] >= entry * (1 + tp_pct / 100)
            sl_hit = candle["low"] <= entry * (1 - sl_pct / 100)
            if tp_hit and sl_hit:
                return {"result": "sl", "pnl": -sl_pct - TAKER_ROUND_TRIP_PCT, "bars": idx - entry_index}
            if tp_hit:
                return {"result": "tp", "pnl": tp_pct - TAKER_ROUND_TRIP_PCT, "bars": idx - entry_index}
            if sl_hit:
                return {"result": "sl", "pnl": -sl_pct - TAKER_ROUND_TRIP_PCT, "bars": idx - entry_index}
        else:
            tp_hit = candle["low"] <= entry * (1 - tp_pct / 100)
            sl_hit = candle["high"] >= entry * (1 + sl_pct / 100)
            if tp_hit and sl_hit:
                return {"result": "sl", "pnl": -sl_pct - TAKER_ROUND_TRIP_PCT, "bars": idx - entry_index}
            if tp_hit:
                return {"result": "tp", "pnl": tp_pct - TAKER_ROUND_TRIP_PCT, "bars": idx - entry_index}
            if sl_hit:
                return {"result": "sl", "pnl": -sl_pct - TAKER_ROUND_TRIP_PCT, "bars": idx - entry_index}
    close = candles[end_index]["close"]
    raw = (close / entry - 1) * 100 if side == "long" else (entry / close - 1) * 100
    return {"result": "timeout", "pnl": raw - TAKER_ROUND_TRIP_PCT, "bars": end_index - entry_index}


def simulate_dca(
    candles: list[dict],
    entry_index: int,
    side: str,
    move_abs: float,
    tp_pct: float,
    sl_pct: float,
    horizon_minutes: int,
    grid_fn=adaptive_dca_grid,
) -> dict:
    entry = candles[entry_index]["open"]
    if entry <= 0:
        return {"result": "bad_entry", "pnl": 0.0, "bars": 0, "orders": 0, "planned_factor": 0.0}

    grid = grid_fn(move_abs)
    distances = []
    distance = 0.0
    step = grid["step"]
    for _ in range(int(grid["active"])):
        distance += step
        distances.append(distance)
        step *= grid["price_multiplier"]

    dca_orders = []
    volume = 1.0
    for distance in distances:
        price = entry * (1 - distance / 100) if side == "long" else entry * (1 + distance / 100)
        dca_orders.append({"price": price, "notional": volume, "filled": False})
        volume *= grid["volume_multiplier"]

    planned_notional = 1.0 + sum(order["notional"] for order in dca_orders)
    filled_notional = 1.0
    qty = 1.0 / entry
    entry_cash = 1.0

    def avg_price() -> float:
        return entry_cash / qty if qty > 0 else entry

    def close_result(result: str, exit_price: float, bars: int) -> dict:
        exit_notional = qty * exit_price
        if side == "long":
            gross = exit_notional - entry_cash
        else:
            gross = entry_cash - exit_notional
        fees = (entry_cash + exit_notional) * (TAKER_ROUND_TRIP_PCT / 2 / 100)
        pnl_pct = (gross - fees) / filled_notional * 100
        return {
            "result": result,
            "pnl": pnl_pct,
            "bars": bars,
            "orders": 1 + sum(1 for order in dca_orders if order["filled"]),
            "planned_factor": planned_notional,
            "filled_factor": filled_notional,
        }

    end_index = min(len(candles) - 1, entry_index + horizon_minutes)
    for idx in range(entry_index, end_index + 1):
        candle = candles[idx]

        for order in dca_orders:
            if order["filled"]:
                continue
            fill = candle["low"] <= order["price"] if side == "long" else candle["high"] >= order["price"]
            if fill:
                order["filled"] = True
                filled_notional += order["notional"]
                entry_cash += order["notional"]
                qty += order["notional"] / order["price"]

        average = avg_price()
        if side == "long":
            tp_price = average * (1 + tp_pct / 100)
            sl_price = average * (1 - sl_pct / 100)
            tp_hit = candle["high"] >= tp_price
            sl_hit = candle["low"] <= sl_price
            if tp_hit and sl_hit:
                return close_result("sl", sl_price, idx - entry_index)
            if sl_hit:
                return close_result("sl", sl_price, idx - entry_index)
            if tp_hit:
                return close_result("tp", tp_price, idx - entry_index)
        else:
            tp_price = average * (1 - tp_pct / 100)
            sl_price = average * (1 + sl_pct / 100)
            tp_hit = candle["low"] <= tp_price
            sl_hit = candle["high"] >= sl_price
            if tp_hit and sl_hit:
                return close_result("sl", sl_price, idx - entry_index)
            if sl_hit:
                return close_result("sl", sl_price, idx - entry_index)
            if tp_hit:
                return close_result("tp", tp_price, idx - entry_index)

    return close_result("timeout", candles[end_index]["close"], end_index - entry_index)


def summarize(results: list[dict]) -> dict:
    if not results:
        return {}
    pnl = [row["pnl"] for row in results]
    wins = [row for row in results if row["pnl"] > 0]
    losses = [row for row in results if row["pnl"] <= 0]
    summary = {
        "trades": len(results),
        "win_rate": round(len(wins) / len(results) * 100, 2),
        "avg_net_pct": round(sum(pnl) / len(pnl), 4),
        "sum_net_pct": round(sum(pnl), 2),
        "tp": sum(1 for row in results if row["result"] == "tp"),
        "sl": sum(1 for row in results if row["result"] == "sl"),
        "timeout": sum(1 for row in results if row["result"] == "timeout"),
        "avg_bars": round(sum(row["bars"] for row in results) / len(results), 1),
        "profit_factor": round(
            sum(row["pnl"] for row in wins) / abs(sum(row["pnl"] for row in losses)),
            3,
        ) if losses and abs(sum(row["pnl"] for row in losses)) > 0 else None,
    }
    if any("orders" in row for row in results):
        summary["avg_orders"] = round(sum(float(row.get("orders") or 1) for row in results) / len(results), 2)
        summary["avg_filled_factor"] = round(sum(float(row.get("filled_factor") or 1) for row in results) / len(results), 2)
        summary["avg_planned_factor"] = round(sum(float(row.get("planned_factor") or 1) for row in results) / len(results), 2)
    return summary


def dedupe_events(events: list[dict], cooldown_seconds: int) -> list[dict]:
    last_by_pair: dict[str, int] = {}
    selected = []
    cooldown_ms = cooldown_seconds * 1000
    for event in events:
        t = int(event["ts"])
        pair = event["pair"]
        if pair in last_by_pair and t - last_by_pair[pair] < cooldown_ms:
            continue
        last_by_pair[pair] = t
        selected.append(event)
    return selected


async def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    cooldown_seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    allowed = list(settings.MARKET_SHOCK_ALLOWED_PAIRS)
    deny = list(settings.MARKET_SHOCK_DENY_PAIRS)
    params = tuple(allowed + deny + [days])
    placeholders_allowed = ",".join(["%s"] * len(allowed))
    placeholders_deny = ",".join(["%s"] * len(deny))
    events = fetch_all(
        f"""
        SELECT id, pair, side, move_pct, UNIX_TIMESTAMP(created_at) * 1000 AS ts, created_at
        FROM ai_market_shock_events
        WHERE pair IN ({placeholders_allowed})
          AND pair NOT IN ({placeholders_deny})
          AND ABS(move_pct) BETWEEN %s AND %s
          AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
        ORDER BY created_at ASC, id ASC
        """,
        tuple(allowed + deny + [settings.MARKET_SHOCK_MIN_MOVE_PCT, settings.MARKET_SHOCK_MAX_MOVE_PCT, days]),
    )
    raw_count = len(events)
    events = dedupe_events(events, cooldown_seconds)
    if not events:
        print({"error": "no events"})
        return
    by_pair = defaultdict(list)
    for event in events:
        event["ts"] = int(float(event["ts"]))
        event["move_pct"] = float(event["move_pct"])
        by_pair[event["pair"]].append(event)

    start_ms = min(event["ts"] for event in events) - 10 * 60_000
    end_ms = max(event["ts"] for event in events) + 6 * 60 * 60_000

    candles_by_pair: dict[str, list[dict]] = {}
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=20) as client:
        async def load(pair: str) -> None:
            async with sem:
                try:
                    candles_by_pair[pair] = await fetch_klines(client, pair, start_ms, end_ms)
                except Exception as exc:
                    candles_by_pair[pair] = []
                    print(f"WARN no candles {pair}: {exc}", file=sys.stderr)
        await asyncio.gather(*(load(pair) for pair in by_pair))

    variants = {
        "counter_tp1_sl2": {"mode": "counter", "tp": 1.0, "sl": 2.0},
        "counter_tp15_sl3": {"mode": "counter", "tp": 1.5, "sl": 3.0},
        "counter_tp2_sl4": {"mode": "counter", "tp": 2.0, "sl": 4.0},
        "counter_adaptive": {"mode": "counter", "tp": None, "sl": None},
        "impulse_adaptive": {"mode": "impulse", "tp": None, "sl": None},
        "counter_dca_adaptive": {"mode": "counter", "tp": None, "sl": None, "dca": True},
        "counter_dca_adaptive_v22": {"mode": "counter", "tp": "adaptive_v22", "sl": "adaptive_v22", "dca": True, "grid": "v22"},
        "counter_dca_tp15_sl4": {"mode": "counter", "tp": 1.5, "sl": 4.0, "dca": True},
        "counter_dca_tp2_sl4": {"mode": "counter", "tp": 2.0, "sl": 4.0, "dca": True},
        "counter_dca_tp2_sl5": {"mode": "counter", "tp": 2.0, "sl": 5.0, "dca": True},
    }
    buckets: dict[str, list[dict]] = {key: [] for key in variants}
    skipped = 0
    for event in events:
        candles = candles_by_pair.get(event["pair"]) or []
        entry_index = find_entry_index(candles, event["ts"])
        if entry_index is None:
            skipped += 1
            continue
        for name, cfg in variants.items():
            side = opposite(event["side"]) if cfg["mode"] == "counter" else event["side"]
            if cfg["tp"] == "adaptive_v22":
                tp, sl = adaptive_tp_sl_v22(abs(event["move_pct"]))
            elif cfg["tp"] is None:
                tp, sl = adaptive_tp_sl(abs(event["move_pct"]))
            else:
                tp, sl = cfg["tp"], cfg["sl"]
            if cfg.get("dca"):
                grid_fn = adaptive_dca_grid_v22 if cfg.get("grid") == "v22" else adaptive_dca_grid
                result = simulate_dca(candles, entry_index, side, abs(event["move_pct"]), tp, sl, 180, grid_fn=grid_fn)
            else:
                result = simulate(candles, entry_index, side, tp, sl, 180)
            result.update({"pair": event["pair"], "event_side": event["side"], "trade_side": side, "move_pct": event["move_pct"]})
            buckets[name].append(result)

    by_move = {}
    for low, high in [(2.8, 5), (5, 8), (8, 99)]:
        subset = [row for row in buckets["counter_dca_tp2_sl4"] if low <= abs(row["move_pct"]) < high]
        by_move[f"{low:g}-{high:g}%"] = summarize(subset)

    by_symbol = []
    grouped = defaultdict(list)
    for row in buckets["counter_dca_tp2_sl4"]:
        grouped[row["pair"]].append(row)
    for pair, rows in grouped.items():
        if len(rows) >= 20:
            s = summarize(rows)
            s["pair"] = pair
            by_symbol.append(s)
    by_symbol.sort(key=lambda row: row["sum_net_pct"])

    output = {
        "period_days": days,
        "raw_events": raw_count,
        "deduped_events": len(events),
        "pairs": len(by_pair),
        "skipped_no_candles": skipped,
        "variants": {name: summarize(rows) for name, rows in buckets.items()},
        "counter_dca_tp2_sl4_by_move": by_move,
        "worst_pairs_counter_dca_tp2_sl4": by_symbol[:12],
        "best_pairs_counter_dca_tp2_sl4": by_symbol[-12:],
    }
    import json
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
