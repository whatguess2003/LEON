"""Python checks for front-desk actions on the core tables. Each check reads the system before Execute is offered. The hotel is
a hotel_id; the acting user is a user_id (None when Leon acts for a guest)."""

from booking_system import db

_METHODS = ("CASH", "BANK TRANSFER", "CARD", "OTA", "CHEQUE", "OTHERS")
_PROFILE_FIELDS = ("guest_name", "email", "country", "special_request")
_MODIFY_FIELDS = ("new_stay_dates", "new_room_type", "new_rooms_booked")
PARAMS = (
    "booking_id",
    "room_type",
    "stay_dates",
    "rooms_booked",
    "new_stay_dates",
    "new_room_type",
    "new_rooms_booked",
    "room_number",
    "stay_date",
    "old_room_number",
    "new_room_number",
    "payment_amount",
    "payment_method",
    "payment_seq",
    "guest_phone",
    "guest_name",
    "guest_count",
    "email",
    "country",
    "price",
    "rooms",
    "ask",
)
_LIVE_BOOKINGS = ("confirmed", "checked_in")


def _text(params, name):
    return str((params or {}).get(name) or "").strip()


def _dates(value):
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _count(value):
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    count = int(text)
    if count < 1:
        return None
    return count


def _iso_night(value, day):
    from booking_system.semantic.dates import as_iso
    return as_iso(value)


def _amount(value):
    try:
        number = float(str(value).strip())
    except ValueError:
        return None
    return number if number > 0 else None


