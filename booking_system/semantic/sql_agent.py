"""LangGraph SQL agent. Write one hotel-scoped PostgreSQL read, run it, and answer. Rewrite only after a database error."""

_RECURSION_LIMIT = 6


def _pg(fn):
    from booking_system.guest_manager import guest_manager

    def wrapped(cur):
        cur.execute("SET statement_timeout = 8000")
        try:
            return fn(cur)
        finally:
            cur.execute("SET statement_timeout = 0")

    return guest_manager._with_pg(wrapped)


def _tables(cur):
    cur.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    )
    return [row[0] for row in cur.fetchall()]


def _columns(cur, table):
    cur.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return cur.fetchall()


def _model():
    import os
    from langchain_anthropic import ChatAnthropic
    from booking_system.llm_utils import HAIKU_MODEL
    return ChatAnthropic(
        model=HAIKU_MODEL,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        default_request_timeout=90,
        max_tokens=16000,
        model_kwargs={"output_config": {"effort": "high"}},
    )


def _rules(catalog_ids, resolved_dates=None):
    from booking_system.semantic.catalog import entries_text
    lines = [
        "Every query filters UPPER(hotel_name) = UPPER(%(hotel_name)s). Do not write the hotel name.",
        "Select every field a catalog rule says the answer states. Confirm a fact the question asserts. Do not leave that field out of the SELECT.",
        "Reconcile by room type and stay date. Do not replace a room type with a hotel-wide total.",
        "A booking count is not an occupied-room count. Show both measures when the question compares them.",
        "An empty guest_phone is unknown. Do not treat blank phones as one guest.",
        "One cancellation is not a pattern. The denominator is that guest_phone's own bookings.",
        "room_inventory_nightly is the current row. There is no inventory snapshot at created_at. Say so when the question asks what could have been sold in the past.",
        "A preference or request is a recorded row, not a guarantee. A missing row is none.",
        "When the rows cover a shorter span than the question asked, state the earliest and latest date found.",
        "A resolution is a suggestion from the rows. Do not change a booking or the inventory.",
        "Write one SELECT or WITH, or up to six statements when the question has several parts. Do not sample with LIMIT when the answer needs a date span or an aggregate.",
        "After a successful query, answer from the rows. Do not write another query unless the previous run returned an error.",
    ]
    lines.append("The session time zone is the hotel time zone. CURRENT_DATE is that hotel's today.")
    lines.append("Yesterday is CURRENT_DATE - 1. Tomorrow is CURRENT_DATE + 1.")
    lines.append("A stock count has no date predicate.")
    body = entries_text(catalog_ids)
    if body:
        lines.append(body)
    return "\n".join(lines)


def _tools(hotel, sink, clock, resolved_dates=None):
    from langchain_core.tools import tool
    from psycopg2 import sql as pg_sql
    hotel_name = str(hotel or "").upper()

    @tool
    def sql_db_schema(table_names: str) -> str:
        """Input is a comma-separated list of tables. Output is the columns and up to 3 rows for this hotel.
        Example input: table1, table2"""
        wanted = [part.strip() for part in str(table_names or "").split(",") if part.strip()]

        def read(cur):
            known = set(_tables(cur))
            blocks = []
            for name in wanted:
                if name not in known:
                    blocks.append("Error: table " + name + " was not found")
                    continue
                columns = _columns(cur, name)
                lines = [name + "(" + ", ".join(column[0] + " " + column[1] for column in columns) + ")"]
                names = [column[0] for column in columns]
                if "hotel_name" in names:
                    statement = pg_sql.SQL("SELECT * FROM {} WHERE UPPER(hotel_name) = UPPER(%s) LIMIT 3").format(
                        pg_sql.Identifier(name)
                    )
                    cur.execute(statement, (hotel_name,))
                    rows = cur.fetchall()
                    headers = [item[0] for item in cur.description]
                    lines.append("sample")
                    lines.append("\t".join(headers))
                    for row in rows:
                        lines.append("\t".join("" if value is None else str(value) for value in row))
                blocks.append("\n".join(lines))
            return "\n\n".join(blocks)

        try:
            return _pg(read)
        except Exception as exc:
            return "Error: " + str(exc)

    @tool
    def sql_db_query(query: str) -> str:
        """Run one read-only PostgreSQL query. The hotel placeholder is bound for you.
        If an error is returned, rewrite the query and try again."""
        from booking_system.semantic.query import execute_reads
        sql = str(query or "")
        result = execute_reads([sql], {"hotel_name": hotel_name})
        if result.get("error"):
            sink["statements"] = []
            sink["error"] = str(result["error"])
            clock.record(sql, error=str(result["error"]), rows=0)
            return "Error: " + str(result["error"])
        rows = result.get("rows") or []
        sink["rows"] = rows
        sink["statements"] = result.get("statements") or []
        sink["error"] = ""
        clock.record(sql, error="", rows=len(rows))
        if not rows:
            return "No rows returned"
        lines = []
        for row in rows:
            lines.append(" ".join(str(key) + "=" + str(value) for key, value in row.items()))
        return "\n".join(lines)

    return [sql_db_schema, sql_db_query]


