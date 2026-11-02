"""Named dynamic pricing setups for a hotel.

Each setup is a document: a name plus the model that fills its parameters. It is found by hotel_id + pricing_name.
"""
import sys

from booking_system import db
from booking_system.content_store import client
from booking_system.llm_utils import canonical_key

INAP_MODEL = "INAP dynamic pricing"
collection = client["leon_hotel_memory"]["dynamic_pricing"]


def _number(value):
    if value is None or value == "":
        return None
    return float(value)


def _limits_error(enabled, sensitivity, lower_limit, upper_limit):
    if not enabled:
        return None
    if sensitivity is None or lower_limit is None or upper_limit is None:
        return "Sensitivity, lower limit, and upper limit are required"
    if sensitivity < 0:
        return "Sensitivity must be zero or greater"
    if lower_limit < 0 or upper_limit < 0:
        return "Limits must be zero or greater"
    if lower_limit > upper_limit:
        return "Lower limit must be at or below the upper limit"
    return None


def _public(doc):
    return {
        "pricing_name": doc.get("pricing_name"),
        "pricing_label": doc.get("pricing_label") or doc.get("pricing_name"),
        "model": doc.get("model"),
        "enabled": bool(doc.get("enabled")),
        "sensitivity": doc.get("sensitivity"),
        "lower_limit": doc.get("lower_limit"),
        "upper_limit": doc.get("upper_limit"),
    }


def _key(hotel_id, pricing_name):
    return "hotel_id=" + hotel_id + "|pricing_name=" + pricing_name


def list_for_hotel(hotel_id):
    return [_public(doc) for doc in collection.find({"hotel_id": hotel_id}).sort("pricing_name", 1)]


def read_one(hotel_id, pricing_name):
    key = canonical_key(str(pricing_name or "").strip())
    doc = collection.find_one({"hotel_id": hotel_id, "pricing_name": key}) if key else None
    return _public(doc) if doc else {"missing": True}


def save(hotel_id, user_id, pricing_label, model, enabled, sensitivity, lower_limit, upper_limit):
    """CREATE or UPDATE by name. A new name close to an existing one asks which was meant."""
    label = str(pricing_label or "").strip()
    key = canonical_key(label)
    if not key:
        return {"error": "Pricing name is required"}
    if model != INAP_MODEL:
        return {"error": "That model is not available yet"}
    try:
        sensitivity_n = _number(sensitivity)
        lower_n = _number(lower_limit)
        upper_n = _number(upper_limit)
    except (TypeError, ValueError):
        return {"error": "Sensitivity, lower limit, and upper limit must be numbers"}
    limits_error = _limits_error(bool(enabled), sensitivity_n, lower_n, upper_n)
    if limits_error:
        return {"error": limits_error}
    existing = list(collection.find({"hotel_id": hotel_id}))
    exact = [doc for doc in existing if doc.get("pricing_name") == key]
    similar = [doc for doc in existing if doc.get("pricing_name") != key and len(key) >= 4 and len(doc.get("pricing_name") or "") >= 4
               and (doc["pricing_name"].startswith(key) or key.startswith(doc["pricing_name"]))]
    if similar and not exact:
        return {"clarify": True, "matches": [_public(doc) for doc in similar]}
    body = {
        "hotel_id": hotel_id, "pricing_name": key, "pricing_label": label, "model": model, "enabled": bool(enabled),
        "sensitivity": sensitivity_n, "lower_limit": lower_n, "upper_limit": upper_n, "updated_by_user_id": user_id,
    }
    db.transaction(lambda cur: collection.write_in(cur, _key(hotel_id, key), body))
    return _public(body)


def delete(hotel_id, pricing_name):
    key = canonical_key(str(pricing_name or "").strip())
    if not key:
        return {"error": "Pricing name is required"}
    doc = collection.find_one({"hotel_id": hotel_id, "pricing_name": key})
    if not doc:
        return {"error": "No dynamic pricing with that name"}
    collection.delete_one({"hotel_id": hotel_id, "pricing_name": key})
    return {"deleted": key, "pricing_label": doc.get("pricing_label") or key}


# `from booking_system.dynamic_pricing import dynamic_pricing` keeps working for callers of the old instance.
dynamic_pricing = sys.modules[__name__]
