"""Named dynamic pricing setups for a hotel.

Each setup is content: a name plus the model that fills its parameters.
The natural key is hotel_name + pricing_name.
"""
import os

from pymongo import MongoClient

from booking_system.llm_utils import canonical_key

INAP_MODEL = "INAP dynamic pricing"


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
        "hotel_name": doc.get("hotel_name"),
        "pricing_name": doc.get("pricing_name"),
        "pricing_label": doc.get("pricing_label") or doc.get("pricing_name"),
        "model": doc.get("model"),
        "enabled": bool(doc.get("enabled")),
        "sensitivity": doc.get("sensitivity"),
        "lower_limit": doc.get("lower_limit"),
        "upper_limit": doc.get("upper_limit"),
    }


class DynamicPricing:
    def __init__(self):
        self.client = MongoClient(os.getenv("MONGO_URL"))
        self.collection = self.client["leon_hotel_memory"]["dynamic_pricing"]
        try:
            self.collection.create_index(
                [("hotel_name", 1), ("pricing_name", 1)],
                unique=True,
                name="hotel_pricing_name",
            )
        except Exception as exc:
            print(f"⚠️ Dynamic pricing index warning: {exc}")

    def list_for_hotel(self, hotel_name):
        hotel = canonical_key(hotel_name)
        rows = []
        for doc in self.collection.find({"hotel_name": hotel}).sort("pricing_name", 1):
            rows.append(_public(doc))
        return rows

    def read_one(self, hotel_name, pricing_name):
        hotel = canonical_key(hotel_name)
        key = canonical_key(str(pricing_name or "").strip())
        if not key:
            return {"missing": True}
        doc = self.collection.find_one({"hotel_name": hotel, "pricing_name": key})
        if not doc:
            return {"missing": True}
        return _public(doc)

    def save(self, hotel_name, pricing_label, model, enabled, sensitivity, lower_limit, upper_limit):
        hotel = canonical_key(hotel_name)
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
        existing = list(self.collection.find({"hotel_name": hotel}))
        exact = None
        similar = []
        for doc in existing:
            other = doc.get("pricing_name") or ""
            if other == key:
                exact = doc
                continue
            if len(key) >= 4 and len(other) >= 4 and (other.startswith(key) or key.startswith(other)):
                similar.append(doc)
        fields = {
            "hotel_name": hotel,
            "pricing_name": key,
            "pricing_label": label,
            "model": model,
            "enabled": bool(enabled),
            "sensitivity": sensitivity_n,
            "lower_limit": lower_n,
            "upper_limit": upper_n,
        }
        if exact:
            self.collection.update_one(
                {"hotel_name": hotel, "pricing_name": key},
                {"$set": fields},
            )
            return self.read_one(hotel, key)
        if similar:
            return {"clarify": True, "matches": [_public(doc) for doc in similar]}
        self.collection.insert_one(fields)
        return self.read_one(hotel, key)

    def delete(self, hotel_name, pricing_name):
        hotel = canonical_key(hotel_name)
        key = canonical_key(str(pricing_name or "").strip())
        if not key:
            return {"error": "Pricing name is required"}
        doc = self.collection.find_one({"hotel_name": hotel, "pricing_name": key})
        if not doc:
            return {"error": "No dynamic pricing with that name"}
        self.collection.delete_one({"hotel_name": hotel, "pricing_name": key})
        return {"deleted": key, "pricing_label": doc.get("pricing_label") or key}


dynamic_pricing = DynamicPricing()
