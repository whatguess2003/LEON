"""Action recipes name an existing PMS operation. Leon prepares one; the person approves it; the registered service runs.

The recipe stores the parameter contract and the approval text. It does not store SQL. hotel_id comes from the session.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from booking_system import db

_TTL = timedelta(minutes=30)

# Stable ids. Each one calls the desk adapter that already calls the PMS service.
_REGISTRY = {
    "reservation.check_in": {"perform": "check_in", "permission": ("reservations", "operate"), "parameters": ("booking_id", "room_number")},
    "reservation.check_out": {"perform": "check_out", "permission": ("reservations", "operate"), "parameters": ("booking_id", "room_number")},
    "reservation.cancel": {"perform": "cancel_booking", "permission": ("reservations", "operate"), "parameters": ("booking_id",)},
    "reservation.create": {"perform": "create_booking", "permission": ("reservations", "operate"), "parameters": ("guest_phone", "room_type", "stay_dates")},
    "reservation.modify": {"perform": "modify_booking", "permission": ("reservations", "operate"), "parameters": ("booking_id", "new_stay_dates", "new_room_type", "new_rooms_booked")},
    "reservation.payment": {"perform": "record_payment", "permission": ("reservations", "operate"), "parameters": ("booking_id", "payment_amount", "payment_method")},
    "reservation.refund": {"perform": "record_refund", "permission": ("reservations", "operate"), "parameters": ("booking_id", "payment_seq")},
    "reservation.reassign_room": {"perform": "reassign_room", "permission": ("reservations", "operate"), "parameters": ("booking_id", "stay_date", "old_room_number", "new_room_number", "mark_old_clean")},
    "reservation.no_show": {"perform": "no_show", "permission": ("reservations", "operate"), "parameters": ("booking_id", "reason")},
    "reservation.notes": {"perform": "booking_notes", "permission": ("reservations", "operate"), "parameters": ("booking_id", "notes")},
    "reservation.assign_room": {"perform": "assign_room", "permission": ("reservations", "operate"), "parameters": ("booking_id", "room_number")},
    "reservation.undo_check_in": {"perform": "undo_check_in", "permission": ("reservations", "operate"), "parameters": ("booking_id", "room_number", "mark_clean")},
    "reservation.reset_night": {"perform": "reset_night", "permission": ("reservations", "operate"), "parameters": ("booking_id", "stay_date")},
    "room.set_readiness": {"perform": "set_readiness", "permission": ("housekeeping", "view"), "parameters": ("room_number", "new_status")},
    "room.block": {"perform": "block_room", "permission": ("maintenance", "operate"), "parameters": ("room_number", "start_date", "end_date", "reason", "block_type")},
    "room.release": {"perform": "release_room", "permission": ("maintenance", "operate"), "parameters": ("room_number", "start_date", "end_date")},
    "guest.service_request": {"perform": "service_request", "permission": ("front_desk", "operate"), "parameters": ("guest_phone", "request", "requested_for", "booking_id")},
    "guest.close_request": {"perform": "close_request", "permission": ("front_desk", "operate"), "parameters": ("guest_phone", "request")},
    "guest.preference": {"perform": "save_preference", "permission": ("front_desk", "operate"), "parameters": ("guest_phone", "preference", "value")},
    "guest.complaint": {"perform": "record_complaint", "permission": ("front_desk", "operate"), "parameters": ("guest_phone", "complaint", "booking_id")},
    "guest.deactivate": {"perform": "deactivate_guest", "permission": ("front_desk", "operate"), "parameters": ("guest_phone",)},
    "guest.update_profile": {"perform": "update_guest_profile", "permission": ("front_desk", "operate"), "parameters": ("guest_phone", "guest_name", "email", "country", "special_request")},
    "housekeeping.create_task": {"perform": "create_cleaning_task", "permission": ("housekeeping", "operate"), "parameters": ("room_number", "staff_phone", "task_type", "priority", "notes")},
    "housekeeping.start": {"perform": "start_cleaning", "permission": ("housekeeping", "operate"), "parameters": ("room_number", "staff_phone", "task_type", "priority")},
    "housekeeping.progress": {"perform": "cleaning_progress", "permission": ("housekeeping", "operate"), "parameters": ("cleaning_id", "new_status", "staff_phone", "notes")},
    "housekeeping.clock_in": {"perform": "clock_in", "permission": ("housekeeping", "operate"), "parameters": ("staff_phone",)},
    "housekeeping.clock_out": {"perform": "clock_out", "permission": ("housekeeping", "operate"), "parameters": ("staff_phone",)},
    "maintenance.create": {"perform": "create_maintenance", "permission": ("maintenance", "operate"), "parameters": ("room_number", "area", "issue", "urgency")},
    "maintenance.progress": {"perform": "maintenance_progress", "permission": ("maintenance", "operate"), "parameters": ("request_id", "status", "staff_phone", "notes")},
    "reservation.price_override": {"perform": "price_override", "permission": ("front_desk", "manage"), "parameters": ("booking_id", "new_total_amount")},
    "inventory.set_prices": {"perform": "set_prices", "permission": ("reservations", "operate"), "parameters": ("room_type", "start_date", "end_date", "price")},
    "inventory.update_night": {"perform": "update_night", "permission": ("reservations", "operate"), "parameters": ("room_type", "stay_date", "price", "quantity")},
    "finance.post_charge": {"perform": "post_charge", "permission": ("finance", "operate"), "parameters": ("folio_type", "folio_ref", "transaction_code", "amount")},
    "finance.folio_payment": {"perform": "post_folio_payment", "permission": ("finance", "operate"), "parameters": ("folio_type", "folio_ref", "transaction_code", "amount")},
    "finance.reverse_posting": {"perform": "reverse_posting", "permission": ("finance", "operate"), "parameters": ("posting_id", "reason")},
    "finance.open_day": {"perform": "open_business_day", "permission": ("finance", "manage"), "parameters": ("business_date",)},
    "finance.close_day": {"perform": "close_business_day", "permission": ("finance", "manage"), "parameters": ("business_date",)},
    "loyalty.award": {"perform": "award_points", "permission": ("front_desk", "operate"), "parameters": ("guest_phone", "points", "reason", "booking_id")},
    "loyalty.redeem": {"perform": "redeem_reward", "permission": ("front_desk", "operate"), "parameters": ("guest_phone", "reward_name")},
}

_PRIORITY = {"low": "low", "normal": "normal", "high": "high", "urgent": "urgent"}
_CLEANING_STATUS = {"pending": "pending", "assigned": "assigned", "in_progress": "in_progress", "completed": "completed", "inspected": "inspected", "cancelled": "cancelled", "quality_check": "inspected"}
_MAINTENANCE_STATUS = {"reported": "reported", "assigned": "assigned", "in_progress": "in_progress", "completed": "completed", "cancelled": "cancelled"}
_FOLIO = {"guest": "guest", "account": "account", "outlet": "outlet"}

_YES = {"yes": "yes", "no": "no"}
_BLOCK_TYPES = {"out_of_order": "out_of_order", "out_of_service": "out_of_service"}


def _now():
    return datetime.now(timezone.utc)


def _text(value):
    return " ".join(str(value or "").split())


def _page(recipe_id, user_id):
    from booking_system.semantic.registry import active_pages, visible_to
    page = (active_pages([recipe_id]) or {}).get(str(recipe_id or ""))
    if not page or not visible_to(page, user_id):
        return None
    body = page.get("body") or {}
    if str(body.get("type") or "") != "action_recipe":
        return None
    return page


def _registered(body):
    action = str(((body.get("solution") or {}).get("backend_action")) or "").strip()
    spec = _REGISTRY.get(action)
    if not spec:
        return None, "unknown action"
    declared = body.get("permission") or ""
    module, level = spec["permission"]
    if declared != module + "." + level:
        return None, "recipe permission does not match the registered action"
    parameters = ((body.get("solution") or {}).get("parameters")) or {}
    if set(parameters) != set(spec["parameters"]):
        return None, "recipe parameters do not match the registered action"
    return spec, ""


def _member(cur, hotel_id, user_id):
    from booking_system.access import membership
    return membership(cur, hotel_id, user_id)


def _may(member, permission):
    from booking_system.access import RANK
    module, level = permission
    held = str((member.get("modules") or {}).get(module) or "none")
    return RANK.get(held, 0) >= RANK[level]


def _value(spec, raw):
    kind = str(spec.get("type") or "")
    if kind == "string":
        text = _text(raw)
        return text if text else None
    if kind == "date":
        text = _text(raw)
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        return text
    if kind == "number":
        try:
            number = float(str(raw).strip())
        except (TypeError, ValueError):
            return None
        if number <= 0:
            return None
        whole = int(number)
        return whole if whole == number else number
    return None


def _parameters(body, given):
    """Typed values from the recipe contract. hotel_id is never taken from the caller. Extra names are refused."""
    specs = ((body.get("solution") or {}).get("parameters")) or {}
    given = given if isinstance(given, dict) else {}
    extra = [name for name in given if name not in specs and name != "hotel_id"]
    if extra:
        return None, "unknown parameter " + ", ".join(sorted(extra))
    values = {}
    missing = []
    for name, spec in specs.items():
        raw = given.get(name)
        if raw in (None, ""):
            if spec.get("required", True):
                missing.append(name)
            continue
        value = _value(spec, raw)
        if value is None:
            return None, name + " is not a " + str(spec.get("type") or "value")
        values[name] = value
    if missing:
        return None, "missing " + ", ".join(missing)
    return values, ""


def _booking(hotel_id, booking_id):
    return db.row(
        """SELECT s.booking_id, s.guest_id, s.booking_status, s.check_in_date, s.check_out_date, g.guest_name,
                  (SELECT t.room_type_name FROM booking_room_night n
                   JOIN room_type t ON t.room_type_id = n.room_type_id
                   WHERE n.hotel_id = s.hotel_id AND n.booking_id = s.booking_id
                   ORDER BY n.stay_date LIMIT 1) AS room_type
           FROM booking_summary s JOIN guest g ON g.guest_id = s.guest_id AND g.hotel_id = s.hotel_id
           WHERE s.hotel_id = %s AND s.booking_id = %s""",
        (hotel_id, booking_id),
    )


def _room(hotel_id, room_number):
    return db.row(
        """SELECT r.room_number, t.room_type_name
           FROM room r JOIN room_type t ON t.room_type_id = r.room_type_id
           WHERE r.hotel_id = %s AND upper(r.room_number) = upper(%s)""",
        (hotel_id, room_number),
    )


def _guest(hotel_id, phone):
    from booking_system.guest_manager import read_guest
    return read_guest(hotel_id, phone)


def _readiness(hotel_id, room_number):
    return db.row(
        """SELECT s.readiness_status
           FROM room_readiness_status s JOIN room r ON r.room_id = s.room_id
           WHERE s.hotel_id = %s AND upper(r.room_number) = upper(%s)""",
        (hotel_id, room_number),
    )


def _open_request(hotel_id, guest_id, request):
    text = _text(request)
    return db.row(
        """SELECT request_id, request FROM guest_request
           WHERE hotel_id = %s AND guest_id = %s AND status = 'open'
             AND (request = %s OR request_id = %s)
           LIMIT 1""",
        (hotel_id, guest_id, text, text),
    )


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _preview(hotel_id, action, values):
    """Facts the approval card may show. Names and rooms come from this hotel's records."""
    from booking_system.hotel_registry import read_hotel_by_id
    hotel = read_hotel_by_id(hotel_id) or {}
    preview = {"hotel_name": hotel.get("hotel_name") or ""}
    booking_guest = ""
    booking_id = str(values.get("booking_id") or "")
    if booking_id:
        row = _booking(hotel_id, booking_id)
        if not row:
            return None, "booking is not at this hotel"
        booking_guest = str(row["guest_id"] or "")
        preview.update(
            guest_name=row["guest_name"] or "",
            booking_id=row["booking_id"],
            booking_status=row["booking_status"] or "",
            check_in_date=_iso(row["check_in_date"]),
            check_out_date=_iso(row["check_out_date"]),
            room_type=row["room_type"] or "",
        )
    for name in ("room_number", "old_room_number", "new_room_number"):
        number = str(values.get(name) or "")
        if not number:
            continue
        room = _room(hotel_id, number)
        if not room:
            return None, "room " + number + " is not at this hotel"
        preview[name] = room["room_number"]
        values[name] = room["room_number"]
    if action == "reservation.create":
        from booking_system.booking_manager import stay_range
        from booking_system.desk_actions import _dates
        guest = _guest(hotel_id, values.get("guest_phone"))
        if not guest:
            return None, "guest phone is not at this hotel"
        known = db.row(
            "SELECT room_type_name FROM room_type WHERE hotel_id = %s AND room_type_name = %s AND status = 'active'",
            (hotel_id, str(values.get("room_type") or "").upper()),
        )
        if not known:
            return None, "room type is not at this hotel"
        stay = stay_range(_dates(values.get("stay_dates")))
        if stay.get("error"):
            return None, stay["error"]
        preview.update(
            guest_name=guest.get("guest_name") or "", guest_phone=guest.get("guest_phone") or "",
            room_type=known["room_type_name"], stay_dates=values.get("stay_dates") or "",
            check_in_date=_iso(stay.get("check_in")), check_out_date=_iso(stay.get("check_out")),
        )
        values["room_type"] = known["room_type_name"]
    if values.get("new_room_type"):
        known = db.row(
            "SELECT room_type_name FROM room_type WHERE hotel_id = %s AND room_type_name = %s AND status = 'active'",
            (hotel_id, str(values.get("new_room_type") or "").upper()),
        )
        if not known:
            return None, "room type is not at this hotel"
        values["new_room_type"] = known["room_type_name"]
        preview["new_room_type"] = known["room_type_name"]
    if values.get("new_stay_dates"):
        from booking_system.booking_manager import stay_range
        from booking_system.desk_actions import _dates
        stay = stay_range(_dates(values.get("new_stay_dates")))
        if stay.get("error"):
            return None, stay["error"]
        preview["check_in_date"] = _iso(stay.get("check_in"))
        preview["check_out_date"] = _iso(stay.get("check_out"))
    guest = None
    if values.get("guest_phone") and action != "reservation.create":
        guest = _guest(hotel_id, values.get("guest_phone"))
        if not guest:
            return None, "guest phone is not at this hotel"
        preview.update(guest_name=guest.get("guest_name") or "", guest_phone=guest.get("guest_phone") or "")
        values["guest_phone"] = guest.get("guest_phone") or values["guest_phone"]
        if booking_id and str(guest.get("guest_id") or "") != booking_guest:
            return None, "booking is not this guest's"
    if action == "guest.update_profile" and values.get("guest_name"):
        preview["updated_name"] = values["guest_name"]
    if action == "guest.close_request":
        found = _open_request(hotel_id, guest.get("guest_id"), values.get("request"))
        if not found:
            return None, "Task not found"
        values["request"] = found["request"]
        preview["request"] = found["request"]
    if action == "guest.preference":
        values["preference"] = _text(values.get("preference")).lower()
        preview["preference"] = values["preference"]
    if action == "room.set_readiness":
        current = _readiness(hotel_id, values.get("room_number"))
        if not current:
            return None, "Room " + str(values.get("room_number") or "") + " has no readiness status"
        preview["current_readiness"] = current["readiness_status"] or ""
        preview["new_status"] = values.get("new_status") or ""
    if action == "room.block":
        preview["block_type"] = values.get("block_type") or "out_of_order"
    if action == "reservation.undo_check_in":
        preview["mark_clean"] = values.get("mark_clean") or "yes"
    if action == "reservation.reassign_room":
        preview["mark_old_clean"] = values.get("mark_old_clean") or "unchanged"
    if action in ("room.block", "room.release", "inventory.set_prices") and values.get("end_date") < values.get("start_date"):
        return None, "end_date is before start_date"
    problem = _preview_floor(hotel_id, action, values, preview, guest)
    if problem:
        return None, problem
    for name in (
        "payment_amount", "payment_method", "payment_seq", "stay_date", "new_stay_dates", "new_room_type", "new_rooms_booked",
        "email", "country", "special_request", "reason", "notes", "mark_clean", "mark_old_clean", "new_status",
        "start_date", "end_date", "block_type", "request", "requested_for", "preference", "value", "complaint",
        "staff_phone", "task_type", "priority", "cleaning_id", "area", "urgency", "issue", "request_id", "status",
        "folio_type", "folio_ref", "transaction_code", "amount", "posting_id", "business_date", "points",
        "reward_name", "quantity", "price", "new_total_amount",
    ):
        if name in values and name not in preview:
            preview[name] = values[name]
    if "room_number" not in preview and preview.get("booking_id"):
        preview["room_number"] = ""
    return preview, ""


