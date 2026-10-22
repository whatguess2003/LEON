#!/usr/bin/env python3
"""
Guest Engagement Manager - Database Operations Only
Handles database operations for pre-arrival guest engagement
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import psycopg2
from psycopg2.extras import RealDictCursor
import pymongo
from pymongo import MongoClient
import redis
from dotenv import load_dotenv

load_dotenv()

# Database connections
REDIS_URL = os.getenv('REDIS_URL')
MONGO_URL = os.getenv('MONGO_URL')
DATABASE_URL = os.getenv('DATABASE_URL')

class GuestEngagementManager:
    """Manages guest engagement database operations"""
    
    def __init__(self):
        self.redis_client = redis.from_url(REDIS_URL) if REDIS_URL else None
        self.mongo_client = MongoClient(MONGO_URL) if MONGO_URL else None
        self.mongo_db = self.mongo_client.guest_engagement if self.mongo_client else None
        
    def get_db_connection(self):
        """Get PostgreSQL database connection"""
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

    def get_hotel_name(self, hotel_id: str) -> str:
        """Convert hotel_id to readable hotel name"""
        hotel_names = {
            'KUL_ELLA_PREMIUM_KLCC': 'ELLA Premium KLCC',
            'PEN_GRAND_ORIENTAL': 'Grand Oriental Penang',
            'BKK_ROYAL_ORCHID': 'Royal Orchid Bangkok'
        }
        return hotel_names.get(hotel_id, hotel_id.replace('_', ' ').title())

    def send_pre_arrival_welcome(self, guest_id: str, arrival_date: str, message_type: str = "standard") -> dict:
        """Send personalized pre-arrival welcome message to guest"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Get guest details and booking info
                    cur.execute("""
                        SELECT g.guest_id, cb.booking_id, cb.hotel_id, cb.room_type_id,
                               cb.check_in_date, cb.check_out_date, cb.total_nights
                        FROM guests g
                        JOIN confirmed_bookings cb ON g.guest_id = cb.guest_id
                        WHERE g.guest_id = %s AND cb.check_in_date = %s
                    """, (guest_id, arrival_date))
                    
                    booking = cur.fetchone()
                    if not booking:
                        return {'success': False, 'error': f"No booking found for guest {guest_id} on {arrival_date}"}
                    
                    hotel_name = self.get_hotel_name(booking['hotel_id'])
                    room_type = booking['room_type_id']
                    
                    # Get guest preferences from MongoDB
                    preferences = {}
                    if self.mongo_db:
                        guest_prefs = self.mongo_db.guest_preferences.find_one({"guest_id": guest_id})
                        if guest_prefs:
                            preferences = guest_prefs.get("preferences", {})
                    
                    # Create personalized message based on type
                    if message_type == "vip":
                        message = f"""🌟 VIP Welcome to {hotel_name}!

Dear Valued Guest,

We're thrilled to welcome you to {hotel_name} on {booking['check_in_date']}. 
Your room {room_type} is being specially prepared for your {booking['total_nights']}-night stay.

As our VIP guest, you'll enjoy:
• Priority check-in (available from 12:00 PM)
• Complimentary room upgrade (subject to availability)
• Welcome amenities in your room
• Late checkout until 2:00 PM

Please let us know if you have any special requests or preferences.

Best regards,
{hotel_name} Guest Relations Team"""

                    elif message_type == "business":
                        message = f"""💼 Business Travel Welcome to {hotel_name}!

Dear Business Traveler,

Welcome to {hotel_name}! We're ready for your arrival on {booking['check_in_date']}.

Business amenities included:
• High-speed WiFi throughout the property
• 24/7 business center access  
• Express check-in/checkout available
• In-room workspace setup

Your room {room_type} is confirmed for {booking['total_nights']} nights.

Safe travels,
{hotel_name} Team"""

                    else:  # standard
                        message = f"""🏨 Welcome to {hotel_name}!

Dear Guest,

Thank you for choosing {hotel_name}! We're excited to host you from {booking['check_in_date']} to {booking['check_out_date']}.

Your reservation details:
• Room: {room_type}
• Duration: {booking['total_nights']} nights
• Check-in: 3:00 PM onwards
• Check-out: 12:00 PM

We look forward to making your stay memorable!

Warm regards,
{hotel_name} Team"""

                    # Log communication in database
                    cur.execute("""
                        INSERT INTO pre_arrival_communications 
                        (guest_id, booking_id, message_type, message_content, sent_date, staff_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (guest_id, booking['booking_id'], message_type, message, 
                          datetime.now().date(), 'system'))
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'guest_id': guest_id,
                        'message_type': message_type,
                        'message': message,
                        'hotel_name': hotel_name
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def collect_guest_preferences(self, guest_id: str, preferences_data: dict) -> dict:
        """Collect and store guest preferences for personalized service"""
        try:
            # Store in MongoDB for rich preference data
            if self.mongo_db is not None:
                preference_doc = {
                    "guest_id": guest_id,
                    "preferences": preferences_data,
                    "updated_date": datetime.now(),
                    "collected_by": "staff"
                }
                
                # Upsert preferences
                self.mongo_db.guest_preferences.update_one(
                    {"guest_id": guest_id},
                    {"$set": preference_doc},
                    upsert=True
                )
            
            # Also log in PostgreSQL for tracking
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO guest_journey_touchpoints 
                        (guest_id, touchpoint_type, touchpoint_data, created_date)
                        VALUES (%s, %s, %s, %s)
                    """, (guest_id, 'preference_collection', json.dumps(preferences_data), datetime.now()))
                    
                    conn.commit()
            
            return {
                'success': True,
                'guest_id': guest_id,
                'preferences': preferences_data
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def offer_room_upgrades(self, guest_id: str, arrival_date: str, current_room_type: str) -> dict:
        """Check available room upgrades and send upgrade offers to guests"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Get current booking details
                    cur.execute("""
                        SELECT hotel_id, room_type_id, check_in_date, check_out_date, total_nights
                        FROM confirmed_bookings 
                        WHERE guest_id = %s AND check_in_date = %s
                    """, (guest_id, arrival_date))
                    
                    booking = cur.fetchone()
                    if not booking:
                        return {'success': False, 'error': f"No booking found for guest {guest_id} on {arrival_date}"}
                    
                    hotel_name = self.get_hotel_name(booking['hotel_id'])
                    
                    # Check availability for upgrade options
                    cur.execute("""
                        SELECT DISTINCT aa.room_type_id, rp.rate_per_night
                        FROM atomic_availability aa
                        JOIN room_pricing rp ON aa.room_type_id = rp.room_type_id
                        WHERE aa.stay_date BETWEEN %s AND %s 
                        AND aa.quantity >= 1
                        AND aa.room_type_id != %s
                        AND aa.room_type_id LIKE %s
                        ORDER BY rp.rate_per_night DESC
                    """, (booking['check_in_date'], booking['check_out_date'], 
                          booking['room_type_id'], f"{booking['hotel_id'][:3]}%"))
                    
                    available_upgrades = cur.fetchall()
                    
                    if not available_upgrades:
                        return {'success': False, 'error': f"No upgrade options available for {guest_id}"}
                    
                    # Create upgrade offers
                    upgrade_options = []
                    for room in available_upgrades[:3]:  # Show top 3 options
                        upgrade_cost = float(room['rate_per_night']) * booking['total_nights']
                        upgrade_options.append({
                            "room_type_id": room['room_type_id'],
                            "total_cost": upgrade_cost
                        })
                    
                    # Log upgrade offer
                    cur.execute("""
                        INSERT INTO guest_journey_touchpoints 
                        (guest_id, touchpoint_type, touchpoint_data, created_date)
                        VALUES (%s, %s, %s, %s)
                    """, (guest_id, 'upgrade_offer', json.dumps(upgrade_options), datetime.now()))
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'guest_id': guest_id,
                        'hotel_name': hotel_name,
                        'upgrade_options': upgrade_options,
                        'booking': dict(booking)
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def schedule_special_services(self, guest_id: str, service_type: str, service_details: str, requested_time: str) -> dict:
        """Schedule special services for guest arrival or during stay"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Check if guest has active booking
                    cur.execute("""
                        SELECT booking_id, hotel_id, check_in_date, check_out_date
                        FROM confirmed_bookings 
                        WHERE guest_id = %s 
                        AND check_in_date >= CURRENT_DATE
                        ORDER BY check_in_date LIMIT 1
                    """, (guest_id,))
                    
                    booking = cur.fetchone()
                    if not booking:
                        return {'success': False, 'error': f"No upcoming booking found for guest {guest_id}"}
                    
                    hotel_name = self.get_hotel_name(booking['hotel_id'])
                    
                    # Create service record
                    service_id = f"{guest_id}service{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    
                    cur.execute("""
                        INSERT INTO guest_special_services 
                        (service_id, guest_id, booking_id, service_type, service_details, 
                         requested_time, service_status, created_date)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (service_id, guest_id, booking['booking_id'], service_type, 
                          service_details, requested_time, 'scheduled', datetime.now()))
                    
                    # Store additional service details in MongoDB
                    if self.mongo_db is not None:
                        service_doc = {
                            "service_id": service_id,
                            "guest_id": guest_id,
                            "service_type": service_type,
                            "service_details": service_details,
                            "requested_time": requested_time,
                            "hotel_name": hotel_name,
                            "status": "scheduled",
                            "created_date": datetime.now()
                        }
                        self.mongo_db.special_services.insert_one(service_doc)
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'service_id': service_id,
                        'guest_id': guest_id,
                        'service_type': service_type,
                        'service_details': service_details,
                        'requested_time': requested_time,
                        'hotel_name': hotel_name
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def process_early_checkin_requests(self, guest_id: str, arrival_date: str, requested_time: str, reason: str = "") -> dict:
        """Process guest requests for early check-in"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Get booking details
                    cur.execute("""
                        SELECT booking_id, hotel_id, room_type_id, check_in_date
                        FROM confirmed_bookings 
                        WHERE guest_id = %s AND check_in_date = %s
                    """, (guest_id, arrival_date))
                    
                    booking = cur.fetchone()
                    if not booking:
                        return {'success': False, 'error': f"No booking found for guest {guest_id} on {arrival_date}"}
                    
                    # Check room availability for early arrival
                    previous_date = datetime.strptime(arrival_date, '%Y-%m-%d') - timedelta(days=1)
                    
                    cur.execute("""
                        SELECT quantity FROM atomic_availability 
                        WHERE room_type_id = %s AND stay_date = %s
                    """, (booking['room_type_id'], previous_date.date()))
                    
                    availability = cur.fetchone()
                    
                    # Determine if early check-in is possible
                    if availability and availability['quantity'] > 0:
                        status = "approved"
                    else:
                        status = "pending"
                    
                    # Log the request
                    cur.execute("""
                        INSERT INTO guest_journey_touchpoints 
                        (guest_id, touchpoint_type, touchpoint_data, created_date)
                        VALUES (%s, %s, %s, %s)
                    """, (guest_id, 'early_checkin_request', 
                          json.dumps({
                              "requested_time": requested_time,
                              "reason": reason,
                              "status": status
                          }), datetime.now()))
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'guest_id': guest_id,
                        'arrival_date': arrival_date,
                        'requested_time': requested_time,
                        'reason': reason,
                        'status': status
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def manage_arrival_instructions(self, guest_id: str, instruction_type: str, details: str) -> dict:
        """Manage special arrival instructions for guests"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Get active booking
                    cur.execute("""
                        SELECT booking_id, hotel_id, check_in_date
                        FROM confirmed_bookings 
                        WHERE guest_id = %s 
                        AND check_in_date >= CURRENT_DATE
                        ORDER BY check_in_date LIMIT 1
                    """, (guest_id,))
                    
                    booking = cur.fetchone()
                    if not booking:
                        return {'success': False, 'error': f"No upcoming booking found for guest {guest_id}"}
                    
                    hotel_name = self.get_hotel_name(booking['hotel_id'])
                    
                    # Store arrival instructions
                    instruction_id = f"{guest_id}instr{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    
                    cur.execute("""
                        INSERT INTO arrival_instructions 
                        (instruction_id, guest_id, booking_id, instruction_type, details, created_date)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (instruction_id, guest_id, booking['booking_id'], 
                          instruction_type, details, datetime.now()))
                    
                    # Also store in MongoDB for staff access
                    if self.mongo_db is not None:
                        instruction_doc = {
                            "instruction_id": instruction_id,
                            "guest_id": guest_id,
                            "booking_id": booking['booking_id'],
                            "instruction_type": instruction_type,
                            "details": details,
                            "hotel_name": hotel_name,
                            "check_in_date": booking['check_in_date'].isoformat(),
                            "created_date": datetime.now()
                        }
                        self.mongo_db.arrival_instructions.insert_one(instruction_doc)
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'instruction_id': instruction_id,
                        'guest_id': guest_id,
                        'instruction_type': instruction_type,
                        'details': details,
                        'hotel_name': hotel_name,
                        'check_in_date': booking['check_in_date']
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

if __name__ == "__main__":
    # Test the manager
    manager = GuestEngagementManager()
    print("🌟 Guest Engagement Manager initialized successfully!") 