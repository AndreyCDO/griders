"""MCP server for a Cryptorg Futures bot/deal trading assistant."""

import argparse
import asyncio
import json
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

import config
import risk
from config import setup_logging, validate_config

log = setup_logging()


def obj(props: dict | None = None, required: list[str] | None = None) -> dict:
    schema = {"type": "object", "properties": props or {}}
    if required:
        schema["required"] = required
    return schema


TOOLS: list[Tool] = [
    Tool(
        name="get_price",
        description="Current Bybit linear futures ticker: price, 24h stats, funding, bid/ask.",
        inputSchema=obj({"symbol": {"type": "string"}, "category": {"type": "string", "default": "linear"}}, ["symbol"]),
    ),
    Tool(
        name="get_candles",
        description="Bybit OHLCV candles for technical analysis. Intervals: 1, 3, 5, 15, 30, 60, 240, D.",
        inputSchema=obj({
            "symbol": {"type": "string"},
            "interval": {"type": "string", "default": "1"},
            "limit": {"type": "integer", "default": 200},
            "category": {"type": "string", "default": "linear"},
        }, ["symbol"]),
    ),
    Tool(
        name="get_orderbook",
        description="Bybit order book with spread and buy/sell pressure.",
        inputSchema=obj({
            "symbol": {"type": "string"},
            "depth": {"type": "integer", "default": 25},
            "category": {"type": "string", "default": "linear"},
        }, ["symbol"]),
    ),
    Tool(
        name="get_recent_trades",
        description="Recent Bybit trades and buyer/seller aggression.",
        inputSchema=obj({
            "symbol": {"type": "string"},
            "limit": {"type": "integer", "default": 60},
            "category": {"type": "string", "default": "linear"},
        }, ["symbol"]),
    ),
    Tool(
        name="get_funding_rate",
        description="Recent Bybit funding history and interpretation.",
        inputSchema=obj({"symbol": {"type": "string"}}, ["symbol"]),
    ),
    Tool(
        name="get_open_interest",
        description="Bybit open interest trend.",
        inputSchema=obj({
            "symbol": {"type": "string"},
            "interval_time": {"type": "string", "default": "5min"},
            "limit": {"type": "integer", "default": 20},
        }, ["symbol"]),
    ),
    Tool(
        name="analyze_indicators",
        description="Technical analysis: RSI, MACD, Bollinger, Stochastic, VWAP, ATR, EMA, S/R and verdict.",
        inputSchema=obj({
            "symbol": {"type": "string"},
            "interval": {"type": "string", "default": "1"},
            "limit": {"type": "integer", "default": 200},
            "category": {"type": "string", "default": "linear"},
        }, ["symbol"]),
    ),
    Tool(
        name="get_market_overview",
        description="Compare liquid Bybit futures pairs, sorted by 24h turnover.",
        inputSchema=obj({
            "symbols": {"type": "array", "items": {"type": "string"}},
            "category": {"type": "string", "default": "linear"},
        }),
    ),
    Tool(
        name="cryptorg_get_access_list",
        description="Cryptorg futures access list.",
        inputSchema=obj(),
    ),
    Tool(
        name="cryptorg_get_pair_list",
        description="Cryptorg futures tradable pair list.",
        inputSchema=obj(),
    ),
    Tool(
        name="cryptorg_account_information",
        description="Cryptorg futures account information with balances and positions.",
        inputSchema=obj(),
    ),
    Tool(
        name="cryptorg_list_bots",
        description="List Cryptorg futures bots, optionally filtered by access, pair or status.",
        inputSchema=obj({
            "access_id": {"type": "integer"},
            "pair": {"type": "string"},
            "status": {"type": "string"},
        }),
    ),
    Tool(
        name="cryptorg_get_bot",
        description="Get Cryptorg futures bot details.",
        inputSchema=obj({"bot_id": {"type": ["integer", "string"]}}, ["bot_id"]),
    ),
    Tool(
        name="cryptorg_get_bot_logs",
        description="Get Cryptorg futures bot logs.",
        inputSchema=obj({"bot_id": {"type": ["integer", "string"]}}, ["bot_id"]),
    ),
    Tool(
        name="cryptorg_create_bot",
        description="Create a Cryptorg futures bot. Pass the official API fields as config.",
        inputSchema=obj({"config": {"type": "object"}}, ["config"]),
    ),
    Tool(
        name="cryptorg_update_bot",
        description="Update a Cryptorg futures bot. config must include botId or bot_id.",
        inputSchema=obj({"config": {"type": "object"}}, ["config"]),
    ),
    Tool(
        name="cryptorg_activate_bot",
        description="Turn on a Cryptorg futures bot.",
        inputSchema=obj({"bot_id": {"type": ["integer", "string"]}}, ["bot_id"]),
    ),
    Tool(
        name="cryptorg_deactivate_bot",
        description="Turn off a Cryptorg futures bot.",
        inputSchema=obj({"bot_id": {"type": ["integer", "string"]}}, ["bot_id"]),
    ),
    Tool(
        name="cryptorg_active_deals",
        description="List active Cryptorg futures bot deals.",
        inputSchema=obj({
            "bot_id": {"type": "integer"},
            "pair": {"type": "string"},
            "access_id": {"type": "integer"},
        }),
    ),
    Tool(
        name="cryptorg_deals_history",
        description="List historical Cryptorg futures bot deals.",
        inputSchema=obj({
            "bot_id": {"type": "integer"},
            "pair": {"type": "string"},
            "limit": {"type": "integer"},
        }),
    ),
    Tool(
        name="cryptorg_start_new_deal",
        description="Start a new deal for an existing Cryptorg futures bot.",
        inputSchema=obj({"bot_id": {"type": ["integer", "string"]}}, ["bot_id"]),
    ),
    Tool(
        name="cryptorg_renew_tp_percentage",
        description="Change take-profit percentage for an active Cryptorg futures deal.",
        inputSchema=obj({
            "deal_id": {"type": ["integer", "string"]},
            "tp_percentage": {"type": "number"},
        }, ["deal_id", "tp_percentage"]),
    ),
    Tool(
        name="cryptorg_complete_deal",
        description="Complete an active Cryptorg futures deal by market.",
        inputSchema=obj({"deal_id": {"type": ["integer", "string"]}}, ["deal_id"]),
    ),
    Tool(
        name="cryptorg_cancel_deal",
        description="Cancel a Cryptorg futures bot deal and leave the exchange position for manual management.",
        inputSchema=obj({"deal_id": {"type": ["integer", "string"]}}, ["deal_id"]),
    ),
    Tool(
        name="get_news",
        description="CryptoPanic news with simple vote-based sentiment. Requires CRYPTOPANIC_API_KEY.",
        inputSchema=obj({
            "currencies": {"type": "string", "default": ""},
            "filter_type": {"type": "string", "default": "hot"},
            "limit": {"type": "integer", "default": 8},
        }),
    ),
    Tool(
        name="get_fear_greed",
        description="Alternative.me Fear & Greed index.",
        inputSchema=obj(),
    ),
    Tool(
        name="calculate_position_size",
        description="Calculate risk-based notional size plus long/short SL and TP levels.",
        inputSchema=obj({
            "balance": {"type": "number"},
            "entry_price": {"type": "number"},
            "stop_loss_pct": {"type": "number"},
            "risk_pct": {"type": "number"},
            "leverage": {"type": "integer", "default": 1},
            "side": {"type": "string", "enum": ["LONG", "SHORT"], "default": "LONG"},
        }, ["balance", "entry_price"]),
    ),
]


