"""When a desk message runs, and which agent receives it."""

import json
import uuid


def handle_user_message(hotel, manager, message, history, idempotency_key=""):
    """Record the message, ask the desk for an intent, then read or act."""
    from booking_system.semantic.context_engine import ContextEngine
    from booking_system.semantic.registry import ensure_workflow_tables, save_event

    ensure_workflow_tables()
    hotel_name = str(hotel or "").upper()
    key = str(idempotency_key or "") or uuid.uuid4().hex
    event = save_event(
        hotel_name,
        "user_message_received",
        key,
        {"manager": str(manager or ""), "question": str(message or "")},
    )
    history_text = _history_text(history)
    desk_package = ContextEngine().build({
        "agent": "desk",
        "purpose": "route",
        "scope": {"hotel_name": hotel_name},
        "input": {"question": str(message or ""), "history": history_text},
        "budget": {"max_tokens": 3000},
    })
    if desk_package.get("context_overflow"):
        return _plain("The context package exceeded the budget.", desk_package)
    from concurrent.futures import ThreadPoolExecutor
    from booking_system.runtime_state import read_session

    context = _definition_context(manager)
    session = read_session(hotel_name, manager)
    held = session.get("awaiting") or {}
    pool = ThreadPoolExecutor(max_workers=1)
    prepared = pool.submit(_prepare_query, message, hotel_name, context)
    pool.shutdown(wait=False)
    routed = _intent(message, history_text, held)
    if held and routed["intent"] == "answers_clarification":
        return _take_clarification(hotel_name, manager, event["event_id"], session, routed["answer"])
    if held and routed["intent"] == "skips_clarification":
        return _skip_clarification(hotel_name, manager, event["event_id"], session)
    if held:
        _store_session(hotel_name, manager, session, {})
    if routed["intent"] == "desk":
        from booking_system.leon_turn import desk_action_turn
        reply, acted, screen, meta = desk_action_turn(hotel, manager, message, history)
        meta["search_ms"] = desk_package.get("search_ms")
        return reply, acted, screen, meta
    if routed["intent"] == "personal_change":
        return _personal_change(hotel_name, manager, message)
    reply, acted, screen, meta = _query_turn(hotel_name, manager, message, event["event_id"], prepared.result(), context)
    reply = _offer_report(hotel_name, manager, reply, meta)
    return _follow_up(hotel_name, manager, reply, meta), acted, screen, meta


def _definition_context(manager):
    """The manager's definitions and phrases, their personal reports, recent multi-figure questions, and published recipes."""
    from booking_system.semantic.definitions import known_keys, manager_definitions, skipped_keys
    from booking_system.semantic.personal import declined_reports, personal_cards, published_recipes, recent_reports

    return {
        "manager": str(manager or "").upper(),
        "definitions": manager_definitions(manager),
        "keys": known_keys(manager),
        "declined": skipped_keys(manager),
        "personal": personal_cards(manager),
        "recent": recent_reports(manager),
        "declined_reports": [{"name": item.get("name"), "question": item.get("question")} for item in declined_reports(manager)],
        "published": published_recipes(manager),
    }


def _store_session(hotel_name, manager, session, held, asked_key="", **extra):
    from booking_system.runtime_state import write_session

    state = dict(session or {})
    asked = [str(item) for item in state.get("asked") or []]
    if asked_key and asked_key not in asked:
        asked.append(asked_key)
    state.update(extra)
    state["awaiting"] = dict(held or {})
    state["asked"] = asked
    write_session(hotel_name, manager, state)


def _meta(status):
    return {"shown": [], "count": 0, "search_ms": 0, "retrieval_status": status}


def _personal_turn(hotel_name, manager, message, spec, context):
    from booking_system.semantic.personal import find_personal, run_personal

    recipe = find_personal(manager, spec["personal_key"])
    if not recipe:
        return "That saved report was not found.", False, None, _meta("personal_missing")
    reply, info = run_personal(hotel_name, manager, recipe, message, context["definitions"])
    meta = _meta("personal")
    meta.update({"route": "personal", "personal": info})
    return reply, False, None, meta


