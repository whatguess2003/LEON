#!/usr/bin/env python3
"""
Agent-Rule Compliant Policy Manager - MongoDB Only
Hotel-Specific Booking & Cancellation Policies | 100% Natural Keys | CRUD Validation

AGENT-RULE COMPLIANCE:
- Natural keys only: hotel_name from manager_context.json
- CRUD operations: CREATE, READ, UPDATE, DELETE
- Database validation: READ first, then validate parameters
- MongoDB ONLY: Policies are content/setup data, not computable data
- Single source of truth: manager_context.json for hotel_name
"""

import pymongo
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from difflib import get_close_matches
import re

# Load environment variables
load_dotenv()

class PolicyManager:
    def __init__(self):
        """Initialize Policy Manager"""
        self.mongo_client = None
        self.mongo_db = None
        
        # Initialize LLM for parsing unstructured policy information
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
        
        self._connect_database()
        self._ensure_collections()
    
    def _connect_database(self):
        """Connect to MongoDB only"""
        try:
            # MongoDB connection
            self.mongo_client = pymongo.MongoClient(os.getenv('MONGO_URL'))
            self.mongo_db = self.mongo_client['leon_hotel_memory']
            
            # Database connection established silently
        except Exception as e:
            print(f"❌ MongoDB connection error: {e}")
    
    def _ensure_collections(self):
        """Ensure policy collections exist with proper indexes"""
        try:
            policies_collection = self.mongo_db['booking_policies']
            
            # Create indexes for efficient querying
            policies_collection.create_index([("hotel_name", 1)])
            policies_collection.create_index([("policy_type", 1)])
            policies_collection.create_index([("status", 1)])
            policies_collection.create_index([("created_at", -1)])
            
            # Compound index for hotel + policy type queries
            policies_collection.create_index([("hotel_name", 1), ("policy_type", 1)])
            
        except Exception as e:
            print(f"⚠️ Collection setup warning: {e}")
    
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
    
    def read_available_hotels(self) -> str:
        """READ available hotels for policy management"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        
        try:
            # Get hotels from multiple collections
            hotels = set()
            
            # From room_encyclopedia
            room_hotels = list(self.mongo_db['room_encyclopedia'].find(
                {}, {"hotel_name": 1, "_id": 0}
            ))
            hotels.update([doc['hotel_name'] for doc in room_hotels])
            
            # From hotel_registry
            registry_hotels = list(self.mongo_db['hotel_registry'].find(
                {}, {"hotel_name": 1, "_id": 0}
            ))
            hotels.update([doc['hotel_name'] for doc in registry_hotels])
            
            # From manager_collection
            manager_hotels = list(self.mongo_db['manager_collection'].find(
                {}, {"hotel_name": 1, "_id": 0}
            ))
            hotels.update([doc['hotel_name'] for doc in manager_hotels])
            
            if not hotels:
                return "❌ No hotels found in system"
            
            result = f"📋 Available Hotels for Policy Management ({len(hotels)} found):\n"
            for hotel in sorted(hotels):
                result += f"  • {hotel}\n"
            
            result += f"\n💡 Usage: Use exact hotel_name in policy operations"
            return result
            
        except Exception as e:
            return f"❌ Error reading available hotels: {e}"
    
    def read_policies(self, policy_type: str = None) -> str:
        """READ policies with natural key validation using manager_context.json"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        
        try:
            # Get hotel context from manager_context.json
            context = self._get_hotel_context()
            hotel_name = context.get("hotel_name", "")
            
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
            
            policies_collection = self.mongo_db['booking_policies']
            
            # Build query
            query = {"hotel_name": hotel_name}
            if policy_type:
                query["policy_type"] = {"$regex": policy_type, "$options": "i"}
            
            policies = list(policies_collection.find(query, {"_id": 0}).sort("created_at", -1))
            
            if not policies:
                if policy_type:
                    return f"❌ No {policy_type} policies found for {hotel_name}"
                else:
                    return f"❌ No policies found for {hotel_name}"
            
            result = f"📋 Policies Found for {hotel_name} ({len(policies)} total):\n"
            
            for policy in policies:
                result += f"  • {policy['policy_type']} - {policy['status']}\n"
                result += f"    📝 {policy['description'][:100]}...\n"
                result += f"    📅 Created: {policy['created_at'].strftime('%Y-%m-%d')}\n"
            
            return result
            
        except Exception as e:
            return f"❌ Error reading policies: {e}"
    
    def create_policy(self, policy_type: str, policy_details: str, dialog_history: str = None) -> str:
        """CREATE policy with natural key validation using manager_context.json"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        
        try:
            # Get hotel context from manager_context.json
            context = self._get_hotel_context()
            hotel_name = context.get("hotel_name", "")
            
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
            
            
            # STEP 1: VALIDATE hotel exists in system
            hotel_exists = False
            
            # Check room_encyclopedia collection
            room_docs = list(self.mongo_db['room_encyclopedia'].find(
                {"hotel_name": hotel_name},
                {"hotel_name": 1, "_id": 0}
            ).limit(1))
            
            if room_docs:
                hotel_exists = True
            else:
                # Check hotel_registry collection
                registry_docs = list(self.mongo_db['hotel_registry'].find(
                    {"hotel_name": hotel_name},
                    {"hotel_name": 1, "_id": 0}
                ).limit(1))
                
                if registry_docs:
                    hotel_exists = True
                else:
                    # Check manager_collection
                    manager_docs = list(self.mongo_db['manager_collection'].find(
                        {"hotel_name": hotel_name},
                        {"hotel_name": 1, "_id": 0}
                    ).limit(1))
                    
                    if manager_docs:
                        hotel_exists = True
            
            if not hotel_exists:
                return f"❌ Hotel '{hotel_name}' not found in system. Please register hotel first."
            
            # STEP 2: PARSE policy details using LLM
 
            parsed_policy = self._parse_policy_details(policy_details, hotel_name, policy_type, dialog_history)
 
            
            # STEP 3: STORE policy in MongoDB
            policies_collection = self.mongo_db['booking_policies']
            
            manager_context = self._get_hotel_context()
            created_by = {
                "manager_name": manager_context.get("manager_name", "Unknown"),
                "manager_phone": manager_context.get("manager_phone", "Unknown")
            }
            policy_doc = {
                "hotel_name": hotel_name,
                "policy_type": policy_type,
                "description": parsed_policy['description'],
                "details": parsed_policy['details'],
                "status": "active",
                "created_at": datetime.now(),
                "created_by": created_by,
                "source": "policy_manager"
            }
            
 
            
            # Insert policy
            result = policies_collection.insert_one(policy_doc)
   
            
            result = f"✅ Policy created successfully for {hotel_name}:\n"
            result += f"  • Type: {policy_type}\n"
            result += f"  • Status: active\n"
            result += f"  • Description: {parsed_policy['description'][:100]}...\n"
            result += f"  • 🎯 Natural Keys: hotel_name from manager_context.json"
            
            return result
            
        except Exception as e:
            return f"❌ Error creating policy: {e}"
    
    def _parse_policy_details(self, policy_details: str, hotel_name: str, policy_type: str, dialog_history: str = None) -> Dict[str, str]:
        """Parse unstructured policy details using LLM with dialog history context"""
        try:
            # Add dialog history context if available
            context_section = ""
            if dialog_history:
                context_section = f"""
