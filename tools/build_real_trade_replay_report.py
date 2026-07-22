"""Build an anonymized real-trade replay report from production Griders data.

This is not a candle backtest. It reconstructs the actual chain that Griders
recorded: sent signal -> user connection -> Cryptorg closed deal. It is meant
to validate the published backtests against real execution.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.db import fetch_all, fetch_one  # noqa: E402


OUT_HTML = ROOT / "webapp/static/reports/grid-dca-real-trade-replay.html"
OUT_JSON = ROOT / "webapp/static/reports/grid-dca-real-trade-replay.json"
DEFAULT_STRATEGY_CODE = "grid_dca_v2"
DEFAULT_V29_START = "2026-07-17T00:00:00+00:00"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY_CODE)
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--v29-start", default=DEFAULT_V29_START)
    parser.add_argument("--html-out", default=str(OUT_HTML))
    parser.add_argument("--json-out", default=str(OUT_JSON))
    args = parser.parse_args()

    bounds = _data_bounds(args.strategy)
    end_dt = _parse_dt(args.end) if args.end else datetime.now(timezone.utc)
    start_dt = _parse_dt(args.start) if args.start else _as_utc(bounds.get("min_sent")) or (end_dt - timedelta(days=60))
    v29_start_dt = _parse_dt(args.v29_start)

    available_rows = _deal_rows(args.strategy, start_dt, end_dt)
    v29_rows = [row for row in available_rows if _as_utc(row.get("closed_at")) and _as_utc(row.get("closed_at")) >= v29_start_dt]
    signal_rows = _signal_status_rows(args.strategy, start_dt, end_dt)
    event_rows = _event_status_rows(args.strategy, start_dt, end_dt)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_code": args.strategy,
        "period": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
        "v29_window": {"start": v29_start_dt.isoformat(), "end": end_dt.isoformat()},
        "data_bounds": _json_safe(bounds),
        "coverage": _coverage(args.strategy, start_dt, end_dt),
        "signal_status": signal_rows,
        "event_status": event_rows,
        "available_live": _period_report("Вся доступная live-история", available_rows, start_dt, end_dt),
        "v29_since_launch": _period_report("Окно после запуска GRID DCA 2.9", v29_rows, v29_start_dt, end_dt),
        "methodology": {
            "type": "real_trade_replay",
            "description": "Фактические закрытые сделки Griders, связанные с signal_id и закрытием Cryptorg.",
            "tariff_classification": "Тариф определяется по strategy_snapshot/grid_snapshot на момент отправки, а не по текущему тарифу пользователя.",
            "limitations": [
                "Историческая версия стратегии не сохранена отдельным полем: strategy_code остается grid_dca_v2, поэтому до 17.07.2026 в данных есть более ранние параметры.",
                "Сделки без strategy_snapshot попадают в отдельный бакет unknown_snapshot.",
                "Ручные вмешательства и неточные close_reason учитываются как они записаны в базе.",
                "Отчет не моделирует сделки, которые могли бы быть открыты у пользователей, если бы у них были другие балансы, тарифы или включенные пары.",
            ],
        },
    }

    html_out = Path(args.html_out)
    json_out = Path(args.json_out)
    html_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    html_out.write_text(_html(report), encoding="utf-8")
    print(json.dumps({"html": str(html_out.resolve()), "json": str(json_out.resolve()), "summary": _summary_for_stdout(report)}, ensure_ascii=False, indent=2))


def _data_bounds(strategy: str) -> dict[str, Any]:
    return fetch_one(
        """
        SELECT MIN(sent_at) AS min_sent, MAX(sent_at) AS max_sent,
               MIN(closed_at) AS min_closed, MAX(closed_at) AS max_closed,
               COUNT(*) AS rows_total
        FROM ai_site_trade_deals
        WHERE strategy_code=%s
        """,
        (strategy,),
    ) or {}


def _coverage(strategy: str, start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    return fetch_one(
        """
        SELECT COUNT(*) AS total_rows,
               SUM(status='closed') AS closed_rows,
               SUM(status='open') AS open_rows,
               SUM(status='canceled') AS canceled_rows,
               SUM(signal_id IS NOT NULL) AS rows_with_signal,
               SUM(strategy_snapshot IS NOT NULL) AS rows_with_strategy_snapshot,
               SUM(grid_snapshot IS NOT NULL) AS rows_with_grid_snapshot,
               SUM(raw_closed_pnl IS NOT NULL) AS rows_with_raw_closed,
               SUM(closed_pnl IS NOT NULL) AS rows_with_pnl,
               SUM(close_reason='unknown') AS unknown_close_rows
        FROM ai_site_trade_deals
        WHERE strategy_code=%s AND sent_at >= %s AND sent_at < %s
        """,
        (strategy, _mysql_dt(start_dt), _mysql_dt(end_dt)),
    ) or {}


def _deal_rows(strategy: str, start_dt: datetime, end_dt: datetime) -> list[dict[str, Any]]:
    return list(
        fetch_all(
            """
            SELECT d.id, d.user_id, d.connection_id, d.signal_id, d.pair, d.side,
                   d.sent_at, d.closed_at, d.closed_pnl, d.close_reason, d.outcome,
                   d.roi_pct, d.r_multiple, d.hold_seconds, d.matched_safety_orders,
                   d.api_entry_value, d.qty, d.avg_exit_price, d.avg_entry_price,
                   d.strategy_snapshot, d.grid_snapshot, d.signal_reasons,
                   s.status AS signal_status, s.webhook_response_ms,
                   s.confirmation_status, s.position_confirmed_at,
                   s.protective_orders_confirmed_at
            FROM ai_site_trade_deals d
            LEFT JOIN ai_signals s ON s.id=d.signal_id
            WHERE d.strategy_code=%s
              AND d.status='closed'
              AND d.closed_at IS NOT NULL
              AND d.closed_at >= %s
              AND d.closed_at < %s
            ORDER BY d.closed_at ASC, d.id ASC
            """,
            (strategy, _mysql_dt(start_dt), _mysql_dt(end_dt)),
        )
    )


def _signal_status_rows(strategy: str, start_dt: datetime, end_dt: datetime) -> list[dict[str, Any]]:
    return list(
        fetch_all(
            """
            SELECT status, COUNT(*) AS signals,
                   AVG(webhook_response_ms) AS avg_webhook_response_ms,
                   SUM(position_confirmed_at IS NOT NULL) AS position_confirmed,
                   SUM(protective_orders_confirmed_at IS NOT NULL) AS protective_confirmed
            FROM ai_signals
            WHERE strategy_code=%s AND created_at >= %s AND created_at < %s
            GROUP BY status ORDER BY status
            """,
            (strategy, _mysql_dt(start_dt), _mysql_dt(end_dt)),
        )
    )


def _event_status_rows(strategy: str, start_dt: datetime, end_dt: datetime) -> list[dict[str, Any]]:
    return list(
        fetch_all(
            """
            SELECT side, COUNT(*) AS events,
                   SUM(processed_at IS NOT NULL) AS processed,
                   SUM(processing_error IS NOT NULL AND processing_error <> '') AS errors
            FROM ai_tradingview_events
            WHERE strategy_code=%s AND created_at >= %s AND created_at < %s
            GROUP BY side ORDER BY side
            """,
            (strategy, _mysql_dt(start_dt), _mysql_dt(end_dt)),
        )
    )


def _period_report(label: str, rows: list[dict[str, Any]], start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    return {
        "label": label,
        "period": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
        "summary": _metrics(rows),
        "by_tariff_snapshot": _grouped(rows, lambda row: _tariff_bucket(row)["code"], with_bucket_meta=True),
        "by_pair": _grouped(rows, lambda row: str(row.get("pair") or "")),
        "by_side": _grouped(rows, lambda row: str(row.get("side") or "")),
        "by_stage": _grouped(rows, lambda row: _stage(row)),
        "by_close_reason": _grouped(rows, lambda row: str(row.get("close_reason") or "")),
        "event_replay": _event_replay(rows),
        "daily_chart": _daily_chart(rows, start_dt, end_dt),
        "worst_events": _worst_events(rows),
        "worst_trades": _worst_trades(rows),
    }


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl_values = [_float(row.get("closed_pnl")) for row in rows]
    pnl = sum(pnl_values)
    gross_profit = sum(value for value in pnl_values if value > 0)
    gross_loss = -sum(value for value in pnl_values if value < 0)
    wins = sum(1 for value in pnl_values if value > 0)
    losses = sum(1 for value in pnl_values if value < 0)
    breakeven = len(rows) - wins - losses
    users = len({int(row["user_id"]) for row in rows if row.get("user_id") is not None})
    connections = len({int(row["connection_id"]) for row in rows if row.get("connection_id") is not None})
    events = len({_event_id(row) for row in rows if _event_id(row)})
    volume = sum(_closed_trade_volume(row) for row in rows)
    return {
        "trades": len(rows),
        "users": users,
        "connections": connections,
        "tradingview_events": events,
        "pnl": pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else math.inf,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": wins / len(rows) * 100 if rows else 0.0,
        "stops": sum(1 for row in rows if row.get("close_reason") == "stop_loss"),
        "take_profits": sum(1 for row in rows if row.get("close_reason") == "take_profit"),
        "manual": sum(1 for row in rows if row.get("close_reason") == "manual"),
        "unknown": sum(1 for row in rows if row.get("close_reason") == "unknown"),
        "avg_pnl": pnl / len(rows) if rows else 0.0,
        "avg_roi_pct": _avg([_float(row.get("roi_pct")) for row in rows if row.get("roi_pct") is not None]),
        "avg_r_multiple": _avg([_float(row.get("r_multiple")) for row in rows if row.get("r_multiple") is not None]),
        "avg_hold_hours": _avg([_float(row.get("hold_seconds")) / 3600 for row in rows if row.get("hold_seconds") is not None]),
        "avg_safety_orders": _avg([_float(row.get("matched_safety_orders")) for row in rows if row.get("matched_safety_orders") is not None]),
        "traded_volume": volume,
        "max_drawdown": _max_drawdown(rows),
    }


def _grouped(rows: list[dict[str, Any]], key_fn, with_bucket_meta: bool = False) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[key_fn(row) or "unknown"].append(row)
    result = []
    for key, subset in buckets.items():
        item = {"key": key, **_metrics(subset)}
        if with_bucket_meta:
            item["name"] = _bucket_name(key)
        result.append(item)
    return sorted(result, key=lambda item: _float(item.get("pnl")), reverse=True)


def _event_replay(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        event_id = _event_id(row)
        by_event[event_id or "unknown"].append(row)
    event_rows = []
    for event_id, subset in by_event.items():
        first = subset[0]
        event_rows.append(
            {
                "event_id": event_id,
                "pair": first.get("pair"),
                "side": first.get("side"),
                "stage": _stage(first),
                **_metrics(subset),
            }
        )
    event_rows = sorted(event_rows, key=lambda item: _float(item.get("pnl")))
    return {
        "events": len(event_rows),
        "avg_users_per_event": _avg([_float(row.get("trades")) for row in event_rows]),
        "profitable_events": sum(1 for row in event_rows if _float(row.get("pnl")) > 0),
        "loss_events": sum(1 for row in event_rows if _float(row.get("pnl")) < 0),
        "worst": event_rows[:20],
        "best": sorted(event_rows, key=lambda item: _float(item.get("pnl")), reverse=True)[:20],
    }


def _daily_chart(rows: list[dict[str, Any]], start_dt: datetime, end_dt: datetime) -> list[dict[str, Any]]:
    current = start_dt.date()
    end_day = end_dt.date()
    days: dict[str, dict[str, Any]] = {}
    while current <= end_day:
        key = current.isoformat()
        days[key] = {"date": key, "label": current.strftime("%d.%m"), "pnl": 0.0, "trades": 0}
        current += timedelta(days=1)
    for row in rows:
        closed = _as_utc(row.get("closed_at"))
        if not closed:
            continue
        key = closed.date().isoformat()
        if key in days:
            days[key]["pnl"] += _float(row.get("closed_pnl"))
            days[key]["trades"] += 1
    cumulative = 0.0
    chart = []
    for key in sorted(days):
        item = days[key]
        cumulative += _float(item["pnl"])
        chart.append(
            {
                **item,
                "pnl": round(_float(item["pnl"]), 8),
                "pnlText": f"{_float(item['pnl']):+.2f} USDT",
                "cumulative": round(cumulative, 8),
                "cumulativeText": f"{cumulative:+.2f} USDT",
            }
        )
    return chart


def _worst_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _event_replay(rows)["worst"][:10]


def _worst_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    worst = sorted(rows, key=lambda row: _float(row.get("closed_pnl")))[:20]
    result = []
    for row in worst:
        result.append(
            {
                "closed_at": _as_utc(row.get("closed_at")).isoformat() if _as_utc(row.get("closed_at")) else "",
                "pair": row.get("pair"),
                "side": row.get("side"),
                "stage": _stage(row),
                "pnl": _float(row.get("closed_pnl")),
                "close_reason": row.get("close_reason"),
                "tariff_bucket": _tariff_bucket(row)["code"],
                "event_id": _event_id(row),
            }
        )
    return result


def _tariff_bucket(row: dict[str, Any]) -> dict[str, str]:
    snapshot = _json_obj(row.get("strategy_snapshot"))
    grid = _json_obj(row.get("grid_snapshot"))
    if not snapshot:
        return {"code": "unknown_snapshot", "name": "Нет snapshot"}
    max_total = _int(snapshot.get("max_active_deals"))
    max_long = _int(snapshot.get("max_long_deals"))
    max_short = _int(snapshot.get("max_short_deals"))
    first_order = _float((grid.get("open") or {}).get("orderVolume") if isinstance(grid.get("open"), dict) else None)
    if first_order <= 0:
        first_order = _float(snapshot.get("min_order_volume"))
    watchlist = str(snapshot.get("watchlist") or "")
    pair_count = len([item for item in watchlist.split(",") if item.strip()])
    exact = (max_total, max_long, max_short)
    if exact == (4, 4, 4) and first_order <= 6.01:
        return {"code": "free", "name": "Бесплатный"}
    if exact == (6, 6, 6) and first_order <= 12.01:
        return {"code": "free_plus", "name": "Бесплатный Плюс"}
    if exact == (8, 8, 8) and first_order <= 60.01:
        return {"code": "start", "name": "Старт"}
    if exact == (10, 10, 10) and first_order <= 120.01:
        return {"code": "start_plus", "name": "Старт Плюс"}
    if exact == (12, 12, 12) and first_order <= 600.01:
        return {"code": "premium", "name": "Премиум"}
    if max_total >= 40 and max_long >= 40 and max_short >= 40 and first_order <= 2000.01:
        return {"code": "premium_plus", "name": "Премиум Плюс"}
    if pair_count >= 17:
        return {"code": "custom", "name": "Индивидуальные настройки"}
    return {"code": "unknown_snapshot", "name": "Не распознано"}


def _bucket_name(code: str) -> str:
    names = {
        "free": "Бесплатный",
        "free_plus": "Бесплатный Плюс",
        "start": "Старт",
        "start_plus": "Старт Плюс",
        "premium": "Премиум",
        "premium_plus": "Премиум Плюс",
        "custom": "Индивидуальные настройки",
        "unknown_snapshot": "Нет/не распознан snapshot",
    }
    return names.get(code, code)


def _stage(row: dict[str, Any]) -> str:
    reasons = _json_obj(row.get("signal_reasons"), default=[])
    if isinstance(reasons, list):
        for reason in reasons:
            text = str(reason)
            marker = "стадия рынка:"
            if marker in text:
                return text.split(marker, 1)[1].strip() or "unknown"
    return "unknown"


def _event_id(row: dict[str, Any]) -> str:
    reasons = _json_obj(row.get("signal_reasons"), default=[])
    text = json.dumps(reasons, ensure_ascii=False) if not isinstance(reasons, str) else reasons
    match = re.search(r"TradingView GRID event #(\d+)", text)
    return match.group(1) if match else ""


def _closed_trade_volume(row: dict[str, Any]) -> float:
    entry = _float(row.get("api_entry_value"))
    qty = abs(_float(row.get("qty")))
    exit_price = _float(row.get("avg_exit_price"))
    exit_value = qty * exit_price if qty > 0 and exit_price > 0 else 0.0
    if entry > 0 and exit_value > 0:
        return entry + exit_value
    if entry > 0:
        return entry * 2
    return 0.0


def _max_drawdown(rows: list[dict[str, Any]]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for row in sorted(rows, key=lambda item: (_as_utc(item.get("closed_at")) or datetime.min.replace(tzinfo=timezone.utc), int(item.get("id") or 0))):
        equity += _float(row.get("closed_pnl"))
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _html(report: dict[str, Any]) -> str:
    available = report["available_live"]
    v29 = report["v29_since_launch"]
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Фактический replay сделок GRID DCA · Griders</title>
  <meta name="description" content="Фактический replay сделок Griders GRID DCA: реальные сигналы, подключения, закрытия Cryptorg, PnL, тарифные snapshot и качество данных.">
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="stylesheet" href="/static/app.css?v=20260720-real-replay">
  <style>
    .report-page {{ padding-top:32px; padding-bottom:48px; }}
    .report-hero {{ display:flex; justify-content:space-between; gap:24px; align-items:flex-start; }}
    .metric-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .metric-grid div {{ border:1px solid var(--line); border-radius:8px; padding:12px; background:var(--bg-soft); }}
    .metric-grid span {{ display:block; color:var(--text-muted); font-size:12px; }}
    .metric-grid strong {{ display:block; margin-top:4px; font-size:20px; }}
    .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    .table-scroll {{ overflow:auto; }}
    .report-monitor-card {{ height:360px; }}
    .chart-head {{ display:flex; justify-content:space-between; gap:16px; align-items:center; margin-bottom:12px; }}
    .pos {{ color:var(--accent-dark); }}
    .neg {{ color:var(--warn); }}
    @media (max-width:900px) {{ .report-hero,.two-col {{ display:block; }} .metric-grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <main class="container report-page">
    <section class="panel report-hero">
      <div>
        <p class="eyebrow">REAL TRADE REPLAY</p>
        <h1>Фактический replay сделок GRID DCA</h1>
        <p class="muted">Это не свечной бэктест. Здесь собраны реальные сделки, которые Griders отправил в Cryptorg и затем сопоставил с закрытыми позициями.</p>
      </div>
      <div class="report-date"><span>Период данных</span><strong>{esc(report['period']['start'][:10])} → {esc(report['period']['end'][:10])}</strong></div>
    </section>
    {_summary_section('Вся доступная live-история', available)}
    {_summary_section('Окно после запуска GRID DCA 2.9', v29)}
    <section class="two-col">
      {_chart_section(available)}
      {_chart_section(v29)}
    </section>
    <section class="panel">
      <h2>По snapshot тарифов</h2>
      {_table(available['by_tariff_snapshot'], ['name','trades','users','pnl','profit_factor','stops','win_rate','traded_volume'], {'name':'Тариф snapshot','trades':'Сделок','users':'Польз.','pnl':'PnL','profit_factor':'PF','stops':'Стопов','win_rate':'Win rate','traded_volume':'Объём'})}
    </section>
    <section class="two-col">
      <section class="panel">
        <h2>Пары</h2>
        {_table(available['by_pair'], ['key','trades','pnl','profit_factor','stops','win_rate'], {'key':'Пара','trades':'Сделок','pnl':'PnL','profit_factor':'PF','stops':'Стопов','win_rate':'Win rate'})}
      </section>
      <section class="panel">
        <h2>Стороны и стадии</h2>
        {_table(available['by_side'], ['key','trades','pnl','profit_factor','stops','win_rate'], {'key':'Сторона','trades':'Сделок','pnl':'PnL','profit_factor':'PF','stops':'Стопов','win_rate':'Win rate'})}
        <br>
        {_table(available['by_stage'], ['key','trades','pnl','profit_factor','stops','win_rate'], {'key':'Стадия','trades':'Сделок','pnl':'PnL','profit_factor':'PF','stops':'Стопов','win_rate':'Win rate'})}
      </section>
    </section>
    <section class="panel">
      <h2>Худшие TradingView события</h2>
      {_table(available['event_replay']['worst'], ['event_id','pair','side','stage','trades','users','pnl','stops','win_rate'], {'event_id':'Event','pair':'Пара','side':'Сторона','stage':'Стадия','trades':'Сделок','users':'Польз.','pnl':'PnL','stops':'Стопов','win_rate':'Win rate'})}
    </section>
    <section class="panel">
      <h2>Качество данных</h2>
      <div class="metric-grid">
        {_metric('Всего строк', num(report['coverage'].get('total_rows')))}
        {_metric('Закрытых', num(report['coverage'].get('closed_rows')))}
        {_metric('С signal_id', num(report['coverage'].get('rows_with_signal')))}
        {_metric('Со strategy snapshot', num(report['coverage'].get('rows_with_strategy_snapshot')))}
        {_metric('С grid snapshot', num(report['coverage'].get('rows_with_grid_snapshot')))}
        {_metric('С raw Cryptorg', num(report['coverage'].get('rows_with_raw_closed')))}
        {_metric('Unknown close', num(report['coverage'].get('unknown_close_rows')))}
        {_metric('Открытых сейчас', num(report['coverage'].get('open_rows')))}
      </div>
      <p class="muted">Главное ограничение: историческая версия стратегии не хранится отдельным полем, поэтому весь период до 17.07.2026 не является чистым GRID DCA 2.9. Для чистой 2.9 выборки данных пока мало.</p>
      <p class="muted"><strong>Предупреждение:</strong> прибыль в прошлом не означает прибыль в будущем. Этот replay показывает фактическую историю, но не гарантирует будущий результат.</p>
    </section>
  </main>
  {chart_js()}
</body>
</html>"""