def _learn_report(hotel_name, manager, name, request, question, saved_line):
    """Map the figures onto published recipes and save the personal report. A figure whose meaning is not defined is asked first;
    otherwise the report runs now."""
    from booking_system.semantic.personal import describe, learn, run_personal, save_personal

    items, seen = learn(manager, name, request)
    saved = save_personal(manager, name, items, seen)
    if saved.get("error"):
        return "That report was not saved. " + saved["error"], False, None, _meta("personal_failed")
    asked = _ask_figure_definition(hotel_name, manager, saved["recipe"], question, saved_line)
    if asked:
        return asked
    reply, info = run_personal(hotel_name, manager, saved["recipe"], question, _definition_context(manager)["definitions"])
    meta = _meta("personal")
    meta.update({"route": "personal", "personal": info})
    return saved_line + " " + describe(saved["recipe"]) + ". " + reply, False, None, meta


def _ask_figure_definition(hotel_name, manager, recipe, question, saved_line):
    """The first figure of a saved report whose meaning the manager has not defined is asked; the question waits on it."""
    from booking_system.runtime_state import read_session
    from booking_system.semantic.personal import describe, needed_definitions

    needed = needed_definitions(manager, recipe)
    if not needed:
        return None
    first = needed[0]
    _store_session(hotel_name, manager, read_session(hotel_name, manager), {
        "question": first["question"],
        "key": first["key"],
        "kind": "definition",
        "original_question": str(question or ""),
    }, first["key"])
    reply = saved_line + " " + describe(recipe) + ". Before I learn " + first["label"].lower() + ": " + first["question"]
    return reply, False, None, _meta("clarification")


def _offer_report(hotel_name, manager, reply, meta):
    """A repeated multi-figure question is offered as a saved report, at most once per chat."""
    from booking_system.runtime_state import read_session
    from booking_system.semantic.personal import remember_report

    spec = meta.get("spec") or {}
    if meta.get("route") not in ("recipe", "live") or len(spec.get("figures") or []) < 2:
        return reply
    session = read_session(hotel_name, manager)
    offer = ""
    if spec["repeats_report"] and spec["report_name"] and not session.get("offered_report") and not session.get("awaiting"):
        offer = "Want me to remember this as your " + spec["report_name"] + "?"
        _store_session(hotel_name, manager, session, {
            "question": offer,
            "key": "",
            "kind": "save_report",
            "report_name": spec["report_name"],
            "original_question": spec["original_question"],
        }, offered_report=True)
    remember_report(manager, spec["original_question"], spec["figures"])
    return (str(reply or "").rstrip() + " " + offer).strip()


