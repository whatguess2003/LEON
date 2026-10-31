"""Curated general recipes for standard hotel terms. Written directly to the catalog; no agent or reviewer."""

SOURCE = "general_seed"
PARENT = "B01"
ACTIVE = "NOT IN ('cancelled', 'no_show')"


def _scope(alias=""):
    return "UPPER(" + alias + "hotel_name) = UPPER(%(hotel_name)s)"


def _local(column):
    """A stored UTC timestamp as the hotel-local date; the session TimeZone is the hotel's configured zone."""
    return "(" + column + " AT TIME ZONE 'UTC' AT TIME ZONE current_setting('TimeZone'))::date"


_PERIODS = """periods AS (
    SELECT to_char(CURRENT_DATE + g.n, 'YYYY-MM-DD') AS period, CURRENT_DATE + g.n AS period_start, CURRENT_DATE + g.n AS period_end
    FROM generate_series(-7, 7) AS g(n)
    UNION ALL
    SELECT r.period, r.period_start, r.period_end FROM (VALUES
        ('last_7_days', CURRENT_DATE - 7, CURRENT_DATE - 1),
        ('next_7_days', CURRENT_DATE + 1, CURRENT_DATE + 7),
        ('last_30_days', CURRENT_DATE - 30, CURRENT_DATE - 1),
        ('next_30_days', CURRENT_DATE + 1, CURRENT_DATE + 30),
        ('this_week', date_trunc('week', CURRENT_DATE)::date, date_trunc('week', CURRENT_DATE)::date + 6),
        ('last_week', date_trunc('week', CURRENT_DATE)::date - 7, date_trunc('week', CURRENT_DATE)::date - 1),
        ('next_week', date_trunc('week', CURRENT_DATE)::date + 7, date_trunc('week', CURRENT_DATE)::date + 13),
        ('month_to_date', date_trunc('month', CURRENT_DATE)::date, CURRENT_DATE),
        ('this_month', date_trunc('month', CURRENT_DATE)::date, (date_trunc('month', CURRENT_DATE) + interval '1 month')::date - 1),
        ('last_month', (date_trunc('month', CURRENT_DATE) - interval '1 month')::date, date_trunc('month', CURRENT_DATE)::date - 1),
        ('next_month', (date_trunc('month', CURRENT_DATE) + interval '1 month')::date, (date_trunc('month', CURRENT_DATE) + interval '2 month')::date - 1),
        ('year_to_date', date_trunc('year', CURRENT_DATE)::date, CURRENT_DATE)
    ) AS r(period, period_start, period_end)
),
span AS (
    SELECT generate_series(MIN(period_start), MAX(period_end), interval '1 day')::date AS d FROM periods
)"""

_PERIOD_NAMES = (
    "each date from 7 days before through 7 days after the hotel-local today (period is that date, YYYY-MM-DD), "
    "last_7_days, next_7_days, last_30_days, next_30_days, this_week, last_week, next_week (weeks start Monday), "
    "month_to_date, this_month, last_month, next_month, year_to_date"
)

_HOTEL_CURRENCY = (
    "hotel_currency AS (SELECT MAX(body->>'hotel_currency') AS code FROM content_document "
    "WHERE collection_name = 'manager_collection' AND UPPER(body->>'hotel_name') = UPPER(%(hotel_name)s))"
)

