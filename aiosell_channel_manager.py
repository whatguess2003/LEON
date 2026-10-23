import os
from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
from typing import List, Dict, Any

PROPERTY_DETAILS_URL = "https://live.aiosell.com/api/v2/cm/property_details/{hotel_code}"
INVENTORY_URL = "https://live.aiosell.com/api/v2/cm/update/{partner}"
RATE_URL = "https://live.aiosell.com/api/v2/cm/update-rates/{partner}"
def read_property_details(hotel_code: str) -> dict:
    """GET Aiosell property details. Username and password are basic auth. Partner id is the URL partnerId."""
    username = str(os.environ.get("AIOSSELL_USERNAME") or "").strip()
    password = str(os.environ.get("AIOSSELL_PASSWORD") or "")
    partner = str(os.environ.get("AIOSSELL_PARTNER_ID") or "").strip()
    code = str(hotel_code or "").strip()
    missing = [name for name, value in (("AIOSSELL_USERNAME", username), ("AIOSSELL_PASSWORD", password), ("AIOSSELL_PARTNER_ID", partner)) if not value]
    if missing:
        return {"error": ", ".join(missing) + " not configured"}
    if not code:
        return {"error": "Hotel code is required"}
    url = PROPERTY_DETAILS_URL.format(hotel_code=quote(code, safe=""))
    try:
        response = requests.get(
            url,
            params={"partnerId": partner},
            auth=HTTPBasicAuth(username, password),
            timeout=(5, 20),
        )
    except requests.RequestException as exc:
        return {"error": str(exc)}
    if response.status_code != 200:
        return {"error": "Aiosell responded " + str(response.status_code) + ": " + response.text[:300]}
    try:
        body = response.json()
    except ValueError:
        return {"error": "Aiosell response was not JSON"}
    if not isinstance(body, dict):
        return {"error": "Aiosell response was not a property record"}
    return {"property": body}

def _credentials():
    username = str(os.environ.get("AIOSSELL_USERNAME") or "").strip()
    password = str(os.environ.get("AIOSSELL_PASSWORD") or "")
    partner = str(os.environ.get("AIOSSELL_PARTNER_ID") or "").strip()
    missing = [name for name, value in (("AIOSSELL_USERNAME", username), ("AIOSSELL_PASSWORD", password), ("AIOSSELL_PARTNER_ID", partner)) if not value]
    if missing:
        return None, {"error": ", ".join(missing) + " not configured"}
    return (username, password, partner), None


def _post_aiosell(url: str, body: dict) -> dict:
    creds, error = _credentials()
    if error:
        return error
    username, password, _partner = creds
    try:
        response = requests.post(
            url,
            json=body,
            auth=HTTPBasicAuth(username, password),
            headers={"Content-Type": "application/json"},
            timeout=(5, 30),
        )
    except requests.RequestException as exc:
        return {"error": str(exc)}
    try:
        payload = response.json()
    except ValueError:
        payload = {"message": response.text[:300]}
    if response.status_code != 200:
        message = payload.get("message") if isinstance(payload, dict) else response.text[:300]
        return {"error": "Aiosell responded " + str(response.status_code) + ": " + str(message)}
    if isinstance(payload, dict) and payload.get("success") is False:
        return {"error": str(payload.get("message") or "Aiosell rejected the update")}
    return {"result": payload}


def push_inventory_to_aiosell(hotel_code: str, updates: list) -> dict:
    """POST availability. Each update is startDate, endDate, and rooms of roomCode plus available."""
    _creds, error = _credentials()
    if error:
        return error
    partner = _creds[2]
    body = {"hotelCode": str(hotel_code or "").strip(), "updates": updates}
    sent = _post_aiosell(INVENTORY_URL.format(partner=quote(partner, safe="")), body)
    sent["payload"] = body
    return sent


def push_rates_to_aiosell(hotel_code: str, updates: list) -> dict:
    """POST rates. Each update is startDate, endDate, and rates of roomCode, rateplanCode, and rate."""
    _creds, error = _credentials()
    if error:
        return error
    partner = _creds[2]
    body = {"hotelCode": str(hotel_code or "").strip(), "updates": updates}
    sent = _post_aiosell(RATE_URL.format(partner=quote(partner, safe="")), body)
    sent["payload"] = body
    return sent

def _parse_day(value):
    text = str(value or "").strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _day_span(start_date, end_date):
    start = _parse_day(start_date)
    end = _parse_day(end_date)
    if start is None or end is None:
        return None, None, "Dates must be YYYY-MM-DD"
    if end < start:
        return None, None, "End date is before the start date"
    if (end - start).days > 30:
        return None, None, "Date range is longer than 31 days"
    return start, end, ""