def _personal_change(hotel_name, manager, message, asked="", chosen=""):
    """Read the saved reports first, then create, change, or delete one. A near name is shown back and clarified."""
    from booking_system.llm_utils import haiku_text
    from booking_system.runtime_state import read_session
    from booking_system.semantic.personal import (
        delete_personal, describe, learn, personal_recipes, save_personal,
    )

    saved = personal_recipes(manager)
    keys = [item["key"] for item in saved]
    labels = [item["key"] + " / " + str(figure.get("label") or "") for item in saved for figure in item.get("items") or []]
    schema = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["create", "add_figures", "drop_figures", "delete", "list"]},
            "target": {"type": "string", "enum": [""] + keys},
            "match": {"type": "string", "enum": ["exact", "similar", "none"]},
            "name": {"type": "string"},
            "figures": {"type": "string"},
            "drop": {"type": "array", "items": {"type": "string", "enum": labels or [""]}},
        },
        "required": ["operation", "target", "match", "name", "figures", "drop"],
        "additionalProperties": False,
    }
    parsed = json.loads(haiku_text(
        (
            "The manager wants to change their saved personal reports. saved lists them. "
            "operation is what they ask: create a new report, add_figures to one, drop_figures from one, delete one, or list them. "
            "name is the report name they used. target is the saved report they mean. "
            "match is exact when the name they used is that saved report, similar when it only resembles one, and none when nothing saved resembles it. "
            "When Leon already asked which saved report they meant and chosen gives their reply, the report that reply points to is the target with match exact. "
            "figures is the figures to create or add, in their words with the day they gave. drop lists the saved figures to remove."
        ),
        json.dumps({
            "sentence": str(message or ""),
            "leon_asked": str(asked or ""),
            "chosen": str(chosen or ""),
            "saved": [{"key": item["key"], "describe": describe(item)} for item in saved],
        }),
        schema=schema,
        max_tokens=800,
        timeout=90,
    ))
    operation = parsed["operation"]
    listing = "; ".join(describe(item) for item in saved) or "none"
    if operation == "list":
        return "Your saved reports: " + listing + ".", False, None, _meta("personal_list")
    if parsed["match"] == "similar":
        question = "I have these saved: " + listing + ". Which one did you mean?"
        _store_session(hotel_name, manager, read_session(hotel_name, manager), {
            "question": question, "key": "", "kind": "report_target", "original_question": str(message or ""),
        })
        return question, False, None, _meta("clarification")
    target = [item for item in saved if item["key"] == parsed["target"]] if parsed["match"] == "exact" else []
    if operation == "create":
        if target:
            return "You already have " + describe(target[0]) + ". Say add or drop to change it.", False, None, _meta("personal_exists")
        items, seen = learn(manager, parsed["name"], parsed["figures"])
        created = save_personal(manager, parsed["name"], items, seen)
        if created.get("error"):
            return "That report was not saved. " + created["error"], False, None, _meta("personal_failed")
        asked = _ask_figure_definition(hotel_name, manager, created["recipe"], "", "Saved your")
        return asked or ("Saved your " + describe(created["recipe"]) + ".", False, None, _meta("personal_saved"))
    if not target:
        return "You have no report called " + parsed["name"] + ". Saved: " + listing + ".", False, None, _meta("personal_missing")
    recipe = target[0]
    if operation == "delete":
        delete_personal(manager, recipe["key"])
        return "Deleted your " + str(recipe.get("name") or "") + ".", False, None, _meta("personal_deleted")
    items = list(recipe.get("items") or [])
    if operation == "add_figures":
        present = {item.get("label") for item in items}
        mapped, _seen = learn(manager, recipe.get("name"), parsed["figures"])
        items = items + [item for item in mapped if item["label"] not in present]
    if operation == "drop_figures":
        dropped = {label.split(" / ", 1)[1] for label in parsed["drop"] if label.startswith(recipe["key"] + " / ")}
        items = [item for item in items if item.get("label") not in dropped]
        if not items:
            return "That would leave your " + str(recipe.get("name") or "") + " empty. Say delete to remove it.", False, None, _meta("personal_empty")
    updated = save_personal(manager, recipe.get("name"), items, recipe.get("published_seen") or "")
    asked = _ask_figure_definition(hotel_name, manager, updated["recipe"], "", "Updated your")
    return asked or ("Updated your " + describe(updated["recipe"]) + ".", False, None, _meta("personal_updated"))


def _take_clarification(hotel_name, manager, event_id, session, answer):
    """Keep a definition on the manager profile, then answer the question that was waiting on it."""
    from booking_system.semantic.definitions import save_definition
    from booking_system.semantic.trace import enqueue_resume

    held = session.get("awaiting") or {}
    _store_session(hotel_name, manager, session, {})
    question = str(held.get("original_question") or "")
    if held.get("kind") == "report":
        request = question + ". Figures: " + str(answer or "")
        return _learn_report(hotel_name, manager, held.get("report_name"), request, question, "Saved as your")
    if held.get("kind") == "save_report":
        return _learn_report(hotel_name, manager, held.get("report_name"), question, question, "Saved as your")
    if held.get("kind") == "report_target":
        return _personal_change(hotel_name, manager, question, held.get("question"), answer)
    if held.get("kind") == "definition":
        saved = save_definition(manager, held.get("key"), answer, "desk")
        if saved.get("error"):
            meta = {"shown": [], "count": 0, "search_ms": 0, "retrieval_status": "definition_failed"}
            return "That definition was not saved. " + str(saved["error"]), False, None, meta
        if saved.get("ready_trace_ids"):
            enqueue_resume(saved["ready_trace_ids"])
    elif question:
        question = question + "\n" + str(held.get("question") or "") + " " + str(answer or "")
    if not question:
        reply = "Got it. I will use that from now on."
        meta = {"shown": [], "count": 0, "search_ms": 0, "retrieval_status": "definition_saved"}
        return _follow_up(hotel_name, manager, reply, meta), False, None, meta
    context = _definition_context(manager)
    prepared = _prepare_query(question, hotel_name, context)
    reply, acted, screen, meta = _query_turn(hotel_name, manager, question, event_id, prepared, context)
    return _follow_up(hotel_name, manager, reply, meta), acted, screen, meta


