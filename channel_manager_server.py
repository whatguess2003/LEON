"""Channel manager. Aiosell posts reservations to /update_reservation."""

import logging
import os

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Body, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from booking_system import service_token

load_dotenv(override=True)

app = FastAPI()
_origins_env = os.getenv("FRONTEND_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080")
_origins_list = [o.strip() for o in _origins_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


def _mapping_response(result: dict):
    status = int((result or {}).get("status") or 0)
    if result.get("error") and status:
        return JSONResponse(content=result, status_code=status)
    if result.get("error") and "hotel_id" not in result and "room_codes" not in result and "room_types" not in result:
        return JSONResponse(content=result, status_code=400)
    return result


def _refused(request: Request, hotel_id: str):
    """Every endpoint except /health and the Aiosell webhook needs the main server's credential for this hotel_id."""
    reason = service_token.refusal(request.headers.get(service_token.HEADER), str(hotel_id or ""))
    if not reason:
        return None
    return JSONResponse(content={"error": reason}, status_code=503 if reason.startswith("CM_SERVICE_SECRET") else 401)


@app.get("/mapping")
def read_mapping(request: Request, hotel_id: str = Query("")):
    from booking_system.ota_mapping import read_mapping as load_mapping
    return _refused(request, hotel_id) or _mapping_response(load_mapping(hotel_id))


@app.post("/mapping")
def save_mapping(request: Request, payload: dict = Body(...)):
    from booking_system.ota_mapping import save_mapping as store_mapping
    body = payload or {}
    hotel_id = str(body.get("hotel_id") or "")
    return _refused(request, hotel_id) or _mapping_response(store_mapping(hotel_id, str(body.get("hotel_code") or "")))


@app.post("/room-mapping")
def save_room_mapping(request: Request, payload: dict = Body(...)):
    from booking_system.ota_mapping import save_room_mapping as store_room
    body = payload or {}
    hotel_id = str(body.get("hotel_id") or "")
    return _refused(request, hotel_id) or _mapping_response(store_room(hotel_id, str(body.get("room_code") or ""), str(body.get("room_type") or "")))


def _listed(request, hotel_id, direction):
    denied = _refused(request, hotel_id)
    if denied:
        return denied
    from booking_system.channel_reservations import read_list
    return read_list(str(hotel_id).strip(), direction)


@app.get("/reservations")
def read_reservations(request: Request, hotel_id: str = Query("")):
    return _listed(request, hotel_id, "reservation_out")


@app.get("/reservation-in")
def read_reservation_in(request: Request, hotel_id: str = Query("")):
    return _listed(request, hotel_id, "reservation_in")


@app.post("/reservation-in")
def push_reservation_in(request: Request, payload: dict = Body(...)):
    from booking_system.channel_reservations import accept_pms_booking
    denied = _refused(request, str((payload or {}).get("hotel_id") or ""))
    if denied:
        return denied
    result = accept_pms_booking(payload or {})
    if result.get("error"):
        return JSONResponse(content=result, status_code=400)
    return result


@app.post("/update_reservation")
async def update_reservation(request: Request, background_tasks: BackgroundTasks):
    """Aiosell posts book, modify, and cancel here. Basic auth uses the Aiosell username and password."""
    from booking_system.aiosell_reservation import authorize_basic
    from booking_system.channel_reservations import accept_webhook
    denied = authorize_basic(request.headers.get("authorization"))
    if denied:
        status = 401 if denied == "Unauthorized" or denied.startswith("Aiosell credentials") else 400
        return JSONResponse(content={"success": False, "message": denied}, status_code=status)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(content={"success": False, "message": "Invalid reservation payload"}, status_code=400)
    logging.info("Aiosell webhook action=%s bookingId=%s hotelCode=%s", (payload or {}).get("action"), (payload or {}).get("bookingId"), (payload or {}).get("hotelCode"))
    result = await run_in_threadpool(accept_webhook, payload if isinstance(payload, dict) else {})
    if result.get("success") and result.get("hotel_id"):
        from aiosell_channel_manager import push_dates
        background_tasks.add_task(push_dates, result["hotel_id"], list(result.get("stay_dates") or []))
    public = {"success": bool(result.get("success")), "message": str(result.get("message") or "")}
    if not public["success"]:
        return JSONResponse(content=public, status_code=400)
    return public


if __name__ == "__main__":
    port = int(os.environ.get("CHANNEL_MANAGER_PORT", "8006"))
    print("Channel manager on http://0.0.0.0:%s" % port)
    uvicorn.run("channel_manager_server:app", host="0.0.0.0", port=port, log_level="info")
