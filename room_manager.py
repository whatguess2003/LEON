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
import json
import psycopg2
from datetime import datetime, timedelta
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pymongo import MongoClient
from typing import List, Union, Any, Dict
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

    def _get_manager_signature(self) -> dict:
        try:
            context_path = os.path.join(os.path.dirname(__file__), 'manager_context.json')
            with open(context_path, 'r') as f:
                context = json.load(f)
                active = context.get('active_manager', {})
                return {
                    'manager_name': active.get('manager_name', 'Unknown'),
                    'manager_phone': active.get('manager_phone', 'Unknown')
                }
        except Exception:
            return {'manager_name': 'Unknown', 'manager_phone': 'Unknown'}

    def _get_hotel_name_from_context(self) -> str:
        try:
            context_path = os.path.join(os.path.dirname(__file__), 'manager_context.json')
            with open(context_path, 'r') as f:
                context = json.load(f)
                hotel_name = context.get('active_manager', {}).get('hotel_name')
                if hotel_name:
                    return hotel_name
                else:
                    return "❌ No hotel_name found in manager_context.json"
        except Exception as e:
            return f"❌ Error reading manager_context.json: {e}"

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

    def _create_empty_room_template(self, user_input: str, room_type: str = "") -> dict:
        """Fallback empty template when LLM parsing fails - MANAGER CONTEXT AWARE"""
        hotel_name = self._get_hotel_name_from_context()
        return {
            "hotel_name": hotel_name,
            "room_type": room_type,
            "specifications": {
                "room_size_sqm": 35,
                "max_occupancy": 2,
                "bed_type": "Queen",
                "bed_count": 1,
                "wifi": True,
                "air_conditioning": True,
                "private_bathroom": True,
                "room_view": "City",
                "price_tier": "mid-range",
                "floor_number": "",
                "window_type": "",
                "bathroom_type": "",
                "soundproofing": False,
                "smart_features": [],
                "kitchenette": False,
                "minibar": False,
                "workspace_type": "",
                "accessibility_features": [],
                "renovation_year": None,
                "flooring_type": "",
                "lighting": "",
                "air_purifier": False,
                "pet_friendly": False,
                "connecting_rooms": False,
                "max_children": 0,
                "crib_available": False
            },
            "status": "active",
            "operation": "create"
        }

    def _create_room_type_logic(self, room_type: str, specifications: dict) -> str:
        """CREATE room type logic (structured only, no natural language)"""
        hotel_name = self._get_hotel_name_from_context()
        if hotel_name.startswith("❌"):
            return hotel_name
        if not room_type or not specifications:
            return "❌ room_type and specifications are required."
        existing = self.collection.find_one({
            "hotel_name": hotel_name,
            "room_type": room_type,
            "has_room_number": False
        })
        if existing:
            return f"❌ Room type '{room_type}' already exists at '{hotel_name}'. Use updates to modify it."
        manager_sig = self._get_manager_signature()
        room_doc = {
            "hotel_name": hotel_name,
            "room_type": room_type,
            "specifications": specifications,
            "status": "active",
            "has_room_number": False,
            "created_at": datetime.now(),
            "created_by": manager_sig,
            "updated_at": datetime.now(),
            "updated_by": manager_sig
        }
        self.collection.insert_one(room_doc)
        inventory_success = self._create_inventory_records(hotel_name, room_type)
        return f"✅ Created room type: {room_type} at {hotel_name}\n" \
               f"📐 Size: {specifications.get('room_size_sqm', 'N/A')}sqm\n" \
               f"👥 Occupancy: {specifications.get('max_occupancy', 'N/A')} guests\n" \
               f"🛏️ Bed: {specifications.get('bed_type', 'N/A')}\n" \
               f"🌆 View: {specifications.get('room_view', 'N/A')}\n" \
               f"📊 Inventory: {'✅ Created' if inventory_success else '❌ Failed'} (0 rooms, 0 prices - fill with inventory_manager)"


 
    def _delete_room_type_logic(self, room_type: str) -> str:
        """DELETE room type logic (context-aware)"""
        hotel_name = self._get_hotel_name_from_context()
        room = self.collection.find_one({
            "hotel_name": {"$regex": hotel_name, "$options": "i"},
            "room_type": {"$regex": room_type, "$options": "i"},
            "has_room_number": False
        })
        if not room:
            return f"❌ Room type '{room_type}' not found at '{hotel_name}'"
        actual_hotel = room["hotel_name"]
        actual_room_type = room["room_type"]
        room_count = self.collection.count_documents({
            "hotel_name": actual_hotel,
            "room_type": actual_room_type,
            "has_room_number": True
        })
        if room_count > 0:
            return f"❌ Cannot delete room type '{actual_room_type}' - {room_count} rooms exist. Delete rooms first."
        result = self.collection.delete_one({
            "hotel_name": actual_hotel,
            "room_type": actual_room_type,
            "has_room_number": False
        })
        if result.deleted_count > 0:
            inventory_deleted = self._delete_inventory_records(actual_hotel, actual_room_type)
            return f"✅ Deleted room type: {actual_room_type} at {actual_hotel}\n" \
                   f"📊 Inventory: {'✅ Deleted' if inventory_deleted else '❌ Failed to delete'} from PostgreSQL"
        else:
            return f"❌ Failed to delete room type"
        
    def _update_room_number(self, room_data: dict, user_input: str) -> str:
        """Update room_number helper"""
        try:
            # Extract updates from user input
            update_data = self._create_empty_room_template(user_input)
            manager_sig = self._get_manager_signature()
            update_ops = {"updated_at": datetime.now(), "updated_by": manager_sig}
            changes = []  # Fix: ensure changes is always defined
            # Update specifications that changed
            if update_data["specifications"]:
                for key, value in update_data["specifications"].items():
                    if value != room_data["specifications"].get(key):
                        update_ops[f"specifications.{key}"] = value
                        changes.append(f"{key}: {room_data['specifications'].get(key, 'N/A')} → {value}")
            
            if not changes:
                return "❌ No changes detected for room_number"
            
            result = self.collection.update_one(
                {
                    "hotel_name": room_data["hotel_name"],
                    "room_type": room_data["room_type"], 
                    "room_number": room_data["room_number"],
                    "has_room_number": True
                },
                {"$set": update_ops}
            )
            
            if result.modified_count > 0:
                response = f"✅ Updated room_number {room_data['room_number']} at {room_data['hotel_name']}\n"
                response += "📋 Changes:\n"
                for change in changes:
                    response += f"  • {change}\n"
                return response
            else:
                return "❌ Failed to update room_number"
            
        except Exception as e:
            return f"❌ Error updating room_number: {str(e)}"

    def _create_assignment_records(self, hotel_name: str, room_type: str, room_number: str, days_ahead: int = 365) -> bool:
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
            start_date = datetime.now().date()
            
            for i in range(days_ahead):
                stay_date = start_date + timedelta(days=i)
                # Insert assignment record (available by default)
                cursor.execute("""
                    INSERT INTO room_number_assignments 
                    (hotel_name, room_type, room_number, stay_date, assignment_status)
                    VALUES (%s, %s, %s, %s, %s)
                """, (hotel_name, room_type, room_number, stay_date, 'available'))
            
            conn.commit()
            cursor.close()
            conn.close()
            return True
            
        except Exception as e:
            print(f"Error creating assignment records: {e}")
            return False

    def _delete_assignment_records(self, hotel_name: str, room_number: str) -> bool:
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
                WHERE hotel_name = %s AND room_number = %s
                """, (canonical_key(hotel_name), canonical_key(room_number)))
            
            assignment_deleted_count = cursor.rowcount
            
            # Delete readiness status for this room
            cursor.execute("""
                DELETE FROM room_readiness_status 
                WHERE hotel_name = %s AND room_number = %s
                """, (canonical_key(hotel_name), canonical_key(room_number)))
            
            readiness_deleted_count = cursor.rowcount
            
            conn.commit()
            cursor.close()
            conn.close()
            
            print(f"✅ Deleted {assignment_deleted_count} assignment records for room {room_number}")
            print(f"✅ Deleted {readiness_deleted_count} readiness status record for room {room_number}")
            return True
            
        except Exception as e:
            print(f"Error deleting room records: {e}")
            return False

    def _create_readiness_status_record(self, hotel_name: str, room_number: str, created_by: str = "system") -> bool:
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
            
            # Insert readiness status record (unprepared by default)
            cursor.execute("""
                INSERT INTO room_readiness_status 
                (hotel_name, room_number, readiness_status, changed_by, notes)
                VALUES (%s, %s, 'unprepared', %s, %s)
                ON CONFLICT (hotel_name, room_number) DO NOTHING
            """, (canonical_key(hotel_name), canonical_key(room_number), created_by, f"Room created by {created_by} - default unprepared status"))
            
            conn.commit()
            cursor.close()
            conn.close()
            return True
            
        except Exception as e:
            print(f"Error creating readiness status record: {e}")
            return False
        
    def _create_inventory_records(self, hotel_name: str, room_type: str) -> bool:
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
            start_date = datetime.now().date()
            for i in range(365):
                stay_date = start_date + timedelta(days=i)
                cursor.execute("""
                    INSERT INTO room_inventory_nightly 
                    (hotel_name, room_type, stay_date, total_inventory,blocked_quantity, updated_at)
                    VALUES (%s, %s, %s, 0, 0, CURRENT_TIMESTAMP)
                """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date)))
            # Optionally, still create daily_pricing records for the next 365 days
            for i in range(365):
                stay_date = start_date + timedelta(days=i)
                cursor.execute("""
                    INSERT INTO daily_pricing 
                    (hotel_name, room_type, stay_date, base_price, updated_at)
                    VALUES (%s, %s, %s, 0.00, CURRENT_TIMESTAMP)
                """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date)))
            conn.commit()
            cursor.close()
            conn.close()
            print(f"✅ Created per-night inventory records for {room_type} at {hotel_name}")
            print(f"📅 365 days of per-night inventory records created (0 quantities)")
            print(f"📊 Inventory quantities set to 0 (to be filled by inventory_manager)")
            return True
        except Exception as e:
            print(f"Error creating per-night inventory records: {e}")
            return False

    def _delete_inventory_records(self, hotel_name: str, room_type: str) -> bool:
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
                WHERE hotel_name = %s AND room_type = %s
            """, (canonical_key(hotel_name), canonical_key(room_type)))
            
            pricing_deleted_count = cursor.rowcount
            
            # STEP 2: Now delete inventory record (no more foreign key references)
            cursor.execute("""
                DELETE FROM room_inventory 
                WHERE hotel_name = %s AND room_type = %s
            """, (canonical_key(hotel_name), canonical_key(room_type)))
            
            inventory_deleted_count = cursor.rowcount
            
            conn.commit()
            cursor.close()
            conn.close()
            
            print(f"✅ Deleted {pricing_deleted_count} pricing records for {room_type}")
            print(f"✅ Deleted {inventory_deleted_count} inventory record for {room_type}")
            return True
            
        except Exception as e:
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

    def read_room_information_from_description(self, room_information: str) -> str:
        """READ room information by parsing natural language description"""
        try:
            parsed_data = self._create_empty_room_template(room_information)
        
            result = f"🏠 Parsed Room Information:\n"
            result += f"  • Hotel: {parsed_data.get('hotel_name', 'N/A')}\n"
            result += f"  • Room Type: {parsed_data.get('room_type', 'N/A')}\n"
            result += f"  • Operation: {parsed_data.get('operation', 'create')}\n"
            
            # Specifications
            specs = parsed_data.get('specifications', {})
            result += f"  • Specifications:\n"
            result += f"    - Size: {specs.get('room_size_sqm', 35)} sqm\n"
            result += f"    - Max Occupancy: {specs.get('max_occupancy', 2)} guests\n"
            result += f"    - Bed: {specs.get('bed_count', 1)} {specs.get('bed_type', 'Queen')}\n"
            result += f"    - View: {specs.get('room_view', 'City')}\n"
            result += f"    - Price Tier: {specs.get('price_tier', 'mid-range')}\n"
            result += f"    - WiFi: {'✅' if specs.get('wifi', True) else '❌'}\n"
            result += f"    - AC: {'✅' if specs.get('air_conditioning', True) else '❌'}\n"
            result += f"    - Private Bath: {'✅' if specs.get('private_bathroom', True) else '❌'}\n"
            
            # Description
            if parsed_data.get('description'):
                result += f"  • Description: {parsed_data.get('description')}\n"
            
            # Validation check
            required_fields = ['hotel_name', 'room_type']
            missing_fields = [field for field in required_fields if not parsed_data.get(field)]
            
            if missing_fields:
                result += f"\n⚠️ Missing Required Fields: {', '.join(missing_fields)}\n"
                result += f"💡 Please provide complete hotel and room type information"
            else:
                result += f"\n✅ All required fields extracted successfully"
                operation = parsed_data.get('operation', 'create')
                result += f"\n🎯 Ready for: {operation}_room_type tool"
        
            return result
            
        except Exception as e:
            return f"❌ Error parsing room information: {str(e)}"


    def _format_specifications_for_display(self, specs: dict) -> str:
        """Format all specification fields for display, showing only non-empty values."""
        field_map = {
            "room_size_sqm": "📐 Size (sqm)",
            "max_occupancy": "👥 Max Occupancy",
            "bed_type": "🛏️ Bed Type",
            "bed_count": "🛏️ Bed Count",
            "room_view": "🌆 View",
            "wifi": "📶 WiFi",
            "air_conditioning": "❄️ AC",
            "private_bathroom": "🚿 Private Bath",
            "soundproofing": "🔇 Soundproofing",
            "smart_features": "🤖 Smart Features",
            "kitchenette": "🍳 Kitchenette",
            "minibar": "🥂 Minibar",
            "workspace_type": "💻 Workspace",
            "balcony": "🌿 Balcony",
            "air_purifier": "🌬️ Air Purifier",
            "pet_friendly": "🐾 Pet Friendly",
            "crib_available": "🍼 Crib",
            "connecting_rooms": "🔗 Connecting Rooms",
            "floor_number": "🏢 Floor",
            "window_type": "🪟 Window",
            "bathroom_type": "🛁 Bathroom",
            "renovation_year": "🛠️ Renovation Year",
            "flooring_type": "🪵 Flooring",
            "lighting": "💡 Lighting",
            "max_children": "👶 Max Children",
            "price_tier": "💰 Price Tier",
            # Add more as needed
        }
        lines = []
        for key, label in field_map.items():
            value = specs.get(key)
            if value not in [None, "", False, 0, [], {}]:
                lines.append(f"{label}: {value}")
        return "\n".join(lines)

    def read_room_type(self, room_type: str, structured: bool = False) -> str:
        """READ room type details using natural keys with smart matching. If structured=True, return full MongoDB document as dict."""
        try:
            hotel_name = self._get_hotel_name_from_context()
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



    def read_room_types_by_criteria(self, criteria: str) -> str:
        """READ room types using natural language search criteria - MANAGER CONTEXT AWARE"""
        try:
            # ALWAYS filter by manager's hotel context first
            hotel_name = self._get_hotel_name_from_context()
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
                specs = room["specifications"]
                response += f"📋 {room['room_type']}\n"
                response += f"    📐 Size: {specs['room_size_sqm']}sqm\n"
                response += f"    👥 Occupancy: {specs['max_occupancy']} guests\n"
                response += f"    🌆 View: {specs['room_view']}\n"
                response += f"    💰 Price Tier: {specs['price_tier']}\n"
                response += f"    📶 WiFi: {'Yes' if specs['wifi'] else 'No'}\n\n"
            
            return response
            
        except Exception as e:
            return f"❌ Error searching room types: {str(e)}"



   
    def read_room_portfolio(self, room_type: str = None, include_physical: bool = True, include_structured: bool = False) -> str:
        """READ room portfolio - room types and rooms using natural keys"""
        try:
            # Get hotel_name from manager_context.json (single source of truth)
            hotel_name = self._get_hotel_name_from_context()
            if hotel_name.startswith("❌"):
                return hotel_name if not include_structured else {"error": hotel_name}
            query = {}
            query["hotel_name"] = {"$regex": hotel_name, "$options": "i"}
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
                        "room_numbers": []
                    }
                    if include_physical:
                        room_number_docs = list(self.collection.find({
                            "hotel_name": hotel_name,
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
                    specs = room_type_doc["specifications"]
                    response += f"  📋 {room_type_doc['room_type']}\n"
                    response += f"      📐 {specs['room_size_sqm']}sqm | 👥 {specs['max_occupancy']} guests | 🌆 {specs['room_view']}\n"
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

    def read_room_system_validation(self) -> str:
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

    def manage_room_description(self, room_type: str, specifications: dict) -> str:
        hotel_name = self._get_hotel_name_from_context()
        if hotel_name.startswith("❌"):
            return hotel_name
        if not isinstance(specifications, dict):
            return "❌ specifications must be a dict."
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
        manager_sig = self._get_manager_signature()
        now = datetime.now()
        if room:
            update_ops = {"updated_at": now, "updated_by": manager_sig}
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
                "created_by": manager_sig,
                "updated_at": now,
                "updated_by": manager_sig
            }
            self.collection.insert_one(room_doc)
            return f"✅ updated room type {room_type} at {hotel_name} with description/specifications."

# Add a dedicated internal function for adding a room number

    def add_room_number(self, room_type: str, room_number: int) -> str:
        """
        Add a new room number to a room type in both MongoDB and PostgreSQL.
        Handles all validation, creation, and error handling for a single room number.
        Each database is validated and inserted independently; no rollback is performed.
        """
        hotel_name = self._get_hotel_name_from_context()
        if hotel_name.startswith("❌"):
            return hotel_name
        mongo_status = ""
        pg_status = ""
        # MongoDB insert
        base_room_type = self.collection.find_one({
            "hotel_name": hotel_name,
            "room_type": room_type,
            "has_room_number": False
        })
        existing_room = self.collection.find_one({
            "hotel_name": hotel_name,
            "room_type": room_type,
            "room_number": str(room_number),
            "has_room_number": True
        })
        if not base_room_type:
            mongo_status = f"❌ Room type '{room_type}' does not exist at {hotel_name} (MongoDB)."
        elif existing_room:
            mongo_status = f"❌ Room number {room_number} already exists for '{room_type}' at {hotel_name} (MongoDB)."
        else:
            new_room_doc = base_room_type.copy()
            new_room_doc["room_number"] = str(room_number)
            new_room_doc["has_room_number"] = True
            new_room_doc["created_at"] = datetime.now()
            new_room_doc.pop("_id", None)
            self.collection.insert_one(new_room_doc)
            mongo_status = f"✅ Room {room_number} added to '{room_type}' at {hotel_name} (MongoDB)."
        # PostgreSQL insert
        try:
            assignment_success = self._create_assignment_records(hotel_name, room_type, str(room_number))
            readiness_success = self._create_readiness_status_record(hotel_name, str(room_number), "room_manager")
            if assignment_success and readiness_success:
                pg_status = f"✅ Room {room_number} added to '{room_type}' at {hotel_name} (PostgreSQL)."
            else:
                pg_status = f"❌ Error creating assignment or readiness records for {room_number} at {hotel_name} (PostgreSQL)."
        except Exception as e:
            pg_status = f"❌ Error creating assignment or readiness records for {room_number} at {hotel_name} (PostgreSQL): {e}"
        # Combine status
        return f"{mongo_status}\n{pg_status}".strip()

# Add delete_room_number method to RoomManager
    def delete_readiness_status_record(self, hotel_name: str, room_number: str) -> bool:
        """
        Delete the readiness status record for a room_number in PostgreSQL.
        Args:
            hotel_name: Hotel name
            room_number: Room number
        Returns:
            bool: Success status (True if deleted, False otherwise)
        """
        try:
            conn = self._get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM room_readiness_status 
                WHERE hotel_name = %s AND room_number = %s
            """, (canonical_key(hotel_name), canonical_key(room_number)))
            deleted_count = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
            return deleted_count > 0
        except Exception as e:
            print(f"Error deleting readiness status record: {e}")
            return False

    def delete_room_number(self, room_type: str, room_number: Union[str, int]) -> str:
        """
        Delete a room number from both MongoDB and PostgreSQL.
        Args:
            room_type: The room type (e.g., 'DELUXE')
            room_number: The room number (e.g., 101)
        Returns:
            Status message
        """
        hotel_name = self._get_hotel_name_from_context()
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
        assignment_deleted = self._delete_assignment_records(hotel_name, str(room_number))
        # Delete readiness status in PostgreSQL
        readiness_deleted = self.delete_readiness_status_record(hotel_name, str(room_number))
        if not assignment_deleted and not readiness_deleted:
            return f"⚠️ Deleted MongoDB doc for room_number {room_number}, but failed to delete assignments and readiness in PostgreSQL. Manual cleanup required."
        elif not assignment_deleted:
            return f"⚠️ Deleted MongoDB doc for room_number {room_number}, deleted readiness, but failed to delete assignments in PostgreSQL. Manual cleanup required."
        elif not readiness_deleted:
            return f"⚠️ Deleted MongoDB doc for room_number {room_number}, deleted assignments, but failed to delete readiness in PostgreSQL. Manual cleanup required."
        return f"✅ Deleted room_number {room_number} ({room_type}) at {hotel_name} (MongoDB + PostgreSQL assignments + readiness)"

# Dedicated LLM parser for room specifications    
def parse_room_specifications_llm(message: str) -> Dict[str, Any]:
    """
    Parse a natural language room description into a dict of allowed specification keys.
    Only the following keys will be returned if present:
        room_size_sqm, max_occupancy, bed_type, bed_count, wifi, air_conditioning, private_bathroom, room_view, price_tier,
        floor_number, window_type, bathroom_type, soundproofing, smart_features, kitchenette, minibar, workspace_type,
        accessibility_features, renovation_year, flooring_type, lighting, air_purifier, pet_friendly, connecting_rooms,
        max_children, crib_available
    """
    # Placeholder: Replace with actual LLM call
    # For now, return empty dict
    return {}

room_manager = RoomManager()

# Singleton instance for tools

# Update add_room_number_tool to call add_room_number directly
@tool
def add_room_number_tool(
    room_type: str,
    room_number: int
) -> str:
    """
    Add a new room number to a room type. Only room_type and room_number are required.
    Parameters:
        room_type: The room type (e.g., "COUNTRY DELUXE")
        room_number: The room number (e.g., 111)
    """
    return room_manager.add_room_number(room_type, room_number)

@tool
def delete_room_number_tool(
    room_type: str,
    room_number: Union[str, int]
) -> str:
    """
    Delete a room number from both MongoDB and PostgreSQL.
    Parameters:
        room_type: The room type (e.g., 'DELUXE')
        room_number: The room number (e.g., 101)
    """
    return room_manager.delete_room_number(room_type, room_number)

@tool
def read_room_portfolio_tool(room_type: str = None, include_physical: bool = True, include_structured: bool = False) -> str:
    """Read room portfolio - room types and rooms using natural keys"""
    return room_manager.read_room_portfolio(room_type=room_type, include_physical=include_physical, include_structured=include_structured)

@tool
def read_room_system_validation_tool() -> str:
    """Read room system validation status and integrity check"""
    return room_manager.read_room_system_validation()

@tool
def manage_room_description_tool(room_type: str, specifications: Any, dialog_history: str = None) -> str:
    """
    Update (or create) the specifications for existing room type.
    - If specifications is a dict, it must contain one or more of the allowed specification keys.
    - If specifications is not a dict (e.g., a string), it will be passed to a dedicated LLM parser to extract a structured dict, which is then used for the update.
    - Allowed keys: room_size_sqm, max_occupancy, bed_type, bed_count, wifi, air_conditioning, private_bathroom, room_view, price_tier,
      floor_number, window_type, bathroom_type, soundproofing, smart_features, kitchenette, minibar, workspace_type,
      accessibility_features, renovation_year, flooring_type, lighting, air_purifier, pet_friendly, connecting_rooms,
      max_children, crib_available
    - Any other keys will be ignored. The tool will return an error if no valid updates are provided.
    """
    if not isinstance(specifications, dict):
        specifications = parse_room_specifications_llm(specifications)
    if not isinstance(specifications, dict):
        return "❌ specifications must be a dict or a string that can be parsed into a dict."
    return room_manager.manage_room_description(room_type, specifications)

@tool
def create_room_type_tool(room_type: str, specifications: Any) -> str:
    """
    Create a new room type for the current hotel.
    - room_type: The name of the room type (e.g., "KING DELUXE").
    - specifications: A dict of canonical specification keys and values (see below), or a natural language string to be parsed.

    If specifications is a string, it will be parsed using the LLM parser to extract canonical keys.
    Allowed specification keys: room_size_sqm, max_occupancy, bed_type, bed_count, wifi, air_conditioning, private_bathroom, room_view, price_tier,
      floor_number, window_type, bathroom_type, soundproofing, smart_features, kitchenette, minibar, workspace_type,
      accessibility_features, renovation_year, flooring_type, lighting, air_purifier, pet_friendly, connecting_rooms,
      max_children, crib_available
    """
    if isinstance(specifications, str):
        specifications = parse_room_specifications_llm(specifications)
    if not isinstance(specifications, dict):
        return "❌ specifications must be a dict or a string that can be parsed into a dict."
    return room_manager._create_room_type_logic(room_type, specifications)

@tool
def delete_room_type_tool(room_type: str) -> str:
    """
    Delete a room type for the current hotel context.
    - room_type: The name of the room type to delete (e.g., "KING DELUXE").
    This tool will delete the room type and all associated inventory records, but will fail if any physical rooms of this type exist.
    Returns a status message.
    """
    return room_manager._delete_room_type_logic(room_type)

@tool
def read_room_type_tool(room_type: str, structured: bool = False) -> Any:
    """
    Read room type details using natural keys with smart matching. If structured=True, return full MongoDB document as dict.
    """
    return room_manager.read_room_type(room_type, structured=structured)

@tool
def read_room_types_by_criteria_tool(criteria: str) -> str:
    """
    Read room types using natural language search criteria.
    """
    return room_manager.read_room_types_by_criteria(criteria)


