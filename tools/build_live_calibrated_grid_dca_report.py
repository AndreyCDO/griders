"""Build a private live-calibrated GRID DCA report.

The report is intentionally written outside public static reports by default.
It can use anonymized output from tools/analyze_live_trade_history.py when a
safe production export is available. Without that file it applies documented
conservative execution haircuts and marks the report as preliminary.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BASE_JSON = Path(".tmp_grid_dca_29_check.json")
FALLBACK_BASE_JSON = Path("webapp/static/reports/grid-dca-29-year-all-tariffs.json")
DEFAULT_OUT = Path(".private_reports/grid-dca-29-live-calibrated-backtest.html")
DEFAULT_JSON_OUT = Path(".private_reports/grid-dca-29-live-calibrated-backtest.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build private live-calibrated GRID DCA report.")
    parser.add_argument("--base-json", default="", help="Base candle backtest JSON.")
    parser.add_argument("--live-json", default="", help="Optional anonymized live history JSON.")
    parser.add_argument("--html-out", default=str(DEFAULT_OUT))
    parser.add_argument("--json-out", default=str(DEFAULT_JSON_OUT))
    args = parser.parse_args()

    base_path = Path(args.base_json) if args.base_json else (DEFAULT_BASE_JSON if DEFAULT_BASE_JSON.exists() else FALLBACK_BASE_JSON)
    base = json.loads(base_path.read_text(encoding="utf-8"))
    live = _load_optional(args.live_json)
    calibration = _calibration_from_live(live)
    rows = [_calibrate_tariff(tariff, calibration) for tariff in base["tariffs"]]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_json": str(base_path),
        "live_json": args.live_json or "",
        "strategy_label": base.get("strategy_label", "GRID DCA"),
        "period": base.get("period", {}),
        "signal_candidates": base.get("signal_candidates"),
        "assumptions": base.get("assumptions", {}),
        "base_variant": base.get("report_variant", {}),
        "calibration": calibration,
        "tariffs": rows,
        "notes": _notes(calibration, bool(live)),
    }
    json_out = Path(args.json_out)
    html_out = Path(args.html_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    html_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_out.write_text(_html(report), encoding="utf-8")
    print(json.dumps({"html": str(html_out.resolve()), "json": str(json_out.resolve()), "calibration_source": calibration["source"]}, ensure_ascii=False, indent=2))


def _load_optional(path: str) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _calibration_from_live(live: dict[str, Any] | None) -> dict[str, Any]:
    if not live:
        return {
            "source": "preliminary_conservative_defaults",
            "description": "Live-history export was not available in this run; conservative execution haircuts are applied.",
            "execution_rate": 0.95,
            "win_pnl_multiplier": 0.85,
            "loss_pnl_multiplier": 1.15,
            "drawdown_multiplier": 1.25,
            "stop_multiplier": 1.05,
            "confidence": "preliminary",
        }
    model = live.get("model_calibration") or {}
    closed = live.get("closed_summary") or {}
    signal_to_sent = _clamp(_float(model.get("signal_to_sent_rate"), 0.95), 0.3, 1.0)
    sent_to_closed = _clamp(_float(model.get("sent_to_closed_rate"), 0.95), 0.3, 1.0)
    avg_r = _float(model.get("avg_live_r_multiple"), _float(closed.get("avg_r_multiple"), 1.0))
    live_stop = _float(model.get("live_stop_rate"), 0.0)
    execution_rate = _clamp(signal_to_sent * sent_to_closed, 0.25, 1.0)
    win_multiplier = _clamp(0.85 + max(-0.15, min(0.1, (avg_r - 1.0) * 0.08)), 0.65, 1.0)
    loss_multiplier = _clamp(1.05 + min(0.35, live_stop * 0.8), 1.0, 1.5)
    return {
        "source": "live_history_export",
        "description": "Calibration derived from anonymized live-history aggregates.",
        "execution_rate": execution_rate,
        "win_pnl_multiplier": win_multiplier,
        "loss_pnl_multiplier": loss_multiplier,
        "drawdown_multiplier": _clamp(loss_multiplier * 1.1, 1.05, 1.7),
        "stop_multiplier": _clamp(1.0 + live_stop, 1.0, 1.5),
        "confidence": "moderate" if _int(closed.get("trades")) >= 300 else "preliminary",
        "live_trades": _int(closed.get("trades")),
        "live_signal_to_sent_rate": signal_to_sent,
        "live_sent_to_closed_rate": sent_to_closed,
        "live_stop_rate": live_stop,
        "live_avg_r_multiple": avg_r,
    }


def _calibrate_tariff(tariff: dict[str, Any], calibration: dict[str, Any]) -> dict[str, Any]:
    metrics = tariff["metrics"]
    pnl = _float(metrics.get("pnl"))
    pf = _float(metrics.get("profit_factor"))
    if pf > 1 and pnl > 0:
        gross_loss = pnl / (pf - 1.0)
        gross_profit = gross_loss * pf
    else:
        gross_profit = max(0.0, pnl)
        gross_loss = abs(min(0.0, pnl))
    execution = _float(calibration["execution_rate"])
    cal_profit = gross_profit * _float(calibration["win_pnl_multiplier"]) * execution
    cal_loss = gross_loss * _float(calibration["loss_pnl_multiplier"]) * execution
    cal_pnl = cal_profit - cal_loss
    initial = _float(tariff["settings"].get("initial_deposit"))
    cal_final = initial + cal_pnl
    cal_pf = cal_profit / cal_loss if cal_loss > 0 else None
    base_dd = _float(metrics.get("max_drawdown"))
    base_dd_pct = _float(metrics.get("max_drawdown_pct"))
    return {
        "code": tariff.get("code"),
        "name": tariff.get("name"),
        "initial_deposit": initial,
        "base": {
            "trades": _int(metrics.get("trades")),
            "pnl": pnl,
            "return_pct": _float(metrics.get("return_pct")),
            "profit_factor": pf,
            "stops": _int(metrics.get("stops")),
            "max_drawdown": base_dd,
            "max_drawdown_pct": base_dd_pct,
            "win_rate": _float(metrics.get("win_rate")),
        },
        "calibrated": {
            "trades_effective": int(round(_int(metrics.get("trades")) * execution)),
            "pnl": cal_pnl,
            "return_pct": (cal_pnl / initial * 100.0) if initial > 0 else 0.0,
            "final_deposit": cal_final,
            "profit_factor": cal_pf,
            "stops_estimated": int(math.ceil(_int(metrics.get("stops")) * _float(calibration["stop_multiplier"]) * execution)),
            "max_drawdown": base_dd * _float(calibration["drawdown_multiplier"]),
            "max_drawdown_pct": base_dd_pct * _float(calibration["drawdown_multiplier"]),
        },
    }


def _notes(calibration: dict[str, Any], has_live: bool) -> list[str]:
    notes = [
        "Базовый слой: свечной backtest GRID DCA 2.9 на 15m Bybit candles.",
        "Калиброванный слой снижает прибыльные закрытия, усиливает убыточные закрытия и масштабирует число исполненных сделок по execution-rate.",
        "Это не публикационный отчет и не обещание доходности.",
    ]
    if not has_live:
        notes.append("Live-history JSON не был доступен в этом запуске, поэтому коэффициенты предварительные и консервативные.")
        notes.append("Для настоящей калибровки запусти на сервере tools/analyze_live_trade_history.py и передай результат через --live-json.")
    return notes


def _html(report: dict[str, Any]) -> str:
    rows = "\n".join(_row_html(row) for row in report["tariffs"])
    notes = "".join(f"<li>{_esc(note)}</li>" for note in report["notes"])
    cal = report["calibration"]
    period = report.get("period") or {}
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(report['strategy_label'])} · live-calibrated private backtest</title>
  <style>
    :root {{ color-scheme: light; --ink:#15171a; --muted:#5e6673; --line:#d9dee7; --bg:#f6f8fb; --panel:#fff; --good:#137a3a; --bad:#b42318; --warn:#936500; }}
    body {{ margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif; color:var(--ink); background:var(--bg); }}
    header, main {{ max-width:1180px; margin:0 auto; padding:28px 20px; }}
    header {{ padding-top:36px; }}
    h1 {{ margin:0 0 8px; font-size:30px; letter-spacing:0; }}
    h2 {{ margin:28px 0 12px; font-size:20px; }}
    p {{ margin:8px 0; color:var(--muted); }}
    .meta {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin-top:18px; }}
    .metric {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; }}
    .metric span {{ display:block; color:var(--muted); font-size:12px; }}
    .metric strong {{ display:block; font-size:18px; margin-top:4px; }}
    table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
    th, td {{ padding:10px 9px; border-bottom:1px solid var(--line); text-align:right; vertical-align:top; }}
    th:first-child, td:first-child {{ text-align:left; }}
    th {{ color:#394150; background:#eef2f7; font-weight:650; font-size:12px; }}
    tr:last-child td {{ border-bottom:0; }}
    .pos {{ color:var(--good); font-weight:650; }}
    .neg {{ color:var(--bad); font-weight:650; }}
    .warn {{ color:var(--warn); font-weight:650; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    ul {{ margin:8px 0 0 20px; color:var(--muted); }}
    code {{ background:#eef2f7; padding:2px 5px; border-radius:5px; }}
    @media (max-width:900px) {{ .meta {{ grid-template-columns:1fr 1fr; }} table {{ font-size:12px; }} th,td {{ padding:8px 6px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{_esc(report['strategy_label'])}: приватный live-calibrated backtest</h1>
    <p>Страница создана локально и не опубликована на сайте. Generated: {_esc(report['generated_at'])}</p>
    <div class="meta">
      <div class="metric"><span>Период базового backtest</span><strong>{_esc(period.get('start',''))[:10]} → {_esc(period.get('end',''))[:10]}</strong></div>
      <div class="metric"><span>Кандидатов сигналов</span><strong>{_num(report.get('signal_candidates'))}</strong></div>
      <div class="metric"><span>Источник калибровки</span><strong>{_esc(cal['source'])}</strong></div>
      <div class="metric"><span>Уверенность</span><strong class="warn">{_esc(cal['confidence'])}</strong></div>
    </div>
  </header>
  <main>
    <section class="panel">
      <h2>Коэффициенты калибровки</h2>
      <p>{_esc(cal['description'])}</p>
      <p><code>execution_rate={cal['execution_rate']:.3f}</code> <code>win_pnl_multiplier={cal['win_pnl_multiplier']:.3f}</code> <code>loss_pnl_multiplier={cal['loss_pnl_multiplier']:.3f}</code> <code>drawdown_multiplier={cal['drawdown_multiplier']:.3f}</code></p>
      <ul>{notes}</ul>
    </section>
    <h2>Результаты по тарифам</h2>
    <table>
      <thead>
        <tr>
          <th>Тариф</th>
          <th>Base PnL</th>
          <th>Calibrated PnL</th>
          <th>Base return</th>
          <th>Calibrated return</th>
          <th>Base PF</th>
          <th>Calibrated PF</th>
          <th>Base stops</th>
          <th>Est. stops</th>
          <th>Calibrated DD</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </main>
</body>
</html>"""


def _row_html(row: dict[str, Any]) -> str:
    base = row["base"]
    cal = row["calibrated"]
    return f"""<tr>
  <td><strong>{_esc(row['code'])}</strong><br><span>{_esc(row.get('name') or '')}</span></td>
  <td class="{_cls(base['pnl'])}">{_money(base['pnl'])}</td>
  <td class="{_cls(cal['pnl'])}">{_money(cal['pnl'])}</td>
  <td>{base['return_pct']:.1f}%</td>
  <td>{cal['return_pct']:.1f}%</td>
  <td>{base['profit_factor']:.2f}</td>
  <td>{cal['profit_factor']:.2f}</td>
  <td>{base['stops']}</td>
  <td>{cal['stops_estimated']}</td>
  <td>{cal['max_drawdown_pct']:.1f}%</td>
</tr>"""


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _money(value: float) -> str:
    return f"{value:+,.2f} USDT".replace(",", " ")


def _num(value: Any) -> str:
    return f"{_int(value):,}".replace(",", " ")


def _cls(value: float) -> str:
    return "pos" if value >= 0 else "neg"


def _esc(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


if __name__ == "__main__":
    main()
