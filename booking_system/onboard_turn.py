"""One chat turn that opens a hotel, from the account through the first booking."""
import json
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from booking_system.semantic.definitions import STANDARD, STYLE, manager_definitions, save_definition


_MODEL = "claude-haiku-5-5"
_DEFINITION_BY_FIELD = {"def_" + item["key"]: item for item in STANDARD}
_DEFINITION_FIELDS = tuple(_DEFINITION_BY_FIELD)
_FIELDS = (
    "hotel_name",
    "manager_name",
    "manager_phone",
    "manager_email",
    "role",
    "nationality",
    "primary_language",
    "hotel_timezone",
    "hotel_currency",
    "city_name",
    "room_type",
    "room_numbers",
    "nightly_price",
    "price_from",
    "price_to",
    "payment_percent",
    "guest_name",
    "guest_phone",
    "stay_night",
) + _DEFINITION_FIELDS
_STEPS = (
    ("account", ("manager_email", "manager_name", "role", "nationality", "primary_language")),
    ("hotel", ("hotel_name", "hotel_timezone", "hotel_currency")),
    ("rooms", ("room_type", "room_numbers", "nightly_price", "price_from", "price_to")),
    ("policies", ("payment_percent",)),
    ("settings", ()),
    ("definitions", _DEFINITION_FIELDS),
    ("booking", ("guest_name", "guest_phone", "stay_night")),
)
_NAMES = dict(_STEPS)
_UPPER = ("hotel_name", "manager_name", "hotel_currency", "city_name", "room_type", "guest_name")
_INSTRUCTIONS = (
    "You read a chat in which a person opens a hotel with Leon, and you fill in the fields. You do not write a reply. "
    "Copy a field only when the person stated that value or accepted the offer Leon just made. "
    "Copy every known value forward. Leave a field empty when it is not known. "
    "A short yes, yeah, ok, or sure accepts only the offer Leon just made. "
    "They may answer in passing, with a city, a time of day, or a currency in words. Store the value in the form below. "
    "Kuala Lumpur time is Asia/Kuala_Lumpur. Ringgit is MYR. 3 pm is 15:00. Noon is 12:00. 30 percent is 30. "
    "Tomorrow, a month ahead, and tomorrow night are the offered dates. "
    "A phone starts with + and the country code, with no spaces. "
    "hotel_name, manager_name, room_type, and guest_name are capitals. "
    "city_name is the city they named, in capitals. hotel_timezone is an IANA name for that city. hotel_currency is three letters. payment_percent is digits. "
    "A time of day is HH:MM. room_numbers are separated by commas. Dates are YYYY-MM-DD. "
    "Tomorrow and a month ahead are the offered dates. price_from and price_to are the first and last night the price covers. stay_night is the night the guest sleeps. "
    "When the person mentions several values, copy each of them. "
    "Do not invent a phone, an email, a hotel, a guest, a date, or a code. "
    "otp_phone and otp_email are codes. Copy otp_phone only when the person's latest message is the WhatsApp code, and otp_email only when it is the email code. "
    "Leave both empty when that message is not a code. Never copy a code from earlier in the chat. "
    "The def_ fields are what this manager means when asking Leon about arrivals, in-house guests, departures, and reservations. "
    "A def_ field holds the offer they accepted, or what they mean. "
    "The set_ fields are how this hotel operates, one per setting question listed under Settings. They have no offer: copy only what "
    "the person said about their own hotel, as one of the listed values; several values are separated by commas. "
    "A sop_ field holds, in the person's own words, how their staff carry out that setting, when they described it."
)
_SETTING, _SOP = "set_", "sop_"
_REWRITE = (
    "You are Leon, opening a hotel with the person in this chat. Speak like a person, in one or two short sentences. "
    "Do not name the field. " + STYLE + " "
    "Results lists what the system wrote this turn; it is the only record of what happened. "
    "Say that something is saved, stored, set up, created, booked or open only when a Results line says Saved or Open. "
    "When Results says nothing was saved this turn, do not say or imply that anything was saved; thank them at most, then ask. "
    "When a result says Saved, say what is now in place, then ask for the next field. "
    "Ask for the next field in your own words, keeping the meaning of its question. When it has an offer, offer that value the way you would say it. "
    "Before an account exists, answer what they just said first. Use their name once you know it. "
    "Say in a few words why the next thing matters. The email receives the Leon code before any manager profile is created. "
    "WhatsApp is collected later and is not confirmed in this chat. "
    "When a result says OTP sent, tell them a code was sent to that WhatsApp or email and ask them to share it here to confirm. "
    "When a result says OTP waiting, ask again for the code already sent. "
    "When a result says OTP failed, say the reason in that line. "
    "When a result says OTP confirmed, say that contact is confirmed, then ask the next question. "
    "Never invent a code and never repeat a code. "
    "When a result says Failed or Already, say it was not done and include the reason. Do not ask for a later step. "
    "When a result says Open, say the hotel is open and include the booking id from the result. Do not ask another question."
)


@lru_cache(maxsize=1)
def _catalog():
    """The setting catalog rows (seeded at startup, fixed for the process)."""
    from booking_system.settings import service
    return tuple(service.catalog_rows())


def _setting_fields():
    """{field: catalog row} for every set_ and sop_ field."""
    found = {_SETTING + row["setting_key"]: row for row in _catalog()}
    found.update({_SOP + row["setting_key"]: row for row in _catalog() if row["sop_prompt"]})
    return found


def _all_fields():
    return _FIELDS + tuple(_setting_fields())


