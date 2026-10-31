"""One chat turn that opens a hotel, from the account through the first booking."""
import json
import os
from datetime import date, datetime, timedelta, timezone

from booking_system.semantic.definitions import STANDARD, STYLE, manager_definitions, save_definition


_MODEL = "claude-haiku-5-5"
_DEFINITION_BY_FIELD = {"def_" + item["key"]: item for item in STANDARD}
_DEFINITION_FIELDS = tuple(_DEFINITION_BY_FIELD)
_FIELDS = (
    "hotel_name",
    "manager_name",
    "manager_phone",
    "password",
    "role",
    "nationality",
    "primary_language",
    "hotel_timezone",
    "hotel_currency",
    "room_type",
    "room_numbers",
    "nightly_price",
    "price_from",
    "price_to",
    "checkin_time",
    "checkout_time",
    "payment_percent",
    "guest_name",
    "guest_phone",
    "stay_night",
) + _DEFINITION_FIELDS
_STEPS = (
    ("account", ("manager_phone", "manager_name", "password", "role", "nationality", "primary_language")),
    ("hotel", ("hotel_name", "hotel_timezone", "hotel_currency")),
    ("rooms", ("room_type", "room_numbers", "nightly_price", "price_from", "price_to")),
    ("policies", ("checkin_time", "checkout_time", "payment_percent")),
    ("definitions", _DEFINITION_FIELDS),
    ("booking", ("guest_name", "guest_phone", "stay_night")),
)
_NAMES = dict(_STEPS)
_UPPER = ("hotel_name", "manager_name", "hotel_currency", "room_type", "guest_name")
_INSTRUCTIONS = (
    "You are Leon, opening a hotel with the person in this chat. "
    "The account comes first. Its key is the WhatsApp phone number. The hotel name comes after the account is saved. "
    "reply is spoken, the way a person talks. One or two short sentences. One question. "
    "Ask for one missing field. When that field has an offer, mention it the way you would say it out loud and let them take it or change it. "
    "Copy a field only when the person stated that value or accepted the offer you just made. "
    "Copy every known value forward. Leave a field empty when it is not known. "
    "A short yes, yeah, ok, or sure accepts only the offer you just made. "
    "They may answer in passing, with a city, a time of day, or a currency in words. Store the value in the form below. "
    "Kuala Lumpur time is Asia/Kuala_Lumpur. Ringgit is MYR. 3 pm is 15:00. Noon is 12:00. 30 percent is 30. "
    "Tomorrow, a month ahead, and tomorrow night are the offered dates. "
    "A phone starts with + and the country code, with no spaces. "
    "hotel_name, manager_name, room_type, and guest_name are capitals. "
    "hotel_timezone is an IANA name for the city they named. hotel_currency is three letters. payment_percent is digits. "
    "A time of day is HH:MM. room_numbers are separated by commas. Dates are YYYY-MM-DD. "
    "Tomorrow and a month ahead are the offered dates. price_from and price_to are the first and last night the price covers. stay_night is the night the guest sleeps. "
    "When the person mentions several values, copy each of them. "
    "Do not invent a phone, a password, a hotel, a guest, or a date. "
    "The def_ fields are what this manager means when asking Leon about arrivals, in-house guests, departures, and reservations. "
    "A def_ field holds the offer they accepted, or what they mean. " + STYLE + " "
    "When a step is saved, do not ask for it again. "
    "When the hotel is open, say so and name the booking. Do not ask another question."
)
_REWRITE = (
    "Speak like a person, in one or two short sentences. Do not name the field. "
    "When a result says Saved, say what is now in place, then ask for the next field. "
    "When the next field has an offer, offer that value the way you would say it. "
    "When a result says Failed or Already, say it was not done and include the reason. Do not ask for a later step. "
    "When a result says Open, say the hotel is open and include the booking id from the result. Do not ask another question."
)


def _client():
    from openai import OpenAI
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _schema():
    properties = {name: {"type": "string"} for name in _FIELDS}
    properties["reply"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": list(_FIELDS) + ["reply"],
        "additionalProperties": False,
    }


def _clean(fields):
    stored = {}
    for name in _FIELDS:
        stored[name] = str((fields or {}).get(name) or "").strip()
    for name in _UPPER:
        stored[name] = stored[name].upper()
    stored["manager_phone"] = "".join(stored["manager_phone"].split())
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
        "checkin_time": "15:00",
        "checkout_time": "12:00",
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


