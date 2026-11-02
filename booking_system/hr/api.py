"""HR routes under /api/hr. The hotel and acting user come from the caller's auth dependency (core-owned), never from the request:
router_for(actor) where actor is a FastAPI dependency returning {'hotel_id': ..., 'user_id': ...}."""

from datetime import date, datetime, time
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from booking_system.hr import service

HTTP = {"created": 201, "refused": 409, "clarify": 409, "not_found": 404}
PLURAL = {"department": "departments", "position": "positions", "shift": "shifts", "leave_type": "leave_types"}


class DepartmentIn(BaseModel):
    department_name: str
    department_type: Literal["operated", "undistributed", "fixed"]
    confirm_new: bool = False


class PositionIn(BaseModel):
    position_name: str
    department_id: str
    confirm_new: bool = False


class ShiftIn(BaseModel):
    shift_name: str
    starts_at: time
    ends_at: time
    break_minutes: int
    confirm_new: bool = False


class LeaveTypeIn(BaseModel):
    leave_type_name: str
    paid: bool
    confirm_new: bool = False


NAMED_IN = {"department": DepartmentIn, "position": PositionIn, "shift": ShiftIn, "leave_type": LeaveTypeIn}


class StaffIn(BaseModel):
    staff_name: str
    department_id: str
    employment_type: Literal["full_time", "part_time", "casual", "contract"]
    phone: Optional[str] = None
    email: Optional[str] = None
    position_id: Optional[str] = None
    user_id: Optional[str] = None
    hired_on: Optional[date] = None
    confirm_new: bool = False


class StaffChange(BaseModel):
    staff_name: Optional[str] = None
    department_id: Optional[str] = None
    position_id: Optional[str] = None
    user_id: Optional[str] = None
    employment_type: Optional[Literal["full_time", "part_time", "casual", "contract"]] = None
    status: Optional[Literal["active", "inactive", "terminated"]] = None
    hired_on: Optional[date] = None
    terminated_on: Optional[date] = None


class IdentityIn(BaseModel):
    identity_type: Literal["phone", "email"]
    identity_value: str


class RosterIn(BaseModel):
    staff_id: str
    shift_id: str
    department_id: str
    work_dates: List[date]


class ClockIn(BaseModel):
    staff_id: str
    roster_id: Optional[str] = None


class ClockOut(BaseModel):
    staff_id: str


class AttendanceChange(BaseModel):
    clock_in: Optional[datetime] = None
    clock_out: Optional[datetime] = None


class LeaveIn(BaseModel):
    staff_id: str
    leave_type_id: str
    start_date: date
    end_date: date


def _reply(result):
    return JSONResponse(jsonable_encoder(result), status_code=HTTP.get(result["status"], 200))


def _set(model):
    return model.model_dump(exclude_unset=True)