def hotel_code_for(hotel_name: str) -> str:
    from booking_system.front_desk_manager import intelligent_front_desk
    doc = intelligent_front_desk.get_mongodb_connection().get_collection("ota_link").find_one(
        {"hotel_name": str(hotel_name or "").strip().upper()},
        {"_id": 0, "hotel_code": 1},
    )
    return str((doc or {}).get("hotel_code") or "").strip()


def read_room_maps(hotel_name: str, hotel_code: str) -> list:
    hotel_name = str(hotel_name or "").strip().upper()
    from booking_system.aiosell_reservation import _ensure_mapping_table
    from booking_system.booking_manager import booking_manager
    with booking_manager.postgres_lock:
        booking_manager._clear_transaction()
        cur = booking_manager.postgres_conn.cursor()
        try:
            _ensure_mapping_table(cur)
            booking_manager.postgres_conn.commit()
        finally:
            cur.close()
        cur = booking_manager.postgres_conn.cursor()
        try:
            cur.execute(
                """
                SELECT room_code, room_type FROM hotel_room_code_mapping
                WHERE hotel_code = %s AND hotel_name = %s
                ORDER BY room_code
                """,
                (hotel_code, hotel_name),
            )
            rows = [{"room_code": row[0], "room_type": row[1]} for row in cur.fetchall()]
        finally:
            cur.close()
            booking_manager._safe_rollback()
        return rows


def _property_rooms(property_body: dict) -> dict:
    found = {}
    for room in (property_body or {}).get("rooms") or []:
        code = str((room or {}).get("room_id") or "").strip()
        plans = []
        for plan in (room or {}).get("rateplans") or []:
            plan_code = str((plan or {}).get("rateplan_id") or "").strip()
            if plan_code:
                plans.append(plan_code)
        if code:
            found[code] = plans
    return found


def _available_by_date(hotel_name: str, room_type: str, start, end) -> dict:
    from booking_system.booking_manager import booking_manager
    from booking_system.llm_utils import canonical_key
    with booking_manager.postgres_lock:
        return _available_by_date_locked(booking_manager, canonical_key, hotel_name, room_type, start, end)


def _available_by_date_locked(booking_manager, canonical_key, hotel_name: str, room_type: str, start, end) -> dict:
    booking_manager._clear_transaction()
    cur = booking_manager.postgres_conn.cursor()
    hotel = canonical_key(hotel_name)
    room = canonical_key(room_type)
    try:
        cur.execute(
            """
            SELECT stay_date, blocked_quantity
            FROM room_inventory_nightly
            WHERE hotel_name = %s AND room_type = %s AND stay_date BETWEEN %s AND %s
            """,
            (hotel, room, start, end),
        )
        blocked = {row[0]: int(row[1] or 0) for row in cur.fetchall()}
        cur.execute(
            """
            SELECT stay_date, COUNT(DISTINCT room_number)
            FROM room_number_assignments
            WHERE hotel_name = %s AND room_type = %s AND stay_date BETWEEN %s AND %s
            GROUP BY stay_date
            """,
            (hotel, room, start, end),
        )
        physical = {row[0]: int(row[1] or 0) for row in cur.fetchall()}
        cur.execute(
            """
            SELECT stay_date, COALESCE(SUM(rooms_booked), 0)
            FROM booking_stays
            WHERE hotel_name = %s AND room_type = %s AND stay_date BETWEEN %s AND %s
              AND LOWER(COALESCE(booking_status, '')) <> 'cancelled'
            GROUP BY stay_date
            """,
            (hotel, room, start, end),
        )
        booked = {row[0]: int(row[1] or 0) for row in cur.fetchall()}
    finally:
        cur.close()
        booking_manager._safe_rollback()
    available = {}
    day = start
    while day <= end:
        if day not in blocked:
            available[day] = None
        else:
            rooms = physical.get(day, 0)
            held = min(blocked.get(day, 0), rooms)
            open_rooms = rooms - held - booked.get(day, 0)
            available[day] = open_rooms if open_rooms > 0 else 0
        day += timedelta(days=1)
    return available


def _mapped_codes(hotel_name: str, hotel_code: str, property_body: dict):
    maps = read_room_maps(hotel_name, hotel_code)
    if not maps:
        return None, "No room codes are mapped for this hotel"
    rooms = _property_rooms(property_body)
    paired = []
    missing = []
    for item in maps:
        code = str(item.get("room_code") or "").strip()
        room_type = str(item.get("room_type") or "").strip()
        if code not in rooms:
            missing.append(code)
            continue
        paired.append({"room_code": code, "room_type": room_type, "rateplans": rooms[code]})
    if missing and not paired:
        return None, "Mapped room codes are not on this property: " + ", ".join(missing)
    return {"paired": paired, "missing": missing}, ""


