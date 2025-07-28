#!/usr/bin/env python3
"""
Enhanced Guest Manager for LEON PMS System
Manager & Copilot focused - Advanced guest management with analytics

PMS ENHANCEMENTS:
- Loyalty program management with tier benefits
- Guest communication tracking and history
- Advanced analytics and reporting
- Manager-specific dashboard views
- Guest preferences and special requests
- Cross-hotel guest tracking
- Revenue analysis per guest
- Guest satisfaction metrics
"""

import pymongo
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI
import re
from .llm_utils import canonical_key

# Load environment variables
load_dotenv()

class EnhancedGuestManager:
    def __init__(self):
        """Initialize Enhanced Guest Manager for PMS operations"""
        self.mongo_client = None
        self.mongo_db = None
        
        # Initialize LLM for parsing unstructured guest information
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
        
        self._connect_database()
        self._ensure_guest_collection()
        self._ensure_loyalty_collection()
        self._ensure_communication_collection()
    
    def _connect_database(self):
        """Connect to MongoDB only"""
        try:
            # MongoDB connection
            self.mongo_client = pymongo.MongoClient(os.getenv('MONGO_URL'))
            self.mongo_db = self.mongo_client['leon_hotel_memory']
            
            # Database connection established silently
            self._ensure_guest_collection()
        except Exception as e:
            print(f"❌ MongoDB connection error: {e}")
    
    def _get_hotel_context(self) -> Dict[str, Any]:
        """Get hotel context from manager_context.json for currency and other hotel-specific settings"""
        try:
            context_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'manager_context.json')
            with open(context_path, 'r') as f:
                context_data = json.load(f)
                return context_data.get('active_manager', {})
        except Exception as e:
            print(f"❌ Error reading manager context: {e}")
            return {}
    
    def _ensure_guest_collection(self):
        """Ensure guests collection exists with comprehensive indexes for scalability"""
        try:
            guests_collection = self.mongo_db['guests']
            
            # Comprehensive indexing for billions of profiles
            # Primary key index (unique)
            guests_collection.create_index("guest_phone", unique=True, name="guest_phone_unique")
            
            # Search performance indexes
            guests_collection.create_index("guest_name", name="guest_name_search")
            guests_collection.create_index("email", name="email_search")
            guests_collection.create_index("nationality", name="nationality_filter")
            guests_collection.create_index("loyalty_tier", name="loyalty_tier_filter")
            guests_collection.create_index("status", name="status_filter")
            
            # Compound indexes for complex queries
            guests_collection.create_index([
                ("status", 1),
                ("loyalty_tier", 1)
            ], name="status_loyalty_compound")
            
            guests_collection.create_index([
                ("nationality", 1),
                ("loyalty_tier", 1)
            ], name="nationality_loyalty_compound")
            
            # Time-based indexes
            guests_collection.create_index("registration_date", name="registration_date_sort")
            guests_collection.create_index("last_updated", name="last_updated_sort")
            
            # Statistical indexes
            guests_collection.create_index("total_bookings", name="total_bookings_sort")
            guests_collection.create_index("total_spent", name="total_spent_sort")
            
            # Indexes created silently for scalability
        except Exception as e:
            print(f"⚠️ Index creation warning: {e}")

    def _ensure_loyalty_collection(self):
        """Ensure loyalty program collection exists"""
        try:
            loyalty_collection = self.mongo_db['loyalty_program']
            
            # Loyalty program indexes
            loyalty_collection.create_index("guest_phone", unique=True, name="loyalty_guest_unique")
            loyalty_collection.create_index("loyalty_tier", name="loyalty_tier_index")
            loyalty_collection.create_index("points_balance", name="points_balance_index")
            loyalty_collection.create_index("last_activity", name="last_activity_index")
            
        except Exception as e:
            print(f"⚠️ Loyalty collection warning: {e}")

    def _ensure_communication_collection(self):
        """Ensure guest communication tracking collection exists"""
        try:
            comm_collection = self.mongo_db['guest_communications']
            
            # Communication tracking indexes
            comm_collection.create_index("guest_phone", name="comm_guest_index")
            comm_collection.create_index("communication_date", name="comm_date_index")
            comm_collection.create_index("communication_type", name="comm_type_index")
            comm_collection.create_index([
                ("guest_phone", 1),
                ("communication_date", -1)
            ], name="guest_comm_compound")
            
        except Exception as e:
            print(f"⚠️ Communication collection warning: {e}")

    def create_guest_profile(self, guest_phone: str, guest_name: str, email: str = None, 
                           nationality: str = None, preferences: Dict = None, 
                           loyalty_tier: str = "standard", special_requests: str = None) -> str:
        """Create new guest profile with comprehensive validation (MongoDB only)
        
        Args:
            guest_phone: Phone number (primary key, e.g., '+60123456789')
            guest_name: Guest full name
            email: Email address (optional)
            nationality: Guest nationality (optional)
            preferences: Guest preferences dict (optional, structured)
            loyalty_tier: Loyalty program tier (default: 'standard')
            special_requests: Any special requests (optional)
            
        Returns:
            Status message with creation result
        """
        try:
            # Validate phone format
            if not self._validate_phone_format(guest_phone):
                return f"❌ Invalid phone format. Use international format: +CountryCodePhoneNumber (e.g., +60123456789, +8613912345678)"
            
            # Universal phone uniqueness check (MongoDB only)
            validation = self._validate_phone_uniqueness(guest_phone, "guest")
            if not validation["can_proceed"]:
                return validation["message"]
            
            # Validate required fields
            if not guest_name or not guest_name.strip():
                return f"❌ Guest name is required"
            
            # Get manager context for signature
            manager_context = self._get_hotel_context()
            created_by = {
                "manager_name": manager_context.get("manager_name", "Unknown"),
                "manager_phone": manager_context.get("manager_phone", "Unknown")
            }
            # --- NEW: Structured preferences with recency ---
            def _normalize_preferences(pref_dict):
                now = datetime.now()
                categories = [
                    "room_type", "room_view", "dietary", "smoking", "accessibility", "bed_type", "floor_preference", "amenities", "other"
                ]
                structured = {}
                for cat in categories:
                    values = pref_dict.get(cat, []) if pref_dict else []
                    # Defensive: if a single string, wrap in list
                    if isinstance(values, str):
                        values = [values]
                    # Defensive: if a list of dicts with 'value', extract those values
                    elif isinstance(values, list) and all(isinstance(v, dict) and 'value' in v for v in values):
                        values = [v['value'] for v in values]
                    # Defensive: if a list of strings, keep as is
                    structured[cat] = [
                        {"value": v, "last_updated": now} for v in values if v
                    ]
                return structured
            preferences_structured = _normalize_preferences(preferences)
            # --- END NEW ---
            # Create guest profile document
            guest_profile = {
                "guest_phone": guest_phone,
                "guest_name": guest_name.strip(),
                "email": email,
                "nationality": nationality,
                "preferences": preferences_structured,
                "loyalty_tier": loyalty_tier,
                "special_requests": special_requests,
                "registration_date": datetime.now(),
                "last_updated": datetime.now(),
                "created_by": created_by,
                "total_bookings": 0,
                "total_spent": 0.0,
                "average_rating": 0.0,
                "status": "active",
                "communication_preferences": {
                    "preferred_language": "English",
                    "communication_method": "phone",
                    "marketing_consent": False
                }
            }
            
            # Insert into MongoDB
            self.mongo_db['guests'].insert_one(guest_profile)
            
            # Initialize loyalty program entry
            loyalty_entry = {
                "guest_phone": guest_phone,
                "loyalty_tier": loyalty_tier,
                "points_balance": 0,
                "points_earned": 0,
                "points_redeemed": 0,
                "last_activity": datetime.now(),
                "tier_benefits": self._get_tier_benefits(loyalty_tier),
                "created_at": datetime.now()
            }
            self.mongo_db['loyalty_program'].insert_one(loyalty_entry)
            
            return f"✅ Guest profile created successfully for {guest_name} ({guest_phone})\n💡 Stored in MongoDB with comprehensive indexing\n💡 Loyalty program initialized\n💡 {validation['message']}"
            
        except Exception as e:
            return f"❌ Error creating guest profile: {e}"

    def read_guest_profile(self, guest_phone: str) -> str:
        """READ comprehensive guest profile data (MongoDB only)"""
        try:
            # Get hotel context for currency
            hotel_context = self._get_hotel_context()
            currency = hotel_context.get('currency', 'MYR')  # Default to MYR if not specified
            # Find guest in MongoDB
            guest = self.mongo_db['guests'].find_one({"guest_phone": guest_phone, "status": "active"})
            if not guest:
                return f"❌ No guest profile found for {guest_phone}. Use create_guest_profile to add new guest."
            # Get loyalty information
            loyalty_info = self.mongo_db['loyalty_program'].find_one({"guest_phone": guest_phone})
            # Get recent communications
            recent_comms = list(self.mongo_db['guest_communications'].find(
                {"guest_phone": guest_phone}
            ).sort("communication_date", -1).limit(5))
            # Build comprehensive profile view
            result = f"👤 GUEST PROFILE ({guest_phone}):\n"
            result += f"  • Name: {guest.get('guest_name', 'Unknown')}\n"
            result += f"  • Email: {guest.get('email', 'None')}\n"
            result += f"  • Nationality: {guest.get('nationality', 'Not specified')}\n"
            result += f"  • Loyalty Tier: {guest.get('loyalty_tier', 'standard')}\n"
            result += f"  • Status: {guest.get('status', 'active')}\n"
            # Loyalty details
            if loyalty_info:
                result += f"  • Loyalty Points: {loyalty_info.get('points_balance', 0)}\n"
                result += f"  • Points Earned: {loyalty_info.get('points_earned', 0)}\n"
                result += f"  • Last Activity: {loyalty_info.get('last_activity', 'Never')}\n"
            # Preferences
            preferences = guest.get('preferences', {})
            if preferences:
                result += f"  • Preferences (with recency):\n"
                for cat, items in preferences.items():
                    if items:
                        result += f"    - {cat.replace('_', ' ').title()}: "
                        result += ", ".join([
                            f"{item['value']} (updated {item['last_updated'].strftime('%Y-%m-%d') if isinstance(item['last_updated'], datetime) else item['last_updated']})"
                            for item in items if 'value' in item
                        ]) + "\n"
            else:
                result += f"  • Preferences: None specified\n"
            # Special requests
            if guest.get('special_requests'):
                result += f"  • Special Requests: {guest.get('special_requests')}\n"
            # Statistics
            result += f"  • Total Bookings: {guest.get('total_bookings', 0)}\n"
            result += f"  • Total Spent: {currency} {guest.get('total_spent', 0.0):.2f}\n"
            result += f"  • Average Rating: {guest.get('average_rating', 0.0):.1f}/10\n"
            # Recent communications
            if recent_comms:
                result += f"  • Recent Communications:\n"
                for comm in recent_comms:
                    comm_type = comm.get('communication_type', 'Unknown')
                    comm_date = comm.get('communication_date', 'Unknown')
                    result += f"    - {comm_type}: {comm_date}\n"
            # Timestamps
            result += f"  • Registered: {guest.get('registration_date', 'Unknown')}\n"
            result += f"  • Last Updated: {guest.get('last_updated', 'Unknown')}\n"
            result += f"\n🎯 Natural Keys: guest_phone as primary identifier"
            result += f"\n💾 Storage: MongoDB with comprehensive indexing"
            result += f"\n💰 Currency: {currency} (from hotel context)"
            return result
        except Exception as e:
            return f"❌ Error viewing guest profile: {e}"

    def update_guest_profile(self, guest_phone: str, update_fields: Dict[str, Any]) -> str:
        """Update guest profile fields (MongoDB only)"""
        try:
            # Check if guest exists
            existing_guest = self.mongo_db['guests'].find_one({"guest_phone": guest_phone, "status": "active"})
            if not existing_guest:
                return f"❌ Guest profile not found for {guest_phone}. Use create_guest_profile to add new guest."
            
            # Get manager context for signature
            manager_context = self._get_hotel_context()
            updated_by = {
                "manager_name": manager_context.get("manager_name", "Unknown"),
                "manager_phone": manager_context.get("manager_phone", "Unknown")
            }
            # Prepare update document
            update_doc = {"$set": {"last_updated": datetime.now(), "updated_by": updated_by}}
            # --- NEW: Merge structured preferences with recency ---
            if "preferences" in update_fields:
                now = datetime.now()
                new_prefs = update_fields["preferences"]
                old_prefs = existing_guest.get("preferences", {})
                categories = [
                    "room_type", "room_view", "dietary", "smoking", "accessibility", "bed_type", "floor_preference", "amenities", "other"
                ]
                merged = {}
                for cat in categories:
                    old_list = old_prefs.get(cat, [])
                    new_list = new_prefs.get(cat, []) if new_prefs else []
                    # Convert old_list to dict for fast lookup
                    old_map = {item["value"]: item for item in old_list if isinstance(item, dict) and "value" in item}
                    # Add/replace with new values, update recency
                    for item in new_list:
                        if isinstance(item, dict):
                            val = item.get("value")
                        else:
                            val = item
                        if val:
                            old_map[val] = {"value": val, "last_updated": now}
                    merged[cat] = list(old_map.values())
                update_doc["$set"]["preferences"] = merged
                del update_fields["preferences"]
            # --- END NEW ---
            # Add all other update fields
            for field, value in update_fields.items():
                update_doc["$set"][field] = value
            # Update guest profile
            result = self.mongo_db['guests'].update_one(
                {"guest_phone": guest_phone}, 
                update_doc
            )
            if result.modified_count > 0:
                result_msg = f"✅ Guest profile updated for {guest_phone}:\n"
                for field, value in update_fields.items():
                    if field == 'preferences' and isinstance(value, dict):
                        result_msg += f"  • {field.title()}: {len(value)} items\n"
                    else:
                        result_msg += f"  • {field.title()}: {value}\n"
                result_msg += f"  • Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                result_msg += f"  • Updated By: {updated_by['manager_name']} ({updated_by['manager_phone']})\n"
                result_msg += f"  • Storage: MongoDB with indexed updates"
                return result_msg
            else:
                return f"⚠️ No changes made to guest profile {guest_phone}"
        except Exception as e:
            return f"❌ Error updating guest profile: {e}"

    def delete_guest_profile(self, guest_phone: str, reason: str = "Guest request") -> str:
        """Soft delete guest profile: mark as inactive, record manager signature and timestamp (MongoDB only)"""
        try:
            # Check if guest exists and get details
            guest = self.mongo_db['guests'].find_one({"guest_phone": guest_phone, "status": "active"})
            if not guest:
                return f"❌ Guest profile not found for {guest_phone}"
            guest_name = guest.get('guest_name', 'Unknown')
            total_bookings = guest.get('total_bookings', 0)
            # Safety warning for guests with booking history
            if total_bookings > 0:
                warning_msg = f"⚠️ WARNING: Guest {guest_name} has {total_bookings} booking(s) in history.\n"
                warning_msg += f"Consider deactivating instead of deleting.\n\n"
            else:
                warning_msg = ""
            # Get manager context for signature
            manager_context = self._get_hotel_context()
            deactivated_by = {
                "manager_name": manager_context.get("manager_name", "Unknown"),
                "manager_phone": manager_context.get("manager_phone", "Unknown")
            }
            # Soft delete: mark as inactive, add deactivated_at and deactivated_by
            result = self.mongo_db['guests'].update_one(
                {"guest_phone": guest_phone},
                {"$set": {
                    "status": "inactive",
                    "deactivated_at": datetime.now(),
                    "deactivated_by": deactivated_by,
                    "deactivation_reason": reason
                }}
            )
            if result.modified_count > 0:
                result_msg = warning_msg + f"✅ Guest profile marked as inactive (soft deleted):\n"
                result_msg += f"  • Guest Phone: {guest_phone}\n"
                result_msg += f"  • Guest Name: {guest_name}\n"
                result_msg += f"  • Deactivation Reason: {reason}\n"
                result_msg += f"  • Deactivated At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                result_msg += f"  • Deactivated By: {deactivated_by['manager_name']} ({deactivated_by['manager_phone']})\n"
                result_msg += f"  • ⚠️ Profile is now inactive (not removed)"
                return result_msg
            else:
                return f"⚠️ No changes made to guest profile {guest_phone} (already inactive?)"
        except Exception as e:
            return f"❌ Error deactivating guest profile: {e}"

    def read_guests_by_criteria(self, name: str = None, email: str = None, nationality: str = None, 
                     loyalty_tier: str = None, limit: int = 20) -> str:
        """READ guest profile data by criteria with indexed queries (MongoDB only)"""
        try:
            # Build MongoDB query with indexed fields
            query = {"status": "active"}
            filters = []
            
            if name:
                query['guest_name'] = {"$regex": name, "$options": "i"}
                filters.append(f"Name: {name}")
            
            if email:
                query['email'] = {"$regex": email, "$options": "i"}
                filters.append(f"Email: {email}")
            
            if nationality:
                query['nationality'] = {"$regex": nationality, "$options": "i"}
                filters.append(f"Nationality: {nationality}")
            
            if loyalty_tier:
                query['loyalty_tier'] = loyalty_tier
                filters.append(f"Loyalty Tier: {loyalty_tier}")
            
            # Execute indexed search
            guests = list(self.mongo_db['guests'].find(query).limit(limit))
            
            if not guests:
                return "❌ No guests found matching search criteria"
            
            # Build results
            result = f"🔍 GUEST SEARCH RESULTS ({len(guests)} found):\n"
            result += f"  • Filters: {', '.join(filters) if filters else 'None'}\n\n"
            
            for guest in guests:
                guest_phone = guest['guest_phone']
                guest_name = guest.get('guest_name', 'Unknown')
                email = guest.get('email', 'N/A')
                nationality = guest.get('nationality', 'N/A')
                loyalty_tier = guest.get('loyalty_tier', 'standard')
                
                result += f"👤 {guest_phone}: {guest_name}\n"
                result += f"  • Email: {email} | Nationality: {nationality}\n"
                result += f"  • Loyalty: {loyalty_tier}\n\n"
            
            result += f"💾 Indexed MongoDB search for optimal performance"
            return result
            
        except Exception as e:
            return f"❌ Error searching guests: {e}"

    def manage_loyalty_program(self, guest_phone: str, action: str, points: int = 0, 
                              new_tier: str = None) -> str:
        """Manage loyalty program for guests
        
        Args:
            guest_phone: Guest phone number
            action: 'earn_points', 'redeem_points', 'upgrade_tier', 'downgrade_tier', 'view_status'
            points: Points to earn/redeem
            new_tier: New loyalty tier for upgrade/downgrade
        """
        try:
            # Check if guest exists
            guest = self.mongo_db['guests'].find_one({"guest_phone": guest_phone, "status": "active"})
            if not guest:
                return f"❌ Guest profile not found for {guest_phone}"
            
            # Get current loyalty status
            loyalty_info = self.mongo_db['loyalty_program'].find_one({"guest_phone": guest_phone})
            if not loyalty_info:
                return f"❌ Loyalty program not found for {guest_phone}"
            
            current_points = loyalty_info.get('points_balance', 0)
            current_tier = loyalty_info.get('loyalty_tier', 'standard')
            
            if action == 'view_status':
                return self._format_loyalty_status(loyalty_info, guest.get('guest_name', 'Unknown'))
            
            elif action == 'earn_points':
                new_balance = current_points + points
                self.mongo_db['loyalty_program'].update_one(
                    {"guest_phone": guest_phone},
                    {
                        "$set": {
                            "points_balance": new_balance,
                            "points_earned": loyalty_info.get('points_earned', 0) + points,
                            "last_activity": datetime.now()
                        }
                    }
                )
                return f"✅ {points} points earned for {guest.get('guest_name', 'Unknown')}\n💡 New balance: {new_balance} points"
            
            elif action == 'redeem_points':
                if points > current_points:
                    return f"❌ Insufficient points. Current balance: {current_points}, requested: {points}"
                
                new_balance = current_points - points
                self.mongo_db['loyalty_program'].update_one(
                    {"guest_phone": guest_phone},
                    {
                        "$set": {
                            "points_balance": new_balance,
                            "points_redeemed": loyalty_info.get('points_redeemed', 0) + points,
                            "last_activity": datetime.now()
                        }
                    }
                )
                return f"✅ {points} points redeemed for {guest.get('guest_name', 'Unknown')}\n💡 New balance: {new_balance} points"
            
            elif action in ['upgrade_tier', 'downgrade_tier']:
                if not new_tier:
                    return f"❌ New tier must be specified for {action}"
                
                valid_tiers = ['bronze', 'silver', 'gold', 'platinum', 'diamond']
                if new_tier not in valid_tiers:
                    return f"❌ Invalid tier. Must be one of: {', '.join(valid_tiers)}"
                
                # Update both guest profile and loyalty program
                self.mongo_db['guests'].update_one(
                    {"guest_phone": guest_phone},
                    {"$set": {"loyalty_tier": new_tier, "last_updated": datetime.now()}}
                )
                
                self.mongo_db['loyalty_program'].update_one(
                    {"guest_phone": guest_phone},
                    {
                        "$set": {
                            "loyalty_tier": new_tier,
                            "tier_benefits": self._get_tier_benefits(new_tier),
                            "last_activity": datetime.now()
                        }
                    }
                )
                
                return f"✅ Loyalty tier updated: {current_tier} → {new_tier}\n💡 New benefits: {', '.join(self._get_tier_benefits(new_tier))}"
            
            else:
                return f"❌ Invalid action. Must be one of: view_status, earn_points, redeem_points, upgrade_tier, downgrade_tier"
                
        except Exception as e:
            return f"❌ Error managing loyalty program: {e}"

    def track_guest_communication(self, guest_phone: str, communication_type: str, 
                                message: str, staff_member: str = None) -> str:
        """Track guest communication for relationship management
        
        Args:
            guest_phone: Guest phone number
            communication_type: 'email', 'phone', 'sms', 'whatsapp', 'in_person'
            message: Communication content or summary
            staff_member: Staff member who made the communication
        """
        try:
            # Check if guest exists
            guest = self.mongo_db['guests'].find_one({"guest_phone": guest_phone, "status": "active"})
            if not guest:
                return f"❌ Guest profile not found for {guest_phone}"
            
            # Record communication
            comm_record = {
                "guest_phone": guest_phone,
                "communication_type": communication_type,
                "message": message,
                "staff_member": staff_member,
                "communication_date": datetime.now(),
                "hotel_context": self._get_hotel_context().get('hotel_name', 'Unknown')
            }
            
            self.mongo_db['guest_communications'].insert_one(comm_record)
            
            return f"✅ Communication tracked for {guest.get('guest_name', 'Unknown')}\n💡 Type: {communication_type}\n💡 Staff: {staff_member or 'System'}\n💡 Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            
        except Exception as e:
            return f"❌ Error tracking communication: {e}"

    def get_guest_analytics(self, date_from: str = None, date_to: str = None) -> str:
        """Get comprehensive guest analytics for management reporting"""
        try:
            # Default to last 30 days if no dates provided
            if not date_from:
                date_from = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
            if not date_to:
                date_to = datetime.now().strftime('%Y-%m-%d')
            
            # Get hotel context
            hotel_context = self._get_hotel_context()
            currency = hotel_context.get('currency', 'MYR')
            
            # Guest registration analytics
            pipeline = [
                {
                    "$match": {
                        "registration_date": {
                            "$gte": datetime.strptime(date_from, '%Y-%m-%d'),
                            "$lte": datetime.strptime(date_to, '%Y-%m-%d')
                        },
                        "status": "active"
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "total_new_guests": {"$sum": 1},
                        "avg_total_spent": {"$avg": "$total_spent"},
                        "avg_rating": {"$avg": "$average_rating"}
                    }
                }
            ]
            
            registration_stats = list(self.mongo_db['guests'].aggregate(pipeline))
            
            # Loyalty program analytics
            loyalty_stats = list(self.mongo_db['loyalty_program'].aggregate([
                {
                    "$group": {
                        "_id": "$loyalty_tier",
                        "count": {"$sum": 1},
                        "avg_points": {"$avg": "$points_balance"}
                    }
                },
                {"$sort": {"count": -1}}
            ]))
            
            # Communication analytics
            comm_stats = list(self.mongo_db['guest_communications'].aggregate([
                {
                    "$match": {
                        "communication_date": {
                            "$gte": datetime.strptime(date_from, '%Y-%m-%d'),
                            "$lte": datetime.strptime(date_to, '%Y-%m-%d')
                        }
                    }
                },
                {
                    "$group": {
                        "_id": "$communication_type",
                        "count": {"$sum": 1}
                    }
                },
                {"$sort": {"count": -1}}
            ]))
            
            # Build analytics report
            result = f"📊 GUEST ANALYTICS REPORT\n"
            result += f"📅 Period: {date_from} to {date_to}\n"
            result += f"🏨 Hotel: {hotel_context.get('hotel_name', 'Unknown')}\n\n"
            
            # Registration stats
            if registration_stats:
                stats = registration_stats[0]
                result += f"👥 REGISTRATION ANALYTICS:\n"
                result += f"  • New Guests: {stats.get('total_new_guests', 0)}\n"
                result += f"  • Avg Total Spent: {currency} {stats.get('avg_total_spent', 0):.2f}\n"
                result += f"  • Avg Rating: {stats.get('avg_rating', 0):.1f}/10\n\n"
            
            # Loyalty stats
            result += f"🎖️ LOYALTY PROGRAM ANALYTICS:\n"
            for tier in loyalty_stats:
                tier_name = tier['_id']
                count = tier['count']
                avg_points = tier['avg_points']
                result += f"  • {tier_name.title()}: {count} members (avg {avg_points:.0f} points)\n"
            
            # Communication stats
            result += f"\n📞 COMMUNICATION ANALYTICS:\n"
            for comm in comm_stats:
                comm_type = comm['_id']
                count = comm['count']
                result += f"  • {comm_type.title()}: {count} interactions\n"
            
            result += f"\n💡 Management Insights:\n"
            result += f"  • Track guest satisfaction trends\n"
            result += f"  • Monitor loyalty program effectiveness\n"
            result += f"  • Analyze communication preferences\n"
            result += f"  • Identify high-value guests for special attention"
            
            return result
            
        except Exception as e:
            return f"❌ Error generating analytics: {e}"

    def _get_tier_benefits(self, tier: str) -> List[str]:
        """Get benefits for loyalty tier"""
        benefits = {
            'bronze': ['Welcome gift', 'Standard check-in'],
            'silver': ['Priority check-in', 'Room upgrade (subject to availability)', 'Late check-out'],
            'gold': ['Complimentary breakfast', 'Free Wi-Fi', 'Concierge service', 'Spa discount'],
            'platinum': ['Suite upgrade', 'Airport transfer', 'Dining credit', 'Spa access'],
            'diamond': ['Personal concierge', 'All-inclusive benefits', 'VIP treatment', 'Exclusive events']
        }
        return benefits.get(tier, ['Standard benefits'])

    def _format_loyalty_status(self, loyalty_info: Dict, guest_name: str) -> str:
        """Format loyalty status for display"""
        result = f"🎖️ LOYALTY STATUS - {guest_name}\n"
        result += f"  • Tier: {loyalty_info.get('loyalty_tier', 'standard').title()}\n"
        result += f"  • Points Balance: {loyalty_info.get('points_balance', 0)}\n"
        result += f"  • Points Earned: {loyalty_info.get('points_earned', 0)}\n"
        result += f"  • Points Redeemed: {loyalty_info.get('points_redeemed', 0)}\n"
        result += f"  • Last Activity: {loyalty_info.get('last_activity', 'Never')}\n"
        result += f"  • Benefits: {', '.join(loyalty_info.get('tier_benefits', []))}"
        return result

    def _validate_phone_format(self, phone: str) -> bool:
        """Validate international phone number format"""
        if not phone or not phone.startswith('+'):
            return False
        # International phone: + followed by country code (1-4 digits) and phone number (8-15 digits total)
        pattern = r'^\+[1-9][0-9]{8,14}$'
        return bool(re.match(pattern, phone))

    def _validate_phone_uniqueness(self, phone: str, operation_type: str = "guest") -> Dict[str, Any]:
        """Universal phone validator - checks both guest and staff collections (MongoDB only)
        
        Args:
            phone: Phone number to validate
            operation_type: "guest" or "staff" - what type of profile we're creating
            
        Returns:
            Dict with validation result and conflict details
        """
        try:
            validation_result = {
                "is_unique": True,
                "conflicts": [],
                "can_proceed": True,
                "message": ""
            }
            
            # Check guest collection (find any, regardless of status)
            guest_exists = self.mongo_db['guests'].find_one({"guest_phone": phone})
            if guest_exists:
                validation_result["conflicts"].append({
                    "type": "guest",
                    "name": guest_exists.get("guest_name", "Unknown"),
                    "phone": phone,
                    "collection": "guests"
                })
            
            # Check staff collection  
            staff_exists = self.mongo_db['staff_profiles'].find_one({"staff_phone": phone})
            if staff_exists:
                validation_result["conflicts"].append({
                    "type": "staff", 
                    "name": staff_exists.get("staff_name", "Unknown"),
                    "phone": phone,
                    "department": staff_exists.get("department", "Unknown"),
                    "collection": "staff_profiles"
                })
            
            # Determine if operation can proceed
            if validation_result["conflicts"]:
                validation_result["is_unique"] = False
                validation_result["can_proceed"] = False
                
                # Build conflict message
                conflict_details = []
                for conflict in validation_result["conflicts"]:
                    if conflict["type"] == "guest":
                        conflict_details.append(f"Guest: {conflict['name']} ({conflict['phone']})")
                    else:
                        conflict_details.append(f"Staff: {conflict['name']} ({conflict['phone']}) - {conflict['department']} dept")
                
                validation_result["message"] = f"❌ Phone number {phone} already registered:\n  • " + "\n  • ".join(conflict_details)
                validation_result["message"] += f"\n💡 Each phone number must be unique across guests and staff"
                
                if operation_type == "guest":
                    validation_result["message"] += f"\n💡 Use update_guest_profile if this is the same person, or use a different phone number"
                else:
                    validation_result["message"] += f"\n💡 Use update_staff_profile if this is the same person, or use a different phone number"
            else:
                validation_result["message"] = f"✅ Phone number {phone} is available for {operation_type} registration"
            
            return validation_result
            
        except Exception as e:
            return {
                "is_unique": False,
                "conflicts": [],
                "can_proceed": False,
                "message": f"❌ Error validating phone uniqueness: {e}"
            }

    def _parse_guest_information(self, guest_information: str, dialog_history: str = None) -> Dict[str, Any]:
        """Parse unstructured guest information using ADVANCED LLM with comprehensive dialog history intelligence"""
        # Add comprehensive dialog history context with advanced intelligence
        context_section = ""
        if dialog_history:
            context_section = f"""
RECENT CONVERSATION CONTEXT:
{dialog_history}

🧠 ADVANCED CONTEXT PROCESSING INSTRUCTIONS:

1. PRONOUN & REFERENCE RESOLUTION:
- "he", "she", "they" → Resolve to guests mentioned in conversation
- "his", "her", "their" → Resolve to guest's profile details
- "the guest", "this person" → Resolve to most recent guest discussion

2. WORKFLOW STATE MANAGEMENT:
- If guest profile creation started earlier, continue same guest
- If guest mentioned in booking context, use same guest information
- If updating existing guest, maintain continuity with previous data

3. PROGRESSIVE INFORMATION BUILDING:
- Combine current input with previous guest details
- Add new information to existing guest context
- Fill gaps using conversation history and context clues

4. PREFERENCE MEMORY:
- "He usually stays in Executive Suites" → Add room preference
- "She's vegetarian" → Add dietary preference
- "They prefer non-smoking rooms" → Add room preference
- "VIP guest with special needs" → Add service level and requirements

5. ERROR RECOVERY:
- If guest name missing, check conversation for guest mentions
- If phone number unclear, use context clues from conversation
- If profile details ambiguous, resolve using conversation history

6. INTENT DISAMBIGUATION:
- "Update the guest" → Use context to determine which guest
- "Add his preferences" → Apply to current guest being processed
- "Change her tier" → Apply to guest in context

7. CONVERSATION REPAIR:
- "Actually, his name is John, not James" → Correct guest name
- "No, she's Gold tier, not Silver" → Correct loyalty tier
- "Wrong phone number, use this one" → Correct phone number

8. MULTI-ENTITY RELATIONSHIP TRACKING:
- "Sarah's booking with breakfast preferences" → Track guest + preferences
- "VIP guest John with corporate rate" → Track guest type + rate preference

9. INTELLIGENT PREDICTIONS:
- If corporate email mentioned, predict business traveler preferences
- If family mentioned, predict family-friendly service preferences
- If VIP mentioned, predict premium service requirements
- If repeat guest, predict historical preferences

10. CONVERSATION ANALYTICS:
- Detect VIP status: "VIP guest", "platinum member" → Upgrade service level
- Detect special needs: "wheelchair access", "dietary restrictions" → Add requirements
- Detect preferences: "prefers quiet rooms", "likes city view" → Add preferences
- Detect relationships: "booking for my wife", "corporate client" → Track relationships
"""
        extraction_prompt = f"""You are LEON's Advanced Guest Intelligence Engine. Parse this guest information with comprehensive context awareness:

{context_section}
CURRENT INPUT: \"{guest_information}\"

Extract and return ONLY valid JSON with this ENHANCED structure:
{{
    "guest_name": "extracted full name",
    "guest_phone": "extracted phone number with + format",
    "email": "extracted email address",
    "nationality": "extracted nationality or country",
    "preferences": {{
        "room_type": [{{"value": "preferred room type", "last_updated": "ISO8601 timestamp"}}, ...],
        "room_view": [{{"value": "preferred view", "last_updated": "ISO8601 timestamp"}}, ...],
        "dietary": [{{"value": "dietary restriction", "last_updated": "ISO8601 timestamp"}}, ...],
        "smoking": [{{"value": "NON SMOKING", "last_updated": "ISO8601 timestamp"}}],
        "accessibility": [{{"value": "accessibility need", "last_updated": "ISO8601 timestamp"}}, ...],
        "bed_type": [{{"value": "bed type", "last_updated": "ISO8601 timestamp"}}, ...],
        "floor_preference": [{{"value": "floor preference", "last_updated": "ISO8601 timestamp"}}, ...],
        "amenities": [{{"value": "amenity", "last_updated": "ISO8601 timestamp"}}, ...],
        "other": [{{"value": "other preference", "last_updated": "ISO8601 timestamp"}}, ...]
    }},
    "loyalty_status": "extracted loyalty status if mentioned",
    "notes": "any additional notes or special information",
    "guest_type": "VIP/corporate/family/leisure/repeat or empty",
    "service_level": "standard/premium/VIP",
    "relationship_context": "individual/corporate/family/group or empty",
    "accessibility_needs": ["extracted accessibility requirements"],
    "communication_preferences": ["email/sms/phone/whatsapp"],
    "room_preferences": ["extracted room preferences like 'high floor', 'city view']",
    "dietary_restrictions": ["extracted dietary requirements"],
    "business_details": {{
        "company": "company name if corporate guest",
        "job_title": "job title if business traveler",
        "corporate_rate": "true/false if corporate rate applies"
    }},
    "family_details": {{
        "family_size": "number of family members if family guest",
        "children_ages": ["ages of children if family with kids"],
        "special_family_needs": ["family-specific requirements"]
    }},
    "workflow_state": "new/continuation/modification",
    "context_confidence": "high/medium/low",
    "context_summary": "brief summary of guest context from conversation"
}}

ADVANCED PROCESSING RULES:
- Always return each preference as a list of objects with 'value' and 'last_updated' (ISO8601 timestamp)
- For room_type, allow up to 3 slots in order of priority
- Use empty lists for missing categories
- Use ISO8601 format for timestamps
- Ensure phone format starts with + (e.g., +60123456789)
- Extract nationality from context (Malaysian, Singaporean, etc.)
- Identify preferences from context (executive level, high floor, etc.)
- If no phone provided, use empty string (will prompt for required field)
- Use conversation context to fill missing information
- Detect guest type from context (VIP mentions, corporate affiliations, family indicators)
- Assess service level based on guest type and loyalty status
- Determine relationship context from conversation
- Identify accessibility needs from context
- Extract communication preferences from context
- Separate room preferences from general preferences
- Identify dietary restrictions specifically
- Extract business details for corporate guests
- Extract family details for family guests
- Identify workflow state (new profile vs. continuing/modifying existing)
- Rate context confidence based on information completeness

CONTEXT EXAMPLES:
- "Create profile for John, he's our VIP guest" → guest_type: "VIP", service_level: "VIP"
- "Sarah from ABC Corp needs a profile" → guest_type: "corporate", business_details: {{"company": "ABC Corp"}}
- "Family of 4, kids aged 8 and 12" → guest_type: "family", family_details: {{"family_size": "4", "children_ages": ["8", "12"]}}
- "Vegetarian guest, wheelchair access needed" → dietary_restrictions: ["vegetarian"], accessibility_needs: ["wheelchair access"]
- "Regular guest, Gold member, prefers high floor city view" → guest_type: "repeat", loyalty_status: "Gold", room_preferences: ["high floor", "city view"]
- "Update his email address" → workflow_state: "modification"
"""
        try:
            response = self.llm.invoke([{"role": "user", "content": extraction_prompt}])
            response_content = response.content.strip()
            if response_content.startswith('```json'):
                response_content = response_content.replace('```json', '').replace('```', '').strip()
            elif response_content.startswith('```'):
                response_content = response_content.replace('```', '').strip()
            parsed_data = json.loads(response_content)
            # Ensure enhanced fields have defaults
            pref_cats = [
                "room_type", "room_view", "dietary", "smoking", "accessibility", "bed_type", "floor_preference", "amenities", "other"
            ]
            if "preferences" not in parsed_data:
                parsed_data["preferences"] = {}
            for cat in pref_cats:
                if cat not in parsed_data["preferences"]:
                    parsed_data["preferences"][cat] = []
            parsed_data.setdefault("guest_type", "")
            parsed_data.setdefault("service_level", "standard")
            parsed_data.setdefault("relationship_context", "")
            parsed_data.setdefault("accessibility_needs", [])
            parsed_data.setdefault("communication_preferences", [])
            parsed_data.setdefault("room_preferences", [])
            parsed_data.setdefault("dietary_restrictions", [])
            parsed_data.setdefault("business_details", {})
            parsed_data.setdefault("family_details", {})
            parsed_data.setdefault("workflow_state", "new")
            parsed_data.setdefault("context_confidence", "medium")
            parsed_data.setdefault("context_summary", "")
            return parsed_data
        except json.JSONDecodeError as e:
            print(f"LLM JSON parsing error: {e}")
            return self._create_empty_guest_template(guest_information)
        except Exception as e:
            print(f"LLM parsing error: {e}")
            return self._create_empty_guest_template(guest_information)
    
    def _create_empty_guest_template(self, guest_information: str) -> Dict[str, Any]:
        """Fallback empty template when LLM parsing fails"""
        return {
            "guest_name": "",
            "guest_phone": "",
            "email": "",
            "nationality": "",
            "preferences": {},
            "loyalty_status": "",
            "notes": guest_information
        }

