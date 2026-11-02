"""Durable background work in job_queue. A job survives a restart: a running job whose lease lapsed is claimed again, and a failed
attempt is retried with backoff until MAX_ATTEMPTS. One runner per server process; claims use FOR UPDATE SKIP LOCKED, so several
processes share the queue. Kinds map to handlers in HANDLERS; a kind without a handler fails at enqueue."""

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool

from booking_system import db
from booking_system.hotel_registry import new_id

LEASE = "10 minutes"
MAX_ATTEMPTS = 5
IDLE_SECONDS = 5
SCHEDULE_SECONDS = 300


def _catalog_agent(hotel_id, payload):
    from booking_system.semantic.review_flow import learn
    learn(payload["trace_id"])


def _catalog_resume(hotel_id, payload):
    from booking_system.semantic.review_flow import resume
    resume(int(payload["trace_id"]))


def _desk_follow(hotel_id, payload):
    from booking_system.semantic.desk import answer_after_publication
    answer_after_publication(payload.get("trace_id"))


def _pm_daily(hotel_id, payload):
    from booking_system.maintenance_manager import run_daily_pm_generation
    run_daily_pm_generation(hotel_id, payload["today"])


def _aiosell_push(hotel_id, payload):
    from aiosell_channel_manager import push_dates
    push_dates(hotel_id, payload["stay_dates"])


def _developer_fix(hotel_id, payload):
    """One recipe fix queued by an audit. The case row holds the five turns."""
    from booking_system.semantic.developer_agent import fix

    body = payload if isinstance(payload, dict) else {}
    if not body and payload:
        try:
            loaded = json.loads(str(payload))
        except Exception:
            loaded = {}
        body = loaded if isinstance(loaded, dict) else {}
    fix(str(body.get("case_id") or ""))


HANDLERS = {
    "catalog_agent": _catalog_agent,
    "catalog_resume": _catalog_resume,
    "desk_follow": _desk_follow,
    "pm_daily": _pm_daily,
    "aiosell_push": _aiosell_push,
    "developer_fix": _developer_fix,
}


def register(job_kind, handler):
    """Handlers defined outside booking_system (main.py) register at import: handler(hotel_id, payload)."""
    HANDLERS[job_kind] = handler


def enqueue(job_kind, payload, hotel_id=None, dedupe_key=None):
    """The job id, or None when a job with this dedupe_key already exists."""
    if job_kind not in HANDLERS:
        raise ValueError("no handler for job kind " + job_kind)
    found = db.row(
        """INSERT INTO job_queue (job_id, job_kind, dedupe_key, hotel_id, payload, job_status) VALUES (%s, %s, %s, %s, %s, 'queued')
           ON CONFLICT (dedupe_key) DO NOTHING RETURNING job_id""",
        (new_id("job"), job_kind, dedupe_key, hotel_id, json.dumps(payload, default=str)),
    )
    return found["job_id"] if found else None


def _claim():
    return db.row(
        """UPDATE job_queue SET job_status = 'running', attempts = attempts + 1, lease_until = now() + %s::interval, updated_at = now()
           WHERE job_id = (SELECT job_id FROM job_queue
                           WHERE (job_status = 'queued' AND run_after <= now()) OR (job_status = 'running' AND lease_until < now())
                           ORDER BY run_after FOR UPDATE SKIP LOCKED LIMIT 1)
           RETURNING job_id, job_kind, hotel_id, payload, attempts""",
        (LEASE,),
    )


def run_one():
    """Claim and run one job. Returns the job id, or None when nothing is ready."""
    job = _claim()
    if not job:
        return None
    try:
        HANDLERS[job["job_kind"]](job["hotel_id"], job["payload"])
    except Exception as error:
        final = job["attempts"] >= MAX_ATTEMPTS
        db.execute(
            """UPDATE job_queue SET job_status = %s, last_error = %s, lease_until = NULL, updated_at = now(),
                      run_after = now() + (power(2, attempts) || ' minutes')::interval WHERE job_id = %s""",
            ("failed" if final else "queued", str(error)[:2000], job["job_id"]),
        )
        return job["job_id"]
    db.execute("UPDATE job_queue SET job_status = 'done', lease_until = NULL, last_error = NULL, updated_at = now() WHERE job_id = %s",
               (job["job_id"],))
    return job["job_id"]


def schedule_daily():
    """One preventive-maintenance job per hotel per hotel-local date."""
    queued = 0
    for hotel in db.rows("SELECT hotel_id, timezone FROM hotel"):
        today = datetime.now(ZoneInfo(hotel["timezone"])).date().isoformat()
        queued += bool(enqueue("pm_daily", {"today": today}, hotel["hotel_id"], "pm_daily/" + hotel["hotel_id"] + "/" + today))
    return queued


_TASKS = []


async def _runner():
    while True:
        try:
            ran = await run_in_threadpool(run_one)
        except Exception as error:
            print("job runner: " + str(error), flush=True)
            ran = None
        if not ran:
            await asyncio.sleep(IDLE_SECONDS)


async def _scheduler():
    while True:
        try:
            await run_in_threadpool(schedule_daily)
        except Exception as error:
            print("job scheduler: " + str(error), flush=True)
        await asyncio.sleep(SCHEDULE_SECONDS)


def start():
    loop = asyncio.get_running_loop()
    _TASKS.extend([loop.create_task(_runner()), loop.create_task(_scheduler())])


def stop():
    for task in _TASKS:
        task.cancel()
    _TASKS.clear()
