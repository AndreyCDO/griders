# Deployment Notes

## Current State

The project is ready to upload to a server as a Python MCP stdio server.

Important: this is not an HTTP service. MCP stdio servers are normally started
by the MCP client process. Running `server.py` as a detached systemd service is
useful only for smoke checks/logging experiments, not for a normal MCP client
connection.

## Server Requirements

- Linux VPS, preferably Ubuntu/Debian.
- Python 3.11+.
- Outbound HTTPS access to:
  - `https://api2.cryptorg.net`
  - `https://api.bybit.com`
  - optional: `https://cryptopanic.com`
  - optional: `https://api.alternative.me`
- Cryptorg futures API key and secret.
- Cryptorg Ghost Bot webhook URL for actual execution.

## Recommended Upload

Upload the project directory to:

```bash
/opt/cryptorg-trader
```

Then on the server:

```bash
cd /opt/cryptorg-trader
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env
./venv/bin/python server.py --check
```

## Minimal `.env`

```bash
CRYPTORG_API_KEY=...
CRYPTORG_API_SECRET=...
CRYPTORG_BASE_URL=https://api2.cryptorg.net
BYBIT_BASE_URL=https://api.bybit.com
CRYPTORG_GHOST_WEBHOOK_URL=https://api3.cryptorg.net/crazy/hook/...
```

`CRYPTOPANIC_API_KEY` is optional.

## Connecting From an MCP Client

For local use on the same machine:

```json
{
  "mcpServers": {
    "cryptorg-futures-trader": {
      "command": "/opt/cryptorg-trader/venv/bin/python",
      "args": ["/opt/cryptorg-trader/server.py"]
    }
  }
}
```

For a remote VPS, use SSH as the MCP command if your client supports launching
stdio commands:

```json
{
  "mcpServers": {
    "cryptorg-futures-trader": {
      "command": "ssh",
      "args": [
        "user@server",
        "cd /opt/cryptorg-trader && ./venv/bin/python server.py"
      ]
    }
  }
}
```

## First Safe Checks

After connecting the MCP server, start read-only:

1. `get_market_overview`
2. `get_price` for `BTCUSDT`
3. `analyze_indicators` for `BTCUSDT`
4. `cryptorg_get_access_list`
5. `cryptorg_get_pair_list`
6. `cryptorg_account_information`
7. `cryptorg_list_bots`
8. `cryptorg_active_deals`

Do not call write tools until the read-only outputs are verified.

Preferred write tools:

- `ghost_open_deal`
- `ghost_send_payload`

Both are dry-run by default. They send only when `confirm=true`.

Legacy Cryptorg API write tools:

- `cryptorg_create_bot`
- `cryptorg_update_bot`
- `cryptorg_activate_bot`
- `cryptorg_deactivate_bot`
- `cryptorg_start_new_deal`
- `cryptorg_renew_tp_percentage`
- `cryptorg_complete_deal`
- `cryptorg_cancel_deal`