# Per-date CTEs: (sql, columns where no row means zero, columns where no row means unknown).
_FACTS = {
    "hotel_currency": (_HOTEL_CURRENCY, [], []),
    "guest_counts": (
        "guest_counts AS (SELECT DISTINCT ON (booking_id) booking_id, guest_count FROM booking_guest_counts "
        "WHERE " + _scope() + " ORDER BY booking_id, recorded_at DESC)",
        [], [],
    ),
    "inventory": (
        "inventory AS (SELECT stay_date AS d, SUM(total_inventory) AS inventory_rooms, "
        "SUM(total_inventory - blocked_quantity) AS sellable_rooms "
        "FROM room_inventory_nightly WHERE " + _scope() + " GROUP BY stay_date)",
        [], ["inventory_rooms", "sellable_rooms"],
    ),
    "nightly": (
        "nightly AS (SELECT stay_date AS d, "
        "SUM(rooms_booked) FILTER (WHERE booking_status " + ACTIVE + ") AS sold_active, "
        "SUM(rooms_booked) FILTER (WHERE booking_status = 'confirmed') AS sold_confirmed, "
        "SUM(rooms_booked) FILTER (WHERE booking_status = 'cancelled') AS cancelled_room_nights, "
        "COUNT(DISTINCT booking_id) FILTER (WHERE booking_status " + ACTIVE + ") AS stay_bookings_active "
        "FROM booking_stays WHERE " + _scope() + " GROUP BY stay_date)",
        ["sold_active", "sold_confirmed", "cancelled_room_nights", "stay_bookings_active"], [],
    ),
    "header": (
        "header AS (SELECT b.check_in_date + k.i AS d, "
        "SUM(b.rooms_booked) FILTER (WHERE b.booking_status " + ACTIVE + ") AS header_active "
        "FROM room_bookings b CROSS JOIN LATERAL generate_series(0, b.nights - 1) AS k(i) "
        "WHERE " + _scope("b.") + " GROUP BY 1)",
        ["header_active"], [],
    ),
    "checked": (
        "checked AS (SELECT stay_date AS d, COUNT(*) AS checked_in_rooms FROM room_stay WHERE " + _scope() + " "
        "AND checked_in_at IS NOT NULL AND (checked_out_at IS NULL OR " + _local("checked_out_at") + " > stay_date) GROUP BY stay_date)",
        ["checked_in_rooms"], [],
    ),
    "guests_night": (
        "guests_night AS (SELECT n.stay_date AS d, SUM(g.guest_count) AS guests_known_active, "
        "COUNT(*) FILTER (WHERE g.booking_id IS NULL) AS bookings_missing_guest_count, "
        "SUM(g.guest_count) FILTER (WHERE n.checked_in) AS guests_known_checked_in "
        "FROM (SELECT s.stay_date, s.booking_id, BOOL_OR(r.checked_in_at IS NOT NULL AND (r.checked_out_at IS NULL OR "
        + _local("r.checked_out_at") + " > s.stay_date)) AS checked_in "
        "FROM booking_stays s LEFT JOIN room_stay r ON r.booking_id = s.booking_id AND r.stay_date = s.stay_date AND " + _scope("r.") + " "
        "WHERE " + _scope("s.") + " AND s.booking_status " + ACTIVE + " GROUP BY s.stay_date, s.booking_id) n "
        "LEFT JOIN guest_counts g ON g.booking_id = n.booking_id GROUP BY n.stay_date)",
        ["guests_known_active", "bookings_missing_guest_count", "guests_known_checked_in"], [],
    ),
    "revenue": (
        "stay_amounts AS (SELECT s.stay_date, s.rooms_booked, dp.base_price, dp.saved_price, b.currency, "
        "b.total_amount * s.rooms_booked / NULLIF(SUM(s.rooms_booked) OVER (PARTITION BY s.booking_id), 0) AS booking_total_share, "
        "t.total_amount * s.rooms_booked / NULLIF(SUM(s.rooms_booked) OVER (PARTITION BY s.booking_id), 0) AS tracking_total_share "
        "FROM booking_stays s JOIN room_bookings b ON b.booking_id = s.booking_id AND " + _scope("b.") + " "
        "LEFT JOIN daily_pricing dp ON dp.room_type = s.room_type AND dp.stay_date = s.stay_date AND " + _scope("dp.") + " "
        "LEFT JOIN payment_tracking t ON t.booking_id = s.booking_id "
        "WHERE " + _scope("s.") + " AND s.booking_status " + ACTIVE + "),\n"
        "revenue AS (SELECT stay_date AS d, "
        "SUM(rooms_booked * base_price) AS rate_card_base, SUM(rooms_booked) FILTER (WHERE base_price IS NOT NULL) AS nights_rate_card, "
        "SUM(rooms_booked * base_price) FILTER (WHERE base_price > 0) AS rate_card_base_nonzero, "
        "SUM(rooms_booked) FILTER (WHERE base_price > 0) AS nights_rate_card_nonzero, "
        "SUM(rooms_booked * saved_price) AS rate_card_saved, "
        "SUM(booking_total_share) AS rev_booking_total, SUM(rooms_booked) FILTER (WHERE booking_total_share IS NOT NULL) AS nights_booking_total, "
        "SUM(tracking_total_share) AS rev_tracking_total, SUM(rooms_booked) FILTER (WHERE tracking_total_share IS NOT NULL) AS nights_tracking_total, "
        "SUM(rooms_booked) FILTER (WHERE currency <> (SELECT code FROM hotel_currency)) AS nights_foreign_currency, "
        "SUM(rooms_booked) FILTER (WHERE currency IS NULL) AS nights_unknown_currency, "
        "SUM(rooms_booked) FILTER (WHERE base_price > 0 AND tracking_total_share > 10 * rooms_booked * base_price) AS nights_implausible_tracking "
        "FROM stay_amounts GROUP BY stay_date)",
        ["nights_rate_card", "nights_rate_card_nonzero", "nights_booking_total", "nights_tracking_total",
         "nights_foreign_currency", "nights_unknown_currency", "nights_implausible_tracking"],
        ["rate_card_base", "rate_card_base_nonzero", "rate_card_saved", "rev_booking_total", "rev_tracking_total"],
    ),
    "arrivals": (
        "arrivals AS (SELECT b.check_in_date AS d, "
        "COUNT(*) FILTER (WHERE b.booking_status = 'confirmed') AS arr_bookings_confirmed, "
        "COUNT(*) FILTER (WHERE b.booking_status " + ACTIVE + ") AS arr_bookings_active, "
        "SUM(b.rooms_booked) FILTER (WHERE b.booking_status " + ACTIVE + ") AS arr_rooms_active, "
        "SUM(g.guest_count) FILTER (WHERE b.booking_status " + ACTIVE + ") AS arr_guests_known_active, "
        "COUNT(*) FILTER (WHERE b.booking_status " + ACTIVE + " AND g.booking_id IS NULL) AS arr_missing_guest_count, "
        "SUM(b.nights) FILTER (WHERE b.booking_status = 'confirmed') AS arr_nights_confirmed, "
        "SUM(b.nights) FILTER (WHERE b.booking_status " + ACTIVE + ") AS arr_nights_active, "
        "SUM(b.nights * b.rooms_booked) FILTER (WHERE b.booking_status " + ACTIVE + ") AS arr_room_nights_active, "
        "SUM(b.check_in_date - " + _local("b.created_at") + ") FILTER (WHERE b.booking_status " + ACTIVE + " AND b.check_in_date >= " + _local("b.created_at") + ") AS lead_days_active, "
        "COUNT(*) FILTER (WHERE b.booking_status " + ACTIVE + " AND b.check_in_date >= " + _local("b.created_at") + ") AS lead_bookings_active, "
        "COUNT(*) FILTER (WHERE b.booking_status " + ACTIVE + " AND b.check_in_date < " + _local("b.created_at") + ") AS negative_lead_bookings, "
        "SUM(b.check_in_date - " + _local("b.created_at") + ") FILTER (WHERE b.check_in_date >= " + _local("b.created_at") + ") AS lead_days_all, "
        "COUNT(*) FILTER (WHERE b.check_in_date >= " + _local("b.created_at") + ") AS lead_bookings_all, "
        "COUNT(*) FILTER (WHERE b.booking_status = 'cancelled') AS cancelled_by_arrival, "
        "COUNT(*) FILTER (WHERE b.booking_status = 'no_show' OR b.no_show_at IS NOT NULL) AS no_show_by_arrival, "
        "COUNT(*) FILTER (WHERE b.booking_status " + ACTIVE + " AND b.no_show_at IS NULL AND b.check_in_date < CURRENT_DATE "
        "AND NOT EXISTS (SELECT 1 FROM room_stay r WHERE r.booking_id = b.booking_id AND " + _scope("r.") + " "
        "AND r.checked_in_at IS NOT NULL)) AS past_arrivals_never_checked_in "
        "FROM room_bookings b LEFT JOIN guest_counts g ON g.booking_id = b.booking_id "
        "WHERE " + _scope("b.") + " GROUP BY b.check_in_date)",
        ["arr_bookings_confirmed", "arr_bookings_active", "arr_rooms_active", "arr_guests_known_active", "arr_missing_guest_count",
         "arr_nights_confirmed", "arr_nights_active", "arr_room_nights_active", "lead_days_active", "lead_bookings_active",
         "negative_lead_bookings", "lead_days_all", "lead_bookings_all", "cancelled_by_arrival", "no_show_by_arrival",
         "past_arrivals_never_checked_in"],
        [],
    ),
    "actual_in": (
        "actual_in AS (SELECT " + _local("checked_in_at") + " AS d, COUNT(DISTINCT booking_id) AS actual_checkins "
        "FROM room_stay WHERE " + _scope() + " AND checked_in_at IS NOT NULL GROUP BY 1)",
        ["actual_checkins"], [],
    ),
    "actual_out": (
        "actual_out AS (SELECT " + _local("checked_out_at") + " AS d, COUNT(DISTINCT booking_id) AS actual_checkouts "
        "FROM room_stay WHERE " + _scope() + " AND checked_out_at IS NOT NULL GROUP BY 1)",
        ["actual_checkouts"], [],
    ),
    "departures": (
        "departures AS (SELECT b.check_in_date + b.nights AS d, "
        "COUNT(*) FILTER (WHERE b.booking_status = 'confirmed') AS dep_bookings_confirmed, "
        "COUNT(*) FILTER (WHERE b.booking_status " + ACTIVE + ") AS dep_bookings_active, "
        "SUM(b.rooms_booked) FILTER (WHERE b.booking_status " + ACTIVE + ") AS dep_rooms_active, "
        "SUM(g.guest_count) FILTER (WHERE b.booking_status " + ACTIVE + ") AS dep_guests_known_active, "
        "COUNT(*) FILTER (WHERE b.booking_status " + ACTIVE + " AND g.booking_id IS NULL) AS dep_missing_guest_count "
        "FROM room_bookings b LEFT JOIN guest_counts g ON g.booking_id = b.booking_id "
        "WHERE " + _scope("b.") + " GROUP BY 1)",
        ["dep_bookings_confirmed", "dep_bookings_active", "dep_rooms_active", "dep_guests_known_active", "dep_missing_guest_count"], [],
    ),
    "created": (
        "created AS (SELECT " + _local("created_at") + " AS d, COUNT(*) AS created_all, "
        "COUNT(*) FILTER (WHERE booking_status " + ACTIVE + ") AS created_active, "
        "COUNT(*) FILTER (WHERE booking_status = 'cancelled') AS created_cancelled_since, "
        "SUM(rooms_booked * nights) FILTER (WHERE booking_status " + ACTIVE + ") AS created_room_nights_active "
        "FROM room_bookings WHERE " + _scope() + " AND created_at IS NOT NULL GROUP BY 1)",
        ["created_all", "created_active", "created_cancelled_since", "created_room_nights_active"], [],
    ),
    "cancel_updates": (
        "cancel_updates AS (SELECT " + _local("updated_at") + " AS d, COUNT(*) AS cancelled_by_last_update "
        "FROM room_bookings WHERE " + _scope() + " AND booking_status = 'cancelled' AND updated_at IS NOT NULL GROUP BY 1)",
        ["cancelled_by_last_update"], [],
    ),
    "cancel_messages": (
        "cancel_messages AS (SELECT " + _local("updated_at") + " AS d, COUNT(DISTINCT booking_id) AS cancel_messages "
        "FROM channel_reservations WHERE " + _scope() + " AND action = 'cancel' AND updated_at IS NOT NULL GROUP BY 1)",
        ["cancel_messages"], [],
    ),
    "no_show_marks": (
        "no_show_marks AS (SELECT " + _local("no_show_at") + " AS d, COUNT(*) AS no_show_marked_on_date "
        "FROM room_bookings WHERE " + _scope() + " AND no_show_at IS NOT NULL GROUP BY 1)",
        ["no_show_marked_on_date"], [],
    ),
}


