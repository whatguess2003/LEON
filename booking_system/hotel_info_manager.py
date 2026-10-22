#!/usr/bin/env python3
"""
Hotel Information Manager Tool for LEON Orchestrator
Stores general hotel information in keyword format

ARCHITECTURE:
- Single source of truth: hotel_name from manager_context.json
- Simple keyword storage: hotel_name + keyword + category + description
- No complex validation: Just store what's provided
- MongoDB: hotel_keywords collection for structured data
- LLM parsing: Extract keywords from unstructured text
"""

import os
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from datetime import datetime, timezone
import pymongo
 

# MongoDB imports
try:
    from pymongo import MongoClient
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False

class HotelInfoManager:
    def __init__(self):
        """Initialize Hotel Info Manager"""
        self.mongo_client = None
        self.mongo_db = None
        
        # Initialize LLM for parsing unstructured hotel information
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
        
        self._connect_database()
        self._ensure_collections()

    # Timezone logic centralized in timezone_utils; use hotel_now_local
    
    # Canonical categories (hotel info only) – structured, persistent
    ALLOWED_CATEGORIES = {
        "contact",   # phone, email
        "location",  # address, city, state_region, country_name, country_code
        "language",  # supported languages (hotel-level)
        "awards",    # distinctions/awards
        "general",   # description, timezone, raw notes
    }

    # Canonical subcategory tokens per category (fixed keyword names)
    ALLOWED_SCHEMA = {
        "contact": {"CONTACT_PHONE", "CONTACT_EMAIL"},
        "location": {"ADDRESS", "CITY", "STATE_REGION", "COUNTRY_NAME", "COUNTRY_CODE"},
        "language": {"LANGUAGE"},
        "awards": {"AWARD"},
        "general": {"DESCRIPTION", "TIMEZONE", "RAW_NOTES"},
    }

    # Property-setup form field -> hotel_keywords natural key (hotel_name + keyword)
    KEYWORD_TEMPLATE_FIELDS = (
        ("contact_phone", "CONTACT_PHONE", "contact"),
        ("contact_email", "CONTACT_EMAIL", "contact"),
        ("timezone", "TIMEZONE", "general"),
        ("description", "DESCRIPTION", "general"),
        ("address", "ADDRESS", "location"),
        ("city", "CITY", "location"),
        ("state_region", "STATE_REGION", "location"),
        ("country", "COUNTRY_NAME", "location"),
        ("country_code", "COUNTRY_CODE", "location"),
    )

    # Canonical hotel facilities (booleans) – major components/services
    ALLOWED_FACILITIES = {
        "restaurant",
        "cafe",
        "bar",
        "room_service",
        "spa",
        "gym",
        "pool",
        "kids_pool",
        "sauna",
        "steam_room",
        "parking",
        "valet_parking",
        "airport_shuttle",
        "elevator",
        "wheelchair_access",
        "non_smoking_floors",
        "business_center",
        "meeting_rooms",
        "banquet_hall",
        "24h_front_desk",
        "concierge",
        "laundry",
        "dry_cleaning",
        "luggage_storage",
        "security_24h",
        "cctv",
        "keycard_access",
        "generator_backup",
    }

    # Canonical amenities categories (values are free-form, normalized by caller) – guest-appeal features
    # Overlap with facilities is acceptable: facilities indicate availability (boolean), amenities describe quality/type
    ALLOWED_AMENITIES_CATEGORIES = {
        # Connectivity (hotel-level)
        "internet",           # e.g., 5G_WIFI, WIFI_6E, FIBER, LIMITED
        # Food & beverage (hotel-level styles)
        "food",               # HALAL, VEGETARIAN, KOSHER, etc.
        "beverage",           # BARISTA_COFFEE, TEA_SELECTION
        # Business services (hotel-level)
        "business",           # PRINTING, MEETING_KIT
        # Transport & parking (hotel-level)
        "parking_type",       # COVERED, EV_CHARGER
        # Additional hotel-level amenity categories (values optional/suggestions)
        "kids_programs",      # KIDS_CLUB, BABYSITTING, DAILY_ACTIVITIES
        "wellness_programs",  # YOGA_CLASSES, SOUND_THERAPY
        "sustainability",     # GREEN_CERTIFIED, SOLAR_ENERGY
        "religious_services", # PRAYER_ROOM, FRIDAY_TRANSFER
        "pet_services",       # PET_FRIENDLY, PET_SITTING
        "concierge_services", # TOUR_DESK, ITINERARY_PLANNING
        "accessibility_services", # WHEELCHAIR_RENTAL, ASSISTED_ACCESS
        "event_services",     # WEDDING_PLANNING, AV_SUPPORT
        "laundry_type",       # SELF_SERVICE, FULL_SERVICE
        "transport_detail",   # AIRPORT_ONLY, CITY_LOOP
    }

    def _connect_database(self):
        """Connect to MongoDB only"""
        try:
            # MongoDB connection
            self.mongo_client = pymongo.MongoClient(os.getenv('MONGO_URL'))
            self.mongo_db = self.mongo_client['leon_hotel_memory']
            
            # Database connection established silently
        except Exception as e:
            print(f"❌ MongoDB connection error: {e}")
    
    def _ensure_collections(self):
        """Ensure hotel_keywords collection exists with proper indexes"""
        try:
            if self.mongo_db is None:
                return  # Skip if database is not available
            
            keywords_collection = self.mongo_db['hotel_keywords']
            
            # Create indexes for efficient querying
            keywords_collection.create_index([("hotel_name", 1)])
            keywords_collection.create_index([("keyword", 1)])
            keywords_collection.create_index([("category", 1)])
            keywords_collection.create_index([("timestamp", -1)])
            
            # Compound index for hotel + category queries
            keywords_collection.create_index([("hotel_name", 1), ("category", 1)])

            # Hotel facilities collection (separate from keywords)
            facilities_collection = self.mongo_db['hotel_facilities']
            facilities_collection.create_index([("hotel_name", 1)], unique=True)
            
        except Exception as e:
            print(f"⚠️ Collection setup warning: {e}")
    
   
    # (moved above for clarity)

    def store_hotel_keywords(self, keywords_data: str, hotel_name: str, manager_name: str, structured: dict | None = None) -> str:
        """Store hotel keywords and information using explicit hotel_name and manager_name (no dialog_history).
        Supports optional structured categorized fields from property_setup (contact/amenities/facilities/etc.).
        """
        if self.mongo_db is None:
            return "❌ Database connection not available"
        try:
            keywords_collection = self.mongo_db['hotel_keywords']
            stored_count = 0
            categories = set()

            def _put(keyword: str, category: str, description: str):
                nonlocal stored_count
                kw = (keyword or '').strip().upper()
                cat_raw = (category or '').strip().lower()
                cat = cat_raw if cat_raw in self.ALLOWED_CATEGORIES else 'general'
                # Enforce canonical keyword tokens per category
                allowed_kw = self.ALLOWED_SCHEMA.get(cat, set())
                if allowed_kw and kw not in allowed_kw:
                    return
                desc = (description or '').strip()
                if not kw or not cat:
                    return
                doc = {
                    "hotel_name": hotel_name,
                    "keyword": kw,
                    "category": cat,
                    "description": desc,
                    "timestamp": datetime.now(timezone.utc),
                    "created_by": manager_name,
                    "source": "hotel_info_manager"
                }
                keywords_collection.replace_one(
                    {"hotel_name": hotel_name, "keyword": kw},
                    doc,
                    upsert=True
                )
                stored_count += 1
                categories.add(cat)

            # 1) Structured fields (preferred)
            s = structured or {}
            if isinstance(s, dict) and s:
                if s.get('contact_phone'): _put('CONTACT_PHONE', 'contact', str(s.get('contact_phone')))
                if s.get('contact_email'): _put('CONTACT_EMAIL', 'contact', str(s.get('contact_email')))
                if s.get('timezone'): _put('TIMEZONE', 'general', str(s.get('timezone')))
                if s.get('address'): _put('ADDRESS', 'location', str(s.get('address')))
                if s.get('city'): _put('CITY', 'location', str(s.get('city')))
                if s.get('country'): _put('COUNTRY_NAME', 'location', str(s.get('country')))
                if s.get('country_code'): _put('COUNTRY_CODE', 'location', str(s.get('country_code')))
                if s.get('state_region'): _put('STATE_REGION', 'location', str(s.get('state_region')))
                # Check-in/out times managed by policy_manager; exclude from keywords
                # Facilities/amenities removed from keywords; housed in hotel_facilities collection (separate manager)
                for v in (s.get('awards') or []):
                    _put('AWARD', 'awards', str(v))
                if s.get('description'):
                    _put('DESCRIPTION', 'general', str(s.get('description')))

            # 2) Raw text (optional)
            txt = (keywords_data or '').strip()
            if txt:
                _put('RAW_NOTES', 'general', txt)

            if stored_count == 0:
                return f"❌ No keywords to store for {hotel_name}"

            result = f"✅ Hotel keywords stored successfully for {hotel_name}:\n"
            result += f"  • Keywords stored: {stored_count}\n"
            if categories:
                result += f"  • Categories: {', '.join(sorted(categories))}\n"
            # Read-back verification (show key fields that are now populated)
            try:
                tpl = self.get_keywords_template(hotel_name).get("keywords_template", {})
                verify_keys = [
                    "contact_phone","contact_email","timezone","address","city",
                    "state_region","country","country_code","checkin_time","checkout_time","description"
                ]
                populated = []
                for k in verify_keys:
                    v = tpl.get(k)
                    if isinstance(v, str) and v.strip():
                        populated.append(f"{k}={v}")
                if populated:
                    result += "  • verification: " + ", ".join(populated[:8]) + "\n"
            except Exception:
                pass
            result += f"  • 🎯 Single Source: hotel_name={hotel_name}, manager_name={manager_name}"
            return result
        except Exception as e:
            return f"❌ Error storing hotel keywords: {e}"

    def query_hotel_keywords(self, query: str, hotel_name: str, manager_name: str) -> str:
        """Query hotel keywords and information using explicit hotel_name and manager_name (no dialog_history)"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        try:
            keywords_collection = self.mongo_db['hotel_keywords']
            keywords = list(keywords_collection.find(
                {"hotel_name": hotel_name},
                {"_id": 0}
            ).sort("timestamp", -1))
            if not keywords:
                return f"❌ No keywords found for {hotel_name}"
            query_result = self._process_keyword_query(query, keywords, hotel_name)
            return query_result
        except Exception as e:
            return f"❌ Error querying hotel keywords: {e}"

    def clear_keywords_by_category(self, category: str, hotel_name: str, manager_name: str) -> str:
        """Clear keywords by category (e.g., 'room_types') from keywords system using explicit hotel_name and manager_name"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        try:
            keywords_collection = self.mongo_db['hotel_keywords']
            count_before = keywords_collection.count_documents({
                "hotel_name": hotel_name,
                "category": category
            })
            delete_result = keywords_collection.delete_many({
                "hotel_name": hotel_name,
                "category": category
            })
            return f"✅ Cleared {delete_result.deleted_count} keywords in category '{category}' for {hotel_name}"
        except Exception as e:
            return f"❌ Error clearing keywords: {e}"

    # --- Hotel Facilities (separate collection) ---
    def read_hotel_facilities(self, hotel_name: str) -> dict:
        """READ hotel facilities booleans (MongoDB). Returns {hotel_name, facilities:{...}}"""
        if self.mongo_db is None:
            return {"error": "db unavailable"}
        try:
            doc = self.mongo_db['hotel_facilities'].find_one({"hotel_name": hotel_name}, {"_id": 0}) or {}
            facilities = doc.get('facilities') or {}
            if not isinstance(facilities, dict):
                facilities = {}
            return {"hotel_name": hotel_name, "facilities": facilities}
        except Exception as e:
            return {"hotel_name": hotel_name, "facilities": {}, "error": str(e)}

    def upsert_hotel_facilities(self, hotel_name: str, manager_name: str, facilities_bool: dict) -> str:
        """WRITE hotel facilities booleans (MongoDB). Merges keys; unspecified keys unchanged.
        facilities_bool: { name: bool }
        """
        if self.mongo_db is None:
            return "❌ Database connection not available"
        try:
            coll = self.mongo_db['hotel_facilities']
            # Ensure document exists
            base = coll.find_one({"hotel_name": hotel_name})
            current = (base or {}).get('facilities') or {}
            if not isinstance(current, dict):
                current = {}
            # Merge only provided keys
            for k, v in (facilities_bool or {}).items():
                key = str(k).strip().lower()
                if not key:
                    continue
                if key not in self.ALLOWED_FACILITIES:
                    continue
                current[key] = bool(v)
            coll.update_one(
                {"hotel_name": hotel_name},
                {"$set": {
                    "hotel_name": hotel_name,
                    "facilities": current,
                    "updated_at": datetime.now(timezone.utc),
                    "updated_by": manager_name,
                }},
                upsert=True,
            )
            return f"✅ Facilities updated for {hotel_name}"
        except Exception as e:
            return f"❌ Error updating facilities: {e}"

    def read_hotel_amenities(self, hotel_name: str) -> dict:
        """READ hotel amenities (categorical free-form values) from MongoDB.
        Returns {hotel_name, amenities:{category: value}}.
        """
        if self.mongo_db is None:
            return {"error": "db unavailable"}
        try:
            doc = self.mongo_db['hotel_facilities'].find_one({"hotel_name": hotel_name}, {"_id": 0}) or {}
            amenities = doc.get('amenities') or {}
            if not isinstance(amenities, dict):
                amenities = {}
            return {"hotel_name": hotel_name, "amenities": amenities}
        except Exception as e:
            return {"hotel_name": hotel_name, "amenities": {}, "error": str(e)}

    def upsert_hotel_amenities(self, hotel_name: str, manager_name: str, amenities: dict) -> str:
        """WRITE hotel amenities (categorical free-form values, UPPERCASE). Merge semantics by category.
        amenities: { category: value } where category ∈ ALLOWED_AMENITIES_CATEGORIES. Empty/None value deletes the category key.
        """
        if self.mongo_db is None:
            return "❌ Database connection not available"
        try:
            coll = self.mongo_db['hotel_facilities']
            base = coll.find_one({"hotel_name": hotel_name})
            current = (base or {}).get('amenities') or {}
            if not isinstance(current, dict):
                current = {}
            for cat, val in (amenities or {}).items():
                key = str(cat).strip().lower()
                if not key or key not in self.ALLOWED_AMENITIES_CATEGORIES:
                    continue
                if val is None or str(val).strip() == "":
                    if key in current:
                        del current[key]
                else:
                    current[key] = str(val).strip().upper()
            coll.update_one(
                {"hotel_name": hotel_name},
                {"$set": {
                    "hotel_name": hotel_name,
                    "amenities": current,
                    "updated_at": datetime.now(timezone.utc),
                    "updated_by": manager_name,
                }},
                upsert=True,
            )
            return f"✅ Amenities updated for {hotel_name}"
        except Exception as e:
            return f"❌ Error updating amenities: {e}"

    # --- Template helpers ---
    def set_keywords_template(self, hotel_name: str, manager_name: str, keywords_template: dict) -> str:
        """Write hotel detail keywords. Natural key is hotel_name + keyword. Empty value deletes a match."""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        try:
            coll = self.mongo_db["hotel_keywords"]
            incoming = keywords_template or {}
            now = datetime.now(timezone.utc)
            for field, keyword, category in self.KEYWORD_TEMPLATE_FIELDS:
                if field not in incoming:
                    continue
                value = str(incoming.get(field) or "").strip()
                existing = coll.find_one({"hotel_name": hotel_name, "keyword": keyword})
                if not value:
                    if existing:
                        coll.delete_one({"hotel_name": hotel_name, "keyword": keyword})
                    continue
                fields = {
                    "hotel_name": hotel_name,
                    "keyword": keyword,
                    "category": category,
                    "description": value,
                    "timestamp": now,
                    "created_by": manager_name,
                    "source": "property_setup",
                }
                if existing:
                    coll.update_one({"hotel_name": hotel_name, "keyword": keyword}, {"$set": fields})
                else:
                    coll.insert_one(fields)
            saved = (self.get_keywords_template(hotel_name).get("keywords_template") or {})
            mismatched = []
            for field, keyword, category in self.KEYWORD_TEMPLATE_FIELDS:
                if field not in incoming:
                    continue
                expected = str(incoming.get(field) or "").strip()
                actual = str(saved.get(field) or "").strip()
                if expected != actual:
                    mismatched.append(field)
            if mismatched:
                return f"❌ Hotel details for {hotel_name} did not save: {', '.join(mismatched)}"
            return f"✅ Hotel details saved for {hotel_name}"
        except Exception as e:
            return f"❌ Error saving hotel details: {e}"

    def get_keywords_template(self, hotel_name: str) -> dict:
        """Return canonical keywords template, prefilled from existing keywords when available."""
        tpl = {
            "contact_phone": "",
            "contact_email": "",
            "timezone": "",
            "address": "",
            "city": "",
            "state_region": "",
            "country": "",
            "country_code": "",
            "checkin_time": "",
            "checkout_time": "",
            "language": [],
            "awards": [],
            "description": "",
        }
        try:
            kw = list(self.mongo_db['hotel_keywords'].find({"hotel_name": hotel_name}, {"_id": 0})) if self.mongo_db is not None else []
            langs = []
            awards = []
            for d in kw:
                cat = str(d.get("category") or "").lower()
                key = str(d.get("keyword") or "").upper()
                desc = str(d.get("description") or "")
                if cat == "contact" and key == "CONTACT_PHONE": tpl["contact_phone"] = desc
                elif cat == "contact" and key == "CONTACT_EMAIL": tpl["contact_email"] = desc
                elif cat == "general" and key == "TIMEZONE": tpl["timezone"] = desc
                elif cat == "general" and key == "DESCRIPTION": tpl["description"] = desc
                elif cat == "location" and key == "ADDRESS": tpl["address"] = desc
                elif cat == "location" and key == "CITY": tpl["city"] = desc
                elif cat == "location" and key == "STATE_REGION": tpl["state_region"] = desc
                elif cat == "location" and key == "COUNTRY_NAME": tpl["country"] = desc
                elif cat == "location" and key == "COUNTRY_CODE": tpl["country_code"] = desc
                elif cat == "hours" and key == "CHECKIN_TIME": tpl["checkin_time"] = desc
                elif cat == "hours" and key == "CHECKOUT_TIME": tpl["checkout_time"] = desc
                elif cat == "language" and key == "LANGUAGE": langs.append(desc)
                elif cat == "awards" and key == "AWARD": awards.append(desc)
            if langs: tpl["language"] = list(dict.fromkeys(langs))
            if awards: tpl["awards"] = list(dict.fromkeys(awards))
        except Exception:
            pass
        return {"hotel_name": hotel_name, "keywords_template": tpl}

    def get_facilities_template(self, hotel_name: str) -> dict:
        """Return full facilities template with all allowed keys (booleans), prefilled from DB."""
        base = self.read_hotel_facilities(hotel_name) or {}
        current = base.get("facilities") or {}
        tpl = {k: bool(current.get(k, False)) for k in sorted(self.ALLOWED_FACILITIES)}
        return {"hotel_name": hotel_name, "facilities_template": tpl}

    def get_amenities_template(self, hotel_name: str) -> dict:
        """Return full amenities template with all categories (values may be empty), prefilled from DB."""
        base = self.read_hotel_amenities(hotel_name) or {}
        current = base.get("amenities") or {}
        tpl = {k: str(current.get(k) or "") for k in sorted(self.ALLOWED_AMENITIES_CATEGORIES)}
        return {"hotel_name": hotel_name, "amenities_template": tpl}

    def set_hotel_facilities_template(self, hotel_name: str, manager_name: str, facilities_full: dict) -> str:
        """MERGE facilities with provided template (only provided keys updated; others unchanged)."""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        try:
            coll = self.mongo_db['hotel_facilities']
            base = coll.find_one({"hotel_name": hotel_name}) or {}
            current = (base.get("facilities") or {}) if isinstance(base, dict) else {}
            if not isinstance(current, dict):
                current = {}
            # Start with current; update only provided keys
            full = dict(current)
            for k, v in (facilities_full or {}).items():
                key = str(k).strip().lower()
                if key in self.ALLOWED_FACILITIES:
                    full[key] = bool(v)
            coll = self.mongo_db['hotel_facilities']
            coll.update_one(
                {"hotel_name": hotel_name},
                {"$set": {
                    "hotel_name": hotel_name,
                    "facilities": full,
                    "updated_at": datetime.now(timezone.utc),
                    "updated_by": manager_name,
                }},
                upsert=True,
            )
            # Read-back verification
            try:
                doc_after = coll.find_one({"hotel_name": hotel_name}, {"_id": 0}) or {}
                fac_now = doc_after.get("facilities") or {}
                requested_true = sorted([k for k, v in full.items() if bool(v)])
                missing = [k for k in requested_true if not bool(fac_now.get(k, False))]
                if missing:
                    return f"✅ Facilities template applied for {hotel_name}\n  • verification: missing={', '.join(missing)}"
                else:
                    return f"✅ Facilities template applied for {hotel_name}\n  • verification: OK ({len(requested_true)} true flags)"
            except Exception:
                return f"✅ Facilities template applied for {hotel_name}"
        except Exception as e:
            return f"❌ Error applying facilities template: {e}"

    def set_hotel_amenities_template(self, hotel_name: str, manager_name: str, amenities_full: dict) -> str:
        """MERGE amenities with provided values (only provided categories updated; empty clears; others unchanged)."""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        try:
            coll = self.mongo_db['hotel_facilities']
            base = coll.find_one({"hotel_name": hotel_name}) or {}
            current = (base.get("amenities") or {}) if isinstance(base, dict) else {}
            if not isinstance(current, dict):
                current = {}
            full = dict(current)
            for k, raw in (amenities_full or {}).items():
                key = str(k).strip().lower()
                if key not in self.ALLOWED_AMENITIES_CATEGORIES:
                    continue
                if raw is None or str(raw).strip() == "":
                    if key in full:
                        del full[key]
                else:
                    full[key] = str(raw).strip().upper()
            coll.update_one(
                {"hotel_name": hotel_name},
                {"$set": {
                    "hotel_name": hotel_name,
                    "amenities": full,
                    "updated_at": datetime.now(timezone.utc),
                    "updated_by": manager_name,
                }},
                upsert=True,
            )
            # Read-back verification
            try:
                doc_after = coll.find_one({"hotel_name": hotel_name}, {"_id": 0}) or {}
                am_now = doc_after.get("amenities") or {}
                mismatches = []
                for k, v in full.items():
                    if (am_now.get(k) or "") != v:
                        mismatches.append(k)
                if mismatches:
                    return f"✅ Amenities template applied for {hotel_name}\n  • verification: mismatches={', '.join(sorted(mismatches))}"
                else:
                    return f"✅ Amenities template applied for {hotel_name}\n  • verification: OK ({len(full)} values)"
            except Exception:
                return f"✅ Amenities template applied for {hotel_name}"
        except Exception as e:
            return f"❌ Error applying amenities template: {e}"

