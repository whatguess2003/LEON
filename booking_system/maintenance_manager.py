"""Maintenance: requests (maintenance_request, a room or an area), preventive schedules (maintenance_schedule) and room blocks
(room_block: out_of_order | out_of_service). Request status: reported | assigned | in_progress | completed | cancelled.
Urgency: low | normal | high | urgent."""

from datetime import date, timedelta

import psycopg2

from booking_system import core_refs, db, staff_manager
from booking_system.content_store import client
from booking_system.hotel_registry import new_id
from booking_system.housekeeping_manager import append_note, notes_by_task

LIVE_NIGHT = ["booked", "checked_in"]
OPEN_REQUEST = ["reported", "assigned", "in_progress"]
ONE_DAY = timedelta(days=1)

notes_col = client["leon_hotel_memory"]["maintenance_request_notes"]

REQUEST_SELECT = """
    SELECT m.request_id, r.room_number, m.area, m.issue, m.urgency, m.request_status, m.schedule_id, m.due_date, m.reported_at,
           u.user_name AS reported_by, m.assigned_staff_id, s.staff_name AS assigned_to, m.assigned_at, m.started_at, m.completed_at,
           m.actual_cost
    FROM maintenance_request m LEFT JOIN room r ON r.room_id = m.room_id LEFT JOIN staff s ON s.staff_id = m.assigned_staff_id
    LEFT JOIN user_account u ON u.user_id = m.reported_by_user_id
"""
SCHEDULE_SELECT = """
    SELECT m.schedule_id, CASE WHEN m.room_id IS NULL THEN 'AREA' ELSE 'ROOM' END AS target_type, m.room_id, r.room_number, m.area,
           m.issue, upper(m.urgency) AS urgency, m.interval_days, m.start_date, m.last_service_date, m.next_due_date,
           m.auto_out_of_order, m.active
    FROM maintenance_schedule m LEFT JOIN room r ON r.room_id = m.room_id
"""


def _run(fn):
    try:
        return db.transaction(fn)
    except (psycopg2.IntegrityError, psycopg2.DataError) as error:
        return {"error": error.diag.message_primary}
    except ValueError as error:
        return {"error": str(error)}


def _date(value):
    return date.fromisoformat(str(value).strip()[:10])


def _target(cur, hotel_id, room_number, area):
    """(room_id, area, '') for exactly one of a room number or an area name, else (None, None, reason)."""
    room_text, area_text = str(room_number or "").strip(), str(area or "").strip().upper()
    if bool(room_text) == bool(area_text):
        return None, None, "give exactly one of room_number or area"
    room = core_refs.room(cur, hotel_id, room_text) if room_text else {"room_id": None}
    if room is None:
        return None, None, "room " + room_text.upper() + " not found"
    return room["room_id"], area_text or None, ""


# ---------- requests ----------

def create_maintenance_request(hotel_id, user_id, room_number=None, area=None, issue="", urgency="normal"):
    """READ first: the same open issue on the same room/area is returned as exists; else CREATE a reported request."""
    text = str(issue or "").strip()

    def run(cur):
        room_id, area_name, reason = _target(cur, hotel_id, room_number, area)
        if reason:
            return {"error": reason}
        if not text:
            return {"error": "issue is required"}
        cur.execute(
            REQUEST_SELECT + """ WHERE m.hotel_id = %s AND m.room_id IS NOT DISTINCT FROM %s AND m.area IS NOT DISTINCT FROM %s
                  AND upper(m.issue) = upper(%s) AND m.request_status = ANY(%s)""",
            (hotel_id, room_id, area_name, text, OPEN_REQUEST),
        )
        found = cur.fetchone()
        if found:
            return {"status": "exists", "request": found}
        request_id = new_id("mtr")
        cur.execute(
            """INSERT INTO maintenance_request (request_id, hotel_id, room_id, area, issue, urgency, request_status, reported_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, 'reported', %s)""",
            (request_id, hotel_id, room_id, area_name, text, str(urgency or "").strip().lower(), user_id),
        )
        cur.execute(REQUEST_SELECT + " WHERE m.request_id = %s", (request_id,))
        return {"status": "created", "request": cur.fetchone()}
    return _run(run)