def _period_sql(facts, columns):
    """One row per named hotel-local period, summed from per-date facts."""
    ctes = [_FACTS[name][0] for name in facts]
    joined = [name for name in facts if _FACTS[name][1] or _FACTS[name][2]]
    picks = []
    for name in joined:
        picks += ["COALESCE(" + name + "." + col + ", 0) AS " + col for col in _FACTS[name][1]]
        picks += [name + "." + col for col in _FACTS[name][2]]
    daily = (
        "daily AS (SELECT span.d, " + ", ".join(picks) + " FROM span "
        + " ".join("LEFT JOIN " + name + " ON " + name + ".d = span.d" for name in joined) + ")"
    )
    return (
        "WITH " + ",\n".join([_PERIODS] + ctes + [daily]) + "\n"
        "SELECT UPPER(%(hotel_name)s) AS hotel_name, p.period, p.period_start, p.period_end, "
        "p.period_end - p.period_start + 1 AS days_in_period,\n    "
        + ",\n    ".join(expr + " AS " + name for name, expr in columns) + "\n"
        "FROM periods p JOIN daily x ON x.d BETWEEN p.period_start AND p.period_end\n"
        "GROUP BY p.period, p.period_start, p.period_end\n"
        "ORDER BY p.period_end - p.period_start, p.period_start"
    )


def _sum(col):
    return "SUM(x." + col + ")"


def _inv(col):
    """Only dates that have an inventory row, so a numerator never outruns its denominator."""
    return "SUM(x." + col + ") FILTER (WHERE x.inventory_rooms IS NOT NULL)"


def _rate(num, den, scale="1.0"):
    return "ROUND(" + scale + " * SUM(x." + num + ") / NULLIF(SUM(x." + den + "), 0), 2)"


def _inv_rate(num, den, scale="1.0"):
    return "ROUND(" + scale + " * " + _inv(num) + " / NULLIF(SUM(x." + den + "), 0), 2)"


