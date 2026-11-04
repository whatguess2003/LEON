"""Bookings, guests, desk payments and OTA reservation routes. The hotel is the session's hotel_id; a hotel_name the UI still sends
is ignored. POST /update_reservation is public: the hotel comes from the channel hotel code in the payload."""

import logging
import os
import urllib.parse
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import booking_system.cm_path
from aiosell_channel_manager import push_dates
from booking_system import booking_manager, guest_manager, payment_manager, service_token
from booking_system.access import session

router = APIRouter()

ACCESS = [
    ("GET /api/ota/mapping", "reservations", "view"),
    ("POST /api/ota/mapping", "reservations", "manage"),
    ("POST /api/ota/room-mapping", "reservations", "manage"),
    ("GET /api/ota/reservation-out", "reservations", "view"),
    ("GET /api/ota/reservation-in", "reservations", "view"),
    ("GET /api/bookings-calendar", "reservations", "view"),
    ("POST /api/bookings/create", "reservations", "operate"),
    ("POST /api/bookings/group", "reservations", "operate"),
    ("POST /api/bookings/modify", "reservations", "operate"),
    ("POST /api/bookings/cancel", "reservations", "operate"),
    ("GET /api/guest_profile", "front_desk", "view"),
    ("POST /api/guest_profile/create", "front_desk", "operate"),
    ("POST /api/guest_profile/update", "front_desk", "operate"),
    ("POST /api/guest_profile/deactivate", "front_desk", "operate"),
    ("GET /api/guests", "front_desk", "view"),
    ("GET /api/guests/search", "front_desk", "view"),
    ("GET /api/guests/record", "front_desk", "view"),
    ("POST /api/payments/update", "front_desk", "operate"),
    ("POST /api/payments/refund", "front_desk", "manage"),
    ("POST /api/payments/void", "front_desk", "manage"),
    ("GET /api/payments/ledger", "front_desk", "view"),
]

# Modify payload names -> booking_manager.modify_booking change names.
MODIFY_FIELDS = {"new_room_type": "room_type_name", "new_rooms_booked": "rooms", "channel": "channel"}


def _refused(result, status=400):
    if isinstance(result, dict) and result.get("error"):
        return JSONResponse(content=dict(result), status_code=status)
    return result


# ---------- channel manager ----------

def _channel_manager_url() -> str:
    return str(os.environ.get("CHANNEL_MANAGER_URL") or "http://127.0.0.1:8006").rstrip("/")


def _credential(hotel_id: str) -> dict:
    """The service header for a channel manager call about hotel_id; {} when CM_SERVICE_SECRET is not set (the call is refused)."""
    token = service_token.issue(hotel_id)
    return {service_token.HEADER: token} if token else {}


def _channel_manager_request(method: str, hotel_id: str, path: str, body: dict = None):
    import requests
    if not _credential(hotel_id):
        return {"error": "CM_SERVICE_SECRET is not set"}
    try:
        response = requests.request(method, _channel_manager_url() + path, json=body, headers=_credential(hotel_id), timeout=(3, 20))
    except requests.RequestException as exc:
        return {"error": "Channel manager is not reachable: " + str(exc)}
    try:
        data = response.json()
    except ValueError:
        return {"error": "Channel manager response was not JSON"}
    if response.status_code >= 400:
        return {"error": str((data or {}).get("error") or "Channel manager request failed")}
    return data


def _push_pms_booking(hotel_id, booking_id, guest_phone, room_type, stay_dates, channel):
    return _channel_manager_request("POST", hotel_id, "/reservation-in", {
        "hotel_id": hotel_id, "booking_id": booking_id, "guest_phone": guest_phone, "room_type": room_type,
        "stay_dates": list(stay_dates or []), "channel": channel, "booking_status": "confirmed",
    })


async def _push_aiosell(s, stay_dates):
    return await run_in_threadpool(push_dates, s["hotel_id"], list(stay_dates or []))


