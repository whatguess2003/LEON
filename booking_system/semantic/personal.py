"""Personal recipes: a manager's named bundle of published general recipes. Stored on the user's user_profile document; no SQL of its own."""

import json
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value):
    return " ".join(str(value or "").split())


def _key(value):
    return "_".join(str(value or "").lower().split())


def _profile(user_id):
    from booking_system.semantic.definitions import _profile as profile
    return profile(user_id)


def _write(user_id, values):
    from booking_system.semantic.definitions import _write as write
    write(user_id, values)


def published_recipes(user_id):
    """Published recipes this manager may use, with a complete contract: their own user recipes first, then general."""
    from booking_system.semantic.recipe import recipe_contract
    from booking_system.semantic.registry import list_active_catalogs, visible_to

    found = []
    for row in list_active_catalogs():
        body = row.get("body") or {}
        if recipe_contract(body) or not visible_to(row, user_id):
            continue
        found.append({
            "catalog_id": str(row.get("catalog_id") or ""),
            "scope": row["scope"],
            "problem": _text(body.get("problem"))[:300],
            "outputs": [str(item) for item in body.get("outputs") or []],
        })
    return sorted(found, key=lambda item: item["scope"] != "user")


def note_user_recipe(user_id, catalog_id, version):
    """List a published user recipe on its owner's profile."""
    doc = _profile(user_id)
    if not doc:
        return {"error": "user not found"}
    kept = [item for item in doc.get("user_recipes") or [] if isinstance(item, dict) and item.get("catalog_id") != catalog_id]
    kept.append({"catalog_id": str(catalog_id), "version": int(version or 0), "published_at": _now()})
    _write(user_id, {"user_recipes": kept})
    return {"catalog_id": catalog_id, "status": "listed"}


def _usable(item, by_id):
    """A ready figure still points at a visible recipe that returns its columns."""
    recipe = by_id.get(item.get("catalog_id"))
    columns = list(item.get("outputs") or []) + ([item["date_column"]] if item.get("date_column") else [])
    return bool(recipe) and all(column in recipe["outputs"] for column in columns)


def personal_recipes(user_id):
    doc = _profile(user_id) or {}
    return [item for item in doc.get("personal_recipes") or [] if isinstance(item, dict) and _key(item.get("key"))]


def personal_cards(user_id):
    """The short list the spec call chooses from."""
    return [
        {"key": item["key"], "name": item.get("name"), "figures": [figure.get("label") for figure in item.get("items") or []]}
        for item in personal_recipes(user_id)
    ]


def find_personal(user_id, key):
    matched = [item for item in personal_recipes(user_id) if item["key"] == _key(key)]
    return matched[0] if matched else None


def save_personal(user_id, name, items, published_seen=""):
    """Read first. A known key is updated; an unknown key is created."""
    key = _key(name)
    if not key or not items:
        return {"error": "name and figures are required"}
    kept = personal_recipes(user_id)
    entry = {"key": key, "name": _text(name), "items": list(items), "published_seen": published_seen, "updated_at": _now()}
    matched = [index for index, item in enumerate(kept) if item["key"] == key]
    if matched:
        entry["created_at"] = kept[matched[0]].get("created_at") or entry["updated_at"]
        kept[matched[0]] = entry
    else:
        entry["created_at"] = entry["updated_at"]
        kept.append(entry)
    _write(user_id, {"personal_recipes": kept})
    return {"key": key, "status": "updated" if matched else "created", "recipe": entry}


def delete_personal(user_id, key):
    """Read first. Only an existing key is deleted."""
    kept = personal_recipes(user_id)
    remaining = [item for item in kept if item["key"] != _key(key)]
    if len(remaining) == len(kept):
        return {"error": "no personal recipe " + _key(key)}
    _write(user_id, {"personal_recipes": remaining})
    return {"key": _key(key), "status": "deleted"}


def recent_reports(user_id):
    doc = _profile(user_id) or {}
    return [item for item in doc.get("recent_reports") or [] if isinstance(item, dict)]


def declined_reports(user_id):
    doc = _profile(user_id) or {}
    return [item for item in doc.get("declined_reports") or [] if isinstance(item, dict)]


def decline_report(user_id, name, question):
    """The manager said no to saving this bundle; it is not offered again."""
    kept = [item for item in declined_reports(user_id) if item.get("question") != _text(question)]
    kept.append({"name": _text(name), "question": _text(question), "declined_at": _now()})
    _write(user_id, {"declined_reports": kept[-20:]})


