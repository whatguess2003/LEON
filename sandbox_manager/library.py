"""Recipe-library writer.

The only write path is booking_system.semantic.seed_general._publish. That function
inserts one catalog version (version 1 when the id is new, or the next version of an
existing id) and indexes that catalog alone through refresh_page_index.

It does not start the desk, call initialize_catalog, reseed, rebuild_all_knowledge_indexes,
or the developer agent. Operational hotel tables are not in this path.

Writes stay closed unless this process opened them for a recipe Sol judged flawed.
A dry run never opens them.
"""

_OPEN = []
_PARAMETERS = ["hotel_id", "start", "end"]


def open_library():
    _OPEN.append(True)


def close_library():
    _OPEN.clear()


def library_open():
    return bool(_OPEN)


def _parent(catalog_id):
    catalog_id = str(catalog_id or "").strip()
    if not catalog_id:
        return "B01"
    from booking_system.content_store import _with

    def run(cur):
        cur.execute("SELECT parent_id FROM catalogs WHERE catalog_id = %s", (catalog_id,))
        row = cur.fetchone()
        return str(row[0] or "") if row else ""

    try:
        found = _with(run)
    except Exception:
        return "B01"
    return found or "B01"


def _catalog_id(value):
    text = str(value or "").strip()
    if not text or len(text) > 64:
        return ""
    for char in text:
        if not (char.isalnum() or char == "_"):
            return ""
    return text


def _texts(value):
    found = []
    for item in value or []:
        text = str(item or "").strip()
        if text and text not in found:
            found.append(text)
    return found


def _placeholder_end(text, index):
    cursor = index + 1
    if cursor >= len(text) or text[cursor] != "(":
        return 0
    cursor += 1
    start = cursor
    while cursor < len(text) and (text[cursor].isalnum() or text[cursor] == "_"):
        cursor += 1
    if cursor == start or not text.startswith(")s", cursor):
        return 0
    return cursor + 2


def _bare_percent(text):
    index = 0
    size = len(text)
    while index < size:
        if text[index] != "%":
            index += 1
            continue
        if text.startswith("%%", index):
            index += 2
            continue
        end = _placeholder_end(text, index)
        if end:
            index = end
            continue
        return True
    return False


def _template_block(sql):
    """Why this template cannot be stored. Empty when it meets the recipe contract."""
    from booking_system.semantic.query import read_rejection

    text = str(sql or "").strip()
    reason = read_rejection([text])
    if reason:
        return reason
    compact = "".join(text.lower().split())
    required = (
        ("%(hotel_id)s", "the read must include %(hotel_id)s"),
        ("%(start)s", "the read must bind %(start)s"),
        ("%(end)s", "the read must bind %(end)s"),
        ("to_date(%(start)s,'mm/dd/yyyy')", "read start with to_date(%(start)s, 'MM/DD/YYYY')"),
        ("to_date(%(end)s,'mm/dd/yyyy')", "read end with to_date(%(end)s, 'MM/DD/YYYY')"),
    )
    for token, message in required:
        if token not in compact:
            return message
    for token in ("interval'7day", "interval'7days", "interval'30day", "interval'30days"):
        if token in compact:
            return "the period is the bound start through end, not a fixed 7-day or 30-day window"
    if _bare_percent(text):
        return "a literal percent in the template is written %%"
    return ""


def _assemble(catalog_id, arguments, current, parent):
    current = dict(current or {})
    arguments = arguments if isinstance(arguments, dict) else {}
    problem = str(arguments.get("problem") or current.get("problem") or "").strip()
    title = str(arguments.get("title") or current.get("title") or problem).strip()
    phrases = _texts(arguments.get("keyphrases") or current.get("keyphrases") or [])
    if problem and problem not in phrases:
        phrases = [problem] + phrases
    outputs = [item.lower() for item in _texts(arguments.get("outputs") or current.get("outputs") or [])]
    grain = str(arguments.get("output_grain") or current.get("output_grain") or "").strip()
    preconditions = _texts(arguments.get("preconditions") or [])
    if not preconditions:
        preconditions = [
            "Only the session hotel is bound as %(hotel_id)s. "
            "start and end are MM/DD/YYYY and are read with to_date(that string, 'MM/DD/YYYY'). "
            "A single day uses the same start and end. The read returns the span from start through end inclusive."
        ]
    verification = str(arguments.get("verification") or "").strip()
    if not verification:
        verification = "Checked by the sandbox manager for the bound hotel, start, and end."
    solution = current.get("solution") if isinstance(current.get("solution"), dict) else {}
    sql = str(arguments.get("sql_template") or "").strip() or str(solution.get("sql_template") or "").strip()
    parent_id = str(current.get("parent") or parent or "B01").strip() or "B01"
    purpose = problem or title or catalog_id
    body = dict(current)
    body.update({
        "catalog_id": catalog_id,
        "parent": parent_id,
        "title": title or purpose,
        "purpose": purpose,
        "problem": problem,
        "preconditions": preconditions,
        "inputs": {
            "hotel_id": "bound by the platform from the session hotel",
            "start": "MM/DD/YYYY inclusive start, bound as %(start)s and read with to_date(start, 'MM/DD/YYYY')",
            "end": "MM/DD/YYYY inclusive end, bound as %(end)s and read with to_date(end, 'MM/DD/YYYY')",
        },
        "output_grain": grain,
        "outputs": outputs,
        "keyphrases": phrases,
        "aliases": list(phrases),
        "verification": verification,
        "solution": {
            "sql_template": sql,
            "parameters": list(_PARAMETERS),
            "method": "sandbox_manager",
        },
        "validation": {"expected_value": ""},
    })
    default = str(current.get("default_output") or "")
    body["default_output"] = default if default in outputs else (outputs[0] if outputs else "")
    return body