def _days(col):
    return "COUNT(x." + col + ")"


_PERIOD_KEYS = ["hotel_name", "period", "period_start", "period_end", "days_in_period"]
_COMMON_LIMITS = [
    "Rows cover only these hotel-local periods: " + _PERIOD_NAMES + ". Another calendar date or range is not answered by this recipe.",
    "active means booking_status is neither cancelled nor no_show; confirmed means booking_status = 'confirmed'. A NULL status is in no status reading.",
    "Stored timestamps (created_at, updated_at, checked_in_at, checked_out_at, no_show_at, payment_date) are UTC without a zone; each is converted to the hotel's configured time zone before its date is taken.",
    "Every reading reflects the current recorded state, not a snapshot taken on that date.",
]
_INVENTORY_LIMIT = (
    "Denominators come from room_inventory_nightly. A date without an inventory row adds nothing to the denominator, and its "
    "numerator is left out too; compare days_with_inventory with days_in_period to see partial coverage."
)
_MONEY_LIMITS = [
    "Amounts are in each booking's currency as recorded. hotel_currency is the hotel's configured currency; mixed_currency is true when "
    "any counted night belongs to a booking in another currency, and unknown_currency_room_nights counts nights whose booking has no recorded currency. "
    "Do not state a total as one currency when mixed_currency is true.",
    "implausible_tracking_room_nights counts nights whose payment_tracking share is more than 10 times that night's rate-card base_price.",
    "No reading is net of OTA commission. Taxes, fees, and non-room charges are not separated.",
]
_MONEY_FLAGS = [
    ("hotel_currency", "(SELECT code FROM hotel_currency)"),
    ("mixed_currency", "SUM(x.nights_foreign_currency) > 0"),
    ("foreign_currency_room_nights", _sum("nights_foreign_currency")),
    ("unknown_currency_room_nights", _sum("nights_unknown_currency")),
    ("implausible_tracking_room_nights", _sum("nights_implausible_tracking")),
]


def _recipe(catalog_id, title, problem, keyphrases, default, concepts, columns, limitations, facts=None, sql=None, grain="night", keys=None):
    outputs = list(keys or _PERIOD_KEYS) + [name for name, _expr in columns]
    return {
        "catalog_id": catalog_id,
        "parent": PARENT,
        "title": title,
        "purpose": problem,
        "problem": problem,
        "preconditions": [
            "The hotel has a configured time zone; CURRENT_DATE is the hotel-local today.",
            "Only hotel_name is bound. Select the row whose period is the requested day or range.",
        ],
        "inputs": {
            "hotel_name": "bound by the platform",
            "date_or_range": "chosen by row: period, period_start, period_end (inclusive hotel-local dates)",
        },
        "output_grain": grain,
        "outputs": outputs,
        "default_output": default,
        "limitations": _COMMON_LIMITS + ["Default reading: " + default + ". The other readings are alternatives a manager definition may choose."] + list(limitations),
        "verification": "Executed for the validation hotel; yesterday, today, and month_to_date rows checked against independent reads of the source tables.",
        "keyphrases": list(keyphrases),
        "aliases": list(keyphrases),
        "concepts": [{"name": name, "formula": formula, "tables": tables} for name, formula, tables in concepts],
        "solution": {
            "sql_template": sql or _period_sql(facts, columns),
            "parameters": ["hotel_name"],
            "method": SOURCE,
        },
        "validation": {"hotel_name": "JACKHOTEL", "expected_value": ""},
    }


_CHANNEL_SQL = (
    "WITH " + _PERIODS + ",\n"
    "ch_nights AS (SELECT COALESCE(NULLIF(b.channel, ''), 'UNKNOWN') AS channel, s.stay_date, s.rooms_booked "
    "FROM booking_stays s JOIN room_bookings b ON b.booking_id = s.booking_id AND " + _scope("b.") + " "
    "WHERE " + _scope("s.") + " AND s.booking_status " + ACTIVE + "),\n"
    "ch_created AS (SELECT COALESCE(NULLIF(channel, ''), 'UNKNOWN') AS channel, " + _local("created_at") + " AS created_on "
    "FROM room_bookings WHERE " + _scope() + " AND booking_status " + ACTIVE + " AND created_at IS NOT NULL),\n"
    "channels AS (SELECT DISTINCT COALESCE(NULLIF(channel, ''), 'UNKNOWN') AS channel FROM room_bookings WHERE " + _scope() + "),\n"
    "counts AS (SELECT p.period, p.period_start, p.period_end, c.channel, "
    "COALESCE((SELECT SUM(n.rooms_booked) FROM ch_nights n WHERE n.channel = c.channel AND n.stay_date BETWEEN p.period_start AND p.period_end), 0) AS room_nights_active, "
    "(SELECT COUNT(*) FROM ch_created k WHERE k.channel = c.channel AND k.created_on BETWEEN p.period_start AND p.period_end) AS bookings_created_active "
    "FROM periods p CROSS JOIN channels c)\n"
    "SELECT UPPER(%(hotel_name)s) AS hotel_name, period, period_start, period_end, period_end - period_start + 1 AS days_in_period, channel, "
    "ROUND(100.0 * room_nights_active / NULLIF(SUM(room_nights_active) OVER (PARTITION BY period), 0), 2) AS room_nights_share_pct, "
    "room_nights_active, bookings_created_active, "
    "ROUND(100.0 * bookings_created_active / NULLIF(SUM(bookings_created_active) OVER (PARTITION BY period), 0), 2) AS bookings_created_share_pct\n"
    "FROM counts ORDER BY period_end - period_start, period_start, channel"
)

