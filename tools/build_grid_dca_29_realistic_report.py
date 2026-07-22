"""Build a live-calibrated GRID DCA 2.9 report.

This report keeps the published yearly candle backtest as the base layer and
adds a realistic calibration layer from an anonymized live-history export.
The calibration is intentionally explicit: it does not replace the candle
simulation, it shows how much the result changes after execution haircuts.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_JSON = ROOT / "webapp/static/reports/grid-dca-29-year-all-tariffs.json"
LIVE_JSON = ROOT / ".private_reports/live-history-60d.json"
HTML_OUT = ROOT / "webapp/static/reports/grid-dca-29-realistic-live-calibrated.html"
JSON_OUT = ROOT / "webapp/static/reports/grid-dca-29-realistic-live-calibrated.json"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-json", default=str(BASE_JSON))
    parser.add_argument("--live-json", default=str(LIVE_JSON))
    parser.add_argument("--html-out", default=str(HTML_OUT))
    parser.add_argument("--json-out", default=str(JSON_OUT))
    args = parser.parse_args()

    base = _load_json(Path(args.base_json))
    live = _load_json(Path(args.live_json))
    calibration = _build_calibration(live)
    tariffs = [_calibrate_tariff(row, calibration) for row in base["tariffs"]]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_label": base.get("strategy_label", "GRID DCA 2.9"),
        "base_report": str(Path(args.base_json)),
        "live_report": str(Path(args.live_json)),
        "period": base.get("period") or {},
        "pairs": base.get("pairs") or [],
        "signal_candidates": base.get("signal_candidates"),
        "assumptions": base.get("assumptions") or {},
        "base_variant": base.get("report_variant") or {},
        "live_summary": _live_summary(live),
        "calibration": calibration,
        "tariffs": tariffs,
    }

    json_out = Path(args.json_out)
    html_out = Path(args.html_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    html_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_out.write_text(_html(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "html": str(html_out.resolve()),
                "json": str(json_out.resolve()),
                "calibration": calibration,
                "metrics": [
                    {
                        "code": row["code"],
                        "base_pnl": row["base_metrics"]["pnl"],
                        "realistic_pnl": row["realistic_metrics"]["pnl"],
                        "base_pf": row["base_metrics"]["profit_factor"],
                        "realistic_pf": row["realistic_metrics"]["profit_factor"],
                    }
                    for row in tariffs
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _build_calibration(live: dict[str, Any]) -> dict[str, Any]:
    model = live.get("model_calibration") or {}
    by_side_reason = live.get("by_side_reason") or []
    closed = live.get("closed_summary") or {}
    coverage = live.get("coverage") or {}
    quality = live.get("data_quality") or {}

    fail_rate = _clamp(_float(model.get("signal_fail_rate")), 0.0, 0.35)
    sent_to_closed = _clamp(_float(model.get("sent_to_closed_rate"), 0.96), 0.5, 1.0)
    pending_rate = _clamp(_float(model.get("open_still_pending_rate")), 0.0, 0.25)
    live_stop_rate = _clamp(_float(model.get("live_stop_rate")), 0.0, 0.25)
    execution_rate = _clamp((1.0 - fail_rate) * sent_to_closed, 0.55, 1.0)

    tp_roi = _weighted_avg(
        by_side_reason,
        lambda row: row.get("close_reason") == "take_profit",
        "avg_roi_pct",
        "trades",
    )
    sl_roi_abs = abs(
        _weighted_avg(
            by_side_reason,
            lambda row: row.get("close_reason") == "stop_loss",
            "avg_roi_pct",
            "trades",
        )
    )

    # Live take-profits in the export are around 0.27-0.30% on average.
    # The candle model uses ideal TP touches, so this ratio is a practical
    # haircut for fees, fill quality, manual edge cases and late confirmations.
    win_multiplier = _clamp((tp_roi / 0.35) if tp_roi > 0 else 0.8, 0.65, 0.95)

    # SL is already conservative inside a 15m candle. We still add a mild
    # penalty for failed/pending confirmations and live execution uncertainty.
    loss_multiplier = _clamp(1.0 + fail_rate + pending_rate * 0.5, 1.03, 1.25)
    drawdown_multiplier = _clamp(1.10 + fail_rate + pending_rate + live_stop_rate, 1.15, 1.45)
    stop_multiplier = _clamp(1.0 + fail_rate + pending_rate + live_stop_rate, 1.0, 1.35)

    return {
        "source": "live_history_60d",
        "description": "Калибровка по реальной истории Griders за 60 дней: failed/pending, sent→closed и средние live ROI тейков/стопов.",
        "execution_rate": execution_rate,
        "win_pnl_multiplier": win_multiplier,
        "loss_pnl_multiplier": loss_multiplier,
        "drawdown_multiplier": drawdown_multiplier,
        "stop_multiplier": stop_multiplier,
        "signal_fail_rate": fail_rate,
        "sent_to_closed_rate": sent_to_closed,
        "open_pending_rate": pending_rate,
        "live_stop_rate": live_stop_rate,
        "live_take_profit_avg_roi_pct": tp_roi,
        "live_stop_avg_abs_roi_pct": sl_roi_abs,
        "live_trades": _int(closed.get("trades")),
        "live_rows_with_raw_closed": _int(coverage.get("rows_with_raw_closed")),
        "live_rows_with_pnl": _int(coverage.get("rows_with_pnl")),
        "closed_without_raw_row": _int(quality.get("closed_without_raw_row")),
        "confidence": "moderate" if _int(closed.get("trades")) >= 1000 else "preliminary",
    }


def _live_summary(live: dict[str, Any]) -> dict[str, Any]:
    model = live.get("model_calibration") or {}
    closed = live.get("closed_summary") or {}
    coverage = live.get("coverage") or {}
    signal_status = live.get("signal_status") or []
    return {
        "period": live.get("period") or {},
        "coverage": coverage,
        "closed_summary": closed,
        "signal_status": signal_status,
        "signal_fail_rate": _float(model.get("signal_fail_rate")),
        "sent_to_closed_rate": _float(model.get("sent_to_closed_rate")),
        "avg_live_r_multiple": _float(model.get("avg_live_r_multiple")),
    }


def _calibrate_tariff(tariff: dict[str, Any], calibration: dict[str, Any]) -> dict[str, Any]:
    metrics = tariff["metrics"]
    pnl = _float(metrics.get("pnl"))
    pf = _float(metrics.get("profit_factor"))
    gross_profit, gross_loss = _gross_from_pnl_pf(pnl, pf)

    execution = _float(calibration["execution_rate"])
    calibrated_profit = gross_profit * _float(calibration["win_pnl_multiplier"]) * execution
    calibrated_loss = gross_loss * _float(calibration["loss_pnl_multiplier"]) * execution
    calibrated_pnl = calibrated_profit - calibrated_loss
    initial = _float(tariff["settings"].get("initial_deposit"))
    realistic_metrics = {
        "trades": int(round(_int(metrics.get("trades")) * execution)),
        "pnl": calibrated_pnl,
        "return_pct": calibrated_pnl / initial * 100.0 if initial else 0.0,
        "final_deposit": initial + calibrated_pnl,
        "win_rate": _float(metrics.get("win_rate")) * execution,
        "stops": int(math.ceil(_int(metrics.get("stops")) * execution * _float(calibration["stop_multiplier"]))),
        "profit_factor": calibrated_profit / calibrated_loss if calibrated_loss > 0 else math.inf,
        "max_drawdown": _float(metrics.get("max_drawdown")) * _float(calibration["drawdown_multiplier"]),
        "max_drawdown_pct": _float(metrics.get("max_drawdown_pct")) * _float(calibration["drawdown_multiplier"]),
        "avg_first_order": _float(metrics.get("avg_first_order")),
        "avg_entry_value": _float(metrics.get("avg_entry_value")),
        "avg_planned_entry_value": _float(metrics.get("avg_planned_entry_value")),
        "gross_profit": calibrated_profit,
        "gross_loss": calibrated_loss,
    }

    return {
        "code": tariff["code"],
        "name": tariff["name"],
        "settings": tariff["settings"],
        "base_metrics": metrics,
        "realistic_metrics": realistic_metrics,
        "daily_chart": _calibrated_daily_chart(tariff.get("daily_chart") or [], calibration),
        "by_pair": _calibrated_pair_rows(tariff.get("by_pair") or [], calibration),
        "worst_trades": _calibrated_worst_rows(tariff.get("worst_trades") or [], calibration),
    }


def _gross_from_pnl_pf(pnl: float, pf: float) -> tuple[float, float]:
    if pnl > 0 and pf > 1 and not math.isinf(pf):
        gross_loss = pnl / (pf - 1.0)
        gross_profit = gross_loss * pf
        return gross_profit, gross_loss
    if pnl >= 0:
        return pnl, 0.0
    return 0.0, abs(pnl)


def _calibrated_daily_chart(rows: list[dict[str, Any]], calibration: dict[str, Any]) -> list[dict[str, Any]]:
    execution = _float(calibration["execution_rate"])
    cumulative = 0.0
    result = []
    for row in rows:
        day_pnl = _calibrated_pnl(_float(row.get("pnl")), calibration)
        cumulative += day_pnl
        trades = int(round(_int(row.get("trades")) * execution))
        item = dict(row)
        item.update(
            {
                "pnl": round(day_pnl, 6),
                "pnlText": f"{day_pnl:+.2f} USDT",
                "cumulative": round(cumulative, 6),
                "cumulativeText": f"{cumulative:+.2f} USDT",
                "trades": trades,
            }
        )
        result.append(item)
    return result


def _calibrated_pair_rows(rows: list[dict[str, Any]], calibration: dict[str, Any]) -> list[dict[str, Any]]:
    execution = _float(calibration["execution_rate"])
    stop_factor = _float(calibration["stop_multiplier"])
    result = []
    for row in rows:
        item = dict(row)
        item["trades"] = int(round(_int(row.get("trades")) * execution))
        item["pnl"] = _calibrated_pnl(_float(row.get("pnl")), calibration)
        item["stops"] = int(math.ceil(_int(row.get("stops")) * execution * stop_factor))
        result.append(item)
    return sorted(result, key=lambda item: _float(item.get("pnl")), reverse=True)


def _calibrated_worst_rows(rows: list[dict[str, Any]], calibration: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        item = dict(row)
        item["pnl"] = _calibrated_pnl(_float(row.get("pnl")), calibration)
        result.append(item)
    return sorted(result, key=lambda item: _float(item.get("pnl")))[:10]


def _calibrated_pnl(value: float, calibration: dict[str, Any]) -> float:
    execution = _float(calibration["execution_rate"])
    if value >= 0:
        return value * _float(calibration["win_pnl_multiplier"]) * execution
    return value * _float(calibration["loss_pnl_multiplier"]) * execution


def _html(report: dict[str, Any]) -> str:
    period = report.get("period") or {}
    start = str(period.get("start", ""))[:10]
    end = str(period.get("end", ""))[:10]
    cards = _cards_html(report)
    charts = _chart_cards_html(report)
    pairs = _pair_rows_html(report)
    worst = _worst_rows_html(report)
    cal = report["calibration"]
    live = report["live_summary"]
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Реалистичный бэктест {esc(report['strategy_label'])} по 6 тарифам · Griders</title>
  <meta name="description" content="Live-calibrated бэктест {esc(report['strategy_label'])}: сравнение свечной модели с реальной историей Griders, исполнением, failed/pending и скорректированным PnL.">
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="stylesheet" href="/static/app.css?v=20260720-grid-dca-29-realistic">
  <style>
    .report-page {{ padding-top: 32px; padding-bottom: 48px; }}
    .report-hero {{ display:flex; justify-content:space-between; gap:24px; align-items:flex-start; }}
    .report-grid.six {{ display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:16px; }}
    .report-card h2 {{ margin: 8px 0 10px; font-size: 32px; }}
    .metric-sub {{ display:block; margin-bottom:14px; color:var(--text-muted); font-size:14px; }}
    .tariff-charts {{ display:grid; grid-template-columns:1fr; gap:16px; }}
    .chart-head {{ display:flex; justify-content:space-between; gap:16px; align-items:center; margin-bottom:12px; }}
    .chart-head h2 {{ margin:0; }}
    .report-monitor-card {{ height: 360px; }}
    .table-scroll {{ overflow:auto; }}
    .tariff-sep {{ border-left: 2px solid var(--line); }}
    tr.tariff-break td {{ border-top: 3px solid var(--line); }}
    .calibration-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .calibration-grid div {{ border:1px solid var(--line); border-radius:8px; padding:12px; background:var(--bg-soft); }}
    .calibration-grid span {{ display:block; color:var(--text-muted); font-size:12px; }}
    .calibration-grid strong {{ display:block; margin-top:4px; font-size:20px; }}
    .pos {{ color: var(--accent-dark); }}
    .neg {{ color: var(--warn); }}
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
        <p class="eyebrow">{esc(report['strategy_label'])}</p>
        <h1>Реалистичный бэктест по 6 тарифам</h1>
        <p class="muted">База: годовой свечной бэктест за период {esc(start)} - {esc(end)}. Второй слой: калибровка по реальной истории Griders за 60 дней, чтобы приблизить результат к live-исполнению.</p>
      </div>
      <div class="report-date"><span>Сигналов-кандидатов</span><strong>{num(report.get('signal_candidates'))}</strong></div>
    </section>

    <section class="panel">
      <h2>Проверка правдивости</h2>
      <p class="muted">Последний публичный отчёт является корректным как свечная модель, но он оптимистичен как прогноз live-торговли: не учитывает failed/pending, задержки подтверждений, неполные raw-строки, funding и проскальзывание. Поэтому ниже показан не заменяющий, а калиброванный слой.</p>
      <div class="calibration-grid">
        <div><span>Execution-rate</span><strong>{pct(cal['execution_rate'])}</strong></div>
        <div><span>Множитель TP</span><strong>{cal['win_pnl_multiplier']:.2f}x</strong></div>
        <div><span>Множитель SL</span><strong>{cal['loss_pnl_multiplier']:.2f}x</strong></div>
        <div><span>Множитель просадки</span><strong>{cal['drawdown_multiplier']:.2f}x</strong></div>
        <div><span>Live closed trades</span><strong>{num(cal['live_trades'])}</strong></div>
        <div><span>Failed signals</span><strong>{pct(cal['signal_fail_rate'])}</strong></div>
        <div><span>Sent → closed</span><strong>{pct(cal['sent_to_closed_rate'])}</strong></div>
        <div><span>Средний live TP ROI</span><strong>{cal['live_take_profit_avg_roi_pct']:.2f}%</strong></div>
      </div>
    </section>

    <section class="report-grid six">{cards}</section>
    <section class="tariff-charts">{charts}</section>

    <section class="panel">
      <h2>PnL по парам, калиброванный слой</h2>
      <div class="table-scroll">
        <table>
          <thead><tr>{_pair_header_html(report)}</tr></thead>
          <tbody>{pairs}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Худшие сделки, калиброванный слой</h2>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Тариф</th><th>Пара</th><th>Сторона</th><th>Стадия</th><th>Вход UTC</th><th>Выход</th><th>PnL</th></tr></thead>
          <tbody>{worst}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Допущения расчёта</h2>
      <ul class="clean-list">
        <li>Базовый слой: опубликованный годовой backtest {esc(report['strategy_label'])} по 15m Bybit candles и тем же шести тарифам.</li>
        <li>Калибровка использует агрегированную live-историю: {num(_int(live.get('closed_summary', {}).get('trades')))} закрытых сделок, период {esc((live.get('period') or {}).get('start', ''))[:10]} - {esc((live.get('period') or {}).get('end', ''))[:10]}.</li>
        <li>Легитимные пользовательские пропуски сигналов не применяются повторно, потому что тарифные лимиты, пары, активные сделки и маржа уже моделируются в портфельном backtest.</li>
        <li>Не учтены индивидуальные ручные вмешательства пользователей, разные фактические депозиты, funding, точная ликвидность стакана и будущие изменения Cryptorg/биржи.</li>
        <li><strong>Предупреждение:</strong> прибыль в прошлом не означает прибыль в будущем. Данный бэктест не гарантирует дохода по стратегии.</li>
      </ul>
    </section>
  </main>
  {report_js()}
</body>
</html>"""


