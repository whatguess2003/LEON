"""Signed link for the guest biography page. The natural key inside the link is hotel and phone."""
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone


def _secret() -> bytes:
    return os.environ["JWT_SECRET"].encode()


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


def guest_link(hotel_id: str, guest_phone: str) -> str:
    """A link suffix that stays valid for 7 days."""
    exp = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    body = json.dumps(
        {"hotel_id": str(hotel_id or ""), "guest_phone": str(guest_phone or ""), "exp": exp},
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
    hotel = str(data.get("hotel_id") or "")
    phone = str(data.get("guest_phone") or "")
    if not hotel or not phone:
        return {"error": "This link is not valid"}
    return {"hotel_id": hotel, "guest_phone": phone}


def portal_url(hotel_id: str, guest_phone: str) -> str:
    """The guest portal address for this hotel and phone. The signed token is the page login."""
    origin = os.getenv("FRONTEND_ORIGINS", "http://localhost:8080").split(",")[0].strip().rstrip("/")
    return origin + "/guest.html?t=" + guest_link(hotel_id, guest_phone)


def deliver_portal_link(hotel_id: str, guest_phone: str, guest_name: str) -> dict:
    """Send the portal link on the approved WhatsApp template. The portal address is returned either way."""
    from booking_system.hotel_registry import read_hotel_by_id
    from booking_system.whatsapp_templates import link_template_key, render_template
    url = portal_url(hotel_id, guest_phone)
    hotel = read_hotel_by_id(hotel_id) or {}
    key = link_template_key()
    rendered = render_template(key, {"guest_name": guest_name, "hotel_name": hotel.get("hotel_name") or "", "guest_link": guest_link(hotel_id, guest_phone)}) if key else {"error": "WhatsApp template is not configured"}
    if rendered.get("error"):
        return {"portal_url": url, "error": rendered["error"]}
    from booking_system.front_desk_manager import intelligent_front_desk
    sent = intelligent_front_desk.send_whatsapp_template(
        guest_phone, rendered["template_name"], rendered["language"], rendered["values"], rendered["button"],
        rendered["sentence"], rendered.get("names"), rendered.get("named"),
    )
    if sent.get("error"):
        return {"portal_url": url, "error": sent["error"]}
    from booking_system.guest_manager import guest_manager
    guest_manager.save_whatsapp_event(sent["message_id"], guest_phone, hotel_id, datetime.now(timezone.utc).isoformat(), sent.get("body") or sent["template_name"], "out")
    return {"portal_url": url, "message_id": sent["message_id"]}
