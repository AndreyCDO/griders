"""Backtest GRID DCA 2.5 on Bybit linear futures candles.

This is a research script, not production execution code.
"""

from __future__ import annotations

import asyncio
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx


PAIRS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "NEARUSDT",
    "ZECUSDT", "ONDOUSDT", "XRPUSDT", "SUIUSDT", "DOGEUSDT",
    "TAOUSDT", "RENDERUSDT", "ADAUSDT", "INJUSDT", "TIAUSDT",
    "ENAUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT", "ARBUSDT",
]

END = datetime(2026, 6, 3, 0, 0, tzinfo=timezone.utc)
START = END - timedelta(days=30)
INTERVAL = "15"
TAKER_FEE = 0.0005
FIRST_ORDER_QUOTE = 6.0

BB_LEN = 20
BB_MULT = 2.0
RSI_LEN = 14
ATR_LEN = 14
EMA_FAST = 9
EMA_MID = 21
EMA_SLOW = 50
VOL_LEN = 30
MIN_VOL_RATIO = 0.45
MAX_ATR = 4.0
MAX_BB_WIDTH = 3.5
MACRO_MOVE1 = 0.8
MACRO_MOVE3 = 1.2
MAX_LONG_RED = 0.7
MAX_SHORT_GREEN = 0.7

PRESETS = {
    "range": {
        "dca_active": 3, "mult_vol": 1.15, "mult_price": 1.05,
        "min_step": 0.45, "max_step": 1.8, "min_tp": 0.35,
        "max_tp": 0.75,
    },
    "trend": {
        "dca_active": 2, "mult_vol": 1.2, "mult_price": 1.15,
        "min_step": 0.75, "max_step": 2.4, "min_tp": 0.45,
        "max_tp": 1.0,
    },
    "pullback": {
        "dca_active": 3, "mult_vol": 1.2, "mult_price": 1.1,
        "min_step": 0.55, "max_step": 2.0, "min_tp": 0.4,
        "max_tp": 0.85,
    },
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def ema(values: list[float], length: int) -> list[float]:
    result = []
    k = 2 / (length + 1)
    current = None
    for value in values:
        current = value if current is None else value * k + current * (1 - k)
        result.append(current)
    return result


def sma(values: list[float], length: int, index: int) -> float | None:
    if index + 1 < length:
        return None
    return sum(values[index - length + 1:index + 1]) / length


def stdev(values: list[float], length: int, index: int) -> float | None:
    mean = sma(values, length, index)
    if mean is None:
        return None
    window = values[index - length + 1:index + 1]
    return (sum((item - mean) ** 2 for item in window) / length) ** 0.5


def rsi(values: list[float], length: int = 14) -> list[float | None]:
    result: list[float | None] = []
    avg_gain = None
    avg_loss = None
    gains = []
    losses = []
    for index, value in enumerate(values):
        if index == 0:
            result.append(None)
            continue
        change = value - values[index - 1]
        gain = max(change, 0)
        loss = max(-change, 0)
        if index <= length:
            gains.append(gain)
            losses.append(loss)
            if index < length:
                result.append(None)
                continue
            avg_gain = sum(gains) / length
            avg_loss = sum(losses) / length
        else:
            avg_gain = (avg_gain * (length - 1) + gain) / length
            avg_loss = (avg_loss * (length - 1) + loss) / length
        rs = avg_gain / avg_loss if avg_loss else 999
        result.append(100 - 100 / (1 + rs))
    return result


def atr(high: list[float], low: list[float], close: list[float], length: int = 14) -> list[float | None]:
    trs = []
    result: list[float | None] = []
    current = None
    for index, close_value in enumerate(close):
        tr = high[index] - low[index] if index == 0 else max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )
        trs.append(tr)
        if index < length:
            result.append(None)
            continue
        if index == length:
            current = sum(trs[1:length + 1]) / length
        else:
            current = (current * (length - 1) + tr) / length
        result.append(current)
    return result


def grid_for(stage: str, atr_pct: float) -> dict:
    preset = PRESETS[stage]
    mult = 0.85 if stage == "range" else 1.1 if stage == "trend" else 0.75
    step = clamp(atr_pct * mult, preset["min_step"], preset["max_step"])
    take_profit = clamp(step * 0.55, preset["min_tp"], preset["max_tp"])
    stop_loss = max(3.5 if stage == "pullback" else 3.0, min(6.5, step * 4.0))
    return {**preset, "step": step, "tp": take_profit, "sl": stop_loss}


