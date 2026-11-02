"""WhatsApp stay templates on the guest thread.

confirmed_booking goes out once the guest profile is complete, one message for each confirmed booking.
Check-in sends the welcome template. Check-out sends the checkout template.
The same text is the WhatsApp message and a row on the guest thread, so the desk sees it.
"""
from datetime import datetime, timezone

from booking_system import db


_SPECS = {
    "confirmed_booking": (
        "confirmed_booking",
        "Your booking at {{hotel_name}} is confirmed. Check-in is {{check_in_date}}. The stay is {{total_night}} total nights. Your check-out date is {{check_out_date}}. We will have everything ready for your arrival.",
        (("hotel_name", "Ella Hotel"), ("check_in_date", "2026-11-02"), ("total_night", "1"), ("check_out_date", "2026-11-03")),
    ),
    "welcome": (
        "welcome_hotel",
        "Welcome to {{hotel_name}}. Your room number is {{room_number}}. This stay is {{total_night}} total nights. Your check-out date is {{check_out_date}}. Please tell the front desk if you need anything during your stay.",
        (("hotel_name", "Ella Hotel"), ("room_number", "101"), ("total_night", "1"), ("check_out_date", "2026-11-03")),
    ),
    "checkout": (
        "checkout_stay",
        "You are now checked out of {{hotel_name}}. Your room number was {{room_number}}. The stay was {{total_night}} total nights. Your check-out date was {{check_out_date}}. Thank you for staying with us and we hope to see you again.",
        (("hotel_name", "Ella Hotel"), ("room_number", "101"), ("total_night", "1"), ("check_out_date", "2026-11-03")),
    ),
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _facts(hotel_id, booking_id):
    return db.row(
        """SELECT s.booking_id, s.booking_status, s.check_in_date, s.check_out_date,
                  (s.check_out_date - s.check_in_date) AS nights, h.hotel_name, g.guest_id, g.guest_name,
                  (SELECT i.identity_value FROM guest_identity i
                    WHERE i.guest_id = g.guest_id AND i.identity_type = 'phone'
                    ORDER BY i.identity_value LIMIT 1) AS phone
           FROM booking_summary s
           JOIN hotel h ON h.hotel_id = s.hotel_id
           JOIN guest g ON g.guest_id = s.guest_id
           WHERE s.hotel_id = %s AND s.booking_id = %s""",
        (hotel_id, booking_id),
    )


def _context(facts, room_number, total_night):
    nights = total_night if total_night not in (None, "") else facts["nights"]
    return {
        "guest_name": facts["guest_name"] or "",
        "hotel_name": facts["hotel_name"] or "",
        "room_number": str(room_number or "").strip(),
        "total_night": str(nights),
        "check_in_date": facts["check_in_date"].isoformat(),
        "check_out_date": facts["check_out_date"].isoformat(),
    }


def _keys(guest_id):
    from booking_system.guest_manager import read_document
    stored = read_document(guest_id).get("stay_sent")
    return list(stored) if isinstance(stored, list) else []


def _mark(hotel_id, phone, guest_id, key):
    from booking_system.guest_manager import write_guest_document
    keys = _keys(guest_id)
    if key in keys:
        return
    keys.append(key)
    write_guest_document(hotel_id, phone, {"stay_sent": keys[-80:]})


def ensure_stay_templates():
    """Create the three stay templates on the WhatsApp account when they are missing."""
    from booking_system.whatsapp_templates import ensure_named_template
    made = {}
    for kind, (name, text, examples) in _SPECS.items():
        made[kind] = ensure_named_template(name, text, examples)
    return made


def _source_ref(kind, booking_id, room):
    slug = {"confirmed_booking": "confirmed", "welcome": "welcome", "checkout": "checkout"}.get(kind, kind)
    ref = "stay-" + slug + "-" + booking_id
    if room:
        ref += "-" + room
    return ref[:255]


def _sentence(text, context):
    """Fill the template from the stay. The chat shows this sentence even while Meta has not approved it."""
    from booking_system.whatsapp_templates import _fill
    sentence, _values, _names = _fill(text, context, {}, [])
    return sentence


def _file_card(phone, hotel_id, ref, sentence):
    """One thread row per stay template. A later send keeps this row so the chat does not gain a second copy."""
    found = db.row("SELECT body FROM guest_message WHERE channel = 'whatsapp' AND source_ref = %s", (ref,))
    if found and str(found["body"] or "").strip():
        return str(found["body"])
    from booking_system.guest_manager import save_whatsapp_event
    save_whatsapp_event(ref, phone, hotel_id, _now(), sentence, "out", "template")
    return sentence


def _card(hotel_id, booking_id, kind, room_number="", total_night=None, send=False):
    """The filled stay sentence and its thread id. send also tries the approved WhatsApp template once."""
    spec = _SPECS.get(kind)
    facts = _facts(hotel_id, booking_id)
    if not spec or not facts or not facts.get("phone"):
        return {"error": "Stay notice could not be addressed"}
    room = str(room_number or "").strip()
    name, text, examples = spec
    context = _context(facts, room, total_night)
    sentence = _sentence(text, context)
    if not sentence:
        return {"error": "Stay notice fields are empty"}
    ref = _source_ref(kind, booking_id, room)
    body = _file_card(facts["phone"], hotel_id, ref, sentence)
    card = {"body": body, "source_ref": ref, "booking_id": booking_id, "kind": kind, "template_name": name}
    key = kind + ":" + booking_id + (":" + room if room else "")
    if not send or key in _keys(facts["guest_id"]):
        if key in _keys(facts["guest_id"]):
            card["skipped"] = key
        return card
    from booking_system.whatsapp_templates import ensure_named_template, render_named
    ready = ensure_named_template(name, text, examples)
    if isinstance(ready, dict) and ready.get("error"):
        card["error"] = ready["error"]
        return card
    rendered = render_named(name, context)
    if rendered.get("error"):
        card["error"] = rendered["error"]
        return card
    from booking_system.front_desk_manager import intelligent_front_desk
    sent = intelligent_front_desk.send_whatsapp_template(
        facts["phone"], rendered["template_name"], rendered["language"], rendered["values"],
        rendered["button"], rendered["sentence"], rendered.get("names"), rendered.get("named"),
    )
    if sent.get("error"):
        card["error"] = sent["error"]
        return card
    db.execute("UPDATE guest_message SET status = %s WHERE channel = 'whatsapp' AND source_ref = %s", ("sent", ref))
    _mark(hotel_id, facts["phone"], facts["guest_id"], key)
    card["message_id"] = sent["message_id"]
    return card


def deliver(hotel_id, booking_id, kind, room_number="", total_night=None):
    """Send one stay template and file the sentence on the guest thread. A booking already sent this kind is left as it is."""
    return _card(hotel_id, booking_id, kind, room_number, total_night, send=True)


def notify_welcome(hotel_id, booking_id, room_number, total_night):
    return deliver(hotel_id, booking_id, "welcome", room_number, total_night)


def notify_checkout(hotel_id, booking_id, room_number, total_night):
    return deliver(hotel_id, booking_id, "checkout", room_number, total_night)


def _confirmed_ids(hotel_id, guest_id):
    rows = db.rows(
        """SELECT booking_id FROM booking_summary
           WHERE hotel_id = %s AND guest_id = %s AND booking_status = 'confirmed'
           ORDER BY check_in_date, booking_id""",
        (hotel_id, guest_id),
    )
    return [row["booking_id"] for row in rows]


def notify_confirmed_for_guest(hotel_id, guest_id):
    """One confirmed_booking template for every confirmed stay, after the profile is complete."""
    from booking_system.guest_manager import guest_row
    from booking_system.guest_onboard import profile_complete
    row = db.transaction(lambda cur: guest_row(cur, hotel_id, guest_id))
    if not row:
        return []
    from booking_system.guest_manager import read_document
    guest = dict(read_document(guest_id), **dict(row))
    guest["guest_email"] = row["email"] or ""
    if not profile_complete(guest):
        return [{"skipped": "profile"}]
    return [deliver(hotel_id, booking_id, "confirmed_booking") for booking_id in _confirmed_ids(hotel_id, guest_id)]


def confirmed_cards(hotel_id, guest_id):
    """Filled confirmed_booking lines already owed to this guest. Does not send."""
    from booking_system.guest_manager import guest_row, read_document
    from booking_system.guest_onboard import profile_complete
    row = db.transaction(lambda cur: guest_row(cur, hotel_id, guest_id))
    if not row:
        return []
    guest = dict(read_document(guest_id), **dict(row))
    guest["guest_email"] = row["email"] or ""
    if not profile_complete(guest):
        return []
    return [_card(hotel_id, booking_id, "confirmed_booking", send=False) for booking_id in _confirmed_ids(hotel_id, guest_id)]


def notify_booking_confirmed(hotel_id, guest_id, booking_id):
    """The booking that was just confirmed, when this guest has already finished the profile."""
    from booking_system.guest_manager import guest_row, read_document
    from booking_system.guest_onboard import profile_complete
    row = db.transaction(lambda cur: guest_row(cur, hotel_id, guest_id))
    if not row:
        return {"skipped": "guest"}
    guest = dict(read_document(guest_id), **dict(row))
    guest["guest_email"] = row["email"] or ""
    if not profile_complete(guest):
        return {"skipped": "profile"}
    return deliver(hotel_id, booking_id, "confirmed_booking")
