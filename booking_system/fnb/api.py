"""F&B outlet API. The session hotel and user come from booking_system.access.session; ids in the body must belong to that hotel.
The access guard checks the fnb module level per route (GET view, writes operate)."""

from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from booking_system import access
from booking_system.access import session
from booking_system.finance import service as finance
from booking_system.fnb import service
from booking_system.fnb.schema import ensure_schema

router = APIRouter(prefix="/api/fnb", tags=["fnb"])
access.register((
    ("POST /api/fnb/outlets", "fnb", "manage"),
    ("DELETE /api/fnb/outlets/{outlet_id}", "fnb", "manage"),
    ("POST /api/fnb/outlets/{outlet_id}/menu", "fnb", "manage"),
    ("DELETE /api/fnb/menu/{item_id}", "fnb", "manage"),
    ("POST /api/fnb/checks/{check_id}/adjust", "fnb", "manage"),
    ("POST /api/fnb/corrections/{correction_id}/decide", "fnb", "manage"),
))


async def _reply(fn, *args, **kwargs):
    result = await run_in_threadpool(fn, *args, **kwargs)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


class Outlet(BaseModel):
    outlet_name: str
    outlet_type: str
    status: str = "active"
    outlet_id: Optional[str] = None
    confirm_new: bool = False


class MenuItem(BaseModel):
    item_name: str
    price: float
    transaction_code: str
    status: str = "active"
    description: Optional[str] = None
    image_url: Optional[str] = None
    item_id: Optional[str] = None
    confirm_new: bool = False


class OpenCheck(BaseModel):
    outlet_id: str
    covers: int
    table_number: Optional[str] = None
    confirm_new: bool = False


class CheckHeader(BaseModel):
    covers: int
    table_number: Optional[str] = None


class Line(BaseModel):
    item_id: str
    quantity: float


class LineDiscount(BaseModel):
    amount: float
    reason: Optional[str] = None


class CheckDiscount(BaseModel):
    percent: float
    reason: Optional[str] = None


class Void(BaseModel):
    reason: str


class Payment(BaseModel):
    transaction_code: str
    amount: float
    reference: Optional[str] = None


class SettleDirect(BaseModel):
    payments: List[Payment]
    cashier_shift_id: Optional[str] = None


class SettleRoom(BaseModel):
    booking_id: str
    room_id: Optional[str] = None
    folio_ref: Optional[str] = None


@router.get("/outlets")
async def outlets(s=Depends(session)):
    return await _reply(service.read_outlets, s["hotel_id"])


@router.post("/outlets")
async def save_outlet(body: Outlet, s=Depends(session)):
    return await _reply(service.save_outlet, s["hotel_id"], body.outlet_name, body.outlet_type, body.status, body.outlet_id, body.confirm_new)


@router.delete("/outlets/{outlet_id}")
async def delete_outlet(outlet_id: str, s=Depends(session)):
    return await _reply(service.delete_outlet, s["hotel_id"], outlet_id)


@router.get("/transaction_codes")
async def transaction_codes(s=Depends(session)):
    return await _reply(finance.list_transaction_codes, s["hotel_id"])


@router.get("/outlets/{outlet_id}/menu")
async def menu(outlet_id: str, s=Depends(session)):
    return await _reply(service.read_menu, s["hotel_id"], outlet_id)


@router.post("/outlets/{outlet_id}/menu")
async def save_menu_item(outlet_id: str, body: MenuItem, s=Depends(session)):
    return await _reply(service.save_menu_item, s["hotel_id"], outlet_id, body.item_name, body.price, body.transaction_code,
                        body.status, body.description, body.image_url, body.item_id, body.confirm_new)


@router.delete("/menu/{item_id}")
async def delete_menu_item(item_id: str, s=Depends(session)):
    return await _reply(service.delete_menu_item, s["hotel_id"], item_id)


@router.get("/outlets/{outlet_id}/checks")
async def checks(outlet_id: str, business_date: Optional[date] = None, status: Optional[str] = None, s=Depends(session)):
    return await _reply(service.read_checks, s["hotel_id"], outlet_id, business_date, status)


@router.post("/checks")
async def open_check(body: OpenCheck, s=Depends(session)):
    return await _reply(service.open_check, s["hotel_id"], body.outlet_id, body.covers, body.table_number, s["user_id"], body.confirm_new)


@router.get("/checks/{check_id}")
async def check(check_id: str, s=Depends(session)):
    return await _reply(service.read_check, s["hotel_id"], check_id)


@router.put("/checks/{check_id}")
async def update_check(check_id: str, body: CheckHeader, s=Depends(session)):
    return await _reply(service.update_check, s["hotel_id"], check_id, body.covers, body.table_number)


@router.post("/checks/{check_id}/lines")
async def add_line(check_id: str, body: Line, s=Depends(session)):
    return await _reply(service.add_line, s["hotel_id"], check_id, body.item_id, body.quantity, s["user_id"])


@router.put("/checks/{check_id}/lines/{line_id}/discount")
async def discount_line(check_id: str, line_id: str, body: LineDiscount, s=Depends(session)):
    return await _reply(service.discount_line, s["hotel_id"], check_id, line_id, body.amount, body.reason)


@router.put("/checks/{check_id}/discount")
async def discount_check(check_id: str, body: CheckDiscount, s=Depends(session)):
    return await _reply(service.discount_check, s["hotel_id"], check_id, body.percent, body.reason)


@router.post("/checks/{check_id}/lines/{line_id}/void")
async def void_line(check_id: str, line_id: str, body: Void, s=Depends(session)):
    return await _reply(service.void_line, s["hotel_id"], check_id, line_id, body.reason, s["user_id"])


@router.post("/checks/{check_id}/void")
async def void_check(check_id: str, body: Void, s=Depends(session)):
    return await _reply(service.void_check, s["hotel_id"], check_id, body.reason, s["user_id"])


@router.post("/checks/{check_id}/settle/direct")
async def settle_direct(check_id: str, body: SettleDirect, s=Depends(session)):
    return await _reply(service.settle_direct, s["hotel_id"], check_id, [p.model_dump() for p in body.payments], s["user_id"],
                        body.cashier_shift_id)


@router.post("/checks/{check_id}/settle/room")
async def settle_room(check_id: str, body: SettleRoom, s=Depends(session)):
    return await _reply(service.settle_room, s["hotel_id"], check_id, body.booking_id, s["user_id"], body.folio_ref, body.room_id)


class Adjust(BaseModel):
    transaction_code: str
    amount: Decimal
    reason: str


class ReopenDecision(BaseModel):
    approve: bool
    cashier_shift_id: Optional[str] = None


@router.post("/checks/{check_id}/adjust")
async def adjust_check(check_id: str, body: Adjust, s=Depends(session)):
    return await _reply(service.adjust_closed_check, s["hotel_id"], check_id, body.transaction_code, body.amount, body.reason, s["user_id"])


@router.post("/checks/{check_id}/reopen")
async def request_reopen(check_id: str, body: Void, s=Depends(session)):
    return await _reply(service.request_reopen, s["hotel_id"], check_id, body.reason, s["user_id"])


@router.post("/corrections/{correction_id}/decide")
async def decide_reopen(correction_id: str, body: ReopenDecision, s=Depends(session)):
    return await _reply(service.decide_reopen, s["hotel_id"], correction_id, body.approve, s["user_id"], body.cashier_shift_id)


@router.get("/revenue_summary")
async def revenue_summary(business_date: Optional[date] = None, s=Depends(session)):
    return await _reply(service.revenue_summary, s["hotel_id"], business_date)


def startup():
    return ensure_schema()
