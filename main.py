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
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Body, Query, Cookie, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging
from datetime import datetime, timedelta, timezone
import traceback
from dotenv import load_dotenv
import urllib.parse
import urllib.request
from fastapi import Request as _FastAPIRequest
from fastapi import Cookie as _Cookie
import jwt as _pyjwt
from starlette.concurrency import run_in_threadpool
import psycopg2
import psycopg2.extras
try:
    from indexes.mongo_index_manifest import ensure_mongo_indexes as _ensure_mongo_indexes
except Exception:
    _ensure_mongo_indexes = None
try:
    from openai import OpenAI as _OpenAI
except Exception:
    _OpenAI = None
def _jwt_payload(token: str):
    try:
        if not token:
            return None
        JWT_SECRET = os.getenv('JWT_SECRET', 'dev-secret')
        return _pyjwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except Exception:
        return None

def _get_tz_from_jwt(token: str) -> str:
    payload = _jwt_payload(token)
    if not payload:
        return None
    return payload.get('hotel_timezone')

def _hotel_from_jwt(token: str) -> str:
    payload = _jwt_payload(token)
    if not payload:
        return ""
    return str(payload.get('hotel_name') or "").strip().upper()
# ZoneInfo not used in server; hotel timezone is obtained from JWT and applied downstream

load_dotenv(override=True)
os.environ.setdefault("PGCONNECT_TIMEOUT", "10")
print("LEON process starting", flush=True)
REDIS_URL = os.getenv('REDIS_URL')
# Add booking_system to path to import LEON
sys.path.append(os.path.join(os.path.dirname(__file__), 'booking_system'))

# Import LEON meta orchestrator and hotel context manager
try:
    from booking_system.leon_meta_orchestrator import LEONMetaOrchestrator
    from booking_system.hotel_context_manager import (
        signup_manager, signin_manager_by_phone, signin_manager_by_name, 
        signin_manager_by_hotel, list_all_managers
    )
except ImportError as e:
    print(f"❌ Error importing LEON: {e}")
    print("Make sure you're running from the root directory and booking_system directory exists")
    sys.exit(1)

from booking_system.inventory_manager import inventory_manager
from booking_system.room_manager import room_manager
from navigation_agent_simple import fast_decide_navigation
from booking_system.housekeeping_manager import housekeeping_manager
from booking_system.staff_manager import staff_manager
from booking_system.hotel_info_manager import hotel_info_manager
from booking_system.policy_manager import policy_manager
from booking_system.maintenance_manager import maintenance_manager as _mm
import random as _random

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="LEON PMS Chat Server",
    description="Chat API server for LEON PMS UI integration",
    version="1.0.0"
)
# --- Daily Preventive Maintenance Runner (restored, 60s refresh) ---
import asyncio as _asyncio

async def _pm_daily_runner():
    while True:
        try:
            try:
                # Run PM generation across all hotels; safe if idempotent
                # Use threadpool per run to avoid tying up the loop; handle DB reconnects each iteration
                # Call with empty string to allow per-hotel processing; create fresh connection per call
                await run_in_threadpool(_mm.run_daily_pm_generation, "")
            except Exception as e:
                logger.error(f"PM runner error: {e}")
        except Exception:
            pass
        try:
            # Sleep with jitter between 300s and 600s
            _delay = int(_random.randint(300, 600))
            await _asyncio.sleep(_delay)
        except Exception:
            break

def setup_scheduled_maintenance(app_inst):
    try:
        loop = _asyncio.get_event_loop()
        loop.create_task(_pm_daily_runner())
    except Exception:
        pass

# --- Metadata caches ---
_COUNTRY_CODES_CACHE = None
_COUNTRY_CODES_CACHE_AT = None
_LANGUAGES_CACHE = None
_LANGUAGES_CACHE_AT = None


