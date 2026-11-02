"""Read-only checks of LEON TEST HOTEL: python fixtures/test_hotel.py --stage verify"""

from psycopg2.extras import RealDictCursor

from booking_system.db import connect
from fixtures import test_hotel_data as data

LIVE = "('booked','checked_in','checked_out')"
CHECKS = (
    ("booking status", "SELECT booking_status, count(*) AS bookings FROM booking WHERE hotel_id = %(h)s GROUP BY 1 ORDER BY 2 DESC"),
    ("channels", "SELECT channel, count(*) AS bookings, sum(gross_amount) AS gross FROM booking WHERE hotel_id = %(h)s GROUP BY 1 ORDER BY 2 DESC"),
    ("today", """SELECT (SELECT count(*) FROM booking WHERE hotel_id = %(h)s AND booking_status = 'checked_in') AS in_house,
                        (SELECT count(*) FROM booking_summary WHERE hotel_id = %(h)s AND check_in_date = %(d)s AND booking_status IN ('confirmed','checked_in')) AS arrivals,
                        (SELECT count(*) FROM booking_summary WHERE hotel_id = %(h)s AND check_in_date = %(d)s AND booking_status = 'confirmed') AS arrivals_pending,
                        (SELECT count(*) FROM booking_summary WHERE hotel_id = %(h)s AND check_out_date = %(d)s AND booking_status IN ('checked_in','checked_out')) AS departures,
                        (SELECT count(*) FROM booking_summary WHERE hotel_id = %(h)s AND check_out_date = %(d)s AND booking_status = 'checked_in') AS departures_pending"""),
    ("occupancy by month", """WITH a AS (SELECT to_char(stay_date, 'YYYY-MM') AS month, sum(total_inventory) AS available
                                         FROM room_inventory_nightly WHERE hotel_id = %(h)s GROUP BY 1),
                                   s AS (SELECT to_char(stay_date, 'YYYY-MM') AS month, count(*) AS sold
                                         FROM booking_room_night WHERE hotel_id = %(h)s AND night_status IN """ + LIVE + """ GROUP BY 1)
                              SELECT a.month, a.available AS room_nights_available, coalesce(s.sold, 0) AS room_nights_sold,
                                     round(100.0 * coalesce(s.sold, 0) / a.available, 1) AS occupancy_pct
                              FROM a LEFT JOIN s ON s.month = a.month ORDER BY 1"""),
    ("double-booked room nights (must be 0)", """SELECT count(*) AS clashes FROM (SELECT room_id, stay_date FROM booking_room_night
                              WHERE hotel_id = %(h)s AND room_id IS NOT NULL AND night_status IN """ + LIVE + """ GROUP BY 1, 2 HAVING count(*) > 1) x"""),
    ("live nights without a room (must be 0)", "SELECT count(*) AS unassigned FROM booking_room_night WHERE hotel_id = %(h)s AND night_status IN " + LIVE + " AND room_id IS NULL"),
    ("payments", """SELECT payment_kind, payment_method, count(*) AS n, sum(amount) AS amount FROM payment WHERE hotel_id = %(h)s
                    GROUP BY 1, 2 ORDER BY 1, 2"""),
    ("checked-out balance due (must be 0 outside city ledger)", """SELECT b.channel = 'CORPORATE' AS city_ledger, count(*) AS bookings, sum(x.balance_due) AS balance_due
                    FROM booking_balance x JOIN booking b ON b.booking_id = x.booking_id
                    WHERE x.hotel_id = %(h)s AND b.booking_status = 'checked_out' GROUP BY 1"""),
    ("hr", """SELECT (SELECT count(*) FROM staff WHERE hotel_id = %(h)s) AS staff, (SELECT count(*) FROM roster WHERE hotel_id = %(h)s) AS rosters,
                     (SELECT count(*) FROM staff_attendance WHERE hotel_id = %(h)s) AS attendance,
                     (SELECT count(*) FROM staff_attendance a JOIN roster r ON r.roster_id = a.roster_id JOIN shift s ON s.shift_id = r.shift_id
                       WHERE a.hotel_id = %(h)s AND (a.clock_in AT TIME ZONE %(z)s)::time > s.starts_at + interval '5 minutes'
                         AND (a.clock_in AT TIME ZONE %(z)s)::date = r.work_date) AS late_arrivals,
                     (SELECT string_agg(status || ':' || n, ' ') FROM (SELECT status, count(*) AS n FROM leave_request WHERE hotel_id = %(h)s GROUP BY 1) l) AS leave"""),
    ("fnb", """SELECT o.outlet_name, c.status, c.settlement, count(*) AS checks, sum(c.covers) AS covers, sum(c.total) AS total
               FROM fnb_check c JOIN outlet o ON o.outlet_id = c.outlet_id WHERE c.hotel_id = %(h)s GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""),
    ("fnb room charges outside a stay (must be 0)", """SELECT count(*) AS outside FROM fnb_check c WHERE c.hotel_id = %(h)s AND c.settlement = 'room'
               AND NOT EXISTS (SELECT 1 FROM booking_room_night n WHERE n.booking_id = c.booking_id AND n.checked_in_at <= c.opened_at
                               AND (n.checked_out_at IS NULL OR n.checked_out_at > c.closed_at))"""),
    ("finance postings", """SELECT t.transaction_code, count(*) AS n, sum(p.amount) AS amount FROM finance_posting p
               JOIN transaction_code t ON t.transaction_code_id = p.transaction_code_id WHERE p.hotel_id = %(h)s GROUP BY 1 ORDER BY 1"""),
    ("finance days", """SELECT status, count(*) AS days, min(business_date) AS first, max(business_date) AS last,
               (SELECT count(*) FROM cashier_shift WHERE hotel_id = %(h)s) AS cashier_shifts,
               (SELECT count(*) FROM daily_close_summary WHERE hotel_id = %(h)s) AS close_rows,
               (SELECT count(*) FROM finance_posting WHERE hotel_id = %(h)s AND reverses_posting_id IS NOT NULL) AS reversals
               FROM business_day WHERE hotel_id = %(h)s GROUP BY 1"""),
    ("unbalanced checked-out guest folios (must be 0)", """SELECT count(*) AS unbalanced FROM fin_folio_balance f JOIN booking b ON b.booking_id = f.folio_ref
               WHERE f.hotel_id = %(h)s AND f.folio_type = 'guest' AND b.booking_status IN ('checked_out','cancelled','no_show') AND f.balance <> 0"""),
    ("city ledger aging", """SELECT account_name, not_due, overdue_1_30, overdue_31_60, overdue_61_90, overdue_90_plus, uninvoiced, balance
               FROM fin_ar_aging WHERE hotel_id = %(h)s ORDER BY 1"""),
)


def _tables(cur):
    cur.execute("""SELECT c.table_name FROM information_schema.columns c JOIN information_schema.tables t
                   ON t.table_name = c.table_name AND t.table_schema = c.table_schema AND t.table_type = 'BASE TABLE'
                   WHERE c.table_schema = current_schema() AND c.column_name = 'hotel_id' ORDER BY 1""")
    return [r["table_name"] for r in cur.fetchall()]


def report():
    conn = connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("BEGIN READ ONLY")
        cur.execute("SELECT hotel_id, timezone, (now() AT TIME ZONE timezone)::date AS today FROM hotel WHERE hotel_name = %s", (data.HOTEL_NAME,))
        hotel = cur.fetchone()
        if hotel is None:
            print(data.HOTEL_NAME, "is not seeded")
            return
        args = {"h": hotel["hotel_id"], "d": hotel["today"], "z": hotel["timezone"]}
        print(data.HOTEL_NAME, "today", hotel["today"])
        counts = []
        for table in _tables(cur):
            cur.execute('SELECT count(*) AS n FROM "' + table + '" WHERE hotel_id = %(h)s', args)
            counts.append((table, cur.fetchone()["n"]))
        print("\n== rows per table (hotel_id scoped)")
        print("  " + ", ".join(t + "=" + str(n) for t, n in counts if n))
        for title, sql in CHECKS:
            print("\n== " + title)
            cur.execute("SAVEPOINT s")
            try:
                cur.execute(sql, args)
                for row in cur.fetchall():
                    print("  " + ", ".join(k + "=" + str(v) for k, v in row.items()))
                cur.execute("RELEASE SAVEPOINT s")
            except Exception as error:
                cur.execute("ROLLBACK TO SAVEPOINT s")
                print("  not available:", str(error).splitlines()[0])
        cur.execute("ROLLBACK")
    finally:
        conn.close()
