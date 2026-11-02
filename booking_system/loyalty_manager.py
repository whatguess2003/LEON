"""Loyalty on loyalty_tier, loyalty_member, loyalty_reward, loyalty_points (a signed ledger) and guest_occasion. A member's balance
is the sum of their ledger; their tier is the highest tier whose min_points the balance reaches. Each writer is one transaction that
reads first; a refusal returns {'error': reason}."""

import sys
from datetime import date

from booking_system import db
from booking_system.guest_manager import Refused, _guest_id_by_phone, run
from booking_system.hotel_registry import new_id


def _guest_id(cur, hotel_id, guest_phone):
    guest_id = _guest_id_by_phone(cur, hotel_id, guest_phone)
    if not guest_id:
        raise Refused("Guest profile not found")
    return guest_id


def _member(cur, hotel_id, guest_phone, lock=False):
    """The active member row of the guest with this phone, with balance and tier."""
    guest_id = _guest_id(cur, hotel_id, guest_phone)
    cur.execute("SELECT member_id, status FROM loyalty_member WHERE hotel_id = %s AND guest_id = %s" + (" FOR UPDATE" if lock else ""),
                (hotel_id, guest_id))
    member = cur.fetchone()
    if not member or member["status"] != "active":
        raise Refused("Guest is not a loyalty member")
    cur.execute(
        """SELECT COALESCE(SUM(points), 0) AS balance,
                  (SELECT tier_name FROM loyalty_tier WHERE hotel_id = %s AND min_points <= COALESCE(SUM(p.points), 0)
                   ORDER BY min_points DESC LIMIT 1) AS tier_name
           FROM loyalty_points p WHERE p.member_id = %s""",
        (hotel_id, member["member_id"]))
    found = cur.fetchone()
    return {"member_id": member["member_id"], "guest_id": guest_id, "balance": int(found["balance"]), "tier_name": found["tier_name"]}


def tiers(hotel_id):
    return db.rows("SELECT tier_name, min_points FROM loyalty_tier WHERE hotel_id = %s ORDER BY min_points", (hotel_id,))


def save_tier(hotel_id, tier_name, min_points):
    """READ the tier; update its threshold or create it."""
    name = str(tier_name or "").strip().upper()
    if not name or int(min_points) < 0:
        return {"error": "tier_name and min_points >= 0 are required"}

    def write(cur):
        cur.execute("SELECT min_points FROM loyalty_tier WHERE hotel_id = %s AND tier_name = %s FOR UPDATE", (hotel_id, name))
        if cur.fetchone():
            cur.execute("UPDATE loyalty_tier SET min_points = %s WHERE hotel_id = %s AND tier_name = %s", (int(min_points), hotel_id, name))
            return {"tier_name": name, "min_points": int(min_points), "result": "updated"}
        cur.execute("INSERT INTO loyalty_tier (hotel_id, tier_name, min_points) VALUES (%s, %s, %s)", (hotel_id, name, int(min_points)))
        return {"tier_name": name, "min_points": int(min_points), "result": "created"}
    return run(write)


def member_status(hotel_id, guest_phone):
    """{'member_id', 'balance', 'tier_name', 'history'} of the member with this phone."""
    def read(cur):
        member = _member(cur, hotel_id, guest_phone)
        cur.execute(
            """SELECT p.entry_id, p.points, p.reason, p.booking_id, r.reward_name, p.recorded_at FROM loyalty_points p
               LEFT JOIN loyalty_reward r ON r.reward_id = p.reward_id WHERE p.member_id = %s ORDER BY p.recorded_at DESC LIMIT 50""",
            (member["member_id"],))
        history = [dict(row, recorded_at=row["recorded_at"].isoformat()) for row in cur.fetchall()]
        return {key: member[key] for key in ("member_id", "balance", "tier_name")} | {"history": history}
    return run(read)


def enroll(hotel_id, user_id, guest_phone):
    """READ the guest's membership; reopen a closed one, keep an active one, or create it."""
    def write(cur):
        guest_id = _guest_id(cur, hotel_id, guest_phone)
        cur.execute("SELECT member_id, status FROM loyalty_member WHERE hotel_id = %s AND guest_id = %s FOR UPDATE", (hotel_id, guest_id))
        found = cur.fetchone()
        if found and found["status"] == "active":
            return {"member_id": found["member_id"], "result": "already a member"}
        if found:
            cur.execute("UPDATE loyalty_member SET status = 'active' WHERE member_id = %s", (found["member_id"],))
            return {"member_id": found["member_id"], "result": "reopened"}
        member_id = new_id("lym")
        cur.execute("INSERT INTO loyalty_member (member_id, hotel_id, guest_id, status, enrolled_by_user_id) VALUES (%s, %s, %s, 'active', %s)",
                    (member_id, hotel_id, guest_id, user_id))
        return {"member_id": member_id, "result": "enrolled"}
    return run(write)


