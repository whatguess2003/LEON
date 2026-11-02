"""WhatsApp interactive messages, matching the Cloud API list, reply-button, and list_reply shapes.

A list message carries one button that opens up to 10 rows in up to 10 sections. A reply-button message carries one to three
buttons and no list. Header text is 60 characters, footer 60, list button 20, list row title 24, list row description 72,
list row id 200. A reply button title is 20 and its id 256. List body is 4096 characters; button body is 1024."""

_BODY = 4096
_BUTTON_BODY = 1024
_HEADER = 60
_FOOTER = 60
_BUTTON = 20
_BUTTON_TITLE = 20
_BUTTONS = 3
_BUTTON_ID = 256
_SECTION = 24
_ROW_ID = 200
_ROW_TITLE = 24
_ROW_DESCRIPTION = 72
_SECTIONS = 10
_ROWS = 10


def _cut(value, limit):
    return str(value or "").strip()[:limit]


def _sections(sections):
    built = []
    remaining = _ROWS
    for section in sections or []:
        if len(built) >= _SECTIONS or remaining <= 0:
            break
        title = _cut((section or {}).get("title"), _SECTION)
        if not title:
            continue
        rows = []
        for item in (section or {}).get("rows") or []:
            if remaining <= 0:
                break
            row_id = _cut((item or {}).get("id"), _ROW_ID)
            row_title = _cut((item or {}).get("title"), _ROW_TITLE)
            if not row_id or not row_title:
                continue
            row = {"id": row_id, "title": row_title}
            detail = _cut((item or {}).get("description"), _ROW_DESCRIPTION)
            if detail:
                row["description"] = detail
            rows.append(row)
            remaining -= 1
        if rows:
            built.append({"title": title, "rows": rows})
    return built


def _frame(kind, header, body, limit):
    text = _cut(body, limit)
    if not text:
        return None
    interactive = {"type": kind}
    head = _cut(header, _HEADER)
    if head:
        interactive["header"] = {"type": "text", "text": head}
    interactive["body"] = {"text": text}
    return interactive


def _footer(interactive, footer):
    foot = _cut(footer, _FOOTER)
    if foot:
        interactive["footer"] = {"text": foot}
    return interactive


def list_interactive(header, body, footer, button, sections):
    """The interactive object for one list. None without a body, a button, and a row."""
    label = _cut(button, _BUTTON)
    interactive = _frame("list", header, body, _BODY)
    built = _sections(sections)
    if not interactive or not label or not built:
        return None
    _footer(interactive, footer)
    interactive["action"] = {"button": label, "sections": built}
    return interactive


def button_interactive(header, body, footer, buttons):
    """The interactive object for one to three reply buttons. None without a body and a button."""
    interactive = _frame("button", header, body, _BUTTON_BODY)
    if not interactive:
        return None
    built = []
    for item in buttons or []:
        if len(built) >= _BUTTONS:
            break
        row_id = _cut((item or {}).get("id"), _BUTTON_ID)
        title = _cut((item or {}).get("title"), _BUTTON_TITLE)
        if not row_id or not title:
            continue
        built.append({"type": "reply", "reply": {"id": row_id, "title": title}})
    if not built:
        return None
    _footer(interactive, footer)
    interactive["action"] = {"buttons": built}
    return interactive


def list_payload(to, header, body, footer, button, sections):
    """The Messages API body for one interactive list. Header and footer are omitted when empty."""
    recipient = str(to or "").strip()
    interactive = list_interactive(header, body, footer, button, sections)
    if not interactive or not recipient:
        return None
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "interactive",
        "interactive": interactive,
    }


def list_reply(message):
    """The row the user selected. None unless the webhook message is an interactive list_reply."""
    interactive = (message or {}).get("interactive") or {}
    if str(interactive.get("type") or "") != "list_reply":
        return None
    reply = interactive.get("list_reply") or {}
    row_id = _cut(reply.get("id"), _ROW_ID)
    if not row_id:
        return None
    context = (message or {}).get("context") or {}
    return {
        "id": row_id,
        "title": _cut(reply.get("title"), _ROW_TITLE),
        "description": _cut(reply.get("description"), _ROW_DESCRIPTION),
        "context_id": str(context.get("id") or "").strip(),
    }
