#!/usr/bin/env python3
"""
INVENTORY MANAGER - Agent-Rule Compliant
ONLY manages hotel room inventory and pricing using natural keys

AGENT-RULE COMPLIANCE:
- Natural Keys: hotel_name from manager_context.json + room_type (no artificial IDs)
- PostgreSQL Only: Computable data (numbers, dates) for inventory/pricing
- CRUD Operations: All operations validate with READ first
- Parameter Validation: All user input validated against database
- Single source of truth: manager_context.json for hotel_name
"""

import psycopg2
import pymongo
import os
import json
from datetime import datetime, timedelta
from typing import Dict, Any
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from .date_utils import normalize_date
import difflib
from .llm_utils import canonical_key

# Load environment variables
load_dotenv()

class InventoryManager:
    def __init__(self):
        """Initialize Inventory Manager with PostgreSQL and MongoDB connections"""
        self.postgres_conn = None
        self.mongo_client = None
        self.mongo_db = None
        self.current_date = datetime.now().strftime('%Y-%m-%d')
        
        # Initialize LLM for parsing unstructured inventory information
        self.llm = ChatOpenAI(
            model="gpt-4.1-nano",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
        
        # Date context for AI agent
        today = datetime.now()
        self.DATE_CONTEXT = {
            'today': today.strftime('%Y-%m-%d'),
            'tomorrow': (today + timedelta(days=1)).strftime('%Y-%m-%d'),
            'next_week': (today + timedelta(days=7)).strftime('%Y-%m-%d'),
            'next_month': (today + timedelta(days=30)).strftime('%Y-%m-%d')
        }
        
        self._connect_databases()
        # self._ensure_tables()  # Removed: schema setup now handled by setup_schema.py
    
    def _connect_databases(self):
        """Connect to PostgreSQL and MongoDB (agent-rule: read room types from MongoDB)"""
        try:
            # PostgreSQL connection for computable data
            self.postgres_conn = psycopg2.connect(os.getenv('DATABASE_URL'))
            # Database connections established silently
            
            # MongoDB connection to read available room types
            self.mongo_client = pymongo.MongoClient(os.getenv('MONGO_URL'))
            self.mongo_db = self.mongo_client['leon_hotel_memory']
            # Database connections established silently
        except Exception as e:
            print(f"❌ Database connection error: {e}")
    
    def _get_hotel_context(self) -> Dict[str, str]:
        """Get hotel context from manager_context.json"""
        try:
            context_path = os.path.join(os.path.dirname(__file__), 'manager_context.json')
            if os.path.exists(context_path):
                with open(context_path, 'r') as f:
                    context = json.load(f)
                    # Read from active_manager object
                    active_manager = context.get("active_manager", {})
                    return {
                        "hotel_name": active_manager.get("hotel_name", ""),
                        "manager_name": active_manager.get("manager_name", ""),
                        "country": active_manager.get("country", ""),
                        "city": active_manager.get("city", "")
                    }
            return {"hotel_name": "", "manager_name": "", "country": "", "city": ""}
        except Exception as e:
            print(f"⚠️ Error reading manager context: {e}")
            return {"hotel_name": "", "manager_name": "", "country": "", "city": ""}
    
    def _validate_and_read_nightly_inventory(self, hotel_name: str, room_type: str, stay_date: str) -> Dict[str, Any]:
        """CRUD VALIDATION: READ nightly inventory to validate parameters (intelligent similarity-based room_type suggestion)"""
        if not self.postgres_conn:
            return {'exists': False, 'error': 'Database connection not available'}
        try:
            cur = self.postgres_conn.cursor()
            # Try exact match first
            cur.execute("""
                SELECT hotel_name, room_type, stay_date, total_inventory, blocked_quantity, updated_at
                FROM room_inventory_nightly
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s;
            """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date)))
            exact_match = cur.fetchone()
            if exact_match:
                return {
                    'exists': True,
                    'exact_match': True,
                    'matched_room_type': exact_match[1],
                    'data': {
                        'hotel_name': exact_match[0],
                        'room_type': exact_match[1],
                        'stay_date': exact_match[2],
                        'total_inventory': exact_match[3],
                        'blocked_quantity': exact_match[4],
                        'updated_at': exact_match[5]
                    }
                }
            # If not found, try case-insensitive match
            cur.execute("""
                SELECT DISTINCT room_type FROM room_inventory_nightly
                WHERE hotel_name = %s AND room_type = %s
                LIMIT 1;
            """, (canonical_key(hotel_name), canonical_key(room_type)))
            ci_match = cur.fetchone()
            if ci_match:
                matched_room_type = ci_match[0]
                return {
                    'exists': False,
                    'exact_match': False,
                    'matched_room_type': matched_room_type,
                    'suggestion': f"Did you mean room type '{matched_room_type}'?",
                }
            # If still not found, use similarity matching
            cur.execute("""
                SELECT DISTINCT room_type FROM room_inventory_nightly
                WHERE hotel_name = %s;
            """, (canonical_key(hotel_name),))
            all_room_types = [row[0] for row in cur.fetchall()]
            close_matches = difflib.get_close_matches(room_type, all_room_types, n=1, cutoff=0.6)
            if close_matches:
                suggestion = close_matches[0]
                return {
                    'exists': False,
                    'exact_match': False,
                    'suggestion': f"Did you mean room type '{suggestion}'?",
                    'matched_room_type': suggestion
                }
            return {'exists': False, 'exact_match': False}
        except Exception as e:
            return {'exists': False, 'error': str(e)}

    def update_nightly_inventory(self, room_type: str, stay_date: str, total_inventory: int = None, blocked_quantity: int = 0) -> str:
        """UPDATE nightly inventory record for a specific date (only total_inventory and blocked_quantity are stored; active_inventory is computed)"""
        if not self.postgres_conn:
            return "❌ Database connection not available"
        context = self._get_hotel_context()
        hotel_name = canonical_key(context.get("hotel_name", ""))
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
        validation = self._validate_and_read_nightly_inventory(hotel_name, room_type, stay_date)
        if validation.get('error'):
            return f"❌ Validation error: {validation['error']}"
        if not validation.get('exists'):
            return f"❌ Cannot UPDATE - no nightly inventory found for '{room_type}' at '{hotel_name}' on {stay_date}. Use CREATE first."
        current_data = validation['data']
        if total_inventory is None:
            total_inventory = current_data['total_inventory']
        if blocked_quantity is None:
            blocked_quantity = current_data.get('blocked_quantity', 0)
        try:
            cur = self.postgres_conn.cursor()
            cur.execute("""
                UPDATE room_inventory_nightly
                SET total_inventory = %s, blocked_quantity = %s, updated_at = CURRENT_TIMESTAMP
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s;
            """, (total_inventory, blocked_quantity, canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date)))
            self.postgres_conn.commit()
            return f"✅ Updated room quantity for {room_type} at {hotel_name} on {stay_date}: Total {total_inventory}, Blocked {blocked_quantity}, Active {total_inventory - blocked_quantity}"
        except Exception as e:
            self.postgres_conn.rollback()
            return f"❌ Error updating nightly inventory: {e}"

    def read_nightly_inventory(self, room_type: str, stay_date: str) -> str:
        """READ nightly inventory record for a specific date (active_inventory is computed as total_inventory - blocked_quantity)"""
        if not self.postgres_conn:
            return "❌ Database connection not available"
        context = self._get_hotel_context()
        hotel_name = canonical_key(context.get("hotel_name", ""))
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
        try:
            cur = self.postgres_conn.cursor()
            cur.execute("""
                SELECT total_inventory, blocked_quantity, updated_at
                FROM room_inventory_nightly
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s;
            """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date)))
            row = cur.fetchone()
            if not row:
                return f"❌ No nightly inventory found for '{room_type}' at '{hotel_name}' on {stay_date}"
            total_inventory, blocked_quantity, updated_at = row
            active_inventory = total_inventory - blocked_quantity
            return f"📊 Nightly Inventory for {room_type} at {hotel_name} on {stay_date}:\n  • Total: {total_inventory}\n  • Blocked: {blocked_quantity}\n  • Active: {active_inventory}\n  • Last Updated: {updated_at}"
        except Exception as e:
            return f"❌ Error reading nightly inventory: {e}"

    def delete_nightly_inventory(self, room_type: str, stay_date: str) -> str:
        """DELETE nightly inventory record for a specific date (canonical for all per-night operations)"""
        if not self.postgres_conn:
            return "❌ Database connection not available"
        context = self._get_hotel_context()
        hotel_name = canonical_key(context.get("hotel_name", ""))
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
        try:
            cur = self.postgres_conn.cursor()
            cur.execute("""
                DELETE FROM room_inventory_nightly
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s;
            """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date)))
            self.postgres_conn.commit()
            return f"✅ Deleted nightly inventory for {room_type} at {hotel_name} on {stay_date}"
        except Exception as e:
            self.postgres_conn.rollback()
            return f"❌ Error deleting nightly inventory: {e}"

    def create_room_price(self, room_type: str, start_date: str, end_date: str, price: float) -> str:
        """CREATE room price data using natural keys with CRUD validation using manager_context.json
        Now independent of inventory: only checks that room_type exists for the hotel (any date).
        Assumes start_date and end_date are valid ISO date strings (YYYY-MM-DD).
        """
        if not self.postgres_conn:
            return "❌ Database connection not available"
        
        # Get hotel context from manager_context.json
        context = self._get_hotel_context()
        hotel_name = canonical_key(context.get("hotel_name", ""))
        
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
        
        # Check that room_type exists for the hotel (any date)
        cur = self.postgres_conn.cursor()
        cur.execute("""
            SELECT 1 FROM room_inventory_nightly
            WHERE hotel_name = %s AND room_type = %s
            LIMIT 1;
        """, (canonical_key(hotel_name), canonical_key(room_type)))
        if not cur.fetchone():
            return f"❌ Cannot CREATE pricing - room type '{room_type}' does not exist for '{hotel_name}'. Create the room type first."
        
        try:
            # Use start_date and end_date as provided (assume valid ISO format)
            start = datetime.strptime(start_date, '%Y-%m-%d')
            end = datetime.strptime(end_date, '%Y-%m-%d')
            
            dates_created = 0
            current = start
            
            while current <= end:
                cur.execute("""
                    INSERT INTO daily_pricing (hotel_name, room_type, stay_date, base_price, updated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                """, (canonical_key(hotel_name), canonical_key(room_type), current.strftime('%Y-%m-%d'), price))
                
                dates_created += 1
                current += timedelta(days=1)
            
            self.postgres_conn.commit()
            
            return f"✅ Created pricing for {room_type} at {hotel_name}:\n" \
                   f"  • Period: {start_date} to {end_date}\n" \
                   f"  • Days updated: {dates_created}\n" \
                   f"  • Price: ${price}/night"
            
        except Exception as e:
            self.postgres_conn.rollback()
            return f"❌ Error creating pricing: {e}"

    def read_room_price(self, room_type: str, start_date: str = None, end_date: str = None) -> str:
        """READ room price data using natural keys with validation using manager_context.json"""
        if not self.postgres_conn:
            return "❌ Database connection not available"
        
        # Get hotel context from manager_context.json
        context = self._get_hotel_context()
        hotel_name = canonical_key(context.get("hotel_name", ""))
        
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
        
        # CRUD VALIDATION: READ inventory first
        validation = self._validate_and_read_nightly_inventory(hotel_name, room_type, start_date) # Assuming start_date is the stay_date for nightly inventory
        
        if validation.get('error'):
            return f"❌ Validation error: {validation['error']}"
        
        if not validation.get('exists'):
            return f"❌ No inventory found for '{room_type}' at '{hotel_name}' on {start_date}"
        
        try:
            # Default to next 7 days if no dates provided
            if not start_date:
                start_date = self.DATE_CONTEXT['today']
            if not end_date:
                end_date = (datetime.strptime(start_date, '%Y-%m-%d') + timedelta(days=6)).strftime('%Y-%m-%d')
            
            cur = self.postgres_conn.cursor()
            
            cur.execute("""
                SELECT stay_date, base_price, updated_at
                FROM daily_pricing 
                WHERE hotel_name = %s AND room_type = %s AND stay_date BETWEEN %s AND %s
                ORDER BY stay_date;
            """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(start_date), canonical_key(end_date)))
            
            results = cur.fetchall()
            
            output = f"💰 Pricing for {room_type} at {hotel_name}:\n"
            output += f"  • Period: {start_date} to {end_date}\n\n"
            
            if not results:
                output += "  ❌ No pricing data set for this period"
            else:
                for stay_date, price, updated_at in results:
                    output += f"  • {stay_date}: ${price}/night (updated {updated_at})\n"
            
            return output.strip()
            
        except Exception as e:
            return f"❌ Error reading pricing: {e}"

    def update_room_price(self, room_type: str, stay_date: str, price: float) -> str:
        """UPDATE room price data for specific date using natural keys with CRUD validation using manager_context.json"""
        if not self.postgres_conn:
            return "❌ Database connection not available"
        
        # Get hotel context from manager_context.json
        context = self._get_hotel_context()
        hotel_name = canonical_key(context.get("hotel_name", ""))
        
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
        
        # Parse date
        parsed_date = stay_date # Assuming stay_date is the date to update
        
        # CRUD VALIDATION: READ inventory first
        validation = self._validate_and_read_nightly_inventory(hotel_name, room_type, parsed_date) # Assuming parsed_date is the stay_date for nightly inventory
        
        if validation.get('error'):
            return f"❌ Validation error: {validation['error']}"
        
        if not validation.get('exists'):
            return f"❌ Cannot UPDATE price - no inventory found for '{room_type}' at '{hotel_name}' on {parsed_date}. Create inventory first."
        
        try:
            cur = self.postgres_conn.cursor()
            
            # Check if pricing record exists for this date
            cur.execute("""
                SELECT base_price FROM daily_pricing 
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s;
            """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(parsed_date)))
            
            existing_price = cur.fetchone()
            
            if existing_price:
                # UPDATE existing price
                cur.execute("""
                    UPDATE daily_pricing 
                    SET base_price = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE hotel_name = %s AND room_type = %s AND stay_date = %s;
                """, (price, canonical_key(hotel_name), canonical_key(room_type), canonical_key(parsed_date)))
                
                action = "Updated"
                old_price = existing_price[0]
            else:
                # CREATE new price record
                cur.execute("""
                    INSERT INTO daily_pricing (hotel_name, room_type, stay_date, base_price, updated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP);
                """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(parsed_date), price))
                
                action = "Created"
                old_price = 0.0
            
            self.postgres_conn.commit()
            
            return f"✅ {action} price for {room_type} at {hotel_name}:\n" \
                   f"  • Date: {parsed_date}\n" \
                   f"  • Price: ${old_price:.2f} → ${price:.2f}\n" \
                   f"  • Action: {action} price record"
            
        except Exception as e:
            self.postgres_conn.rollback()
            return f"❌ Error updating price: {e}"

    def read_available_room_types(self) -> str:
        """READ available hotel_name and room_type combinations from PostgreSQL using manager_context.json
        
        This shows what hotel_name + room_type combinations exist and can have inventory updated
        """
        if not self.postgres_conn:
            return "❌ PostgreSQL connection not available"
        
        # Get hotel context from manager_context.json
        context = self._get_hotel_context()
        hotel_name = canonical_key(context.get("hotel_name", ""))
        
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
        
        try:
            cur = self.postgres_conn.cursor()
            
            # Get room types for this hotel from PostgreSQL (automatically created by room_manager)
            cur.execute("""
                SELECT room_type, total_inventory, blocked_quantity, updated_date
                FROM room_inventory_nightly 
                WHERE hotel_name = %s
                ORDER BY room_type
            """, (canonical_key(hotel_name),))
            
            room_data = cur.fetchall()
            
            if not room_data:
                return f"❌ No room types found for {hotel_name} in PostgreSQL. Create room types first using room_manager tools."
            
            result = f"📋 Available Room Types for Inventory Management at {hotel_name} ({len(room_data)} types):\n"
            result += "💡 These room types have inventory records (quantities and prices can be updated):\n\n"
            
            for room_type, total_inventory, blocked_quantity, updated_date in room_data:
                active_inventory = total_inventory - blocked_quantity
                status = "✅ Ready" if total_inventory > 0 else "⏳ Empty"
                result += f"  • {room_type} ({status})\n"
                result += f"    - Total: {total_inventory} | Blocked: {blocked_quantity} | Active: {active_inventory}\n"
                result += f"    - Updated: {updated_date}\n"
            
            if room_data:
                result += f"\n💡 Example: update_inventory('{room_data[0][0]}', 10, 8)"
            
            return result.strip()
            
        except Exception as e:
            return f"❌ Error reading available room types: {e}"

    def _parse_inventory_information(self, inventory_information: str, dialog_history: str = None) -> Dict[str, Any]:
        """Parse unstructured inventory information using internal LLM with dialog history context"""
        
        # Get hotel context from manager_context.json
        context = self._get_hotel_context()
        hotel_name = canonical_key(context.get("hotel_name", ""))
        
        # Add dialog history context if available
        context_section = ""
        if dialog_history:
            context_section = f"""
RECENT CONVERSATION CONTEXT:
{dialog_history}

Use this context to resolve pronouns and references (e.g., "it", "its", "that room", "those prices").
"""
        
        extraction_prompt = f"""Parse this inventory/pricing information into structured JSON format:

HOTEL CONTEXT: {hotel_name}
{context_section}
CURRENT INPUT: "{inventory_information}"

Extract and return ONLY valid JSON with this exact structure:
{{
    "hotel_name": "{hotel_name}",
    "room_type": "extracted room type (e.g., Superior Room, Deluxe Suite)",
    "inventory_data": {{
        "total_inventory": extracted_total_rooms_as_integer,
        "blocked_quantity": extracted_blocked_rooms_as_integer,
        "operation": "create/update/delete - what action is requested"
    }},
    "pricing_data": {{
        "base_price": extracted_price_as_float,
        "start_date": "extracted start date in YYYY-MM-DD format",
        "end_date": "extracted end date in YYYY-MM-DD format",
        "currency": "extracted currency (MYR, SGD, USD, etc.)",
        "operation": "create/update/delete - what pricing action is requested"
    }},
    "notes": "any additional notes or special information"
}}

RULES:
- Return ONLY the JSON, no other text
- Use empty strings "" for missing text fields
- Use 0 for missing numeric fields
- Operations: create, update, delete
- If only inventory mentioned, set pricing_data operation to ""
- If only pricing mentioned, set inventory_data operation to ""
- Today's date for reference: {self.DATE_CONTEXT['today']}
- Common date formats: "tomorrow", "next week", "Dec 15-20", etc."""

        try:
            response = self.llm.invoke([{"role": "user", "content": extraction_prompt}])
            response_content = response.content.strip()
            
            # Clean JSON response
            if response_content.startswith('```json'):
                response_content = response_content.replace('```json', '').replace('```', '').strip()
            elif response_content.startswith('```'):
                response_content = response_content.replace('```', '').strip()
            
            parsed_data = json.loads(response_content)
            return parsed_data
            
        except json.JSONDecodeError as e:
            print(f"LLM JSON parsing error: {e}")
            return self._create_empty_inventory_template(inventory_information)
            
        except Exception as e:
            print(f"LLM parsing error: {e}")
            return self._create_empty_inventory_template(inventory_information)
    
    def _create_empty_inventory_template(self, inventory_information: str) -> Dict[str, Any]:
        """Fallback empty template when LLM parsing fails"""
        # Get hotel context from manager_context.json
        context = self._get_hotel_context()
        hotel_name = canonical_key(context.get("hotel_name", ""))
        
        return {
            "hotel_name": hotel_name,
            "room_type": "",
            "inventory_data": {
                "total_inventory": 0,
                "blocked_quantity": 0,
                "operation": ""
            },
            "pricing_data": {
                "base_price": 0.0,
                "start_date": "",
                "end_date": "",
                "currency": "MYR",
                "operation": ""
            },
            "notes": inventory_information
        }

    def manage_room_quantity(self, room_type: str, start_date: str, end_date: str, total_quantity: int, blocked_quantity: int = 0) -> str:
        context = self._get_hotel_context()
        hotel_name = canonical_key(context.get("hotel_name", ""))
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
        except Exception:
            return "❌ Invalid date format. Use YYYY-MM-DD."
        results = []
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            validation = self._validate_and_read_nightly_inventory(hotel_name, room_type, date_str)
            if not validation.get('exists'):
                results.append(f"{date_str}: ❌ No room quantity found for '{room_type}' at '{hotel_name}'.")
            elif total_quantity is None:
                results.append(f"{date_str}: ❌ total_quantity is required to update room quantity.")
            else:
                result = self.update_nightly_inventory(room_type, date_str, total_quantity, blocked_quantity)
                results.append(f"{date_str}: {result}")
            current += timedelta(days=1)
        return "\n".join(results)

    def manage_room_price(self, room_type: str, start_date: str, end_date: str, price: float) -> str:
        context = self._get_hotel_context()
        hotel_name = canonical_key(context.get("hotel_name", ""))
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
        except Exception:
            return "❌ Invalid date format. Use YYYY-MM-DD."
        results = []
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            cur = self.postgres_conn.cursor()
            cur.execute(
                """SELECT base_price FROM daily_pricing 
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s;""",
                (hotel_name, canonical_key(room_type), canonical_key(date_str))
            )
            existing_price = cur.fetchone()
            if not existing_price:
                results.append(f"{date_str}: ❌ No price record found for '{room_type}' at '{hotel_name}'.")
            else:
                result = self.update_room_price(room_type, date_str, price)
                results.append(f"{date_str}: {result}")
            current += timedelta(days=1)
        return "\n".join(results)

    def read_inventory_information_from_description(self, inventory_information: str, dialog_history: str = None) -> str:
        try:
            parsed_data = self._parse_inventory_information(inventory_information, dialog_history)
            result = f"📊 Parsed Inventory Information:\n"
            result += f"  • Hotel: {parsed_data.get('hotel_name', 'N/A')}\n"
            result += f"  • Room Type: {parsed_data.get('room_type', 'N/A')}\n"
            # Inventory data
            inventory_data = parsed_data.get('inventory_data', {})
            if inventory_data.get('operation'):
                result += f"  • Inventory Operation: {inventory_data.get('operation')}\n"
                result += f"    - Total Rooms: {inventory_data.get('total_inventory', 0)}\n"
                result += f"    - Blocked Rooms: {inventory_data.get('blocked_quantity', 0)}\n"
            # Pricing data
            pricing_data = parsed_data.get('pricing_data', {})
            if pricing_data.get('operation'):
                result += f"  • Pricing Operation: {pricing_data.get('operation')}\n"
                result += f"    - Price: {pricing_data.get('currency', 'MYR')} {pricing_data.get('base_price', 0.0):.2f}\n"
                result += f"    - Period: {pricing_data.get('start_date', 'N/A')} to {pricing_data.get('end_date', 'N/A')}\n"
            # Notes and validation
            if parsed_data.get('notes'):
                result += f"  • Notes: {parsed_data.get('notes')}\n"
            # Validation status
            required_fields = ['hotel_name', 'room_type']
            missing_fields = [field for field in required_fields if not parsed_data.get(field)]
            if missing_fields:
                result += f"\n⚠️ Missing Required Fields: {', '.join(missing_fields)}\n"
                result += f"💡 Please provide complete hotel and room type information"
            else:
                result += f"\n✅ All required fields extracted successfully"
            # Operation recommendations
            inventory_op = inventory_data.get('operation', '')
            pricing_op = pricing_data.get('operation', '')
            if inventory_op:
                result += f"\n🎯 Ready for: {inventory_op}_inventory tool"
            if pricing_op:
                result += f"\n🎯 Ready for: {pricing_op}_pricing tool"
            return result
        except Exception as e:
            return f"❌ Error parsing inventory information: {str(e)}"

    def check_room_pricing(self, room_type: str, stay_date: str = None, end_date: str = None) -> str:
        today = datetime.now().date()
        if stay_date:
            if stay_date.lower() == "today":
                stay_date = today.strftime("%Y-%m-%d")
            elif stay_date.lower() == "tomorrow":
                stay_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        if end_date:
            if end_date.lower() == "today":
                end_date = today.strftime("%Y-%m-%d")
            elif end_date.lower() == "tomorrow":
                end_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        # If only stay_date is provided, query only that date
        if stay_date and not end_date:
            return self.read_room_price(room_type, stay_date, stay_date)
        return self.read_room_price(room_type, stay_date, end_date)

