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
import psycopg2.extensions
import pymongo
import os
import threading
from datetime import timedelta
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from langchain.tools import tool
from .guest_manager import guest_manager

# Load environment variables
load_dotenv()

class AgentRuleCompliantBookingManager:
    # Timezone logic centralized in timezone_utils; use hotel_now_local/hotel_local_today

    def __init__(self):
        """Initialize Agent-Rule Compliant Booking Manager"""
        self.postgres_conn = None
        self.postgres_lock = threading.RLock()
        self.mongo_client = None
        self.mongo_db = None  # For hotel/room data
        self.guest_db = None  # For guest profiles
        # Date context for AI agent (deferred). Strict policy: requires hotel timezone via JWT.
        # Not computed at init time to avoid non-hotel (UTC) timestamps.
        self.DATE_CONTEXT = {}
        
        self._connect_databases()
    
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

    def _ensure_postgres_connection(self) -> None:
        """Ensure PostgreSQL connection is alive; reconnect if closed."""
        try:
            if self.postgres_conn is None or getattr(self.postgres_conn, "closed", 1):
                self.postgres_conn = psycopg2.connect(os.getenv('DATABASE_URL'))
        except Exception as e:
            raise RuntimeError(f"Database reconnection failed: {e}")

    def _reconnect_postgres(self) -> None:
        """Replace a connection that is still aborted after rollback."""
        try:
            if self.postgres_conn is not None:
                self.postgres_conn.close()
        except Exception:
            pass
        self.postgres_conn = psycopg2.connect(os.getenv("DATABASE_URL"))

    def _clear_transaction(self) -> None:
        """Drop an aborted transaction so the next statement can run."""
        self._ensure_postgres_connection()
        conn = self.postgres_conn
        try:
            status = conn.get_transaction_status()
        except Exception:
            self._reconnect_postgres()
            return
        if status != psycopg2.extensions.TRANSACTION_STATUS_INERROR:
            return
        try:
            conn.rollback()
        except Exception:
            self._reconnect_postgres()
            return
        try:
            status = conn.get_transaction_status()
        except Exception:
            self._reconnect_postgres()
            return
        if status == psycopg2.extensions.TRANSACTION_STATUS_INERROR:
            self._reconnect_postgres()

    def _safe_rollback(self) -> None:
        """Rollback only if connection is open; ignore secondary errors."""
        try:
            if self.postgres_conn is not None and getattr(self.postgres_conn, "closed", 1) == 0:
                self.postgres_conn.rollback()
        except Exception:
            pass
        self._clear_transaction()

    def read_available_room_combinations(self, hotel_name: str) -> str:
        """List all available room combinations for the given hotel."""
        try:
            room_docs = list(self.mongo_db['room_encyclopedia'].find(
                {"hotel_name": hotel_name},
                {"hotel_name": 1, "room_type": 1, "_id": 0}
            ))
            if not room_docs:
                return f"❌ No room types found for {hotel_name}"
            combinations = set()
            for doc in room_docs:
                hname = doc.get("hotel_name")
                room_type = doc.get("room_type")
                if hname and room_type:
                    combinations.add((hname, room_type))
            result = f"📋 Available Room Combinations for {hotel_name} ({len(combinations)} found):\n"
            for hname, room_type in sorted(combinations):
                result += f"  • {hname} → {room_type}\n"
            result += f"\n💡 Usage: Use exact room_type in booking operations (hotel_name from context)"
            return result
        except Exception as e:
            return f"❌ Error: {e}"

    # Guest profile operations removed - use Guest Manager for all guest operations

    def get_stay_dates_for_booking(self, booking_id: str) -> list:
        """Return all stay_dates for a given booking_id, sorted."""
        cur = self.postgres_conn.cursor()
        cur.execute("SELECT stay_date FROM booking_stays WHERE booking_id = %s ORDER BY stay_date", (booking_id,))
        return [row[0] for row in cur.fetchall()]

    def read_availability(self, hotel_name: str, room_type: str, check_date: str, rooms_needed: int = 1) -> str:
        """Read room availability for a hotel and room type."""
        if not self.postgres_conn:
            return "❌ Database connection not available"
        try:
            # Ensure connection is alive (auto-reconnect on demand)
            self._ensure_postgres_connection()
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
            cur = self.postgres_conn.cursor()
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
            # Attempt one auto-recovery cycle on closed connection, then retry once
            try:
                self._safe_rollback()
                self._ensure_postgres_connection()
                cur = self.postgres_conn.cursor()
                cur.execute("""
                    SELECT COALESCE(SUM(rooms_booked), 0) FROM booking_stays
                    WHERE hotel_name = %s AND room_type = %s AND stay_date = %s AND booking_status = 'confirmed'
                """, (hotel_name, room_type, check_date))
                booked_rooms = cur.fetchone()[0]
                from booking_system.inventory_manager import InventoryManager
                inv = InventoryManager()
                inv_status = inv._validate_and_read_nightly_inventory(hotel_name, room_type, check_date)
                if not inv_status.get('exists'):
                    return f"❌ No inventory found for {room_type} at {hotel_name} on {check_date}"
                total_inventory = inv_status['data']['total_inventory']
                blocked_quantity = inv_status['data'].get('blocked_quantity', 0)
                active_inventory = total_inventory - blocked_quantity
                available_rooms = active_inventory - booked_rooms
                result = f"📊 Availability for {hotel_name} → {room_type} on {check_date}:\n"
                result += f"  • Total Inventory: {total_inventory}\n"
                result += f"  • Currently Booked: {booked_rooms}\n"
                result += f"  • Available: {available_rooms}\n"
                result += f"  • Requested: {rooms_needed}\n"
                result += f"  • Status: {'✅ AVAILABLE' if available_rooms >= rooms_needed else '❌ INSUFFICIENT'}\n"
                result += f"  • 🎯 Natural Keys: hotel_name + room_type + stay_date validated"
                return result
            except Exception as e2:
                return f"❌ Error reading availability: {e2}"

    def _assign_open_rooms(self, cur, hotel_name: str, room_type: str, booking_id: str, guest_phone: str, manager_name: str, stay_dates: list, rooms_booked: int) -> list:
        """Claim rooms that are free on every night of the stay. Same room is held for the whole stay."""
        nights = [str(d)[:10] for d in (stay_dates or [])]
        if not nights or rooms_booked < 1:
            return []
        night_keys = tuple(nights)
        cur.execute(
            """
            SELECT a.room_number
            FROM room_number_assignments a
            WHERE UPPER(a.hotel_name) = UPPER(%s)
              AND UPPER(COALESCE(a.room_type, '')) = UPPER(%s)
              AND a.stay_date IN %s
              AND a.booking_id IS NULL
              AND COALESCE(a.assignment_status, 'available') = 'available'
              AND NOT EXISTS (
                    SELECT 1
                    FROM room_stay rs
                    LEFT JOIN room_bookings rb
                      ON rb.booking_id = rs.booking_id AND UPPER(rb.hotel_name) = UPPER(rs.hotel_name)
                    WHERE UPPER(rs.hotel_name) = UPPER(a.hotel_name)
                      AND rs.room_number = a.room_number
                      AND rs.stay_date IN %s
                      AND rs.booking_id IS NOT NULL
                      AND rs.booking_id <> %s
                      AND COALESCE(rb.booking_status, 'confirmed') <> 'cancelled'
              )
            GROUP BY a.room_number
            HAVING COUNT(DISTINCT a.stay_date) = %s
            ORDER BY a.room_number
            LIMIT %s
            """,
            (hotel_name, room_type, night_keys, night_keys, booking_id, len(nights), rooms_booked)
        )
        chosen = [row[0] for row in (cur.fetchall() or [])]
        assigned = []
        for offset, room_number in enumerate(chosen):
            stay_index = offset + 1
            held_nights = []
            for stay_date in nights:
                cur.execute(
                    """
                    UPDATE room_number_assignments
                    SET room_type = %s,
                        booking_id = %s,
                        guest_phone = %s,
                        assignment_status = 'assigned',
                        updated_by = %s,
                        assigned_by = %s,
                        assigned_at = CURRENT_TIMESTAMP
                    WHERE UPPER(hotel_name) = UPPER(%s)
                      AND room_number = %s
                      AND stay_date = %s
                      AND booking_id IS NULL
                      AND COALESCE(assignment_status, 'available') = 'available'
                    """,
                    (room_type, booking_id, guest_phone, manager_name, manager_name, hotel_name, room_number, stay_date)
                )
                if cur.rowcount == 0:
                    break
                held_nights.append(stay_date)
            if len(held_nights) != len(nights):
                if held_nights:
                    cur.execute(
                        """
                        UPDATE room_number_assignments
                        SET booking_id = NULL,
                            guest_phone = NULL,
                            assignment_status = 'available',
                            updated_by = %s
                        WHERE UPPER(hotel_name) = UPPER(%s)
                          AND room_number = %s
                          AND stay_date IN %s
                          AND booking_id = %s
                        """,
                        (manager_name, hotel_name, room_number, tuple(held_nights), booking_id)
                    )
                continue
            for stay_date in nights:
                cur.execute(
                    """
                    UPDATE room_stay
                    SET room_number = %s,
                        stay_status = 'assigned',
                        assigned_by = %s,
                        assigned_at = CURRENT_TIMESTAMP,
                        assignment_id = %s
                    WHERE booking_id = %s AND stay_date = %s AND stay_index = %s
                    """,
                    (room_number, manager_name, f"{booking_id}#{room_number}", booking_id, stay_date, stay_index)
                )
                cur.execute(
                    """
                    UPDATE room_number_assignments
                    SET assignment_id = %s
                    WHERE UPPER(hotel_name) = UPPER(%s)
                      AND room_number = %s
                      AND stay_date = %s
                      AND booking_id = %s
                    """,
                    (f"{booking_id}#{room_number}", hotel_name, room_number, stay_date, booking_id)
                )
            assigned.append(str(room_number))
        return assigned

    def _ensure_booking_channel(self):
        """Add room_bookings.channel and label bookings made before this field as PMS."""
        cur = self.postgres_conn.cursor()
        cur.execute("ALTER TABLE room_stay ADD COLUMN IF NOT EXISTS assignment_id VARCHAR")
        cur.execute("ALTER TABLE room_number_assignments ADD COLUMN IF NOT EXISTS assignment_id VARCHAR")
        cur.execute("""
            UPDATE room_stay
            SET assignment_id = booking_id || '#' || room_number
            WHERE room_number IS NOT NULL AND BTRIM(room_number) <> ''
              AND COALESCE(assignment_id, '') <> booking_id || '#' || room_number
        """)
        cur.execute("""
            UPDATE room_stay
            SET assignment_id = booking_id || '#' || stay_index::text
            WHERE (room_number IS NULL OR BTRIM(room_number) = '')
              AND (assignment_id IS NULL OR assignment_id = '')
        """)
        cur.execute("""
            UPDATE room_number_assignments
            SET assignment_id = booking_id || '#' || room_number
            WHERE booking_id IS NOT NULL AND room_number IS NOT NULL AND BTRIM(room_number) <> ''
              AND COALESCE(assignment_id, '') <> booking_id || '#' || room_number
        """)
        cur.execute("ALTER TABLE room_bookings ADD COLUMN IF NOT EXISTS channel VARCHAR")
        cur.execute("ALTER TABLE room_bookings ADD COLUMN IF NOT EXISTS group_name VARCHAR")
        cur.execute("ALTER TABLE room_bookings ADD COLUMN IF NOT EXISTS rate_plan VARCHAR")
        cur.execute("UPDATE room_bookings SET channel = 'PMS' WHERE channel IS NULL OR channel = ''")
        self.postgres_conn.commit()

    def _channel_value(self, channel):
        value = str(channel or "").strip().upper()
        if value not in ("PMS", "AGODA", "BOOKING.COM", "CTRIP", "TRAVELOKA", "GROUP"):
            return ""
        return value

    def create_booking(self, hotel_name: str, manager_name: str, guest_phone: str, room_type: str, stay_dates: list, rooms_booked: int = 1, booking_notes: str = None, channel: str = None, group_name: str = None) -> str:
        """
        CREATE booking with full CRUD validation (AGENT-RULE COMPLIANT, ATOMIC).
        All parameters must be explicit, normalized, and pre-validated by the orchestrator.
        stay_dates: list of YYYY-MM-DD strings representing each night of the stay.
        manager_name: required for audit trail.
        """
        if not self.postgres_conn:
            return "❌ Database connection not available"
        channel_value = self._channel_value(channel)
        group_label = str(group_name or "").strip().upper()
        if not channel_value:
            return "❌ channel must be PMS, AGODA, BOOKING.COM, CTRIP, TRAVELOKA, or GROUP"
        try:
            self._ensure_booking_channel()
            # --- GUEST PROFILE VALIDATION (Single Source of Truth) ---
            guest_profile_result = guest_manager.read_guest_profile(guest_phone)
            if guest_profile_result.startswith("❌ No guest profile found"):
                return f"❌ No guest profile found for {guest_phone}. Kindly assist to register guest profile first."
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
                now_local = datetime.now(timezone.utc)
                room_doc = {
                    "hotel_name": hotel_name,
                    "room_type": room_type,
                    "is_physical_room": False,
                    "created_at": now_local,
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
                # Insert one row per room per night into room_night_assignments using room_index
                for i in range(rooms_booked):
                    cur.execute("""
                        INSERT INTO room_stay (booking_id, stay_date, stay_index, hotel_name, room_number, stay_status, assigned_by, assignment_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (booking_id, stay_date, i+1, hotel_name, None, 'pending', manager_name, f"{booking_id}#{i+1}"))
            # STEP 3: Extract guest details from validated guest_doc
            # STEP 4: READ pricing and calculate totals (sum each night's price)
            nightly_prices = []
            for sd in stay_dates:
                cur.execute(
                    """
                SELECT base_price FROM daily_pricing 
                WHERE hotel_name = %s AND room_type = %s AND stay_date = %s
                    """,
                    (hotel_name, room_type, sd)
                )
                rowp = cur.fetchone()
                if not rowp:
                    self._safe_rollback()
                    return f"❌ Pricing not found on \"{sd}\""
                try:
                    nightly_prices.append(float(rowp[0]))
                except Exception:
                    self._safe_rollback()
                    return f"❌ Pricing not found on \"{sd}\""
            if not nightly_prices:
                self._safe_rollback()
                return "❌ Pricing not found"
            subtotal = sum(nightly_prices) * rooms_booked
            # --- POLICY ENFORCEMENT: Fetch from booking_policies collection (status=active) ---
            booking_policy = self.mongo_db['booking_policies'].find_one({
                "hotel_name": hotel_name,
                "policy_type": {"$regex": "booking", "$options": "i"},
                "status": "active"
            })
            if not booking_policy:
                self._safe_rollback()
                return f"❌ No booking policy found for {hotel_name}. Use Policy Manager to create hotel policies first."
            payment_policy = self.mongo_db['booking_policies'].find_one({
                "hotel_name": hotel_name,
                "policy_type": {"$regex": "payment", "$options": "i"},
                "status": "active"
            })
            if not payment_policy:
                self._safe_rollback()
                return f"❌ No payment policy found for {hotel_name}. Use Policy Manager to create payment policies first."
            # Tax policy (optional but preferred)
            tax_policy = self.mongo_db['booking_policies'].find_one({
                "hotel_name": hotel_name,
                "policy_type": {"$regex": "tax", "$options": "i"},
                "status": "active"
            }) or {}
            # Extract policy details (with sensible defaults)
            # Payment window: prefer payment_window_days -> hours; fallback to payment_required_within_hours
            payment_window_days = payment_policy.get("payment_window_days")
            payment_required_within_hours = payment_policy.get("payment_required_within_hours")
            if payment_required_within_hours is None:
                payment_required_within_hours = int(payment_window_days) * 24 if payment_window_days is not None else 24
            # Payment threshold percent comes from payment policy (fallback 30)
            payment_threshold_percent = payment_policy.get("payment_threshold_percent", 30)
            # Taxes from tax policy: service charge %, vat %, tourism tax per night
            vat_pct = float(tax_policy.get("vat_percent", 0.0)) / 100.0
            svc_pct = float(tax_policy.get("service_charge_percent", 0.0)) / 100.0
            tourism_tax_per_night = float(tax_policy.get("tourism_tax_per_night", 0.0))
            service_charge = subtotal * svc_pct
            vat_base = subtotal + service_charge
            vat_amount = vat_base * vat_pct
            tourism_tax = tourism_tax_per_night * len(stay_dates) * rooms_booked
            taxes = service_charge + vat_amount + tourism_tax
            total_amount = subtotal + taxes
            payment_due_amount = total_amount * (payment_threshold_percent / 100)
            payment_deadline_hours = payment_required_within_hours
            # Compute payment deadline in hotel-local timezone for display
            now_local = datetime.now(timezone.utc)
            payment_deadline = (now_local + timedelta(hours=payment_deadline_hours)).isoformat(timespec='minutes')
            from .currency_utils import get_hotel_currency_str
            currency = get_hotel_currency_str(hotel_name) or ''
            # Build finalized pricing breakdown snapshot (immutable reference at creation time)
            nightly_lines = []
            for i, sd in enumerate(stay_dates):
                price_val = float(nightly_prices[i] if i < len(nightly_prices) else 0.0)
                nightly_lines.append({
                    "stay_date": sd,
                    "unit_price": price_val,
                    "rooms_booked": int(rooms_booked),
                    "line_total": float(price_val * int(rooms_booked))
                })
            pricing_breakdown = {
                "lines": nightly_lines,
                "subtotal": float(subtotal),
                "tax_policy_snapshot": {
                    "vat_percent": float(tax_policy.get("vat_percent", 0.0)),
                    "service_charge_percent": float(tax_policy.get("service_charge_percent", 0.0)),
                    "tourism_tax_per_night": float(tax_policy.get("tourism_tax_per_night", 0.0))
                },
                "service_charge_amount": float(service_charge),
                "vat_amount": float(vat_amount),
                "tourism_tax_amount": float(tourism_tax),
                "taxes_total": float(taxes),
                "total_amount": float(total_amount),
                "payment_policy_snapshot": {
                    "payment_threshold_percent": float(payment_threshold_percent),
                    "payment_required_within_hours": int(payment_required_within_hours)
                },
                "booking_policy_snapshot": {
                    "cancellation_policy": booking_policy.get("cancellation_policy"),
                    "refund_percentage": booking_policy.get("refund_percentage")
                },
                "currency": currency
            }
            # Compute number of nights
            nights = len(stay_dates)
            cur.execute("""
                INSERT INTO room_bookings 
                (booking_id, guest_phone, hotel_name, room_type, 
                 rooms_booked, booking_status, booking_reference, created_at, nights, channel, group_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s)
            """, (booking_id, guest_phone, hotel_name, room_type,
                  rooms_booked, 'confirmed', booking_id, nights, channel_value, group_label or None))
            # STEP 6: Insert into payment_tracking
            cur.execute("""
                INSERT INTO payment_tracking 
                (booking_id, guest_phone_number, total_amount, amount_paid, remaining_balance, payment_status, payment_method, payment_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (booking_id, guest_phone, total_amount, 0.0, total_amount, 'pending', None, None))
            # STEP 7: Insert into MongoDB
            created_by = {
                "manager_name": manager_name
            }
            guest_bookings = self.mongo_db['guest_bookings']
            now_local_ins = datetime.now(timezone.utc)
            guest_bookings.insert_one({
                "booking_id": booking_id,
                "guest_phone": guest_phone,
                "guest_name": guest_name,
                "guest_email": guest_email,
                "guest_loyalty_status": guest_loyalty,
                "hotel_name": hotel_name,
                "room_type": room_type,
                "stay_dates": stay_dates,
                "rooms_booked": rooms_booked,
                "total_amount": float(total_amount),
                "pricing_breakdown": pricing_breakdown,
                "booking_context": {
                    "booking_notes": booking_notes,
                    "channel": channel_value,
                    "group_name": group_label,
                    "created_via": "agent_rule_compliant_booking_manager",
                    "natural_keys_used": True
                },
                "created_timestamp": now_local_ins,
                "created_by": created_by,
                "updated_at": now_local_ins,
                "updated_by": created_by
            })
            assigned_rooms = self._assign_open_rooms(cur, hotel_name, room_type, booking_id, guest_phone, manager_name, stay_dates, rooms_booked)
            self._last_assigned_rooms = assigned_rooms
            self._last_booking_id = booking_id
            self.postgres_conn.commit()
            result = f"✅ Booking created successfully:\n"
            result += f"  • Booking ID: {booking_id}\n"
            result += f"  • Guest: {guest_phone} ({guest_name})\n"
            result += f"  • Email: {guest_email}\n"
            result += f"  • Hotel: {hotel_name}\n"
            result += f"  • Room Type: {room_type}\n"
            result += f"  • Stay Dates: {', '.join(stay_dates)}\n"
            result += f"  • Rooms: {rooms_booked}\n"
            room_line = ', '.join(assigned_rooms)
            if not room_line:
                room_line = 'none'
            result += f"  • Assigned Rooms: {room_line}\n"
            try:
                representative_rate = nightly_prices[0] if nightly_prices else 0.0
            except Exception:
                representative_rate = 0.0
            result += f"  • Room Rate: {currency} {representative_rate:.2f}/night\n"
            result += f"  • Subtotal: {currency} {subtotal:.2f}\n"
            # Render taxes breakdown when available
            if svc_pct or vat_pct or tourism_tax_per_night:
                if svc_pct:
                    result += f"  • Service Charge ({svc_pct*100:.0f}%): {currency} {service_charge:.2f}\n"
                if vat_pct:
                    result += f"  • VAT ({vat_pct*100:.0f}%): {currency} {vat_amount:.2f}\n"
                if tourism_tax_per_night:
                    result += f"  • Tourism Tax: {currency} {tourism_tax:.2f}\n"
                result += f"  • Taxes Total: {currency} {taxes:.2f}\n"
            else:
                result += f"  • Taxes: {currency} {taxes:.2f}\n"
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
            self.postgres_conn.rollback()
            return f"❌ Error creating booking: {e}"

    def read_booking(self, hotel_name: str, manager_name: str, booking_id: str = None, guest_phone: str = None, stay_date: str = None) -> str:
        """READ booking details with payment info (CRUD COMPLIANT, per-night, canonical)"""
        if not hotel_name or not manager_name:
            return "❌ hotel_name and manager_name are required."
        if not self.postgres_conn:
            return "❌ Database connection not available"
        try:
            cur = self.postgres_conn.cursor()
            # All queries must include hotel_name as a filter
            if booking_id:
                cur.execute("""
                    SELECT s.booking_id, s.hotel_name, s.room_type, s.stay_date, s.rooms_booked, s.booking_status,
                           b.guest_phone, b.created_at,
                           p.total_amount, p.amount_paid, p.remaining_balance, p.payment_status, p.payment_method, p.payment_date
                    FROM booking_stays s
                    JOIN room_bookings b ON s.booking_id = b.booking_id
                    LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                    WHERE s.hotel_name = %s AND s.booking_id = %s
                    ORDER BY s.stay_date
                """, (hotel_name, booking_id))
                bookings = cur.fetchall()
                if not bookings:
                    return f"❌ No booking found with ID: {booking_id} at hotel: {hotel_name}"
            elif guest_phone:
                cur.execute("""
                    SELECT s.booking_id, s.hotel_name, s.room_type, s.stay_date, s.rooms_booked, s.booking_status,
                           b.guest_phone, b.created_at,
                           p.total_amount, p.amount_paid, p.remaining_balance, p.payment_status, p.payment_method, p.payment_date
                    FROM booking_stays s
                    JOIN room_bookings b ON s.booking_id = b.booking_id
                    LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                    WHERE s.hotel_name = %s AND b.guest_phone = %s
                    ORDER BY s.stay_date DESC
                """, (hotel_name, guest_phone))
                bookings = cur.fetchall()
                if not bookings:
                    return f"❌ No bookings found for guest: {guest_phone} at hotel: {hotel_name}"
            elif stay_date:
                cur.execute("""
                    SELECT s.booking_id, s.hotel_name, s.room_type, s.stay_date, s.rooms_booked, s.booking_status,
                           b.guest_phone, b.created_at,
                           p.total_amount, p.amount_paid, p.remaining_balance, p.payment_status, p.payment_method, p.payment_date
                    FROM booking_stays s
                    JOIN room_bookings b ON s.booking_id = b.booking_id
                    LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                    WHERE s.hotel_name = %s AND s.stay_date = %s
                    ORDER BY s.stay_date
                """, (hotel_name, stay_date))
                bookings = cur.fetchall()
                if not bookings:
                    return f"❌ No bookings found for stay date: {stay_date} at hotel: {hotel_name}"
            else:
                cur.execute("""
                    SELECT s.booking_id, s.hotel_name, s.room_type, s.stay_date, s.rooms_booked, s.booking_status,
                           b.guest_phone, b.created_at,
                           p.total_amount, p.amount_paid, p.remaining_balance, p.payment_status, p.payment_method, p.payment_date
                    FROM booking_stays s
                    JOIN room_bookings b ON s.booking_id = b.booking_id
                    LEFT JOIN payment_tracking p ON b.booking_id = p.booking_id
                    WHERE s.hotel_name = %s
                    ORDER BY s.stay_date DESC
                    LIMIT 10
                """, (hotel_name,))
                bookings = cur.fetchall()
                if not bookings:
                    return f"❌ No bookings found in system for hotel: {hotel_name}"
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
                guest_doc = self.guest_db['guests'].find_one({"guest_phone": guest_phone, "status": "active", "hotel_name": hotel_name})
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

    def read_bookings_by_criteria(self, hotel_name: str, manager_name: str, room_type: str = None, 
                                 guest_phone: str = None, booking_status: str = None,
                                 date_from: str = None, date_to: str = None, limit: int = 20) -> str:
        """READ bookings by multiple criteria with natural key validation (hotel_name from context, per-night, canonical)"""
        if not hotel_name or not manager_name:
            return "❌ hotel_name and manager_name are required."
        if not self.postgres_conn:
            return "❌ Database connection not available"
        try:
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
                guest_doc = self.guest_db['guests'].find_one({"guest_phone": guest_phone, "status": "active", "hotel_name": hotel_name})
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

    def read_recent_bookings(self, hotel_name: str, limit: int = 50) -> dict:
        """READ recent bookings for a hotel from room_bookings and booking_stays."""
        try:
            hn = (hotel_name or "").upper()
            self._ensure_postgres_connection()
            self._safe_rollback()
            bookings = []
            cur = self.postgres_conn.cursor()
            cur.execute(
                """
                SELECT b.booking_id,
                       b.guest_phone,
                       b.room_type,
                       b.booking_status,
                       b.channel,
                       b.group_name,
                       b.created_at,
                       MIN(s.stay_date) AS check_in,
                       MAX(s.stay_date) AS last_night,
                       MAX(s.rooms_booked) AS rooms_booked,
                       COALESCE(MAX(p.total_amount), 0) AS total_amount
                FROM room_bookings b
                LEFT JOIN booking_stays s
                  ON s.booking_id = b.booking_id AND s.hotel_name = b.hotel_name
                LEFT JOIN payment_tracking p ON p.booking_id = b.booking_id
                WHERE b.hotel_name = %s
                GROUP BY b.booking_id, b.guest_phone, b.room_type, b.booking_status, b.channel, b.group_name, b.created_at
                ORDER BY b.created_at DESC
                LIMIT %s
                """,
                (hn, int(limit)),
            )
            rows = cur.fetchall()
            self._safe_rollback()
            names = {}
            booking_names = {}
            try:
                phones = [str(row[1] or "") for row in rows]
                if phones:
                    for doc in self.mongo_db["guests"].find({"guest_phone": {"$in": phones}}, {"guest_phone": 1, "guest_name": 1}):
                        names[str(doc.get("guest_phone") or "")] = str(doc.get("guest_name") or "")
                booking_ids = [str(row[0] or "") for row in rows if row[0]]
                if booking_ids:
                    for doc in self.mongo_db["guest_bookings"].find({"hotel_name": hn, "booking_id": {"$in": booking_ids}}, {"_id": 0, "booking_id": 1, "guest_name": 1}):
                        booking_names[str(doc.get("booking_id") or "")] = str(doc.get("guest_name") or "")
            except Exception:
                names = {}
                booking_names = {}
            from datetime import timedelta
            for bid, phone, room_type, status, channel, group_name, created_at, check_in, last_night, rooms_booked, amount in rows:
                try:
                    check_out = (last_night + timedelta(days=1)).isoformat() if last_night else ""
                except Exception:
                    check_out = str(last_night or "")
                guest_phone = str(phone or "")
                bookings.append({
                    "id": str(bid),
                    "guest_name": names.get(guest_phone) or booking_names.get(str(bid)) or guest_phone,
                    "guest_phone": guest_phone,
                    "room_number": str(room_type or ""),
                    "check_in": str(check_in or ""),
                    "check_out": check_out,
                    "status": str(status or ""),
                    "channel": str(channel or ""),
                    "group_name": str(group_name or ""),
                    "amount": float(amount or 0.0),
                    "rooms_booked": int(rooms_booked or 1),
                    "timestamp": str(created_at or ""),
                })
            return {"bookings": bookings}
        except Exception as e:
            return {"bookings": [], "error": str(e)}

    def read_hotel_analytics(self, hotel_name: str, days: int = 30) -> dict:
        """READ occupancy and room revenue from booking_stays and daily_pricing."""
        from datetime import date, timedelta
        hn = (hotel_name or "").upper()
        span = max(1, int(days or 30))
        try:
            from booking_system.timezone_utils import hotel_local_today
            today = hotel_local_today(hn)
        except Exception:
            today = date.today()
        start = today - timedelta(days=span - 1)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span - 1)
        self._ensure_postgres_connection()
        self._safe_rollback()
        cur = self.postgres_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM room_readiness_status WHERE hotel_name = %s", (hn,))
        room_count = int((cur.fetchone() or [0])[0] or 0)

        def pull(a, b):
            cur.execute(
                """
                SELECT s.stay_date, s.room_type, COALESCE(s.rooms_booked, 0), COALESCE(d.base_price, 0), b.guest_phone
                FROM booking_stays s
                JOIN room_bookings b ON b.booking_id = s.booking_id AND b.hotel_name = s.hotel_name
                LEFT JOIN daily_pricing d
                  ON d.hotel_name = s.hotel_name AND d.room_type = s.room_type AND d.stay_date = s.stay_date
                WHERE s.hotel_name = %s
                  AND LOWER(COALESCE(s.booking_status, '')) = 'confirmed'
                  AND s.stay_date BETWEEN %s AND %s
                """,
                (hn, a, b),
            )
            return cur.fetchall() or []

        current = pull(start, today)
        previous = pull(prev_start, prev_end)
        self._safe_rollback()

        def pack(rows, a, b):
            by_day = {}
            by_type = {}
            phones = set()
            d = a
            while d <= b:
                by_day[d.isoformat()] = {"rooms": 0, "revenue": 0.0}
                d = d + timedelta(days=1)
            for stay_date, room_type, rooms_booked, base_price, guest_phone in rows:
                key = stay_date.isoformat() if hasattr(stay_date, "isoformat") else str(stay_date)
                rooms = int(rooms_booked or 0)
                revenue = float(base_price or 0) * rooms
                bucket = by_day.setdefault(key, {"rooms": 0, "revenue": 0.0})
                bucket["rooms"] += rooms
                bucket["revenue"] += revenue
                rt = str(room_type or "")
                type_bucket = by_type.setdefault(rt, {"revenue": 0.0, "bookings": 0})
                type_bucket["revenue"] += revenue
                type_bucket["bookings"] += rooms
                if guest_phone:
                    phones.add(str(guest_phone))
            nights = sum(v["rooms"] for v in by_day.values())
            revenue = sum(v["revenue"] for v in by_day.values())
            denom = room_count * max(1, len(by_day))
            occupancy = (nights / denom * 100.0) if denom else 0.0
            adr = (revenue / nights) if nights else 0.0
            return by_day, by_type, phones, nights, revenue, occupancy, adr

        cur_days, cur_types, phones, nights, revenue, occupancy, adr = pack(current, start, today)
        _, _, _, prev_nights, prev_revenue, prev_occ, prev_adr = pack(previous, prev_start, prev_end)

        def delta(now, before):
            if not before:
                return 0.0
            return ((now - before) / before) * 100.0

        names = {}
        try:
            if phones:
                for doc in self.mongo_db["guests"].find(
                    {"guest_phone": {"$in": list(phones)}},
                    {"guest_phone": 1, "nationality": 1},
                ):
                    names[str(doc.get("guest_phone") or "")] = str(doc.get("nationality") or "")
        except Exception:
            names = {}
        countries = {}
        for phone in phones:
            country = names.get(phone) or "Unknown"
            countries[country] = countries.get(country, 0) + 1
        guest_total = sum(countries.values()) or 1
        demographics = [
            {"country": k, "guests": v, "percentage": round(v / guest_total * 100, 1)}
            for k, v in sorted(countries.items(), key=lambda kv: kv[1], reverse=True)
        ]
        return {
            "kpis": {
                "totalRevenue": round(revenue, 2),
                "occupancyRate": round(occupancy, 1),
                "adr": round(adr, 2),
                "guestSatisfaction": None,
                "roomCount": room_count,
                "occupiedNights": nights,
                "changes": {
                    "revenue": round(delta(revenue, prev_revenue), 1),
                    "occupancy": round(delta(occupancy, prev_occ), 1),
                    "adr": round(delta(adr, prev_adr), 1),
                    "satisfaction": 0,
                },
            },
            "revenue": [{"date": k, "revenue": round(v["revenue"], 2)} for k, v in cur_days.items()],
            "occupancy": [
                {"date": k, "occupancy": round((v["rooms"] / room_count * 100.0) if room_count else 0.0, 1)}
                for k, v in cur_days.items()
            ],
            "roomTypes": [
                {"type": k or "Unspecified", "revenue": round(v["revenue"], 2), "bookings": v["bookings"]}
                for k, v in sorted(cur_types.items(), key=lambda kv: kv[1]["revenue"], reverse=True)
            ],
            "bookingSources": [{"source": "Hotel", "bookings": len(phones), "percentage": 100}],
            "demographics": demographics,
        }

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

    def update_booking_status(self, hotel_name: str, booking_id: str, new_status: str, notes: str = None, manager_name: str = None) -> str:
        """Update the status of a booking, enforcing hotel_name validation in all database operations."""
        try:
            cur = self.postgres_conn.cursor()
            cur.execute(
                """
                UPDATE room_bookings SET booking_status = %s, booking_notes = COALESCE(booking_notes, '') || %s, updated_at = CURRENT_TIMESTAMP, updated_by = %s
                WHERE hotel_name = %s AND booking_id = %s
                """,
                (new_status, f" [Status update: {new_status}]" if notes is None else f" [Status update: {new_status}, Notes: {notes}]", manager_name or '', hotel_name, booking_id)
            )
            # Also update booking_stays status
            cur.execute(
                """
                UPDATE booking_stays SET booking_status = %s
                WHERE hotel_name = %s AND booking_id = %s
                """,
                (new_status, hotel_name, booking_id)
            )
            self.postgres_conn.commit()
            return f"✅ Booking {booking_id} at {hotel_name} status updated to {new_status}."
        except Exception as e:
            self.postgres_conn.rollback()
            return f"❌ Error updating booking status: {e}"


    def get_booking_analytics(self, hotel_name: str, date_from: str = None, date_to: str = None) -> str:
        """Get comprehensive booking analytics for management reporting (per-night, canonical)"""
        try:
            self._ensure_postgres_connection()
            from .currency_utils import get_hotel_currency_str
            currency = get_hotel_currency_str(hotel_name) or ''
            if not hotel_name:
                return "❌ No hotel context found. Please ensure hotel_name is provided."
            now_local = datetime.now(timezone.utc)
            if not date_from:
                date_from = (now_local - timedelta(days=30)).date().isoformat()
            if not date_to:
                date_to = now_local.date().isoformat()
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
            self._safe_rollback()
            return f"❌ Error generating booking analytics: {e}"

    def get_revenue_forecast(self, hotel_name: str, forecast_days: int = 30) -> str:
        """Get revenue forecast based on historical data and current bookings (per-night, canonical)"""
        try:
            self._ensure_postgres_connection()
            from .currency_utils import get_hotel_currency_str
            currency = get_hotel_currency_str(hotel_name) or ''
            if not hotel_name:
                return "❌ No hotel context found. Please ensure hotel_name is provided."
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
            now_local = datetime.now(timezone.utc)
            forecast_start = now_local.date().isoformat()
            forecast_end = (now_local + timedelta(days=forecast_days)).date().isoformat()
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
            self._safe_rollback()
            return f"❌ Error generating revenue forecast: {e}"

    def analyze_booking_patterns(self, hotel_name: str, analysis_days: int = 90) -> str:
        """Analyze booking patterns for strategic insights (per-night, canonical)"""
        try:
            self._ensure_postgres_connection()
            if not hotel_name:
                return "❌ No hotel context found. Please ensure hotel_name is provided."
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
            self._safe_rollback()
            return f"❌ Error analyzing booking patterns: {e}"

    # Legacy wrapper removed; use modify_booking()

    # Legacy wrapper removed; use modify_booking()

    def create_group_booking(self, hotel_name: str, manager_name: str, guest_phone: str, stay_dates: list, lines: list, booking_notes: str = None) -> str:
        """CREATE one PMS booking per room type."""
        if not isinstance(lines, list) or not lines:
            return "❌ At least one room type is required"
        created = []
        for line in lines:
            room_type = str((line or {}).get("room_type") or "").strip().upper()
            try:
                rooms_booked = int((line or {}).get("rooms_booked") or 0)
            except Exception:
                rooms_booked = 0
            if not room_type or rooms_booked < 1:
                for bid in created:
                    self.cancel_booking(hotel_name, bid, manager_name)
                return "❌ Each line needs a room type and at least one room"
            result = self.create_booking(hotel_name, manager_name, guest_phone, room_type, stay_dates, rooms_booked, booking_notes, "PMS")
            if isinstance(result, str) and result.startswith("❌"):
                for bid in created:
                    self.cancel_booking(hotel_name, bid, manager_name)
                return result
            created.append(getattr(self, "_last_booking_id", "") or "")
        self._last_group_booking_ids = [bid for bid in created if bid]
        return "✅ Booking created:\n" + "\n".join("  • " + bid for bid in self._last_group_booking_ids)

    def modify_booking(self, hotel_name: str, booking_id: str, manager_name: str, new_rooms_booked: int = None, new_stay_dates: list = None, booking_notes: str = None, new_room_type: str = None, channel: str = None) -> str:
        """Combined modify function: updates rooms_booked and/or stay_dates and/or room_type using create-booking validations, preserving booking_id."""
        try:
            cur = self.postgres_conn.cursor()
            # Load current snapshot
            cur.execute("SELECT room_type, rooms_booked FROM booking_stays WHERE hotel_name = %s AND booking_id = %s LIMIT 1", (hotel_name, booking_id))
            row = cur.fetchone()
            if not row:
                return f"❌ No booking_stays found for booking_id {booking_id} at hotel {hotel_name}"
            room_type, current_rooms = row
            cur.execute("SELECT stay_date FROM booking_stays WHERE hotel_name = %s AND booking_id = %s ORDER BY stay_date", (hotel_name, booking_id))
            current_stays = [r[0] for r in cur.fetchall()]
            effective_rooms = int(new_rooms_booked) if new_rooms_booked is not None else int(current_rooms)
            effective_stays = list(new_stay_dates) if isinstance(new_stay_dates, list) and new_stay_dates else current_stays
            effective_room_type = str(new_room_type or '').upper() if new_room_type else room_type
            return self._rebuild_booking_same_id(hotel_name, manager_name, booking_id, effective_room_type, effective_stays, effective_rooms, booking_notes, channel)
        except Exception as e:
            self.postgres_conn.rollback()
            return f"❌ Error modifying booking: {e}"

    def _rebuild_booking_same_id(self, hotel_name: str, manager_name: str, booking_id: str, room_type: str, stay_dates: list, rooms_booked: int, booking_notes: str = None, channel: str = None) -> str:
        """Internal: Rebuild booking rows and totals for an existing booking_id using create_booking validations (inventory, pricing, policies)."""
        if not self.postgres_conn:
            return "❌ Database connection not available"
        try:
            from booking_system.inventory_manager import InventoryManager
            inv = InventoryManager()
            for sd in stay_dates:
                inv_status = inv._validate_and_read_nightly_inventory(hotel_name, room_type, sd)
                if not inv_status.get('exists') or (inv_status['data']['total_inventory'] - inv_status['data'].get('blocked_quantity', 0)) < rooms_booked:
                    return f"❌ Insufficient inventory for {room_type} at {hotel_name} on {sd}"
            cur = self.postgres_conn.cursor()
            self._ensure_booking_channel()
            nightly_prices = []
            for sd in stay_dates:
                cur.execute("SELECT base_price FROM daily_pricing WHERE hotel_name = %s AND room_type = %s AND stay_date = %s", (hotel_name, room_type, sd))
                rowp = cur.fetchone()
                if not rowp:
                    return f"❌ Pricing not found on \"{sd}\""
                try:
                    nightly_prices.append(float(rowp[0]))
                except Exception:
                    return f"❌ Pricing not found on \"{sd}\""
            if not nightly_prices:
                return "❌ Pricing not found"
            subtotal = sum(nightly_prices) * rooms_booked
            booking_policy = self.mongo_db['booking_policies'].find_one({"hotel_name": hotel_name, "policy_type": {"$regex": "booking", "$options": "i"}, "status": "active"})
            if not booking_policy:
                return f"❌ No booking policy found for {hotel_name}. Use Policy Manager to create hotel policies first."
            payment_policy = self.mongo_db['booking_policies'].find_one({"hotel_name": hotel_name, "policy_type": {"$regex": "payment", "$options": "i"}, "status": "active"})
            if not payment_policy:
                return f"❌ No payment policy found for {hotel_name}. Use Policy Manager to create payment policies first."
            tax_policy = self.mongo_db['booking_policies'].find_one({"hotel_name": hotel_name, "policy_type": {"$regex": "tax", "$options": "i"}, "status": "active"}) or {}
            vat_pct = float(tax_policy.get("vat_percent", 0.0)) / 100.0
            svc_pct = float(tax_policy.get("service_charge_percent", 0.0)) / 100.0
            tourism_tax_per_night = float(tax_policy.get("tourism_tax_per_night", 0.0))
            service_charge = subtotal * svc_pct
            vat_base = subtotal + service_charge
            vat_amount = vat_base * vat_pct
            tourism_tax = tourism_tax_per_night * len(stay_dates) * rooms_booked
            taxes = service_charge + vat_amount + tourism_tax
            total_amount = subtotal + taxes
            # Release any existing assignments in room_number_assignments for this booking
            try:
                cur.execute(
                """
                    UPDATE room_number_assignments
                    SET booking_id = NULL,
                        guest_phone = NULL,
                        assignment_status = 'available',
                        updated_by = %s
                    WHERE booking_id = %s
                    """,
                    (manager_name, booking_id)
                )
            except Exception:
                pass
            # FULL DELETE then RECREATE (preserving booking_id and guest_phone)
            # 1) Capture existing guest_phone before full delete
            cur.execute("SELECT guest_phone, channel, group_name FROM room_bookings WHERE hotel_name = %s AND booking_id = %s", (hotel_name, booking_id))
            gp_row = cur.fetchone()
            existing_guest_phone = str(gp_row[0]) if gp_row and gp_row[0] else None
            kept_channel = self._channel_value(channel) or (str(gp_row[1]) if gp_row and gp_row[1] else "")
            kept_group = str(gp_row[2]) if gp_row and gp_row[2] else None
            if kept_channel not in ("PMS", "AGODA", "BOOKING.COM", "CTRIP", "TRAVELOKA", "GROUP"):
                return "❌ channel must be PMS, AGODA, BOOKING.COM, CTRIP, TRAVELOKA, or GROUP"
            # 2) Mark and delete dependent rows
            cur.execute("UPDATE booking_stays SET booking_status = 'modified' WHERE hotel_name = %s AND booking_id = %s", (hotel_name, booking_id))
            cur.execute("DELETE FROM room_stay WHERE booking_id = %s", (booking_id,))
            cur.execute("DELETE FROM booking_stays WHERE hotel_name = %s AND booking_id = %s", (hotel_name, booking_id))
            # Preserve payment_tracking; do not delete so amount_paid can be retained
            # 3) Recreate primary booking row (keep same booking_id, guest_phone)
            cur.execute("DELETE FROM room_bookings WHERE hotel_name = %s AND booking_id = %s", (hotel_name, booking_id))
            cur.execute(
                """
                INSERT INTO room_bookings (booking_id, guest_phone, hotel_name, room_type, rooms_booked, booking_status, booking_reference, created_at, nights, channel, group_name)
                VALUES (%s, %s, %s, %s, %s, 'confirmed', %s, CURRENT_TIMESTAMP, %s, %s, %s)
                """,
                (booking_id, existing_guest_phone, hotel_name, room_type, rooms_booked, booking_id, len(stay_dates), kept_channel, kept_group)
            )
            # Insert fresh booking_stays and room_stay
            for sd in stay_dates:
                cur.execute("INSERT INTO booking_stays (hotel_name, room_type, stay_date, booking_id, rooms_booked, booking_status) VALUES (%s, %s, %s, %s, %s, %s)", (hotel_name, room_type, sd, booking_id, rooms_booked, 'confirmed'))
                for i in range(rooms_booked):
                    cur.execute("INSERT INTO room_stay (booking_id, stay_date, stay_index, hotel_name, room_number, stay_status, assigned_by, assignment_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (booking_id, sd, i+1, hotel_name, None, 'pending', manager_name, f"{booking_id}#{i+1}"))
            # Update summaries
            cur.execute("UPDATE room_bookings SET rooms_booked = %s, nights = %s, booking_status = 'confirmed', updated_at = CURRENT_TIMESTAMP, updated_by = %s WHERE hotel_name = %s AND booking_id = %s", (rooms_booked, len(stay_dates), manager_name, hotel_name, booking_id))
            # Update payment tracking
            # Copy over existing amount_paid if exists
            cur.execute("SELECT amount_paid FROM payment_tracking WHERE booking_id = %s", (booking_id,))
            rowpay = cur.fetchone(); existing_amount_paid = float((rowpay or [0])[0] or 0)
            remaining = max(total_amount - existing_amount_paid, 0)
            status = 'completed' if remaining <= 0.01 and total_amount > 0 else ('partial' if existing_amount_paid > 0 else 'pending')
            cur.execute("UPDATE payment_tracking SET total_amount = %s, remaining_balance = %s, payment_status = %s WHERE booking_id = %s", (total_amount, remaining, status, booking_id))
            if cur.rowcount == 0:
                cur.execute(
                    """
                    INSERT INTO payment_tracking (booking_id, guest_phone_number, total_amount, amount_paid, remaining_balance, payment_status, payment_method, payment_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (booking_id, existing_guest_phone, total_amount, existing_amount_paid, remaining, status, None, None)
                )
            # Update MongoDB
            try:
                # Rebuild pricing breakdown snapshot on modification as well
                # Recompute nightly prices already done above; reuse for snapshot
                nightly_lines = []
                for i, sd in enumerate(stay_dates):
                    price_val = float(nightly_prices[i] if i < len(nightly_prices) else 0.0)
                    nightly_lines.append({
                        "stay_date": sd,
                        "unit_price": price_val,
                        "rooms_booked": int(rooms_booked),
                        "line_total": float(price_val * int(rooms_booked))
                    })
                pricing_breakdown = {
                    "lines": nightly_lines,
                    "subtotal": float(subtotal),
                    "tax_policy_snapshot": {
                        "vat_percent": float(vat_pct * 100.0),
                        "service_charge_percent": float(svc_pct * 100.0),
                        "tourism_tax_per_night": float(tourism_tax_per_night)
                    },
                    "service_charge_amount": float(service_charge),
                    "vat_amount": float(vat_amount),
                    "tourism_tax_amount": float(tourism_tax),
                    "taxes_total": float(taxes),
                    "total_amount": float(total_amount)
                }
                self.mongo_db['guest_bookings'].update_one(
                    {"hotel_name": hotel_name, "booking_id": booking_id},
                    {"$set": {"stay_dates": stay_dates, "rooms_booked": int(rooms_booked), "total_amount": float(total_amount), "pricing_breakdown": pricing_breakdown, "booking_context.booking_notes": booking_notes if booking_notes is not None else "", "booking_context.channel": kept_channel, "updated_at": datetime.now(timezone.utc), "updated_by": {"manager_name": manager_name}}}
                )
            except Exception:
                pass
            self.postgres_conn.commit()
            return f"✅ Booking {booking_id} rebuilt with {rooms_booked} room(s) and {len(stay_dates)} night(s)."
        except Exception as e:
            self.postgres_conn.rollback()
            return f"❌ Error rebuilding booking: {e}"

    def add_booking_special_request(self, hotel_name: str, booking_id: str, special_request: str, manager_name: str) -> str:
        """Add a special request to a booking, enforcing hotel_name validation in all database operations."""
        special_request = special_request.strip()
        mongo_success = False
        pg_success = False
        mongo_result = None
        pg_result = None
        compensation_log = []
        try:
            guest_bookings = self.mongo_db['guest_bookings']
            booking_doc = guest_bookings.find_one({"hotel_name": hotel_name, "booking_id": booking_id})
            if not booking_doc:
                return f"❌ Booking {booking_id} not found in MongoDB for hotel {hotel_name}."
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
                {"hotel_name": hotel_name, "booking_id": booking_id},
                {"$set": {"booking_context": booking_context, "updated_at": datetime.now(timezone.utc), "updated_by": {"manager_name": manager_name}}}
            )
            mongo_result = f"MongoDB updated. Special requests: {special_requests}"
            mongo_success = True
        except Exception as e:
            mongo_result = f"❌ MongoDB error: {e}"
            compensation_log.append({"db": "MongoDB", "hotel_name": hotel_name, "booking_id": booking_id, "special_request": special_request, "error": str(e), "action": "retry"})
        try:
            cur = self.postgres_conn.cursor()
            note = f" [Special request: {special_request}]"
            cur.execute(
                """
                UPDATE room_bookings SET booking_notes = COALESCE(booking_notes, '') || %s WHERE hotel_name = %s AND booking_id = %s
                """,
                (note, hotel_name, booking_id)
            )
            self.postgres_conn.commit()
            pg_result = "PostgreSQL booking_notes updated."
            pg_success = True
        except Exception as e:
            pg_result = f"❌ PostgreSQL error: {e}"
            compensation_log.append({"db": "PostgreSQL", "hotel_name": hotel_name, "booking_id": booking_id, "special_request": special_request, "error": str(e), "action": "retry"})
        # Compensation logic
        if mongo_success and not pg_success:
            self._log_compensation(compensation_log)
            return f"❌ Partial failure: MongoDB succeeded, PostgreSQL failed. Support has been notified.\n{mongo_result}\n{pg_result}"
        elif pg_success and not mongo_success:
            self._log_compensation(compensation_log)
            return f"❌ Partial failure: PostgreSQL succeeded, MongoDB failed. Support has been notified.\n{mongo_result}\n{pg_result}"
        elif not mongo_success and not pg_success:
            self._log_compensation(compensation_log)
            return f"❌ Failed to add special request. Both DBs failed. Support has been notified.\n{mongo_result}\n{pg_result}"
        return f"✅ Special request added to booking {booking_id} at {hotel_name}.\n{mongo_result}\n{pg_result}"

    def _log_compensation(self, compensation_log: list):
        """Log compensation actions for later retry or alerting."""
        import json
        with open("compensation_queue.log", "a") as f:
            for entry in compensation_log:
                f.write(json.dumps(entry) + "\n")

    def send_booking_confirmation(self, booking_id: str, method: str = 'email') -> str:
        # Prototype stub for modularity
        stay_dates = self.get_stay_dates_for_booking(booking_id)
        if not stay_dates:
            return f"❌ Booking {booking_id} not found"
        return f"[Prototype] Booking confirmation would be sent for booking {booking_id} (all stay dates: {', '.join(str(d) for d in stay_dates)}) via {method}."

    def get_payment_status_for_booking(self, hotel_name: str, booking_id: str) -> str:
        """Get payment status for a booking, enforcing hotel_name validation in all database operations."""
        try:
            cur = self.postgres_conn.cursor()
            # Prefer payment_tracking table; fall back to deriving from transactions if needed
            cur.execute(
                """
                SELECT pt.payment_status, pt.amount_paid, pt.remaining_balance
                FROM payment_tracking pt
                JOIN room_bookings rb ON rb.booking_id = pt.booking_id
                WHERE rb.hotel_name = %s AND pt.booking_id = %s
                """,
                (hotel_name, booking_id)
            )
            row = cur.fetchone()
            if row:
                status, paid, balance = row[0], float(row[1] or 0), float(row[2] or 0)
                return f"💳 Payment status for booking {booking_id} at {hotel_name}: {status} (paid {paid:.2f}, balance {balance:.2f})"
            # Fallback: derive using room_bookings total_amount and payment_ledger (per-transaction)
            cur.execute(
                """
                SELECT COALESCE(rb.total_amount, 0)
                FROM room_bookings rb
                WHERE rb.booking_id = %s AND rb.hotel_name = %s
                """,
                (booking_id, hotel_name)
            )
            total_row = cur.fetchone()
            total = float((total_row or [0])[0] or 0)
            cur.execute(
                """
                SELECT COALESCE(SUM(payment_amount), 0)
                FROM payment_ledger
                WHERE booking_id = %s
                """,
                (booking_id,)
            )
            paid = float((cur.fetchone() or [0])[0] or 0)
            balance = max(total - paid, 0)
            status = 'completed' if balance <= 0.01 and total > 0 else ('partial' if paid > 0 else 'pending')
            return f"💳 Payment status for booking {booking_id} at {hotel_name}: {status} (paid {paid:.2f}, balance {balance:.2f})"
        except Exception as e:
            return f"❌ Error retrieving payment status: {e}"

    def list_bookings_with_special_requests(self, hotel_name: str, manager_name: str, limit: int = 20, guest_phone: str = None) -> str:
        """List all bookings that have special requests for the given hotel and manager. Optionally filter by guest_phone. Returns booking_id, guest_phone, room_type, stay_dates, and the special requests. (READ MongoDB)"""
        try:
            guest_bookings = self.mongo_db['guest_bookings']
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
                updated_by = doc.get("updated_by", {})
                manager_name = updated_by.get("manager_name", "N/A")
                result += f"  • Booking ID: {booking_id}\n"
                result += f"    Guest: {guest_phone}\n"
                result += f"    Room Type: {room_type}\n"
                result += f"    Stay Dates: {', '.join(stay_dates)}\n"
                result += f"    Special Requests: {special_requests}\n"
                result += f"    Last Updated By: {manager_name}\n\n"
            return result.strip()
        except Exception as e:
            return f"❌ Error listing bookings with special requests: {e}"

    def cancel_booking(self, hotel_name: str, booking_id: str, manager_name: str) -> str:
        """Cancel a booking, enforcing hotel_name validation in all database operations. Also cancels all related room_night_assignments (atomic)."""
        try:
            cur = self.postgres_conn.cursor()
            # Validate booking_id exists for hotel_name
            cur.execute("SELECT 1 FROM room_bookings WHERE hotel_name = %s AND booking_id = %s", (hotel_name, booking_id))
            if not cur.fetchone():
                return f"❌ Booking {booking_id} not found for hotel {hotel_name}."
            # Cascade unassign before marking cancelled to release rooms
            try:
                from booking_system.front_desk_manager import intelligent_front_desk
                _ = intelligent_front_desk.unassign_room_number_internal(hotel_name, manager_name, booking_id, stay_dates=None, room_numbers_by_date=None)
            except Exception:
                pass
            cur.execute(
                """
                UPDATE room_bookings SET booking_status = 'cancelled', updated_at = NOW(), updated_by = %s
                WHERE hotel_name = %s AND booking_id = %s
                """,
                (manager_name, hotel_name, booking_id)
            )
            # Also cancel all related booking_stays rows
            cur.execute(
                """
                UPDATE booking_stays SET booking_status = 'cancelled'
                WHERE hotel_name = %s AND booking_id = %s
                """,
                (hotel_name, booking_id)
            )
            # Also cancel all related room_stay rows
            cur.execute(
                """
                UPDATE room_stay SET stay_status = 'cancelled', room_number = NULL
                WHERE hotel_name = %s AND booking_id = %s
                """,
                (hotel_name, booking_id)
            )
            # Free any room_number_assignments
            cur.execute(
                """
                UPDATE room_number_assignments
                SET booking_id = NULL,
                    guest_phone = NULL,
                    assignment_status = 'available',
                    updated_by = %s
                WHERE hotel_name = %s AND booking_id = %s
                """,
                (manager_name, hotel_name, booking_id)
            )
            self.postgres_conn.commit()
            return f"✅ Booking {booking_id} at {hotel_name} cancelled (atomic assignments updated)."
        except Exception as e:
            self.postgres_conn.rollback()
            return f"❌ Error cancelling booking: {e}"


# Create global instance for LEON to use
booking_manager = AgentRuleCompliantBookingManager()

# --- Agent-Rule Compliant Tool Functions (No 'self' parameters) ---

@tool
def list_bookable_room_types_tool(hotel_name: str) -> str:
    """List all room types available for booking at the current hotel."""
    return booking_manager.read_available_room_combinations(hotel_name)

@tool
def check_room_availability_tool(hotel_name: str, room_type: str, stay_date: str, rooms_needed: int = 1) -> dict:
    """Check availability for a specific room type and stay_date at the current hotel. All parameters must be provided by the orchestrator. Returns a structured response with status and details. Do not use this tool to manage quantity or price to a room type."""
    return booking_manager.read_availability(hotel_name, room_type, stay_date, rooms_needed)

@tool
def create_booking_tool(hotel_name: str, manager_name: str, guest_phone: str, room_type: str, stay_dates: list, rooms_booked: int = 1, booking_notes: str = "", channel: str = "") -> dict:
    """Create a new booking atomically. channel is PMS, AGODA, BOOKING.COM, CTRIP, or TRAVELOKA. Return only 1 stay_date for each night stay. Stay_dates does not equal to check_in_date or check_out_date, only return stay_dates for each night stay."""
    result = booking_manager.create_booking(
        hotel_name,
        manager_name,
        guest_phone,
        room_type,
        stay_dates,
        rooms_booked,
        booking_notes,
        channel
    )
    return result

@tool
def get_booking_details_tool(hotel_name: str, manager_name: str, booking_id: str = None, guest_phone: str = None) -> str:
    """Get details of a booking by booking ID or guest phone number."""
    if not hotel_name or not manager_name:
        return "❌ hotel_name and manager_name are required for all operations."
    return booking_manager.read_booking(
        hotel_name,
        manager_name,
        booking_id,
        guest_phone
    )

@tool
def cancel_booking_tool(hotel_name: str, booking_id: str, manager_name: str) -> str:
    """Cancel a booking, enforcing hotel_name for strict multi-tenancy validation."""
    return booking_manager.cancel_booking(hotel_name, booking_id, manager_name)

@tool
def search_bookings_tool(hotel_name: str, manager_name: str, room_type: str = None, guest_phone: str = None, booking_status: str = None, date_from: str = None, date_to: str = None, limit: int = 20) -> str:
    """Search for bookings by hotel, room type, guest phone, status, or date range. booking_status can be cancelled or confirmed"""
    if not hotel_name or not manager_name:
        return "❌ hotel_name and manager_name are required for all operations."
    return booking_manager.read_bookings_by_criteria(
        hotel_name,
        manager_name,
        room_type,
        guest_phone,
        booking_status,
        date_from,
        date_to,
        limit
    )

 

@tool
def get_revenue_forecast_tool(hotel_name: str, forecast_days: int = 30) -> str:
    """Get a revenue forecast for the next N days based on bookings for a hotel."""
    return booking_manager.get_revenue_forecast(hotel_name, forecast_days)

@tool
def analyze_booking_patterns_tool(hotel_name: str, analysis_days: int = 90) -> str:
    """Analyze booking patterns (lead time, stay length, day of week, etc.) for a hotel."""
    return booking_manager.analyze_booking_patterns(hotel_name, analysis_days)

@tool
def add_booking_special_request_tool(hotel_name: str, booking_id: str, special_request: str, manager_name: str) -> str:
    """Add a special request (e.g., late check-in, extra bed) to an existing booking."""
    return booking_manager.add_booking_special_request(hotel_name, booking_id, special_request, manager_name)

@tool
def send_booking_confirmation_tool(booking_id: str, method: str = 'email') -> str:
    """Send a booking confirmation to the guest via the specified method (email, SMS, WhatsApp, etc.)."""
    return booking_manager.send_booking_confirmation(booking_id, method)

@tool
def get_payment_status_for_booking_tool(hotel_name: str, booking_id: str) -> str:
    """Get payment status for a booking"""
    return booking_manager.get_payment_status_for_booking(hotel_name, booking_id)

@tool
def update_booking_status_tool(hotel_name: str, booking_id: str, new_status: str, notes: str = None, manager_name: str = None) -> str:
    """Update the status of a booking. new_status can only be confirmed, use cancel_booking_tool to cancel a booking."""
    return booking_manager.update_booking_status(hotel_name, booking_id, new_status, notes, manager_name)

@tool
def modify_booking_tool(hotel_name: str, booking_id: str, manager_name: str, new_rooms_booked: int = None, new_stay_dates: list = None, booking_notes: str = None, new_room_type: str = None, channel: str = None) -> str:
    """Modify an existing booking by quantity, stay dates, room type, and/or channel while preserving booking_id.
    - Provide new_rooms_booked to change quantity
    - Provide new_stay_dates (list of YYYY-MM-DD) to change nights
    - Provide new_room_type to move booking to another room type
    - Provide channel as PMS, AGODA, BOOKING.COM, CTRIP, or TRAVELOKA
    Requirements: All nights must have pricing; inventory must be sufficient for each night
    """
    return booking_manager.modify_booking(hotel_name, booking_id, manager_name, new_rooms_booked, new_stay_dates, booking_notes, new_room_type, channel)

@tool
def list_bookings_with_special_requests_tool(hotel_name: str, manager_name: str, limit: int = 20, guest_phone: str = None) -> str:
    """List all bookings that have special requests for the given hotel and manager. Optionally filter by guest_phone. Returns booking_id, guest_phone, room_type, stay_dates, and the special requests."""
    return booking_manager.list_bookings_with_special_requests(
        hotel_name,
        manager_name,
        limit,
        guest_phone
    ) 