def _bad(result):
    text = str(result or "").strip()
    first = text.split("\n")[0].strip()
    if first.startswith("❌"):
        return first.lstrip("❌").strip()
    return ""


def _account_doc(fields):
    from booking_system.hotel_context_manager import manager_col
    phone = str(fields.get("manager_phone") or "").strip()
    if not phone or manager_col is None:
        return None
    return manager_col.find_one({"manager_phone": phone}, {"password": 0})


def _account_exact(doc, fields):
    return str(doc.get("manager_phone") or "") == fields["manager_phone"] and str(doc.get("manager_name") or "").upper() == fields["manager_name"]


def _recall(fields):
    """Fill empty account and hotel values from the profile stored under this WhatsApp number."""
    doc = _account_doc(fields)
    if not doc:
        return fields
    stored_name = str(doc.get("manager_name") or "").upper()
    if fields["manager_name"] and fields["manager_name"] != stored_name:
        return fields
    for name in ("manager_name", "role", "nationality", "primary_language", "hotel_name", "hotel_timezone", "hotel_currency"):
        if not fields[name] and doc.get(name):
            fields[name] = str(doc.get(name) or "")
    fields.update({name: value for name, value in _profile_definitions(stored_name).items() if not fields[name]})
    return _clean(fields)


def _profile_definitions(manager_name):
    """def_ fields already answered on this manager's profile."""
    known = {item["key"]: item["definition"] for item in manager_definitions(manager_name)}
    return {field: known[item["key"]] for field, item in _DEFINITION_BY_FIELD.items() if known.get(item["key"])}


def _account_text(doc):
    return "Hotel " + str(doc.get("hotel_name") or "") + ", manager " + str(doc.get("manager_name") or "") + ", phone " + str(doc.get("manager_phone") or "")


def _write_account(fields):
    from booking_system.hotel_context_manager import signin_manager_by_phone, signup_manager
    doc = _account_doc(fields)
    if doc and not _account_exact(doc, fields):
        return "clarify", "Already on record. " + _account_text(doc), {}
    if doc:
        if not fields["password"]:
            return "saved", "", {}
        signed = signin_manager_by_phone(fields["manager_phone"], fields["password"])
        if not signed.get("success"):
            return "failed", "Failed account. " + str(signed.get("message") or "Sign in failed"), {}
        return "saved", "", {"token": signed.get("token") or ""}
    if _missing(fields, _STEPS[0][1]):
        return "missing", "", {}
    created = signup_manager({
        "manager_name": fields["manager_name"],
        "manager_phone": fields["manager_phone"],
        "password": fields["password"],
        "role": fields["role"],
        "nationality": fields["nationality"],
        "primary_language": fields["primary_language"],
    })
    if not created.get("success"):
        return "failed", "Failed account. " + str(created.get("message") or "Signup failed"), {}
    signed = signin_manager_by_phone(fields["manager_phone"], fields["password"])
    if not signed.get("success"):
        return "failed", "Failed account. " + str(signed.get("message") or "Sign in failed"), {}
    line = "Saved account. " + fields["manager_name"] + " is signed in on " + fields["manager_phone"] + "."
    return "saved", line, {"token": signed.get("token") or ""}


def _write_hotel(fields):
    from booking_system.hotel_context_manager import set_manager_hotel
    if _missing(fields, _STEPS[1][1]):
        return "missing", "", {}
    saved = set_manager_hotel(fields["manager_phone"], fields["hotel_name"], fields["hotel_timezone"], fields["hotel_currency"])
    if saved.get("clarify"):
        return "clarify", "Already on record. " + str(saved.get("message") or ""), {}
    if not saved.get("success"):
        return "failed", "Failed hotel. " + str(saved.get("message") or "Hotel was not saved"), {}
    line = "Saved hotel. " + fields["hotel_name"] + " is on this account."
    return "saved", line, {"token": saved.get("token") or "", "hotel_name": saved.get("hotel_name") or fields["hotel_name"]}