# Remove _parse_hotel_keywords and _process_keyword_query

# Singleton instance for tools
hotel_info_manager = HotelInfoManager()

# Remove all Pydantic BaseModel classes and tool wrappers that use them
# Refactor all tool wrappers to use explicit keyword arguments

 

 

@tool
def read_property_templates(hotel_name: str) -> dict:
    """READ canonical templates prefilled from DB.
    REQUIRED STEP before any write. Always call this first and use the shapes/keys returned.
    Returns: { hotel_name, keywords_template, facilities_template, amenities_template }
    - keywords_template fields: contact_phone, contact_email, timezone, address, city, state_region,
      country, country_code, checkin_time, checkout_time, language[], awards[], description
    - facilities_template: { facility_name:boolean } for the canonical facility list
    - amenities_template: { category:value } where category is canonical, value is free-form (UPPERCASE by caller)
    """
    k = hotel_info_manager.get_keywords_template(hotel_name)
    f = hotel_info_manager.get_facilities_template(hotel_name)
    a = hotel_info_manager.get_amenities_template(hotel_name)
    return {
        "hotel_name": hotel_name,
        "keywords_template": k.get("keywords_template"),
        "facilities_template": f.get("facilities_template"),
        "amenities_template": a.get("amenities_template"),
    }

 

