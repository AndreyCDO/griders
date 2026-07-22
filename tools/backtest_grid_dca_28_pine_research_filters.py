"""Research backtest for GRID DCA 2.8 with stricter Pine-side filters.

This script does not change production Pine, server logic, or public reports.

Variant ideas:
- coin's own 1h/4h EMA20 trend filter by direction;
- stricter short entries;
- 3-candle adverse impulse guard on the traded coin;
- stage-dependent TP multiplier.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_26_vs_31 import (
    ATR_LEN,
    BB_LEN,
    BB_MULT,
    EMA_SLOW,
    VOL_LEN,
    ema,
    hourly_rsi_by_15m,
    indicators,
    sma,
    stdev,
)
from tools.build_grid_dca_28_year_all_tariffs_report import all_tariffs


BASELINE_JSON = Path("webapp/static/reports/grid-dca-28-year-all-tariffs.json")
OUT_JSON = Path(".private_reports/grid-dca-28-pine-research-filters.json")

COIN_MOVE3_GUARD_PCT = 1.20
TP_MULTIPLIER_BY_STAGE = {
    "range": 1.00,
    "trend": 1.08,
    "pullback": 1.05,
}


def _tf_close_series(rows: list, tf_minutes: int) -> tuple[list[float | None], list[float | None]]:
    bucket_ms = tf_minutes * 60 * 1000
    closes: list[tuple[int, float]] = []
    current_bucket = None
    current_close = None
    for row in rows:
        bucket = int(row[0]) // bucket_ms
        if current_bucket is None:
            current_bucket = bucket
        if bucket != current_bucket:
            closes.append((current_bucket, float(current_close)))
            current_bucket = bucket
        current_close = float(row[4])
    if current_bucket is not None and current_close is not None:
        closes.append((current_bucket, float(current_close)))

    ema20_values = ema([close for _bucket, close in closes], 20)
    by_bucket = {
        bucket: {"close": close, "ema20": ema20_value}
        for (bucket, close), ema20_value in zip(closes, ema20_values)
    }
    aligned_close: list[float | None] = []
    aligned_ema20: list[float | None] = []
    for row in rows:
        bucket = int(row[0]) // bucket_ms
        previous = by_bucket.get(bucket - 1)
        aligned_close.append(previous["close"] if previous else None)
        aligned_ema20.append(previous["ema20"] if previous else None)
    return aligned_close, aligned_ema20


def _grid_for_research(stage: str, atr_pct: float) -> dict:
    grid = base._with_dca_max({"stage": stage, "grid": base_grid(stage, atr_pct)})["grid"]
    multiplier = TP_MULTIPLIER_BY_STAGE.get(stage, 1.0)
    grid = {**grid, "tp": min(1.0, float(grid["tp"]) * multiplier)}
    return grid


def base_grid(stage: str, atr_pct: float) -> dict:
    if stage == "range":
        dca_active, mult_vol, mult_price = 3, 1.15, 1.05
        step = max(0.45, min(1.8, atr_pct * 0.85))
        take_profit = max(0.35, min(0.75, step * 0.55))
        stop_loss = max(3.0, min(6.0, step * 4.0))
    elif stage == "trend":
        dca_active, mult_vol, mult_price = 2, 1.2, 1.15
        step = max(0.75, min(2.4, atr_pct * 1.1))
        take_profit = max(0.45, min(1.0, step * 0.55))
        stop_loss = max(3.0, min(6.5, step * 4.0))
    else:
        dca_active, mult_vol, mult_price = 3, 1.2, 1.1
        step = max(0.55, min(2.0, atr_pct * 0.75))
        take_profit = max(0.4, min(0.85, step * 0.55))
        stop_loss = max(3.5, min(6.5, step * 4.0))
    return {
        "dca_active": dca_active,
        "mult_vol": mult_vol,
        "mult_price": mult_price,
        "step": step,
        "tp": take_profit,
        "sl": stop_loss,
    }


def research_signal_at(ind: dict, index: int, btc: dict, eth: dict, rsi60: list[float | None], tf: dict) -> dict | None:
    close = ind["c"]
    if index < max(EMA_SLOW, BB_LEN, VOL_LEN, ATR_LEN, 16):
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
    coin_move3 = (close[index] - close[index - 3]) / close[index - 3] * 100 if close[index - 3] else 0
    rsi15 = float(ind["rsi15"][index])
    rsi1h = float(rsi60[index])

    btc1 = (btc["c"][index] - btc["c"][index - 1]) / btc["c"][index - 1] * 100 if btc["c"][index - 1] else 0
    btc3 = (btc["c"][index] - btc["c"][index - 3]) / btc["c"][index - 3] * 100 if btc["c"][index - 3] else 0
    eth1 = (eth["c"][index] - eth["c"][index - 1]) / eth["c"][index - 1] * 100 if eth["c"][index - 1] else 0
    eth3 = (eth["c"][index] - eth["c"][index - 3]) / eth["c"][index - 3] * 100 if eth["c"][index - 3] else 0

    close_1h, ema20_1h = tf["close_1h"][index], tf["ema20_1h"][index]
    close_4h, ema20_4h = tf["close_4h"][index], tf["ema20_4h"][index]
    if close_1h is None or ema20_1h is None or close_4h is None or ema20_4h is None:
        return None
    coin_long_trend = close_1h > ema20_1h and close_4h > ema20_4h
    coin_short_trend = close_1h < ema20_1h and close_4h < ema20_4h

    max_bb_width = 3.5
    min_volume = 0.45
    max_atr = 4.0
    macro_1 = 0.8
    macro_3 = 1.2
    range_market = not bull and not bear and width <= max_bb_width
    macro_long = btc1 <= -macro_1 or eth1 <= -macro_1 or btc3 <= -macro_3 or eth3 <= -macro_3
    macro_short = btc1 >= macro_1 or eth1 >= macro_1 or btc3 >= macro_3 or eth3 >= macro_3
    long_break = (
        bbpos < 0
        or candle <= -0.7
        or coin_move3 <= -COIN_MOVE3_GUARD_PCT
        or (bar_move <= -0.7 and volratio >= 1.4)
        or (close[index] < lower and volratio >= 1.0)
    )
    short_break = (
        bbpos > 100
        or candle >= 0.7
        or coin_move3 >= COIN_MOVE3_GUARD_PCT
        or (bar_move >= 0.7 and volratio >= 1.4)
        or (close[index] > upper and volratio >= 1.0)
    )

    pull_up = bull and coin_long_trend and 42 <= bbpos <= 58 and 42 <= rsi15 <= 58 and 42 <= rsi1h <= 68 and not long_break and not macro_long
    pull_down = bear and coin_short_trend and 48 <= bbpos <= 72 and 40 <= rsi15 <= 56 and 34 <= rsi1h <= 56 and not short_break and not macro_short
    tradable = atr_pct <= max_atr and volratio >= min_volume
    range_short = range_market and coin_short_trend and 80 <= bbpos <= 95 and 60 <= rsi15 <= 72 and 42 <= rsi1h <= 60 and not short_break and not macro_short
    trend_short = bear and coin_short_trend and 40 <= rsi15 <= 54 and 42 <= rsi1h <= 55 and 48 <= bbpos <= 72 and not short_break and not macro_short

    side = "long" if tradable and pull_up else "short" if tradable and (range_short or trend_short or pull_down) else None
    if not side:
        return None
    stage = "pullback" if pull_up or pull_down else "trend" if bull or bear else "range"
    return {
        "side": side,
        "stage": stage,
        "grid": _grid_for_research(stage, atr_pct),
        "atr": atr_pct,
        "volratio": volratio,
        "rsi15": rsi15,
        "rsi60": rsi1h,
        "bbpos": bbpos,
        "bbwidth": width,
        "coin_move3": coin_move3,
        "coin_1h_above_ema20": close_1h > ema20_1h,
        "coin_4h_above_ema20": close_4h > ema20_4h,
    }


def research_candidates(start: datetime, all_rows: dict[str, list]) -> tuple[list[dict], dict]:
    btc = indicators(all_rows["BTCUSDT"])
    eth = indicators(all_rows["ETHUSDT"])
    trend_context = base._daily_trend_context(all_rows["BTCUSDT"], all_rows["ETHUSDT"])
    start_ms = int(start.timestamp() * 1000)
    candidates: list[dict] = []
    skipped = {
        "tradingview_daily_regime_long": 0,
        "tradingview_daily_regime_short": 0,
        "server_ema20_long": 0,
        "server_ema20_short": 0,
    }
    for symbol in base.ALL_PAIRS:
        rows = all_rows[symbol]
        ind = indicators(rows)
        rsi60 = hourly_rsi_by_15m(rows)
        close_1h, ema20_1h = _tf_close_series(rows, 60)
        close_4h, ema20_4h = _tf_close_series(rows, 240)
        tf = {
            "close_1h": close_1h,
            "ema20_1h": ema20_1h,
            "close_4h": close_4h,
            "ema20_4h": ema20_4h,
        }
        for index in range(max(EMA_SLOW, VOL_LEN, BB_LEN, ATR_LEN, 16), len(rows) - 1):
            signal = research_signal_at(ind, index, btc, eth, rsi60, tf)
            if not signal:
                continue
            trend = base._trend_for_bar(trend_context, int(rows[index][0]))
            btc_above = bool(trend.get("btc_daily_above_ema20", True))
            eth_above = bool(trend.get("eth_daily_above_ema20", True))
            side = signal["side"]
            if side == "long" and trend.get("regime") == "downtrend":
                skipped["tradingview_daily_regime_long"] += 1
                continue
            if side == "short" and trend.get("regime") == "uptrend":
                skipped["tradingview_daily_regime_short"] += 1
                continue
            if side == "long" and not btc_above and not eth_above:
                skipped["server_ema20_long"] += 1
                continue
            if side == "short" and btc_above and eth_above:
                skipped["server_ema20_short"] += 1
                continue
            signal = base._with_dca_max(signal)
            entry_index = index + 1
            entry_time = int(rows[entry_index][0])
            if entry_time < start_ms:
                continue
            candidates.append({
                "symbol": symbol,
                "side": side,
                "stage": signal["stage"],
                "grid": signal["grid"],
                "entry_index": entry_index,
                "entry_time": entry_time,
                "atr": signal["atr"],
                "volratio": signal["volratio"],
                "rsi15": signal["rsi15"],
                "rsi60": signal["rsi60"],
                "bbpos": signal["bbpos"],
                "bbwidth": signal["bbwidth"],
                "coin_move3": signal["coin_move3"],
                "coin_1h_above_ema20": signal["coin_1h_above_ema20"],
                "coin_4h_above_ema20": signal["coin_4h_above_ema20"],
                "global_market_regime": trend.get("regime", "neutral"),
                "btc_daily_move_3": trend.get("btc_daily_move_3"),
                "eth_daily_move_3": trend.get("eth_daily_move_3"),
                "global_daily_move_3": trend.get("global_daily_move_3"),
                "btc_daily_above_ema20": btc_above,
                "eth_daily_above_ema20": eth_above,
            })
    return sorted(candidates, key=lambda item: (item["entry_time"], item["symbol"], item["side"])), skipped


def compact(result: dict) -> dict:
    m = base.metric(result)
    return {
        "code": result["tariff"].code,
        "name": result["tariff"].name,
        "trades": m["trades"],
        "pnl": m["pnl"],
        "return_pct": m["return_pct"],
        "profit_factor": m["profit_factor"],
        "stops": m["stops"],
        "win_rate": m["win_rate"],
        "max_drawdown": m["max_drawdown"],
        "max_drawdown_pct": m["max_drawdown_pct"],
        "avg_first_order": m["avg_first_order"],
        "skipped": m["skipped"],
    }


def baseline_rows() -> list[dict]:
    data = json.loads(BASELINE_JSON.read_text(encoding="utf-8-sig"))
    rows = []
    for tariff in data["tariffs"]:
        m = tariff["metrics"]
        rows.append({
            "code": tariff["code"],
            "name": tariff["name"],
            "trades": m["trades"],
            "pnl": m["pnl"],
            "return_pct": m["return_pct"],
            "profit_factor": m["profit_factor"],
            "stops": m["stops"],
            "win_rate": m["win_rate"],
            "max_drawdown": m["max_drawdown"],
            "max_drawdown_pct": m["max_drawdown_pct"],
        })
    return rows


def comparison(baseline: list[dict], variant: list[dict]) -> list[dict]:
    old_by_code = {row["code"]: row for row in baseline}
    rows = []
    for row in variant:
        old = old_by_code[row["code"]]
        rows.append({
            "code": row["code"],
            "name": row["name"],
            "trades": row["trades"],
            "trades_delta": row["trades"] - old["trades"],
            "pnl": row["pnl"],
            "pnl_delta": row["pnl"] - old["pnl"],
            "return_pct": row["return_pct"],
            "return_pct_delta": row["return_pct"] - old["return_pct"],
            "profit_factor": row["profit_factor"],
            "profit_factor_delta": row["profit_factor"] - old["profit_factor"],
            "stops": row["stops"],
            "stops_delta": row["stops"] - old["stops"],
            "win_rate": row["win_rate"],
            "win_rate_delta": row["win_rate"] - old["win_rate"],
            "max_drawdown": row["max_drawdown"],
            "max_drawdown_delta": row["max_drawdown"] - old["max_drawdown"],
        })
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    start, end, rows = await base.fetch_all(args.days, end)
    candidates, candidate_skipped = research_candidates(start, rows)
    original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
    base.SIDE_WEBHOOK_COOLDOWN_MS = 0
    try:
        results = [base.run_portfolio(tariff, candidates, rows) for tariff in all_tariffs()]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown

    baseline = baseline_rows()
    variant = [compact(result) for result in results]
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days},
        "variant": {
            "code": "grid_dca_28_pine_research_filters",
            "production_changed": False,
            "coin_trend_filter": "long requires coin 1h and 4h close above EMA20; short requires below EMA20",
            "coin_adverse_move3_guard_pct": COIN_MOVE3_GUARD_PCT,
            "stricter_shorts": True,
            "tp_multiplier_by_stage": TP_MULTIPLIER_BY_STAGE,
            "side_webhook_cooldown_ms": 0,
        },
        "signal_candidates": len(candidates),
        "candidate_skipped": candidate_skipped,
        "baseline": baseline,
        "variant_results": variant,
        "comparison": comparison(baseline, variant),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON.resolve()),
        "period": data["period"],
        "variant": data["variant"],
        "signal_candidates": data["signal_candidates"],
        "comparison": data["comparison"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
