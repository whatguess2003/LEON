"""Investigation traces and stall detection for the Catalog Agent."""

import contextvars
import hashlib
import re
import time
from psycopg2.extras import Json

_FOLLOWUP = contextvars.ContextVar("desk_catalog_followup", default=False)


def followup_token(active):
    """Mark the desk turn that runs after a recipe is published. That turn does not start another catalog job."""
    return _FOLLOWUP.set(bool(active))


def followup_reset(token):
    _FOLLOWUP.reset(token)


def in_followup():
    return bool(_FOLLOWUP.get())

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


def save_trace(hotel_id, question, catalog_ids, catalog_versions, attempts, stall, row_count, elapsed_ms, reply, error, retrieval=None):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            INSERT INTO investigation_traces (
                hotel_id, question, catalog_ids, catalog_versions, attempts,
                stall_reason, row_count, elapsed_ms, reply_excerpt, error, retrieval
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING trace_id
            """,
            (
                str(hotel_id or ""),
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
            SELECT trace_id, hotel_id, question, catalog_ids, catalog_versions, attempts,
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
            "hotel_id": row[1],
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


def queue_missing_recipe(hotel_id, hotel_name, manager, question, dates=None, spec=None, task=None):
    """A desk question with no published recipe. The catalog job is queued and this call returns.
    A follow-up after publication does not queue another one. No statement is written."""
    if in_followup():
        return {"queued": False, "followup": True}
    import json
    from booking_system import jobs
    from booking_system.semantic.registry import open_investigation

    hotel_id = str(hotel_id or "")
    manager = str(manager or "")
    hotel_name = str(hotel_name or "")
    question = " ".join(str(question or "").split())
    if not hotel_id or not manager or not question:
        return {"queued": False}
    spec = spec if isinstance(spec, dict) else {}
    capability = " ".join(str(spec.get("capability") or "").split())
    fingerprint = "missing_recipe|" + capability + "|" + question[:400]
    retrieval = json.loads(json.dumps({
        "source": "missing_recipe",
        "manager": manager,
        "hotel_name": hotel_name,
        "question": question,
        "dates": dates if isinstance(dates, dict) else {"resolutions": []},
        "task": task if isinstance(task, dict) else {},
        "spec": spec,
        "capability": capability,
    }, default=str))
    trace_id = save_trace(hotel_id, question, [], [], [], "missing_recipe", 0, 0, "", "", retrieval)
    opened = open_investigation(hotel_id, fingerprint, trace_id, manager)
    if opened.get("attached"):
        return {"queued": False, "attached": True, "trace_id": trace_id}
    try:
        jobs.enqueue(
            "catalog_agent",
            {"trace_id": int(trace_id)},
            hotel_id=hotel_id,
            dedupe_key="catalog_agent/" + str(trace_id),
        )
    except Exception:
        return {"queued": False, "trace_id": trace_id}
    return {"queued": True, "trace_id": trace_id}


def enqueue_catalog_agent(trace_id):
    """Catalog Agent runs off the chat turn as a durable job (booking_system.jobs). CATALOG_AGENT_SYNC=1 runs it inline for tests."""
    import os
    from booking_system import jobs

    if os.getenv("CATALOG_AGENT_SYNC") == "1":
        from booking_system.semantic.review_flow import learn
        started = time.monotonic()
        try:
            learn(trace_id)
        finally:
            _AGENT_MS[int(trace_id)] = int((time.monotonic() - started) * 1000)
        return catalog_agent_ms(trace_id)
    return jobs.enqueue("catalog_agent", {"trace_id": trace_id}, dedupe_key="catalog_agent/" + str(trace_id))


def enqueue_resume(trace_ids):
    """Review blocked investigations again off the request clock, one durable job per trace."""
    from booking_system import jobs

    return [jobs.enqueue("catalog_resume", {"trace_id": int(trace_id)}) for trace_id in trace_ids or []]


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
