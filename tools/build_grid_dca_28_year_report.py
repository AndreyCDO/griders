"""Build a public-ready GRID DCA 2.8 yearly tariff report without linking it."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_27_ema20_no_side_cooldown_base_tp import (
    all_signal_candidates_base_tp_with_server_guard,
)
from tools.backtest_grid_dca_27_wide_limits_tp5_ema20_guard import TARIFFS


ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "webapp/static/reports/grid-dca-28-year-tariffs.json"
HTML_PATH = ROOT / "webapp/static/reports/grid-dca-28-year-tariffs.html"
STRATEGY_LABEL = "GRID DCA 2.8"


def fmt(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "∞"
    value = float(value)
    if math.isinf(value):
        return "∞"
    return f"{value:.{digits}f}"


def signed(value: float, digits: int = 2) -> str:
    return f"{float(value):+.{digits}f}"


def css_num(value: float) -> str:
    value = float(value)
    return "pos" if value > 0 else "neg" if value < 0 else ""


def dt(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime("%d.%m.%Y %H:%M")


def day_chart(start: datetime, end: datetime, trades: list[dict]) -> list[dict]:
    start_date = start.date()
    end_date = end.date()
    days: dict[str, dict] = {}
    current = start_date
    while current <= end_date:
        key = current.isoformat()
        days[key] = {"date": key, "pnl": 0.0, "trades": 0}
        current += timedelta(days=1)

    for trade in trades:
        closed = datetime.fromtimestamp(int(trade["exit_time"]) / 1000, tz=timezone.utc).date().isoformat()
        if closed in days:
            days[closed]["pnl"] += float(trade["pnl"])
            days[closed]["trades"] += 1

    cumulative = 0.0
    rows = []
    for key in sorted(days):
        item = days[key]
        cumulative += float(item["pnl"])
        day = datetime.fromisoformat(key)
        rows.append({
            "date": day.strftime("%d.%m.%Y"),
            "label": day.strftime("%d.%m"),
            "pnl": round(float(item["pnl"]), 6),
            "pnlText": f"{float(item['pnl']):+.2f} USDT",
            "cumulative": round(cumulative, 6),
            "cumulativeText": f"{cumulative:+.2f} USDT",
            "trades": int(item["trades"]),
        })
    return rows


def cards_html(data: dict) -> str:
    cards = []
    for tariff in data["tariffs"]:
        metrics = tariff["metrics"]
        settings = tariff["settings"]
        cards.append(f"""
        <article class="panel report-card">
          <p class="eyebrow">{tariff['name']}</p>
          <h2 class="{css_num(metrics['pnl'])}">{signed(metrics['pnl'])} USDT</h2>
          <div class="report-mini-grid">
            <div><span>Итоговый депозит</span><strong>{fmt(metrics['final_deposit'])} USDT</strong></div>
            <div><span>Доходность</span><strong class="{css_num(metrics['return_pct'])}">{signed(metrics['return_pct'])}%</strong></div>
            <div><span>Сделок</span><strong>{metrics['trades']}</strong></div>
            <div><span>Win rate</span><strong>{fmt(metrics['win_rate'])}%</strong></div>
            <div><span>Стопов</span><strong>{metrics['stops']}</strong></div>
            <div><span>Макс. просадка</span><strong>{fmt(metrics['max_drawdown'])} USDT ({fmt(metrics['max_drawdown_pct'])}%)</strong></div>
            <div><span>Средний первый ордер</span><strong>{fmt(metrics['avg_first_order'])} USDT</strong></div>
            <div><span>Profit factor</span><strong>{fmt(metrics['profit_factor'])}</strong></div>
          </div>
          <p class="form-note">Депозит: {fmt(settings['initial_deposit'])} USDT. Лимиты: {settings['max_total']} всего / {settings['max_long']} лонг / {settings['max_short']} шорт. Пары: {len(settings['pairs'])}.</p>
        </article>
        """)
    return "".join(cards)


def chart_cards_html(data: dict) -> str:
    period_start = data["period"]["start"][:10]
    period_end = data["period"]["end"][:10]
    cards = []
    for index, tariff in enumerate(data["tariffs"]):
        metrics = tariff["metrics"]
        cards.append(f"""
        <article class="panel">
          <div class="chart-head">
            <div>
              <h2>{tariff['name']}</h2>
              <small class="muted">{period_start} - {period_end}</small>
            </div>
            <strong class="{css_num(metrics['pnl'])}">{signed(metrics['pnl'])} USDT</strong>
          </div>
          <div class="monitor-chart-card report-monitor-card" data-empty="Нет данных за выбранный период">
            <script type="application/json" class="report-chart-data">{json.dumps(tariff['daily_chart'], ensure_ascii=False)}</script>
            <svg class="monitor-svg report-monitor-svg" viewBox="0 0 920 360" role="img" aria-label="График PnL и сделок {tariff['name']}"></svg>
            <div class="monitor-tooltip" hidden></div>
          </div>
        </article>
        """)
    return "".join(cards)


def pair_rows_html(data: dict) -> str:
    by_tariff = [{row["symbol"]: row for row in tariff["by_pair"]} for tariff in data["tariffs"]]
    rows = []
    for symbol in data["pairs"]:
        cells = [f"<td><strong>{symbol}</strong></td>"]
        for tariff_index, item_by_pair in enumerate(by_tariff):
            row = item_by_pair.get(symbol)
            sep_class = " tariff-sep" if tariff_index > 0 else ""
            if row:
                cells.append(f"<td class=\"{sep_class.strip()}\">{row['trades']}</td><td class=\"{css_num(row['pnl'])}\">{signed(row['pnl'])}</td>")
            else:
                cells.append(f"<td class=\"{sep_class.strip()}\">-</td><td>-</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return "".join(rows)


def worst_rows_html(data: dict) -> str:
    rows = []
    for tariff_index, tariff in enumerate(data["tariffs"]):
        for trade_index, trade in enumerate(tariff["worst_trades"][:5]):
            row_class = " class=\"tariff-break\"" if tariff_index > 0 and trade_index == 0 else ""
            side = "лонг" if trade["side"] == "long" else "шорт"
            rows.append(f"""
            <tr{row_class}>
              <td>{tariff['name']}</td>
              <td><strong>{trade['symbol']}</strong></td>
              <td>{side}</td>
              <td>{trade['stage']}</td>
              <td>{dt(int(trade['entry_time']))}</td>
              <td>{str(trade['exit_reason']).upper()}</td>
              <td class="{css_num(trade['pnl'])}">{signed(trade['pnl'])}</td>
            </tr>
            """)
    return "".join(rows)


def report_js() -> str:
    return r"""
