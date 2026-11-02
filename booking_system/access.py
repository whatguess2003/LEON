"""Sign-in tokens, the request session, and module access.

Every API route is either in PUBLIC_ROUTES or has an access point ("METHOD /path" -> module, level). The guard middleware decodes the
leon_jwt token, checks that the (hotel_id, user_id) membership is still active at the token's access_version (one indexed read), and that
the token's level on the point's module meets the point's level. Route code reads the session through the `session` dependency."""

import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from booking_system import db

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGO = "HS256"
TOKEN_HOURS = 12
RANK = {"none": 0, "view": 1, "operate": 2, "manage": 3}

# D16 role matrix. Every role has a row for every module; GENERAL_MANAGER manages all.
ROLES = {
    "GENERAL_MANAGER": (5, {"front_desk": "manage", "reservations": "manage", "housekeeping": "manage", "maintenance": "manage", "reporting": "manage",
                            "hr": "manage", "fnb": "manage", "finance": "manage", "admin": "manage"}),
    "FRONT_DESK": (2, {"front_desk": "operate", "reservations": "operate", "housekeeping": "view", "maintenance": "operate", "reporting": "view",
                       "hr": "none", "fnb": "view", "finance": "none", "admin": "none"}),
    "NIGHT_AUDITOR": (2, {"front_desk": "manage", "reservations": "view", "housekeeping": "view", "maintenance": "view", "reporting": "view",
                          "hr": "none", "fnb": "view", "finance": "operate", "admin": "none"}),
    "HOUSEKEEPING": (2, {"front_desk": "view", "reservations": "none", "housekeeping": "operate", "maintenance": "operate", "reporting": "none",
                         "hr": "none", "fnb": "none", "finance": "none", "admin": "none"}),
    "MAINTENANCE": (2, {"front_desk": "view", "reservations": "none", "housekeeping": "view", "maintenance": "manage", "reporting": "none",
                        "hr": "none", "fnb": "none", "finance": "none", "admin": "none"}),
    "FNB_CASHIER": (2, {"front_desk": "none", "reservations": "none", "housekeeping": "none", "maintenance": "none", "reporting": "none",
                        "hr": "none", "fnb": "operate", "finance": "none", "admin": "none"}),
    "ACCOUNTANT": (2, {"front_desk": "view", "reservations": "view", "housekeeping": "none", "maintenance": "none", "reporting": "operate",
                       "hr": "none", "fnb": "view", "finance": "manage", "admin": "none"}),
    "HR": (2, {"front_desk": "none", "reservations": "none", "housekeeping": "none", "maintenance": "none", "reporting": "view",
               "hr": "manage", "fnb": "none", "finance": "none", "admin": "none"}),
}

PUBLIC_ROUTES = {
    "GET /", "GET /health", "GET /api/status", "POST /api/auth/signup", "POST /api/auth/signin", "POST /api/auth/logout",
    "POST /api/auth/signout", "POST /api/auth/signin/otp/send", "POST /api/auth/signin/otp",
    "GET /api/meta/country_codes", "GET /api/meta/languages", "GET /webhook/whatsapp", "POST /webhook/whatsapp",
    "POST /update_reservation", "POST /api/onboard/turn", "GET /openapi.json", "GET /docs", "GET /docs/oauth2-redirect", "GET /redoc",
}
# Routes that need a signed-in person but no hotel membership (choosing or switching a hotel).
ACCOUNT_ROUTES = {"POST /api/auth/switch_hotel", "GET /api/auth/me", "GET /api/auth/active"}

# Hotel member routes: an active, current membership of the session's hotel and no module level; the handler checks per item.
MEMBER_ROUTES = {"GET /api/settings", "GET /api/settings/pending", "GET /api/settings/{setting_key}/history", "POST /api/settings"}

# Platform-team routers authenticate with their own admin token, never a hotel session.
PLATFORM_PREFIXES = ("/api/admin/complaints",)

# Department routers: GET needs view, any write needs operate, unless a point below says otherwise.
ROUTER_MODULES = {"/api/hr/": "hr", "/api/finance/": "finance", "/api/fnb/": "fnb"}

_POINTS = {}


