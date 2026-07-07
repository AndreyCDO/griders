"""Research backtest: current GRID DCA 2.8 on the 20 current pairs plus 30 new pairs.

The script keeps the same portfolio engine and tariff settings used by the
latest public GRID DCA 2.8 yearly all-tariffs report, but expands the pair
universe to 50 Bybit USDT perpetual symbols.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_27_ema20_no_side_cooldown_base_tp import (
    all_signal_candidates_base_tp_with_server_guard,
)
from tools.build_grid_dca_28_year_all_tariffs_report import all_tariffs


OUT_JSON = ROOT / ".private_reports/grid-dca-28-50-pairs-all-tariffs.json"
OUT_HTML = ROOT / ".private_reports/grid-dca-28-50-pairs-all-tariffs-comparison.html"
BASELINE_JSON = ROOT / "webapp/static/reports/grid-dca-28-year-all-tariffs.json"

CURRENT_20_PAIRS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "HYPEUSDT",
    "NEARUSDT",
    "ZECUSDT",
    "ONDOUSDT",
    "XRPUSDT",
    "SUIUSDT",
    "FILUSDT",
    "TAOUSDT",
    "RENDERUSDT",
    "ADAUSDT",
    "INJUSDT",
    "LITUSDT",
    "ENAUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "JUPUSDT",
    "ARBUSDT",
]

NEW_30_PAIRS = [
    "DOGEUSDT",
    "TRXUSDT",
    "XLMUSDT",
    "BCHUSDT",
    "LTCUSDT",
    "HBARUSDT",
    "BNBUSDT",
    "UNIUSDT",
    "DOTUSDT",
    "WLDUSDT",
    "MNTUSDT",
    "AAVEUSDT",
    "ICPUSDT",
    "ETCUSDT",
    "MORPHOUSDT",
    "KASUSDT",
    "QNTUSDT",
    "ATOMUSDT",
    "ALGOUSDT",
    "POLUSDT",
    "XDCUSDT",
    "APTUSDT",
    "AEROUSDT",
    "CAKEUSDT",
    "DASHUSDT",
    "VETUSDT",
    "JTOUSDT",
    "VIRTUALUSDT",
    "SEIUSDT",
    "TIAUSDT",
]

ALL_50_PAIRS = list(dict.fromkeys([*CURRENT_20_PAIRS, *NEW_30_PAIRS]))


def compact_tariff(row: dict) -> dict:
    metrics = row["metrics"]
    return {
        "code": row["code"],
        "name": row["name"],
        "settings": row["settings"],
        "metrics": {
            "trades": metrics["trades"],
            "pnl": metrics["pnl"],
            "return_pct": metrics["return_pct"],
            "final_deposit": metrics["final_deposit"],
            "win_rate": metrics["win_rate"],
            "stops": metrics["stops"],
            "profit_factor": metrics["profit_factor"],
            "max_drawdown": metrics["max_drawdown"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "avg_first_order": metrics["avg_first_order"],
            "avg_planned_entry_value": metrics["avg_planned_entry_value"],
            "skipped": metrics["skipped"],
        },
        "by_pair": row.get("by_pair", []),
        "worst_trades": row.get("worst_trades", [])[:10],
    }


def coverage(rows: dict[str, list], start_ms: int, end_ms: int) -> list[dict]:
    result = []
    for symbol in ALL_50_PAIRS:
        symbol_rows = rows.get(symbol) or []
        first = int(symbol_rows[0][0]) if symbol_rows else None
        last = int(symbol_rows[-1][0]) if symbol_rows else None
        result.append(
            {
                "symbol": symbol,
                "candles": len(symbol_rows),
                "has_start": bool(first is not None and first <= start_ms),
                "has_end": bool(last is not None and last >= end_ms - base.INTERVAL_MS),
                "first": datetime.fromtimestamp(first / 1000, tz=timezone.utc).isoformat() if first else None,
                "last": datetime.fromtimestamp(last / 1000, tz=timezone.utc).isoformat() if last else None,
            }
        )
    return result


def compare_rows(data: dict) -> str:
    baseline = {row["code"]: row for row in data["baseline_20_pairs"]["tariffs"]}
    rows = []
    for row in data["expanded_50_pairs"]["tariffs"]:
        old = baseline[row["code"]]
        old_m = old["metrics"]
        new_m = row["metrics"]
        rows.append(
            f"""
            <tr>
              <td><strong>{row['name']}</strong></td>
              <td>{old_m['trades']} -> {new_m['trades']} <span class="muted">({new_m['trades'] - old_m['trades']:+d})</span></td>
              <td>{old_m['stops']} -> {new_m['stops']} <span class="muted">({new_m['stops'] - old_m['stops']:+d})</span></td>
              <td>{old_m['pnl']:.2f} -> {new_m['pnl']:.2f}</td>
              <td class="{css_num(new_m['pnl'] - old_m['pnl'])}">{new_m['pnl'] - old_m['pnl']:+.2f}</td>
              <td>{old_m['profit_factor']:.2f} -> {new_m['profit_factor']:.2f}</td>
              <td>{old_m['max_drawdown']:.2f} -> {new_m['max_drawdown']:.2f}</td>
            </tr>
            """
        )
    return "".join(rows)


def pair_rows(data: dict) -> str:
    premium_plus = next(
        row for row in data["expanded_50_pairs"]["tariffs"] if row["code"] == "premium_plus"
    )
    rows = []
    for row in premium_plus["by_pair"]:
        rows.append(
            f"""
            <tr>
              <td><strong>{row['symbol']}</strong></td>
              <td>{row['trades']}</td>
              <td>{row['stops']}</td>
              <td class="{css_num(row['pnl'])}">{row['pnl']:.2f}</td>
              <td>{row['win_rate']:.2f}%</td>
            </tr>
            """
        )
    return "".join(rows)


def coverage_rows(data: dict) -> str:
    rows = []
    for row in data["coverage"]:
        cls = "" if row["has_start"] and row["has_end"] else "warn"
        rows.append(
            f"""
            <tr class="{cls}">
              <td><strong>{row['symbol']}</strong></td>
              <td>{row['candles']}</td>
              <td>{row['first'] or '-'}</td>
              <td>{row['last'] or '-'}</td>
            </tr>
            """
        )
    return "".join(rows)


def css_num(value: float) -> str:
    return "pos" if value > 0 else "neg" if value < 0 else ""


def render_html(data: dict) -> str:
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GRID DCA 2.8: 50 монет против 20</title>
  <style>
    :root {{ color-scheme: light; --line:#d9e1e6; --muted:#607080; --pos:#007f68; --neg:#c7431d; }}
    body {{ margin:32px; font-family: Arial, sans-serif; color:#0c1720; background:#f5f8fa; }}
    .panel {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:22px; margin:0 0 18px; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ border-bottom:1px solid var(--line); padding:10px; text-align:left; vertical-align:top; }}
    th {{ color:var(--muted); font-size:12px; text-transform:uppercase; }}
    .muted {{ color:var(--muted); }}
    .pos {{ color:var(--pos); font-weight:700; }}
    .neg {{ color:var(--neg); font-weight:700; }}
    .warn td {{ background:#fff6e2; }}
    .grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:18px; }}
    @media (max-width: 900px) {{ body {{ margin:16px; }} .grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <section class="panel">
    <h1>GRID DCA 2.8: пакетный бэктест 50 монет</h1>
    <p class="muted">Период: {data['period']['start']} - {data['period']['end']}. Сравнение с последним годовым прогоном действующей стратегии на 20 монетах. Лимиты тарифов, проверка маржи, базовый TP, global trend/EMA20 guard и отсутствие 5-минутного ограничения по стороне оставлены как в текущем отчёте 2.8.</p>
    <p class="muted">Кандидатов сигналов: 20 монет {data['baseline_20_pairs']['signal_candidates']} -> 50 монет {data['expanded_50_pairs']['signal_candidates']}.</p>
  </section>
  <section class="panel">
    <h2>Сравнение по тарифам</h2>
    <table>
      <thead><tr><th>Тариф</th><th>Сделки</th><th>Стопы</th><th>PnL</th><th>Разница PnL</th><th>Profit factor</th><th>Max DD</th></tr></thead>
      <tbody>{compare_rows(data)}</tbody>
    </table>
  </section>
  <div class="grid">
    <section class="panel">
      <h2>Разбор 50 монет по Premium Plus</h2>
      <p class="muted">Для оценки вклада монет показан самый широкий тариф, где доступен весь набор.</p>
      <table>
        <thead><tr><th>Пара</th><th>Сделки</th><th>Стопы</th><th>PnL</th><th>Win rate</th></tr></thead>
        <tbody>{pair_rows(data)}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Покрытие свечей</h2>
      <p class="muted">Жёлтым отмечаются пары, где Bybit не дал полный диапазон свечей.</p>
      <table>
        <thead><tr><th>Пара</th><th>Свечей</th><th>Первая свеча UTC</th><th>Последняя свеча UTC</th></tr></thead>
        <tbody>{coverage_rows(data)}</tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    original_pairs = base.ALL_PAIRS
    original_side_cooldown = base.SIDE_WEBHOOK_COOLDOWN_MS
    base.ALL_PAIRS = ALL_50_PAIRS[:]
    base.SIDE_WEBHOOK_COOLDOWN_MS = 0
    try:
        start, end, rows = await base.fetch_all(args.days, end)
        candidates, candidate_skipped = all_signal_candidates_base_tp_with_server_guard(start, rows)
        tariffs = all_tariffs()
        results = [base.run_portfolio(tariff, candidates, rows) for tariff in tariffs]
        result_json = base.result_to_json(start, end, candidates, results)
    finally:
        base.ALL_PAIRS = original_pairs
        base.SIDE_WEBHOOK_COOLDOWN_MS = original_side_cooldown

    baseline = json.loads(BASELINE_JSON.read_text(encoding="utf-8-sig"))
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": result_json["period"],
        "current_20_pairs": CURRENT_20_PAIRS,
        "new_30_pairs": NEW_30_PAIRS,
        "all_50_pairs": ALL_50_PAIRS,
        "baseline_20_pairs": {
            "source": str(BASELINE_JSON),
            "signal_candidates": baseline["signal_candidates"],
            "tariffs": [compact_tariff(row) for row in baseline["tariffs"]],
        },
        "expanded_50_pairs": {
            "signal_candidates": result_json["signal_candidates"],
            "candidate_skipped": candidate_skipped,
            "tariffs": [compact_tariff(row) for row in result_json["tariffs"]],
        },
        "coverage": coverage(rows, start_ms, end_ms),
        "notes": {
            "tariff_pair_rules": "The same tariff exclusions as the 20-pair report are preserved: free/free_plus exclude BTC, ETH, SOL; start excludes BTC; start_plus, premium and premium_plus use the full expanded universe.",
            "strategy": "Current GRID DCA 2.8: base take profit, BTC/ETH global trend and EMA20 guard, margin check, no same-side 5-minute cooldown.",
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    OUT_HTML.write_text(render_html(data), encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON.resolve()),
        "html": str(OUT_HTML.resolve()),
        "period": data["period"],
        "signal_candidates_20": data["baseline_20_pairs"]["signal_candidates"],
        "signal_candidates_50": data["expanded_50_pairs"]["signal_candidates"],
        "metrics": [
            {
                "code": row["code"],
                "name": row["name"],
                "trades": row["metrics"]["trades"],
                "pnl": round(row["metrics"]["pnl"], 2),
                "profit_factor": round(row["metrics"]["profit_factor"], 3),
                "stops": row["metrics"]["stops"],
                "max_drawdown": round(row["metrics"]["max_drawdown"], 2),
            }
            for row in data["expanded_50_pairs"]["tariffs"]
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
