#!/usr/bin/env python3
"""
Agent-Rule Compliant Policy Manager - MongoDB Only
Hotel-Specific Booking & Cancellation Policies | 100% Natural Keys | CRUD Validation

AGENT-RULE COMPLIANCE:
- Natural keys only (hotel_name provided explicitly)
- CRUD operations: CREATE, READ, UPDATE, DELETE
- Database validation: READ first, then validate parameters
- MongoDB ONLY: Policies are content/setup data, not computable data
"""

import pymongo
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI
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

    # Timezone logic centralized in timezone_utils; use hotel_now_local
    
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
    
    def read_available_hotels(self, hotel_name: str) -> str:
        """READ available hotels for policy management"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        
        try:
            # Only show the hotel_name provided (enforce strict multi-tenancy)
            hotels = set()
            # Check if hotel exists in any collection
            exists = False
            for collection in ['room_encyclopedia', 'hotel_registry', 'manager_collection']:
                doc = self.mongo_db[collection].find_one({"hotel_name": hotel_name}, {"hotel_name": 1, "_id": 0})
                if doc:
                    exists = True
                    hotels.add(hotel_name)
            if not exists:
                return f"❌ Hotel '{hotel_name}' not found in system"
            result = f"📋 Available Hotels for Policy Management (1 found):\n  • {hotel_name}\n"
            result += f"\n💡 Usage: Use exact hotel_name in policy operations"
            return result
            
        except Exception as e:
            return f"❌ Error reading available hotels: {e}"
    
    def read_policies(self, hotel_name: str, policy_type: str = None) -> str:
        """READ policies for a hotel (natural keys)"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        
        try:
            if not hotel_name:
                return "❌ hotel_name is required"
            
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
                try:
                    created_str = policy.get('created_at').isoformat() if policy.get('created_at') and hasattr(policy.get('created_at'), 'isoformat') else policy.get('created_at')
                except Exception:
                    created_str = policy.get('created_at')
                result += f"    📅 Created: {created_str}\n"
            
            return result
            
        except Exception as e:
            return f"❌ Error reading policies: {e}"

    def read_policies_structured(self, hotel_name: str, policy_type: str = None) -> dict:
        """READ policies in structured template form (MongoDB). Returns {"items": [...]}"""
        items = []
        try:
            if self.mongo_db is None:
                return {"items": items}
            hotel = (hotel_name or "").upper()
            def _default_for(pt: str):
                if pt == 'payment':
                    return {
                        "payment_threshold_percent": 30,
                        "payment_window_days": 7,
                        "methods_allowed": ["CASH","CARD","BANK TRANSFER","OTA"],
                        "deadline_policy": "before_check_in",
                        "notes": ""
                    }
                if pt == 'booking':
                    return {
                        "checkin_time": "15:00",
                        "checkout_time": "12:00",
                        "max_stay_nights": 14,
                        "cancellation_policy": {"window_hours": 48, "penalty_nights": 1, "refund_percent": 50},
                        "no_show_policy": {"penalty_nights": 1},
                        "overbooking_policy": "do_not_overbook",
                        "notes": ""
                    }
                if pt == 'tax':
                    return {
                        "vat_percent": 6.0,
                        "service_charge_percent": 10.0,
                        "tourism_tax_per_night": 10.0,
                        "notes": ""
                    }
                return {}
            existing = {"payment": None, "booking": None, "tax": None}
            try:
                col = self.mongo_db['booking_policies']
                q = {"hotel_name": hotel}
                if policy_type:
                    q["policy_type"] = {"$regex": policy_type, "$options": "i"}
                for doc in col.find(q, {"_id": 0}).sort("created_at", -1):
                    try:
                        pt = str(doc.get('policy_type') or '').lower()
                        if pt in existing and existing[pt] is None:
                            existing[pt] = doc
                    except Exception:
                        continue
            except Exception:
                pass
            seq = ['payment', 'booking', 'tax'] if not policy_type else [str(policy_type).lower()]
            for pt in seq:
                base = _default_for(pt)
                doc = existing.get(pt)
                details = base
                updated_at = None
                if isinstance(doc, dict):
                    det = doc.get('details')
                    if isinstance(det, dict):
                        details = det
                    try:
                        updated_at = (doc.get('updated_at') or doc.get('created_at'))
                        if hasattr(updated_at, 'isoformat'):
                            updated_at = updated_at.isoformat()
                    except Exception:
                        updated_at = None
                items.append({
                    "policy_type": pt,
                    "details": details,
                    "exists": doc is not None,
                    "updated_at": updated_at
                })
            return {"items": items}
        except Exception:
            return {"items": items}
    
    def create_policy(self, policy_type: str, policy_details: str, hotel_name: str, manager_name: str) -> str:
        """CREATE policy for a hotel (natural keys)"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        try:
            if not hotel_name:
                return "❌ hotel_name is required"
            hotel_exists = False
            room_docs = list(self.mongo_db['room_encyclopedia'].find(
                {"hotel_name": hotel_name},
                {"hotel_name": 1, "_id": 0}
            ).limit(1))
            if room_docs:
                hotel_exists = True
            else:
                registry_docs = list(self.mongo_db['hotel_registry'].find(
                    {"hotel_name": hotel_name},
                    {"hotel_name": 1, "_id": 0}
                ).limit(1))
                if registry_docs:
                    hotel_exists = True
                else:
                    manager_docs = list(self.mongo_db['manager_collection'].find(
                        {"hotel_name": hotel_name},
                        {"hotel_name": 1, "_id": 0}
                    ).limit(1))
                    if manager_docs:
                        hotel_exists = True
            if not hotel_exists:
                return f"❌ Hotel '{hotel_name}' not found in system. Please register hotel first."
            # STEP 2: PARSE policy details using LLM should be at tool layer
            # parsed_policy = self._parse_policy_details(policy_details, hotel_name, policy_type, dialog_history)
            # Instead, expect structured policy_details as dict
            parsed_policy = policy_details if isinstance(policy_details, dict) else {"description": policy_details, "details": policy_details}
            policies_collection = self.mongo_db['booking_policies']
            created_by = {
                "manager_name": manager_name,
                "manager_phone": "Unknown"
            }
            # Normalize fields to avoid KeyError when description/details are omitted
            description_value = parsed_policy.get("description") if isinstance(parsed_policy, dict) else str(parsed_policy)
            if description_value is None:
                description_value = f"{policy_type} policy"
            details_value = parsed_policy.get("details") if isinstance(parsed_policy, dict) else parsed_policy
            if details_value is None:
                details_value = parsed_policy
            policy_doc = {
                "hotel_name": hotel_name,
                "policy_type": policy_type,
                "description": description_value,
                "details": details_value,
                "status": "active",
                "created_at": datetime.now(timezone.utc),
                "created_by": created_by,
                "source": "policy_manager"
            }
            result = policies_collection.insert_one(policy_doc)
            result = f"✅ Policy created successfully for {hotel_name}:\n"
            result += f"  • Type: {policy_type}\n"
            result += f"  • Status: active\n"
            result += f"  • Description: {description_value[:100]}...\n"
            result += f"  • 🎯 Natural Keys: hotel_name provided explicitly"
            return result
        except Exception as e:
            return f"❌ Error creating policy: {e}"

    def update_policy(self, policy_type: str, updates: str, hotel_name: str, manager_name: str) -> str:
        """UPDATE policy for a hotel (natural keys)"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        try:
            if not hotel_name:
                return "❌ hotel_name is required"
            hotel_exists = False
            room_docs = list(self.mongo_db['room_encyclopedia'].find(
                {"hotel_name": hotel_name},
                {"hotel_name": 1, "_id": 0}
            ).limit(1))
            if room_docs:
                hotel_exists = True
            else:
                registry_docs = list(self.mongo_db['hotel_registry'].find(
                    {"hotel_name": hotel_name},
                    {"hotel_name": 1, "_id": 0}
                ).limit(1))
                if registry_docs:
                    hotel_exists = True
                else:
                    manager_docs = list(self.mongo_db['manager_collection'].find(
                        {"hotel_name": hotel_name},
                        {"hotel_name": 1, "_id": 0}
                    ).limit(1))
                    if manager_docs:
                        hotel_exists = True
            if not hotel_exists:
                return f"❌ Hotel '{hotel_name}' not found in system"
            policies_collection = self.mongo_db['booking_policies']
            existing_policy = policies_collection.find_one({
                "hotel_name": hotel_name,
                "policy_type": {"$regex": policy_type, "$options": "i"}
            })
            if not existing_policy:
                return f"❌ No {policy_type} policy found for {hotel_name}"
            # STEP 3: PARSE updates using LLM should be at tool layer
            # parsed_updates = self._parse_policy_updates(updates, existing_policy, dialog_history)
            # Instead, expect structured updates as dict
            parsed_updates = updates if isinstance(updates, dict) else {"description": updates, "details": updates}
            updated_by = {
                "manager_name": manager_name,
                "manager_phone": "Unknown"
            }
            update_data = {
                "updated_at": datetime.now(timezone.utc),
                "updated_by": updated_by
            }
            if isinstance(parsed_updates, dict):
                if 'description' in parsed_updates:
                    update_data['description'] = parsed_updates['description']
                # If caller provides nested details explicitly, use it; otherwise treat the whole dict as details payload
                if 'details' in parsed_updates:
                    update_data['details'] = parsed_updates['details']
                else:
                    # Merge into details fully (overwrite) to reflect structured template updates
                    update_data['details'] = parsed_updates
            policies_collection.update_one(
                {"_id": existing_policy["_id"]},
                {"$set": update_data}
            )
            # Safe preview text (avoid KeyError when description absent)
            preview_desc = ""
            if isinstance(parsed_updates, dict):
                preview_desc = str(parsed_updates.get('description') or '')
            else:
                preview_desc = str(parsed_updates or '')
            result = f"✅ Policy updated successfully for {hotel_name}:\n"
            result += f"  • Type: {policy_type}\n"
            if preview_desc:
                result += f"  • Description: {preview_desc[:100]}...\n"
            result += f"  • 🎯 Natural Keys: hotel_name provided explicitly"
            return result
        except Exception as e:
            return f"❌ Error updating policy: {e}"

    def delete_policy(self, policy_type: str, hotel_name: str) -> str:
        """DELETE policy for a hotel (natural keys)"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        
        try:
            if not hotel_name:
                return "❌ hotel_name is required"
            
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
            
            return f"✅ Policy deleted successfully for {hotel_name}:\n  • Type: {policy_type}\n  • 🎯 Natural Keys: hotel_name provided explicitly"
            
        except Exception as e:
            return f"❌ Error deleting policy: {e}"
    
    
# Create global instance for LEON to use
policy_manager = PolicyManager()

# STREAMLINED Agent-Rule Compliant Tool Functions
# Remove all Pydantic BaseModel classes and tool wrappers that use them
# Refactor all tool wrappers to use explicit keyword arguments

@tool
def read_available_hotels_tool(hotel_name: str, manager_name: str) -> str:
    """READ available hotels for policy configuration. Requires hotel_name and manager_name."""
    return policy_manager.read_available_hotels(hotel_name)

@tool
def read_policies_tool(hotel_name: str, manager_name: str, policy_type: str = None) -> str:
    """READ all policies (hotel & payment) for a hotel. Requires hotel_name and manager_name."""
    return policy_manager.read_policies(hotel_name, policy_type)


@tool
def update_policy_tool(hotel_name: str, manager_name: str, policy_type: str, updates: str) -> str:
    """UPDATE a hotel policy. Preferred: pass structured updates dict matching the templates.

    Use the same field names as in create templates:
    - payment: { "payment_threshold_percent": 50, "methods_allowed": ["CARD","OTA"] }
    - booking: { "cancellation_policy": { "window_hours": 24, "penalty_nights": 1, "refund_percent": 0 } }
    - tax: { "vat_percent": 8.0 }
    """
    return policy_manager.update_policy(policy_type, updates, hotel_name, manager_name)

@tool
def delete_policy_tool(hotel_name: str, manager_name: str, policy_type: str) -> str:
    """DELETE all policies for a hotel. Requires hotel_name and manager_name."""
    return policy_manager.delete_policy(policy_type, hotel_name)

# --- Modular convenience tools with fixed policy_type and explicit templates ---

@tool
def create_payment_policy(hotel_name: str, manager_name: str, policy_details: dict) -> str:
    """CREATE payment policy. policy_details template:
    {
      "payment_threshold_percent": 30,
      "payment_window_days": 7,
      "methods_allowed": ["CASH","CARD","BANK TRANSFER","OTA"],
      "deadline_policy": "before_check_in",  // before_check_in | upon_booking | before_check_out
      "notes": "optional"
    }
    """
    return policy_manager.create_policy("payment", policy_details, hotel_name, manager_name)

@tool
def update_payment_policy(hotel_name: str, manager_name: str, updates: dict) -> str:
    """UPDATE payment policy. Provide partial fields from the payment template, e.g. {"payment_threshold_percent": 50} """
    return policy_manager.update_policy("payment", updates, hotel_name, manager_name)

@tool
def create_booking_policy(hotel_name: str, manager_name: str, policy_details: dict) -> str:
    """CREATE booking policy (check-in/out, cancellation, no-show, stay limits). policy_details template:
    {
      "checkin_time": "15:00",
      "checkout_time": "12:00",
      "max_stay_nights": 14,
      "cancellation_policy": { "window_hours": 48, "penalty_nights": 1, "refund_percent": 50 },
      "no_show_policy": { "penalty_nights": 1 },
      "overbooking_policy": "do_not_overbook",
      "notes": "optional"
    }
    """
    return policy_manager.create_policy("booking", policy_details, hotel_name, manager_name)

@tool
def update_booking_policy(hotel_name: str, manager_name: str, updates: dict) -> str:
    """UPDATE booking policy. Provide partial fields from the booking template, e.g. {"checkin_time": "14:00"} """
    return policy_manager.update_policy("booking", updates, hotel_name, manager_name)

@tool
def create_tax_rate(hotel_name: str, manager_name: str, policy_details: dict) -> str:
    """CREATE tax policy. policy_details template:
    {
      "vat_percent": 6.0,
      "service_charge_percent": 10.0,
      "tourism_tax_per_night": 10.0,
      "notes": "optional"
    }
    """
    return policy_manager.create_policy("tax", policy_details, hotel_name, manager_name)

@tool
def update_tax_rate(hotel_name: str, manager_name: str, updates: dict) -> str:
    """UPDATE tax policy. Provide partial fields from the tax template, e.g. {"vat_percent": 8.0} """
    return policy_manager.update_policy("tax", updates, hotel_name, manager_name)

@tool
def calculate_policy_impact_tool(booking_details: str, policies: list, hotel_name: str, dialog_history: str = None) -> None:
    """Calculate policy impact using LLM (subagent/tool level). Handles dialog_history and LLM prompt, prints summary directly."""
    from langchain_openai import ChatOpenAI
    import os
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.1,
        openai_api_key=os.getenv('OPENAI_API_KEY')
    )
    policies_text = "\n".join([
        f"- {p['policy_type']}: {p['description']}" for p in policies
    ])
    context_section = ""
    if dialog_history:
        context_section = f"""
RECENT CONVERSATION CONTEXT:
{dialog_history}

Use this context to resolve pronouns and references (e.g., 'this booking', 'that guest', 'their stay').
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
    response = llm.invoke(prompt)
    impact_summary = response.content.strip()
    print(impact_summary)

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
# AFTER:
# ✅ hotel_name passed explicitly by caller
#
# BENEFITS:
# 🎯 Eliminated hardcoded hotel_name parameters from all tools
# 🎯 Single source of truth: manager_context.json for hotel_name
# 🎯 Simplified tool signatures and reduced parameter complexity
# 🎯 Consistent context-locked operations across all policy functions
# 🎯 Maintained full CRUD compliance and agent-rule alignment
# 🎯 Clean separation: tools focus on business logic, context handles hotel identity
# ============================================================================ 