"""One turn for a guest or the front desk. The hotel is chosen here, then keep and settle run. Callers name the desk hotel by
hotel_name (the workflow contract); pins, screens, seen cards and WhatsApp sync documents are keyed by hotel_id. A hotel user on
WhatsApp is found by their phone identity, and their turns share the web desk's chat (hotel_name, user_id)."""
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor


def _client():
    from openai import OpenAI
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _hotel_id(hotel):
    from booking_system.hotel_registry import hotel_id
    return hotel_id(hotel)


def _today(hotel):
    from booking_system.desk_actions import hotel_today
    return hotel_today(_hotel_id(hotel)).isoformat()


def prepare_guest(phone):
    """Stage, hotel list, and recent messages before the model call."""
    from booking_system.desk_actions import hotel_names, stage_for_phone, stage_text
    from booking_system.guest_manager import guest_manager
    stage = stage_for_phone(phone)
    hotels = hotel_names()
    return {
        "stage": stage,
        "stage_text": stage_text(stage, hotels),
        "phone_messages": guest_manager.messages_for_phone(phone)[-12:],
    }


def finish_guest(client, phone, brief):
    """Resolve the hotel, then read or write only when that hotel is known. Leon acts for the guest (no user)."""
    from booking_system.desk_actions import hotel_names, keep, resolve_hotel, save_settled, settle, stage_for_phone
    from booking_system.guest_manager import guest_manager
    from booking_system.guest_summary import rewrite_reply
    resolved = resolve_hotel(stage_for_phone(phone), brief.get("hotel_specific"), brief.get("hotel_name"), hotel_names())
    if resolved.get("ask"):
        brief["guest_reply"] = resolved["ask"]
        return {"brief": brief, "acted": False}
    hotel_id = _hotel_id(resolved["hotel"])
    proposed = list(brief.get("executional_actions") or [])
    brief["executional_actions"] = keep(proposed, hotel_id, phone, _today(resolved["hotel"]))
    guest = guest_manager.read_guest(hotel_id, phone)
    settled = {"lines": [], "completed": []}
    if guest and guest["status"] == "active":
        settled = settle(brief, hotel_id, None, guest)
        save_settled(hotel_id, None, guest, settled)
    if proposed and not brief["executional_actions"]:
        settled["lines"] = list(settled["lines"]) + ["Not done"]
    if settled["lines"]:
        try:
            brief["guest_reply"] = rewrite_reply(client, brief.get("guest_reply"), settled["lines"])
        except Exception:
            pass
    return {"brief": brief, "acted": bool(settled.get("completed"))}


def _queue_text(hotel):
    from booking_system.guest_manager import guest_manager
    approvals, tasks = guest_manager.desk_queue(_hotel_id(hotel))
    lines = []
    for row in approvals:
        lines.append("Approval id: " + str(row.get("label") or ""))
        lines.append(str(row.get("guest_name") or "") + " " + str(row.get("guest_phone") or ""))
    for task in tasks:
        lines.append("Task id: " + str(task.get("task") or ""))
        lines.append(str(task.get("guest_name") or "") + " " + str(task.get("guest_phone") or ""))
    return "\n".join(lines)


_PIN_CATEGORIES = ("urgent", "to-do", "follow-up", "insights")
_PIN_RANK = {"urgent": 0, "to-do": 1, "follow-up": 2, "insights": 3}


def _clip(value, limit):
    return str(value or "").strip()[:limit]


_chat_indexes = False


def _chat_collections():
    global _chat_indexes
    from booking_system.guest_manager import guest_manager
    pins = guest_manager.records["hotel_pins"]
    screen = guest_manager.records["hotel_screen"]
    seen = guest_manager.records["hotel_card_seen"]
    if not _chat_indexes:
        pins.create_index("hotel_id", name="hotel_id_pins")
        screen.create_index("hotel_id", unique=True, name="hotel_id_screen")
        seen.create_index("hotel_id", name="hotel_id_seen")
        _chat_indexes = True
    return pins, screen, seen


def _pin_collection():
    pins, _screen, _seen = _chat_collections()
    return pins


def _screen_collection():
    _pins, screen, _seen = _chat_collections()
    return screen


def _seen_collection():
    _pins, _screen, seen = _chat_collections()
    return seen


def pin_message(hotel, text, category, who):
    """Read the pin. Update it when this hotel and text already exist, otherwise create it."""
    hotel_id = _hotel_id(hotel)
    body = str(text or "").strip()
    kind = str(category or "").strip()
    if not hotel_id or not body or kind not in _PIN_CATEGORIES:
        return "missing"
    from datetime import datetime, timezone
    coll = _pin_collection()
    key = {"hotel_id": hotel_id, "text": body}
    fields = {"category": kind, "who": str(who or ""), "pinned_at": datetime.now(timezone.utc).isoformat()}
    if coll.find_one(key):
        coll.update_one(key, {"$set": fields})
        return "updated"
    coll.insert_one({**key, **fields})
    return "created"


