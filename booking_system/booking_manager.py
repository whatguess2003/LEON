"""Bookings on the core tables: one booking row and one booking_room_night per night per room (room_seq 1..rooms; room_id stays NULL
until the front desk assigns a room). Each public writer is one transaction that reads first; a refusal rolls back and returns
{'error': reason}. Booking notes and channel payloads are documents keyed by booking_id."""

import sys
from datetime import date, timedelta
from decimal import Decimal

from booking_system import core_refs, db
from booking_system.content_store import client
from booking_system.guest_manager import Refused, ensure_guest, run
from booking_system.hotel_registry import new_id

booking_docs = client["leon_hotel_memory"]["guest_bookings"]
channel_docs = client["leon_hotel_memory"]["ota_reservations"]
LIVE = ("booked", "checked_in")
# Changes that rebuild the nights; every other editable field is a booking column.
LAYOUT = ("check_in", "check_out", "room_type_name", "rooms", "nightly_rate")


def _day(value):
    from booking_system.semantic.dates import as_date
    found = value if isinstance(value, date) else as_date(value)
    if found is None:
        raise Refused("Invalid date: " + str(value))
    return found


def _nights(check_in, check_out):
    start, end = _day(check_in), _day(check_out)
    if end <= start:
        raise Refused("check_out must be after check_in")
    return [start + timedelta(days=offset) for offset in range((end - start).days)]


def stay_range(stay_dates):
    """{'check_in', 'check_out'} for a list of consecutive nights, or {'error'}."""
    try:
        days = sorted({_day(value) for value in stay_dates or []})
    except Refused as reason:
        return {"error": str(reason)}
    if not days:
        return {"error": "stay_dates are required"}
    if (days[-1] - days[0]).days + 1 != len(days):
        return {"error": "stay_dates must be consecutive nights"}
    return {"check_in": days[0].isoformat(), "check_out": (days[-1] + timedelta(days=1)).isoformat()}


def _text(value):
    return str(value).strip().upper() or None if value is not None else None


def _count(value):
    return int(value) if value is not None else None


def _rate_value(value):
    return Decimal(str(value)) if value is not None else None


def _iso(value):
    return _day(value).isoformat()


# Normal form of each editable field (modify_booking compares current and wanted in this form).
EDITABLE = {"check_in": _iso, "check_out": _iso, "room_type_name": _text, "rooms": _count, "nightly_rate": _rate_value,
            "adults": _count, "children": _count, "rate_plan": _text, "group_name": _text, "channel": _text}


def _currency(cur, hotel_id):
    cur.execute("SELECT currency FROM hotel WHERE hotel_id = %s", (hotel_id,))
    return cur.fetchone()["currency"].strip()


def _type_name(cur, room_type_id):
    cur.execute("SELECT room_type_name FROM room_type WHERE room_type_id = %s", (room_type_id,))
    return cur.fetchone()["room_type_name"]


def _room_type(cur, hotel_id, room_type_name):
    found = core_refs.room_type_id(cur, hotel_id, room_type_name)
    if not found:
        raise Refused("Room type " + str(room_type_name or "") + " not found")
    return found


def _rate(cur, room_type_id, stay_date, nightly_rate):
    """(sell_rate, rate_source): the given nightly rate, else the night's saved or base price."""
    if nightly_rate is not None:
        return Decimal(str(nightly_rate)), "override"
    cur.execute("SELECT COALESCE(saved_price, base_price) AS price FROM daily_pricing WHERE room_type_id = %s AND stay_date = %s",
                (room_type_id, stay_date))
    found = cur.fetchone()
    if not found:
        raise Refused('Pricing not found on "' + stay_date.isoformat() + '"')
    return found["price"], "desk"


