"""Rooms, room types, inventory, pricing, rate plans, Kite and dynamic pricing routes. The hotel is the session's hotel_id; a
hotel_name the UI still sends is ignored, and a {hotel_id} path segment must be the session's (the guard refuses another)."""

from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import booking_system.cm_path
from aiosell_channel_manager import push_dates, push_rates_for_date, push_rates_for_dates
from booking_system import dynamic_pricing, hotel_registry, inventory_manager, room_manager
from booking_system.access import session
from booking_system.kite_pricing import kite_pricing

router = APIRouter()

ACCESS = [
    ("GET /api/hotels", "front_desk", "view"),
    ("GET /api/hotels/rooms", "front_desk", "view"),
    ("GET /api/hotels/available-rooms", "front_desk", "view"),
    ("GET /api/hotels/{hotel_id}", "front_desk", "view"),
    ("GET /api/property/templates", "front_desk", "view"),
    ("POST /api/property/templates/apply", "admin", "operate"),
    ("POST /api/room_types/manage", "admin", "operate"),
    ("POST /api/room_types/delete", "admin", "operate"),
    ("POST /api/rooms/add", "admin", "operate"),
    ("POST /api/rooms/remove", "admin", "operate"),
    ("POST /api/rooms/types/create", "admin", "operate"),
    ("POST /api/rooms/labels", "admin", "operate"),
    ("POST /api/rooms/rename", "admin", "operate"),
    ("GET /api/availability", "reservations", "view"),
    ("GET /api/rate_plans", "reservations", "view"),
    ("POST /api/rate_plans", "admin", "operate"),
    ("POST /api/rate_plans/update", "admin", "operate"),
    ("POST /api/rate_plans/delete", "admin", "operate"),
    ("GET /api/analytics", "reporting", "view"),
    ("GET /api/hotels/{hotel_id}/analytics", "reporting", "view"),
    ("POST /api/hotels/{hotel_id}/update-room-status", "housekeeping", "operate"),
    ("GET /api/inventory", "reservations", "view"),
    ("GET /api/hotels/{hotel_id}/pricing", "reservations", "view"),
    ("POST /api/hotels/{hotel_id}/inventory/update", "reservations", "operate"),
    ("POST /api/hotels/{hotel_id}/pricing/update", "reservations", "operate"),
    ("POST /api/hotels/{hotel_id}/inventory/update-date", "reservations", "operate"),
    ("POST /api/hotels/{hotel_id}/inventory/update-dates", "reservations", "operate"),
    ("POST /api/hotels/{hotel_id}/inventory/night-rules", "reservations", "operate"),
    ("POST /api/hotels/{hotel_id}/inventory/allotment", "reservations", "operate"),
    ("POST /api/hotels/{hotel_id}/inventory/allotments/release", "reservations", "operate"),
    ("GET /api/kite", "reservations", "view"),
    ("POST /api/kite", "reservations", "operate"),
    ("POST /api/kite/day", "reservations", "operate"),
    ("POST /api/kite/days", "reservations", "operate"),
    ("GET /api/dynamic_pricing", "reservations", "view"),
    ("POST /api/dynamic_pricing", "reservations", "operate"),
    ("DELETE /api/dynamic_pricing", "reservations", "operate"),
]


def _refused(result):
    """The result, or a JSON error response when it carries 'error' (status from the result, else 400)."""
    if isinstance(result, dict) and result.get("error"):
        body = dict(result)
        return JSONResponse(content=body, status_code=int(body.pop("status", 400)))
    return result


def _numbers(single, many):
    return list(many or []) + ([str(single)] if single else [])


# ---------- hotel and rooms (reads) ----------

@router.get("/api/hotels")
def get_hotels(s: dict = Depends(session)):
    profile = hotel_registry.profile_col.find_one({"hotel_id": s["hotel_id"]}) or {}
    return {"hotels": [{"id": s["hotel_id"], "name": s["hotel_name"], "location": profile.get("city_name") or "",
                        "manager": s["user_name"], "status": "active"}]}


@router.get("/api/hotels/rooms")
def get_hotel_rooms(room_type: str = Query(""), s: dict = Depends(session)):
    """Room types with their rooms and readiness."""
    return dict(room_manager.read_portfolio(s["hotel_id"], room_type), hotel_name=s["hotel_name"], hotel_id=s["hotel_id"])


