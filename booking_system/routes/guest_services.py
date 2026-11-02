"""Guest service routes: loyalty, preferences and requests, upgrade offers, concierge content, partner referrals, mobile
check-in with digital keys, and online booking search. The hotel is the session's hotel_id."""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from booking_system import concierge_manager, guest_engagement_manager as engagement, loyalty_manager as loyalty
from booking_system import mobile_checkin_manager as mobile, ota_agent, referral_agent
from booking_system.access import session

router = APIRouter()

ACCESS = [
    ("GET /api/loyalty/member", "front_desk", "view"), ("POST /api/loyalty/enroll", "front_desk", "operate"),
    ("POST /api/loyalty/award", "front_desk", "operate"), ("POST /api/loyalty/redeem", "front_desk", "operate"),
    ("GET /api/loyalty/setup", "front_desk", "view"), ("POST /api/loyalty/tiers", "front_desk", "manage"),
    ("POST /api/loyalty/rewards", "front_desk", "manage"),
    ("GET /api/guests/occasions", "front_desk", "view"), ("POST /api/guests/occasions", "front_desk", "operate"),
    ("GET /api/guests/preferences", "front_desk", "view"), ("POST /api/guests/preferences", "front_desk", "operate"),
    ("GET /api/guests/requests", "front_desk", "view"), ("POST /api/guests/requests", "front_desk", "operate"),
    ("POST /api/guests/complaints", "front_desk", "operate"), ("GET /api/bookings/upgrade_offers", "front_desk", "view"),
    ("GET /api/concierge", "front_desk", "view"), ("POST /api/concierge", "front_desk", "manage"),
    ("POST /api/concierge/delete", "front_desk", "manage"),
    ("GET /api/partnerships", "front_desk", "view"), ("POST /api/partnerships", "admin", "manage"),
    ("POST /api/referrals", "front_desk", "operate"),
    ("GET /api/mobile_checkin", "front_desk", "view"), ("POST /api/mobile_checkin/start", "front_desk", "operate"),
    ("POST /api/mobile_checkin/verify", "front_desk", "operate"), ("POST /api/mobile_checkin/key", "front_desk", "operate"),
    ("POST /api/mobile_checkin/key/revoke", "front_desk", "operate"),
    ("GET /api/online/search", "reservations", "view"), ("POST /api/online/book", "reservations", "operate"),
]


def _reply(result):
    if isinstance(result, dict) and result.get("error"):
        return JSONResponse(content=result, status_code=400)
    return result


def _int(payload, name, default=None):
    try:
        return int(payload.get(name, default))
    except (TypeError, ValueError):
        return None


# ---------- loyalty ----------

@router.get("/api/loyalty/member")
def loyalty_member(guest_phone: str = Query(...), s: dict = Depends(session)):
    return _reply(loyalty.member_status(s["hotel_id"], guest_phone))


