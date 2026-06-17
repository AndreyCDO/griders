"""Async client for the official Cryptorg Futures Bots API v2.

This API controls Cryptorg futures bots and bot deals. It is not a raw
Binance/Bybit order API, so all execution methods map to /bot-futures/*.
"""

from typing import Any

import httpx

from auth import api_headers, build_query
from config import CRYPTORG_BASE, HTTP_TIMEOUT


class CryptorgError(Exception):
    pass


def _decode_response(data: Any) -> Any:
    if isinstance(data, dict):
        if data.get("status") == "error":
            raise CryptorgError(data.get("message") or str(data))
        if "error" in data and data["error"]:
            raise CryptorgError(str(data["error"]))
    return data


async def request(
    method: str,
    path: str,
    params: dict | None = None,
    body: dict | list | None = None,
) -> Any:
    query = build_query(params)
    url = f"{CRYPTORG_BASE.rstrip('/')}{path}?{query}"

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        kwargs = {"headers": api_headers(path, query)}
        if body is not None:
            kwargs["json"] = body
        response = await client.request(
            method=method.upper(),
            url=url,
            **kwargs,
        )
    response.raise_for_status()
    return _decode_response(response.json())


async def get(path: str, params: dict | None = None) -> Any:
    return await request("GET", path, params=params)


async def post(path: str, params: dict | None = None, body: dict | list | None = None) -> Any:
    return await request("POST", path, params=params, body=body)


async def access_list() -> Any:
    return await get("/bot-futures/access-list")


async def pair_list() -> Any:
    return await get("/bot-futures/pair-list")


async def bots_all(params: dict | None = None) -> Any:
    return await get("/bot-futures/all", params)


async def bot_detail(bot_id: int | str) -> Any:
    return await get("/bot-futures/detail", {"botId": bot_id})


async def bot_logs(bot_id: int | str) -> Any:
    return await get("/bot-futures/logs", {"botId": bot_id})


async def activate_bot(bot_id: int | str) -> Any:
    return await post("/bot-futures/activate", {"botId": bot_id})


async def deactivate_bot(bot_id: int | str) -> Any:
    return await post("/bot-futures/deactivate", {"botId": bot_id})


async def create_bot(params: dict) -> Any:
    return await post("/bot-futures/create", params)


async def update_bot(params: dict) -> Any:
    return await post("/bot-futures/update", params)


async def active_deals(params: dict | None = None) -> Any:
    return await get("/bot-futures/active-deals", params)


async def deals_history(params: dict | None = None) -> Any:
    return await get("/bot-futures/deals-history", params)


async def start_new_deal(bot_id: int | str) -> Any:
    return await post("/bot-futures/start-new-deal", {"botId": bot_id})


async def renew_tp_percentage(deal_id: int | str, tp_percentage: float) -> Any:
    return await post(
        "/bot-futures/renew-tp-percentage",
        {"dealId": deal_id, "tpPercentage": tp_percentage},
    )


async def complete_deal(deal_id: int | str) -> Any:
    return await post("/bot-futures/complete-deal", {"dealId": deal_id})


async def cancel_deal(deal_id: int | str) -> Any:
    return await post("/bot-futures/cancel-deal", {"dealId": deal_id})


async def account_information() -> Any:
    return await post("/bot-futures/account-information")
