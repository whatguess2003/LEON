"""Signed link for the guest biography page. The natural key inside the link is hotel and phone."""
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone


def _secret() -> bytes:
    return str(os.getenv("JWT_SECRET") or "dev-secret").encode()


def _pack(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unpack(text: str) -> bytes:
    padded = str(text or "")
    while len(padded) % 4:
        padded += "="
    try:
        return base64.urlsafe_b64decode(padded.encode())
    except Exception:
        return b""


def guest_link(hotel_name: str, guest_phone: str) -> str:
    """A link suffix that stays valid for 7 days."""
    exp = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    body = json.dumps(
        {"hotel_name": str(hotel_name or "").upper(), "guest_phone": str(guest_phone or ""), "exp": exp},
        separators=(",", ":"),
    ).encode()
    sig = hmac.new(_secret(), body, hashlib.sha256).digest()
    return _pack(body) + "." + _pack(sig)


def read_guest_link(token: str) -> dict:
    """Hotel and phone when the link is intact and still within 7 days."""
    parts = str(token or "").split(".", 1)
    if len(parts) != 2:
        return {"error": "This link is not valid"}
    body = _unpack(parts[0])
    sig = _unpack(parts[1])
    if not body or not sig:
        return {"error": "This link is not valid"}
    expected = hmac.new(_secret(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return {"error": "This link is not valid"}
    try:
        data = json.loads(body.decode())
    except json.JSONDecodeError:
        return {"error": "This link is not valid"}
    try:
        until = datetime.fromisoformat(str(data.get("exp") or ""))
    except ValueError:
        return {"error": "This link is not valid"}
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > until:
        return {"error": "This link has expired"}
    hotel = str(data.get("hotel_name") or "").upper()
    phone = str(data.get("guest_phone") or "")
    if not hotel or not phone:
        return {"error": "This link is not valid"}
    return {"hotel_name": hotel, "guest_phone": phone}
