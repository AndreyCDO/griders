"""Постатейная сверка бектест-модели с реальными сделками.

Берёт реальные сделки (pair, side, sent_at, grid_snapshot, closed_pnl),
прогоняет simulate_trade на тех же 15m свечах с теми же параметрами сетки
и сравнивает предсказанный PnL с реальным. Показывает, где модель врёт:
в TP, в комиссии, в фандинге, в срабатывании SL.

Запуск: python tools/verify_backtest_vs_reality.py
"""
from __future__ import annotations

import glob
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

# Переиспользуем функции из бектеста
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.backtest_grid_dca_29_independent import (
    CANDLE_DIR, load_pair_candles, TAKER_FEE, LEVERAGE,
    FUNDING_CACHE_DIR, load_funding,
)

SAMPLE_PATH = Path(".tmp_trades_sample.json")
OUT_REPORT = Path(".private_reports/backtest-vs-reality-verification.json")


def load_all_candles(pair: str) -> list[list]:
    """Загрузить ВСЕ свечи пары из кэша без фильтра по периоду (для сверки реальных сделок)."""
    import glob
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
    return candles


def find_candle_index(candles: list[list], target_ts_ms: int) -> int:
    """Найти индекс СИГНАЛЬНОЙ свечи: последняя свеча с ts <= sent_at.

    TradingView формирует confirmed-сигнал на закрытии 15m свечи. sent_at —
    момент приёма вебхука Griders, чуть позже закрытия (на обработку/сеть).
    Значит сигнальная свеча = та, чьё закрытие (ts+15min) <= sent_at,
    т.е. последняя свеча с ts <= sent_at. Вход — на open следующей свечи.
    """
    lo, hi = 0, len(candles) - 1
    result = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if candles[mid][0] <= target_ts_ms:
            result = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def simulate_one_trade(candles, pair, side, entry_index, grid, funding_events, apply_funding=True):
    """Упрощённая симуляция одной сделки с реальными параметрами grid.

    Возвращает dict с предсказанным PnL, reason, fills, funding.
    Логика зеркалит simulate_trade из бектеста (TP/SL от средней цены входа).
    """
    if entry_index + 1 >= len(candles):
        return None
    entry = float(candles[entry_index + 1][1])
    first_order = float(grid["open"]["orderVolume"])
    dca = grid.get("dca", {})
    dca_active = int(dca.get("active", 0))
    dca_percent = float(dca.get("percent", 0))
    mult_vol = float(dca.get("multiplierVolume", 1))
    mult_price = float(dca.get("multiplierPrice", 1))
    tp_pct = float(grid["close"]["value"])
    # SL не хранится в grid_snapshot напрямую; нет stop-секции в примере.
    # Используем оценку: SL = 3.0..6.5 * 1.3 как в стратегии (по step*4).
    step = dca_percent
    sl_pct = max(3.0, min(6.5, step * 4.0)) * 1.3

    orders = [(entry, first_order / entry, first_order)]
    fees = first_order * TAKER_FEE
    levels = []
    cur_step = dca_percent
    cumulative = 0.0
    for order_num in range(dca_active):
        cumulative += cur_step
        level = entry * (1 - cumulative / 100) if side == "long" else entry * (1 + cumulative / 100)
        safety_quote = first_order * (mult_vol ** order_num)
        levels.append((level, safety_quote))
        cur_step *= mult_price

    filled = 0
    exit_price = None
    exit_reason = "eod"
    exit_index = len(candles) - 1
    for scan_index in range(entry_index + 2, len(candles)):
        high = float(candles[scan_index][2])
        low = float(candles[scan_index][3])
        avg = sum(p * q for p, q, _ in orders) / sum(q for _, q, _ in orders)
        while filled < len(levels):
            level, safety_quote = levels[filled]
            hit = low <= level if side == "long" else high >= level
            if not hit:
                break
            orders.append((level, safety_quote / level, safety_quote))
            fees += safety_quote * TAKER_FEE
            filled += 1
            avg = sum(p * q for p, q, _ in orders) / sum(q for _, q, _ in orders)
        sl_price = avg * (1 - sl_pct / 100) if side == "long" else avg * (1 + sl_pct / 100)
        tp_price = avg * (1 + tp_pct / 100) if side == "long" else avg * (1 - tp_pct / 100)
        sl_hit = low <= sl_price if side == "long" else high >= sl_price
        tp_hit = high >= tp_price if side == "long" else low <= tp_price
        if sl_hit:
            exit_price = sl_price; exit_reason = "sl"; exit_index = scan_index; break
        if tp_hit:
            exit_price = tp_price; exit_reason = "tp"; exit_index = scan_index; break

    if exit_price is None:
        exit_price = float(candles[-1][4])
    total_qty = sum(q for _, q, _ in orders)
    avg = sum(p * q for p, q, _ in orders) / total_qty
    exit_value = total_qty * exit_price
    fees += exit_value * TAKER_FEE
    gross = (exit_price - avg) * total_qty if side == "long" else (avg - exit_price) * total_qty
    # Фандинг
    funding = 0.0
    if apply_funding and funding_events:
        notional = total_qty * avg
        sign = 1.0 if side == "long" else -1.0
        entry_ts = int(candles[entry_index + 1][0])
        exit_ts = int(candles[exit_index][0])
        for ev in funding_events:
            ts = int(ev.get("ts") or 0)
            if entry_ts <= ts <= exit_ts:
                funding += float(ev.get("rate") or 0) * notional * sign
    pnl = gross - fees - funding
    return {
        "pnl": pnl, "gross": gross, "fees": fees, "funding": funding,
        "reason": exit_reason, "fills": len(orders), "entry": entry, "exit": exit_price,
        "avg_entry": avg, "bars_held": exit_index - entry_index,
    }


