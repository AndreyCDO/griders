"""Build a public-ready GRID DCA 2.8 yearly report for all six tariffs."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_27_ema20_no_side_cooldown_base_tp import (
    all_signal_candidates_base_tp_with_server_guard,
)
from tools.build_grid_dca_28_year_report import (
    STRATEGY_LABEL,
    cards_html,
    chart_cards_html,
    css_num,
    day_chart,
    fmt,
    pair_rows_html,
    report_js,
    signed,
    worst_rows_html,
)


ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "webapp/static/reports/grid-dca-28-year-all-tariffs.json"
HTML_PATH = ROOT / "webapp/static/reports/grid-dca-28-year-all-tariffs.html"


def all_tariffs() -> list[base.Tariff]:
    free_pairs = [pair for pair in base.ALL_PAIRS if pair not in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}]
    start_pairs = [pair for pair in base.ALL_PAIRS if pair != "BTCUSDT"]
    all_pairs = base.ALL_PAIRS[:]
    return [
        base.Tariff(
            code="free",
            name="Бесплатный",
            initial_deposit=50.0,
            pairs=free_pairs,
            max_total=4,
            max_long=4,
            max_short=4,
            first_order_mode="manual",
            manual_first_order=6.0,
            max_first_order=6.0,
        ),
        base.Tariff(
            code="free_plus",
            name="Бесплатный Плюс",
            initial_deposit=100.0,
            pairs=free_pairs,
            max_total=6,
            max_long=6,
            max_short=6,
            first_order_mode="deposit_pct",
            risk_pct=5.0,
            max_first_order=12.0,
        ),
        base.Tariff(
            code="start",
            name="Старт",
            initial_deposit=500.0,
            pairs=start_pairs,
            max_total=8,
            max_long=8,
            max_short=8,
            first_order_mode="deposit_pct",
            risk_pct=5.0,
            max_first_order=60.0,
        ),
        base.Tariff(
            code="start_plus",
            name="Старт Плюс",
            initial_deposit=1000.0,
            pairs=all_pairs,
            max_total=10,
            max_long=10,
            max_short=10,
            first_order_mode="deposit_pct",
            risk_pct=5.0,
            max_first_order=120.0,
        ),
        base.Tariff(
            code="premium",
            name="Премиум",
            initial_deposit=5000.0,
            pairs=all_pairs,
            max_total=12,
            max_long=12,
            max_short=12,
            first_order_mode="deposit_pct",
            risk_pct=5.0,
            max_first_order=600.0,
        ),
        base.Tariff(
            code="premium_plus",
            name="Премиум Плюс",
            initial_deposit=10000.0,
            pairs=all_pairs,
            max_total=40,
            max_long=40,
            max_short=40,
            first_order_mode="deposit_pct",
            risk_pct=5.0,
            max_first_order=2000.0,
        ),
    ]


def pair_header_html(data: dict) -> str:
    cells = ["<th>Пара</th>"]
    for index, tariff in enumerate(data["tariffs"]):
        sep = ' class="tariff-sep"' if index else ""
        name = tariff["name"]
        cells.append(f"<th{sep}>Сделок {name}</th><th>PnL {name}</th>")
    return "".join(cells)


def tariff_ordered_cards_html(data: dict) -> str:
    order = ["free", "start", "premium", "free_plus", "start_plus", "premium_plus"]
    by_code = {tariff["code"]: tariff for tariff in data["tariffs"]}
    ordered = [by_code[code] for code in order if code in by_code]
    return cards_html({**data, "tariffs": ordered})


def build_html(data: dict) -> str:
    period_start = data["period"]["start"][:10]
    period_end = data["period"]["end"][:10]
    best = max(data["tariffs"], key=lambda item: float(item["metrics"]["pnl"]))
    html = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Годовой бэктест __STRATEGY__ по 6 тарифам · Griders</title>
  <meta name="description" content="Годовой бэктест стратегии __STRATEGY__ по 6 тарифам Griders: прибыль, просадка, сделки, комиссии, стоп-лоссы и результаты по торговым парам.">
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="stylesheet" href="/static/app.css?v=20260628-grid-dca-28-report">
  <style>
    .report-page { padding-top: 32px; padding-bottom: 48px; }
    .report-hero { display:flex; justify-content:space-between; gap:24px; align-items:flex-start; }
    .report-grid.six { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:16px; }
    .report-card h2 { margin: 8px 0 16px; font-size: 32px; }
    .tariff-charts { display:grid; grid-template-columns:1fr; gap:16px; }
    .chart-head { display:flex; justify-content:space-between; gap:16px; align-items:center; margin-bottom:12px; }
    .chart-head h2 { margin:0; }
    .report-monitor-card { height: 360px; }
    .tariff-sep { border-left: 2px solid var(--line); }
    tr.tariff-break td { border-top: 3px solid var(--line); }
    .table-scroll { overflow:auto; }
    .pos { color: var(--accent-dark); }
    .neg { color: var(--warn); }
    @media (max-width: 900px) {
      .report-hero { display:block; }
      .report-grid.six { grid-template-columns:1fr; }
    }
  </style>
</head>
<body>
  <main class="container report-page">
    <section class="panel report-hero">
      <div>
        <p class="eyebrow">__STRATEGY__</p>
        <h1>Годовой бэктест по 6 тарифам</h1>
        <p class="muted">Портфельная симуляция за период __PERIOD_START__ - __PERIOD_END__. Все разрешённые пары работают одновременно, а лимиты каждого тарифа применяются к общей очереди сигналов.</p>
      </div>
      <div class="report-date"><span>Сигналов-кандидатов</span><strong>__SIGNAL_CANDIDATES__</strong></div>
    </section>
    <section class="panel">
      <h2>Краткий вывод</h2>
      <p class="muted">Лучший результат в этом прогоне: <strong>__BEST_TARIFF__</strong>, PnL <strong class="__BEST_CLASS__">__BEST_PNL__ USDT</strong>, profit factor <strong>__BEST_PF__</strong>.</p>
    </section>
    <section class="report-grid six">__CARDS__</section>
    <section class="tariff-charts">__CHARTS__</section>
    <section class="panel">
      <h2>PnL по парам</h2>
      <div class="table-scroll">
        <table>
          <thead><tr>__PAIR_HEADER__</tr></thead>
          <tbody>__PAIR_ROWS__</tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <h2>Худшие сделки</h2>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Тариф</th><th>Пара</th><th>Сторона</th><th>Стадия</th><th>Вход UTC</th><th>Выход</th><th>PnL</th></tr></thead>
          <tbody>__WORST_ROWS__</tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <h2>Допущения расчёта</h2>
      <ul class="clean-list">
        <li>Источник данных: публичные свечи Bybit linear futures. Таймфрейм входа - 15 минут.</li>
        <li>__STRATEGY__ использует базовые сигналы GRID DCA и текущий фильтр дневного тренда BTC/ETH: лонги блокируются в downtrend, шорты блокируются в uptrend; дополнительно серверный EMA20 guard отсекает сделки против дневного положения BTC и ETH.</li>
        <li>Вход считается по открытию следующей 15-минутной свечи после подтверждённого сигнала TradingView.</li>
        <li>Первый ордер для тарифов с расчётом от депозита считается по 5% на сделку и ограничивается максимумом тарифа.</li>
        <li>Перед открытием новой сделки проверяется плановая маржа: сумма маржи уже открытых сеток и новой сетки не должна превышать текущий депозит тарифа.</li>
        <li>Комиссия: taker 0.05% на первый ордер, страховочные ордера и выход.</li>
        <li>Если внутри одной свечи одновременно могли сработать TP и SL, засчитывается SL как более осторожный сценарий.</li>
        <li>После стоп-лосса применяется пауза GRID DCA на 3 часа.</li>
        <li>Проскальзывание, funding, задержки webhook и возможные отказы Cryptorg/биржи не учитываются.</li>
        <li><strong>Предупреждение:</strong> прибыль в прошлом не означает прибыль в будущем. Данный бэктест не гарантирует дохода по этой стратегии.</li>
      </ul>
    </section>
  </main>
  __SCRIPT__
</body>
</html>"""
    replacements = {
        "__STRATEGY__": STRATEGY_LABEL,
        "__PERIOD_START__": period_start,
        "__PERIOD_END__": period_end,
        "__SIGNAL_CANDIDATES__": str(data["signal_candidates"]),
        "__BEST_TARIFF__": best["name"],
        "__BEST_CLASS__": css_num(best["metrics"]["pnl"]),
        "__BEST_PNL__": signed(best["metrics"]["pnl"]),
        "__BEST_PF__": fmt(best["metrics"]["profit_factor"]),
        "__CARDS__": tariff_ordered_cards_html(data),
        "__CHARTS__": chart_cards_html(data),
        "__PAIR_HEADER__": pair_header_html(data),
        "__PAIR_ROWS__": pair_rows_html(data),
        "__WORST_ROWS__": worst_rows_html(data),
        "__SCRIPT__": report_js(),
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return html


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    start, end, rows = await base.fetch_all(args.days, end)
    candidates, candidate_skipped = all_signal_candidates_base_tp_with_server_guard(start, rows)
    original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
    base.SIDE_WEBHOOK_COOLDOWN_MS = 0
    tariffs = all_tariffs()
    try:
        results = [base.run_portfolio(tariff, candidates, rows) for tariff in tariffs]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown

    data = base.result_to_json(start, end, candidates, results)
    data["strategy_label"] = STRATEGY_LABEL
    data["report_variant"] = {
        "code": "grid_dca_28_current_year_all_tariffs",
        "description": "Current GRID DCA 2.8 for all six tariff limits: base TP, EMA20/global trend guard, no same-side cooldown.",
        "take_profit_multiplier": 1.0,
        "side_webhook_cooldown_ms": 0,
        "candidate_skipped": candidate_skipped,
    }
    for tariff, result in zip(data["tariffs"], results):
        tariff["daily_chart"] = day_chart(start, end, result["trades"])

    JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    HTML_PATH.write_text(build_html(data), encoding="utf-8")
    print(json.dumps({
        "html": str(HTML_PATH.resolve()),
        "json": str(JSON_PATH.resolve()),
        "period": data["period"],
        "signal_candidates": data["signal_candidates"],
        "metrics": [
            {
                tariff["code"]: {
                    "trades": tariff["metrics"]["trades"],
                    "pnl": tariff["metrics"]["pnl"],
                    "return_pct": tariff["metrics"]["return_pct"],
                    "profit_factor": tariff["metrics"]["profit_factor"],
                    "stops": tariff["metrics"]["stops"],
                    "max_drawdown": tariff["metrics"]["max_drawdown"],
                }
            }
            for tariff in data["tariffs"]
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
