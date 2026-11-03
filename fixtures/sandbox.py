"""SANDBOX HOTEL: the small hotel the sandbox manager and the stress desk client sign in to.

This is a stand-in fixture. Its rooms, guests, bookings, payments and housekeeping rows are invented
and deterministic for a given hotel-local day; they are not production data.

    python -m fixtures.sandbox seed      # create the hotel, its manager login, and its rows (refuses if it exists)
    python -m fixtures.sandbox reset     # wipe the hotel, then seed again
    python -m fixtures.sandbox wipe      # remove the hotel, its rows, and logins that belong only to it

HOTELS and PASSWORD are what fixtures.stress.desk.Desk.sign_in reads. Every table that carries hotel_id is cleared
by wipe, so import batches and chat state go with the hotel.
"""

import os
import random
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASSWORD = os.getenv("SANDBOX_PASSWORD") or "sandbox-pass-2026"
TIMEZONE = "Asia/Kuala_Lumpur"
CURRENCY = "MYR"
SEED = 20261102

HOTELS = [
    {
        "hotel_name": "SANDBOX HOTEL",
        "city_name": "Kuala Lumpur",
        "manager": ("Sandbox Manager", {"username": "sandbox_manager", "email": "sandbox.manager@example.invalid"}),
    },
]

ROOM_TYPES = (
    ("DELUXE", Decimal("220.00"), ("101", "102", "103")),
    ("FAMILY", Decimal("340.00"), ("201", "202")),
)
CHANNELS = (("DIRECT", Decimal("0")), ("WALK_IN", Decimal("0")), ("BOOKING.COM", Decimal("15")), ("EXPEDIA", Decimal("18")))
METHODS = ("CARD", "CASH", "BANK_TRANSFER")
FIRST_NAMES = ("AISHAH", "DANIEL", "MEI LING", "RAJ", "SITI", "JAMES", "NUR", "KENJI", "PRIYA", "OMAR", "HANA", "LUCAS")
LAST_NAMES = ("TAN", "LIM", "KUMAR", "ABDULLAH", "WONG", "SMITH", "ISMAIL", "SATO", "NAIR", "LEE")
NATIONALITIES = ("MALAYSIAN", "SINGAPOREAN", "INDONESIAN", "BRITISH", "JAPANESE", "INDIAN", "AUSTRALIAN")
PAST_DAYS = 30
FUTURE_DAYS = 30


def _spec(hotel_name):
    return next(item for item in HOTELS if item["hotel_name"] == hotel_name)


def _id(rng, prefix):
    return prefix + "_" + "%032x" % rng.getrandbits(128)


def _at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), ZoneInfo(TIMEZONE))


def _today():
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def _stays(rng, today):
    """Back-to-back stays per room across the window. Each is (room_number, arrival, nights)."""
    found = []
    for _type, _rate, numbers in ROOM_TYPES:
        for number in numbers:
            day = today - timedelta(days=PAST_DAYS)
            while day < today + timedelta(days=FUTURE_DAYS):
                day += timedelta(days=rng.choice((0, 0, 1, 2)))
                nights = rng.choice((1, 1, 2, 2, 3, 4))
                found.append((number, day, nights))
                day += timedelta(days=nights)
    return found


def _status(rng, arrival, departure, today):
    roll = rng.random()
    if roll < 0.06:
        return "cancelled"
    if arrival < today and roll < 0.09:
        return "no_show"
    if departure < today:
        return "checked_out"
    if arrival < today <= departure:
        return "checked_in"
    if arrival == today:
        return "confirmed"
    return "confirmed"


