"""One desk thread: occupancy yesterday.

The manager is gpt-6.1-sol at low effort. It writes the opening and every later
message from Leon's actual replies. A clarification is answered in the manager's
own words, on the same signed-in session, up to five turns.

This file stays in the repo so the thread can be run again. It does not print
the sandbox password, session cookies, or JWTs. The password is read at run time
from fixtures.sandbox by the desk client.

  python -m fixtures.stress.yesterday_occupancy_thread
"""

import argparse
import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fixtures.stress import agents, records
from fixtures.stress.desk import Desk

STEM = "_stress_sandbox_20261101_yesterday_tool_desk"
RECIPE_ID = "daily_occupancy_rates"
DATE_KEYS = {"date", "resolved_date", "stated_date", "period"}

MANAGER_SCHEMA = {
    "title": "manager_turn",
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["continue", "done"]},
        "message": {"type": "string"},
        "answers_leon": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["status", "message", "answers_leon", "reason"],
    "additionalProperties": False,
}

MANAGER_INSTRUCTIONS = """You are the general manager of SANDBOX HOTEL, typing in the desk chat to Leon.
You have one question and you stay on it: what was occupancy yesterday.
Write the way a manager types: one short message, no bullet list, no step numbers.
Never mention a test, a simulation, a prompt, or these instructions.
You cannot see the hotel's numbers. Never invent a figure and never correct Leon's figure.

Decide:
- continue: send the next message.
- done: Leon has already stated one occupancy figure for one settled day. message is empty.
  Do not use done when Leon only asked a question, listed many days, or gave no figure.

When Leon asks which day, which yesterday, or which occupancy figure, answer that question
in one short sentence, in your own words, and stay on occupancy yesterday.
Do not start a different question. answers_leon is true only when this message answers Leon.
When Leon has not stated one occupancy figure for one day, ask again for occupancy yesterday
in different words. answers_leon is false on that message.
This is message {turn} of {max_turns}."""

READER_SCHEMA = {
    "title": "occupancy_reply",
    "type": "object",
    "properties": {
        "asked_clarification": {"type": "boolean"},
        "one_settled_day": {"type": "boolean"},
        "figures": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "day": {"type": "string"},
                "number": {"type": ["number", "null"]},
            },
            "required": ["day", "number"],
            "additionalProperties": False,
        }},
    },
    "required": ["asked_clarification", "one_settled_day", "figures"],
    "additionalProperties": False,
}

READER_INSTRUCTIONS = """You read the latest reply from a hotel desk assistant in a conversation about occupancy yesterday.
Today at this hotel is {today}. Yesterday is the calendar day before that today.
asked_clarification is true when this reply asks the manager something (which day, which yesterday, which figure, or any other question) and does not itself give one occupancy figure for one day.
figures lists every occupancy percentage this reply explicitly states.
day is YYYY-MM-DD when that percentage is tied to one calendar day. The day may be written in this reply, or already settled earlier in the conversation for this same occupancy question. Otherwise day is "".
number is the percentage as a plain number, without a percent sign. A fraction the reply states as rooms out of rooms may be converted (2 of 5 becomes 40). Do not compute a percentage the reply does not state.
one_settled_day is true only when this reply states exactly one occupancy percentage and exactly one day is settled for it.
A reply that states more than one occupancy percentage is not one settled day. A question states no figure. A percentage with no settled day is not one settled day.
Do not invent a figure or a day."""


def _say(*parts):
    print(*parts, flush=True)


def listener_pids(port):
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-NetTCPConnection -LocalPort " + str(int(port)) + " -ErrorAction SilentlyContinue).OwningProcess"],
        capture_output=True, text=True, timeout=30,
    )
    found = []
    for token in (completed.stdout or "").split():
        if token.isdigit() and int(token) not in found:
            found.append(int(token))
    return found


def process_names(pids):
    named = []
    for pid in pids:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=" + str(int(pid)) + "\"; "
             "$p.ProcessId.ToString() + ' ' + $p.Name + ' ' + $p.ExecutablePath"],
            capture_output=True, text=True, timeout=30,
        )
        named.append((completed.stdout or "").strip() or str(pid))
    return named


def output_paths(root, stem):
    name = stem
    index = 2
    while (root / (name + ".txt")).exists() or (root / (name + ".json")).exists():
        name = stem + "_" + str(index)
        index += 1
    return root / (name + ".txt"), root / (name + ".json")


