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
    assert _choices("hotel_name") == [] and _choices("manager_email") == []


def test_input_kinds():
    assert [_input_kind(n) for n in ("guest_phone", "manager_email", "otp_email", "room_numbers", "nightly_price", "hotel_name", "manager_phone")] == \
        ["phone", "email", "code", "room_numbers", "money", "text", "text"]


def test_account_starts_with_email():
    from booking_system.onboard_turn import _next_name, _question
    assert _next_name({}, []) == "manager_email"
    assert _question("manager_email") == "What's your email?"
    assert _question("otp_email").startswith("An OTP has been sent to your email")
    assert _question("manager_phone") == ""
    assert _question("otp_phone") == ""
    assert "whatsapp" not in _question("guest_phone").lower()