RECENT CONVERSATION CONTEXT:
{dialog_history}

Use this context to resolve pronouns and references (e.g., "it", "this policy", "that rule").
"""
            
            prompt = f"""
            Parse this hotel policy information into structured format.
            
            HOTEL: {hotel_name}
            POLICY TYPE: {policy_type}
            {context_section}
            CURRENT POLICY DETAILS: {policy_details}
            
            Extract and structure the policy information into:
            1. A clear description (2-3 sentences)
            2. Detailed policy rules and conditions
            
            Return ONLY a JSON object with this exact format:
            {{"description": "clear policy description", "details": "detailed policy rules"}}
            """
            
            response = self.llm.invoke(prompt)
            
            # Parse JSON response
            import json
            
            # Extract JSON from markdown code blocks if present
            content = response.content.strip()
            json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if json_match:
                content = json_match.group(1).strip()
            
            parsed = json.loads(content)
            
            return {
                "description": parsed.get("description", f"{policy_type} policy for {hotel_name}"),
                "details": parsed.get("details", policy_details)
            }
            
        except Exception as e:
            # Fallback to basic parsing
            return {
                "description": f"{policy_type} policy for {hotel_name}",
                "details": policy_details
            }
    
    def update_policy(self, policy_type: str, updates: str, dialog_history: str = None) -> str:
        """UPDATE policy with natural key validation using manager_context.json"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        
        try:
            # Get hotel context from manager_context.json
            context = self._get_hotel_context()
            hotel_name = context.get("hotel_name", "")
            
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
            
            # STEP 1: VALIDATE hotel exists in system
            hotel_exists = False
            
            # Check room_encyclopedia collection
            room_docs = list(self.mongo_db['room_encyclopedia'].find(
                {"hotel_name": hotel_name},
                {"hotel_name": 1, "_id": 0}
            ).limit(1))
            
            if room_docs:
                hotel_exists = True
            else:
                # Check hotel_registry collection
                registry_docs = list(self.mongo_db['hotel_registry'].find(
                    {"hotel_name": hotel_name},
                    {"hotel_name": 1, "_id": 0}
                ).limit(1))
                
                if registry_docs:
                    hotel_exists = True
                else:
                    # Check manager_collection
                    manager_docs = list(self.mongo_db['manager_collection'].find(
                        {"hotel_name": hotel_name},
                        {"hotel_name": 1, "_id": 0}
                    ).limit(1))
                    
                    if manager_docs:
                        hotel_exists = True
            
            if not hotel_exists:
                return f"❌ Hotel '{hotel_name}' not found in system"
            
            # STEP 2: FIND existing policy
            policies_collection = self.mongo_db['booking_policies']
            existing_policy = policies_collection.find_one({
                "hotel_name": hotel_name,
                "policy_type": {"$regex": policy_type, "$options": "i"}
            })
            
            if not existing_policy:
                return f"❌ No {policy_type} policy found for {hotel_name}"
            
            # STEP 3: PARSE updates using LLM
            parsed_updates = self._parse_policy_updates(updates, existing_policy, dialog_history)
            
            # STEP 4: UPDATE policy in MongoDB
            manager_context = self._get_hotel_context()
            updated_by = {
                "manager_name": manager_context.get("manager_name", "Unknown"),
                "manager_phone": manager_context.get("manager_phone", "Unknown")
            }
            update_data = {
                "description": parsed_updates['description'],
                "details": parsed_updates['details'],
                "updated_at": datetime.now(),
                "updated_by": updated_by
            }
            
            policies_collection.update_one(
                {"_id": existing_policy["_id"]},
                {"$set": update_data}
            )
            
            result = f"✅ Policy updated successfully for {hotel_name}:\n"
            result += f"  • Type: {policy_type}\n"
            result += f"  • Description: {parsed_updates['description'][:100]}...\n"
            result += f"  • 🎯 Natural Keys: hotel_name from manager_context.json"
            
            return result
            
        except Exception as e:
            return f"❌ Error updating policy: {e}"
    
    def _parse_policy_updates(self, updates: str, existing_policy: Dict, dialog_history: str = None) -> Dict[str, str]:
        """Parse policy updates using LLM with dialog history context"""
        try:
            # Add dialog history context if available
            context_section = ""
            if dialog_history:
                context_section = f"""
RECENT CONVERSATION CONTEXT:
{dialog_history}

Use this context to resolve pronouns and references (e.g., "it", "this policy", "that rule").
"""
            
            prompt = f"""
            Update this existing hotel policy with new information.
            
            EXISTING POLICY:
            Type: {existing_policy.get('policy_type', '')}
            Description: {existing_policy.get('description', '')}
            Details: {existing_policy.get('details', '')}
            
            {context_section}
            CURRENT UPDATES: {updates}
            
            Return ONLY a JSON object with updated description and details:
            {{"description": "updated description", "details": "updated details"}}
            """
            
            response = self.llm.invoke(prompt)
            
            # Parse JSON response
            import json
            
            # Extract JSON from markdown code blocks if present
            content = response.content.strip()
            json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if json_match:
                content = json_match.group(1).strip()
            
            parsed = json.loads(content)
            
            return {
                "description": parsed.get("description", existing_policy.get('description', '')),
                "details": parsed.get("details", existing_policy.get('details', ''))
            }
            
        except Exception as e:
            # Fallback to basic parsing
            return {
                "description": existing_policy.get('description', ''),
                "details": updates
            }
    
    def delete_policy(self, policy_type: str) -> str:
        """DELETE policy with natural key validation using manager_context.json"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        
        try:
            # Get hotel context from manager_context.json
            context = self._get_hotel_context()
            hotel_name = context.get("hotel_name", "")
            
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
            
            # STEP 1: VALIDATE hotel exists in system
            hotel_exists = False
            
            # Check room_encyclopedia collection
            room_docs = list(self.mongo_db['room_encyclopedia'].find(
                {"hotel_name": hotel_name},
                {"hotel_name": 1, "_id": 0}
            ).limit(1))
            
            if room_docs:
                hotel_exists = True
            else:
                # Check hotel_registry collection
                registry_docs = list(self.mongo_db['hotel_registry'].find(
                    {"hotel_name": hotel_name},
                    {"hotel_name": 1, "_id": 0}
                ).limit(1))
                
                if registry_docs:
                    hotel_exists = True
                else:
                    # Check manager_collection
                    manager_docs = list(self.mongo_db['manager_collection'].find(
                        {"hotel_name": hotel_name},
                        {"hotel_name": 1, "_id": 0}
                    ).limit(1))
                    
                    if manager_docs:
                        hotel_exists = True
            
            if not hotel_exists:
                return f"❌ Hotel '{hotel_name}' not found in system"
            
            # STEP 2: DELETE policy from MongoDB
            policies_collection = self.mongo_db['booking_policies']
            
            result = policies_collection.delete_one({
                "hotel_name": hotel_name,
                "policy_type": {"$regex": policy_type, "$options": "i"}
            })
            
            if result.deleted_count == 0:
                return f"❌ No {policy_type} policy found for {hotel_name}"
            
            return f"✅ Policy deleted successfully for {hotel_name}:\n  • Type: {policy_type}\n  • 🎯 Natural Keys: hotel_name from manager_context.json"
            
        except Exception as e:
            return f"❌ Error deleting policy: {e}"
    
    def calculate_booking_policy_impact(self, booking_details: str, dialog_history: str = None) -> str:
        """Calculate policy impact on booking with natural key validation using manager_context.json"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        
        try:
            # Get hotel context from manager_context.json
            context = self._get_hotel_context()
            hotel_name = context.get("hotel_name", "")
            
            if not hotel_name:
                return "❌ No hotel context found. Please ensure manager_context.json contains hotel_name"
            
            # STEP 1: VALIDATE hotel exists in system
            hotel_exists = False
            
            # Check room_encyclopedia collection
            room_docs = list(self.mongo_db['room_encyclopedia'].find(
                {"hotel_name": hotel_name},
                {"hotel_name": 1, "_id": 0}
            ).limit(1))
            
            if room_docs:
                hotel_exists = True
            else:
                # Check hotel_registry collection
                registry_docs = list(self.mongo_db['hotel_registry'].find(
                    {"hotel_name": hotel_name},
                    {"hotel_name": 1, "_id": 0}
                ).limit(1))
                
                if registry_docs:
                    hotel_exists = True
                else:
                    # Check manager_collection
                    manager_docs = list(self.mongo_db['manager_collection'].find(
                        {"hotel_name": hotel_name},
                        {"hotel_name": 1, "_id": 0}
                    ).limit(1))
                    
                    if manager_docs:
                        hotel_exists = True
            
            if not hotel_exists:
                return f"❌ Hotel '{hotel_name}' not found in system"
            
            # STEP 2: READ policies for this hotel
            policies_collection = self.mongo_db['booking_policies']
            policies = list(policies_collection.find({
                "hotel_name": hotel_name,
                "status": "active"
            }, {"_id": 0}))
            
            if not policies:
                return f"❌ No active policies found for {hotel_name}"
            
            # STEP 3: CALCULATE impact using LLM
            impact_result = self._calculate_policy_impact(booking_details, policies, hotel_name, dialog_history)
            
            return impact_result
            
        except Exception as e:
            return f"❌ Error calculating policy impact: {e}"
    
    def _calculate_policy_impact(self, booking_details: str, policies: List[Dict], hotel_name: str, dialog_history: str = None) -> str:
        """Calculate policy impact using LLM with dialog history context"""
        try:
            policies_text = "\n".join([
                f"- {p['policy_type']}: {p['description']}"
                for p in policies
            ])
            
            # Add dialog history context if available
            context_section = ""
            if dialog_history:
                context_section = f"""
RECENT CONVERSATION CONTEXT:
{dialog_history}

Use this context to resolve pronouns and references (e.g., "this booking", "that guest", "their stay").
"""
            
            prompt = f"""
            Calculate how hotel policies will impact this booking scenario.
            
            HOTEL: {hotel_name}
            {context_section}
            CURRENT BOOKING DETAILS: {booking_details}
            
            ACTIVE POLICIES:
            {policies_text}
            
            Analyze the impact and return a clear summary covering:
            1. Payment requirements and deadlines
            2. Cancellation terms and refund policies
            3. Any special conditions or restrictions
            4. Recommendations for the guest
            
            Format as a clear, professional response.
            """
            
            response = self.llm.invoke(prompt)
            return response.content
            
        except Exception as e:
            return f"❌ Error calculating policy impact: {e}"

