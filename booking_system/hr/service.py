"""HR service. Every call opens one short transaction (settings are SET LOCAL), reads first, then writes.
Results carry a status: created, exists, updated, deleted, clarify (near matches to confirm), refused (reason), not_found, ok."""

import uuid

import psycopg2
from psycopg2.extras import Json

from booking_system.db import transaction

NEAR = 0.4
PROFILE = ("leon_hr", "staff_profile")
NAMED = {
    "department": {"table": "department", "id": "department_id", "prefix": "dep", "name": "department_name", "fields": ("department_type",)},
    "position": {"table": "staff_position", "id": "position_id", "prefix": "pos", "name": "position_name", "fields": ("department_id",)},
    "shift": {"table": "shift", "id": "shift_id", "prefix": "shf", "name": "shift_name", "fields": ("starts_at", "ends_at", "break_minutes")},
    "leave_type": {"table": "leave_type", "id": "leave_type_id", "prefix": "lvt", "name": "leave_type_name", "fields": ("paid",)},
}
STAFF_FIELDS = ("staff_name", "department_id", "position_id", "user_id", "employment_type", "status", "hired_on", "terminated_on")
ATTENDANCE_FIELDS = ("clock_in", "clock_out")
STAFF_SELECT = """
    SELECT s.staff_id, s.staff_name, s.status, s.employment_type, s.hired_on, s.terminated_on, s.user_id,
           s.department_id, d.department_name, s.position_id, p.position_name,
           COALESCE((SELECT json_agg(json_build_object('staff_identity_id', i.staff_identity_id, 'kind', i.identity_type, 'value', i.identity_value)
                                     ORDER BY i.identity_type, i.identity_value)
                     FROM staff_identity i WHERE i.staff_id = s.staff_id), '[]') AS identities
    FROM staff s
    JOIN department d ON d.department_id = s.department_id
    LEFT JOIN staff_position p ON p.position_id = s.position_id
"""


def new_id(prefix):
    return prefix + "_" + str(uuid.uuid4())


def _run(fn):
    try:
        return transaction(fn, (("statement_timeout", 8000), ("lock_timeout", 3000)))
    except (psycopg2.IntegrityError, psycopg2.DataError) as error:
        return {"status": "refused", "reason": error.diag.message_primary}


def _name(value):
    return " ".join(str(value or "").split()).upper()


def _identity_value(kind, value):
    text = str(value or "").strip()
    return {"phone": text, "email": text.lower()}[kind]


def _local_today(cur, hotel_id):
    cur.execute("SELECT (now() AT TIME ZONE timezone)::date AS today FROM hotel WHERE hotel_id = %s", (hotel_id,))
    row = cur.fetchone()
    return row["today"] if row else None


# Departments, positions, shifts, leave types

def list_named(kind, hotel_id):
    spec = NAMED[kind]
    def run(cur):
        cur.execute("SELECT * FROM " + spec["table"] + " WHERE hotel_id = %s ORDER BY " + spec["name"], (hotel_id,))
        return {"status": "ok", kind + "s": cur.fetchall()}
    return _run(run)


def _find_named(cur, spec, hotel_id, name):
    cur.execute(
        "SELECT *, similarity(" + spec["name"] + ", %s) AS score FROM " + spec["table"]
        + " WHERE hotel_id = %s AND (" + spec["name"] + " = %s OR similarity(" + spec["name"] + ", %s) >= %s) ORDER BY score DESC",
        (name, hotel_id, name, name, NEAR),
    )
    rows = cur.fetchall()
    return [row for row in rows if row[spec["name"]] == name], rows


def find_named(kind, hotel_id, name):
    spec = NAMED[kind]
    def run(cur):
        exact, near = _find_named(cur, spec, hotel_id, _name(name))
        if exact:
            return {"status": "ok", kind: exact[0]}
        if near:
            return {"status": "clarify", "matches": near}
        return {"status": "not_found"}
    return _run(run)


