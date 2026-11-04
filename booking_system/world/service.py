"""World Mode service. Every read goes through the existing desk services and every write is an existing action recipe prepared
through booking_system.action_recipe.prepare_action, then approved by the person through POST /api/leon/actions/{id}/approve.

The session gives the hotel (never the caller). Fields are trimmed to the person's module levels. Nothing here invents PMS state:
an unknown photo is reported as no_photo, an unavailable action carries the reason, and a prepared action is only 'pending'."""

import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from booking_system import db
from booking_system.access import RANK
from booking_system.world import manifest as manifest_contract
from booking_system.world import media_check
from booking_system.world.spans import build_blocks, build_spans, build_unassigned, floor_of

SETTING_KEY = "front_desk.world_mode"
MAX_WINDOW_DAYS = 62
READINESS = ("clean", "dirty", "cleaning", "inspected")

# Presentation intent -> the published action recipe and how its parameters are filled. The recipe contract, the registered PMS
# operation, the permission check and the approval card all stay in action_recipe; this table only names which recipe an intent uses.
INTENTS = {
    "check_in": {"recipe_id": "reservation_check_in", "label": "Check in", "needs": ("booking_id", "room_number")},
    "check_out": {"recipe_id": "reservation_check_out", "label": "Check out", "needs": ("booking_id", "room_number")},
    "set_readiness": {"recipe_id": "room_set_readiness", "label": "Set readiness", "needs": ("room_number", "new_status")},
}
# The module level each intent's registered action needs (mirrors action_recipe._REGISTRY; checked again there on prepare and approve).
INTENT_PERMISSION = {"check_in": ("reservations", "operate"), "check_out": ("reservations", "operate"), "set_readiness": ("housekeeping", "view")}


class WorldError(Exception):
    def __init__(self, status, state, message):
        super().__init__(message)
        self.status, self.state, self.message = status, state, message


# ---------- session helpers ----------

def _level(session, module):
    return RANK.get(str((session.get("modules") or {}).get(module) or "none"), 0)


def _may(session, module, level):
    return _level(session, module) >= RANK[level]


def _today(session):
    return datetime.now(ZoneInfo(session.get("hotel_timezone") or "UTC")).date()


def _day(text, name):
    try:
        return date.fromisoformat(str(text or "")[:10])
    except ValueError:
        raise WorldError(400, "error", name + " must be YYYY-MM-DD")


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------- feature flag ----------

def platform_switch():
    """WORLD_MODE_ENABLED: unset/0/false -> off everywhere; 1/true -> on for hotels whose front_desk.world_mode is true;
    all -> on for every hotel (development only)."""
    value = str(os.getenv("WORLD_MODE_ENABLED") or "").strip().lower()
    if value == "all":
        return "all"
    return "hotel" if value in ("1", "true", "yes", "on") else "off"


def hotel_switch(hotel_id):
    found = db.row(
        """SELECT s.value FROM hotel_setting s WHERE s.hotel_id = %s AND s.setting_key = %s AND s.effective_from <= hotel_local_date(%s)
           ORDER BY s.effective_from DESC, s.set_at DESC LIMIT 1""",
        (hotel_id, SETTING_KEY, hotel_id))
    return bool(found) and found["value"] in (True, "true")


def enabled(hotel_id):
    """(on, reason)."""
    switch = platform_switch()
    if switch == "off":
        return False, "World Mode is switched off on this server (WORLD_MODE_ENABLED)."
    if switch == "all":
        return True, ""
    if not hotel_switch(hotel_id):
        return False, "World Mode is off for this hotel (setting " + SETTING_KEY + ")."
    return True, ""


def require_enabled(session):
    on, reason = enabled(session["hotel_id"])
    if not on:
        raise WorldError(409, "disabled", reason)


def config(session):
    on, reason = enabled(session["hotel_id"])
    return {
        "enabled": on, "reason": reason, "hotel_name": session.get("hotel_name"), "hotel_timezone": session.get("hotel_timezone"),
        "hotel_currency": session.get("hotel_currency"), "today": _today(session).isoformat(), "user_name": session.get("user_name"),
        "role_name": session.get("role_name"),
        "can": {
            "see_guest_contact": _may(session, "reservations", "view"),
            "see_amounts": _may(session, "reservations", "view") or _may(session, "finance", "view"),
            "manage_media": _may(session, "admin", "manage"),
            "intents": {name: _may(session, *INTENT_PERMISSION[name]) for name in INTENTS},
        },
        "max_window_days": MAX_WINDOW_DAYS,
    }