def _check_available(cur, hotel_id, need):
    """need: {(room_type_id, stay_date): rooms}. Refuses a night with no inventory on file or too few rooms."""
    from booking_system.inventory_manager import available_rooms
    for (room_type_id, stay_date), rooms in sorted(need.items()):
        free = available_rooms(cur, hotel_id, room_type_id, stay_date)
        if free is None:
            raise Refused("No inventory on file for " + _type_name(cur, room_type_id) + " on " + stay_date.isoformat())
        if free < rooms:
            raise Refused("Insufficient availability for " + _type_name(cur, room_type_id) + " on " + stay_date.isoformat()
                          + ": " + str(free) + " free, " + str(rooms) + " needed")


def _write_nights(cur, hotel_id, booking_id, wanted, currency, check):
    """Make the booking's nights equal wanted {(stay_date, room_seq): (room_type_id, sell_rate, rate_source)}. A live night of the
    same room type is kept with its room; any other night is replaced. check: refuse added nights beyond availability.
    Returns every stay date the booking held before or holds now."""
    cur.execute("SELECT booking_night_id, stay_date, room_seq, room_type_id, night_status FROM booking_room_night WHERE booking_id = %s",
                (booking_id,))
    have = {(row["stay_date"], row["room_seq"]): row for row in cur.fetchall()}
    kept = {key for key in wanted if key in have and have[key]["room_type_id"] == wanted[key][0] and have[key]["night_status"] in LIVE}
    dropped = [row["booking_night_id"] for key, row in have.items() if key not in kept]
    if dropped:
        cur.execute("DELETE FROM booking_room_night WHERE booking_night_id = ANY(%s)", (dropped,))
    added = sorted(key for key in wanted if key not in kept)
    if check:
        need = {}
        for stay_date, _seq in added:
            slot = (wanted[(stay_date, _seq)][0], stay_date)
            need[slot] = need.get(slot, 0) + 1
        _check_available(cur, hotel_id, need)
    for key in kept:
        _room_type_id, rate, source = wanted[key]
        cur.execute("UPDATE booking_room_night SET sell_rate = %s, rate_source = %s, currency = %s WHERE booking_night_id = %s",
                    (rate, source, currency, have[key]["booking_night_id"]))
    for stay_date, room_seq in added:
        room_type_id, rate, source = wanted[(stay_date, room_seq)]
        cur.execute(
            """INSERT INTO booking_room_night (booking_night_id, hotel_id, booking_id, stay_date, room_seq, room_type_id, night_status,
                                               sell_rate, currency, rate_source)
               VALUES (%s, %s, %s, %s, %s, %s, 'booked', %s, %s, %s)""",
            (new_id("bkn"), hotel_id, booking_id, stay_date, room_seq, room_type_id, rate, currency, source),
        )
    return sorted({day.isoformat() for day, _seq in list(have) + list(wanted)})