# Create global instance for LEON to use
guest_manager = EnhancedGuestManager()

# Enhanced tool creation functions for LEON Orchestrator
@tool
def create_guest_profile_tool(guest_phone: str, guest_name: str, email: str = None, phone: str = None, 
                            nationality: str = None, preferences: Any = None) -> str:
    """Create new guest profile in Omni-PMS MongoDB collection with loyalty program initialization"""
    # Defensive: parse preferences if passed as a string
    if isinstance(preferences, str):
        try:
            preferences = json.loads(preferences)
        except Exception:
            return "❌ Preferences must be a valid dictionary or JSON object"
    return guest_manager.create_guest_profile(guest_phone, guest_name, email, phone, nationality, preferences)

@tool
def read_guest_profile_tool(guest_phone: str) -> str:
    """READ comprehensive guest profile data with loyalty status and communication history"""
    return guest_manager.read_guest_profile(guest_phone)

@tool
def update_guest_profile_tool(guest_phone: str, email: str = None, nationality: str = None, 
                            guest_name: str = None, phone: str = None, loyalty_status: str = None,
                            preferences: Any = None) -> str:
    """Update guest profile fields (flexible parameter handling)"""
    # Build update_fields dictionary from provided parameters
    update_fields = {}
    
    if email:
        update_fields['email'] = email
    if nationality:
        update_fields['nationality'] = nationality
    if guest_name:
        update_fields['guest_name'] = guest_name
    if phone:
        update_fields['phone'] = phone
    if loyalty_status:
        update_fields['loyalty_status'] = loyalty_status
    if preferences:
        # Defensive: parse preferences if passed as a string
        try:
            if isinstance(preferences, str):
                preferences = json.loads(preferences)
        except Exception:
            return "❌ Preferences must be a valid dictionary or JSON object"
        update_fields['preferences'] = preferences
    
    if not update_fields:
        return f"❌ No valid update fields provided for guest {guest_phone}"
    
    return guest_manager.update_guest_profile(guest_phone, update_fields)

