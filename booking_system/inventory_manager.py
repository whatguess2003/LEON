#!/usr/bin/env python3
"""
INVENTORY MANAGER - Agent-Rule Compliant
ONLY manages hotel room inventory and pricing using natural keys

AGENT-RULE COMPLIANCE:
- Natural Keys: explicit hotel_name + room_type (no artificial IDs)
- PostgreSQL Only: Computable data (numbers, dates) for inventory/pricing
- CRUD Operations: All operations validate with READ first
- Parameter Validation: All user input validated against database
"""

import psycopg2
import pymongo
import os
from datetime import timedelta
from datetime import datetime, timezone
from typing import Dict, Any
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI
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
        # Remove naive date context; compute per hotel on demand
        self.current_date = None
        
        # Initialize LLM for parsing unstructured inventory information
        self.llm = ChatOpenAI(
            model="gpt-4.1-nano",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
        
        # No global DATE_CONTEXT; defaults computed with hotel timezone where needed
        self.DATE_CONTEXT = {}

    # Timezone logic centralized in timezone_utils; use hotel_now_local/hotel_local_today
        
        self._connect_databases()
        # self._ensure_tables()  # Removed: schema setup now handled by setup_schema.py
    
    def _safe_rollback(self):
        try:
            if self.postgres_conn:
                self.postgres_conn.rollback()
        except Exception:
            pass

    def _ensure_postgres_connection(self) -> bool:
        """Ensure PostgreSQL connection is alive; reconnect if closed/broken."""
        try:
            if self.postgres_conn is None or getattr(self.postgres_conn, 'closed', True):
                self.postgres_conn = psycopg2.connect(os.getenv('DATABASE_URL'))
                return True
            # Probe connection
            try:
                with self.postgres_conn.cursor() as cur:
                    cur.execute("SELECT 1;")
            except Exception:
                # Reconnect on failure
                self.postgres_conn = psycopg2.connect(os.getenv('DATABASE_URL'))
            return True
        except Exception as e:
            print(f"❌ PostgreSQL reconnect error: {e}")
            return False

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
    
    def _validate_and_read_nightly_inventory(self, hotel_name: str, room_type: str, stay_date: str) -> Dict[str, Any]:
        """CRUD VALIDATION: READ nightly inventory to validate parameters (intelligent similarity-based room_type suggestion)"""
        if not self._ensure_postgres_connection():
            return {'exists': False, 'error': 'Database connection not available'}
        try:
            # Clear any aborted transaction state
            self._safe_rollback()
            cur = self.postgres_conn.cursor()
            # Try exact match first
            cur.execute("""
                SELECT hotel_name, room_type, stay_date, total_inventory, blocked_quantity, updated_at
                FROM room_inventory_nightly
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s;
            """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date)))
            exact_match = cur.fetchone()
            if exact_match:
                rooms = self.count_rooms(hotel_name, room_type, stay_date)
                blocked = min(int(exact_match[4] or 0), rooms)
                return {
                    'exists': True,
                    'exact_match': True,
                    'matched_room_type': exact_match[1],
                    'data': {
                        'hotel_name': exact_match[0],
                        'room_type': exact_match[1],
                        'stay_date': exact_match[2],
                        'total_inventory': rooms,
                        'blocked_quantity': blocked,
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
            self._safe_rollback()
            return {'exists': False, 'error': str(e)}

    def count_rooms(self, hotel_name: str, room_type: str, stay_date: str = None) -> int:
        """Physical rooms of this room type. A stay date counts the rooms that exist that night."""
        if not self._ensure_postgres_connection():
            return 0
        try:
            self._safe_rollback()
            cur = self.postgres_conn.cursor()
            if stay_date:
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT room_number)
                    FROM room_number_assignments
                    WHERE hotel_name = %s AND room_type = %s AND stay_date = %s
                    """,
                    (canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date))
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT room_number)
                    FROM room_number_assignments
                    WHERE hotel_name = %s AND room_type = %s
                    """,
                    (canonical_key(hotel_name), canonical_key(room_type))
                )
            row = cur.fetchone()
            self._safe_rollback()
            return int(row[0] or 0) if row else 0
        except Exception:
            self._safe_rollback()
            return 0

    def room_counts_between(self, hotel_name: str, room_type: str, start_date: str, end_date: str):
        if not self._ensure_postgres_connection():
            return {}
        try:
            self._safe_rollback()
            cur = self.postgres_conn.cursor()
            cur.execute(
                """
                SELECT stay_date, COUNT(DISTINCT room_number)
                FROM room_number_assignments
                WHERE hotel_name = %s AND room_type = %s AND stay_date BETWEEN %s AND %s
                GROUP BY stay_date
                """,
                (canonical_key(hotel_name), canonical_key(room_type), canonical_key(start_date), canonical_key(end_date))
            )
            counts = {str(stay_date): int(rooms or 0) for stay_date, rooms in cur.fetchall()}
            self._safe_rollback()
            return counts
        except Exception:
            self._safe_rollback()
            return {}

    def update_dates(self, hotel_name: str, manager_name: str, room_type: str, dates, price, quantity):
        """Write price and inventory for many stay dates in one database transaction."""
        if not self._ensure_postgres_connection():
            return {"error": "Database connection not available"}
        hotel = canonical_key(hotel_name)
        room = canonical_key(room_type)
        days = []
        seen = set()
        for raw in dates or []:
            text = str(raw or "").strip()[:10]
            if not text or text in seen:
                continue
            try:
                day = datetime.strptime(text, "%Y-%m-%d").date()
            except ValueError:
                return {"error": "Invalid date"}
            seen.add(text)
            days.append(day)
        if not days:
            return {"error": "dates are required"}
        try:
            price_value = float(price)
        except (TypeError, ValueError):
            return {"error": "Price is required"}
        block_all = quantity is not None and int(quantity) == 0
        try:
            from psycopg2.extras import execute_values
            from booking_system.kite_pricing import ensure_schema
            ensure_schema()
            self._safe_rollback()
            cur = self.postgres_conn.cursor()
            cur.execute(
                """
                SELECT stay_date, COUNT(DISTINCT room_number)
                FROM room_number_assignments
                WHERE hotel_name = %s AND room_type = %s AND stay_date = ANY(%s)
                GROUP BY stay_date
                """,
                (hotel, room, days),
            )
            counts = {row[0]: int(row[1] or 0) for row in cur.fetchall()}
            cur.execute(
                """
                SELECT stay_date, blocked_quantity
                FROM room_inventory_nightly
                WHERE hotel_name = %s AND room_type = %s AND stay_date = ANY(%s)
                """,
                (hotel, room, days),
            )
            blocked_now = {row[0]: int(row[1] or 0) for row in cur.fetchall()}
            now = datetime.now(timezone.utc)
            inventory_rows = []
            for day in days:
                rooms = counts.get(day, 0)
                blocked = rooms if block_all else min(blocked_now.get(day, 0), rooms)
                inventory_rows.append((hotel, room, day, rooms, blocked, now, manager_name))
            execute_values(
                cur,
                """
                INSERT INTO room_inventory_nightly
                    (hotel_name, room_type, stay_date, total_inventory, blocked_quantity, updated_at, updated_by)
                VALUES %s
                ON CONFLICT (hotel_name, room_type, stay_date)
                DO UPDATE SET total_inventory = EXCLUDED.total_inventory,
                              blocked_quantity = EXCLUDED.blocked_quantity,
                              updated_at = EXCLUDED.updated_at,
                              updated_by = EXCLUDED.updated_by
                """,
                inventory_rows,
            )
            execute_values(
                cur,
                """
                INSERT INTO daily_pricing
                    (hotel_name, room_type, stay_date, base_price, saved_price, updated_at, created_by, updated_by)
                VALUES %s
                ON CONFLICT (hotel_name, room_type, stay_date)
                DO UPDATE SET base_price = EXCLUDED.base_price,
                              saved_price = EXCLUDED.saved_price,
                              updated_at = EXCLUDED.updated_at,
                              updated_by = EXCLUDED.updated_by
                """,
                [(hotel, room, day, price_value, price_value, now, manager_name, manager_name) for day in days],
            )
            execute_values(
                cur,
                """
                INSERT INTO kite_price_anchor (hotel_name, room_type, stay_date, anchor_at)
                VALUES %s
                ON CONFLICT (hotel_name, room_type, stay_date)
                DO UPDATE SET anchor_at = EXCLUDED.anchor_at
                """,
                [(hotel, room, day, now) for day in days],
            )
            self.postgres_conn.commit()
            cur.close()
            return {"success": True, "dates": len(days)}
        except Exception as exc:
            self._safe_rollback()
            return {"error": str(exc)}

    def apply_room_count(self, hotel_name: str, manager_name: str, room_type: str) -> str:
        """Write each night's total from the rooms of this room type. Blocked nights stay blocked, capped at that count."""
        if not self._ensure_postgres_connection():
            return "❌ Database connection not available"
        try:
            self._safe_rollback()
            cur = self.postgres_conn.cursor()
            cur.execute(
                """
                UPDATE room_inventory_nightly AS nightly
                SET total_inventory = counts.rooms,
                    blocked_quantity = LEAST(nightly.blocked_quantity, counts.rooms),
                    updated_at = %s,
                    updated_by = %s
                FROM (
                    SELECT stay_date, COUNT(DISTINCT room_number) AS rooms
                    FROM room_number_assignments
                    WHERE hotel_name = %s AND room_type = %s
                    GROUP BY stay_date
                ) AS counts
                WHERE nightly.hotel_name = %s AND nightly.room_type = %s AND nightly.stay_date = counts.stay_date
                """,
                (datetime.now(timezone.utc), manager_name, canonical_key(hotel_name), canonical_key(room_type), canonical_key(hotel_name), canonical_key(room_type))
            )
            self.postgres_conn.commit()
            return f"✅ Inventory for {room_type} follows its rooms"
        except Exception as e:
            self._safe_rollback()
            return f"❌ Error applying room count: {e}"

    def update_nightly_inventory(self, hotel_name: str, manager_name: str, room_type: str, stay_date: str, total_inventory: int = None, blocked_quantity: int = 0) -> str:
        """UPDATE nightly inventory record for a specific date (only total_inventory and blocked_quantity are stored; active_inventory is computed)"""
        if not self.postgres_conn:
            return "❌ Database connection not available"
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
                SET total_inventory = %s, blocked_quantity = %s, updated_at = %s, updated_by = %s
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s;
            """, (total_inventory, blocked_quantity, datetime.now(timezone.utc), manager_name, canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date)))
            self.postgres_conn.commit()
            return f"✅ Updated room quantity for {room_type} at {hotel_name} on {stay_date}: Total {total_inventory}, Blocked {blocked_quantity}, Active {total_inventory - blocked_quantity} (by {manager_name})"
        except Exception as e:
            self.postgres_conn.rollback()
            return f"❌ Error updating nightly inventory: {e}"

    def read_nightly_inventory(self, hotel_name: str, room_type: str, stay_date: str) -> str:
        """READ nightly inventory record for a specific date (active_inventory is computed as total_inventory - blocked_quantity)"""
        if not self._ensure_postgres_connection():
            return "❌ Database connection not available"
        try:
            self._safe_rollback()
            cur = self.postgres_conn.cursor()
            cur.execute("""
                SELECT total_inventory, blocked_quantity, updated_at
                FROM room_inventory_nightly
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s;
            """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date)))
            row = cur.fetchone()
            if not row:
                return f"❌ No nightly inventory found for '{room_type}' at '{hotel_name}' on {stay_date}"
            stored_total, blocked_quantity, updated_at = row
            total_inventory = self.count_rooms(hotel_name, room_type, stay_date)
            blocked_quantity = min(int(blocked_quantity or 0), total_inventory)
            active_inventory = total_inventory - blocked_quantity
            return f"📊 Nightly Inventory for {room_type} at {hotel_name} on {stay_date}:\n  • Total: {total_inventory}\n  • Blocked: {blocked_quantity}\n  • Active: {active_inventory}\n  • Last Updated: {updated_at}"
        except Exception as e:
            self._safe_rollback()
            return f"❌ Error reading nightly inventory: {e}"

    def read_calendar(self, hotel_name: str, start_date: str, end_date: str):
        """Prices and nightly quantities for every room type in one connection."""
        if not self._ensure_postgres_connection():
            return {"room_types": [], "error": "Database connection not available"}
        hotel = canonical_key(hotel_name)
        start = canonical_key(start_date)
        end = canonical_key(end_date)
        try:
            self._safe_rollback()
            cur = self.postgres_conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT room_type
                FROM room_inventory_nightly
                WHERE hotel_name = %s
                ORDER BY room_type
                """,
                (hotel,),
            )
            rooms = [row[0] for row in cur.fetchall()]
            cur.execute(
                """
                SELECT room_type, stay_date, base_price, COALESCE(saved_price, base_price), updated_at
                FROM daily_pricing
                WHERE hotel_name = %s AND stay_date BETWEEN %s AND %s
                """,
                (hotel, start, end),
            )
            prices = {}
            for room_type, stay_date, base_price, saved_price, updated_at in cur.fetchall():
                prices.setdefault(room_type, []).append({
                    "date": str(stay_date),
                    "price": float(base_price) if base_price is not None else 0.0,
                    "saved_price": float(saved_price) if saved_price is not None else float(base_price or 0),
                    "updated_at": str(updated_at) if updated_at is not None else "",
                })
            cur.execute(
                """
                SELECT room_type, stay_date, blocked_quantity, updated_at
                FROM room_inventory_nightly
                WHERE hotel_name = %s AND stay_date BETWEEN %s AND %s
                """,
                (hotel, start, end),
            )
            nightly = {}
            for room_type, stay_date, blocked_quantity, updated_at in cur.fetchall():
                nightly.setdefault(room_type, {})[str(stay_date)] = (int(blocked_quantity or 0), updated_at)
            cur.execute(
                """
                SELECT room_type, stay_date, COUNT(DISTINCT room_number)
                FROM room_number_assignments
                WHERE hotel_name = %s AND stay_date BETWEEN %s AND %s
                GROUP BY room_type, stay_date
                """,
                (hotel, start, end),
            )
            counts = {}
            for room_type, stay_date, rooms_n in cur.fetchall():
                counts.setdefault(room_type, {})[str(stay_date)] = int(rooms_n or 0)
            cur.close()
            self._safe_rollback()
            room_types = []
            for room in rooms:
                quantities = []
                for date_text, (blocked_raw, updated_at) in nightly.get(room, {}).items():
                    rooms_n = counts.get(room, {}).get(date_text, 0)
                    blocked = min(blocked_raw, rooms_n)
                    quantities.append({
                        "date": date_text,
                        "total_inventory": rooms_n,
                        "active_inventory": rooms_n - blocked,
                        "updated_at": str(updated_at) if updated_at is not None else "",
                    })
                room_types.append({
                    "room_type": room,
                    "pricing": prices.get(room, []),
                    "quantities": quantities,
                })
            return {"room_types": room_types}
        except Exception as e:
            self._safe_rollback()
            return {"room_types": [], "error": str(e)}

    def read_nightly_inventory_range(self, hotel_name: str, room_type: str, start_date: str, end_date: str):
        """READ nightly inventory for a date range; returns list of dicts per date."""
        if not self._ensure_postgres_connection():
            return {"items": [], "error": "Database connection not available"}
        try:
            self._safe_rollback()
            cur = self.postgres_conn.cursor()
            cur.execute(
                """
                SELECT stay_date, total_inventory, blocked_quantity, updated_at
                FROM room_inventory_nightly
                WHERE hotel_name = %s AND room_type = %s AND stay_date BETWEEN %s AND %s
                ORDER BY stay_date
                """,
                (canonical_key(hotel_name), canonical_key(room_type), canonical_key(start_date), canonical_key(end_date))
            )
            rows = cur.fetchall()
            counts = self.room_counts_between(hotel_name, room_type, start_date, end_date)
            items = []
            for stay_date, total_inventory, blocked_quantity, updated_at in rows:
                rooms = counts.get(str(stay_date), 0)
                blocked = min(int(blocked_quantity or 0), rooms)
                items.append({
                    "date": str(stay_date),
                    "total_inventory": rooms,
                    "active_inventory": rooms - blocked,
                    "updated_at": str(updated_at) if updated_at is not None else ""
                })
            self._safe_rollback()
            return {"items": items}
        except Exception as e:
            self._safe_rollback()
            return {"items": [], "error": str(e)}

    def delete_nightly_inventory(self, hotel_name: str, manager_name: str, room_type: str, stay_date: str) -> str:
        """DELETE nightly inventory record for a specific date (canonical for all per-night operations)"""
        if not self.postgres_conn:
            return "❌ Database connection not available"
        try:
            cur = self.postgres_conn.cursor()
            cur.execute("""
                DELETE FROM room_inventory_nightly
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s;
            """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(stay_date)))
            self.postgres_conn.commit()
            return f"✅ Deleted nightly inventory for {room_type} at {hotel_name} on {stay_date} (by {manager_name})"
        except Exception as e:
            self.postgres_conn.rollback()
            return f"❌ Error deleting nightly inventory: {e}"

    def create_room_price(self, hotel_name: str, manager_name: str, room_type: str, start_date: str, end_date: str, price: float) -> str:
        """CREATE room price data using natural keys with CRUD validation using manager_context.json
        Now independent of inventory: only checks that room_type exists for the hotel (any date).
        Assumes start_date and end_date are valid ISO date strings (YYYY-MM-DD).
        """
        if not self.postgres_conn:
            return "❌ Database connection not available"
        
        cur = self.postgres_conn.cursor()
        cur.execute("""
            SELECT 1 FROM room_inventory_nightly
            WHERE hotel_name = %s AND room_type = %s
            LIMIT 1;
        """, (canonical_key(hotel_name), canonical_key(room_type)))
        if not cur.fetchone():
            return f"❌ Cannot CREATE pricing - room type '{room_type}' does not exist for '{hotel_name}'. Create the room type first."
        
        try:
            from booking_system.kite_pricing import ensure_schema
            ensure_schema()
            # Use start_date and end_date as provided (assume valid ISO format)
            y1, m1, d1 = [int(x) for x in str(start_date).split('-')]
            y2, m2, d2 = [int(x) for x in str(end_date).split('-')]
            base_today = datetime.now(timezone.utc).date()
            start = base_today.replace(year=y1, month=m1, day=d1)
            end = base_today.replace(year=y2, month=m2, day=d2)
            
            dates_created = 0
            current = start
            
            while current <= end:
                cur.execute("""
                    INSERT INTO daily_pricing (hotel_name, room_type, stay_date, base_price, saved_price, updated_at, created_by, updated_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (hotel_name, room_type, stay_date) DO UPDATE SET
                        base_price = EXCLUDED.base_price,
                        saved_price = EXCLUDED.saved_price,
                        updated_at = %s,
                        updated_by = EXCLUDED.updated_by;
                """, (canonical_key(hotel_name), canonical_key(room_type), current.isoformat(), price, price, datetime.now(timezone.utc), manager_name, manager_name, datetime.now(timezone.utc)))
                
                dates_created += 1
                current += timedelta(days=1)
            
            self.postgres_conn.commit()
            from booking_system.kite_pricing import kite_pricing
            day = start
            while day <= end:
                kite_pricing.note_anchor(hotel_name, room_type, day.isoformat())
                day += timedelta(days=1)
            
            return f"✅ Created pricing for {room_type} at {hotel_name} (by {manager_name}):\n" \
                   f"  • Period: {start_date} to {end_date}\n" \
                   f"  • Days updated: {dates_created}\n" \
                   f"  • Price: ${price}/night"
            
        except Exception as e:
            self.postgres_conn.rollback()
            return f"❌ Error creating pricing: {e}"

    def read_room_price(self, hotel_name: str, room_type: str, start_date: str = None, end_date: str = None) -> str:
        """READ room price data using natural keys with validation using manager_context.json"""
        if not self._ensure_postgres_connection():
            return "❌ Database connection not available"
        
        try:
            self._safe_rollback()
            # Default to next 7 days if no dates provided (hotel-local)
            if not start_date:
                try:
                    start_date = datetime.now(timezone.utc).date().isoformat()
                except Exception:
                    return "❌ Missing hotel timezone for pricing window"
            if not end_date:
                y, m, d = [int(x) for x in str(start_date).split('-')]
                base_today = datetime.now(timezone.utc).date()
                start_dt = base_today.replace(year=y, month=m, day=d)
                end_date = (start_dt + timedelta(days=6)).isoformat()
            
            cur = self.postgres_conn.cursor()
            
            cur.execute("""
                SELECT stay_date, base_price, COALESCE(saved_price, base_price), updated_at
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
                for stay_date, price, saved_price, updated_at in results:
                    output += f"  • {stay_date}: ${price}/night saved ${saved_price}/night (updated {updated_at})\n"
            self._safe_rollback()
            return output.strip()
            
        except Exception as e:
            self._safe_rollback()
            return f"❌ Error reading pricing: {e}"

    def update_room_price(self, hotel_name: str, manager_name: str, room_type: str, stay_date: str, price: float) -> str:
        """UPDATE room price data for specific date using natural keys with CRUD validation using manager_context.json"""
        if not self._ensure_postgres_connection():
            return "❌ Database connection not available"
        
        parsed_date = stay_date
        
        validation = self._validate_and_read_nightly_inventory(hotel_name, room_type, parsed_date)
        
        if validation.get('error'):
            return f"❌ Validation error: {validation['error']}"
        
        if not validation.get('exists'):
            return f"❌ Cannot UPDATE price - no inventory found for '{room_type}' at '{hotel_name}' on {parsed_date}. Create inventory first."
        
        try:
            from booking_system.kite_pricing import ensure_schema
            ensure_schema()
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
                    SET base_price = %s, saved_price = %s, updated_at = %s, updated_by = %s
                    WHERE hotel_name = %s AND room_type = %s AND stay_date = %s;
                """, (price, price, datetime.now(timezone.utc), manager_name, canonical_key(hotel_name), canonical_key(room_type), canonical_key(parsed_date)))
                
                action = "Updated"
                old_price = existing_price[0]
            else:
                # CREATE new price record
                cur.execute("""
                    INSERT INTO daily_pricing (hotel_name, room_type, stay_date, base_price, saved_price, updated_at, created_by, updated_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(parsed_date), price, price, datetime.now(timezone.utc), manager_name, manager_name))
                
                action = "Created"
                old_price = 0.0
            
            self.postgres_conn.commit()
            from booking_system.kite_pricing import kite_pricing
            kite_pricing.note_anchor(hotel_name, room_type, parsed_date)
            
            return f"✅ {action} price for {room_type} at {hotel_name} (by {manager_name}):\n" \
                   f"  • Date: {parsed_date}\n" \
                   f"  • Price: ${old_price:.2f} → ${price:.2f}\n" \
                   f"  • Action: {action} price record"
            
        except Exception as e:
            self.postgres_conn.rollback()
            return f"❌ Error updating price: {e}"

    def read_available_room_types(self, hotel_name: str) -> str:
        """READ available hotel_name and room_type combinations from PostgreSQL using manager_context.json
        
        This shows what hotel_name + room_type combinations exist and can have inventory updated
        """
        if not self._ensure_postgres_connection():
            return "❌ PostgreSQL connection not available"
        
        try:
            # Clear any aborted transaction state before running SELECTs
            self._safe_rollback()
            cur = self.postgres_conn.cursor()
            
            # Get latest record per room_type for this hotel
            cur.execute("""
                SELECT DISTINCT ON (room_type)
                    room_type, total_inventory, blocked_quantity, updated_at
                FROM room_inventory_nightly
                WHERE hotel_name = %s
                ORDER BY room_type, updated_at DESC
            """, (canonical_key(hotel_name),))
            
            room_data = cur.fetchall()
            
            if not room_data:
                return f"❌ No room types found for {hotel_name} in PostgreSQL. Create room types first using room_manager tools."
            
            result = f"📋 Available Room Types for Inventory Management at {hotel_name} ({len(room_data)} types):\n"
            result += "💡 These room types have inventory records (quantities and prices can be updated):\n\n"
            
            for room_type, total_inventory, blocked_quantity, updated_at in room_data:
                rooms = self.count_rooms(hotel_name, room_type)
                blocked = min(int(blocked_quantity or 0), rooms)
                active_inventory = rooms - blocked
                status = "✅ Ready" if rooms > 0 else "⏳ Empty"
                result += f"  • {room_type} ({status})\n"
                result += f"    - Total: {rooms} | Blocked: {blocked} | Active: {active_inventory}\n"
                result += f"    - Updated: {updated_at}\n"
            
            if room_data:
                result += f"\n💡 Example: update_inventory('{room_data[0][0]}', 10, 8)"
            
            return result.strip()
            
        except Exception as e:
            self._safe_rollback()
            return f"❌ Error reading available room types: {e}"

    def _parse_inventory_information(self, parsed_data: dict) -> dict:
        """Accepts structured inventory data and returns it unchanged (no LLM, no prompt logic)."""
        return parsed_data

    def _create_empty_inventory_template(self,hotel_name: str, inventory_information: str) -> Dict[str, Any]:
        """Fallback empty template when LLM parsing fails"""
        # Get hotel context from manager_context.json
        # context = self._get_hotel_context() # Removed: manager_context.json is no longer used
        # hotel_name = canonical_key(context.get("hotel_name", "")) # Removed: manager_context.json is no longer used
        
        return {
            "hotel_name": hotel_name, # Removed: manager_context.json is no longer used
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
                "currency": "",
                "operation": ""
            },
            "notes": inventory_information
        }

    def manage_room_quantity(self, hotel_name: str, manager_name: str, room_type: str, start_date: str, end_date: str, total_quantity: int, blocked_quantity: int) -> str:
        if not self._ensure_postgres_connection():
            return "❌ Database connection not available"
        try:
            y1, m1, d1 = [int(x) for x in str(start_date).split('-')]
            y2, m2, d2 = [int(x) for x in str(end_date).split('-')]
            base_today = datetime.now(timezone.utc).date()
            start = base_today.replace(year=y1, month=m1, day=d1)
            end = base_today.replace(year=y2, month=m2, day=d2)
        except Exception:
            return "❌ Invalid date format. Use YYYY-MM-DD."
        results = []
        current = start
        conn = self.postgres_conn
        while current <= end:
            date_str = current.isoformat()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 1 FROM room_inventory_nightly
                        WHERE hotel_name = %s AND room_type = %s AND stay_date = %s
                    """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(date_str)))
                    exists = cur.fetchone() is not None
                    if not exists:
                        # Insert new row
                        cur.execute("""
                            INSERT INTO room_inventory_nightly (hotel_name, room_type, stay_date, total_inventory, blocked_quantity, updated_at, updated_by)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (canonical_key(hotel_name), canonical_key(room_type), canonical_key(date_str), total_quantity, blocked_quantity, datetime.now(timezone.utc), manager_name))
                        conn.commit()
                        results.append(f"{date_str}: ✅ Created new inventory row for '{room_type}' at '{hotel_name}'.")
                    else:
                        # Update existing row
                        cur.execute("""
                            UPDATE room_inventory_nightly
                            SET total_inventory = %s, blocked_quantity = %s, updated_at = %s, updated_by = %s
                            WHERE hotel_name = %s AND room_type = %s AND stay_date = %s
                        """, (total_quantity, blocked_quantity, datetime.now(timezone.utc), manager_name, canonical_key(hotel_name), canonical_key(room_type), canonical_key(date_str)))
                        conn.commit()
                        results.append(f"{date_str}: ✅ Updated inventory for '{room_type}' at '{hotel_name}'.")
            except Exception as e:
                self.postgres_conn.rollback()
                results.append(f"{date_str}: ❌ Error: {str(e)}")
            current += timedelta(days=1)
        return "\n".join(results)

    def manage_room_price(self, hotel_name: str, manager_name: str, room_type: str, start_date: str, end_date: str, price: float) -> str:
        try:
            y1, m1, d1 = [int(x) for x in str(start_date).split('-')]
            y2, m2, d2 = [int(x) for x in str(end_date).split('-')]
            base_today = datetime.now(timezone.utc).date()
            start = base_today.replace(year=y1, month=m1, day=d1)
            end = base_today.replace(year=y2, month=m2, day=d2)
        except Exception:
            return "❌ Invalid date format. Use YYYY-MM-DD."
        results = []
        current = start
        while current <= end:
            date_str = current.isoformat()
            result = self.update_room_price(hotel_name, manager_name, room_type, date_str, price)
            results.append(f"{date_str}: {result}")
            current += timedelta(days=1)
        return "\n".join(results)

    def read_inventory_information_from_description(self, hotel_name: str, inventory_information: str) -> str:
        try:
            parsed_data = self._parse_inventory_information(inventory_information)
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
                result += f"    - Price: {pricing_data.get('currency', '')} {pricing_data.get('base_price', 0.0):.2f}\n"
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

    def check_room_pricing(self, hotel_name: str, room_type: str, stay_date: str = None, end_date: str = None) -> str:
        try:
            today = datetime.now(timezone.utc).date()
        except Exception:
            return "❌ Missing hotel timezone for pricing check"
        if stay_date:
            if stay_date.lower() == "today":
                stay_date = today.isoformat()
            elif stay_date.lower() == "tomorrow":
                stay_date = (today + timedelta(days=1)).isoformat()
        if end_date:
            if end_date.lower() == "today":
                end_date = today.isoformat()
            elif end_date.lower() == "tomorrow":
                end_date = (today + timedelta(days=1)).isoformat()
        # If only stay_date is provided, query only that date
        if stay_date and not end_date:
            return self.read_room_price(hotel_name, room_type, stay_date, stay_date)
        return self.read_room_price(hotel_name, room_type, stay_date, end_date)

# Create global instance
inventory_manager = InventoryManager()


# Remove all Pydantic BaseModel classes and tool wrappers that use them
# Refactor all tool wrappers to use explicit keyword arguments

@tool
def manage_room_price(
    hotel_name: str,
    manager_name: str,
    room_type: str,
    start_date: str,
    end_date: str,
    price: float
) -> str:
    """Manage (create/update) room price for a hotel and room type (write operation)"""
    return inventory_manager.manage_room_price(
        hotel_name,
        manager_name,
        room_type,
        start_date,
        end_date,
        price
    )

@tool
def parse_inventory_information_tool(hotel_name: str, inventory_information: str) -> None:
    """Parse unstructured inventory information into structured data using LLM (subagent/tool level). Prints the result directly."""
    from langchain_openai import ChatOpenAI
    import os
    import json
    from datetime import timedelta
    llm = ChatOpenAI(
        model="gpt-4.1-nano",
        temperature=0.1,
        openai_api_key=os.getenv('OPENAI_API_KEY')
    )
    # Use hotel-local context for example date hints
    base = datetime.now(timezone.utc)
    today = base
    date_context = {
        'today': today.date().isoformat(),
        'tomorrow': (today + timedelta(days=1)).date().isoformat(),
        'next_week': (today + timedelta(days=7)).date().isoformat(),
        'next_month': (today + timedelta(days=30)).date().isoformat()
    }
    extraction_prompt = f"""Parse this inventory/pricing information into structured JSON format:

HOTEL CONTEXT: {hotel_name}
CURRENT INPUT: \"{inventory_information}\"

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
- Today's date for reference: {date_context['today']}
- Common date formats: "tomorrow", "next week", "Dec 15-20", etc."""
    response = llm.invoke([{"role": "user", "content": extraction_prompt}])
    response_content = response.content.strip()
    if response_content.startswith('```json'):
        response_content = response_content.replace('```json', '').replace('```', '').strip()
    elif response_content.startswith('```'):
        response_content = response_content.replace('```', '').strip()
    try:
        parsed_data = json.loads(response_content)
        print(parsed_data)
    except Exception as e:
        print({
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
                "currency": "",
                "operation": ""
            },
            "notes": inventory_information
        })

@tool
def read_inventory_information_from_description(
    hotel_name: str,
    inventory_information: str
) -> str:
    """Parse and read inventory/pricing information from unstructured description (read operation)"""
    return inventory_manager.read_inventory_information_from_description(
        hotel_name,
        inventory_information
    )

@tool
def check_room_pricing(
    hotel_name: str,
    room_type: str,
    stay_date: str = None,
    end_date: str = None
) -> str:
    """Check room pricing for a hotel and room type for given dates (read operation)"""
    return inventory_manager.check_room_pricing(
        hotel_name,
        room_type,
        stay_date,
        end_date
    )

@tool
def manage_room_quantity(
    hotel_name: str,
    manager_name: str,
    room_type: str,
    start_date: str,
    end_date: str,
    total_quantity: int,
    blocked_quantity: int = 0
) -> str:
    """Manage room quantity for a hotel and room type (write operation). If stop sell, let blocked_quantity = total_quantity, use read_nightly_inventory to check first"""
    return inventory_manager.manage_room_quantity(
        hotel_name,
        manager_name,
        room_type,
        start_date,
        end_date,
        total_quantity,
        blocked_quantity
    )

@tool
def read_nightly_inventory(
    hotel_name: str,
    room_type: str,
    stay_date: str
) -> str:
    """Read nightly inventory for a specific date (read operation)."""
    return inventory_manager.read_nightly_inventory(
        hotel_name,
        room_type,
        stay_date
    )

@tool
def read_nightly_inventory_range(
    hotel_name: str,
    room_type: str,
    start_date: str,
    end_date: str
) -> str:
    """Read nightly inventory for a date range (read operation)."""
    data = inventory_manager.read_nightly_inventory_range(
        hotel_name,
        room_type,
        start_date,
        end_date
    )
    try:
        items = data.get("items") if isinstance(data, dict) else []
        if not items:
            if isinstance(data, dict) and data.get("error"):
                return f"❌ {data.get('error')}"
            return "❌ No inventory found for the specified range"
        lines = [f"📊 Nightly Inventory for {room_type} at {hotel_name} ({start_date} to {end_date}):"]
        for it in items:
            lines.append(
                f"  • {it.get('date')}: Total {it.get('total_inventory')} | Active {it.get('active_inventory')} (updated {it.get('updated_at')})"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Error formatting nightly inventory: {e}"

@tool
def read_available_room_types_tool(hotel_name: str) -> str:
    """Read available room types that have inventory rows for a hotel (read operation)."""
    return inventory_manager.read_available_room_types(hotel_name)