# Create global instance for LEON to use
policy_manager = PolicyManager()

# STREAMLINED Agent-Rule Compliant Tool Functions
@tool
def read_available_hotels_tool() -> str:
    """READ available hotels for policy configuration"""
    return policy_manager.read_available_hotels()

@tool  
def read_policies_tool(policy_type: str = None) -> str:
    """READ all policies (hotel & payment) with natural key validation using manager_context.json"""
    return policy_manager.read_policies(policy_type)

@tool
def create_policy_tool(policy_type: str, policy_details: str, dialog_history: str = None) -> str:
    """CREATE complete hotel policy from natural language description using manager_context.json
    
    Examples:
    - "Flexible cancellation with 30% upfront payment, 7-day payment window, full refund if cancelled 24 hours before check-in"
    - "Strict policy: 50% deposit, non-refundable, payment due in 3 days, VIP guests get 72 hours"
    - "Moderate: 80% refund, 48h cancellation, send reminders at 24h and 6h, accept all payment methods"
    """
    return policy_manager.create_policy(policy_type, policy_details, dialog_history)

@tool  
def update_policy_tool(policy_type: str, updates: str, dialog_history: str = None) -> str:
    """UPDATE hotel policy from natural language description using manager_context.json
    
    Examples:  
    - "Change to strict cancellation with 50% refund"
    - "Update VIP payment deadline to 4 days, add crypto payments"
    - "Send reminders at 48h, 12h, and 2h"
    """
    return policy_manager.update_policy(policy_type, updates, dialog_history)