async def fetch_klines(client: httpx.AsyncClient, symbol: str, start_ms: int, end_ms: int) -> list:
    rows = []
    cursor_end = end_ms
    while cursor_end > start_ms:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": INTERVAL,
            "start": start_ms,
            "end": cursor_end,
            "limit": 1000,
        }
        data = None
        for attempt in range(8):
            response = await client.get("https://api.bybit.com/v5/market/kline", params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("retCode") == 10006 or "Too many visits" in str(data.get("retMsg")):
                await asyncio.sleep(1.0 + attempt * 0.75)
                continue
            break
        if data.get("retCode") != 0:
            raise RuntimeError(f"{symbol}: {data.get('retMsg')}")
        page = data["result"]["list"] or []
        if not page:
            break
        rows.extend(page)
        oldest = min(int(item[0]) for item in page)
        if oldest <= start_ms or len(page) < 1000:
            break
        cursor_end = oldest - 1
        await asyncio.sleep(0.03)
    unique = {int(item[0]): item for item in rows if start_ms <= int(item[0]) <= end_ms}
    return [unique[key] for key in sorted(unique)]


def indicators(rows: list) -> dict:
    open_ = [float(item[1]) for item in rows]
    high = [float(item[2]) for item in rows]
    low = [float(item[3]) for item in rows]
    close = [float(item[4]) for item in rows]
    volume = [float(item[5]) for item in rows]
    return {
        "o": open_, "h": high, "l": low, "c": close, "v": volume,
        "ef": ema(close, EMA_FAST), "em": ema(close, EMA_MID), "es": ema(close, EMA_SLOW),
        "rsi": rsi(close, RSI_LEN), "atr": atr(high, low, close, ATR_LEN),
    }


def signal_at(ind: dict, index: int, btc: dict, eth: dict) -> dict | None:
    close = ind["c"]
    if index < max(EMA_SLOW, BB_LEN, VOL_LEN, ATR_LEN, 3):
        return None
    if ind["rsi"][index] is None or ind["atr"][index] is None:
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

    btc1 = (btc["c"][index] - btc["c"][index - 1]) / btc["c"][index - 1] * 100 if btc["c"][index - 1] else 0
    btc3 = (btc["c"][index] - btc["c"][index - 3]) / btc["c"][index - 3] * 100 if btc["c"][index - 3] else 0
    eth1 = (eth["c"][index] - eth["c"][index - 1]) / eth["c"][index - 1] * 100 if eth["c"][index - 1] else 0
    eth3 = (eth["c"][index] - eth["c"][index - 3]) / eth["c"][index - 3] * 100 if eth["c"][index - 3] else 0

    range_market = not bull and not bear and width <= MAX_BB_WIDTH
    macro_long = btc1 <= -MACRO_MOVE1 or eth1 <= -MACRO_MOVE1 or btc3 <= -MACRO_MOVE3 or eth3 <= -MACRO_MOVE3
    macro_short = btc1 >= MACRO_MOVE1 or eth1 >= MACRO_MOVE1 or btc3 >= MACRO_MOVE3 or eth3 >= MACRO_MOVE3
    long_break = bbpos < 0 or candle <= -MAX_LONG_RED or (bar_move <= -MAX_LONG_RED and volratio >= 1.4) or (close[index] < lower and volratio >= 1.0)
    short_break = bbpos > 100 or candle >= MAX_SHORT_GREEN or (bar_move >= MAX_SHORT_GREEN and volratio >= 1.4) or (close[index] > upper and volratio >= 1.0)

    rsi_value = ind["rsi"][index]
    pull_up = bull and 42 <= bbpos <= 58 and 42 <= rsi_value <= 58 and not long_break and not macro_long
    pull_down = bear and 45 <= bbpos <= 78 and 44 <= rsi_value <= 60 and not short_break and not macro_short
    tradable = atr_pct <= MAX_ATR and volratio >= MIN_VOL_RATIO
    range_short = range_market and bbpos >= 75 and rsi_value >= 58 and not short_break and not macro_short
    trend_short = bear and 38 <= rsi_value <= 56 and 42 <= bbpos <= 78 and not short_break and not macro_short

    side = "long" if tradable and pull_up else "short" if tradable and (range_short or trend_short or pull_down) else None
    if not side:
        return None
    stage = "pullback" if pull_up or pull_down else "trend" if bull or bear else "range"
    return {
        "side": side, "stage": stage, "grid": grid_for(stage, atr_pct),
        "atr": atr_pct, "volratio": volratio, "rsi": rsi_value,
        "bbpos": bbpos, "bbwidth": width,
    }


def run_backtest(symbol: str, rows: list, ind: dict, btc: dict, eth: dict) -> list[dict]:
    trades = []
    index = max(EMA_SLOW, VOL_LEN, BB_LEN, ATR_LEN, 3)
    while index < len(rows) - 1:
        signal = signal_at(ind, index, btc, eth)
        if not signal:
            index += 1
            continue

        entry_index = index + 1
        entry = float(rows[entry_index][1])
        side = signal["side"]
        grid = signal["grid"]
        orders = []
        fees = 0.0
        quote = FIRST_ORDER_QUOTE
        orders.append((entry, quote / entry, quote))
        fees += quote * TAKER_FEE

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

            # Pessimistic intrabar path: adverse move first, then favorable.
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
            "rsi": signal["rsi"],
            "bbpos": signal["bbpos"],
            "bbwidth": signal["bbwidth"],
        })
        index = exit_index + 1
    return trades