@tool
def deactivate_guest_profile_tool(guest_phone: str, reason: str = "Guest request") -> str:
    """Deactivate (soft delete) a guest profile. If a delete is queried, the allowable operation is deactivate."""
    return guest_manager.delete_guest_profile(guest_phone, reason)

@tool
def reactivate_guest_profile_tool(guest_phone: str) -> str:
    """Reactivate a previously deactivated (inactive) guest profile. Only this tool can overwrite the status field to 'active'."""
    try:
        guest = guest_manager.mongo_db['guests'].find_one({"guest_phone": guest_phone})
        if not guest:
            return f"❌ Guest profile not found for {guest_phone}"
        if guest.get("status") == "active":
            return f"✅ Guest profile for {guest_phone} is already active."
        result = guest_manager.mongo_db['guests'].update_one(
            {"guest_phone": guest_phone},
            {"$set": {"status": "active"}, "$unset": {"deactivated_at": "", "deactivated_by": "", "deactivation_reason": ""}}
        )
        if result.modified_count > 0:
            return f"✅ Guest profile for {guest_phone} has been reactivated."
        else:
            return f"⚠️ No changes made to guest profile {guest_phone} (already active?)"
    except Exception as e:
        return f"❌ Error reactivating guest profile: {e}"

@tool
def read_guests_by_criteria_tool(name: str = None, email: str = None, nationality: str = None, 
                     loyalty_status: str = None, limit: int = 20) -> str:
    """READ guest profile data by criteria across Omni-PMS"""
    return guest_manager.read_guests_by_criteria(name, email, nationality, loyalty_status, limit)