_BALANCE_SQL = (
    "WITH " + _PERIODS + ",\n" + _HOTEL_CURRENCY + ",\n"
    "balances AS (SELECT b.booking_id, b.check_in_date, b.currency, t.booking_id IS NOT NULL AS tracked, "
    "t.total_amount, t.remaining_balance, "
    "(SELECT SUM(l.payment_amount) FROM payment_ledger l WHERE l.booking_id = b.booking_id AND " + _scope("l.") + " "
    "AND l.payment_status = 'received') AS ledger_received "
    "FROM room_bookings b LEFT JOIN payment_tracking t ON t.booking_id = b.booking_id "
    "WHERE " + _scope("b.") + " AND b.booking_status " + ACTIVE + "),\n"
    "windows AS (SELECT period, period_start, period_end, period_end - period_start + 1 AS days_in_period FROM periods "
    "UNION ALL SELECT 'all_open', MIN(check_in_date), MAX(check_in_date), NULL FROM balances)\n"
    "SELECT UPPER(%(hotel_name)s) AS hotel_name, w.period, w.period_start, w.period_end, w.days_in_period, "
    "SUM(b.remaining_balance) AS outstanding_tracking_remaining, "
    "SUM(b.total_amount - COALESCE(b.ledger_received, 0)) AS outstanding_total_less_ledger, "
    "COUNT(*) FILTER (WHERE b.remaining_balance > 0) AS bookings_with_balance, "
    "SUM(b.ledger_received) AS ledger_received, "
    "COUNT(b.booking_id) AS bookings, COUNT(b.booking_id) FILTER (WHERE NOT b.tracked) AS bookings_without_tracking, "
    "SUM(b.total_amount) AS tracking_total_amount, "
    "(SELECT code FROM hotel_currency) AS hotel_currency, "
    "COUNT(*) FILTER (WHERE b.currency <> (SELECT code FROM hotel_currency)) > 0 AS mixed_currency, "
    "COUNT(*) FILTER (WHERE b.currency <> (SELECT code FROM hotel_currency)) AS foreign_currency_bookings, "
    "COUNT(*) FILTER (WHERE b.booking_id IS NOT NULL AND b.currency IS NULL) AS unknown_currency_bookings\n"
    "FROM windows w LEFT JOIN balances b ON w.period = 'all_open' OR b.check_in_date BETWEEN w.period_start AND w.period_end\n"
    "GROUP BY w.period, w.period_start, w.period_end, w.days_in_period\n"
    "ORDER BY w.period = 'all_open', w.period_end - w.period_start, w.period_start"
)

_REVENUE_CONCEPTS = [
    ("revenue_tracking_total", "SUM(payment_tracking.total_amount * night rooms / booking rooms) over active nights", ["booking_stays", "payment_tracking", "room_bookings"]),
    ("rate_card_value_base", "SUM(booking_stays.rooms_booked * daily_pricing.base_price) over active nights; a price list value, not revenue", ["booking_stays", "daily_pricing"]),
]
_REVENUE_FACTS = ["hotel_currency", "revenue"]
_REVENUE_LIMIT = (
    "revenue_tracking_total spreads payment_tracking.total_amount over the booking's active nights by rooms; revenue_booking_total does the same "
    "with room_bookings.total_amount and is NULL where that amount is not recorded. rate_card_value_* multiply rooms by the daily_pricing price list "
    "for that room type and night; they are what the nights would cost at list price, not money taken. NULL means no source amount, not zero."
)

