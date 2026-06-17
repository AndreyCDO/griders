"""Minimal Cryptorg Futures API auth check.

This script tests only the official read-only endpoint:
GET /bot-futures/access-list

It is useful for verifying API key/secret, IP whitelist and signing without
starting the MCP server.
"""

import asyncio
import base64
import hashlib
import hmac
import os
import time

import httpx
from dotenv import load_dotenv


async def main() -> None:
    load_dotenv()

    api_key = os.environ["CRYPTORG_API_KEY"]
    api_secret = os.environ["CRYPTORG_API_SECRET"]
    base_url = os.getenv("CRYPTORG_BASE_URL", "https://api2.cryptorg.net").rstrip("/")

    path = "/bot-futures/access-list"
    query = ""
    nonce = str(int(time.time()))
    payload = f"{path}/{nonce}/{query}"
    signature = hmac.new(
        api_secret.encode("utf-8"),
        base64.b64encode(payload.encode("utf-8")),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "CTG-API-SIGNATURE": signature,
        "CTG-API-KEY": api_key,
        "CTG-API-NONCE": nonce,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{base_url}{path}?", headers=headers)

    print(f"endpoint: {path}")
    print(f"status: {response.status_code}")
    print(f"key_length: {len(api_key)}")
    print(f"secret_length: {len(api_secret)}")
    print(f"nonce: {nonce}")
    print(f"payload: {payload!r}")
    print(f"body: {response.text[:1000]}")


if __name__ == "__main__":
    asyncio.run(main())
