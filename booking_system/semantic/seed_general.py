"""Curated general recipes for standard hotel terms. Written directly to the catalog; no agent or reviewer."""

SOURCE = "general_seed"
PARENT = "B01"
ACTIVE = "NOT IN ('cancelled', 'no_show')"
LIVE = "IN ('booked', 'checked_in', 'checked_out')"
SOLD = "b.booking_status " + ACTIVE + " AND n.night_status " + LIVE


def _scope(alias=""):
    return alias + "hotel_id = %(hotel_id)s"


def _local(column):
    """A stored timestamptz as the hotel-local date; the session TimeZone is the hotel's configured zone."""
    return "(" + column + " AT TIME ZONE current_setting('TimeZone'))::date"


_NIGHTS = "booking_room_night n JOIN booking b ON b.booking_id = n.booking_id AND " + _scope("b.")
_ARRIVING = "booking_summary s JOIN booking b ON b.booking_id = s.booking_id AND " + _scope("b.")

_PERIODS = """periods AS (
    SELECT CASE
        WHEN to_date(%(start)s, 'MM/DD/YYYY') = to_date(%(end)s, 'MM/DD/YYYY')
        THEN to_char(to_date(%(start)s, 'MM/DD/YYYY'), 'YYYY-MM-DD')
        ELSE to_char(to_date(%(start)s, 'MM/DD/YYYY'), 'YYYY-MM-DD') || '/' || to_char(to_date(%(end)s, 'MM/DD/YYYY'), 'YYYY-MM-DD')
    END AS period,
    to_date(%(start)s, 'MM/DD/YYYY') AS period_start,
    to_date(%(end)s, 'MM/DD/YYYY') AS period_end
),
span AS (
    SELECT generate_series(to_date(%(start)s, 'MM/DD/YYYY'), to_date(%(end)s, 'MM/DD/YYYY'), interval '1 day')::date AS d
)"""

_PERIOD_NAMES = (
    "the one span from start through end, inclusive. A single day is that span with start equal to end, and its period is that date as YYYY-MM-DD. "
    "start and end are MM/DD/YYYY strings bound into the template and read with to_date(that string, 'MM/DD/YYYY')"
)

_DATE_PARAMETERS = ["hotel_id", "start", "end"]

_HOTEL_CURRENCY = (
    "hotel_currency AS (SELECT btrim(currency) AS code FROM hotel WHERE hotel_id = %(hotel_id)s)"
)

