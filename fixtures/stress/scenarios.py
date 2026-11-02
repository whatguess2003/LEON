"""Thirty desk conversations on SANDBOX HOTEL. Each has a goal the simulated manager pursues, a persona hint, the figures the
judge reads from the desk's replies, and an independent read-only SQL that computes those figures from the base tables
(not from recipe SQL) after every desk turn. Action scenarios also carry state checks run once the conversation ends.

clarify: never (the question is fully specified for this profile), maybe (either is fine), required (the phrase is not
defined anywhere and the desk must ask what it means)."""

T = ("WITH t AS (SELECT (now() AT TIME ZONE h.timezone)::date AS d, h.timezone AS tz,"
     " (SELECT max(business_date) FROM business_day WHERE hotel_id = h.hotel_id AND status = 'open') AS bd_open,"
     " (SELECT max(business_date) FROM business_day WHERE hotel_id = h.hotel_id AND status = 'closed') AS bd_closed"
     " FROM hotel h WHERE h.hotel_id = %(h)s)")
LIVE_NIGHT = "n.night_status NOT IN ('cancelled', 'no_show')"
ACTIVE = "s.booking_status NOT IN ('cancelled', 'no_show')"
# The manager's in-house definition on the profile: bookings sold for tonight, checked in or not, by party size.
IN_HOUSE = ("EXISTS (SELECT 1 FROM booking_room_night n, t WHERE n.booking_id = b.booking_id AND n.stay_date = t.d AND "
            + LIVE_NIGHT + ")")
SOLD = ("(SELECT count(*) FROM booking_room_night n WHERE n.hotel_id = i.hotel_id AND n.room_type_id = i.room_type_id "
        "AND n.stay_date = i.stay_date AND " + LIVE_NIGHT + ")")


def SETTING(key, day):
    """The hotel's operating setting in effect on day (hotel_setting is versioned by effective_from)."""
    return ("(SELECT hs.value #>> '{}' FROM hotel_setting hs WHERE hs.hotel_id = %(h)s AND hs.setting_key = '" + key + "'"
            " AND hs.effective_from <= " + day + " ORDER BY hs.effective_from DESC, hs.set_at DESC LIMIT 1)")


def CHECK_NET(where):
    """Net F&B sales of closed checks: after discounts, and with the tax taken out when prices were tax-inclusive on the
    check's business date (finance.tax_pricing)."""
    return ("(SELECT coalesce(sum(c.subtotal - coalesce(c.discount_total, 0) - CASE WHEN " + SETTING("finance.tax_pricing", "c.business_date")
            + " = 'inclusive' THEN coalesce(c.tax_total, 0) ELSE 0 END), 0) FROM fnb_check c WHERE c.hotel_id = %(h)s AND c.status = 'closed' AND "
            + where + ")")


def FNB_ADJUSTED(where):
    """Closed-check corrections: fnb.closed_check_correction is folio_adjustment, so they are adjustment postings, not reopened checks."""
    return ("(SELECT coalesce(sum(p.amount), 0) FROM finance_posting p WHERE p.hotel_id = %(h)s AND p.source_module = 'fnb'"
            " AND p.transaction_type = 'adjustment' AND " + where + ")")


def FNB_REVENUE(day):
    return "(" + CHECK_NET("c.business_date = " + day) + " + " + FNB_ADJUSTED("p.business_date = " + day) + ")"

# What this manager means. The simulator answers the desk's definition questions from these and never sees the figures.
PERSONA_DEFINITIONS = {
    "arrivals": "Bookings scheduled to arrive that day, excluding cancelled bookings and no-shows.",
    "departures": "Bookings scheduled to check out that day, excluding cancelled bookings and no-shows.",
    "in house": "Bookings sold for tonight, whether checked in yet or not, counted by party size (adults plus children).",
    "reservations": "Confirmed bookings whose stay is current or still ahead.",
    "room availability / rooms left": "Rooms of each type in inventory, minus blocked rooms, minus nights already sold that night.",
    "room nights sold": "Booked room nights for that night, excluding cancelled and no-show nights.",
    "occupancy": "Room nights sold that night divided by the total rooms in inventory that night, as a percentage.",
    "room revenue": "The nightly room rates of room nights that were not cancelled and not no-shows, counted on the stay night.",
    "ADR": "Room revenue divided by room nights sold for the same nights.",
    "busy": "How full the hotel is tonight, meaning occupancy.",
    "OTA bookings": "Bookings that came through an online travel agency such as Booking.com, Expedia or Agoda. Direct means PMS, website or walk-in.",
    "F&B revenue / restaurant sales": ("Net sales of closed F&B checks after discounts and without tax, less any folio adjustments; voided checks "
                                       "do not count. Counted by business date, not calendar date."),
    "business date": ("F&B and finance figures are by business date. 'Today' for F&B or finance means the business date that is open now; "
                      "'the day we just closed' means the latest closed business date."),
    "covers": "Guests served on closed F&B checks, by business date.",
    "late": "A clock-in any minute after the rostered shift start counts as late. A rostered shift with no clock-in is missing, not late.",
    "worked hours": "Clock-out minus clock-in, minus the rostered shift's unpaid break; only shifts with both a clock-in and a clock-out.",
    "leave days": "Every calendar day from the first to the last day of the leave.",
    "family stay": "A booking with at least one child on it.",
    "outstanding balance / what guests owe": "Booking total minus payments received, for bookings that are not cancelled and not no-shows.",
    "lost revenue from cancellations": "The booking total of each cancelled booking.",
    "last 7 / last 14 days": "The 7 (or 14) nights before tonight, not including tonight.",
    "previous 7 days": "The 7 nights before the last 7 days.",
    "cancellations in a period": "Bookings whose cancellation was recorded in that period, by the hotel's local date.",
    "this week / next 7 days": "Today and the six days after it.",
}