def update_maintenance_progress(hotel_id, user_id, request_id, status, staff=None, progress_notes="", resolution_description="",
                                parts_used="", actual_cost=None):
    """READ the request, then UPDATE status / assignee (staff: staff_id, phone, email or exact name) / cost. Timestamps are set
    when the request first reaches each step; notes, resolution and parts go to its notes document."""
    new_status = str(status or "").strip().lower()

    def run(cur):
        cur.execute("SELECT * FROM maintenance_request WHERE hotel_id = %s AND request_id = %s FOR UPDATE", (hotel_id, request_id))
        current = cur.fetchone()
        if current is None:
            return {"error": "maintenance request " + str(request_id) + " not found"}
        staff_id = current["assigned_staff_id"]
        if str(staff or "").strip():
            person, reason = staff_manager.assignable_staff(cur, hotel_id, staff, "MAINTENANCE")
            if person is None:
                return {"error": reason}
            staff_id = person["staff_id"]
        if new_status == "assigned" and staff_id is None:
            return {"error": "an assigned request needs staff"}
        cur.execute(
            """UPDATE maintenance_request SET request_status = %(status)s, assigned_staff_id = %(staff)s,
                      assigned_at = CASE WHEN assigned_staff_id IS DISTINCT FROM %(staff)s THEN now() ELSE assigned_at END,
                      started_at = CASE WHEN %(status)s = 'in_progress' THEN COALESCE(started_at, now()) ELSE started_at END,
                      completed_at = CASE WHEN %(status)s = 'completed' THEN COALESCE(completed_at, now()) ELSE completed_at END,
                      actual_cost = COALESCE(%(cost)s, actual_cost)
               WHERE request_id = %(id)s""",
            {"status": new_status, "staff": staff_id, "cost": actual_cost, "id": request_id},
        )
        append_note(cur, notes_col, hotel_id, user_id, request_id, new_status,
                    {"progress_notes": progress_notes, "resolution_description": resolution_description, "parts_used": parts_used})
        cur.execute(REQUEST_SELECT + " WHERE m.request_id = %s", (request_id,))
        return {"status": "updated", "previous_status": current["request_status"], "request": cur.fetchone()}
    return _run(run)


def read_maintenance_requests(hotel_id, status_filter="", urgency_filter="", room_number="", assigned_to="", limit=50):
    """Requests newest first with their notes. Empty filters match everything; assigned_to is a staff_id or exact staff name."""
    rows = db.rows(
        REQUEST_SELECT + """ WHERE m.hotel_id = %(hotel)s
              AND (%(status)s = '' OR m.request_status = %(status)s) AND (%(urgency)s = '' OR m.urgency = %(urgency)s)
              AND (%(room)s = '' OR r.room_number = %(room)s) AND (%(staff)s = '' OR m.assigned_staff_id = %(staff)s OR s.staff_name = upper(%(staff)s))
            ORDER BY m.reported_at DESC LIMIT %(limit)s""",
        {"hotel": hotel_id, "status": str(status_filter or "").strip().lower(), "urgency": str(urgency_filter or "").strip().lower(),
         "room": str(room_number or "").strip().upper(), "staff": str(assigned_to or "").strip(), "limit": int(limit)},
    )
    notes = notes_by_task(notes_col, hotel_id)
    return {"items": [dict(row, notes=notes.get(row["request_id"], [])) for row in rows]}


# ---------- preventive schedules ----------

def read_scheduled_maintenance(hotel_id, target_type="", room_number="", area="", active_only=True, limit=100):
    """Schedules by next due date. target_type ROOM | AREA; empty filters match everything."""
    return {"items": db.rows(
        "SELECT * FROM (" + SCHEDULE_SELECT + """ WHERE m.hotel_id = %(hotel)s) x
            WHERE (%(type)s = '' OR x.target_type = %(type)s) AND (%(room)s = '' OR x.room_number = %(room)s)
              AND (%(area)s = '' OR x.area = %(area)s) AND (x.active OR NOT %(active_only)s)
            ORDER BY x.next_due_date LIMIT %(limit)s""",
        {"hotel": hotel_id, "type": str(target_type or "").strip().upper(), "room": str(room_number or "").strip().upper(),
         "area": str(area or "").strip().upper(), "active_only": bool(active_only), "limit": int(limit)},
    )}