def _price_exists(hotel, room_type, day):
    from booking_system.inventory_manager import inventory_manager
    from booking_system.llm_utils import canonical_key
    if not day or not inventory_manager._ensure_postgres_connection():
        return False
    inventory_manager._safe_rollback()
    cur = inventory_manager.postgres_conn.cursor()
    cur.execute(
        "SELECT base_price FROM daily_pricing WHERE hotel_name = %s AND room_type = %s AND stay_date = %s",
        (canonical_key(hotel), canonical_key(room_type), day),
    )
    row = cur.fetchone()
    cur.close()
    return row is not None


def _write_rooms(fields):
    from booking_system.inventory_manager import inventory_manager
    from booking_system.room_manager import room_manager
    hotel = fields["hotel_name"]
    room_type = fields["room_type"]
    numbers = _numbers(fields["room_numbers"])
    if not room_type and not numbers:
        return "missing", "", {}
    base = room_manager.collection.find_one({"hotel_name": hotel, "room_type": room_type, "has_room_number": False}) if room_type else None
    owned = []
    if room_type:
        owned = [str(doc.get("room_number") or "") for doc in room_manager.collection.find({"hotel_name": hotel, "room_type": room_type, "has_room_number": True}, {"room_number": 1})]
    used = []
    if numbers:
        used = list(room_manager.collection.find({"hotel_name": hotel, "room_number": {"$in": numbers}, "has_room_number": True}, {"room_number": 1, "room_type": 1}))
    same = bool(base) and bool(numbers) and set(numbers) == set(owned)
    other = [str(doc.get("room_number") or "") + " " + str(doc.get("room_type") or "") for doc in used if str(doc.get("room_type") or "") != room_type]
    priced = _price_exists(hotel, room_type, fields["price_from"]) if same else False
    if same and priced:
        return "saved", "", {}
    if other or (base and numbers and not same):
        listed = ", ".join(owned) or "none"
        extra = (" Also used: " + ", ".join(other)) if other else ""
        return "clarify", "Already on record. " + room_type + " rooms at " + hotel + ": " + listed + "." + extra, {}
    if _missing(fields, _NAMES["rooms"]):
        return "missing", "", {}
    nights = _nights(fields["price_from"], fields["price_to"])
    if not nights:
        return "failed", "Failed rooms. The price dates are not a range of nights.", {}
    try:
        price = float(fields["nightly_price"])
    except ValueError:
        return "failed", "Failed rooms. The nightly price is not a number.", {}
    if not base:
        created = room_manager._create_room_type_logic(hotel, fields["manager_name"], room_type, {})
        reason = _bad(created)
        if reason:
            return "failed", "Failed rooms. " + reason, {}
        added = room_manager.add_room_numbers(hotel, fields["manager_name"], room_type, numbers)
        reason = _bad(added)
        if reason:
            return "failed", "Failed rooms. " + reason, {}
    priced_now = inventory_manager.update_dates(hotel, fields["manager_name"], room_type, nights, price, len(numbers))
    if isinstance(priced_now, dict) and priced_now.get("error"):
        return "failed", "Failed rooms. " + str(priced_now.get("error")), {}
    return "saved", "Saved rooms. Room number " + ", ".join(numbers) + ", type " + room_type + ", at " + str(price) + " each night.", {}


def _policy_docs(hotel):
    from booking_system.policy_manager import policy_manager
    rows = list(policy_manager.records["booking_policies"].find(
        {"hotel_name": hotel, "status": "active"},
        {"_id": 0, "policy_type": 1},
    ))
    return [str(row.get("policy_type") or "") for row in rows]


def _write_policies(fields):
    from booking_system.policy_manager import policy_manager
    hotel = fields["hotel_name"]
    present = _policy_docs(hotel)
    have_booking = "booking" in present
    have_payment = "payment" in present
    if have_booking and have_payment:
        return "saved", "", {}
    if _missing(fields, _NAMES["policies"]):
        return "missing", "", {}
    try:
        percent = float(fields["payment_percent"])
    except ValueError:
        return "failed", "Failed policies. The payment percent is not a number.", {}
    manager = fields["manager_name"]
    if not have_booking:
        created = policy_manager.create_policy("booking", {
            "checkin_time": fields["checkin_time"],
            "checkout_time": fields["checkout_time"],
            "cancellation_policy": {"window_hours": 48, "penalty_nights": 1, "refund_percent": 50},
        }, hotel, manager)
        reason = _bad(created)
        if reason:
            return "failed", "Failed policies. " + reason, {}
    if not have_payment:
        created = policy_manager.create_policy("payment", {
            "payment_threshold_percent": percent,
            "payment_window_days": 7,
            "methods_allowed": ["CASH"],
        }, hotel, manager)
        reason = _bad(created)
        if reason:
            return "failed", "Failed policies. " + reason, {}
    return "saved", "Saved policies. Check-in " + fields["checkin_time"] + ", check-out " + fields["checkout_time"] + ", deposit " + str(percent) + " percent.", {}