def remember_report(user_id, question, figures):
    """Keep the manager's last five multi-figure questions so a repeat can be offered as a saved report."""
    kept = recent_reports(user_id)
    kept.append({"question": _text(question), "figures": list(figures or []), "asked_at": _now()})
    _write(user_id, {"recent_reports": kept[-5:]})


def describe(recipe):
    parts = []
    for item in recipe.get("items") or []:
        day = _item_phrase(item)
        waiting = " (waiting for your definition)" if str(item.get("mapped_with") or "").endswith("|open") else " (waiting for a recipe)"
        pending = waiting if item.get("status") == "pending" else ""
        parts.append(str(item.get("label") or "") + " for " + day + pending)
    return str(recipe.get("name") or "") + ": " + ", ".join(parts)


def _item_phrase(item):
    return " ".join(str(item.get("period") or "").split("_"))


def map_figures(name, request, published, keys=(), definitions=(), dates=None):
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
                        "definition_key": {"type": "string", "enum": [""] + key_names + ["new"]},
                        "new_key": {"type": "string"},
                        "new_label": {"type": "string"},
                        "definition_question": {"type": "string"},
                    },
                    "required": [
                        "label", "catalog_id", "outputs", "date_column",
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
            "label is the figure in two to four words, without any day or date. "
            "Do not choose a day, a period, or an offset. The dates in this turn were already written. "
            "catalog_id is the published recipe whose problem answers that figure, and none when no recipe answers it. "
            "A recipe with scope user is this manager's own; choose it over a general recipe whenever it answers the figure. "
            "outputs are the output columns of that recipe that hold the figure, copied exactly; choose the reading the figure names. "
            "A recipe that returns one row per date answers any day: date_column is its output column that names each row's day. "
            "When the recipe names each row's day or range in one column, such as period, date_column is that column, "
            "never a start or end date column, because a day and a range can share a start date. "
            "A recipe without a date column is not tied to a day. When dates has a resolution whose unresolved is false, use none instead of a recipe that has no date column. "
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
            "dates": dates or {"resolutions": []},
            "published": published,
            "keys": list(keys or []),
            "definitions": list(definitions or []),
        }),
        schema=schema,
        max_tokens=2000,
        timeout=90,
    ))
    from booking_system.semantic.dates import usable_periods
    by_id = {item["catalog_id"]: item for item in published}
    answered = {item["key"] for item in definitions or []}
    periods = usable_periods(dates)
    agent_period = periods[0] if len(periods) == 1 else ""
    items = []
    for item in parsed.get("items") or []:
        recipe = by_id.get(str(item.get("catalog_id") or ""))
        entry = {"label": _text(item.get("label")), "period": "", "catalog_id": "", "outputs": [], "date_column": "", "status": "pending"}
        chosen_key = str(item.get("definition_key") or "")
        if chosen_key == "new":
            entry["definition"] = {"key": _key(item.get("new_key")), "label": _text(item.get("new_label")), "question": _text(item.get("definition_question"))}
        elif chosen_key:
            entry["definition"] = {"key": chosen_key, "label": "", "question": ""}
        if recipe and _settled(entry, answered):
            outputs = [column for column in item.get("outputs") or [] if column in recipe["outputs"]]
            date_column = str(item.get("date_column") or "")
            dated = date_column in recipe["outputs"]
            if outputs and (dated or not agent_period):
                entry.update({
                    "catalog_id": recipe["catalog_id"],
                    "outputs": outputs,
                    "date_column": date_column if dated else "",
                    "period": "",
                    "status": "ready",
                })
        items.append(entry)
    return items


def learn(user_id, name, request, dates=None):
    """Map a request onto today's published recipes. Returns the items and the published set they were mapped against."""
    from booking_system.semantic.definitions import known_keys, manager_definitions

    published = published_recipes(user_id)
    definitions = manager_definitions(user_id)
    seen = ",".join(sorted(item["catalog_id"] for item in published))
    items = map_figures(name, request, published, known_keys(user_id), definitions, dates)
    answered = {item["key"] for item in definitions}
    return [_marked(item, seen, answered) for item in items], seen


def _marked(item, seen, answered):
    """What a pending figure was last mapped against: the published set and whether its meaning was settled."""
    if item.get("status") != "pending":
        return item
    return dict(item, mapped_with=seen + "|" + ("settled" if _settled(item, answered) else "open"))