def award(hotel_id, user_id, guest_phone, points, reason, booking_id=None):
    """Add positive points to the member's ledger. A booking must be the guest's own."""
    if int(points) <= 0 or not str(reason or "").strip():
        return {"error": "points > 0 and reason are required"}

    def write(cur):
        member = _member(cur, hotel_id, guest_phone, lock=True)
        if booking_id:
            cur.execute("SELECT 1 AS own FROM booking WHERE hotel_id = %s AND booking_id = %s AND guest_id = %s",
                        (hotel_id, booking_id, member["guest_id"]))
            if not cur.fetchone():
                raise Refused("Booking " + str(booking_id) + " is not this guest's")
        entry_id = new_id("lyp")
        cur.execute(
            """INSERT INTO loyalty_points (entry_id, hotel_id, member_id, points, reason, booking_id, recorded_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (entry_id, hotel_id, member["member_id"], int(points), str(reason).strip()[:255], booking_id or None, user_id))
        return {"entry_id": entry_id, "balance": member["balance"] + int(points)}
    return run(write)


def rewards(hotel_id):
    return db.rows("SELECT reward_id, reward_name, points_cost, status FROM loyalty_reward WHERE hotel_id = %s ORDER BY points_cost, reward_name",
                   (hotel_id,))


def save_reward(hotel_id, reward_name, points_cost, status="active"):
    """READ the reward by name; update its cost and status or create it."""
    name = str(reward_name or "").strip().upper()
    if not name or int(points_cost) <= 0 or status not in ("active", "inactive"):
        return {"error": "reward_name, points_cost > 0 and status active|inactive are required"}

    def write(cur):
        cur.execute("SELECT reward_id FROM loyalty_reward WHERE hotel_id = %s AND reward_name = %s FOR UPDATE", (hotel_id, name))
        found = cur.fetchone()
        if found:
            cur.execute("UPDATE loyalty_reward SET points_cost = %s, status = %s WHERE reward_id = %s", (int(points_cost), status, found["reward_id"]))
            return {"reward_id": found["reward_id"], "result": "updated"}
        reward_id = new_id("lyr")
        cur.execute("INSERT INTO loyalty_reward (reward_id, hotel_id, reward_name, points_cost, status) VALUES (%s, %s, %s, %s, %s)",
                    (reward_id, hotel_id, name, int(points_cost), status))
        return {"reward_id": reward_id, "result": "created"}
    return run(write)


def redeem(hotel_id, user_id, guest_phone, reward_name):
    """Spend the reward's points when the balance covers them."""
    def write(cur):
        member = _member(cur, hotel_id, guest_phone, lock=True)
        cur.execute("SELECT reward_id, reward_name, points_cost FROM loyalty_reward WHERE hotel_id = %s AND reward_name = %s AND status = 'active'",
                    (hotel_id, str(reward_name or "").strip().upper()))
        reward = cur.fetchone()
        if not reward:
            raise Refused("Reward " + str(reward_name or "") + " not found")
        if member["balance"] < reward["points_cost"]:
            raise Refused("Balance " + str(member["balance"]) + " is below " + str(reward["points_cost"]))
        entry_id = new_id("lyp")
        cur.execute(
            """INSERT INTO loyalty_points (entry_id, hotel_id, member_id, points, reason, reward_id, recorded_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (entry_id, hotel_id, member["member_id"], -reward["points_cost"], "redeemed " + reward["reward_name"], reward["reward_id"], user_id))
        return {"entry_id": entry_id, "reward_name": reward["reward_name"], "balance": member["balance"] - reward["points_cost"]}
    return run(write)


def save_occasion(hotel_id, user_id, guest_phone, occasion, occasion_date, notes=None):
    """READ the guest's occasion on that date; update its notes or create it."""
    name = str(occasion or "").strip().lower()
    try:
        day = date.fromisoformat(str(occasion_date or "")[:10])
    except ValueError:
        return {"error": "occasion_date must be YYYY-MM-DD"}
    if not name:
        return {"error": "occasion is required"}

    def write(cur):
        guest_id = _guest_id(cur, hotel_id, guest_phone)
        cur.execute("SELECT occasion_id FROM guest_occasion WHERE guest_id = %s AND occasion = %s AND occasion_date = %s FOR UPDATE",
                    (guest_id, name, day))
        found = cur.fetchone()
        if found:
            cur.execute("UPDATE guest_occasion SET notes = %s WHERE occasion_id = %s", (notes, found["occasion_id"]))
            return {"occasion_id": found["occasion_id"], "result": "updated"}
        occasion_id = new_id("goc")
        cur.execute(
            """INSERT INTO guest_occasion (occasion_id, hotel_id, guest_id, occasion, occasion_date, notes, recorded_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (occasion_id, hotel_id, guest_id, name, day, notes, user_id))
        return {"occasion_id": occasion_id, "result": "created"}
    return run(write)


def occasions(hotel_id, guest_phone):
    def read(cur):
        cur.execute("SELECT occasion_id, occasion, occasion_date, notes FROM guest_occasion WHERE hotel_id = %s AND guest_id = %s ORDER BY occasion_date",
                    (hotel_id, _guest_id(cur, hotel_id, guest_phone)))
        return {"occasions": [dict(row, occasion_date=row["occasion_date"].isoformat()) for row in cur.fetchall()]}
    return run(read)


loyalty_manager = sys.modules[__name__]