def create_named(kind, hotel_id, name, values, confirm_new=False):
    """values holds exactly spec['fields']. A near-named row is returned for clarification unless confirm_new."""
    spec = NAMED[kind]
    canonical = _name(name)
    def run(cur):
        exact, near = _find_named(cur, spec, hotel_id, canonical)
        if exact:
            return {"status": "exists", kind: exact[0]}
        if near and not confirm_new:
            return {"status": "clarify", "matches": near}
        columns = (spec["id"], "hotel_id", spec["name"]) + spec["fields"]
        cur.execute(
            "INSERT INTO " + spec["table"] + " (" + ", ".join(columns) + ") VALUES (" + ", ".join(["%s"] * len(columns)) + ") RETURNING *",
            (new_id(spec["prefix"]), hotel_id, canonical) + tuple(values[field] for field in spec["fields"]),
        )
        return {"status": "created", kind: cur.fetchone()}
    return _run(run)


def update_named(kind, hotel_id, entity_id, changes):
    spec = NAMED[kind]
    allowed = (spec["name"],) + spec["fields"]
    fields = {field: changes[field] for field in allowed if field in changes}
    if spec["name"] in fields:
        fields[spec["name"]] = _name(fields[spec["name"]])
    def run(cur):
        cur.execute("SELECT * FROM " + spec["table"] + " WHERE hotel_id = %s AND " + spec["id"] + " = %s FOR UPDATE", (hotel_id, entity_id))
        current = cur.fetchone()
        if current is None:
            return {"status": "not_found"}
        if all(current[field] == value for field, value in fields.items()):
            return {"status": "unchanged", kind: current}
        cur.execute(
            "UPDATE " + spec["table"] + " SET " + ", ".join(field + " = %s" for field in fields) + " WHERE " + spec["id"] + " = %s RETURNING *",
            tuple(fields.values()) + (entity_id,),
        )
        return {"status": "updated", kind: cur.fetchone()}
    return _run(run)


def delete_named(kind, hotel_id, entity_id):
    """Refused while anything references the row."""
    spec = NAMED[kind]
    def run(cur):
        cur.execute("DELETE FROM " + spec["table"] + " WHERE hotel_id = %s AND " + spec["id"] + " = %s RETURNING *", (hotel_id, entity_id))
        row = cur.fetchone()
        return {"status": "deleted", kind: row} if row else {"status": "not_found"}
    return _run(run)


# Staff

def _staff(cur, hotel_id, staff_id):
    cur.execute(STAFF_SELECT + " WHERE s.hotel_id = %s AND s.staff_id = %s", (hotel_id, staff_id))
    return cur.fetchone()


def _staff_matches(cur, hotel_id, text):
    """Staff whose identity equals the text, whose name equals it, or whose name is near it."""
    cur.execute(
        STAFF_SELECT + """
        , LATERAL (SELECT EXISTS (SELECT 1 FROM staff_identity i WHERE i.staff_id = s.staff_id AND i.identity_value IN (%s, %s)) AS by_identity,
                          s.staff_name = %s AS by_name, similarity(s.staff_name, %s) AS score) m
        WHERE s.hotel_id = %s AND (m.by_identity OR m.by_name OR m.score >= %s)
        ORDER BY m.by_identity DESC, m.by_name DESC, m.score DESC
        """,
        (str(text or "").strip(), str(text or "").strip().lower(), _name(text), _name(text), hotel_id, NEAR),
    )
    return cur.fetchall()


def read_staff(hotel_id, staff_id):
    def run(cur):
        row = _staff(cur, hotel_id, staff_id)
        return {"status": "ok", "staff": row} if row else {"status": "not_found"}
    return _run(run)


def list_staff(hotel_id, department_id=None, status=None):
    def run(cur):
        cur.execute(
            STAFF_SELECT + " WHERE s.hotel_id = %s AND (%s::varchar IS NULL OR s.department_id = %s) AND (%s::varchar IS NULL OR s.status = %s) ORDER BY s.staff_name",
            (hotel_id, department_id, department_id, status, status),
        )
        return {"status": "ok", "staff": cur.fetchall()}
    return _run(run)


