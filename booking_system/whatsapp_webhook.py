"""WhatsApp Cloud API webhook. Meta verifies with GET, then posts messages and statuses."""
import hashlib
import hmac
import os
from datetime import datetime, timezone


def _same(left: str, right: str) -> bool:
    left = str(left or "")
    right = str(right or "")
    if len(left) != len(right) or not left:
        return False
    return hmac.compare_digest(left, right)


def subscription_challenge(mode: str, token: str, challenge: str) -> str:
    expected = str(os.getenv("WHATSAPP_VERIFY_TOKEN") or "")
    if str(mode or "") == "subscribe" and _same(token, expected):
        return str(challenge or "")
    return ""


def signature_ok(raw: bytes, header: str) -> bool:
    secret = str(os.getenv("WHATSAPP_APP_SECRET") or "").encode()
    given = str(header or "")
    if not secret or not given.startswith("sha256="):
        return False
    digest = hmac.new(secret, raw, hashlib.sha256).hexdigest()
    return _same(given, "sha256=" + digest)


def _phone(value: str) -> str:
    number = str(value or "").strip()
    if number.startswith("+"):
        return number
    digits = "".join(ch for ch in number if ch.isdigit())
    if not digits:
        return ""
    return "+" + digits


def _sent_at(timestamp: str) -> str:
    try:
        return datetime.fromtimestamp(int(str(timestamp or "")), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _guest_hotel(phone: str) -> str:
    """The hotel_id when exactly one hotel has an active guest with this phone, else ''."""
    from booking_system.guest_manager import guest_manager
    hotels = guest_manager.hotels_for_phone(phone)
    return hotels[0]["hotel_id"] if len(hotels) == 1 else ""


def _media_part(message: dict) -> dict:
    kind = str((message or {}).get("type") or "")
    part = (message or {}).get(kind)
    if not isinstance(part, dict) or not part.get("id"):
        return {}
    if not part.get("mime_type") and not part.get("sha256"):
        return {}
    return part


def _text_body(message: dict) -> str:
    text = str(((message or {}).get("text") or {}).get("body") or "").strip()
    if text:
        return text
    part = _media_part(message)
    caption = str(part.get("caption") or "").strip()
    if caption:
        return caption
    filename = str(part.get("filename") or "").strip()
    if filename:
        return filename
    return str((message or {}).get("type") or "")


def _keep_media(source_ref: str, message: dict) -> None:
    part = _media_part(message)
    media_id = str(part.get("id") or "")
    if not media_id:
        return
    from booking_system.guest_manager import guest_manager
    from booking_system.whatsapp_media import download_media
    fetched = download_media(media_id)
    if fetched.get("error"):
        return
    filename = str(part.get("filename") or "")
    guest_manager.save_guest_media(source_ref, fetched["data"], fetched["mime"], filename)
    guest_manager.save_whatsapp_event(source_ref, media_mime=fetched["mime"], media_name=filename)


def accept_webhook(payload: dict) -> list:
    """Save inbound messages and return each (hotel_id, phone) whose needs should be summarized. A hotel user's message is a desk
    turn instead."""
    from booking_system.guest_manager import guest_manager
    touched = []
    for entry in (payload or {}).get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            if str((change or {}).get("field") or "") != "messages":
                continue
            value = (change or {}).get("value") or {}
            for message in value.get("messages") or []:
                phone = _phone(str((message or {}).get("from") or ""))
                source_ref = str((message or {}).get("id") or "")
                sent_at = _sent_at(str((message or {}).get("timestamp") or ""))
                if not phone or not source_ref or not sent_at:
                    continue
                from booking_system.leon_turn import manager_account, note_manager_inbound, receive_manager_whatsapp
                from booking_system.whatsapp_list import list_reply
                hotel_user = manager_account(phone)
                if hotel_user:
                    note_manager_inbound(hotel_user["user_id"], sent_at)
                    selection = list_reply(message)
                    row_id = ""
                    text = ""
                    if selection:
                        row_id = selection.get("id") or ""
                        text = str(selection.get("title") or "").strip()
                        detail = str(selection.get("description") or "").strip()
                        if detail:
                            text = text + "\n" + detail
                    elif str((message or {}).get("type") or "") == "text":
                        text = _text_body(message)
                    if text:
                        receive_manager_whatsapp(hotel_user, source_ref, text, row_id)
                    continue
                hotel = _guest_hotel(phone)
                guest_manager.save_whatsapp_event(source_ref, phone, hotel, sent_at, _text_body(message), "in")
                _keep_media(source_ref, message)
                pair = (hotel, phone)
                if hotel and pair not in touched:
                    touched.append(pair)
            for status in value.get("statuses") or []:
                source_ref = str((status or {}).get("id") or "")
                phone = _phone(str((status or {}).get("recipient_id") or ""))
                state = str((status or {}).get("status") or "")
                if not source_ref or not state:
                    continue
                errors = (status or {}).get("errors") or []
                first = errors[0] if errors else {}
                detail = " ".join(part for part in (str((first or {}).get("code") or ""), str((first or {}).get("title") or (first or {}).get("message") or "")) if part)
                if detail:
                    print("whatsapp status " + state + " " + detail, flush=True)
                else:
                    print("whatsapp status " + state, flush=True)
                guest_manager.save_whatsapp_event(source_ref, phone, _guest_hotel(phone), _sent_at(str((status or {}).get("timestamp") or "")), "", "out", state if not detail else state + " " + detail)
    return touched
