"""One onboarding turn. The acting step (gpt-6.1-sol, low effort, tool calls) makes the writes the manager's message calls for; the
reply step receives the state after the writes and the outcome of every write, and writes the reply (it may claim a save only from a
successful outcome) with the questions it asks, which are recorded as open items. An empty message is the manager opening the page:
no writes, a greeting, or a recap when they return after a while."""

import json
import os
from datetime import datetime, timezone

from booking_system.onboarding import importer, invites, llm, sops, state, tools

_PROMPT = open(os.path.join(os.path.dirname(__file__), "onboarding_agent_prompt.md"), encoding="utf-8").read()
_SHARED, _, _STEPS = _PROMPT.partition("\n## Acting step")
_ACT, _, _REPLY = _STEPS.partition("\n## Reply step")
ACT_INSTRUCTIONS = _SHARED + "\n## Acting step" + _ACT
REPLY_INSTRUCTIONS = _SHARED + "\n## Reply step" + _REPLY
MAX_ROUNDS = 6
HISTORY = 14


def _reply_schema():
    return {
        "type": "object",
        "properties": {
            "reply": {"type": "string"},
            "asked": {"type": "array", "items": {
                "type": "object",
                "properties": {"section": {"type": "string", "enum": list(state.SECTION_NAMES)}, "key": {"type": "string"},
                               "question": {"type": "string"}},
                "required": ["section", "key", "question"], "additionalProperties": False}},
        },
        "required": ["reply", "asked"],
        "additionalProperties": False,
    }


# ---------- what the data proves ----------

def _pending(hotel_id):
    from booking_system.settings import service
    return service.pending(hotel_id) if hotel_id else {"modules": {}}


def _open_definitions(user_id):
    from booking_system.semantic.definitions import open_definitions
    return open_definitions(user_id) if user_id else []


def _proven(turn, pending, definitions_open):
    modules = pending.get("modules") or {}
    asks = [row["setting_key"] for found in modules.values() for row in found["ask"]]
    enablement = {name + ".enabled" for name in modules}
    return {
        "account": bool(turn["user_id"]),
        "hotel": bool(turn["hotel_id"]),
        "modules": bool(turn["hotel_id"]) and not [key for key in asks if key in enablement],
        "settings": bool(turn["hotel_id"]) and not asks,
        "definitions": bool(turn["user_id"]) and not [item for item in definitions_open if not item["skipped"]],
    }


def _recipe_phrases(user_id):
    """Published recipes this manager may use whose columns are alternative readings of one figure: the populations a definition picks."""
    from booking_system.semantic.registry import list_active_catalogs, visible_to
    found = []
    for row in list_active_catalogs():
        body = row.get("body") or {}
        readings = body.get("readings") or {}
        if readings and visible_to(row, user_id):
            found.append({"catalog_id": str(row.get("catalog_id") or ""), "problem": " ".join(str(body.get("problem") or "").split())[:160],
                          "populations": sorted({str(item.get("population") or "") for item in readings.values()})})
    return found


def _settings_view(hotel_id, pending):
    from booking_system.settings import service
    if not hotel_id:
        return {}
    rows = {row["setting_key"]: row for row in service.catalog_rows()}
    values = service.values_in_effect(hotel_id)
    return {
        "modules": {name: {"governed": found["governed"], "enabled": found["enabled"],
                           "ask": [{key: row[key] for key in ("setting_key", "question", "explanation", "value_type", "options", "sop_prompt")}
                                   for row in found["ask"]],
                           "waiting": [{key: row[key] for key in ("setting_key", "question", "waits_for")} for row in found["waiting"]]}
                    for name, found in (pending.get("modules") or {}).items()},
        "in_effect": {key: {"value": value, "meaning": next((o["meaning"] for o in rows[key]["options"] if o["value"] == value), "")}
                      for key, value in values.items() if key in rows},
    }


def _imports_view(hotel_id, onboarding_id):
    if not hotel_id:
        return []
    return [{"batch_id": found["batch"]["batch_id"], "status": found["batch"]["status"], "detail": found["batch"]["detail"],
             "files": [{key: item[key] for key in ("file_id", "file_name", "entity", "label", "mapping", "mapping_status", "row_count", "valid_count",
                                                   "error_count", "duplicate_count", "created_count")}
                       | {"errors": item["errors"][:5], "headers": item["headers"]} for item in found["files"]]}
            for found in importer.batches(hotel_id, onboarding_id)]