def _booking_doc(fields):
    from booking_system.guest_manager import guest_manager
    night = fields["stay_night"]
    return guest_manager.records["guest_bookings"].find_one({
        "hotel_name": fields["hotel_name"],
        "guest_phone": fields["guest_phone"],
        "room_type": fields["room_type"],
        "stay_dates": night,
    }, {"_id": 0, "booking_id": 1, "guest_name": 1})


def _write_booking(fields):
    from booking_system.booking_manager import booking_manager
    from booking_system.guest_manager import guest_manager
    if not fields["guest_phone"] and not fields["guest_name"]:
        return "missing", "", {}
    guest = guest_manager.records["guests"].find_one({"guest_phone": fields["guest_phone"], "status": "active"}) if fields["guest_phone"] else None
    if guest and str(guest.get("hotel_name") or "").upper() != fields["hotel_name"]:
        return "clarify", "Already on record. Guest " + str(guest.get("guest_name") or "") + " " + fields["guest_phone"] + " is at " + str(guest.get("hotel_name") or "") + ".", {}
    if guest and str(guest.get("guest_name") or "").upper() != fields["guest_name"] and fields["guest_name"]:
        return "clarify", "Already on record. Guest " + fields["guest_phone"] + " is " + str(guest.get("guest_name") or "") + " at " + fields["hotel_name"] + ".", {}
    existing = _booking_doc(fields) if fields["guest_phone"] and fields["stay_night"] and fields["room_type"] else None
    if existing:
        return "saved", "", {"booking_id": existing.get("booking_id") or ""}
    if _missing(fields, _NAMES["booking"]):
        return "missing", "", {}
    try:
        datetime.strptime(fields["stay_night"], "%Y-%m-%d")
    except ValueError:
        return "failed", "Failed booking. The stay night is not a date.", {}
    if not guest:
        created = guest_manager.create_guest_profile(
            fields["hotel_name"], fields["manager_name"], fields["guest_phone"], fields["guest_name"], nationality=fields["nationality"]
        )
        reason = _bad(created)
        if reason:
            return "failed", "Failed booking. " + reason, {}
    result = booking_manager.create_booking(
        fields["hotel_name"], fields["manager_name"], fields["guest_phone"], fields["room_type"],
        [fields["stay_night"]], 1, "", "PMS",
    )
    reason = _bad(result)
    if reason:
        return "failed", "Failed booking. " + reason, {}
    booking_id = str(getattr(booking_manager, "_last_booking_id", "") or "")
    return "saved", "Saved booking. " + booking_id + " is confirmed for " + fields["stay_night"] + ".", {"booking_id": booking_id}


def _write_definitions(fields):
    """Read the manager profile first. A new or changed meaning is saved; a matching one is left alone."""
    manager = fields["manager_name"]
    stored = _profile_definitions(manager)
    changed = [name for name in _DEFINITION_FIELDS if fields[name] and fields[name] != stored.get(name)]
    for name in changed:
        result = save_definition(manager, _DEFINITION_BY_FIELD[name]["key"], fields[name], "onboarding")
        if result.get("error"):
            return "failed", "Failed definitions. " + str(result["error"]), {}
    if _missing(fields, _DEFINITION_FIELDS):
        return "missing", "", {}
    if not changed:
        return "saved", "", {}
    said = "; ".join(_DEFINITION_BY_FIELD[name]["label"] + ": " + fields[name] for name in changed)
    return "saved", "Saved definitions. " + said + ".", {}


_WRITERS = {
    "account": _write_account,
    "hotel": _write_hotel,
    "rooms": _write_rooms,
    "policies": _write_policies,
    "definitions": _write_definitions,
    "booking": _write_booking,
}


def _apply(fields):
    lines = []
    saved = []
    extra = {}
    for step, _names in _STEPS:
        outcome, line, more = _WRITERS[step](fields)
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


