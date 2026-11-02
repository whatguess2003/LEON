"""INAP Kite pricing.

Hotel-wide sensitivity and limits. The nightly base price stays what bookings
charge. The dynamic price is calculated only for display and for the rate
sent to Aiosell when that stay date is toggled on.

kite_hotel_setting and kite_push_day are keyed by hotel_id, kite_price_anchor by room_type_id and stay date.
"""
from datetime import datetime, timedelta, date
from decimal import Decimal, ROUND_HALF_UP

from booking_system import db
from booking_system.llm_utils import canonical_key

# (days before the stay date at local midnight, booked share). The last
# checkpoint is 18:00 on the stay date and is stored with days = 0 and hour 18.
PACE_CHECKPOINTS = (
    (30, 0.05, 0),
    (14, 0.10, 0),
    (7, 0.20, 0),
    (1, 0.50, 0),
    (0, 0.80, 18),
)
LIVE_NIGHTS = ["booked", "checked_in"]


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


def _named_models(hotel_id):
    from booking_system import dynamic_pricing
    return {row["pricing_name"]: row for row in dynamic_pricing.list_for_hotel(hotel_id)}


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


def _setting_row(row):
    if not row:
        return {"enabled": False, "sensitivity": None, "lower_limit": None, "upper_limit": None}
    return {"enabled": bool(row["enabled"]), "sensitivity": float(row["sensitivity"]), "lower_limit": float(row["lower_limit"]),
            "upper_limit": float(row["upper_limit"])}


def _hotel_now(hotel_id):
    from zoneinfo import ZoneInfo
    from booking_system.hotel_registry import read_hotel_by_id
    return datetime.now(ZoneInfo(read_hotel_by_id(hotel_id)["timezone"]))


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value).strip()[:10])


def _range_facts(cur, hotel_id, start, end, room_type=None):
    """Prices, push days, inventory, live nights and anchors between start and end, by (room type name, stay_date)."""
    room = canonical_key(room_type) if room_type else None
    scope = (hotel_id, start, end, room, room)
    where = " WHERE x.hotel_id = %s AND x.stay_date BETWEEN %s AND %s AND (%s::text IS NULL OR t.room_type_name = %s)"
    joined = " x JOIN room_type t ON t.room_type_id = x.room_type_id"
    cur.execute("SELECT t.room_type_name, x.stay_date, x.base_price FROM daily_pricing" + joined + where, scope)
    prices = {(row["room_type_name"], row["stay_date"]): float(row["base_price"]) for row in cur.fetchall()}
    cur.execute("SELECT t.room_type_name, x.stay_date, x.total_inventory FROM room_inventory_nightly" + joined + where, scope)
    inventories = {(row["room_type_name"], row["stay_date"]): int(row["total_inventory"]) for row in cur.fetchall()}
    cur.execute("SELECT t.room_type_name, x.stay_date, COUNT(*) AS nights FROM booking_room_night" + joined + where
                + " AND x.night_status = ANY(%s) GROUP BY 1, 2", scope + (LIVE_NIGHTS,))
    booked = {(row["room_type_name"], row["stay_date"]): int(row["nights"]) for row in cur.fetchall()}
    cur.execute("SELECT t.room_type_name, x.stay_date, x.anchor_at FROM kite_price_anchor" + joined + where, scope)
    anchors = {(row["room_type_name"], row["stay_date"]): row["anchor_at"] for row in cur.fetchall()}
    cur.execute("SELECT stay_date, push_dynamic, pricing_name FROM kite_push_day WHERE hotel_id = %s AND stay_date BETWEEN %s AND %s",
                (hotel_id, start, end))
    push_rows = cur.fetchall()
    cur.execute("SELECT enabled, sensitivity, lower_limit, upper_limit FROM kite_hotel_setting WHERE hotel_id = %s", (hotel_id,))
    setting = _setting_row(cur.fetchone())
    return {
        "prices": prices, "inventories": inventories, "booked": booked, "anchors": anchors, "setting": setting,
        "pushes": {row["stay_date"]: bool(row["push_dynamic"]) for row in push_rows},
        "names": {row["stay_date"]: (row["pricing_name"] or "") for row in push_rows},
    }


def _quote(facts, named, now, room, day):
    base = facts["prices"].get((room, day))
    pricing_name = facts["names"].get(day, "")
    limits = _day_limits(facts["setting"], named, pricing_name)
    dynamic = base
    if limits and base is not None and now is not None:
        dynamic = selling_price(
            base,
            facts["booked"].get((room, day), 0),
            facts["inventories"].get((room, day), 0),
            on_track_share(now, day, facts["anchors"].get((room, day))),
            limits["sensitivity"],
            limits["lower_limit"],
            limits["upper_limit"],
        )
    push_dynamic = facts["pushes"].get(day, False)
    return {
        "stay_date": day.isoformat(),
        "room_type": room,
        "base_price": base,
        "dynamic_price": dynamic,
        "push_dynamic": push_dynamic,
        "pricing_name": pricing_name or "",
        "push_price": dynamic if push_dynamic and dynamic is not None else base,
    }


def _now_when_priced(hotel_id, setting, named):
    has_limits = bool(_day_limits(setting, named, "")) or any(row.get("enabled") for row in named.values())
    return _hotel_now(hotel_id) if has_limits else None