def _property(name):
    """A set_ field takes its catalog row's value form; every other field is free text."""
    from booking_system.settings import service
    row = _setting_fields().get(name) if name.startswith(_SETTING) else None
    return service.text_property(row) if row else {"type": "string"}


def _schema():
    names = _all_fields()
    properties = {name: _property(name) for name in names}
    properties["otp_phone"] = {"type": "string"}
    properties["otp_email"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": list(names) + ["otp_phone", "otp_email"],
        "additionalProperties": False,
    }


def _clean(fields):
    stored = {}
    for name in _all_fields():
        stored[name] = str((fields or {}).get(name) or "").strip()
    for name in _UPPER:
        stored[name] = stored[name].upper()
    stored["manager_phone"] = "".join(stored["manager_phone"].split())
    stored["manager_email"] = stored["manager_email"].lower()
    stored["guest_phone"] = "".join(stored["guest_phone"].split())
    return stored


def _offers(today):
    tomorrow = today + timedelta(days=1)
    return {
        "role": "Hotel Manager",
        "nationality": "Malaysian",
        "primary_language": "English",
        "hotel_timezone": "Asia/Kuala_Lumpur",
        "hotel_currency": "MYR",
        "payment_percent": "30",
        "price_from": tomorrow.isoformat(),
        "price_to": (tomorrow + timedelta(days=30)).isoformat(),
        "stay_night": tomorrow.isoformat(),
        **{field: item["offer"] for field, item in _DEFINITION_BY_FIELD.items()},
    }


def _numbers(value):
    found = []
    for part in str(value or "").split(","):
        key = part.strip().upper()
        if key and key not in found:
            found.append(key)
    return found


def _nights(start, end):
    try:
        first = datetime.strptime(str(start or ""), "%Y-%m-%d").date()
        last = datetime.strptime(str(end or ""), "%Y-%m-%d").date()
    except ValueError:
        return []
    if last < first:
        return []
    days = []
    cursor = first
    while cursor <= last:
        days.append(cursor.isoformat())
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return days


def _missing(fields, names):
    return [name for name in names if not str(fields.get(name) or "").strip()]


def _hotel_row(hotel_name):
    from booking_system.hotel_registry import read_hotel
    return (read_hotel(hotel_name) if hotel_name else None) or {}


def _account_by_phone(fields):
    """The person holding this WhatsApp number: user_id, user_name, profile language and nationality, and their hotels; or None."""
    from booking_system import db
    from booking_system.user_manager import find_user, hotels_of, profile_col
    phone = str(fields.get("manager_phone") or "").strip()
    if not phone:
        return None

    def read(cur):
        user = find_user(cur, "phone", phone)
        return dict(user, hotels=hotels_of(cur, user["user_id"])) if user else None
    user = db.transaction(read)
    if not user:
        return None
    profile = profile_col.find_one({"user_id": user["user_id"]}) or {}
    first = user["hotels"][0] if user["hotels"] else {}
    return {"user_id": user["user_id"], "manager_name": user["user_name"], "manager_phone": phone, "hotels": user["hotels"],
            "hotel_name": first.get("hotel_name") or "", "role": first.get("role_name") or "",
            "nationality": profile.get("nationality") or "", "primary_language": profile.get("primary_language") or ""}


def _account_doc(fields, owner):
    """The account on this phone only when it is the signed-in person (owner user_id); a phone alone proves nothing."""
    doc = _account_by_phone(fields) if owner else None
    return doc if doc and doc["user_id"] == owner else None


def _account_exact(doc, fields):
    return str(doc.get("manager_phone") or "") == fields["manager_phone"] and str(doc.get("manager_name") or "").upper() == fields["manager_name"]


def _hotel_of(doc, hotel_name):
    """The membership row of this account at this hotel, or None."""
    return next((row for row in (doc or {}).get("hotels") or [] if row["hotel_name"] == hotel_name), None)


def _recall(fields, owner):
    """Fill empty account and hotel values from the signed-in person's profile."""
    doc = _account_doc(fields, owner)
    if not doc:
        return fields
    stored_name = str(doc.get("manager_name") or "").upper()
    if fields["manager_name"] and fields["manager_name"] != stored_name:
        return fields
    for name in ("manager_name", "role", "nationality", "primary_language", "hotel_name"):
        if not fields[name] and doc.get(name):
            fields[name] = str(doc.get(name) or "")
    row = _hotel_row(fields["hotel_name"])
    for name, column in (("hotel_timezone", "timezone"), ("hotel_currency", "currency")):
        if not fields[name] and row.get(column):
            fields[name] = row[column]
    fields.update({name: value for name, value in _profile_definitions(doc["user_id"]).items() if not fields[name]})
    return _clean(fields)


def _profile_definitions(user_id):
    """def_ fields already answered on this user's profile."""
    known = {item["key"]: item["definition"] for item in manager_definitions(user_id)} if user_id else {}
    return {field: known[item["key"]] for field, item in _DEFINITION_BY_FIELD.items() if known.get(item["key"])}


def _otp_result(state, place, renewed):
    """(ready, line) for a send result. A send error is the line Leon can say."""
    if state == "sent" and renewed:
        return False, "OTP sent. A new code was sent to their " + place + "."
    if state == "sent":
        return False, "OTP sent. A code was sent to their " + place + "."
    if state == "waiting":
        return False, "OTP waiting. A code was already sent to their " + place + "."
    if state == "invalid":
        return False, "OTP failed. That address is not usable."
    return False, "OTP failed. " + state


