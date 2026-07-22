"""Build a public-ready GRID DCA 2.8 yearly report for all six tariffs."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
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
            name="Р‘РµСЃРїР»Р°С‚РЅС‹Р№",
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
            name="Р‘РµСЃРїР»Р°С‚РЅС‹Р№ РџР»СЋСЃ",
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
            name="РЎС‚Р°СЂС‚",
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
            name="РЎС‚Р°СЂС‚ РџР»СЋСЃ",
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
            name="РџСЂРµРјРёСѓРј",
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
            name="РџСЂРµРјРёСѓРј РџР»СЋСЃ",
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
    cells = ["<th>РџР°СЂР°</th>"]
    for index, tariff in enumerate(data["tariffs"]):
        sep = ' class="tariff-sep"' if index else ""
        name = tariff["name"]
        cells.append(f"<th{sep}>РЎРґРµР»РѕРє {name}</th><th>PnL {name}</th>")
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
  <title>Р“РѕРґРѕРІРѕР№ Р±СЌРєС‚РµСЃС‚ __STRATEGY__ РїРѕ 6 С‚Р°СЂРёС„Р°Рј В· Griders</title>
  <meta name="description" content="Р“РѕРґРѕРІРѕР№ Р±СЌРєС‚РµСЃС‚ СЃС‚СЂР°С‚РµРіРёРё __STRATEGY__ РїРѕ 6 С‚Р°СЂРёС„Р°Рј Griders: РїСЂРёР±С‹Р»СЊ, РїСЂРѕСЃР°РґРєР°, СЃРґРµР»РєРё, РєРѕРјРёСЃСЃРёРё, СЃС‚РѕРї-Р»РѕСЃСЃС‹ Рё СЂРµР·СѓР»СЊС‚Р°С‚С‹ РїРѕ С‚РѕСЂРіРѕРІС‹Рј РїР°СЂР°Рј.">
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
        <h1>Р“РѕРґРѕРІРѕР№ Р±СЌРєС‚РµСЃС‚ РїРѕ 6 С‚Р°СЂРёС„Р°Рј</h1>
        <p class="muted">РџРѕСЂС‚С„РµР»СЊРЅР°СЏ СЃРёРјСѓР»СЏС†РёСЏ Р·Р° РїРµСЂРёРѕРґ __PERIOD_START__ - __PERIOD_END__. Р’СЃРµ СЂР°Р·СЂРµС€С‘РЅРЅС‹Рµ РїР°СЂС‹ СЂР°Р±РѕС‚Р°СЋС‚ РѕРґРЅРѕРІСЂРµРјРµРЅРЅРѕ, Р° Р»РёРјРёС‚С‹ РєР°Р¶РґРѕРіРѕ С‚Р°СЂРёС„Р° РїСЂРёРјРµРЅСЏСЋС‚СЃСЏ Рє РѕР±С‰РµР№ РѕС‡РµСЂРµРґРё СЃРёРіРЅР°Р»РѕРІ.</p>
      </div>
      <div class="report-date"><span>РЎРёРіРЅР°Р»РѕРІ-РєР°РЅРґРёРґР°С‚РѕРІ</span><strong>__SIGNAL_CANDIDATES__</strong></div>
    </section>
    <section class="report-grid six">__CARDS__</section>
    <section class="tariff-charts">__CHARTS__</section>
    <section class="panel">
      <h2>PnL РїРѕ РїР°СЂР°Рј</h2>
      <div class="table-scroll">
        <table>
          <thead><tr>__PAIR_HEADER__</tr></thead>
          <tbody>__PAIR_ROWS__</tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <h2>РҐСѓРґС€РёРµ СЃРґРµР»РєРё</h2>
      <div class="table-scroll">
        <table>
          <thead><tr><th>РўР°СЂРёС„</th><th>РџР°СЂР°</th><th>РЎС‚РѕСЂРѕРЅР°</th><th>РЎС‚Р°РґРёСЏ</th><th>Р’С…РѕРґ UTC</th><th>Р’С‹С…РѕРґ</th><th>PnL</th></tr></thead>
          <tbody>__WORST_ROWS__</tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <h2>Р”РѕРїСѓС‰РµРЅРёСЏ СЂР°СЃС‡С‘С‚Р°</h2>
      <ul class="clean-list">
        <li>РСЃС‚РѕС‡РЅРёРє РґР°РЅРЅС‹С…: РїСѓР±Р»РёС‡РЅС‹Рµ СЃРІРµС‡Рё Bybit linear futures. РўР°Р№РјС„СЂРµР№Рј РІС…РѕРґР° - 15 РјРёРЅСѓС‚.</li>
        <li>__STRATEGY__ РёСЃРїРѕР»СЊР·СѓРµС‚ Р±Р°Р·РѕРІС‹Рµ СЃРёРіРЅР°Р»С‹ GRID DCA Рё С‚РµРєСѓС‰РёР№ С„РёР»СЊС‚СЂ РґРЅРµРІРЅРѕРіРѕ С‚СЂРµРЅРґР° BTC/ETH: Р»РѕРЅРіРё Р±Р»РѕРєРёСЂСѓСЋС‚СЃСЏ РІ downtrend, С€РѕСЂС‚С‹ Р±Р»РѕРєРёСЂСѓСЋС‚СЃСЏ РІ uptrend; РґРѕРїРѕР»РЅРёС‚РµР»СЊРЅРѕ СЃРµСЂРІРµСЂРЅС‹Р№ EMA20 guard РѕС‚СЃРµРєР°РµС‚ СЃРґРµР»РєРё РїСЂРѕС‚РёРІ РґРЅРµРІРЅРѕРіРѕ РїРѕР»РѕР¶РµРЅРёСЏ BTC Рё ETH.</li>
        <li>Р’С…РѕРґ СЃС‡РёС‚Р°РµС‚СЃСЏ РїРѕ РѕС‚РєСЂС‹С‚РёСЋ СЃР»РµРґСѓСЋС‰РµР№ 15-РјРёРЅСѓС‚РЅРѕР№ СЃРІРµС‡Рё РїРѕСЃР»Рµ РїРѕРґС‚РІРµСЂР¶РґС‘РЅРЅРѕРіРѕ СЃРёРіРЅР°Р»Р° TradingView.</li>
        <li>РџРµСЂРІС‹Р№ РѕСЂРґРµСЂ РґР»СЏ С‚Р°СЂРёС„РѕРІ СЃ СЂР°СЃС‡С‘С‚РѕРј РѕС‚ РґРµРїРѕР·РёС‚Р° СЃС‡РёС‚Р°РµС‚СЃСЏ РїРѕ 5% РЅР° СЃРґРµР»РєСѓ Рё РѕРіСЂР°РЅРёС‡РёРІР°РµС‚СЃСЏ РјР°РєСЃРёРјСѓРјРѕРј С‚Р°СЂРёС„Р°.</li>
        <li>РџРµСЂРµРґ РѕС‚РєСЂС‹С‚РёРµРј РЅРѕРІРѕР№ СЃРґРµР»РєРё РїСЂРѕРІРµСЂСЏРµС‚СЃСЏ РїР»Р°РЅРѕРІР°СЏ РјР°СЂР¶Р°: СЃСѓРјРјР° РјР°СЂР¶Рё СѓР¶Рµ РѕС‚РєСЂС‹С‚С‹С… СЃРµС‚РѕРє Рё РЅРѕРІРѕР№ СЃРµС‚РєРё РЅРµ РґРѕР»Р¶РЅР° РїСЂРµРІС‹С€Р°С‚СЊ С‚РµРєСѓС‰РёР№ РґРµРїРѕР·РёС‚ С‚Р°СЂРёС„Р°.</li>
        <li>РљРѕРјРёСЃСЃРёСЏ: taker 0.05% РЅР° РїРµСЂРІС‹Р№ РѕСЂРґРµСЂ, СЃС‚СЂР°С…РѕРІРѕС‡РЅС‹Рµ РѕСЂРґРµСЂР° Рё РІС‹С…РѕРґ.</li>
        <li>Р•СЃР»Рё РІРЅСѓС‚СЂРё РѕРґРЅРѕР№ СЃРІРµС‡Рё РѕРґРЅРѕРІСЂРµРјРµРЅРЅРѕ РјРѕРіР»Рё СЃСЂР°Р±РѕС‚Р°С‚СЊ TP Рё SL, Р·Р°СЃС‡РёС‚С‹РІР°РµС‚СЃСЏ SL РєР°Рє Р±РѕР»РµРµ РѕСЃС‚РѕСЂРѕР¶РЅС‹Р№ СЃС†РµРЅР°СЂРёР№.</li>
        <li>РџРѕСЃР»Рµ СЃС‚РѕРї-Р»РѕСЃСЃР° РїСЂРёРјРµРЅСЏРµС‚СЃСЏ РїР°СѓР·Р° GRID DCA РЅР° 3 С‡Р°СЃР°.</li>
        <li>РџСЂРѕСЃРєР°Р»СЊР·С‹РІР°РЅРёРµ, funding, Р·Р°РґРµСЂР¶РєРё webhook Рё РІРѕР·РјРѕР¶РЅС‹Рµ РѕС‚РєР°Р·С‹ Cryptorg/Р±РёСЂР¶Рё РЅРµ СѓС‡РёС‚С‹РІР°СЋС‚СЃСЏ.</li>
        <li><strong>РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ:</strong> РїСЂРёР±С‹Р»СЊ РІ РїСЂРѕС€Р»РѕРј РЅРµ РѕР·РЅР°С‡Р°РµС‚ РїСЂРёР±С‹Р»СЊ РІ Р±СѓРґСѓС‰РµРј. Р”Р°РЅРЅС‹Р№ Р±СЌРєС‚РµСЃС‚ РЅРµ РіР°СЂР°РЅС‚РёСЂСѓРµС‚ РґРѕС…РѕРґР° РїРѕ СЌС‚РѕР№ СЃС‚СЂР°С‚РµРіРёРё.</li>
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
    parser.add_argument("--start", default=None)
    parser.add_argument("--html-out", default=str(HTML_PATH))
    parser.add_argument("--json-out", default=str(JSON_PATH))
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    if args.start:
        start = datetime.fromisoformat(args.start).astimezone(timezone.utc)
        if start >= end:
            raise SystemExit("--start must be earlier than --end")
        fetch_days = max(1, math.ceil((end - start).total_seconds() / 86400))
        _, end, rows = await base.fetch_all(fetch_days, end)
    else:
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

    json_path = Path(args.json_out)
    html_path = Path(args.html_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    html_path.write_text(build_html(data), encoding="utf-8")
    print(json.dumps({
        "html": str(html_path.resolve()),
        "json": str(json_path.resolve()),
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

