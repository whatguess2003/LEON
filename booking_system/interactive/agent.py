"""Load one interactive template from PostgreSQL, fill it from the hotel, and return the Cloud API message."""

from booking_system import db
from booking_system.whatsapp_list import button_interactive, list_interactive

_HOTEL_SQL = """
SELECT h.hotel_name, to_char(hotel_local_date(h.hotel_id), 'YYYY-MM-DD') AS local_date
FROM hotel h WHERE h.hotel_id = %(hotel_id)s
"""


def _split(row_id):
    text = str(row_id or "").strip()
    if "/" not in text:
        return text, ""
    head, record = text.split("/", 1)
    return head.strip(), record.strip()


def _select(sql, params):
    text = str(sql or "").strip()
    if not text:
        return []
    if ";" in text or not text.lstrip().lower().startswith(("select", "with")):
        raise ValueError("template sql is one select")
    return db.rows(text, params)


def _fill(text, values):
    out = str(text or "")
    for key, value in values.items():
        out = out.replace("{" + str(key) + "}", "" if value is None else str(value))
    if "{" in out:
        return "Nothing on file for this hotel."
    return out


def _clip(value, limit):
    return str(value or "").strip()[:limit]


def _choice_rows(template, params):
    found = []
    for row in _select(template["choice_sql"], params):
        found.append({
            "id": _clip(row.get("row_id"), 200),
            "title": _clip(row.get("title"), 24),
            "description": _clip(row.get("description"), 72),
            "section": _clip(row.get("section_title"), 24) or _clip(template["header_text"], 24) or "Open",
        })
    stored = db.rows(
        """SELECT row_id, title, description, section_title FROM wa_interactive_choice
           WHERE template_key = %s ORDER BY choice_order""",
        (template["template_key"],),
    )
    for row in stored:
        found.append({
            "id": _clip(row["row_id"], 200),
            "title": _clip(row["title"], 24),
            "description": _clip(row["description"], 72),
            "section": _clip(row["section_title"], 24) or "Open",
        })
    return [row for row in found if row["id"] and row["title"]]


def _with_back(template, rows):
    if not rows and str(template.get("choice_sql") or "").strip():
        rows.append({
            "id": template["template_key"] + "/none",
            "title": "None",
            "description": "Nothing on file",
            "section": _clip(template["header_text"], 24) or "Open",
        })
    back = str(template.get("back_template_key") or "").strip()
    if back and back not in {row["id"] for row in rows}:
        section = rows[0]["section"] if rows else (_clip(template["header_text"], 24) or "Open")
        rows.append({"id": back, "title": "Back", "description": "Previous menu", "section": section})
    limit = 3 if template["interactive_type"] == "button" else 10
    title_limit = 20 if template["interactive_type"] == "button" else 24
    if len(rows) > limit:
        tail = rows[-1] if rows[-1]["title"] == "Back" else None
        body = rows[:-1] if tail else rows
        keep = limit - (1 if tail else 0)
        rows = body[:keep] + ([tail] if tail else [])
    for row in rows:
        row["title"] = _clip(row["title"], title_limit) or "Choose"
    return rows


def _sections(rows):
    sections = []
    for row in rows:
        if not sections or sections[-1]["title"] != row["section"]:
            sections.append({"title": row["section"], "rows": []})
        sections[-1]["rows"].append(row)
    return sections


def complete(hotel_id, row_id):
    """The filled template. None when the id is not an active template key."""
    head, record = _split(row_id)
    if not head:
        return None
    template = db.row(
        "SELECT * FROM wa_interactive_template WHERE template_key = %s AND status = 'active'",
        (head,),
    )
    if not template:
        return None
    params = {"hotel_id": hotel_id, "record_id": record}
    hotel_rows = _select(_HOTEL_SQL, params)
    values = dict(hotel_rows[0]) if hotel_rows else {}
    missing = False
    if template["complete_sql"]:
        extra = _select(template["complete_sql"], params)
        if extra:
            values.update(extra[0])
        else:
            missing = True
    values["record_id"] = record
    body = "Nothing on file for this hotel." if missing else _fill(template["body_text"], values)
    header = _fill(template["header_text"], values)
    footer = _fill(template["footer_text"], values)
    rows = _with_back(template, _choice_rows(template, params))
    sections = _sections(rows)
    if template["interactive_type"] == "button":
        interactive = button_interactive(header, body, footer, rows)
        label = "Reply"
    else:
        interactive = list_interactive(header, body, footer, template["button_text"], sections)
        label = template["button_text"] or "Open"
    screen = {
        "card": "menu",
        "header": header,
        "body": body,
        "footer": footer,
        "button": label,
        "sections": [{"title": section["title"], "rows": [
            {"id": row["id"], "title": row["title"], "description": row["description"], "kind": "menu", "section": row["section"]}
            for row in section["rows"]
        ]} for section in sections],
        "template_key": head,
        "purpose": template["purpose"],
    }
    message = None
    if interactive:
        message = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "type": "interactive",
            "interactive": interactive,
        }
    return {
        "template_key": head,
        "record_id": record,
        "purpose": template["purpose"],
        "text": body,
        "interactive": interactive,
        "message": message,
        "screen": screen,
    }


def deliver(hotel_name, done):
    """Send the completed interactive message to hotel users whose WhatsApp window is open."""
    from booking_system.front_desk_manager import intelligent_front_desk
    from booking_system.leon_turn import _open_phones
    interactive = (done or {}).get("interactive")
    if not interactive:
        return "closed"
    sent = [phone for phone in _open_phones(hotel_name)
            if not intelligent_front_desk.send_whatsapp_interactive(phone, interactive).get("error")]
    return "sent" if sent else "closed"
