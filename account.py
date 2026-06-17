"""Cryptorg futures account and catalog tools."""

from datetime import datetime, timezone
from typing import Any

import cryptorg_client as ctg


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("data", "result", "items", "list"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


async def get_access_list() -> dict:
    data = await ctg.access_list()
    return {"accesses": _as_list(data), "raw": data}


async def get_pair_list() -> dict:
    data = await ctg.pair_list()
    return {"pairs": _as_list(data), "raw": data}


async def get_account_information() -> dict:
    data = await ctg.account_information()
    return {
        "account": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def get_balance() -> dict:
    data = await get_account_information()
    account = data["account"]
    balances = account.get("balances") if isinstance(account, dict) else None
    positions = account.get("positions") if isinstance(account, dict) else None
    return {
        "balances": balances or [],
        "positions": positions or [],
        "raw": account,
        "timestamp": data["timestamp"],
    }


async def get_positions(symbol: str | None = None) -> dict:
    data = await get_account_information()
    account = data["account"]
    positions = account.get("positions", []) if isinstance(account, dict) else []
    if symbol:
        sym = symbol.upper()
        positions = [
            p for p in positions
            if str(p.get("symbol") or p.get("pair") or "").upper() == sym
        ]
    return {"positions": positions, "count": len(positions), "raw": account}