def plan(hotel_id, manager_id, today=None):
    """Rows for every operational table, keyed by table name, in insert order."""
    today = today or _today()
    rng = random.Random(SEED)
    now = datetime.now(ZoneInfo(TIMEZONE))
    types, rooms, room_of, rate_of = [], [], {}, {}
    for name, rate, numbers in ROOM_TYPES:
        type_id = _id(rng, "rt")
        types.append({"room_type_id": type_id, "hotel_id": hotel_id, "room_type_name": name, "status": "active"})
        for number in numbers:
            room_id = _id(rng, "rm")
            rooms.append({"room_id": room_id, "hotel_id": hotel_id, "room_type_id": type_id, "room_number": number, "status": "active"})
            room_of[number] = (room_id, type_id)
            rate_of[type_id] = rate
    days = [today - timedelta(days=PAST_DAYS) + timedelta(days=k) for k in range(PAST_DAYS + FUTURE_DAYS + 1)]
    inventory, pricing = [], []
    for name, rate, numbers in ROOM_TYPES:
        type_id = room_of[numbers[0]][1]
        for day in days:
            price = rate + (Decimal("40.00") if day.weekday() >= 4 else Decimal("0"))
            inventory.append({"hotel_id": hotel_id, "room_type_id": type_id, "stay_date": day, "total_inventory": len(numbers),
                              "blocked_quantity": 0, "updated_by_user_id": manager_id})
            pricing.append({"hotel_id": hotel_id, "room_type_id": type_id, "stay_date": day, "base_price": price, "saved_price": price,
                            "updated_by_user_id": manager_id})
    guests, identities, bookings, nights, payments = [], [], [], [], []
    for index, (number, arrival, count) in enumerate(_stays(rng, today)):
        departure = arrival + timedelta(days=count)
        room_id, type_id = room_of[number]
        status = _status(rng, arrival, departure, today)
        guest_id = _id(rng, "gst")
        guest_name = rng.choice(FIRST_NAMES) + " " + rng.choice(LAST_NAMES)
        booked_at = _at(arrival - timedelta(days=rng.randint(1, 40)), rng.randint(8, 21), rng.choice((0, 15, 30, 45)))
        guests.append({"guest_id": guest_id, "hotel_id": hotel_id, "guest_name": guest_name, "nationality": rng.choice(NATIONALITIES),
                       "vip": rng.random() < 0.1, "status": "active", "created_at": booked_at, "created_by_user_id": manager_id})
        identities.append({"identity_id": _id(rng, "gid"), "hotel_id": hotel_id, "guest_id": guest_id, "identity_type": "phone",
                           "identity_value": "+601" + str(10000000 + index * 7919)[-8:]})
        channel, commission = rng.choice(CHANNELS)
        ota = commission > 0
        rates = [rate_of[type_id] + (Decimal("40.00") if (arrival + timedelta(days=k)).weekday() >= 4 else Decimal("0")) for k in range(count)]
        gross = sum(rates, Decimal("0"))
        booking_id = _id(rng, "bkg")
        checked_in_at = _at(arrival, 15, rng.randint(0, 59)) if status in ("checked_in", "checked_out") else None
        checked_out_at = _at(departure, 11, rng.randint(0, 59)) if status == "checked_out" else None
        bookings.append({
            "booking_id": booking_id, "hotel_id": hotel_id, "guest_id": guest_id, "booking_status": status, "channel": channel,
            "channel_booking_ref": channel[:3] + "-" + str(100000 + index), "rate_plan": "OTA STANDARD" if ota else "BAR",
            "adults": rng.choice((1, 2, 2)), "children": rng.choice((0, 0, 1)), "currency": CURRENCY, "gross_amount": gross,
            "commission_amount": (gross * commission / 100).quantize(Decimal("0.01")), "commission_rate": commission, "booked_at": booked_at,
            "created_by_user_id": None if ota else manager_id,
            "cancelled_at": booked_at + timedelta(days=1) if status == "cancelled" else None,
            "no_show_at": _at(arrival + timedelta(days=1), 1) if status == "no_show" else None,
        })
        assigned = status in ("checked_in", "checked_out", "confirmed")
        night_status = {"confirmed": "booked", "checked_in": "checked_in", "checked_out": "checked_out",
                        "cancelled": "cancelled", "no_show": "no_show"}[status]
        for k, rate in enumerate(rates):
            nights.append({
                "booking_night_id": _id(rng, "bkn"), "hotel_id": hotel_id, "booking_id": booking_id, "stay_date": arrival + timedelta(days=k),
                "room_seq": 1, "room_type_id": type_id, "room_id": room_id if assigned else None, "night_status": night_status, "sell_rate": rate,
                "currency": CURRENCY, "rate_source": "channel" if ota else "desk", "assigned_at": booked_at if assigned else None,
                "assigned_by_user_id": manager_id if assigned else None, "checked_in_at": checked_in_at, "checked_out_at": checked_out_at,
            })
        method = rng.choice(METHODS)
        if status == "checked_out":
            payments.append(_payment(rng, hotel_id, booking_id, gross, method, "SETTLEMENT", checked_out_at, manager_id))
        elif status == "checked_in":
            payments.append(_payment(rng, hotel_id, booking_id, (gross / 2).quantize(Decimal("0.01")), method, "DEPOSIT", checked_in_at, manager_id))
        elif status == "confirmed" and rng.random() < 0.4:
            payments.append(_payment(rng, hotel_id, booking_id, rates[0], "CARD", "FIRST NIGHT DEPOSIT", booked_at, manager_id))
    occupied = {row["room_id"] for row in nights if row["stay_date"] == today and row["night_status"] == "checked_in"}
    departed = {row["room_id"] for row in nights
                if row["night_status"] == "checked_out" and row["checked_out_at"] and row["checked_out_at"].date() == today}
    readiness = []
    for room in rooms:
        state = "dirty" if room["room_id"] in departed else ("inspected" if room["room_number"] == "202" and room["room_id"] not in occupied else "clean")
        readiness.append({"room_id": room["room_id"], "hotel_id": hotel_id, "readiness_status": state, "changed_at": now - timedelta(hours=2),
                          "changed_by_user_id": manager_id, "clean_since": None if state == "dirty" else now - timedelta(hours=2),
                          "auto_dirty_days": 3})
    return {
        "room_type": types, "room": rooms, "room_inventory_nightly": inventory, "daily_pricing": pricing, "guest": guests,
        "guest_identity": identities, "booking": bookings, "booking_room_night": nights, "payment": payments,
        "room_readiness_status": readiness,
    }


