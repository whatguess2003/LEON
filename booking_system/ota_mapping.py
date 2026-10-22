"""Hotel and room mapping for the channel manager."""

from datetime import datetime, timedelta


def _link_collection():
    from booking_system.front_desk_manager import intelligent_front_desk
    return intelligent_front_desk.get_mongodb_connection().get_collection("ota_link")


def _hotel_key(hotel_name: str) -> str:
    return str(hotel_name or "").strip().upper()


def _keep_one_case(coll, hotel: str):
    for doc in list(coll.find({}, {"_id": 1, "hotel_name": 1})):
        stored = str((doc or {}).get("hotel_name") or "").strip()
        if stored and stored != hotel and _hotel_key(stored) == hotel:
            coll.delete_one({"_id": doc["_id"]})


def resolve_hotel(hotel_name: str) -> tuple:
    """Use the named hotel, or the one linked hotel when the name is empty."""
    name = _hotel_key(hotel_name)
    if name:
        return name, ""
    names = []
    for doc in _link_collection().find({}, {"_id": 0, "hotel_name": 1}):
        hotel = _hotel_key((doc or {}).get("hotel_name"))
        if hotel and hotel not in names:
            names.append(hotel)
    if len(names) == 1:
        return names[0], ""
    if not names:
        return "", "Hotel is not linked"
    return "", "Hotel name is required"


def _room_types(hotel: str) -> list:
    from booking_system.front_desk_manager import intelligent_front_desk
    col = intelligent_front_desk.get_mongodb_connection().get_collection("room_encyclopedia")
    stored = _hotel_key(hotel)
    found = []
    for doc in col.find({"hotel_name": stored, "has_room_number": False}, {"_id": 0, "room_type": 1}):
        room_type = str((doc or {}).get("room_type") or "").strip()
        if room_type and room_type not in found:
            found.append(room_type)
    return found


def _payload(hotel: str, hotel_code: str, property_body: dict, room_types: list, room_maps: list) -> dict:
    body = property_body or {}
    channels = body.get("connected_channels")
    rooms = body.get("rooms")
    return {
        "hotel_name": hotel,
        "hotel_code": hotel_code,
        "property_name": str(body.get("hotel_name") or ""),
        "currency": str(body.get("currency") or ""),
        "timezone": str(body.get("timezone") or ""),
        "channels": channels if isinstance(channels, list) else [],
        "rooms": rooms if isinstance(rooms, list) else [],
        "room_types": room_types or [],
        "room_maps": room_maps or [],
    }


def read_mapping(hotel_name: str) -> dict:
    hotel, error = resolve_hotel(hotel_name)
    if error:
        return {"error": error}
    current = _link_collection().find_one({"hotel_name": hotel}, {"_id": 0})
    hotel_code = str((current or {}).get("hotel_code") or "").strip()
    room_types = _room_types(hotel)
    if not hotel_code:
        return {"hotel_name": hotel, "hotel_code": "", "channels": [], "rooms": [], "room_types": room_types, "room_maps": []}
    from aiosell_channel_manager import read_property_details, read_room_maps
    details = read_property_details(hotel_code)
    room_maps = read_room_maps(hotel, hotel_code)
    if details.get("error"):
        return {
            "error": details["error"],
            "hotel_name": hotel,
            "hotel_code": hotel_code,
            "channels": [],
            "rooms": [],
            "room_types": room_types,
            "room_maps": room_maps,
            "status": 502,
        }
    return _payload(hotel, hotel_code, details.get("property"), room_types, room_maps)


def save_mapping(hotel_name: str, hotel_code: str) -> dict:
    hotel, error = resolve_hotel(hotel_name)
    if error:
        return {"error": error}
    code = str(hotel_code or "").strip()
    if not code:
        return {"error": "Hotel code is required"}
    coll = _link_collection()
    current = coll.find_one({"hotel_name": hotel})
    fields = {"hotel_name": hotel, "hotel_code": code, "updated_by": "CHANNEL"}
    if current:
        coll.update_one({"hotel_name": hotel}, {"$set": fields, "$unset": {"partner_id": "", "aiosell_username": "", "aiosell_password": ""}})
    else:
        coll.insert_one(fields)
    _keep_one_case(coll, hotel)
    from aiosell_channel_manager import read_property_details, read_room_maps
    details = read_property_details(code)
    room_types = _room_types(hotel)
    room_maps = read_room_maps(hotel, code)
    if details.get("error"):
        return {
            "error": details["error"],
            "hotel_name": hotel,
            "hotel_code": code,
            "channels": [],
            "rooms": [],
            "room_types": room_types,
            "room_maps": room_maps,
            "status": 502,
        }
    return _payload(hotel, code, details.get("property"), room_types, room_maps)


def save_room_mapping(hotel_name: str, room_code: str, room_type: str) -> dict:
    hotel, error = resolve_hotel(hotel_name)
    if error:
        return {"error": error}
    code = str(room_code or "").strip()
    chosen = str(room_type or "").strip().upper()
    if not code:
        return {"error": "Room code is required"}
    current = _link_collection().find_one({"hotel_name": hotel}, {"_id": 0, "hotel_code": 1})
    hotel_code = str((current or {}).get("hotel_code") or "").strip()
    if not hotel_code:
        return {"error": "Hotel code is not linked"}
    from aiosell_channel_manager import push_dates, read_property_details, read_room_maps
    from booking_system.aiosell_reservation import _ensure_mapping_table
    from booking_system.booking_manager import booking_manager
    details = read_property_details(hotel_code)
    if details.get("error"):
        return {"error": details["error"], "status": 502}
    codes = []
    for room in (details.get("property") or {}).get("rooms") or []:
        room_id = str((room or {}).get("room_id") or "").strip()
        if room_id:
            codes.append(room_id)
    if code not in codes:
        return {"error": "Room code " + code + " is not on this property", "room_codes": codes}
    room_types = _room_types(hotel)
    if chosen and chosen not in room_types:
        return {"error": "Room type " + chosen + " was not found", "room_types": room_types}
    with booking_manager.postgres_lock:
        booking_manager._clear_transaction()
        cur = booking_manager.postgres_conn.cursor()
        try:
            _ensure_mapping_table(cur)
            cur.execute(
                """
                SELECT room_type FROM hotel_room_code_mapping
                WHERE hotel_code = %s AND room_code = %s AND hotel_name = %s
                """,
                (hotel_code, code, hotel),
            )
            existing = cur.fetchone()
            if not chosen:
                if not existing:
                    booking_manager.postgres_conn.rollback()
                    return {"error": "Room code is not mapped", "room_types": room_types}
                cur.execute(
                    "DELETE FROM hotel_room_code_mapping WHERE hotel_code = %s AND room_code = %s AND hotel_name = %s",
                    (hotel_code, code, hotel),
                )
            elif existing:
                cur.execute(
                    """
                    UPDATE hotel_room_code_mapping SET room_type = %s
                    WHERE hotel_code = %s AND room_code = %s AND hotel_name = %s
                    """,
                    (chosen, hotel_code, code, hotel),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO hotel_room_code_mapping (hotel_code, room_code, hotel_name, room_type)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (hotel_code, code, hotel, chosen),
                )
            booking_manager.postgres_conn.commit()
        finally:
            cur.close()
    saved = _payload(hotel, hotel_code, details.get("property"), room_types, read_room_maps(hotel, hotel_code))
    start = datetime.now().date()
    saved["aiosell"] = push_dates(hotel, [(start + timedelta(days=offset)).isoformat() for offset in range(31)])
    return saved