def find_staff(hotel_id, text):
    """One staff member by phone, email or name; several candidates come back for clarification."""
    def run(cur):
        rows = _staff_matches(cur, hotel_id, text)
        if len(rows) == 1:
            return {"status": "ok", "staff": rows[0]}
        if rows:
            return {"status": "clarify", "matches": rows}
        return {"status": "not_found"}
    return _run(run)


def create_staff(hotel_id, staff_name, department_id, employment_type, phone=None, email=None, position_id=None,
                 user_id=None, hired_on=None, confirm_new=False):
    identities = [(kind, _identity_value(kind, value)) for kind, value in (("phone", phone), ("email", email)) if value]
    def run(cur):
        cur.execute(
            "SELECT s.staff_id FROM staff s WHERE s.hotel_id = %s AND (s.user_id = %s OR EXISTS (SELECT 1 FROM staff_identity i "
            "WHERE i.staff_id = s.staff_id AND (i.identity_type, i.identity_value) IN %s))",
            (hotel_id, user_id, tuple(identities) or (("", ""),)),
        )
        bound = cur.fetchone()
        if bound:
            return {"status": "exists", "staff": _staff(cur, hotel_id, bound["staff_id"])}
        near = _staff_matches(cur, hotel_id, staff_name)
        if near and not confirm_new:
            return {"status": "clarify", "matches": near}
        staff_id = new_id("stf")
        cur.execute(
            """INSERT INTO staff (staff_id, hotel_id, staff_name, department_id, position_id, user_id, employment_type, status, hired_on)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s)""",
            (staff_id, hotel_id, _name(staff_name), department_id, position_id, user_id, employment_type, hired_on),
        )
        for kind, value in identities:
            cur.execute(
                "INSERT INTO staff_identity (staff_identity_id, hotel_id, staff_id, identity_type, identity_value) VALUES (%s, %s, %s, %s, %s)",
                (new_id("sid"), hotel_id, staff_id, kind, value),
            )
        return {"status": "created", "staff": _staff(cur, hotel_id, staff_id)}
    return _run(run)


def update_staff(hotel_id, staff_id, changes):
    """Terminating sets status 'terminated' together with terminated_on (the table enforces both)."""
    fields = {field: changes[field] for field in STAFF_FIELDS if field in changes}
    if "staff_name" in fields:
        fields["staff_name"] = _name(fields["staff_name"])
    def run(cur):
        cur.execute("SELECT * FROM staff WHERE hotel_id = %s AND staff_id = %s FOR UPDATE", (hotel_id, staff_id))
        current = cur.fetchone()
        if current is None:
            return {"status": "not_found"}
        if all(current[field] == value for field, value in fields.items()):
            return {"status": "unchanged", "staff": _staff(cur, hotel_id, staff_id)}
        cur.execute("UPDATE staff SET " + ", ".join(field + " = %s" for field in fields) + " WHERE staff_id = %s", tuple(fields.values()) + (staff_id,))
        return {"status": "updated", "staff": _staff(cur, hotel_id, staff_id)}
    return _run(run)


def delete_staff(hotel_id, staff_id):
    """Only a staff member with no roster, attendance or leave history; otherwise terminate."""
    def run(cur):
        row = _staff(cur, hotel_id, staff_id)
        if row is None:
            return {"status": "not_found"}
        cur.execute("DELETE FROM staff WHERE staff_id = %s", (staff_id,))
        cur.execute("DELETE FROM content_document WHERE database_name = %s AND collection_name = %s AND document_key = %s", PROFILE + (staff_id,))
        return {"status": "deleted", "staff": row}
    return _run(run)


