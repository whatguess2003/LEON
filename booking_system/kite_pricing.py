"""INAP Kite pricing.

Hotel-wide sensitivity and limits. The nightly base price stays what bookings
charge. The dynamic price is calculated only for display and for the rate
sent to Aiosell when that stay date is toggled on.
"""
import os
from datetime import datetime, timedelta, date
from decimal import Decimal, ROUND_HALF_UP

import psycopg2
from dotenv import load_dotenv

from booking_system.llm_utils import canonical_key

load_dotenv()

# (days before the stay date at local midnight, booked share). The last
# checkpoint is 18:00 on the stay date and is stored with days = 0 and hour 18.
PACE_CHECKPOINTS = (
    (30, 0.05, 0),
    (14, 0.10, 0),
    (7, 0.20, 0),
    (1, 0.50, 0),
    (0, 0.80, 18),
)


def _money(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def checkpoint_moments(stay_date, tzinfo):
    stay_start = datetime(stay_date.year, stay_date.month, stay_date.day, tzinfo=tzinfo)
    moments = []
    for days_before, share, hour in PACE_CHECKPOINTS:
        moment = stay_start - timedelta(days=days_before)
        if hour:
            moment = moment.replace(hour=hour)
        moments.append((moment, share))
    return moments


def on_track_share(now, stay_date, anchor_at):
    """Share of rooms that should already be booked at `now`."""
    points = checkpoint_moments(stay_date, now.tzinfo)
    if anchor_at is not None:
        if anchor_at.tzinfo is None:
            anchor_at = anchor_at.replace(tzinfo=now.tzinfo)
        else:
            anchor_at = anchor_at.astimezone(now.tzinfo)
        if anchor_at < points[0][0]:
            points = [(anchor_at, 0.0)] + points
    if now >= points[-1][0]:
        return points[-1][1]
    if now <= points[0][0]:
        return points[0][1]
    for index in range(1, len(points)):
        start_at, start_share = points[index - 1]
        end_at, end_share = points[index]
        if start_at <= now <= end_at:
            span = (end_at - start_at).total_seconds()
            if span <= 0:
                return end_share
            covered = (now - start_at).total_seconds() / span
            return start_share + (end_share - start_share) * covered
    return points[-1][1]


def selling_price(saved_price, booked_rooms, inventory, on_track, sensitivity, lower_limit, upper_limit):
    """Return the clamped selling price. Each call starts from saved_price."""
    saved = float(saved_price)
    rooms = float(inventory or 0)
    if rooms <= 0:
        raw = saved
    else:
        booked_share = float(booked_rooms or 0) / rooms
        pace = float(on_track or 0)
        if pace <= 0:
            gap = 0.0 if float(booked_rooms or 0) <= 0 else 1.0
        else:
            gap = (booked_share - pace) / pace
        raw = saved * (1.0 + float(sensitivity) * gap)
    bounded = min(max(raw, float(lower_limit)), float(upper_limit))
    return _money(bounded)


def _connect():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


_schema_ready = False


def ensure_schema(conn=None):
    global _schema_ready
    if _schema_ready:
        return
    own = conn is None
    if own:
        conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS kite_hotel_setting (
            hotel_name VARCHAR(255) PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            sensitivity NUMERIC(8,4) NOT NULL,
            lower_limit NUMERIC(12,2) NOT NULL,
            upper_limit NUMERIC(12,2) NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_by VARCHAR NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS kite_push_day (
            hotel_name VARCHAR(255) NOT NULL,
            stay_date DATE NOT NULL,
            push_dynamic BOOLEAN NOT NULL DEFAULT FALSE,
            pricing_name VARCHAR NULL,
            PRIMARY KEY (hotel_name, stay_date)
        );
        """
    )
    cur.execute("ALTER TABLE kite_push_day ADD COLUMN IF NOT EXISTS pricing_name VARCHAR;")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS kite_price_anchor (
            hotel_name VARCHAR(255) NOT NULL,
            room_type VARCHAR(255) NOT NULL,
            stay_date DATE NOT NULL,
            anchor_at TIMESTAMP NOT NULL,
            PRIMARY KEY (hotel_name, room_type, stay_date)
        );
        """
    )
    conn.commit()
    cur.close()
    if own:
        conn.close()
    _schema_ready = True


def _named_models(hotel):
    from booking_system.dynamic_pricing import dynamic_pricing
    return {row["pricing_name"]: row for row in dynamic_pricing.list_for_hotel(hotel)}


def _day_limits(setting, named, pricing_name):
    if pricing_name:
        model = named.get(canonical_key(pricing_name))
        if not model or not model.get("enabled"):
            return None
        if model.get("sensitivity") is None or model.get("lower_limit") is None or model.get("upper_limit") is None:
            return None
        return model
    if (
        setting.get("enabled")
        and setting.get("sensitivity") is not None
        and setting.get("lower_limit") is not None
        and setting.get("upper_limit") is not None
    ):
        return setting
    return None


class KitePricing:
    def read_setting(self, hotel_name, room_type=None):
        hotel = canonical_key(hotel_name)
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT enabled, sensitivity, lower_limit, upper_limit
            FROM kite_hotel_setting
            WHERE hotel_name = %s
            """,
            (hotel,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return {
                "hotel_name": hotel,
                "enabled": False,
                "sensitivity": None,
                "lower_limit": None,
                "upper_limit": None,
            }
        return {
            "hotel_name": hotel,
            "enabled": bool(row[0]),
            "sensitivity": float(row[1]),
            "lower_limit": float(row[2]),
            "upper_limit": float(row[3]),
        }

    def save_setting(self, hotel_name, enabled, sensitivity, lower_limit, upper_limit, manager_name, room_type=None):
        hotel = canonical_key(hotel_name)
        if enabled:
            if sensitivity is None or lower_limit is None or upper_limit is None:
                return {"error": "Sensitivity, lower limit, and upper limit are required"}
            if float(sensitivity) < 0:
                return {"error": "Sensitivity must be zero or greater"}
            if float(lower_limit) < 0 or float(upper_limit) < 0:
                return {"error": "Limits must be zero or greater"}
            if float(lower_limit) > float(upper_limit):
                return {"error": "Lower limit must be at or below the upper limit"}
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM kite_hotel_setting WHERE hotel_name = %s", (hotel,))
        existing = cur.fetchone()
        if existing:
            cur.execute(
                """
                UPDATE kite_hotel_setting
                SET enabled = %s,
                    sensitivity = COALESCE(%s, sensitivity),
                    lower_limit = COALESCE(%s, lower_limit),
                    upper_limit = COALESCE(%s, upper_limit),
                    updated_at = CURRENT_TIMESTAMP,
                    updated_by = %s
                WHERE hotel_name = %s
                """,
                (
                    bool(enabled),
                    None if sensitivity is None else float(sensitivity),
                    None if lower_limit is None else float(lower_limit),
                    None if upper_limit is None else float(upper_limit),
                    manager_name,
                    hotel,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO kite_hotel_setting
                    (hotel_name, enabled, sensitivity, lower_limit, upper_limit, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    hotel,
                    bool(enabled),
                    float(sensitivity or 0),
                    float(lower_limit or 0),
                    float(upper_limit or 0),
                    manager_name,
                ),
            )
        if enabled:
            cur.execute(
                """
                INSERT INTO kite_price_anchor (hotel_name, room_type, stay_date, anchor_at)
                SELECT hotel_name, room_type, stay_date, CURRENT_TIMESTAMP
                FROM daily_pricing
                WHERE hotel_name = %s
                ON CONFLICT (hotel_name, room_type, stay_date) DO NOTHING
                """,
                (hotel,),
            )
        conn.commit()
        cur.close()
        conn.close()
        return self.read_setting(hotel)

    def set_push_day(self, hotel_name, stay_date, push_dynamic, pricing_name=None):
        hotel = canonical_key(hotel_name)
        stay = stay_date if isinstance(stay_date, date) else date.fromisoformat(str(stay_date)[:10])
        name = canonical_key(pricing_name) if pricing_name else None
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO kite_push_day (hotel_name, stay_date, push_dynamic, pricing_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (hotel_name, stay_date)
            DO UPDATE SET push_dynamic = EXCLUDED.push_dynamic,
                          pricing_name = EXCLUDED.pricing_name
            """,
            (hotel, stay, bool(push_dynamic), name),
        )
        conn.commit()
        cur.close()
        conn.close()
        return {
            "hotel_name": hotel,
            "stay_date": stay.isoformat(),
            "push_dynamic": bool(push_dynamic),
            "pricing_name": name or "",
        }

    def clear_pricing_name(self, hotel_name, pricing_name):
        hotel = canonical_key(hotel_name)
        name = canonical_key(pricing_name) if pricing_name else ""
        if not name:
            return {"cleared": 0}
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE kite_push_day
            SET pricing_name = NULL, push_dynamic = FALSE
            WHERE hotel_name = %s AND pricing_name = %s
            """,
            (hotel, name),
        )
        cleared = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return {"cleared": cleared}

    def set_push_dates(self, hotel_name, stay_dates, push_dynamic, pricing_name=None):
        hotel = canonical_key(hotel_name)
        name = canonical_key(pricing_name) if pricing_name else None
        days = []
        for stay_date in stay_dates or []:
            text = str(stay_date or "").strip()
            if not text:
                continue
            days.append(stay_date if isinstance(stay_date, date) else date.fromisoformat(text[:10]))
        ensure_schema()
        if days:
            conn = _connect()
            cur = conn.cursor()
            cur.executemany(
                """
                INSERT INTO kite_push_day (hotel_name, stay_date, push_dynamic, pricing_name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (hotel_name, stay_date)
                DO UPDATE SET push_dynamic = EXCLUDED.push_dynamic,
                              pricing_name = EXCLUDED.pricing_name
                """,
                [(hotel, stay, bool(push_dynamic), name) for stay in days],
            )
            conn.commit()
            cur.close()
            conn.close()
        return {
            "hotel_name": hotel,
            "stay_dates": [stay.isoformat() for stay in days],
            "push_dynamic": bool(push_dynamic),
            "pricing_name": name or "",
        }

    def note_anchor(self, hotel_name, room_type, stay_date):
        hotel = canonical_key(hotel_name)
        room = canonical_key(room_type)
        stay = stay_date if isinstance(stay_date, date) else date.fromisoformat(str(stay_date)[:10])
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO kite_price_anchor (hotel_name, room_type, stay_date, anchor_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (hotel_name, room_type, stay_date)
            DO UPDATE SET anchor_at = CURRENT_TIMESTAMP
            """,
            (hotel, room, stay),
        )
        conn.commit()
        cur.close()
        conn.close()

    def quote(self, hotel_name, room_type, stay_date, now=None):
        hotel = canonical_key(hotel_name)
        room = canonical_key(room_type)
        stay = stay_date if isinstance(stay_date, date) else date.fromisoformat(str(stay_date)[:10])
        setting = self.read_setting(hotel)
        named = _named_models(hotel)
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT base_price FROM daily_pricing
            WHERE hotel_name = %s AND room_type = %s AND stay_date = %s
            """,
            (hotel, room, stay),
        )
        price_row = cur.fetchone()
        base = float(price_row[0]) if price_row else None
        cur.execute(
            """
            SELECT push_dynamic, pricing_name FROM kite_push_day
            WHERE hotel_name = %s AND stay_date = %s
            """,
            (hotel, stay),
        )
        push_row = cur.fetchone()
        push_dynamic = bool(push_row[0]) if push_row else False
        pricing_name = push_row[1] if push_row and push_row[1] else ""
        limits = _day_limits(setting, named, pricing_name)
        dynamic = base
        if limits and base is not None:
            if now is None:
                from booking_system.timezone_utils import hotel_now_local
                try:
                    now = hotel_now_local(hotel)
                except Exception:
                    now = None
            if now is not None:
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT room_number)
                    FROM room_number_assignments
                    WHERE hotel_name = %s AND room_type = %s AND stay_date = %s
                    """,
                    (hotel, room, stay),
                )
                inv_row = cur.fetchone()
                inventory = int(inv_row[0]) if inv_row else 0
                cur.execute(
                    """
                    SELECT COALESCE(SUM(rooms_booked), 0)
                    FROM booking_stays
                    WHERE hotel_name = %s AND room_type = %s AND stay_date = %s
                      AND LOWER(COALESCE(booking_status, '')) <> 'cancelled'
                    """,
                    (hotel, room, stay),
                )
                booked = int(cur.fetchone()[0] or 0)
                cur.execute(
                    """
                    SELECT anchor_at FROM kite_price_anchor
                    WHERE hotel_name = %s AND room_type = %s AND stay_date = %s
                    """,
                    (hotel, room, stay),
                )
                anchor_row = cur.fetchone()
                pace = on_track_share(now, stay, anchor_row[0] if anchor_row else None)
                dynamic = selling_price(
                    base,
                    booked,
                    inventory,
                    pace,
                    limits["sensitivity"],
                    limits["lower_limit"],
                    limits["upper_limit"],
                )
        cur.close()
        conn.close()
        pushed = dynamic if push_dynamic and dynamic is not None else base
        return {
            "stay_date": stay.isoformat(),
            "room_type": room,
            "base_price": base,
            "dynamic_price": dynamic,
            "push_dynamic": push_dynamic,
            "pricing_name": pricing_name or "",
            "push_price": pushed,
        }

    def attach_quotes(self, hotel_name, start_date, end_date, room_types, conn=None):
        """Fill dynamic fields for every room type with one database connection."""
        hotel = canonical_key(hotel_name)
        start = date.fromisoformat(str(start_date)[:10])
        end = date.fromisoformat(str(end_date)[:10])
        ensure_schema()
        named = _named_models(hotel)
        own = conn is None
        if own:
            conn = _connect()
        else:
            try:
                conn.rollback()
            except Exception:
                pass
        cur = conn.cursor()
        cur.execute(
            """
            SELECT enabled, sensitivity, lower_limit, upper_limit
            FROM kite_hotel_setting
            WHERE hotel_name = %s
            """,
            (hotel,),
        )
        row = cur.fetchone()
        if not row:
            setting = {"enabled": False, "sensitivity": None, "lower_limit": None, "upper_limit": None}
        else:
            setting = {
                "enabled": bool(row[0]),
                "sensitivity": float(row[1]),
                "lower_limit": float(row[2]),
                "upper_limit": float(row[3]),
            }
        now = None
        has_limits = bool(_day_limits(setting, named, "")) or any(item.get("enabled") for item in named.values())
        if has_limits:
            from booking_system.timezone_utils import hotel_now_local
            now = hotel_now_local(hotel)
        cur.execute(
            """
            SELECT stay_date, push_dynamic, pricing_name FROM kite_push_day
            WHERE hotel_name = %s AND stay_date BETWEEN %s AND %s
            """,
            (hotel, start, end),
        )
        push_rows = cur.fetchall()
        pushes = {item[0]: bool(item[1]) for item in push_rows}
        names = {item[0]: (item[2] or "") for item in push_rows}
        cur.execute(
            """
            SELECT room_type, stay_date, COUNT(DISTINCT room_number)
            FROM room_number_assignments
            WHERE hotel_name = %s AND stay_date BETWEEN %s AND %s
            GROUP BY room_type, stay_date
            """,
            (hotel, start, end),
        )
        inventories = {}
        for room_type, stay_date, rooms_n in cur.fetchall():
            inventories.setdefault(room_type, {})[stay_date] = int(rooms_n or 0)
        cur.execute(
            """
            SELECT room_type, stay_date, COALESCE(SUM(rooms_booked), 0)
            FROM booking_stays
            WHERE hotel_name = %s AND stay_date BETWEEN %s AND %s
              AND LOWER(COALESCE(booking_status, '')) <> 'cancelled'
            GROUP BY room_type, stay_date
            """,
            (hotel, start, end),
        )
        booked_map = {}
        for room_type, stay_date, booked in cur.fetchall():
            booked_map.setdefault(room_type, {})[stay_date] = int(booked or 0)
        cur.execute(
            """
            SELECT room_type, stay_date, anchor_at FROM kite_price_anchor
            WHERE hotel_name = %s AND stay_date BETWEEN %s AND %s
            """,
            (hotel, start, end),
        )
        anchors = {}
        for room_type, stay_date, anchor_at in cur.fetchall():
            anchors.setdefault(room_type, {})[stay_date] = anchor_at
        cur.close()
        if own:
            conn.close()
        else:
            try:
                conn.rollback()
            except Exception:
                pass
        for room in room_types or []:
            room_key = canonical_key(room.get("room_type"))
            for price_row in room.get("pricing") or []:
                try:
                    day = date.fromisoformat(str(price_row.get("date"))[:10])
                except ValueError:
                    continue
                base = price_row.get("price")
                push_dynamic = pushes.get(day, False)
                pricing_name = names.get(day, "")
                limits = _day_limits(setting, named, pricing_name)
                dynamic = base
                if limits and base is not None and now is not None:
                    dynamic = selling_price(
                        base,
                        booked_map.get(room_key, {}).get(day, 0),
                        inventories.get(room_key, {}).get(day, 0),
                        on_track_share(now, day, anchors.get(room_key, {}).get(day)),
                        limits["sensitivity"],
                        limits["lower_limit"],
                        limits["upper_limit"],
                    )
                price_row["base_price"] = base
                price_row["dynamic_price"] = dynamic
                price_row["push_dynamic"] = push_dynamic
                price_row["pricing_name"] = pricing_name or ""

    def quotes_for_range(self, hotel_name, room_type, start_date, end_date):
        hotel = canonical_key(hotel_name)
        room = canonical_key(room_type)
        start = date.fromisoformat(str(start_date)[:10])
        end = date.fromisoformat(str(end_date)[:10])
        setting = self.read_setting(hotel)
        named = _named_models(hotel)
        now = None
        has_limits = bool(_day_limits(setting, named, "")) or any(row.get("enabled") for row in named.values())
        if has_limits:
            from booking_system.timezone_utils import hotel_now_local
            now = hotel_now_local(hotel)
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT stay_date, base_price FROM daily_pricing
            WHERE hotel_name = %s AND room_type = %s AND stay_date BETWEEN %s AND %s
            """,
            (hotel, room, start, end),
        )
        prices = {row[0]: float(row[1]) for row in cur.fetchall()}
        cur.execute(
            """
            SELECT stay_date, push_dynamic, pricing_name FROM kite_push_day
            WHERE hotel_name = %s AND stay_date BETWEEN %s AND %s
            """,
            (hotel, start, end),
        )
        push_rows = cur.fetchall()
        pushes = {row[0]: bool(row[1]) for row in push_rows}
        names = {row[0]: (row[2] or "") for row in push_rows}
        cur.execute(
            """
            SELECT stay_date, COUNT(DISTINCT room_number)
            FROM room_number_assignments
            WHERE hotel_name = %s AND room_type = %s AND stay_date BETWEEN %s AND %s
            GROUP BY stay_date
            """,
            (hotel, room, start, end),
        )
        inventories = {row[0]: int(row[1]) for row in cur.fetchall()}
        cur.execute(
            """
            SELECT stay_date, COALESCE(SUM(rooms_booked), 0)
            FROM booking_stays
            WHERE hotel_name = %s AND room_type = %s AND stay_date BETWEEN %s AND %s
              AND LOWER(COALESCE(booking_status, '')) <> 'cancelled'
            GROUP BY stay_date
            """,
            (hotel, room, start, end),
        )
        booked_map = {row[0]: int(row[1] or 0) for row in cur.fetchall()}
        cur.execute(
            """
            SELECT stay_date, anchor_at FROM kite_price_anchor
            WHERE hotel_name = %s AND room_type = %s AND stay_date BETWEEN %s AND %s
            """,
            (hotel, room, start, end),
        )
        anchors = {row[0]: row[1] for row in cur.fetchall()}
        cur.close()
        conn.close()
        quotes = []
        day = start
        while day <= end:
            base = prices.get(day)
            push_dynamic = pushes.get(day, False)
            pricing_name = names.get(day, "")
            limits = _day_limits(setting, named, pricing_name)
            dynamic = base
            if limits and base is not None and now is not None:
                dynamic = selling_price(
                    base,
                    booked_map.get(day, 0),
                    inventories.get(day, 0),
                    on_track_share(now, day, anchors.get(day)),
                    limits["sensitivity"],
                    limits["lower_limit"],
                    limits["upper_limit"],
                )
            pushed = dynamic if push_dynamic and dynamic is not None else base
            quotes.append({
                "stay_date": day.isoformat(),
                "room_type": room,
                "base_price": base,
                "dynamic_price": dynamic,
                "push_dynamic": push_dynamic,
                "pricing_name": pricing_name or "",
                "push_price": pushed,
            })
            day += timedelta(days=1)
        return quotes

    def rates_to_push(self, hotel_name, stay_date):
        hotel = canonical_key(hotel_name)
        stay = stay_date if isinstance(stay_date, date) else date.fromisoformat(str(stay_date)[:10])
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT room_type FROM daily_pricing
            WHERE hotel_name = %s AND stay_date = %s
            ORDER BY room_type
            """,
            (hotel, stay),
        )
        rooms = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        rates = []
        for room in rooms:
            quoted = self.quote(hotel, room, stay)
            if quoted.get("push_price") is None:
                continue
            rates.append({
                "room_type": room,
                "rate": quoted["push_price"],
                "push_dynamic": quoted["push_dynamic"],
            })
        return rates

    def rates_for_dates(self, hotel_name, stay_dates):
        """One quote pass per room type for the selected stay dates."""
        hotel = canonical_key(hotel_name)
        days = []
        for stay_date in stay_dates or []:
            text = str(stay_date or "").strip()
            if not text:
                continue
            days.append(stay_date if isinstance(stay_date, date) else date.fromisoformat(text[:10]))
        if not days:
            return {}
        start = min(days)
        end = max(days)
        wanted = {day.isoformat() for day in days}
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT room_type FROM daily_pricing
            WHERE hotel_name = %s AND stay_date BETWEEN %s AND %s
            ORDER BY room_type
            """,
            (hotel, start, end),
        )
        rooms = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        by_date = {day: [] for day in wanted}
        for room in rooms:
            for item in self.quotes_for_range(hotel, room, start.isoformat(), end.isoformat()):
                if item["stay_date"] not in wanted or item.get("push_price") is None:
                    continue
                by_date[item["stay_date"]].append({
                    "room_type": room,
                    "rate": item["push_price"],
                })
        return by_date


kite_pricing = KitePricing()


def _self_check():
    tz = datetime.now().astimezone().tzinfo
    stay = date(2026, 12, 1)
    now = datetime(2026, 11, 9, tzinfo=tz)
    pace = on_track_share(now, stay, datetime(2026, 8, 1, tzinfo=tz))
    assert abs(pace - 0.075) < 0.002
    assert selling_price(100, 3, 20, 0.075, 0.30, 70, 150) == 130.00
    assert selling_price(100, 1.5, 20, 0.075, 0.30, 70, 150) == 100.00
    assert selling_price(100, 0, 20, 0.075, 0.30, 70, 150) == 70.00
    assert selling_price(100, 0, 20, 0.075, 0.30, 80, 150) == 80.00
    assert selling_price(100, 20, 20, 0.075, 0.30, 70, 150) == 150.00


if __name__ == "__main__":
    _self_check()
    print("kite pricing checks passed")
