"""One-year portfolio backtest for GRID DCA 2.6 tariff settings.

The simulation mirrors the current TradingView Pine signal as a portfolio:
all configured pairs produce candidates at the same time, then tariff limits,
strategy cooldowns, and open-position limits decide which candidates can open.
"""

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
    _signal_at,
    hourly_rsi_by_15m,
    indicators,
)
from tools.backtest_grid_dca_v25 import TAKER_FEE, fetch_klines


ALL_PAIRS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "NEARUSDT",
    "ZECUSDT", "TONUSDT", "XRPUSDT", "SUIUSDT", "FILUSDT",
    "TAOUSDT", "RENDERUSDT", "ADAUSDT", "INJUSDT", "LITUSDT",
    "ENAUSDT", "LINKUSDT", "AVAXUSDT", "JUPUSDT", "ARBUSDT",
]

LEVERAGE = 10
INTERVAL_MS = 15 * 60 * 1000
PAIR_LAUNCH_COOLDOWN_MS = 60 * 1000
SIDE_WEBHOOK_COOLDOWN_MS = 5 * 60 * 1000
STOP_LOSS_PAUSE_MS = 3 * 60 * 60 * 1000
REPORT_DIR = Path("webapp/static/reports")
REPORT_PATH = REPORT_DIR / "grid-dca-26-year-tariffs.html"
JSON_PATH = REPORT_DIR / "grid-dca-26-year-tariffs.json"
CACHE_DIR = Path(".cache/backtests/bybit_15m")


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
    max_first_order: float | None = None


TARIFFS = [
    Tariff(
        code="free",
        name="Бесплатный",
        initial_deposit=50.0,
        pairs=[pair for pair in ALL_PAIRS if pair not in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}],
        max_total=4,
        max_long=2,
        max_short=2,
        first_order_mode="manual",
        manual_first_order=6.0,
        max_first_order=6.0,
    ),
    Tariff(
        code="start",
        name="Старт",
        initial_deposit=500.0,
        pairs=[pair for pair in ALL_PAIRS if pair != "BTCUSDT"],
        max_total=6,
        max_long=3,
        max_short=3,
        first_order_mode="deposit_pct",
        risk_pct=5.0,
        max_first_order=60.0,
    ),
    Tariff(
        code="premium",
        name="Премиум",
        initial_deposit=5000.0,
        pairs=ALL_PAIRS[:],
        max_total=12,
        max_long=6,
        max_short=6,
        first_order_mode="deposit_pct",
        risk_pct=5.0,
        max_first_order=600.0,
    ),
]


def _utc_floor_15m(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc).replace(second=0, microsecond=0)
    minute = value.minute - value.minute % 15
    return value.replace(minute=minute)


def _cache_path(symbol: str, start_ms: int, end_ms: int) -> Path:
    return CACHE_DIR / f"{symbol}_{start_ms}_{end_ms}.json"


async def fetch_symbol(client: httpx.AsyncClient, symbol: str, start_ms: int, end_ms: int) -> list:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(symbol, start_ms, end_ms)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    for attempt in range(10):
        try:
            rows = await fetch_klines(client, symbol, start_ms, end_ms)
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            return rows
        except (RuntimeError, httpx.HTTPError) as exc:
            if attempt == 9:
                raise
            wait_seconds = 2.0 + attempt * 2.0
            print("retry", symbol, wait_seconds, type(exc).__name__, str(exc)[:160])
            await asyncio.sleep(wait_seconds)
    return []


async def fetch_all(days: int, end: datetime) -> tuple[datetime, datetime, dict[str, list]]:
    end = _utc_floor_15m(end)
    start = end - timedelta(days=days)
    warmup_start = start - timedelta(days=7)
    start_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    symbols = sorted(set(["BTCUSDT", "ETHUSDT", *ALL_PAIRS]))
    rows: dict[str, list] = {}
    async with httpx.AsyncClient(timeout=40) as client:
        for symbol in symbols:
            rows[symbol] = await fetch_symbol(client, symbol, start_ms, end_ms)
            print("fetched", symbol, len(rows[symbol]))
            await asyncio.sleep(0.15)
    return start, end, rows


def _with_dca_max(signal: dict) -> dict:
    stage = str(signal.get("stage") or "range")
    max_by_stage = {"range": 4, "trend": 3, "pullback": 5}
    signal["grid"] = {**signal["grid"], "dca_max": max_by_stage.get(stage, 4)}
    return signal


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
            signal = _with_dca_max(signal)
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


