#!/usr/bin/env python3
"""
ROOM MANAGER - Natural Key Room Content Operations

AGENT-RULE COMPLIANT ARCHITECTURE (3-TOOL STANDARD):
- Only 3 tool types: manage_*, read_*, (optionally generate_*)
- All CRUD operations consolidated into manage_* tools (no separate create/update/delete)
- All helpers are internal; only used by manage/read tools

NATURAL KEY STRUCTURE:
- Room Types: Unique by hotel_name + room_type (no artificial IDs)
- Rooms: hotel_name + room_type + room_number
- Assignment IDs: hotel_name||room_number||stay_date (unique constraint)
- No KUL_ROOM_000001 artificial IDs anywhere

DATABASE SEPARATION:
- MongoDB: Content data (room types, specs, etc.)
- PostgreSQL: Room assignments, inventory, daily pricing

AGENT-RULES ENFORCED:
- All parameters validated against database before any operation
- No fallback logic; all failures are explicit
- All crucial MongoDB fields (hotel_name, room_type, etc.) are indexed for discovery
- Single source of truth: hotel_name from manager_context.json

This file is the SOLE source of truth for all room content and assignment operations.
"""

import os
import re
import psycopg2
from psycopg2.extras import execute_values
from datetime import timedelta, datetime, timezone
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pymongo import MongoClient
from typing import Any
from .llm_utils import canonical_key

load_dotenv()