# ---------- calendar ----------

def calendar(session, start_date, end_date):
    """Rooms, stay spans, blocks and unassigned nights for [start_date, end_date] (hotel-local nights), with readiness per room."""
    from booking_system.front_desk_manager import intelligent_front_desk as desk
    start, end = _day(start_date, "start_date"), _day(end_date, "end_date")
    if end < start:
        raise WorldError(400, "error", "end_date must be on or after start_date")
    if (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise WorldError(400, "error", "at most " + str(MAX_WINDOW_DAYS) + " days at a time")
    # One more night than the window so a span that runs past end is known to continue (and the desk read adds the night before).
    data = desk.read_room_number_assignments_calendar_internal(session["hotel_id"], start.isoformat(), (end + timedelta(days=1)).isoformat())
    if data.get("error"):
        raise WorldError(400, "error", data["error"])
    readiness = {item["room_number"]: item for item in desk.read_all_room_readiness_status_internal(session["hotel_id"]).get("items") or []}
    today = _today(session)
    spans = build_spans(data, start.isoformat(), end.isoformat(), today)
    contact = _may(session, "reservations", "view")
    for span in spans:
        if not contact:
            span["guest_name"] = _initials(span["guest_name"])
    rooms = [{"room_number": room["room_number"], "room_type": room["room_type"], "floor": floor_of(room["room_number"]),
              "readiness": (readiness.get(room["room_number"]) or {}).get("readiness_status")} for room in data.get("rooms") or []]
    return {
        "state": "ok", "as_of": _now_iso(), "hotel_name": session.get("hotel_name"), "today": today.isoformat(),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "days": [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)],
        "rooms": rooms, "stays": spans, "blocks": build_blocks(data), "unassigned": build_unassigned(data, start.isoformat(), end.isoformat()),
        "redacted": {"guest_names": not contact},
    }


def _initials(name):
    parts = [part for part in str(name or "").split() if part]
    return ". ".join(part[0] for part in parts[:3]) + "." if parts else "Guest"


# ---------- media ----------

def _media_rows(hotel_id, kind, refs):
    refs = [str(ref).strip().upper() for ref in refs if ref]
    if not refs:
        return []
    return db.rows(
        """SELECT media_id, binding_kind, binding_ref, caption, width, height, mime, checksum, (thumb_data IS NOT NULL) AS has_thumb
           FROM world_media WHERE hotel_id = %s AND binding_kind = %s AND binding_ref = ANY(%s) AND status = 'approved'
           ORDER BY display_order, uploaded_at""",
        (hotel_id, kind, refs))


def _media_public(row, exact):
    return {"media_id": row["media_id"], "caption": row["caption"], "width": row["width"], "height": row["height"],
            "binding": {"kind": row["binding_kind"], "ref": row["binding_ref"]}, "exact": exact,
            "label": "This room" if exact else "Example of room type " + row["binding_ref"], "version": row["checksum"][:16],
            "has_thumb": bool(row["has_thumb"])}


def room_media(hotel_id, room_numbers, room_type):
    """Authentic photos for these rooms first (exact), then their room type (labelled as an example). {state, items}."""
    exact = [_media_public(row, True) for row in _media_rows(hotel_id, "room", room_numbers)]
    examples = [_media_public(row, False) for row in _media_rows(hotel_id, "room_type", [room_type])] if room_type else []
    items = exact + examples
    return {"state": "ok" if items else "no_photo", "items": items}


def media_bytes(session, media_id, variant):
    row = db.row(
        """SELECT mime, data, thumb_mime, thumb_data, checksum FROM world_media
           WHERE hotel_id = %s AND media_id = %s AND status = 'approved'""",
        (session["hotel_id"], str(media_id or "")))
    if not row:
        raise WorldError(404, "not_found", "photo not found")
    if variant == "thumb" and row["thumb_data"] is not None:
        return row["thumb_mime"], bytes(row["thumb_data"]), row["checksum"] + "-t"
    return row["mime"], bytes(row["data"]), row["checksum"]


