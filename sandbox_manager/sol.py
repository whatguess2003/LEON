"""gpt-6.1-sol at high effort. Drafts questions, judges a reply, and decides a recipe change.

The verdict is the model's. This module does not score the reply with keyword lists.
"""

import json

from sandbox_manager.config import EFFORT, MODEL, public_text

_ROUNDS = 4

_SEARCH_INSTRUCTIONS = """You search the public web for questions a hotel operations manager asks.
Cover occupancy, arrivals and departures, in-house guests, room availability, revenue, rates, payments due, housekeeping, and channels.
Summarize the concrete questions you found. Do not write SQL and do not speak to a guest.
"""

_DRAFT_INSTRUCTIONS = """You draft questions for one hotel manager chatting with an operations assistant.

Use the web-search findings in the conversation, and your own knowledge of hotel operations. Every question must be answerable from the hotel database described in the packet. Skip anything that needs data the tables do not hold.

Call submit_questions with up to 12 questions. Each item is one sentence the manager would type. Name the day the way a person would (today, yesterday, tonight, this month, a named date). Do not ask for a fixed 7-day or 30-day window. Do not repeat a question already stored.
"""

_JUDGE_INSTRUCTIONS = """You judge one reply from a hotel operations assistant. You do not speak to the manager.

You receive the question, the reply, and any recipe the assistant used. When the question can be answered from this hotel's database, call search_sql with one read-only SELECT or WITH that finds the figure. The statement includes %(hotel_id)s. Use %(start)s and %(end)s for the period, passed as MM/DD/YYYY. A single day uses the same start and end. The server binds the session hotel. Call table_columns only when you need columns to write that read.

Then call judge.
verdict is match when the reply states the figure the rows support, mismatch when it states a different figure, and unsaid when the reply does not state the figure the question asked.
recipe_flawed is true only when the stored recipe cannot produce that figure. It is false when no recipe change is needed, including when the reply was simply silent.
flag_sentence is "i doubt the answer is correct" or "i think this is incorrect" when verdict is mismatch or unsaid, and "none" when verdict is match.
ground_figure is the figure the rows support, or empty when the database cannot answer the question.
"""

_REVISE_INSTRUCTIONS = """You correct one hotel recipe. You do not speak to the manager.

You receive the question, the reply, the recipe that was used, and the judgment that the recipe is flawed. Decide the library change.

If that recipe measures the same thing, call publish_version with the same catalog id and a corrected template.
If it measures something else, or no recipe was used, call publish_new with a new catalog id. publish_version is only for the same measure.
If a corrected read shows the stored recipe already returns the figure, call keep_recipe and publish nothing.

The template is one read-only SELECT or WITH. It includes %(hotel_id)s. It reads the period with to_date(%(start)s, 'MM/DD/YYYY') and to_date(%(end)s, 'MM/DD/YYYY'). A single day uses the same start and end. The span is the bound start through end, inclusive. It does not use a fixed 7-day or 30-day window. A literal percent sign is written %%.

Parameters are hotel_id, start, and end. start and end on the tool call are the MM/DD/YYYY period you used to check the read.

Call table_columns when you need columns. Call search_sql to check a read before you publish. outputs are the select aliases. output_grain is the grain of one result row. keyphrases are phrases a manager would use for this measure.
"""


def _client():
    import os
    import httpx
    from openai import OpenAI

    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=httpx.Timeout(None))


def _object(raw):
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or ""))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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


def _create(client, instructions, items, tools):
    return client.responses.create(
        model=MODEL,
        instructions=instructions,
        input=items,
        tools=list(tools),
        reasoning={"effort": EFFORT},
        store=False,
        max_output_tokens=64000,
    )


def _user(text):
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def _search(client, packet):
    last = None
    prompt = [_user(json.dumps(packet, default=str))]
    for tool_name in ("web_search", "web_search_preview"):
        for choice in ({"type": tool_name}, None):
            try:
                kwargs = {
                    "model": MODEL,
                    "instructions": _SEARCH_INSTRUCTIONS,
                    "input": prompt,
                    "tools": [{"type": tool_name}],
                    "reasoning": {"effort": EFFORT},
                    "store": False,
                    "max_output_tokens": 64000,
                }
                if choice:
                    kwargs["tool_choice"] = choice
                return client.responses.create(**kwargs)
            except Exception as exc:
                last = exc
    if last:
        raise last
    raise RuntimeError("web search produced no response")


