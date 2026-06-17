"""Compare GRID DCA 2.6 and GRID DCA 3.1 on Bybit linear futures candles.

Research script only. It mirrors the current TradingView dual Pine signal
filters closely enough for strategy comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx

from tools.backtest_grid_dca_v25 import (
    ATR_LEN,
    BB_LEN,
    BB_MULT,
    EMA_FAST,
    EMA_MID,
    EMA_SLOW,
    FIRST_ORDER_QUOTE,
    INTERVAL,
    PAIRS,
    RSI_LEN,
    TAKER_FEE,
    VOL_LEN,
    atr,
    clamp,
    ema,
    fetch_klines,
    metrics,
    rounded,
    rsi,
    sma,
    stdev,
)


INTERVAL_MS = 15 * 60 * 1000
HOUR_MS = 60 * 60 * 1000


def _utc_floor_15m(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc).replace(second=0, microsecond=0)
    minute = value.minute - value.minute % 15
    return value.replace(minute=minute)


def indicators(rows: list) -> dict:
    open_ = [float(item[1]) for item in rows]
    high = [float(item[2]) for item in rows]
    low = [float(item[3]) for item in rows]
    close = [float(item[4]) for item in rows]
    volume = [float(item[5]) for item in rows]
    return {
        "o": open_,
        "h": high,
        "l": low,
        "c": close,
        "v": volume,
        "t": [int(item[0]) for item in rows],
        "ef": ema(close, EMA_FAST),
        "em": ema(close, EMA_MID),
        "es": ema(close, EMA_SLOW),
        "rsi15": rsi(close, RSI_LEN),
        "atr": atr(high, low, close, ATR_LEN),
    }


def hourly_rsi_by_15m(rows: list) -> list[float | None]:
    buckets: dict[int, list] = {}
    for row in rows:
        ts = int(row[0])
        bucket = ts - ts % HOUR_MS
        buckets.setdefault(bucket, row)
        buckets[bucket] = row
    hourly_rows = [buckets[key] for key in sorted(buckets)]
    hourly_close = [float(item[4]) for item in hourly_rows]
    hourly_values = rsi(hourly_close, RSI_LEN)
    hourly_by_ts = {int(row[0]) - int(row[0]) % HOUR_MS: value for row, value in zip(hourly_rows, hourly_values)}
    result = []
    last_value = None
    for row in rows:
        bucket = int(row[0]) - int(row[0]) % HOUR_MS
        value = hourly_by_ts.get(bucket)
        if value is not None:
            last_value = value
        result.append(last_value)
    return result


def grid_for(version: str, stage: str, atr_pct: float) -> dict:
    if version == "3.1":
        mult = 0.95 if stage == "range" else 1.15 if stage == "trend" else 0.85
        step = clamp(atr_pct * mult, 0.55, 2.2)
        take_profit = clamp(step * 0.55, 0.4, 0.95)
        stop_loss = max(3.5 if stage == "pullback" else 3.0, min(6.2, step * 4.0))
        return {
            "dca_active": 3 if stage in {"range", "pullback"} else 2,
            "mult_vol": 1.2 if stage != "range" else 1.15,
            "mult_price": 1.1 if stage == "pullback" else 1.15 if stage == "trend" else 1.05,
            "step": step,
            "tp": take_profit,
            "sl": stop_loss,
        }

    mult = 0.85 if stage == "range" else 1.1 if stage == "trend" else 0.75
    if stage == "range":
        step = clamp(atr_pct * mult, 0.45, 1.8)
        take_profit = clamp(step * 0.55, 0.35, 0.75)
        dca_active, mult_vol, mult_price = 3, 1.15, 1.05
    elif stage == "trend":
        step = clamp(atr_pct * mult, 0.75, 2.4)
        take_profit = clamp(step * 0.55, 0.45, 1.0)
        dca_active, mult_vol, mult_price = 2, 1.2, 1.15
    else:
        step = clamp(atr_pct * mult, 0.55, 2.0)
        take_profit = clamp(step * 0.55, 0.4, 0.85)
        dca_active, mult_vol, mult_price = 3, 1.2, 1.1
    stop_loss = max(3.5 if stage == "pullback" else 3.0, min(6.5, step * 4.0))
    return {
        "dca_active": dca_active,
        "mult_vol": mult_vol,
        "mult_price": mult_price,
        "step": step,
        "tp": take_profit,
        "sl": stop_loss,
    }


def _signal_at(version: str, ind: dict, index: int, btc: dict, eth: dict, rsi60: list[float | None]) -> dict | None:
    close = ind["c"]
    if index < max(EMA_SLOW, BB_LEN, VOL_LEN, ATR_LEN, 3):
        return None
    if ind["rsi15"][index] is None or rsi60[index] is None or ind["atr"][index] is None:
        return None
    basis = sma(close, BB_LEN, index)
    sd = stdev(close, BB_LEN, index)
    volbase = sma(ind["v"], VOL_LEN, index)
    if not basis or sd is None or not volbase:
        return None

    upper = basis + BB_MULT * sd
    lower = basis - BB_MULT * sd
    width = (upper - lower) / basis * 100 if basis else 0
    bbpos = (close[index] - lower) / (upper - lower) * 100 if upper > lower else 50
    bull = ind["ef"][index] > ind["em"][index] > ind["es"][index]
    bear = ind["ef"][index] < ind["em"][index] < ind["es"][index]
    atr_pct = ind["atr"][index] / close[index] * 100 if close[index] else 0
    volratio = ind["v"][index] / volbase if volbase else 1
    candle = (close[index] - ind["o"][index]) / ind["o"][index] * 100 if ind["o"][index] else 0
    bar_move = (close[index] - close[index - 1]) / close[index - 1] * 100 if close[index - 1] else 0
    rsi15 = float(ind["rsi15"][index])
    rsi1h = float(rsi60[index])

    btc1 = (btc["c"][index] - btc["c"][index - 1]) / btc["c"][index - 1] * 100 if btc["c"][index - 1] else 0
    btc3 = (btc["c"][index] - btc["c"][index - 3]) / btc["c"][index - 3] * 100 if btc["c"][index - 3] else 0
    eth1 = (eth["c"][index] - eth["c"][index - 1]) / eth["c"][index - 1] * 100 if eth["c"][index - 1] else 0
    eth3 = (eth["c"][index] - eth["c"][index - 3]) / eth["c"][index - 3] * 100 if eth["c"][index - 3] else 0

    if version == "3.1":
        max_bb_width = 2.8
        min_volume = 0.75
        max_atr = 3.2
        macro_1 = 0.55
        macro_3 = 0.9
        adverse_candle = 0.5
        adverse_volume = 1.2
        range_market = not bull and not bear and width <= max_bb_width
        macro_long = btc1 <= -macro_1 or eth1 <= -macro_1 or btc3 <= -macro_3 or eth3 <= -macro_3
        macro_short = btc1 >= macro_1 or eth1 >= macro_1 or btc3 >= macro_3 or eth3 >= macro_3
        long_break = bbpos < 5 or candle <= -adverse_candle or (bar_move <= -adverse_candle and volratio >= adverse_volume) or (close[index] < lower and volratio >= 0.9)
        short_break = bbpos > 95 or candle >= adverse_candle or (bar_move >= adverse_candle and volratio >= adverse_volume) or (close[index] > upper and volratio >= 0.9)
        pull_up = bull and 44 <= bbpos <= 56 and 44 <= rsi15 <= 56 and 45 <= rsi1h <= 64 and volratio >= min_volume and not long_break and not macro_long
        pull_down = bear and 48 <= bbpos <= 72 and 40 <= rsi15 <= 58 and 32 <= rsi1h <= 56 and volratio >= min_volume and not short_break and not macro_short
        tradable = atr_pct <= max_atr and volratio >= min_volume
        range_short = range_market and 78 <= bbpos <= 95 and 60 <= rsi15 <= 72 and rsi1h <= 60 and not short_break and not macro_short
        trend_short = bear and 40 <= rsi15 <= 54 and rsi1h <= 55 and 48 <= bbpos <= 72 and not short_break and not macro_short
    else:
        max_bb_width = 3.5
        min_volume = 0.45
        max_atr = 4.0
        macro_1 = 0.8
        macro_3 = 1.2
        range_market = not bull and not bear and width <= max_bb_width
        macro_long = btc1 <= -macro_1 or eth1 <= -macro_1 or btc3 <= -macro_3 or eth3 <= -macro_3
        macro_short = btc1 >= macro_1 or eth1 >= macro_1 or btc3 >= macro_3 or eth3 >= macro_3
        long_break = bbpos < 0 or candle <= -0.7 or (bar_move <= -0.7 and volratio >= 1.4) or (close[index] < lower and volratio >= 1.0)
        short_break = bbpos > 100 or candle >= 0.7 or (bar_move >= 0.7 and volratio >= 1.4) or (close[index] > upper and volratio >= 1.0)
        pull_up = bull and 42 <= bbpos <= 58 and 42 <= rsi15 <= 58 and 42 <= rsi1h <= 68 and not long_break and not macro_long
        pull_down = bear and 45 <= bbpos <= 78 and 38 <= rsi15 <= 60 and 30 <= rsi1h <= 60 and not short_break and not macro_short
        tradable = atr_pct <= max_atr and volratio >= min_volume
        range_short = range_market and bbpos >= 75 and rsi15 >= 58 and rsi1h <= 65 and not short_break and not macro_short
        trend_short = bear and 38 <= rsi15 <= 56 and rsi1h <= 58 and 42 <= bbpos <= 78 and not short_break and not macro_short

    side = "long" if tradable and pull_up else "short" if tradable and (range_short or trend_short or pull_down) else None
    if not side:
        return None
    stage = "pullback" if pull_up or pull_down else "trend" if bull or bear else "range"
    return {
        "side": side,
        "stage": stage,
        "grid": grid_for(version, stage, atr_pct),
        "atr": atr_pct,
        "volratio": volratio,
        "rsi15": rsi15,
        "rsi60": rsi1h,
        "bbpos": bbpos,
        "bbwidth": width,
    }


def run_backtest(version: str, symbol: str, rows: list, ind: dict, btc: dict, eth: dict) -> list[dict]:
    trades = []
    rsi60 = hourly_rsi_by_15m(rows)
    index = max(EMA_SLOW, VOL_LEN, BB_LEN, ATR_LEN, 3)
    while index < len(rows) - 1:
        signal = _signal_at(version, ind, index, btc, eth, rsi60)
        if not signal:
            index += 1
            continue

        entry_index = index + 1
        entry = float(rows[entry_index][1])
        side = signal["side"]
        grid = signal["grid"]
        orders = []
        fees = 0.0
        orders.append((entry, FIRST_ORDER_QUOTE / entry, FIRST_ORDER_QUOTE))
        fees += FIRST_ORDER_QUOTE * TAKER_FEE

        levels = []
        step = grid["step"]
        cumulative = 0.0
        for order_num in range(int(grid["dca_active"])):
            cumulative += step
            level = entry * (1 - cumulative / 100) if side == "long" else entry * (1 + cumulative / 100)
            safety_quote = FIRST_ORDER_QUOTE * (float(grid["mult_vol"]) ** order_num)
            levels.append((level, safety_quote))
            step *= float(grid["mult_price"])

        filled = 0
        exit_price = None
        exit_reason = "eod"
        exit_index = len(rows) - 1
        scan_index = entry_index + 1
        while scan_index < len(rows):
            high = float(rows[scan_index][2])
            low = float(rows[scan_index][3])
            avg = sum(price * qty for price, qty, _ in orders) / sum(qty for _, qty, _ in orders)

            while filled < len(levels):
                level, safety_quote = levels[filled]
                hit = low <= level if side == "long" else high >= level
                if not hit:
                    break
                orders.append((level, safety_quote / level, safety_quote))
                fees += safety_quote * TAKER_FEE
                filled += 1
                avg = sum(price * qty for price, qty, _ in orders) / sum(qty for _, qty, _ in orders)

            sl_price = avg * (1 - grid["sl"] / 100) if side == "long" else avg * (1 + grid["sl"] / 100)
            tp_price = avg * (1 + grid["tp"] / 100) if side == "long" else avg * (1 - grid["tp"] / 100)
            sl_hit = low <= sl_price if side == "long" else high >= sl_price
            tp_hit = high >= tp_price if side == "long" else low <= tp_price
            if sl_hit:
                exit_price = sl_price
                exit_reason = "sl"
                exit_index = scan_index
                break
            if tp_hit:
                exit_price = tp_price
                exit_reason = "tp"
                exit_index = scan_index
                break
            scan_index += 1

        if exit_price is None:
            exit_price = float(rows[-1][4])

        total_qty = sum(qty for _, qty, _ in orders)
        avg = sum(price * qty for price, qty, _ in orders) / total_qty
        entry_value = sum(quote for _, _, quote in orders)
        exit_value = total_qty * exit_price
        fees += exit_value * TAKER_FEE
        gross = (exit_price - avg) * total_qty if side == "long" else (avg - exit_price) * total_qty
        trades.append({
            "version": version,
            "symbol": symbol,
            "side": side,
            "stage": signal["stage"],
            "reason": exit_reason,
            "pnl": gross - fees,
            "gross": gross,
            "fees": fees,
            "fills": len(orders),
            "entry_value": entry_value,
            "entry_time": int(rows[entry_index][0]),
            "exit_time": int(rows[exit_index][0]),
            "duration_bars": exit_index - entry_index,
            "atr": signal["atr"],
            "volratio": signal["volratio"],
            "rsi15": signal["rsi15"],
            "rsi60": signal["rsi60"],
            "bbpos": signal["bbpos"],
            "bbwidth": signal["bbwidth"],
        })
        index = exit_index + 1
    return trades


def _apply_global_pause(trades: list[dict], hours: float = 3.0) -> tuple[list[dict], int]:
    pause_until = 0
    accepted = []
    skipped = 0
    for trade in sorted(trades, key=lambda item: (item["entry_time"], item["symbol"], item["side"])):
        if trade["entry_time"] < pause_until:
            skipped += 1
            continue
        accepted.append(trade)
        if trade["reason"] == "sl":
            pause_until = max(pause_until, trade["exit_time"] + int(hours * 60 * 60 * 1000))
    return accepted, skipped


def _metrics_with_pause(trades: list[dict]) -> dict:
    accepted, skipped = _apply_global_pause(trades)
    data = rounded(metrics(accepted))
    data["skipped_global_3h"] = skipped
    return data


async def fetch_all(days: int, end: datetime) -> tuple[datetime, datetime, dict[str, list]]:
    end = _utc_floor_15m(end)
    start = end - timedelta(days=days)
    warmup_start = start - timedelta(days=5)
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
                except (RuntimeError, httpx.HTTPError) as exc:
                    message = str(exc)
                    retryable = "Too many visits" in message or isinstance(exc, httpx.HTTPError)
                    if not retryable or attempt == 5:
                        raise
                    wait_seconds = 8 + attempt * 6
                    print("retry_wait", symbol, wait_seconds, type(exc).__name__)
                    await asyncio.sleep(wait_seconds)
            print("fetched", symbol, len(all_rows[symbol]))
            await asyncio.sleep(0.75)
    return start, end, all_rows


def print_report(start: datetime, end: datetime, all_rows: dict[str, list]) -> None:
    btc = indicators(all_rows["BTCUSDT"])
    eth = indicators(all_rows["ETHUSDT"])
    start_trade_ms = int(start.timestamp() * 1000)
    all_trades: dict[str, list[dict]] = {"2.6": [], "3.1": []}
    by_pair: dict[str, dict[str, list[dict]]] = {"2.6": defaultdict(list), "3.1": defaultdict(list)}

    for symbol in PAIRS:
        rows = all_rows[symbol]
        ind = indicators(rows)
        for version in ["2.6", "3.1"]:
            trades = run_backtest(version, symbol, rows, ind, btc, eth)
            trades = [trade for trade in trades if trade["entry_time"] >= start_trade_ms]
            all_trades[version].extend(trades)
            by_pair[version][symbol].extend(trades)

    print("\n=== BACKTEST GRID DCA 2.6 VS GRID DCA 3.1 ===")
    print("period_utc", start.isoformat(), end.isoformat())
    print("pairs", len(PAIRS), "fee_taker_pct", TAKER_FEE * 100, "first_order_quote", FIRST_ORDER_QUOTE)
    print("assumptions", "15m_candles; RSI 15m/1h; next_bar_open_entry; pessimistic_intrabar; one_active_trade_per_pair; global_3h_pause_after_sl; no_funding_no_slippage")
    for version in ["2.6", "3.1"]:
        print("TOTAL", version, _metrics_with_pause(all_trades[version]))

    print("\nBY_SIDE")
    for version in ["2.6", "3.1"]:
        for side in ["long", "short"]:
            subset = [trade for trade in all_trades[version] if trade["side"] == side]
            print(version, side, rounded(metrics(subset)))

    print("\nBY_STAGE")
    for version in ["2.6", "3.1"]:
        for stage in ["range", "trend", "pullback"]:
            subset = [trade for trade in all_trades[version] if trade["stage"] == stage]
            print(version, stage, rounded(metrics(subset)))

    print("\nBY_PAIR")
    for symbol in PAIRS:
        left = rounded(metrics(by_pair["2.6"][symbol]))
        right = rounded(metrics(by_pair["3.1"][symbol]))
        print(symbol, "2.6", left, "3.1", right)

    print("\nWORST_2.6")
    for trade in sorted(all_trades["2.6"], key=lambda item: item["pnl"])[:12]:
        print(_trade_view(trade))
    print("\nWORST_3.1")
    for trade in sorted(all_trades["3.1"], key=lambda item: item["pnl"])[:12]:
        print(_trade_view(trade))


def _trade_view(trade: dict) -> dict:
    output = {
        key: trade[key]
        for key in ["symbol", "side", "stage", "reason", "pnl", "gross", "fees", "fills", "entry_value", "atr", "volratio", "rsi15", "rsi60", "bbpos", "duration_bars"]
    }
    output["entry_time"] = datetime.fromtimestamp(trade["entry_time"] / 1000, tz=timezone.utc).isoformat()
    output["exit_time"] = datetime.fromtimestamp(trade["exit_time"] / 1000, tz=timezone.utc).isoformat()
    return rounded(output)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    start, end, all_rows = await fetch_all(args.days, datetime.now(timezone.utc))
    print_report(start, end, all_rows)


if __name__ == "__main__":
    asyncio.run(main())
