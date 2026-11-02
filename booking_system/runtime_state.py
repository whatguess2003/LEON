"""Desk chat, sticky topic, and accepted SQL. Stored in PostgreSQL, keyed by hotel_id. Callers name the session hotel."""

import contextvars
import copy

from psycopg2.extras import Json

from booking_system.content_store import _with, hotel_column

_SESSIONS = contextvars.ContextVar("desk_sessions", default=None)


def begin_turn():
    """Session reads in this context share one read per chat until end_turn. Writes update it."""
    return _SESSIONS.set({})


def end_turn(token):
    _SESSIONS.reset(token)


def chat_ddl(cur, seen=None):
    """The chat tables. The hotel table exists first. seen is the caller's schema_catalog snapshot."""
    chats = (("desk_dialog", "turns JSONB"), ("desk_session", "state JSONB"), ("desk_topic", "topic VARCHAR"))
    cur.execute(";\n".join(
        ["CREATE TABLE IF NOT EXISTS " + table + " (hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id), "
         "manager_name VARCHAR NOT NULL, " + value + " NOT NULL, PRIMARY KEY (hotel_id, manager_name))" for table, value in chats]
        + ["""CREATE TABLE IF NOT EXISTS read_sql_cache (
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            cache_key VARCHAR NOT NULL,
            body JSONB NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (hotel_id, cache_key)
        )"""]))
    for table, _value in chats:
        hotel_column(cur, table, ("hotel_id", "manager_name"), seen)
    hotel_column(cur, "read_sql_cache", ("hotel_id", "cache_key"), seen)


def _hotel_id(hotel):
    from booking_system.semantic.hotel import session_hotel_id
    return session_hotel_id(str(hotel or "").strip().upper()) if str(hotel or "").strip() else ""


def _pair(hotel, manager):
    return _hotel_id(hotel), str(manager or "").strip().upper()


def read_turns(hotel, manager):
    hotel_id, manager_name = _pair(hotel, manager)
    if not hotel_id or not manager_name:
        return []

    def run(cur):
        cur.execute(
            "SELECT turns FROM desk_dialog WHERE hotel_id = %s AND manager_name = %s",
            (hotel_id, manager_name),
        )
        row = cur.fetchone()
        return row[0] if row else []

    loaded = _with(run)
    return loaded if isinstance(loaded, list) else []


def write_turns(hotel, manager, turns):
    hotel_id, manager_name = _pair(hotel, manager)
    if not hotel_id or not manager_name:
        return
    body = list(turns or [])[-20:]

    def run(cur):
        cur.execute(
            """
            INSERT INTO desk_dialog (hotel_id, manager_name, turns)
            VALUES (%s, %s, %s)
            ON CONFLICT (hotel_id, manager_name) DO UPDATE SET turns = EXCLUDED.turns
            """,
            (hotel_id, manager_name, Json(body)),
        )

    _with(run)


def read_session(hotel, manager):
    """This chat's open clarification and the definition keys already asked. A cleared chat starts empty."""
    hotel_id, manager_name = _pair(hotel, manager)
    if not hotel_id or not manager_name:
        return {}
    cache = _SESSIONS.get()
    if cache is not None and (hotel_id, manager_name) in cache:
        return copy.deepcopy(cache[(hotel_id, manager_name)])

    def run(cur):
        cur.execute(
            "SELECT state FROM desk_session WHERE hotel_id = %s AND manager_name = %s",
            (hotel_id, manager_name),
        )
        row = cur.fetchone()
        return row[0] if row else {}

    loaded = _with(run)
    state = loaded if isinstance(loaded, dict) else {}
    if cache is not None:
        cache[(hotel_id, manager_name)] = copy.deepcopy(state)
    return state


def write_session(hotel, manager, state):
    hotel_id, manager_name = _pair(hotel, manager)
    if not hotel_id or not manager_name:
        return

    def run(cur):
        cur.execute(
            """
            INSERT INTO desk_session (hotel_id, manager_name, state)
            VALUES (%s, %s, %s)
            ON CONFLICT (hotel_id, manager_name) DO UPDATE SET state = EXCLUDED.state
            """,
            (hotel_id, manager_name, Json(dict(state or {}))),
        )

    _with(run)
    cache = _SESSIONS.get()
    if cache is not None:
        cache[(hotel_id, manager_name)] = copy.deepcopy(dict(state or {}))


def delete_session(hotel, manager):
    hotel_id, manager_name = _pair(hotel, manager)
    if not hotel_id or not manager_name:
        return
    cache = _SESSIONS.get()
    if cache is not None:
        cache.pop((hotel_id, manager_name), None)
    _with(lambda cur: cur.execute(
        "DELETE FROM desk_session WHERE hotel_id = %s AND manager_name = %s",
        (hotel_id, manager_name),
    ))


def read_topic(hotel, manager):
    hotel_id, manager_name = _pair(hotel, manager)
    if not hotel_id or not manager_name:
        return ""

    def run(cur):
        cur.execute(
            "SELECT topic FROM desk_topic WHERE hotel_id = %s AND manager_name = %s",
            (hotel_id, manager_name),
        )
        row = cur.fetchone()
        return str(row[0] or "") if row else ""

    return _with(run)


def write_topic(hotel, manager, topic):
    hotel_id, manager_name = _pair(hotel, manager)
    if not hotel_id or not manager_name:
        return
    text = str(topic or "").strip()

    def run(cur):
        if not text:
            cur.execute(
                "DELETE FROM desk_topic WHERE hotel_id = %s AND manager_name = %s",
                (hotel_id, manager_name),
            )
            return
        cur.execute(
            """
            INSERT INTO desk_topic (hotel_id, manager_name, topic)
            VALUES (%s, %s, %s)
            ON CONFLICT (hotel_id, manager_name) DO UPDATE SET topic = EXCLUDED.topic
            """,
            (hotel_id, manager_name, text),
        )

    _with(run)


def read_sql(hotel_id, cache_key):
    if not hotel_id or not cache_key:
        return None

    def run(cur):
        cur.execute(
            """
            SELECT body FROM read_sql_cache
            WHERE hotel_id = %s AND cache_key = %s AND expires_at > NOW()
            """,
            (hotel_id, cache_key),
        )
        row = cur.fetchone()
        return row[0] if row else None

    return _with(run)


def write_sql(hotel_id, cache_key, body):
    if not hotel_id or not cache_key:
        return

    def run(cur):
        cur.execute(
            """
            INSERT INTO read_sql_cache (hotel_id, cache_key, body, expires_at)
            VALUES (%s, %s, %s, NOW() + INTERVAL '30 seconds')
            ON CONFLICT (hotel_id, cache_key) DO UPDATE
            SET body = EXCLUDED.body, expires_at = EXCLUDED.expires_at
            """,
            (hotel_id, cache_key, Json(body)),
        )

    _with(run)


def delete_sql(hotel_id, cache_key):
    if not hotel_id or not cache_key:
        return

    def run(cur):
        cur.execute(
            "DELETE FROM read_sql_cache WHERE hotel_id = %s AND cache_key = %s",
            (hotel_id, cache_key),
        )

    _with(run)


def delete_hotel_sql(hotel_id):
    if not hotel_id:
        return

    def run(cur):
        cur.execute("DELETE FROM read_sql_cache WHERE hotel_id = %s", (hotel_id,))

    _with(run)
