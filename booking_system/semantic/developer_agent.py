"""Recipe developer. The desk queues one fix after each audit returns. It is not the catalog agent and not the reviewer.

The saved audit_fixes row holds the last five turns, the recipe ids used on those turns, and the audit result.
The job queue runs this module afterward, so the desk reply is not waiting on publication.
"""

import json

_DEVELOPER_MODEL = "gpt-6.1-sol"
_DEVELOPER_EFFORT = "high"
_ROUNDS = 4
_PARAMETERS = ["hotel_id", "start", "end"]
_READY = []

_INSTRUCTIONS = """You are LEON's recipe developer. You do not speak to the user. You are not the catalog agent and not the reviewer.

You receive one saved audit: the last five conversation turns, the recipes used on those turns, and the audit supervisor result. Decide from those whether the recipe that was used is flawed for what the turns asked.

A period is the start and end those turns need, as MM/DD/YYYY. A single day uses the same start and end.

If that recipe is suitable, call record_suitable and publish nothing.

If it is flawed and it measures the same thing, call publish_version with that same catalog id and a corrected template. The server writes a new version of that id and indexes only that catalog.

If the stored recipe measures something else, call publish_new with a new catalog id. publish_version is only for the same measure.

The template is one read-only SELECT or WITH. It includes %(hotel_id)s. It reads the period with to_date(%(start)s, 'MM/DD/YYYY') and to_date(%(end)s, 'MM/DD/YYYY'). The span is the bound start through end, inclusive. It does not use a fixed 7-day or 30-day window.

Call table_columns when you need columns. Call search_sql to check a read before you publish. Then call record_suitable, publish_version, or publish_new.

outputs are the select aliases. output_grain is the grain of one result row. keyphrases are phrases a manager would use for this measure. preconditions say the read is limited to the session hotel and to start through end.
"""


def _json(value):
    from psycopg2.extras import Json
    return Json(value, dumps=lambda item: json.dumps(item, default=str))


def _loaded(value):
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _ids(values):
    found = []
    for item in values or []:
        text = str(item or "").strip()
        if text and text not in found:
            found.append(text)
    return found


def _turns(turns):
    rows = []
    for item in list(turns or [])[-5:]:
        if not isinstance(item, dict):
            continue
        rows.append({"role": str(item.get("role") or ""), "text": str(item.get("text") or "")})
    return rows


def _texts(value):
    return _ids(value)


def ensure_tables():
    """The audit row. Created on use. This does not seed catalogs or rebuild indexes."""
    if _READY:
        return
    from booking_system.content_store import DDL_LOCK_TIMEOUT, _with

    def run(cur):
        cur.execute("SELECT current_setting('lock_timeout')")
        previous = cur.fetchone()[0]
        cur.execute("SET lock_timeout = '" + DDL_LOCK_TIMEOUT + "'")
        try:
            _create_fix_tables(cur)
        finally:
            cur.execute("SELECT set_config('lock_timeout', %s, false)", (previous,))

    _with(run)
    _READY.append(True)


