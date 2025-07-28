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
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain.agents import AgentExecutor, create_openai_tools_agent
from pathlib import Path

# Always load .env from the project root (LEON/.env)
load_dotenv()


# Import tools from specialized managers
from .booking_manager import (
    list_bookable_room_types_tool,
    check_room_availability_tool,
    create_booking_tool,
    get_booking_details_tool,
    modify_booking_tool,
    modify_booking_quantity_tool,
    cancel_booking_tool,
    search_bookings_tool,
    get_booking_analytics_tool,
    get_revenue_forecast_tool,
    analyze_booking_patterns_tool,
    add_booking_special_request_tool,
    send_booking_confirmation_tool,
    get_payment_status_for_booking_tool,
    update_booking_status_tool,
    create_group_booking_tool,
    add_to_waitlist_tool,
    show_booking_policy_tool,
    add_service_to_booking_tool,
    list_bookings_with_special_requests_tool,
    modify_stay_date_tool
)


from .guest_manager import (
    create_guest_profile_tool,
    read_guest_profile_tool,
    update_guest_profile_tool,
    deactivate_guest_profile_tool,
    read_guests_by_criteria_tool,
    read_guest_profiles_tool,
    update_loyalty_program,
    track_guest_communication_tool,
    read_guest_analytics,
    read_guest_information_from_description,
    reactivate_guest_profile_tool,
)

from .room_manager import (
    create_room_type_tool,
    delete_room_type_tool,
    add_room_number_tool,
    manage_room_number_tool,
    manage_room_description_tool,
    read_room_type_tool,
    read_room_types_by_criteria_tool,
    read_room_portfolio_tool,
    read_room_system_validation_tool,
)

from .inventory_manager import (
   
    manage_room_quantity,
    manage_room_price,
    read_inventory_information_from_description,
    check_room_pricing
)

from .hotel_info_manager import (
    create_hotel_keywords,
    read_hotel_keywords,
    delete_hotel_keywords_by_category
)

from .policy_manager import (
    read_available_hotels_tool,
    read_policies_tool,
    create_policy_tool,
    update_policy_tool,
    delete_policy_tool,
    read_booking_policy_impact
)

from .front_desk_manager import (
    
    update_room_assignment_auto,
    read_assignment_dashboard,
    update_room_out_of_order_status,
    update_room_available_status,
    update_room_readiness_status,
    read_rooms_exceeding_prepared_threshold,
    update_guest_check_in_status,
    update_guest_check_out_status,
    update_rooms_by_threshold_auto,
    read_room_assignment_tool,
    read_room_assignment_suggestions,
    read_maintenance_issue_analysis,
    read_room_readiness_status_tool,
    relocate_guest_tool
)

from .staff_manager import (
    read_staff_profiles_tool,
    create_staff_profile_tool,
    update_staff_profile_tool,
    delete_staff_profile_tool,
    read_staff_by_department_tool,
    read_available_staff_tool,
    read_staff_requirements_from_description,
    read_staff_skills_from_description,
    read_staff_performance_analysis
)

from .housekeeping_manager import (
    read_unprepared_rooms,
    create_preparation_task,
    read_preparation_tasks,
    update_preparation_progress,
    read_housekeeping_analytics,
    read_staff_performance_report,
    read_operational_efficiency_metrics
)

from .payment_manager import (
    read_payment_status_tool,
    update_payment_status_tool,
    read_payment_deadlines
)

from .maintenance_manager import (
    create_maintenance_request,
    update_maintenance_progress,
    read_maintenance_requests,
    read_maintenance_dashboard
)

from .report_manager import get_tools as get_report_tools

from .concierge_manager import (
    read_local_attractions,
    create_local_attraction,
    read_concierge_system_validation,
    read_local_restaurants,
    read_city_guide,
    read_local_shopping,
    read_local_events,
    read_transportation_info
)

from .llm_utils import canonical_key

# Orchestrator is independent - no manager context dependencies