def router_for(actor):
    router = APIRouter(prefix="/api/hr", tags=["hr"])

    def named_routes(kind):
        path = "/" + PLURAL[kind]
        spec = service.NAMED[kind]

        def list_items(who=Depends(actor)):
            return _reply(service.list_named(kind, who["hotel_id"]))

        def find_item(name: str, who=Depends(actor)):
            return _reply(service.find_named(kind, who["hotel_id"], name))

        def create_item(body: NAMED_IN[kind], who=Depends(actor)):
            values = body.model_dump()
            return _reply(service.create_named(kind, who["hotel_id"], values[spec["name"]], values, body.confirm_new))

        def update_item(entity_id: str, body: Dict[str, object], who=Depends(actor)):
            return _reply(service.update_named(kind, who["hotel_id"], entity_id, body))

        def delete_item(entity_id: str, who=Depends(actor)):
            return _reply(service.delete_named(kind, who["hotel_id"], entity_id))

        router.add_api_route(path, list_items, methods=["GET"], name="hr_list_" + kind)
        router.add_api_route(path + "/find", find_item, methods=["GET"], name="hr_find_" + kind)
        router.add_api_route(path, create_item, methods=["POST"], name="hr_create_" + kind)
        router.add_api_route(path + "/{entity_id}", update_item, methods=["PATCH"], name="hr_update_" + kind)
        router.add_api_route(path + "/{entity_id}", delete_item, methods=["DELETE"], name="hr_delete_" + kind)

    for kind in service.NAMED:
        named_routes(kind)

    @router.get("/staff")
    def list_staff(department_id: Optional[str] = None, status: Optional[str] = None, who=Depends(actor)):
        return _reply(service.list_staff(who["hotel_id"], department_id, status))

    @router.get("/staff/find")
    def find_staff(q: str, who=Depends(actor)):
        return _reply(service.find_staff(who["hotel_id"], q))

    @router.get("/staff/available")
    def available_staff(department_id: str, work_date: date, who=Depends(actor)):
        return _reply(service.available_staff(who["hotel_id"], department_id, work_date))

    @router.get("/staff/assignable")
    def check_assignable(staff_id: str, department_id: str, work_date: date, who=Depends(actor)):
        return _reply(service.check_assignable(who["hotel_id"], staff_id, department_id, work_date))

    @router.get("/staff/on_duty")
    def on_duty(department_id: Optional[str] = None, who=Depends(actor)):
        return _reply(service.on_duty_staff(who["hotel_id"], department_id))

    @router.post("/staff")
    def create_staff(body: StaffIn, who=Depends(actor)):
        return _reply(service.create_staff(who["hotel_id"], **body.model_dump()))

    @router.get("/staff/{staff_id}")
    def read_staff(staff_id: str, who=Depends(actor)):
        return _reply(service.read_staff(who["hotel_id"], staff_id))

    @router.patch("/staff/{staff_id}")
    def update_staff(staff_id: str, body: StaffChange, who=Depends(actor)):
        return _reply(service.update_staff(who["hotel_id"], staff_id, _set(body)))

    @router.delete("/staff/{staff_id}")
    def delete_staff(staff_id: str, who=Depends(actor)):
        return _reply(service.delete_staff(who["hotel_id"], staff_id))

    @router.post("/staff/{staff_id}/identities")
    def add_identity(staff_id: str, body: IdentityIn, who=Depends(actor)):
        return _reply(service.add_identity(who["hotel_id"], staff_id, body.identity_type, body.identity_value))

    @router.delete("/staff/identities/{staff_identity_id}")
    def remove_identity(staff_identity_id: str, who=Depends(actor)):
        return _reply(service.remove_identity(who["hotel_id"], staff_identity_id))

    @router.get("/staff/{staff_id}/profile")
    def read_profile(staff_id: str, who=Depends(actor)):
        return _reply(service.read_staff_profile(who["hotel_id"], staff_id))

    @router.patch("/staff/{staff_id}/profile")
    def write_profile(staff_id: str, body: Dict[str, object], who=Depends(actor)):
        return _reply(service.write_staff_profile(who["hotel_id"], staff_id, body))

    @router.get("/roster")
    def list_roster(date_from: date, date_to: date, department_id: Optional[str] = None, staff_id: Optional[str] = None, who=Depends(actor)):
        return _reply(service.list_roster(who["hotel_id"], date_from, date_to, department_id, staff_id))

    @router.post("/roster")
    def create_roster(body: RosterIn, who=Depends(actor)):
        return _reply(service.create_roster(who["hotel_id"], body.staff_id, body.shift_id, body.department_id, body.work_dates, who["user_id"]))

    @router.delete("/roster/{roster_id}")
    def delete_roster(roster_id: str, who=Depends(actor)):
        return _reply(service.delete_roster(who["hotel_id"], roster_id))

    @router.get("/attendance")
    def list_attendance(date_from: date, date_to: date, staff_id: Optional[str] = None, who=Depends(actor)):
        return _reply(service.list_attendance(who["hotel_id"], date_from, date_to, staff_id))

    @router.post("/attendance/clock_in")
    def clock_in(body: ClockIn, who=Depends(actor)):
        return _reply(service.clock_in(who["hotel_id"], body.staff_id, who["user_id"], body.roster_id))

    @router.post("/attendance/clock_out")
    def clock_out(body: ClockOut, who=Depends(actor)):
        return _reply(service.clock_out(who["hotel_id"], body.staff_id, who["user_id"]))

    @router.patch("/attendance/{attendance_id}")
    def correct_attendance(attendance_id: str, body: AttendanceChange, who=Depends(actor)):
        return _reply(service.correct_attendance(who["hotel_id"], attendance_id, _set(body), who["user_id"]))

    @router.get("/leave")
    def list_leave(status: Optional[str] = None, staff_id: Optional[str] = None, date_from: Optional[date] = None,
                   date_to: Optional[date] = None, who=Depends(actor)):
        return _reply(service.list_leave(who["hotel_id"], status, staff_id, date_from, date_to))

    @router.post("/leave")
    def request_leave(body: LeaveIn, who=Depends(actor)):
        return _reply(service.request_leave(who["hotel_id"], body.staff_id, body.leave_type_id, body.start_date, body.end_date, who["user_id"]))

    @router.post("/leave/{leave_request_id}/approve")
    def approve_leave(leave_request_id: str, who=Depends(actor)):
        return _reply(service.approve_leave(who["hotel_id"], leave_request_id, who["user_id"]))

    @router.post("/leave/{leave_request_id}/reject")
    def reject_leave(leave_request_id: str, who=Depends(actor)):
        return _reply(service.reject_leave(who["hotel_id"], leave_request_id, who["user_id"]))

    @router.post("/leave/{leave_request_id}/cancel")
    def cancel_leave(leave_request_id: str, who=Depends(actor)):
        return _reply(service.cancel_leave(who["hotel_id"], leave_request_id, who["user_id"]))

    return router
