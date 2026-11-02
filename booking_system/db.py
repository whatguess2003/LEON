"""The one Postgres entry point. The URL comes only from DATABASE_URL; connections come from a small per-process pool and
carry no session state between transactions (settings are set_config(..., true)), so a transaction-mode pooler can sit in front."""

import os
import threading

import psycopg2
from psycopg2.extras import RealDictCursor

POOL_MAX = 8
_IDLE = []
_LOCK = threading.Lock()
_SLOTS = threading.BoundedSemaphore(POOL_MAX)
_KEEPALIVE = {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3}


def database_url():
    url = os.environ["DATABASE_URL"]
    if "sslrootcert=system" in url:
        import certifi
        url = url.replace("sslrootcert=system", "sslrootcert=" + certifi.where())
    return url


def connect(autocommit=False):
    """A dedicated connection (DDL, scripts). The caller closes it."""
    conn = psycopg2.connect(database_url())
    conn.autocommit = autocommit
    return conn


def _take():
    """An idle pooled connection, or a new one. A connection costs a TLS handshake through the proxy, so it is kept after use."""
    with _LOCK:
        while _IDLE:
            conn = _IDLE.pop()
            if not conn.closed:
                return conn
    return psycopg2.connect(database_url(), **_KEEPALIVE)


def _give(conn, broken):
    if broken or conn.closed:
        if not conn.closed:
            conn.close()
        return
    with _LOCK:
        _IDLE.append(conn)


def transaction(fn, settings=(), dict_rows=True):
    """fn(cur) inside one transaction: commit on return, rollback on error. settings are (name, value) pairs local to it."""
    _SLOTS.acquire()
    broken = False
    try:
        conn = _take()
    except Exception:
        _SLOTS.release()
        raise
    try:
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=RealDictCursor) if dict_rows else conn.cursor()
        try:
            for name, value in settings:
                cur.execute("SELECT set_config(%s, %s, true)", (name, str(value)))
            result = fn(cur)
            conn.commit()
            return result
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            broken = True
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            if not conn.closed:
                cur.close()
    finally:
        _give(conn, broken)
        _SLOTS.release()


def rows(sql, params=None, settings=()):
    def run(cur):
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    return transaction(run, settings)


def row(sql, params=None, settings=()):
    found = rows(sql, params, settings)
    return found[0] if found else None


def execute(sql, params=None):
    def run(cur):
        cur.execute(sql, params)
        return cur.rowcount
    return transaction(run)