def _summary_section(title: str, period: dict[str, Any]) -> str:
    m = period["summary"]
    return f"""
    <section class="panel">
      <h2>{esc(title)}</h2>
      <p class="muted">{esc(period['period']['start'][:10])} - {esc(period['period']['end'][:10])}</p>
      <div class="metric-grid">
        {_metric('PnL', money(m['pnl']), css_num(m['pnl']))}
        {_metric('Сделок', num(m['trades']))}
        {_metric('Пользователей', num(m['users']))}
        {_metric('TV событий', num(m['tradingview_events']))}
        {_metric('Profit factor', fmt(m['profit_factor']))}
        {_metric('Win rate', f"{fmt(m['win_rate'])}%")}
        {_metric('Стопов', num(m['stops']))}
        {_metric('Макс. просадка', money(m['max_drawdown']), 'neg' if _float(m['max_drawdown']) > 0 else '')}
        {_metric('Средний PnL', money(m['avg_pnl']), css_num(m['avg_pnl']))}
        {_metric('Средний ROI', f"{fmt(m['avg_roi_pct'])}%")}
        {_metric('Сред. СО', fmt(m['avg_safety_orders']))}
        {_metric('Объём', money(m['traded_volume']))}
      </div>
    </section>"""


def _chart_section(period: dict[str, Any]) -> str:
    m = period["summary"]
    return f"""
      <section class="panel">
        <div class="chart-head">
          <div><h2>{esc(period['label'])}</h2><small class="muted">PnL по закрытым сделкам</small></div>
          <strong class="{css_num(m['pnl'])}">{money(m['pnl'])}</strong>
        </div>
        <div class="monitor-chart-card report-monitor-card" data-empty="Нет данных">
          <script type="application/json" class="report-chart-data">{json.dumps(period['daily_chart'], ensure_ascii=False)}</script>
          <svg class="monitor-svg report-monitor-svg" viewBox="0 0 920 360" role="img" aria-label="График фактического PnL"></svg>
          <div class="monitor-tooltip" hidden></div>
        </div>
      </section>"""