def add_identity(hotel_id, staff_id, identity_type, identity_value):
    value = _identity_value(identity_type, identity_value)
    def run(cur):
        cur.execute(
            "SELECT i.*, s.staff_name FROM staff_identity i JOIN staff s USING (staff_id) WHERE i.hotel_id = %s AND i.identity_type = %s AND i.identity_value = %s",
            (hotel_id, identity_type, value),
        )
        bound = cur.fetchone()
        if bound and bound["staff_id"] == staff_id:
            return {"status": "exists", "identity": bound}
        if bound:
            return {"status": "refused", "reason": value + " belongs to " + bound["staff_name"], "identity": bound}
        cur.execute(
            "INSERT INTO staff_identity (staff_identity_id, hotel_id, staff_id, identity_type, identity_value) VALUES (%s, %s, %s, %s, %s) RETURNING *",
            (new_id("sid"), hotel_id, staff_id, identity_type, value),
        )
        return {"status": "created", "identity": cur.fetchone()}
    return _run(run)


def remove_identity(hotel_id, staff_identity_id):
    def run(cur):
        cur.execute("DELETE FROM staff_identity WHERE hotel_id = %s AND staff_identity_id = %s RETURNING *", (hotel_id, staff_identity_id))
        row = cur.fetchone()
        return {"status": "deleted", "identity": row} if row else {"status": "not_found"}
    return _run(run)


def read_staff_profile(hotel_id, staff_id):
    """Personal content (address, emergency contact, ID document): the staff_profile document keyed by staff_id."""
    def run(cur):
        cur.execute(
            "SELECT body FROM content_document WHERE database_name = %s AND collection_name = %s AND document_key = %s AND body->>'hotel_id' = %s",
            PROFILE + (staff_id, hotel_id),
        )
        row = cur.fetchone()
        return {"status": "ok", "profile": row["body"]} if row else {"status": "not_found"}
    return _run(run)


def write_staff_profile(hotel_id, staff_id, fields):
    """Merges fields into the document; a field set to None is removed."""
    def run(cur):
        if _staff(cur, hotel_id, staff_id) is None:
            return {"status": "not_found"}
        cur.execute(
            "SELECT body FROM content_document WHERE database_name = %s AND collection_name = %s AND document_key = %s FOR UPDATE",
            PROFILE + (staff_id,),
        )
        row = cur.fetchone()
        body = dict(row["body"] if row else {}, **fields, staff_id=staff_id, hotel_id=hotel_id)
        body = {key: value for key, value in body.items() if value is not None}
        cur.execute(
            """INSERT INTO content_document (database_name, collection_name, document_key, body) VALUES (%s, %s, %s, %s)
               ON CONFLICT (database_name, collection_name, document_key) DO UPDATE SET body = EXCLUDED.body""",
            PROFILE + (staff_id, Json(body)),
        )
        return {"status": "updated" if row else "created", "profile": body}
    return _run(run)


# Rosters

ROSTER_SELECT = """
    SELECT r.roster_id, r.work_date, r.staff_id, s.staff_name, r.department_id, d.department_name,
           r.shift_id, f.shift_name, f.starts_at, f.ends_at, f.break_minutes
    FROM roster r
    JOIN staff s ON s.staff_id = r.staff_id
    JOIN department d ON d.department_id = r.department_id
    JOIN shift f ON f.shift_id = r.shift_id
"""