def _reply_text(output):
    if isinstance(output, list):
        parts = []
        for item in output:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item or ""))
        return "\n".join(part for part in parts if part).strip()
    return str(output or "").strip()


def _final_reply(messages):
    for message in reversed(messages):
        if getattr(message, "tool_calls", None):
            continue
        if getattr(message, "type", "") != "ai":
            continue
        text = _reply_text(getattr(message, "content", ""))
        if text:
            return text
    return ""


def _rows_reply(rows, limitation=""):
    lines = []
    for row in rows or []:
        parts = []
        for key, value in row.items():
            if key == "hotel_name" or value is None:
                continue
            parts.append(str(key) + " " + str(value))
        if parts:
            lines.append(" ".join(parts))
    body = "\n".join(lines) if lines else "No rows returned"
    note = str(limitation or "").strip()
    if note:
        return body + "\n" + note
    return body


def _cached_reply(rows, accepted_at):
    body = _rows_reply(rows)
    when = str(accepted_at or "").strip()
    if when:
        return body + "\nAs of " + when
    return body


def _finish(hotel, question, catalog_ids, clock, sink, reply, error="", recursion_hit=False):
    from booking_system.semantic.registry import active_versions
    from booking_system.semantic.trace import save_trace

    succeeded = bool(sink.get("statements")) and not str(sink.get("error") or "")
    if succeeded:
        reply = _rows_reply(sink.get("rows") or [])
        recursion_hit = False
        error = ""
    reason = clock.reason(recursion_hit=recursion_hit)
    if not reason and clock.over_budget():
        reason = "time_budget"
    versions = [
        {"catalog_id": item["catalog_id"], "version": item["version"]}
        for item in active_versions(catalog_ids)
    ]
    if not succeeded and not reply and sink.get("rows"):
        reply = _rows_reply(
            sink["rows"],
            "The investigation stopped before a full answer. These are the rows already returned.",
        )
    trace_id = save_trace(
        hotel,
        question,
        catalog_ids,
        versions,
        clock.attempts,
        reason,
        len(sink.get("rows") or []),
        clock.elapsed_ms(),
        reply,
        error or sink.get("error") or "",
    )
    return {
        "reply": reply or "",
        "rows": sink.get("rows") or [],
        "statements": list(sink.get("statements") or []),
        "error": error or sink.get("error") or "",
        "stall_reason": reason,
        "trace_id": trace_id,
        "elapsed_ms": clock.elapsed_ms(),
        "attempts": list(clock.attempts),
    }


def answer_question(hotel, question, catalog_ids=None, parameters=None, resolved_dates=None, definitions=None):
    """The SQL graph answers the desk. A repeated parameter set re-runs the accepted SQL."""
    from booking_system.semantic.cache import drop, get, put, schema_text, usable
    from booking_system.semantic.query import execute_reads
    hotel_name = str(hotel or "").upper()
    params = usable(parameters)
    if params:
        cached = get(hotel_name, catalog_ids, params)
        if cached:
            result = execute_reads(cached.get("statements") or [], {"hotel_name": hotel_name})
            if result.get("error"):
                drop(hotel_name, catalog_ids, params)
            else:
                return {
                    "reply": _cached_reply(result.get("rows") or [], cached.get("accepted_at") or ""),
                    "rows": result.get("rows") or [],
                    "error": "",
                    "cache": "hit",
                }
    import time
    from booking_system.semantic.trace import _QUERY_BUDGET_S, save_trace
    from booking_system.semantic.registry import active_versions
    started = time.monotonic()
    page_ids = list(catalog_ids or [])
    found = _investigate(hotel_name, question, page_ids, schema_text(page_ids), None, definitions)
    wall_ms = int((time.monotonic() - started) * 1000)
    found["elapsed_ms"] = max(int(found.get("elapsed_ms") or 0), wall_ms)
    # A 20s wall clock is a timeout. The controller records it and does not open a catalog investigation.
    if wall_ms >= int(_QUERY_BUDGET_S * 1000) and not found.get("stall_reason"):
        found["stall_reason"] = "time_budget"
        versions = [
            {"catalog_id": item["catalog_id"], "version": item["version"]}
            for item in active_versions(catalog_ids)
        ]
        trace_id = save_trace(
            hotel_name,
            question,
            catalog_ids,
            versions,
            [],
            "time_budget",
            len(found.get("rows") or []),
            wall_ms,
            found.get("reply") or "",
            found.get("error") or "",
        )
        found["trace_id"] = trace_id
    if params and found.get("statements") and not found.get("error") and not found.get("stall_reason"):
        put(hotel_name, catalog_ids, params, found["statements"])
    found["cache"] = "miss" if params else "bypass"
    return found