def unpin_message(hotel, text):
    """Read the pin. Delete it when this hotel and text exist."""
    coll = _pin_collection()
    key = {"hotel_id": _hotel_id(hotel), "text": str(text or "").strip()}
    if not coll.find_one(key):
        return "missing"
    coll.delete_one(key)
    return "deleted"


def list_pins(hotel):
    """Every pin for this hotel, in screen order."""
    rows = list(_pin_collection().find({"hotel_id": _hotel_id(hotel)}, {"_id": 0}))
    rows.sort(key=lambda row: (_PIN_RANK.get(str(row.get("category") or ""), 4), 0 if str(row.get("who") or "") == "user" else 1, str(row.get("pinned_at") or "")))
    return rows


def shown_pins(hotel):
    """Three pins for the column. Urgent, then to-do, follow-up, insights. A user pin comes before an agent pin. Older first."""
    rows = list_pins(hotel)
    return rows[:3], len(rows)


def _pin_text(hotel):
    shown, _count = shown_pins(hotel)
    return "\n".join(str(row.get("category") or "") + " " + str(row.get("text") or "") for row in shown)


def _kept_screen(hotel, brief):
    """Keep list rows whose id is an open approval label or task. The agent wrote the words."""
    from booking_system.guest_manager import guest_manager
    raw = (brief or {}).get("screen") or {}
    approvals, tasks = guest_manager.desk_queue(_hotel_id(hotel))
    known = {}
    for row in approvals:
        if row.get("label"):
            known[str(row["label"])] = {"kind": "approval", "record": row}
    for row in tasks:
        if row.get("task"):
            known[str(row["task"])] = {"kind": "task", "record": row}
    rows = []
    for item in raw.get("rows") or []:
        ident = str((item or {}).get("id") or "").strip()
        match = known.get(ident)
        if not match or len(rows) >= 10:
            continue
        rows.append({
            "section": _clip((item or {}).get("section"), 24),
            "id": ident[:200],
            "title": _clip((item or {}).get("title"), 24),
            "description": _clip((item or {}).get("description"), 72),
            "kind": match["kind"],
            "record": {key: value for key, value in (match["record"] or {}).items() if key != "_id"},
        })
    if not rows:
        return None
    sections = []
    for row in rows:
        if not sections or sections[-1]["title"] != row["section"]:
            sections.append({"title": row["section"], "rows": []})
        sections[-1]["rows"].append({key: row[key] for key in ("id", "title", "description", "kind", "record")})
    body = _clip(raw.get("body"), 1024) or _clip((brief or {}).get("guest_reply"), 1024)
    return {
        "hotel_name": str(hotel or "").upper(),
        "header": _clip(raw.get("header"), 60),
        "body": body,
        "footer": _clip(raw.get("footer"), 60),
        "button": _clip(raw.get("button"), 20),
        "sections": sections,
    }


def store_screen(hotel, screen):
    """Read the hotel's list. Update it, or delete it when this turn has no rows."""
    coll = _screen_collection()
    key = {"hotel_id": _hotel_id(hotel)}
    existing = coll.find_one(key)
    if not screen:
        if existing:
            coll.delete_one(key)
        return "deleted"
    coll.update_one(key, {"$set": {"screen": screen}}, upsert=True)
    return "updated" if existing else "created"


def read_screen(hotel):
    doc = _screen_collection().find_one({"hotel_id": _hotel_id(hotel)}, {"_id": 0})
    return doc.get("screen") if doc else None


def drop_screen_row(hotel, row_id):
    """Read the stored list and remove the chosen row."""
    ident = str(row_id or "")
    coll = _screen_collection()
    key = {"hotel_id": _hotel_id(hotel)}
    doc = coll.find_one(key)
    if not doc:
        return "missing"
    screen = dict(doc.get("screen") or {})
    sections = []
    for section in screen.get("sections") or []:
        kept = [row for row in (section.get("rows") or []) if str(row.get("id") or "") != ident]
        if kept:
            sections.append({"title": section.get("title") or "", "rows": kept})
    if not sections:
        coll.delete_one(key)
        return "deleted"
    screen["sections"] = sections
    coll.update_one(key, {"$set": {"screen": screen}})
    return "updated"


def _subject_phone(hotel_id, brief):
    from booking_system.desk_actions import booking_phone
    phone = str(brief.get("subject_phone") or "").strip()
    if phone:
        return phone
    found = []
    for row in brief.get("executional_actions") or []:
        one = str((row or {}).get("guest_phone") or "").strip() or booking_phone(hotel_id, (row or {}).get("booking_id"))
        if one and one not in found:
            found.append(one)
    return found[0] if len(found) == 1 else ""


