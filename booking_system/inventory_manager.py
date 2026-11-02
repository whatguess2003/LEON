"""Nightly inventory and prices per room type: room_inventory_nightly, daily_pricing, room_allotments.

available_rooms / free_rooms / count_rooms take the caller's cursor (RealDictCursor) so every module reads availability inside its own
transaction. A night without a room_inventory_nightly row is not for sale: those reads return None and the caller reports it."""

import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from booking_system import core_refs, db

HORIZON_DAYS = 365
LIVE_NIGHTS = ("booked", "checked_in")
SOLD_NIGHTS = ("booked", "checked_in", "checked_out")


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value or "").strip()[:10])


def _days(values):
    """Sorted distinct dates, or None when one is not YYYY-MM-DD."""
    try:
        return sorted({_day(value) for value in values or []})
    except ValueError:
        return None


def hotel_today(zone):
    return datetime.now(ZoneInfo(zone)).date()


# ---------- availability (shared) ----------

def count_rooms(cur, hotel_id, room_type_id):
    """Active physical rooms of a room type."""
    cur.execute("SELECT COUNT(*) AS n FROM room WHERE hotel_id = %s AND room_type_id = %s AND status = 'active'", (hotel_id, room_type_id))
    return int(cur.fetchone()["n"])


def availability(cur, hotel_id, room_type_id, start, end):
    """{stay_date: {total, blocked, booked, stop_sell, available}} for the nights on file between start and end."""
    cur.execute(
        """SELECT n.stay_date, n.total_inventory, n.blocked_quantity, n.stop_sell,
                  (SELECT COUNT(*) FROM booking_room_night b
                   WHERE b.room_type_id = n.room_type_id AND b.stay_date = n.stay_date AND b.night_status = ANY(%s)) AS booked
           FROM room_inventory_nightly n
           WHERE n.hotel_id = %s AND n.room_type_id = %s AND n.stay_date BETWEEN %s AND %s""",
        (list(LIVE_NIGHTS), hotel_id, room_type_id, start, end),
    )
    nights = {}
    for row in cur.fetchall():
        open_rooms = row["total_inventory"] - row["blocked_quantity"] - row["booked"]
        nights[row["stay_date"]] = {
            "total": row["total_inventory"], "blocked": row["blocked_quantity"], "booked": int(row["booked"]),
            "stop_sell": row["stop_sell"], "available": 0 if row["stop_sell"] else max(open_rooms, 0),
        }
    return nights


def available_rooms(cur, hotel_id, room_type_id, stay_date):
    """total_inventory - blocked_quantity - live nights of the type; 0 on stop sell; None when the night has no inventory."""
    day = _day(stay_date)
    found = availability(cur, hotel_id, room_type_id, day, day).get(day)
    return found["available"] if found else None


def free_rooms(cur, hotel_id, room_type_id, stay_date):
    """[{'room_id', 'room_number'}] of the type with no live night and no room_block on stay_date."""
    cur.execute(
        """SELECT r.room_id, r.room_number FROM room r
           WHERE r.hotel_id = %s AND r.room_type_id = %s AND r.status = 'active'
             AND NOT EXISTS (SELECT 1 FROM booking_room_night b
                             WHERE b.room_id = r.room_id AND b.stay_date = %s AND b.night_status = ANY(%s))
             AND NOT EXISTS (SELECT 1 FROM room_block k WHERE k.room_id = r.room_id AND %s BETWEEN k.start_date AND k.end_date)
           ORDER BY r.room_number""",
        (hotel_id, room_type_id, _day(stay_date), list(LIVE_NIGHTS), _day(stay_date)),
    )
    return [dict(row) for row in cur.fetchall()]


# ---------- nights a room type sells ----------

def seed_nights(cur, hotel_id, user_id, room_type_id, start, base_price):
    """Inventory (total = room count) and price rows for HORIZON_DAYS from start. Nights already on file are kept."""
    days = [start + timedelta(days=offset) for offset in range(HORIZON_DAYS)]
    total = count_rooms(cur, hotel_id, room_type_id)
    cur.execute(
        """INSERT INTO room_inventory_nightly (hotel_id, room_type_id, stay_date, total_inventory, blocked_quantity, updated_by_user_id)
           SELECT %s, %s, d, %s, 0, %s FROM unnest(%s::date[]) AS d
           ON CONFLICT (room_type_id, stay_date) DO NOTHING""",
        (hotel_id, room_type_id, total, user_id, days),
    )
    cur.execute(
        """INSERT INTO daily_pricing (hotel_id, room_type_id, stay_date, base_price, saved_price, updated_by_user_id)
           SELECT %s, %s, d, %s, %s, %s FROM unnest(%s::date[]) AS d
           ON CONFLICT (room_type_id, stay_date) DO NOTHING""",
        (hotel_id, room_type_id, base_price, base_price, user_id, days),
    )
    return len(days)


