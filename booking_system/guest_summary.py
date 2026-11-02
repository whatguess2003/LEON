"""Read a guest's stays, messages, and saved files, then write the front-desk brief."""
import base64
import io
import json

_SUMMARY_MODEL = "claude-haiku-5-5"
_TRANSCRIBE_MODEL = "gpt-transcribe"
_FILE_LIMIT = 50 * 1024 * 1024
_SPEECH_LIMIT = 25 * 1024 * 1024
_KINDS = (
    ("image/", "image"),
    ("application/pdf", "file"),
    ("audio/", "speech"),
    ("video/", "speech"),
)
_SPEECH_NAMES = {
    "audio/mpeg": "message.mp3",
    "audio/mp4": "message.m4a",
    "audio/aac": "message.aac",
    "audio/amr": "message.amr",
    "audio/ogg": "message.ogg",
    "video/mp4": "message.mp4",
    "video/3gpp": "message.3gp",
}
_INSTRUCTIONS = (
    "The reader is the human front-desk agent. "
    "guest_profile_summary is who the guest is and the stay facts the agent should know, in one short paragraph. "
    "guest_needs is the guest's concerns and what they need from the hotel. If none were raised, say the guest has not raised a concern or request. "
    "recommended_action is the next sentence that agent says, in a confident voice. "
    "guest_reply is the WhatsApp message back to the guest. Leave guest_reply empty when the guest should not be messaged. "
    "When an operation is allowed, guest_reply tells the guest what the desk will do and does not say that it is already finished. "
    "When the guest wants an operation and a required parameter is missing, guest_reply asks for it and names the choices already in the stay facts. "
    "When the named booking is not confirmed, the sentence says that operation cannot be done. "
    "If there is no open need, say no action is needed. "
    "informational_actions is check_availability when the guest asks whether a room is free. "
    "executional_actions lists a system change only when every required parameter was stated or is already on the record. "
    "stay_dates lists each night the guest sleeps, separated by commas. The checkout morning is not a night. "
    "room_type is copied from the hotel's room types. "
    "When the guest asks to book and the room type and nights are known, create_booking is included. Do not ask the guest to confirm that booking. "
    "desk_tasks lists a manual job for the human desk, one short line each, such as send towel to room 111. "
    "Copy booking ids, room numbers, room types, and dates from the stay facts and messages. "
    "Put each gathered parameter in parameters as name and value. Omit parameters that were not gathered. "
    "When the guest did not ask for an operation or a manual job, return empty lists. "
    "Use only the working card, file notes, messages, and stay facts given. "
    "hotel_specific is true only when the latest message names one hotel. "
    "hotel_name is that hotel when hotel_specific is true, and empty when it does not name a hotel. "
    "During pre-booking, leave hotel_name empty unless the message names one hotel. "
    "subject_phone stays empty."
)
_REPLY_INSTRUCTIONS = (
    "Rewrite guest_reply as one short WhatsApp message using the results. "
    "When a read has numbers, include them. "
    "When a change was completed, say it is done. "
    "When a change is waiting for approval, say the desk will confirm it. "
    "When a result says Not done, say the change was not made. "
    "When a result says Failed, say the change was not made and include the reason. "
    "Do not say a waiting change is already done."
)
_NOTE_INSTRUCTIONS = (
    "Write one short sentence about what this file shows and what the guest needs from it. "
    "Do not invent dates, prices, or room numbers that are not visible."
)
_BRIEF_FIELDS = ("guest_profile_summary", "guest_needs", "recommended_action")


def _kind(mime: str) -> str:
    text = str(mime or "").split(";")[0].strip().lower()
    matched = [kind for prefix, kind in _KINDS if text.startswith(prefix)]
    return matched[0] if matched else ""


