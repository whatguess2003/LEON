import logging
from fastapi import FastAPI, Request, Path
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
import uvicorn
import psycopg2
import os
import json
from typing import List, Dict, Any
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')
MONGO_URL = os.getenv('MONGO_URL')
mongo_client = MongoClient(MONGO_URL)
buffer_col = mongo_client['aiosell']['buffer_raw']

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("aiosell_reservation.log"),
        logging.StreamHandler()
    ]
)

app = FastAPI()

def create_aiosell_buffer_room_nights_table():
    """Create the aiosell_buffer_room_nights table if it does not exist (canonical keys)."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS aiosell_buffer_room_nights (
            booking_id VARCHAR(64),
            hotel_name VARCHAR(255),
            room_type VARCHAR(255),
            stay_date DATE,
            status VARCHAR(32) DEFAULT 'pending',
            PRIMARY KEY (booking_id, hotel_name, room_type, stay_date)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

def create_hotel_room_code_mapping_table():
    """Create the hotel_room_code_mapping table for mapping OTA codes to canonical keys."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hotel_room_code_mapping (
            hotel_code VARCHAR(64),
            room_code VARCHAR(64),
            hotel_name VARCHAR(255),
            room_type VARCHAR(255),
            PRIMARY KEY (hotel_code, room_code)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

def lookup_canonical_keys(hotel_code: str, room_code: str) -> (str, str):
    """Look up canonical hotel_name and room_type from mapping table. Returns (hotel_name, room_type) or (None, None) if not found."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT hotel_name, room_type FROM hotel_room_code_mapping
        WHERE hotel_code = %s AND room_code = %s
    """, (hotel_code, room_code))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return row[0], row[1]
    else:
        return None, None

def store_raw_payload_mongo(payload: Dict[str, Any]):
    """Store the full raw Aiosell payload in MongoDB for audit and future mapping."""
    buffer_col.insert_one({
        "booking_id": payload.get("bookingId"),
        "received_at": datetime.utcnow(),
        "payload": payload
    })

def upsert_buffer_room_nights(payload: Dict[str, Any]):
    """For each room and each night, upsert a row in aiosell_buffer_room_nights (canonical keys)."""
    booking_id = payload.get('bookingId')
    hotel_code = payload.get('hotelCode')
    checkin = payload.get('checkin')
    checkout = payload.get('checkout')
    rooms = payload.get('rooms', [])
    status = 'pending'
    if not (booking_id and hotel_code and checkin and checkout and rooms):
        logging.warning(f"Missing required fields for buffer room nights: booking_id={booking_id}, hotel_code={hotel_code}, checkin={checkin}, checkout={checkout}, rooms={rooms}")
        return
    d0 = datetime.strptime(checkin, "%Y-%m-%d")
    d1 = datetime.strptime(checkout, "%Y-%m-%d")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    for room in rooms:
        room_code = room.get('roomCode')
        if not room_code:
            logging.warning(f"Missing roomCode in room entry for bookingId={booking_id}")
            continue
        hotel_name, room_type = lookup_canonical_keys(hotel_code, room_code)
        if not hotel_name or not room_type:
            logging.error(f"Mapping not found for hotel_code={hotel_code}, room_code={room_code}. Skipping.")
            continue
        for i in range((d1 - d0).days):
            stay_date = (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
            cur.execute("""
                INSERT INTO aiosell_buffer_room_nights (booking_id, hotel_name, room_type, stay_date, status)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (booking_id, hotel_name, room_type, stay_date) DO UPDATE SET
                    status = EXCLUDED.status
            """, (booking_id, hotel_name, room_type, stay_date, status))
    conn.commit()
    cur.close()
    conn.close()

def read_aiosell_buffer_booking(booking_id: str) -> dict:
    """
    Read buffer booking by booking_id from PostgreSQL and MongoDB.
    Returns a dict with all relevant info for front desk.
    """
    # 1. Get room nights from PostgreSQL
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT hotel_name, room_type, stay_date, status
        FROM aiosell_buffer_room_nights
        WHERE booking_id = %s
        ORDER BY stay_date
    """, (booking_id,))
    room_nights = [
        {
            "hotel_name": row[0],
            "room_type": row[1],
            "stay_date": row[2].strftime("%Y-%m-%d"),
            "status": row[3]
        } for row in cur.fetchall()
    ]
    cur.close()
    conn.close()
    # 2. Get full payload from MongoDB
    doc = buffer_col.find_one({"booking_id": booking_id})
    payload = doc["payload"] if doc else None
    # 3. Return combined info
    logging.info(f"Read buffer booking for booking_id={booking_id}")
    return {
        "room_nights": room_nights,
        "raw_payload": payload
    }

def mark_buffer_booking_imported(booking_id: str):
    """
    Mark all room nights for a buffer booking as imported in PostgreSQL and MongoDB.
    """
    # PostgreSQL
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        UPDATE aiosell_buffer_room_nights
        SET status = 'imported'
        WHERE booking_id = %s
    """, (booking_id,))
    conn.commit()
    cur.close()
    conn.close()
    # MongoDB
    buffer_col.update_one(
        {"booking_id": booking_id},
        {"$set": {"imported_at": datetime.utcnow(), "status": "imported"}}
    )
    logging.info(f"Marked buffer booking as imported for booking_id={booking_id}")

# Ensure buffer and mapping tables exist on startup
create_aiosell_buffer_room_nights_table()
create_hotel_room_code_mapping_table()

@app.post("/aiosell/reservation")
async def aiosell_reservation(request: Request):
    data = await request.json()
    logging.info("Aiosell reservation action=%s bookingId=%s", (data or {}).get("action"), (data or {}).get("bookingId"))
    try:
        from booking_system.aiosell_reservation import handle_reservation
        result = handle_reservation(data if isinstance(data, dict) else {})
        if not result.get("success"):
            return JSONResponse(content=result, status_code=400)
        return JSONResponse(content=result)
    except Exception as e:
        logging.exception("Error processing Aiosell reservation")
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=500)

@app.get("/aiosell/buffer/booking/{booking_id}")
async def api_read_aiosell_buffer_booking(booking_id: str = Path(...)):
    """API endpoint to read buffer booking by booking_id."""
    result = read_aiosell_buffer_booking(booking_id)
    if not result["room_nights"] and not result["raw_payload"]:
        return JSONResponse(content={"success": False, "message": f"No buffer booking found for booking_id {booking_id}"}, status_code=404)
    return JSONResponse(content={"success": True, "data": result})

@app.post("/aiosell/buffer/booking/{booking_id}/imported")
async def api_mark_buffer_booking_imported(booking_id: str = Path(...)):
    """API endpoint to mark buffer booking as imported."""
    mark_buffer_booking_imported(booking_id)
    return JSONResponse(content={"success": True, "message": f"Buffer booking {booking_id} marked as imported."})

if __name__ == "__main__":
    uvicorn.run("booking_system.aiosell_reservation_webhook:app", host="0.0.0.0", port=8005, reload=True) 