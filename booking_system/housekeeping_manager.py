"""Housekeeping: room readiness (room_readiness_status), cleaning tasks (cleaning_task), and staff duty through HR attendance.
Readiness: clean | dirty | cleaning | inspected. Task status: pending | assigned | in_progress | completed | inspected | cancelled."""

import psycopg2

from booking_system import core_refs, db, staff_manager
from booking_system.content_store import client
from booking_system.hotel_registry import new_id
from booking_system.hr import service as hr

OPEN_TASK = ["pending", "assigned", "in_progress"]
# The housekeeping board calls the inspected column quality_check.
TASK_STATUS = {"quality_check": "inspected"}
# Readiness a room takes when its cleaning task reaches the status; other statuses leave readiness as it is.
READINESS_AFTER = {"in_progress": "cleaning", "completed": "clean", "inspected": "inspected"}
ACTIVITY_COLOR = {"pending": "bg-gray-400", "assigned": "bg-blue-400", "in_progress": "bg-yellow-400", "completed": "bg-green-500",
                  "inspected": "bg-indigo-500", "cancelled": "bg-gray-400"}
HR_DONE = ("created", "exists", "updated")
BOARD_LIMIT = 200

notes_col = client["leon_hotel_memory"]["cleaning_task_notes"]

# One row per cleaning task, plus a 'dirty' row for each dirty room without an open task.
BOARD = """
    SELECT * FROM (
        SELECT t.cleaning_task_id AS cleaning_id, r.room_number, t.task_status AS status, t.task_type, t.priority,
               t.assigned_staff_id AS staff_id, s.staff_name AS assigned_to, t.assigned_at, t.started_at, t.completed_at, t.inspected_at,
               GREATEST(t.created_at, t.assigned_at, t.started_at, t.completed_at, t.inspected_at) AS changed_at
        FROM cleaning_task t JOIN room r ON r.room_id = t.room_id LEFT JOIN staff s ON s.staff_id = t.assigned_staff_id
        WHERE t.hotel_id = %(hotel_id)s
        UNION ALL
        SELECT NULL, r.room_number, 'dirty', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, rs.changed_at
        FROM room_readiness_status rs JOIN room r ON r.room_id = rs.room_id
        WHERE rs.hotel_id = %(hotel_id)s AND rs.readiness_status = 'dirty'
          AND NOT EXISTS (SELECT 1 FROM cleaning_task o WHERE o.room_id = rs.room_id AND o.task_status = ANY(%(open)s))
    ) b
"""


def _run(fn):
    try:
        return db.transaction(fn)
    except (psycopg2.IntegrityError, psycopg2.DataError) as error:
        return {"error": error.diag.message_primary}


def set_readiness(cur, hotel_id, user_id, room_id, status):
    """READ then UPDATE the room's readiness row (created with the room). clean_since starts when the room becomes clean, is kept
    through inspection, and clears while it is dirty or being cleaned."""
    cur.execute("SELECT readiness_status FROM room_readiness_status WHERE hotel_id = %s AND room_id = %s FOR UPDATE", (hotel_id, room_id))
    current = cur.fetchone()
    if current is None:
        return {"error": "room " + room_id + " has no readiness row"}
    if current["readiness_status"] == status:
        return {"room_id": room_id, "readiness_status": status, "status": "unchanged"}
    cur.execute(
        """UPDATE room_readiness_status SET readiness_status = %s, changed_at = now(), changed_by_user_id = %s,
                  clean_since = CASE %s WHEN 'clean' THEN now() WHEN 'inspected' THEN COALESCE(clean_since, now()) END
           WHERE room_id = %s""",
        (status, user_id, status, room_id),
    )
    return {"room_id": room_id, "readiness_status": status, "previous_status": current["readiness_status"], "status": "updated"}


def append_note(cur, collection, hotel_id, user_id, task_id, status, fields):
    """Free text about a task lives in one document per task (keyed by task_id), appended in the caller's transaction.
    fields: {name: text}; empty texts are dropped and nothing is written when none remain."""
    texts = {name: str(text).strip() for name, text in fields.items() if str(text or "").strip()}
    if not texts:
        return
    current = collection.find_one({"task_id": task_id}) or {"task_id": task_id, "hotel_id": hotel_id, "notes": []}
    cur.execute("SELECT now()::text AS at")
    entry = dict(texts, at=cur.fetchone()["at"], user_id=user_id, status=status)
    collection.write_in(cur, "task_id=" + task_id, dict(current, notes=current["notes"] + [entry]))