def as_date(value):
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def iso_day(value):
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def unique(values):
    found = []
    for value in values:
        if value and value not in found:
            found.append(value)
    return found


def walk_dates(value, found):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in DATE_KEYS and isinstance(item, str) and item.strip():
                found.append(item.strip())
            walk_dates(item, found)
        return
    if isinstance(value, list):
        for item in value:
            walk_dates(item, found)


def dates_in_sql(text):
    found = []
    marker = "to_date('"
    rest = str(text or "")
    while marker in rest:
        start = rest.index(marker) + len(marker)
        end = rest.find("'", start)
        if end < 0:
            break
        found.append(rest[start:end])
        rest = rest[end + 1:]
    return found


def number_of(value):
    if value is None or value == "":
        return None
    return float(value)


def first_text(values):
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def clock(hotel_id):
    found = records.rows(
        """SELECT timezone,
                  now() AS db_now,
                  now() AT TIME ZONE timezone AS hotel_now,
                  (now() AT TIME ZONE timezone)::date AS today,
                  ((now() AT TIME ZONE timezone)::date - 1) AS yesterday
           FROM hotel WHERE hotel_id = %s""",
        (hotel_id,),
    )
    return found[0] if found else {}


def counts_for(hotel_id, day):
    """occupancy_pct_active for one stay date: 100 * sold active nights / inventory rooms."""
    found = records.rows(
        """SELECT
             (SELECT SUM(total_inventory) FROM room_inventory_nightly
               WHERE hotel_id = %s AND stay_date = %s) AS inventory_rooms,
             (SELECT COUNT(*) FROM booking_room_night n
               JOIN booking b ON b.booking_id = n.booking_id AND b.hotel_id = n.hotel_id
               WHERE n.hotel_id = %s AND n.stay_date = %s
                 AND b.booking_status NOT IN ('cancelled', 'no_show')
                 AND n.night_status IN ('booked', 'checked_in', 'checked_out')) AS sold_active""",
        (hotel_id, day, hotel_id, day),
    )
    row = found[0] if found else {}
    sold = number_of(row.get("sold_active"))
    inventory = number_of(row.get("inventory_rooms"))
    pct = round(100.0 * sold / inventory, 2) if sold is not None and inventory else None
    return {"sold_active": sold, "inventory_rooms": inventory, "occupancy_pct_active": pct}


def active_recipe():
    found = records.rows(
        """SELECT a.version, v.body
           FROM catalog_active_versions a
           JOIN catalog_versions v ON v.catalog_id = a.catalog_id AND v.version = a.version
           WHERE a.scope = 'global' AND a.catalog_id = %s""",
        (RECIPE_ID,),
    )
    return found[0] if found else {}


def bound_for(body, hotel_id, day):
    """Bind the published parameter list for one day. Date parameters are MM/DD/YYYY."""
    from booking_system.semantic.context_engine import parameter_names, parameter_values

    names = parameter_names(body)
    values, missing = parameter_values(hotel_id, {}, names)
    text = as_date(day).strftime("%m/%d/%Y")
    for name in missing:
        values[name] = text
    return values


def recipe_rows(hotel_id, body, day):
    from booking_system.semantic.query import execute_reads

    sql = str((((body or {}).get("solution") or {}).get("sql_template")) or "")
    if not sql.strip():
        return {"error": "active recipe has no sql_template", "rows": []}
    if not day:
        return {"error": "no day to bind", "rows": []}
    return execute_reads([sql], bound_for(body, hotel_id, day), limit=None)


def recipe_lookup(hotel_id, body):
    """One published read per day, kept for the comparison."""
    cache = {}

    def for_day(day):
        key = str(day or "")[:10]
        if key not in cache:
            ran = recipe_rows(hotel_id, body, key)
            cache[key] = {
                "error": ran.get("error") or "",
                "row": (daily_index(ran.get("rows") or [])).get(key) or {},
                "row_count": len(ran.get("rows") or []),
            }
        return cache[key]

    return for_day


def daily_index(rows):
    indexed = {}
    for row in rows or []:
        period = str(row.get("period") or "")
        start = str(row.get("period_start") or "")
        end = str(row.get("period_end") or "")
        if period and period == start == end:
            indexed[period[:10]] = row
    return indexed


