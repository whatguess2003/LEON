"""LangGraph Catalog Agent. Prefers repairing an existing Postgres subcatalog. Never amends application code."""

import json
import os
from typing import Annotated, Literal, TypedDict

from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages

_CATALOG_MODEL = "gpt-6.1-sol"
_CATALOG_EFFORT = "low"
_RECURSION_LIMIT = 24


class CatalogState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    trace_id: int
    auto_promote: bool
    result: dict


def _model():
    import httpx
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=_CATALOG_MODEL,
        api_key=os.getenv("OPENAI_API_KEY"),
        timeout=httpx.Timeout(None),
        use_responses_api=True,
        reasoning_effort=_CATALOG_EFFORT,
    )


def _schema_problem(body):
    """A schema proposal names a future table. It does not create that table."""
    spec = body.get("schema") if isinstance(body.get("schema"), dict) else {}
    table = str(spec.get("table") or "").strip()
    key = [str(item or "").strip() for item in (spec.get("natural_key") or []) if str(item or "").strip()]
    names = []
    for column in spec.get("columns") or []:
        if isinstance(column, dict):
            name = str(column.get("name") or "").strip()
        else:
            name = str(column or "").strip()
        if name:
            names.append(name)
    aliases = [str(item or "").strip() for item in (body.get("aliases") or body.get("keyphrases") or []) if str(item or "").strip()]
    sql = str(((body.get("solution") or {}).get("sql_template")) or "").strip()
    from booking_system.semantic.query import _words, accepted_reads
    words = set(_words(sql))
    column_words = {name.upper() for name in names if name.upper() != "HOTEL_ID"}
    sketched = table.upper() in words and "HOTEL_ID" in words and (not column_words or bool(column_words & words))
    if not table or not key or "hotel_id" not in key or not names or not aliases or not accepted_reads([sql]) or not sketched:
        return "schema needs a table, a key including hotel_id, columns, aliases, and a read sketched on that table"
    return ""


