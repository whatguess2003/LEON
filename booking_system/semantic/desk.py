"""Desk turn. One Haiku tool-calling loop. It does not compile a graph or call the catalog or reviewer.
The audit supervisor runs only when the model calls audit_answer. That call stores the last five turns
and queues a developer recipe fix. The developer runs after the audit result is returned."""

import json
import os

from booking_system.llm_utils import HAIKU_MODEL

_SYSTEM = (
    "You are LEON, a hotel operations assistant. "
    "Look up a recipe, then run it. Pass start and end as MM/DD/YYYY. A single day uses the same start and end. "
    "When the user flags, suspects, or claims that an answer is wrong, call audit_answer and tell them its result. "
    "Do not call audit_answer on an ordinary question."
)
_ROUNDS = 6
_RECENT = 20
_AUDIT_TURNS = 5
_RECIPE_LOG = {}

_TOOLS = (
    {
        "name": "get_recipe",
        "description": (
            "Look up a short list of published recipes similar to this query. "
            "Returns at most eight cards: id, problem, grain, columns, and parameter names. "
            "Does not return SQL. Each recipe takes hotel_id, start, and end."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "run_sql",
        "description": (
            "Run one published recipe, or one read-only SQL statement. "
            "Pass recipe_id to load that recipe's SQL template. "
            "parameters carries start and end as MM/DD/YYYY strings. A single day uses the same start and end. "
            "The server sets hotel_id for this session and ignores a hotel_id supplied here. "
            "A statement passed in sql must be a single SELECT or WITH, must include %(hotel_id)s, and must not name another hotel. "
            "Writes are refused. The database times the query out."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "recipe_id": {"type": "string"},
                "sql": {"type": "string"},
                "parameters": {"type": "object", "additionalProperties": True},
            },
        },
    },
    {
        "name": "audit_answer",
        "description": (
            "Call the audit supervisor when the user tells you that an answer is objectively wrong or incorrect, "
            "or otherwise flags, suspects, or claims that your answer is wrong. "
            "Do not call this on an ordinary question. "
            "It reads the recent turns and the recipes those turns used, checks the figure, "
            "and returns whether the user was right, the corrected figure, "
            "and whether the existing recipe was reused or the recipe was flawed. "
            "Tell the user that result. You still speak; the supervisor does not replace you."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
)


def handle_user_message(hotel, manager, message, history, idempotency_key="", dates=None, catalog_followup=False):
    """One desk turn. The model may call get_recipe, run_sql, and audit_answer. audit_answer runs only when the model calls it."""
    turn = _converse(str(hotel or ""), str(manager or ""), str(message or ""), history)
    return turn["reply"], False, None, {
        "shown": [],
        "count": 0,
        "search_ms": 0,
        "retrieval_status": "desk",
        "finished": turn["finished"] is True,
    }


def answer_after_publication(trace_id):
    """The desk answers the original question after a recipe is published. The user is not asked to repeat it."""
    from booking_system.runtime_state import read_turns, write_turns
    from booking_system.semantic.trace import load_trace

    trace = load_trace(trace_id) or {}
    retrieval = trace.get("retrieval") if isinstance(trace.get("retrieval"), dict) else {}
    if retrieval.get("source") != "missing_recipe":
        return {}
    hotel_name = str(retrieval.get("hotel_name") or "").upper()
    manager = str(retrieval.get("manager") or "")
    question = str(trace.get("question") or retrieval.get("question") or "")
    dates = retrieval.get("dates") if isinstance(retrieval.get("dates"), dict) else {"resolutions": []}
    if not hotel_name or not manager or not question:
        return {}
    history = read_turns(hotel_name, manager)
    reply, acted, screen, meta = handle_user_message(
        hotel_name, manager, question, history, dates=dates, catalog_followup=True,
    )
    if reply:
        write_turns(hotel_name, manager, list(history) + [{"sender": "leon", "text": reply}])
    if screen:
        from booking_system.leon_turn import store_screen
        store_screen(hotel_name, screen)
    return {"reply": reply, "acted": acted, "meta": meta}


def get_recipe(query, manager):
    """Up to eight similar published recipes. Cards only. The template loads when one is run."""
    from booking_system.semantic.context_engine import parameter_names, recipe_candidates

    try:
        pages, _search_ms = recipe_candidates(query, manager, 8)
    except Exception as exc:
        return {"recipes": [], "error": str(exc)}
    recipes = []
    for page in pages or []:
        body = page.get("body") or {}
        recipes.append({
            "id": str(page.get("catalog_id") or ""),
            "problem": str(body.get("problem") or ""),
            "grain": str(body.get("output_grain") or ""),
            "columns": [str(name) for name in body.get("outputs") or []],
            "parameters": parameter_names(body),
        })
    return {"recipes": recipes}


def _template(catalog_id, manager):
    """The published SQL for one recipe this user may run."""
    from booking_system.semantic.registry import active_pages, visible_to

    page = (active_pages([catalog_id]) or {}).get(str(catalog_id or ""))
    if not page or not visible_to(page, manager):
        return ""
    return str((((page.get("body") or {}).get("solution") or {}).get("sql_template")) or "").strip()


def run_sql(sql, parameters, hotel_id, recipe_id="", manager=""):
    """One hotel-scoped read. A recipe id loads that template. The session hotel_id is bound here."""
    from booking_system.semantic.query import _statements, execute_reads, read_rejection

    chosen = str(recipe_id or "").strip()
    statement = _template(chosen, manager) if chosen else str(sql or "").strip()
    if chosen and not statement:
        return {"rows": [], "error": "recipe not available"}
    if len(_statements(statement)) != 1:
        return {"rows": [], "error": "A read is one statement."}
    reason = read_rejection([statement])
    if reason:
        return {"rows": [], "error": reason}
    bound = {}
    if isinstance(parameters, dict):
        bound.update(parameters)
    bound["hotel_id"] = str(hotel_id or "")
    result = execute_reads([statement], bound)
    return {"rows": result.get("rows") or [], "error": result.get("error") or ""}


def _recent(history, message):
    """Recent chat turns, then this message. Roles follow the stored sender."""
    rows = []
    for entry in list(history or [])[-_RECENT:]:
        if isinstance(entry, dict):
            sender = str(entry.get("sender") or entry.get("from") or "")
            text = str(entry.get("text") or "")
        else:
            sender, text = "", str(entry or "")
        if not text.strip():
            continue
        role = "assistant" if sender.lower() in ("leon", "assistant") else "user"
        if rows and rows[-1]["role"] == role:
            rows[-1]["content"] += "\n" + text
        else:
            rows.append({"role": role, "content": text})
    current = str(message or "")
    if rows and rows[-1]["role"] == "user":
        rows[-1]["content"] += "\n" + current
    else:
        rows.append({"role": "user", "content": current})
    if rows[0]["role"] != "user":
        rows.insert(0, {"role": "user", "content": "."})
    return rows


def _message_turns(history):
    """Stored chat turns in order. A leon sender is the assistant."""
    rows = []
    for entry in list(history or []):
        if isinstance(entry, dict):
            sender = str(entry.get("sender") or entry.get("from") or "")
            text = str(entry.get("text") or "")
        else:
            sender, text = "", str(entry or "")
        role = "assistant" if sender.lower() in ("leon", "assistant") else "user"
        rows.append({"role": role, "text": text})
    return rows


def last_turns(history, message, limit=_AUDIT_TURNS):
    """The last conversation turns, including the message on this turn."""
    rows = _message_turns(history)
    rows.append({"role": "user", "text": str(message or "")})
    return rows[-int(limit or _AUDIT_TURNS):]


def _ids(values):
    found = []
    for item in values or []:
        text = str(item or "").strip()
        if text and text not in found:
            found.append(text)
    return found


def _log_key(hotel, manager):
    return (str(hotel or "").upper(), str(manager or ""))


def _assistant_count(history):
    return len([row for row in _message_turns(history) if row["role"] == "assistant"])


def _load_log(hotel, manager, history):
    """Recipe ids recorded for previous desk replies. Ids only. A shorter saved copy does not drop a newer in-process record."""
    key = _log_key(hotel, manager)
    memory = [list(item) for item in list(_RECIPE_LOG.get(key) or [])]
    stored = memory
    try:
        from booking_system.runtime_state import read_session
        raw = read_session(hotel, manager).get("recipe_turns")
        if isinstance(raw, list):
            parsed = [_ids(item) if isinstance(item, list) else [] for item in raw]
            if len(parsed) >= len(memory):
                stored = parsed
    except Exception:
        stored = memory
    count = _assistant_count(history)
    trimmed = [list(item) for item in (stored[-count:] if count else [])]
    _RECIPE_LOG[key] = trimmed
    return trimmed


def _store_log(hotel, manager, log):
    key = _log_key(hotel, manager)
    body = [list(item) for item in list(log or [])[-20:]]
    _RECIPE_LOG[key] = body
    try:
        from booking_system.runtime_state import read_session, write_session
        state = read_session(hotel, manager)
        state["recipe_turns"] = body
        write_session(hotel, manager, state)
    except Exception:
        return


def _remember_turn(hotel, manager, history, pending):
    """Keep this turn's recipe ids with the reply the desk is about to store."""
    try:
        log = _load_log(hotel, manager, history)
    except Exception:
        return
    log.append(_ids(pending))
    _store_log(hotel, manager, log)


def recipe_ids_for(history, message, log, pending, limit=_AUDIT_TURNS):
    """Recipe ids used on the last conversation turns, then any recipe this turn already ran."""
    past = _message_turns(history)
    positions = [index for index, row in enumerate(past) if row["role"] == "assistant"]
    usable = list(log or [])[-len(positions):] if positions else []
    start = len(positions) - len(usable)
    paired = {}
    for offset, recipes in enumerate(usable):
        paired[positions[start + offset]] = list(recipes or [])
    window_start = max(0, len(past) + 1 - int(limit or _AUDIT_TURNS))
    ids = []
    for position, recipes in paired.items():
        if position >= window_start:
            ids.extend(recipes)
    ids.extend(pending or [])
    return _ids(ids)


def _run_and_note(arguments, ctx):
    """Run the desk read and remember a recipe id the model passed."""
    recipe_id = str(arguments.get("recipe_id") or "").strip()
    if recipe_id:
        ctx["pending"].append(recipe_id)
    return run_sql(
        arguments.get("sql"),
        arguments.get("parameters"),
        ctx["hotel_id"],
        arguments.get("recipe_id"),
        ctx["manager"],
    )


def _audit(ctx):
    """Audit this turn, store the last five turns, and queue a developer fix. Publication waits until after this returns."""
    from booking_system.semantic.audit_agent import supervise
    from booking_system.semantic.developer_agent import start_fix

    turns = last_turns(ctx["history"], ctx["message"])
    recipe_ids = recipe_ids_for(ctx["history"], ctx["message"], ctx["log"], ctx["pending"])
    try:
        verdict = supervise(ctx["hotel_id"], ctx["manager"], turns, recipe_ids)
    except Exception:
        verdict = {
            "user_was_right": None,
            "corrected_figure": "",
            "recipe_outcome": "",
            "say": "The audit could not be completed.",
            "rows": [],
            "error": "audit unavailable",
        }
    try:
        start_fix(ctx["hotel_id"], ctx["manager"], turns, recipe_ids, verdict)
    except Exception:
        return verdict
    return verdict


def _dispatch(name, arguments, ctx):
    arguments = arguments if isinstance(arguments, dict) else {}
    calls = {
        "get_recipe": lambda: get_recipe(arguments.get("query"), ctx["manager"]),
        "run_sql": lambda: _run_and_note(arguments, ctx),
        "audit_answer": lambda: _audit(ctx),
    }
    call = calls.get(str(name or ""))
    if call is None:
        return {"error": "unknown tool"}
    try:
        return call()
    except Exception as exc:
        return {"error": str(exc)}


def _text(content):
    parts = []
    for block in content or []:
        kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
        if kind == "text":
            parts.append(block.get("text") if isinstance(block, dict) else getattr(block, "text", ""))
    return "\n".join(part for part in parts if part).strip()


def _public(block):
    """Replay shape for one response block. Thinking stays intact; empty text is omitted."""
    kind = getattr(block, "type", "")
    if kind == "thinking":
        replay = {"type": "thinking"}
        if hasattr(block, "thinking"):
            replay["thinking"] = block.thinking
        if hasattr(block, "signature"):
            replay["signature"] = block.signature
        return replay
    if kind == "redacted_thinking":
        replay = {"type": "redacted_thinking"}
        if hasattr(block, "data"):
            replay["data"] = block.data
        return replay
    if kind == "text":
        text = getattr(block, "text", "") or ""
        if not text:
            return None
        return {"type": "text", "text": text}
    if kind == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input if isinstance(block.input, dict) else {},
        }
    return None


_THINKING = {"thinking", "redacted_thinking"}


def _create(client, messages, tools):
    """One Haiku turn. The tool rounds keep the same tools list, which thinking signatures are bound to."""
    kwargs = {
        "model": HAIKU_MODEL,
        "max_tokens": 4096,
        "system": _SYSTEM,
        "messages": messages,
    }
    if tools is not None:
        kwargs["tools"] = list(tools)
    return client.messages.create(**kwargs)


def _closing_messages(messages):
    """Thinking signatures are bound to the tool list. The closing call sends neither, so those blocks cannot be replayed."""
    cleaned = []
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            cleaned.append(message)
            continue
        kept = [block for block in content if block.get("type") not in _THINKING]
        if kept:
            cleaned.append({"role": message.get("role"), "content": kept})
    return cleaned


def _close(client, messages):
    """One sentence after the tool rounds. Tools are withheld and thinking is off, so the reply is a text block."""
    return client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=4096,
        system=_SYSTEM,
        messages=_closing_messages(messages),
        thinking={"type": "disabled"},
    )