def apply_room_count(cur, hotel_id, user_id, room_type_id, start):
    """From start on, each night's total follows the room count; blocked rooms are capped at it."""
    total = count_rooms(cur, hotel_id, room_type_id)
    cur.execute(
        """UPDATE room_inventory_nightly SET total_inventory = %s, blocked_quantity = LEAST(blocked_quantity, %s),
                  updated_at = now(), updated_by_user_id = %s
           WHERE hotel_id = %s AND room_type_id = %s AND stay_date >= %s""",
        (total, total, user_id, hotel_id, room_type_id, start),
    )
    return total


def _anchor(cur, hotel_id, room_type_id, days):
    """Kite pace starts again from now for re-priced nights."""
    cur.execute(
        """INSERT INTO kite_price_anchor (hotel_id, room_type_id, stay_date, anchor_at)
           SELECT %s, %s, d, now() FROM unnest(%s::date[]) AS d
           ON CONFLICT (room_type_id, stay_date) DO UPDATE SET anchor_at = EXCLUDED.anchor_at""",
        (hotel_id, room_type_id, days),
    )


def _type_id(cur, hotel_id, room_type):
    found = core_refs.room_type_id(cur, hotel_id, room_type)
    return found, ({} if found else {"error": "Room type " + str(room_type or "").strip().upper() + " was not found"})


def night_on_file(hotel_id, room_type, stay_date):
    """{'room_type', 'day', 'total', 'blocked', 'stop_sell', 'price'} for a night with inventory, or None."""
    def run(cur):
        cur.execute(
            """SELECT t.room_type_name, n.stay_date, n.total_inventory, n.blocked_quantity, n.stop_sell, p.base_price
               FROM room_inventory_nightly n JOIN room_type t ON t.room_type_id = n.room_type_id
               LEFT JOIN daily_pricing p ON p.room_type_id = n.room_type_id AND p.stay_date = n.stay_date
               WHERE n.hotel_id = %s AND t.room_type_name = %s AND n.stay_date = %s""",
            (hotel_id, str(room_type or "").strip().upper(), _day(stay_date)),
        )
        return cur.fetchone()
    found = db.transaction(run)
    if not found:
        return None
    return {"room_type": found["room_type_name"], "day": found["stay_date"].isoformat(), "total": found["total_inventory"],
            "blocked": found["blocked_quantity"], "stop_sell": found["stop_sell"],
            "price": None if found["base_price"] is None else float(found["base_price"])}


def update_dates(hotel_id, user_id, room_type, dates, price, quantity):
    """Price and inventory for many nights in one transaction. Total follows the room count; quantity 0 stops sale, any other
    quantity reopens it, None keeps it."""
    days = _days(dates)
    if days is None:
        return {"error": "Invalid date"}
    if not days:
        return {"error": "dates are required"}
    try:
        amount = float(price)
    except (TypeError, ValueError):
        return {"error": "Price is required"}
    stop = None if quantity is None else int(quantity) == 0

    def run(cur):
        type_id, missing = _type_id(cur, hotel_id, room_type)
        if missing:
            return missing
        total = count_rooms(cur, hotel_id, type_id)
        cur.execute(
            """INSERT INTO room_inventory_nightly (hotel_id, room_type_id, stay_date, total_inventory, blocked_quantity, stop_sell, updated_by_user_id)
               SELECT %s, %s, d, %s, 0, COALESCE(%s, FALSE), %s FROM unnest(%s::date[]) AS d
               ON CONFLICT (room_type_id, stay_date) DO UPDATE SET
                   total_inventory = EXCLUDED.total_inventory,
                   blocked_quantity = LEAST(room_inventory_nightly.blocked_quantity, EXCLUDED.total_inventory),
                   stop_sell = COALESCE(%s, room_inventory_nightly.stop_sell),
                   updated_at = now(), updated_by_user_id = EXCLUDED.updated_by_user_id""",
            (hotel_id, type_id, total, stop, user_id, days, stop),
        )
        cur.execute(
            """INSERT INTO daily_pricing (hotel_id, room_type_id, stay_date, base_price, saved_price, updated_by_user_id)
               SELECT %s, %s, d, %s, %s, %s FROM unnest(%s::date[]) AS d
               ON CONFLICT (room_type_id, stay_date) DO UPDATE SET
                   base_price = EXCLUDED.base_price, saved_price = EXCLUDED.saved_price,
                   updated_at = now(), updated_by_user_id = EXCLUDED.updated_by_user_id""",
            (hotel_id, type_id, amount, amount, user_id, days),
        )
        _anchor(cur, hotel_id, type_id, days)
        return {"success": True, "dates": len(days), "rooms": total}
    return db.transaction(run)


