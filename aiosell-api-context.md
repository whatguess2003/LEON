This is an inbound webhook FROM Aiosell TO your PMS - you implement the endpoint, Aiosell calls it.
The same endpoint receives `book`, `modify`, and `cancel` events differentiated by the `action` field.
Guest details depend on the OTA. Each channel decides how much it shares, and many mask or withhold the email, phone, and address. Treat every `guest.*` field as optional: it may arrive empty, `null`, or missing. Do not require them, and do not reject a booking because they are absent.
Card details: `creditCard` is sent only when the OTA shares the guest card. Every value in it is encrypted with AES-128-ECB, using a plain-text 128-bit (16-character) key, and hex encoded. Decrypt each field separately.
The card encryption key is set by the Aiosell team after verification - you must be onboarded as an Aiosell partner to get one. Never log decrypted card data, and handle it per PCI-DSS.
`specialRequests` is free text and its contents depend on the OTA. It usually carries guest requests, but some channels use it for other booking information. Store it as plain text and do not parse it into a fixed structure.

post: https://sample-pms.com/update_reservation
{
  "action": "book",
  "hotelCode": "sandbox-pms",
  "channel": "booking.com",
  "bookingId": "BDC5238846",
  "cmBookingId": "CM-BDC5238846",
  "bookedOn": "2026-09-11 11:01:50",
  "checkin": "2026-09-18",
  "checkout": "2026-09-20",
  "segment": "OTA",
  "specialRequests": "",
  "pah": false,
  "amount": {
    "amountAfterTax": 13922.28,
    "amountBeforeTax": 13922.28,
    "tax": 0,
    "currency": "INR",
    "commission": null,
    "tcs": null,
    "tds": null
  },
  "guest": {
    "firstName": "Test",
    "lastName": "Guest",
    "email": "testguest@example.com",
    "phone": "9876543210",
    "address": {
      "line1": "1 Test Street",
      "city": "Bangalore",
      "state": "Karnataka",
      "country": "India",
      "zipCode": "560001"
    }
  },
  "rooms": [
    {
      "roomCode": "suite",
      "rateplanCode": "suite-d-cp",
      "guestName": "Test Guest",
      "occupancy": {
        "adults": 2,
        "children": 2
      },
      "prices": [
        {
          "date": "2026-09-18",
          "sellRate": 6961.14
        },
        {
          "date": "2026-09-19",
          "sellRate": 6961.14
        }
      ]
    }
  ],
  "creditCard": {
    "cardNumber": "bc38e0895300c8a7300a81b2583bf67e332e61d7e44500dfe44dacb610486d5e",
    "cvv": "eb263b0a60d027db97f3e3ab1a0dce16",
    "expiry": "483d9533b52bdbf246a3c493d64be327",
    "cardHolderName": "e042c93cb463b92661eadae1daa4af06",
    "cardType": "31f15747aab9fd88ef9250288620d300",
    "activationDate": "e598eaa5e9d41395e18bcf4491ea2269"
  }
}
#expected response#
{
    "success": true,
    "message": "Reservation Updated Successfully"
  }

  The payload represents the *new* booking state - overwrite, not delta.

  post: https://sample-pms.com/update_reservation

  {
    "action": "modify",
    "hotelCode": "sandbox-pms",
    "channel": "Goibibo",
    "bookingId": "111222333",
    "cmBookingId": "AAABBBCCC",
    "bookedOn": "2022-12-08 15:25:35",
    "checkin": "2022-12-10",
    "checkout": "2022-12-12",
    "segment": "OTA",
    "pah": false,
    "amount": {
      "amountAfterTax": 1204,
      "amountBeforeTax": 1075,
      "tax": 129,
      "currency": "INR",
      "commission": 215,
      "tcs": 5.38,
      "tds": 1.08
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
        "roomCode": "executive",
        "rateplanCode": "executive-s-ep",
        "guestName": "Akshay Kumar",
        "occupancy": {
          "adults": 1,
          "children": 0
        },
        "prices": [
          {
            "date": "2022-12-10",
            "sellRate": 537.5
          },
          {
            "date": "2022-12-11",
            "sellRate": 537.5
          }
        ]
      }
    ]
  }

  #expected response#
  {
    "success": true,
    "message": "Reservation Modified Successfully"
  }

  post: https://sample-pms.com/update_reservation

  {
    "action": "cancel",
    "hotelCode": "sandbox-pms",
    "channel": "Goibibo",
    "bookingId": "111222333"
  }

  #expected response#
  {
    "success": true,
    "message": "Reservation Cancelled Successfully"
  }