# Per-date CTEs: (sql, columns where no row means zero, columns where no row means unknown).
_FACTS = {
    "hotel_currency": (_HOTEL_CURRENCY, [], []),
    "guest_counts": (
        "guest_counts AS (SELECT booking_id, adults + COALESCE(children, 0) AS guest_count FROM booking "
        "WHERE " + _scope() + " AND adults IS NOT NULL)",
        [], [],
    ),
    "inventory": (
        "inventory AS (SELECT stay_date AS d, SUM(total_inventory) AS inventory_rooms, "
        "SUM(total_inventory - blocked_quantity) AS sellable_rooms "
        "FROM room_inventory_nightly WHERE " + _scope() + " GROUP BY stay_date)",
        [], ["inventory_rooms", "sellable_rooms"],
    ),
    "nightly": (
        "nightly AS (SELECT n.stay_date AS d, "
        "COUNT(*) FILTER (WHERE " + SOLD + ") AS sold_active, "
        "COUNT(*) FILTER (WHERE b.booking_status = 'confirmed' AND n.night_status " + LIVE + ") AS sold_confirmed, "
        "COUNT(*) FILTER (WHERE b.booking_status = 'cancelled') AS cancelled_room_nights, "
        "COUNT(DISTINCT n.booking_id) FILTER (WHERE " + SOLD + ") AS stay_bookings_active "
        "FROM " + _NIGHTS + " WHERE " + _scope("n.") + " GROUP BY n.stay_date)",
        ["sold_active", "sold_confirmed", "cancelled_room_nights", "stay_bookings_active"], [],
    ),
    "header": (
        "header AS (SELECT s.check_in_date + k.i AS d, "
        "SUM(s.rooms_booked) FILTER (WHERE s.booking_status " + ACTIVE + ") AS header_active "
        "FROM booking_summary s CROSS JOIN LATERAL generate_series(0, s.check_out_date - s.check_in_date - 1) AS k(i) "
        "WHERE " + _scope("s.") + " GROUP BY 1)",
        ["header_active"], [],
    ),
    "checked": (
        "checked AS (SELECT stay_date AS d, COUNT(*) AS checked_in_rooms FROM booking_room_night WHERE " + _scope() + " "
        "AND checked_in_at IS NOT NULL AND (checked_out_at IS NULL OR " + _local("checked_out_at") + " > stay_date) GROUP BY stay_date)",
        ["checked_in_rooms"], [],
    ),
    "guests_night": (
        "guests_night AS (SELECT k.stay_date AS d, SUM(g.guest_count) AS guests_known_active, "
        "COUNT(*) FILTER (WHERE g.booking_id IS NULL) AS bookings_missing_guest_count, "
        "SUM(g.guest_count) FILTER (WHERE k.checked_in) AS guests_known_checked_in "
        "FROM (SELECT n.stay_date, n.booking_id, BOOL_OR(n.checked_in_at IS NOT NULL AND (n.checked_out_at IS NULL OR "
        + _local("n.checked_out_at") + " > n.stay_date)) AS checked_in "
        "FROM " + _NIGHTS + " WHERE " + _scope("n.") + " AND " + SOLD + " GROUP BY n.stay_date, n.booking_id) k "
        "LEFT JOIN guest_counts g ON g.booking_id = k.booking_id GROUP BY k.stay_date)",
        ["guests_known_active", "bookings_missing_guest_count", "guests_known_checked_in"], [],
    ),
    "still_in": (
        "still_in AS (SELECT span.d, COUNT(k.booking_id) AS still_in_bookings, COALESCE(SUM(g.guest_count), 0) AS still_in_guests_known, "
        "COUNT(k.booking_id) FILTER (WHERE g.booking_id IS NULL) AS still_in_missing_guest_count "
        "FROM span LEFT JOIN (SELECT booking_id, MIN(" + _local("checked_in_at") + ") AS first_in, "
        "BOOL_OR(checked_out_at IS NULL) AS open_room, MAX(" + _local("checked_out_at") + ") AS last_out "
        "FROM booking_room_night WHERE " + _scope() + " AND checked_in_at IS NOT NULL GROUP BY booking_id) k "
        "ON k.first_in <= span.d AND (k.open_room OR k.last_out > span.d) "
        "LEFT JOIN guest_counts g ON g.booking_id = k.booking_id WHERE span.d <= CURRENT_DATE GROUP BY span.d)",
        [], ["still_in_bookings", "still_in_guests_known", "still_in_missing_guest_count"],
    ),
    "revenue": (
        "stay_amounts AS (SELECT n.stay_date, dp.base_price, dp.saved_price, b.currency, n.currency AS night_currency, n.sell_rate, "
        "b.gross_amount / COUNT(*) OVER (PARTITION BY n.booking_id) AS booking_total_share "
        "FROM " + _NIGHTS + " "
        "LEFT JOIN daily_pricing dp ON dp.room_type_id = n.room_type_id AND dp.stay_date = n.stay_date AND " + _scope("dp.") + " "
        "WHERE " + _scope("n.") + " AND " + SOLD + "),\n"
        "revenue AS (SELECT stay_date AS d, "
        "SUM(base_price) AS rate_card_base, COUNT(base_price) AS nights_rate_card, "
        "SUM(base_price) FILTER (WHERE base_price > 0) AS rate_card_base_nonzero, "
        "COUNT(*) FILTER (WHERE base_price > 0) AS nights_rate_card_nonzero, "
        "SUM(saved_price) AS rate_card_saved, "
        "SUM(booking_total_share) AS rev_booking_total, COUNT(booking_total_share) AS nights_booking_total, "
        "SUM(sell_rate) AS rev_tracking_total, COUNT(sell_rate) AS nights_tracking_total, "
        "COUNT(*) FILTER (WHERE currency <> (SELECT code FROM hotel_currency) OR night_currency <> (SELECT code FROM hotel_currency)) AS nights_foreign_currency, "
        "COUNT(*) FILTER (WHERE currency IS NULL) AS nights_unknown_currency, "
        "COUNT(*) FILTER (WHERE base_price > 0 AND sell_rate > 10 * base_price) AS nights_implausible_tracking "
        "FROM stay_amounts GROUP BY stay_date)",
        ["nights_rate_card", "nights_rate_card_nonzero", "nights_booking_total", "nights_tracking_total",
         "nights_foreign_currency", "nights_unknown_currency", "nights_implausible_tracking"],
        ["rate_card_base", "rate_card_base_nonzero", "rate_card_saved", "rev_booking_total", "rev_tracking_total"],
    ),
    "arrivals": (
        "arrivals AS (SELECT s.check_in_date AS d, "
        "COUNT(*) FILTER (WHERE s.booking_status = 'confirmed') AS arr_bookings_confirmed, "
        "COUNT(*) FILTER (WHERE s.booking_status " + ACTIVE + ") AS arr_bookings_active, "
        "SUM(s.rooms_booked) FILTER (WHERE s.booking_status " + ACTIVE + ") AS arr_rooms_active, "
        "SUM(g.guest_count) FILTER (WHERE s.booking_status " + ACTIVE + ") AS arr_guests_known_active, "
        "COUNT(*) FILTER (WHERE s.booking_status " + ACTIVE + " AND g.booking_id IS NULL) AS arr_missing_guest_count, "
        "SUM(s.nights) FILTER (WHERE s.booking_status = 'confirmed') AS arr_nights_confirmed, "
        "SUM(s.nights) FILTER (WHERE s.booking_status " + ACTIVE + ") AS arr_nights_active, "
        "SUM(s.nights * s.rooms_booked) FILTER (WHERE s.booking_status " + ACTIVE + ") AS arr_room_nights_active, "
        "SUM(s.check_in_date - " + _local("s.booked_at") + ") FILTER (WHERE s.booking_status " + ACTIVE + " AND s.check_in_date >= " + _local("s.booked_at") + ") AS lead_days_active, "
        "COUNT(*) FILTER (WHERE s.booking_status " + ACTIVE + " AND s.check_in_date >= " + _local("s.booked_at") + ") AS lead_bookings_active, "
        "COUNT(*) FILTER (WHERE s.booking_status " + ACTIVE + " AND s.check_in_date < " + _local("s.booked_at") + ") AS negative_lead_bookings, "
        "SUM(s.check_in_date - " + _local("s.booked_at") + ") FILTER (WHERE s.check_in_date >= " + _local("s.booked_at") + ") AS lead_days_all, "
        "COUNT(*) FILTER (WHERE s.check_in_date >= " + _local("s.booked_at") + ") AS lead_bookings_all, "
        "COUNT(*) FILTER (WHERE s.booking_status = 'cancelled') AS cancelled_by_arrival, "
        "COUNT(*) FILTER (WHERE s.booking_status = 'no_show' OR b.no_show_at IS NOT NULL) AS no_show_by_arrival, "
        "COUNT(*) FILTER (WHERE s.booking_status " + ACTIVE + " AND b.no_show_at IS NULL AND s.check_in_date < CURRENT_DATE "
        "AND NOT EXISTS (SELECT 1 FROM booking_room_night r WHERE r.booking_id = s.booking_id AND " + _scope("r.") + " "
        "AND r.checked_in_at IS NOT NULL)) AS past_arrivals_never_checked_in "
        "FROM " + _ARRIVING + " LEFT JOIN guest_counts g ON g.booking_id = s.booking_id "
        "WHERE " + _scope("s.") + " GROUP BY s.check_in_date)",
        ["arr_bookings_confirmed", "arr_bookings_active", "arr_rooms_active", "arr_guests_known_active", "arr_missing_guest_count",
         "arr_nights_confirmed", "arr_nights_active", "arr_room_nights_active", "lead_days_active", "lead_bookings_active",
         "negative_lead_bookings", "lead_days_all", "lead_bookings_all", "cancelled_by_arrival", "no_show_by_arrival",
         "past_arrivals_never_checked_in"],
        [],
    ),
    "actual_in": (
        "actual_in AS (SELECT " + _local("checked_in_at") + " AS d, COUNT(DISTINCT booking_id) AS actual_checkins "
        "FROM booking_room_night WHERE " + _scope() + " AND checked_in_at IS NOT NULL GROUP BY 1)",
        ["actual_checkins"], [],
    ),
    "actual_out": (
        "actual_out AS (SELECT " + _local("checked_out_at") + " AS d, COUNT(DISTINCT booking_id) AS actual_checkouts "
        "FROM booking_room_night WHERE " + _scope() + " AND checked_out_at IS NOT NULL GROUP BY 1)",
        ["actual_checkouts"], [],
    ),
    "departures": (
        "departures AS (SELECT s.check_out_date AS d, "
        "COUNT(*) FILTER (WHERE s.booking_status = 'confirmed') AS dep_bookings_confirmed, "
        "COUNT(*) FILTER (WHERE s.booking_status " + ACTIVE + ") AS dep_bookings_active, "
        "SUM(s.rooms_booked) FILTER (WHERE s.booking_status " + ACTIVE + ") AS dep_rooms_active, "
        "SUM(g.guest_count) FILTER (WHERE s.booking_status " + ACTIVE + ") AS dep_guests_known_active, "
        "COUNT(*) FILTER (WHERE s.booking_status " + ACTIVE + " AND g.booking_id IS NULL) AS dep_missing_guest_count "
        "FROM booking_summary s LEFT JOIN guest_counts g ON g.booking_id = s.booking_id "
        "WHERE " + _scope("s.") + " GROUP BY 1)",
        ["dep_bookings_confirmed", "dep_bookings_active", "dep_rooms_active", "dep_guests_known_active", "dep_missing_guest_count"], [],
    ),
    "created": (
        "created AS (SELECT " + _local("booked_at") + " AS d, COUNT(*) AS created_all, "
        "COUNT(*) FILTER (WHERE booking_status " + ACTIVE + ") AS created_active, "
        "COUNT(*) FILTER (WHERE booking_status = 'cancelled') AS created_cancelled_since, "
        "SUM(rooms_booked * nights) FILTER (WHERE booking_status " + ACTIVE + ") AS created_room_nights_active "
        "FROM booking_summary WHERE " + _scope() + " GROUP BY 1)",
        ["created_all", "created_active", "created_cancelled_since", "created_room_nights_active"], [],
    ),
    "cancel_dates": (
        "cancel_dates AS (SELECT " + _local("cancelled_at") + " AS d, COUNT(*) AS cancelled_on_date "
        "FROM booking WHERE " + _scope() + " AND booking_status = 'cancelled' GROUP BY 1)",
        ["cancelled_on_date"], [],
    ),
    "channel_cancels": (
        "channel_cancels AS (SELECT " + _local("cancelled_at") + " AS d, COUNT(*) AS channel_cancels "
        "FROM booking WHERE " + _scope() + " AND booking_status = 'cancelled' AND channel_booking_ref IS NOT NULL GROUP BY 1)",
        ["channel_cancels"], [],
    ),
    "no_show_marks": (
        "no_show_marks AS (SELECT " + _local("no_show_at") + " AS d, COUNT(*) AS no_show_marked_on_date "
        "FROM booking WHERE " + _scope() + " AND no_show_at IS NOT NULL GROUP BY 1)",
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
        "SELECT (SELECT hotel_name FROM hotel WHERE hotel_id = %(hotel_id)s) AS hotel_name, p.period, p.period_start, p.period_end, "
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
    "active means booking.booking_status is neither cancelled nor no_show; confirmed means booking_status = 'confirmed'. "
    "A sold room night is a booking_room_night row of an active booking whose night_status is booked, checked_in, or checked_out; "
    "cancelled and released nights are not sold.",
    "Stored timestamps (booked_at, cancelled_at, checked_in_at, checked_out_at, no_show_at, paid_at) are read in the hotel's configured time zone before their date is taken.",
    "Every reading reflects the current recorded state, not a snapshot taken on that date.",
]
_INVENTORY_LIMIT = (
    "Denominators come from room_inventory_nightly. A date without an inventory row adds nothing to the denominator, and its "
    "numerator is left out too; compare days_with_inventory with days_in_period to see partial coverage."
)
_MONEY_LIMITS = [
    "Amounts are in each booking's currency as recorded. hotel_currency is the hotel's configured currency; mixed_currency is true when "
    "any counted night belongs to a booking, or carries a sell_rate, in another currency, and unknown_currency_room_nights counts nights whose booking has no recorded currency. "
    "Do not state a total as one currency when mixed_currency is true.",
    "implausible_tracking_room_nights counts nights whose sell_rate is more than 10 times that night's rate-card base_price.",
    "No reading is net of OTA commission. Taxes, fees, and non-room charges are not separated.",
]
_MONEY_FLAGS = [
    ("hotel_currency", "(SELECT code FROM hotel_currency)"),
    ("mixed_currency", "SUM(x.nights_foreign_currency) > 0"),
    ("foreign_currency_room_nights", _sum("nights_foreign_currency")),
    ("unknown_currency_room_nights", _sum("nights_unknown_currency")),
    ("implausible_tracking_room_nights", _sum("nights_implausible_tracking")),
]


def _readings(rows):
    """Which columns are alternative readings of one figure: each column's population (which bookings or guests count) and unit
    (what is counted). A manager definition picks a population; the question picks the unit."""
    return {column: {"population": population, "unit": unit} for column, population, unit in rows}


def _recipe(catalog_id, title, problem, keyphrases, default, concepts, columns, limitations, facts=None, sql=None, grain="night", keys=None,
            readings=None):
    outputs = list(keys or _PERIOD_KEYS) + [name for name, _expr in columns]
    extra = {"readings": _readings(readings)} if readings else {}
    return extra | {
        "catalog_id": catalog_id,
        "parent": PARENT,
        "title": title,
        "purpose": problem,
        "problem": problem,
        "preconditions": [
            "The hotel has a configured time zone; CURRENT_DATE is the hotel-local today.",
            "Only the session hotel is bound; the read filters hotel_id = %(hotel_id)s. "
            "start and end are MM/DD/YYYY strings bound into the template. The read returns the one span from start through end inclusive.",
        ],
        "inputs": {
            "hotel_id": "bound by the platform from the session hotel",
            "start": "MM/DD/YYYY inclusive start, bound as %(start)s and read with to_date(start, 'MM/DD/YYYY')",
            "end": "MM/DD/YYYY inclusive end, bound as %(end)s and read with to_date(end, 'MM/DD/YYYY')",
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
            "parameters": list(_DATE_PARAMETERS),
            "method": SOURCE,
        },
        "validation": {"expected_value": ""},
    }


_CHANNEL_SQL = (
    "WITH " + _PERIODS + ",\n"
    "ch_nights AS (SELECT b.channel, n.stay_date FROM " + _NIGHTS + " WHERE " + _scope("n.") + " AND " + SOLD + "),\n"
    "ch_created AS (SELECT channel, " + _local("booked_at") + " AS created_on "
    "FROM booking WHERE " + _scope() + " AND booking_status " + ACTIVE + "),\n"
    "channels AS (SELECT DISTINCT channel FROM booking WHERE " + _scope() + "),\n"
    "counts AS (SELECT p.period, p.period_start, p.period_end, c.channel, "
    "(SELECT COUNT(*) FROM ch_nights n WHERE n.channel = c.channel AND n.stay_date BETWEEN p.period_start AND p.period_end) AS room_nights_active, "
    "(SELECT COUNT(*) FROM ch_created k WHERE k.channel = c.channel AND k.created_on BETWEEN p.period_start AND p.period_end) AS bookings_created_active "
    "FROM periods p CROSS JOIN channels c)\n"
    "SELECT (SELECT hotel_name FROM hotel WHERE hotel_id = %(hotel_id)s) AS hotel_name, period, period_start, period_end, period_end - period_start + 1 AS days_in_period, channel, "
    "ROUND(100.0 * room_nights_active / NULLIF(SUM(room_nights_active) OVER (PARTITION BY period), 0), 2) AS room_nights_share_pct, "
    "room_nights_active, bookings_created_active, "
    "ROUND(100.0 * bookings_created_active / NULLIF(SUM(bookings_created_active) OVER (PARTITION BY period), 0), 2) AS bookings_created_share_pct\n"
    "FROM counts ORDER BY period_end - period_start, period_start, channel"
)

_BALANCE_SQL = (
    "WITH " + _PERIODS + ",\n" + _HOTEL_CURRENCY + ",\n"
    "balances AS (SELECT s.booking_id, s.check_in_date, s.currency, s.gross_amount, k.amount_paid, k.balance_due, "
    "(SELECT SUM(n.sell_rate) FROM booking_room_night n WHERE n.booking_id = s.booking_id AND " + _scope("n.") + " "
    "AND n.night_status " + LIVE + ") AS night_rate_total "
    "FROM booking_summary s JOIN booking_balance k ON k.booking_id = s.booking_id AND " + _scope("k.") + " "
    "WHERE " + _scope("s.") + " AND s.booking_status " + ACTIVE + "),\n"
    "windows AS (SELECT period, period_start, period_end, period_end - period_start + 1 AS days_in_period FROM periods)\n"
    "SELECT (SELECT hotel_name FROM hotel WHERE hotel_id = %(hotel_id)s) AS hotel_name, w.period, w.period_start, w.period_end, w.days_in_period, "
    "SUM(b.balance_due) AS outstanding_tracking_remaining, "
    "SUM(b.night_rate_total - b.amount_paid) AS outstanding_total_less_ledger, "
    "COUNT(*) FILTER (WHERE b.balance_due > 0) AS bookings_with_balance, "
    "SUM(b.amount_paid) AS ledger_received, "
    "COUNT(b.booking_id) AS bookings, COUNT(b.booking_id) FILTER (WHERE b.gross_amount IS NULL) AS bookings_without_tracking, "
    "SUM(b.gross_amount) AS tracking_total_amount, "
    "(SELECT code FROM hotel_currency) AS hotel_currency, "
    "COUNT(*) FILTER (WHERE b.currency <> (SELECT code FROM hotel_currency)) > 0 AS mixed_currency, "
    "COUNT(*) FILTER (WHERE b.currency <> (SELECT code FROM hotel_currency)) AS foreign_currency_bookings, "
    "COUNT(*) FILTER (WHERE b.booking_id IS NOT NULL AND b.currency IS NULL) AS unknown_currency_bookings\n"
    "FROM windows w LEFT JOIN balances b ON b.check_in_date BETWEEN w.period_start AND w.period_end\n"
    "GROUP BY w.period, w.period_start, w.period_end, w.days_in_period\n"
    "ORDER BY w.period_end - w.period_start, w.period_start"
)

_REVENUE_CONCEPTS = [
    ("revenue_tracking_total", "SUM(booking_room_night.sell_rate) over sold room nights", ["booking_room_night", "booking"]),
    ("rate_card_value_base", "SUM(daily_pricing.base_price) over sold room nights, by room_type_id and stay_date; a price list value, not revenue", ["booking_room_night", "daily_pricing"]),
]
_REVENUE_FACTS = ["hotel_currency", "revenue"]
_REVENUE_LIMIT = (
    "revenue_tracking_total sums each sold night's recorded booking_room_night.sell_rate and is NULL for nights without one; revenue_booking_total "
    "spreads booking.gross_amount evenly over the booking's sold nights and is NULL where that amount is not recorded. rate_card_value_* add the "
    "daily_pricing price list for that room type and night; they are what the nights would cost at list price, not money taken. NULL means no source amount, not zero."
)

_ROOMS_LEFT_SQL = (
    "WITH nights AS (SELECT generate_series(to_date(%(start)s, 'MM/DD/YYYY'), to_date(%(end)s, 'MM/DD/YYYY'), interval '1 day')::date AS stay_date),\n"
    "sold AS (SELECT n.room_type_id, n.stay_date, COUNT(*) AS rooms_booked FROM " + _NIGHTS + " WHERE " + _scope("n.") + " AND " + SOLD + " "
    "GROUP BY n.room_type_id, n.stay_date)\n"
    "SELECT (SELECT hotel_name FROM hotel WHERE " + _scope() + ") AS hotel_name, to_char(d.stay_date, 'YYYY-MM-DD') AS period, d.stay_date, "
    "t.room_type_name AS room_type, i.total_inventory, i.blocked_quantity, COALESCE(s.rooms_booked, 0) AS rooms_booked, "
    "CASE WHEN i.stop_sell THEN 0 ELSE i.total_inventory - COALESCE(i.blocked_quantity, 0) - COALESCE(s.rooms_booked, 0) END AS rooms_left, "
    "i.total_inventory - COALESCE(i.blocked_quantity, 0) - COALESCE(s.rooms_booked, 0) AS sellable_left, "
    "i.stop_sell, i.closed_to_arrival, i.min_stay\n"
    "FROM nights d JOIN room_inventory_nightly i ON i.stay_date = d.stay_date AND " + _scope("i.") + "\n"
    "JOIN room_type t ON t.room_type_id = i.room_type_id AND " + _scope("t.") + "\n"
    "LEFT JOIN sold s ON s.room_type_id = i.room_type_id AND s.stay_date = d.stay_date\n"
    "ORDER BY d.stay_date < CURRENT_DATE, d.stay_date, t.room_type_name"
)


def _nightly(*args, **kwargs):
    """One row per night from start through end inclusive. A span is those two dates, not a fixed week."""
    body = _recipe(*args, **kwargs)
    body["limitations"] = [
        "Each row is one hotel-local night from start through end inclusive (period is that date, YYYY-MM-DD). A total across the span is not this recipe."
    ] + body["limitations"][1:]
    return body


RECIPES = [
    _recipe(
        "daily_occupancy_rates", "Occupancy rate by period",
        "Occupancy rate for a hotel-local day or range: active room nights sold as a share of room nights in inventory, with confirmed, sellable, and checked-in readings.",
        ["occupancy rate", "occupancy", "occupancy percentage", "how full are we", "hotel occupancy yesterday", "occupancy today", "occupancy this month"],
        "occupancy_pct_active",
        [("occupancy_pct_active", "100 * sold booking_room_night rows / room_inventory_nightly.total_inventory, on dates with inventory", ["booking_room_night", "booking", "room_inventory_nightly"])],
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
         "sellable subtracts blocked_quantity. checked_in counts booking_room_night rows with checked_in_at set and no check-out before that night.",
         "Physical room counts and booking-header nights are not occupancy readings here; room nights by booking header are in daily_room_nights_sold."],
        ["inventory", "nightly", "checked"],
    ),
    _recipe(
        "daily_room_nights_sold", "Room nights sold by period",
        "Room nights sold for a hotel-local day or range, as active, confirmed, booking-header, and checked-in readings.",
        ["room nights sold", "room nights", "rooms sold", "how many rooms did we sell", "sold rooms"],
        "sold_room_nights_active",
        [("sold_room_nights_active", "COUNT(booking_room_night) where the booking is active and the night is booked, checked_in, or checked_out", ["booking_room_night", "booking"])],
        [
            ("sold_room_nights_active", _sum("sold_active")),
            ("sold_room_nights_confirmed", _sum("sold_confirmed")),
            ("sold_room_nights_header_active", _sum("header_active")),
            ("checked_in_room_nights", _sum("checked_in_rooms")),
        ],
        ["sold counts booking_room_night rows; header spreads booking_summary.rooms_booked over every date from check_in_date to the day before check_out_date. "
         "They disagree when a booking has released nights, gaps between nights, or a different room count per night.",
         "checked_in counts booking_room_night rows with checked_in_at set and no check-out before that night."],
        ["nightly", "header", "checked"],
    ),
    _recipe(
        "daily_room_revenue", "Room revenue by period",
        "Total room revenue (room sales) for a hotel-local day or range by stay night, from nightly sell rates and booking amounts, with rate-card values as separate columns and currency flags.",
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
        "ADR, average daily rate, for a hotel-local day or range: room revenue per sold room night from nightly sell rates and booking amounts, with rate-card readings and currency flags.",
        ["adr", "average daily rate", "average room rate", "average rate per room night"],
        "adr_tracking_total",
        _REVENUE_CONCEPTS + [("adr_tracking_total", "revenue_tracking_total / sold room nights with a sell_rate", ["booking_room_night"])],
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
        _REVENUE_CONCEPTS + [("revpar_tracking_total", "revenue_tracking_total on dates with inventory / inventory room nights", ["booking_room_night", "room_inventory_nightly"])],
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
        "Channel mix for a hotel-local day or range: one row per period and booking channel with active room nights by stay night and active bookings by booking date, and each channel's share.",
        ["channel mix", "booking sources", "share by channel", "ota vs direct", "where do bookings come from", "channel share"],
        "room_nights_share_pct",
        [("room_nights_share_pct", "channel sold room nights / all channels' sold room nights in the period", ["booking_room_night", "booking"])],
        [("channel", ""), ("room_nights_share_pct", ""), ("room_nights_active", ""), ("bookings_created_active", ""), ("bookings_created_share_pct", "")],
        ["Every channel the hotel has a booking from is listed in every period, including channels with only cancelled bookings, at zero. channel is booking.channel as stored.",
         "No commission or revenue mix."],
        sql=_CHANNEL_SQL, grain="channel",
    ),
    _recipe(
        "daily_bookings_made", "Bookings made by period",
        "Bookings made (created) on a hotel-local day or range, by booking date, with all, active, and since-cancelled readings and active room nights booked.",
        ["bookings made", "new bookings", "bookings created", "reservations made", "how many bookings did we get"],
        "bookings_created_active",
        [("bookings_created_active", "COUNT(booking) by hotel-local booked_at date where booking_status is active", ["booking", "booking_summary"])],
        [
            ("bookings_created_active", _sum("created_active")),
            ("bookings_created_all", _sum("created_all")),
            ("bookings_created_cancelled_since", _sum("created_cancelled_since")),
            ("room_nights_created_active", _sum("created_room_nights_active")),
        ],
        ["Bookings are counted by booked_at. Room nights booked are booking_summary.rooms_booked times nights. A booking with no nights is not counted."],
        ["created"],
    ),
    _recipe(
        "daily_arrival_counts", "Arrivals by period",
        "Arrivals for a hotel-local day or range: active and confirmed bookings, rooms, and known guests scheduled to arrive by check_in_date, and actual check-ins by check-in time.",
        ["arrivals", "check-ins", "arriving guests", "expected arrivals"],
        "arrivals_bookings_active",
        [("arrivals_bookings_active", "COUNT(booking_summary) by check_in_date where booking_status is active", ["booking_summary", "booking"])],
        [
            ("arrivals_bookings_active", _sum("arr_bookings_active")),
            ("arrivals_bookings_confirmed", _sum("arr_bookings_confirmed")),
            ("arrivals_rooms_active", _sum("arr_rooms_active")),
            ("arrivals_guests_known_active", _sum("arr_guests_known_active")),
            ("arrivals_missing_guest_count", _sum("arr_missing_guest_count")),
            ("actual_checkins", _sum("actual_checkins")),
        ],
        ["check_in_date is the booking's first night on booking_room_night. arrivals_guests_known_active sums booking.adults plus children and skips bookings without adults (arrivals_missing_guest_count).",
         "actual_checkins counts bookings by the hotel-local date of booking_room_night.checked_in_at."],
        ["guest_counts", "arrivals", "actual_in"],
        readings=[
            ("arrivals_bookings_active", "active", "bookings"),
            ("arrivals_bookings_confirmed", "confirmed", "bookings"),
            ("arrivals_rooms_active", "active", "rooms"),
            ("arrivals_guests_known_active", "active", "guests"),
            ("actual_checkins", "actually_checked_in", "bookings"),
        ],
    ),
    _recipe(
        "daily_departure_counts", "Departures by period",
        "Departures for a hotel-local day or range: active and confirmed bookings, rooms, and known guests due to check out on check_out_date, and actual check-outs by check-out time.",
        ["departures", "check-outs", "checkouts", "departing guests"],
        "departures_bookings_active",
        [("departures_bookings_active", "COUNT(booking_summary) by check_out_date where booking_status is active", ["booking_summary"])],
        [
            ("departures_bookings_active", _sum("dep_bookings_active")),
            ("departures_bookings_confirmed", _sum("dep_bookings_confirmed")),
            ("departures_rooms_active", _sum("dep_rooms_active")),
            ("departures_guests_known_active", _sum("dep_guests_known_active")),
            ("departures_missing_guest_count", _sum("dep_missing_guest_count")),
            ("actual_checkouts", _sum("actual_checkouts")),
        ],
        ["check_out_date is the morning after the booking's last night. actual_checkouts counts bookings by the hotel-local date of booking_room_night.checked_out_at."],
        ["guest_counts", "departures", "actual_out"],
        readings=[
            ("departures_bookings_active", "active", "bookings"),
            ("departures_bookings_confirmed", "confirmed", "bookings"),
            ("departures_rooms_active", "active", "rooms"),
            ("departures_guests_known_active", "active", "guests"),
            ("actual_checkouts", "actually_checked_out", "bookings"),
        ],
    ),
    _recipe(
        "daily_in_house_counts", "In-house by period",
        "In-house rooms, bookings, and known guests for a hotel-local night or range (summed per night for a range), by sold booking_room_night rows, by checked-in nights, and by bookings checked in and not yet checked out.",
        ["in house", "in-house guests", "guests staying", "occupied rooms", "house count"],
        "in_house_rooms_active",
        [("in_house_rooms_active", "COUNT(booking_room_night) sold that night", ["booking_room_night", "booking"])],
        [
            ("in_house_rooms_active", _sum("sold_active")),
            ("in_house_bookings_active", _sum("stay_bookings_active")),
            ("in_house_guests_known_active", _sum("guests_known_active")),
            ("in_house_bookings_missing_guest_count", _sum("bookings_missing_guest_count")),
            ("in_house_rooms_checked_in", _sum("checked_in_rooms")),
            ("in_house_guests_known_checked_in", _sum("guests_known_checked_in")),
            ("still_in_house_bookings", _sum("still_in_bookings")),
            ("still_in_house_guests_known", _sum("still_in_guests_known")),
            ("still_in_house_missing_guest_count", _sum("still_in_missing_guest_count")),
        ],
        ["A night is the stay_date; a guest arriving today and leaving tomorrow is in house on today's row. For a range every column is a sum of nights (room nights, guest nights).",
         "Checked-in readings count booking_room_night rows with checked_in_at set and no check-out before that night, whatever the current night_status; "
         "a guest whose last night was last night is not on today's row even before checking out. "
         "Guests are booking.adults plus children; a booking without adults is counted in in_house_bookings_missing_guest_count.",
         "still_in_house readings count bookings checked in on or before the date and not checked out by the end of it: a booking with a room "
         "whose checked_out_at is empty, or whose last check-out is after the date. On today's row this is everyone checked in and not yet checked out "
         "right now, including a guest due to leave today who has not checked out. Dates after today are empty, because nobody has checked in for them yet. "
         "Bookings without adults are counted in still_in_house_missing_guest_count."],
        ["guest_counts", "nightly", "checked", "guests_night", "still_in"],
        readings=[
            ("in_house_rooms_active", "sold_tonight", "rooms"),
            ("in_house_bookings_active", "sold_tonight", "bookings"),
            ("in_house_guests_known_active", "sold_tonight", "guests"),
            ("in_house_rooms_checked_in", "checked_in_tonight", "rooms"),
            ("in_house_guests_known_checked_in", "checked_in_tonight", "guests"),
            ("still_in_house_bookings", "checked_in_not_checked_out", "bookings"),
            ("still_in_house_guests_known", "checked_in_not_checked_out", "guests"),
        ],
    ),
    _recipe(
        "daily_cancellation_counts", "Cancellations by period",
        "Cancellations for a hotel-local day or range, by scheduled arrival date, by cancelled stay night, by the date the booking was cancelled, and channel cancellations by that date.",
        ["cancellations", "cancelled bookings", "how many cancellations", "cancels"],
        "cancelled_bookings_by_arrival_date",
        [("cancelled_bookings_by_arrival_date", "COUNT(booking_summary) where booking_status = 'cancelled' by check_in_date", ["booking_summary"])],
        [
            ("cancelled_bookings_by_arrival_date", _sum("cancelled_by_arrival")),
            ("cancelled_room_nights_by_stay_date", _sum("cancelled_room_nights")),
            ("cancelled_bookings_by_last_update_date", _sum("cancelled_on_date")),
            ("cancel_messages_by_message_date", _sum("channel_cancels")),
        ],
        ["cancelled_bookings_by_last_update_date counts cancelled bookings by the hotel-local date of booking.cancelled_at. "
         "cancel_messages_by_message_date counts the same for bookings that carry a channel_booking_ref; the channel's own message time is not read."],
        ["guest_counts", "arrivals", "nightly", "cancel_dates", "channel_cancels"],
    ),
    _recipe(
        "daily_no_show_counts", "No-shows by period",
        "No-shows for a hotel-local day or range: bookings marked no-show by arrival date and by the date they were marked, and past active arrivals never checked in.",
        ["no shows", "no-shows", "noshow", "guests who did not arrive", "did not show up"],
        "no_shows_by_arrival_date",
        [("no_shows_by_arrival_date", "COUNT(booking) with booking_status = 'no_show' or no_show_at set, by check_in_date", ["booking", "booking_summary"])],
        [
            ("no_shows_by_arrival_date", _sum("no_show_by_arrival")),
            ("no_shows_marked_on_date", _sum("no_show_marked_on_date")),
            ("past_arrivals_never_checked_in", _sum("past_arrivals_never_checked_in")),
        ],
        ["past_arrivals_never_checked_in counts active bookings whose arrival date has passed with no no_show mark and no booking_room_night checked_in_at; it is a candidate count, not a confirmed no-show."],
        ["guest_counts", "arrivals", "no_show_marks"],
    ),
    _recipe(
        "daily_average_length_of_stay", "Average length of stay by period",
        "Average length of stay for bookings arriving on a hotel-local day or range: per active booking, per confirmed booking, and room-weighted.",
        ["average length of stay", "alos", "length of stay", "how long do guests stay", "average nights"],
        "alos_active",
        [("alos_active", "SUM(nights) / COUNT(bookings) for active arrivals", ["booking_summary"])],
        [
            ("alos_active", _rate("arr_nights_active", "arr_bookings_active")),
            ("alos_confirmed", _rate("arr_nights_confirmed", "arr_bookings_confirmed")),
            ("alos_room_weighted_active", _rate("arr_room_nights_active", "arr_rooms_active")),
            ("arrivals_bookings_active", _sum("arr_bookings_active")),
            ("arrival_nights_active", _sum("arr_nights_active")),
            ("arrivals_rooms_active", _sum("arr_rooms_active")),
            ("arrival_room_nights_active", _sum("arr_room_nights_active")),
        ],
        ["Stays are grouped by arrival date, not by the nights they cover. Nights are booking_summary.nights, the distinct stay dates of the booking."],
        ["guest_counts", "arrivals"],
    ),
    _recipe(
        "daily_booking_lead_time", "Booking lead time by period",
        "Average booking lead time in days between the hotel-local booking date and arrival, for bookings arriving on a hotel-local day or range, for active and all bookings.",
        ["booking lead time", "lead time", "booking window", "how far ahead do guests book", "days in advance"],
        "avg_lead_days_active",
        [("avg_lead_days_active", "SUM(check_in_date - local booked_at date) / COUNT(bookings) for active arrivals with a non-negative lead", ["booking_summary"])],
        [
            ("avg_lead_days_active", _rate("lead_days_active", "lead_bookings_active")),
            ("avg_lead_days_all", _rate("lead_days_all", "lead_bookings_all")),
            ("negative_lead_bookings", _sum("negative_lead_bookings")),
            ("lead_bookings_active", _sum("lead_bookings_active")),
            ("lead_days_active", _sum("lead_days_active")),
        ],
        ["Grouped by arrival date. Averages use only bookings recorded on or before their arrival date; negative_lead_bookings counts active bookings recorded after arrival."],
        ["guest_counts", "arrivals"],
    ),
    _recipe(
        "daily_outstanding_balance", "Outstanding balance by period",
        "Outstanding balance (payments due, receivables) for active bookings arriving from start through end, inclusive, from booking amounts, nightly sell rates, and received payments, with currency flags.",
        ["outstanding balance", "payments due", "unpaid balance", "amount owed", "receivables", "who still owes", "how much do guests owe", "accounts receivable"],
        "outstanding_tracking_remaining",
        [("outstanding_tracking_remaining", "SUM(booking_balance.balance_due) for active bookings by check_in_date", ["booking_balance", "booking_summary", "payment"])],
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
        ["Balances are current values for arrivals from start through end inclusive. The row is that one span.",
         "outstanding_tracking_remaining is booking.gross_amount less received payments plus received refunds (booking_balance.balance_due); tracking_total_amount is booking.gross_amount and "
         "bookings_without_tracking counts bookings without one. outstanding_total_less_ledger is the sum of the booking's live nightly sell_rate less the same net payments; "
         "ledger_received is received payments less received refunds. Void payments are not counted. The two outstanding readings can disagree.",
         "Amounts are in each booking's currency as recorded; do not state a total as one currency when mixed_currency is true."],
        sql=_BALANCE_SQL,
    ),
    _nightly(
        "daily_rooms_left_by_room_type", "Rooms left by room type and night",
        "Rooms left to sell (availability, vacancy, open inventory) for each room type on each hotel-local night, with inventory, blocked rooms, rooms booked, stop-sell, closed-to-arrival, and minimum stay.",
        ["rooms left", "room availability", "availability", "rooms available", "vacancy", "how many rooms are left", "can we accommodate",
         "open inventory", "rooms left today", "availability by room type"],
        "rooms_left",
        [("rooms_left", "room_inventory_nightly.total_inventory - blocked_quantity - sold booking_room_night rows of that room type and night; 0 on a stop_sell night",
          ["room_inventory_nightly", "room_type", "booking_room_night", "booking"])],
        [(name, "") for name in ("total_inventory", "blocked_quantity", "rooms_booked", "rooms_left", "sellable_left", "stop_sell", "closed_to_arrival", "min_stay")],
        ["One row per room type with an inventory row on that night; a room type without one is not listed.",
         "rooms_left is 0 on a stop_sell night; sellable_left ignores stop_sell. A negative sellable_left means sold above recorded inventory.",
         "Rows are ordered from the hotel-local today forward, then past nights. A range total is not answered; each night is its own row."],
        sql=_ROOMS_LEFT_SQL,
        keys=["hotel_name", "period", "stay_date", "room_type"],
    ),
]

