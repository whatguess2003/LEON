"""Front desk routes: the assignment calendar, booking detail, check-in and check-out, room moves, pricing, readiness and the desk queue."""

from datetime import timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from booking_system.access import session
from booking_system.front_desk_manager import _day, intelligent_front_desk as desk

router = APIRouter()

ACCESS = [
    ("GET /api/assignments/calendar", "front_desk", "view"),
    ("GET /api/assignments/calendar/checkins", "front_desk", "view"),
    ("GET /api/assignments/calendar/checkouts", "front_desk", "view"),
    ("POST /api/assignments/preview_bulk", "front_desk", "view"),
    ("GET /api/assignments/detail", "front_desk", "view"),
    ("GET /api/pricing/breakdown", "front_desk", "view"),
    ("POST /api/pricing/override", "front_desk", "manage"),
    ("POST /api/assignments/verify-phone", "front_desk", "operate"),
    ("POST /api/assignments/update", "front_desk", "operate"),
    ("POST /api/assignments/cancel", "front_desk", "operate"),
    ("POST /api/assignments/check_in", "front_desk", "operate"),
    ("POST /api/assignments/undo_check_in", "front_desk", "operate"),
    ("POST /api/assignments/reset_stay", "front_desk", "operate"),
    ("POST /api/assignments/check_out", "front_desk", "operate"),
    ("POST /api/assignments/bulk_checkout_past", "front_desk", "operate"),
    ("GET /api/assignments/past_stays", "front_desk", "view"),
    ("POST /api/assignments/past_stays", "front_desk", "view"),
    ("POST /api/assignments/bulk_checkout_selected", "front_desk", "operate"),
    ("POST /api/assignments/reassign", "front_desk", "operate"),
    ("GET /api/assignments/history", "front_desk", "view"),
    ("GET /api/rooms/readiness_status", "front_desk", "view"),
    ("GET /api/rooms/readiness_status_all", "front_desk", "view"),
    ("POST /api/rooms/readiness_status/update", "housekeeping", "view"),
    ("GET /api/desk/queue", "front_desk", "view"),
]


def _reply(result):
    return JSONResponse(content=result, status_code=400 if result.get("error") else 200)


def _calendar(s, start_date, end_date, room_type, room_numbers):
    """The grid through the day after end_date, so the last visible night shows its check-out."""
    end = _day(end_date)
    if not end:
        return {"error": "Invalid date format. Use YYYY-MM-DD"}
    numbers = [part.strip() for part in room_numbers.split(",") if part.strip()] if room_numbers else None
    data = desk.read_room_number_assignments_calendar_internal(s["hotel_id"], start_date, (end + timedelta(days=1)).isoformat(), room_type, numbers)
    return dict(data, hotel_name=s["hotel_name"])


def _edges(data, edge, pick):
    """Per room, the dates where a stay on that room starts (checkin) or ends (checkout)."""
    if data.get("error"):
        return data
    rooms = []
    for room in data["rooms"]:
        entries, halves = room["entries"], {}
        for day in data["days"]:
            before = (entries.get((_day(day) - timedelta(days=1)).isoformat()) or {}).get("booking_id")
            here = (entries.get(day) or {}).get("booking_id")
            booking_id, other = pick(here, before)
            if booking_id and booking_id != other:
                halves[day] = {edge: {"booking_id": booking_id}}
        rooms.append({"room_number": room["room_number"], "room_type": room["room_type"], "halves": halves})
    return {"hotel_name": data["hotel_name"], "start_date": data["start_date"], "end_date": data["end_date"], "days": data["days"], "rooms": rooms}


@router.get("/api/assignments/calendar")
def assignments_calendar(start_date: str = Query(...), end_date: str = Query(...), room_type: Optional[str] = Query(None),
                         room_numbers: Optional[str] = Query(None), s: dict = Depends(session)):
    return _reply(_calendar(s, start_date, end_date, room_type, room_numbers))


