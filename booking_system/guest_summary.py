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
    "recommended_action is the next step the agent should take. If there is no open need, say no action is needed. "
    "Use only the messages, images, PDFs, transcripts, and stay facts given."
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


def _speech_part(client, mime: str, data: bytes, filename: str, label: str):
    try:
        spoken = _transcript(client, mime, data, filename)
    except Exception as exc:
        spoken = "Transcription failed: " + str(exc)
    heard = spoken or "No speech was found."
    return [{"type": "input_text", "text": label + "\nFile " + str(filename or "") + "\nSpoken content: " + heard}]


def _parts_for(client, kind: str, mime: str, data: bytes, filename: str, label: str):
    makers = {"image": _image_part, "file": _file_part}
    maker = makers.get(kind)
    if maker:
        return maker(mime, data, filename, label)
    if kind == "speech":
        return _speech_part(client, mime, data, filename, label)
    return [{"type": "input_text", "text": label}]


def summary_input(client, record: dict, read_media) -> list:
    """Text for every message, plus the image, PDF, or transcript saved with it."""
    parts = [{"type": "input_text", "text": _facts(record)}]
    budget = 0
    for message in (record or {}).get("messages") or []:
        label = _label(message)
        mime = str(message.get("media_mime") or "").split(";")[0].strip().lower()
        kind = _kind(mime)
        stored = read_media(str(message.get("source_ref") or "")) if kind and message.get("source_ref") else None
        data = (stored or {}).get("data") or b""
        filename = str((stored or {}).get("filename") or message.get("media_name") or "")
        limit = _SPEECH_LIMIT if kind == "speech" else _FILE_LIMIT
        too_big = bool(data) and (len(data) > limit or (kind != "speech" and budget + len(data) > _FILE_LIMIT))
        if too_big:
            parts.append({"type": "input_text", "text": label + "\nFile " + filename + " was too large to read."})
            continue
        if data and kind != "speech":
            budget += len(data)
        parts.extend(_parts_for(client, kind, mime, data, filename, label) if data else [{"type": "input_text", "text": label}])
    return parts


def summarize_guest(client, record: dict, read_media) -> dict:
    """Ask GPT-6 Astra for the profile, the guest's needs, and the next action."""
    response = client.responses.create(
        model=_SUMMARY_MODEL,
        instructions=_INSTRUCTIONS,
        input=[{"role": "user", "content": summary_input(client, record, read_media)}],
        text={
            "format": {
                "type": "json_schema",
                "name": "guest_brief",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {name: {"type": "string"} for name in _BRIEF_FIELDS},
                    "required": list(_BRIEF_FIELDS),
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
    return {name: str(parsed.get(name) or "").strip() for name in _BRIEF_FIELDS}