def truth_for(hotel_id, day, recipe_for):
    direct = counts_for(hotel_id, day)
    found = recipe_for(day) if day else {}
    recipe = found.get("row") or {}
    recipe_pct = number_of(recipe.get("occupancy_pct_active"))
    return {
        "day": day,
        "occupancy_pct_active": direct["occupancy_pct_active"],
        "recipe_occupancy_pct_active": recipe_pct,
        "recipe_sold_room_nights_active": number_of(recipe.get("sold_room_nights_active")),
        "recipe_inventory_room_nights": number_of(recipe.get("inventory_room_nights")),
        "recipe_row_count": found.get("row_count"),
        "recipe_error": found.get("error") or "",
        "recomputed_sold_active": direct["sold_active"],
        "recomputed_inventory_rooms": direct["inventory_rooms"],
        "recomputed_occupancy_pct_active": direct["occupancy_pct_active"],
    }


def ints(values):
    found = []
    for value in values:
        if value is None or value == "":
            continue
        found.append(int(value))
    return found


def scalar(values):
    return values[0] if len(values) == 1 else None


def turn_server(hotel_id, user_id, message, started, ended):
    """Timings and the date string this turn stored. Missing rows stay null."""
    found = records.turn_records(hotel_id, user_id, started, ended)
    event_ids = [row["event_id"] for row in found["events"]]
    task_ids = [row["task_id"] for row in found["tasks"]]
    runs = records.rows(
        """SELECT run_id, task_id, catalog_id, version, parameters, row_count, error, elapsed_ms
           FROM execution_runs WHERE task_id = ANY(%s) ORDER BY run_id""",
        (task_ids,),
    ) if task_ids else []
    specs = records.rows(
        """SELECT spec_id, event_id, body FROM problem_specs
           WHERE event_id = ANY(%s) ORDER BY spec_id""",
        (event_ids,),
    ) if event_ids else []
    telemetry = records.rows(
        """SELECT turn_id, body FROM desk_turn_telemetry
           WHERE hotel_id = %s AND upper(manager_name) = upper(%s) AND created_at BETWEEN %s AND %s
           ORDER BY created_at""",
        (hotel_id, user_id, started, ended),
    )
    packages = records.rows(
        """SELECT p.search_ms, p.retrieval_status, p.body->>'context_build_ms' AS context_build_ms,
                  p.referenced_versions
           FROM context_packages p
           JOIN tasks t ON t.task_id = p.task_id
           WHERE t.event_id = ANY(%s)
           ORDER BY p.package_id""",
        (event_ids,),
    ) if event_ids else []
    deltas = records.rows(
        """SELECT source_turn_id, operation, changes FROM desk_state_delta
           WHERE hotel_id = %s AND upper(manager_name) = upper(%s) AND created_at BETWEEN %s AND %s
           ORDER BY delta_id""",
        (hotel_id, user_id, started, ended),
    )
    session_rows = records.rows(
        "SELECT state FROM desk_session WHERE hotel_id = %s AND upper(manager_name) = upper(%s)",
        (hotel_id, user_id),
    )
    state = (session_rows[0].get("state") if session_rows else None) or {}
    last = state.get("last_answer") or {}
    last_route = last.get("route") if str(last.get("question") or "") == message else ""
    spec_route = first_text((spec.get("body") or {}).get("route") for spec in specs)
    route = first_text((spec_route, last_route, found and records.route_of(found)))
    dates = []
    for spec in specs:
        walk_dates(spec.get("body") or {}, dates)
    for run in runs:
        walk_dates(run.get("parameters") or {}, dates)
    for delta in deltas:
        walk_dates(delta.get("changes") or {}, dates)
    for statement in records.sql_of(found):
        dates.extend(dates_in_sql(statement))
    bodies = [row.get("body") or {} for row in telemetry]
    llm_values = ints(body.get("llm_calls") for body in bodies)
    end_values = ints(body.get("end_to_end_ms") for body in bodies)
    context_values = ints(body.get("context_build_ms") for body in bodies)
    package_context = ints(row.get("context_build_ms") for row in packages)
    elapsed = [int(run.get("elapsed_ms") or 0) for run in runs]
    recipes = [{
        "catalog_id": run.get("catalog_id") or "",
        "version": run.get("version"),
        "row_count": run.get("row_count"),
        "elapsed_ms": run.get("elapsed_ms"),
        "error": run.get("error") or "",
    } for run in runs]
    return {
        "event_ids": event_ids,
        "end_to_end_ms": scalar(end_values),
        "end_to_end_ms_values": end_values,
        "context_build_ms": scalar(context_values),
        "context_build_ms_values": context_values,
        "context_package_context_build_ms": package_context,
        "context_package_search_ms": [row.get("search_ms") for row in packages],
        "sql_elapsed_ms": elapsed,
        "llm_calls": sum(llm_values) if llm_values else None,
        "llm_calls_values": llm_values,
        "route": route,
        "recipes": recipes,
        "stored_dates": unique(dates),
        "clarification_requested": any(bool(body.get("clarification_requested")) for body in bodies),
        "referenced_versions": [row.get("referenced_versions") for row in packages],
        "jobs": [{"job_kind": row.get("job_kind"), "job_status": row.get("job_status")} for row in found["jobs"]],
        "awaiting": found.get("awaiting") or {},
    }