def _cards_html(report: dict[str, Any]) -> str:
    cards = []
    for tariff in report["tariffs"]:
        base = tariff["base_metrics"]
        metrics = tariff["realistic_metrics"]
        settings = tariff["settings"]
        cards.append(
            f"""
        <article class="panel report-card">
          <p class="eyebrow">{esc(tariff['name'])}</p>
          <h2 class="{css_num(metrics['pnl'])}">{signed(metrics['pnl'])} USDT</h2>
          <span class="metric-sub">Свечной слой: <strong class="{css_num(base['pnl'])}">{signed(base['pnl'])} USDT</strong></span>
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
        """
        )
    return "".join(cards)


def _chart_cards_html(report: dict[str, Any]) -> str:
    start = str(report["period"].get("start", ""))[:10]
    end = str(report["period"].get("end", ""))[:10]
    cards = []
    for tariff in report["tariffs"]:
        metrics = tariff["realistic_metrics"]
        cards.append(
            f"""
        <article class="panel">
          <div class="chart-head">
            <div>
              <h2>{esc(tariff['name'])}</h2>
              <small class="muted">{esc(start)} - {esc(end)}</small>
            </div>
            <strong class="{css_num(metrics['pnl'])}">{signed(metrics['pnl'])} USDT</strong>
          </div>
          <div class="monitor-chart-card report-monitor-card" data-empty="Нет данных за выбранный период">
            <script type="application/json" class="report-chart-data">{json.dumps(tariff['daily_chart'], ensure_ascii=False)}</script>
            <svg class="monitor-svg report-monitor-svg" viewBox="0 0 920 360" role="img" aria-label="Калиброванный график PnL и сделок {esc(tariff['name'])}"></svg>
            <div class="monitor-tooltip" hidden></div>
          </div>
        </article>
        """
        )
    return "".join(cards)