class KitePricing:
    def read_setting(self, hotel_id):
        found = db.row("SELECT enabled, sensitivity, lower_limit, upper_limit FROM kite_hotel_setting WHERE hotel_id = %s", (hotel_id,))
        return dict(_setting_row(found), hotel_id=hotel_id)

    def save_setting(self, hotel_id, user_id, enabled, sensitivity, lower_limit, upper_limit):
        """READ first: update the hotel's row (a value left out keeps the saved one), else create it. Switching on anchors every
        priced night that has no anchor yet."""
        if enabled:
            if sensitivity is None or lower_limit is None or upper_limit is None:
                return {"error": "Sensitivity, lower limit, and upper limit are required"}
            if float(sensitivity) < 0:
                return {"error": "Sensitivity must be zero or greater"}
            if float(lower_limit) < 0 or float(upper_limit) < 0:
                return {"error": "Limits must be zero or greater"}
            if float(lower_limit) > float(upper_limit):
                return {"error": "Lower limit must be at or below the upper limit"}
        values = tuple(None if value is None else float(value) for value in (sensitivity, lower_limit, upper_limit))

        def run(cur):
            cur.execute("SELECT 1 FROM kite_hotel_setting WHERE hotel_id = %s FOR UPDATE", (hotel_id,))
            if cur.fetchone():
                cur.execute(
                    """UPDATE kite_hotel_setting SET enabled = %s, sensitivity = COALESCE(%s, sensitivity),
                              lower_limit = COALESCE(%s, lower_limit), upper_limit = COALESCE(%s, upper_limit),
                              updated_at = now(), updated_by_user_id = %s
                       WHERE hotel_id = %s""",
                    (bool(enabled),) + values + (user_id, hotel_id))
            else:
                cur.execute(
                    """INSERT INTO kite_hotel_setting (hotel_id, enabled, sensitivity, lower_limit, upper_limit, updated_by_user_id)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (hotel_id, bool(enabled)) + tuple(value or 0 for value in values) + (user_id,))
            if enabled:
                cur.execute(
                    """INSERT INTO kite_price_anchor (hotel_id, room_type_id, stay_date, anchor_at)
                       SELECT hotel_id, room_type_id, stay_date, now() FROM daily_pricing WHERE hotel_id = %s
                       ON CONFLICT (room_type_id, stay_date) DO NOTHING""",
                    (hotel_id,))
        db.transaction(run)
        return self.read_setting(hotel_id)

    def set_push_day(self, hotel_id, stay_date, push_dynamic, pricing_name=None):
        saved = self.set_push_dates(hotel_id, [stay_date], push_dynamic, pricing_name)
        return {"hotel_id": hotel_id, "stay_date": saved["stay_dates"][0], "push_dynamic": saved["push_dynamic"],
                "pricing_name": saved["pricing_name"]}

    def clear_pricing_name(self, hotel_id, pricing_name):
        name = canonical_key(pricing_name) if pricing_name else ""
        if not name:
            return {"cleared": 0}
        cleared = db.execute("UPDATE kite_push_day SET pricing_name = NULL, push_dynamic = FALSE WHERE hotel_id = %s AND pricing_name = %s",
                             (hotel_id, name))
        return {"cleared": cleared}

    def set_push_dates(self, hotel_id, stay_dates, push_dynamic, pricing_name=None):
        name = canonical_key(pricing_name) if pricing_name else None
        days = [_day(stay) for stay in stay_dates or [] if str(stay or "").strip()]
        db.execute(
            """INSERT INTO kite_push_day (hotel_id, stay_date, push_dynamic, pricing_name)
               SELECT %s, d, %s, %s FROM unnest(%s::date[]) AS d
               ON CONFLICT (hotel_id, stay_date) DO UPDATE SET push_dynamic = EXCLUDED.push_dynamic, pricing_name = EXCLUDED.pricing_name""",
            (hotel_id, bool(push_dynamic), name, days),
        )
        return {"hotel_id": hotel_id, "stay_dates": [stay.isoformat() for stay in days], "push_dynamic": bool(push_dynamic),
                "pricing_name": name or ""}

    def quote(self, hotel_id, room_type, stay_date, now=None):
        stay = _day(stay_date).isoformat()
        return self.quotes_for_range(hotel_id, room_type, stay, stay, now)[0]

    def attach_quotes(self, hotel_id, start_date, end_date, room_types):
        """Fill dynamic fields on each room type's pricing rows ({'room_type', 'pricing': [{'date', 'price', ...}]}) in one read."""
        start, end = _day(start_date), _day(end_date)
        named = _named_models(hotel_id)
        facts = db.transaction(lambda cur: _range_facts(cur, hotel_id, start, end))
        now = _now_when_priced(hotel_id, facts["setting"], named)
        for room in room_types or []:
            room_key = canonical_key(room.get("room_type"))
            for price_row in room.get("pricing") or []:
                quoted = _quote(facts, named, now, room_key, _day(price_row.get("date")))
                price_row["base_price"] = quoted["base_price"]
                price_row["dynamic_price"] = quoted["dynamic_price"]
                price_row["push_dynamic"] = quoted["push_dynamic"]
                price_row["pricing_name"] = quoted["pricing_name"]

    def quotes_for_range(self, hotel_id, room_type, start_date, end_date, now=None):
        room = canonical_key(room_type)
        start, end = _day(start_date), _day(end_date)
        named = _named_models(hotel_id)
        facts = db.transaction(lambda cur: _range_facts(cur, hotel_id, start, end, room))
        moment = now or _now_when_priced(hotel_id, facts["setting"], named)
        return [_quote(facts, named, moment, room, start + timedelta(days=offset)) for offset in range((end - start).days + 1)]


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
