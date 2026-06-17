"""Cryptorg Futures Bots API tools.

Execution is intentionally bot/deal oriented: Cryptorg exposes futures bots
and bot deals through the public API, not raw exchange order endpoints.
"""

import logging
from typing import Any

import cryptorg_client as ctg

log = logging.getLogger("cryptorg-mcp.trading")


async def list_bots(
    access_id: int | None = None,
    pair: str | None = None,
    status: str | None = None,
) -> dict:
    params = {
        "accessId": access_id,
        "pair": pair.upper() if pair else None,
        "status": status,
    }
    data = await ctg.bots_all(params)
    return {"bots": _extract_list(data), "raw": data}


async def get_bot(bot_id: int | str) -> dict:
    data = await ctg.bot_detail(bot_id)
    return {"bot": data}


async def get_bot_logs(bot_id: int | str) -> dict:
    data = await ctg.bot_logs(bot_id)
    return {"logs": _extract_list(data), "raw": data}


async def activate_bot(bot_id: int | str) -> dict:
    log.warning("ACTIVATE BOT -> %s", bot_id)
    return {"activated": True, "result": await ctg.activate_bot(bot_id)}


async def deactivate_bot(bot_id: int | str) -> dict:
    log.warning("DEACTIVATE BOT -> %s", bot_id)
    return {"deactivated": True, "result": await ctg.deactivate_bot(bot_id)}


async def create_bot(config: dict) -> dict:
    log.warning("CREATE BOT -> %s", config)
    return {"created": True, "result": await ctg.create_bot(config)}


async def update_bot(config: dict) -> dict:
    if "botId" not in config and "bot_id" in config:
        config = {**config, "botId": config["bot_id"]}
    log.warning("UPDATE BOT -> %s", config)
    return {"updated": True, "result": await ctg.update_bot(config)}


async def active_deals(
    bot_id: int | None = None,
    pair: str | None = None,
    access_id: int | None = None,
) -> dict:
    params = {
        "botId": bot_id,
        "pair": pair.upper() if pair else None,
        "accessId": access_id,
    }
    data = await ctg.active_deals(params)
    return {"deals": _extract_list(data), "raw": data}


async def deals_history(
    bot_id: int | None = None,
    pair: str | None = None,
    limit: int | None = None,
) -> dict:
    params = {
        "botId": bot_id,
        "pair": pair.upper() if pair else None,
        "limit": limit,
    }
    data = await ctg.deals_history(params)
    return {"deals": _extract_list(data), "raw": data}


async def start_new_deal(bot_id: int | str) -> dict:
    log.warning("START NEW DEAL -> botId=%s", bot_id)
    return {"started": True, "result": await ctg.start_new_deal(bot_id)}


async def renew_tp_percentage(deal_id: int | str, tp_percentage: float) -> dict:
    log.warning("RENEW TP -> dealId=%s tp=%s", deal_id, tp_percentage)
    return {
        "renewed": True,
        "result": await ctg.renew_tp_percentage(deal_id, tp_percentage),
    }


async def complete_deal(deal_id: int | str) -> dict:
    log.warning("COMPLETE DEAL -> dealId=%s", deal_id)
    return {"completed": True, "result": await ctg.complete_deal(deal_id)}


async def cancel_deal(deal_id: int | str) -> dict:
    log.warning("CANCEL DEAL -> dealId=%s", deal_id)
    return {"cancelled": True, "result": await ctg.cancel_deal(deal_id)}


def _extract_list(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "result", "items", "list", "bots", "deals", "logs"):
            if isinstance(data.get(key), list):
                return data[key]
    return []