@router.get("/api/assignments/calendar/checkins")
def assignments_calendar_checkins(start_date: str = Query(...), end_date: str = Query(...), room_type: Optional[str] = Query(None),
                                  room_numbers: Optional[str] = Query(None), s: dict = Depends(session)):
    return _reply(_edges(_calendar(s, start_date, end_date, room_type, room_numbers), "checkin", lambda here, before: (here, before)))


@router.get("/api/assignments/calendar/checkouts")
def assignments_calendar_checkouts(start_date: str = Query(...), end_date: str = Query(...), room_type: Optional[str] = Query(None),
                                   room_numbers: Optional[str] = Query(None), s: dict = Depends(session)):
    return _reply(_edges(_calendar(s, start_date, end_date, room_type, room_numbers), "checkout", lambda here, before: (before, here)))


@router.post("/api/assignments/preview_bulk")
def assignments_preview_bulk(payload: dict = Body(...), s: dict = Depends(session)):
    booking_ids = payload.get("booking_ids") or []
    return {"previews": desk.read_booking_previews(s["hotel_id"], booking_ids if isinstance(booking_ids, list) else [])}


@router.get("/api/assignments/detail")
def assignments_detail(booking_id: str = Query(...), stay_date: str = Query(None), s: dict = Depends(session)):
    return _reply(desk.read_booking_detail_internal(s["hotel_id"], booking_id, stay_date))


@router.get("/api/pricing/breakdown")
def pricing_breakdown(booking_id: str = Query(...), s: dict = Depends(session)):
    return _reply(desk.read_pricing_breakdown_internal(s["hotel_id"], booking_id))


class OverridePricingPayload(BaseModel):
    booking_id: str
    new_total_amount: float


@router.post("/api/pricing/override")
def pricing_override(payload: OverridePricingPayload, s: dict = Depends(session)):
    return _reply(desk.override_pricing_internal(s["hotel_id"], s["user_id"], payload.booking_id, payload.new_total_amount))


class VerifyPhonePayload(BaseModel):
    booking_id: str
    guest_phone: str = ""
    guest_name: str = ""
    country_code: str = ""


@router.post("/api/assignments/verify-phone")
def assignments_verify_phone(payload: VerifyPhonePayload, s: dict = Depends(session)):
    return _reply(desk.verify_booking_phone(s["hotel_id"], s["user_id"], payload.booking_id.strip(), payload.guest_phone.strip(),
                                            payload.guest_name.strip(), payload.country_code.strip()))


class UpdateBookingDetailPayload(BaseModel):
    booking_id: str
    guest: Optional[Dict[str, Any]] = None
    booking: Optional[Dict[str, Any]] = None
    payment: Optional[Dict[str, Any]] = None


@router.post("/api/assignments/update")
def assignments_update(payload: UpdateBookingDetailPayload, s: dict = Depends(session)):
    if payload.payment:
        return _reply({"error": "Record payments through /api/payments/update"})
    return _reply(desk.update_booking_detail_internal(s["hotel_id"], s["user_id"], payload.booking_id, payload.guest, payload.booking))


class BookingPayload(BaseModel):
    booking_id: str


@router.post("/api/assignments/cancel")
def assignments_cancel(payload: BookingPayload, s: dict = Depends(session)):
    from booking_system.booking_manager import booking_manager
    return _reply(booking_manager.cancel_booking(s["hotel_id"], s["user_id"], payload.booking_id, "Cancelled at the front desk"))


class CheckInPayload(BaseModel):
    booking_id: str
    room_number: Optional[str] = None
    assignment_id: Optional[str] = None


@router.post("/api/assignments/check_in")
def assignments_check_in(payload: CheckInPayload, s: dict = Depends(session)):
    return _reply(desk.update_guest_check_in_status_internal(s["hotel_id"], s["user_id"], s["hotel_timezone"], payload.booking_id,
                                                             payload.room_number, payload.assignment_id))


class UndoCheckInPayload(BaseModel):
    booking_id: str
    room_number: str
    mark_clean: bool = True