def _code_step(channel, address_value, given):
    """(ready, line). Ready once this address is confirmed. A live code that does not match is a miss; otherwise a code is sent."""
    from booking_system import contact_otp
    if contact_otp.confirmed(channel, address_value):
        return True, ""
    place = "WhatsApp" if channel == "whatsapp" else "email"
    noun = "WhatsApp number" if channel == "whatsapp" else "email"
    typed = str(given or "").strip()
    if typed and contact_otp.check(channel, address_value, typed):
        return True, "OTP confirmed. Their " + noun + " is confirmed."
    if typed and contact_otp.exhausted(channel, address_value):
        return _otp_result(contact_otp.issue(channel, address_value), place, True)
    state = contact_otp.issue(channel, address_value)
    if typed and state == "waiting":
        return False, "OTP failed. That code did not match."
    return _otp_result(state, place, False)


def _write_account(fields, owner, otp=None):
    """READ the email first. The Leon code confirms it before a manager profile is created. An existing person then signs in."""
    from booking_system import contact_otp, user_manager
    email = fields["manager_email"]
    phone = fields["manager_phone"]
    if not email:
        return "missing", "", {}
    note = ""
    if not contact_otp.confirmed("email", email):
        ready, note = _code_step("email", email, (otp or {}).get("email"))
        if not ready:
            return "clarify", note, {}
    if owner:
        held = _account_doc(fields, owner)
        if held and held["user_id"] == owner:
            return "saved", note, {"user_id": owner}
    doc = _account_by_phone(fields) if phone else None
    if doc:
        if owner and doc["user_id"] != owner:
            return "failed", "Failed account. That WhatsApp number belongs to another account.", {}
        if not fields["manager_name"]:
            fields["manager_name"] = doc["manager_name"]
        signed = user_manager.session_for(doc["user_id"])
        if signed.get("error"):
            return "failed", "Failed account. " + signed["error"], {}
        line = (note + " " if note else "") + "Saved account. " + fields["manager_name"] + " is signed in on " + phone + "."
        return "saved", line.strip(), {"token": signed.get("token") or "", "user_id": doc["user_id"]}
    if _missing(fields, _STEPS[0][1]):
        return "missing", note, {}
    identities = {"email": email}
    if phone:
        identities["phone"] = phone
    created = user_manager.signup(fields["manager_name"], identities, None,
                                  fields["primary_language"], fields["nationality"])
    if created.get("error"):
        return "failed", "Failed account. " + created["error"], {}
    signed = user_manager.session_for(created["user_id"])
    if signed.get("error"):
        return "failed", "Failed account. " + signed["error"], {}
    signed_on = email + (" and " + phone if phone else "")
    line = " ".join(part for part in (note, "Saved account. " + fields["manager_name"] + " is signed in on " + signed_on + ".") if part)
    return "saved", line, {"token": signed.get("token") or "", "user_id": created["user_id"]}


def _write_hotel(fields, owner):
    from booking_system import user_manager
    if _missing(fields, _STEPS[1][1]):
        return "missing", "", {}
    doc = _account_doc(fields, owner)
    if not doc:
        return "missing", "", {}
    member = _hotel_of(doc, fields["hotel_name"])
    if member:
        issued = user_manager.token_for(doc["user_id"], member["hotel_id"]) or {}
        return "saved", "", {"token": issued.get("token") or "", "hotel_name": fields["hotel_name"]}
    if _hotel_row(fields["hotel_name"]):
        return "clarify", "Already on record. Hotel " + fields["hotel_name"] + " belongs to another account.", {}
    created = user_manager.create_hotel(doc["user_id"], fields["hotel_name"], fields["hotel_currency"], fields["hotel_timezone"], fields["city_name"])
    if created.get("error"):
        return "failed", "Failed hotel. " + created["error"], {}
    issued = user_manager.token_for(doc["user_id"], created["hotel_id"]) or {}
    line = "Saved hotel. " + fields["hotel_name"] + " is on this account."
    return "saved", line, {"token": issued.get("token") or "", "hotel_name": created["hotel_name"]}


def _owner(fields, owner):
    """(hotel row, user_id) of the signed-in account at the hotel being opened; ({}, None) before both exist."""
    doc = _account_doc(fields, owner)
    row = _hotel_row(fields["hotel_name"])
    if not doc or not row or not _hotel_of(doc, fields["hotel_name"]):
        return {}, None
    return row, doc["user_id"]


def _price_exists(hotel_id, room_type, day):
    from booking_system.inventory_manager import night_on_file
    if not day:
        return False
    try:
        night = night_on_file(hotel_id, room_type, day)
    except ValueError:
        return False
    return bool(night) and night["price"] is not None


def _room_numbers_by_type(hotel_id):
    from booking_system.room_manager import read_portfolio
    return {row["room_type"]: [room["room_number"] for room in row["room_numbers"]] for row in read_portfolio(hotel_id)["room_types"]}