def _skip_clarification(hotel_name, manager, event_id, session):
    """The manager declined. A declined definition is not asked again; the waiting question is answered with every reading."""
    from booking_system.semantic.definitions import skip_definition
    from booking_system.semantic.personal import decline_report

    held = session.get("awaiting") or {}
    _store_session(hotel_name, manager, session, {})
    meta = {"shown": [], "count": 0, "search_ms": 0, "retrieval_status": "clarification_skipped"}
    if held.get("kind") == "save_report":
        decline_report(manager, held.get("report_name"), held.get("original_question"))
        return "Okay, I will not offer that one again.", False, None, meta
    if held.get("kind") != "definition":
        return "Okay, I will leave that one.", False, None, meta
    skip_definition(manager, held.get("key"))
    question = str(held.get("original_question") or "")
    if not question:
        return "No problem, I will not ask about that again.", False, None, meta
    context = _definition_context(manager)
    prepared = _prepare_query(question, hotel_name, context)
    return _query_turn(hotel_name, manager, question, event_id, prepared, context)


def _follow_up(hotel_name, manager, reply, meta):
    """Ask one open definition after an answer, at most once per chat. Declined definitions are not asked."""
    from booking_system.runtime_state import read_session
    from booking_system.semantic.definitions import open_definitions

    session = read_session(hotel_name, manager)
    if meta.get("retrieval_status") == "clarification" or session.get("awaiting"):
        return reply
    asked = set(session.get("asked") or [])
    waiting = [item for item in open_definitions(manager) if not item["skipped"] and item["key"] not in asked]
    if not waiting:
        return reply
    first = waiting[0]
    _store_session(hotel_name, manager, session, {
        "question": first["question"],
        "key": first["key"],
        "kind": "definition",
        "original_question": "",
    }, first["key"])
    return str(reply or "").rstrip() + " One more thing: " + first["question"] + " You can say skip."


def _prepare_query(message, hotel_name, context):
    """Published candidates, then one spec call that picks the route: a recipe, a clarification, or the live agent."""
    from booking_system.semantic.context_engine import recipe_candidates
    from booking_system.semantic.gate import known_capabilities
    from booking_system.semantic.recipe import specify_problem

    try:
        candidates, search_ms = recipe_candidates(message, context["manager"])
    except Exception as exc:
        return {"error": "retrieval error " + str(exc)}
    try:
        spec = specify_problem(message, hotel_name, candidates, dict(context, capabilities=known_capabilities(hotel_name)))
    except Exception as exc:
        return {"error": "The problem specification could not be written. " + str(exc)}
    return {"spec": spec, "candidates": candidates, "search_ms": search_ms}


