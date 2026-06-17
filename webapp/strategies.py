"""Strategy definitions for the MVP."""

from dataclasses import dataclass

from market import analyze_indicators, get_candles, get_orderbook, get_price, get_recent_trades


@dataclass(frozen=True)
class Strategy:
    code: str
    name: str
    description: str
    timeframe: str = "15"


STRATEGIES = {
    "grid_dca_v2": Strategy(
        code="grid_dca_v2",
        name="GRID DCA 2.6",
        description="Market-stage GRID DCA strategy with RSI 15m/1h filters, ATR-adaptive averaging, BTC/ETH market guard, take profit, stop loss, and deal limits.",
    ),
    "grid_dca_v3": Strategy(
        code="grid_dca_v3",
        name="GRID DCA 3.1",
        description="Admin-only stricter GRID DCA strategy: tighter RSI 15m/1h and market filters, stronger BTC/ETH guard, ATR-adaptive DCA, take profit, stop loss, and deal limits.",
    ),
    "market_shock_impulse_v1": Strategy(
        code="market_shock_impulse_v1",
        name="MarketShok Impulse 2.0",
        description="Breakout strategy inspired by Cryptorg Market Shock: it looks for abnormal futures impulses, volume expansion, and continuation confirmation.",
        timeframe="1",
    ),
    "market_shock_reversal_dca_v21": Strategy(
        code="market_shock_reversal_dca_v21",
        name="MarketShok Reversal DCA 3.0",
        description="Admin-only experimental Market Shock short-biased strategy: shorts upward overextensions against the impulse and downward shocks with continuation, using adaptive DCA, TP, SL, and strict deal limits.",
        timeframe="1",
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
        "min_stop": 3.0,
        "max_stop": 6.0,
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
        "min_stop": 3.0,
        "max_stop": 6.5,
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
        "min_stop": 3.5,
        "max_stop": 6.5,
    },
}


async def analyze_pair(pair: str, strategy_code: str = "grid_dca_v2") -> dict:
    if strategy_code in {"market_shock_impulse_v1", "market_shock_reversal_dca_v21"}:
        return await _analyze_market_shock_impulse(pair)
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


async def _analyze_market_shock_impulse(pair: str) -> dict:
    price, candles_1m, indicators_5m, indicators_15m, orderbook, trades = await _gather_impulse_context(pair)
    one_minute = candles_1m.get("candles", [])
    ind_5m = indicators_5m.get("indicators", {})
    ind_15m = indicators_15m.get("indicators", {})
    signals_5m = indicators_5m.get("signals", {})
    signals_15m = indicators_15m.get("signals", {})

    last_price = float(price.get("price") or 0)
    spread_pct = float(orderbook.get("spread_pct") or 99)
    atr = float(ind_5m.get("atr") or ind_15m.get("atr") or 0)
    atr_pct = (atr / last_price * 100) if last_price else 0
    move_3m = _window_move_pct(one_minute, 3)
    move_5m = _window_move_pct(one_minute, 5)
    shock_move = move_3m if abs(move_3m) >= abs(move_5m) else move_5m
    volume_ratio = _recent_volume_ratio(one_minute, window=5, lookback=30)
    direction = "long" if shock_move > 0 else "short"
    trend_5m = _trend_from_indicators(ind_5m, signals_5m)
    trend_15m = _trend_from_indicators(ind_15m, signals_15m)
    aggression = str(trades.get("aggression") or "BALANCED")

    reasons = [
        "стратегия: MarketShok Impulse 2.0",
        f"импульс: {shock_move:.2f}% за 3-5 минут",
        f"объём импульса: x{volume_ratio:.2f}",
        f"тренд 5м: {_trend_label(trend_5m)}",
        f"тренд 15м: {_trend_label(trend_15m)}",
    ]
    side = "wait"
    confidence = 0.0

    tradable, block_reason = _impulse_tradability_filter(
        shock_move=shock_move,
        spread_pct=spread_pct,
        volume_ratio=volume_ratio,
        atr_pct=atr_pct,
        direction=direction,
        trend_5m=trend_5m,
        trend_15m=trend_15m,
        aggression=aggression,
    )
    if not tradable:
        reasons.append(block_reason)
    else:
        side = direction
        confidence = _impulse_confidence(abs(shock_move), volume_ratio, trend_5m, trend_15m, aggression, direction)
        reasons += [
            "подходящий рыночный шок по Market Shock",
            "объём и тренд подтверждают продолжение движения",
            "вход готовится через один Ghost Bot",
        ]

    grid = _impulse_grid(abs(shock_move), atr_pct)
    if side == "wait":
        reasons.append("нет подтверждения для импульсного входа")
    else:
        reasons += [
            f"шаг сетки: {grid['dca_percent']}%",
            f"тейк-профит: {grid['take_profit']}%",
            f"стоп-лосс: {grid['stop_loss']}%",
        ]

    return {
        "pair": pair.upper(),
        "side": side,
        "confidence": confidence,
        "price": last_price,
        "market_stage": "impulse",
        "grid": grid,
        "reasons": reasons,
        "snapshot": {
            "strategy": "market_shock_impulse_v1",
            "shock_move_pct": round(shock_move, 3),
            "volume_ratio": round(volume_ratio, 3),
            "atr_pct": round(atr_pct, 3),
            "trend_5m": trend_5m,
            "trend_15m": trend_15m,
            "spread_pct": spread_pct,
            "aggression": aggression,
        },
    }


