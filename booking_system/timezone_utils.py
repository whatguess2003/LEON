import re
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None  # type: ignore


def get_hotel_timezone_str(hotel_name: str) -> str:
    """hotel.timezone; '' when the hotel is not registered."""
    if not hotel_name:
        return ""
    from booking_system.hotel_registry import hotel_timezone
    return hotel_timezone(hotel_name) or ""


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
