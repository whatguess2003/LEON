"""Hotel partnerships (hotel_partnership: this hotel sends guests to a partner hotel) and the guests it referred (guest_referral).
Each writer reads first; a refusal returns {'error': reason}."""

import sys

from booking_system import db
from booking_system.guest_manager import Refused, _guest_id_by_phone, run
from booking_system.hotel_registry import new_id


def _partner_id(cur, partner_hotel_name):
    cur.execute("SELECT hotel_id FROM hotel WHERE hotel_name = %s", (str(partner_hotel_name or "").strip().upper(),))
    found = cur.fetchone()
    if not found:
        raise Refused("Hotel " + str(partner_hotel_name or "") + " not found")
    return found["hotel_id"]


def partnerships(hotel_id):
    """This hotel's partnerships with the partner's name and how many guests it referred."""
    rows = db.rows(
        """SELECT p.partnership_id, h.hotel_name AS partner_hotel, p.status, p.notes, p.created_at,
                  (SELECT COUNT(*) FROM guest_referral r WHERE r.partnership_id = p.partnership_id) AS referrals
           FROM hotel_partnership p JOIN hotel h ON h.hotel_id = p.partner_hotel_id WHERE p.hotel_id = %s ORDER BY h.hotel_name""",
        (hotel_id,))
    return [dict(row, created_at=row["created_at"].isoformat()) for row in rows]


def save_partnership(hotel_id, user_id, partner_hotel_name, status="active", notes=None):
    """READ the partnership with that hotel; update its status and notes or create it."""
    if status not in ("active", "ended"):
        return {"error": "status must be active or ended"}

    def write(cur):
        partner_id = _partner_id(cur, partner_hotel_name)
        if partner_id == hotel_id:
            raise Refused("A hotel cannot partner with itself")
        cur.execute("SELECT partnership_id FROM hotel_partnership WHERE hotel_id = %s AND partner_hotel_id = %s FOR UPDATE", (hotel_id, partner_id))
        found = cur.fetchone()
        if found:
            cur.execute("UPDATE hotel_partnership SET status = %s, notes = %s WHERE partnership_id = %s", (status, notes, found["partnership_id"]))
            return {"partnership_id": found["partnership_id"], "result": "updated"}
        partnership_id = new_id("hpt")
        cur.execute(
            """INSERT INTO hotel_partnership (partnership_id, hotel_id, partner_hotel_id, status, notes, created_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (partnership_id, hotel_id, partner_id, status, notes, user_id))
        return {"partnership_id": partnership_id, "result": "created"}
    return run(write)


def refer_guest(hotel_id, user_id, guest_phone, partner_hotel_name, reason=None):
    """Record that this hotel sent its guest to an active partner."""
    def write(cur):
        guest_id = _guest_id_by_phone(cur, hotel_id, guest_phone)
        if not guest_id:
            raise Refused("Guest profile not found")
        cur.execute("SELECT partnership_id FROM hotel_partnership WHERE hotel_id = %s AND partner_hotel_id = %s AND status = 'active'",
                    (hotel_id, _partner_id(cur, partner_hotel_name)))
        found = cur.fetchone()
        if not found:
            raise Refused("No active partnership with " + str(partner_hotel_name or ""))
        referral_id = new_id("grf")
        cur.execute(
            """INSERT INTO guest_referral (referral_id, hotel_id, partnership_id, guest_id, reason, referred_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (referral_id, hotel_id, found["partnership_id"], guest_id, reason, user_id))
        return {"referral_id": referral_id}
    return run(write)


referral_agent = sys.modules[__name__]
