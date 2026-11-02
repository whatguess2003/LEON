"""Synthetic HR test rows for a fixture hotel, written through the HR service (read first, so a rerun reports exists).
Run: python -m booking_system.hr.seed "SANDBOX HOTEL" """

import sys
from datetime import datetime, time, timedelta

from dotenv import load_dotenv

load_dotenv()

from booking_system.hr import service

DEPARTMENTS = (("ROOMS", "operated"), ("HOUSEKEEPING", "operated"), ("MAINTENANCE", "undistributed"), ("MANAGEMENT", "undistributed"))
POSITIONS = (("FRONT DESK AGENT", "ROOMS"), ("ROOM ATTENDANT", "HOUSEKEEPING"), ("HOUSEKEEPING SUPERVISOR", "HOUSEKEEPING"),
             ("MAINTENANCE TECHNICIAN", "MAINTENANCE"), ("GENERAL MANAGER", "MANAGEMENT"))
SHIFTS = (("MORNING", time(7), time(15), 30), ("EVENING", time(15), time(23), 30), ("NIGHT", time(23), time(7), 30))
LEAVE_TYPES = (("ANNUAL", True), ("SICK", True), ("UNPAID", False))
# (name, position, phone, email)
STAFF = (
    ("HRTEST ATTENDANT ONE", "ROOM ATTENDANT", "+15555550130", "attendant1@example.com"),
    ("HRTEST ATTENDANT TWO", "ROOM ATTENDANT", "+15555550131", None),
    ("HRTEST ATTENDANT THREE", "ROOM ATTENDANT", "+15555550132", None),
    ("HRTEST SUPERVISOR", "HOUSEKEEPING SUPERVISOR", "+15555550133", None),
    ("HRTEST TECHNICIAN", "MAINTENANCE TECHNICIAN", "+15555550134", "technician@example.com"),
    ("HRTEST DESK AGENT", "FRONT DESK AGENT", "+15555550135", None),
)
# (staff name, shift, day offsets from hotel-local today)
ROSTER = (
    ("HRTEST ATTENDANT ONE", "MORNING", (-1, 0, 1)),
    ("HRTEST ATTENDANT TWO", "MORNING", (-1, 0, 1)),
    ("HRTEST ATTENDANT THREE", "MORNING", (-1, 1)),
    ("HRTEST SUPERVISOR", "MORNING", (0, 1)),
    ("HRTEST TECHNICIAN", "EVENING", (-1, 0)),
    ("HRTEST DESK AGENT", "NIGHT", (-1, 0)),
)


def _row(result, kind):
    if result["status"] not in ("created", "exists", "ok"):
        raise RuntimeError(kind + ": " + str(result))
    return result[kind]


def _hotel(hotel_name):
    def run(cur):
        cur.execute("SELECT hotel_id, (now() AT TIME ZONE timezone)::date AS today, timezone FROM hotel WHERE hotel_name = %s", (hotel_name,))
        return cur.fetchone()
    return service._run(run)


def _manager(hotel_id):
    """(the hotel's GENERAL_MANAGER login, None) — D8: a manager is also staff — or (None, why it is pending)."""
    def run(cur):
        cur.execute(
            "SELECT count(*) AS found FROM pg_attribute WHERE NOT attisdropped AND ((attrelid = to_regclass('user_account') AND attname IN ('user_id', 'user_name')) "
            "OR (attrelid = to_regclass('hotel_user') AND attname IN ('hotel_id', 'user_id', 'role_id', 'status')) "
            "OR (attrelid = to_regclass('access_role') AND attname IN ('role_id', 'role_name')))"
        )
        if cur.fetchone()["found"] < 8:
            return None, "pending: user tables not built"
        cur.execute(
            "SELECT u.user_id, u.user_name FROM hotel_user h JOIN access_role r ON r.role_id = h.role_id JOIN user_account u ON u.user_id = h.user_id "
            "WHERE h.hotel_id = %s AND r.role_name = 'GENERAL_MANAGER' AND h.status = 'active' ORDER BY u.user_name LIMIT 1",
            (hotel_id,),
        )
        row = cur.fetchone()
        return row, None if row else "pending: no active GENERAL_MANAGER login for this hotel"
    return service._run(run)


def _local(day, clock, zone):
    from zoneinfo import ZoneInfo
    return datetime.combine(day, clock, ZoneInfo(zone))


