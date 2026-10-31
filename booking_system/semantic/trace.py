"""Investigation traces and stall detection for the Catalog Agent."""

import hashlib
import re
import threading
import time
from psycopg2.extras import Json

_QUERY_BUDGET_S = 20
_MAX_ATTEMPTS = 5


def fingerprint(sql):
    text = str(sql or "").lower()
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"'[^']*'", "'?'", text)
    text = re.sub(r"\b\d+\b", "?", text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def stall_reason(attempts, elapsed_s, recursion_hit=False):
    if recursion_hit:
        return "recursion_limit"
    if elapsed_s >= _QUERY_BUDGET_S:
        return "time_budget"
    if len(attempts) >= _MAX_ATTEMPTS:
        return "attempt_budget"
    prints = {}
    errors = {}
    for item in attempts or []:
        print_key = str(item.get("fingerprint") or "")
        if print_key:
            prints[print_key] = prints.get(print_key, 0) + 1
            if prints[print_key] >= 2:
                return "repeat_sql"
        err = str(item.get("error") or "").strip()
        if err:
            class_key = err.split(":")[0][:80]
            errors[class_key] = errors.get(class_key, 0) + 1
            if errors[class_key] >= 2:
                return "repeat_error"
    return ""


def save_trace(hotel, question, catalog_ids, catalog_versions, attempts, stall, row_count, elapsed_ms, reply, error, retrieval=None):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            INSERT INTO investigation_traces (
                hotel_name, question, catalog_ids, catalog_versions, attempts,
                stall_reason, row_count, elapsed_ms, reply_excerpt, error, retrieval
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING trace_id
            """,
            (
                str(hotel or "").upper(),
                str(question or "")[:4000],
                Json(list(catalog_ids or [])),
                Json(list(catalog_versions or [])),
                Json(list(attempts or [])),
                str(stall or ""),
                int(row_count or 0),
                int(elapsed_ms or 0),
                str(reply or "")[:2000],
                str(error or "")[:2000],
                Json(retrieval or {}),
            ),
        )
        return cur.fetchone()[0]

    return _with(run)


def load_trace(trace_id):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT trace_id, hotel_name, question, catalog_ids, catalog_versions, attempts,
                   stall_reason, row_count, elapsed_ms, reply_excerpt, error, retrieval
            FROM investigation_traces WHERE trace_id = %s
            """,
            (int(trace_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "trace_id": row[0],
            "hotel_name": row[1],
            "question": row[2],
            "catalog_ids": row[3],
            "catalog_versions": row[4],
            "attempts": row[5],
            "stall_reason": row[6],
            "row_count": row[7],
            "elapsed_ms": row[8],
            "reply_excerpt": row[9],
            "error": row[10],
            "retrieval": row[11] if isinstance(row[11], dict) else {},
        }

    return _with(run)


_AGENT_MS = {}


def catalog_agent_ms(trace_id):
    """Elapsed Catalog Agent time, recorded outside the user turn."""
    return _AGENT_MS.get(int(trace_id)) if trace_id is not None else None


def enqueue_catalog_agent(trace_id):
    """Fire Catalog Agent off the chat turn. Its duration is not part of the reply time."""
    import os
    from booking_system.semantic.review_flow import learn

    def worker():
        started = time.monotonic()
        try:
            learn(trace_id)
        except Exception:
            pass
        finally:
            _AGENT_MS[int(trace_id)] = int((time.monotonic() - started) * 1000)

    if os.getenv("CATALOG_AGENT_SYNC") == "1":
        worker()
        return catalog_agent_ms(trace_id)

    thread = threading.Thread(target=worker, name="catalog-agent-" + str(trace_id), daemon=False)
    thread.start()
    return thread


def enqueue_resume(trace_ids):
    """Review blocked investigations again off the request clock."""
    from booking_system.semantic.review_flow import resume

    def worker():
        for trace_id in trace_ids or []:
            try:
                resume(int(trace_id))
            except Exception:
                pass

    thread = threading.Thread(target=worker, name="catalog-resume", daemon=False)
    thread.start()
    return thread


class InvestigationClock:
    def __init__(self):
        self.started = time.monotonic()
        self.attempts = []

    def elapsed(self):
        return time.monotonic() - self.started

    def elapsed_ms(self):
        return int(self.elapsed() * 1000)

    def over_budget(self):
        return self.elapsed() >= _QUERY_BUDGET_S

    def record(self, sql, error="", rows=0):
        self.attempts.append({
            "sql": str(sql or "")[:4000],
            "fingerprint": fingerprint(sql),
            "error": str(error or "")[:1000],
            "rows": int(rows or 0),
            "elapsed_ms": self.elapsed_ms(),
        })

    def reason(self, recursion_hit=False):
        return stall_reason(self.attempts, self.elapsed(), recursion_hit=recursion_hit)
