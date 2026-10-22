import base64
import hashlib
import hmac
import json
from typing import Any


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign(key: bytes, purpose: str, data: dict[str, Any], now: float) -> str:
    """Create a tamper-proof token carrying `data`, bound to a purpose and issue time."""
    payload = json.dumps({"purpose": purpose, "iat": int(now), "data": data}, separators=(",", ":"))
    body = _b64encode(payload.encode())
    signature = _b64encode(hmac.new(key, body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def unsign(key: bytes, token: str, purpose: str, max_age: int, now: float) -> dict[str, Any] | None:
    """Return the token's data if it is authentic, has the purpose and is not expired."""
    body, _, signature = token.partition(".")
    expected = _b64encode(hmac.new(key, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64decode(body))
    except ValueError:
        return None
    if payload.get("purpose") != purpose or not 0 <= now - payload.get("iat", 0) <= max_age:
        return None
    return payload["data"]