# Create global instance
inventory_manager = InventoryManager()

# CRUD Tool Functions with Agent-Rule Compliance
@tool
def manage_room_quantity(room_type: str, start_date: str, end_date: str, total_quantity: int, blocked_quantity: int = 0) -> str:
    """Update room quantity for a room type over a date range (inclusive). If start_date == end_date, only that date is updated.
    Args:
        room_type: Room type (natural key)
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        total_quantity: Total rooms available for each date
        blocked_quantity: Number of rooms blocked (default 0)
    Returns:
        Summary of updates.
    """
    return inventory_manager.manage_room_quantity(room_type, start_date, end_date, total_quantity, blocked_quantity)

@tool
def manage_room_price(room_type: str, start_date: str, end_date: str, price: float) -> str:
    """Update room price for a room type over a date range (inclusive). If start_date == end_date, only that date is updated.
    Args:
        room_type: Room type (natural key)
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        price: Price per night (required)
    Returns:
        Summary of updates.
    """
    return inventory_manager.manage_room_price(room_type, start_date, end_date, price)
        
@tool
def read_inventory_information_from_description(inventory_information: str, dialog_history: str = None) -> str:
    """READ inventory/pricing information by parsing natural language description
    
    Handles any format: "Set Superior Room inventory to 50 total, 45 active with pricing RM250 for next week"
    
    Args:
        inventory_information: Natural language inventory/pricing information
        dialog_history: Recent conversation context for resolving pronouns (optional)
    
    Returns: Parsed inventory and pricing details with validation
    """
    return inventory_manager.read_inventory_information_from_description(inventory_information, dialog_history)

@tool
def check_room_pricing(room_type: str, stay_date: str = None, end_date: str = None) -> str:
    """Check the price for a room type for a specific date or date range.
    Args:
        room_type: Room type (natural key)
        stay_date: Date to check price for (YYYY-MM-DD or 'today', 'tomorrow'). If not provided, defaults to today.
        end_date: Optional end date for range pricing (YYYY-MM-DD). If not provided, only stay_date is used.
    Returns:
        Price information for the specified room type and date(s).
    """
    return inventory_manager.check_room_pricing(room_type, stay_date, end_date)