def _tools(sink):
    from langchain_core.tools import tool
    from booking_system.semantic.registry import (
        active_body,
        catalog_scopes,
        list_active_catalogs,
        save_proposal,
        visible_to,
    )

    manager = str(sink.get("manager") or "")

    @tool
    def list_catalogs() -> str:
        """List the active catalogs this manager may use: general ones and the manager's own user recipes, with scope, parents, titles, aliases, keyphrases, and concept names."""
        rows = []
        for row in list_active_catalogs():
            if not visible_to(row, manager):
                continue
            names = [str(c.get("name") or "") for c in row.get("concepts") or []]
            body = row.get("body") or {}
            aliases = [str(item or "").strip() for item in (body.get("aliases") or []) if str(item or "").strip()]
            rows.append({
                "catalog_id": row["catalog_id"],
                "scope": row["scope"],
                "parent_id": row.get("parent_id"),
                "title": row.get("title"),
                "purpose": row.get("purpose"),
                "version": row.get("version"),
                "aliases": aliases,
                "keyphrases": list(body.get("keyphrases") or []),
                "concepts": names,
            })
        return json.dumps(rows, ensure_ascii=True)

    @tool
    def read_catalog(catalog_id: str) -> str:
        """Read one active catalog body this manager may use, with its scope."""
        scope = catalog_scopes([catalog_id]).get(str(catalog_id or "")) or {}
        body = active_body(catalog_id) if visible_to(scope, manager) else None
        if not body:
            return json.dumps({"error": "catalog not found", "catalog_id": catalog_id})
        return json.dumps(dict(body, scope=scope["scope"]), ensure_ascii=True)

    @tool
    def inspect_schema(table_names: str) -> str:
        """Comma-separated public table names. Returns columns only. An empty list means that table is not in the public schema."""
        from booking_system.content_store import _with
        wanted = [part.strip() for part in str(table_names or "").split(",") if part.strip()]
        if not wanted:
            return "[]"

        def read(cur):
            cur.execute(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = ANY(%s)
                ORDER BY table_name, ordinal_position
                """,
                (wanted,),
            )
            grouped = {name: [] for name in wanted}
            for table, column, data_type in cur.fetchall():
                grouped.setdefault(table, []).append(column + " " + data_type)
            return grouped

        try:
            grouped = _with(read)
        except Exception as exc:
            return "Error: " + str(exc)
        return json.dumps(grouped, ensure_ascii=True)

    @tool
    def list_tables() -> str:
        """Public base table names. Use this before deciding a fact has no table."""
        from booking_system.content_store import _with

        def read(cur):
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
            return [row[0] for row in cur.fetchall()]

        try:
            names = _with(read)
        except Exception as exc:
            return "Error: " + str(exc)
        return json.dumps(names, ensure_ascii=True)

    @tool
    def run_read(sql: str, parameter_names_json: str = "") -> str:
        """Run the SQL you will submit. parameter_names_json is a JSON list whose only name is hotel_id. Relative days use CURRENT_DATE in the hotel time zone. No rows is a valid result."""
        from booking_system.semantic.context_engine import parameter_names, parameter_values
        from booking_system.semantic.executor import run_read as execute_read
        hotel_id = str(sink.get("hotel_id") or "")
        raw = []
        text = str(parameter_names_json or "").strip()
        if text:
            try:
                parsed = json.loads(text)
            except Exception:
                return json.dumps({"error": "parameter_names_json is not a JSON list"})
            if not isinstance(parsed, list):
                return json.dumps({"error": "parameter_names_json must be a list"})
            raw = parsed
        values, missing = parameter_values(
            hotel_id,
            [],
            parameter_names({"solution": {"parameters": raw}}),
        )
        if missing:
            return json.dumps({"error": "missing parameter " + " ".join(missing)})
        result = execute_read([str(sql or "")], values, None, hotel_id, "catalog")
        return json.dumps({
            "rows": result.get("rows") or [],
            "error": result.get("error") or "",
        }, ensure_ascii=True)

    @tool
    def expand_context(references_json: str) -> str:
        """Load one permitted reference. references_json is a JSON list of schema:table, page:catalog_id, or evidence:id."""
        from booking_system.semantic.context_engine import ContextEngine
        try:
            refs = json.loads(references_json) if isinstance(references_json, str) else []
        except Exception:
            return json.dumps({"error": "references_json is not a JSON list"})
        if not isinstance(refs, list):
            return json.dumps({"error": "references_json must be a list"})
        package = ContextEngine().expand(0, refs, {"max_tokens": 3000}, hotel_id=str(sink.get("hotel_id") or ""), user_id=manager)
        return json.dumps(package, ensure_ascii=True, default=str)

    @tool
    def submit_proposal(action: str, catalog_id: str, parent_id: str, diagnosis: str, body_json: str) -> str:
        """Save one proposal. action is repair, create_child, schema, or none.

        repair updates an existing catalog_id. create_child adds one reusable page.
        A decline returns a required fix. Repair the proposal and submit again.
        A repair or create_child body needs concepts, each with a formula, plus keyphrases and aliases.
        The knowledge index searches aliases and keyphrases. purpose is the summary shown after a match.
        A repair or create_child is one recipe. It needs problem, preconditions, inputs, output_grain, outputs, verification, and solution.sql_template or steps.
        catalog_id is a short natural snake_case name for the capability. It is required.
        outputs is a list of the column names the read returns, as plain strings.
        output_grain is guest, reservation, room, night, or channel.
        The validator runs a SQL page at the fixture hotel and checks isolation against another hotel.
        When run_read returns rows, validation.expected_value must appear in those rows.
        When run_read returns no rows, leave validation.expected_value empty and still submit the page.
        Seeded or unseeded rows do not change the SQL or the action. An empty result is a valid read of existing tables.
        Saving a proposal does not publish it. The reviewer decides whether it is published.
        If a required business definition is not established, put it in unresolved and use action none.
        """
        try:
            body = json.loads(body_json) if isinstance(body_json, str) else {}
        except Exception:
            return json.dumps({"error": "body_json is not valid JSON"})
        if not isinstance(body, dict):
            return json.dumps({"error": "body must be an object"})
        action = str(action or "none")
        catalog_id = str(catalog_id or body.get("catalog_id") or "")
        if action in ("schema", "alter"):
            action = "schema"
            problem = _schema_problem(body)
            if problem:
                return json.dumps({"error": problem})
            reason = " ".join(str(diagnosis or body.get("explanation") or "").split())
            if len(reason.split(" ")) < 6:
                return json.dumps({"error": "schema needs an explanation of why this table is required"})
            body["catalog_id"] = catalog_id
            body["explanation"] = reason
            proposal_id = save_proposal(
                sink.get("trace_id"),
                "schema",
                catalog_id,
                parent_id or body.get("parent") or "",
                body,
                reason,
            )
            result = {
                "trace_id": sink.get("trace_id"),
                "proposal_id": proposal_id,
                "action": "schema",
                "catalog_id": catalog_id,
                "diagnosis": reason,
                "status": "candidate",
            }
            sink["result"] = result
            return json.dumps(result, ensure_ascii=True)
        if action == "none":
            sink["result"] = {
                "trace_id": sink.get("trace_id"),
                "action": "none",
                "diagnosis": str(diagnosis or ""),
                "failure_class": "no_catalog_change",
            }
            return json.dumps(sink["result"])
        if not catalog_id.strip():
            return json.dumps({"error": "catalog_id required: a short natural snake_case name for the capability"})
        existing = catalog_scopes([catalog_id]).get(catalog_id)
        if existing and not (existing["scope"] == "user" and existing["user_id"] == manager):
            return json.dumps({
                "error": catalog_id + " is taken by a " + existing["scope"] + " recipe that is read-only for this manager. "
                "Submit create_child with a new catalog_id for this manager's own user recipe."
            })
        action = "repair" if existing else "create_child"
        body["catalog_id"] = catalog_id
        if action == "repair" and not body.get("parent"):
            body["parent"] = str(parent_id or "B01") if catalog_id != "B01" else ""
        concepts = body.get("concepts") or []
        phrases = [str(item or "").strip() for item in (body.get("keyphrases") or []) if str(item or "").strip()]
        if action in ("repair", "create_child") and (
            not concepts or not all(str(item.get("formula") or "").strip() for item in concepts)
        ):
            return json.dumps({"error": "each concept needs a formula"})
        if action in ("repair", "create_child") and not phrases:
            return json.dumps({"error": "keyphrases required"})
        from booking_system.semantic.recipe import recipe_contract
        missing = recipe_contract(body)
        if action in ("repair", "create_child") and missing:
            return json.dumps({"error": "recipe contract " + " ".join(missing)})
        body["keyphrases"] = phrases
        if not body.get("aliases"):
            body["aliases"] = list(phrases)
        proposal_id = save_proposal(
            sink.get("trace_id"),
            action,
            catalog_id,
            parent_id or body.get("parent") or "B01",
            body,
            str(diagnosis or ""),
        )
        pending = {
            "trace_id": sink.get("trace_id"),
            "proposal_id": proposal_id,
            "action": action,
            "catalog_id": catalog_id,
            "diagnosis": str(diagnosis or ""),
            "status": "candidate",
        }
        sink["pending"] = pending
        return json.dumps(pending, ensure_ascii=True)

    return [list_catalogs, read_catalog, inspect_schema, list_tables, run_read, expand_context, submit_proposal]


def _system(trace):
    path = os.path.join(os.path.dirname(__file__), "catalog_agent_prompt.md")
    with open(path, encoding="utf-8") as handle:
        prompt = handle.read().strip()
    return prompt + "\n\nTrace: " + json.dumps(trace, ensure_ascii=True)


def _trace_input(trace):
    return ""


def _close(sink):
    if sink.get("result"):
        return sink["result"]
    pending = dict(sink.get("pending") or {})
    if not pending.get("proposal_id"):
        return None
    pending["status"] = "candidate"
    sink["result"] = pending
    return pending


def improve_from_trace(trace_id, auto_promote=True, findings=None):
    """LangGraph catalog improvement. Writes proposals and promotions to Postgres only."""
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.prebuilt import ToolNode
    from booking_system.semantic.trace import load_trace

    trace = load_trace(trace_id)
    if not trace:
        return {"error": "trace not found", "trace_id": trace_id}
    if not str(trace.get("stall_reason") or "").strip() and int(trace.get("elapsed_ms") or 0) < 20000:
        return {"error": "trace is not a stall", "trace_id": trace_id}

    sink = {
        "trace_id": int(trace_id),
        "auto_promote": bool(auto_promote),
        "result": {},
        "hotel_id": trace.get("hotel_id") or "",
        "question": trace.get("question") or "",
        "stall_reason": trace.get("stall_reason") or "",
    }
    from booking_system.semantic.definitions import manager_definitions, trace_manager
    from booking_system.semantic.gate import over_investigation_budget, record_usage
    from booking_system.semantic.review_flow import platform_facts

    sink["manager"] = trace_manager(trace)

    facts = platform_facts(trace["hotel_id"])
    definitions = manager_definitions(trace_manager(trace))
    tools = _tools(sink)
    model = _model().bind_tools(tools)

    def agent(state: CatalogState):
        if sink.get("result") or sink.get("pending"):
            return {"messages": [AIMessage(content="Proposal recorded.")]}
        if over_investigation_budget(trace_id):
            sink["token_budget"] = True
            return {"messages": [AIMessage(content="Investigation token budget spent.")]}
        response = model.invoke([
            {"role": "system", "content": _system({
                "trace_id": trace["trace_id"],
                "hotel_id": trace["hotel_id"],
                "question": trace["question"],
                "catalog_ids": trace["catalog_ids"],
                "stall_reason": trace["stall_reason"],
                "elapsed_ms": trace["elapsed_ms"],
                "row_count": trace["row_count"],
                "error": trace["error"],
                "reply_excerpt": trace["reply_excerpt"],
                "attempts": trace["attempts"],
                "retrieval": trace.get("retrieval") or {},
                "platform_facts": facts,
                "manager_definitions": definitions,
            })},
            *state["messages"],
        ])
        usage = getattr(response, "usage_metadata", None) or {}
        record_usage(trace, "catalog", usage.get("input_tokens"), usage.get("output_tokens"))
        return {"messages": [response]}

    def should_continue(state: CatalogState) -> Literal["tools", "__end__"]:
        if sink.get("result") or sink.get("pending") or sink.get("token_budget"):
            return "__end__"
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return "__end__"

    builder = StateGraph(CatalogState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue)
    builder.add_edge("tools", "agent")
    graph = builder.compile()
    try:
        instruction = (
            _trace_input(trace)
            + "Investigate this trace and call submit_proposal once. "
            + "You do not approve or publish. The reviewer decides. "
            + "Use action none when the failure is not a catalog change."
        )
        if findings:
            instruction += (
                " The previous proposal was declined. Repair it using every required_fix, then call submit_proposal once."
                + "\nReviewer findings\n"
                + json.dumps(findings, ensure_ascii=True)
            )
        graph.invoke(
            {
                "messages": [HumanMessage(content=instruction)],
                "trace_id": int(trace_id),
                "auto_promote": bool(auto_promote),
            },
            config={"recursion_limit": _RECURSION_LIMIT},
        )
    except Exception as exc:
        closed = _close(sink)
        if closed:
            return closed
        return {"error": str(exc), "trace_id": trace_id}
    closed = _close(sink)
    if not closed and sink.get("token_budget"):
        return {"error": "investigation token budget spent", "token_budget": True, "trace_id": trace_id}
    if not closed:
        return {"error": "catalog agent did not submit a proposal", "trace_id": trace_id}
    return closed
