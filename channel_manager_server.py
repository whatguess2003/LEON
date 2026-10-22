"""Channel manager. Polls Aiosell every 300 seconds and keeps the reservation list."""

import asyncio
import logging
import os

import uvicorn
from dotenv import load_dotenv
from fastapi import Body, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

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
POLL_SECONDS = 300


def _sync_once():
    from booking_system.channel_reservations import sync_linked_hotels
    return sync_linked_hotels()


async def _poll():
    await asyncio.sleep(5)
    while True:
        try:
            await run_in_threadpool(_sync_once)
        except Exception:
            logging.exception("Channel manager poll failed")
        try:
            await asyncio.sleep(POLL_SECONDS)
        except Exception:
            break


@app.on_event("startup")
async def startup():
    from booking_system.channel_reservations import ensure_tables
    await run_in_threadpool(ensure_tables)
    asyncio.get_event_loop().create_task(_poll())


@app.get("/health")
def health():
    return {"ok": True}


def _mapping_response(result: dict):
    status = int((result or {}).get("status") or 0)
    if result.get("error") and status:
        return JSONResponse(content=result, status_code=status)
    if result.get("error") and "hotel_name" not in result and "room_codes" not in result and "room_types" not in result:
        return JSONResponse(content=result, status_code=400)
    return result


@app.get("/mapping")
def read_mapping(hotel_name: str = Query("")):
    from booking_system.ota_mapping import read_mapping as load_mapping
    return _mapping_response(load_mapping(hotel_name))


@app.post("/mapping")
def save_mapping(payload: dict = Body(...)):
    from booking_system.ota_mapping import save_mapping as store_mapping
    body = payload or {}
    return _mapping_response(store_mapping(str(body.get("hotel_name") or ""), str(body.get("hotel_code") or "")))


@app.post("/room-mapping")
def save_room_mapping(payload: dict = Body(...)):
    from booking_system.ota_mapping import save_room_mapping as store_room
    body = payload or {}
    return _mapping_response(store_room(str(body.get("hotel_name") or ""), str(body.get("room_code") or ""), str(body.get("room_type") or "")))


@app.get("/reservations")
def read_reservations(hotel_name: str = Query("")):
    hotel = str(hotel_name or "").strip()
    if not hotel:
        return JSONResponse(content={"error": "hotel_name is required"}, status_code=400)
    from booking_system.channel_reservations import read_list
    return read_list(hotel, "reservation_out")


@app.get("/reservation-in")
def read_reservation_in(hotel_name: str = Query("")):
    hotel = str(hotel_name or "").strip()
    if not hotel:
        return JSONResponse(content={"error": "hotel_name is required"}, status_code=400)
    from booking_system.channel_reservations import read_list
    return read_list(hotel, "reservation_in")


@app.post("/reservation-in")
def push_reservation_in(payload: dict = Body(...)):
    from booking_system.channel_reservations import accept_pms_booking
    result = accept_pms_booking(payload or {})
    if result.get("error"):
        return JSONResponse(content=result, status_code=400)
    return result


@app.post("/sync")
def sync_now():
    return _sync_once()


@app.get("/health")
def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    port = int(os.environ.get("CHANNEL_MANAGER_PORT", "8006"))
    print("Channel manager on http://0.0.0.0:%s" % port)
    uvicorn.run("channel_manager_server:app", host="0.0.0.0", port=port, log_level="info")