def _seq(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    return number if number > 0 else None


def _hotel(hotel_id):
    from booking_system.hotel_registry import read_hotel_by_id
    return read_hotel_by_id(hotel_id) or {}


def _hotel_name(hotel_id):
    return str(_hotel(hotel_id).get("hotel_name") or "")


def hotel_today(hotel_id):
    """Today at the hotel, from hotel.timezone."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(_hotel(hotel_id)["timezone"])).date()


def _known_room_types(hotel_id):
    rows = db.rows("SELECT room_type_name FROM room_type WHERE hotel_id = %s AND status = 'active' ORDER BY room_type_name", (hotel_id,))
    return [row["room_type_name"] for row in rows]


def _one_room_type(hotel_id, given):
    """Use the hotel room type when the words contain exactly one of them."""
    text = str(given or "").strip().upper()
    if not text:
        return ""
    known = _known_room_types(hotel_id)
    if not known or text in known:
        return text
    tokens = [part for part in text.split(" ") if part]
    hits = []
    for name in known:
        parts = [part for part in name.split(" ") if part]
        span = len(parts)
        found = any(tokens[index:index + span] == parts for index in range(len(tokens) - span + 1))
        if found and name not in hits:
            hits.append(name)
    if len(hits) == 1:
        return hits[0]
    return text


def _rooms(value):
    text = str(value or "").strip()
    if not text:
        return 1
    return _seq(text)


def _with_pg(fn):
    from booking_system.guest_manager import guest_manager
    return guest_manager._with_pg(fn)


def _booking(hotel_id, phone, booking_id):
    """{'booking_status', 'room_type', 'check_in'} of a booking held by a guest with this phone, or None."""
    return db.row(
        """SELECT s.booking_status, s.check_in_date AS check_in,
                  (SELECT t.room_type_name FROM booking_room_night n JOIN room_type t ON t.room_type_id = n.room_type_id
                   WHERE n.booking_id = s.booking_id ORDER BY n.stay_date, n.room_seq LIMIT 1) AS room_type
           FROM booking_summary s
           WHERE s.hotel_id = %s AND s.booking_id = %s
             AND EXISTS (SELECT 1 FROM guest_identity i WHERE i.guest_id = s.guest_id AND i.identity_type = 'phone' AND i.identity_value = %s)""",
        (hotel_id, booking_id, phone))


def _stay_nights(hotel_id, booking_id):
    rows = db.rows(
        """SELECT DISTINCT stay_date FROM booking_room_night WHERE hotel_id = %s AND booking_id = %s AND night_status IN ('booked','checked_in')
           ORDER BY stay_date""", (hotel_id, booking_id))
    return [row["stay_date"].isoformat() for row in rows]


def _nights_open(hotel_id, room_type, nights, rooms_needed):
    if not nights or not room_type:
        return False
    from booking_system.inventory_manager import inventory_manager
    needed = rooms_needed or 1
    for night in nights:
        found = inventory_manager.read_availability(hotel_id, room_type, night)
        if found.get("error") or found.get("stop_sell") or int(found.get("available") or 0) < needed:
            return False
    return True


def _room_row(hotel_id, room_number, stay_date):
    """{'room_type', 'booking_id'} for an active room on a night (booking_id of the live night holding it), or None."""
    return db.row(
        """SELECT t.room_type_name AS room_type,
                  (SELECT n.booking_id FROM booking_room_night n WHERE n.room_id = r.room_id AND n.stay_date = %s
                     AND n.night_status IN ('booked','checked_in') LIMIT 1) AS booking_id
           FROM room r JOIN room_type t ON t.room_type_id = r.room_type_id
           WHERE r.hotel_id = %s AND r.room_number = %s AND r.status = 'active'""",
        (str(stay_date or "")[:10] or None, hotel_id, str(room_number or "").strip().upper()))


def _check_cancel(hotel_id, phone, params, today):
    row = _booking(hotel_id, phone, _text(params, "booking_id"))
    return bool(row) and row["booking_status"] == "confirmed"


def _check_create(hotel_id, phone, params, today):
    if _text(params, "guest_phone") != phone:
        return False
    rooms = _rooms(params.get("rooms_booked"))
    if rooms is None:
        return False
    return _nights_open(hotel_id, _text(params, "room_type"), _dates(params.get("stay_dates")), rooms)


def _check_modify(hotel_id, phone, params, today):
    booking_id = _text(params, "booking_id")
    row = _booking(hotel_id, phone, booking_id)
    if not row or row["booking_status"] != "confirmed":
        return False
    rooms = _rooms(params.get("new_rooms_booked"))
    if rooms is None:
        return False
    nights = _dates(params.get("new_stay_dates"))
    room_type = _text(params, "new_room_type") or str(row["room_type"] or "")
    if nights or _text(params, "new_room_type"):
        if not _nights_open(hotel_id, room_type, nights or _stay_nights(hotel_id, booking_id), rooms):
            return False
    return True


def _check_in(hotel_id, phone, params, today):
    row = _booking(hotel_id, phone, _text(params, "booking_id"))
    if not row or row["booking_status"] != "confirmed":
        return False
    arrival = row["check_in"].isoformat() if row["check_in"] else ""
    if arrival != str(today or ""):
        return False
    room = _room_row(hotel_id, _text(params, "room_number"), arrival)
    if not room:
        return False
    holder = str(room["booking_id"] or "")
    return room["room_type"] == str(row["room_type"] or "") and (not holder or holder == _text(params, "booking_id"))


def _check_out(hotel_id, phone, params, today):
    booking_id = _text(params, "booking_id")
    if not _booking(hotel_id, phone, booking_id):
        return False
    return db.row(
        """SELECT 1 AS found FROM booking_room_night n JOIN room r ON r.room_id = n.room_id
           WHERE n.hotel_id = %s AND n.booking_id = %s AND r.room_number = %s AND n.night_status = 'checked_in' LIMIT 1""",
        (hotel_id, booking_id, _text(params, "room_number").upper())) is not None


def _check_reassign(hotel_id, phone, params, today):
    booking_id = _text(params, "booking_id")
    row = _booking(hotel_id, phone, booking_id)
    if not row:
        return False
    stay_date = _text(params, "stay_date")
    old_room = _room_row(hotel_id, _text(params, "old_room_number"), stay_date)
    new_room = _room_row(hotel_id, _text(params, "new_room_number"), stay_date)
    if not old_room or str(old_room["booking_id"] or "") != booking_id or not new_room:
        return False
    holder = str(new_room["booking_id"] or "")
    return new_room["room_type"] == str(row["room_type"] or "") and (not holder or holder == booking_id)


def _check_payment(hotel_id, phone, params, today):
    row = _booking(hotel_id, phone, _text(params, "booking_id"))
    method = _text(params, "payment_method").upper()
    return bool(row) and row["booking_status"] != "cancelled" and method in _METHODS and _amount(params.get("payment_amount")) is not None


def _payments(hotel_id, booking_id):
    """The booking's payments in ledger order with payment_seq and whether a received refund points at each."""
    return db.rows(
        """SELECT p.payment_id, p.amount, p.payment_method, p.payment_status, ROW_NUMBER() OVER (ORDER BY p.paid_at, p.payment_id) AS payment_seq,
                  EXISTS (SELECT 1 FROM payment r WHERE r.booking_id = p.booking_id AND r.payment_kind = 'refund'
                          AND r.payment_status = 'received' AND r.reference = p.payment_id) AS refunded
           FROM payment p WHERE p.hotel_id = %s AND p.booking_id = %s AND p.payment_kind = 'payment' ORDER BY payment_seq""",
        (hotel_id, booking_id))


def _check_refund(hotel_id, phone, params, today):
    booking_id = _text(params, "booking_id")
    seq = _seq(params.get("payment_seq"))
    if seq is None or not _booking(hotel_id, phone, booking_id):
        return False
    found = [row for row in _payments(hotel_id, booking_id) if row["payment_seq"] == seq]
    return bool(found) and found[0]["payment_status"] == "received" and not found[0]["refunded"]


def _check_profile(hotel_id, phone, params, today):
    if _text(params, "guest_phone") != phone:
        return False
    from booking_system.guest_manager import guest_manager
    guest = guest_manager.read_guest(hotel_id, phone)
    return bool(guest) and guest["status"] == "active"


_CHECKS = {
    "cancel_booking": _check_cancel,
    "create_booking": _check_create,
    "modify_booking": _check_modify,
    "check_in": _check_in,
    "check_out": _check_out,
    "reassign_room": _check_reassign,
    "record_payment": _check_payment,
    "record_refund": _check_refund,
    "update_guest_profile": _check_profile,
}

_REQUIRED = {
    "cancel_booking": ("booking_id",),
    "create_booking": ("room_type", "stay_dates"),
    "modify_booking": ("booking_id",),
    "check_in": ("booking_id", "room_number"),
    "check_out": ("booking_id", "room_number"),
    "reassign_room": ("booking_id", "stay_date", "old_room_number", "new_room_number"),
    "record_payment": ("booking_id", "payment_amount", "payment_method"),
    "record_refund": ("booking_id", "payment_seq"),
    "update_guest_profile": ("guest_phone",),
}

_ONE_OF = {
    "modify_booking": _MODIFY_FIELDS,
    "update_guest_profile": _PROFILE_FIELDS,
}

_LABELS = {
    "cancel_booking": ("cancel booking", ("booking_id",)),
    "create_booking": ("create booking", ("room_type", "stay_dates")),
    "modify_booking": ("modify booking", ("booking_id", "new_stay_dates", "new_room_type", "new_rooms_booked")),
    "check_in": ("check in", ("booking_id", "room_number")),
    "check_out": ("check out", ("booking_id", "room_number")),
    "reassign_room": ("reassign room", ("booking_id", "stay_date", "old_room_number", "new_room_number")),
    "record_payment": ("record payment", ("booking_id", "payment_amount", "payment_method")),
    "record_refund": ("record refund", ("booking_id", "payment_seq")),
    "update_guest_profile": ("update guest", _PROFILE_FIELDS),
}

_KNOWN_PHONE = ("create_booking", "update_guest_profile")


_INFORMATIONAL = {"check_availability": ("room_type", "stay_dates")}


def action_names():
    return list(_CHECKS)


def information_names():
    return list(_INFORMATIONAL)


_LISTS = {
    "check_availability": ("Availability", "How many rooms are left?", "Availability", "Open", "No rooms on file"),
    "cancel_booking": ("Cancel booking", "Which booking should be cancelled?", "Bookings", "Cancel", "No booking to cancel"),
    "create_booking": ("Create booking", "Which room should be booked?", "Room types", "Book", "No room type"),
    "modify_booking": ("Modify booking", "Which booking should change?", "Bookings", "Modify", "No booking to change"),
    "check_in": ("Check in", "Who is checking in?", "Arrivals", "Check in", "No arrival today"),
    "check_out": ("Check out", "Who is checking out?", "Departures", "Check out", "No departure today"),
    "reassign_room": ("Reassign room", "Which room should move?", "Rooms", "Reassign", "No room assigned today"),
    "record_payment": ("Record payment", "Which booking is being paid?", "Payments", "Payment", "No booking to pay"),
    "record_refund": ("Record refund", "Which payment should be refunded?", "Refunds", "Refund", "No payment to refund"),
    "update_guest_profile": ("Guest profile", "Which detail should be saved?", "Profile", "Profile", "No guest on file"),
}


def _clip_list(value, limit):
    return str(value or "").strip()[:limit]


def _list_row(section, ident, title, description, kind, record):
    row = {
        "section": _clip_list(section, 24),
        "id": _clip_list(ident, 200),
        "title": _clip_list(title, 24) or "Choose",
        "description": _clip_list(description, 72),
        "kind": kind,
    }
    if record:
        row["record"] = record
    return row


def _plain_record(row):
    return {key: value for key, value in (row or {}).items() if key != "_id"}


def _iso(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value or "")[:10]


def _span(first, last):
    start = _iso(first)
    end = _iso(last)
    if start and end and start != end:
        return start + " to " + end
    return start or end


def _action_phone(brief):
    phone = ""
    for group in ("informational_actions", "executional_actions"):
        for item in (brief or {}).get(group) or []:
            found = _text(item, "guest_phone")
            if found and not phone:
                phone = found
    return phone


def _called_actions(brief):
    names = []
    for item in (brief or {}).get("informational_actions") or []:
        name = str((item or {}).get("action") or "")
        if name == "check_availability" and name not in names:
            names.append(name)
    source = (brief or {}).get("listed_actions")
    if not isinstance(source, list):
        source = (brief or {}).get("executional_actions") or []
    for item in source:
        name = str((item or {}).get("action") or "")
        if name in _LISTS and name not in names:
            names.append(name)
    return names


def _hotel_bookings(hotel_id, phone, statuses=_LIVE_BOOKINGS):
    """Up to 10 bookings of the hotel in these statuses (of one phone when given): booking_id, guest_phone, guest_name, room_type,
    first_night, last_night."""
    return db.rows(
        """SELECT s.booking_id, g.guest_name, s.check_in_date AS first_night, s.check_out_date - 1 AS last_night,
                  (SELECT identity_value FROM guest_identity i WHERE i.guest_id = s.guest_id AND i.identity_type = 'phone'
                   ORDER BY identity_value LIMIT 1) AS guest_phone,
                  (SELECT t.room_type_name FROM booking_room_night n JOIN room_type t ON t.room_type_id = n.room_type_id
                   WHERE n.booking_id = s.booking_id ORDER BY n.stay_date, n.room_seq LIMIT 1) AS room_type
           FROM booking_summary s JOIN guest g ON g.guest_id = s.guest_id
           WHERE s.hotel_id = %s AND s.booking_status = ANY(%s)
             AND (%s = '' OR EXISTS (SELECT 1 FROM guest_identity i WHERE i.guest_id = s.guest_id AND i.identity_type = 'phone'
                                     AND i.identity_value = %s))
           ORDER BY s.check_in_date, s.booking_id LIMIT 10""",
        (hotel_id, list(statuses), phone, phone))


def _priced_types(hotel_id, day):
    rows = db.rows(
        """SELECT t.room_type_name, p.base_price FROM room_type t
           LEFT JOIN daily_pricing p ON p.room_type_id = t.room_type_id AND p.stay_date = %s
           WHERE t.hotel_id = %s AND t.status = 'active' ORDER BY t.room_type_name LIMIT 10""",
        (day, hotel_id))
    return [(row["room_type_name"], row["base_price"]) for row in rows]


def _assigned_rooms(hotel_id, day):
    return db.rows(
        """SELECT DISTINCT r.room_number, t.room_type_name AS room_type, n.booking_id
           FROM booking_room_night n JOIN room r ON r.room_id = n.room_id JOIN room_type t ON t.room_type_id = n.room_type_id
           WHERE n.hotel_id = %s AND n.stay_date = %s AND n.night_status IN ('booked','checked_in') ORDER BY r.room_number LIMIT 10""",
        (hotel_id, day))


def _open_payments(hotel_id):
    """Up to 10 latest received payments not yet refunded, each with its payment_seq on the booking."""
    return db.rows(
        """SELECT booking_id, payment_seq, amount, payment_method FROM (
               SELECT p.booking_id, p.amount, p.payment_method, p.payment_status, p.paid_at,
                      ROW_NUMBER() OVER (PARTITION BY p.booking_id ORDER BY p.paid_at, p.payment_id) AS payment_seq,
                      EXISTS (SELECT 1 FROM payment r WHERE r.booking_id = p.booking_id AND r.payment_kind = 'refund'
                              AND r.payment_status = 'received' AND r.reference = p.payment_id) AS refunded
               FROM payment p WHERE p.hotel_id = %s AND p.payment_kind = 'payment') x
           WHERE payment_status = 'received' AND NOT refunded ORDER BY paid_at DESC LIMIT 10""",
        (hotel_id,))


def _held_for(approvals, action, booking_id):
    for row in approvals or []:
        if str(row.get("action") or "") == action and str(row.get("booking_id") or "") == str(booking_id or ""):
            return row
    return None


def _booking_choice(section, action, row, approvals):
    held = _held_for(approvals, action, row["booking_id"])
    kind = "approval" if held else "quote"
    title = str(row["guest_name"] or "").strip() or str(row["room_type"] or "").strip() or "Booking"
    detail = " ".join(part for part in [row["booking_id"], str(row["room_type"] or ""), _span(row["first_night"], row["last_night"])] if part)
    return _list_row(section, row["booking_id"], title, detail, kind, _plain_record(held) if held else None)


_ASK_BODY = {
    "rooms_left": "How many rooms are left?",
    "stay": "Which night limits the stay?",
    "sold_out": "Which nights are sold out?",
    "tight": "Which nights have one room left?",
    "price": "What is the price?",
    "set_price": "The price is updated.",
    "block": "Rooms are held back.",
    "open": "Rooms are open to sell.",
}

_ASK_EMPTY = {
    "rooms_left": "No rooms on file",
    "stay": "Room type and dates are needed",
    "sold_out": "No sold-out night this month",
    "tight": "No night with one room left",
    "price": "No price on file",
    "set_price": "Room type, date, and price are needed",
    "block": "Room type, date, and rooms are needed",
    "open": "Room type and date are needed",
}


def _night_list(value):
    """Each stay night already named, converted to YYYY-MM-DD. Nights between two dates are not filled in."""
    from booking_system.semantic.dates import as_iso
    return [as_iso(part) or part for part in _dates(value)]


def _query_fact(hotel_id, room_type, day):
    """The inventory night a price or block change reads first."""
    from booking_system.inventory_manager import inventory_manager
    room = str(room_type or "").upper()
    night = str(day or "")[:10]
    if not room or not night:
        return None
    return inventory_manager.night_on_file(hotel_id, room, night)


def _money(currency, price):
    if price is None:
        return ""
    return (str(currency or "").strip() + " " + str(price)).strip()


def _none_row(section, ask):
    return [_list_row(section, "none:" + ask, "None", _ASK_EMPTY.get(ask, "Nothing to show"), "quote", None)]


def _availability_item(brief):
    for item in (brief or {}).get("informational_actions") or []:
        if str((item or {}).get("action") or "") == "check_availability":
            return item or {}
    return {"action": "check_availability", "ask": "rooms_left"}


def _ask_set_price(hotel_id, user_id, day, item):
    from booking_system.inventory_manager import inventory_manager
    currency = str(_hotel(hotel_id).get("currency") or "").strip()
    room = _text(item, "room_type").upper()
    amount = _amount(_text(item, "price"))
    nights = _night_list(_text(item, "stay_dates"))
    if not nights:
        return _none_row("Price", "set_price"), _ASK_BODY["set_price"]
    if not room or amount is None:
        return _none_row("Price", "set_price"), _ASK_BODY["set_price"]
    rows = []
    for night in nights[:10]:
        fact = _query_fact(hotel_id, room, night)
        if not fact:
            rows.append(_list_row("Price", "none:" + night, night[:24], "No inventory on " + night, "quote", None))
            continue
        result = inventory_manager.update_room_price(hotel_id, user_id, room, night, amount)
        detail = night + " " + (_money(currency, fact["price"]) or "none") + " to " + _money(currency, _text(item, "price"))
        if isinstance(result, dict) and result.get("error"):
            detail = str(result["error"])
        rows.append(_list_row("Price", "price:" + night, room[:24], detail, "quote", None))
    return rows, _ASK_BODY["set_price"]


def _change_block(hotel_id, user_id, day, item, ask):
    from booking_system.inventory_manager import inventory_manager
    room = _text(item, "room_type").upper()
    nights = _night_list(_text(item, "stay_dates"))
    night = nights[0] if nights else ""
    if not night:
        return _none_row("Block", ask), _ASK_BODY[ask]
    stated = _seq(_text(item, "rooms"))
    if ask == "open" and stated is None:
        stated = 0
    if not room or (ask == "block" and stated is None):
        return _none_row("Block", ask), _ASK_BODY[ask]
    fact = _query_fact(hotel_id, room, night)
    if not fact:
        return [_list_row("Block", "none:" + night, night[:24], "No inventory on " + night, "quote", None)], _ASK_BODY[ask]
    result = inventory_manager.update_nightly_inventory(hotel_id, user_id, room, night, None, stated)
    detail = night + " blocked " + str(fact["blocked"]) + " to " + str(stated)
    if isinstance(result, dict) and result.get("error"):
        detail = str(result["error"])
    return [_list_row("Block", "block:" + night, room[:24], detail, "quote", None)], _ASK_BODY[ask]


def _ask_block(hotel_id, user_id, day, item):
    return _change_block(hotel_id, user_id, day, item, "block")


def _ask_open(hotel_id, user_id, day, item):
    return _change_block(hotel_id, user_id, day, item, "open")


_ASK_BUILDERS = {
    "set_price": _ask_set_price,
    "block": _ask_block,
    "open": _ask_open,
}


def _cell(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)


def _row_text(row):
    preferred = (
        "stay_date", "room_number", "guest_phone", "room_type", "assignment_status", "booking_id", "booking_status",
        "rooms_left", "left", "base_price", "price",
        "blocked_quantity", "rooms_booked", "total_inventory",
    )
    ordered = [key for key in preferred if key in (row or {})]
    ordered += [key for key in (row or {}) if key not in ordered and key not in ("hotel_name", "hotel_id")]
    parts = []
    for key in ordered:
        value = row.get(key)
        if value is None:
            continue
        shown = _cell(value)
        parts.append(shown if key in ("stay_date", "room_type") else key + " " + shown)
    return " ".join(parts)


def _rows_from_query(rows, error):
    if error and not rows:
        return [_list_row("Open", "none:query", "None", error, "quote", None)], error
    if not rows:
        return [_list_row("Open", "none:query", "None", "No rows returned", "quote", None)], "No rows returned"
    built = []
    for index, row in enumerate(rows[:10]):
        title = _cell(row.get("room_number") or row.get("stay_date") or row.get("room_type") or "Row")
        built.append(_list_row("Open", "row:" + str(index), title, _row_text(row), "quote", None))
    lines = [_row_text(row) for row in rows[:10]]
    return built, "\n".join(lines)


def reply_from_rows(rows):
    return "\n".join(_row_text(row) for row in rows or [] if row)


def availability_rows(hotel_id, day, item, user_id=None, dates=None):
    item = dict(item or {})
    ask = str(item.get("ask") or "rooms_left")
    if ask in _ASK_BUILDERS:
        rows, body = _ASK_BUILDERS[ask](hotel_id, user_id, day, item)
        return (rows or _none_row("Open", ask))[:10], body
    from booking_system.semantic.library import answer_prepared
    found = answer_prepared(hotel_id, "How many rooms are left?", {
        "page_ids": ["daily_rooms_left_by_room_type"],
        "resolved_dates": dates if isinstance(dates, dict) else {"resolutions": []},
    })
    return _rows_from_query(found.get("rows") or [], found.get("error") or "")


def _card(hotel_id, card, header, body, button, sections):
    hotel_name = _hotel_name(hotel_id)
    return {
        "hotel_name": hotel_name,
        "card": card,
        "header": _clip_list(header, 60),
        "body": _clip_list(body, 4096),
        "footer": _clip_list(hotel_name, 60),
        "button": _clip_list(button, 20),
        "sections": sections,
    }


def _agent_day(brief):
    """MM/DD/YYYY converted for a date column. Empty when the desk did not settle a day."""
    from booking_system.semantic.dates import as_iso, first_resolved
    return as_iso(first_resolved((brief or {}).get("dates")))


def availability_screen(hotel_id, brief, user_id=None):
    """The availability list. Rows come from the query agent; a price or block change is written by user_id."""
    day = _agent_day(brief)
    item = _availability_item(brief)
    ask = str(item.get("ask") or "")
    if brief.get("query_rows") is not None and ask not in _ASK_BUILDERS:
        rows, body = _rows_from_query(brief.get("query_rows") or [], brief.get("query_error") or "")
    else:
        rows, body = availability_rows(hotel_id, day, item, user_id, (brief or {}).get("dates"))
    sections = []
    for row in rows:
        title = row.get("section") or "Open"
        if not sections or sections[-1]["title"] != title:
            sections.append({"title": title, "rows": []})
        sections[-1]["rows"].append(row)
    return _card(hotel_id, "function", "Availability", body, "Availability", sections)


def read_screen(hotel_id, brief, header):
    """A catalog read that is not the availability or occupancy list."""
    rows, body = _rows_from_query(brief.get("query_rows") or [], brief.get("query_error") or "")
    title = _clip_list(header or "Read", 60) or "Read"
    return _card(hotel_id, "function", title, body, _clip_list(title, 20) or "Read", [{"title": _clip_list(title, 24) or "Read", "rows": rows}])


def occupancy_screen(hotel_id, brief):
    """Who is in which room. Rows come from the query agent."""
    rows, body = _rows_from_query(brief.get("query_rows") or [], brief.get("query_error") or "")
    return _card(hotel_id, "function", "Stays", body, "Stays", [{"title": "Stays", "rows": rows}])


def _rows_availability(hotel_id, phone, day, approvals, dates=None):
    rows, _body = availability_rows(hotel_id, day, {"ask": "rooms_left"}, dates=dates)
    return rows, "Availability"


def _rows_bookings(hotel_id, phone, day, approvals, section, action):
    return [_booking_choice(section, action, row, approvals) for row in _hotel_bookings(hotel_id, phone)], None


def _booking_draft(brief):
    for group in ("listed_actions", "executional_actions"):
        for row in (brief or {}).get(group) or []:
            if str((row or {}).get("action") or "") == "create_booking":
                return row or {}
    return {}


def _intake_rows(brief):
    """Fields still being collected for a booking. Empty once the booking can be written."""
    item = _booking_draft(brief)
    params = {field: _text(item, field) for field in PARAMS}
    if _complete("create_booking", params) and params.get("guest_phone"):
        return []
    fields = (
        ("guest_name", "Name"),
        ("guest_phone", "Phone"),
        ("rooms_booked", "Rooms"),
        ("stay_dates", "Dates"),
        ("special_request", "Request"),
    )
    return [_list_row("Details", "field:" + field, title, params.get(field) or "", "quote", None) for field, title in fields]


def _rows_types(hotel_id, phone, day, approvals):
    rows = [_list_row("Book", "type:" + name, name, "" if price is None else str(price), "quote", None) for name, price in _priced_types(hotel_id, day)]
    return rows[:10], None


def _rows_arrivals(hotel_id, phone, day, approvals):
    found = [row for row in _hotel_bookings(hotel_id, phone, ("confirmed",)) if _iso(row["first_night"]) == day]
    return [_booking_choice("Check in", "check_in", row, approvals) for row in found][:10], None


def _rows_departures(hotel_id, phone, day, approvals):
    from datetime import timedelta
    found = [row for row in _hotel_bookings(hotel_id, phone, ("checked_in",)) if (row["last_night"] + timedelta(days=1)).isoformat() == day]
    return [_booking_choice("Check out", "check_out", row, approvals) for row in found][:10], None


def _rows_rooms(hotel_id, phone, day, approvals):
    rows = []
    for row in _assigned_rooms(hotel_id, day):
        held = _held_for(approvals, "reassign_room", row["booking_id"])
        detail = " ".join([str(row["room_type"] or ""), str(row["booking_id"] or "")]).strip()
        rows.append(_list_row("Reassign", "room:" + row["room_number"], row["room_number"], detail, "approval" if held else "quote",
                              _plain_record(held) if held else None))
    return rows[:10], None


def _rows_payments(hotel_id, phone, day, approvals):
    rows = [_booking_choice("Payment", "record_payment", row, approvals) for row in _hotel_bookings(hotel_id, phone)[:4]]
    if not rows:
        rows.append(_list_row("Payment", "none:record_payment", "None", _LISTS["record_payment"][4], "quote", None))
    rows += [_list_row("Pay by", "method:" + method, method, "", "quote", None) for method in _METHODS]
    return rows[:10], None


def _rows_refunds(hotel_id, phone, day, approvals):
    rows = []
    for row in _open_payments(hotel_id):
        title = str(row["payment_method"] or "").strip() or str(row["amount"]) or "Refund"
        detail = " ".join([row["booking_id"], str(row["payment_seq"]), str(row["amount"])])
        held = _held_for(approvals, "record_refund", row["booking_id"])
        rows.append(_list_row("Refund", "pay:" + row["booking_id"] + ":" + str(row["payment_seq"]), title, detail,
                              "approval" if held else "quote", _plain_record(held) if held else None))
    return rows[:10], None


def _rows_profile(hotel_id, phone, day, approvals):
    from booking_system.guest_manager import guest_manager
    guest = (guest_manager.read_guest(hotel_id, phone) if phone else None) or {}
    shown = {"guest_name": guest.get("guest_name"), "email": guest.get("guest_email"), "country": guest.get("country"),
             "special_request": guest.get("special_request")}
    labels = (("guest_name", "Name"), ("email", "Email"), ("country", "Country"), ("special_request", "Request"))
    return [_list_row("Profile", field, title, str(shown[field] or ""), "quote", None) for field, title in labels], None


_ROW_BUILDERS = {
    "check_availability": _rows_availability,
    "cancel_booking": lambda hotel_id, phone, day, approvals: _rows_bookings(hotel_id, phone, day, approvals, "Cancel", "cancel_booking"),
    "create_booking": _rows_types,
    "modify_booking": lambda hotel_id, phone, day, approvals: _rows_bookings(hotel_id, phone, day, approvals, "Modify", "modify_booking"),
    "check_in": _rows_arrivals,
    "check_out": _rows_departures,
    "reassign_room": _rows_rooms,
    "record_payment": _rows_payments,
    "record_refund": _rows_refunds,
    "update_guest_profile": _rows_profile,
}


_POPULAR = (
    "check_availability",
    "create_booking",
    "check_in",
    "check_out",
    "modify_booking",
    "cancel_booking",
    "record_payment",
    "record_refund",
    "reassign_room",
    "update_guest_profile",
)


def menu_screen(hotel_id):
    """The list shown when the hotel user logs in. The interactive catalog is the navigation; the ten desk actions remain if it is empty."""
    from booking_system.interactive.agent import complete
    done = complete(hotel_id, "nav.home")
    if done:
        return done["screen"]
    rows = [_list_row("Actions", name, _LISTS[name][0], _LISTS[name][1], "menu", None) for name in _POPULAR]
    return _card(hotel_id, "menu", "Leon", "What should we do?", "Actions", [{"title": "Actions", "rows": rows}])


def open_action(hotel_id, name):
    """The database list for one catalog action, with fields still empty. A WhatsApp template key is completed from PostgreSQL."""
    from booking_system.interactive.agent import complete
    done = complete(hotel_id, name)
    if done:
        return done["screen"]
    action = str(name or "").strip()
    if action == "check_availability":
        return availability_screen(hotel_id, {"informational_actions": [{"action": "check_availability", "ask": "rooms_left"}]})
    if action not in _LISTS:
        return None
    brief = {"informational_actions": [], "listed_actions": [], "desk_tasks": []}
    slot = "informational_actions" if action in _INFORMATIONAL else "listed_actions"
    brief[slot] = [{"action": action}]
    return function_screen(hotel_id, brief, [])


def function_screen(hotel_id, brief, lines):
    """One WhatsApp list for the functions this turn called. Each row is a database read."""
    from booking_system.guest_manager import guest_manager
    called = _called_actions(brief)
    tasks = [str(task or "").strip() for task in ((brief or {}).get("desk_tasks") or []) if str(task or "").strip()]
    if not called and not tasks:
        return None
    day = _agent_day(brief)
    phone = _action_phone(brief)
    approvals, open_tasks = guest_manager.desk_queue(hotel_id)
    by_task = {str(row.get("task") or ""): row for row in open_tasks if row.get("task")}
    sections = []
    header = ""
    body = ""
    button = ""
    used = 0
    for name in called:
        spec = _LISTS.get(name)
        builder = _ROW_BUILDERS.get(name)
        if not spec or not builder or used >= 10:
            continue
        if name == "check_availability":
            built, button_name = _rows_availability(hotel_id, phone, day, approvals, (brief or {}).get("dates"))
        else:
            built, button_name = builder(hotel_id, phone, day, approvals)
        if name == "create_booking":
            details = _intake_rows(brief)
            if details:
                built = details + list(built or [])
                button_name = "Booking"
                spec = (spec[0], "What should the booking include?", "Booking", spec[3], spec[4])
        if not built:
            built = [_list_row(spec[3], "none:" + name, "None", spec[4], "quote", None)]
        if not header:
            header, body, button = spec[0], spec[1], button_name or spec[2]
        opened = False
        for row in built:
            if used >= 10:
                break
            title = row.get("section") or spec[3]
            if not opened or not sections or sections[-1]["title"] != title:
                if len(sections) >= 10:
                    break
                sections.append({"title": title, "rows": []})
                opened = True
            sections[-1]["rows"].append(row)
            used += 1
    if tasks and used < 10:
        if not header:
            header, body, button = "To do", "Which job should be done?", "Tasks"
        chunk = []
        for text in tasks:
            if used >= 10:
                break
            record = by_task.get(text)
            chunk.append(_list_row("To do", text, text[:24], text if len(text) > 24 else "", "task" if record else "quote",
                                   _plain_record(record) if record else None))
            used += 1
        if chunk:
            sections.append({"title": "To do", "rows": chunk})
    if not sections:
        return None
    return _card(hotel_id, "function", header, body, button, sections)


def catalog_text():
    """Action names and the fields the orchestrator must fill."""
    lines = ["check_availability is informational. ask is rooms_left, stay, sold_out, tight, price, set_price, block, or open."]
    lines.append("rooms_left, stay, sold_out, and tight use stay_dates only when resolutions settled a day. They do not invent a span and they do not use today.")
    lines.append("set_price needs room_type, stay_dates, and price. block needs room_type, stay_dates, and rooms. open needs room_type and stay_dates, and rooms is the blocked count to keep.")
    lines.append("stay_dates lists each confident night as MM/DD/YYYY, separated by commas. The checkout morning is not a night. Leave it empty when the span is not confident.")
    lines.append("Manual tasks are short lines such as send towel to room 111.")
    for name in _CHECKS:
        fields = list(_REQUIRED[name])
        extra = _ONE_OF.get(name) or ()
        if extra:
            fields.append("one of " + ", ".join(extra))
        lines.append(name + " is executional and requires " + ", ".join(fields))
    return "\n".join(lines)


def _complete(name, params):
    for field in _REQUIRED[name]:
        if not _text(params, field):
            return False
    extra = _ONE_OF.get(name) or ()
    if extra and not any(_text(params, field) for field in extra):
        return False
    return True


def _label(name, phone, params):
    title, fields = _LABELS[name]
    pieces = [title, phone]
    for field in fields:
        value = _text(params, field)
        if value:
            pieces.append(value)
    return " ".join(pieces)


def keep(proposed, hotel_id, phone, today):
    """Return actions whose parameters are complete and whose system read passed."""
    guest_phone = str(phone or "")
    kept = []
    seen = []
    for item in proposed or []:
        name = str((item or {}).get("action") or "")
        check = _CHECKS.get(name)
        if not check:
            continue
        params = {field: _text(item, field) for field in PARAMS}
        if name in _KNOWN_PHONE and not params["guest_phone"]:
            params["guest_phone"] = guest_phone
        params["payment_method"] = params["payment_method"].upper()
        params["room_type"] = _one_room_type(hotel_id, params["room_type"])
        params["new_room_type"] = _one_room_type(hotel_id, params["new_room_type"])
        if not _complete(name, params):
            continue
        try:
            allowed = check(hotel_id, guest_phone, params, today)
        except Exception:
            allowed = False
        if not allowed:
            continue
        label = _label(name, guest_phone, params)
        if label in seen:
            continue
        seen.append(label)
        row = {"action": name, "label": label}
        for field in PARAMS:
            if params[field]:
                row[field] = params[field]
        kept.append(row)
    return kept


def approval_names(hotel_id):
    """Action names this hotel holds for a person to approve."""
    rows = db.rows("SELECT action_name FROM desk_action_approval WHERE hotel_id = %s ORDER BY action_name", (hotel_id,))
    return [row["action_name"] for row in rows if row["action_name"] in _CHECKS]


def save_approval(hotel_id, user_id, action, required):
    """Read the hotel's approval list, then add or remove this action. Returns the list, or 'unknown'."""
    name = str(action or "")
    if name not in _CHECKS:
        return "unknown"

    def write(cur):
        cur.execute("SELECT 1 AS held FROM desk_action_approval WHERE hotel_id = %s AND action_name = %s FOR UPDATE", (hotel_id, name))
        held = cur.fetchone() is not None
        if required and not held:
            cur.execute("INSERT INTO desk_action_approval (hotel_id, action_name, set_by_user_id) VALUES (%s, %s, %s)", (hotel_id, name, user_id))
        if held and not required:
            cur.execute("DELETE FROM desk_action_approval WHERE hotel_id = %s AND action_name = %s", (hotel_id, name))
    db.transaction(write)
    return approval_names(hotel_id)


def read_information(proposed, hotel_id):
    """Availability is answered by the query agent. This path does not read."""
    return []


def _ok(result):
    if result is False or result is None:
        return False
    if result is True:
        return True
    if isinstance(result, dict):
        return not result.get("error")
    return not str(result or "").lstrip().startswith("❌")


def _failure_text(result):
    if isinstance(result, dict):
        return str(result.get("error") or "").strip()
    text = str(result or "").strip()
    if text.startswith("❌"):
        text = text[1:].strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines else ""


def _perform_cancel(hotel_id, user_id, params):
    from booking_system import booking_manager
    return booking_manager.cancel_booking(hotel_id, user_id, params["booking_id"], "desk")


def _perform_create(hotel_id, user_id, params):
    from booking_system import booking_manager
    from booking_system.guest_manager import guest_manager
    stay = booking_manager.stay_range(_dates(params.get("stay_dates")))
    if stay.get("error"):
        return stay
    held = guest_manager.read_guest(hotel_id, params["guest_phone"])
    guest = {"guest_name": held["guest_name"] if held else params.get("guest_name"), "phone": params["guest_phone"]}
    return booking_manager.create_booking(hotel_id, user_id, guest, params["room_type"], stay["check_in"], stay["check_out"],
                                          _rooms(params.get("rooms_booked")) or 1, _count(params.get("guest_count")) or 1, channel="PMS")


def _perform_modify(hotel_id, user_id, params):
    from booking_system import booking_manager
    changes = {}
    if params.get("new_stay_dates"):
        stay = booking_manager.stay_range(_dates(params["new_stay_dates"]))
        if stay.get("error"):
            return stay
        changes.update(stay)
    if params.get("new_room_type"):
        changes["room_type_name"] = params["new_room_type"]
    if params.get("new_rooms_booked"):
        changes["rooms"] = _seq(params["new_rooms_booked"])
    if _count(params.get("guest_count")):
        changes["adults"] = _count(params["guest_count"])
    return booking_manager.modify_booking(hotel_id, user_id, params["booking_id"], changes, "desk")


def _perform_check_in(hotel_id, user_id, params):
    from booking_system.front_desk_manager import intelligent_front_desk
    return intelligent_front_desk.update_guest_check_in_status_internal(hotel_id, user_id, _hotel(hotel_id)["timezone"], params["booking_id"],
                                                                        params["room_number"])


def _perform_check_out(hotel_id, user_id, params):
    from booking_system.front_desk_manager import intelligent_front_desk
    return intelligent_front_desk.update_guest_check_out_status_internal(hotel_id, user_id, params["booking_id"], params["room_number"])


def _perform_reassign(hotel_id, user_id, params):
    from booking_system.front_desk_manager import intelligent_front_desk
    return intelligent_front_desk.reassign_room_for_date_internal(hotel_id, user_id, params["booking_id"], params["stay_date"],
                                                                  params["old_room_number"], params["new_room_number"])


def _perform_payment(hotel_id, user_id, params):
    from booking_system import payment_manager
    amount = _amount(params.get("payment_amount"))
    if amount is None:
        return False
    return payment_manager.record_payment(hotel_id, user_id, params["booking_id"], amount, params["payment_method"])


def _perform_refund(hotel_id, user_id, params):
    from booking_system import payment_manager
    seq = _seq(params.get("payment_seq"))
    if seq is None:
        return False
    return payment_manager.record_refund(hotel_id, user_id, params["booking_id"], seq)


def _perform_profile(hotel_id, user_id, params):
    from booking_system.guest_manager import guest_manager
    fields = {key: params[key] for key in _PROFILE_FIELDS if params.get(key)}
    if "country" in fields:
        fields["country"] = fields["country"].upper()
        fields["nationality"] = fields["country"]
    if not fields:
        return False
    return guest_manager.update_guest_profile(hotel_id, user_id, params["guest_phone"], fields)


_PERFORM = {
    "cancel_booking": _perform_cancel,
    "create_booking": _perform_create,
    "modify_booking": _perform_modify,
    "check_in": _perform_check_in,
    "check_out": _perform_check_out,
    "reassign_room": _perform_reassign,
    "record_payment": _perform_payment,
    "record_refund": _perform_refund,
    "update_guest_profile": _perform_profile,
}


def settle(brief, hotel_id, user_id, guest):
    """Run the writes the hotel allows, hold the rest for approval. guest is the guest record (read_guest). Returns the guest
    document lists (approval_actions, carried_actions), the new desk task texts, and the result lines."""
    held_names = approval_names(hotel_id)
    carried = [item for item in ((guest or {}).get("carried_actions") or []) if isinstance(item, dict)]
    carried_labels = [str(item.get("label") or "") for item in carried]
    run = []
    hold = []
    for row in (brief or {}).get("executional_actions") or []:
        label = str((row or {}).get("label") or "")
        if not label or label in carried_labels:
            continue
        (hold if str(row.get("action") or "") in held_names else run).append(row)
    completed = []
    failed = []
    for row in run:
        perform = _PERFORM.get(str(row.get("action") or ""))
        try:
            result = perform(hotel_id, user_id, row) if perform else False
        except Exception as error:
            result = {"error": str(error)}
        if _ok(result):
            completed.append(row["label"])
            carried.append({"action": row["action"], "label": row["label"]})
            carried_labels.append(row["label"])
        else:
            reason = _failure_text(result)
            failed.append(row["label"] + (". " + reason if reason else ""))
    existing = [item for item in ((guest or {}).get("approval_actions") or []) if isinstance(item, dict)]
    existing_labels = [str(item.get("label") or "") for item in existing]
    for row in hold:
        if row["label"] not in existing_labels and row["label"] not in carried_labels:
            existing.append(row)
            existing_labels.append(row["label"])
    approval = [item for item in existing if str(item.get("label") or "") not in carried_labels]
    tasks = [str(text or "").strip() for text in (brief or {}).get("desk_tasks") or [] if str(text or "").strip()]
    lines = ["Completed " + label for label in completed]
    lines.extend("Failed " + label for label in failed)
    lines.extend("Waiting for approval " + str(row.get("label") or "") for row in hold)
    return {
        "informational": [],
        "completed": completed,
        "failed": failed,
        "approval_actions": approval,
        "desk_tasks": tasks,
        "carried_actions": carried,
        "lines": lines,
    }


def save_settled(hotel_id, user_id, guest, settled):
    """Write the guest document lists and open the new desk tasks as guest_request rows."""
    from booking_system.guest_manager import guest_manager
    guest_manager.write_guest_document(hotel_id, guest["guest_phone"], {
        "approval_actions": settled["approval_actions"], "carried_actions": settled["carried_actions"], "executable_actions": []})
    if settled["desk_tasks"]:
        guest_manager.add_requests(hotel_id, user_id, guest["guest_id"], settled["desk_tasks"])


def mark_task_done(hotel_id, user_id, phone, task):
    """Read the guest's open request and mark it done. 'updated' or 'missing'."""
    from booking_system.guest_manager import guest_manager
    result = guest_manager.close_request(hotel_id, user_id, phone, task)
    return "missing" if result.get("error") else "updated"


def hotel_names():
    """Names of the registered hotels."""
    return [row["hotel_name"] for row in db.rows("SELECT hotel_name FROM hotel ORDER BY hotel_name")]


def stays_for_phone(phone):
    """Every stay for this phone, at every hotel."""
    rows = db.rows(
        """SELECT h.hotel_id, h.hotel_name, s.booking_id, s.booking_status, s.check_in_date AS check_in, s.check_out_date - 1 AS last_night
           FROM guest_identity i JOIN booking_summary s ON s.guest_id = i.guest_id JOIN hotel h ON h.hotel_id = s.hotel_id
           WHERE i.identity_type = 'phone' AND i.identity_value = %s ORDER BY s.check_in_date""",
        (str(phone or "").strip(),))
    return [dict(row) for row in rows]


def _day(value):
    from booking_system.semantic.dates import as_date
    if hasattr(value, "year") and not hasattr(value, "hour"):
        return value
    return as_date(value)


def _bucket(stay, today):
    status = str(stay.get("booking_status") or "").lower()
    first = _day(stay.get("check_in"))
    last = _day(stay.get("last_night"))
    if status in ("cancelled", "no_show") or not first or not last or not today:
        return ""
    if first <= today <= last:
        return "in-house"
    if first > today:
        return "pre-arrival"
    return "departed"


def _pick_active(stage_name, rows):
    if stage_name == "pre-arrival":
        ordered = sorted(rows, key=lambda stay: str(_day(stay.get("check_in")) or ""))
        tied = len(ordered) > 1 and str(_day(ordered[0].get("check_in")) or "") == str(_day(ordered[1].get("check_in")) or "")
    elif stage_name == "departed":
        ordered = sorted(rows, key=lambda stay: str(_day(stay.get("last_night")) or ""), reverse=True)
        tied = len(ordered) > 1 and str(_day(ordered[0].get("last_night")) or "") == str(_day(ordered[1].get("last_night")) or "")
    else:
        ordered = list(rows)
        tied = len(ordered) > 1
    if len(ordered) == 1:
        return ordered[0], False
    if tied:
        return None, True
    return ordered[0], False


def stage_for_phone(phone):
    """pre-booking, pre-arrival, in-house, or departed, plus the active booking."""
    placed = []
    for stay in stays_for_phone(phone):
        bucket = _bucket(stay, hotel_today(stay["hotel_id"]))
        if bucket:
            placed.append((bucket, stay))
    for name in ("in-house", "pre-arrival", "departed"):
        rows = [stay for bucket, stay in placed if bucket == name]
        if not rows:
            continue
        active, ambiguous = _pick_active(name, rows)
        return {"stage": name, "active": active, "ambiguous": ambiguous, "choices": rows}
    return {"stage": "pre-booking", "active": None, "ambiguous": False, "choices": []}


def _stay_label(stay):
    first = _day(stay.get("check_in"))
    last = _day(stay.get("last_night"))
    return " ".join([
        str(stay.get("booking_id") or ""),
        str(stay.get("hotel_name") or ""),
        first.isoformat() if first else "",
        last.isoformat() if last else "",
    ]).strip()


def stage_text(stage, hotels):
    """Lines the model reads before it answers."""
    active = (stage or {}).get("active") or {}
    lines = ["Stage " + str((stage or {}).get("stage") or "pre-booking")]
    lines.append("Active booking " + (_stay_label(active) if active else "none"))
    choices = (stage or {}).get("choices") or []
    if (stage or {}).get("ambiguous"):
        lines.append("Bookings " + ", ".join(_stay_label(stay) for stay in choices))
    lines.append("Hotels " + ", ".join(hotels or []))
    return "\n".join(lines)


def resolve_hotel(stage, hotel_specific, hotel_name, hotels):
    """The hotel name for this turn. An ask means stop before any read or write."""
    known = [str(name or "").upper() for name in (hotels or []) if str(name or "").strip()]
    named = str(hotel_name or "").strip().upper()
    exact = named if named in known else ""
    if hotel_specific and not exact:
        return {"ask": "Which hotel? " + ", ".join(known)}
    active = (stage or {}).get("active") or {}
    active_hotel = str(active.get("hotel_name") or "").upper()
    if exact and exact == active_hotel:
        return {"hotel": exact, "booking_id": str(active.get("booking_id") or "")}
    if exact:
        return {"hotel": exact, "booking_id": ""}
    if (stage or {}).get("ambiguous"):
        return {"ask": "Which booking? " + ", ".join(_stay_label(stay) for stay in ((stage or {}).get("choices") or []))}
    if str((stage or {}).get("stage") or "") != "pre-booking" and active_hotel:
        return {"hotel": active_hotel, "booking_id": str(active.get("booking_id") or "")}
    if len(known) == 1:
        return {"hotel": known[0], "booking_id": ""}
    return {"ask": "Which hotel? " + ", ".join(known)}


def booking_phone(hotel_id, booking_id):
    """The phone of the guest on one booking, or ''."""
    found = db.row(
        """SELECT i.identity_value FROM booking b JOIN guest_identity i ON i.guest_id = b.guest_id AND i.identity_type = 'phone'
           WHERE b.hotel_id = %s AND b.booking_id = %s ORDER BY i.identity_value LIMIT 1""",
        (hotel_id, str(booking_id or "")))
    return found["identity_value"] if found else ""


def drop_approval(hotel_id, phone, label):
    """Read the guest and move one held action to carried after it was approved. 'updated' or 'missing'."""
    from booking_system.guest_manager import guest_manager
    guest = guest_manager.read_guest(hotel_id, phone)
    if not guest or guest["status"] != "active":
        return "missing"
    text = str(label or "").strip()
    rows = [item for item in (guest.get("approval_actions") or []) if isinstance(item, dict)]
    matched = [item for item in rows if str(item.get("label") or "") == text]
    if not matched:
        return "missing"
    carried = [item for item in (guest.get("carried_actions") or []) if isinstance(item, dict)]
    carried.extend({"action": item.get("action"), "label": item.get("label")} for item in matched)
    guest_manager.write_guest_document(hotel_id, phone, {"approval_actions": [item for item in rows if item not in matched],
                                                         "carried_actions": carried})
    return "updated"
