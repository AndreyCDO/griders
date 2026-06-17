"""
bybit_client.py — клиент Bybit V5 Public API.
Используется для получения рыночных данных (свечи, стакан, тикеры, OI, фандинг).
Аутентификация не требуется — все эндпоинты публичные.
"""

from typing import Any

import httpx

from config import BYBIT_BASE, HTTP_TIMEOUT


class BybitError(Exception):
    pass


async def get(path: str, params: dict | None = None) -> Any:
    """GET-запрос к Bybit V5. Возвращает data['result'] или бросает BybitError."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.get(f"{BYBIT_BASE}{path}", params=params or {})
    r.raise_for_status()
    data = r.json()
    if data.get("retCode", 0) != 0:
        raise BybitError(f"Bybit {data['retCode']}: {data.get('retMsg', 'unknown error')}")
    return data.get("result", {})


def to_symbol(raw: str) -> str:
    """Нормализует тикер: 'btc-usdt' → 'BTCUSDT'."""
    return raw.upper().replace("-", "").replace("/", "").replace("_", "")
