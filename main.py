#!/usr/bin/env python3
"""LEON server. Every route is public, an account route, or has an access point checked by booking_system.access.guard;
area routes live in booking_system/routes and the department routers (finance, F&B)."""

import os
import sys
import json
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Body, Cookie, Depends
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
from starlette.concurrency import run_in_threadpool

load_dotenv(override=True)
os.environ.setdefault("PGCONNECT_TIMEOUT", "10")
print("LEON process starting", flush=True)
sys.path.append(os.path.join(os.path.dirname(__file__), 'booking_system'))

from booking_system import access, user_manager
from booking_system.leon_meta_orchestrator import LEONMetaOrchestrator
from navigation_agent_simple import fast_decide_navigation
try:
    from openai import OpenAI as _OpenAI
except Exception:
    _OpenAI = None


def _jwt_payload(token: str):
    return access.decode(token)


def _get_tz_from_jwt(token: str) -> str:
    payload = access.decode(token)
    return payload.get('hotel_timezone') if payload else None


def _hotel_from_jwt(token: str) -> str:
    payload = access.decode(token)
    return str(payload.get('hotel_name') or "").strip().upper() if payload else ""


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="LEON PMS Chat Server",
    description="Chat API server for LEON PMS UI integration",
    version="1.0.0"
)

_COUNTRY_CODES_CACHE = None
_COUNTRY_CODES_CACHE_AT = None
_LANGUAGES_CACHE = None
_LANGUAGES_CACHE_AT = None

# Middleware runs outermost-last-added: CORS, then bearer -> cookie, then the access guard.
app.middleware("http")(access.guard)


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
            if hasattr(request, "_cookies"):
                delattr(request, "_cookies")
    return await call_next(request)


