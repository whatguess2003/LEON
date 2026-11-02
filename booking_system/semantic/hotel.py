"""The semantic layer keys every row and read by hotel_id. The desk chat names its hotel; the id is read from the hotel row once."""

from datetime import datetime
from zoneinfo import ZoneInfo

_ROWS = {}
_IDS = {}


def session_hotel_id(hotel_name):
    """The id of the hotel the chat names. A registered name keeps its id for the process."""
    key = str(hotel_name or "")
    if key not in _IDS:
        from booking_system.hotel_registry import hotel_id
        found = hotel_id(key) if key else ""
        if not found:
            return ""
        _IDS[key] = found
    return _IDS[key]


def hotel_row(hotel_id):
    """The hotel row, or None. Rows are kept per process."""
    key = str(hotel_id or "")
    if key not in _ROWS:
        from booking_system.hotel_registry import read_hotel_by_id
        found = read_hotel_by_id(key) if key else None
        if found is None:
            return None
        _ROWS[key] = found
    return dict(_ROWS[key])


def hotel_zone(hotel_id):
    return str((hotel_row(hotel_id) or {}).get("timezone") or "")


def hotel_today(hotel_id):
    return datetime.now(ZoneInfo(hotel_zone(hotel_id))).date()


def business_dates(hotel_id):
    """The hotel's open business date and latest closed business date, from business_day. Keys are the period values a spec can select."""
    from booking_system.content_store import _with

    def read(cur):
        cur.execute(
            """
            SELECT
                (SELECT business_date FROM business_day WHERE hotel_id = %s AND status = 'open'),
                (SELECT max(business_date) FROM business_day WHERE hotel_id = %s AND status = 'closed')
            """,
            (str(hotel_id or ""), str(hotel_id or "")),
        )
        return cur.fetchone()

    opened, closed = _with(read) or (None, None)
    found = {}
    for key, value in (("open_business_date", opened), ("latest_closed_business_date", closed)):
        if value is None:
            continue
        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
        found[key] = text[:10]
    return found


def hotel_label(hotel_id):
    return str((hotel_row(hotel_id) or {}).get("hotel_name") or "")