def apply_desk(hotel, brief, user_id=None):
    """Run the desk's actions at the session hotel as user_id. A different named hotel is not written."""
    from booking_system.desk_actions import hotel_names, keep, save_settled, settle
    from booking_system.guest_manager import guest_manager
    from booking_system.guest_summary import rewrite_reply
    hotel_name = str(hotel or "").upper()
    hotel_id = _hotel_id(hotel_name)
    named = str(brief.get("hotel_name") or "").upper()
    if brief.get("hotel_specific") and named and named != hotel_name:
        known = hotel_names()
        brief["guest_reply"] = ("Which hotel? " + ", ".join(known) if named not in known
                                else "This desk is " + hotel_name + ". This turn is about " + named + ".")
        brief["listed_actions"] = []
        return brief, False
    brief["listed_actions"] = list(brief.get("executional_actions") or [])
    phone = _subject_phone(hotel_id, brief)
    guest = guest_manager.read_guest(hotel_id, phone) if phone else None
    lines, acted = [], False
    if guest and guest["status"] == "active":
        proposed = list(brief.get("executional_actions") or [])
        brief["executional_actions"] = keep(proposed, hotel_id, phone, _today(hotel_name))
        settled = settle(brief, hotel_id, user_id, guest)
        save_settled(hotel_id, user_id, guest, settled)
        lines = list(settled["lines"]) + (["Not done"] if proposed and not brief["executional_actions"] else [])
        acted = bool(settled.get("completed"))
    brief["result_lines"] = lines
    if lines:
        try:
            brief["guest_reply"] = rewrite_reply(_client(), brief.get("guest_reply"), lines)
        except Exception:
            pass
    return brief, acted


# ---------- the hotel user on WhatsApp ----------

def manager_account(phone):
    """The active hotel user holding this phone identity: {'user_id', 'user_name', 'phone', 'hotel_id', 'hotel_name'} at their last
    hotel when that membership is active, else at their only active hotel. None for a guest or an unclear hotel."""
    from booking_system import db
    rows = db.rows(
        """SELECT u.user_id, u.user_name, i.identity_value AS phone, h.hotel_id, h.hotel_name, h.hotel_id = u.last_hotel_id AS last_hotel
           FROM user_identity i JOIN user_account u ON u.user_id = i.user_id
           JOIN hotel_user hu ON hu.user_id = u.user_id AND hu.status = 'active' JOIN hotel h ON h.hotel_id = hu.hotel_id
           WHERE i.identity_type = 'phone' AND i.identity_value = %s AND u.status = 'active'""",
        (str(phone or "").strip(),))
    chosen = [row for row in rows if row["last_hotel"]] or rows
    return {key: chosen[0][key] for key in ("user_id", "user_name", "phone", "hotel_id", "hotel_name")} if len(chosen) == 1 else None


def note_manager_inbound(user_id, sent_at):
    """READ the user's WhatsApp window; move it to this inbound moment when later, or create it."""
    from booking_system import db
    moment = str(sent_at or "").strip()
    if not user_id or not moment:
        return "missing"

    def write(cur):
        cur.execute("SELECT last_inbound_at FROM user_whatsapp_window WHERE user_id = %s FOR UPDATE", (user_id,))
        if cur.fetchone():
            cur.execute("UPDATE user_whatsapp_window SET last_inbound_at = GREATEST(last_inbound_at, %s::timestamptz) WHERE user_id = %s",
                        (moment, user_id))
            return "updated"
        cur.execute("INSERT INTO user_whatsapp_window (user_id, last_inbound_at) VALUES (%s, %s::timestamptz)", (user_id, moment))
        return "created"
    return db.transaction(write)


def _open_phones(hotel):
    """Phones of the hotel's active users whose WhatsApp window is open (an inbound in the last 24 hours)."""
    from booking_system import db
    rows = db.rows(
        """SELECT DISTINCT i.identity_value FROM hotel_user hu JOIN user_account u ON u.user_id = hu.user_id AND u.status = 'active'
           JOIN user_identity i ON i.user_id = u.user_id AND i.identity_type = 'phone'
           JOIN user_whatsapp_window w ON w.user_id = u.user_id
           WHERE hu.hotel_id = %s AND hu.status = 'active' AND w.last_inbound_at > now() - interval '24 hours'""",
        (_hotel_id(hotel),))
    return [row["identity_value"] for row in rows]


def _card_seen(hotel, card, day):
    return _seen_collection().find_one({"hotel_id": _hotel_id(hotel), "card": card, "day": day}) is not None