def _payment(rng, hotel_id, booking_id, amount, method, reference, paid_at, manager_id):
    return {"payment_id": _id(rng, "pay"), "hotel_id": hotel_id, "booking_id": booking_id, "payment_kind": "payment", "amount": amount,
            "currency": CURRENCY, "payment_method": method, "payment_status": "received", "reference": reference, "paid_at": paid_at,
            "recorded_by_user_id": manager_id}


def _hotel(cur, hotel_name):
    cur.execute("SELECT hotel_id FROM hotel WHERE hotel_name = %s", (hotel_name,))
    row = cur.fetchone()
    return (row["hotel_id"] if isinstance(row, dict) else row[0]) if row else None


def seed(hotel_name="SANDBOX HOTEL"):
    """Create the manager login, the hotel, and its rows. Returns {'hotel_id', 'user_id', counts}."""
    from psycopg2.extras import execute_values
    from booking_system import db, user_manager

    spec = _spec(hotel_name)
    name, identities = spec["manager"]
    if db.transaction(lambda cur: _hotel(cur, hotel_name)):
        raise SystemExit(hotel_name + " already exists; use reset")
    created = user_manager.signup(name, identities, PASSWORD, "English", "Malaysian")
    if created.get("error"):
        raise SystemExit("manager login: " + created["error"])
    user_id = created["user_id"]
    hotel = user_manager.create_hotel(user_id, hotel_name, CURRENCY, TIMEZONE, spec.get("city_name"))
    if hotel.get("error"):
        raise SystemExit("hotel: " + hotel["error"])
    hotel_id = hotel["hotel_id"]
    rows = plan(hotel_id, user_id)

    def write(cur):
        counts = {}
        for table, found in rows.items():
            if found:
                cols = list(found[0])
                execute_values(cur, "INSERT INTO " + table + " (" + ", ".join(cols) + ") VALUES %s",
                               [tuple(row[c] for c in cols) for row in found], page_size=1000)
            counts[table] = len(found)
        return counts

    counts = db.transaction(write)
    return {"hotel_id": hotel_id, "user_id": user_id, "rows": counts}


def wipe(hotel_name="SANDBOX HOTEL"):
    """Delete every row that names this hotel, the hotel itself, and logins that belong to no other hotel."""
    from booking_system import db

    conn = db.connect()
    try:
        cur = conn.cursor()
        hotel_id = _hotel(cur, hotel_name)
        if not hotel_id:
            return {"wiped": False}
        cur.execute("SELECT user_id FROM hotel_user WHERE hotel_id = %s", (hotel_id,))
        users = [row[0] for row in cur.fetchall()]
        cur.execute("""SELECT table_name FROM information_schema.columns
                       WHERE table_schema = current_schema() AND column_name = 'hotel_id' AND table_name <> 'hotel'
                       AND table_name IN (SELECT table_name FROM information_schema.tables
                                          WHERE table_schema = current_schema() AND table_type = 'BASE TABLE')""")
        pending = [row[0] for row in cur.fetchall()]
        for _pass in range(len(pending) + 1):
            blocked = []
            for table in pending:
                cur.execute("SAVEPOINT wipe_step")
                try:
                    cur.execute("DELETE FROM " + table + " WHERE hotel_id = %s", (hotel_id,))
                    cur.execute("RELEASE SAVEPOINT wipe_step")
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT wipe_step")
                    blocked.append(table)
            if not blocked:
                break
            if len(blocked) == len(pending):
                raise RuntimeError("cannot clear " + ", ".join(blocked))
            pending = blocked
        cur.execute("DELETE FROM hotel WHERE hotel_id = %s", (hotel_id,))
        for user_id in users:
            cur.execute("SELECT 1 FROM hotel_user WHERE user_id = %s LIMIT 1", (user_id,))
            if cur.fetchone():
                continue
            cur.execute("""SELECT table_name FROM information_schema.columns WHERE table_schema = current_schema() AND column_name = 'user_id'
                           AND table_name <> 'user_account' AND table_name IN (SELECT table_name FROM information_schema.tables
                           WHERE table_schema = current_schema() AND table_type = 'BASE TABLE')""")
            for (table,) in cur.fetchall():
                cur.execute("SAVEPOINT wipe_user")
                try:
                    cur.execute("DELETE FROM " + table + " WHERE user_id = %s", (user_id,))
                    cur.execute("RELEASE SAVEPOINT wipe_user")
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT wipe_user")
            cur.execute("DELETE FROM user_account WHERE user_id = %s", (user_id,))
        conn.commit()
        return {"wiped": True, "hotel_id": hotel_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv=None):
    import json
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
    args = list(sys.argv[1:] if argv is None else argv)
    command = (args[0] if args else "seed").strip().lower()
    actions = {
        "seed": lambda: seed(),
        "wipe": lambda: wipe(),
        "reset": lambda: (wipe(), seed())[1],
    }
    if command not in actions:
        raise SystemExit("use: python -m fixtures.sandbox seed | reset | wipe")
    print(json.dumps(actions[command](), default=str, indent=1))


if __name__ == "__main__":
    main()
