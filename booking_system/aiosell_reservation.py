"""Inbound Aiosell reservation workflow.

One endpoint receives book, modify, and cancel. The payload is the full new state.
Guest fields may be missing. Special requests stay plain text. Card values stay encrypted.
"""

import logging
import os
from datetime import datetime, timedelta

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MANAGER_NAME = "AIOSSELL"

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


def _nights(checkin: str, checkout: str):
    start = datetime.strptime(checkin, "%Y-%m-%d")
    end = datetime.strptime(checkout, "%Y-%m-%d")
    days = []
    cursor = start
    while cursor < end:
        days.append(cursor.strftime("%Y-%m-%d"))
        cursor += timedelta(days=1)
    return days


def _room_nights(room: dict, checkin: str, checkout: str):
    dates = []
    prices = room.get("prices")
    entries = prices if isinstance(prices, list) else []
    for price in entries:
        if isinstance(price, dict):
            day = _stay_day(price.get("date"))
        else:
            day = _stay_day(price)
        if day and day not in dates:
            dates.append(day)
    if dates:
        return dates
    start = _stay_day(checkin)
    end = _stay_day(checkout)
    if start and end:
        return _nights(start, end)
    return []


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


def handle_reservation(payload: dict, push: bool = True) -> dict:
    action = str((payload or {}).get("action") or "").strip().lower()
    message = ACTION_MESSAGES.get(action)
    if not message:
        return {"success": False, "message": "Unknown action"}
    booking_id = str((payload or {}).get("bookingId") or "").strip()
    hotel_code = str((payload or {}).get("hotelCode") or "").strip()
    if not booking_id or not hotel_code:
        return {"success": False, "message": "bookingId and hotelCode are required"}
    if action == "cancel":
        return _cancel(hotel_code, booking_id, message, push)
    return _apply(payload, hotel_code, booking_id, message, push)


def _hotel_name(db, hotel_code: str):
    names = []
    for doc in db["ota_link"].find({"hotel_code": hotel_code}, {"_id": 0, "hotel_name": 1}):
        hotel = str((doc or {}).get("hotel_name") or "").strip().upper()
        if hotel and hotel not in names:
            names.append(hotel)
    if len(names) == 1:
        return names[0], ""
    if not names:
        return "", "Hotel code is not linked"
    return "", "Hotel code matches more than one hotel"


def _room_type(cur, hotel_code: str, hotel_name: str, room_code: str):
    cur.execute(
        """
        SELECT room_type FROM hotel_room_code_mapping
        WHERE hotel_code = %s AND room_code = %s AND hotel_name = %s
        """,
        (hotel_code, room_code, hotel_name),
    )
    row = cur.fetchone()
    if not row:
        return ""
    return str(row[0] or "")