def write_property_templates(hotel_name: str, manager_name: str, facilities_template: dict | None = None, amenities_template: dict | None = None, keywords_template: dict | None = None) -> str:
    """Write property-setup templates. Each section is read, then created or updated on its natural key."""
    parts = []
    if isinstance(facilities_template, dict):
        msg = hotel_info_manager.set_hotel_facilities_template(hotel_name, manager_name, facilities_template)
        if str(msg).startswith("❌"):
            return msg
        parts.append(msg)
    if isinstance(amenities_template, dict):
        msg = hotel_info_manager.set_hotel_amenities_template(hotel_name, manager_name, amenities_template)
        if str(msg).startswith("❌"):
            return msg
        parts.append(msg)
    if isinstance(keywords_template, dict):
        msg = hotel_info_manager.set_keywords_template(hotel_name, manager_name, keywords_template)
        if str(msg).startswith("❌"):
            return msg
        parts.append(msg)
    if not parts:
        return "❌ No property templates to apply"
    return "\n".join(parts)


@tool
def apply_property_templates(hotel_name: str, manager_name: str, facilities_template: dict | None = None, amenities_template: dict | None = None, keywords_template: dict | None = None) -> str:
    """WRITE property templates from the PMS property setup page."""
    return write_property_templates(hotel_name, manager_name, facilities_template, amenities_template, keywords_template)

