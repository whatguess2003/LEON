"""Channel manager reservation list.

The list lives in Postgres. A failed Aiosell poll records the error and leaves the rows in place.
"""

from datetime import datetime, timedelta

from booking_system.booking_manager import booking_manager


def ensure_tables():
    booking_manager._ensure_postgres_connection()
    with booking_manager.postgres_lock:
        booking_manager._clear_transaction()
        cur = booking_manager.postgres_conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_reservations (
                    hotel_name VARCHAR(255) NOT NULL,
                    booking_id VARCHAR(255) NOT NULL,
                    source VARCHAR(32) NOT NULL,
                    direction VARCHAR(32) NOT NULL,
                    action VARCHAR(32) NOT NULL,
                    guest_phone VARCHAR(64) NULL,
                    room_type VARCHAR(255) NULL,
                    checkin DATE NULL,
                    checkout DATE NULL,
                    booking_status VARCHAR(32) NOT NULL,
                    message TEXT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (hotel_name, booking_id)
                )
                """
            )
            cur.execute("ALTER TABLE channel_reservations ADD COLUMN IF NOT EXISTS direction VARCHAR(32)")
            cur.execute("ALTER TABLE channel_reservations ADD COLUMN IF NOT EXISTS guest_name VARCHAR(255)")
            cur.execute(
                """
                UPDATE channel_reservations
                SET direction = 'reservation_in'
                WHERE source = 'PMS' AND COALESCE(direction, '') = ''
                """
            )
            cur.execute(
                """
                UPDATE channel_reservations
                SET direction = 'reservation_out'
                WHERE COALESCE(direction, '') = ''
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_reservation_sync (
                    hotel_name VARCHAR(255) PRIMARY KEY,
                    hotel_code VARCHAR(64) NULL,
                    start_date DATE NULL,
                    end_date DATE NULL,
                    synced_at TIMESTAMP NULL,
                    last_error TEXT NULL
                )
                """
            )
            booking_manager.postgres_conn.commit()
        finally:
            cur.close()


def _day(value):
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _span(stay_dates):
    days = []
    for value in stay_dates or []:
        day = _day(value)
        if day and day not in days:
            days.append(day)
    if not days:
        return None, None
    days.sort()
    return days[0], days[-1]


def store_reservation(hotel_name: str, booking_id: str, source: str, direction: str, action: str, guest_phone: str, guest_name: str, room_type: str, checkin, checkout, booking_status: str, message: str) -> dict:
    """Insert the reservation, or update it when hotel_name and booking_id already exist."""
    ensure_tables()
    with booking_manager.postgres_lock:
        booking_manager._clear_transaction()
        cur = booking_manager.postgres_conn.cursor()
        try:
            cur.execute(
                "SELECT booking_id FROM channel_reservations WHERE hotel_name = %s AND booking_id = %s",
                (hotel_name, booking_id),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    """
                    UPDATE channel_reservations
                    SET source = %s, direction = %s, action = %s, guest_phone = %s, guest_name = %s, room_type = %s,
                        checkin = %s, checkout = %s, booking_status = %s, message = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE hotel_name = %s AND booking_id = %s
                    """,
                    (source, direction, action, guest_phone, guest_name, room_type, checkin, checkout, booking_status, message, hotel_name, booking_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO channel_reservations (
                        hotel_name, booking_id, source, direction, action, guest_phone, guest_name, room_type,
                        checkin, checkout, booking_status, message
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (hotel_name, booking_id, source, direction, action, guest_phone, guest_name, room_type, checkin, checkout, booking_status, message),
                )
            booking_manager.postgres_conn.commit()
        finally:
            cur.close()
    return read_reservation(hotel_name, booking_id)


def read_reservation(hotel_name: str, booking_id: str) -> dict:
    ensure_tables()
    with booking_manager.postgres_lock:
        booking_manager._clear_transaction()
        cur = booking_manager.postgres_conn.cursor()
        try:
            cur.execute(
                """
                SELECT source, direction, action, guest_phone, guest_name, room_type, checkin, checkout, booking_status, message
                FROM channel_reservations
                WHERE hotel_name = %s AND booking_id = %s
                """,
                (hotel_name, booking_id),
            )
            row = cur.fetchone()
        finally:
            cur.close()
            booking_manager._safe_rollback()
    if not row:
        return {}
    return {
        "hotel_name": hotel_name,
        "booking_id": booking_id,
        "source": row[0],
        "direction": row[1],
        "action": row[2],
        "guest_phone": row[3] or "",
        "guest_name": row[4] or "",
        "room_type": row[5] or "",
        "checkin": row[6].isoformat() if row[6] else "",
        "checkout": row[7].isoformat() if row[7] else "",
        "booking_status": row[8],
        "message": row[9] or "",
    }


def record_sync(hotel_name: str, hotel_code: str, start_date, end_date, error: str):
    """Record the poll. Does not change reservation rows."""
    ensure_tables()
    with booking_manager.postgres_lock:
        booking_manager._clear_transaction()
        cur = booking_manager.postgres_conn.cursor()
        try:
            cur.execute(
                "SELECT hotel_name FROM channel_reservation_sync WHERE hotel_name = %s",
                (hotel_name,),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    """
                    UPDATE channel_reservation_sync
                    SET hotel_code = %s, start_date = %s, end_date = %s, synced_at = CURRENT_TIMESTAMP, last_error = %s
                    WHERE hotel_name = %s
                    """,
                    (hotel_code, start_date, end_date, error, hotel_name),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO channel_reservation_sync (hotel_name, hotel_code, start_date, end_date, synced_at, last_error)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                    """,
                    (hotel_name, hotel_code, start_date, end_date, error),
                )
            booking_manager.postgres_conn.commit()
        finally:
            cur.close()