def push_inventory_range(hotel_name: str, start_date: str, end_date: str) -> dict:
    """Push mapped room availability to Aiosell for each date in the range."""
    hotel_name = str(hotel_name or "").strip().upper()
    start, end, span_error = _day_span(start_date, end_date)
    if span_error:
        return {"error": span_error}
    hotel_code = hotel_code_for(hotel_name)
    if not hotel_code:
        return {"error": "Hotel code is not linked"}
    details = read_property_details(hotel_code)
    if details.get("error"):
        return {"error": details["error"]}
    mapped, map_error = _mapped_codes(hotel_name, hotel_code, details.get("property"))
    if map_error:
        return {"error": map_error}
    by_type = {}
    updates = []
    gaps = []
    day = start
    while day <= end:
        rooms = []
        for item in mapped["paired"]:
            room_type = item["room_type"]
            if room_type not in by_type:
                by_type[room_type] = _available_by_date(hotel_name, room_type, start, end)
            count = by_type[room_type].get(day)
            if count is None:
                gaps.append(day.isoformat() + " " + room_type)
                continue
            rooms.append({"roomCode": item["room_code"], "available": count})
        if rooms:
            text = day.isoformat()
            updates.append({"startDate": text, "endDate": text, "rooms": rooms})
        day += timedelta(days=1)
    if not updates:
        return {"error": "No nightly inventory for the mapped room types in this range"}
    sent = push_inventory_to_aiosell(hotel_code, updates)
    sent["missing_inventory"] = gaps
    sent["unmatched_codes"] = mapped["missing"]
    return sent


def push_rate_range(hotel_name: str, start_date: str, end_date: str) -> dict:
    """Push the mapped room type price onto each rate plan for each date."""
    from booking_system.kite_pricing import kite_pricing
    hotel_name = str(hotel_name or "").strip().upper()
    start, end, span_error = _day_span(start_date, end_date)
    if span_error:
        return {"error": span_error}
    hotel_code = hotel_code_for(hotel_name)
    if not hotel_code:
        return {"error": "Hotel code is not linked"}
    details = read_property_details(hotel_code)
    if details.get("error"):
        return {"error": details["error"]}
    mapped, map_error = _mapped_codes(hotel_name, hotel_code, details.get("property"))
    if map_error:
        return {"error": map_error}
    quotes = {}
    for item in mapped["paired"]:
        room_type = item["room_type"]
        if room_type in quotes:
            continue
        quotes[room_type] = {}
        for quote in kite_pricing.quotes_for_range(hotel_name, room_type, start.isoformat(), end.isoformat()):
            quotes[room_type][quote["stay_date"]] = quote.get("push_price")
    updates = []
    gaps = []
    day = start
    while day <= end:
        rates = []
        text = day.isoformat()
        for item in mapped["paired"]:
            price = quotes.get(item["room_type"], {}).get(text)
            if price is None:
                gaps.append(text + " " + item["room_type"])
                continue
            for plan_code in item["rateplans"]:
                rates.append({
                    "roomCode": item["room_code"],
                    "rateplanCode": plan_code,
                    "rate": price,
                })
        if rates:
            updates.append({"startDate": text, "endDate": text, "rates": rates})
        day += timedelta(days=1)
    if not updates:
        return {"error": "No prices for the mapped room types in this range"}
    sent = push_rates_to_aiosell(hotel_code, updates)
    sent["missing_prices"] = gaps
    sent["unmatched_codes"] = mapped["missing"]
    return sent


def push_dates(hotel_name: str, stay_dates) -> dict:
    """Push inventory and rates for the stay dates that changed."""
    days = []
    seen = set()
    for stay_date in stay_dates or []:
        parsed = _parse_day(stay_date)
        if parsed is None or parsed in seen:
            continue
        seen.add(parsed)
        days.append(parsed)
    if not days:
        return {"skipped": "no dates"}
    days.sort()
    chunks = []
    start = days[0]
    end = days[0]
    for day in days[1:]:
        if (day - start).days > 30:
            chunks.append((start, end))
            start = day
        end = day
    chunks.append((start, end))
    inventory = []
    rates = []
    for chunk_start, chunk_end in chunks:
        inventory.append(push_inventory_range(hotel_name, chunk_start.isoformat(), chunk_end.isoformat()))
        rates.append(push_rate_range(hotel_name, chunk_start.isoformat(), chunk_end.isoformat()))
    return {"inventory": inventory, "rates": rates}


def push_rates_for_date(hotel_name, stay_date, to_channels=None):
    """Send the selected price for every mapped room on one stay date."""
    text = str(stay_date)[:10]
    return push_rate_range(hotel_name, text, text)


def push_rates_for_dates(hotel_name, stay_dates, to_channels=None):
    """Send the selected price for every mapped room on each of the given stay dates."""
    days = []
    for stay_date in stay_dates or []:
        parsed = _parse_day(stay_date)
        if parsed is not None:
            days.append(parsed)
    if not days:
        return {"error": "Dates must be YYYY-MM-DD"}
    return push_rate_range(hotel_name, min(days).isoformat(), max(days).isoformat())