RECIPES = [
    _recipe(
        "daily_occupancy_rates", "Occupancy rate by period",
        "Occupancy rate for a hotel-local day or range: active room nights sold as a share of room nights in inventory, with confirmed, sellable, and checked-in readings.",
        ["occupancy rate", "occupancy", "occupancy percentage", "how full are we", "hotel occupancy yesterday", "occupancy today", "occupancy this month"],
        "occupancy_pct_active",
        [("occupancy_pct_active", "100 * active booking_stays room nights / room_inventory_nightly.total_inventory, on dates with inventory", ["booking_stays", "room_inventory_nightly"])],
        [
            ("days_with_inventory", _days("inventory_rooms")),
            ("inventory_room_nights", _sum("inventory_rooms")),
            ("sellable_room_nights", _sum("sellable_rooms")),
            ("sold_room_nights_active", _inv("sold_active")),
            ("sold_room_nights_confirmed", _inv("sold_confirmed")),
            ("checked_in_room_nights", _inv("checked_in_rooms")),
            ("occupancy_pct_active", _inv_rate("sold_active", "inventory_rooms", "100.0")),
            ("occupancy_pct_confirmed", _inv_rate("sold_confirmed", "inventory_rooms", "100.0")),
            ("occupancy_pct_active_of_sellable", _inv_rate("sold_active", "sellable_rooms", "100.0")),
            ("occupancy_pct_checked_in", _inv_rate("checked_in_rooms", "inventory_rooms", "100.0")),
        ],
        [_INVENTORY_LIMIT,
         "sellable subtracts blocked_quantity. checked_in counts room_stay nights with checked_in_at set and no check-out before that night.",
         "Physical room counts and booking-header nights are not occupancy readings here; room nights by booking header are in daily_room_nights_sold."],
        ["inventory", "nightly", "checked"],
    ),
    _recipe(
        "daily_room_nights_sold", "Room nights sold by period",
        "Room nights sold for a hotel-local day or range, as active, confirmed, booking-header, and checked-in readings.",
        ["room nights sold", "room nights", "rooms sold", "how many rooms did we sell", "sold rooms"],
        "sold_room_nights_active",
        [("sold_room_nights_active", "SUM(booking_stays.rooms_booked) where booking_status is active", ["booking_stays"])],
        [
            ("sold_room_nights_active", _sum("sold_active")),
            ("sold_room_nights_confirmed", _sum("sold_confirmed")),
            ("sold_room_nights_header_active", _sum("header_active")),
            ("checked_in_room_nights", _sum("checked_in_rooms")),
        ],
        ["sold reads booking_stays nights; header reads room_bookings check_in_date for nights nights. They can disagree when a booking was moved.",
         "checked_in counts room_stay nights with checked_in_at set and no check-out before that night."],
        ["nightly", "header", "checked"],
    ),
    _recipe(
        "daily_room_revenue", "Room revenue by period",
        "Total room revenue (room sales) for a hotel-local day or range by stay night, from booking amounts, with rate-card values as separate columns and currency flags.",
        ["room revenue", "total revenue", "room sales", "sales", "revenue yesterday", "how much did we make"],
        "revenue_tracking_total",
        _REVENUE_CONCEPTS,
        [
            ("revenue_tracking_total", "ROUND(" + _sum("rev_tracking_total") + ", 2)"),
            ("revenue_booking_total", "ROUND(" + _sum("rev_booking_total") + ", 2)"),
            ("rate_card_value_base", "ROUND(" + _sum("rate_card_base") + ", 2)"),
            ("rate_card_value_saved", "ROUND(" + _sum("rate_card_saved") + ", 2)"),
            ("room_nights_tracking_total", _sum("nights_tracking_total")),
            ("room_nights_rate_card", _sum("nights_rate_card")),
        ] + _MONEY_FLAGS,
        [_REVENUE_LIMIT] + _MONEY_LIMITS + ["Payments received are cash by payment date, not stay-night revenue; see daily_outstanding_balance."],
        _REVENUE_FACTS,
    ),
    _recipe(
        "daily_adr", "ADR by period",
        "ADR, average daily rate, for a hotel-local day or range: room revenue per sold room night from booking amounts, with rate-card readings and currency flags.",
        ["adr", "average daily rate", "average room rate", "average rate per room night"],
        "adr_tracking_total",
        _REVENUE_CONCEPTS + [("adr_tracking_total", "revenue_tracking_total / room nights with a payment_tracking amount", ["booking_stays", "payment_tracking"])],
        [
            ("adr_tracking_total", _rate("rev_tracking_total", "nights_tracking_total")),
            ("adr_booking_total", _rate("rev_booking_total", "nights_booking_total")),
            ("adr_rate_card_base", _rate("rate_card_base", "nights_rate_card")),
            ("adr_excl_zero_rate", _rate("rate_card_base_nonzero", "nights_rate_card_nonzero")),
            ("revenue_tracking_total", "ROUND(" + _sum("rev_tracking_total") + ", 2)"),
            ("room_nights_tracking_total", _sum("nights_tracking_total")),
            ("rate_card_value_base", "ROUND(" + _sum("rate_card_base") + ", 2)"),
            ("room_nights_rate_card", _sum("nights_rate_card")),
        ] + _MONEY_FLAGS,
        [_REVENUE_LIMIT, "Each ADR divides by the room nights its source covers. adr_excl_zero_rate is the rate-card ADR over nights whose base_price is above zero."]
        + _MONEY_LIMITS,
        _REVENUE_FACTS,
    ),
    _recipe(
        "daily_revpar", "RevPAR by period",
        "RevPAR, room revenue per available room night, for a hotel-local day or range, counting only dates with inventory, with rate-card readings and currency flags.",
        ["revpar", "revenue per available room", "rev par"],
        "revpar_tracking_total",
        _REVENUE_CONCEPTS + [("revpar_tracking_total", "revenue_tracking_total on dates with inventory / inventory room nights", ["payment_tracking", "room_inventory_nightly"])],
        [
            ("days_with_inventory", _days("inventory_rooms")),
            ("inventory_room_nights", _sum("inventory_rooms")),
            ("revenue_tracking_total_on_inventory_days", "ROUND(" + _inv("rev_tracking_total") + ", 2)"),
            ("revpar_tracking_total", _inv_rate("rev_tracking_total", "inventory_rooms")),
            ("revpar_booking_total", _inv_rate("rev_booking_total", "inventory_rooms")),
            ("revpar_rate_card_base", _inv_rate("rate_card_base", "inventory_rooms")),
        ] + _MONEY_FLAGS,
        [_REVENUE_LIMIT, _INVENTORY_LIMIT] + _MONEY_LIMITS,
        _REVENUE_FACTS + ["inventory"],
    ),
    _recipe(
        "daily_channel_mix", "Channel mix",
        "Channel mix for a hotel-local day or range: one row per period and booking channel with active room nights by stay night and active bookings by creation date, and each channel's share.",
        ["channel mix", "booking sources", "share by channel", "ota vs direct", "where do bookings come from", "channel share"],
        "room_nights_share_pct",
        [("room_nights_share_pct", "channel active room nights / all channels' active room nights in the period", ["booking_stays", "room_bookings"])],
        [("channel", ""), ("room_nights_share_pct", ""), ("room_nights_active", ""), ("bookings_created_active", ""), ("bookings_created_share_pct", "")],
        ["Every channel the hotel has a booking from is listed in every period, including channels with only cancelled bookings, at zero. A blank channel is UNKNOWN.",
         "No commission or revenue mix."],
        sql=_CHANNEL_SQL, grain="channel",
    ),
    _recipe(
        "daily_bookings_made", "Bookings made by period",
        "Bookings made (created) on a hotel-local day or range, by creation date, with all, active, and since-cancelled readings and active room nights booked.",
        ["bookings made", "new bookings", "bookings created", "reservations made", "how many bookings did we get"],
        "bookings_created_active",
        [("bookings_created_active", "COUNT(room_bookings) by hotel-local created_at date where booking_status is active", ["room_bookings"])],
        [
            ("bookings_created_active", _sum("created_active")),
            ("bookings_created_all", _sum("created_all")),
            ("bookings_created_cancelled_since", _sum("created_cancelled_since")),
            ("room_nights_created_active", _sum("created_room_nights_active")),
        ],
        ["Bookings without created_at are not counted."],
        ["created"],
    ),
    _recipe(
        "daily_arrival_counts", "Arrivals by period",
        "Arrivals for a hotel-local day or range: active and confirmed bookings, rooms, and known guests scheduled to arrive by check_in_date, and actual check-ins by check-in time.",
        ["arrivals", "check-ins", "arriving guests", "expected arrivals"],
        "arrivals_bookings_active",
        [("arrivals_bookings_active", "COUNT(room_bookings) by check_in_date where booking_status is active", ["room_bookings"])],
        [
            ("arrivals_bookings_active", _sum("arr_bookings_active")),
            ("arrivals_bookings_confirmed", _sum("arr_bookings_confirmed")),
            ("arrivals_rooms_active", _sum("arr_rooms_active")),
            ("arrivals_guests_known_active", _sum("arr_guests_known_active")),
            ("arrivals_missing_guest_count", _sum("arr_missing_guest_count")),
            ("actual_checkins", _sum("actual_checkins")),
        ],
        ["arrivals_guests_known_active sums booking_guest_counts and skips bookings without a count (arrivals_missing_guest_count).",
         "actual_checkins counts bookings by the hotel-local date of room_stay.checked_in_at. The first booking_stays night is not a separate reading."],
        ["guest_counts", "arrivals", "actual_in"],
    ),
    _recipe(
        "daily_departure_counts", "Departures by period",
        "Departures for a hotel-local day or range: active and confirmed bookings, rooms, and known guests due to check out by check_in_date plus nights, and actual check-outs by check-out time.",
        ["departures", "check-outs", "checkouts", "departing guests"],
        "departures_bookings_active",
        [("departures_bookings_active", "COUNT(room_bookings) by check_in_date + nights where booking_status is active", ["room_bookings"])],
        [
            ("departures_bookings_active", _sum("dep_bookings_active")),
            ("departures_bookings_confirmed", _sum("dep_bookings_confirmed")),
            ("departures_rooms_active", _sum("dep_rooms_active")),
            ("departures_guests_known_active", _sum("dep_guests_known_active")),
            ("departures_missing_guest_count", _sum("dep_missing_guest_count")),
            ("actual_checkouts", _sum("actual_checkouts")),
        ],
        ["actual_checkouts counts bookings by the hotel-local date of room_stay.checked_out_at. The day after the last booking_stays night is not a separate reading."],
        ["guest_counts", "departures", "actual_out"],
    ),
    _recipe(
        "daily_in_house_counts", "In-house by period",
        "In-house rooms, bookings, and known guests for a hotel-local night or range (summed per night for a range), by active booking_stays nights and by checked-in room_stay records.",
        ["in house", "in-house guests", "guests staying", "occupied rooms", "house count"],
        "in_house_rooms_active",
        [("in_house_rooms_active", "SUM(booking_stays.rooms_booked) for active bookings that night", ["booking_stays"])],
        [
            ("in_house_rooms_active", _sum("sold_active")),
            ("in_house_bookings_active", _sum("stay_bookings_active")),
            ("in_house_guests_known_active", _sum("guests_known_active")),
            ("in_house_bookings_missing_guest_count", _sum("bookings_missing_guest_count")),
            ("in_house_rooms_checked_in", _sum("checked_in_rooms")),
            ("in_house_guests_known_checked_in", _sum("guests_known_checked_in")),
        ],
        ["A night is the stay_date; a guest arriving today and leaving tomorrow is in house on today's row. For a range every column is a sum of nights (room nights, guest nights).",
         "Checked-in readings count room_stay nights with checked_in_at set and no check-out before that night, whatever the current stay_status."],
        ["guest_counts", "nightly", "checked", "guests_night"],
    ),
    _recipe(
        "daily_cancellation_counts", "Cancellations by period",
        "Cancellations for a hotel-local day or range, by scheduled arrival date, by cancelled stay night, by the booking's last update date, and by channel cancel message date.",
        ["cancellations", "cancelled bookings", "how many cancellations", "cancels"],
        "cancelled_bookings_by_arrival_date",
        [("cancelled_bookings_by_arrival_date", "COUNT(room_bookings) where booking_status = 'cancelled' by check_in_date", ["room_bookings"])],
        [
            ("cancelled_bookings_by_arrival_date", _sum("cancelled_by_arrival")),
            ("cancelled_room_nights_by_stay_date", _sum("cancelled_room_nights")),
            ("cancelled_bookings_by_last_update_date", _sum("cancelled_by_last_update")),
            ("cancel_messages_by_message_date", _sum("cancel_messages")),
        ],
        ["No cancellation timestamp is recorded. last_update_date is room_bookings.updated_at of a cancelled booking, which a later edit moves; cancel messages come from channel_reservations and cover channel bookings only."],
        ["guest_counts", "arrivals", "nightly", "cancel_updates", "cancel_messages"],
    ),
    _recipe(
        "daily_no_show_counts", "No-shows by period",
        "No-shows for a hotel-local day or range: bookings marked no-show by arrival date and by the date they were marked, and past active arrivals never checked in.",
        ["no shows", "no-shows", "noshow", "guests who did not arrive", "did not show up"],
        "no_shows_by_arrival_date",
        [("no_shows_by_arrival_date", "COUNT(room_bookings) with booking_status = 'no_show' or no_show_at set, by check_in_date", ["room_bookings"])],
        [
            ("no_shows_by_arrival_date", _sum("no_show_by_arrival")),
            ("no_shows_marked_on_date", _sum("no_show_marked_on_date")),
            ("past_arrivals_never_checked_in", _sum("past_arrivals_never_checked_in")),
        ],
        ["past_arrivals_never_checked_in counts active bookings whose arrival date has passed with no no_show mark and no room_stay checked_in_at; it is a candidate count, not a confirmed no-show."],
        ["guest_counts", "arrivals", "no_show_marks"],
    ),
    _recipe(
        "daily_average_length_of_stay", "Average length of stay by period",
        "Average length of stay for bookings arriving on a hotel-local day or range: per active booking, per confirmed booking, and room-weighted.",
        ["average length of stay", "alos", "length of stay", "how long do guests stay", "average nights"],
        "alos_active",
        [("alos_active", "SUM(nights) / COUNT(bookings) for active arrivals", ["room_bookings"])],
        [
            ("alos_active", _rate("arr_nights_active", "arr_bookings_active")),
            ("alos_confirmed", _rate("arr_nights_confirmed", "arr_bookings_confirmed")),
            ("alos_room_weighted_active", _rate("arr_room_nights_active", "arr_rooms_active")),
            ("arrivals_bookings_active", _sum("arr_bookings_active")),
            ("arrival_nights_active", _sum("arr_nights_active")),
            ("arrivals_rooms_active", _sum("arr_rooms_active")),
            ("arrival_room_nights_active", _sum("arr_room_nights_active")),
        ],
        ["Stays are grouped by arrival date, not by the nights they cover. Nights are room_bookings.nights."],
        ["guest_counts", "arrivals"],
    ),
    _recipe(
        "daily_booking_lead_time", "Booking lead time by period",
        "Average booking lead time in days between the hotel-local booking date and arrival, for bookings arriving on a hotel-local day or range, for active and all bookings.",
        ["booking lead time", "lead time", "booking window", "how far ahead do guests book", "days in advance"],
        "avg_lead_days_active",
        [("avg_lead_days_active", "SUM(check_in_date - local created date) / COUNT(bookings) for active arrivals with a non-negative lead", ["room_bookings"])],
        [
            ("avg_lead_days_active", _rate("lead_days_active", "lead_bookings_active")),
            ("avg_lead_days_all", _rate("lead_days_all", "lead_bookings_all")),
            ("negative_lead_bookings", _sum("negative_lead_bookings")),
            ("lead_bookings_active", _sum("lead_bookings_active")),
            ("lead_days_active", _sum("lead_days_active")),
        ],
        ["Grouped by arrival date. Averages use only bookings recorded on or before their arrival date; negative_lead_bookings counts active bookings recorded after arrival. Bookings without created_at are skipped."],
        ["guest_counts", "arrivals"],
    ),
    _recipe(
        "daily_outstanding_balance", "Outstanding balance by period",
        "Outstanding balance (payments due, receivables) for active bookings arriving on a hotel-local day or range, plus an all_open row for every active booking, from payment_tracking and from payment_ledger receipts, with currency flags.",
        ["outstanding balance", "payments due", "unpaid balance", "amount owed", "receivables", "who still owes", "how much do guests owe", "accounts receivable"],
        "outstanding_tracking_remaining",
        [("outstanding_tracking_remaining", "SUM(payment_tracking.remaining_balance) for active bookings by check_in_date", ["payment_tracking", "room_bookings"])],
        [
            ("outstanding_tracking_remaining", ""),
            ("outstanding_total_less_ledger", ""),
            ("bookings_with_balance", ""),
            ("ledger_received", ""),
            ("bookings", ""),
            ("bookings_without_tracking", ""),
            ("tracking_total_amount", ""),
            ("hotel_currency", ""),
            ("mixed_currency", ""),
            ("foreign_currency_bookings", ""),
            ("unknown_currency_bookings", ""),
        ],
        ["Balances are current values grouped by arrival date. The all_open row covers every active booking whatever its arrival date; its period_start and period_end are the earliest and latest arrival dates and days_in_period is NULL.",
         "payment_tracking.remaining_balance and payment_ledger receipts are separate records and can disagree.",
         "Amounts are in each booking's currency as recorded; do not state a total as one currency when mixed_currency is true."],
        sql=_BALANCE_SQL,
    ),
]