def notes_by_task(collection, hotel_id):
    """{task_id: [note, ...]} for the hotel (one read of the collection)."""
    return {doc["task_id"]: doc["notes"] for doc in collection.find({"hotel_id": hotel_id})}


def _note(cur, hotel_id, user_id, task_id, status, text):
    append_note(cur, notes_col, hotel_id, user_id, task_id, status, {"text": text})


def _follow_readiness(cur, hotel_id, user_id, room_id, task_status):
    readiness = READINESS_AFTER.get(task_status)
    return set_readiness(cur, hotel_id, user_id, room_id, readiness) if readiness else None


def _create_task(cur, hotel_id, user_id, room_number, staff, task_type, priority, task_status, notes):
    room = core_refs.room(cur, hotel_id, room_number)
    if room is None:
        return {"error": "room " + str(room_number) + " not found"}
    person, reason = staff_manager.assignable_staff(cur, hotel_id, staff, "HOUSEKEEPING")
    if person is None:
        return {"error": reason}
    cur.execute("SELECT cleaning_task_id, task_status FROM cleaning_task WHERE room_id = %s AND task_status = ANY(%s) FOR UPDATE",
                (room["room_id"], OPEN_TASK))
    open_task = cur.fetchone()
    if open_task:
        return {"error": "room " + str(room_number).upper() + " already has a " + open_task["task_status"] + " cleaning task",
                "cleaning_id": open_task["cleaning_task_id"]}
    task_id = new_id("cln")
    cur.execute(
        """INSERT INTO cleaning_task (cleaning_task_id, hotel_id, room_id, task_status, task_type, priority, assigned_staff_id,
                                      created_by_user_id, assigned_at, started_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), CASE WHEN %s = 'in_progress' THEN now() END)""",
        (task_id, hotel_id, room["room_id"], task_status, str(task_type).strip().lower(), str(priority).strip().lower(),
         person["staff_id"], user_id, task_status),
    )
    _note(cur, hotel_id, user_id, task_id, task_status, notes)
    return {"cleaning_id": task_id, "room_number": str(room_number).upper(), "status": task_status, "staff_id": person["staff_id"],
            "assigned_to": person["staff_name"], "readiness": _follow_readiness(cur, hotel_id, user_id, room["room_id"], task_status)}


def create_cleaning_task(hotel_id, user_id, room_number, staff, task_type="standard_cleaning", priority="normal", notes=""):
    """CREATE an assigned task for a room with no open task. staff: staff_id, phone, email or exact name."""
    return _run(lambda cur: _create_task(cur, hotel_id, user_id, room_number, staff, task_type, priority, "assigned", notes))


def start_cleaning_for_room(hotel_id, user_id, room_number, staff, task_type="standard_cleaning", priority="normal", notes=""):
    """CREATE a task already in progress; the room becomes 'cleaning'."""
    return _run(lambda cur: _create_task(cur, hotel_id, user_id, room_number, staff, task_type, priority, "in_progress", notes))


def task_status(status):
    text = str(status or "").strip().lower()
    return TASK_STATUS.get(text, text)


def update_cleaning_progress(hotel_id, user_id, cleaning_id, new_status, staff=None, notes=""):
    """READ the task, then UPDATE its status (and assignee when staff is given). Readiness follows READINESS_AFTER."""
    status = task_status(new_status)

    def run(cur):
        cur.execute(
            """SELECT t.*, r.room_number FROM cleaning_task t JOIN room r ON r.room_id = t.room_id
               WHERE t.hotel_id = %s AND t.cleaning_task_id = %s FOR UPDATE OF t""",
            (hotel_id, cleaning_id),
        )
        task = cur.fetchone()
        if task is None:
            return {"error": "cleaning task " + str(cleaning_id) + " not found"}
        staff_id = task["assigned_staff_id"]
        if str(staff or "").strip():
            person, reason = staff_manager.assignable_staff(cur, hotel_id, staff, "HOUSEKEEPING")
            if person is None:
                return {"error": reason}
            staff_id = person["staff_id"]
        result = {"cleaning_id": cleaning_id, "room_number": task["room_number"], "previous_status": task["task_status"], "status": status}
        _note(cur, hotel_id, user_id, cleaning_id, status, notes)
        if (task["task_status"], task["assigned_staff_id"]) == (status, staff_id):
            return dict(result, result="unchanged")
        cur.execute(
            """UPDATE cleaning_task SET task_status = %(status)s, assigned_staff_id = %(staff)s,
                      assigned_at = CASE WHEN assigned_staff_id IS DISTINCT FROM %(staff)s THEN now() ELSE assigned_at END,
                      started_at = CASE WHEN %(status)s = 'in_progress' THEN COALESCE(started_at, now()) ELSE started_at END,
                      completed_at = CASE WHEN %(status)s IN ('completed', 'inspected') THEN COALESCE(completed_at, now()) ELSE completed_at END,
                      inspected_at = CASE WHEN %(status)s = 'inspected' THEN now() ELSE inspected_at END,
                      inspected_by_user_id = CASE WHEN %(status)s = 'inspected' THEN %(user)s ELSE inspected_by_user_id END
               WHERE cleaning_task_id = %(task)s""",
            {"status": status, "staff": staff_id, "user": user_id, "task": cleaning_id},
        )
        return dict(result, result="updated", staff_id=staff_id,
                    readiness=_follow_readiness(cur, hotel_id, user_id, task["room_id"], status))
    return _run(run)