def _write_rooms(fields, owner):
    from booking_system import inventory_manager, room_manager
    hotel, user_id = _owner(fields, owner)
    if not user_id:
        return "missing", "", {}
    room_type = fields["room_type"]
    numbers = _numbers(fields["room_numbers"])
    if not room_type and not numbers:
        return "missing", "", {}
    by_type = _room_numbers_by_type(hotel["hotel_id"])
    base = room_type in by_type
    owned = by_type.get(room_type) or []
    other = [number + " " + kind for kind, rooms in by_type.items() if kind != room_type for number in rooms if number in numbers]
    same = base and bool(numbers) and set(numbers) == set(owned)
    if same and _price_exists(hotel["hotel_id"], room_type, fields["price_from"]):
        return "saved", "", {}
    if other or (base and numbers and not same):
        listed = ", ".join(owned) or "none"
        extra = (" Also used: " + ", ".join(other)) if other else ""
        return "clarify", "Already on record. " + room_type + " rooms at " + fields["hotel_name"] + ": " + listed + "." + extra, {}
    if _missing(fields, _NAMES["rooms"]):
        return "missing", "", {}
    if not _nights(fields["price_from"], fields["price_to"]):
        return "failed", "Failed rooms. The price dates are not a range of nights.", {}
    try:
        price = float(fields["nightly_price"])
    except ValueError:
        return "failed", "Failed rooms. The nightly price is not a number.", {}
    if not base:
        created = room_manager.create_room_type(hotel["hotel_id"], user_id, hotel["timezone"], room_type, {}, numbers, price)
        if created.get("error"):
            return "failed", "Failed rooms. " + created["error"], {}
    priced = inventory_manager.set_prices(hotel["hotel_id"], user_id, room_type, fields["price_from"], fields["price_to"], price)
    if priced.get("error"):
        return "failed", "Failed rooms. " + priced["error"], {}
    return "saved", "Saved rooms. Room number " + ", ".join(numbers) + ", type " + room_type + ", at " + str(price) + " each night.", {}


def _booking_doc(hotel_id, fields):
    """The live booking of this guest phone, room type and night at the hotel, or None."""
    from booking_system import db
    from booking_system.user_manager import normalize
    return db.row(
        """SELECT DISTINCT b.booking_id, g.guest_name FROM booking b
           JOIN guest g ON g.guest_id = b.guest_id JOIN guest_identity i ON i.guest_id = g.guest_id AND i.identity_type = 'phone'
           JOIN booking_room_night n ON n.booking_id = b.booking_id JOIN room_type t ON t.room_type_id = n.room_type_id
           WHERE b.hotel_id = %s AND i.identity_value = %s AND t.room_type_name = %s AND n.stay_date = %s
             AND b.booking_status NOT IN ('cancelled', 'no_show')""",
        (hotel_id, normalize("phone", fields["guest_phone"]), fields["room_type"], fields["stay_night"]),
    )


def _write_booking(fields, owner):
    from booking_system import booking_manager
    from booking_system.guest_manager import guest_manager
    hotel, user_id = _owner(fields, owner)
    if not user_id:
        return "missing", "", {}
    if not fields["guest_phone"] and not fields["guest_name"]:
        return "missing", "", {}
    guest = guest_manager.read_guest(hotel["hotel_id"], fields["guest_phone"]) if fields["guest_phone"] else None
    if guest and fields["guest_name"] and str(guest.get("guest_name") or "").upper() != fields["guest_name"]:
        return "clarify", "Already on record. Guest " + fields["guest_phone"] + " is " + str(guest.get("guest_name") or "") + " at " + fields["hotel_name"] + ".", {}
    existing = _booking_doc(hotel["hotel_id"], fields) if fields["guest_phone"] and fields["stay_night"] and fields["room_type"] else None
    if existing:
        return "saved", "", {"booking_id": existing["booking_id"]}
    if _missing(fields, _NAMES["booking"]):
        return "missing", "", {}
    try:
        night = datetime.strptime(fields["stay_night"], "%Y-%m-%d").date()
    except ValueError:
        return "failed", "Failed booking. The stay night is not a date.", {}
    result = booking_manager.create_booking(
        hotel["hotel_id"], user_id, {"guest_name": fields["guest_name"], "phone": fields["guest_phone"], "nationality": fields["nationality"]},
        fields["room_type"], night.isoformat(), (night + timedelta(days=1)).isoformat(),
    )
    if result.get("error"):
        return "failed", "Failed booking. " + result["error"], {}
    booking_id = result["booking_id"]
    return "saved", "Saved booking. " + booking_id + " is confirmed for " + fields["stay_night"] + ".", {"booking_id": booking_id}


def _write_definitions(fields, owner):
    """Read the user_profile document first. A new or changed meaning is saved on it; a matching one is left alone."""
    doc = _account_doc(fields, owner)
    if not doc:
        return "missing", "", {}
    stored = _profile_definitions(doc["user_id"])
    changed = [name for name in _DEFINITION_FIELDS if fields[name] and fields[name] != stored.get(name)]
    for name in changed:
        result = save_definition(doc["user_id"], _DEFINITION_BY_FIELD[name]["key"], fields[name], "onboarding")
        if result.get("error"):
            return "failed", "Failed definitions. " + str(result["error"]), {}
    if _missing(fields, _DEFINITION_FIELDS):
        return "missing", "", {}
    if not changed:
        return "saved", "", {}
    said = "; ".join(_DEFINITION_BY_FIELD[name]["label"] + ": " + fields[name] for name in changed)
    return "saved", "Saved definitions. " + said + ".", {}


