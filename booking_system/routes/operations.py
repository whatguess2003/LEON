"""Housekeeping, maintenance, staff availability and room out-of-order routes. The hotel and actor come from the session;
a hotel_name the UI still sends is ignored."""

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from booking_system import housekeeping_manager as hk, maintenance_manager as mm, staff_manager
from booking_system.stay_rules import mark_stale_clean_rooms
from booking_system.access import session

router = APIRouter()

ACCESS = [
    ("POST /api/maintenance/pm_run_now", "maintenance", "operate"),
    ("GET /api/housekeeping/kpis", "housekeeping", "view"),
    ("GET /api/housekeeping/board", "housekeeping", "view"),
    ("GET /api/housekeeping/by_status", "housekeeping", "view"),
    ("GET /api/housekeeping/staff_workload", "housekeeping", "view"),
    ("GET /api/housekeeping/activity", "housekeeping", "view"),
    ("GET /api/housekeeping/on_duty", "housekeeping", "view"),
    ("POST /api/housekeeping/clock_in", "housekeeping", "operate"),
    ("POST /api/housekeeping/clock_out", "housekeeping", "operate"),
    ("POST /api/housekeeping/start_cleaning", "housekeeping", "operate"),
    ("GET /api/housekeeping/dirty", "housekeeping", "view"),
    ("GET /api/housekeeping/staff_status", "housekeeping", "view"),
    ("GET /api/housekeeping/room_status", "housekeeping", "view"),
    ("POST /api/housekeeping/create_task", "housekeeping", "operate"),
    ("POST /api/housekeeping/auto_dirty", "housekeeping", "operate"),
    ("POST /api/housekeeping/update_progress", "housekeeping", "operate"),
    ("GET /api/staff/available", "housekeeping", "view"),
    ("POST /api/maintenance/create", "maintenance", "operate"),
    ("GET /api/maintenance/list", "maintenance", "view"),
    ("POST /api/maintenance/update", "maintenance", "operate"),
    ("GET /api/maintenance/schedules/read", "maintenance", "view"),
    ("POST /api/maintenance/schedules/setup", "maintenance", "manage"),
    ("POST /api/maintenance/schedules/update", "maintenance", "manage"),
    ("POST /api/assignments/mark_out_of_order", "maintenance", "operate"),
    ("POST /api/assignments/mark_available", "maintenance", "operate"),
]


def _reply(data):
    return JSONResponse(content=jsonable_encoder(data), status_code=400 if "error" in data else 200)


def _result(data):
    """{'result': data}, or the manager's {'error': ...} as a 400."""
    return _reply(data if "error" in data else {"result": data})


def _today(s):
    return datetime.now(ZoneInfo(s["hotel_timezone"])).date()


# ---------- housekeeping ----------

class StaffDutyPayload(BaseModel):
    hotel_name: Optional[str] = None
    staff_phone: str
    shift_label: Optional[str] = None


class StartCleaningPayload(BaseModel):
    hotel_name: Optional[str] = None
    room_number: str
    staff_phone: str
    task_type: Optional[str] = "standard_cleaning"
    priority: Optional[str] = "normal"
    notes: Optional[str] = ""


class UpdateCleaningProgressPayload(BaseModel):
    hotel_name: Optional[str] = None
    cleaning_id: str
    new_status: str
    staff_phone: Optional[str] = None
    notes: Optional[str] = ""


@router.post("/api/maintenance/pm_run_now")
def pm_run_now(s: dict = Depends(session)):
    return _result(mm.run_daily_pm_generation(s["hotel_id"], _today(s)))


@router.get("/api/housekeeping/kpis")
def hk_kpis(s: dict = Depends(session)):
    return _reply(hk.get_kpis(s["hotel_id"]))


@router.get("/api/housekeeping/board")
def hk_board(s: dict = Depends(session)):
    return _reply(hk.get_board(s["hotel_id"]))


@router.get("/api/housekeeping/by_status")
def hk_by_status(status: str = Query(...), s: dict = Depends(session)):
    return _reply(dict(hk.get_cleaning_items_by_status_json(s["hotel_id"], status), hotel_name=s["hotel_name"]))


@router.get("/api/housekeeping/staff_workload")
def hk_staff_workload(s: dict = Depends(session)):
    return _reply(dict(hk.get_staff_workload_json(s["hotel_id"]), hotel_name=s["hotel_name"]))


@router.get("/api/housekeeping/activity")
def hk_activity(limit: int = Query(30), s: dict = Depends(session)):
    return _reply(dict(hk.get_activity_json(s["hotel_id"], limit), hotel_name=s["hotel_name"]))


