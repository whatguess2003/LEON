"""Channel manager reservation list (channel_reservations, keyed by reservation_id; one row per hotel, direction and channel
reference). The bookings themselves are core bookings written by aiosell_reservation."""

from booking_system import booking_manager, db
from booking_system.hotel_registry import new_id, read_hotel_by_code

DIRECTIONS = ("reservation_in", "reservation_out")


def store_reservation(hotel_id, direction, channel_booking_ref, booking_id, source, action, guest_phone, guest_name, room_types,
                      check_in, check_out, booking_status, message):
    """READ by hotel, direction and channel reference; update the row when found, otherwise create it."""
    fields = (booking_id, source, action, guest_phone or None, guest_name or None, room_types or None, check_in, check_out,
              booking_status, message or None)

    def write(cur):
        cur.execute("SELECT reservation_id FROM channel_reservations WHERE hotel_id = %s AND direction = %s AND channel_booking_ref = %s",
                    (hotel_id, direction, channel_booking_ref))
        found = cur.fetchone()
        if found:
            cur.execute(
                """UPDATE channel_reservations SET booking_id = %s, source = %s, action = %s, guest_phone = %s, guest_name = %s,
                          room_types = %s, check_in = %s, check_out = %s, booking_status = %s, message = %s, updated_at = now()
                   WHERE reservation_id = %s""",
                fields + (found["reservation_id"],))
            return found["reservation_id"]
        reservation_id = new_id("chr")
        cur.execute(
            """INSERT INTO channel_reservations (reservation_id, hotel_id, direction, channel_booking_ref, booking_id, source, action,
                                                 guest_phone, guest_name, room_types, check_in, check_out, booking_status, message)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (reservation_id, hotel_id, direction, channel_booking_ref) + fields)
        return reservation_id
    return read_reservation(db.transaction(write))


def read_reservation(reservation_id):
    row = db.row("SELECT * FROM channel_reservations WHERE reservation_id = %s", (reservation_id,))
    if not row:
        return {}
    return dict(row, check_in=row["check_in"].isoformat() if row["check_in"] else "",
                check_out=row["check_out"].isoformat() if row["check_out"] else "", updated_at=row["updated_at"].isoformat())


def read_list(hotel_id, direction):
    """The reservations listed for this hotel in one direction, newest first."""
    rows = db.rows(
        """SELECT channel_booking_ref, booking_id, source, action, booking_status, message, updated_at, guest_name
           FROM channel_reservations WHERE hotel_id = %s AND direction = %s ORDER BY updated_at DESC, channel_booking_ref""",
        (hotel_id, direction))
    latest = max((row["updated_at"] for row in rows), default=None)
    return {
        "hotel_id": hotel_id, "direction": direction, "synced_at": latest.isoformat(timespec="seconds") if latest else "",
        "reservations": [{"bookingId": row["channel_booking_ref"], "booking_id": row["booking_id"] or "", "source": row["source"],
                          "action": row["action"], "success": row["booking_status"] != "rejected", "message": row["message"] or "",
                          "guest_name": row["guest_name"] or "", "direction": direction} for row in rows],
    }


def accept_pms_booking(body):
    """List one booking the PMS created as reservation_in. The booking must exist at that hotel."""
    hotel_id = str((body or {}).get("hotel_id") or "").strip()
    booking_id = str((body or {}).get("booking_id") or "").strip()
    if not hotel_id or not booking_id:
        return {"error": "hotel_id and booking_id are required"}
    if not db.row("SELECT 1 AS found FROM booking WHERE hotel_id = %s AND booking_id = %s", (hotel_id, booking_id)):
        return {"error": "Booking " + booking_id + " not found"}
    stay = booking_manager.stay_range(body.get("stay_dates") or [])
    if stay.get("error"):
        return stay
    stored = store_reservation(
        hotel_id, "reservation_in", booking_id, booking_id, "PMS", "book", str(body.get("guest_phone") or "").strip(),
        str(body.get("guest_name") or "").strip(), str(body.get("room_type") or "").strip(), stay["check_in"], stay["check_out"],
        "confirmed", "Reservation in")
    return {"success": True, "reservation": stored}


def record_aiosell_reservation(hotel_id, payload, outcome):
    """List one Aiosell delivery as reservation_out."""
    from booking_system.aiosell_reservation import _guest_name, _guest_phone, _stay_day

    ref = str(payload.get("bookingId") or "").strip()
    if not ref:
        return
    action = str(payload.get("action") or "").strip().lower() or "book"
    status = ("cancelled" if action == "cancel" else "confirmed") if outcome.get("success") else "rejected"
    store_reservation(
        hotel_id, "reservation_out", ref, outcome.get("booking_id"), "AIOSELL", action, _guest_phone(payload), _guest_name(payload),
        ", ".join(outcome.get("room_types") or []), _stay_day(payload.get("checkin")) or None, _stay_day(payload.get("checkout")) or None,
        status, str(outcome.get("message") or ""))


def accept_webhook(payload):
    """Apply one reservation Aiosell posts to the hotel its hotelCode is linked to, and list it. The caller answers Aiosell before
    any inventory push."""
    from booking_system.aiosell_reservation import handle_reservation

    body = payload if isinstance(payload, dict) else {}
    hotel = read_hotel_by_code(str(body.get("hotelCode") or "").strip())
    if not hotel:
        return {"success": False, "message": "Hotel code is not linked"}
    outcome = handle_reservation(hotel, body)
    record_aiosell_reservation(hotel["hotel_id"], body, outcome)
    return {"success": bool(outcome.get("success")), "message": str(outcome.get("message") or ""), "hotel_name": hotel["hotel_name"],
            "hotel_id": hotel["hotel_id"], "booking_id": outcome.get("booking_id"), "stay_dates": list(outcome.get("stay_dates") or [])}
