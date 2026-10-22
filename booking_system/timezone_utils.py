import os
import re
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None  # type: ignore

try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None  # type: ignore


_memory_client = None
_tz_cache = {}


def _shared_client():
    global _memory_client
    if _memory_client is not None:
        return _memory_client
    try:
        from booking_system.dynamic_pricing import dynamic_pricing
        if getattr(dynamic_pricing, "client", None) is not None:
            _memory_client = dynamic_pricing.client
            return _memory_client
    except Exception:
        pass
    if MongoClient is None:
        return None
    url = os.getenv('MONGO_URL')
    if not url:
        return None
    _memory_client = MongoClient(url)
    return _memory_client


def _get_mongo_db():
    client = _shared_client()
    if client is None:
        return None
    return client.get_database('leon_hotel_memory')


def _get_manager_db():
    client = _shared_client()
    if client is None:
        return None
    return client.get_database('leon_manager_system')


def get_hotel_timezone_str(hotel_name: str) -> str:
    if not hotel_name:
        return ""
    env_tz = os.getenv('HOTEL_TIMEZONE')
    if env_tz:
        return env_tz
    name_upper = str(hotel_name).upper()
    if name_upper in _tz_cache:
        return _tz_cache[name_upper]
    found = _lookup_hotel_timezone(hotel_name)
    _tz_cache[name_upper] = found
    return found


def _lookup_hotel_timezone(hotel_name: str) -> str:
    """
    Return IANA timezone string for a given hotel.
    Looks up MongoDB `leon_hotel_memory` for common collections.
    No guessing: if not found, return empty string.
    """
    if not hotel_name:
        return ""
    # 0) Environment override
    env_tz = os.getenv('HOTEL_TIMEZONE')
    if env_tz:
        return env_tz
    db = _get_mongo_db()
    candidates = (
        ('hotel_info', 'timezone'),
        ('hotels', 'timezone'),
        ('hotel_policies', 'timezone'),
    )
    key = 'hotel_name'
    name = str(hotel_name or "")
    name_upper = name.upper()
    name_lower = name.lower()
    if db is not None:
        for coll_name, tz_field in candidates:
            try:
                coll = db.get_collection(coll_name)
                # 1) Exact
                doc = coll.find_one({key: name}, {tz_field: 1})
                if doc and doc.get(tz_field):
                    return str(doc.get(tz_field))
                # 2) Exact UPPER/LOWER variants
                if name_upper != name:
                    doc = coll.find_one({key: name_upper}, {tz_field: 1})
                    if doc and doc.get(tz_field):
                        return str(doc.get(tz_field))
                if name_lower != name:
                    doc = coll.find_one({key: name_lower}, {tz_field: 1})
                    if doc and doc.get(tz_field):
                        return str(doc.get(tz_field))
                # 3) Case-insensitive anchored regex
                rx = re.compile(r'^' + re.escape(name) + r'$', re.IGNORECASE)
                doc = coll.find_one({key: rx}, {tz_field: 1})
                if doc and doc.get(tz_field):
                    return str(doc.get(tz_field))
            except Exception:
                continue

    # 3. Manager system profile (signup stores hotel_timezone)
    try:
        mdb = _get_manager_db()
        if mdb is not None:
            coll = mdb.get_collection('manager_collection')
            # exact
            doc = coll.find_one({'hotel_name': name}, {'hotel_timezone': 1})
            if doc and doc.get('hotel_timezone'):
                return str(doc.get('hotel_timezone'))
            # upper/lower
            if name_upper != name:
                doc = coll.find_one({'hotel_name': name_upper}, {'hotel_timezone': 1})
                if doc and doc.get('hotel_timezone'):
                    return str(doc.get('hotel_timezone'))
            if name_lower != name:
                doc = coll.find_one({'hotel_name': name_lower}, {'hotel_timezone': 1})
                if doc and doc.get('hotel_timezone'):
                    return str(doc.get('hotel_timezone'))
            # case-insensitive regex
            rx = re.compile(r'^' + re.escape(name) + r'$', re.IGNORECASE)
            doc = coll.find_one({'hotel_name': rx}, {'hotel_timezone': 1})
            if doc and doc.get('hotel_timezone'):
                return str(doc.get('hotel_timezone'))
    except Exception:
        pass

    # 4) Fallback: read booking_system/manager_context.json (legacy)
    try:
        import json
        here = os.path.dirname(__file__)
        ctx_path = os.path.join(here, 'manager_context.json')
        if os.path.exists(ctx_path):
            with open(ctx_path, 'r', encoding='utf-8') as f:
                ctx = json.load(f)
            # Possible shapes:
            # a) {"hotel_name": "...", "timezone": "..."}
            hn = str(ctx.get('hotel_name') or ctx.get('name') or '')
            tz = ctx.get('timezone')
            if hn and tz and hn.upper() == name_upper:
                return str(tz)
            # b) {"hotels": [{"hotel_name": "...", "timezone": "..."}, ...]}
            hotels = ctx.get('hotels') or []
            if isinstance(hotels, list):
                for h in hotels:
                    try:
                        hn2 = str((h or {}).get('hotel_name') or (h or {}).get('name') or '')
                        tz2 = (h or {}).get('timezone')
                        if hn2 and tz2 and hn2.upper() == name_upper:
                            return str(tz2)
                    except Exception:
                        continue
            # c) Mapping {"LANG HOTEL": {"timezone":"..."}}
            if isinstance(ctx, dict):
                node = ctx.get(name) or ctx.get(name_upper) or ctx.get(name_lower)
                if isinstance(node, dict) and node.get('timezone'):
                    return str(node.get('timezone'))
    except Exception:
        pass
    return ""


