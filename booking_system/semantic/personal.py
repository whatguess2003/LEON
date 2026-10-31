"""Personal recipes: a manager's named bundle of published general recipes. Stored on the manager profile; no SQL of its own."""

import json
from datetime import datetime, timedelta, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value):
    return " ".join(str(value or "").split())


def _key(value):
    return "_".join(str(value or "").lower().split())


def _profile(manager_name):
    from booking_system.hotel_context_manager import manager_col
    name = str(manager_name or "").upper()
    if not name or manager_col is None:
        return None
    return manager_col.find_one({"manager_name": name}, {"password": 0})


def _write(manager_name, values):
    from booking_system.hotel_context_manager import manager_col
    manager_col.update_one({"manager_name": str(manager_name or "").upper()}, {"$set": values})


def published_recipes(manager_name):
    """Published recipes this manager may use, with a complete contract: their own user recipes first, then general."""
    from booking_system.semantic.recipe import recipe_contract
    from booking_system.semantic.registry import list_active_catalogs, visible_to

    found = []
    for row in list_active_catalogs():
        body = row.get("body") or {}
        if recipe_contract(body) or not visible_to(row, manager_name):
            continue
        found.append({
            "catalog_id": str(row.get("catalog_id") or ""),
            "scope": row["scope"],
            "problem": _text(body.get("problem"))[:300],
            "outputs": [str(item) for item in body.get("outputs") or []],
        })
    return sorted(found, key=lambda item: item["scope"] != "user")


def note_user_recipe(manager_name, catalog_id, version):
    """List a published user recipe on its owner's profile."""
    doc = _profile(manager_name)
    if not doc:
        return {"error": "manager not found"}
    kept = [item for item in doc.get("user_recipes") or [] if isinstance(item, dict) and item.get("catalog_id") != catalog_id]
    kept.append({"catalog_id": str(catalog_id), "version": int(version or 0), "published_at": _now()})
    _write(manager_name, {"user_recipes": kept})
    return {"catalog_id": catalog_id, "status": "listed"}


def _usable(item, by_id):
    """A ready figure still points at a visible recipe that returns its columns."""
    recipe = by_id.get(item.get("catalog_id"))
    columns = list(item.get("outputs") or []) + ([item["date_column"]] if item.get("date_column") else [])
    return bool(recipe) and all(column in recipe["outputs"] for column in columns)


def personal_recipes(manager_name):
    doc = _profile(manager_name) or {}
    return [item for item in doc.get("personal_recipes") or [] if isinstance(item, dict) and _key(item.get("key"))]


def personal_cards(manager_name):
    """The short list the spec call chooses from."""
    return [
        {"key": item["key"], "name": item.get("name"), "figures": [figure.get("label") for figure in item.get("items") or []]}
        for item in personal_recipes(manager_name)
    ]


def find_personal(manager_name, key):
    matched = [item for item in personal_recipes(manager_name) if item["key"] == _key(key)]
    return matched[0] if matched else None


def save_personal(manager_name, name, items, published_seen=""):
    """Read first. A known key is updated; an unknown key is created."""
    key = _key(name)
    if not key or not items:
        return {"error": "name and figures are required"}
    kept = personal_recipes(manager_name)
    entry = {"key": key, "name": _text(name), "items": list(items), "published_seen": published_seen, "updated_at": _now()}
    matched = [index for index, item in enumerate(kept) if item["key"] == key]
    if matched:
        entry["created_at"] = kept[matched[0]].get("created_at") or entry["updated_at"]
        kept[matched[0]] = entry
    else:
        entry["created_at"] = entry["updated_at"]
        kept.append(entry)
    _write(manager_name, {"personal_recipes": kept})
    return {"key": key, "status": "updated" if matched else "created", "recipe": entry}


