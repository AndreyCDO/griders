# Griders TradingView Alerts

## GRID DCA 2.6 + GRID DCA 3.1

TradingView detects GRID DCA signals and sends ready JSON payloads to Griders. The server no longer scans Bybit public candles for these strategies. It only checks the user's risk limits, balance, active positions, risk pauses, and then sends the Cryptorg Ghost Bot webhook.
GRID DCA 2.6 also blocks repeated launches for the same pair for one minute, limits each user to one long webhook and one short webhook every 5 minutes, keeps a short 5-minute safety capacity for accepted launches while Cryptorg/read-only positions catch up, and filters entries during sharp BTC/ETH moves, local candle breakdowns against the grid, and RSI conditions on 15m and 1h.

`griders_grid_dca_dual_v3.pine` sends both:

- `grid_dca_v2` signals for the existing GRID DCA 2.6 strategy.
- `grid_dca_v3` signals for the admin-only stricter GRID DCA 3.1 strategy.

This keeps the same 20 TradingView alerts. Replace the old indicator on each chart with the dual script and keep the alert condition as `Any alert() function call`.

1. Open a USDT perpetual chart in TradingView, for example `BYBIT:TONUSDT.P`.
2. Add `griders_grid_dca_dual_v3.pine` as an indicator.
3. Create an alert for the indicator.
4. Select `Any alert() function call`.
5. Use webhook URL:

```text
https://griders.ru/integrations/tradingview/grid-dca/<TRADINGVIEW_WEBHOOK_SECRET>
```

6. Leave the alert message empty. The Pine script sends JSON itself through `alert()`.
7. Use `Once Per Bar Close`.

The symbol must be selected in the user's Griders watchlist for the selected connection, otherwise the server ignores the signal. GRID DCA 3.1 is processed only for admin users.
