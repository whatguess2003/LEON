"""Guest engagement on the guest tables: preferences (guest_preference), asked-for services and desk tasks (guest_request),
complaints (guest_complaint), and upgrade offers read from inventory and pricing. Each writer reads first; a refusal returns
{'error': reason}."""

import sys
from datetime import datetime

from booking_system import db
from booking_system.guest_manager import Refused, _guest_id_by_phone, add_requests, run
from booking_system.hotel_registry import new_id
from booking_system.inventory_manager import availability


def _guest_id(cur, hotel_id, guest_phone):
    guest_id = _guest_id_by_phone(cur, hotel_id, guest_phone)
    if not guest_id:
        raise Refused("Guest profile not found")
    return guest_id


def _own_booking(cur, hotel_id, guest_id, booking_id):
    if booking_id:
        cur.execute("SELECT 1 AS own FROM booking WHERE hotel_id = %s AND booking_id = %s AND guest_id = %s", (hotel_id, booking_id, guest_id))
        if not cur.fetchone():
            raise Refused("Booking " + str(booking_id) + " is not this guest's")


def preferences(hotel_id, guest_phone):
    def read(cur):
        cur.execute("SELECT preference, preference_value, recorded_at FROM guest_preference WHERE hotel_id = %s AND guest_id = %s ORDER BY preference",
                    (hotel_id, _guest_id(cur, hotel_id, guest_phone)))
        return {"preferences": [dict(row, recorded_at=row["recorded_at"].isoformat()) for row in cur.fetchall()]}
    return run(read)


def save_preference(hotel_id, user_id, guest_phone, preference, value):
    """READ the guest's preference; update its value or create it."""
    name = str(preference or "").strip().lower()
    text = str(value or "").strip()
    if not name or not text:
        return {"error": "preference and value are required"}

    def write(cur):
        guest_id = _guest_id(cur, hotel_id, guest_phone)
        cur.execute("SELECT preference_id FROM guest_preference WHERE guest_id = %s AND preference = %s FOR UPDATE", (guest_id, name))
        found = cur.fetchone()
        if found:
            cur.execute("UPDATE guest_preference SET preference_value = %s, recorded_at = now(), recorded_by_user_id = %s WHERE preference_id = %s",
                        (text[:255], user_id, found["preference_id"]))
            return {"preference_id": found["preference_id"], "result": "updated"}
        preference_id = new_id("gpf")
        cur.execute(
            """INSERT INTO guest_preference (preference_id, hotel_id, guest_id, preference, preference_value, recorded_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (preference_id, hotel_id, guest_id, name, text[:255], user_id))
        return {"preference_id": preference_id, "result": "created"}
    return run(write)


def request_service(hotel_id, user_id, guest_phone, request, requested_for=None, booking_id=None):
    """Open a guest request (a service or a desk task). An identical open request is not repeated."""
    text = str(request or "").strip()
    if not text:
        return {"error": "request is required"}
    try:
        moment = datetime.fromisoformat(str(requested_for)) if requested_for else None
    except ValueError:
        return {"error": "requested_for must be an ISO date-time"}

    def check(cur):
        guest_id = _guest_id(cur, hotel_id, guest_phone)
        _own_booking(cur, hotel_id, guest_id, booking_id)
        return {"guest_id": guest_id}
    found = run(check)
    if found.get("error"):
        return found
    made = add_requests(hotel_id, user_id, found["guest_id"], [text], booking_id or None, moment)
    return made if isinstance(made, dict) else {"request_ids": made, "result": "created" if made else "already open"}


def record_complaint(hotel_id, user_id, guest_phone, complaint, booking_id=None):
    text = str(complaint or "").strip()
    if not text:
        return {"error": "complaint is required"}

    def write(cur):
        guest_id = _guest_id(cur, hotel_id, guest_phone)
        _own_booking(cur, hotel_id, guest_id, booking_id)
        complaint_id = new_id("gcp")
        cur.execute(
            """INSERT INTO guest_complaint (complaint_id, hotel_id, guest_id, booking_id, complaint, recorded_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (complaint_id, hotel_id, guest_id, booking_id or None, text[:1000], user_id))
        return {"complaint_id": complaint_id}
    return run(write)


def upgrade_offers(hotel_id, booking_id):
    """Room types priced above the booking's type on every night of its stay, with rooms free on each night for the whole booking.
    -> {'booking_id', 'room_type', 'offers': [{'room_type', 'extra_total'}]} (extra_total: price difference over the stay per room)."""
    def read(cur):
        cur.execute(
            """SELECT n.room_type_id, MAX(n.room_seq) AS rooms, MIN(n.stay_date) AS first_night, MAX(n.stay_date) AS last_night,
                      SUM(p.base_price) / MAX(n.room_seq) AS stay_price
               FROM booking_room_night n JOIN booking b ON b.booking_id = n.booking_id
               LEFT JOIN daily_pricing p ON p.room_type_id = n.room_type_id AND p.stay_date = n.stay_date
               WHERE b.hotel_id = %s AND b.booking_id = %s AND b.booking_status = 'confirmed' AND n.night_status = 'booked'
               GROUP BY n.room_type_id""",
            (hotel_id, booking_id))
        stays = cur.fetchall()
        if len(stays) != 1:
            raise Refused("Booking " + str(booking_id) + " is not one confirmed room type")
        stay = stays[0]
        nights = (stay["last_night"] - stay["first_night"]).days + 1
        cur.execute(
            """SELECT t.room_type_id, t.room_type_name, COUNT(p.stay_date) AS priced, SUM(p.base_price) AS stay_price FROM room_type t
               LEFT JOIN daily_pricing p ON p.room_type_id = t.room_type_id AND p.stay_date BETWEEN %s AND %s
               WHERE t.hotel_id = %s AND t.status = 'active' AND t.room_type_id <> %s GROUP BY t.room_type_id, t.room_type_name""",
            (stay["first_night"], stay["last_night"], hotel_id, stay["room_type_id"]))
        offers = []
        for row in cur.fetchall():
            if row["priced"] != nights or stay["stay_price"] is None or row["stay_price"] <= stay["stay_price"]:
                continue
            open_nights = availability(cur, hotel_id, row["room_type_id"], stay["first_night"], stay["last_night"])
            if len(open_nights) == nights and all(night["available"] >= stay["rooms"] for night in open_nights.values()):
                offers.append({"room_type": row["room_type_name"], "extra_total": float(row["stay_price"] - stay["stay_price"])})
        cur.execute("SELECT room_type_name FROM room_type WHERE room_type_id = %s", (stay["room_type_id"],))
        return {"booking_id": booking_id, "room_type": cur.fetchone()["room_type_name"],
                "offers": sorted(offers, key=lambda offer: offer["extra_total"])}
    return run(read)


def open_requests(hotel_id):
    """Open requests of the hotel with the guest's name."""
    rows = db.rows(
        """SELECT r.request_id, r.request, r.requested_for, r.booking_id, g.guest_name, r.recorded_at FROM guest_request r
           JOIN guest g ON g.guest_id = r.guest_id WHERE r.hotel_id = %s AND r.status = 'open' ORDER BY r.recorded_at""",
        (hotel_id,))
    return [dict(row, recorded_at=row["recorded_at"].isoformat(),
                 requested_for=row["requested_for"].isoformat() if row["requested_for"] else None) for row in rows]


guest_engagement_manager = sys.modules[__name__]