def _ready(body):
    from booking_system.semantic.recipe import GRAINS, recipe_contract

    missing = recipe_contract(body)
    if missing:
        return "recipe contract " + " ".join(missing)
    if str(body.get("output_grain") or "") not in GRAINS:
        return "output_grain is not a recipe grain"
    names = list(((body.get("solution") or {}).get("parameters")) or [])
    if names != _PARAMETERS:
        return "parameters are hotel_id, start, and end"
    return _template_block(str(((body.get("solution") or {}).get("sql_template")) or ""))


def _try_read(hotel_id, body, arguments):
    from sandbox_manager.reads import search_sql

    start = str((arguments or {}).get("start") or "").strip()
    end = str((arguments or {}).get("end") or "").strip()
    if not start or not end:
        return "start and end are required"
    result = search_sql(str(((body.get("solution") or {}).get("sql_template")) or ""), start, end, hotel_id)
    if result.get("error"):
        return str(result["error"])
    rows = result.get("rows") or []
    if rows:
        present = {str(name).lower() for name in rows[0]}
        absent = [name for name in body.get("outputs") or [] if name not in present]
        if absent:
            return "outputs not returned by the read: " + " ".join(absent)
    return ""


def _publish_body(body):
    from booking_system.content_store import _transaction
    from booking_system.semantic.seed_general import _publish

    return _transaction(lambda cur: _publish(cur, body, "sandbox_manager"))


def _current(catalog_id):
    from booking_system.semantic.registry import active_body
    try:
        body = active_body(catalog_id) or {}
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


def publish_version(arguments, hotel_id, used_ids):
    """New version of an existing catalog id. Indexes only that recipe."""
    if not library_open():
        return {"published": "no", "error": "recipe writes are closed"}
    arguments = arguments if isinstance(arguments, dict) else {}
    catalog_id = _catalog_id(arguments.get("catalog_id"))
    if not catalog_id:
        return {"error": "catalog_id uses letters, digits, and underscores"}
    if catalog_id not in set(used_ids or []):
        return {"error": "publish a version of a recipe used on this question"}
    current = _current(catalog_id)
    if not current:
        return {"error": "that recipe has no active version; publish a new id when the measure is different"}
    body = _assemble(catalog_id, arguments, current, _parent(catalog_id))
    problem = _ready(body)
    if problem:
        return {"error": problem}
    if str(((current.get("solution") or {}).get("sql_template")) or "").strip() == str(((body.get("solution") or {}).get("sql_template")) or "").strip():
        return {"error": "that template matches the stored recipe"}
    problem = _try_read(hotel_id, body, arguments)
    if problem:
        return {"error": problem}
    result = _publish_body(body)
    published = result.get("status") == "published"
    return {
        "published": "yes" if published else "no",
        "catalog_id": result.get("catalog_id"),
        "version": result.get("version"),
        "previous_version": result.get("previous_version"),
        "status": result.get("status"),
    }


def publish_new(arguments, hotel_id, used_ids):
    """Version 1 of a new catalog id when the measure is different. Indexes only that recipe."""
    if not library_open():
        return {"published": "no", "error": "recipe writes are closed"}
    arguments = arguments if isinstance(arguments, dict) else {}
    catalog_id = _catalog_id(arguments.get("catalog_id"))
    if not catalog_id:
        return {"error": "catalog_id uses letters, digits, and underscores"}
    if catalog_id in set(used_ids or []) or _current(catalog_id):
        return {"error": "that id already exists; publish a version when it is the same measure"}
    source = str(arguments.get("source_catalog_id") or "").strip()
    body = _assemble(catalog_id, arguments, {}, _parent(source))
    problem = _ready(body)
    if problem:
        return {"error": problem}
    problem = _try_read(hotel_id, body, arguments)
    if problem:
        return {"error": problem}
    result = _publish_body(body)
    published = result.get("status") == "published"
    return {
        "published": "yes" if published else "no",
        "catalog_id": result.get("catalog_id"),
        "version": result.get("version"),
        "previous_version": result.get("previous_version"),
        "status": result.get("status"),
    }