def _table(rows: list[dict[str, Any]], fields: list[str], labels: dict[str, str]) -> str:
    head = "".join(f"<th>{esc(labels.get(field, field))}</th>" for field in fields)
    body = []
    for row in rows[:60]:
        cells = []
        for field in fields:
            value = row.get(field)
            cls = ""
            if field in {"pnl"}:
                value = money(value)
                cls = css_num(row.get(field))
            elif field in {"profit_factor"}:
                value = fmt(value)
            elif field in {"win_rate"}:
                value = f"{fmt(value)}%"
            elif field in {"traded_volume"}:
                value = money(value)
            elif field in {"users", "trades", "stops"}:
                value = num(value)
            cells.append(f"<td class=\"{cls}\">{esc(value)}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    return f"<div class=\"table-scroll\"><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def _metric(label: str, value: Any, cls: str = "") -> str:
    return f"<div><span>{esc(label)}</span><strong class=\"{cls}\">{esc(value)}</strong></div>"


def chart_js() -> str:
    return r"""
<script>
(() => {
  const width = 920, height = 360;
  const margin = { top: 22, right: 28, bottom: 48, left: 112 };
  const plotTop = 8, plotBottom = 280, barTop = 294, barBottom = height - margin.bottom;
  const plotWidth = width - margin.left - margin.right;
  const ns = "http://www.w3.org/2000/svg";
  const fmt = (v) => `${Number(v || 0).toFixed(2)} USDT`;
  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[ch]));
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
    if (!data.length) return;
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
    el("rect", { x: 0, y: 0, width, height, rx: 10, fill: "#fbfdfc" });
    axis.ticks.forEach((value) => {
      const y = yAt(value);
      el("line", { x1: margin.left, x2: width - margin.right, y1: y, y2: y, stroke: "#dfe8e4", "stroke-width": 1 });
      el("text", { x: margin.left - 18, y: y + 4, "text-anchor": "end", fill: "#667680" }).textContent = Number(value).toFixed(Math.abs(value) >= 1 ? 1 : 2);
    });
    el("line", { x1: margin.left - 8, x2: width - margin.right, y1: yAt(0), y2: yAt(0), stroke: "#99a7ad", "stroke-width": 1, "stroke-dasharray": "3 5" });
    data.forEach((item, index) => {
      const x = xAt(index);
      const h = barHeight(Number(item.trades || 0));
      el("rect", { x: x - 4, y: barBottom - h, width: 8, height: h, rx: 2, fill: "#3b78a8", opacity: "0.42" });
      if (index % Math.max(1, Math.ceil(data.length / 8)) === 0 || index === data.length - 1) {
        el("text", { x, y: height - 18, "text-anchor": "middle", fill: "#667680" }).textContent = item.label;
      }
    });
    const points = data.map((item, index) => `${xAt(index)},${yAt(Number(item.cumulative || 0))}`).join(" ");
    el("polyline", { points, fill: "none", stroke: "#00856f", "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round" });
    const hoverLine = el("line", { y1: plotTop, y2: barBottom, stroke: "#7b8d94", "stroke-width": 1, "stroke-dasharray": "4 4", opacity: 0 });
    const focus = el("circle", { r: 5, fill: "#fff", stroke: "#00856f", "stroke-width": 3, opacity: 0 });
    const hit = el("rect", { x: margin.left, y: plotTop, width: plotWidth, height: barBottom - plotTop, fill: "transparent" });
    const tooltip = root.querySelector(".monitor-tooltip");
    hit.addEventListener("mousemove", (event) => {
      const rect = svg.getBoundingClientRect();
      const x = ((event.clientX - rect.left) / rect.width) * width;
      const index = Math.max(0, Math.min(data.length - 1, Math.round(((x - margin.left) / plotWidth) * (data.length - 1))));
      const item = data[index], px = xAt(index), py = yAt(Number(item.cumulative || 0));
      hoverLine.setAttribute("x1", px); hoverLine.setAttribute("x2", px); hoverLine.setAttribute("opacity", "0.8");
      focus.setAttribute("cx", px); focus.setAttribute("cy", py); focus.setAttribute("opacity", "1");
      if (tooltip) {
        tooltip.hidden = false;
        tooltip.innerHTML = `<strong>${escapeHtml(item.date)}</strong><br>PnL за день: ${escapeHtml(item.pnlText || fmt(item.pnl))}<br>PnL кумулятивный: ${escapeHtml(item.cumulativeText || fmt(item.cumulative))}<br>Сделок: ${Number(item.trades || 0)}`;
        tooltip.style.left = `${Math.min(Math.max((px / width) * rect.width + 10, 8), rect.width - 210)}px`;
        tooltip.style.top = `${Math.max((py / height) * rect.height - 28, 8)}px`;
      }
    });
    hit.addEventListener("mouseleave", () => {
      hoverLine.setAttribute("opacity", "0");
      focus.setAttribute("opacity", "0");
      if (tooltip) tooltip.hidden = true;
    });
  });
})();
</script>
"""


def _summary_for_stdout(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": report["available_live"]["summary"],
        "v29_since_launch": report["v29_since_launch"]["summary"],
    }


def _json_obj(value: Any, default: Any | None = None) -> Any:
    if default is None:
        default = {}
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_utc(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc))
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mysql_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def fmt(value: Any, digits: int = 2) -> str:
    number = _float(value)
    if math.isinf(number):
        return "∞"
    return f"{number:.{digits}f}"


def money(value: Any) -> str:
    return f"{_float(value):+,.2f} USDT".replace(",", " ")


def num(value: Any) -> str:
    return f"{_int(value):,}".replace(",", " ")


def css_num(value: Any) -> str:
    number = _float(value)
    return "pos" if number > 0 else "neg" if number < 0 else ""


def esc(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


if __name__ == "__main__":
    main()