def register(points):
    """points: iterable of ("METHOD /path", module, level). Declared by each routes module at import."""
    for name, module, level in points:
        _POINTS[name] = (module, level)


def point(name):
    return _POINTS.get(name)


# ---------- tokens ----------

def membership(cur, hotel_id, user_id):
    """The active membership with its module levels, or None."""
    cur.execute(
        """SELECT u.hotel_id, u.user_id, u.access_version, r.role_name, a.user_name, h.hotel_name, h.timezone, h.currency
           FROM hotel_user u JOIN access_role r ON r.role_id = u.role_id JOIN user_account a ON a.user_id = u.user_id
           JOIN hotel h ON h.hotel_id = u.hotel_id
           WHERE u.hotel_id = %s AND u.user_id = %s AND u.status = 'active' AND a.status = 'active'""",
        (hotel_id, user_id),
    )
    found = cur.fetchone()
    if not found:
        return None
    cur.execute("SELECT module_name, access_level FROM user_module_access WHERE hotel_id = %s AND user_id = %s", (hotel_id, user_id))
    return dict(found, modules={row["module_name"]: row["access_level"] for row in cur.fetchall()})


def issue_token(cur, hotel_id, user_id, primary_language):
    member = membership(cur, hotel_id, user_id)
    if not member:
        return None
    now = datetime.now(timezone.utc)
    claims = {
        "user_id": user_id, "hotel_id": hotel_id, "hotel_name": member["hotel_name"], "user_name": member["user_name"],
        "manager_name": member["user_name"], "role_name": member["role_name"], "modules": member["modules"],
        "access_version": member["access_version"], "hotel_timezone": member["timezone"], "hotel_currency": member["currency"].strip(),
        "primary_language": primary_language, "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=TOKEN_HOURS)).timestamp()),
    }
    return jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGO), claims


def account_token(user_id, user_name, primary_language):
    """A token for a person without a chosen hotel (onboarding, hotel choice). It passes no hotel route."""
    now = datetime.now(timezone.utc)
    claims = {"user_id": user_id, "user_name": user_name, "primary_language": primary_language,
              "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=TOKEN_HOURS)).timestamp())}
    return jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGO)


def decode(token):
    """Claims of a valid, unexpired token, or None."""
    if not token:
        return None
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        return None


# ---------- request guard ----------

def _route(app, scope):
    from starlette.routing import Match
    for route in app.router.routes:
        match, child = route.matches(scope)
        if match == Match.FULL:
            return route, child.get("path_params") or {}
    return None, {}


def requirement(method, path):
    """(module, level) for a route template, or None when it has no access point."""
    name = method + " " + path
    if name in _POINTS:
        return _POINTS[name]
    for prefix, module in ROUTER_MODULES.items():
        if path.startswith(prefix):
            return module, ("view" if method == "GET" else "operate")
    return None


def _refuse(status, message, **extra):
    return JSONResponse(status_code=status, content=dict({"error": message}, **extra))


async def guard(request: Request, call_next):
    """Middleware. Public routes pass; every other route needs a valid token, an active membership and the module level."""
    from starlette.concurrency import run_in_threadpool
    route, params = _route(request.app, request.scope)
    if route is None or not hasattr(route, "methods"):
        return await call_next(request)
    method = request.method if request.method != "HEAD" else "GET"
    name = method + " " + route.path
    if name in PUBLIC_ROUTES or method == "OPTIONS" or route.path.startswith(PLATFORM_PREFIXES):
        return await call_next(request)
    claims = decode(request.cookies.get("leon_jwt"))
    if not claims:
        return _refuse(401, "sign in required")
    if name in ACCOUNT_ROUTES:
        request.state.session = dict(claims)
        return await call_next(request)
    needed = ("admin", "none") if name in MEMBER_ROUTES else requirement(method, route.path)
    if needed is None:
        return _refuse(403, "route has no access point", route=name)
    if not claims.get("hotel_id"):
        return _refuse(403, "choose a hotel first")
    module, level = needed
    current = await run_in_threadpool(
        db.row, "SELECT u.access_version, hotel_module_state(u.hotel_id, %s, hotel_local_date(u.hotel_id)) AS module_state "
                "FROM hotel_user u JOIN user_account a ON a.user_id = u.user_id "
                "WHERE u.hotel_id = %s AND u.user_id = %s AND u.status = 'active' AND a.status = 'active'",
        (module, claims["hotel_id"], claims["user_id"]))
    if not current or current["access_version"] != claims.get("access_version"):
        return _refuse(401, "access changed; sign in again")
    held = (claims.get("modules") or {}).get(module, "none")
    if RANK[held] < RANK[level]:
        return _refuse(403, "outside your access", module=module, needed=level)
    from booking_system.settings.service import module_refusal
    refusal = "" if name in MEMBER_ROUTES else module_refusal(current["module_state"])
    if refusal:
        return _refuse(409, module + " " + refusal, module=module, missing=current["module_state"]["missing"])
    asked = [params.get("hotel_id"), request.query_params.get("hotel_id")]
    if any(value and value != claims["hotel_id"] for value in asked):
        return _refuse(403, "another hotel is outside your session")
    named = (request.query_params.get("hotel_name") or "").strip()
    if named and named.upper() != str(claims.get("hotel_name") or "").strip().upper():
        return _refuse(403, "another hotel is outside your session")
    request.state.session = dict(claims)
    return await call_next(request)