def planned_grid_factor(grid: dict) -> float:
    dca_count = int(grid.get("dca_max") or grid.get("dca_active") or 0)
    multiplier = float(grid.get("mult_vol") or 1)
    factor = 1.0
    leg = 1.0
    for _ in range(dca_count):
        factor += leg
        leg *= multiplier
    return factor


def first_order_for(tariff: Tariff, deposit: float, grid: dict) -> float:
    if tariff.first_order_mode == "manual":
        return round(tariff.manual_first_order, 2)
    factor = planned_grid_factor(grid)
    raw = deposit * tariff.risk_pct / 100.0 * LEVERAGE / factor
    value = max(6.0, raw)
    if tariff.max_first_order is not None:
        value = min(value, tariff.max_first_order)
    return round(value, 2)


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
    dca_count = int(grid.get("dca_max") or grid.get("dca_active") or 0)
    for order_num in range(dca_count):
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
        "dca_fills": max(0, len(orders) - 1),
        "planned_factor": planned_grid_factor(grid),
        "entry_value": sum(quote for _, _, quote in orders),
        "planned_entry_value": first_order * planned_grid_factor(grid),
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
    open_trades: list[dict] = []
    pause_until = 0
    last_pair_launch: dict[str, int] = {}
    side_lock_until = {"long": 0, "short": 0}
    skipped = {
        "pair": 0,
        "limit": 0,
        "pause": 0,
        "same_pair_active": 0,
        "pair_cooldown": 0,
        "side_cooldown": 0,
    }

    for candidate in candidates:
        entry_time = int(candidate["entry_time"])
        open_trades = [trade for trade in open_trades if int(trade["exit_time"]) > entry_time]
        symbol = candidate["symbol"]
        side = candidate["side"]
        if symbol not in tariff.pairs:
            skipped["pair"] += 1
            continue
        if entry_time < pause_until:
            skipped["pause"] += 1
            continue
        if entry_time < side_lock_until[side]:
            skipped["side_cooldown"] += 1
            continue
        if entry_time < last_pair_launch.get(symbol, 0) + PAIR_LAUNCH_COOLDOWN_MS:
            skipped["pair_cooldown"] += 1
            continue
        if any(trade["symbol"] == symbol for trade in open_trades):
            skipped["same_pair_active"] += 1
            continue
        counts = active_counts(open_trades)
        if not can_open(tariff, counts, side):
            skipped["limit"] += 1
            continue
        first_order = first_order_for(tariff, deposit, candidate["grid"])
        trade = simulate_trade(all_rows[symbol], candidate, first_order)
        deposit += float(trade["pnl"])
        trade["deposit_after"] = deposit
        trades.append(trade)
        open_trades.append(trade)
        last_pair_launch[symbol] = entry_time
        side_lock_until[side] = entry_time + SIDE_WEBHOOK_COOLDOWN_MS
        if trade["exit_reason"] == "sl":
            pause_until = max(pause_until, int(trade["exit_time"]) + STOP_LOSS_PAUSE_MS)

    return {
        "tariff": tariff,
        "trades": trades,
        "skipped": skipped,
        "final_deposit": deposit,
        "pnl": deposit - tariff.initial_deposit,
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


def metric(result: dict) -> dict:
    tariff = result["tariff"]
    trades = result["trades"]
    pnl = sum(float(trade["pnl"]) for trade in trades)
    wins = sum(1 for trade in trades if float(trade["pnl"]) > 0)
    losses = sum(1 for trade in trades if float(trade["pnl"]) <= 0)
    stops = sum(1 for trade in trades if trade["exit_reason"] == "sl")
    gross_profit = sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) > 0)
    gross_loss = -sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) < 0)
    max_dd = max_drawdown(tariff.initial_deposit, trades)
    return {
        "trades": len(trades),
        "pnl": pnl,
        "return_pct": pnl / tariff.initial_deposit * 100,
        "final_deposit": result["final_deposit"],
        "win_rate": wins / len(trades) * 100 if trades else 0,
        "wins": wins,
        "losses": losses,
        "stops": stops,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else math.inf,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd / tariff.initial_deposit * 100,
        "avg_first_order": sum(float(trade["first_order"]) for trade in trades) / len(trades) if trades else 0,
        "avg_entry_value": sum(float(trade["entry_value"]) for trade in trades) / len(trades) if trades else 0,
        "avg_planned_entry_value": sum(float(trade["planned_entry_value"]) for trade in trades) / len(trades) if trades else 0,
        "skipped": result["skipped"],
    }


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


