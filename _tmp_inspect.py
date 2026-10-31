import json
from dotenv import load_dotenv
load_dotenv()
from booking_system.content_store import _connection

TABLES = ["room_inventory_nightly", "daily_pricing", "room_bookings", "booking_stays", "room_stay", "room_number_assignments",
          "room_readiness_status", "payment_ledger", "payment_tracking", "channel_reservations", "channel_reservation_sync",
          "hotel_room_code_mapping", "booking_guest_counts", "guest_profile", "guest_message", "guest_preference", "guest_request",
          "guest_complaint", "maintenance_requests", "maintenance_schedules", "cleaning_status", "staff_duty_log", "room_allotments",
          "kite_hotel_setting", "kite_push_day", "kite_price_anchor", "booking_modification_log", "aiosell_buffer_room_nights", "content_document"]
cur = _connection().cursor()
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1")
print("ALL TABLES", [r[0] for r in cur.fetchall()])
cur.execute("SELECT schema_name FROM information_schema.schemata")
print("SCHEMAS", [r[0] for r in cur.fetchall()])
for t in TABLES:
    cur.execute("SELECT data_type, is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND column_name='hotel_name'", (t,))
    col = cur.fetchone()
    if col is None:
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (t,))
        print(t, "MISSING TABLE" if cur.fetchone()[0] == 0 else "NO hotel_name")
        continue
    cur.execute("SELECT count(*) FROM " + t)
    n = cur.fetchone()[0]
    if t == "content_document":
        print(t, n, col); continue
    cur.execute("SELECT hotel_name, count(*) FROM " + t + " GROUP BY 1 ORDER BY 1")
    print(t, n, col, cur.fetchall())
cur.execute("SELECT conrelid::regclass, conname, contype, pg_get_constraintdef(oid) FROM pg_constraint WHERE contype='f' AND connamespace='public'::regnamespace")
print("FKS", cur.fetchall())
for coll in ("manager_collection", "hotel_guide", "ota_link"):
    cur.execute("SELECT document_key, body FROM content_document WHERE collection_name=%s", (coll,))
    for k, b in cur.fetchall():
        b = dict(b)
        for s in ("password", "manager_password", "api_key", "token", "secret", "aiosell_password"):
            if s in b:
                b[s] = "***"
        if coll == "hotel_guide":
            b = {"hotel_name": b.get("hotel_name"), "fields_keys": list((b.get("fields") or {}).keys()), "ci": (b.get("fields") or {}).get("checkin_time"), "co": (b.get("fields") or {}).get("checkout_time")}
        print(coll, k, json.dumps(b, default=str)[:900])
cur.execute("SELECT database_name, collection_name, count(*) FROM content_document GROUP BY 1,2 ORDER BY 1,2")
print("COLLS", cur.fetchall())