def _routines(user_id):
    from booking_system.semantic.personal import personal_recipes
    return [{"key": item["key"], "name": item.get("name"),
             "figures": [{"label": figure.get("label"), "status": figure.get("status"), "day_offset": figure.get("day_offset")}
                         for figure in item.get("items") or []]}
            for item in (personal_recipes(user_id) if user_id else [])]


def _account(turn):
    from booking_system.user_manager import read_user
    user = read_user(turn["user_id"]) if turn["user_id"] else None
    return {"signed_in": bool(user), "user_name": (user or {}).get("user_name") or "", "hotel_name": turn["hotel_name"],
            "primary_language": (user or {}).get("primary_language") or "", "hotels": [row["hotel_name"] for row in (user or {}).get("hotels") or []]}


def snapshot(turn, session, away):
    """The state the steps read and the UI shows."""
    from booking_system.semantic.definitions import manager_definitions
    pending = _pending(turn["hotel_id"])
    definitions_open = _open_definitions(turn["user_id"])
    sections = state.checklist(turn["onboarding_id"], _proven(turn, pending, definitions_open))
    every = state.items(turn["onboarding_id"])
    closed = [item for item in every if item["kind"] == "open" and item["status"] != "open"]
    return {
        "onboarding_id": turn["onboarding_id"],
        "returning": away >= state.RETURNING_HOURS,
        "away_hours": away,
        "style": {key: session[key] for key in ("language", "tone", "pace", "role_note")},
        "account": _account(turn),
        "needs_password": not turn["user_id"],
        "checklist": [{key: row[key] for key in ("section", "label", "why", "status", "note")} for row in sections],
        "open_questions": [{"key": item["item_key"], "section": item["section"], "question": item["summary"], "asked_count": item["asked_count"],
                            "last_asked_at": item["last_asked_at"], "stale": state.is_stale(item)}
                           for item in every if item["kind"] == "open" and item["status"] == "open"],
        "answered_or_skipped": [{"key": item["item_key"], "status": item["status"]} for item in closed[-30:]],
        "saved": [{"section": item["section"], "key": item["item_key"], "summary": item["summary"], "at": item["created_at"]}
                  for item in every if item["kind"] == "saved"][-30:],
        "recap": state.recap(sections, every),
        "definitions": {
            "answered": [{key: item[key] for key in ("key", "phrase", "definition")} for item in (manager_definitions(turn["user_id"]) if turn["user_id"] else [])],
            "open": definitions_open,
            "phrases_in_recipes": _recipe_phrases(turn["user_id"]) if turn["user_id"] else [],
        },
        "settings": _settings_view(turn["hotel_id"], pending),
        "routines": _routines(turn["user_id"]),
        "sops": sops.listing(turn["hotel_id"]) if turn["hotel_id"] else [],
        "imports": _imports_view(turn["hotel_id"], turn["onboarding_id"]),
        "importable": importer.entity_catalog(importer.available(turn["hotel_id"], turn["modules"]), turn["hotel_id"]) if turn["hotel_id"] else [],
        "invites": invites.listing(turn["hotel_id"]) if turn["hotel_id"] else [],
    }


# ---------- the turn ----------