def _mark_seen(hotel, card, day, how):
    coll = _seen_collection()
    key = {"hotel_id": _hotel_id(hotel), "card": card, "day": day}
    if coll.find_one(key):
        coll.update_one(key, {"$set": {"how": how}})
        return "updated"
    coll.insert_one({**key, "how": how})
    return "created"


def sales_facts(hotel):
    """Today's arrivals, bookings in house against active rooms, and the average sell rate of tonight's sold nights."""
    from booking_system import db
    hotel_id = _hotel_id(hotel)
    day = _today(hotel)
    found = db.row(
        """SELECT (SELECT COUNT(*) FROM booking_summary s WHERE s.hotel_id = %(h)s AND s.booking_status IN ('confirmed','checked_in')
                     AND s.check_in_date = %(d)s) AS bookings,
                  (SELECT COUNT(*) FROM booking_summary s WHERE s.hotel_id = %(h)s AND s.booking_status IN ('confirmed','checked_in')
                     AND s.check_in_date <= %(d)s AND s.check_out_date > %(d)s) AS in_house,
                  (SELECT COUNT(*) FROM room r WHERE r.hotel_id = %(h)s AND r.status = 'active') AS rooms,
                  (SELECT AVG(n.sell_rate) FROM booking_room_night n WHERE n.hotel_id = %(h)s AND n.stay_date = %(d)s
                     AND n.night_status IN ('booked','checked_in') AND n.sell_rate > 0) AS rate""",
        {"h": hotel_id, "d": day})
    return {"day": day, "bookings": found["bookings"], "in_house": found["in_house"], "rooms": found["rooms"],
            "rate": int(found["rate"] or 0)}


def _sales_body(facts):
    line = (
        "Bookings " + str(facts["bookings"])
        + ". In house " + str(facts["in_house"])
        + " of " + str(facts["rooms"])
        + ". Rate " + str(facts["rate"]) + "."
    )
    from booking_system.llm_utils import haiku_text
    try:
        raw = haiku_text(
            "Write one sentence for the hotel user from these sales facts. Use only these numbers. Do not add a count that is not here.",
            line,
            schema={
                "type": "object",
                "properties": {"body": {"type": "string"}},
                "required": ["body"],
                "additionalProperties": False,
            },
            max_tokens=200,
            timeout=30,
        )
        import json
        body = str((json.loads(raw) or {}).get("body") or "").strip()
    except Exception:
        body = ""
    return _clip(body, 200) or _clip(line, 200)


def _sales_screen(hotel, day, body):
    return {
        "hotel_name": hotel,
        "card": "sales",
        "day": day,
        "header": "Sales Pulse",
        "body": body,
        "footer": _clip(hotel, 60),
        "button": "Open",
        "whatsapp_sent": False,
        "sections": [{
            "title": "Sales Pulse",
            "rows": [
                {"id": "pin", "title": "Pin to bar", "description": "", "kind": "pin"},
                {"id": "read", "title": "I have read", "description": "", "kind": "read"},
                {"id": "dismiss", "title": "Dismiss", "description": "", "kind": "dismiss"},
            ],
        }],
    }


def _sync_collection():
    from booking_system.guest_manager import guest_manager
    coll = guest_manager.records["whatsapp_sync"]
    if not getattr(_sync_collection, "ready", False):
        coll.create_index("source_ref", unique=True)
        _sync_collection.ready = True
    return coll


def remember_outbound(message_id, hotel_id, screen):
    """Remember a list this desk sent, so a webhook echo of that id is not copied back."""
    ref = str(message_id or "").strip()
    if not ref:
        return "missing"
    coll = _sync_collection()
    if coll.find_one({"source_ref": ref}):
        return "duplicate"
    try:
        coll.insert_one({
            "source_ref": ref,
            "hotel_id": hotel_id,
            "direction": "out",
            "message_type": "list",
            "applied": True,
            "sections": (screen or {}).get("sections") or [],
        })
    except Exception as error:
        if error.__class__.__name__ != "DuplicateKeyError":
            raise
        return "duplicate"
    return "created"


def _pending_summary(history):
    """Leon webpage turns not yet sent to WhatsApp. WhatsApp turns are already on that chat."""
    from booking_system.leon_meta_orchestrator import LEONMetaOrchestrator
    lines = []
    indexes = []
    for index, entry in enumerate(list(history or [])[-20:]):
        if isinstance(entry, dict) and (entry.get("summarized") or entry.get("channel") == "whatsapp"):
            continue
        sender, text = LEONMetaOrchestrator.dialog_line(None, entry, index)
        text = str(text or "").strip()
        if not text:
            continue
        lines.append(("Desk" if sender == "user" else "Leon") + ": " + text)
        indexes.append(index)
    return lines, indexes