RETIRED = ["daily_rooms_available"]

UNSUPPORTED = {
    "ota_commission": "No commission rate or amount is stored on bookings; channel payloads carry amount.commission but it is zero or absent.",
}


def _publish(cur, body):
    """Read the active page first. Write a new version only when the body changed."""
    from psycopg2.extras import Json
    from booking_system.semantic.registry import refresh_page_index

    catalog_id = body["catalog_id"]
    cur.execute(
        """
        SELECT v.version, v.body FROM catalog_active_versions a
        JOIN catalog_versions v ON v.catalog_id = a.catalog_id AND v.version = a.version
        WHERE a.scope = 'global' AND a.catalog_id = %s
        """,
        (catalog_id,),
    )
    active = cur.fetchone()
    if active and active[1] == body:
        return {"catalog_id": catalog_id, "status": "unchanged", "version": int(active[0])}
    cur.execute("SELECT 1 FROM catalogs WHERE catalog_id = %s", (catalog_id,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO catalogs (catalog_id, parent_id, domain, purpose, status, store, scope) VALUES (%s, %s, %s, %s, 'active', 'postgresql', 'general')",
            (catalog_id, PARENT, PARENT, body["purpose"]),
        )
    cur.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM catalog_versions WHERE catalog_id = %s", (catalog_id,))
    version = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO catalog_versions (catalog_id, version, body, provenance) VALUES (%s, %s, %s, %s)",
        (catalog_id, version, Json(body), Json({"source": SOURCE})),
    )
    cur.execute(
        """
        INSERT INTO catalog_active_versions (scope, catalog_id, version) VALUES ('global', %s, %s)
        ON CONFLICT (scope, catalog_id) DO UPDATE SET version = EXCLUDED.version, activated_at = NOW()
        """,
        (catalog_id, version),
    )
    cur.execute(
        "INSERT INTO knowledge_publications (catalog_id, version, previous_version) VALUES (%s, %s, %s)",
        (catalog_id, version, int(active[0]) if active else None),
    )
    refresh_page_index(cur, catalog_id)
    return {"catalog_id": catalog_id, "status": "published", "version": version, "previous_version": int(active[0]) if active else None}


