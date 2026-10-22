#!/usr/bin/env python3
"""
Loyalty Manager - Database Operations Only
Handles database operations for loyalty program management
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from decimal import Decimal
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

class LoyaltyManager:
    """Manages loyalty program database operations"""
    
    def __init__(self):
        self.redis_client = redis.from_url(REDIS_URL) if REDIS_URL else None
        self.mongo_client = MongoClient(MONGO_URL) if MONGO_URL else None
        # Use the same database as guest_manager.py
        self.mongo_db = self.mongo_client['leon_hotel_memory'] if self.mongo_client else None
        # All loyalty program data should be in 'loyalty_program' collection within 'leon_hotel_memory'
        
    def get_db_connection(self):
        """Get PostgreSQL database connection"""
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

    def check_loyalty_status(self, guest_id: str) -> dict:
        """Check guest's current loyalty program status and benefits"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Get guest's loyalty information
                    cur.execute("""
                        SELECT g.guest_id, g.guest_name, lm.membership_tier, lm.points_balance,
                               lm.lifetime_points, lm.member_since, lm.tier_expiry_date,
                               lm.next_tier_points_needed, lm.membership_status
                        FROM guests g
                        LEFT JOIN loyalty_members lm ON g.guest_id = lm.guest_id
                        WHERE g.guest_id = %s
                    """, (guest_id,))
                    
                    member = cur.fetchone()
                    if not member:
                        return {'success': False, 'error': f"Guest {guest_id} not found in system"}
                    
                    if not member['membership_tier']:
                        return {
                            'success': True,
                            'is_member': False,
                            'guest_name': member['guest_name'],
                            'guest_id': guest_id
                        }

                    # Get recent activity
                    cur.execute("""
                        SELECT activity_type, points_earned, points_redeemed, 
                               activity_date, description
                        FROM loyalty_transactions
                        WHERE guest_id = %s
                        ORDER BY activity_date DESC
                        LIMIT 5
                    """, (guest_id,))
                    
                    recent_activity = cur.fetchall()
                    
                    return {
                        'success': True,
                        'is_member': True,
                        'member_data': dict(member),
                        'recent_activity': [dict(row) for row in recent_activity]
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def enroll_loyalty_member(self, guest_id: str, enrollment_tier: str = "BRONZE") -> dict:
        """Enroll a guest in the loyalty program"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Check if guest exists
                    cur.execute("SELECT guest_id, guest_name FROM guests WHERE guest_id = %s", (guest_id,))
                    guest = cur.fetchone()
                    
                    if not guest:
                        return {'success': False, 'error': f"Guest {guest_id} not found. Please ensure guest is registered first."}
                    
                    # Check if already enrolled
                    cur.execute("SELECT guest_id FROM loyalty_members WHERE guest_id = %s", (guest_id,))
                    existing = cur.fetchone()
                    
                    if existing:
                        return {'success': False, 'error': f"Guest {guest_id} is already enrolled in the loyalty program"}
                    
                    # Generate membership number
                    membership_number = f"LM{guest_id[-6:]}{datetime.now().strftime('%Y%m')}"
                    
                    # Calculate tier expiry (1 year from enrollment)
                    tier_expiry = datetime.now() + timedelta(days=365)
                    
                    # Points needed for next tier
                    next_tier_points = {
                        'BRONZE': 5000,
                        'SILVER': 10000,
                        'GOLD': 25000,
                        'PLATINUM': None
                    }
                    
                    # Enroll member
                    cur.execute("""
                        INSERT INTO loyalty_members (
                            guest_id, membership_number, membership_tier, points_balance,
                            lifetime_points, member_since, tier_expiry_date, 
                            next_tier_points_needed, membership_status
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (guest_id, membership_number, enrollment_tier, 0, 0,
                          datetime.now(), tier_expiry, next_tier_points[enrollment_tier], 'ACTIVE'))
                    
                    # Give welcome bonus
                    welcome_points = 500
                    cur.execute("""
                        INSERT INTO loyalty_transactions (
                            transaction_id, guest_id, activity_type, points_earned,
                            activity_date, description, staff_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (f"{guest_id}welcome{datetime.now().strftime('%Y%m%d%H%M%S')}",
                          guest_id, 'WELCOME_BONUS', welcome_points, datetime.now(),
                          'Welcome bonus for joining loyalty program', 'system'))
                    
                    # Update points balance
                    cur.execute("""
                        UPDATE loyalty_members 
                        SET points_balance = points_balance + %s,
                            lifetime_points = lifetime_points + %s
                        WHERE guest_id = %s
                    """, (welcome_points, welcome_points, guest_id))
                    
                    # Store member profile in MongoDB
                    if self.mongo_db is not None:
                        member_profile = {
                            "guest_id": guest_id,
                            "membership_number": membership_number,
                            "enrollment_date": datetime.now(),
                            "enrollment_tier": enrollment_tier,
                            "preferences": {
                                "communication_method": "email",
                                "newsletter_subscription": True,
                                "promotional_offers": True
                            },
                            "special_occasions": [],
                            "redemption_history": []
                        }
                        self.mongo_db.member_profiles.insert_one(member_profile)
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'guest_name': guest['guest_name'],
                        'membership_number': membership_number,
                        'enrollment_tier': enrollment_tier,
                        'welcome_points': welcome_points,
                        'tier_expiry': tier_expiry
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def award_loyalty_points(self, guest_id: str, points_amount: int, activity_description: str, activity_type: str = "STAY") -> dict:
        """Award loyalty points to a member for various activities"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Check if member exists
                    cur.execute("""
                        SELECT membership_tier, points_balance, lifetime_points,
                               next_tier_points_needed, membership_status
                        FROM loyalty_members 
                        WHERE guest_id = %s
                    """, (guest_id,))
                    
                    member = cur.fetchone()
                    if not member:
                        return {'success': False, 'error': f"Guest {guest_id} is not a loyalty member. Please enroll first."}
                    
                    if member['membership_status'] != 'ACTIVE':
                        return {'success': False, 'error': f"Member {guest_id} has {member['membership_status']} status. Cannot award points."}
                    
                    # Record transaction
                    transaction_id = f"{guest_id}{activity_type.lower()}{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    
                    cur.execute("""
                        INSERT INTO loyalty_transactions (
                            transaction_id, guest_id, activity_type, points_earned,
                            activity_date, description, staff_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (transaction_id, guest_id, activity_type, points_amount,
                          datetime.now(), activity_description, 'staff'))
                    
                    # Update member points
                    new_balance = member['points_balance'] + points_amount
                    new_lifetime = member['lifetime_points'] + points_amount
                    
                    cur.execute("""
                        UPDATE loyalty_members 
                        SET points_balance = %s, lifetime_points = %s
                        WHERE guest_id = %s
                    """, (new_balance, new_lifetime, guest_id))
                    
                    # Check for tier upgrade
                    current_tier = member['membership_tier']
                    tier_thresholds = {
                        'BRONZE': 0,
                        'SILVER': 5000,
                        'GOLD': 15000,
                        'PLATINUM': 30000
                    }
                    
                    new_tier = current_tier
                    for tier, threshold in tier_thresholds.items():
                        if new_lifetime >= threshold:
                            new_tier = tier
                    
                    tier_upgraded = False
                    if new_tier != current_tier:
                        # Update tier
                        tier_expiry = datetime.now() + timedelta(days=365)
                        next_tier_points = {
                            'BRONZE': 5000,
                            'SILVER': 15000,
                            'GOLD': 30000,
                            'PLATINUM': None
                        }
                        
                        cur.execute("""
                            UPDATE loyalty_members 
                            SET membership_tier = %s, tier_expiry_date = %s,
                                next_tier_points_needed = %s
                            WHERE guest_id = %s
                        """, (new_tier, tier_expiry, 
                              next_tier_points[new_tier] - new_lifetime if next_tier_points[new_tier] else None,
                              guest_id))
                        
                        tier_upgraded = True
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'transaction_id': transaction_id,
                        'guest_id': guest_id,
                        'activity_description': activity_description,
                        'points_amount': points_amount,
                        'activity_type': activity_type,
                        'previous_balance': member['points_balance'],
                        'new_balance': new_balance,
                        'new_lifetime': new_lifetime,
                        'current_tier': current_tier,
                        'new_tier': new_tier,
                        'tier_upgraded': tier_upgraded
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def redeem_loyalty_rewards(self, guest_id: str, reward_type: str, points_cost: int) -> dict:
        """Process loyalty points redemption for rewards"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Check member balance
                    cur.execute("""
                        SELECT membership_tier, points_balance, membership_status
                        FROM loyalty_members 
                        WHERE guest_id = %s
                    """, (guest_id,))
                    
                    member = cur.fetchone()
                    if not member:
                        return {'success': False, 'error': f"Guest {guest_id} is not a loyalty member"}
                    
                    if member['membership_status'] != 'ACTIVE':
                        return {'success': False, 'error': f"Member {guest_id} has {member['membership_status']} status"}
                    
                    if member['points_balance'] < points_cost:
                        return {
                            'success': False,
                            'error': 'insufficient_points',
                            'current_balance': member['points_balance'],
                            'required_points': points_cost,
                            'shortage': points_cost - member['points_balance']
                        }
                    
                    # Process redemption
                    transaction_id = f"{guest_id}redeem{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    
                    cur.execute("""
                        INSERT INTO loyalty_transactions (
                            transaction_id, guest_id, activity_type, points_redeemed,
                            activity_date, description, staff_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (transaction_id, guest_id, 'REDEMPTION', points_cost,
                          datetime.now(), f"Redeemed {reward_type}", 'staff'))
                    
                    # Update balance
                    new_balance = member['points_balance'] - points_cost
                    cur.execute("""
                        UPDATE loyalty_members 
                        SET points_balance = %s
                        WHERE guest_id = %s
                    """, (new_balance, guest_id))
                    
                    # Generate reward voucher
                    voucher_code = f"RWD{guest_id[-4:]}{datetime.now().strftime('%m%d%H%M')}"
                    
                    cur.execute("""
                        INSERT INTO loyalty_rewards (
                            voucher_code, guest_id, reward_type, points_redeemed,
                            issue_date, expiry_date, reward_status, transaction_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (voucher_code, guest_id, reward_type, points_cost,
                          datetime.now(), datetime.now() + timedelta(days=365),
                          'ISSUED', transaction_id))
                    
                    # Store in MongoDB for detailed tracking
                    if self.mongo_db is not None:
                        reward_doc = {
                            "voucher_code": voucher_code,
                            "guest_id": guest_id,
                            "reward_type": reward_type,
                            "points_redeemed": points_cost,
                            "issue_date": datetime.now(),
                            "expiry_date": datetime.now() + timedelta(days=365),
                            "usage_instructions": f"Present voucher code {voucher_code} at time of service",
                            "terms_conditions": "Valid for 12 months from issue date. Cannot be exchanged for cash."
                        }
                        self.mongo_db.issued_rewards.insert_one(reward_doc)
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'voucher_code': voucher_code,
                        'guest_id': guest_id,
                        'reward_type': reward_type,
                        'points_cost': points_cost,
                        'previous_balance': member['points_balance'],
                        'new_balance': new_balance,
                        'transaction_id': transaction_id,
                        'expiry_date': datetime.now() + timedelta(days=365)
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def check_available_rewards(self, guest_id: str) -> dict:
        """Show available rewards based on member's point balance and tier"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Get member details
                    cur.execute("""
                        SELECT membership_tier, points_balance
                        FROM loyalty_members 
                        WHERE guest_id = %s
                    """, (guest_id,))
                    
                    member = cur.fetchone()
                    if not member:
                        return {'success': False, 'error': f"Guest {guest_id} is not a loyalty member"}
                    
                    points_balance = member['points_balance']
                    tier = member['membership_tier']
                    
                    # Define available rewards by tier
                    rewards_catalog = {
                        'BRONZE': [
                            {'name': 'Late Checkout (2 PM)', 'points': 500, 'type': 'late_checkout_2pm'},
                            {'name': 'Welcome Drink Voucher', 'points': 750, 'type': 'welcome_drink'},
                            {'name': '$25 Dining Credit', 'points': 1500, 'type': 'dining_credit_25'}
                        ],
                        'SILVER': [
                            {'name': 'Late Checkout (3 PM)', 'points': 500, 'type': 'late_checkout_3pm'},
                            {'name': 'Room Upgrade', 'points': 2000, 'type': 'room_upgrade'},
                            {'name': '$50 Dining Credit', 'points': 2500, 'type': 'dining_credit_50'},
                            {'name': 'Spa Treatment (60 min)', 'points': 4000, 'type': 'spa_60min'}
                        ],
                        'GOLD': [
                            {'name': 'Late Checkout (4 PM)', 'points': 500, 'type': 'late_checkout_4pm'},
                            {'name': 'Suite Upgrade', 'points': 3000, 'type': 'suite_upgrade'},
                            {'name': '$100 Dining Credit', 'points': 4000, 'type': 'dining_credit_100'},
                            {'name': 'Spa Package (90 min)', 'points': 6000, 'type': 'spa_90min'},
                            {'name': 'Airport Transfer', 'points': 3500, 'type': 'airport_transfer'}
                        ],
                        'PLATINUM': [
                            {'name': 'Late Checkout (6 PM)', 'points': 500, 'type': 'late_checkout_6pm'},
                            {'name': 'Presidential Suite Upgrade', 'points': 5000, 'type': 'presidential_upgrade'},
                            {'name': '$200 Dining Credit', 'points': 7000, 'type': 'dining_credit_200'},
                            {'name': 'Full Day Spa Package', 'points': 10000, 'type': 'spa_full_day'},
                            {'name': 'Free Night Stay', 'points': 15000, 'type': 'free_night'},
                            {'name': 'Weekend Getaway Package', 'points': 25000, 'type': 'weekend_package'}
                        ]
                    }
                    
                    available_rewards = rewards_catalog.get(tier, [])
                    
                    return {
                        'success': True,
                        'guest_id': guest_id,
                        'tier': tier,
                        'points_balance': points_balance,
                        'available_rewards': available_rewards
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def manage_special_occasions(self, guest_id: str, occasion_type: str, occasion_date: str, notes: str = "") -> dict:
        """Manage special occasions for loyalty members"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Check if member exists
                    cur.execute("SELECT membership_tier FROM loyalty_members WHERE guest_id = %s", (guest_id,))
                    member = cur.fetchone()
                    
                    if not member:
                        return {'success': False, 'error': f"Guest {guest_id} is not a loyalty member"}
                    
                    # Store special occasion
                    occasion_id = f"{guest_id}{occasion_type}{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    
                    cur.execute("""
                        INSERT INTO member_special_occasions (
                            occasion_id, guest_id, occasion_type, occasion_date,
                            notes, created_date, notification_enabled
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (occasion_id, guest_id, occasion_type, occasion_date,
                          notes, datetime.now(), True))
                    
                    # Store in MongoDB for rich data
                    if self.mongo_db is not None:
                        occasion_doc = {
                            "occasion_id": occasion_id,
                            "guest_id": guest_id,
                            "occasion_type": occasion_type,
                            "occasion_date": datetime.strptime(occasion_date, '%Y-%m-%d'),
                            "notes": notes,
                            "preferences": {
                                "room_decoration": True,
                                "special_amenities": True,
                                "surprise_gift": True
                            },
                            "history": []
                        }
                        self.mongo_db.special_occasions.insert_one(occasion_doc)
                    
                    # Calculate days until occasion
                    occasion_dt = datetime.strptime(occasion_date, '%Y-%m-%d')
                    days_until = (occasion_dt - datetime.now()).days
                    
                    conn.commit()
                    
                    return {
                        'success': True,
                        'occasion_id': occasion_id,
                        'guest_id': guest_id,
                        'occasion_type': occasion_type,
                        'occasion_date': occasion_dt,
                        'notes': notes,
                        'days_until': days_until
                    }
                    
        except Exception as e:
            return {'success': False, 'error': str(e)}

if __name__ == "__main__":
    # Test the manager
    manager = LoyaltyManager()
    print("🌟 Loyalty Manager initialized successfully!")