app = Server("cryptorg-futures-trader")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments or {})
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    except Exception as e:
        log.error("Tool '%s' failed: %s", name, e, exc_info=True)
        return [TextContent(type="text", text=json.dumps({"error": str(e), "tool": name}, ensure_ascii=False))]


async def _dispatch(name: str, args: dict) -> Any:
    import account
    import trading
    import market
    import sentiment

    if name == "get_price":
        return await market.get_price(args["symbol"], args.get("category", "linear"))
    if name == "get_candles":
        return await market.get_candles(args["symbol"], args.get("interval", "1"), args.get("limit", 200), args.get("category", "linear"))
    if name == "get_orderbook":
        return await market.get_orderbook(args["symbol"], args.get("depth", 25), args.get("category", "linear"))
    if name == "get_recent_trades":
        return await market.get_recent_trades(args["symbol"], args.get("limit", 60), args.get("category", "linear"))
    if name == "get_funding_rate":
        return await market.get_funding_rate(args["symbol"])
    if name == "get_open_interest":
        return await market.get_open_interest(args["symbol"], args.get("interval_time", "5min"), args.get("limit", 20))
    if name == "analyze_indicators":
        return await market.analyze_indicators(args["symbol"], args.get("interval", "1"), args.get("limit", 200), args.get("category", "linear"))
    if name == "get_market_overview":
        return await market.get_market_overview(args.get("symbols"), args.get("category", "linear"))

    if name == "cryptorg_get_access_list":
        return await account.get_access_list()
    if name == "cryptorg_get_pair_list":
        return await account.get_pair_list()
    if name == "cryptorg_account_information":
        return await account.get_account_information()
    if name == "cryptorg_list_bots":
        return await trading.list_bots(args.get("access_id"), args.get("pair"), args.get("status"))
    if name == "cryptorg_get_bot":
        return await trading.get_bot(args["bot_id"])
    if name == "cryptorg_get_bot_logs":
        return await trading.get_bot_logs(args["bot_id"])
    if name == "cryptorg_create_bot":
        return await trading.create_bot(args["config"])
    if name == "cryptorg_update_bot":
        return await trading.update_bot(args["config"])
    if name == "cryptorg_activate_bot":
        return await trading.activate_bot(args["bot_id"])
    if name == "cryptorg_deactivate_bot":
        return await trading.deactivate_bot(args["bot_id"])
    if name == "cryptorg_active_deals":
        return await trading.active_deals(args.get("bot_id"), args.get("pair"), args.get("access_id"))
    if name == "cryptorg_deals_history":
        return await trading.deals_history(args.get("bot_id"), args.get("pair"), args.get("limit"))
    if name == "cryptorg_start_new_deal":
        return await trading.start_new_deal(args["bot_id"])
    if name == "cryptorg_renew_tp_percentage":
        return await trading.renew_tp_percentage(args["deal_id"], args["tp_percentage"])
    if name == "cryptorg_complete_deal":
        return await trading.complete_deal(args["deal_id"])
    if name == "cryptorg_cancel_deal":
        return await trading.cancel_deal(args["deal_id"])

    if name == "get_news":
        return await sentiment.get_news(args.get("currencies", ""), args.get("filter_type", "hot"), args.get("limit", 8))
    if name == "get_fear_greed":
        return await sentiment.get_fear_greed()
    if name == "calculate_position_size":
        return risk.calculate_position(
            balance=args["balance"],
            entry_price=args["entry_price"],
            stop_loss_pct=args.get("stop_loss_pct"),
            risk_pct=args.get("risk_pct"),
            leverage=args.get("leverage", 1),
            side=args.get("side", "LONG"),
        )

    raise ValueError(f"Unknown tool: {name}")


async def check_mode() -> None:
    from market import get_price

    ok = validate_config(log)
    if not ok:
        sys.exit(1)

    price = await get_price("BTCUSDT")
    log.info("Bybit OK: BTCUSDT=%s", price["price"])

    import account

    info = await account.get_account_information()
    log.info("Cryptorg OK: account-information keys=%s", list(info["account"].keys()) if isinstance(info["account"], dict) else type(info["account"]))


async def main() -> None:
    validate_config(log)
    log.info("Cryptorg Futures MCP server started with %s tools", len(TOOLS))
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cryptorg Futures MCP Server")
    parser.add_argument("--check", action="store_true", help="Check config and API connectivity")
    args_cli = parser.parse_args()

    if args_cli.check:
        asyncio.run(check_mode())
    else:
        asyncio.run(main())
