"""
risk.py — расчёт размера позиции и риск-менеджмент.

Формула Kelly (упрощённая):
    risk_amount = balance × risk_pct / 100
    position    = (risk_amount / stop_loss_pct) × leverage

Ограничения:
    - Не более 90% от максимально доступной маржи
    - SL/TP рассчитываются автоматически по заданному RR
"""

import math

from config import RISK_PER_TRADE_PCT, STOP_LOSS_PCT, TAKE_PROFIT_RR


def calculate_position(
    balance:        float,
    entry_price:    float,
    stop_loss_pct:  float | None = None,
    risk_pct:       float | None = None,
    leverage:       int   = 1,
    rr:             float | None = None,
    side:           str   = "LONG",
) -> dict:
    """
    Рассчитывает безопасный размер позиции.

    Args:
        balance:       Доступный баланс в USDT
        entry_price:   Цена входа
        stop_loss_pct: Стоп-лосс в % от цены входа (дефолт из config)
        risk_pct:      Риск на сделку в % от баланса (дефолт из config)
        leverage:      Кредитное плечо
        rr:            Risk/Reward (дефолт из config)

    Returns:
        dict с quantity, position_usdt, sl_price, tp_price и пояснением
    """
    sl_pct   = stop_loss_pct or STOP_LOSS_PCT
    r_pct    = risk_pct      or RISK_PER_TRADE_PCT
    rr_ratio = rr            or TAKE_PROFIT_RR

    risk_usdt    = balance * r_pct / 100
    position_raw = (risk_usdt / (sl_pct / 100)) * leverage
    max_position = balance * leverage * 0.90
    position_usdt = min(position_raw, max_position)

    quantity = position_usdt / entry_price if entry_price else 0

    # Округляем до разумного числа знаков
    qty_rounded = _round_qty(quantity)

    direction = side.upper()
    if direction == "SHORT":
        sl_price = round(entry_price * (1 + sl_pct / 100), 6)
        tp_price = round(entry_price * (1 - sl_pct / 100 * rr_ratio), 6)
    else:
        direction = "LONG"
        sl_price = round(entry_price * (1 - sl_pct / 100), 6)
        tp_price = round(entry_price * (1 + sl_pct / 100 * rr_ratio), 6)

    return {
        "balance_usdt":       round(balance, 2),
        "risk_pct":           r_pct,
        "risk_amount_usdt":   round(risk_usdt, 2),
        "stop_loss_pct":      sl_pct,
        "take_profit_rr":     rr_ratio,
        "leverage":           leverage,
        "side":               direction,
        "position_size_usdt": round(position_usdt, 2),
        "quantity":           qty_rounded,
        "entry_price":        entry_price,
        "stop_loss_price":    sl_price,
        "take_profit_price":  tp_price,
        "note": (
            f"Риск: {round(risk_usdt, 2)} USDT ({r_pct}% баланса). "
            f"SL: {sl_price} (−{sl_pct}%). "
            f"TP: {tp_price} (+{round(sl_pct * rr_ratio, 2)}%). "
            f"RR = 1:{rr_ratio}."
        ),
    }


def _round_qty(qty: float) -> float:
    """Округляет количество до 3 значимых цифр (подходит для большинства пар Bybit)."""
    if qty == 0:
        return 0.0
    magnitude = math.floor(math.log10(abs(qty)))
    factor    = 10 ** (3 - 1 - magnitude)
    return round(qty * factor) / factor


def daily_loss_check(
    starting_balance: float,
    current_balance:  float,
    max_loss_pct:     float = 5.0,
) -> dict:
    """Проверяет, не превышен ли дневной лимит убытков."""
    loss_usdt = starting_balance - current_balance
    loss_pct  = (loss_usdt / starting_balance * 100) if starting_balance else 0
    exceeded  = loss_pct >= max_loss_pct
    return {
        "starting_balance": starting_balance,
        "current_balance":  current_balance,
        "daily_loss_usdt":  round(loss_usdt, 2),
        "daily_loss_pct":   round(loss_pct, 2),
        "limit_pct":        max_loss_pct,
        "limit_exceeded":   exceeded,
        "action":           "STOP_TRADING" if exceeded else "OK",
    }
