"""Property setup routes: booking / payment / tax policies and the guest registration card. The hotel is the session's hotel_id."""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from booking_system import policy_manager
from booking_system.access import session

router = APIRouter()

ACCESS = [
    ("GET /api/property/policies", "admin", "view"), ("POST /api/property/policies/create", "admin", "operate"),
    ("POST /api/property/policies/update", "admin", "operate"),
    ("GET /api/property/registration_card", "front_desk", "view"), ("POST /api/property/registration_card", "admin", "operate"),
]


def _reply(result):
    if isinstance(result, dict) and result.get("error"):
        return JSONResponse(content=result, status_code=400)
    return result


@router.get("/api/property/policies")
def read_policies(policy_type: str = Query(""), s: dict = Depends(session)):
    return _reply(policy_manager.read_policies(s["hotel_id"], policy_type or None))


@router.post("/api/property/policies/create")
def create_policy(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(policy_manager.create_policy(s["hotel_id"], s["user_id"], payload.get("policy_type"), payload.get("policy_details"),
                                               payload.get("description")))


@router.post("/api/property/policies/update")
def update_policy(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(policy_manager.update_policy(s["hotel_id"], s["user_id"], payload.get("policy_type"), payload.get("updates")))


@router.get("/api/property/registration_card")
def read_registration_card(s: dict = Depends(session)):
    return policy_manager.read_registration_card(s["hotel_id"])


@router.post("/api/property/registration_card")
def save_registration_card(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    return _reply(policy_manager.save_registration_card(s["hotel_id"], s["user_id"], payload.get("house_rules"), payload.get("terms")))