@router.post("/api/loyalty/enroll")
def loyalty_enroll(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(loyalty.enroll(s["hotel_id"], s["user_id"], payload.get("guest_phone")))


@router.post("/api/loyalty/award")
def loyalty_award(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    points = _int(payload, "points")
    if points is None:
        return JSONResponse(content={"error": "points must be a whole number"}, status_code=400)
    return _reply(loyalty.award(s["hotel_id"], s["user_id"], payload.get("guest_phone"), points, payload.get("reason"), payload.get("booking_id")))


@router.post("/api/loyalty/redeem")
def loyalty_redeem(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(loyalty.redeem(s["hotel_id"], s["user_id"], payload.get("guest_phone"), payload.get("reward_name")))


@router.get("/api/loyalty/setup")
def loyalty_setup(s: dict = Depends(session)):
    return {"tiers": loyalty.tiers(s["hotel_id"]), "rewards": loyalty.rewards(s["hotel_id"])}


@router.post("/api/loyalty/tiers")
def loyalty_tier(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    points = _int(payload, "min_points")
    if points is None:
        return JSONResponse(content={"error": "min_points must be a whole number"}, status_code=400)
    return _reply(loyalty.save_tier(s["hotel_id"], payload.get("tier_name"), points))


@router.post("/api/loyalty/rewards")
def loyalty_reward(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    cost = _int(payload, "points_cost")
    if cost is None:
        return JSONResponse(content={"error": "points_cost must be a whole number"}, status_code=400)
    return _reply(loyalty.save_reward(s["hotel_id"], payload.get("reward_name"), cost, payload.get("status") or "active"))


@router.get("/api/guests/occasions")
def guest_occasions(guest_phone: str = Query(...), s: dict = Depends(session)):
    return _reply(loyalty.occasions(s["hotel_id"], guest_phone))


@router.post("/api/guests/occasions")
def guest_occasion(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(loyalty.save_occasion(s["hotel_id"], s["user_id"], payload.get("guest_phone"), payload.get("occasion"),
                                        payload.get("occasion_date"), payload.get("notes")))


# ---------- engagement ----------

@router.get("/api/guests/preferences")
def guest_preferences(guest_phone: str = Query(...), s: dict = Depends(session)):
    return _reply(engagement.preferences(s["hotel_id"], guest_phone))


@router.post("/api/guests/preferences")
def guest_preference(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(engagement.save_preference(s["hotel_id"], s["user_id"], payload.get("guest_phone"), payload.get("preference"), payload.get("value")))


@router.get("/api/guests/requests")
def guest_requests(s: dict = Depends(session)):
    return {"requests": engagement.open_requests(s["hotel_id"])}


@router.post("/api/guests/requests")
def guest_request(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(engagement.request_service(s["hotel_id"], s["user_id"], payload.get("guest_phone"), payload.get("request"),
                                             payload.get("requested_for"), payload.get("booking_id")))


@router.post("/api/guests/complaints")
def guest_complaint(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(engagement.record_complaint(s["hotel_id"], s["user_id"], payload.get("guest_phone"), payload.get("complaint"),
                                              payload.get("booking_id")))


@router.get("/api/bookings/upgrade_offers")
def upgrade_offers(booking_id: str = Query(...), s: dict = Depends(session)):
    return _reply(engagement.upgrade_offers(s["hotel_id"], booking_id))


# ---------- concierge ----------

@router.get("/api/concierge")
def concierge_list(kind: str = Query(""), s: dict = Depends(session)):
    return _reply(concierge_manager.list_items(s["hotel_id"], kind))


@router.post("/api/concierge")
def concierge_save(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(concierge_manager.save_item(s["hotel_id"], s["user_id"], payload.get("kind"), payload.get("name"), payload.get("fields") or {}))


@router.post("/api/concierge/delete")
def concierge_delete(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(concierge_manager.delete_item(s["hotel_id"], payload.get("kind"), payload.get("name")))


# ---------- referrals ----------

@router.get("/api/partnerships")
def partnerships(s: dict = Depends(session)):
    return {"partnerships": referral_agent.partnerships(s["hotel_id"])}


@router.post("/api/partnerships")
def partnership_save(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(referral_agent.save_partnership(s["hotel_id"], s["user_id"], payload.get("partner_hotel"), payload.get("status") or "active",
                                                  payload.get("notes")))


@router.post("/api/referrals")
def referral(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(referral_agent.refer_guest(s["hotel_id"], s["user_id"], payload.get("guest_phone"), payload.get("partner_hotel"),
                                             payload.get("reason")))


# ---------- mobile check-in ----------

@router.get("/api/mobile_checkin")
def mobile_status(booking_id: str = Query(...), s: dict = Depends(session)):
    return _reply(mobile.status(s["hotel_id"], booking_id))


@router.post("/api/mobile_checkin/start")
def mobile_start(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(mobile.start(s["hotel_id"], s["user_id"], payload.get("booking_id")))


@router.post("/api/mobile_checkin/verify")
def mobile_verify(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(mobile.verify(s["hotel_id"], s["user_id"], payload.get("booking_id")))


@router.post("/api/mobile_checkin/key")
def mobile_key(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(mobile.issue_key(s["hotel_id"], s["user_id"], payload.get("booking_id"), payload.get("room_number")))


@router.post("/api/mobile_checkin/key/revoke")
def mobile_key_revoke(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(mobile.revoke_key(s["hotel_id"], s["user_id"], payload.get("key_id")))


# ---------- online booking ----------

@router.get("/api/online/search")
def online_search(check_in: str = Query(...), check_out: str = Query(...), rooms: int = Query(1), s: dict = Depends(session)):
    return _reply(ota_agent.search(s["hotel_id"], check_in, check_out, rooms))


@router.post("/api/online/book")
def online_book(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    rooms, adults = _int(payload, "rooms", 1), _int(payload, "adults", 1)
    if rooms is None or adults is None:
        return JSONResponse(content={"error": "rooms and adults must be whole numbers"}, status_code=400)
    return _reply(ota_agent.book(s["hotel_id"], s["user_id"], payload.get("channel"), payload.get("guest_name"), payload.get("guest_phone"),
                                 payload.get("room_type"), payload.get("check_in"), payload.get("check_out"), rooms, adults,
                                 payload.get("channel_booking_ref"), payload.get("email")))
