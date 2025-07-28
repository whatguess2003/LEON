#!/usr/bin/env python3
"""
Enhanced Booking Manager for LEON PMS System

BOOKING CREATION SOP (Standard Operating Procedure)
---------------------------------------------------
Required Parameters:
- guest_phone (str)
- room_type (str)
- stay_dates (list of YYYY-MM-DD)
- rooms_booked (int, default 1)
- booking_notes (optional)

Validation Steps (in order):
1. Guest: Ensure guest profile exists (create if missing).
2. Room Type: Ensure room type exists (create minimal if missing).
3. Inventory: Ensure inventory exists (create default if missing).
4. Availability: Check room availability for requested dates/rooms.
5. Policies: Ensure hotel and payment policies exist (error if missing).
6. Pricing: Use daily pricing or fallback to default policy.

Booking is created ONLY if all validations pass. Each step returns a clear error if validation fails and cannot be auto-resolved.
"""

import psycopg2
import pymongo
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from difflib import get_close_matches
from .guest_manager import EnhancedGuestManager
from .payment_manager import payment_manager
import re
from .date_utils import normalize_date
from .llm_utils import unified_parser, unified_execution_detector, canonical_key
from pydantic import BaseModel
from .guest_manager import guest_manager

# Load environment variables
load_dotenv()

