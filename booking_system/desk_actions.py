"""Python checks for front-desk actions. Each check reads the system before Execute is offered."""

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
    "email",
    "country",
    "special_request",
)


def _text(params, name):
    return str((params or {}).get(name) or "").strip()


def _dates(value):
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


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


def _rooms(value):
    text = str(value or "").strip()
    if not text:
        return 1
    return _seq(text)


def _with_pg(fn):
    from booking_system.guest_manager import guest_manager
    return guest_manager._with_pg(fn)


def _booking(hotel, phone, booking_id):
    def query(cur):
        cur.execute(
            """
            SELECT rb.booking_status, rb.room_type, MIN(bs.stay_date)
            FROM room_bookings rb
            LEFT JOIN booking_stays bs
              ON bs.booking_id = rb.booking_id AND bs.hotel_name = rb.hotel_name
            WHERE UPPER(rb.hotel_name) = %s AND rb.booking_id = %s AND rb.guest_phone = %s
            GROUP BY rb.booking_status, rb.room_type
            """,
            (hotel, booking_id, phone),
        )
        return cur.fetchone()
    return _with_pg(query)


def _stay_nights(hotel, booking_id):
    def query(cur):
        cur.execute(
            """
            SELECT stay_date FROM booking_stays
            WHERE UPPER(hotel_name) = %s AND booking_id = %s
              AND LOWER(COALESCE(booking_status, '')) = 'confirmed'
            ORDER BY stay_date
            """,
            (hotel, booking_id),
        )
        nights = []
        for row in cur.fetchall():
            night = row[0]
            nights.append(night.isoformat() if hasattr(night, "isoformat") else str(night)[:10])
        return nights
    return _with_pg(query)


def _nights_open(hotel, room_type, nights, rooms_needed):
    if not nights or not room_type:
        return False
    from booking_system.booking_manager import booking_manager
    needed = rooms_needed or 1
    for night in nights:
        report = booking_manager.read_availability(hotel, room_type, night, needed)
        if "AVAILABLE" not in report or "INSUFFICIENT" in report or "❌" in report:
            return False
    return True


def _room_row(hotel, room_number, stay_date):
    def query(cur):
        cur.execute(
            """
            SELECT room_type, booking_id
            FROM room_number_assignments
            WHERE UPPER(hotel_name) = %s AND room_number = %s AND stay_date = %s
            """,
            (hotel, room_number, stay_date),
        )
        return cur.fetchone()
    return _with_pg(query)


def _check_cancel(hotel, phone, params, today):
    row = _booking(hotel, phone, _text(params, "booking_id"))
    return bool(row) and str(row[0] or "").strip().lower() == "confirmed"


def _check_create(hotel, phone, params, today):
    if _text(params, "guest_phone") != phone:
        return False
    rooms = _rooms(params.get("rooms_booked"))
    if rooms is None:
        return False
    return _nights_open(hotel, _text(params, "room_type"), _dates(params.get("stay_dates")), rooms)


def _check_modify(hotel, phone, params, today):
    booking_id = _text(params, "booking_id")
    row = _booking(hotel, phone, booking_id)
    if not row or str(row[0] or "").strip().lower() != "confirmed":
        return False
    rooms = _rooms(params.get("new_rooms_booked"))
    if rooms is None:
        return False
    nights = _dates(params.get("new_stay_dates"))
    room_type = _text(params, "new_room_type") or str(row[1] or "").upper()
    if nights or _text(params, "new_room_type"):
        check_nights = nights or _stay_nights(hotel, booking_id)
        if not _nights_open(hotel, room_type, check_nights, rooms):
            return False
    return True


def _check_in(hotel, phone, params, today):
    row = _booking(hotel, phone, _text(params, "booking_id"))
    if not row or str(row[0] or "").strip().lower() != "confirmed":
        return False
    arrival = row[2].isoformat() if row[2] else ""
    if arrival != str(today or ""):
        return False
    room = _room_row(hotel, _text(params, "room_number"), arrival)
    if not room:
        return False
    same_type = str(room[0] or "").upper() == str(row[1] or "").upper()
    holder = str(room[1] or "")
    return same_type and (not holder or holder == _text(params, "booking_id"))


def _check_out(hotel, phone, params, today):
    booking_id = _text(params, "booking_id")
    room_number = _text(params, "room_number")
    if not _booking(hotel, phone, booking_id):
        return False

    def query(cur):
        cur.execute(
            """
            SELECT 1 FROM room_stay
            WHERE UPPER(hotel_name) = %s AND booking_id = %s AND room_number = %s
              AND LOWER(COALESCE(stay_status, '')) = 'checked_in'
            """,
            (hotel, booking_id, room_number),
        )
        return cur.fetchone()
    return bool(_with_pg(query))


def _check_reassign(hotel, phone, params, today):
    booking_id = _text(params, "booking_id")
    row = _booking(hotel, phone, booking_id)
    if not row:
        return False
    stay_date = _text(params, "stay_date")
    old_room = _room_row(hotel, _text(params, "old_room_number"), stay_date)
    new_room = _room_row(hotel, _text(params, "new_room_number"), stay_date)
    if not old_room or str(old_room[1] or "") != booking_id:
        return False
    if not new_room:
        return False
    same_type = str(new_room[0] or "").upper() == str(row[1] or "").upper()
    holder = str(new_room[1] or "")
    return same_type and (not holder or holder == booking_id)


def _check_payment(hotel, phone, params, today):
    row = _booking(hotel, phone, _text(params, "booking_id"))
    method = _text(params, "payment_method").upper()
    return bool(row) and str(row[0] or "").strip().lower() != "cancelled" and method in _METHODS and _amount(params.get("payment_amount")) is not None


def _check_refund(hotel, phone, params, today):
    booking_id = _text(params, "booking_id")
    seq = _seq(params.get("payment_seq"))
    if seq is None or not _booking(hotel, phone, booking_id):
        return False

    def query(cur):
        cur.execute(
            """
            SELECT LOWER(COALESCE(payment_status, ''))
            FROM payment_ledger
            WHERE UPPER(hotel_name) = %s AND booking_id = %s AND payment_seq = %s
            """,
            (hotel, booking_id, seq),
        )
        return cur.fetchone()
    found = _with_pg(query)
    return bool(found) and found[0] != "refunded"


def _check_profile(hotel, phone, params, today):
    if _text(params, "guest_phone") != phone:
        return False
    from booking_system.guest_manager import guest_manager
    return guest_manager.mongo_db["guests"].find_one({"guest_phone": phone, "status": "active"}) is not None


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


def action_names():
    return list(_CHECKS)


def catalog_text():
    """Action names and the fields the orchestrator must fill."""
    lines = []
    for name in _CHECKS:
        fields = list(_REQUIRED[name])
        extra = _ONE_OF.get(name) or ()
        if extra:
            fields.append("one of " + ", ".join(extra))
        lines.append(name + " requires " + ", ".join(fields))
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


def keep(proposed, hotel, phone, today):
    """Return actions whose parameters are complete and whose system read passed."""
    hotel_name = str(hotel or "").upper()
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
        params["room_type"] = params["room_type"].upper()
        params["new_room_type"] = params["new_room_type"].upper()
        if not _complete(name, params):
            continue
        try:
            allowed = check(hotel_name, guest_phone, params, today)
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