def _one_summary(lines):
    """One WhatsApp text for the webpage chat. Empty when the model does not answer."""
    try:
        from booking_system.llm_utils import haiku_text
        text = haiku_text(
            (
                "Summarize this Leon chat as one WhatsApp text for the hotel user. "
                "Keep the facts. Do not greet and do not ask a question."
            ),
            "\n".join(lines),
            max_tokens=400,
            timeout=60,
        )
    except Exception:
        return ""
    return text.strip()[:4096]


def _send_chat_summary(account):
    """Send one summary of the user's web desk chat that WhatsApp has not received yet."""
    from booking_system.leon_meta_orchestrator import LEONMetaOrchestrator
    from booking_system.runtime_state import read_turns, write_turns
    history = read_turns(account["hotel_name"], account["user_id"])
    lines, indexes = _pending_summary(history)
    if not lines:
        return "empty"
    text = _one_summary(lines)
    if not text:
        return "failed"
    from booking_system.front_desk_manager import intelligent_front_desk
    result = intelligent_front_desk.send_whatsapp_text(account["phone"], text)
    if result.get("error"):
        return "closed"
    remember_outbound(result.get("message_id"), account["hotel_id"], {})
    for index in indexes:
        sender, turn = LEONMetaOrchestrator.dialog_line(None, history[index], index)
        history[index] = {"sender": sender, "text": turn, "summarized": True}
    history.append({
        "sender": "leon",
        "text": text,
        "channel": "whatsapp",
        "summarized": True,
        "source_ref": str(result.get("message_id") or ""),
    })
    write_turns(account["hotel_name"], account["user_id"], history)
    return "sent"


def share_desk_chat(hotel_name, user_id):
    """Summarize this user's web desk chat onto their WhatsApp when they have a phone at this hotel."""
    from booking_system import db
    found = db.row(
        """SELECT u.user_id, u.user_name, i.identity_value AS phone, h.hotel_id, h.hotel_name
           FROM user_account u
           JOIN user_identity i ON i.user_id = u.user_id AND i.identity_type = 'phone'
           JOIN hotel h ON upper(h.hotel_name) = %s
           JOIN hotel_user hu ON hu.user_id = u.user_id AND hu.hotel_id = h.hotel_id AND hu.status = 'active'
           WHERE u.user_id = %s AND u.status = 'active'
           ORDER BY i.identity_value LIMIT 1""",
        (str(hotel_name or "").strip().upper(), user_id),
    )
    if not found:
        return "no-phone"
    return _send_chat_summary(found)


def _deliver_list(hotel, phone, screen):
    """Send the list once and keep its WhatsApp message id."""
    from booking_system.front_desk_manager import intelligent_front_desk
    result = intelligent_front_desk.send_whatsapp_list(
        phone,
        (screen or {}).get("header"),
        (screen or {}).get("body"),
        (screen or {}).get("footer"),
        (screen or {}).get("button"),
        [],
        (screen or {}).get("sections"),
    )
    if not result.get("error"):
        remember_outbound(result.get("message_id"), _hotel_id(hotel), screen)
    return result


def _send_to_open(hotel, screen):
    """Send the list to each hotel user whose WhatsApp window is open. 'sent' when at least one took it."""
    sent = [phone for phone in _open_phones(hotel) if not _deliver_list(hotel, phone, screen).get("error")]
    return "sent" if sent else "closed"


def _copy_sales(hotel, screen):
    """Send the sales list once, only to hotel users whose WhatsApp window is open."""
    if not screen or screen.get("whatsapp_sent"):
        return "sent"
    if _send_to_open(hotel, screen) != "sent":
        return "closed"
    screen["whatsapp_sent"] = True
    store_screen(hotel, screen)
    return "sent"


def ensure_sales_card(hotel):
    """Show today's sales card unless the hotel user already read or dismissed it."""
    hotel_name = str(hotel or "").upper()
    current = read_screen(hotel_name)
    day = _today(hotel_name)
    if current and current.get("card") == "sales" and current.get("day") == day:
        if _card_seen(hotel_name, "sales", day):
            store_screen(hotel_name, None)
            return None
        if not current.get("whatsapp_sent"):
            _copy_sales(hotel_name, current)
            return read_screen(hotel_name)
        return current
    if _card_seen(hotel_name, "sales", day):
        return current if current and current.get("card") != "sales" else None
    if current and current.get("card") != "sales" and current.get("sections"):
        return current
    facts = sales_facts(hotel_name)
    screen = _sales_screen(hotel_name, facts["day"], _sales_body(facts))
    store_screen(hotel_name, screen)
    _copy_sales(hotel_name, screen)
    return read_screen(hotel_name)