# ---------- reads ----------

def get_cleaning_items_by_status_json(hotel_id, status):
    """Board items in one column: a task status, or 'dirty' (dirty rooms without an open task)."""
    return {"items": db.rows(BOARD + " WHERE b.status = %(status)s ORDER BY b.changed_at LIMIT %(limit)s",
                             {"hotel_id": hotel_id, "open": OPEN_TASK, "status": task_status(status), "limit": BOARD_LIMIT})}


def get_board(hotel_id):
    rooms = db.rows(BOARD + " ORDER BY b.changed_at DESC LIMIT %(limit)s", {"hotel_id": hotel_id, "open": OPEN_TASK, "limit": BOARD_LIMIT})
    return {"rooms": rooms, "staff": get_staff_workload_json(hotel_id)["staff"], "activity": get_activity_json(hotel_id)["activity"]}


def get_kpis(hotel_id):
    """Room counts by readiness ('ready' is clean)."""
    return db.row(
        """SELECT COUNT(*) FILTER (WHERE readiness_status = 'dirty') AS dirty, COUNT(*) FILTER (WHERE readiness_status = 'cleaning') AS cleaning,
                  COUNT(*) FILTER (WHERE readiness_status = 'inspected') AS inspected, COUNT(*) FILTER (WHERE readiness_status = 'clean') AS ready
           FROM room_readiness_status WHERE hotel_id = %s""",
        (hotel_id,),
    )


def read_dirty_rooms(hotel_id):
    return {"rooms": db.rows(
        """SELECT r.room_number, t.room_type_name, rs.readiness_status AS status, rs.changed_at, a.user_name AS changed_by
           FROM room_readiness_status rs JOIN room r ON r.room_id = rs.room_id JOIN room_type t ON t.room_type_id = r.room_type_id
           LEFT JOIN user_account a ON a.user_id = rs.changed_by_user_id
           WHERE rs.hotel_id = %s AND rs.readiness_status = 'dirty' ORDER BY rs.changed_at""",
        (hotel_id,),
    )}


def get_staff_workload_json(hotel_id):
    """Open and finished task counts per assigned staff member."""
    return {"staff": db.rows(
        """SELECT s.staff_id, s.staff_name AS name,
                  (SELECT i.identity_value FROM staff_identity i WHERE i.staff_id = s.staff_id AND i.identity_type = 'phone'
                   ORDER BY i.identity_value LIMIT 1) AS phone,
                  COUNT(*) FILTER (WHERE t.task_status = ANY(%s)) AS tasks,
                  COUNT(*) FILTER (WHERE t.task_status IN ('completed', 'inspected')) AS completed
           FROM cleaning_task t JOIN staff s ON s.staff_id = t.assigned_staff_id
           WHERE t.hotel_id = %s GROUP BY s.staff_id ORDER BY tasks DESC, s.staff_name""",
        (OPEN_TASK, hotel_id),
    )}


def get_activity_json(hotel_id, limit=30):
    """Latest task changes, newest first."""
    rows = db.rows(BOARD + " WHERE b.cleaning_id IS NOT NULL ORDER BY b.changed_at DESC LIMIT %(limit)s",
                   {"hotel_id": hotel_id, "open": OPEN_TASK, "limit": int(limit)})
    return {"activity": [{"color": ACTIVITY_COLOR[row["status"]], "time": row["changed_at"], "cleaning_id": row["cleaning_id"],
                          "text": "Room " + row["room_number"] + " " + row["status"].replace("_", " ") + " by " + (row["assigned_to"] or "unassigned")}
                         for row in rows]}


