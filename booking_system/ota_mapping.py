"""Hotel and room mapping for the channel manager. Callers send the hotel_id; room codes map to room_type_id in
hotel_room_code_mapping."""

from datetime import timedelta

from booking_system import db, hotel_registry


def resolve_hotel(hotel_id: str) -> tuple:
    """(hotel row, '') for the hotel_id; ({}, reason) otherwise."""
    if not str(hotel_id or "").strip():
        return {}, "hotel_id is required"
    found = hotel_registry.read_hotel_by_id(str(hotel_id).strip())
    return (found, "") if found else ({}, "Hotel " + str(hotel_id) + " is not registered")


def _room_types(hotel_id: str) -> list:
    rows = db.rows("SELECT room_type_name FROM room_type WHERE hotel_id = %s AND status = 'active' ORDER BY room_type_name", (hotel_id,))
    return [row["room_type_name"] for row in rows]


def _payload(hotel: dict, hotel_code: str, property_body: dict, room_maps: list) -> dict:
    body = property_body or {}
    channels = body.get("connected_channels")
    rooms = body.get("rooms")
    return {
        "hotel_id": hotel["hotel_id"],
        "hotel_name": hotel["hotel_name"],
        "hotel_code": hotel_code,
        "property_name": str(body.get("hotel_name") or ""),
        "currency": str(body.get("currency") or ""),
        "timezone": str(body.get("timezone") or ""),
        "channels": channels if isinstance(channels, list) else [],
        "rooms": rooms if isinstance(rooms, list) else [],
        "room_types": _room_types(hotel["hotel_id"]),
        "room_maps": room_maps or [],
    }


def _linked(hotel: dict, hotel_code: str) -> dict:
    from aiosell_channel_manager import read_property_details, read_room_maps
    details = read_property_details(hotel_code)
    room_maps = read_room_maps(hotel["hotel_id"])
    if details.get("error"):
        return dict(_payload(hotel, hotel_code, {}, room_maps), error=details["error"], status=502)
    return _payload(hotel, hotel_code, details.get("property"), room_maps)


def read_mapping(hotel_id: str) -> dict:
    hotel, error = resolve_hotel(hotel_id)
    if error:
        return {"error": error}
    hotel_code = str(hotel.get("channel_hotel_code") or "")
    if not hotel_code:
        return _payload(hotel, "", {}, [])
    return _linked(hotel, hotel_code)


def save_mapping(hotel_id: str, hotel_code: str) -> dict:
    hotel, error = resolve_hotel(hotel_id)
    if error:
        return {"error": error}
    code = str(hotel_code or "").strip()
    if not code:
        return {"error": "Hotel code is required"}
    reason = hotel_registry.write_channel_code(hotel["hotel_id"], code)
    if reason:
        return {"error": reason}
    return _linked(hotel, code)


def save_room_mapping(hotel_id: str, room_code: str, room_type: str) -> dict:
    """Map a channel room code to a room type; an empty room type removes the mapping. Then push 31 days."""
    hotel, error = resolve_hotel(hotel_id)
    if error:
        return {"error": error}
    code = str(room_code or "").strip()
    chosen = str(room_type or "").strip().upper()
    if not code:
        return {"error": "Room code is required"}
    hotel_code = str(hotel.get("channel_hotel_code") or "")
    if not hotel_code:
        return {"error": "Hotel code is not linked"}
    from aiosell_channel_manager import push_dates, read_property_details
    from booking_system import core_refs
    from booking_system.inventory_manager import hotel_today
    details = read_property_details(hotel_code)
    if details.get("error"):
        return {"error": details["error"], "status": 502}
    codes = [str((room or {}).get("room_id") or "").strip() for room in (details.get("property") or {}).get("rooms") or []]
    codes = [item for item in codes if item]
    if code not in codes:
        return {"error": "Room code " + code + " is not on this property", "room_codes": codes}

    def run(cur):
        type_id = core_refs.room_type_id(cur, hotel_id, chosen) if chosen else None
        if chosen and not type_id:
            return {"error": "Room type " + chosen + " was not found", "room_types": _room_types(hotel_id)}
        cur.execute("SELECT room_type_id FROM hotel_room_code_mapping WHERE hotel_id = %s AND channel_room_code = %s", (hotel_id, code))
        existing = cur.fetchone()
        if not chosen and not existing:
            return {"error": "Room code is not mapped", "room_types": _room_types(hotel_id)}
        cur.execute("DELETE FROM hotel_room_code_mapping WHERE hotel_id = %s AND channel_room_code = %s", (hotel_id, code))
        if chosen:
            cur.execute("INSERT INTO hotel_room_code_mapping (hotel_id, channel_room_code, room_type_id) VALUES (%s, %s, %s)",
                        (hotel_id, code, type_id))
        return {}
    refused = db.transaction(run)
    if refused:
        return refused
    saved = _linked(hotel, hotel_code)
    start = hotel_today(hotel["timezone"])
    saved["aiosell"] = push_dates(hotel_id, [(start + timedelta(days=offset)).isoformat() for offset in range(31)])
    return saved
