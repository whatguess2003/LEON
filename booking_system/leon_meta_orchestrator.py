#!/usr/bin/env python3
"""
LEON Meta Orchestrator - Main Storage and Tool Router
Handles article summarization and routes to specialized tools

ARCHITECTURE:
- Main Storage: Orchestrator stores summarized articles
- Tool Router: Routes to specialized tools (booking, guest, room, hotel_info)
- Summarization: Orchestrator handles article summarization
- Tools: Extract structured data only (no summarization)
"""

import os
import json
from datetime import datetime, timezone, timedelta
from typing import List
from dotenv import load_dotenv

# Always load .env from the project root (LEON/.env)
load_dotenv()

# Orchestrator is independent - no manager context dependencies

def read_hotel_local_time_tool(hotel_name: str = "", jwt_token: str = "") -> dict:
    """READ current hotel-local datetime and IANA timezone.
    Returns {"hotel_name","timezone","now_local"}. The timezone is hotel.timezone.
    """
    try:
        from .leon_meta_orchestrator import orchestrator
        from zoneinfo import ZoneInfo
        from booking_system.hotel_registry import hotel_timezone
        hotel_in = (hotel_name or '').strip().strip('"').strip("'")
        hotel = (hotel_in or getattr(orchestrator, 'hotel_name', '') or '').upper()
        if not hotel:
            return {"error": "❌ Missing hotel_name"}
        tz = hotel_timezone(hotel)
        if not tz:
            return {"error": "❌ Hotel " + hotel + " is not registered"}
        try:
            now_local = datetime.now(ZoneInfo(tz))
        except Exception as e:
            return {"error": f"❌ Invalid timezone: {e}"}
        return {"hotel_name": hotel, "timezone": tz, "now_local": now_local.isoformat()}
    except Exception as e:
        return {"error": f"❌ {e}"}

def get_hotel_name_from_jwt(jwt_token: str) -> str:
    """
    Extract the hotel_name from the provided JWT token. Returns the hotel_name string if present, otherwise an error message.
    For orchestrator/internal use only.
    """
    from booking_system.access import decode
    payload = decode(jwt_token)
    if not payload:
        return "❌ Invalid or expired session"
    return payload.get('hotel_name') or "❌ No hotel in session"

def get_manager_name_from_jwt(jwt_token: str) -> str:
    from booking_system.access import decode
    payload = decode(jwt_token)
    if not payload:
        return "❌ Invalid or expired session"
    return payload.get('user_name') or "❌ No user in session"

def get_desk_user_from_jwt(jwt_token: str) -> str:
    """The user_id that owns this desk's chat, definitions, personal reports and complaints."""
    from booking_system.access import decode
    payload = decode(jwt_token)
    if not payload:
        return "❌ Invalid or expired session"
    return payload.get('user_id') or "❌ No user in session"