def _person(hotel_id, staff, department=""):
    if department:
        from booking_system.staff_manager import assignable_staff
        person, reason = db.transaction(lambda cur: assignable_staff(cur, hotel_id, staff, department))
    else:
        from booking_system.staff_manager import one_staff
        person, reason = db.transaction(lambda cur: one_staff(cur, hotel_id, staff))
    return (dict(person) if person else None), reason


def _show_staff(values, preview, person):
    preview["staff_name"] = person.get("staff_name") or ""
    preview["staff_phone"] = values.get("staff_phone") or ""
    return ""


def _preview_floor(hotel_id, action, values, preview, guest):
    """Names, tasks, prices, and folios the later cards show. A missing record stops the card."""
    if action in ("housekeeping.create_task", "housekeeping.start"):
        person, reason = _person(hotel_id, values.get("staff_phone"), "HOUSEKEEPING")
        if not person:
            return reason
        _show_staff(values, preview, person)
        preview["task_type"] = values.get("task_type") or "standard_cleaning"
        preview["priority"] = values.get("priority") or "normal"
    if action in ("housekeeping.clock_in", "housekeeping.clock_out"):
        person, reason = _person(hotel_id, values.get("staff_phone"))
        if not person:
            return reason
        _show_staff(values, preview, person)
    if action == "housekeeping.progress":
        row = db.row(
            """SELECT t.cleaning_task_id, t.task_status, r.room_number
               FROM cleaning_task t JOIN room r ON r.room_id = t.room_id
               WHERE t.hotel_id = %s AND t.cleaning_task_id = %s""",
            (hotel_id, values.get("cleaning_id")),
        )
        if not row:
            return "cleaning task " + str(values.get("cleaning_id") or "") + " not found"
        preview.update(cleaning_id=row["cleaning_task_id"], room_number=row["room_number"], current_status=row["task_status"] or "")
        if values.get("staff_phone"):
            person, reason = _person(hotel_id, values.get("staff_phone"), "HOUSEKEEPING")
            if not person:
                return reason
            _show_staff(values, preview, person)
    if action == "maintenance.create":
        has_room = bool(values.get("room_number"))
        has_area = bool(_text(values.get("area")))
        if has_room == has_area:
            return "give exactly one of room_number or area"
        if has_area:
            values["area"] = _text(values.get("area")).upper()
            preview["area"] = values["area"]
        preview["urgency"] = values.get("urgency") or "normal"
        preview["issue"] = values.get("issue") or ""
    if action == "maintenance.progress":
        row = db.row(
            """SELECT m.request_id, m.request_status, m.issue, r.room_number, m.area
               FROM maintenance_request m LEFT JOIN room r ON r.room_id = m.room_id
               WHERE m.hotel_id = %s AND m.request_id = %s""",
            (hotel_id, values.get("request_id")),
        )
        if not row:
            return "maintenance request " + str(values.get("request_id") or "") + " not found"
        preview.update(request_id=row["request_id"], current_status=row["request_status"] or "", issue=row["issue"] or "",
                       room_number=row["room_number"] or "", area=row["area"] or "")
        if values.get("staff_phone"):
            person, reason = _person(hotel_id, values.get("staff_phone"), "MAINTENANCE")
            if not person:
                return reason
            _show_staff(values, preview, person)
    if action == "reservation.price_override":
        row = db.row("SELECT gross_amount, currency FROM booking WHERE hotel_id = %s AND booking_id = %s", (hotel_id, values.get("booking_id")))
        preview["current_total"] = "" if not row or row["gross_amount"] is None else str(row["gross_amount"])
        preview["new_total_amount"] = values.get("new_total_amount")
    if values.get("room_type") and action != "reservation.create":
        known = db.row(
            "SELECT room_type_name FROM room_type WHERE hotel_id = %s AND room_type_name = %s AND status = 'active'",
            (hotel_id, str(values.get("room_type") or "").upper()),
        )
        if not known:
            return "room type is not at this hotel"
        values["room_type"] = known["room_type_name"]
        preview["room_type"] = known["room_type_name"]
    if action in ("finance.post_charge", "finance.folio_payment"):
        code = db.row(
            "SELECT transaction_code, transaction_type FROM transaction_code WHERE hotel_id = %s AND transaction_code = %s",
            (hotel_id, values.get("transaction_code")),
        )
        if not code:
            return "transaction code " + str(values.get("transaction_code") or "") + " not found"
        expected = ("charge",) if action == "finance.post_charge" else ("payment", "refund")
        if code["transaction_type"] not in expected:
            return "code " + code["transaction_code"] + " is a " + code["transaction_type"] + " code"
        preview["transaction_code"] = code["transaction_code"]
        preview["transaction_type"] = code["transaction_type"]
    if action == "finance.reverse_posting":
        row = db.row(
            "SELECT posting_id, folio_type, folio_ref, amount FROM finance_posting WHERE hotel_id = %s AND posting_id = %s",
            (hotel_id, values.get("posting_id")),
        )
        if not row:
            return "posting not found"
        preview.update(posting_id=row["posting_id"], folio_type=row["folio_type"], folio_ref=row["folio_ref"], amount=str(row["amount"]))
    if action in ("finance.open_day", "finance.close_day"):
        opened = db.row("SELECT business_date FROM business_day WHERE hotel_id = %s AND status = 'open'", (hotel_id,))
        latest = db.row("SELECT business_date FROM business_day WHERE hotel_id = %s ORDER BY business_date DESC LIMIT 1", (hotel_id,))
        preview["open_business_date"] = _iso(opened["business_date"]) if opened else "none"
        preview["latest_business_date"] = _iso(latest["business_date"]) if latest else "none"
    if action in ("loyalty.award", "loyalty.redeem"):
        member = db.row("SELECT status FROM loyalty_member WHERE hotel_id = %s AND guest_id = %s", (hotel_id, (guest or {}).get("guest_id")))
        if not member or member["status"] != "active":
            return "Guest is not a loyalty member"
    if action == "loyalty.redeem":
        name = _text(values.get("reward_name")).upper()
        reward = db.row(
            "SELECT reward_name, points_cost FROM loyalty_reward WHERE hotel_id = %s AND reward_name = %s AND status = 'active'",
            (hotel_id, name),
        )
        if not reward:
            return "Reward " + str(values.get("reward_name") or "") + " not found"
        values["reward_name"] = reward["reward_name"]
        preview["reward_name"] = reward["reward_name"]
        preview["points_cost"] = reward["points_cost"]
    return ""


