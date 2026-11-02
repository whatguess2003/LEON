"""Read-only database access for the stress run: ground truth, state checks, and the records each desk turn leaves behind.
Every query runs inside a transaction_read_only transaction. A dropped connection is retried; the pool throws the broken one away."""

import json
import time
from decimal import Decimal

import psycopg2

from booking_system import db

READ_ONLY = (("transaction_read_only", "on"),)
_READ_TRIES = 5


def _plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def read(fn):
    caught = None
    for attempt in range(_READ_TRIES):
        try:
            return _plain(db.transaction(fn, settings=READ_ONLY))
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as error:
            caught = error
            time.sleep(min(30, 2 ** attempt))
    raise caught


def rows(sql, params):
    def run(cur):
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    return read(run)


def sandbox():
    """SANDBOX HOTEL and its manager."""
    def run(cur):
        cur.execute("SELECT hotel_id, timezone, currency FROM hotel WHERE hotel_name = 'SANDBOX HOTEL'")
        hotel = dict(cur.fetchone())
        cur.execute("""SELECT a.user_id FROM user_account a JOIN hotel_user h ON h.user_id = a.user_id
                       WHERE h.hotel_id = %s AND a.user_name = 'SANDBOX MANAGER'""", (hotel["hotel_id"],))
        hotel["user_id"] = cur.fetchone()["user_id"]
        cur.execute("SELECT now() AS now, (now() AT TIME ZONE %s)::date AS today", (hotel["timezone"],))
        hotel.update(dict(cur.fetchone()))
        return hotel
    return read(run)


def truth(scenario, params):
    found = rows(scenario["truth"], params)
    return found[0] if found else {}


def checks(scenario, params):
    out = []
    for check in scenario.get("checks") or []:
        got = (rows(check["sql"], params) or [{}])[0]
        out.append({"label": check["label"], "got": got, "expect": check["expect"],
                    "ok": all(got.get(key) == value for key, value in check["expect"].items())})
    return out


def usage(hotel_id):
    """agent_usage rows for the hotel over the last two days, and the database clock."""
    def run(cur):
        cur.execute("SELECT now() AS now")
        now = cur.fetchone()["now"]
        cur.execute("""SELECT usage_date, origin, agent, calls, input_tokens, output_tokens FROM agent_usage
                       WHERE hotel_id = %s AND usage_date >= (now() - interval '2 days')::date""", (hotel_id,))
        return {"now": now, "rows": [dict(row) for row in cur.fetchall()]}
    return read(run)


