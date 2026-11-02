"""Room types and physical rooms (room_type, room) with their content documents.

Room type specs and description are the room_type_profile document keyed by room_type_id; a room's display label is the room_profile
document keyed by room_id; rate plans are rate_plans documents of the hotel. Callers pass hotel_id, the acting user_id and, where nights
are written, the hotel timezone from the session. Results are dicts, {'error': ...} on refusal."""

import sys

from booking_system import core_refs, db, inventory_manager
from booking_system.content_store import client
from booking_system.hotel_registry import new_id

AUTO_DIRTY_DAYS = 3
SPEC_KEYS = (
    "room_size_sqm", "max_occupancy", "bed_type", "bed_count", "wifi", "air_conditioning", "private_bathroom", "room_view", "price_tier",
    "floor_number", "window_type", "bathroom_type", "soundproofing", "smart_features", "kitchenette", "minibar", "workspace_type",
    "accessibility_features", "renovation_year", "flooring_type", "lighting", "air_purifier", "pet_friendly", "connecting_rooms",
    "max_children", "crib_available",
)
TYPE_DOCS = client["leon_hotel_memory"]["room_type_profile"]
ROOM_DOCS = client["leon_hotel_memory"]["room_profile"]
RATE_DOCS = client["leon_hotel_memory"]["rate_plans"]


def _name(value):
    return str(value or "").strip().upper()


def _numbers(values):
    """(distinct room numbers, '') or ([], reason)."""
    numbers = []
    for raw in values or []:
        number = _name(raw)
        if not number:
            return [], "Room number is required"
        if number in numbers:
            return [], "Room number " + number + " is already used"
        numbers.append(number)
    return numbers, ("" if numbers else "Room number is required")


def _specs(specifications):
    """Known spec fields; text values upper-cased."""
    given = specifications if isinstance(specifications, dict) else {}
    return {key: (value.upper() if isinstance(value, str) else value) for key, value in given.items() if key in SPEC_KEYS}


def _taken(cur, hotel_id, numbers):
    cur.execute("SELECT room_number FROM room WHERE hotel_id = %s AND room_number = ANY(%s) ORDER BY room_number", (hotel_id, numbers))
    return [row["room_number"] for row in cur.fetchall()]


def _insert_rooms(cur, hotel_id, user_id, room_type_id, numbers):
    for number in numbers:
        room_id = new_id("rm")
        cur.execute("INSERT INTO room (room_id, hotel_id, room_type_id, room_number, status) VALUES (%s, %s, %s, %s, 'active')",
                    (room_id, hotel_id, room_type_id, number))
        cur.execute(
            """INSERT INTO room_readiness_status (room_id, hotel_id, readiness_status, changed_by_user_id, auto_dirty_days)
               VALUES (%s, %s, 'dirty', %s, %s)""",
            (room_id, hotel_id, user_id, AUTO_DIRTY_DAYS),
        )


def _type_or_error(cur, hotel_id, room_type):
    found = core_refs.room_type_id(cur, hotel_id, room_type)
    return found, ({} if found else {"error": "Room type " + _name(room_type) + " was not found"})


def _booked(cur, column, values):
    """The room_id / room_type_id values among `values` that booking nights still reference."""
    cur.execute(
        "SELECT DISTINCT " + column + " AS ref FROM booking_room_night WHERE " + column + " = ANY(%s)",
        (values,),
    )
    return {row["ref"] for row in cur.fetchall()}


# ---------- reads ----------

def read_portfolio(hotel_id, room_type=None):
    """{'room_types': [{room_type, room_type_id, status, specifications, description, room_numbers: [...]}]}."""
    wanted = _name(room_type)

    def run(cur):
        cur.execute(
            "SELECT room_type_id, room_type_name, status FROM room_type WHERE hotel_id = %s AND (%s = '' OR room_type_name = %s) ORDER BY room_type_name",
            (hotel_id, wanted, wanted),
        )
        types = cur.fetchall()
        cur.execute(
            """SELECT r.room_id, r.room_type_id, r.room_number, r.status, s.readiness_status,
                      CASE WHEN EXISTS (
                               SELECT 1 FROM booking_room_night n
                               WHERE n.room_id = r.room_id AND n.night_status = 'checked_in'
                                 AND n.stay_date = (now() AT TIME ZONE h.timezone)::date)
                           THEN 'occupied' ELSE s.readiness_status END AS room_status
               FROM room r JOIN hotel h ON h.hotel_id = r.hotel_id
               LEFT JOIN room_readiness_status s ON s.room_id = r.room_id
               WHERE r.hotel_id = %s ORDER BY r.room_number""",
            (hotel_id,),
        )
        return types, cur.fetchall()
    types, rooms = db.transaction(run)
    profiles = {doc.get("room_type_id"): doc for doc in TYPE_DOCS.find({"hotel_id": hotel_id})}
    labels = {doc.get("room_id"): doc.get("room_label") for doc in ROOM_DOCS.find({"hotel_id": hotel_id})}
    listed = []
    for row in types:
        profile = profiles.get(row["room_type_id"]) or {}
        listed.append({
            "room_type": row["room_type_name"], "room_type_id": row["room_type_id"], "status": row["status"],
            "specifications": profile.get("specifications") or {}, "description": profile.get("description") or "",
            "room_numbers": [
                {"room_id": room["room_id"], "room_number": room["room_number"], "room_type": row["room_type_name"],
                 "status": room["status"], "room_status": room["room_status"], "readiness_status": room["readiness_status"],
                 "room_label": labels.get(room["room_id"]) or room["room_number"]}
                for room in rooms if room["room_type_id"] == row["room_type_id"]
            ],
        })
    return {"room_types": listed}