@tool
def read_guest_profiles_tool(guest_phone: str = None, guest_name: str = None, limit: int = 10) -> str:
    """Read guest profiles by phone or name (compatible with booking manager interface)"""
    if guest_phone:
        return guest_manager.read_guest_profile(guest_phone)
    elif guest_name:
        return guest_manager.read_guests_by_criteria(name=guest_name, limit=limit)
    else:
        return guest_manager.read_guests_by_criteria(limit=limit)

# New PMS-specific tools
@tool
def update_loyalty_program(guest_phone: str, action: str, points: int = 0, new_tier: str = None) -> str:
    """UPDATE loyalty program for guests (earn_points, redeem_points, upgrade_tier, downgrade_tier, view_status)"""
    return guest_manager.manage_loyalty_program(guest_phone, action, points, new_tier)

@tool
def track_guest_communication_tool(guest_phone: str, communication_type: str, message: str, staff_member: str = None) -> str:
    """Track guest communication for relationship management"""
    return guest_manager.track_guest_communication(guest_phone, communication_type, message, staff_member)

@tool
def read_guest_analytics(date_from: str = None, date_to: str = None) -> str:
    """READ comprehensive guest analytics for management reporting"""
    return guest_manager.get_guest_analytics(date_from, date_to)

# ===== LANGCHAIN TOOLS FOR LEON ORCHESTRATOR =====