def _question_input(question, resolved_dates):
    return str(question or "")


def _investigate(hotel, question, catalog_ids, columns, resolved_dates=None, definitions=None):
    """Write and run SQL. Cached columns skip the schema model call."""
    import json
    from typing import Literal
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.errors import GraphRecursionError
    from langgraph.graph import START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode
    from booking_system.semantic.trace import InvestigationClock

    clock = InvestigationClock()
    sink = {"rows": [], "statements": [], "error": ""}
    dates = [str(day) for day in (resolved_dates or []) if str(day)]
    schema_tool, query_tool = _tools(hotel, sink, clock, dates)
    model = _model()
    rules = _rules(catalog_ids, dates)
    if columns:
        rules = rules + "\nColumns\n" + columns
    if definitions:
        rules = rules + "\nThe manager's definitions. Count exactly what they say.\n" + json.dumps(list(definitions), ensure_ascii=False)

    def list_tables(state):
        try:
            names = _pg(_tables)
            text = ", ".join(names)
        except Exception as exc:
            text = "Error: " + str(exc)
        return {"messages": [HumanMessage(content="Available tables: " + text)]}

    def call_get_schema(state):
        bound = model.bind_tools([schema_tool], tool_choice="sql_db_schema")
        response = bound.invoke([
            {"role": "system", "content": "Call sql_db_schema for the tables that answer the question. " + rules},
            *state["messages"],
        ])
        return {"messages": [response]}

    def generate_query(state):
        if sink.get("statements") and not sink.get("error"):
            return {"messages": [AIMessage(content=_rows_reply(sink.get("rows") or []))]}
        if clock.over_budget() or clock.reason() or len(clock.attempts) >= 3:
            text = _rows_reply(
                sink.get("rows") or [],
                "The investigation hit its time or attempt budget. These are the rows already returned.",
            )
            return {"messages": [AIMessage(content=text)]}
        system = (
            "You are an agent designed to interact with a SQL database. "
            "Given an input question, create a syntactically correct PostgreSQL query to run, "
            "then look at the results of the query and return the answer. "
            "Never query for all the columns from a specific table, only ask for the relevant columns given the question. "
            "DO NOT make any DML statements (INSERT, UPDATE, DELETE, DROP etc.) to the database. "
            "If the query returns an error, rewrite the query and try again. "
            + rules
        )
        bound = model.bind_tools([query_tool])
        response = bound.invoke([
            {"role": "system", "content": system},
            *state["messages"],
        ])
        return {"messages": [response]}

    def should_continue(state: MessagesState) -> Literal["run_query", "__end__"]:
        if clock.over_budget() or clock.reason() or len(clock.attempts) >= 3:
            return "__end__"
        if sink.get("statements") and not sink.get("error"):
            return "__end__"
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "run_query"
        return "__end__"

    def after_query(state: MessagesState) -> Literal["generate_query", "__end__"]:
        if sink.get("statements") and not sink.get("error"):
            return "__end__"
        return "generate_query"

    builder = StateGraph(MessagesState)
    builder.add_node("generate_query", generate_query)
    builder.add_node("run_query", ToolNode([query_tool], name="run_query"))
    if columns:
        builder.add_edge(START, "generate_query")
    else:
        builder.add_node("list_tables", list_tables)
        builder.add_node("call_get_schema", call_get_schema)
        builder.add_node("get_schema", ToolNode([schema_tool], name="get_schema"))
        builder.add_edge(START, "list_tables")
        builder.add_edge("list_tables", "call_get_schema")
        builder.add_edge("call_get_schema", "get_schema")
        builder.add_edge("get_schema", "generate_query")
    builder.add_conditional_edges("generate_query", should_continue)
    builder.add_conditional_edges("run_query", after_query)
    graph = builder.compile()
    try:
        result = graph.invoke(
            {"messages": [HumanMessage(content=_question_input(question, dates))]},
            config={"recursion_limit": _RECURSION_LIMIT},
        )
    except GraphRecursionError as exc:
        return _finish(
            hotel, question, catalog_ids, clock, sink,
            _final_reply([]),
            error=sink.get("error") or str(exc),
            recursion_hit=True,
        )
    except Exception as exc:
        return _finish(hotel, question, catalog_ids, clock, sink, str(exc), error=str(exc))
    reply = _final_reply(result.get("messages") or [])
    return _finish(hotel, question, catalog_ids, clock, sink, reply)