def needed_definitions(user_id, recipe):
    """Pending figures whose meaning the manager has not defined yet. A new phrase is opened on the profile first."""
    from booking_system.semantic.definitions import known_keys, manager_definitions, open_request, skipped_keys

    answered = {item["key"] for item in manager_definitions(user_id)}
    declined = set(skipped_keys(user_id))
    stored = {item["key"]: item["question"] for item in known_keys(user_id)}
    needed = []
    for item in recipe.get("items") or []:
        wanted = item.get("definition") or {}
        key = wanted.get("key") or ""
        if item.get("status") != "pending" or not key or key in answered or key in declined:
            continue
        question = wanted.get("question") or stored.get(key) or "What do you count as " + item["label"].lower() + "?"
        if wanted.get("label"):
            opened = open_request(user_id, key, wanted["label"], question)
            if opened.get("error"):
                continue
        needed.append({"key": key, "label": item["label"], "question": question})
    return needed


def _settled(item, answered):
    key = (item.get("definition") or {}).get("key") or ""
    return not key or key in answered


def _promote(user_id, recipe, published):
    """A pending figure is mapped again once the published recipes or its settled meaning changed; it becomes ready when a recipe answers it."""
    from booking_system.semantic.definitions import known_keys, manager_definitions

    definitions = manager_definitions(user_id)
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
        for item in map_figures(recipe.get("name"), request, published, known_keys(user_id), definitions)
    }
    items = []
    for item in recipe.get("items") or []:
        found = mapped.get(item.get("label"))
        if item in stale and found and found["status"] == "ready":
            items.append(dict(found, definition=item.get("definition") or found.get("definition") or {}))
            continue
        items.append(_marked(item, seen, answered) if item in stale else item)
    return save_personal(user_id, recipe.get("name"), items, seen)["recipe"]


def _run_item(hotel_id, item, agent_period):
    """Run the referenced general recipe and keep the rows for the period the desk wrote."""
    from booking_system.semantic.query import accepted_reads, execute_reads, rows_for
    from booking_system.semantic.registry import active_body

    day = str(agent_period or "")
    body = active_body(item["catalog_id"]) or {}
    template = str(((body.get("solution") or {}).get("sql_template")) or "").strip()
    statements = accepted_reads([template]) if template else []
    if not statements:
        return {"figure": item["label"], "day": day, "error": "recipe " + item["catalog_id"] + " has no runnable template"}
    result = execute_reads(statements, {"hotel_id": str(hotel_id or "")}, None)
    if result.get("error"):
        return {"figure": item["label"], "day": day, "error": result["error"]}
    rows = result.get("rows") or []
    column = item.get("date_column") or ""
    if column and not day:
        return {"figure": item["label"], "day": "", "error": "no date was settled for this figure"}
    if column:
        rows = rows_for(rows, {"column": column, "period": day, "day": ""})
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


def _run_live(hotel_id, manager, item, agent_period, definitions, dates=None):
    """A figure with no published recipe is not answered by writing SQL. The catalog is queued when this is not a follow-up."""
    from booking_system.semantic.hotel import hotel_row
    from booking_system.semantic.trace import in_followup, queue_missing_recipe

    day = str(agent_period or "")
    if _gap_blocked(item.get("gap_trace_id")):
        return {"figure": item["label"], "day": day, "error": "the hotel records do not hold this figure yet", "gap_trace_id": item["gap_trace_id"]}
    hotel_name = str((hotel_row(hotel_id) or {}).get("hotel_name") or "")
    started = {}
    if not in_followup():
        started = queue_missing_recipe(hotel_id, hotel_name, manager, str(item.get("label") or ""), dates, {}, {})
    return {
        "figure": item["label"],
        "day": day,
        "values": [],
        "error": "Give me a minute." if started.get("queued") or started.get("attached") else "I could not answer that from a published recipe.",
    }


def run_personal(hotel_id, manager, recipe, question, definitions, dates=None):
    """Every figure in parallel. The reply node writes the sentence."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    from booking_system.semantic.dates import usable_periods

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
    periods = usable_periods(dates)
    agent_period = periods[0] if len(periods) == 1 else ""
    items = list(recipe.get("items") or [])
    with ThreadPoolExecutor(max_workers=max(1, len(items))) as pool:
        futures = [
            pool.submit(_run_item, hotel_id, item, agent_period) if item.get("status") == "ready"
            else pool.submit(_run_live, hotel_id, manager, item, agent_period, definitions, dates)
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
    return {
        "question": str(question or "") + " (" + describe(recipe) + ")",
        "figures": figures,
        "errors": errors,
        "definitions": definitions,
        "info": {
            "personal_key": recipe.get("key"),
            "figures": figures,
            "read_ms": read_ms,
            "live_figures": [item["figure"] for item in figures if item.get("live")],
        },
    }
