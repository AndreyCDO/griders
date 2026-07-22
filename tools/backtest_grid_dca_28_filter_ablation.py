"""Ablation backtest for individual GRID DCA 2.8 research filters.

Research-only script. It does not change production Pine, server logic,
strategy settings, or public reports.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
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
from tools.backtest_grid_dca_28_pine_research_filters import (
    BASELINE_JSON,
    COIN_MOVE3_GUARD_PCT,
    TP_MULTIPLIER_BY_STAGE,
    _tf_close_series,
    base_grid,
    baseline_rows,
    compact,
    comparison,
)


OUT_JSON = Path(".private_reports/grid-dca-28-filter-ablation.json")


@dataclass(frozen=True)
class Variant:
    code: str
    title: str
    coin_trend_filter: bool = False
    stricter_shorts: bool = False
    impulse_guard: bool = False
    stage_tp: bool = False


VARIANTS = [
    Variant(
        code="coin_1h_4h_ema20_trend_only",
        title="Фильтр тренда монеты 1h/4h EMA20",
        coin_trend_filter=True,
    ),
    Variant(
        code="stricter_shorts_only",
        title="Ужесточение условий для шорт-сигналов",
        stricter_shorts=True,
    ),
    Variant(
        code="coin_move3_impulse_guard_only",
        title="3-свечной импульсный защитный фильтр",
        impulse_guard=True,
    ),
    Variant(
        code="stage_dependent_tp_only",
        title="Stage-dependent TP: range 1.00x, trend 1.08x, pullback 1.05x",
        stage_tp=True,
    ),
]


def _grid_for_variant(stage: str, atr_pct: float, variant: Variant) -> dict:
    grid = base._with_dca_max({"stage": stage, "grid": base_grid(stage, atr_pct)})["grid"]
    if variant.stage_tp:
        multiplier = TP_MULTIPLIER_BY_STAGE.get(stage, 1.0)
        grid = {**grid, "tp": min(1.0, float(grid["tp"]) * multiplier)}
    return grid


def _coin_trend(tf: dict, index: int) -> tuple[bool, bool, bool, bool] | None:
    close_1h, ema20_1h = tf["close_1h"][index], tf["ema20_1h"][index]
    close_4h, ema20_4h = tf["close_4h"][index], tf["ema20_4h"][index]
    if close_1h is None or ema20_1h is None or close_4h is None or ema20_4h is None:
        return None
    one_hour_above = close_1h > ema20_1h
    four_hour_above = close_4h > ema20_4h
    return one_hour_above and four_hour_above, (not one_hour_above and not four_hour_above), one_hour_above, four_hour_above


def signal_at_variant(
    variant: Variant,
    ind: dict,
    index: int,
    btc: dict,
    eth: dict,
    rsi60: list[float | None],
    tf: dict,
) -> dict | None:
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

    coin_trend = _coin_trend(tf, index)
    if coin_trend is None:
        return None
    coin_long_trend, coin_short_trend, one_hour_above, four_hour_above = coin_trend
    if not variant.coin_trend_filter:
        coin_long_trend = True
        coin_short_trend = True

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
        or (variant.impulse_guard and coin_move3 <= -COIN_MOVE3_GUARD_PCT)
        or (bar_move <= -0.7 and volratio >= 1.4)
        or (close[index] < lower and volratio >= 1.0)
    )
    short_break = (
        bbpos > 100
        or candle >= 0.7
        or (variant.impulse_guard and coin_move3 >= COIN_MOVE3_GUARD_PCT)
        or (bar_move >= 0.7 and volratio >= 1.4)
        or (close[index] > upper and volratio >= 1.0)
    )

    pull_up = (
        bull
        and coin_long_trend
        and 42 <= bbpos <= 58
        and 42 <= rsi15 <= 58
        and 42 <= rsi1h <= 68
        and not long_break
        and not macro_long
    )
    if variant.stricter_shorts:
        pull_down = bear and coin_short_trend and 48 <= bbpos <= 72 and 40 <= rsi15 <= 56 and 34 <= rsi1h <= 56 and not short_break and not macro_short
        range_short = range_market and coin_short_trend and 80 <= bbpos <= 95 and 60 <= rsi15 <= 72 and 42 <= rsi1h <= 60 and not short_break and not macro_short
        trend_short = bear and coin_short_trend and 40 <= rsi15 <= 54 and 42 <= rsi1h <= 55 and 48 <= bbpos <= 72 and not short_break and not macro_short
    else:
        pull_down = bear and coin_short_trend and 45 <= bbpos <= 78 and 38 <= rsi15 <= 60 and 30 <= rsi1h <= 60 and not short_break and not macro_short
        range_short = range_market and coin_short_trend and bbpos >= 75 and rsi15 >= 58 and rsi1h <= 65 and not short_break and not macro_short
        trend_short = bear and coin_short_trend and 38 <= rsi15 <= 56 and rsi1h <= 58 and 42 <= bbpos <= 78 and not short_break and not macro_short

    tradable = atr_pct <= max_atr and volratio >= min_volume
    side = "long" if tradable and pull_up else "short" if tradable and (range_short or trend_short or pull_down) else None
    if not side:
        return None
    stage = "pullback" if pull_up or pull_down else "trend" if bull or bear else "range"
    return {
        "side": side,
        "stage": stage,
        "grid": _grid_for_variant(stage, atr_pct, variant),
        "atr": atr_pct,
        "volratio": volratio,
        "rsi15": rsi15,
        "rsi60": rsi1h,
        "bbpos": bbpos,
        "bbwidth": width,
        "coin_move3": coin_move3,
        "coin_1h_above_ema20": one_hour_above,
        "coin_4h_above_ema20": four_hour_above,
    }


def candidates_for_variant(variant: Variant, start: datetime, all_rows: dict[str, list]) -> tuple[list[dict], dict]:
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
            signal = signal_at_variant(variant, ind, index, btc, eth, rsi60, tf)
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


def run_variant(variant: Variant, start: datetime, all_rows: dict[str, list]) -> dict:
    candidates, candidate_skipped = candidates_for_variant(variant, start, all_rows)
    original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
    base.SIDE_WEBHOOK_COOLDOWN_MS = 0
    try:
        results = [base.run_portfolio(tariff, candidates, all_rows) for tariff in all_tariffs()]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown
    variant_rows = [compact(result) for result in results]
    baseline = baseline_rows()
    return {
        "variant": {
            "code": variant.code,
            "title": variant.title,
            "coin_trend_filter": variant.coin_trend_filter,
            "stricter_shorts": variant.stricter_shorts,
            "impulse_guard": variant.impulse_guard,
            "stage_tp": variant.stage_tp,
        },
        "signal_candidates": len(candidates),
        "candidate_skipped": candidate_skipped,
        "results": variant_rows,
        "comparison": comparison(baseline, variant_rows),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    start, end, rows = await base.fetch_all(args.days, end)
    baseline = baseline_rows()
    variants = [run_variant(variant, start, rows) for variant in VARIANTS]
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days},
        "baseline_source": str(BASELINE_JSON),
        "baseline": baseline,
        "variants": variants,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON.resolve()),
        "period": data["period"],
        "summary": [
            {
                "code": item["variant"]["code"],
                "signal_candidates": item["signal_candidates"],
                "comparison": [
                    {
                        "tariff": row["code"],
                        "trades_delta": row["trades_delta"],
                        "pnl_delta": row["pnl_delta"],
                        "profit_factor_delta": row["profit_factor_delta"],
                        "stops_delta": row["stops_delta"],
                        "max_drawdown_delta": row["max_drawdown_delta"],
                    }
                    for row in item["comparison"]
                ],
            }
            for item in variants
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
