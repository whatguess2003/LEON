"""Audit supervisor. The desk calls it only when the desk model chooses the audit tool. It does not replace the desk,
and it does not call the catalog agent or the reviewer. It returns the verdict for this turn.
The desk then stores that audit and queues the developer; this module does not publish a recipe."""

import json
import os

_AUDIT_MODEL = "gpt-6.1-sol"
_AUDIT_EFFORT = "high"
_ROUNDS = 4

_INSTRUCTIONS = """You are LEON's audit supervisor. You do not speak to the user. LEON will say your result.

You are called because the user flagged, suspected, or claimed that LEON's answer is wrong. You receive the last conversation turns and the stored recipes used on those turns. Decide from those turns and from the rows you read. Do not publish a recipe and do not write a second copy of one.

Do this in order:
1. Decide whether the user is right that LEON's answer was wrong.
2. If a recipe already used is suitable, call reuse_recipe with that recipe id and the start and end the period needs. start and end are MM/DD/YYYY. A single day uses the same start and end. The server binds hotel_id from the session and runs the stored template.
3. If the recipe is flawed, or no stored recipe was used, call search_sql with one read-only SELECT or WITH that finds the correct figure. Include %(hotel_id)s. Use %(start)s and %(end)s for the period, passed as MM/DD/YYYY. The server binds hotel_id, refuses another hotel, and times the statement out. Call table_columns only when you need columns to write that read.
4. Call report after a read returns. user_was_right is true only when the user was right that the answer was wrong. corrected_figure is the figure the rows support. recipe_outcome is reused when the stored recipe was run, and flawed when a custom read was required. say is the result LEON can tell the user, covering those three facts.
"""

_TOOLS = (
    {
        "type": "function",
        "name": "reuse_recipe",
        "description": (
            "Re-run one stored recipe template for this hotel session. "
            "recipe_id is one of the recipes already used. "
            "start and end are MM/DD/YYYY. A single day uses the same start and end. "
            "The server sets hotel_id. This does not copy or publish the recipe."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "recipe_id": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["recipe_id", "start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "search_sql",
        "description": (
            "Run one custom read-only SELECT or WITH when the stored recipe is flawed. "
            "The statement must include %(hotel_id)s. "
            "Pass start and end as MM/DD/YYYY when the statement uses %(start)s and %(end)s. "
            "The server sets hotel_id, refuses another hotel, and times the statement out. "
            "This does not publish a recipe."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["sql", "start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "table_columns",
        "description": "Columns of one public table, used only to write a custom read when the stored recipe is flawed.",
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
        "name": "report",
        "description": (
            "Return the supervisor result after a read. "
            "user_was_right is whether the user was right that LEON's answer was wrong. "
            "corrected_figure is the figure the rows support. "
            "recipe_outcome is reused when the stored recipe was run again, or flawed when a custom read was required. "
            "say is what LEON can tell the user."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "user_was_right": {"type": "boolean"},
                "corrected_figure": {"type": "string"},
                "recipe_outcome": {"type": "string", "enum": ["reused", "flawed"]},
                "say": {"type": "string"},
            },
            "required": ["user_was_right", "corrected_figure", "recipe_outcome", "say"],
            "additionalProperties": False,
        },
    },
)


def _card(catalog_id, manager):
    """The stored recipe the audit may reuse. Reading it does not write another copy."""
    from booking_system.semantic.context_engine import parameter_names
    from booking_system.semantic.registry import active_pages, visible_to

    page = (active_pages([catalog_id]) or {}).get(str(catalog_id or ""))
    if not page or not visible_to(page, manager):
        return {"id": str(catalog_id or ""), "available": False}
    body = page.get("body") or {}
    solution = body.get("solution") or {}
    return {
        "id": str(catalog_id or ""),
        "available": True,
        "problem": str(body.get("problem") or ""),
        "grain": str(body.get("output_grain") or ""),
        "columns": [str(name) for name in body.get("outputs") or []],
        "parameters": parameter_names(body),
        "sql_template": str(solution.get("sql_template") or "").strip(),
    }


def _object(raw):
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or ""))
    except Exception:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _period(arguments):
    arguments = arguments if isinstance(arguments, dict) else {}
    return {"start": arguments.get("start"), "end": arguments.get("end")}


def _reuse(arguments, ctx):
    """Re-run a stored template for a recipe already used on these turns. This does not write a recipe."""
    from booking_system.semantic.desk import run_sql

    recipe_id = str(arguments.get("recipe_id") or "").strip()
    if recipe_id not in set(ctx.get("recipe_ids") or []):
        return {"rows": [], "error": "that recipe was not used on these turns"}
    result = run_sql("", _period(arguments), ctx["hotel_id"], recipe_id, ctx["manager"])
    ctx["last_read"] = result
    return result


def _search(arguments, ctx):
    """Custom read. Same gateway as the desk's run_sql. Not a desk tool."""
    from booking_system.semantic.desk import run_sql

    result = run_sql(arguments.get("sql"), _period(arguments), ctx["hotel_id"], "", ctx["manager"])
    ctx["last_read"] = result
    return result


def _columns(arguments):
    from booking_system.content_store import _with

    name = str((arguments or {}).get("table") or "").strip()

    def read(cur):
        cur.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (name,),
        )
        return [column + " " + data_type for column, data_type in cur.fetchall()]

    try:
        columns = _with(read)
    except Exception:
        return {"table": name, "columns": [], "error": "columns unavailable"}
    return {"table": name, "columns": columns or []}