def card_click(hotel, card, row_id):
    """Pin, mark read, or dismiss. Leon and a WhatsApp list reply both call this."""
    hotel_name = str(hotel or "").upper()
    ident = str(row_id or "").strip()
    if str(card or "") != "sales" or ident not in ("pin", "read", "dismiss"):
        return "missing"
    screen = read_screen(hotel_name) or {}
    if screen.get("card") != "sales":
        return "missing"
    if ident == "pin":
        pin_message(hotel_name, screen.get("body"), "insights", "user")
        return "pinned"
    _mark_seen(hotel_name, "sales", screen.get("day"), ident)
    store_screen(hotel_name, None)
    return ident


def _copy_function(hotel, screen):
    """Send the function list to hotel users whose WhatsApp window is open."""
    if not screen or screen.get("card") not in ("function", "menu"):
        return "closed"
    return _send_to_open(hotel, screen)


def read_topic(hotel, manager):
    from booking_system.runtime_state import read_topic as stored_topic
    if not manager:
        return ""
    return stored_topic(hotel, manager)


def remember_topic(hotel, manager, topic):
    from booking_system.runtime_state import write_topic
    if not manager:
        return
    write_topic(hotel, manager, topic)


_READ_TOPICS = ("availability", "occupancy", "guest", "reservation")
# topic -> general recipe key (the catalog id of the published recipe)
_STICKY_READ = {
    "availability": "daily_rooms_left_by_room_type",
    "occupancy": "daily_in_house_counts",
    "guest": "daily_in_house_counts",
    "reservation": "daily_arrival_counts",
}


def kept_topic(stored, named):
    """Stay on a read until the model names a different catalog job."""
    from booking_system.desk_actions import action_names
    aliases = {"check_availability": "availability"}
    name = aliases.get(str(named or "").strip(), str(named or "").strip())
    catalog = set(action_names())
    catalog.update(_READ_TOPICS)
    catalog.discard("check_availability")
    if name in _READ_TOPICS:
        return name
    if not name and stored in _READ_TOPICS:
        return stored
    if name in catalog:
        return name
    return ""


def _only_action(brief, name):
    brief["informational_actions"] = [
        row for row in brief.get("informational_actions") or [] if str((row or {}).get("action") or "") == name
    ]
    brief["executional_actions"] = [
        row for row in brief.get("executional_actions") or [] if str((row or {}).get("action") or "") == name
    ]


def _read_ids(topic, brief):
    """The ids the desk named. A sticky read stays on its primary section."""
    from booking_system.semantic.catalog import index_ids, ready_ids
    known = set(index_ids())
    ids = []
    for item in brief.get("catalog_ids") or []:
        name = str(item or "")
        if name in known and name not in ids:
            ids.append(name)
    ready = [name for name in ready_ids(ids) if name not in ("F01", "F02")]
    sticky = _STICKY_READ.get(topic, "") if _STICKY_READ.get(topic, "") in known else ""
    if ready and sticky and sticky not in ready:
        return ids, ""
    if sticky and sticky not in ids:
        ids.append(sticky)
    return ids, topic


def _apply_read(brief, found):
    brief["sql_reply"] = found.get("reply") or ""
    brief["query_rows"] = found.get("rows") or []
    brief["query_error"] = found.get("error") or ""
    brief["query_stall"] = found.get("stall_reason") or ""


def compile_desk_sentence(question, rows, error="", stall="", definitions=None, dates=None):
    """One short desk sentence from the question and the returned rows."""
    import json
    from booking_system.llm_utils import haiku_text

    schema = {
        "type": "object",
        "properties": {"sentence": {"type": "string"}},
        "required": ["sentence"],
        "additionalProperties": False,
    }
    body = json.dumps(
        {
            "question": str(question or ""),
            "rows": rows or [],
            "error": str(error or ""),
            "stall": str(stall or ""),
            "definitions": list(definitions or []),
            "dates": dates or {"resolutions": []},
        },
        default=str,
    )
    try:
        parsed = json.loads(haiku_text(
            (
                "Write one short sentence the front-desk manager can say aloud. "
                "Use only the question, the rows, and dates. Do not invent guests, dates, or numbers. "
                "Name each resolved date as MM/DD/YYYY from dates. Do not choose a different day. "
                "When a resolution has a correction, include it, and do not repeat stated_date as the day the figure is for. "
                "Skip a resolution whose unresolved is true. "
                "A count states the number. A who question names the people, or says nobody. "
                "When the rows are empty and error and stall are empty, say that nothing matched. "
                "When error or stall is set, say the lookup did not finish and include that reason. "
                "Do not mention column names, SQL, or that no rows were returned. "
                "When several rows are readings of the same count, state the one the definitions name."
            ),
            body,
            schema=schema,
            max_tokens=800,
            timeout=90,
        ))
        sentence = str(parsed.get("sentence") or "").strip()
        if not sentence:
            return "The sentence could not be written."
        return sentence
    except Exception as exc:
        return "The sentence could not be written. " + str(exc)


