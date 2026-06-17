"""Render a static visual report for GRID DCA 2.6 vs 3.1 backtest."""

from __future__ import annotations

from pathlib import Path


REPORT_DIR = Path("tools/backtest_reports")
REPORT_PATH = REPORT_DIR / "grid_dca_26_vs_31_20260605.html"


WINDOWS = [
    {"days": 14, "v26": {"trades": 737, "pnl": 12.1104, "win": 98.2361, "sl": 12, "skipped": 369}, "v31": {"trades": 348, "pnl": 2.5305, "win": 97.4138, "sl": 9, "skipped": 116}},
    {"days": 30, "v26": {"trades": 1362, "pnl": 21.8823, "win": 98.2379, "sl": 23, "skipped": 939}, "v31": {"trades": 706, "pnl": 5.6988, "win": 97.5921, "sl": 17, "skipped": 324}},
    {"days": 60, "v26": {"trades": 2255, "pnl": 29.0136, "win": 97.9157, "sl": 44, "skipped": 2126}, "v31": {"trades": 1265, "pnl": 8.1536, "win": 97.3913, "sl": 32, "skipped": 836}},
]


PAIR_30D = [
    ("BTCUSDT", 2.1519, 1.2194),
    ("ETHUSDT", 0.4551, -0.3096),
    ("SOLUSDT", 1.5873, 0.5783),
    ("HYPEUSDT", 2.7191, 0.3587),
    ("NEARUSDT", 1.4597, 1.1441),
    ("ZECUSDT", 2.9636, -0.7561),
    ("TONUSDT", 3.2880, -0.2204),
    ("XRPUSDT", 0.8930, 0.3765),
    ("SUIUSDT", -0.3581, 0.4975),
    ("DOGEUSDT", -1.0912, 0.8462),
    ("TAOUSDT", 1.6527, 1.0400),
    ("RENDERUSDT", -0.6968, 0.1471),
    ("ADAUSDT", -0.8745, 0.6239),
    ("INJUSDT", 1.4010, -0.3290),
    ("TIAUSDT", -1.4562, -2.6626),
    ("ENAUSDT", 1.9684, 1.1123),
    ("LINKUSDT", -1.2474, 0.5961),
    ("AVAXUSDT", -0.0887, 1.8607),
    ("DOTUSDT", -1.7852, -1.9621),
    ("ARBUSDT", -0.4133, -0.1623),
]


