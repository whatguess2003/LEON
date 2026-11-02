"""Natural attribute -> id lookups inside a caller's transaction (RealDictCursor). Names and numbers are matched in their stored form;
a miss returns None and the caller reports it."""


def room_type_id(cur, hotel_id, room_type_name):
    cur.execute("SELECT room_type_id FROM room_type WHERE hotel_id = %s AND room_type_name = %s",
                (hotel_id, str(room_type_name or "").strip().upper()))
    found = cur.fetchone()
    return found["room_type_id"] if found else None


def room(cur, hotel_id, room_number):
    """{'room_id', 'room_type_id', 'room_type_name', 'status'} for a room number, or None."""
    cur.execute(
        """SELECT r.room_id, r.room_type_id, t.room_type_name, r.status FROM room r JOIN room_type t ON t.room_type_id = r.room_type_id
           WHERE r.hotel_id = %s AND r.room_number = %s""",
        (hotel_id, str(room_number or "").strip().upper()),
    )
    return cur.fetchone()


def guest_by_identity(cur, hotel_id, identity_type, identity_value):
    """{'guest_id', 'guest_name'} for a guest phone/email at this hotel, or None. Phones are E.164, emails lower-case."""
    cur.execute(
        """SELECT g.guest_id, g.guest_name FROM guest_identity i JOIN guest g ON g.guest_id = i.guest_id
           WHERE i.hotel_id = %s AND i.identity_type = %s AND i.identity_value = %s""",
        (hotel_id, identity_type, identity_value),
    )
    return cur.fetchone()


def booking(cur, hotel_id, booking_id):
    """The booking_summary row for a booking of this hotel, or None."""
    cur.execute("SELECT * FROM booking_summary WHERE hotel_id = %s AND booking_id = %s", (hotel_id, booking_id))
    return cur.fetchone()