def _fill(template, preview):
    text = str(template or "")
    for name, value in (preview or {}).items():
        text = text.replace("{" + name + "}", str(value if value is not None else ""))
    return re.sub(r"\{[a-z0-9_]+\}", "", text)


def _obj(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _card(body, preview):
    approval = body.get("request_approval") or {}
    return {
        "title": _fill(approval.get("title"), preview),
        "message": _fill(approval.get("message"), preview),
        "approve_label": str(approval.get("approve_label") or "Approve"),
        "reject_label": str(approval.get("reject_label") or "Cancel"),
    }


def _key(hotel_id, recipe_id, version, action, values):
    raw = json.dumps({"hotel_id": hotel_id, "recipe_id": recipe_id, "version": version, "action": action, "parameters": values}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _public(row):
    approval = _obj(row.get("approval"))
    return {
        "approval_id": row["approval_id"],
        "recipe_id": row["recipe_id"],
        "recipe_version": row["recipe_version"],
        "backend_action": row["backend_action"],
        "status": row["status"],
        "title": approval.get("title") or "",
        "message": approval.get("message") or "",
        "approve_label": approval.get("approve_label") or "Approve",
        "reject_label": approval.get("reject_label") or "Cancel",
        "expires_at": row["expires_at"].isoformat() if hasattr(row["expires_at"], "isoformat") else str(row["expires_at"] or ""),
    }


def _supersede(cur, hotel_id, recipe_id, values, keep):
    column = next((name for name in ("guest_phone", "booking_id", "room_number", "staff_phone", "cleaning_id", "request_id", "posting_id", "folio_ref", "room_type", "business_date") if values.get(name)), "")
    target = str(values.get(column) or "") if column else ""
    # A business date is the proposal itself. A later open or close replaces every earlier card for that recipe.
    if column == "business_date":
        cur.execute(
            """UPDATE leon_pending_action
               SET status = 'superseded', decided_at = now()
               WHERE hotel_id = %s AND recipe_id = %s AND status = 'pending' AND approval_id <> %s""",
            (hotel_id, recipe_id, keep),
        )
        return
    if not target:
        return
    cur.execute(
        """UPDATE leon_pending_action
           SET status = 'superseded', decided_at = now()
           WHERE hotel_id = %s AND recipe_id = %s AND status = 'pending' AND approval_id <> %s
             AND parameters->>%s = %s""",
        (hotel_id, recipe_id, keep, column, target),
    )


def prepare_action(hotel_id, user_id, recipe_id, parameters):
    """Validate the published action recipe, store one pending operation, and return its approval card. Nothing in the PMS changes."""
    page = _page(recipe_id, user_id)
    if not page:
        return {"error": "action recipe is not available"}
    body = page.get("body") or {}
    spec, problem = _registered(body)
    if problem:
        return {"error": problem}
    values, problem = _parameters(body, parameters)
    if problem:
        return {"error": problem}
    action = str((body.get("solution") or {}).get("backend_action") or "")
    if action == "reservation.modify" and not any(values.get(name) for name in ("new_stay_dates", "new_room_type", "new_rooms_booked")):
        return {"error": "missing a change"}
    if action == "guest.update_profile" and not any(name in values for name in ("guest_name", "email", "country", "special_request")):
        return {"error": "missing a profile field"}
    if "payment_method" in values:
        from booking_system.desk_actions import _METHODS
        method = str(values["payment_method"]).upper()
        if method not in _METHODS:
            return {"error": "payment method is not one this hotel records"}
        values["payment_method"] = method
    for name in ("mark_clean", "mark_old_clean"):
        if name not in values:
            continue
        flag = _YES.get(str(values[name]).lower())
        if not flag:
            return {"error": name + " is yes or no"}
        values[name] = flag
    if action == "room.set_readiness" and "new_status" in values:
        from booking_system.front_desk_manager import READINESS
        status = str(values["new_status"]).lower()
        if status not in READINESS:
            return {"error": "readiness must be clean, dirty, cleaning, or inspected"}
        values["new_status"] = status
    if action == "housekeeping.progress" and "new_status" in values:
        status = _CLEANING_STATUS.get(str(values["new_status"]).lower().replace(" ", "_"))
        if not status:
            return {"error": "cleaning status is pending, assigned, in_progress, completed, inspected, or cancelled"}
        values["new_status"] = status
    if action == "maintenance.progress" and "status" in values:
        status = _MAINTENANCE_STATUS.get(str(values["status"]).lower().replace(" ", "_"))
        if not status:
            return {"error": "maintenance status is reported, assigned, in_progress, completed, or cancelled"}
        values["status"] = status
    for name in ("priority", "urgency"):
        if name not in values:
            continue
        flag = _PRIORITY.get(str(values[name]).lower())
        if not flag:
            return {"error": name + " is low, normal, high, or urgent"}
        values[name] = flag
    if "task_type" in values:
        values["task_type"] = str(values["task_type"]).strip().lower()
    if "folio_type" in values:
        folio = _FOLIO.get(str(values["folio_type"]).lower())
        if not folio:
            return {"error": "folio type is guest, account, or outlet"}
        values["folio_type"] = folio
    if "transaction_code" in values:
        values["transaction_code"] = str(values["transaction_code"]).strip().upper()
    if "quantity" in values and not str(values["quantity"]).isdigit():
        return {"error": "quantity is a whole number, and 0 stops sale"}
    if "block_type" in values:
        kind = _BLOCK_TYPES.get(str(values["block_type"]).lower().replace(" ", "_"))
        if not kind:
            return {"error": "block type is out_of_order or out_of_service"}
        values["block_type"] = kind
    if "requested_for" in values:
        try:
            datetime.fromisoformat(values["requested_for"])
        except ValueError:
            return {"error": "requested_for must be an ISO date-time"}
    preview, problem = _preview(hotel_id, action, values)
    if problem:
        return {"error": problem}
    card = _card(body, preview)
    version = int(page.get("version") or 0)
    digest = _key(hotel_id, str(recipe_id), version, action, values)

    def write(cur):
        member = _member(cur, hotel_id, user_id)
        if not member or not _may(member, spec["permission"]):
            return {"error": "outside your access"}
        cur.execute(
            """SELECT approval_id, recipe_id, recipe_version, backend_action, approval, status, expires_at
               FROM leon_pending_action
               WHERE hotel_id = %s AND idempotency_key = %s AND status = 'pending' AND expires_at > now()
               ORDER BY created_at DESC LIMIT 1""",
            (hotel_id, digest),
        )
        found = cur.fetchone()
        if found:
            return dict(found)
        from booking_system.hotel_registry import new_id
        approval_id = new_id("apr")
        _supersede(cur, hotel_id, str(recipe_id), values, approval_id)
        cur.execute(
            """INSERT INTO leon_pending_action
               (approval_id, hotel_id, requester_id, recipe_id, recipe_version, backend_action, parameters, preview, approval, status, idempotency_key, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)""",
            (approval_id, hotel_id, user_id, str(recipe_id), version, action, json.dumps(values), json.dumps(preview, default=str), json.dumps(card), digest, _now() + _TTL),
        )
        cur.execute(
            "SELECT approval_id, recipe_id, recipe_version, backend_action, approval, status, expires_at FROM leon_pending_action WHERE approval_id = %s",
            (approval_id,),
        )
        return dict(cur.fetchone())

    stored = db.transaction(write)
    if stored.get("error"):
        return stored
    return _public(stored)


def _load(cur, hotel_id, approval_id):
    cur.execute(
        """SELECT approval_id, hotel_id, recipe_id, recipe_version, backend_action, parameters, preview, approval, status, result, expires_at
           FROM leon_pending_action WHERE approval_id = %s FOR UPDATE""",
        (str(approval_id or ""),),
    )
    row = cur.fetchone()
    if not row or row["hotel_id"] != hotel_id:
        return None
    return dict(row)


def _finish(cur, approval_id, user_id, status, result):
    cur.execute(
        """UPDATE leon_pending_action
           SET status = %s, result = %s, decided_at = now(), decided_by_user_id = %s
           WHERE approval_id = %s""",
        (status, json.dumps(result, default=str), user_id, approval_id),
    )


def reject_action(hotel_id, user_id, approval_id):
    """Cancel one pending action. The PMS is left as it is."""
    def write(cur):
        row = _load(cur, hotel_id, approval_id)
        if not row:
            return {"error": "approval is not at this hotel"}
        if row["status"] == "rejected":
            return {"approval_id": row["approval_id"], "status": "rejected"}
        if row["status"] != "pending":
            return {"error": "approval is " + row["status"]}
        _finish(cur, row["approval_id"], user_id, "rejected", {"rejected": True})
        return {"approval_id": row["approval_id"], "status": "rejected"}
    return db.transaction(write)


def approve_action(hotel_id, user_id, approval_id):
    """Run the stored action once through its registered PMS service. A second approval returns the stored result."""
    def claim(cur):
        member = _member(cur, hotel_id, user_id)
        row = _load(cur, hotel_id, approval_id)
        if not row:
            return {"error": "approval is not at this hotel"}
        spec = _REGISTRY.get(row["backend_action"])
        if not member or not spec or not _may(member, spec["permission"]):
            return {"error": "outside your access"}
        if row["status"] in ("approved", "failed", "rejected", "expired", "superseded"):
            return {"done": True, "approval_id": row["approval_id"], "status": row["status"], "result": _obj(row.get("result"))}
        if row["status"] == "running":
            return {"error": "approval is already running"}
        expires = row["expires_at"]
        if getattr(expires, "tzinfo", None) is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= _now():
            _finish(cur, row["approval_id"], user_id, "expired", {"expired": True})
            return {"done": True, "approval_id": row["approval_id"], "status": "expired", "result": {"expired": True}}
        cur.execute("UPDATE leon_pending_action SET status = 'running' WHERE approval_id = %s", (row["approval_id"],))
        return {"claimed": True, "row": row, "perform": spec["perform"]}

    claimed = db.transaction(claim)
    if claimed.get("error") or claimed.get("done"):
        return claimed
    row = claimed["row"]
    parameters = _obj(row.get("parameters"))
    from booking_system.desk_actions import _PERFORM, _failure_text, _ok
    try:
        outcome = _PERFORM[claimed["perform"]](hotel_id, user_id, parameters)
    except Exception as error:
        outcome = {"error": str(error)}
    ok = _ok(outcome)
    status = "approved" if ok else "failed"
    result = {"ok": ok, "detail": "" if ok else _failure_text(outcome) or "the operation was not completed"}

    def finish(cur):
        current = _load(cur, hotel_id, row["approval_id"])
        if not current or current["status"] != "running":
            return {"error": "approval changed before it finished"}
        _finish(cur, row["approval_id"], user_id, status, result)
        return {"approval_id": row["approval_id"], "status": status, "result": result}

    return db.transaction(finish)