def _create_fix_tables(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_fixes (
            case_id VARCHAR(48) PRIMARY KEY CHECK (left(case_id, 4) = 'aud_'),
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            manager_name VARCHAR NOT NULL,
            turns JSONB NOT NULL,
            recipe_ids JSONB NOT NULL,
            recipes JSONB NOT NULL DEFAULT '[]'::jsonb,
            audit_result JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(16) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'suitable', 'published', 'unresolved')),
            outcome JSONB NOT NULL DEFAULT '{}'::jsonb,
            job_id VARCHAR(48),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS audit_fixes_hotel_idx ON audit_fixes (hotel_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS audit_fixes_status_idx ON audit_fixes (status, created_at)")


def _snapshot(recipe_id, body):
    body = body if isinstance(body, dict) else {}
    solution = body.get("solution") if isinstance(body.get("solution"), dict) else {}
    return {
        "id": str(recipe_id or ""),
        "available": bool(body),
        "title": str(body.get("title") or ""),
        "problem": str(body.get("problem") or ""),
        "parent": str(body.get("parent") or ""),
        "output_grain": str(body.get("output_grain") or ""),
        "outputs": [str(item) for item in body.get("outputs") or []],
        "parameters": [str(item) for item in solution.get("parameters") or []],
        "keyphrases": [str(item) for item in body.get("keyphrases") or []],
        "preconditions": [str(item) for item in body.get("preconditions") or []],
        "sql_template": str(solution.get("sql_template") or ""),
    }


def _snapshots(recipe_ids):
    from booking_system.semantic.registry import active_body

    found = []
    for recipe_id in recipe_ids:
        try:
            body = active_body(recipe_id) or {}
        except Exception:
            body = {}
        found.append(_snapshot(recipe_id, body))
    return found


def load_fix(case_id):
    """The saved audit. Empty when that case is not stored."""
    from booking_system.content_store import _with

    ensure_tables()

    def run(cur):
        cur.execute(
            """
            SELECT case_id, hotel_id, manager_name, turns, recipe_ids, recipes, audit_result, status, outcome, job_id
            FROM audit_fixes WHERE case_id = %s
            """,
            (str(case_id or ""),),
        )
        return cur.fetchone()

    row = _with(run)
    if not row:
        return {}
    return {
        "case_id": row[0],
        "hotel_id": row[1],
        "manager_name": row[2],
        "turns": _loaded(row[3]) or [],
        "recipe_ids": _loaded(row[4]) or [],
        "recipes": _loaded(row[5]) or [],
        "audit_result": _loaded(row[6]) or {},
        "status": str(row[7] or ""),
        "outcome": _loaded(row[8]) or {},
        "job_id": str(row[9] or ""),
    }


def start_fix(hotel_id, manager, turns, recipe_ids, audit_result):
    """Persist this audit and queue the developer in one commit. The row is readable before the desk turn returns."""
    from booking_system import db, jobs
    from booking_system.hotel_registry import new_id

    if "developer_fix" not in jobs.HANDLERS:
        raise RuntimeError("developer fix is not queued")
    ensure_tables()
    case_id = new_id("aud")
    job_id = new_id("job")
    stored_turns = _turns(turns)
    stored_ids = _ids(recipe_ids)
    recipes = _snapshots(stored_ids)
    payload = {
        "case_id": case_id,
        "turns": stored_turns,
        "recipe_ids": stored_ids,
        "audit_result": audit_result or {},
    }

    def run(cur):
        cur.execute(
            """
            INSERT INTO audit_fixes (
                case_id, hotel_id, manager_name, turns, recipe_ids, recipes, audit_result, status, job_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)
            """,
            (
                case_id,
                str(hotel_id or ""),
                str(manager or ""),
                _json(stored_turns),
                _json(stored_ids),
                _json(recipes),
                _json(audit_result or {}),
                job_id,
            ),
        )
        cur.execute(
            """
            INSERT INTO job_queue (job_id, job_kind, dedupe_key, hotel_id, payload, job_status)
            VALUES (%s, 'developer_fix', %s, %s, %s, 'queued')
            """,
            (
                job_id,
                "developer_fix/" + case_id,
                str(hotel_id or "") or None,
                json.dumps(payload, default=str),
            ),
        )

    db.transaction(run)
    return {"case_id": case_id, "job_id": job_id}


def _compact(sql):
    return "".join(str(sql or "").lower().split())


def _contract_error(sql):
    """Why this template is not a hotel period read. Empty when it meets the recipe contract."""
    from booking_system.semantic.query import read_rejection

    text = str(sql or "").strip()
    reason = read_rejection([text])
    if reason:
        return reason
    if "%(start)s" not in text or "%(end)s" not in text:
        return "the read must bind %(start)s and %(end)s"
    compact = _compact(text)
    start = "to_date(%(start)s,'mm/dd/yyyy')"
    end = "to_date(%(end)s,'mm/dd/yyyy')"
    if start not in compact or end not in compact:
        return "read start and end with to_date(%(start)s, 'MM/DD/YYYY') and to_date(%(end)s, 'MM/DD/YYYY')"
    for token in ("interval'7day", 'interval"7day', "interval'30day", 'interval"30day'):
        if token in compact:
            return "the period is the bound start through end, not a fixed 7-day or 30-day window"
    return ""


def _template(body):
    solution = (body or {}).get("solution") if isinstance((body or {}).get("solution"), dict) else {}
    return str(solution.get("sql_template") or "").strip()


def _assemble(catalog_id, arguments, current, parent):
    """One recipe body. Parameters stay hotel_id, start, and end."""
    current = dict(current or {})
    arguments = arguments if isinstance(arguments, dict) else {}
    problem = str(arguments.get("problem") or current.get("problem") or "").strip()
    title = str(arguments.get("title") or current.get("title") or problem).strip()
    phrases = _texts(arguments.get("keyphrases") or current.get("keyphrases") or [])
    if problem and problem not in phrases:
        phrases = [problem] + phrases
    outputs = [item.lower() for item in _texts(arguments.get("outputs") or current.get("outputs") or [])]
    grain = str(arguments.get("output_grain") or current.get("output_grain") or "").strip()
    preconditions = _texts(arguments.get("preconditions") or current.get("preconditions") or [])
    if not preconditions:
        preconditions = [
            "Only the session hotel is bound as %(hotel_id)s. "
            "start and end are MM/DD/YYYY and are read with to_date(that string, 'MM/DD/YYYY'). "
            "A single day uses the same start and end. The read returns the span from start through end inclusive."
        ]
    verification = str(arguments.get("verification") or current.get("verification") or "").strip()
    if not verification:
        verification = "Checked by the developer for the bound hotel, start, and end."
    sql = str(arguments.get("sql_template") or "").strip() or _template(current)
    parent_id = str(current.get("parent") or parent or "B01").strip() or "B01"
    purpose = problem or title or str(catalog_id or "")
    body = dict(current)
    body.update({
        "catalog_id": str(catalog_id or ""),
        "parent": parent_id,
        "title": title or purpose,
        "purpose": purpose,
        "problem": problem,
        "preconditions": preconditions,
        "inputs": {
            "hotel_id": "bound by the platform from the session hotel",
            "start": "MM/DD/YYYY inclusive start, bound as %(start)s and read with to_date(start, 'MM/DD/YYYY')",
            "end": "MM/DD/YYYY inclusive end, bound as %(end)s and read with to_date(end, 'MM/DD/YYYY')",
        },
        "output_grain": grain,
        "outputs": outputs,
        "keyphrases": phrases,
        "aliases": list(phrases),
        "verification": verification,
        "solution": {
            "sql_template": sql,
            "parameters": list(_PARAMETERS),
            "method": "developer",
        },
        "validation": {"expected_value": ""},
    })
    default = str(current.get("default_output") or "")
    body["default_output"] = default if default in outputs else (outputs[0] if outputs else "")
    return body


def _ready(body):
    from booking_system.semantic.recipe import GRAINS, recipe_contract

    missing = recipe_contract(body)
    if missing:
        return "recipe contract " + " ".join(missing)
    if str(body.get("output_grain") or "") not in GRAINS:
        return "output_grain is not a recipe grain"
    names = list(((body.get("solution") or {}).get("parameters")) or [])
    if names != _PARAMETERS:
        return "parameters are hotel_id, start, and end"
    return _contract_error(_template(body))


def _parent(catalog_id):
    from booking_system.content_store import _with

    catalog_id = str(catalog_id or "").strip()
    if not catalog_id:
        return "B01"

    def run(cur):
        cur.execute("SELECT parent_id FROM catalogs WHERE catalog_id = %s", (catalog_id,))
        row = cur.fetchone()
        return str(row[0] or "") if row else ""

    return _with(run) or "B01"


def _exists(catalog_id):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute("SELECT 1 FROM catalogs WHERE catalog_id = %s", (str(catalog_id or ""),))
        return bool(cur.fetchone())

    return bool(_with(run))


def _try_read(hotel_id, body, arguments):
    from booking_system.semantic.query import execute_reads

    start = str((arguments or {}).get("start") or "").strip()
    end = str((arguments or {}).get("end") or "").strip()
    if not start or not end:
        return "start and end are required"
    result = execute_reads([_template(body)], {"hotel_id": str(hotel_id or ""), "start": start, "end": end})
    if result.get("error"):
        return str(result["error"])
    rows = result.get("rows") or []
    if rows:
        present = {str(name).lower() for name in rows[0]}
        absent = [name for name in body.get("outputs") or [] if name not in present]
        if absent:
            return "outputs not returned by the read: " + " ".join(absent)
    return ""


def _matching(recipe_ids, sql):
    from booking_system.semantic.registry import active_body

    wanted = str(sql or "").strip()
    if not wanted:
        return ""
    for recipe_id in recipe_ids or []:
        if _template(active_body(recipe_id) or {}) == wanted:
            return str(recipe_id)
    return ""


def _finish(case_id, status, outcome):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            UPDATE audit_fixes SET status = %s, outcome = %s, updated_at = NOW()
            WHERE case_id = %s AND status = 'pending'
            RETURNING case_id
            """,
            (status, _json(outcome), case_id),
        )
        return cur.fetchone()

    return _with(run)


def _commit(ctx, body, action):
    """Write one catalog version and the audit outcome together. Index only this catalog."""
    from booking_system.content_store import _transaction
    from booking_system.semantic.seed_general import _publish

    def run(cur):
        result = _publish(cur, body, "developer")
        published = result.get("status") == "published"
        outcome = {
            "status": "published" if published else "suitable",
            "action": action if published else "unchanged",
            "catalog_id": result.get("catalog_id"),
            "version": result.get("version"),
            "previous_version": result.get("previous_version"),
        }
        cur.execute(
            """
            UPDATE audit_fixes SET status = %s, outcome = %s, updated_at = NOW()
            WHERE case_id = %s AND status = 'pending'
            RETURNING case_id
            """,
            (outcome["status"], _json(outcome), ctx["case_id"]),
        )
        if not cur.fetchone():
            raise RuntimeError("audit fix was already finished")
        return outcome

    outcome = _transaction(run)
    ctx["done"] = outcome
    return outcome


def _suitable(arguments, ctx):
    outcome = {
        "status": "suitable",
        "catalog_id": str((arguments or {}).get("catalog_id") or ""),
        "reason": str((arguments or {}).get("reason") or ""),
    }
    if not _finish(ctx["case_id"], "suitable", outcome):
        return {"error": "this audit fix is already decided"}
    ctx["done"] = outcome
    return outcome


def _publish_version(arguments, ctx):
    from booking_system.semantic.registry import active_body

    arguments = arguments if isinstance(arguments, dict) else {}
    catalog_id = str(arguments.get("catalog_id") or "").strip()
    if catalog_id not in set(ctx.get("recipe_ids") or []):
        return {"error": "publish a version of a recipe used on these turns"}
    current = active_body(catalog_id) or {}
    if not current:
        return {"error": "that recipe has no active version"}
    body = _assemble(catalog_id, arguments, current, _parent(catalog_id))
    problem = _ready(body)
    if problem:
        return {"error": problem}
    if _template(current) == _template(body):
        return {"error": "that template matches the stored recipe; call record_suitable"}
    problem = _try_read(ctx["hotel_id"], body, arguments)
    if problem:
        return {"error": problem}
    return _commit(ctx, body, "version")


def _publish_new(arguments, ctx):
    arguments = arguments if isinstance(arguments, dict) else {}
    catalog_id = str(arguments.get("catalog_id") or "").strip()
    if not catalog_id or len(catalog_id) > 64:
        return {"error": "catalog_id is required"}
    for char in catalog_id:
        if not (char.isalnum() or char == "_"):
            return {"error": "catalog_id uses letters, digits, and underscores"}
    if catalog_id in set(ctx.get("recipe_ids") or []) or _exists(catalog_id):
        return {"error": "that id already exists; publish a version when it is the same measure"}
    source = str(arguments.get("source_catalog_id") or "").strip()
    body = _assemble(catalog_id, arguments, {}, _parent(source))
    problem = _ready(body)
    if problem:
        return {"error": problem}
    match = _matching(ctx.get("recipe_ids"), _template(body))
    if match:
        return {"error": "that read matches " + match + "; publish a version of that id"}
    problem = _try_read(ctx["hotel_id"], body, arguments)
    if problem:
        return {"error": problem}
    return _commit(ctx, body, "new")


def _search(arguments, ctx):
    from booking_system.semantic.desk import run_sql

    arguments = arguments if isinstance(arguments, dict) else {}
    return run_sql(
        arguments.get("sql"),
        {"start": arguments.get("start"), "end": arguments.get("end")},
        ctx["hotel_id"],
        "",
        "",
    )


def _columns(arguments):
    from booking_system.semantic.audit_agent import _columns as columns
    return columns(arguments)


def _object(raw):
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or ""))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tools():
    from booking_system.semantic.recipe import GRAINS

    grains = list(GRAINS)
    period = {
        "start": {"type": "string"},
        "end": {"type": "string"},
    }
    recipe = {
        "catalog_id": {"type": "string"},
        "title": {"type": "string"},
        "problem": {"type": "string"},
        "output_grain": {"type": "string", "enum": grains},
        "outputs": {"type": "array", "items": {"type": "string"}},
        "keyphrases": {"type": "array", "items": {"type": "string"}},
        "preconditions": {"type": "array", "items": {"type": "string"}},
        "verification": {"type": "string"},
        "sql_template": {"type": "string"},
    }
    recipe.update(period)
    return (
        {
            "type": "function",
            "name": "table_columns",
            "description": "Columns of one public table, used to write a corrected read.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"table": {"type": "string"}},
                "required": ["table"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "search_sql",
            "description": (
                "Run one read-only SELECT or WITH for this hotel. "
                "Include %(hotel_id)s. Pass start and end as MM/DD/YYYY when the statement uses them. "
                "The server sets hotel_id, refuses another hotel, and times the statement out. This does not publish."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}, **period},
                "required": ["sql", "start", "end"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "record_suitable",
            "description": "The recipe used on these turns is suitable. Record that and publish nothing.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "catalog_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["catalog_id", "reason"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "publish_version",
            "description": (
                "Publish a new version of a recipe used on these turns when it measures the same thing and its template is flawed. "
                "start and end are the MM/DD/YYYY period used to check the read. A single day uses the same start and end."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": recipe,
                "required": list(recipe),
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "publish_new",
            "description": (
                "Publish a new catalog id when the stored recipe measures something else. "
                "source_catalog_id is the recipe that was used, or empty. "
                "start and end are the MM/DD/YYYY period used to check the read."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"source_catalog_id": {"type": "string"}, **recipe},
                "required": ["source_catalog_id", *list(recipe)],
                "additionalProperties": False,
            },
        },
    )


def _dispatch(name, arguments, ctx):
    arguments = arguments if isinstance(arguments, dict) else {}
    calls = {
        "table_columns": lambda: _columns(arguments),
        "search_sql": lambda: _search(arguments, ctx),
        "record_suitable": lambda: _suitable(arguments, ctx),
        "publish_version": lambda: _publish_version(arguments, ctx),
        "publish_new": lambda: _publish_new(arguments, ctx),
    }
    call = calls.get(str(name or ""))
    if call is None:
        return {"error": "unknown tool"}
    try:
        return call()
    except Exception as exc:
        return {"error": str(exc)[:500]}


def _function_calls(response):
    found = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") != "function_call":
            continue
        found.append({
            "name": str(getattr(item, "name", "") or ""),
            "call_id": str(getattr(item, "call_id", "") or ""),
            "arguments": _object(getattr(item, "arguments", "")),
        })
    return found


def _ordered(calls):
    """Check a read before a publication when the model sends both in one response."""
    finish = {"record_suitable", "publish_version", "publish_new"}
    first = [call for call in calls if call["name"] not in finish]
    second = [call for call in calls if call["name"] in finish]
    return first + second


def _packet(case):
    return {
        "turns": list(case.get("turns") or []),
        "recipe_ids": list(case.get("recipe_ids") or []),
        "recipes": list(case.get("recipes") or []),
        "audit_result": case.get("audit_result") or {},
    }


def _develop(case):
    import os
    import httpx
    from openai import OpenAI

    ctx = {
        "case_id": case["case_id"],
        "hotel_id": str(case.get("hotel_id") or ""),
        "recipe_ids": _ids(case.get("recipe_ids")),
        "done": None,
    }
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=httpx.Timeout(None))
    items = [{
        "role": "user",
        "content": [{"type": "input_text", "text": json.dumps(_packet(case), default=str)}],
    }]
    tools = list(_tools())
    for _round in range(_ROUNDS):
        response = client.responses.create(
            model=_DEVELOPER_MODEL,
            instructions=_INSTRUCTIONS,
            input=items,
            tools=tools,
            reasoning={"effort": _DEVELOPER_EFFORT},
            store=False,
            max_output_tokens=64000,
        )
        calls = _ordered(_function_calls(response))
        if not calls:
            break
        items.extend(getattr(response, "output", None) or [])
        for call in calls:
            if ctx.get("done"):
                result = {"error": "this audit fix is already decided"}
            else:
                result = _dispatch(call["name"], call["arguments"], ctx)
            items.append({
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": json.dumps(result, default=str),
            })
        if ctx.get("done"):
            return ctx["done"]
    outcome = {"status": "unresolved", "reason": "the developer did not finish"}
    _finish(case["case_id"], "unresolved", outcome)
    return outcome


def fix(case_id):
    """Run one queued developer fix. A finished case is left as it was recorded."""
    case = load_fix(case_id)
    if not case:
        raise RuntimeError("audit fix not found")
    if case.get("status") in ("suitable", "published", "unresolved"):
        return {"case_id": case["case_id"], "status": case["status"], "outcome": case.get("outcome") or {}}
    return _develop(case)
