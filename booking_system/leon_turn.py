"""One turn for a guest or the front desk. The hotel is chosen here, then keep and settle run."""
import os


def _client():
    from openai import OpenAI
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


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
    """Resolve the hotel, then read or write only when that hotel is known."""
    from booking_system.desk_actions import hotel_names, keep, read_information, resolve_hotel, settle, stage_for_phone
    from booking_system.guest_manager import guest_manager
    from booking_system.guest_summary import rewrite_reply
    from booking_system.timezone_utils import hotel_local_today
    stage = stage_for_phone(phone)
    resolved = resolve_hotel(stage, brief.get("hotel_specific"), brief.get("hotel_name"), hotel_names())
    if resolved.get("ask"):
        brief["guest_reply"] = resolved["ask"]
        return {"brief": brief, "lists": None, "acted": False}
    hotel = str(resolved.get("hotel") or "").upper()
    today = hotel_local_today(hotel).isoformat()
    proposed = list(brief.get("executional_actions") or [])
    brief["executional_actions"] = keep(proposed, hotel, phone, today)
    guest = guest_manager.records["guests"].find_one({"guest_phone": str(phone or ""), "status": "active"})
    profile_hotel = str((guest or {}).get("hotel_name") or "").upper()
    if guest and profile_hotel == hotel:
        settled = settle(brief, hotel, phone, guest)
        lists = {
            "approval_actions": settled["approval_actions"],
            "desk_tasks": settled["desk_tasks"],
            "carried_actions": settled["carried_actions"],
            "executable_actions": [],
        }
    else:
        notes = read_information(brief.get("informational_actions"), hotel)
        settled = {"lines": ["Read " + note for note in notes], "completed": []}
        lists = None
    if proposed and not brief["executional_actions"]:
        settled["lines"] = list(settled["lines"]) + ["Not done"]
    if settled["lines"]:
        try:
            brief["guest_reply"] = rewrite_reply(client, brief.get("guest_reply"), settled["lines"])
        except Exception:
            pass
    return {"brief": brief, "lists": lists, "acted": bool(settled.get("completed"))}


def _queue_text(hotel):
    from booking_system.guest_manager import guest_manager
    approvals, tasks = guest_manager.desk_queue(hotel)
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
        pins.create_index("hotel_name", name="hotel_name_pins")
        screen.create_index("hotel_name", unique=True, name="hotel_name_screen")
        seen.create_index("hotel_name", name="hotel_name_seen")
        _chat_indexes = True
    return pins, screen, seen


def _pin_collection():
    pins, _screen, _seen = _chat_collections()
    return pins


def _screen_collection():
    _pins, screen, _seen = _chat_collections()
    return screen


def pin_message(hotel, text, category, who):
    """Read the pin. Update it when this hotel and text already exist, otherwise create it."""
    hotel_name = str(hotel or "").upper()
    body = str(text or "").strip()
    kind = str(category or "").strip()
    if not hotel_name or not body or kind not in _PIN_CATEGORIES:
        return "missing"
    from datetime import datetime, timezone
    coll = _pin_collection()
    key = {"hotel_name": hotel_name, "text": body}
    fields = {"category": kind, "who": str(who or ""), "pinned_at": datetime.now(timezone.utc).isoformat()}
    if coll.find_one(key):
        coll.update_one(key, {"$set": fields})
        return "updated"
    coll.insert_one({**key, **fields})
    return "created"


def unpin_message(hotel, text):
    """Read the pin. Delete it when this hotel and text exist."""
    hotel_name = str(hotel or "").upper()
    body = str(text or "").strip()
    coll = _pin_collection()
    key = {"hotel_name": hotel_name, "text": body}
    if not coll.find_one(key):
        return "missing"
    coll.delete_one(key)
    return "deleted"


def list_pins(hotel):
    """Every pin for this hotel, in screen order."""
    hotel_name = str(hotel or "").upper()
    rows = list(_pin_collection().find({"hotel_name": hotel_name}, {"_id": 0}))
    rows.sort(key=lambda row: (_PIN_RANK.get(str(row.get("category") or ""), 4), 0 if str(row.get("who") or "") == "user" else 1, str(row.get("pinned_at") or "")))
    return rows


