"""Strategy definitions for the MVP."""

from dataclasses import dataclass

from market import analyze_indicators, get_orderbook, get_price


@dataclass(frozen=True)
class Strategy:
    code: str
    name: str
    description: str
    timeframe: str = "15"


STRATEGIES = {
    "grid_dca_v2": Strategy(
        code="grid_dca_v2",
        name="GRID DCA 2.9",
        description="Market-stage GRID DCA strategy with RSI 15m/1h filters, ATR-adaptive averaging, BTC/ETH market guard, stage-dependent take profit, wider stop loss, and deal limits.",
    ),
}


GRID_PRESETS = {
    "range": {
        "dca_max": 4,
        "dca_active": 3,
        "dca_multiplier_volume": "1.15",
        "dca_multiplier_price": "1.05",
        "stop_delay": 3,
        "atr_step_mult": 0.85,
        "min_step": 0.45,
        "max_step": 1.8,
        "min_tp": 0.35,
        "max_tp": 0.75,
        "tp_multiplier": 1.0,
        "min_stop": 3.0,
        "max_stop": 6.0,
        "sl_multiplier": 1.3,
    },
    "trend": {
        "dca_max": 3,
        "dca_active": 2,
        "dca_multiplier_volume": "1.2",
        "dca_multiplier_price": "1.15",
        "stop_delay": 3,
        "atr_step_mult": 1.1,
        "min_step": 0.75,
        "max_step": 2.4,
        "min_tp": 0.45,
        "max_tp": 1.0,
        "tp_multiplier": 1.15,
        "min_stop": 3.0,
        "max_stop": 6.5,
        "sl_multiplier": 1.3,
    },
    "pullback": {
        "dca_max": 5,
        "dca_active": 3,
        "dca_multiplier_volume": "1.2",
        "dca_multiplier_price": "1.1",
        "stop_delay": 3,
        "atr_step_mult": 0.75,
        "min_step": 0.55,
        "max_step": 2.0,
        "min_tp": 0.4,
        "max_tp": 0.85,
        "tp_multiplier": 1.2,
        "min_stop": 3.5,
        "max_stop": 6.5,
        "sl_multiplier": 1.3,
    },
}


async def analyze_pair(pair: str, strategy_code: str = "grid_dca_v2") -> dict:
    return await _analyze_grid_dca(pair)


async def _analyze_grid_dca(pair: str) -> dict:
    price = await get_price(pair)
    indicators_5m = await analyze_indicators(pair, interval="5")
    indicators_15m = await analyze_indicators(pair, interval="15")
    indicators_60m = await analyze_indicators(pair, interval="60")
    orderbook = await get_orderbook(pair)

    ind_5m = indicators_5m.get("indicators", {})
    ind_15m = indicators_15m.get("indicators", {})
    ind_60m = indicators_60m.get("indicators", {})
    signals_5m = indicators_5m.get("signals", {})
    signals_15m = indicators_15m.get("signals", {})
    summary_15m = indicators_15m.get("summary", {})
    summary_60m = indicators_60m.get("summary", {})
    levels_15m = indicators_15m.get("levels", {})

    last_price = float(price.get("price") or indicators_15m.get("price") or 0)
    rsi_5m = float(ind_5m.get("rsi") or 50)
    rsi_15m = float(ind_15m.get("rsi") or 50)
    bb_15m = ind_15m.get("bollinger", {})
    bb_position = float(bb_15m.get("position") or 50)
    bb_width = float(bb_15m.get("width_pct") or 0)
    atr = float(ind_15m.get("atr") or 0)
    atr_pct = (atr / last_price * 100) if last_price else 0
    volume = ind_15m.get("volume", {})
    volume_ratio = float(volume.get("ratio") or 1)
    spread_pct = float(orderbook.get("spread_pct") or 99)

    trend_15m = _trend_from_indicators(ind_15m, signals_15m)
    trend_60m = _trend_from_indicators(ind_60m, indicators_60m.get("signals", {}))
    market_stage = _classify_market_stage(
        trend_15m=trend_15m,
        trend_60m=trend_60m,
        bb_width=bb_width,
        atr_pct=atr_pct,
        volume_ratio=volume_ratio,
        spread_pct=spread_pct,
    )

    reasons: list[str] = [
        f"стадия рынка: {_stage_label(market_stage)}",
        f"тренд 15м: {_trend_label(trend_15m)}",
        f"тренд 60м: {_trend_label(trend_60m)}",
    ]
    side = "wait"
    confidence = 0.0
    preset_name = "range"

    tradable, block_reason = _tradability_filter(spread_pct, atr_pct, volume_ratio)
    if not tradable:
        reasons.append(block_reason)
    elif market_stage == "uptrend":
        reasons.append("лонг в тренде отключён до отката")
    elif market_stage == "downtrend":
        if 38 <= rsi_15m <= 56 and rsi_5m > 34 and bb_position >= 42 and volume_ratio >= 0.8:
            side = "short"
            confidence = 0.74
            preset_name = "trend"
            reasons += ["продолжение нисходящего тренда", "вход после отката", "RSI не перепродан"]
    elif market_stage == "range":
        if bb_position >= 75 and rsi_15m >= 58 and rsi_5m >= 48:
            side = "short"
            confidence = 0.68
            preset_name = "range"
            reasons += ["верхняя граница боковика", "возврат к среднему в шорт"]
    elif market_stage == "pullback_up":
        if bb_position <= 55 and 38 <= rsi_15m <= 58:
            side = "long"
            confidence = 0.71
            preset_name = "pullback"
            reasons += ["откат в восходящем тренде", "сетка может усредняться у поддержки"]
    elif market_stage == "pullback_down":
        if bb_position >= 50 and 44 <= rsi_15m <= 60 and rsi_5m >= 40:
            side = "short"
            confidence = 0.71
            preset_name = "pullback"
            reasons += ["откат в нисходящем тренде", "сетка может усредняться у сопротивления"]

    grid = _grid_for_stage(preset_name, atr_pct)
    if side == "wait":
        reasons.append("нет условий для сетки DCA")
    else:
        reasons += [
            f"шаг сетки по ATR: {grid['dca_percent']}%",
            f"тейк-профит: {grid['take_profit']}%",
            f"стоп-лосс: {grid['stop_loss']}%",
        ]

    return {
        "pair": pair.upper(),
        "side": side,
        "confidence": confidence,
        "price": last_price,
        "market_stage": market_stage,
        "grid": grid,
        "reasons": reasons,
        "snapshot": {
            "summary_15m": summary_15m,
            "summary_60m": summary_60m,
            "rsi_5m": rsi_5m,
            "rsi_15m": rsi_15m,
            "bb_position": bb_position,
            "bb_width": bb_width,
            "atr_pct": round(atr_pct, 3),
            "signals_5m": signals_5m,
            "signals_15m": signals_15m,
            "levels_15m": levels_15m,
            "spread_pct": orderbook.get("spread_pct"),
            "volume_ratio": volume_ratio,
        },
    }