def _onboarding_graph(turn, view, away, text):
    """Acting and reply are nodes. Tool calls are a node. Nothing in the turn calls the model outside this graph."""
    from typing import Annotated, TypedDict

    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode

    class OnboardState(TypedDict, total=False):
        messages: Annotated[list, add_messages]
        rounds: int
        spoken: dict

    bound = tools.build(turn)
    model = llm.chat().bind_tools(bound)
    tool_node = ToolNode(bound)

    def act(graph_state: OnboardState):
        answer = model.invoke(graph_state.get("messages") or [])
        return {"messages": [answer], "rounds": int(graph_state.get("rounds") or 0) + 1}

    def after_act(graph_state: OnboardState):
        last = (graph_state.get("messages") or [None])[-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return "reply"

    def after_tools(graph_state: OnboardState):
        if int(graph_state.get("rounds") or 0) >= MAX_ROUNDS:
            return "reply"
        return "act"

    def reply(graph_state: OnboardState):
        current = snapshot(turn, state.read_session(turn["onboarding_id"]), away) if text else view
        return {"spoken": _speak(turn, current, text)}

    def record(graph_state: OnboardState):
        spoken = graph_state.get("spoken") or {"reply": "", "asked": []}
        for item in spoken.get("asked") or []:
            if str(item.get("key") or "").strip():
                state.note_asked(turn["onboarding_id"], item["section"], item["key"].strip(), item["question"])
        entries = ([{"role": "person", "text": text}] if text else []) + [{"role": "leon", "text": spoken.get("reply") or "", "results": [item["said"] for item in turn["outcomes"]]}]
        state.append(turn["onboarding_id"], entries, turn["user_id"], turn["hotel_id"])
        final = snapshot(turn, state.read_session(turn["onboarding_id"]), 0.0)
        return {"spoken": dict(spoken, state=final)}

    def start(graph_state: OnboardState):
        return "act" if text else "reply"

    builder = StateGraph(OnboardState)
    builder.add_node("act", act)
    builder.add_node("tools", tool_node)
    builder.add_node("reply", reply)
    builder.add_node("record", record)
    builder.add_conditional_edges(START, start)
    builder.add_conditional_edges("act", after_act)
    builder.add_conditional_edges("tools", after_tools)
    builder.add_edge("reply", "record")
    builder.add_edge("record", END)
    compiled = builder.compile()
    payload = dict(view, today=datetime.now(timezone.utc).date().isoformat(), conversation=state.conversation(turn["onboarding_id"], HISTORY),
                   message=text, password_typed=bool(turn["secret"]))
    opening = [SystemMessage(ACT_INSTRUCTIONS), HumanMessage(json.dumps(payload, default=str))] if text else []
    return compiled.invoke({"messages": opening, "rounds": 0}, config={"recursion_limit": MAX_ROUNDS * 2 + 4})


def _speak(turn, view, message):
    results = [item["said"] for item in turn["outcomes"]] or ["Nothing was written this turn."]
    payload = dict(view, conversation=state.conversation(turn["onboarding_id"], HISTORY), message=message or "(the manager opened onboarding)",
                   Results=results, password_typed=bool(turn["secret"]))
    return llm.structured(REPLY_INSTRUCTIONS, payload, "onboarding_reply", _reply_schema())


def _turn(onboarding_id, user_id, hotel_id, hotel_name, modules, secret):
    return {"onboarding_id": onboarding_id, "user_id": user_id, "hotel_id": hotel_id, "hotel_name": hotel_name, "modules": modules or {},
            "secret": str(secret or ""), "outcomes": [], "token": "", "user_name": ""}


def run_turn(onboarding_id, message, secret, claims):
    """claims: the verified session claims ({} when signed out). Returns the reply, this turn's outcomes, the state, and a new token
    when the turn created or signed in the account or created the hotel."""
    claims = claims or {}
    session = state.open_session(onboarding_id, claims.get("user_id"), claims.get("hotel_id"))
    away = state.away_hours(state.touch(session["onboarding_id"]))
    turn = _turn(session["onboarding_id"], session["user_id"], session["hotel_id"], claims.get("hotel_name") or "", claims.get("modules"), secret)
    text = str(message or "").strip()
    view = snapshot(turn, session, away)
    found = _onboarding_graph(turn, view, away, text)
    spoken = found.get("spoken") or {}
    final = spoken.get("state") or snapshot(turn, state.read_session(turn["onboarding_id"]), 0.0)
    return {"onboarding_id": turn["onboarding_id"], "reply": spoken.get("reply") or "", "asked": spoken.get("asked") or [],
            "outcomes": [{"tool": o["tool"], "ok": o["ok"], "said": o["said"]} for o in turn["outcomes"]],
            "state": final, "token": turn["token"], "hotel_name": turn["hotel_name"]}


def read_state(onboarding_id, claims):
    """The state for the page, without a model call: the session the visitor holds (or the person's latest), with the recap."""
    claims = claims or {}
    session = state.open_session(onboarding_id, claims.get("user_id"), claims.get("hotel_id"))
    turn = _turn(session["onboarding_id"], session["user_id"], session["hotel_id"], claims.get("hotel_name") or "", claims.get("modules"), "")
    view = snapshot(turn, session, state.away_hours(session["last_turn_at"]) if session["turns"] else 0.0)
    return dict(view, conversation=state.conversation(turn["onboarding_id"]))


def note_event(onboarding_id, user_id, hotel_id, text, section="", key="", summary=""):
    """Something the manager did in the panel (an upload, a confirmation), so the agent sees it in the conversation."""
    if not onboarding_id:
        return
    found = state.read_session(onboarding_id)
    if not found or found["hotel_id"] != hotel_id:
        return
    state.append(onboarding_id, [{"role": "event", "text": text}], user_id, hotel_id)
    if section and key and summary:
        state.note_saved(onboarding_id, section, key, summary)
