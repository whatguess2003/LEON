"""Inbound Aiosell reservations. One endpoint receives book, modify and cancel; the payload is the full new state. The booking is the
core booking under (channel, channel_booking_ref = bookingId), so a repeat delivery updates it. Room codes map through
hotel_room_code_mapping. Special requests and the raw payload are documents keyed by booking_id; card values stay encrypted."""

import os
from datetime import datetime, timedelta

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from booking_system import db
from booking_system.booking_manager import booking_docs, booking_by_ref, cancel_in, channel_docs, write_channel_booking
from booking_system.guest_manager import Refused

ACTION_MESSAGES = {
    "book": "Reservation Updated Successfully",
    "modify": "Reservation Modified Successfully",
    "cancel": "Reservation Cancelled Successfully",
}


def authorize_basic(authorization_header: str):
    """Return an error message when the Aiosell basic credential does not match the environment."""
    username = str(os.environ.get("AIOSSELL_USERNAME") or "").strip()
    password = str(os.environ.get("AIOSSELL_PASSWORD") or "")
    if not username or not password:
        return "Aiosell username and password are not configured"
    header = str(authorization_header or "")
    prefix = "Basic "
    if not header.startswith(prefix):
        return "Unauthorized"
    import base64
    import hmac
    try:
        decoded = base64.b64decode(header[len(prefix):].strip()).decode("utf-8")
    except Exception:
        return "Unauthorized"
    supplied_user, separator, supplied_password = decoded.partition(":")
    if not separator:
        return "Unauthorized"
    if hmac.compare_digest(supplied_user, username) and hmac.compare_digest(supplied_password, password):
        return ""
    return "Unauthorized"


def _card_key():
    key = str(os.environ.get("AIOSSELL_CARD_KEY") or "")
    if len(key.encode("utf-8")) != 16:
        return b""
    return key.encode("utf-8")


def decrypt_card_field(hex_text: str, key: bytes) -> str:
    """Decrypt one AES-128-ECB hex field. Caller must not log the result."""
    raw = bytes.fromhex(str(hex_text or "").strip())
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    plain = decryptor.update(raw) + decryptor.finalize()
    pad = plain[-1]
    if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
        plain = plain[:-pad]
    return plain.decode("utf-8")


def _open_card(payload: dict) -> bool:
    """Decrypt card fields when a key is configured. Plaintext is not stored or returned."""
    card = payload.get("creditCard")
    if not isinstance(card, dict) or not card:
        return False
    key = _card_key()
    if not key:
        return True
    for value in card.values():
        text = str(value or "").strip()
        if not text:
            continue
        try:
            decrypt_card_field(text, key)
        except Exception:
            return True
    return True


def _stay_day(value):
    """Aiosell dates may be a date, a datetime, or text. Store YYYY-MM-DD."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value or "").strip()
    if not text:
        return ""
    candidates = [text, text[:10], text[:19].replace("T", " ")]
    patterns = ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S"]
    for candidate in candidates:
        for pattern in patterns:
            try:
                return datetime.strptime(candidate, pattern).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return ""


def _money(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _count(rooms, field):
    """Sum of one occupancy field over the rooms, or None when no room states it."""
    values = [str(room["occupancy"].get(field)).strip() for room in rooms
              if isinstance(room.get("occupancy"), dict) and room["occupancy"].get(field) is not None]
    return sum(int(value) for value in values if value.isdigit()) if values else None


def _room_nights(room: dict, checkin: str, checkout: str):
    """[(stay_date, sell_rate or None)] from the room's prices, else every night from checkin to checkout."""
    nights = {}
    for price in room.get("prices") if isinstance(room.get("prices"), list) else []:
        day = _stay_day(price.get("date") if isinstance(price, dict) else price)
        if day and day not in nights:
            nights[day] = _money(price.get("sellRate")) if isinstance(price, dict) else None
    if not nights and checkin and checkout:
        start, end = datetime.strptime(checkin, "%Y-%m-%d").date(), datetime.strptime(checkout, "%Y-%m-%d").date()
        nights = {(start + timedelta(days=offset)).isoformat(): None for offset in range((end - start).days)}
    return [(datetime.strptime(day, "%Y-%m-%d").date(), rate) for day, rate in sorted(nights.items())]


def _guest_name(payload: dict) -> str:
    guest = payload.get("guest") if isinstance(payload.get("guest"), dict) else {}
    parts = [str(guest.get("firstName") or "").strip(), str(guest.get("lastName") or "").strip()]
    name = " ".join([part for part in parts if part])
    if name:
        return name
    for room in payload.get("rooms") or []:
        room_name = str((room or {}).get("guestName") or "").strip()
        if room_name:
            return room_name
    return ""


def _guest_phone(payload: dict) -> str:
    guest = payload.get("guest") if isinstance(payload.get("guest"), dict) else {}
    return str(guest.get("phone") or "").strip()


