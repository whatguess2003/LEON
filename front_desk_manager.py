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
import psycopg2
import json
from pymongo import MongoClient
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from langchain.tools import tool
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from .date_utils import normalize_date
from .llm_utils import canonical_key

load_dotenv()

# LLM-Enhanced Front Desk Manager Class
class IntelligentFrontDeskManager:
    """Hybrid Front Desk Manager with LLM for natural language processing and direct tools for operations"""
    
    def __init__(self):
        """Initialize with LLM for natural language processing"""
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
        # self.conn = self.get_postgresql_connection()  # Removed persistent connection
        self.hotel_context = self._get_hotel_context()
        self.hotel_name = self.hotel_context.get('hotel_name')
        self.mongo = MongoClient(os.getenv('MONGO_URL'))
        self.mongo_db = self.mongo['leon_hotel_memory']
    
    def get_postgresql_connection(self):
        """Get PostgreSQL connection for assignment operations"""
        return psycopg2.connect(os.getenv('DATABASE_URL'))

    def get_mongodb_connection(self):
        """Get MongoDB connection for guest profiles and content"""
        return self.mongo_db

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

    def _get_hotel_policy(self, hotel_name: str, policy_type: str = "prepared_threshold_days") -> int:
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
    
    def parse_room_assignment_request(self, request: str, booking_id: str, dialog_history: str = None) -> str:
        """Parse natural language room assignment request using LLM with dialog history context"""
        
        # Get hotel context from single source of truth
        hotel_context = self.hotel_context
        hotel_name = self.hotel_name
        
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
        
        # Get available rooms for context from existing tables
        conn = self.get_postgresql_connection()
        cursor = conn.cursor()
        
        # Get room types and availability from existing tables
        cursor.execute("""
            SELECT DISTINCT pra.room_number, pra.room_type, 
                   pra.assignment_status, pra.stay_date
            FROM room_number_assignments pra
            WHERE pra.hotel_name = %s 
            AND pra.stay_date = CURRENT_DATE
            ORDER BY pra.room_number
        """, (hotel_name,))
        
        rooms = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not rooms:
            return f"❌ No room data found for {hotel_name}"
        
        # Build room context for LLM
        room_context = "AVAILABLE ROOMS:\n"
        available_rooms = []
        for room_num, room_type, status, stay_date in rooms:
            status = status or 'available'
            room_context += f"- Room {room_num}: {room_type}, Status: {status}\n"
            if status == 'available':
                available_rooms.append({
                    'room_number': room_num,
                    'room_type': room_type
                })
        
        # Add dialog history context if available
        context_section = ""
        if dialog_history:
            context_section = f"""
RECENT CONVERSATION CONTEXT:
{dialog_history}

Use this context to resolve pronouns and references (e.g., "they", "them", "this guest", "that family").
"""
        
        parsing_prompt = f"""
        CONTEXT: Front desk room assignment for {hotel_name}
        BOOKING: {booking_id}
        {context_section}
        CURRENT REQUEST: "{request}"
        
        {room_context}
        
        TASK: Parse the natural language request and recommend the best room assignment.
        
        Consider:
        - Guest preferences (view, floor, quiet, size, bed type)
        - Special needs (accessibility, connecting rooms, away from elevator)
        - Room availability and characteristics
        - Hotel layout considerations
        
        Respond with:
        1. RECOMMENDED ROOM: [room_number]
        2. REASONING: Why this room matches the request
        3. ALTERNATIVES: 2-3 backup options
        4. SPECIAL NOTES: Any considerations or guest communication needed
        
        If request is unclear or impossible to fulfill, suggest clarification questions.
        """
        
        try:
            response = self.llm.invoke([{"role": "user", "content": parsing_prompt}])
            
            result = f"🤖 INTELLIGENT ROOM ASSIGNMENT ANALYSIS\n"
            result += f"📋 Booking: {booking_id}\n"
            result += f"🏨 Hotel: {hotel_name}\n"
            result += f"📝 Request: {request}\n\n"
            result += f"🎯 LLM RECOMMENDATION:\n{response.content}\n\n"
            result += f"💡 Next Step: Use update_room_assignment tool with recommended room"
            
            return result
            
        except Exception as e:
            return f"❌ Error parsing room assignment request: {e}"
    
    def interpret_maintenance_issue(self, issue_description: str, room_number: str, dialog_history: str = None) -> str:
        """Interpret maintenance issues and suggest appropriate actions using LLM with dialog history context"""
        
        # Get hotel context from single source of truth
        hotel_context = self.hotel_context
        hotel_name = self.hotel_name
        
        if not hotel_name:
            return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
        
        # Add dialog history context if available
        context_section = ""
        if dialog_history:
            context_section = f"""
RECENT CONVERSATION CONTEXT:
{dialog_history}

Use this context to resolve pronouns and references (e.g., "it", "this", "that room", "the problem").
"""
        
        interpretation_prompt = f"""
        CONTEXT: Hotel maintenance issue reporting
        HOTEL: {hotel_name}
        ROOM: {room_number}
        {context_section}
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
        
        Format as structured output for front desk action.
        """
        
        try:
            response = self.llm.invoke([{"role": "user", "content": interpretation_prompt}])
            
            result = f"🔧 MAINTENANCE ISSUE ANALYSIS\n"
            result += f"🏨 Hotel: {hotel_name}\n"
            result += f"🚪 Room: {room_number}\n"
            result += f"📝 Issue: {issue_description}\n\n"
            result += f"🤖 LLM ANALYSIS:\n{response.content}\n\n"
            result += f"💡 Suggested Tools:\n"
            result += f"- update_room_out_of_order_status (if room closure needed)\n"
            result += f"- update_room_readiness_status (for status changes)\n"
            result += f"- Contact housekeeping/maintenance teams"
            
            return result
            
        except Exception as e:
            return f"❌ Error interpreting maintenance issue: {e}"

    def update_room_assignment_auto(self, booking_id: str, room_number: str, stay_dates: list = None, staff_member: str = "front_desk") -> str:
        """
        Assign booking_id to room_number for the given stay_dates.
        If stay_dates is not provided, all booking dates will be used (auto).
        If guest already checked in, update readiness status for old and new rooms accordingly.
        """
        try:
            hotel_name = self.hotel_name
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
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
            guest_phone, booking_hotel, room_type = booking
            if canonical_key(hotel_name) != canonical_key(booking_hotel):
                cursor.close()
                conn.close()
                return f"❌ Hotel mismatch: Booking is for '{booking_hotel}', not '{hotel_name}'"
            # If stay_dates not provided, fetch from DB
            if stay_dates is None:
                cursor.execute("""
                    SELECT stay_date FROM booking_stays WHERE booking_id = %s ORDER BY stay_date
                """, (booking_id,))
                stay_dates = [row[0] for row in cursor.fetchall()]
                if not stay_dates:
                    cursor.close()
                    conn.close()
                    return f"❌ No stay_dates found for booking {booking_id}"
            # Find old room assignments for these stay_dates
            cursor.execute("""
                SELECT room_number, stay_date, check_in_time FROM room_number_assignments
                WHERE hotel_name = %s AND booking_id = %s AND stay_date = ANY(%s)
            """, (hotel_name, booking_id, stay_dates))
            old_assignments = cursor.fetchall()
            old_rooms = [row[0] for row in old_assignments if row[0] != room_number]
            guest_checked_in = any(row[2] is not None for row in old_assignments)
            # Insert or update assignment for each stay_date
            for stay_date in stay_dates:
                cursor.execute("""
                    INSERT INTO room_number_assignments (
                        hotel_name, room_type, room_number, stay_date, assignment_status, booking_id, guest_phone, assigned_at, assigned_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                    ON CONFLICT (hotel_name, room_number, stay_date)
                    DO UPDATE SET
                        assignment_status = EXCLUDED.assignment_status,
                        booking_id = EXCLUDED.booking_id,
                        guest_phone = EXCLUDED.guest_phone,
                        assigned_at = EXCLUDED.assigned_at,
                        assigned_by = EXCLUDED.assigned_by
                """,
                    (hotel_name, room_type, room_number, stay_date, 'assigned', booking_id, guest_phone, staff_member)
                )
            conn.commit()
            cursor.close()
            conn.close()
            # If guest already checked in, update readiness status for old and new rooms
            readiness_msgs = []
            if guest_checked_in:
                # Set old rooms to unprepared for each stay_date
                for old_room in old_rooms:
                    for stay_date in stay_dates:
                        msg = self.update_room_readiness_status_internal(
                            old_room, 'unprepared', staff_member,
                            notes=f"Relocation from {old_room} to {room_number} for booking {booking_id} on {stay_date}"
                        )
                        readiness_msgs.append(msg)
                # Set new room to occupied for each stay_date
                for stay_date in stay_dates:
                    msg = self.update_room_readiness_status_internal(
                        room_number, 'occupied', staff_member,
                        notes=f"Relocation to {room_number} for booking {booking_id} on {stay_date}"
                    )
                    readiness_msgs.append(msg)
            result_msg = f"✅ Room {room_number} assigned for stay_dates: {', '.join(str(d) for d in stay_dates)}"
            if readiness_msgs:
                result_msg += "\n" + "\n".join(readiness_msgs)
            return result_msg
        except Exception as e:
            return f"❌ Error assigning room: {str(e)}"

    def read_assignment_dashboard(self, date: str = None) -> str:
        """Internal method to read comprehensive assignment dashboard data for front desk operations"""
        try:
            hotel_name = self.hotel_name
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            # Parse date
            if date:
                try:
                    target_date = datetime.strptime(date, '%Y-%m-%d').date()
                except ValueError:
                    return f"❌ Invalid date format. Use YYYY-MM-DD"
            else:
                target_date = datetime.now().date()
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

    def update_room_out_of_order_status_internal(self, room_number: str, start_date: str, end_date: str, reason: str, staff_member: str = "front_desk") -> str:
        """
        Internal function to set room as out-of-order for a date range. Dates must be in YYYY-MM-DD format.
        """
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
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
                """, (self.hotel_name, room_number, date))
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
                """, (staff_member, f"Out-of-order: {reason}", self.hotel_name, room_number, date))
                updated_count += cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
            response = f"✅ Room Set Out-of-Order\n"
            response += f"🏨 Hotel: {self.hotel_name}\n"
            response += f"🚪 Room: {room_number}\n"
            response += f"📅 Dates: {start_date} to {end_date} ({len(dates)} days)\n"
            response += f"🔧 Reason: {reason}\n"
            response += f"👤 Updated by: {staff_member}\n"
            response += f"📊 Records updated: {updated_count}\n"
            response += f"⚠️ Room unavailable for bookings during this period"
            return response
        except Exception as e:
            return f"❌ Error setting room out-of-order: {str(e)}"

    def update_room_available_status_internal(self, room_number: str, start_date: str = None, end_date: str = None, staff_member: str = "front_desk") -> str:
        """
        Internal function to restore room to available for a date range. Dates must be in YYYY-MM-DD format. If start_date or end_date is None, will find all future out-of-order dates and use their range.
        """
        from datetime import datetime
        # If start_date or end_date is None, find all future out-of-order dates for this room
        if not start_date or not end_date:
            today = datetime.now().date()
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT stay_date FROM room_number_assignments
                WHERE hotel_name = %s AND room_number = %s
                AND assignment_status = 'out-of-order'
                AND stay_date >= %s
                ORDER BY stay_date
            """, (self.hotel_name, room_number, today))
            dates = [row[0] for row in cursor.fetchall()]
            cursor.close()
            conn.close()
            if not dates:
                return f"❌ No out-of-order dates found for room {room_number}. Please specify a date range."
            start_date = min(dates).strftime('%Y-%m-%d')
            end_date = max(dates).strftime('%Y-%m-%d')
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
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
            # Restore room to available for all dates
            updated_count = 0
            for date in dates:
                cursor.execute("""
                    UPDATE room_number_assignments 
                    SET assignment_status = 'available',
                        booking_id = NULL,
                        guest_phone = NULL,
                        assigned_at = CURRENT_TIMESTAMP,
                        assigned_by = %s,
                        assignment_notes = %s
                    WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
                    AND assignment_status = 'out-of-order'
                """, (staff_member, f"Restored from out-of-order by {staff_member}", self.hotel_name, room_number, date))
                updated_count += cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
            response = f"✅ Room Restored from Out-of-Order\n"
            response += f"🏨 Hotel: {self.hotel_name}\n"
            response += f"🚪 Room: {room_number}\n"
            response += f"📅 Dates: {start_date} to {end_date} ({len(dates)} days)\n"
            response += f"👤 Restored by: {staff_member}\n"
            response += f"📊 Records updated: {updated_count}\n"
            response += f"✅ Room now available for bookings"
            return response
        except Exception as e:
            return f"❌ Error restoring room: {str(e)}"

    def update_room_readiness_status_internal(self, room_number: str, new_status: str, staff_member: str = "front_desk", notes: str = "") -> str:
        """
        Internal function to update room readiness status (prepared, unprepared, preparing, occupied).
        """
        valid_statuses = ['prepared', 'unprepared', 'preparing', 'occupied']
        if new_status not in valid_statuses:
            return f"❌ Invalid status. Must be one of: {', '.join(valid_statuses)}"
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT readiness_status, prepared_since 
                FROM room_readiness_status 
                WHERE hotel_name = %s AND room_number = %s
            """, (self.hotel_name, room_number))
            existing = cursor.fetchone()
            if not existing:
                cursor.close()
                conn.close()
                return f"❌ No readiness status record found for room {room_number} at {self.hotel_name}"
            current_status, current_prepared_since = existing
            update_fields = {
                'readiness_status': new_status,
                'status_changed_at': 'CURRENT_TIMESTAMP',
                'changed_by': staff_member,
                'notes': notes or f"Status changed to {new_status} by {staff_member}"
            }
            if new_status == 'prepared' and current_status != 'prepared':
                update_fields['prepared_since'] = 'CURRENT_TIMESTAMP'
            elif new_status != 'prepared':
                update_fields['prepared_since'] = None
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
            values.extend([self.hotel_name, room_number])
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
            response += f"🏨 Hotel: {self.hotel_name}\n"
            response += f"🚪 Room: {room_number}\n"
            response += f"📊 Status: {current_status} → {new_status}\n"
            response += f"👤 Updated by: {staff_member}\n"
            if new_status == 'prepared':
                response += f"⏰ Prepared timestamp set\n"
                response += f"🔄 Auto-unprepare: After 3 days (configurable)"
            elif current_status == 'prepared':
                response += f"⏰ Prepared timestamp cleared"
            if notes:
                response += f"\n📝 Notes: {notes}"
            return response
        except Exception as e:
            return f"❌ Error updating readiness status: {str(e)}"

    def read_rooms_exceeding_prepared_threshold_internal(self, threshold_days: int = None) -> str:
        """
        Internal function to read rooms that have been prepared longer than threshold and need re-preparation.
        """
        try:
            # Get threshold from hotel policy if not specified
            if threshold_days is None:
                threshold_days = self._get_hotel_policy(self.hotel_name, "prepared_threshold_days")
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            # Find rooms prepared longer than threshold
            cursor.execute("""
                SELECT room_number, readiness_status, prepared_since, 
                       EXTRACT(DAYS FROM (CURRENT_TIMESTAMP - prepared_since)) as days_prepared,
                       auto_unprepare_threshold_days
                FROM room_readiness_status 
                WHERE hotel_name = %s 
                AND readiness_status = 'prepared'
                AND prepared_since IS NOT NULL
                AND EXTRACT(DAYS FROM (CURRENT_TIMESTAMP - prepared_since)) >= %s
                ORDER BY prepared_since ASC
            """, (self.hotel_name, threshold_days))
            exceeding_rooms = cursor.fetchall()
            cursor.close()
            conn.close()
            if not exceeding_rooms:
                return f"✅ No rooms at {self.hotel_name} exceed the {threshold_days}-day prepared threshold"
            response = f"⚠️ Rooms Exceeding Prepared Threshold - {self.hotel_name}\n"
            response += f"🕐 Threshold: {threshold_days} days (from hotel policy)\n\n"
            response += "🚪 ROOMS NEEDING RE-PREPARATION:\n"
            for room_number, status, prepared_since, days_prepared, room_threshold in exceeding_rooms:
                response += f"  ❌ Room {room_number}\n"
                response += f"      📅 Prepared since: {prepared_since.strftime('%Y-%m-%d %H:%M')}\n"
                response += f"      ⏰ Days prepared: {int(days_prepared)} days\n"
                response += f"      🎯 Room threshold: {room_threshold} days\n\n"
            response += f"💡 Recommendation: Set these rooms to 'unprepared' status\n"
            response += f"🔧 Use update_room_readiness_status tool to change status"
            return response
        except Exception as e:
            return f"❌ Error checking prepared threshold: {str(e)}"

    def update_guest_check_in_status_internal(self, booking_id: str, room_number: str = None, staff_member: str = "front_desk", notes: str = "") -> str:
        """
        Internal function to update guest check-in status and room readiness from prepared to occupied.
        If room_number is None, will look up the assigned room for the booking_id and today.
        If the room is not assigned to the booking for today, assign it first.
        """
        try:
            from datetime import datetime
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            today = datetime.now().date()
            # If room_number is None, look up the assigned room(s) for this booking and today
            if room_number is None:
                cursor.execute("""
                    SELECT room_number FROM room_number_assignments
                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                """, (self.hotel_name, booking_id, today))
                assigned_rooms = [row[0] for row in cursor.fetchall()]
                if not assigned_rooms:
                    cursor.close()
                    conn.close()
                    return f"❌ No room assigned to booking {booking_id} for today at {self.hotel_name}"
                if len(assigned_rooms) > 1:
                    cursor.close()
                    conn.close()
                    return f"❌ Multiple rooms assigned to booking {booking_id} for today: {', '.join(assigned_rooms)}. Please specify room_number."
                room_number = assigned_rooms[0]
            # Check if the room is assigned to the booking for today
            cursor.execute("""
                SELECT assignment_status, booking_id, guest_phone
                FROM room_number_assignments 
                WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
            """, (self.hotel_name, room_number, today))
            assignment = cursor.fetchone()
            # If not assigned, assign the room for today
            if not assignment or assignment[1] != booking_id:
                cursor.close()
                conn.close()
                assign_result = self.update_room_assignment_auto(booking_id, room_number, stay_dates=[today], staff_member=staff_member)
                # Reopen connection and cursor for check-in
                conn = self.get_postgresql_connection()
                cursor = conn.cursor()
                # Re-fetch assignment after assignment
                cursor.execute("""
                    SELECT assignment_status, booking_id, guest_phone
                    FROM room_number_assignments 
                    WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
                """, (self.hotel_name, room_number, today))
                assignment = cursor.fetchone()
                if not assignment or assignment[1] != booking_id:
                    cursor.close()
                    conn.close()
                    return f"❌ Failed to assign room {room_number} to booking {booking_id} for today."
            assignment_status, assigned_booking, assigned_guest_phone = assignment
            if assigned_booking != booking_id:
                mongodb = self.mongo_db
                guests_collection = mongodb['guest_profiles']
                assigned_guest = guests_collection.find_one({"guest_phone": assigned_guest_phone})
                assigned_guest_name = assigned_guest.get('guest_name', 'Unknown') if assigned_guest else 'Unknown'
                requesting_guest_phone = booking_id.split('booking')[0] if 'booking' in booking_id else 'Unknown'
                requesting_guest = guests_collection.find_one({"guest_phone": requesting_guest_phone})
                requesting_guest_name = requesting_guest.get('guest_name', 'Unknown') if requesting_guest else 'Unknown'
                cursor.execute("""
                    SELECT room_number, assignment_status 
                    FROM room_number_assignments 
                    WHERE hotel_name = %s AND stay_date = %s AND booking_id = %s
                """, (self.hotel_name, today, booking_id))
                correct_assignment = cursor.fetchone()
                cursor.execute("""
                    SELECT room_number 
                    FROM room_number_assignments 
                    WHERE hotel_name = %s AND stay_date = %s AND assignment_status = 'available'
                    LIMIT 3
                """, (self.hotel_name, today))
                available_rooms = cursor.fetchall()
                cursor.close()
                conn.close()
                response = f"❌ ASSIGNMENT MISMATCH - Room {room_number}\n\n"
                response += f"🚪 Room {room_number} is currently assigned to:\n"
                response += f"  📋 Booking: {assigned_booking}\n"
                response += f"  👤 Guest: {assigned_guest_name} ({assigned_guest_phone})\n\n"
                response += f"🔍 You are trying to check in:\n"
                response += f"  📋 Booking: {booking_id}\n"
                response += f"  👤 Guest: {requesting_guest_name} ({requesting_guest_phone})\n\n"
                if correct_assignment:
                    correct_room, correct_status = correct_assignment
                    response += f"✅ SOLUTION FOUND:\n"
                    response += f"  🏨 {requesting_guest_name} is assigned to room {correct_room} (status: {correct_status})\n"
                    response += f"  💡 Use room {correct_room} for check-in instead.\n\n"
                response += f"🔧 MANAGEMENT OPTIONS:\n"
                response += f"  1. Check in {requesting_guest_name} to their assigned room\n"
                response += f"  2. Reassign {assigned_guest_name} to another room\n"
                response += f"  3. Reassign {requesting_guest_name} to another room\n\n"
                if available_rooms:
                    response += f"🏨 Available rooms for reassignment: {', '.join([room[0] for room in available_rooms])}\n"
                response += f"📞 Please clarify: Do you wish to assign {assigned_guest_name} to another room, or {requesting_guest_name} to another room?"
                return response
            if assignment_status != 'assigned':
                cursor.close()
                conn.close()
                return f"❌ Room {room_number} status is '{assignment_status}', cannot check-in. Room must be 'assigned' status."
            cursor.execute("""
                SELECT readiness_status 
                FROM room_readiness_status 
                WHERE hotel_name = %s AND room_number = %s
            """, (self.hotel_name, room_number))
            readiness = cursor.fetchone()
            if not readiness:
                cursor.close()
                conn.close()
                return f"❌ No readiness status record found for room {room_number}"
            current_readiness = readiness[0]
            if current_readiness not in ['prepared']:
                # Auto-prepare the room if not prepared
                prep_msg = self.update_room_readiness_status_internal(room_number, 'prepared', staff_member, notes=f'Auto-prepared for check-in to booking {booking_id}')
                # Re-check readiness
                cursor.execute("""
                    SELECT readiness_status 
                    FROM room_readiness_status 
                    WHERE hotel_name = %s AND room_number = %s
                """, (self.hotel_name, room_number))
                readiness = cursor.fetchone()
                if not readiness or readiness[0] != 'prepared':
                    cursor.close()
                    conn.close()
                    return f"❌ Room {room_number} readiness status is still not 'prepared' after auto-prepare. {prep_msg}"
                # Optionally, include prep_msg in the response
            # Now proceed with check-in
            cursor.execute("""
                UPDATE room_number_assignments 
                SET check_in_time = CURRENT_TIMESTAMP,
                    assignment_notes = %s
                WHERE hotel_name = %s AND room_number = %s AND stay_date = %s AND booking_id = %s
            """, (f"Checked in by {staff_member}. {notes}".strip(), 
                  self.hotel_name, room_number, today, booking_id))
            cursor.execute("""
                UPDATE room_readiness_status 
                SET readiness_status = 'occupied',
                    status_changed_at = CURRENT_TIMESTAMP,
                    changed_by = %s,
                    notes = %s,
                    prepared_since = NULL
                WHERE hotel_name = %s AND room_number = %s
            """, (staff_member, f"Check-in: {booking_id}. {notes}".strip(), self.hotel_name, room_number))
            conn.commit()
            cursor.close()
            conn.close()
            response = f"✅ Guest Check-In Successful\n"
            response += f"🏨 Hotel: {self.hotel_name}\n"
            response += f"🚪 Room: {room_number}\n"
            response += f"📋 Booking: {booking_id}\n"
            response += f"📞 Guest: {assigned_guest_phone}\n"
            response += f"👤 Checked in by: {staff_member}\n"
            response += f"⏰ Check-in time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            response += f"📊 Readiness: prepared → occupied\n"
            if notes:
                response += f"📝 Notes: {notes}"
            return response
        except Exception as e:
            return f"❌ Error processing check-in: {str(e)}"

    def update_guest_check_out_status_internal(self, booking_id: str, room_number: str = None, staff_member: str = "front_desk", notes: str = "") -> str:
        """
        Internal function to update guest check-out status and room readiness from occupied to unprepared.
        If room_number is None, will look up the assigned room for the booking_id and today.
        """
        try:
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            today = datetime.now().date()
            # If room_number is None, look up the assigned room(s) for this booking and today
            if room_number is None:
                cursor.execute("""
                    SELECT room_number FROM room_number_assignments
                    WHERE hotel_name = %s AND booking_id = %s AND stay_date = %s
                """, (self.hotel_name, booking_id, today))
                assigned_rooms = [row[0] for row in cursor.fetchall()]
                if not assigned_rooms:
                    cursor.close()
                    conn.close()
                    return f"❌ No room assigned to booking {booking_id} for today at {self.hotel_name}"
                if len(assigned_rooms) > 1:
                    cursor.close()
                    conn.close()
                    return f"❌ Multiple rooms assigned to booking {booking_id} for today: {', '.join(assigned_rooms)}. Please specify room_number."
                room_number = assigned_rooms[0]
            cursor.execute("""
                SELECT guest_phone, check_in_time
                FROM room_number_assignments 
                WHERE hotel_name = %s AND room_number = %s AND stay_date = %s AND booking_id = %s
            """, (self.hotel_name, room_number, today, booking_id))
            result = cursor.fetchone()
            if not result:
                cursor.close()
                conn.close()
                return f"❌ No assignment found for room {room_number} and booking {booking_id} on {today}"
            guest_phone, check_in_time = result
            cursor.execute("""
                SELECT readiness_status 
                FROM room_readiness_status 
                WHERE hotel_name = %s AND room_number = %s
            """, (self.hotel_name, room_number))
            readiness = cursor.fetchone()
            if not readiness:
                cursor.close()
                conn.close()
                return f"❌ No readiness status record found for room {room_number}"
            current_readiness = readiness[0]
            if current_readiness != 'occupied':
                cursor.close()
                conn.close()
                return f"❌ Room {room_number} readiness status is '{current_readiness}'. Room must be 'occupied' for check-out"
            cursor.execute("""
                UPDATE room_number_assignments 
                SET check_out_time = CURRENT_TIMESTAMP,
                    assignment_notes = assignment_notes || %s
                WHERE hotel_name = %s AND room_number = %s AND stay_date = %s AND booking_id = %s
            """, (f" | Checked out by {staff_member}. {notes}".strip(), 
                  self.hotel_name, room_number, today, booking_id))
            cursor.execute("""
                UPDATE room_readiness_status 
                SET readiness_status = 'unprepared',
                    status_changed_at = CURRENT_TIMESTAMP,
                    changed_by = %s,
                    notes = %s,
                    prepared_since = NULL
                WHERE hotel_name = %s AND room_number = %s
            """, (staff_member, f"Check-out: {booking_id}. Room needs cleaning. {notes}".strip(), self.hotel_name, room_number))
            conn.commit()
            cursor.close()
            conn.close()
            response = f"✅ Guest Check-Out Successful\n"
            response += f"🏨 Hotel: {self.hotel_name}\n"
            response += f"🚪 Room: {room_number}\n"
            response += f"📋 Booking: {booking_id}\n"
            response += f"📞 Guest: {guest_phone}\n"
            response += f"👤 Checked out by: {staff_member}\n"
            response += f"⏰ Check-out time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            response += f"🏠 Stay duration: {check_in_time.strftime('%Y-%m-%d %H:%M')} to now\n"
            response += f"📊 Readiness: occupied → unprepared (needs cleaning)\n"
            if notes:
                response += f"📝 Notes: {notes}"
            return response
        except Exception as e:
            return f"❌ Error processing check-out: {str(e)}"

    def update_rooms_by_threshold_auto_internal(self, staff_member: str = "system") -> str:
        """
        Internal function to update rooms that exceed their prepared threshold (bulk operation).
        """
        try:
            hotel_context = self.hotel_context
            hotel_name = hotel_context.get('hotel_name')
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT room_number, EXTRACT(DAYS FROM (CURRENT_TIMESTAMP - prepared_since)) as days_prepared,
                       auto_unprepare_threshold_days
                FROM room_readiness_status 
                WHERE hotel_name = %s 
                AND readiness_status = 'prepared'
                AND prepared_since IS NOT NULL
                AND EXTRACT(DAYS FROM (CURRENT_TIMESTAMP - prepared_since)) >= auto_unprepare_threshold_days
            """, (hotel_name,))
            rooms_to_unprepare = cursor.fetchall()
            if not rooms_to_unprepare:
                cursor.close()
                conn.close()
                return f"✅ No rooms at {hotel_name} need auto-unpreparing"
            unprepared_count = 0
            unprepared_rooms = []
            for room_number, days_prepared, threshold in rooms_to_unprepare:
                cursor.execute("""
                    UPDATE room_readiness_status 
                    SET readiness_status = 'unprepared',
                        status_changed_at = CURRENT_TIMESTAMP,
                        changed_by = %s,
                        notes = %s,
                        prepared_since = NULL
                    WHERE hotel_name = %s AND room_number = %s
                """, (staff_member, f"Auto-unprepared after {int(days_prepared)} days (threshold: {threshold})", hotel_name, room_number))
                if cursor.rowcount > 0:
                    unprepared_count += 1
                    unprepared_rooms.append(f"Room {room_number} (prepared {int(days_prepared)} days, threshold {threshold})")
            conn.commit()
            cursor.close()
            conn.close()
            response = f"🔄 Auto-Unprepare Results - {hotel_name}\n"
            response += f"📊 Rooms processed: {unprepared_count}/{len(rooms_to_unprepare)}\n\n"
            if unprepared_rooms:
                response += "✅ ROOMS AUTO-UNPREPARED:\n"
                for room_info in unprepared_rooms:
                    response += f"  • {room_info}\n"
                response += f"\n👤 Changed by: {staff_member}\n"
                response += f"📝 Reason: Exceeded prepared threshold (from hotel policy)"
            return response
        except Exception as e:
            return f"❌ Error in auto-unprepare operation: {str(e)}"

    def read_room_assignment(self, booking_id: str = None, date_range: str = "all", room_number: str = None) -> str:
        """
        Read assigned room numbers for a given booking_id (optionally for a date or date range, and/or a specific room_number).
        If booking_id is provided and room_number is None, automatically fetch all room_numbers assigned to that booking.
        If both booking_id and room_number are None, return all assignments for the hotel/date.
        """
        try:
            hotel_name = self.hotel_name
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            # Parse date range
            if date_range.lower() == "today":
                start_date = end_date = datetime.now().date()
            elif date_range.lower() == "all":
                start_date = None
                end_date = None
            else:
                try:
                    start_date = end_date = datetime.strptime(date_range, '%Y-%m-%d').date()
                except Exception:
                    start_date = end_date = datetime.now().date()
            # If booking_id is provided and room_number is None, fetch all room_numbers for that booking
            if booking_id and room_number is None:
                cursor.execute("""
                    SELECT DISTINCT room_number FROM room_number_assignments
                    WHERE hotel_name = %s AND booking_id = %s
                """, (hotel_name, booking_id))
                room_numbers = [row[0] for row in cursor.fetchall()]
                if not room_numbers:
                    cursor.close()
                    conn.close()
                    return f"❌ No room assignments found for booking {booking_id} at {hotel_name}"
            else:
                room_numbers = [room_number] if room_number else []
            # Build main query
            query = """
                SELECT room_number, stay_date, assignment_status, booking_id
                FROM room_number_assignments
                WHERE hotel_name = %s
            """
            params = [hotel_name]
            if booking_id:
                query += " AND booking_id = %s"
                params.append(booking_id)
            if room_numbers:
                query += " AND room_number = ANY(%s)"
                params.append(room_numbers)
            if start_date and end_date:
                query += " AND stay_date BETWEEN %s AND %s"
                params.extend([start_date, end_date])
            query += " ORDER BY stay_date"
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            if not rows:
                return f"❌ No room assignments found for the given criteria at {hotel_name}"
            response = f"🏨 Room Assignment Results ({hotel_name})\n"
            for room_number, stay_date, status, booking_id_row in rows:
                line = f"📅 {stay_date}: Room {room_number} ({status})"
                if booking_id_row:
                    line += f" | Booking: {booking_id_row}"
                response += line + "\n"
            return response.strip()
        except Exception as e:
            return f"❌ Error reading room assignment: {str(e)}"

    def read_room_readiness_status(self, room_number:str) -> str:
        """READ readiness status for rooms"""
        try:
            hotel_name = self.hotel_name
            if hotel_name.startswith("❌"):
                return hotel_name
            conn = self.get_postgresql_connection()
            cursor = conn.cursor()
            # Build query
            if room_number:
                query = """
                    SELECT room_number, readiness_status, status_changed_at, changed_by, 
                           notes, prepared_since, auto_unprepare_threshold_days
                    FROM room_readiness_status 
                    WHERE hotel_name = %s AND room_number = %s
                    ORDER BY room_number
                """
                cursor.execute(query, (hotel_name, room_number))
            else:
                query = """
                    SELECT room_number, readiness_status, status_changed_at, changed_by, 
                           notes, prepared_since, auto_unprepare_threshold_days
                    FROM room_readiness_status 
                    WHERE hotel_name = %s
                    ORDER BY room_number
                """
                cursor.execute(query, (hotel_name,))
            readiness_records = cursor.fetchall()
            cursor.close()
            conn.close()
            if not readiness_records:
                room_desc = f" for room_number {room_number}" if room_number else ""
                return f"❌ No readiness status records found{room_desc} at {hotel_name}"
            response = f"🏨 Room Readiness Status - {hotel_name}\n"
            if room_number:
                response += f"🚪 Room Number: {room_number}\n"
            response += "\n"
            # Status summary if showing all room_numbers
            if not room_number:
                status_counts = {}
                for record in readiness_records:
                    status = record[1]
                    status_counts[status] = status_counts.get(status, 0) + 1
                response += "📊 READINESS SUMMARY\n"
                total_room_numbers = len(readiness_records)
                for status, count in status_counts.items():
                    percentage = (count / total_room_numbers) * 100
                    status_icon = {
                        'prepared': '✅',
                        'unprepared': '❌',
                        'preparing': '🔧',
                        'occupied': '🔴',
                        
                    }.get(status, '❓')
                    response += f"  {status_icon} {status.upper()}: {count} room_numbers ({percentage:.1f}%)\n"
                response += f"\n🎯 TOTAL ROOM NUMBERS: {total_room_numbers}\n\n"
            # Detailed readiness status
            response += "🚪 ROOM NUMBER READINESS DETAILS\n"
            for record in readiness_records:
                room_num, status, changed_at, changed_by, notes, prepared_since, threshold = record
                status_icon = {
                    'prepared': '✅',
                    'unprepared': '❌',
                    'preparing': '🔧',
                    'occupied': '🔴'
                }.get(status, '❓')
                response += f"  {status_icon} Room Number {room_num} - {status.upper()}"
                if changed_by:
                    response += f" | By: {changed_by}"
                if changed_at:
                    response += f" | Changed: {changed_at.strftime('%Y-%m-%d %H:%M')}"
                # Show prepared duration
                if status == 'prepared' and prepared_since:
                    from datetime import datetime
                    days_prepared = (datetime.now() - prepared_since).days
                    response += f" | Prepared for: {days_prepared} days"
                    if days_prepared >= threshold:
                        response += f" ⚠️ (Exceeds {threshold}-day threshold)"
                if notes:
                    response += f" | Notes: {notes}"
                response += "\n"
            return response
        except Exception as e:
            return f"❌ Error reading readiness status: {str(e)}"

# Create global instance for LLM-enhanced operations
intelligent_front_desk = IntelligentFrontDeskManager()

@tool
def update_room_assignment_auto(booking_id: str, room_number: str, stay_dates: list = None, staff_member: str = "front_desk") -> str:
    """
    Assign booking_id to room_number for the given stay_dates.
    If stay_dates is not provided, all booking dates will be used (auto).
    If guest already checked in, update readiness status for old and new rooms accordingly.
    """
    return intelligent_front_desk.update_room_assignment_auto(booking_id, room_number, stay_dates, staff_member)

@tool
def read_assignment_dashboard(date: str = None) -> str:
    """READ comprehensive assignment dashboard data for front desk operations"""
    return intelligent_front_desk.read_assignment_dashboard(date)

@tool
def update_room_out_of_order_status(room_number: str, start_date: str, end_date: str, 
                         reason: str, staff_member: str = "front_desk") -> str:
    """
    Set room as out-of-order for a date range. Dates must be in YYYY-MM-DD format.
    """
    return intelligent_front_desk.update_room_out_of_order_status_internal(room_number, start_date, end_date, reason, staff_member)

@tool
def update_room_available_status(room_number: str, start_date: str = None, end_date: str = None,
                                  staff_member: str = "front_desk") -> str:
    """
    USE THIS TOOL TO MAKE A ROOM AVAILABLE AFTER MAINTENANCE OR OUT-OF-ORDER PERIOD.
    
    Examples:
    - 'Room 107 bathroom fixed'
    - 'Room 105 is available after repair'
    - 'Room 106 maintenance complete'
    - 'Room 108 is available for guests after being out-of-order'
    
    If start_date or end_date is not provided, the tool will automatically find all future out-of-order dates for the room and restore availability for that period.
    """
    return intelligent_front_desk.update_room_available_status_internal(room_number, start_date, end_date, staff_member)

@tool
def update_room_readiness_status(room_number: str, new_status: str, 
                                staff_member: str = "front_desk", notes: str = "") -> str:
    """
    DO NOT USE FOR MAINTENANCE OR OUT-OF-ORDER ROOMS. USE update_room_available_status INSTEAD.
    
    This tool is ONLY for cleaning/preparation status (prepared, unprepared, preparing, occupied).
    
    Examples:
    - Use for: 'Room 101 is clean', 'Room 102 needs cleaning', 'Room 103 is occupied'
    - DO NOT use for: 'Room 104 is fixed', 'Room 105 is available after repair', 'Room 106 maintenance complete'
    
    Args:
        hotel_name: Hotel name
        room_number: Room number
        new_status: New readiness status (prepared, unprepared, preparing, occupied)
        staff_member: Staff member making the change
        notes: Optional notes about the status change
    
    Returns:
        Status message
    
    Note:
        - prepared: Room is clean and ready for guests
        - unprepared: Room needs cleaning
        - preparing: Staff is currently working in room (cleaning)
        - occupied: Guest is currently in the room
    """
    return intelligent_front_desk.update_room_readiness_status_internal(room_number, new_status, staff_member, notes)

@tool
def read_rooms_exceeding_prepared_threshold(threshold_days: int = None) -> str:
    """
    READ rooms that have been prepared longer than threshold and need re-preparation
    """
    return intelligent_front_desk.read_rooms_exceeding_prepared_threshold_internal(threshold_days)

@tool
def update_guest_check_in_status(booking_id: str, room_number: str = None, staff_member: str = "front_desk", notes: str = "") -> str:
    """Update guest check-in status and room readiness. If room_number is None, will look up the assigned room for the booking_id and today."""
    return intelligent_front_desk.update_guest_check_in_status_internal(booking_id, room_number, staff_member, notes)

@tool
def update_guest_check_out_status(booking_id: str, room_number: str = None, staff_member: str = "front_desk", notes: str = "") -> str:
    """Update guest check-out status and room readiness. If room_number is None, will look up the assigned room for the booking_id and today."""
    return intelligent_front_desk.update_guest_check_out_status_internal(booking_id, room_number, staff_member, notes)

@tool
def update_rooms_by_threshold_auto(staff_member: str = "system") -> str:
    """UPDATE rooms that exceed their prepared threshold (bulk operation)
    
    Args:
        staff_member: Staff member performing the auto-unprepare (default: system)
        
    Returns:
        Results of auto-unprepare operation
    """
    return intelligent_front_desk.update_rooms_by_threshold_auto_internal(staff_member)

# NEW LLM-Enhanced Tools
@tool
def read_room_assignment_suggestions(request: str, booking_id: str, dialog_history: str = None) -> str:
    """READ room assignment suggestions based on natural language request
    
    Examples:
    - "Put the Johnson family in a corner room with city view, high floor, away from elevator"
    - "VIP guest needs connecting rooms, quiet area, near spa"
    - "Elderly couple wants ground floor, accessible, close to restaurant"
    """
    return intelligent_front_desk.parse_room_assignment_request(request, booking_id, dialog_history)

@tool  
def read_maintenance_issue_analysis(issue_description: str, room_number: str, dialog_history: str = None) -> str:
    """READ maintenance issue analysis and provide structured recommendations
    
    Examples:
    - "Room 205 AC too loud, bathroom faucet leaking, carpet stained"
    - "Guest complained about TV not working and room too cold"
    - Automatically determines severity, duration, departments needed
    """
    return intelligent_front_desk.interpret_maintenance_issue(issue_description, room_number, dialog_history)

@tool
def read_room_assignment_tool(booking_id: str = None, date_range: str = "all", room_number: str = None) -> str:
    """Read assigned room numbers for a given booking_id (optionally for a date or date range, and/or a specific room_number). If booking_id is provided and room_number is None, all assigned room_numbers for that booking are returned."""
    return intelligent_front_desk.read_room_assignment(booking_id, date_range, room_number)

@tool
def read_room_readiness_status_tool(room_number: str) -> str:
    """Read readiness status for a room_number"""
    return intelligent_front_desk.read_room_readiness_status(room_number=room_number)

@tool
def relocate_guest_tool(booking_id: str, new_room_number: str, staff_member: str = "front_desk") -> str:
    """
    Relocate a checked-in guest to a new room for all current and future stay_dates.
    - Updates room assignment to new room for all current/future stay_dates.
    - Sets old room(s) readiness to 'unprepared'.
    - Sets new room readiness to 'occupied' if guest is checked in.
    - Logs the relocation.
    - Returns a summary.
    """
    hotel_name = intelligent_front_desk.hotel_name
    if not hotel_name:
        return "❌ No hotel context found. Please ensure manager_context.json is properly configured."
    conn = intelligent_front_desk.get_postgresql_connection()
    cursor = conn.cursor()
    try:
        # Validate booking exists and get stay_dates
        cursor.execute("""
            SELECT guest_phone, hotel_name, room_type FROM room_bookings WHERE booking_id = %s
        """, (booking_id,))
        booking = cursor.fetchone()
        if not booking:
            cursor.close()
            conn.close()
            return f"❌ Booking {booking_id} not found in room_bookings table"
        guest_phone, booking_hotel, room_type = booking
        if canonical_key(hotel_name) != canonical_key(booking_hotel):
            cursor.close()
            conn.close()
            return f"❌ Hotel mismatch: Booking is for '{booking_hotel}', not '{hotel_name}'"
        # Always fetch all current and future stay_dates
        cursor.execute("""
            SELECT stay_date FROM booking_stays WHERE booking_id = %s AND stay_date >= CURRENT_DATE ORDER BY stay_date
        """, (booking_id,))
        stay_dates = [row[0] for row in cursor.fetchall()]
        if not stay_dates:
            cursor.close()
            conn.close()
            return f"❌ No stay_dates found for booking {booking_id}"
        # Find old room assignments for these stay_dates
        cursor.execute("""
            SELECT room_number, stay_date, check_in_time FROM room_number_assignments
            WHERE hotel_name = %s AND booking_id = %s AND stay_date = ANY(%s)
        """, (hotel_name, booking_id, stay_dates))
        old_assignments = cursor.fetchall()
        old_rooms = [row[0] for row in old_assignments if row[0] != new_room_number]
        guest_checked_in = any(row[2] is not None for row in old_assignments)
        # Insert or update assignment for each stay_date
        for stay_date in stay_dates:
            cursor.execute("""
                INSERT INTO room_number_assignments (
                    hotel_name, room_type, room_number, stay_date, assignment_status, booking_id, guest_phone, assigned_at, assigned_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                ON CONFLICT (hotel_name, room_number, stay_date)
                DO UPDATE SET
                    assignment_status = EXCLUDED.assignment_status,
                    booking_id = EXCLUDED.booking_id,
                    guest_phone = EXCLUDED.guest_phone,
                    assigned_at = EXCLUDED.assigned_at,
                    assigned_by = EXCLUDED.assigned_by
            """,
                (hotel_name, room_type, new_room_number, stay_date, 'assigned', booking_id, guest_phone, staff_member)
            )
        # Set old rooms to unprepared for each stay_date
        readiness_msgs = []
        for old_room in old_rooms:
            for stay_date in stay_dates:
                msg = intelligent_front_desk.update_room_readiness_status_internal(
                    old_room, 'unprepared', staff_member,
                    notes=f"Relocation from {old_room} to {new_room_number} for booking {booking_id} on {stay_date}"
                )
                readiness_msgs.append(msg)
        # Set new room to occupied for each stay_date if checked in
        if guest_checked_in:
            for stay_date in stay_dates:
                msg = intelligent_front_desk.update_room_readiness_status_internal(
                    new_room_number, 'occupied', staff_member,
                    notes=f"Relocation to {new_room_number} for booking {booking_id} on {stay_date}"
                )
                readiness_msgs.append(msg)
        # Log the relocation (optional: could add to a dedicated log table)
        # Commit transaction
        conn.commit()
        cursor.close()
        conn.close()
        result_msg = f"✅ Guest relocated to room {new_room_number} for stay_dates: {', '.join(str(d) for d in stay_dates)}"
        if readiness_msgs:
            result_msg += "\n" + "\n".join(readiness_msgs)
        return result_msg
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        return f"❌ Error relocating guest: {str(e)}"

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