def manager_move(transcript, turn, max_turns, settled, must_speak=False):
    payload = {
        "conversation_so_far": [{"you": item["manager"], "leon": item["leon"]} for item in transcript],
        "leon_has_stated_one_occupancy_figure_for_one_day": settled,
        "turn": turn,
        "max_turns": max_turns,
    }
    if must_speak:
        payload["required"] = (
            "Leon has not stated one occupancy figure for one day. "
            "status must be continue and message must be the next thing you type."
        )
    instructions = MANAGER_INSTRUCTIONS.replace("{turn}", str(turn)).replace("{max_turns}", str(max_turns))
    return agents._structured("simulator", MANAGER_SCHEMA, instructions, json.dumps(payload, ensure_ascii=False))


def continuing(move):
    return move.get("status") == "continue" and str(move.get("message") or "").strip()


def read_reply(transcript, reply, today):
    content = json.dumps({
        "today": str(today),
        "conversation_before_this_reply": [{"manager": item["manager"], "leon": item["leon"]} for item in transcript],
        "assistant_reply": reply,
    }, ensure_ascii=False)
    return agents._structured("judge", READER_SCHEMA, READER_INSTRUCTIONS.replace("{today}", str(today)), content)


def named_figures(reading):
    named = []
    for item in reading.get("figures") or []:
        day = iso_day(item.get("day"))
        number = item.get("number")
        if number is not None and day:
            named.append({"day": day, "number": float(number)})
    return named


def one_settled(reading):
    named = named_figures(reading)
    return len(named) == 1 and not reading.get("asked_clarification")


def compare_figure(hotel_id, reading, server, recipe_for):
    named = named_figures(reading)
    stored = unique(iso_day(value) for value in server.get("stored_dates") or [])
    if reading.get("asked_clarification") and len(named) != 1:
        return {"scored": False, "note": "clarification; not scored as a number", "stored_dates": server.get("stored_dates") or []}
    if len(named) != 1:
        return {
            "scored": False,
            "spoken_figures": reading.get("figures") or [],
            "stored_dates": server.get("stored_dates") or [],
            "note": "Leon did not state one occupancy figure for one settled day",
        }
    spoken = named[0]
    day = stored[0] if len(stored) == 1 else spoken["day"]
    truth = truth_for(hotel_id, day, recipe_for)
    figure = truth["recomputed_occupancy_pct_active"]
    match = figure is not None and abs(spoken["number"] - figure) < 0.05
    return {
        "scored": True,
        "spoken_number": spoken["number"],
        "spoken_day": spoken["day"],
        "stored_dates": server.get("stored_dates") or [],
        "day_leon_used": day,
        "compared_with": "recomputed_occupancy_pct_active",
        "ground_truth": truth,
        "match": match,
    }


def blank_reading():
    return {"asked_clarification": False, "one_settled_day": False, "figures": [], "reader_error": ""}


def empty_server():
    return {
        "end_to_end_ms": None, "context_build_ms": None, "sql_elapsed_ms": [], "llm_calls": None,
        "route": "", "recipes": [], "stored_dates": [], "clarification_requested": False,
        "end_to_end_ms_values": [], "context_build_ms_values": [], "llm_calls_values": [],
        "jobs": [], "awaiting": {}, "referenced_versions": [], "context_package_search_ms": [],
        "context_package_context_build_ms": [], "event_ids": [], "snapshot_error": "",
    }


def quoted_pieces(text):
    found = []
    rest = str(text or "")
    while "'" in rest:
        start = rest.index("'") + 1
        end = rest.find("'", start)
        if end < 0:
            break
        found.append(rest[start:end])
        rest = rest[end + 1:]
    return found


