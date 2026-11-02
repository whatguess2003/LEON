"""LEON TEST HOTEL: a repeatable test hotel with 50 rooms, ~700 guests, 1000 bookings over 3 months centred on today, 10 staff logins,
and HR, F&B and Finance data. Separate from the SANDBOX fixture. Data comes from fixtures/test_hotel_data.py (deterministic).

    python fixtures/test_hotel.py --stage core|hr|fnb|finance|all [--reset]
    python fixtures/test_hotel.py --stage verify

Each stage first checks that every table and column it writes exists (and stops with the missing list otherwise), then reads whether
this hotel already has that stage's rows: without --reset it refuses, with --reset it deletes only this hotel's rows of that stage
(FK-safe order) and seeds again. A stage whose later stages hold data refuses to reset alone; use --stage all --reset.
The test password of the 10 logins is test_hotel_data.PASSWORD."""

import argparse
import os
import random
import sys
from datetime import datetime, timedelta
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from psycopg2.extras import Json, RealDictCursor, execute_values

from booking_system import access
from booking_system.db import connect
from fixtures import test_hotel_data as data

STAGES = ("core", "hr", "fnb", "finance")
# role -> (daily investigation cap, module -> level): the one matrix every hotel gets (booking_system.access.ROLES)
ROLE_CAPS = {role: cap for role, (cap, _) in access.ROLES.items()}
ROLE_MATRIX = {role: levels for role, (_, levels) in access.ROLES.items()}
NIGHT_STATUS = {"confirmed": "booked", "checked_in": "checked_in", "checked_out": "checked_out", "cancelled": "cancelled", "no_show": "no_show"}
RATE_PLAN = {"DIRECT": "BAR", "WALK_IN": "BAR", "CORPORATE": "CORPORATE"}
PAYMENT_REFERENCE = {"deposit": "DEPOSIT", "guarantee": "FIRST NIGHT AT CHECK-IN", "settlement": "SETTLEMENT", "refund": "CANCELLATION REFUND",
                     "no_show_fee": "NO SHOW CHARGE"}
FNB_DEPARTMENT_OF_CODE = {code: department for department, code in data.FNB_CODE.items()}


class NotReady(Exception):
    pass


# ---------- plumbing ----------

def _columns(cur):
    cur.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()")
    found = {}
    for row in cur.fetchall():
        found.setdefault(row["table_name"], set()).add(row["column_name"])
    return found


def _require(cur, stage, shapes):
    """shapes: [(table, columns)]. Stops the stage when anything it writes is missing."""
    have = _columns(cur)
    missing = [table + "(" + ", ".join(sorted(set(cols) - have.get(table, set()))) + ")" for table, cols in shapes if not set(cols) <= have.get(table, set())]
    if missing:
        raise NotReady(stage + " stage pending; missing " + "; ".join(missing))


def _insert(cur, table, cols, rows):
    if rows:
        execute_values(cur, "INSERT INTO " + table + " (" + ", ".join(cols) + ") VALUES %s", [tuple(r[c] for c in cols) for r in rows], page_size=1000)
    return len(rows)


def _write(cur, plan):
    for table, cols, rows in plan:
        print("  ", table, _insert(cur, table, cols, rows), flush=True)


def _hotel_id(cur):
    cur.execute("SELECT hotel_id FROM hotel WHERE hotel_name = %s", (data.HOTEL_NAME,))
    row = cur.fetchone()
    return row["hotel_id"] if row else None


def _count(cur, sql, hotel_id):
    cur.execute(sql, (hotel_id,))
    return list(cur.fetchone().values())[0]


def _now():
    return datetime.now(data.tz())


def _users(cur, hotel_id):
    """STAFF index -> user_id, matched on the fixture phones."""
    cur.execute("""SELECT i.identity_value, u.user_id FROM hotel_user u JOIN user_identity i ON i.user_id = u.user_id AND i.identity_type = 'phone'
                   WHERE u.hotel_id = %s""", (hotel_id,))
    by_phone = {r["identity_value"]: r["user_id"] for r in cur.fetchall()}
    missing = [p for p in data.STAFF_PHONES if p not in by_phone]
    if missing:
        raise NotReady("core stage has not created the staff logins (" + str(len(missing)) + " missing)")
    return [by_phone[p] for p in data.STAFF_PHONES]


def _by_role(user_ids):
    roles = {}
    for i, (name, role, *_) in enumerate(data.STAFF):
        roles.setdefault(role, []).append(user_ids[i])
    return roles


# ---------- core ----------

CORE_SHAPES = (
    ("hotel", ("hotel_id", "hotel_name", "currency", "timezone")),
    ("user_account", ("user_id", "user_name", "status", "last_hotel_id", "created_at")),
    ("user_identity", ("identity_id", "user_id", "identity_type", "identity_value")),
    ("access_role", ("role_id", "hotel_id", "role_name", "daily_investigation_cap")),
    ("role_module", ("role_id", "module_name", "access_level")),
    ("hotel_user", ("hotel_id", "user_id", "role_id", "job_title", "status", "invited_by_user_id", "invited_at", "activated_at")),
    ("hotel_setting", ("hotel_setting_id", "hotel_id", "setting_key", "value", "effective_from", "set_by_user_id", "source", "note")),
    ("room_type", ("room_type_id", "hotel_id", "room_type_name", "status")),
    ("room", ("room_id", "hotel_id", "room_type_id", "room_number", "status")),
    ("room_inventory_nightly", ("hotel_id", "room_type_id", "stay_date", "total_inventory", "blocked_quantity", "updated_by_user_id")),
    ("daily_pricing", ("hotel_id", "room_type_id", "stay_date", "base_price", "saved_price", "updated_by_user_id")),
    ("guest", ("guest_id", "hotel_id", "guest_name", "nationality", "vip", "status", "created_at", "created_by_user_id")),
    ("guest_identity", ("identity_id", "hotel_id", "guest_id", "identity_type", "identity_value")),
    ("booking", ("booking_id", "hotel_id", "guest_id", "booking_status", "channel", "channel_booking_ref", "rate_plan", "adults", "children", "currency",
                 "gross_amount", "commission_amount", "commission_rate", "booked_at", "created_by_user_id", "cancelled_at", "no_show_at")),
    ("booking_room_night", ("booking_night_id", "hotel_id", "booking_id", "stay_date", "room_seq", "room_type_id", "room_id", "night_status", "sell_rate",
                            "currency", "rate_source", "assigned_at", "assigned_by_user_id", "checked_in_at", "checked_out_at")),
    ("payment", ("payment_id", "hotel_id", "booking_id", "payment_kind", "amount", "currency", "payment_method", "payment_status", "reference", "paid_at",
                 "recorded_by_user_id")),
    ("room_readiness_status", ("room_id", "hotel_id", "readiness_status", "changed_at", "changed_by_user_id", "clean_since", "auto_dirty_days")),
    ("cleaning_task", ("cleaning_task_id", "hotel_id", "room_id", "task_status", "task_type", "priority", "created_at", "created_by_user_id", "assigned_at",
                       "started_at", "completed_at", "inspected_at", "inspected_by_user_id")),
    ("maintenance_schedule", ("schedule_id", "hotel_id", "room_id", "area", "issue", "urgency", "interval_days", "start_date", "last_service_date",
                              "next_due_date", "auto_out_of_order", "active")),
    ("maintenance_request", ("request_id", "hotel_id", "room_id", "area", "issue", "urgency", "request_status", "schedule_id", "due_date", "reported_at",
                             "reported_by_user_id", "assigned_at", "started_at", "completed_at", "actual_cost")),
)


