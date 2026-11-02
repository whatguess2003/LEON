"""Staff lookups for housekeeping and maintenance. Who a person is comes from HR's staff and staff_identity tables; whether they
may take a task, who is available and who is on duty come from the HR service (roster, leave, attendance). HR owns every write."""

from booking_system import db
from booking_system.hr import service as hr

STAFF_SELECT = """
    SELECT s.staff_id, s.staff_name, s.status, d.department_name AS department, p.position_name AS role,
           (SELECT i.identity_value FROM staff_identity i WHERE i.staff_id = s.staff_id AND i.identity_type = 'phone'
            ORDER BY i.identity_value LIMIT 1) AS staff_phone
    FROM staff s
    JOIN department d ON d.department_id = s.department_id
    LEFT JOIN staff_position p ON p.position_id = s.position_id
"""


def resolve_staff(cur, hotel_id, staff):
    """Staff rows of this hotel whose staff_id, phone/email identity or exact name equals the text (callers need exactly one)."""
    text = str(staff or "").strip()
    cur.execute(
        STAFF_SELECT + """ WHERE s.hotel_id = %s AND (s.staff_id = %s OR s.staff_name = %s
              OR EXISTS (SELECT 1 FROM staff_identity i WHERE i.staff_id = s.staff_id AND i.identity_value IN (%s, %s)))""",
        (hotel_id, text, text.upper(), text, text.lower()),
    )
    return cur.fetchall()


def one_staff(cur, hotel_id, staff):
    """(row, '') for exactly one active match, else (None, reason)."""
    found = resolve_staff(cur, hotel_id, staff)
    active = [row for row in found if row["status"] == "active"]
    if len(active) == 1:
        return active[0], ""
    if not active:
        return None, "no active staff matches " + str(staff or "")
    return None, str(len(active)) + " staff match " + str(staff or "") + "; use the staff_id"


def _department_today(cur, hotel_id, department_name):
    """(department_id, hotel-local today) or (None, today) when the hotel has no such department."""
    cur.execute(
        """SELECT d.department_id, (now() AT TIME ZONE h.timezone)::date AS today FROM hotel h
           LEFT JOIN department d ON d.hotel_id = h.hotel_id AND d.department_name = upper(%s) WHERE h.hotel_id = %s""",
        (department_name, hotel_id),
    )
    row = cur.fetchone()
    return row["department_id"], row["today"]


def assignable_staff(cur, hotel_id, staff, department_name):
    """(row, '') when the person resolves and HR says they may take a task of this department today, else (None, reason)."""
    person, reason = one_staff(cur, hotel_id, staff)
    if person is None:
        return None, reason
    department_id, today = _department_today(cur, hotel_id, department_name)
    if department_id is None:
        return None, "this hotel has no " + str(department_name) + " department"
    verdict = hr.check_assignable(hotel_id, person["staff_id"], department_id, today)
    if not verdict.get("assignable"):
        return None, person["staff_name"] + " cannot take " + str(department_name).lower() + " work today: " + str(verdict.get("reason"))
    return person, ""


def read_staff_profiles(hotel_id, staff=None, department=None, limit=50):
    """Staff of this hotel, optionally one person (id, phone, email or name) and/or one department name."""
    return {"staff": db.rows(
        STAFF_SELECT + """ WHERE s.hotel_id = %s AND (%s::text IS NULL OR s.staff_id = %s OR s.staff_name = upper(%s)
              OR EXISTS (SELECT 1 FROM staff_identity i WHERE i.staff_id = s.staff_id AND i.identity_value IN (%s, lower(%s))))
              AND (%s::text IS NULL OR d.department_name = upper(%s))
            ORDER BY s.staff_name LIMIT %s""",
        (hotel_id, staff, staff, staff, staff, staff, department, department, int(limit)),
    )}


def get_available_staff_json(hotel_id, department):
    """HR's available staff for a department today (hotel local): active, rostered there, not on approved leave; on duty first."""
    department_id, today = db.transaction(lambda cur: _department_today(cur, hotel_id, department))
    if department_id is None:
        return {"error": "this hotel has no " + str(department) + " department"}
    found = hr.available_staff(hotel_id, department_id, today)
    return {"department": str(department).upper(), "work_date": today.isoformat(), "staff": found.get("staff", [])}


def on_duty_staff(hotel_id, department=None):
    """HR's clocked-in staff, optionally only those rostered in one department."""
    department_id = None
    if department:
        department_id, _today = db.transaction(lambda cur: _department_today(cur, hotel_id, department))
        if department_id is None:
            return {"error": "this hotel has no " + str(department) + " department"}
    return {"on_duty": hr.on_duty_staff(hotel_id, department_id).get("staff", [])}