def _facts(record: dict) -> str:
    guest = (record or {}).get("guest") or {}
    lines = [
        "Guest " + str(guest.get("guest_name") or "") + " " + str(guest.get("guest_phone") or ""),
        "Country " + str(guest.get("country") or "") + ". Special request " + str(guest.get("special_request") or "") + ".",
        "In house " + str(len(record.get("current") or [])) + ". Future " + str(len(record.get("future") or [])) + ". Past " + str(len(record.get("past") or [])) + ".",
    ]
    for label, rows in (("In house", record.get("current")), ("Future", record.get("future")), ("Past", record.get("past"))):
        for booking in rows or []:
            rooms = ", ".join(str(room.get("room_number") or "") for room in (booking.get("rooms") or []))
            lines.append(
                label + " " + str(booking.get("booking_id") or "") + " " + str(booking.get("booking_status") or "")
                + " " + str(booking.get("check_in") or "") + " to " + str(booking.get("check_out") or "")
                + " " + str(booking.get("room_type") or "") + " rooms " + rooms
                + " amount " + str(booking.get("total_amount") or "")
            )
    return "\n".join(lines)


def _label(message: dict) -> str:
    return (
        "Message " + str(message.get("hotel_name") or "") + " " + str(message.get("channel") or "") + " " + str(message.get("direction") or "")
        + " " + str(message.get("sent_at") or "") + ": " + str(message.get("body") or "")
    )


def _speech_name(mime: str, filename: str) -> str:
    given = str(filename or "")
    if "." in given:
        return given
    return _SPEECH_NAMES.get(mime) or "message.mp4"


def _transcript(client, mime: str, data: bytes, filename: str) -> str:
    audio = io.BytesIO(data)
    audio.name = _speech_name(mime, filename)
    result = client.audio.transcriptions.create(model=_TRANSCRIBE_MODEL, file=audio, timeout=60)
    return str(getattr(result, "text", "") or "").strip()


def _image_part(mime: str, data: bytes, filename: str, label: str):
    encoded = base64.b64encode(data).decode()
    return [
        {"type": "input_text", "text": label + "\nImage " + str(filename or "")},
        {"type": "input_image", "image_url": "data:" + mime + ";base64," + encoded},
    ]


def _file_part(mime: str, data: bytes, filename: str, label: str):
    encoded = base64.b64encode(data).decode()
    name = str(filename or "file.pdf")
    return [
        {"type": "input_text", "text": label + "\nPDF " + name},
        {"type": "input_file", "filename": name, "file_data": "data:application/pdf;base64," + encoded},
    ]


def _file_note(client, kind: str, mime: str, data: bytes, filename: str) -> str:
    if kind == "speech":
        spoken = _transcript(client, mime, data, filename)
        return spoken or "No speech was found."
    maker = {"image": _image_part, "file": _file_part}.get(kind)
    if not maker:
        return ""
    from booking_system.llm_utils import haiku_text
    return haiku_text(_NOTE_INSTRUCTIONS, maker(mime, data, filename, "File"), max_tokens=200, timeout=90)


def ensure_file_notes(client, record: dict, read_media, save_note):
    """Write a file note once. Later briefs read the note and do not resend the bytes."""
    for message in (record or {}).get("messages") or []:
        if str(message.get("media_note") or "").strip():
            continue
        mime = str(message.get("media_mime") or "").split(";")[0].strip().lower()
        kind = _kind(mime)
        ref = str(message.get("source_ref") or "")
        if not kind or not ref:
            continue
        stored = read_media(ref)
        data = (stored or {}).get("data") or b""
        if not data:
            continue
        filename = str((stored or {}).get("filename") or message.get("media_name") or "")
        limit = _SPEECH_LIMIT if kind == "speech" else _FILE_LIMIT
        if len(data) > limit:
            note = "File " + filename + " was too large to read."
        else:
            try:
                note = _file_note(client, kind, mime, data, filename)
            except Exception:
                note = "File could not be read."
        note = str(note or "").strip()
        if not note:
            continue
        message["media_note"] = note
        if save_note:
            save_note(ref, note)


def _selected_messages(record: dict) -> list:
    messages = list((record or {}).get("messages") or [])
    marker = str(((record or {}).get("guest") or {}).get("ai_summary_at") or "")
    if not marker:
        return messages
    latest_in = ""
    for message in messages:
        sent = str(message.get("sent_at") or "")
        if str(message.get("direction") or "") == "in" and sent > latest_in:
            latest_in = sent
    chosen = []
    for message in messages:
        sent = str(message.get("sent_at") or "")
        inbound = str(message.get("direction") or "") == "in" and sent == latest_in
        if sent > marker or inbound:
            chosen.append(message)
    return chosen


