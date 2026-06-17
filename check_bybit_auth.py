"""Minimal Bybit V5 private auth check using the current .env credentials."""

import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    api_key = os.environ["CRYPTORG_API_KEY"]
    api_secret = os.environ["CRYPTORG_API_SECRET"]
    recv_window = "5000"
    query = urlencode({"accountType": "UNIFIED"})
    timestamp = str(round(time.time() * 1000))
    payload = timestamp + api_key + recv_window + query
    signature = hmac.new(
        api_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Accept": "application/json",
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
    }

    response = httpx.get(
        f"https://api.bybit.com/v5/account/wallet-balance?{query}",
        headers=headers,
        timeout=15,
    )
    print(f"status: {response.status_code}")
    print(f"key_length: {len(api_key)}")
    print(f"secret_length: {len(api_secret)}")
    print(f"body: {response.text[:1000]}")


if __name__ == "__main__":
    main()
