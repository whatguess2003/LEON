"""Front desk on booking_room_night: room assignment, check-in, check-out, readiness reads, the assignment calendar and nightly
pricing. Functions take the hotel and the actor explicitly; 'today' is the hotel date in the session timezone (zone)."""

import json
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg2

from booking_system import core_refs, db
from booking_system.hotel_registry import new_id

# Longest country calling code first. WhatsApp matches the code, then the national number.
_WHATSAPP_DIAL_CODES = (
    "376", "971", "355", "374", "244", "297", "994", "387", "880", "226", "359", "973", "257", "229", "673", "591",
    "975", "267", "375", "501", "243", "236", "242", "225", "237", "506", "238", "599", "357", "420", "253", "213",
    "593", "372", "251", "358", "679", "241", "995", "233", "350", "220", "224", "240", "502", "592", "852", "504",
    "385", "509", "353", "972", "964", "354", "962", "254", "996", "855", "686", "269", "850", "965", "856", "961",
    "423", "231", "266", "370", "352", "371", "218", "212", "377", "373", "382", "261", "692", "389", "223", "976",
    "853", "222", "356", "230", "960", "265", "258", "264", "227", "234", "505", "977", "674", "968", "507", "689",
    "675", "351", "595", "974", "381", "250", "966", "248", "249", "386", "421", "232", "221", "252", "597", "503",
    "992", "670", "993", "216", "886", "255", "380", "256", "598", "998", "379", "678", "685", "967", "260", "263",
    "93", "54", "43", "61", "32", "55", "41", "56", "86", "57", "53", "49", "45", "20", "34", "33", "44", "30",
    "36", "62", "91", "98", "39", "81", "82", "94", "95", "52", "60", "31", "47", "64", "51", "63", "92", "48",
    "40", "46", "65", "66", "90", "58", "84", "27", "1", "7",
)

LIVE = ("booked", "checked_in")
# front_desk.checkin_room_readiness -> the readiness statuses a room may have when a guest is put in it
RELEASE = {"clean_or_inspected": ("clean", "inspected"), "inspected_only": ("inspected",)}
READINESS = ("clean", "dirty", "cleaning", "inspected")
STAY_STATUS = {"booked": "assigned", "checked_in": "checked_in", "checked_out": "checked_out"}
PROFILE_FIELDS = ("date_of_birth", "gender", "company", "address", "identity_type", "identity_number", "special_request")
CENT = Decimal("0.01")
BAD_DATE = "Invalid date format. Use YYYY-MM-DD"

_NIGHTS = """
    SELECT n.booking_night_id, n.booking_id, n.stay_date, n.room_seq, n.room_type_id, t.room_type_name, n.room_id, r.room_number,
           n.night_status, n.sell_rate, n.currency, n.rate_source, n.assigned_at, n.checked_in_at, n.checked_out_at
    FROM booking_room_night n JOIN room_type t ON t.room_type_id = n.room_type_id LEFT JOIN room r ON r.room_id = n.room_id
    WHERE n.hotel_id = %s AND n.booking_id = %s ORDER BY n.stay_date, n.room_seq"""


def _identity(guest, kind, alias):
    """A lateral join to one identity value of a guest (several phones: the lowest value)."""
    return (" LEFT JOIN LATERAL (SELECT identity_id, identity_value FROM guest_identity i WHERE i.guest_id = " + guest
            + ".guest_id AND i.identity_type = '" + kind + "' ORDER BY i.identity_value LIMIT 1) " + alias + " ON TRUE")


class Refused(Exception):
    """A rule refused the change; the transaction rolls back and the reason is returned."""


def _run(fn):
    try:
        return db.transaction(fn)
    except Refused as refusal:
        return {"error": str(refusal)}
    except psycopg2.errors.UniqueViolation as clash:
        return {"error": "Another change took this first: " + str(clash.diag.message_primary)}
    except psycopg2.errors.InvalidParameterValue as unset:
        return {"error": str(unset.diag.message_primary)}


def _release(cur, hotel_id, day):
    """The readiness statuses a room needs to take a guest on day, per the hotel's front_desk.checkin_room_readiness (refused while unset)."""
    cur.execute("SELECT hotel_setting_required(%s, 'front_desk.checkin_room_readiness', %s) #>> '{}' AS rule", (hotel_id, day))
    return RELEASE[cur.fetchone()["rule"]]


def _day(text):
    try:
        return date.fromisoformat(str(text or "")[:10])
    except ValueError:
        return None


def _today(zone):
    return datetime.now(ZoneInfo(zone)).date()


def _iso(value):
    return value.isoformat() if value is not None else None


def _money(value):
    return float(value) if value is not None else None


def _booking(cur, hotel_id, booking_id):
    found = core_refs.booking(cur, hotel_id, booking_id)
    if not found:
        raise Refused("Booking " + str(booking_id) + " not found")
    return found


def _nights(cur, hotel_id, booking_id):
    cur.execute(_NIGHTS, (hotel_id, booking_id))
    return cur.fetchall()