def _already(fields):
    """Steps already stored. This reads and does not write."""
    saved = []
    doc = _account_doc(fields)
    if not doc or not _account_exact(doc, fields):
        return saved, ""
    saved.append("account")
    hotel_on_file = str(doc.get("hotel_name") or "")
    if not hotel_on_file or not doc.get("hotel_timezone") or not doc.get("hotel_currency"):
        return saved, ""
    if fields["hotel_name"] and fields["hotel_name"] != hotel_on_file:
        return saved, ""
    saved.append("hotel")
    from booking_system.room_manager import room_manager
    hotel = fields["hotel_name"] or hotel_on_file
    room_type = fields["room_type"]
    numbers = _numbers(fields["room_numbers"])
    if not room_type or not numbers:
        return saved, ""
    base = room_manager.collection.find_one({"hotel_name": hotel, "room_type": room_type, "has_room_number": False})
    owned = [str(doc.get("room_number") or "") for doc in room_manager.collection.find({"hotel_name": hotel, "room_type": room_type, "has_room_number": True}, {"room_number": 1})]
    if not base or set(numbers) != set(owned) or not _price_exists(hotel, room_type, fields["price_from"]):
        return saved, ""
    saved.append("rooms")
    present = _policy_docs(hotel)
    if "booking" not in present or "payment" not in present:
        return saved, ""
    saved.append("policies")
    if _missing(_profile_definitions(fields["manager_name"]), _DEFINITION_FIELDS):
        return saved, ""
    saved.append("definitions")
    existing = _booking_doc(fields) if fields["guest_phone"] and fields["stay_night"] else None
    if not existing:
        return saved, ""
    saved.append("booking")
    return saved, str(existing.get("booking_id") or "")


def _public(fields, saved):
    shown = {}
    for name in _FIELDS:
        if name == "password" and "account" in saved:
            continue
        shown[name] = fields[name]
    return shown


def _prompt(message, history, fields, offers, saved, booking_id):
    lines = ["Today " + date.today().isoformat(), "Known"]
    for name in _FIELDS:
        lines.append(name + ": " + fields.get(name, ""))
    lines.append("Offers")
    for name, value in offers.items():
        lines.append(name + ": " + value)
    lines.append("Saved: " + ", ".join(saved))
    gap = ""
    for step, names in _STEPS:
        if step in saved:
            continue
        missing = _missing(fields, names)
        gap = missing[0] if missing else ""
        break
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
    "manager_phone": "What's your WhatsApp number, with the country code?",
    "password": "What password do you want?",
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
    "checkin_time": "Is check-in at 3 pm?",
    "checkout_time": "Is check-out at noon?",
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
    "checkin_time": "3 pm",
    "checkout_time": "noon",
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
    "role": "Role",
    "nationality": "Nationality",
    "primary_language": "Language",
    "hotel_timezone": "Timezone",
    "hotel_currency": "Currency",
    "room_type": "Room",
    "room_numbers": "Room numbers",
    "nightly_price": "Nightly price",
    "price_from": "Price from",
    "price_to": "Price to",
    "checkin_time": "Check-in",
    "checkout_time": "Check-out",
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
    "definitions": "Your definitions",
    "booking": "First booking",
}


def _next_name(fields, saved):
    for step, names in _STEPS:
        if step in saved:
            continue
        missing = _missing(fields, names)
        return missing[0] if missing else ""
    return ""


def _recap(step, fields, booking_id):
    lines = {
        "account": fields["manager_name"] + " " + fields["manager_phone"],
        "hotel": fields["hotel_name"],
        "rooms": fields["room_type"] + " " + fields["room_numbers"] + " at " + fields["nightly_price"],
        "policies": "In " + fields["checkin_time"] + ", out " + fields["checkout_time"] + ", deposit " + fields["payment_percent"] + "%",
        "definitions": str(len(STANDARD) - len(_missing(fields, _DEFINITION_FIELDS))) + " of " + str(len(STANDARD)) + " set",
        "booking": fields["guest_name"] + " " + str(booking_id or ""),
    }
    return lines.get(step) or ""


