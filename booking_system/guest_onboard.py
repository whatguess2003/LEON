"""Ella's chat that fills a guest profile. The guest already has a name and phone; the signed portal link is the login.
A turn reads the chat, keeps the stated profile fields, and writes them on the guest. Ella speaks after the write."""
import json
from datetime import datetime

from booking_system.guest_manager import PROFILE_TEXT


_ASK = (
    "email",
    "country",
    "date_of_birth",
    "gender",
    "language",
    "identity_type",
    "identity_number",
    "company",
    "address",
    "special_request",
)
_OPTIONAL = ("company", "address", "special_request")
_QUESTIONS = {
    "email": "What's your email?",
    "country": "Which country is home?",
    "date_of_birth": "What's your date of birth?",
    "gender": "How should I note your gender?",
    "language": "Which language should I use with you?",
    "identity_type": "Is your ID a passport?",
    "identity_number": "What's the ID number?",
    "company": "Do you travel with a company name?",
    "address": "What's your address?",
    "special_request": "Anything the hotel should prepare for you?",
}
_OFFERS = {
    "country": "Malaysia",
    "language": "English",
    "identity_type": "Passport",
}
_CHIPS = {
    "country": "Malaysia",
    "language": "English",
    "identity_type": "Passport",
}
_LABELS = {
    "email": "Email",
    "country": "Country",
    "date_of_birth": "Date of birth",
    "gender": "Gender",
    "language": "Language",
    "identity_type": "ID type",
    "identity_number": "ID number",
    "company": "Company",
    "address": "Address",
    "special_request": "Request",
}
_INSTRUCTIONS = (
    "You read a chat in which a guest gives Ella their profile, and you fill in the fields. You do not write a reply. "
    "Copy a field only when the guest stated that value or accepted the offer Ella just made. "
    "Copy every known value forward. Leave a field empty when it is not known. "
    "A short yes, yeah, ok, or sure accepts only the offer Ella just made. "
    "email is lower case. date_of_birth is YYYY-MM-DD. country is the country they named. "
    "identity_type is Passport, IC, or MyKad when they name one of those. "
    "For company, address, and special_request, store none when they say they have none or want to skip it. "
    "guest_name changes only when they give a different name. Do not invent an email, a date, an ID number, or a name."
)
_REWRITE = (
    "You are Ella, collecting a guest's details for their stay. Speak like a person, in one or two short sentences. "
    "Use their name. Do not name the field. "
    "Results lists what was written this turn. Say that something is saved only when a Results line says Saved or Complete. "
    "When a result says Saved, say what is now in place, then ask for the next field in your own words, keeping the meaning of its question. "
    "When it has an offer, offer that value the way you would say it. "
    "When Results says nothing was saved this turn, do not say anything was saved. Thank them at most, then ask. "
    "When a result says Failed, say it was not kept and include the reason. "
    "When a result says Complete, say their details are in. Do not ask another question."
)


def _blank():
    return {name: "" for name in _ASK}


def _text(value):
    return " ".join(str(value or "").split())


def _from_guest(guest):
    """Profile fields already on this guest. Country falls back to nationality."""
    stored = _blank()
    for name in _ASK:
        value = guest.get(name)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        stored[name] = _text(value)
    if not stored["country"]:
        stored["country"] = _text(guest.get("nationality"))
    email = _text(guest.get("guest_email") or guest.get("email") or stored["email"]).lower()
    stored["email"] = email
    day = stored["date_of_birth"]
    if "T" in day:
        stored["date_of_birth"] = day.split("T", 1)[0]
    stored["guest_name"] = _text(guest.get("guest_name"))
    return stored


def _filled(fields, name):
    return bool(_text(fields.get(name)))


def _missing(fields):
    return [name for name in _ASK if not _filled(fields, name)]


def _next_name(fields):
    missing = _missing(fields)
    return missing[0] if missing else ""


def _schema():
    names = list(_ASK) + ["guest_name"]
    return {
        "type": "object",
        "properties": {name: {"type": "string"} for name in names},
        "required": names,
        "additionalProperties": False,
    }


def _prompt(message, history, fields, guest_name, hotel_name):
    lines = ["Hotel " + hotel_name, "Guest " + guest_name, "Known"]
    for name in _ASK:
        lines.append(name + ": " + fields.get(name, ""))
    lines.append("Offers")
    for name, value in _OFFERS.items():
        lines.append(name + ": " + value)
    lines.append("Next field: " + _next_name(fields))
    lines.append("Conversation")
    for item in list(history or [])[-12:]:
        speaker = "Guest" if str((item or {}).get("role") or "") == "guest" else "Ella"
        lines.append(speaker + ": " + str((item or {}).get("text") or ""))
    lines.append("Guest: " + str(message or ""))
    return "\n".join(lines)


