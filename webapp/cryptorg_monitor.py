"""Cryptorg Bybit Liquidity read-only helpers for monitoring."""

import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import httpx

RECV_WINDOW = os.getenv("BYBIT_RECV_WINDOW", "30000")
BASE_URL = "https://api.bybit.com"
_HTTP_CLIENT: httpx.AsyncClient | None = None
SIGNATURE_ERROR_HELP = (
    "API вернул ошибку подписи. Проверьте, что ключ API и секрет API взяты из одной "
    "связки Cryptorg Bybit Liquidity, а при смене ключа секрет тоже был введён заново."
)


class CryptorgMonitorError(Exception):
    pass


def _http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=15,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _HTTP_CLIENT


def _query(params: dict) -> str:
    return urlencode(sorted(params.items()))


async def monitor_get(path: str, api_key: str, api_secret: str, params: dict) -> dict:
    query = _query(params)
    timestamp = str(round(time.time() * 1000))
    payload = timestamp + api_key + RECV_WINDOW + query
    signature = hmac.new(api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        "Accept": "application/json",
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": signature,
    }
    response = await _http_client().get(f"{BASE_URL}{path}?{query}", headers=headers)
    response.raise_for_status()
    data = response.json()
    if int(data.get("retCode", -1)) != 0:
        ret_msg = data.get("retMsg", "Ошибка API мониторинга Cryptorg")
        if "Error sign" in ret_msg:
            raise CryptorgMonitorError(SIGNATURE_ERROR_HELP)
        raise CryptorgMonitorError(ret_msg)
    return data.get("result", {})


async def wallet_balance(api_key: str, api_secret: str) -> dict:
    return await monitor_get(
        "/v5/account/wallet-balance",
        api_key,
        api_secret,
        {"accountType": "UNIFIED"},
    )


async def positions(api_key: str, api_secret: str) -> list[dict]:
    result = await monitor_get(
        "/v5/position/list",
        api_key,
        api_secret,
        {"category": "linear", "settleCoin": "USDT", "limit": 50},
    )
    return list(result.get("list") or [])


async def open_orders(api_key: str, api_secret: str, symbol: str, limit: int = 50) -> list[dict]:
    result = await monitor_get(
        "/v5/order/realtime",
        api_key,
        api_secret,
        {
            "category": "linear",
            "symbol": symbol.upper(),
            "openOnly": 0,
            "limit": min(limit, 50),
        },
    )
    return list(result.get("list") or [])


async def closed_pnl_history_page(
    api_key: str,
    api_secret: str,
    start_ms: int,
    end_ms: int,
    limit: int = 100,
    cursor: str | None = None,
) -> dict:
    params = {
        "category": "linear",
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": min(limit, 100),
    }
    if cursor:
        params["cursor"] = cursor
    return await monitor_get(
        "/v5/position/closed-pnl",
        api_key,
        api_secret,
        params,
    )


async def closed_pnl_history(api_key: str, api_secret: str, start_ms: int, end_ms: int, limit: int = 100) -> list[dict]:
    result = await closed_pnl_history_page(api_key, api_secret, start_ms, end_ms, limit=limit)
    return list(result.get("list") or [])


async def order_history(api_key: str, api_secret: str, symbol: str, start_ms: int, end_ms: int, limit: int = 50) -> list[dict]:
    result = await monitor_get(
        "/v5/order/history",
        api_key,
        api_secret,
        {
            "category": "linear",
            "symbol": symbol.upper(),
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": min(limit, 50),
        },
    )
    return list(result.get("list") or [])


def extract_usdt_balance(wallet: dict) -> float:
    accounts = wallet.get("list") or []
    if not accounts:
        return 0.0
    account = accounts[0]
    for coin in account.get("coin", []):
        if coin.get("coin") == "USDT":
            return float(coin.get("walletBalance") or coin.get("equity") or 0)
    return float(account.get("totalWalletBalance") or account.get("totalEquity") or 0)