@router.get("/api/hotels/available-rooms")
def get_available_rooms(room_type: str = Query(""), stay_date: str = Query(""), s: dict = Depends(session)):
    """Room numbers of a type with no live night and no block on stay_date."""
    if not room_type or not stay_date:
        return JSONResponse(content={"available": [], "error": "Missing parameters"}, status_code=400)
    found = inventory_manager.read_free_rooms(s["hotel_id"], room_type, stay_date)
    if found.get("error"):
        return JSONResponse(content={"available": [], "error": found["error"]}, status_code=400)
    return {"hotel_name": s["hotel_name"], "room_type": room_type.strip().upper(), "stay_date": stay_date, "available": found["available"]}


@router.get("/api/property/templates")
def api_property_templates(s: dict = Depends(session)):
    """READ canonical templates (keywords/facilities/amenities) prefilled from DB. REQUIRED before apply."""
    from booking_system.hotel_info_manager import read_property_templates
    return read_property_templates(s["hotel_id"])


@router.post("/api/property/templates/apply")
def api_property_templates_apply(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """APPLY templates. Must be preceded by /api/property/templates read. Overwrites only provided templates."""
    from booking_system.hotel_info_manager import write_property_templates
    applied = write_property_templates(s["hotel_id"], s["user_id"], payload.get("facilities_template"),
                                       payload.get("amenities_template"), payload.get("keywords_template"))
    if applied.get("error"):
        return JSONResponse(content=applied, status_code=400)
    return applied


@router.get("/api/hotels/{hotel_id}")
def get_hotel(hotel_id: str, s: dict = Depends(session)):
    profile = hotel_registry.profile_col.find_one({"hotel_id": s["hotel_id"]}) or {}
    return {"id": s["hotel_id"], "name": s["hotel_name"], "location": profile.get("city_name") or "", "manager": s["user_name"],
            "phone": "", "status": "active", "currency": s["hotel_currency"], "timezone": s["hotel_timezone"]}


# ---------- room types and rooms (writes) ----------

@router.post("/api/room_types/manage")
def manage_room_type_specs(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """UPDATE a room type's specifications and description."""
    room_type = str(payload.get("room_type") or "").strip().upper()
    specifications = payload.get("specifications")
    if not room_type or not isinstance(specifications, dict):
        return JSONResponse(content={"error": "room_type and specifications are required"}, status_code=400)
    result = room_manager.update_room_type_profile(s["hotel_id"], s["user_id"], room_type, specifications)
    return _refused(result) if result.get("error") else dict(result, result="Updated " + room_type)


@router.post("/api/room_types/delete")
def delete_room_type_api(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """DELETE a room type with its rooms, inventory and prices (refused while bookings use it)."""
    room_type = str(payload.get("room_type") or "").strip().upper()
    if not room_type:
        return JSONResponse(content={"error": "room_type is required"}, status_code=400)
    result = room_manager.delete_room_type(s["hotel_id"], s["user_id"], room_type)
    return _refused(result) if result.get("error") else dict(result, result="Deleted room type " + room_type)


class AddRoomPayload(BaseModel):
    hotel_name: Optional[str] = None
    room_type: str
    room_number: Optional[str] = None
    room_numbers: Optional[List[str]] = None


@router.post("/api/rooms/add")
def api_add_room(payload: AddRoomPayload, s: dict = Depends(session)):
    numbers = _numbers(payload.room_number, payload.room_numbers)
    result = room_manager.add_rooms(s["hotel_id"], s["user_id"], s["hotel_timezone"], payload.room_type, numbers)
    if result.get("error"):
        return _refused(result)
    return dict(result, result="Room " + ", ".join(result["room_numbers"]) + " added to " + result["room_type"])


class RemoveRoomPayload(BaseModel):
    hotel_name: Optional[str] = None
    room_type: str
    room_number: Optional[str] = None
    room_numbers: Optional[List[str]] = None


@router.post("/api/rooms/remove")
def api_remove_room(payload: RemoveRoomPayload, s: dict = Depends(session)):
    numbers = _numbers(payload.room_number, payload.room_numbers)
    result = room_manager.remove_rooms(s["hotel_id"], s["user_id"], s["hotel_timezone"], payload.room_type, numbers)
    if result.get("error"):
        return _refused(dict(result, removed=[]))
    return {"result": "removed", "removed": result["removed"], "rooms": result["rooms"]}


class CreateRoomTypePayload(BaseModel):
    hotel_name: Optional[str] = None
    room_type: str
    specifications: Dict[str, Any] = {}
    room_numbers: Optional[List[str]] = None
    base_price: float = 0.0


@router.post("/api/rooms/types/create")
def api_create_room_type(payload: CreateRoomTypePayload, s: dict = Depends(session)):
    """CREATE a room type with its rooms; nights and prices (base_price) are seeded for the inventory horizon."""
    result = room_manager.create_room_type(s["hotel_id"], s["user_id"], s["hotel_timezone"], payload.room_type, payload.specifications,
                                           payload.room_numbers, payload.base_price)
    if result.get("error"):
        return _refused(result)
    return dict(result, result="Created room type " + result["room_type"])


class RoomLabelItem(BaseModel):
    room_number: str
    room_label: Optional[str] = None


class RoomLabelsPayload(BaseModel):
    hotel_name: Optional[str] = None
    room_type: str
    labels: List[RoomLabelItem] = []


@router.post("/api/rooms/labels")
def api_room_labels(payload: RoomLabelsPayload, s: dict = Depends(session)):
    return _refused(room_manager.set_room_labels(s["hotel_id"], s["user_id"], payload.room_type, [item.dict() for item in payload.labels]))


class RoomNumberChange(BaseModel):
    room_number: str
    new_room_number: str


class RenameRoomsPayload(BaseModel):
    hotel_name: Optional[str] = None
    room_type: str
    rooms: List[RoomNumberChange] = []


@router.post("/api/rooms/rename")
def api_rename_rooms(payload: RenameRoomsPayload, s: dict = Depends(session)):
    return _refused(room_manager.rename_rooms(s["hotel_id"], s["user_id"], payload.room_type, [item.dict() for item in payload.rooms]))


# ---------- availability, rate plans, analytics ----------

@router.get("/api/availability")
def api_availability(room_type: str = Query(...), stay_date: str = Query(...), rooms_needed: int = Query(1), s: dict = Depends(session)):
    """{available, total, blocked, booked, stop_sell, requested} for one night of a room type."""
    found = inventory_manager.read_availability(s["hotel_id"], room_type, stay_date)
    return _refused(dict(found, requested=int(rooms_needed)))


@router.get("/api/rate_plans")
def list_rate_plans(s: dict = Depends(session)):
    return {"rate_plans": room_manager.read_rate_plans(s["hotel_id"])}


@router.post("/api/rate_plans")
def save_rate_plan(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    result = room_manager.save_rate_plan(s["hotel_id"], s["user_id"], payload.get("rate_plan"), payload.get("description"))
    return _refused(result) if result.get("error") else dict(result, hotel_name=s["hotel_name"])


@router.post("/api/rate_plans/update")
def update_rate_plan(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Rename or rewrite a rate plan; bookings follow the new name."""
    plan = payload.get("rate_plan")
    result = room_manager.rename_rate_plan(s["hotel_id"], s["user_id"], plan, payload.get("new_rate_plan") or plan, payload.get("description"))
    return _refused(result) if result.get("error") else dict(result, hotel_name=s["hotel_name"])


@router.post("/api/rate_plans/delete")
def delete_rate_plan(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Remove a rate plan. Bookings that used it are cleared."""
    result = room_manager.delete_rate_plan(s["hotel_id"], s["user_id"], payload.get("rate_plan"))
    return _refused(result) if result.get("error") else dict(result, hotel_name=s["hotel_name"])


@router.get("/api/analytics")
def api_analytics(days: int = 30, s: dict = Depends(session)):
    """Occupancy and room revenue for the signed-in hotel."""
    return inventory_manager.read_hotel_analytics(s["hotel_id"], s["hotel_timezone"], days)


@router.get("/api/hotels/{hotel_id}/analytics")
def get_hotel_analytics(hotel_id: str, days: int = 30, s: dict = Depends(session)):
    return inventory_manager.read_hotel_analytics(s["hotel_id"], s["hotel_timezone"], days)


@router.post("/api/hotels/{hotel_id}/update-room-status")
def update_room_status(hotel_id: str, request: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Placeholder kept from the old route: it records nothing."""
    return {"success": True, "message": "Room " + str(request.get("room_id")) + " status updated to " + str(request.get("status"))}


# ---------- inventory and pricing ----------

@router.get("/api/inventory")
def get_inventory(start_date: str = None, end_date: str = None, s: dict = Depends(session)):
    """Prices (with Kite quotes) and nightly quantities per room type. Without dates: the next 7 hotel days."""
    start = start_date or inventory_manager.hotel_today(s["hotel_timezone"]).isoformat()
    end = end_date or (inventory_manager._day(start) + timedelta(days=6)).isoformat()
    calendar = inventory_manager.read_calendar(s["hotel_id"], start, end)
    if calendar.get("error"):
        return calendar
    kite_pricing.attach_quotes(s["hotel_id"], start, end, calendar["room_types"])
    for room in calendar["room_types"]:
        for row in room["pricing"]:
            row["currency"] = s["hotel_currency"]
    return calendar


@router.get("/api/hotels/{hotel_id}/pricing")
def get_hotel_pricing(hotel_id: str, room_type: str = None, days: int = 7, start_date: str = None, s: dict = Depends(session)):
    """With room_type and start_date: {price, currency} for that night. Otherwise {pricing: [summary]} over `days`."""
    if not room_type:
        return {"pricing": []}
    start = start_date or inventory_manager.hotel_today(s["hotel_timezone"]).isoformat()
    span = max(int(days or 1), 1)
    end = start if start_date else (inventory_manager._day(start) + timedelta(days=span - 1)).isoformat()
    found = inventory_manager.read_prices(s["hotel_id"], room_type, start, end)
    if found.get("error"):
        return _refused(found)
    prices = found["prices"]
    if start_date:
        price = prices.get(inventory_manager._day(start).isoformat())
        missing = {} if price is not None else {"error": "No price for " + found["room_type"] + " on " + start}
        return dict({"price": price, "currency": s["hotel_currency"]}, **missing)
    sample = next(iter(prices.values()), None)
    return {"pricing": [{"room_type": found["room_type"], "has_pricing": bool(prices), "sample_price": sample,
                         "currency": s["hotel_currency"], "period": str(span) + " days"}]}


@router.post("/api/hotels/{hotel_id}/inventory/update")
def update_hotel_inventory(hotel_id: str, request: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Totals follow the rooms of each room type; quantities change per night through inventory/update-dates."""
    return {"success": False, "error": "Inventory totals follow the rooms of each room type; set nights with inventory/update-dates"}


@router.post("/api/hotels/{hotel_id}/pricing/update")
def update_hotel_pricing(hotel_id: str, request: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Price a room type from start_date to end_date (nights with inventory)."""
    result = inventory_manager.set_prices(s["hotel_id"], s["user_id"], request.get("room_type"), request.get("start_date"),
                                          request.get("end_date"), request.get("price"))
    if result.get("error"):
        return {"success": False, "error": result["error"]}
    return dict(result, message="Priced " + str(len(result["dates"])) + " nights", aiosell=push_dates(s["hotel_id"], result["dates"]))


@router.post("/api/hotels/{hotel_id}/inventory/update-date")
async def update_inventory_date(hotel_id: str, request: Request, s: dict = Depends(session)):
    """Price and quantity for one night. Quantity 0 stops sale; total follows the room count."""
    from starlette.concurrency import run_in_threadpool
    data = await request.json()
    room_type, day, price = data.get("room_type"), data.get("date"), data.get("price")
    if not room_type or not day or price is None:
        return {"success": False, "error": "Missing required fields"}
    result = await run_in_threadpool(inventory_manager.update_dates, s["hotel_id"], s["user_id"], room_type, [day], price, data.get("quantity"))
    if result.get("error"):
        return {"success": False, "error": result["error"]}
    aiosell = await run_in_threadpool(push_dates, s["hotel_id"], [str(day)[:10]])
    return {"success": True, "message": "Updated " + str(room_type).upper() + " for " + str(day) + ": " + str(result["rooms"]) + " rooms at " + str(price),
            "aiosell": aiosell}


@router.post("/api/hotels/{hotel_id}/inventory/update-dates")
async def update_inventory_dates(hotel_id: str, request: Request, s: dict = Depends(session)):
    """Price and quantity for many nights in one transaction."""
    from starlette.concurrency import run_in_threadpool
    data = await request.json()
    room_type, dates, price = data.get("room_type"), data.get("dates"), data.get("price")
    if not room_type or not isinstance(dates, list) or not dates or price is None:
        return {"success": False, "error": "Missing required fields"}
    result = await run_in_threadpool(inventory_manager.update_dates, s["hotel_id"], s["user_id"], room_type, dates, price, data.get("quantity"))
    if result.get("error"):
        return {"success": False, "error": result["error"]}
    result["aiosell"] = await run_in_threadpool(push_dates, s["hotel_id"], dates)
    return result


@router.post("/api/hotels/{hotel_id}/inventory/night-rules")
def inventory_night_rules(hotel_id: str, request: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Stop-sell, minimum stay and closed-to-arrival on one night that already has inventory."""
    result = inventory_manager.update_night_rules(s["hotel_id"], s["user_id"], request.get("room_type"), request.get("stay_date"),
                                                  request.get("stop_sell"), request.get("min_stay"), request.get("closed_to_arrival"))
    if result.get("error"):
        return {"success": False, "error": result["error"]}
    return result


@router.post("/api/hotels/{hotel_id}/inventory/allotment")
def inventory_allotment(hotel_id: str, request: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Hold rooms for one channel on one night."""
    result = inventory_manager.save_allotment(s["hotel_id"], s["user_id"], request.get("channel"), request.get("room_type"),
                                              request.get("stay_date"), request.get("rooms_allotted"), request.get("release_date"))
    if result.get("error"):
        return {"success": False, "error": result["error"]}
    return result


@router.post("/api/hotels/{hotel_id}/inventory/allotments/release")
def inventory_release_allotments(hotel_id: str, s: dict = Depends(session)):
    """Stamp allotments whose release date has arrived. Availability already ignores those nights."""
    return inventory_manager.release_allotments(s["hotel_id"], s["user_id"], inventory_manager.hotel_today(s["hotel_timezone"]))


# ---------- Kite and named dynamic pricing ----------

@router.get("/api/kite")
def read_kite_setting(s: dict = Depends(session)):
    """Read hotel-wide INAP dynamic pricing controls."""
    return kite_pricing.read_setting(s["hotel_id"])


@router.post("/api/kite")
def save_kite_setting(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Save the hotel-wide switch, sensitivity, and price limits."""
    return _refused(kite_pricing.save_setting(s["hotel_id"], s["user_id"], bool(payload.get("enabled")), payload.get("sensitivity"),
                                              payload.get("lower_limit"), payload.get("upper_limit")))


def _named_pricing_missing(s, pricing_name):
    return bool(pricing_name) and bool(dynamic_pricing.read_one(s["hotel_id"], pricing_name).get("missing"))


@router.post("/api/kite/day")
def save_kite_push_day(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Choose whether one stay date pushes the dynamic price or the base price."""
    stay_date = str(payload.get("stay_date") or "").strip()
    pricing_name = str(payload.get("pricing_name") or "").strip()
    if not stay_date:
        return JSONResponse(content={"error": "stay_date is required"}, status_code=400)
    if _named_pricing_missing(s, pricing_name):
        return JSONResponse(content={"error": "No dynamic pricing with that name"}, status_code=400)
    saved = kite_pricing.set_push_day(s["hotel_id"], stay_date, bool(payload.get("push_dynamic")), pricing_name or None)
    saved["aiosell"] = push_rates_for_date(s["hotel_id"], stay_date)
    return saved


@router.post("/api/kite/days")
def save_kite_push_days(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Set the dynamic-or-base choice for many stay dates at once."""
    stay_dates = payload.get("stay_dates")
    pricing_name = str(payload.get("pricing_name") or "").strip()
    if not isinstance(stay_dates, list) or not stay_dates:
        return JSONResponse(content={"error": "stay_dates is required"}, status_code=400)
    if _named_pricing_missing(s, pricing_name):
        return JSONResponse(content={"error": "No dynamic pricing with that name"}, status_code=400)
    saved = kite_pricing.set_push_dates(s["hotel_id"], stay_dates, bool(payload.get("push_dynamic")), pricing_name or None)
    saved["aiosell"] = push_rates_for_dates(s["hotel_id"], saved["stay_dates"])
    return saved


@router.get("/api/dynamic_pricing")
def list_dynamic_pricing(s: dict = Depends(session)):
    """List named dynamic pricing setups for this hotel."""
    return {"pricings": dynamic_pricing.list_for_hotel(s["hotel_id"])}


@router.post("/api/dynamic_pricing")
def save_dynamic_pricing(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Create a named pricing, or update it when the name already exists."""
    result = dynamic_pricing.save(s["hotel_id"], s["user_id"], payload.get("pricing_name"), payload.get("model"), bool(payload.get("enabled")),
                                  payload.get("sensitivity"), payload.get("lower_limit"), payload.get("upper_limit"))
    if result.get("clarify"):
        return JSONResponse(content=result, status_code=409)
    return _refused(result)


@router.delete("/api/dynamic_pricing")
def remove_dynamic_pricing(pricing_name: str, s: dict = Depends(session)):
    """Delete one named pricing when the name matches; stay dates that used it go back to the base price."""
    result = dynamic_pricing.delete(s["hotel_id"], pricing_name)
    if result.get("error"):
        return _refused(result)
    kite_pricing.clear_pricing_name(s["hotel_id"], result["deleted"])
    return result