def create_roster(hotel_id, staff_id, shift_id, department_id, work_dates, user_id=None):
    """One row per date. A date already rostered is returned as exists; a date on approved leave is refused."""
    def run(cur):
        cur.execute("SELECT status FROM staff WHERE hotel_id = %s AND staff_id = %s FOR UPDATE", (hotel_id, staff_id))
        staff = cur.fetchone()
        if staff is None:
            return {"status": "not_found"}
        if staff["status"] != "active":
            return {"status": "refused", "reason": "staff is " + staff["status"]}
        results = []
        for work_date in work_dates:
            cur.execute(ROSTER_SELECT + " WHERE r.staff_id = %s AND r.work_date = %s AND r.shift_id = %s", (staff_id, work_date, shift_id))
            existing = cur.fetchone()
            if existing:
                results.append({"status": "exists", "roster": existing})
                continue
            cur.execute(
                "SELECT leave_request_id FROM leave_request WHERE staff_id = %s AND status = 'approved' AND %s::date BETWEEN start_date AND end_date",
                (staff_id, work_date),
            )
            leave = cur.fetchone()
            if leave:
                results.append({"status": "refused", "work_date": work_date, "reason": "on approved leave", "leave_request_id": leave["leave_request_id"]})
                continue
            roster_id = new_id("ros")
            cur.execute(
                "INSERT INTO roster (roster_id, hotel_id, staff_id, shift_id, department_id, work_date, created_by_user_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (roster_id, hotel_id, staff_id, shift_id, department_id, work_date, user_id),
            )
            cur.execute(ROSTER_SELECT + " WHERE r.roster_id = %s", (roster_id,))
            results.append({"status": "created", "roster": cur.fetchone()})
        return {"status": "ok", "results": results}
    return _run(run)


def list_roster(hotel_id, date_from, date_to, department_id=None, staff_id=None):
    def run(cur):
        cur.execute(
            ROSTER_SELECT + """ WHERE r.hotel_id = %s AND r.work_date BETWEEN %s AND %s
              AND (%s::varchar IS NULL OR r.department_id = %s) AND (%s::varchar IS NULL OR r.staff_id = %s)
            ORDER BY r.work_date, f.starts_at, s.staff_name""",
            (hotel_id, date_from, date_to, department_id, department_id, staff_id, staff_id),
        )
        return {"status": "ok", "roster": cur.fetchall()}
    return _run(run)


def delete_roster(hotel_id, roster_id):
    """Refused once attendance is recorded against it."""
    def run(cur):
        cur.execute(ROSTER_SELECT + " WHERE r.hotel_id = %s AND r.roster_id = %s", (hotel_id, roster_id))
        row = cur.fetchone()
        if row is None:
            return {"status": "not_found"}
        cur.execute("DELETE FROM roster WHERE roster_id = %s", (roster_id,))
        return {"status": "deleted", "roster": row}
    return _run(run)


# Attendance

ATTENDANCE_SELECT = """
    SELECT a.attendance_id, a.staff_id, s.staff_name, a.roster_id, r.work_date, r.department_id, f.shift_name,
           a.clock_in, a.clock_out, a.corrected_by_user_id, a.corrected_at,
           hr_worked_hours(a.hotel_id, COALESCE(r.work_date, (a.clock_in AT TIME ZONE h.timezone)::date), a.clock_in, a.clock_out,
                           f.break_minutes) AS worked_hours,
           hotel_setting_at(a.hotel_id, 'hr.worked_hours_breaks', COALESCE(r.work_date, (a.clock_in AT TIME ZONE h.timezone)::date)) #>> '{}'
               AS break_rule,
           CASE WHEN r.roster_id IS NOT NULL THEN
               greatest(0, floor(extract(epoch FROM a.clock_in - ((r.work_date + f.starts_at) AT TIME ZONE h.timezone)) / 60))::int END AS late_minutes
    FROM staff_attendance a
    JOIN staff s ON s.staff_id = a.staff_id
    JOIN hotel h ON h.hotel_id = a.hotel_id
    LEFT JOIN roster r ON r.roster_id = a.roster_id
    LEFT JOIN shift f ON f.shift_id = r.shift_id
"""


def _attendance(cur, attendance_id):
    cur.execute(ATTENDANCE_SELECT + " WHERE a.attendance_id = %s", (attendance_id,))
    return cur.fetchone()


