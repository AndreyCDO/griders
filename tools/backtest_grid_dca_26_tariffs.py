"""Portfolio backtest for GRID DCA 2.6 free vs premium tariff settings."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from tools.backtest_grid_dca_26_vs_31 import (
    INTERVAL_MS,
    grid_for,
    hourly_rsi_by_15m,
    indicators,
    _signal_at,
)
from tools.backtest_grid_dca_v25 import TAKER_FEE, fetch_klines


ALL_PAIRS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "NEARUSDT",
    "ZECUSDT", "TONUSDT", "XRPUSDT", "SUIUSDT", "FILUSDT",
    "TAOUSDT", "RENDERUSDT", "ADAUSDT", "INJUSDT", "LITUSDT",
    "ENAUSDT", "LINKUSDT", "AVAXUSDT", "JUPUSDT", "ARBUSDT",
]

REPORT_DIR = Path("webapp/static/reports")
REPORT_PATH = REPORT_DIR / "grid-dca-26-tariffs.html"
JSON_PATH = REPORT_DIR / "grid-dca-26-tariffs.json"


@dataclass(frozen=True)
class Tariff:
    code: str
    name: str
    initial_deposit: float
    pairs: list[str]
    max_total: int
    max_long: int
    max_short: int
    first_order_mode: str
    manual_first_order: float = 6.0
    risk_pct: float = 5.0
    max_first_order: float = 60.0


TARIFFS = [
    Tariff(
        code="free",
        name="Бесплатный тариф",
        initial_deposit=100.0,
        pairs=[pair for pair in ALL_PAIRS if pair not in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}],
        max_total=4,
        max_long=2,
        max_short=2,
        first_order_mode="manual",
        manual_first_order=6.0,
    ),
    Tariff(
        code="premium",
        name="Старт",
        initial_deposit=500.0,
        pairs=[pair for pair in ALL_PAIRS if pair != "BTCUSDT"],
        max_total=8,
        max_long=4,
        max_short=4,
        first_order_mode="deposit_pct",
        risk_pct=5.0,
        max_first_order=60.0,
    ),
]


def _utc_floor_15m(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc).replace(second=0, microsecond=0)
    minute = value.minute - value.minute % 15
    return value.replace(minute=minute)


async def fetch_all(days: int, end: datetime) -> tuple[datetime, datetime, dict[str, list]]:
    end = _utc_floor_15m(end)
    start = end - timedelta(days=days)
    warmup_start = start - timedelta(days=5)
    start_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    needed = sorted(set(["BTCUSDT", "ETHUSDT", *ALL_PAIRS]))
    all_rows: dict[str, list] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for symbol in needed:
            for attempt in range(8):
                try:
                    all_rows[symbol] = await fetch_klines(client, symbol, start_ms, end_ms)
                    break
                except (RuntimeError, httpx.HTTPError) as exc:
                    if attempt == 7:
                        raise
                    wait_seconds = 2 + attempt * 2
                    print("retry_wait", symbol, wait_seconds, type(exc).__name__, str(exc)[:120])
                    await asyncio.sleep(wait_seconds)
            print("fetched", symbol, len(all_rows[symbol]))
            await asyncio.sleep(0.25)
    return start, end, all_rows


def planned_grid_factor(grid: dict) -> float:
    active = int(grid.get("dca_active") or 0)
    multiplier = float(grid.get("mult_vol") or 1)
    factor = 1.0
    leg = 1.0
    for _ in range(active):
        factor += leg
        leg *= multiplier
    return factor


def first_order_for(tariff: Tariff, deposit: float, grid: dict) -> float:
    if tariff.first_order_mode == "manual":
        return tariff.manual_first_order
    factor = planned_grid_factor(grid)
    raw = deposit * tariff.risk_pct / 100.0 * 10.0 / factor
    return round(max(6.0, min(tariff.max_first_order, raw)), 2)


def all_signal_candidates(start: datetime, all_rows: dict[str, list]) -> list[dict]:
    btc = indicators(all_rows["BTCUSDT"])
    eth = indicators(all_rows["ETHUSDT"])
    start_ms = int(start.timestamp() * 1000)
    candidates: list[dict] = []
    for symbol in ALL_PAIRS:
        rows = all_rows[symbol]
        ind = indicators(rows)
        rsi60 = hourly_rsi_by_15m(rows)
        for index in range(max(50, 30, 20, 14, 3), len(rows) - 1):
            signal = _signal_at("2.6", ind, index, btc, eth, rsi60)
            if not signal:
                continue
            entry_index = index + 1
            entry_time = int(rows[entry_index][0])
            if entry_time < start_ms:
                continue
            candidates.append({
                "symbol": symbol,
                "side": signal["side"],
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
            })
    return sorted(candidates, key=lambda item: (item["entry_time"], item["symbol"], item["side"]))


def simulate_trade(rows: list, candidate: dict, first_order: float) -> dict:
    entry_index = int(candidate["entry_index"])
    entry = float(rows[entry_index][1])
    side = candidate["side"]
    grid = candidate["grid"]
    orders = [(entry, first_order / entry, first_order)]
    fees = first_order * TAKER_FEE
    levels = []
    step = float(grid["step"])
    cumulative = 0.0
    for order_num in range(int(grid["dca_active"])):
        cumulative += step
        level = entry * (1 - cumulative / 100) if side == "long" else entry * (1 + cumulative / 100)
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
    exit_value = total_qty * exit_price
    fees += exit_value * TAKER_FEE
    gross = (exit_price - avg) * total_qty if side == "long" else (avg - exit_price) * total_qty
    return {
        **candidate,
        "first_order": first_order,
        "pnl": gross - fees,
        "gross": gross,
        "fees": fees,
        "fills": len(orders),
        "entry_value": sum(quote for _, _, quote in orders),
        "exit_time": int(rows[exit_index][0]),
        "exit_index": exit_index,
        "exit_reason": exit_reason,
        "duration_bars": exit_index - entry_index,
    }


def active_counts(open_trades: list[dict]) -> dict:
    counts = {"total": len(open_trades), "long": 0, "short": 0}
    for trade in open_trades:
        counts[trade["side"]] += 1
    return counts


def can_open(tariff: Tariff, counts: dict, side: str) -> bool:
    if counts["total"] >= tariff.max_total:
        return False
    if side == "long" and counts["long"] >= tariff.max_long:
        return False
    if side == "short" and counts["short"] >= tariff.max_short:
        return False
    return True


def run_portfolio(tariff: Tariff, candidates: list[dict], all_rows: dict[str, list]) -> dict:
    deposit = tariff.initial_deposit
    trades: list[dict] = []
    skipped_pair = 0
    skipped_limit = 0
    skipped_pause = 0
    skipped_same_pair = 0
    pause_until = 0
    open_trades: list[dict] = []

    for candidate in candidates:
        entry_time = int(candidate["entry_time"])
        open_trades = [trade for trade in open_trades if int(trade["exit_time"]) > entry_time]
        if candidate["symbol"] not in tariff.pairs:
            skipped_pair += 1
            continue
        if entry_time < pause_until:
            skipped_pause += 1
            continue
        if any(trade["symbol"] == candidate["symbol"] for trade in open_trades):
            skipped_same_pair += 1
            continue
        counts = active_counts(open_trades)
        if not can_open(tariff, counts, candidate["side"]):
            skipped_limit += 1
            continue
        first_order = first_order_for(tariff, deposit, candidate["grid"])
        trade = simulate_trade(all_rows[candidate["symbol"]], candidate, first_order)
        deposit += float(trade["pnl"])
        trade["deposit_after"] = deposit
        trades.append(trade)
        open_trades.append(trade)
        if trade["exit_reason"] == "sl":
            pause_until = max(pause_until, int(trade["exit_time"]) + 3 * 60 * 60 * 1000)

    return {
        "tariff": tariff,
        "trades": trades,
        "skipped_pair": skipped_pair,
        "skipped_limit": skipped_limit,
        "skipped_pause": skipped_pause,
        "skipped_same_pair": skipped_same_pair,
        "final_deposit": deposit,
        "pnl": deposit - tariff.initial_deposit,
    }


def metric(result: dict) -> dict:
    trades = result["trades"]
    pnl = sum(float(trade["pnl"]) for trade in trades)
    wins = sum(1 for trade in trades if float(trade["pnl"]) > 0)
    losses = sum(1 for trade in trades if float(trade["pnl"]) <= 0)
    stops = sum(1 for trade in trades if trade["exit_reason"] == "sl")
    gross_profit = sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) > 0)
    gross_loss = -sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) < 0)
    max_dd = max_drawdown(result["tariff"].initial_deposit, trades)
    return {
        "trades": len(trades),
        "pnl": pnl,
        "return_pct": pnl / result["tariff"].initial_deposit * 100,
        "final_deposit": result["final_deposit"],
        "win_rate": wins / len(trades) * 100 if trades else 0,
        "wins": wins,
        "losses": losses,
        "stops": stops,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else math.inf,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd / result["tariff"].initial_deposit * 100,
        "avg_first_order": sum(float(trade["first_order"]) for trade in trades) / len(trades) if trades else 0,
        "avg_entry_value": sum(float(trade["entry_value"]) for trade in trades) / len(trades) if trades else 0,
        "skipped_pair": result["skipped_pair"],
        "skipped_limit": result["skipped_limit"],
        "skipped_pause": result["skipped_pause"],
        "skipped_same_pair": result["skipped_same_pair"],
    }


def max_drawdown(initial: float, trades: list[dict]) -> float:
    equity = initial
    peak = initial
    drawdown = 0.0
    for trade in sorted(trades, key=lambda item: item["exit_time"]):
        equity += float(trade["pnl"])
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def by_pair(trades: list[dict]) -> list[dict]:
    rows = []
    for symbol in ALL_PAIRS:
        subset = [trade for trade in trades if trade["symbol"] == symbol]
        if not subset:
            continue
        pnl = sum(float(trade["pnl"]) for trade in subset)
        rows.append({
            "symbol": symbol,
            "trades": len(subset),
            "pnl": pnl,
            "stops": sum(1 for trade in subset if trade["exit_reason"] == "sl"),
            "win_rate": sum(1 for trade in subset if float(trade["pnl"]) > 0) / len(subset) * 100,
        })
    return sorted(rows, key=lambda item: item["pnl"], reverse=True)


def fmt(value: float, digits: int = 2) -> str:
    if math.isinf(value):
        return "∞"
    return f"{value:.{digits}f}"


def signed(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}"


def css_num(value: float) -> str:
    return "pos" if value > 0 else "neg" if value < 0 else ""


def ts(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%d.%m.%Y %H:%M")


def result_to_json(start: datetime, end: datetime, candidates: list[dict], results: list[dict]) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days},
        "pairs": ALL_PAIRS,
        "signal_candidates": len(candidates),
        "tariffs": [
            {
                "code": result["tariff"].code,
                "name": result["tariff"].name,
                "settings": {
                    "initial_deposit": result["tariff"].initial_deposit,
                    "pairs": result["tariff"].pairs,
                    "max_total": result["tariff"].max_total,
                    "max_long": result["tariff"].max_long,
                    "max_short": result["tariff"].max_short,
                    "first_order_mode": result["tariff"].first_order_mode,
                    "manual_first_order": result["tariff"].manual_first_order,
                    "risk_pct": result["tariff"].risk_pct,
                    "max_first_order": result["tariff"].max_first_order,
                },
                "metrics": metric(result),
                "by_pair": by_pair(result["trades"]),
                "sample_trades": sorted(result["trades"], key=lambda item: item["pnl"])[:8],
            }
            for result in results
        ],
    }


def render_report(data: dict) -> str:
    free = data["tariffs"][0]
    premium = data["tariffs"][1]
    fm = free["metrics"]
    pm = premium["metrics"]
    max_pnl = max(abs(fm["pnl"]), abs(pm["pnl"]), 1)
    free_bar = abs(fm["pnl"]) / max_pnl * 100
    premium_bar = abs(pm["pnl"]) / max_pnl * 100

    def pair_rows() -> str:
        free_pairs = {row["symbol"]: row for row in free["by_pair"]}
        premium_pairs = {row["symbol"]: row for row in premium["by_pair"]}
        rows = []
        for symbol in ALL_PAIRS:
            f = free_pairs.get(symbol)
            p = premium_pairs.get(symbol)
            rows.append(f"""
              <tr>
                <td><strong>{symbol}</strong></td>
                <td>{f['trades'] if f else '—'}</td>
                <td class="{css_num(f['pnl']) if f else ''}">{signed(f['pnl']) if f else '—'}</td>
                <td>{p['trades'] if p else '—'}</td>
                <td class="{css_num(p['pnl']) if p else ''}">{signed(p['pnl']) if p else '—'}</td>
              </tr>
            """)
        return "\n".join(rows)

    def worst_rows(tariff: dict) -> str:
        rows = []
        for trade in tariff["sample_trades"][:6]:
            rows.append(f"""
              <tr>
                <td><strong>{trade['symbol']}</strong></td>
                <td>{'лонг' if trade['side'] == 'long' else 'шорт'}</td>
                <td>{trade['stage']}</td>
                <td>{ts(int(trade['entry_time']))}</td>
                <td>{trade['exit_reason'].upper()}</td>
                <td class="{css_num(float(trade['pnl']))}">{signed(float(trade['pnl']))}</td>
              </tr>
            """)
        return "\n".join(rows)

    assumptions = [
        "Период: последние 30 дней, таймфрейм 15 минут, вход по открытию следующей свечи после сигнала.",
        "Сигналы: логика GRID DCA 2.6 с RSI 15m/1h, ATR, Bollinger Bands, EMA и фильтром BTC/ETH.",
        "Если на одной свече одновременно возможен тейк-профит и стоп-лосс, засчитывается стоп-лосс.",
        "Комиссия: taker 0.05% на вход, DCA и выход. Funding, проскальзывание и задержки webhook не учитываются.",
        "После стоп-лосса применяется глобальная пауза GRID DCA на 3 часа.",
        "Повторная позиция по той же паре не открывается, пока предыдущая позиция по этой паре активна.",
    ]
    assumptions_html = "".join(f"<li>{item}</li>" for item in assumptions)

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Бэктест GRID DCA 2.6 · Griders</title>
  <link rel="icon" href="/favicon.ico?v=20260601" sizes="any">
  <link rel="stylesheet" href="/static/app.css?v=20260605-backtest">
</head>
<body>
  <main class="container report-page">
    <section class="panel report-hero">
      <div>
        <p class="eyebrow">GRID DCA 2.6</p>
        <h1>Бэктест стратегии для бесплатного и платного тарифа</h1>
        <p class="muted">Портфельная симуляция по актуальным 20 парам Griders за период {data['period']['start'][:10]} — {data['period']['end'][:10]}. Отчёт показывает, как ограничения тарифа влияют на количество сделок, PnL и просадку.</p>
      </div>
      <div class="report-date">
        <span>Обновлено</span>
        <strong>{datetime.fromisoformat(data['generated_at']).strftime('%d.%m.%Y')}</strong>
      </div>
    </section>

    <section class="report-grid report-metrics">
      <article class="metric"><span>Бесплатный тариф</span><strong class="{css_num(fm['pnl'])}">{signed(fm['pnl'])} USDT</strong><small>Доходность {signed(fm['return_pct'])}%</small></article>
      <article class="metric"><span>Тариф Старт</span><strong class="{css_num(pm['pnl'])}">{signed(pm['pnl'])} USDT</strong><small>Доходность {signed(pm['return_pct'])}%</small></article>
      <article class="metric"><span>Сделки</span><strong>{fm['trades']} / {pm['trades']}</strong><small>free / старт</small></article>
      <article class="metric"><span>Win rate</span><strong>{fmt(fm['win_rate'])}% / {fmt(pm['win_rate'])}%</strong><small>free / старт</small></article>
    </section>

    <section class="report-grid two">
      <article class="panel">
        <h2>Настройки бесплатного тарифа</h2>
        <ul class="clean-list">
          <li>Начальный депозит: <strong>100 USDT</strong></li>
          <li>Первый ордер: <strong>6 USDT</strong></li>
          <li>Лимиты: <strong>4 активных / 2 лонг / 2 шорт</strong></li>
          <li>Запрещены пары: <strong>BTCUSDT, ETHUSDT, SOLUSDT</strong></li>
          <li>Итоговый депозит: <strong>{fmt(fm['final_deposit'])} USDT</strong></li>
          <li>Максимальная просадка: <strong>{fmt(fm['max_drawdown'])} USDT ({fmt(fm['max_drawdown_pct'])}%)</strong></li>
        </ul>
      </article>
      <article class="panel">
        <h2>Настройки тарифа Старт</h2>
        <ul class="clean-list">
          <li>Начальный депозит: <strong>500 USDT</strong></li>
          <li>Первый ордер рассчитывается исходя из правила: <strong>5% от депозита на всю сделку с учётом сетки DCA</strong>, пересчёт после каждой закрытой сделки</li>
          <li>Ограничение первого ордера: <strong>не больше 60 USDT</strong></li>
          <li>Лимиты: <strong>8 активных / 4 лонг / 4 шорт</strong></li>
          <li>Запрещена пара: <strong>BTCUSDT</strong></li>
          <li>Итоговый депозит: <strong>{fmt(pm['final_deposit'])} USDT</strong></li>
          <li>Максимальная просадка: <strong>{fmt(pm['max_drawdown'])} USDT ({fmt(pm['max_drawdown_pct'])}%)</strong></li>
        </ul>
      </article>
    </section>

    <section class="panel">
      <h2>Сравнение результата</h2>
      <div class="report-bars">
        <div class="report-bar-row"><span>Бесплатный тариф</span><div class="report-bar-track"><i class="{'positive' if fm['pnl'] >= 0 else 'negative'}" style="width:{free_bar:.2f}%"></i><b>{signed(fm['pnl'])} USDT</b></div></div>
        <div class="report-bar-row"><span>Тариф Старт</span><div class="report-bar-track"><i class="{'positive' if pm['pnl'] >= 0 else 'negative'}" style="width:{premium_bar:.2f}%"></i><b>{signed(pm['pnl'])} USDT</b></div></div>
      </div>
      <div class="report-mini-grid">
        <div><span>Стоп-лоссы free</span><strong>{fm['stops']}</strong></div>
        <div><span>Стоп-лоссы Старт</span><strong>{pm['stops']}</strong></div>
        <div><span>Пропуск по лимитам free</span><strong>{fm['skipped_limit']}</strong></div>
        <div><span>Пропуск по лимитам Старт</span><strong>{pm['skipped_limit']}</strong></div>
        <div><span>Средний первый ордер free</span><strong>{fmt(fm['avg_first_order'])} USDT</strong></div>
        <div><span>Средний первый ордер Старт</span><strong>{fmt(pm['avg_first_order'])} USDT</strong></div>
      </div>
    </section>

    <section class="panel">
      <h2>PnL по парам</h2>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Пара</th><th>Сделок free</th><th>PnL free</th><th>Сделок Старт</th><th>PnL Старт</th></tr></thead>
          <tbody>{pair_rows()}</tbody>
        </table>
      </div>
    </section>

    <section class="report-grid two">
      <article class="panel">
        <h2>Худшие сделки free</h2>
        <div class="table-scroll"><table><thead><tr><th>Пара</th><th>Сторона</th><th>Стадия</th><th>Вход UTC</th><th>Выход</th><th>PnL</th></tr></thead><tbody>{worst_rows(free)}</tbody></table></div>
      </article>
      <article class="panel">
        <h2>Худшие сделки тарифа Старт</h2>
        <div class="table-scroll"><table><thead><tr><th>Пара</th><th>Сторона</th><th>Стадия</th><th>Вход UTC</th><th>Выход</th><th>PnL</th></tr></thead><tbody>{worst_rows(premium)}</tbody></table></div>
      </article>
    </section>

    <section class="panel">
      <h2>Допущения расчёта</h2>
      <ul class="clean-list">{assumptions_html}</ul>
      <p class="form-note">Бэктест не является гарантией будущей доходности. Реальная торговля может отличаться из-за проскальзывания, ликвидности, задержек webhook, ошибок сторонних сервисов, funding и ручных действий пользователя.</p>
    </section>
  </main>
</body>
</html>
"""


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--end", default="")
    args = parser.parse_args()
    end = datetime.fromisoformat(args.end).astimezone(timezone.utc) if args.end else datetime.now(timezone.utc)
    start, end, all_rows = await fetch_all(args.days, end)
    candidates = all_signal_candidates(start, all_rows)
    results = [run_portfolio(tariff, candidates, all_rows) for tariff in TARIFFS]
    data = result_to_json(start, end, candidates, results)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    REPORT_PATH.write_text(render_report(data), encoding="utf-8")
    print(json.dumps({
        "report": str(REPORT_PATH.resolve()),
        "json": str(JSON_PATH.resolve()),
        "period": data["period"],
        "signal_candidates": data["signal_candidates"],
        "metrics": [{item["code"]: item["metrics"]} for item in data["tariffs"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
