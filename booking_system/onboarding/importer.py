"""Data migration from another PMS's CSV/XLSX exports into a batch (imb_): upload -> the model proposes the file's entity, its column
mapping to the LEON fields, the date format of each date column and the translation of each coded value (a status 'In House', a role
'FO Supervisor') to a LEON value; all shown for confirmation and editable -> preview (row counts, errors, duplicates by phone/email
identity, room number, channel reference or old-system id, against the hotel and within the batch) -> commit: every valid row of every
file in one transaction, in dependency order (room types, rooms, guests, bookings, payments; accounts on their own), with each created
row recorded -> rollback removes exactly those rows, newest first (finance postings are reversed, never deleted).

Files link to each other the way the old system did: a booking names its guest, room type and room by the old system's id or by name,
a payment names its booking by the old id or the channel reference. Each imported row keeps its old id as an external_ref
(import_external_ref), never as its LEON id.

ENTITIES is data: the fields of each entity (type, required, what the model is told), the module and access an import needs, the
setting that must be on (staff only with HR, outlets and menu only with F&B), and the per-entity key, check and writer."""

import inspect
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import psycopg2
from psycopg2.extras import Json, execute_values

from booking_system import booking_manager, core_refs, db, inventory_manager, room_manager
from booking_system.access import RANK
from booking_system.content_store import client, document_key
from booking_system.core_schema import MODULES
from booking_system.guest_manager import Refused, ensure_guest, find_guest, identities
from booking_system.hotel_registry import new_id, read_hotel_by_id
from booking_system.onboarding import files, invites, llm

CENT = Decimal("0.01")
FORMATS = ("date_format", "phone_has_country_code", "phone_country_code", "phone_trunk_prefix")
SAMPLE_ROWS = 8
CODED_VALUES = 25
ERRORS_SHOWN = 60
STATUSES = ("confirmed", "checked_in", "checked_out", "cancelled", "no_show")
NIGHT_STATUS = {"confirmed": "booked", "checked_in": "checked_in", "checked_out": "checked_out", "cancelled": "cancelled", "no_show": "no_show"}
LIVE_STATUSES = ("confirmed", "checked_in")
ROOMED_STATUSES = ("checked_in", "checked_out")
account_profiles = client["leon_finance"]["ar_account_profile"]


class Invalid(Exception):
    pass


# ---------- field types: text -> stored value, or Invalid ----------

def _name(text, mapping, field):
    return " ".join(text.split()).upper()


def _plain(text, mapping, field):
    return " ".join(text.split())


def _ref(text, mapping, field):
    return " ".join(text.split()).upper()[:128]


def _amount(text, mapping, field):
    """Currency signs, spaces and thousands commas are dropped: 'RM 1,200.50' is 1200.50."""
    kept = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
    try:
        amount = Decimal(kept)
    except InvalidOperation:
        raise Invalid("is not an amount: " + text)
    if amount < 0:
        raise Invalid("is negative: " + text)
    return amount.quantize(CENT)


def _integer(text, mapping, field):
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise Invalid("is not a whole number: " + text)
    if number != number.to_integral_value() or number < 0:
        raise Invalid("is not a whole number: " + text)
    return int(number)


def _date(text, mapping, field):
    pattern = str(field.get("format") or mapping.get("date_format") or "")
    if not pattern:
        raise Invalid("needs the column's date format in the mapping")
    try:
        return datetime.strptime(text, pattern).date()
    except ValueError:
        raise Invalid("does not match the date format " + pattern + ": " + text)


def _phone(text, mapping, field):
    digits = "".join(ch for ch in text if ch.isdigit())
    international = text.strip().startswith("+") or bool(mapping.get("phone_has_country_code"))
    trunk = "".join(ch for ch in str(mapping.get("phone_trunk_prefix") or "") if ch.isdigit())
    local = digits[len(trunk):] if trunk and digits.startswith(trunk) else digits
    code = "".join(ch for ch in str(mapping.get("phone_country_code") or "") if ch.isdigit())
    if not international and not code:
        raise Invalid("has no country code and the mapping names none: " + text)
    stored = "+" + (digits if international else code + local)
    if not 8 <= len(stored) <= 16:
        raise Invalid("is not a phone number: " + text)
    return stored


def _email(text, mapping, field):
    stored = text.strip().lower()
    local, _, domain = stored.partition("@")
    if not local or "@" in domain or "." not in domain.strip(".") or " " in stored:
        raise Invalid("is not an email address: " + text)
    return stored


def _choice(text, mapping, field):
    stored = "_".join(text.lower().split())
    if stored not in field["choices"]:
        raise Invalid("must be one of " + ", ".join(field["choices"]) + ": " + text)
    return stored


def _access(text, mapping, field):
    """'front_desk:manage, hr:view' -> {module: level}."""
    found = {}
    for part in text.split(","):
        module, _, level = part.strip().partition(":")
        if module.strip() not in MODULES or level.strip() not in RANK:
            raise Invalid("is not module:level pairs over " + ", ".join(MODULES) + " and " + ", ".join(RANK) + ": " + text)
        found[module.strip()] = level.strip()
    return found


TYPES = {"name": _name, "text": _plain, "ref": _ref, "amount": _amount, "integer": _integer, "date": _date, "phone": _phone,
         "email": _email, "choice": _choice, "access": _access}


# ---------- shared lookups ----------

