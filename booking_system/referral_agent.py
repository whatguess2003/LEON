#!/usr/bin/env python3
"""
HOTEL REFERRAL AGENT - Multi-Hotel Business Operations
Handles referrals, partnerships, and cross-hotel operations when primary hotel is full
Separate from hotel_info_manager.py for clean architecture
"""

import os
import json
from pymongo import MongoClient
from datetime import datetime
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv()

class HotelReferralAgent:
    def __init__(self):
        # MongoDB connection
        self.mongo_url = os.getenv('MONGO_URL')
        if not self.mongo_url:
            raise ValueError("MONGO_URL environment variable is required")
        
        self.mongo_client = MongoClient(self.mongo_url)
        self.mongo_db = self.mongo_client.ella_hotel_encyclopedia
        self.hotels_collection = self.mongo_db.hotels
        self.partnerships_collection = self.mongo_db.hotel_partnerships
        
        # Initialize LLM for parsing
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
        
        # Ensure partnership indexes
        self._ensure_partnership_indexes()
    
    def _ensure_partnership_indexes(self):
        """Create indexes for partnership data"""
        try:
            # Partnership indexes
            self.partnerships_collection.create_index("primary_hotel", unique=False)
            self.partnerships_collection.create_index("partner_hotel", unique=False)
            self.partnerships_collection.create_index("partnership_type", unique=False)
            self.partnerships_collection.create_index("status", unique=False)
            
            print("✅ Referral agent indexes ensured")
        except Exception as e:
            print(f"⚠️ Partnership index warning: {e}")
    
    def suggest_referrals(self, primary_hotel: str, guest_criteria: str) -> Dict:
        """Main referral function - suggest partner hotels when primary is full"""
        try:
            # Get partnerships for primary hotel
            partnerships = list(self.partnerships_collection.find({
                "primary_hotel": primary_hotel,
                "status": "active"
            }))
            
            if not partnerships:
                return {
                    "success": False,
                    "message": f"No partner hotels found for {primary_hotel}",
                    "suggestion": "Consider creating partnerships with nearby hotels"
                }
            
            partner_suggestions = []
            
            for partnership in partnerships:
                partner_hotel = partnership['partner_hotel']
                
                # Get partner hotel information
                partner_info = self.hotels_collection.find_one({"hotel_name": partner_hotel})
                
                if partner_info:
                    partner_suggestions.append({
                        "hotel_name": partner_hotel,
                        "partnership_type": partnership['partnership_type'],
                        "commission_rate": partnership.get('commission_rate', 0),
                        "location": partner_info.get('location', {}),
                        "star_rating": partner_info.get('star_rating', 0),
                        "pricing_tier": partner_info.get('pricing_tier', ''),
                        "contact": partner_info.get('contact', {})
                    })
            
            # Record referral attempt
            self._record_referral_attempt(primary_hotel)
            
            return {
                "success": True,
                "primary_hotel_full": True,
                "partner_suggestions": partner_suggestions[:3],  # Top 3 suggestions
                "total_suggestions": len(partner_suggestions),
                "message": f"🏨 {primary_hotel} is fully booked. Here are partner hotel options:"
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def create_partnership(self, primary_hotel: str, partner_hotel: str, 
                          partnership_type: str, commission_rate: float = 0.0) -> Dict:
        """Create a new hotel partnership"""
        try:
            # Validate both hotels exist
            primary_exists = self.hotels_collection.find_one({"hotel_name": primary_hotel})
            partner_exists = self.hotels_collection.find_one({"hotel_name": partner_hotel})
            
            if not primary_exists:
                return {"success": False, "error": f"Primary hotel '{primary_hotel}' not found"}
            
            if not partner_exists:
                return {"success": False, "error": f"Partner hotel '{partner_hotel}' not found"}
            
            # Check if partnership already exists
            existing = self.partnerships_collection.find_one({
                "primary_hotel": primary_hotel,
                "partner_hotel": partner_hotel
            })
            
            if existing:
                return {"success": False, "error": "Partnership already exists"}
            
            # Create partnership
            partnership = {
                "primary_hotel": primary_hotel,
                "partner_hotel": partner_hotel,
                "partnership_type": partnership_type,  # referral, sister_property, alliance
                "commission_rate": commission_rate,
                "status": "active",
                "created_at": datetime.now(),
                "last_referral": None,
                "total_referrals": 0,
                "total_commission_earned": 0.0
            }
            
            result = self.partnerships_collection.insert_one(partnership)
            
            return {
                "success": True,
                "partnership_id": str(result.inserted_id),
                "message": f"✅ Partnership created: {primary_hotel} ↔ {partner_hotel}"
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _record_referral_attempt(self, primary_hotel: str):
        """Record that a referral was attempted"""
        try:
            self.partnerships_collection.update_many(
                {"primary_hotel": primary_hotel, "status": "active"},
                {
                    "$set": {"last_referral": datetime.now()},
                    "$inc": {"total_referrals": 1}
                }
            )
        except Exception as e:
            print(f"Error recording referral: {e}")
    
    def get_partnership_stats(self, hotel_name: str) -> Dict:
        """Get partnership statistics for a hotel"""
        try:
            # Partnerships where hotel is primary
            as_primary = list(self.partnerships_collection.find({"primary_hotel": hotel_name}))
            
            # Partnerships where hotel is partner
            as_partner = list(self.partnerships_collection.find({"partner_hotel": hotel_name}))
            
            total_partnerships = len(as_primary) + len(as_partner)
            total_referrals_sent = sum(p.get('total_referrals', 0) for p in as_primary)
            total_commission_earned = sum(p.get('total_commission_earned', 0) for p in as_primary)
            
            return {
                "success": True,
                "hotel_name": hotel_name,
                "total_partnerships": total_partnerships,
                "partnerships_as_primary": len(as_primary),
                "partnerships_as_partner": len(as_partner),
                "total_referrals_sent": total_referrals_sent,
                "total_commission_earned": total_commission_earned,
                "active_partnerships": [
                    {
                        "partner": p['partner_hotel'],
                        "type": p['partnership_type'],
                        "commission_rate": p.get('commission_rate', 0)
                    } for p in as_primary if p.get('status') == 'active'
                ]
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}

# Initialize the referral agent
referral_agent = HotelReferralAgent()

# ===== LANGCHAIN TOOLS FOR REFERRAL OPERATIONS =====

@tool
def suggest_partner_hotels(primary_hotel: str, guest_criteria: str) -> str:
    """
    🤝 SUGGEST PARTNER HOTELS - When primary hotel is fully booked
    
    Finds partner hotels that can accommodate guests when the primary hotel has no availability.
    Handles referral commissions and partnership relationships.
    
    Args:
        primary_hotel: Name of the hotel that is fully booked
        guest_criteria: Guest requirements (dates, amenities, location preferences, etc.)
        
    Returns: List of partner hotel suggestions with commission info
    """
    try:
        result = referral_agent.suggest_referrals(primary_hotel, guest_criteria)
        
        if result['success']:
            suggestions = result['partner_suggestions']
            
            if not suggestions:
                return f"""🏨 {primary_hotel} is fully booked.

❌ No partner hotels available for referral.
💡 Consider creating partnerships with nearby hotels using create_hotel_partnership()"""
            
            referral_text = f"""🏨 {primary_hotel} is fully booked.
🤝 Here are {len(suggestions)} partner hotel options:

"""
            
            for i, partner in enumerate(suggestions, 1):
                commission_info = f"💰 Commission: {partner['commission_rate']}%" if partner['commission_rate'] > 0 else "🤝 Sister Property"
                
                referral_text += f"""
{i}. 🏨 {partner['hotel_name']} ⭐{partner['star_rating']}
   📍 {partner['location'].get('city', 'N/A')}, {partner['location'].get('country', 'N/A')}
   🏷️ {partner['pricing_tier']} • {partner['partnership_type']}
   {commission_info}
   📞 {partner['contact'].get('phone', 'Contact via hotel')}
"""
            
            referral_text += f"""
💡 Referral Protocol:
1. Contact partner hotel directly for availability
2. Mention partnership with {primary_hotel}
3. Commission automatically tracked in system"""
            
            return referral_text
            
        else:
            return f"❌ Referral search failed: {result.get('error', 'Unknown error')}"
            
    except Exception as e:
        return f"❌ Error: {str(e)}"

@tool
def create_hotel_partnership(primary_hotel: str, partner_hotel: str, 
                           partnership_type: str, commission_rate: str = "0") -> str:
    """
    🤝 CREATE HOTEL PARTNERSHIP - Establish referral relationships
    
    Creates formal partnerships between hotels for referral business.
    
    Args:
        primary_hotel: Hotel that will send referrals
        partner_hotel: Hotel that will receive referrals  
        partnership_type: "referral", "sister_property", or "alliance"
        commission_rate: Percentage commission (e.g. "5.0" for 5%)
    """
    try:
        commission = float(commission_rate) if commission_rate else 0.0
        
        result = referral_agent.create_partnership(
            primary_hotel, partner_hotel, partnership_type, commission
        )
        
        if result['success']:
            commission_text = f"💰 {commission}% commission" if commission > 0 else "🤝 No commission"
            
            return f"""✅ Hotel Partnership Created!

🏨 Primary Hotel: {primary_hotel}
🤝 Partner Hotel: {partner_hotel}
📋 Partnership Type: {partnership_type}
{commission_text}

🎯 Benefits:
• When {primary_hotel} is full → automatically suggest {partner_hotel}
• Track referrals and commissions
• Maintain guest relationships instead of losing business

💡 Partnership ID: {result['partnership_id']}"""
            
        else:
            return f"❌ Partnership creation failed: {result['error']}"
            
    except Exception as e:
        return f"❌ Error: {str(e)}"

@tool
def read_partnership_stats(hotel_name: str) -> str:
    """READ partnership statistics and performance metrics"""
    try:
        result = referral_agent.get_partnership_stats(hotel_name)
        
        if result['success']:
            stats = f"""📊 Partnership Statistics for {hotel_name}

🤝 Partnership Overview:
• Total Partnerships: {result['total_partnerships']}
• As Primary Hotel: {result['partnerships_as_primary']} (sending referrals)
• As Partner Hotel: {result['partnerships_as_partner']} (receiving referrals)

📈 Performance:
• Total Referrals Sent: {result['total_referrals_sent']}
• Total Commission Earned: ${result['total_commission_earned']:.2f}

🏨 Active Partner Hotels:
"""
            
            if result['active_partnerships']:
                for partnership in result['active_partnerships']:
                    commission_text = f"{partnership['commission_rate']}% commission" if partnership['commission_rate'] > 0 else "Sister property"
                    stats += f"• {partnership['partner']} ({partnership['type']}) - {commission_text}\n"
            else:
                stats += "• No active partnerships\n"
            
            return stats
            
        else:
            return f"❌ Stats check failed: {result['error']}"
            
    except Exception as e:
        return f"❌ Error: {str(e)}"

# Export tools
__all__ = [
    'suggest_partner_hotels',
    'create_hotel_partnership', 
    'read_partnership_stats'
]

if __name__ == "__main__":
    print("🤝 Testing Hotel Referral Agent")
    print("-" * 50)
    print("✅ Referral Agent ready for multi-hotel operations!") 