def clock_in(hotel_id, staff_id, user_id=None, roster_id=None):
    """Without roster_id: the staff member's one roster for today (hotel local) is linked; several are returned
    for clarification; none records an unrostered clock-in (roster_id NULL)."""
    def run(cur):
        cur.execute("SELECT status FROM staff WHERE hotel_id = %s AND staff_id = %s FOR UPDATE", (hotel_id, staff_id))
        staff = cur.fetchone()
        if staff is None:
            return {"status": "not_found"}
        if staff["status"] != "active":
            return {"status": "refused", "reason": "staff is " + staff["status"]}
        cur.execute("SELECT attendance_id FROM staff_attendance WHERE staff_id = %s AND clock_out IS NULL", (staff_id,))
        open_row = cur.fetchone()
        if open_row:
            return {"status": "exists", "attendance": _attendance(cur, open_row["attendance_id"])}
        cur.execute(
            ROSTER_SELECT + " WHERE r.staff_id = %s AND (r.roster_id = %s OR (%s::varchar IS NULL AND r.work_date = %s)) ORDER BY f.starts_at",
            (staff_id, roster_id, roster_id, _local_today(cur, hotel_id)),
        )
        rosters = cur.fetchall()
        if roster_id and not rosters:
            return {"status": "refused", "reason": "roster " + roster_id + " is not this staff member's"}
        if len(rosters) > 1:
            return {"status": "clarify", "matches": rosters}
        attendance_id = new_id("att")
        cur.execute(
            "INSERT INTO staff_attendance (attendance_id, hotel_id, staff_id, roster_id, clock_in, clock_in_by_user_id) VALUES (%s, %s, %s, %s, now(), %s)",
            (attendance_id, hotel_id, staff_id, rosters[0]["roster_id"] if rosters else None, user_id),
        )
        return {"status": "created", "attendance": _attendance(cur, attendance_id)}
    return _run(run)


def clock_out(hotel_id, staff_id, user_id=None):
    def run(cur):
        cur.execute(
            "UPDATE staff_attendance SET clock_out = now(), clock_out_by_user_id = %s WHERE hotel_id = %s AND staff_id = %s AND clock_out IS NULL RETURNING attendance_id",
            (user_id, hotel_id, staff_id),
        )
        row = cur.fetchone()
        if row is None:
            return {"status": "refused", "reason": "not clocked in"}
        return {"status": "updated", "attendance": _attendance(cur, row["attendance_id"])}
    return _run(run)


def correct_attendance(hotel_id, attendance_id, changes, user_id=None):
    """Manager correction of clock_in / clock_out (e.g. a forgotten clock-out)."""
    fields = {field: changes[field] for field in ATTENDANCE_FIELDS if field in changes}
    def run(cur):
        cur.execute("SELECT * FROM staff_attendance WHERE hotel_id = %s AND attendance_id = %s FOR UPDATE", (hotel_id, attendance_id))
        if cur.fetchone() is None:
            return {"status": "not_found"}
        cur.execute(
            "UPDATE staff_attendance SET " + ", ".join(field + " = %s" for field in fields) + ", corrected_by_user_id = %s, corrected_at = now() WHERE attendance_id = %s",
            tuple(fields.values()) + (user_id, attendance_id),
        )
        return {"status": "updated", "attendance": _attendance(cur, attendance_id)}
    return _run(run)


def list_attendance(hotel_id, date_from, date_to, staff_id=None):
    """Clock-ins whose hotel-local date is in the range."""
    def run(cur):
        cur.execute(
            ATTENDANCE_SELECT + """ WHERE a.hotel_id = %s AND (a.clock_in AT TIME ZONE h.timezone)::date BETWEEN %s AND %s
              AND (%s::varchar IS NULL OR a.staff_id = %s) ORDER BY a.clock_in""",
            (hotel_id, date_from, date_to, staff_id, staff_id),
        )
        return {"status": "ok", "attendance": cur.fetchall()}
    return _run(run)


# Leave

LEAVE_SELECT = """
    SELECT l.leave_request_id, l.staff_id, s.staff_name, l.leave_type_id, t.leave_type_name, t.paid,
           l.start_date, l.end_date, hr_leave_days(l.hotel_id, l.start_date, l.end_date, l.start_date) AS days,
           hotel_setting_at(l.hotel_id, 'hr.leave_day_counting', l.start_date) #>> '{}' AS day_counting, l.status,
           l.requested_by_user_id, l.requested_at, l.decided_by_user_id, l.decided_at, l.cancelled_by_user_id, l.cancelled_at
    FROM leave_request l
    JOIN staff s ON s.staff_id = l.staff_id
    JOIN leave_type t ON t.leave_type_id = l.leave_type_id
"""


