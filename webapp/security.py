"""Password hashing, signed sessions and secret encryption."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time

from cryptography.fernet import Fernet

from . import settings


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 240_000)
    return f"pbkdf2_sha256${salt}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest_b64 = stored.split("$", 2)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    expected = base64.b64decode(digest_b64.encode())
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 240_000)
    return hmac.compare_digest(actual, expected)


def _sign(value: str) -> str:
    return hmac.new(settings.APP_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_session(user_id: int) -> str:
    data = {"uid": user_id, "iat": int(time.time()), "nonce": secrets.token_hex(8)}
    payload = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    return f"{payload}.{_sign(payload)}"


def make_pending_2fa(user_id: int) -> str:
    data = {"uid": user_id, "iat": int(time.time()), "purpose": "2fa", "nonce": secrets.token_hex(8)}
    payload = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    return f"{payload}.{_sign(payload)}"


def parse_pending_2fa(token: str | None, max_age: int = 600) -> int | None:
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
    except Exception:
        return None
    if data.get("purpose") != "2fa":
        return None
    if int(time.time()) - int(data.get("iat", 0)) > max_age:
        return None
    return int(data["uid"])


def parse_session(token: str | None, max_age: int | None = None) -> int | None:
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
    except Exception:
        return None
    if int(time.time()) - int(data.get("iat", 0)) > (max_age or settings.SESSION_IDLE_TIMEOUT_SECONDS):
        return None
    return int(data["uid"])


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.APP_SECRET.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    return _fernet().decrypt(value.encode()).decode()


def mask_secret(value: str | None, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * (len(value) - visible) + value[-visible:]


def make_reset_token() -> str:
    return secrets.token_urlsafe(32)


def hash_reset_token(token: str) -> str:
    return hmac.new(settings.APP_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()


def make_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_uri(email: str, secret: str, issuer: str = "Griders") -> str:
    from urllib.parse import quote

    label = quote(f"{issuer}:{email}")
    issuer_q = quote(issuer)
    return f"otpauth://totp/{label}?secret={secret}&issuer={issuer_q}&algorithm=SHA1&digits=6&period=30"


def totp_code(secret: str, for_time: int | None = None, step: int = 30, digits: int = 6) -> str:
    normalized = secret.strip().replace(" ", "").upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode((normalized + padding).encode(), casefold=True)
    counter = int((for_time or time.time()) // step)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    cleaned = "".join(ch for ch in code if ch.isdigit())
    if len(cleaned) != 6:
        return False
    now = int(time.time())
    for offset in range(-window, window + 1):
        if hmac.compare_digest(totp_code(secret, now + offset * 30), cleaned):
            return True
    return False