def _already(fields, owner):
    """Steps already stored for the signed-in person. This reads and does not write."""
    saved = []
    doc = _account_doc(fields, owner)
    if not doc or not _account_exact(doc, fields):
        return saved, ""
    saved.append("account")
    hotel_on_file = fields["hotel_name"] or doc["hotel_name"]
    if not hotel_on_file or not _hotel_of(doc, hotel_on_file):
        return saved, ""
    saved.append("hotel")
    hotel = _hotel_row(hotel_on_file)
    room_type = fields["room_type"]
    numbers = _numbers(fields["room_numbers"])
    if not room_type or not numbers:
        return saved, ""
    owned = _room_numbers_by_type(hotel["hotel_id"]).get(room_type)
    if owned is None or set(numbers) != set(owned) or not _price_exists(hotel["hotel_id"], room_type, fields["price_from"]):
        return saved, ""
    saved.append("rooms")
    if not _policies_on_file(hotel["hotel_id"]):
        return saved, ""
    saved.append("policies")
    if _setting_asks(fields):
        return saved, ""
    saved.append("settings")
    if _missing(_profile_definitions(doc["user_id"]), _DEFINITION_FIELDS):
        return saved, ""
    saved.append("definitions")
    existing = _booking_doc(hotel["hotel_id"], fields) if fields["guest_phone"] and fields["stay_night"] else None
    if not existing:
        return saved, ""
    saved.append("booking")
    return saved, existing["booking_id"]


def _priced_span(hotel_id, room_type):
    from booking_system import db
    row = db.row(
        """SELECT MIN(p.stay_date) AS first, MAX(p.stay_date) AS last, MAX(p.base_price) AS price FROM daily_pricing p
           JOIN room_type t ON t.room_type_id = p.room_type_id WHERE p.hotel_id = %s AND t.room_type_name = %s AND p.base_price > 0""",
        (hotel_id, room_type),
    )
    if not row or not row["first"]:
        return "", "", ""
    return row["first"].isoformat(), row["last"].isoformat(), _money(row["price"])


def _fields_from_hotel(hotel_id):
    """Read the hotel that is already open: its general manager, first room type, policies and first booking."""
    from booking_system import db
    from booking_system.hotel_registry import read_hotel_by_id
    from booking_system.policy_manager import read_policy
    from booking_system.user_manager import profile_col
    row = read_hotel_by_id(hotel_id)
    if not row:
        return None, "", None
    manager = db.row(
        """SELECT a.user_id, a.user_name, r.role_name,
                  (SELECT i.identity_value FROM user_identity i WHERE i.user_id = a.user_id AND i.identity_type = 'phone' LIMIT 1) AS phone
           FROM hotel_user u JOIN access_role r ON r.role_id = u.role_id JOIN user_account a ON a.user_id = u.user_id
           WHERE u.hotel_id = %s AND r.role_name = 'GENERAL_MANAGER' ORDER BY u.activated_at LIMIT 1""",
        (hotel_id,),
    )
    if not manager:
        return None, "", None
    profile = profile_col.find_one({"user_id": manager["user_id"]}) or {}
    fields = _clean({
        "hotel_name": row["hotel_name"],
        "manager_name": manager["user_name"],
        "manager_phone": manager["phone"],
        "role": manager["role_name"],
        "nationality": profile.get("nationality"),
        "primary_language": profile.get("primary_language"),
        "hotel_timezone": row.get("timezone"),
        "hotel_currency": row.get("currency"),
        **_profile_definitions(manager["user_id"]),
    })
    owned = {kind: rooms for kind, rooms in _room_numbers_by_type(hotel_id).items() if rooms}
    chosen = min(owned) if owned else ""
    if chosen:
        start, end, price = _priced_span(hotel_id, chosen)
        fields["room_type"] = chosen
        fields["room_numbers"] = ", ".join(owned[chosen])
        fields["price_from"] = start
        fields["price_to"] = end
        fields["nightly_price"] = price
    payment_details = (read_policy(hotel_id, "payment") or {}).get("details") or {}
    fields.update(_stated_settings(hotel_id))
    percent = payment_details.get("payment_threshold_percent")
    fields["payment_percent"] = _money(percent) if percent is not None else ""
    booking = db.row(
        """SELECT b.booking_id, g.guest_name, b.check_in_date, t.room_type_name,
                  (SELECT i.identity_value FROM guest_identity i WHERE i.guest_id = g.guest_id AND i.identity_type = 'phone' LIMIT 1) AS phone
           FROM booking_summary b JOIN guest g ON g.guest_id = b.guest_id
           LEFT JOIN LATERAL (SELECT n.room_type_id FROM booking_room_night n WHERE n.booking_id = b.booking_id LIMIT 1) n ON TRUE
           LEFT JOIN room_type t ON t.room_type_id = n.room_type_id
           WHERE b.hotel_id = %s ORDER BY b.booked_at LIMIT 1""",
        (hotel_id,),
    )
    booking_id = ""
    if booking:
        fields["guest_name"] = booking["guest_name"]
        fields["guest_phone"] = booking["phone"] or ""
        fields["stay_night"] = booking["check_in_date"].isoformat()
        if not fields["room_type"]:
            fields["room_type"] = booking["room_type_name"] or ""
        booking_id = booking["booking_id"]
    return _clean(fields), booking_id, manager["user_id"]


def _policies_on_file(hotel_id):
    from booking_system.policy_manager import read_policy
    return all(read_policy(hotel_id, kind) for kind in ("booking", "payment"))


def _write_policies(fields, owner):
    """READ the hotel's booking and payment policies; create the missing ones from the stated times and deposit."""
    from booking_system.policy_manager import create_policy, read_policy
    hotel, user_id = _owner(fields, owner)
    if not user_id:
        return "missing", "", {}
    hotel_id = hotel["hotel_id"]
    have_booking = read_policy(hotel_id, "booking") is not None
    have_payment = read_policy(hotel_id, "payment") is not None
    if have_booking and have_payment:
        return "saved", "", {}
    if _missing(fields, _NAMES["policies"]):
        return "missing", "", {}
    try:
        percent = float(fields["payment_percent"])
    except ValueError:
        return "failed", "Failed policies. The payment percent is not a number.", {}
    wanted = []
    if not have_booking:
        wanted.append(("booking", {"cancellation_policy": {"window_hours": 48, "penalty_nights": 1, "refund_percent": 50}}))
    if not have_payment:
        wanted.append(("payment", {"payment_threshold_percent": percent, "payment_window_days": 7, "methods_allowed": ["CASH"]}))
    for kind, details in wanted:
        created = create_policy(hotel_id, user_id, kind, details)
        if created.get("error"):
            return "failed", "Failed policies. " + created["error"], {}
    return "saved", "Saved policies. Deposit " + str(percent) + " percent.", {}