def delete_personal(manager_name, key):
    """Read first. Only an existing key is deleted."""
    kept = personal_recipes(manager_name)
    remaining = [item for item in kept if item["key"] != _key(key)]
    if len(remaining) == len(kept):
        return {"error": "no personal recipe " + _key(key)}
    _write(manager_name, {"personal_recipes": remaining})
    return {"key": _key(key), "status": "deleted"}


def recent_reports(manager_name):
    doc = _profile(manager_name) or {}
    return [item for item in doc.get("recent_reports") or [] if isinstance(item, dict)]


def declined_reports(manager_name):
    doc = _profile(manager_name) or {}
    return [item for item in doc.get("declined_reports") or [] if isinstance(item, dict)]


def decline_report(manager_name, name, question):
    """The manager said no to saving this bundle; it is not offered again."""
    kept = [item for item in declined_reports(manager_name) if item.get("question") != _text(question)]
    kept.append({"name": _text(name), "question": _text(question), "declined_at": _now()})
    _write(manager_name, {"declined_reports": kept[-20:]})


def remember_report(manager_name, question, figures):
    """Keep the manager's last five multi-figure questions so a repeat can be offered as a saved report."""
    kept = recent_reports(manager_name)
    kept.append({"question": _text(question), "figures": list(figures or []), "asked_at": _now()})
    _write(manager_name, {"recent_reports": kept[-5:]})


def describe(recipe):
    parts = []
    for item in recipe.get("items") or []:
        day = _item_phrase(item)
        waiting = " (waiting for your definition)" if str(item.get("mapped_with") or "").endswith("|open") else " (waiting for a recipe)"
        pending = waiting if item.get("status") == "pending" else ""
        parts.append(str(item.get("label") or "") + " for " + day + pending)
    return str(recipe.get("name") or "") + ": " + ", ".join(parts)


def _item_phrase(item):
    return " ".join(str(item.get("period") or "").split("_")) or _day_phrase(int(item.get("day_offset") or 0))


def _day_phrase(offset):
    names = {-1: "yesterday", 0: "today", 1: "tomorrow"}
    if offset in names:
        return names[offset]
    return str(abs(offset)) + (" days ago" if offset < 0 else " days ahead")