def main():
    sample = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    print(f"Загружено {len(sample)} реальных сделок для сверки")

    # Группируем по парам, грузим свечи и фандинг один раз на пару
    pairs = sorted(set(s["pair"] for s in sample))
    print(f"Пар: {len(pairs)}")
    candles_cache = {}
    funding_cache = {}
    for p in pairs:
        candles_cache[p] = load_all_candles(p)
        funding_cache[p] = load_funding(p)
        print(f"  {p}: {len(candles_cache[p])} свечей, фандинг {len(funding_cache[p])}")

    results = []
    matched = 0
    skipped = 0
    for s in sample:
        pair = s["pair"]
        candles = candles_cache.get(pair, [])
        if not candles:
            skipped += 1
            continue
        # sent_at -> ts
        sent_at = s["sent_at"]
        if not sent_at:
            skipped += 1
            continue
        # sent_at хранится в UTC. Строка из БД приходит без tz-метки, поэтому
        # явно интерпретируем как UTC, иначе .timestamp() на клиентской машине
        # применит её локальный часовой пояс (Москва UTC+3) и сдвинет вход на 3ч.
        dt = datetime.fromisoformat(sent_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts_ms = int(dt.timestamp() * 1000)
        entry_index = find_candle_index(candles, ts_ms)
        if entry_index < 0 or entry_index + 2 >= len(candles):
            skipped += 1
            continue
        sim = simulate_one_trade(candles, pair, s["side"], entry_index, s["grid"],
                                 funding_cache.get(pair, []), apply_funding=True)
        if sim is None:
            skipped += 1
            continue
        real_pnl = s["closed_pnl"]
        diff = sim["pnl"] - real_pnl
        results.append({
            "pair": pair, "side": s["side"], "close_reason": s["close_reason"],
            "real_pnl": real_pnl, "sim_pnl": sim["pnl"], "diff": diff,
            "sim_reason": sim["reason"], "real_reason": s["close_reason"],
            "sim_fills": sim["fills"], "sim_bars": sim["bars_held"],
            "sim_fees": sim["fees"], "sim_funding": sim["funding"],
            "sim_gross": sim["gross"],
        })
        matched += 1

    print(f"\nСверено: {matched}, пропущено: {skipped}")

    # Агрегаты
    real_pnls = [r["real_pnl"] for r in results]
    sim_pnls = [r["sim_pnl"] for r in results]
    diffs = [r["diff"] for r in results]
    print(f"\n=== СВОДКА ===")
    print(f"  Реальный сумм. PnL: {sum(real_pnls):.2f}")
    print(f"  Симулированный PnL: {sum(sim_pnls):.2f}")
    print(f"  Разница (сим-реал): {sum(diffs):.2f} (средн {statistics.mean(diffs):.4f}/сделку)")
    print(f"  Медиана разницы: {statistics.median(diffs):.4f}")

    # Сходимость причин закрытия
    reason_match = sum(1 for r in results if r["sim_reason"] == r["real_reason"])
    print(f"\n  Совпадение причины закрытия: {reason_match}/{len(results)} ({reason_match/len(results)*100:.1f}%)")
    # Расходимость: сим TP, реал SL (опасно — модель считает прибыльной убыточную)
    tp_sim_sl_real = sum(1 for r in results if r["sim_reason"] == "tp" and r["real_reason"] == "sl")
    sl_sim_tp_real = sum(1 for r in results if r["sim_reason"] == "sl" and r["real_reason"] == "tp")
    print(f"  Сим=TP а реал=SL (модель ошибочно прибыльна): {tp_sim_sl_real}")
    print(f"  Сим=SL а реал=TP (модель ошибочно убыточна): {sl_sim_tp_real}")

    # Разбивка по reason
    for reason in ["take_profit", "stop_loss"]:
        sub = [r for r in results if r["close_reason"] == reason]
        if not sub:
            continue
        print(f"\n  --- {reason} (реальный) ---")
        print(f"    сделок: {len(sub)}")
        print(f"    реальн PnL: средн {statistics.mean(r['real_pnl'] for r in sub):.4f}")
        print(f"    сим PnL:    средн {statistics.mean(r['sim_pnl'] for r in sub):.4f}")
        print(f"    разница:    средн {statistics.mean(r['diff'] for r in sub):.4f}")

    # Разница по перцентилей
    abs_diffs = [abs(d) for d in diffs]
    abs_diffs.sort()
    n = len(abs_diffs)
    print(f"\n  |разница| перцентили: p50={abs_diffs[n//2]:.4f} p90={abs_diffs[n*9//10]:.4f} p99={abs_diffs[min(n-1,n*99//100)]:.4f} max={abs_diffs[-1]:.4f}")

    # Сохраним отчёт
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "matched": matched, "skipped": skipped,
        "real_total_pnl": sum(real_pnls), "sim_total_pnl": sum(sim_pnls),
        "mean_diff": statistics.mean(diffs), "median_diff": statistics.median(diffs),
        "reason_match_pct": reason_match / len(results) * 100,
        "tp_sim_sl_real": tp_sim_sl_real, "sl_sim_tp_real": sl_sim_tp_real,
        "sample_results": results[:50],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {OUT_REPORT.resolve()}")


if __name__ == "__main__":
    main()