def read_room_type(hotel_id, room_type):
    found = read_portfolio(hotel_id, room_type)["room_types"]
    return found[0] if found else {"error": "Room type " + _name(room_type) + " was not found"}


# ---------- room types ----------

def create_room_type(hotel_id, user_id, zone, room_type, specifications, room_numbers, base_price):
    """Room type, its rooms, HORIZON_DAYS of inventory (total = room count) and prices at base_price, and its spec document."""
    name = _name(room_type)
    numbers, reason = _numbers(room_numbers)
    if not name:
        return {"error": "room_type is required"}
    if reason:
        return {"error": reason}
    try:
        price = float(base_price)
    except (TypeError, ValueError):
        return {"error": "base_price must be a number"}

    def run(cur):
        if core_refs.room_type_id(cur, hotel_id, name):
            return {"error": "Room type " + name + " already exists"}
        used = _taken(cur, hotel_id, numbers)
        if used:
            return {"error": "Room number " + ", ".join(used) + " is already used"}
        type_id = new_id("rt")
        cur.execute("INSERT INTO room_type (room_type_id, hotel_id, room_type_name, status) VALUES (%s, %s, %s, 'active')",
                    (type_id, hotel_id, name))
        _insert_rooms(cur, hotel_id, user_id, type_id, numbers)
        nights = inventory_manager.seed_nights(cur, hotel_id, user_id, type_id, inventory_manager.hotel_today(zone), price)
        key, body = TYPE_DOCS.staged_insert({"hotel_id": hotel_id, "room_type_id": type_id, "specifications": _specs(specifications),
                                             "description": "", "updated_by_user_id": user_id})
        TYPE_DOCS.write_in(cur, key, body)
        return {"success": True, "room_type": name, "room_type_id": type_id, "room_numbers": numbers, "nights": nights}
    return db.transaction(run)


def update_room_type_profile(hotel_id, user_id, room_type, specifications):
    """UPDATE the spec document of a room type: known spec fields and 'description'."""
    given = specifications if isinstance(specifications, dict) else {}
    specs = _specs(given)
    if not specs and "description" not in given:
        return {"error": "No valid updates provided for " + _name(room_type)}

    def run(cur):
        type_id, missing = _type_or_error(cur, hotel_id, room_type)
        if missing:
            return missing
        current = TYPE_DOCS.find_one({"room_type_id": type_id}) or {"hotel_id": hotel_id, "room_type_id": type_id, "specifications": {}}
        current.pop("_id", None)
        body = dict(current, specifications=dict(current.get("specifications") or {}, **specs), updated_by_user_id=user_id)
        body["description"] = str(given["description"]) if "description" in given else str(current.get("description") or "")
        TYPE_DOCS.write_in(cur, TYPE_DOCS.staged_insert(body)[0], body)
        return {"success": True, "room_type": _name(room_type), "specifications": body["specifications"]}
    return db.transaction(run)


def delete_room_type(hotel_id, user_id, room_type):
    """DELETE a room type with its rooms, inventory, prices and documents. Refused while booking nights reference it."""
    def run(cur):
        type_id, missing = _type_or_error(cur, hotel_id, room_type)
        if missing:
            return missing
        cur.execute("SELECT room_id FROM room WHERE room_type_id = %s", (type_id,))
        room_ids = [row["room_id"] for row in cur.fetchall()]
        if _booked(cur, "room_type_id", [type_id]) or _booked(cur, "room_id", room_ids):
            return {"error": "Room type " + _name(room_type) + " has bookings and cannot be deleted"}
        cur.execute("DELETE FROM room WHERE room_type_id = %s", (type_id,))
        cur.execute("DELETE FROM room_type WHERE room_type_id = %s", (type_id,))
        return {"success": True, "room_type": _name(room_type), "room_type_id": type_id, "rooms_deleted": len(room_ids), "room_ids": room_ids}
    result = db.transaction(run)
    if result.get("success"):
        TYPE_DOCS.delete_many({"room_type_id": result["room_type_id"]})
        ROOM_DOCS.delete_many({"room_id": {"$in": result.pop("room_ids")}})
    return result