def read_staff_current_cleaning_status(hotel_id, staff, room_number=None):
    """The staff member's tasks, newest first; optionally for one room."""
    def run(cur):
        person, reason = staff_manager.one_staff(cur, hotel_id, staff)
        if person is None:
            return {"error": reason}
        cur.execute(BOARD + " WHERE b.staff_id = %(staff)s AND (%(room)s::text IS NULL OR b.room_number = upper(%(room)s)) ORDER BY b.changed_at DESC LIMIT 20",
                    {"hotel_id": hotel_id, "open": OPEN_TASK, "staff": person["staff_id"], "room": room_number})
        return {"staff_id": person["staff_id"], "staff_name": person["staff_name"], "tasks": cur.fetchall()}
    return _run(run)


def read_room_current_cleaning(hotel_id, room_number):
    """The room's readiness and its latest cleaning task."""
    def run(cur):
        room = core_refs.room(cur, hotel_id, room_number)
        if room is None:
            return {"error": "room " + str(room_number) + " not found"}
        cur.execute("SELECT readiness_status, changed_at, clean_since, auto_dirty_days FROM room_readiness_status WHERE room_id = %s",
                    (room["room_id"],))
        readiness = cur.fetchone()
        cur.execute(BOARD + " WHERE b.cleaning_id IS NOT NULL AND b.room_number = %(room)s ORDER BY b.changed_at DESC LIMIT 1",
                    {"hotel_id": hotel_id, "open": OPEN_TASK, "room": str(room_number).strip().upper()})
        return {"room_number": str(room_number).strip().upper(), "readiness": readiness, "task": cur.fetchone()}
    return _run(run)


def get_housekeeping_analytics(hotel_id, date_from, date_to):
    """Tasks created between the hotel-local dates: totals, per staff member and per task type."""
    scope = """FROM cleaning_task t JOIN hotel h ON h.hotel_id = t.hotel_id LEFT JOIN staff s ON s.staff_id = t.assigned_staff_id
               WHERE t.hotel_id = %s AND (t.created_at AT TIME ZONE h.timezone)::date BETWEEN %s AND %s"""
    measures = """COUNT(*) AS tasks, COUNT(*) FILTER (WHERE t.task_status IN ('completed', 'inspected')) AS completed,
                  COUNT(*) FILTER (WHERE t.task_status = 'inspected') AS inspected, COUNT(*) FILTER (WHERE t.task_status = 'cancelled') AS cancelled,
                  round(AVG(extract(epoch FROM t.completed_at - t.started_at) / 60)::numeric, 1) AS avg_minutes"""
    params = (hotel_id, date_from, date_to)

    def run(cur):
        cur.execute("SELECT " + measures + " " + scope, params)
        summary = cur.fetchone()
        cur.execute("SELECT s.staff_id, s.staff_name, " + measures + " " + scope + " GROUP BY s.staff_id, s.staff_name ORDER BY completed DESC", params)
        by_staff = cur.fetchall()
        cur.execute("SELECT t.task_type, " + measures + " " + scope + " GROUP BY t.task_type ORDER BY tasks DESC", params)
        return {"date_from": str(date_from), "date_to": str(date_to), "summary": summary, "by_staff": by_staff, "by_task_type": cur.fetchall()}
    return _run(run)


# ---------- staff duty (HR attendance) ----------

def _hr(result):
    if result.get("status") in HR_DONE:
        return result
    return {"error": "HR did not record it (" + str(result.get("status")) + ")", "reason": result.get("reason"), "hr": result}


def _staff_id(hotel_id, staff):
    return db.transaction(lambda cur: staff_manager.one_staff(cur, hotel_id, staff))


def staff_clock_in(hotel_id, user_id, staff):
    person, reason = _staff_id(hotel_id, staff)
    if person is None:
        return {"error": reason}
    return _hr(hr.clock_in(hotel_id, person["staff_id"], user_id))


def staff_clock_out(hotel_id, user_id, staff):
    """HR clock-out, then the staff member's open tasks go back to pending, unassigned."""
    person, reason = _staff_id(hotel_id, staff)
    if person is None:
        return {"error": reason}
    result = _hr(hr.clock_out(hotel_id, person["staff_id"], user_id))
    if "error" in result:
        return result
    released = db.rows(
        """UPDATE cleaning_task SET task_status = 'pending', assigned_staff_id = NULL, assigned_at = NULL
           WHERE hotel_id = %s AND assigned_staff_id = %s AND task_status IN ('assigned', 'in_progress') RETURNING cleaning_task_id""",
        (hotel_id, person["staff_id"]),
    )
    return dict(result, unassigned=[row["cleaning_task_id"] for row in released])


def get_on_duty_staff_json(hotel_id):
    """Clocked-in staff rostered in HOUSEKEEPING."""
    return staff_manager.on_duty_staff(hotel_id, "HOUSEKEEPING")
