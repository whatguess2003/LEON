"""Read a guest's stays, messages, and saved files, then write the front-desk brief."""
import base64
import io
import json

_SUMMARY_MODEL = "gpt-6-astra"
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
    "When the guest wants an operation and a required parameter is missing, that sentence asks for it and names the choices already in the stay facts. "
    "When the named booking is not confirmed, the sentence says that operation cannot be done. "
    "If there is no open need, say no action is needed. "
    "executable_actions lists an operation only when every required parameter was stated or is already on the record. "
    "Copy booking ids, room numbers, room types, and dates from the stay facts and messages. "
    "Put each gathered parameter in parameters as name and value. Omit parameters that were not gathered. "
    "When the guest did not ask for an operation, return an empty list. "
    "Use only the working card, file notes, messages, and stay facts given."
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
        "Message " + str(message.get("channel") or "") + " " + str(message.get("direction") or "")
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
    response = client.responses.create(
        model=_SUMMARY_MODEL,
        instructions=_NOTE_INSTRUCTIONS,
        input=[{"role": "user", "content": maker(mime, data, filename, "File")}],
        reasoning={"effort": "low"},
        store=False,
        max_output_tokens=200,
        timeout=90,
    )
    return str(getattr(response, "output_text", "") or "").strip()


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
        _facts(record),
    ])
    parts = [{"type": "input_text", "text": card}]
    for message in _selected_messages(record):
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


def summarize_guest(client, record: dict, read_media) -> dict:
    """Ask GPT-6 Astra for the desk sentence, then keep only actions the system allows."""
    from booking_system.desk_actions import action_names, keep, PARAMS
    from booking_system.guest_manager import guest_manager
    ensure_file_notes(client, record, read_media, guest_manager.save_media_note)
    response = client.responses.create(
        model=_SUMMARY_MODEL,
        instructions=_INSTRUCTIONS,
        input=[{"role": "user", "content": summary_input(record)}],
        text={
            "format": {
                "type": "json_schema",
                "name": "guest_brief",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        **{name: {"type": "string"} for name in _BRIEF_FIELDS},
                        "executable_actions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "string", "enum": action_names()},
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
                        },
                    },
                    "required": list(_BRIEF_FIELDS) + ["executable_actions"],
                    "additionalProperties": False,
                },
            }
        },
        reasoning={"effort": "low"},
        store=False,
        max_output_tokens=2000,
        timeout=90,
    )
    parsed = json.loads(str(getattr(response, "output_text", "") or ""))
    guest = (record or {}).get("guest") or {}
    brief = {name: str(parsed.get(name) or "").strip() for name in _BRIEF_FIELDS}
    brief["executable_actions"] = keep(
        _proposed(parsed.get("executable_actions")),
        (record or {}).get("hotel_name"),
        str(guest.get("guest_phone") or ""),
        (record or {}).get("today"),
    )
    return brief
