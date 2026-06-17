"""Authentication helpers for the official Cryptorg Futures Bots API."""

import base64
import hashlib
import hmac
import time
from urllib.parse import urlencode

from config import CRYPTORG_API_KEY, CRYPTORG_API_SECRET


def build_query(params: dict | None = None) -> str:
    """Build the query string exactly as it is used for request signing."""
    if not params:
        return ""
    clean = {k: v for k, v in params.items() if v is not None}
    return urlencode(clean, doseq=True)


def api_headers(path: str, query: str = "") -> dict:
    nonce = str(int(time.time()))
    payload = f"{path}/{nonce}/{query}"
    encoded = base64.b64encode(payload.encode("utf-8"))
    signature = hmac.new(
        CRYPTORG_API_SECRET.encode("utf-8"),
        encoded,
        hashlib.sha256,
    ).hexdigest()

    return {
        "CTG-API-SIGNATURE": signature,
        "CTG-API-KEY": CRYPTORG_API_KEY,
        "CTG-API-NONCE": nonce,
        "Content-Type": "application/json",
    }