@router.get("/api/housekeeping/on_duty")
def hk_on_duty(s: dict = Depends(session)):
    return _reply(dict(hk.get_on_duty_staff_json(s["hotel_id"]), hotel_name=s["hotel_name"]))


@router.post("/api/housekeeping/clock_in")
def hk_clock_in(payload: StaffDutyPayload, s: dict = Depends(session)):
    return _result(hk.staff_clock_in(s["hotel_id"], s["user_id"], payload.staff_phone))


@router.post("/api/housekeeping/clock_out")
def hk_clock_out(payload: StaffDutyPayload, s: dict = Depends(session)):
    return _result(hk.staff_clock_out(s["hotel_id"], s["user_id"], payload.staff_phone))


@router.post("/api/housekeeping/start_cleaning")
def hk_start_cleaning(payload: StartCleaningPayload, s: dict = Depends(session)):
    return _reply(hk.start_cleaning_for_room(s["hotel_id"], s["user_id"], payload.room_number, payload.staff_phone,
                                             payload.task_type or "standard_cleaning", payload.priority or "normal", payload.notes or ""))


@router.get("/api/staff/available")
def staff_available(department: str = Query(...), s: dict = Depends(session)):
    return _reply(dict(staff_manager.get_available_staff_json(s["hotel_id"], department), hotel_name=s["hotel_name"]))


@router.get("/api/housekeeping/dirty")
def hk_dirty(s: dict = Depends(session)):
    return _reply(dict(hk.read_dirty_rooms(s["hotel_id"]), hotel_name=s["hotel_name"]))


@router.get("/api/housekeeping/staff_status")
def hk_staff_status(staff_phone: str = Query(...), room_number: Optional[str] = Query(None), s: dict = Depends(session)):
    return _result(hk.read_staff_current_cleaning_status(s["hotel_id"], staff_phone, room_number))


@router.get("/api/housekeeping/room_status")
def hk_room_status(room_number: str = Query(...), s: dict = Depends(session)):
    return _result(hk.read_room_current_cleaning(s["hotel_id"], room_number))


@router.post("/api/housekeeping/auto_dirty")
def hk_auto_dirty(s: dict = Depends(session)):
    """Mark vacant rooms dirty once they have been clean for their auto_dirty_days."""
    return _result(mark_stale_clean_rooms(s["hotel_id"], s["user_id"]))


@router.post("/api/housekeeping/create_task")
def hk_create_task(payload: StartCleaningPayload, s: dict = Depends(session)):
    return _result(hk.create_cleaning_task(s["hotel_id"], s["user_id"], payload.room_number, payload.staff_phone,
                                           payload.task_type or "standard_cleaning", payload.priority or "normal", payload.notes or ""))


@router.post("/api/housekeeping/update_progress")
def hk_update_progress(payload: UpdateCleaningProgressPayload, s: dict = Depends(session)):
    return _result(hk.update_cleaning_progress(s["hotel_id"], s["user_id"], payload.cleaning_id, payload.new_status,
                                               payload.staff_phone, payload.notes or ""))


# ---------- maintenance ----------

class CreateMaintenancePayload(BaseModel):
    hotel_name: Optional[str] = None
    room_number: Optional[str] = None
    area: Optional[str] = None
    issue: str
    urgency: str = "NORMAL"


class UpdateMaintenancePayload(BaseModel):
    hotel_name: Optional[str] = None
    task_id: str
    status: str
    progress_notes: Optional[str] = ""
    resolution_description: Optional[str] = ""
    parts_used: Optional[str] = ""
    actual_cost: Optional[float] = None
    assigned_to_phone: Optional[str] = None


class ScheduleSetupPayload(BaseModel):
    hotel_name: Optional[str] = None
    room_number: Optional[str] = None
    area: Optional[str] = None
    issue: str = ""
    urgency: str = "NORMAL"
    interval_days: int = 90
    start_date: str
    auto_out_of_order: Optional[bool] = False
    active: Optional[bool] = True


class ScheduleUpdatePayload(BaseModel):
    hotel_name: Optional[str] = None
    schedule_id: str
    room_number: Optional[str] = None
    area: Optional[str] = None
    issue: Optional[str] = None
    urgency: Optional[str] = None
    interval_days: Optional[int] = None
    start_date: Optional[str] = None
    auto_out_of_order: Optional[bool] = None
    active: Optional[bool] = None


