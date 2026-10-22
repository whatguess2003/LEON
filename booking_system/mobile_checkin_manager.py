#!/usr/bin/env python3
"""
Mobile Check-in Manager - Database Operations Only
Handles database operations for mobile check-in processes
"""

import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from pymongo import MongoClient
import redis
from datetime import datetime, date, timedelta
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
from .timezone_utils import get_hotel_timezone_str
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

# Database connections
DATABASE_URL = os.getenv('DATABASE_URL')
MONGO_URL = os.getenv('MONGO_URL')
REDIS_URL = os.getenv('REDIS_URL')

class MobileCheckinManager:
    """Manages mobile check-in database operations"""
    
    def __init__(self):
        self.mongo_client = MongoClient(MONGO_URL) if MONGO_URL else None
        self.mongo_db = self.mongo_client.ella_hotel_encyclopedia if self.mongo_client else None
        self.redis_client = redis.from_url(REDIS_URL) if REDIS_URL else None
    def _require_zoneinfo(self, hotel_name: str):
        tz = get_hotel_timezone_str(hotel_name)
        if not tz or ZoneInfo is None:
            raise ValueError("❌ Missing hotel timezone")
        return ZoneInfo(tz)
    def _now_local(self, hotel_name: str) -> datetime:
        return datetime.now(self._require_zoneinfo(hotel_name))
        
    def get_db_connection(self):
        """Get PostgreSQL database connection"""
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

    def initiate_mobile_checkin(self, guest_id: str, arrival_date: str) -> dict:
        """Start mobile check-in process for a guest"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Get booking details
                    cur.execute("""
                        SELECT cb.*, g.guest_name 
                        FROM confirmed_bookings cb
                        JOIN guests g ON cb.guest_id = g.guest_id
                        WHERE cb.guest_id = %s AND cb.check_in_date = %s
                    """, (guest_id, arrival_date))
                    
                    booking = cur.fetchone()
                    if not booking:
                        return {'success': False, 'error': f"No booking found for {guest_id} on {arrival_date}"}
                    
                    # Create mobile check-in record
                    checkin_id = f"{guest_id}checkin{datetime.now().strftime('%Y%m%d%H%M')}"
                    
                    cur.execute("""
                        INSERT INTO mobile_checkin_sessions (
                            session_id, guest_id, booking_id, hotel_id, room_type_id,
                            checkin_status, initiated_at
                        ) VALUES (%s, %s, %s, %s, %s, 'INITIATED', NOW())
                    """, (checkin_id, guest_id, booking['booking_id'], booking['hotel_id'], booking['room_type_id']))
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'checkin_id': checkin_id,
                        'guest_name': booking['guest_name'],
                        'guest_id': guest_id,
                        'booking_id': booking['booking_id'],
                        'room_type_id': booking['room_type_id'],
                        'arrival_date': arrival_date
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def verify_guest_identity(self, guest_id: str, verification_data: dict) -> dict:
        """Verify guest identity for mobile check-in"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Get guest details
                    cur.execute("SELECT * FROM guests WHERE guest_id = %s", (guest_id,))
                    guest = cur.fetchone()
                    
                    if not guest:
                        return {'success': False, 'error': f"Guest {guest_id} not found"}
                    
                    # Update mobile check-in with verification
                    cur.execute("""
                        UPDATE mobile_checkin_sessions 
                        SET identity_verified = TRUE, 
                            verification_method = %s,
                            verification_time = NOW()
                        WHERE guest_id = %s AND checkin_status = 'INITIATED'
                    """, (verification_data.get('method', 'ID_CARD'), guest_id))
                    
                    # Store verification details in MongoDB
                    if self.mongo_db:
                        self.mongo_db.guest_verifications.insert_one({
                            'guest_id': guest_id,
                            'verification_data': verification_data,
                            'verified_at': datetime.now(),
                            'verified_by': 'mobile_checkin_manager'
                        })
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'guest_name': guest['guest_name'],
                        'verification_method': verification_data.get('method', 'ID_CARD'),
                        'document_type': verification_data.get('document_type', 'N/A')
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def update_room_preferences(self, guest_id: str, preferences: dict) -> dict:
        """Update room preferences during mobile check-in"""
        try:
            # Update guest preferences in MongoDB
            if self.mongo_db:
                self.mongo_db.guest_preferences.update_one(
                    {'guest_id': guest_id},
                    {
                        '$set': {
                            'room_preferences': preferences,
                            'updated_at': datetime.now(),
                            'updated_via': 'mobile_checkin'
                        }
                    },
                    upsert=True
                )
            
            # Update mobile check-in record
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE mobile_checkin_sessions 
                        SET preferences_updated = TRUE,
                            preferences_time = NOW()
                        WHERE guest_id = %s AND checkin_status = 'INITIATED'
                    """, (guest_id,))
                    
                    conn.commit()
            
            return {
                'success': True,
                'guest_id': guest_id,
                'preferences': preferences
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def generate_digital_room_key(self, guest_id: str, room_type_id: str) -> dict:
        """Generate digital room key for mobile check-in"""
        try:
            # Generate unique key
            key_code = f"KEY{guest_id[-4:]}{room_type_id[-3:]}{datetime.now().strftime('%H%M')}"
            
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Create digital key record
                    key_id = f"{guest_id}key{datetime.now().strftime('%Y%m%d')}"
                    valid_until = datetime.now() + timedelta(days=7)
                    
                    cur.execute("""
                        INSERT INTO digital_keys (
                            key_id, guest_id, room_type_id, key_code,
                            valid_from, valid_until, key_status, created_at
                        ) VALUES (%s, %s, %s, %s, NOW(), %s, 'ACTIVE', NOW())
                    """, (key_id, guest_id, room_type_id, key_code, valid_until))
                    
                    # Update mobile check-in
                    cur.execute("""
                        UPDATE mobile_checkin_sessions 
                        SET digital_key_generated = TRUE,
                            key_generation_time = NOW(),
                            checkin_status = 'COMPLETED'
                        WHERE guest_id = %s AND room_type_id = %s
                    """, (guest_id, room_type_id))
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'guest_id': guest_id,
                        'room_type_id': room_type_id,
                        'key_code': key_code,
                        'valid_from': datetime.now(),
                        'valid_until': valid_until
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def check_mobile_checkin_status(self, guest_id: str) -> dict:
        """Check current mobile check-in status for a guest"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT mc.*, g.guest_name, cb.room_type_id
                        FROM mobile_checkin_sessions mc
                        JOIN guests g ON mc.guest_id = g.guest_id
                        JOIN confirmed_bookings cb ON mc.booking_id = cb.booking_id
                        WHERE mc.guest_id = %s
                        ORDER BY mc.initiated_at DESC
                        LIMIT 1
                    """, (guest_id,))
                    
                    checkin = cur.fetchone()
                    if not checkin:
                        return {'success': False, 'error': f"No mobile check-in found for {guest_id}"}
                    
                    # Check digital key status
                    cur.execute("""
                        SELECT key_code, valid_until, key_status
                        FROM digital_keys
                        WHERE guest_id = %s AND room_type_id = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, (guest_id, checkin['room_type_id']))
                    
                    key_info = cur.fetchone()
                    
                    return {
                        'success': True,
                        'checkin_data': dict(checkin),
                        'key_info': dict(key_info) if key_info else None
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def handle_checkin_issues(self, guest_id: str, issue_type: str, resolution: str) -> dict:
        """Handle mobile check-in issues and provide resolutions"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Log the issue
                    issue_id = f"{guest_id}issue{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    
                    cur.execute("""
                        INSERT INTO checkin_issues (
                            issue_id, guest_id, issue_type, issue_description,
                            resolution_action, resolved_at, resolved_by
                        ) VALUES (%s, %s, %s, %s, %s, NOW(), 'mobile_checkin_manager')
                    """, (issue_id, guest_id, issue_type, issue_type, resolution))
                    
                    # Update mobile check-in status if needed
                    if issue_type == 'identity_failed':
                        cur.execute("""
                            UPDATE mobile_checkin_sessions 
                            SET checkin_status = 'IDENTITY_REVIEW',
                                identity_verified = FALSE
                            WHERE guest_id = %s
                        """, (guest_id,))
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'issue_id': issue_id,
                        'guest_id': guest_id,
                        'issue_type': issue_type,
                        'resolution': resolution
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

if __name__ == "__main__":
    # Test the manager
    manager = MobileCheckinManager()
    print("🌟 Mobile Check-in Manager initialized successfully!") 