def watch_queries(stop, found):
    """Statements visible on the server while this turn is in flight. A fast statement can finish between samples."""
    try:
        from booking_system import db
        conn = db.connect(autocommit=True)
    except Exception as exc:
        found.append("watch error: " + str(exc).splitlines()[0][:200])
        return
    try:
        cur = conn.cursor()
        while not stop.is_set():
            cur.execute(
                "SELECT left(query, 4000) FROM pg_stat_activity WHERE state = 'active' AND pid <> pg_backend_pid()"
            )
            for row in cur.fetchall():
                text = " ".join(str(row[0] or "").split())
                if text and "pg_stat_activity" not in text and text not in found:
                    found.append(text)
            stop.wait(0.05)
    except Exception as exc:
        found.append("watch error: " + str(exc).splitlines()[0][:200])
    finally:
        conn.close()


def tools_from(server, samples):
    """Tool evidence this desk left. Sampling records a tool only when its statement was visible."""
    recipes = server.get("recipes") or []
    get_recipe = True if any("knowledge_index" in text or "catalog_active_versions" in text for text in samples if isinstance(text, str)) else None
    run_sql = True if recipes else None
    recipe_id = ""
    row_count = None
    if recipes:
        recipe_id = recipes[0].get("catalog_id") or ""
        row_count = recipes[0].get("row_count")
        run_sql = True
    dates = list(server.get("stored_dates") or [])
    visible = []
    for text in samples:
        if not isinstance(text, str) or text.startswith("watch error"):
            continue
        visible.append(text[:2000])
        dates.extend(dates_in_sql(text))
        for piece in quoted_pieces(text):
            day = iso_day(piece)
            if day:
                dates.append(day)
        if "booking_room_night" in text or "%(begin)s" in text or "to_date(" in text:
            run_sql = True
        if "occupancy_pct_active" in text:
            recipe_id = recipe_id or RECIPE_ID
    return {
        "get_recipe": get_recipe,
        "run_sql": run_sql,
        "recipe_id": recipe_id or None,
        "row_count": row_count,
        "dates": unique(dates),
        "note": "This desk does not store tool calls. get_recipe and run_sql are true only when a statement was visible in pg_stat_activity or an execution row was written.",
        "samples": visible,
    }


def play(desk, hotel, recipe_for, transcript, message, answers_leon):
    before = records.usage(hotel["hotel_id"])
    seen = []
    stop = threading.Event()
    watcher = threading.Thread(target=watch_queries, args=(stop, seen), daemon=True)
    watcher.start()
    try:
        sent = desk.send(message)
        error = ""
    except Exception as exc:
        sent = {"status": 0, "reply": "", "ttfb_ms": None, "wall_ms": None}
        error = str(exc).splitlines()[0][:300]
    finally:
        stop.set()
        watcher.join(timeout=2)
    after = records.usage(hotel["hotel_id"])
    server = empty_server()
    if not error:
        try:
            server = turn_server(hotel["hotel_id"], hotel["user_id"], message, before["now"], after["now"])
        except Exception as exc:
            server["snapshot_error"] = str(exc).splitlines()[0][:300]
    reply = str(sent.get("reply") or "")
    reading = blank_reading()
    if reply.strip():
        try:
            reading = read_reply(transcript, reply, hotel["today"])
        except Exception as exc:
            reading["reader_error"] = str(exc).splitlines()[0][:300]
    settled = one_settled(reading)
    return {
        "manager": message,
        "leon": reply,
        "manager_answers_clarification": bool(answers_leon),
        "leon_asked_clarification": bool(reading.get("asked_clarification")),
        "one_settled_day": settled,
        "client": {"status": sent.get("status"), "wall_ms": sent.get("wall_ms"), "ttfb_ms": sent.get("ttfb_ms"), "error": error},
        "server": server,
        "reader": reading,
        "tools": tools_from(server, list(seen)),
        "comparison": compare_figure(hotel["hotel_id"], reading, server, recipe_for),
    }


def clarification_outcome(turns):
    pending = False
    answered = False
    asked = False
    for turn in turns:
        if pending and turn["manager_answers_clarification"]:
            answered = True
            pending = False
        if turn["leon_asked_clarification"]:
            asked = True
            pending = True
    return {"leon_clarified": asked, "manager_answered": answered, "clarification_still_open": pending}