def _requests_text(items, zone):
    """The block-per-request text the maintenance and rooms pages parse (blank line between blocks)."""
    def shown(moment):
        return moment.astimezone(zone).strftime("%d-%m-%Y %I:%M %p") if moment else "N/A"

    blocks = []
    for item in items:
        latest = item["notes"][-1] if item["notes"] else {}
        lines = [
            "TASK ID: " + item["request_id"] + " | " + item["urgency"].upper(),
            "current_status: " + item["request_status"].upper(),
            "assigned_at: " + shown(item["assigned_at"]),
            "assigned_to: " + (item["assigned_to"] or "N/A"),
            "reported: " + shown(item["reported_at"]),
            "reported_by: " + (item["reported_by"] or "N/A"),
            "room: " + (item["room_number"] or "N/A"),
        ]
        lines += ["area: " + item["area"]] if item["area"] else []
        lines.append("details: " + " ".join(item["issue"].split()))
        lines += [label + ": " + " ".join(str(latest[key]).split()) for label, key in
                  (("notes", "progress_notes"), ("resolution", "resolution_description"), ("parts_used", "parts_used")) if latest.get(key)]
        lines += ["actual_cost: " + str(item["actual_cost"])] if item["actual_cost"] is not None else []
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


@router.post("/api/maintenance/create")
def maintenance_create(payload: CreateMaintenancePayload, s: dict = Depends(session)):
    return _result(mm.create_maintenance_request(s["hotel_id"], s["user_id"], payload.room_number, payload.area, payload.issue, payload.urgency))


@router.get("/api/maintenance/list")
def maintenance_list(room_number: str = Query(""), status_filter: str = Query(""), urgency_filter: str = Query(""), s: dict = Depends(session)):
    data = mm.read_maintenance_requests(s["hotel_id"], status_filter, urgency_filter, room_number, "", 50)
    return _reply(dict(data, result=_requests_text(data["items"], ZoneInfo(s["hotel_timezone"]))))


@router.post("/api/maintenance/update")
def maintenance_update(payload: UpdateMaintenancePayload, s: dict = Depends(session)):
    return _result(mm.update_maintenance_progress(s["hotel_id"], s["user_id"], payload.task_id, payload.status, payload.assigned_to_phone,
                                                  payload.progress_notes or "", payload.resolution_description or "",
                                                  payload.parts_used or "", payload.actual_cost))


@router.get("/api/maintenance/schedules/read")
def schedules_read(target_type: str = Query(""), room_number: str = Query(""), area: str = Query(""), active_only: bool = Query(True),
                   limit: int = Query(100), s: dict = Depends(session)):
    return _reply(mm.read_scheduled_maintenance(s["hotel_id"], target_type, room_number, area, active_only, limit))


@router.post("/api/maintenance/schedules/setup")
def schedules_setup(payload: ScheduleSetupPayload, s: dict = Depends(session)):
    return _result(mm.setup_scheduled_maintenance(s["hotel_id"], s["user_id"], payload.room_number, payload.area, payload.issue,
                                                  payload.urgency, payload.interval_days, payload.start_date,
                                                  bool(payload.auto_out_of_order), payload.active is not False))


@router.post("/api/maintenance/schedules/update")
def schedules_update(payload: ScheduleUpdatePayload, s: dict = Depends(session)):
    return _result(mm.update_scheduled_maintenance(s["hotel_id"], s["user_id"], payload.schedule_id, payload.room_number, payload.area,
                                                   payload.issue, payload.urgency, payload.interval_days, payload.start_date,
                                                   payload.auto_out_of_order, payload.active))


# ---------- room out-of-order ----------

class MarkOutOfOrderPayload(BaseModel):
    hotel_name: Optional[str] = None
    room_number: str
    start_date: str
    end_date: str
    reason: str
    block_type: str = "out_of_order"


class MarkAvailablePayload(BaseModel):
    hotel_name: Optional[str] = None
    room_number: str
    start_date: str
    end_date: str


@router.post("/api/assignments/mark_out_of_order")
def mark_out_of_order(payload: MarkOutOfOrderPayload, s: dict = Depends(session)):
    return _result(mm.mark_out_of_order(s["hotel_id"], s["user_id"], payload.room_number, payload.start_date, payload.end_date,
                                        payload.reason, payload.block_type))


@router.post("/api/assignments/mark_available")
def mark_available(payload: MarkAvailablePayload, s: dict = Depends(session)):
    return _result(mm.mark_available(s["hotel_id"], s["user_id"], payload.room_number, payload.start_date, payload.end_date))
