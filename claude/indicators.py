"""
indicators.py — технические индикаторы на чистом Python.
Без pandas/numpy — быстро, без зависимостей.

Индикаторы: EMA, RSI, MACD, Bollinger Bands, Stochastic %K/%D,
            VWAP, ATR, уровни поддержки/сопротивления, сводный сигнал.
"""

import math
from typing import TypedDict


# ── Типы ─────────────────────────────────────────────────────────────────────

class Candle(TypedDict):
    time:     int
    open:     float
    high:     float
    low:      float
    close:    float
    volume:   float


class MACDResult(TypedDict):
    macd:      float
    signal:    float
    histogram: float
    trend:     str


class BBResult(TypedDict):
    upper:     float
    middle:    float
    lower:     float
    width_pct: float
    position:  float   # 0=у нижней границы, 100=у верхней


class StochResult(TypedDict):
    k:      float
    d:      float
    signal: str        # OVERSOLD / OVERBOUGHT / NEUTRAL


class SRResult(TypedDict):
    resistance:   float
    support:      float
    dist_to_res:  float
    dist_to_sup:  float


class IndicatorSummary(TypedDict):
    verdict:    str    # STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
    strength:   str
    buy_count:  int
    sell_count: int


# ── Базовые функции ───────────────────────────────────────────────────────────

def ema(values: list[float], period: int) -> list[float]:
    """Экспоненциальная скользящая средняя."""
    if not values:
        return []
    k = 2 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def sma(values: list[float], period: int) -> float:
    """Простая скользящая средняя за последние period значений."""
    if len(values) < period:
        return values[-1] if values else 0.0
    return sum(values[-period:]) / period


# ── Индикаторы ────────────────────────────────────────────────────────────────

def rsi(closes: list[float], period: int = 14) -> float:
    """RSI — индекс относительной силы."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas[-period:]]
    losses = [abs(min(d, 0)) for d in deltas[-period:]]
    avg_g  = sum(gains) / period
    avg_l  = sum(losses) / period
    if avg_l == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_g / avg_l)), 2)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal_period: int = 9) -> MACDResult:
    """MACD с сигнальной линией и гистограммой."""
    if len(closes) < slow:
        return MACDResult(macd=0.0, signal=0.0, histogram=0.0, trend="NEUTRAL")
    ema_fast   = ema(closes, fast)
    ema_slow   = ema(closes, slow)
    macd_line  = [f - s for f, s in zip(ema_fast[slow - fast:], ema_slow)]
    signal_line = ema(macd_line, signal_period)
    hist       = macd_line[-1] - signal_line[-1]
    return MACDResult(
        macd=round(macd_line[-1], 6),
        signal=round(signal_line[-1], 6),
        histogram=round(hist, 6),
        trend="BULLISH" if hist > 0 else "BEARISH",
    )


def bollinger(closes: list[float], period: int = 20, mult: float = 2.0) -> BBResult:
    """Полосы Боллинджера."""
    if len(closes) < period:
        c = closes[-1] if closes else 0.0
        return BBResult(upper=c, middle=c, lower=c, width_pct=0.0, position=50.0)
    window = closes[-period:]
    mid    = sum(window) / period
    std    = math.sqrt(sum((x - mid) ** 2 for x in window) / period)
    upper  = mid + mult * std
    lower  = mid - mult * std
    price  = closes[-1]
    pos    = round((price - lower) / (upper - lower) * 100, 1) if upper != lower else 50.0
    return BBResult(
        upper=round(upper, 6),
        middle=round(mid, 6),
        lower=round(lower, 6),
        width_pct=round((mult * 2 * std / mid) * 100, 2) if mid else 0.0,
        position=pos,
    )


def stochastic(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
    smooth: int = 3,
) -> StochResult:
    """Стохастик %K/%D."""
    if len(closes) < period:
        return StochResult(k=50.0, d=50.0, signal="NEUTRAL")

    def _k(offset: int = 0) -> float:
        idx = len(closes) - offset
        h   = max(highs[idx - period : idx])
        l   = min(lows[idx - period : idx])
        c   = closes[idx - 1]
        return ((c - l) / (h - l) * 100) if h != l else 50.0

    k_val = round(_k(), 2)
    ks    = [_k(i) for i in range(smooth) if len(closes) - i >= period]
    d_val = round(sum(ks) / len(ks) if ks else k_val, 2)
    sig   = "OVERSOLD" if k_val < 20 else ("OVERBOUGHT" if k_val > 80 else "NEUTRAL")
    return StochResult(k=k_val, d=d_val, signal=sig)


def vwap(candles: list[Candle]) -> float:
    """VWAP (Volume Weighted Average Price) — дневной."""
    if not candles:
        return 0.0
    total_pv = sum((c["high"] + c["low"] + c["close"]) / 3 * c["volume"] for c in candles)
    total_v  = sum(c["volume"] for c in candles)
    return round(total_pv / total_v, 6) if total_v else 0.0


def atr(candles: list[Candle], period: int = 14) -> float:
    """ATR — средний истинный диапазон (волатильность)."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return round(sum(trs[-period:]) / period, 6)