@tool
def read_guest_information_from_description(guest_information: str, dialog_history: str = None) -> str:
    """READ guest information by parsing natural language description
    
    Handles any format: "John Tan from Malaysia, +60123456789, john@example.com, prefers high floor city view rooms"
    
    Args:
        guest_information: Natural language guest information
        dialog_history: Recent conversation context for resolving pronouns (optional)
    
    Returns: Parsed guest details with validation
    """
    try:
        parsed_data = guest_manager._parse_guest_information(guest_information, dialog_history)
        
        result = f"👤 Parsed Guest Information:\n"
        result += f"  • Name: {parsed_data.get('guest_name', 'N/A')}\n"
        result += f"  • Phone: {parsed_data.get('guest_phone', 'N/A')}\n"
        result += f"  • Email: {parsed_data.get('email', 'N/A')}\n"
        result += f"  • Nationality: {parsed_data.get('nationality', 'N/A')}\n"
        result += f"  • Loyalty Status: {parsed_data.get('loyalty_status', 'Standard')}\n"
        
        # Preferences
        preferences = parsed_data.get('preferences', {})
        if preferences and isinstance(preferences, dict) and any(preferences.values()):
            result += f"  • Preferences:\n"
            for key, value in preferences.items():
                if value:
                    result += f"    - {key.replace('_', ' ').title()}: {value}\n"
        
        # Notes
        if parsed_data.get('notes'):
            result += f"  • Notes: {parsed_data.get('notes')}\n"
        
        # Validation status
        required_fields = ['guest_name', 'guest_phone']
        missing_fields = []
        
        for field in required_fields:
            if not parsed_data.get(field):
                missing_fields.append(field)
        
        if missing_fields:
            result += f"\n⚠️ Missing Required Fields: {', '.join(missing_fields)}\n"
            result += f"💡 Please provide complete name and phone number information"
        else:
            result += f"\n✅ All required fields extracted successfully"
            result += f"\n🎯 Ready for: create_guest_profile_tool"
    
        return result
        
    except Exception as e:
        return f"❌ Error parsing guest information: {str(e)}" 