def hotel_now_local(hotel_name: str) -> datetime:
    tz_str = get_hotel_timezone_str(hotel_name)
    if not tz_str:
        raise ValueError("❌ Missing hotel timezone")
    tzinfo = _resolve_tzinfo(tz_str)
    if tzinfo is None:
        raise ValueError("❌ Missing hotel timezone")
    return datetime.now(tzinfo)


def hotel_local_today(hotel_name: str):
    return hotel_now_local(hotel_name).date()


def to_local_iso(dt: datetime, tz_str: str) -> str:
    """Convert an aware or naive datetime to ISO string in the given timezone."""
    if not tz_str:
        return dt.isoformat() if hasattr(dt, 'isoformat') else str(dt)
    tz = _resolve_tzinfo(tz_str)
    if tz is None:
        return dt.isoformat() if hasattr(dt, 'isoformat') else str(dt)
    if getattr(dt, 'tzinfo', None) is None:
        return dt.replace(tzinfo=tz).isoformat()
    return dt.astimezone(tz).isoformat()


def _resolve_tzinfo(tz_str: str):
    """
    Resolve tz_str to a tzinfo. Supports IANA names (via ZoneInfo) and common offset strings:
    - "GMT+8", "GMT+08:00", "UTC+8", "+08:00", "-05:30" (case-insensitive)
    Returns tzinfo or None on failure.
    """
    s = str(tz_str or '').strip()
    # Try IANA first
    if ZoneInfo is not None:
        try:
            return ZoneInfo(s)
        except Exception:
            pass
    # Parse offset patterns
    m = re.match(r'^(?:GMT|UTC)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$', s, re.IGNORECASE)
    if not m:
        # Also accept "+08:00" or "-0530"
        m = re.match(r'^([+-])\s*(\d{1,2})(?::?(\d{2}))?$', s)
    if m:
        sign = -1 if m.group(1) == '-' else 1
        hours = int(m.group(2) or 0)
        minutes = int(m.group(3) or 0)
        offset = sign * (hours * 60 + minutes)
        return timezone(timedelta(minutes=offset))
    # Accept "Etc/GMT-8" style
    if ZoneInfo is not None and s.upper().startswith('ETC/GMT'):
        try:
            return ZoneInfo(s)
        except Exception:
            pass
    return None