def build_guide(fields, saved, booking_id):
    """The to-do, the accepted facts, and the one offered answer. The password value is never included."""
    highlights = []
    if "account" in saved or fields.get("password"):
        highlights.append({"label": "Password", "value": "Password set"})
    for name in _FIELDS:
        if name == "password" or name not in _HIGHLIGHTS:
            continue
        value = str(fields.get(name) or "").strip()
        if value:
            highlights.append({"label": _HIGHLIGHTS[name], "value": value})
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
        todos.append({"step": step, "label": label, "state": "now", "detail": _QUESTIONS.get(gap) or ""})
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
    collection = guest_manager.records["hotel_guide"]
    collection.create_index("hotel_name", unique=True, name="hotel_name_guide")
    return collection


def store_guide(guide):
    """Read the hotel guide, then create or update it. Natural key is hotel_name."""
    hotel = str(guide.get("hotel_name") or "").upper()
    if not hotel:
        return
    collection = _guide_collection()
    body = {
        "hotel_name": hotel,
        "highlights": guide.get("highlights") or [],
        "todos": guide.get("todos") or [],
        "suggestion": str(guide.get("suggestion") or ""),
        "booking_id": str(guide.get("booking_id") or ""),
        "fields": guide.get("fields") or {},
        "updated_at": datetime.now(timezone.utc),
    }
    current = collection.find_one({"hotel_name": hotel}, {"_id": 1})
    if current:
        collection.update_one({"hotel_name": hotel}, {"$set": body})
        return
    collection.insert_one(body)


def _money(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "").strip()
    if number == int(number):
        return str(int(number))
    return str(number)


def _priced_span(hotel, room_type):
    from booking_system.inventory_manager import inventory_manager
    from booking_system.llm_utils import canonical_key
    if not room_type or not inventory_manager._ensure_postgres_connection():
        return "", "", ""
    inventory_manager._safe_rollback()
    cur = inventory_manager.postgres_conn.cursor()
    cur.execute(
        "SELECT MIN(stay_date), MAX(stay_date), MAX(base_price) FROM daily_pricing WHERE hotel_name = %s AND room_type = %s AND base_price > 0",
        (canonical_key(hotel), canonical_key(room_type)),
    )
    row = cur.fetchone()
    cur.close()
    inventory_manager._safe_rollback()
    if not row or not row[0]:
        return "", "", ""
    start = row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0])[:10]
    end = row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1])[:10]
    return start, end, _money(row[2])


def _fields_from_hotel(hotel):
    """Read the hotel that is already open. Returns fields and a booking id."""
    from booking_system.guest_manager import guest_manager
    from booking_system.hotel_context_manager import manager_col
    from booking_system.policy_manager import policy_manager
    from booking_system.room_manager import room_manager
    manager = manager_col.find_one({"hotel_name": hotel}, {"password": 0}) if manager_col is not None else None
    if not manager:
        return None, ""
    fields = _clean({
        "hotel_name": hotel,
        "manager_name": manager.get("manager_name"),
        "manager_phone": manager.get("manager_phone"),
        "role": manager.get("role"),
        "nationality": manager.get("nationality"),
        "primary_language": manager.get("primary_language"),
        "hotel_timezone": manager.get("hotel_timezone"),
        "hotel_currency": manager.get("hotel_currency"),
        **_profile_definitions(manager.get("manager_name")),
    })
    bases = list(room_manager.collection.find({"hotel_name": hotel, "has_room_number": False}, {"room_type": 1}))
    chosen = ""
    numbers = []
    for base in bases:
        room_type = str(base.get("room_type") or "")
        owned = [str(doc.get("room_number") or "") for doc in room_manager.collection.find({"hotel_name": hotel, "room_type": room_type, "has_room_number": True}, {"room_number": 1})]
        if owned and not chosen:
            chosen = room_type
            numbers = owned
    if chosen:
        start, end, price = _priced_span(hotel, chosen)
        fields["room_type"] = chosen
        fields["room_numbers"] = ", ".join(numbers)
        fields["price_from"] = start
        fields["price_to"] = end
        fields["nightly_price"] = price
    policies = list(policy_manager.records["booking_policies"].find({"hotel_name": hotel, "status": "active"}, {"_id": 0, "policy_type": 1, "details": 1}))
    by_type = {}
    for row in policies:
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        by_type[str(row.get("policy_type") or "")] = details
    booking_details = by_type.get("booking") or {}
    payment_details = by_type.get("payment") or {}
    fields["checkin_time"] = str(booking_details.get("checkin_time") or "")
    fields["checkout_time"] = str(booking_details.get("checkout_time") or "")
    percent = payment_details.get("payment_threshold_percent")
    fields["payment_percent"] = _money(percent) if percent is not None else ""
    first = guest_manager.records["guest_bookings"].find(
        {"hotel_name": hotel},
        {"_id": 0, "booking_id": 1, "guest_name": 1, "guest_phone": 1, "stay_dates": 1, "room_type": 1, "created_timestamp": 1},
    ).sort("created_timestamp", 1).limit(1)
    booking = next(iter(first), None)
    booking_id = ""
    if booking:
        nights = booking.get("stay_dates") or []
        fields["guest_name"] = str(booking.get("guest_name") or "")
        fields["guest_phone"] = str(booking.get("guest_phone") or "")
        fields["stay_night"] = str(nights[0])[:10] if nights else ""
        if not fields["room_type"]:
            fields["room_type"] = str(booking.get("room_type") or "")
        booking_id = str(booking.get("booking_id") or "")
    return _clean(fields), booking_id


