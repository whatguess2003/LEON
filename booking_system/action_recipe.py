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
    "reservation.reassign_room": {"perform": "reassign_room", "permission": ("reservations", "operate"), "parameters": ("booking_id", "stay_date", "old_room_number", "new_room_number")},
    "guest.update_profile": {"perform": "update_guest_profile", "permission": ("front_desk", "operate"), "parameters": ("guest_phone", "guest_name", "email", "country", "special_request")},
}


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
        """SELECT s.booking_id, s.booking_status, s.check_in_date, s.check_out_date, g.guest_name,
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


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _preview(hotel_id, action, values):
    """Facts the approval card may show. Names and rooms come from this hotel's records."""
    from booking_system.hotel_registry import read_hotel_by_id
    hotel = read_hotel_by_id(hotel_id) or {}
    preview = {"hotel_name": hotel.get("hotel_name") or ""}
    booking_id = str(values.get("booking_id") or "")
    if booking_id:
        row = _booking(hotel_id, booking_id)
        if not row:
            return None, "booking is not at this hotel"
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
    if action == "guest.update_profile":
        guest = _guest(hotel_id, values.get("guest_phone"))
        if not guest:
            return None, "guest phone is not at this hotel"
        preview.update(guest_name=guest.get("guest_name") or "", guest_phone=guest.get("guest_phone") or "")
        if values.get("guest_name"):
            preview["updated_name"] = values["guest_name"]
    for name in ("payment_amount", "payment_method", "payment_seq", "stay_date", "new_stay_dates", "new_room_type", "new_rooms_booked", "email", "country", "special_request"):
        if name in values:
            preview[name] = values[name]
    if "room_number" not in preview and preview.get("booking_id"):
        preview["room_number"] = ""
    return preview, ""


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
    target = str(values.get("booking_id") or values.get("guest_phone") or "")
    if not target:
        return
    cur.execute(
        """UPDATE leon_pending_action
           SET status = 'superseded', decided_at = now()
           WHERE hotel_id = %s AND recipe_id = %s AND status = 'pending' AND approval_id <> %s
             AND (parameters->>'booking_id' = %s OR parameters->>'guest_phone' = %s)""",
        (hotel_id, recipe_id, keep, target, target),
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
