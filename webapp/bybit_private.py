"""Cryptorg Bybit Liquidity read-only helpers for monitoring."""

import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import httpx

RECV_WINDOW = os.getenv("BYBIT_RECV_WINDOW", "30000")
BASE_URL = "https://api.bybit.com"
SIGNATURE_ERROR_HELP = (
    "API вернул Error sign. Проверьте, что API key и API secret взяты из одной "
    "связки Cryptorg Bybit Liquidity, а при смене ключа secret тоже был введен заново."
)


class BybitPrivateError(Exception):
    pass


def _query(params: dict) -> str:
    return urlencode(sorted(params.items()))


async def bybit_get(path: str, api_key: str, api_secret: str, params: dict) -> dict:
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
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{BASE_URL}{path}?{query}", headers=headers)
    response.raise_for_status()
    data = response.json()
    if int(data.get("retCode", -1)) != 0:
        ret_msg = data.get("retMsg", "Bybit API error")
        if "Error sign" in ret_msg:
            raise BybitPrivateError(SIGNATURE_ERROR_HELP)
        raise BybitPrivateError(ret_msg)
    return data.get("result", {})


async def wallet_balance(api_key: str, api_secret: str) -> dict:
    return await bybit_get(
        "/v5/account/wallet-balance",
        api_key,
        api_secret,
        {"accountType": "UNIFIED"},
    )


async def positions(api_key: str, api_secret: str) -> list[dict]:
    result = await bybit_get(
        "/v5/position/list",
        api_key,
        api_secret,
        {"category": "linear", "settleCoin": "USDT", "limit": 50},
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
