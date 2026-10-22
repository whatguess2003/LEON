import requests
import json

URL = "http://localhost:8005/aiosell/reservation"

sample_payload = {
    "action": "book",
    "hotelCode": "SANDBOX-PMS",
    "channel": "Goingo",
    "bookingId": "111222333",
    "cmBookingId": "AAABBBCCC",
    "bookedOn": "2022-12-08 15:25:35",
    "checkin": "2022-12-10",
    "checkout": "2022-12-12",
    "segment": "OTA",
    "specialRequests": "Airport Taxi Required",
    "pah": False,
    "amount": {
        "amountAfterTax": 1204.0,
        "amountBeforeTax": 1075.0,
        "tax": 129.0,
        "currency": "INR"
    },
    "guest": {
        "firstName": "Akshay",
        "lastName": "Kumar",
        "email": "akshaykumar@gmail.com",
        "phone": "9988776655",
        "address": {
            "line1": "51",
            "city": "Bangalore",
            "state": "Karnataka",
            "country": "India",
            "zipCode": "560035"
        }
    },
    "rooms": [
        {
            "roomCode": "SUITE",
            "rateplanCode": "SUITE-S-101",
            "guestName": "Akshay Kumar",
            "occupancy": {
                "adults": 1,
                "children": 0
            },
            "prices": [
                {"date": "2022-12-10", "sellRate": 537.5},
                {"date": "2022-12-11", "sellRate": 537.5}
            ]
        }
    ]
}

if __name__ == "__main__":
    headers = {"Content-Type": "application/json"}
    response = requests.post(URL, data=json.dumps(sample_payload), headers=headers)
    print(f"Status Code: {response.status_code}")
    print("Response:", response.json()) 