async def _gather_impulse_context(pair: str) -> tuple[dict, dict, dict, dict, dict, dict]:
    import asyncio

    return await asyncio.gather(
        get_price(pair),
        get_candles(pair, interval="1", limit=60),
        analyze_indicators(pair, interval="5"),
        analyze_indicators(pair, interval="15"),
        get_orderbook(pair),
        get_recent_trades(pair, limit=80),
    )


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


def _window_move_pct(candles: list[dict], minutes: int) -> float:
    if len(candles) < minutes + 1:
        return 0.0
    start = float(candles[-minutes - 1].get("close") or candles[-minutes - 1].get("open") or 0)
    end = float(candles[-1].get("close") or 0)
    if start <= 0:
        return 0.0
    return (end - start) / start * 100


def _recent_volume_ratio(candles: list[dict], window: int = 5, lookback: int = 30) -> float:
    if len(candles) < window + 2:
        return 1.0
    recent = candles[-window:]
    history = candles[-lookback - window:-window] if len(candles) >= lookback + window else candles[:-window]
    recent_avg = sum(float(item.get("volume") or 0) for item in recent) / max(len(recent), 1)
    history_avg = sum(float(item.get("volume") or 0) for item in history) / max(len(history), 1)
    return recent_avg / history_avg if history_avg > 0 else 1.0


def _impulse_tradability_filter(
    shock_move: float,
    spread_pct: float,
    volume_ratio: float,
    atr_pct: float,
    direction: str,
    trend_5m: str,
    trend_15m: str,
    aggression: str,
) -> tuple[bool, str]:
    impulse_size = abs(shock_move)
    if spread_pct > 0.10:
        return False, "слишком широкий спред для импульсного входа"
    if impulse_size < 3.0:
        return False, "импульс меньше 3%, это ниже рабочего фильтра Market Shock"
    if impulse_size > 9.0:
        return False, "слишком сильный импульс, вход может быть поздним"
    if volume_ratio < 1.8:
        return False, "нет подтверждения объёмом"
    if atr_pct > 7.0:
        return False, "волатильность слишком высокая для импульсного входа"
    if direction == "long" and trend_5m == "bearish" and trend_15m == "bearish":
        return False, "вход против старшего нисходящего тренда"
    if direction == "short" and trend_5m == "bullish" and trend_15m == "bullish":
        return False, "вход против старшего восходящего тренда"
    if direction == "long" and aggression == "SELLERS":
        return False, "лента сделок не подтверждает лонг"
    if direction == "short" and aggression == "BUYERS":
        return False, "лента сделок не подтверждает шорт"
    return True, "импульс подтверждён"


def _impulse_confidence(
    impulse_size: float,
    volume_ratio: float,
    trend_5m: str,
    trend_15m: str,
    aggression: str,
    direction: str,
) -> float:
    score = 0.62
    if impulse_size >= 4.0:
        score += 0.06
    if impulse_size >= 6.0:
        score += 0.05
    if volume_ratio >= 2.5:
        score += 0.06
    if direction == "long" and trend_5m == "bullish":
        score += 0.04
    if direction == "short" and trend_5m == "bearish":
        score += 0.04
    if direction == "long" and trend_15m == "bullish":
        score += 0.03
    if direction == "short" and trend_15m == "bearish":
        score += 0.03
    if (direction == "long" and aggression == "BUYERS") or (direction == "short" and aggression == "SELLERS"):
        score += 0.04
    return round(_clamp(score, 0.0, 0.88), 2)


def _impulse_grid(impulse_size: float, atr_pct: float) -> dict:
    if impulse_size < 5:
        dca_max = 1
        dca_active = 1
        step = _clamp(max(impulse_size * 0.42, atr_pct * 0.9), 1.2, 1.8)
        stop_min, stop_max = 2.6, 3.8
        multiplier_volume = "1.1"
    elif impulse_size < 8:
        dca_max = 2
        dca_active = 2
        step = _clamp(max(impulse_size * 0.36, atr_pct * 0.95), 1.8, 2.6)
        stop_min, stop_max = 3.6, 5.2
        multiplier_volume = "1.12"
    else:
        dca_max = 2
        dca_active = 2
        step = _clamp(max(impulse_size * 0.3, atr_pct), 2.4, 3.2)
        stop_min, stop_max = 5.0, 6.0
        multiplier_volume = "1.1"

    multiplier_price = 1.15
    grid_coverage = _grid_coverage(step, dca_active, multiplier_price)
    take_profit = _clamp(max(impulse_size * 0.34, atr_pct * 0.9), 1.0, 2.6)
    stop_loss = _clamp(max(impulse_size * 0.65, atr_pct * 1.4, grid_coverage * 1.05, take_profit * 1.9), stop_min, stop_max)
    return {
        "dca_max": dca_max,
        "dca_active": dca_active,
        "dca_percent": _fmt_pct(step),
        "dca_multiplier_volume": multiplier_volume,
        "dca_multiplier_price": _fmt_pct(multiplier_price),
        "take_profit": _fmt_pct(take_profit),
        "stop_loss": _fmt_pct(stop_loss),
        "stop_delay": 2,
        "atr_pct": round(atr_pct, 3),
        "grid_coverage_pct": _fmt_pct(grid_coverage),
    }


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
    grid_coverage = _grid_coverage(
        step=step,
        active=int(preset["dca_active"]),
        multiplier_price=float(preset["dca_multiplier_price"]),
    )
    stop_loss = _clamp(grid_coverage * 1.25, float(preset["min_stop"]), float(preset["max_stop"]))

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