def _stated_settings(hotel_id):
    """set_ fields from the values the hotel has in effect."""
    from booking_system.settings import service
    types = {row["setting_key"]: row["value_type"] for row in _catalog()}
    return {_SETTING + key: service.TO_TEXT[types[key]](value) for key, value in service.values_in_effect(hotel_id).items() if key in types}


def _setting_asks(fields):
    """set_ fields the hotel being opened still has to answer: each module's yes/no first, then what an enabled module needs."""
    from booking_system.settings import service
    hotel = _hotel_row(fields.get("hotel_name"))
    return [_SETTING + key for key in service.asks(hotel["hotel_id"])] if hotel else []


def _write_settings(fields, owner):
    """READ the catalog and each value in effect (write_setting); record every stated value that differs, effective on the hotel's
    operating date, with its SOP text, in one transaction. The step is done when no module has a question left."""
    from booking_system import db
    from booking_system.settings import service
    hotel, user_id = _owner(fields, owner)
    if not user_id:
        return "missing", "", {}
    stated = [row for row in _catalog() if fields.get(_SETTING + row["setting_key"])]

    def run(cur):
        said = []
        for row in stated:
            key = row["setting_key"]
            on = service.earliest_date(cur, hotel["hotel_id"], row["effective_basis"])
            done = service.write_setting(cur, hotel["hotel_id"], user_id, key, service.FROM_TEXT[row["value_type"]](fields[_SETTING + key]), on,
                                         "onboarding", "", fields.get(_SOP + key))
            if done["result"] == "saved" or done["sop_saved"]:
                said.append(key + " = " + (done["meaning"] or json.dumps(done["value"])) + " from " + done["effective_from"]
                            + (" with its procedure" if done["sop_saved"] else ""))
        return said
    try:
        said = db.transaction(run)
    except (service.Refused, ValueError) as refusal:
        return "failed", "Failed settings. " + str(refusal), {}
    line = ("Saved settings. " + "; ".join(said) + ".") if said else ""
    if _setting_asks(fields):
        return "missing", line, {}
    later = service.waiting(hotel["hotel_id"])
    line += "".join(" Not asked yet: " + item["setting_key"] + " (" + item["question"] + ") needs " + item["waits_for"]
                    + "; that module stays refused until it is answered." for item in later)
    return "saved", line.strip(), {}


_WRITERS = {
    "account": _write_account,
    "hotel": _write_hotel,
    "rooms": _write_rooms,
    "policies": _write_policies,
    "settings": _write_settings,
    "definitions": _write_definitions,
    "booking": _write_booking,
}
# Steps whose questions depend on what is already stored rather than a fixed field list.
_ASKED = {"settings": _setting_asks}


def _apply(fields, owner, otp=None):
    """Run each step in order as the signed-in person; the account step may sign them in for the steps after it."""
    lines = []
    saved = []
    extra = {}
    for step, _names in _STEPS:
        if step == "account":
            outcome, line, more = _write_account(fields, extra.get("user_id") or owner, otp)
        else:
            outcome, line, more = _WRITERS[step](fields, extra.get("user_id") or owner)
        if more:
            extra.update(more)
        if outcome == "saved":
            saved.append(step)
            if line:
                lines.append(line)
            continue
        if line:
            lines.append(line)
        break
    if lines and len(saved) == len(_STEPS):
        lines.append("Open. Booking " + str(extra.get("booking_id") or "") + " at " + fields["hotel_name"] + ".")
    return lines, saved, extra


def _public(fields, saved):
    return {name: fields[name] for name in _all_fields()}


def _step_names(step, names, fields):
    return _ASKED[step](fields) if step in _ASKED else names


def _setting_lines():
    """The Settings block of the extraction prompt: each set_ field with its question and allowed values, each sop_ field with its prompt."""
    from booking_system.settings import service
    lines = ["Settings"]
    for row in _catalog():
        lines.append(_SETTING + service.describe_values(row))
        if row["sop_prompt"]:
            lines.append(_SOP + row["setting_key"] + ": " + row["sop_prompt"])
    return lines


def _prompt(message, history, fields, offers, saved, booking_id):
    lines = ["Today " + date.today().isoformat(), "Known"]
    for name in _all_fields():
        lines.append(name + ": " + fields.get(name, ""))
    lines.append("Offers")
    for name, value in offers.items():
        lines.append(name + ": " + value)
    lines.extend(_setting_lines())
    lines.append("Saved: " + ", ".join(saved))
    gap = _next_name(fields, saved)
    lines.append("Next field: " + gap)
    lines.append("Booking id: " + str(booking_id or ""))
    lines.append("The booking policy uses a 48 hour cancellation window, a one night penalty, and a 50 percent refund. Payment is due in 7 days by cash.")
    lines.append("Conversation")
    for item in list(history or [])[-16:]:
        speaker = "Person" if str((item or {}).get("role") or "") == "person" else "Leon"
        lines.append(speaker + ": " + str((item or {}).get("text") or ""))
    lines.append("Person: " + str(message or ""))
    return "\n".join(lines)