class LEONMetaOrchestrator:
    def __init__(self):
        """Initialize LEON Meta Orchestrator"""
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
        
        # Initialize MongoDB for article storage
        self._initialize_mongodb()
        
        # 3-Layer Context Architecture (minimal)
        self.dialog_history = []  # Last 10 turns
        self.hotel_article = None  # Current hotel's summarized knowledge
        self.hotel_name = None
        self.manager_name = None
        self.country = None
        self.city = None
        self.date_context = self._get_date_context()
        
        # All available tools
        self.tools = [
            # Booking tools
            list_bookable_room_types_tool,
            check_room_availability_tool,
            create_booking_tool,
            get_booking_details_tool,
            modify_booking_tool,
            modify_booking_quantity_tool,
            cancel_booking_tool,
            search_bookings_tool,
            get_booking_analytics_tool,
            get_revenue_forecast_tool,
            analyze_booking_patterns_tool,
            add_booking_special_request_tool,
            send_booking_confirmation_tool,
            get_payment_status_for_booking_tool,
            update_booking_status_tool,
            create_group_booking_tool,
            add_to_waitlist_tool,
            show_booking_policy_tool,
            add_service_to_booking_tool,
            list_bookings_with_special_requests_tool,
            modify_stay_date_tool,
            # Guest tools
            create_guest_profile_tool,
            read_guest_profile_tool,
            update_guest_profile_tool,
            deactivate_guest_profile_tool,
            read_guests_by_criteria_tool,
            read_guest_profiles_tool,
            update_loyalty_program,
            track_guest_communication_tool,
            read_guest_analytics,
            read_guest_information_from_description,
            reactivate_guest_profile_tool,  
            # Room tools
            delete_room_type_tool,
            create_room_type_tool,
            add_room_number_tool,
            manage_room_number_tool,
            manage_room_description_tool,
            read_room_type_tool,
            read_room_types_by_criteria_tool,
            read_room_portfolio_tool,
            read_room_system_validation_tool,
  
            # Hotel info tools
            create_hotel_keywords,
            read_hotel_keywords,
            delete_hotel_keywords_by_category,
            
            # Policy tools
            read_available_hotels_tool,
            read_policies_tool,
            create_policy_tool,
            update_policy_tool,
            delete_policy_tool,
            read_booking_policy_impact,
            
            # Front desk tools
            update_room_assignment_auto,
            read_assignment_dashboard,
            update_room_out_of_order_status,
            update_room_available_status,
            update_room_readiness_status,
            read_rooms_exceeding_prepared_threshold,
            update_guest_check_in_status,
            update_guest_check_out_status,
            update_rooms_by_threshold_auto,
            read_room_assignment_tool,
            read_room_assignment_suggestions,
            read_maintenance_issue_analysis,
            read_room_readiness_status_tool,
            relocate_guest_tool,
            # Staff tools
            read_staff_profiles_tool,
            create_staff_profile_tool,
            update_staff_profile_tool,
            delete_staff_profile_tool,
            read_staff_by_department_tool,
            read_available_staff_tool,
            read_staff_requirements_from_description,
            read_staff_skills_from_description,
            read_staff_performance_analysis,

            # Housekeeping tools
            read_unprepared_rooms,
            create_preparation_task,
            read_preparation_tasks,
            update_preparation_progress,
            read_housekeeping_analytics,
            read_staff_performance_report,
            read_operational_efficiency_metrics,

            # Payment tools
            read_payment_status_tool,
            update_payment_status_tool,
            read_payment_deadlines,

            # Maintenance tools
            create_maintenance_request,
            update_maintenance_progress,
            read_maintenance_requests,
            read_maintenance_dashboard,

            # Inventory tools
            manage_room_quantity,
            manage_room_price,
            read_inventory_information_from_description,
            check_room_pricing,
            
            # Concierge tools
            read_local_attractions,
            create_local_attraction,
            read_concierge_system_validation,
            read_local_restaurants,
            read_city_guide,
            read_local_shopping,
            read_local_events,
            read_transportation_info
        ]
        
        # Create tool mapping
        self.tool_map = {tool.name: tool for tool in self.tools}
        
        # Add report_tools to the main tools list for the orchestrator
        report_tools = get_report_tools()
        self.tools += report_tools

        # --- Build tool descriptions for LLM context ---
        self.tool_descriptions = "\n".join([
            f"{tool.name}: {getattr(tool, 'description', tool.__doc__ or '').strip()}"
            for tool in self.tools
        ])

    def _initialize_mongodb(self):
        """Initialize MongoDB for article storage"""
        try:
            from pymongo import MongoClient
            mongo_url = os.getenv('MONGO_URL', 'mongodb://localhost:27017/')
            self.client = MongoClient(mongo_url)
            self.db = self.client['leon_hotel_memory']
            self.articles = self.db['hotel_articles']
            
            # Create indexes
            self.articles.create_index([("hotel_name", 1)])
            self.articles.create_index([("timestamp", -1)])
            
        except Exception as e:
            print(f"⚠️ MongoDB initialization failed: {e}")
            self.client = None
            self.db = None
            self.articles = None
    
    def _load_hotel_article(self, hotel_name: str):
        """Load hotel article into context"""
        if self.articles is None:
            return
        
        try:
            article = self.articles.find_one({"hotel_name": hotel_name})
            if article:
                self.hotel_article = article.get('summary', '')
                print(f"📚 Loaded hotel article for {hotel_name} ({len(self.hotel_article)} chars)")
            else:
                self.hotel_article = None
                print(f"📚 No hotel article found for {hotel_name}")
        except Exception as e:
            print(f"⚠️ Failed to load hotel article: {e}")
            self.hotel_article = None
    
    def _load_hotel_knowledge(self, hotel_name: str, dialog_history: str = None):
        """Load hotel knowledge: PRIORITIZE keywords first, then articles"""
        knowledge_loaded = False
        
        # PRIORITY 1: Load from keywords system
        try:
            from hotel_info_manager import hotel_info_tool
            keywords_result = hotel_info_tool.query_hotel_keywords("What information do you have about this hotel?", dialog_history)
            
            if keywords_result and not keywords_result.startswith("❌"):
                self.hotel_article = f"KEYWORDS: {keywords_result}"
                print(f"🔑 Loaded hotel keywords for {hotel_name} ({len(keywords_result)} chars)")
                knowledge_loaded = True
            else:
                print(f"🔑 No hotel keywords found for {hotel_name}")
                
        except Exception as e:
            print(f"⚠️ Failed to load hotel keywords: {e}")
        
        # PRIORITY 2: Load from articles system (fallback)
        if not knowledge_loaded:
            try:
                if self.articles is not None:
                    article = self.articles.find_one({"hotel_name": hotel_name})
                    if article:
                        self.hotel_article = f"ARTICLE: {article.get('summary', '')}"
                        print(f"📚 Loaded hotel article for {hotel_name} ({len(self.hotel_article)} chars)")
                        knowledge_loaded = True
                    else:
                        print(f"📚 No hotel article found for {hotel_name}")
            except Exception as e:
                print(f"⚠️ Failed to load hotel article: {e}")
        
        # No knowledge found
        if not knowledge_loaded:
            self.hotel_article = None
            print(f"❌ No hotel knowledge found for {hotel_name}")
    
    def _store_dual_hotel_information(self, information: str, hotel_name: str, dialog_history: str = None):
        """Store information in BOTH keywords and articles systems"""
        results = []
        
        # STORAGE 1: Store in keywords system
        try:
            from hotel_info_manager import hotel_info_tool
            keywords_result = hotel_info_tool.store_hotel_keywords(information, dialog_history)
            results.append(f"Keywords: {keywords_result}")
            print(f"🔑 Stored in keywords system: {keywords_result}")
        except Exception as e:
            results.append(f"Keywords: ❌ Failed - {e}")
            print(f"⚠️ Failed to store in keywords: {e}")
        
        # STORAGE 2: Store in articles system
        try:
            article_result = self.store_hotel_article(information, hotel_name)
            results.append(f"Article: {article_result.get('message', 'Stored successfully')}")
            print(f"📚 Stored in articles system: {article_result.get('message', 'Success')}")
        except Exception as e:
            results.append(f"Article: ❌ Failed - {e}")
            print(f"⚠️ Failed to store in articles: {e}")
        
        return results
    
    def get_full_context(self):
        """Get full context for LLM processing"""
        context = {
            "dialog_history": self.dialog_history[-10:] if self.dialog_history else [],
            "hotel_article": self.hotel_article,
            "hotel_name": self.hotel_name,
            "manager": {
                "manager_name": self.manager_name,
                "hotel_name": self.hotel_name,
                "country": self.country,
                "city": self.city
            },
        }
        return context
    
    def add_to_dialog_history(self, message: str):
        """Add message to dialog history (keep last 10 turns)"""
        self.dialog_history.append(message)
        # Keep only last 10 turns
        if len(self.dialog_history) > 10:
            self.dialog_history = self.dialog_history[-10:]
    
    def _summarize_dialog_history(self, dialog_history: List[str], hotel_name: str) -> str:
        """Summarize dialog history by extracting only CORE HOTEL KNOWLEDGE and filtering out operational data, optimized for token usage."""
        try:
            # Limit dialog history to last 10 turns
            MAX_TURNS = 10
            if len(dialog_history) > MAX_TURNS:
                dialog_history = dialog_history[-MAX_TURNS:]

            full_conversation = "\n".join(dialog_history)

            # Get existing summary for contradiction checking, truncate if too long
            existing_article = self.articles.find_one({"hotel_name": hotel_name})
            existing_summary = existing_article.get('summary', '') if existing_article else ''
            MAX_SUMMARY_CHARS = 1000
            if len(existing_summary) > MAX_SUMMARY_CHARS:
                existing_summary = existing_summary[:MAX_SUMMARY_CHARS] + "..."

            # Concise prompt
            if existing_summary:
                prompt = (
                    f"Extract only core hotel knowledge (permanent, factual info) from the conversation below, "
                    f"and resolve contradictions with the existing summary. Ignore operational data (bookings, guests, staff, payments, etc.).\n\n"
                    f"HOTEL: {hotel_name}\n"
                    f"EXISTING SUMMARY:\n{existing_summary}\n\n"
                    f"NEW CONVERSATION:\n{full_conversation}\n\n"
                    f"Return a clean, factual summary of core hotel knowledge only."
                )
            else:
                prompt = (
                    f"Extract only core hotel knowledge (permanent, factual info) from the conversation below. "
                    f"Ignore operational data (bookings, guests, staff, payments, etc.).\n\n"
                    f"HOTEL: {hotel_name}\n"
                    f"CONVERSATION:\n{full_conversation}\n\n"
                    f"Return a clean, factual summary of core hotel knowledge only."
                )

            response = self.llm.invoke(prompt)
            return response.content.strip()

        except Exception as e:
            print(f"⚠️ Dialog summarization failed: {e}")
            return full_conversation
    
    def store_dialog_as_article(self, hotel_name: str) -> Dict[str, Any]:
        """Store dialog history as summarized article and update context"""
        if self.articles is None:
            return {"status": "error", "message": "MongoDB not available"}
        
        if not self.dialog_history:
            return {"status": "error", "message": "No dialog history to store"}
        
        try:
            print(f"📄 Processing dialog history for {hotel_name}")
            
            # Summarize dialog history
            summary = self._summarize_dialog_history(self.dialog_history, hotel_name)
            
            # Store in MongoDB
            document = {
                "hotel_name": hotel_name,
                "dialog_history": self.dialog_history.copy(),
                "summary": summary,
                "timestamp": datetime.now(),
                "dialog_length": len(self.dialog_history),
                "summary_length": len(summary)
            }
            
            # Upsert by hotel name (one article per hotel)
            self.articles.replace_one(
                {"hotel_name": hotel_name},
                document,
                upsert=True
            )
            
            # Update context with new article
            self.hotel_article = summary
            
            print(f"✅ Stored dialog history as summarized article for {hotel_name}")
            
            return {
                "status": "success",
                "message": "Successfully stored dialog history as summarized article.",
                "hotel_name": hotel_name,
                "dialog_messages": len(self.dialog_history),
                "summary_length": len(summary)
            }
            
        except Exception as e:
            print(f"❌ Dialog storage failed: {e}")
            return {"status": "error", "message": f"Storage failed: {str(e)}"}
    
    def update_context_from_message(self, message: str):
        """Update context based on message content with dialog history support"""
        # Get current dialog history for context-aware processing
        # dialog_history = self.get_recent_dialog_history(5)  # Removed: not needed for single-turn
        # Don't automatically load - let LLM actively query when needed with dialog_history context
        # The tools will now receive dialog_history automatically when called
        pass
    
    def store_hotel_article(self, article_content: str, hotel_name: str) -> Dict[str, Any]:
        """Store hotel article directly in MongoDB"""
        if self.articles is None:
            return {"status": "error", "message": "MongoDB not available"}
        
        try:
            document = {
                "hotel_name": hotel_name,
                "summary": article_content,
                "timestamp": datetime.now(),
                "content_length": len(article_content),
                "type": "manual_article"
            }
            
            # Upsert by hotel name (one article per hotel)
            self.articles.replace_one(
                {"hotel_name": hotel_name},
                document,
                upsert=True
            )
            
            # Update context with new article
            self.hotel_article = article_content
            
            return {
                "status": "success",
                "message": f"Successfully stored article for {hotel_name}",
                "hotel_name": hotel_name,
                "content_length": len(article_content)
            }
            
        except Exception as e:
            return {"status": "error", "message": f"Storage failed: {str(e)}"}
    
    def query_hotel_article(self, query: str, hotel_name: str, dialog_history: str = None) -> str:
        """Query hotel article information with context"""
        # Use the updated function name
        keywords_result = read_hotel_keywords(query, dialog_history)
        
        response = f"""
        �� HOTEL INFORMATION: {hotel_name}
        
        📋 QUERY RESULT:
        {keywords_result}
        
        💬 Context: {len(self.dialog_history)} previous dialog turns
        """
        
        return response

    def process_request(self, message: str) -> str:
        """Process user message using act-only orchestrator (no planning)."""
        try:
            self.date_context = self._get_date_context()
            self.add_to_dialog_history(message)
            date_context_str = "\n".join([f"{k}: {v}" for k, v in self.date_context.items()])
            system_prompt = f"""You are LEON, a hotel management AI assistant.
ALL user-supplied string parameters (hotel_name, room_type, guest_phone, etc.) must be normalized to UPPERCASE before any lookup, validation, tool call, or storage. Always use UPPERCASE for all internal logic and display, regardless of how the user types them.
You have access to the following tools. Use them to fulfill the user's request.
Date context: {date_context_str}
"""
            prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                ("human", "{input}"),
                ("placeholder", "{agent_scratchpad}")
            ])
            agent = create_openai_tools_agent(self.llm, self.tools, prompt)
            executor = AgentExecutor(agent=agent, tools=self.tools, verbose=True, handle_parsing_errors=True, max_iterations=15)
            result = executor.invoke({"input": message})
            response = result.get('output', 'Unable to process request.')
            self.add_to_dialog_history(f"LEON: {response}")
            return response
        except Exception as e:
            return f"❌ Error processing message: {str(e)}"

    def _get_date_context(self):
        today = datetime.now()
        # Calculate this weekend (Saturday and Sunday)
        weekday = today.weekday()  # Monday=0, Sunday=6
        days_until_saturday = (5 - weekday) % 7
        days_until_sunday = (6 - weekday) % 7
        this_saturday = today + timedelta(days=days_until_saturday)
        this_sunday = today + timedelta(days=days_until_sunday)
        # If today is already Saturday or Sunday, treat as this weekend
        if weekday == 5:
            this_saturday = today
            this_sunday = today + timedelta(days=1)
        elif weekday == 6:
            this_saturday = today - timedelta(days=1)
            this_sunday = today
        # Next weekend (Saturday and Sunday after this weekend)
        next_saturday = this_saturday + timedelta(days=7)
        next_sunday = this_sunday + timedelta(days=7)
        return {
            'today': today.strftime('%Y-%m-%d'),
            'tomorrow': (today + timedelta(days=1)).strftime('%Y-%m-%d'),
            'next_week': (today + timedelta(days=7)).strftime('%Y-%m-%d'),
            'next_month': (today.replace(day=1) + timedelta(days=32)).replace(day=1).strftime('%Y-%m-%d'),
            'this_weekend': [this_saturday.strftime('%Y-%m-%d'), this_sunday.strftime('%Y-%m-%d')],
            'next_weekend': [next_saturday.strftime('%Y-%m-%d'), next_sunday.strftime('%Y-%m-%d')]
        }

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
    
    # Load manager context if available
    try:
        context_file = os.path.join('manager_context.json')
        if os.path.exists(context_file):
            with open(context_file, 'r') as f:
                context = json.load(f)
                # Extract from nested active_manager structure
                active_manager = context.get('active_manager', {})
                if active_manager:
                    orchestrator.hotel_name = active_manager.get('hotel_name', '')
                    orchestrator.manager_name = active_manager.get('manager_name', '')
                    orchestrator.country = active_manager.get('nationality', '')
                    orchestrator.city = active_manager.get('hotel_name', '')  # Use hotel name as city for now
                    
                    print(f"👤 Manager: {orchestrator.manager_name}")
                    print(f"📱 Phone: {active_manager.get('manager_phone', '')}")
                    print(f"🏨 Hotel: {orchestrator.hotel_name}")
                    print(f"💼 Role: {active_manager.get('role', '')}")
                    print(f"🌍 Nationality: {orchestrator.country}")
                    print(f"🗣️ Languages: {', '.join(active_manager.get('preferred_languages', []))}")
                    print("-" * 60)
                else:
                    print("⚠️ No active manager found in context")
    except Exception as e:
        print(f"⚠️ Manager context not loaded: {e}")
    
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
    
    result = orchestrator.store_hotel_article(test_article, "Marina Bay Hotel")
    print(f"Storage result: {result}")
    
    # Test query
    query_result = orchestrator.query_hotel_article("What awards has Marina Bay Hotel won?", "Marina Bay Hotel")
    print(f"Query result: {query_result}")

if __name__ == "__main__":
    interactive_dashboard() 