# Add CORS middleware (explicit origins required when credentials are used)
_origins_env = os.getenv('FRONTEND_ORIGINS', 'http://localhost:8080,http://127.0.0.1:8080')
_origins_list = [o.strip() for o in _origins_env.split(',') if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def _bearer_session(request: Request, call_next):
    authorization = request.headers.get("authorization", "")
    marker = "bearer "
    if authorization.lower().startswith(marker) and "leon_jwt" not in request.cookies:
        token = authorization[len(marker):].strip()
        if token:
            headers = [(key, value) for key, value in request.scope["headers"] if key.lower() != b"cookie"]
            existing = request.headers.get("cookie", "")
            cookie = (existing + "; leon_jwt=" + token) if existing else ("leon_jwt=" + token)
            headers.append((b"cookie", cookie.encode("latin-1")))
            request.scope["headers"] = headers
    return await call_next(request)

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
    password: str
    hotel_name: str
    role: str
    nationality: Optional[str] = "Malaysian"
    primary_language: str
    hotel_timezone: Optional[str] = None
    hotel_currency: Optional[str] = None

class SigninRequest(BaseModel):
    auth_type: str  # "phone", "name", or "hotel"
    auth_value: str  # the actual phone/name/hotel value
    password: str

class AuthResponse(BaseModel):
    success: bool
    message: str
    manager_name: Optional[str] = None
    hotel_name: Optional[str] = None
    role: Optional[str] = None
    auth_method: Optional[str] = None

# UI igation models
 

# UI Navigation models
 

# Global LEON instance
leon_instance = None

from typing import Optional as _Optional

def record_activity(text: str, hotel_name: _Optional[str] = None, manager_name: _Optional[str] = None, leon_jwt: _Optional[str] = None, actor: _Optional[str] = None):
    try:
        from datetime import datetime, timezone
        tz_str = None
        try:
            if leon_jwt:
                tz_str = _get_tz_from_jwt(leon_jwt)
        except Exception:
            pass
        # No fallback to deleted timezone_utils; rely solely on JWT for timezone
        # Local timestamp (hotel timezone preferred; fallback UTC only if tz unknown)
        try:
            if tz_str:
                from zoneinfo import ZoneInfo
                now_local = datetime.now(ZoneInfo(tz_str))
            else:
                now_local = datetime.now(timezone.utc)
        except Exception:
            now_local = datetime.now(timezone.utc)
        ts_local = now_local.isoformat()
        item = {"ts": ts_local, "text": text}
        if tz_str:
            item["timezone"] = tz_str
        if hotel_name:
            item["hotel_name"] = str(hotel_name)
        if manager_name:
            item["manager_name"] = str(manager_name)
        if actor:
            item["actor"] = str(actor).upper()
        # Persist via running instance if available; else module-level orchestrator
        try:
            global leon_instance
            if leon_instance and hasattr(leon_instance, 'write_activity_log_internal'):
                leon_instance.write_activity_log_internal(item)
            else:
                from booking_system.leon_meta_orchestrator import orchestrator as _module_orchestrator
                _module_orchestrator.write_activity_log_internal(item)
        except Exception:
            pass
        try:
            logger.info(f"ACTIVITY {ts_local} :: {text}")
        except Exception:
            pass
    except Exception:
        pass

@app.on_event("startup")
async def startup_event():
    """Initialize LEON on startup"""
    global leon_instance
    print("LEON server startup", flush=True)
    try:
        from setup_schema import setup_schema
        setup_schema()
        from booking_system.channel_reservations import ensure_tables
        ensure_tables()
        from booking_system.booking_manager import booking_manager
        booking_manager._ensure_postgres_connection()
        booking_manager._ensure_booking_channel()
    except Exception as e:
        logger.error("PostgreSQL schema setup failed: %s", e)
        traceback.print_exc()
    try:
        print("Initializing LEON Chat Server...", flush=True)
        leon_instance = LEONMetaOrchestrator()
        # Ensure booking_system tools referencing module-level 'orchestrator'
        # use the same runtime instance created here
        try:
            import booking_system.leon_meta_orchestrator as _lmo
            _lmo.orchestrator = leon_instance
        except Exception:
            pass
        print("LEON Chat Server initialized", flush=True)
    except Exception as e:
        logger.error("Failed to initialize LEON: %s", e)
    # Start background PM runner
    try:
        setup_scheduled_maintenance(app)
    except Exception:
        pass
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
        "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    }

# Ensure Mongo indexes at startup (idempotent)
@app.on_event("startup")
async def _startup_indexes():
    try:
        if _ensure_mongo_indexes:
            _ensure_mongo_indexes()
    except Exception:
        pass

@app.post("/api/maintenance/pm_run_now")
def api_pm_run_now(hotel_name: str = Query(""), leon_jwt: str = Cookie(None)):
    """Synchronous PM generation trigger. Called on navigating to maintenance page."""
    try:
        # Derive manager and hotel from JWT if needed
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m, get_hotel_name_from_jwt as _get_h
            mgr = _get_m(leon_jwt)
            if not isinstance(mgr, str) or mgr.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            if not hotel_name:
                h = _get_h(leon_jwt)
                if isinstance(h, str) and not h.startswith("❌"):
                    hotel_name = h
        except Exception:
            return JSONResponse(content={"error": "Invalid token"}, status_code=401)
        hotel = str(hotel_name or "").upper()
        if not hotel:
            return JSONResponse(content={"error": "Missing hotel_name"}, status_code=400)
        msg = _mm.run_daily_pm_generation(hotel)
        return JSONResponse(content={"result": msg})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/meta/country_codes")
async def get_country_codes():
    """Return consolidated international calling codes with flags (cached)."""
    global _COUNTRY_CODES_CACHE, _COUNTRY_CODES_CACHE_AT
    try:
        # Simple 6h cache
        if _COUNTRY_CODES_CACHE and _COUNTRY_CODES_CACHE_AT and (datetime.now(timezone.utc) - _COUNTRY_CODES_CACHE_AT).total_seconds() < 21600:
            return JSONResponse(content={"items": _COUNTRY_CODES_CACHE})
        # Fetch upstream datasets
        def _fetch_json(url: str):
            req = urllib.request.Request(url, headers={"User-Agent": "LEON/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                import json as _json
                return _json.loads(resp.read().decode("utf-8"))
        items = []
        # Primary source: annexare Countries repository (reliable)
        try:
            sources = [
                'https://raw.githubusercontent.com/annexare/Countries/master/data/countries.json',
                'https://cdn.jsdelivr.net/gh/annexare/Countries@master/data/countries.json',
                'https://unpkg.com/annexare/countries@2.5.0/data/countries.json',
            ]
            got = None
            for url in sources:
                try:
                    got = _fetch_json(url)
                    if isinstance(got, dict) and len(got) > 100:
                        break
                except Exception:
                    got = None
            countries = got or {}
            if isinstance(countries, dict):
                def _flag_from_code(cc: str) -> str:
                    try:
                        cc = (cc or '').upper()
                        return ''.join(chr(0x1F1E6 + ord(ch) - ord('A')) for ch in cc) if len(cc) == 2 else ''
                    except Exception:
                        return ''
                for code, data in countries.items():
                    try:
                        cc = str(code).upper()
                        phone = str((data or {}).get('phone') or '')
                        # phone values like "1", "1-242", "44", "7", "61"
                        first = phone.split(',')[0]
                        digits = ''.join(ch for ch in first if ch.isdigit())
                        if not cc or not digits:
                            continue
                        items.append({"code": cc, "dial": "+" + digits, "flag": _flag_from_code(cc)})
                    except Exception:
                        continue
        except Exception:
            items = []
        # Secondary source: npm dataset (if primary failed or empty)
        if not items:
            try:
                codes = _fetch_json('https://cdn.jsdelivr.net/npm/country-calling-code@1.1.1/build/cc.json')
                for c in (codes or []):
                    try:
                        code = str(c.get('isoCode2') or c.get('isoCode') or c.get('alpha2') or '').upper()
                        dial = c.get('callingCode') or c.get('phone') or c.get('countryCodes')
                        if isinstance(dial, list):
                            dial = dial[0] if dial else ''
                        digits = ''.join([ch for ch in str(dial) if ch.isdigit()])
                        if not code or not digits:
                            continue
                        # Generate flag locally
                        flag = ''.join(chr(0x1F1E6 + ord(ch) - ord('A')) for ch in code) if len(code) == 2 else ''
                        items.append({"code": code, "dial": "+" + digits, "flag": flag})
                    except Exception:
                        continue
            except Exception:
                items = []
        # Sort and cache
        items.sort(key=lambda x: x.get('code',''))
        # De-duplicate by code
        dedup = {}
        for it in items:
            dedup[it['code']] = it
        _COUNTRY_CODES_CACHE = list(dedup.values())
        _COUNTRY_CODES_CACHE_AT = datetime.now(timezone.utc)
        # Minimal fallback to ensure UI works
        return JSONResponse(content={"items": _COUNTRY_CODES_CACHE})
    except Exception as e:
        return JSONResponse(content={"items": [], "error": str(e)}, status_code=200)

@app.get("/api/meta/languages")
async def get_languages():
    """Return ISO 639-1 languages with English names (cached)."""
    global _LANGUAGES_CACHE, _LANGUAGES_CACHE_AT
    try:
        if _LANGUAGES_CACHE and _LANGUAGES_CACHE_AT and (datetime.now(timezone.utc) - _LANGUAGES_CACHE_AT).total_seconds() < 21600:
            return JSONResponse(content={"items": _LANGUAGES_CACHE})
        def _fetch_json(url: str):
            req = urllib.request.Request(url, headers={"User-Agent": "LEON/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                import json as _json
                return _json.loads(resp.read().decode("utf-8"))
        # Reliable dataset: annexare/Countries languages.json
        data = _fetch_json('https://raw.githubusercontent.com/annexare/Countries/master/data/languages.json')
        items = []
        if isinstance(data, dict):
            for code, name in data.items():
                try:
                    nm = str(name)
                    if nm:
                        items.append({"code": str(code).lower(), "name": nm})
                except Exception:
                    continue
        items.sort(key=lambda x: x.get('name',''))
        _LANGUAGES_CACHE = items
        _LANGUAGES_CACHE_AT = datetime.now(timezone.utc)
        return JSONResponse(content={"items": items})
    except Exception as e:
        return JSONResponse(content={"items": [{"code": "en", "name": "English"}] , "error": str(e)}, status_code=200)


def _auth_cookie_attrs(req: Request) -> dict:
    host = req.headers.get("host", "") if req else ""
    local = ("localhost" in host) or ("127.0.0.1" in host)
    return {"secure": not local, "samesite": "lax" if local else "none"}


def _partition_cross_site_cookies(response: JSONResponse) -> None:
    for index, (key, value) in enumerate(response.raw_headers):
        if key.lower() == b"set-cookie" and b"samesite=none" in value.lower() and b"partitioned" not in value.lower():
            response.raw_headers[index] = (key, value + b"; Partitioned")


# Authentication endpoints
@app.post("/api/auth/signup", response_model=AuthResponse)
async def signup_endpoint(request: SignupRequest):
    """Manager signup endpoint"""
    try:
        data = {
            'manager_name': request.manager_name,
            'manager_phone': request.manager_phone,
            'password': request.password,  # <-- Ensure password is included
            'hotel_name': request.hotel_name,
            'role': request.role,
            'nationality': request.nationality,
            'primary_language': request.primary_language,
            'hotel_timezone': request.hotel_timezone,
            'hotel_currency': request.hotel_currency,
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
async def signin_endpoint(request: SigninRequest, req: Request):
    """Manager signin endpoint (sets JWT as HTTP-only cookie)"""
    try:

        if request.auth_type == "phone":
            result = signin_manager_by_phone(request.auth_value, request.password)
 
        elif request.auth_type == "name":
            result = signin_manager_by_name(request.auth_value, request.password)
 
        elif request.auth_type == "hotel":
            result = signin_manager_by_hotel(request.auth_value, request.password)

        else:
            return AuthResponse(
                success=False,
                message="Invalid auth_type. Must be 'phone', 'name', or 'hotel'"
            )
        # If signin is successful and a token is present, set it as a cookie
        if result.get('success') and result.get('token'):
            response = JSONResponse(content={
                "success": result['success'],
                "message": result['message'],
                "manager_name": result.get('manager_name'),
                "hotel_name": result.get('hotel_name'),
                "role": result.get('role'),
                "auth_method": result.get('auth_method'),
                "token": result['token']
            })
            cookie_attrs = _auth_cookie_attrs(req)
            response.set_cookie(
                key="leon_jwt",
                value=result['token'],
                httponly=True,
                max_age=7 * 24 * 3600,
                **cookie_attrs
            )
            # Also set a readable hotel cookie for frontend defaulting
            try:
                hn = result.get('hotel_name') or ''
                if hn:
                    safe_hn = urllib.parse.quote(hn, safe='')
                    response.set_cookie(
                        key="leon_hotel",
                        value=safe_hn,
                        httponly=False,
                        **cookie_attrs
                    )
            except Exception:
                pass
            _partition_cross_site_cookies(response)
            # Register timezone immediately so subsequent writes have hotel tz ready
            try:
                _get_tz_from_jwt(result['token'])
            except Exception:
                pass
            return response
        else:
            return JSONResponse(content={
                "success": result.get('success', False),
                "message": result.get('message', 'Signin failed'),
                "manager_name": result.get('manager_name'),
                "hotel_name": result.get('hotel_name'),
                "role": result.get('role'),
                "auth_method": result.get('auth_method')
            })
    except Exception as e:
 
        return JSONResponse(content={
            "success": False,
            "message": f"Signin failed: {str(e)}"
        })

@app.post("/api/auth/logout")
async def logout_endpoint(req: Request):
    """Clear auth cookies and end session."""
    try:
        response = JSONResponse(content={"success": True, "message": "Logged out"})
        cookie_attrs = _auth_cookie_attrs(req)
        response.set_cookie(
            key="leon_jwt",
            value="",
            httponly=True,
            max_age=0,
            expires=0,
            **cookie_attrs
        )
        response.set_cookie(
            key="leon_hotel",
            value="",
            httponly=False,
            max_age=0,
            expires=0,
            **cookie_attrs
        )
        _partition_cross_site_cookies(response)
        return response
    except Exception as e:
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=500)

@app.get("/api/auth/me")
async def auth_me(leon_jwt: str = Cookie(None)):
    """Return manager profile basics from JWT (manager_name, hotel_name, role, timezone)."""
    if not leon_jwt:
        return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
    try:
        JWT_SECRET = os.getenv('JWT_SECRET', 'dev-secret')
        payload = _pyjwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
        return {
            "manager_name": payload.get('manager_name'),
            "hotel_name": payload.get('hotel_name'),
            "role": payload.get('role'),
            "hotel_timezone": payload.get('hotel_timezone'),
        }
    except Exception as e:
        return JSONResponse(content={"error": f"Invalid token: {e}"}, status_code=401)

@app.get("/api/auth/active")
async def auth_active(leon_jwt: str = Cookie(None)):
    """Signed-in manager. Same session as /api/auth/me."""
    if not leon_jwt:
        return JSONResponse(content={"success": False, "error": "Unauthorized"}, status_code=401)
    try:
        JWT_SECRET = os.getenv('JWT_SECRET', 'dev-secret')
        payload = _pyjwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
        manager = {
            "manager_name": payload.get('manager_name'),
            "hotel_name": payload.get('hotel_name'),
            "role": payload.get('role'),
            "hotel_timezone": payload.get('hotel_timezone'),
            "hotel_currency": payload.get('hotel_currency'),
        }
        return {"success": True, "manager": manager}
    except Exception as e:
        return JSONResponse(content={"success": False, "error": f"Invalid token: {e}"}, status_code=401)

# --- Classic single-step chat endpoint using leon_meta_orchestrator ---
@app.post("/api/chat/start")
async def chat_start(request: ChatRequest, leon_jwt: str = Cookie(None)):
 
    try:
        jwt_token = leon_jwt
        # Run blocking orchestration in a thread to avoid blocking the event loop
        response = await run_in_threadpool(leon_instance.process_request, request.message, jwt_token=jwt_token)
        # If tools were executed in the last request, surface a refresh hint
        refresh = False
        try:
            refresh = bool(getattr(leon_instance, 'last_tools_used', False))
            # Reset hint so it only triggers once per call
            leon_instance.last_tools_used = False
        except Exception:
            refresh = False
        return {"final_message": response, "refresh": refresh}
    except Exception as e:
        logger.error(f"Exception in /api/chat/start: {e}\n{traceback.format_exc()}")
        return {"final_message": f"Backend error: {str(e)}"}

@app.post("/api/chat/complete")
async def chat_complete(payload: dict = Body(...), leon_jwt: str = Cookie(None)):

    try:
        jwt_token = leon_jwt
        message = payload.get("message")
        response = await run_in_threadpool(leon_instance.process_request, message, jwt_token=jwt_token)
        refresh = False
        try:
            refresh = bool(getattr(leon_instance, 'last_tools_used', False))
            leon_instance.last_tools_used = False
        except Exception:
            refresh = False
        return {"final_message": response, "refresh": refresh}
    except Exception as e:
        logger.error(f"Exception in /api/chat/complete: {e}\n{traceback.format_exc()}")
        return {"final_message": f"Backend error: {str(e)}"}

# --- Chat history endpoints (sync UI with backend dialog_history) ---
@app.get("/api/chat/history")
async def chat_history(leon_jwt: str = Cookie(None)):
    try:
        if not leon_jwt:
            return JSONResponse(content={"items": []}, status_code=200)
        # Prime orchestrator context from JWT, then load dialog history
        try:
            from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_hotel, get_manager_name_from_jwt as _get_mgr
            hotel = _get_hotel(leon_jwt)
            mgr = _get_mgr(leon_jwt)
            if isinstance(hotel, str) and not hotel.startswith('❌'):
                leon_instance.hotel_name = hotel.upper()
            if isinstance(mgr, str) and not mgr.startswith('❌'):
                leon_instance.manager_name = mgr.upper()
        except Exception:
            pass
        try:
            if hasattr(leon_instance, 'load_dialog_history'):
                leon_instance.load_dialog_history()
        except Exception:
            pass
        history = getattr(leon_instance, 'dialog_history', []) or []
        items = []
        for idx, text in enumerate(history):
            try:
                sender = 'user' if idx % 2 == 0 else 'leon'
                items.append({"sender": sender, "text": str(text)})
            except Exception:
                continue
        return JSONResponse(content={"items": items})
    except Exception as e:
        return JSONResponse(content={"items": [], "error": str(e)}, status_code=200)

@app.post("/api/chat/clear")
async def chat_clear(leon_jwt: str = Cookie(None)):
    try:
        if not leon_jwt:
            return JSONResponse(content={"success": False, "message": "Unauthorized"}, status_code=401)
        # Prime orchestrator context from JWT
        try:
            from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_hotel, get_manager_name_from_jwt as _get_mgr
            hotel = _get_hotel(leon_jwt)
            mgr = _get_mgr(leon_jwt)
            if isinstance(hotel, str) and not hotel.startswith('❌'):
                leon_instance.hotel_name = hotel.upper()
            if isinstance(mgr, str) and not mgr.startswith('❌'):
                leon_instance.manager_name = mgr.upper()
        except Exception:
            pass
        # Clear and persist
        try:
            leon_instance.dialog_history = []
            if hasattr(leon_instance, 'save_dialog_history'):
                leon_instance.save_dialog_history()
        except Exception:
            pass
        return {"success": True}
    except Exception as e:
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=500)

# --- AI-generated contextual suggestion endpoint (OpenAI Chat Completions, no LangChain) ---
_SUGGEST_GUARDS = {}

@app.post("/api/chat/suggest")
async def chat_suggest(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Return a short, high-value suggestion for the current page, generated by OpenAI Chat Completions."""
    try:
        # Server-side throttle/circuit-breaker
        if os.getenv('LEON_SUGGESTIONS_ENABLED', '1') == '0':
            return {"text": ""}
        key = leon_jwt or (payload or {}).get("route") or "global"
        now = datetime.now(timezone.utc)
        guard = _SUGGEST_GUARDS.get(key) or {}
        until = guard.get('until')
        inflight = guard.get('inflight', False)
        if (until and until > now) or inflight:
            return {"text": ""}
        # set inflight guard
        _SUGGEST_GUARDS[key] = {"inflight": True, "until": now + timedelta(seconds=6)}
        route = (payload or {}).get("route") or ""
        extra = (payload or {}).get("context") or {}
        if not leon_jwt:
            _SUGGEST_GUARDS[key] = {"inflight": False, "until": now + timedelta(seconds=6)}
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        if _OpenAI is None:
            _SUGGEST_GUARDS[key] = {"inflight": False, "until": now + timedelta(seconds=6)}
            return JSONResponse(content={"error": "LLM unavailable"}, status_code=503)
        # Extract manager/hotel context from JWT
        JWT_SECRET = os.getenv('JWT_SECRET', 'dev-secret')
        try:
            payload_jwt = _pyjwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
            hotel_name = (payload_jwt.get('hotel_name') or '').upper()
            manager_name = (payload_jwt.get('manager_name') or '').upper()
            primary_language = (payload_jwt.get('primary_language') or '').strip()
        except Exception as e:
            return JSONResponse(content={"error": f"Invalid token: {e}"}, status_code=401)

        # Prime orchestrator context and load recent dialog history
        recent_history = []
        try:
            global leon_instance
            # Set hotel/manager into orchestrator instance for consistent context
            if leon_instance:
                if hotel_name:
                    leon_instance.hotel_name = hotel_name
                if manager_name:
                    leon_instance.manager_name = manager_name
                if hasattr(leon_instance, 'load_dialog_history'):
                    leon_instance.load_dialog_history()
                history = getattr(leon_instance, 'dialog_history', []) or []
                # Take last 8 turns (4 exchanges)
                window = history[-8:]
                for idx, msg in enumerate(window):
                    try:
                        role = 'MANAGER' if ((len(history)-len(window)+idx) % 2 == 0) else 'LEON'
                        recent_history.append(f"{role}: {str(msg)}")
                    except Exception:
                        continue
        except Exception:
            recent_history = []

        # Minimal page→purpose and prioritized tools for value guidance
        page_purpose = {
            'dashboard.html': 'Live KPIs, occupancy, revenue, alerts, and quick actions.',
            'rooms.html': 'Create/edit room types, specifications, and physical room numbers.',
            'inventory.html': 'Control nightly quantities and prices, including bulk updates.',
            'bookings.html': 'Create, search, modify, cancel bookings; confirmations and payments.',
            'guests.html': 'Manage guest profiles, loyalty, and guest analytics.',
            'housekeeping.html': 'Track readiness, dirty rooms, and cleaning task progress.',
            'operations.html': 'Assignments, relocations, OoO, maintenance, and readiness updates.',
            'analytics.html': 'Revenue/occupancy analytics, dashboards, reconciliations, channel reports.',
            'assignments.html': 'View and optimize room assignments for arrivals and in-house.',
            'activities.html': 'Audit recent operational actions across domains.'
        }
        tools_by_page = {
            'dashboard.html': ['generate_performance_dashboard', 'generate_operational_alerts', 'read_revenue_analytics', 'daily_manager_report', 'daily_forecast_report'],
            'rooms.html': ['read_room_types_by_criteria_tool', 'create_room_type_tool', 'manage_room_description_tool', 'add_room_numbers_tool', 'read_room_type_from_room_number_tool'],
            'inventory.html': ['manage_room_quantity', 'manage_room_price', 'check_room_pricing', 'read_inventory_information_from_description'],
            'bookings.html': ['create_booking_tool', 'search_bookings_tool', 'modify_stay_dates_tool', 'cancel_booking_tool', 'get_payment_status_for_booking_tool', 'send_booking_confirmation_tool'],
            'guests.html': ['create_guest_profile_tool', 'update_guest_profile_tool', 'read_guests_by_criteria_tool', 'reactivate_guest_profile_tool', 'read_guest_analytics'],
            'housekeeping.html': ['read_dirty_rooms', 'create_cleaning_task', 'update_cleaning_progress', 'read_room_readiness_status_tool', 'read_housekeeping_analytics'],
            'operations.html': ['assign_room_number', 'suggest_room_assignment', 'mark_room_out_of_order', 'mark_room_available', 'read_maintenance_requests'],
            'analytics.html': ['calculate_revenue_analytics', 'generate_occupancy_and_booking_analytics', 'generate_financial_reconciliation', 'generate_performance_dashboard', 'channel_performance_report'],
            'assignments.html': ['read_room_assignments_for_booking_tool', 'suggest_room_assignment', 'read_assignment_dashboard'],
            'activities.html': ['read_activity_logs_tool']
        }
        key = (route or '').split('?')[0].split('/')[-1].lower()
        purpose = page_purpose.get(key, '')
        tool_list = tools_by_page.get(key, [])

        model = os.getenv('LEON_SUGGESTION_MODEL', 'gpt-4o-mini')
        client = _OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        # Language coherence with LEONMetaOrchestrator
        lang_directive = (f"Respond in {primary_language}. " if primary_language else "")
        system = (
            "You are LEON, assisting a hotel manager. "
            + lang_directive +
            "Generate ONE casual, short, high-value, actionable suggestion for the current page, less than 7 words, no emojis, no markdown"
            "Prioritize actions that save time or prevent errors."
            "Do not remind to do something, just suggest the action."
            "End with a question mark"
        )
        # Compose prompt with brief dialog history to bias toward unfinished items
        recent_history_str = "\n".join(recent_history[-8:]) if recent_history else "(none)"
        content = (
            f"Hotel: {hotel_name}\nManager: {manager_name}\nPage: {key}\nPurpose: {purpose}\n"
            f"Relevant tools: {', '.join(tool_list)}\n"
            f"Context: {json.dumps(extra or {}, ensure_ascii=False)}\n"
            f"Recent dialog_history (latest last):\n{recent_history_str}\n"
            "Guidelines: If there is an unfinished or ongoing discussion, prefer a suggestion that continues it. Otherwise, propose the most valuable action for this page.\n"
        )
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=1.0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                timeout=10,
            )
            text = (resp.choices[0].message.content or '').strip() if resp and resp.choices else ''
            if text.startswith('```'):
                text = text.strip('`').strip()
            return {"text": text}
        except Exception as e:
            return JSONResponse(content={"error": f"LLM error: {e}"}, status_code=500)
        finally:
            # release guard and set backoff (8–16s jitter)
            try:
                jitter = 8 + (hash(key) % 9)
            except Exception:
                jitter = 10
            _SUGGEST_GUARDS[key] = {"inflight": False, "until": datetime.now(timezone.utc) + timedelta(seconds=jitter)}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

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

@app.post("/api/navigation/simple")
async def simple_navigation(payload: Dict[str, Any], request: Request):
    """Stateless simple navigation router. Returns route suggestion from text.
    Body: { "instruction": "go to rooms for king suite", "context": {"hotel":"AOE HOTEL"} }
    """
    try:
        instruction = (payload or {}).get("instruction", "")
        client_tag = (payload or {}).get("source") or request.headers.get("X-NAV-CLIENT") or "unknown"
        if not instruction:
            return JSONResponse(content={"error": "missing instruction"}, status_code=400)
        # First try fast local matcher for immediate route
        result = fast_decide_navigation(instruction)
        if not result:
            return {"route": None, "topic": None, "params": {}}
        # Optionally merge whitelisted params from context
        ctx = (payload or {}).get("context") or {}
        merged_params = dict(result.get("params") or {})
        for k in ("hotel", "room_type", "booking_id", "guest_phone"):
            if k in ctx and k not in merged_params:
                merged_params[k] = ctx[k]
        return {"route": result["route"], "topic": result["topic"], "params": merged_params, "source": client_tag}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
 

 

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
async def get_hotels(leon_jwt: str = Cookie(None)):
    """Get list of hotels derived from JWT (no manager_context)."""
    try:
        hotel_name = None
        if leon_jwt:
            try:
                from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
                decoded = get_hotel_name_from_jwt(leon_jwt)
                if isinstance(decoded, str) and not decoded.startswith("❌"):
                    hotel_name = decoded
            except Exception:
                hotel_name = None
        name = (hotel_name or "UNKNOWN HOTEL").upper()
        hid = name.lower().replace(' ', '_')
        hotels = [{
            'id': hid,
            'name': name,
            'location': 'UNKNOWN LOCATION',
            'manager': 'Hotel Manager',
            'status': 'active'
        }]
        return {"hotels": hotels}
    except Exception:
        return {"hotels": [{
            'id': 'unknown_hotel',
            'name': 'UNKNOWN HOTEL',
            'location': 'UNKNOWN LOCATION',
            'manager': 'Hotel Manager',
            'status': 'active'
        }]}

# Place this static endpoint BEFORE any /api/hotels/{hotel_id} or similar dynamic endpoints
@app.get("/api/hotels/rooms")
async def get_hotel_rooms(hotel_name: str = Query(""), room_type: str = Query(""), leon_jwt: str = Cookie(None)):
    """Get room portfolio for a hotel using room_manager (structured)."""
    try:
        # Normalize hotel_name: strip quotes and URL-decoding
        clean_hotel = (hotel_name or "").strip().strip('"').strip("'")
        try:
            clean_hotel = urllib.parse.unquote(clean_hotel)
        except Exception:
            pass
        if not clean_hotel:
            clean_hotel = _hotel_from_jwt(leon_jwt)
        if not clean_hotel:
            return JSONResponse(content={"hotel_name": "", "room_types": []})
        result = room_manager.read_room_portfolio(
            hotel_name=(clean_hotel or "").upper(),
            room_type=room_type or None,
            include_physical=True,
            include_structured=True,
        )
        if isinstance(result, dict) and isinstance(result.get("room_types"), list):
            # Merge readiness statuses
            try:
                from booking_system.front_desk_manager import intelligent_front_desk
                hotel_key = (clean_hotel or result.get("hotel_name") or "").upper()
                readiness = intelligent_front_desk.read_all_room_readiness_status_internal(hotel_key)
                status_map = {str(it.get("room_number")): str(it.get("readiness_status")).lower() for it in readiness.get("items", [])}
                for rt in result.get("room_types", []):
                    for rn in rt.get("room_numbers", []) if isinstance(rt.get("room_numbers"), list) else []:
                        num = str(rn.get("room_number"))
                        if num in status_map:
                            rn["room_status"] = status_map[num]
            except Exception:
                pass
            return JSONResponse(content=result)
        # Fallback: wrap string response
        return JSONResponse(content={"room_types": [], "raw": result})
    except Exception as e:
        return JSONResponse(content={"room_types": [], "error": str(e)})

@app.get("/api/hotels/available-rooms")
async def get_available_rooms(hotel_name: str = Query(None), room_type: str = Query(""), stay_date: str = Query(""), leon_jwt: str = Cookie(None)):
    """Fast list of available room numbers by (hotel, room_type, stay_date).
    Uses a single LEFT JOIN-style check via internal function; avoids heavy portfolio reads.
    """
    try:
        if not room_type or not stay_date:
            return JSONResponse(content={"available": [], "error": "Missing parameters"}, status_code=400)
        # Resolve hotel from query or JWT
        try:
            from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h
            if (not hotel_name) and leon_jwt:
                h = _get_h(leon_jwt)
                if isinstance(h, str) and not h.startswith('❌'):
                    hotel_name = h
        except Exception:
            pass
        if not hotel_name:
            return JSONResponse(content={"available": [], "error": "Missing hotel_name"}, status_code=400)
        from booking_system.front_desk_manager import intelligent_front_desk
        data = intelligent_front_desk.read_available_room_numbers_internal(
            (hotel_name or "").upper(), (room_type or "").upper(), stay_date
        )
        # Normalize output shape
        if isinstance(data, dict) and data.get("error"):
            return JSONResponse(content={"available": [], **data}, status_code=400)
        available = (data or {}).get("available_room_numbers") or []
        return JSONResponse(content={"hotel_name": (hotel_name or "").upper(), "room_type": (room_type or "").upper(), "stay_date": stay_date, "available": available})
    except Exception as e:
        return JSONResponse(content={"available": [], "error": str(e)}, status_code=500)

# --- Property Setup APIs (policies + templates) ---

# --- Property Templates (canonical, template-first flow) ---
@app.get("/api/property/templates")
async def api_property_templates(hotel_name: str = Query(None), leon_jwt: str = Cookie(None)):
    """READ canonical templates (keywords/facilities/amenities) prefilled from DB. REQUIRED before apply."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h
        _h = _get_h(leon_jwt)
        h = (hotel_name or "").upper() or ((isinstance(_h, str) and not _h.startswith("❌")) and _h.upper()) or ""
        if not h:
            return JSONResponse(content={"error": "Missing hotel_name"}, status_code=400)
        from booking_system.hotel_info_manager import read_property_templates as _read
        data = _read.invoke({"hotel_name": h}) if hasattr(_read, "invoke") else _read(h)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/property/templates/apply")
async def api_property_templates_apply(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """APPLY templates. Must be preceded by /api/property/templates read. Overwrites only provided templates."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h, get_manager_name_from_jwt as _get_m
        _h = _get_h(leon_jwt); _m = _get_m(leon_jwt)
        if not isinstance(_m, str) or _m.startswith("❌"):
            return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
        h = (payload.get("hotel_name") or "").upper() or ((isinstance(_h, str) and not _h.startswith("❌")) and _h.upper()) or ""
        if not h:
            return JSONResponse(content={"error": "Missing hotel_name"}, status_code=400)
        facilities_template = payload.get("facilities_template")
        amenities_template = payload.get("amenities_template")
        keywords_template = payload.get("keywords_template")
        from booking_system.hotel_info_manager import write_property_templates
        applied = write_property_templates(h, _m.upper(), facilities_template, amenities_template, keywords_template)
        if isinstance(applied, str) and applied.startswith("❌"):
            return JSONResponse(content={"error": applied}, status_code=400)
        return JSONResponse(content={"result": applied})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

async def _push_aiosell(hotel_name: str, stay_dates):
    from aiosell_channel_manager import push_dates
    return await run_in_threadpool(push_dates, hotel_name, list(stay_dates or []))

def _stay_dates_for_booking(hotel: str, booking_id: str) -> list:
    from booking_system.booking_manager import booking_manager as _bm
    _bm._ensure_postgres_connection()
    cur = _bm.postgres_conn.cursor()
    cur.execute(
        "SELECT stay_date FROM booking_stays WHERE hotel_name = %s AND booking_id = %s",
        (hotel, booking_id),
    )
    days = [str(row[0])[:10] for row in cur.fetchall()]
    _bm._safe_rollback()
    return days

def _write_guest_needs(hotel: str, guest_phone: str) -> dict:
    """Read the guest and save what the front desk needs to know."""
    if _OpenAI is None or not os.getenv("OPENAI_API_KEY"):
        return {"error": "ChatGPT is unavailable", "status": 503}
    from booking_system.guest_manager import guest_manager as gm
    from booking_system.guest_summary import summarize_guest
    record = gm.read_guest_with_bookings(hotel, guest_phone)
    if not record:
        return {"error": "Guest profile not found", "status": 404}
    client = _OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        brief = summarize_guest(client, record, gm.read_guest_media)
    except Exception as model_error:
        detail = str(model_error)
        if "insufficient_quota" in detail:
            return {"error": "ChatGPT has no credits remaining", "status": 429}
        return {"error": detail, "status": 502}
    missing = [name for name in ("guest_profile_summary", "guest_needs", "recommended_action") if not brief.get(name)]
    if missing:
        return {"error": "ChatGPT returned an empty summary", "status": 502}
    summary_at = datetime.now(timezone.utc).isoformat()
    saved = {
        "guest_profile_summary": brief["guest_profile_summary"],
        "guest_needs": brief["guest_needs"],
        "recommended_action": brief["recommended_action"],
        "ai_summary_at": summary_at,
        "ai_summary_for": "brief",
    }
    gm.mongo_db["guests"].update_one({"guest_phone": guest_phone}, {"$set": saved})
    return {"guest_phone": guest_phone, **saved}


@app.get("/webhook/whatsapp")
async def whatsapp_webhook_verify(request: Request):
    """Meta subscription handshake. The response body is the hub.challenge string."""
    from booking_system.whatsapp_webhook import subscription_challenge
    challenge = subscription_challenge(
        request.query_params.get("hub.mode", ""),
        request.query_params.get("hub.verify_token", ""),
        request.query_params.get("hub.challenge", ""),
    )
    if not challenge:
        return Response(status_code=403)
    return Response(content=challenge, media_type="text/plain")

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Incoming WhatsApp messages and delivery statuses for the messages field."""
    from booking_system.whatsapp_webhook import accept_webhook, signature_ok
    raw = await request.body()
    if not signature_ok(raw, request.headers.get("x-hub-signature-256", "")):
        return Response(status_code=403)
    try:
        payload = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        return JSONResponse(content={"error": "Invalid webhook payload"}, status_code=400)
    touched = await run_in_threadpool(accept_webhook, payload if isinstance(payload, dict) else {})
    for hotel, phone in touched or []:
        background_tasks.add_task(_write_guest_needs, hotel, phone)
    return {"success": True}

@app.post("/update_reservation")
async def update_reservation(request: Request, background_tasks: BackgroundTasks):
    """Aiosell book, modify, and cancel. The body is the full reservation state."""
    from booking_system.aiosell_reservation import authorize_basic
    from booking_system.channel_reservations import accept_webhook
    denied = authorize_basic(request.headers.get("authorization"))
    if denied:
        status = 401 if denied == "Unauthorized" or denied.startswith("Aiosell credentials") else 400
        return JSONResponse(content={"success": False, "message": denied}, status_code=status)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(content={"success": False, "message": "Invalid reservation payload"}, status_code=400)
    logging.info("Aiosell reservation action=%s bookingId=%s hotelCode=%s", (payload or {}).get("action"), (payload or {}).get("bookingId"), (payload or {}).get("hotelCode"))
    result = await run_in_threadpool(accept_webhook, payload if isinstance(payload, dict) else {})
    if result.get("success") and result.get("hotel_name"):
        from aiosell_channel_manager import push_dates
        background_tasks.add_task(push_dates, result["hotel_name"], list(result.get("stay_dates") or []))
    public = {"success": bool(result.get("success")), "message": str(result.get("message") or "")}
    if not public["success"]:
        return JSONResponse(content=public, status_code=400)
    return public

def _channel_manager_url() -> str:
    return str(os.environ.get("CHANNEL_MANAGER_URL") or "http://127.0.0.1:8006").rstrip("/")

def _channel_manager_request(method: str, path: str, body: dict = None):
    import requests
    try:
        response = requests.request(method, _channel_manager_url() + path, json=body, timeout=(3, 20))
    except requests.RequestException as exc:
        return {"error": "Channel manager is not reachable: " + str(exc)}
    try:
        data = response.json()
    except ValueError:
        return {"error": "Channel manager response was not JSON"}
    if response.status_code >= 400:
        return {"error": str((data or {}).get("error") or "Channel manager request failed")}
    return data

def _push_pms_booking(hotel: str, booking_id: str, guest_phone: str, room_type: str, stay_dates, channel: str):
    return _channel_manager_request("POST", "/reservation-in", {
        "hotel_name": hotel,
        "booking_id": booking_id,
        "guest_phone": guest_phone,
        "room_type": room_type,
        "stay_dates": list(stay_dates or []),
        "channel": channel,
        "booking_status": "confirmed",
    })

@app.get("/api/ota/reservation-out")
async def read_ota_reservation_out(leon_jwt: str = Cookie(None)):
    """Read the reservation list held by the channel manager."""
    try:
        hotel, denied = _rate_plan_hotel(leon_jwt)
        if denied:
            return denied
        result = await run_in_threadpool(_channel_manager_request, "GET", "/reservations?hotel_name=" + urllib.parse.quote(hotel))
        if result.get("error") and "reservations" not in result:
            return JSONResponse(content=result, status_code=502)
        return result
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/ota/reservation-in")
async def read_ota_reservation_in(leon_jwt: str = Cookie(None)):
    """Read PMS bookings stored as reservation in on the channel manager."""
    try:
        hotel, denied = _rate_plan_hotel(leon_jwt)
        if denied:
            return denied
        result = await run_in_threadpool(_channel_manager_request, "GET", "/reservation-in?hotel_name=" + urllib.parse.quote(hotel))
        if result.get("error") and "reservations" not in result:
            return JSONResponse(content=result, status_code=502)
        return result
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/property/policies")
async def api_property_policies(hotel_name: str = Query(None), policy_type: str = Query(None), manager_name: str = Query(None), leon_jwt: str = Cookie(None)):
    """READ all policies (MongoDB)."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        # Require manager_name from JWT (no fallback)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h, get_manager_name_from_jwt as _get_m
        _hotel_decoded = _get_h(leon_jwt)
        _mgr_decoded = _get_m(leon_jwt)
        if not isinstance(_mgr_decoded, str) or _mgr_decoded.startswith("❌"):
            return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
        m = _mgr_decoded.upper()
        h = (hotel_name or "").upper() or ((isinstance(_hotel_decoded, str) and not _hotel_decoded.startswith("❌")) and _hotel_decoded.upper()) or ""
        if not h:
            return JSONResponse(content={"error": "Missing hotel_name"}, status_code=400)
        structured = policy_manager.read_policies_structured(h, policy_type)
        text = policy_manager.read_policies(h, policy_type)
        return JSONResponse(content={"items": (structured or {}).get("items", []), "raw": text})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/property/registration_card")
async def read_registration_card(hotel_name: str = Query(...)):
    """House rules and terms printed on the guest registration card. Natural key is hotel_name."""
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        hotel = str(hotel_name or "").upper()
        doc = intelligent_front_desk.get_mongodb_connection().get_collection("registration_card").find_one(
            {"hotel_name": hotel}, {"_id": 0, "house_rules": 1, "terms": 1}
        )
        if not doc:
            return {"hotel_name": hotel, "house_rules": "", "terms": ""}
        return {"hotel_name": hotel, "house_rules": str(doc.get("house_rules") or ""), "terms": str(doc.get("terms") or "")}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/property/registration_card")
async def save_registration_card(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Create or update the registration card text for this hotel."""
    try:
        hotel, denied = _rate_plan_hotel(leon_jwt)
        if denied:
            return denied
        house_rules = str((payload or {}).get("house_rules") or "")
        terms = str((payload or {}).get("terms") or "")
        from booking_system.front_desk_manager import intelligent_front_desk
        coll = intelligent_front_desk.get_mongodb_connection().get_collection("registration_card")
        current = coll.find_one({"hotel_name": hotel})
        if current:
            coll.update_one({"hotel_name": hotel}, {"$set": {"house_rules": house_rules, "terms": terms}})
        else:
            coll.insert_one({"hotel_name": hotel, "house_rules": house_rules, "terms": terms})
        return {"hotel_name": hotel, "house_rules": house_rules, "terms": terms}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/property/policies/create")
async def api_property_policies_create(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """CREATE policy for a hotel (MongoDB)."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        hotel_name = (payload.get("hotel_name") or "").upper()
        manager_name = None
        # Require JWT for manager_name and (if missing) hotel_name
        try:
            from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h, get_manager_name_from_jwt as _get_m
            _h = _get_h(leon_jwt)
            _m = _get_m(leon_jwt)
            if isinstance(_h, str) and not _h.startswith("❌"):
                hotel_name = hotel_name or _h.upper()
            if isinstance(_m, str) and not _m.startswith("❌"):
                manager_name = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Invalid token"}, status_code=401)
        if not manager_name:
            return JSONResponse(content={"error": "Missing manager_name in token"}, status_code=401)
        if not hotel_name:
            return JSONResponse(content={"error": "Missing hotel_name"}, status_code=400)
        policy_type = (payload.get("policy_type") or "").lower()
        details = payload.get("policy_details") or {}
        result = policy_manager.create_policy(policy_type, details, hotel_name, manager_name)
        return JSONResponse(content={"result": result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/property/policies/update")
async def api_property_policies_update(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """UPDATE policy for a hotel (MongoDB)."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        hotel_name = (payload.get("hotel_name") or "").upper()
        manager_name = None
        try:
            from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h, get_manager_name_from_jwt as _get_m
            _h = _get_h(leon_jwt)
            _m = _get_m(leon_jwt)
            if isinstance(_h, str) and not _h.startswith("❌"):
                hotel_name = hotel_name or _h.upper()
            if isinstance(_m, str) and not _m.startswith("❌"):
                manager_name = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Invalid token"}, status_code=401)
        if not manager_name:
            return JSONResponse(content={"error": "Missing manager_name in token"}, status_code=401)
        if not hotel_name:
            return JSONResponse(content={"error": "Missing hotel_name"}, status_code=400)
        policy_type = (payload.get("policy_type") or "").lower()
        updates = payload.get("updates") or {}
        result = policy_manager.update_policy(policy_type, updates, hotel_name, manager_name)
        return JSONResponse(content={"result": result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/hotels/{hotel_id}")
async def get_hotel(hotel_id: str):
    """Get specific hotel information (manager_context deprecated → placeholder)"""
    try:
        hotel = {
            'id': hotel_id,
            'name': 'UNKNOWN HOTEL',
            'location': 'UNKNOWN LOCATION',
            'manager': 'Hotel Manager',
            'phone': '',
            'status': 'active'
        }
        return hotel
    except Exception as e:
        return JSONResponse(content={"error": str(e)})

# Housekeeping APIs (initial minimal set)
@app.get("/api/housekeeping/kpis")
async def hk_kpis():
    # manager_context deprecated → return zeros until JWT hotel context wired
    return {"dirty": 0, "cleaning": 0, "inspected": 0, "ready": 0}

@app.get("/api/housekeeping/board")
async def hk_board():
    # manager_context deprecated → return empty until JWT hotel context wired
    return {"rooms": [], "staff": [], "activity": []}

# Real housekeeping data endpoints (explicit params)
@app.get("/api/housekeeping/by_status")
async def hk_by_status(status: str = Query(...), leon_jwt: str = Cookie(None)):
    # Resolve hotel strictly from JWT
    if not leon_jwt:
        return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
    try:
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
        decoded = get_hotel_name_from_jwt(leon_jwt)
        if not decoded or isinstance(decoded, str) and decoded.startswith("❌"):
            return JSONResponse(content={"error": "❌ Invalid JWT hotel"}, status_code=401)
        clean_hotel = urllib.parse.unquote((decoded or "").strip().strip('"').strip("'"))
    except Exception as e:
        return JSONResponse(content={"error": f"❌ JWT decode failed: {e}"}, status_code=401)
    data = housekeeping_manager.get_cleaning_items_by_status_json(clean_hotel, status)
    return JSONResponse(content=data)

@app.get("/api/housekeeping/staff_workload")
async def hk_staff_workload(leon_jwt: str = Cookie(None)):
    if not leon_jwt:
        return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
    try:
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
        decoded = get_hotel_name_from_jwt(leon_jwt)
        if not decoded or isinstance(decoded, str) and decoded.startswith("❌"):
            return JSONResponse(content={"error": "❌ Invalid JWT hotel"}, status_code=401)
        clean_hotel = urllib.parse.unquote((decoded or "").strip().strip('"').strip("'"))
    except Exception as e:
        return JSONResponse(content={"error": f"❌ JWT decode failed: {e}"}, status_code=401)
    data = housekeeping_manager.get_staff_workload_json(clean_hotel)
    return JSONResponse(content=data)

@app.get("/api/housekeeping/activity")
async def hk_activity(limit: int = Query(30), leon_jwt: str = Cookie(None)):
    if not leon_jwt:
        return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
    try:
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
        decoded = get_hotel_name_from_jwt(leon_jwt)
        if not decoded or isinstance(decoded, str) and decoded.startswith("❌"):
            return JSONResponse(content={"error": "❌ Invalid JWT hotel"}, status_code=401)
        clean_hotel = urllib.parse.unquote((decoded or "").strip().strip('"').strip("'"))
    except Exception as e:
        return JSONResponse(content={"error": f"❌ JWT decode failed: {e}"}, status_code=401)
    data = housekeeping_manager.get_activity_json(clean_hotel, limit)
    return JSONResponse(content=data)

# Duty management + assignment APIs
@app.get("/api/housekeeping/on_duty")
async def hk_on_duty(leon_jwt: str = Cookie(None)):
    if not leon_jwt:
        return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
    try:
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
        decoded = get_hotel_name_from_jwt(leon_jwt)
        if not decoded or isinstance(decoded, str) and decoded.startswith("❌"):
            return JSONResponse(content={"error": "❌ Invalid JWT hotel"}, status_code=401)
        clean_hotel = urllib.parse.unquote((decoded or "").strip().strip('"').strip("'"))
    except Exception as e:
        return JSONResponse(content={"error": f"❌ JWT decode failed: {e}"}, status_code=401)
    data = housekeeping_manager.get_on_duty_staff_json(clean_hotel)
    return JSONResponse(content=data)

class StaffDutyPayload(BaseModel):
    hotel_name: str
    staff_phone: str
    shift_label: Optional[str] = None

@app.post("/api/housekeeping/clock_in")
async def hk_clock_in(payload: StaffDutyPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
        _m = _get_m(leon_jwt)
        if not isinstance(_m, str) or _m.startswith("❌"):
            return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
        manager_up = _m.upper()
    except Exception:
        return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
    msg = housekeeping_manager.staff_clock_in(payload.hotel_name, manager_up, payload.staff_phone, payload.shift_label)
    return {"result": msg}

@app.post("/api/housekeeping/clock_out")
async def hk_clock_out(payload: StaffDutyPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
        _m = _get_m(leon_jwt)
        if not isinstance(_m, str) or _m.startswith("❌"):
            return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
        manager_up = _m.upper()
    except Exception:
        return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
    msg = housekeeping_manager.staff_clock_out(payload.hotel_name, manager_up, payload.staff_phone)
    return {"result": msg}

class StartCleaningPayload(BaseModel):
    hotel_name: str
    room_number: str
    staff_phone: str
    task_type: Optional[str] = "standard_cleaning"
    priority: Optional[str] = "normal"
    notes: Optional[str] = ""

@app.post("/api/housekeeping/start_cleaning")
async def hk_start_cleaning(payload: StartCleaningPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
        _m = _get_m(leon_jwt)
        if not isinstance(_m, str) or _m.startswith("❌"):
            return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
        manager_up = _m.upper()
    except Exception:
        return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
    data = housekeeping_manager.start_cleaning_for_room(
        payload.hotel_name,
        manager_up,
        payload.room_number,
        payload.staff_phone,
        payload.task_type,
        payload.priority,
        payload.notes,
    )
    return JSONResponse(content=data)

@app.get("/api/staff/available")
async def staff_available(hotel_name: str = Query(...), department: Optional[str] = Query(None), availability_status: str = Query("available")):
    data = staff_manager.get_available_staff_json(hotel_name, department, availability_status)
    return JSONResponse(content=data)

# Real housekeeping data endpoints (explicit params)
@app.get("/api/housekeeping/dirty")
async def hk_dirty(leon_jwt: str = Cookie(None)):
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
        decoded = get_hotel_name_from_jwt(leon_jwt)
        if not decoded or isinstance(decoded, str) and decoded.startswith("❌"):
            return JSONResponse(content={"error": "❌ Invalid JWT hotel"}, status_code=401)
        hotel = (urllib.parse.unquote(decoded) or "").upper()
        manager = ""
        text = housekeeping_manager.read_dirty_rooms(hotel, manager)
        # Parse simple JSON response from text
        rooms = []
        if isinstance(text, str):
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("❌ Room") or line.startswith("- Room") or line.startswith("• Room") or line.startswith("Room"):
                    # Extract room number digits
                    import re
                    m = re.search(r"Room\s+(\w+)", line)
                    if m:
                        rooms.append({"room_number": m.group(1), "status": "dirty"})
        return {"hotel_name": hotel, "rooms": rooms}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/housekeeping/staff_status")
async def hk_staff_status(staff_phone: str = Query(...), room_number: Optional[str] = Query(None), leon_jwt: str = Cookie(None)):
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
        decoded = get_hotel_name_from_jwt(leon_jwt)
        if not decoded or isinstance(decoded, str) and decoded.startswith("❌"):
            return JSONResponse(content={"error": "❌ Invalid JWT hotel"}, status_code=401)
        hotel = (urllib.parse.unquote(decoded) or "").upper()
        manager = ""
        phone = (staff_phone or "").upper()
        rn = (room_number or None)
        text = housekeeping_manager.read_staff_current_cleaning_status(hotel, manager, phone, rn)
        return {"result": text}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/housekeeping/room_status")
async def hk_room_status(room_number: str = Query(...), leon_jwt: str = Cookie(None)):
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
        decoded = get_hotel_name_from_jwt(leon_jwt)
        if not decoded or isinstance(decoded, str) and decoded.startswith("❌"):
            return JSONResponse(content={"error": "❌ Invalid JWT hotel"}, status_code=401)
        hotel = (urllib.parse.unquote(decoded) or "").upper()
        manager = ""
        rn = (room_number or "").upper()
        text = housekeeping_manager.read_room_current_cleaning(hotel, manager, rn)
        return {"result": text}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/rooms/readiness_status")
async def rooms_readiness_status(hotel_name: str = Query(...), room_number: str = Query(...)):
    """Read readiness status (clean, dirty, cleaning, occupied) for a room (JSON)."""
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        hotel = (hotel_name or "").upper()
        rn = (room_number or "").upper()
        data = intelligent_front_desk.read_room_readiness_status_json(hotel, rn)
        if isinstance(data, dict) and data.get("error"):
            return JSONResponse(content=data, status_code=400)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/rooms/readiness_status_all")
async def rooms_readiness_status_all(hotel_name: str = Query(...)):
    """Bulk readiness statuses for a hotel (single call)."""
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        clean_hotel = (hotel_name or "").strip().strip('"').strip("'")
        try:
            clean_hotel = urllib.parse.unquote(clean_hotel)
        except Exception:
            pass
        hotel = (clean_hotel or "").upper()
        data = intelligent_front_desk.read_all_room_readiness_status_internal(hotel)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/assignments/calendar")
async def get_assignments_calendar(
    hotel_name: str = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    room_type: Optional[str] = Query(None),
    room_numbers: Optional[str] = Query(None),
    leon_jwt: str = Cookie(None)
):
    """Return calendar-style room_number assignments between start_date and end_date.
    room_numbers can be a comma-separated list.
    """
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        rooms_list = None
        if room_numbers:
            rooms_list = [x.strip() for x in room_numbers.split(',') if x.strip()]
        clean_hotel = (hotel_name or "").strip().strip('"').strip("'")
        try:
            clean_hotel = urllib.parse.unquote(clean_hotel)
        except Exception:
            pass
        if not clean_hotel:
            clean_hotel = _hotel_from_jwt(leon_jwt)
        # Optionally set per-request timezone override from JWT if present
        prev_tz_env = None
        try:
            if leon_jwt:
                import jwt as _jwt, os as _os
                JWT_SECRET = _os.getenv('JWT_SECRET', 'dev-secret')
                payload = _jwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
                tz = payload.get('hotel_timezone')
                if tz:
                    prev_tz_env = _os.getenv('HOTEL_TIMEZONE')
                    _os.environ['HOTEL_TIMEZONE'] = str(tz)
        except Exception:
            pass

        # Extend end_date by +1 day server-side so the response includes the next day
        try:
            from datetime import datetime, timezone, timedelta
            y, m, d = [int(x) for x in str(end_date).split('-')]
            base = datetime.now(timezone.utc).date()
            end_coerced = base.replace(year=y, month=m, day=d) + timedelta(days=1)
            end_for_query = end_coerced.isoformat()
        except Exception:
            end_for_query = end_date
        try:
            data = intelligent_front_desk.read_room_number_assignments_calendar_internal(
                (clean_hotel or "").upper(), start_date, end_for_query, room_type, rooms_list
            )
        finally:
            # Restore env override if we set it
            try:
                import os as _os
                if prev_tz_env is None:
                    if 'HOTEL_TIMEZONE' in _os.environ:
                        del _os.environ['HOTEL_TIMEZONE']
                else:
                    _os.environ['HOTEL_TIMEZONE'] = prev_tz_env
            except Exception:
                pass
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/assignments/preview_bulk")
async def get_assignment_preview_bulk(payload: dict = Body(...)):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        booking_ids = payload.get("booking_ids") or []
        if not isinstance(booking_ids, list) or not booking_ids:
            return JSONResponse(content={"previews": {}}, status_code=200)
        out = {}
        for bid in booking_ids:
            try:
                out[str(bid)] = intelligent_front_desk.read_booking_preview_internal(str(bid))
            except Exception as _:
                out[str(bid)] = {"error": "failed"}
        return JSONResponse(content={"previews": out})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/assignments/detail")
async def get_assignment_detail(booking_id: str = Query(...), stay_date: str = Query(None), leon_jwt: str = Cookie(None)):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        # Optionally set per-request timezone override from JWT if present
        prev_tz_env = None
        try:
            if leon_jwt:
                import jwt as _jwt, os as _os
                JWT_SECRET = _os.getenv('JWT_SECRET', 'dev-secret')
                payload = _jwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
                tz = payload.get('hotel_timezone')
                if tz:
                    prev_tz_env = _os.getenv('HOTEL_TIMEZONE')
                    _os.environ['HOTEL_TIMEZONE'] = str(tz)
        except Exception:
            pass
        try:
            data = intelligent_front_desk.read_booking_detail_internal(booking_id, stay_date)
        finally:
            # Restore env override if we set it
            try:
                import os as _os
                if prev_tz_env is None:
                    if 'HOTEL_TIMEZONE' in _os.environ:
                        del _os.environ['HOTEL_TIMEZONE']
                else:
                    _os.environ['HOTEL_TIMEZONE'] = prev_tz_env
            except Exception:
                pass
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/pricing/breakdown")
async def get_pricing_breakdown(booking_id: str = Query(...)):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        data = intelligent_front_desk.read_pricing_breakdown_internal(booking_id)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class OverridePricingPayload(BaseModel):
    hotel_name: Optional[str] = None
    booking_id: str
    new_total_amount: float

@app.post("/api/pricing/override")
async def post_pricing_override(payload: OverridePricingPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        # Resolve hotel_name from JWT if not provided
        hotel_name = payload.hotel_name
        try:
            if not hotel_name:
                from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
                from fastapi import Cookie
        except Exception:
            pass
        try:
            # Access leon_jwt cookie (FastAPI dependency injection not used inside handler scope)
            pass
        except Exception:
            pass
        # Fallback to payload.hotel_name if cookie approach not accessible here
        hotel_name = hotel_name or ""
        # Require manager from JWT
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        data = intelligent_front_desk.override_pricing_internal(hotel_name, manager_up, payload.booking_id, float(payload.new_total_amount))
        if isinstance(data, dict) and data.get("error"):
            return JSONResponse(content=data, status_code=400)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class UpdateBookingDetailPayload(BaseModel):
    booking_id: str
    guest: Optional[Dict[str, Any]] = None
    booking: Optional[Dict[str, Any]] = None
    payment: Optional[Dict[str, Any]] = None

class VerifyPhonePayload(BaseModel):
    booking_id: str
    guest_phone: str = ""
    guest_name: str = ""
    country_code: str = ""

@app.post("/api/assignments/verify-phone")
async def verify_assignment_phone(payload: VerifyPhonePayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt
        manager_name = get_manager_name_from_jwt(leon_jwt)
        if not isinstance(manager_name, str) or manager_name.startswith("❌"):
            return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
        from booking_system.front_desk_manager import intelligent_front_desk
        data = intelligent_front_desk.verify_booking_phone(str(payload.booking_id or "").strip(), manager_name.upper(), str(payload.guest_phone or "").strip(), str(payload.guest_name or "").strip(), str(payload.country_code or "").strip())
        if data.get("error"):
            return JSONResponse(content=data, status_code=400)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/assignments/update")
async def update_assignment_detail(payload: UpdateBookingDetailPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        data = payload.dict()
        # Optionally set per-request timezone override from JWT if present
        prev_tz_env = None
        try:
            if leon_jwt:
                import jwt as _jwt, os as _os
                JWT_SECRET = _os.getenv('JWT_SECRET', 'dev-secret')
                p2 = _jwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
                tz = p2.get('hotel_timezone')
                if tz:
                    prev_tz_env = _os.getenv('HOTEL_TIMEZONE')
                    _os.environ['HOTEL_TIMEZONE'] = str(tz)
        except Exception:
            pass
        # Optionally set per-request timezone override from JWT if present
        prev_tz_env = None
        try:
            if leon_jwt:
                import jwt as _jwt, os as _os
                JWT_SECRET = _os.getenv('JWT_SECRET', 'dev-secret')
                p2 = _jwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
                tz = p2.get('hotel_timezone')
                if tz:
                    prev_tz_env = _os.getenv('HOTEL_TIMEZONE')
                    _os.environ['HOTEL_TIMEZONE'] = str(tz)
        except Exception:
            pass
        try:
            result = intelligent_front_desk.update_booking_detail_internal(
                data["booking_id"], data.get("guest"), data.get("booking"), data.get("payment")
            )
        finally:
            # Restore env override if we set it
            try:
                import os as _os
                if prev_tz_env is None:
                    if 'HOTEL_TIMEZONE' in _os.environ:
                        del _os.environ['HOTEL_TIMEZONE']
                else:
                    _os.environ['HOTEL_TIMEZONE'] = prev_tz_env
            except Exception:
                pass
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class CancelBookingPayload(BaseModel):
    booking_id: str
    hotel_name: Optional[str] = None

@app.post("/api/assignments/cancel")
async def cancel_assignment_booking(payload: CancelBookingPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        from booking_system.booking_manager import booking_manager
        data = payload.dict()
        booking_id = data["booking_id"]
        hotel_name = data.get("hotel_name")
        if not hotel_name:
            conn = intelligent_front_desk.get_postgresql_connection()
            cur = conn.cursor()
            cur.execute("SELECT hotel_name FROM room_bookings WHERE booking_id = %s", (booking_id,))
            row = cur.fetchone()
            cur.close(); conn.close()
            if not row:
                return JSONResponse(content={"error": f"Booking {booking_id} not found"}, status_code=404)
            hotel_name = row[0]
        # Require manager from JWT
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_name = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        previous_dates = _stay_dates_for_booking(str(hotel_name or "").upper(), booking_id)
        result = booking_manager.cancel_booking(hotel_name, booking_id, manager_name)
        aiosell_push = await _push_aiosell(str(hotel_name or "").upper(), previous_dates)
        return JSONResponse(content={"result": result, "aiosell": aiosell_push})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class CheckInPayload(BaseModel):
    hotel_name: str
    booking_id: str
    room_number: Optional[str] = None
    assignment_id: Optional[str] = None
    notes: Optional[str] = ""

@app.post("/api/assignments/check_in")
async def assignments_check_in(payload: CheckInPayload, leon_jwt: str = Cookie(None)):
    """Check in a guest for a booking. Uses front desk manager's internal function."""
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        # Per-request timezone via JWT
        prev_tz_env = None
        try:
            if leon_jwt:
                import jwt as _jwt, os as _os
                JWT_SECRET = _os.getenv('JWT_SECRET', 'dev-secret')
                p2 = _jwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
                tz = p2.get('hotel_timezone')
                if tz:
                    prev_tz_env = _os.getenv('HOTEL_TIMEZONE')
                    _os.environ['HOTEL_TIMEZONE'] = str(tz)
        except Exception:
            pass
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        try:
            result = intelligent_front_desk.update_guest_check_in_status_internal(
                (payload.hotel_name or "").upper(),
                manager_up,
                payload.booking_id,
                payload.room_number,
                payload.notes or "",
                (payload.assignment_id or None),
            )
        finally:
            try:
                import os as _os
                if prev_tz_env is None:
                    if 'HOTEL_TIMEZONE' in _os.environ:
                        del _os.environ['HOTEL_TIMEZONE']
                else:
                    _os.environ['HOTEL_TIMEZONE'] = prev_tz_env
            except Exception:
                pass
        if isinstance(result, dict):
            return JSONResponse(content=result, status_code=400)
        if isinstance(result, str) and result.startswith("❌"):
            return JSONResponse(content={"error": result}, status_code=400)
        return JSONResponse(content={"result": result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class UndoCheckInPayload(BaseModel):
    hotel_name: str
    booking_id: str
    room_number: str
    mark_clean: bool = True

@app.post("/api/assignments/undo_check_in")
async def assignments_undo_check_in(payload: UndoCheckInPayload, leon_jwt: str = Cookie(None)):
    """Return one checked-in room to assigned. Other rooms on the booking are left unchanged."""
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        prev_tz_env = None
        try:
            if leon_jwt:
                import jwt as _jwt, os as _os
                JWT_SECRET = _os.getenv('JWT_SECRET', 'dev-secret')
                p2 = _jwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
                tz = p2.get('hotel_timezone')
                if tz:
                    prev_tz_env = _os.getenv('HOTEL_TIMEZONE')
                    _os.environ['HOTEL_TIMEZONE'] = str(tz)
        except Exception:
            pass
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        try:
            result = intelligent_front_desk.undo_guest_check_in_internal(
                (payload.hotel_name or "").upper(),
                manager_up,
                payload.booking_id,
                (payload.room_number or "").upper(),
                bool(payload.mark_clean),
            )
        finally:
            try:
                import os as _os
                if prev_tz_env is None:
                    if 'HOTEL_TIMEZONE' in _os.environ:
                        del _os.environ['HOTEL_TIMEZONE']
                else:
                    _os.environ['HOTEL_TIMEZONE'] = prev_tz_env
            except Exception:
                pass
        if isinstance(result, str) and result.startswith("❌"):
            return JSONResponse(content={"error": result}, status_code=400)
        return JSONResponse(content={"result": result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class ResetStayPayload(BaseModel):
    hotel_name: str
    booking_id: str
    stay_date: str

@app.post("/api/assignments/reset_stay")
async def assignments_reset_stay(payload: ResetStayPayload, leon_jwt: str = Cookie(None)):
    """Reset a booking's stay state for a date while keeping room assignment (clear CI/CO timestamps, set status to assigned)."""
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        # Per-request timezone via JWT
        prev_tz_env = None
        try:
            if leon_jwt:
                import jwt as _jwt, os as _os
                JWT_SECRET = _os.getenv('JWT_SECRET', 'dev-secret')
                p2 = _jwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
                tz = p2.get('hotel_timezone')
                if tz:
                    prev_tz_env = _os.getenv('HOTEL_TIMEZONE')
                    _os.environ['HOTEL_TIMEZONE'] = str(tz)
        except Exception:
            pass
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        try:
            result = intelligent_front_desk.reset_stay_state_for_date_internal(
                (payload.hotel_name or "").upper(),
                manager_up,
                payload.booking_id,
                payload.stay_date
            )
        finally:
            try:
                import os as _os
                if prev_tz_env is None:
                    if 'HOTEL_TIMEZONE' in _os.environ:
                        del _os.environ['HOTEL_TIMEZONE']
                else:
                    _os.environ['HOTEL_TIMEZONE'] = prev_tz_env
            except Exception:
                pass
        if isinstance(result, str) and result.startswith("❌"):
            return JSONResponse(content={"error": result}, status_code=400)
        return JSONResponse(content={"result": result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class MarkOutOfOrderPayload(BaseModel):
    hotel_name: str
    room_number: str
    start_date: str
    end_date: str
    reason: str

@app.post("/api/assignments/mark_out_of_order")
async def api_mark_out_of_order(payload: MarkOutOfOrderPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        text = intelligent_front_desk.update_room_out_of_order_status_internal(
            (payload.hotel_name or "").upper(), manager_up,
            (payload.room_number or "").upper(), payload.start_date, payload.end_date, payload.reason
        )
        if isinstance(text, str) and text.startswith("❌"):
            return JSONResponse(content={"error": text}, status_code=400)
        return {"result": text}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class MarkAvailablePayload(BaseModel):
    hotel_name: str
    room_number: str
    start_date: str
    end_date: str

@app.post("/api/assignments/mark_available")
async def api_mark_available(payload: MarkAvailablePayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        text = intelligent_front_desk.update_room_available_status_internal(
            (payload.hotel_name or "").upper(), manager_up,
            (payload.room_number or "").upper(), payload.start_date, payload.end_date
        )
        if isinstance(text, str) and text.startswith("❌"):
            return JSONResponse(content={"error": text}, status_code=400)
        return {"result": text}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class CreateMaintenancePayload(BaseModel):
    hotel_name: str
    room_number: str
    issue: str
    urgency: str = "NORMAL"

@app.post("/api/maintenance/create")
async def api_create_maintenance(payload: CreateMaintenancePayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.maintenance_manager import maintenance_manager
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        text = maintenance_manager.create_maintenance_request(
            (payload.hotel_name or "").upper(), manager_up,
            (payload.room_number or "").upper(), None, payload.issue, payload.urgency
        )
        if isinstance(text, str) and text.startswith("❌"):
            return JSONResponse(content={"error": text}, status_code=400)
        return {"result": text}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/maintenance/list")
async def api_list_maintenance(hotel_name: str = Query(...), room_number: str = Query(None), status_filter: str = Query(""), urgency_filter: str = Query(""), leon_jwt: str = Cookie(None)):
    try:
        from booking_system.maintenance_manager import maintenance_manager
        tz = _get_tz_from_jwt(leon_jwt)
        # Require manager from JWT
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        text = maintenance_manager.read_maintenance_requests(
            (hotel_name or "").upper(), manager_up,
            status_filter, urgency_filter, (room_number or "").upper(), "", 50, tz
        )
        return {"result": text}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class UpdateMaintenancePayload(BaseModel):
    hotel_name: str
    task_id: str
    status: str
    progress_notes: Optional[str] = ""
    resolution_description: Optional[str] = ""
    assigned_to_phone: Optional[str] = None
    assigned_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

@app.post("/api/maintenance/update")
async def api_update_maintenance(payload: UpdateMaintenancePayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.maintenance_manager import maintenance_manager
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        text = maintenance_manager.update_maintenance_progress(
            (payload.hotel_name or "").upper(), manager_up,
            payload.task_id, payload.status,
            payload.assigned_to_phone, payload.assigned_at, payload.started_at, payload.completed_at,
            payload.progress_notes or "", payload.resolution_description or "", "", None
        )
        if isinstance(text, str) and text.startswith("❌"):
            return JSONResponse(content={"error": text}, status_code=400)
        return {"result": text}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# --- Scheduled Maintenance APIs ---
class ScheduleSetupPayload(BaseModel):
    hotel_name: str
    room_number: Optional[str] = None
    area: Optional[str] = None
    issue: str = ""
    urgency: str = "NORMAL"
    interval_days: int = 90
    start_date: str
    auto_out_of_order: Optional[bool] = False
    preferred_window: Optional[str] = None
    active: Optional[bool] = True

class ScheduleUpdatePayload(BaseModel):
    hotel_name: str
    schedule_id: str
    room_number: Optional[str] = None
    area: Optional[str] = None
    issue: Optional[str] = None
    urgency: Optional[str] = None
    interval_days: Optional[int] = None
    start_date: Optional[str] = None
    preferred_window: Optional[str] = None
    auto_out_of_order: Optional[bool] = None
    active: Optional[bool] = None

@app.get("/api/maintenance/schedules/read")
def api_read_schedules(hotel_name: str = Query(""), target_type: str = Query(""), room_number: str = Query(""), area: str = Query(""), active_only: bool = Query(True), limit: int = Query(100), leon_jwt: str = Cookie(None)):
    try:
        from booking_system.maintenance_manager import maintenance_manager as _mm
        # Require manager from JWT and derive hotel from JWT if available
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m, get_hotel_name_from_jwt as _get_h
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"items": [], "error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
            _h = _get_h(leon_jwt)
            if isinstance(_h, str) and not _h.startswith("❌"):
                hotel_name = (hotel_name or _h).upper()
        except Exception:
            return JSONResponse(content={"items": [], "error": "Unauthorized"}, status_code=401)
        data = _mm.read_scheduled_maintenance(str(hotel_name or '').upper(), manager_up, target_type, room_number, area, active_only, limit)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"items": [], "error": str(e)}, status_code=500)

@app.post("/api/maintenance/schedules/setup")
async def api_setup_schedule(payload: ScheduleSetupPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.maintenance_manager import maintenance_manager as _mm
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        msg = _mm.setup_scheduled_maintenance(
            (payload.hotel_name or "").upper(), manager_up,
            (payload.room_number or None), (payload.area or None), payload.issue, payload.urgency,
            int(payload.interval_days), payload.start_date, bool(payload.auto_out_of_order or False),
            (payload.preferred_window or None), None, bool(payload.active if payload.active is not None else True)
        )
        if isinstance(msg, str) and msg.startswith("❌"):
            return JSONResponse(content={"error": msg}, status_code=400)
        return {"result": msg}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/maintenance/schedules/update")
async def api_update_schedule(payload: ScheduleUpdatePayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.maintenance_manager import maintenance_manager as _mm
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        msg = _mm.update_scheduled_maintenance(
            (payload.hotel_name or "").upper(), manager_up, payload.schedule_id,
            payload.room_number, payload.area, payload.issue, payload.urgency, payload.interval_days,
            payload.start_date, payload.preferred_window, payload.auto_out_of_order, payload.active
        )
        if isinstance(msg, str) and msg.startswith("❌"):
            return JSONResponse(content={"error": msg}, status_code=400)
        return {"result": msg}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class CheckOutPayload(BaseModel):
    hotel_name: str
    booking_id: str
    room_number: Optional[str] = None
    notes: Optional[str] = ""

@app.post("/api/assignments/check_out")
async def assignments_check_out(payload: CheckOutPayload, leon_jwt: str = Cookie(None)):
    """Check out a guest for a booking. Uses front desk manager's internal function."""
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        # Per-request timezone via JWT
        prev_tz_env = None
        try:
            if leon_jwt:
                import jwt as _jwt, os as _os
                JWT_SECRET = _os.getenv('JWT_SECRET', 'dev-secret')
                p2 = _jwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
                tz = p2.get('hotel_timezone')
                if tz:
                    prev_tz_env = _os.getenv('HOTEL_TIMEZONE')
                    _os.environ['HOTEL_TIMEZONE'] = str(tz)
        except Exception:
            pass
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        try:
            result = intelligent_front_desk.update_guest_check_out_status_internal(
                (payload.hotel_name or "").upper(),
                manager_up,
                payload.booking_id,
                payload.room_number,
                payload.notes or "",
            )
        finally:
            try:
                import os as _os
                if prev_tz_env is None:
                    if 'HOTEL_TIMEZONE' in _os.environ:
                        del _os.environ['HOTEL_TIMEZONE']
                else:
                    _os.environ['HOTEL_TIMEZONE'] = prev_tz_env
            except Exception:
                pass
        if isinstance(result, str) and result.startswith("❌"):
            return JSONResponse(content={"error": result}, status_code=400)
        return JSONResponse(content={"result": result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class BulkCheckoutPastPayload(BaseModel):
    hotel_name: str

@app.post("/api/assignments/bulk_checkout_past")
async def assignments_bulk_checkout_past(payload: BulkCheckoutPastPayload, leon_jwt: str = Cookie(None)):
    """Bulk check-out all past stays (stay_date < yesterday) for a hotel. Readiness is not changed.
    Database operation: write (PostgreSQL).
    """
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        # Timezone from JWT (optional)
        prev_tz_env = None
        try:
            if leon_jwt:
                import jwt as _jwt, os as _os
                JWT_SECRET = _os.getenv('JWT_SECRET', 'dev-secret')
                p2 = _jwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
                tz = p2.get('hotel_timezone')
                if tz:
                    prev_tz_env = _os.getenv('HOTEL_TIMEZONE')
                    _os.environ['HOTEL_TIMEZONE'] = str(tz)
        except Exception:
            pass
        # Manager from JWT
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        try:
            result = intelligent_front_desk.bulk_checkout_past_internal(
                (payload.hotel_name or "").upper(),
                manager_up,
            )
        finally:
            try:
                import os as _os
                if prev_tz_env is None:
                    if 'HOTEL_TIMEZONE' in _os.environ:
                        del _os.environ['HOTEL_TIMEZONE']
                else:
                    _os.environ['HOTEL_TIMEZONE'] = prev_tz_env
            except Exception:
                pass
        if isinstance(result, dict) and result.get('error'):
            return JSONResponse(content=result, status_code=400)
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/assignments/past_stays")
async def assignments_past_stays(hotel_name: str, leon_jwt: str = Cookie(None)):
    """READ: List past stays (stay_date < yesterday) not yet departed for a hotel.
    Database operation: read (PostgreSQL + MongoDB).
    """
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        result = intelligent_front_desk.read_past_stays_internal((hotel_name or '').upper())
        if isinstance(result, dict) and result.get('error'):
            return JSONResponse(content=result, status_code=400)
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class PastStaysPayload(BaseModel):
    hotel_name: str

@app.post("/api/assignments/past_stays")
async def assignments_past_stays_post(payload: PastStaysPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        result = intelligent_front_desk.read_past_stays_internal((payload.hotel_name or '').upper())
        if isinstance(result, dict) and result.get('error'):
            return JSONResponse(content=result, status_code=400)
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class BulkCheckoutSelectedPayload(BaseModel):
    hotel_name: str
    items: list

@app.post("/api/assignments/bulk_checkout_selected")
async def assignments_bulk_checkout_selected(payload: BulkCheckoutSelectedPayload, leon_jwt: str = Cookie(None)):
    """WRITE: Check-out selected (booking_id, stay_date) pairs.
    Database operation: write (PostgreSQL).
    """
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        result = intelligent_front_desk.bulk_checkout_selected_internal(
            (payload.hotel_name or '').upper(),
            manager_up,
            payload.items or []
        )
        if isinstance(result, dict) and result.get('error'):
            return JSONResponse(content=result, status_code=400)
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# Manage room type specifications (description/specs)
@app.post("/api/room_types/manage")
async def manage_room_type_specs(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
        try:
            from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_hotel, get_manager_name_from_jwt as _get_mgr
            hotel = _get_hotel(leon_jwt)
            mgr = _get_mgr(leon_jwt)
            if not isinstance(hotel, str) or hotel.startswith('❌'):
                return JSONResponse(content={"error": "❌ Invalid hotel in token"}, status_code=401)
            if not isinstance(mgr, str) or mgr.startswith('❌'):
                return JSONResponse(content={"error": "❌ Invalid manager in token"}, status_code=401)
            hotel = hotel.upper(); mgr = mgr.upper()
        except Exception as e:
            return JSONResponse(content={"error": f"❌ JWT decode failed: {e}"}, status_code=401)
        room_type = str((payload or {}).get('room_type') or '').upper()
        specifications = (payload or {}).get('specifications') or {}
        if not room_type or not isinstance(specifications, dict):
            return JSONResponse(content={"error": "❌ room_type and specifications are required"}, status_code=400)
        from booking_system.room_manager import room_manager as _rm
        result = _rm.manage_room_description(hotel, mgr, room_type, specifications)
        if isinstance(result, str) and result.startswith("❌"):
            return JSONResponse(content={"error": result}, status_code=400)
        return JSONResponse(content={"result": result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/room_types/delete")
async def delete_room_type_api(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """DELETE a room type and cascade delete all associated room numbers and inventory.
    Database operation: write (MongoDB + PostgreSQL)."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
        try:
            from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_hotel, get_manager_name_from_jwt as _get_mgr
            hotel = _get_hotel(leon_jwt)
            mgr = _get_mgr(leon_jwt)
            if not isinstance(hotel, str) or hotel.startswith('❌'):
                return JSONResponse(content={"error": "❌ Invalid hotel in token"}, status_code=401)
            if not isinstance(mgr, str) or mgr.startswith('❌'):
                return JSONResponse(content={"error": "❌ Invalid manager in token"}, status_code=401)
            hotel = hotel.upper(); mgr = mgr.upper()
        except Exception as e:
            return JSONResponse(content={"error": f"❌ JWT decode failed: {e}"}, status_code=401)
        room_type = str((payload or {}).get('room_type') or '').upper()
        if not room_type:
            return JSONResponse(content={"error": "❌ room_type is required"}, status_code=400)
        from booking_system.room_manager import room_manager as _rm
        result = _rm._delete_room_type_logic(hotel, mgr, room_type)
        if isinstance(result, str) and result.startswith("❌"):
            return JSONResponse(content={"error": result}, status_code=400)
        return JSONResponse(content={"result": result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class PaymentUpdatePayload(BaseModel):
    hotel_name: str
    booking_id: str
    payment_amount: float
    payment_method: str

@app.post("/api/payments/update")
async def payments_update(payload: PaymentUpdatePayload, leon_jwt: str = Cookie(None)):
    """Record a payment against a booking using payment manager's ledger logic."""
    try:
        from booking_system.payment_manager import payment_manager as _pm
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        msg = _pm.record_payment(
            (payload.hotel_name or "").upper(),
            manager_up,
            payload.booking_id,
            float(payload.payment_amount),
            payload.payment_method or "cash",
        )
        if isinstance(msg, str) and msg.strip().startswith("❌"):
            return JSONResponse(content={"error": msg}, status_code=400)
        return JSONResponse(content={"result": msg})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class RefundPayload(BaseModel):
    hotel_name: str
    booking_id: str
    payment_seq: int
    refund_type: str | None = None

@app.post("/api/payments/refund")
async def payments_refund(payload: RefundPayload, leon_jwt: str = Cookie(None)):
    """Mark a specific payment_ledger row as refunded by (booking_id, payment_seq)."""
    try:
        from booking_system.payment_manager import payment_manager as _pm
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        msg = _pm.record_refund(
            (payload.hotel_name or "").upper(),
            manager_up,
            payload.booking_id,
            int(payload.payment_seq),
            payload.refund_type,
        )
        if isinstance(msg, str) and msg.strip().startswith("❌"):
            return JSONResponse(content={"error": msg}, status_code=400)
        return JSONResponse(content={"result": msg})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/payments/ledger")
async def payments_ledger(booking_id: str = Query(...)):
    """Return full payment_ledger for a booking."""
    try:
        from booking_system.payment_manager import payment_manager as _pm
        data = _pm.read_payment_ledger(booking_id)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"booking_id": booking_id, "entries": [], "error": str(e)}, status_code=500)

class CreateCleaningTaskPayload(BaseModel):
    hotel_name: str
    room_number: str
    staff_phone: str
    task_type: Optional[str] = "standard_cleaning"
    priority: Optional[str] = "normal"
    notes: Optional[str] = ""

@app.post("/api/housekeeping/create_task")
async def hk_create_task(payload: CreateCleaningTaskPayload, leon_jwt: str = Cookie(None)):
    try:
        data = payload.dict()
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        text = housekeeping_manager.create_cleaning_task(
            data["hotel_name"].upper(),
            manager_up,
            data["room_number"].upper(),
            data["staff_phone"].upper(),
            data.get("task_type") or "standard_cleaning",
            data.get("priority") or "normal",
            data.get("notes") or "",
        )
        return {"result": text}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class UpdateCleaningProgressPayload(BaseModel):
    hotel_name: str
    cleaning_id: str
    new_status: str
    staff_phone: str
    notes: Optional[str] = ""

@app.post("/api/housekeeping/update_progress")
async def hk_update_progress(payload: UpdateCleaningProgressPayload, leon_jwt: str = Cookie(None)):
    try:
        data = payload.dict()
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        text = housekeeping_manager.update_cleaning_progress(
            data["hotel_name"].upper(),
            manager_up,
            data["cleaning_id"],
            data["new_status"].lower(),
            data["staff_phone"].upper(),
            data.get("notes") or "",
        )
        return {"result": text}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class AddRoomPayload(BaseModel):
    hotel_name: str
    room_type: str
    room_number: Optional[str] = None
    room_numbers: Optional[List[str]] = None

@app.post("/api/rooms/add")
async def api_add_room(payload: AddRoomPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.room_manager import room_manager as _rm
        clean_hotel = (payload.hotel_name or "").strip().strip('"').strip("'")
        try:
            clean_hotel = urllib.parse.unquote(clean_hotel)
        except Exception:
            pass
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        numbers = list(payload.room_numbers or [])
        if payload.room_number:
            numbers.append(str(payload.room_number))
        result = _rm.add_room_numbers((clean_hotel or "").upper(), manager_up, payload.room_type, numbers)
        if isinstance(result, str) and result.startswith("❌"):
            return JSONResponse(content={"error": result}, status_code=400)
        return {"result": result}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class RemoveRoomPayload(BaseModel):
    hotel_name: str
    room_type: str
    room_number: Optional[str] = None
    room_numbers: Optional[List[str]] = None

@app.post("/api/rooms/remove")
async def api_remove_room(payload: RemoveRoomPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.room_manager import room_manager as _rm
        clean_hotel = (payload.hotel_name or "").strip().strip('"').strip("'")
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        numbers = list(payload.room_numbers or [])
        if payload.room_number:
            numbers.append(str(payload.room_number))
        removed = []
        for number in numbers:
            result = _rm.delete_room_number((clean_hotel or "").upper(), manager_up, payload.room_type, number)
            if isinstance(result, str) and result.startswith("❌"):
                return JSONResponse(content={"error": result, "removed": removed}, status_code=400)
            removed.append(number)
        return {"result": "removed", "removed": removed}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class CreateRoomTypePayload(BaseModel):
    hotel_name: str
    room_type: str
    specifications: Dict[str, Any] = {}
    room_numbers: Optional[List[str]] = None

@app.post("/api/rooms/types/create")
async def api_create_room_type(payload: CreateRoomTypePayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.room_manager import room_manager as _rm
        clean_hotel = (payload.hotel_name or "").strip().strip('"').strip("'")
        # Normalize to UPPERCASE for consistency
        hotel_up = (clean_hotel or "").upper()
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        room_type_up = (payload.room_type or "").upper()
        numbers = []
        for raw in payload.room_numbers or []:
            key = str(raw or "").strip().upper()
            if not key:
                return JSONResponse(content={"error": "Room number is required"}, status_code=400)
            if key in numbers:
                return JSONResponse(content={"error": f"Room number {key} is already used"}, status_code=400)
            numbers.append(key)
        if not numbers:
            return JSONResponse(content={"error": "Room number is required"}, status_code=400)
        used = list(_rm.collection.find(
            {"hotel_name": hotel_up, "room_number": {"$in": numbers}, "has_room_number": True},
            {"room_number": 1}
        ))
        if used:
            found = ", ".join(str(doc.get("room_number")) for doc in used)
            return JSONResponse(content={"error": f"Room number {found} is already used"}, status_code=400)
        specs_in = payload.specifications or {}
        # Uppercase string values in specifications; keep numbers/bools as-is
        normalized_specs = {}
        try:
            for k, v in (specs_in.items() if isinstance(specs_in, dict) else []):
                if isinstance(v, str):
                    normalized_specs[k] = v.upper()
                else:
                    normalized_specs[k] = v
        except Exception:
            normalized_specs = specs_in if isinstance(specs_in, dict) else {}
        result = _rm._create_room_type_logic(hotel_up, manager_up, room_type_up, normalized_specs)
        if isinstance(result, str) and result.startswith("❌"):
            return JSONResponse(content={"error": result}, status_code=400)
        added = _rm.add_room_numbers(hotel_up, manager_up, room_type_up, numbers)
        if isinstance(added, str) and added.startswith("❌"):
            return JSONResponse(content={"error": added}, status_code=400)
        return {"result": result, "room_numbers": numbers}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class RoomLabelItem(BaseModel):
    room_number: str
    room_label: Optional[str] = None

class RoomLabelsPayload(BaseModel):
    hotel_name: str
    room_type: str
    labels: List[RoomLabelItem] = []

@app.post("/api/rooms/labels")
async def api_room_labels(payload: RoomLabelsPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.room_manager import room_manager as _rm
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        result = _rm.set_room_labels(
            (payload.hotel_name or "").upper(),
            manager_up,
            payload.room_type,
            [item.dict() for item in payload.labels],
        )
        if isinstance(result, dict) and result.get("error"):
            return JSONResponse(content=result, status_code=400)
        return result
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class RoomNumberChange(BaseModel):
    room_number: str
    new_room_number: str

class RenameRoomsPayload(BaseModel):
    hotel_name: str
    room_type: str
    rooms: List[RoomNumberChange] = []

@app.post("/api/rooms/rename")
async def api_rename_rooms(payload: RenameRoomsPayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.room_manager import room_manager as _rm
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        result = _rm.rename_room_numbers(
            (payload.hotel_name or "").upper(),
            manager_up,
            payload.room_type,
            [item.dict() for item in payload.rooms],
        )
        if isinstance(result, dict) and result.get("error"):
            return JSONResponse(content=result, status_code=400)
        return result
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class ReassignRoomForDatePayload(BaseModel):
    hotel_name: str
    booking_id: str
    stay_date: str
    old_room_number: str
    new_room_number: str
    mark_old_clean: Optional[bool] = None

@app.post("/api/assignments/reassign")
async def reassign_room_for_date(payload: ReassignRoomForDatePayload, leon_jwt: str = Cookie(None)):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        data = payload.dict()
        # Optionally set per-request timezone override from JWT if present
        prev_tz_env = None
        try:
            if leon_jwt:
                import jwt as _jwt, os as _os
                JWT_SECRET = _os.getenv('JWT_SECRET', 'dev-secret')
                p2 = _jwt.decode(leon_jwt, JWT_SECRET, algorithms=['HS256'])
                tz = p2.get('hotel_timezone')
                if tz:
                    prev_tz_env = _os.getenv('HOTEL_TIMEZONE')
                    _os.environ['HOTEL_TIMEZONE'] = str(tz)
        except Exception:
            pass
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
            _m = _get_m(leon_jwt)
            if not isinstance(_m, str) or _m.startswith("❌"):
                return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
            manager_up = _m.upper()
        except Exception:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        try:
            result = intelligent_front_desk.reassign_room_for_date_internal(
                (data["hotel_name"] or "").upper(),
                manager_up,
                data["booking_id"],
                data["stay_date"],
                (data["old_room_number"] or "").upper(),
                (data["new_room_number"] or "").upper(),
                data.get("mark_old_clean"),
            )
        finally:
            # Restore env override if we set it
            try:
                import os as _os
                if prev_tz_env is None:
                    if 'HOTEL_TIMEZONE' in _os.environ:
                        del _os.environ['HOTEL_TIMEZONE']
                else:
                    _os.environ['HOTEL_TIMEZONE'] = prev_tz_env
            except Exception:
                pass
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/assignments/history")
async def get_assignment_history(booking_id: str = Query(...)):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        data = intelligent_front_desk.read_assignment_history_internal(booking_id)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/bookings-calendar")
async def get_bookings_calendar(leon_jwt: str = Cookie(None)):
    try:
        if not leon_jwt:
            return {"bookings": []}
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h
        hotel_name = _get_h(leon_jwt)
        if not isinstance(hotel_name, str) or hotel_name.startswith('❌'):
            return {"bookings": []}
        from booking_system.booking_manager import booking_manager as _bm
        data = _bm.read_recent_bookings(hotel_name.upper(), limit=50)
        return data if isinstance(data, dict) else {"bookings": []}
    except Exception as e:
        return {"bookings": [], "error": str(e)}

@app.post("/api/bookings/create")
async def api_bookings_create(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Create a booking using booking_manager.create_booking. Requires JWT for hotel/manager.
    Expected payload: { guest_phone, room_type, stay_dates: [YYYY-MM-DD,...], rooms_booked?, booking_notes? }
    """
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_hotel, get_manager_name_from_jwt as _get_mgr
        hotel = _get_hotel(leon_jwt); mgr = _get_mgr(leon_jwt)
        if not isinstance(hotel, str) or hotel.startswith('❌'):
            return JSONResponse(content={"error": "❌ Invalid hotel in token"}, status_code=401)
        if not isinstance(mgr, str) or mgr.startswith('❌'):
            return JSONResponse(content={"error": "❌ Invalid manager in token"}, status_code=401)
        hotel = hotel.upper(); mgr = mgr.upper()
        guest_phone = str((payload or {}).get('guest_phone') or '').upper()
        room_type = str((payload or {}).get('room_type') or '').upper()
        stay_dates = (payload or {}).get('stay_dates') or []
        rooms_booked = int((payload or {}).get('rooms_booked') or 1)
        booking_notes = (payload or {}).get('booking_notes') or ""
        channel = str((payload or {}).get('channel') or 'PMS').upper()
        if not guest_phone or not room_type or not isinstance(stay_dates, list) or not stay_dates:
            return JSONResponse(content={"error": "❌ guest_phone, room_type, and stay_dates are required"}, status_code=400)
        from booking_system.booking_manager import booking_manager as _bm
        result = _bm.create_booking(hotel, mgr, guest_phone, room_type, stay_dates, rooms_booked, booking_notes, channel)
        if isinstance(result, str) and result.startswith("❌"):
            return JSONResponse(content={"error": result}, status_code=400)
        aiosell = await _push_aiosell(hotel, stay_dates)
        booking_id = getattr(_bm, "_last_booking_id", "") or ""
        channel_manager = await run_in_threadpool(_push_pms_booking, hotel, booking_id, guest_phone, room_type, stay_dates, channel)
        return JSONResponse(content={"result": result, "booking_id": booking_id, "assigned_rooms": list(getattr(_bm, "_last_assigned_rooms", []) or []), "aiosell": aiosell, "channel_manager": channel_manager})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/bookings/group")
async def api_bookings_group(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Create one PMS booking per room type."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_hotel, get_manager_name_from_jwt as _get_mgr
        hotel = _get_hotel(leon_jwt); mgr = _get_mgr(leon_jwt)
        if not isinstance(hotel, str) or hotel.startswith('❌'):
            return JSONResponse(content={"error": "❌ Invalid hotel in token"}, status_code=401)
        if not isinstance(mgr, str) or mgr.startswith('❌'):
            return JSONResponse(content={"error": "❌ Invalid manager in token"}, status_code=401)
        hotel = hotel.upper(); mgr = mgr.upper()
        guest_phone = str((payload or {}).get('guest_phone') or '').upper()
        stay_dates = (payload or {}).get('stay_dates') or []
        lines = (payload or {}).get('lines') or []
        booking_notes = (payload or {}).get('booking_notes') or ""
        if not guest_phone or not isinstance(stay_dates, list) or not stay_dates or not isinstance(lines, list) or not lines:
            return JSONResponse(content={"error": "❌ guest_phone, stay_dates, and lines are required"}, status_code=400)
        from booking_system.booking_manager import booking_manager as _bm
        result = _bm.create_group_booking(hotel, mgr, guest_phone, stay_dates, lines, booking_notes)
        if isinstance(result, str) and result.startswith("❌"):
            return JSONResponse(content={"error": result}, status_code=400)
        aiosell = await _push_aiosell(hotel, stay_dates)
        booking_ids = list(getattr(_bm, "_last_group_booking_ids", []) or [])
        channel_manager = []
        for index, booking_id in enumerate(booking_ids):
            line = lines[index] if index < len(lines) else {}
            line_type = str((line or {}).get("room_type") or "").strip().upper()
            channel_manager.append(await run_in_threadpool(_push_pms_booking, hotel, booking_id, guest_phone, line_type, stay_dates, "PMS"))
        return JSONResponse(content={"result": result, "booking_ids": booking_ids, "aiosell": aiosell, "channel_manager": channel_manager})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/bookings/modify")
async def api_bookings_modify(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Modify an existing booking via booking_manager.modify_booking. Requires JWT for hotel/manager.
    Expected payload: { booking_id, new_rooms_booked?, new_stay_dates?, booking_notes? }
    """
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_hotel, get_manager_name_from_jwt as _get_mgr
        hotel = _get_hotel(leon_jwt); mgr = _get_mgr(leon_jwt)
        if not isinstance(hotel, str) or hotel.startswith('❌'):
            return JSONResponse(content={"error": "❌ Invalid hotel in token"}, status_code=401)
        if not isinstance(mgr, str) or mgr.startswith('❌'):
            return JSONResponse(content={"error": "❌ Invalid manager in token"}, status_code=401)
        hotel = hotel.upper(); mgr = mgr.upper()
        booking_id = str((payload or {}).get('booking_id') or '').strip()
        new_rooms_booked = (payload or {}).get('new_rooms_booked')
        new_stay_dates = (payload or {}).get('new_stay_dates') or None
        notes = (payload or {}).get('booking_notes') or None
        new_room_type = str((payload or {}).get('new_room_type') or '').upper() or None
        channel = (payload or {}).get('channel')
        if channel is not None:
            channel = str(channel).upper()
        if not booking_id:
            return JSONResponse(content={"error": "❌ booking_id is required"}, status_code=400)
        # Normalize dates to list[str]
        if new_stay_dates is not None:
            if not isinstance(new_stay_dates, list):
                return JSONResponse(content={"error": "❌ new_stay_dates must be a list of YYYY-MM-DD"}, status_code=400)
            new_stay_dates = [str(d) for d in new_stay_dates]
        if new_rooms_booked is not None:
            try:
                new_rooms_booked = int(new_rooms_booked)
            except Exception:
                return JSONResponse(content={"error": "❌ new_rooms_booked must be an integer"}, status_code=400)
        from booking_system.booking_manager import booking_manager as _bm
        previous_dates = _stay_dates_for_booking(hotel, booking_id)
        result = _bm.modify_booking(hotel, booking_id, mgr, new_rooms_booked, new_stay_dates, notes, new_room_type, channel)
        if isinstance(result, str) and result.startswith("❌"):
            return JSONResponse(content={"error": result}, status_code=400)
        changed_dates = list(previous_dates)
        if new_stay_dates:
            changed_dates.extend(new_stay_dates)
        aiosell = await _push_aiosell(hotel, changed_dates)
        return JSONResponse(content={"result": result, "aiosell": aiosell})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/bookings/cancel")
async def api_bookings_cancel(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h, get_manager_name_from_jwt as _get_m
        hotel = _get_h(leon_jwt); mgr = _get_m(leon_jwt)
        if not isinstance(hotel, str) or hotel.startswith('❌'):
            return JSONResponse(content={"error": "❌ Invalid hotel in token"}, status_code=401)
        if not isinstance(mgr, str) or mgr.startswith('❌'):
            return JSONResponse(content={"error": "❌ Invalid manager in token"}, status_code=401)
        hotel = hotel.upper(); mgr = mgr.upper()
        booking_id = str((payload or {}).get('booking_id') or '').strip()
        if not booking_id:
            return JSONResponse(content={"error": "❌ booking_id is required"}, status_code=400)
        from booking_system.booking_manager import booking_manager as _bm
        previous_dates = _stay_dates_for_booking(hotel, booking_id)
        # PRE-CANCEL: capture affected rooms for today/yesterday
        pre_rooms = []
        try:
            from zoneinfo import ZoneInfo
            tz = _get_tz_from_jwt(leon_jwt)
            from datetime import datetime, timedelta, timezone as _tz
            today_local = datetime.now(ZoneInfo(tz)).date() if tz else datetime.now(_tz.utc).date()
            target_dates = [today_local, today_local - timedelta(days=1)]
            from booking_system.front_desk_manager import intelligent_front_desk
            conn0 = intelligent_front_desk.get_postgresql_connection()
            cur0 = conn0.cursor()
            cur0.execute(
                """
                SELECT DISTINCT room_number
                FROM room_stay
                WHERE hotel_name = %s AND booking_id = %s AND stay_date IN (%s, %s) AND room_number IS NOT NULL
                """,
                (hotel, booking_id, target_dates[0], target_dates[1])
            )
            pre_rooms = [str(r[0]) for r in (cur0.fetchall() or []) if r and r[0] is not None]
            try:
                cur0.close(); conn0.close()
            except Exception:
                pass
        except Exception:
            pre_rooms = []

        # CANCEL
        result = _bm.cancel_booking(hotel, booking_id, mgr)
        if isinstance(result, str) and result.startswith("❌"):
            return JSONResponse(content={"error": result}, status_code=400)
        aiosell_push = await _push_aiosell(hotel, previous_dates)

        # POST-CANCEL: set readiness to dirty for captured rooms currently occupied
        try:
            from booking_system.front_desk_manager import intelligent_front_desk
            for rn in pre_rooms:
                try:
                    status_json = intelligent_front_desk.read_room_readiness_status_json(hotel, rn)
                    # Flip only if occupied
                    if (status_json or {}).get("readiness_status") == 'occupied':
                        intelligent_front_desk.update_room_readiness_status_internal(
                            hotel, mgr, rn, 'dirty', f'Cancelled {booking_id} (post-cancel readiness)'
                        )
                except Exception:
                    continue
        except Exception:
            pass
        return JSONResponse(content={"result": result, "aiosell": aiosell_push})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/availability")
async def api_availability(hotel_name: str = Query(...), room_type: str = Query(...), stay_date: str = Query(...), rooms_needed: int = Query(1)):
    """Return availability using booking_manager.read_availability.
    Response: { available: int, total: int, booked: int, requested: int, raw?: str }
    """
    try:
        from booking_system.booking_manager import booking_manager as _bm
        txt = _bm.read_availability(hotel_name.upper(), room_type.upper(), stay_date, int(rooms_needed))
        if isinstance(txt, str) and txt.strip().startswith("❌"):
            return JSONResponse(content={"error": txt}, status_code=400)
        # Parse standardized lines
        total = booked = available = requested = 0
        try:
            for line in txt.splitlines():
                s = line.strip()
                if s.startswith('• Total Inventory:'):
                    total = int(s.split(':',1)[1].strip())
                elif s.startswith('• Currently Booked:'):
                    booked = int(s.split(':',1)[1].strip())
                elif s.startswith('• Available:'):
                    available = int(s.split(':',1)[1].strip())
                elif s.startswith('• Requested:'):
                    requested = int(s.split(':',1)[1].strip())
        except Exception:
            pass
        return {"available": available, "total": total, "booked": booked, "requested": requested, "raw": txt}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/guest_profile")
async def api_guest_profile(guest_phone: str = Query(...)):
    """Return guest profile text for a phone number. Response: { profile: str } or { error: str }"""
    try:
        from booking_system.guest_manager import guest_manager as gm
        phone = str(guest_phone or '').upper()
        txt = gm.read_guest_profile(phone)
        if isinstance(txt, str) and txt.strip().startswith("❌"):
            return JSONResponse(content={"error": txt}, status_code=404)
        guest = gm.mongo_db['guests'].find_one({"guest_phone": phone, "status": "active"}, {"_id": 0})
        record = {}
        for key in ("guest_phone", "guest_name", "email", "guest_email", "nationality", "country", "date_of_birth", "gender", "company", "address", "identity_type", "identity_number", "special_request", "special_requests", "vip", "loyalty_tier", "status"):
            record[key] = guest.get(key) if guest else None
        booking_count, total_spent, currency = gm.guest_booking_totals(phone, (guest or {}).get("hotel_name"))
        record["total_bookings"] = booking_count
        record["total_spent"] = total_spent
        record["currency"] = currency
        return {"profile": str(txt), "guest": record}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

def _guests_hotel(leon_jwt: str, hotel_name: str):
    if leon_jwt:
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h
        hotel = _get_h(leon_jwt)
        if not isinstance(hotel, str) or hotel.startswith("❌"):
            return None, JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
        return hotel.upper(), None
    hotel = str(hotel_name or "").strip().upper()
    if not hotel:
        return None, JSONResponse(content={"error": "hotel_name is required"}, status_code=400)
    return hotel, None

@app.get("/api/guests")
@app.get("/api/guests/search")
async def api_guests_search(q: str = "", hotel_name: str = "", leon_jwt: str = Cookie(None)):
    """Search active guests at the hotel by phone, name, or email."""
    try:
        hotel, denied = _guests_hotel(leon_jwt, hotel_name)
        if denied:
            return denied
        from booking_system.guest_manager import guest_manager as gm
        return {"success": True, "hotel_name": hotel, "guests": gm.search_guests(hotel, q)}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/guests/record")
async def api_guests_record(guest_phone: str = Query(...), hotel_name: str = "", leon_jwt: str = Cookie(None)):
    """One guest, with past, current, and future bookings and assigned rooms."""
    try:
        hotel, denied = _guests_hotel(leon_jwt, hotel_name)
        if denied:
            return denied
        from booking_system.guest_manager import guest_manager as gm
        record = gm.read_guest_with_bookings(hotel, str(guest_phone or "").upper())
        if not record:
            return JSONResponse(content={"error": "Guest profile not found"}, status_code=404)
        return record
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/guests/whatsapp")
async def api_guest_whatsapp(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Send one WhatsApp text to a guest and save it on the guest record."""
    try:
        hotel, denied = _guests_hotel(leon_jwt, str((payload or {}).get("hotel_name") or ""))
        if denied:
            return denied
        guest_phone = str((payload or {}).get("guest_phone") or "").upper()
        text = str((payload or {}).get("body") or "").strip()
        if not guest_phone or not text:
            return JSONResponse(content={"error": "guest_phone and body are required"}, status_code=400)
        from booking_system.guest_manager import guest_manager as gm
        from booking_system.front_desk_manager import intelligent_front_desk
        guest = gm.mongo_db["guests"].find_one({"guest_phone": guest_phone, "status": "active"})
        if not guest or str(guest.get("hotel_name") or "").upper() != hotel:
            return JSONResponse(content={"error": "Guest profile not found"}, status_code=404)
        window = gm.whatsapp_text_window(gm.list_guest_messages(hotel, guest_phone))
        if not window["open"]:
            return JSONResponse(content={"error": "Free text is closed"}, status_code=400)
        sent = intelligent_front_desk.send_whatsapp_text(guest_phone, text)
        if sent.get("error"):
            return JSONResponse(content={"error": sent["error"]}, status_code=400)
        sent_at = datetime.now(timezone.utc).isoformat()
        gm.save_whatsapp_event(sent["message_id"], guest_phone, hotel, sent_at, text, "out")
        return {"success": True, "guest_phone": guest_phone, "body": text, "sent_at": sent_at, "source_ref": sent["message_id"], "direction": "out", "channel": "whatsapp"}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/guests/whatsapp/media")
async def api_guest_whatsapp_media(guest_phone: str = Form(""), hotel_name: str = Form(""), caption: str = Form(""), file: UploadFile = File(...), leon_jwt: str = Cookie(None)):
    """Upload a file to WhatsApp and send it. Free text must be open."""
    try:
        hotel, denied = _guests_hotel(leon_jwt, hotel_name)
        if denied:
            return denied
        phone = str(guest_phone or "").upper()
        if not phone:
            return JSONResponse(content={"error": "guest_phone is required"}, status_code=400)
        from booking_system.guest_manager import guest_manager as gm
        from booking_system.front_desk_manager import intelligent_front_desk
        from booking_system.whatsapp_media import upload_media
        guest = gm.mongo_db["guests"].find_one({"guest_phone": phone, "status": "active"})
        if not guest or str(guest.get("hotel_name") or "").upper() != hotel:
            return JSONResponse(content={"error": "Guest profile not found"}, status_code=404)
        window = gm.whatsapp_text_window(gm.list_guest_messages(hotel, phone))
        if not window["open"]:
            return JSONResponse(content={"error": "Free text is closed"}, status_code=400)
        data = await file.read()
        uploaded = upload_media(file.filename or "file", file.content_type or "", data)
        if uploaded.get("error"):
            return JSONResponse(content={"error": uploaded["error"]}, status_code=400)
        text = str(caption or "").strip()
        sent = intelligent_front_desk.send_whatsapp_media(phone, uploaded["kind"], uploaded["id"], text)
        if sent.get("error"):
            return JSONResponse(content={"error": sent["error"]}, status_code=400)
        sent_at = datetime.now(timezone.utc).isoformat()
        body = text or uploaded["filename"]
        gm.save_whatsapp_event(sent["message_id"], phone, hotel, sent_at, body, "out", "", uploaded["mime"], uploaded["filename"])
        gm.save_guest_media(sent["message_id"], data, uploaded["mime"], uploaded["filename"])
        return {"success": True, "guest_phone": phone, "body": body, "sent_at": sent_at, "source_ref": sent["message_id"], "direction": "out", "channel": "whatsapp", "media_mime": uploaded["mime"], "media_name": uploaded["filename"]}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/guests/whatsapp/media")
async def api_guest_whatsapp_media_file(source_ref: str = Query(""), guest_phone: str = Query(""), hotel_name: str = "", leon_jwt: str = Cookie(None)):
    """The file saved for one WhatsApp message."""
    try:
        hotel, denied = _guests_hotel(leon_jwt, hotel_name)
        if denied:
            return denied
        phone = str(guest_phone or "").upper()
        ref = str(source_ref or "").strip()
        if not phone or not ref:
            return JSONResponse(content={"error": "guest_phone and source_ref are required"}, status_code=400)
        from booking_system.guest_manager import guest_manager as gm
        row = gm.mongo_db["guest_messages"].find_one({"channel": "whatsapp", "source_ref": ref, "hotel_name": hotel})
        if not row or str(row.get("guest_phone") or "").upper() != phone:
            return JSONResponse(content={"error": "Message not found"}, status_code=404)
        stored = gm.read_guest_media(ref)
        if not stored:
            return JSONResponse(content={"error": "File not found"}, status_code=404)
        filename = "".join(ch for ch in str(stored.get("filename") or "file") if ch not in "\"\r\n\\") or "file"
        from urllib.parse import quote
        return Response(content=stored["data"], media_type=stored["mime"], headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/guests/whatsapp/templates")
async def api_guest_whatsapp_templates(guest_phone: str = Query(""), hotel_name: str = "", leon_jwt: str = Cookie(None)):
    """Active WhatsApp templates with the two fields already filled into the sentence."""
    try:
        hotel, denied = _guests_hotel(leon_jwt, hotel_name)
        if denied:
            return denied
        phone = str(guest_phone or "").upper()
        if not phone:
            return JSONResponse(content={"error": "guest_phone is required"}, status_code=400)
        from booking_system.guest_manager import guest_manager as gm
        from booking_system.whatsapp_templates import active_templates
        guest = gm.mongo_db["guests"].find_one({"guest_phone": phone, "status": "active"})
        if not guest or str(guest.get("hotel_name") or "").upper() != hotel:
            return JSONResponse(content={"error": "Guest profile not found"}, status_code=404)
        guest_name = str(guest.get("guest_name") or "").strip()
        rows = active_templates({"guest_name": guest_name, "hotel_name": hotel})
        if isinstance(rows, dict):
            return JSONResponse(content={"error": rows.get("error") or "WhatsApp templates are not available"}, status_code=400)
        return {"templates": rows}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/guests/whatsapp/template")
async def api_guest_whatsapp_template(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Send the template the staff selected. Fields are filled from the guest, not from typed text."""
    try:
        hotel, denied = _guests_hotel(leon_jwt, str((payload or {}).get("hotel_name") or ""))
        if denied:
            return denied
        guest_phone = str((payload or {}).get("guest_phone") or "").upper()
        template_key = str((payload or {}).get("template") or "").strip()
        if not guest_phone or not template_key:
            return JSONResponse(content={"error": "guest_phone and template are required"}, status_code=400)
        from booking_system.guest_manager import guest_manager as gm
        from booking_system.front_desk_manager import intelligent_front_desk
        from booking_system.guest_link import guest_link
        from booking_system.whatsapp_templates import render_template
        guest = gm.mongo_db["guests"].find_one({"guest_phone": guest_phone, "status": "active"})
        if not guest or str(guest.get("hotel_name") or "").upper() != hotel:
            return JSONResponse(content={"error": "Guest profile not found"}, status_code=404)
        guest_name = str(guest.get("guest_name") or "").strip()
        rendered = render_template(template_key, {"guest_name": guest_name, "hotel_name": hotel, "guest_link": guest_link(hotel, guest_phone)})
        if rendered.get("error"):
            return JSONResponse(content={"error": rendered["error"]}, status_code=400)
        sent = intelligent_front_desk.send_whatsapp_template(guest_phone, rendered["template_name"], rendered["language"], rendered["values"], rendered["button"], rendered["sentence"], rendered.get("names"), rendered.get("named"))
        if sent.get("error"):
            return JSONResponse(content={"error": sent["error"]}, status_code=400)
        sent_at = datetime.now(timezone.utc).isoformat()
        gm.save_whatsapp_event(sent["message_id"], guest_phone, hotel, sent_at, sent["body"], "out")
        return {"success": True, "guest_phone": guest_phone, "body": sent["body"], "sent_at": sent_at, "source_ref": sent["message_id"], "direction": "out", "channel": "whatsapp"}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

def _guest_from_link(token: str):
    from booking_system.guest_link import read_guest_link
    from booking_system.guest_manager import guest_manager as gm
    opened = read_guest_link(token)
    if opened.get("error"):
        return None, JSONResponse(content={"error": opened["error"]}, status_code=400)
    guest = gm.mongo_db["guests"].find_one({"guest_phone": opened["guest_phone"], "status": "active"})
    if not guest or str(guest.get("hotel_name") or "").upper() != opened["hotel_name"]:
        return None, JSONResponse(content={"error": "Guest profile not found"}, status_code=404)
    return guest, None

@app.get("/api/guest/biography")
async def api_guest_biography(token: str = Query("")):
    """Biography for the guest who opened the WhatsApp verify link."""
    try:
        guest, denied = _guest_from_link(token)
        if denied:
            return denied
        record = {}
        for key in ("guest_phone", "guest_name", "email", "guest_email", "country", "nationality", "date_of_birth", "gender", "company", "address", "identity_type", "identity_number", "special_request", "hotel_name"):
            record[key] = guest.get(key)
        if not record.get("email"):
            record["email"] = guest.get("guest_email") or ""
        if not record.get("country"):
            record["country"] = guest.get("nationality") or ""
        return record
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/guest/biography")
async def api_guest_biography_save(payload: Dict[str, Any] = Body(...)):
    """Save the biography the guest typed. The link is the login."""
    try:
        guest, denied = _guest_from_link(str((payload or {}).get("token") or ""))
        if denied:
            return denied
        phone = str(guest.get("guest_phone") or "")
        fields = {"last_updated": datetime.now(timezone.utc), "updated_by": {"guest_phone": phone}}
        for key in ("guest_name", "email", "country", "date_of_birth", "gender", "company", "address", "identity_type", "identity_number", "special_request"):
            if key in (payload or {}):
                fields[key] = str((payload or {}).get(key) or "").strip()
        if "guest_name" in fields and not fields["guest_name"]:
            return JSONResponse(content={"error": "Name is required"}, status_code=400)
        if "country" in fields:
            fields["nationality"] = fields["country"]
        if "email" in fields:
            fields["guest_email"] = fields["email"]
        if "special_request" in fields:
            fields["special_requests"] = fields["special_request"]
        from booking_system.guest_manager import guest_manager as gm
        from booking_system.front_desk_manager import intelligent_front_desk
        gm.mongo_db["guests"].update_one({"guest_phone": phone}, {"$set": fields})
        name = fields.get("guest_name") or str(guest.get("guest_name") or "")
        return {"success": True, "whatsapp_url": intelligent_front_desk.whatsapp_click_url(name)}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/guests/messages")
async def api_guest_message(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Save one guest message. Natural key is hotel, phone, channel, sent_at, and source_ref."""
    try:
        hotel, denied = _guests_hotel(leon_jwt, str((payload or {}).get("hotel_name") or ""))
        if denied:
            return denied
        guest_phone = str((payload or {}).get("guest_phone") or "").upper()
        channel = str((payload or {}).get("channel") or "").strip().lower()
        sent_at = str((payload or {}).get("sent_at") or "").strip()
        body = str((payload or {}).get("body") or "")
        direction = str((payload or {}).get("direction") or "").strip().lower()
        source_ref = str((payload or {}).get("source_ref") or "")
        if channel not in ("email", "whatsapp", "voice"):
            return JSONResponse(content={"error": "channel must be email, whatsapp, or voice"}, status_code=400)
        if direction not in ("in", "out"):
            return JSONResponse(content={"error": "direction must be in or out"}, status_code=400)
        if not guest_phone or not sent_at or not body:
            return JSONResponse(content={"error": "guest_phone, sent_at, and body are required"}, status_code=400)
        from booking_system.guest_manager import guest_manager as gm
        guest = gm.mongo_db["guests"].find_one({"guest_phone": guest_phone, "status": "active"})
        if not guest or str(guest.get("hotel_name") or "").upper() != hotel:
            return JSONResponse(content={"error": "Guest profile not found"}, status_code=404)
        result = gm.save_guest_message(hotel, guest_phone, channel, sent_at, body, direction, source_ref)
        return {"result": result, "guest_phone": guest_phone, "channel": channel, "sent_at": sent_at}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/guests/summarize")
async def api_guest_summarize(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Write a guest summary from stays, messages, and saved files."""
    try:
        hotel, denied = _guests_hotel(leon_jwt, str((payload or {}).get("hotel_name") or ""))
        if denied:
            return denied
        guest_phone = str((payload or {}).get("guest_phone") or "").upper()
        if not guest_phone:
            return JSONResponse(content={"error": "guest_phone is required"}, status_code=400)
        written = await run_in_threadpool(_write_guest_needs, hotel, guest_phone)
        if written.get("error"):
            return JSONResponse(content={"error": written["error"]}, status_code=written.get("status") or 502)
        return written
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/guest_profile/create")
async def api_guest_profile_create(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Create guest profile via EnhancedGuestManager.create_guest_profile.
    Expected payload: { guest_phone, guest_name, email?, nationality? }
    """
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "❌ Missing leon_jwt"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h, get_manager_name_from_jwt as _get_m
        hotel = _get_h(leon_jwt); mgr = _get_m(leon_jwt)
        if not isinstance(hotel, str) or hotel.startswith('❌'):
            return JSONResponse(content={"error": "❌ Invalid hotel in token"}, status_code=401)
        if not isinstance(mgr, str) or mgr.startswith('❌'):
            return JSONResponse(content={"error": "❌ Invalid manager in token"}, status_code=401)
        hotel = hotel.upper(); mgr = mgr.upper()
        guest_phone = str((payload or {}).get('guest_phone') or '').upper()
        guest_name = str((payload or {}).get('guest_name') or '').upper()
        email = (payload or {}).get('email')
        nationality = (payload or {}).get('nationality')
        email = str(email).strip() if email is not None else None
        nationality = str(nationality).upper().strip() if nationality is not None else None
        if not guest_phone or not guest_name:
            return JSONResponse(content={"error": "❌ guest_phone and guest_name are required"}, status_code=400)
        from booking_system.guest_manager import guest_manager as gm
        txt = gm.create_guest_profile(hotel, mgr, guest_phone, guest_name, email=email, nationality=nationality)
        if isinstance(txt, str) and txt.strip().startswith("❌"):
            return JSONResponse(content={"error": txt}, status_code=400)
        return {"result": str(txt)}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/rate_plans")
async def list_rate_plans(hotel_name: str = Query(...)):
    """Room packages for a hotel. Natural key is hotel_name + rate_plan."""
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        db = intelligent_front_desk.get_mongodb_connection()
        rows = list(db.get_collection("rate_plans").find(
            {"hotel_name": str(hotel_name or "").upper()},
            {"_id": 0, "rate_plan": 1, "description": 1}
        ))
        return {"rate_plans": rows}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/rate_plans")
async def save_rate_plan(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Create or update a room package. Content lives in Mongo. Natural key is hotel_name + rate_plan."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h
        hotel = _get_h(leon_jwt)
        if not isinstance(hotel, str) or hotel.startswith("❌"):
            return JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
        hotel = hotel.upper()
        rate_plan = str((payload or {}).get("rate_plan") or "").strip().upper()
        description = str((payload or {}).get("description") or "").strip()
        if not rate_plan:
            return JSONResponse(content={"error": "rate_plan is required"}, status_code=400)
        from booking_system.front_desk_manager import intelligent_front_desk
        db = intelligent_front_desk.get_mongodb_connection()
        db.get_collection("rate_plans").update_one(
            {"hotel_name": hotel, "rate_plan": rate_plan},
            {"$set": {"description": description}},
            upsert=True
        )
        return {"hotel_name": hotel, "rate_plan": rate_plan, "description": description}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

def _rate_plan_hotel(leon_jwt: str):
    if not leon_jwt:
        return None, JSONResponse(content={"error": "Unauthorized"}, status_code=401)
    from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h
    hotel = _get_h(leon_jwt)
    if not isinstance(hotel, str) or hotel.startswith("❌"):
        return None, JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
    return hotel.upper(), None

@app.post("/api/rate_plans/update")
async def update_rate_plan(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Rename or rewrite a room package. Natural key is hotel_name + rate_plan."""
    try:
        hotel, denied = _rate_plan_hotel(leon_jwt)
        if denied:
            return denied
        rate_plan = str((payload or {}).get("rate_plan") or "").strip().upper()
        new_rate_plan = str((payload or {}).get("new_rate_plan") or rate_plan).strip().upper()
        description = str((payload or {}).get("description") or "").strip()
        if not rate_plan or not new_rate_plan:
            return JSONResponse(content={"error": "rate_plan is required"}, status_code=400)
        from booking_system.front_desk_manager import intelligent_front_desk
        db = intelligent_front_desk.get_mongodb_connection()
        coll = db.get_collection("rate_plans")
        current = coll.find_one({"hotel_name": hotel, "rate_plan": rate_plan})
        if not current:
            return JSONResponse(content={"error": "Rate plan not found"}, status_code=404)
        if new_rate_plan != rate_plan and coll.find_one({"hotel_name": hotel, "rate_plan": new_rate_plan}):
            return JSONResponse(content={"error": "Rate plan already exists"}, status_code=400)
        coll.delete_one({"hotel_name": hotel, "rate_plan": rate_plan})
        coll.insert_one({"hotel_name": hotel, "rate_plan": new_rate_plan, "description": description})
        from booking_system.booking_manager import booking_manager as _bm
        _bm._ensure_postgres_connection()
        _bm._ensure_booking_channel()
        cur = _bm.postgres_conn.cursor()
        cur.execute(
            "UPDATE room_bookings SET rate_plan = %s WHERE hotel_name = %s AND UPPER(COALESCE(rate_plan, '')) = %s",
            (new_rate_plan, hotel, rate_plan)
        )
        _bm.postgres_conn.commit()
        cur.close()
        return {"hotel_name": hotel, "rate_plan": new_rate_plan, "description": description}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/rate_plans/delete")
async def delete_rate_plan(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Remove a room package. Bookings that used it are cleared."""
    try:
        hotel, denied = _rate_plan_hotel(leon_jwt)
        if denied:
            return denied
        rate_plan = str((payload or {}).get("rate_plan") or "").strip().upper()
        if not rate_plan:
            return JSONResponse(content={"error": "rate_plan is required"}, status_code=400)
        from booking_system.front_desk_manager import intelligent_front_desk
        db = intelligent_front_desk.get_mongodb_connection()
        coll = db.get_collection("rate_plans")
        current = coll.find_one({"hotel_name": hotel, "rate_plan": rate_plan})
        if not current:
            return JSONResponse(content={"error": "Rate plan not found"}, status_code=404)
        coll.delete_one({"hotel_name": hotel, "rate_plan": rate_plan})
        from booking_system.booking_manager import booking_manager as _bm
        _bm._ensure_postgres_connection()
        _bm._ensure_booking_channel()
        cur = _bm.postgres_conn.cursor()
        cur.execute(
            "UPDATE room_bookings SET rate_plan = '' WHERE hotel_name = %s AND UPPER(COALESCE(rate_plan, '')) = %s",
            (hotel, rate_plan)
        )
        _bm.postgres_conn.commit()
        cur.close()
        return {"hotel_name": hotel, "rate_plan": rate_plan, "removed": True}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/guest_profile/update")
async def api_guest_profile_update(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Update an existing guest profile. Natural key is guest_phone."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
        mgr = _get_m(leon_jwt)
        if not isinstance(mgr, str) or mgr.startswith("❌"):
            return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
        guest_phone = str((payload or {}).get("guest_phone") or "").upper()
        if not guest_phone:
            return JSONResponse(content={"error": "guest_phone is required"}, status_code=400)
        from booking_system.guest_manager import guest_manager as gm
        from datetime import datetime, timezone
        existing = gm.mongo_db["guests"].find_one({"guest_phone": guest_phone})
        if not existing:
            return JSONResponse(content={"error": "Guest profile not found"}, status_code=404)
        fields = {"last_updated": datetime.now(timezone.utc), "updated_by": {"manager_name": mgr.upper()}}
        for key in ("guest_name", "email", "nationality", "country", "date_of_birth", "gender", "company", "address", "identity_type", "identity_number", "special_request"):
            if key in (payload or {}):
                fields[key] = str((payload or {}).get(key) or "").strip()
        if "guest_name" in fields:
            if not fields["guest_name"]:
                return JSONResponse(content={"error": "guest_name is required"}, status_code=400)
            fields["guest_name"] = fields["guest_name"].upper()
        if "nationality" in fields:
            fields["nationality"] = fields["nationality"].upper()
        if "country" in fields and "nationality" not in fields:
            fields["nationality"] = fields["country"].upper()
        if "email" in fields:
            fields["guest_email"] = fields["email"]
        if "special_request" in fields:
            fields["special_requests"] = fields["special_request"]
        if "vip" in (payload or {}):
            fields["vip"] = bool((payload or {}).get("vip"))
        if len(fields) <= 2:
            return JSONResponse(content={"error": "No fields to update"}, status_code=400)
        gm.mongo_db["guests"].update_one({"guest_phone": guest_phone}, {"$set": fields})
        return {"result": "Updated"}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/guest_profile/deactivate")
async def api_guest_profile_deactivate(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Mark a guest profile inactive. Natural key is guest_phone."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m
        mgr = _get_m(leon_jwt)
        if not isinstance(mgr, str) or mgr.startswith("❌"):
            return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
        guest_phone = str((payload or {}).get("guest_phone") or "").upper()
        if not guest_phone:
            return JSONResponse(content={"error": "guest_phone is required"}, status_code=400)
        from booking_system.guest_manager import guest_manager as gm
        from datetime import datetime, timezone
        existing = gm.mongo_db["guests"].find_one({"guest_phone": guest_phone})
        if not existing:
            return JSONResponse(content={"error": "Guest profile not found"}, status_code=404)
        gm.mongo_db["guests"].update_one(
            {"guest_phone": guest_phone},
            {"$set": {"status": "inactive", "deactivated_at": datetime.now(timezone.utc), "deactivated_by": {"manager_name": mgr.upper()}}},
        )
        return {"result": "Inactive"}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/analytics")
async def api_analytics(days: int = 30, leon_jwt: str = Cookie(None)):
    """Occupancy and room revenue for the signed-in hotel."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_h
        hotel = _get_h(leon_jwt)
        if not isinstance(hotel, str) or hotel.startswith("❌"):
            return JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
        from booking_system.booking_manager import booking_manager as _bm
        return _bm.read_hotel_analytics(hotel.upper(), days)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/hotels/{hotel_id}/analytics")
async def get_hotel_analytics(hotel_id: str, days: int = 30, leon_jwt: str = Cookie(None)):
    """Get analytics for the signed-in hotel."""
    return await api_analytics(days=days, leon_jwt=leon_jwt)

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
async def get_inventory(start_date: str = None, end_date: str = None, leon_jwt: str = Cookie(None)):
    """Return inventory summary and pricing for the authenticated hotel."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"room_types": [], "error": "Unauthorized"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
        hotel_name = get_hotel_name_from_jwt(leon_jwt)
        if not isinstance(hotel_name, str) or hotel_name.startswith('❌'):
            return JSONResponse(content={"room_types": [], "error": "Invalid hotel in token"}, status_code=401)
        hotel_name_up = (hotel_name or "").upper()
        from booking_system.inventory_manager import inventory_manager
        if start_date and end_date:
            calendar = inventory_manager.read_calendar(hotel_name_up, start_date, end_date)
            if calendar.get("error") and not calendar.get("room_types"):
                return {"room_types": [], "error": calendar["error"]}
            try:
                from booking_system.currency_utils import get_currency_from_jwt
                currency = get_currency_from_jwt(leon_jwt) or ""
            except Exception:
                currency = ""
            try:
                from booking_system.kite_pricing import kite_pricing
                kite_pricing.attach_quotes(
                    hotel_name_up,
                    start_date,
                    end_date,
                    calendar.get("room_types") or [],
                    inventory_manager.postgres_conn,
                )
            except Exception:
                pass
            for room in calendar.get("room_types") or []:
                for row in room.get("pricing") or []:
                    row["currency"] = currency
            return {"room_types": calendar.get("room_types") or []}
        # Read available room types (and inventory totals) for this hotel
        avail_text = inventory_manager.read_available_room_types(hotel_name_up)
        if isinstance(avail_text, str) and avail_text.startswith("❌"):
            return {"room_types": [], "error": avail_text}
        room_types = []
        current_room = None
        total_val = None
        active_val = None
        updated_val = None
        lines = str(avail_text or "").split('\n')
        for raw in lines:
            line = raw.strip()
            # Header line: "• {ROOM_TYPE} (STATUS)"
            if line.startswith('• '):
                # Flush previous
                if current_room is not None:
                    room_types.append({
                        "room_type": current_room
                    })
                try:
                    current_room = line.split('•', 1)[1].strip()
                    if ' (' in current_room:
                        current_room = current_room.split(' (', 1)[0].strip()
                except Exception:
                    current_room = None
                total_val = None; active_val = None; updated_val = None
                continue
            # Detail totals line: "- Total: X | Blocked: Y | Active: Z"
            if line.startswith('- Total:'):
                try:
                    # Extract Total and Active
                    # Example: "- Total: 10 | Blocked: 2 | Active: 8"
                    parts = line.replace('- ', '').split('|')
                    for p in parts:
                        p = p.strip()
                        if p.lower().startswith('total:'):
                            total_val = int(p.split(':', 1)[1].strip())
                        if p.lower().startswith('active:'):
                            active_val = int(p.split(':', 1)[1].strip())
                except Exception:
                    pass
                continue
            # Updated line: "- Updated: 2025-08-22 ..."
            if line.startswith('- Updated:'):
                try:
                    updated_val = line.split(':', 1)[1].strip()
                except Exception:
                    updated_val = None
                continue
        # Flush last entry
        if current_room is not None:
            room_types.append({
                "room_type": current_room
            })
        # Attach pricing and date-based quantities for each room type
        try:
            from booking_system.currency_utils import get_currency_from_jwt
            currency = get_currency_from_jwt(leon_jwt) or ""
        except Exception:
            currency = ""
        for rt in room_types:
            pricing = []
            quantities = []
            try:
                pricing_result = inventory_manager.read_room_price(hotel_name_up, rt["room_type"], start_date, end_date)
                if isinstance(pricing_result, str) and not pricing_result.startswith('❌'):
                    for pline in pricing_result.split('\n'):
                        pl = pline.strip()
                        if pl.startswith('•') and '/night' in pl:
                            date = ''
                            price = 0.0
                            updated_at = ""
                            try:
                                date = pl.split(':', 1)[0].replace('•', '').strip()
                                price_part = pl.split(':', 1)[1].split('/night')[0].replace('$', '').strip()
                                price = float(price_part)
                            except Exception:
                                pass
                            if 'updated' in pl:
                                try:
                                    updated_at = pl.split('updated', 1)[1].replace(')', '').strip()
                                except Exception:
                                    updated_at = ""
                            saved_price = price
                            if 'saved' in pl:
                                try:
                                    saved_price = float(pl.split('saved', 1)[1].split('/night')[0].replace('$', '').strip())
                                except Exception:
                                    saved_price = price
                            pricing.append({"date": date, "price": price, "saved_price": saved_price, "currency": currency, "updated_at": updated_at})
                if start_date and end_date and pricing:
                    try:
                        from booking_system.kite_pricing import kite_pricing
                        quoted = kite_pricing.quotes_for_range(hotel_name_up, rt["room_type"], start_date, end_date)
                        by_day = {item["stay_date"]: item for item in quoted}
                        for row in pricing:
                            quote = by_day.get(str(row["date"])[:10])
                            if quote:
                                row["base_price"] = quote["base_price"]
                                row["dynamic_price"] = quote["dynamic_price"]
                                row["push_dynamic"] = quote["push_dynamic"]
                                row["pricing_name"] = quote.get("pricing_name") or ""
                    except Exception:
                        pass
                # read date-based quantities
                try:
                    start_q = start_date or (pricing[0]["date"] if pricing else datetime.now(timezone.utc).date().isoformat())
                    end_q = end_date or (pricing[-1]["date"] if pricing else datetime.now(timezone.utc).date().isoformat())
                    q = inventory_manager.read_nightly_inventory_range(hotel_name_up, rt["room_type"], start_q, end_q)
                except Exception:
                    q = {"items": []}
                if isinstance(q, dict) and isinstance(q.get("items"), list):
                    quantities = q["items"]
            except Exception:
                pricing = []
                quantities = []
            rt["pricing"] = pricing
            rt["quantities"] = quantities
        return {"room_types": room_types}
    except Exception as e:
        return JSONResponse(content={"room_types": [], "error": str(e)}, status_code=500)

@app.get("/api/hotels/{hotel_id}/pricing")
async def get_hotel_pricing(hotel_id: str, room_type: str = None, days: int = 7, start_date: str = None):
    """Get pricing. If room_type and start_date provided, return price for that date.
    Response (specific date): { price: float, currency: str }
    Fallback: { pricing: [...] }
    """
    try:
        from datetime import datetime, timedelta, timezone
        from booking_system.inventory_manager import inventory_manager
        hotel_name = hotel_id.replace('_', ' ').upper()
        if room_type and start_date:
            txt = inventory_manager.read_room_price(hotel_name, str(room_type).upper(), start_date, start_date)
            price = None
            try:
                for line in str(txt).splitlines():
                    s = line.strip()
                    if s.startswith('•') and '$' in s and '/night' in s:
                        price_part = s.split('$',1)[1].split('/night',1)[0]
                        price = float(price_part)
                        break
            except Exception:
                pass
            return {"price": float(price or 0.0), "currency": ""}
        if not start_date:
            start_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = start_dt + timedelta(days=max(int(days or 1),1)-1)
        end_date = end_dt.strftime('%Y-%m-%d')
        if not room_type:
            return {"pricing": []}
        txt = inventory_manager.read_room_price(hotel_name, str(room_type).upper(), start_date, end_date)
        sample_price = 0.0
        has_pricing = False
        try:
            for line in str(txt).splitlines():
                s = line.strip()
                if s.startswith('•') and '$' in s and '/night' in s:
                    price_part = s.split('$',1)[1].split('/night',1)[0]
                    sample_price = float(price_part)
                    has_pricing = True
                    break
        except Exception:
            pass
        return {"pricing": [{"room_type": str(room_type).upper(), "has_pricing": has_pricing, "sample_price": sample_price, "currency": "", "period": f"{days} days"}]}
    except Exception as e:
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

@app.get("/api/kite")
async def read_kite_setting(leon_jwt: str = Cookie(None)):
    """Read hotel-wide INAP dynamic pricing controls."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
        hotel_name = get_hotel_name_from_jwt(leon_jwt)
        if not isinstance(hotel_name, str) or hotel_name.startswith("❌"):
            return JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
        from booking_system.kite_pricing import kite_pricing
        return kite_pricing.read_setting(hotel_name)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/kite")
async def save_kite_setting(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Save the hotel-wide switch, sensitivity, and price limits."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt, get_manager_name_from_jwt
        hotel_name = get_hotel_name_from_jwt(leon_jwt)
        manager_name = get_manager_name_from_jwt(leon_jwt)
        if not isinstance(hotel_name, str) or hotel_name.startswith("❌"):
            return JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
        if not isinstance(manager_name, str) or manager_name.startswith("❌"):
            return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
        from booking_system.kite_pricing import kite_pricing
        result = kite_pricing.save_setting(
            hotel_name,
            bool((payload or {}).get("enabled")),
            (payload or {}).get("sensitivity"),
            (payload or {}).get("lower_limit"),
            (payload or {}).get("upper_limit"),
            manager_name,
        )
        if isinstance(result, dict) and result.get("error"):
            return JSONResponse(content=result, status_code=400)
        return result
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/kite/day")
async def save_kite_push_day(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Choose whether one stay date pushes the dynamic price or the base price."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
        hotel_name = get_hotel_name_from_jwt(leon_jwt)
        if not isinstance(hotel_name, str) or hotel_name.startswith("❌"):
            return JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
        stay_date = str((payload or {}).get("stay_date") or "").strip()
        if not stay_date:
            return JSONResponse(content={"error": "stay_date is required"}, status_code=400)
        from booking_system.dynamic_pricing import dynamic_pricing
        from booking_system.kite_pricing import kite_pricing
        from aiosell_channel_manager import push_rates_for_date
        pricing_name = str((payload or {}).get("pricing_name") or "").strip()
        if pricing_name:
            found = dynamic_pricing.read_one(hotel_name, pricing_name)
            if found.get("missing"):
                return JSONResponse(content={"error": "No dynamic pricing with that name"}, status_code=400)
        saved = await run_in_threadpool(
            kite_pricing.set_push_day,
            hotel_name,
            stay_date,
            bool((payload or {}).get("push_dynamic")),
            pricing_name or None,
        )
        pushed = await run_in_threadpool(push_rates_for_date, hotel_name, stay_date)
        saved["aiosell"] = pushed
        return saved
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/kite/days")
async def save_kite_push_days(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Set the dynamic-or-base choice for many stay dates at once."""
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
        hotel_name = get_hotel_name_from_jwt(leon_jwt)
        if not isinstance(hotel_name, str) or hotel_name.startswith("❌"):
            return JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
        stay_dates = (payload or {}).get("stay_dates") or []
        if not isinstance(stay_dates, list) or not stay_dates:
            return JSONResponse(content={"error": "stay_dates is required"}, status_code=400)
        from booking_system.dynamic_pricing import dynamic_pricing
        from booking_system.kite_pricing import kite_pricing
        from aiosell_channel_manager import push_rates_for_dates
        pricing_name = str((payload or {}).get("pricing_name") or "").strip()
        if pricing_name:
            found = dynamic_pricing.read_one(hotel_name, pricing_name)
            if found.get("missing"):
                return JSONResponse(content={"error": "No dynamic pricing with that name"}, status_code=400)
        push_dynamic = bool((payload or {}).get("push_dynamic"))
        saved = await run_in_threadpool(
            kite_pricing.set_push_dates,
            hotel_name,
            stay_dates,
            push_dynamic,
            pricing_name or None,
        )
        saved["aiosell"] = await run_in_threadpool(push_rates_for_dates, hotel_name, saved.get("stay_dates") or [])
        return saved
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

def _pricing_hotel(leon_jwt):
    if not leon_jwt:
        return None, JSONResponse(content={"error": "Unauthorized"}, status_code=401)
    from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt
    hotel_name = get_hotel_name_from_jwt(leon_jwt)
    if not isinstance(hotel_name, str) or hotel_name.startswith("❌"):
        return None, JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
    return hotel_name, None

@app.get("/api/dynamic_pricing")
async def list_dynamic_pricing(leon_jwt: str = Cookie(None)):
    """List named dynamic pricing setups for this hotel."""
    try:
        hotel_name, error = _pricing_hotel(leon_jwt)
        if error:
            return error
        from booking_system.dynamic_pricing import dynamic_pricing
        return {"pricings": dynamic_pricing.list_for_hotel(hotel_name)}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/dynamic_pricing")
async def save_dynamic_pricing(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Create a named pricing, or update it when the name already exists."""
    try:
        hotel_name, error = _pricing_hotel(leon_jwt)
        if error:
            return error
        from booking_system.dynamic_pricing import dynamic_pricing
        result = dynamic_pricing.save(
            hotel_name,
            (payload or {}).get("pricing_name"),
            (payload or {}).get("model"),
            bool((payload or {}).get("enabled")),
            (payload or {}).get("sensitivity"),
            (payload or {}).get("lower_limit"),
            (payload or {}).get("upper_limit"),
        )
        if isinstance(result, dict) and result.get("error"):
            return JSONResponse(content=result, status_code=400)
        if isinstance(result, dict) and result.get("clarify"):
            return JSONResponse(content=result, status_code=409)
        return result
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.delete("/api/dynamic_pricing")
async def remove_dynamic_pricing(pricing_name: str, leon_jwt: str = Cookie(None)):
    """Delete one named pricing when the name matches."""
    try:
        hotel_name, error = _pricing_hotel(leon_jwt)
        if error:
            return error
        from booking_system.dynamic_pricing import dynamic_pricing
        from booking_system.kite_pricing import kite_pricing
        result = dynamic_pricing.delete(hotel_name, pricing_name)
        if isinstance(result, dict) and result.get("error"):
            return JSONResponse(content=result, status_code=400)
        kite_pricing.clear_pricing_name(hotel_name, result.get("deleted"))
        return result
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

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
async def update_inventory_date(hotel_id: str, request: Request, leon_jwt: str = Cookie(None)):
    """Update inventory quantity and price for specific date"""
    try:
        data = await request.json()
        room_type = data.get('room_type')
        date = data.get('date')
        quantity = data.get('quantity')
        price = data.get('price')
        
        if not all([room_type, date, price is not None]):
            return {"success": False, "error": "Missing required fields"}
        
        # Convert hotel_id back to hotel name
        hotel_name = hotel_id.replace('_', ' ').upper()
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_mgr
            _mgr = _get_mgr(leon_jwt)
            if not isinstance(_mgr, str) or _mgr.startswith('❌'):
                return {"success": False, "error": "Unauthorized"}
            manager_name = _mgr.upper()
        except Exception:
            return {"success": False, "error": "Unauthorized"}

        # 1) Update price for the specific date (create or update)
        try:
            _ = inventory_manager.update_room_price(hotel_name, manager_name, str(room_type).upper(), str(date), float(price))
        except Exception as _:
            pass

        # Quantity is the room count for this room type. Stop-sell is the only manual quantity, and it blocks every room.
        rooms = 0
        try:
            rooms = inventory_manager.count_rooms(hotel_name, str(room_type).upper(), str(date))
            current = inventory_manager._validate_and_read_nightly_inventory(hotel_name, str(room_type).upper(), str(date))
            blocked = 0
            if current.get("exists"):
                blocked = int((current.get("data") or {}).get("blocked_quantity") or 0)
            if quantity is not None and int(quantity) == 0:
                blocked = rooms
            blocked = min(blocked, rooms)
            if current.get("exists"):
                _ = inventory_manager.update_nightly_inventory(
                    hotel_name,
                    manager_name,
                    str(room_type).upper(),
                    str(date),
                    total_inventory=rooms,
                    blocked_quantity=blocked
                )
            else:
                _ = inventory_manager.manage_room_quantity(hotel_name, manager_name, str(room_type).upper(), str(date), str(date), rooms, blocked)
        except Exception:
            pass

        aiosell = await _push_aiosell(hotel_name, [str(date)[:10]])
        return {"success": True, "message": f"Updated {room_type} for {date}: {rooms} rooms at {price}", "aiosell": aiosell}
            
    except Exception as e:
        print(f"Error updating inventory date: {str(e)}")
        return {"success": False, "error": str(e)}

@app.post("/api/hotels/{hotel_id}/inventory/update-dates")
async def update_inventory_dates(hotel_id: str, request: Request, leon_jwt: str = Cookie(None)):
    """Update inventory quantity and price for many dates in one request."""
    try:
        data = await request.json()
        room_type = data.get("room_type")
        dates = data.get("dates")
        quantity = data.get("quantity")
        price = data.get("price")
        if not room_type or not isinstance(dates, list) or not dates or price is None:
            return {"success": False, "error": "Missing required fields"}
        hotel_name = hotel_id.replace("_", " ").upper()
        try:
            from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_mgr
            _mgr = _get_mgr(leon_jwt)
            if not isinstance(_mgr, str) or _mgr.startswith("❌"):
                return {"success": False, "error": "Unauthorized"}
            manager_name = _mgr.upper()
        except Exception:
            return {"success": False, "error": "Unauthorized"}
        result = await run_in_threadpool(
            inventory_manager.update_dates,
            hotel_name,
            manager_name,
            str(room_type).upper(),
            dates,
            price,
            quantity,
        )
        if isinstance(result, dict) and result.get("error"):
            return {"success": False, "error": result["error"]}
        if isinstance(result, dict):
            result["aiosell"] = await _push_aiosell(hotel_name, dates)
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}

# --- Global Activity Logs (human-readable GET/POST tracking) ---
from collections import deque
from datetime import datetime, timezone
from typing import Dict

ACTIVITY_LOGS = deque(maxlen=1000)

def _humanize_post_request(path: str, payload: Dict[str, object]) -> str:
    try:
        # Assignments
        if path.endswith('/api/assignments/update'):
            bid = payload.get('booking_id') or ''
            g = payload.get('guest') or {}
            b = payload.get('booking') or {}
            name = (g or {}).get('guest_name') or ''
            notes = (b or {}).get('booking_notes')
            bits = [f"Update booking {bid}"]
            if name: bits.append(f"guest_name='{str(name)[:40]}'")
            if notes: bits.append("notes=updated")
            return ' | '.join(bits)
        if path.endswith('/api/assignments/reassign'):
            bid = payload.get('booking_id') or ''
            sd = payload.get('stay_date') or ''
            old_r = payload.get('old_room_number') or ''
            new_r = payload.get('new_room_number') or ''
            return f"Reassign {bid} on {sd} {old_r or '(UNASSIGNED)'} → {new_r or '(UNASSIGNED)'}"
        if path.endswith('/api/assignments/cancel'):
            bid = payload.get('booking_id') or ''
            return f"Cancel booking {bid}"
        if path.endswith('/api/assignments/check_in'):
            bid = payload.get('booking_id') or ''
            rn = payload.get('room_number') or ''
            return f"Check-in booking {bid} room {rn or '(ASSIGNED)'}"
        if path.endswith('/api/assignments/check_out'):
            bid = payload.get('booking_id') or ''
            rn = payload.get('room_number') or ''
            return f"Check-out booking {bid} room {rn or '(ASSIGNED)'}"
        # Payments & pricing
        if path.endswith('/api/payments/update'):
            bid = payload.get('booking_id') or ''
            amt = payload.get('payment_amount')
            method = payload.get('payment_method') or ''
            try:
                amt_str = f"{float(amt):.2f}"
            except Exception:
                amt_str = str(amt)
            return f"Payment {bid} amount={amt_str} method={method}"
        if path.endswith('/api/pricing/override'):
            bid = payload.get('booking_id') or ''
            new_total = payload.get('new_total_amount')
            try:
                nt = f"{float(new_total):.2f}"
            except Exception:
                nt = str(new_total)
            return f"Pricing override {bid} total={nt}"
        # Maintenance
        if path.endswith('/api/maintenance/create'):
            hotel = (payload.get('hotel_name') or '')
            rn = payload.get('room_number') or ''
            issue = payload.get('issue') or ''
            urg = str(payload.get('urgency') or '').upper()
            short_issue = str(issue)[:60]
            return f"Create maintenance room {rn} issue='{short_issue}' urgency={urg} hotel={(hotel or '')}"
        if path.endswith('/api/maintenance/update'):
            tid = payload.get('task_id') or ''
            status = str(payload.get('status') or '').upper()
            assignee = payload.get('assigned_to_phone') or ''
            has_notes = bool((payload.get('progress_notes') or '') or (payload.get('resolution_description') or ''))
            bits = [f"Update maintenance {tid} status={status}"]
            if assignee:
                bits.append(f"assigned_to={assignee}")
            if has_notes:
                bits.append("notes=updated")
            return ' | '.join(bits)
        # Rooms
        if path.endswith('/api/rooms/add'):
            hotel = (payload.get('hotel_name') or '')
            rt = payload.get('room_type') or ''
            rooms = payload.get('room_numbers') or payload.get('room_number') or ''
            if isinstance(rooms, list): rooms = ','.join([str(x) for x in rooms])
            return f"Add room(s) {rooms} type={rt} hotel={(hotel or '')}"
        if path.endswith('/api/rooms/types/create'):
            hotel = (payload.get('hotel_name') or '')
            rt = payload.get('room_type') or ''
            return f"Create room type {rt} hotel={(hotel or '')}"
        # Default fallback
        keys = ', '.join(list(payload.keys())[:5]) if isinstance(payload, dict) else ''
        return f"POST {path} {{{keys}}}"
    except Exception:
        return f"POST {path}"

@app.middleware("http")
async def _activity_log_middleware(request: _FastAPIRequest, call_next):
    start_utc = datetime.now(timezone.utc)
    # Register timezone per request (JWT-based). No-op now; rely on per-call decoding where needed.
    # Capture body for POST so downstream can still read it
    raw_body = b""
    if request.method == 'POST' and request.url.path.startswith('/api/'):
        try:
            raw_body = await request.body()
            async def receive():
                return {"type": "http.request", "body": raw_body}
            request = _FastAPIRequest(request.scope, receive)
        except Exception:
            raw_body = b""
    response = await call_next(request)
    try:
        path = request.url.path
        if path == '/api/activity/logs':
            return response
        # Log POST (exclude auth)
        if (
            request.method == 'POST'
            and path.startswith('/api/')
            and not path.startswith('/api/auth/')
            and not path.startswith('/api/chat/')
            and path not in (
                '/api/navigation/simple',
                '/api/reset',
                '/api/assignments/preview_bulk'
            )
        ):
            payload = {}
            try:
                if raw_body:
                    payload = json.loads(raw_body.decode('utf-8'))
            except Exception:
                payload = {}
            text = _humanize_post_request(path, payload if isinstance(payload, dict) else {})
            status_code = getattr(response, 'status_code', None)
            # Only record successful entries
            if status_code == 200:
                # Skip non-humanized generic entries like "POST /api/..."
                try:
                    if isinstance(text, str) and text.startswith('POST /api/'):
                        return response
                except Exception:
                    pass
                # Enrich with hotel/manager from JWT cookie if available
                hotel = None; mgr = None
                try:
                    jwt = request.cookies.get('leon_jwt')
                    if jwt:
                        try:
                            from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_hotel, get_manager_name_from_jwt as _get_mgr
                            _h = _get_hotel(jwt)
                            _m = _get_mgr(jwt)
                            if isinstance(_h, str) and not _h.startswith('❌'):
                                hotel = _h.upper()
                            if isinstance(_m, str) and not _m.startswith('❌'):
                                mgr = _m.upper()
                        except Exception:
                            pass
                except Exception:
                    pass
                record_activity(text, hotel_name=hotel, manager_name=mgr, leon_jwt=request.cookies.get('leon_jwt'), actor=mgr)
    except Exception:
        pass
    return response

@app.get('/api/activity/logs/by_hotel')
async def get_activity_logs_by_hotel(limit: int = 100, leon_jwt: str = _Cookie(None)):
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        try:
            from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_hotel
            hotel = _get_hotel(leon_jwt)
            if not isinstance(hotel, str) or hotel.startswith('❌'):
                return JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
            hotel = hotel.upper()
        except Exception:
            return JSONResponse(content={"error": "Invalid token"}, status_code=401)
        # Prefer running instance
        data = None
        try:
            global leon_instance
            if leon_instance and hasattr(leon_instance, 'read_activity_logs_internal'):
                data = leon_instance.read_activity_logs_internal(hotel, limit)
        except Exception:
            data = None
        if data is None:
            try:
                from booking_system.leon_meta_orchestrator import orchestrator
                data = orchestrator.read_activity_logs_internal(hotel, limit)
            except Exception:
                data = {"items": []}
        # Add compatibility alias 'ts' if only ts_utc exists
        try:
            items = data.get("items") or []
            for it in items:
                if "ts" not in it and "ts_utc" in it:
                    it["ts"] = it.get("ts_utc")
        except Exception:
            pass
        try:
            tz = _get_tz_from_jwt(leon_jwt) or "UTC"
        except Exception:
            tz = "UTC"
        data["timezone"] = tz
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
# --- End Global Activity Logs ---

@app.get("/api/assignments/calendar/checkins")
async def get_assignments_calendar_checkins(
    hotel_name: str = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    room_type: Optional[str] = Query(None),
    room_numbers: Optional[str] = Query(None)
):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        rooms_list = None
        if room_numbers:
            rooms_list = [x.strip() for x in room_numbers.split(',') if x.strip()]
        clean_hotel = (hotel_name or "").strip().strip('"').strip("'")
        try:
            clean_hotel = urllib.parse.unquote(clean_hotel)
        except Exception:
            pass
        data = intelligent_front_desk.read_room_number_assignments_calendar_internal(
            (clean_hotel or "").upper(), start_date, end_date, room_type, rooms_list
        )
        # Transform to only checkins
        rooms = []
        for r in data.get("rooms", []):
            halves = r.get("halves", {}) or {}
            out_halves = {}
            for d, h in halves.items():
                hi = h or {}
                chk = hi.get("checkin") or hi.get("check_in")
                if chk:
                    out_halves[d] = {"checkin": chk}
            rooms.append({"room_number": r.get("room_number"), "room_type": r.get("room_type"), "halves": out_halves})
        return JSONResponse(content={
            "hotel_name": data.get("hotel_name"),
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date"),
            "days": data.get("days", []),
            "rooms": rooms
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/assignments/calendar/checkouts")
async def get_assignments_calendar_checkouts(
    hotel_name: str = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    room_type: Optional[str] = Query(None),
    room_numbers: Optional[str] = Query(None)
):
    try:
        from booking_system.front_desk_manager import intelligent_front_desk
        rooms_list = None
        if room_numbers:
            rooms_list = [x.strip() for x in room_numbers.split(',') if x.strip()]
        clean_hotel = (hotel_name or "").strip().strip('"').strip("'")
        try:
            clean_hotel = urllib.parse.unquote(clean_hotel)
        except Exception:
            pass
        data = intelligent_front_desk.read_room_number_assignments_calendar_internal(
            (clean_hotel or "").upper(), start_date, end_date, room_type, rooms_list
        )
        # Transform to only checkouts
        rooms = []
        for r in data.get("rooms", []):
            halves = r.get("halves", {}) or {}
            out_halves = {}
            for d, h in halves.items():
                hi = h or {}
                chk = hi.get("checkout") or hi.get("check_out")
                if chk:
                    out_halves[d] = {"checkout": chk}
            rooms.append({"room_number": r.get("room_number"), "room_type": r.get("room_type"), "halves": out_halves})
        return JSONResponse(content={
            "hotel_name": data.get("hotel_name"),
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date"),
            "days": data.get("days", []),
            "rooms": rooms
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class UpdateReadinessPayload(BaseModel):
    hotel_name: Optional[str] = None
    room_number: str
    new_status: str
    notes: Optional[str] = None

@app.post("/api/rooms/readiness_status/update")
async def update_readiness_status(payload: UpdateReadinessPayload, leon_jwt: str = Cookie(None)):
    try:
        if not leon_jwt:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        from booking_system.leon_meta_orchestrator import get_manager_name_from_jwt as _get_m, get_hotel_name_from_jwt as _get_h
        manager = _get_m(leon_jwt)
        if not isinstance(manager, str) or manager.startswith("❌"):
            return JSONResponse(content={"error": "Invalid manager in token"}, status_code=401)
        hotel = (payload.hotel_name or _get_h(leon_jwt) or "").upper()
        if not hotel or not payload.room_number or not payload.new_status:
            return JSONResponse(content={"error": "Missing parameters (hotel_name, room_number, new_status)"}, status_code=400)
        # Call internal updater
        from booking_system.front_desk_manager import intelligent_front_desk
        msg = intelligent_front_desk.update_room_readiness_status_internal(
            hotel,
            manager.upper(),
            str(payload.room_number),
            str(payload.new_status).lower(),
            str(payload.notes or "")
        )
        if isinstance(msg, str) and msg.strip().startswith("❌"):
            return JSONResponse(content={"error": msg}, status_code=400)
        return JSONResponse(content={"result": msg})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

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
        port=int(os.environ.get("PORT", 8002)),
        log_level="info"
    ) 