def summary_input(record: dict) -> list:
    """Working card, action catalog, stay facts, and messages newer than the card."""
    from booking_system.desk_actions import catalog_text
    guest = (record or {}).get("guest") or {}
    card = "\n".join([
        "Today " + str((record or {}).get("today") or ""),
        "Working card",
        "Profile " + str(guest.get("guest_profile_summary") or ""),
        "Needs " + str(guest.get("guest_needs") or ""),
        "Previous action " + str(guest.get("recommended_action") or ""),
        "Actions",
        catalog_text(),
        str((record or {}).get("stage_text") or ""),
        _facts(record),
    ])
    parts = [{"type": "input_text", "text": card}]
    chosen = (record or {}).get("phone_messages")
    if chosen is None:
        chosen = _selected_messages(record)
    for message in chosen:
        note = str(message.get("media_note") or "").strip()
        text = _label(message) + ("\nFile note: " + note if note else "")
        parts.append({"type": "input_text", "text": text})
    return parts


def _proposed(items):
    from booking_system.desk_actions import PARAMS
    names = set(PARAMS)
    rows = []
    for item in items or []:
        row = {"action": str((item or {}).get("action") or "")}
        for pair in (item or {}).get("parameters") or []:
            name = str((pair or {}).get("name") or "")
            if name in names and name not in row:
                row[name] = str((pair or {}).get("value") or "").strip()
        rows.append(row)
    return rows


def _action_schema(names):
    from booking_system.desk_actions import PARAMS
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(names)},
                "parameters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "enum": list(PARAMS)},
                            "value": {"type": "string"},
                        },
                        "required": ["name", "value"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["action", "parameters"],
            "additionalProperties": False,
        },
    }


def rewrite_reply(client, draft: str, facts) -> str:
    """One short call so the guest reply matches the reads and writes that just happened."""
    from booking_system.llm_utils import haiku_text
    text = haiku_text(
        _REPLY_INSTRUCTIONS,
        "Draft\n" + str(draft or "") + "\nResults\n" + "\n".join(facts or []),
        max_tokens=300,
        timeout=60,
    ).strip()
    return text or str(draft or "").strip()


def _turn_schema():
    from booking_system.desk_actions import action_names, information_names
    return {
        "type": "object",
        "properties": {
            **{name: {"type": "string"} for name in _BRIEF_FIELDS},
            "guest_reply": {"type": "string"},
            "hotel_specific": {"type": "boolean"},
            "hotel_name": {"type": "string"},
            "subject_phone": {"type": "string"},
            "informational_actions": _action_schema(information_names()),
            "executional_actions": _action_schema(action_names()),
            "desk_tasks": {"type": "array", "items": {"type": "string"}},
        },
        "required": list(_BRIEF_FIELDS) + [
            "guest_reply",
            "hotel_specific",
            "hotel_name",
            "subject_phone",
            "informational_actions",
            "executional_actions",
            "desk_tasks",
        ],
        "additionalProperties": False,
    }


def _brief_from_parsed(parsed):
    brief = {name: str((parsed or {}).get(name) or "").strip() for name in _BRIEF_FIELDS}
    brief["guest_reply"] = str((parsed or {}).get("guest_reply") or "").strip()
    brief["hotel_specific"] = bool((parsed or {}).get("hotel_specific"))
    brief["hotel_name"] = str((parsed or {}).get("hotel_name") or "").strip().upper()
    brief["subject_phone"] = str((parsed or {}).get("subject_phone") or "").strip()
    brief["informational_actions"] = _proposed((parsed or {}).get("informational_actions"))
    brief["executional_actions"] = _proposed((parsed or {}).get("executional_actions"))
    brief["desk_tasks"] = [str(text or "").strip() for text in ((parsed or {}).get("desk_tasks") or []) if str(text or "").strip()]
    return brief