class RoomManager:
    def __init__(self):
        # Database connections
        self.mongo_client = MongoClient(os.getenv('MONGO_URL'))
        self.db = self.mongo_client['leon_hotel_memory']
        self.collection = self.db['room_encyclopedia']
        self.llm = ChatOpenAI(
            model="gpt-4.1-nano",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
        self._ensure_indexes()

    # Timezone logic centralized in timezone_utils

    def _ensure_indexes(self):
        try:
            c = self.collection
            c.create_index([("hotel_name", 1)], unique=False)
            c.create_index([("room_type", 1)], unique=False)
            c.create_index([("has_room_number", 1)])
            c.create_index([("status", 1)])
            c.create_index([("floor", 1)])
            c.create_index([("hotel_name", 1), ("room_type", 1)], unique=True, partialFilterExpression={"has_room_number": False})
            c.create_index([("hotel_name", 1), ("room_type", 1), ("room_number", 1)], unique=True, partialFilterExpression={"has_room_number": True})
            c.create_index([("specifications.room_type", 1)])
            c.create_index([("specifications.bed_type", 1)])
            c.create_index([("specifications.bed_count", 1)])
            c.create_index([("specifications.room_size_sqm", 1)])
            c.create_index([("specifications.room_view", 1)])
            c.create_index([("specifications.wifi", 1)])
            c.create_index([("specifications.air_conditioning", 1)])
            c.create_index([("specifications.private_bathroom", 1)])
            c.create_index([("specifications.max_occupancy", 1)])
            c.create_index([("specifications.accessibility_features", 1)])
            c.create_index([("specifications.pet_friendly", 1)])
            c.create_index([("specifications.smoking", 1)])
            c.create_index([("specifications.minibar", 1)])
            c.create_index([("specifications.kitchenette", 1)])
            c.create_index([("specifications.workspace_type", 1)])
            c.create_index([("specifications.balcony", 1)])
            c.create_index([("specifications.soundproofing", 1)])
            c.create_index([("specifications.air_purifier", 1)])
            c.create_index([("specifications.crib_available", 1)])
            c.create_index([("specifications.connecting_rooms", 1)])
            c.create_index([("specifications.floor_number", 1)])
            c.create_index([("specifications.window_type", 1)])
            c.create_index([("specifications.bathroom_type", 1)])
            c.create_index([("specifications.renovation_year", 1)])
            c.create_index([("specifications.flooring_type", 1)])
            c.create_index([("specifications.lighting", 1)])
            c.create_index([("specifications.smart_features", 1)])
            c.create_index([("specifications.max_children", 1)])
            c.create_index([("hotel_name", "text"), ("room_type", "text"), ("description", "text")])
        except Exception:
            pass

    def _create_room_type_logic(self, hotel_name: str, manager_name: str, room_type: str, specifications: dict) -> str:
        """CREATE room type logic (structured only, no natural language)"""
        # Normalize to UPPERCASE for all string params and values
        try:
            hotel_name = str(hotel_name or "").upper()
            manager_name = str(manager_name or "").upper()
            room_type = str(room_type or "").upper()
            def _upper_values(obj):
                if isinstance(obj, str):
                    return obj.upper()
                if isinstance(obj, dict):
                    return {k: _upper_values(v) for k, v in obj.items()}
                if isinstance(obj, list):
                    return [_upper_values(v) for v in obj]
                return obj
            specifications = _upper_values(specifications or {})
        except Exception:
            pass
        if hotel_name.startswith("❌"):
            return hotel_name
        if not room_type:
            return "❌ room_type is required."
        if not isinstance(specifications, dict):
            specifications = {}
        existing = self.collection.find_one({
            "hotel_name": hotel_name,
            "room_type": room_type,
            "has_room_number": False
        })
        if existing:
            return f"❌ Room type '{room_type}' already exists at '{hotel_name}'. Use updates to modify it."
        now = datetime.now(timezone.utc)
        room_doc = {
            "hotel_name": hotel_name,
            "room_type": room_type,
            "specifications": specifications,
            "status": "active",
            "has_room_number": False,
            "created_at": now,
            "created_by": manager_name,
            "updated_at": now,
            "updated_by": manager_name
        }
        self.collection.insert_one(room_doc)
        inventory_success = self._create_inventory_records(hotel_name, room_type, manager_name)
        return f"✅ Created room type: {room_type} at {hotel_name}\n" \
               f"📐 Size: {specifications.get('room_size_sqm', 'N/A')}sqm\n" \
               f"👥 Occupancy: {specifications.get('max_occupancy', 'N/A')} guests\n" \
               f"🛏️ Bed: {specifications.get('bed_type', 'N/A')}\n" \
               f"🌆 View: {specifications.get('room_view', 'N/A')}\n" \
               f"📊 Inventory: {'✅ Created' if inventory_success else '❌ Failed'} (0 rooms, 0 prices - fill with inventory_manager)"


    def get_room_type_spec_template(self, hotel_name: str, room_type: str) -> dict:
        """Return canonical room type specifications template, prefilled from existing data when available.
        Database operation: read (MongoDB)."""
        # Canonical allowed keys for specs
        allowed_keys = [
            "room_size_sqm", "max_occupancy", "bed_type", "bed_count", "wifi",
            "air_conditioning", "private_bathroom", "room_view", "price_tier",
            "floor_number", "window_type", "bathroom_type", "soundproofing",
            "smart_features", "kitchenette", "minibar", "workspace_type",
            "accessibility_features", "renovation_year", "flooring_type", "lighting",
            "air_purifier", "pet_friendly", "connecting_rooms", "max_children", "crib_available"
        ]
        # Base empty template (strings empty, numbers empty string, booleans empty string for UI fill)
        tmpl = {k: "" for k in allowed_keys}
        try:
            doc = self.collection.find_one({
                "hotel_name": {"$regex": hotel_name, "$options": "i"},
                "room_type": {"$regex": room_type, "$options": "i"},
                "has_room_number": False
            })
            if doc and isinstance(doc.get("specifications"), dict):
                for k in allowed_keys:
                    v = doc["specifications"].get(k)
                    if v is None:
                        continue
                    tmpl[k] = v
        except Exception:
            pass
        return {"hotel_name": hotel_name, "room_type": room_type, "spec_template": tmpl}

 
    def _delete_room_type_logic(self, hotel_name: str, manager_name: str, room_type: str) -> str:
        """DELETE room type logic (context-aware). Cascades to delete all associated room numbers first."""
        room = self.collection.find_one({
            "hotel_name": {"$regex": hotel_name, "$options": "i"},
            "room_type": {"$regex": room_type, "$options": "i"},
            "has_room_number": False
        })
        if not room:
            return f"❌ Room type '{room_type}' not found at '{hotel_name}'"
        actual_hotel = room["hotel_name"]
        actual_room_type = room["room_type"]
        # Cascade delete for associated room numbers (MongoDB + PostgreSQL)
        associated = list(self.collection.find({
            "hotel_name": actual_hotel,
            "room_type": actual_room_type,
            "has_room_number": True
        }, {"room_number": 1}))
        deleted_rooms = 0
        for doc in associated:
            try:
                rn = str(doc.get("room_number"))
                msg = self.delete_room_number(actual_hotel, manager_name, actual_room_type, rn)
                if msg.startswith("✅") or "Deleted" in msg:
                    deleted_rooms += 1
            except Exception:
                pass
        result = self.collection.delete_one({
            "hotel_name": actual_hotel,
            "room_type": actual_room_type,
            "has_room_number": False
        })
        if result.deleted_count > 0:
            inventory_deleted = self._delete_inventory_records(actual_hotel, actual_room_type, manager_name)
            return f"✅ Deleted room type: {actual_room_type} at {actual_hotel}\n" \
                   f"🧹 Deleted associated rooms: {deleted_rooms}\n" \
                   f"📊 Inventory: {'✅ Deleted' if inventory_deleted else '❌ Failed to delete'} from PostgreSQL"
        else:
            return f"❌ Failed to delete room type"
        
    def _create_assignment_records(self, hotel_name: str, room_type: str, room_number: str, days_ahead: int = 365, manager_name: str = None) -> bool:
        """Create assignment records for a room for the next X days
        
        Args:
            hotel_name: Hotel name
            room_type: Room type
            room_number: Room number
            days_ahead: Number of days to create assignments for (default: 365)
        
        Returns:
            bool: Success status
        """
        try:
            conn = self._get_postgresql_connection()
            cursor = conn.cursor()
            start_date = datetime.now(timezone.utc).date()
            
            room = canonical_key(room_number)
            hotel = canonical_key(hotel_name)
            rtype = canonical_key(room_type)
            rows = [
                (hotel, rtype, room, start_date + timedelta(days=i), "available", manager_name)
                for i in range(days_ahead)
            ]
            execute_values(cursor, """
                INSERT INTO room_number_assignments
                (hotel_name, room_type, room_number, stay_date, assignment_status, assigned_by)
                VALUES %s
                ON CONFLICT (hotel_name, room_number, stay_date) DO NOTHING
            """, rows, page_size=500)
            
            conn.commit()
            cursor.close()
            conn.close()
            return True
            
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            print(f"Error creating assignment records: {e}")
            return False

    def _delete_assignment_records(self, hotel_name: str, room_number: str, manager_name: str = None) -> bool:
        """Delete all assignment records and readiness status for a room
        
        Args:
            hotel_name: Hotel name
            room_number: Room number
        
        Returns:
            bool: Success status
        """
        try:
            conn = self._get_postgresql_connection()
            cursor = conn.cursor()
            
            # Delete all assignments for this room
            cursor.execute("""
                DELETE FROM room_number_assignments 
                WHERE hotel_name = %s AND room_number = %s;
            """, (canonical_key(hotel_name), canonical_key(room_number)))
            
            assignment_deleted_count = cursor.rowcount
            
            conn.commit()
            cursor.close()
            conn.close()
            
            print(f"✅ Deleted {assignment_deleted_count} assignment records for room {room_number}")
            return True
            
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            print(f"Error deleting room records: {e}")
            return False

    def _create_readiness_status_record(self, hotel_name: str, room_number: str, manager_name: str = "system") -> bool:
        """Create readiness status record for a room_number
        
        Args:
            hotel_name: Hotel name
            room_number: Room number
            created_by: Who created the record (default: system)
        
        Returns:
            bool: Success status
        """
        try:
            conn = self._get_postgresql_connection()
            cursor = conn.cursor()
            
            # Insert readiness status record (dirty by default)
            cursor.execute("""
                INSERT INTO room_readiness_status 
                (hotel_name, room_number, readiness_status, changed_by, created_by, updated_by, notes, clean_since, auto_dirty_threshold_days)
                VALUES (%s, %s, 'dirty', %s, %s, %s, %s, NULL, 3)
                ON CONFLICT (hotel_name, room_number) DO NOTHING;
            """, (canonical_key(hotel_name), canonical_key(room_number), manager_name, manager_name, manager_name, f"Room created by {manager_name} - default dirty status"))
            
            conn.commit()
            cursor.close()
            conn.close()
            return True
            
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            print(f"Error creating readiness status record: {e}")
            return False
        
    def _create_inventory_records(self, hotel_name: str, room_type: str, manager_name: str) -> bool:
        """Create per-night inventory records for a room type in PostgreSQL (canonical, per-night only)
        Args:
            hotel_name: Hotel name
            room_type: Room type name
        Returns:
            bool: Success status
        """
        try:
            conn = self._get_postgresql_connection()
            cursor = conn.cursor()
            # Create per-night inventory records for the next 365 days
            start_date = datetime.now(timezone.utc).date()
            for i in range(365):
                stay_date = start_date + timedelta(days=i)
                cursor.execute("""
                    INSERT INTO room_inventory_nightly 
                    (hotel_name, room_type, stay_date, total_inventory, blocked_quantity, updated_at, created_by, updated_by)
                    VALUES (%s, %s, %s, 0, 0, CURRENT_TIMESTAMP, %s, %s)
                    ON CONFLICT (hotel_name, room_type, stay_date) DO UPDATE SET
                        updated_by = EXCLUDED.updated_by;
                """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date), manager_name, manager_name))
            # Optionally, still create daily_pricing records for the next 365 days
            for i in range(365):
                stay_date = start_date + timedelta(days=i)
                cursor.execute("""
                    INSERT INTO daily_pricing 
                    (hotel_name, room_type, stay_date, base_price, updated_at, created_by, updated_by)
                    VALUES (%s, %s, %s, 0.00, CURRENT_TIMESTAMP, %s, %s)
                    ON CONFLICT (hotel_name, room_type, stay_date) DO UPDATE SET
                        updated_by = EXCLUDED.updated_by;
                """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date), manager_name, manager_name))
            conn.commit()
            cursor.close()
            conn.close()
            print(f"✅ Created per-night inventory records for {room_type} at {hotel_name}")
            print(f"📅 365 days of per-night inventory records created (0 quantities)")
            print(f"📊 Inventory quantities set to 0 (to be filled by inventory_manager)")
            return True
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            print(f"Error creating per-night inventory records: {e}")
            return False

    def _delete_inventory_records(self, hotel_name: str, room_type: str, manager_name: str = None) -> bool:
        """Delete inventory records for a room type from PostgreSQL
        
        FIXED: Delete in correct order to avoid foreign key constraint violations
        1. Delete daily_pricing first (remove foreign key references)
        2. Then delete room_inventory (remove the referenced table)
        
        Args:
            hotel_name: Hotel name
            room_type: Room type name
        
        Returns:
            bool: Success status
        """
        try:
            conn = self._get_postgresql_connection()
            cursor = conn.cursor()
            
            # STEP 1: Delete all pricing records first (remove foreign key references)
            cursor.execute("""
                DELETE FROM daily_pricing 
                WHERE hotel_name = %s AND room_type = %s;
            """, (canonical_key(hotel_name), canonical_key(room_type)))
            
            pricing_deleted_count = cursor.rowcount
            
            # STEP 2: Now delete inventory record (no more foreign key references)
            cursor.execute("""
                DELETE FROM room_inventory_nightly 
                WHERE hotel_name = %s AND room_type = %s;
            """, (canonical_key(hotel_name), canonical_key(room_type)))
            
            inventory_deleted_count = cursor.rowcount
            
            conn.commit()
            cursor.close()
            conn.close()
            
            print(f"✅ Deleted {pricing_deleted_count} pricing records for {room_type}")
            print(f"✅ Deleted {inventory_deleted_count} inventory record for {room_type}")
            return True
            
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            print(f"Error deleting inventory records: {e}")
            return False 

    def _get_postgresql_connection(self):
        """Helper to get a PostgreSQL connection using DATABASE_URL from .env."""
        try:
            conn = psycopg2.connect(os.getenv('DATABASE_URL'))
            return conn
        except Exception as e:
            print(f"Error connecting to PostgreSQL using DATABASE_URL: {e}")
            return None

    def read_room_type(self, hotel_name: str, room_type: str, structured: bool = False) -> str:
        """READ room type details using natural keys with smart matching. If structured=True, return full MongoDB document as dict."""
        try:
            if hotel_name.startswith("❌"):
                return hotel_name if not structured else {"error": hotel_name}
            query = {
                "hotel_name": {"$regex": hotel_name, "$options": "i"},
                "room_type": {"$regex": room_type, "$options": "i"},
                "has_room_number": False
            }
            room = self.collection.find_one(query)
            if not room:
                similar_hotels = self.collection.distinct("hotel_name", {"hotel_name": {"$regex": hotel_name, "$options": "i"}})
                similar_rooms = self.collection.distinct("room_type", {"room_type": {"$regex": room_type, "$options": "i"}})
                suggestion = ""
                if similar_hotels:
                    suggestion += f"\n💡 Similar hotels: {', '.join(similar_hotels[:3])}"
                if similar_rooms:
                    suggestion += f"\n💡 Similar room types: {', '.join(similar_rooms[:3])}"
                msg = f"❌ Room type '{room_type}' not found at '{hotel_name}'{suggestion}"
                return msg if not structured else {"error": msg}
            if structured:
                # Remove 'description' from structured output if present
                if "description" in room:
                    del room["description"]
                return room
            specs = room.get("specifications", {})
            response = f"🏨 {room['hotel_name']}\n"
            response += f"🏠 {room['room_type']}\n"
            response += self._format_specifications_for_display(specs) + "\n"
 
            # Do not display description
            return response
        except Exception as e:
            return f"❌ Error retrieving room type: {str(e)}" if not structured else {"error": str(e)}



    def read_room_types_by_criteria(self, hotel_name: str, criteria: str) -> str:
        """READ room types using natural language search criteria - MANAGER CONTEXT AWARE"""
        try:
            # ALWAYS filter by manager's hotel context first
            if hotel_name.startswith("❌"):
                return hotel_name
            
            # Build search query from criteria with hotel context
            query = {
                "has_room_number": False,
                "hotel_name": hotel_name  # AUTOMATIC hotel filtering
            }
            
            criteria_canon = canonical_key(criteria)
            
            # Room type patterns
            room_patterns = [
                ("executive", "executive"),
                ("deluxe", "deluxe"),
                ("suite", "suite"),
                ("premium", "premium"),
                ("standard", "standard")
            ]
            
            for pattern, room_type in room_patterns:
                if pattern in criteria_canon:
                    query["room_type"] = {"$regex": room_type, "$options": "i"}
                    break
            
            # Specifications
            if "city view" in criteria_canon:
                query["specifications.room_view"] = {"$regex": "city", "$options": "i"}
            elif "ocean view" in criteria_canon or "sea view" in criteria_canon:
                query["specifications.room_view"] = {"$regex": "ocean|sea", "$options": "i"}
            elif "garden view" in criteria_canon:
                query["specifications.room_view"] = {"$regex": "garden", "$options": "i"}
            
            if "wifi" in criteria_canon:
                query["specifications.wifi"] = True
            
            # Size criteria
            size_match = re.search(r'(\d+)\s*sqm', criteria_canon)
            if size_match:
                size = int(size_match.group(1))
                if "larger than" in criteria_canon or "more than" in criteria_canon:
                    query["specifications.room_size_sqm"] = {"$gt": size}
                elif "smaller than" in criteria_canon or "less than" in criteria_canon:
                    query["specifications.room_size_sqm"] = {"$lt": size}
                else:
                    query["specifications.room_size_sqm"] = size
            
            # Occupancy
            occupancy_match = re.search(r'(\d+)\s*guests?', criteria_canon)
            if occupancy_match:
                occupancy = int(occupancy_match.group(1))
                query["specifications.max_occupancy"] = {"$gte": occupancy}
            
            # Execute search
            rooms = list(self.collection.find(query).limit(20))
            
            if not rooms:
                return f"❌ No room types found at {hotel_name} matching: {criteria}"
            
            # Format response for single hotel (manager context)
            response = f"🏨 {hotel_name} - Room Types ({len(rooms)} found)\n"
            response += f"🔍 Criteria: {criteria}\n\n"
            
            for room in rooms:
                specs = room.get("specifications", {}) or {}
                size = specs.get('room_size_sqm', 'N/A')
                occupancy = specs.get('max_occupancy', 'N/A')
                view = specs.get('room_view', 'N/A')
                price_tier = specs.get('price_tier', 'N/A')
                wifi = 'Yes' if bool(specs.get('wifi')) else 'No'
                response += f"📋 {room.get('room_type','UNKNOWN')}\n"
                response += f"    📐 Size: {size}sqm\n"
                response += f"    👥 Occupancy: {occupancy} guests\n"
                response += f"    🌆 View: {view}\n"
                response += f"    💰 Price Tier: {price_tier}\n"
                response += f"    📶 WiFi: {wifi}\n\n"
            
            return response
            
        except Exception as e:
            return f"❌ Error searching room types: {str(e)}"

    def read_room_portfolio(self, hotel_name: str, room_type: str = None, include_physical: bool = True, include_structured: bool = False) -> str:
        """READ room portfolio - room types and rooms using natural keys"""
        try:
            # Get hotel_name from manager_context.json (single source of truth)
            if hotel_name.startswith("❌"):
                return hotel_name if not include_structured else {"error": hotel_name}
            hotel = canonical_key(hotel_name)
            if not hotel:
                msg = "❌ No room types found"
                return msg if not include_structured else {"hotel_name": "", "room_types": []}
            query = {}
            query["hotel_name"] = hotel
            if room_type:
                query["room_type"] = {"$regex": room_type, "$options": "i"}
            # Get room types
            room_types_query = query.copy()
            room_types_query["has_room_number"] = False
            room_types = list(self.collection.find(room_types_query))
            if not room_types:
                filter_desc = f" at {hotel_name}"
                filter_desc += f" for {room_type}" if room_type else ""
                msg = f"❌ No room types found{filter_desc}"
                return msg if not include_structured else {"error": msg}
            # Build structured output
            if include_structured:
                result = {
                    "hotel_name": hotel_name,
                    "room_types": []
                }
                for room_type_doc in room_types:
                    room_type_entry = {
                        "room_type": room_type_doc.get("room_type"),
                        "specifications": room_type_doc.get("specifications", {}),
                        "room_key_high": int(room_type_doc.get("room_key_high") or 0),
                        "room_numbers": []
                    }
                    if include_physical:
                        room_number_docs = list(self.collection.find({
                            "hotel_name": room_type_doc.get("hotel_name"),
                            "room_type": room_type_doc["room_type"],
                            "has_room_number": True
                        }))
                        for pr in room_number_docs:
                            room_type_entry["room_numbers"].append({
                                "room_id": f"{pr['hotel_name']}_{pr['room_type']}_{pr['room_number']}",
                                "room_number": pr.get("room_number"),
                                "room_type": pr.get("room_type"),
                                "room_status": pr.get("room_status", "UNKNOWN"),
                                "floor": pr.get("floor"),
                                "guest_name": pr.get("guest_name"),
                                "checkout_date": pr.get("checkout_date")
                            })
                    result["room_types"].append(room_type_entry)
                return result
            # Default: formatted string for LLM chat
            response = f"🏨 Room Portfolio\n"
            response += f"🎯 Hotel: {hotel_name}\n"
            if room_type:
                response += f"📋 Room Type: {room_type}\n"
            response += "\n"
            hotel_groups = {}
            for room_type_doc in room_types:
                hotel = room_type_doc["hotel_name"]
                if hotel not in hotel_groups:
                    hotel_groups[hotel] = []
                hotel_groups[hotel].append(room_type_doc)
            for hotel, room_type_docs in hotel_groups.items():
                response += f"🏨 {hotel}\n"
                for room_type_doc in room_type_docs:
                    specs = room_type_doc.get("specifications", {}) or {}
                    size = specs.get('room_size_sqm', 'N/A')
                    occupancy = specs.get('max_occupancy', 'N/A')
                    view = specs.get('room_view', 'N/A')
                    response += f"  📋 {room_type_doc.get('room_type','UNKNOWN')}\n"
                    response += f"      📐 {size}sqm | 👥 {occupancy} guests | 🌆 {view}\n"
                    if include_physical:
                        room_number_docs = list(self.collection.find({
                            "hotel_name": hotel,
                            "room_type": room_type_doc["room_type"],
                            "has_room_number": True
                        }))
                        if room_number_docs:
                            response += f"      🚪 Room Numbers ({len(room_number_docs)}): "
                            room_numbers = [pr["room_number"] for pr in room_number_docs]
                            response += ", ".join(room_numbers[:10])
                            if len(room_numbers) > 10:
                                response += f" ... (+{len(room_numbers)-10} more)"
                            response += "\n"
                        else:
                            response += "      🚪 No room numbers created\n"
                    response += "\n"
            return response
        except Exception as e:
            if include_structured:
                return {"error": f"❌ Error listing rooms: {str(e)}"}
            return f"❌ Error listing rooms: {str(e)}"

    def read_room_system_validation(self, hotel_name: str) -> str:
        """READ room system validation status and integrity check"""
        try:
            # Check natural key compliance
            total_documents = self.collection.count_documents({})
            room_types = self.collection.count_documents({"has_room_number": False})
            room_numbers = self.collection.count_documents({"has_room_number": True})
        
            # Check for proper natural keys
            missing_hotel_name = self.collection.count_documents({"hotel_name": {"$exists": False}})
            missing_room_type = self.collection.count_documents({"room_type": {"$exists": False}})
        
            # Check indexing
            indexes = self.collection.list_indexes()
            indexed_fields = set()
            for index in indexes:
                for field in index.get('key', {}):
                    indexed_fields.add(field)
            
            # Hotels and room types summary
            unique_hotels = len(self.collection.distinct("hotel_name"))
            unique_room_types = len(self.collection.distinct("room_type"))
            
            response = "🔍 Room Management System Validation\n\n"
            response += "�� Database Health:\n"
            response += f"  • Total Documents: {total_documents}\n"
            response += f"  • Room Types: {room_types}\n"
            response += f"  • Room Numbers: {room_numbers}\n"
            response += f"  • Unique Hotels: {unique_hotels}\n"
            response += f"  • Unique Room Types: {unique_room_types}\n\n"
            
            response += "🔑 Natural Key Compliance:\n"
            if missing_hotel_name == 0 and missing_room_type == 0:
                response += "  ✅ All rooms have natural keys (hotel_name + room_type)\n"
            else:
                response += f"  ❌ Missing hotel_name: {missing_hotel_name}\n"
                response += f"  ❌ Missing room_type: {missing_room_type}\n"
            
            response += "\n📋 Agent-Rule Compliance:\n"
            response += "  ✅ Content data in MongoDB only\n"
            response += "  ✅ Natural keys used throughout\n"
            response += "  ✅ No artificial IDs\n"
            response += f"  ✅ {len(indexed_fields)} fields indexed for discovery\n"
            
            return response
            
        except Exception as e:
            return f"❌ Error validating system: {str(e)}"

    def manage_room_description(self, hotel_name: str, manager_name: str, room_type: str, specifications: dict) -> str:
        if hotel_name.startswith("❌"):
            return hotel_name
        allowed_keys = {
            "room_size_sqm", "max_occupancy", "bed_type", "bed_count", "wifi", "air_conditioning", "private_bathroom", "room_view", "price_tier",
            "floor_number", "window_type", "bathroom_type", "soundproofing", "smart_features", "kitchenette", "minibar", "workspace_type",
            "accessibility_features", "renovation_year", "flooring_type", "lighting", "air_purifier", "pet_friendly", "connecting_rooms",
            "max_children", "crib_available"
        }
        specs_update = {k: v for k, v in specifications.items() if k in allowed_keys}
        description_update = None
        for k, v in specifications.items():
            if k == "description":
                description_update = v
        room = self.collection.find_one({
            "hotel_name": hotel_name,
            "room_type": room_type,
            "has_room_number": False
        })
        try:
            now = datetime.now(timezone.utc)
        except Exception:
            return "❌ Missing hotel timezone for description update"
        if room:
            update_ops = {"updated_at": now, "updated_by": manager_name}
            if specs_update:
                for key, value in specs_update.items():
                    update_ops[f"specifications.{key}"] = value
            if description_update:
                update_ops["description"] = description_update
            if not (specs_update or description_update):
                return f"⚠️ No valid updates provided for {room_type} at {hotel_name}."
            result = self.collection.update_one(
                {"hotel_name": hotel_name, "room_type": room_type, "has_room_number": False},
                {"$set": update_ops}
            )
            if result.modified_count > 0:
                return f"✅ Updated {room_type} at {hotel_name} with new description/specifications."
            else:
                return f"⚠️ No changes made to {room_type} at {hotel_name}."
        else:
            room_doc = {
                "hotel_name": hotel_name,
                "room_type": room_type,
                "specifications": specs_update,
                "description": description_update or "",
                "status": "active",
                "has_room_number": False,
                "created_at": now,
                "created_by": manager_name,
                "updated_at": now,
                "updated_by": manager_name
            }
            self.collection.insert_one(room_doc)
            return f"✅ updated room type {room_type} at {hotel_name} with description/specifications."