def list_media(session, kind=None, ref=None):
    rows = db.rows(
        """SELECT media_id, binding_kind, binding_ref, caption, width, height, mime, checksum, (thumb_data IS NOT NULL) AS has_thumb
           FROM world_media WHERE hotel_id = %s AND status = 'approved' AND (%s::varchar IS NULL OR binding_kind = %s)
             AND (%s::varchar IS NULL OR binding_ref = %s) ORDER BY binding_kind, binding_ref, display_order, uploaded_at""",
        (session["hotel_id"], kind, kind, ref, ref))
    return {"state": "ok" if rows else "no_photo", "items": [_media_public(row, row["binding_kind"] == "room") for row in rows]}


def add_media(session, binding_kind, binding_ref, caption, attested, full, thumb):
    """Store one authentic photograph. The uploader attests it is a real, unaltered photo of this hotel; generated images are refused
    by that attestation and by review, never silently accepted."""
    from booking_system.hotel_registry import new_id
    if binding_kind not in ("room", "room_type", "facility"):
        raise WorldError(400, "error", "binding_kind is room, room_type or facility")
    ref = str(binding_ref or "").strip().upper()
    if not ref or len(ref) > 64:
        raise WorldError(400, "error", "binding_ref is required")
    if not attested:
        raise WorldError(400, "error", "confirm this is an authentic, unaltered photograph of this hotel")
    caption = " ".join(str(caption or "").split())[:160]
    if any(char in caption for char in "<>{}`"):
        raise WorldError(400, "error", "caption is plain text")
    info, problem = media_check.check(full)
    if problem:
        raise WorldError(400, "error", "photo: " + problem)
    thumb_info = None
    if thumb:
        thumb_info, problem = media_check.check(thumb, thumb=True)
        if problem:
            raise WorldError(400, "error", "thumbnail: " + problem)
    hotel_id = session["hotel_id"]
    if binding_kind == "room" and not db.row("SELECT 1 FROM room WHERE hotel_id = %s AND upper(room_number) = %s AND status = 'active'", (hotel_id, ref)):
        raise WorldError(404, "not_found", "room " + ref + " is not at this hotel")
    if binding_kind == "room_type" and not db.row("SELECT 1 FROM room_type WHERE hotel_id = %s AND room_type_name = %s AND status = 'active'", (hotel_id, ref)):
        raise WorldError(404, "not_found", "room type " + ref + " is not at this hotel")
    checksum = hashlib.sha256(full).hexdigest()
    media_id = new_id("wmd")[:48]

    def write(cur):
        cur.execute("SELECT media_id, status FROM world_media WHERE hotel_id = %s AND checksum = %s", (hotel_id, checksum))
        found = cur.fetchone()
        if found and found["status"] == "approved":
            return {"media_id": found["media_id"], "duplicate": True}
        if found:
            # The same photo was retired before: publish it again under the new binding and attestation.
            cur.execute(
                """UPDATE world_media SET status = 'approved', retired_at = NULL, binding_kind = %s, binding_ref = %s, caption = %s,
                          thumb_mime = %s, thumb_data = %s, attested_by_user_id = %s, uploaded_at = now() WHERE media_id = %s""",
                (binding_kind, ref, caption, thumb_info["mime"] if thumb_info else None, thumb if thumb_info else None, session["user_id"],
                 found["media_id"]))
            return {"media_id": found["media_id"], "duplicate": False, "republished": True}
        cur.execute(
            """INSERT INTO world_media (media_id, hotel_id, binding_kind, binding_ref, caption, mime, width, height, byte_size, checksum, data,
                                        thumb_mime, thumb_data, attested_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (media_id, hotel_id, binding_kind, ref, caption, info["mime"], info["width"], info["height"], info["bytes"], checksum, full,
             thumb_info["mime"] if thumb_info else None, thumb if thumb_info else None, session["user_id"]))
        return {"media_id": media_id, "duplicate": False}
    return dict(db.transaction(write), state="ok")


def retire_media(session, media_id):
    found = db.row("UPDATE world_media SET status = 'retired', retired_at = now() WHERE hotel_id = %s AND media_id = %s AND status = 'approved' "
                   "RETURNING media_id", (session["hotel_id"], str(media_id or "")))
    if not found:
        raise WorldError(404, "not_found", "photo not found")
    return {"state": "ok", "media_id": found["media_id"], "status": "retired"}


# ---------- presentations ----------

def _intent(name, available, reason=""):
    spec = INTENTS[name]
    return {"intent": name, "label": spec["label"], "recipe_id": spec["recipe_id"], "available": bool(available), "reason": reason}


def reservation(session, booking_id, stay_date=None):
    """The reservation panel: the desk's booking detail trimmed to the person's levels, authentic media, and action intents."""
    from booking_system.front_desk_manager import intelligent_front_desk as desk
    today = _today(session)
    day = _day(stay_date, "stay_date") if stay_date else today
    detail = desk.read_booking_detail_internal(session["hotel_id"], str(booking_id or ""), day.isoformat())
    if detail.get("error"):
        raise WorldError(404, "not_found", detail["error"])
    contact = _may(session, "reservations", "view")
    amounts = contact or _may(session, "finance", "view")
    guest = detail.get("guest") or {}
    booking = detail.get("booking") or {}
    checkin = detail.get("checkin") or {}
    rooms = checkin.get("rooms") or []
    room_type = booking.get("room_type")
    nights = _booking_nights(session["hotel_id"], detail["booking_id"])
    out = {
        "state": "ok", "as_of": _now_iso(), "booking_id": detail["booking_id"], "stay_date": detail["stay_date"],
        "check_in_date": detail["check_in_date"], "check_out_date": detail["check_out_date"],
        "nights": len({night["stay_date"] for night in nights}),
        "booking_status": booking.get("booking_status"), "rooms_booked": booking.get("rooms_booked"), "room_type": room_type,
        "rate_plan": booking.get("rate_plan") or "",
        "rooms_tonight": rooms, "room_plan": nights,
        "guest": {"name": guest.get("guest_name") if contact else _initials(guest.get("guest_name")), "vip": bool(guest.get("vip")),
                  "nationality": guest.get("nationality") or "" if contact else "",
                  "phone": guest.get("guest_phone") if contact else None, "email": guest.get("guest_email") if contact else None,
                  "special_request": guest.get("special_request") or "" if contact else ""},
        "notes": booking.get("booking_notes") if contact else None,
        "payment": ({"currency": (detail.get("payment") or {}).get("currency"), "total_amount": booking.get("total_amount"),
                     "amount_paid": (detail.get("payment") or {}).get("amount_paid"),
                     "balance_due": (detail.get("payment") or {}).get("remaining_balance"),
                     "payment_status": (detail.get("payment") or {}).get("payment_status")} if amounts else None),
        "redacted": {"contact": not contact, "amounts": not amounts},
        "media": room_media(session["hotel_id"], [room["room_number"] for room in rooms], room_type),
    }
    out["actions"] = _reservation_intents(session, today, day, nights, rooms, booking.get("booking_status"))
    return out


def _booking_nights(hotel_id, booking_id):
    rows = db.rows(
        """SELECT n.stay_date, n.room_seq, r.room_number, t.room_type_name, n.night_status
           FROM booking_room_night n JOIN room_type t ON t.room_type_id = n.room_type_id LEFT JOIN room r ON r.room_id = n.room_id
           WHERE n.hotel_id = %s AND n.booking_id = %s ORDER BY n.stay_date, n.room_seq""",
        (hotel_id, booking_id))
    return [{"stay_date": row["stay_date"].isoformat(), "room_seq": row["room_seq"], "room_number": row["room_number"],
             "room_type": row["room_type_name"], "night_status": row["night_status"]} for row in rows]


def _reservation_intents(session, today, day, nights, rooms, booking_status):
    """Which intents to offer and why one is not. The recipe's own checks on prepare and the PMS service on approve stay authoritative."""
    actions = []
    tonight = [night for night in nights if night["stay_date"] == today.isoformat()]
    check_in_room = next((night["room_number"] for night in tonight if night["night_status"] == "booked" and night["room_number"]), None)
    in_house = [night for night in nights if night["night_status"] == "checked_in" and night["room_number"]]
    if not _may(session, *INTENT_PERMISSION["check_in"]):
        actions.append(_intent("check_in", False, "outside your access"))
    elif booking_status not in ("confirmed", "checked_in"):
        actions.append(_intent("check_in", False, "booking is " + str(booking_status or "not open")))
    elif not tonight:
        actions.append(_intent("check_in", False, "no night of this booking is today (" + today.isoformat() + ")"))
    elif not check_in_room:
        reason = "assign a room for tonight first" if any(not night["room_number"] for night in tonight) else "tonight is already checked in"
        actions.append(_intent("check_in", False, reason))
    else:
        actions.append(dict(_intent("check_in", True), room_number=check_in_room))
    if not _may(session, *INTENT_PERMISSION["check_out"]):
        actions.append(_intent("check_out", False, "outside your access"))
    elif not in_house:
        actions.append(_intent("check_out", False, "guest is not checked in"))
    else:
        actions.append(dict(_intent("check_out", True), room_number=in_house[-1]["room_number"]))
    return actions


def room(session, room_number):
    """The room panel: type, readiness, who is in it tonight (as far as the person may see), media and readiness intents."""
    from booking_system.front_desk_manager import intelligent_front_desk as desk
    hotel_id = session["hotel_id"]
    label = str(room_number or "").strip().upper()
    found = db.row(
        """SELECT r.room_number, t.room_type_name FROM room r JOIN room_type t ON t.room_type_id = r.room_type_id
           WHERE r.hotel_id = %s AND upper(r.room_number) = %s AND r.status = 'active'""",
        (hotel_id, label))
    if not found:
        raise WorldError(404, "not_found", "room " + label + " is not at this hotel")
    readiness = desk.read_room_readiness_status_json(hotel_id, found["room_number"])
    today = _today(session)
    tonight = db.row(
        """SELECT n.booking_id, n.night_status, g.guest_name FROM booking_room_night n JOIN room r ON r.room_id = n.room_id
           JOIN booking b ON b.booking_id = n.booking_id JOIN guest g ON g.guest_id = b.guest_id
           WHERE n.hotel_id = %s AND r.room_number = %s AND n.stay_date = %s AND n.night_status IN ('booked', 'checked_in', 'checked_out')
           LIMIT 1""",
        (hotel_id, found["room_number"], today))
    contact = _may(session, "reservations", "view")
    current = (readiness or {}).get("readiness_status") if isinstance(readiness, dict) else None
    can_set = _may(session, *INTENT_PERMISSION["set_readiness"])
    actions = [dict(_intent("set_readiness", can_set and status != current,
                            "" if can_set and status != current else ("already " + status if can_set else "outside your access")),
                    new_status=status, label="Mark " + status) for status in READINESS]
    return {
        "state": "ok", "as_of": _now_iso(), "room_number": found["room_number"], "room_type": found["room_type_name"],
        "floor": floor_of(found["room_number"]), "readiness": current,
        "readiness_changed_at": (readiness or {}).get("status_changed_at") if isinstance(readiness, dict) else None,
        "tonight": ({"booking_id": tonight["booking_id"], "night_status": tonight["night_status"],
                     "guest_name": tonight["guest_name"] if contact else _initials(tonight["guest_name"])} if tonight else None),
        "media": room_media(hotel_id, [found["room_number"]], found["room_type_name"]),
        "actions": actions,
    }


# ---------- actions ----------

def prepare(session, intent, booking_id=None, room_number=None, new_status=None):
    """Prepare the existing action recipe for an intent. Returns the stored approval card (status pending); nothing in the PMS changes
    until the person approves it through POST /api/leon/actions/{approval_id}/approve."""
    from booking_system.action_recipe import prepare_action
    spec = INTENTS.get(str(intent or ""))
    if not spec:
        raise WorldError(400, "error", "unknown intent")
    given = {"booking_id": booking_id, "room_number": room_number, "new_status": new_status}
    parameters = {name: str(given[name]).strip() for name in spec["needs"] if given.get(name) not in (None, "")}
    missing = [name for name in spec["needs"] if name not in parameters]
    if missing:
        raise WorldError(400, "error", "missing " + ", ".join(missing))
    card = prepare_action(session["hotel_id"], session["user_id"], spec["recipe_id"], parameters)
    if card.get("error"):
        status = 403 if card["error"] == "outside your access" else 409
        raise WorldError(status, "forbidden" if status == 403 else "refused", card["error"])
    return dict(card, state="pending", intent=intent,
                approve_path="/api/leon/actions/" + card["approval_id"] + "/approve",
                reject_path="/api/leon/actions/" + card["approval_id"] + "/reject")


# ---------- scene manifests ----------

def active_manifest(session, scene_id):
    found = db.row(
        """SELECT v.manifest, v.scene_version, a.published_at FROM world_scene_active a
           JOIN world_scene_version v ON v.hotel_id = a.hotel_id AND v.scene_id = a.scene_id AND v.scene_version = a.scene_version
           WHERE a.hotel_id = %s AND a.scene_id = %s""",
        (session["hotel_id"], str(scene_id or "")))
    if not found:
        raise WorldError(404, "not_found", "no published scene " + str(scene_id or ""))
    manifest = found["manifest"] if isinstance(found["manifest"], dict) else json.loads(found["manifest"])
    problems = manifest_contract.validate(manifest)
    if problems:
        # A stored revision that no longer validates (contract tightened) is not served; the client shows its fallback.
        raise WorldError(409, "stale", "published scene no longer validates: " + problems[0])
    return {"state": "ok", "scene_version": found["scene_version"], "published_at": found["published_at"].isoformat(), "manifest": manifest}


def publish_manifest(session, manifest):
    """Validate, store as the next immutable revision and make it active, in one transaction."""
    if len(json.dumps(manifest, default=str)) > manifest_contract.MAX_BYTES:
        raise WorldError(400, "error", "manifest is too large")
    problems = manifest_contract.validate(manifest)
    if problems:
        raise WorldError(400, "error", "; ".join(problems[:10]))
    hotel_id, scene_id = session["hotel_id"], manifest["scene_id"]

    def write(cur):
        cur.execute("SELECT COALESCE(MAX(scene_version), 0) + 1 AS next FROM world_scene_version WHERE hotel_id = %s AND scene_id = %s",
                    (hotel_id, scene_id))
        version = cur.fetchone()["next"]
        stored = dict(manifest, scene_version=version, world_id="hotel:" + hotel_id)
        cur.execute("INSERT INTO world_scene_version (hotel_id, scene_id, scene_version, manifest, created_by_user_id) VALUES (%s, %s, %s, %s, %s)",
                    (hotel_id, scene_id, version, json.dumps(stored), session["user_id"]))
        cur.execute(
            """INSERT INTO world_scene_active (hotel_id, scene_id, scene_version, published_by_user_id) VALUES (%s, %s, %s, %s)
               ON CONFLICT (hotel_id, scene_id) DO UPDATE SET scene_version = EXCLUDED.scene_version,
                   published_by_user_id = EXCLUDED.published_by_user_id, published_at = now()""",
            (hotel_id, scene_id, version, session["user_id"]))
        return {"state": "ok", "scene_id": scene_id, "scene_version": version}
    return db.transaction(write)


def rollback_manifest(session, scene_id, scene_version):
    hotel_id = session["hotel_id"]

    def write(cur):
        cur.execute("SELECT 1 FROM world_scene_version WHERE hotel_id = %s AND scene_id = %s AND scene_version = %s",
                    (hotel_id, scene_id, scene_version))
        if not cur.fetchone():
            return None
        cur.execute("UPDATE world_scene_active SET scene_version = %s, published_by_user_id = %s, published_at = now() "
                    "WHERE hotel_id = %s AND scene_id = %s", (scene_version, session["user_id"], hotel_id, scene_id))
        return {"state": "ok", "scene_id": scene_id, "scene_version": scene_version}
    done = db.transaction(write)
    if not done:
        raise WorldError(404, "not_found", "no such scene revision")
    return done