_origins_env = os.getenv('FRONTEND_ORIGINS', 'http://localhost:8080,http://127.0.0.1:8080')
_origins_list = [o.strip() for o in _origins_env.split(',') if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    staff_id: Optional[str] = "Hotel Manager"
    context: Optional[Dict[str, Any]] = {}


class ChatResponse(BaseModel):
    messages: List[str]
    staff_id: str
    success: bool = True
    action: Optional[Dict[str, Any]] = None


class SignupRequest(BaseModel):
    manager_name: str
    password: str
    primary_language: str
    nationality: Optional[str] = None
    manager_phone: Optional[str] = None
    email: Optional[str] = None
    username: Optional[str] = None
    hotel_name: Optional[str] = None
    hotel_timezone: Optional[str] = None
    hotel_currency: Optional[str] = None
    city_name: Optional[str] = None


class SigninRequest(BaseModel):
    auth_type: str  # phone | email | username
    auth_value: str
    password: str
    hotel_id: Optional[str] = None


class OtpSendRequest(BaseModel):
    channel: str  # whatsapp | email
    address: str


class OtpConfirmRequest(BaseModel):
    channel: str  # whatsapp | email
    address: str
    code: str


class SwitchHotelRequest(BaseModel):
    hotel_id: str


leon_instance = None


@app.on_event("startup")
async def startup_event():
    """Schema, the route access check, the job runner, then the orchestrator. A failure here stops the server."""
    global leon_instance
    print("LEON server startup", flush=True)
    from setup_schema import setup_schema
    await run_in_threadpool(setup_schema)
    points = await run_in_threadpool(access.check_routes, app)
    print("access points checked: " + str(points), flush=True)
    from booking_system import jobs
    jobs.start()
    leon_instance = LEONMetaOrchestrator()
    import booking_system.leon_meta_orchestrator as _lmo
    _lmo.orchestrator = leon_instance
    print("LEON Chat Server initialized", flush=True)


@app.on_event("shutdown")
async def shutdown_event():
    from booking_system import jobs
    jobs.stop()
    if leon_instance and hasattr(leon_instance, 'cleanup'):
        leon_instance.cleanup()


@app.get("/")
async def root():
    return {"message": "LEON PMS Chat Server is running", "version": "1.0.0", "status": "online"}


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "leon_initialized": leon_instance is not None,
        "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }


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


def _session_response(content: dict, token: str, hotel_name: str, req: Request) -> JSONResponse:
    response = JSONResponse(content=content)
    cookie_attrs = _auth_cookie_attrs(req)
    response.set_cookie(key="leon_jwt", value=token, httponly=True, max_age=access.TOKEN_HOURS * 3600, **cookie_attrs)
    if hotel_name:
        response.set_cookie(key="leon_hotel", value=urllib.parse.quote(hotel_name, safe=""), httponly=False, **cookie_attrs)
    _partition_cross_site_cookies(response)
    return response


def _signed_in(result: dict, req: Request, message: str) -> JSONResponse:
    """A sign-in result as the UI's response: one hotel -> session cookie; several -> account cookie plus the hotels to choose."""
    if result.get("error"):
        return JSONResponse(content={"success": False, "message": result["error"]})
    if result.get("choose") is not None:
        return _session_response({"success": True, "message": "Choose a hotel", "manager_name": result["user_name"],
                                  "user_id": result["user_id"], "hotels": result["choose"]}, result["token"], "", req)
    claims = result["claims"]
    return _session_response({"success": True, "message": message, "manager_name": claims["user_name"], "user_id": claims["user_id"],
                              "hotel_id": claims["hotel_id"], "hotel_name": claims["hotel_name"], "role": claims["role_name"],
                              "token": result["token"]}, result["token"], claims["hotel_name"], req)


@app.post("/api/auth/signup")
async def signup_endpoint(request: SignupRequest, req: Request):
    """A person (phone, email and/or username) and, when hotel_name is given, their hotel with them as general manager."""
    identities = {"phone": request.manager_phone, "email": request.email, "username": request.username}
    created = await run_in_threadpool(user_manager.signup, request.manager_name, identities, request.password,
                                      request.primary_language, request.nationality)
    if created.get("error"):
        return JSONResponse(content={"success": False, "message": created["error"]})
    if request.hotel_name:
        hotel = await run_in_threadpool(user_manager.create_hotel, created["user_id"], request.hotel_name, request.hotel_currency,
                                        request.hotel_timezone, request.city_name)
        if hotel.get("error"):
            return JSONResponse(content={"success": False, "message": hotel["error"], "user_id": created["user_id"]})
    identity_type = next(kind for kind, value in identities.items() if value)
    result = await run_in_threadpool(user_manager.signin, identity_type, identities[identity_type], request.password)
    return _signed_in(result, req, "Account created")


@app.post("/api/onboard/turn")
async def api_onboard_turn(payload: Dict[str, Any] = Body(default={}), req: Request = None):
    """Chat turn that opens a hotel. Sets the sign-in cookie once the account exists."""
    from booking_system.onboard_turn import onboard_turn
    signed = access.decode(req.cookies.get("leon_jwt")) or {}
    try:
        result = await run_in_threadpool(
            onboard_turn,
            str((payload or {}).get("message") or ""),
            (payload or {}).get("history") or [],
            (payload or {}).get("fields") or {},
            signed.get("user_id"),
        )
    except Exception as error:
        return JSONResponse(content={"reply": "I could not finish that turn. " + str(error), "saved": [], "booking_id": ""}, status_code=500)
    token = str(result.pop("token", "") or "")
    hotel = str(result.pop("cookie_hotel", "") or "")
    if token:
        result["token"] = token
        return _session_response(result, token, hotel, req)
    return JSONResponse(content=result)


@app.get("/api/guide")
async def api_guide(req: Request):
    """The hotel's to-do list and highlights. They remain after onboarding."""
    from booking_system.onboard_turn import read_guide
    try:
        return await run_in_threadpool(read_guide, req.state.session["hotel_id"])
    except Exception as error:
        return JSONResponse(content={"error": str(error)}, status_code=500)


@app.get("/api/onboard/template")
async def api_onboard_template(id: str = "read.onboarding", req: Request = None):
    """The interactive message for the onboard page, filled from this hotel. id is a template key or template_key/record_id."""
    from booking_system.interactive.agent import complete, lead
    chosen = str(id or "").strip() or "read.onboarding"
    done = await run_in_threadpool(complete, req.state.session["hotel_id"], chosen)
    if not done:
        return JSONResponse(content={"error": "no template"}, status_code=404)
    return {
        "screen": done["screen"], "interactive": done["interactive"], "message": done["message"],
        "template_key": done["template_key"], "purpose": done["purpose"], "text": done["text"],
        "reply": lead(done),
    }


@app.post("/api/auth/signin")
async def signin_endpoint(request: SigninRequest, req: Request):
    """Sign in by phone, email or username; all three resolve to the same person."""
    result = await run_in_threadpool(user_manager.signin, request.auth_type, request.auth_value, request.password, request.hotel_id)
    return _signed_in(result, req, "Signed in")


def _otp_channel(channel: str):
    return {"whatsapp": "phone", "email": "email"}.get(str(channel or "").strip())


@app.post("/api/auth/signin/otp/send")
async def signin_otp_send(request: OtpSendRequest):
    """Send a sign-in code to a WhatsApp number or email that already belongs to an account. The code is not in the response."""
    from booking_system import contact_otp, db
    kind = _otp_channel(request.channel)
    if not kind:
        return JSONResponse(content={"success": False, "message": "Choose WhatsApp or email."})
    user = await run_in_threadpool(db.transaction, lambda cur: user_manager.find_user(cur, kind, request.address))
    if not user or user["status"] != "active":
        place = "WhatsApp number" if kind == "phone" else "email"
        return JSONResponse(content={"success": False, "message": "No account uses that " + place + "."})
    state = await run_in_threadpool(contact_otp.issue, request.channel, request.address)
    if state == "invalid":
        return JSONResponse(content={"success": False, "message": "That address is not usable."})
    if state == "waiting":
        return JSONResponse(content={"success": True, "message": "A code is already on its way. Share it here."})
    return JSONResponse(content={"success": True, "message": "A code was sent. Share it here to sign in."})


@app.post("/api/auth/signin/otp")
async def signin_otp_confirm(request: OtpConfirmRequest, req: Request):
    """Confirm a sign-in code and open the session. The path stays under /auth/signin so the page keeps the token."""
    from booking_system import contact_otp, db
    kind = _otp_channel(request.channel)
    if not kind:
        return JSONResponse(content={"success": False, "message": "Choose WhatsApp or email."})
    matched = await run_in_threadpool(contact_otp.check, request.channel, request.address, request.code)
    if not matched:
        return JSONResponse(content={"success": False, "message": "That code did not match."})
    user = await run_in_threadpool(db.transaction, lambda cur: user_manager.find_user(cur, kind, request.address))
    if not user or user["status"] != "active":
        return JSONResponse(content={"success": False, "message": "No account uses that address."})
    result = await run_in_threadpool(user_manager.session_for, user["user_id"])
    return _signed_in(result, req, "Signed in")


@app.post("/api/auth/switch_hotel")
async def switch_hotel_endpoint(request: SwitchHotelRequest, req: Request):
    """Re-issue the session for another hotel the signed-in person belongs to."""
    issued = await run_in_threadpool(user_manager.token_for, req.state.session["user_id"], request.hotel_id)
    if not issued:
        return JSONResponse(content={"success": False, "message": "not an active user of that hotel"}, status_code=403)
    return _signed_in(issued, req, "Hotel switched")


@app.post("/api/auth/logout")
@app.post("/api/auth/signout")
async def logout_endpoint(req: Request):
    response = JSONResponse(content={"success": True, "message": "Logged out"})
    cookie_attrs = _auth_cookie_attrs(req)
    response.set_cookie(key="leon_jwt", value="", httponly=True, max_age=0, expires=0, **cookie_attrs)
    response.set_cookie(key="leon_hotel", value="", httponly=False, max_age=0, expires=0, **cookie_attrs)
    _partition_cross_site_cookies(response)
    return response


def _me(claims: dict) -> dict:
    return {
        "user_id": claims.get("user_id"), "manager_name": claims.get("user_name"), "user_name": claims.get("user_name"),
        "hotel_id": claims.get("hotel_id"), "hotel_name": claims.get("hotel_name"), "role": claims.get("role_name"),
        "modules": claims.get("modules"), "hotel_timezone": claims.get("hotel_timezone"), "hotel_currency": claims.get("hotel_currency"),
        "primary_language": claims.get("primary_language"),
    }


@app.get("/api/auth/me")
async def auth_me(req: Request):
    """The signed-in person and session hotel (no hotel_id until one is chosen)."""
    return _me(req.state.session)


@app.get("/api/auth/active")
async def auth_active(req: Request):
    return {"success": True, "manager": _me(req.state.session)}


# --- Classic single-step chat endpoint using leon_meta_orchestrator ---
@app.post("/api/leon/actions/{approval_id}/approve")
async def leon_action_approve(approval_id: str, req: Request):
    """The signed-in person runs the stored action. The model cannot call this."""
    from booking_system.action_recipe import approve_action
    session = req.state.session
    return await run_in_threadpool(approve_action, session["hotel_id"], session["user_id"], approval_id)


@app.post("/api/leon/actions/{approval_id}/reject")
async def leon_action_reject(approval_id: str, req: Request):
    """The signed-in person cancels the stored action. Nothing in the hotel changes."""
    from booking_system.action_recipe import reject_action
    session = req.state.session
    return await run_in_threadpool(reject_action, session["hotel_id"], session["user_id"], approval_id)


@app.post("/api/chat/start")
async def chat_start(request: ChatRequest, leon_jwt: str = Cookie(None)):
 
    try:
        jwt_token = leon_jwt
        # Run blocking orchestration in a thread to avoid blocking the event loop
        response = await run_in_threadpool(leon_instance.process_desk, request.message, jwt_token)
        return {
            "final_message": response.get("reply") or "",
            "refresh": bool(response.get("refresh")),
            "screen": response.get("screen"),
            "pins": response.get("pins") or [],
            "pin_count": response.get("pin_count") or 0,
            "approvals": response.get("approvals") or [],
            "finished": response.get("finished") is True,
        }
    except Exception as e:
        logger.error(f"Exception in /api/chat/start: {e}\n{traceback.format_exc()}")
        return {"final_message": f"Backend error: {str(e)}", "finished": False}

@app.post("/api/chat/complete")
async def chat_complete(payload: dict = Body(...), leon_jwt: str = Cookie(None)):

    try:
        jwt_token = leon_jwt
        message = payload.get("message")
        response = await run_in_threadpool(leon_instance.process_desk, message, jwt_token)
        return {
            "final_message": response.get("reply") or "",
            "refresh": bool(response.get("refresh")),
            "screen": response.get("screen"),
            "pins": response.get("pins") or [],
            "pin_count": response.get("pin_count") or 0,
            "approvals": response.get("approvals") or [],
            "finished": response.get("finished") is True,
        }
    except Exception as e:
        logger.error(f"Exception in /api/chat/complete: {e}\n{traceback.format_exc()}")
        return {"final_message": f"Backend error: {str(e)}", "finished": False}

# --- Chat history endpoints (sync UI with backend dialog_history) ---
@app.get("/api/chat/history")
async def chat_history(leon_jwt: str = Cookie(None)):
    try:
        if not leon_jwt:
            return JSONResponse(content={"items": []}, status_code=200)
        # Prime orchestrator context from JWT, then load dialog history
        hotel = ""
        mgr = ""
        try:
            from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_hotel, get_desk_user_from_jwt as _get_mgr
            hotel = _get_hotel(leon_jwt)
            mgr = _get_mgr(leon_jwt)
        except Exception:
            hotel = ""
        if not isinstance(hotel, str) or hotel.startswith("❌") or not isinstance(mgr, str) or mgr.startswith("❌"):
            return JSONResponse(content={"items": []}, status_code=200)
        history = leon_instance.read_dialog(hotel.upper(), mgr.upper())
        items = []
        for idx, entry in enumerate(history):
            try:
                sender, text = leon_instance.dialog_line(entry, idx)
                items.append({
                    "sender": sender,
                    "text": text,
                    "channel": str(entry.get("channel") or "") if isinstance(entry, dict) else "",
                    "source_ref": str(entry.get("source_ref") or "") if isinstance(entry, dict) else "",
                })
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
        from booking_system.leon_meta_orchestrator import get_hotel_name_from_jwt as _get_hotel, get_desk_user_from_jwt as _get_mgr
        hotel = _get_hotel(leon_jwt)
        mgr = _get_mgr(leon_jwt)
        if not isinstance(hotel, str) or hotel.startswith("❌") or not isinstance(mgr, str) or mgr.startswith("❌"):
            return JSONResponse(content={"success": False, "message": "Invalid hotel in token"}, status_code=401)
        leon_instance.write_dialog(hotel.upper(), mgr.upper(), [])
        from booking_system.leon_turn import remember_topic
        from booking_system.runtime_state import delete_session
        remember_topic(hotel, mgr, "")
        delete_session(hotel, mgr)
        return {"success": True}
    except Exception as e:
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=500)

