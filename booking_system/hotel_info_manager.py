"""Property details on the hotel_profile document (key hotel_id): contact, address and description text, facility flags and
amenity values. The time zone is the hotel row's and the check-in/check-out times are the hotel's front-desk settings (shown, not
edited here; an unanswered time shows empty)."""

from datetime import datetime, timezone

from booking_system.hotel_registry import profile_col, read_hotel_by_id
from booking_system.settings import service as settings

# property-setup form field -> hotel_profile field
DETAIL_FIELDS = {
    "contact_phone": "contact_phone",
    "contact_email": "contact_email",
    "address": "address",
    "city": "city_name",
    "state_region": "state_region",
    "country": "country_name",
    "country_code": "country_code",
    "description": "description",
}

FACILITIES = (
    "restaurant", "cafe", "bar", "room_service", "spa", "gym", "pool", "kids_pool", "sauna", "steam_room", "parking",
    "valet_parking", "airport_shuttle", "elevator", "wheelchair_access", "smoking_area", "non_smoking_floors",
    "business_center", "meeting_rooms", "banquet_hall", "24h_front_desk", "concierge", "laundry", "dry_cleaning",
    "luggage_storage", "security_24h", "cctv", "keycard_access", "generator_backup",
)

AMENITIES = (
    "internet", "food", "beverage", "business", "parking_type", "kids_programs", "wellness_programs", "sustainability",
    "religious_services", "pet_services", "concierge_services", "accessibility_services", "event_services",
    "laundry_type", "transport_detail",
)


def read_property_templates(hotel_id):
    """READ the hotel's details, facilities and amenities as the property-setup templates."""
    hotel = read_hotel_by_id(hotel_id)
    if not hotel:
        return {"error": "unknown hotel"}
    profile = profile_col.find_one({"hotel_id": hotel_id}) or {}
    times = {row["setting_key"]: row["value"] for row in (settings.read_settings(hotel_id, "front_desk").get("settings") or [])}
    details = {field: str(profile.get(stored) or "") for field, stored in DETAIL_FIELDS.items()}
    details.update(timezone=hotel.get("timezone") or "", checkin_time=str(times.get("front_desk.checkin_time") or ""),
                   checkout_time=str(times.get("front_desk.checkout_time") or ""))
    facilities = profile.get("facilities") or {}
    amenities = profile.get("amenities") or {}
    return {
        "hotel_id": hotel_id,
        "keywords_template": details,
        "facilities_template": {name: bool(facilities.get(name)) for name in FACILITIES},
        "amenities_template": {name: str(amenities.get(name) or "") for name in AMENITIES},
    }


def _unknown(given, known):
    return sorted(set(given or {}) - set(known))


def write_property_templates(hotel_id, user_id, facilities_template=None, amenities_template=None, keywords_template=None):
    """READ the profile, then set the provided sections; keys not provided stay as they are, an empty amenity clears it."""
    current = read_property_templates(hotel_id)
    if current.get("error"):
        return current
    details = dict(keywords_template or {})
    zone = details.pop("timezone", None)
    if zone not in (None, "", current["keywords_template"]["timezone"]):
        return {"error": "timezone is the hotel's (" + current["keywords_template"]["timezone"] + "); it is set at onboarding"}
    details.pop("checkin_time", None)
    details.pop("checkout_time", None)
    unknown = _unknown(details, DETAIL_FIELDS) + _unknown(facilities_template, FACILITIES) + _unknown(amenities_template, AMENITIES)
    if unknown:
        return {"error": "unknown property fields: " + ", ".join(unknown)}
    if not (details or facilities_template or amenities_template):
        return {"error": "no property templates to apply"}
    profile = profile_col.find_one({"hotel_id": hotel_id}) or {}
    facilities = dict(profile.get("facilities") or {}, **{name: bool(on) for name, on in (facilities_template or {}).items()})
    amenities = dict(profile.get("amenities") or {}, **{name: str(value or "").strip().upper() for name, value in (amenities_template or {}).items()})
    changes = {DETAIL_FIELDS[field]: str(value or "").strip() for field, value in details.items()}
    changes.update(hotel_id=hotel_id, facilities={name: on for name, on in facilities.items() if on},
                   amenities={name: value for name, value in amenities.items() if value},
                   updated_at=datetime.now(timezone.utc).isoformat(), updated_by_user_id=user_id)
    profile_col.update_one({"hotel_id": hotel_id}, {"$set": changes}, upsert=True)
    return {"result": "property details saved", **read_property_templates(hotel_id)}
