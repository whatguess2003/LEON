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
import pymongo
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.tools import tool
import redis

# Always load .env from the project root (LEON/.env)
load_dotenv()


# Import tools from specialized managers
from .booking_manager import (
    list_bookable_room_types_tool,
    check_room_availability_tool,
    create_booking_tool,
    get_booking_details_tool,
    modify_booking_tool,
    cancel_booking_tool,
    search_bookings_tool,
    get_revenue_forecast_tool,
    analyze_booking_patterns_tool,
    add_booking_special_request_tool,
    send_booking_confirmation_tool,
    get_payment_status_for_booking_tool,
    update_booking_status_tool,
    list_bookings_with_special_requests_tool   
)


from .guest_manager import (
    create_guest_profile_tool,
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
    delete_room_number_tool,
    manage_room_description_tool,
    read_room_type_tool,
    read_room_types_by_criteria_tool,
    read_room_portfolio_tool,
    read_room_system_validation_tool,
    read_room_type_from_room_number_tool
)

from .inventory_manager import (
   
    manage_room_quantity,
    manage_room_price,
    read_inventory_information_from_description,
    check_room_pricing,
    read_nightly_inventory,
    read_nightly_inventory_range,
    read_available_room_types_tool 
)

from .hotel_info_manager import (
    read_property_templates,
    apply_property_templates
)

from .policy_manager import (
    read_available_hotels_tool,
    read_policies_tool,
    create_payment_policy,
    update_payment_policy,
    create_booking_policy,
    update_booking_policy,
    create_tax_rate,
    update_tax_rate,
    delete_policy_tool,
    calculate_policy_impact_tool,
)

from .front_desk_manager import (
    assign_room_number,
    read_assignment_dashboard,
    mark_room_out_of_order,
    mark_room_available,
    update_room_readiness_status,
    read_rooms_exceeding_clean_threshold,
    check_in_guest,
    check_out_guest,
    update_rooms_by_threshold_auto,
    read_room_assignment_tool,  
    read_room_assignments_for_booking_tool,
    read_maintenance_issue_analysis,
    read_room_readiness_status_tool,
    relocate_guest_tool,
    override_pricing,
    suggest_room_assignment,
    unassign_room_number,

)

from .staff_manager import (
    read_staff_profiles_tool,
    create_staff_profile_tool,
    update_staff_profile_tool,
    delete_staff_profile_tool,
    read_staff_by_department_tool,
    read_available_staff_tool,
    parse_staff_requirements_tool,
    parse_staff_skills_description_tool,
    analyze_staff_performance_query_tool,    
)

from .housekeeping_manager import (
    read_dirty_rooms,
    create_cleaning_task,
    update_cleaning_progress,
    read_housekeeping_analytics,
    read_staff_performance_report,
    read_operational_efficiency_metrics,
    read_room_current_cleaning,
    read_staff_current_cleaning_status
)
    
from .payment_manager import (
    read_payment_status_tool,
    record_payment_tool,
    record_refund_tool,
    read_payment_deadlines
)

from .maintenance_manager import (
    create_maintenance_request,
    update_maintenance_progress,
    read_maintenance_requests,
    read_scheduled_maintenance,
    update_scheduled_maintenance,
    setup_scheduled_maintenance
)
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

from .report_manager import (
    calculate_revenue_analytics,
    generate_occupancy_and_booking_analytics,
    generate_guest_analytics,
    generate_performance_dashboard,
    generate_financial_reconciliation,
    generate_operational_alerts,
    daily_manager_report,
    daily_forecast_report,
    weekly_pace_report,
    occupancy_revenue_analysis,
    channel_performance_report,
    read_revenue_analytics
)

from .web_search_tool import (
    web_search_tool
)

from .currency_utils import get_currency_from_jwt

# Orchestrator is independent - no manager context dependencies

@tool
def read_activity_logs_tool(hotel_name: str = "", limit: int = 10) -> dict:
    """READ recent activity logs (frontend POST API + agent tool executions) for a hotel.
    Params: hotel_name (required), limit (1-1000). Returns {"items": [{"ts","text"}]}"""
    try:
        from .leon_meta_orchestrator import orchestrator
        hotel_in = (hotel_name or '').strip().strip('"').strip("'")
        hotel = (hotel_in or getattr(orchestrator, 'hotel_name', '') or '').upper()
        if not hotel:
            return {"items": []}
        try:
            lim = max(1, min(int(limit or 10), 1000))
        except Exception:
            lim = 10
        return orchestrator.read_activity_logs_internal(hotel, lim)
    except Exception:
        return {"items": []}