def map_figures(name, request, published, keys=(), definitions=()):
    """Haiku maps the figures a manager asked for onto published recipes. A figure no recipe answers stays pending,
    with the definition its meaning still needs."""
    from booking_system.llm_utils import haiku_text

    ids = [item["catalog_id"] for item in published]
    key_names = [item["key"] for item in keys or []]
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "catalog_id": {"type": "string", "enum": ids + ["none"]},
                        "outputs": {"type": "array", "items": {"type": "string"}},
                        "date_column": {"type": "string"},
                        "day_offset": {"type": "integer"},
                        "period": {"type": "string"},
                        "definition_key": {"type": "string", "enum": [""] + key_names + ["new"]},
                        "new_key": {"type": "string"},
                        "new_label": {"type": "string"},
                        "definition_question": {"type": "string"},
                    },
                    "required": [
                        "label", "catalog_id", "outputs", "date_column", "day_offset", "period",
                        "definition_key", "new_key", "new_label", "definition_question",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    parsed = json.loads(haiku_text(
        (
            "The manager names a personal report and the figures it holds. Return one item per figure. "
            "label is the figure in two to four words, without any day or date, because day_offset carries the day. "
            "day_offset is the day the figure is for, relative to the hotel's today: -1 for yesterday, 0 for today, 1 for tomorrow. "
            "Take the day from the report name or the request; never a calendar date. "
            "catalog_id is the published recipe whose problem answers that figure, and none when no recipe answers it. "
            "A recipe with scope user is this manager's own; choose it over a general recipe whenever it answers the figure. "
            "outputs are the output columns of that recipe that hold the figure, copied exactly; choose the reading the figure names. "
            "A recipe that returns one row per date answers any day: date_column is its output column that names each row's day. "
            "When the recipe names each row's day or range in one column, such as period, date_column is that column, "
            "never a start or end date column, because a day and a range can share a start date. "
            "period is the range the figure covers when it is a named range that recipe returns, copied exactly as the recipe's "
            "preconditions or limitations name it, such as last_week; empty when the figure is for one day. "
            "A recipe without a date column answers only today; do not use it for another day, use none instead. "
            "With catalog_id none, leave outputs and date_column empty. "
            "definition_key is the phrase whose meaning the figure needs, chosen from keys, for any figure with more than one reading "
            "that changes the number, including a recipe that offers several output columns for it. "
            "When no key fits, definition_key is new, new_key is a short snake_case name, new_label is the figure in two or three words, "
            "and definition_question asks in one short spoken sentence what the manager counts as that figure, offering the likely readings. "
            "When definitions hold that key, choose the outputs that match the manager's definition. "
            "Leave definition_key empty only when the figure has one plain reading."
        ),
        json.dumps({
            "report": _text(name),
            "request": _text(request),
            "published": published,
            "keys": list(keys or []),
            "definitions": list(definitions or []),
        }),
        schema=schema,
        max_tokens=2000,
        timeout=90,
    ))
    by_id = {item["catalog_id"]: item for item in published}
    answered = {item["key"] for item in definitions or []}
    items = []
    for item in parsed.get("items") or []:
        recipe = by_id.get(str(item.get("catalog_id") or ""))
        offset = int(item.get("day_offset") or 0)
        entry = {"label": _text(item.get("label")), "day_offset": offset, "period": "", "catalog_id": "", "outputs": [], "date_column": "", "status": "pending"}
        chosen_key = str(item.get("definition_key") or "")
        if chosen_key == "new":
            entry["definition"] = {"key": _key(item.get("new_key")), "label": _text(item.get("new_label")), "question": _text(item.get("definition_question"))}
        elif chosen_key:
            entry["definition"] = {"key": chosen_key, "label": "", "question": ""}
        if recipe and _settled(entry, answered):
            outputs = [column for column in item.get("outputs") or [] if column in recipe["outputs"]]
            date_column = str(item.get("date_column") or "")
            dated = date_column in recipe["outputs"]
            if outputs and (dated or offset == 0):
                entry.update({
                    "catalog_id": recipe["catalog_id"],
                    "outputs": outputs,
                    "date_column": date_column if dated else "",
                    "period": _text(item.get("period")) if dated else "",
                    "status": "ready",
                })
        items.append(entry)
    return items


def learn(manager_name, name, request):
    """Map a request onto today's published recipes. Returns the items and the published set they were mapped against."""
    from booking_system.semantic.definitions import known_keys, manager_definitions

    published = published_recipes(manager_name)
    definitions = manager_definitions(manager_name)
    seen = ",".join(sorted(item["catalog_id"] for item in published))
    items = map_figures(name, request, published, known_keys(manager_name), definitions)
    answered = {item["key"] for item in definitions}
    return [_marked(item, seen, answered) for item in items], seen


def _marked(item, seen, answered):
    """What a pending figure was last mapped against: the published set and whether its meaning was settled."""
    if item.get("status") != "pending":
        return item
    return dict(item, mapped_with=seen + "|" + ("settled" if _settled(item, answered) else "open"))


def needed_definitions(manager_name, recipe):
    """Pending figures whose meaning the manager has not defined yet. A new phrase is opened on the profile first."""
    from booking_system.semantic.definitions import known_keys, manager_definitions, open_request, skipped_keys

    answered = {item["key"] for item in manager_definitions(manager_name)}
    declined = set(skipped_keys(manager_name))
    stored = {item["key"]: item["question"] for item in known_keys(manager_name)}
    needed = []
    for item in recipe.get("items") or []:
        wanted = item.get("definition") or {}
        key = wanted.get("key") or ""
        if item.get("status") != "pending" or not key or key in answered or key in declined:
            continue
        question = wanted.get("question") or stored.get(key) or "What do you count as " + item["label"].lower() + "?"
        if wanted.get("label"):
            opened = open_request(manager_name, key, wanted["label"], question)
            if opened.get("error"):
                continue
        needed.append({"key": key, "label": item["label"], "question": question})
    return needed


def _settled(item, answered):
    key = (item.get("definition") or {}).get("key") or ""
    return not key or key in answered


def _promote(manager_name, recipe, published):
    """A pending figure is mapped again once the published recipes or its settled meaning changed; it becomes ready when a recipe answers it."""
    from booking_system.semantic.definitions import known_keys, manager_definitions

    definitions = manager_definitions(manager_name)
    answered = {item["key"] for item in definitions}
    seen = ",".join(sorted(item["catalog_id"] for item in published))
    stale = [
        item for item in recipe.get("items") or []
        if item.get("status") == "pending" and _marked(item, seen, answered)["mapped_with"] != item.get("mapped_with")
    ]
    if not stale:
        return recipe
    request = "; ".join(
        item["label"] + " for " + _item_phrase(item)
        + "".join(" (" + entry["definition"] + ")" for entry in definitions if entry["key"] == (item.get("definition") or {}).get("key"))
        for item in stale
    )
    mapped = {
        item["label"]: item
        for item in map_figures(recipe.get("name"), request, published, known_keys(manager_name), definitions)
    }
    items = []
    for item in recipe.get("items") or []:
        found = mapped.get(item.get("label"))
        if item in stale and found and found["status"] == "ready":
            items.append(dict(found, definition=item.get("definition") or found.get("definition") or {}))
            continue
        items.append(_marked(item, seen, answered) if item in stale else item)
    return save_personal(manager_name, recipe.get("name"), items, seen)["recipe"]


def _run_item(hotel_name, item, today):
    """Run the referenced general recipe and keep the rows for the item's day and the chosen columns."""
    from booking_system.semantic.query import accepted_reads, execute_reads, rows_for
    from booking_system.semantic.registry import active_body

    day = item.get("period") or (today + timedelta(days=int(item.get("day_offset") or 0))).isoformat()
    body = active_body(item["catalog_id"]) or {}
    template = str(((body.get("solution") or {}).get("sql_template")) or "").strip()
    statements = accepted_reads([template]) if template else []
    if not statements:
        return {"figure": item["label"], "day": day, "error": "recipe " + item["catalog_id"] + " has no runnable template"}
    result = execute_reads(statements, {"hotel_name": str(hotel_name or "").upper()}, None)
    if result.get("error"):
        return {"figure": item["label"], "day": day, "error": result["error"]}
    rows = result.get("rows") or []
    column = item.get("date_column") or ""
    if column:
        rows = rows_for(rows, {"column": column, "period": item.get("period") or "", "day": day})
    values = [{name: row.get(name) for name in item["outputs"]} for row in rows]
    return {"figure": item["label"], "day": day, "recipe": item["catalog_id"], "values": values}


def _gap_blocked(trace_id):
    """The investigation for this figure's gap, when it is blocked because the hotel data does not hold the figure."""
    from booking_system.content_store import _with

    if not trace_id:
        return ""

    def run(cur):
        cur.execute(
            """
            SELECT next_action FROM investigations
            WHERE (trace_id = %s OR evidence_refs @> %s::jsonb) AND status = 'blocked' AND next_action = 'schema_change'
            ORDER BY investigation_id DESC LIMIT 1
            """,
            (int(trace_id), json.dumps([int(trace_id)])),
        )
        row = cur.fetchone()
        return str(row[0]) if row else ""

    return _with(run)


def _run_live(hotel_name, manager, item, today, definitions):
    """A pending figure is answered live and the gap is queued for the catalog.
    A figure the hotel data cannot hold is reported as not recorded, without another live attempt."""
    from booking_system.semantic.sql_agent import answer_question
    from booking_system.semantic.workflow import _queue_catalog

    offset = int(item.get("day_offset") or 0)
    day = (today + timedelta(days=offset)).isoformat()
    if _gap_blocked(item.get("gap_trace_id")):
        return {"figure": item["label"], "day": day, "error": "the hotel records do not hold this figure yet", "gap_trace_id": item["gap_trace_id"]}
    found = answer_question(hotel_name, item["label"] + " for " + _item_phrase(item), [], None, None, definitions)
    gap_trace_id = item.get("gap_trace_id")
    if _settled(item, {entry["key"] for entry in definitions or []}):
        succeeded = bool(found.get("statements")) and not found.get("error") and not found.get("stall_reason")
        package = {"page_ids": [], "referenced_versions": [], "retrieval": {"status": "missing_recipe"}, "shortlist": [], "manager": manager}
        spec = {
            "output_grain": "night",
            "entity": item["label"],
            "constraints": [{"fact": item["label"] + " per hotel-local date"}],
            "learn": "recurring",
            "capability": _key(item["label"]) + "_per_day",
        }
        gap = (
            item["label"] + " for each hotel-local date. A figure in a manager's daily report, read for " + _day_phrase(offset)
            + ". Return one row per hotel-local date over a rolling window, with the date as a column, so any relative day can be read."
        )
        gap_trace_id = _queue_catalog(hotel_name, gap, package, found, "knowledge_gap_detected" if succeeded else "live_answer_failed", spec)
    return {
        "figure": item["label"],
        "day": day,
        "live": True,
        "gap_trace_id": gap_trace_id,
        "values": list(found.get("rows") or [])[:5],
        "error": str(found.get("error") or found.get("stall_reason") or ""),
    }


def run_personal(hotel_name, manager, recipe, question, definitions):
    """Every figure in parallel, then one desk sentence."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    from booking_system.leon_turn import compile_desk_sentence
    from booking_system.timezone_utils import hotel_local_today

    started = time.monotonic()
    published = published_recipes(manager)
    by_id = {item["catalog_id"]: item for item in published}
    checked = [
        item if item.get("status") != "ready" or _usable(item, by_id)
        else dict(item, status="pending", catalog_id="", outputs=[], date_column="", mapped_with="")
        for item in recipe.get("items") or []
    ]
    if checked != list(recipe.get("items") or []):
        recipe = save_personal(manager, recipe.get("name"), checked, recipe.get("published_seen") or "")["recipe"]
    recipe = _promote(manager, recipe, published)
    definitions = list(definitions or [])
    today = hotel_local_today(hotel_name)
    items = list(recipe.get("items") or [])
    with ThreadPoolExecutor(max_workers=max(1, len(items))) as pool:
        futures = [
            pool.submit(_run_item, hotel_name, item, today) if item.get("status") == "ready"
            else pool.submit(_run_live, hotel_name, manager, item, today, definitions)
            for item in items
        ]
        figures = [future.result() for future in futures]
    read_ms = int((time.monotonic() - started) * 1000)
    traced = [
        dict(item, gap_trace_id=figure["gap_trace_id"]) if figure.get("gap_trace_id") and figure["gap_trace_id"] != item.get("gap_trace_id") else item
        for item, figure in zip(items, figures)
    ]
    if traced != items:
        recipe = save_personal(manager, recipe.get("name"), traced, recipe.get("published_seen") or "")["recipe"]
    errors = "; ".join(item["figure"] + ": " + item["error"] for item in figures if item.get("error"))
    sentence = compile_desk_sentence(
        str(question or "") + " (" + describe(recipe) + ")",
        figures,
        errors,
        "",
        definitions,
    )
    return sentence, {
        "personal_key": recipe.get("key"),
        "figures": figures,
        "read_ms": read_ms,
        "live_figures": [item["figure"] for item in figures if item.get("live")],
    }