def figure_outcome(turns):
    for turn in turns:
        comparison = turn["comparison"]
        if comparison.get("scored"):
            return comparison
    return {"scored": False, "note": "Leon did not state one occupancy figure for one settled day"}


def recipe_line(server):
    recipes = server.get("recipes") or []
    if not recipes:
        return "none", None, None
    first = recipes[0]
    return first.get("catalog_id") or "", first.get("version"), first.get("row_count")


def render(report):
    lines = [
        "occupancy yesterday, one thread, same desk session",
        "server " + str(report.get("base_url")) + " health " + str(report.get("health_status"))
        + " listener pid " + ", ".join(str(pid) for pid in report.get("listener_pids") or [])
        + " expected " + str(report.get("expected_listener_pid")),
        "simulator " + str(report.get("simulator")) + " cap " + str(report.get("max_turns")) + " ending " + str(report.get("ending")),
        "signed in as " + str((report.get("manager") or {}).get("manager_name") or "") + " at " + str((report.get("manager") or {}).get("hotel_name") or ""),
        "clock at start " + json.dumps(report.get("clock_start"), default=str),
        "ground truth " + json.dumps(report.get("ground_truth"), default=str),
        "clock at end " + json.dumps(report.get("clock_end"), default=str),
        "leon clarified " + str(report.get("leon_clarified")),
        "manager answered the clarification " + str(report.get("manager_answered")),
        "clarification still open " + str(report.get("clarification_still_open")),
        "figure versus ground truth " + json.dumps(report.get("figure"), default=str),
        "",
    ]
    for index, turn in enumerate(report.get("turns") or [], start=1):
        server = turn["server"]
        recipe_id, version, row_count = recipe_line(server)
        tools = turn.get("tools") or {}
        lines.extend([
            "turn " + str(index),
            "  manager: " + turn["manager"],
            "  leon: " + turn["leon"],
            "  leon_asked_clarification: " + str(turn["leon_asked_clarification"]),
            "  manager_answers_clarification: " + str(turn["manager_answers_clarification"]),
            "  client wall_ms " + str(turn["client"]["wall_ms"]) + " ttfb_ms " + str(turn["client"]["ttfb_ms"])
            + " http " + str(turn["client"]["status"]),
            "  tools get_recipe " + str(tools.get("get_recipe"))
            + " run_sql " + str(tools.get("run_sql"))
            + " recipe " + str(tools.get("recipe_id"))
            + " row_count " + str(tools.get("row_count"))
            + " dates " + json.dumps(tools.get("dates")),
            "  server end_to_end_ms " + str(server.get("end_to_end_ms"))
            + " context_build_ms " + str(server.get("context_build_ms"))
            + " sql_elapsed_ms " + json.dumps(server.get("sql_elapsed_ms"))
            + " llm_calls " + str(server.get("llm_calls"))
            + " route " + str(server.get("route"))
            + " execution_recipe " + str(recipe_id) + " version " + str(version)
            + " execution_row_count " + str(row_count)
            + " stored_date " + json.dumps(server.get("stored_dates")),
            "  comparison " + json.dumps(turn["comparison"], default=str),
            "",
        ])
        if turn["client"].get("error"):
            lines.append("  client error: " + turn["client"]["error"])
    if report.get("stop_note"):
        lines.append(report["stop_note"])
    lines.append("harness usage " + json.dumps(report.get("harness_usage"), default=str))
    return "\n".join(lines) + "\n"


def write_report(paths, report):
    text_path, json_path = paths
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    text_path.write_text(render(report), encoding="utf-8")


def health_status(base):
    import httpx
    try:
        response = httpx.get(base.rstrip("/") + "/health", timeout=10)
        return response.status_code, ""
    except Exception as exc:
        return 0, str(exc).splitlines()[0][:300]