# ---------- rooms ----------

def add_rooms(hotel_id, user_id, zone, room_type, room_numbers):
    """CREATE rooms on a room type; from today on, its nightly total follows the new room count."""
    numbers, reason = _numbers(room_numbers)
    if reason:
        return {"error": reason}

    def run(cur):
        type_id, missing = _type_or_error(cur, hotel_id, room_type)
        if missing:
            return missing
        used = _taken(cur, hotel_id, numbers)
        if used:
            return {"error": "Room number " + ", ".join(used) + " is already used"}
        _insert_rooms(cur, hotel_id, user_id, type_id, numbers)
        total = inventory_manager.apply_room_count(cur, hotel_id, user_id, type_id, inventory_manager.hotel_today(zone))
        return {"success": True, "room_type": _name(room_type), "room_numbers": numbers, "rooms": total}
    return db.transaction(run)


def remove_rooms(hotel_id, user_id, zone, room_type, room_numbers):
    """DELETE rooms of a room type. Refused while booking nights reference a room."""
    numbers, reason = _numbers(room_numbers)
    if reason:
        return {"error": reason}

    def run(cur):
        type_id, missing = _type_or_error(cur, hotel_id, room_type)
        if missing:
            return missing
        cur.execute("SELECT room_id, room_number FROM room WHERE room_type_id = %s AND room_number = ANY(%s)", (type_id, numbers))
        found = {row["room_number"]: row["room_id"] for row in cur.fetchall()}
        absent = [number for number in numbers if number not in found]
        if absent:
            return {"error": "Room number " + ", ".join(absent) + " is not on " + _name(room_type)}
        booked = _booked(cur, "room_id", list(found.values()))
        if booked:
            names = sorted(number for number, room_id in found.items() if room_id in booked)
            return {"error": "Room " + ", ".join(names) + " has bookings and cannot be removed"}
        cur.execute("DELETE FROM room WHERE room_id = ANY(%s)", (list(found.values()),))
        total = inventory_manager.apply_room_count(cur, hotel_id, user_id, type_id, inventory_manager.hotel_today(zone))
        return {"success": True, "removed": numbers, "rooms": total, "room_ids": list(found.values())}
    result = db.transaction(run)
    if result.get("success"):
        ROOM_DOCS.delete_many({"room_id": {"$in": result.pop("room_ids")}})
    return result


def rename_rooms(hotel_id, user_id, room_type, changes):
    """Change room numbers of a room type. Swaps within the hotel are allowed; the final numbers must be unique."""
    proposed = {}
    for item in changes or []:
        old, new = _name((item or {}).get("room_number")), _name((item or {}).get("new_room_number"))
        if not old or not new:
            return {"error": "Room number is required"}
        if old in proposed:
            return {"error": "Room number " + old + " is already used"}
        proposed[old] = new

    def run(cur):
        type_id, missing = _type_or_error(cur, hotel_id, room_type)
        if missing:
            return missing
        cur.execute("SELECT room_id, room_type_id, room_number FROM room WHERE hotel_id = %s", (hotel_id,))
        rooms = {row["room_number"]: row for row in cur.fetchall()}
        foreign = [old for old in proposed if (rooms.get(old) or {}).get("room_type_id") != type_id]
        if foreign:
            return {"error": "Room " + foreign[0] + " is not on " + _name(room_type)}
        finals = [proposed.get(number, number) for number in rooms]
        repeated = sorted({number for number in finals if finals.count(number) > 1})
        if repeated:
            return {"error": "Room number " + repeated[0] + " is already used"}
        moves = [(rooms[old]["room_id"], new) for old, new in proposed.items() if old != new]
        for index, (room_id, _new) in enumerate(moves):
            cur.execute("UPDATE room SET room_number = %s WHERE room_id = %s", ("~" + str(index + 1), room_id))
        for room_id, new in moves:
            cur.execute("UPDATE room SET room_number = %s WHERE room_id = %s", (new, room_id))
        return {"success": True, "rooms": [new for _room_id, new in moves], "room_ids": [room_id for room_id, _new in moves]}
    result = db.transaction(run)
    if result.get("success"):
        ROOM_DOCS.delete_many({"room_id": {"$in": result.pop("room_ids")}})
    return result


