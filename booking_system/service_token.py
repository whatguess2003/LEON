"""Main server -> channel manager credential: {hotel_id, exp} signed with CM_SERVICE_SECRET, valid for 60 seconds. The channel
manager accepts a request only when the token is intact, unexpired and names the hotel_id the request acts on. Without the
secret nothing is issued and everything is refused."""

import base64
import hashlib
import hmac
import json
import os
import time

HEADER = "X-Leon-Service"
LIFETIME_SECONDS = 60


def _secret() -> bytes:
    return str(os.environ.get("CM_SERVICE_SECRET") or "").encode()


def _pack(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unpack(text: str) -> bytes:
    padded = str(text or "") + "=" * (-len(str(text or "")) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode())
    except ValueError:
        return b""


def issue(hotel_id: str) -> str:
    """The header value for one call about hotel_id; '' when the secret is not set."""
    secret = _secret()
    if not secret:
        return ""
    body = json.dumps({"hotel_id": str(hotel_id or ""), "exp": int(time.time()) + LIFETIME_SECONDS}, separators=(",", ":")).encode()
    return _pack(body) + "." + _pack(hmac.new(secret, body, hashlib.sha256).digest())


def refusal(token: str, hotel_id: str) -> str:
    """'' when the token allows acting on hotel_id; otherwise the reason."""
    secret = _secret()
    if not secret:
        return "CM_SERVICE_SECRET is not set"
    body_text, _, sig_text = str(token or "").partition(".")
    body, sig = _unpack(body_text), _unpack(sig_text)
    if not body or not sig or not hmac.compare_digest(sig, hmac.new(secret, body, hashlib.sha256).digest()):
        return "service credential is missing or invalid"
    try:
        claims = json.loads(body.decode())
    except ValueError:
        return "service credential is missing or invalid"
    if int(claims.get("exp") or 0) < int(time.time()):
        return "service credential has expired"
    if not hotel_id or not hmac.compare_digest(str(claims.get("hotel_id") or ""), str(hotel_id)):
        return "service credential is for another hotel"
    return ""
