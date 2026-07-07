"""GRID DCA 2.7 TP+5 research backtest with local rebound and short-burst filters.

Research-only script. It does not change production strategy code.
Baseline: .private_reports/grid-dca-27-wide-limits-tp-plus5-summary.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from tools import backtest_grid_dca_26_year_tariffs as base
from tools.backtest_grid_dca_26_vs_31 import _signal_at, hourly_rsi_by_15m, indicators


BASELINE_JSON = Path(".private_reports/grid-dca-27-wide-limits-tp-plus5-summary.json")

TP_MULTIPLIER = 1.05
SHORT_BURST_WINDOW_MS = 30 * 60 * 1000


TARIFFS = [
    base.Tariff(
        code="free",
        name="Бесплатный",
        initial_deposit=50.0,
        pairs=[pair for pair in base.ALL_PAIRS if pair not in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}],
        max_total=4,
        max_long=4,
        max_short=4,
        first_order_mode="manual",
        manual_first_order=6.0,
        max_first_order=6.0,
    ),
    base.Tariff(
        code="start",
        name="Старт",
        initial_deposit=500.0,
        pairs=[pair for pair in base.ALL_PAIRS if pair != "BTCUSDT"],
        max_total=8,
        max_long=8,
        max_short=8,
        first_order_mode="deposit_pct",
        risk_pct=5.0,
        max_first_order=60.0,
    ),
    base.Tariff(
        code="premium",
        name="Премиум",
        initial_deposit=5000.0,
        pairs=base.ALL_PAIRS[:],
        max_total=12,
        max_long=12,
        max_short=12,
        first_order_mode="deposit_pct",
        risk_pct=5.0,
        max_first_order=600.0,
    ),
]


def _compact_tariff(row: dict) -> dict:
    metrics = row["metrics"]
    return {
        "code": row["code"],
        "name": row["name"],
        "trades": metrics["trades"],
        "pnl": metrics["pnl"],
        "return_pct": metrics["return_pct"],
        "profit_factor": metrics["profit_factor"],
        "stops": metrics["stops"],
        "win_rate": metrics["win_rate"],
        "max_drawdown": metrics["max_drawdown"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "skipped": metrics["skipped"],
        "by_pair": row.get("by_pair", []),
        "worst_trades": row.get("worst_trades", []),
    }


def _short_local_rebound(signal: dict) -> bool:
    if signal["side"] != "short":
        return False
    btc3 = float(signal.get("btc_move_3") or 0.0)
    eth3 = float(signal.get("eth_move_3") or 0.0)
    btc1 = float(signal.get("btc_move_1") or 0.0)
    eth1 = float(signal.get("eth_move_1") or 0.0)
    candle = float(signal.get("candle_pct") or 0.0)
    bar_move = float(signal.get("bar_move_pct") or 0.0)
    macro_recovering = (btc3 >= 0.0 and eth3 >= 0.0) or (btc1 >= 0.15 and eth1 >= 0.15)
    coin_green = candle >= 0.10 or bar_move >= 0.10
    return macro_recovering and coin_green


def _short_burst_risk(signal: dict, recent_shorts: list[dict]) -> bool:
    if signal["side"] != "short":
        return False
    entry_time = int(signal["entry_time"])
    btc3 = float(signal.get("btc_move_3") or 0.0)
    eth3 = float(signal.get("eth_move_3") or 0.0)
    btc1 = float(signal.get("btc_move_1") or 0.0)
    eth1 = float(signal.get("eth_move_1") or 0.0)
    macro_not_confirming_down = max(btc3, eth3) >= -0.10 and max(btc1, eth1) >= 0.0
    if not macro_not_confirming_down:
        return False
    return any(
        item["symbol"] != signal["symbol"] and entry_time - int(item["entry_time"]) <= SHORT_BURST_WINDOW_MS
        for item in recent_shorts
    )


def all_signal_candidates_with_filters(start: datetime, all_rows: dict[str, list], mode: str) -> tuple[list[dict], dict]:
    btc = indicators(all_rows["BTCUSDT"])
    eth = indicators(all_rows["ETHUSDT"])
    trend_context = base._daily_trend_context(all_rows["BTCUSDT"], all_rows["ETHUSDT"])
    start_ms = int(start.timestamp() * 1000)
    raw_candidates: list[dict] = []
    skipped = {
        "tradingview_daily_regime_long": 0,
        "tradingview_daily_regime_short": 0,
        "short_local_rebound": 0,
        "short_burst_cooldown": 0,
    }

    for symbol in base.ALL_PAIRS:
        rows = all_rows[symbol]
        ind = indicators(rows)
        rsi60 = hourly_rsi_by_15m(rows)
        for index in range(max(50, 30, 20, 14, 3), len(rows) - 1):
            signal = _signal_at(base.SIGNAL_VERSION, ind, index, btc, eth, rsi60)
            if not signal:
                continue
            trend = base._trend_for_bar(trend_context, int(rows[index][0]))
            side = signal["side"]
            if side == "long" and trend.get("regime") == "downtrend":
                skipped["tradingview_daily_regime_long"] += 1
                continue
            if side == "short" and trend.get("regime") == "uptrend":
                skipped["tradingview_daily_regime_short"] += 1
                continue

            entry_index = index + 1
            entry_time = int(rows[entry_index][0])
            if entry_time < start_ms:
                continue

            close = ind["c"]
            btc_move_1 = (btc["c"][index] - btc["c"][index - 1]) / btc["c"][index - 1] * 100 if btc["c"][index - 1] else 0.0
            btc_move_3 = (btc["c"][index] - btc["c"][index - 3]) / btc["c"][index - 3] * 100 if btc["c"][index - 3] else 0.0
            eth_move_1 = (eth["c"][index] - eth["c"][index - 1]) / eth["c"][index - 1] * 100 if eth["c"][index - 1] else 0.0
            eth_move_3 = (eth["c"][index] - eth["c"][index - 3]) / eth["c"][index - 3] * 100 if eth["c"][index - 3] else 0.0
            candle_pct = (close[index] - ind["o"][index]) / ind["o"][index] * 100 if ind["o"][index] else 0.0
            bar_move_pct = (close[index] - close[index - 1]) / close[index - 1] * 100 if close[index - 1] else 0.0

            grid_signal = base._with_dca_max(signal)
            grid = {**grid_signal["grid"], "tp": float(grid_signal["grid"]["tp"]) * TP_MULTIPLIER}
            raw_candidates.append({
                "symbol": symbol,
                "side": side,
                "stage": signal["stage"],
                "grid": grid,
                "entry_index": entry_index,
                "entry_time": entry_time,
                "atr": signal["atr"],
                "volratio": signal["volratio"],
                "rsi15": signal["rsi15"],
                "rsi60": signal["rsi60"],
                "bbpos": signal["bbpos"],
                "bbwidth": signal["bbwidth"],
                "global_market_regime": trend.get("regime", "neutral"),
                "btc_daily_move_3": trend.get("btc_daily_move_3"),
                "eth_daily_move_3": trend.get("eth_daily_move_3"),
                "global_daily_move_3": trend.get("global_daily_move_3"),
                "btc_move_1": btc_move_1,
                "btc_move_3": btc_move_3,
                "eth_move_1": eth_move_1,
                "eth_move_3": eth_move_3,
                "candle_pct": candle_pct,
                "bar_move_pct": bar_move_pct,
            })

    candidates: list[dict] = []
    recent_shorts: list[dict] = []
    for candidate in sorted(raw_candidates, key=lambda item: (item["entry_time"], item["symbol"], item["side"])):
        entry_time = int(candidate["entry_time"])
        recent_shorts = [item for item in recent_shorts if entry_time - int(item["entry_time"]) <= SHORT_BURST_WINDOW_MS]
        if mode in {"combined", "local"} and _short_local_rebound(candidate):
            skipped["short_local_rebound"] += 1
            continue
        if mode in {"combined", "burst"} and _short_burst_risk(candidate, recent_shorts):
            skipped["short_burst_cooldown"] += 1
            continue
        candidates.append(candidate)
        if candidate["side"] == "short":
            recent_shorts.append(candidate)
    return candidates, skipped


def _render_comparison(data: dict) -> str:
    baseline_by_code = {row["code"]: row for row in data["baseline"]["tariffs"]}
    rows = []
    for row in data["filtered"]["tariffs"]:
        old = baseline_by_code[row["code"]]
        rows.append(f"""
        <tr>
          <td><strong>{row['name']}</strong></td>
          <td>{old['trades']} → {row['trades']}</td>
          <td>{old['stops']} → {row['stops']}</td>
          <td>{old['pnl']:.2f} → {row['pnl']:.2f}</td>
          <td>{row['pnl'] - old['pnl']:+.2f}</td>
          <td>{old['profit_factor']:.2f} → {row['profit_factor']:.2f}</td>
          <td>{old['max_drawdown']:.2f} → {row['max_drawdown']:.2f}</td>
        </tr>
        """)
    skipped = data["filtered"]["candidate_skipped"]
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>GRID DCA 2.7 TP+5 rebound/burst filters</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #10202b; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #d9e1e6; padding: 10px; text-align: left; }}
    th {{ color: #607080; font-size: 12px; text-transform: uppercase; }}
    .note {{ color: #607080; max-width: 980px; line-height: 1.45; }}
    code {{ background: #eef3f5; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>GRID DCA 2.7 TP+5: локальный отскок + пачка short-сигналов</h1>
  <p class="note">Период: {data['period']['start']} — {data['period']['end']}. Сравнение с baseline <code>wide_limits_tp_plus5</code>. Это исследовательский прогон, рабочая стратегия не изменялась.</p>
  <p class="note">Кандидатов после фильтров: {data['filtered']['signal_candidates']} из baseline {data['baseline']['signal_candidates']}. Отфильтровано: local rebound short {skipped['short_local_rebound']}, short burst {skipped['short_burst_cooldown']}.</p>
  <table>
    <thead><tr><th>Тариф</th><th>Сделки</th><th>Стопы</th><th>PnL</th><th>Δ PnL</th><th>PF</th><th>Max DD</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>"""


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", default="2026-06-11T14:45:00+00:00")
    parser.add_argument("--mode", choices=["combined", "local", "burst"], default="combined")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    start, end, rows = await base.fetch_all(args.days, end)
    candidates, candidate_skipped = all_signal_candidates_with_filters(start, rows, args.mode)
    results = [base.run_portfolio(tariff, candidates, rows) for tariff in TARIFFS]
    result_json = base.result_to_json(start, end, candidates, results)
    baseline = json.loads(BASELINE_JSON.read_text(encoding="utf-8-sig"))
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": result_json["period"],
        "variant": {
            "code": "wide_limits_tp_plus5_rebound_burst_filters",
            "mode": args.mode,
            "take_profit_multiplier": TP_MULTIPLIER,
            "short_burst_window_minutes": SHORT_BURST_WINDOW_MS // 60000,
            "filters": [
                "block short when BTC/ETH 3 bars are both non-negative or both 1-bar moves >= +0.15%, and coin candle/bar is >= +0.10%",
                "block new short if another alt short appeared in the previous 30 minutes while BTC/ETH are not confirming downside",
            ],
        },
        "baseline": {
            "variant": baseline.get("variant") or baseline.get("report_variant"),
            "signal_candidates": baseline.get("signal_candidates"),
            "tariffs": [_compact_tariff(row) for row in baseline["tariffs"]],
        },
        "filtered": {
            "signal_candidates": result_json["signal_candidates"],
            "candidate_skipped": candidate_skipped,
            "tariffs": [_compact_tariff(row) for row in result_json["tariffs"]],
        },
    }
    suffix = {
        "combined": "rebound-burst",
        "local": "local-rebound",
        "burst": "short-burst",
    }[args.mode]
    out_json = Path(f".private_reports/grid-dca-27-wide-limits-tp-plus5-{suffix}-summary.json")
    out_html = Path(f".private_reports/grid-dca-27-wide-limits-tp-plus5-{suffix}-comparison.html")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    out_html.write_text(_render_comparison(data), encoding="utf-8")
    print(json.dumps({
        "json": str(out_json.resolve()),
        "html": str(out_html.resolve()),
        "period": data["period"],
        "baseline_candidates": data["baseline"]["signal_candidates"],
        "filtered_candidates": data["filtered"]["signal_candidates"],
        "candidate_skipped": candidate_skipped,
        "metrics": [{row["code"]: {
            "trades": row["trades"],
            "pnl": row["pnl"],
            "stops": row["stops"],
            "profit_factor": row["profit_factor"],
            "max_drawdown": row["max_drawdown"],
        }} for row in data["filtered"]["tariffs"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