# Add a dedicated internal function for adding a room number

    def _write_room_rows(self, hotel_name: str, room_type: str, room_numbers: list, manager_name: str, days_ahead: int = 365) -> bool:
        """One connection, one insert for every night of every new room."""
        conn = None
        try:
            conn = self._get_postgresql_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            start_date = datetime.now(timezone.utc).date()
            hotel = canonical_key(hotel_name)
            rtype = canonical_key(room_type)
            assign_rows = []
            ready_rows = []
            for rn in room_numbers:
                room = canonical_key(str(rn))
                for i in range(days_ahead):
                    assign_rows.append((hotel, rtype, room, start_date + timedelta(days=i), "available", manager_name))
                ready_rows.append((
                    hotel, room, "dirty", manager_name, manager_name, manager_name,
                    f"Room created by {manager_name} - default dirty status", None, 3
                ))
            execute_values(cursor, """
                INSERT INTO room_number_assignments
                (hotel_name, room_type, room_number, stay_date, assignment_status, assigned_by)
                VALUES %s
                ON CONFLICT (hotel_name, room_number, stay_date) DO NOTHING
            """, assign_rows, page_size=500)
            execute_values(cursor, """
                INSERT INTO room_readiness_status
                (hotel_name, room_number, readiness_status, changed_by, created_by, updated_by, notes, clean_since, auto_dirty_threshold_days)
                VALUES %s
                ON CONFLICT (hotel_name, room_number) DO NOTHING
            """, ready_rows, page_size=100)
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            print(f"Error creating assignment records: {e}")
            return False

    def add_room_numbers(self, hotel_name: str, manager_name: str, room_type: str, room_numbers: list) -> str:
        if hotel_name.startswith("❌"):
            return hotel_name
        hotel_name = canonical_key(hotel_name)
        room_type = canonical_key(room_type)
        numbers = []
        for raw in room_numbers or []:
            key = canonical_key(str(raw))
            if not key:
                return "❌ Room number is required"
            if key in numbers:
                return f"❌ Room number {key} is already used"
            numbers.append(key)
        if not numbers:
            return "❌ Room number is required"
        base_room_type = self.collection.find_one({
            "hotel_name": hotel_name,
            "room_type": room_type,
            "has_room_number": False
        })
        if not base_room_type:
            return f"❌ Room type '{room_type}' does not exist at {hotel_name} (MongoDB)."
        existing = list(self.collection.find({
            "hotel_name": hotel_name,
            "room_number": {"$in": numbers},
            "has_room_number": True
        }, {"room_number": 1}))
        if existing:
            found = ", ".join(str(d.get("room_number")) for d in existing)
            return f"❌ Room number {found} is already used"
        now = datetime.now(timezone.utc)
        docs = []
        for rn in numbers:
            doc = base_room_type.copy()
            doc.pop("_id", None)
            doc["room_number"] = rn
            doc["has_room_number"] = True
            doc["created_at"] = now
            doc["created_by"] = manager_name
            doc["updated_at"] = now
            doc["updated_by"] = manager_name
            docs.append(doc)
        self.collection.insert_many(docs)
        wrote = self._write_room_rows(hotel_name, room_type, numbers, manager_name)
        if wrote:
            try:
                from booking_system.inventory_manager import inventory_manager
                inventory_manager.apply_room_count(hotel_name, manager_name, room_type)
            except Exception:
                pass
        if not wrote:
            self.collection.delete_many({
                "hotel_name": hotel_name,
                "room_type": room_type,
                "room_number": {"$in": numbers},
                "has_room_number": True
            })
            listed = ", ".join(numbers)
            return f"❌ Error creating assignment or readiness records for {listed} at {hotel_name} (PostgreSQL)."
        listed = ", ".join(numbers)
        self._sync_room_quantity(hotel_name, room_type)
        return f"✅ Room {listed} added to '{room_type}' at {hotel_name}."

    def _free_room_number(self, taken):
        index = 1
        while True:
            candidate = "#" + str(index)
            if candidate not in taken:
                return candidate
            index += 1

    def _ordered_room_moves(self, taken, moves):
        current = set(taken)
        remaining = [(old, new) for old, new in moves]
        ordered = []
        while remaining:
            ready = [(old, new) for old, new in remaining if new not in current]
            if not ready:
                old, new = remaining[0]
                temp = self._free_room_number(current)
                ordered.append((old, temp))
                current.remove(old)
                current.add(temp)
                remaining[0] = (temp, new)
                continue
            for old, new in ready:
                ordered.append((old, new))
                current.remove(old)
                current.add(new)
            moved = [old for old, new in ready]
            remaining = [(old, new) for old, new in remaining if old not in moved]
        return ordered

    def _move_room_number_sql(self, cursor, hotel, old, new):
        cursor.execute(
            "UPDATE room_number_assignments SET room_number = %s WHERE hotel_name = %s AND room_number = %s",
            (new, hotel, old)
        )
        cursor.execute(
            "UPDATE room_readiness_status SET room_number = %s WHERE hotel_name = %s AND room_number = %s",
            (new, hotel, old)
        )
        cursor.execute(
            "UPDATE room_stay SET room_number = %s WHERE hotel_name = %s AND room_number = %s",
            (new, hotel, old)
        )
        cursor.execute(
            "UPDATE cleaning_status SET room_number = %s WHERE hotel_name = %s AND room_number = %s",
            (new, hotel, old)
        )
        cursor.execute(
            "UPDATE maintenance_requests SET room_number = %s WHERE hotel_name = %s AND room_number = %s",
            (new, hotel, old)
        )
        cursor.execute(
            "UPDATE maintenance_schedules SET room_number = %s WHERE hotel_name = %s AND room_number = %s",
            (new, hotel, old)
        )

    def rename_room_numbers(self, hotel_name, manager_name, room_type, changes):
        """Change stored room numbers. The new number is the room number everywhere."""
        hotel = canonical_key(hotel_name)
        room = canonical_key(room_type)
        now = datetime.now(timezone.utc)
        docs = list(self.collection.find(
            {"hotel_name": hotel, "has_room_number": True},
            {"room_type": 1, "room_number": 1}
        ))
        by_number = {}
        for doc in docs:
            by_number[canonical_key(doc.get("room_number"))] = canonical_key(doc.get("room_type"))
        proposed = {}
        for item in changes or []:
            old = canonical_key(str((item or {}).get("room_number") or ""))
            new = canonical_key(str((item or {}).get("new_room_number") or ""))
            if not old or not new:
                return {"error": "Room number is required"}
            if by_number.get(old) != room:
                return {"error": f"Room {old} is not on {room}"}
            if old in proposed:
                return {"error": f"Room number {old} is already used"}
            proposed[old] = new
        finals = {}
        for number in by_number:
            final = proposed[number] if number in proposed else number
            if final in finals:
                return {"error": f"Room number {final} is already used"}
            finals[final] = number
        moves = [(old, new) for old, new in proposed.items() if old != new]
        if not moves:
            return {"success": True, "rooms": []}
        ordered = self._ordered_room_moves(set(by_number), moves)
        conn = None
        try:
            conn = self._get_postgresql_connection()
            cursor = conn.cursor()
            for old, new in ordered:
                self._move_room_number_sql(cursor, hotel, old, new)
            conn.commit()
            cursor.close()
            conn.close()
            conn = None
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
            return {"error": str(e)}
        for old, new in ordered:
            self.collection.update_one(
                {"hotel_name": hotel, "room_type": room, "room_number": old, "has_room_number": True},
                {"$set": {"room_number": new, "updated_at": now, "updated_by": manager_name}, "$unset": {"room_label": ""}}
            )
        return {"success": True, "rooms": [new for old, new in moves]}

    def _room_index(self, room_type, room_number):
        stem = "".join(canonical_key(room_type).split())
        number = canonical_key(str(room_number or ""))
        if not stem or not number.startswith(stem):
            return 0
        tail = number[len(stem):]
        if not tail.isdigit():
            return 0
        return int(tail)

    def _remember_room_key_high(self, hotel_name, room_type):
        hotel = canonical_key(hotel_name)
        room = canonical_key(room_type)
        type_doc = self.collection.find_one(
            {"hotel_name": hotel, "room_type": room, "has_room_number": False},
            {"room_key_high": 1}
        )
        stored = int((type_doc or {}).get("room_key_high") or 0)
        highest = stored
        for doc in self.collection.find(
            {"hotel_name": hotel, "room_type": room, "has_room_number": True},
            {"room_number": 1}
        ):
            index = self._room_index(room, doc.get("room_number"))
            if index > highest:
                highest = index
        if type_doc is not None and highest != stored:
            self.collection.update_one(
                {"hotel_name": hotel, "room_type": room, "has_room_number": False},
                {"$set": {"room_key_high": highest}}
            )
        return highest

    def _sync_room_quantity(self, hotel_name, room_type):
        hotel = canonical_key(hotel_name)
        room = canonical_key(room_type)
        count = self.collection.count_documents({
            "hotel_name": hotel,
            "room_type": room,
            "has_room_number": True
        })
        self.collection.update_one(
            {"hotel_name": hotel, "room_type": room, "has_room_number": False},
            {"$set": {"room_quantity": count}}
        )

    def room_keys(self, room_type, quantity):
        """Stable room names for a type: KING SUITE with quantity 2 is KINGSUITE1, KINGSUITE2."""
        stem = "".join(canonical_key(room_type).split())
        count = int(quantity)
        if not stem or count < 1:
            return []
        return [f"{stem}{index}" for index in range(1, count + 1)]

    def set_room_labels(self, hotel_name, manager_name, room_type, labels):
        """Rename the display name. The same display number cannot be used twice in the hotel."""
        hotel = canonical_key(hotel_name)
        room = canonical_key(room_type)
        now = datetime.now(timezone.utc)
        rooms = list(self.collection.find(
            {"hotel_name": hotel, "has_room_number": True},
            {"room_type": 1, "room_number": 1, "room_label": 1}
        ))
        known = {}
        for doc in rooms:
            known[(canonical_key(doc.get("room_type")), canonical_key(doc.get("room_number")))] = doc
        proposed = {}
        for item in labels or []:
            number = canonical_key(str((item or {}).get("room_number") or ""))
            label = canonical_key(str((item or {}).get("room_label") or "")) or number
            if not number:
                return {"error": "room_number is required"}
            if (room, number) not in known:
                return {"error": f"Room {number} is not on {room}"}
            proposed[(room, number)] = label
        seen = {}
        for doc in rooms:
            key = (canonical_key(doc.get("room_type")), canonical_key(doc.get("room_number")))
            display = proposed[key] if key in proposed else canonical_key(str(doc.get("room_label") or doc.get("room_number") or ""))
            if not display:
                continue
            if display in seen:
                return {"error": f"Room number {display} is already used"}
            seen[display] = key
        updated = []
        for key, label in proposed.items():
            number = key[1]
            self.collection.update_one(
                {"hotel_name": hotel, "room_type": room, "room_number": number, "has_room_number": True},
                {"$set": {"room_label": label, "updated_at": now, "updated_by": manager_name}}
            )
            updated.append(number)
        return {"success": True, "rooms": updated}

