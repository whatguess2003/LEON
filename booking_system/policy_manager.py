"""Booking, payment and tax policies: one document per (hotel_id, policy_type) in the `booking_policies` collection. The details
follow TEMPLATES; a type with no document is shown with its template. Each write reads first; a refusal returns {'error': reason}."""

import sys
from datetime import datetime, timezone

from booking_system.content_store import client
from booking_system.hotel_registry import read_hotel_by_id

TEMPLATES = {
    "payment": {
        "payment_threshold_percent": 30,
        "payment_window_days": 7,
        "methods_allowed": ["CASH", "CARD", "BANK TRANSFER", "OTA"],
        "deadline_policy": "before_check_in",
        "notes": "",
    },
    "booking": {
        "max_stay_nights": 14,
        "cancellation_policy": {"window_hours": 48, "penalty_nights": 1, "refund_percent": 50},
        "no_show_policy": {"penalty_nights": 1},
        "overbooking_policy": "do_not_overbook",
        "notes": "",
    },
    "tax": {
        "vat_percent": 6.0,
        "service_charge_percent": 10.0,
        "tourism_tax_per_night": 10.0,
        "notes": "",
    },
}
policies = client["leon_hotel_memory"]["booking_policies"]


def _type(policy_type):
    return str(policy_type or "").strip().lower()


def _refused(hotel_id, policy_type):
    if _type(policy_type) not in TEMPLATES:
        return "policy_type must be one of " + ", ".join(TEMPLATES)
    if not read_hotel_by_id(hotel_id):
        return "Hotel " + str(hotel_id or "") + " is not registered"
    return ""


def _outside(policy_type, details):
    """'' when every field is the template's, else the refusal naming the others (operating choices are hotel settings, not policy fields)."""
    extra = sorted(set(details) - set(TEMPLATES[_type(policy_type)]))
    return "not " + _type(policy_type) + " policy fields: " + ", ".join(extra) if extra else ""


def _key(hotel_id, policy_type):
    return {"hotel_id": hotel_id, "policy_type": _type(policy_type)}


def read_policy(hotel_id, policy_type):
    """The stored policy document (no _id), or None."""
    return policies.find_one(dict(_key(hotel_id, policy_type), status="active"), {"_id": 0})


def read_policies(hotel_id, policy_type=None):
    """{'items': [{policy_type, details, exists, updated_at}]} for one type or all; a missing type shows its template."""
    if policy_type and _type(policy_type) not in TEMPLATES:
        return {"error": "policy_type must be one of " + ", ".join(TEMPLATES)}
    items = []
    for kind in [_type(policy_type)] if policy_type else list(TEMPLATES):
        found = read_policy(hotel_id, kind)
        items.append({"policy_type": kind, "details": (found or {}).get("details") or TEMPLATES[kind], "exists": found is not None,
                      "updated_at": (found or {}).get("updated_at") or (found or {}).get("created_at")})
    return {"items": items}


def create_policy(hotel_id, user_id, policy_type, details, description=""):
    """READ first: a hotel holds one policy per type."""
    refused = _refused(hotel_id, policy_type)
    if refused:
        return {"error": refused}
    if not isinstance(details, dict):
        return {"error": "policy details must be an object"}
    if _outside(policy_type, details):
        return {"error": _outside(policy_type, details)}
    if read_policy(hotel_id, policy_type):
        return {"error": "A " + _type(policy_type) + " policy is already on file; update it instead"}
    now = datetime.now(timezone.utc).isoformat()
    key = _key(hotel_id, policy_type)
    policies.insert_one(dict(key, _id=hotel_id + "|" + key["policy_type"], description=str(description or key["policy_type"] + " policy"),
                             details=details, status="active", created_at=now, created_by_user_id=user_id))
    return dict(key, details=details, result="created")


def update_policy(hotel_id, user_id, policy_type, updates):
    """READ the policy, then merge the given template fields into its details."""
    refused = _refused(hotel_id, policy_type)
    if refused:
        return {"error": refused}
    if not isinstance(updates, dict) or not updates:
        return {"error": "updates must be a non-empty object"}
    if _outside(policy_type, updates):
        return {"error": _outside(policy_type, updates)}
    found = read_policy(hotel_id, policy_type)
    if not found:
        return {"error": "No " + _type(policy_type) + " policy on file"}
    details = dict(found.get("details") or {}, **updates)
    policies.update_one(_key(hotel_id, policy_type), {"$set": {"details": details, "updated_at": datetime.now(timezone.utc).isoformat(),
                                                               "updated_by_user_id": user_id}})
    return dict(_key(hotel_id, policy_type), details=details, result="updated")


def delete_policy(hotel_id, policy_type):
    refused = _refused(hotel_id, policy_type)
    if refused:
        return {"error": refused}
    if not read_policy(hotel_id, policy_type):
        return {"error": "No " + _type(policy_type) + " policy on file"}
    policies.delete_one(_key(hotel_id, policy_type))
    return dict(_key(hotel_id, policy_type), result="deleted")


registration_cards = client["leon_hotel_memory"]["registration_card"]


def read_registration_card(hotel_id):
    """House rules and terms printed on the guest registration card; empty text when none is saved."""
    found = registration_cards.find_one({"hotel_id": hotel_id}, {"_id": 0}) or {}
    return {"hotel_id": hotel_id, "house_rules": str(found.get("house_rules") or ""), "terms": str(found.get("terms") or "")}


def save_registration_card(hotel_id, user_id, house_rules, terms):
    """READ the card, then update it or create it."""
    if not read_hotel_by_id(hotel_id):
        return {"error": "Hotel " + str(hotel_id or "") + " is not registered"}
    body = {"house_rules": str(house_rules or ""), "terms": str(terms or ""), "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by_user_id": user_id}
    if registration_cards.find_one({"hotel_id": hotel_id}):
        registration_cards.update_one({"hotel_id": hotel_id}, {"$set": body})
        return dict(read_registration_card(hotel_id), result="updated")
    registration_cards.insert_one(dict(body, _id=hotel_id, hotel_id=hotel_id))
    return dict(read_registration_card(hotel_id), result="created")


policy_manager = sys.modules[__name__]