def set_prices(hotel_id, user_id, room_type, start_date, end_date, price):
    """Price every night with inventory from start_date to end_date. Nights without inventory are listed, not created."""
    try:
        start, end, amount = _day(start_date), _day(end_date), float(price)
    except (TypeError, ValueError):
        return {"error": "start_date, end_date (YYYY-MM-DD) and price are required"}
    if end < start:
        return {"error": "End date is before the start date"}

    def run(cur):
        type_id, missing = _type_id(cur, hotel_id, room_type)
        if missing:
            return missing
        cur.execute("SELECT stay_date FROM room_inventory_nightly WHERE room_type_id = %s AND stay_date BETWEEN %s AND %s",
                    (type_id, start, end))
        on_file = sorted(row["stay_date"] for row in cur.fetchall())
        cur.execute(
            """INSERT INTO daily_pricing (hotel_id, room_type_id, stay_date, base_price, saved_price, updated_by_user_id)
               SELECT %s, %s, d, %s, %s, %s FROM unnest(%s::date[]) AS d
               ON CONFLICT (room_type_id, stay_date) DO UPDATE SET
                   base_price = EXCLUDED.base_price, saved_price = EXCLUDED.saved_price,
                   updated_at = now(), updated_by_user_id = EXCLUDED.updated_by_user_id""",
            (hotel_id, type_id, amount, amount, user_id, on_file),
        )
        _anchor(cur, hotel_id, type_id, on_file)
        priced = set(on_file)
        wanted = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
        return {"success": True, "dates": [day.isoformat() for day in on_file],
                "missing_inventory": [day.isoformat() for day in wanted if day not in priced]}
    return db.transaction(run)


def update_room_price(hotel_id, user_id, room_type, stay_date, price):
    """UPDATE one night's price. The night must have inventory."""
    try:
        day, amount = _day(stay_date), float(price)
    except (TypeError, ValueError):
        return {"error": "stay_date (YYYY-MM-DD) and price are required"}

    def run(cur):
        type_id, missing = _type_id(cur, hotel_id, room_type)
        if missing:
            return missing
        cur.execute(
            """SELECT p.base_price FROM room_inventory_nightly n
               LEFT JOIN daily_pricing p ON p.room_type_id = n.room_type_id AND p.stay_date = n.stay_date
               WHERE n.room_type_id = %s AND n.stay_date = %s""",
            (type_id, day),
        )
        night = cur.fetchone()
        if not night:
            return {"error": "No inventory for " + str(room_type).upper() + " on " + day.isoformat()}
        cur.execute(
            """INSERT INTO daily_pricing (hotel_id, room_type_id, stay_date, base_price, saved_price, updated_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (room_type_id, stay_date) DO UPDATE SET
                   base_price = EXCLUDED.base_price, saved_price = EXCLUDED.saved_price,
                   updated_at = now(), updated_by_user_id = EXCLUDED.updated_by_user_id""",
            (hotel_id, type_id, day, amount, amount, user_id),
        )
        _anchor(cur, hotel_id, type_id, [day])
        previous = night["base_price"]
        return {"success": True, "room_type": str(room_type).upper(), "stay_date": day.isoformat(),
                "old_price": None if previous is None else float(previous), "price": amount}
    return db.transaction(run)