def _blocks(response):
    return [replay for replay in (_public(block) for block in response.content) if replay]


def _converse(hotel, manager, message, history):
    import anthropic
    from booking_system.semantic.hotel import session_hotel_id

    hotel_id = session_hotel_id(str(hotel or "").upper())
    messages = _recent(history, message)
    log = _load_log(hotel, manager, history)
    ctx = {
        "hotel": hotel,
        "hotel_id": hotel_id,
        "manager": manager,
        "history": history,
        "message": message,
        "log": log,
        "pending": [],
    }
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=90)
    spoken = ""
    finished = False
    for _round in range(_ROUNDS):
        response = _create(client, messages, _TOOLS)
        blocks = _blocks(response)
        calls = [block for block in blocks if block["type"] == "tool_use"]
        if not calls:
            spoken = _text(blocks) or "I could not answer that from the hotel data."
            finished = True
            break
        messages.append({"role": "assistant", "content": blocks})
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": json.dumps(_dispatch(block["name"], block["input"], ctx), default=str),
                }
                for block in calls
            ],
        })
    if not finished:
        try:
            response = _close(client, messages)
        except Exception:
            spoken = ""
        else:
            spoken = _text(_blocks(response))
            if spoken:
                finished = True
    _remember_turn(hotel, manager, history, ctx["pending"])
    return {"reply": spoken, "finished": finished}
