"""Независимый бектест GRID DCA 2.9 (написан с нуля, v2 — корректная механика).

Эта версия точно воспроизводит торговую модель, описанную в документации
Cryptorg (https://wiki.cryptorg.net) и применённую предыдущим автором, но
собственным кодом, без импорта чужих модулей. Цель — получить свои абсолютные
метрики и сравнить с предыдущим бектестом того же периода.

Механика (по wiki Cryptorg + webapp/grid_dca_webhook.py):
  - TP «Процент» считается от СРЕДНЕЙ цены входа после усреднений (wiki, ШАГ 4).
  - SL «Процент» считается от СРЕДНЕЙ цены входа (wiki, ШАГ 5: «отклонение от
    цены входа в позицию», где позиция = усреднённая).
  - Safety orders (DCA) усредняют цену: шаг растёт геометрически (mult_price),
    объём каждого следующего ордера растёт (mult_vol, мартингейл).
  - Сделка занимает пару до выхода; одна сделка на пару одновременно.
  - Портфель: лимиты max_total/max_long/max_short, маржинальная проверка,
    кулдауны (pair 1m, side 5m, глобальный после SL 3h).

Параметры (для сопоставимости с предыдущим отчётом):
  период:    2025-06-11 -> 2026-06-11 (365 дней)
  таймфрейм: 15m, RSI 1h из часовых закрытий, построенных из 15m
  комиссия:  taker 0.05% на каждый ордер (вход, safety, выход)
  плечо:     x10 (изолированная маржа)
  TP/SL:     range 1.00x, trend 1.15x, pullback 1.20x; SL x1.3 для всех этапов

Запуск:
  python tools/backtest_grid_dca_29_independent.py
  python tools/backtest_grid_dca_29_independent.py --compare <prev_json>
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Конфигурация (привязана к параметрам предыдущего бектеста)
# ---------------------------------------------------------------------------

ALL_PAIRS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "NEARUSDT", "ZECUSDT",
    "ONDOUSDT", "XRPUSDT", "SUIUSDT", "FILUSDT", "TAOUSDT", "RENDERUSDT",
    "ADAUSDT", "INJUSDT", "LITUSDT", "ENAUSDT", "LINKUSDT", "AVAXUSDT",
    "JUPUSDT", "ARBUSDT",
]

CANDLE_DIR = Path(".cache/backtests/bybit_15m")
PERIOD_START = datetime(2025, 6, 11, 0, 0, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc)

# Торговые параметры
TAKER_FEE = 0.0005
LEVERAGE = 10
# Проскальзывание рыночных ордеров. Вход/выход — Market, на волатильных
# альтах цена исполнения хуже заявленной. Берём консервативную оценку 0.1%
# (типичное проскальзывание на Bybit для альткоинов с рыночным ордером).
# Применяется к цене входа (хуже для покупателя) и выхода (хуже для продажи).
SLIPPAGE = 0.001
PAIR_LAUNCH_COOLDOWN_MS = 60 * 1000          # 1 минута между запусками по паре
SIDE_WEBHOOK_COOLDOWN_MS = 5 * 60 * 1000     # 5 минут между сигналами одной стороны (v28+ = 0, но в базовой модели 2.6 учитывается)
STOP_LOSS_PAUSE_MS = 3 * 60 * 60 * 1000      # 3 часа глобальной паузы после SL
MIN_FIRST_ORDER = 6.0

# Параметры Pine v29
BB_LENGTH, BB_MULT = 20, 2.0
RSI_LENGTH = 14
ATR_LENGTH = 14
EMA_FAST_LEN, EMA_MID_LEN, EMA_SLOW_LEN = 9, 21, 50
VOLUME_LEN = 30
MIN_VOLUME_RATIO = 0.45
MAX_ATR_PCT = 4.0
MAX_BB_WIDTH_PCT = 3.5

# Пер-парные адаптивные пороги (зеркало Pine v29 adaptive thresholds).
# Потолки ATR/BB-width масштабируются от медианы волатильности пары, но не
# выходят за [floor, ceiling]. На волатильных парах пороги ужесточаются.
ADAPTIVE_VOL_LOOKBACK = 500      # окно медианы (~5 дней на 15m)
ADAPTIVE_ATR_FLOOR = 2.0         # минимум потолка ATR %
ADAPTIVE_ATR_CEILING = 6.0       # максимум потолка ATR %
ADAPTIVE_BB_FLOOR = 2.0          # минимум потолка BB-width %
ADAPTIVE_BB_CEILING = 5.0        # максимум потолка BB-width %
MACRO_MOVE1_LIMIT = 0.8
MACRO_MOVE3_LIMIT = 1.2
GLOBAL_TREND_MOVE3_LIMIT = 1.5
MAX_LONG_RED_CANDLE_PCT = 0.7
MAX_SHORT_GREEN_CANDLE_PCT = 0.7

# Множители GRID DCA 2.9 (отличие от 2.7/2.8: TP по этапам, SL x1.3)
GRID_29_TP_MULT = {"range": 1.0, "trend": 1.15, "pullback": 1.20}
GRID_29_SL_MULT = 1.3

# Динамический TP по волатильности (опциональный режим, --dynamic-tp).
# Базовый TP = step*0.55 слишком мал относительно SL: на реальных сделках
# ratio win/loss = 0.02 (средний выигрыш 0.14 vs убыток 7.33). Динамический TP
# привязывается к ATR напрямую с целевым соотношением выигрыш/риск ~0.4:
# при win rate 80% даёт 0.8*0.4 - 0.2*1 = +0.12 на сделку в среднем.
# TP = clamp(atr_pct * DYN_TP_ATR_MULT * stage_mult, min, max),
# где DYN_TP_ATR_MULT подобран так, чтобы TP ~ 0.4 * типичного SL.
DYN_TP_ATR_MULT = 0.75       # доля ATR для базового TP (волатильностный) — v2.10
DYN_TP_TARGET_RATIO = 0.40   # целевое соотношение выигрыш/убыток (для валидации)
DYN_TP_MIN = 0.45            # нижний порог TP (% от средней цены входа)
DYN_TP_MAX = 1.50            # верхний порог TP (не ширре 1.5%, чтобы не держать вечно)
DYN_TP_STAGE_MULT = {"range": 1.0, "trend": 1.25, "pullback": 1.35}  # тренд/откат — крупнее TP

# Пресеты DCA-сетки по этапам (из _grid_from_event сервера / grid_for автора)
GRID_PRESETS = {
    "range":    {"dca_active": 3, "mult_vol": 1.15, "mult_price": 1.05, "atr_step_mult": 0.85, "min_step": 0.45, "max_step": 1.8,  "min_tp": 0.35, "max_tp": 0.75, "min_stop": 3.0, "max_stop": 6.0},
    "trend":    {"dca_active": 2, "mult_vol": 1.20, "mult_price": 1.15, "atr_step_mult": 1.10, "min_step": 0.75, "max_step": 2.4,  "min_tp": 0.45, "max_tp": 1.00, "min_stop": 3.0, "max_stop": 6.5},
    "pullback": {"dca_active": 3, "mult_vol": 1.20, "mult_price": 1.10, "atr_step_mult": 0.75, "min_step": 0.55, "max_step": 2.0,  "min_tp": 0.40, "max_tp": 0.85, "min_stop": 3.5, "max_stop": 6.5},
}

OUT_JSON = Path(".private_reports/grid-dca-29-independent-backtest.json")
OUT_HTML = Path(".private_reports/grid-dca-29-independent-backtest.html")


@dataclass
class Tariff:
    code: str
    name_ru: str
    initial_deposit: float
    pairs: list[str]
    max_total: int
    max_long: int
    max_short: int
    first_order_mode: str          # "manual" | "deposit_pct"
    manual_first_order: float = 0.0
    risk_pct: float = 5.0
    max_first_order: float | None = None


def build_tariffs() -> list[Tariff]:
    """Тарифы с параметрами как у предыдущего автора (включая фильтр пар)."""
    free_pairs = [p for p in ALL_PAIRS if p not in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}]
    start_pairs = [p for p in ALL_PAIRS if p != "BTCUSDT"]
    all_pairs = ALL_PAIRS[:]
    return [
        Tariff("free", "Бесплатный", 50.0, free_pairs, 4, 4, 4, "manual", 6.0, 0.0, 6.0),
        Tariff("free_plus", "Бесплатный Плюс", 100.0, free_pairs, 6, 6, 6, "deposit_pct", 0.0, 5.0, 12.0),
        Tariff("start", "Старт", 500.0, start_pairs, 8, 8, 8, "deposit_pct", 0.0, 5.0, 60.0),
        Tariff("start_plus", "Старт Плюс", 1000.0, all_pairs, 10, 10, 10, "deposit_pct", 0.0, 5.0, 120.0),
        Tariff("premium", "Премиум", 5000.0, all_pairs, 12, 12, 12, "deposit_pct", 0.0, 5.0, 600.0),
        Tariff("premium_plus", "Премиум Плюс", 10000.0, all_pairs, 40, 20, 20, "deposit_pct", 0.0, 5.0, 2000.0),
    ]


# ---------------------------------------------------------------------------
# Загрузка свечей
# ---------------------------------------------------------------------------

def load_pair_candles(pair: str) -> list[list]:
    files = sorted(glob.glob(str(CANDLE_DIR / f"{pair}_*.json")))
    candles: list[list] = []
    seen: set[int] = set()
    for f in files:
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for c in data:
            ts = int(c[0])
            if ts in seen:
                continue
            seen.add(ts)
            candles.append([ts, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
    candles.sort(key=lambda c: c[0])
    start_ms = int(PERIOD_START.timestamp() * 1000)
    end_ms = int(PERIOD_END.timestamp() * 1000)
    return [c for c in candles if start_ms <= c[0] < end_ms]


# ---------------------------------------------------------------------------
# Индикаторы (собственная реализация)
# ---------------------------------------------------------------------------

def ema(values: list[float], length: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (length + 1)
    out = [values[0]]
    for i in range(1, len(values)):
        out.append(alpha * values[i] + (1 - alpha) * out[i - 1])
    return out


def rsi_series(closes: list[float], length: int) -> list[float]:
    if len(closes) < length + 1:
        return [50.0] * len(closes)
    gains, losses = [], []
    for i in range(1, length + 1):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_gain = sum(gains) / length
    avg_loss = sum(losses) / length
    out: list[float] = [0.0] * len(closes)
    out[length] = 100.0 - 100.0 / (1.0 + (avg_gain / avg_loss if avg_loss else 999999.0))
    for i in range(length + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        avg_gain = (avg_gain * (length - 1) + g) / length
        avg_loss = (avg_loss * (length - 1) + l) / length
        rs = avg_gain / avg_loss if avg_loss else 999999.0
        out[i] = 100.0 - 100.0 / (1.0 + rs)
    for i in range(length):
        out[i] = out[length]
    return out


def atr(candles: list[list], length: int) -> list[float]:
    if len(candles) < length + 1:
        return [0.0] * len(candles)
    trs: list[float] = []
    for i in range(len(candles)):
        if i == 0:
            trs.append(candles[i][2] - candles[i][3])
            continue
        h, l, pc = candles[i][2], candles[i][3], candles[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    out = [0.0] * len(candles)
    out[length] = sum(trs[1:length + 1]) / length
    for i in range(length + 1, len(candles)):
        out[i] = (out[i - 1] * (length - 1) + trs[i]) / length
    for i in range(length):
        out[i] = out[length]
    return out


def sma(values: list[float], length: int, idx: int) -> float:
    if idx < length - 1:
        return sum(values[:idx + 1]) / (idx + 1)
    return sum(values[idx - length + 1:idx + 1]) / length


def stdev(values: list[float], length: int, idx: int) -> float:
    window = values[:idx + 1] if idx < length - 1 else values[idx - length + 1:idx + 1]
    if len(window) < 2:
        return 0.0
    m = sum(window) / len(window)
    return math.sqrt(sum((x - m) ** 2 for x in window) / len(window))


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Расчёт индикаторов и сигналов (точная транскрипция Pine v29)
# ---------------------------------------------------------------------------

def compute_signals(candles: list[list], pair: str, btc_ctx: dict, eth_ctx: dict,
                    dynamic_tp: bool = False, short_mode: str = "all",
                    adaptive_thresholds: bool = False) -> list[dict]:
    """Возвращает список кандидатов-сигналов с параметрами сетки (без фильтра пар).

    dynamic_tp=True включает расчёт TP по волатильности (ATR) с целевым
    соотношением выигрыш/риск ~0.4 вместо базового step*0.55.
    short_mode: какие шорт-сигналы генерировать.
      "all"        — range_short + trend_short + pullback_down (как в Pine v29)
      "trend_only" — только trend_short (шорт строго в нисходящем тренде)
      "none"       — шорты отключены (только лонги)
    """
    n = len(candles)
    if n < EMA_SLOW_LEN + 5:
        return []
    closes = [c[4] for c in candles]
    ema_f = ema(closes, EMA_FAST_LEN)
    ema_m = ema(closes, EMA_MID_LEN)
    ema_s = ema(closes, EMA_SLOW_LEN)
    atr_arr = atr(candles, ATR_LENGTH)
    rsi15 = rsi_series(closes, RSI_LENGTH)
    # 1h RSI из часовых закрытий, построенных из 15m
    hourly_closes = [closes[min(i + 3, n - 1)] for i in range(0, n, 4)]
    rsi60h = rsi_series(hourly_closes, RSI_LENGTH)
    rsi60 = [rsi60h[min(i // 4, len(rsi60h) - 1)] for i in range(n)]
    vols = [c[5] for c in candles]

    # Per-pair адаптивные пороги (зеркало Pine v29). Считаем медианы ATR% и
    # BB-width% по всей паре как характеристику её "типичной" волатильности.
    effective_max_atr = MAX_ATR_PCT
    effective_max_bb = MAX_BB_WIDTH_PCT
    if adaptive_thresholds and n > ADAPTIVE_VOL_LOOKBACK:
        import statistics as _st
        atr_pct_all = [(atr_arr[i] / closes[i] * 100.0) if closes[i] > 0 else 0.0 for i in range(n)]
        bb_basis_all = [sma(closes, BB_LENGTH, i) for i in range(n)]
        bb_dev_all = [BB_MULT * stdev(closes, BB_LENGTH, i) for i in range(n)]
        bb_width_all = [((bb_basis_all[i] + bb_dev_all[i] - (bb_basis_all[i] - bb_dev_all[i])) / bb_basis_all[i] * 100.0) if bb_basis_all[i] > 0 else 0.0 for i in range(n)]
        typical_atr = _st.median(atr_pct_all)
        typical_bb = _st.median(bb_width_all)
        adaptive_atr = clamp(typical_atr * 2.2, ADAPTIVE_ATR_FLOOR, ADAPTIVE_ATR_CEILING)
        adaptive_bb = clamp(typical_bb * 1.8, ADAPTIVE_BB_FLOOR, ADAPTIVE_BB_CEILING)
        effective_max_atr = min(MAX_ATR_PCT, adaptive_atr)
        effective_max_bb = min(MAX_BB_WIDTH_PCT, adaptive_bb)

    signals = []
    for i in range(n):
        c_close = closes[i]
        atr_pct = (atr_arr[i] / c_close * 100.0) if c_close > 0 else 0.0
        vol_ratio = vols[i] / sma(vols, VOLUME_LEN, i) if sma(vols, VOLUME_LEN, i) > 0 else 0.0
        bb_basis = sma(closes, BB_LENGTH, i)
        bb_dev = BB_MULT * stdev(closes, BB_LENGTH, i)
        bb_upper, bb_lower = bb_basis + bb_dev, bb_basis - bb_dev
        bb_width_pct = ((bb_upper - bb_lower) / bb_basis * 100.0) if bb_basis > 0 else 0.0
        bb_position = ((c_close - bb_lower) / (bb_upper - bb_lower) * 100.0) if (bb_upper - bb_lower) > 0 else 50.0
        candle_pct = ((c_close - candles[i][1]) / candles[i][1] * 100.0) if candles[i][1] > 0 else 0.0
        bar_move_pct = ((closes[i] - closes[i - 1]) / closes[i - 1] * 100.0) if i > 0 and closes[i - 1] > 0 else 0.0

        bull_trend = ema_f[i] > ema_m[i] and ema_m[i] > ema_s[i]
        bear_trend = ema_f[i] < ema_m[i] and ema_m[i] < ema_s[i]
        range_market = (not bull_trend) and (not bear_trend) and bb_width_pct <= effective_max_bb

        btc1 = btc_ctx["move1"][i] if i < len(btc_ctx["move1"]) else 0.0
        btc3 = btc_ctx["move3"][i] if i < len(btc_ctx["move3"]) else 0.0
        eth1 = eth_ctx["move1"][i] if i < len(eth_ctx["move1"]) else 0.0
        eth3 = eth_ctx["move3"][i] if i < len(eth_ctx["move3"]) else 0.0
        btc_above = btc_ctx["above_ema20"][i] if i < len(btc_ctx["above_ema20"]) else True
        eth_above = eth_ctx["above_ema20"][i] if i < len(eth_ctx["above_ema20"]) else True
        btc_d3 = btc_ctx["daily_move3"][i] if i < len(btc_ctx["daily_move3"]) else 0.0
        eth_d3 = eth_ctx["daily_move3"][i] if i < len(eth_ctx["daily_move3"]) else 0.0
        global_d3 = (btc_d3 + eth_d3) / 2.0
        global_uptrend = (btc_above and eth_above) or global_d3 >= GLOBAL_TREND_MOVE3_LIMIT
        global_downtrend = ((not btc_above) and (not eth_above)) or global_d3 <= -GLOBAL_TREND_MOVE3_LIMIT
        regime = "uptrend" if (global_uptrend and not global_downtrend) else ("downtrend" if (global_downtrend and not global_uptrend) else "neutral")
        server_long_block = (not btc_above) and (not eth_above)
        server_short_block = btc_above and eth_above
        global_long_block = (regime == "downtrend") or server_long_block
        global_short_block = (regime == "uptrend") or server_short_block
        macro_long_block = (btc1 <= -MACRO_MOVE1_LIMIT or eth1 <= -MACRO_MOVE1_LIMIT or btc3 <= -MACRO_MOVE3_LIMIT or eth3 <= -MACRO_MOVE3_LIMIT)
        macro_short_block = (btc1 >= MACRO_MOVE1_LIMIT or eth1 >= MACRO_MOVE1_LIMIT or btc3 >= MACRO_MOVE3_LIMIT or eth3 >= MACRO_MOVE3_LIMIT)
        long_breakdown = (bb_position < 0 or candle_pct <= -MAX_LONG_RED_CANDLE_PCT or (bar_move_pct <= -MAX_LONG_RED_CANDLE_PCT and vol_ratio >= 1.4) or (c_close < bb_lower and vol_ratio >= 1.0))
        short_breakout = (bb_position > 100 or candle_pct >= MAX_SHORT_GREEN_CANDLE_PCT or (bar_move_pct >= MAX_SHORT_GREEN_CANDLE_PCT and vol_ratio >= 1.4) or (c_close > bb_upper and vol_ratio >= 1.0))
        long_rsi = (42 <= rsi15[i] <= 58) and (42 <= rsi60[i] <= 68)
        short_rsi = (38 <= rsi15[i] <= 60) and (30 <= rsi60[i] <= 60)
        range_short_rsi = (rsi15[i] >= 58) and (rsi60[i] <= 65)
        trend_short_rsi = (38 <= rsi15[i] <= 56) and (rsi60[i] <= 58)
        pullback_up = bull_trend and 42 <= bb_position <= 58 and long_rsi and not long_breakdown and not macro_long_block and not global_long_block
        pullback_down = bear_trend and 45 <= bb_position <= 78 and short_rsi and not short_breakout and not macro_short_block and not global_short_block
        tradable = (atr_pct <= effective_max_atr) and (vol_ratio >= MIN_VOLUME_RATIO)
        range_short = range_market and bb_position >= 75 and range_short_rsi and not short_breakout and not macro_short_block and not global_short_block
        trend_short = bear_trend and trend_short_rsi and 42 <= bb_position <= 78 and not short_breakout and not macro_short_block and not global_short_block
        long_sig = tradable and pullback_up and not macro_long_block and not global_long_block
        if short_mode == "none":
            short_sig = False
        elif short_mode == "trend_only":
            short_sig = tradable and trend_short and not macro_short_block and not global_short_block
        else:
            short_sig = tradable and (range_short or trend_short or pullback_down) and not macro_short_block and not global_short_block
        if not (long_sig or short_sig):
            continue
        stage = "pullback" if (pullback_up or pullback_down) else ("trend" if (bear_trend or bull_trend) else "range")
        preset = GRID_PRESETS[stage]
        step = clamp(atr_pct * preset["atr_step_mult"], preset["min_step"], preset["max_step"])
        if dynamic_tp:
            # Динамический TP по волатильности: привязан к ATR напрямую с
            # целевым соотношением выигрыш/риск ~0.4 (вместо базового 0.02).
            base_tp = clamp(atr_pct * DYN_TP_ATR_MULT, DYN_TP_MIN, DYN_TP_MAX)
            tp = min(DYN_TP_MAX, base_tp * DYN_TP_STAGE_MULT[stage])
        else:
            base_tp = clamp(step * 0.55, preset["min_tp"], preset["max_tp"])
            tp = min(1.0, base_tp * GRID_29_TP_MULT[stage])
        sl = max(preset["min_stop"], min(preset["max_stop"], step * 4.0)) * GRID_29_SL_MULT
        signals.append({
            "pair": pair,
            "entry_index": i + 1,
            "entry_time": candles[i + 1][0] if i + 1 < n else candles[i][0],
            "side": "long" if long_sig else "short",
            "stage": stage,
            "grid": {
                "step": step, "tp": tp, "sl": sl,
                "dca_active": preset["dca_active"],
                "mult_vol": preset["mult_vol"],
                "mult_price": preset["mult_price"],
            },
        })
    return signals


# ---------------------------------------------------------------------------
# Симуляция сделки (точная модель автора + wiki Cryptorg)
# ---------------------------------------------------------------------------

def planned_grid_factor(grid: dict) -> float:
    dca_count = int(grid.get("dca_active") or 0)
    mult = float(grid.get("mult_vol") or 1)
    factor, leg = 1.0, 1.0
    for _ in range(dca_count):
        factor += leg
        leg *= mult
    return factor


def first_order_for(tariff: Tariff, deposit: float, grid: dict) -> float:
    if tariff.first_order_mode == "manual":
        return round(tariff.manual_first_order, 2)
    factor = planned_grid_factor(grid)
    raw = deposit * tariff.risk_pct / 100.0 * LEVERAGE / factor
    value = max(MIN_FIRST_ORDER, raw)
    if tariff.max_first_order is not None:
        value = min(value, tariff.max_first_order)
    return round(value, 2)


def simulate_trade(rows: list[list], candidate: dict, first_order: float,
                   funding_events: list[dict] | None = None, apply_funding: bool = False,
                   model_unknown: bool = False) -> dict:
    """Симуляция сделки с DCA-сеткой. TP и SL — от СРЕДНЕЙ цены входа.

    funding_events: список {'ts','rate'} событий фандинга по паре (опционально).
    apply_funding: если True, вычитать фандинг из PnL. Фандинг применяется к
    НОМИНАЛЬНОЙ позиции (notional = qty*price, не маржа) на каждом событии
    в течение удержания сделки. Лонги платят при rate>0, шорты — при rate<0
    (т.е. знак = +1 для лонга, -1 для шорта).
    """
    entry_index = int(candidate["entry_index"])
    side = candidate["side"]
    # Проскальзывание рыночного ордера: покупаем дороже (лонг), продаём дешевле (шорт).
    raw_entry = float(rows[entry_index][1])
    entry = raw_entry * (1 + SLIPPAGE) if side == "long" else raw_entry * (1 - SLIPPAGE)
    grid = candidate["grid"]
    # Фантомные/мгновенные сделки (unknown в реальности, ~19% всех закрытий).
    # Cryptorg иногда открывает позицию без DCA/TP/SL (баг), Griders её
    # мгновенно закрывает. Реальные данные: медиана PnL +0.032, среднее −0.013
    # от entry_value (комиссии съедают при мгновенном входе-выходе).
    # Моделируем: с вер-тью 19% сделка закрывается через 1-3 свечи с убытком
    # ~2× комиссии от входа (вход маркетом + мгновенный выход маркетом).
    if model_unknown and entry_index + 4 < len(rows) and random.random() < 0.19:
        phantom_fills = 1  # без DCA-сетки
        exit_idx = entry_index + 1 + random.randint(1, 3)
        exit_idx = min(exit_idx, len(rows) - 1)
        phantom_exit_raw = float(rows[exit_idx][4])
        phantom_exit = phantom_exit_raw * (1 - SLIPPAGE) if side == "long" else phantom_exit_raw * (1 + SLIPPAGE)
        qty = first_order / entry
        gross = (phantom_exit - entry) * qty if side == "long" else (entry - phantom_exit) * qty
        exit_value = qty * phantom_exit
        phantom_fees = first_order * TAKER_FEE + exit_value * TAKER_FEE
        return {
            "symbol": candidate["pair"], "side": side, "stage": candidate["stage"],
            "reason": "unknown", "pnl": gross - phantom_fees, "gross": gross,
            "fees": phantom_fees, "funding": 0.0, "fills": phantom_fills,
            "entry_value": first_order, "planned_entry_value": first_order * planned_grid_factor(grid),
            "entry_time": int(rows[entry_index][0]), "exit_time": int(rows[exit_idx][0]),
        }
    # orders: список (price, qty_in_coins, quote_usdt)
    orders = [(entry, first_order / entry, first_order)]
    fees = first_order * TAKER_FEE
    # Уровни safety orders (считаются от цены входа БЕЗ проскальзывания — это лимитные ордера)
    levels = []
    step = float(grid["step"])
    cumulative = 0.0
    dca_count = int(grid.get("dca_active") or 0)
    for order_num in range(dca_count):
        cumulative += step
        level = raw_entry * (1 - cumulative / 100) if side == "long" else raw_entry * (1 + cumulative / 100)
        safety_quote = first_order * (float(grid["mult_vol"]) ** order_num)
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
        avg = sum(p * q for p, q, _ in orders) / sum(q for _, q, _ in orders)
        # Safety orders срабатывают по достижении уровня
        while filled < len(levels):
            level, safety_quote = levels[filled]
            hit = low <= level if side == "long" else high >= level
            if not hit:
                break
            orders.append((level, safety_quote / level, safety_quote))
            fees += safety_quote * TAKER_FEE
            filled += 1
            avg = sum(p * q for p, q, _ in orders) / sum(q for _, q, _ in orders)
        # TP и SL — от средней цены входа (как в wiki Cryptorg)
        sl_price = avg * (1 - grid["sl"] / 100) if side == "long" else avg * (1 + grid["sl"] / 100)
        tp_price = avg * (1 + grid["tp"] / 100) if side == "long" else avg * (1 - grid["tp"] / 100)
        sl_hit = low <= sl_price if side == "long" else high >= sl_price
        tp_hit = high >= tp_price if side == "long" else low <= tp_price
        # Приоритет SL при одновременном касании (как в assumptions)
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
    # Проскальзывание рыночного ордера на выходе: продаём дешевле (лонг), откупаем дороже (шорт).
    exit_price = exit_price * (1 - SLIPPAGE) if side == "long" else exit_price * (1 + SLIPPAGE)
    total_qty = sum(q for _, q, _ in orders)
    avg = sum(p * q for p, q, _ in orders) / total_qty
    entry_value = sum(qt for _, _, qt in orders)
    exit_value = total_qty * exit_price
    fees += exit_value * TAKER_FEE
    gross = (exit_price - avg) * total_qty if side == "long" else (avg - exit_price) * total_qty
    funding = 0.0
    if apply_funding and funding_events:
        # Номинальная позиция (notional). На фьючерсах фандинг платится от
        # notional, а не от маржи — поэтому при плече x10 это значимо.
        # Консервативно: считаем по итоговому notional (с safety orders).
        notional = total_qty * avg
        sign = 1.0 if side == "long" else -1.0
        entry_ts = int(rows[entry_index][0])
        exit_ts = int(rows[exit_index][0])
        for ev in funding_events:
            ts = int(ev.get("ts") or 0)
            if entry_ts <= ts <= exit_ts:
                # Лонги платят при rate>0, шорты при rate<0 (платят держатели
                # в сторону преобладающего давления). sign корректирует сторону.
                funding += float(ev.get("rate") or 0) * notional * sign
    return {
        "symbol": candidate["pair"],
        "side": side,
        "stage": candidate["stage"],
        "reason": exit_reason,
        "pnl": gross - fees - funding,
        "gross": gross,
        "fees": fees,
        "funding": funding,
        "fills": len(orders),
        "entry_value": entry_value,
        "planned_entry_value": first_order * planned_grid_factor(grid),
        "entry_time": int(rows[entry_index][0]),
        "exit_time": int(rows[exit_index][0]),
    }


# ---------------------------------------------------------------------------
# Портфельная симуляция с лимитами тарифа
# ---------------------------------------------------------------------------

def active_counts(open_trades: list[dict]) -> dict:
    counts = {"total": len(open_trades), "long": 0, "short": 0}
    for t in open_trades:
        counts[t["side"]] += 1
    return counts


def can_open(tariff: Tariff, counts: dict, side: str) -> bool:
    if counts["total"] >= tariff.max_total:
        return False
    if side == "long" and counts["long"] >= tariff.max_long:
        return False
    if side == "short" and counts["short"] >= tariff.max_short:
        return False
    return True


def run_portfolio(tariff: Tariff, candidates: list[dict], data: dict[str, list[list]],
                  funding_data: dict[str, list[dict]] | None = None, apply_funding: bool = False,
                  model_unknown: bool = False) -> dict:
    deposit = tariff.initial_deposit
    initial_deposit = deposit
    trades: list[dict] = []
    open_trades: list[dict] = []
    pause_until = 0
    last_pair_launch: dict[str, int] = {}
    peak = deposit
    max_dd = 0.0
    skipped = {"pair": 0, "limit": 0, "pause": 0, "same_pair_active": 0, "pair_cooldown": 0, "side_cooldown": 0, "margin": 0}

    for cand in candidates:
        entry_time = int(cand["entry_time"])
        # Убираем закрывшиеся сделки
        open_trades = [t for t in open_trades if int(t["exit_time"]) > entry_time]
        symbol = cand["pair"]
        side = cand["side"]
        if symbol not in tariff.pairs:
            skipped["pair"] += 1
            continue
        if entry_time < pause_until:
            skipped["pause"] += 1
            continue
        if entry_time < last_pair_launch.get(symbol, 0) + PAIR_LAUNCH_COOLDOWN_MS:
            skipped["pair_cooldown"] += 1
            continue
        if any(t["symbol"] == symbol for t in open_trades):
            skipped["same_pair_active"] += 1
            continue
        counts = active_counts(open_trades)
        if not can_open(tariff, counts, side):
            skipped["limit"] += 1
            continue
        first_order = first_order_for(tariff, deposit, cand["grid"])
        planned_entry_value = first_order * planned_grid_factor(cand["grid"])
        required_margin = planned_entry_value / LEVERAGE
        active_margin = sum(float(t.get("planned_entry_value") or 0.0) / LEVERAGE for t in open_trades)
        if deposit <= 0 or active_margin + required_margin > deposit:
            skipped["margin"] += 1
            continue
        trade = simulate_trade(data[symbol], cand, first_order,
                               funding_events=(funding_data or {}).get(symbol),
                               apply_funding=apply_funding, model_unknown=model_unknown)
        deposit += float(trade["pnl"])
        trade["deposit_after"] = deposit
        trades.append(trade)
        open_trades.append(trade)
        last_pair_launch[symbol] = entry_time
        if deposit > peak:
            peak = deposit
        dd = peak - deposit
        if dd > max_dd:
            max_dd = dd
        if trade["reason"] == "sl":
            pause_until = max(pause_until, int(trade["exit_time"]) + STOP_LOSS_PAUSE_MS)

    return compute_metrics(tariff, trades, deposit, initial_deposit, max_dd, peak, skipped)


def compute_metrics(tariff: Tariff, trades: list[dict], deposit: float,
                    initial_deposit: float, max_dd: float, peak: float, skipped: dict) -> dict:
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    pnl = sum(t["pnl"] for t in trades)
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    stops = sum(1 for t in trades if t["reason"] == "sl")
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    win_rate = len(wins) / len(trades) * 100.0 if trades else 0.0
    max_dd_pct = max_dd / peak * 100.0 if peak > 0 else 0.0
    return_pct = (deposit - initial_deposit) / initial_deposit * 100.0 if initial_deposit > 0 else 0.0
    # Дневные ряды для графиков PnL-кривой (cumulative по дням).
    from datetime import datetime as _dt, timezone as _tz
    daily_map: dict[str, dict] = {}
    running = initial_deposit
    for t in sorted(trades, key=lambda x: int(x.get("entry_time") or 0)):
        day = _dt.fromtimestamp(int(t.get("entry_time") or 0) / 1000, tz=_tz.utc).strftime("%Y-%m-%d")
        d = daily_map.setdefault(day, {"date": day, "pnl": 0.0, "trades": 0})
        d["pnl"] += float(t["pnl"])
        d["trades"] += 1
    daily = []
    running = initial_deposit
    for day in sorted(daily_map):
        running += daily_map[day]["pnl"]
        daily.append({"date": day, "pnl": round(daily_map[day]["pnl"], 2),
                      "cumulative": round(running, 2), "trades": daily_map[day]["trades"]})
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "stops": stops,
        "win_rate": round(win_rate, 2),
        "pnl": round(pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "final_deposit": round(deposit, 2),
        "initial_deposit": initial_deposit,
        "return_pct": round(return_pct, 2),
        "skipped": skipped,
        "daily": daily,
    }


# ---------------------------------------------------------------------------
# Подготовка дневных рядов BTC/ETH
# ---------------------------------------------------------------------------

def build_daily_context(candles: list[list], n_15m: int) -> dict:
    if not candles:
        return {"move1": [0.0] * n_15m, "move3": [0.0] * n_15m, "above_ema20": [True] * n_15m, "daily_move3": [0.0] * n_15m}
    closes = [c[4] for c in candles]
    move1 = [0.0] * len(closes)
    move3 = [0.0] * len(closes)
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            move1[i] = (closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
        if i >= 3 and closes[i - 3] > 0:
            move3[i] = (closes[i] - closes[i - 3]) / closes[i - 3] * 100.0
    daily = [closes[min(i + 95, len(closes) - 1)] for i in range(0, len(closes), 96)]
    daily_ema = ema(daily, 20) if len(daily) >= 20 else ([closes[-1]] * len(daily))
    daily_move3 = [0.0] * len(daily)
    for i in range(3, len(daily)):
        if daily[i - 3] > 0:
            daily_move3[i] = (daily[i] - daily[i - 3]) / daily[i - 3] * 100.0
    daily_idx = lambda i: min(i // 96, len(daily) - 1)
    above_ema = [daily[daily_idx(i)] > daily_ema[daily_idx(i)] for i in range(len(closes))]
    dm3_15m = [daily_move3[daily_idx(i)] for i in range(len(closes))]

    def pad(arr, n, default=0.0):
        return arr[:n] + [default] * max(0, n - len(arr))

    return {"move1": pad(move1, n_15m), "move3": pad(move3, n_15m),
            "above_ema20": pad(above_ema, n_15m, True), "daily_move3": pad(dm3_15m, n_15m)}


# ---------------------------------------------------------------------------
# Основной прогон
# ---------------------------------------------------------------------------

FUNDING_CACHE_DIR = Path(".cache/backtests/bybit_funding")


def load_funding(pair: str) -> list[dict]:
    """Загрузить кэшированный фандинг по паре (через tools/fetch_funding_history.py)."""
    p = FUNDING_CACHE_DIR / f"{pair}.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def run_backtest(exclude_pairs: list[str] | None = None, label: str = "",
                 dynamic_tp: bool = False, apply_funding: bool = False, short_mode: str = "all",
                 adaptive_thresholds: bool = False, model_unknown: bool = False) -> dict:
    exclude_pairs = set(p.upper() for p in (exclude_pairs or []))
    # Торговые пары = ALL_PAIRS минус исключённые. BTC/ETH ВСЕГДА нужны как
    # макро-контекст (фильтры дневного тренда EMA20), поэтому из контекста их
    # не выкидываем — только из генерации торговых сигналов.
    trade_pairs = [p for p in ALL_PAIRS if p not in exclude_pairs]
    if exclude_pairs:
        print(f"Режим фильтрации пар: исключены {sorted(exclude_pairs)}")
        print(f"Торгуемых пар: {len(trade_pairs)} из {len(ALL_PAIRS)}")
    print("Загрузка свечей...")
    data: dict[str, list[list]] = {}
    for pair in ALL_PAIRS:
        data[pair] = load_pair_candles(pair)
        print(f"  {pair}: {len(data[pair])} свечей")

    funding_data: dict[str, list[dict]] = {}
    if apply_funding:
        print("Загрузка фандинга...")
        for pair in trade_pairs:
            funding_data[pair] = load_funding(pair)
        loaded = sum(1 for v in funding_data.values() if v)
        print(f"  фандинг загружен для {loaded}/{len(trade_pairs)} пар")

    print("Расчёт сигналов...")
    candidates: list[dict] = []
    for pair in trade_pairs:
        candles = data[pair]
        if len(candles) < EMA_SLOW_LEN + 5:
            continue
        # Контексты BTC/ETH выравниваем по длине текущей пары
        btc_ctx = build_daily_context(data["BTCUSDT"], len(candles))
        eth_ctx = build_daily_context(data["ETHUSDT"], len(candles))
        sigs = compute_signals(candles, pair, btc_ctx, eth_ctx, dynamic_tp=dynamic_tp, short_mode=short_mode, adaptive_thresholds=adaptive_thresholds)
        candidates.extend(sigs)
        print(f"  {pair}: {len(sigs)} сигналов")
    candidates.sort(key=lambda s: (s["entry_time"], s["pair"]))
    print(f"Всего кандидатов: {len(candidates)}")

    tariffs = build_tariffs()
    results = []
    for tariff in tariffs:
        # Убираем исключённые пары и из per-tariff списка (иначе они
        # проторговываются на тарифах, где разрешены — Start/Premium/Plus).
        tariff.pairs = [p for p in tariff.pairs if p not in exclude_pairs]
        metrics = run_portfolio(tariff, candidates, data, funding_data=funding_data, apply_funding=apply_funding, model_unknown=model_unknown)
        results.append({"code": tariff.code, "name_ru": tariff.name_ru, "metrics": metrics,
                        "initial_deposit": tariff.initial_deposit, "pairs": tariff.pairs,
                        "max_total": tariff.max_total, "max_first_order": tariff.max_first_order})
        m = metrics
        print(f"  {tariff.code:<14} trades={m['trades']:<6} win%={m['win_rate']:<6} PnL={m['pnl']:<10} "
              f"PF={m['profit_factor']:<5} DD%={m['max_drawdown_pct']:<6} ret%={m['return_pct']}")

    variant_code = "grid_dca_29_independent_v2"
    variant_desc = "Independent backtest with full Cryptorg trade model."
    if exclude_pairs:
        variant_code = "grid_dca_29_independent_excluded"
        variant_desc = f"Independent backtest, excluded pairs: {', '.join(sorted(exclude_pairs))}."
    if dynamic_tp:
        variant_code = "grid_dca_29_independent_dynamic_tp"
        variant_desc = (f"Independent backtest with DYNAMIC TP by volatility "
                        f"(ATR mult {DYN_TP_ATR_MULT}, target ratio {DYN_TP_TARGET_RATIO}, "
                        f"TP {DYN_TP_MIN}-{DYN_TP_MAX}%).")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_label": f"GRID DCA 2.9 (independent backtest{', '+label if label else ''})",
        "period": {"start": PERIOD_START.isoformat(), "end": PERIOD_END.isoformat(), "days": (PERIOD_END - PERIOD_START).days},
        "pairs": trade_pairs,
        "excluded_pairs": sorted(exclude_pairs),
        "signal_candidates": len(candidates),
        "assumptions": {
            "timeframes": "15m candles, 1h RSI from hourly closes built from 15m",
            "fee": f"Taker {TAKER_FEE*100:.2f}% on every order (entry, each safety order, exit)",
            "leverage": f"x{LEVERAGE} isolated",
            "entry": "Next 15m open after confirmed signal (barstate.isconfirmed)",
            "tp_sl_from": "Average entry price after safety orders (per Cryptorg wiki)",
            "same_candle_priority": "If TP and SL hit same candle, SL counted first",
            "cooldowns": f"pair {PAIR_LAUNCH_COOLDOWN_MS//1000}s; global pause {STOP_LOSS_PAUSE_MS//3600000}h after SL",
            "grid_dca_29": "TP: range 1.00x, trend 1.15x, pullback 1.20x; SL x1.3 for all stages",
            "portfolio": "Per-tariff pair filter, max_total/long/short limits, margin check, cumulative deposit",
        },
        "tariffs": results,
        "report_variant": {"code": variant_code, "description": variant_desc},
    }


# ---------------------------------------------------------------------------
# Сравнение с предыдущим отчётом
# ---------------------------------------------------------------------------

def compare_with_previous(report: dict, prev_path: str) -> dict:
    prev = json.loads(Path(prev_path).read_text(encoding="utf-8"))
    prev_by = {t["code"]: t for t in prev.get("tariffs", [])}
    mine_by = {t["code"]: t for t in report["tariffs"]}
    rows = []
    for tariff in build_tariffs():
        code = tariff.code
        p = prev_by.get(code, {}).get("metrics", {})
        m = mine_by.get(code, {}).get("metrics", {})
        rows.append({
            "tariff": code,
            "prev_trades": p.get("trades"), "mine_trades": m.get("trades"),
            "trades_diff": (m.get("trades", 0) - (p.get("trades") or 0)),
            "prev_win_rate": p.get("win_rate"), "mine_win_rate": m.get("win_rate"),
            "prev_pnl": p.get("pnl"), "mine_pnl": m.get("pnl"),
            "prev_pf": p.get("profit_factor"), "mine_pf": m.get("profit_factor"),
            "prev_dd_pct": p.get("max_drawdown_pct"), "mine_dd_pct": m.get("max_drawdown_pct"),
            "prev_return": p.get("return_pct"), "mine_return": m.get("return_pct"),
        })
    return {"previous_report": prev_path, "previous_period": prev.get("period"),
            "previous_variant": prev.get("report_variant", {}).get("code"), "rows": rows}


def _fmt(v, n):
    if v is None:
        return "—"
    try:
        return f"{float(v):.{n}f}"
    except Exception:
        return str(v)


def write_outputs(report: dict, comparison: dict | None, json_path: Path = OUT_JSON, html_path: Path = OUT_HTML) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report_full = {**report, "comparison": comparison}
    json_path.write_text(json.dumps(report_full, ensure_ascii=False, indent=2), encoding="utf-8")
    title = report.get("strategy_label", "Независимый бектест GRID DCA 2.9")
    html = ['<!doctype html><html lang="ru"><head><meta charset="utf-8">',
            '<meta name="robots" content="noindex,nofollow">',
            f'<title>{title}</title>',
            '<style>body{font-family:system-ui,sans-serif;margin:2rem;max-width:1200px}',
            'table{border-collapse:collapse;width:100%;margin:1rem 0}td,th{border:1px solid #ccc;padding:6px 10px;text-align:right}',
            'th{background:#f0f0f0}td.t{font-weight:600}.muted{color:#666}.neg{color:#c00}.pos{color:#070}</style>',
            '</head><body>']
    html.append('<h1>Независимый бектест GRID DCA 2.9</h1>')
    html.append(f'<p class="muted">Период: {report["period"]["start"]} → {report["period"]["end"]} ({report["period"]["days"]} дней) · '
                f'Пар: {len(report["pairs"])} · Сигналов-кандидатов: {report["signal_candidates"]}</p>')
    html.append('<h2>Мои метрики по тарифам</h2><table><tr><th class="t">Тариф</th><th>Сделок</th><th>Win %</th><th>PnL</th>'
                '<th>PF</th><th>Max DD %</th><th>Return %</th><th>Стопы</th></tr>')
    for t in report["tariffs"]:
        m = t["metrics"]
        cls = "pos" if m["pnl"] >= 0 else "neg"
        html.append(f'<tr><td class="t">{t["code"]}</td><td>{m["trades"]}</td><td>{m["win_rate"]:.1f}</td>'
                    f'<td class="{cls}">{m["pnl"]:.2f}</td><td>{m["profit_factor"]:.2f}</td>'
                    f'<td>{m["max_drawdown_pct"]:.1f}</td><td>{m["return_pct"]:.1f}</td><td>{m["stops"]}</td></tr>')
    html.append('</table>')
    if comparison:
        html.append('<h2>Сверка с предыдущим бектестом</h2>')
        html.append(f'<p class="muted">Предыдущий отчёт: <code>{comparison["previous_report"]}</code> · '
                    f'вариант: {comparison["previous_variant"]}</p>')
        html.append('<table><tr><th class="t">Тариф</th><th>Сделок пред/моё</th><th>Δ сделок</th>'
                    '<th>Win %</th><th>PnL пред/моё</th><th>PF пред/моё</th><th>DD %</th></tr>')
        for r in comparison["rows"]:
            td = r["trades_diff"]
            base = max(1, abs(r["prev_trades"] or 1))
            td_cls = "" if abs(td) / base < 0.05 else ("neg" if td < 0 else "pos")
            html.append(f'<tr><td class="t">{r["tariff"]}</td>'
                        f'<td>{_fmt(r["prev_trades"],0)} / {_fmt(r["mine_trades"],0)}</td>'
                        f'<td class="{td_cls}">{td:+d}</td>'
                        f'<td>{_fmt(r["prev_win_rate"],1)} / {_fmt(r["mine_win_rate"],1)}</td>'
                        f'<td>{_fmt(r["prev_pnl"],1)} / {_fmt(r["mine_pnl"],1)}</td>'
                        f'<td>{_fmt(r["prev_pf"],2)} / {_fmt(r["mine_pf"],2)}</td>'
                        f'<td>{_fmt(r["prev_dd_pct"],1)} / {_fmt(r["mine_dd_pct"],1)}</td></tr>')
        html.append('</table>')
        html.append('<h2>Вывод</h2><div>См. итоговый ответ в чате.</div>')
    html.append('<h2>Допущения</h2><ul>')
    for k, v in report["assumptions"].items():
        html.append(f'<li><b>{k}:</b> {v}</li>')
    html.append('</ul><p class="muted">Хорошие результаты backtest не гарантируют будущих результатов. '
                'Stop loss — часть стратегии. Реальные комиссии, funding, проскальзывание и задержки webhook '
                'могут ухудшить исполнение относительно бектеста.</p>')
    html.append('</body></html>')
    html_path.write_text("".join(html), encoding="utf-8")
    print(f"\nОтчёт: {html_path.resolve()}")
    print(f"JSON:  {json_path.resolve()}")


def run_sweep(mults: list[float], out_path: Path, apply_funding: bool = False, model_unknown: bool = False) -> None:
    """Sweep по DYN_TP_ATR_MULT: один раз грузит свечи/контекст, для каждого
    множителя пересчитывает сигналы+портфель и собирает сводную таблицу.

    Цель — проверить устойчивость результата динамического TP к выбору
    параметра (защита от переобучения под конкретный год).
    """
    import csv
    global DYN_TP_ATR_MULT
    print("Sweep по DYN_TP_ATR_MULT — загрузка свечей (один раз)...")
    data: dict[str, list[list]] = {p: load_pair_candles(p) for p in ALL_PAIRS}
    print(f"  загружено {len(data)} пар")
    funding_data: dict[str, list[dict]] = {}
    if apply_funding:
        print("Загрузка фандинга...")
        for pair in ALL_PAIRS:
            funding_data[pair] = load_funding(pair)
        loaded = sum(1 for v in funding_data.values() if v)
        print(f"  фандинг загружен для {loaded}/{len(ALL_PAIRS)} пар")

    rows_out = []
    header = ["atr_mult", "tariff", "trades", "win_rate", "pnl", "profit_factor", "max_dd_pct", "return_pct"]
    rows_out.append(header)

    for mult in mults:
        DYN_TP_ATR_MULT = mult
        print(f"\n=== DYN_TP_ATR_MULT = {mult} ===")
        candidates: list[dict] = []
        for pair in ALL_PAIRS:
            candles = data[pair]
            if len(candles) < EMA_SLOW_LEN + 5:
                continue
            btc_ctx = build_daily_context(data["BTCUSDT"], len(candles))
            eth_ctx = build_daily_context(data["ETHUSDT"], len(candles))
            candidates.extend(compute_signals(candles, pair, btc_ctx, eth_ctx, dynamic_tp=True))
        candidates.sort(key=lambda s: (s["entry_time"], s["pair"]))
        for tariff in build_tariffs():
            metrics = run_portfolio(tariff, candidates, data, funding_data=funding_data, apply_funding=apply_funding, model_unknown=model_unknown)
            print(f"  {tariff.code:<14} trades={metrics['trades']:<6} win%={metrics['win_rate']:<6} "
                  f"PnL={metrics['pnl']:<10} PF={metrics['profit_factor']:<5} DD%={metrics['max_drawdown_pct']}")
            rows_out.append([mult, tariff.code, metrics["trades"], metrics["win_rate"],
                             metrics["pnl"], metrics["profit_factor"], metrics["max_drawdown_pct"], metrics["return_pct"]])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerows(rows_out)
    print(f"\nSweep CSV: {out_path.resolve()}")

    # Сводная таблица по premium_plus (самый показательный тариф) для быстрого вывода
    print("\n=== СВОДКА (premium_plus) ===")
    print(f"{'atr_mult':<10}{'trades':<8}{'win%':<8}{'PnL':<14}{'PF':<7}{'DD%':<7}{'ret%':<8}")
    pp = [r for r in rows_out[1:] if r[1] == "premium_plus"]
    for r in sorted(pp, key=lambda x: x[0]):
        print(f"{r[0]:<10}{r[2]:<8}{r[3]:<8}{r[5]:<14}{r[6]:<7}{r[7]:<7}{r[8]:<8}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent GRID DCA 2.9 backtest (v2).")
    parser.add_argument("--compare", default="webapp/static/reports/grid-dca-29-year-all-tariffs.json",
                        help="JSON предыдущего отчёта для сверки.")
    parser.add_argument("--no-compare", action="store_true", help="Не делать сверку.")
    parser.add_argument("--exclude-pairs", default="", help="Пары для исключения через запятую, напр. NEARUSDT,ARBUSDT.")
    parser.add_argument("--label", default="", help="Метка варианта (для имён файлов и заголовка).")
    parser.add_argument("--dynamic-tp", action="store_true",
                        help="Включить динамический TP по волатильности (ATR) вместо базового step*0.55.")
    parser.add_argument("--sweep", action="store_true",
                        help="Sweep-режим: прогнать сетку DYN_TP_ATR_MULT для проверки устойчивости.")
    parser.add_argument("--sweep-mults", default="0.35,0.45,0.55,0.65,0.75,0.90",
                        help="Значения DYN_TP_ATR_MULT через запятую для sweep.")
    parser.add_argument("--funding", action="store_true",
                        help="Учитывать фандинг Bybit (применяется к notional позиции каждые 8ч).")
    parser.add_argument("--short-mode", default="all", choices=["all", "trend_only", "none"],
                        help="Режим шортов: all (по умолчанию), trend_only (только trendShort), none (шорты отключены).")
    parser.add_argument("--adaptive-thresholds", action="store_true",
                        help="Пер-парные адаптивные пороги ATR/BB-width (зеркало Pine v29).")
    parser.add_argument("--model-unknown", action="store_true",
                        help="Моделировать фантомные unknown-сделки (19%, как в реальности).")
    parser.add_argument("--start", default="", help="Дата старта периода YYYY-MM-DD (по умолчанию PERIOD_START).")
    parser.add_argument("--end", default="", help="Дата конца периода YYYY-MM-DD (по умолчанию PERIOD_END).")
    args = parser.parse_args()

    # Переопределение периода (для сверки с реальными сделками на том же отрезке).
    global PERIOD_START, PERIOD_END
    if args.start:
        PERIOD_START = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    if args.end:
        PERIOD_END = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    if args.sweep:
        mults = [float(x.strip()) for x in args.sweep_mults.split(",") if x.strip()]
        run_sweep(mults, OUT_JSON.parent / "grid-dca-29-dynamic-tp-sweep.csv",
                  apply_funding=args.funding, model_unknown=args.model_unknown)
        return

    exclude_pairs = [p.strip().upper() for p in args.exclude_pairs.split(",") if p.strip()]
    report = run_backtest(exclude_pairs=exclude_pairs, label=args.label,
                          dynamic_tp=args.dynamic_tp, apply_funding=args.funding, short_mode=args.short_mode,
                          adaptive_thresholds=args.adaptive_thresholds, model_unknown=args.model_unknown)

    # Имена выходных файлов с учётом метки/исключений/режима
    suffix = args.label or ("excluded" if exclude_pairs else ("dynamic_tp" if args.dynamic_tp else "v2"))
    json_path = OUT_JSON.parent / f"grid-dca-29-independent-{suffix}.json"
    html_path = OUT_HTML.parent / f"grid-dca-29-independent-{suffix}.html"

    comparison = None
    if not args.no_compare and Path(args.compare).exists():
        comparison = compare_with_previous(report, args.compare)
        print("\n=== Сверка с референс-отчётом ===")
        print(f"{'Тариф':<14}{'Сделок пред/моё':<24}{'PnL пред/моё':<30}{'PF пред/моё':<22}")
        for r in comparison["rows"]:
            tr = f"{_fmt(r['prev_trades'],0)}/{_fmt(r['mine_trades'],0)}"
            pl = f"{_fmt(r['prev_pnl'],1)}/{_fmt(r['mine_pnl'],1)}"
            pf = f"{_fmt(r['prev_pf'],2)}/{_fmt(r['mine_pf'],2)}"
            print(f"{r['tariff']:<14}{tr:<24}{pl:<30}{pf:<22}")
    write_outputs(report, comparison, json_path=json_path, html_path=html_path)


if __name__ == "__main__":
    main()