def usage_delta(before, after):
    key = lambda row: (str(row["usage_date"]), row["origin"], row["agent"])
    old = {key(row): row for row in before["rows"]}
    out = {}
    for row in after["rows"]:
        prior = old.get(key(row), {})
        delta = {field: int(row[field] or 0) - int(prior.get(field) or 0) for field in ("calls", "input_tokens", "output_tokens")}
        if any(delta.values()):
            name = row["origin"] + "/" + row["agent"]
            total = out.setdefault(name, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            for field, value in delta.items():
                total[field] += value
    return out


def turn_records(hotel_id, user_id, started, ended):
    """Everything this manager's turn wrote between two database timestamps."""
    def run(cur):
        found = {}
        cur.execute("""SELECT event_id, event_type, payload->>'question' AS question FROM events
                       WHERE hotel_id = %s AND payload->>'manager' = %s AND created_at BETWEEN %s AND %s ORDER BY event_id""",
                    (hotel_id, user_id, started, ended))
        found["events"] = [dict(row) for row in cur.fetchall()]
        events = [row["event_id"] for row in found["events"]]
        cur.execute("""SELECT spec_id, event_id, body->>'route' AS route, body->'recipe_ids' AS recipe_ids, body->>'capability' AS capability,
                              body->>'learn' AS learn, body->>'clarification_kind' AS clarification_kind, body->>'definition_key' AS definition_key,
                              body->>'clarification' AS clarification, body->'figures' AS figures, body->'unresolved' AS unresolved
                       FROM problem_specs WHERE event_id = ANY(%s) ORDER BY spec_id""", (events,))
        found["specs"] = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT task_id, event_id, agent, purpose, status FROM tasks WHERE event_id = ANY(%s) ORDER BY task_id", (events,))
        found["tasks"] = [dict(row) for row in cur.fetchall()]
        tasks = [row["task_id"] for row in found["tasks"]]
        cur.execute("""SELECT run_id, task_id, catalog_id, version, row_count, error, elapsed_ms FROM execution_runs
                       WHERE task_id = ANY(%s) ORDER BY run_id""", (tasks,))
        found["execution_runs"] = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT plan_id, task_id, body->'recipes' AS recipes FROM composition_plans WHERE task_id = ANY(%s)", (tasks,))
        found["composition_plans"] = [dict(row) for row in cur.fetchall()]
        cur.execute("""SELECT task_id, evidence_type, payload->'statements' AS statements, payload->>'error' AS error,
                              payload->>'stall_reason' AS stall_reason FROM evidence WHERE task_id = ANY(%s)""", (tasks,))
        found["evidence"] = [dict(row) for row in cur.fetchall()]
        recipes = sorted({row["catalog_id"] for row in found["execution_runs"]}
                         | {item for spec in found["specs"] for item in (spec["recipe_ids"] or [])})
        cur.execute("SELECT catalog_id, scope, user_id FROM catalogs WHERE catalog_id = ANY(%s)", (recipes,))
        found["recipe_scopes"] = {row["catalog_id"]: row["scope"] for row in cur.fetchall()}
        cur.execute("""SELECT trace_id, stall_reason AS reason, catalog_ids, retrieval->>'gap_fingerprint' AS gap_fingerprint,
                              retrieval->>'status' AS retrieval_status FROM investigation_traces
                       WHERE hotel_id = %s AND retrieval->>'manager' = %s AND created_at BETWEEN %s AND %s ORDER BY trace_id""",
                    (hotel_id, user_id, started, ended))
        found["traces"] = [dict(row) for row in cur.fetchall()]
        traces = [row["trace_id"] for row in found["traces"]]
        cur.execute("""SELECT investigation_id, trace_id, gap_fingerprint, status, current_step, created_at >= %s AS opened_now FROM investigations
                       WHERE hotel_id = %s AND (trace_id = ANY(%s) OR (user_id = %s AND created_at BETWEEN %s AND %s))""",
                    (started, hotel_id, traces, user_id, started, ended))
        found["investigations"] = [dict(row) for row in cur.fetchall()]
        cur.execute("""SELECT gap_fingerprint, occurrences, status, reason FROM gap_candidates
                       WHERE hotel_id = %s AND user_id = %s AND last_seen BETWEEN %s AND %s""", (hotel_id, user_id, started, ended))
        found["gap_candidates"] = [dict(row) for row in cur.fetchall()]
        cur.execute("""SELECT job_id, job_kind, job_status, payload FROM job_queue WHERE hotel_id = %s AND created_at BETWEEN %s AND %s""",
                    (hotel_id, started, ended))
        found["jobs"] = [dict(row) for row in cur.fetchall()]
        cur.execute("""SELECT complaint_id, status, left(message, 300) AS message FROM answer_complaints
                       WHERE hotel_id = %s AND user_id = %s AND created_at BETWEEN %s AND %s""", (hotel_id, user_id, started, ended))
        found["complaints"] = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT state FROM desk_session WHERE hotel_id = %s AND upper(manager_name) = upper(%s)", (hotel_id, user_id))
        session = cur.fetchone()
        found["awaiting"] = dict((session or {}).get("state") or {}).get("awaiting") or {}
        return found
    return read(run)


def route_of(records, held_before=None):
    """personal / user / general / clarify / live / decline from the turn's problem spec. Without a spec: complaint, an answer to
    the question the desk held, or no_spec (the desk-action path, or a spec that could not be written)."""
    specs = records["specs"]
    if not specs:
        if records["complaints"]:
            return "complaint"
        if (held_before or {}).get("kind"):
            return "clarification_answer"
        return "no_spec" if records["events"] else "none"
    spec = specs[-1]
    route = spec["route"] or "unknown"
    if route == "recipe":
        scopes = [records["recipe_scopes"].get(item, "") for item in (spec["recipe_ids"] or [])]
        return "user" if any(scope and scope != "general" for scope in scopes) else "general"
    return route


def gate_of(records):
    """What the gate did with this turn's gap, inferred from the rows it wrote."""
    out = []
    opened = {row["trace_id"] for row in records["investigations"] if row["opened_now"]}
    for trace in records["traces"]:
        out.append({"trace_id": trace["trace_id"], "reason": trace["reason"],
                    "decision": "queue(opened)" if trace["trace_id"] in opened else "attach"})
    traced = {str(trace["gap_fingerprint"] or "").rsplit("|", 1)[0] for trace in records["traces"]}
    for gap in records["gap_candidates"]:
        if gap["gap_fingerprint"] not in traced:
            out.append({"gap": gap["gap_fingerprint"], "decision": gap["status"] or "candidate", "reason": gap["reason"] or "",
                        "occurrences": gap["occurrences"]})
    if not out:
        for spec in records["specs"]:
            if spec["route"] == "live" and spec["learn"]:
                out.append({"decision": "not_queued", "learn": spec["learn"]})
    return out


def sql_of(records):
    statements = []
    for item in records["evidence"]:
        for statement in item.get("statements") or []:
            text = statement if isinstance(statement, str) else json.dumps(statement, default=str)
            statements.append(text[:2000])
    return statements


def gap_outcomes(hotel_id, user_id, gaps):
    """Per gap (gate fingerprint), this manager's investigations of the last day and what the Catalog Agent and reviewer did with them."""
    def run(cur):
        out = {}
        for gap in gaps:
            cur.execute("""SELECT investigation_id, trace_id, status, current_step, next_action, proposal_id, revision_count, tokens_used, created_at, closed_at
                           FROM investigations WHERE hotel_id = %s AND gap_fingerprint = %s AND created_at >= now() - interval '1 day'
                           ORDER BY investigation_id""", (hotel_id, gap + "|" + user_id))
            investigations = [dict(row) for row in cur.fetchall()]
            traces = [row["trace_id"] for row in investigations]
            cur.execute("""SELECT proposal_id, trace_id, action, catalog_id, status, left(diagnosis, 300) AS diagnosis, created_at FROM catalog_proposals
                           WHERE trace_id = ANY(%s) OR proposal_id = ANY(%s) ORDER BY proposal_id""",
                        (traces, [row["proposal_id"] for row in investigations if row["proposal_id"]]))
            proposals = [dict(row) for row in cur.fetchall()]
            ids = [row["proposal_id"] for row in proposals]
            cur.execute("""SELECT proposal_id, baseline_ok, candidate_ok, left(notes, 200) AS notes, created_at FROM catalog_test_runs
                           WHERE proposal_id = ANY(%s) ORDER BY run_id""", (ids,))
            tests = [dict(row) for row in cur.fetchall()]
            cur.execute("""SELECT proposal_id, decision, confidence, findings, unresolved_questions, created_at FROM recipe_reviews
                           WHERE proposal_id = ANY(%s) ORDER BY review_id""", (ids,))
            reviews = [dict(row) for row in cur.fetchall()]
            cur.execute("""SELECT proposal_id, catalog_id, status, left(notes, 300) AS notes, created_at FROM publication_decisions
                           WHERE proposal_id = ANY(%s) ORDER BY decision_id""", (ids,))
            decisions = [dict(row) for row in cur.fetchall()]
            cur.execute("""SELECT v.catalog_id, v.version, v.created_at, c.scope, c.user_id AS owner, c.status FROM catalog_versions v
                           LEFT JOIN catalogs c ON c.catalog_id = v.catalog_id WHERE v.catalog_id = ANY(%s) ORDER BY v.created_at""",
                        ([row["catalog_id"] for row in proposals],))
            versions = [dict(row) for row in cur.fetchall()]
            cur.execute("""SELECT job_kind, job_status, attempts, left(last_error, 200) AS last_error, payload->>'trace_id' AS trace_id FROM job_queue
                           WHERE job_kind IN ('catalog_agent', 'catalog_resume') AND payload->>'trace_id' = ANY(%s)""", ([str(item) for item in traces],))
            jobs = [dict(row) for row in cur.fetchall()]
            out[gap] = {"investigations": investigations, "proposals": proposals, "tests": tests, "reviews": reviews, "decisions": decisions,
                        "versions": versions, "jobs": jobs}
        return out
    return read(run)


def catalog_outcomes(trace_ids, since):
    """Background Catalog Agent and reviewer results for the traces this run opened."""
    def run(cur):
        out = {}
        cur.execute("""SELECT investigation_id, trace_id, gap_fingerprint, status, current_step, next_action, proposal_id, revision_count,
                              tokens_used, closed_at FROM investigations WHERE trace_id = ANY(%s) ORDER BY investigation_id""", (trace_ids,))
        out["investigations"] = [dict(row) for row in cur.fetchall()]
        cur.execute("""SELECT proposal_id, trace_id, action, catalog_id, parent_id, status, left(diagnosis, 400) AS diagnosis, created_at
                       FROM catalog_proposals WHERE trace_id = ANY(%s) ORDER BY proposal_id""", (trace_ids,))
        out["proposals"] = [dict(row) for row in cur.fetchall()]
        proposals = [row["proposal_id"] for row in out["proposals"]]
        cur.execute("""SELECT review_id, proposal_id, decision, confidence, model, jsonb_array_length(coalesce(findings, '[]'::jsonb)) AS findings,
                              unresolved_questions, created_at FROM recipe_reviews WHERE proposal_id = ANY(%s) ORDER BY review_id""", (proposals,))
        out["reviews"] = [dict(row) for row in cur.fetchall()]
        cur.execute("""SELECT decision_id, proposal_id, catalog_id, status, left(notes, 400) AS notes, created_at FROM publication_decisions
                       WHERE proposal_id = ANY(%s) ORDER BY decision_id""", (proposals,))
        out["decisions"] = [dict(row) for row in cur.fetchall()]
        cur.execute("""SELECT run_id, proposal_id, baseline_ok, candidate_ok, left(notes, 300) AS notes FROM catalog_test_runs
                       WHERE proposal_id = ANY(%s) ORDER BY run_id""", (proposals,))
        out["test_runs"] = [dict(row) for row in cur.fetchall()]
        cur.execute("""SELECT v.catalog_id, v.version, v.created_at, v.provenance->>'proposal_id' AS proposal_id, c.scope, c.user_id AS owner, c.status
                       FROM catalog_versions v LEFT JOIN catalogs c ON c.catalog_id = v.catalog_id
                       WHERE v.created_at >= %s ORDER BY v.created_at""", (since,))
        out["new_versions"] = [dict(row) for row in cur.fetchall()]
        cur.execute("""SELECT job_kind, job_status, attempts, left(last_error, 300) AS last_error, payload->>'trace_id' AS trace_id FROM job_queue
                       WHERE job_kind IN ('catalog_agent', 'catalog_resume') AND (payload->>'trace_id')::bigint = ANY(%s)""", (trace_ids,))
        out["jobs"] = [dict(row) for row in cur.fetchall()]
        return out
    return read(run)
