# Griders TradingView Alerts

## GRID DCA 2.9

TradingView detects GRID DCA signals and sends ready JSON payloads to Griders. The server no longer scans Bybit public candles for these strategies. It only checks the user's risk limits, balance, active positions, risk pauses, and then sends the Cryptorg Ghost Bot webhook.
GRID DCA 2.9 also blocks repeated launches for the same pair for one minute, keeps a short 5-minute safety capacity for accepted launches while Cryptorg/read-only positions catch up, and filters entries during sharp BTC/ETH moves, local candle breakdowns against the grid, RSI conditions on 15m and 1h, and the BTC/ETH daily trend direction.
Compared with 2.8, GRID DCA 2.9 keeps the same DCA grid, increases take profit by market stage for trend and pullback signals, and uses a wider stop loss.
The Pine script also mirrors the server EMA20 guard: long alerts are not sent when both BTC and ETH are below daily EMA20, and short alerts are not sent when both BTC and ETH are above daily EMA20.

`griders_grid_dca_v29.pine` sends only `grid_dca_v2` signals for the current public GRID DCA 2.9 strategy.

Replace the old indicator on each chart with the GRID DCA 2.9 script and keep the alert condition as `Any alert() function call`.
Delete old TradingView alerts and create new ones after replacing the script. TradingView can keep running the script snapshot that existed when the alert was created.

1. Open a USDT perpetual chart in TradingView, for example `BYBIT:ONDOUSDT.P`.
2. Add `griders_grid_dca_v29.pine` as an indicator.
3. Create an alert for the indicator.
4. Select `Any alert() function call`.
5. Use webhook URL:

```text
https://griders.ru/integrations/tradingview/grid-dca/<TRADINGVIEW_WEBHOOK_SECRET>
```

6. Leave the alert message empty. The Pine script sends JSON itself through `alert()`.
7. Use `Once Per Bar Close`.

The symbol must be selected in the user's Griders watchlist for the selected connection, otherwise the server ignores the signal.

The webhook payload includes trend diagnostics:

- `global_market_regime`: `uptrend`, `downtrend`, or `neutral`.
- `trend_filter_passed`: whether the current signal direction passed the daily trend filter.
- `trend_filter_reason`: `passed`, `disabled`, `long_blocked_by_daily_trend`, or `short_blocked_by_daily_trend`.
- `btc_daily_move_3`, `eth_daily_move_3`, `global_daily_move_3`.
- `btc_daily_above_ema20`, `eth_daily_above_ema20`.
