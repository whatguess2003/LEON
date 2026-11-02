"""Mobile check-in on mobile_checkin (one per booking: started, then verified) and digital_key (one active key per booking room,
valid until the hotel's check-out time (setting front_desk.checkout_time) on the morning after the room's last night). Each writer reads first; a refusal returns
{'error': reason}."""

import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from booking_system.guest_manager import Refused, run
from booking_system.hotel_registry import new_id


def _booking(cur, hotel_id, booking_id):
    cur.execute("SELECT booking_status FROM booking WHERE hotel_id = %s AND booking_id = %s", (hotel_id, booking_id))
    found = cur.fetchone()
    if not found:
        raise Refused("Booking " + str(booking_id or "") + " not found")
    return found


def start(hotel_id, user_id, booking_id):
    """READ the booking's mobile check-in; create it for a confirmed booking."""
    def write(cur):
        if _booking(cur, hotel_id, booking_id)["booking_status"] != "confirmed":
            raise Refused("Booking " + booking_id + " is not confirmed")
        cur.execute("SELECT checkin_id, status FROM mobile_checkin WHERE booking_id = %s", (booking_id,))
        found = cur.fetchone()
        if found:
            return {"checkin_id": found["checkin_id"], "status": found["status"], "result": "already started"}
        checkin_id = new_id("mci")
        cur.execute("INSERT INTO mobile_checkin (checkin_id, hotel_id, booking_id, status, started_by_user_id) VALUES (%s, %s, %s, 'started', %s)",
                    (checkin_id, hotel_id, booking_id, user_id))
        return {"checkin_id": checkin_id, "status": "started", "result": "created"}
    return run(write)


def verify(hotel_id, user_id, booking_id):
    """The desk confirmed the guest's identity: started becomes verified."""
    def write(cur):
        cur.execute("SELECT checkin_id, status FROM mobile_checkin WHERE hotel_id = %s AND booking_id = %s FOR UPDATE", (hotel_id, booking_id))
        found = cur.fetchone()
        if not found:
            raise Refused("Mobile check-in not started for " + str(booking_id or ""))
        if found["status"] == "verified":
            return {"checkin_id": found["checkin_id"], "status": "verified", "result": "already verified"}
        cur.execute("UPDATE mobile_checkin SET status = 'verified', verified_at = now(), verified_by_user_id = %s WHERE checkin_id = %s",
                    (user_id, found["checkin_id"]))
        return {"checkin_id": found["checkin_id"], "status": "verified", "result": "updated"}
    return run(write)


def issue_key(hotel_id, user_id, booking_id, room_number):
    """A key for a verified check-in's room that is checked in; an active key for the same room is returned, not repeated."""
    def write(cur):
        cur.execute("SELECT status FROM mobile_checkin WHERE hotel_id = %s AND booking_id = %s", (hotel_id, booking_id))
        found = cur.fetchone()
        if not found or found["status"] != "verified":
            raise Refused("Mobile check-in is not verified for " + str(booking_id or ""))
        cur.execute(
            """SELECT r.room_id, MAX(n.stay_date) AS last_night, h.timezone
               FROM booking_room_night n JOIN room r ON r.room_id = n.room_id JOIN hotel h ON h.hotel_id = n.hotel_id
               WHERE n.hotel_id = %s AND n.booking_id = %s AND r.room_number = %s AND n.night_status = 'checked_in'
               GROUP BY r.room_id, h.timezone""",
            (hotel_id, booking_id, str(room_number or "").strip().upper()))
        room = cur.fetchone()
        if not room:
            raise Refused("Room " + str(room_number or "") + " is not checked in on " + str(booking_id or ""))
        cur.execute("SELECT hotel_setting_required(%s, 'front_desk.checkout_time', %s) #>> '{}' AS checkout",
                    (hotel_id, room["last_night"] + timedelta(days=1)))
        room["standard_checkout_time"] = time.fromisoformat(cur.fetchone()["checkout"])
        cur.execute("SELECT key_id, valid_until FROM digital_key WHERE booking_id = %s AND room_id = %s AND status = 'active'", (booking_id, room["room_id"]))
        held = cur.fetchone()
        if held:
            return {"key_id": held["key_id"], "valid_until": held["valid_until"].isoformat(), "result": "already issued"}
        until = datetime.combine(room["last_night"] + timedelta(days=1), room["standard_checkout_time"], ZoneInfo(room["timezone"]))
        key_id = new_id("dkey")
        cur.execute(
            """INSERT INTO digital_key (key_id, hotel_id, booking_id, room_id, valid_from, valid_until, status, issued_by_user_id)
               VALUES (%s, %s, %s, %s, now(), %s, 'active', %s)""",
            (key_id, hotel_id, booking_id, room["room_id"], until, user_id))
        return {"key_id": key_id, "valid_until": until.isoformat(), "result": "created"}
    return run(write)


def revoke_key(hotel_id, user_id, key_id):
    def write(cur):
        cur.execute("SELECT status FROM digital_key WHERE hotel_id = %s AND key_id = %s FOR UPDATE", (hotel_id, key_id))
        found = cur.fetchone()
        if not found:
            raise Refused("Key " + str(key_id or "") + " not found")
        if found["status"] == "active":
            cur.execute("UPDATE digital_key SET status = 'revoked' WHERE key_id = %s", (key_id,))
        return {"key_id": key_id, "status": "revoked"}
    return run(write)


def status(hotel_id, booking_id):
    """The booking's mobile check-in and its keys."""
    def read(cur):
        _booking(cur, hotel_id, booking_id)
        cur.execute("SELECT checkin_id, status, started_at, verified_at FROM mobile_checkin WHERE booking_id = %s", (booking_id,))
        found = cur.fetchone()
        cur.execute(
            """SELECT k.key_id, r.room_number, k.valid_from, k.valid_until, k.status FROM digital_key k JOIN room r ON r.room_id = k.room_id
               WHERE k.booking_id = %s ORDER BY k.issued_at""", (booking_id,))
        keys = [{name: (value.isoformat() if hasattr(value, "isoformat") else value) for name, value in row.items()} for row in cur.fetchall()]
        checkin = {name: (value.isoformat() if hasattr(value, "isoformat") else value) for name, value in found.items()} if found else None
        return {"booking_id": booking_id, "mobile_checkin": checkin, "keys": keys}
    return run(read)


mobile_checkin_manager = sys.modules[__name__]