def _leave(cur, leave_request_id):
    cur.execute(LEAVE_SELECT + " WHERE l.leave_request_id = %s", (leave_request_id,))
    return cur.fetchone()


def request_leave(hotel_id, staff_id, leave_type_id, start_date, end_date, user_id=None):
    """The same request again is returned as exists; any other open request overlapping the dates refuses."""
    def run(cur):
        cur.execute("SELECT status FROM staff WHERE hotel_id = %s AND staff_id = %s FOR UPDATE", (hotel_id, staff_id))
        if cur.fetchone() is None:
            return {"status": "not_found"}
        cur.execute(
            LEAVE_SELECT + " WHERE l.staff_id = %s AND l.status IN ('requested', 'approved') AND l.start_date <= %s AND l.end_date >= %s",
            (staff_id, end_date, start_date),
        )
        overlap = cur.fetchone()
        if overlap and (str(overlap["start_date"]), str(overlap["end_date"]), overlap["leave_type_id"]) == (str(start_date), str(end_date), leave_type_id):
            return {"status": "exists", "leave": overlap}
        if overlap:
            return {"status": "refused", "reason": "overlaps another leave request", "leave": overlap}
        leave_request_id = new_id("lvr")
        cur.execute(
            """INSERT INTO leave_request (leave_request_id, hotel_id, staff_id, leave_type_id, start_date, end_date, status, requested_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, 'requested', %s)""",
            (leave_request_id, hotel_id, staff_id, leave_type_id, start_date, end_date, user_id),
        )
        return {"status": "created", "leave": _leave(cur, leave_request_id)}
    return _run(run)


def _set_leave_status(hotel_id, leave_request_id, user_id, status, from_statuses):
    def run(cur):
        cur.execute("SELECT status FROM leave_request WHERE hotel_id = %s AND leave_request_id = %s FOR UPDATE", (hotel_id, leave_request_id))
        current = cur.fetchone()
        if current is None:
            return {"status": "not_found"}
        if current["status"] == status:
            return {"status": "unchanged", "leave": _leave(cur, leave_request_id)}
        if current["status"] not in from_statuses:
            return {"status": "refused", "reason": "leave is " + current["status"], "leave": _leave(cur, leave_request_id)}
        decided = status in ("approved", "rejected")
        cur.execute(
            """UPDATE leave_request SET status = %s,
                   decided_by_user_id = CASE WHEN %s THEN %s ELSE decided_by_user_id END,
                   decided_at = CASE WHEN %s THEN now() ELSE decided_at END,
                   cancelled_by_user_id = CASE WHEN %s THEN cancelled_by_user_id ELSE %s END,
                   cancelled_at = CASE WHEN %s THEN cancelled_at ELSE now() END
               WHERE leave_request_id = %s""",
            (status, decided, user_id, decided, decided, user_id, decided, leave_request_id),
        )
        leave = _leave(cur, leave_request_id)
        cur.execute(
            ROSTER_SELECT + " WHERE r.staff_id = %s AND r.work_date BETWEEN %s AND %s AND %s ORDER BY r.work_date",
            (leave["staff_id"], leave["start_date"], leave["end_date"], status == "approved"),
        )
        return {"status": "updated", "leave": leave, "rostered_during_leave": cur.fetchall()}
    return _run(run)


def approve_leave(hotel_id, leave_request_id, user_id):
    """Rosters inside the approved dates are listed in rostered_during_leave for the manager to remove or reassign."""
    return _set_leave_status(hotel_id, leave_request_id, user_id, "approved", ("requested",))


def reject_leave(hotel_id, leave_request_id, user_id):
    return _set_leave_status(hotel_id, leave_request_id, user_id, "rejected", ("requested",))