def _field(kind, description, required=True):
    return {"type": kind, "required": required, "description": description}


def _action(catalog_id, problem, phrases, action, permission, parameters, approval, grain="reservation", output=""):
    """A published action recipe. It names the registered PMS operation and the approval text. It has no SQL."""
    return {
        "catalog_id": catalog_id,
        "type": "action_recipe",
        "parent": PARENT,
        "title": problem,
        "purpose": problem,
        "problem": problem,
        "preconditions": ["The session hotel is bound by the server. The person approves the card before the PMS operation runs."],
        "inputs": {name: spec["description"] for name, spec in parameters.items()},
        "output_grain": grain,
        "outputs": [output or {"reservation": "booking_id", "guest": "guest_phone", "room": "room_number", "staff": "staff_phone", "night": "stay_date", "transaction_code": "transaction_code"}.get(grain, "booking_id")],
        "verification": {
            "reservation": "Prepared against a booking at the session hotel. Reject leaves the booking unchanged. Approve calls the existing PMS service.",
            "guest": "Prepared against a guest at the session hotel. Reject leaves the guest unchanged. Approve calls the existing PMS service.",
            "room": "Prepared against a room at the session hotel. Reject leaves the room unchanged. Approve calls the existing PMS service.",
            "staff": "Prepared against a staff member at the session hotel. Reject leaves attendance unchanged. Approve calls the existing PMS service.",
            "night": "Prepared against a night at the session hotel. Reject leaves the night unchanged. Approve calls the existing PMS service.",
            "transaction_code": "Prepared against a folio at the session hotel. Reject leaves the folio unchanged. Approve calls the existing PMS service.",
        }.get(grain, "Prepared at the session hotel. Reject leaves the hotel unchanged. Approve calls the existing PMS service."),
        "keyphrases": list(phrases),
        "aliases": list(phrases),
        "permission": permission,
        "request_approval": approval,
        "solution": {"backend_action": action, "parameters": parameters, "method": SOURCE},
        "validation": {"expected_value": ""},
    }