def _one(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchone()


def _known(cur, ctx, entity, ref):
    """The LEON id an earlier import gave this old-system id, or None."""
    found = _one(cur, "SELECT entity_id FROM import_external_ref WHERE hotel_id = %s AND entity = %s AND external_ref = %s",
                 (ctx["hotel_id"], entity, ref)) if ref else None
    return found["entity_id"] if found else None


def _in_batch(ctx, entity, field, value):
    return next((row for row in ctx["batch"].get(entity, []) if value and row.get(field) == value), None)


def _keep_ref(cur, ctx, entity, values, entity_id):
    if values.get("external_ref"):
        cur.execute("INSERT INTO import_external_ref (hotel_id, entity, external_ref, entity_id, batch_id) VALUES (%s, %s, %s, %s, %s)",
                    (ctx["hotel_id"], entity, values["external_ref"], entity_id, ctx["batch_id"]))


def _ref_key(values):
    return [("ref", values["external_ref"])] if values.get("external_ref") else []


def _ref_exists(entity):
    def run(cur, ctx, key):
        return bool(_known(cur, ctx, entity, key[1]))
    return run


def _local(day, clock):
    """(day + HH:MM) as a local timestamp, read in the hotel's time zone by the SQL."""
    return datetime.combine(day, datetime.strptime(clock, "%H:%M").time()).isoformat()


# ---------- room types and rooms ----------

def _type_name(cur, ctx, value):
    """A room type named by the old system's id (in this batch or an earlier one) or by its name."""
    row = _in_batch(ctx, "room_types", "external_ref", value)
    if row:
        return row["room_type"]
    known = _known(cur, ctx, "room_types", value)
    if known:
        return _one(cur, "SELECT room_type_name FROM room_type WHERE room_type_id = %s", (known,))["room_type_name"]
    return value


def _type_known(cur, ctx, name):
    priced = {row["room_type"] for row in ctx["batch"].get("rooms", []) if row.get("base_price") is not None}
    return name in {row["room_type"] for row in ctx["batch"].get("room_types", [])} | priced or bool(core_refs.room_type_id(cur, ctx["hotel_id"], name))


def _room_type_key(values):
    return [("type", values["room_type"])] + _ref_key(values)


def _room_type_exists(cur, ctx, key):
    return bool(core_refs.room_type_id(cur, ctx["hotel_id"], key[1])) if key[0] == "type" else bool(_known(cur, ctx, "room_types", key[1]))


def _new_type(cur, ctx, name, price):
    hotel_id, user_id = ctx["hotel_id"], ctx["user_id"]
    type_id = new_id("rt")
    cur.execute("INSERT INTO room_type (room_type_id, hotel_id, room_type_name, status) VALUES (%s, %s, %s, 'active')", (type_id, hotel_id, name))
    ctx["record"]("room_type", type_id)
    inventory_manager.seed_nights(cur, hotel_id, user_id, type_id, ctx["today"], price)
    key, body = room_manager.TYPE_DOCS.staged_insert({"hotel_id": hotel_id, "room_type_id": type_id, "specifications": {},
                                                      "description": "", "updated_by_user_id": user_id})
    room_manager.TYPE_DOCS.write_in(cur, key, body)
    ctx["record"]("content_document", key, room_manager.TYPE_DOCS)
    return type_id


def _write_room_types(cur, ctx, rows):
    for values in rows:
        ctx["at"] = values["_row"]
        _keep_ref(cur, ctx, "room_types", values, _new_type(cur, ctx, values["room_type"], values["base_price"]))
    return len(rows)


def _room_number(cur, ctx, value):
    row = _in_batch(ctx, "rooms", "external_ref", value)
    if row:
        return row["room_number"]
    known = _known(cur, ctx, "rooms", value)
    if known:
        return _one(cur, "SELECT room_number FROM room WHERE room_id = %s", (known,))["room_number"]
    return value


def _room_key(values):
    return [("room", values["room_number"])] + _ref_key(values)


def _room_exists(cur, ctx, key):
    if key[0] == "ref":
        return bool(_known(cur, ctx, "rooms", key[1]))
    return bool(_one(cur, "SELECT 1 FROM room WHERE hotel_id = %s AND room_number = %s", (ctx["hotel_id"], key[1])))


def _room_check(cur, ctx, values):
    values["room_type"] = _type_name(cur, ctx, values["room_type"])
    known = _type_known(cur, ctx, values["room_type"])
    return [] if known or values.get("base_price") is not None else [("base_price", "missing", "a new room type needs its nightly price")]


def _write_rooms(cur, ctx, rows):
    hotel_id, user_id = ctx["hotel_id"], ctx["user_id"]
    grouped = {}
    for values in rows:
        grouped.setdefault(values["room_type"], []).append(values)
    for name, group in grouped.items():
        ctx["at"] = group[0]["_row"]
        numbers = [values["room_number"] for values in group]
        type_id = core_refs.room_type_id(cur, hotel_id, name)
        created = not type_id
        if created:
            type_id = _new_type(cur, ctx, name, next(values["base_price"] for values in group if values.get("base_price") is not None))
        else:
            ctx["record"]("room_type_recount", type_id)
        room_manager._insert_rooms(cur, hotel_id, user_id, type_id, numbers)
        cur.execute("SELECT room_id, room_number FROM room WHERE hotel_id = %s AND room_number = ANY(%s)", (hotel_id, numbers))
        ids = {row["room_number"]: row["room_id"] for row in cur.fetchall()}
        for values in group:
            ctx["record"]("room", ids[values["room_number"]])
            _keep_ref(cur, ctx, "rooms", values, ids[values["room_number"]])
        inventory_manager.apply_room_count(cur, hotel_id, user_id, type_id, ctx["today"])
    return len(rows)


# ---------- guests ----------

def _identity_keys(values):
    return [(kind, values[kind]) for kind in ("phone", "email") if values.get(kind)]


def _guest_key(values):
    return _identity_keys(values) + _ref_key(values)


def _guest_exists(cur, ctx, key):
    if key[0] == "ref":
        return bool(_known(cur, ctx, "guests", key[1]))
    return bool(core_refs.guest_by_identity(cur, ctx["hotel_id"], key[0], key[1]))


def _guest_check(cur, ctx, values):
    return [] if _guest_key(values) else [("phone", "missing", "a guest needs a phone, an email or the old system's guest id")]


def _guest_of(cur, ctx, values):
    """The guest_id for these values; a guest the import creates is recorded."""
    pairs = identities(values.get("phone"), values.get("email"))
    known = find_guest(cur, ctx["hotel_id"], pairs) if pairs else None
    if known:
        return known
    guest_id = ensure_guest(cur, ctx["hotel_id"], ctx["user_id"], values["guest_name"], values.get("phone"), values.get("email"),
                            values.get("nationality"))
    ctx["record"]("guest", guest_id)
    return guest_id


def _write_guests(cur, ctx, rows):
    for values in rows:
        ctx["at"] = values["_row"]
        _keep_ref(cur, ctx, "guests", values, _guest_of(cur, ctx, values))
    return len(rows)


# ---------- bookings: upcoming, in house and history ----------

def _booking_key(values):
    keys = _ref_key(values)
    if values.get("channel_booking_ref"):
        keys.append(("channel", values.get("channel") or BOOKING_DEFAULTS["channel"], values["channel_booking_ref"]))
    who = values.get("phone") or values.get("email") or values.get("guest_name") or values.get("guest_ref")
    return keys or [("stay", who, values["check_in"].isoformat(), values["room_type"])]


def _booking_exists(cur, ctx, key):
    if key[0] == "ref":
        return bool(_known(cur, ctx, "bookings", key[1]))
    if key[0] == "channel":
        return bool(_one(cur, "SELECT 1 FROM booking WHERE hotel_id = %s AND channel = %s AND channel_booking_ref = %s", (ctx["hotel_id"], key[1], key[2])))
    return bool(_one(cur, """SELECT 1 FROM booking_summary s JOIN guest g ON g.guest_id = s.guest_id
                             JOIN booking_room_night n ON n.booking_id = s.booking_id AND n.stay_date = s.check_in_date
                             JOIN room_type t ON t.room_type_id = n.room_type_id
                             WHERE s.hotel_id = %s AND s.check_in_date = %s AND t.room_type_name = %s AND s.booking_status <> 'cancelled'
                               AND (g.guest_name = %s OR EXISTS (SELECT 1 FROM guest_identity i WHERE i.guest_id = g.guest_id
                                                                 AND i.identity_value = %s))""",
                     (ctx["hotel_id"], key[2], key[3], key[1], key[1])))


def _stay_days(values):
    return [values["check_in"] + timedelta(days=offset) for offset in range((values["check_out"] - values["check_in"]).days)]


# What each status says about the dates, against the hotel's today.
STATUS_DATES = {
    "confirmed": (lambda v, today: v["check_in"] >= today, "is confirmed but arrives {check_in}, before today; give it checked_out, cancelled or no_show"),
    "checked_in": (lambda v, today: v["check_in"] <= today < v["check_out"], "is in house but its stay {check_in} to {check_out} does not include today"),
    "checked_out": (lambda v, today: v["check_out"] <= today, "is checked out but departs {check_out}, after today"),
    "cancelled": (lambda v, today: True, ""),
    "no_show": (lambda v, today: v["check_in"] <= today, "is a no-show but arrives {check_in}, after today"),
}


def _room_of(cur, ctx, number):
    """(room_type_name or None) of a room in this batch or at the hotel."""
    row = _in_batch(ctx, "rooms", "room_number", number)
    if row:
        return row["room_type"]
    found = core_refs.room(cur, ctx["hotel_id"], number)
    return found["room_type_name"] if found else None


def _guest_ref_found(cur, ctx, ref):
    return bool(_in_batch(ctx, "guests", "external_ref", ref) or _known(cur, ctx, "guests", ref))


def _booking_check(cur, ctx, values):
    found = []
    values["room_type"] = _type_name(cur, ctx, values["room_type"])
    if values.get("room_number"):
        values["room_number"] = _room_number(cur, ctx, values["room_number"])
    if values.get("guest_ref") and not _guest_ref_found(cur, ctx, values["guest_ref"]):
        found.append(("guest_ref", "invalid", "guest " + values["guest_ref"] + " is neither at the hotel nor in this import"))
    if not values.get("guest_ref") and not values.get("guest_name"):
        found.append(("guest_name", "missing", "a booking needs the guest's name or the old system's guest id"))
    if not _type_known(cur, ctx, values["room_type"]):
        found.append(("room_type", "invalid", "room type " + values["room_type"] + " is neither at the hotel nor in this import"))
    if values["check_out"] <= values["check_in"]:
        return found + [("check_out", "invalid", "check_out must be after check_in")]
    holds, message = STATUS_DATES[values["status"]]
    if not holds(values, ctx["today"]):
        found.append(("status", "invalid", "booking " + message.format(check_in=values["check_in"], check_out=values["check_out"])))
    if values["status"] == "cancelled" and not values.get("cancelled_on"):
        found.append(("cancelled_on", "missing", "a cancelled booking needs the date it was cancelled"))
    if values["status"] in ROOMED_STATUSES and not values.get("room_number"):
        found.append(("room_number", "missing", "a booking that was in house needs its room"))
    if values["status"] in ROOMED_STATUSES and not (ctx["values"].get("front_desk.checkin_time") and ctx["values"].get("front_desk.checkout_time")):
        found.append(("status", "invalid", "the hotel's check-in and check-out times are not set yet; they date the stay"))
    if values.get("room_number"):
        room_type = _room_of(cur, ctx, values["room_number"])
        if room_type is None:
            found.append(("room_number", "invalid", "room " + values["room_number"] + " is neither at the hotel nor in this import"))
        elif room_type != values["room_type"]:
            found.append(("room_number", "invalid", "room " + values["room_number"] + " is a " + room_type + ", not a " + values["room_type"]))
    days = _stay_days(values)
    if days[0] < ctx["today"] and values.get("nightly_rate") is None and values.get("total_amount") is None:
        found.append(("nightly_rate", "missing", "a past stay needs its nightly rate or total amount; there is no price list for past nights"))
    if values.get("room_number") and values["status"] in LIVE_STATUSES and not found:
        taken = ctx.setdefault("room_nights", set())
        nights = {(values["room_number"], day) for day in days}
        clash = sorted(day.isoformat() for room, day in nights & taken) or [
            row["stay_date"].isoformat() for row in _clashes(cur, ctx, values["room_number"], days)]
        if clash:
            found.append(("room_number", "invalid", "room " + values["room_number"] + " is already booked on " + ", ".join(clash)))
    return found


def _hold_none(ctx, values):
    return None


def _hold_booking(ctx, values):
    """Room nights of a row that will be written, so a later row in the batch can see them. A rejected row holds nothing."""
    if values.get("room_number") and values["status"] in LIVE_STATUSES:
        ctx.setdefault("room_nights", set()).update((values["room_number"], day) for day in _stay_days(values))


def _clashes(cur, ctx, number, days):
    cur.execute("""SELECT n.stay_date FROM booking_room_night n JOIN room r ON r.room_id = n.room_id
                   WHERE r.hotel_id = %s AND r.room_number = %s AND n.stay_date = ANY(%s) AND n.night_status IN ('booked','checked_in')""",
                (ctx["hotel_id"], number, days))
    return cur.fetchall()


# create_booking's own defaults for what a row leaves empty.
BOOKING_DEFAULTS = {name: item.default for name, item in inspect.signature(booking_manager.create_booking).parameters.items()
                    if item.default is not inspect.Parameter.empty}


def _rates(cur, values, type_id, days):
    """{day: (sell_rate, rate_source)}: the row's nightly rate, its total split over the nights, or the price list for upcoming nights."""
    if values.get("nightly_rate") is not None:
        return {day: (values["nightly_rate"], "override") for day in days}
    if values.get("total_amount") is not None:
        share = (values["total_amount"] / len(days)).quantize(CENT)
        last = values["total_amount"] - share * (len(days) - 1)
        return {day: (last if index == len(days) - 1 else share, "override") for index, day in enumerate(days)}
    return {day: booking_manager._rate(cur, type_id, day, None) for day in days}


def _stamp(day, ctx):
    return datetime.combine(day, datetime.min.time()).isoformat() if day else None


def _write_booking(cur, ctx, values):
    hotel_id, user_id = ctx["hotel_id"], ctx["user_id"]
    guest_id = _known(cur, ctx, "guests", values.get("guest_ref")) or _guest_of(cur, ctx, values)
    type_id = core_refs.room_type_id(cur, hotel_id, values["room_type"])
    room = core_refs.room(cur, hotel_id, values["room_number"]) if values.get("room_number") else None
    days = _stay_days(values)
    if values["status"] in LIVE_STATUSES:
        booking_manager._check_available(cur, hotel_id, {(type_id, day): 1 for day in days if day >= ctx["today"]})
    rates = _rates(cur, values, type_id, days)
    given = {name: values.get(name) if values.get(name) is not None else BOOKING_DEFAULTS[name] for name in ("adults", "children", "channel")}
    booking_id = new_id("bkg")
    cur.execute(
        """INSERT INTO booking (booking_id, hotel_id, guest_id, booking_status, channel, channel_booking_ref, adults, children, currency,
                                gross_amount, booked_at, cancelled_at, created_by_user_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamp AT TIME ZONE %s, now()), %s::timestamp AT TIME ZONE %s, %s)""",
        (booking_id, hotel_id, guest_id, values["status"], str(given["channel"]).strip().upper(), values.get("channel_booking_ref"),
         given["adults"], given["children"], ctx["currency"], sum(rate for rate, _source in rates.values()),
         _stamp(values.get("booked_on"), ctx), ctx["timezone"], _stamp(values.get("cancelled_on"), ctx), ctx["timezone"], user_id),
    )
    booking_manager._write_nights(cur, hotel_id, booking_id, {(day, 1): (type_id,) + rates[day] for day in days}, ctx["currency"], check=False)
    roomed = values["status"] in ROOMED_STATUSES
    out = values["status"] == "checked_out"
    arrived = _local(values["check_in"], ctx["values"]["front_desk.checkin_time"]) if roomed else None
    left = _local(values["check_out"], ctx["values"]["front_desk.checkout_time"]) if out else None
    cur.execute(
        """UPDATE booking_room_night SET night_status = %s, room_id = %s, assigned_at = CASE WHEN %s::varchar IS NULL THEN NULL ELSE now() END,
                  assigned_by_user_id = CASE WHEN %s::varchar IS NULL THEN NULL ELSE %s END,
                  checked_in_at = CASE WHEN %s THEN %s::timestamp AT TIME ZONE %s END,
                  checked_out_at = CASE WHEN %s THEN %s::timestamp AT TIME ZONE %s END
           WHERE booking_id = %s""",
        (NIGHT_STATUS[values["status"]], room and room["room_id"], room and room["room_id"], room and room["room_id"], user_id,
         roomed, arrived, ctx["timezone"], out, left, ctx["timezone"], booking_id),
    )
    ctx["record"]("booking", booking_id)
    if values.get("notes"):
        booking_manager._write_document(cur, booking_manager.booking_docs, hotel_id, booking_id, {"booking_notes": values["notes"]})
        ctx["record"]("content_document", document_key({"hotel_id": hotel_id, "booking_id": booking_id}), booking_manager.booking_docs)
    _keep_ref(cur, ctx, "bookings", values, booking_id)
    return type_id, days


def _past_inventory(cur, ctx, past):
    """Past nights of the imported stays get inventory rows (the room count), so occupancy reports read the history."""
    for type_id, days in past.items():
        total = inventory_manager.count_rooms(cur, ctx["hotel_id"], type_id)
        span = [min(days) + timedelta(days=offset) for offset in range((ctx["today"] - min(days)).days)]
        cur.execute("""INSERT INTO room_inventory_nightly (hotel_id, room_type_id, stay_date, total_inventory, blocked_quantity, updated_by_user_id)
                       SELECT %s, %s, d, %s, 0, %s FROM unnest(%s::date[]) AS d ON CONFLICT (room_type_id, stay_date) DO NOTHING RETURNING stay_date""",
                    (ctx["hotel_id"], type_id, total, ctx["user_id"], span))
        for row in cur.fetchall():
            ctx["record"]("room_inventory_nightly", type_id + "/" + row["stay_date"].isoformat())


def _write_bookings(cur, ctx, rows):
    past = {}
    for values in rows:
        ctx["at"] = values["_row"]
        type_id, days = _write_booking(cur, ctx, values)
        past.setdefault(type_id, []).extend(day for day in days if day < ctx["today"])
    _past_inventory(cur, ctx, {type_id: days for type_id, days in past.items() if days})
    return len(rows)


# ---------- payments and deposits ----------

def _payment_key(values):
    return _ref_key(values) or [("paid", values["booking_ref"], values["paid_on"].isoformat(), str(values["amount"]), values["method"], values["kind"])]


def _booking_named(cur, ctx, ref):
    """booking_id named by the old system's booking id or its channel reference, at the hotel."""
    found = _known(cur, ctx, "bookings", ref)
    if found:
        return found
    row = _one(cur, "SELECT booking_id FROM booking WHERE hotel_id = %s AND upper(channel_booking_ref) = %s", (ctx["hotel_id"], ref))
    return row["booking_id"] if row else None


def _payment_exists(cur, ctx, key):
    if key[0] == "ref":
        return bool(_known(cur, ctx, "payments", key[1]))
    booking_id = _booking_named(cur, ctx, key[1])
    return bool(booking_id and _one(cur, """SELECT 1 FROM payment WHERE booking_id = %s AND (paid_at AT TIME ZONE %s)::date = %s AND amount = %s
                                           AND payment_method = %s AND payment_kind = %s""",
                                    (booking_id, ctx["timezone"], key[2], key[3], key[4], key[5])))


def _payment_check(cur, ctx, values):
    batch = _in_batch(ctx, "bookings", "external_ref", values["booking_ref"]) or _in_batch(ctx, "bookings", "channel_booking_ref", values["booking_ref"])
    found = [] if batch or _booking_named(cur, ctx, values["booking_ref"]) else [
        ("booking_ref", "invalid", "booking " + values["booking_ref"] + " is neither at the hotel nor in this import")]
    return found + ([] if values["amount"] > 0 else [("amount", "invalid", "a payment amount must be more than zero")])


def _write_payments(cur, ctx, rows):
    for values in rows:
        ctx["at"] = values["_row"]
        payment_id = new_id("pay")
        cur.execute("""INSERT INTO payment (payment_id, hotel_id, booking_id, payment_kind, amount, currency, payment_method, payment_status,
                                            reference, paid_at, recorded_by_user_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'received', %s, %s::timestamp AT TIME ZONE %s, %s)""",
                    (payment_id, ctx["hotel_id"], _booking_named(cur, ctx, values["booking_ref"]), values["kind"], values["amount"], ctx["currency"],
                     values["method"], values.get("reference"), _stamp(values["paid_on"], ctx), ctx["timezone"], ctx["user_id"]))
        ctx["record"]("payment", payment_id)
        _keep_ref(cur, ctx, "payments", values, payment_id)
    return len(rows)


# ---------- staff accounts: invited, never with a password ----------

def _person_of(cur, values):
    """The user_ids already holding these identities."""
    from booking_system.user_manager import find_user
    return {found["user_id"] for found in (find_user(cur, kind, value) for kind, value in _identity_keys(values)) if found}


def _account_key(values):
    return _identity_keys(values) + _ref_key(values)


def _account_exists(cur, ctx, key):
    if key[0] == "ref":
        return bool(_known(cur, ctx, "user_accounts", key[1]))
    return bool(_one(cur, """SELECT 1 FROM user_identity i JOIN hotel_user u ON u.user_id = i.user_id
                             WHERE u.hotel_id = %s AND i.identity_type = %s AND i.identity_value = %s""", (ctx["hotel_id"], key[0], key[1])))


def _roles(cur, hotel_id):
    cur.execute("SELECT role_name, role_id FROM access_role WHERE hotel_id = %s ORDER BY role_name", (hotel_id,))
    return {row["role_name"]: row["role_id"] for row in cur.fetchall()}


def _account_check(cur, ctx, values):
    found = [] if _identity_keys(values) else [("phone", "missing", "an invite needs the person's phone or email")]
    if values["role"] not in _roles(cur, ctx["hotel_id"]):
        found.append(("role", "invalid", "role " + values["role"] + " is not one of " + ", ".join(_roles(cur, ctx["hotel_id"]))))
    return found + ([("email", "invalid", "the phone and the email belong to two different people")] if len(_person_of(cur, values)) > 1 else [])


def _write_accounts(cur, ctx, rows):
    from booking_system.user_manager import profile_col
    roles = _roles(cur, ctx["hotel_id"])
    for values in rows:
        ctx["at"] = values["_row"]
        user_id = next(iter(_person_of(cur, values)), None)
        if not user_id:
            user_id = new_id("usr")
            cur.execute("INSERT INTO user_account (user_id, user_name, status) VALUES (%s, %s, 'active')", (user_id, values["user_name"]))
            ctx["record"]("user_account", user_id)
            for kind, value in _identity_keys(values):
                cur.execute("INSERT INTO user_identity (identity_id, user_id, identity_type, identity_value) VALUES (%s, %s, %s, %s)",
                            (new_id("uid"), user_id, kind, value))
            key, body = profile_col.staged_insert({"user_id": user_id, "primary_language": None, "nationality": None})
            profile_col.write_in(cur, key, body)
            ctx["record"]("content_document", key, profile_col)
        cur.execute("""INSERT INTO hotel_user (hotel_id, user_id, role_id, job_title, status, invited_by_user_id)
                       VALUES (%s, %s, %s, %s, 'invited', %s)""", (ctx["hotel_id"], user_id, roles[values["role"]], values.get("job_title"), ctx["user_id"]))
        ctx["record"]("hotel_user", user_id)
        for module, level in (values.get("module_access") or {}).items():
            cur.execute("""INSERT INTO hotel_user_module (hotel_id, user_id, module_name, access_level, granted_by_user_id)
                           VALUES (%s, %s, %s, %s, %s)""", (ctx["hotel_id"], user_id, module, level, ctx["user_id"]))
        ctx["record"]("onboarding_invite", invites.create_in(cur, ctx["hotel_id"], user_id, ctx["user_id"], ctx["batch_id"]))
        _keep_ref(cur, ctx, "user_accounts", values, user_id)
    return len(rows)


# ---------- company / city-ledger accounts ----------

def _company_key(values):
    return [("account", values["account_name"])] + _ref_key(values)


def _company_exists(cur, ctx, key):
    if key[0] == "ref":
        return bool(_known(cur, ctx, "company_accounts", key[1]))
    return bool(_one(cur, "SELECT 1 FROM ar_account WHERE hotel_id = %s AND account_name = %s", (ctx["hotel_id"], key[1])))


def _charge_code(ctx, code):
    from booking_system.finance import service as finance
    if code not in ctx["codes"]:
        found = finance.resolve_transaction_code(ctx["hotel_id"], code)
        row = found["transaction_code"] if found["ok"] else {}
        ctx["codes"][code] = row if row.get("transaction_type") == "charge" else None
    return ctx["codes"][code]


def _company_check(cur, ctx, values):
    if not values.get("opening_balance"):
        return []
    from booking_system.finance import service as finance
    if ctx["values"].get("finance.enabled") is not True:
        return [("opening_balance", "invalid", "an opening balance posts through Finance, which is off; switch Finance on or leave the balance out")]
    if not (finance.read_business_day(ctx["hotel_id"]).get("open")):
        return [("opening_balance", "invalid", "Finance has no open business date to post the opening balance on")]
    try:
        finance._setup(cur, ctx["hotel_id"], finance._open_day(cur, ctx["hotel_id"])["business_date"])
    except finance.Refused as reason:
        return [("opening_balance", "invalid", str(reason))]
    code = _charge_code(ctx, values.get("opening_charge_code") or "")
    found = [] if code else [("opening_charge_code", "invalid", "the opening balance needs a finance charge code of the hotel")]
    found += [("opening_charge_code", "invalid", "the opening balance code must not carry tax")] if code and code.get("is_taxable") else []
    over = values.get("credit_limit") is not None and values["opening_balance"] > values["credit_limit"]
    return found + ([("opening_balance", "invalid", "the opening balance is above the credit limit")] if over else [])


def _write_companies(cur, ctx, rows):
    from booking_system.finance import service as finance
    for values in rows:
        ctx["at"] = values["_row"]
        account_id = new_id("ara")
        cur.execute("""INSERT INTO ar_account (ar_account_id, hotel_id, account_name, account_type, currency, credit_limit, payment_terms_days, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'active')""",
                    (account_id, ctx["hotel_id"], values["account_name"], values["account_type"], ctx["currency"], values["credit_limit"],
                     values["payment_terms_days"]))
        ctx["record"]("ar_account", account_id)
        contact = {key: values.get(key) for key in ("contact_name", "contact_email", "contact_phone") if values.get(key)}
        if contact:
            account_profiles.write_in(cur, account_id, dict(contact, _id=account_id, ar_account_id=account_id, hotel_id=ctx["hotel_id"]))
            ctx["record"]("content_document", account_id, account_profiles)
        if values.get("opening_balance"):
            posted = finance.post_charge_in(cur, ctx["hotel_id"], "account", account_id, values["opening_charge_code"], values["opening_balance"],
                                            "migration", ctx["batch_id"] + ":" + account_id, system="migration", ar_account_id=account_id,
                                            reference="opening balance")
            if not posted["ok"]:
                raise Refused("opening balance of " + values["account_name"] + ": " + posted["error"])
            ctx["record"]("finance_posting", posted["posting"]["posting_id"])
        _keep_ref(cur, ctx, "company_accounts", values, account_id)
    return len(rows)


# ---------- HR staff records ----------

def _staff_key(values):
    return _identity_keys(values) + _ref_key(values)


def _staff_exists(cur, ctx, key):
    if key[0] == "ref":
        return bool(_known(cur, ctx, "staff", key[1]))
    return bool(_one(cur, "SELECT 1 FROM staff_identity WHERE hotel_id = %s AND identity_type = %s AND identity_value = %s",
                     (ctx["hotel_id"], key[0], key[1])))


def _department(cur, ctx, name):
    found = _one(cur, "SELECT department_id FROM department WHERE hotel_id = %s AND department_name = %s", (ctx["hotel_id"], name))
    return found["department_id"] if found else None


def _staff_check(cur, ctx, values):
    found = [] if _department(cur, ctx, values["department"]) else [("department", "invalid", "department " + values["department"] + " is not set up")]
    return found + ([] if _identity_keys(values) else [("phone", "missing", "a staff member needs a phone or an email")])


def _write_staff(cur, ctx, rows):
    for values in rows:
        ctx["at"] = values["_row"]
        staff_id = new_id("stf")
        cur.execute("""INSERT INTO staff (staff_id, hotel_id, staff_name, department_id, employment_type, status, hired_on)
                       VALUES (%s, %s, %s, %s, %s, 'active', %s)""",
                    (staff_id, ctx["hotel_id"], values["staff_name"], _department(cur, ctx, values["department"]), values["employment_type"],
                     values.get("hired_on")))
        for kind, value in _identity_keys(values):
            cur.execute("INSERT INTO staff_identity (staff_identity_id, hotel_id, staff_id, identity_type, identity_value) VALUES (%s, %s, %s, %s, %s)",
                        (new_id("sid"), ctx["hotel_id"], staff_id, kind, value))
        ctx["record"]("staff", staff_id)
        _keep_ref(cur, ctx, "staff", values, staff_id)
    return len(rows)


# ---------- F&B outlets and menu ----------

def _outlet_key(values):
    return [("outlet", values["outlet_name"])]


def _outlet_exists(cur, ctx, key):
    return bool(_one(cur, "SELECT 1 FROM outlet WHERE hotel_id = %s AND outlet_name = %s", (ctx["hotel_id"], key[1])))


def _write_outlets(cur, ctx, rows):
    for values in rows:
        ctx["at"] = values["_row"]
        outlet_id = new_id("out")
        cur.execute("INSERT INTO outlet (outlet_id, hotel_id, outlet_name, outlet_type, status) VALUES (%s, %s, %s, %s, 'active')",
                    (outlet_id, ctx["hotel_id"], values["outlet_name"], values["outlet_type"]))
        ctx["record"]("outlet", outlet_id)
    return len(rows)


def _item_key(values):
    return [("item", values["outlet_name"], values["item_name"])]


def _item_exists(cur, ctx, key):
    return bool(_one(cur, "SELECT 1 FROM fnb_menu_item i JOIN outlet o ON o.outlet_id = i.outlet_id WHERE o.hotel_id = %s AND o.outlet_name = %s "
                          "AND i.item_name = %s", (ctx["hotel_id"], key[1], key[2])))


def _item_check(cur, ctx, values):
    found = []
    outlets = {row["outlet_name"] for row in ctx["batch"].get("outlets", [])}
    if values["outlet_name"] not in outlets and not _outlet_exists(cur, ctx, ("outlet", values["outlet_name"])):
        found.append(("outlet_name", "invalid", "outlet " + values["outlet_name"] + " is neither at the hotel nor in this import"))
    if not _charge_code(ctx, values["transaction_code"]):
        found.append(("transaction_code", "invalid", values["transaction_code"] + " is not a finance charge code of the hotel"))
    return found


def _write_items(cur, ctx, rows):
    for values in rows:
        ctx["at"] = values["_row"]
        outlet = _one(cur, "SELECT outlet_id FROM outlet WHERE hotel_id = %s AND outlet_name = %s", (ctx["hotel_id"], values["outlet_name"]))
        item_id = new_id("fmi")
        cur.execute("""INSERT INTO fnb_menu_item (item_id, hotel_id, outlet_id, item_name, price, transaction_code_id, status)
                       VALUES (%s, %s, %s, %s, %s, %s, 'active')""",
                    (item_id, ctx["hotel_id"], outlet["outlet_id"], values["item_name"], values["price"],
                     _charge_code(ctx, values["transaction_code"])["transaction_code_id"]))
        ctx["record"]("fnb_menu_item", item_id)
    return len(rows)


def _none(cur, ctx, values):
    return []


def _field(kind, required, about, choices=(), choices_from=""):
    return {"type": kind, "required": required, "about": about, "choices": list(choices), "choices_from": choices_from}


_OLD_ID = "the old system's id for this row, kept so other files can name it"
ENTITIES = {
    "room_types": {
        "label": "Room types", "access": ("front_desk", "manage"), "requires": "",
        "fields": {"external_ref": _field("ref", False, _OLD_ID), "room_type": _field("name", True, "the room type's name"),
                   "base_price": _field("amount", True, "the room type's standard nightly price")},
        "key": _room_type_key, "exists": _room_type_exists, "check": _none, "write": _write_room_types,
    },
    "rooms": {
        "label": "Rooms", "access": ("front_desk", "manage"), "requires": "",
        "fields": {"external_ref": _field("ref", False, _OLD_ID), "room_number": _field("name", True, "the physical room number or name"),
                   "room_type": _field("name", True, "the room's type: its name, or the old system's room-type id"),
                   "base_price": _field("amount", False, "the nightly price of the room type (needed for a room type the hotel does not have yet)")},
        "key": _room_key, "exists": _room_exists, "check": _room_check, "write": _write_rooms,
    },
    "guests": {
        "label": "Guest profiles", "access": ("reservations", "operate"), "requires": "",
        "fields": {"external_ref": _field("ref", False, _OLD_ID), "guest_name": _field("name", True, "the guest's full name"),
                   "phone": _field("phone", False, "the guest's mobile phone"), "email": _field("email", False, "the guest's email"),
                   "nationality": _field("name", False, "the guest's nationality or country")},
        "key": _guest_key, "exists": _guest_exists, "check": _guest_check, "write": _write_guests,
    },
    "bookings": {
        "label": "Bookings (upcoming, in house and past)", "access": ("reservations", "operate"), "requires": "",
        "fields": {"external_ref": _field("ref", False, _OLD_ID),
                   "guest_ref": _field("ref", False, "the old system's id of the booking's guest, when the file names the guest by id"),
                   "guest_name": _field("name", False, "the booker or main guest's full name"),
                   "phone": _field("phone", False, "the guest's mobile phone"), "email": _field("email", False, "the guest's email"),
                   "nationality": _field("name", False, "the guest's nationality"),
                   "room_type": _field("name", True, "the booked room type: its name, or the old system's room-type id"),
                   "room_number": _field("name", False, "the room the guest had or has: its number, or the old system's room id"),
                   "check_in": _field("date", True, "the arrival date"),
                   "check_out": _field("date", True, "the departure date (the morning after the last night)"),
                   "status": _field("choice", True, "where the booking stands", STATUSES),
                   "booked_on": _field("date", False, "the date the booking was made"),
                   "cancelled_on": _field("date", False, "the date a cancelled booking was cancelled"),
                   "adults": _field("integer", False, "number of adults"), "children": _field("integer", False, "number of children"),
                   "channel": _field("name", False, "where the booking came from (an OTA name, or direct)"),
                   "channel_booking_ref": _field("text", False, "the booking's reference number in that channel"),
                   "nightly_rate": _field("amount", False, "the agreed price per night"),
                   "total_amount": _field("amount", False, "the stay's total room price, when there is no nightly rate"),
                   "notes": _field("text", False, "booking notes")},
        "key": _booking_key, "exists": _booking_exists, "check": _booking_check, "hold": _hold_booking, "write": _write_bookings,
    },
    "payments": {
        "label": "Payments and deposits", "access": ("reservations", "operate"), "requires": "",
        "fields": {"external_ref": _field("ref", False, _OLD_ID),
                   "booking_ref": _field("ref", True, "the booking paid for: the old system's booking id or its channel reference"),
                   "amount": _field("amount", True, "the amount paid or refunded"), "paid_on": _field("date", True, "the date it was paid"),
                   "method": _field("name", True, "how it was paid (cash, card, bank transfer, ...)"),
                   "kind": _field("choice", True, "a payment (deposits included) or a refund", ("payment", "refund")),
                   "reference": _field("text", False, "the receipt or transaction reference")},
        "key": _payment_key, "exists": _payment_exists, "check": _payment_check, "write": _write_payments,
    },
    "user_accounts": {
        "label": "Staff user accounts (invited)", "access": ("admin", "manage"), "requires": "",
        "fields": {"external_ref": _field("ref", False, _OLD_ID), "user_name": _field("name", True, "the person's full name"),
                   "phone": _field("phone", False, "their mobile phone (the invite goes there)"), "email": _field("email", False, "their email"),
                   "role": _field("name", True, "their LEON role", choices_from="roles"),
                   "module_access": _field("access", False, "access that differs from the role, as module:level pairs"),
                   "job_title": _field("text", False, "their job title")},
        "key": _account_key, "exists": _account_exists, "check": _account_check, "write": _write_accounts,
    },
    "company_accounts": {
        "label": "Company and city-ledger accounts", "access": ("finance", "manage"), "requires": "",
        "fields": {"external_ref": _field("ref", False, _OLD_ID), "account_name": _field("name", True, "the company or agent's name"),
                   "account_type": _field("choice", True, "the kind of account", ("company", "travel_agent", "channel", "government", "other")),
                   "credit_limit": _field("amount", True, "the credit limit"),
                   "payment_terms_days": _field("integer", True, "days the account has to pay an invoice"),
                   "opening_balance": _field("amount", False, "what the account owes today, carried over"),
                   "opening_charge_code": _field("name", False, "the untaxed finance charge code the opening balance posts to",
                                                 choices_from="charge_codes"),
                   "contact_name": _field("text", False, "the billing contact"), "contact_email": _field("email", False, "the billing email"),
                   "contact_phone": _field("phone", False, "the billing phone")},
        "key": _company_key, "exists": _company_exists, "check": _company_check, "write": _write_companies,
    },
    "staff": {
        "label": "HR staff records", "access": ("hr", "manage"), "requires": "hr.enabled",
        "fields": {"external_ref": _field("ref", False, _OLD_ID), "staff_name": _field("name", True, "the staff member's full name"),
                   "department": _field("name", True, "the department they work in"),
                   "employment_type": _field("choice", True, "how they are employed", ("full_time", "part_time", "casual", "contract")),
                   "phone": _field("phone", False, "their mobile phone"), "email": _field("email", False, "their email"),
                   "hired_on": _field("date", False, "the date they started")},
        "key": _staff_key, "exists": _staff_exists, "check": _staff_check, "write": _write_staff,
    },
    "outlets": {
        "label": "F&B outlets", "access": ("fnb", "manage"), "requires": "fnb.enabled",
        "fields": {"outlet_name": _field("name", True, "the outlet's name"),
                   "outlet_type": _field("choice", True, "the kind of outlet", ("restaurant", "bar", "room_service", "cafe", "banquet", "other"))},
        "key": _outlet_key, "exists": _outlet_exists, "check": _none, "write": _write_outlets,
    },
    "menu_items": {
        "label": "Menu items", "access": ("fnb", "manage"), "requires": "fnb.enabled",
        "fields": {"outlet_name": _field("name", True, "the outlet that sells the item"), "item_name": _field("name", True, "the item's name"),
                   "price": _field("amount", True, "the item's price"),
                   "transaction_code": _field("name", True, "the finance charge code the item posts to", choices_from="charge_codes")},
        "key": _item_key, "exists": _item_exists, "check": _item_check, "write": _write_items,
    },
}
ORDER = tuple(ENTITIES)


# ---------- rollback: what each recorded row needs ----------

def _undo_sql(sql, changed):
    def run(cur, ctx, record):
        cur.execute(sql, (record["entity_id"],))
        if cur.rowcount != 1:
            raise Refused(changed + " " + record["entity_id"])
    return run


def _undo_document(cur, ctx, record):
    database_name, collection_name = record["collection"].split("/", 1)
    cur.execute("DELETE FROM content_document WHERE database_name = %s AND collection_name = %s AND document_key = %s",
                (database_name, collection_name, record["entity_id"]))


def _undo_recount(cur, ctx, record):
    inventory_manager.apply_room_count(cur, ctx["hotel_id"], ctx["user_id"], record["entity_id"], ctx["today"])


def _undo_inventory(cur, ctx, record):
    type_id, _, day = record["entity_id"].partition("/")
    cur.execute("DELETE FROM room_inventory_nightly WHERE room_type_id = %s AND stay_date = %s", (type_id, day))


def _undo_hotel_user(cur, ctx, record):
    cur.execute("DELETE FROM hotel_user WHERE hotel_id = %s AND user_id = %s AND status = 'invited'", (ctx["hotel_id"], record["entity_id"]))
    if cur.rowcount != 1:
        raise Refused("an invited person has already joined or was changed: " + record["entity_id"])


def _undo_posting(cur, ctx, record):
    from booking_system.finance import service as finance
    reversed_ = finance.reverse_posting_in(cur, ctx["hotel_id"], record["entity_id"], "import rolled back", "migration",
                                           "rollback:" + record["entity_id"], system="migration")
    if not reversed_["ok"]:
        raise Refused("opening balance " + record["entity_id"] + " cannot be reversed: " + reversed_["error"])


def _undo_account(cur, ctx, record):
    """An account without postings is removed; one whose opening balance was reversed is closed (the ledger keeps both postings)."""
    cur.execute("SELECT 1 FROM finance_posting WHERE ar_account_id = %s LIMIT 1", (record["entity_id"],))
    posted = cur.fetchone()
    cur.execute("UPDATE ar_account SET status = 'closed' WHERE ar_account_id = %s" if posted else "DELETE FROM ar_account WHERE ar_account_id = %s",
                (record["entity_id"],))
    ctx.setdefault("closed_accounts", []).extend([record["entity_id"]] if posted else [])


UNDO = {
    "booking": _undo_sql("DELETE FROM booking WHERE booking_id = %s AND updated_at IS NULL "
                         "AND NOT EXISTS (SELECT 1 FROM payment p WHERE p.booking_id = booking.booking_id)",
                         "changed since the import (checked in, cancelled, modified or paid):"),
    "payment": _undo_sql("DELETE FROM payment WHERE payment_id = %s AND payment_status = 'received'", "payment voided since the import:"),
    "guest": _undo_sql("DELETE FROM guest WHERE guest_id = %s", "guest already removed:"),
    "room": _undo_sql("DELETE FROM room WHERE room_id = %s", "room already removed:"),
    "room_type": _undo_sql("DELETE FROM room_type WHERE room_type_id = %s", "room type already removed:"),
    "room_type_recount": _undo_recount,
    "room_inventory_nightly": _undo_inventory,
    "content_document": _undo_document,
    "user_account": _undo_sql("DELETE FROM user_account WHERE user_id = %s AND NOT EXISTS (SELECT 1 FROM hotel_user h WHERE h.user_id = user_account.user_id)",
                              "person belongs to a hotel since the import:"),
    "hotel_user": _undo_hotel_user,
    "onboarding_invite": _undo_sql("DELETE FROM onboarding_invite WHERE invite_id = %s AND status <> 'accepted'", "invite already accepted:"),
    "ar_account": _undo_account,
    "finance_posting": _undo_posting,
    "staff": _undo_sql("DELETE FROM staff WHERE staff_id = %s", "staff already removed:"),
    "outlet": _undo_sql("DELETE FROM outlet WHERE outlet_id = %s", "outlet already removed:"),
    "fnb_menu_item": _undo_sql("DELETE FROM fnb_menu_item WHERE item_id = %s", "menu item already removed:"),
}


# ---------- access ----------

def available(hotel_id, modules):
    """Entities this person may import now: their module level meets the entity's, and its module is switched on."""
    from booking_system.settings import service
    values = service.values_in_effect(hotel_id)
    return [name for name, spec in ENTITIES.items()
            if RANK[(modules or {}).get(spec["access"][0], "none")] >= RANK[spec["access"][1]] and (not spec["requires"] or values.get(spec["requires"]) is True)]


def _charge_codes(hotel_id):
    from booking_system.finance import service as finance
    listed = finance.list_transaction_codes(hotel_id)
    return sorted(row["transaction_code"] for row in listed.get("transaction_codes") or []
                  if row["transaction_type"] == "charge" and row["status"] == "active")


CHOICE_SOURCES = {"roles": lambda hotel_id: sorted(db.transaction(lambda cur: _roles(cur, hotel_id))), "charge_codes": _charge_codes}


def entity_catalog(names, hotel_id):
    def described(field, key):
        return dict(field, field=key, choices=CHOICE_SOURCES[field["choices_from"]](hotel_id) if field["choices_from"] else field["choices"])
    return [{"entity": name, "label": ENTITIES[name]["label"],
             "fields": [described(field, key) for key, field in ENTITIES[name]["fields"].items()]} for name in names]


# ---------- mapping ----------

_MAPPING_INSTRUCTIONS = (
    "A hotel is moving to LEON from another PMS and uploaded one exported file. Choose which LEON entity the file holds, from the "
    "listed entities only, and map each LEON field of that entity to the file column that holds it. source is the column name copied "
    "exactly, or empty when no column holds the field. fixed is a value for every row when the file has no column for it but the "
    "file name or its content makes the value certain, or, for a field with listed choices that no column holds, the one choice "
    "named for that purpose (an opening balance code for opening balances); empty otherwise. Read the sample rows, not only the headers. "
    "format, for a date field, is the Python strptime format of that column's values as they appear (a column with a time of day "
    "includes it); empty for other fields. date_format is the format most date columns share. "
    "value_maps translate the coded values a column holds (column_values lists each low-variety column's distinct values) into the "
    "field's choices: one entry per distinct source value of a mapped choice field, value being one of the field's choices; for a "
    "module_access field, value is module:level pairs separated by commas; for a whole-number field whose values carry words "
    "('30 days'), value is the number. A source value that matches no choice gets no entry. "
    "A rooms file's housekeeping column (vacant, dirty, out of order) is not the booking status field; leave it unmapped. "
    "phone_has_country_code is true when the phone numbers already start with the country code (with or without +). "
    "phone_country_code is the country calling code (digits) for numbers written locally, from the hotel's country; "
    "phone_trunk_prefix is the digit a local number starts with that is dropped after the country code (0 in many countries), "
    "or empty. explanation tells the manager in one or two plain sentences what the file holds and anything left unmapped."
)


def _mapping_schema(names, headers):
    fields = sorted({key for name in names for key in ENTITIES[name]["fields"]})
    return {
        "type": "object",
        "properties": {
            "entity": {"type": "string", "enum": list(names)},
            "fields": {"type": "array", "items": {
                "type": "object",
                "properties": {"field": {"type": "string", "enum": fields}, "source": {"type": "string", "enum": [""] + list(headers)},
                               "fixed": {"type": "string"}, "format": {"type": "string"}},
                "required": ["field", "source", "fixed", "format"], "additionalProperties": False}},
            "value_maps": {"type": "array", "items": {
                "type": "object",
                "properties": {"field": {"type": "string", "enum": fields}, "source_value": {"type": "string"}, "value": {"type": "string"}},
                "required": ["field", "source_value", "value"], "additionalProperties": False}},
            "date_format": {"type": "string"},
            "phone_has_country_code": {"type": "boolean"},
            "phone_country_code": {"type": "string"},
            "phone_trunk_prefix": {"type": "string"},
            "explanation": {"type": "string"},
        },
        "required": ["entity", "fields", "value_maps", "date_format", "phone_has_country_code", "phone_country_code", "phone_trunk_prefix", "explanation"],
        "additionalProperties": False,
    }


def _column_values(headers, rows):
    """Each column with few distinct values, and those values: the coded columns a mapping translates."""
    found = {}
    for header in headers:
        distinct = sorted({str(row.get(header) or "").strip() for row in rows} - {""})
        if distinct and len(distinct) <= CODED_VALUES and len(distinct) < max(2, len(rows)):
            found[header] = distinct
    return found


def propose_mapping(hotel_id, names, file_name, headers, rows, hint):
    hotel = read_hotel_by_id(hotel_id) or {}
    parsed = llm.structured(_MAPPING_INSTRUCTIONS, {
        "hotel": {"timezone": hotel.get("timezone"), "currency": str(hotel.get("currency") or "").strip()},
        "entities": entity_catalog(names, hotel_id), "manager_hint": hint, "file_name": file_name, "headers": headers,
        "sample_rows": rows[:SAMPLE_ROWS], "column_values": _column_values(headers, rows),
    }, "import_mapping", _mapping_schema(names, headers))
    return _clean_mapping(parsed["entity"], parsed)


def _clean_mapping(entity, given):
    """Every field of the entity once: its source column, fixed value and date format; the value translations of its fields."""
    spec = ENTITIES[entity]["fields"]
    named = {item["field"]: item for item in given.get("fields") or [] if item.get("field") in spec}
    maps = {}
    for item in given.get("value_maps") or []:
        if item.get("field") in spec and str(item.get("source_value") or "").strip():
            maps.setdefault(item["field"], {})[str(item["source_value"]).strip()] = str(item.get("value") or "")
    return {
        "entity": entity,
        "fields": {key: {"source": str((named.get(key) or {}).get("source") or ""), "fixed": str((named.get(key) or {}).get("fixed") or ""),
                         "format": str((named.get(key) or {}).get("format") or "")}
                   for key in spec},
        "value_maps": maps,
        "date_format": str(given.get("date_format") or ""),
        "phone_has_country_code": bool(given.get("phone_has_country_code")),
        "phone_country_code": str(given.get("phone_country_code") or ""),
        "phone_trunk_prefix": str(given.get("phone_trunk_prefix") or ""),
        "explanation": str(given.get("explanation") or ""),
    }


# ---------- batches and files ----------

def _batch(cur, hotel_id, batch_id):
    cur.execute("SELECT * FROM import_batch WHERE hotel_id = %s AND batch_id = %s FOR UPDATE", (hotel_id, batch_id))
    found = cur.fetchone()
    if not found:
        raise Refused("no import batch " + str(batch_id) + " at this hotel")
    return found


def _open_batch(cur, hotel_id, user_id, onboarding_id):
    """READ the session's draft or validated batch; create one when there is none."""
    cur.execute("""SELECT batch_id FROM import_batch WHERE hotel_id = %s AND status IN ('draft','validated')
                   AND onboarding_id IS NOT DISTINCT FROM %s ORDER BY created_at DESC LIMIT 1""", (hotel_id, onboarding_id))
    found = cur.fetchone()
    if found:
        return found["batch_id"]
    batch_id = new_id("imb")
    cur.execute("INSERT INTO import_batch (batch_id, hotel_id, user_id, onboarding_id, status) VALUES (%s, %s, %s, %s, 'draft')",
                (batch_id, hotel_id, user_id, onboarding_id))
    return batch_id


def _run(fn):
    try:
        return db.transaction(fn)
    except Refused as refusal:
        return {"ok": False, "error": str(refusal)}


def add_file(hotel_id, user_id, onboarding_id, modules, file_name, content_base64, hint=""):
    """Read the file, have the model propose its entity and mapping, and keep it in the session's open batch."""
    names = available(hotel_id, modules)
    if not names:
        return {"ok": False, "error": "nothing can be imported with your access"}
    try:
        headers, rows = files.read_table(file_name, files.decode(content_base64))
    except files.Unreadable as reason:
        return {"ok": False, "error": str(reason)}
    mapping = propose_mapping(hotel_id, names, file_name, headers, rows, hint)
    file_id = new_id("imf")

    def run(cur):
        batch_id = _open_batch(cur, hotel_id, user_id, onboarding_id)
        cur.execute("""INSERT INTO import_file (file_id, batch_id, hotel_id, file_name, entity, headers, mapping, mapping_status, row_count)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'proposed', %s)""",
                    (file_id, batch_id, hotel_id, str(file_name)[:255], mapping["entity"], Json(headers), Json(mapping), len(rows)))
        execute_values(cur, "INSERT INTO import_row (file_id, hotel_id, row_number, raw) VALUES %s",
                       [(file_id, hotel_id, index + 1, Json(row)) for index, row in enumerate(rows)])
        cur.execute("UPDATE import_batch SET status = 'draft', validated_at = NULL WHERE batch_id = %s", (batch_id,))
        return {"ok": True, "batch_id": batch_id, "file_id": file_id}
    return dict(_run(run), file_name=file_name, entity=mapping["entity"], rows=len(rows), mapping=mapping)


def set_mapping(hotel_id, batch_id, file_id, entity, fields, formats, value_maps=None):
    """The manager's corrections: entity, {field: {source, fixed, format}}, the date and phone formats, and [{field, source_value, value}].
    The batch must be previewed again."""
    def run(cur):
        batch = _batch(cur, hotel_id, batch_id)
        if batch["status"] not in ("draft", "validated"):
            raise Refused("batch " + batch_id + " is " + batch["status"])
        cur.execute("SELECT headers, mapping FROM import_file WHERE file_id = %s AND batch_id = %s", (file_id, batch_id))
        found = cur.fetchone()
        if not found:
            raise Refused("no file " + str(file_id) + " in batch " + batch_id)
        chosen = entity or found["mapping"]["entity"]
        if chosen not in ENTITIES:
            raise Refused("entity is one of " + ", ".join(ORDER))
        current = found["mapping"] if chosen == found["mapping"]["entity"] else _clean_mapping(chosen, dict(found["mapping"], fields=[], value_maps=[]))
        unknown = [item.get("source") for item in (fields or {}).values() if item.get("source") and item["source"] not in found["headers"]]
        if unknown:
            raise Refused("no column " + ", ".join(unknown) + " in the file")
        wanted = {key: dict(current["fields"].get(key) or {}, **(fields or {}).get(key, {})) for key in ENTITIES[chosen]["fields"]}
        incoming = {}
        for item in value_maps or []:
            incoming.setdefault(str(item.get("field") or ""), {})[str(item.get("source_value") or "")] = item.get("value")
        maps = {key: dict((current.get("value_maps") or {}).get(key) or {}, **incoming.get(key, {}))
                for key in set(current.get("value_maps") or {}) | set(incoming)}
        given = dict(current, **{key: (formats or {})[key] for key in FORMATS if key in (formats or {})})
        mapping = _clean_mapping(chosen, dict(given, fields=[dict(value, field=key) for key, value in wanted.items()],
                                              value_maps=[{"field": key, "source_value": source, "value": value}
                                                          for key, pairs in maps.items() for source, value in pairs.items()]))
        cur.execute("UPDATE import_file SET entity = %s, mapping = %s, mapping_status = 'proposed' WHERE file_id = %s", (chosen, Json(mapping), file_id))
        cur.execute("UPDATE import_batch SET status = 'draft', validated_at = NULL WHERE batch_id = %s", (batch_id,))
        return {"ok": True, "batch_id": batch_id, "file_id": file_id, "mapping": mapping}
    return _run(run)


def remove_file(hotel_id, batch_id, file_id):
    def run(cur):
        batch = _batch(cur, hotel_id, batch_id)
        if batch["status"] not in ("draft", "validated"):
            raise Refused("batch " + batch_id + " is " + batch["status"])
        cur.execute("DELETE FROM import_file WHERE file_id = %s AND batch_id = %s", (file_id, batch_id))
        cur.execute("UPDATE import_batch SET status = 'draft', validated_at = NULL WHERE batch_id = %s", (batch_id,))
        return {"ok": bool(cur.rowcount), "batch_id": batch_id}
    return _run(run)


# ---------- validation ----------

def _values(row, mapping, spec):
    """(values, errors) of one source row under the mapping: the value translation first, then the field's type."""
    values, errors = {}, []
    for key, field in spec["fields"].items():
        chosen = mapping["fields"].get(key) or {}
        text = str(row.get(chosen.get("source")) or "").strip() if chosen.get("source") else str(chosen.get("fixed") or "").strip()
        text = ((mapping.get("value_maps") or {}).get(key) or {}).get(text, text)
        if not text:
            values[key] = None
            errors.extend([(key, "missing", key + " is empty")] if field["required"] else [])
            continue
        try:
            values[key] = TYPES[field["type"]](text, mapping, dict(field, format=chosen.get("format") or ""))
        except Invalid as reason:
            values[key] = None
            errors.append((key, "invalid", key + " " + str(reason)))
    return values, errors


def _check(cur, ctx, batch_id):
    """Every file of the batch in entity order: valid rows per file and row errors. Valid rows of earlier entities are visible to
    later ones (a booking's room type and guest may come from other files)."""
    cur.execute("SELECT file_id, file_name, entity, mapping FROM import_file WHERE batch_id = %s", (batch_id,))
    found = sorted(cur.fetchall(), key=lambda row: (ORDER.index(row["entity"]), row["file_name"]))
    ctx["batch"], ctx["room_nights"] = {}, set()
    seen = set()
    report = []
    for item in found:
        spec = ENTITIES[item["entity"]]
        cur.execute("SELECT row_number, raw FROM import_row WHERE file_id = %s ORDER BY row_number", (item["file_id"],))
        valid, errors, duplicates = [], [], 0
        for row in cur.fetchall():
            values, unreadable = _values(row["raw"], item["mapping"], spec)
            problems = unreadable or spec["check"](cur, ctx, values)
            keys = [] if unreadable else [(item["entity"],) + key for key in spec["key"](values)]
            clash = [key for key in keys if key in seen or spec["exists"](cur, ctx, key[1:])]
            if clash:
                duplicates += 1
                where = "earlier in this import" if clash[0] in seen else "already at the hotel"
                problems = [(clash[0][1] if clash[0][1] in spec["fields"] else "", "duplicate",
                             " ".join(str(part) for part in clash[0][1:]) + " is " + where)]
            seen.update([] if problems else keys)
            errors.extend((row["row_number"], field, kind, message) for field, kind, message in problems)
            if not problems:
                valid.append(dict(values, _row=row["row_number"]))
                (spec.get("hold") or _hold_none)(ctx, values)
        ctx["batch"][item["entity"]] = ctx["batch"].get(item["entity"], []) + valid
        report.append({"file_id": item["file_id"], "file_name": item["file_name"], "entity": item["entity"], "valid": valid,
                       "errors": errors, "duplicates": duplicates})
    return report


def _context(hotel_id, user_id):
    from booking_system.semantic.hotel import hotel_today
    from booking_system.settings import service
    hotel = read_hotel_by_id(hotel_id) or {}
    return {"hotel_id": hotel_id, "user_id": user_id, "today": hotel_today(hotel_id), "codes": {}, "batch": {}, "batch_id": "",
            "values": service.values_in_effect(hotel_id), "timezone": hotel.get("timezone"), "currency": str(hotel.get("currency") or "").strip()}


def _store_report(cur, report):
    for item in report:
        cur.execute("DELETE FROM import_row_error WHERE file_id = %s", (item["file_id"],))
        if item["errors"]:
            execute_values(cur, "INSERT INTO import_row_error (error_id, file_id, hotel_id, row_number, field, error_kind, message) VALUES %s",
                           [(new_id("ime"), item["file_id"], item["hotel_id"], *error) for error in item["errors"]])
        cur.execute("""UPDATE import_file SET valid_count = %s, error_count = %s, duplicate_count = %s, mapping_status = 'confirmed'
                       WHERE file_id = %s""", (len(item["valid"]), len({error[0] for error in item["errors"]}), item["duplicates"], item["file_id"]))


def preview(hotel_id, user_id, batch_id):
    """The manager confirmed the mappings: check every row, store the row errors and counts. The batch is validated when some row
    can be imported."""
    ctx = _context(hotel_id, user_id)

    def run(cur):
        batch = _batch(cur, hotel_id, batch_id)
        if batch["status"] not in ("draft", "validated"):
            raise Refused("batch " + batch_id + " is " + batch["status"])
        report = [dict(item, hotel_id=hotel_id) for item in _check(cur, ctx, batch_id)]
        if not report:
            raise Refused("batch " + batch_id + " has no files")
        _store_report(cur, report)
        ready = any(item["valid"] for item in report)
        cur.execute("UPDATE import_batch SET status = %s, validated_at = CASE WHEN %s THEN now() END, detail = '' WHERE batch_id = %s",
                    ("validated" if ready else "draft", ready, batch_id))
        return {"ok": True, "batch_id": batch_id, "status": "validated" if ready else "draft"}
    result = _run(run)
    return dict(result, **read_batch(hotel_id, batch_id)) if result.get("ok") else result


def commit(hotel_id, user_id, batch_id, modules):
    """One transaction: check every row again, write the valid ones in entity order, record each created row. Any refusal rolls
    everything back; it is stored as that row's error and the batch must be previewed again."""
    ctx = dict(_context(hotel_id, user_id), batch_id=batch_id, at=0)
    allowed = set(available(hotel_id, modules))
    records = []
    failure = {}

    def record(table, entity_id, collection=None):
        records.append((table, entity_id, collection.database_name + "/" + collection.collection_name if collection else ""))

    ctx["record"] = record

    def run(cur):
        batch = _batch(cur, hotel_id, batch_id)
        if batch["status"] != "validated":
            raise Refused("batch " + batch_id + " is " + batch["status"] + "; preview it first")
        report = _check(cur, ctx, batch_id)
        blocked = sorted({item["entity"] for item in report} - allowed)
        if blocked:
            raise Refused("your access or the hotel's modules do not allow importing " + ", ".join(blocked))
        for item in report:
            failure.update(file_id=item["file_id"])
            item["created"] = ENTITIES[item["entity"]]["write"](cur, ctx, item["valid"])
        execute_values(cur, "INSERT INTO import_record (batch_id, hotel_id, seq, entity_table, entity_id, collection) VALUES %s",
                       [(batch_id, hotel_id, index + 1, *row) for index, row in enumerate(records)])
        for item in report:
            cur.execute("UPDATE import_file SET created_count = %s WHERE file_id = %s", (item["created"], item["file_id"]))
        cur.execute("UPDATE import_batch SET status = 'committed', committed_at = now(), detail = '' WHERE batch_id = %s", (batch_id,))
        return {"ok": True, "batch_id": batch_id, "status": "committed",
                "created": {item["entity"]: item["created"] for item in report}, "skipped_rows": sum(len({e[0] for e in item["errors"]}) for item in report)}
    try:
        result = _run(run)
    except psycopg2.Error as error:
        detail = " ".join(part for part in (error.diag.message_primary, error.diag.message_detail) if part)
        result = {"ok": False, "error": detail.strip()}
    if not result.get("ok") and failure.get("file_id"):
        db.transaction(lambda cur: _refused(cur, hotel_id, batch_id, failure["file_id"], ctx["at"], result["error"]))
    return dict(result, **read_batch(hotel_id, batch_id))


def _refused(cur, hotel_id, batch_id, file_id, row_number, message):
    cur.execute("INSERT INTO import_row_error (error_id, file_id, hotel_id, row_number, error_kind, message) VALUES (%s, %s, %s, %s, 'refused', %s)",
                (new_id("ime"), file_id, hotel_id, row_number, "the import was refused and nothing was written: " + message))
    cur.execute("UPDATE import_batch SET status = 'draft', validated_at = NULL, detail = %s WHERE batch_id = %s", (message, batch_id))


def rollback(hotel_id, user_id, batch_id):
    """READ the committed batch and its records; undo them newest first in one transaction. A row changed since the import (a
    booking checked in, modified or paid, a guest with other bookings, an invited person who joined) refuses the whole rollback."""
    ctx = _context(hotel_id, user_id)

    def run(cur):
        batch = _batch(cur, hotel_id, batch_id)
        if batch["status"] != "committed":
            raise Refused("batch " + batch_id + " is " + batch["status"] + ", not committed")
        cur.execute("SELECT seq, entity_table, entity_id, collection FROM import_record WHERE batch_id = %s ORDER BY seq DESC", (batch_id,))
        done = {}
        for record in cur.fetchall():
            UNDO[record["entity_table"]](cur, ctx, record)
            done[record["entity_table"]] = done.get(record["entity_table"], 0) + 1
        cur.execute("DELETE FROM import_external_ref WHERE batch_id = %s", (batch_id,))
        cur.execute("UPDATE import_batch SET status = 'rolled_back', rolled_back_at = now() WHERE batch_id = %s", (batch_id,))
        return {"ok": True, "batch_id": batch_id, "status": "rolled_back", "removed": done,
                "closed_accounts": ctx.get("closed_accounts") or []}
    try:
        result = _run(run)
    except psycopg2.Error as error:
        result = {"ok": False, "error": "rollback refused, nothing was removed: " + (error.diag.message_primary or str(error)).strip()}
    return dict(result, **read_batch(hotel_id, batch_id))


# ---------- reads ----------

def read_batch(hotel_id, batch_id):
    batch = db.row("SELECT batch_id, status, created_at, validated_at, committed_at, rolled_back_at, detail FROM import_batch "
                   "WHERE hotel_id = %s AND batch_id = %s", (hotel_id, batch_id))
    if not batch:
        return {"batch": None, "files": []}
    found = db.rows("""SELECT file_id, file_name, entity, headers, mapping, mapping_status, row_count, valid_count, error_count, duplicate_count,
                              created_count FROM import_file WHERE batch_id = %s ORDER BY created_at""", (batch_id,))
    errors = db.rows("""SELECT file_id, row_number, field, error_kind, message FROM import_row_error WHERE file_id = ANY(%s)
                        ORDER BY file_id, row_number LIMIT %s""", ([row["file_id"] for row in found], ERRORS_SHOWN * max(1, len(found))))
    samples = db.rows("SELECT file_id, row_number, raw FROM import_row WHERE file_id = ANY(%s) AND row_number <= 5 ORDER BY row_number",
                      ([row["file_id"] for row in found],))
    return {"batch": batch, "files": [dict(row, label=ENTITIES[row["entity"]]["label"],
                                           errors=[e for e in errors if e["file_id"] == row["file_id"]][:ERRORS_SHOWN],
                                           sample=[s["raw"] for s in samples if s["file_id"] == row["file_id"]]) for row in found]}


def batches(hotel_id, onboarding_id=None):
    rows = db.rows("SELECT batch_id FROM import_batch WHERE hotel_id = %s AND (%s::varchar IS NULL OR onboarding_id = %s) ORDER BY created_at",
                   (hotel_id, onboarding_id, onboarding_id))
    return [read_batch(hotel_id, row["batch_id"]) for row in rows]