def setup_scheduled_maintenance(hotel_id, user_id, room_number=None, area=None, issue="", urgency="normal", interval_days=90,
                                start_date=None, auto_out_of_order=False, active=True):
    """READ first: the schedule for the same room/area and issue is updated (see update_scheduled_maintenance); else CREATE one
    whose first due date is start_date."""
    text = str(issue or "").strip().upper()

    def run(cur):
        room_id, area_name, reason = _target(cur, hotel_id, room_number, area)
        if reason:
            return {"error": reason}
        if not text:
            return {"error": "issue is required"}
        cur.execute(
            """SELECT schedule_id FROM maintenance_schedule WHERE hotel_id = %s AND room_id IS NOT DISTINCT FROM %s
                  AND area IS NOT DISTINCT FROM %s AND issue = %s""",
            (hotel_id, room_id, area_name, text),
        )
        found = cur.fetchone()
        if found:
            return _update_schedule(cur, hotel_id, found["schedule_id"], {"urgency": urgency, "interval_days": interval_days,
                                    "start_date": start_date, "auto_out_of_order": auto_out_of_order, "active": active})
        schedule_id = new_id("mts")
        first_due = _date(start_date)
        cur.execute(
            """INSERT INTO maintenance_schedule (schedule_id, hotel_id, room_id, area, issue, urgency, interval_days, start_date,
                                                 next_due_date, auto_out_of_order, active)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (schedule_id, hotel_id, room_id, area_name, text, str(urgency or "").strip().lower(), int(interval_days), first_due, first_due,
             bool(auto_out_of_order), bool(active)),
        )
        cur.execute(SCHEDULE_SELECT + " WHERE m.schedule_id = %s", (schedule_id,))
        return {"status": "created", "schedule": cur.fetchone()}
    return _run(run)


def _update_schedule(cur, hotel_id, schedule_id, given, target=None):
    """given: column -> new value (None = keep). A new start_date also becomes the next due date. target: (room_id, area)."""
    cur.execute("SELECT * FROM maintenance_schedule WHERE hotel_id = %s AND schedule_id = %s FOR UPDATE", (hotel_id, schedule_id))
    current = cur.fetchone()
    if current is None:
        return {"error": "schedule " + str(schedule_id) + " not found"}
    clean = {"issue": lambda v: str(v).strip().upper(), "urgency": lambda v: str(v).strip().lower(), "interval_days": int,
             "start_date": _date, "auto_out_of_order": bool, "active": bool}
    fields = {name: clean[name](value) for name, value in given.items() if value is not None}
    fields.update(dict(zip(("room_id", "area"), target or ())))
    changed = {name: value for name, value in fields.items() if current[name] != value}
    if "start_date" in changed:
        changed["next_due_date"] = changed["start_date"]
    if changed:
        cur.execute("UPDATE maintenance_schedule SET " + ", ".join(name + " = %s" for name in changed) + " WHERE schedule_id = %s",
                    tuple(changed.values()) + (schedule_id,))
    cur.execute(SCHEDULE_SELECT + " WHERE m.schedule_id = %s", (schedule_id,))
    return {"status": "updated" if changed else "unchanged", "changed": sorted(changed), "schedule": cur.fetchone()}


def update_scheduled_maintenance(hotel_id, user_id, schedule_id, room_number=None, area=None, issue=None, urgency=None,
                                 interval_days=None, start_date=None, auto_out_of_order=None, active=None):
    """UPDATE the given fields of a schedule. A room number or an area (not both) re-targets it."""
    def run(cur):
        target = None
        if str(room_number or "").strip() or str(area or "").strip():
            room_id, area_name, reason = _target(cur, hotel_id, room_number, area)
            if reason:
                return {"error": reason}
            target = (room_id, area_name)
        return _update_schedule(cur, hotel_id, schedule_id, {"issue": issue, "urgency": urgency, "interval_days": interval_days,
                                "start_date": start_date, "auto_out_of_order": auto_out_of_order, "active": active}, target)
    return _run(run)


def run_daily_pm_generation(hotel_id, today):
    """One request per (schedule, due date) for this hotel's active schedules due on or before today (hotel-local date); the
    maintenance_request_due_uq index makes a rerun a no-op. Each schedule then moves to its first due date after today.
    A newly generated request of an auto_out_of_order room schedule blocks the room for today unless a live booking night holds it."""
    day = _date(today)

    def run(cur):
        cur.execute("SELECT * FROM maintenance_schedule WHERE hotel_id = %s AND active AND next_due_date <= %s ORDER BY next_due_date FOR UPDATE",
                    (hotel_id, day))
        report = {"created": [], "existing": [], "blocked": [], "block_refused": []}
        for schedule in cur.fetchall():
            cur.execute(
                """INSERT INTO maintenance_request (request_id, hotel_id, room_id, area, issue, urgency, request_status, schedule_id, due_date)
                   VALUES (%s, %s, %s, %s, %s, %s, 'reported', %s, %s)
                   ON CONFLICT (schedule_id, due_date) WHERE schedule_id IS NOT NULL DO NOTHING RETURNING request_id""",
                (new_id("mtr"), hotel_id, schedule["room_id"], schedule["area"], schedule["issue"], schedule["urgency"],
                 schedule["schedule_id"], schedule["next_due_date"]),
            )
            created = cur.fetchone()
            report["created" if created else "existing"].append({"schedule_id": schedule["schedule_id"], "due_date": schedule["next_due_date"]})
            cur.execute(
                """UPDATE maintenance_schedule SET last_service_date = next_due_date,
                          next_due_date = next_due_date + interval_days * ((%s::date - next_due_date) / interval_days + 1)
                   WHERE schedule_id = %s""",
                (day, schedule["schedule_id"]),
            )
            if created and schedule["auto_out_of_order"] and schedule["room_id"]:
                block = _block(cur, hotel_id, None, schedule["room_id"], day, day, "Scheduled maintenance: " + schedule["issue"], "out_of_order")
                report["block_refused" if "error" in block else "blocked"].append(dict(block, schedule_id=schedule["schedule_id"]))
        return report
    return _run(run)


# ---------- room blocks ----------

def _block(cur, hotel_id, user_id, room_id, start, end, reason, block_type):
    """Refused while a live booking night uses the room in [start, end] or another block overlaps it; a block of the same type
    that already covers the range is returned as exists."""
    if end < start:
        return {"error": "end_date is before start_date"}
    cur.execute("SELECT room_id FROM room WHERE room_id = %s FOR UPDATE", (room_id,))
    cur.execute(
        """SELECT stay_date, booking_id, night_status FROM booking_room_night
           WHERE room_id = %s AND stay_date BETWEEN %s AND %s AND night_status = ANY(%s) ORDER BY stay_date""",
        (room_id, start, end, LIVE_NIGHT),
    )
    nights = cur.fetchall()
    if nights:
        return {"error": "the room has live booking nights in the range", "nights": nights}
    cur.execute("SELECT * FROM room_block WHERE room_id = %s AND start_date <= %s AND end_date >= %s ORDER BY start_date", (room_id, end, start))
    overlapping = cur.fetchall()
    covering = [b for b in overlapping if b["block_type"] == block_type and b["start_date"] <= start and b["end_date"] >= end]
    if covering:
        return {"status": "exists", "block": covering[0]}
    if overlapping:
        return {"error": "the range overlaps another block", "blocks": overlapping}
    cur.execute(
        """INSERT INTO room_block (room_block_id, hotel_id, room_id, block_type, start_date, end_date, reason, created_by_user_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (new_id("rbk"), hotel_id, room_id, block_type, start, end, str(reason or "").strip(), user_id),
    )
    return {"status": "created", "block": cur.fetchone()}


def mark_out_of_order(hotel_id, user_id, room_number, start_date, end_date, reason, block_type="out_of_order"):
    """CREATE a room block for the dates (inclusive). block_type: out_of_order | out_of_service."""
    def run(cur):
        room = core_refs.room(cur, hotel_id, room_number)
        if room is None:
            return {"error": "room " + str(room_number) + " not found"}
        return dict(_block(cur, hotel_id, user_id, room["room_id"], _date(start_date), _date(end_date), reason, block_type),
                    room_number=str(room_number).strip().upper())
    return _run(run)


def mark_available(hotel_id, user_id, room_number, start_date, end_date):
    """Free the dates (inclusive): blocks inside the range are removed, blocks reaching past it keep their outside parts."""
    def run(cur):
        room = core_refs.room(cur, hotel_id, room_number)
        if room is None:
            return {"error": "room " + str(room_number) + " not found"}
        start, end = _date(start_date), _date(end_date)
        if end < start:
            return {"error": "end_date is before start_date"}
        cur.execute("SELECT * FROM room_block WHERE room_id = %s AND start_date <= %s AND end_date >= %s FOR UPDATE", (room["room_id"], end, start))
        blocks = cur.fetchall()
        if not blocks:
            return {"error": "room " + str(room_number).strip().upper() + " has no block between " + str(start) + " and " + str(end)}
        kept = []
        for block in blocks:
            cur.execute("DELETE FROM room_block WHERE room_block_id = %s", (block["room_block_id"],))
            for piece_start, piece_end in ((block["start_date"], start - ONE_DAY), (end + ONE_DAY, block["end_date"])):
                if piece_start <= piece_end:
                    cur.execute(
                        """INSERT INTO room_block (room_block_id, hotel_id, room_id, block_type, start_date, end_date, reason, created_by_user_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                        (new_id("rbk"), hotel_id, block["room_id"], block["block_type"], piece_start, piece_end, block["reason"],
                         block["created_by_user_id"]),
                    )
                    kept.append(cur.fetchone())
        return {"status": "updated", "room_number": str(room_number).strip().upper(), "removed": blocks, "kept": kept}
    return _run(run)


def read_room_blocks(hotel_id, room_number, date_from, date_to):
    """Blocks of a room overlapping the dates."""
    return {"blocks": db.rows(
        """SELECT b.* FROM room_block b JOIN room r ON r.room_id = b.room_id
           WHERE b.hotel_id = %s AND r.room_number = %s AND b.start_date <= %s AND b.end_date >= %s ORDER BY b.start_date""",
        (hotel_id, str(room_number or "").strip().upper(), date_to, date_from),
    )}