@app.get("/api/definitions")
async def definitions_list(req: Request):
    """Questions this user still needs to answer, and the definitions already on their profile."""
    from booking_system.semantic.definitions import manager_definitions, open_definitions
    s = access.session(req)
    return {"user_id": s["user_id"], "manager_name": s["user_name"], "pending": open_definitions(s["user_id"]),
            "answered": manager_definitions(s["user_id"])}


@app.get("/api/personal_recipes")
async def personal_recipes_list(req: Request):
    """This user's saved reports: each references published general recipes with a relative day."""
    from booking_system.semantic.personal import describe, personal_recipes
    s = access.session(req)
    saved = personal_recipes(s["user_id"])
    return {"user_id": s["user_id"], "manager_name": s["user_name"],
            "personal_recipes": [dict(item, summary=describe(item)) for item in saved]}


@app.post("/api/personal_recipes")
async def personal_recipes_change(req: Request, payload: Dict[str, Any] = Body(...)):
    """Read first. create needs a new name, update and delete need a saved one. figures are mapped onto published recipes."""
    from booking_system.semantic.personal import delete_personal, find_personal, learn, needed_definitions, save_personal
    manager = access.session(req)["user_id"]
    body = payload or {}
    operation = str(body.get("operation") or "")
    name = str(body.get("name") or "")
    existing = find_personal(manager, name)
    if operation not in ("create", "update", "delete"):
        return JSONResponse(content={"success": False, "message": "operation is create, update, or delete"}, status_code=400)
    if operation == "create" and existing:
        return JSONResponse(content={"success": False, "message": "already saved", "personal_recipe": existing}, status_code=409)
    if operation != "create" and not existing:
        return JSONResponse(content={"success": False, "message": "no saved report " + name}, status_code=404)
    if operation == "delete":
        return {"success": True, **delete_personal(manager, name)}
    figures = str(body.get("figures") or "")
    if not figures:
        return JSONResponse(content={"success": False, "message": "figures are required"}, status_code=400)
    items, seen = await run_in_threadpool(learn, manager, name, figures)
    result = save_personal(manager, name, items, seen)
    if result.get("error"):
        return JSONResponse(content={"success": False, "message": result["error"]}, status_code=400)
    return {"success": True, **result, "needs_definitions": needed_definitions(manager, result["recipe"])}