def shown_pins(hotel):
    """Three pins for the column. Urgent, then to-do, follow-up, insights. A user pin comes before an agent pin. Older first."""
    rows = list_pins(hotel)
    return rows[:3], len(rows)


def _pin_text(hotel):
    shown, _count = shown_pins(hotel)
    lines = []
    for row in shown:
        lines.append(str(row.get("category") or "") + " " + str(row.get("text") or ""))
    return "\n".join(lines)


def _kept_screen(hotel, brief):
    """Keep list rows whose id is an open approval label or task. The agent wrote the words."""
    from booking_system.guest_manager import guest_manager
    raw = (brief or {}).get("screen") or {}
    approvals, tasks = guest_manager.desk_queue(hotel)
    known = {}
    for row in approvals:
        label = str(row.get("label") or "")
        if label:
            known[label] = {"kind": "approval", "record": row}
    for row in tasks:
        task = str(row.get("task") or "")
        if task:
            known[task] = {"kind": "task", "record": row}
    rows = []
    for item in raw.get("rows") or []:
        ident = str((item or {}).get("id") or "").strip()
        match = known.get(ident)
        if not match or len(rows) >= 10:
            continue
        record = {}
        for key, value in (match["record"] or {}).items():
            if key != "_id":
                record[key] = value
        section = _clip((item or {}).get("section"), 24)
        rows.append({
            "section": section,
            "id": ident[:200],
            "title": _clip((item or {}).get("title"), 24),
            "description": _clip((item or {}).get("description"), 72),
            "kind": match["kind"],
            "record": record,
        })
    if not rows:
        return None
    sections = []
    for row in rows:
        if not sections or sections[-1]["title"] != row["section"]:
            sections.append({"title": row["section"], "rows": []})
        sections[-1]["rows"].append({
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "kind": row["kind"],
            "record": row["record"],
        })
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
    hotel_name = str(hotel or "").upper()
    coll = _screen_collection()
    key = {"hotel_name": hotel_name}
    existing = coll.find_one(key)
    if not screen:
        if existing:
            coll.delete_one(key)
        return "deleted"
    coll.update_one(key, {"$set": {"screen": screen}}, upsert=True)
    return "updated" if existing else "created"


def read_screen(hotel):
    hotel_name = str(hotel or "").upper()
    doc = _screen_collection().find_one({"hotel_name": hotel_name}, {"_id": 0})
    if not doc:
        return None
    return doc.get("screen")


def drop_screen_row(hotel, row_id):
    """Read the stored list and remove the chosen row."""
    hotel_name = str(hotel or "").upper()
    ident = str(row_id or "")
    coll = _screen_collection()
    doc = coll.find_one({"hotel_name": hotel_name})
    if not doc:
        return "missing"
    screen = dict(doc.get("screen") or {})
    sections = []
    for section in screen.get("sections") or []:
        kept = [row for row in (section.get("rows") or []) if str(row.get("id") or "") != ident]
        if kept:
            sections.append({"title": section.get("title") or "", "rows": kept})
    if not sections:
        coll.delete_one({"hotel_name": hotel_name})
        return "deleted"
    screen["sections"] = sections
    coll.update_one({"hotel_name": hotel_name}, {"$set": {"screen": screen}})
    return "updated"


def _subject_phone(hotel, brief):
    from booking_system.desk_actions import booking_phone
    phone = str(brief.get("subject_phone") or "").strip()
    if phone:
        return phone
    found = []
    for row in brief.get("executional_actions") or []:
        one = str((row or {}).get("guest_phone") or "").strip()
        if not one:
            one = booking_phone(hotel, (row or {}).get("booking_id"))
        if one and one not in found:
            found.append(one)
    if len(found) == 1:
        return found[0]
    return ""