ACTION_RECIPES = [
    _action(
        "reservation_check_in", "Check in a guest reservation",
        ["check in", "check-in", "check in guest", "check in reservation", "arrive guest"],
        "reservation.check_in", "reservations.operate",
        {"booking_id": _field("string", "The booking id at this hotel"), "room_number": _field("string", "The room number to give the guest")},
        {"title": "Confirm guest check-in", "message": "Guest: {guest_name}\nReservation: {booking_id}\nRoom: {room_number}\nCheck-in: {check_in_date}\nCheck in this reservation now?", "approve_label": "Check in", "reject_label": "Cancel"},
    ),
    _action(
        "reservation_check_out", "Check out a guest reservation",
        ["check out", "check-out", "check out guest", "depart guest"],
        "reservation.check_out", "reservations.operate",
        {"booking_id": _field("string", "The booking id at this hotel"), "room_number": _field("string", "The room number the guest is leaving")},
        {"title": "Confirm guest check-out", "message": "Guest: {guest_name}\nReservation: {booking_id}\nRoom: {room_number}\nCheck-out: {check_out_date}\nCheck out this reservation now?", "approve_label": "Check out", "reject_label": "Cancel"},
    ),
    _action(
        "reservation_cancel", "Cancel a reservation",
        ["cancel booking", "cancel reservation", "cancel stay"],
        "reservation.cancel", "reservations.operate",
        {"booking_id": _field("string", "The booking id at this hotel")},
        {"title": "Cancel reservation", "message": "Guest: {guest_name}\nReservation: {booking_id}\n{check_in_date} to {check_out_date}\nCancel this reservation?", "approve_label": "Cancel reservation", "reject_label": "Keep it"},
    ),
    _action(
        "reservation_create", "Create a reservation",
        ["create booking", "new reservation", "book a room", "make a booking"],
        "reservation.create", "reservations.operate",
        {
            "guest_phone": _field("string", "The guest phone already on file, with country code"),
            "room_type": _field("string", "The room type name at this hotel"),
            "stay_dates": _field("string", "Check-in and check-out as YYYY-MM-DD,YYYY-MM-DD"),
        },
        {"title": "Create reservation", "message": "Guest: {guest_name}\nPhone: {guest_phone}\nRoom type: {room_type}\n{check_in_date} to {check_out_date}\nCreate this reservation?", "approve_label": "Create", "reject_label": "Cancel"},
    ),
    _action(
        "reservation_modify", "Modify a reservation",
        ["modify booking", "change reservation", "change dates", "change room type"],
        "reservation.modify", "reservations.operate",
        {
            "booking_id": _field("string", "The booking id at this hotel"),
            "new_stay_dates": _field("string", "New check-in and check-out as YYYY-MM-DD,YYYY-MM-DD", False),
            "new_room_type": _field("string", "The new room type name", False),
            "new_rooms_booked": _field("number", "The new number of rooms", False),
        },
        {"title": "Modify reservation", "message": "Guest: {guest_name}\nReservation: {booking_id}\nDates: {new_stay_dates}\nRoom type: {new_room_type}\nRooms: {new_rooms_booked}\nApply this change?", "approve_label": "Modify", "reject_label": "Cancel"},
    ),
    _action(
        "reservation_payment", "Record a payment",
        ["record payment", "take payment", "post payment", "guest payment"],
        "reservation.payment", "reservations.operate",
        {
            "booking_id": _field("string", "The booking id at this hotel"),
            "payment_amount": _field("number", "The amount received, in the hotel currency"),
            "payment_method": _field("string", "CASH, BANK TRANSFER, CARD, OTA, CHEQUE, or OTHERS"),
        },
        {"title": "Record payment", "message": "Guest: {guest_name}\nReservation: {booking_id}\nAmount: {payment_amount}\nMethod: {payment_method}\nRecord this payment?", "approve_label": "Record payment", "reject_label": "Cancel"},
    ),
    _action(
        "reservation_refund", "Record a refund",
        ["record refund", "refund payment", "give a refund"],
        "reservation.refund", "reservations.operate",
        {"booking_id": _field("string", "The booking id at this hotel"), "payment_seq": _field("number", "The payment sequence to refund")},
        {"title": "Record refund", "message": "Guest: {guest_name}\nReservation: {booking_id}\nPayment: {payment_seq}\nRecord this refund?", "approve_label": "Record refund", "reject_label": "Cancel"},
    ),
    _action(
        "reservation_reassign_room", "Reassign a room",
        ["reassign room", "move room", "change room number"],
        "reservation.reassign_room", "reservations.operate",
        {
            "booking_id": _field("string", "The booking id at this hotel"),
            "stay_date": _field("date", "The night to move, YYYY-MM-DD"),
            "old_room_number": _field("string", "The room number the guest has now"),
            "new_room_number": _field("string", "The room number to move them to"),
            "mark_old_clean": _field("string", "yes or no. Leave empty to leave the old room's readiness unchanged", False),
        },
        {"title": "Reassign room", "message": "Guest: {guest_name}\nReservation: {booking_id}\nNight: {stay_date}\nFrom room {old_room_number} to room {new_room_number}\nMark the old room clean: {mark_old_clean}\nMove this room?", "approve_label": "Reassign", "reject_label": "Cancel"},
    ),
    _action(
        "reservation_no_show", "Mark a reservation as a no-show",
        ["no show", "no-show", "mark no show", "guest did not arrive"],
        "reservation.no_show", "reservations.operate",
        {"booking_id": _field("string", "The booking id at this hotel"), "reason": _field("string", "Why the guest did not arrive", False)},
        {"title": "Mark no-show", "message": "Guest: {guest_name}\nReservation: {booking_id}\n{check_in_date} to {check_out_date}\nReason: {reason}\nMark this reservation as a no-show?", "approve_label": "Mark no-show", "reject_label": "Cancel"},
    ),
    _action(
        "reservation_notes", "Write reservation notes",
        ["booking notes", "reservation notes", "add a note", "write notes"],
        "reservation.notes", "reservations.operate",
        {"booking_id": _field("string", "The booking id at this hotel"), "notes": _field("string", "The note to save on the reservation")},
        {"title": "Save reservation note", "message": "Guest: {guest_name}\nReservation: {booking_id}\nNote: {notes}\nSave this note?", "approve_label": "Save note", "reject_label": "Cancel"},
    ),
    _action(
        "reservation_assign_room", "Assign a room to a reservation",
        ["assign room", "give a room", "allocate room"],
        "reservation.assign_room", "reservations.operate",
        {"booking_id": _field("string", "The booking id at this hotel"), "room_number": _field("string", "The room number to assign")},
        {"title": "Assign room", "message": "Guest: {guest_name}\nReservation: {booking_id}\nRoom: {room_number}\n{check_in_date} to {check_out_date}\nAssign this room?", "approve_label": "Assign", "reject_label": "Cancel"},
    ),
    _action(
        "reservation_undo_check_in", "Undo a check-in",
        ["undo check in", "undo check-in", "reverse check in"],
        "reservation.undo_check_in", "reservations.operate",
        {
            "booking_id": _field("string", "The booking id at this hotel"),
            "room_number": _field("string", "The room number to return to booked"),
            "mark_clean": _field("string", "yes or no. Leave empty to mark the room clean", False),
        },
        {"title": "Undo check-in", "message": "Guest: {guest_name}\nReservation: {booking_id}\nRoom: {room_number}\nMark the room clean: {mark_clean}\nUndo this check-in?", "approve_label": "Undo check-in", "reject_label": "Cancel"},
    ),
    _action(
        "reservation_reset_night", "Reset a stay night",
        ["reset night", "reset stay", "return night to booked"],
        "reservation.reset_night", "reservations.operate",
        {"booking_id": _field("string", "The booking id at this hotel"), "stay_date": _field("date", "A night of the stay, YYYY-MM-DD")},
        {"title": "Reset stay night", "message": "Guest: {guest_name}\nReservation: {booking_id}\nNight: {stay_date}\nReturn checked-in and checked-out nights to booked?", "approve_label": "Reset night", "reject_label": "Cancel"},
    ),
    _action(
        "room_set_readiness", "Set a room's readiness",
        ["set room readiness", "mark room clean", "mark room dirty", "room inspected", "room cleaning"],
        "room.set_readiness", "housekeeping.view",
        {"room_number": _field("string", "The room number at this hotel"), "new_status": _field("string", "clean, dirty, cleaning, or inspected")},
        {"title": "Set room readiness", "message": "Room: {room_number}\nNow: {current_readiness}\nNew status: {new_status}\nUpdate this room?", "approve_label": "Update readiness", "reject_label": "Cancel"},
        grain="room",
    ),
    _action(
        "room_block", "Block a room",
        ["block room", "out of order", "out of service", "mark room blocked"],
        "room.block", "maintenance.operate",
        {
            "room_number": _field("string", "The room number at this hotel"),
            "start_date": _field("date", "First blocked night, YYYY-MM-DD"),
            "end_date": _field("date", "Last blocked night, YYYY-MM-DD"),
            "reason": _field("string", "Why the room is blocked"),
            "block_type": _field("string", "out_of_order or out_of_service. Leave empty for out_of_order", False),
        },
        {"title": "Block room", "message": "Room: {room_number}\n{start_date} to {end_date}\nType: {block_type}\nReason: {reason}\nBlock this room?", "approve_label": "Block room", "reject_label": "Cancel"},
        grain="room",
    ),
    _action(
        "room_release", "Release a room block",
        ["release room", "unblock room", "make room available", "remove room block"],
        "room.release", "maintenance.operate",
        {
            "room_number": _field("string", "The room number at this hotel"),
            "start_date": _field("date", "First night to free, YYYY-MM-DD"),
            "end_date": _field("date", "Last night to free, YYYY-MM-DD"),
        },
        {"title": "Release room", "message": "Room: {room_number}\n{start_date} to {end_date}\nRelease the block on this room?", "approve_label": "Release", "reject_label": "Cancel"},
        grain="room",
    ),
    _action(
        "guest_service_request", "Open a guest service request",
        ["service request", "guest request", "request service", "desk task"],
        "guest.service_request", "front_desk.operate",
        {
            "guest_phone": _field("string", "The guest phone already on file, with country code"),
            "request": _field("string", "What the guest asked for"),
            "requested_for": _field("string", "When the guest wants it, as an ISO date-time", False),
            "booking_id": _field("string", "The booking id, when the request is about one stay", False),
        },
        {"title": "Open service request", "message": "Guest: {guest_name}\nPhone: {guest_phone}\nRequest: {request}\nWhen: {requested_for}\nReservation: {booking_id}\nOpen this request?", "approve_label": "Open request", "reject_label": "Cancel"},
        grain="guest",
    ),
    _action(
        "guest_close_request", "Close a guest service request",
        ["close request", "complete request", "finish guest request", "mark request done"],
        "guest.close_request", "front_desk.operate",
        {"guest_phone": _field("string", "The guest phone already on file, with country code"), "request": _field("string", "The open request text, or its request id")},
        {"title": "Close service request", "message": "Guest: {guest_name}\nPhone: {guest_phone}\nRequest: {request}\nMark this request done?", "approve_label": "Close request", "reject_label": "Cancel"},
        grain="guest",
    ),
    _action(
        "guest_preference", "Save a guest preference",
        ["guest preference", "save preference", "remember preference"],
        "guest.preference", "front_desk.operate",
        {
            "guest_phone": _field("string", "The guest phone already on file, with country code"),
            "preference": _field("string", "The preference name"),
            "value": _field("string", "The preference value"),
        },
        {"title": "Save guest preference", "message": "Guest: {guest_name}\nPhone: {guest_phone}\n{preference}: {value}\nSave this preference?", "approve_label": "Save preference", "reject_label": "Cancel"},
        grain="guest",
    ),
    _action(
        "guest_complaint", "Record a guest complaint",
        ["guest complaint", "record complaint", "file a complaint"],
        "guest.complaint", "front_desk.operate",
        {
            "guest_phone": _field("string", "The guest phone already on file, with country code"),
            "complaint": _field("string", "What the guest reported"),
            "booking_id": _field("string", "The booking id, when the complaint is about one stay", False),
        },
        {"title": "Record complaint", "message": "Guest: {guest_name}\nPhone: {guest_phone}\nReservation: {booking_id}\nComplaint: {complaint}\nRecord this complaint?", "approve_label": "Record complaint", "reject_label": "Cancel"},
        grain="guest",
    ),
    _action(
        "guest_deactivate", "Deactivate a guest",
        ["deactivate guest", "make guest inactive", "disable guest"],
        "guest.deactivate", "front_desk.operate",
        {"guest_phone": _field("string", "The guest phone already on file, with country code")},
        {"title": "Deactivate guest", "message": "Guest: {guest_name}\nPhone: {guest_phone}\nDeactivate this guest?", "approve_label": "Deactivate", "reject_label": "Cancel"},
        grain="guest",
    ),
    _action(
        "guest_update_profile", "Update a guest profile",
        ["update guest", "change guest name", "change guest email", "guest profile"],
        "guest.update_profile", "front_desk.operate",
        {
            "guest_phone": _field("string", "The guest phone already on file, with country code"),
            "guest_name": _field("string", "The new guest name", False),
            "email": _field("string", "The new email", False),
            "country": _field("string", "The new country", False),
            "special_request": _field("string", "The new special request", False),
        },
        {"title": "Update guest", "message": "Guest: {guest_name}\nPhone: {guest_phone}\nNew name: {updated_name}\nEmail: {email}\nCountry: {country}\nRequest: {special_request}\nSave this profile?", "approve_label": "Save", "reject_label": "Cancel"},
        grain="guest",
    ),
    _action(
        "housekeeping_create_task", "Create a cleaning task",
        ["create cleaning task", "assign cleaning", "new housekeeping task"],
        "housekeeping.create_task", "housekeeping.operate",
        {
            "room_number": _field("string", "The room number at this hotel"),
            "staff_phone": _field("string", "The housekeeper's phone, email, name, or staff id"),
            "task_type": _field("string", "The cleaning type. Leave empty for standard_cleaning", False),
            "priority": _field("string", "low, normal, high, or urgent. Leave empty for normal", False),
            "notes": _field("string", "A note for the task", False),
        },
        {"title": "Create cleaning task", "message": "Room: {room_number}\nStaff: {staff_name}\nType: {task_type}\nPriority: {priority}\nNote: {notes}\nCreate this task?", "approve_label": "Create task", "reject_label": "Cancel"},
        grain="room",
    ),
    _action(
        "housekeeping_start", "Start cleaning a room",
        ["start cleaning", "begin cleaning", "start housekeeping"],
        "housekeeping.start", "housekeeping.operate",
        {
            "room_number": _field("string", "The room number at this hotel"),
            "staff_phone": _field("string", "The housekeeper's phone, email, name, or staff id"),
            "task_type": _field("string", "The cleaning type. Leave empty for standard_cleaning", False),
            "priority": _field("string", "low, normal, high, or urgent. Leave empty for normal", False),
        },
        {"title": "Start cleaning", "message": "Room: {room_number}\nStaff: {staff_name}\nType: {task_type}\nPriority: {priority}\nStart cleaning this room?", "approve_label": "Start cleaning", "reject_label": "Cancel"},
        grain="room",
    ),
    _action(
        "housekeeping_progress", "Update cleaning progress",
        ["update cleaning", "cleaning progress", "finish cleaning", "inspect room"],
        "housekeeping.progress", "housekeeping.operate",
        {
            "cleaning_id": _field("string", "The cleaning task id"),
            "new_status": _field("string", "pending, assigned, in_progress, completed, inspected, or cancelled"),
            "staff_phone": _field("string", "The housekeeper to assign, when the assignee changes", False),
            "notes": _field("string", "A progress note", False),
        },
        {"title": "Update cleaning", "message": "Task: {cleaning_id}\nRoom: {room_number}\nNow: {current_status}\nNew status: {new_status}\nUpdate this task?", "approve_label": "Update cleaning", "reject_label": "Cancel"},
        grain="room", output="cleaning_id",
    ),
    _action(
        "housekeeping_clock_in", "Clock in housekeeping staff",
        ["clock in housekeeping", "housekeeper clock in", "start shift housekeeping"],
        "housekeeping.clock_in", "housekeeping.operate",
        {"staff_phone": _field("string", "The staff phone, email, name, or staff id")},
        {"title": "Clock in", "message": "Staff: {staff_name}\nClock this person in?", "approve_label": "Clock in", "reject_label": "Cancel"},
        grain="staff",
    ),
    _action(
        "housekeeping_clock_out", "Clock out housekeeping staff",
        ["clock out housekeeping", "housekeeper clock out", "end shift housekeeping"],
        "housekeeping.clock_out", "housekeeping.operate",
        {"staff_phone": _field("string", "The staff phone, email, name, or staff id")},
        {"title": "Clock out", "message": "Staff: {staff_name}\nClock this person out?", "approve_label": "Clock out", "reject_label": "Cancel"},
        grain="staff",
    ),
    _action(
        "maintenance_create", "Create a maintenance request",
        ["create maintenance", "report maintenance", "maintenance request"],
        "maintenance.create", "maintenance.operate",
        {
            "room_number": _field("string", "The room number. Leave empty when the issue is an area", False),
            "area": _field("string", "The area name. Leave empty when the issue is a room", False),
            "issue": _field("string", "What needs work"),
            "urgency": _field("string", "low, normal, high, or urgent. Leave empty for normal", False),
        },
        {"title": "Create maintenance request", "message": "Room: {room_number}\nArea: {area}\nIssue: {issue}\nUrgency: {urgency}\nCreate this request?", "approve_label": "Create request", "reject_label": "Cancel"},
        grain="room",
    ),
    _action(
        "maintenance_progress", "Update a maintenance request",
        ["update maintenance", "maintenance progress", "complete maintenance"],
        "maintenance.progress", "maintenance.operate",
        {
            "request_id": _field("string", "The maintenance request id"),
            "status": _field("string", "reported, assigned, in_progress, completed, or cancelled"),
            "staff_phone": _field("string", "The technician to assign", False),
            "notes": _field("string", "A progress note", False),
        },
        {"title": "Update maintenance", "message": "Request: {request_id}\nRoom: {room_number}\nArea: {area}\nIssue: {issue}\nNow: {current_status}\nNew status: {status}\nUpdate this request?", "approve_label": "Update request", "reject_label": "Cancel"},
        grain="room", output="request_id",
    ),
    _action(
        "reservation_price_override", "Override a reservation total",
        ["price override", "override total", "change booking total"],
        "reservation.price_override", "front_desk.manage",
        {"booking_id": _field("string", "The booking id at this hotel"), "new_total_amount": _field("number", "The new stay total, in the hotel currency")},
        {"title": "Override price", "message": "Guest: {guest_name}\nReservation: {booking_id}\nCurrent total: {current_total}\nNew total: {new_total_amount}\nApply this total?", "approve_label": "Override price", "reject_label": "Cancel"},
    ),
    _action(
        "inventory_set_prices", "Set room prices for a date range",
        ["set prices", "update rate", "change room price"],
        "inventory.set_prices", "reservations.operate",
        {
            "room_type": _field("string", "The room type name at this hotel"),
            "start_date": _field("date", "The first night, YYYY-MM-DD"),
            "end_date": _field("date", "The last night, YYYY-MM-DD"),
            "price": _field("number", "The nightly price, in the hotel currency"),
        },
        {"title": "Set prices", "message": "Room type: {room_type}\n{start_date} to {end_date}\nPrice: {price}\nSet these prices?", "approve_label": "Set prices", "reject_label": "Cancel"},
        grain="night",
    ),
    _action(
        "inventory_update_night", "Update one night's price and inventory",
        ["update night inventory", "stop sell", "change one night price"],
        "inventory.update_night", "reservations.operate",
        {
            "room_type": _field("string", "The room type name at this hotel"),
            "stay_date": _field("date", "The night, YYYY-MM-DD"),
            "price": _field("number", "The nightly price, in the hotel currency"),
            "quantity": _field("string", "Rooms to sell. 0 stops sale"),
        },
        {"title": "Update night", "message": "Room type: {room_type}\nNight: {stay_date}\nPrice: {price}\nQuantity: {quantity}\nUpdate this night?", "approve_label": "Update night", "reject_label": "Cancel"},
        grain="night",
    ),
    _action(
        "finance_post_charge", "Post a folio charge",
        ["post charge", "folio charge", "add a charge"],
        "finance.post_charge", "finance.operate",
        {
            "folio_type": _field("string", "guest, account, or outlet"),
            "folio_ref": _field("string", "The booking id, city-ledger account id, or outlet id"),
            "transaction_code": _field("string", "The charge code at this hotel"),
            "amount": _field("number", "The amount, in the hotel currency"),
        },
        {"title": "Post charge", "message": "Folio: {folio_type} {folio_ref}\nCode: {transaction_code}\nAmount: {amount}\nPost this charge?", "approve_label": "Post charge", "reject_label": "Cancel"},
        grain="transaction_code",
    ),
    _action(
        "finance_folio_payment", "Post a folio payment",
        ["folio payment", "post folio payment", "pay the folio"],
        "finance.folio_payment", "finance.operate",
        {
            "folio_type": _field("string", "guest, account, or outlet"),
            "folio_ref": _field("string", "The booking id, city-ledger account id, or outlet id"),
            "transaction_code": _field("string", "The payment or refund code at this hotel"),
            "amount": _field("number", "The amount received, in the hotel currency"),
        },
        {"title": "Post folio payment", "message": "Folio: {folio_type} {folio_ref}\nCode: {transaction_code}\nAmount: {amount}\nPost this payment?", "approve_label": "Post payment", "reject_label": "Cancel"},
        grain="transaction_code",
    ),
    _action(
        "finance_reverse_posting", "Reverse a folio posting",
        ["reverse posting", "void posting", "reverse a charge"],
        "finance.reverse_posting", "finance.operate",
        {"posting_id": _field("string", "The posting id to reverse"), "reason": _field("string", "Why the posting is reversed")},
        {"title": "Reverse posting", "message": "Posting: {posting_id}\nFolio: {folio_type} {folio_ref}\nAmount: {amount}\nReason: {reason}\nReverse this posting?", "approve_label": "Reverse", "reject_label": "Cancel"},
        grain="transaction_code", output="posting_id",
    ),
    _action(
        "finance_open_day", "Open the first business day",
        ["open business day", "open the day", "start business date"],
        "finance.open_day", "finance.manage",
        {"business_date": _field("date", "The first business date, YYYY-MM-DD")},
        {"title": "Open business day", "message": "Date: {business_date}\nOpen day now: {open_business_date}\nLatest day: {latest_business_date}\nOpen this business date?", "approve_label": "Open day", "reject_label": "Cancel"},
        grain="night", output="business_date",
    ),
    _action(
        "finance_close_day", "Close the business day",
        ["close business day", "night audit", "close the day"],
        "finance.close_day", "finance.manage",
        {"business_date": _field("date", "The open business date to close, YYYY-MM-DD")},
        {"title": "Close business day", "message": "Date: {business_date}\nOpen day now: {open_business_date}\nClose this business date?", "approve_label": "Close day", "reject_label": "Cancel"},
        grain="night", output="business_date",
    ),
    _action(
        "loyalty_award", "Award loyalty points",
        ["award points", "add loyalty points", "give points"],
        "loyalty.award", "front_desk.operate",
        {
            "guest_phone": _field("string", "The guest phone already on file, with country code"),
            "points": _field("number", "The points to add"),
            "reason": _field("string", "Why the points are awarded"),
            "booking_id": _field("string", "The booking id, when the points belong to one stay", False),
        },
        {"title": "Award points", "message": "Guest: {guest_name}\nPhone: {guest_phone}\nPoints: {points}\nReason: {reason}\nReservation: {booking_id}\nAward these points?", "approve_label": "Award points", "reject_label": "Cancel"},
        grain="guest",
    ),
    _action(
        "loyalty_redeem", "Redeem a loyalty reward",
        ["redeem points", "redeem reward", "use loyalty reward"],
        "loyalty.redeem", "front_desk.operate",
        {"guest_phone": _field("string", "The guest phone already on file, with country code"), "reward_name": _field("string", "The reward name")},
        {"title": "Redeem reward", "message": "Guest: {guest_name}\nPhone: {guest_phone}\nReward: {reward_name}\nPoints: {points_cost}\nRedeem this reward?", "approve_label": "Redeem", "reject_label": "Cancel"},
        grain="guest",
    ),
]

RETIRED = ["daily_rooms_available"]

UNSUPPORTED = {
    "ota_commission": "No general recipe reads booking.commission_amount or commission_rate yet; channels often send them as zero or absent.",
}


def _publish(cur, body, source=SOURCE):
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
            (catalog_id, body["parent"], body["parent"], body["purpose"]),
        )
    cur.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM catalog_versions WHERE catalog_id = %s", (catalog_id,))
    version = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO catalog_versions (catalog_id, version, body, provenance) VALUES (%s, %s, %s, %s)",
        (catalog_id, version, Json(body), Json({"source": source})),
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
    for body in list(RECIPES) + list(ACTION_RECIPES):
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