def fmt(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def signed(value: float) -> str:
    return f"{value:+.2f}"


def bars(metric: str, label: str, suffix: str = "", scale: float | None = None) -> str:
    max_value = scale or max(max(item["v26"][metric], item["v31"][metric]) for item in WINDOWS)
    rows = []
    for item in WINDOWS:
        v26 = item["v26"][metric]
        v31 = item["v31"][metric]
        rows.append(f"""
        <div class="bar-row">
          <div class="bar-label">{item['days']} дней</div>
          <div class="bar-pair">
            <div class="bar-line"><span class="bar v26" style="width:{max(v26 / max_value * 100, 2):.2f}%"></span><b>{fmt(v26)}{suffix}</b></div>
            <div class="bar-line"><span class="bar v31" style="width:{max(v31 / max_value * 100, 2):.2f}%"></span><b>{fmt(v31)}{suffix}</b></div>
          </div>
        </div>
        """)
    return f'<section class="panel"><h2>{label}</h2><div class="bars">{"".join(rows)}</div></section>'


def pair_table() -> str:
    rows = []
    for pair, v26, v31 in PAIR_30D:
        winner = "2.6" if v26 > v31 else "3.1"
        delta = v31 - v26
        rows.append(f"""
        <tr>
          <td><strong>{pair}</strong></td>
          <td class="{css_num(v26)}">{signed(v26)}</td>
          <td class="{css_num(v31)}">{signed(v31)}</td>
          <td><span class="pill {'blue' if winner == '2.6' else 'green'}">{winner}</span></td>
          <td class="{css_num(delta)}">{signed(delta)}</td>
        </tr>
        """)
    return "\n".join(rows)


def css_num(value: float) -> str:
    if value > 0:
        return "pos"
    if value < 0:
        return "neg"
    return ""


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GRID DCA 2.6 vs 3.1 · Backtest</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #eef3f1;
      --panel: #ffffff;
      --ink: #10202a;
      --muted: #64727e;
      --line: #dbe4e3;
      --green: #159b73;
      --green-soft: #dff5ec;
      --blue: #2477d4;
      --blue-soft: #e5f0ff;
      --red: #c74949;
      --amber: #b6791c;
      --shadow: 0 18px 50px rgba(24, 46, 58, .12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 15px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(36,119,212,.14), transparent 32rem),
        linear-gradient(180deg, #f7faf9, var(--bg));
    }}
    .wrap {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 36px 0 54px; }}
    header {{ display: flex; gap: 24px; align-items: end; justify-content: space-between; margin-bottom: 24px; }}
    h1 {{ margin: 0; font-size: clamp(28px, 4vw, 48px); letter-spacing: 0; line-height: 1.05; }}
    h2 {{ margin: 0 0 16px; font-size: 19px; }}
    .lead {{ max-width: 720px; color: var(--muted); margin: 12px 0 0; font-size: 16px; }}
    .stamp {{ color: var(--muted); text-align: right; }}
    .grid {{ display: grid; gap: 16px; }}
    .cards {{ grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 16px; }}
    .two {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .panel, .card {{
      background: rgba(255,255,255,.88);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .panel {{ padding: 22px; }}
    .card {{ padding: 18px; min-height: 126px; }}
    .kicker {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: .04em; }}
    .value {{ display: block; margin-top: 12px; font-size: 30px; font-weight: 800; }}
    .note {{ color: var(--muted); margin-top: 8px; }}
    .pos {{ color: var(--green); }}
    .neg {{ color: var(--red); }}
    .bars {{ display: grid; gap: 16px; }}
    .bar-row {{ display: grid; grid-template-columns: 86px 1fr; gap: 14px; align-items: center; }}
    .bar-label {{ color: var(--muted); font-weight: 700; }}
    .bar-pair {{ display: grid; gap: 8px; }}
    .bar-line {{ height: 30px; border-radius: 6px; background: #edf3f2; position: relative; overflow: hidden; }}
    .bar-line b {{ position: absolute; left: 10px; top: 4px; font-size: 14px; }}
    .bar {{ display: block; height: 100%; border-radius: inherit; opacity: .9; }}
    .bar.v26 {{ background: linear-gradient(90deg, #9fc4ff, var(--blue)); }}
    .bar.v31 {{ background: linear-gradient(90deg, #97e3c7, var(--green)); }}
    .legend {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }}
    .pill {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 5px 10px; font-weight: 800; font-size: 13px; }}
    .pill.blue {{ background: var(--blue-soft); color: var(--blue); }}
    .pill.green {{ background: var(--green-soft); color: var(--green); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 12px 10px; border-bottom: 1px solid var(--line); }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    .summary {{ display: grid; grid-template-columns: 1.15fr .85fr; gap: 16px; margin-top: 16px; }}
    ul {{ margin: 0; padding-left: 18px; }}
    li {{ margin: 8px 0; }}
    @media (max-width: 860px) {{
      header, .summary {{ display: block; }}
      .stamp {{ text-align: left; margin-top: 12px; }}
      .cards, .two {{ grid-template-columns: 1fr; }}
      .panel {{ padding: 18px; overflow-x: auto; }}
      .bar-row {{ grid-template-columns: 1fr; gap: 6px; }}
      table {{ min-width: 720px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <header>
      <div>
        <h1>GRID DCA 2.6 vs GRID DCA 3.1</h1>
        <p class="lead">Сравнение двух версий стратегии по 20 фьючерсным парам. В расчёте: 15m свечи, RSI 15m/1h, вход по открытию следующей свечи, pessimistic intrabar, taker 0.05%, первый ордер 6 USDT, глобальная пауза 3 часа после стоп-лосса.</p>
      </div>
      <div class="stamp">Обновлено<br><strong>05.06.2026</strong></div>
    </header>

    <section class="grid cards">
      <div class="card"><span class="kicker">Лучший результат 30д</span><span class="value pos">+21.88</span><div class="note">GRID DCA 2.6, USDT</div></div>
      <div class="card"><span class="kicker">GRID DCA 3.1</span><span class="value pos">+5.70</span><div class="note">30 дней, USDT</div></div>
      <div class="card"><span class="kicker">Сигналов 30д</span><span class="value">1362 / 706</span><div class="note">2.6 даёт почти в 1.9 раза больше сделок</div></div>
      <div class="card"><span class="kicker">Вывод</span><span class="value">2.6</span><div class="note">лучше по общей прибыли на 14/30/60 днях</div></div>
    </section>

    <div class="legend"><span class="pill blue">GRID DCA 2.6</span><span class="pill green">GRID DCA 3.1</span></div>
    <section class="grid two">
      {bars("pnl", "Кумулятивный PnL", " USDT", 30)}
      {bars("trades", "Количество сделок", "", 2300)}
      {bars("win", "Win rate", "%", 100)}
      {bars("sl", "Стоп-лоссы", "", 45)}
    </section>

    <section class="summary">
      <div class="panel">
        <h2>Ключевой вывод</h2>
        <ul>
          <li><strong>GRID DCA 2.6 прибыльнее</strong> на всех окнах: 14д, 30д и 60д.</li>
          <li><strong>GRID DCA 3.1 действительно режет количество входов</strong> примерно на 44-48%, но прибыль падает сильнее, чем риск.</li>
          <li>3.1 полезна как осторожная админская версия, но пока не выглядит лучше основной стратегии.</li>
          <li>Самая слабая зона обеих версий — pullback-входы, особенно по TIA, DOT, ARB, ZEC.</li>
        </ul>
      </div>
      <div class="panel">
        <h2>Рекомендация</h2>
        <p>Основной пользовательской стратегией оставить <strong>GRID DCA 2.6</strong>. Версию <strong>3.1</strong> не выкатывать пользователям как замену: её стоит дорабатывать точечно, особенно фильтры pullback и список монет.</p>
      </div>
    </section>

    <section class="panel" style="margin-top:16px">
      <h2>PnL по парам за 30 дней</h2>
      <table>
        <thead>
          <tr><th>Пара</th><th>GRID 2.6</th><th>GRID 3.1</th><th>Лучше</th><th>Разница 3.1 - 2.6</th></tr>
        </thead>
        <tbody>
          {pair_table()}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""
    REPORT_PATH.write_text(html, encoding="utf-8")
    print(REPORT_PATH.resolve())


if __name__ == "__main__":
    main()