def read_guide(hotel_name):
    """The stored guide, or one built from the hotel record when none is stored yet."""
    hotel = str(hotel_name or "").upper()
    if not hotel:
        return {"hotel_name": "", "highlights": [], "todos": [], "suggestion": "", "booking_id": "", "fields": {}}
    collection = _guide_collection()
    current = collection.find_one({"hotel_name": hotel}, {"_id": 0, "updated_at": 0})
    if current:
        return current
    fields, booking_id = _fields_from_hotel(hotel)
    if not fields:
        return {"hotel_name": hotel, "highlights": [], "todos": [], "suggestion": "", "booking_id": "", "fields": {}}
    saved, found = _already(fields)
    if found:
        booking_id = found
    guide = build_guide(fields, saved, booking_id)
    if "account" in saved:
        store_guide(guide)
    return guide


def _speak(client, draft, lines, offers, fields, saved):
    gap = ""
    for step, names in _STEPS:
        if step in saved:
            continue
        missing = _missing(fields, names)
        gap = missing[0] if missing else ""
        break
    offer = offers.get(gap) or ""
    text = "Draft\n" + str(draft or "") + "\nResults\n" + "\n".join(lines) + "\nNext field: " + gap + "\nOffer: " + offer
    from booking_system.llm_utils import haiku_text
    spoken = haiku_text(_REWRITE, text, max_tokens=300, timeout=60).strip()
    return spoken or str(draft or "").strip()


def onboard_turn(message, history, fields):
    """Read the chat, keep the stated fields, then create the step that is complete."""
    incoming = _recall(_clean(fields))
    client_password = incoming["password"]
    known_saved, known_booking = _already(incoming)
    today = date.today()
    offers = _offers(today)
    client = _client()
    from booking_system.llm_utils import haiku_text
    response_text = haiku_text(
        _INSTRUCTIONS,
        _prompt(message, history, incoming, offers, known_saved, known_booking),
        schema=_schema(),
        max_tokens=1200,
        timeout=90,
    )
    parsed = json.loads(response_text)
    stored = dict(incoming)
    for name in _FIELDS:
        value = str((parsed or {}).get(name) or "").strip()
        if value:
            stored[name] = value
    stored = _clean(stored)
    if "account" in known_saved and not client_password:
        stored["password"] = ""
    lines, saved, extra = _apply(stored)
    reply = str((parsed or {}).get("reply") or "").strip()
    if lines:
        try:
            reply = _speak(client, reply, lines, offers, stored, saved)
        except Exception:
            reply = " ".join(lines)
    if not reply:
        reply = "What's your WhatsApp number, with the country code?"
    if "account" in saved:
        stored["password"] = ""
    booking_id = str(extra.get("booking_id") or "")
    guide = build_guide(stored, saved, booking_id)
    if "hotel" in saved:
        store_guide(guide)
    return {
        "reply": reply,
        "fields": guide["fields"],
        "saved": saved,
        "booking_id": booking_id,
        "todos": guide["todos"],
        "highlights": guide["highlights"],
        "suggestion": guide["suggestion"],
        "token": extra.get("token") or "",
        "cookie_hotel": extra.get("hotel_name") or "",
    }