def seed(hotel_name):
    hotel = _hotel(hotel_name)
    if hotel is None:
        raise RuntimeError("hotel " + hotel_name + " is not registered")
    hotel_id, today, zone = hotel["hotel_id"], hotel["today"], hotel["timezone"]
    manager, manager_pending = _manager(hotel_id)
    report = {}

    departments = {}
    for name, kind in DEPARTMENTS:
        result = service.create_named("department", hotel_id, name, {"department_type": kind}, confirm_new=True)
        departments[name] = _row(result, "department")["department_id"]
        report["department " + name] = result["status"]
    positions = {}
    for name, department in POSITIONS:
        result = service.create_named("position", hotel_id, name, {"department_id": departments[department]}, confirm_new=True)
        positions[name] = _row(result, "position")
        report["position " + name] = result["status"]
    shifts = {}
    for name, starts, ends, minutes in SHIFTS:
        result = service.create_named("shift", hotel_id, name, {"starts_at": starts, "ends_at": ends, "break_minutes": minutes}, confirm_new=True)
        shifts[name] = _row(result, "shift")
        report["shift " + name] = result["status"]
    leave_types = {}
    for name, paid in LEAVE_TYPES:
        result = service.create_named("leave_type", hotel_id, name, {"paid": paid}, confirm_new=True)
        leave_types[name] = _row(result, "leave_type")["leave_type_id"]
        report["leave_type " + name] = result["status"]

    staff = {}
    for name, position, phone, email in STAFF:
        result = service.create_staff(hotel_id, name, positions[position]["department_id"], "full_time", phone=phone, email=email,
                                      position_id=positions[position]["position_id"], hired_on=today - timedelta(days=90), confirm_new=True)
        staff[name] = _row(result, "staff")["staff_id"]
        report["staff " + name] = result["status"]
    if manager:
        result = service.create_staff(hotel_id, manager["user_name"], departments["MANAGEMENT"], "full_time",
                                      position_id=positions["GENERAL MANAGER"]["position_id"], user_id=manager["user_id"], confirm_new=True)
        report["staff (manager) " + manager["user_name"]] = result["status"]
    else:
        report["staff (manager)"] = manager_pending

    sick = service.request_leave(hotel_id, staff["HRTEST ATTENDANT THREE"], leave_types["SICK"], today, today, manager and manager["user_id"])
    report["leave SICK attendant three"] = sick["status"]
    if sick["status"] == "created":
        report["leave SICK approve"] = service.approve_leave(hotel_id, sick["leave"]["leave_request_id"], manager and manager["user_id"])["status"]
    report["leave ANNUAL attendant two"] = service.request_leave(
        hotel_id, staff["HRTEST ATTENDANT TWO"], leave_types["ANNUAL"], today + timedelta(days=7), today + timedelta(days=9), None)["status"]

    for name, shift, offsets in ROSTER:
        department = positions[next(p for n, p, *_ in STAFF if n == name)]["department_id"]
        result = service.create_roster(hotel_id, staff[name], shifts[shift]["shift_id"], department, [today + timedelta(days=k) for k in offsets])
        report["roster " + name] = [item["status"] for item in _row(result, "results")]

    # (staff, day offset, minutes after shift start for clock-in, clock-out at shift end); today's rows stay open
    for name, offset, late in (("HRTEST ATTENDANT ONE", -1, 0), ("HRTEST ATTENDANT TWO", -1, 25), ("HRTEST TECHNICIAN", -1, 5),
                               ("HRTEST ATTENDANT ONE", 0, 12), ("HRTEST ATTENDANT TWO", 0, 0)):
        day = today + timedelta(days=offset)
        if service.list_attendance(hotel_id, day, day, staff[name])["attendance"]:
            report["attendance " + name + " " + str(day)] = "exists"
            continue
        roster = service.list_roster(hotel_id, day, day, staff_id=staff[name])["roster"][0]
        opened = service.clock_in(hotel_id, staff[name], None, roster["roster_id"])
        attendance = _row(opened, "attendance")
        start = _local(day, roster["starts_at"], zone)
        changes = {"clock_in": start + timedelta(minutes=late)}
        if offset < 0:
            changes["clock_out"] = start + timedelta(hours=8)
        service.correct_attendance(hotel_id, attendance["attendance_id"], changes)
        report["attendance " + name + " " + str(day)] = opened["status"]
    return report


if __name__ == "__main__":
    for key, value in seed(sys.argv[1]).items():
        print(key, "|", value)