def update_nightly_inventory(hotel_id, user_id, room_type, stay_date, total_inventory=None, blocked_quantity=None):
    """UPDATE one night's total and blocked rooms; None keeps the stored value. Blocked cannot exceed the total."""
    try:
        day = _day(stay_date)
    except ValueError:
        return {"error": "stay_date must be YYYY-MM-DD"}

    def run(cur):
        type_id, missing = _type_id(cur, hotel_id, room_type)
        if missing:
            return missing
        cur.execute("SELECT total_inventory, blocked_quantity FROM room_inventory_nightly WHERE room_type_id = %s AND stay_date = %s",
                    (type_id, day))
        night = cur.fetchone()
        if not night:
            return {"error": "No inventory for " + str(room_type).upper() + " on " + day.isoformat()}
        total = night["total_inventory"] if total_inventory is None else int(total_inventory)
        blocked = night["blocked_quantity"] if blocked_quantity is None else int(blocked_quantity)
        if total < 0 or blocked < 0 or blocked > total:
            return {"error": "Blocked rooms must be between 0 and " + str(total)}
        cur.execute(
            """UPDATE room_inventory_nightly SET total_inventory = %s, blocked_quantity = %s, updated_at = now(), updated_by_user_id = %s
               WHERE room_type_id = %s AND stay_date = %s""",
            (total, blocked, user_id, type_id, day),
        )
        return {"success": True, "room_type": str(room_type).upper(), "stay_date": day.isoformat(),
                "total": total, "blocked": blocked, "old_blocked": night["blocked_quantity"]}
    return db.transaction(run)


def update_night_rules(hotel_id, user_id, room_type, stay_date, stop_sell, min_stay, closed_to_arrival):
    """UPDATE restrictions on a night that has inventory."""
    try:
        day = _day(stay_date)
    except ValueError:
        return {"error": "stay_date must be YYYY-MM-DD"}

    def run(cur):
        type_id, missing = _type_id(cur, hotel_id, room_type)
        if missing:
            return missing
        cur.execute(
            """UPDATE room_inventory_nightly SET stop_sell = %s, min_stay = %s, closed_to_arrival = %s, updated_at = now(), updated_by_user_id = %s
               WHERE room_type_id = %s AND stay_date = %s""",
            (bool(stop_sell), min_stay, bool(closed_to_arrival), user_id, type_id, day),
        )
        if not cur.rowcount:
            return {"error": "No inventory for " + str(room_type).upper() + " on " + day.isoformat()}
        return {"success": True}
    return db.transaction(run)


def save_allotment(hotel_id, user_id, channel, room_type, stay_date, rooms_allotted, release_date):
    """CREATE or UPDATE a channel allotment on a night that has inventory."""
    name = str(channel or "").strip().upper()
    if not name:
        return {"error": "channel is required"}
    try:
        day = _day(stay_date)
        release = None if release_date in (None, "") else _day(release_date)
    except ValueError:
        return {"error": "Dates must be YYYY-MM-DD"}

    def run(cur):
        type_id, missing = _type_id(cur, hotel_id, room_type)
        if missing:
            return missing
        cur.execute("SELECT 1 FROM room_inventory_nightly WHERE room_type_id = %s AND stay_date = %s", (type_id, day))
        if not cur.fetchone():
            return {"error": "No inventory for " + str(room_type).upper() + " on " + day.isoformat()}
        cur.execute(
            """INSERT INTO room_allotments (hotel_id, room_type_id, channel, stay_date, rooms_allotted, release_date, updated_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (room_type_id, channel, stay_date) DO UPDATE SET
                   rooms_allotted = EXCLUDED.rooms_allotted, release_date = EXCLUDED.release_date,
                   updated_at = now(), updated_by_user_id = EXCLUDED.updated_by_user_id""",
            (hotel_id, type_id, name, day, int(rooms_allotted), release, user_id),
        )
        return {"success": True, "channel": name, "stay_date": day.isoformat(), "rooms_allotted": int(rooms_allotted)}
    return db.transaction(run)


# ---------- reads for the UI ----------