@router.post("/api/assignments/undo_check_in")
def assignments_undo_check_in(payload: UndoCheckInPayload, s: dict = Depends(session)):
    return _reply(desk.undo_guest_check_in_internal(s["hotel_id"], s["user_id"], payload.booking_id, payload.room_number, payload.mark_clean))


class ResetStayPayload(BaseModel):
    booking_id: str
    stay_date: str


@router.post("/api/assignments/reset_stay")
def assignments_reset_stay(payload: ResetStayPayload, s: dict = Depends(session)):
    return _reply(desk.reset_stay_state_for_date_internal(s["hotel_id"], s["user_id"], payload.booking_id, payload.stay_date))


@router.post("/api/assignments/check_out")
def assignments_check_out(payload: CheckInPayload, s: dict = Depends(session)):
    return _reply(desk.update_guest_check_out_status_internal(s["hotel_id"], s["user_id"], payload.booking_id, payload.room_number))


@router.post("/api/assignments/bulk_checkout_past")
def assignments_bulk_checkout_past(s: dict = Depends(session)):
    return _reply(desk.bulk_checkout_past_internal(s["hotel_id"], s["user_id"], s["hotel_timezone"]))


@router.get("/api/assignments/past_stays")
@router.post("/api/assignments/past_stays")
def assignments_past_stays(s: dict = Depends(session)):
    return _reply(dict(desk.read_past_stays_internal(s["hotel_id"], s["hotel_timezone"]), hotel_name=s["hotel_name"]))


class BulkCheckoutSelectedPayload(BaseModel):
    items: list


@router.post("/api/assignments/bulk_checkout_selected")
def assignments_bulk_checkout_selected(payload: BulkCheckoutSelectedPayload, s: dict = Depends(session)):
    return _reply(desk.bulk_checkout_selected_internal(s["hotel_id"], s["user_id"], payload.items))


class ReassignPayload(BaseModel):
    booking_id: str
    stay_date: str
    old_room_number: str = ""
    new_room_number: str = ""
    mark_old_clean: Optional[bool] = None


@router.post("/api/assignments/reassign")
def assignments_reassign(payload: ReassignPayload, s: dict = Depends(session)):
    return _reply(desk.reassign_room_for_date_internal(s["hotel_id"], s["user_id"], payload.booking_id, payload.stay_date,
                                                       payload.old_room_number, payload.new_room_number, payload.mark_old_clean))


@router.get("/api/assignments/history")
def assignments_history(booking_id: str = Query(...), s: dict = Depends(session)):
    return _reply(desk.read_assignment_history_internal(s["hotel_id"], booking_id))


@router.get("/api/rooms/readiness_status")
def rooms_readiness_status(room_number: str = Query(...), s: dict = Depends(session)):
    return _reply(desk.read_room_readiness_status_json(s["hotel_id"], room_number))


@router.get("/api/rooms/readiness_status_all")
def rooms_readiness_status_all(s: dict = Depends(session)):
    return _reply(dict(desk.read_all_room_readiness_status_internal(s["hotel_id"]), hotel_name=s["hotel_name"]))


class ReadinessPayload(BaseModel):
    room_number: str
    new_status: str


@router.post("/api/rooms/readiness_status/update")
def rooms_readiness_status_update(payload: ReadinessPayload, s: dict = Depends(session)):
    return _reply(desk.update_room_readiness_status_internal(s["hotel_id"], s["user_id"], payload.room_number, payload.new_status))


@router.get("/api/desk/queue")
def desk_queue(s: dict = Depends(session)):
    """Open approval rows and manual tasks for the session hotel."""
    from booking_system.desk_actions import action_names, approval_names
    from booking_system.guest_manager import guest_manager
    approvals, tasks = guest_manager.desk_queue(s["hotel_id"])
    return {"hotel_name": s["hotel_name"], "desk_require_approval": approval_names(s["hotel_id"]), "executional_names": action_names(),
            "approvals": approvals, "tasks": tasks}
