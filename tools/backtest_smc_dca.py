import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone


BYBIT_BASE = "https://api.bybit.com"
TAKER_ROUND_TRIP_PCT = 0.10
PAIRS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "SUIUSDT", "NEARUSDT", "ONDOUSDT", "LINKUSDT",
    "AVAXUSDT", "DOTUSDT", "INJUSDT", "RENDERUSDT", "ARBUSDT",
    "OPUSDT", "APTUSDT", "SEIUSDT", "TAOUSDT", "HYPEUSDT",
]


def bybit_get(path: str, params: dict) -> dict:
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{BYBIT_BASE}{path}?{query}", timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    if int(data.get("retCode", -1)) != 0:
        raise RuntimeError(f"Bybit {data.get('retCode')}: {data.get('retMsg')}")
    return data.get("result") or {}


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    rows: list[dict] = []
    cursor = start_ms
    step_ms = int(interval) * 60_000
    while cursor < end_ms:
        chunk_end = min(end_ms, cursor + step_ms * 900)
        result = bybit_get(
            "/v5/market/kline",
            {
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "start": cursor,
                "end": chunk_end,
                "limit": 1000,
            },
        )
        batch = list(result.get("list") or [])
        if not batch:
            cursor = chunk_end + step_ms
            time.sleep(0.03)
            continue
        if len(batch) >= 1000:
            raise RuntimeError(f"chunk too large for {symbol} {interval}: {len(batch)}")
        candles = [
            {
                "time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "turnover": float(row[6]),
            }
            for row in reversed(batch)
        ]
        rows.extend(candles)
        if chunk_end <= cursor:
            break
        cursor = chunk_end + step_ms
        time.sleep(0.03)
    dedup = {row["time"]: row for row in rows}
    return [dedup[key] for key in sorted(dedup)]


def atr_values(candles: list[dict], period: int = 14) -> list[float | None]:
    values: list[float | None] = [None] * len(candles)
    trs: list[float] = []
    for i, candle in enumerate(candles):
        if i == 0:
            tr = candle["high"] - candle["low"]
        else:
            prev_close = candles[i - 1]["close"]
            tr = max(
                candle["high"] - candle["low"],
                abs(candle["high"] - prev_close),
                abs(candle["low"] - prev_close),
            )
        trs.append(tr)
        if i >= period - 1:
            values[i] = sum(trs[i - period + 1:i + 1]) / period
    return values


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def find_setups(candles: list[dict]) -> list[dict]:
    lookback = 20
    confirmation_window = 4
    atr = atr_values(candles)
    setups: list[dict] = []
    for i in range(lookback + 20, len(candles) - confirmation_window - 2):
        candle = candles[i]
        atr_value = atr[i]
        if not atr_value or candle["close"] <= 0:
            continue
        atr_pct = atr_value / candle["close"] * 100
        if atr_pct < 0.25 or atr_pct > 6.0:
            continue
        prev = candles[i - lookback:i]
        prev_low = min(row["low"] for row in prev)
        prev_high = max(row["high"] for row in prev)
        body_position = (candle["close"] - candle["low"]) / max(candle["high"] - candle["low"], 1e-12)
        upper_position = (candle["high"] - candle["close"]) / max(candle["high"] - candle["low"], 1e-12)

        long_sweep = candle["low"] < prev_low * 0.9995 and candle["close"] > prev_low and body_position >= 0.52
        short_sweep = candle["high"] > prev_high * 1.0005 and candle["close"] < prev_high and upper_position >= 0.52

        if long_sweep:
            for j in range(i + 1, i + confirmation_window + 1):
                if candles[j]["close"] > candle["high"]:
                    entry_index = j + 1
                    if entry_index >= len(candles):
                        break
                    stop_price = candle["low"] - atr_value * 0.25
                    entry_price = candles[entry_index]["open"]
                    stop_pct = (entry_price - stop_price) / entry_price * 100
                    if 0.8 <= stop_pct <= 4.5:
                        target_pct = clamp((prev_high - entry_price) / entry_price * 100 * 0.65, 1.0, 3.0)
                        setups.append({
                            "side": "long",
                            "setup_index": i,
                            "entry_index": entry_index,
                            "stop_price": stop_price,
                            "atr_pct": atr_pct,
                            "target_pct": target_pct,
                            "sweep_depth_pct": (prev_low - candle["low"]) / prev_low * 100,
                        })
                    break

        if short_sweep:
            for j in range(i + 1, i + confirmation_window + 1):
                if candles[j]["close"] < candle["low"]:
                    entry_index = j + 1
                    if entry_index >= len(candles):
                        break
                    stop_price = candle["high"] + atr_value * 0.25
                    entry_price = candles[entry_index]["open"]
                    stop_pct = (stop_price - entry_price) / entry_price * 100
                    if 0.8 <= stop_pct <= 4.5:
                        target_pct = clamp((entry_price - prev_low) / entry_price * 100 * 0.65, 1.0, 3.0)
                        setups.append({
                            "side": "short",
                            "setup_index": i,
                            "entry_index": entry_index,
                            "stop_price": stop_price,
                            "atr_pct": atr_pct,
                            "target_pct": target_pct,
                            "sweep_depth_pct": (candle["high"] - prev_high) / prev_high * 100,
                        })
                    break
    return setups