def read_calendar(hotel_id, start_date, end_date):
    """{'room_types': [{room_type, room_type_id, pricing: [...], quantities: [...]}]} for active room types."""
    try:
        start, end = _day(start_date), _day(end_date)
    except ValueError:
        return {"room_types": [], "error": "Dates must be YYYY-MM-DD"}

    def run(cur):
        cur.execute("SELECT room_type_id, room_type_name FROM room_type WHERE hotel_id = %s AND status = 'active' ORDER BY room_type_name",
                    (hotel_id,))
        types = cur.fetchall()
        cur.execute(
            """SELECT room_type_id, stay_date, base_price, COALESCE(saved_price, base_price) AS saved_price, updated_at
               FROM daily_pricing WHERE hotel_id = %s AND stay_date BETWEEN %s AND %s ORDER BY stay_date""",
            (hotel_id, start, end),
        )
        prices = cur.fetchall()
        cur.execute(
            """SELECT room_type_id, stay_date, total_inventory, blocked_quantity, stop_sell, updated_at
               FROM room_inventory_nightly WHERE hotel_id = %s AND stay_date BETWEEN %s AND %s ORDER BY stay_date""",
            (hotel_id, start, end),
        )
        return types, prices, cur.fetchall()
    types, prices, nights = db.transaction(run)
    by_type = {row["room_type_id"]: {"room_type": row["room_type_name"], "room_type_id": row["room_type_id"], "pricing": [], "quantities": []}
               for row in types}
    for row in prices:
        by_type.get(row["room_type_id"], {"pricing": []})["pricing"].append({
            "date": row["stay_date"].isoformat(), "price": float(row["base_price"]), "saved_price": float(row["saved_price"]),
            "updated_at": str(row["updated_at"]),
        })
    for row in nights:
        active = 0 if row["stop_sell"] else row["total_inventory"] - row["blocked_quantity"]
        by_type.get(row["room_type_id"], {"quantities": []})["quantities"].append({
            "date": row["stay_date"].isoformat(), "total_inventory": row["total_inventory"], "active_inventory": active,
            "blocked_quantity": row["blocked_quantity"], "stop_sell": row["stop_sell"], "updated_at": str(row["updated_at"]),
        })
    return {"room_types": list(by_type.values())}


def read_prices(hotel_id, room_type, start_date, end_date):
    """{'room_type', 'prices': {date: base_price}} or {'error'}."""
    try:
        start, end = _day(start_date), _day(end_date)
    except ValueError:
        return {"error": "Dates must be YYYY-MM-DD"}

    def run(cur):
        type_id, missing = _type_id(cur, hotel_id, room_type)
        if missing:
            return missing
        cur.execute("SELECT stay_date, base_price FROM daily_pricing WHERE room_type_id = %s AND stay_date BETWEEN %s AND %s ORDER BY stay_date",
                    (type_id, start, end))
        return {"room_type": str(room_type).strip().upper(),
                "prices": {row["stay_date"].isoformat(): float(row["base_price"]) for row in cur.fetchall()}}
    return db.transaction(run)


def read_availability(hotel_id, room_type, stay_date):
    """{'total', 'blocked', 'booked', 'stop_sell', 'available'} for one night, or {'error'}."""
    try:
        day = _day(stay_date)
    except ValueError:
        return {"error": "stay_date must be YYYY-MM-DD"}

    def run(cur):
        type_id, missing = _type_id(cur, hotel_id, room_type)
        if missing:
            return missing
        return availability(cur, hotel_id, type_id, day, day).get(day) or {
            "error": "No inventory for " + str(room_type).strip().upper() + " on " + day.isoformat()}
    return db.transaction(run)


def read_free_rooms(hotel_id, room_type, stay_date):
    """{'available': [room numbers]} free on stay_date, or {'error'}."""
    try:
        day = _day(stay_date)
    except ValueError:
        return {"error": "stay_date must be YYYY-MM-DD"}

    def run(cur):
        type_id, missing = _type_id(cur, hotel_id, room_type)
        if missing:
            return missing
        return {"available": [row["room_number"] for row in free_rooms(cur, hotel_id, type_id, day)]}
    return db.transaction(run)