# The test hotel's answers to the operating-settings catalog, as its manager would give them at onboarding.
SETTINGS = {
    "front_desk.checkin_time": data.CHECKIN_TIME.strftime("%H:%M"), "front_desk.checkout_time": data.CHECKOUT_TIME.strftime("%H:%M"),
    "front_desk.night_audit_time": data.NIGHT_AUDIT_TIME.strftime("%H:%M"), "front_desk.checkin_room_readiness": "clean_or_inspected",
    "finance.enabled": True, "finance.tax_pricing": "exclusive", "finance.tax_calculation": "simple", "finance.cashier_variance_tolerance": "5.00",
    "fnb.enabled": True, "fnb.closed_check_correction": "folio_adjustment",
    "hr.enabled": True, "hr.worked_hours_breaks": "deduct_unpaid_breaks", "hr.leave_day_counting": "calendar_days",
}


def _core_plan(cur, anchor, now):
    rng = random.Random(data.SEED + 9)
    hotel_id = data.new_id(rng, "htl")
    shapes = dict(CORE_SHAPES)
    cur.execute("SELECT module_name FROM module")
    modules = sorted(r["module_name"] for r in cur.fetchall())
    unknown = [m for m in modules if m not in ROLE_MATRIX["FRONT_DESK"]]
    if unknown or not modules:
        raise NotReady("core stage: role matrix has no level for modules " + ", ".join(unknown or ["(module table empty)"]))
    created = data.at(anchor - timedelta(days=data.PAST_DAYS + 30), datetime.min.time())
    users, identities, roles, role_modules, hotel_users = [], [], {}, [], []
    for role in ROLE_MATRIX:
        roles[role] = {"role_id": data.new_id(rng, "rol"), "hotel_id": hotel_id, "role_name": role, "daily_investigation_cap": ROLE_CAPS[role]}
        role_modules.extend({"role_id": roles[role]["role_id"], "module_name": m, "access_level": ROLE_MATRIX[role][m]} for m in modules)
    for i, (name, role, title, *_) in enumerate(data.STAFF):
        user_id = data.new_id(rng, "usr")
        users.append({"user_id": user_id, "user_name": name, "status": "active", "last_hotel_id": hotel_id, "created_at": created})
        username = data.STAFF_EMAILS[i].split("@")[0]
        identities.extend({"identity_id": data.new_id(rng, "uid"), "user_id": user_id, "identity_type": kind, "identity_value": value}
                          for kind, value in (("phone", data.STAFF_PHONES[i]), ("email", data.STAFF_EMAILS[i]), ("username", username)))
        hotel_users.append({"hotel_id": hotel_id, "user_id": user_id, "role_id": roles[role]["role_id"], "job_title": title, "status": "active",
                            "invited_by_user_id": users[0]["user_id"] if i else None, "invited_at": created, "activated_at": created})
    user_of = [u["user_id"] for u in users]
    manager, desk, supervisor = user_of[0], user_of[1:4], user_of[4]
    core = data.core(anchor, now)
    start, end = data.window(anchor)
    days = [start + timedelta(days=k) for k in range((end - start).days)]
    types = [{"room_type_id": t["room_type_id"], "hotel_id": hotel_id, "room_type_name": t["room_type"], "status": "active"} for t in core["room_types"]]
    rooms = [{"room_id": r["room_id"], "hotel_id": hotel_id, "room_type_id": r["room_type_id"], "room_number": r["room_number"], "status": "active"}
             for r in core["rooms"]]
    inventory = [{"hotel_id": hotel_id, "room_type_id": t["room_type_id"], "stay_date": d, "total_inventory": t["room_count"], "blocked_quantity": 0,
                  "updated_by_user_id": manager} for t in core["room_types"] for d in days]
    pricing = []
    for t in core["room_types"]:
        for d in days:
            price = data.nightly_rate(t["base_rate"], d, Decimal("1.00"), 1)
            pricing.append({"hotel_id": hotel_id, "room_type_id": t["room_type_id"], "stay_date": d, "base_price": price, "saved_price": price,
                            "updated_by_user_id": manager})
    first_seen, stays = {}, {}
    for b in core["bookings"]:
        first_seen.setdefault(b["guest_id"], b["booked_at"])
        stays[b["guest_id"]] = stays.get(b["guest_id"], 0) + 1
    guests, guest_ids = [], []
    for g in core["guests"]:
        guests.append({"guest_id": g["guest_id"], "hotel_id": hotel_id, "guest_name": g["guest_name"], "nationality": g["nationality"],
                       "vip": stays[g["guest_id"]] >= 4, "status": "active", "created_at": first_seen[g["guest_id"]],
                       "created_by_user_id": desk[int(g["guest_id"][-2:], 16) % 3]})
        guest_ids.extend({"identity_id": data.new_id(rng, "gid"), "hotel_id": hotel_id, "guest_id": g["guest_id"], "identity_type": kind,
                          "identity_value": value} for kind, value in (("phone", g["phone"]), ("email", g["email"])))
    bookings, nights, payments = [], [], []
    for b in core["bookings"]:
        desk_user = desk[int(b["booking_id"][-2:], 16) % 3]
        ota = b["channel"] not in RATE_PLAN
        bookings.append({"booking_id": b["booking_id"], "hotel_id": hotel_id, "guest_id": b["guest_id"], "booking_status": b["status"],
                         "channel": b["channel"], "channel_booking_ref": b["reference"], "rate_plan": RATE_PLAN.get(b["channel"], "OTA STANDARD"),
                         "adults": b["adults"], "children": b["children"], "currency": data.CURRENCY, "gross_amount": b["total"],
                         "commission_amount": b["commission_amount"], "commission_rate": Decimal(b["commission_percent"]), "booked_at": b["booked_at"],
                         "created_by_user_id": None if ota else desk_user, "cancelled_at": b["cancelled_at"], "no_show_at": b["no_show_at"]})
        assigned = min(max(b["booked_at"], data.at(b["arrival"] - timedelta(days=1), datetime.min.time().replace(hour=10))), now) if b["room_id"] else None
        for n in b["nights_detail"]:
            nights.append({"booking_night_id": n["night_id"], "hotel_id": hotel_id, "booking_id": b["booking_id"], "stay_date": n["stay_date"], "room_seq": 1,
                           "room_type_id": b["room_type_id"], "room_id": b["room_id"], "night_status": NIGHT_STATUS[b["status"]], "sell_rate": n["rate"],
                           "currency": data.CURRENCY, "rate_source": "channel" if ota else "desk", "assigned_at": assigned,
                           "assigned_by_user_id": desk_user if assigned else None, "checked_in_at": b["checked_in_at"], "checked_out_at": b["checked_out_at"]})
    for p in core["payments"]:
        payments.append({"payment_id": p["payment_id"], "hotel_id": hotel_id, "booking_id": p["booking_id"],
                         "payment_kind": "refund" if p["kind"] == "refund" else "payment", "amount": p["amount"], "currency": data.CURRENCY,
                         "payment_method": p["method"].upper(), "payment_status": "received", "reference": PAYMENT_REFERENCE[p["kind"]],
                         "paid_at": p["paid_at"], "recorded_by_user_id": user_of[p["staff"]] if p["staff"] else None})
    hk = data.housekeeping(core["bookings"], core["rooms"], anchor, now)
    readiness = [dict(r, hotel_id=hotel_id, changed_by_user_id=supervisor, auto_dirty_days=3) for r in hk["readiness"]]
    cleanings = [dict(c, hotel_id=hotel_id, created_by_user_id=supervisor, inspected_by_user_id=supervisor if c["inspected_at"] else None)
                 for c in hk["cleanings"]]
    schedules = [dict(s, hotel_id=hotel_id, room_id=None, auto_out_of_order=False, active=True) for s in hk["schedules"]]
    requests = [dict(m, hotel_id=hotel_id, reported_by_user_id=supervisor if m["room_id"] else manager) for m in hk["maintenance"]]
    hotel = [{"hotel_id": hotel_id, "hotel_name": data.HOTEL_NAME, "currency": data.CURRENCY, "timezone": data.TIMEZONE}]
    settings = [{"hotel_setting_id": data.new_id(rng, "hst"), "hotel_id": hotel_id, "setting_key": key, "value": Json(value),
                 "effective_from": start, "set_by_user_id": manager, "source": "fixture", "note": ""} for key, value in SETTINGS.items()]
    rows = {"hotel": hotel, "user_account": users, "user_identity": identities, "access_role": list(roles.values()), "role_module": role_modules,
            "hotel_user": hotel_users, "hotel_setting": settings, "room_type": types, "room": rooms, "room_inventory_nightly": inventory, "daily_pricing": pricing,
            "guest": guests, "guest_identity": guest_ids, "booking": bookings, "booking_room_night": nights, "payment": payments,
            "room_readiness_status": readiness, "cleaning_task": cleanings, "maintenance_schedule": schedules, "maintenance_request": requests}
    return [(table, shapes[table], rows[table]) for table, _ in CORE_SHAPES]