def _report(arguments, ctx):
    arguments = arguments if isinstance(arguments, dict) else {}
    outcome = str(arguments.get("recipe_outcome") or "")
    if outcome not in ("reused", "flawed"):
        outcome = ""
    right = arguments.get("user_was_right")
    if not isinstance(right, bool):
        right = None
    read = ctx.get("last_read") or {}
    verdict = {
        "user_was_right": right,
        "corrected_figure": str(arguments.get("corrected_figure") or ""),
        "recipe_outcome": outcome,
        "say": str(arguments.get("say") or ""),
        "rows": read.get("rows") or [],
        "error": read.get("error") or "",
    }
    ctx["verdict"] = verdict
    return verdict


def _unfinished(ctx):
    read = ctx.get("last_read") or {}
    return {
        "user_was_right": None,
        "corrected_figure": "",
        "recipe_outcome": "",
        "say": "The audit could not confirm that figure.",
        "rows": read.get("rows") or [],
        "error": read.get("error") or "audit did not report",
    }


def _dispatch(name, arguments, ctx):
    arguments = arguments if isinstance(arguments, dict) else {}
    calls = {
        "reuse_recipe": lambda: _reuse(arguments, ctx),
        "search_sql": lambda: _search(arguments, ctx),
        "table_columns": lambda: _columns(arguments),
        "report": lambda: _report(arguments, ctx),
    }
    call = calls.get(str(name or ""))
    if call is None:
        return {"error": "unknown tool"}
    try:
        return call()
    except Exception:
        return {"error": "audit tool failed"}


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
    """Run a read before report when the model sends both in one response."""
    reads = [call for call in calls if call["name"] != "report"]
    reports = [call for call in calls if call["name"] == "report"]
    return reads + reports


def _supervise_call(packet, ctx):
    import httpx
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=httpx.Timeout(None))
    items = [{
        "role": "user",
        "content": [{"type": "input_text", "text": json.dumps(packet or {}, default=str)}],
    }]
    for _round in range(_ROUNDS):
        response = client.responses.create(
            model=_AUDIT_MODEL,
            instructions=_INSTRUCTIONS,
            input=items,
            tools=list(_TOOLS),
            reasoning={"effort": _AUDIT_EFFORT},
            store=False,
            max_output_tokens=64000,
        )
        calls = _ordered(_function_calls(response))
        if not calls:
            break
        items.extend(getattr(response, "output", None) or [])
        for call in calls:
            result = _dispatch(call["name"], call["arguments"], ctx)
            items.append({
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": json.dumps(result, default=str),
            })
        if ctx.get("verdict"):
            return ctx["verdict"]
    return _unfinished(ctx)


def supervise(hotel_id, manager, turns, recipe_ids):
    """One audit. Turns are the last conversation turns. recipe_ids are the recipes used on those turns."""
    ctx = {
        "hotel_id": str(hotel_id or ""),
        "manager": str(manager or ""),
        "recipe_ids": [str(item or "").strip() for item in list(recipe_ids or []) if str(item or "").strip()],
        "last_read": {},
        "verdict": None,
    }
    packet = {
        "turns": list(turns or []),
        "recipes": [_card(recipe_id, manager) for recipe_id in list(recipe_ids or [])],
    }
    try:
        return _supervise_call(packet, ctx)
    except Exception:
        return {
            "user_was_right": None,
            "corrected_figure": "",
            "recipe_outcome": "",
            "say": "The audit could not be completed.",
            "rows": [],
            "error": "audit unavailable",
        }
