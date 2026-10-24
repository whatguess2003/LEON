#!/usr/bin/env python3
"""
FRONT DESK MANAGER AGENT
Room number assignment operations and front desk workflow management
Core responsibility: Assign booking_id to room numbers and manage guest check-in/check-out

HYBRID ARCHITECTURE:
- LLM Integration: Natural language room assignment requests and guest preference interpretation
- Direct Tools: Precise database operations and structured workflows
- Best of Both: Intelligent suggestions with reliable execution
- Single source of truth: manager_context.json for hotel_name

CRUD OPERATIONS FOR ROOM NUMBER ASSIGNMENTS:
- READ: Check assignment status and availability  
- UPDATE: Assign booking_id to room numbers, check-in/check-out guests, reassign room numbers
- No CREATE/DELETE: Room assignments created by room_manager, deleted with room deletion

NATURAL KEY ARCHITECTURE:
- Assignment ID: hotel_name||room_number||stay_date
- Booking ID: guest_phone + "booking" + counter (e.g., +60123456789booking1)
- Guest Phone: Natural guest identifier
"""

import os
import json
from typing import Dict
import psycopg2
from pymongo import MongoClient
from datetime import timedelta
from datetime import datetime, timezone
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from .llm_utils import canonical_key

load_dotenv()

# Longest country calling code first. WhatsApp matches the code, then the national number.
_WHATSAPP_DIAL_CODES = (
    "376", "971", "355", "374", "244", "297", "994", "387", "880", "226", "359", "973", "257", "229", "673", "591",
    "975", "267", "375", "501", "243", "236", "242", "225", "237", "506", "238", "599", "357", "420", "253", "213",
    "593", "372", "251", "358", "679", "241", "995", "233", "350", "220", "224", "240", "502", "592", "852", "504",
    "385", "509", "353", "972", "964", "354", "962", "254", "996", "855", "686", "269", "850", "965", "856", "961",
    "423", "231", "266", "370", "352", "371", "218", "212", "377", "373", "382", "261", "692", "389", "223", "976",
    "853", "222", "356", "230", "960", "265", "258", "264", "227", "234", "505", "977", "674", "968", "507", "689",
    "675", "351", "595", "974", "381", "250", "966", "248", "249", "386", "421", "232", "221", "252", "597", "503",
    "992", "670", "993", "216", "886", "255", "380", "256", "598", "998", "379", "678", "685", "967", "260", "263",
    "93", "54", "43", "61", "32", "55", "41", "56", "86", "57", "53", "49", "45", "20", "34", "33", "44", "30",
    "36", "62", "91", "98", "39", "81", "82", "94", "95", "52", "60", "31", "47", "64", "51", "63", "92", "48",
    "40", "46", "65", "66", "90", "58", "84", "27", "1", "7",
)

