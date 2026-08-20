"""Сборка страницы реалистичного бектеста GRID DCA 2.10 по 6 тарифам.

Использует результат откалиброванного бектеста (.private_reports/grid-dca-29-independent-realistic-year.json)
и генерирует HTML-страницу в стиле существующего отчёта
webapp/static/reports/grid-dca-29-realistic-live-calibrated.html —
с hero, блоком «проверка правдивости», 6 карточками тарифов и PnL-графиками.

Запуск:
  python tools/build_realistic_backtest_report.py
  (предварительно прогнать: backtest_grid_dca_29_independent.py --label realistic-year --funding --model-unknown)
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_JSON = ROOT / ".private_reports/grid-dca-29-independent-v210-final.json"
OUT_HTML = ROOT / "webapp/static/reports/grid-dca-210-realistic.html"

TARIFF_NAMES = {
    "free": "Бесплатный",
    "free_plus": "Бесплатный Плюс",
    "start": "Старт",
    "start_plus": "Старт Плюс",
    "premium": "Премиум",
    "premium_plus": "Премиум Плюс",
}


def fmt(v, n=2):
    try:
        return f"{float(v):.{n}f}"
    except Exception:
        return str(v)


def build_card(t: dict) -> str:
    m = t["metrics"]
    code = t["code"]
    name = TARIFF_NAMES.get(code, code)
    pnl = float(m["pnl"])
    cls = "pos" if pnl >= 0 else "neg"
    deposit = float(m["final_deposit"])
    ret = float(m["return_pct"])
    ret_cls = "pos" if ret >= 0 else "neg"
    daily_json = json.dumps(m.get("daily", []), ensure_ascii=False)
    pairs_n = len(t.get("pairs", []))
    daily_id = f"chart-{code}"
    return f"""
        <article class="panel report-card">
          <p class="eyebrow">{name}</p>
          <h2 class="{cls}">{'+' if pnl>=0 else ''}{fmt(pnl)} USDT</h2>
          <span class="metric-sub">Откалиброванный PnL (фандинг + проскальзывание + unknown-сделки)</span>
          <div class="report-mini-grid">
            <div><span>Итоговый депозит</span><strong>{fmt(deposit)} USDT</strong></div>
            <div><span>Доходность</span><strong class="{ret_cls}">{'+' if ret>=0 else ''}{fmt(ret)}%</strong></div>
            <div><span>Сделок</span><strong>{m['trades']}</strong></div>
            <div><span>Win rate</span><strong>{fmt(m['win_rate'],1)}%</strong></div>
            <div><span>Стопов</span><strong>{m['stops']}</strong></div>
            <div><span>Макс. просадка</span><strong>{fmt(m['max_drawdown'])} USDT ({fmt(m['max_drawdown_pct'],1)}%)</strong></div>
            <div><span>Первый ордер (макс)</span><strong>{fmt(t.get('max_first_order') or 0)} USDT</strong></div>
            <div><span>Profit factor</span><strong>{fmt(m['profit_factor'])}</strong></div>
          </div>
          <p class="form-note">Депозит: {fmt(t.get('initial_deposit') or 0)} USDT. Лимиты: {t.get('max_total','—')} всего. Пары: {pairs_n}.</p>
          <div class="chart-head"><h2>PnL по дням</h2></div>
          <div class="monitor-chart-card report-monitor-card" data-chart="{daily_id}">
            <script type="application/json" data-chart-data>{daily_json}</script>
            <svg class="monitor-svg report-svg" viewBox="0 0 920 360" role="img" aria-label="PnL по дням {name}"></svg>
            <div class="monitor-tooltip" hidden></div>
          </div>
        </article>"""


def main() -> None:
    if not SRC_JSON.exists():
        raise SystemExit(f"Не найден {SRC_JSON}. Сначала прогоните backtest_grid_dca_29_independent.py --label realistic-year --funding --model-unknown")
    report = json.loads(SRC_JSON.read_text(encoding="utf-8"))
    period = report.get("period", {})
    pairs = report.get("pairs", [])
    cards = "\n".join(build_card(t) for t in report["tariffs"])
    assumptions = report.get("assumptions", {})

    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>Реалистичный бэктест GRID DCA 2.10 по 6 тарифам · Griders</title>
  <meta name="description" content="Откалиброванный реалистичный бэктест GRID DCA 2.10: годовая свечная модель с фандингом, проскальзыванием и unknown-сделками, приближенная к live-торговле.">
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="stylesheet" href="/static/app.css?v=20260806-realistic-independent">
  <style>
    .report-page {{ padding-top: 32px; padding-bottom: 48px; }}
    .report-hero {{ display:flex; justify-content:space-between; gap:24px; align-items:flex-start; }}
    .report-grid.six {{ display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:16px; }}
    .report-card h2 {{ margin: 8px 0 10px; font-size: 32px; }}
    .metric-sub {{ display:block; margin-bottom:14px; color:var(--text-muted); font-size:14px; }}
    .report-monitor-card {{ height: 360px; margin-top: 12px; }}
    .chart-head {{ display:flex; justify-content:space-between; gap:16px; align-items:center; margin: 16px 0 4px; }}
    .chart-head h2 {{ margin:0; font-size: 18px; }}
    .table-scroll {{ overflow:auto; }}
    .calibration-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .calibration-grid div {{ border:1px solid var(--line); border-radius:8px; padding:12px; background:var(--bg-soft); }}
    .calibration-grid span {{ display:block; color:var(--text-muted); font-size:12px; }}
    .calibration-grid strong {{ display:block; margin-top:4px; font-size:20px; }}
    .pos {{ color: var(--accent-dark); }}
    .neg {{ color: var(--warn); }}
    .disclaimer {{ border-left: 4px solid var(--warn); padding: 14px 18px; background: var(--bg-soft); border-radius: 8px; }}
    @media (max-width: 900px) {{
      .report-hero {{ display:block; }}
      .report-grid.six, .calibration-grid {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <main class="container report-page">
    <section class="panel report-hero">
      <div>
        <p class="eyebrow">GRID DCA 2.10 · ОТКАЛИБРОВАННАЯ МОДЕЛЬ</p>
        <h1>Реалистичный бэктест по 6 тарифам</h1>
        <p class="muted">Период: {period.get('start','')[:10]} — {period.get('end','')[:10]} ({period.get('days','—')} дней). {len(pairs)} пар. {report.get('signal_candidates','—')} сигналов-кандидатов.</p>
        <p class="muted">Свечная модель 15m с DCA-сеткой, усреднением и TP/SL от средней цены. В отличие от раннего бэктеста, здесь учтены реальные факторы live-торговли, выявленные сверкой с фактическими сделками Griders.</p>
      </div>
      <div class="report-date"><span>Сигналов-кандидатов</span><strong>{report.get('signal_candidates','—')}</strong></div>
    </section>

    <section class="panel">
      <h2>Проверка правдивости: что добавлено против наивного бэктеста</h2>
      <p class="muted">Наивная свечная модель давала Profit Factor ~2.9 и выглядела слишком хорошо. Сверка с 22 000+ реальных сделок за тот же период показала реальный PF ~0.96. Эта страница — результат откалиброванной модели, которая приближается к реальности за счёт четырёх поправок.</p>
      <div class="calibration-grid">
        <div><span>Проскальзывание (рыночные ордера)</span><strong>0.10%</strong></div>
        <div><span>Фандинг Bybit (каждые 8ч)</span><strong>учтён</strong></div>
        <div><span>Фантомные unknown-сделки</span><strong>19%</strong></div>
        <div><span>Timing входа</span><strong>UTC + сигнал. свеча</strong></div>
      </div>
    </section>

    <section class="report-grid six">
{cards}
    </section>

    <section class="panel disclaimer" style="margin-top:24px;">
      <h2 style="margin-top:0;">Важно про риски</h2>
      <p class="muted" style="margin-bottom:0;">Результаты бэктеста не гарантируют будущих результатов. Stop loss — нормальная часть стратегии. Реальные торги включают funding, проскальзывание, задержки webhook и риск ликвидации при плече ×10, которые могут ухудшить исполнение. PF 1.0–1.5 на откалиброванной модели — это реалистичный, а не гарантированный диапазон.</p>
    </section>
  </main>

<script>
(function () {{
  document.querySelectorAll('[data-chart]').forEach(function (root) {{
    var dataEl = root.querySelector('[data-chart-data]');
    if (!dataEl) return;
    var data = JSON.parse(dataEl.textContent || '[]');
    if (!data.length) return;
    var svg = root.querySelector('svg');
    if (!svg) return;
    var width = 920, height = 360;
    var margin = {{left: 56, right: 16, top: 16, bottom: 36}};
    var plotW = width - margin.left - margin.right;
    var plotH = height - margin.top - margin.bottom;
    var plotTop = margin.top, barBottom = margin.top + plotH;
    svg.innerHTML = '';
    function el(name, attrs) {{
      var e = document.createElementNS('http://www.w3.org/2000/svg', name);
      for (var k in attrs) e.setAttribute(k, attrs[k]);
      svg.appendChild(e); return e;
    }}
    var pnls = data.map(function(d) {{ return Number(d.cumulative || 0); }});
    var min = Math.min.apply(null, pnls.concat([0]));
    var max = Math.max.apply(null, pnls.concat([0]));
    if (max === min) {{ max = min + 1; }}
    var xAt = function(i) {{ return margin.left + (plotW / Math.max(1, data.length - 1)) * i; }};
    var yAt = function(v) {{ return barBottom - ((v - min) / (max - min)) * plotH; }};
    el('rect', {{x: margin.left, y: plotTop, width: plotW, height: barBottom - plotTop, class: 'monitor-chart-bg', fill: '#fbfdfc'}});
    el('line', {{x1: margin.left, x2: margin.left + plotW, y1: yAt(0), y2: yAt(0), class: 'monitor-zero-line', stroke: '#cbd9d4', 'stroke-width': 1, 'stroke-dasharray': '4 4'}});
    var points = data.map(function(d, i) {{ return xAt(i) + ',' + yAt(Number(d.cumulative || 0)); }}).join(' ');
    el('polyline', {{points: points, fill: 'none', class: 'monitor-equity-line', stroke: '#00856f', 'stroke-width': 3, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}});
    var focus = el('circle', {{r: 5, class: 'monitor-focus-dot', fill: '#fff', stroke: '#00856f', 'stroke-width': 3, opacity: 0}});
    var hoverLine = el('line', {{y1: plotTop, y2: barBottom, class: 'monitor-hover-line', stroke: '#7b8d94', 'stroke-width': 1, 'stroke-dasharray': '4 4', opacity: 0}});
    var hit = el('rect', {{x: margin.left, y: plotTop, width: plotW, height: barBottom - plotTop, fill: 'transparent'}});
    var tooltip = root.querySelector('.monitor-tooltip');
    var showAt = function(clientX) {{
      var rect = svg.getBoundingClientRect();
      var x = ((clientX - rect.left) / rect.width) * width;
      var raw = ((x - margin.left) / plotW) * (data.length - 1);
      var i = Math.max(0, Math.min(data.length - 1, Math.round(raw)));
      var d = data[i];
      var px = xAt(i), py = yAt(Number(d.cumulative || 0));
      hoverLine.setAttribute('x1', px); hoverLine.setAttribute('x2', px); hoverLine.setAttribute('opacity', '0.8');
      focus.setAttribute('cx', px); focus.setAttribute('cy', py); focus.setAttribute('opacity', '1');
      if (tooltip) {{
        tooltip.hidden = false;
        tooltip.innerHTML = '<strong>' + (d.date) + '</strong><br>Кумул.: ' + (Number(d.cumulative||0).toFixed(2)) + ' USDT<br>День: ' + (Number(d.pnl||0).toFixed(2)) + ' USDT<br>Сделок: ' + (d.trades||0);
        tooltip.style.left = Math.min(Math.max((px / width) * rect.width + 10, 8), rect.width - 220) + 'px';
        tooltip.style.top = Math.max((py / height) * rect.height - 28, 8) + 'px';
      }}
    }};
    hit.addEventListener('mousemove', function(e) {{ showAt(e.clientX); }});
    hit.addEventListener('mouseleave', function() {{ hoverLine.setAttribute('opacity','0'); focus.setAttribute('opacity','0'); if (tooltip) tooltip.hidden = true; }});
  }});
}})();
</script>
</body>
</html>
"""
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"Страница: {OUT_HTML.resolve()}")
    print(f"URL (приватный): https://griders.ru/static/reports/grid-dca-29-independent-realistic.html")


if __name__ == "__main__":
    main()
