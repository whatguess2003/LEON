"""Hotel currency. The hotel row (or the session's hotel_currency) is the only source."""

from booking_system import db


def hotel_currency(hotel_id):
    found = db.row("SELECT currency FROM hotel WHERE hotel_id = %s", (hotel_id,))
    return found["currency"].strip() if found else None


def format_amount(amount, currency):
    return (currency + " " if currency else "") + f"{float(amount):,.2f}"
