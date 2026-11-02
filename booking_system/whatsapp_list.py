"""WhatsApp interactive list messages, matching the Cloud API list request and list_reply webhook."""

_BODY = 4096
_HEADER = 60
_FOOTER = 60
_BUTTON = 20
_SECTION = 24
_ROW_ID = 200
_ROW_TITLE = 24
_ROW_DESCRIPTION = 72
_SECTIONS = 10
_ROWS = 10


def _cut(value, limit):
    return str(value or "").strip()[:limit]


def list_payload(to, header, body, footer, button, sections):
    """The Messages API body for one interactive list. Header and footer are omitted when empty."""
    text = _cut(body, _BODY)
    label = _cut(button, _BUTTON)
    recipient = str(to or "").strip()
    if not text or not label or not recipient:
        return None
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
    if not built:
        return None
    interactive = {"type": "list"}
    head = _cut(header, _HEADER)
    foot = _cut(footer, _FOOTER)
    if head:
        interactive["header"] = {"type": "text", "text": head}
    interactive["body"] = {"text": text}
    if foot:
        interactive["footer"] = {"text": foot}
    interactive["action"] = {"button": label, "sections": built}
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
