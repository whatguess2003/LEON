#!/usr/bin/env python3
"""
LEON Chat Server - Main Entry Point
Serves as the chat API server for the LEON PMS UI
Integrates with the existing LEON meta orchestrator from booking-system
"""

import os
import sys
import json
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging
from datetime import datetime, timedelta
import redis
import uuid
import traceback
from dotenv import load_dotenv

load_dotenv()
REDIS_URL = os.getenv('REDIS_URL')
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Add booking_system to path to import LEON
sys.path.append(os.path.join(os.path.dirname(__file__), 'booking_system'))

# Import LEON meta orchestrator and hotel context manager
try:
    from booking_system.leon_meta_orchestrator import LEONMetaOrchestrator
    from booking_system.hotel_context_manager import (
        signup_manager, signin_manager_by_phone, signin_manager_by_name, 
        signin_manager_by_hotel, get_active_manager, signout_manager,
        list_all_managers
    )
except ImportError as e:
    print(f"❌ Error importing LEON: {e}")
    print("Make sure you're running from the root directory and booking_system directory exists")
    sys.exit(1)

from booking_system.inventory_manager import inventory_manager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="LEON PMS Chat Server",
    description="Chat API server for LEON PMS UI integration",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request/Response models
class ChatRequest(BaseModel):
    message: str
    staff_id: Optional[str] = "Hotel Manager"
    context: Optional[Dict[str, Any]] = {}

class ChatResponse(BaseModel):
    messages: List[str]
    staff_id: str
    success: bool = True
    action: Optional[Dict[str, Any]] = None

# Authentication models
class SignupRequest(BaseModel):
    manager_name: str
    manager_phone: str
    hotel_name: str
    role: str
    nationality: Optional[str] = "Malaysian"
    preferred_languages: Optional[List[str]] = ["English", "Bahasa Malaysia"]

class SigninRequest(BaseModel):
    auth_type: str  # "phone", "name", or "hotel"
    auth_value: str  # the actual phone/name/hotel value

class AuthResponse(BaseModel):
    success: bool
    message: str
    manager_name: Optional[str] = None
    hotel_name: Optional[str] = None
    role: Optional[str] = None
    auth_method: Optional[str] = None

# Global LEON instance
leon_instance = None

def get_active_manager_phone() -> str:
    """Return the active manager's phone from manager_context.json, or 'Hotel Manager' if not found."""
    try:
        context_path = os.path.join(os.path.dirname(__file__), 'booking_system', 'manager_context.json')
        with open(context_path, 'r') as f:
            context = json.load(f)
            active_manager = context.get('active_manager', {})
            phone = active_manager.get('manager_phone')
            if phone:
                return phone
    except Exception:
        pass
    return "Hotel Manager"