def _ask(hotel_name, manager, message, spec):
    """Hold the clarification for this chat. A new phrase becomes an open definition on the profile first."""
    from booking_system.runtime_state import read_session
    from booking_system.semantic.definitions import open_request

    key = ""
    if spec["clarification_kind"] == "definition":
        key = spec["definition_key"]
        if spec["new_definition"]:
            opened = open_request(manager, spec["new_definition"]["key"], spec["new_definition"]["label"], spec["clarification"])
            if opened.get("error"):
                return _plain("This question needs a definition that could not be recorded. " + opened["error"], {"search_ms": 0, "retrieval": {"status": "clarification_failed"}})
            key = opened["key"]
    session = read_session(hotel_name, manager)
    _store_session(hotel_name, manager, session, {
        "question": spec["clarification"],
        "key": key,
        "kind": spec["clarification_kind"],
        "report_name": spec["report_name"],
        "original_question": str(message or ""),
    }, key)
    return _plain(spec["clarification"], {"search_ms": 0, "retrieval": {"status": "clarification"}})


def _query_turn(hotel_name, manager, message, event_id, prepared, context):
    from booking_system.leon_turn import present_read
    from booking_system.semantic.context_engine import ContextEngine
    from booking_system.semantic.executor import run_composition, run_read, run_steps
    from booking_system.semantic.recipe import answer_check
    from booking_system.semantic.registry import (
        finish_task,
        save_composition_plan,
        save_context_package,
        save_evidence,
        save_execution_run,
        save_problem_spec,
        save_task,
    )
    from booking_system.semantic.sql_agent import answer_question

    if prepared.get("error"):
        return _plain(prepared["error"], {"search_ms": 0, "retrieval": {"status": "retrieval_error"}})
    spec = prepared["spec"]
    save_problem_spec(event_id, hotel_name, spec)
    if spec["route"] == "personal":
        return _personal_turn(hotel_name, manager, message, spec, context)
    if spec["route"] == "clarify":
        if not spec["clarification"] or not spec["clarification_kind"]:
            return _plain("This question needs a clarification that could not be written.", {"search_ms": 0, "retrieval": {"status": "clarification_failed"}})
        return _ask(hotel_name, manager, message, spec)
    if spec["route"] == "decline":
        if not spec["clarification"]:
            return _plain("This question was declined but the reply could not be written.", {"search_ms": 0, "retrieval": {"status": "decline_failed"}})
        return _plain(spec["clarification"], {"search_ms": 0, "retrieval": {"status": "declined"}})
    if spec["route"] == "live":
        spec["recipe_ids"] = []
    definitions = context["definitions"]
    if spec.get("unresolved"):
        return _plain("This request still needs " + ", ".join(spec["unresolved"]) + ".", {"search_ms": 0, "retrieval": {"status": "unresolved"}})
    package = ContextEngine().build({
        "agent": "query",
        "purpose": "answer_database_question",
        "scope": {"hotel_name": hotel_name, "manager": str(manager or "")},
        "input": {
            "question": str(message or ""),
            "resolved_dates": [],
            "spec": spec,
            "candidates": prepared.get("candidates") or [],
            "search_ms": prepared.get("search_ms") or 0,
        },
        "budget": {"max_tokens": 3000},
        "policy": "recipe_resolver_v1",
    })
    task = save_task(event_id, hotel_name, "query", "answer_database_question", "running")
    stored = save_context_package(task["task_id"], package)
    package["context_package_id"] = stored["package_id"]
    package["manager"] = str(manager or "").upper()
    status = str((package.get("retrieval") or {}).get("status") or "")
    execution = str((package.get("retrieval") or {}).get("execution") or "agent")
    if status == "access_denied":
        finish_task(task["task_id"], "access_denied")
        return _plain("This desk has no hotel scope.", package)
    if status == "retrieval_error":
        finish_task(task["task_id"], "retrieval_error")
        return _plain(str(package.get("error") or "retrieval error"), package)
    if status == "unsupported_composition":
        _queue_catalog(
            hotel_name,
            message,
            package,
            {"rows": [], "reply": "Those recipes cannot be combined.", "error": "unsupported_composition", "attempts": [], "elapsed_ms": 0},
            "composition_gap",
            spec,
        )
        finish_task(task["task_id"], "unsupported_composition")
        return _plain("Those recipes cannot be combined.", package)
    if execution == "template":
        if package.get("steps") and not package.get("statements"):
            result = run_steps(package.get("steps") or [], {}, hotel_name, [], task["task_id"])
        else:
            result = run_read(
                package.get("statements") or [],
                package.get("parameters") or {"hotel_name": hotel_name},
                task["task_id"],
                hotel_name,
                "recipe",
                package.get("row_filter"),
            )
        found = _checked(result, package.get("outputs") or [])
        save_execution_run(
            task["task_id"], hotel_name, (package.get("page_ids") or [""])[0],
            ((package.get("referenced_versions") or [{}])[0]).get("version"),
            package.get("parameters") or {}, len(found.get("rows") or []),
            found.get("error") or "", result.get("elapsed_ms") or 0,
        )
    elif execution == "composition":
        result = run_composition(package.get("composition") or [], task["task_id"], hotel_name)
        save_composition_plan(task["task_id"], hotel_name, {"recipes": package.get("page_ids") or [], "groups": result.get("groups") or []})
        found = _checked(result, [])
    elif package.get("context_overflow"):
        found = {
            "reply": "The context package exceeded the budget.",
            "rows": [],
            "error": "context_overflow",
            "statements": [],
            "stall_reason": "context_overflow",
            "answer_check": {"status": "unavailable", "error": "context_overflow"},
        }
    else:
        found = answer_question(hotel_name, message, [], None, None, definitions)
        found["answer_check"] = answer_check(found.get("rows") or [], [], found.get("error") or found.get("stall_reason") or "")
        save_evidence(
            task["task_id"],
            hotel_name,
            "sql_read",
            {
                "statements": list(found.get("statements") or []),
                "rows": list(found.get("rows") or []),
                "error": str(found.get("error") or ""),
                "stall_reason": str(found.get("stall_reason") or ""),
            },
            "query_agent",
        )
    succeeded = bool(found.get("statements")) and not found.get("error") and not found.get("stall_reason")
    check = found.get("answer_check") or {}
    if check.get("status") in ("grain_mismatch", "unavailable"):
        found["rows"] = []
        found["error"] = check.get("error") or check.get("status")
    if check.get("status") == "grain_mismatch":
        found["trace_id"] = _queue_catalog(hotel_name, message, package, found, "semantic_mismatch", spec)
    elif execution == "template" and found.get("error"):
        found["trace_id"] = _queue_catalog(hotel_name, message, package, found, "catalog_validation_failed", spec)
    elif status == "missing_recipe" and succeeded:
        found["trace_id"] = _queue_catalog(hotel_name, message, package, found, "knowledge_gap_detected", spec)
    elif status == "missing_recipe":
        found["trace_id"] = _queue_catalog(hotel_name, message, package, found, "live_answer_failed", spec)
    elif status == "simple_composition_possible" and succeeded:
        found["trace_id"] = _queue_catalog(hotel_name, message, package, found, "composition_succeeded_without_catalog", spec)
    finish_task(task["task_id"], "completed" if succeeded else status or "completed")
    reply, acted, screen, meta = present_read(hotel_name, found, package.get("page_ids") or [], message, definitions)
    meta["search_ms"] = package.get("search_ms")
    meta["retrieval_status"] = status
    meta["route"] = spec["route"]
    meta["gate"] = package.get("gate") or ""
    meta["spec"] = spec
    meta["recipe_ids"] = list(package.get("page_ids") or []) if execution == "template" else []
    meta["output_grain"] = package.get("output_grain") or spec.get("output_grain")
    return reply, acted, screen, meta


