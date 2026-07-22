# Live-History Calibrated Backtest

Текущий свечной backtest GRID DCA 2.9 полезен как модель сигналов, но он не видит весь production reality: отказы webhook, реальные задержки, защитные ордера, ручные закрытия, funding, проскальзывание и то, какие сигналы фактически были пропущены пользователями из-за лимитов, баланса и защит.

В проекте уже пишется история, на которой можно сделать более достоверный backtest.

## Какие live-данные уже есть

Основные таблицы:

- `ai_tradingview_events` - входящие TradingView события, `source_message_id`, `strategy_code`, `pair`, `side`, `confidence`, `raw_payload`, `processed_at`, `processing_error`.
- `ai_signals` - fanout события по пользователям/подключениям: `status`, `order_volume`, `payload`, `response`, `error_message`, timing webhook, confirmation timestamps.
- `ai_site_trade_deals` - сделка Griders от отправленного webhook до закрытия: `sent_at`, `grid_snapshot`, `strategy_snapshot`, `expected_profits`, `planned_volumes`, `status`, `closed_pnl`, `api_entry_value`, `qty`, `avg_entry_price`, `avg_exit_price`, `roi_pct`, `r_multiple`, `outcome`, `close_reason`, `hold_seconds`, `raw_closed_pnl`.
- `ai_site_trade_daily_stats` и `ai_user_trade_daily_stats` - дневные агрегаты.
- `ai_admin_closed_pnl_rows` - дополнительная история Bybit/Cryptorg closed PnL для админской статистики.

Важный старт счетчика сделок в коде: `COUNTER_START_DATE = "2026-06-08"` в `webapp/trade_stats.py`. До этой даты live-история может быть неполной для site-wide сделок.

## Чем live-история лучше обычного backtest

Она позволяет измерить:

- сколько TradingView событий реально дошло до сервера;
- сколько сигналов стало `sent`, `skipped`, `failed`;
- причины пропусков: watchlist, лимиты, баланс, active positions, cooldown, stop guards, stale events;
- фактическую задержку webhook и confirmation;
- факт появления позиции и защитных ордеров;
- фактический `closed_pnl`, entry/exit price, qty, ROI, safety-order depth;
- manual/unknown close и аварийные cleanup-сценарии;
- реальные отличия между свечной моделью и исполнением Cryptorg/Bybit.

## Как сделать более достоверный backtest

1. Построить базовый candle backtest как сейчас: сигнал, вход на следующей 15m свече, TP/SL, DCA, тарифные лимиты.
2. На каждом смоделированном сигнале применить live-фильтры:
   - вероятность fanout по watchlist/tariff;
   - observed skip-rate по причинам;
   - лимиты активных сделок и active same-pair positions;
   - pair launch cooldown и stop guards;
   - rate failed/stale webhook.
3. Заменить идеализированное исполнение на live-calibration:
   - фактическое распределение `closed_pnl / api_entry_value`;
   - распределение `r_multiple` по pair/side/stage/close_reason;
   - observed safety-order depth из `matched_safety_orders`;
   - observed hold time.
4. Добавить execution haircut:
   - observed webhook failures;
   - average/percentile latency;
   - manual/unknown close rate;
   - difference between expected TP profit and actual `closed_pnl`.
5. Отдельно считать две версии:
   - `signal-model backtest`: чистая стратегия на свечах;
   - `live-calibrated backtest`: стратегия после реальных фильтров и исполнения.

## Read-only диагностика

На сервере можно запустить:

```bash
./venv/bin/python tools/analyze_live_trade_history.py --days 45 --json-out .private_reports/live-history-45d.json
```

Скрипт выводит только обезличенные агрегаты. Он не печатает пользователей, email, API keys, webhook URLs, encrypted secrets, raw payloads и `raw_closed_pnl`.

## Минимальные метрики для решения

- `signal_to_sent_rate` - сколько сигналов реально отправляется.
- `signal_skip_rate` и разбор skip reasons.
- `sent_to_closed_rate` и доля зависших open/canceled.
- `live_win_rate`, `live_stop_rate`.
- `avg_live_r_multiple` и распределение `r_multiple`.
- PnL по pair/side/close_reason.
- Worst days и stop clusters.
- Разница `expected_profits` vs real `closed_pnl`.

## Ограничения

- Если live-история началась 2026-06-08, она пока короткая относительно годового backtest.
- Закрытие `manual` и `unknown` нельзя автоматически считать TP/SL без проверки raw API rows.
- Если пользователь вмешивался руками, сделка остается полезной для execution reality, но не как чистая оценка стратегии.
- Реальные данные могут быть смещены тарифами, watchlist пользователей и малой выборкой.