@router.post("/update_reservation")
async def update_reservation(request: Request, background_tasks: BackgroundTasks):
    """Aiosell book, modify, and cancel. The body is the full reservation state."""
    from booking_system.aiosell_reservation import authorize_basic
    from booking_system.channel_reservations import accept_webhook
    denied = authorize_basic(request.headers.get("authorization"))
    if denied:
        status = 401 if denied == "Unauthorized" else 400
        return JSONResponse(content={"success": False, "message": denied}, status_code=status)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(content={"success": False, "message": "Invalid reservation payload"}, status_code=400)
    body = payload if isinstance(payload, dict) else {}
    logging.info("Aiosell reservation action=%s bookingId=%s hotelCode=%s", body.get("action"), body.get("bookingId"), body.get("hotelCode"))
    result = await run_in_threadpool(accept_webhook, body)
    if result.get("success"):
        background_tasks.add_task(push_dates, result["hotel_id"], list(result.get("stay_dates") or []))
    public = {"success": bool(result.get("success")), "message": str(result.get("message") or "")}
    return public if public["success"] else JSONResponse(content=public, status_code=400)


async def _reservation_list(path, s):
    result = await run_in_threadpool(_channel_manager_request, "GET", s["hotel_id"], path + "?hotel_id=" + urllib.parse.quote(s["hotel_id"]))
    if result.get("error") and "reservations" not in result:
        return JSONResponse(content=result, status_code=502)
    return result


def _channel_manager_forward(method: str, hotel_id: str, path: str, body: dict = None):
    """The channel manager's own status and body, so a refused mapping still carries its room codes and room types."""
    import requests
    if not _credential(hotel_id):
        return JSONResponse(content={"error": "CM_SERVICE_SECRET is not set"}, status_code=503)
    try:
        response = requests.request(method, _channel_manager_url() + path, json=body, headers=_credential(hotel_id), timeout=(3, 60))
        data = response.json()
    except requests.RequestException as exc:
        return JSONResponse(content={"error": "Channel manager is not reachable: " + str(exc)}, status_code=502)
    except ValueError:
        return JSONResponse(content={"error": "Channel manager response was not JSON"}, status_code=502)
    return JSONResponse(content=data, status_code=response.status_code)


@router.get("/api/ota/mapping")
async def read_ota_mapping(s: dict = Depends(session)):
    """The hotel's Aiosell hotel code, property details, room types and room code mapping."""
    return await run_in_threadpool(_channel_manager_forward, "GET", s["hotel_id"], "/mapping?hotel_id=" + urllib.parse.quote(s["hotel_id"]))