def _checked(result, outputs):
    from booking_system.semantic.recipe import answer_check
    from booking_system.semantic.sql_agent import _rows_reply

    rows = result.get("rows") or []
    error = result.get("error") or ""
    return {
        "reply": _rows_reply(rows),
        "rows": rows,
        "error": error,
        "statements": result.get("statements") or [],
        "stall_reason": "",
        "answer_check": answer_check(rows, outputs, error),
    }


def _queue_catalog(hotel_name, message, package, found, reason, spec):
    """A gap reaches the catalog only through the gate. Returns the trace id, or None when nothing was queued."""
    from booking_system.semantic.context_engine import capability_fingerprint
    from booking_system.semantic.gate import admit, capability_key
    from booking_system.semantic.registry import open_investigation
    from booking_system.semantic.trace import enqueue_catalog_agent, save_trace

    fingerprint = capability_key(spec["capability"]) if (spec or {}).get("capability") else capability_fingerprint(
        reason,
        (spec or {}).get("output_grain"),
        (spec or {}).get("entity"),
        [item.get("fact") for item in (spec or {}).get("constraints") or [] if isinstance(item, dict)],
    )
    manager = str(package.get("manager") or "")
    admitted, fingerprint = admit(
        hotel_name, manager, fingerprint, str((spec or {}).get("learn") or ""), message,
        free_only=reason == "composition_succeeded_without_catalog",
    )
    package["gate"] = admitted
    if admitted not in ("attach", "queue"):
        return None
    trace_id = save_trace(
        hotel_name,
        message,
        package.get("page_ids") or [],
        list(package.get("referenced_versions") or []),
        list(found.get("attempts") or []),
        reason,
        len(found.get("rows") or []),
        int(found.get("elapsed_ms") or 0),
        found.get("reply") or "",
        found.get("error") or "",
        {
            "status": (package.get("retrieval") or {}).get("status"),
            "shortlist": package.get("shortlist") or [],
            "gap_fingerprint": fingerprint,
            "manager": manager,
        },
    )
    opened = open_investigation(hotel_name, fingerprint, trace_id, manager)
    if not opened.get("attached"):
        enqueue_catalog_agent(trace_id)
    return trace_id