def _figure(name, kind, description):
    return {"name": name, "kind": kind, "description": description}


SCENARIOS = [
    # ---------------------------------------------------------------- easy: one turn
    {
        "id": "E01", "tier": "easy", "clarify": "maybe",
        "persona": "Busy GM between meetings, terse.",
        "goal": ["Find out how many arrivals the hotel has today."],
        "figures": [_figure("arrivals", "count", "number of arrivals today")],
        "truth": T + " SELECT count(*) AS arrivals FROM booking_summary s, t WHERE s.hotel_id = %(h)s AND s.check_in_date = t.d AND " + ACTIVE,
    },
    {
        "id": "E02", "tier": "easy", "clarify": "maybe",
        "persona": "Revenue-minded manager checking whether to push rates.",
        "goal": ["Find out how many rooms are still left to sell tonight for each room type (DELUXE and FAMILY)."],
        "figures": [_figure("deluxe_left", "count", "DELUXE rooms left to sell tonight"),
                    _figure("family_left", "count", "FAMILY rooms left to sell tonight")],
        "truth": T + """, x AS (SELECT rt.room_type_name AS name, i.total_inventory - i.blocked_quantity - """ + SOLD + """ AS left_n
            FROM t, room_inventory_nightly i JOIN room_type rt ON rt.room_type_id = i.room_type_id
            WHERE i.hotel_id = %(h)s AND i.stay_date = t.d)
            SELECT sum(left_n) FILTER (WHERE name = 'DELUXE') AS deluxe_left, sum(left_n) FILTER (WHERE name = 'FAMILY') AS family_left FROM x""",
    },
    {
        "id": "E03", "tier": "easy", "clarify": "never",
        "persona": "Night manager doing a headcount for fire safety.",
        "goal": ["Find out how many guests are in house right now."],
        "figures": [_figure("in_house_guests", "count", "guests currently in house")],
        "truth": T + " SELECT coalesce(sum(b.adults + b.children), 0) AS in_house_guests FROM booking b WHERE b.hotel_id = %(h)s AND " + IN_HOUSE,
    },
    {
        "id": "E04", "tier": "easy", "clarify": "maybe",
        "persona": "Front office manager planning the morning.",
        "goal": ["Find out how many departures there are today."],
        "figures": [_figure("departures", "count", "number of departures today")],
        "truth": T + " SELECT count(*) AS departures FROM booking_summary s, t WHERE s.hotel_id = %(h)s AND s.check_out_date = t.d AND " + ACTIVE,
    },
    {
        "id": "E05", "tier": "easy", "clarify": "never",
        "persona": "F&B director, casual tone.",
        "goal": ["Find out how many covers the restaurant served on the business date that was just closed."],
        "figures": [_figure("covers", "count", "F&B covers served on the latest closed business date")],
        "truth": T + " SELECT coalesce(sum(c.covers) FILTER (WHERE c.status = 'closed'), 0) AS covers FROM t, fnb_check c WHERE c.hotel_id = %(h)s AND c.business_date = t.bd_closed",
    },
    {
        "id": "E06", "tier": "easy", "clarify": "never",
        "persona": "Owner who wants one number.",
        "goal": ["Find out the F&B revenue (net sales) of the business date that is open now."],
        "figures": [_figure("fnb_net_sales", "money", "F&B net sales of the open business date in MYR")],
        "truth": T + " SELECT " + FNB_REVENUE("t.bd_open") + " AS fnb_net_sales FROM t",
    },
    {
        "id": "E07", "tier": "easy", "clarify": "never",
        "persona": "HR manager checking the roster.",
        "goal": ["Find out how many staff are rostered today and how many of them have clocked in."],
        "figures": [_figure("rostered", "count", "staff rostered today"), _figure("clocked_in", "count", "rostered staff who clocked in today")],
        "truth": T + """ SELECT count(DISTINCT r.staff_id) AS rostered, count(DISTINCT r.staff_id) FILTER (WHERE a.clock_in IS NOT NULL) AS clocked_in
            FROM t, roster r LEFT JOIN staff_attendance a ON a.roster_id = r.roster_id WHERE r.hotel_id = %(h)s AND r.work_date = t.d""",
    },
    {
        "id": "E08", "tier": "easy", "clarify": "never",
        "persona": "HR manager, polite.",
        "goal": ["Find out how many leave days are waiting for approval (requested, not yet approved)."],
        "figures": [_figure("pending_leave_days", "count", "leave days in requests still pending approval")],
        "truth": T + """ SELECT coalesce(sum((SELECT count(*) FROM generate_series(l.start_date, l.end_date, interval '1 day') g(day)
                WHERE """ + SETTING("hr.leave_day_counting", "t.d") + """ = 'calendar_days'
                   OR to_char(g.day, 'dy') = ANY(ARRAY(SELECT jsonb_array_elements_text(coalesce((SELECT hs.value FROM hotel_setting hs
                      WHERE hs.hotel_id = %(h)s AND hs.setting_key = 'hr.working_weekdays' AND hs.effective_from <= t.d
                      ORDER BY hs.effective_from DESC, hs.set_at DESC LIMIT 1), '[]'::jsonb)))))), 0) AS pending_leave_days
            FROM t, leave_request l WHERE l.hotel_id = %(h)s AND l.status = 'requested'""",
    },
    {
        "id": "E09", "tier": "easy", "clarify": "never",
        "persona": "Reservations manager following up on yesterday.",
        "goal": ["Find out how many no-shows the hotel had yesterday."],
        "figures": [_figure("no_shows", "count", "no-show bookings yesterday")],
        "truth": T + " SELECT count(*) AS no_shows FROM booking_summary s, t WHERE s.hotel_id = %(h)s AND s.booking_status = 'no_show' AND s.check_in_date = t.d - 1",
    },
    {
        "id": "E10", "tier": "easy", "clarify": "never",
        "persona": "Revenue manager doing the daily pickup.",
        "goal": ["Find out how many room nights were sold last night."],
        "figures": [_figure("room_nights_last_night", "count", "room nights sold last night")],
        "truth": T + " SELECT count(*) AS room_nights_last_night FROM t, booking_room_night n WHERE n.hotel_id = %(h)s AND n.stay_date = t.d - 1 AND " + LIVE_NIGHT,
    },
    # ---------------------------------------------------------------- intermediate: 2-3 turns
    {
        "id": "I01", "tier": "intermediate", "clarify": "maybe",
        "persona": "GM who speaks loosely; says 'busy' rather than occupancy.",
        "goal": ["Ask how busy the hotel is tonight (you mean occupancy).", "Then ask for that broken down by room type."],
        "figures": [_figure("occupancy_pct", "pct", "hotel occupancy tonight in percent"),
                    _figure("deluxe_occupancy_pct", "pct", "DELUXE occupancy tonight in percent"),
                    _figure("family_occupancy_pct", "pct", "FAMILY occupancy tonight in percent")],
        "truth": T + """, x AS (SELECT rt.room_type_name AS name, i.total_inventory AS inv, """ + SOLD + """ AS sold
            FROM t, room_inventory_nightly i JOIN room_type rt ON rt.room_type_id = i.room_type_id WHERE i.hotel_id = %(h)s AND i.stay_date = t.d)
            SELECT round(100.0 * sum(sold) / nullif(sum(inv), 0), 2) AS occupancy_pct,
                   round(100.0 * sum(sold) FILTER (WHERE name = 'DELUXE') / nullif(sum(inv) FILTER (WHERE name = 'DELUXE'), 0), 2) AS deluxe_occupancy_pct,
                   round(100.0 * sum(sold) FILTER (WHERE name = 'FAMILY') / nullif(sum(inv) FILTER (WHERE name = 'FAMILY'), 0), 2) AS family_occupancy_pct FROM x""",
    },
    {
        "id": "I02", "tier": "intermediate", "clarify": "maybe",
        "persona": "Distribution manager worried about OTA dependence.",
        "goal": ["Ask how many arrivals there are today.", "Then ask how many of those came through an OTA and how much commission they carry."],
        "figures": [_figure("arrivals", "count", "arrivals today"), _figure("ota_arrivals", "count", "arrivals today booked through an OTA"),
                    _figure("ota_commission", "money", "commission on today's OTA arrivals in MYR")],
        "truth": T + """ SELECT count(*) AS arrivals, count(*) FILTER (WHERE s.channel NOT IN ('PMS', 'WEBSITE', 'WALK_IN', 'WALKIN', 'DIRECT')) AS ota_arrivals,
            coalesce(sum(s.commission_amount) FILTER (WHERE s.channel NOT IN ('PMS', 'WEBSITE', 'WALK_IN', 'WALKIN', 'DIRECT')), 0) AS ota_commission
            FROM booking_summary s, t WHERE s.hotel_id = %(h)s AND s.check_in_date = t.d AND """ + ACTIVE,
    },
    {
        "id": "I03", "tier": "intermediate", "clarify": "never",
        "persona": "Owner comparing days, a bit impatient.",
        "goal": ["Ask for room revenue last night.", "Then ask how that compares with the night before (both figures and the difference)."],
        "figures": [_figure("revenue_last_night", "money", "room revenue last night in MYR"),
                    _figure("revenue_night_before", "money", "room revenue the night before last in MYR"),
                    _figure("revenue_difference", "money", "last night minus the night before, in MYR")],
        "truth": T + """, r AS (SELECT coalesce(sum(n.sell_rate) FILTER (WHERE n.stay_date = t.d - 1), 0) AS a,
            coalesce(sum(n.sell_rate) FILTER (WHERE n.stay_date = t.d - 2), 0) AS b
            FROM t, booking_room_night n WHERE n.hotel_id = %(h)s AND """ + LIVE_NIGHT + """)
            SELECT a AS revenue_last_night, b AS revenue_night_before, a - b AS revenue_difference FROM r""",
    },
    {
        "id": "I04", "tier": "intermediate", "clarify": "never",
        "persona": "F&B director preparing for the morning meeting.",
        "goal": ["Ask for restaurant sales on the business date that was just closed.", "Then ask how much of it was charged to guest rooms versus paid directly at the outlet."],
        "figures": [_figure("fnb_net_sales", "money", "F&B net sales of the latest closed business date in MYR"),
                    _figure("charged_to_room", "money", "of that, sales charged to guest rooms in MYR"),
                    _figure("paid_direct", "money", "of that, sales settled directly at the outlet in MYR")],
        "truth": T + " SELECT " + FNB_REVENUE("t.bd_closed") + " AS fnb_net_sales, "
            + CHECK_NET("c.business_date = t.bd_closed AND c.settlement = 'room'") + " + "
            + FNB_ADJUSTED("p.business_date = t.bd_closed AND p.folio_type = 'guest'") + " AS charged_to_room, "
            + CHECK_NET("c.business_date = t.bd_closed AND c.settlement = 'direct'") + " + "
            + FNB_ADJUSTED("p.business_date = t.bd_closed AND p.folio_type = 'outlet'") + " AS paid_direct FROM t",
    },
    {
        "id": "I05", "tier": "intermediate", "clarify": "never",
        "persona": "Guest relations manager planning a kids' activity.",
        "goal": ["Ask how many guests are in house now.", "Then ask how many of them are children and how many adults."],
        "figures": [_figure("in_house_guests", "count", "guests in house now"), _figure("in_house_children", "count", "children in house now"),
                    _figure("in_house_adults", "count", "adults in house now")],
        "truth": T + """ SELECT coalesce(sum(b.adults + b.children), 0) AS in_house_guests, coalesce(sum(b.children), 0) AS in_house_children,
            coalesce(sum(b.adults), 0) AS in_house_adults FROM booking b WHERE b.hotel_id = %(h)s AND """ + IN_HOUSE,
    },
    {
        "id": "I06", "tier": "intermediate", "clarify": "maybe",
        "persona": "Revenue manager, precise wording.",
        "goal": ["Ask for ADR over the last 7 days.", "Then ask for ADR by room type for the same period."],
        "figures": [_figure("adr_7d", "money", "ADR over the last 7 nights in MYR"), _figure("adr_7d_deluxe", "money", "DELUXE ADR over the last 7 nights"),
                    _figure("adr_7d_family", "money", "FAMILY ADR over the last 7 nights")],
        "truth": T + """, x AS (SELECT rt.room_type_name AS name, n.sell_rate FROM t, booking_room_night n JOIN room_type rt ON rt.room_type_id = n.room_type_id
            WHERE n.hotel_id = %(h)s AND n.stay_date BETWEEN t.d - 7 AND t.d - 1 AND """ + LIVE_NIGHT + """)
            SELECT round(avg(sell_rate), 2) AS adr_7d, round(avg(sell_rate) FILTER (WHERE name = 'DELUXE'), 2) AS adr_7d_deluxe,
                   round(avg(sell_rate) FILTER (WHERE name = 'FAMILY'), 2) AS adr_7d_family FROM x""",
    },
    {
        "id": "I07", "tier": "intermediate", "clarify": "maybe",
        "persona": "HR manager annoyed about punctuality.",
        "goal": ["Ask how many staff were late for their shift yesterday.", "Then ask how many rostered shifts had no clock-in at all.",
                 "Then ask how many hours staff actually worked yesterday in total."],
        "figures": [_figure("late_count", "count", "late clock-ins yesterday"), _figure("missing_clock_ins", "count", "rostered shifts yesterday without a clock-in"),
                    _figure("worked_hours", "hours", "total hours worked yesterday")],
        "truth": T + """, x AS (SELECT a.clock_in, a.clock_out, s.break_minutes, r.work_date, ((r.work_date + s.starts_at) AT TIME ZONE t.tz) AS start_at
            FROM t, roster r JOIN shift s ON s.shift_id = r.shift_id LEFT JOIN staff_attendance a ON a.roster_id = r.roster_id
            WHERE r.hotel_id = %(h)s AND r.work_date = t.d - 1)
            SELECT count(*) FILTER (WHERE clock_in > start_at) AS late_count, count(*) FILTER (WHERE clock_in IS NULL) AS missing_clock_ins,
                   round(coalesce(sum(extract(epoch FROM clock_out - clock_in) / 3600.0
                       - CASE WHEN """ + SETTING("hr.worked_hours_breaks", "x.work_date") + """ = 'deduct_unpaid_breaks' THEN coalesce(break_minutes, 0) / 60.0 ELSE 0 END)
                       FILTER (WHERE clock_in IS NOT NULL AND clock_out IS NOT NULL), 0), 2) AS worked_hours FROM x""",
    },
    {
        "id": "I08", "tier": "intermediate", "clarify": "maybe",
        "persona": "Front office manager looking ahead at the week.",
        "goal": ["Ask how many arrivals are coming in over the next 7 days (today included).", "Then ask how many room nights are booked over those same 7 nights."],
        "figures": [_figure("arrivals_7d", "count", "arrivals over today and the next six days"),
                    _figure("room_nights_7d", "count", "room nights booked over tonight and the next six nights")],
        "truth": T + """ SELECT (SELECT count(*) FROM booking_summary s WHERE s.hotel_id = %(h)s AND s.check_in_date BETWEEN t.d AND t.d + 6 AND """ + ACTIVE + """) AS arrivals_7d,
            (SELECT count(*) FROM booking_room_night n WHERE n.hotel_id = %(h)s AND n.stay_date BETWEEN t.d AND t.d + 6 AND """ + LIVE_NIGHT + """) AS room_nights_7d FROM t""",
    },
    {
        "id": "I09", "tier": "intermediate", "clarify": "maybe",
        "persona": "Owner worried about cancellations.",
        "goal": ["Ask how many bookings were cancelled in the last 7 days (count cancellations recorded today and the six days before).", "Then ask how much revenue those cancellations represent."],
        "figures": [_figure("cancellations_7d", "count", "bookings cancelled in the last 7 days"),
                    _figure("cancelled_value", "money", "total booking value of those cancellations in MYR")],
        "truth": T + """ SELECT count(*) AS cancellations_7d, coalesce(sum(b.gross_amount), 0) AS cancelled_value FROM t, booking b
            WHERE b.hotel_id = %(h)s AND b.booking_status = 'cancelled' AND (b.cancelled_at AT TIME ZONE t.tz)::date BETWEEN t.d - 6 AND t.d""",
    },
    {
        "id": "I10", "tier": "intermediate", "clarify": "required",
        "persona": "GM who uses in-house jargon: 'family stays'.",
        "goal": ["Ask how many family stays are in house right now (use the phrase 'family stays' without explaining it unless asked).", "Then ask how many guests those family stays bring."],
        "figures": [_figure("family_stays_in_house", "count", "family-stay bookings in house now"),
                    _figure("family_stay_guests", "count", "guests on those family-stay bookings")],
        "truth": T + """ SELECT count(*) AS family_stays_in_house, coalesce(sum(b.adults + b.children), 0) AS family_stay_guests
            FROM booking b WHERE b.hotel_id = %(h)s AND b.children > 0 AND """ + IN_HOUSE,
    },
    # ---------------------------------------------------------------- hard: more than 3 turns
    {
        "id": "H01", "tier": "hard", "clarify": "maybe",
        "persona": "Owner wanting a cross-department snapshot, asks one thing at a time.",
        "goal": ["Ask for tonight's occupancy.", "Then ask for tonight's room revenue.", "Then ask for F&B net sales of the business date that was just closed.",
                 "Then ask for the combined total of tonight's room revenue and those F&B net sales."],
        "figures": [_figure("occupancy_pct", "pct", "occupancy tonight in percent"), _figure("room_revenue_tonight", "money", "room revenue tonight in MYR"),
                    _figure("fnb_net_sales", "money", "F&B net sales of the latest closed business date in MYR"),
                    _figure("combined_revenue", "money", "room revenue tonight plus those F&B net sales")],
        "truth": T + """, r AS (SELECT (SELECT count(*) FROM booking_room_night n WHERE n.hotel_id = %(h)s AND n.stay_date = t.d AND """ + LIVE_NIGHT + """) AS sold,
            (SELECT sum(i.total_inventory) FROM room_inventory_nightly i WHERE i.hotel_id = %(h)s AND i.stay_date = t.d) AS inv,
            (SELECT coalesce(sum(n.sell_rate), 0) FROM booking_room_night n WHERE n.hotel_id = %(h)s AND n.stay_date = t.d AND """ + LIVE_NIGHT + """) AS rooms,
            """ + FNB_REVENUE("t.bd_closed") + """ AS fnb FROM t)
            SELECT round(100.0 * sold / nullif(inv, 0), 2) AS occupancy_pct, rooms AS room_revenue_tonight, fnb AS fnb_net_sales, rooms + fnb AS combined_revenue FROM r""",
    },
    {
        "id": "H02", "tier": "hard", "clarify": "maybe",
        "persona": "Financial controller investigating receivables.",
        "goal": ["Ask how much guests owe the hotel in total right now.", "Then ask how much of that is owed by bookings staying tonight versus bookings arriving after today.",
                 "Then ask which guest owes the most and how much.", "If anything looks off, push back once and ask the desk to double-check."],
        "figures": [_figure("total_outstanding", "money", "total outstanding balance in MYR"),
                    _figure("tonight_outstanding", "money", "outstanding balance of bookings staying tonight in MYR"),
                    _figure("future_outstanding", "money", "outstanding balance of bookings arriving after today in MYR"),
                    _figure("top_debtor", "text", "name of the guest who owes the most"), _figure("top_debtor_amount", "money", "amount the top debtor owes in MYR")],
        "truth": T + """, x AS (SELECT g.guest_name, bb.balance_due, s.check_in_date, t.d, """ + IN_HOUSE.replace("b.booking_id", "s.booking_id") + """ AS tonight
            FROM t, booking_summary s JOIN booking_balance bb ON bb.booking_id = s.booking_id JOIN guest g ON g.guest_id = s.guest_id
            WHERE s.hotel_id = %(h)s AND """ + ACTIVE + """), top AS (SELECT guest_name, sum(balance_due) AS owed FROM x GROUP BY 1 ORDER BY 2 DESC LIMIT 1)
            SELECT coalesce(sum(balance_due), 0) AS total_outstanding, coalesce(sum(balance_due) FILTER (WHERE tonight), 0) AS tonight_outstanding,
                   coalesce(sum(balance_due) FILTER (WHERE check_in_date > d), 0) AS future_outstanding,
                   (SELECT guest_name FROM top) AS top_debtor, (SELECT owed FROM top) AS top_debtor_amount FROM x""",
    },
    {
        "id": "H03", "tier": "hard", "clarify": "maybe",
        "persona": "Revenue manager writing a weekly report, methodical.",
        "goal": ["Ask for room nights sold over the last 7 days.", "Then ask for room revenue over the same 7 days.",
                 "Then ask for the same two figures for the previous 7 days.", "Then ask whether the week was up or down and by how much revenue."],
        "figures": [_figure("nights_last7", "count", "room nights sold in the last 7 nights"), _figure("revenue_last7", "money", "room revenue in the last 7 nights"),
                    _figure("nights_prev7", "count", "room nights sold in the 7 nights before that"), _figure("revenue_prev7", "money", "room revenue in the 7 nights before that"),
                    _figure("revenue_change", "money", "last 7 nights revenue minus previous 7 nights revenue")],
        "truth": T + """, r AS (SELECT count(*) FILTER (WHERE n.stay_date BETWEEN t.d - 7 AND t.d - 1) AS a_n,
            coalesce(sum(n.sell_rate) FILTER (WHERE n.stay_date BETWEEN t.d - 7 AND t.d - 1), 0) AS a_r,
            count(*) FILTER (WHERE n.stay_date BETWEEN t.d - 14 AND t.d - 8) AS b_n,
            coalesce(sum(n.sell_rate) FILTER (WHERE n.stay_date BETWEEN t.d - 14 AND t.d - 8), 0) AS b_r
            FROM t, booking_room_night n WHERE n.hotel_id = %(h)s AND """ + LIVE_NIGHT + """)
            SELECT a_n AS nights_last7, a_r AS revenue_last7, b_n AS nights_prev7, b_r AS revenue_prev7, a_r - b_r AS revenue_change FROM r""",
    },
    {
        "id": "H04", "tier": "hard", "clarify": "maybe",
        "persona": "Night manager who counts only adults for the evacuation list and says so only after the first answer.",
        "goal": ["Ask how many guests are in house.", "After the answer, tell the desk that number is wrong for your purpose because you only count adults, and complain that it should have said so.",
                 "Get the number of adults in house.", "Then ask how many in-house rooms (bookings) those adults are spread across."],
        "figures": [_figure("in_house_guests", "count", "guests in house"), _figure("in_house_adults", "count", "adults in house"),
                    _figure("in_house_bookings", "count", "in-house bookings")],
        "truth": T + """ SELECT coalesce(sum(b.adults + b.children), 0) AS in_house_guests, coalesce(sum(b.adults), 0) AS in_house_adults,
            count(*) AS in_house_bookings FROM booking b WHERE b.hotel_id = %(h)s AND """ + IN_HOUSE,
        "expect_complaint": True,
    },
    {
        "id": "H05", "tier": "hard", "clarify": "maybe", "action": True,
        "persona": "Reservations agent taking a phone booking, then undoing it because the guest changed their mind.",
        "goal": ["Ask the desk to create a booking for guest {guest}, phone +15555550199, email stress.guest@example.com, one DELUXE room for 1 night arriving tomorrow, 2 adults, rate as usual. Confirm when asked.",
                 "Ask how many DELUXE rooms are left for tomorrow night.",
                 "Tell the desk the guest changed their mind and ask it to cancel that booking. Confirm when asked.",
                 "Ask again how many DELUXE rooms are left for tomorrow night."],
        "figures": [_figure("deluxe_left_tomorrow", "count", "DELUXE rooms left to sell tomorrow night")],
        "truth": T + """ SELECT i.total_inventory - i.blocked_quantity - """ + SOLD + """ AS deluxe_left_tomorrow
            FROM t, room_inventory_nightly i JOIN room_type rt ON rt.room_type_id = i.room_type_id
            WHERE i.hotel_id = %(h)s AND i.stay_date = t.d + 1 AND rt.room_type_name = 'DELUXE'""",
        "checks": [{
            "label": "stress booking created during the scenario and cancelled at the end",
            "sql": T + """ SELECT count(*) AS created, count(*) FILTER (WHERE s.booking_status = 'cancelled') AS cancelled,
                count(*) FILTER (WHERE s.check_in_date = t.d + 1 AND s.nights = 1) AS right_dates
                FROM t, booking_summary s JOIN guest g ON g.guest_id = s.guest_id
                WHERE s.hotel_id = %(h)s AND g.guest_name = upper(%(guest)s) AND s.booked_at >= %(started)s""",
            "expect": {"created": 1, "cancelled": 1, "right_dates": 1},
        }],
    },
    {
        "id": "H06", "tier": "hard", "clarify": "never", "action": True,
        "persona": "Housekeeping supervisor on the floor.",
        "goal": ["Tell the desk room 103 is dirty and ask it to mark it dirty.", "Ask how many rooms are clean right now.",
                 "Tell the desk room 103 has been cleaned and ask it to mark it clean.", "Ask again how many rooms are clean right now."],
        "figures": [_figure("clean_rooms", "count", "rooms currently clean")],
        "truth": T + " SELECT count(*) FILTER (WHERE s.readiness_status = 'clean') AS clean_rooms FROM room_readiness_status s JOIN room r ON r.room_id = s.room_id WHERE s.hotel_id = %(h)s AND r.status = 'active'",
        "checks": [{
            "label": "room 103 was changed during the scenario and ends clean",
            "sql": T + """ SELECT count(*) FILTER (WHERE s.readiness_status = 'clean') AS clean_now, count(*) FILTER (WHERE s.changed_at >= %(started)s) AS changed
                FROM room_readiness_status s JOIN room r ON r.room_id = s.room_id WHERE s.hotel_id = %(h)s AND r.room_number = '103'""",
            "expect": {"clean_now": 1, "changed": 1},
        }],
    },
    {
        "id": "H07", "tier": "hard", "clarify": "maybe", "catalog": True,
        "persona": "Asset manager with an unusual analytical question.",
        "goal": ["Ask which room number earned the most room revenue over the last 14 days.", "Ask how many nights that room was occupied in that period.",
                 "Ask for the same two figures for room 201 to compare.", "Ask what share of total room revenue in the period the top room earned."],
        "figures": [_figure("top_room", "text", "room number with the highest room revenue in the last 14 nights"),
                    _figure("top_room_revenue", "money", "room revenue of that room in MYR"), _figure("top_room_nights", "count", "nights that room was occupied"),
                    _figure("room201_revenue", "money", "room revenue of room 201 in the last 14 nights"), _figure("room201_nights", "count", "nights room 201 was occupied"),
                    _figure("top_room_share_pct", "pct", "top room's share of total room revenue in percent")],
        "truth": T + """, x AS (SELECT r.room_number, count(*) AS nights, sum(n.sell_rate) AS revenue FROM t, booking_room_night n JOIN room r ON r.room_id = n.room_id
            WHERE n.hotel_id = %(h)s AND n.stay_date BETWEEN t.d - 14 AND t.d - 1 AND """ + LIVE_NIGHT + """ GROUP BY 1),
            top AS (SELECT * FROM x ORDER BY revenue DESC, room_number LIMIT 1)
            SELECT (SELECT room_number FROM top) AS top_room, (SELECT revenue FROM top) AS top_room_revenue, (SELECT nights FROM top) AS top_room_nights,
                   coalesce((SELECT revenue FROM x WHERE room_number = '201'), 0) AS room201_revenue, coalesce((SELECT nights FROM x WHERE room_number = '201'), 0) AS room201_nights,
                   round(100.0 * (SELECT revenue FROM top) / nullif((SELECT sum(revenue) FROM x), 0), 2) AS top_room_share_pct""",
    },
    {
        "id": "H08", "tier": "hard", "clarify": "maybe",
        "persona": "Front office manager whose follow-ups depend on the previous answer ('he', 'that guest').",
        "goal": ["Ask who is checking out today.", "Ask whether he/she still owes anything on the booking (refer to the guest only by pronoun).",
                 "Ask whether that guest spent anything at the restaurant.", "Ask the same two questions about the family staying in room 201."],
        "figures": [_figure("departures", "count", "departures today"), _figure("departing_guest", "text", "name of the guest checking out today"),
                    _figure("departing_balance", "money", "balance still owed on the departing guest's booking"),
                    _figure("departing_fnb", "money", "F&B spend of the departing guest"),
                    _figure("room201_balance", "money", "balance still owed on the booking in room 201"),
                    _figure("room201_fnb", "money", "F&B spend charged by the guests in room 201")],
        "truth": T + """, dep AS (SELECT s.booking_id, g.guest_name FROM t, booking_summary s JOIN guest g ON g.guest_id = s.guest_id
                WHERE s.hotel_id = %(h)s AND s.check_out_date = t.d AND """ + ACTIVE + """ ORDER BY s.booking_id LIMIT 1),
            r201 AS (SELECT DISTINCT n.booking_id FROM t, booking_room_night n JOIN room r ON r.room_id = n.room_id
                WHERE n.hotel_id = %(h)s AND r.room_number = '201' AND n.stay_date = t.d AND """ + LIVE_NIGHT + """)
            SELECT (SELECT count(*) FROM t, booking_summary s WHERE s.hotel_id = %(h)s AND s.check_out_date = t.d AND """ + ACTIVE + """) AS departures,
                   (SELECT guest_name FROM dep) AS departing_guest,
                   (SELECT balance_due FROM booking_balance WHERE booking_id = (SELECT booking_id FROM dep)) AS departing_balance,
                   (SELECT coalesce(sum(total), 0) FROM fnb_check WHERE status = 'closed' AND booking_id = (SELECT booking_id FROM dep)) AS departing_fnb,
                   (SELECT sum(balance_due) FROM booking_balance WHERE booking_id IN (SELECT booking_id FROM r201)) AS room201_balance,
                   (SELECT coalesce(sum(total), 0) FROM fnb_check WHERE status = 'closed' AND booking_id IN (SELECT booking_id FROM r201)) AS room201_fnb""",
    },
    {
        "id": "H09", "tier": "hard", "clarify": "maybe",
        "persona": "Rooms division manager checking staffing against workload.",
        "goal": ["Ask how many housekeeping staff are rostered today.", "Ask how many of them have clocked in and whether anyone in housekeeping is on leave today.",
                 "Ask how many departures there are today and how many rooms are occupied tonight.", "Ask whether that staffing looks enough, and accept a reasoned answer."],
        "figures": [_figure("hk_rostered", "count", "housekeeping staff rostered today"), _figure("hk_clocked_in", "count", "rostered housekeeping staff who clocked in today"),
                    _figure("hk_on_leave", "count", "housekeeping staff on approved leave today"), _figure("departures", "count", "departures today"),
                    _figure("occupied_tonight", "count", "rooms occupied (sold) tonight")],
        "truth": T + """ SELECT
            (SELECT count(DISTINCT r.staff_id) FROM roster r JOIN department d ON d.department_id = r.department_id WHERE r.hotel_id = %(h)s AND r.work_date = t.d AND d.department_name = 'HOUSEKEEPING') AS hk_rostered,
            (SELECT count(DISTINCT r.staff_id) FROM roster r JOIN department d ON d.department_id = r.department_id JOIN staff_attendance a ON a.roster_id = r.roster_id
                WHERE r.hotel_id = %(h)s AND r.work_date = t.d AND d.department_name = 'HOUSEKEEPING' AND a.clock_in IS NOT NULL) AS hk_clocked_in,
            (SELECT count(*) FROM leave_request l JOIN staff s ON s.staff_id = l.staff_id JOIN department d ON d.department_id = s.department_id
                WHERE l.hotel_id = %(h)s AND l.status = 'approved' AND t.d BETWEEN l.start_date AND l.end_date AND d.department_name = 'HOUSEKEEPING') AS hk_on_leave,
            (SELECT count(*) FROM booking_summary s WHERE s.hotel_id = %(h)s AND s.check_out_date = t.d AND """ + ACTIVE + """) AS departures,
            (SELECT count(*) FROM booking_room_night n WHERE n.hotel_id = %(h)s AND n.stay_date = t.d AND """ + LIVE_NIGHT + """) AS occupied_tonight FROM t""",
    },
    {
        "id": "H10", "tier": "hard", "clarify": "maybe",
        "persona": "Finance manager doing the night audit checklist.",
        "goal": ["Ask which business date is open now and which was the last one closed.", "Ask for the F&B revenue posted on the business date that was just closed.",
                 "Ask how much tax was collected on that closed business date, and how much so far on the open one.", "Ask how much is still sitting on guest folios."],
        "figures": [_figure("open_business_date", "date", "the business date open now, as YYYY-MM-DD"),
                    _figure("closed_business_date", "date", "the latest closed business date, as YYYY-MM-DD"),
                    _figure("fnb_revenue_closed_day", "money", "F&B revenue posted on the closed business date in MYR (after adjustments, without tax)"),
                    _figure("tax_closed_day", "money", "tax collected on the closed business date in MYR"),
                    _figure("tax_open_day", "money", "tax collected so far on the open business date in MYR"),
                    _figure("guest_folio_balance", "money", "total balance on guest folios in MYR")],
        "truth": T + """ SELECT t.bd_open::text AS open_business_date, t.bd_closed::text AS closed_business_date,
            (SELECT coalesce(sum(p.amount), 0) FROM finance_posting p WHERE p.hotel_id = %(h)s AND p.business_date = t.bd_closed AND p.source_module = 'fnb'
                AND p.transaction_type IN ('charge', 'adjustment', 'allowance')) AS fnb_revenue_closed_day,
            (SELECT coalesce(sum(p.amount), 0) FROM finance_posting p WHERE p.hotel_id = %(h)s AND p.business_date = t.bd_closed AND p.transaction_type = 'tax') AS tax_closed_day,
            (SELECT coalesce(sum(p.amount), 0) FROM finance_posting p WHERE p.hotel_id = %(h)s AND p.business_date = t.bd_open AND p.transaction_type = 'tax') AS tax_open_day,
            (SELECT coalesce(sum(f.balance), 0) FROM fin_folio_balance f WHERE f.hotel_id = %(h)s AND f.folio_type = 'guest') AS guest_folio_balance FROM t""",
    },
]

TIERS = ("easy", "intermediate", "hard")