@router.post("/api/ota/mapping")
async def save_ota_mapping(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Link the hotel to its Aiosell hotel code."""
    return await run_in_threadpool(_channel_manager_forward, "POST", s["hotel_id"], "/mapping",
                                   {"hotel_id": s["hotel_id"], "hotel_code": str((payload or {}).get("hotel_code") or "")})


@router.post("/api/ota/room-mapping")
async def save_ota_room_mapping(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Map one Aiosell room code to a room type (an empty room type removes it), then push 31 days."""
    body = payload or {}
    return await run_in_threadpool(_channel_manager_forward, "POST", s["hotel_id"], "/room-mapping",
                                   {"hotel_id": s["hotel_id"], "room_code": str(body.get("room_code") or ""), "room_type": str(body.get("room_type") or "")})


@router.get("/api/ota/reservation-out")
async def read_ota_reservation_out(s: dict = Depends(session)):
    """The reservation list held by the channel manager."""
    return await _reservation_list("/reservations", s)


@router.get("/api/ota/reservation-in")
async def read_ota_reservation_in(s: dict = Depends(session)):
    """PMS bookings stored as reservation in on the channel manager."""
    return await _reservation_list("/reservation-in", s)


# ---------- bookings ----------

@router.get("/api/bookings-calendar")
def get_bookings_calendar(s: dict = Depends(session)):
    return booking_manager.read_recent_bookings(s["hotel_id"], limit=50)


def _summary(made):
    return ("Booking " + made["booking_id"] + " confirmed: " + str(made["rooms"]) + " x " + made["room_type_name"] + ", "
            + made["check_in"] + " to " + made["check_out"] + ", " + made["currency"] + " " + format(made["gross_amount"], ".2f"))


def _desk_guest(s, guest_phone):
    """The guest dict for a booking, or {'error'} when no active guest holds the phone."""
    found = guest_manager.read_guest(s["hotel_id"], guest_phone)
    if not found or found["status"] != "active":
        return {"error": "No guest profile found for " + str(guest_phone or "") + ". Create the guest profile first."}
    return {"guest_name": found["guest_name"], "phone": found["guest_phone"]}


@router.post("/api/bookings/create")
async def api_bookings_create(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """payload: {guest_phone, room_type, stay_dates: [YYYY-MM-DD, ...], rooms_booked?, booking_notes?, channel?}"""
    guest_phone = str(payload.get("guest_phone") or "").strip()
    room_type = str(payload.get("room_type") or "").strip().upper()
    stay_dates = payload.get("stay_dates") or []
    channel = str(payload.get("channel") or "PMS").strip().upper()
    if not guest_phone or not room_type or not isinstance(stay_dates, list) or not stay_dates:
        return JSONResponse(content={"error": "guest_phone, room_type, and stay_dates are required"}, status_code=400)
    stay = booking_manager.stay_range(stay_dates)
    guest = await run_in_threadpool(_desk_guest, s, guest_phone)
    if stay.get("error") or guest.get("error"):
        return _refused(stay if stay.get("error") else guest)
    made = await run_in_threadpool(
        booking_manager.create_booking, s["hotel_id"], s["user_id"], guest, room_type, stay["check_in"], stay["check_out"],
        int(payload.get("rooms_booked") or 1), channel=channel, notes=payload.get("booking_notes") or None,
    )
    if made.get("error"):
        return _refused(made)
    aiosell = await _push_aiosell(s, made["stay_dates"])
    channel_manager = await run_in_threadpool(_push_pms_booking, s["hotel_id"], made["booking_id"], guest["phone"], made["room_type_name"],
                                              made["stay_dates"], channel)
    return {"result": _summary(made), "booking_id": made["booking_id"], "assigned_rooms": [], "aiosell": aiosell,
            "channel_manager": channel_manager, "guest_notice": made.get("guest_notice") or {}}


@router.post("/api/bookings/group")
async def api_bookings_group(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """One booking per room type line: {guest_phone, stay_dates, lines: [{room_type, rooms_booked}], booking_notes?}"""
    guest_phone = str(payload.get("guest_phone") or "").strip()
    stay_dates = payload.get("stay_dates") or []
    lines = payload.get("lines") or []
    if not guest_phone or not isinstance(stay_dates, list) or not stay_dates or not isinstance(lines, list) or not lines:
        return JSONResponse(content={"error": "guest_phone, stay_dates, and lines are required"}, status_code=400)
    stay = booking_manager.stay_range(stay_dates)
    guest = await run_in_threadpool(_desk_guest, s, guest_phone)
    if stay.get("error") or guest.get("error"):
        return _refused(stay if stay.get("error") else guest)
    wanted = [{"room_type_name": str(line.get("room_type") or "").strip().upper(), "rooms": int(line.get("rooms_booked") or 0)}
              for line in lines]
    made = await run_in_threadpool(booking_manager.create_group_booking, s["hotel_id"], s["user_id"], guest, stay["check_in"],
                                   stay["check_out"], wanted, notes=payload.get("booking_notes") or None)
    if made.get("error"):
        return _refused(made)
    aiosell = await _push_aiosell(s, made["stay_dates"])
    channel_manager = [await run_in_threadpool(_push_pms_booking, s["hotel_id"], item["booking_id"], guest["phone"], item["room_type_name"],
                                               item["stay_dates"], "PMS") for item in made["bookings"]]
    return {"result": "\n".join(_summary(item) for item in made["bookings"]), "booking_ids": made["booking_ids"], "aiosell": aiosell,
            "channel_manager": channel_manager}


@router.post("/api/bookings/modify")
async def api_bookings_modify(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """payload: {booking_id, new_rooms_booked?, new_stay_dates?, new_room_type?, channel?, booking_notes?}"""
    booking_id = str(payload.get("booking_id") or "").strip()
    if not booking_id:
        return JSONResponse(content={"error": "booking_id is required"}, status_code=400)
    changes = {name: payload[key] for key, name in MODIFY_FIELDS.items() if payload.get(key) not in (None, "")}
    if payload.get("new_stay_dates"):
        stay = booking_manager.stay_range(payload["new_stay_dates"])
        if stay.get("error"):
            return _refused(stay)
        changes.update(stay)
    result = await run_in_threadpool(booking_manager.modify_booking, s["hotel_id"], s["user_id"], booking_id, changes)
    if result.get("error"):
        return _refused(result)
    if payload.get("booking_notes") is not None:
        await run_in_threadpool(booking_manager.write_booking_notes, s["hotel_id"], s["user_id"], booking_id, payload["booking_notes"])
    aiosell = await _push_aiosell(s, result["stay_dates"]) if result["changed"] else {"skipped": "no change"}
    summary = "Booking " + booking_id + (" updated: " + ", ".join(result["changed"]) if result["changed"] else " unchanged")
    return {"result": summary, "changed": result["changed"], "aiosell": aiosell}


@router.post("/api/bookings/cancel")
async def api_bookings_cancel(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    booking_id = str(payload.get("booking_id") or "").strip()
    if not booking_id:
        return JSONResponse(content={"error": "booking_id is required"}, status_code=400)
    result = await run_in_threadpool(booking_manager.cancel_booking, s["hotel_id"], s["user_id"], booking_id, payload.get("reason"))
    if result.get("error"):
        return _refused(result)
    aiosell = await _push_aiosell(s, result["stay_dates"])
    return {"result": "Booking " + booking_id + " cancelled", "aiosell": aiosell}


# ---------- guests ----------

@router.get("/api/guest_profile")
def api_guest_profile(guest_phone: str = Query(...), s: dict = Depends(session)):
    """{profile: text, guest: record with total_bookings, total_spent, currency}; 404 'No guest profile found ...'."""
    return _refused(guest_manager.read_guest_profile(s["hotel_id"], guest_phone), 404)


@router.post("/api/guest_profile/create")
def api_guest_profile_create(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """payload: {guest_phone, guest_name, email?, nationality?}"""
    guest_phone = str(payload.get("guest_phone") or "").strip()
    guest_name = str(payload.get("guest_name") or "").strip()
    if not guest_phone or not guest_name:
        return JSONResponse(content={"error": "guest_phone and guest_name are required"}, status_code=400)
    made = guest_manager.create_guest_profile(s["hotel_id"], s["user_id"], guest_phone, guest_name, payload.get("email"),
                                              payload.get("nationality"))
    if made.get("error"):
        return _refused(made)
    from booking_system.guest_link import deliver_portal_link
    sent = deliver_portal_link(s["hotel_id"], made["guest_phone"], made["guest_name"])
    return dict(made, result="Guest profile created for " + made["guest_name"] + " (" + made["guest_phone"] + ")",
                portal_url=sent.get("portal_url") or "", whatsapp_error=sent.get("error") or "",
                whatsapp_message_id=sent.get("message_id") or "")


@router.post("/api/guest_profile/update")
def api_guest_profile_update(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """payload: {guest_phone, any of guest_name, nationality, vip, email and the profile text fields}"""
    guest_phone = str(payload.get("guest_phone") or "").strip()
    if not guest_phone:
        return JSONResponse(content={"error": "guest_phone is required"}, status_code=400)
    fields = {key: value for key, value in payload.items() if key not in ("guest_phone", "hotel_name")}
    if not fields:
        return JSONResponse(content={"error": "No fields to update"}, status_code=400)
    result = guest_manager.update_guest_profile(s["hotel_id"], s["user_id"], guest_phone, fields)
    return _refused(result) if result.get("error") else {"result": "Updated"}


@router.post("/api/guest_profile/deactivate")
def api_guest_profile_deactivate(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    guest_phone = str(payload.get("guest_phone") or "").strip()
    if not guest_phone:
        return JSONResponse(content={"error": "guest_phone is required"}, status_code=400)
    result = guest_manager.deactivate_guest(s["hotel_id"], s["user_id"], guest_phone)
    return _refused(result, 404) if result.get("error") else {"result": "Inactive"}


@router.get("/api/guests")
@router.get("/api/guests/search")
def api_guests_search(q: str = "", s: dict = Depends(session)):
    """Active guests at the hotel whose phone, name, or email contains q."""
    return {"success": True, "hotel_name": s["hotel_name"], "guests": guest_manager.search_guests(s["hotel_id"], q)}


@router.get("/api/guests/record")
def api_guests_record(guest_phone: str = Query(...), s: dict = Depends(session)):
    """One guest, with past, current, and future bookings and assigned rooms."""
    record = guest_manager.read_guest_with_bookings(s["hotel_id"], guest_phone, s["hotel_timezone"])
    if not record:
        return JSONResponse(content={"error": "Guest profile not found"}, status_code=404)
    return record


# ---------- desk payments ----------

class PaymentUpdatePayload(BaseModel):
    hotel_name: Optional[str] = None
    booking_id: str
    payment_amount: float
    payment_method: str
    reference: Optional[str] = None


@router.post("/api/payments/update")
def payments_update(payload: PaymentUpdatePayload, s: dict = Depends(session)):
    """Record a desk payment against a booking."""
    made = payment_manager.record_payment(s["hotel_id"], s["user_id"], payload.booking_id, payload.payment_amount, payload.payment_method,
                                          payload.reference)
    if made.get("error"):
        return _refused(made)
    return dict(made, result="Payment of " + made["currency"] + " " + format(made["amount"], ".2f") + " recorded on " + payload.booking_id)


class RefundPayload(BaseModel):
    hotel_name: Optional[str] = None
    booking_id: str
    payment_seq: int
    refund_type: Optional[str] = None


@router.post("/api/payments/refund")
def payments_refund(payload: RefundPayload, s: dict = Depends(session)):
    """Refund what is still left of the booking's payment number payment_seq."""
    made = payment_manager.record_refund(s["hotel_id"], s["user_id"], payload.booking_id, payload.payment_seq, payload.refund_type or None)
    if made.get("error"):
        return _refused(made)
    return dict(made, result="Payment " + str(payload.payment_seq) + " refunded: " + made["currency"] + " " + format(made["amount"], ".2f"))


class VoidPayload(BaseModel):
    payment_id: str


@router.post("/api/payments/void")
def payments_void(payload: VoidPayload, s: dict = Depends(session)):
    """Void a received payment or refund that was entered in error."""
    made = payment_manager.void_payment(s["hotel_id"], s["user_id"], payload.payment_id)
    if made.get("error"):
        return _refused(made)
    return dict(made, result="Payment " + payload.payment_id + " voided")


@router.get("/api/payments/ledger")
def payments_ledger(booking_id: str = Query(...), s: dict = Depends(session)):
    """Payments (with payment_seq and refunded status), refunds and the booking balance."""
    ledger = payment_manager.read_ledger(s["hotel_id"], booking_id)
    if ledger.get("error"):
        return JSONResponse(content=dict(ledger, booking_id=booking_id, entries=[]), status_code=404)
    return ledger