def apply_desk(hotel, brief):
    """Run the desk's actions at the JWT hotel. A different named hotel is not written."""
    from booking_system.desk_actions import hotel_names, keep, read_information, settle
    from booking_system.guest_manager import guest_manager
    from booking_system.guest_summary import rewrite_reply
    from booking_system.timezone_utils import hotel_local_today
    hotel_name = str(hotel or "").upper()
    named = str(brief.get("hotel_name") or "").upper()
    known = hotel_names()
    if brief.get("hotel_specific") and named and named != hotel_name:
        if named not in known:
            brief["guest_reply"] = "Which hotel? " + ", ".join(known)
        else:
            brief["guest_reply"] = "This desk is " + hotel_name + ". This turn is about " + named + "."
        brief["listed_actions"] = []
        return brief, False
    brief["listed_actions"] = list(brief.get("executional_actions") or [])
    phone = _subject_phone(hotel_name, brief)
    today = hotel_local_today(hotel_name).isoformat()
    guest = guest_manager.records["guests"].find_one({"guest_phone": phone, "status": "active"}) if phone else None
    same_hotel = guest and str(guest.get("hotel_name") or "").upper() == hotel_name
    if same_hotel:
        proposed = list(brief.get("executional_actions") or [])
        brief["executional_actions"] = keep(proposed, hotel_name, phone, today)
        settled = settle(brief, hotel_name, phone, guest)
        if proposed and not brief["executional_actions"]:
            settled["lines"] = list(settled["lines"]) + ["Not done"]
        guest_manager.records["guests"].update_one(
            {"guest_phone": guest["guest_phone"]},
            {"$set": {
                "approval_actions": settled["approval_actions"],
                "desk_tasks": settled["desk_tasks"],
                "carried_actions": settled["carried_actions"],
                "executable_actions": [],
            }},
        )
        lines = settled["lines"]
        acted = bool(settled.get("completed"))
    else:
        notes = read_information(brief.get("informational_actions"), hotel_name)
        lines = ["Read " + note for note in notes]
        acted = False
    brief["result_lines"] = lines
    if lines:
        try:
            brief["guest_reply"] = rewrite_reply(_client(), brief.get("guest_reply"), lines)
        except Exception:
            pass
    return brief, acted


def _managers():
    from booking_system.guest_manager import guest_manager
    return guest_manager.store["leon_manager_system"]["manager_collection"]


def _seen_collection():
    _pins, _screen, seen = _chat_collections()
    return seen


def manager_hotel(phone):
    """The hotel whose manager_phone is this number. Empty when the phone is a guest."""
    wanted = str(phone or "").strip()
    if not wanted:
        return ""
    doc = _managers().find_one({"manager_phone": wanted}, {"hotel_name": 1, "_id": 0})
    if not doc:
        return ""
    return str(doc.get("hotel_name") or "").upper()


def note_manager_inbound(hotel, sent_at):
    """Record the hotel user's latest WhatsApp so the 24-hour window can open."""
    hotel_name = str(hotel or "").upper()
    moment = str(sent_at or "").strip()
    if not hotel_name or not moment:
        return "missing"
    _managers().update_one({"hotel_name": hotel_name}, {"$set": {"whatsapp_in_at": moment}})
    return "updated"


def _window_open(sent_at):
    from datetime import datetime, timedelta, timezone
    text = str(sent_at or "").strip()
    if not text:
        return False
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - moment <= timedelta(hours=24)


def _card_seen(hotel, card, day):
    return _seen_collection().find_one({"hotel_name": hotel, "card": card, "day": day}) is not None


def _mark_seen(hotel, card, day, how):
    coll = _seen_collection()
    key = {"hotel_name": hotel, "card": card, "day": day}
    existing = coll.find_one(key)
    if existing:
        coll.update_one(key, {"$set": {"how": how}})
        return "updated"
    coll.insert_one({**key, "how": how})
    return "created"


