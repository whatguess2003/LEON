"""Concierge content: places and tips the desk recommends, kept as documents in the `concierge` collection keyed by
(hotel_id, kind, name). Kinds are fixed; the text is free. Each write reads first."""

import sys
from datetime import datetime, timezone

from booking_system.content_store import client

KINDS = ("attraction", "restaurant", "shopping", "event", "transport", "guide")
FIELDS = ("description", "address", "area", "opening_hours", "price_range", "contact", "website", "notes")
items = client["leon_hotel_memory"]["concierge"]


def _key(hotel_id, kind, name):
    return {"hotel_id": hotel_id, "kind": str(kind or "").strip().lower(), "name": str(name or "").strip().upper()}


def save_item(hotel_id, user_id, kind, name, fields):
    """READ the item; update its text fields or create it. fields: any of FIELDS."""
    key = _key(hotel_id, kind, name)
    if key["kind"] not in KINDS:
        return {"error": "kind must be one of " + ", ".join(KINDS)}
    if not key["name"]:
        return {"error": "name is required"}
    unknown = sorted(set(fields or {}) - set(FIELDS))
    if unknown:
        return {"error": "Unknown concierge fields: " + ", ".join(unknown)}
    body = {name_: str(value or "").strip() for name_, value in (fields or {}).items()}
    body.update(updated_at=datetime.now(timezone.utc).isoformat(), updated_by_user_id=user_id)
    if items.find_one(key):
        items.update_one(key, {"$set": body})
        return dict(key, result="updated")
    items.insert_one(dict(key, _id="|".join((key["hotel_id"], key["kind"], key["name"])), **body))
    return dict(key, result="created")


def list_items(hotel_id, kind=""):
    """The hotel's items, of one kind when given, by kind and name."""
    wanted = {"hotel_id": hotel_id}
    if kind:
        if str(kind).strip().lower() not in KINDS:
            return {"error": "kind must be one of " + ", ".join(KINDS)}
        wanted["kind"] = str(kind).strip().lower()
    rows = list(items.find(wanted, {"_id": 0}))
    return {"items": sorted(rows, key=lambda row: (row.get("kind") or "", row.get("name") or ""))}


def delete_item(hotel_id, kind, name):
    key = _key(hotel_id, kind, name)
    if not items.find_one(key):
        return {"error": "Item not found"}
    items.delete_one(key)
    return dict(key, result="deleted")


concierge_manager = sys.modules[__name__]