def _ensure_mapping_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS hotel_room_code_mapping (
            hotel_code VARCHAR(64),
            room_code VARCHAR(64),
            hotel_name VARCHAR(255),
            room_type VARCHAR(255),
            PRIMARY KEY (hotel_code, room_code)
        )
        """
    )


def _apply(payload: dict, hotel_code: str, booking_id: str, message: str, push: bool = True) -> dict:
    from booking_system.booking_manager import booking_manager

    booking_manager._ensure_postgres_connection()
    db = booking_manager.mongo_db
    if db is None or booking_manager.postgres_conn is None:
        return {"success": False, "message": "Database connection not available"}
    hotel_name, hotel_error = _hotel_name(db, hotel_code)
    if hotel_error:
        return {"success": False, "message": hotel_error}
    rooms = payload.get("rooms") if isinstance(payload.get("rooms"), list) else []
    if not rooms:
        return {"success": False, "message": "Rooms are required"}
    checkin = _stay_day(payload.get("checkin"))
    checkout = _stay_day(payload.get("checkout"))
    card_present = _open_card(payload)
    with booking_manager.postgres_lock:
        return _apply_locked(booking_manager, payload, hotel_code, booking_id, message, push, hotel_name, db, rooms, checkin, checkout, card_present)


def _apply_locked(booking_manager, payload, hotel_code, booking_id, message, push, hotel_name, db, rooms, checkin, checkout, card_present) -> dict:
    booking_manager._clear_transaction()
    conn = booking_manager.postgres_conn
    cur = conn.cursor()
    try:
        _ensure_mapping_table(cur)
        lines = []
        refused = ""
        for room in rooms:
            room_code = str((room or {}).get("roomCode") or "").strip()
            if not room_code:
                refused = "Room code is required"
                break
            room_type = _room_type(cur, hotel_code, hotel_name, room_code)
            if not room_type:
                refused = "Room code " + room_code + " is not mapped"
                break
            nights = _room_nights(room, checkin, checkout)
            if not nights:
                refused = "Stay dates are required"
                break
            lines.append({"room_type": room_type, "nights": nights, "rateplan": str((room or {}).get("rateplanCode") or "")})
        if refused:
            conn.rollback()
            return {"success": False, "message": refused}
        cur.execute("SELECT hotel_name, guest_phone FROM room_bookings WHERE booking_id = %s", (booking_id,))
        existing = cur.fetchone()
        if existing and str(existing[0] or "") != hotel_name:
            conn.rollback()
            return {"success": False, "message": "Booking belongs to another hotel"}
        reference_phone = _guest_phone(payload)
        phone = ""
        phone_verified = False
        if existing:
            current_phone = str(existing[1] or "").strip()
            if current_phone and db["guests"].find_one({"guest_phone": current_phone, "hotel_name": hotel_name}):
                phone = current_phone
                phone_verified = True
        cur.execute(
            """
            UPDATE room_number_assignments
            SET booking_id = NULL, guest_phone = NULL, assignment_status = 'available', updated_by = %s
            WHERE hotel_name = %s AND booking_id = %s
            """,
            (MANAGER_NAME, hotel_name, booking_id),
        )
        cur.execute("DELETE FROM room_stay WHERE hotel_name = %s AND booking_id = %s", (hotel_name, booking_id))
        cur.execute("SELECT stay_date FROM booking_stays WHERE hotel_name = %s AND booking_id = %s", (hotel_name, booking_id))
        previous_dates = [row[0] for row in cur.fetchall()]
        cur.execute("DELETE FROM booking_stays WHERE hotel_name = %s AND booking_id = %s", (hotel_name, booking_id))
        per_date = {}
        stay_rows = []
        type_counts = {}
        all_dates = []
        for line in lines:
            type_counts[line["room_type"]] = type_counts.get(line["room_type"], 0) + 1
            for stay_date in line["nights"]:
                per_date[stay_date] = per_date.get(stay_date, 0) + 1
                stay_rows.append((stay_date, per_date[stay_date], line["room_type"]))
                if stay_date not in all_dates:
                    all_dates.append(stay_date)
        header_type = lines[0]["room_type"]
        channel = str(payload.get("channel") or "").strip().upper()
        reference = str(payload.get("cmBookingId") or booking_id)
        notes = payload.get("specialRequests")
        notes_text = "" if notes is None else str(notes)
        amount = payload.get("amount") if isinstance(payload.get("amount"), dict) else {}
        total = _money(amount.get("amountAfterTax"))
        if existing:
            cur.execute(
                """
                UPDATE room_bookings
                SET guest_phone = %s, hotel_name = %s, room_type = %s, rooms_booked = %s,
                    booking_status = 'confirmed', booking_reference = %s, nights = %s,
                    channel = %s, booking_notes = %s, check_in_date = %s,
                    updated_at = NOW(), updated_by = %s
                WHERE booking_id = %s
                """,
                (phone, hotel_name, header_type, len(lines), reference, len(all_dates), channel, notes_text, checkin or None, MANAGER_NAME, booking_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO room_bookings
                (booking_id, guest_phone, hotel_name, room_type, rooms_booked, booking_status, booking_reference, created_at, nights, channel, booking_notes, check_in_date)
                VALUES (%s, %s, %s, %s, %s, 'confirmed', %s, CURRENT_TIMESTAMP, %s, %s, %s, %s)
                """,
                (booking_id, phone, hotel_name, header_type, len(lines), reference, len(all_dates), channel, notes_text, checkin or None),
            )
        for stay_date, count in per_date.items():
            room_types_that_night = []
            for stay, index, room_type in stay_rows:
                if stay == stay_date and room_type not in room_types_that_night:
                    room_types_that_night.append(room_type)
            for room_type in room_types_that_night:
                booked = len([row for row in stay_rows if row[0] == stay_date and row[2] == room_type])
                cur.execute(
                    """
                    INSERT INTO booking_stays (hotel_name, room_type, stay_date, booking_id, rooms_booked, booking_status)
                    VALUES (%s, %s, %s, %s, %s, 'confirmed')
                    """,
                    (hotel_name, room_type, stay_date, booking_id, booked),
                )
        for stay_date, index, room_type in stay_rows:
            cur.execute(
                """
                INSERT INTO room_stay (booking_id, stay_date, stay_index, hotel_name, room_number, stay_status, assigned_by, assignment_id)
                VALUES (%s, %s, %s, %s, NULL, 'pending', %s, %s)
                """,
                (booking_id, stay_date, index, hotel_name, MANAGER_NAME, booking_id + "#" + str(index)),
            )
        single_type = len(type_counts) == 1
        if single_type:
            booking_manager._assign_open_rooms(cur, hotel_name, header_type, booking_id, phone, MANAGER_NAME, all_dates, len(lines))
        paid = 0.0
        total_value = total
        remaining = None if total_value is None else total_value - paid
        cur.execute("SELECT booking_id FROM payment_tracking WHERE booking_id = %s", (booking_id,))
        payment = cur.fetchone()
        if payment:
            cur.execute(
                """
                UPDATE payment_tracking
                SET guest_phone_number = %s, total_amount = %s, amount_paid = %s, remaining_balance = %s, payment_status = 'pending'
                WHERE booking_id = %s
                """,
                (phone, total_value, paid, remaining, booking_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO payment_tracking
                (booking_id, guest_phone_number, total_amount, amount_paid, remaining_balance, payment_status, payment_method, payment_date)
                VALUES (%s, %s, %s, %s, %s, 'pending', NULL, NULL)
                """,
                (booking_id, phone, total_value, paid, remaining),
            )
        stored_rooms = []
        for room in rooms:
            stored_rooms.append({
                "roomCode": (room or {}).get("roomCode"),
                "rateplanCode": (room or {}).get("rateplanCode"),
                "guestName": (room or {}).get("guestName"),
                "occupancy": (room or {}).get("occupancy"),
                "prices": (room or {}).get("prices"),
            })
        content = {
            "hotel_name": hotel_name,
            "booking_id": booking_id,
            "guest_phone": phone,
            "reference_phone": reference_phone,
            "phone_verified": phone_verified,
            "guest_name": _guest_name(payload),
            "channel": channel,
            "cm_booking_id": str(payload.get("cmBookingId") or ""),
            "segment": str(payload.get("segment") or ""),
            "special_requests": notes_text,
            "pah": payload.get("pah"),
            "amount": amount,
            "rooms": stored_rooms,
            "card_present": card_present,
            "checkin": checkin,
            "checkout": checkout,
        }
        bookings = db["guest_bookings"]
        current_doc = bookings.find_one({"booking_id": booking_id, "hotel_name": hotel_name})
        if current_doc:
            bookings.update_one({"booking_id": booking_id, "hotel_name": hotel_name}, {"$set": content})
        else:
            bookings.insert_one(content)
        reservations = db["ota_reservations"]
        saved = dict(payload)
        if isinstance(saved.get("creditCard"), dict):
            saved["creditCard"] = {"stored": "encrypted"}
        record = {"hotel_name": hotel_name, "booking_id": booking_id, "payload": saved}
        current_res = reservations.find_one({"hotel_name": hotel_name, "booking_id": booking_id})
        if current_res:
            reservations.update_one({"hotel_name": hotel_name, "booking_id": booking_id}, {"$set": record})
        else:
            reservations.insert_one(record)
        conn.commit()
        pushed_dates = [str(day)[:10] for day in previous_dates] + list(all_dates)
        outcome = {"success": True, "message": message}
    except Exception as exc:
        conn.rollback()
        pushed_dates = None
        outcome = {"success": False, "message": str(exc)}
    finally:
        cur.close()
    if pushed_dates is not None:
        outcome["stay_dates"] = pushed_dates
        if push:
            booking_manager._safe_rollback()
            _push_stay_dates(hotel_name, pushed_dates)
    return outcome


def _cancel(hotel_code: str, booking_id: str, message: str, push: bool = True) -> dict:
    from booking_system.booking_manager import booking_manager

    booking_manager._ensure_postgres_connection()
    db = booking_manager.mongo_db
    if db is None or booking_manager.postgres_conn is None:
        return {"success": False, "message": "Database connection not available"}
    hotel_name, hotel_error = _hotel_name(db, hotel_code)
    if hotel_error:
        return {"success": False, "message": hotel_error}
    with booking_manager.postgres_lock:
        booking_manager._clear_transaction()
        cur = booking_manager.postgres_conn.cursor()
        cur.execute("SELECT stay_date FROM booking_stays WHERE hotel_name = %s AND booking_id = %s", (hotel_name, booking_id))
        previous_dates = [str(row[0])[:10] for row in cur.fetchall()]
        cur.close()
        result = booking_manager.cancel_booking(hotel_name, booking_id, MANAGER_NAME)
    if str(result).startswith("❌"):
        return {"success": False, "message": result}
    db["guest_bookings"].update_one(
        {"hotel_name": hotel_name, "booking_id": booking_id},
        {"$set": {"booking_status": "cancelled"}},
    )
    if push:
        _push_stay_dates(hotel_name, previous_dates)
    return {"success": True, "message": message, "stay_dates": previous_dates}


def _push_stay_dates(hotel_name: str, stay_dates):
    from booking_system.booking_manager import booking_manager
    try:
        booking_manager._safe_rollback()
        from aiosell_channel_manager import push_dates
        pushed = push_dates(hotel_name, stay_dates)
        if pushed.get("error") or pushed.get("skipped"):
            logging.info("Aiosell push %s", pushed.get("error") or pushed.get("skipped"))
    except Exception:
        booking_manager._safe_rollback()
        logging.exception("Aiosell push failed")