# Remove tool_input_mapper and related input_mapper usage from tool registration
class LEONMetaOrchestrator:
    def __init__(self):
        """Initialize LEON Meta Orchestrator"""
        # 3-Layer Context Architecture (minimal)
        self.dialog_history = []  # Last 10 turns
        self.hotel_name = None
        self.manager_name = None
        # Defer date context until JWT/hotel timezone is available
        self.date_context = {}

    def dialog_line(self, entry, index):
        """One stored turn. A sender on the turn wins. Older turns stay paired by position."""
        if isinstance(entry, dict):
            sender = str(entry.get("sender") or "")
            text = str(entry.get("text") or "")
            if sender in ("user", "leon"):
                return sender, text
        return ("user" if index % 2 == 0 else "leon"), str(entry or "")

    def read_dialog(self, hotel, manager):
        """Load this desk's turns for this request. Nothing is kept on the instance."""
        from booking_system.runtime_state import read_turns
        if not hotel or not manager:
            return []
        return read_turns(hotel, manager)

    def write_dialog(self, hotel, manager, history):
        """Save this desk's turns for this hotel and manager."""
        from booking_system.runtime_state import write_turns
        if hotel and manager:
            write_turns(hotel, manager, history)

    def _summarize_dialog_history(self, dialog_history: List[str], hotel_name: str) -> str:
        """Summarize dialog history with LLM, focusing on unfinished workflow, pending confirmations, and incomplete tasks."""
        try:
            MAX_TURNS = 20
            if len(dialog_history) > MAX_TURNS:
                dialog_history = dialog_history[-MAX_TURNS:]
            full_conversation = "\n".join(dialog_history)
            from booking_system.llm_utils import haiku, model_text
            llm = haiku()
            summarize_prompt = f"""
You are a hotel management AI assistant. Summarize the following dialog history, focusing ONLY on:
- Unfinished workflows
- Pending confirmations
- Incomplete tasks or actions that require follow-up
- Any open questions or unresolved issues

Return a concise summary (max 5 bullet points) that will help the agent resume or complete the pending work for hotel: {hotel_name}.

DIALOG HISTORY:
{full_conversation}
"""
            response = llm.invoke([{"role": "user", "content": summarize_prompt}])
            summary = model_text(response)
            return summary
        except Exception:
            return "\n".join(dialog_history)

    def process_desk(self, message: str, jwt_token: str = None) -> dict:
        """One desk turn. The reply, the list the agent wrote, and the three pins."""
        empty = {"reply": "", "refresh": False, "screen": None, "pins": [], "pin_count": 0}
        if not jwt_token:
            empty["reply"] = "No JWT token provided. Please authenticate."
            return empty
        hotel = get_hotel_name_from_jwt(jwt_token)
        manager = get_desk_user_from_jwt(jwt_token)
        if not isinstance(hotel, str) or hotel.startswith("❌"):
            empty["reply"] = str(hotel)
            return empty
        if not isinstance(manager, str) or manager.startswith("❌"):
            empty["reply"] = str(manager)
            return empty
        hotel = hotel.upper()
        history = self.read_dialog(hotel, manager)
        from booking_system.leon_turn import desk_turn
        try:
            reply, acted, screen, pins = desk_turn(hotel, manager, message, history)
        except Exception as error:
            empty["reply"] = "Error processing message: " + str(error)
            return empty
        self.write_dialog(hotel, manager, (list(history) + [
            {"sender": "user", "text": str(message or "")},
            {"sender": "leon", "text": reply},
        ])[-20:])
        try:
            from booking_system.leon_turn import share_desk_chat
            share_desk_chat(hotel, manager)
        except Exception:
            pass
        self.last_tools_used = bool(acted)
        return {
            "reply": reply,
            "refresh": bool(acted),
            "screen": screen,
            "pins": pins["shown"],
            "pin_count": pins["count"],
        }

    def process_request(self, message: str, jwt_token: str = None) -> str:
        """One reply for the front desk. The hotel comes from this request, not from shared state."""
        return str(self.process_desk(message, jwt_token).get("reply") or "")


    def _get_date_context(self):
        now_local = datetime.now(timezone.utc)
        # Calculate this weekend (Saturday and Sunday) using hotel-local time
        weekday = now_local.weekday()  # Monday=0, Sunday=6
        days_until_saturday = (5 - weekday) % 7
        days_until_sunday = (6 - weekday) % 7
        this_saturday = now_local + timedelta(days=days_until_saturday)
        this_sunday = now_local + timedelta(days=days_until_sunday)
        # If today is already Saturday or Sunday, treat as this weekend
        if weekday == 5:
            this_saturday = now_local
            this_sunday = now_local + timedelta(days=1)
        elif weekday == 6:
            this_saturday = now_local - timedelta(days=1)
            this_sunday = now_local
        # Next weekend (Saturday and Sunday after this weekend)
        next_saturday = this_saturday + timedelta(days=7)
        next_sunday = this_sunday + timedelta(days=7)
        return {
            'today': now_local.date().isoformat(),
            'tomorrow': (now_local + timedelta(days=1)).date().isoformat(),
            'next_week': (now_local + timedelta(days=7)).date().isoformat(),
            'next_month': ((now_local.replace(day=1) + timedelta(days=32)).replace(day=1)).date().isoformat(),
            'this_weekend': [this_saturday.date().isoformat(), this_sunday.date().isoformat()],
            'next_weekend': [next_saturday.date().isoformat(), next_sunday.date().isoformat()]
        }

    def read_activity_logs_internal(self, hotel_id: str, limit: int = 100) -> dict:
        """The hotel's latest activity items, newest first: {items: [{ts, timezone, text, hotel_id, user_id, actor}]}."""
        from booking_system.content_store import client
        found = client["leon_hotel_memory"].get_collection("activity_logs").find({"hotel_id": hotel_id}, {"_id": 0})
        return {"items": list(found.sort("ts", -1).limit(max(1, min(int(limit or 100), 1000))))}

    def write_activity_log_internal(self, item: dict) -> None:
        """WRITE one activity item: {ts, timezone, text, hotel_id, user_id, actor}."""
        from booking_system.content_store import client
        from booking_system.hotel_registry import new_id
        client["leon_hotel_memory"].get_collection("activity_logs").insert_one(dict(item, _id=new_id("act")))

# Create global instance
orchestrator = LEONMetaOrchestrator()

def interactive_dashboard():
    """Interactive LEON Meta Orchestrator Dashboard"""
    print("🏨 LEON Meta Orchestrator - Interactive Dashboard")
    print("=" * 60)
    print("🤖 Welcome to LEON - Your comprehensive hotel management AI assistant")
    print("📚 Features: Hotel Management, Booking, Guest Services, Analytics, Reports")
    print("💬 Start chatting with LEON below (type 'quit' to exit)")
    print("=" * 60)
    
    # Deprecated manager_context.json removed. Use JWT via API layer for context during requests.
    
    # Interactive chat loop
    while True:
        try:
            # Get user input
            user_input = input("\n💬 You: ").strip()
            
            # Check for exit commands
            if user_input.lower() in ['quit', 'exit', 'q', 'bye']:
                print("👋 Thank you for using LEON! Goodbye!")
                break
            
            # Skip empty inputs
            if not user_input:
                continue
            
            # Show processing indicator
            print("🤖 LEON: Processing your request...")
            
            # Process with orchestrator
            response = orchestrator.process_request(user_input)
            
            # Display response
            print(f"🤖 LEON: {response}")
            
        except KeyboardInterrupt:
            print("\n👋 Thank you for using LEON! Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {str(e)}")
            print("Please try again or type 'quit' to exit.")

def test_orchestrator():
    """Test orchestrator functionality (for development)"""
    print("🧪 Testing LEON Meta Orchestrator")
    print("=" * 50)
    
    # Test article storage
    test_article = """
    Marina Bay Hotel Kuala Lumpur - 5-star luxury hotel.
    Contact: +60 3-2156-8888, reservations@marinabaykl.com
    Awards: Best Luxury Hotel 2022, Excellence in Service 2021
    Grand Ballroom capacity 500 guests
    Check-in: 3:00 PM, Check-out: 12:00 PM
    """
    
    # result = orchestrator.store_hotel_article(test_article, "Marina Bay Hotel") # Removed
    # print(f"Storage result: {result}") # Removed
    
    # Test query
    query_result = orchestrator.query_hotel_article("What awards has Marina Bay Hotel won?", "Marina Bay Hotel")
    print(f"Query result: {query_result}")

if __name__ == "__main__":
    interactive_dashboard() 
