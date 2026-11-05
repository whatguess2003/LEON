"""Quick answers and input hints the sign-up chat returns for the field being asked (no database for fixed fields)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("JWT_SECRET", "unit-test-secret-only")

from booking_system.onboard_turn import _choices, _input_kind  # noqa: E402


def test_offer_first_then_alternatives_without_duplicates():
    labels = [c["label"] for c in _choices("hotel_currency")]
    assert labels[0] == "ringgit" and len(labels) == len(set(labels)) and len(labels) <= 6
    assert [c["label"] for c in _choices("payment_percent")][0] == "30 percent"


def test_fields_without_choices():
    assert _choices("hotel_name") == [] and _choices("manager_phone") == []


def test_input_kinds():
    assert [_input_kind(n) for n in ("manager_phone", "guest_phone", "manager_email", "otp_phone", "otp_email", "room_numbers", "nightly_price", "hotel_name")] == \
        ["phone", "phone", "email", "code", "code", "room_numbers", "money", "text"]