def _read_brief(page_ids):
    return {
        "guest_reply": "",
        "hotel_specific": False,
        "hotel_name": "",
        "subject_phone": "",
        "informational_actions": [],
        "executional_actions": [],
        "desk_tasks": [],
        "guide_actions": False,
        "pin_category": "",
        "catalog_ids": list(page_ids or []),
        "read_parameters": [],
        "screen": {"header": "", "body": "", "footer": "", "button": "", "rows": []},
    }


# page ids -> (monotonic expiry, card title). A catalog title is a label; a republished title shows within HEADER_SECONDS.
HEADER_SECONDS = 300
_HEADERS = {}
_PRESENT = ThreadPoolExecutor(max_workers=8, thread_name_prefix="present_read")


def _read_header(page_ids):
    key = tuple(page_ids or ())
    kept = _HEADERS.get(key)
    if kept and kept[0] > time.monotonic():
        return kept[1]
    from booking_system.semantic.catalog import catalog_row
    title = "Read"
    for map_id in key:
        entry = catalog_row(map_id) or {}
        if entry.get("concepts"):
            title = entry.get("title") or map_id
            break
    _HEADERS[key] = (time.monotonic() + HEADER_SECONDS, title)
    return title


def _copy_function_logged(hotel, screen):
    try:
        return _copy_function(hotel, screen)
    except Exception:
        logging.exception("WhatsApp copy of the read screen failed")
        return "failed"


def present_read(hotel, found, page_ids, question="", definitions=None, dates=None):
    """Turn a query result into the desk reply and the read screen. The card title and pins are read while the sentence is
    written; the WhatsApp copy to open windows is sent after the reply, off the turn."""
    from booking_system.desk_actions import read_screen as read_card
    brief = _read_brief(page_ids)
    _apply_read(brief, found)
    brief, acted = apply_desk(hotel, brief)
    header = _PRESENT.submit(_read_header, page_ids)
    pins = _PRESENT.submit(shown_pins, hotel)
    brief["guest_reply"] = compile_desk_sentence(
        question,
        brief.get("query_rows") or [],
        brief.get("query_error") or "",
        brief.get("query_stall") or "",
        definitions,
        dates,
    )
    reply = str(brief.get("guest_reply") or "").strip() or "No action is needed."
    screen = read_card(_hotel_id(hotel), brief, header.result())
    if screen:
        _PRESENT.submit(_copy_function_logged, hotel, screen)
    shown, count = pins.result()
    return reply, acted, screen, {"shown": shown, "count": count}


def desk_turn(hotel, manager, message, history):
    """Front-desk chat. The controller chooses a read or a desk action."""
    from booking_system.semantic.desk import handle_user_message
    return handle_user_message(hotel, manager, message, history)


def prepare_desk_action(hotel, manager, message, history, dates=None):
    """The desk-action model. A read is left for the live SQL node; this step does not choose a day or run SQL."""
    from booking_system.guest_summary import desk_reply
    from booking_system.semantic.catalog import ready_ids

    hotel_id = _hotel_id(hotel)
    stored = read_topic(hotel, manager)
    brief = desk_reply(
        _client(), hotel, manager, message, history,
        _queue_text(hotel), _pin_text(hotel), stored, dates=dates,
    )
    from booking_system.semantic.dates import pass_stay_dates
    if not isinstance(brief.get("dates"), dict):
        brief["dates"] = {"resolutions": []}
    pass_stay_dates(brief)
    topic = kept_topic(stored, brief.get("topic"))
    ids, topic = _read_ids(topic, brief)
    brief["catalog_ids"] = ids
    remember_topic(hotel, manager, topic)
    readable = ready_ids(ids)
    read_now = False
    if topic == "availability":
        _only_action(brief, "check_availability")
        brief["executional_actions"] = []
        from booking_system.desk_actions import _availability_item
        ask = str((_availability_item(brief) or {}).get("ask") or "rooms_left")
        read_now = ask not in ("set_price", "block", "open") and bool(readable)
    elif topic in ("occupancy", "guest", "reservation"):
        brief["informational_actions"] = []
        brief["executional_actions"] = []
        read_now = bool(readable)
    elif topic:
        _only_action(brief, topic)
    elif readable:
        read_now = True
    elif ids:
        brief["guest_reply"] = "That catalog is not an operational SQL read."
    return {
        "read_now": read_now,
        "brief": brief,
        "topic": topic,
        "ids": ids,
        "readable": readable,
        "hotel_id": hotel_id,
    }