def _log(cur, hotel_id, user_id, booking_id, field, old, new, reason):
    cur.execute(
        """INSERT INTO booking_modification_log (modification_id, hotel_id, booking_id, modified_by_user_id, field_changed, old_value, new_value, change_reason)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (new_id("bkm"), hotel_id, booking_id, user_id, field, old, new, reason),
    )


def _busy(cur, room_id, dates, own_ids):
    """Dates on which the room holds another live night or a room block."""
    cur.execute(
        """SELECT stay_date FROM booking_room_night
           WHERE room_id = %s AND stay_date = ANY(%s) AND night_status IN ('booked','checked_in') AND NOT booking_night_id = ANY(%s)
           UNION SELECT d::date FROM unnest(%s::date[]) d JOIN room_block b ON b.room_id = %s AND d BETWEEN b.start_date AND b.end_date
           ORDER BY 1""",
        (room_id, list(dates), list(own_ids), list(dates), room_id),
    )
    return [row["stay_date"].isoformat() for row in cur.fetchall()]


def _readiness(cur, room_id):
    cur.execute("SELECT readiness_status FROM room_readiness_status WHERE room_id = %s", (room_id,))
    found = cur.fetchone()
    return found["readiness_status"] if found else None


def _set_readiness(cur, user_id, room_id, room_number, status):
    cur.execute(
        """UPDATE room_readiness_status SET readiness_status = %s, changed_at = now(), changed_by_user_id = %s,
               clean_since = CASE WHEN %s IN ('clean','inspected')
                                  THEN CASE WHEN readiness_status IN ('clean','inspected') THEN clean_since ELSE now() END END
           WHERE room_id = %s""",
        (status, user_id, status, room_id),
    )
    if not cur.rowcount:
        raise Refused("Room " + room_number + " has no readiness status")


def _settle(cur, hotel_id, user_id, booking_id):
    """booking_status follows its nights: any in house -> checked_in, else any booked -> confirmed, else checked_out."""
    cur.execute("SELECT booking_status FROM booking WHERE booking_id = %s", (booking_id,))
    current = cur.fetchone()["booking_status"]
    if current not in ("confirmed", "checked_in", "checked_out"):
            return
    cur.execute(
        """SELECT CASE WHEN bool_or(night_status = 'checked_in') THEN 'checked_in' WHEN bool_or(night_status = 'booked') THEN 'confirmed'
                       ELSE 'checked_out' END AS status
           FROM booking_room_night WHERE booking_id = %s AND night_status IN ('booked','checked_in','checked_out') HAVING COUNT(*) > 0""",
        (booking_id,),
    )
    found = cur.fetchone()
    if not found or found["status"] == current:
        return
    cur.execute("UPDATE booking SET booking_status = %s, updated_at = now(), updated_by_user_id = %s WHERE booking_id = %s",
                (found["status"], user_id, booking_id))
    _log(cur, hotel_id, user_id, booking_id, "booking_status", current, found["status"], "front desk")


def _slots(nights):
    """{room_seq: [live nights]}."""
    slots = {}
    for night in nights:
        if night["night_status"] in LIVE:
            slots.setdefault(night["room_seq"], []).append(night)
    return slots


def _place(cur, hotel_id, user_id, booking_id, nights, room_number, change_type):
    """Put the nights in one room. The room keeps the nights' room type unless change_type. Returns assigned_at."""
    label = str(room_number or "").strip().upper()
    room = core_refs.room(cur, hotel_id, label)
    if not room:
        raise Refused("Room " + label + " not found")
    if room["status"] != "active":
        raise Refused("Room " + label + " is " + room["status"])
    other = sorted({night["room_type_name"] for night in nights if night["room_type_id"] != room["room_type_id"]})
    if other and not change_type:
        raise Refused("Room " + label + " is " + room["room_type_name"] + "; the booked room type is " + ", ".join(other))
    ids = [night["booking_night_id"] for night in nights]
    busy = _busy(cur, room["room_id"], [night["stay_date"] for night in nights], ids)
    if busy:
        raise Refused("Room " + label + " unavailable on: " + ", ".join(busy))
    if any(night["night_status"] == "checked_in" for night in nights):
        allowed = _release(cur, hotel_id, min(night["stay_date"] for night in nights))
        if _readiness(cur, room["room_id"]) not in allowed:
            raise Refused("Room " + label + " must be " + " or ".join(allowed) + " for an in-house guest (hotel setting front_desk.checkin_room_readiness)")
    cur.execute(
        """UPDATE booking_room_night SET room_id = %s, room_type_id = %s, assigned_at = now(), assigned_by_user_id = %s
           WHERE booking_night_id = ANY(%s) RETURNING assigned_at""",
        (room["room_id"], room["room_type_id"], user_id, ids),
    )
    assigned_at = cur.fetchone()["assigned_at"]
    before = ", ".join(sorted({night["room_number"] for night in nights if night["room_number"]})) or None
    _log(cur, hotel_id, user_id, booking_id, "room", before, label,
         "room " + str(nights[0]["room_seq"]) + ", " + nights[0]["stay_date"].isoformat() + " to " + nights[-1]["stay_date"].isoformat())
    return assigned_at


def _free_room(cur, hotel_id, nights, taken):
    """The first room number free on every night (inventory_manager.free_rooms), not already taken."""
    from booking_system import inventory_manager
    common = None
    for night in nights:
        numbers = {row["room_number"] for row in inventory_manager.free_rooms(cur, hotel_id, night["room_type_id"], night["stay_date"])}
        common = numbers if common is None else common & numbers
    left = sorted((common or set()) - set(taken))
    if not left:
        raise Refused("No " + nights[0]["room_type_name"] + " room is free for every night of room " + str(nights[0]["room_seq"]))
    return left[0]


def _open_nights(cur, hotel_id, condition, params):
    """Live nights of confirmed or in-house bookings matching condition, with guest, room and whether a balance is owed."""
    cur.execute(
        """SELECT n.booking_night_id, n.booking_id, n.stay_date, n.night_status, n.room_id, r.room_number, g.guest_name,
                  ph.identity_value AS guest_phone, (SELECT MAX(x.room_seq) FROM booking_room_night x WHERE x.booking_id = n.booking_id) AS rooms_booked,
                  COALESCE(bal.balance_due > 0, FALSE) AS owed
           FROM booking_room_night n JOIN booking b ON b.booking_id = n.booking_id JOIN guest g ON g.guest_id = b.guest_id
           JOIN booking_balance bal ON bal.booking_id = n.booking_id LEFT JOIN room r ON r.room_id = n.room_id""" + _identity("g", "phone", "ph") + """
           WHERE n.hotel_id = %s AND n.night_status IN ('booked','checked_in') AND b.booking_status IN ('confirmed','checked_in') AND """
        + condition + " ORDER BY n.stay_date, n.booking_id, n.room_seq",
        (hotel_id,) + tuple(params),
    )
    return cur.fetchall()


def _check_out_nights(cur, hotel_id, user_id, nights):
    """Bulk check-out of assigned nights on bookings with nothing owed. A night never checked in takes the check-out time."""
    done = [night for night in nights if night["room_id"] and not night["owed"]]
    kept = {night["booking_night_id"] for night in done}
    cur.execute(
        """UPDATE booking_room_night SET night_status = 'checked_out', checked_in_at = COALESCE(checked_in_at, now()), checked_out_at = now()
           WHERE booking_night_id = ANY(%s)""",
        (list(kept),),
    )
    for booking_id in sorted({night["booking_id"] for night in done}):
        _settle(cur, hotel_id, user_id, booking_id)
    skipped = sorted({night["booking_id"] for night in nights if night["booking_night_id"] not in kept})
    return {"result": "success", "rows_updated": len(kept), "skipped": skipped}


class FrontDesk:
    def __init__(self):
        from booking_system.content_store import client
        self.records = client["leon_hotel_memory"]

    def content_db(self):
        return self.records

    # ---------- rooms and readiness ----------

    def read_available_room_numbers_internal(self, hotel_id: str, room_type: str, stay_date: str) -> dict:
        """READ room numbers of a type free on one night."""
        day = _day(stay_date)
        if not day:
            return {"error": BAD_DATE}

        def run(cur):
            from booking_system import inventory_manager
            type_id = core_refs.room_type_id(cur, hotel_id, room_type)
            if not type_id:
                raise Refused("Room type " + str(room_type) + " not found")
            rooms = inventory_manager.free_rooms(cur, hotel_id, type_id, day)
            return {"room_type": str(room_type).strip().upper(), "stay_date": day.isoformat(),
                    "available_room_numbers": sorted(row["room_number"] for row in rooms)}
        return _run(run)

    def read_room_readiness_status_json(self, hotel_id: str, room_number: str) -> dict:
        """READ one room's readiness."""
        found = db.row(
            """SELECT r.room_number, s.readiness_status, s.changed_at, u.user_name, s.clean_since, s.auto_dirty_days
               FROM room r LEFT JOIN room_readiness_status s ON s.room_id = r.room_id LEFT JOIN user_account u ON u.user_id = s.changed_by_user_id
               WHERE r.hotel_id = %s AND r.room_number = %s""",
            (hotel_id, str(room_number or "").strip().upper()),
        )
        if not found:
            return {"error": "Room " + str(room_number) + " not found"}
        return {"room_number": found["room_number"], "readiness_status": found["readiness_status"], "status_changed_at": _iso(found["changed_at"]),
                "changed_by": found["user_name"], "clean_since": _iso(found["clean_since"]), "auto_dirty_threshold_days": found["auto_dirty_days"]}

    read_room_readiness_status = read_room_readiness_status_json

    def read_all_room_readiness_status_internal(self, hotel_id: str) -> dict:
        """READ every room's readiness in one call."""
        rows = db.rows(
            """SELECT r.room_number, s.readiness_status, s.changed_at, u.user_name
               FROM room_readiness_status s JOIN room r ON r.room_id = s.room_id LEFT JOIN user_account u ON u.user_id = s.changed_by_user_id
               WHERE s.hotel_id = %s ORDER BY r.room_number""",
            (hotel_id,),
        )
        return {"items": [{"room_number": row["room_number"], "readiness_status": row["readiness_status"],
                           "status_changed_at": _iso(row["changed_at"]), "changed_by": row["user_name"]} for row in rows]}

    def update_room_readiness_status_internal(self, hotel_id: str, user_id: str, room_number: str, new_status: str) -> dict:
        """READ the room's readiness, then UPDATE it (clean, dirty, cleaning, inspected)."""
        status = str(new_status or "").strip().lower()
        if status not in READINESS:
            return {"error": "Readiness must be one of " + ", ".join(READINESS)}
        label = str(room_number or "").strip().upper()

        def run(cur):
            room = core_refs.room(cur, hotel_id, label)
            if not room:
                raise Refused("Room " + label + " not found")
            current = _readiness(cur, room["room_id"])
            if current is None:
                raise Refused("Room " + label + " has no readiness status")
            if current == status:
                return {"result": "Room " + label + " is already " + status}
            _set_readiness(cur, user_id, room["room_id"], label, status)
            return {"result": "Room " + label + ": " + current + " -> " + status}
        return _run(run)

    # ---------- assignment ----------

    def assign_rooms(self, hotel_id: str, user_id: str, booking_id: str, rooms: list, auto_fill: bool = False) -> dict:
        """rooms[i] takes every live night of room i+1. With auto_fill, the later rooms still without a room get a free one."""
        chosen = [str(room or "").strip().upper() for room in rooms or []]

        def run(cur):
            _booking(cur, hotel_id, booking_id)
            slots = _slots(_nights(cur, hotel_id, booking_id))
            if len(chosen) > len(slots):
                raise Refused("Booking " + booking_id + " has " + str(len(slots)) + " room(s); " + str(len(chosen)) + " given")
            placed = {}
            for index, seq in enumerate(sorted(slots)):
                nights = slots[seq]
                wanted = chosen[index] if index < len(chosen) else ""
                if not wanted and auto_fill and any(night["room_id"] is None for night in nights):
                    wanted = _free_room(cur, hotel_id, nights, placed.values())
                if wanted:
                    _place(cur, hotel_id, user_id, booking_id, nights, wanted, False)
                    placed[str(seq)] = wanted
            return {"result": "success", "booking_id": booking_id, "rooms": placed}
        return _run(run)

    def reassign_room_for_date_internal(self, hotel_id: str, user_id: str, booking_id: str, stay_date: str, old_room_number: str,
                                        new_room_number: str, mark_old_clean=None, change_type: bool = False) -> dict:
        """Move the booking's room that holds old_room_number (or no room) on stay_date to new_room_number (empty: unassign).
        Before arrival the room's every night moves; in house, the nights from stay_date on."""
        day = _day(stay_date)
        if not day:
            return {"error": BAD_DATE}
        old = str(old_room_number or "").strip().upper()
        new = str(new_room_number or "").strip().upper()

        def run(cur):
            _booking(cur, hotel_id, booking_id)
            nights = [night for night in _nights(cur, hotel_id, booking_id) if night["night_status"] in LIVE]
            tonight = [night for night in nights if night["stay_date"] == day and (night["room_number"] or "") == old]
            if not tonight:
                raise Refused(("Room " + old if old else "An unassigned room") + " is not on booking " + booking_id + " for " + day.isoformat())
            slot = [night for night in nights if night["room_seq"] == tonight[0]["room_seq"]]
            in_house = any(night["night_status"] == "checked_in" for night in slot)
            moving = [night for night in slot if not in_house or night["stay_date"] >= day]
            assigned_at = None
            if new:
                assigned_at = _place(cur, hotel_id, user_id, booking_id, moving, new, change_type)
            else:
                if in_house:
                    raise Refused("Undo check-in or check out before removing the room of an in-house guest")
                cur.execute("UPDATE booking_room_night SET room_id = NULL, assigned_at = NULL, assigned_by_user_id = NULL WHERE booking_night_id = ANY(%s)",
                            ([night["booking_night_id"] for night in moving],))
                _log(cur, hotel_id, user_id, booking_id, "room", old or None, None, "unassigned from " + day.isoformat())
            if mark_old_clean is not None and old:
                _set_readiness(cur, user_id, tonight[0]["room_id"], old, "clean" if mark_old_clean else "dirty")
            return {"result": "success", "booking_id": booking_id, "stay_date": day.isoformat(), "old_room_number": old,
                    "new_room_number": new, "assigned_at": _iso(assigned_at)}
        return _run(run)

    # ---------- check-in and check-out ----------

    def update_guest_check_in_status_internal(self, hotel_id: str, user_id: str, zone: str, booking_id: str, room_number: str = None, assignment_id: str = None) -> dict:
        """Check in one assigned room of tonight: its booked nights become checked_in. The room must be clean or inspected.
        Several rooms waiting, and no room or assignment chosen, returns {assignments: [{room_number, assignment_id}]} for the desk to pick."""
        wanted = str(room_number or "").strip().upper()
        chosen = str(assignment_id or "").strip()

        def run(cur):
            booking = _booking(cur, hotel_id, booking_id)
            if booking["booking_status"] not in ("confirmed", "checked_in"):
                raise Refused("Booking " + booking_id + " is " + booking["booking_status"])
            today = _today(zone)
            if today < booking["check_in_date"]:
                raise Refused("Check-in opens on " + booking["check_in_date"].isoformat())
            if today >= booking["check_out_date"]:
                raise Refused("The stay ended on " + booking["check_out_date"].isoformat())
            from booking_system.stay_rules import checkin_clock_refusal, deposit_refusal
            clock = checkin_clock_refusal(cur, hotel_id, zone, today, booking["check_in_date"])
            if clock:
                raise Refused(clock)
            deposit = deposit_refusal(cur, hotel_id, today, booking)
            if deposit:
                raise Refused(deposit)
            tonight = [night for night in _nights(cur, hotel_id, booking_id)
                       if night["stay_date"] == today and night["night_status"] == "booked" and night["room_id"]
                       and (not wanted or night["room_number"] == wanted) and (not chosen or night["booking_night_id"] == chosen)]
            rooms = sorted({night["room_number"] for night in tonight})
            if len(rooms) > 1:
                return {"assignments": [{"room_number": room, "assignment_id": next(night["booking_night_id"] for night in tonight if night["room_number"] == room)}
                                        for room in rooms]}
            if len(rooms) != 1:
                raise Refused("No assigned room is waiting for check-in tonight; assign a room first")
            room_id, label = tonight[0]["room_id"], rooms[0]
            status = _readiness(cur, room_id)
            allowed = _release(cur, hotel_id, today)
            if status not in allowed:
                raise Refused("Room " + label + " is " + str(status or "without a readiness status") + "; it must be " + " or ".join(allowed)
                              + " (hotel setting front_desk.checkin_room_readiness)")
            cur.execute("UPDATE booking_room_night SET night_status = 'checked_in', checked_in_at = now() WHERE booking_id = %s AND room_id = %s AND night_status = 'booked'",
                        (booking_id, room_id))
            nights = cur.rowcount
            _settle(cur, hotel_id, user_id, booking_id)
            return {"result": "Checked in " + booking_id + " to room " + label, "booking_id": booking_id, "room_number": label, "nights": nights}
        result = _run(run)
        if result.get("room_number") and "assignments" not in result and not result.get("error"):
            try:
                from booking_system.stay_notice import notify_welcome
                result["whatsapp"] = notify_welcome(hotel_id, booking_id, result["room_number"], result.get("nights"))
            except Exception as exc:
                result["whatsapp"] = {"error": str(exc)}
        return result

    def undo_guest_check_in_internal(self, hotel_id: str, user_id: str, booking_id: str, room_number: str, mark_clean: bool = True) -> dict:
        """Return one checked-in room to booked; the room becomes clean or dirty. Other rooms stay as they are."""
        label = str(room_number or "").strip().upper()

        def run(cur):
            _booking(cur, hotel_id, booking_id)
            room = core_refs.room(cur, hotel_id, label)
            if not room:
                raise Refused("Room " + label + " not found")
            cur.execute("UPDATE booking_room_night SET night_status = 'booked', checked_in_at = NULL WHERE booking_id = %s AND room_id = %s AND night_status = 'checked_in'",
                        (booking_id, room["room_id"]))
            if not cur.rowcount:
                raise Refused("Room " + label + " is not checked in for " + booking_id)
            _set_readiness(cur, user_id, room["room_id"], label, "clean" if mark_clean else "dirty")
            _settle(cur, hotel_id, user_id, booking_id)
            return {"result": "Undid check-in for room " + label + " on " + booking_id}
        return _run(run)

    def update_guest_check_out_status_internal(self, hotel_id: str, user_id: str, booking_id: str, room_number: str = None) -> dict:
        """Refused while a balance is due. A late departure records its fee in its own transaction first, so the refusal leaves the fee
        to be paid. The in-house nights of the room (or of every room) become checked_out; the room turns dirty."""
        from booking_system.stay_rules import apply_late_fee
        fee = _run(lambda cur: apply_late_fee(cur, hotel_id, user_id, booking_id))
        if fee.get("error"):
            return fee
        wanted = str(room_number or "").strip().upper()

        def run(cur):
            _booking(cur, hotel_id, booking_id)
            cur.execute("SELECT balance_due, currency, COALESCE(balance_due > 0, FALSE) AS owed FROM booking_balance WHERE booking_id = %s", (booking_id,))
            balance = cur.fetchone()
            if balance["owed"]:
                raise Refused("Balance due " + str(balance["currency"]).strip() + " " + str(balance["balance_due"]) + "; settle it before check-out")
            in_house = [night for night in _nights(cur, hotel_id, booking_id)
                        if night["night_status"] == "checked_in" and (not wanted or night["room_number"] == wanted)]
            if not in_house:
                raise Refused("Room " + wanted + " is not checked in for " + booking_id if wanted else "No room is checked in for " + booking_id)
            cur.execute("UPDATE booking_room_night SET night_status = 'checked_out', checked_out_at = now() WHERE booking_night_id = ANY(%s)",
                        ([night["booking_night_id"] for night in in_house],))
            rooms = sorted({(night["room_number"], night["room_id"]) for night in in_house})
            for label, room_id in rooms:
                _set_readiness(cur, user_id, room_id, label, "dirty")
            _settle(cur, hotel_id, user_id, booking_id)
            return {"result": "Checked out " + booking_id + " from room " + ", ".join(label for label, _ in rooms),
                    "booking_id": booking_id, "rooms": [label for label, _ in rooms], "nights": len(in_house)}
        result = _run(run)
        if fee.get("late_fee_amount") is not None:
            result = dict(result, late_fee_amount=fee["late_fee_amount"])
        if result.get("rooms") and not result.get("error"):
            notices = []
            for label in result["rooms"]:
                try:
                    from booking_system.stay_notice import notify_checkout
                    notices.append(notify_checkout(hotel_id, booking_id, label, result.get("nights")))
                except Exception as exc:
                    notices.append({"error": str(exc), "room_number": label})
            result["whatsapp"] = notices
        return result

    def reset_stay_state_for_date_internal(self, hotel_id: str, user_id: str, booking_id: str, stay_date: str) -> dict:
        """Return every checked-in or checked-out night of the booking to booked, keeping its room. Readiness is unchanged."""
        day = _day(stay_date)
        if not day:
            return {"error": BAD_DATE}

        def run(cur):
            _booking(cur, hotel_id, booking_id)
            nights = _nights(cur, hotel_id, booking_id)
            if not any(night["stay_date"] == day for night in nights):
                raise Refused(day.isoformat() + " is not a night of " + booking_id)
            touched = [night for night in nights if night["night_status"] in ("checked_in", "checked_out")]
            if not touched:
                raise Refused("Booking " + booking_id + " has no checked-in or checked-out night")
            departed = [night for night in touched if night["night_status"] == "checked_out"]
            for room_id, label in sorted({(night["room_id"], night["room_number"]) for night in departed}):
                mine = [night for night in departed if night["room_id"] == room_id]
                busy = _busy(cur, room_id, [night["stay_date"] for night in mine], [night["booking_night_id"] for night in mine])
                if busy:
                    raise Refused("Room " + label + " is taken again on: " + ", ".join(busy))
            cur.execute("UPDATE booking_room_night SET night_status = 'booked', checked_in_at = NULL, checked_out_at = NULL WHERE booking_night_id = ANY(%s)",
                        ([night["booking_night_id"] for night in touched],))
            _settle(cur, hotel_id, user_id, booking_id)
            kept = ", ".join(sorted({night["room_number"] for night in touched}))
            return {"result": "Reset stay state for " + booking_id + " across " + str(len(touched)) + " night(s) (kept room(s) " + kept + ")"}
        return _run(run)

    def read_past_stays_internal(self, hotel_id: str, zone: str) -> dict:
        """READ live nights before yesterday, per night and per booking."""
        cutoff = _today(zone) - timedelta(days=1)
        nights = db.transaction(lambda cur: _open_nights(cur, hotel_id, "n.stay_date < %s", (cutoff,)))
        items, bookings = [], {}
        for night in nights:
            row = {"booking_id": night["booking_id"], "stay_date": night["stay_date"].isoformat(), "guest_phone": night["guest_phone"],
                   "guest_name": night["guest_name"], "rooms_booked": night["rooms_booked"], "stay_status": STAY_STATUS[night["night_status"]],
                   "room_number": night["room_number"]}
            items.append(row)
            entry = bookings.setdefault(night["booking_id"], {"booking_id": night["booking_id"], "guest_phone": night["guest_phone"],
                                                              "guest_name": night["guest_name"], "rooms_booked": night["rooms_booked"],
                                                              "stay_dates": [], "rooms": [], "stay_status": row["stay_status"], "balance_owed": night["owed"]})
            entry["stay_dates"] = sorted(set(entry["stay_dates"]) | {row["stay_date"]})
            entry["rooms"] = sorted(set(entry["rooms"]) | ({night["room_number"]} - {None}))
        return {"cutoff": cutoff.isoformat(), "items": items, "bookings": list(bookings.values())}

    def bulk_checkout_past_internal(self, hotel_id: str, user_id: str, zone: str) -> dict:
        """Check out every assigned live night before yesterday. Bookings with a balance due are skipped. Readiness is unchanged."""
        cutoff = _today(zone) - timedelta(days=1)

        def run(cur):
            result = _check_out_nights(cur, hotel_id, user_id, _open_nights(cur, hotel_id, "n.stay_date < %s", (cutoff,)))
            return dict(result, cutoff=cutoff.isoformat())
        return _run(run)

    def bulk_checkout_selected_internal(self, hotel_id: str, user_id: str, items: list) -> dict:
        """Check out the selected (booking_id, stay_date) nights. Bookings with a balance due are skipped. Readiness is unchanged."""
        pairs = {(str((item or {}).get("booking_id") or ""), _day((item or {}).get("stay_date"))) for item in items or []}
        pairs = {pair for pair in pairs if pair[0] and pair[1]}
        if not pairs:
            return {"error": "No items provided"}

        def run(cur):
            nights = _open_nights(cur, hotel_id, "n.booking_id = ANY(%s)", ([pair[0] for pair in pairs],))
            return _check_out_nights(cur, hotel_id, user_id, [night for night in nights if (night["booking_id"], night["stay_date"]) in pairs])
        return _run(run)

    # ---------- calendar and booking reads ----------

    def read_room_number_assignments_calendar_internal(self, hotel_id: str, start_date: str, end_date: str, room_type: str = None, room_numbers: list = None) -> dict:
        """READ the room grid from the day before start_date to end_date: nights per room and date, room blocks, and unassigned nights by room type."""
        start, end = _day(start_date), _day(end_date)
        if not start or not end:
            return {"error": BAD_DATE}
        if end < start:
            return {"error": "end_date must be on or after start_date"}
        first = start - timedelta(days=1)
        type_name = str(room_type or "").strip().upper() or None
        numbers = [str(number).strip().upper() for number in room_numbers] if room_numbers else None

        def run(cur):
            cur.execute(
                """SELECT r.room_id, r.room_number, t.room_type_name FROM room r JOIN room_type t ON t.room_type_id = r.room_type_id
                   WHERE r.hotel_id = %s AND r.status = 'active' AND (%s::text IS NULL OR t.room_type_name = %s) AND (%s::text[] IS NULL OR r.room_number = ANY(%s))
                   ORDER BY t.room_type_name, r.room_number""",
                (hotel_id, type_name, type_name, numbers, numbers),
            )
            rooms = cur.fetchall()
            cur.execute(
                """SELECT n.room_id, n.stay_date, n.booking_id, n.night_status, t.room_type_name, b.channel, g.guest_name, ph.identity_value AS guest_phone
                   FROM booking_room_night n JOIN booking b ON b.booking_id = n.booking_id JOIN guest g ON g.guest_id = b.guest_id
                   JOIN room_type t ON t.room_type_id = n.room_type_id""" + _identity("g", "phone", "ph") + """
                   WHERE n.hotel_id = %s AND n.stay_date BETWEEN %s AND %s AND n.night_status IN ('booked','checked_in','checked_out')
                   ORDER BY n.stay_date, n.booking_id, n.room_seq""",
                (hotel_id, first, end),
            )
            nights = cur.fetchall()
            cur.execute("SELECT room_id, start_date, end_date, reason FROM room_block WHERE hotel_id = %s AND start_date <= %s AND end_date >= %s ORDER BY start_date",
                        (hotel_id, end, start))
            return rooms, nights, cur.fetchall()
        rooms, nights, blocks = db.transaction(run)

        grid = {room["room_id"]: {"room_number": room["room_number"], "room_type": room["room_type_name"], "entries": {}} for room in rooms}
        ooo_blocks = {}
        for block in blocks:
            if block["room_id"] not in grid:
                        continue
            room = grid[block["room_id"]]
            span = {"start": max(block["start_date"], start).isoformat(), "end": min(block["end_date"], end).isoformat(), "reason": block["reason"]}
            room.setdefault("ooo_linear", []).append(span)
            ooo_blocks.setdefault(room["room_number"], []).append(span)
            day = max(block["start_date"], start)
            while day <= min(block["end_date"], end):
                room["entries"][day.isoformat()] = {"status": "out-of-order", "booking_id": None, "guest_phone": None, "guest_name": None,
                                                    "channel": "", "stay_status": None, "notes": block["reason"]}
                day += timedelta(days=1)
        unassigned = {}
        for night in nights:
            entry = {"booking_id": night["booking_id"], "guest_phone": night["guest_phone"], "guest_name": night["guest_name"], "channel": night["channel"]}
            if night["room_id"] in grid:
                grid[night["room_id"]]["entries"][night["stay_date"].isoformat()] = dict(entry, status="assigned", stay_status=STAY_STATUS[night["night_status"]], notes=None)
            elif night["room_id"] is None and night["stay_date"] >= start and type_name in (None, night["room_type_name"]):
                unassigned.setdefault(night["room_type_name"], {}).setdefault(night["stay_date"].isoformat(), []).append(dict(entry, stay_status="pending"))
        return {"start_date": start.isoformat(), "end_date": end.isoformat(),
                "days": [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)],
                "rooms": list(grid.values()), "ooo_blocks": ooo_blocks, "unassigned_by_type": unassigned}

    def read_booking_previews(self, hotel_id: str, booking_ids: list) -> dict:
        """READ tooltip previews {booking_id: preview} in one query."""
        ids = sorted({str(booking_id) for booking_id in booking_ids or [] if booking_id})
        rows = db.rows(
            """SELECT s.booking_id, h.hotel_name, s.currency, s.gross_amount, s.check_in_date, s.check_out_date,
                      ph.identity_value AS guest_phone, em.identity_value AS guest_email
               FROM booking_summary s JOIN hotel h ON h.hotel_id = s.hotel_id""" + _identity("s", "phone", "ph") + _identity("s", "email", "em") + """
               WHERE s.hotel_id = %s AND s.booking_id = ANY(%s)""",
            (hotel_id, ids),
        )
        notes = {doc.get("booking_id"): doc.get("booking_notes") for doc in self.records["guest_bookings"].find(
            {"hotel_id": hotel_id, "booking_id": {"$in": ids}}, {"booking_id": 1, "booking_notes": 1})}
        previews = {booking_id: {"error": "Booking " + booking_id + " not found"} for booking_id in ids}
        for row in rows:
            previews[row["booking_id"]] = {
                "booking_id": row["booking_id"], "hotel_name": row["hotel_name"], "guest_phone": row["guest_phone"], "guest_email": row["guest_email"],
                "total_price": _money(row["gross_amount"]), "booking_notes": notes.get(row["booking_id"]), "currency": (row["currency"] or "").strip() or None,
                "check_in_date": _iso(row["check_in_date"]), "check_out_date": _iso(row["check_out_date"]),
            }
        return previews

    def read_booking_preview_internal(self, hotel_id: str, booking_id: str) -> dict:
        return self.read_booking_previews(hotel_id, [booking_id])[str(booking_id)]

    def read_booking_detail_internal(self, hotel_id: str, booking_id: str, stay_date: str) -> dict:
        """READ the edit modal: guest, booking, payment, and the rooms and stay state of stay_date."""
        day = _day(stay_date)
        if not day:
            return {"error": "stay_date is required (YYYY-MM-DD)"}

        def run(cur):
            cur.execute(
                """SELECT s.booking_id, s.guest_id, s.booking_status, s.currency, s.check_in_date, s.check_out_date, s.rooms_booked, b.rate_plan,
                          h.hotel_name, g.guest_name, g.nationality, g.vip, ph.identity_value AS guest_phone, em.identity_value AS guest_email,
                          bal.gross_amount, bal.amount_paid, bal.balance_due,
                          CASE WHEN bal.gross_amount > 0 AND bal.balance_due <= 0 THEN 'completed' WHEN bal.amount_paid > 0 THEN 'partial' ELSE 'pending' END AS payment_status,
                          (SELECT p.payment_method FROM payment p WHERE p.booking_id = s.booking_id AND p.payment_status = 'received' AND p.payment_kind = 'payment'
                           ORDER BY p.paid_at DESC LIMIT 1) AS payment_method
                   FROM booking_summary s JOIN booking b ON b.booking_id = s.booking_id JOIN hotel h ON h.hotel_id = s.hotel_id
                   JOIN guest g ON g.guest_id = s.guest_id JOIN booking_balance bal ON bal.booking_id = s.booking_id"""
                + _identity("g", "phone", "ph") + _identity("g", "email", "em") + " WHERE s.hotel_id = %s AND s.booking_id = %s",
                (hotel_id, booking_id),
            )
            head = cur.fetchone()
            if not head:
                raise Refused("Booking " + str(booking_id) + " not found")
            return head, _nights(cur, hotel_id, booking_id)
        found = _run(run)
        if isinstance(found, dict):
            return found
        head, nights = found
        profile = self.records["guests"].find_one({"hotel_id": hotel_id, "guest_id": head["guest_id"]}, {"_id": 0}) or {}
        booking_doc = self.records["guest_bookings"].find_one({"hotel_id": hotel_id, "booking_id": booking_id}, {"_id": 0}) or {}
        plan = self.records["rate_plans"].find_one({"hotel_id": hotel_id, "rate_plan": head["rate_plan"]}, {"description": 1}) if head["rate_plan"] else None
        tonight = [night for night in nights if night["stay_date"] == day and night["night_status"] in STAY_STATUS]
        placed = [night for night in tonight if night["room_id"]]
        total = _money(head["gross_amount"])
        currency = (head["currency"] or "").strip() or None
        return {
            "booking_id": head["booking_id"], "hotel_name": head["hotel_name"], "stay_date": day.isoformat(),
            "check_in_date": _iso(head["check_in_date"]), "check_out_date": _iso(head["check_out_date"]),
            "guest": dict({field: profile.get(field) or "" for field in PROFILE_FIELDS},
                          guest_phone=head["guest_phone"], reference_phone=str(booking_doc.get("reference_phone") or ""), phone_verified=bool(head["guest_phone"]),
                          guest_name=head["guest_name"], guest_email=head["guest_email"], nationality=head["nationality"] or "", country=head["nationality"] or "",
                          vip=bool(head["vip"])),
            "booking": {"booking_status": head["booking_status"], "booking_notes": booking_doc.get("booking_notes"), "total_amount": total,
                        "rooms_booked": head["rooms_booked"], "rate_plan": head["rate_plan"] or "", "rate_plan_description": str((plan or {}).get("description") or ""),
                        "room_type": tonight[0]["room_type_name"] if tonight else None},
            "payment": {"payment_status": head["payment_status"], "amount_paid": _money(head["amount_paid"]), "remaining_balance": _money(head["balance_due"]),
                        "payment_method": head["payment_method"], "total_amount": total, "currency": currency},
            "checkin": {
                "is_checked_in": any(night["night_status"] == "checked_in" for night in placed),
                "today_rooms": [night["room_number"] for night in placed],
                "rooms": [{"room_number": night["room_number"], "stay_status": STAY_STATUS[night["night_status"]]} for night in placed],
                "checked_in_at": _iso(min((night["checked_in_at"] for night in placed if night["checked_in_at"]), default=None)),
                "checked_out_at": _iso(max((night["checked_out_at"] for night in placed if night["checked_out_at"]), default=None)),
                "assigned_at": _iso(min((night["assigned_at"] for night in placed if night["assigned_at"]), default=None)),
                "can_check_in_today": bool(tonight),
            },
        }

    def read_assignment_history_internal(self, hotel_id: str, booking_id: str) -> dict:
        """READ the booking's current nights and every logged room change."""
        def run(cur):
            cur.execute(
                """SELECT n.stay_date, n.room_seq, r.room_number, n.assigned_at, u.user_name, n.night_status
                   FROM booking_room_night n LEFT JOIN room r ON r.room_id = n.room_id LEFT JOIN user_account u ON u.user_id = n.assigned_by_user_id
                   WHERE n.hotel_id = %s AND n.booking_id = %s ORDER BY n.stay_date, n.room_seq""",
                (hotel_id, booking_id),
            )
            nights = cur.fetchall()
            cur.execute(
                """SELECT l.modified_at, l.old_value, l.new_value, l.change_reason, u.user_name FROM booking_modification_log l
                   LEFT JOIN user_account u ON u.user_id = l.modified_by_user_id
                   WHERE l.hotel_id = %s AND l.booking_id = %s AND l.field_changed = 'room' ORDER BY l.modified_at""",
                (hotel_id, booking_id),
            )
            return nights, cur.fetchall()
        nights, changes = db.transaction(run)
        entries = [{"source": "booking_room_night", "stay_date": night["stay_date"].isoformat(), "room_number": night["room_number"] or "",
                    "assigned_at": _iso(night["assigned_at"]), "assigned_by": night["user_name"], "stay_index": night["room_seq"],
                    "night_status": night["night_status"]} for night in nights]
        entries += [{"source": "booking_modification_log", "room_number": change["new_value"] or "", "previous_room_number": change["old_value"] or "",
                     "assigned_at": _iso(change["modified_at"]), "assigned_by": change["user_name"], "note": change["change_reason"]} for change in changes]
        entries.sort(key=lambda entry: (entry["assigned_at"] or "", entry.get("stay_date") or "", entry["room_number"]))
        return {"booking_id": booking_id, "entries": entries}

    def read_assignment_dashboard(self, hotel_id: str, zone: str, stay_date: str = None) -> dict:
        """READ each room's state for one night (today when stay_date is empty) and the counts per state."""
        day = _day(stay_date) if stay_date else _today(zone)
        if not day:
            return {"error": BAD_DATE}
        rows = db.rows(
            """SELECT r.room_number, t.room_type_name, s.readiness_status, n.booking_id,
                      CASE WHEN n.night_status = 'checked_in' THEN 'occupied' WHEN n.booking_id IS NOT NULL THEN 'assigned'
                           WHEN k.reason IS NOT NULL THEN 'out_of_order' ELSE 'available' END AS status, k.reason AS block_reason
               FROM room r JOIN room_type t ON t.room_type_id = r.room_type_id LEFT JOIN room_readiness_status s ON s.room_id = r.room_id
               LEFT JOIN booking_room_night n ON n.room_id = r.room_id AND n.stay_date = %s AND n.night_status IN ('booked','checked_in')
               LEFT JOIN LATERAL (SELECT b.reason FROM room_block b WHERE b.room_id = r.room_id AND %s BETWEEN b.start_date AND b.end_date LIMIT 1) k ON TRUE
               WHERE r.hotel_id = %s AND r.status = 'active' ORDER BY r.room_number""",
            (day, day, hotel_id),
        )
        return {"stay_date": day.isoformat(), "summary": dict(Counter(row["status"] for row in rows)), "rooms": rows}

    def read_pricing_breakdown_internal(self, hotel_id: str, booking_id: str) -> dict:
        """READ the priced nights per date and room type, and the booking total."""
        def run(cur):
            cur.execute("SELECT gross_amount, currency FROM booking WHERE hotel_id = %s AND booking_id = %s", (hotel_id, booking_id))
            head = cur.fetchone()
            if not head:
                raise Refused("Booking " + str(booking_id) + " not found")
            cur.execute(
                """SELECT n.stay_date, t.room_type_name, COUNT(*) AS rooms_booked, MIN(n.sell_rate) AS unit_price, SUM(n.sell_rate) AS line_total,
                          string_agg(DISTINCT n.rate_source, ', ') AS rate_source
                   FROM booking_room_night n JOIN room_type t ON t.room_type_id = n.room_type_id
                   WHERE n.hotel_id = %s AND n.booking_id = %s AND n.night_status NOT IN ('cancelled','released')
                   GROUP BY n.stay_date, t.room_type_name ORDER BY n.stay_date, t.room_type_name""",
                (hotel_id, booking_id),
            )
            return head, cur.fetchall()
        found = _run(run)
        if isinstance(found, dict):
            return found
        head, lines = found
        subtotal = sum(_money(line["line_total"]) or 0.0 for line in lines)
        return {
            "booking_id": booking_id, "room_type": lines[0]["room_type_name"] if lines else None,
            "lines": [{"stay_date": line["stay_date"].isoformat(), "room_type": line["room_type_name"], "rooms_booked": line["rooms_booked"],
                       "unit_price": _money(line["unit_price"]), "line_total": _money(line["line_total"]), "notes": line["rate_source"] or ""} for line in lines],
            "subtotal": subtotal, "computed_total": subtotal, "current_total": _money(head["gross_amount"]), "currency": (head["currency"] or "").strip() or None,
        }

    def override_pricing_internal(self, hotel_id: str, user_id: str, booking_id: str, new_total_amount: float) -> dict:
        """Scale the priced nights' sell_rate to the new total (last night takes the rounding), set gross_amount, and log it."""
        total = Decimal(str(new_total_amount)).quantize(CENT)
        if total < 0:
            return {"error": "The total cannot be negative"}

        def run(cur):
            cur.execute("SELECT gross_amount FROM booking WHERE hotel_id = %s AND booking_id = %s", (hotel_id, booking_id))
            head = cur.fetchone()
            if not head:
                raise Refused("Booking " + str(booking_id) + " not found")
            priced = [night for night in _nights(cur, hotel_id, booking_id) if night["night_status"] not in ("cancelled", "released")]
            unpriced = [night["stay_date"].isoformat() for night in priced if night["sell_rate"] is None]
            if unpriced:
                raise Refused("Nights without a rate: " + ", ".join(unpriced))
            current = sum(night["sell_rate"] for night in priced)
            if current <= 0:
                raise Refused("The booking has no priced nights to scale")
            rates = [(night["sell_rate"] * total / current).quantize(CENT) for night in priced]
            rates[-1] = total - sum(rates[:-1])
            for night, rate in zip(priced, rates):
                cur.execute("UPDATE booking_room_night SET sell_rate = %s, rate_source = 'override' WHERE booking_night_id = %s", (rate, night["booking_night_id"]))
            cur.execute("UPDATE booking SET gross_amount = %s, currency = %s, updated_at = now(), updated_by_user_id = %s WHERE booking_id = %s",
                        (total, priced[0]["currency"], user_id, booking_id))
            _log(cur, hotel_id, user_id, booking_id, "gross_amount", str(head["gross_amount"]) if head["gross_amount"] is not None else None, str(total), "price override")
            cur.execute(
                """SELECT gross_amount, amount_paid, balance_due,
                          CASE WHEN gross_amount > 0 AND balance_due <= 0 THEN 'completed' WHEN amount_paid > 0 THEN 'partial' ELSE 'pending' END AS payment_status
                   FROM booking_balance WHERE booking_id = %s""",
                (booking_id,),
            )
            balance = cur.fetchone()
            return {"result": "success", "booking_id": booking_id, "total_amount": _money(balance["gross_amount"]), "amount_paid": _money(balance["amount_paid"]),
                    "remaining_balance": _money(balance["balance_due"]), "payment_status": balance["payment_status"]}
        return _run(run)

    # ---------- booking and guest edits ----------

    def update_booking_detail_internal(self, hotel_id: str, user_id: str, booking_id: str, guest: dict = None, booking: dict = None) -> dict:
        """READ then UPDATE the stay edit: guest name, nationality, VIP and email in the guest tables, the other profile fields in the
        guest document, booking notes in the booking document, and a changed rate plan through booking_manager.modify_booking."""
        guest, booking = guest or {}, booking or {}
        profile = {field: guest[field] for field in PROFILE_FIELDS if field in guest}
        email = str(guest.get("guest_email") or "").strip().lower()
        plan = str(booking.get("rate_plan") or "").strip().upper()

        def run(cur):
            cur.execute(
                """SELECT b.rate_plan, g.guest_id, g.guest_name, g.nationality, g.vip, em.identity_id, em.identity_value AS email
                   FROM booking b JOIN guest g ON g.guest_id = b.guest_id""" + _identity("g", "email", "em") + " WHERE b.hotel_id = %s AND b.booking_id = %s",
                (hotel_id, booking_id),
            )
            row = cur.fetchone()
            if not row:
                raise Refused("Booking " + str(booking_id) + " not found")
            given = {"guest_name": str(guest.get("guest_name") or "").strip().upper(), "nationality": str(guest.get("nationality") or "").strip(), "vip": guest.get("vip")}
            changed = {key: value for key, value in given.items() if value not in (None, "") and value != row[key]}
            if changed:
                cur.execute("UPDATE guest SET " + ", ".join(key + " = %s" for key in changed) + " WHERE guest_id = %s", tuple(changed.values()) + (row["guest_id"],))
            if email and email != row["email"]:
                owner = core_refs.guest_by_identity(cur, hotel_id, "email", email)
                if owner and owner["guest_id"] != row["guest_id"]:
                    raise Refused("Email " + email + " belongs to guest " + owner["guest_name"])
                if row["identity_id"]:
                    cur.execute("UPDATE guest_identity SET identity_value = %s WHERE identity_id = %s", (email, row["identity_id"]))
                else:
                    cur.execute("INSERT INTO guest_identity (identity_id, hotel_id, guest_id, identity_type, identity_value) VALUES (%s, %s, %s, 'email', %s)",
                                (new_id("gid"), hotel_id, row["guest_id"], email))
            if profile:
                guests = self.records["guests"]
                guests.write_in(cur, *guests.staged_update({"hotel_id": hotel_id, "guest_id": row["guest_id"]}, {"$set": profile}, upsert=True))
            if "booking_notes" in booking:
                docs = self.records["guest_bookings"]
                docs.write_in(cur, *docs.staged_update({"hotel_id": hotel_id, "booking_id": booking_id}, {"$set": {"booking_notes": booking["booking_notes"]}}, upsert=True))
            return plan and plan != (row["rate_plan"] or "")
        replan = _run(run)
        if isinstance(replan, dict):
            return replan
        if replan:
            from booking_system.booking_manager import booking_manager
            changed = booking_manager.modify_booking(hotel_id, user_id, booking_id, {"rate_plan": plan})
            if changed.get("error"):
                return changed
        return {"result": "success", "booking_id": booking_id}

    def verify_booking_phone(self, hotel_id: str, user_id: str, booking_id: str, guest_phone: str = "", guest_name: str = "", country_code: str = "") -> dict:
        """Send the guest link by WhatsApp to the typed (or reference) phone, then link that phone to the booking's guest: an existing
        guest with the phone becomes the booking's guest, otherwise the phone is added to the booking's guest."""
        found = db.row(
            """SELECT b.guest_id, g.guest_name, h.hotel_name FROM booking b JOIN guest g ON g.guest_id = b.guest_id JOIN hotel h ON h.hotel_id = b.hotel_id
               WHERE b.hotel_id = %s AND b.booking_id = %s""",
            (hotel_id, booking_id),
        )
        if not found:
            return {"error": "Booking " + booking_id + " not found"}
        docs = self.records["guest_bookings"]
        booking_doc = docs.find_one({"hotel_id": hotel_id, "booking_id": booking_id}, {"_id": 0}) or {}
        reference_phone = str(booking_doc.get("reference_phone") or "").strip()
        dial = str(country_code or "").strip()
        raw_phone = str(guest_phone or "").strip() or reference_phone
        name = str(guest_name or "").strip() or found["guest_name"]
        if not dial:
            return {"error": "Country code is required"}
        if not raw_phone:
            return {"error": "Phone is required"}
        phone = self._international_phone(dial, raw_phone)
        if not phone:
            return {"error": "Country code does not match the phone number"}
        from booking_system.guest_link import guest_link
        from booking_system.whatsapp_templates import link_template_key, render_template
        hotel_name = found["hotel_name"]
        rendered = render_template(link_template_key(), {"guest_name": name, "hotel_name": hotel_name, "guest_link": guest_link(hotel_id, phone)})
        if rendered.get("error"):
            return {"error": rendered["error"]}
        sent = self.send_whatsapp_template(phone, rendered["template_name"], rendered["language"], rendered["values"], rendered["button"],
                                           rendered["sentence"], rendered.get("names"), rendered.get("named"))
        if sent.get("error"):
            return {"error": sent["error"]}

        def run(cur):
            owner = core_refs.guest_by_identity(cur, hotel_id, "phone", phone)
            if not owner:
                cur.execute("INSERT INTO guest_identity (identity_id, hotel_id, guest_id, identity_type, identity_value) VALUES (%s, %s, %s, 'phone', %s)",
                            (new_id("gid"), hotel_id, found["guest_id"], phone))
            elif owner["guest_id"] != found["guest_id"]:
                cur.execute("UPDATE booking SET guest_id = %s, updated_at = now(), updated_by_user_id = %s WHERE booking_id = %s", (owner["guest_id"], user_id, booking_id))
                _log(cur, hotel_id, user_id, booking_id, "guest_id", found["guest_id"], owner["guest_id"], "phone verified " + phone)
            fields = {"phone_verified": True, "guest_phone": phone, "guest_name": name, "reference_phone": reference_phone or phone}
            docs.write_in(cur, *docs.staged_update({"hotel_id": hotel_id, "booking_id": booking_id}, {"$set": fields}, upsert=True))
        db.transaction(run)
        from booking_system.guest_manager import guest_manager
        guest_manager.save_whatsapp_event(sent["message_id"], phone, hotel_id, datetime.now(timezone.utc).isoformat(), sent.get("body") or sent["template_name"], "out")
        return {"success": True, "guest_phone": phone, "guest_name": name, "phone_verified": True, "whatsapp_message_id": sent["message_id"]}

    # ---------- WhatsApp ----------

    def _international_phone(self, country_code: str, phone: str) -> str:
        dial = str(country_code or "").strip()
        local = "".join(ch for ch in str(phone or "") if ch.isdigit() or ch == "+")
        dial_digits = dial[1:] if dial.startswith("+") else ""
        if not dial_digits.isdigit() or dial_digits.startswith("0"):
            return ""
        if local.startswith(dial):
            return local
        if local.startswith("+"):
            return ""
        if local.startswith("0"):
            local = local[1:]
        if not local.isdigit():
            return ""
        return dial + local

    def _whatsapp_to(self, guest_phone: str) -> str:
        """Country calling code plus national number, trunk 0 removed. Guest profiles stay unchanged."""
        digits = "".join(ch for ch in str(guest_phone or "") if ch.isdigit())
        dial = ""
        for code in _WHATSAPP_DIAL_CODES:
            if digits.startswith(code):
                dial = code
                break
        national = digits[len(dial):]
        if national.startswith("0"):
            national = national[1:]
        if not dial or not national.isdigit():
            return ""
        return "+" + dial + national

    def whatsapp_click_url(self, guest_name: str = "") -> str:
        """Chat link that makes the guest message the hotel first."""
        from urllib.parse import quote
        raw = str(os.getenv("WHATSAPP_PHONE_NUMBER") or "").strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return ""
        name = str(guest_name or "").strip()
        text = "Hi, this is " + name + ". I have completed my profile." if name else "Hi, I have completed my profile."
        return "https://wa.me/" + digits + "?text=" + quote(text)

    def _post_whatsapp(self, payload: dict) -> dict:
        import urllib.error
        import urllib.request
        token = str(os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()
        phone_number_id = str(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
        if not token or not phone_number_id:
            return {"error": "WhatsApp access token is not configured"}
        version = str(os.getenv("WHATSAPP_GRAPH_VERSION") or "v26.0").strip()
        request = urllib.request.Request(
            "https://graph.facebook.com/" + version + "/" + phone_number_id + "/messages",
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {}
            return {"error": str((parsed.get("error") or {}).get("message") or "WhatsApp send failed")}
        except Exception as exc:
            return {"error": str(exc)}
        sent = (body.get("messages") or [{}])[0]
        message_id = str((sent or {}).get("id") or "")
        if not message_id:
            return {"error": "WhatsApp did not accept the message"}
        return {"message_id": message_id}

    def send_whatsapp_text(self, guest_phone: str, text: str) -> dict:
        """Send one free-form WhatsApp text. Meta accepts this only inside an open customer service window."""
        body = str(text or "").strip()[:4096]
        if not body:
            return {"error": "Message is required"}
        recipient = self._whatsapp_to(guest_phone)
        if not recipient:
            return {"error": "WhatsApp number needs a country code"}
        return self._post_whatsapp({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"body": body},
        })

    def send_whatsapp_list(self, guest_phone: str, header: str, body: str, footer: str, button: str, rows: list, sections: list = None) -> dict:
        """Send one interactive list. Meta accepts this only inside an open customer service window."""
        from booking_system.whatsapp_list import list_payload
        recipient = self._whatsapp_to(guest_phone)
        if not recipient:
            return {"error": "WhatsApp number needs a country code"}
        source = sections if sections else [{"title": "Sales Pulse", "rows": rows}]
        payload = list_payload(recipient, header, body, footer, button, source)
        if not payload:
            return {"error": "List needs a body, a button, and a row"}
        return self._post_whatsapp(payload)

    def send_whatsapp_interactive(self, guest_phone: str, interactive: dict) -> dict:
        """Send one completed interactive message (list or reply buttons). Meta accepts it only inside an open window."""
        recipient = self._whatsapp_to(guest_phone)
        if not recipient:
            return {"error": "WhatsApp number needs a country code"}
        if not isinstance(interactive, dict) or not interactive.get("type"):
            return {"error": "Interactive message is empty"}
        return self._post_whatsapp({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "interactive",
            "interactive": interactive,
        })

    def send_whatsapp_media(self, guest_phone: str, kind: str, media_id: str, caption: str = "") -> dict:
        """Send a file already uploaded to WhatsApp. kind is image, video, audio, or document."""
        from booking_system.whatsapp_media import caption_allowed
        recipient = self._whatsapp_to(guest_phone)
        if not recipient:
            return {"error": "WhatsApp number needs a country code"}
        media = str(kind or "").strip()
        file_id = str(media_id or "").strip()
        if not media or not file_id:
            return {"error": "WhatsApp media is not configured"}
        part = {"id": file_id}
        text = str(caption or "").strip()
        if text and caption_allowed(media):
            part["caption"] = text
        return self._post_whatsapp({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": media,
            media: part,
        })

    def send_whatsapp_template(self, guest_phone: str, template_name: str, language: str, values=None, button: str = "", sentence: str = "", names=None, named: bool = False) -> dict:
        """Send one approved Meta template. values fill the body fields in order. button is a dynamic URL suffix."""
        template_name = str(template_name or "").strip()
        language = str(language or "").strip()
        if not template_name:
            return {"error": "WhatsApp template is not configured"}
        if not language:
            return {"error": "WhatsApp template language is not configured"}
        recipient = self._whatsapp_to(guest_phone)
        if not recipient:
            return {"error": "WhatsApp number needs a country code"}
        labels = list(names or [])
        parameters = []
        for index, value in enumerate(values or []):
            text = str(value or "").strip()
            if not text:
                continue
            row = {"type": "text", "text": text}
            if named and index < len(labels) and labels[index]:
                row["parameter_name"] = str(labels[index])
            parameters.append(row)
        components = []
        if parameters:
            components.append({"type": "body", "parameters": parameters})
        suffix = str(button or "").strip()
        if suffix:
            components.append({
                "type": "button",
                "sub_type": "url",
                "index": "0",
                "parameters": [{"type": "text", "text": suffix}],
            })
        template = {"name": template_name, "language": {"code": language}}
        if components:
            template["components"] = components
        sent = self._post_whatsapp({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "template",
            "template": template,
        })
        if sent.get("message_id"):
            sent["template_name"] = template_name
            sent["body"] = str(sentence or "").strip() or template_name
        return sent

    def send_whatsapp_otp(self, guest_phone: str, code: str) -> dict:
        """Send the sign-in code as a normal WhatsApp message. The code is not written to the log."""
        sent = self.send_whatsapp_text(guest_phone, "Leon code: " + str(code or "").strip() + ". It expires in 10 minutes.")
        if sent.get("error"):
            print("whatsapp otp send failed: " + sent["error"], flush=True)
        elif sent.get("message_id"):
            print("whatsapp otp sent " + sent["message_id"], flush=True)
        return sent


intelligent_front_desk = FrontDesk()


def assign_room_number(hotel_id: str, user_id: str, booking_id: str, rooms: list, auto_fill: bool = False) -> dict:
    """WRITE: rooms[i] takes every night of booked room i+1 (same room type). auto_fill gives the later unassigned rooms a free room."""
    return intelligent_front_desk.assign_rooms(hotel_id, user_id, booking_id, rooms, auto_fill)


def read_assignment_dashboard(hotel_id: str, zone: str, stay_date: str = None) -> dict:
    return intelligent_front_desk.read_assignment_dashboard(hotel_id, zone, stay_date)


def read_assignment_history(hotel_id: str, booking_id: str) -> dict:
    return intelligent_front_desk.read_assignment_history_internal(hotel_id, booking_id)


def suggest_room_assignment(hotel_id: str, request: str, booking_id: str, stay_date: str = None) -> dict:
    """READ the free rooms of the booking's room type on stay_date (the arrival night when empty) and ask the model which fits the request."""
    def run(cur):
        from booking_system import inventory_manager
        booking = _booking(cur, hotel_id, booking_id)
        day = _day(stay_date) if stay_date else booking["check_in_date"]
        if not day:
            raise Refused(BAD_DATE)
        nights = [night for night in _nights(cur, hotel_id, booking_id) if night["stay_date"] == day and night["night_status"] in LIVE]
        if not nights:
            raise Refused(day.isoformat() + " is not a night of " + booking_id)
        free = inventory_manager.free_rooms(cur, hotel_id, nights[0]["room_type_id"], day)
        return {"stay_date": day.isoformat(), "room_type": nights[0]["room_type_name"], "rooms": sorted(row["room_number"] for row in free)}
    found = _run(run)
    if found.get("error"):
        return found
    if not found["rooms"]:
        return {"error": "No free " + found["room_type"] + " room on " + found["stay_date"]}
    from booking_system.llm_utils import haiku, model_text
    prompt = ("Front desk room assignment for booking " + booking_id + ", " + found["room_type"] + " on " + found["stay_date"] + ".\n"
              "Request: " + json.dumps(str(request or "")) + "\nFree rooms: " + ", ".join(found["rooms"]) + "\n"
              "Reply with JSON only: {\"recommended_room\": one free room, \"reasoning\": why, \"alternatives\": [up to 3 free rooms], "
              "\"special_notes\": anything the desk should tell the guest or ask}.")
    text = model_text(haiku().invoke([{"role": "user", "content": prompt}])).strip().strip("`")
    try:
        parsed = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except json.JSONDecodeError:
        return {"error": "The suggestion was not readable", "reply": text}
    return dict(parsed, booking_id=booking_id, stay_date=found["stay_date"], room_type=found["room_type"], free_rooms=found["rooms"])