@tool
def read_hotel_local_time_tool(hotel_name: str = "", jwt_token: str = "") -> dict:
    """READ current hotel-local datetime and IANA timezone.
    Returns {"hotel_name","timezone","now_local"}. Requires hotel timezone via JWT (param or session).
    """
    try:
        from .leon_meta_orchestrator import orchestrator
        import os, jwt
        from zoneinfo import ZoneInfo
        hotel_in = (hotel_name or '').strip().strip('"').strip("'")
        hotel = (hotel_in or getattr(orchestrator, 'hotel_name', '') or '').upper()
        if not hotel:
            return {"error": "❌ Missing hotel_name"}
        # Decode timezone strictly from JWT that the orchestrator stored during process_request
        jwt_token = (jwt_token or getattr(orchestrator, 'jwt_token', None))
        if not jwt_token:
            return {"error": "❌ Missing JWT for timezone"}
        try:
            JWT_SECRET = os.getenv('JWT_SECRET', 'dev-secret')
            payload = jwt.decode(jwt_token, JWT_SECRET, algorithms=['HS256'])
            tz = payload.get('hotel_timezone')
            if not tz:
                return {"error": "❌ Missing hotel_timezone in JWT"}
        except Exception as e:
            return {"error": f"❌ JWT decode failed: {e}"}
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
    import jwt, os
    if not jwt_token:
      
        return "❌ No JWT token provided"
    try:
        JWT_SECRET = os.getenv('JWT_SECRET', 'dev-secret')
        JWT_ALGO = 'HS256'
        payload = jwt.decode(jwt_token, JWT_SECRET, algorithms=[JWT_ALGO])
        hotel_name = payload.get('hotel_name')
  
        if hotel_name:
      
            return hotel_name
        else:
 
            return "❌ No hotel_name found in JWT"
    except Exception as e:
  
        return f"❌ Error decoding JWT: {str(e)}"

def get_manager_name_from_jwt(jwt_token: str) -> str:
    import jwt, os
    if not jwt_token:

        return "❌ No JWT token provided"
    try:
        JWT_SECRET = os.getenv('JWT_SECRET', 'dev-secret')
        payload = jwt.decode(jwt_token, JWT_SECRET, algorithms=['HS256'])
        manager_name = payload.get('manager_name')
 
        if manager_name:
            return manager_name
        else:
   
            return "❌ No manager_name found in JWT"
    except Exception as e:
        return f"❌ Error decoding JWT: {e}"