def sales_facts(hotel):
    """Today's new bookings, rooms in house against rooms on the books, and the rate of occupied rooms."""
    from booking_system.desk_actions import _with_pg
    from booking_system.guest_manager import guest_manager
    from booking_system.timezone_utils import hotel_local_today
    hotel_name = str(hotel or "").upper()
    today = hotel_local_today(hotel_name)
    day = today.isoformat() if hasattr(today, "isoformat") else str(today)[:10]

    def stays(cur):
        cur.execute(
            """
            SELECT rb.booking_id, MIN(bs.stay_date), MAX(bs.stay_date)
            FROM room_bookings rb
            JOIN booking_stays bs
              ON bs.booking_id = rb.booking_id AND bs.hotel_name = rb.hotel_name
            WHERE UPPER(rb.hotel_name) = %s
              AND LOWER(COALESCE(rb.booking_status, '')) = 'confirmed'
            GROUP BY rb.booking_id
            """,
            (hotel_name,),
        )
        return cur.fetchall()

    def priced(cur):
        cur.execute(
            """
            SELECT COALESCE(SUM(bs.rooms_booked * dp.base_price), 0),
                   COALESCE(SUM(bs.rooms_booked), 0)
            FROM booking_stays bs
            JOIN room_bookings rb
              ON rb.booking_id = bs.booking_id AND rb.hotel_name = bs.hotel_name
            JOIN daily_pricing dp
              ON UPPER(dp.hotel_name) = UPPER(rb.hotel_name)
             AND dp.room_type = rb.room_type
             AND dp.stay_date = bs.stay_date
            WHERE UPPER(rb.hotel_name) = %s
              AND bs.stay_date = %s
              AND LOWER(COALESCE(rb.booking_status, '')) = 'confirmed'
              AND dp.base_price > 0
            """,
            (hotel_name, day),
        )
        return cur.fetchone()

    arrivals = 0
    in_house = 0
    for _booking_id, first, last in _with_pg(stays) or []:
        first_day = first.isoformat() if hasattr(first, "isoformat") else str(first)[:10]
        last_day = last.isoformat() if hasattr(last, "isoformat") else str(last)[:10]
        if first_day == day:
            arrivals += 1
        if first_day <= day <= last_day:
            in_house += 1
    rooms = guest_manager.records["room_encyclopedia"].count_documents({
        "hotel_name": hotel_name,
        "room_number": {"$gt": ""},
    })
    total, sold = _with_pg(priced) or (0, 0)
    rate = int(total / sold) if sold else 0
    return {"day": day, "bookings": arrivals, "in_house": in_house, "rooms": rooms, "rate": rate}


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


def remember_outbound(message_id, hotel, screen):
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
            "hotel_name": str(hotel or "").upper(),
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


def _desk_dialog(hotel):
    """The hotel user's last 20 Leon chat turns."""
    from booking_system.runtime_state import read_turns
    hotel_name = str(hotel or "").upper()
    doc = _managers().find_one({"hotel_name": hotel_name}, {"manager_name": 1, "manager_phone": 1, "_id": 0})
    manager = str((doc or {}).get("manager_name") or "").upper()
    phone = str((doc or {}).get("manager_phone") or "")
    history = read_turns(hotel_name, manager) if manager else []
    return history, manager, phone


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
        speaker = "Desk" if sender == "user" else "Leon"
        lines.append(speaker + ": " + text)
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


def _send_chat_summary(hotel, phone):
    """Send one summary of the Leon chat that WhatsApp has not received yet."""
    from booking_system.leon_meta_orchestrator import LEONMetaOrchestrator
    from booking_system.runtime_state import write_turns
    history, manager, stored_phone = _desk_dialog(hotel)
    if not manager:
        return "missing"
    lines, indexes = _pending_summary(history)
    if not lines:
        return "empty"
    text = _one_summary(lines)
    if not text:
        return "failed"
    from booking_system.front_desk_manager import intelligent_front_desk
    result = intelligent_front_desk.send_whatsapp_text(phone or stored_phone, text)
    if result.get("error"):
        return "closed"
    remember_outbound(result.get("message_id"), hotel, {})
    for index in indexes:
        sender, turn = LEONMetaOrchestrator.dialog_line(None, history[index], index)
        history[index] = {"sender": sender, "text": turn, "summarized": True}
    write_turns(hotel, manager, history)
    return "sent"