def _pair_header_html(report: dict[str, Any]) -> str:
    cells = ["<th>Пара</th>"]
    for index, tariff in enumerate(report["tariffs"]):
        sep = ' class="tariff-sep"' if index else ""
        cells.append(f"<th{sep}>Сделок {esc(tariff['name'])}</th><th>PnL {esc(tariff['name'])}</th>")
    return "".join(cells)


def _pair_rows_html(report: dict[str, Any]) -> str:
    by_tariff = [{row["symbol"]: row for row in tariff["by_pair"]} for tariff in report["tariffs"]]
    rows = []
    for symbol in report["pairs"]:
        cells = [f"<td><strong>{esc(symbol)}</strong></td>"]
        for tariff_index, item_by_pair in enumerate(by_tariff):
            row = item_by_pair.get(symbol)
            sep_class = " tariff-sep" if tariff_index > 0 else ""
            if row:
                cells.append(f"<td class=\"{sep_class.strip()}\">{row['trades']}</td><td class=\"{css_num(row['pnl'])}\">{signed(row['pnl'])}</td>")
            else:
                cells.append(f"<td class=\"{sep_class.strip()}\">-</td><td>-</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return "".join(rows)


def _worst_rows_html(report: dict[str, Any]) -> str:
    rows = []
    for tariff_index, tariff in enumerate(report["tariffs"]):
        for trade_index, trade in enumerate(tariff["worst_trades"][:5]):
            row_class = " class=\"tariff-break\"" if tariff_index > 0 and trade_index == 0 else ""
            side = "лонг" if trade.get("side") == "long" else "шорт"
            rows.append(
                f"""
            <tr{row_class}>
              <td>{esc(tariff['name'])}</td>
              <td><strong>{esc(trade.get('symbol'))}</strong></td>
              <td>{side}</td>
              <td>{esc(trade.get('stage'))}</td>
              <td>{dt(_int(trade.get('entry_time')))}</td>
              <td>{esc(str(trade.get('exit_reason')).upper())}</td>
              <td class="{css_num(trade.get('pnl'))}">{signed(trade.get('pnl'))}</td>
            </tr>
            """
            )
    return "".join(rows)


def report_js() -> str:
    # Same style of monitoring chart as the public backtest reports.
    return r"""
<script>
(() => {
  const labels = { dayPnl: "PnL за день", cumulative: "PnL кумулятивный", trades: "Сделок" };
  const width = 920, height = 360;
  const margin = { top: 22, right: 28, bottom: 48, left: 112 };
  const plotTop = 8, plotBottom = 280, barTop = 294, barBottom = height - margin.bottom;
  const plotWidth = width - margin.left - margin.right;
  const ns = "http://www.w3.org/2000/svg";
  const formatMoney = (value) => `${Number(value || 0).toFixed(2)} USDT`;
  const formatAxisNumber = (value) => {
    const num = Number(value || 0);
    if (Math.abs(num - Math.round(num)) < 0.000001) return String(Math.round(num));
    return num.toFixed(Math.abs(num) >= 1 ? 1 : 2);
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
    let minY = Math.min(0, ...cumulatives), maxY = Math.max(0, ...cumulatives);
    if (minY === maxY) { minY -= 1; maxY += 1; }
    const pad = (maxY - minY) * 0.14;
    const axis = niceTicks(minY - pad, maxY + pad, 7);
    minY = axis.min; maxY = axis.max;
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

    const points = data.map((item, index) => `${xAt(index)},${yAt(Number(item.cumulative || 0))}`).join(" ");
    el("polyline", { points, fill: "none", class: "monitor-equity-line", stroke: "#00856f", "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round" });

    const hoverLine = el("line", { y1: plotTop, y2: barBottom, class: "monitor-hover-line", stroke: "#7b8d94", "stroke-width": 1, "stroke-dasharray": "4 4", opacity: 0 });
    const focus = el("circle", { r: 5, class: "monitor-focus-dot", fill: "#fff", stroke: "#00856f", "stroke-width": 3, opacity: 0 });
    const hit = el("rect", { x: margin.left, y: plotTop, width: plotWidth, height: barBottom - plotTop, fill: "transparent" });
    const tooltip = root.querySelector(".monitor-tooltip");
    const showAt = (clientX) => {
      const rect = svg.getBoundingClientRect();
      const x = ((clientX - rect.left) / rect.width) * width;
      const raw = ((x - margin.left) / plotWidth) * (data.length - 1);
      const index = Math.max(0, Math.min(data.length - 1, Math.round(raw)));
      const item = data[index];
      const px = xAt(index), py = yAt(Number(item.cumulative || 0));
      hoverLine.setAttribute("x1", px); hoverLine.setAttribute("x2", px); hoverLine.setAttribute("opacity", "0.8");
      focus.setAttribute("cx", px); focus.setAttribute("cy", py); focus.setAttribute("opacity", "1");
      if (tooltip) {
        tooltip.hidden = false;
        tooltip.innerHTML = `<strong>${escapeHtml(item.date)}</strong><br>${labels.dayPnl}: ${escapeHtml(item.pnlText || formatMoney(item.pnl))}<br>${labels.cumulative}: ${escapeHtml(item.cumulativeText || formatMoney(item.cumulative))}<br>${labels.trades}: ${Number(item.trades || 0)}`;
        tooltip.style.left = `${Math.min(Math.max((px / width) * rect.width + 10, 8), rect.width - 210)}px`;
        tooltip.style.top = `${Math.max((py / height) * rect.height - 28, 8)}px`;
      }
    };
    hit.addEventListener("mousemove", (event) => showAt(event.clientX));
    hit.addEventListener("mouseleave", () => {
      hoverLine.setAttribute("opacity", "0");
      focus.setAttribute("opacity", "0");
      if (tooltip) tooltip.hidden = true;
    });
  });
})();
</script>
"""


def _weighted_avg(rows: list[dict[str, Any]], predicate, value_key: str, weight_key: str) -> float:
    total_weight = 0
    total_value = 0.0
    for row in rows:
        if not predicate(row):
            continue
        weight = _int(row.get(weight_key))
        value = _float(row.get(value_key))
        total_weight += weight
        total_value += value * weight
    return total_value / total_weight if total_weight else 0.0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def fmt(value: Any, digits: int = 2) -> str:
    number = _float(value)
    if math.isinf(number):
        return "∞"
    return f"{number:.{digits}f}"


def signed(value: Any, digits: int = 2) -> str:
    return f"{_float(value):+.{digits}f}"


def pct(value: Any) -> str:
    return f"{_float(value) * 100:.1f}%"


def num(value: Any) -> str:
    return f"{_int(value):,}".replace(",", " ")


def css_num(value: Any) -> str:
    number = _float(value)
    return "pos" if number > 0 else "neg" if number < 0 else ""


def dt(timestamp_ms: int) -> str:
    if not timestamp_ms:
        return "-"
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime("%d.%m.%Y %H:%M")


def esc(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


if __name__ == "__main__":
    main()