_QUESTIONS = {
    "hotel_name": "What's the hotel called?",
    "manager_name": "What's your name?",
    "manager_phone": "What's the WhatsApp number for your account, with the country code?",
    "manager_email": "What's your email?",
    "otp_phone": "An OTP has been sent to your WhatsApp. Please share it here to confirm your phone number.",
    "otp_email": "An OTP has been sent to your email. Please share it here to confirm your email.",
    "role": "Are you the hotel manager?",
    "nationality": "Are you Malaysian?",
    "primary_language": "Is English the language you use?",
    "hotel_timezone": "Are you in Kuala Lumpur?",
    "hotel_currency": "Do you charge in ringgit?",
    "room_type": "What kind of room is it?",
    "room_numbers": "Which room numbers?",
    "nightly_price": "What is the nightly price?",
    "price_from": "Does the price start tomorrow?",
    "price_to": "Does the price run for a month?",
    "payment_percent": "Is the deposit 30 percent?",
    "guest_name": "Who is the first guest?",
    "guest_phone": "What is the guest's phone number, with the country code?",
    "stay_night": "Is the stay tomorrow night?",
    **{field: item["question"] for field, item in _DEFINITION_BY_FIELD.items()},
}
_CHIPS = {
    "role": "Hotel Manager",
    "nationality": "Malaysian",
    "primary_language": "English",
    "hotel_timezone": "Kuala Lumpur time",
    "hotel_currency": "ringgit",
    "payment_percent": "30 percent",
    "price_from": "tomorrow",
    "price_to": "a month ahead",
    "stay_night": "tomorrow night",
    **{field: item["chip"] for field, item in _DEFINITION_BY_FIELD.items()},
}
_HIGHLIGHTS = {
    "hotel_name": "Hotel",
    "manager_name": "Manager",
    "manager_phone": "WhatsApp",
    "manager_email": "Email",
    "role": "Role",
    "nationality": "Nationality",
    "primary_language": "Language",
    "hotel_timezone": "Timezone",
    "hotel_currency": "Currency",
    "city_name": "City",
    "room_type": "Room",
    "room_numbers": "Room numbers",
    "nightly_price": "Nightly price",
    "price_from": "Price from",
    "price_to": "Price to",
    "payment_percent": "Deposit",
    "guest_name": "Guest",
    "guest_phone": "Guest phone",
    "stay_night": "Stay night",
    **{field: item["label"] for field, item in _DEFINITION_BY_FIELD.items()},
}
_STEP_LABELS = {
    "account": "Account",
    "hotel": "Hotel",
    "rooms": "Rooms",
    "policies": "Policies",
    "settings": "How the hotel operates",
    "definitions": "Your definitions",
    "booking": "First booking",
}


def _question(name):
    """A fixed field's question, or a set_ field's catalog question."""
    row = _setting_fields().get(name) if name.startswith(_SETTING) else None
    return _QUESTIONS.get(name) or (row or {}).get("question") or ""


def _next_name(fields, saved):
    if "account" not in saved:
        from booking_system import contact_otp
        email = str(fields.get("manager_email") or "").strip()
        if email and not contact_otp.confirmed("email", email):
            return "otp_email"
    for step, names in _STEPS:
        if step in saved:
            continue
        missing = _missing(fields, _step_names(step, names, fields))
        return missing[0] if missing else ""
    return ""


def _recap(step, fields, booking_id):
    answered = [name for name in _setting_fields() if name.startswith(_SETTING) and fields.get(name)]
    lines = {
        "account": fields["manager_name"] + " " + fields["manager_phone"],
        "hotel": fields["hotel_name"],
        "rooms": fields["room_type"] + " " + fields["room_numbers"] + " at " + fields["nightly_price"],
        "policies": "Deposit " + fields["payment_percent"] + "%",
        "settings": str(len(answered)) + " answered",
        "definitions": str(len(STANDARD) - len(_missing(fields, _DEFINITION_FIELDS))) + " of " + str(len(STANDARD)) + " set",
        "booking": fields["guest_name"] + " " + str(booking_id or ""),
    }
    return lines.get(step) or ""


def build_guide(fields, saved, booking_id):
    """The to-do, the accepted facts, and the one offered answer."""
    highlights = []
    labels = dict(_HIGHLIGHTS, **{name: row["setting_key"] for name, row in _setting_fields().items() if name.startswith(_SETTING)})
    for name in _all_fields():
        if name not in labels:
            continue
        value = str(fields.get(name) or "").strip()
        if value:
            highlights.append({"label": labels[name], "value": value})
    todos = []
    opened = False
    suggestion = ""
    for step, _names in _STEPS:
        label = _STEP_LABELS[step]
        if step in saved:
            todos.append({"step": step, "label": label, "state": "done", "detail": _recap(step, fields, booking_id)})
            continue
        if opened:
            todos.append({"step": step, "label": label, "state": "later", "detail": ""})
            continue
        opened = True
        gap = _next_name(fields, saved)
        suggestion = _CHIPS.get(gap) or ""
        todos.append({"step": step, "label": label, "state": "now", "detail": _question(gap)})
    return {
        "hotel_name": str(fields.get("hotel_name") or ""),
        "highlights": highlights,
        "todos": todos,
        "suggestion": suggestion,
        "booking_id": str(booking_id or ""),
        "fields": _public(fields, saved),
    }


def _guide_collection():
    from booking_system.guest_manager import guest_manager
    return guest_manager.records["hotel_guide"]