def _append_user_turn(hotel, text, source_ref):
    from booking_system.runtime_state import write_turns
    history, manager, _phone = _desk_dialog(hotel)
    if not manager:
        return "missing"
    for entry in history:
        if isinstance(entry, dict) and str(entry.get("source_ref") or "") == source_ref:
            return "present"
    history.append({
        "sender": "user",
        "text": text,
        "source_ref": source_ref,
        "channel": "whatsapp",
        "summarized": True,
    })
    write_turns(hotel, manager, history)
    return "added"


def take_list_reply(hotel, source_ref, selection):
    """Copy one WhatsApp list selection into the Leon thread. The same message id is kept once."""
    ref = str(source_ref or "").strip()
    row_id = str((selection or {}).get("id") or "").strip()
    if not ref or not row_id:
        return "missing"
    coll = _sync_collection()
    existing = coll.find_one({"source_ref": ref})
    if existing and existing.get("direction") == "out":
        return "duplicate"
    if existing and existing.get("applied"):
        return "duplicate"
    if not existing:
        try:
            coll.insert_one({
                "source_ref": ref,
                "hotel_name": str(hotel or "").upper(),
                "direction": "in",
                "message_type": "list_reply",
                "row_id": row_id,
                "title": str((selection or {}).get("title") or ""),
                "description": str((selection or {}).get("description") or ""),
                "context_id": str((selection or {}).get("context_id") or ""),
                "applied": False,
            })
        except Exception as error:
            if error.__class__.__name__ != "DuplicateKeyError":
                raise
            existing = coll.find_one({"source_ref": ref})
            if existing and existing.get("applied"):
                return "duplicate"
    title = str((selection or {}).get("title") or "").strip()
    detail = str((selection or {}).get("description") or "").strip()
    quote = title + ("\n" + detail if detail else "")
    if quote:
        _append_user_turn(hotel, quote, ref)
    coll.update_one({"source_ref": ref}, {"$set": {"applied": True}})
    if row_id in ("pin", "read", "dismiss"):
        card_click(hotel, "sales", row_id)
    return "created"


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
        remember_outbound(result.get("message_id"), hotel, screen)
    return result


def _copy_sales(hotel, screen):
    """Send the same list to the hotel user once, only while their WhatsApp window is open."""
    if not screen or screen.get("whatsapp_sent"):
        return "sent"
    hotel_name = str(hotel or "").upper()
    doc = _managers().find_one({"hotel_name": hotel_name}, {"manager_phone": 1, "whatsapp_in_at": 1, "_id": 0})
    phone = str((doc or {}).get("manager_phone") or "")
    if not phone or not _window_open((doc or {}).get("whatsapp_in_at")):
        return "closed"
    result = _deliver_list(hotel_name, phone, screen)
    if result.get("error"):
        return "closed"
    screen["whatsapp_sent"] = True
    store_screen(hotel_name, screen)
    return "sent"


def ensure_sales_card(hotel):
    """Show today's sales card unless the hotel user already read or dismissed it."""
    from booking_system.timezone_utils import hotel_local_today
    hotel_name = str(hotel or "").upper()
    current = read_screen(hotel_name)
    today = hotel_local_today(hotel_name)
    day = today.isoformat() if hasattr(today, "isoformat") else str(today)[:10]
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
    """Send the function list to the hotel user while their WhatsApp window is open."""
    if not screen or screen.get("card") not in ("function", "menu"):
        return "closed"
    hotel_name = str(hotel or "").upper()
    doc = _managers().find_one({"hotel_name": hotel_name}, {"manager_phone": 1, "whatsapp_in_at": 1, "_id": 0})
    phone = str((doc or {}).get("manager_phone") or "")
    if not phone or not _window_open((doc or {}).get("whatsapp_in_at")):
        return "closed"
    result = _deliver_list(hotel_name, phone, screen)
    if result.get("error"):
        return "closed"
    return "sent"


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
_STICKY_READ = {"availability": "B03a", "occupancy": "B03b", "guest": "B02a", "reservation": "B01b"}


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
    sticky = _STICKY_READ.get(topic, "")
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