def seed(keys=None):
    """Check each recipe's contract and execution, then publish it. A failed check is not written. Retired pages leave the index."""
    from booking_system.content_store import _transaction
    from booking_system.semantic.library import validate_publication
    from booking_system.semantic.recipe import recipe_contract
    from booking_system.semantic.registry import catalog_scopes, retire_catalog

    results = []
    for body in RECIPES:
        if keys and body["catalog_id"] not in keys:
            continue
        missing = recipe_contract(body)
        report = {"ok": False, "error": "recipe contract " + " ".join(missing)} if missing else validate_publication(body["catalog_id"], body)
        if not report.get("ok"):
            results.append({"catalog_id": body["catalog_id"], "status": "failed", "error": report.get("error")})
            continue
        results.append(_transaction(lambda cur, item=body: _publish(cur, item)))
    for catalog_id in RETIRED:
        if keys and catalog_id not in keys:
            continue
        if (catalog_scopes([catalog_id]).get(catalog_id) or {}).get("status") == "retired":
            results.append({"catalog_id": catalog_id, "status": "retired"})
            continue
        results.append(retire_catalog(catalog_id))
    return results


if __name__ == "__main__":
    import json
    import sys
    from dotenv import load_dotenv

    load_dotenv()
    for result in seed(sys.argv[1:]):
        print(json.dumps(result, default=str), flush=True)