# Remove tool_input_mapper and related input_mapper usage from tool registration
class LEONMetaOrchestrator:
    def __init__(self):
        """Initialize LEON Meta Orchestrator"""
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
       
        # 3-Layer Context Architecture (minimal)
        self.dialog_history = []  # Last 10 turns
        self.hotel_name = None
        self.manager_name = None
        # Defer date context until JWT/hotel timezone is available
        self.date_context = {}
        
        # Redis setup
        self.redis_client = redis.from_url(os.getenv("REDIS_URL"))
        # UI metadata tracking removed

        # Register tools directly without input_mapper
        self.tools = [
            list_bookable_room_types_tool,
            check_room_availability_tool,
            create_booking_tool,
            get_booking_details_tool,
            modify_booking_tool,
            cancel_booking_tool,
            search_bookings_tool,
            get_revenue_forecast_tool,
            analyze_booking_patterns_tool,
            add_booking_special_request_tool,
            send_booking_confirmation_tool,
            get_payment_status_for_booking_tool,
            update_booking_status_tool,
            list_bookings_with_special_requests_tool,
            # Guest tools
            create_guest_profile_tool,
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
            delete_room_number_tool,
            manage_room_description_tool,
            read_room_type_tool,
            read_room_types_by_criteria_tool,
            read_room_portfolio_tool,
            read_room_system_validation_tool,
            read_room_type_from_room_number_tool,
            # Navigation tools
           
            # Hotel info tools (template-first only)
            read_property_templates,
            apply_property_templates,
            # Policy tools
            read_available_hotels_tool,
            read_policies_tool,
            create_payment_policy,
            create_booking_policy,
            create_tax_rate,
            update_payment_policy,
            update_booking_policy,
            update_tax_rate,
            delete_policy_tool,
            calculate_policy_impact_tool,
            # Front desk tools
            assign_room_number,
            read_assignment_dashboard,
            mark_room_out_of_order,
            mark_room_available,
            update_room_readiness_status,
            read_rooms_exceeding_clean_threshold,
            check_in_guest,
            check_out_guest,
            update_rooms_by_threshold_auto,
            read_room_assignment_tool,
            suggest_room_assignment,
            read_room_assignments_for_booking_tool,
            read_maintenance_issue_analysis,
            read_room_readiness_status_tool,
            relocate_guest_tool,
            override_pricing,
            unassign_room_number,
            # Staff tools
            read_staff_profiles_tool,
            create_staff_profile_tool,
            update_staff_profile_tool,
            delete_staff_profile_tool,
            read_staff_by_department_tool,
            read_available_staff_tool,
            parse_staff_requirements_tool,
            parse_staff_skills_description_tool,
            analyze_staff_performance_query_tool,

            # Housekeeping tools
            read_dirty_rooms,
            create_cleaning_task,
            update_cleaning_progress,
            read_housekeeping_analytics,
            read_staff_performance_report,
            read_operational_efficiency_metrics,
            read_room_current_cleaning,
            read_staff_current_cleaning_status,
            # Payment tools
            read_payment_status_tool,
            record_payment_tool,
            record_refund_tool,
            read_payment_deadlines,
            # Maintenance tools
            create_maintenance_request,
            update_maintenance_progress,
            read_maintenance_requests,
            read_scheduled_maintenance,
            update_scheduled_maintenance,
            setup_scheduled_maintenance,
            # Inventory tools
            manage_room_quantity,
            manage_room_price,
            read_inventory_information_from_description,
            check_room_pricing,
            read_nightly_inventory,
            read_nightly_inventory_range, 
            read_available_room_types_tool,
            # Concierge tools
            read_local_attractions,
            create_local_attraction,
            read_concierge_system_validation,
            read_local_restaurants,
            read_city_guide,
            read_local_shopping,
            read_local_events,
            read_transportation_info,
            # Report tools
            calculate_revenue_analytics,
            generate_occupancy_and_booking_analytics,
            generate_guest_analytics,
            generate_performance_dashboard,
            generate_financial_reconciliation,
            generate_operational_alerts,
            daily_manager_report,
            daily_forecast_report,
            weekly_pace_report,
            occupancy_revenue_analysis,
            channel_performance_report,
            read_revenue_analytics,            
            # Web search tool
            web_search_tool,
            # Activity tool
            read_activity_logs_tool,
            read_hotel_local_time_tool,
        ]
        # Remove agent_scratchpad and all TODO logic

    def _dialog_history_key(self):
        return f"dialog_history:{self.hotel_name}:{self.manager_name}"

    def load_dialog_history(self):
        key = self._dialog_history_key()
        if self.redis_client and self.hotel_name and self.manager_name:
            data = self.redis_client.get(key)
            if data:
                self.dialog_history = json.loads(data)
            else:
                self.dialog_history = []
        else:
            # Fallback to in-memory only
            if not hasattr(self, 'dialog_history'):
                self.dialog_history = []

    def save_dialog_history(self):
        key = self._dialog_history_key()
        if self.redis_client and self.hotel_name and self.manager_name:
            self.redis_client.set(key, json.dumps(self.dialog_history))

    def add_to_dialog_history(self, message: str):
        """Add message to dialog history (keep last 20 turns, store in Redis)"""
        self.dialog_history.append(message)
        if len(self.dialog_history) > 20:
            self.dialog_history = self.dialog_history[-20:]
        self.save_dialog_history()

    def _summarize_dialog_history(self, dialog_history: List[str], hotel_name: str) -> str:
        """Summarize dialog history with LLM, focusing on unfinished workflow, pending confirmations, and incomplete tasks."""
        try:
            MAX_TURNS = 20
            if len(dialog_history) > MAX_TURNS:
                dialog_history = dialog_history[-MAX_TURNS:]
            full_conversation = "\n".join(dialog_history)
            llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.1,
                openai_api_key=os.getenv('OPENAI_API_KEY')
            )
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
            summary = response.content.strip()
            return summary
        except Exception:
            return "\n".join(dialog_history)

    def process_request(self, message: str, jwt_token: str = None) -> str:
        """Process user message using act-only orchestrator (no TODO logic, normal LLM planning)."""
        if not jwt_token:
            return "❌ No JWT token provided. Please authenticate."
        try:
            # Store JWT for tools that need to decode timezone
            self.jwt_token = jwt_token
            # Ensure module-level reference used by tool wrappers points to this live instance
            try:
                import booking_system.leon_meta_orchestrator as _lmo
                _lmo.orchestrator = self
            except Exception:
                pass
            # Register per-hotel timezone strictly from JWT (no env fallback)
            try:
                get_currency_from_jwt(jwt_token)
            except Exception:
                pass
            self.hotel_name = get_hotel_name_from_jwt(jwt_token)
            if isinstance(self.hotel_name, str):
                self.hotel_name = self.hotel_name.upper()
            self.manager_name = get_manager_name_from_jwt(jwt_token)
            if isinstance(self.manager_name, str):
                self.manager_name = self.manager_name.upper()
            self.load_dialog_history()
            self.date_context = self._get_date_context()
            date_context_str = "\n".join([f"{k}: {v}" for k, v in self.date_context.items()])
            # Read recent activity logs for hotel to keep agent aware of changes done via frontend API and tools
            try:
                recent = self.read_activity_logs_internal(self.hotel_name, limit=10)
                items = []
                try:
                    if isinstance(recent, dict):
                        items = recent.get('items') or []
                except Exception:
                    items = []
                try:
                    activity_context_str = "\n".join([f"- {str(getattr(it,'get',None) and it.get('ts') or it.get('ts','') if isinstance(it, dict) else '')}: {str(it.get('text','') if isinstance(it, dict) else '')}" for it in (items or [])])
                except Exception:
                    activity_context_str = ""
            except Exception:
                activity_context_str = ""
            # Avoid injecting raw tool objects (which may contain braces) into the prompt to prevent template parsing errors
            # Filter only valid LangChain tools (avoid plain functions)
            try:
                tools = []
                for t in self.tools:
                    if hasattr(t, 'name'):
                        tools.append(t)
                if not tools:
                    return "❌ No tools available."
            except Exception:
                tools = []
                return "❌ No tools available."
            tool_names_str = ", ".join([getattr(t, 'name', str(t)) for t in tools])
            # Extract manager profile from JWT for agent context
            try:
                import jwt as _jwt, os as _os
                _secret = _os.getenv('JWT_SECRET','dev-secret')
                _payload = _jwt.decode(jwt_token, _secret, algorithms=['HS256'])
                _mgr_nationality = _payload.get('nationality') or ''
                _mgr_currency = _payload.get('hotel_currency') or ''
                _mgr_primary = _payload.get('primary_language') or ''
                _manager_profile_str = f"Manager profile: nationality={_mgr_nationality} | primary_language={_mgr_primary} | currency={_mgr_currency}"
            except Exception:
                _manager_profile_str = "Manager profile: (unavailable)"
            system_prompt = f"""You are LEON, a hotel management AI assistant, working for {self.hotel_name} (manager {self.manager_name}). You are talking to the manager{_manager_profile_str}. Converse in primary_language.
 
 MANDATORY TOOL-FIRST POLICY
 - You have access to the following tools: {tool_names_str}.
 - NEVER speculate or answer from memory for operational data (rooms, readiness, bookings, staff, housekeeping). If an action or data is requested, CALL THE RELEVANT TOOL.
 - If parameters are incomplete, ASK ONE short clarifying question, then CALL THE TOOL. Do NOT answer with assumptions.
 - If the user mentions entities (room numbers, ranges, room types) that may or may not exist, DO NOT claim existence. CALL A READ TOOL to verify, or directly CALL the manage tool and let the tool validate.
 
 STRICT RESPONSES
 - If a tool returns an error, show that error and STOP. Do not rephrase it as your own conclusion.
 - Keep text minimal; prioritize tool outputs.
 
 DATA NORMALIZATION
 - Normalize all string params to UPPERCASE before any tool call.
 - For all tool calls, always use hotel_name={self.hotel_name} and manager_name={self.manager_name}.
 
 Date context: {date_context_str}
 RECENT ACTIVITY (latest first):
 {activity_context_str}
 """
            # Build prompt with dialog_history (use MessagesPlaceholder to avoid brace parsing issues)
            history_msgs = []
            for i, msg in enumerate(self.dialog_history[-10:] if self.dialog_history else []):
                if i % 2 == 0:
                    history_msgs.append(HumanMessage(content=msg))
                else:
                    history_msgs.append(AIMessage(content=msg))
            summarized_context = self._summarize_dialog_history(self.dialog_history, self.hotel_name)
            # Tools agent with enforced tool-first policy and embedded hotel/manager context
            tool_policy = (
                f"MANDATE: Always call a tool for operational data/actions."
                f" Always include \"hotel_name\": \"{self.hotel_name}\" and \"manager_name\": \"{self.manager_name}\" in tool inputs (UPPERCASE)."
            )
            # Escape any stray braces in system prompt to avoid template var collisions
            system_prompt_escaped = (system_prompt + "\n" + tool_policy).replace("{", "{{").replace("}", "}}")
            prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt_escaped),
                ("system", "SUMMARY OF UNFINISHED WORKFLOW:\n{summarized_context}"),
                MessagesPlaceholder(variable_name="chat_history"),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ])
            agent = create_openai_tools_agent(self.llm, tools, prompt)

            # Wrap tools to enforce UPPERCASE on all string inputs
            def _uppercase_tool_input(tool_call):
                try:
                    ti = getattr(tool_call, 'tool_input', None)
                    if isinstance(ti, dict):
                        for k, v in list(ti.items()):
                            if isinstance(v, str):
                                ti[k] = v.upper()
                            elif isinstance(v, list):
                                ti[k] = [x.upper() if isinstance(x, str) else x for x in v]
                            elif isinstance(v, dict):
                                ti[k] = {kk: (vv.upper() if isinstance(vv, str) else vv) for kk, vv in v.items()}
                    setattr(tool_call, 'tool_input', ti)
                except Exception:
                    pass
                return tool_call

            class UppercaseAgentExecutor(AgentExecutor):
                async def ainvoke(self, input, config=None, **kwargs):
                    result = await super().ainvoke(input, config=config, **kwargs)
                    return result
                def invoke(self, input, config=None, **kwargs):
                    res = super().invoke(input, config=config, **kwargs)
                    return res
                def _call(self, inputs):
                    out = super()._call(inputs)
                    return out

            executor = UppercaseAgentExecutor(
                agent=agent,
                tools=tools,
                verbose=True,
                handle_parsing_errors=True,
                max_iterations=20,
                return_intermediate_steps=True,
            )
            # Intercept tool calls to uppercase inputs by using callbacks on result steps
            result = executor.invoke({
                "input": message,
                "hotel_name": self.hotel_name,
                "chat_history": history_msgs,
                "summarized_context": summarized_context,
            })
            # Normalize executor result across langchain versions
            try:
                if isinstance(result, dict):
                    output_text = result.get('output', '')
                    steps_raw = result.get('intermediate_steps') or []
                else:
                    # AgentFinish or unexpected type
                    output_text = getattr(result, 'output', '') or str(result)
                    steps_raw = getattr(result, 'intermediate_steps', []) or []
            except Exception:
                output_text, steps_raw = '', []

            # Serialize intermediate steps to API-friendly JSON
            def _serialize_steps(steps):
                data = []
                try:
                    for pair in steps or []:
                        try:
                            action, observation = pair
                        except Exception:
                            action, observation = pair, None
                        try:
                            _uppercase_tool_input(action)
                        except Exception:
                            pass
                        entry = {
                            "tool": getattr(action, "tool", None),
                            "tool_input": getattr(action, "tool_input", None),
                            "log": getattr(action, "log", None),
                            "observation": observation,
                        }
                        data.append(entry)
                except Exception:
                    pass
                return data
 
            # Agent tool execution audit push
            try:
                steps = steps_raw or []
                # Expose a simple flag for external APIs (e.g., chat widget refresh)
                try:
                    self.last_tools_used = True if steps else False
                except Exception:
                    pass
                # Build a human-readable tool trace for frontend/dev panels
                try:
                    trace_lines = []
                    import json as _json
                    for pair in steps or []:
                        try:
                            action, _obs = pair
                        except Exception:
                            action, _obs = pair, None
                        t = str(getattr(action, "tool", "") or "")
                        ti = getattr(action, "tool_input", None)
                        try:
                            ti_str = _json.dumps(ti, ensure_ascii=False)
                        except Exception:
                            ti_str = str(ti)
                        trace_lines.append(f"→ {t} {ti_str}")
                    self.last_tool_trace = "\n".join(trace_lines)
                except Exception:
                    self.last_tool_trace = ""
                if steps:
                    from main import record_activity
                    def _extract_summary(entry: dict) -> str:
                        t = str(entry.get('tool') or '')
                        ti = entry.get('tool_input') or {}
                        if isinstance(ti, dict):
                            bid = ti.get('booking_id') or ti.get('BOOKING_ID') or ''
                            rn = ti.get('room_number') or ti.get('ROOM_NUMBER') or ''
                            rt = ti.get('room_type') or ti.get('ROOM_TYPE') or ''
                            sd = ti.get('stay_date') or ti.get('STAY_DATE') or ''
                            parts = [p for p in [t, bid and f"booking={bid}", rn and f"room={rn}", rt and f"type={rt}", sd and f"date={sd}"] if p]
                            return ' | '.join(parts) or t
                        return str(t)
                    for pair in steps:
                        try:
                            action, _obs = pair
                            tool_name = str(getattr(action, "tool", "") or "")
                            name_lc = tool_name.lower()
                            # Skip non-mutating categories
                            skip_prefixes = (
                                "list",
                                "check",
                                "get",
                                "search",
                                "analyze",
                                "read",
                                "track",
                                "parse",
                                "process",
                                "calculate",
                                "suggest",
                                "generate",
                                "web_search",
                            )
                            skip_suffixes = ("_report", "_analysis")
                            if name_lc.startswith(skip_prefixes) or name_lc.endswith(skip_suffixes) or "web_search" in name_lc:
                                continue
                            entry = {"tool": tool_name, "tool_input": getattr(action, "tool_input", None)}
                            record_activity(_extract_summary(entry), hotel_name=self.hotel_name, manager_name=self.manager_name, actor="LEON")
                        except Exception:
                            pass
            except Exception:
                pass
 
            # Keep history minimal but store last request/summary
            self.dialog_history.append(message)
            self.dialog_history.append(output_text or "")
            if len(self.dialog_history) > 20:
                self.dialog_history = self.dialog_history[-20:]
            self.save_dialog_history()
 
            # Return final text (normal chat response)
            return str(output_text or "")
        except Exception as e:
            return f"❌ Error processing message: {str(e)}"


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

    def read_activity_logs_internal(self, hotel_name: str, limit: int = 100) -> dict:
        """READ consolidated activity logs for a hotel from MongoDB (falls back to empty).
        Returns { items: [ { text, hotel_name?, manager_name?, ts_utc?, timezone? } ] }
        """
        try:
            lim = max(1, min(int(limit or 100), 1000))
        except Exception:
            lim = 100
        items: List[dict] = []
        try:
            client = pymongo.MongoClient(os.getenv('MONGO_URL'))
            coll = client.get_database('leon_hotel_memory').get_collection('activity_logs')
            cur = coll.find({"hotel_name": str(hotel_name).upper()}, {"_id": 0}).sort("ts_utc", -1).limit(lim)
            items = list(cur)
            try:
                client.close()
            except Exception:
                pass
        except Exception:
            items = []
        return {"items": items}

    def write_activity_log_internal(self, item: dict) -> None:
        """WRITE a single activity item to MongoDB activity_logs collection.
        item: { text, hotel_name?, manager_name?, ts?, ts_utc?, timezone? }
        """
        try:
            client = pymongo.MongoClient(os.getenv('MONGO_URL'))
            coll = client.get_database('leon_hotel_memory').get_collection('activity_logs')
            # Prefer provided local 'ts'; keep ts_utc if provided for compatibility
            safe = {
                "text": str(item.get('text') or ''),
            }
            if item.get('ts'):
                safe['ts'] = str(item['ts'])
            if item.get('ts_utc'):
                safe['ts_utc'] = str(item['ts_utc'])
            if item.get('timezone'):
                safe['timezone'] = str(item['timezone'])
            if item.get('hotel_name'):
                safe['hotel_name'] = str(item['hotel_name']).upper()
            if item.get('manager_name'):
                safe['manager_name'] = str(item['manager_name']).upper()
            if item.get('actor'):
                safe['actor'] = str(item['actor']).upper()
            # If neither ts nor ts_utc provided, add current UTC as minimal fallback
            if 'ts' not in safe and 'ts_utc' not in safe:
                from datetime import datetime as _dt, timezone as _tz
                safe['ts_utc'] = _dt.now(_tz.utc).isoformat().replace('+00:00','Z')
            coll.insert_one(safe)
            try:
                client.close()
            except Exception:
                pass
        except Exception:
            try:
                import logging as _logging
                _logging.getLogger(__name__).warning("Failed to write activity log to MongoDB", exc_info=True)
            except Exception:
                pass

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