def equity_curve(initial: float, trades: list[dict]) -> list[dict]:
    equity = initial
    curve = []
    for trade in sorted(trades, key=lambda item: item["exit_time"]):
        equity += float(trade["pnl"])
        curve.append({"time": int(trade["exit_time"]), "equity": equity})
    return curve


def result_to_json(start: datetime, end: datetime, candidates: list[dict], results: list[dict]) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days},
        "pairs": ALL_PAIRS,
        "signal_candidates": len(candidates),
        "assumptions": {
            "timeframes": "15m candles, 1h RSI derived from hourly closes built from 15m candles",
            "fee": "Taker fee 0.05% on entries, safety orders and exit",
            "entry": "Next 15m candle open after confirmed TradingView signal",
            "same_candle_priority": "If TP and SL are both touched within one candle, SL is counted first",
            "portfolio": "All eligible pairs are processed together in chronological order",
            "cooldowns": "3h GRID DCA pause after SL, one long and one short webhook per connection every 5 minutes",
        },
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
                "equity_curve": equity_curve(result["tariff"].initial_deposit, result["trades"]),
                "worst_trades": sorted(result["trades"], key=lambda item: item["pnl"])[:10],
            }
            for result in results
        ],
    }


def fmt(value: float, digits: int = 2) -> str:
    if math.isinf(value):
        return "∞"
    return f"{value:.{digits}f}"


def signed(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}"


def css_num(value: float) -> str:
    return "pos" if value > 0 else "neg" if value < 0 else ""