def store_guide(hotel_id, guide):
    """Read the hotel's guide, then create or update it. Key: hotel_id."""
    if not hotel_id:
        return
    collection = _guide_collection()
    body = {
        "hotel_id": hotel_id,
        "hotel_name": str(guide.get("hotel_name") or ""),
        "highlights": guide.get("highlights") or [],
        "todos": guide.get("todos") or [],
        "suggestion": str(guide.get("suggestion") or ""),
        "booking_id": str(guide.get("booking_id") or ""),
        "fields": guide.get("fields") or {},
        "updated_at": datetime.now(timezone.utc),
    }
    if collection.find_one({"hotel_id": hotel_id}, {"_id": 1}):
        collection.update_one({"hotel_id": hotel_id}, {"$set": body})
        return
    collection.insert_one(dict(body, _id=hotel_id))


def _money(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "").strip()
    if number == int(number):
        return str(int(number))
    return str(number)


def read_guide(hotel_id):
    """The stored guide, or one built from the hotel record when none is stored yet."""
    empty = {"hotel_name": "", "highlights": [], "todos": [], "suggestion": "", "booking_id": "", "fields": {}}
    if not hotel_id:
        return empty
    current = _guide_collection().find_one({"hotel_id": hotel_id}, {"_id": 0, "hotel_id": 0, "updated_at": 0})
    if current:
        return current
    fields, booking_id, manager_id = _fields_from_hotel(hotel_id)
    if not fields:
        return empty
    saved, found = _already(fields, manager_id)
    if found:
        booking_id = found
    guide = build_guide(fields, saved, booking_id)
    if "account" in saved:
        store_guide(hotel_id, guide)
    return guide


def _results(lines):
    """The write outcomes of this turn, the speaker's only source for what happened."""
    return list(lines) or ["Nothing was saved this turn."]


def _speak(message, history, lines, offers, fields, saved):
    """The reply, written after the writes from their outcomes; the extraction step never speaks."""
    gap = _next_name(fields, saved)
    text = "\n".join(
        ["Conversation"]
        + [("Person: " if str((item or {}).get("role") or "") == "person" else "Leon: ") + str((item or {}).get("text") or "")
           for item in list(history or [])[-6:]]
        + ["Person: " + str(message or ""), "Results"] + _results(lines)
        + ["Next field question: " + _question(gap), "Offer: " + (offers.get(gap) or "")]
    )
    from booking_system.llm_utils import haiku_text
    try:
        spoken = haiku_text(_REWRITE, text, max_tokens=300, timeout=60).strip()
    except Exception:
        spoken = ""
    return spoken or " ".join(list(lines) + [_question(gap)]).strip()


def _signup_graph(message, history, incoming, offers, known_saved, known_booking, signed_user_id):
    """Signup extract, writes, and reply are nodes. The model calls stay inside read and reply."""
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class State(TypedDict, total=False):
        parsed: dict
        stored: dict
        lines: list
        saved: list
        extra: dict
        reply: str

    def read(state: State):
        from booking_system.llm_utils import haiku_text

        response_text = haiku_text(
            _INSTRUCTIONS,
            _prompt(message, history, incoming, offers, known_saved, known_booking),
            schema=_schema(),
            max_tokens=1200,
            timeout=90,
        )
        return {"parsed": json.loads(response_text)}

    def apply(state: State):
        parsed = state.get("parsed") or {}
        stored = dict(incoming)
        for name in _all_fields():
            value = str(parsed.get(name) or "").strip()
            if value:
                stored[name] = value
        stored = _clean(stored)
        otp = {"phone": str(parsed.get("otp_phone") or "").strip(), "email": str(parsed.get("otp_email") or "").strip()}
        lines, saved, extra = _apply(stored, signed_user_id, otp)
        return {"stored": stored, "lines": lines, "saved": saved, "extra": extra}

    def reply(state: State):
        stored = state.get("stored") or {}
        saved = state.get("saved") or []
        text = _speak(message, history, state.get("lines") or [], offers, stored, saved)
        return {"reply": text, "stored": stored}

    builder = StateGraph(State)
    builder.add_node("read", read)
    builder.add_node("apply", apply)
    builder.add_node("reply", reply)
    builder.add_edge(START, "read")
    builder.add_edge("read", "apply")
    builder.add_edge("apply", "reply")
    builder.add_edge("reply", END)
    return builder.compile().invoke({})


def onboard_turn(message, history, fields, signed_user_id=None):
    """Read the chat, keep the stated fields, then create the step that is complete. signed_user_id is the person on the
    request's token. A phone is confirmed with a code before an existing account is opened or a new one is created."""
    incoming = _recall(_clean(fields), signed_user_id)
    known_saved, known_booking = _already(incoming, signed_user_id)
    offers = _offers(date.today())
    found = _signup_graph(message, history, incoming, offers, known_saved, known_booking, signed_user_id)
    stored = found.get("stored") or {}
    saved = found.get("saved") or []
    extra = found.get("extra") or {}
    booking_id = str(extra.get("booking_id") or "")
    guide = build_guide(stored, saved, booking_id)
    if "hotel" in saved:
        store_guide(_hotel_row(stored["hotel_name"]).get("hotel_id"), guide)
    return {
        "reply": found.get("reply") or "",
        "fields": guide["fields"],
        "saved": saved,
        "booking_id": booking_id,
        "todos": guide["todos"],
        "highlights": guide["highlights"],
        "suggestion": guide["suggestion"],
        "token": extra.get("token") or "",
        "cookie_hotel": extra.get("hotel_name") or "",
    }