# LLM-Enhanced Front Desk Manager Class
class IntelligentFrontDeskManager:
    """Hybrid Front Desk Manager with LLM for natural language processing and direct tools for operations"""
    
    def __init__(self):
        self.mongo = MongoClient(os.getenv('MONGO_URL'))
        self.mongo_db = self.mongo['leon_hotel_memory']
    
    # Timezone logic centralized in timezone_utils; use hotel_now_local/hotel_local_today
    
    def get_postgresql_connection(self):
        """Get PostgreSQL connection for assignment operations"""
        return psycopg2.connect(os.getenv('DATABASE_URL'))

    def get_mongodb_connection(self):
        """Get MongoDB connection for guest profiles and content"""
        return self.mongo_db

    def read_available_room_numbers_internal(self, hotel_name: str, room_type: str, stay_date: str) -> dict:
        """
        READ: Fast list of available room numbers using room_number_assignments directly.
        Criteria (for the given date): booking_id IS NULL OR assignment_status = 'available', and room_type matches.
        """
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            # Ensure required timestamp columns exist (safe, idempotent)
            try:
                self._ensure_assignment_timestamp_columns()
            except Exception:
                pass
            cursor.execute(
                """
                SELECT r.room_number
                FROM room_number_assignments r
                WHERE r.hotel_name = %s
                  AND r.stay_date = %s
                  AND UPPER(COALESCE(r.room_type, '')) = UPPER(%s)
                  AND (
                        (r.booking_id IS NULL AND COALESCE(r.assignment_status,'available') = 'available')
                      )
                  AND NOT EXISTS (
                        SELECT 1 FROM room_number_assignments x
                        WHERE x.hotel_name = r.hotel_name
                          AND x.room_number = r.room_number
                          AND x.stay_date = r.stay_date
                          AND x.booking_id IS NOT NULL
                  )
                  AND NOT EXISTS (
                        SELECT 1
                        FROM room_stay rs
                        LEFT JOIN room_bookings rb
                          ON rb.booking_id = rs.booking_id AND rb.hotel_name = rs.hotel_name
                        WHERE rs.hotel_name = r.hotel_name
                          AND rs.stay_date = r.stay_date
                          AND rs.room_number = r.room_number
                          AND rs.booking_id IS NOT NULL
                          AND COALESCE(rb.booking_status, 'confirmed') <> 'cancelled'
                  )
                ORDER BY r.room_number
                """,
                (hotel_name, stay_date, room_type)
            )
            available = [row[0] for row in (cursor.fetchall() or [])]
            cursor.close(); conn.close()
            # Final post-check removed: this function only returns availability list
            return {
                "hotel_name": hotel_name,
                "room_type": room_type,
                "stay_date": stay_date,
                "available_room_numbers": available
            }
        except Exception as e:
            try:
                if 'conn' in locals(): conn.close()
            except Exception:
                pass
            return {"error": str(e)}

    def _get_hotel_policy(self, hotel_name: str, policy_type: str = "clean_threshold_days") -> int:
        """Get hotel-specific policy settings from MongoDB"""
        try:
            mongodb = self.get_mongodb_connection()
            hotel_policy = mongodb['hotel_policies'].find_one({"hotel_name": hotel_name})
            
            if hotel_policy:
                return hotel_policy.get(policy_type, 3)  # Default to 3 days if not specified
            else:
                return 3  # Default fallback
        except Exception as e:
            print(f"⚠️ Error reading hotel policy: {e}")
            return 3  # Default fallback

    def _ensure_assignment_timestamp_columns(self) -> None:
        """Idempotently ensure timestamp columns exist in core tables (live DB safety)."""
        try:
            conn = self.get_postgresql_connection()
            cur = conn.cursor()
            # room_number_assignments
            try:
                cur.execute("ALTER TABLE room_number_assignments ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMP NULL;")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE room_number_assignments ADD COLUMN IF NOT EXISTS assigned_by VARCHAR NULL;")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE room_number_assignments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NULL;")
            except Exception:
                pass
            # room_stay
            try:
                cur.execute("ALTER TABLE room_stay ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMP NULL;")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE room_stay ADD COLUMN IF NOT EXISTS checked_in_at TIMESTAMP NULL;")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE room_stay ADD COLUMN IF NOT EXISTS checked_out_at TIMESTAMP NULL;")
            except Exception:
                pass
            try:
                conn.commit()
            except Exception:
                pass
            try:
                cur.close(); conn.close()
            except Exception:
                pass
        except Exception:
            # Safe no-op if connection fails
            pass
    
    def parse_room_assignment_request(self, hotel_name: str, manager_name: str, booking_id: str, recommended_room: str, reasoning: str, alternatives: list, special_notes: str) -> str:
        """Process room assignment suggestion with structured parameters (no LLM call, no natural language)."""
        result = f"🤖 ROOM ASSIGNMENT SUGGESTION\n"
        result += f"🏨 Hotel: {hotel_name}\n"
        result += f"📋 Booking: {booking_id}\n"
        result += f"Recommended Room: {recommended_room}\n"
        result += f"Reasoning: {reasoning}\n"
        result += f"Alternatives: {alternatives}\n"
        result += f"Special Notes: {special_notes}\n"
        return result

    def interpret_maintenance_issue(self, hotel_name: str, manager_name: str, room_number: str, severity: str, room_status: str, estimated_duration: str, departments_needed: list, guest_impact: str, urgency: str, recommendations: dict) -> str:
        """Process maintenance issue with structured parameters (no LLM call, no natural language)."""
        # Example: Format a summary or perform business logic/database updates here
        result = f"🔧 MAINTENANCE ISSUE SUMMARY\n"
        result += f"🏨 Hotel: {hotel_name}\n"
        result += f"🚪 Room: {room_number}\n"
        result += f"Severity: {severity}\n"
        result += f"Room Status: {room_status}\n"
        result += f"Estimated Duration: {estimated_duration}\n"
        result += f"Departments Needed: {', '.join(departments_needed)}\n"
        result += f"Guest Impact: {guest_impact}\n"
        result += f"Urgency: {urgency}\n"
        result += f"Recommendations: {recommendations}\n"
        # Add any business logic or database updates here as needed
        return result

    def update_room_assignment_auto(self, hotel_name: str, manager_name: str, booking_id: str, room_number: str = None, stay_dates: list = None, room_numbers_by_date: dict = None, rooms: list = None, auto_fill: bool = False) -> str:
        """
        READ-then-WRITE: Assign rooms for a booking with strict validation.

        Behavior:
        - If room_numbers_by_date is provided (dict YYYY-MM-DD -> list of room_numbers):
          - For each date, validate list length equals required slots for that night
          - Validate each room is available and matches booking room_type
          - If shorter than required and auto_fill is True, fill remaining slots with available rooms of same room_type
          - No guessing when auto_fill is False
        - If rooms (list) is provided:
          - Interpreted as first-night mapping; applied identically to all nights
          - Length must equal required slots for the booking per night
        - If only room_number is provided:
          - Allowed only when required slots per date == 1; otherwise error suggesting room_numbers_by_date
        - If neither provided: error

        Sets room_stay rows and room_number_assignments; per-night stay_status becomes 'assigned' only when all slots filled, else remains 'pending'.
        """
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            # Validate booking exists and get booking stay_dates
            cursor.execute("""
                SELECT guest_phone, hotel_name, room_type FROM room_bookings WHERE booking_id = %s
            """, (booking_id,))
            booking = cursor.fetchone()
            if not booking:
                cursor.close()
                conn.close()
                return f"❌ Booking {booking_id} not found in room_bookings table"
            guest_phone, booking_hotel, booking_room_type = booking
            if canonical_key(hotel_name) != canonical_key(booking_hotel):
                cursor.close()
                conn.close()
                return f"❌ Hotel mismatch: Booking is for '{booking_hotel}', not '{hotel_name}'"
            # If stay_dates not provided, fetch from room_night_assignments
            if stay_dates is None:
                cursor.execute("""
                    SELECT DISTINCT stay_date FROM room_stay WHERE booking_id = %s ORDER BY stay_date
                """, (booking_id,))
                stay_dates = [row[0] for row in cursor.fetchall()]
                if not stay_dates:
                    cursor.close()
                    conn.close()
                    return f"❌ No stay_dates found for booking {booking_id}"
            # Enforce merged-dates using first night mapping
            assignment_results = []
            anchor_date = min(stay_dates)
            # Determine rooms needed on anchor night
            cursor.execute(
                """
                SELECT stay_index FROM room_stay
                    WHERE booking_id = %s AND stay_date = %s
                    ORDER BY stay_index
                """,
                (booking_id, anchor_date)
            )
            anchor_indices = [r[0] for r in cursor.fetchall()]
            if not anchor_indices:
                cursor.close(); conn.close()
                return f"❌ No room_stay found for anchor date {anchor_date}"
            rooms_needed = len(anchor_indices)
            # Build base proposed list from anchor only
            if room_numbers_by_date is not None:
                anchor_key = anchor_date if isinstance(anchor_date, str) else str(anchor_date)
                provided = list(room_numbers_by_date.get(anchor_key, []))
                # Reject other keys if provided
                extra_keys = [k for k in room_numbers_by_date.keys() if str(k) != str(anchor_key)]
                if extra_keys:
                    cursor.close(); conn.close()
                    return f"❌ MERGED DATES: Provide rooms only for first night {anchor_key}."
                base_proposed = []
                seen = set()
                for rn in provided:
                    if rn not in seen:
                        seen.add(rn); base_proposed.append(rn)
                if len(base_proposed) != rooms_needed:
                    cursor.close(); conn.close()
                    return f"❌ {anchor_key}: proposed {len(base_proposed)} does not match required {rooms_needed}"
            elif rooms is not None:
                base_proposed = []
                seen = set()
                for rn in list(rooms):
                    if rn not in seen:
                        seen.add(rn); base_proposed.append(rn)
                if len(base_proposed) != rooms_needed:
                    cursor.close(); conn.close()
                    return f"❌ {anchor_date}: proposed {len(base_proposed)} does not match required {rooms_needed}. Provide rooms list equal to required slots."
            elif room_number:
                if rooms_needed != 1:
                    cursor.close(); conn.close()
                    return f"❌ {anchor_date}: multiple rooms required ({rooms_needed}). Provide rooms list for first night (e.g., rooms=['301','302'])."
                base_proposed = [room_number]
            else:
                cursor.close(); conn.close()
                return f"❌ No rooms provided. Use rooms list (first night only) or room_number when single is required."
            # Validate base_proposed across ALL nights
            for sdate in stay_dates:
                # Count must match per night
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM room_stay
                    WHERE booking_id = %s AND stay_date = %s
                    """,
                    (booking_id, sdate)
                )
                cnt = int(cursor.fetchone()[0] or 0)
                if cnt != len(base_proposed):
                    cursor.close(); conn.close()
                    return f"❌ {sdate}: required {cnt} does not match first-night proposed {len(base_proposed)}"
                for rn in base_proposed:
                    cursor.execute(
                        """
                        SELECT booking_id, assignment_status, COALESCE(room_type,'')
                        FROM room_number_assignments
                        WHERE hotel_name = %s AND stay_date = %s AND room_number = %s
                        """,
                        (hotel_name, sdate, rn)
                    )
                    row = cursor.fetchone()
                    if not row:
                        cursor.close(); conn.close()
                        return f"❌ {sdate}: room {rn} has no assignment record"
                    assigned_bid, status, rn_type = row
                    if assigned_bid and str(assigned_bid).strip() != str(booking_id).strip():
                        existing_booking_id = assigned_bid
                        cursor.close(); conn.close()
                        return f"❌ {sdate}: room {rn} already assigned to {existing_booking_id}"
                    if status != 'available' and (not assigned_bid or str(assigned_bid).strip() != str(booking_id).strip()):
                        cursor.close(); conn.close()
                        return f"❌ {sdate}: room {rn} not available (status={status})"
                    if rn_type and canonical_key(rn_type) != canonical_key(booking_room_type):
                        cursor.close(); conn.close()
                        return f"❌ {sdate}: room {rn} type {rn_type} != booking type {booking_room_type}"
            # Perform assignments across ALL nights
            for sdate in stay_dates:
                cursor.execute(
                    """
                    SELECT stay_index FROM room_stay
                    WHERE booking_id = %s AND stay_date = %s
                    ORDER BY stay_index
                    """,
                    (booking_id, sdate)
                )
                indices = [r[0] for r in cursor.fetchall()]
                for i, stay_index in enumerate(indices):
                    rn = base_proposed[i]
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET room_number = %s, stay_status = 'assigned', assigned_by = %s, assigned_at = CURRENT_TIMESTAMP
                        WHERE booking_id = %s AND stay_date = %s AND stay_index = %s
                        """,
                        (rn, manager_name, booking_id, sdate, stay_index)
                    )
                    cursor.execute(
                        """
                        UPDATE room_number_assignments
                        SET room_type = %s,
                            booking_id = %s,
                            guest_phone = %s,
                            assignment_status = 'assigned',
                            updated_by = %s
                        WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
                        """,
                        (booking_room_type, booking_id, guest_phone, manager_name, hotel_name, rn, sdate)
                    )
                    if cursor.rowcount == 0:
                        cursor.execute(
                            """
                            INSERT INTO room_number_assignments (
                                hotel_name, room_type, room_number, stay_date, assignment_status, booking_id, guest_phone, assigned_by, assigned_at, updated_by
                            ) VALUES (%s, %s, %s, %s, 'assigned', %s, %s, %s, CURRENT_TIMESTAMP, %s)
                            """,
                            (hotel_name, booking_room_type, rn, sdate, booking_id, guest_phone, manager_name, manager_name)
                        )
                # Update per-night completeness
                    cursor.execute(
                        """
                        SELECT COUNT(*) FILTER (WHERE room_number IS NOT NULL) AS assigned_cnt,
                               COUNT(*) AS total_cnt
                        FROM room_stay
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                        """,
                        (hotel_name, booking_id, sdate)
                    )
                    row_cnts = cursor.fetchone() or (0, 0)
                    assigned_cnt = int(row_cnts[0] or 0)
                    total_cnt = int(row_cnts[1] or 0)
                    if total_cnt > 0:
                        if assigned_cnt >= total_cnt:
                            cursor.execute(
                                """
                                UPDATE room_stay
                                SET stay_status = 'assigned',
                                    assigned_at = COALESCE(assigned_at, CURRENT_TIMESTAMP)
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                                  AND stay_status NOT IN ('checked_in','departed','cancelled')
                                """,
                                (hotel_name, booking_id, sdate)
                            )
                        else:
                            cursor.execute(
                                """
                                UPDATE room_stay
                                SET stay_status = 'pending'
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                                  AND stay_status NOT IN ('checked_in','departed','cancelled')
                                """,
                                (hotel_name, booking_id, sdate)
                            )
                
                assignment_results.append(f"✅ Assigned rooms for {sdate}: {', '.join(base_proposed)}")
            # Booking-wide normalization after room-based checkout: mark entire stay as departed and fill checked_out_at
            try:
                cursor.execute(
                    """
                    UPDATE room_stay
                    SET stay_status = 'departed',
                        checked_out_at = COALESCE(checked_out_at, CURRENT_TIMESTAMP)
                    WHERE hotel_name = %s AND booking_id = %s
                      AND LOWER(COALESCE(stay_status,'')) <> 'cancelled'
                    """,
                    (hotel_name, booking_id)
                )
            except Exception:
                    pass
            conn.commit()
            cursor.close()
            conn.close()
            return "\n".join(assignment_results)
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Error assigning rooms: {str(e)}"

    def unassign_room_number_internal(self, hotel_name: str, manager_name: str, booking_id: str, stay_dates: list = None, room_numbers_by_date: dict = None) -> str:
        """
        READ-then-WRITE: Unassign rooms for a booking with MERGED-DATES enforcement.
        - Only first-night unassignment mapping is accepted; subsequent nights follow automatically.
        - If room_numbers_by_date provided, only the first-night key is accepted; else unassign all for that night and propagate to all nights.
        """
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            # Validate booking and get hotel
            cursor.execute("""
                SELECT hotel_name, guest_phone FROM room_bookings WHERE booking_id = %s
            """, (booking_id,))
            row = cursor.fetchone()
            if not row:
                cursor.close()
                conn.close()
                return f"❌ Booking {booking_id} not found"
            booking_hotel, guest_phone = row
            if canonical_key(hotel_name) != canonical_key(booking_hotel):
                cursor.close()
                conn.close()
                return f"❌ Hotel mismatch: Booking is for '{booking_hotel}', not '{hotel_name}'"
            # Determine full stay dates and anchor
            cursor.execute("""
                SELECT DISTINCT stay_date FROM room_stay WHERE booking_id = %s ORDER BY stay_date
            """, (booking_id,))
            all_dates = [r[0] for r in cursor.fetchall()]
            if not all_dates:
                cursor.close(); conn.close()
                return f"❌ No stay_dates found for booking {booking_id}"
            anchor_date = min(all_dates)
            # Build target rooms from first night only
            target_rooms = None
            if room_numbers_by_date is not None:
                extra_keys = [k for k in room_numbers_by_date.keys() if str(k) != str(anchor_date)]
                if extra_keys:
                    cursor.close(); conn.close()
                    return f"❌ MERGED DATES: Provide rooms only for first night {anchor_date}."
                target_rooms = set(room_numbers_by_date.get(str(anchor_date), []))
            results = []
            for sdate in all_dates:
                # Fetch current assignments
                cursor.execute("""
                    SELECT stay_index, room_number FROM room_stay
                    WHERE booking_id = %s AND stay_date = %s
                    ORDER BY stay_index
                """, (booking_id, sdate))
                rows = cursor.fetchall() or []
                if not rows:
                    results.append(f"❌ {sdate}: no room_stay rows found")
                    continue
                to_clear = []
                if target_rooms is not None:
                    for stay_index, rn in rows:
                        if rn in target_rooms:
                            to_clear.append((stay_index, rn))
                else:
                    for stay_index, rn in rows:
                        if rn:
                            to_clear.append((stay_index, rn))
                if not to_clear:
                    results.append(f"ℹ️ {sdate}: nothing to unassign")
                    continue
                # Perform unassign across all nights
                for stay_index, rn in to_clear:
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET room_number = NULL, stay_status = 'pending', assigned_by = %s, assigned_at = CURRENT_TIMESTAMP
                        WHERE booking_id = %s AND stay_date = %s AND stay_index = %s
                          AND stay_status NOT IN ('checked_in','departed','cancelled')
                        """,
                        (manager_name, booking_id, sdate, stay_index)
                    )
                    cursor.execute(
                        """
                        UPDATE room_number_assignments
                        SET booking_id = NULL, guest_phone = NULL, assignment_status = 'available', updated_by = %s
                        WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
                        """,
                        (manager_name, hotel_name, rn, sdate)
                    )
                results.append(f"✅ {sdate}: unassigned {len(to_clear)} room(s)")
            conn.commit()
            cursor.close()
            conn.close()
            return "\n".join(results)
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Error unassigning rooms: {str(e)}"

    def read_assignment_dashboard(self, hotel_name: str, manager_name: str, date: str = None) -> str:
        """READ comprehensive assignment dashboard data for front desk operations. hotel_name and manager_name are required for canonical compliance, even if not used."""
        try:
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
            if not manager_name:
                return "❌ No manager context found. manager_name is required."
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            # Parse date (hotel-local)
            if date:
                try:
                    y, m, d = [int(x) for x in str(date).split('-')]
                    from booking_system.timezone_utils import hotel_local_today
                    base_today = hotel_local_today(hotel_name)
                    target_date = base_today.replace(year=y, month=m, day=d)
                except Exception:
                    return f"❌ Invalid date format. Use YYYY-MM-DD"
            else:
                target_date = datetime.now(timezone.utc).date()
            # Get assignment summary
            cursor.execute("""
                SELECT assignment_status, COUNT(*) 
                FROM room_number_assignments 
                WHERE hotel_name = %s AND stay_date = %s
                GROUP BY assignment_status
                ORDER BY assignment_status
            """, (hotel_name, target_date))
            status_summary = cursor.fetchall()
            # Get detailed assignments
            cursor.execute("""
                SELECT room_number, assignment_status, booking_id, guest_phone, 
                       assigned_at, assigned_by, assignment_notes
                FROM room_number_assignments 
                WHERE hotel_name = %s AND stay_date = %s
                ORDER BY room_number
            """, (hotel_name, target_date))
            assignments = cursor.fetchall()
            cursor.close()
            if not assignments:
                return f"❌ No assignment records found for {hotel_name} on {target_date}"
            # Build dashboard
            response = f"🏨 FRONT DESK ASSIGNMENT DASHBOARD\n"
            response += f"Hotel: {hotel_name} | Date: {target_date}\n"
            response += "=" * 50 + "\n\n"
            # Status summary
            response += "📊 ASSIGNMENT SUMMARY\n"
            total_rooms = sum([count for _, count in status_summary])
            for status, count in status_summary:
                percentage = (count / total_rooms) * 100
                status_icon = {
                    'available': '✅',
                    'assigned': '📋',
                    'out-of-order': '🔧'
                }.get(status, '❓')
                response += f"  {status_icon} {status.upper()}: {count} rooms ({percentage:.1f}%)\n"
            response += f"\n🎯 TOTAL ROOMS: {total_rooms}\n\n"
            # Detailed assignments
            response += "🚪 ROOM ASSIGNMENT STATUS\n"
            for room_number, status, booking_id, guest_phone, assigned_at, assigned_by, notes in assignments:
                status_icon = {
                    'available': '✅',
                    'assigned': '📋',
                    'out-of-order': '🔧'
                }.get(status, '❓')
                response += f"  {status_icon} Room {room_number} - {status.upper()}"
                if booking_id:
                    response += f" | Booking: {booking_id}"
                if guest_phone:
                    response += f" | Guest: {guest_phone}"
                if assigned_by:
                    response += f" | By: {assigned_by}"
                if status == 'out-of-order' and notes:
                    response += f" | Reason: {notes}"
                response += "\n"
            return response
        except Exception as e:
            return f"❌ Error generating dashboard: {str(e)}"

    def update_room_out_of_order_status_internal(self, hotel_name: str, manager_name: str, room_number: str, start_date: str, end_date: str, reason: str) -> str:
        """
        Internal function to set room as out-of-order for a date range. Dates must be in YYYY-MM-DD format.
        """
        try:
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
            if not manager_name:
                return "❌ No manager context found. manager_name is required."
            y1, m1, d1 = [int(x) for x in str(start_date).split('-')]
            y2, m2, d2 = [int(x) for x in str(end_date).split('-')]
            from booking_system.timezone_utils import hotel_local_today
            base_today = hotel_local_today(hotel_name)
            start_dt = base_today.replace(year=y1, month=m1, day=d1)
            end_dt = base_today.replace(year=y2, month=m2, day=d2)
        except Exception:
            return "❌ Invalid date format. Use YYYY-MM-DD"
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            if start_dt > end_dt:
                return "❌ Start date cannot be after end date"
            # Generate list of dates
            dates = []
            current_date = start_dt
            while current_date <= end_dt:
                dates.append(current_date)
                current_date += timedelta(days=1)
            # Check if any dates are already assigned to bookings
            occupied_dates = []
            for date in dates:
                cursor.execute("""
                    SELECT assignment_status, booking_id 
                    FROM room_number_assignments 
                    WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
                    AND assignment_status = 'assigned'
                """, (hotel_name, room_number, date))
                result = cursor.fetchone()
                if result:
                    occupied_dates.append(f"{date} (status: {result[0]}, booking: {result[1]})")
            if occupied_dates:
                cursor.close()
                conn.close()
                return f"❌ Cannot set out-of-order. Room has active booking assignments:\n" + "\n".join([f"  • {date}" for date in occupied_dates])
            # Set room as out-of-order for all dates
            updated_count = 0
            for date in dates:
                cursor.execute("""
                    UPDATE room_number_assignments 
                    SET assignment_status = 'out-of-order',
                        booking_id = NULL,
                        guest_phone = NULL,
                        assigned_at = CURRENT_TIMESTAMP,
                        assigned_by = %s,
                        assignment_notes = %s
                    WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
                """, (manager_name, f"Out-of-order: {reason}", hotel_name, room_number, date))
                updated_count += cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
            response = f"✅ Room Set Out-of-Order\n"
            response += f"🏨 Hotel: {hotel_name}\n"
            response += f"🚪 Room: {room_number}\n"
            response += f"📅 Dates: {start_date} to {end_date} ({len(dates)} days)\n"
            response += f"🔧 Reason: {reason}\n"
            response += f"👤 Updated by: {manager_name}\n"
            response += f"📊 Records updated: {updated_count}\n"
            response += f"⚠️ Room unavailable for bookings during this period"
            return response
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Error setting room out-of-order: {str(e)}"

    def update_room_available_status_internal(self, hotel_name: str, manager_name: str, room_number: str, start_date: str = None, end_date: str = None) -> str:
        """
        Internal function to restore room to available for a date range. Dates must be in YYYY-MM-DD format. If start_date or end_date is None, will find all future out-of-order dates and use their range.
        """
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
        if not manager_name:
            return "❌ No manager context found. manager_name is required."
        # If start_date or end_date is None, find all future out-of-order dates for this room
        if not start_date or not end_date:
            try:
                from booking_system.timezone_utils import hotel_local_today
                today = hotel_local_today(hotel_name)
            except Exception:
                return "❌ Missing hotel timezone"
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT stay_date FROM room_number_assignments
                WHERE hotel_name = %s AND room_number = %s
                AND assignment_status = 'out-of-order'
                AND stay_date >= %s
                ORDER BY stay_date
            """, (hotel_name, room_number, today))
            dates = [row[0] for row in cursor.fetchall()]
            cursor.close()
            conn.close()
            if not dates:
                return f"❌ No out-of-order dates found for room {room_number}. Please specify a date range."
            start_date = min(dates).isoformat()
            end_date = max(dates).isoformat()
        try:
            y1, m1, d1 = [int(x) for x in str(start_date).split('-')]
            y2, m2, d2 = [int(x) for x in str(end_date).split('-')]
            from booking_system.timezone_utils import hotel_local_today
            base_today = hotel_local_today(hotel_name)
            start_dt = base_today.replace(year=y1, month=m1, day=d1)
            end_dt = base_today.replace(year=y2, month=m2, day=d2)
        except Exception:
            return "❌ Invalid date format. Use YYYY-MM-DD"
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            if start_dt > end_dt:
                return "❌ Start date cannot be after end date"
            # Generate list of dates
            dates = []
            current_date = start_dt
            while current_date <= end_dt:
                dates.append(current_date)
                current_date += timedelta(days=1)
            updated_count = 0
            inserted_count = 0
            for date in dates:
                # Check if row exists
                cursor.execute("""
                    SELECT 1 FROM room_number_assignments
                    WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
                """, (hotel_name, room_number, date))
                if cursor.fetchone():
                    # Update to available
                    cursor.execute("""
                        UPDATE room_number_assignments 
                        SET assignment_status = 'available',
                            booking_id = NULL,
                            guest_phone = NULL,
                            assigned_at = CURRENT_TIMESTAMP,
                            assigned_by = %s,
                            assignment_notes = %s
                        WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
                    """, (manager_name, f"Restored from out-of-order by {manager_name}", hotel_name, room_number, date))
                    updated_count += cursor.rowcount
                else:
                    # Insert new row as available, room_type is now nullable
                    cursor.execute("""
                        INSERT INTO room_number_assignments (
                            hotel_name, room_type, room_number, stay_date, assignment_status, created_by, updated_by
                        ) VALUES (%s, %s, %s, %s, 'available', %s, %s)
                    """, (hotel_name, None, room_number, date, manager_name, manager_name))
                    inserted_count += 1
            conn.commit()
            cursor.close()
            conn.close()
            response = f"✅ Room Restored from Out-of-Order or Created\n"
            response += f"🏨 Hotel: {hotel_name}\n"
            response += f"🚪 Room: {room_number}\n"
            response += f"📅 Dates: {start_date} to {end_date} ({len(dates)} days)\n"
            response += f"👤 Restored by: {manager_name}\n"
            response += f"📊 Records updated: {updated_count}, inserted: {inserted_count}\n"
            response += f"✅ Room now available for bookings"
            return response
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Error restoring room: {str(e)}"

    def update_room_readiness_status_internal(self, hotel_name: str, manager_name: str, room_number: str, new_status: str, notes: str = "") -> str:
        """
        Internal function to update room readiness status (clean, dirty, cleaning, occupied).
        """
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
        if not manager_name:
            return "❌ No manager context found. manager_name is required."
        valid_statuses = ['clean', 'dirty', 'cleaning', 'occupied']
        if new_status not in valid_statuses:
            return f"❌ Invalid status. Must be one of: {', '.join(valid_statuses)}"
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT readiness_status, clean_since 
                FROM room_readiness_status 
                WHERE hotel_name = %s AND room_number = %s
            """, (hotel_name, room_number))
            existing = cursor.fetchone()    
            if not existing:
                # Fallback: create the readiness status record if missing
                cursor.execute("""
                    INSERT INTO room_readiness_status 
                    (hotel_name, room_number, readiness_status, changed_by, created_by, updated_by, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (hotel_name, room_number) DO NOTHING;
                """, (hotel_name, room_number, new_status, manager_name, manager_name, manager_name, f"Created by fallback in update_room_readiness_status_internal to {new_status}"))
                conn.commit()
                # Try to fetch again
                cursor.execute("""
                    SELECT readiness_status, clean_since 
                    FROM room_readiness_status 
                    WHERE hotel_name = %s AND room_number = %s
                """, (hotel_name, room_number))
                existing = cursor.fetchone()
                if not existing:
                    cursor.close()
                    conn.close()
                    return f"❌ Failed to create readiness status record for room {room_number} at {hotel_name}"
            current_status, current_clean_since = existing
            update_fields = {
                'readiness_status': new_status,
                'status_changed_at': 'CURRENT_TIMESTAMP',
                'changed_by': manager_name,
                'notes': notes or f"Status changed to {new_status} by {manager_name}"
            }
            if new_status == 'clean' and current_status != 'clean':
                update_fields['clean_since'] = 'CURRENT_TIMESTAMP'
            elif new_status != 'clean':
                update_fields['clean_since'] = None
            set_clause = []
            values = []
            for field, value in update_fields.items():
                if value == 'CURRENT_TIMESTAMP':
                    set_clause.append(f"{field} = CURRENT_TIMESTAMP")
                elif value is None:
                    set_clause.append(f"{field} = NULL")
                else:
                    set_clause.append(f"{field} = %s")
                    values.append(value)
            values.extend([hotel_name, room_number])
            query = f"""
                UPDATE room_readiness_status 
                SET {', '.join(set_clause)}
                WHERE hotel_name = %s AND room_number = %s
            """
            cursor.execute(query, values)
            if cursor.rowcount == 0:
                cursor.close()
                conn.close()
                return f"❌ Failed to update readiness status"
            conn.commit()
            cursor.close()
            conn.close()
            response = f"✅ Room Readiness Status Updated\n"
            response += f"🏨 Hotel: {hotel_name}\n"
            response += f"🚪 Room: {room_number}\n"
            response += f"📊 Status: {current_status} → {new_status}\n"
            response += f"👤 Updated by: {manager_name}\n"
            if new_status == 'clean':
                response += f"⏰ Clean timestamp set\n"
                response += f"🔄 Auto-dirty: After 3 days (configurable)"
            elif current_status == 'clean':
                response += f"⏰ Clean timestamp cleared"
            if notes:
                response += f"\n📝 Notes: {notes}"
            return response
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Error updating readiness status: {str(e)}"

    def read_rooms_exceeding_clean_threshold_internal(self, hotel_name: str, threshold_days: int = None) -> str:
        """
        Internal function to read rooms that have been clean longer than threshold and need re-cleaning.
        """
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
        try:
            # Get threshold from hotel policy if not specified
            if threshold_days is None:
                threshold_days = self._get_hotel_policy(hotel_name, "clean_threshold_days")
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            # Find rooms clean longer than threshold
            cursor.execute("""
                SELECT room_number, readiness_status, clean_since, 
                       EXTRACT(DAYS FROM (clock_timestamp() - clean_since)) as days_clean,
                       auto_dirty_threshold_days
                FROM room_readiness_status 
                WHERE hotel_name = %s 
                AND readiness_status = 'clean'
                AND clean_since IS NOT NULL
                AND EXTRACT(DAYS FROM (CURRENT_TIMESTAMP - clean_since)) >= %s
                ORDER BY clean_since ASC
            """, (hotel_name, threshold_days))
            exceeding_rooms = cursor.fetchall()
            cursor.close()
            conn.close()
            if not exceeding_rooms:
                return f"✅ No rooms at {hotel_name} exceed the {threshold_days}-day clean threshold"
            response = f"⚠️ Rooms Exceeding Clean Threshold - {hotel_name}\n"
            response += f"🕐 Threshold: {threshold_days} days (from hotel policy)\n\n"
            response += "🚪 ROOMS NEEDING RE-CLEANING:\n"
            for room_number, status, clean_since, days_clean, room_threshold in exceeding_rooms:
                response += f"  ❌ Room {room_number}\n"
                response += f"      📅 Clean since: {clean_since.isoformat() if hasattr(clean_since,'isoformat') else clean_since}\n"
                response += f"      ⏰ Days clean: {int(days_clean)} days\n"
                response += f"      🎯 Room threshold: {room_threshold} days\n\n"
            response += f"💡 Recommendation: Set these rooms to 'dirty' status\n"
            response += f"🔧 Use update_room_readiness_status tool to change status"
            return response
        except Exception as e:
            return f"❌ Error checking clean threshold: {str(e)}"

    def bulk_checkout_past_internal(self, hotel_name: str, manager_name: str) -> dict:
        """WRITE: Mark all past stays (stay_date < yesterday) as departed without changing readiness.
        Database operation: write (PostgreSQL).
        """
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            try:
                from booking_system.timezone_utils import hotel_local_today
                today = hotel_local_today(hotel_name)
            except Exception:
                from datetime import datetime, timezone, timedelta
                today = datetime.now(timezone.utc).date()
            try:
                from datetime import timedelta
                cutoff = today - timedelta(days=1)
            except Exception:
                cutoff = today
            # Update room_stay for all non-cancelled bookings before cutoff
            cursor.execute(
                """
                WITH tgt AS (
                    SELECT rs.hotel_name, rs.booking_id, rs.stay_date
                    FROM room_stay rs
                    JOIN room_bookings rb
                      ON rb.booking_id = rs.booking_id AND rb.hotel_name = rs.hotel_name
                    WHERE rs.hotel_name = %s
                      AND rs.stay_date < %s
                      AND COALESCE(rb.booking_status, 'confirmed') <> 'cancelled'
                      AND LOWER(COALESCE(rs.stay_status,'')) <> 'departed'
                )
                UPDATE room_stay rs
                SET stay_status = 'departed',
                    checked_out_at = COALESCE(rs.checked_out_at, CURRENT_TIMESTAMP)
                FROM tgt
                WHERE rs.hotel_name = tgt.hotel_name
                  AND rs.booking_id = tgt.booking_id
                  AND rs.stay_date = tgt.stay_date
                """,
                (hotel_name, cutoff)
            )
            affected = cursor.rowcount or 0
            conn.commit(); cursor.close(); conn.close()
            return {"result": "success", "hotel_name": hotel_name, "cutoff": str(cutoff), "rows_updated": int(affected)}
        except Exception as e:
            try:
                if 'conn' in locals(): conn.rollback(); conn.close()
            except Exception:
                pass
            return {"error": str(e)}

    def read_past_stays_internal(self, hotel_name: str) -> dict:
        """READ: List all stays with stay_date < yesterday and stay_status <> 'departed' (non-cancelled bookings).
        Database operation: read (PostgreSQL + MongoDB for names).
        """
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            try:
                from booking_system.timezone_utils import hotel_local_today
                today = hotel_local_today(hotel_name)
            except Exception:
                from datetime import datetime, timezone
                today = datetime.now(timezone.utc).date()
            from datetime import timedelta
            cutoff = today - timedelta(days=1)
            cursor.execute(
                """
                SELECT rs.booking_id,
                       rs.stay_date,
                       rb.guest_phone,
                       COALESCE(rb.rooms_booked, 1) AS rooms_booked,
                       COALESCE(rs.stay_status, '') AS stay_status,
                       rs.room_number
                FROM room_stay rs
                JOIN room_bookings rb
                  ON rb.booking_id = rs.booking_id AND rb.hotel_name = rs.hotel_name
                WHERE rs.hotel_name = %s
                  AND rs.stay_date < %s
                  AND COALESCE(rb.booking_status, 'confirmed') <> 'cancelled'
                  AND LOWER(COALESCE(rs.stay_status,'')) <> 'departed'
                ORDER BY rs.stay_date, rs.booking_id
                """,
                (hotel_name, cutoff)
            )
            rows = cursor.fetchall() or []
            # Map guest names from Mongo
            names = {}
            booking_names = {}
            try:
                db = self.get_mongodb_connection()
                phones = sorted({str(r[2]) for r in rows if r and r[2]})
                booking_ids = sorted({str(r[0]) for r in rows if r and r[0]})
                if phones:
                    coll = db.get_collection('guests')
                    for doc in coll.find({"guest_phone": {"$in": phones}}, {"guest_phone": 1, "guest_name": 1}):
                            ph = str(doc.get('guest_phone'))
                            nm = str(doc.get('guest_name') or '').strip()
                            if ph and nm and ph not in names:
                                names[ph] = nm
                if booking_ids:
                    for doc in db.get_collection('guest_bookings').find({"hotel_name": hotel_name, "booking_id": {"$in": booking_ids}}, {"_id": 0, "booking_id": 1, "guest_name": 1}):
                        bid = str(doc.get("booking_id") or "")
                        nm = str(doc.get("guest_name") or "").strip()
                        if bid and nm:
                            booking_names[bid] = nm
            except Exception:
                names = {}
                booking_names = {}
            items = []
            agg = {}
            def _rank(st):
                s = (str(st or '').strip().lower())
                return 3 if s == 'checked_in' else (2 if s == 'assigned' else (1 if s == 'pending' else 0))
            for bid, sd, ph, rb, st, rn in rows:
                items.append({
                    "booking_id": str(bid),
                    "stay_date": (sd.isoformat() if hasattr(sd,'isoformat') else str(sd)),
                    "guest_phone": (str(ph) if ph is not None else None),
                    "guest_name": names.get(str(ph)) or booking_names.get(str(bid)) or None,
                    "rooms_booked": int(rb or 1),
                    "stay_status": str(st or ''),
                    "room_number": (str(rn) if rn is not None else None)
                })
                key = str(bid)
                date_iso = (sd.isoformat() if hasattr(sd,'isoformat') else str(sd))
                if key not in agg:
                    agg[key] = {
                        "booking_id": key,
                        "guest_phone": (str(ph) if ph is not None else None),
                        "guest_name": names.get(str(ph)) or booking_names.get(str(bid)) or None,
                        "rooms_booked": int(rb or 1),
                        "stay_dates": [],
                        "rooms": set(),
                        "stay_status": str(st or '')
                    }
                agg[key]["stay_dates"].append(date_iso)
                if rn:
                    try:
                        agg[key]["rooms"].add(str(rn))
                    except Exception:
                        pass
                # unify status highest rank
                if _rank(st) > _rank(agg[key]["stay_status"]):
                    agg[key]["stay_status"] = str(st or '')
            bookings = []
            for bk in agg.values():
                bk_out = {
                    "booking_id": bk["booking_id"],
                    "guest_phone": bk["guest_phone"],
                    "guest_name": bk["guest_name"],
                    "rooms_booked": bk["rooms_booked"],
                    "stay_dates": sorted(bk["stay_dates"]),
                    "rooms": sorted(list(bk["rooms"])) if isinstance(bk["rooms"], set) else [],
                    "stay_status": bk["stay_status"]
                }
                bookings.append(bk_out)
            cursor.close(); conn.close()
            return {"hotel_name": hotel_name, "cutoff": cutoff.isoformat(), "items": items, "bookings": bookings}
        except Exception as e:
            try:
                if 'conn' in locals(): conn.close()
            except Exception:
                pass
            return {"error": str(e)}

    def bulk_checkout_selected_internal(self, hotel_name: str, manager_name: str, items: list[dict]) -> dict:
        """WRITE: Mark selected (booking_id, stay_date) pairs as departed. Readiness unchanged.
        Database operation: write (PostgreSQL).
        """
        try:
            if not isinstance(items, list) or not items:
                return {"error": "No items provided"}
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            # Normalize inputs
            pairs = []
            for it in items:
                try:
                    bid = str((it or {}).get('booking_id') or '')
                    sd = str((it or {}).get('stay_date') or '')[:10]
                    if bid and sd:
                        pairs.append((bid, sd))
                except Exception:
                    continue
            if not pairs:
                cursor.close(); conn.close(); return {"error": "No valid items"}
            # Execute updates in batches
            updated = 0
            for bid, sd in pairs:
                cursor.execute(
                    """
                    UPDATE room_stay
                    SET stay_status = 'departed',
                        checked_out_at = COALESCE(checked_out_at, CURRENT_TIMESTAMP)
                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                    """,
                    (hotel_name, bid, sd)
                )
                updated += cursor.rowcount or 0
            conn.commit(); cursor.close(); conn.close()
            return {"result": "success", "rows_updated": int(updated)}
        except Exception as e:
            try:
                if 'conn' in locals(): conn.rollback(); conn.close()
            except Exception:
                pass
            return {"error": str(e)}

    def update_guest_check_in_status_internal(self, hotel_name: str, manager_name: str, booking_id: str, room_number: str = None, notes: str = "", assignment_id: str = None) -> str:
        """
        Update guest check-in. Rules:
        - Future stay (today < min_date): disallow
        - Past (today > max_date): update room_stay only
        - Today in window: ensure assignment, set readiness to occupied, set room_stay checked_in
        """
        try:
            try:
                from booking_system.booking_manager import booking_manager as _bm
                _bm._ensure_booking_channel()
            except Exception:
                pass
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            try:
                from booking_system.timezone_utils import hotel_local_today
                today = hotel_local_today(hotel_name)
            except Exception:
                return "❌ Missing hotel timezone"
            cursor.execute("SELECT MIN(stay_date), MAX(stay_date) FROM booking_stays WHERE booking_id = %s", (booking_id,))
            row_rng = cursor.fetchone() or (None, None)
            min_date, max_date = row_rng[0], row_rng[1]
            if not min_date or not max_date:
                cursor.close(); conn.close()
                return f"❌ No stay dates found for booking {booking_id}"
            if today < min_date:
                cursor.close(); conn.close()
                return f"❌ Check-in not allowed for future stay dates ({min_date} → {max_date})."
            if today > max_date:
                target_date = max_date
                cursor.execute(
                    """
                    UPDATE room_stay
                    SET stay_status = 'checked_in',
                        checked_in_at = COALESCE(checked_in_at, CURRENT_TIMESTAMP),
                        checked_out_at = NULL
                    WHERE booking_id = %s AND stay_date = %s
                    """,
                    (booking_id, target_date)
                )
                # Also reflect current readiness only if effective operational day (today or yesterday)
                try:
                    from datetime import timedelta as _td
                    is_effective_day = (target_date == today) or (target_date == (today - _td(days=1)))
                    if is_effective_day and room_number:
                        try:
                            cursor.execute(
                                """
                                INSERT INTO room_readiness_status (hotel_name, room_number, readiness_status, changed_by, created_by, updated_by, notes)
                                VALUES (%s, %s, 'occupied', %s, %s, %s, %s)
                                ON CONFLICT (hotel_name, room_number) DO UPDATE SET
                                  readiness_status = 'occupied',
                                  status_changed_at = CURRENT_TIMESTAMP,
                                  changed_by = EXCLUDED.changed_by,
                                  notes = EXCLUDED.notes,
                                  clean_since = NULL
                                """,
                                (hotel_name, room_number, manager_name, manager_name, manager_name, f"Check-in processed on {today} for {booking_id}")
                            )
                        except Exception:
                            pass
                except Exception:
                    pass
                # Booking-wide normalization: ensure all stay_dates for this booking reflect checked_in and have a timestamp
                try:
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET stay_status = 'checked_in',
                            checked_in_at = COALESCE(checked_in_at, CURRENT_TIMESTAMP),
                            checked_out_at = NULL
                        WHERE hotel_name = %s AND booking_id = %s
                          AND LOWER(COALESCE(stay_status,'')) NOT IN ('departed','cancelled')
                        """,
                        (hotel_name, booking_id)
                    )
                except Exception:
                    pass
                conn.commit(); cursor.close(); conn.close()
                try:
                    from booking_system.timezone_utils import hotel_local_today
                    _today_disp = hotel_local_today(hotel_name)
                except Exception:
                    from datetime import datetime, timezone as _tz
                    _today_disp = datetime.now(_tz.utc).date()
                return f"✅ Check-in recorded for {booking_id} (processed {str(_today_disp)})"
            # Today within window → always act on today's date for operational updates (post-midnight check-in)
            target_date = today
            # Within window on target_date → if no room, allow room_stay-only check-in; else perform full flow
            if room_number is None:
                cursor.execute(
                    """SELECT room_number FROM room_number_assignments WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s""",
                    (hotel_name, booking_id, target_date)
                )
                assigned_rooms = [row[0] for row in (cursor.fetchall() or [])]
                if not assigned_rooms:
                    # No room assigned: mark room_stay checked_in only (no readiness/assignment updates)
                    try:
                        cursor.execute(
                    """
                            UPDATE room_stay
                            SET stay_status = 'checked_in',
                                checked_in_at = COALESCE(checked_in_at, CURRENT_TIMESTAMP),
                                checked_out_at = NULL
                            WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                            """,
                            (hotel_name, target_date, booking_id)
                        )
                    except Exception as e:
                        if 'checked_in_at' in str(e):
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                            cursor.execute(
                                """
                                UPDATE room_stay
                                SET stay_status = 'checked_in',
                                    checked_out_at = NULL
                                WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                                """,
                                (hotel_name, target_date, booking_id)
                            )
                        else:
                            raise
                    # Normalize all rows on target_date to checked_in if still pending/assigned
                    try:
                        cursor.execute(
                            """
                            UPDATE room_stay
                            SET stay_status = 'checked_in',
                                checked_in_at = COALESCE(checked_in_at, CURRENT_TIMESTAMP),
                                checked_out_at = NULL
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                              AND LOWER(COALESCE(stay_status,'')) NOT IN ('checked_in','departed','cancelled')
                            """,
                            (hotel_name, booking_id, target_date)
                        )
                    except Exception:
                        pass
                    # Booking-wide normalization: ensure all stay_dates for this booking reflect checked_in and have a timestamp
                    try:
                        cursor.execute(
                            """
                            UPDATE room_stay
                            SET stay_status = 'checked_in',
                                checked_in_at = COALESCE(checked_in_at, CURRENT_TIMESTAMP),
                                checked_out_at = NULL
                            WHERE hotel_name = %s AND booking_id = %s
                              AND LOWER(COALESCE(stay_status,'')) NOT IN ('departed','cancelled')
                            """,
                            (hotel_name, booking_id)
                        )
                    except Exception:
                        pass
                    conn.commit(); cursor.close(); conn.close()
                    return f"✅ Check-in recorded for {booking_id} on {target_date} (no room assigned; readiness unchanged)"
                if len(assigned_rooms) > 1 and not assignment_id:
                    cursor.execute(
                        """
                        SELECT DISTINCT assignment_id, room_number
                        FROM room_number_assignments
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number IS NOT NULL
                        ORDER BY room_number
                        """,
                        (hotel_name, booking_id, target_date)
                    )
                    choices = [{"assignment_id": str(aid or ""), "room_number": str(rn)} for aid, rn in (cursor.fetchall() or [])]
                    cursor.close(); conn.close()
                    listed = ", ".join(f"{c['assignment_id']} room {c['room_number']}" for c in choices)
                    return {"error": f"❌ Check in one assignment at a time. {listed}", "assignments": choices}
                if len(assigned_rooms) == 1 and not assignment_id:
                    room_number = assigned_rooms[0]
            if assignment_id and not room_number:
                cursor.execute(
                    """
                    SELECT room_number FROM room_number_assignments
                    WHERE hotel_name = %s AND booking_id = %s AND assignment_id = %s AND stay_date = %s
                    """,
                    (hotel_name, booking_id, assignment_id, target_date)
                )
                found = cursor.fetchone()
                if not found or not found[0]:
                    cursor.close(); conn.close()
                    return f"❌ No room for assignment {assignment_id} on {target_date}"
                room_number = found[0]
            if room_number and not assignment_id:
                cursor.execute(
                    """
                    SELECT assignment_id FROM room_number_assignments
                    WHERE hotel_name = %s AND booking_id = %s AND room_number = %s AND stay_date = %s
                    """,
                    (hotel_name, booking_id, room_number, target_date)
                )
                found = cursor.fetchone()
                assignment_id = str(found[0]) if found and found[0] else ""
            cursor.execute(
                """SELECT booking_id FROM room_number_assignments WHERE hotel_name = %s AND room_number = %s AND stay_date = %s""",
                (hotel_name, room_number, target_date)
            )
            row_assn = cursor.fetchone()
            if row_assn and row_assn[0] and str(row_assn[0]).strip() != str(booking_id).strip():
                cursor.close(); conn.close()
                return f"❌ Room {room_number} already assigned to {row_assn[0]}"
            cursor.execute(
                """
                UPDATE room_number_assignments
                SET booking_id = %s, assignment_status = 'assigned', updated_by = %s
                WHERE hotel_name = %s AND room_number = %s AND stay_date = %s AND (booking_id IS NULL OR booking_id = %s)
                """,
                (booking_id, manager_name, hotel_name, room_number, target_date, booking_id)
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    """
                    INSERT INTO room_number_assignments (hotel_name, room_type, room_number, stay_date, assignment_status, booking_id, updated_by)
                    VALUES (%s, NULL, %s, %s, 'assigned', %s, %s)
                    """,
                    (hotel_name, room_number, target_date, booking_id, manager_name)
                )
            # Readiness → occupied only if target_date is effective (today or yesterday)
            try:
                from datetime import timedelta as _td
                is_effective_day = (target_date == today) or (target_date == (today - _td(days=1)))
            except Exception:
                is_effective_day = False
            prev_readiness = None; curr_readiness = None
            if is_effective_day:
                cursor.execute("SELECT readiness_status FROM room_readiness_status WHERE hotel_name = %s AND room_number = %s", (hotel_name, room_number))
                prev_row = cursor.fetchone(); prev_readiness = (prev_row[0] if prev_row else None)
                cursor.execute(
                """
                INSERT INTO room_readiness_status (hotel_name, room_number, readiness_status, changed_by, created_by, updated_by, notes)
                VALUES (%s, %s, 'occupied', %s, %s, %s, %s)
                ON CONFLICT (hotel_name, room_number) DO UPDATE SET
                  readiness_status = 'occupied', status_changed_at = CURRENT_TIMESTAMP, changed_by = EXCLUDED.changed_by,
                  notes = EXCLUDED.notes, clean_since = NULL
                """,
                (hotel_name, room_number, manager_name, manager_name, manager_name, f"Check-in: {booking_id}. {notes}".strip())
            )
                cursor.execute("SELECT readiness_status FROM room_readiness_status WHERE hotel_name = %s AND room_number = %s", (hotel_name, room_number))
                r_now = cursor.fetchone(); curr_readiness = (r_now[0] if r_now else None)
            # Verify readiness became 'occupied'; if not, force upsert and log
            try:
                cursor.execute("SELECT readiness_status FROM room_readiness_status WHERE hotel_name = %s AND room_number = %s", (hotel_name, room_number))
                _now = cursor.fetchone()
                if (not _now) or str(_now[0]).strip().lower() != 'occupied':
                    cursor.execute(
                    """
                        INSERT INTO room_readiness_status (hotel_name, room_number, readiness_status, changed_by, created_by, updated_by, notes)
                        VALUES (%s, %s, 'occupied', %s, %s, %s, %s)
                        ON CONFLICT (hotel_name, room_number) DO UPDATE SET
                          readiness_status = 'occupied',
                          status_changed_at = CURRENT_TIMESTAMP,
                          changed_by = EXCLUDED.changed_by,
                          notes = EXCLUDED.notes,
                          clean_since = NULL
                        """,
                        (hotel_name, room_number, manager_name, manager_name, manager_name, f"Check-in(upsert): {booking_id}. {notes}".strip())
                    )
                    try:
                        print(f"READINESS AFTER CHECK-IN UPSERT: hotel={hotel_name} room={room_number} status=occupied")
                    except Exception:
                        pass
            except Exception:
                pass
            # Persist readiness change immediately to avoid rollback by later steps
            try:
                conn.commit()
            except Exception:
                pass
            # The stay bar is this physical room. Check in that room's nights only.
            assignment_id = f"{booking_id}#{room_number}"
            try:
                cursor.execute(
                    """
                    UPDATE room_stay
                    SET stay_status = 'checked_in',
                        assignment_id = %s,
                        checked_in_at = COALESCE(checked_in_at, CURRENT_TIMESTAMP),
                        checked_out_at = NULL
                    WHERE hotel_name = %s AND booking_id = %s AND room_number = %s
                      AND LOWER(COALESCE(stay_status,'')) NOT IN ('departed','cancelled')
                    """,
                    (assignment_id, hotel_name, booking_id, room_number)
                )
                cursor.execute(
                    """
                    UPDATE room_number_assignments
                    SET assignment_id = %s
                    WHERE hotel_name = %s AND booking_id = %s AND room_number = %s
                    """,
                    (assignment_id, hotel_name, booking_id, room_number)
                )
            except Exception as e:
                if 'checked_in_at' in str(e):
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET stay_status = 'checked_in',
                            assignment_id = %s,
                            checked_out_at = NULL
                        WHERE hotel_name = %s AND booking_id = %s AND room_number = %s
                          AND LOWER(COALESCE(stay_status,'')) NOT IN ('departed','cancelled')
                        """,
                        (assignment_id, hotel_name, booking_id, room_number)
                    )
                else:
                    raise
            conn.commit(); cursor.close(); conn.close()
            # Build response
            response = f"✅ Guest Check-In Successful\n"
            response += f"🏨 Hotel: {hotel_name}\n"
            response += f"🚪 Room: {room_number}\n"
            response += f"🔖 Assignment: {assignment_id}\n"
            response += f"📋 Booking: {booking_id}\n"
            response += f"👤 Checked in by: {manager_name}\n"
            response += f"📊 Readiness: {str(prev_readiness).lower() if prev_readiness else 'unknown'} → {str(curr_readiness).lower() if curr_readiness else 'unknown'}\n"
            if notes:
                response += f"📝 Notes: {notes}"
            return response
        except Exception as e:
            if 'conn' in locals():
                try:
                    conn.rollback()
                except Exception:
                    pass
            return f"❌ Error processing check-in: {str(e)}"

    def undo_guest_check_in_internal(self, hotel_name: str, manager_name: str, booking_id: str, room_number: str, mark_clean: bool = True) -> str:
        """Return one checked-in room to assigned. Other rooms on the booking stay as they are."""
        try:
            room_number = str(room_number or "").strip()
            if not room_number:
                return "❌ Room is required"
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE room_stay
                SET stay_status = 'assigned',
                    checked_in_at = NULL,
                    checked_out_at = NULL
                WHERE hotel_name = %s AND booking_id = %s AND room_number = %s
                  AND LOWER(COALESCE(stay_status,'')) = 'checked_in'
                """,
                (hotel_name, booking_id, room_number)
            )
            if cursor.rowcount == 0:
                cursor.close(); conn.close()
                return f"❌ Room {room_number} is not checked in for {booking_id}"
            cursor.execute("SAVEPOINT undo_side")
            try:
                cursor.execute(
                    """
                    UPDATE room_number_assignments
                    SET check_in_time = NULL,
                        check_out_time = NULL,
                        updated_by = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE hotel_name = %s AND booking_id = %s AND room_number = %s
                    """,
                    (manager_name, hotel_name, booking_id, room_number)
                )
                ready = 'clean' if mark_clean else 'dirty'
                cursor.execute(
                    """
                    UPDATE room_readiness_status
                    SET readiness_status = %s,
                        status_changed_at = CURRENT_TIMESTAMP,
                        changed_by = %s,
                        notes = %s
                    WHERE hotel_name = %s AND room_number = %s
                    """,
                    (ready, manager_name, f"Undo check-in: {booking_id}", hotel_name, room_number)
                )
                cursor.execute("RELEASE SAVEPOINT undo_side")
            except Exception:
                cursor.execute("ROLLBACK TO SAVEPOINT undo_side")
            conn.commit(); cursor.close(); conn.close()
            return f"✅ Undo check-in for room {room_number} on {booking_id}"
        except Exception as e:
            if 'conn' in locals():
                try:
                    conn.rollback()
                except Exception:
                    pass
            return f"❌ Error undoing check-in: {str(e)}"

    def reset_stay_state_for_date_internal(self, hotel_name: str, manager_name: str, booking_id: str, stay_date: str) -> str:
        """
        Reset a booking's stay state for a specific date while keeping the room assignment.
        - Clears check-in/check-out timestamps and notes on room_number_assignments for that date
        - Sets room_stay.stay_status to 'assigned' and clears checked_in_at/checked_out_at
        - Does NOT change room_number, assigned_at, or readiness status
        """
        try:
            # Parse target date in hotel-local context
            try:
                y, m, d = [int(x) for x in str(stay_date).split('-')]
                from booking_system.timezone_utils import hotel_local_today
                base_today = hotel_local_today(hotel_name)
                target_date = base_today.replace(year=y, month=m, day=d)
            except Exception:
                return "❌ Invalid date format. Use YYYY-MM-DD"

            conn = self.get_postgresql_connection()
            cursor = conn.cursor()

            # Determine the full set of stay_dates for this booking to ensure booking-level consistency
            dates_to_reset = []
            try:
                cursor.execute(
                    "SELECT DISTINCT stay_date FROM room_stay WHERE booking_id = %s ORDER BY stay_date",
                    (booking_id,)
                )
                dates_to_reset = [r[0] for r in (cursor.fetchall() or [])]
            except Exception:
                dates_to_reset = []
            if not dates_to_reset:
                try:
                    cursor.execute(
                        "SELECT DISTINCT stay_date FROM booking_stays WHERE booking_id = %s ORDER BY stay_date",
                        (booking_id,)
                    )
                    dates_to_reset = [r[0] for r in (cursor.fetchall() or [])]
                except Exception:
                    dates_to_reset = []
            if not dates_to_reset:
                dates_to_reset = [target_date]

            kept_rooms_set = set()
            # For each date, clear assignment timestamps/notes and reset room_stay status to NULL (booking-level reset)
            for td in dates_to_reset:
                cursor.execute(
                    """
                    SELECT room_number
                    FROM room_stay
                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number IS NOT NULL
                    ORDER BY stay_index
                    """,
                    (hotel_name, booking_id, td)
                )
                rs_rooms = [r[0] for r in (cursor.fetchall() or []) if r and r[0] is not None]
                if not rs_rooms:
                    cursor.execute(
                        """
                        SELECT room_number
                        FROM room_number_assignments
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number IS NOT NULL
                        ORDER BY room_number
                        """,
                        (hotel_name, booking_id, td)
                    )
                    rs_rooms = [r[0] for r in (cursor.fetchall() or []) if r and r[0] is not None]
                for rn in rs_rooms:
                    kept_rooms_set.add(str(rn))
                # Clear timestamps/notes on assignment rows for this date
                if rs_rooms:
                    for rn in rs_rooms:
                        try:
                            cursor.execute(
                                """
                                UPDATE room_number_assignments
                                SET check_in_time = NULL,
                                    check_out_time = NULL,
                                    assignment_notes = NULL,
                                    updated_by = %s,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number = %s
                                """,
                                (manager_name, hotel_name, booking_id, td, rn)
                            )
                        except Exception:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                            try:
                                cursor.execute(
                                    """
                                    UPDATE room_number_assignments
                                    SET assignment_notes = NULL,
                                        updated_by = %s
                                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number = %s
                                    """,
                                    (manager_name, hotel_name, booking_id, td, rn)
                                )
                            except Exception:
                                pass
                # Reset room_stay status to 'pending' for booking-level consistency, keep room_number and assigned_at
                try:
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET stay_status = 'pending',
                            checked_in_at = NULL,
                            checked_out_at = NULL
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                        """,
                        (hotel_name, booking_id, td)
                    )
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    try:
                        cursor.execute(
                            """
                            UPDATE room_stay
                            SET stay_status = 'pending'
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                            """,
                            (hotel_name, booking_id, td)
                        )
                    except Exception:
                        pass
            # Ensure booking-wide state is pending and timestamps cleared
            try:
                cursor.execute(
                    """
                    UPDATE room_stay
                    SET stay_status = 'pending',
                        checked_in_at = NULL,
                        checked_out_at = NULL
                    WHERE hotel_name = %s AND booking_id = %s
                    """,
                    (hotel_name, booking_id)
                )
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
            conn.commit(); cursor.close(); conn.close()
            kept_rooms = ", ".join(sorted(kept_rooms_set)) if kept_rooms_set else "NONE"
            return f"✅ Reset stay state for {booking_id} across {len(dates_to_reset)} date(s) (kept room(s) {kept_rooms})"
        except Exception as e:
            if 'conn' in locals():
                try:
                    conn.rollback()
                except Exception:
                    pass
            return f"❌ Error resetting stay: {str(e)}"

    def _release_nights_after_checkout(self, cursor, hotel_name: str, manager_name: str, booking_id: str, today, room_number: str = None) -> None:
        """Early checkout frees nights after today for the room that left."""
        if room_number:
            cursor.execute(
                """
                UPDATE booking_stays
                SET rooms_booked = GREATEST(rooms_booked - 1, 0),
                    booking_status = CASE
                        WHEN rooms_booked - 1 <= 0 THEN 'cancelled'
                        ELSE booking_status
                    END
                WHERE hotel_name = %s AND booking_id = %s AND stay_date > %s
                  AND COALESCE(booking_status, 'confirmed') <> 'cancelled'
                """,
                (hotel_name, booking_id, today)
            )
            cursor.execute(
                """
                UPDATE room_number_assignments
                SET booking_id = NULL,
                    guest_phone = NULL,
                    assignment_status = 'available',
                    updated_by = %s
                WHERE hotel_name = %s AND booking_id = %s AND stay_date > %s AND room_number = %s
                """,
                (manager_name, hotel_name, booking_id, today, room_number)
            )
            cursor.execute(
                """
                UPDATE room_stay
                SET room_number = NULL,
                    stay_status = 'cancelled'
                WHERE hotel_name = %s AND booking_id = %s AND stay_date > %s AND room_number = %s
                """,
                (hotel_name, booking_id, today, room_number)
            )
            return
        cursor.execute(
            """
            UPDATE booking_stays
            SET booking_status = 'cancelled'
            WHERE hotel_name = %s AND booking_id = %s AND stay_date > %s
              AND COALESCE(booking_status, 'confirmed') <> 'cancelled'
            """,
            (hotel_name, booking_id, today)
        )
        cursor.execute(
            """
            UPDATE room_number_assignments
            SET booking_id = NULL,
                guest_phone = NULL,
                assignment_status = 'available',
                updated_by = %s
            WHERE hotel_name = %s AND booking_id = %s AND stay_date > %s
            """,
            (manager_name, hotel_name, booking_id, today)
        )
        cursor.execute(
            """
            UPDATE room_stay
            SET room_number = NULL,
                stay_status = 'cancelled'
            WHERE hotel_name = %s AND booking_id = %s AND stay_date > %s
            """,
            (hotel_name, booking_id, today)
        )

    def update_guest_check_out_status_internal(self, hotel_name: str, manager_name: str, booking_id: str, room_number: str = None, notes: str = "") -> str:
        """
        Internal function to update guest check-out status and room readiness from occupied to dirty.
        Consolidated rules:
        - Ensure the room is currently assigned to this booking for today
        - Mark room_number_assignments check_out_time and notes
        - Set readiness to dirty and room_stay to departed
        If room_number is None, will look up the assigned room for the booking_id and today.
        """
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            try:
                from booking_system.timezone_utils import hotel_local_today
                today = hotel_local_today(hotel_name)
            except Exception:
                return "❌ Missing hotel timezone"
            # Disallow check-out for future stays (first stay_date later than today)
            try:
                cursor.execute("SELECT MIN(stay_date) FROM booking_stays WHERE booking_id = %s", (booking_id,))
                min_row = cursor.fetchone() or (None,)
                first_stay_date = min_row[0]
                if first_stay_date and today < first_stay_date:
                    cursor.close(); conn.close()
                    return f"❌ Check-out not allowed before stay starts ({first_stay_date})"
            except Exception:
                pass
            # Determine target_date for checkout: prefer the most recent assigned date for this booking
            target_date = None
            cursor.execute(
                """
                    SELECT stay_date, room_number
                    FROM room_number_assignments
                    WHERE hotel_name = %s AND booking_id = %s
                    ORDER BY stay_date DESC
                    LIMIT 10
                """,
                (hotel_name, booking_id)
            )
            rows_assigned = cursor.fetchall() or []
            if room_number is None:
                if not rows_assigned:
                    # No assignments at all; allow room_stay-only checkout on the correct stay_date
                    # Choose latest stay_date <= today; fallback to MIN(stay_date); fallback to today
                    no_assign_target_date = None
                    try:
                        cursor.execute(
                            """
                                SELECT MAX(stay_date)
                                FROM booking_stays
                                WHERE booking_id = %s AND stay_date <= %s
                            """,
                            (booking_id, today)
                        )
                        row_td = cursor.fetchone()
                        no_assign_target_date = (row_td or [None])[0]
                    except Exception:
                        no_assign_target_date = None
                    if not no_assign_target_date:
                        try:
                            cursor.execute(
                                """
                                    SELECT MIN(stay_date)
                                    FROM booking_stays
                                    WHERE booking_id = %s
                                """,
                                (booking_id,)
                            )
                            row_min = cursor.fetchone()
                            no_assign_target_date = (row_min or [None])[0]
                        except Exception:
                            no_assign_target_date = None
                    if not no_assign_target_date:
                        no_assign_target_date = today
                    # Perform room_stay-only checkout; readiness unchanged
                    try:
                        cursor.execute(
                            """
                            UPDATE room_stay
                            SET stay_status = 'departed',
                                checked_out_at = COALESCE(checked_out_at, CURRENT_TIMESTAMP)
                            WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                            """,
                            (hotel_name, no_assign_target_date, booking_id)
                        )
                    except Exception as e:
                        if 'checked_out_at' in str(e):
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                            cursor.execute(
                                """
                                UPDATE room_stay
                                SET stay_status = 'departed'
                                WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                                """,
                                (hotel_name, no_assign_target_date, booking_id)
                            )
                        else:
                            raise
                    try:
                        affected = getattr(cursor, 'rowcount', None)
                        if not affected or affected == 0:
                            try:
                                cursor.execute(
                                    """
                                    INSERT INTO room_stay (hotel_name, booking_id, stay_date, stay_status, checked_out_at)
                                    VALUES (%s, %s, %s, 'departed', CURRENT_TIMESTAMP)
                                    ON CONFLICT (hotel_name, booking_id, stay_date)
                                    DO UPDATE SET stay_status = 'departed',
                                                  checked_out_at = COALESCE(room_stay.checked_out_at, EXCLUDED.checked_out_at)
                                    """,
                                    (hotel_name, booking_id, no_assign_target_date)
                                )
                            except Exception as e2:
                                if 'checked_out_at' in str(e2):
                                    try:
                                        conn.rollback()
                                    except Exception:
                                        pass
                                    cursor.execute(
                                        """
                                        INSERT INTO room_stay (hotel_name, booking_id, stay_date, stay_status)
                                        VALUES (%s, %s, %s, 'departed')
                                        ON CONFLICT (hotel_name, booking_id, stay_date)
                                        DO UPDATE SET stay_status = 'departed'
                                        """,
                                        (hotel_name, booking_id, no_assign_target_date)
                                    )
                                else:
                                    raise
                    except Exception:
                        pass
                    # Force-set stay_status to departed to avoid partial updates where only timestamp changes
                    try:
                        cursor.execute(
                            """
                            UPDATE room_stay
                            SET stay_status = 'departed'
                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                            """,
                            (hotel_name, booking_id, no_assign_target_date)
                        )
                    except Exception:
                        pass
                    # Booking-wide normalization: mark entire stay as departed and fill checked_out_at
                    try:
                        cursor.execute(
                            """
                            UPDATE room_stay
                            SET stay_status = 'departed',
                                checked_out_at = COALESCE(checked_out_at, CURRENT_TIMESTAMP)
                            WHERE hotel_name = %s AND booking_id = %s
                              AND LOWER(COALESCE(stay_status,'')) <> 'cancelled'
                            """,
                            (hotel_name, booking_id)
                        )
                    except Exception:
                        pass
                    self._release_nights_after_checkout(cursor, hotel_name, manager_name, booking_id, today, None)
                    conn.commit(); cursor.close(); conn.close()
                    return f"✅ Check-out recorded for {booking_id} on {no_assign_target_date} (no room assigned; readiness unchanged)"
                # Choose the first (most recent) assignment; if multiple rooms on latest date, require explicit room_number
                latest_date = rows_assigned[0][0]
                candidates = [r[1] for r in rows_assigned if r[0] == latest_date]
                if len(candidates) > 1:
                    cursor.close(); conn.close()
                    return f"❌ Multiple rooms assigned to booking {booking_id} on {latest_date}: {', '.join([str(x) for x in candidates])}. Please specify room_number."
                target_date = latest_date
                room_number = candidates[0]
            else:
                # If room_number specified, try to find its latest assigned date for this booking
                cursor.execute(
                    """
                        SELECT stay_date
                        FROM room_number_assignments
                        WHERE hotel_name = %s AND booking_id = %s AND room_number = %s
                        ORDER BY stay_date DESC
                        LIMIT 1
                    """,
                    (hotel_name, booking_id, room_number)
                )
                row_td = cursor.fetchone()
                target_date = row_td[0] if row_td else today
            cursor.execute("""
                SELECT guest_phone, check_in_time
                FROM room_number_assignments 
                WHERE hotel_name = %s AND room_number = %s AND stay_date = %s AND booking_id = %s
            """, (hotel_name, room_number, target_date, booking_id))
            result = cursor.fetchone()
            if not result:
                cursor.close()
                conn.close()
                return f"❌ No assignment found for room {room_number} and booking {booking_id} on {today}"
            guest_phone, check_in_time = result
            # If today is not within stay dates for this booking, only update room_stay; skip assignment/readiness
            cursor.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM booking_stays WHERE booking_id = %s AND stay_date = %s
                )
                """,
                (booking_id, today)
            )
            can_checkout_today = bool(cursor.fetchone()[0])
            # When today is within the stay window, enforce target_date = today for all checkout updates
            if can_checkout_today:
                target_date = today
            if not can_checkout_today:
                try:
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET stay_status = 'departed',
                            checked_out_at = COALESCE(checked_out_at, CURRENT_TIMESTAMP)
                        WHERE hotel_name = %s AND room_number = %s AND stay_date = %s AND booking_id = %s
                        """,
                        (hotel_name, room_number, target_date, booking_id)
                    )
                except Exception as e:
                    if 'checked_out_at' in str(e):
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        cursor.execute(
                            """
                            UPDATE room_stay
                            SET stay_status = 'departed'
                            WHERE hotel_name = %s AND room_number = %s AND stay_date = %s AND booking_id = %s
                            """,
                            (hotel_name, room_number, target_date, booking_id)
                        )
                    else:
                        raise
                # Operationally, readiness → dirty only if effective day (today or yesterday)
                try:
                    from datetime import timedelta as _td
                    _is_effective_day = (no_assign_target_date == today) or (no_assign_target_date == (today - _td(days=1)))
                except Exception:
                    _is_effective_day = False
                if _is_effective_day:
                    try:
                        cursor.execute(
                            """
                            UPDATE room_readiness_status
                            SET readiness_status = 'dirty',
                                status_changed_at = CURRENT_TIMESTAMP,
                                changed_by = %s,
                                notes = %s,
                                clean_since = NULL
                            WHERE hotel_name = %s AND room_number = %s
                            """,
                            (manager_name, f"Check-out processed on {today} for {booking_id}", hotel_name, room_number)
                        )
                    except Exception:
                        pass
                try:
                    from booking_system.timezone_utils import hotel_local_today
                    _today_disp = hotel_local_today(hotel_name)
                except Exception:
                    from datetime import datetime as _dt_fallback, timezone as _tz
                    _today_disp = _dt_fallback.now(_tz.utc).date()
                # Booking-wide normalization: mark entire stay as departed and fill checked_out_at
                try:
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET stay_status = 'departed',
                            checked_out_at = COALESCE(checked_out_at, CURRENT_TIMESTAMP)
                        WHERE hotel_name = %s AND booking_id = %s
                          AND LOWER(COALESCE(stay_status,'')) <> 'cancelled'
                        """,
                        (hotel_name, booking_id)
                    )
                except Exception:
                    pass
                self._release_nights_after_checkout(cursor, hotel_name, manager_name, booking_id, today, room_number)
                conn.commit(); cursor.close(); conn.close()
                return f"✅ Check-out recorded for {booking_id} (processed {str(_today_disp)})"
            # If within stay window but there is no assigned room for today, allow room_stay-only checkout
            has_room_today = False
            try:
                cursor.execute(
                    """
                    SELECT 1 FROM room_stay
                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number IS NOT NULL
                    LIMIT 1
                    """,
                    (hotel_name, booking_id, today)
                )
                has_room_today = bool(cursor.fetchone())
            except Exception:
                has_room_today = False
            if not has_room_today:
                try:
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET stay_status = 'departed',
                            checked_out_at = COALESCE(checked_out_at, CURRENT_TIMESTAMP)
                        WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                        """,
                        (hotel_name, today, booking_id)
                    )
                except Exception as e:
                    if 'checked_out_at' in str(e):
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        cursor.execute(
                            """
                            UPDATE room_stay
                            SET stay_status = 'departed'
                            WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                            """,
                            (hotel_name, today, booking_id)
                        )
                    else:
                        raise
                try:
                    affected = getattr(cursor, 'rowcount', None)
                    if not affected or affected == 0:
                        try:
                            cursor.execute(
                                """
                                INSERT INTO room_stay (hotel_name, booking_id, stay_date, stay_status, checked_out_at)
                                VALUES (%s, %s, %s, 'departed', CURRENT_TIMESTAMP)
                                ON CONFLICT (hotel_name, booking_id, stay_date)
                                DO UPDATE SET stay_status = 'departed',
                                              checked_out_at = COALESCE(room_stay.checked_out_at, EXCLUDED.checked_out_at)
                                """,
                                (hotel_name, booking_id, today)
                            )
                        except Exception as e2:
                            if 'checked_out_at' in str(e2):
                                try:
                                    conn.rollback()
                                except Exception:
                                    pass
                                cursor.execute(
                                    """
                                    INSERT INTO room_stay (hotel_name, booking_id, stay_date, stay_status)
                                    VALUES (%s, %s, %s, 'departed')
                                    ON CONFLICT (hotel_name, booking_id, stay_date)
                                    DO UPDATE SET stay_status = 'departed'
                                    """,
                                    (hotel_name, booking_id, today)
                                )
                            else:
                                raise
                except Exception:
                    pass
                # Force-set stay_status to departed to avoid partial updates where only timestamp changes
                try:
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET stay_status = 'departed'
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                        """,
                        (hotel_name, booking_id, today)
                    )
                except Exception:
                    pass
                # Booking-wide normalization: mark entire stay as departed and fill checked_out_at
                try:
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET stay_status = 'departed',
                            checked_out_at = COALESCE(checked_out_at, CURRENT_TIMESTAMP)
                        WHERE hotel_name = %s AND booking_id = %s
                          AND LOWER(COALESCE(stay_status,'')) <> 'cancelled'
                        """,
                        (hotel_name, booking_id)
                    )
                except Exception:
                    pass
                self._release_nights_after_checkout(cursor, hotel_name, manager_name, booking_id, today, room_number)
                conn.commit(); cursor.close(); conn.close()
                return f"✅ Check-out recorded for {booking_id} on {today} (no room assigned; readiness unchanged)"
            # Now we have a room today; proceed with readiness validation and updates
            cursor.execute("""
                SELECT readiness_status 
                FROM room_readiness_status 
                WHERE hotel_name = %s AND room_number = %s
            """, (hotel_name, room_number))
            readiness = cursor.fetchone()
            if not readiness:
                cursor.close()
                conn.close()
                return f"❌ No readiness status record found for room {room_number}"
            current_readiness = readiness[0]
            # If no room assigned today, allow room_stay-only checkout
            if not room_number:
                try:
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET stay_status = 'departed',
                            checked_out_at = COALESCE(checked_out_at, CURRENT_TIMESTAMP)
                        WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                        """,
                        (hotel_name, today, booking_id)
                    )
                except Exception as e:
                    if 'checked_out_at' in str(e):
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        cursor.execute(
                            """
                            UPDATE room_stay
                            SET stay_status = 'departed'
                            WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                            """,
                            (hotel_name, today, booking_id)
                        )
                    else:
                        raise
                self._release_nights_after_checkout(cursor, hotel_name, manager_name, booking_id, today, None)
                conn.commit(); cursor.close(); conn.close()
                return f"✅ Check-out recorded for {booking_id} on {today} (no room assigned; readiness unchanged)"
            # Allow checkout even if readiness is 'dirty' (e.g., previously vacated/unassigned today)
            # Only block if readiness is in an unknown/blocked state
            if current_readiness not in ('occupied', 'clean', 'dirty'):
                cursor.close()
                conn.close()
                return f"❌ Room {room_number} readiness status is '{current_readiness}'. Room is not eligible for check-out"
            cursor.execute("""
                UPDATE room_number_assignments 
                SET check_out_time = CURRENT_TIMESTAMP,
                    assignment_notes = assignment_notes || %s
                WHERE hotel_name = %s AND room_number = %s AND stay_date = %s AND booking_id = %s
            """, (f" | Checked out by {manager_name}. {notes}".strip(), 
                  hotel_name, room_number, target_date, booking_id))
            # Ensure readiness row exists before marking dirty
            cursor.execute("SELECT 1 FROM room_readiness_status WHERE hotel_name = %s AND room_number = %s", (hotel_name, room_number))
            if cursor.fetchone() is None:
                try:
                    cursor.execute(
                        """
                        INSERT INTO room_readiness_status (hotel_name, room_number, readiness_status, changed_by, created_by, updated_by, notes)
                        VALUES (%s, %s, 'occupied', %s, %s, %s, %s)
                        ON CONFLICT (hotel_name, room_number) DO NOTHING
                        """,
                        (hotel_name, room_number, manager_name, manager_name, manager_name, f"Created by check-out bootstrap for {booking_id}")
                    )
                except Exception:
                    pass
            # Readiness → dirty only if effective day (today or yesterday)
            from datetime import timedelta as _td
            _is_effective_day2 = (target_date == today) or (target_date == (today - _td(days=1)))
            if _is_effective_day2:
                cursor.execute("""
                    UPDATE room_readiness_status 
                    SET readiness_status = 'dirty',
                        status_changed_at = CURRENT_TIMESTAMP,
                        changed_by = %s,
                        notes = %s,
                        clean_since = NULL
                    WHERE hotel_name = %s AND room_number = %s
                """, (manager_name, f"Check-out: {booking_id}. Room needs cleaning. {notes}".strip(), hotel_name, room_number))
            # Immediately persist readiness change so timestamp updates reliably
            try:
                conn.commit()
            except Exception:
                pass
            # Read-back to confirm immediately
            try:
                cursor.execute(
                    "SELECT readiness_status, status_changed_at, changed_by FROM room_readiness_status WHERE hotel_name = %s AND room_number = %s",
                    (hotel_name, room_number)
                )
                _rs2 = cursor.fetchone()
                print(f"READINESS AFTER CHECK-OUT: hotel={hotel_name} room={room_number} status={_rs2[0] if _rs2 else 'NA'} changed_at={_rs2[1] if _rs2 else 'NA'} by={_rs2[2] if _rs2 else 'NA'}")
            except Exception:
                pass
            # Verify dirty took effect only on effective operational days
            from datetime import timedelta as _td
            _is_effective_day3 = (target_date == today) or (target_date == (today - _td(days=1)))
            if _is_effective_day3:
                try:
                    cursor.execute("SELECT readiness_status FROM room_readiness_status WHERE hotel_name = %s AND room_number = %s", (hotel_name, room_number))
                    rs2 = cursor.fetchone()
                    if not rs2 or str(rs2[0]).strip().lower() != 'dirty':
                        cursor.execute(
                            """
                            INSERT INTO room_readiness_status (hotel_name, room_number, readiness_status, changed_by, created_by, updated_by, notes)
                            VALUES (%s, %s, 'dirty', %s, %s, %s, %s)
                            ON CONFLICT (hotel_name, room_number) DO UPDATE SET
                              readiness_status = EXCLUDED.readiness_status,
                              status_changed_at = CURRENT_TIMESTAMP,
                              changed_by = EXCLUDED.changed_by,
                              notes = EXCLUDED.notes,
                              clean_since = NULL
                            """,
                            (hotel_name, room_number, manager_name, manager_name, manager_name, f"Upserted by check-out for {booking_id}")
                        )
                except Exception:
                    pass
            try:
                cursor.execute("""
                    UPDATE room_stay
                    SET stay_status = 'departed',
                        checked_out_at = COALESCE(checked_out_at, CURRENT_TIMESTAMP)
                    WHERE hotel_name = %s AND room_number = %s AND stay_date = %s AND booking_id = %s
                """, (hotel_name, room_number, target_date, booking_id))
            except Exception as e:
                if 'checked_out_at' in str(e):
                    try:
                        conn.rollback()
                    except Exception:
                        pass
            cursor.execute("""
                UPDATE room_stay
                SET stay_status = 'departed'
                WHERE hotel_name = %s AND room_number = %s AND stay_date = %s AND booking_id = %s
                    """, (hotel_name, room_number, target_date, booking_id))
            # Ensure all room_stay rows for this booking/date are marked departed and timestamped
            try:
                cursor.execute(
                    """
                    UPDATE room_stay
                    SET stay_status = 'departed',
                        checked_out_at = COALESCE(checked_out_at, CURRENT_TIMESTAMP)
                    WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                    """,
                    (hotel_name, target_date, booking_id)
                )
            except Exception:
                # Fallback to status-only if schema lacks checked_out_at
                try:
                    conn.rollback()
                except Exception:
                    pass
                cursor.execute(
                    """
                UPDATE room_stay
                SET stay_status = 'departed'
                    WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                    """,
                    (hotel_name, target_date, booking_id)
                )
            self._release_nights_after_checkout(cursor, hotel_name, manager_name, booking_id, today, room_number)
            conn.commit()
            cursor.close()
            conn.close()
            response = f"✅ Guest Check-Out Successful\n"
            response += f"🏨 Hotel: {hotel_name}\n"
            response += f"🚪 Room: {room_number}\n"
            response += f"📋 Booking: {booking_id}\n"
            response += f"📞 Guest: {guest_phone}\n"
            response += f"👤 Checked out by: {manager_name}\n"
            try:
                t = datetime.now(timezone.utc)
                response += f"⏰ Check-out time: {t.isoformat()}\n"
            except Exception:
                pass
            response += f"🏠 Stay duration: {(check_in_time.isoformat() if hasattr(check_in_time,'isoformat') else check_in_time)} to now\n"
            response += f"📊 Readiness: occupied → dirty (needs cleaning)\n"
            if notes:
                response += f"📝 Notes: {notes}"
            return response
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Error processing check-out: {str(e)}"

    def update_rooms_by_threshold_auto_internal(self, hotel_name: str, manager_name: str) -> str:
        """
        Internal function to update rooms that exceed their clean threshold (bulk operation).
        """
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT room_number, EXTRACT(DAYS FROM (CURRENT_TIMESTAMP - clean_since)) as days_clean,
                       auto_dirty_threshold_days
                FROM room_readiness_status 
                WHERE hotel_name = %s 
                AND readiness_status = 'clean'
                AND clean_since IS NOT NULL
                AND EXTRACT(DAYS FROM (CURRENT_TIMESTAMP - clean_since)) >= auto_dirty_threshold_days
            """, (hotel_name,))
            rooms_to_dirty = cursor.fetchall()
            if not rooms_to_dirty:
                cursor.close()
                conn.close()
                return f"✅ No rooms at {hotel_name} need auto-dirty"
            dirty_count = 0
            dirty_rooms = []
            for room_number, days_clean, threshold in rooms_to_dirty:
                cursor.execute("""
                    UPDATE room_readiness_status 
                    SET readiness_status = 'dirty',
                        status_changed_at = CURRENT_TIMESTAMP,
                        changed_by = %s,
                        notes = %s,
                        clean_since = NULL
                    WHERE hotel_name = %s AND room_number = %s
                """, (manager_name, f"Auto-dirty after {int(days_clean)} days (threshold: {threshold})", hotel_name, room_number))
                if cursor.rowcount > 0:
                    dirty_count += 1
                    dirty_rooms.append(f"Room {room_number} (clean {int(days_clean)} days, threshold {threshold})")
            conn.commit()
            cursor.close()
            conn.close()
            response = f"🔄 Auto-Dirty Results - {hotel_name}\n"
            response += f"📊 Rooms processed: {dirty_count}/{len(rooms_to_dirty)}\n\n"
            if dirty_rooms:
                response += "✅ ROOMS AUTO-DIRTY:\n"
                for room_info in dirty_rooms:
                    response += f"  • {room_info}\n"
                response += f"\n👤 Changed by: {manager_name}\n"
                response += f"📝 Reason: Exceeded clean threshold (from hotel policy)"
            return response
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Error in auto-dirty operation: {str(e)}"

    def read_room_readiness_status(self, hotel_name: str, room_number:str) -> str:
        """Read readiness status for a room, safely handling NULL clean_since."""
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT readiness_status, status_changed_at, changed_by, notes, clean_since, auto_dirty_threshold_days
                FROM room_readiness_status
                WHERE hotel_name = %s AND room_number = %s;
            """, (hotel_name, room_number))
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if not row:
                return f"❌ No readiness status found for room {room_number} at {hotel_name}"
            readiness_status, status_changed_at, changed_by, notes, clean_since, auto_dirty_threshold_days = row
            if clean_since is not None:
                days_clean = (datetime.now(timezone.utc) - clean_since).days
                clean_since_str = clean_since.isoformat() if hasattr(clean_since,'isoformat') else clean_since
            else:
                days_clean = 0
                clean_since_str = 'Never cleaned'
            result = f"🧹 Room Readiness Status\n"
            result += f"🏨 Hotel: {hotel_name}\n"
            result += f"🚪 Room: {room_number}\n"
            result += f"📊 Status: {readiness_status}\n"
            try:
                status_changed_str = status_changed_at.isoformat() if hasattr(status_changed_at,'isoformat') else status_changed_at
            except Exception:
                status_changed_str = status_changed_at.isoformat() if hasattr(status_changed_at, 'isoformat') else str(status_changed_at)
            result += f"🕐 Status Changed: {status_changed_str}\n"
            result += f"👤 Changed By: {changed_by}\n"
            result += f"📝 Notes: {notes or '-'}\n"
            result += f"🧼 Clean Since: {clean_since_str}\n"
            result += f"📅 Days Clean: {days_clean}\n"
            result += f"🔢 Auto Dirty Threshold: {auto_dirty_threshold_days} days\n"
            return result
        except Exception as e:
            return f"❌ Error reading readiness status: {str(e)}"

    def read_room_readiness_status_json(self, hotel_name: str, room_number: str) -> dict:
        """READ readiness status for a room as structured JSON (preferred for APIs)."""
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT readiness_status, status_changed_at, changed_by, notes, clean_since, auto_dirty_threshold_days
                FROM room_readiness_status
                WHERE hotel_name = %s AND room_number = %s
                """,
                (hotel_name, room_number)
            )
            row = cursor.fetchone()
            cursor.close(); conn.close()
            if not row:
                return {
                    "hotel_name": hotel_name,
                    "room_number": room_number,
                    "readiness_status": None,
                    "status_changed_at": None,
                    "changed_by": None,
                    "notes": None,
                    "clean_since": None,
                    "auto_dirty_threshold_days": None,
                }
            readiness_status, status_changed_at, changed_by, notes, clean_since, auto_days = row
            def _iso(x):
                try:
                    return x.isoformat() if hasattr(x, 'isoformat') else (str(x) if x is not None else None)
                except Exception:
                    return str(x) if x is not None else None
            return {
                "hotel_name": hotel_name,
                "room_number": room_number,
                "readiness_status": readiness_status,
                "status_changed_at": _iso(status_changed_at),
                "changed_by": changed_by,
                "notes": notes,
                "clean_since": _iso(clean_since),
                "auto_dirty_threshold_days": int(auto_days) if auto_days is not None else None,
            }
        except Exception as e:
            try:
                if 'conn' in locals(): conn.close()
            except Exception:
                pass
            return {"error": str(e)}

    def read_all_room_readiness_status_internal(self, hotel_name: str) -> dict:
        """READ all room readiness statuses for a hotel in one call (clean, dirty, cleaning, occupied)."""
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT room_number, readiness_status, status_changed_at, changed_by
                FROM room_readiness_status
                WHERE hotel_name = %s
                ORDER BY room_number
                """,
                (hotel_name,)
            )
            rows = cursor.fetchall()
            cursor.close(); conn.close()
            items = []
            for rn, status, changed_at, changed_by in rows:
                ts = (changed_at.isoformat() if hasattr(changed_at,'isoformat') else changed_at) if changed_at else ""
                items.append({
                    "room_number": rn,
                    "readiness_status": status,
                    "status_changed_at": ts,
                    "changed_by": changed_by,
                })
            return {"hotel_name": hotel_name, "items": items}
        except Exception as e:
            try:
                if 'conn' in locals(): conn.close()
            except Exception:
                pass
            return {"hotel_name": hotel_name, "items": [], "error": str(e)}

    def read_room_number_assignments_calendar_internal(self, hotel_name: str, start_date: str, end_date: str, room_type: str = None, room_numbers: list = None) -> dict:
        """READ calendar-style room number assignments for a hotel between start_date and end_date (inclusive)."""
        try:
            # Parse dates (hotel-local)
            # Resolve hotel-local base date first so timezone errors are not masked as date format errors
            try:
                from booking_system.timezone_utils import hotel_local_today
                base_today = hotel_local_today(hotel_name)
            except Exception:
                return {"error": "Missing hotel timezone"}
            # Normalize and parse YYYY-MM-DD from possible ISO strings
            def _norm_ymd(s):
                s = str(s or "").strip().strip('"').strip("'")
                if 'T' in s:
                    s = s.split('T', 1)[0]
                if len(s) >= 10:
                    s = s[:10]
                return s
            try:
                sd = _norm_ymd(start_date)
                ed = _norm_ymd(end_date)
                y1, m1, d1 = [int(x) for x in sd.split('-')]
                y2, m2, d2 = [int(x) for x in ed.split('-')]
                start = base_today.replace(year=y1, month=m1, day=d1)
                end = base_today.replace(year=y2, month=m2, day=d2)
                start_minus = start - timedelta(days=1)
            except Exception:
                return {"error": "Invalid date format. Use YYYY-MM-DD"}
            if end < start:
                return {"error": "end_date must be on or after start_date"}

            conn = self.get_postgresql_connection()
            cursor = conn.cursor()

            # Expand query window by one day earlier to compute checkout fragments for the first visible day
            params = [hotel_name, start_minus, end]
            filters = []
            if room_type:
                filters.append("room_type = %s")
                params.append(room_type)
            if room_numbers:
                placeholders = ",".join(["%s"] * len(room_numbers))
                filters.append(f"room_number IN ({placeholders})")
                params.extend(room_numbers)

            where_clause = " AND ".join(["hotel_name = %s", "stay_date BETWEEN %s AND %s"] + filters)
            query = f"""
                SELECT room_number, COALESCE(room_type, ''), stay_date, assignment_status, booking_id, guest_phone, assignment_notes
                FROM room_number_assignments
                WHERE {where_clause}
                ORDER BY room_number, stay_date
            """
            cursor.execute(query, params)
            rows = cursor.fetchall()
            channel_map = {}
            try:
                bid_list = sorted({str(r[4]) for r in rows if r and r[4]})
                if bid_list:
                    placeholders = ",".join(["%s"] * len(bid_list))
                    cursor.execute(
                        f"SELECT booking_id, channel FROM room_bookings WHERE hotel_name = %s AND booking_id IN ({placeholders})",
                        [hotel_name] + bid_list,
                    )
                    for b, ch in (cursor.fetchall() or []):
                        channel_map[str(b)] = str(ch or "")
            except Exception:
                channel_map = {}

            # Fetch stay_status for each booking/date/room from room_stay, inclusive of start-1
            stay_params = [hotel_name, start_minus, end]
            stay_query = (
                "SELECT booking_id, stay_date, room_number, stay_status "
                "FROM room_stay WHERE hotel_name = %s AND stay_date BETWEEN %s AND %s"
            )
            cursor.execute(stay_query, stay_params)
            stay_rows = cursor.fetchall() or []
            # Status belongs to the physical room row. Two rooms on one booking stay independent.
            day_status_map = {}
            def _rank(st):
                s = (str(st or '').strip().lower())
                return 2 if s == 'checked_in' else (1 if s == 'assigned' else (0 if s == 'pending' else -1))
            for bid, sdate, rnum, sstat in stay_rows:
                key = (str(bid), str(rnum or ''), sdate.isoformat())
                val = str(sstat or '').strip().lower()
                if key in day_status_map:
                    if _rank(val) > _rank(day_status_map[key]):
                        day_status_map[key] = val
                else:
                    day_status_map[key] = val

            # Build guest_name map from MongoDB by guest_phone, then by booking when the phone is absent
            name_map = {}
            booking_names = {}
            try:
                phones = sorted({str(r[5]) for r in rows if r[5]})
                booking_ids = sorted({str(r[4]) for r in rows if r and r[4]})
                import pymongo, os
                mongo = pymongo.MongoClient(os.getenv('MONGO_URL'))
                db = mongo.get_database('leon_hotel_memory')
                if phones:
                    coll = db.get_collection('guests')
                    for doc in coll.find({"guest_phone": {"$in": phones}}, {"guest_phone": 1, "guest_name": 1}):
                        ph = str(doc.get('guest_phone'))
                        nm = str(doc.get('guest_name') or '').strip()
                        if ph and ph not in name_map and nm:
                            name_map[ph] = nm
                if booking_ids:
                    for doc in db.get_collection('guest_bookings').find({"hotel_name": hotel_name, "booking_id": {"$in": booking_ids}}, {"_id": 0, "booking_id": 1, "guest_name": 1}):
                        bid = str(doc.get("booking_id") or "")
                        nm = str(doc.get("guest_name") or "").strip()
                        if bid and nm:
                            booking_names[bid] = nm
                try:
                    mongo.close()
                except Exception:
                    pass
            except Exception:
                # Non-fatal if Mongo is unavailable
                name_map = {}
                booking_names = {}

            # Build days list
            days = []
            d = start
            while d <= end:
                days.append(d.isoformat())
                d += timedelta(days=1)

            # Build rooms map (visible range) and full map (start-1 .. end) for fragmentation
            rooms_map: Dict[str, dict] = {}
            rooms_full: Dict[str, dict] = {}
            for room_number, rt, stay_date, status, booking_id, guest_phone, assignment_notes in rows:
                key = str(room_number)
                entry_date = stay_date.isoformat()
                # full map (for computing start/end across boundary)
                if key not in rooms_full:
                    rooms_full[key] = {"room_number": key, "room_type": rt, "entries": {}}
                stay_status_val = None
                if booking_id:
                    d_key = (str(booking_id), key, entry_date)
                    stay_status_val = day_status_map.get(d_key)
                resolved_name = name_map.get(str(guest_phone)) or booking_names.get(str(booking_id)) or None
                rooms_full[key]["entries"][entry_date] = {
                    "status": status,
                    "booking_id": booking_id,
                    "guest_phone": guest_phone,
                    "guest_name": resolved_name,
                    "channel": channel_map.get(str(booking_id), "") if booking_id else "",
                    "stay_status": stay_status_val,
                    "notes": assignment_notes
                }
                # visible range entries only
                if start <= stay_date <= end:
                    if key not in rooms_map:
                        rooms_map[key] = {"room_number": key, "room_type": rt, "entries": {}}
                    rooms_map[key]["entries"][entry_date] = rooms_full[key]["entries"][entry_date]

            # Backfill from room_stay in case room_number_assignments lacks rows (e.g., assign from NO ROOM)
            try:
                # Preload room_type by (booking_id, stay_date) from booking_stays within visible range
                bs_type_map: Dict[tuple, str] = {}
                try:
                    cursor.execute(
                        """
                        SELECT booking_id, stay_date, room_type
                        FROM booking_stays
                        WHERE hotel_name = %s AND stay_date BETWEEN %s AND %s
                        """,
                        (hotel_name, start, end)
                    )
                    for _bid, _sd, _rt in (cursor.fetchall() or []):
                        bs_type_map[(str(_bid), (_sd.isoformat() if hasattr(_sd,'isoformat') else str(_sd)))] = str(_rt or '')
                except Exception:
                    bs_type_map = {}
                cursor.execute(
                    """
                    SELECT rs.stay_date, rs.room_number, rs.booking_id
                    FROM room_stay rs
                    WHERE rs.hotel_name = %s AND rs.stay_date BETWEEN %s AND %s AND rs.room_number IS NOT NULL
                    """,
                    (hotel_name, start_minus, end)
                )
                rs_fill = cursor.fetchall() or []
                # Build a quick map for booking_id -> guest_phone
                bid_set = sorted({str(bid) for _, _, bid in rs_fill if bid})
                bid_to_phone: Dict[str, str] = {}
                if bid_set:
                    try:
                        ph_params = bid_set
                        placeholders = ",".join(["%s"] * len(ph_params))
                        cursor.execute(f"SELECT booking_id, guest_phone FROM room_bookings WHERE booking_id IN ({placeholders})", ph_params)
                        for b, ph in (cursor.fetchall() or []):
                            bid_to_phone[str(b)] = (str(ph) if ph is not None else None)
                    except Exception:
                        bid_to_phone = {}
                existing_keys = set()
                for rn, data in rooms_full.items():
                    for d_iso in (data.get("entries") or {}).keys():
                        existing_keys.add((rn, d_iso))
                for sdate, rnum, bid in rs_fill:
                    rn_key = str(rnum)
                    d_iso = sdate.isoformat() if hasattr(sdate, 'isoformat') else str(sdate)
                    if (rn_key, d_iso) in existing_keys:
                        continue
                    # Initialize room containers if needed
                    if rn_key not in rooms_full:
                        # Fill room_type from booking_stays if available
                        inferred_rt = bs_type_map.get((str(bid), d_iso), "")
                        rooms_full[rn_key] = {"room_number": rn_key, "room_type": inferred_rt, "entries": {}}
                    else:
                        # If room_type empty, try to fill
                        try:
                            if not rooms_full[rn_key].get("room_type"):
                                rooms_full[rn_key]["room_type"] = bs_type_map.get((str(bid), d_iso), rooms_full[rn_key].get("room_type") or "")
                        except Exception:
                            pass
                    stay_status_val = day_status_map.get((str(bid), rn_key, d_iso))
                    rooms_full[rn_key]["entries"][d_iso] = {
                        "status": 'assigned',
                        "booking_id": bid,
                        "guest_phone": bid_to_phone.get(str(bid)),
                        "guest_name": name_map.get(str(bid_to_phone.get(str(bid)) or "")) or booking_names.get(str(bid)) or None,
                        "channel": channel_map.get(str(bid), ""),
                        "stay_status": stay_status_val,
                        "notes": None
                    }
                    if start <= sdate <= end:
                        if rn_key not in rooms_map:
                            rooms_map[rn_key] = {"room_number": rn_key, "room_type": rooms_full[rn_key].get("room_type") or "", "entries": {}}
                        else:
                            # Ensure propagated room_type is present
                            try:
                                if not rooms_map[rn_key].get("room_type"):
                                    rooms_map[rn_key]["room_type"] = rooms_full[rn_key].get("room_type") or ""
                            except Exception:
                                pass
                        rooms_map[rn_key]["entries"][d_iso] = rooms_full[rn_key]["entries"][d_iso]
            except Exception:
                pass

            # No halves/fills: only return raw stay_date entries; we still queried from start-1 to end
            # so consumers can detect cross-boundary conditions if needed.

            # Build UNASSIGNED map by per-slot deficits: rooms_booked - assigned_count
            unassigned_by_type: Dict[str, Dict[str, list]] = {}
            try:
                conn.commit()
                conn2 = self.get_postgresql_connection()
                cur2 = conn2.cursor()
                # Build required slots per (room_type, stay_date, booking_id)
                ua_params_req = [hotel_name, start, end]
                ua_filters_req = []
                if room_type:
                    ua_filters_req.append("bs.room_type = %s")
                    ua_params_req.append(room_type)
                ua_where_req = " AND ".join(["bs.hotel_name = %s", "bs.stay_date BETWEEN %s AND %s"] + ua_filters_req)
                q_req = f"""
                    SELECT bs.room_type,
                           bs.stay_date,
                           bs.booking_id,
                           rb.guest_phone,
                           SUM(bs.rooms_booked) AS required_slots_bs,
                           MAX(COALESCE(rb.rooms_booked, 1)) AS required_slots_rb
                    FROM booking_stays bs
                    JOIN room_bookings rb
                      ON rb.booking_id = bs.booking_id AND rb.hotel_name = bs.hotel_name
                    WHERE {ua_where_req}
                      AND COALESCE(bs.booking_status, 'confirmed') <> 'cancelled'
                      AND COALESCE(rb.booking_status, 'confirmed') <> 'cancelled'
                    GROUP BY bs.room_type, bs.stay_date, bs.booking_id, rb.guest_phone
                    ORDER BY bs.room_type, bs.stay_date, bs.booking_id
                """
                cur2.execute(q_req, ua_params_req)
                req_rows_raw = cur2.fetchall() or []
                # Fallback required slots from room_stay counts in case booking_stays under-reports
                cur2.execute(
                    """
                    SELECT stay_date, booking_id, COUNT(*) AS slot_cnt
                    FROM room_stay
                    WHERE hotel_name = %s AND stay_date BETWEEN %s AND %s
                    GROUP BY stay_date, booking_id
                    """,
                    (hotel_name, start, end)
                )
                rs_slot_rows = cur2.fetchall() or []
                slot_map = { (str(bid), sd.isoformat() if hasattr(sd,'isoformat') else str(sd)) : int(cnt or 0)
                             for sd, bid, cnt in rs_slot_rows }
                # Augment name_map with guest_phones from required rows (not only assignments)
                try:
                    phones_from_req = sorted({str(r[3]) for r in req_rows_raw if r[3]})
                    missing = [ph for ph in phones_from_req if ph not in name_map]
                    if missing:
                        import pymongo, os
                        mongo2 = pymongo.MongoClient(os.getenv('MONGO_URL'))
                        db2 = mongo2.get_database('leon_hotel_memory')
                        coll2 = db2.get_collection('guests')
                        for doc in coll2.find({"guest_phone": {"$in": missing}}, {"guest_phone": 1, "guest_name": 1}):
                                ph2 = str(doc.get('guest_phone'))
                                nm2 = str(doc.get('guest_name') or '').strip()
                                if ph2 and ph2 not in name_map and nm2:
                                    name_map[ph2] = nm2
                        try:
                            mongo2.close()
                        except Exception:
                            pass
                except Exception:
                    pass
                # Build assigned slots per (stay_date, booking_id) from authoritative assignments table
                ua_params_asg = [hotel_name, start, end]
                q_asg = """
                    SELECT rna.stay_date,
                           rna.booking_id,
                           COUNT(*) FILTER (WHERE COALESCE(rna.assignment_status,'assigned') = 'assigned' AND rna.booking_id IS NOT NULL) AS assigned_slots
                    FROM room_number_assignments rna
                    LEFT JOIN room_bookings rb
                      ON rb.booking_id = rna.booking_id AND rb.hotel_name = rna.hotel_name
                    WHERE rna.hotel_name = %s
                      AND rna.stay_date BETWEEN %s AND %s
                      AND rna.booking_id IS NOT NULL
                      AND COALESCE(rb.booking_status, 'confirmed') <> 'cancelled'
                    GROUP BY rna.stay_date, rna.booking_id
                """
                cur2.execute(q_asg, ua_params_asg)
                asg_rows = cur2.fetchall() or []
                assigned_map: Dict[tuple, int] = {}
                for sdate, bid, assigned_slots in asg_rows:
                    if not bid:
                        continue
                    assigned_map[(sdate.isoformat(), str(bid))] = int(assigned_slots or 0)
                # Compute a single booking-level status across entire booking (one truth across stay_dates)
                booking_status_map: Dict[str, str] = {}
                try:
                    bid_set2 = sorted({str(r[2]) for r in req_rows_raw if r and r[2]})
                    if bid_set2:
                        placeholders_bs = ",".join(["%s"] * len(bid_set2))
                        params_bs = [hotel_name] + bid_set2
                        q_bs = (
                            "SELECT rs.booking_id, "
                            "MAX(CASE WHEN LOWER(COALESCE(rs.stay_status,'')) = 'checked_in' THEN 1 ELSE 0 END) AS any_ci, "
                            "MAX(CASE WHEN LOWER(COALESCE(rs.stay_status,'')) IN ('departed','checked_out') THEN 1 ELSE 0 END) AS any_dep "
                            "FROM room_stay rs "
                            "WHERE rs.hotel_name = %s AND rs.booking_id IN (" + placeholders_bs + ") "
                            "GROUP BY rs.booking_id"
                        )
                        cur2.execute(q_bs, params_bs)
                        for _bid, any_ci, any_dep in (cur2.fetchall() or []):
                            status_val = 'pending'
                            try:
                                if int(any_ci or 0) > 0:
                                    status_val = 'checked_in'
                                elif int(any_dep or 0) > 0:
                                    status_val = 'departed'
                            except Exception:
                                status_val = 'pending'
                            booking_status_map[str(_bid)] = status_val
                except Exception:
                    booking_status_map = {}

                # Compute deficits and populate unassigned_by_type using union of required (bs/rb) and assigned_map keys
                # Map per-date required slots from booking_stays rows
                bs_required_map = {}
                for rt, sdate, bid, gphone, required_bs, required_rb in req_rows_raw:
                    key = (str(bid), sdate.isoformat())
                    bs_required_map[key] = int(required_bs or 0)
                # Collect all booking_ids and (bid, day) pairs
                all_pairs = set(bs_required_map.keys()) | set((str(b), d) for (d, b) in [(k[0], k[1]) for k in []])
                # Also include keys from assigned_map
                for (day, bid_s) in assigned_map.keys():
                    all_pairs.add((bid_s, day))
                # Build rb info for all bids
                all_bids = sorted({p[0] for p in all_pairs})
                rb_guest_map = {}
                rb_rooms_booked_map = {}
                rb_channel_map = {}
                if all_bids:
                    try:
                        placeholders = ",".join(["%s"] * len(all_bids))
                        cur2.execute(
                            f"SELECT booking_id, guest_phone, COALESCE(rooms_booked,1), channel FROM room_bookings WHERE booking_id IN ({placeholders})",
                            all_bids
                        )
                        for b, ph, rb_rooms, ch in (cur2.fetchall() or []):
                            rb_guest_map[str(b)] = ph
                            rb_rooms_booked_map[str(b)] = int(rb_rooms or 1)
                            rb_channel_map[str(b)] = str(ch or "")
                    except Exception:
                        rb_guest_map = {}
                        rb_rooms_booked_map = {}
                # Now compute deficits per pair
                for bid_s, day in sorted(all_pairs):
                    if not bid_s or bid_s not in rb_rooms_booked_map and (bid_s, day) not in bs_required_map:
                        continue
                    assigned_slots = assigned_map.get((day, bid_s), 0)
                    rb_req = int(rb_rooms_booked_map.get(bid_s, 0))
                    bs_req = int(bs_required_map.get((bid_s, day), 0))
                    required_slots = max(rb_req, bs_req)
                    deficit = max(required_slots - int(assigned_slots or 0), 0)
                    if deficit <= 0:
                        continue
                    # Resolve room_type and guest_phone
                    # room_type by (bid, day) from bs_type_map; fallback empty
                    rt = bs_type_map.get((bid_s, day), "") if 'bs_type_map' in locals() else ""
                    key_rt = str(rt or '').upper()
                    if key_rt not in unassigned_by_type:
                        unassigned_by_type[key_rt] = {}
                    if day not in unassigned_by_type[key_rt]:
                        unassigned_by_type[key_rt][day] = []
                    gphone = rb_guest_map.get(bid_s)
                    stay_status_val = 'pending'
                    for _ in range(deficit):
                        unassigned_by_type[key_rt][day].append({
                            "booking_id": bid_s,
                        "guest_phone": (str(gphone) if gphone is not None else None),
                            "guest_name": name_map.get(str(gphone)) or booking_names.get(bid_s) or None,
                            "channel": rb_channel_map.get(bid_s, ""),
                            "stay_status": stay_status_val
                    })
                try:
                    cur2.close()
                    conn2.close()
                except Exception:
                    pass
            except Exception:
                # If unassigned derivation fails, proceed without it
                unassigned_by_type = {}

            cursor.close()
            conn.close()

            # Build contiguous out-of-order blocks per room as convenience for UIs
            ooo_blocks = {}
            try:
                for rnum, data in rooms_map.items():
                    entries = data.get("entries") or {}
                    dates = sorted(entries.keys())
                    def _add_days(iso, n):
                        try:
                            d = datetime.strptime(iso, "%Y-%m-%d").date()
                        except Exception:
                            d = datetime.now(timezone.utc).date()
                        return (d + timedelta(days=n)).isoformat()
                    i = 0
                    blocks = []
                    while i < len(dates):
                        d = dates[i]
                        st = str((entries.get(d) or {}).get("status") or '').lower()
                        if st in ('out-of-order','out_of_order','outoforder'):
                            start_b = d
                            end_b = d
                            j = i + 1
                            reason_val = (entries.get(d) or {}).get("notes")
                            while j < len(dates):
                                nx = dates[j]
                                nxst = str((entries.get(nx) or {}).get("status") or '').lower()
                                if nx == _add_days(end_b, 1) and nxst in ('out-of-order','out_of_order','outoforder'):
                                    # enforce identical reason across block (after sanitization)
                                    nx_reason_raw = (entries.get(nx) or {}).get("notes")
                                    def _san(r):
                                        if not r: return ''
                                        try:
                                            import re
                                            r2 = re.sub(r"^\s*out\s*-?\s*of\s*-?\s*order\s*:\s*", "", str(r), flags=re.IGNORECASE)
                                            return r2.strip()
                                        except Exception:
                                            return str(r)
                                    base_san = _san(reason_val)
                                    nxt_san = _san(nx_reason_raw)
                                    if base_san and nxt_san and base_san != nxt_san:
                                        break
                                    end_b = nx
                                    if not reason_val and nx_reason_raw:
                                        reason_val = nx_reason_raw
                                    j += 1
                                else:
                                    break
                            blocks.append({"start": start_b, "end": end_b, "reason": reason_val})
                            i = j
                        else:
                            i += 1
                    if blocks:
                        ooo_blocks[rnum] = blocks
                        # Also attach into the room object for easier client consumption
                        try:
                            rooms_map[rnum]["ooo_linear"] = blocks
                        except Exception:
                            pass
            except Exception:
                ooo_blocks = {}

            # Physical rooms live in room_encyclopedia. Include them even when no stay is assigned.
            try:
                import pymongo, os
                mongo_rooms = pymongo.MongoClient(os.getenv("MONGO_URL"))
                enc = mongo_rooms.get_database("leon_hotel_memory").get_collection("room_encyclopedia")
                room_query = {"hotel_name": hotel_name, "has_room_number": True}
                if room_type:
                    room_query["room_type"] = room_type
                if room_numbers:
                    room_query["room_number"] = {"$in": [str(x) for x in room_numbers]}
                for doc in enc.find(room_query, {"room_number": 1, "room_type": 1}):
                    rn_key = str(doc.get("room_number") or "")
                    rt_val = str(doc.get("room_type") or "")
                    if not rn_key:
                        continue
                    if rn_key not in rooms_full:
                        rooms_full[rn_key] = {"room_number": rn_key, "room_type": rt_val, "entries": {}}
                    elif not rooms_full[rn_key].get("room_type") and rt_val:
                        rooms_full[rn_key]["room_type"] = rt_val
                try:
                    mongo_rooms.close()
                except Exception:
                    pass
            except Exception:
                pass

            # Prepare response rooms INCLUSIVE of start-1 day entries
            rooms_out = []
            for rnum, data in rooms_full.items():
                ents = data.get("entries", {}) or {}
                # filter to [start_minus .. end]
                filtered = {}
                for d_iso, payload in ents.items():
                    try:
                        d_dt = datetime.strptime(d_iso, "%Y-%m-%d").date()
                    except Exception:
                        continue
                    if (start - timedelta(days=1)) <= d_dt <= end:
                        filtered[d_iso] = payload
                out = {
                    "room_number": data.get("room_number"),
                    "room_type": data.get("room_type"),
                    "entries": filtered
                }
                # Attach any computed OOO blocks from visible map if present
                if rnum in rooms_map and rooms_map[rnum].get("ooo_linear"):
                    out["ooo_linear"] = rooms_map[rnum].get("ooo_linear")
                rooms_out.append(out)

            return {
                "hotel_name": hotel_name,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "days": days,
                "rooms": rooms_out,
                "ooo_blocks": ooo_blocks,
                "unassigned_by_type": unassigned_by_type
            }
        except Exception as e:
            try:
                if 'conn' in locals():
                    conn.rollback()
            except Exception:
                pass
            return {"error": str(e)}

    def read_booking_preview_internal(self, booking_id: str) -> dict:
        """READ minimal booking preview for tooltip:
        - booking_id, hotel_name, guest_phone, guest_email
        - total_price (hotel currency), booking_notes
        - check_in_date, check_out_date (derived from booking_stays range)
        """
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            # Guest and hotel from room_bookings
            cursor.execute(
                """
                SELECT guest_phone, hotel_name
                FROM room_bookings
                WHERE booking_id = %s
                """,
                (booking_id,)
            )
            row = cursor.fetchone()
            if not row:
                cursor.close(); conn.close()
                return {"error": f"Booking {booking_id} not found"}
            guest_phone, hotel_name = row
            # Prefer payment_tracking.total_amount (final total). Fallback to room_bookings.total_amount. If both missing, derive subtotal
            pt_total = None; pt_guest_phone = None
            try:
                cursor.execute(
                    """
                    SELECT total_amount, guest_phone_number
                    FROM payment_tracking
                    WHERE booking_id = %s
                    LIMIT 1
                    """,
                    (booking_id,)
                )
                pt_row = cursor.fetchone()
                if pt_row:
                    pt_total = pt_row[0]
                    pt_guest_phone = pt_row[1]
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                pt_total = None

            # Derive stay range (check_in_date, check_out_date exclusive)
            cursor.execute(
                """
                SELECT MIN(stay_date), MAX(stay_date)
                FROM booking_stays
                WHERE booking_id = %s
                """,
                (booking_id,)
            )
            rng = cursor.fetchone() or (None, None)
            check_in_date = rng[0]
            check_out_date = None
            if rng[1] is not None:
                try:
                    from datetime import timedelta
                    check_out_date = rng[1] + timedelta(days=1)
                except Exception:
                    check_out_date = rng[1]

            cursor.execute(
                """
                SELECT total_amount, booking_notes
                FROM room_bookings
                WHERE booking_id = %s
                """,
                (booking_id,)
            )
            rb_row = cursor.fetchone()
            total_amount = float(rb_row[0]) if rb_row and rb_row[0] is not None else None
            booking_notes = (rb_row[1] if rb_row else None) or None
            if pt_total is not None:
                try:
                    total_amount = float(pt_total)
                except Exception:
                    total_amount = pt_total
            if (not guest_phone) and pt_guest_phone:
                guest_phone = pt_guest_phone
            if total_amount is None:
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(bs.rooms_booked * COALESCE(dp.base_price, 0)), 0)
                    FROM booking_stays bs
                    LEFT JOIN daily_pricing dp
                    ON dp.hotel_name = bs.hotel_name
                    AND dp.room_type = bs.room_type
                    AND dp.stay_date = bs.stay_date
                    WHERE bs.booking_id = %s
                    """,
                    (booking_id,)
                )
                total_amount = float(cursor.fetchone()[0] or 0)
            cursor.close(); conn.close()
            # Guest email from MongoDB
            guest_email = None
            try:
                db = self.get_mongodb_connection()
                doc = db.get_collection('guests').find_one({"guest_phone": str(guest_phone)}, {"guest_phone": 1, "guest_email": 1, "email": 1})
                if doc:
                    guest_email = doc.get('guest_email') or doc.get('email') or None
            except Exception:
                guest_email = None
            return {
                "booking_id": booking_id,
                "hotel_name": hotel_name,
                "guest_phone": guest_phone,
                "guest_email": guest_email,
                "total_price": float(total_amount),
                "booking_notes": booking_notes,
                "currency": None,
                "check_in_date": (check_in_date.isoformat() if hasattr(check_in_date,'isoformat') else check_in_date),
                "check_out_date": (check_out_date.isoformat() if hasattr(check_out_date,'isoformat') else check_out_date)
            }
        except Exception as e:
            try:
                if 'conn' in locals(): conn.close()
            except Exception:
                pass
            return {"error": str(e)}

    def _international_phone(self, country_code: str, phone: str) -> str:
        dial = str(country_code or "").strip()
        local = "".join(ch for ch in str(phone or "") if ch.isdigit() or ch == "+")
        dial_digits = dial[1:] if dial.startswith("+") else ""
        if not dial_digits.isdigit() or dial_digits.startswith("0"):
            return ""
        if local.startswith(dial):
            return local
        if local.startswith("+"):
            return ""
        if local.startswith("0"):
            local = local[1:]
        if not local.isdigit():
            return ""
        return dial + local

    def _whatsapp_to(self, guest_phone: str) -> str:
        """Country calling code plus national number, trunk 0 removed. Guest profiles stay unchanged."""
        digits = "".join(ch for ch in str(guest_phone or "") if ch.isdigit())
        dial = ""
        for code in _WHATSAPP_DIAL_CODES:
            if digits.startswith(code):
                dial = code
                break
        national = digits[len(dial):]
        if national.startswith("0"):
            national = national[1:]
        if not dial or not national.isdigit():
            return ""
        return "+" + dial + national

    def _post_whatsapp(self, payload: dict) -> dict:
        import urllib.error
        import urllib.request
        token = str(os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()
        phone_number_id = str(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
        if not token or not phone_number_id:
            return {"error": "WhatsApp access token is not configured"}
        request = urllib.request.Request(
            "https://graph.facebook.com/v23.0/" + phone_number_id + "/messages",
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {}
            return {"error": str((parsed.get("error") or {}).get("message") or "WhatsApp send failed")}
        except Exception as exc:
            return {"error": str(exc)}
        sent = (body.get("messages") or [{}])[0]
        message_id = str((sent or {}).get("id") or "")
        if not message_id:
            return {"error": "WhatsApp did not accept the message"}
        return {"message_id": message_id}

    def send_whatsapp_text(self, guest_phone: str, text: str) -> dict:
        """Send one free-form WhatsApp text. Meta accepts this only inside an open customer service window."""
        body = str(text or "").strip()
        if not body:
            return {"error": "Message is required"}
        recipient = self._whatsapp_to(guest_phone)
        if not recipient:
            return {"error": "WhatsApp number needs a country code"}
        return self._post_whatsapp({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"body": body},
        })

    def send_whatsapp_template(self, guest_phone: str) -> dict:
        """Send the configured WhatsApp template. A first message to a guest must be a template."""
        template_name = str(os.getenv("WHATSAPP_TEMPLATE_NAME") or "").strip()
        language = str(os.getenv("WHATSAPP_TEMPLATE_LANGUAGE") or "").strip()
        if not template_name:
            return {"error": "WhatsApp template is not configured"}
        if not language:
            return {"error": "WhatsApp template language is not configured"}
        recipient = self._whatsapp_to(guest_phone)
        if not recipient:
            return {"error": "WhatsApp number needs a country code"}
        sent = self._post_whatsapp({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "template",
            "template": {"name": template_name, "language": {"code": language}},
        })
        if sent.get("message_id"):
            sent["template_name"] = template_name
        return sent

    def verify_booking_phone(self, booking_id: str, manager_name: str, guest_phone: str = "", guest_name: str = "", country_code: str = "") -> dict:
        """Create or link the guest profile from the phone and country code on the stay edit."""
        conn = self.get_postgresql_connection()
        cur = conn.cursor()
        cur.execute("SELECT hotel_name, guest_phone FROM room_bookings WHERE booking_id = %s", (booking_id,))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return {"error": "Booking " + booking_id + " not found"}
        hotel_name = str(row[0] or "").strip().upper()
        typed_phone = str(guest_phone or "").strip()
        typed_name = str(guest_name or "").strip()
        typed_dial = str(country_code or "").strip()
        db = self.get_mongodb_connection()
        booking_doc = db.get_collection("guest_bookings").find_one({"hotel_name": hotel_name, "booking_id": booking_id}, {"_id": 0})
        reference_phone = str((booking_doc or {}).get("reference_phone") or "").strip()
        guest_name = str((booking_doc or {}).get("guest_name") or "").strip()
        stored = db.get_collection("ota_reservations").find_one({"hotel_name": hotel_name, "booking_id": booking_id}, {"_id": 0, "payload": 1})
        from booking_system.aiosell_reservation import _guest_name, _guest_phone
        payload = (stored or {}).get("payload") or {}
        if not reference_phone:
            reference_phone = _guest_phone(payload)
        if not guest_name:
            guest_name = _guest_name(payload)
        raw_phone = typed_phone or reference_phone
        name = typed_name or guest_name
        if not typed_dial:
            cur.close()
            conn.close()
            return {"error": "Country code is required"}
        if not raw_phone:
            cur.close()
            conn.close()
            return {"error": "Phone is required"}
        phone = self._international_phone(typed_dial, raw_phone)
        if not phone:
            cur.close()
            conn.close()
            return {"error": "Country code does not match the phone number"}
        if not name:
            cur.close()
            conn.close()
            return {"error": "Guest name is required"}
        sent = self.send_whatsapp_template(phone)
        if sent.get("error"):
            cur.close()
            conn.close()
            return {"error": sent["error"]}
        current = db.get_collection("guests").find_one({"guest_phone": phone, "hotel_name": hotel_name})
        if not current:
            from booking_system.guest_manager import guest_manager
            text = guest_manager.create_guest_profile(hotel_name, manager_name, phone, name)
            if str(text).startswith("❌"):
                cur.close()
                conn.close()
                return {"error": text}
        cur.execute(
            "UPDATE room_bookings SET guest_phone = %s, updated_at = CURRENT_TIMESTAMP, updated_by = %s WHERE booking_id = %s AND hotel_name = %s",
            (phone, manager_name, booking_id, hotel_name),
        )
        cur.execute(
            "UPDATE room_number_assignments SET guest_phone = %s, updated_by = %s WHERE hotel_name = %s AND booking_id = %s",
            (phone, manager_name, hotel_name, booking_id),
        )
        cur.execute(
            "UPDATE payment_tracking SET guest_phone_number = %s WHERE booking_id = %s",
            (phone, booking_id),
        )
        conn.commit()
        cur.close()
        conn.close()
        fields = {"guest_phone": phone, "phone_verified": True, "guest_name": name}
        if reference_phone:
            fields["reference_phone"] = reference_phone
        coll = db.get_collection("guest_bookings")
        if booking_doc:
            coll.update_one({"hotel_name": hotel_name, "booking_id": booking_id}, {"$set": fields})
        else:
            fields["hotel_name"] = hotel_name
            fields["booking_id"] = booking_id
            coll.insert_one(fields)
        from booking_system.guest_manager import guest_manager
        sent_at = datetime.now(timezone.utc).isoformat()
        guest_manager.save_whatsapp_event(sent["message_id"], phone, hotel_name, sent_at, sent["template_name"], "out")
        return {"success": True, "guest_phone": phone, "guest_name": name, "phone_verified": True, "whatsapp_message_id": sent["message_id"]}

    def read_booking_detail_internal(self, booking_id: str, stay_date: str = None) -> dict:
        """READ full booking detail for edit modal: guest, booking, payment sections.
        stay_date (YYYY-MM-DD) is required. All date-scoped values (rooms, check-in status, timestamps,
        can_check_in_today) are computed strictly for that date; no fallbacks.
        """
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT hotel_name, guest_phone, booking_status, booking_notes, total_amount, COALESCE(rate_plan, '')
                FROM room_bookings
                WHERE booking_id = %s
                """,
                (booking_id,)
            )
            rb = cursor.fetchone()
            if not rb:
                cursor.close(); conn.close()
                return {"error": f"Booking {booking_id} not found"}
            hotel_name, guest_phone, booking_status, booking_notes, rb_total, rate_plan = rb

            # Payment summary (prefer payment_tracking; fallback to payment_ledger)
            cursor.execute(
                """
                SELECT payment_status, COALESCE(amount_paid,0), COALESCE(remaining_balance,0), payment_method, COALESCE(total_amount,0)
                FROM payment_tracking
                WHERE booking_id = %s
                """,
                (booking_id,)
            )
            row_pt = cursor.fetchone()
            if row_pt:
                payment_status, amount_paid, remaining_balance, payment_method, pt_total = row_pt
            else:
                # Derive from ledger and room_bookings
                cursor.execute(
                    """
                    SELECT COALESCE(total_amount,0)
                    FROM room_bookings
                    WHERE booking_id = %s
                    """,
                    (booking_id,)
                )
                rb_total_row = cursor.fetchone()
                pt_total = float((rb_total_row or [0])[0] or 0)
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(payment_amount),0) FROM payment_ledger WHERE booking_id = %s
                    """,
                    (booking_id,)
                )
                amount_paid = float((cursor.fetchone() or [0])[0] or 0)
                remaining_balance = max(pt_total - amount_paid, 0.0)
                payment_status = 'completed' if remaining_balance <= 0.00001 and pt_total > 0 else ('partial' if amount_paid > 0 else 'pending')
                try:
                    cursor.execute(
                        """
                        SELECT payment_method, payment_status
                        FROM payment_ledger
                        WHERE booking_id = %s
                        ORDER BY payment_date DESC, payment_seq DESC
                        LIMIT 1
                        """,
                        (booking_id,)
                    )
                    last = cursor.fetchone() or (None, None)
                    payment_method = last[0]
                except Exception:
                    payment_method = None
            # Derive stay range (check_in_date, check_out_date exclusive)
            cursor.execute(
                """
                SELECT MIN(stay_date), MAX(stay_date)
                FROM booking_stays
                WHERE booking_id = %s
                """,
                (booking_id,)
            )
            rng = cursor.fetchone() or (None, None)
            preview_check_in_date = rng[0]
            preview_check_out_date = None
            if rng[1] is not None:
                try:
                    from datetime import timedelta
                    preview_check_out_date = rng[1] + timedelta(days=1)
                except Exception:
                    preview_check_out_date = rng[1]

            # Compute check-in status and rooms for the requested stay_date only (no fallback)
            if not stay_date:
                cursor.close(); conn.close()
                return {"error": "stay_date is required (YYYY-MM-DD)"}
            # Normalize and parse stay_date (accept ISO forms like YYYY-MM-DDTHH:MM:SSZ)
            def _norm_ymd(s):
                s = str(s or "").strip().strip('"').strip("'")
                if 'T' in s:
                    s = s.split('T', 1)[0]
                if len(s) >= 10:
                    s = s[:10]
                return s
            try:
                sd = _norm_ymd(stay_date)
                y, m, d = [int(x) for x in sd.split('-')]
                from booking_system.timezone_utils import hotel_local_today
                base_today = hotel_local_today(hotel_name)
                target_date = base_today.replace(year=y, month=m, day=d)
            except Exception as e:
                cursor.close(); conn.close()
                msg = str(e)
                if 'Missing hotel timezone' in msg:
                    return {"error": "Missing hotel timezone"}
                return {"error": "Invalid date format. Use YYYY-MM-DD"}
            try:
                # Can check-in on target_date?
                cursor.execute(
                    """
                    SELECT EXISTS(SELECT 1 FROM booking_stays WHERE booking_id = %s AND stay_date = %s)
                    """,
                    (booking_id, target_date)
                )
                can_check_in_today = bool(cursor.fetchone()[0])
                # Rooms booked (canonical count) from room_bookings
                rooms_booked_count = None
                try:
                    cursor.execute(
                        """
                        SELECT COALESCE(rooms_booked, 0)
                        FROM room_bookings
                        WHERE booking_id = %s
                        """,
                        (booking_id,)
                    )
                    rb_row = cursor.fetchone()
                    if rb_row is not None:
                        rooms_booked_count = int(rb_row[0] or 0)
                except Exception:
                    rooms_booked_count = None
                cursor.execute(
                    """
                    SELECT room_number, stay_status
                    FROM room_stay
                    WHERE booking_id = %s AND stay_date = %s
                    ORDER BY stay_index
                    """,
                    (booking_id, target_date)
                )
                rs_rows = cursor.fetchall() or []
                # Snapshot baseline slot count before any mutation to preserve NULL slot visibility
                try:
                    cursor.execute(
                        """
                        SELECT COUNT(*) FROM room_stay
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                        """,
                        (hotel_name, booking_id, target_date)
                    )
                    _baseline_cnt = int((cursor.fetchone() or [0])[0] or 0)
                except Exception:
                    _baseline_cnt = None
                today_rooms = [str(rn) for rn, st in rs_rows if rn]
                room_rows = [{"room_number": str(rn), "stay_status": str(st or "").strip().lower()} for rn, st in rs_rows if rn]
                is_checked_in = any((st or '').strip().lower() == 'checked_in' for rn, st in rs_rows)
                checked_in_at = None
                checked_out_at = None
                assigned_at_ts = None
                # Read timestamps from room_stay (authoritative per-stay timestamps)
                try:
                    cursor.execute(
                        """
                        SELECT MIN(checked_in_at), MAX(checked_out_at), MIN(assigned_at)
                        FROM room_stay
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                        """,
                        (hotel_name, booking_id, target_date)
                    )
                    ts_row = cursor.fetchone() or (None, None, None)
                    if ts_row[0] is not None:
                        try:
                            checked_in_at = ts_row[0].isoformat() if hasattr(ts_row[0], 'isoformat') else ts_row[0]
                        except Exception:
                            checked_in_at = str(ts_row[0])
                    if ts_row[1] is not None:
                        try:
                            checked_out_at = ts_row[1].isoformat() if hasattr(ts_row[1], 'isoformat') else ts_row[1]
                        except Exception:
                            checked_out_at = str(ts_row[1])
                    if ts_row[2] is not None:
                        try:
                            assigned_at_ts = ts_row[2].isoformat() if hasattr(ts_row[2], 'isoformat') else ts_row[2]
                        except Exception:
                            assigned_at_ts = str(ts_row[2])
                    # If room_stay.assigned_at is NULL for this date, try room_number_assignments.assigned_at
                    if not assigned_at_ts:
                        try:
                            cursor.execute(
                                """
                                SELECT MIN(COALESCE(rs.assigned_at, rna.assigned_at))
                                FROM room_stay rs
                                LEFT JOIN room_number_assignments rna
                                  ON rna.hotel_name = rs.hotel_name
                                 AND rna.booking_id = rs.booking_id
                                 AND rna.stay_date = rs.stay_date
                                WHERE rs.hotel_name = %s AND rs.booking_id = %s AND rs.stay_date = %s
                                """,
                                (hotel_name, booking_id, target_date)
                            )
                            row_a = cursor.fetchone()
                            if row_a and row_a[0] is not None:
                                try:
                                    assigned_at_ts = row_a[0].isoformat() if hasattr(row_a[0], 'isoformat') else row_a[0]
                                except Exception:
                                    assigned_at_ts = str(row_a[0])
                        except Exception:
                            pass
                    # Final fallback: if a current room is present, read its assignment row timestamp directly
                    if (not assigned_at_ts) and today_rooms:
                        try:
                            cursor.execute(
                                """
                                SELECT assigned_at
                                FROM room_number_assignments
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number = %s
                                ORDER BY assigned_at ASC
                                LIMIT 1
                                """,
                                (hotel_name, booking_id, target_date, str(today_rooms[0]))
                            )
                            ra = cursor.fetchone()
                            if ra and ra[0] is not None:
                                try:
                                    assigned_at_ts = ra[0].isoformat() if hasattr(ra[0], 'isoformat') else ra[0]
                                except Exception:
                                    assigned_at_ts = str(ra[0])
                        except Exception:
                            pass
                    # Absolute last fallback: any assignment for booking/date
                    if not assigned_at_ts:
                        try:
                            cursor.execute(
                                """
                                SELECT MIN(assigned_at)
                                FROM room_number_assignments
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                                """,
                                (hotel_name, booking_id, target_date)
                            )
                            ra2 = cursor.fetchone()
                            if ra2 and ra2[0] is not None:
                                try:
                                    assigned_at_ts = ra2[0].isoformat() if hasattr(ra2[0], 'isoformat') else ra2[0]
                                except Exception:
                                    assigned_at_ts = str(ra2[0])
                        except Exception:
                            pass
                except Exception:
                    pass
            except Exception:
                today_rooms = []
                is_checked_in = False
                checked_in_at = None
                checked_out_at = None
                assigned_at_ts = None
                can_check_in_today = False
                room_rows = []
            cursor.close(); conn.close()

            # Guest profile from MongoDB. An Aiosell phone stays a reference until it is verified.
            guest_name = None
            guest_email = None
            guest_extra = {}
            reference_phone = ""
            phone_verified = False
            rate_plan_description = ""
            try:
                db = self.get_mongodb_connection()
                booking_doc = db.get_collection('guest_bookings').find_one({"hotel_name": str(hotel_name).upper(), "booking_id": booking_id}, {"_id": 0})
                if booking_doc:
                    reference_phone = str(booking_doc.get("reference_phone") or "").strip()
                    phone_verified = bool(booking_doc.get("phone_verified"))
                    guest_name = booking_doc.get("guest_name") or guest_name
                if not reference_phone or not guest_name:
                    stored = db.get_collection('ota_reservations').find_one({"hotel_name": str(hotel_name).upper(), "booking_id": booking_id}, {"_id": 0, "payload": 1})
                    from booking_system.aiosell_reservation import _guest_name, _guest_phone
                    payload = (stored or {}).get("payload") or {}
                    if not reference_phone:
                        reference_phone = _guest_phone(payload)
                    if not guest_name:
                        guest_name = _guest_name(payload) or guest_name
                doc = db.get_collection('guests').find_one({"guest_phone": str(guest_phone), "hotel_name": str(hotel_name).upper()}) if guest_phone else None
                if doc:
                    phone_verified = True
                    guest_name = doc.get('guest_name') or guest_name
                    guest_email = doc.get('guest_email') or doc.get('email') or guest_email
                    for key in ("nationality", "country", "date_of_birth", "gender", "company", "address", "identity_type", "identity_number", "special_request", "vip"):
                        if doc.get(key) not in (None, ""):
                            guest_extra[key] = doc.get(key)
                if rate_plan:
                    plan = db.get_collection('rate_plans').find_one({"hotel_name": str(hotel_name).upper(), "rate_plan": str(rate_plan).upper()}, {"_id": 0, "description": 1})
                    if plan:
                        rate_plan_description = str(plan.get("description") or "")
            except Exception:
                pass

            return {
                "booking_id": booking_id,
                "hotel_name": hotel_name,
                "stay_date": (target_date.isoformat() if 'target_date' in locals() else None),
                "check_in_date": (preview_check_in_date.isoformat() if preview_check_in_date and hasattr(preview_check_in_date,'isoformat') else preview_check_in_date),
                "check_out_date": (preview_check_out_date.isoformat() if preview_check_out_date and hasattr(preview_check_out_date,'isoformat') else preview_check_out_date),
                "guest": {
                    "guest_phone": guest_phone,
                    "reference_phone": reference_phone,
                    "phone_verified": phone_verified,
                    "guest_name": guest_name,
                    "guest_email": guest_email,
                    "nationality": guest_extra.get("nationality") or guest_extra.get("country") or "",
                    "country": guest_extra.get("country") or guest_extra.get("nationality") or "",
                    "date_of_birth": guest_extra.get("date_of_birth") or "",
                    "gender": guest_extra.get("gender") or "",
                    "company": guest_extra.get("company") or "",
                    "address": guest_extra.get("address") or "",
                    "identity_type": guest_extra.get("identity_type") or "",
                    "identity_number": guest_extra.get("identity_number") or "",
                    "special_request": guest_extra.get("special_request") or "",
                    "vip": bool(guest_extra.get("vip"))
                },
                "booking": {
                    "booking_status": booking_status,
                    "booking_notes": booking_notes,
                    "total_amount": float(pt_total or rb_total or 0),
                    "rooms_booked": rooms_booked_count,
                    "rate_plan": str(rate_plan or ""),
                    "rate_plan_description": rate_plan_description
                },
                "payment": {
                    "payment_status": payment_status,
                    "amount_paid": float(amount_paid or 0),
                    "remaining_balance": float(remaining_balance or 0),
                    "payment_method": payment_method,
                    "total_amount": float(pt_total or rb_total or 0),
                    "currency": None
                },
                "checkin": {
                    "is_checked_in": bool(is_checked_in),
                    "today_rooms": today_rooms,
                    "rooms": room_rows if 'room_rows' in locals() else [],
                    "checked_in_at": checked_in_at,
                    "checked_out_at": checked_out_at,
                    "assigned_at": assigned_at_ts,
                    "can_check_in_today": bool(can_check_in_today)
                }
            }
        except Exception as e:
            try:
                if 'conn' in locals(): conn.close()
            except Exception:
                pass
            return {"error": str(e)}

    def read_pricing_breakdown_internal(self, booking_id: str) -> dict:
        """READ pricing breakdown snapshot from MongoDB guest_bookings.pricing_breakdown (no recomputation)."""
        try:
            db = self.get_mongodb_connection()
            doc = db.get_collection('guest_bookings').find_one({"booking_id": booking_id})
            if not doc:
                return {"error": f"Booking {booking_id} not found in MongoDB"}
            pb = doc.get('pricing_breakdown')
            if not pb:
                return {"error": "Pricing breakdown snapshot not available for this booking"}
            hotel_name = str(doc.get('hotel_name') or '')
            room_type = str(doc.get('room_type') or '')
            tax_rate = 0.0
            try:
                vat_pct = float((pb.get('tax_policy_snapshot') or {}).get('vat_percent', 0.0))
                tax_rate = vat_pct / 100.0
            except Exception:
                tax_rate = 0.0
            return {
                "booking_id": booking_id,
                "hotel_name": hotel_name,
                "room_type": room_type,
                "tax_rate": tax_rate,
                "lines": pb.get('lines', []),
                "subtotal": float(pb.get('subtotal', 0.0)),
                "taxes": float(pb.get('taxes_total', 0.0)),
                "computed_total": float(pb.get('total_amount', 0.0)),
                "current_total": float(doc.get('total_amount', 0.0)),
                "currency": pb.get('currency')
            }
        except Exception as e:
            return {"error": str(e)}

    def override_pricing_internal(self, hotel_name: str, manager_name: str, booking_id: str, new_total_amount: float) -> dict:
        """WRITE override: set room_bookings.total_amount and upsert payment_tracking summary fields based on ledger.
        Recomputes amount_paid/remaining using payment_ledger.
        """
        try:
            conn = self.get_postgresql_connection()
            cur = conn.cursor()
            # Validate booking and fetch hotel
            cur.execute("SELECT hotel_name, guest_phone FROM room_bookings WHERE booking_id = %s", (booking_id,))
            rb = cur.fetchone()
            if not rb:
                cur.close(); conn.close();
                return {"error": f"Booking {booking_id} not found"}
            booking_hotel_name, guest_phone = rb
            if canonical_key(hotel_name) != canonical_key(booking_hotel_name):
                cur.close(); conn.close();
                return {"error": f"Hotel mismatch: booking belongs to '{booking_hotel_name}', not '{hotel_name}'"}
            # Update room_bookings total_amount
            cur.execute(
                """
                UPDATE room_bookings SET total_amount = %s, updated_at = CURRENT_TIMESTAMP, updated_by = %s
                WHERE booking_id = %s
                """,
                (float(new_total_amount), (manager_name or "SYSTEM").upper(), booking_id)
            )
            # Sum paid from ledger
            try:
                cur.execute("SELECT COALESCE(SUM(payment_amount),0) FROM payment_ledger WHERE booking_id = %s", (booking_id,))
                paid = float((cur.fetchone() or [0])[0] or 0.0)
            except Exception:
                paid = 0.0
            remaining = max(float(new_total_amount) - paid, 0.0)
            status = 'completed' if remaining <= 0.00001 and new_total_amount > 0 else ('partial' if paid > 0 else 'pending')
            # Upsert payment_tracking
            cur.execute("SELECT 1 FROM payment_tracking WHERE booking_id = %s", (booking_id,))
            exists = cur.fetchone() is not None
            if exists:
                cur.execute(
                    """
                    UPDATE payment_tracking
                    SET total_amount = %s, amount_paid = %s, remaining_balance = %s, payment_status = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE booking_id = %s
                    """,
                    (float(new_total_amount), paid, remaining, status, booking_id)
                )
            else:
                cur.execute(
                    """
                    INSERT INTO payment_tracking (booking_id, guest_phone_number, total_amount, amount_paid, remaining_balance, payment_status)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (booking_id, guest_phone, float(new_total_amount), paid, remaining, status)
                )
            conn.commit(); cur.close(); conn.close()
            return {"result": "success", "booking_id": booking_id, "total_amount": float(new_total_amount), "amount_paid": paid, "remaining_balance": remaining, "payment_status": status}
        except Exception as e:
            try:
                if 'conn' in locals(): conn.rollback()
            except Exception:
                pass
            return {"error": str(e)}
    def update_booking_detail_internal(self, booking_id: str, guest: dict = None, booking: dict = None, payment: dict = None) -> dict:
        """UPDATE guest (MongoDB), booking (PostgreSQL room_bookings), and payment (payment_tracking) info.
        booking_id and guest_phone are immutable from client; guest_phone resolved from room_bookings."""
        try:
            guest = guest or {}
            booking = booking or {}
            payment = payment or {}

            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT hotel_name, guest_phone FROM room_bookings WHERE booking_id = %s", (booking_id,))
            rb = cursor.fetchone()
            if not rb:
                cursor.close(); conn.close()
                return {"error": f"Booking {booking_id} not found"}
            hotel_name, guest_phone = rb

            # Update room_bookings fields
            updates = []
            values = []
            if 'booking_status' in booking and booking['booking_status']:
                updates.append("booking_status = %s")
                values.append(booking['booking_status'])
            if 'booking_notes' in booking:
                updates.append("booking_notes = %s")
                values.append(booking['booking_notes'])
            if 'rate_plan' in booking:
                updates.append("rate_plan = %s")
                values.append(str(booking.get('rate_plan') or '').strip().upper())
            if 'total_amount' in booking and booking['total_amount'] is not None:
                updates.append("total_amount = %s")
                values.append(float(booking['total_amount']))
            if updates:
                q = f"UPDATE room_bookings SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE booking_id = %s"
                values.append(booking_id)
                cursor.execute(q, values)

            # Upsert payment_tracking
            if payment:
                cursor.execute("SELECT 1 FROM payment_tracking WHERE booking_id = %s", (booking_id,))
                exists = cursor.fetchone() is not None
                if exists:
                    p_updates = []
                    p_vals = []
                    for k in ('payment_status','amount_paid','remaining_balance','payment_method','total_amount'):
                        if k in payment and payment[k] is not None:
                            p_updates.append(f"{k} = %s")
                            if k in ('amount_paid','remaining_balance','total_amount'):
                                p_vals.append(float(payment[k]))
                            else:
                                p_vals.append(payment[k])
                    if p_updates:
                        p_updates.append("updated_at = CURRENT_TIMESTAMP")
                        q = f"UPDATE payment_tracking SET {', '.join(p_updates)} WHERE booking_id = %s"
                        p_vals.append(booking_id)
                        cursor.execute(q, p_vals)
                else:
                    cursor.execute(
                        """
                        INSERT INTO payment_tracking (booking_id, guest_phone_number, payment_status, amount_paid, remaining_balance, payment_method, total_amount)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            booking_id,
                            guest_phone,
                            payment.get('payment_status') or 'unpaid',
                            float(payment.get('amount_paid') or 0),
                            float(payment.get('remaining_balance') or 0),
                            payment.get('payment_method'),
                            float(payment.get('total_amount') or 0)
                        )
                    )

            # Update guest profile in MongoDB
            try:
                if guest:
                    db = self.get_mongodb_connection()
                    coll = db.get_collection('guests')
                    guest_fields = {}
                    for key in ("guest_name", "nationality", "country", "date_of_birth", "gender", "company", "address", "identity_type", "identity_number", "special_request"):
                        if guest.get(key) is not None:
                            guest_fields[key] = guest.get(key)
                    if guest.get('guest_email') is not None or guest.get('email') is not None:
                        guest_fields["guest_email"] = guest.get('guest_email') or guest.get('email')
                        guest_fields["email"] = guest_fields["guest_email"]
                    if guest.get('special_request') is not None:
                        guest_fields["special_requests"] = guest.get('special_request')
                    if 'vip' in guest:
                        guest_fields["vip"] = bool(guest.get('vip'))
                    if guest_fields:
                        coll.update_one({"guest_phone": str(guest_phone)}, {"$set": guest_fields}, upsert=True)
            except Exception:
                pass

            conn.commit(); cursor.close(); conn.close()
            return {"result": "success", "booking_id": booking_id}
        except Exception as e:
            try:
                if 'conn' in locals(): conn.rollback()
            except Exception:
                pass
            try:
                if 'cursor' in locals(): cursor.close()
                if 'conn' in locals(): conn.close()
            except Exception:
                pass
            return {"error": str(e)}

    def relocate_guest_internal(self, hotel_name: str, manager_name: str, booking_id: str, room_numbers_by_date: dict) -> str:
        """
        READ-then-WRITE with MERGED-DATES: First-night mapping applies to all current/future stay dates.
        Strict validation:
        - Require room_numbers_by_date for the FIRST stay date only; other keys are rejected
        - Proposed count must equal required slots for the first date, and will be enforced for all dates
        - Each room must be available (or already assigned to this booking) and match booking room_type across all affected dates
        - Today's date (if in stay window) sets stay_status to 'checked_in'; future dates set to 'assigned'
        """
        conn = self.get_postgresql_connection()
        cursor = conn.cursor()
        try:
            from booking_system.timezone_utils import hotel_local_today
            today = hotel_local_today(hotel_name)
            # Validate booking exists and get context
            cursor.execute(
                """
                SELECT guest_phone, hotel_name, room_type FROM room_bookings WHERE booking_id = %s
                """,
                (booking_id,)
            )
            rowb = cursor.fetchone()
            if not rowb:
                cursor.close(); conn.close()
                return f"❌ Booking {booking_id} not found in room_bookings table"
            guest_phone, booking_hotel, booking_room_type = rowb
            if canonical_key(hotel_name) != canonical_key(booking_hotel):
                cursor.close(); conn.close()
                return f"❌ Hotel mismatch: Booking is for '{booking_hotel}', not '{hotel_name}'"
            # Fetch current and future stay dates
            cursor.execute(
                """
                SELECT DISTINCT stay_date FROM room_stay
                WHERE booking_id = %s AND stay_date >= CURRENT_DATE
                ORDER BY stay_date
                """,
                (booking_id,)
            )
            stay_dates = [r[0] for r in cursor.fetchall()]
            if not stay_dates:
                cursor.close(); conn.close()
                return f"❌ No stay_dates found for booking {booking_id}"
            relocation_results = []
            # Enforce merged dates from first date only
            anchor_date = min(stay_dates)
            anchor_key = anchor_date if isinstance(anchor_date, str) else str(anchor_date)
            extra_keys = [k for k in room_numbers_by_date.keys() if str(k) != str(anchor_key)]
            if extra_keys:
                cursor.close(); conn.close()
                return f"❌ MERGED DATES: Provide rooms only for first night {anchor_key}."
            proposed = list(room_numbers_by_date.get(str(anchor_key), []))
            # Determine required slots on anchor
            cursor.execute(
                """
                SELECT stay_index FROM room_stay
                WHERE booking_id = %s AND stay_date = %s
                ORDER BY stay_index
                """,
                (booking_id, anchor_date)
            )
            anchor_indices = [r[0] for r in cursor.fetchall()]
            if not anchor_indices:
                cursor.close(); conn.close()
                return f"❌ {anchor_key}: no room_stay rows found"
            if len(proposed) != len(anchor_indices):
                cursor.close(); conn.close()
                return f"❌ {anchor_key}: proposed {len(proposed)} does not match required {len(anchor_indices)}"
            # Validate across all current/future stay dates
            for sdate in stay_dates:
                for rn in proposed:
                    cursor.execute(
                        """
                        SELECT booking_id, assignment_status, COALESCE(room_type,'')
                        FROM room_number_assignments
                        WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
                        """,
                        (hotel_name, rn, sdate)
                    )
                    r = cursor.fetchone()
                    if not r:
                        cursor.close(); conn.close()
                        return f"❌ {sdate}: room {rn} has no assignment record"
                    assigned_bid, status, rn_type = r
                    if assigned_bid and str(assigned_bid).strip() != str(booking_id).strip():
                        existing_booking_id = assigned_bid
                        cursor.close(); conn.close()
                        return f"❌ {sdate}: room {rn} already assigned to {existing_booking_id}"
                    if status != 'available' and (not assigned_bid or str(assigned_bid).strip() != str(booking_id).strip()):
                        cursor.close(); conn.close()
                        return f"❌ {sdate}: room {rn} not available (status={status})"
                    if rn_type and canonical_key(rn_type) != canonical_key(booking_room_type):
                        cursor.close(); conn.close()
                        return f"❌ {sdate}: room {rn} type {rn_type} != booking type {booking_room_type}"
            # Apply relocation for all dates
            for sdate in stay_dates:
                # Clear existing rows for date
                cursor.execute(
                    """
                    UPDATE room_number_assignments
                    SET booking_id = NULL, guest_phone = NULL, assignment_status = 'available', updated_by = %s
                    WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                    """,
                    (manager_name, hotel_name, sdate, booking_id)
                )
                # Indices for date
                cursor.execute(
                    """
                    SELECT stay_index FROM room_stay
                    WHERE booking_id = %s AND stay_date = %s
                    ORDER BY stay_index
                    """,
                    (booking_id, sdate)
                )
                indices = [r[0] for r in cursor.fetchall()]
                if len(indices) != len(proposed):
                    cursor.close(); conn.close()
                    return f"❌ {sdate}: required {len(indices)} does not match first-night proposed {len(proposed)}"
                assigned_this_date = []
                for i, stay_index in enumerate(indices):
                    rn = proposed[i]
                    new_status = 'checked_in' if (hasattr(sdate, 'isoformat') and sdate == today) or (not hasattr(sdate, 'isoformat') and str(sdate) == today.isoformat()) else 'assigned'
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET room_number = %s, stay_status = %s, assigned_by = %s, assigned_at = CURRENT_TIMESTAMP
                        WHERE booking_id = %s AND stay_date = %s AND stay_index = %s
                        """,
                        (rn, new_status, manager_name, booking_id, sdate, stay_index)
                    )
                    cursor.execute(
                        """
                        UPDATE room_number_assignments
                        SET booking_id = %s, guest_phone = %s, assignment_status = 'assigned', updated_by = %s
                        WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
                        """,
                        (booking_id, guest_phone, manager_name, hotel_name, rn, sdate)
                    )
                    if cursor.rowcount == 0:
                        cursor.execute(
                            """
                            INSERT INTO room_number_assignments (hotel_name, room_type, room_number, stay_date, assignment_status, booking_id, guest_phone, assigned_by, assigned_at, updated_by)
                            VALUES (%s, NULL, %s, %s, 'assigned', %s, %s, %s, CURRENT_TIMESTAMP, %s)
                            """,
                            (hotel_name, rn, sdate, booking_id, guest_phone, manager_name, manager_name)
                        )
                    assigned_this_date.append(rn)
                    if (hasattr(sdate, 'isoformat') and sdate == today) or (not hasattr(sdate, 'isoformat') and str(sdate) == today.isoformat()):
                        cursor.execute(
                            """
                            UPDATE room_readiness_status
                            SET readiness_status = 'occupied', status_changed_at = CURRENT_TIMESTAMP, changed_by = %s, notes = %s
                            WHERE hotel_name = %s AND room_number = %s
                            """,
                            (manager_name, f'Relocated to {rn} on {sdate}', hotel_name, rn)
                        )
                relocation_results.append(f"✅ Relocated booking {booking_id} for {sdate} to rooms: {', '.join(assigned_this_date)}")
            conn.commit(); cursor.close(); conn.close()
            return "\n".join(relocation_results)
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            try:
                cursor.close(); conn.close()
            except Exception:
                pass
            return f"❌ Error relocating guest: {str(e)}"

    def _fill_blank_stay_slots(self, cursor, hotel_name: str, booking_id: str, stay_dates: list) -> None:
        """When assignments exist but every stay slot is blank, copy those rooms onto the slots."""
        for stay_date in stay_dates:
            cursor.execute(
                """
                SELECT room_number
                FROM room_number_assignments
                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number IS NOT NULL
                ORDER BY room_number
                """,
                (hotel_name, booking_id, stay_date)
            )
            assigned = [row[0] for row in (cursor.fetchall() or []) if row and row[0]]
            cursor.execute(
                """
                SELECT stay_index, room_number
                FROM room_stay
                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                ORDER BY stay_index
                """,
                (hotel_name, booking_id, stay_date)
            )
            slots = cursor.fetchall() or []
            if not assigned or not slots or any(row[1] for row in slots):
                continue
            for (stay_index, _), room_number in zip(slots, assigned):
                cursor.execute(
                    """
                    UPDATE room_stay
                    SET room_number = %s, stay_status = 'assigned'
                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND stay_index = %s
                      AND room_number IS NULL
                    """,
                    (room_number, hotel_name, booking_id, stay_date, stay_index)
                )

    def _stay_slot_for_room(self, cursor, hotel_name: str, booking_id: str, stay_date, room_number: str):
        cursor.execute(
            """
            SELECT stay_index
            FROM room_stay
            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number = %s
            ORDER BY stay_index
            LIMIT 1
            """,
            (hotel_name, booking_id, stay_date, room_number)
        )
        row = cursor.fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])

    def _room_open_for_slot(self, cursor, hotel_name: str, booking_id: str, stay_date, room_number: str, stay_index: int) -> bool:
        cursor.execute(
            """
            SELECT booking_id
            FROM room_number_assignments
            WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
            """,
            (hotel_name, room_number, stay_date)
        )
        row = cursor.fetchone()
        holder = str(row[0]).strip() if row and row[0] else ''
        if holder and holder != str(booking_id).strip():
            return False
        cursor.execute(
            """
            SELECT 1
            FROM room_stay
            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number = %s AND stay_index <> %s
            LIMIT 1
            """,
            (hotel_name, booking_id, stay_date, room_number, stay_index)
        )
        return cursor.fetchone() is None

    def _hold_room_for_stay_slot(self, cursor, hotel_name: str, manager_name: str, booking_id: str, room_type: str, guest_phone: str, stay_dates: list, stay_index: int, old_room_number: str, new_room_number: str) -> None:
        """Keep one booked room on the same room for every night of the stay."""
        for stay_date in stay_dates:
            cursor.execute(
                """
                SELECT room_number, stay_status
                FROM room_stay
                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND stay_index = %s
                """,
                (hotel_name, booking_id, stay_date, stay_index)
            )
            row = cursor.fetchone()
            prev_room = row[0] if row else None
            prev_status = str((row[1] if row else '') or '').strip().lower()
            next_status = 'checked_in' if prev_status == 'checked_in' else 'assigned'
            if row:
                cursor.execute(
                    """
                    UPDATE room_stay
                    SET room_number = %s,
                        stay_status = %s,
                        assigned_by = %s,
                        assigned_at = CURRENT_TIMESTAMP,
                        assignment_id = %s
                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND stay_index = %s
                    """,
                    (new_room_number, next_status, manager_name, f"{booking_id}#{new_room_number}", hotel_name, booking_id, stay_date, stay_index)
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO room_stay (booking_id, stay_date, stay_index, hotel_name, room_number, stay_status, assigned_by, assigned_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    """,
                    (booking_id, stay_date, stay_index, hotel_name, new_room_number, next_status, manager_name)
                )
            cursor.execute(
                """
                UPDATE room_stay
                SET room_number = NULL
                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND stay_index <> %s AND room_number = %s
                """,
                (hotel_name, booking_id, stay_date, stay_index, new_room_number)
            )
            for released in (prev_room, old_room_number):
                if not released or str(released).strip() == str(new_room_number).strip():
                    continue
                cursor.execute(
                    """
                    SELECT 1 FROM room_stay
                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number = %s
                    LIMIT 1
                    """,
                    (hotel_name, booking_id, stay_date, released)
                )
                if cursor.fetchone():
                    continue
                cursor.execute(
                    """
                    UPDATE room_number_assignments
                    SET booking_id = NULL,
                        guest_phone = NULL,
                        assignment_status = 'available',
                        updated_by = %s
                    WHERE hotel_name = %s AND room_number = %s AND stay_date = %s AND booking_id = %s
                    """,
                    (manager_name, hotel_name, released, stay_date, booking_id)
                )
            cursor.execute(
                """
                INSERT INTO room_number_assignments (
                    hotel_name, room_type, room_number, stay_date, assignment_status, booking_id, guest_phone, assigned_by, assigned_at, updated_by, assignment_id
                ) VALUES (%s, %s, %s, %s, 'assigned', %s, %s, %s, CURRENT_TIMESTAMP, %s, %s)
                ON CONFLICT (hotel_name, room_number, stay_date)
                DO UPDATE SET
                    room_type = COALESCE(room_number_assignments.room_type, EXCLUDED.room_type),
                    booking_id = EXCLUDED.booking_id,
                    guest_phone = EXCLUDED.guest_phone,
                    assignment_status = 'assigned',
                    updated_by = EXCLUDED.updated_by,
                    assigned_by = EXCLUDED.assigned_by,
                    assigned_at = EXCLUDED.assigned_at,
                    assignment_id = EXCLUDED.assignment_id
                """,
                (hotel_name, room_type, new_room_number, stay_date, booking_id, guest_phone, manager_name, manager_name, f"{booking_id}#{new_room_number}")
            )
            cursor.execute(
                """
                UPDATE room_stay
                SET stay_status = 'assigned'
                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                  AND room_number IS NOT NULL
                  AND LOWER(COALESCE(stay_status, '')) NOT IN ('checked_in', 'departed', 'cancelled')
                """,
                (hotel_name, booking_id, stay_date)
            )

    def reassign_room_for_date_internal(self, hotel_name: str, manager_name: str, booking_id: str, stay_date: str, old_room_number: str, new_room_number: str, mark_old_clean=None) -> dict:
        """UPDATE a single-night room assignment by moving a booking from old_room_number to new_room_number on stay_date.
        Validations:
        - Both rooms exist in room_number_assignments for the given date and hotel
        - Both rooms share the same room_type
        - New room is not assigned to a different booking for that date
        - There exists a room_stay row for (booking_id, stay_date, old_room_number)
        """
        try:
            # Normalize room numbers to consistent text
            try:
                old_room_number = str(old_room_number or '').strip()
                new_room_number = str(new_room_number or '').strip()
            except Exception:
                pass
            # Parse date (hotel-local)
            try:
                y, m, d = [int(x) for x in str(stay_date).split('-')]
                from booking_system.timezone_utils import hotel_local_today
                base_today = hotel_local_today(hotel_name)
                target_date = base_today.replace(year=y, month=m, day=d)
            except Exception:
                return {"error": "Invalid date format. Use YYYY-MM-DD"}

            conn = self.get_postgresql_connection()
            cursor = conn.cursor()

            # Disallow relocating on days outside booking stay window; allow past dates
            try:
                from booking_system.timezone_utils import hotel_local_today
                today = hotel_local_today(hotel_name)
            except Exception:
                return {"error": "Missing hotel timezone"}
            cursor.execute(
                """
                SELECT EXISTS(SELECT 1 FROM booking_stays WHERE booking_id = %s AND stay_date = %s)
                """,
                (booking_id, target_date)
            )
            is_date_in_stay = bool(cursor.fetchone()[0])
            if not is_date_in_stay:
                cursor.execute(
                    """
                    SELECT MIN(stay_date), MAX(stay_date) FROM booking_stays WHERE booking_id = %s
                    """,
                    (booking_id,)
                )
                rng = cursor.fetchone() or (None, None)
                start_str = rng[0].isoformat() if rng[0] else '-'
                end_str = rng[1].isoformat() if rng[1] else '-'
                cursor.close(); conn.close()
                return {"error": f"Relocation not allowed. {target_date.isoformat()} is outside the stay dates ({start_str} → {end_str})."}

            # Determine whether to apply to entire stay (rooms_booked == 1 ⇒ enforce same room across all nights)
            cursor.execute("SELECT COALESCE(rooms_booked,1), room_type FROM room_bookings WHERE booking_id = %s", (booking_id,))
            row_rb = cursor.fetchone() or (1, None)
            rooms_booked_val = int((row_rb[0] or 1))
            booking_room_type = row_rb[1]
            cursor.execute("SELECT stay_date FROM booking_stays WHERE booking_id = %s ORDER BY stay_date", (booking_id,))
            all_stay_dates = [r[0] for r in (cursor.fetchall() or [])]
            target_dates = [target_date]
            # Propagate across all stay_dates when:
            # - Single-room bookings (rooms_booked_val == 1), or
            # - Multi-room bookings where we're assigning from NO ROOM (old_room_number empty)
            if all_stay_dates and (rooms_booked_val == 1 or (rooms_booked_val > 1 and (not old_room_number))):
                target_dates = all_stay_dates

            # A dragged room, or an Assign from Needs a room, keeps one room for every night of that slot.
            slot_index = None
            if rooms_booked_val > 1 and new_room_number and all_stay_dates and old_room_number:
                self._fill_blank_stay_slots(cursor, hotel_name, booking_id, all_stay_dates)
                slot_index = self._stay_slot_for_room(cursor, hotel_name, booking_id, target_date, old_room_number)
            elif rooms_booked_val > 1 and new_room_number and all_stay_dates and (not old_room_number):
                cursor.execute(
                    """
                    SELECT stay_index
                    FROM room_stay
                    WHERE hotel_name = %s AND booking_id = %s AND stay_date IN %s
                    GROUP BY stay_index
                    HAVING COUNT(room_number) = 0
                    ORDER BY COUNT(*) DESC, stay_index
                    LIMIT 1
                    """,
                    (hotel_name, booking_id, tuple(all_stay_dates))
                )
                picked = cursor.fetchone()
                if picked and picked[0] is not None:
                    slot_index = int(picked[0])
            if slot_index is not None:
                blocked_nights = []
                for stay_night in all_stay_dates:
                    if not self._room_open_for_slot(cursor, hotel_name, booking_id, stay_night, new_room_number, slot_index):
                        blocked_nights.append(stay_night.isoformat() if hasattr(stay_night, 'isoformat') else str(stay_night))
                if blocked_nights:
                    conn.rollback(); cursor.close(); conn.close()
                    return {"error": f"Target room {new_room_number} unavailable on: {', '.join(blocked_nights)}"}

            # Preselect a consistent stay_index across dates when assigning from NO ROOM in multi-room, multi-date case
            global_si = None
            if (rooms_booked_val > 1) and (not old_room_number) and isinstance(target_dates, list) and len(target_dates) > 1:
                try:
                    td0 = target_dates[0]
                    cursor.execute(
                        """
                        SELECT stay_index
                        FROM room_stay
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number IS NULL
                        ORDER BY stay_index
                        LIMIT 1
                        """,
                        (hotel_name, booking_id, td0)
                    )
                    rsi = cursor.fetchone()
                    if rsi and rsi[0] is not None:
                        global_si = int(rsi[0])
                    else:
                        cursor.execute(
                            """
                            SELECT COALESCE(MAX(stay_index),0)+1
                            FROM room_stay
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                            """,
                            (hotel_name, booking_id, td0)
                        )
                        global_si = int((cursor.fetchone() or [1])[0] or 1)
                except Exception:
                    global_si = None

            # If new_room_number is empty → UNASSIGN (clear assignment)
            if not new_room_number:
                try:
                    from booking_system.timezone_utils import hotel_local_today
                    today = hotel_local_today(hotel_name)
                except Exception:
                    return {"error": "Missing hotel timezone"}

                cursor.execute(
                    """
                    SELECT EXISTS(SELECT 1 FROM booking_stays WHERE booking_id = %s AND stay_date = %s)
                    """,
                    (booking_id, target_date)
                )
                if not bool(cursor.fetchone()[0]):
                    cursor.execute(
                        """
                        SELECT MIN(stay_date), MAX(stay_date) FROM booking_stays WHERE booking_id = %s
                        """,
                        (booking_id,)
                    )
                    rng = cursor.fetchone() or (None, None)
                    start_str = rng[0].isoformat() if rng[0] else '-'
                    end_str = rng[1].isoformat() if rng[1] else '-'
                    cursor.close(); conn.close()
                    return {"error": f"Unassignment not allowed. {target_date.isoformat()} is outside the stay dates ({start_str} → {end_str})."}

                if old_room_number and all_stay_dates and len(all_stay_dates) > 1:
                    slot_index = self._stay_slot_for_room(cursor, hotel_name, booking_id, target_date, old_room_number)
                    if slot_index is not None:
                        for td in all_stay_dates:
                            cursor.execute(
                                """
                                SELECT room_number, LOWER(COALESCE(stay_status,''))
                                FROM room_stay
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND stay_index = %s
                                """,
                                (hotel_name, booking_id, td, slot_index)
                            )
                            slot_row = cursor.fetchone()
                            slot_room = str(slot_row[0]).strip() if slot_row and slot_row[0] else ''
                            slot_status = str(slot_row[1] or '') if slot_row else ''
                            if slot_room:
                                cursor.execute(
                                    """
                                    UPDATE room_number_assignments
                                    SET booking_id = NULL,
                                        guest_phone = NULL,
                                        assignment_status = 'available',
                                        updated_by = %s,
                                        updated_at = CURRENT_TIMESTAMP
                                    WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s AND room_number = %s
                                    """,
                                    (manager_name, hotel_name, td, booking_id, slot_room)
                                )
                                if slot_status == 'checked_in' and td == today:
                                    try:
                                        cursor.execute(
                                            """
                                            UPDATE room_readiness_status
                                            SET readiness_status = 'dirty', status_changed_at = CURRENT_TIMESTAMP, changed_by = %s, notes = %s
                                            WHERE hotel_name = %s AND room_number = %s
                                            """,
                                            (manager_name, f"Unassigned on {td} for {booking_id}", hotel_name, slot_room)
                                        )
                                    except Exception:
                                        pass
                            cursor.execute(
                                """
                                UPDATE room_stay
                                SET room_number = NULL
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND stay_index = %s
                                """,
                                (hotel_name, booking_id, td, slot_index)
                            )
                        conn.commit()
                        try:
                            cursor.close(); conn.close()
                        except Exception:
                            pass
                        return {"result": f"✅ Unassigned booking {booking_id} from room {old_room_number} across {len(all_stay_dates)} night(s)"}

                # Clear slot(s) per date
                for td in target_dates:
                    # If old_room_number not provided and multi-room with one assigned + one NULL, unassign all for that date
                    try:
                        if not old_room_number:
                            cursor.execute(
                                """
                                SELECT 
                                  COUNT(*) FILTER (WHERE room_number IS NULL) AS null_cnt,
                                  COUNT(*) FILTER (WHERE room_number IS NOT NULL) AS nonnull_cnt
                                FROM room_stay
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                                """,
                                (hotel_name, booking_id, td)
                            )
                            rc = cursor.fetchone() or (0,0)
                            null_cnt = int(rc[0] or 0)
                            nonnull_cnt = int(rc[1] or 0)
                            cursor.execute(
                                """
                                SELECT COUNT(*) FROM room_number_assignments
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND booking_id IS NOT NULL
                                """,
                                (hotel_name, booking_id, td)
                            )
                            assigned_cnt = int((cursor.fetchone() or [0])[0] or 0)
                            if assigned_cnt >= 1 and null_cnt >= 1:
                                cursor.execute(
                                    """
                                    UPDATE room_number_assignments
                                    SET booking_id = NULL,
                                        guest_phone = NULL,
                                        assignment_status = 'available',
                                        updated_by = %s,
                                        updated_at = CURRENT_TIMESTAMP
                                    WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                                    """,
                                    (manager_name, hotel_name, td, booking_id)
                                )
                                cursor.execute(
                                    """
                                    UPDATE room_stay
                                    SET room_number = NULL
                                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                                    """,
                                    (hotel_name, booking_id, td)
                                )
                                # proceed to next date
                                continue
                    except Exception:
                        pass
                    # Determine the specific stay_index to unassign for this date
                    cursor.execute(
                        """
                        SELECT stay_index, room_number, stay_status, assigned_at
                        FROM room_stay
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                        ORDER BY stay_index
                        """,
                        (hotel_name, booking_id, td)
                    )
                    _rows = cursor.fetchall() or []
                    target_si = None
                    current_rn = None
                    # 1) Exact old_room_number match
                    for si, rn, st, at in _rows:
                        if old_room_number and str(rn or '').strip() == str(old_room_number).strip():
                            target_si = si; current_rn = rn; break
                    # 2) Prefer checked_in slot if none matched explicitly
                    if target_si is None:
                        for si, rn, st, at in _rows:
                            if str(st or '').strip().lower() == 'checked_in' and rn is not None:
                                target_si = si; current_rn = rn; break
                    # 3) If still none, choose the most recently assigned non-null slot
                    if target_si is None:
                        try:
                            candidates = [(si, rn, st, at) for (si, rn, st, at) in _rows if rn is not None]
                            if candidates:
                                # order by assigned_at desc nulls last, then stay_index desc
                                candidates.sort(key=lambda x: ((x[3] or 0), x[0]))
                                # sort ascending then take last (max)
                                si, rn, st, at = candidates[-1]
                                target_si = si; current_rn = rn
                        except Exception:
                            pass
                    # 4) Fallback to first non-null room slot
                    if target_si is None:
                        for si, rn, st, at in _rows:
                            if rn is not None:
                                target_si = si; current_rn = rn; break
                    if target_si is None and _rows:
                        target_si = _rows[0][0]; current_rn = _rows[0][1]

                    # Clear any timestamps/notes on the single assignment row (scoped by room_number if known)
                    if current_rn:
                        try:
                            cursor.execute(
                                """
                                UPDATE room_number_assignments
                                SET check_in_time = NULL,
                                    check_out_time = NULL,
                                    assignment_notes = NULL,
                                    updated_by = %s,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s AND room_number = %s
                                """,
                                (manager_name, hotel_name, td, booking_id, current_rn)
                            )
                        except Exception:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                    # Clear only the targeted assignment row to UNASSIGNED
                    if current_rn:
                        cursor.execute(
                            """
                            UPDATE room_number_assignments
                            SET booking_id = NULL,
                                guest_phone = NULL,
                                assignment_status = 'available',
                                updated_by = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s AND room_number = %s
                            """,
                            (manager_name, hotel_name, td, booking_id, current_rn)
                        )
                        # Ensure the other assigned rooms for this booking/date remain intact; do not clear them
                        # Do not modify other assigned rows for the same booking/date (multi-room stays)
                    # If the affected date is today and previously checked_in, mark the old room dirty
                    try:
                        cursor.execute(
                            """
                            SELECT MAX(CASE WHEN LOWER(COALESCE(stay_status,''))='checked_in' THEN 1 ELSE 0 END)
                            FROM room_stay
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                            """,
                            (hotel_name, booking_id, td)
                        )
                        was_ci = bool((cursor.fetchone() or [0])[0])
                        if was_ci and old_room_number:
                            cursor.execute(
                                """
                                UPDATE room_readiness_status
                                SET readiness_status = 'dirty', status_changed_at = CURRENT_TIMESTAMP, changed_by = %s, notes = %s
                                WHERE hotel_name = %s AND room_number = %s
                                """,
                                (manager_name, f"Unassigned on {td} for {booking_id}", hotel_name, old_room_number)
                            )
                    except Exception:
                        pass

                    # Clear room_number in room_stay ONLY for the targeted stay_index; persist stay_status
                    if target_si is not None:
                        try:
                            cursor.execute(
                                """
                                UPDATE room_stay
                                SET room_number = NULL
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND stay_index = %s
                                """,
                                (hotel_name, booking_id, td, target_si)
                            )
                        except Exception:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                        # Do not collapse other room_stay rows; preserve multi-room bookings

                conn.commit()
                # After unassign, there is no assigned_at for this date
                assigned_ts_out = None
                try:
                    cursor.close(); conn.close()
                except Exception:
                    pass
                return {"result": f"✅ Unassigned booking {booking_id} from room {old_room_number} across {len(target_dates)} night(s)", "assigned_at": assigned_ts_out}

            # Read existing assignment rows for validation (support old_room_number being NULL/empty when coming from UNASSIGNED)
            params_list = [hotel_name, target_date]
            room_checks = []
            if old_room_number:
                room_checks.append(old_room_number)
            if new_room_number:
                room_checks.append(new_room_number)
            if not room_checks:
                cursor.close(); conn.close()
                return {"error": "No target room specified"}
            placeholders = ",".join(["%s"] * len(room_checks))
            query = f"""
                SELECT room_number, room_type, assignment_status, booking_id
                FROM room_number_assignments
                WHERE hotel_name = %s AND stay_date = %s AND room_number IN ({placeholders})
                ORDER BY room_number
            """
            cursor.execute(query, params_list + room_checks)
            rows = cursor.fetchall()
            # Map rows
            info = {r[0]: {"room_type": r[1], "status": r[2], "booking_id": r[3]} for r in rows}
            # Validate presence of new room for target_date; for other dates we will insert if missing
            if new_room_number not in info:
                info[new_room_number] = {"room_type": booking_room_type, "status": 'available', "booking_id": None}
            # If old room was provided, validate it belongs to this booking and room_type matches
            if old_room_number:
                if old_room_number not in info or (info[old_room_number]["booking_id"] or "").strip() != (booking_id or "").strip():
                    # Fallback: verify from room_stay instead of room_number_assignments
                    try:
                        cursor.execute(
                            """
                            SELECT 1 FROM room_stay
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number = %s
                            LIMIT 1
                            """,
                            (hotel_name, booking_id, target_date, old_room_number)
                        )
                        rs_exists = bool(cursor.fetchone())
                    except Exception:
                        rs_exists = False
                    if not rs_exists:
                        cursor.close(); conn.close()
                        return {"error": "Old room is not assigned to the specified booking on that date"}
                    # Synthesize info for old room based on booking context to proceed
                    info[old_room_number] = {"room_type": info.get(new_room_number, {}).get("room_type", booking_room_type), "status": 'assigned', "booking_id": booking_id}
                # Validate type compatibility
                if info[old_room_number]["room_type"] != info[new_room_number]["room_type"]:
                    cursor.close(); conn.close()
                    return {"error": "Room type mismatch between old and new rooms"}
            # Validate availability across all target_dates before making any changes
            conflict_dates = []
            for td in target_dates:
                rs_updated_flag = False
                cursor.execute(
                    """
                    SELECT booking_id, assignment_status FROM room_number_assignments
                    WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
                    """,
                    (hotel_name, new_room_number, td)
                )
                r = cursor.fetchone()
                if r and r[0] and str(r[0]).strip() != str(booking_id).strip():
                    conflict_dates.append(td.isoformat() if hasattr(td,'isoformat') else str(td))
            if conflict_dates:
                cursor.close(); conn.close()
                return {"error": f"Target room {new_room_number} unavailable on: {', '.join(conflict_dates)}"}

            # Fetch guest_phone for this booking (used to enrich assignment row)
            cursor.execute(
                """
                SELECT guest_phone FROM room_bookings WHERE booking_id = %s
                """,
                (booking_id,)
            )
            row_gp = cursor.fetchone()
            booking_guest_phone = row_gp[0] if row_gp else None

            # Find a room_stay row for this booking/date to update
            # Prefer the row that currently has old_room_number; otherwise first unassigned; otherwise first row
            cursor.execute(
                """
                SELECT stay_index, room_number, stay_status
                FROM room_stay
                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                ORDER BY stay_index
                """,
                (hotel_name, booking_id, target_date)
            )
            rows_rs = cursor.fetchall()
            if not rows_rs:
                # CREATE: no room_stay rows exist yet for this booking/date → create one to satisfy reassignment
                try:
                    cursor.execute(
                        """
                        INSERT INTO room_stay (booking_id, stay_date, stay_index, hotel_name, room_number, stay_status, assigned_by, assigned_at)
                        VALUES (%s, %s, %s, %s, %s, 'assigned', %s, CURRENT_TIMESTAMP)
                        """,
                        (booking_id, target_date, 1, hotel_name, old_room_number if old_room_number else None, manager_name)
                    )
                    rows_rs = [(1, old_room_number, 'assigned')]
                except Exception as _e:
                    conn.rollback(); cursor.close(); conn.close()
                    return {"error": f"Failed to create room_stay row: {_e}"}
            preferred = None
            fallback_unassigned = None
            any_row = None
            prev_status_for_si = None
            for si, rn, st in rows_rs:
                any_row = any_row or si
                if old_room_number and rn == old_room_number:
                    preferred = si
                    prev_status_for_si = st
                    break
                if rn is None or str(rn).strip() == "":
                    if fallback_unassigned is None:
                        fallback_unassigned = si
            # Choose target index strictly: if no old_room_number, must use a NULL slot to avoid stealing existing room
            if old_room_number:
                stay_index = preferred or fallback_unassigned or any_row
            else:
                stay_index = fallback_unassigned or any_row
            if prev_status_for_si is None:
                for si, rn, st in rows_rs:
                    if si == stay_index:
                        prev_status_for_si = st
                        break
            norm_prev = (str(prev_status_for_si or '')).strip().lower()
            new_stay_status = 'checked_in' if norm_prev == 'checked_in' else 'assigned'

            # Perform updates atomically across all target_dates
            for td in target_dates:
                # Locate stay_index to update for this date
                cursor.execute(
                    """
                    SELECT stay_index, room_number, stay_status FROM room_stay
                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                    ORDER BY stay_index
                    """,
                    (hotel_name, booking_id, td)
                )
                rs_rows = cursor.fetchall() or []
                if not rs_rows:
                    try:
                        cursor.execute(
                            """
                            INSERT INTO room_stay (booking_id, stay_date, stay_index, hotel_name, room_number, stay_status, assigned_by, assigned_at)
                            VALUES (%s, %s, %s, %s, %s, 'assigned', %s, CURRENT_TIMESTAMP)
                            """,
                            (booking_id, td, 1, hotel_name, old_room_number if old_room_number else None, manager_name)
                        )
                        rs_rows = [(1, old_room_number, 'assigned')]
                    except Exception as _e:
                        conn.rollback(); cursor.close(); conn.close()
                        return {"error": f"Failed to prepare room_stay for {td}: {_e}"}

                preferred = None
                preferred_unassigned_ci = None
                fallback_unassigned = None
                any_row = None
                prev_status_for_si = None
                non_null_indices = []
                for si, rn, st in rs_rows:
                    any_row = any_row or si
                    if rn == old_room_number:
                        preferred = si
                        prev_status_for_si = st
                        break
                    if rn is not None and str(rn).strip() != "":
                        non_null_indices.append((si, rn, st))
                    if (rn is None or str(rn).strip() == "") and str(st or '').strip().lower() == 'checked_in':
                        if preferred_unassigned_ci is None:
                            preferred_unassigned_ci = si
                    if rn is None or str(rn).strip() == "":
                        if fallback_unassigned is None:
                            fallback_unassigned = si
                # If no explicit old_room provided → must target a NULL slot; never select an existing room index
                inferred_assigned_si = None
                inferred_assigned_rn = None
                stay_index_td = preferred or preferred_unassigned_ci or fallback_unassigned or any_row
                if not old_room_number:
                    # Prefer a globally consistent index across dates if available
                    if global_si is not None:
                        stay_index_td = int(global_si)
                        # Ensure a row exists for this specific index on this date
                        try:
                            cursor.execute(
                                """
                                SELECT 1 FROM room_stay
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND stay_index = %s
                                LIMIT 1
                                """,
                                (hotel_name, booking_id, td, stay_index_td)
                            )
                            exists_row = bool(cursor.fetchone())
                        except Exception:
                            exists_row = False
                        if not exists_row:
                            try:
                                cursor.execute(
                                    """
                                    INSERT INTO room_stay (booking_id, stay_date, stay_index, hotel_name, room_number, stay_status, assigned_by, assigned_at)
                                    VALUES (%s, %s, %s, %s, NULL, 'pending', %s, CURRENT_TIMESTAMP)
                                    """,
                                    (booking_id, td, stay_index_td, hotel_name, manager_name)
                                )
                            except Exception:
                                pass
                    elif fallback_unassigned is not None:
                        stay_index_td = fallback_unassigned
                    else:
                        # create a new NULL slot to avoid stealing an existing room
                        try:
                            cursor.execute(
                                """
                                INSERT INTO room_stay (booking_id, stay_date, stay_index, hotel_name, room_number, stay_status, assigned_by, assigned_at)
                                VALUES (%s, %s, (
                                    SELECT COALESCE(MAX(stay_index),0)+1 FROM room_stay WHERE booking_id = %s AND stay_date = %s
                                ), %s, NULL, 'pending', %s, CURRENT_TIMESTAMP)
                                RETURNING stay_index
                                """,
                                (booking_id, td, booking_id, td, hotel_name, manager_name)
                            )
                            _rowni = cursor.fetchone()
                            stay_index_td = (_rowni or [None])[0]
                        except Exception:
                            pass
                current_old_rn = None
                if preferred:
                    # exact match used
                    for si, rn, st in rs_rows:
                        if si == preferred:
                            current_old_rn = rn
                            break
                elif inferred_assigned_si is not None:
                    current_old_rn = inferred_assigned_rn
                else:
                    # use the room at chosen index if any
                    for si, rn, st in rs_rows:
                        if si == stay_index_td:
                            current_old_rn = rn
                            break
                if prev_status_for_si is None:
                    for si, rn, st in rs_rows:
                        if si == stay_index_td:
                            prev_status_for_si = st
                            break
                norm_prev = (str(prev_status_for_si or '')).strip().lower()
                if norm_prev == 'checked_in':
                    new_stay_status_td = 'checked_in'
                elif norm_prev == 'departed':
                    new_stay_status_td = 'departed'
                else:
                    new_stay_status_td = 'assigned'

                # Update room_stay
                if rooms_booked_val == 1:
                    cursor.execute(
                        """
                        UPDATE room_stay
                        SET room_number = %s,
                            stay_status = %s,
                            assigned_by = %s,
                            assigned_at = CURRENT_TIMESTAMP
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                        """,
                        (new_room_number, new_stay_status_td, manager_name, hotel_name, booking_id, td)
                    )
                    rs_updated_flag = rs_updated_flag or (cursor.rowcount and cursor.rowcount > 0)
                else:
                    # For multi-room bookings, update ONLY the selected stay_index on that date
                    cursor.execute(
                    """
                    UPDATE room_stay
                    SET room_number = %s,
                        stay_status = %s,
                        assigned_by = %s,
                        assigned_at = CURRENT_TIMESTAMP
                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND stay_index = %s
                """,
                    (new_room_number, new_stay_status_td, manager_name, hotel_name, booking_id, td, stay_index_td)
            )
                    rs_updated_flag = rs_updated_flag or (cursor.rowcount and cursor.rowcount > 0)
                # Ensure no duplicate claim of the same room by other indices for this booking/date (safe for multi-room)
                    try:
                        cursor.execute(
                            """
                            WITH others AS (
                                SELECT stay_index FROM room_stay
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                                  AND stay_index <> %s AND room_number = %s
                                ORDER BY stay_index ASC
                            )
                            UPDATE room_stay rs
                            SET room_number = NULL
                            FROM others o
                            WHERE rs.hotel_name = %s AND rs.booking_id = %s AND rs.stay_date = %s AND rs.stay_index = o.stay_index
                            """,
                            (hotel_name, booking_id, td, stay_index_td, new_room_number,
                             hotel_name, booking_id, td)
                        )
                    except Exception:
                        pass

                # Sync room_stay with authoritative assignments: clear any room_stay rooms not present in assignments for this booking/date
                try:
                    cursor.execute(
                        """
                        SELECT room_number FROM room_number_assignments
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND booking_id IS NOT NULL
                        """,
                        (hotel_name, booking_id, td)
                    )
                    assigned_rooms_set = {str(r[0]) for r in (cursor.fetchall() or []) if r and r[0] is not None}
                    if assigned_rooms_set:
                        placeholders = ",".join(["%s"] * len(assigned_rooms_set))
                        params = [hotel_name, booking_id, td] + list(assigned_rooms_set)
                        cursor.execute(
                            f"""
                            UPDATE room_stay
                            SET room_number = NULL
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                              AND room_number IS NOT NULL AND room_number NOT IN ({placeholders})
                            """,
                            params
                        )
                    # After sync, ensure deficit NULL slots exist for visibility
                    try:
                        cursor.execute(
                            """
                            SELECT COALESCE(SUM(bs.rooms_booked),0)
                            FROM booking_stays bs
                            WHERE bs.booking_id = %s AND bs.stay_date = %s
                            """,
                            (booking_id, td)
                        )
                        required_slots = int((cursor.fetchone() or [0])[0] or 0)
                    except Exception:
                        required_slots = rooms_booked_val
                    try:
                        cursor.execute(
                            """
                            SELECT 
                              COUNT(*) FILTER (WHERE room_number IS NOT NULL) AS assigned_cnt,
                              COUNT(*) AS total_cnt,
                              COUNT(*) FILTER (WHERE room_number IS NULL) AS null_cnt
                            FROM room_stay
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                            """,
                            (hotel_name, booking_id, td)
                        )
                        rowc = cursor.fetchone() or (0,0,0)
                        assigned_cnt = int(rowc[0] or 0)
                        null_cnt = int(rowc[2] or 0)
                        need_null = max(required_slots - assigned_cnt, 0)
                        if need_null > null_cnt:
                            to_insert = need_null - null_cnt
                            for _ in range(to_insert):
                                try:
                                    cursor.execute(
                                        """
                                        INSERT INTO room_stay (booking_id, stay_date, stay_index, hotel_name, room_number, stay_status, assigned_by, assigned_at)
                                        VALUES (%s, %s, (
                                            SELECT COALESCE(MAX(stay_index),0)+1 FROM room_stay WHERE booking_id = %s AND stay_date = %s
                                        ), %s, NULL, 'pending', %s, CURRENT_TIMESTAMP)
                                        """,
                                        (booking_id, td, booking_id, td, hotel_name, manager_name)
                                    )
                                except Exception:
                                    pass
                    except Exception:
                        pass
                except Exception:
                    pass
            if cursor.rowcount == 0:
                conn.rollback(); cursor.close(); conn.close()
                return {"error": f"Failed to update room_stay for {td}"}
            else:

                # Clear old room assignment for this date (scoped by booking)
                if old_room_number or current_old_rn:
                    cursor.execute(
                        """
                        UPDATE room_number_assignments
                        SET booking_id = NULL,
                            guest_phone = NULL,
                            assignment_status = 'available',
                            updated_by = %s
                        WHERE hotel_name = %s AND room_number = %s AND stay_date = %s AND booking_id = %s
                        """,
                        (manager_name, hotel_name, (old_room_number or current_old_rn), td, booking_id)
                    )

                # Set new room assignment for this date (UPSERT to guarantee write)
                cursor.execute(
                    """
                    INSERT INTO room_number_assignments (
                        hotel_name, room_type, room_number, stay_date, assignment_status, booking_id, guest_phone, assigned_by, assigned_at, updated_by
                    ) VALUES (%s, %s, %s, %s, 'assigned', %s, %s, %s, CURRENT_TIMESTAMP, %s)
                    ON CONFLICT (hotel_name, room_number, stay_date)
                    DO UPDATE SET
                        room_type = COALESCE(room_number_assignments.room_type, EXCLUDED.room_type),
                        booking_id = EXCLUDED.booking_id,
                        guest_phone = EXCLUDED.guest_phone,
                        assignment_status = 'assigned',
                        updated_by = EXCLUDED.updated_by,
                        assigned_by = EXCLUDED.assigned_by,
                        assigned_at = EXCLUDED.assigned_at
                    """,
                    (hotel_name, booking_room_type, new_room_number, td, booking_id, booking_guest_phone, manager_name, manager_name)
                )
                # Ensure there remains at least the required number of rows (keep unassigned slots visible)
                try:
                    cursor.execute(
                        """
                        SELECT COUNT(*) FROM room_stay
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                        """,
                        (hotel_name, booking_id, td)
                    )
                    cnt_rows = int((cursor.fetchone() or [0])[0] or 0)
                    # Determine required slots on this date
                    req_slots = rooms_booked_val
                    try:
                        cursor.execute(
                            """
                            SELECT COALESCE(SUM(rooms_booked),0)
                            FROM booking_stays
                            WHERE booking_id = %s AND stay_date = %s
                            """,
                            (booking_id, td)
                        )
                        req_slots = max(req_slots or 0, int((cursor.fetchone() or [0])[0] or 0))
                    except Exception:
                        pass
                    try:
                        # Include baseline count snapshot from earlier in the loop (if taken for this date)
                        cursor.execute(
                            """
                            SELECT COUNT(*) FROM room_stay
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                            """,
                            (hotel_name, booking_id, td)
                        )
                        _baseline_cnt = int((cursor.fetchone() or [0])[0] or 0)
                        req_slots = max(req_slots or 0, int(_baseline_cnt or 0))
                    except Exception:
                        pass
                    if req_slots > 0 and cnt_rows < req_slots:
                        # Insert missing NULL slots to keep unassigned bars visible
                        need = req_slots - cnt_rows
                        for _ in range(need):
                            try:
                                cursor.execute(
                                    """
                                    INSERT INTO room_stay (booking_id, stay_date, stay_index, hotel_name, room_number, stay_status, assigned_by, assigned_at)
                                    VALUES (%s, %s, (
                                        SELECT COALESCE(MAX(stay_index),0)+1 FROM room_stay WHERE booking_id = %s AND stay_date = %s
                                    ), %s, NULL, 'pending', %s, CURRENT_TIMESTAMP)
                                    """,
                                    (booking_id, td, booking_id, td, hotel_name, manager_name)
                                )
                            except Exception:
                                pass
                except Exception:
                    pass
                # Ensure room_stay reflects the new assignment ONLY when no slot was updated above
                if not rs_updated_flag:
                    try:
                        cursor.execute(
                            """
                            SELECT 1 FROM room_stay
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number = %s
                            LIMIT 1
                            """,
                            (hotel_name, booking_id, td, new_room_number)
                        )
                        exists_rs = bool(cursor.fetchone())
                    except Exception:
                        exists_rs = False
                    if not exists_rs:
                        # Prefer a checked_in unassigned slot; else first NULL slot
                        try:
                            cursor.execute(
                                """
                                SELECT stay_index
                                FROM room_stay
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number IS NULL
                                ORDER BY CASE WHEN LOWER(COALESCE(stay_status,''))='checked_in' THEN 0 ELSE 1 END, stay_index
                                LIMIT 1
                                """,
                                (hotel_name, booking_id, td)
                            )
                            row_si = cursor.fetchone()
                            if row_si:
                                cursor.execute(
                                    """
                                    UPDATE room_stay
                                    SET room_number = %s,
                                        stay_status = %s,
                                        assigned_by = %s,
                                        assigned_at = COALESCE(assigned_at, CURRENT_TIMESTAMP)
                                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND stay_index = %s
                                    """,
                                    (new_room_number, new_stay_status_td, manager_name, hotel_name, booking_id, td, row_si[0])
                                )
                        except Exception:
                            pass
                # Ensure row_type is preserved for downstream readers (calendar)
                try:
                    cursor.execute(
                        """
                        UPDATE room_number_assignments
                        SET room_type = COALESCE(room_type, %s)
                        WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
                        """,
                        (booking_room_type, hotel_name, new_room_number, td)
                    )
                except Exception:
                    pass
                # Ensure uniqueness when single room per night (calendar expects single 'assigned' row)
                if rooms_booked_val == 1:
                    cursor.execute(
                        """
                        UPDATE room_number_assignments
                        SET booking_id = NULL,
                            guest_phone = NULL,
                            assignment_status = 'available',
                            updated_by = %s
                        WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s AND room_number <> %s
                        """,
                        (manager_name, hotel_name, td, booking_id, new_room_number)
                    )
                    # Normalize room_stay rows: set the new room on all rows for that date
                    try:
                        cursor.execute(
                            """
                            UPDATE room_stay
                            SET room_number = %s
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                            """,
                            (new_room_number, hotel_name, booking_id, td)
                        )
                    except Exception:
                        pass

                # Adjust readiness if already checked in and the affected stay_date is today or yesterday
                try:
                    from datetime import timedelta as _td
                    _is_effective_day = (td == today) or (td == (today - _td(days=1)))
                    if new_stay_status_td == 'checked_in' and _is_effective_day:
                        if new_room_number:
                            cursor.execute(
                                """
                                UPDATE room_readiness_status
                                SET readiness_status = 'occupied', status_changed_at = CURRENT_TIMESTAMP, changed_by = %s, notes = %s
                                WHERE hotel_name = %s AND room_number = %s
                                """,
                                (manager_name, f'Relocated to {new_room_number} from {old_room_number}', hotel_name, new_room_number)
                            )
                            try:
                                print(f"ROOM READINESS UPDATE: occupied | hotel={hotel_name} room={new_room_number} date={td} booking={booking_id}")
                            except Exception:
                                pass
                        if old_room_number:
                            cursor.execute(
                                """
                                UPDATE room_readiness_status
                                SET readiness_status = %s, status_changed_at = CURRENT_TIMESTAMP, changed_by = %s, notes = %s
                                WHERE hotel_name = %s AND room_number = %s
                                """,
                                ('clean' if mark_old_clean else 'dirty', manager_name, f'Relocated from {old_room_number} to {new_room_number}', hotel_name, old_room_number)
                            )
                            try:
                                print(f"ROOM READINESS UPDATE: dirty    | hotel={hotel_name} room={old_room_number} date={td} booking={booking_id}")
                            except Exception:
                                pass
                except Exception:
                    pass

            # Enforce consistency across entire stay for single-room bookings
            try:
                if rooms_booked_val == 1 and all_stay_dates:
                    for td_all in all_stay_dates:
                        # Ensure room_stay rows all point to the new room for that date
                        try:
                            cursor.execute(
                                """
                                UPDATE room_stay
                                SET room_number = %s,
                                    assigned_by = %s,
                                    assigned_at = COALESCE(assigned_at, CURRENT_TIMESTAMP)
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                                """,
                                (new_room_number, manager_name, hotel_name, booking_id, td_all)
                            )
                        except Exception:
                            pass
                        # Upsert assignment rows for the new room/date
                        try:
                            cursor.execute(
                                """
                                INSERT INTO room_number_assignments (
                                    hotel_name, room_type, room_number, stay_date, assignment_status, booking_id, guest_phone, assigned_by, assigned_at, updated_by
                                ) VALUES (%s, %s, %s, %s, 'assigned', %s, %s, %s, CURRENT_TIMESTAMP, %s)
                                ON CONFLICT (hotel_name, room_number, stay_date)
                                DO UPDATE SET
                                    room_type = COALESCE(room_number_assignments.room_type, EXCLUDED.room_type),
                                    booking_id = EXCLUDED.booking_id,
                                    guest_phone = EXCLUDED.guest_phone,
                                    assignment_status = 'assigned',
                                    updated_by = EXCLUDED.updated_by,
                                    assigned_by = EXCLUDED.assigned_by,
                                    assigned_at = EXCLUDED.assigned_at
                                """,
                                (hotel_name, booking_room_type, new_room_number, td_all, booking_id, booking_guest_phone, manager_name, manager_name)
                            )
                        except Exception:
                            pass
                        # Ensure no other rooms keep this booking/date
                        try:
                            cursor.execute(
                                """
                                UPDATE room_number_assignments
                                SET booking_id = NULL,
                                    guest_phone = NULL,
                                    assignment_status = 'available',
                                    updated_by = %s
                                WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s AND room_number <> %s
                                """,
                                (manager_name, hotel_name, td_all, booking_id, new_room_number)
                            )
                        except Exception:
                            pass
            except Exception:
                pass

                # Reconcile room_stay rows to preserve required NULL slots for multi-room bookings
                try:
                    # Determine required slots for this date
                    req_slots = rooms_booked_val
                    try:
                        cursor.execute(
                            """
                            SELECT COALESCE(SUM(rooms_booked),0)
                            FROM booking_stays
                            WHERE booking_id = %s AND stay_date = %s
                            """,
                            (booking_id, td)
                        )
                        _bs_req = int((cursor.fetchone() or [0])[0] or 0)
                        req_slots = max(req_slots or 0, _bs_req)
                    except Exception:
                        pass
                    # Assigned rooms from authoritative assignments table
                    cursor.execute(
                        """
                        SELECT room_number FROM room_number_assignments
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND booking_id IS NOT NULL
                        ORDER BY room_number
                        """,
                        (hotel_name, booking_id, td)
                    )
                    assigned_rooms_set = [str(r[0]) for r in (cursor.fetchall() or []) if r and r[0] is not None]
                    # Current room_stay snapshot
                    cursor.execute(
                        """
                        SELECT stay_index, room_number, COALESCE(stay_status,'')
                        FROM room_stay
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                        ORDER BY stay_index
                        """,
                        (hotel_name, booking_id, td)
                    )
                    rs_snap = cursor.fetchall() or []
                    any_ci = False
                    for _si, _rn, _st in rs_snap:
                        if str(_st or '').strip().lower() == 'checked_in':
                            any_ci = True; break
                    current_rooms = [str(rn) for (_si, rn, _st) in rs_snap if rn]
                    current_rooms_set = set(current_rooms)
                    # Add missing assigned rooms into NULL slots
                    to_add = [rn for rn in assigned_rooms_set if rn not in current_rooms_set]
                    if to_add:
                        # candidate NULL rows (prefer checked_in status if any)
                        null_rows_ci = [si for (si, rn, st) in rs_snap if (not rn) and str(st or '').strip().lower() == 'checked_in']
                        null_rows_any = [si for (si, rn, st) in rs_snap if not rn]
                        for rn in to_add:
                            target_si2 = None
                            if null_rows_ci:
                                target_si2 = null_rows_ci.pop(0)
                                try:
                                    null_rows_any.remove(target_si2)
                                except Exception:
                                    pass
                            elif null_rows_any:
                                target_si2 = null_rows_any.pop(0)
                            if target_si2 is None:
                                # insert a new NULL row first
                                try:
                                    cursor.execute(
                                        """
                                        INSERT INTO room_stay (booking_id, stay_date, stay_index, hotel_name, room_number, stay_status, assigned_by, assigned_at)
                                        VALUES (%s, %s, (
                                            SELECT COALESCE(MAX(stay_index),0)+1 FROM room_stay WHERE booking_id = %s AND stay_date = %s
                                        ), %s, NULL, %s, %s, CURRENT_TIMESTAMP)
                                        RETURNING stay_index
                                        """,
                                        (booking_id, td, booking_id, td, hotel_name, 'pending', manager_name)
                                    )
                                    new_si = cursor.fetchone()[0]
                                    target_si2 = new_si
                                except Exception:
                                    target_si2 = None
                            if target_si2 is not None:
                                try:
                                    cursor.execute(
                                        """
                                        UPDATE room_stay
                                        SET room_number = %s,
                                            stay_status = %s,
                                            assigned_by = %s,
                                            assigned_at = COALESCE(assigned_at, CURRENT_TIMESTAMP)
                                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND stay_index = %s
                                        """,
                                        (rn, 'assigned', manager_name, hotel_name, booking_id, td, target_si2)
                                    )
                                except Exception:
                                    pass
                    # Clear extra rooms not in authoritative set only for single-room bookings; for multi-room, do not clear
                    if rooms_booked_val == 1:
                        to_clear = [rn for rn in current_rooms if rn not in assigned_rooms_set]
                        for rn in to_clear:
                            try:
                                cursor.execute(
                                    """
                                    UPDATE room_stay
                                    SET room_number = NULL
                                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number = %s
                                    """,
                                    (hotel_name, booking_id, td, rn)
                                )
                            except Exception:
                                pass
                    # Ensure at least req_slots rows exist (keep an unassigned visible)
                    try:
                        cursor.execute(
                            """
                            SELECT COUNT(*) FROM room_stay
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                            """,
                            (hotel_name, booking_id, td)
                        )
                        total_rows = int((cursor.fetchone() or [0])[0] or 0)
                        if req_slots > 0 and total_rows < req_slots:
                            for _ in range(req_slots - total_rows):
                                try:
                                    cursor.execute(
                                        """
                                        INSERT INTO room_stay (booking_id, stay_date, stay_index, hotel_name, room_number, stay_status, assigned_by, assigned_at)
                                        VALUES (%s, %s, (
                                            SELECT COALESCE(MAX(stay_index),0)+1 FROM room_stay WHERE booking_id = %s AND stay_date = %s
                                        ), %s, NULL, %s, %s, CURRENT_TIMESTAMP)
                                        """,
                                        (booking_id, td, booking_id, td, hotel_name, 'pending', manager_name)
                                    )
                                except Exception:
                                    pass
                    except Exception:
                        pass
                except Exception:
                    pass

                # Aggregate per-night assignment completeness with cap to rooms_booked
            try:
                cursor.execute(
                    """
                    SELECT COUNT(*) FILTER (WHERE room_number IS NOT NULL) AS assigned_cnt,
                           COUNT(*) AS total_cnt
                    FROM room_stay
                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                    """,
                    (hotel_name, booking_id, target_date)
                )
                row_cnts = cursor.fetchone() or (0, 0)
                assigned_cnt = int(row_cnts[0] or 0)
                total_cnt = int(row_cnts[1] or 0)
                # Cap assigned rows to required slots (rooms_booked_val) by clearing extras if any
                if rooms_booked_val > 0 and assigned_cnt > rooms_booked_val:
                    try:
                        cursor.execute(
                            """
                            WITH ranked AS (
                                SELECT stay_index, room_number,
                                       ROW_NUMBER() OVER (ORDER BY stay_index) AS rn
                                FROM room_stay
                                WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s AND room_number IS NOT NULL
                            )
                            UPDATE room_stay rs
                            SET room_number = NULL
                            FROM ranked r
                            WHERE rs.hotel_name = %s AND rs.booking_id = %s AND rs.stay_date = %s
                              AND rs.stay_index = r.stay_index AND r.rn > %s
                            """,
                            (hotel_name, booking_id, target_date, hotel_name, booking_id, target_date, rooms_booked_val)
                        )
                    except Exception:
                        pass
                # Determine required slots for this date using booking_stays, room_bookings, or current row count
                required_slots_date = rooms_booked_val
                try:
                    cursor.execute(
                        """
                        SELECT COALESCE(SUM(rooms_booked),0)
                        FROM booking_stays
                        WHERE booking_id = %s AND stay_date = %s
                        """,
                        (booking_id, target_date)
                    )
                    bs_req = int((cursor.fetchone() or [0])[0] or 0)
                    required_slots_date = max(required_slots_date or 0, bs_req)
                except Exception:
                    pass
                try:
                    cursor.execute(
                        """
                        SELECT COUNT(*) FROM room_stay
                        WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                        """,
                        (hotel_name, booking_id, target_date)
                    )
                    rs_rows_cnt = int((cursor.fetchone() or [0])[0] or 0)
                    required_slots_date = max(required_slots_date or 0, rs_rows_cnt)
                except Exception:
                    pass
                if required_slots_date > 0:
                    if assigned_cnt >= required_slots_date:
                        cursor.execute(
                            """
                            UPDATE room_stay
                            SET stay_status = 'assigned',
                                assigned_at = COALESCE(assigned_at, CURRENT_TIMESTAMP)
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                              AND stay_status NOT IN ('checked_in','departed','cancelled')
                            """,
                            (hotel_name, booking_id, target_date)
                        )
                    else:
                        cursor.execute(
                            """
                            UPDATE room_stay
                            SET stay_status = 'pending'
                            WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                              AND stay_status NOT IN ('checked_in','departed','cancelled')
                            """,
                            (hotel_name, booking_id, target_date)
                        )
            except Exception:
                # Non-fatal aggregation failure
                    pass

            if slot_index is not None and rooms_booked_val > 1 and new_room_number and all_stay_dates:
                self._hold_room_for_stay_slot(
                    cursor, hotel_name, manager_name, booking_id, booking_room_type, booking_guest_phone,
                    all_stay_dates, slot_index, old_room_number, new_room_number
                )

            if mark_old_clean is not None and old_room_number:
                try:
                    cursor.execute(
                        """
                        UPDATE room_readiness_status
                        SET readiness_status = %s, status_changed_at = CURRENT_TIMESTAMP, changed_by = %s, notes = %s
                        WHERE hotel_name = %s AND room_number = %s
                        """,
                        ('clean' if mark_old_clean else 'dirty', manager_name, f'Moved stay off {old_room_number}', hotel_name, old_room_number)
                    )
                except Exception:
                    pass

            conn.commit()
            # Fetch assigned_at to include in response
            assigned_ts_out = None
            try:
                cursor2 = None
                try:
                    cursor2 = conn.cursor()
                except Exception:
                    # Reopen if needed
                    try:
                        conn = self.get_postgresql_connection()
                        cursor2 = conn.cursor()
                    except Exception:
                        cursor2 = None
                if cursor2 is not None:
                    cursor2.execute(
                        """
                        SELECT MIN(COALESCE(rs.assigned_at, rna.assigned_at))
                        FROM room_stay rs
                        LEFT JOIN room_number_assignments rna
                          ON rna.hotel_name = rs.hotel_name
                         AND rna.booking_id = rs.booking_id
                         AND rna.stay_date = rs.stay_date
                        WHERE rs.hotel_name = %s AND rs.booking_id = %s AND rs.stay_date = %s
                        """,
                        (hotel_name, booking_id, target_date)
                    )
                    _row = cursor2.fetchone()
                    if _row and _row[0] is not None:
                        try:
                            assigned_ts_out = _row[0].isoformat() if hasattr(_row[0], 'isoformat') else _row[0]
                        except Exception:
                            assigned_ts_out = str(_row[0])
                    try:
                        cursor2.close()
                    except Exception:
                        pass
            except Exception:
                assigned_ts_out = None
            try:
                cursor.close(); conn.close()
            except Exception:
                pass
            return {
                "result": "success",
                "hotel_name": hotel_name,
                "booking_id": booking_id,
                "stay_date": target_date.isoformat(),
                "old_room_number": old_room_number,
                "new_room_number": new_room_number,
                "assigned_at": assigned_ts_out
            }
        except Exception as e:
            try:
                if 'conn' in locals(): conn.rollback()
            except Exception:
                pass
            try:
                if 'cursor' in locals(): cursor.close()
                if 'conn' in locals(): conn.close()
            except Exception:
                pass
            return {"error": str(e)}

    def read_assignment_history_internal(self, booking_id: str) -> dict:
        """READ assignment history for a booking: list all room_number assignments per stay_date with timestamps."""
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            # Resolve hotel for timezone conversion
            hotel_name = None
            try:
                cursor.execute(
                    """
                    SELECT hotel_name FROM room_bookings WHERE booking_id = %s
                    """,
                    (booking_id,)
                )
                row = cursor.fetchone()
                if row:
                    hotel_name = row[0]
            except Exception:
                hotel_name = None
            # Read assignment history from room_stay
            cursor.execute(
                """
                SELECT stay_date, COALESCE(room_number, ''), assigned_at, assigned_by, stay_index
                FROM room_stay
                WHERE booking_id = %s
                ORDER BY stay_date, assigned_at NULLS LAST, stay_index
                """,
                (booking_id,)
            )
            rows = cursor.fetchall() or []
            # Also include entries from room_number_assignments for this booking
            try:
                cursor.execute(
                    """
                    SELECT stay_date, COALESCE(room_number, ''), assigned_at, assigned_by
                    FROM room_number_assignments
                    WHERE booking_id = %s
                    ORDER BY stay_date, assigned_at NULLS LAST
                    """,
                    (booking_id,)
                )
                rows_rna = cursor.fetchall() or []
            except Exception:
                rows_rna = []
            # Convert timestamps to hotel-local ISO
            entries = []
            try:
                from booking_system.timezone_utils import to_local_iso  # type: ignore
            except Exception:
                to_local_iso = lambda dt, _tz: (dt.isoformat() + "Z") if dt else None  # type: ignore
            for sdate, rn, assigned_at, assigned_by, si in rows:
                ts = (assigned_at.isoformat() if hasattr(assigned_at,'isoformat') else assigned_at) if assigned_at else None
                entries.append({
                    "source": "room_stay",
                    "stay_date": sdate.isoformat() if hasattr(sdate, 'isoformat') else str(sdate),
                    "room_number": str(rn or ''),
                    "assigned_at": ts,
                    "assigned_by": assigned_by,
                    "stay_index": si,
                })
            for sdate, rn, assigned_at, assigned_by in rows_rna:
                ts = (assigned_at.isoformat() if hasattr(assigned_at,'isoformat') else assigned_at) if assigned_at else None
                entries.append({
                    "source": "room_number_assignments",
                    "stay_date": sdate.isoformat() if hasattr(sdate, 'isoformat') else str(sdate),
                    "room_number": str(rn or ''),
                    "assigned_at": ts,
                    "assigned_by": assigned_by,
                })
            # Sort by timestamp then date/room
            entries.sort(key=lambda x: (x.get('assigned_at') or '', x.get('stay_date') or '', x.get('room_number') or ''))
            cursor.close(); conn.close()
            return {"booking_id": booking_id, "hotel_name": hotel_name, "entries": entries}
        except Exception as e:
            try:
                if 'conn' in locals(): conn.close()
            except Exception:
                pass
            return {"booking_id": booking_id, "entries": [], "error": str(e)}


# Create global instance for LLM-enhanced operations
intelligent_front_desk = IntelligentFrontDeskManager()

# Remove all Pydantic BaseModel classes and tool wrappers that use them
# Refactor all tool wrappers to use explicit keyword arguments

@tool
def assign_room_number(hotel_name: str, manager_name: str, booking_id: str, room_number: str = None, stay_dates: list = None, room_numbers_by_date: dict = None, rooms: list = None, auto_fill: bool = False) -> str:
    """MANAGE (WRITE): Assign room(s) to a booking with strict READ-first validation.

    Usage:
    - Preferred: Provide rooms list ["ROOM101", "ROOM102"] for the first night; applies to all nights.
    - Alternative: Provide room_numbers_by_date: {"YYYY-MM-DD": ["ROOM101", "ROOM102"]} for exact per-night mapping.
    - For single-room-per-night bookings, you may provide room_number instead.
    - Set auto_fill=True to allow the system to fill missing slots from available rooms of the SAME room_type; default is False (no guessing).

    Notes:
    - This tool validates booking dates, required slots, room availability, and room_type matching.
    - Per-night stay_status becomes 'assigned' only when all slots for that date are filled; otherwise remains 'pending'.
    """
    return intelligent_front_desk.update_room_assignment_auto(
        hotel_name=hotel_name,
        manager_name=manager_name,
        booking_id=booking_id,
        room_number=room_number,
        stay_dates=stay_dates,
        room_numbers_by_date=room_numbers_by_date,
        rooms=rooms,
        auto_fill=auto_fill,
    )

@tool
def unassign_room_number(hotel_name: str, manager_name: str, booking_id: str, stay_dates: list = None, room_numbers_by_date: dict = None) -> str:
    """MANAGE (WRITE): Unassign room(s) for a booking.

    - Provide room_numbers_by_date to unassign specific rooms per date.
    - If omitted, unassigns all currently assigned rooms for the booking on the given dates (or all booking dates if stay_dates is None).
    - Enforces per-date status back to 'pending' when any slot is unassigned.
    """
    return intelligent_front_desk.unassign_room_number_internal(
        hotel_name=hotel_name,
        manager_name=manager_name,
        booking_id=booking_id,
        stay_dates=stay_dates,
        room_numbers_by_date=room_numbers_by_date,
    )

@tool
def read_assignment_dashboard(hotel_name: str, manager_name: str, date: str = None) -> str:
    """READ comprehensive assignment dashboard data for front desk operations. Requires hotel_name and manager_name."""
    return intelligent_front_desk.read_assignment_dashboard(hotel_name, manager_name, date)

@tool
def mark_room_out_of_order(hotel_name: str, manager_name: str, room_number: str, start_date: str, end_date: str, reason: str) -> str:
    """Set room as out-of-order for a date range. Dates must be in YYYY-MM-DD format. Requires hotel_name and manager_name."""
    return intelligent_front_desk.update_room_out_of_order_status_internal(hotel_name, manager_name, room_number, start_date, end_date, reason)

@tool
def mark_room_available(hotel_name: str, manager_name: str, room_number: str, start_date: str = None, end_date: str = None) -> str:
    """
    USE THIS TOOL TO MAKE A ROOM AVAILABLE AFTER MAINTENANCE OR OUT-OF-ORDER PERIOD.
    If start_date or end_date is not provided, the tool will automatically find all future out-of-order dates for the room and restore availability for that period.
    """
    return intelligent_front_desk.update_room_available_status_internal(hotel_name, manager_name, room_number, start_date, end_date)

@tool
def update_room_readiness_status(hotel_name: str, manager_name: str, room_number: str, new_status: str, notes: str = "") -> str:
    """
    DO NOT USE FOR MAINTENANCE OR OUT-OF-ORDER ROOMS. USE update_room_available_status INSTEAD.
    This tool is ONLY for cleaning status (clean, dirty, cleaning, occupied).
    """
    return intelligent_front_desk.update_room_readiness_status_internal(hotel_name, manager_name, room_number, new_status, notes)

@tool
def read_rooms_exceeding_clean_threshold(hotel_name: str, manager_name: str, threshold_days: int = None) -> str:
    """
    READ rooms that have been clean longer than threshold and need re-cleaning
    """
    return intelligent_front_desk.read_rooms_exceeding_clean_threshold_internal(hotel_name, manager_name, threshold_days)

@tool
def check_in_guest(hotel_name: str, manager_name: str, booking_id: str, room_number: str = None, notes: str = "") -> str:
    """Mark guest check-in status and room readiness. If room_number is None, will look up the assigned room for the booking_id and today."""
    return intelligent_front_desk.update_guest_check_in_status_internal(hotel_name, manager_name, booking_id, room_number, notes)

@tool
def check_out_guest(hotel_name: str, manager_name: str, booking_id: str, room_number: str = None, notes: str = "") -> str:
    """Mark guest check-out status and room readiness. If room_number is None, will look up the assigned room for the booking_id and today."""
    return intelligent_front_desk.update_guest_check_out_status_internal(hotel_name, manager_name, booking_id, room_number, notes)

@tool
def update_rooms_by_threshold_auto(hotel_name: str, manager_name: str) -> str:
    """UPDATE rooms that exceed their clean threshold (bulk operation)"""
    return intelligent_front_desk.update_rooms_by_threshold_auto_internal(hotel_name, manager_name)

# NEW LLM-Enhanced Tools
@tool
def override_pricing(hotel_name: str, manager_name: str, booking_id: str, new_total_amount: float) -> dict:
    """WRITE: Override a booking's total amount and recompute payment tracking.
    Parameters:
    - hotel_name: context hotel (required by LEON policy; not used for lookup since booking_id is global)
    - manager_name: manager executing the change (UPPERCASE)
    - booking_id: natural key (e.g., +60123456789booking1)
    - new_total_amount
    """
    return intelligent_front_desk.override_pricing_internal(hotel_name, (manager_name or "SYSTEM").upper(), booking_id, float(new_total_amount))

@tool
def suggest_room_assignment(hotel_name: str, manager_name: str, request: str, booking_id: str, stay_date: str = None) -> str:
    """
    SUGGEST room assignment based on natural language request (LLM parsing at tool/agent level only).
    Optionally filter by stay_date (YYYY-MM-DD). If not provided, defaults to today.
    """
    from booking_system.front_desk_manager import intelligent_front_desk
    conn = intelligent_front_desk.get_postgresql_connection()
    cursor = conn.cursor()
    if not stay_date:
        stay_date = datetime.now(timezone.utc).date().isoformat()
    cursor.execute("""
        SELECT DISTINCT pra.room_number, pra.room_type, pra.assignment_status, pra.stay_date
        FROM room_number_assignments pra
        WHERE pra.hotel_name = %s AND pra.stay_date = %s
        ORDER BY pra.room_number
    """, (hotel_name, stay_date))
    rooms = cursor.fetchall()
    cursor.close()
    conn.close()
    if not rooms:
        return f"❌ No room data found for {hotel_name} on {stay_date}"
    room_context = "AVAILABLE ROOMS:\n"
    for room_num, room_type, status, stay_date_row in rooms:
        status = status or 'available'
        room_context += f"- Room {room_num}: {room_type}, Status: {status}\n"
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.1,
        openai_api_key=os.getenv('OPENAI_API_KEY')
    )
    parsing_prompt = f"""
    CONTEXT: Front desk room assignment for {hotel_name}
    BOOKING: {booking_id}
    CURRENT REQUEST: \"{request}\"
    {room_context}
    TASK: Parse the natural language request and recommend the best room assignment.
    Consider:
    - Guest preferences (view, floor, quiet, size, bed type)
    - Special needs (accessibility, connecting rooms, away from elevator)
    - Room availability and characteristics
    - Hotel layout considerations
    Respond as valid JSON with keys: recommended_room, reasoning, alternatives, special_notes.
    1. recommended_room: [room_number]
    2. reasoning: Why this room matches the request
    3. alternatives: 2-3 backup options (list)
    4. special_notes: Any considerations or guest communication needed
    If request is unclear or impossible to fulfill, suggest clarification questions in special_notes.
    """
    try:
        response = llm.invoke([{"role": "user", "content": parsing_prompt}])
        content = response.content.strip()
        if content.startswith('```json'):
            content = content.replace('```json', '').replace('```', '').strip()
        elif content.startswith('```'):
            content = content.replace('```', '').strip()
        parsed = json.loads(content)
        recommended_room = parsed.get('recommended_room', '')
        reasoning = parsed.get('reasoning', '')
        alternatives = parsed.get('alternatives', [])
        special_notes = parsed.get('special_notes', '')
        return intelligent_front_desk.parse_room_assignment_request(
            hotel_name,
            manager_name,
            booking_id,
            recommended_room,
            reasoning,
            alternatives,
            special_notes
        )
    except Exception as e:
        return f"❌ Error parsing room assignment request: {e}"

@tool
def read_maintenance_issue_analysis(hotel_name: str, manager_name: str, issue_description: str, room_number: str) -> str:
    """READ maintenance issue analysis and provide structured recommendations (LLM parsing at tool/agent level only)"""
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.1,
        openai_api_key=os.getenv('OPENAI_API_KEY')
    )
    interpretation_prompt = f"""
    CONTEXT: Hotel maintenance issue reporting
    HOTEL: {hotel_name}
    ROOM: {room_number}
    CURRENT ISSUE: "{issue_description}"
    TASK: Analyze the maintenance issue and provide structured recommendations.
    Determine:
    1. SEVERITY: Minor, Moderate, Major, Critical
    2. ROOM STATUS: Can remain available, Should be out-of-order, Immediate evacuation needed
    3. ESTIMATED DURATION: How long will repairs take (hours/days/weeks)
    4. DEPARTMENTS NEEDED: Housekeeping, Maintenance, External contractors
    5. GUEST IMPACT: Can guest stay in room, needs relocation, safety concerns
    6. URGENCY: Immediate, Same day, Within 24-48 hours, Scheduled maintenance
    Provide specific recommendations for:
    - Whether to set room out-of-order
    - Start and end dates for closure
    - Staff assignments and priorities
    - Guest communication if needed
    Format as valid JSON with keys: severity, room_status, estimated_duration, departments_needed, guest_impact, urgency, recommendations
    """
    try:
        response = llm.invoke([{"role": "user", "content": interpretation_prompt}])
        content = response.content.strip()
        # Attempt to extract JSON from the response
        if content.startswith('```json'):
            content = content.replace('```json', '').replace('```', '').strip()
        elif content.startswith('```'):
            content = content.replace('```', '').strip()
        parsed = json.loads(content)
        severity = parsed.get('severity', '')
        room_status = parsed.get('room_status', '')
        estimated_duration = parsed.get('estimated_duration', '')
        departments_needed = parsed.get('departments_needed', [])
        guest_impact = parsed.get('guest_impact', '')
        urgency = parsed.get('urgency', '')
        recommendations = parsed.get('recommendations', {})
        return intelligent_front_desk.interpret_maintenance_issue(
            hotel_name,
            manager_name,
            room_number,
            severity,
            room_status,
            estimated_duration,
            departments_needed,
            guest_impact,
            urgency,
            recommendations
        )
    except Exception as e:
        return f"❌ Error interpreting maintenance issue: {e}"

@tool
def read_room_assignment_tool(hotel_name: str, room_number: str, date_range: str = "all") -> str:
    """
    Given hotel_name, room_number, and date_range, return the associated booking_id(s) for those assignments.
    date_range can be a single date (YYYY-MM-DD), a comma-separated list, or 'all'.
    Returns a JSON list of booking_ids.
    """
    conn = intelligent_front_desk.get_postgresql_connection()
    cursor = conn.cursor()
    query = "SELECT DISTINCT booking_id FROM room_stay WHERE hotel_name = %s AND room_number = %s"
    params = [hotel_name, room_number]
    if date_range != "all":
        dates = [d.strip() for d in date_range.split(",") if d.strip()]
        if len(dates) == 1:
            query += " AND stay_date = %s"
            params.append(dates[0])
        elif len(dates) > 1:
            query += " AND stay_date IN (%s)" % (",".join(["%s"] * len(dates)))
            params.extend(dates)
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    booking_ids = [r[0] for r in rows]
    return json.dumps(booking_ids)

@tool
def read_room_readiness_status_tool(hotel_name: str, room_number: str) -> str:
    """Check if room is clean, dirty, cleaning, occupied"""
    return intelligent_front_desk.read_room_readiness_status(hotel_name, room_number)

@tool
def relocate_guest_tool(hotel_name: str, manager_name: str, booking_id: str, room_numbers_by_date: dict) -> str:
    """MANAGE (WRITE): Strict relocation with explicit per-date rooms.

    - Provide room_numbers_by_date: {"YYYY-MM-DD": ["ROOM1205", "ROOM1206"]}
    - Proposed count per date must equal required slots for that date
    - Each proposed room must be available (or already assigned to this booking) and match booking room_type
    - Today is set to 'checked_in'; future dates set to 'assigned'
    """
    return intelligent_front_desk.relocate_guest_internal(hotel_name, manager_name, booking_id, room_numbers_by_date)

@tool
def read_room_assignments_for_booking_tool(hotel_name: str, booking_id: str) -> str:
    """
    Return all assigned room_numbers for a given booking_id in a structured calendar style.
    """
    conn = intelligent_front_desk.get_postgresql_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT stay_date, stay_index, room_number, stay_status
        FROM room_stay
        WHERE booking_id = %s AND hotel_name = %s
        ORDER BY stay_date, stay_index
    """, (booking_id, hotel_name))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    if not rows:
        return json.dumps({"error": f"No assignments found for booking {booking_id}"})
    calendar = {}
    for stay_date, stay_index, room_number, status in rows:
        date_str = str(stay_date)
        if date_str not in calendar:
            calendar[date_str] = []
        calendar[date_str].append({
            "stay_index": stay_index,
            "room_number": room_number or None,
            "stay_status": status
        })
    return json.dumps(calendar, indent=2)

# Simple test function
def test_assignment_system():
    """Test the assignment system directly"""
    
    print("🔍 TESTING ASSIGNMENT SYSTEM")
    print("=" * 30)
    
    try:
        conn = intelligent_front_desk.get_postgresql_connection()
        cursor = conn.cursor()
        
        # Count assignment records
        cursor.execute("SELECT COUNT(*) FROM room_number_assignments")
        total = cursor.fetchone()[0]
        print(f"📊 Total assignment records: {total}")
        
        # Count by hotel
        cursor.execute("""
            SELECT hotel_name, COUNT(*) 
            FROM room_number_assignments 
            GROUP BY hotel_name 
            ORDER BY COUNT(*) DESC 
            LIMIT 5
        """)
        
        hotels = cursor.fetchall()
        print(f"🏨 Top hotels:")
        for hotel, count in hotels:
            print(f"  • {hotel}: {count} records")
        
        cursor.close()
        conn.close()
        
        print("✅ Assignment system is working!")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_assignment_system()

# ============================================================================
# HYBRID ARCHITECTURE SUMMARY - FRONT DESK MANAGER
# ============================================================================
# 
# ARCHITECTURE DECISION: YES - Front Desk Manager benefits from self.llm
#
# WHY LLM INTEGRATION IS BENEFICIAL:
# 
# 1. NATURAL LANGUAGE ROOM ASSIGNMENTS
#    - "Put VIP guest in corner suite with city view, high floor, quiet area"
#    - "Family with children needs connecting rooms near elevator"
#    - "Elderly couple wants ground floor, accessible, close to restaurant"
#
# 2. MAINTENANCE ISSUE INTERPRETATION  
#    - "Room 205 AC too loud, bathroom faucet leaking, carpet stained"
#    - "Guest complained about TV not working and room too cold"
#    - Automatically determines severity, duration, departments needed
#
# 3. INTELLIGENT CONFLICT RESOLUTION
#    - Analyzes guest profiles for optimal room swaps
#    - Considers preferences, loyalty status, special needs
#    - Suggests best alternatives when conflicts occur
#
# HYBRID APPROACH IMPLEMENTATION:
# ✅ LLM-Enhanced Tools: read_room_assignment_suggestions, read_maintenance_issue_analysis
# ✅ Direct Database Tools: update_room_assignment, update_guest_check_in_status, etc. (preserved)
# ✅ Best of Both Worlds: Intelligent suggestions + Reliable execution
#
# ARCHITECTURAL BENEFITS:
# 🎯 Natural language interface for complex requests
# 🎯 Intelligent decision support for staff
# 🎯 Maintains precise database operations
# 🎯 Enhanced guest experience through better room matching
# 🎯 Streamlined maintenance issue processing
# 
# COMPARISON WITH OTHER MANAGERS:
# - policy_manager.py: LLM for natural language policy parsing ✅
# - room_manager.py: LLM for room information parsing ✅  
# - guest_manager.py: LLM for guest profile parsing ✅
# - front_desk_manager.py: LLM for assignment requests + maintenance ✅ (NEW)
# - housekeeping_manager.py: No LLM (simple CRUD operations) ❌
# - payment_manager.py: No LLM (transactional operations) ❌
# ============================================================================ 