def _intent(message, history_text, held=None):
    schema = {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": ["query_data", "desk", "personal_change", "answers_clarification", "skips_clarification"]},
            "answer": {"type": "string"},
        },
        "required": ["intent", "answer"],
        "additionalProperties": False,
    }
    asked = str((held or {}).get("question") or "")
    kind = str((held or {}).get("kind") or "")
    from booking_system.llm_utils import haiku_text
    from booking_system.semantic.definitions import STYLE
    parsed = json.loads(haiku_text(
        (
            "intent is query_data when the sentence asks for a fact from the hotel. "
            "intent is desk when the sentence is a greeting or asks to change a booking, "
            "take a payment, send a message, or assign a task. "
            "intent is personal_change when the sentence asks to create, change, add to, drop from, delete, or list "
            "the manager's own saved report, such as add a figure to my report or delete my report. "
            "Asking to see or share a report is query_data, not personal_change. "
            "Leon asked the Awaiting question. intent is answers_clarification when the sentence answers that question about that phrase. "
            "A sentence that defines a different phrase, or asks a new question, does not answer it. "
            "intent is skips_clarification when the sentence declines to answer it or asks Leon to skip it. "
            "When Awaiting kind is definition, answer is the definition. " + STYLE + " "
            "When Awaiting kind is turn, answer restates the missing detail the person gave as one complete statement. "
            "When Awaiting kind is report, answer lists the figures the person wants in the report, in their words. "
            "When Awaiting kind is save_report, a yes is answers_clarification with answer yes, and a no is skips_clarification. "
            "When Awaiting kind is report_target, answer is the saved report they chose. "
            "Leave answer empty for any other intent."
        ),
        "Awaiting (" + (kind or "none") + ")\n" + (asked or "None") + "\nHistory\n" + (history_text or "None") + "\nSentence\n" + str(message or ""),
        schema=schema,
        max_tokens=800,
        timeout=90,
    ))
    return {"intent": str(parsed.get("intent") or ""), "answer": str(parsed.get("answer") or "")}


def _history_text(history):
    lines = []
    for entry in list(history or [])[-6:]:
        if isinstance(entry, dict):
            lines.append(str(entry.get("sender") or "") + ": " + str(entry.get("text") or ""))
        else:
            lines.append(str(entry or ""))
    return "\n".join(lines)


def _plain(reply, package):
    return reply, False, None, {
        "shown": [],
        "count": 0,
        "search_ms": package.get("search_ms"),
        "retrieval_status": (package.get("retrieval") or {}).get("status"),
    }