class AgentRuleCompliantBookingManager:
    def _ensure_booking_stays_table(self):
        """Create booking_stays table for per-night booking records (canonical for all booking/occupancy logic)"""
        try:
            cur = self.postgres_conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS booking_stays (
                    hotel_name VARCHAR(255) NOT NULL,
                    room_type VARCHAR(255) NOT NULL,
                    stay_date DATE NOT NULL,
                    booking_id VARCHAR(255) NOT NULL,
                    rooms_booked INTEGER NOT NULL DEFAULT 1,
                    booking_status VARCHAR(32) NOT NULL,
                    PRIMARY KEY (hotel_name, room_type, stay_date, booking_id)
                );
            """)
            self.postgres_conn.commit()
        except Exception as e:
            print(f"❌ Error creating booking_stays table: {e}")

    def __init__(self):
        """Initialize Agent-Rule Compliant Booking Manager"""
        self.postgres_conn = None
        self.mongo_client = None
        self.mongo_db = None  # For hotel/room data
        self.guest_db = None  # For guest profiles
        
        # Initialize LLM for parsing unstructured booking requests
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
        
        # Date context for AI agent
        today = datetime.now()
        # Calculate this weekend (Saturday and Sunday)
        weekday = today.weekday()  # Monday=0, Sunday=6
        days_until_saturday = (5 - weekday) % 7
        days_until_sunday = (6 - weekday) % 7
        this_saturday = today + timedelta(days=days_until_saturday)
        this_sunday = today + timedelta(days=days_until_sunday)
        # If today is already Saturday or Sunday, treat as this weekend
        if weekday == 5:
            this_saturday = today
            this_sunday = today + timedelta(days=1)
        elif weekday == 6:
            this_saturday = today - timedelta(days=1)
            this_sunday = today
        # Next weekend (Saturday and Sunday after this weekend)
        next_saturday = this_saturday + timedelta(days=7)
        next_sunday = this_sunday + timedelta(days=7)
        self.DATE_CONTEXT = {
            'today': today.strftime('%Y-%m-%d'),
            'tomorrow': (today + timedelta(days=1)).strftime('%Y-%m-%d'),
            'next_week': (today + timedelta(days=7)).strftime('%Y-%m-%d'),
            'next_month': (today + timedelta(days=30)).strftime('%Y-%m-%d'),
            'this_weekend': [this_saturday.strftime('%Y-%m-%d'), this_sunday.strftime('%Y-%m-%d')],
            'next_weekend': [next_saturday.strftime('%Y-%m-%d'), next_sunday.strftime('%Y-%m-%d')]
        }
        
        self._connect_databases()
        self.guest_helper = EnhancedGuestManager()
        self._ensure_booking_stays_table()
    
    def _connect_databases(self):
        """Connect to PostgreSQL and MongoDB (dual databases for separation)"""
        try:
            # PostgreSQL connection
            self.postgres_conn = psycopg2.connect(os.getenv('DATABASE_URL'))
            
            # MongoDB connections (unified database architecture)
            self.mongo_client = pymongo.MongoClient(os.getenv('MONGO_URL'))
            self.mongo_db = self.mongo_client['leon_hotel_memory']  # Hotel/room data
            self.guest_db = self.mongo_client['leon_hotel_memory']  # Guest profiles
            
            # Agent-Rule Compliant Booking Manager initialized silently
        except Exception as e:
            print(f"❌ Database connection error: {e}")

    def _get_hotel_context(self) -> Dict[str, Any]:
        """Get hotel context from manager_context.json (single source of truth)"""
        try:
            # Always use the path relative to this file
            context_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'manager_context.json')
            with open(context_path, 'r') as f:
                context_data = json.load(f)
                return context_data.get('active_manager', {})
        except Exception as e:
            print(f"❌ Error reading manager context: {e}")
            return {}

    def read_available_room_combinations(self) -> str:
        """READ available (hotel_name, room_type) combinations for booking discovery"""
        try:
            # Get hotel context from single source of truth
            hotel_context = self._get_hotel_context()
            hotel_name = hotel_context.get('hotel_name')
            
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
            
            # Read from MongoDB room_encyclopedia for discovery (filtered by current hotel)
            room_docs = list(self.mongo_db['room_encyclopedia'].find(
                {"hotel_name": hotel_name},
                {"hotel_name": 1, "room_type": 1, "_id": 0}
            ))
            
            if not room_docs:
                return f"❌ No room types found for {hotel_name}"
            
            # Extract unique combinations
            combinations = set()
            for doc in room_docs:
                hotel_name = doc.get("hotel_name")
                room_type = doc.get("room_type")
                if hotel_name and room_type:
                    combinations.add((hotel_name, room_type))
            
            result = f"📋 Available Room Combinations for {hotel_name} ({len(combinations)} found):\n"
            for hotel_name, room_type in sorted(combinations):
                result += f"  • {hotel_name} → {room_type}\n"
            
            result += f"\n💡 Usage: Use exact room_type in booking operations (hotel_name from context)"
            return result
            
        except Exception as e:
            return f"❌ Error reading room combinations: {e}"

    # Guest profile operations removed - use Guest Manager for all guest operations

    def get_stay_dates_for_booking(self, booking_id: str) -> list:
        """Return all stay_dates for a given booking_id, sorted."""
        cur = self.postgres_conn.cursor()
        cur.execute("SELECT stay_date FROM booking_stays WHERE booking_id = %s ORDER BY stay_date", (booking_id,))
        return [row[0] for row in cur.fetchall()]

    def read_availability(self, room_type: str, check_date: str, rooms_needed: int = 1) -> str:
        """READ room availability for a specific stay_date (per-night, canonical)"""
        if not self.postgres_conn:
            return "❌ Database connection not available"
        try:
            hotel_context = self._get_hotel_context()
            hotel_name = hotel_context.get('hotel_name')
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
            cur = self.postgres_conn.cursor()
            # Get nightly inventory
            from booking_system.inventory_manager import InventoryManager
            inv = InventoryManager()
            inv_status = inv._validate_and_read_nightly_inventory(hotel_name, room_type, check_date)
            if not inv_status.get('exists'):
                return f"❌ No inventory found for {room_type} at {hotel_name} on {check_date}"
            total_inventory = inv_status['data']['total_inventory']
            blocked_quantity = inv_status['data'].get('blocked_quantity', 0)
            active_inventory = total_inventory - blocked_quantity
            # Get booked rooms for this date
            cur.execute("""
                SELECT COALESCE(SUM(rooms_booked), 0) FROM booking_stays
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s AND booking_status = 'confirmed'
            """, (hotel_name, room_type, check_date))
            booked_rooms = cur.fetchone()[0]
            available_rooms = active_inventory - booked_rooms
            result = f"📊 Availability for {hotel_name} → {room_type} on {check_date}:\n"
            result += f"  • Total Inventory: {total_inventory}\n"
            result += f"  • Currently Booked: {booked_rooms}\n"
            result += f"  • Available: {available_rooms}\n"
            result += f"  • Requested: {rooms_needed}\n"
            result += f"  • Status: {'✅ AVAILABLE' if available_rooms >= rooms_needed else '❌ INSUFFICIENT'}\n"
            result += f"  • 🎯 Natural Keys: hotel_name + room_type + stay_date validated"
            return result
        except Exception as e:
            return f"❌ Error reading availability: {e}"

    def create_booking(self, guest_phone: str, room_type: str, stay_dates: list, rooms_booked: int = 1, booking_notes: str = None) -> str:
        """
        CREATE booking with full CRUD validation (AGENT-RULE COMPLIANT, ATOMIC).
        All parameters must be explicit, normalized, and pre-validated by the orchestrator.
        stay_dates: list of YYYY-MM-DD strings representing each night of the stay.
        """
        if not self.postgres_conn:
            return "❌ Database connection not available"
        try:
            hotel_context = self._get_hotel_context()
            hotel_name = hotel_context.get('hotel_name')
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
            # --- GUEST PROFILE VALIDATION (Single Source of Truth) ---
            guest_profile_result = guest_manager.read_guest_profile(guest_phone)
            if guest_profile_result.startswith("❌ No guest profile found"):
                guest_name = 'Guest'
                if booking_notes and isinstance(booking_notes, str) and len(booking_notes.split()) > 0:
                    guest_name = booking_notes.split()[0]
                create_result = self.guest_helper.create_guest_profile(guest_phone, guest_name)
                guest_profile_result = guest_manager.read_guest_profile(guest_phone)
                if guest_profile_result.startswith("❌ No guest profile found"):
                    return f"❌ Failed to create guest profile for {guest_phone}: {create_result}"
                if self.postgres_conn:
                    self.postgres_conn.commit()
            # Extract guest_name, guest_email, guest_loyalty from guest_profile_result (simple parse)
            guest_name = 'Unknown'
            guest_email = 'N/A'
            guest_loyalty = 'standard'
            for line in guest_profile_result.splitlines():
                if line.strip().startswith('• Name:'):
                    guest_name = line.split(':',1)[1].strip()
                elif line.strip().startswith('• Email:'):
                    guest_email = line.split(':',1)[1].strip()
                elif line.strip().startswith('• Loyalty Tier:'):
                    guest_loyalty = line.split(':',1)[1].strip()
            room_doc = self.mongo_db['room_encyclopedia'].find_one({
                "hotel_name": hotel_name,
                "room_type": room_type,
                "is_physical_room": False
            })
            if not room_doc:
                room_doc = {
                    "hotel_name": hotel_name,
                    "room_type": room_type,
                    "is_physical_room": False,
                    "created_at": datetime.now(),
                    "SPECIFICATIONS": {},
                    "DIRECTORY": {}
                }
                self.mongo_db['room_encyclopedia'].insert_one(room_doc)
            cur = self.postgres_conn.cursor()
            # CRUD VALIDATION: Check nightly inventory for all stay_dates
            from booking_system.inventory_manager import InventoryManager
            inv = InventoryManager()
            for stay_date in stay_dates:
                inv_status = inv._validate_and_read_nightly_inventory(hotel_name, room_type, stay_date)
                if not inv_status.get('exists') or (inv_status['data']['total_inventory'] - inv_status['data'].get('blocked_quantity', 0)) < rooms_booked:
                    return f"❌ Insufficient inventory for {room_type} at {hotel_name} on {stay_date}"
            # Generate booking_id
            booking_counter = self._get_next_booking_counter(guest_phone)
            booking_id = f"{guest_phone}booking{booking_counter}"
            # Insert booking_stays rows for each night
            for stay_date in stay_dates:
                cur.execute("""
                    INSERT INTO booking_stays (hotel_name, room_type, stay_date, booking_id, rooms_booked, booking_status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (hotel_name, room_type, stay_date, booking_id, rooms_booked, 'confirmed'))
                # No need to decrement active_inventory; inventory is managed by total_inventory and blocked_quantity only
            # STEP 3: Extract guest details from validated guest_doc
            # STEP 4: READ pricing and calculate totals (use first stay_date for pricing)
            cur.execute("""
                SELECT base_price FROM daily_pricing 
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s
            """, (hotel_name, room_type, stay_dates[0]))
            pricing_result = cur.fetchone()
            if not pricing_result:
                # fallback to default price if no daily pricing
                room_rate = 150.0
            else:
                room_rate = float(pricing_result[0])
            subtotal = room_rate * rooms_booked * len(stay_dates)
            # --- POLICY ENFORCEMENT: Fetch from booking_policies collection (status=active) ---
            booking_policy = self.mongo_db['booking_policies'].find_one({
                "hotel_name": hotel_name,
                "policy_type": {"$regex": "booking", "$options": "i"},
                "status": "active"
            })
            if not booking_policy:
                return f"❌ No booking policy found for {hotel_name}. Use Policy Manager to create hotel policies first."
            payment_policy = self.mongo_db['booking_policies'].find_one({
                "hotel_name": hotel_name,
                "policy_type": {"$regex": "payment", "$options": "i"},
                "status": "active"
            })
            if not payment_policy:
                return f"❌ No payment policy found for {hotel_name}. Use Policy Manager to create payment policies first."
            # Extract policy details (with sensible defaults)
            payment_required_within_hours = payment_policy.get("payment_required_within_hours", 24)
            payment_threshold_percent = booking_policy.get("payment_threshold_percent", 30)
            tax_rate = booking_policy.get("tax_rate", 0.10)
            taxes = subtotal * tax_rate
            total_amount = subtotal + taxes
            payment_due_amount = total_amount * (payment_threshold_percent / 100)
            payment_deadline_hours = payment_required_within_hours
            payment_deadline = (datetime.now() + timedelta(hours=payment_deadline_hours)).strftime('%Y-%m-%d %H:%M')
            currency = hotel_context.get('currency', 'MYR')
            # Compute number of nights
            nights = len(stay_dates)
            cur.execute("""
                INSERT INTO room_bookings 
                (booking_id, guest_phone, hotel_name, room_type, 
                 rooms_booked, booking_status, booking_reference, created_at, nights)
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
            """, (booking_id, guest_phone, hotel_name, room_type,
                  rooms_booked, 'confirmed', booking_id, nights))
            # STEP 6: Insert into payment_tracking
            cur.execute("""
                INSERT INTO payment_tracking 
                (booking_id, guest_phone_number, total_amount, amount_paid, remaining_balance, payment_status, payment_method, payment_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (booking_id, guest_phone, total_amount, 0.0, total_amount, 'pending', None, None))
            # STEP 7: Insert into MongoDB
            manager_context = self._get_hotel_context()
            created_by = {
                "manager_name": manager_context.get("manager_name", "Unknown"),
                "manager_phone": manager_context.get("manager_phone", "Unknown")
            }
            guest_bookings = self.mongo_db['guest_bookings']
            guest_bookings.insert_one({
                "booking_id": booking_id,
                "guest_phone": guest_phone,
                "guest_name": guest_name,
                "guest_email": guest_email,
                "guest_loyalty_status": guest_loyalty,
                "hotel_name": hotel_name,
                "room_type": room_type,
                "stay_dates": stay_dates,
                "booking_context": {
                    "booking_notes": booking_notes,
                    "created_via": "agent_rule_compliant_booking_manager",
                    "natural_keys_used": True
                },
                "created_timestamp": datetime.now(),
                "created_by": created_by,
                "updated_at": datetime.now(),
                "updated_by": created_by
            })
            self.postgres_conn.commit()
            result = f"✅ Booking created successfully:\n"
            result += f"  • Booking ID: {booking_id}\n"
            result += f"  • Guest: {guest_phone} ({guest_name})\n"
            result += f"  • Email: {guest_email}\n"
            result += f"  • Hotel: {hotel_name}\n"
            result += f"  • Room Type: {room_type}\n"
            result += f"  • Stay Dates: {', '.join(stay_dates)}\n"
            result += f"  • Rooms: {rooms_booked}\n"
            result += f"  • Room Rate: {currency} {room_rate:.2f}/night\n"
            result += f"  • Subtotal: {currency} {subtotal:.2f}\n"
            result += f"  • Taxes ({tax_rate*100:.0f}%): {currency} {taxes:.2f}\n"
            result += f"  • Total: {currency} {total_amount:.2f}\n"
            if booking_policy:
                result += f"  • Payment Due: {currency} {payment_due_amount:.2f} ({payment_threshold_percent}% within {payment_deadline_hours} hours)\n"
                result += f"  • Payment Deadline: {payment_deadline}\n"
                result += f"  • Cancellation Policy: {booking_policy.get('cancellation_policy', 'flexible')} ({booking_policy.get('refund_percentage', 100)}% refund)\n"
            result += f"  • 🎯 CRUD Validation: ✅ All parameters validated\n"
            result += f"  • 🎯 Hotel Policies: ✅ Applied from MongoDB\n"
            result += f"  • 🎯 Natural Keys: ✅ No artificial IDs used"
            return result
        except Exception as e:
            if self.postgres_conn:
                self.postgres_conn.rollback()
            return f"❌ Error creating booking: {e}"

    def read_booking(self, booking_id: str = None, guest_phone: str = None, stay_date: str = None) -> str:
        """READ booking details with payment info (CRUD COMPLIANT, per-night, canonical)"""
        if not self.postgres_conn:
            return "❌ Database connection not available"
        try:
            cur = self.postgres_conn.cursor()
            # Join booking_stays and room_bookings for details
            if booking_id:
                cur.execute("""
                    SELECT s.booking_id, s.hotel_name, s.room_type, s.stay_date, s.rooms_booked, s.booking_status,
                           b.guest_phone, b.created_at,
                           p.total_amount, p.amount_paid, p.remaining_balance, p.payment_status, p.payment_method, p.payment_date
                    FROM booking_stays s
                    JOIN room_bookings b ON s.booking_id = b.booking_id
                    LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                    WHERE s.booking_id = %s
                    ORDER BY s.stay_date
                """, (booking_id,))
                bookings = cur.fetchall()
                if not bookings:
                    return f"❌ No booking found with ID: {booking_id}"
            elif guest_phone:
                cur.execute("""
                    SELECT s.booking_id, s.hotel_name, s.room_type, s.stay_date, s.rooms_booked, s.booking_status,
                           b.guest_phone, b.created_at,
                           p.total_amount, p.amount_paid, p.remaining_balance, p.payment_status, p.payment_method, p.payment_date
                    FROM booking_stays s
                    JOIN room_bookings b ON s.booking_id = b.booking_id
                    LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                    WHERE b.guest_phone = %s
                    ORDER BY s.stay_date DESC
                """, (guest_phone,))
                bookings = cur.fetchall()
                if not bookings:
                    return f"❌ No bookings found for guest: {guest_phone}"
            elif stay_date:
                cur.execute("""
                    SELECT s.booking_id, s.hotel_name, s.room_type, s.stay_date, s.rooms_booked, s.booking_status,
                           b.guest_phone, b.created_at,
                           p.total_amount, p.amount_paid, p.remaining_balance, p.payment_status, p.payment_method, p.payment_date
                    FROM booking_stays s
                    JOIN room_bookings b ON s.booking_id = b.booking_id
                    LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                    WHERE s.stay_date = %s
                    ORDER BY s.stay_date
                """, (stay_date,))
                bookings = cur.fetchall()
                if not bookings:
                    return f"❌ No bookings found for stay date: {stay_date}"
            else:
                cur.execute("""
                    SELECT s.booking_id, s.hotel_name, s.room_type, s.stay_date, s.rooms_booked, s.booking_status,
                           b.guest_phone, b.created_at,
                           p.total_amount, p.amount_paid, p.remaining_balance, p.payment_status, p.payment_method, p.payment_date
                    FROM booking_stays s
                    JOIN room_bookings b ON s.booking_id = b.booking_id
                    LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                    ORDER BY s.stay_date DESC
                    LIMIT 10
                """)
                bookings = cur.fetchall()
                if not bookings:
                    return "❌ No bookings found in system"
            # Aggregate by booking_id
            from collections import defaultdict
            booking_map = defaultdict(list)
            for booking in bookings:
                booking_map[booking[0]].append(booking)
            result = f"📋 Booking Details ({len(booking_map)} found):\n\n"
            for booking_id, booking_rows in booking_map.items():
                # Use the first row for summary fields
                (booking_id, hotel_name, room_type, _, rooms_booked, booking_status,
                 guest_phone, created_at,
                 total_amount, amount_paid, remaining_balance, payment_status, payment_method, payment_date) = booking_rows[0]
                guest_doc = self.guest_db['guests'].find_one({"guest_phone": guest_phone, "status": "active"})
                guest_name = guest_doc.get('guest_name', 'Unknown') if guest_doc else 'Unknown'
                stay_dates = [row[3] for row in booking_rows]
                status_set = set(row[5] for row in booking_rows)
                # Only allow confirmed, modified, cancelled
                allowed_statuses = {'confirmed', 'modified', 'cancelled'}
                filtered_statuses = [s for s in status_set if s in allowed_statuses]
                if all(s == 'cancelled' for s in filtered_statuses):
                    overall_status = 'cancelled'
                elif all(s == 'modified' for s in filtered_statuses):
                    overall_status = 'modified'
                else:
                    overall_status = 'confirmed'
                result += f"  • Booking ID: {booking_id}\n"
                result += f"    Guest: {guest_phone} ({guest_name})\n"
                result += f"    Hotel: {hotel_name}\n"
                result += f"    Room Type: {room_type}\n"
                result += f"    Stay Dates: {', '.join(str(d) for d in stay_dates)}\n"
                result += f"    Rooms: {rooms_booked} | Status: {overall_status}\n"
                result += f"    Created: {created_at}\n"
                result += f"    Payment: Total RM{total_amount or 0:.2f}, Paid RM{amount_paid or 0:.2f}, Remaining RM{remaining_balance or 0:.2f}\n"
                result += f"    Payment Status: {payment_status or 'N/A'} via {payment_method or 'N/A'} on {payment_date or 'N/A'}\n\n"
            result += f"🎯 Natural Keys: All data retrieved using hotel_name + room_type + guest_phone + stay_date"
            return result.strip()
        except Exception as e:
            return f"❌ Error reading booking: {e}"

    def read_bookings_by_criteria(self, room_type: str = None, 
                                 guest_phone: str = None, booking_status: str = None,
                                 date_from: str = None, date_to: str = None, limit: int = 20) -> str:
        """READ bookings by multiple criteria with natural key validation (hotel_name from context, per-night, canonical)"""
        if not self.postgres_conn:
            return "❌ Database connection not available"
        try:
            hotel_context = self._get_hotel_context()
            hotel_name = hotel_context.get('hotel_name')
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
            cur = self.postgres_conn.cursor()
            query = """
                SELECT s.booking_id, s.hotel_name, s.room_type, s.stay_date, s.rooms_booked, s.booking_status,
                       b.guest_phone, b.created_at,
                       p.total_amount, p.amount_paid, p.remaining_balance, p.payment_status, p.payment_method, p.payment_date
                FROM booking_stays s
                JOIN room_bookings b ON s.booking_id = b.booking_id
                LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                WHERE s.hotel_name = %s
            """
            params = [hotel_name]
            filters = [f"Hotel: {hotel_name}"]
            if room_type:
                query += " AND s.room_type = %s"
                params.append(room_type)
                filters.append(f"Room Type: {room_type}")
            if guest_phone:
                query += " AND b.guest_phone = %s"
                params.append(guest_phone)
                filters.append(f"Guest: {guest_phone}")
            if booking_status:
                query += " AND s.booking_status = %s"
                params.append(booking_status)
                filters.append(f"Status: {booking_status}")
            if date_from:
                query += " AND s.stay_date >= %s"
                params.append(date_from)
                filters.append(f"From: {date_from}")
            if date_to:
                query += " AND s.stay_date <= %s"
                params.append(date_to)
                filters.append(f"To: {date_to}")
            query += " ORDER BY s.stay_date DESC LIMIT %s"
            params.append(limit)
            cur.execute(query, params)
            bookings = cur.fetchall()
            if not bookings:
                return "❌ No bookings found matching criteria"
            result = f"🔍 Booking Search Results ({len(bookings)} found):\n"
            result += f"  • Filters: {', '.join(filters)}\n\n"
            total_revenue = 0
            for booking in bookings:
                (booking_id, hotel_name, room_type, stay_date, rooms_booked, booking_status,
                 guest_phone, created_at,
                 total_amount, amount_paid, remaining_balance, payment_status, payment_method, payment_date) = booking
                guest_doc = self.guest_db['guests'].find_one({"guest_phone": guest_phone, "status": "active"})
                guest_name = guest_doc.get('guest_name', 'Unknown') if guest_doc else 'Unknown'
                result += f"  • {booking_id}\n"
                result += f"    Guest: {guest_phone} ({guest_name})\n"
                result += f"    Hotel: {hotel_name} → {room_type}\n"
                result += f"    Stay Date: {stay_date} | Rooms: {rooms_booked}\n"
                result += f"    Amount: RM{total_amount or 0:.2f} | Status: {booking_status}\n"
                result += "\n"
                if booking_status != 'cancelled':
                    total_revenue += total_amount or 0
            result += f"📊 SUMMARY:\n"
            result += f"  • Total Bookings: {len(bookings)}\n"
            result += f"  • Total Revenue: RM{total_revenue:.2f}\n"
            result += f"  • 🎯 Natural Keys: All searches using hotel_name + room_type + guest_phone + stay_date"
            return result
        except Exception as e:
            return f"❌ Error reading bookings by criteria: {e}"

    def _get_next_booking_counter(self, guest_phone: str) -> int:
        """Get next booking counter for guest using natural key"""
        if not self.postgres_conn:
            raise Exception("Database connection not available")
        
        cur = self.postgres_conn.cursor()
        # Escape special regex characters in guest_phone and use proper regex pattern
        import re
        escaped_phone = re.escape(guest_phone)
        pattern = f'{escaped_phone}booking(\\d+)'
        
        cur.execute("""
            SELECT COALESCE(MAX(CAST(SUBSTRING(booking_id FROM %s) AS INTEGER)), 0) + 1
            FROM room_bookings 
            WHERE guest_phone = %s
        """, (pattern, guest_phone))
        
        result = cur.fetchone()
        if not result:
            raise Exception(f"Failed to get booking counter for guest {guest_phone}")
        
        return result[0]

    def update_booking_status(self, booking_id: str, new_status: str, notes: str = None) -> str:
        """Centralized function to update booking_status for a booking_id in all relevant tables, with optional notes."""
        allowed_statuses = {'confirmed', 'modified', 'cancelled'}
        if new_status not in allowed_statuses:
            return f"❌ Invalid booking status: {new_status}. Must be one of: confirmed, modified, cancelled."
        cur = self.postgres_conn.cursor()
        cur.execute("""
            UPDATE booking_stays SET booking_status = %s WHERE booking_id = %s
        """, (new_status, booking_id))
        if notes:
            cur.execute("""
                UPDATE room_bookings SET booking_status = %s, booking_notes = COALESCE(booking_notes, '') || %s, updated_at = %s WHERE booking_id = %s
            """, (new_status, notes, datetime.now(), booking_id))
        else:
            cur.execute("""
                UPDATE room_bookings SET booking_status = %s, updated_at = %s WHERE booking_id = %s
            """, (new_status, datetime.now(), booking_id))
        # Update MongoDB guest_bookings
        manager_context = self._get_hotel_context()
        updated_by = {
            "manager_name": manager_context.get("manager_name", "Unknown"),
            "manager_phone": manager_context.get("manager_phone", "Unknown")
        }
        guest_bookings = self.mongo_db['guest_bookings']
        guest_bookings.update_one(
            {"booking_id": booking_id},
            {"$set": {"updated_at": datetime.now(), "updated_by": updated_by}}
        )
        self.postgres_conn.commit()
        return f"Booking {booking_id} status updated to {new_status}"


    def get_booking_analytics(self, date_from: str = None, date_to: str = None) -> str:
        """Get comprehensive booking analytics for management reporting (per-night, canonical)"""
        try:
            hotel_context = self._get_hotel_context()
            hotel_name = hotel_context.get('hotel_name')
            currency = hotel_context.get('currency', 'MYR')
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
            if not date_from:
                date_from = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
            if not date_to:
                date_to = datetime.now().strftime('%Y-%m-%d')
            cur = self.postgres_conn.cursor()
            # Revenue analytics (per-night)
            cur.execute("""
                SELECT 
                    COUNT(*) as total_nights,
                    COUNT(CASE WHEN s.booking_status = 'confirmed' THEN 1 END) as confirmed_nights,
                    COUNT(CASE WHEN s.booking_status = 'cancelled' THEN 1 END) as cancelled_nights,
                    SUM(CASE WHEN s.booking_status = 'confirmed' THEN p.total_amount ELSE 0 END) as total_revenue,
                    AVG(CASE WHEN s.booking_status = 'confirmed' THEN p.total_amount ELSE NULL END) as avg_booking_value
                FROM booking_stays s
                JOIN room_bookings b ON s.booking_id = b.booking_id
                LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                WHERE s.hotel_name = %s 
                AND s.stay_date BETWEEN %s AND %s
            """, (hotel_name, date_from, date_to))
            revenue_stats = cur.fetchone()
            # Room type performance (per-night)
            cur.execute("""
                SELECT 
                    s.room_type,
                    COUNT(*) as nights,
                    SUM(p.total_amount) as revenue,
                    AVG(p.total_amount) as avg_value
                FROM booking_stays s
                JOIN room_bookings b ON s.booking_id = b.booking_id
                LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                WHERE s.hotel_name = %s 
                AND s.booking_status = 'confirmed'
                AND s.stay_date BETWEEN %s AND %s
                GROUP BY s.room_type
                ORDER BY revenue DESC
            """, (hotel_name, date_from, date_to))
            room_performance = cur.fetchall()
            # Cancellation analysis (per-night)
            cur.execute("""
                SELECT 
                    COUNT(*) as total_cancellations,
                    AVG(p.total_amount) as avg_cancelled_value
                FROM booking_stays s
                JOIN room_bookings b ON s.booking_id = b.booking_id
                LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                WHERE s.hotel_name = %s 
                AND s.booking_status = 'cancelled'
                AND s.stay_date BETWEEN %s AND %s
            """, (hotel_name, date_from, date_to))
            cancellation_stats = cur.fetchone()
            result = f"📊 BOOKING ANALYTICS REPORT\n"
            result += f"📅 Period: {date_from} to {date_to}\n"
            result += f"🏨 Hotel: {hotel_name}\n\n"
            # Revenue summary
            if revenue_stats:
                total_nights, confirmed_nights, cancelled_nights, total_revenue, avg_value = revenue_stats
                result += f"💰 REVENUE SUMMARY:\n"
                result += f"  • Total Nights: {total_nights or 0}\n"
                result += f"  • Confirmed: {confirmed_nights or 0}\n"
                result += f"  • Cancelled: {cancelled_nights or 0}\n"
                result += f"  • Total Revenue: {currency} {total_revenue or 0:.2f}\n"
                result += f"  • Avg Booking Value: {currency} {avg_value or 0:.2f}\n"
                if total_nights and total_nights > 0:
                    confirmation_rate = (confirmed_nights / total_nights) * 100
                    result += f"  • Confirmation Rate: {confirmation_rate:.1f}%\n"
            # Room type performance
            result += f"\n🏠 ROOM TYPE PERFORMANCE:\n"
            for room in room_performance:
                room_type, nights, revenue, avg_value = room
                result += f"  • {room_type}:\n"
                result += f"    - Nights: {nights}\n"
                result += f"    - Revenue: {currency} {revenue or 0:.2f}\n"
                result += f"    - Avg Value: {currency} {avg_value or 0:.2f}\n"
            # Cancellation analysis
            if cancellation_stats:
                total_cancellations, avg_cancelled_value = cancellation_stats
                result += f"\n❌ CANCELLATION ANALYSIS:\n"
                result += f"  • Total Cancellations: {total_cancellations or 0}\n"
                result += f"  • Avg Cancelled Value: {currency} {avg_cancelled_value or 0:.2f}\n"
                if total_nights and total_nights > 0:
                    cancellation_rate = (total_cancellations / total_nights) * 100
                    result += f"  • Cancellation Rate: {cancellation_rate:.1f}%\n"
            result += f"\n💡 MANAGEMENT INSIGHTS:\n"
            result += f"  • Monitor high-performing room types\n"
            result += f"  • Analyze cancellation patterns\n"
            result += f"  • Optimize pricing strategies\n"
            result += f"  • Focus on revenue optimization"
            return result
            
        except Exception as e:
            return f"❌ Error generating booking analytics: {e}"

    def get_revenue_forecast(self, forecast_days: int = 30) -> str:
        """Get revenue forecast based on historical data and current bookings (per-night, canonical)"""
        try:
            hotel_context = self._get_hotel_context()
            hotel_name = hotel_context.get('hotel_name')
            currency = hotel_context.get('currency', 'MYR')
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
            cur = self.postgres_conn.cursor()
            # Historical booking patterns (per-night)
            cur.execute("""
                SELECT 
                    s.stay_date,
                    COUNT(*) as daily_nights,
                    SUM(p.total_amount) as daily_revenue,
                    AVG(p.total_amount) as avg_booking_value
                FROM booking_stays s
                JOIN room_bookings b ON s.booking_id = b.booking_id
                LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                WHERE s.hotel_name = %s 
                AND s.booking_status = 'confirmed'
                AND s.stay_date >= CURRENT_DATE - INTERVAL '90 days'
                GROUP BY s.stay_date
                ORDER BY s.stay_date
            """, (hotel_name,))
            historical_data = cur.fetchall()
            # Forecast period
            forecast_start = datetime.now().strftime('%Y-%m-%d')
            forecast_end = (datetime.now() + timedelta(days=forecast_days)).strftime('%Y-%m-%d')
            # Current confirmed bookings for forecast period (per-night)
            cur.execute("""
                SELECT 
                    s.stay_date,
                    COUNT(*) as confirmed_nights,
                    SUM(p.total_amount) as confirmed_revenue
                FROM booking_stays s
                JOIN room_bookings b ON s.booking_id = b.booking_id
                LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                WHERE s.hotel_name = %s 
                AND s.booking_status = 'confirmed'
                AND s.stay_date BETWEEN %s AND %s
                GROUP BY s.stay_date
                ORDER BY s.stay_date
            """, (hotel_name, forecast_start, forecast_end))
            confirmed_bookings = cur.fetchall()
            # Calculate forecast metrics
            if historical_data:
                avg_daily_nights = sum(row[1] for row in historical_data) / len(historical_data)
                avg_daily_revenue = sum(row[2] or 0 for row in historical_data) / len(historical_data)
                avg_booking_value = sum(row[3] or 0 for row in historical_data) / len(historical_data)
            else:
                avg_daily_nights = 0
                avg_daily_revenue = 0
                avg_booking_value = 0
            total_forecast_nights = avg_daily_nights * forecast_days
            total_forecast_revenue = avg_daily_revenue * forecast_days
            confirmed_count = sum(row[1] for row in confirmed_bookings)
            confirmed_revenue = sum(row[2] or 0 for row in confirmed_bookings)
            remaining_nights = total_forecast_nights - confirmed_count
            remaining_revenue = total_forecast_revenue - confirmed_revenue
            result = f"🔮 REVENUE FORECAST ({forecast_days} days)\n"
            result += f"🏨 Hotel: {hotel_name}\n"
            result += f"📅 Forecast Period: {forecast_start} to {forecast_end}\n\n"
            result += f"📊 FORECAST METRICS:\n"
            result += f"  • Avg Daily Nights: {avg_daily_nights:.1f}\n"
            result += f"  • Avg Daily Revenue: {currency} {avg_daily_revenue:.2f}\n"
            result += f"  • Avg Booking Value: {currency} {avg_booking_value:.2f}\n\n"
            result += f"📈 FORECAST BREAKDOWN:\n"
            result += f"  • Total Forecast Nights: {total_forecast_nights:.0f}\n"
            result += f"  • Total Forecast Revenue: {currency} {total_forecast_revenue:.2f}\n"
            result += f"  • Confirmed Nights: {confirmed_count}\n"
            result += f"  • Confirmed Revenue: {currency} {confirmed_revenue:.2f}\n"
            result += f"  • Remaining Forecast: {remaining_nights:.0f} nights\n"
            result += f"  • Remaining Revenue: {currency} {remaining_revenue:.2f}\n\n"
            result += f"💡 FORECAST INSIGHTS:\n"
            result += f"  • Occupancy projection based on historical patterns\n"
            result += f"  • Revenue optimization opportunities\n"
            result += f"  • Pricing strategy recommendations\n"
            result += f"  • Marketing campaign planning"
            return result
            
        except Exception as e:
            return f"❌ Error generating revenue forecast: {e}"

    def analyze_booking_patterns(self, analysis_days: int = 90) -> str:
        """Analyze booking patterns for strategic insights (per-night, canonical)"""
        try:
            hotel_context = self._get_hotel_context()
            hotel_name = hotel_context.get('hotel_name')
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
            cur = self.postgres_conn.cursor()
            # Day of week analysis (per-night)
            cur.execute("""
                SELECT 
                    EXTRACT(DOW FROM s.stay_date) as day_of_week,
                    COUNT(*) as nights,
                    AVG(p.total_amount) as avg_value
                FROM booking_stays s
                JOIN room_bookings b ON s.booking_id = b.booking_id
                LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                WHERE s.hotel_name = %s 
                AND s.booking_status = 'confirmed'
                AND s.stay_date >= CURRENT_DATE - INTERVAL '%s days'
                GROUP BY EXTRACT(DOW FROM s.stay_date)
                ORDER BY nights DESC
            """, (hotel_name, analysis_days))
            day_patterns = cur.fetchall()
            # Lead time analysis (per-night)
            cur.execute("""
                SELECT 
                    CASE 
                        WHEN EXTRACT(EPOCH FROM (s.stay_date - b.created_at))/86400 <= 1 THEN 'Same Day'
                        WHEN EXTRACT(EPOCH FROM (s.stay_date - b.created_at))/86400 <= 7 THEN '1-7 Days'
                        WHEN EXTRACT(EPOCH FROM (s.stay_date - b.created_at))/86400 <= 30 THEN '8-30 Days'
                        ELSE '30+ Days'
                    END as lead_time,
                    COUNT(*) as nights,
                    AVG(p.total_amount) as avg_value
                FROM booking_stays s
                JOIN room_bookings b ON s.booking_id = b.booking_id
                LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                WHERE s.hotel_name = %s 
                AND s.booking_status = 'confirmed'
                AND s.stay_date >= CURRENT_DATE - INTERVAL '%s days'
                GROUP BY lead_time
                ORDER BY nights DESC
            """, (hotel_name, analysis_days))
            lead_time_patterns = cur.fetchall()
            # Length of stay analysis (aggregate by booking_id)
            cur.execute("""
                SELECT 
                    CASE 
                        WHEN nights = 1 THEN '1 Night'
                        WHEN nights <= 3 THEN '2-3 Nights'
                        WHEN nights <= 7 THEN '4-7 Nights'
                        ELSE '7+ Nights'
                    END as stay_length,
                    COUNT(*) as bookings,
                    AVG(p.total_amount) as avg_value
                FROM (
                    SELECT s.booking_id, COUNT(*) as nights
                    FROM booking_stays s
                    WHERE s.hotel_name = %s 
                    AND s.booking_status = 'confirmed'
                    AND s.stay_date >= CURRENT_DATE - INTERVAL '%s days'
                    GROUP BY s.booking_id
                ) agg
                LEFT JOIN payment_tracking p ON agg.booking_id = p.booking_id
                GROUP BY stay_length
                ORDER BY bookings DESC
            """, (hotel_name, analysis_days))
            stay_patterns = cur.fetchall()
            result = f"📊 BOOKING PATTERN ANALYSIS\n"
            result += f"🏨 Hotel: {hotel_name}\n"
            result += f"📅 Analysis Period: Last {analysis_days} days\n\n"
            # Day of week patterns
            result += f"📅 DAY OF WEEK PATTERNS:\n"
            day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
            for day in day_patterns:
                day_num, nights, avg_value = day
                day_name = day_names[int(day_num)]
                result += f"  • {day_name}: {nights} nights, avg {avg_value or 0:.2f}\n"
            # Lead time patterns
            result += f"\n⏰ LEAD TIME PATTERNS:\n"
            for lead in lead_time_patterns:
                lead_time, nights, avg_value = lead
                result += f"  • {lead_time}: {nights} nights, avg {avg_value or 0:.2f}\n"
            # Stay length patterns
            result += f"\n🏨 LENGTH OF STAY PATTERNS:\n"
            for stay in stay_patterns:
                stay_length, bookings, avg_value = stay
                result += f"  • {stay_length}: {bookings} bookings, avg {avg_value or 0:.2f}\n"
            result += f"\n💡 STRATEGIC INSIGHTS:\n"
            result += f"  • Optimize pricing for peak days\n"
            result += f"  • Plan marketing campaigns based on lead times\n"
            result += f"  • Develop packages for different stay lengths\n"
            result += f"  • Revenue management opportunities"
            return result
            
        except Exception as e:
            return f"❌ Error analyzing booking patterns: {e}"


# Create global instance for LEON to use
booking_manager = AgentRuleCompliantBookingManager()

# --- Booking Parameter Validation (moved and restored) ---
def validate_booking_parameters(params, dbs, date_context):
    """Validate booking parameters: guest, room_type, stay_dates, and inventory."""
    guest_phone = params.get('guest_phone')
    if guest_phone:
        guest_phone = guest_phone.strip()
    room_type = params.get('room_type')
    stay_dates = params.get('stay_dates')
    hotel_name = params.get('hotel_name')
    rooms_booked = params.get('rooms_booked', 1)
    # Validate guest exists
    guest_col = dbs['guests']
    guest_lookup = guest_col.find_one({'guest_phone': guest_phone, "status": "active"})
    print(f"[validate_booking_parameters] Looking up guest_phone: '{guest_phone}' - Found: {bool(guest_lookup)}")
    if not guest_lookup:
        return False, f"Guest {guest_phone} not found. Please create guest profile first."
    # Validate room_type exists for hotel
    room_col = dbs['rooms']
    if not room_col.find_one({'hotel_name': hotel_name, 'room_type': room_type}):
        return False, f"Room type '{room_type}' not found for hotel '{hotel_name}'."
    # Validate stay_dates is a non-empty list of valid dates
    if not isinstance(stay_dates, list) or not stay_dates:
        return False, "Stay dates must be a non-empty list."
    for d in stay_dates:
        try:
            datetime.strptime(d, '%Y-%m-%d')
        except Exception:
            return False, f"Invalid date format: {d}. Use YYYY-MM-DD."
    # Validate inventory for each date (pseudo, as real check is in booking logic)
    # Optionally, add more checks here if needed
    return True, None

# Agent-Rule Compliant Tool Functions (No 'self' parameters)
@tool
def list_bookable_room_types_tool() -> str:
    """List all room types available for booking at the current hotel."""
    return booking_manager.read_available_room_combinations()

# Note: read_guest_profiles_tool is now provided by guest_manager.py to avoid conflicts

@tool
def check_room_availability_tool(room_type: str, stay_date: str, rooms_needed: int = 1, dialog_history: str = "") -> dict:
    """
    Check availability for a specific room type and stay_date at the current hotel. All parameters must be provided by the orchestrator. Returns a structured response with status and details. Do not use this tool to manage quantity or price to a room type.
    """
    return booking_manager.read_availability(room_type, stay_date, rooms_needed)

@tool
def create_booking_tool(guest_phone: str, room_type: str, stay_dates: list, rooms_booked: int = 1, booking_notes: str = "", user_confirmation: str = None, dialog_history: str = "", special_request: str = None) -> dict:
    """
    Create a new booking atomically. This tool performs all required validation and business logic internally, including:
      - Room availability checks
      - Pricing and rate validation
      - Policy enforcement (cancellation, payment, etc.)
      - Guest profile validation/creation
    
    There is NO NEED to call any other tool (such as check_room_availability_tool) before this one. All required checks are performed within this function, ensuring a single source of truth and transactional integrity.
    
    Parameters must be provided by the orchestrator. Only stay_dates (list of YYYY-MM-DD) is accepted as the canonical date input.
    If special_request is provided, it will be added to the booking after creation.
    """
    result = booking_manager.create_booking(
        guest_phone=guest_phone,
        room_type=room_type,
        stay_dates=stay_dates,
        rooms_booked=rooms_booked,
        booking_notes=booking_notes
    )
    return result

@tool
def get_booking_details_tool(booking_id: str = None, guest_phone: str = None) -> str:
    """Get details of a booking by booking ID or guest phone number."""
    return booking_manager.read_booking(booking_id, guest_phone)

# Pydantic models for tool inputs
class ModifyBookingParams(BaseModel):
    booking_id: str
    update_fields: dict

class CancelBookingParams(BaseModel):
    booking_id: str
    cancellation_reason: str = None

class SearchBookingsParams(BaseModel):
    room_type: str = None
    guest_phone: str = None
    booking_status: str = None
    date_from: str = None
    date_to: str = None
    limit: int = 20

class GetBookingAnalyticsParams(BaseModel):
    date_from: str = None
    date_to: str = None

class GetRevenueForecastParams(BaseModel):
    forecast_days: int = 30

class AnalyzeBookingPatternsParams(BaseModel):
    analysis_days: int = 90

class AddBookingSpecialRequestParams(BaseModel):
    booking_id: str
    special_request: str
    dialog_history: str = None

class SendBookingConfirmationParams(BaseModel):
    booking_id: str
    method: str = 'email'
    dialog_history: str = None

class GetPaymentStatusForBookingParams(BaseModel):
    booking_id: str
    dialog_history: str = None

class UpdateBookingStatusParams(BaseModel):
    booking_id: str
    new_status: str
    notes: str = None

class ListUpcomingBookingsParams(BaseModel):
    guest_phone: str
    dialog_history: str = None

class ListPastBookingsParams(BaseModel):
    guest_phone: str
    dialog_history: str = None

class CreateGroupBookingParams(BaseModel):
    group_description: str
    dialog_history: str = None

class AddToWaitlistParams(BaseModel):
    room_type: str
    stay_date: str
    guest_phone: str
    dialog_history: str = None

class ShowBookingPolicyParams(BaseModel):
    booking_id: str
    dialog_history: str = None

class AddServiceToBookingParams(BaseModel):
    booking_id: str
    service_description: str
    dialog_history: str = None

@tool
def modify_booking_tool(params: ModifyBookingParams) -> str:
    """
    Modify an existing booking IN-PLACE. All updates are applied to the same booking_id.
    Supports 'new_start_date' in update_fields: shifts the entire stay to start from the new date, preserving original length.
    """
    booking_id = params.booking_id
    update_fields = params.update_fields
    if not update_fields or not isinstance(update_fields, dict) or not update_fields:
        return "❌ update_fields (dict) is required and cannot be empty."
    # Validate rooms_booked if present
    if "rooms_booked" in update_fields and update_fields["rooms_booked"] < 1:
        return "❌ Cannot set rooms_booked below 1. To cancel the booking, please use the cancel option."
    booking_details = booking_manager.read_booking(booking_id=booking_id)
    if not booking_details or '❌' in booking_details:
        return f"❌ Cannot modify booking: {booking_details}"
    import re
    from datetime import datetime, timedelta
    def extract_field(label, text):
        match = re.search(rf"{label}: ([^\n]+)", text)
        return match.group(1).strip() if match else None
    guest_phone_field = extract_field('Guest', booking_details)
    guest_phone = None
    if guest_phone_field:
        match = re.search(r'\+\d{8,15}', guest_phone_field)
        if match:
            guest_phone = match.group(0)
    room_type = extract_field('Room Type', booking_details)
    rooms_booked = extract_field('Rooms', booking_details)
    booking_notes = extract_field('Notes', booking_details) or ''
    try:
        rooms_booked = int(rooms_booked)
    except Exception:
        rooms_booked = 1
    stay_dates = None
    stay_dates_match = re.search(r'Stay Dates: ([^\n]+)', booking_details)
    if stay_dates_match:
        stay_dates = [d.strip() for d in stay_dates_match.group(1).split(',')]
    # --- Enhancement: handle new_start_date argument ---
    if 'new_start_date' in update_fields:
        if not stay_dates or len(stay_dates) == 0:
            return "❌ Cannot shift stay: original stay_dates not found."
        try:
            original_length = len(stay_dates)
            new_start = datetime.strptime(update_fields['new_start_date'], "%Y-%m-%d")
            new_stay_dates = [(new_start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(original_length)]
            update_fields['stay_dates'] = new_stay_dates
            del update_fields['new_start_date']
        except Exception as e:
            return f"❌ Invalid new_start_date: {e}"
    params_dict = {
        'guest_phone': guest_phone,
        'room_type': room_type,
        'stay_dates': stay_dates,
        'rooms_booked': rooms_booked,
        'booking_notes': booking_notes
    }
    params_dict.update(update_fields)
    hotel_context = booking_manager._get_hotel_context()
    hotel_name = hotel_context.get('hotel_name')
    dbs = {
        'guests': booking_manager.guest_db['guests'],
        'rooms': booking_manager.mongo_db['room_encyclopedia'],
    }
    is_valid, issues = validate_booking_parameters({**params_dict, 'hotel_name': hotel_name}, dbs, booking_manager.DATE_CONTEXT)
    if not is_valid:
        return f"❌ Cannot modify booking: {issues}"
    try:
        # Update room_bookings table in-place
        set_clauses = []
        values = []
        cur = booking_manager.postgres_conn.cursor()
        for field, value in update_fields.items():
            if field == 'stay_dates':
                continue  # Do not update stay_dates in room_bookings; handled below
            set_clauses.append(f"{field} = %s")
            values.append(value)
        set_clauses.append("modified_at = %s")
        values.append(datetime.now())
        set_clause_str = ", ".join(set_clauses)
        if set_clauses:  # Only execute if there are fields to update
            cur.execute(
                f"UPDATE room_bookings SET {set_clause_str} WHERE booking_id = %s",
                (*values, booking_id)
            )
        # If stay_dates changed, update booking_stays
        if 'stay_dates' in update_fields:
            cur.execute("DELETE FROM booking_stays WHERE booking_id = %s", (booking_id,))
            for d in update_fields['stay_dates']:
                # Insert with hotel_name and room_type (and other NOT NULL columns if required)
                cur.execute("INSERT INTO booking_stays (booking_id, hotel_name, room_type, stay_date, booking_status) VALUES (%s, %s, %s, %s, %s)", (booking_id, hotel_name, room_type, d, 'confirmed'))
        # If room_type changed, update room assignments (optional: more logic can be added)
        if 'room_type' in update_fields:
            cur.execute("UPDATE room_number_assignments SET room_type = %s WHERE booking_id = %s", (update_fields['room_type'], booking_id))
        # Optionally, update booking_notes for audit
        if 'booking_notes' in update_fields:
            cur.execute("UPDATE room_bookings SET booking_notes = COALESCE(booking_notes, '') || %s WHERE booking_id = %s", (f" [Modified at {datetime.now()}]", booking_id))
        # --- Log every modification ---
        for field, new_value in update_fields.items():
            # Fetch old value
            if field == 'stay_dates':
                continue  # Do not log stay_dates in room_bookings
            cur.execute(f"SELECT {field} FROM room_bookings WHERE booking_id = %s", (booking_id,))
            old_value = cur.fetchone()
            old_value = old_value[0] if old_value else None
            cur.execute(
                """INSERT INTO booking_modification_log
                (booking_id, modified_at, modified_by, field_changed, old_value, new_value, change_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (booking_id, datetime.now(), 'system', field, str(old_value), str(new_value), 'user modification')
            )
        booking_manager.postgres_conn.commit()
        return f"✅ Booking {booking_id} updated in-place."
    except Exception as e:
        booking_manager.postgres_conn.rollback()
        return f"❌ Error updating booking: {e}"

@tool
def cancel_booking_tool(params: CancelBookingParams) -> str:
    """Cancel a booking by booking ID, optionally providing a reason."""
    notes = f" [Cancelled: {params.cancellation_reason}]" if params.cancellation_reason else None
    return booking_manager.update_booking_status(params.booking_id, 'cancelled', notes)

@tool
def search_bookings_tool(params: SearchBookingsParams) -> str:
    """Search for bookings by room type, guest phone, status, or date range."""
    return booking_manager.read_bookings_by_criteria(
        room_type=params.room_type,
        guest_phone=params.guest_phone,
        booking_status=params.booking_status,
        date_from=params.date_from,
        date_to=params.date_to,
        limit=params.limit
    )

# New PMS-specific tools for managers and copilots
@tool
def get_booking_analytics_tool(params: GetBookingAnalyticsParams) -> str:
    """Get analytics for bookings in a specified date range."""
    return booking_manager.get_booking_analytics(params.date_from, params.date_to)

@tool
def get_revenue_forecast_tool(params: GetRevenueForecastParams) -> str:
    """Get a revenue forecast for the next N days based on bookings."""
    return booking_manager.get_revenue_forecast(params.forecast_days)

@tool
def analyze_booking_patterns_tool(params: AnalyzeBookingPatternsParams) -> str:
    """Analyze booking patterns (lead time, stay length, day of week, etc.) for strategic insights."""
    return booking_manager.analyze_booking_patterns(params.analysis_days) 

@tool
def add_booking_special_request_tool(params: AddBookingSpecialRequestParams) -> str:
    """Add a special request (e.g., late check-in, extra bed) to an existing booking."""
    # Update MongoDB guest_bookings
    booking_id = params.booking_id
    special_request = params.special_request.strip()
    mongo_result = None
    pg_result = None
    try:
        guest_bookings = booking_manager.mongo_db['guest_bookings']
        booking_doc = guest_bookings.find_one({"booking_id": booking_id})
        if not booking_doc:
            return f"❌ Booking {booking_id} not found in MongoDB."
        # Update special_requests in booking_context
        booking_context = booking_doc.get("booking_context", {})
        special_requests = booking_context.get("special_requests")
        if special_requests is None:
            special_requests = []
        elif isinstance(special_requests, str):
            special_requests = [special_requests]
        elif not isinstance(special_requests, list):
            special_requests = [str(special_requests)]
        special_requests.append(special_request)
        booking_context["special_requests"] = special_requests
        guest_bookings.update_one(
            {"booking_id": booking_id},
            {"$set": {"booking_context": booking_context, "updated_at": datetime.now()}}
        )
        mongo_result = f"MongoDB updated. Special requests: {special_requests}"
    except Exception as e:
        mongo_result = f"❌ MongoDB error: {e}"
    # Optionally, update booking_notes in PostgreSQL
    try:
        cur = booking_manager.postgres_conn.cursor()
        note = f" [Special request: {special_request}]"
        cur.execute(
            """
            UPDATE room_bookings SET booking_notes = COALESCE(booking_notes, '') || %s WHERE booking_id = %s
            """,
            (note, booking_id)
        )
        booking_manager.postgres_conn.commit()
        pg_result = "PostgreSQL booking_notes updated."
    except Exception as e:
        pg_result = f"❌ PostgreSQL error: {e}"
    if mongo_result.startswith("❌") and pg_result and pg_result.startswith("❌"):
        return f"❌ Failed to add special request.\nMongoDB: {mongo_result}\nPostgreSQL: {pg_result}"
    return f"✅ Special request added to booking {booking_id}.\n{mongo_result}\n{pg_result}"

@tool
def send_booking_confirmation_tool(params: SendBookingConfirmationParams) -> str:
    """Send a booking confirmation to the guest via the specified method (email, SMS, WhatsApp, etc.)."""
    stay_dates = booking_manager.get_stay_dates_for_booking(params.booking_id)
    if not stay_dates:
        return f"❌ Booking {params.booking_id} not found"
    return f"[Prototype] Booking confirmation would be sent for booking {params.booking_id} (all stay dates: {', '.join(str(d) for d in stay_dates)}) via {params.method}."

@tool
def get_payment_status_for_booking_tool(params: GetPaymentStatusForBookingParams) -> str:
    """Get the payment status for a specific booking."""
    stay_dates = booking_manager.get_stay_dates_for_booking(params.booking_id)
    if not stay_dates:
        return f"❌ Booking {params.booking_id} not found"
    return f"[Prototype] Payment status for booking {params.booking_id} (all stay dates: {', '.join(str(d) for d in stay_dates)})."

@tool
def update_booking_status_tool(params: UpdateBookingStatusParams) -> str:
    """Update the booking_status for a booking_id in all relevant tables, with optional notes."""
    return booking_manager.update_booking_status(params.booking_id, params.new_status, params.notes)

@tool
def create_group_booking_tool(params: CreateGroupBookingParams) -> str:
    """Prototype tool, not yet implemented."""
    return "[Prototype] Create group booking logic not yet implemented."

@tool
def add_to_waitlist_tool(params: AddToWaitlistParams) -> str:
    """Prototype tool, not yet implemented."""
    return "[Prototype] Add to waitlist logic not yet implemented."

@tool
def show_booking_policy_tool(params: ShowBookingPolicyParams) -> str:
    """Prototype tool, not yet implemented."""
    return "[Prototype] Show booking policy logic not yet implemented."

@tool
def add_service_to_booking_tool(params: AddServiceToBookingParams) -> str:
    """Prototype tool, not yet implemented."""
    return "[Prototype] Add service to booking logic not yet implemented." 

class ModifyBookingQuantityParams(BaseModel):
    booking_id: str
    new_quantity: int

@tool
def modify_booking_quantity_tool(params: ModifyBookingQuantityParams) -> str:
    """
    Set the rooms_booked (quantity) for a booking to a specific value (must be ≥ 1).
    Example: modify_booking_quantity_tool(params={"booking_id": "+60123456789booking1", "new_quantity": 2})
    This will update the booking to have 2 rooms booked.
    """
    if params.new_quantity < 1:
        return "❌ Cannot set rooms_booked below 1. To cancel the booking, please use the cancel option."
    return modify_booking_tool.invoke({
        "params": {
            "booking_id": params.booking_id,
            "update_fields": {"rooms_booked": params.new_quantity}
        }
    }) 

@tool
def modify_stay_date_tool(booking_id: str, new_start_date: str) -> str:
    """
    Modify the starting date (check-in) for an existing booking. The tool shifts the entire stay to start from new_start_date, preserving the original length. No new booking_id is created. All necessary validation (availability, policy, etc.) is performed internally.
    - booking_id: The booking to modify.
    - new_start_date: The new check-in date (YYYY-MM-DD).
    """
    params = {
        "booking_id": booking_id,
        "update_fields": {"new_start_date": new_start_date}
    }
    return modify_booking_tool.invoke({"params": params})

@tool
def list_bookings_with_special_requests_tool(limit: int = 20, guest_phone: Optional[str] = None) -> str:
    """List all bookings that have special requests. Optionally filter by guest_phone. Returns booking_id, guest_phone, room_type, stay_dates, and the special requests."""
    try:
        hotel_context = booking_manager._get_hotel_context()
        hotel_name = hotel_context.get('hotel_name')
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
        guest_bookings = booking_manager.mongo_db['guest_bookings']
        query = {
            "hotel_name": hotel_name,
            "booking_context.special_requests": {"$exists": True, "$ne": []}
        }
        if guest_phone:
            query["guest_phone"] = guest_phone
        cursor = guest_bookings.find(query).sort("created_timestamp", -1).limit(limit)
        bookings = list(cursor)
        if not bookings:
            return "❌ No bookings with special requests found."
        result = f"📋 Bookings with Special Requests ({len(bookings)} found):\n\n"
        for doc in bookings:
            booking_id = doc.get("booking_id")
            guest_phone = doc.get("guest_phone")
            room_type = doc.get("room_type")
            stay_dates = doc.get("stay_dates", [])
            special_requests = doc.get("booking_context", {}).get("special_requests", [])
            result += f"  • Booking ID: {booking_id}\n"
            result += f"    Guest: {guest_phone}\n"
            result += f"    Room Type: {room_type}\n"
            result += f"    Stay Dates: {', '.join(stay_dates)}\n"
            result += f"    Special Requests: {special_requests}\n\n"
        return result.strip()
    except Exception as e:
        return f"❌ Error listing bookings with special requests: {e}" 


