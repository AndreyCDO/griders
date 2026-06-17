# Cryptorg Futures MCP Trader

MCP server for an AI assistant that analyzes Bybit futures market data and
manages Cryptorg futures bots/deals through the official Cryptorg Futures Bots
API v2.

This project intentionally does not use spot trading and does not call raw
`/fapi/*` order endpoints. Cryptorg execution is bot/deal based.

## Architecture

```text
Bybit public API
  -> price, candles, order book, trades, funding, open interest
  -> indicators and setup analysis

Cryptorg Futures Bots API v2
  -> access list, pair list, account information
  -> list/create/update/activate/deactivate bots
  -> active deals, history, start deal, renew TP, complete/cancel deal

MCP server
  -> exposes safe tools to ChatGPT/Codex-compatible MCP clients
```

## Files

```text
server.py            MCP server and tool dispatcher
config.py            environment config
auth.py              CTG-API-* request signing
cryptorg_client.py   official Cryptorg Futures Bots API client
bybit_client.py      Bybit V5 public market-data client
market.py            market-data MCP tool implementations
indicators.py        RSI, MACD, BB, Stoch, VWAP, ATR, EMA, S/R
account.py           Cryptorg account/catalog tools
trading.py           Cryptorg futures bot/deal tools
risk.py              risk and position-size calculations
sentiment.py         CryptoPanic and Fear & Greed helpers
```

## Environment

```bash
CRYPTORG_API_KEY=...
CRYPTORG_API_SECRET=...
CRYPTORG_BASE_URL=https://api2.cryptorg.net
BYBIT_BASE_URL=https://api.bybit.com
CRYPTOPANIC_API_KEY=...
```

`CRYPTOPANIC_API_KEY` is optional; only `get_news` needs it.

## Install

```bash
pip install -r requirements.txt
python server.py --check
```

## MCP Tools

Market and analysis:

- `get_price`
- `get_candles`
- `get_orderbook`
- `get_recent_trades`
- `get_funding_rate`
- `get_open_interest`
- `get_market_overview`
- `analyze_indicators`
- `calculate_position_size`
- `get_news`
- `get_fear_greed`

Cryptorg futures:

- `cryptorg_get_access_list`
- `cryptorg_get_pair_list`
- `cryptorg_account_information`
- `cryptorg_list_bots`
- `cryptorg_get_bot`
- `cryptorg_get_bot_logs`
- `cryptorg_create_bot`
- `cryptorg_update_bot`
- `cryptorg_activate_bot`
- `cryptorg_deactivate_bot`
- `cryptorg_active_deals`
- `cryptorg_deals_history`
- `cryptorg_start_new_deal`
- `cryptorg_renew_tp_percentage`
- `cryptorg_complete_deal`
- `cryptorg_cancel_deal`

## Recommended Agent Process

Before any execution action:

1. Check `get_market_overview`.
2. Check `get_fear_greed`.
3. Check `get_funding_rate` and `get_open_interest` for the selected symbol.
4. Run `analyze_indicators` on 1m and 5m.
5. Check `get_orderbook` and `get_recent_trades`.
6. Check `cryptorg_account_information` and `cryptorg_active_deals`.
7. Run `calculate_position_size`.
8. Only then propose a bot/deal action.

Execution tools should be treated as high risk. Prefer read-only analysis first,
then explicit user confirmation before `cryptorg_start_new_deal`,
`cryptorg_renew_tp_percentage`, `cryptorg_complete_deal`,
`cryptorg_cancel_deal`, `cryptorg_create_bot`, or `cryptorg_update_bot`.