@app.post("/api/definitions")
async def definitions_answer(req: Request, payload: Dict[str, Any] = Body(...)):
    """The user answers one definition. Investigations waiting only on answered questions are reviewed again."""
    from booking_system.semantic.definitions import save_definition
    from booking_system.semantic.trace import enqueue_resume
    result = save_definition(access.session(req)["user_id"], (payload or {}).get("key"), (payload or {}).get("definition"), "api")
    if result.get("error"):
        return JSONResponse(content={"success": False, "message": result["error"]}, status_code=400)
    if result.get("ready_trace_ids"):
        enqueue_resume(result["ready_trace_ids"])
    return {"success": True, **result}


@app.post("/api/chat/menu")
async def chat_menu_open(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Open the database list for the catalog action the hotel user chose."""
    hotel = _hotel_from_jwt(leon_jwt)
    if not hotel:
        return JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
    from booking_system.desk_actions import open_action
    from booking_system.hotel_registry import hotel_id
    from booking_system.interactive.agent import complete, deliver
    from booking_system.leon_meta_orchestrator import get_desk_user_from_jwt
    from booking_system.leon_turn import _copy_function, remember_topic
    chosen = str((payload or {}).get("id") or "") or "nav.home"
    if chosen == "check_availability":
        manager = get_desk_user_from_jwt(leon_jwt)
        if isinstance(manager, str) and not manager.startswith("❌"):
            await run_in_threadpool(remember_topic, hotel, manager, "availability")
    ident = hotel_id(hotel)
    done = await run_in_threadpool(complete, ident, chosen)
    if done:
        await run_in_threadpool(deliver, hotel, done)
        return {"screen": done["screen"], "interactive": done["interactive"], "message": done["message"],
                "template_key": done["template_key"], "purpose": done["purpose"]}
    screen = await run_in_threadpool(open_action, ident, chosen)
    if not screen:
        return JSONResponse(content={"error": "Action was not found"}, status_code=400)
    await run_in_threadpool(_copy_function, hotel, screen)
    return {"screen": screen}


@app.get("/api/chat/desk")
async def chat_desk(build: int = 0, leon_jwt: str = Cookie(None)):
    """The stored list and the three pins. The sales card is built only when the chat opens."""
    hotel = _hotel_from_jwt(leon_jwt)
    if not hotel:
        return {"screen": None, "pins": [], "pin_count": 0}
    from booking_system.leon_turn import ensure_sales_card, list_pins, read_screen
    stored = await run_in_threadpool(list_pins, hotel)
    screen = await run_in_threadpool(ensure_sales_card if build else read_screen, hotel)
    return {"screen": screen, "pins": stored[:3], "pin_count": len(stored), "stored": stored}

@app.post("/api/chat/card")
async def chat_card(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Pin, mark read, or dismiss the sales card. The same call serves a WhatsApp list reply."""
    hotel = _hotel_from_jwt(leon_jwt)
    if not hotel:
        return JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
    from booking_system.leon_turn import card_click, ensure_sales_card, shown_pins
    result = await run_in_threadpool(card_click, hotel, (payload or {}).get("card"), (payload or {}).get("id"))
    if result == "missing":
        return JSONResponse(content={"error": "Card action was not saved"}, status_code=400)
    screen = await run_in_threadpool(ensure_sales_card, hotel)
    shown, count = await run_in_threadpool(shown_pins, hotel)
    return {"screen": screen, "pins": shown, "pin_count": count, "result": result}

@app.post("/api/chat/pin")
async def chat_pin(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """The hotel user pins one message into a category."""
    hotel = _hotel_from_jwt(leon_jwt)
    if not hotel:
        return JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
    from booking_system.leon_turn import pin_message, shown_pins
    result = await run_in_threadpool(pin_message, hotel, (payload or {}).get("text"), (payload or {}).get("category"), "user")
    if result == "missing":
        return JSONResponse(content={"error": "Pin was not saved"}, status_code=400)
    shown, count = await run_in_threadpool(shown_pins, hotel)
    return {"pins": shown, "pin_count": count}

@app.post("/api/chat/unpin")
async def chat_unpin(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """The hotel user removes one pinned message."""
    hotel = _hotel_from_jwt(leon_jwt)
    if not hotel:
        return JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
    from booking_system.leon_turn import shown_pins, unpin_message
    result = await run_in_threadpool(unpin_message, hotel, (payload or {}).get("text"))
    if result == "missing":
        return JSONResponse(content={"error": "Pin not found"}, status_code=404)
    shown, count = await run_in_threadpool(shown_pins, hotel)
    return {"pins": shown, "pin_count": count}

@app.post("/api/chat/screen/drop")
async def chat_screen_drop(payload: Dict[str, Any] = Body(...), leon_jwt: str = Cookie(None)):
    """Remove one chosen row from the list the agent sent."""
    hotel = _hotel_from_jwt(leon_jwt)
    if not hotel:
        return JSONResponse(content={"error": "Invalid hotel in token"}, status_code=401)
    from booking_system.leon_turn import drop_screen_row, read_screen
    await run_in_threadpool(drop_screen_row, hotel, (payload or {}).get("id"))
    return {"screen": await run_in_threadpool(read_screen, hotel)}

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
        try:
            payload_jwt = access.decode(leon_jwt)
            hotel_name = (payload_jwt.get('hotel_name') or '').upper()
            manager_name = (payload_jwt.get('manager_name') or '').upper()
            primary_language = (payload_jwt.get('primary_language') or '').strip()
        except Exception as e:
            return JSONResponse(content={"error": f"Invalid token: {e}"}, status_code=401)

        # Prime orchestrator context and load recent dialog history
        recent_history = []
        try:
            if leon_instance:
                history = await run_in_threadpool(leon_instance.read_dialog, hotel_name, payload_jwt.get("user_id"))
                # Take last 8 turns (4 exchanges)
                window = history[-8:]
                for idx, msg in enumerate(window):
                    try:
                        sender, text = leon_instance.dialog_line(msg, len(history) - len(window) + idx)
                        role = 'MANAGER' if sender == 'user' else 'LEON'
                        recent_history.append(f"{role}: {text}")
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
            'dashboard.html': [],
            'rooms.html': ['read_room_types_by_criteria_tool', 'create_room_type_tool', 'manage_room_description_tool', 'add_room_numbers_tool', 'read_room_type_from_room_number_tool'],
            'inventory.html': ['manage_room_quantity', 'manage_room_price', 'check_room_pricing', 'read_inventory_information_from_description'],
            'bookings.html': ['create_booking_tool', 'search_bookings_tool', 'modify_stay_dates_tool', 'cancel_booking_tool', 'get_payment_status_for_booking_tool', 'send_booking_confirmation_tool'],
            'guests.html': ['create_guest_profile_tool', 'update_guest_profile_tool', 'read_guests_by_criteria_tool', 'reactivate_guest_profile_tool', 'read_guest_analytics'],
            'housekeeping.html': ['read_dirty_rooms', 'create_cleaning_task', 'update_cleaning_progress', 'read_room_readiness_status_tool', 'read_housekeeping_analytics'],
            'operations.html': ['assign_room_number', 'suggest_room_assignment', 'mark_room_out_of_order', 'mark_room_available', 'read_maintenance_requests'],
            'analytics.html': [],
            'assignments.html': ['read_room_assignments_for_booking_tool', 'suggest_room_assignment', 'read_assignment_dashboard'],
            'activities.html': []
        }
        key = (route or '').split('?')[0].split('/')[-1].lower()
        purpose = page_purpose.get(key, '')
        tool_list = tools_by_page.get(key, [])

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
            from booking_system.llm_utils import haiku_text
            text = haiku_text(system, content, max_tokens=40, timeout=10).strip()
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

# Place this static endpoint BEFORE any /api/hotels/{hotel_id} or similar dynamic endpoints


# --- Property Setup APIs (policies + templates) ---

# --- Property Templates (canonical, template-first flow) ---



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

from booking_system import jobs as _jobs
from booking_system.routes import guests as guests_routes
_jobs.register("guest_needs", lambda hotel_id, payload: guests_routes.write_guest_needs(hotel_id, payload["guest_phone"]))


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
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
    for hotel_id, phone in touched or []:
        await run_in_threadpool(_jobs.enqueue, "guest_needs", {"guest_phone": phone}, hotel_id)
    return {"success": True}


# --- Activity log: successful desk POSTs, written after the response is sent ---
ACTIVITY_TEXT = {
    "/api/assignments/update": ("Update booking", ("booking_id",)),
    "/api/assignments/reassign": ("Reassign", ("booking_id", "stay_date", "old_room_number", "new_room_number")),
    "/api/assignments/cancel": ("Cancel booking", ("booking_id",)),
    "/api/assignments/check_in": ("Check-in", ("booking_id", "room_number")),
    "/api/assignments/check_out": ("Check-out", ("booking_id", "room_number")),
    "/api/payments/update": ("Payment", ("booking_id", "payment_amount", "payment_method")),
    "/api/pricing/override": ("Pricing override", ("booking_id", "new_total_amount")),
    "/api/maintenance/create": ("Create maintenance", ("room_number", "area", "issue", "urgency")),
    "/api/maintenance/update": ("Update maintenance", ("task_id", "status", "assigned_to_phone")),
    "/api/rooms/add": ("Add rooms", ("room_type", "room_number", "room_numbers")),
    "/api/rooms/types/create": ("Create room type", ("room_type",)),
}


def _activity_text(path, body):
    """'<label> key=value, ...' for a listed path, from the JSON body fields the table names."""
    label, fields = ACTIVITY_TEXT[path]
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except ValueError:
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    shown = [name + "=" + str(payload[name])[:60] for name in fields if payload.get(name) not in (None, "", [])]
    return " ".join([label] + [", ".join(shown)] if shown else [label])


def _write_activity(signed_in, text):
    from zoneinfo import ZoneInfo
    from booking_system.leon_meta_orchestrator import orchestrator as _orchestrator
    zone = signed_in.get("hotel_timezone") or "UTC"
    _orchestrator.write_activity_log_internal({
        "ts": datetime.now(ZoneInfo(zone)).isoformat(), "timezone": zone, "text": text, "hotel_id": signed_in["hotel_id"],
        "user_id": signed_in["user_id"], "actor": signed_in.get("user_name") or "",
    })
    logger.info("ACTIVITY " + signed_in["hotel_id"] + " :: " + text)


class ActivityLog:
    """Pure ASGI: passes receive/send through unchanged, keeps a copy of the body and the status, and logs after the response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] not in ACTIVITY_TEXT:
            return await self.app(scope, receive, send)
        state = scope.setdefault("state", {})
        chunks, status = [], []

        async def tee():
            message = await receive()
            if message["type"] == "http.request":
                chunks.append(message.get("body", b""))
            return message

        async def watch(message):
            if message["type"] == "http.response.start":
                status.append(message["status"])
            await send(message)

        await self.app(scope, tee, watch)
        signed_in = state.get("session") or {}
        if status == [200] and signed_in.get("hotel_id"):
            try:
                await run_in_threadpool(_write_activity, signed_in, _activity_text(scope["path"], b"".join(chunks)))
            except Exception:
                logger.warning("activity log write failed", exc_info=True)


app.add_middleware(ActivityLog)


@app.get('/api/activity/logs/by_hotel')
def get_activity_logs_by_hotel(limit: int = 100, s: dict = Depends(access.session)):
    from booking_system.leon_meta_orchestrator import orchestrator as _orchestrator
    data = _orchestrator.read_activity_logs_internal(s["hotel_id"], limit)
    return dict(data, timezone=s.get("hotel_timezone") or "UTC")
# --- End activity log ---


# ---------- routers and access points ----------
from booking_system.finance import api as finance_api
from booking_system.fnb import api as fnb_api
from booking_system.hr import api as hr_api
from booking_system.semantic.complaints_api import router as complaints_admin_router

app.include_router(finance_api.build_router(access.session))
app.include_router(fnb_api.router)
app.include_router(hr_api.router_for(access.session))
app.include_router(complaints_admin_router)

from booking_system.onboarding import api as onboarding_api

access.PUBLIC_ROUTES.update(onboarding_api.PUBLIC)
access.register(onboarding_api.ACCESS)
app.include_router(onboarding_api.router)

from booking_system.routes import bookings as bookings_routes, frontdesk as frontdesk_routes
from booking_system.routes import operations as operations_routes, rooms as rooms_routes
from booking_system.routes import guest_services as guest_services_routes, property as property_routes
from booking_system.routes import settings as settings_routes
for _area in (rooms_routes, bookings_routes, frontdesk_routes, operations_routes, guests_routes, guest_services_routes, property_routes,
              settings_routes):
    access.register(_area.ACCESS)
    app.include_router(_area.router)

access.PUBLIC_ROUTES.update(guests_routes.PUBLIC)
access.register([
    ("GET /api/guide", "front_desk", "view"),
    ("GET /api/onboard/template", "front_desk", "view"),
    ("POST /api/chat/start", "front_desk", "view"), ("POST /api/chat/complete", "front_desk", "view"),
    ("POST /api/leon/actions/{approval_id}/approve", "front_desk", "operate"),
    ("POST /api/leon/actions/{approval_id}/reject", "front_desk", "operate"),
    ("GET /api/chat/history", "front_desk", "view"), ("POST /api/chat/clear", "front_desk", "view"),
    ("POST /api/chat/menu", "front_desk", "view"), ("GET /api/chat/desk", "front_desk", "view"),
    ("POST /api/chat/card", "front_desk", "view"), ("POST /api/chat/pin", "front_desk", "view"),
    ("POST /api/chat/unpin", "front_desk", "view"), ("POST /api/chat/screen/drop", "front_desk", "view"),
    ("POST /api/chat/suggest", "front_desk", "view"),
    ("GET /api/definitions", "front_desk", "view"), ("POST /api/definitions", "front_desk", "view"),
    ("GET /api/personal_recipes", "front_desk", "view"), ("POST /api/personal_recipes", "front_desk", "view"),
    ("POST /api/navigation/simple", "front_desk", "view"), ("POST /api/reset", "front_desk", "view"),
    ("GET /api/activity/logs/by_hotel", "reporting", "view"),
    # finance: setup, codes, taxes and the business day are the finance manager's
    ("POST /api/finance/setup/seed", "finance", "manage"), ("POST /api/finance/departments", "finance", "manage"),
    ("DELETE /api/finance/departments/{department_name}", "finance", "manage"),
    ("POST /api/finance/transaction_codes", "finance", "manage"),
    ("PUT /api/finance/transaction_codes/{transaction_code}/gl_export_code", "finance", "manage"),
    ("DELETE /api/finance/transaction_codes/{transaction_code}", "finance", "manage"),
    ("POST /api/finance/tax_codes", "finance", "manage"), ("POST /api/finance/tax_codes/{tax_code}/rules", "finance", "manage"),
    ("POST /api/finance/business_day/open", "finance", "manage"), ("POST /api/finance/business_day/close", "finance", "manage"),
    ("POST /api/finance/tax_quote", "finance", "view"),
    # F&B setup is the outlet manager's; F&B reads codes Finance owns
    ("POST /api/fnb/outlets", "fnb", "manage"), ("DELETE /api/fnb/outlets/{outlet_id}", "fnb", "manage"),
    ("POST /api/fnb/outlets/{outlet_id}/menu", "fnb", "manage"), ("DELETE /api/fnb/menu/{item_id}", "fnb", "manage"),
    # HR: setup, staff records, rosters, leave decisions and attendance corrections are the HR manager's;
    # clocking and leave requests are operate (the router default for writes), reads are view
    *[(method + " /api/hr/" + plural + tail, "hr", "manage")
      for plural in hr_api.PLURAL.values() for method, tail in (("POST", ""), ("PATCH", "/{entity_id}"), ("DELETE", "/{entity_id}"))],
    ("POST /api/hr/staff", "hr", "manage"), ("PATCH /api/hr/staff/{staff_id}", "hr", "manage"),
    ("DELETE /api/hr/staff/{staff_id}", "hr", "manage"), ("POST /api/hr/staff/{staff_id}/identities", "hr", "manage"),
    ("DELETE /api/hr/staff/identities/{staff_identity_id}", "hr", "manage"), ("PATCH /api/hr/staff/{staff_id}/profile", "hr", "manage"),
    ("POST /api/hr/roster", "hr", "manage"), ("DELETE /api/hr/roster/{roster_id}", "hr", "manage"),
    ("PATCH /api/hr/attendance/{attendance_id}", "hr", "manage"),
    ("POST /api/hr/leave/{leave_request_id}/approve", "hr", "manage"), ("POST /api/hr/leave/{leave_request_id}/reject", "hr", "manage"),
])


if __name__ == "__main__":
    print("🤖 Starting LEON PMS Chat Server...")
    print("🌐 Server will be available at: http://localhost:8002")
    print("📱 PMS UI can connect to: http://localhost:8002/api/chat")
    print("🔧 Health check: http://localhost:8002/health")
    print("📊 Status: http://localhost:8002/api/status")
    
    # Run the server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8002)),
        log_level="info",
        timeout_keep_alive=75,
    ) 