@tool
def delete_policy_tool(policy_type: str) -> str:
    """DELETE all policies for a hotel with CRUD validation using manager_context.json"""
    return policy_manager.delete_policy(policy_type)

@tool
def read_booking_policy_impact(booking_details: str, dialog_history: str = None) -> str:
    """READ policy impact analysis for booking scenarios with natural key validation using manager_context.json"""
    return policy_manager.calculate_booking_policy_impact(booking_details, dialog_history)

# ============================================================================
# SINGLE SOURCE OF TRUTH ARCHITECTURE SUMMARY:
# ============================================================================
# ✅ COMPLETED TRANSFORMATION:
#
# BEFORE (Hardcoded Parameters):
# ❌ create_policy_tool(hotel_name: str, policy_type: str, policy_details: str)
# ❌ update_policy_tool(hotel_name: str, policy_type: str, updates: str)
# ❌ delete_policy_tool(hotel_name: str, policy_type: str)
# ❌ read_policies_tool(hotel_name: str = None)
# ❌ calculate_booking_policy_impact_tool(hotel_name: str, booking_details: str)
#
# AFTER (Single Source of Truth):
# ✅ create_policy_tool(policy_type: str, policy_details: str) - hotel_name from manager_context.json
# ✅ update_policy_tool(policy_type: str, updates: str) - hotel_name from manager_context.json
# ✅ delete_policy_tool(policy_type: str) - hotel_name from manager_context.json
# ✅ read_policies_tool(policy_type: str = None) - hotel_name from manager_context.json
# ✅ calculate_booking_policy_impact_tool(booking_details: str) - hotel_name from manager_context.json
#
# BENEFITS:
# 🎯 Eliminated hardcoded hotel_name parameters from all tools
# 🎯 Single source of truth: manager_context.json for hotel_name
# 🎯 Simplified tool signatures and reduced parameter complexity
# 🎯 Consistent context-locked operations across all policy functions
# 🎯 Maintained full CRUD compliance and agent-rule alignment
# 🎯 Clean separation: tools focus on business logic, context handles hotel identity
# ============================================================================ 