def _date_ok(value):
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _save(hotel_id, phone, before, stated):
    """Write the newly stated profile fields. An unusable email or date is left as it was."""
    from booking_system.guest_manager import _email, update_guest_profile
    merged = dict(before)
    refused = ""
    for name in _ASK:
        value = _text(stated.get(name))
        if name in _OPTIONAL:
            value = value.lower() if value.lower() == "none" else value
        if name == "email":
            value = value.lower()
        if not value or value == before.get(name):
            continue
        if name == "email" and not _email(value):
            refused = "Failed email. That email is not usable."
            continue
        if name == "date_of_birth" and not _date_ok(value):
            refused = "Failed date of birth. That is not a date."
            continue
        merged[name] = value
    if _text(stated.get("guest_name")) and _text(stated.get("guest_name")).upper() != before.get("guest_name"):
        merged["guest_name"] = _text(stated.get("guest_name")).upper()
    changed = {name: merged[name] for name in _ASK if merged.get(name) and merged.get(name) != before.get(name)}
    if merged.get("guest_name") and merged["guest_name"] != before.get("guest_name"):
        changed["guest_name"] = merged["guest_name"]
    if "country" in changed:
        changed["nationality"] = changed["country"]
    if not changed:
        return refused, merged
    unknown = [name for name in changed if name not in PROFILE_TEXT and name not in ("guest_name", "email", "nationality")]
    if unknown:
        return "Failed profile. " + ", ".join(unknown) + " is not a profile field.", before
    saved = update_guest_profile(hotel_id, None, phone, changed)
    if saved.get("error"):
        return "Failed profile. " + saved["error"], before
    said = ", ".join(_LABELS.get(name, name) + " " + changed[name] for name in changed if name != "nationality")
    line = "Saved profile. " + said + "."
    if not _missing(merged):
        line += " Complete. Their details are in."
    return " ".join(part for part in (line, refused) if part), merged


def _guide(fields):
    gap = _next_name(fields)
    done = not gap
    highlights = [{"label": _LABELS[name], "value": fields[name]} for name in _ASK if _filled(fields, name)]
    detail = "Your details are in" if done else _QUESTIONS.get(gap, "")
    return {
        "fields": {name: fields.get(name, "") for name in list(_ASK) + ["guest_name"]},
        "highlights": highlights,
        "todos": [{"step": "profile", "label": "Your details", "state": "done" if done else "now", "detail": detail}],
        "suggestion": "" if done else (_CHIPS.get(gap) or ""),
        "complete": done,
    }


def _results(line):
    return [line] if line else ["Nothing was saved this turn."]


def _speak(message, history, line, fields, guest_name, hotel_name):
    gap = _next_name(fields)
    text = "\n".join(
        ["Hotel " + hotel_name, "Guest name " + guest_name, "Conversation"]
        + [("Guest: " if str((item or {}).get("role") or "") == "guest" else "Ella: ") + str((item or {}).get("text") or "")
           for item in list(history or [])[-6:]]
        + ["Guest: " + str(message or ""), "Results"] + _results(line)
        + ["Next field question: " + (_QUESTIONS.get(gap) or ""), "Offer: " + (_OFFERS.get(gap) or "")]
    )
    from booking_system.llm_utils import haiku_text
    try:
        spoken = haiku_text(_REWRITE, text, max_tokens=300, timeout=60).strip()
    except Exception:
        spoken = ""
    return spoken or " ".join(part for part in (line, _QUESTIONS.get(gap, "")) if part).strip()


def _extract(message, history, fields, guest_name, hotel_name):
    from booking_system.llm_utils import haiku_text
    response_text = haiku_text(
        _INSTRUCTIONS,
        _prompt(message, history, fields, guest_name, hotel_name),
        schema=_schema(),
        max_tokens=800,
        timeout=90,
    )
    return json.loads(response_text)


def _open_guest(token):
    from booking_system.guest_link import read_guest_link
    from booking_system.guest_manager import read_guest
    from booking_system.hotel_registry import read_hotel_by_id
    opened = read_guest_link(token)
    if opened.get("error"):
        return None, None, {"error": opened["error"]}
    guest = read_guest(opened["hotel_id"], opened["guest_phone"])
    if not guest or guest.get("status") != "active":
        return None, None, {"error": "Guest profile not found"}
    hotel = read_hotel_by_id(opened["hotel_id"]) or {}
    return opened["hotel_id"], dict(guest, hotel_name=hotel.get("hotel_name") or ""), None


def _greeting(fields, hotel_name):
    name = fields.get("guest_name") or ""
    spoken = name.title() if name.isupper() else name
    hello = "Hey " + spoken + ", I'm Ella." if spoken else "Hey, I'm Ella."
    gap = _next_name(fields)
    if not gap:
        return hello + " Your details for " + hotel_name + " are in."
    return hello + " I'll take your details for " + hotel_name + ", one piece at a time. " + _QUESTIONS[gap]


def guest_open(token):
    """The portal's first screen: who the guest is, what is already known, and Ella's greeting."""
    hotel_id, guest, denied = _open_guest(token)
    if denied:
        return denied
    fields = _from_guest(guest)
    guide = _guide(fields)
    return {
        "greeting": _greeting(fields, guest["hotel_name"]),
        "guest_name": fields["guest_name"],
        "guest_phone": guest.get("guest_phone") or "",
        "hotel_name": guest["hotel_name"],
        **guide,
    }


def guest_turn(token, message, history):
    """One guest line, then Ella's sentence. The signed link chooses the guest."""
    hotel_id, guest, denied = _open_guest(token)
    if denied:
        return denied
    before = _from_guest(guest)
    try:
        parsed = _extract(message, history, before, before["guest_name"], guest["hotel_name"])
    except Exception as error:
        return {"reply": "I could not read that. " + str(error), "error": str(error)}
    line, stored = _save(hotel_id, guest["guest_phone"], before, parsed if isinstance(parsed, dict) else {})
    reply = _speak(message, history, line, stored, stored.get("guest_name") or before["guest_name"], guest["hotel_name"])
    guide = _guide(stored)
    body = {"reply": reply, "guest_name": stored.get("guest_name") or before["guest_name"], "hotel_name": guest["hotel_name"], **guide}
    if guide["complete"]:
        from booking_system.front_desk_manager import intelligent_front_desk
        body["whatsapp_url"] = intelligent_front_desk.whatsapp_click_url(body["guest_name"])
    return body