def _submit_tool():
    return {
        "type": "function",
        "name": "submit_questions",
        "description": "Store the hotel-operations questions drafted from the web search.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
            "required": ["questions"],
            "additionalProperties": False,
        },
    }


def _store_questions(store, arguments):
    questions = list((arguments or {}).get("questions") or [])[:12]
    known = store.known_norms()
    added = 0
    for text in questions:
        clean = " ".join(str(text or "").split())
        if not clean or len(clean) > 500:
            continue
        key = clean.casefold()
        if key in known:
            continue
        known.add(key)
        added += int(store.insert_question(clean, "web") or 0)
    return {"stored": added}


def draft(store, packet):
    """Web search, then draft questions into the unused pool. Returns how many were stored."""
    client = _client()
    try:
        found = _search(client, packet)
    except Exception as exc:
        store.event("web_search_failed", public_text(exc))
        return 0
    items = [_user(json.dumps(packet, default=str))]
    items.extend(list(getattr(found, "output", None) or []))
    items.append(_user("Call submit_questions with the questions drafted from the search."))
    added = 0
    tool = _submit_tool()
    for _round in range(3):
        try:
            response = _create(client, _DRAFT_INSTRUCTIONS, items, [tool])
        except Exception as exc:
            store.event("web_search_failed", public_text(exc))
            return added
        calls = _function_calls(response)
        items.extend(list(getattr(response, "output", None) or []))
        if not calls:
            continue
        for call in calls:
            result = _store_questions(store, call["arguments"])
            added += int(result.get("stored") or 0)
            items.append({
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": json.dumps(result),
            })
        if added:
            return added
    if not added:
        store.event("pool_empty", "web search drafted no new question")
    return added


def _read_tools():
    period = {
        "start": {"type": "string"},
        "end": {"type": "string"},
    }
    return (
        {
            "type": "function",
            "name": "table_columns",
            "description": "Columns of one public table, used to write a read.",
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
                "Include %(hotel_id)s. Pass start and end as MM/DD/YYYY. "
                "A single day uses the same start and end. The server binds hotel_id."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}, **period},
                "required": ["sql", "start", "end"],
                "additionalProperties": False,
            },
        },
    )


def _judge_tool():
    return {
        "type": "function",
        "name": "judge",
        "description": "Record the verdict after a read, or after deciding the database cannot answer.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["match", "mismatch", "unsaid"]},
                "recipe_flawed": {"type": "boolean"},
                "flag_sentence": {
                    "type": "string",
                    "enum": ["i doubt the answer is correct", "i think this is incorrect", "none"],
                },
                "ground_figure": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["verdict", "recipe_flawed", "flag_sentence", "ground_figure", "note"],
            "additionalProperties": False,
        },
    }


def _ordered(calls, finish):
    early = [call for call in calls if call["name"] not in finish]
    late = [call for call in calls if call["name"] in finish]
    return early + late


def _converse(instructions, packet, tools, dispatch, finish, ctx, rounds=_ROUNDS, nudge=""):
    client = _client()
    items = [_user(json.dumps(packet, default=str))]
    for _round in range(rounds):
        response = _create(client, instructions, items, tools)
        calls = _ordered(_function_calls(response), finish)
        items.extend(list(getattr(response, "output", None) or []))
        if not calls:
            if nudge:
                items.append(_user(nudge))
            continue
        for call in calls:
            ctx.setdefault("tools", []).append(str(call["name"] or ""))
            if ctx.get("done") and call["name"] in finish:
                result = {"error": "already decided"}
            else:
                try:
                    result = dispatch(call["name"], call["arguments"])
                except Exception as exc:
                    result = {"error": public_text(exc)}
            items.append({
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": json.dumps(result, default=str),
            })
        if ctx.get("done"):
            return
        if nudge and _round >= 1:
            items.append(_user(nudge))
    return