def read_list(hotel_name: str, direction: str) -> dict:
    """Return the stored reservations for one direction and the last Aiosell poll status."""
    ensure_tables()
    with booking_manager.postgres_lock:
        booking_manager._clear_transaction()
        cur = booking_manager.postgres_conn.cursor()
        try:
            cur.execute(
                """
                SELECT hotel_code, start_date, end_date, synced_at, last_error
                FROM channel_reservation_sync WHERE hotel_name = %s
                """,
                (hotel_name,),
            )
            sync = cur.fetchone()
            cur.execute(
                """
                SELECT booking_id, source, action, booking_status, message, updated_at, guest_name
                FROM channel_reservations
                WHERE hotel_name = %s AND direction = %s
                ORDER BY updated_at DESC, booking_id
                """,
                (hotel_name, direction),
            )
            rows = cur.fetchall()
        finally:
            cur.close()
            booking_manager._safe_rollback()
    reservations = []
    latest = None
    for row in rows:
        reservations.append({
            "bookingId": row[0],
            "source": row[1],
            "action": row[2],
            "success": row[3] != "rejected",
            "message": row[4] or "",
            "guest_name": row[6] or "",
            "direction": direction,
        })
        if latest is None or (row[5] and row[5] > latest):
            latest = row[5]
    if direction == "reservation_in":
        return {
            "hotel_name": hotel_name,
            "direction": "reservation_in",
            "endpoint": "/reservation-in",
            "synced_at": latest.isoformat(timespec="seconds") if latest else "",
            "reservations": reservations,
        }
    if not sync:
        return {
            "hotel_name": hotel_name,
            "hotel_code": "",
            "direction": "reservation_out",
            "endpoint": "/update_reservation",
            "start_date": "",
            "end_date": "",
            "synced_at": "",
            "error": "",
            "reservations": reservations,
        }
    return {
        "hotel_name": hotel_name,
        "hotel_code": sync[0] or "",
        "direction": "reservation_out",
        "endpoint": "/update_reservation",
        "start_date": sync[1].isoformat() if sync[1] else "",
        "end_date": sync[2].isoformat() if sync[2] else "",
        "synced_at": sync[3].isoformat(timespec="seconds") if sync[3] else "",
        "error": sync[4] or "",
        "reservations": reservations,
    }


def accept_pms_booking(body: dict) -> dict:
    """Store one booking the PMS created. The channel manager list is the record."""
    hotel_name = str((body or {}).get("hotel_name") or "").strip().upper()
    booking_id = str((body or {}).get("booking_id") or "").strip()
    if not hotel_name or not booking_id:
        return {"error": "hotel_name and booking_id are required"}
    stay_dates = (body or {}).get("stay_dates") or []
    checkin, checkout = _span(stay_dates)
    guest_phone = str((body or {}).get("guest_phone") or "").strip()
    guest_name = str((body or {}).get("guest_name") or "").strip()
    room_type = str((body or {}).get("room_type") or "").strip()
    status = str((body or {}).get("booking_status") or "").strip() or "confirmed"
    stored = store_reservation(
        hotel_name,
        booking_id,
        "PMS",
        "reservation_in",
        "book",
        guest_phone,
        guest_name,
        room_type,
        checkin,
        checkout,
        status,
        "Reservation in",
    )
    return {"success": True, "reservation": stored}