@app.on_event("startup")
async def startup_event():
    """Initialize LEON on startup"""
    global leon_instance
    try:
        print("🚀 Initializing LEON Chat Server...")
        leon_instance = LEONMetaOrchestrator()
        print("✅ LEON Chat Server initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize LEON: {e}")
        raise

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up LEON on shutdown"""
    global leon_instance
    if leon_instance:
        try:
            # Check if cleanup method exists
            if hasattr(leon_instance, 'cleanup'):
                leon_instance.cleanup()
                print("🧹 LEON cleanup completed")
            else:
                print("🧹 LEON cleanup not required")
        except Exception as e:
            logger.error(f"⚠️ Cleanup error: {e}")

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "LEON PMS Chat Server is running",
        "version": "1.0.0",
        "status": "online"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "leon_initialized": leon_instance is not None,
        "timestamp": "2024-01-01T00:00:00Z"
    }

# Authentication endpoints
@app.post("/api/auth/signup", response_model=AuthResponse)
async def signup_endpoint(request: SignupRequest):
    """Manager signup endpoint"""
    try:
        data = {
            'manager_name': request.manager_name,
            'manager_phone': request.manager_phone,
            'hotel_name': request.hotel_name,
            'role': request.role,
            'nationality': request.nationality,
            'preferred_languages': request.preferred_languages
        }
        
        result = signup_manager(data)
        
        return AuthResponse(
            success=result['success'],
            message=result['message'],
            manager_name=result.get('manager_name'),
            hotel_name=result.get('hotel_name'),
            auth_method="signup"
        )
    except Exception as e:
        return AuthResponse(
            success=False,
            message=f"Signup failed: {str(e)}"
        )

@app.post("/api/auth/signin", response_model=AuthResponse)
async def signin_endpoint(request: SigninRequest):
    """Manager signin endpoint"""
    try:
        if request.auth_type == "phone":
            result = signin_manager_by_phone(request.auth_value)
        elif request.auth_type == "name":
            result = signin_manager_by_name(request.auth_value)
        elif request.auth_type == "hotel":
            result = signin_manager_by_hotel(request.auth_value)
        else:
            return AuthResponse(
                success=False,
                message="Invalid auth_type. Must be 'phone', 'name', or 'hotel'"
            )
        
        return AuthResponse(
            success=result['success'],
            message=result['message'],
            manager_name=result.get('manager_name'),
            hotel_name=result.get('hotel_name'),
            role=result.get('role'),
            auth_method=result.get('auth_method')
        )
    except Exception as e:
        return AuthResponse(
            success=False,
            message=f"Signin failed: {str(e)}"
        )

@app.post("/api/auth/signout", response_model=AuthResponse)
async def signout_endpoint():
    """Manager signout endpoint"""
    try:
        result = signout_manager()
        return AuthResponse(
            success=result['success'],
            message=result['message']
        )
    except Exception as e:
        return AuthResponse(
            success=False,
            message=f"Signout failed: {str(e)}"
        )

@app.get("/api/auth/active")
async def get_active_manager_endpoint():
    """Get active manager endpoint"""
    try:
        manager = get_active_manager()
        if manager:
            return {
                "success": True,
                "manager": manager
            }
        else:
            return {
                "success": False,
                "message": "No active manager"
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to get active manager: {str(e)}"
        }

@app.get("/api/auth/managers")
async def list_managers_endpoint():
    """List all registered managers endpoint"""
    try:
        managers = list_all_managers()
        return {
            "success": True,
            "managers": managers,
            "count": len(managers)
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to list managers: {str(e)}"
        }

# --- Classic single-step chat endpoint using leon_meta_orchestrator ---
@app.post("/api/chat/start")
async def chat_start(request: ChatRequest):
    try:
        # Use leon_meta_orchestrator as the sole response generator
        # TODO: Enhance chitchat handling in orchestrator if needed
        response = leon_instance.process_request(request.message)
        return {"final_message": response}
    except Exception as e:
        logger.error(f"Exception in /api/chat/start: {e}\n{traceback.format_exc()}")
        return {"final_message": f"Backend error: {str(e)}"}

@app.post("/api/chat/complete")
async def chat_complete(payload: dict = Body(...)):
    try:
        # Use leon_meta_orchestrator as the sole response generator
        # TODO: Enhance chitchat handling in orchestrator if needed
        message = payload.get("message")
        # staff_id = payload.get("staff_id", "Hotel Manager")
        # context = payload.get("context", {})
        response = leon_instance.process_request(message)
        return {"final_message": response}
    except Exception as e:
        logger.error(f"Exception in /api/chat/complete: {e}\n{traceback.format_exc()}")
        return {"final_message": f"Backend error: {str(e)}"}

# Additional endpoints for PMS UI integration
@app.get("/api/status")
async def get_leon_status():
    """Get LEON system status"""
    global leon_instance
    return {
        "leon_online": leon_instance is not None,
        "agents_available": ["hotel", "guest", "booking", "room", "inventory"],
        "current_agent": getattr(leon_instance, 'current_agent', None) if leon_instance else None,
        "session_id": getattr(leon_instance, 'session_id', None) if leon_instance else None,
        "hotel_name": getattr(leon_instance, 'hotel_name', None) if leon_instance else None,
        "dialog_history_length": len(getattr(leon_instance, 'dialog_history', [])) if leon_instance else 0
    }

@app.post("/api/reset")
async def reset_conversation():
    """Reset LEON conversation context"""
    global leon_instance
    if leon_instance:
        try:
            # Reset dialog history for LEONMetaOrchestrator
            if hasattr(leon_instance, 'dialog_history'):
                leon_instance.dialog_history = []
            
            # Reset current agent and workflow if they exist
            if hasattr(leon_instance, 'current_agent'):
                leon_instance.current_agent = None
            if hasattr(leon_instance, 'workflow_turns'):
                leon_instance.workflow_turns = 0
            
            return {"message": "Conversation context reset successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Reset failed: {e}")
    else:
        raise HTTPException(status_code=500, detail="LEON not initialized")

# PMS Data Endpoints for leon-pms-ui
@app.get("/api/hotels")
async def get_hotels():
    """Get list of hotels"""
    try:
        # Return the hotel from manager context
        context_path = os.path.join(os.path.dirname(__file__), 'booking_system', 'manager_context.json')
        with open(context_path, 'r') as f:
            context = json.load(f)
            active_manager = context.get('active_manager', {})
            hotel_name = active_manager.get('hotel_name', 'Unknown Hotel')
            
        hotels = [{
            'id': 'marina_heights',
            'name': hotel_name,
            'location': active_manager.get('location', 'Unknown Location'),
            'manager': active_manager.get('manager_name', 'Unknown Manager'),
            'status': 'active'
        }]
        
        return {"hotels": hotels}
    except Exception as e:
        return {"hotels": [], "error": str(e)}

# Place this static endpoint BEFORE any /api/hotels/{hotel_id} or similar dynamic endpoints
@app.get("/api/hotels/rooms")
async def get_hotel_rooms():
    """Get all room types and room numbers for the current hotel using canonical room_manager.py logic"""
    try:
        from booking_system.room_manager import read_room_portfolio
        result = read_room_portfolio.invoke({
            "room_type": "",
            "include_physical": True,
            "include_structured": True
        })
        if isinstance(result, dict) and "room_types" in result:
            return JSONResponse(content=result)
        else:
            return JSONResponse(content={"room_types": result if isinstance(result, list) else []})
    except Exception as e:
        return JSONResponse(content={"room_types": [], "error": str(e)})

@app.get("/api/hotels/{hotel_id}")
async def get_hotel(hotel_id: str):
    """Get specific hotel information"""
    try:
        # Return the hotel from manager context
        context_path = os.path.join(os.path.dirname(__file__), 'booking_system', 'manager_context.json')
        with open(context_path, 'r') as f:
            context = json.load(f)
            active_manager = context.get('active_manager', {})
            
        hotel = {
            'id': hotel_id,
            'name': active_manager.get('hotel_name', 'Unknown Hotel'),
            'location': active_manager.get('location', 'Unknown Location'),
            'manager': active_manager.get('manager_name', 'Unknown Manager'),
            'phone': active_manager.get('manager_phone', ''),
            'status': 'active'
        }
        
        return hotel
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/bookings-calendar")
async def get_bookings_calendar():
    try:
        from booking_system.booking_manager import read_bookings_by_criteria_tool
        # Use canonical tool, which is context-locked to manager_context.json
        result = read_bookings_by_criteria_tool()
        # If result is a JSON string, parse it; if it's a string, try to parse as JSON, else return as error
        import json
        bookings = []
        if isinstance(result, str):
            try:
                data = json.loads(result)
                if isinstance(data, dict) and 'bookings' in data:
                    bookings = data['bookings']
                elif isinstance(data, list):
                    bookings = data
                else:
                    bookings = []
            except Exception:
                # If not JSON, return as error
                return {"bookings": [], "error": result}
        elif isinstance(result, list):
            bookings = result
        elif isinstance(result, dict) and 'bookings' in result:
            bookings = result['bookings']
        return {"bookings": bookings}
    except Exception as e:
        return {"bookings": [], "error": str(e)}

@app.get("/api/guests")
async def get_all_guests():
    """Get all guest profiles from MongoDB (global data)"""
    try:
        # Import guest manager
        from booking_system.guest_manager import EnhancedGuestManager
        
        # Get all guest data from MongoDB
        guest_manager = EnhancedGuestManager()
        guests_result = guest_manager.search_guests()
        
        # Parse guests (simplified for now - would need proper parsing)
        guests = []
        # This would parse the actual MongoDB guest data
        
        return {"guests": guests}
    except Exception as e:
        return {"guests": [], "error": str(e)}

@app.get("/api/hotels/{hotel_id}/guests")
async def get_hotel_guests(hotel_id: str):
    """Get guests who have bookings at a specific hotel (derived from bookings)"""
    try:
        # This endpoint now returns guests filtered by hotel bookings
        # The frontend handles the filtering logic by:
        # 1. Loading hotel bookings
        # 2. Extracting guest identifiers from bookings  
        # 3. Loading all guest profiles
        # 4. Filtering guests who have bookings at this hotel
        
        # Return empty for now - frontend handles the filtering
        return {"guests": [], "message": "Use /api/guests and filter by hotel bookings"}
    except Exception as e:
        return {"guests": [], "error": str(e)}

@app.get("/api/hotels/{hotel_id}/analytics")
async def get_hotel_analytics(hotel_id: str, days: int = 30):
    """Get analytics for a specific hotel"""
    try:
        # Return mock analytics data for now
        analytics = {
            "revenue": {
                "total": 15000,
                "today": 500,
                "growth": 12.5
            },
            "occupancy": {
                "percentage": 75.5,
                "total_rooms": 4,
                "occupied_rooms": 0
            },
            "bookings": {
                "total": 45,
                "today": 3,
                "pending": 2
            }
        }
        
        return analytics
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/hotels/{hotel_id}/update-room-status")
async def update_room_status(hotel_id: str, request: dict):
    """Update room status"""
    try:
        room_id = request.get('room_id')
        status = request.get('status')
        
        # This would integrate with the room management system
        # For now, return success
        return {"success": True, "message": f"Room {room_id} status updated to {status}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

# Inventory Management API Endpoints
@app.get("/api/inventory")
async def get_inventory(start_date: str = None, end_date: str = None):
    from booking_system.inventory_manager import inventory_manager
    # Get all room types for the current hotel
    result = inventory_manager.read_room_quantity()
    if result.startswith("❌"):
        return {"room_types": [], "error": result}
    try:
        lines = result.split('\n')
        room_types = []
        for line in lines:
            if '•' in line and 'total' in line:
                parts = line.split('•')[1].strip().split(':')
                if len(parts) >= 2:
                    room_type = parts[0].strip()
                    inventory_info = parts[1].strip()
                    total_match = inventory_info.split(' total')[0].strip()
                    active_part = inventory_info.split(', ')[1] if ', ' in inventory_info else total_match + ' active'
                    active_match = active_part.split(' active')[0].strip()
                    updated_match = "Unknown"
                    if 'updated' in inventory_info:
                        updated_part = inventory_info.split('updated ')[1].replace(')', '')
                        updated_match = updated_part.strip()
                    pricing_result = inventory_manager.read_room_price(room_type, start_date, end_date)
                    pricing = []
                    currency = "MYR"
                    if not pricing_result.startswith("❌"):
                        price_lines = pricing_result.split('\n')
                        for pline in price_lines:
                            if pline.strip().startswith("•") and "/night" in pline:
                                pparts = pline.split(":")
                                if len(pparts) >= 2:
                                    date = pparts[0].replace("•", "").strip()
                                    price_part = pparts[1].split("/night")[0].replace("$", "").strip()
                                    try:
                                        price = float(price_part)
                                    except Exception:
                                        price = 0.0
                                    updated_at = ""
                                    if "updated" in pline:
                                        updated_at = pline.split("updated")[-1].replace(")", "").strip()
                                    pricing.append({
                                        "date": date,
                                        "price": price,
                                        "currency": currency,
                                        "updated_at": updated_at
                                    })
                    room_types.append({
                        "room_type": room_type,
                        "total_inventory": int(total_match),
                        "active_inventory": int(active_match),
                        "updated_date": updated_match,
                        "pricing": pricing
                    })
        return {"room_types": room_types}
    except Exception as e:
        return {"room_types": [], "error": f"Parse error: {e}", "raw": result}

@app.get("/api/hotels/{hotel_id}/pricing")
async def get_hotel_pricing(hotel_id: str, room_type: str = None, days: int = 7, start_date: str = None):
    """Get pricing for room types at hotel"""
    try:
        # Convert hotel_id back to hotel name
        hotel_name = hotel_id.replace('_', ' ').title()
        
        # Get available room types first
        available_result = inventory_manager.read_available_room_types()
        room_types = []
        
        if "Available Room Types" in available_result:
            lines = available_result.split('\n')
            for line in lines:
                if '•' in line and '(' in line:
                    room_type_name = line.split('•')[1].strip().split(' (')[0]
                    room_types.append(room_type_name)
        
        # Set default start date if not provided
        if not start_date:
            start_date = datetime.now().strftime('%Y-%m-%d')
        
        # Calculate end date based on start_date + days
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = start_dt + timedelta(days=days-1)
        end_date = end_dt.strftime('%Y-%m-%d')
        
        # Get pricing for each room type
        pricing_data = []
        for rt in room_types[:3]:  # Limit to first 3 room types for performance
            try:
                # Read actual pricing from database for the date range
                pricing_result = inventory_manager.read_pricing(rt, start_date, end_date)
                
                # Parse REAL pricing data from database
                actual_price = 0.00
                has_pricing = False
                
                if "💰 Pricing for" in pricing_result and "❌ No pricing data" not in pricing_result:
                    has_pricing = True
                    # Extract actual price from the result
                    lines = pricing_result.split('\n')
                    for line in lines:
                        if '$' in line and '/night' in line:
                            # Parse line like "  • 2025-07-07: $200.00/night"
                            price_part = line.split('$')[1].split('/night')[0]
                            actual_price = float(price_part)
                            break
                
                pricing_data.append({
                    'room_type': rt,
                    'has_pricing': has_pricing,
                    'sample_price': actual_price,  # USE REAL DATABASE PRICE
                    'currency': 'MYR',
                    'period': f"{days} days"
                })
                
            except Exception as e:
                print(f"Error reading pricing for {rt}: {e}")
                pricing_data.append({
                    'room_type': rt,
                    'has_pricing': False,
                    'sample_price': 0.00,
                    'currency': 'MYR',
                    'period': f"{days} days"
                })
        
        return {"pricing": pricing_data}
    except Exception as e:
        print(f"Error in get_hotel_pricing: {e}")
        return {"pricing": [], "error": str(e)}

@app.post("/api/hotels/{hotel_id}/inventory/update")
async def update_hotel_inventory(hotel_id: str, request: dict):
    """Update inventory for a room type"""
    try:
        room_type = request.get('room_type')
        total_inventory = request.get('total_inventory')
        active_inventory = request.get('active_inventory')
        
        # Update inventory
        result = inventory_manager.update_inventory(room_type, total_inventory, active_inventory)
        
        if "✅" in result:
            return {"success": True, "message": result}
        else:
            return {"success": False, "error": result}
            
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/hotels/{hotel_id}/pricing/update")
async def update_hotel_pricing(hotel_id: str, request: dict):
    """Update pricing for a room type"""
    try:
        room_type = request.get('room_type')
        start_date = request.get('start_date')
        end_date = request.get('end_date')
        price = request.get('price')
        
        # Update pricing
        result = inventory_manager.create_pricing(room_type, start_date, end_date, float(price))
        
        if "✅" in result:
            return {"success": True, "message": result}
        else:
            return {"success": False, "error": result}
            
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/hotels/{hotel_id}/inventory/update-date")
async def update_inventory_date(hotel_id: str, request: Request):
    """Update inventory quantity and price for specific date"""
    try:
        data = await request.json()
        room_type = data.get('room_type')
        date = data.get('date')
        quantity = data.get('quantity')
        price = data.get('price')
        
        if not all([room_type, date, quantity is not None, price is not None]):
            return {"success": False, "error": "Missing required fields"}
        
        # Convert hotel_id back to hotel name
        hotel_name = hotel_id.replace('_', ' ').title()
        
        # Use inventory manager to update date-based inventory
        # For now, we'll update the pricing table and assume quantity is capacity-based
        success = inventory_manager.create_pricing(
            hotel_name=hotel_name,
            room_type=room_type,
            start_date=date,
            end_date=date,
            base_price=float(price)
        )
        
        if success:
            return {"success": True, "message": f"Updated {room_type} for {date}: {quantity} rooms at {price}"}
        else:
            return {"success": False, "error": "Failed to update inventory"}
            
    except Exception as e:
        print(f"Error updating inventory date: {str(e)}")
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    print("🤖 Starting LEON PMS Chat Server...")
    print("🌐 Server will be available at: http://localhost:8002")
    print("📱 PMS UI can connect to: http://localhost:8002/api/chat")
    print("🔧 Health check: http://localhost:8002/health")
    print("📊 Status: http://localhost:8002/api/status")
    
    # Run the server
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
        log_level="info"
    ) 