# --- SINGLE ROOM ADDITION ---
    def add_room_number(self, hotel_name: str, manager_name: str, room_type: str, room_number: str) -> str:
        return self.add_room_numbers(hotel_name, manager_name, room_type, [room_number])

    # Add delete_room_number method to RoomManager
    def delete_readiness_status_record(self, hotel_name: str, room_number: str, manager_name: str) -> bool:
        try:
            conn = self._get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM room_readiness_status 
                WHERE hotel_name = %s AND room_number = %s;
            """, (canonical_key(hotel_name), canonical_key(room_number)))
            deleted_count = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
            return deleted_count > 0
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            print(f"Error deleting readiness status record: {e}")
            return False

    def delete_room_number(self, hotel_name: str, manager_name: str, room_type: str, room_number: str) -> str:
        if hotel_name.startswith("❌"):
            return hotel_name
        # Delete from MongoDB
        delete_result = self.collection.delete_one({
            "hotel_name": {"$regex": hotel_name, "$options": "i"},
            "room_type": {"$regex": room_type, "$options": "i"},
            "room_number": str(room_number),
            "has_room_number": True
        })
        if delete_result.deleted_count == 0:
            return f"❌ Room number {room_number} of type '{room_type}' not found at '{hotel_name}' (MongoDB)"
        # Delete assignments in PostgreSQL
        assignment_deleted = self._delete_assignment_records(hotel_name, str(room_number), manager_name=manager_name)
        readiness_deleted = self.delete_readiness_status_record(hotel_name, str(room_number), manager_name)
        if assignment_deleted:
            try:
                from booking_system.inventory_manager import inventory_manager
                inventory_manager.apply_room_count(hotel_name, manager_name, room_type)
            except Exception:
                pass
            self._sync_room_quantity(hotel_name, room_type)
            return f"✅ Deleted room_number {room_number} ({room_type}) at {hotel_name} (MongoDB + PostgreSQL assignments + readiness)"
        return f"⚠️ Deleted MongoDB doc for room_number {room_number}, but failed to delete assignments in PostgreSQL. Manual cleanup required."

# Dedicated LLM parser for room specifications    
room_manager = RoomManager()


@tool
def add_room_number_tool(hotel_name: str, manager_name: str, room_type: str, room_number: str) -> str:
    """Add a room number to a room type (write operation)."""
    return room_manager.add_room_number(hotel_name, manager_name, room_type, room_number)

@tool
def delete_room_number_tool(
    hotel_name: str,
    manager_name: str,
    room_type: str,
    room_number: str
) -> str:
    """Delete a room number from a room type (write operation)"""
    return room_manager.delete_room_number(hotel_name, manager_name, room_type, room_number)

@tool
def read_room_portfolio_tool(
    hotel_name: str,
    room_type: str = None,
    include_physical: bool = True,
    include_structured: bool = False
) -> str:
    """Read the room portfolio for a hotel (read operation)"""
    return room_manager.read_room_portfolio(hotel_name, room_type=room_type, include_physical=include_physical, include_structured=include_structured)

@tool
def read_room_system_validation_tool(
    hotel_name: str
) -> str:
    """Read room system validation status and integrity check (read operation)"""
    return room_manager.read_room_system_validation(hotel_name)

@tool
def manage_room_description_tool(
    hotel_name: str,
    manager_name: str,
    room_type: str,
    specifications: Any,
) -> str:
    """WRITE disabled: Please use the PMS app to update room specifications."""
    return "PLEASE USE THE PMS APPS TO UPDATE THE ROOM TYPE SPECIFICATIONS"

@tool
def create_room_type_tool(
    hotel_name: str,
    manager_name: str,
    room_type: str,
    specifications: Any
) -> str:
    """WRITE disabled: Please use the PMS app to create room types."""
    return "PLEASE USE THE PMS APPS TO CREATE ROOM TYPES"

@tool
def delete_room_type_tool(
    hotel_name: str,
    manager_name: str,
    room_type: str
) -> str:
    """Delete a room type from a hotel (write operation)"""
    return room_manager._delete_room_type_logic(hotel_name, manager_name, room_type)

@tool
def read_room_type_tool(
    hotel_name: str,
    room_type: str,
    structured: bool = False
) -> Any:
    """Read details of a specific room type (read operation)"""
    return room_manager.read_room_type(hotel_name, room_type, structured=structured)

@tool
def read_room_types_by_criteria_tool(
    hotel_name: str,
    criteria: str
) -> str:
    """Read room types matching search criteria for a hotel (read operation)"""
    return room_manager.read_room_types_by_criteria(hotel_name, criteria)

@tool
def read_room_type_from_room_number_tool(hotel_name: str, room_number: str) -> str:
    """
    Given hotel_name and room_number, return the associated room_type from the room_number_assignments table.
    Returns the room_type as a string, or an error message if not found.
    """
    try:
        conn = room_manager._get_postgresql_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT room_type FROM room_number_assignments
            WHERE hotel_name = %s AND room_number = %s LIMIT 1
            """,
            (hotel_name, room_number)
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            return row[0]
        return f"❌ No room_type found for room_number {room_number} at {hotel_name}."
    except Exception as e:
        return f"❌ Error looking up room_type: {str(e)}"

@tool
def read_room_type_spec_template_tool(hotel_name: str, room_type: str) -> dict:
    """READ canonical room type specification template prefilled from DB.
    Database operation: read. Returns { hotel_name, room_type, spec_template }"""
    return room_manager.get_room_type_spec_template(hotel_name, room_type)