def record_aiosell_reservation(hotel_name: str, payload: dict, outcome: dict) -> None:
    """Write one Aiosell reservation onto the reservation-out list."""
    from booking_system.aiosell_reservation import _guest_name, _guest_phone, _stay_day

    booking_id = str((payload or {}).get("bookingId") or "").strip()
    hotel = str(hotel_name or "").strip().upper()
    if not hotel or not booking_id:
        return
    action = str((payload or {}).get("action") or "").strip().lower()
    status = "cancelled" if action == "cancel" else ("confirmed" if outcome.get("success") else "rejected")
    phone = _guest_phone(payload)
    guest_name = _guest_name(payload)
    room_type = ""
    if outcome.get("success"):
        with booking_manager.postgres_lock:
            booking_manager._clear_transaction()
            cur = booking_manager.postgres_conn.cursor()
            try:
                cur.execute(
                    "SELECT room_type FROM room_bookings WHERE hotel_name = %s AND booking_id = %s",
                    (hotel, booking_id),
                )
                found = cur.fetchone()
                room_type = str(found[0] or "") if found else ""
            finally:
                cur.close()
                booking_manager._safe_rollback()
    store_reservation(
        hotel,
        booking_id,
        "AIOSSELL",
        "reservation_out",
        action or "book",
        phone,
        guest_name,
        room_type,
        _day(_stay_day((payload or {}).get("checkin"))),
        _day(_stay_day((payload or {}).get("checkout"))),
        status,
        str(outcome.get("message") or ""),
    )


def accept_webhook(payload: dict) -> dict:
    """Apply one reservation Aiosell posts and store it. The caller answers Aiosell before any inventory push."""
    from booking_system.aiosell_reservation import handle_reservation, _hotel_name

    body = payload if isinstance(payload, dict) else {}
    outcome = handle_reservation(body, push=False)
    public = {"success": bool(outcome.get("success")), "message": str(outcome.get("message") or "")}
    booking_manager._ensure_postgres_connection()
    db = booking_manager.mongo_db
    hotel_name = ""
    if db is not None:
        hotel_name, _error = _hotel_name(db, str(body.get("hotelCode") or "").strip())
    if hotel_name:
        record_aiosell_reservation(hotel_name, body, outcome)
        public["hotel_name"] = hotel_name
        public["stay_dates"] = list(outcome.get("stay_dates") or [])
    return public


def sync_hotel(hotel_name: str) -> dict:
    """Pull Aiosell into the stored list. A failed pull keeps the rows already stored."""
    from aiosell_channel_manager import fetch_reservations, hotel_code_for
    from booking_system.aiosell_reservation import handle_reservation, _push_stay_dates

    hotel_code = hotel_code_for(hotel_name)
    today = datetime.now().date()
    start = today - timedelta(days=30)
    end = today + timedelta(days=30)
    if not hotel_code:
        record_sync(hotel_name, "", start, end, "Hotel code is not linked")
        listed = read_list(hotel_name, "reservation_out")
        listed["error"] = "Hotel code is not linked"
        return listed
    fetched = fetch_reservations(hotel_code, start.isoformat(), end.isoformat())
    if fetched.get("error"):
        record_sync(hotel_name, hotel_code, start, end, str(fetched.get("error") or ""))
        listed = read_list(hotel_name, "reservation_out")
        return listed
    incoming = [item for item in fetched.get("reservations") or [] if isinstance(item, dict)]
    incoming.sort(key=lambda item: str(item.get("bookedOn") or ""))
    stay_dates = []
    for item in incoming:
        outcome = handle_reservation(item, push=False)
        record_aiosell_reservation(hotel_name, item, outcome)
        for day in outcome.get("stay_dates") or []:
            text = str(day)[:10]
            if text and text not in stay_dates:
                stay_dates.append(text)
    if stay_dates:
        _push_stay_dates(hotel_name, stay_dates)
    record_sync(hotel_name, hotel_code, start, end, "")
    return read_list(hotel_name, "reservation_out")


def sync_linked_hotels() -> dict:
    from booking_system.front_desk_manager import intelligent_front_desk
    col = intelligent_front_desk.get_mongodb_connection().get_collection("ota_link")
    hotels = []
    for doc in col.find({}, {"_id": 0, "hotel_name": 1, "hotel_code": 1}):
        hotel = str((doc or {}).get("hotel_name") or "").strip().upper()
        code = str((doc or {}).get("hotel_code") or "").strip()
        if hotel and code and hotel not in [item.get("hotel_name") for item in hotels]:
            hotels.append(sync_hotel(hotel))
    return {"hotels": hotels}