def parse_hotel_keywords_tool(text: str, hotel_name: str) -> None:
    """Parse unstructured hotel information text into structured keywords using LLM (subagent/tool level). Prints the result directly."""
    from langchain_openai import ChatOpenAI
    import os
    import json
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.1,
        openai_api_key=os.getenv('OPENAI_API_KEY')
    )
    prompt = f"""
    Extract structured keywords from this hotel information text.
    HOTEL: {hotel_name}
    CURRENT TEXT: {text}
    Extract keywords in this format:
    - keyword: the key term
    - category: contact, awards, facilities, hours, policies, amenities, location, room_types
    - description: brief explanation of the keyword
    IMPORTANT CATEGORIZATION:
    - Room types (suite, room, accommodation types) → category: "room_types"
    - Physical facilities (pool, gym, cafe) → category: "facilities"
    Return as JSON array:
    [
        {"keyword": "phone", "category": "contact", "description": "Hotel phone number"},
        {"keyword": "award", "category": "awards", "description": "Hotel recognition"}
    ]
    Focus on:
    - Contact info (phone, email, address)
    - Awards and recognition
    - Facilities (pools, gyms, restaurants)
    - Operating hours
    - Policies (check-in/out times)
    - Amenities and services
    - Location and nearby attractions
    """
    response = llm.invoke(prompt)
    content = response.content.strip()
    if content.startswith('```json'):
        content = content[7:]
    if content.startswith('```'):
        content = content[3:]
    if content.endswith('```'):
        content = content[:-3]
    content = content.strip()
    try:
        keywords = json.loads(content)
        print(keywords)
    except Exception:
        print([
            {"keyword": "hotel_info", "category": "general", "description": f"Information about {hotel_name}"}
        ])

