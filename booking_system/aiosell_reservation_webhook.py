"""Standalone Aiosell reservation service (port 8005): applies deliveries through channel_reservations and keeps a raw-payload
buffer per booking for the front desk (aiosell_buffer_room_nights: one row per hotel, Aiosell booking id, room type and night)."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Path, Request
from fastapi.responses import JSONResponse

load_dotenv()

from booking_system import db
from booking_system.content_store import client as store
from booking_system.hotel_registry import read_hotel_by_code

buffer_col = store["aiosell"]["buffer_raw"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("aiosell_reservation.log"), logging.StreamHandler()],
)

app = FastAPI()


def lookup_room_type(hotel_id: str, room_code: str):
    """The room_type_id a channel room code maps to at this hotel, or None."""
    found = db.row("SELECT room_type_id FROM hotel_room_code_mapping WHERE hotel_id = %s AND channel_room_code = %s", (hotel_id, room_code))
    return found["room_type_id"] if found else None


def store_raw_payload(payload: Dict[str, Any]):
    """Store the full raw Aiosell payload for audit and future mapping."""
    buffer_col.insert_one({"booking_id": payload.get("bookingId"), "received_at": datetime.now(timezone.utc), "payload": payload})


def upsert_buffer_room_nights(payload: Dict[str, Any]):
    """For each room and each night, upsert a row in aiosell_buffer_room_nights."""
    booking_id, checkin, checkout = payload.get("bookingId"), payload.get("checkin"), payload.get("checkout")
    rooms = payload.get("rooms", [])
    hotel = read_hotel_by_code(str(payload.get("hotelCode") or "").strip())
    if not (booking_id and hotel and checkin and checkout and rooms):
        logging.warning("Buffer room nights skipped for booking_id=%s: hotel, dates or rooms missing", booking_id)
        return
    d0, d1 = datetime.strptime(checkin, "%Y-%m-%d"), datetime.strptime(checkout, "%Y-%m-%d")
    nights = []
    for room in rooms:
        room_type = lookup_room_type(hotel["hotel_id"], str(room.get("roomCode") or ""))
        if not room_type:
            logging.error("Mapping not found for hotel %s room_code=%s. Skipping.", hotel["hotel_name"], room.get("roomCode"))
            continue
        nights += [(hotel["hotel_id"], booking_id, room_type, (d0 + timedelta(days=i)).strftime("%Y-%m-%d")) for i in range((d1 - d0).days)]

    def run(cur):
        for night in nights:
            cur.execute(
                """INSERT INTO aiosell_buffer_room_nights (hotel_id, channel_booking_ref, room_type_id, stay_date, status)
                   VALUES (%s, %s, %s, %s, 'pending')
                   ON CONFLICT (hotel_id, channel_booking_ref, room_type_id, stay_date) DO UPDATE SET status = EXCLUDED.status""",
                night,
            )
    db.transaction(run)


def read_aiosell_buffer_booking(booking_id: str) -> dict:
    """Room nights and the raw payload buffered for one Aiosell booking id."""
    rows = db.rows(
        """SELECT b.hotel_id, t.room_type_name AS room_type, b.stay_date, b.status FROM aiosell_buffer_room_nights b
           JOIN room_type t ON t.room_type_id = b.room_type_id WHERE b.channel_booking_ref = %s ORDER BY b.stay_date""",
        (booking_id,))
    doc = buffer_col.find_one({"booking_id": booking_id})
    return {"room_nights": [dict(row, stay_date=row["stay_date"].strftime("%Y-%m-%d")) for row in rows],
            "raw_payload": doc["payload"] if doc else None}


def mark_buffer_booking_imported(booking_id: str):
    db.execute("UPDATE aiosell_buffer_room_nights SET status = 'imported' WHERE channel_booking_ref = %s", (booking_id,))
    buffer_col.update_one({"booking_id": booking_id}, {"$set": {"imported_at": datetime.now(timezone.utc), "status": "imported"}})
    logging.info("Marked buffer booking as imported for booking_id=%s", booking_id)


@app.post("/aiosell/reservation")
async def aiosell_reservation(request: Request):
    data = await request.json()
    body = data if isinstance(data, dict) else {}
    logging.info("Aiosell reservation action=%s bookingId=%s", body.get("action"), body.get("bookingId"))
    try:
        from booking_system.channel_reservations import accept_webhook
        result = accept_webhook(body)
        return JSONResponse(content=result, status_code=200 if result.get("success") else 400)
    except Exception as e:
        logging.exception("Error processing Aiosell reservation")
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=500)


@app.get("/aiosell/buffer/booking/{booking_id}")
async def api_read_aiosell_buffer_booking(booking_id: str = Path(...)):
    result = read_aiosell_buffer_booking(booking_id)
    if not result["room_nights"] and not result["raw_payload"]:
        return JSONResponse(content={"success": False, "message": f"No buffer booking found for booking_id {booking_id}"}, status_code=404)
    return JSONResponse(content={"success": True, "data": result})


@app.post("/aiosell/buffer/booking/{booking_id}/imported")
async def api_mark_buffer_booking_imported(booking_id: str = Path(...)):
    mark_buffer_booking_imported(booking_id)
    return JSONResponse(content={"success": True, "message": f"Buffer booking {booking_id} marked as imported."})


if __name__ == "__main__":
    uvicorn.run("booking_system.aiosell_reservation_webhook:app", host="0.0.0.0", port=8005, reload=True)