def metrics(trades: list[dict]) -> dict:
    wins = [trade for trade in trades if trade["pnl"] > 0]
    losses = [trade for trade in trades if trade["pnl"] <= 0]
    total = sum(trade["pnl"] for trade in trades)
    return {
        "n": len(trades),
        "pnl": total,
        "winrate": len(wins) / len(trades) * 100 if trades else 0,
        "avg": total / len(trades) if trades else 0,
        "tp": sum(1 for trade in trades if trade["reason"] == "tp"),
        "sl": sum(1 for trade in trades if trade["reason"] == "sl"),
        "eod": sum(1 for trade in trades if trade["reason"] == "eod"),
        "avg_win": statistics.mean([trade["pnl"] for trade in wins]) if wins else 0,
        "avg_loss": statistics.mean([trade["pnl"] for trade in losses]) if losses else 0,
    }


def rounded(data: dict) -> dict:
    return {key: round(value, 4) if isinstance(value, float) else value for key, value in data.items()}


async def main() -> None:
    warmup_start = START - timedelta(days=3)
    start_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    async with httpx.AsyncClient(timeout=25) as client:
        all_rows = {}
        for symbol in ["BTCUSDT", "ETHUSDT", *PAIRS]:
            if symbol in all_rows:
                continue
            all_rows[symbol] = await fetch_klines(client, symbol, start_ms, end_ms)
            print("fetched", symbol, len(all_rows[symbol]))
            await asyncio.sleep(0.25)

    btc = indicators(all_rows["BTCUSDT"])
    eth = indicators(all_rows["ETHUSDT"])
    all_trades = []
    for symbol in PAIRS:
        rows = all_rows[symbol]
        trades = run_backtest(symbol, rows, indicators(rows), btc, eth)
        trades = [trade for trade in trades if trade["entry_time"] >= int(START.timestamp() * 1000)]
        all_trades.extend(trades)

    print("\n=== BACKTEST GRID DCA 2.5 ===")
    print("period_utc", START.isoformat(), END.isoformat())
    print("pairs", len(PAIRS), "fee_taker_pct", TAKER_FEE * 100, "first_order_quote", FIRST_ORDER_QUOTE)
    print("assumptions", "next_bar_open_entry; pessimistic_intrabar; one_active_trade_per_pair; no_funding_no_slippage")
    print("TOTAL", rounded(metrics(all_trades)))

    by_pair = defaultdict(list)
    for trade in all_trades:
        by_pair[trade["symbol"]].append(trade)
    print("\nBY_PAIR")
    for symbol in PAIRS:
        print(symbol, rounded(metrics(by_pair[symbol])))

    by_stage_side = defaultdict(list)
    for trade in all_trades:
        by_stage_side[(trade["stage"], trade["side"])].append(trade)
    print("\nBY_STAGE_SIDE")
    for key, trades in sorted(by_stage_side.items(), key=lambda item: metrics(item[1])["pnl"]):
        print(key, rounded(metrics(trades)))

    print("\nWORST_TRADES")
    for trade in sorted(all_trades, key=lambda item: item["pnl"])[:20]:
        output = {
            key: trade[key]
            for key in ["symbol", "side", "stage", "reason", "pnl", "gross", "fees", "fills", "entry_value", "atr", "volratio", "rsi", "bbpos", "duration_bars"]
        }
        output["entry_time"] = datetime.fromtimestamp(trade["entry_time"] / 1000, tz=timezone.utc).isoformat()
        output["exit_time"] = datetime.fromtimestamp(trade["exit_time"] / 1000, tz=timezone.utc).isoformat()
        print(rounded(output))

    print("\nBEST_TRADES")
    for trade in sorted(all_trades, key=lambda item: item["pnl"], reverse=True)[:10]:
        output = {
            key: trade[key]
            for key in ["symbol", "side", "stage", "reason", "pnl", "gross", "fees", "fills", "entry_value", "atr", "volratio", "rsi", "bbpos", "duration_bars"]
        }
        output["entry_time"] = datetime.fromtimestamp(trade["entry_time"] / 1000, tz=timezone.utc).isoformat()
        output["exit_time"] = datetime.fromtimestamp(trade["exit_time"] / 1000, tz=timezone.utc).isoformat()
        print(rounded(output))


if __name__ == "__main__":
    asyncio.run(main())