def set_room_labels(hotel_id, user_id, room_type, labels):
    """Display labels of rooms of a room type. A display label (or the room number) cannot repeat in the hotel."""
    def run(cur):
        type_id, missing = _type_or_error(cur, hotel_id, room_type)
        if missing:
            return missing
        cur.execute("SELECT room_id, room_type_id, room_number FROM room WHERE hotel_id = %s", (hotel_id,))
        rooms = {row["room_number"]: row for row in cur.fetchall()}
        stored = {doc.get("room_id"): doc.get("room_label") for doc in ROOM_DOCS.find({"hotel_id": hotel_id})}
        proposed = {}
        for item in labels or []:
            number = _name((item or {}).get("room_number"))
            if not number:
                return {"error": "room_number is required"}
            if (rooms.get(number) or {}).get("room_type_id") != type_id:
                return {"error": "Room " + number + " is not on " + _name(room_type)}
            proposed[rooms[number]["room_id"]] = _name((item or {}).get("room_label")) or number
        shown = [proposed.get(row["room_id"]) or stored.get(row["room_id"]) or number for number, row in rooms.items()]
        repeated = sorted({label for label in shown if shown.count(label) > 1})
        if repeated:
            return {"error": "Room number " + repeated[0] + " is already used"}
        for room_id, label in proposed.items():
            key, body = ROOM_DOCS.staged_insert({"hotel_id": hotel_id, "room_id": room_id, "room_label": label, "updated_by_user_id": user_id})
            ROOM_DOCS.write_in(cur, key, body)
        by_id = {row["room_id"]: number for number, row in rooms.items()}
        return {"success": True, "rooms": [by_id[room_id] for room_id in proposed]}
    return db.transaction(run)


# ---------- rate plans (documents of the hotel) ----------

def _rate_key(hotel_id, rate_plan):
    return "hotel_id=" + hotel_id + "|rate_plan=" + rate_plan


def read_rate_plans(hotel_id):
    return [{"rate_plan": doc.get("rate_plan"), "description": doc.get("description") or ""}
            for doc in RATE_DOCS.find({"hotel_id": hotel_id}).sort("rate_plan")]


def save_rate_plan(hotel_id, user_id, rate_plan, description):
    """CREATE or UPDATE a rate plan document."""
    plan = _name(rate_plan)
    if not plan:
        return {"error": "rate_plan is required"}
    body = {"hotel_id": hotel_id, "rate_plan": plan, "description": str(description or "").strip(), "updated_by_user_id": user_id}
    db.transaction(lambda cur: RATE_DOCS.write_in(cur, _rate_key(hotel_id, plan), body))
    return {"rate_plan": plan, "description": body["description"]}


def rename_rate_plan(hotel_id, user_id, rate_plan, new_rate_plan, description):
    """Rename or rewrite a rate plan; bookings on the old name follow the new one."""
    plan, new_plan = _name(rate_plan), _name(new_rate_plan)
    if not plan or not new_plan:
        return {"error": "rate_plan is required"}
    if not RATE_DOCS.find_one({"hotel_id": hotel_id, "rate_plan": plan}):
        return {"error": "Rate plan not found", "status": 404}
    if new_plan != plan and RATE_DOCS.find_one({"hotel_id": hotel_id, "rate_plan": new_plan}):
        return {"error": "Rate plan already exists"}
    body = {"hotel_id": hotel_id, "rate_plan": new_plan, "description": str(description or "").strip(), "updated_by_user_id": user_id}

    def run(cur):
        RATE_DOCS.write_in(cur, _rate_key(hotel_id, new_plan), body)
        cur.execute("UPDATE booking SET rate_plan = %s, updated_at = now(), updated_by_user_id = %s WHERE hotel_id = %s AND rate_plan = %s",
                    (new_plan, user_id, hotel_id, plan))
        return cur.rowcount
    moved = db.transaction(run)
    if new_plan != plan:
        RATE_DOCS.delete_one({"hotel_id": hotel_id, "rate_plan": plan})
    return {"rate_plan": new_plan, "description": body["description"], "bookings_updated": moved}


def delete_rate_plan(hotel_id, user_id, rate_plan):
    """DELETE a rate plan; bookings that used it are cleared."""
    plan = _name(rate_plan)
    if not plan:
        return {"error": "rate_plan is required"}
    if not RATE_DOCS.find_one({"hotel_id": hotel_id, "rate_plan": plan}):
        return {"error": "Rate plan not found", "status": 404}

    def run(cur):
        cur.execute("UPDATE booking SET rate_plan = NULL, updated_at = now(), updated_by_user_id = %s WHERE hotel_id = %s AND rate_plan = %s",
                    (user_id, hotel_id, plan))
        return cur.rowcount
    cleared = db.transaction(run)
    RATE_DOCS.delete_one({"hotel_id": hotel_id, "rate_plan": plan})
    return {"rate_plan": plan, "removed": True, "bookings_cleared": cleared}


# `from booking_system.room_manager import room_manager` keeps working for callers of the old instance.
room_manager = sys.modules[__name__]
