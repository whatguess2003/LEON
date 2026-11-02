"""hotel: keyed by hotel_id ('htl_' + uuid4); hotel_name is its unique, indexed name. Descriptive content is the hotel_profile document.
Every child table carries hotel_id itself."""

import uuid
from zoneinfo import ZoneInfo

from booking_system.content_store import DDL_LOCK_TIMEOUT, _transaction, _with, client

_COLUMNS = ("hotel_id", "hotel_name", "currency", "timezone", "channel_hotel_code")
_CACHE = {}

profile_col = client["leon_hotel_memory"]["hotel_profile"]


def new_id(prefix):
    return prefix + "_" + str(uuid.uuid4())


def ensure_schema(cur):
    """Autocommit cursor. Each DDL waits at most DDL_LOCK_TIMEOUT; a step already in place reads the catalog and takes no lock."""
    cur.execute("SET lock_timeout = '" + DDL_LOCK_TIMEOUT + "'")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS hotel (
            hotel_id VARCHAR(48) PRIMARY KEY CHECK (left(hotel_id, 4) = 'htl_'),
            hotel_name VARCHAR(255) NOT NULL UNIQUE CHECK (hotel_name = UPPER(btrim(hotel_name)) AND hotel_name <> ''),
            currency CHAR(3) NOT NULL CHECK (currency = UPPER(currency)),
            timezone VARCHAR(64) NOT NULL,
            channel_hotel_code VARCHAR(64) UNIQUE
        )
        """
    )
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'bind_hotel_id'")
    if cur.fetchone():
        cur.execute("DROP FUNCTION bind_hotel_id() CASCADE")


def _key(hotel_name):
    return str(hotel_name or "").strip().upper()


def _row(values):
    if not values:
        return None
    row = dict(zip(_COLUMNS, values))
    row["currency"] = row["currency"].strip()
    return row


def read_hotel_with(cur, hotel_name):
    cur.execute("SELECT " + ", ".join(_COLUMNS) + " FROM hotel WHERE hotel_name = %s", (_key(hotel_name),))
    return _row(cur.fetchone())


def read_hotel(hotel_name):
    """The hotel row, or None. Rows are kept per process; every write here clears the entry."""
    hotel = _key(hotel_name)
    if hotel not in _CACHE:
        found = _with(lambda cur: read_hotel_with(cur, hotel))
        if found is None:
            return None
        _CACHE[hotel] = found
    return dict(_CACHE[hotel])


def hotel_id(hotel_name):
    return (read_hotel(hotel_name) or {}).get("hotel_id")


def hotel_currency(hotel_name):
    return (read_hotel(hotel_name) or {}).get("currency")


def hotel_timezone(hotel_name):
    return (read_hotel(hotel_name) or {}).get("timezone")


def read_hotel_by_id(hotel_id):
    """The hotel row, or None, from the same per-process cache as read_hotel (keyed by name, cleared by every write here)."""
    wanted = str(hotel_id or "")
    for row in list(_CACHE.values()):
        if row["hotel_id"] == wanted:
            return dict(row)

    def run(cur):
        cur.execute("SELECT " + ", ".join(_COLUMNS) + " FROM hotel WHERE hotel_id = %s", (wanted,))
        return _row(cur.fetchone())
    found = _with(run)
    if found is None:
        return None
    _CACHE[found["hotel_name"]] = found
    return dict(found)


def read_hotel_by_code(channel_hotel_code):
    def run(cur):
        cur.execute("SELECT " + ", ".join(_COLUMNS) + " FROM hotel WHERE channel_hotel_code = %s", (str(channel_hotel_code or "").strip(),))
        return _row(cur.fetchone())
    return _with(run)


def validate(currency, timezone):
    """'' when both are usable, else the reason."""
    code = str(currency or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        return "Invalid hotel_currency: " + str(currency or "")
    try:
        ZoneInfo(str(timezone or "").strip())
    except Exception:
        return "Invalid hotel_timezone: " + str(timezone or "")
    return ""


def write_hotel(cur, hotel_name, currency, timezone):
    """READ first. No row: CREATE (operating times are the hotel's settings, asked at onboarding). A different currency or timezone: UPDATE."""
    hotel = _key(hotel_name)
    code = str(currency or "").strip().upper()
    zone = str(timezone or "").strip()
    _CACHE.pop(hotel, None)
    current = read_hotel_with(cur, hotel)
    if current is None:
        cur.execute(
            """
            INSERT INTO hotel (hotel_id, hotel_name, currency, timezone)
            VALUES (%s, %s, %s, %s)
            """,
            (new_id("htl"), hotel, code, zone),
        )
        return "created"
    if current["currency"] == code and current["timezone"] == zone:
        return "unchanged"
    cur.execute("UPDATE hotel SET currency = %s, timezone = %s WHERE hotel_name = %s", (code, zone, hotel))
    return "updated"


def write_channel_code(hotel_id, channel_hotel_code):
    """READ first: the code may belong to one hotel only. Returns '' or the reason it was not saved."""
    code = str(channel_hotel_code or "").strip()

    def run(cur):
        cur.execute("SELECT hotel_name FROM hotel WHERE hotel_id = %s", (hotel_id,))
        own = cur.fetchone()
        if own is None:
            return "Hotel " + str(hotel_id or "") + " is not registered"
        _CACHE.pop(own[0], None)
        cur.execute("SELECT hotel_name FROM hotel WHERE channel_hotel_code = %s AND hotel_id <> %s", (code, hotel_id))
        other = cur.fetchone()
        if other:
            return "Hotel code " + code + " is linked to " + other[0]
        cur.execute("UPDATE hotel SET channel_hotel_code = %s WHERE hotel_id = %s", (code, hotel_id))
        return ""
    reason = _transaction(run)
    for name, row in list(_CACHE.items()):
        if row["hotel_id"] == hotel_id:
            _CACHE.pop(name, None)
    return reason


def write_profile(cur, hotel_id, city_name):
    """READ the hotel_profile document (key hotel_id) on the caller's cursor, then create it or set the city. An unknown city stays None."""
    city = str(city_name or "").strip().upper() or None
    current = profile_col.find_one({"hotel_id": hotel_id})
    if current and (city is None or current.get("city_name") == city):
        return
    key, body = profile_col.staged_update({"hotel_id": hotel_id}, {"$set": {"hotel_id": hotel_id, "city_name": city}}, upsert=True)
    profile_col.write_in(cur, key, body)