def support_resistance(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    lookback: int = 50,
) -> SRResult:
    """Ключевые уровни поддержки и сопротивления (простой метод экстремумов)."""
    h     = highs[-lookback:] if len(highs) >= lookback else highs
    l     = lows[-lookback:]  if len(lows)  >= lookback else lows
    price = closes[-1]
    res   = max(h)
    sup   = min(l)
    return SRResult(
        resistance=round(res, 6),
        support=round(sup, 6),
        dist_to_res=round((res - price) / price * 100, 3),
        dist_to_sup=round((price - sup) / price * 100, 3),
    )


def volume_analysis(candles: list[Candle], lookback: int = 20) -> dict:
    """Анализ объёма относительно среднего."""
    if not candles:
        return {"ratio": 1.0, "signal": "NORMAL", "trend": "FLAT"}
    avg_vol = sum(c["volume"] for c in candles[-lookback:]) / min(lookback, len(candles))
    cur_vol = candles[-1]["volume"]
    ratio   = round(cur_vol / avg_vol, 2) if avg_vol else 1.0
    signal  = "HIGH" if ratio > 1.5 else ("LOW" if ratio < 0.5 else "NORMAL")

    # Тренд объёма: сравниваем две половины
    half = max(len(candles) // 2, 1)
    avg_first  = sum(c["volume"] for c in candles[:half]) / half
    avg_second = sum(c["volume"] for c in candles[half:]) / max(len(candles) - half, 1)
    vol_trend  = "INCREASING" if avg_second > avg_first * 1.1 else ("DECREASING" if avg_second < avg_first * 0.9 else "FLAT")

    return {"ratio": ratio, "signal": signal, "trend": vol_trend}


# ── Сводный сигнал ────────────────────────────────────────────────────────────

_BUY_WORDS  = {"BUY", "BULLISH", "OVERSOLD"}
_SELL_WORDS = {"SELL", "BEARISH", "OVERBOUGHT"}


def summary(signals: dict[str, str]) -> IndicatorSummary:
    """Агрегирует отдельные сигналы в сводный вердикт."""
    buy  = sum(1 for s in signals.values() if s in _BUY_WORDS)
    sell = sum(1 for s in signals.values() if s in _SELL_WORDS)
    n    = len(signals)

    if buy >= 5:
        verdict = "STRONG_BUY"
    elif buy >= 4:
        verdict = "BUY"
    elif sell >= 5:
        verdict = "STRONG_SELL"
    elif sell >= 4:
        verdict = "SELL"
    else:
        verdict = "NEUTRAL"

    return IndicatorSummary(
        verdict=verdict,
        strength=f"{max(buy, sell)}/{n}",
        buy_count=buy,
        sell_count=sell,
    )


def full_analysis(candles: list[Candle]) -> dict:
    """
    Полный технический анализ по списку свечей.
    Возвращает все индикаторы + сигналы + сводный вердикт.
    """
    if len(candles) < 30:
        return {"error": f"Недостаточно свечей для анализа ({len(candles)} < 30)"}

    closes = [c["close"]  for c in candles]
    highs  = [c["high"]   for c in candles]
    lows   = [c["low"]    for c in candles]
    price  = closes[-1]

    rsi_val   = rsi(closes)
    macd_val  = macd(closes)
    bb_val    = bollinger(closes)
    stoch_val = stochastic(highs, lows, closes)
    vwap_val  = vwap(candles)
    atr_val   = atr(candles)
    sr_val    = support_resistance(highs, lows, closes)
    vol_val   = volume_analysis(candles)

    ema9_vals  = ema(closes, 9)
    ema21_vals = ema(closes, 21)
    ema50_vals = ema(closes, 50)

    signals: dict[str, str] = {
        "rsi":       "OVERSOLD"   if rsi_val < 30  else ("OVERBOUGHT" if rsi_val > 70  else "NEUTRAL"),
        "macd":      "BULLISH"    if macd_val["histogram"] > 0 else "BEARISH",
        "bb":        "BUY"        if price < bb_val["lower"]   else ("SELL" if price > bb_val["upper"] else "NEUTRAL"),
        "stoch":     stoch_val["signal"],
        "ema_cross": "BULLISH"    if ema9_vals[-1] > ema21_vals[-1] else "BEARISH",
        "vwap":      "BULLISH"    if price > vwap_val else "BEARISH",
    }

    sum_val = summary(signals)

    # Пригодность для скальпинга
    est_spread  = price * 0.0002
    scalp_ok    = atr_val > est_spread * 3 and vol_val["ratio"] > 1.0

    return {
        "price":   price,
        "indicators": {
            "rsi":        rsi_val,
            "macd":       dict(macd_val),
            "bollinger":  dict(bb_val),
            "stochastic": dict(stoch_val),
            "vwap":       vwap_val,
            "atr":        atr_val,
            "ema9":       round(ema9_vals[-1],  4),
            "ema21":      round(ema21_vals[-1], 4),
            "ema50":      round(ema50_vals[-1], 4),
            "volume":     vol_val,
        },
        "levels":  dict(sr_val),
        "signals": signals,
        "summary": dict(sum_val),
        "scalping": {
            "ok":   scalp_ok,
            "note": (
                f"ATR={atr_val:.5f} ({round(atr_val / price * 100, 3)}%). "
                f"Объём: {vol_val['ratio']}x от среднего. "
                + ("✅ Условия хорошие." if scalp_ok else "⚠️ Слабая ликвидность или малый ATR.")
            ),
        },
    }