def cancel_leave(hotel_id, leave_request_id, user_id=None):
    return _set_leave_status(hotel_id, leave_request_id, user_id, "cancelled", ("requested", "approved"))


def list_leave(hotel_id, status=None, staff_id=None, date_from=None, date_to=None):
    def run(cur):
        cur.execute(
            LEAVE_SELECT + """ WHERE l.hotel_id = %s AND (%s::varchar IS NULL OR l.status = %s) AND (%s::varchar IS NULL OR l.staff_id = %s)
              AND (%s::date IS NULL OR l.end_date >= %s) AND (%s::date IS NULL OR l.start_date <= %s) ORDER BY l.start_date, s.staff_name""",
            (hotel_id, status, status, staff_id, staff_id, date_from, date_from, date_to, date_to),
        )
        return {"status": "ok", "leave": cur.fetchall()}
    return _run(run)


# Staff lookup for housekeeping, maintenance and task assignment

AVAILABLE_SELECT = """
    SELECT s.staff_id, s.staff_name, p.position_name, r.roster_id, r.work_date, f.shift_name, f.starts_at, f.ends_at,
           EXISTS (SELECT 1 FROM staff_attendance a WHERE a.staff_id = s.staff_id AND a.clock_out IS NULL) AS on_duty
    FROM roster r
    JOIN staff s ON s.staff_id = r.staff_id AND s.status = 'active'
    JOIN shift f ON f.shift_id = r.shift_id
    LEFT JOIN staff_position p ON p.position_id = s.position_id
    WHERE r.hotel_id = %s AND r.department_id = %s AND r.work_date = %s
      AND NOT EXISTS (SELECT 1 FROM leave_request l WHERE l.staff_id = s.staff_id AND l.status = 'approved' AND r.work_date BETWEEN l.start_date AND l.end_date)
"""


def available_staff(hotel_id, department_id, work_date):
    """Staff who may take a task of this department on this date: active, rostered in the department, not on approved leave."""
    def run(cur):
        cur.execute(AVAILABLE_SELECT + " ORDER BY on_duty DESC, f.starts_at, s.staff_name", (hotel_id, department_id, work_date))
        return {"status": "ok", "staff": cur.fetchall()}
    return _run(run)


def check_assignable(hotel_id, staff_id, department_id, work_date):
    """{'assignable': True, 'roster': ...} or {'assignable': False, 'reason': ...}. No other staff member is picked."""
    def run(cur):
        cur.execute("SELECT status FROM staff WHERE hotel_id = %s AND staff_id = %s", (hotel_id, staff_id))
        staff = cur.fetchone()
        if staff is None:
            return {"status": "not_found", "assignable": False, "reason": "unknown staff"}
        if staff["status"] != "active":
            return {"status": "ok", "assignable": False, "reason": "staff is " + staff["status"]}
        cur.execute(
            "SELECT leave_request_id FROM leave_request WHERE staff_id = %s AND status = 'approved' AND %s::date BETWEEN start_date AND end_date",
            (staff_id, work_date),
        )
        if cur.fetchone():
            return {"status": "ok", "assignable": False, "reason": "on approved leave"}
        cur.execute(AVAILABLE_SELECT + " AND s.staff_id = %s", (hotel_id, department_id, work_date, staff_id))
        roster = cur.fetchall()
        if not roster:
            return {"status": "ok", "assignable": False, "reason": "not rostered in this department on " + str(work_date)}
        return {"status": "ok", "assignable": True, "roster": roster}
    return _run(run)


def on_duty_staff(hotel_id, department_id=None):
    """Staff clocked in now, with the department of their roster (NULL when unrostered)."""
    def run(cur):
        cur.execute(
            ATTENDANCE_SELECT + " WHERE a.hotel_id = %s AND a.clock_out IS NULL AND (%s::varchar IS NULL OR r.department_id = %s) ORDER BY s.staff_name",
            (hotel_id, department_id, department_id),
        )
        return {"status": "ok", "staff": cur.fetchall()}
    return _run(run)