def judge(question, reply, cards, hotel_id):
    """Sol's verdict. hotel_id is bound into search_sql by this process."""
    from sandbox_manager.config import hotel_clock
    from sandbox_manager.reads import columns, search_sql

    ctx = {"done": None}

    def dispatch(name, arguments):
        arguments = arguments if isinstance(arguments, dict) else {}
        calls = {
            "table_columns": lambda: columns(arguments.get("table")),
            "search_sql": lambda: search_sql(arguments.get("sql"), arguments.get("start"), arguments.get("end"), hotel_id),
            "judge": lambda: _record_judge(arguments, ctx),
        }
        call = calls.get(str(name or ""))
        if call is None:
            return {"error": "unknown tool"}
        return call()

    packet = {
        "clock": hotel_clock(),
        "question": str(question or ""),
        "reply": str(reply or ""),
        "recipes": list(cards or []),
    }
    tools = list(_read_tools()) + [_judge_tool()]
    _converse(
        _JUDGE_INSTRUCTIONS,
        packet,
        tools,
        dispatch,
        {"judge"},
        ctx,
        rounds=6,
        nudge="Call judge with verdict match, mismatch, or unsaid.",
    )
    if ctx.get("done"):
        return ctx["done"]
    used = ",".join(ctx.get("tools") or [])
    return {
        "verdict": "",
        "recipe_flawed": False,
        "flag_sentence": "",
        "ground_figure": "",
        "note": "judge did not report" + ("; tools " + used if used else ""),
    }


def _record_judge(arguments, ctx):
    arguments = arguments if isinstance(arguments, dict) else {}
    verdict = {"match": "match", "mismatch": "mismatch", "unsaid": "unsaid"}.get(arguments.get("verdict"), "")
    flawed = arguments.get("recipe_flawed") is True
    judgment = {
        "verdict": verdict,
        "recipe_flawed": flawed,
        "flag_sentence": str(arguments.get("flag_sentence") or ""),
        "ground_figure": str(arguments.get("ground_figure") or ""),
        "note": str(arguments.get("note") or ""),
    }
    ctx["done"] = judgment
    return judgment


def _revise_tools():
    from booking_system.semantic.recipe import GRAINS

    grains = list(GRAINS)
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
        "start": {"type": "string"},
        "end": {"type": "string"},
    }
    required = list(recipe)
    return recipe, required


def revise(question, reply, cards, judgment, hotel_id, used_ids):
    """Sol decides the library change. The writer publishes only while the library is open."""
    from sandbox_manager.config import hotel_clock
    from sandbox_manager.library import close_library, open_library, publish_new, publish_version
    from sandbox_manager.reads import columns, search_sql

    recipe, required = _revise_tools()
    ctx = {"done": None}
    used = [str(item) for item in used_ids or []]

    def dispatch(name, arguments):
        arguments = arguments if isinstance(arguments, dict) else {}
        calls = {
            "table_columns": lambda: columns(arguments.get("table")),
            "search_sql": lambda: search_sql(arguments.get("sql"), arguments.get("start"), arguments.get("end"), hotel_id),
            "keep_recipe": lambda: _keep(arguments, ctx),
            "publish_version": lambda: _published(publish_version(arguments, hotel_id, used), ctx),
            "publish_new": lambda: _published(publish_new(arguments, hotel_id, used), ctx),
        }
        call = calls.get(str(name or ""))
        if call is None:
            return {"error": "unknown tool"}
        return call()

    tools = list(_read_tools()) + [
        {
            "type": "function",
            "name": "keep_recipe",
            "description": "The stored recipe already returns the figure. Publish nothing.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"catalog_id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["catalog_id", "reason"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "publish_version",
            "description": "Publish a new version of a recipe used on this question when it measures the same thing.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": recipe,
                "required": required,
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "publish_new",
            "description": "Publish version 1 of a new catalog id when the measure is different.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"source_catalog_id": {"type": "string"}, **recipe},
                "required": ["source_catalog_id", *required],
                "additionalProperties": False,
            },
        },
    ]
    packet = {
        "clock": hotel_clock(),
        "question": str(question or ""),
        "reply": str(reply or ""),
        "recipes": list(cards or []),
        "judgment": judgment or {},
        "recipe_ids": used,
    }
    open_library()
    try:
        _converse(
            _REVISE_INSTRUCTIONS,
            packet,
            tools,
            dispatch,
            {"keep_recipe", "publish_version", "publish_new"},
            ctx,
            rounds=6,
            nudge="Call publish_version, publish_new, or keep_recipe.",
        )
    finally:
        close_library()
    return ctx["done"] or {"published": "no", "note": "recipe change was not published"}


def _keep(arguments, ctx):
    outcome = {
        "published": "no",
        "catalog_id": str((arguments or {}).get("catalog_id") or ""),
        "reason": str((arguments or {}).get("reason") or ""),
    }
    ctx["done"] = outcome
    return outcome


def _published(result, ctx):
    if isinstance(result, dict) and result.get("published") == "yes":
        ctx["done"] = result
    return result