def _log(cur, hotel_id, booking_id, user_id, field, old, new, reason=None):
    cur.execute(
        """INSERT INTO booking_modification_log (modification_id, hotel_id, booking_id, modified_by_user_id, field_changed, old_value,
                                                 new_value, change_reason)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (new_id("bkm"), hotel_id, booking_id, user_id, field, None if old is None else str(old), None if new is None else str(new), reason),
    )


def _write_document(cur, collection, hotel_id, booking_id, fields):
    if not fields:
        return
    key, body = collection.staged_update({"booking_id": booking_id}, {"$set": dict(fields, hotel_id=hotel_id)}, upsert=True)
    collection.write_in(cur, key, body)


def _locked(cur, hotel_id, booking_id):
    cur.execute("SELECT * FROM booking WHERE hotel_id = %s AND booking_id = %s FOR UPDATE", (hotel_id, booking_id))
    found = cur.fetchone()
    if not found:
        raise Refused("Booking " + str(booking_id or "") + " not found")
    return found


def booking_by_ref(cur, hotel_id, channel, channel_booking_ref):
    """The booking row holding a channel reference at this hotel, locked, or None."""
    cur.execute("SELECT * FROM booking WHERE hotel_id = %s AND channel = %s AND channel_booking_ref = %s FOR UPDATE",
                (hotel_id, channel, channel_booking_ref))
    return cur.fetchone()


def _gross(cur, booking_id):
    cur.execute("SELECT SUM(sell_rate) AS gross FROM booking_room_night WHERE booking_id = %s AND night_status = ANY(%s)",
                (booking_id, list(LIVE)))
    return cur.fetchone()["gross"]


# ---------- create ----------

def _create(cur, hotel_id, user_id, guest, room_type_name, check_in, check_out, rooms, adults, children, channel, channel_booking_ref,
            rate_plan, group_name, nightly_rate, commission_amount, commission_rate, notes):
    room_type_id = _room_type(cur, hotel_id, room_type_name)
    nights = _nights(check_in, check_out)
    if int(rooms) < 1:
        raise Refused("rooms must be at least 1")
    channel = _text(channel)
    if not channel:
        raise Refused("channel is required")
    ref = str(channel_booking_ref or "").strip() or None
    if ref and booking_by_ref(cur, hotel_id, channel, ref):
        raise Refused(channel + " reference " + ref + " is already booked")
    currency = _currency(cur, hotel_id)
    rates = {day: _rate(cur, room_type_id, day, nightly_rate) for day in nights}
    guest_id = ensure_guest(cur, hotel_id, user_id, **guest)
    booking_id = new_id("bkg")
    gross = sum(rate for rate, _source in rates.values()) * int(rooms)
    cur.execute(
        """INSERT INTO booking (booking_id, hotel_id, guest_id, booking_status, channel, channel_booking_ref, group_name, rate_plan, adults,
                                children, currency, gross_amount, commission_amount, commission_rate, created_by_user_id)
           VALUES (%s, %s, %s, 'confirmed', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (booking_id, hotel_id, guest_id, channel, ref, _text(group_name), _text(rate_plan), _count(adults), _count(children), currency,
         gross, commission_amount, commission_rate, user_id),
    )
    wanted = {(day, seq): (room_type_id,) + rates[day] for day in nights for seq in range(1, int(rooms) + 1)}
    stay_dates = _write_nights(cur, hotel_id, booking_id, wanted, currency, check=True)
    _write_document(cur, booking_docs, hotel_id, booking_id, {"booking_notes": notes} if notes else {})
    return {"booking_id": booking_id, "guest_id": guest_id, "room_type_name": _type_name(cur, room_type_id), "check_in": nights[0].isoformat(),
            "check_out": _day(check_out).isoformat(), "stay_dates": stay_dates, "rooms": int(rooms), "currency": currency,
            "gross_amount": float(gross)}


def create_booking(hotel_id, user_id, guest, room_type_name, check_in, check_out, rooms=1, adults=1, children=0, channel="PMS",
                   channel_booking_ref=None, rate_plan=None, group_name=None, nightly_rate=None, commission_amount=None,
                   commission_rate=None, notes=None):
    """guest: {'guest_name', 'phone', 'email', 'nationality'} (found by phone, then email, else created)."""
    return run(lambda cur: _create(cur, hotel_id, user_id, guest, room_type_name, check_in, check_out, rooms, adults, children, channel,
                                   channel_booking_ref, rate_plan, group_name, nightly_rate, commission_amount, commission_rate, notes))


def create_group_booking(hotel_id, user_id, guest, check_in, check_out, lines, group_name=None, channel="PMS", notes=None):
    """One booking per line {'room_type_name', 'rooms'}, all in one transaction."""
    def write(cur):
        if not lines:
            raise Refused("At least one room type is required")
        made = [_create(cur, hotel_id, user_id, guest, line["room_type_name"], check_in, check_out, line["rooms"], 1, 0, channel, None,
                        None, group_name, None, None, None, notes) for line in lines]
        return {"booking_ids": [item["booking_id"] for item in made], "bookings": made,
                "stay_dates": sorted({day for item in made for day in item["stay_dates"]})}
    return run(write)


# ---------- status changes ----------

def _close(cur, hotel_id, user_id, booking_id, status, stamp, reason):
    """confirmed -> cancelled | no_show. A booking already in that status is returned unchanged."""
    booking = _locked(cur, hotel_id, booking_id)
    cur.execute("SELECT DISTINCT stay_date FROM booking_room_night WHERE booking_id = %s ORDER BY stay_date", (booking_id,))
    stay_dates = [row["stay_date"].isoformat() for row in cur.fetchall()]
    result = {"booking_id": booking_id, "booking_status": status, "stay_dates": stay_dates}
    if booking["booking_status"] == status:
        return dict(result, unchanged=True)
    if booking["booking_status"] != "confirmed":
        raise Refused("Booking " + booking_id + " is " + booking["booking_status"] + "; only a confirmed booking can become " + status)
    cur.execute("UPDATE booking SET booking_status = %s, " + stamp + " = now(), updated_at = now(), updated_by_user_id = %s "
                "WHERE booking_id = %s", (status, user_id, booking_id))
    cur.execute("UPDATE booking_room_night SET night_status = %s WHERE booking_id = %s AND night_status = 'booked'", (status, booking_id))
    _log(cur, hotel_id, booking_id, user_id, "booking_status", booking["booking_status"], status, reason)
    return result


def cancel_in(cur, hotel_id, user_id, booking_id, reason=None):
    return _close(cur, hotel_id, user_id, booking_id, "cancelled", "cancelled_at", reason)


def cancel_booking(hotel_id, user_id, booking_id, reason=None):
    return run(lambda cur: cancel_in(cur, hotel_id, user_id, booking_id, reason))


def mark_no_show(hotel_id, user_id, booking_id, reason=None):
    return run(lambda cur: _close(cur, hotel_id, user_id, booking_id, "no_show", "no_show_at", reason))


# ---------- modify ----------

def _current(cur, booking, nights):
    types = {row["room_type_id"] for row in nights}
    return {
        "check_in": min(row["stay_date"] for row in nights).isoformat(),
        "check_out": (max(row["stay_date"] for row in nights) + timedelta(days=1)).isoformat(),
        "room_type_name": _type_name(cur, types.pop()) if len(types) == 1 else None,
        "rooms": max(row["room_seq"] for row in nights), "nightly_rate": None,
        "adults": booking["adults"], "children": booking["children"], "rate_plan": booking["rate_plan"],
        "group_name": booking["group_name"], "channel": booking["channel"],
    }


def modify_booking(hotel_id, user_id, booking_id, changes, reason=None):
    """changes: any of EDITABLE. Dates, room type, rooms or rate rebuild the nights (kept nights keep their room and rate);
    every changed field is logged in booking_modification_log."""
    unknown = sorted(set(changes) - set(EDITABLE))
    if unknown:
        return {"error": "Unknown booking fields: " + ", ".join(unknown)}

    def write(cur):
        booking = _locked(cur, hotel_id, booking_id)
        if booking["booking_status"] != "confirmed":
            raise Refused("Booking " + booking_id + " is " + booking["booking_status"] + "; only a confirmed booking can be modified")
        cur.execute("SELECT * FROM booking_room_night WHERE booking_id = %s AND night_status = ANY(%s)", (booking_id, list(LIVE)))
        nights = cur.fetchall()
        current = _current(cur, booking, nights)
        wanted = {name: EDITABLE[name](value) for name, value in dict(current, **changes).items()}
        changed = sorted(name for name in EDITABLE if wanted[name] != current[name])
        stay_dates = sorted({row["stay_date"].isoformat() for row in nights})
        if not changed:
            return {"booking_id": booking_id, "changed": [], "stay_dates": stay_dates}
        gross = booking["gross_amount"]
        if set(changed) & set(LAYOUT):
            if not wanted["room_type_name"]:
                raise Refused("Booking " + booking_id + " holds several room types; name the room type")
            stay_dates = _rebuild(cur, hotel_id, booking_id, booking["currency"], nights, wanted)
            gross = _gross(cur, booking_id)
        cur.execute(
            """UPDATE booking SET adults = %s, children = %s, rate_plan = %s, group_name = %s, channel = %s, gross_amount = %s,
                      updated_at = now(), updated_by_user_id = %s WHERE booking_id = %s""",
            (wanted["adults"], wanted["children"], wanted["rate_plan"], wanted["group_name"], wanted["channel"], gross, user_id, booking_id),
        )
        for name in changed:
            _log(cur, hotel_id, booking_id, user_id, name, current[name], wanted[name], reason)
        return {"booking_id": booking_id, "changed": changed, "stay_dates": stay_dates,
                "gross_amount": None if gross is None else float(gross)}
    return run(write)


def _rebuild(cur, hotel_id, booking_id, currency, nights, wanted):
    room_type_id = _room_type(cur, hotel_id, wanted["room_type_name"])
    have = {(row["stay_date"], row["room_seq"]): row for row in nights}
    plan = {}
    for day in _nights(wanted["check_in"], wanted["check_out"]):
        for seq in range(1, wanted["rooms"] + 1):
            old = have.get((day, seq))
            keep = wanted["nightly_rate"] is None and old is not None and old["room_type_id"] == room_type_id
            plan[(day, seq)] = (room_type_id,) + ((old["sell_rate"], old["rate_source"]) if keep else _rate(cur, room_type_id, day, wanted["nightly_rate"]))
    return _write_nights(cur, hotel_id, booking_id, plan, currency, check=True)


def write_booking_notes(hotel_id, user_id, booking_id, notes):
    def write(cur):
        _locked(cur, hotel_id, booking_id)
        _write_document(cur, booking_docs, hotel_id, booking_id, {"booking_notes": str(notes or "")})
        return {"booking_id": booking_id}
    return run(write)


# ---------- channel deliveries ----------

def _layout(nights):
    return ", ".join(row["stay_date"].isoformat() + "#" + str(row["room_seq"]) + " " + row["room_type_id"] for row in nights)


def write_channel_booking(cur, hotel_id, channel, channel_booking_ref, guest, lines, adults=None, children=None, currency=None,
                          gross_amount=None, commission_amount=None, commission_rate=None, documents=None):
    """Create or update the booking a channel sold under (channel, channel_booking_ref): a repeat delivery updates the same booking.
    lines: [(room_type_id, [(stay_date, sell_rate or None)])]. The channel already sold these nights, so availability is not
    checked and an overbooking shows as negative availability. documents: {collection: fields} written with it."""
    channel = _text(channel)
    existing = booking_by_ref(cur, hotel_id, channel, channel_booking_ref)
    if existing and existing["booking_status"] in ("checked_in", "checked_out"):
        raise Refused("Booking " + existing["booking_id"] + " is " + existing["booking_status"] + "; the channel change is not applied")
    wanted, per_day = {}, {}
    for room_type_id, nights in lines:
        for stay_date, rate in nights:
            per_day[stay_date] = per_day.get(stay_date, 0) + 1
            priced = rate is not None and currency is not None
            wanted[(stay_date, per_day[stay_date])] = (room_type_id, rate if priced else None, "channel" if priced else None)
    values = (channel, _count(adults), _count(children), currency, gross_amount, commission_amount, commission_rate)
    if existing:
        booking_id = existing["booking_id"]
        cur.execute(
            """UPDATE booking SET booking_status = 'confirmed', cancelled_at = NULL, channel = %s, adults = %s, children = %s, currency = %s,
                      gross_amount = %s, commission_amount = %s, commission_rate = %s, updated_at = now(), updated_by_user_id = NULL
               WHERE booking_id = %s""",
            values + (booking_id,),
        )
        if existing["booking_status"] != "confirmed":
            _log(cur, hotel_id, booking_id, None, "booking_status", existing["booking_status"], "confirmed", channel + " delivery")
    else:
        booking_id = new_id("bkg")
        guest_id = ensure_guest(cur, hotel_id, None, **guest)
        cur.execute(
            """INSERT INTO booking (booking_id, hotel_id, guest_id, booking_status, channel_booking_ref, channel, adults, children, currency,
                                    gross_amount, commission_amount, commission_rate)
               VALUES (%s, %s, %s, 'confirmed', %s, %s, %s, %s, %s, %s, %s, %s)""",
            (booking_id, hotel_id, guest_id, channel_booking_ref) + values,
        )
    cur.execute("SELECT stay_date, room_seq, room_type_id FROM booking_room_night WHERE booking_id = %s AND night_status = ANY(%s) "
                "ORDER BY stay_date, room_seq", (booking_id, list(LIVE)))
    before = _layout(cur.fetchall())
    stay_dates = _write_nights(cur, hotel_id, booking_id, wanted, currency, check=False)
    after = _layout({"stay_date": day, "room_seq": seq, "room_type_id": wanted[(day, seq)][0]} for day, seq in sorted(wanted))
    if existing and before != after:
        _log(cur, hotel_id, booking_id, None, "nights", before, after, channel + " delivery")
    for collection, fields in (documents or {}).items():
        _write_document(cur, collection, hotel_id, booking_id, fields)
    return {"booking_id": booking_id, "stay_dates": stay_dates, "created": not existing}


# ---------- reads ----------

def stay_dates(hotel_id, booking_id):
    rows = db.rows("SELECT DISTINCT stay_date FROM booking_room_night WHERE hotel_id = %s AND booking_id = %s ORDER BY stay_date",
                   (hotel_id, booking_id))
    return [row["stay_date"].isoformat() for row in rows]


def read_recent_bookings(hotel_id, limit=50):
    """{'bookings': [...]} newest first, in the shape the bookings calendar reads."""
    rows = db.rows(
        """SELECT s.booking_id, s.booking_status, s.channel, s.group_name, s.booked_at, s.check_in_date, s.check_out_date, s.rooms_booked,
                  s.gross_amount, g.guest_name,
                  (SELECT identity_value FROM guest_identity i WHERE i.guest_id = s.guest_id AND i.identity_type = 'phone'
                   ORDER BY identity_value LIMIT 1) AS guest_phone,
                  (SELECT string_agg(DISTINCT t.room_type_name, ', ') FROM booking_room_night n JOIN room_type t ON t.room_type_id = n.room_type_id
                   WHERE n.booking_id = s.booking_id) AS room_type,
                  (SELECT string_agg(DISTINCT rm.room_number, ', ') FROM booking_room_night n JOIN room rm ON rm.room_id = n.room_id
                   WHERE n.booking_id = s.booking_id) AS room_number
           FROM booking_summary s JOIN guest g ON g.guest_id = s.guest_id
           WHERE s.hotel_id = %s ORDER BY s.booked_at DESC LIMIT %s""",
        (hotel_id, int(limit)),
    )
    return {"bookings": [{
        "id": row["booking_id"], "guest_name": row["guest_name"], "guest_phone": row["guest_phone"] or "",
        "room_number": row["room_number"] or "", "room_type": row["room_type"] or "", "check_in": row["check_in_date"].isoformat(),
        "check_out": row["check_out_date"].isoformat(), "status": row["booking_status"], "channel": row["channel"],
        "group_name": row["group_name"] or "", "amount": float(row["gross_amount"] or 0), "rooms_booked": int(row["rooms_booked"]),
        "timestamp": row["booked_at"].isoformat(),
    } for row in rows]}


# `from booking_system.booking_manager import booking_manager` keeps working: the name is this module.
booking_manager = sys.modules[__name__]