def main():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--expected-pid", type=int, default=21560)
    parser.add_argument("--max-turns", type=int, default=5)
    parser.add_argument("--stem", default=STEM)
    args = parser.parse_args()
    paths = output_paths(ROOT, args.stem)
    port = urlparse(args.base_url).port or 80
    status, health_error = health_status(args.base_url)
    pids = listener_pids(port)
    report = {
        "base_url": args.base_url,
        "health_status": status,
        "health_error": health_error,
        "listener_pids": pids,
        "expected_listener_pid": args.expected_pid,
        "listener_processes": process_names(pids) if status != 200 else [],
        "simulator": agents.MODEL + "/" + agents.EFFORT,
        "max_turns": args.max_turns,
        "ending": "",
        "turns": [],
        "leon_clarified": False,
        "manager_answered": False,
        "clarification_still_open": False,
        "figure": {},
        "ground_truth": {},
        "clock_start": {},
        "clock_end": {},
        "manager": {},
        "harness_usage": {},
        "stop_note": "",
        "result_txt": str(paths[0]),
        "result_json": str(paths[1]),
    }
    if status != 200:
        report["ending"] = "health_not_200"
        report["stop_note"] = "Stopped before any desk turn. Port " + str(port) + " processes: " + json.dumps(report["listener_processes"])
        write_report(paths, report)
        _say(report["stop_note"])
        return 1
    hotel = records.sandbox()
    started_clock = clock(hotel["hotel_id"])
    report["clock_start"] = started_clock
    hotel["today"] = str(started_clock.get("today") or hotel.get("today"))
    published = active_recipe()
    body = published.get("body") or {}
    yesterday = str(started_clock.get("yesterday") or "")
    recipe_for = recipe_lookup(hotel["hotel_id"], body)
    opened = recipe_for(yesterday) if yesterday else {}
    yesterday_truth = truth_for(hotel["hotel_id"], yesterday, recipe_for) if yesterday else {}
    report["ground_truth"] = {
        "recipe": RECIPE_ID,
        "active_version": published.get("version"),
        "parameters": bound_for(body, hotel["hotel_id"], yesterday) if yesterday else {},
        "recipe_sql_error": opened.get("error") or "",
        "recipe_row_count": opened.get("row_count"),
        "yesterday": yesterday_truth,
        "recipe_matches_recompute": (
            yesterday_truth.get("recipe_occupancy_pct_active") is not None
            and yesterday_truth.get("recomputed_occupancy_pct_active") is not None
            and abs(yesterday_truth["recipe_occupancy_pct_active"] - yesterday_truth["recomputed_occupancy_pct_active"]) < 0.05
        ),
    }
    desk = Desk(args.base_url)
    me = desk.sign_in()
    report["manager"] = {"manager_name": me.get("manager_name"), "hotel_name": me.get("hotel_name"), "role": me.get("role")}
    if me.get("hotel_id") != hotel["hotel_id"] or me.get("user_id") != hotel["user_id"]:
        report["ending"] = "wrong_session"
        report["stop_note"] = "Signed-in hotel or user did not match SANDBOX HOTEL. No occupancy question was sent."
        write_report(paths, report)
        _say(report["stop_note"])
        return 1
    cleared = desk.clear()
    if not cleared.get("success"):
        report["ending"] = "clear_failed"
        report["stop_note"] = "Chat clear did not succeed. No occupancy question was sent."
        write_report(paths, report)
        _say(report["stop_note"])
        return 1
    transcript = []
    ending = "max_turns"
    for number in range(1, args.max_turns + 1):
        try:
            move = manager_move(transcript, number, args.max_turns, False)
            if not continuing(move):
                move = manager_move(transcript, number, args.max_turns, False, must_speak=True)
        except Exception as exc:
            ending = "simulator_error"
            report["stop_note"] = str(exc).splitlines()[0][:300]
            break
        if not continuing(move):
            ending = "manager_stopped"
            report["stop_note"] = "The manager returned no message. reason: " + str(move.get("reason") or "")
            break
        _say("turn", number, "manager:", str(move["message"])[:240])
        turn = play(desk, hotel, recipe_for, transcript, move["message"], move.get("answers_leon"))
        report["turns"].append(turn)
        transcript.append({"manager": move["message"], "leon": turn["leon"]})
        write_report(paths, report)
        _say("turn", number, "leon:", turn["leon"][:240], "wall_ms", turn["client"]["wall_ms"], "llm_calls", turn["server"].get("llm_calls"))
        if turn["client"].get("error"):
            ending = "send_error"
            break
        if turn["one_settled_day"]:
            ending = "one_occupancy_figure"
            break
    else:
        ending = "max_turns"
    outcome = clarification_outcome(report["turns"])
    report.update(outcome)
    report["figure"] = figure_outcome(report["turns"])
    report["ending"] = ending
    report["clock_end"] = clock(hotel["hotel_id"])
    report["harness_usage"] = agents.USAGE
    write_report(paths, report)
    _say("ending", ending, "wrote", paths[0].name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