def session(request: Request):
    """FastAPI dependency: {'hotel_id', 'user_id', 'hotel_name', 'user_name', 'role_name', 'modules', 'hotel_timezone', 'hotel_currency', ...}."""
    found = getattr(request.state, "session", None)
    if not found or not found.get("hotel_id"):
        raise HTTPException(401, "sign in required")
    return found


def check_routes(app):
    """Startup: every route is public, an account route, or has an access point. Writes the api points to access_point. Raises otherwise."""
    missing, rows = [], []
    for route in app.router.routes:
        if not hasattr(route, "methods"):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            name = method + " " + route.path
            if name in PUBLIC_ROUTES or name in ACCOUNT_ROUTES or name in MEMBER_ROUTES or route.path.startswith(PLATFORM_PREFIXES):
                continue
            needed = requirement(method, route.path)
            if needed is None:
                missing.append(name)
            else:
                rows.append((name,) + needed)
    if missing:
        raise RuntimeError("routes without an access point: " + ", ".join(sorted(missing)))

    def run(cur):
        """READ both lists in one round trip; rewrite each only when it differs, in one statement each."""
        cur.execute("SELECT (SELECT COALESCE(jsonb_agg(jsonb_build_array(access_point_name, module_name, access_level)), '[]') "
                    "FROM access_point WHERE access_kind = 'api') AS points, "
                    "(SELECT COALESCE(jsonb_agg(route_name), '[]') FROM public_route) AS public")
        found = cur.fetchone()
        if sorted(tuple(item) for item in found["points"]) != sorted(rows):
            cur.execute("DELETE FROM access_point WHERE access_kind = 'api'")
            cur.execute("INSERT INTO access_point (access_kind, access_point_name, module_name, access_level) "
                        "SELECT 'api', * FROM unnest(%s::text[], %s::text[], %s::text[])",
                        ([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]))
        if sorted(found["public"]) != sorted(PUBLIC_ROUTES):
            cur.execute("DELETE FROM public_route")
            cur.execute("INSERT INTO public_route (route_name) SELECT unnest(%s::text[])", (sorted(PUBLIC_ROUTES),))
    db.transaction(run)
    return len(rows)


def allowed(hotel_id, user_id, kind, name):
    """(True, '') or (False, module) for a desk action, desk read, tool or table. A point without a row is refused."""
    found = db.rows(
        """SELECT p.module_name, l.level_rank AS needed, a.level_rank AS held
           FROM access_point p JOIN access_level l ON l.access_level = p.access_level
           LEFT JOIN user_module_access a ON a.hotel_id = %s AND a.user_id = %s AND a.module_name = p.module_name
           WHERE p.access_kind = %s AND p.access_point_name = %s""",
        (hotel_id, user_id, kind, name))
    if not found:
        return False, "unlisted"
    for item in found:
        if item["held"] is not None and item["held"] >= item["needed"]:
            return True, ""
    return False, found[0]["module_name"]