def render_report(data: dict) -> str:
    cards = []
    for tariff in data["tariffs"]:
        m = tariff["metrics"]
        s = tariff["settings"]
        cards.append(f"""
        <article class="panel report-card">
          <p class="eyebrow">{tariff['name']}</p>
          <h2 class="{css_num(m['pnl'])}">{signed(m['pnl'])} USDT</h2>
          <div class="report-mini-grid">
            <div><span>Итоговый депозит</span><strong>{fmt(m['final_deposit'])} USDT</strong></div>
            <div><span>Доходность</span><strong class="{css_num(m['return_pct'])}">{signed(m['return_pct'])}%</strong></div>
            <div><span>Сделок</span><strong>{m['trades']}</strong></div>
            <div><span>Win rate</span><strong>{fmt(m['win_rate'])}%</strong></div>
            <div><span>Стопов</span><strong>{m['stops']}</strong></div>
            <div><span>Макс. просадка</span><strong>{fmt(m['max_drawdown'])} USDT ({fmt(m['max_drawdown_pct'])}%)</strong></div>
            <div><span>Средний первый ордер</span><strong>{fmt(m['avg_first_order'])} USDT</strong></div>
            <div><span>Profit factor</span><strong>{fmt(m['profit_factor'])}</strong></div>
          </div>
          <p class="form-note">Депозит: {fmt(s['initial_deposit'])} USDT. Лимиты: {s['max_total']} всего / {s['max_long']} лонг / {s['max_short']} шорт. Пары: {len(s['pairs'])}.</p>
        </article>
        """)

    pair_symbols = ALL_PAIRS
    pair_rows = []
    by_tariff = [{row["symbol"]: row for row in tariff["by_pair"]} for tariff in data["tariffs"]]
    for symbol in pair_symbols:
        cells = [f"<td><strong>{symbol}</strong></td>"]
        for tariff_index, rows in enumerate(by_tariff):
            row = rows.get(symbol)
            sep_class = " tariff-sep" if tariff_index > 0 else ""
            if row:
                cells.append(f"<td class=\"{sep_class.strip()}\">{row['trades']}</td><td class=\"{css_num(row['pnl'])}\">{signed(row['pnl'])}</td>")
            else:
                cells.append(f"<td class=\"{sep_class.strip()}\">—</td><td>—</td>")
        pair_rows.append(f"<tr>{''.join(cells)}</tr>")

    worst_rows = []
    for tariff_index, tariff in enumerate(data["tariffs"]):
        for trade_index, trade in enumerate(tariff["worst_trades"][:5]):
            row_class = " class=\"tariff-break\"" if tariff_index > 0 and trade_index == 0 else ""
            worst_rows.append(f"""
            <tr{row_class}>
              <td>{tariff['name']}</td>
              <td><strong>{trade['symbol']}</strong></td>
              <td>{'лонг' if trade['side'] == 'long' else 'шорт'}</td>
              <td>{trade['stage']}</td>
              <td>{datetime.fromtimestamp(int(trade['entry_time']) / 1000, tz=timezone.utc).strftime('%d.%m.%Y %H:%M')}</td>
              <td>{trade['exit_reason'].upper()}</td>
              <td class="{css_num(float(trade['pnl']))}">{signed(float(trade['pnl']))}</td>
            </tr>
            """)

    data_json = json.dumps(data, ensure_ascii=False)
    chart_cards = []
    period_start_label = data["period"]["start"][:10]
    period_end_label = data["period"]["end"][:10]
    for index, tariff in enumerate(data["tariffs"]):
        metric_data = tariff["metrics"]
        chart_cards.append(f"""
        <article class="panel">
          <div class="chart-head">
            <div>
              <h2>{tariff['name']}</h2>
              <small class="muted">{period_start_label} — {period_end_label}</small>
            </div>
            <strong class="{css_num(metric_data['pnl'])}">{signed(metric_data['pnl'])} USDT</strong>
          </div>
          <canvas class="report-chart tariff-equity-chart" data-tariff-index="{index}"></canvas>
        </article>
        """)

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Годовой бэктест GRID DCA 2.6 · Griders</title>
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="stylesheet" href="/static/app.css?v=20260611-year-backtest">
  <style>
    .report-page {{ padding-top: 32px; padding-bottom: 48px; }}
    .report-hero {{ display:flex; justify-content:space-between; gap:24px; align-items:flex-start; }}
    .report-grid.three {{ display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:16px; }}
    .report-card h2 {{ margin: 8px 0 16px; font-size: 32px; }}
    .report-chart {{ width:100%; height:320px; border:1px solid var(--border); border-radius:8px; background:var(--panel-soft); }}
    .tariff-charts {{ display:grid; grid-template-columns:1fr; gap:16px; }}
    .chart-head {{ display:flex; justify-content:space-between; gap:16px; align-items:center; margin-bottom:12px; }}
    .chart-head h2 {{ margin:0; }}
    .tariff-sep {{ border-left: 2px solid var(--line); }}
    tr.tariff-break td {{ border-top: 3px solid var(--line); }}
    .table-scroll {{ overflow:auto; }}
    .pos {{ color: var(--accent-dark); }}
    .neg {{ color: var(--warn); }}
    @media (max-width: 900px) {{
      .report-hero {{ display:block; }}
      .report-grid.three {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <main class="container report-page">
    <section class="panel report-hero">
      <div>
        <p class="eyebrow">GRID DCA 2.6</p>
        <h1>Годовой бэктест по тарифам</h1>
        <p class="muted">Портфельная симуляция за период {data['period']['start'][:10]} — {data['period']['end'][:10]}. Все разрешённые пары работают одновременно, а лимиты тарифа применяются к общей очереди сигналов.</p>
      </div>
      <div class="report-date"><span>Сигналов-кандидатов</span><strong>{data['signal_candidates']}</strong></div>
    </section>

    <section class="report-grid three">
      {''.join(cards)}
    </section>

    <section class="tariff-charts">
      {''.join(chart_cards)}
    </section>

    <section class="panel">
      <h2>PnL по парам</h2>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Пара</th>
              <th>Сделок Free</th><th>PnL Free</th>
              <th class="tariff-sep">Сделок Start</th><th>PnL Start</th>
              <th class="tariff-sep">Сделок Premium</th><th>PnL Premium</th>
            </tr>
          </thead>
          <tbody>{''.join(pair_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Худшие сделки</h2>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Тариф</th><th>Пара</th><th>Сторона</th><th>Стадия</th><th>Вход UTC</th><th>Выход</th><th>PnL</th></tr></thead>
          <tbody>{''.join(worst_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Допущения расчёта</h2>
      <ul class="clean-list">
        <li>Источник данных: публичные свечи Bybit linear futures. Таймфрейм входа — 15 минут.</li>
        <li>Часовой RSI рассчитывается из часовых закрытий, собранных из 15-минутных свечей.</li>
        <li>Вход считается по открытию следующей 15-минутной свечи после подтверждённого сигнала TradingView.</li>
        <li>Комиссия: taker 0.05% на первый ордер, страховочные ордера и выход.</li>
        <li>Если внутри одной свечи одновременно могли сработать TP и SL, засчитывается SL как более осторожный сценарий.</li>
        <li>После стоп-лосса применяется пауза GRID DCA на 3 часа.</li>
        <li>Защита от нескольких одновременных сигналов: не больше одного webhook в лонг и одного webhook в шорт на подключение за 5 минут.</li>
        <li>Проскальзывание, funding, задержки webhook и возможные отказы Cryptorg/биржи не учитываются.</li>
      </ul>
    </section>
  </main>

  <script>
    const reportData = {data_json};
    const canvases = Array.from(document.querySelectorAll('.tariff-equity-chart'));
    function resize() {{
      canvases.forEach((canvas) => {{
        const dpr = window.devicePixelRatio || 1;
        canvas.width = canvas.clientWidth * dpr;
        canvas.height = canvas.clientHeight * dpr;
        const ctx = canvas.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }});
      drawAll();
    }}
    function drawAll() {{
      canvases.forEach((canvas) => draw(canvas, Number(canvas.dataset.tariffIndex || 0)));
    }}
    function draw(canvas, tariffIndex) {{
      const ctx = canvas.getContext('2d');
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);
      const pad = {{left: 58, right: 20, top: 24, bottom: 48}};
      const tariff = reportData.tariffs[tariffIndex];
      const curve = {{name: tariff.name, data: tariff.equity_curve, initial: tariff.settings.initial_deposit}};
      const values = [];
      values.push(curve.initial);
      curve.data.forEach(p => values.push(p.equity));
      const minY = Math.min(...values);
      const maxY = Math.max(...values);
      const start = new Date(reportData.period.start).getTime();
      const end = new Date(reportData.period.end).getTime();
      const yMin = minY - Math.max(1, (maxY - minY) * 0.08);
      const yMax = maxY + Math.max(1, (maxY - minY) * 0.08);
      const x = ts => pad.left + (ts - start) / (end - start) * (w - pad.left - pad.right);
      const y = val => pad.top + (yMax - val) / (yMax - yMin) * (h - pad.top - pad.bottom);
      ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--border') || '#d8e1e8';
      ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--muted') || '#6b7a86';
      ctx.font = '12px system-ui';
      for (let i = 0; i <= 5; i++) {{
        const val = yMin + (yMax - yMin) * i / 5;
        const yy = y(val);
        ctx.beginPath();
        ctx.moveTo(pad.left, yy);
        ctx.lineTo(w - pad.right, yy);
        ctx.stroke();
        ctx.fillText(val.toFixed(0), 8, yy + 4);
      }}
      ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--border') || '#d8e1e8';
      ctx.beginPath();
      ctx.moveTo(pad.left, h - pad.bottom);
      ctx.lineTo(w - pad.right, h - pad.bottom);
      ctx.stroke();
      const dateFmt = new Intl.DateTimeFormat('ru-RU', {{day: '2-digit', month: '2-digit'}});
      for (let i = 0; i <= 6; i++) {{
        const ts = start + (end - start) * i / 6;
        const xx = x(ts);
        ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--border') || '#d8e1e8';
        ctx.beginPath();
        ctx.moveTo(xx, h - pad.bottom);
        ctx.lineTo(xx, h - pad.bottom + 5);
        ctx.stroke();
        ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--muted') || '#6b7a86';
        ctx.textAlign = i === 0 ? 'left' : i === 6 ? 'right' : 'center';
        ctx.fillText(dateFmt.format(new Date(ts)), xx, h - 14);
      }}
      ctx.textAlign = 'left';
      const colors = ['#18b893', '#2563eb', '#f97316'];
      const color = colors[tariffIndex] || '#18b893';
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.moveTo(x(start), y(curve.initial));
      curve.data.forEach(point => ctx.lineTo(x(point.time), y(point.equity)));
      ctx.stroke();
    }}
    window.addEventListener('resize', resize);
    resize();
  </script>
</body>
</html>
"""


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="")
    args = parser.parse_args()
    end = datetime.fromisoformat(args.end).astimezone(timezone.utc) if args.end else datetime.now(timezone.utc)
    start, end, rows = await fetch_all(args.days, end)
    candidates = all_signal_candidates(start, rows)
    results = [run_portfolio(tariff, candidates, rows) for tariff in TARIFFS]
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