def compile_desk_sentence(question, rows, error="", stall="", definitions=None):
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
        },
        default=str,
    )
    try:
        parsed = json.loads(haiku_text(
            (
                "Write one short sentence a front-desk manager can say aloud. "
                "Use only the question and the rows. "
                "A count question states the number. "
                "A who question names the people, or says nobody. "
                "When the rows are empty and error and stall are empty, say that nothing matched, in plain language. "
                "When error or stall is set, say the lookup did not finish and include that reason. "
                "Do not mention column names, SQL, or that no rows were returned. "
                "Do not invent guests, dates, or numbers that are not in the rows. "
                "When the rows hold several readings of the same count, state the one the manager's definitions name."
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


def _phrase_read(hotel, message, ids):
    """A matched keyphrase goes straight to the SQL agent and the read screen."""
    from booking_system.desk_actions import read_screen
    from booking_system.semantic.catalog import catalog_row, ready_ids
    from booking_system.semantic.sql_agent import answer_question

    brief = {
        "guest_reply": "",
        "hotel_specific": False,
        "hotel_name": "",
        "subject_phone": "",
        "informational_actions": [],
        "executional_actions": [],
        "desk_tasks": [],
        "guide_actions": False,
        "pin_category": "",
        "catalog_ids": list(ids),
        "read_parameters": [],
        "screen": {"header": "", "body": "", "footer": "", "button": "", "rows": []},
    }
    readable = ready_ids(ids)
    _apply_read(brief, answer_question(hotel, message, readable, [], None))
    brief, acted = apply_desk(hotel, brief)
    brief["guest_reply"] = compile_desk_sentence(
        message,
        brief.get("query_rows") or [],
        brief.get("query_error") or "",
        brief.get("query_stall") or "",
    )
    reply = str(brief.get("guest_reply") or "").strip() or "No action is needed."
    header = "Read"
    for map_id in ids:
        entry = catalog_row(map_id) or {}
        if entry.get("concepts"):
            header = entry.get("title") or map_id
            break
    screen = read_screen(hotel, brief, header) if readable else None
    if screen:
        _copy_function(hotel, screen)
    shown, count = shown_pins(hotel)
    return reply, acted, screen, {"shown": shown, "count": count}


def present_read(hotel, found, page_ids, question="", definitions=None):
    """Turn a query result into the desk reply and the read screen."""
    from booking_system.desk_actions import read_screen
    from booking_system.semantic.catalog import catalog_row

    brief = {
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
    _apply_read(brief, found)
    brief, acted = apply_desk(hotel, brief)
    brief["guest_reply"] = compile_desk_sentence(
        question,
        brief.get("query_rows") or [],
        brief.get("query_error") or "",
        brief.get("query_stall") or "",
        definitions,
    )
    reply = str(brief.get("guest_reply") or "").strip() or "No action is needed."
    header = "Read"
    for map_id in page_ids or []:
        entry = catalog_row(map_id) or {}
        if entry.get("concepts"):
            header = entry.get("title") or map_id
            break
    screen = read_screen(hotel, brief, header)
    if screen:
        _copy_function(hotel, screen)
    shown, count = shown_pins(hotel)
    return reply, acted, screen, {"shown": shown, "count": count}


def desk_turn(hotel, manager, message, history):
    """Front-desk chat. The controller chooses a read or a desk action."""
    from booking_system.semantic.workflow import handle_user_message
    return handle_user_message(hotel, manager, message, history)


def desk_action_turn(hotel, manager, message, history):
    """A greeting or a change to a booking, a payment, a message, or a task."""
    from booking_system.guest_summary import desk_reply
    from booking_system.semantic.catalog import catalog_row, ready_ids
    stored = read_topic(hotel, manager)
    brief = desk_reply(
        _client(), hotel, manager, message, history,
        _queue_text(hotel), _pin_text(hotel), stored,
    )
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
    elif topic == "occupancy":
        brief["informational_actions"] = []
        brief["executional_actions"] = []
        read_now = bool(readable)
    elif topic == "guest":
        brief["informational_actions"] = []
        brief["executional_actions"] = []
        read_now = bool(readable)
    elif topic == "reservation":
        brief["informational_actions"] = []
        brief["executional_actions"] = []
        read_now = bool(readable)
    elif topic:
        _only_action(brief, topic)
    elif readable:
        read_now = True
    elif ids:
        brief["guest_reply"] = "That catalog is not an operational SQL read."
    if read_now:
        from booking_system.semantic.sql_agent import answer_question
        _apply_read(brief, answer_question(hotel, message, readable, brief.get("read_parameters")))
    brief, acted = apply_desk(hotel, brief)
    from booking_system.desk_actions import availability_screen, function_screen, menu_screen, occupancy_screen, read_screen
    if read_now:
        brief["guest_reply"] = compile_desk_sentence(
            message,
            brief.get("query_rows") or [],
            brief.get("query_error") or "",
            brief.get("query_stall") or "",
        )
    reply = str(brief.get("guest_reply") or "").strip() or "No action is needed."
    if topic == "availability":
        screen = availability_screen(hotel, brief)
    elif topic == "occupancy":
        screen = occupancy_screen(hotel, brief)
    elif brief.get("query_rows") is not None and readable:
        header = "Read"
        for map_id in ids:
            entry = catalog_row(map_id) or {}
            if entry.get("concepts"):
                header = entry.get("title") or map_id
                break
        screen = read_screen(hotel, brief, header)
    else:
        screen = function_screen(hotel, brief, brief.get("result_lines"))
        if not screen and brief.get("guide_actions"):
            screen = menu_screen(hotel)
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


def receive_manager_whatsapp(hotel, source_ref, text, row_id=""):
    """A WhatsApp message from the hotel user. Summarize the Leon chat, send that one text, then reply."""
    import json
    ref = str(source_ref or "").strip()
    body = str(text or "").strip()
    if not ref or not body:
        return "missing"
    coll = _sync_collection()
    existing = coll.find_one({"source_ref": ref})
    if existing and (existing.get("applied") or existing.get("direction") == "out"):
        return "duplicate"
    if not existing:
        try:
            coll.insert_one({
                "source_ref": ref,
                "hotel_name": str(hotel or "").upper(),
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
    _history, _manager, phone = _desk_dialog(hotel)
    if not existing.get("summary_sent"):
        summary = _send_chat_summary(hotel, phone)
        if summary in ("sent", "empty"):
            coll.update_one({"source_ref": ref}, {"$set": {"summary_sent": True}})
    if str(row_id or "") in ("pin", "read", "dismiss") and not existing.get("clicked"):
        card_click(hotel, "sales", row_id)
        coll.update_one({"source_ref": ref}, {"$set": {"clicked": True}})
    existing = coll.find_one({"source_ref": ref}) or {}
    reply = str(existing.get("reply") or "")
    if not reply:
        history, manager, phone = _desk_dialog(hotel)
        reply, _acted, _screen, _pins = desk_turn(hotel, manager, body, history)
        coll.update_one({"source_ref": ref}, {"$set": {"reply": reply}})
    if not existing.get("reply_sent"):
        from booking_system.front_desk_manager import intelligent_front_desk
        result = intelligent_front_desk.send_whatsapp_text(phone, reply)
        if not result.get("error"):
            remember_outbound(result.get("message_id"), hotel, {})
            coll.update_one({"source_ref": ref}, {"$set": {"reply_sent": True}})
            existing["reply_sent"] = True
    from booking_system.runtime_state import write_turns
    history, manager, _phone = _desk_dialog(hotel)
    if manager:
        present = False
        for entry in history:
            if isinstance(entry, dict) and str(entry.get("source_ref") or "") == ref:
                present = True
        if not present:
            history.append({
                "sender": "user",
                "text": body,
                "source_ref": ref,
                "channel": "whatsapp",
                "summarized": True,
            })
            leon_turn = {"sender": "leon", "text": reply}
            if existing.get("reply_sent"):
                leon_turn["channel"] = "whatsapp"
                leon_turn["summarized"] = True
            history.append(leon_turn)
            write_turns(hotel, manager, history)
    coll.update_one({"source_ref": ref}, {"$set": {"applied": True}})
    return "created"