def seed_core(cur, anchor, now):
    _require(cur, "core", CORE_SHAPES)
    plan = _core_plan(cur, anchor, now)
    _write(cur, plan)
    _profiles(cur, dict((table, rows) for table, _, rows in plan)["user_account"])


def _profiles(cur, users):
    """Each login's user_profile document (password hash, language) on this transaction, as user_manager.signup writes it."""
    from passlib.hash import bcrypt
    from booking_system.user_manager import profile_col
    hashed = bcrypt.hash(data.PASSWORD)
    for user in users:
        profile_col.write_in(cur, *profile_col.staged_insert({"user_id": user["user_id"], "password_hash": hashed,
                                                             "primary_language": "English", "nationality": "Malaysian"}))
    print("   user_profile", len(users))


def _core_users(cur, hotel_id):
    cur.execute("SELECT user_id FROM hotel_user WHERE hotel_id = %s", (hotel_id,))
    users = [r["user_id"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT user_id FROM hotel_user WHERE user_id = ANY(%s) AND hotel_id <> %s", (users, hotel_id))
    shared = [r["user_id"] for r in cur.fetchall()]
    if shared:
        raise NotReady("core reset: " + str(len(shared)) + " fixture users also belong to another hotel; remove them there first")
    return users


def reset_core(cur, hotel_id):
    users = _core_users(cur, hotel_id)
    reset_fnb(cur, hotel_id)
    for sql in (
        "DELETE FROM department WHERE hotel_id = %(h)s",
        "DELETE FROM maintenance_request WHERE hotel_id = %(h)s",
        "DELETE FROM maintenance_schedule WHERE hotel_id = %(h)s",
        "DELETE FROM cleaning_task WHERE hotel_id = %(h)s",
        "DELETE FROM room_readiness_status WHERE hotel_id = %(h)s",
        "DELETE FROM payment WHERE hotel_id = %(h)s",
        "DELETE FROM booking_room_night WHERE hotel_id = %(h)s",
        "DELETE FROM booking WHERE hotel_id = %(h)s",
        "DELETE FROM guest_identity WHERE hotel_id = %(h)s",
        "DELETE FROM guest WHERE hotel_id = %(h)s",
        "DELETE FROM daily_pricing WHERE hotel_id = %(h)s",
        "DELETE FROM room_inventory_nightly WHERE hotel_id = %(h)s",
        "DELETE FROM room WHERE hotel_id = %(h)s",
        "DELETE FROM room_type WHERE hotel_id = %(h)s",
        "DELETE FROM hotel_user_module WHERE hotel_id = %(h)s",
        "DELETE FROM hotel_user WHERE hotel_id = %(h)s",
        "DELETE FROM role_module WHERE role_id IN (SELECT role_id FROM access_role WHERE hotel_id = %(h)s)",
        "DELETE FROM access_role WHERE hotel_id = %(h)s",
        "DELETE FROM content_document WHERE collection_name = 'user_profile' AND body->>'user_id' = ANY(%(u)s)",
        "DELETE FROM user_identity WHERE user_id = ANY(%(u)s)",
        "DELETE FROM user_account WHERE user_id = ANY(%(u)s)",
        "DELETE FROM hotel WHERE hotel_id = %(h)s",
    ):
        cur.execute(sql, {"h": hotel_id, "u": users})


# ---------- HR ----------

HR_SHAPES = (
    ("department", ("department_id", "hotel_id", "department_name", "department_type")),
    ("staff_position", ("position_id", "hotel_id", "department_id", "position_name")),
    ("staff", ("staff_id", "hotel_id", "staff_name", "department_id", "position_id", "user_id", "employment_type", "status", "hired_on")),
    ("staff_identity", ("staff_identity_id", "hotel_id", "staff_id", "identity_type", "identity_value")),
    ("shift", ("shift_id", "hotel_id", "shift_name", "starts_at", "ends_at", "break_minutes")),
    ("roster", ("roster_id", "hotel_id", "staff_id", "shift_id", "department_id", "work_date", "created_by_user_id")),
    ("staff_attendance", ("attendance_id", "hotel_id", "staff_id", "roster_id", "clock_in", "clock_out", "clock_in_by_user_id", "clock_out_by_user_id")),
    ("leave_type", ("leave_type_id", "hotel_id", "leave_type_name", "paid")),
    ("leave_request", ("leave_request_id", "hotel_id", "staff_id", "leave_type_id", "start_date", "end_date", "status", "requested_by_user_id",
                       "requested_at", "decided_by_user_id", "decided_at", "cancelled_by_user_id", "cancelled_at")),
    ("cleaning_task", ("assigned_staff_id",)),
    ("maintenance_request", ("assigned_staff_id",)),
)


def _departments(cur, hotel_id, stage):
    """Department is shared by HR and Finance: reuse this hotel's rows by name, insert the missing ones."""
    cur.execute("SELECT department_name, department_id FROM department WHERE hotel_id = %s", (hotel_id,))
    found = {r["department_name"]: r["department_id"] for r in cur.fetchall()}
    missing = [dict(d, hotel_id=hotel_id) for d in data.departments() if d["department_name"] not in found]
    print("  ", "department", _insert(cur, "department", ("department_id", "hotel_id", "department_name", "department_type"), missing), "(" + stage + ")")
    found.update({d["department_name"]: d["department_id"] for d in missing})
    return found


def seed_hr(cur, hotel_id, anchor, now):
    _require(cur, "hr", HR_SHAPES)
    users = _users(cur, hotel_id)
    dep = _departments(cur, hotel_id, "hr")
    hr = data.hr(anchor, now, dep, users)
    rng = random.Random(data.SEED + 6)
    shapes = dict(HR_SHAPES)
    staff_of = [s["staff_id"] for s in hr["staff"]]
    identities = [{"staff_identity_id": data.new_id(rng, "sid"), "hotel_id": hotel_id, "staff_id": s["staff_id"], "identity_type": kind,
                   "identity_value": s[kind]} for s in hr["staff"] for kind in ("phone", "email")]
    rosters = [dict(r, hotel_id=hotel_id, staff_id=staff_of[r["staff"]], created_by_user_id=users[0]) for r in hr["rosters"]]
    attendance = [dict(a, hotel_id=hotel_id, staff_id=staff_of[a["staff"]], clock_in_by_user_id=users[a["staff"]],
                       clock_out_by_user_id=users[a["staff"]] if a["clock_out"] else None) for a in hr["attendance"]]
    leave = [dict(l, hotel_id=hotel_id, staff_id=staff_of[l["staff"]], requested_by_user_id=users[l["staff"]],
                  decided_by_user_id=users[0] if l["decided_at"] else None, cancelled_by_user_id=users[l["staff"]] if l["cancelled_at"] else None)
             for l in hr["leave"]]
    _write(cur, [
        ("staff_position", shapes["staff_position"], [dict(p, hotel_id=hotel_id) for p in hr["positions"]]),
        ("staff", shapes["staff"], [dict(s, hotel_id=hotel_id) for s in hr["staff"]]),
        ("staff_identity", shapes["staff_identity"], identities),
        ("shift", shapes["shift"], [dict(s, hotel_id=hotel_id) for s in hr["shifts"]]),
        ("roster", shapes["roster"], rosters),
        ("staff_attendance", shapes["staff_attendance"], attendance),
        ("leave_type", shapes["leave_type"], [dict(t, hotel_id=hotel_id) for t in hr["leave_types"]]),
        ("leave_request", shapes["leave_request"], leave),
    ])
    attendants, technician = staff_of[4:7], staff_of[7]
    cur.execute("SELECT cleaning_task_id FROM cleaning_task WHERE hotel_id = %s AND assigned_at IS NOT NULL ORDER BY cleaning_task_id", (hotel_id,))
    tasks = [(attendants[int(r["cleaning_task_id"][-2:], 16) % 3], r["cleaning_task_id"]) for r in cur.fetchall()]
    execute_values(cur, "UPDATE cleaning_task c SET assigned_staff_id = v.s FROM (VALUES %s) AS v(s, id) WHERE c.cleaning_task_id = v.id", tasks)
    cur.execute("UPDATE maintenance_request SET assigned_staff_id = %s WHERE hotel_id = %s AND assigned_at IS NOT NULL", (technician, hotel_id))
    print("   cleaning_task / maintenance_request assigned", len(tasks), cur.rowcount)


def _unused_departments(cur, hotel_id):
    cur.execute("""DELETE FROM department d WHERE d.hotel_id = %s
                   AND NOT EXISTS (SELECT 1 FROM staff_position x WHERE x.department_id = d.department_id)
                   AND NOT EXISTS (SELECT 1 FROM staff x WHERE x.department_id = d.department_id)
                   AND NOT EXISTS (SELECT 1 FROM transaction_code x WHERE x.department_id = d.department_id)""", (hotel_id,))


def reset_hr(cur, hotel_id):
    for sql in (
        "UPDATE cleaning_task SET assigned_staff_id = NULL WHERE hotel_id = %(h)s",
        "UPDATE maintenance_request SET assigned_staff_id = NULL WHERE hotel_id = %(h)s",
        "DELETE FROM leave_request WHERE hotel_id = %(h)s",
        "DELETE FROM leave_type WHERE hotel_id = %(h)s",
        "DELETE FROM staff_attendance WHERE hotel_id = %(h)s",
        "DELETE FROM roster WHERE hotel_id = %(h)s",
        "DELETE FROM shift WHERE hotel_id = %(h)s",
        "DELETE FROM staff_identity WHERE hotel_id = %(h)s",
        "DELETE FROM staff WHERE hotel_id = %(h)s",
        "DELETE FROM staff_position WHERE hotel_id = %(h)s",
    ):
        cur.execute(sql, {"h": hotel_id})
    _unused_departments(cur, hotel_id)


# ---------- F&B ----------

SETUP_SHAPES = (
    ("department", ("department_id", "hotel_id", "department_name", "department_type")),
    ("transaction_code", ("transaction_code_id", "hotel_id", "transaction_code", "transaction_type", "department_id", "payment_method", "is_taxable",
                          "gl_export_code", "status")),
    ("tax_code", ("tax_code_id", "hotel_id", "tax_code", "tax_basis", "tax_transaction_code_id", "tax_transaction_type", "status")),
    ("tax_rule", ("tax_rule_id", "tax_code_id", "tax_basis", "effective_from", "rate_percent")),
    ("transaction_code_tax", ("transaction_code_tax_id", "transaction_code_id", "tax_code_id")),
    ("ar_account", ("ar_account_id", "hotel_id", "account_name", "account_type", "currency", "credit_limit", "payment_terms_days", "status")),
)
FNB_SHAPES = SETUP_SHAPES + (
    ("outlet", ("outlet_id", "hotel_id", "outlet_name", "outlet_type", "status")),
    ("fnb_menu_item", ("item_id", "hotel_id", "outlet_id", "item_name", "price", "transaction_code_id", "status")),
    ("fnb_check", ("check_id", "hotel_id", "outlet_id", "business_date", "check_number", "table_number", "covers", "status", "settlement", "booking_id",
                   "room_id", "currency", "subtotal", "discount_total", "tax_total", "total", "void_reason", "opened_at", "opened_by_user_id", "closed_at",
                   "closed_by_user_id")),
    ("fnb_check_line", ("line_id", "check_id", "line_seq", "item_id", "transaction_code_id", "quantity", "unit_price", "discount_amount", "discount_reason",
                        "voided", "void_reason", "voided_by_user_id", "voided_at", "added_by_user_id", "added_at")),
)


def _finance_setup(cur, hotel_id, anchor):
    """Finance's codes, taxes and city-ledger accounts (F&B items and lines point at its charge codes). Written once; returns the setup
    with this hotel's ids read back by code text, so a later stage on another day reuses the rows already there."""
    setup = data.finance_setup(data.window(anchor)[0])
    cur.execute("SELECT transaction_code, transaction_code_id FROM transaction_code WHERE hotel_id = %s", (hotel_id,))
    have = {r["transaction_code"]: r["transaction_code_id"] for r in cur.fetchall()}
    if not have:
        dep = _departments(cur, hotel_id, "finance setup")
        shapes = dict(SETUP_SHAPES)
        codes = [dict(c, hotel_id=hotel_id, department_id=dep[c["department"]], status="active") for c in setup["codes"].values()]
        taxes = [dict(t, hotel_id=hotel_id, tax_basis="percent", tax_transaction_type="tax", status="active") for t in setup["taxes"].values()]
        rules = [{"tax_rule_id": t["tax_rule_id"], "tax_code_id": t["tax_code_id"], "tax_basis": "percent", "effective_from": t["effective_from"],
                  "rate_percent": t["rate_percent"]} for t in setup["taxes"].values()]
        _write(cur, [
            ("transaction_code", shapes["transaction_code"], codes),
            ("tax_code", shapes["tax_code"], taxes),
            ("tax_rule", shapes["tax_rule"], rules),
            ("transaction_code_tax", shapes["transaction_code_tax"], setup["links"]),
            ("ar_account", shapes["ar_account"], [dict(a, hotel_id=hotel_id, currency=data.CURRENCY, status="active") for a in setup["accounts"]]),
        ])
        return setup
    for code in setup["codes"].values():
        code["transaction_code_id"] = have[code["transaction_code"]]
    cur.execute("""SELECT t.tax_code, t.tax_code_id, r.tax_rule_id FROM tax_code t JOIN tax_rule r ON r.tax_code_id = t.tax_code_id
                   WHERE t.hotel_id = %s""", (hotel_id,))
    for r in cur.fetchall():
        setup["taxes"][r["tax_code"]].update(tax_code_id=r["tax_code_id"], tax_rule_id=r["tax_rule_id"])
    posting_code = {tax: tax_txn for tax, tax_txn, *_ in data.FIN_TAXES}
    for tax in setup["taxes"].values():
        tax["tax_transaction_code_id"] = setup["codes"][posting_code[tax["tax_code"]]]["transaction_code_id"]
    cur.execute("SELECT account_name, ar_account_id FROM ar_account WHERE hotel_id = %s", (hotel_id,))
    accounts = {r["account_name"]: r["ar_account_id"] for r in cur.fetchall()}
    for account in setup["accounts"]:
        account["ar_account_id"] = accounts[account["account_name"]]
    return setup


def _stays(cur, hotel_id):
    """Bookings with their nights as the database holds them, in a stable order."""
    cur.execute("""SELECT b.booking_id, b.channel, b.booking_status, b.cancelled_at, n.booking_night_id, n.stay_date, n.sell_rate,
                          n.room_id, n.checked_in_at, n.checked_out_at
                   FROM booking b JOIN booking_room_night n ON n.booking_id = b.booking_id
                   WHERE b.hotel_id = %s ORDER BY b.booked_at, b.booking_id, n.stay_date""", (hotel_id,))
    stays = {}
    for r in cur.fetchall():
        b = stays.setdefault(r["booking_id"], {"booking_id": r["booking_id"], "channel": r["channel"], "status": r["booking_status"],
                                               "cancelled_at": r["cancelled_at"], "room_id": r["room_id"], "checked_in_at": r["checked_in_at"], "checked_out_at": r["checked_out_at"],
                                               "nights_detail": []})
        b["nights_detail"].append({"night_id": r["booking_night_id"], "stay_date": r["stay_date"], "rate": r["sell_rate"]})
    for b in stays.values():
        b["arrival"] = b["nights_detail"][0]["stay_date"]
        b["departure"] = b["nights_detail"][-1]["stay_date"] + timedelta(days=1)
    return list(stays.values())


def seed_fnb(cur, hotel_id, anchor, now):
    _require(cur, "fnb", FNB_SHAPES)
    users = _users(cur, hotel_id)
    cashier, manager = _by_role(users)["FNB_CASHIER"][0], users[0]
    stays = _stays(cur, hotel_id)
    start = min(n["stay_date"] for b in stays for n in b["nights_detail"])
    f = data.fnb(max(start, data.window(anchor)[0]), anchor, now, stays)
    code_of = {d: c["transaction_code_id"] for d, c in ((d, _finance_setup(cur, hotel_id, anchor)["codes"][code]) for d, code in data.FNB_CODE.items())}
    room_of = {b["booking_id"]: b["room_id"] for b in stays}
    checks, lines = [], []
    for c in f["checks"]:
        closed = c["status"] == "closed"
        checks.append({"check_id": c["check_id"], "hotel_id": hotel_id, "outlet_id": c["outlet_id"], "business_date": c["business_date"],
                       "check_number": c["check_number"], "table_number": c["table_number"], "covers": c["covers"], "status": c["status"],
                       "settlement": c["settlement"], "booking_id": c["booking_id"], "room_id": room_of.get(c["booking_id"]), "currency": data.CURRENCY,
                       "subtotal": c["subtotal"] if closed else None, "discount_total": c["discount_total"] if closed else None,
                       "tax_total": c["tax_total"] if closed else None, "total": c["total"] if closed else None, "void_reason": c["void_reason"],
                       "opened_at": c["opened_at"], "opened_by_user_id": cashier, "closed_at": c["closed_at"],
                       "closed_by_user_id": (manager if c["status"] == "void" else cashier) if c["closed_at"] else None})
        for l in c["lines"]:
            lines.append({"line_id": l["line_id"], "check_id": c["check_id"], "line_seq": l["line_seq"], "item_id": l["item"]["item_id"],
                          "transaction_code_id": code_of[l["item"]["department"]], "quantity": l["quantity"], "unit_price": l["item"]["price"],
                          "discount_amount": l["discount"], "discount_reason": l["discount_reason"], "voided": l["voided"], "void_reason": l["void_reason"],
                          "voided_by_user_id": manager if l["voided"] else None, "voided_at": l["added_at"] + timedelta(minutes=3) if l["voided"] else None,
                          "added_by_user_id": cashier, "added_at": l["added_at"]})
    shapes = dict(FNB_SHAPES)
    _write(cur, [
        ("outlet", shapes["outlet"], [dict(o, hotel_id=hotel_id, status="active") for o in f["outlets"]]),
        ("fnb_menu_item", shapes["fnb_menu_item"], [dict(i, hotel_id=hotel_id, transaction_code_id=code_of[i["department"]], status="active")
                                                    for i in f["items"]]),
        ("fnb_check", shapes["fnb_check"], checks),
        ("fnb_check_line", shapes["fnb_check_line"], lines),
    ])


def reset_fnb(cur, hotel_id):
    if _count(cur, "SELECT count(*) FROM finance_posting WHERE hotel_id = %s AND source_module = 'fnb'", hotel_id):
        raise NotReady("fnb reset: finance postings reference these checks; run --stage all --reset (or finance --reset first)")
    """F&B rows, then the Finance setup they point at (the fnb stage writes it; finance --reset keeps it)."""
    cur.execute("SET LOCAL session_replication_role = replica")
    for sql in (
        "DELETE FROM fnb_check_line WHERE check_id IN (SELECT check_id FROM fnb_check WHERE hotel_id = %(h)s)",
        "DELETE FROM fnb_check WHERE hotel_id = %(h)s",
        "DELETE FROM fnb_menu_item WHERE hotel_id = %(h)s",
        "DELETE FROM outlet WHERE hotel_id = %(h)s",
        "DELETE FROM ar_account WHERE hotel_id = %(h)s",
        "DELETE FROM transaction_code_tax WHERE transaction_code_id IN (SELECT transaction_code_id FROM transaction_code WHERE hotel_id = %(h)s)",
        "DELETE FROM tax_rule WHERE tax_code_id IN (SELECT tax_code_id FROM tax_code WHERE hotel_id = %(h)s)",
        "DELETE FROM tax_code WHERE hotel_id = %(h)s",
        "DELETE FROM transaction_code WHERE hotel_id = %(h)s",
    ):
        cur.execute(sql, {"h": hotel_id})
    cur.execute("SET LOCAL session_replication_role = origin")
    _unused_departments(cur, hotel_id)


# ---------- Finance ----------

FIN_SHAPES = SETUP_SHAPES + (
    ("business_day", ("business_day_id", "hotel_id", "business_date", "status", "opened_at", "opened_by_user_id", "closed_at", "closed_by_user_id")),
    ("cashier_shift", ("cashier_shift_id", "hotel_id", "business_day_id", "cashier_user_id", "currency", "opening_float", "status", "opened_at",
                       "closed_at", "expected_cash", "counted_cash")),
    ("finance_posting", ("posting_id", "hotel_id", "business_day_id", "business_date", "folio_type", "folio_ref", "booking_id", "ar_account_id",
                         "outlet_id", "transaction_code_id", "transaction_type", "quantity", "amount", "currency", "tax_code_id", "tax_rule_id",
                         "parent_posting_id", "reverses_posting_id", "cashier_shift_id", "reason", "reference", "source_module", "source_ref",
                         "posted_at", "posted_by_user_id", "posted_by_system")),
    ("daily_close_summary", ("daily_close_summary_id", "hotel_id", "business_day_id", "business_date", "transaction_code_id", "transaction_type",
                             "department_id", "payment_method", "tax_code_id", "posting_count", "quantity_total", "amount_total", "currency")),
    ("ar_invoice", ("ar_invoice_id", "hotel_id", "ar_account_id", "invoice_number", "invoice_date", "due_date", "currency", "status", "issued_at",
                    "issued_by_user_id")),
    ("ar_invoice_item", ("ar_invoice_item_id", "ar_invoice_id", "posting_id")),
    ("ar_allocation", ("ar_allocation_id", "ar_invoice_id", "credit_posting_id", "amount", "allocated_at", "allocated_by_user_id")),
)
CHECK_PAY_METHODS = ("card", "card", "card", "cash", "cash")


def _closed_checks(cur, hotel_id):
    """Closed F&B checks with net and tax per F&B department (tax rounded per line, as the check total is)."""
    cur.execute("""SELECT c.check_id, c.outlet_id, c.business_date, c.closed_at, c.settlement, c.booking_id, c.total, t.transaction_code,
                          sum(a.net) AS net, sum(round(a.net * %s / 100, 2)) AS tax
                   FROM fnb_check c JOIN fnb_check_line_amount a ON a.check_id = c.check_id
                   JOIN transaction_code t ON t.transaction_code_id = a.transaction_code_id
                   WHERE c.hotel_id = %s AND c.status = 'closed' GROUP BY 1, 2, 3, 4, 5, 6, 7, 8 ORDER BY c.closed_at, c.check_id""",
                (data.FNB_TAX_PERCENT, hotel_id))
    checks = {}
    for r in cur.fetchall():
        c = checks.setdefault(r["check_id"], dict(r, by_department={}, pay_method=CHECK_PAY_METHODS[int(r["check_id"][-2:], 16) % 5]))
        c["by_department"][FNB_DEPARTMENT_OF_CODE[r["transaction_code"]]] = (r["net"], r["tax"])
    return list(checks.values())


def _posting_row(p, hotel_id, day_id):
    folio_type, folio_ref, booking_id = p["folio"]
    return {"posting_id": p["posting_id"], "hotel_id": hotel_id, "business_day_id": day_id, "business_date": p["business_date"], "folio_type": folio_type,
            "folio_ref": folio_ref, "booking_id": booking_id, "ar_account_id": folio_ref if folio_type == "account" else None, "outlet_id": p["outlet_id"],
            "transaction_code_id": p["transaction_code_id"], "transaction_type": p["transaction_type"], "quantity": p["quantity"], "amount": p["amount"],
            "currency": data.CURRENCY, "tax_code_id": p["tax_code_id"], "tax_rule_id": p["tax_rule_id"], "parent_posting_id": p["parent"],
            "reverses_posting_id": p["reverses"], "cashier_shift_id": p["cashier_shift_id"], "reason": p["reason"], "reference": p["reference"],
            "source_module": p["source_module"], "source_ref": p["source_ref"], "posted_at": p["posted_at"], "posted_by_user_id": p["user_id"],
            "posted_by_system": p["system"]}


def seed_finance(cur, hotel_id, anchor, now):
    _require(cur, "finance", FIN_SHAPES)
    if not _count(cur, "SELECT count(*) FROM fnb_check WHERE hotel_id = %s", hotel_id):
        raise NotReady("finance stage pending; run the fnb stage first so F&B checks reach the guest and outlet folios")
    users = _users(cur, hotel_id)
    stays = _stays(cur, hotel_id)
    cur.execute("""SELECT payment_id, booking_id, payment_kind, amount, payment_method, paid_at, recorded_by_user_id FROM payment
                   WHERE hotel_id = %s AND payment_status = 'received' ORDER BY paid_at, payment_id""", (hotel_id,))
    payments = [{"payment_id": r["payment_id"], "booking_id": r["booking_id"], "amount": r["amount"] if r["payment_kind"] == "payment" else -r["amount"],
                 "method": r["payment_method"].lower(), "paid_at": r["paid_at"], "user_id": r["recorded_by_user_id"]} for r in cur.fetchall()]
    start = min([n["stay_date"] for b in stays for n in b["nights_detail"]] + [p["paid_at"].astimezone(data.tz()).date() for p in payments])
    setup = _finance_setup(cur, hotel_id, anchor)
    fin = data.finance(start, anchor, now, stays, payments, _closed_checks(cur, hotel_id), _by_role(users), setup)
    shapes = dict(FIN_SHAPES)
    cur.execute("SELECT transaction_code_id, department_id, payment_method FROM transaction_code WHERE hotel_id = %s", (hotel_id,))
    code_of = {r["transaction_code_id"]: r for r in cur.fetchall()}
    rng = random.Random(data.SEED + 7)
    totals = {"business_day": 0, "cashier_shift": 0, "finance_posting": 0, "daily_close_summary": 0}
    for day in fin["days"]:
        day_id = day["business_day_id"]
        closing = day["closed_at"] is not None
        _insert(cur, "business_day", ("business_day_id", "hotel_id", "business_date", "status", "opened_at", "opened_by_user_id"),
                [{"business_day_id": day_id, "hotel_id": hotel_id, "business_date": day["business_date"], "status": "open", "opened_at": day["opened_at"],
                  "opened_by_user_id": fin["opened_by"]}])
        shifts = list(day["shifts"].values())
        _insert(cur, "cashier_shift", ("cashier_shift_id", "hotel_id", "business_day_id", "cashier_user_id", "currency", "opening_float", "status", "opened_at"),
                [dict(s, hotel_id=hotel_id, business_day_id=day_id, currency=data.CURRENCY, status="open") for s in shifts])
        _insert(cur, "finance_posting", shapes["finance_posting"], [_posting_row(p, hotel_id, day_id) for p in day["postings"]])
        totals["business_day"] += 1
        totals["cashier_shift"] += len(shifts)
        totals["finance_posting"] += len(day["postings"])
        if not closing:
            continue
        if shifts:
            execute_values(cur, """UPDATE cashier_shift c SET status = 'closed', closed_at = v.closed_at, expected_cash = v.expected, counted_cash = v.counted
                                   FROM (VALUES %s) AS v(id, closed_at, expected, counted) WHERE c.cashier_shift_id = v.id""",
                           [(s["cashier_shift_id"], s["closed_at"], s["expected_cash"], s["counted_cash"]) for s in shifts])
        cur.execute("""SELECT transaction_code_id, transaction_type, tax_code_id, count(*) AS n, sum(quantity) AS q, sum(amount) AS a
                       FROM finance_posting WHERE business_day_id = %s GROUP BY 1, 2, 3 ORDER BY 1, 3""", (day_id,))
        summary = [{"daily_close_summary_id": data.new_id(rng, "dcs"), "hotel_id": hotel_id, "business_day_id": day_id, "business_date": day["business_date"],
                    "transaction_code_id": r["transaction_code_id"], "transaction_type": r["transaction_type"],
                    "department_id": code_of[r["transaction_code_id"]]["department_id"], "payment_method": code_of[r["transaction_code_id"]]["payment_method"],
                    "tax_code_id": r["tax_code_id"], "posting_count": r["n"], "quantity_total": r["q"], "amount_total": r["a"], "currency": data.CURRENCY}
                   for r in cur.fetchall()]
        totals["daily_close_summary"] += _insert(cur, "daily_close_summary", shapes["daily_close_summary"], summary)
        cur.execute("UPDATE business_day SET status = 'closed', closed_at = %s, closed_by_user_id = %s WHERE business_day_id = %s",
                    (day["closed_at"], day["closed_by"], day_id))
    for table, n in totals.items():
        print("  ", table, n, flush=True)
    invoices = [dict(i, hotel_id=hotel_id, currency=data.CURRENCY, status="open", issued_by_user_id=i["issued_by"]) for i in fin["invoices"]]
    items = [{"ar_invoice_item_id": data.new_id(rng, "aii"), "ar_invoice_id": i["ar_invoice_id"], "posting_id": p} for i in fin["invoices"] for p in i["items"]]
    allocations = [dict(a, ar_invoice_id=i["ar_invoice_id"], allocated_by_user_id=i["issued_by"]) for i in fin["invoices"] for a in i["allocations"]]
    _write(cur, [("ar_invoice", shapes["ar_invoice"], invoices), ("ar_invoice_item", shapes["ar_invoice_item"], items),
                 ("ar_allocation", shapes["ar_allocation"], allocations)])


def reset_finance(cur, hotel_id):
    """Finance rows are guarded by immutability triggers; the reset bypasses them for this transaction only and deletes this hotel's
    days, postings and invoices. The code/tax/account setup stays with the fnb stage."""
    cur.execute("SET LOCAL session_replication_role = replica")
    for sql in (
        "DELETE FROM ar_allocation WHERE ar_invoice_id IN (SELECT ar_invoice_id FROM ar_invoice WHERE hotel_id = %(h)s)",
        "DELETE FROM ar_invoice_item WHERE ar_invoice_id IN (SELECT ar_invoice_id FROM ar_invoice WHERE hotel_id = %(h)s)",
        "DELETE FROM ar_invoice WHERE hotel_id = %(h)s",
        "DELETE FROM daily_close_summary WHERE hotel_id = %(h)s",
        "DELETE FROM finance_posting WHERE hotel_id = %(h)s",
        "DELETE FROM cashier_shift WHERE hotel_id = %(h)s",
        "DELETE FROM business_day WHERE hotel_id = %(h)s",
    ):
        cur.execute(sql, {"h": hotel_id})
    cur.execute("SET LOCAL session_replication_role = origin")


# ---------- stage control ----------

PRESENCE = {
    "core": "SELECT count(*) FROM hotel WHERE hotel_id = %s",
    "hr": "SELECT count(*) FROM staff WHERE hotel_id = %s",
    "fnb": "SELECT count(*) FROM outlet WHERE hotel_id = %s",
    "finance": "SELECT count(*) FROM business_day WHERE hotel_id = %s",
}
PRESENCE_TABLE = {"core": "hotel", "hr": "staff", "fnb": "outlet", "finance": "business_day"}
SEED = {"hr": seed_hr, "fnb": seed_fnb, "finance": seed_finance}
RESET = {"core": reset_core, "hr": reset_hr, "fnb": reset_fnb, "finance": reset_finance}


def _seeded(cur, hotel_id):
    have = _columns(cur)
    return {s for s in STAGES if hotel_id and PRESENCE_TABLE[s] in have and _count(cur, PRESENCE[s], hotel_id)}


def run_stage(stage, reset, anchor, now):
    conn = connect()
    try:
        with conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT set_config('lock_timeout', '10s', true), set_config('statement_timeout', '0', true)")
            hotel_id = _hotel_id(cur)
            seeded = _seeded(cur, hotel_id)
            later = [s for s in STAGES[STAGES.index(stage) + 1:] if s in seeded]
            if stage in seeded and not reset:
                raise NotReady(stage + " stage: " + data.HOTEL_NAME + " already has " + stage + " data; add --reset to rebuild it")
            if stage in seeded and later:
                raise NotReady(stage + " reset: later stages " + ", ".join(later) + " hold data; use --stage all --reset")
            if stage != "core" and "core" not in seeded:
                raise NotReady(stage + " stage pending; run the core stage first")
            print(stage + ":", "resetting" if stage in seeded else "seeding", data.HOTEL_NAME, "anchor", anchor.isoformat(), flush=True)
            if stage in seeded:
                RESET[stage](cur, hotel_id)
                hotel_id = _hotel_id(cur)
            if stage == "core":
                seed_core(cur, anchor, now)
            else:
                SEED[stage](cur, hotel_id, anchor, now)
        print(stage + ": done", flush=True)
        return True
    except NotReady as reason:
        print("PENDING", reason, flush=True)
        return False
    finally:
        conn.close()


def reset_all():
    conn = connect()
    try:
        with conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            hotel_id = _hotel_id(cur)
            seeded = _seeded(cur, hotel_id)
            for stage in reversed(STAGES):
                if stage in seeded:
                    RESET[stage](cur, hotel_id)
                    print("reset", stage, flush=True)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Seed LEON TEST HOTEL")
    parser.add_argument("--stage", choices=STAGES + ("all", "verify"), default="all")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    now = _now()
    anchor = now.date()
    if args.stage == "verify":
        from fixtures.test_hotel_verify import report
        report()
        return
    if args.stage == "all" and args.reset:
        reset_all()
    stages = STAGES if args.stage == "all" else (args.stage,)
    done = {s: run_stage(s, args.reset and args.stage != "all", anchor, now) for s in stages}
    print("stages:", ", ".join(s + "=" + ("done" if ok else "pending") for s, ok in done.items()))
    print("logins:", ", ".join(data.STAFF_EMAILS), "| password: test_hotel_data.PASSWORD")


if __name__ == "__main__":
    main()
