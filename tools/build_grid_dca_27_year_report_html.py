"""Build the GRID DCA 2.7 yearly tariff report HTML from computed JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "webapp/static/reports/grid-dca-27-year-tariffs.json"
HTML_PATH = ROOT / "webapp/static/reports/grid-dca-27-year-tariffs.html"
STRATEGY_LABEL = "GRID DCA 2.7"


def fmt(value: float, digits: int = 2) -> str:
    return "∞" if value == float("inf") else f"{float(value):.{digits}f}"


def signed(value: float, digits: int = 2) -> str:
    return f"{float(value):+.{digits}f}"


def css_num(value: float) -> str:
    value = float(value)
    return "pos" if value > 0 else "neg" if value < 0 else ""


def dt(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime("%d.%m.%Y %H:%M")


def build_html(data: dict) -> str:
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

    by_tariff = [{row["symbol"]: row for row in tariff["by_pair"]} for tariff in data["tariffs"]]
    pair_rows = []
    for symbol in data["pairs"]:
        cells = [f"<td><strong>{symbol}</strong></td>"]
        for tariff_index, rows in enumerate(by_tariff):
            row = rows.get(symbol)
            sep_class = " tariff-sep" if tariff_index > 0 else ""
            if row:
                cells.append(f"<td class=\"{sep_class.strip()}\">{row['trades']}</td><td class=\"{css_num(row['pnl'])}\">{signed(row['pnl'])}</td>")
            else:
                cells.append(f"<td class=\"{sep_class.strip()}\">-</td><td>-</td>")
        pair_rows.append(f"<tr>{''.join(cells)}</tr>")

    worst_rows = []
    for tariff_index, tariff in enumerate(data["tariffs"]):
        for trade_index, trade in enumerate(tariff["worst_trades"][:5]):
            row_class = " class=\"tariff-break\"" if tariff_index > 0 and trade_index == 0 else ""
            side = "лонг" if trade["side"] == "long" else "шорт"
            worst_rows.append(f"""
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

    chart_cards = []
    period_start = data["period"]["start"][:10]
    period_end = data["period"]["end"][:10]
    for index, tariff in enumerate(data["tariffs"]):
        metrics = tariff["metrics"]
        chart_cards.append(f"""
        <article class="panel">
          <div class="chart-head">
            <div>
              <h2>{tariff['name']}</h2>
              <small class="muted">{period_start} - {period_end}</small>
            </div>
            <strong class="{css_num(metrics['pnl'])}">{signed(metrics['pnl'])} USDT</strong>
          </div>
          <canvas class="report-chart tariff-equity-chart" data-tariff-index="{index}"></canvas>
        </article>
        """)

    data_json = json.dumps(data, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Годовой бэктест {STRATEGY_LABEL} · Griders</title>
  <meta name="description" content="Годовой бэктест стратегии {STRATEGY_LABEL} по тарифам Griders: прибыль, просадка, сделки, комиссии, стоп-лоссы и результаты по торговым парам.">
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
        <p class="eyebrow">{STRATEGY_LABEL}</p>
        <h1>Годовой бэктест по тарифам</h1>
        <p class="muted">Портфельная симуляция за период {period_start} - {period_end}. Все разрешённые пары работают одновременно, а лимиты тарифа применяются к общей очереди сигналов.</p>
      </div>
      <div class="report-date"><span>Сигналов-кандидатов</span><strong>{data['signal_candidates']}</strong></div>
    </section>
    <section class="report-grid three">{''.join(cards)}</section>
    <section class="tariff-charts">{''.join(chart_cards)}</section>
    <section class="panel">
      <h2>PnL по парам</h2>
      <div class="table-scroll"><table><thead><tr><th>Пара</th><th>Сделок Free</th><th>PnL Free</th><th class="tariff-sep">Сделок Start</th><th>PnL Start</th><th class="tariff-sep">Сделок Premium</th><th>PnL Premium</th></tr></thead><tbody>{''.join(pair_rows)}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>Худшие сделки</h2>
      <div class="table-scroll"><table><thead><tr><th>Тариф</th><th>Пара</th><th>Сторона</th><th>Стадия</th><th>Вход UTC</th><th>Выход</th><th>PnL</th></tr></thead><tbody>{''.join(worst_rows)}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>Допущения расчёта</h2>
      <ul class="clean-list">
        <li>Источник данных: публичные свечи Bybit linear futures. Таймфрейм входа - 15 минут.</li>
        <li>Часовой RSI рассчитывается из часовых закрытий, собранных из 15-минутных свечей.</li>
        <li>{STRATEGY_LABEL} использует базовые сигналы 2.6 и блокирует лонги во время дневного downtrend BTC/ETH, а шорты во время дневного uptrend BTC/ETH.</li>
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
    function drawAll() {{ canvases.forEach((canvas) => draw(canvas, Number(canvas.dataset.tariffIndex || 0))); }}
    function draw(canvas, index) {{
      const tariff = reportData.tariffs[index];
      const points = tariff.equity_curve || [];
      const ctx = canvas.getContext('2d');
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);
      if (!points.length) return;
      const pad = 28;
      const xs = points.map(p => p.time);
      const ys = points.map(p => p.equity);
      const minX = Math.min(...xs), maxX = Math.max(...xs);
      const minY = Math.min(...ys, tariff.settings.initial_deposit);
      const maxY = Math.max(...ys, tariff.settings.initial_deposit);
      const spanX = Math.max(1, maxX - minX);
      const spanY = Math.max(1e-9, maxY - minY);
      function x(v) {{ return pad + (v - minX) / spanX * (w - pad * 2); }}
      function y(v) {{ return h - pad - (v - minY) / spanY * (h - pad * 2); }}
      ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--line') || '#d7dee8';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(pad, y(tariff.settings.initial_deposit)); ctx.lineTo(w - pad, y(tariff.settings.initial_deposit)); ctx.stroke();
      ctx.strokeStyle = tariff.metrics.pnl >= 0 ? '#047857' : '#b42318';
      ctx.lineWidth = 2;
      ctx.beginPath();
      points.forEach((p, i) => {{
        const px = x(p.time), py = y(p.equity);
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }});
      ctx.stroke();
      ctx.fillStyle = '#5b667a';
      ctx.font = '12px Inter, system-ui, sans-serif';
      ctx.fillText(minY.toFixed(2) + ' USDT', pad, h - 8);
      ctx.fillText(maxY.toFixed(2) + ' USDT', pad, 16);
    }}
    window.addEventListener('resize', resize);
    resize();
  </script>
</body>
</html>"""


def main() -> None:
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    HTML_PATH.write_text(build_html(data), encoding="utf-8")
    print(HTML_PATH)


if __name__ == "__main__":
    main()
