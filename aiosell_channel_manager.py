import requests
from datetime import datetime, timedelta
from typing import List, Dict, Any

# These URLs will be provided by Aiosell. Use as global endpoints for all hotels.
INVENTORY_IN_URL = "https://aiosell.com/api/inventory-in"  # TODO: Replace with actual URL from Aiosell
RATE_IN_URL = "https://aiosell.com/api/rate-in"            # TODO: Replace with actual URL from Aiosell

def push_rates_to_aiosell(payload: dict) -> dict:
    """
    Push rates and restrictions to Aiosell Channel Manager (global endpoint).
    :param payload: Dict with the required fields as per Aiosell API.
    :return: Dict with API response.
    """
    headers = {
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(RATE_IN_URL, json=payload, headers=headers, timeout=10)
        return {
            "status_code": response.status_code,
            "response": response.json() if response.headers.get('Content-Type', '').startswith('application/json') else response.text
        }
    except requests.RequestException as e:
        return {
            "error": str(e)
        }

def push_inventory_to_aiosell(payload: dict) -> dict:
    """
    Push inventory (room availability) to Aiosell Channel Manager (global endpoint).
    :param payload: Dict as per Aiosell API documentation.
    :return: Dict with API response.
    """
    headers = {
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(INVENTORY_IN_URL, json=payload, headers=headers, timeout=10)
        return {
            "status_code": response.status_code,
            "response": response.json() if response.headers.get('Content-Type', '').startswith('application/json') else response.text
        }
    except requests.RequestException as e:
        return {
            "error": str(e)
        }

def build_inventory_payload(
    hotel_name: str,
    room_type: str,
    start_date: str,
    end_date: str,
    inventory: int,
    to_channels: List[str]
) -> Dict[str, Any]:
    """
    Build a payload for inventory push to Aiosell.
    :param hotel_name: Hotel name as registered in PMS
    :param room_type: Room code as per PMS
    :param start_date: Start date (YYYY-MM-DD)
    :param end_date: End date (YYYY-MM-DD)
    :param inventory: Number of rooms available
    :param to_channels: List of channels to update
    :return: Dict payload ready for push_inventory_to_aiosell
    """
    return {
        "hotelName": hotel_name,
        "toChannels": to_channels,
        "updates": [
            {
                "startDate": start_date,
                "endDate": end_date,
                "inventories": [
                    {
                        "roomType": room_type,
                        "inventory": inventory
                    }
                ]
            }
        ]
    }

if __name__ == "__main__":
    # Example: Build and push inventory for EXECUTIVE room, 2024-07-06 to 2024-07-08
    payload = build_inventory_payload(
        hotel_name="HARBOR VIEW HOTEL",
        room_type="EXECUTIVE",
        start_date=datetime.now().strftime("%Y-%m-%d"),
        end_date=(datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d"),
        inventory=5,
        to_channels=["agoda", "booking.com"]
    )
    print("Inventory payload:", payload)
    print("Inventory endpoint:", INVENTORY_IN_URL)
    result = push_inventory_to_aiosell(payload)
    print("Aiosell Inventory API Response:", result) 