def finish_desk_action(hotel, manager, message, brief, dates, topic, ids, readable, read_now):
    """Screens and, when a read ran, the reply sentence. The sentence model runs in the reply node."""
    from booking_system.desk_actions import availability_screen, function_screen, menu_screen, occupancy_screen
    from booking_system.desk_actions import read_screen as read_card

    brief, acted = apply_desk(hotel, brief, manager)
    if read_now:
        brief["guest_reply"] = compile_desk_sentence(
            message,
            brief.get("query_rows") or [],
            brief.get("query_error") or "",
            brief.get("query_stall") or "",
            dates=dates,
        )
    reply = str(brief.get("guest_reply") or "").strip() or "No action is needed."
    hotel_id = _hotel_id(hotel)
    if topic == "availability":
        screen = availability_screen(hotel_id, brief, manager)
    elif topic == "occupancy":
        screen = occupancy_screen(hotel_id, brief)
    elif brief.get("query_rows") is not None and readable:
        screen = read_card(hotel_id, brief, _read_header(ids))
    else:
        screen = function_screen(hotel_id, brief, brief.get("result_lines"))
        if not screen and brief.get("guide_actions"):
            screen = menu_screen(hotel_id)
    if screen:
        _copy_function(hotel, screen)
    else:
        screen = _kept_screen(hotel, brief)
        if screen:
            store_screen(hotel, screen)
    category = str(brief.get("pin_category") or "")
    if category in _PIN_CATEGORIES:
        pin_message(hotel, reply, category, "agent")
    shown, count = shown_pins(hotel)
    return reply, acted, screen, {"shown": shown, "count": count}


def receive_manager_whatsapp(account, source_ref, text, row_id=""):
    """A WhatsApp message from a hotel user (manager_account). Summarize their web chat, send that one text, then reply. The turn
    is written to the same chat as the web desk."""
    from booking_system.runtime_state import read_turns, write_turns
    ref = str(source_ref or "").strip()
    body = str(text or "").strip()
    if not ref or not body:
        return "missing"
    hotel = account["hotel_name"]
    coll = _sync_collection()
    existing = coll.find_one({"source_ref": ref})
    if existing and (existing.get("applied") or existing.get("direction") == "out"):
        return "duplicate"
    if not existing:
        try:
            coll.insert_one({
                "source_ref": ref,
                "hotel_id": account["hotel_id"],
                "user_id": account["user_id"],
                "direction": "in",
                "message_type": "text",
                "row_id": str(row_id or ""),
                "applied": False,
                "summary_sent": False,
                "reply_sent": False,
            })
        except Exception as error:
            if error.__class__.__name__ != "DuplicateKeyError":
                raise
            existing = coll.find_one({"source_ref": ref})
            if existing and existing.get("applied"):
                return "duplicate"
    existing = coll.find_one({"source_ref": ref}) or {}
    if not existing.get("summary_sent"):
        if _send_chat_summary(account) in ("sent", "empty"):
            coll.update_one({"source_ref": ref}, {"$set": {"summary_sent": True}})
    if str(row_id or "") in ("pin", "read", "dismiss") and not existing.get("clicked"):
        card_click(hotel, "sales", row_id)
        coll.update_one({"source_ref": ref}, {"$set": {"clicked": True}})
    existing = coll.find_one({"source_ref": ref}) or {}
    from booking_system.interactive.agent import complete
    navigated = complete(account["hotel_id"], row_id) if row_id else None
    reply = str(existing.get("reply") or "")
    if not reply:
        if navigated:
            reply = navigated["text"]
        else:
            reply, _acted, _screen, _pins = desk_turn(hotel, account["user_id"], body, read_turns(hotel, account["user_id"]))
        coll.update_one({"source_ref": ref}, {"$set": {"reply": reply}})
    if not existing.get("reply_sent"):
        from booking_system.front_desk_manager import intelligent_front_desk
        if navigated and navigated.get("interactive"):
            result = intelligent_front_desk.send_whatsapp_interactive(account["phone"], navigated["interactive"])
        else:
            result = intelligent_front_desk.send_whatsapp_text(account["phone"], reply)
        if not result.get("error"):
            remember_outbound(result.get("message_id"), account["hotel_id"], {})
            coll.update_one({"source_ref": ref}, {"$set": {"reply_sent": True}})
            existing["reply_sent"] = True
    history = read_turns(hotel, account["user_id"])
    if not any(isinstance(entry, dict) and str(entry.get("source_ref") or "") == ref for entry in history):
        history.append({"sender": "user", "text": body, "source_ref": ref, "channel": "whatsapp", "summarized": True})
        answer = {"sender": "leon", "text": reply}
        if existing.get("reply_sent"):
            answer.update(channel="whatsapp", summarized=True)
        history.append(answer)
        write_turns(hotel, account["user_id"], history)
    coll.update_one({"source_ref": ref}, {"$set": {"applied": True}})
    return "created"