<script>
(() => {
  const labels = {
    dayPnl: "PnL за день",
    cumulative: "PnL кумулятивный",
    trades: "Сделок",
  };
  const width = 920;
  const height = 360;
  const margin = { top: 22, right: 28, bottom: 48, left: 112 };
  const plotTop = 8;
  const plotBottom = 280;
  const barTop = 294;
  const barBottom = height - margin.bottom;
  const plotWidth = width - margin.left - margin.right;
  const ns = "http://www.w3.org/2000/svg";
  const formatMoney = (value) => `${Number(value || 0).toFixed(2)} USDT`;
  const formatAxisNumber = (value) => {
    const num = Number(value || 0);
    if (Math.abs(num - Math.round(num)) < 0.000001) return String(Math.round(num));
    const abs = Math.abs(num);
    return num.toFixed(abs >= 1 ? 1 : 2);
  };
  const niceStep = (range, targetTicks = 5) => {
    const rough = Math.max(0.000001, range / Math.max(1, targetTicks - 1));
    const power = 10 ** Math.floor(Math.log10(rough));
    const fraction = rough / power;
    return (fraction <= 1 ? 1 : (fraction <= 2 ? 2 : (fraction <= 5 ? 5 : 10))) * power;
  };
  const niceTicks = (min, max, targetTicks = 5) => {
    const step = niceStep(max - min, targetTicks);
    const start = Math.floor(min / step) * step;
    const end = Math.ceil(max / step) * step;
    const ticks = [];
    for (let value = start; value <= end + step * 0.5; value += step) ticks.push(Number(value.toFixed(10)));
    return { ticks, min: start, max: end };
  };
  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  }[ch]));

  document.querySelectorAll(".report-monitor-card").forEach((root) => {
    const svg = root.querySelector(".report-monitor-svg");
    const source = root.querySelector(".report-chart-data");
    if (!svg || !source) return;
    const data = JSON.parse(source.textContent || "[]");
    const el = (name, attrs = {}) => {
      const node = document.createElementNS(ns, name);
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
      svg.appendChild(node);
      return node;
    };
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!data.length) {
      el("text", { x: width / 2, y: height / 2, "text-anchor": "middle", class: "monitor-empty-label", fill: "#667680" }).textContent = root.dataset.empty || "Нет данных";
      return;
    }
    const cumulatives = data.map((item) => Number(item.cumulative || 0));
    const trades = data.map((item) => Number(item.trades || 0));
    let minY = Math.min(0, ...cumulatives);
    let maxY = Math.max(0, ...cumulatives);
    if (minY === maxY) { minY -= 1; maxY += 1; }
    const pad = (maxY - minY) * 0.14;
    const axis = niceTicks(minY - pad, maxY + pad, 7);
    minY = axis.min;
    maxY = axis.max;
    const maxTrades = Math.max(1, ...trades);
    const xAt = (index) => margin.left + (data.length === 1 ? plotWidth / 2 : (index / (data.length - 1)) * plotWidth);
    const yAt = (value) => plotBottom - ((value - minY) / (maxY - minY)) * (plotBottom - plotTop);
    const barHeight = (value) => (value / maxTrades) * (barBottom - barTop);

    el("rect", { x: 0, y: 0, width, height, rx: 10, class: "monitor-chart-bg", fill: "#fbfdfc" });
    axis.ticks.forEach((value) => {
      const y = yAt(value);
      el("line", { x1: margin.left, x2: width - margin.right, y1: y, y2: y, class: "monitor-grid-line", stroke: "#dfe8e4", "stroke-width": 1 });
      el("text", { x: margin.left - 18, y: y + 4, "text-anchor": "end", class: "monitor-axis-label", fill: "#667680" }).textContent = formatAxisNumber(value);
    });
    const startY = yAt(0);
    el("line", { x1: margin.left - 8, x2: width - margin.right, y1: startY, y2: startY, class: "monitor-start-line", stroke: "#99a7ad", "stroke-width": 1, "stroke-dasharray": "3 5", opacity: "0.8" });
    el("line", { x1: margin.left, x2: width - margin.right, y1: barBottom, y2: barBottom, class: "monitor-axis-line", stroke: "#cbd9d4", "stroke-width": 1 });
    el("text", { x: margin.left - 18, y: barTop + 18, "text-anchor": "end", class: "monitor-axis-label", fill: "#667680" }).textContent = labels.trades;

    const tickStep = Math.max(1, Math.ceil(data.length / 8));
    data.forEach((item, index) => {
      const x = xAt(index);
      if (index % tickStep === 0 || index === data.length - 1) {
        el("text", { x, y: height - 18, "text-anchor": "middle", class: "monitor-axis-label", fill: "#667680" }).textContent = item.label;
      }
      const h = barHeight(Number(item.trades || 0));
      el("rect", { x: x - 4, y: barBottom - h, width: 8, height: h, rx: 2, class: "monitor-trade-bar", fill: "#3b78a8", opacity: "0.42" });
    });

    const path = data.map((item, index) => `${index ? "L" : "M"} ${xAt(index).toFixed(2)} ${yAt(Number(item.cumulative || 0)).toFixed(2)}`).join(" ");
    el("path", { d: path, class: "monitor-pnl-line", fill: "none", stroke: "#0b8f70", "stroke-width": 3, "stroke-linecap": "round", "stroke-linejoin": "round" });
    const crosshair = el("line", { x1: 0, x2: 0, y1: plotTop, y2: barBottom, class: "monitor-crosshair", stroke: "#7b8d96", "stroke-width": 1, "stroke-dasharray": "4 4", hidden: "hidden" });
    const active = el("circle", { cx: 0, cy: 0, r: 7, class: "monitor-active-point", fill: "#1e9be0", stroke: "#ffffff", "stroke-width": 3, hidden: "hidden" });
    const tooltip = root.querySelector(".monitor-tooltip");
    const showAt = (event) => {
      const rect = svg.getBoundingClientRect();
      const mouseX = (event.clientX - rect.left) * (width / rect.width);
      let nearest = 0;
      let best = Infinity;
      data.forEach((_, index) => {
        const dist = Math.abs(xAt(index) - mouseX);
        if (dist < best) { best = dist; nearest = index; }
      });
      const item = data[nearest];
      const x = xAt(nearest);
      const y = yAt(Number(item.cumulative || 0));
      crosshair.removeAttribute("hidden");
      active.removeAttribute("hidden");
      crosshair.setAttribute("x1", x);
      crosshair.setAttribute("x2", x);
      active.setAttribute("cx", x);
      active.setAttribute("cy", y);
      tooltip.hidden = false;
      tooltip.innerHTML = `<strong>${escapeHtml(item.date)}</strong><span>${labels.dayPnl}: ${escapeHtml(item.pnlText || formatMoney(item.pnl))}</span><span>${labels.cumulative}: ${escapeHtml(item.cumulativeText || formatMoney(item.cumulative))}</span><span>${labels.trades}: ${escapeHtml(item.trades)}</span>`;
      tooltip.style.left = `${Math.min(rect.width - 220, Math.max(12, (x / width) * rect.width + 12))}px`;
      tooltip.style.top = `${Math.min(rect.height - 112, Math.max(10, (y / height) * rect.height - 58))}px`;
    };
    const hide = () => {
      crosshair.setAttribute("hidden", "hidden");
      active.setAttribute("hidden", "hidden");
      tooltip.hidden = true;
    };
    svg.addEventListener("pointermove", showAt);
    svg.addEventListener("pointerleave", hide);
  });
})();
</script>
"""


def build_html(data: dict) -> str:
    period_start = data["period"]["start"][:10]
    period_end = data["period"]["end"][:10]
    html = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Годовой бэктест __STRATEGY__ · Griders</title>
  <meta name="description" content="Годовой бэктест стратегии __STRATEGY__ по тарифам Griders: прибыль, просадка, сделки, комиссии, стоп-лоссы и результаты по торговым парам.">
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="stylesheet" href="/static/app.css?v=20260628-grid-dca-28-report">
  <style>
    .report-page { padding-top: 32px; padding-bottom: 48px; }
    .report-hero { display:flex; justify-content:space-between; gap:24px; align-items:flex-start; }
    .report-grid.three { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:16px; }
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
      .report-grid.three { grid-template-columns:1fr; }
    }
  </style>
</head>
<body>
  <main class="container report-page">
    <section class="panel report-hero">
      <div>
        <p class="eyebrow">__STRATEGY__</p>
        <h1>Годовой бэктест по тарифам</h1>
        <p class="muted">Портфельная симуляция за период __PERIOD_START__ - __PERIOD_END__. Все разрешённые пары работают одновременно, а лимиты тарифа применяются к общей очереди сигналов.</p>
      </div>
      <div class="report-date"><span>Сигналов-кандидатов</span><strong>__SIGNAL_CANDIDATES__</strong></div>
    </section>
    <section class="report-grid three">__CARDS__</section>
    <section class="tariff-charts">__CHARTS__</section>
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
        "__CARDS__": cards_html(data),
        "__CHARTS__": chart_cards_html(data),
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
    try:
        results = [base.run_portfolio(tariff, candidates, rows) for tariff in TARIFFS]
    finally:
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown

    data = base.result_to_json(start, end, candidates, results)
    data["strategy_label"] = STRATEGY_LABEL
    data["report_variant"] = {
        "code": "grid_dca_28_current_year_tariffs",
        "description": "Current GRID DCA 2.8: base TP, EMA20/global trend guard, no same-side cooldown.",
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