def read_hotel_analytics(hotel_id, zone, days=30):
    """Occupancy and room revenue (sold nights' sell_rate) for the last `days` hotel days, against the period before."""
    span = max(1, int(days or 30))
    today = hotel_today(zone)
    start = today - timedelta(days=span - 1)
    prev_start = start - timedelta(days=span)

    def run(cur):
        cur.execute("SELECT COUNT(*) AS n FROM room WHERE hotel_id = %s AND status = 'active'", (hotel_id,))
        rooms = int(cur.fetchone()["n"])
        cur.execute(
            """SELECT n.stay_date, COUNT(*) AS nights, COALESCE(SUM(n.sell_rate), 0) AS revenue FROM booking_room_night n
               WHERE n.hotel_id = %s AND n.night_status = ANY(%s) AND n.stay_date BETWEEN %s AND %s GROUP BY n.stay_date""",
            (hotel_id, list(SOLD_NIGHTS), prev_start, today),
        )
        by_day = {row["stay_date"]: row for row in cur.fetchall()}
        cur.execute(
            """SELECT t.room_type_name, COUNT(*) AS nights, COALESCE(SUM(n.sell_rate), 0) AS revenue
               FROM booking_room_night n JOIN room_type t ON t.room_type_id = n.room_type_id
               WHERE n.hotel_id = %s AND n.night_status = ANY(%s) AND n.stay_date BETWEEN %s AND %s
               GROUP BY t.room_type_name ORDER BY revenue DESC""",
            (hotel_id, list(SOLD_NIGHTS), start, today),
        )
        types = cur.fetchall()
        cur.execute(
            """SELECT b.channel, COUNT(DISTINCT b.booking_id) AS bookings
               FROM booking_room_night n JOIN booking b ON b.booking_id = n.booking_id
               WHERE n.hotel_id = %s AND n.night_status = ANY(%s) AND n.stay_date BETWEEN %s AND %s
               GROUP BY b.channel ORDER BY bookings DESC""",
            (hotel_id, list(SOLD_NIGHTS), start, today),
        )
        channels = cur.fetchall()
        cur.execute(
            """SELECT COALESCE(g.nationality, 'Unknown') AS country, COUNT(DISTINCT g.guest_id) AS guests
               FROM booking_room_night n JOIN booking b ON b.booking_id = n.booking_id JOIN guest g ON g.guest_id = b.guest_id
               WHERE n.hotel_id = %s AND n.night_status = ANY(%s) AND n.stay_date BETWEEN %s AND %s
               GROUP BY 1 ORDER BY guests DESC""",
            (hotel_id, list(SOLD_NIGHTS), start, today),
        )
        return rooms, by_day, types, channels, cur.fetchall()
    rooms, by_day, types, channels, countries = db.transaction(run)

    def window(first):
        dates = [first + timedelta(days=offset) for offset in range(span)]
        nights = sum(int(by_day[day]["nights"]) for day in dates if day in by_day)
        revenue = sum(float(by_day[day]["revenue"]) for day in dates if day in by_day)
        occupancy = nights / (rooms * span) * 100.0 if rooms else 0.0
        return dates, nights, revenue, occupancy, (revenue / nights if nights else 0.0)

    dates, nights, revenue, occupancy, adr = window(start)
    _, _, prev_revenue, prev_occupancy, prev_adr = window(prev_start)

    def change(now, before):
        return round((now - before) / before * 100.0, 1) if before else 0.0

    bookings = sum(int(row["bookings"]) for row in channels) or 1
    guests = sum(int(row["guests"]) for row in countries) or 1
    return {
        "kpis": {
            "totalRevenue": round(revenue, 2), "occupancyRate": round(occupancy, 1), "adr": round(adr, 2), "guestSatisfaction": None,
            "roomCount": rooms, "occupiedNights": nights,
            "changes": {"revenue": change(revenue, prev_revenue), "occupancy": change(occupancy, prev_occupancy),
                        "adr": change(adr, prev_adr), "satisfaction": 0},
        },
        "revenue": [{"date": day.isoformat(), "revenue": round(float(by_day[day]["revenue"]) if day in by_day else 0.0, 2)} for day in dates],
        "occupancy": [{"date": day.isoformat(),
                       "occupancy": round(int(by_day[day]["nights"]) / rooms * 100.0, 1) if rooms and day in by_day else 0.0} for day in dates],
        "roomTypes": [{"type": row["room_type_name"], "revenue": round(float(row["revenue"]), 2), "bookings": int(row["nights"])} for row in types],
        "bookingSources": [{"source": row["channel"], "bookings": int(row["bookings"]),
                            "percentage": round(int(row["bookings"]) / bookings * 100, 1)} for row in channels],
        "demographics": [{"country": row["country"], "guests": int(row["guests"]),
                          "percentage": round(int(row["guests"]) / guests * 100, 1)} for row in countries],
    }


# `from booking_system.inventory_manager import inventory_manager` keeps working for callers of the old instance.
inventory_manager = sys.modules[__name__]