def summarize_guest(client, record: dict, read_media) -> dict:
    """Ask the desk model for the sentence. Python keeps the actions after the hotel is known."""
    from booking_system.guest_manager import guest_manager
    ensure_file_notes(client, record, read_media, guest_manager.save_media_note)
    from booking_system.llm_utils import haiku_text
    raw = haiku_text(_INSTRUCTIONS, summary_input(record), schema=_turn_schema(), max_tokens=2000, timeout=90)
    return _brief_from_parsed(json.loads(raw))


_DESK_INSTRUCTIONS = (
    "You are talking to the front desk of the hotel named in the input. "
    "guest_reply is one short sentence back to the front desk. "
    "hotel_specific is true only when this message names one hotel. "
    "hotel_name is that hotel when hotel_specific is true, and empty otherwise. "
    "subject_phone is the guest this turn is about, and empty when no guest is known. "
    "You do not write SQL. Leave catalog_ids empty. A read is chosen before this call. "
    "read_parameters lists booking_id, guest_phone, guest_name, room_type, stay_dates, and room_number when the desk stated them. "
    "resolutions are the dates for this step. When a day or span is confident, resolved_date is MM/DD/YYYY copied from the date facts. "
    "When it is not confident, unresolved is true, clarification asks, and stay date values stay empty. "
    "When an action needs nights and a resolution is confident, stay_dates is those MM/DD/YYYY values. "
    "informational_actions is check_availability when someone asks whether a room is free. "
    "check_availability includes room_type and stay_dates when Dates has a resolved day. The checkout morning is not a night. "
    "executional_actions lists a system change only when its required fields are known. "
    "create_booking is included while the desk is still collecting the guest name, phone, room type, rooms, dates, guest count, or special request, with every stated field filled and the missing ones empty. "
    "desk_tasks lists a manual job, one short line each. "
    "When the message does not ask for an operation, return empty lists. "
    "topic is the job already underway. Keep topic as availability while the desk stays on rooms left, prices, blocked rooms, night rules, channel allotment, or a discrepancy between the booked count and the rooms. "
    "Set topic to occupancy when the desk asks which guest is in which room, who is in house, or which rooms are empty. Keep topic as occupancy while that question continues. "
    "Set topic to guest when the desk asks about a guest profile, stay history, preference, request, complaint, message, balance, or VIP flag. Keep topic as guest while that question continues. "
    "Set topic to reservation when the desk asks for a booking status, who has not arrived, an arrival, a departure, or a no-show. Keep topic as reservation while that question continues. "
    "Set topic to another action only when the desk asks for that job. Leave topic empty when there is no job. "
    "guide_actions is true when the desk greets, the message has no operation, and topic is empty. "
    "guide_actions is false when topic is availability, occupancy, guest, or reservation, catalog_ids is not empty, or the message asks for an action, a task, or a fact. "
    "When the open queue is empty, screen rows are empty and header, footer, and button are empty. "
    "When the open queue has ids, copy those ids into screen rows. "
    "pin_category is urgent, to-do, follow-up, or insights when this reply should stay pinned, and empty otherwise."
)


def _topic_names():
    from booking_system.desk_actions import action_names
    return ["", "availability", "occupancy", "guest", "reservation"] + action_names()


def _desk_schema():
    row = {
        "type": "object",
        "properties": {
            "section": {"type": "string"},
            "id": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["section", "id", "title", "description"],
        "additionalProperties": False,
    }
    screen = {
        "type": "object",
        "properties": {
            "header": {"type": "string"},
            "body": {"type": "string"},
            "footer": {"type": "string"},
            "button": {"type": "string"},
            "rows": {"type": "array", "items": row},
        },
        "required": ["header", "body", "footer", "button", "rows"],
        "additionalProperties": False,
    }
    from booking_system.desk_actions import action_names, information_names
    from booking_system.semantic.cache import READ_PARAMS
    from booking_system.semantic.catalog import desk_ids
    from booking_system.semantic.dates import resolution_schema
    return {
        "type": "object",
        "properties": {
            "guest_reply": {"type": "string"},
            "hotel_specific": {"type": "boolean"},
            "hotel_name": {"type": "string"},
            "subject_phone": {"type": "string"},
            "informational_actions": _action_schema(information_names()),
            "executional_actions": _action_schema(action_names()),
            "desk_tasks": {"type": "array", "items": {"type": "string"}},
            "screen": screen,
            "pin_category": {"type": "string", "enum": ["", "urgent", "to-do", "follow-up", "insights"]},
            "guide_actions": {"type": "boolean"},
            "topic": {"type": "string", "enum": _topic_names()},
            "resolutions": {"type": "array", "items": resolution_schema()},
            "catalog_ids": {
                "type": "array",
                "items": {"type": "string", "enum": desk_ids()},
            },
            "read_parameters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": list(READ_PARAMS)},
                        "value": {"type": "string"},
                    },
                    "required": ["name", "value"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "guest_reply",
            "hotel_specific",
            "hotel_name",
            "subject_phone",
            "informational_actions",
            "executional_actions",
            "desk_tasks",
            "screen",
            "pin_category",
            "guide_actions",
            "topic",
            "resolutions",
            "catalog_ids",
            "read_parameters",
        ],
        "additionalProperties": False,
    }


