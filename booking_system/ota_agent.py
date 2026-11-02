"""Online booking for a channel or the hotel's booking page: search the room types sellable for a stay (every night on file,
enough rooms free, priced), then book one through booking_manager on that channel."""

import sys
from datetime import date

from booking_system import booking_manager, db
from booking_system.inventory_manager import availability


def search(hotel_id, check_in, check_out, rooms=1):
    """-> {'check_in', 'check_out', 'rooms', 'room_types': [{'room_type', 'nightly', 'total', 'currency', 'min_available'}]}."""
    try:
        start, end = date.fromisoformat(str(check_in)[:10]), date.fromisoformat(str(check_out)[:10])
    except ValueError:
        return {"error": "check_in and check_out must be YYYY-MM-DD"}
    nights = (end - start).days
    if nights <= 0 or int(rooms) <= 0:
        return {"error": "check_out must be after check_in and rooms must be at least 1"}
    last = date.fromordinal(end.toordinal() - 1)

    def read(cur):
        cur.execute(
            """SELECT t.room_type_id, t.room_type_name, h.currency, COUNT(p.stay_date) AS priced, SUM(p.base_price) AS total
               FROM room_type t JOIN hotel h ON h.hotel_id = t.hotel_id
               LEFT JOIN daily_pricing p ON p.room_type_id = t.room_type_id AND p.stay_date BETWEEN %s AND %s
               WHERE t.hotel_id = %s AND t.status = 'active' GROUP BY t.room_type_id, t.room_type_name, h.currency ORDER BY t.room_type_name""",
            (start, last, hotel_id))
        found = []
        for row in cur.fetchall():
            open_nights = availability(cur, hotel_id, row["room_type_id"], start, last)
            if row["priced"] != nights or len(open_nights) != nights:
                continue
            least = min(night["available"] for night in open_nights.values())
            if least >= int(rooms):
                total = float(row["total"])
                found.append({"room_type": row["room_type_name"], "nightly": round(total / nights, 2), "total": total * int(rooms),
                              "currency": row["currency"].strip(), "min_available": least})
        return found
    return {"check_in": start.isoformat(), "check_out": end.isoformat(), "rooms": int(rooms), "room_types": db.transaction(read)}


def book(hotel_id, user_id, channel, guest_name, guest_phone, room_type, check_in, check_out, rooms=1, adults=1, channel_booking_ref=None,
         email=None):
    """Create the booking on this channel (the guest is found by phone, then email, or created)."""
    if not str(channel or "").strip():
        return {"error": "channel is required"}
    guest = {"guest_name": guest_name, "phone": guest_phone, "email": email}
    return booking_manager.create_booking(hotel_id, user_id, guest, room_type, check_in, check_out, int(rooms), int(adults),
                                          channel=str(channel).strip().upper(), channel_booking_ref=channel_booking_ref or None)


ota_agent = sys.modules[__name__]