def _trend_from_indicators(indicators: dict, signals: dict) -> str:
    ema9 = float(indicators.get("ema9") or 0)
    ema21 = float(indicators.get("ema21") or 0)
    ema50 = float(indicators.get("ema50") or 0)
    macd = signals.get("macd")
    vwap = signals.get("vwap")

    bullish_score = 0
    bearish_score = 0
    if ema9 > ema21 > ema50:
        bullish_score += 2
    elif ema9 < ema21 < ema50:
        bearish_score += 2
    if macd == "BULLISH":
        bullish_score += 1
    elif macd == "BEARISH":
        bearish_score += 1
    if vwap == "BULLISH":
        bullish_score += 1
    elif vwap == "BEARISH":
        bearish_score += 1

    if bullish_score >= 3:
        return "bullish"
    if bearish_score >= 3:
        return "bearish"
    return "neutral"


def _classify_market_stage(
    trend_15m: str,
    trend_60m: str,
    bb_width: float,
    atr_pct: float,
    volume_ratio: float,
    spread_pct: float,
) -> str:
    if spread_pct > 0.08 or atr_pct > 4.0:
        return "unstable"
    if trend_15m == "bullish" and trend_60m == "bullish":
        return "uptrend"
    if trend_15m == "bearish" and trend_60m == "bearish":
        return "downtrend"
    if trend_15m == "neutral" and trend_60m == "bullish":
        return "pullback_up"
    if trend_15m == "neutral" and trend_60m == "bearish":
        return "pullback_down"
    if bb_width <= 3.5 and atr_pct <= 1.6:
        return "range"
    if volume_ratio < 0.6:
        return "quiet"
    return "mixed"


def _tradability_filter(spread_pct: float, atr_pct: float, volume_ratio: float) -> tuple[bool, str]:
    if spread_pct > 0.08:
        return False, "слишком широкий спред"
    if atr_pct > 4.0:
        return False, "волатильность слишком высокая для сетки DCA"
    if volume_ratio < 0.45:
        return False, "слишком низкий объём"
    return True, "можно торговать"






def _stage_label(stage: str) -> str:
    labels = {
        "uptrend": "восходящий тренд",
        "downtrend": "нисходящий тренд",
        "range": "боковик",
        "pullback_up": "откат вверх",
        "pullback_down": "откат вниз",
        "unstable": "нестабильно",
        "quiet": "тихий рынок",
        "mixed": "смешанный рынок",
    }
    return labels.get(stage, stage)


def _trend_label(trend: str) -> str:
    return {"bullish": "бычий", "bearish": "медвежий", "neutral": "нейтральный"}.get(trend, trend)


def _grid_for_stage(stage: str, atr_pct: float) -> dict:
    preset = dict(GRID_PRESETS[stage])
    step = _clamp(atr_pct * float(preset["atr_step_mult"]), float(preset["min_step"]), float(preset["max_step"]))
    take_profit = _clamp(step * 0.55, float(preset["min_tp"]), float(preset["max_tp"]))
    take_profit = min(1.0, take_profit * float(preset.get("tp_multiplier") or 1.0))
    grid_coverage = _grid_coverage(
        step=step,
        active=int(preset["dca_active"]),
        multiplier_price=float(preset["dca_multiplier_price"]),
    )
    stop_loss = _clamp(grid_coverage * 1.25, float(preset["min_stop"]), float(preset["max_stop"]))
    stop_loss *= float(preset.get("sl_multiplier") or 1.0)

    return {
        "dca_max": preset["dca_max"],
        "dca_active": preset["dca_active"],
        "dca_percent": _fmt_pct(step),
        "dca_multiplier_volume": preset["dca_multiplier_volume"],
        "dca_multiplier_price": preset["dca_multiplier_price"],
        "take_profit": _fmt_pct(take_profit),
        "stop_loss": _fmt_pct(stop_loss),
        "stop_delay": preset["stop_delay"],
        "atr_pct": round(atr_pct, 3),
        "grid_coverage_pct": _fmt_pct(grid_coverage),
    }


def _grid_coverage(step: float, active: int, multiplier_price: float) -> float:
    coverage = 0.0
    leg_step = step
    for _ in range(max(active, 1)):
        coverage += leg_step
        leg_step *= multiplier_price
    return coverage


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def _fmt_pct(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")