def _desk_from_parsed(parsed):
    brief = _brief_from_parsed(parsed)
    screen = (parsed or {}).get("screen") or {}
    rows = []
    for item in screen.get("rows") or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "section": str(item.get("section") or "").strip(),
            "id": str(item.get("id") or "").strip(),
            "title": str(item.get("title") or "").strip(),
            "description": str(item.get("description") or "").strip(),
        })
    brief["screen"] = {
        "header": str(screen.get("header") or "").strip(),
        "body": str(screen.get("body") or "").strip(),
        "footer": str(screen.get("footer") or "").strip(),
        "button": str(screen.get("button") or "").strip(),
        "rows": rows,
    }
    brief["pin_category"] = str((parsed or {}).get("pin_category") or "").strip()
    brief["guide_actions"] = (parsed or {}).get("guide_actions") is True
    from booking_system.semantic.cache import usable
    from booking_system.semantic.catalog import index_ids
    known = set(index_ids())
    brief["topic"] = str((parsed or {}).get("topic") or "")
    ids = []
    for item in (parsed or {}).get("catalog_ids") or []:
        name = str(item or "")
        if name in known and name not in ids:
            ids.append(name)
    brief["catalog_ids"] = ids
    brief["read_parameters"] = usable((parsed or {}).get("read_parameters"))
    from booking_system.semantic.dates import accept_resolutions
    brief["dates"] = accept_resolutions((parsed or {}).get("resolutions"))
    return brief


def desk_reply(client, hotel, manager, message, history, queue_text, pin_text, topic="", evidence="", dates=None):
    """One call for the front-desk chat. The hotel on the call is the desk's hotel; manager is the desk user_id."""
    from booking_system import db
    from booking_system.desk_actions import catalog_text
    from booking_system.semantic.dates import DATE_RULES, date_facts
    from booking_system.semantic.hotel import session_hotel_id
    user = db.row("SELECT user_name FROM user_account WHERE user_id = %s", (str(manager or ""),))
    lines = [
        "Hotel " + str(hotel or ""),
        "Manager " + (user["user_name"] if user else ""),
        "Topic " + str(topic or ""),
        "Open queue",
        str(queue_text or "None"),
        "Pinned",
        str(pin_text or "None"),
        "Actions",
        catalog_text(),
        "Reads",
        str(evidence or "None"),
        "Date facts",
        json.dumps(date_facts(session_hotel_id(hotel)), default=str),
        "Conversation",
    ]
    from booking_system.leon_meta_orchestrator import LEONMetaOrchestrator
    past = list(history or [])[-10:]
    for index, entry in enumerate(past):
        sender, text = LEONMetaOrchestrator.dialog_line(None, entry, index)
        speaker = "Desk" if sender == "user" else "Leon"
        lines.append(speaker + ": " + text)
    lines.append("Desk: " + str(message or ""))
    from booking_system.llm_utils import haiku_text
    raw = haiku_text(_DESK_INSTRUCTIONS + " " + DATE_RULES, "\n".join(lines), schema=_desk_schema(), max_tokens=1200, timeout=90)
    return _desk_from_parsed(json.loads(raw))