def simulate_dca(candles: list[dict], setup: dict, tp_pct: float | None, horizon_bars: int = 96) -> dict:
    entry_index = int(setup["entry_index"])
    entry = candles[entry_index]["open"]
    side = setup["side"]
    atr_pct = float(setup["atr_pct"])
    step = clamp(atr_pct * 0.60, 0.40, 1.40)
    distances = [step, step + step * 1.15]
    volumes = [1.0, 1.0, 1.15]
    dca_orders = []
    for distance, volume in zip(distances, volumes[1:]):
        price = entry * (1 - distance / 100) if side == "long" else entry * (1 + distance / 100)
        dca_orders.append({"price": price, "notional": volume, "filled": False})

    qty = volumes[0] / entry
    entry_cash = volumes[0]
    filled_notional = volumes[0]
    planned_notional = sum(volumes)
    tp = float(tp_pct if tp_pct is not None else setup["target_pct"])
    stop_price = float(setup["stop_price"])

    def average_price() -> float:
        return entry_cash / qty if qty > 0 else entry

    def close(result: str, exit_price: float, bars: int) -> dict:
        exit_notional = qty * exit_price
        gross = exit_notional - entry_cash if side == "long" else entry_cash - exit_notional
        fees = (entry_cash + exit_notional) * (TAKER_ROUND_TRIP_PCT / 2 / 100)
        pnl_pct = (gross - fees) / filled_notional * 100
        return {
            "result": result,
            "pnl": pnl_pct,
            "bars": bars,
            "orders": 1 + sum(1 for order in dca_orders if order["filled"]),
            "filled_factor": filled_notional,
            "planned_factor": planned_notional,
        }

    end_index = min(len(candles) - 1, entry_index + horizon_bars)
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

        avg = average_price()
        if side == "long":
            tp_price = avg * (1 + tp / 100)
            tp_hit = candle["high"] >= tp_price
            sl_hit = candle["low"] <= stop_price
        else:
            tp_price = avg * (1 - tp / 100)
            tp_hit = candle["low"] <= tp_price
            sl_hit = candle["high"] >= stop_price
        if tp_hit and sl_hit:
            return close("sl", stop_price, idx - entry_index)
        if sl_hit:
            return close("sl", stop_price, idx - entry_index)
        if tp_hit:
            return close("tp", tp_price, idx - entry_index)
    return close("timeout", candles[end_index]["close"], end_index - entry_index)


def summarize(results: list[dict]) -> dict:
    if not results:
        return {
            "trades": 0,
            "win_rate": 0,
            "avg_net_pct": 0,
            "sum_net_pct": 0,
            "profit_factor": None,
        }
    wins = [row for row in results if row["pnl"] > 0]
    losses = [row for row in results if row["pnl"] <= 0]
    win_sum = sum(row["pnl"] for row in wins)
    loss_sum = abs(sum(row["pnl"] for row in losses))
    return {
        "trades": len(results),
        "win_rate": round(len(wins) / len(results) * 100, 2),
        "avg_net_pct": round(sum(row["pnl"] for row in results) / len(results), 4),
        "sum_net_pct": round(sum(row["pnl"] for row in results), 2),
        "profit_factor": round(win_sum / loss_sum, 3) if loss_sum > 0 else None,
        "tp": sum(1 for row in results if row["result"] == "tp"),
        "sl": sum(1 for row in results if row["result"] == "sl"),
        "timeout": sum(1 for row in results if row["result"] == "timeout"),
        "avg_bars": round(sum(row["bars"] for row in results) / len(results), 1),
        "avg_orders": round(sum(row["orders"] for row in results) / len(results), 2),
        "avg_filled_factor": round(sum(row["filled_factor"] for row in results) / len(results), 2),
        "avg_planned_factor": round(sum(row["planned_factor"] for row in results) / len(results), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--interval", default="15")
    args = parser.parse_args()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    variants = {
        "smc_dca_liquidity": None,
        "smc_dca_tp12": 1.2,
        "smc_dca_tp15": 1.5,
        "smc_dca_tp20": 2.0,
    }
    all_results = {key: [] for key in variants}
    by_pair = {key: defaultdict(list) for key in variants}
    diagnostics = {}
    for pair in PAIRS:
        try:
            candles = fetch_klines(pair, args.interval, start_ms, end_ms)
            setups = find_setups(candles)
            filtered = []
            last_exit_index = -1
            for setup in setups:
                if setup["entry_index"] <= last_exit_index:
                    continue
                sample = simulate_dca(candles, setup, 1.5)
                last_exit_index = int(setup["entry_index"]) + int(sample["bars"])
                filtered.append(setup)
            diagnostics[pair] = {"candles": len(candles), "setups": len(setups), "deduped_setups": len(filtered)}
            for setup in filtered:
                for name, tp in variants.items():
                    result = simulate_dca(candles, setup, tp)
                    result.update({"pair": pair, "side": setup["side"], "atr_pct": setup["atr_pct"]})
                    all_results[name].append(result)
                    by_pair[name][pair].append(result)
        except Exception as exc:
            diagnostics[pair] = {"error": str(exc)}

    output = {
        "period_days": args.days,
        "interval": args.interval,
        "pairs": PAIRS,
        "diagnostics": diagnostics,
        "variants": {name: summarize(rows) for name, rows in all_results.items()},
        "by_pair": {
            name: {
                pair: summarize(rows)
                for pair, rows in sorted(pair_rows.items())
            }
            for name, pair_rows in by_pair.items()
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