def process_keyword_query_tool(query: str, keywords: list, hotel_name: str) -> None:
    """Process user query against stored keywords using LLM (subagent/tool level). Prints the result directly."""
    from langchain_openai import ChatOpenAI
    import os
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.1,
        openai_api_key=os.getenv('OPENAI_API_KEY')
    )
    keywords_text = ""
    for kw in keywords:
        keywords_text += f"• {kw['keyword']} ({kw['category']}): {kw['description']}\n"
    prompt = f"""
    Answer this query based on the hotel keywords:
    QUERY: {query}
    HOTEL: {hotel_name}
    AVAILABLE KEYWORDS:
    {keywords_text}
    Provide a clear, helpful answer based on the available keywords.
    If no relevant information is found, say so clearly.
    """
    response = llm.invoke(prompt)
    answer = response.content.strip()
    print(answer)

def test_hotel_info_tool():
    """Test hotel info tool functionality"""
    print("🧪 Testing Hotel Info Tool")
    print("=" * 50)
    
    # Test keyword storage
    test_text = """
    Seri Wawasan Putrajaya Hotel - 4-star business hotel.
    Contact: +60 3-8888-9999, reservations@seriwawasan.com
    Awards: Best Business Hotel 2023, Excellence in Service 2022
    Conference Hall capacity 200 guests
    Check-in: 2:00 PM, Check-out: 11:00 AM
    """
    
    result = hotel_info_manager.store_hotel_keywords(test_text, "Seri Wawasan Putrajaya Hotel", "Test Manager")
    print(f"Storage result: {result}")
    
    # Test query
    query_result = hotel_info_manager.query_hotel_keywords("What awards has this hotel won?", "Seri Wawasan Putrajaya Hotel", "Test Manager")
    print(f"Query result: {query_result}")

if __name__ == "__main__":
    test_hotel_info_tool() 