def _room_type(cur, hotel_id, room):
    code = str(room.get("roomCode") or "").strip()
    if not code:
        raise Refused("Room code is required")
    cur.execute("SELECT room_type_id FROM hotel_room_code_mapping WHERE hotel_id = %s AND channel_room_code = %s", (hotel_id, code))
    found = cur.fetchone()
    if not found:
        raise Refused("Room code " + code + " is not mapped")
    return found["room_type_id"]


def _live_dates(cur, booking_id):
    cur.execute("SELECT DISTINCT stay_date FROM booking_room_night WHERE booking_id = %s AND night_status IN ('booked', 'checked_in') "
                "ORDER BY stay_date", (booking_id,))
    return [row["stay_date"].isoformat() for row in cur.fetchall()]


def _apply(cur, hotel, channel, reference, payload):
    hotel_id = hotel["hotel_id"]
    rooms = [room for room in payload.get("rooms") or [] if isinstance(room, dict)]
    if not rooms:
        raise Refused("Rooms are required")
    checkin, checkout = _stay_day(payload.get("checkin")), _stay_day(payload.get("checkout"))
    lines = [(_room_type(cur, hotel_id, room), _room_nights(room, checkin, checkout)) for room in rooms]
    if not all(nights for _type, nights in lines):
        raise Refused("Stay dates are required")
    existing = booking_by_ref(cur, hotel_id, channel, reference)
    before = _live_dates(cur, existing["booking_id"]) if existing else []
    guest = payload.get("guest") if isinstance(payload.get("guest"), dict) else {}
    amount = payload.get("amount") if isinstance(payload.get("amount"), dict) else {}
    currency = str(amount.get("currency") or "").strip().upper() or None
    priced = currency is not None
    notes = payload.get("specialRequests")
    saved = dict(payload)
    if isinstance(saved.get("creditCard"), dict):
        saved["creditCard"] = {"stored": "encrypted"}
    documents = {
        booking_docs: {
            "channel": channel, "cm_booking_id": str(payload.get("cmBookingId") or ""), "segment": str(payload.get("segment") or ""),
            "special_requests": "" if notes is None else str(notes), "pah": payload.get("pah"), "amount": amount,
            "reference_phone": _guest_phone(payload), "card_present": _open_card(payload), "checkin": checkin, "checkout": checkout,
            "rooms": [{name: room.get(name) for name in ("roomCode", "rateplanCode", "guestName", "occupancy", "prices")} for room in rooms],
        },
        channel_docs: {"channel_booking_ref": reference, "payload": saved},
    }
    written = write_channel_booking(
        cur, hotel_id, channel, reference,
        {"guest_name": _guest_name(payload), "phone": _guest_phone(payload), "email": guest.get("email")},
        lines, _count(rooms, "adults"), _count(rooms, "children"), currency,
        _money(amount.get("amountAfterTax")) if priced else None, _money(amount.get("commission")) if priced else None, None, documents,
    )
    cur.execute("SELECT DISTINCT t.room_type_name FROM booking_room_night n JOIN room_type t ON t.room_type_id = n.room_type_id "
                "WHERE n.booking_id = %s ORDER BY 1", (written["booking_id"],))
    room_types = [row["room_type_name"] for row in cur.fetchall()]
    return {"booking_id": written["booking_id"], "room_types": room_types,
            "stay_dates": sorted(set(before) | {day.isoformat() for _type, nights in lines for day, _rate in nights})}


def _cancel(cur, hotel, channel, reference, payload):
    existing = booking_by_ref(cur, hotel["hotel_id"], channel, reference)
    if not existing:
        raise Refused("Booking " + reference + " not found for " + channel)
    closed = cancel_in(cur, hotel["hotel_id"], None, existing["booking_id"], channel + " cancellation")
    return {"booking_id": existing["booking_id"], "room_types": [], "stay_dates": closed["stay_dates"]}


def handle_reservation(hotel: dict, payload: dict) -> dict:
    """Apply one delivery for the hotel row resolved from hotelCode. -> {'success', 'message', 'booking_id', 'room_types', 'stay_dates'}."""
    action = str(payload.get("action") or "").strip().lower()
    message = ACTION_MESSAGES.get(action)
    if not message:
        return {"success": False, "message": "Unknown action"}
    reference = str(payload.get("bookingId") or "").strip()
    channel = str(payload.get("channel") or "").strip().upper()
    if not reference or not channel:
        return {"success": False, "message": "bookingId and channel are required"}
    work = {"cancel": _cancel}.get(action, _apply)
    try:
        done = db.transaction(lambda cur: work(cur, hotel, channel, reference, payload))
    except Refused as reason:
        return {"success": False, "message": str(reason)}
    return dict(done, success=True, message=message)
