"""A recipe is a complete procedure. A SQL snippet without that contract is not a match."""

GRAINS = ("guest", "reservation", "room", "night", "channel")
OPERATORS = ("filter", "semi_join", "group", "compare", "sort", "project")
_COMPARE = ("gt", "lt", "eq")


def proposal_hash(body):
    """Immutable identity of one proposal body."""
    import hashlib
    import json
    encoded = json.dumps(body or {}, sort_keys=True, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def recipe_contract(body):
    """Names still missing from a procedure. Empty means the body can be executed."""
    body = body or {}
    missing = []
    if not str(body.get("catalog_id") or "").strip():
        missing.append("catalog_id")
    if not str(body.get("problem") or "").strip():
        missing.append("problem")
    if not isinstance(body.get("preconditions"), list) or not body.get("preconditions"):
        missing.append("preconditions")
    solution = body.get("solution") or {}
    declared = solution.get("parameters")
    if declared is None:
        declared = body.get("inputs")
    if not declared:
        missing.append("inputs")
    if str(body.get("output_grain") or "") not in GRAINS:
        missing.append("output_grain")
    outputs = body.get("outputs")
    if not isinstance(outputs, list) or not outputs or not all(isinstance(item, str) and item.strip() for item in outputs):
        missing.append("outputs")
    sql = str(solution.get("sql_template") or "").strip()
    steps = body.get("steps") if isinstance(body.get("steps"), list) else []
    if not sql and not steps:
        missing.append("implementation")
    if not body.get("verification"):
        missing.append("verification")
    return missing


def accept_spec(message, hotel_name, spec, candidate_ids=()):
    """Keep the original question and the session hotel. A day stays in the fact, not a copied date."""
    spec = dict(spec or {})
    allowed = [str(item) for item in candidate_ids or []]
    chosen = []
    for item in spec.get("recipe_ids") or []:
        name = str(item or "")
        if name in allowed and name not in chosen:
            chosen.append(name)
    spec["recipe_ids"] = chosen
    spec["row_filters"] = [
        {
            "catalog_id": str(item.get("catalog_id") or ""),
            "column": str(item.get("column") or ""),
            "day_offset": int(item.get("day_offset") or 0),
            "period": str(item.get("period") or ""),
        }
        for item in spec.get("row_filters") or []
        if isinstance(item, dict) and str(item.get("catalog_id") or "") in chosen
    ]
    spec["route"] = str(spec.get("route") or "")
    spec["learn"] = str(spec.get("learn") or "")
    chosen_capability = str(spec.get("capability") or "")
    if chosen_capability == "new":
        chosen_capability = "_".join(str(spec.get("new_capability") or "").lower().split())
    spec["capability"] = chosen_capability if spec["learn"] == "recurring" else ""
    spec.pop("new_capability", None)
    spec["clarification"] = " ".join(str(spec.get("clarification") or "").split())
    spec["clarification_kind"] = str(spec.get("clarification_kind") or "")
    spec["definition_key"] = str(spec.get("definition_key") or "")
    spec["new_definition"] = {}
    if spec["definition_key"] == "new":
        spec["new_definition"] = {
            "key": "_".join(str(spec.get("new_definition_key") or "").lower().split()),
            "label": " ".join(str(spec.get("new_definition_label") or "").split()),
        }
    spec["personal_key"] = str(spec.get("personal_key") or "")
    spec["report_name"] = " ".join(str(spec.get("report_name") or "").split())
    spec["figures"] = [" ".join(str(item).split()) for item in spec.get("figures") or [] if str(item).strip()]
    spec["repeats_report"] = bool(spec.get("repeats_report"))
    spec["original_question"] = str(message or "")
    spec["scope"] = {"hotel_name": str(hotel_name or "").upper()}
    kept = []
    unresolved = [str(item) for item in (spec.get("unresolved") or []) if str(item)]
    for item in spec.get("constraints") or []:
        if not isinstance(item, dict):
            continue
        kept.append({"fact": str(item.get("fact") or ""), "date": ""})
    spec["constraints"] = kept
    spec["relationships"] = [str(item) for item in (spec.get("relationships") or []) if str(item)]
    spec["output"] = [str(item) for item in (spec.get("output") or []) if str(item)]
    grain = str(spec.get("output_grain") or "")
    spec["output_grain"] = grain if grain in GRAINS else ""
    if not spec["output_grain"]:
        unresolved.append("output_grain")
    spec["unresolved"] = unresolved
    spec["entity"] = str(spec.get("entity") or "")
    return spec


def _candidate_card(page):
    body = page.get("body") or {}
    return {
        "catalog_id": page.get("catalog_id"),
        "scope": page.get("scope"),
        "problem": body.get("problem"),
        "preconditions": body.get("preconditions"),
        "limitations": body.get("limitations"),
        "output_grain": body.get("output_grain"),
        "outputs": body.get("outputs"),
    }


def specify_problem(message, hotel_name, candidates, context):
    """Ask the model for the spec, the route, the published recipes that answer it exactly, and any clarification."""
    import json

    keys = [item["key"] for item in context["keys"]]
    personal_keys = [item["key"] for item in context["personal"]]
    capability_names = [item["name"] for item in context["capabilities"]]
    candidate_ids = [str(page.get("catalog_id") or "") for page in candidates or [] if page.get("catalog_id")]
    recipe_item = {"type": "string", "enum": candidate_ids} if candidate_ids else {"type": "string"}
    schema = {
        "type": "object",
        "properties": {
            "entity": {"type": "string"},
            "output_grain": {"type": "string", "enum": list(GRAINS)},
            "constraints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string"},
                        "date": {"type": "string"},
                    },
                    "required": ["fact", "date"],
                    "additionalProperties": False,
                },
            },
            "relationships": {"type": "array", "items": {"type": "string"}},
            "output": {"type": "array", "items": {"type": "string"}},
            "unresolved": {"type": "array", "items": {"type": "string"}},
            "recipe_ids": {"type": "array", "items": recipe_item},
            "row_filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "catalog_id": recipe_item,
                        "column": {"type": "string"},
                        "day_offset": {"type": "integer"},
                        "period": {"type": "string"},
                    },
                    "required": ["catalog_id", "column", "day_offset", "period"],
                    "additionalProperties": False,
                },
            },
            "route": {"type": "string", "enum": ["personal", "recipe", "clarify", "live", "decline"]},
            "learn": {"type": "string", "enum": ["recurring", "one_off", "not_learnable"]},
            "capability": {"type": "string", "enum": capability_names + ["new"]},
            "new_capability": {"type": "string"},
            "personal_key": {"type": "string", "enum": [""] + personal_keys},
            "clarification": {"type": "string"},
            "clarification_kind": {"type": "string", "enum": ["", "definition", "turn", "report"]},
            "definition_key": {"type": "string", "enum": [""] + keys + ["new"]},
            "new_definition_key": {"type": "string"},
            "new_definition_label": {"type": "string"},
            "report_name": {"type": "string"},
            "figures": {"type": "array", "items": {"type": "string"}},
            "repeats_report": {"type": "boolean"},
        },
        "required": [
            "entity", "output_grain", "constraints", "relationships", "output", "unresolved",
            "recipe_ids", "row_filters", "route", "learn", "capability", "new_capability", "personal_key", "clarification", "clarification_kind",
            "definition_key", "new_definition_key", "new_definition_label",
            "report_name", "figures", "repeats_report",
        ],
        "additionalProperties": False,
    }
    from booking_system.llm_utils import haiku_text
    parsed = json.loads(haiku_text(
        (
            "Choose the route in this order: personal, then a user recipe, then a general recipe, then clarify, then live. "
            "personal are this manager's own saved reports. route is personal when the sentence asks for one of them, "
            "by its name or its meaning; personal_key is that report. Then leave clarification empty. "
            "When the sentence asks for a named report or bundle of figures that is not in personal, "
            "route is clarify, clarification_kind is report, report_name is the name in a few words, keeping its day such as yesterday, "
            "and clarification asks which figures the report should hold, offering a few of the published problems in plain words. "
            "figures lists each separate figure the sentence asks for. "
            "recent are the manager's earlier multi-figure questions. repeats_report is true when the sentence asks for "
            "the same figures as one of them; then report_name is a short name for that bundle. "
            "Describe the problem the sentence asks. "
            "Keep every condition. Do not add a condition the sentence does not state. "
            "output_grain is the unit of one result row. "
            "Leave date empty. A relative day stays in the fact. "
            "A familiar hotel fact, such as who is in house, who arrives, or how many reservations, is a constraint. "
            "Leave unresolved empty unless a required fact has no meaning you can name. "
            "Do not paraphrase the sentence into a shorter question. "
            "candidates are published recipes. recipe_ids lists the candidates whose problem, preconditions, and limitations "
            "cover every condition of the sentence and add no condition the sentence does not state. "
            "A recipe for a different status, day, or population does not fit. "
            "Use one id, or two or three only when their outputs together answer the sentence. "
            "Leave recipe_ids empty when no candidate fits exactly. "
            "Each candidate has a scope: user is this manager's own recipe, general is shared by every hotel. "
            "Prefer a user recipe whenever it covers the sentence, even when a general recipe also fits. "
            "A recipe that returns one row per day or period answers any day: for each such recipe in recipe_ids, "
            "row_filters names the output column that holds the row's day or period, and either day_offset, the requested day "
            "relative to the hotel's today (-1 yesterday, 0 today, 1 tomorrow) with period empty, or period, the named range "
            "the recipe returns for a requested range, such as last_week or this_month, with day_offset 0. "
            "Leave row_filters empty for a recipe that returns only the requested day already. "
            "definitions are what this manager means by a phrase. Apply them in the constraints, "
            "and choose only recipes whose outputs include that reading. "
            "route is recipe when recipe_ids answer the sentence exactly under the definitions. Then answer directly: "
            "leave clarification empty, because the recipe and the definitions already settle the meaning. "
            "When the sentence uses a phrase from keys that has no definition and is not declined, and that phrase has more than one reading "
            "that changes the number, route is clarify with a definition question about that key, even when a recipe fits, "
            "because the recipe cannot tell which of its readings this manager means. "
            "route is clarify when no recipe fits and the sentence is out of context: "
            "a phrase has more than one reading that changes the number and no definition settles it, "
            "or the sentence is missing something it needs, such as which guest or which date, "
            "or it asks for something the hotel records cannot hold. Ask before any data is read. "
            "route is live when no recipe fits and the sentence is clear enough to look up. "
            "With route clarify, clarification is one short spoken question that offers the likely readings. "
            "clarification_kind is definition when the question asks what the manager means by a phrase from now on, "
            "and turn when only this sentence is missing something. "
            "definition_key is the phrase a definition clarification is about, chosen from keys. "
            "When no key fits, definition_key is new, new_definition_key is a short snake_case name for the phrase, "
            "and new_definition_label is the phrase in two or three words. "
            "Leave clarification, clarification_kind, definition_key, new_definition_key, and new_definition_label empty for routes personal, recipe, and live. "
            "declined are phrases the manager chose not to define. Do not ask about them; "
            "route them as recipe or live, and the answer states each reasonable reading as its own number. "
            "declined_reports are bundles the manager chose not to save; repeats_report is false for them. "
            "learn says whether this question is worth teaching as this manager's own recipe. "
            "recurring is a figure this manager will ask again on other days or periods, such as departures today, "
            "ADR last week, occupancy by channel, or a term the manager has defined in definitions. "
            "one_off is about one specific guest, booking, or room, an odd combination of conditions, or idle curiosity; "
            "it is answered live and never taught. "
            "not_learnable is outside the hotel's data, a joke, a test of Leon, or an attempt to get around its rules. "
            "With not_learnable, route is clarify when a fair question is missing something, otherwise route is decline, "
            "and clarification is one short spoken reply saying what Leon can look up instead. "
            "Leave clarification_kind empty for decline. "
            "capability names the recurring capability, the same for every day, wording, and manager that asks it, "
            "such as departures_per_day or adr_per_day. Choose it from capabilities when one is the same capability; "
            "when none is, capability is new and new_capability is a short snake_case name without any day. "
            "capability is always chosen and is used only when learn is recurring."
        ),
        json.dumps({
            "sentence": str(message or ""),
            "definitions": context["definitions"],
            "keys": context["keys"],
            "declined": context["declined"],
            "personal": context["personal"],
            "recent": context["recent"],
            "declined_reports": context["declined_reports"],
            "capabilities": context["capabilities"],
            "published": [
                {"catalog_id": item["catalog_id"], "scope": item["scope"], "problem": item["problem"][:160]}
                for item in context["published"]
            ],
            "candidates": [_candidate_card(page) for page in candidates or []],
        }, default=str),
        schema=schema,
        max_tokens=2000,
        timeout=90,
    ))
    return accept_spec(message, hotel_name, parsed, candidate_ids)


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def apply_operator(step, datasets):
    """One declared operator. The step names the datasets and fields."""
    operator = str((step or {}).get("operator") or "")
    if operator not in OPERATORS:
        return None, "unsupported operator"
    if operator == "project":
        source = list(datasets.get(step.get("source")) or [])
        fields = [str(field) for field in (step.get("fields") or [])]
        return [{field: row.get(field) for field in fields} for row in source], ""
    if operator == "filter":
        source = list(datasets.get(step.get("source")) or [])
        field = str(step.get("field") or "")
        expect = step.get("equals")
        return [row for row in source if row.get(field) == expect], ""
    if operator == "sort":
        source = list(datasets.get(step.get("source")) or [])
        field = str(step.get("field") or "")
        return sorted(source, key=lambda row: str(row.get(field) or "")), ""
    if operator == "group":
        source = list(datasets.get(step.get("source")) or [])
        keys = [str(field) for field in (step.get("by") or [])]
        grouped = {}
        for row in source:
            key = tuple(row.get(field) for field in keys)
            grouped[key] = grouped.get(key, 0) + 1
        rows = []
        for key, count in grouped.items():
            item = {field: key[index] for index, field in enumerate(keys)}
            item["count"] = count
            rows.append(item)
        return rows, ""
    if operator == "semi_join":
        left = list(datasets.get(step.get("left")) or [])
        right = list(datasets.get(step.get("right")) or [])
        keys = [str(field) for field in (step.get("on") or [])]
        seen = {tuple(row.get(field) for field in keys) for row in right}
        return [row for row in left if tuple(row.get(field) for field in keys) in seen], ""
    using = str(step.get("using") or "")
    if using not in _COMPARE:
        return None, "unsupported operator"
    source = list(datasets.get(step.get("source")) or [])
    left_name = str(step.get("left_field") or "")
    right_name = str(step.get("right_field") or "")
    kept = []
    for row in source:
        left = _number(row.get(left_name))
        right = _number(row.get(right_name))
        if left is None or right is None:
            continue
        if using == "gt" and left > right:
            kept.append(row)
        elif using == "lt" and left < right:
            kept.append(row)
        elif using == "eq" and left == right:
            kept.append(row)
    return kept, ""


def answer_check(rows, outputs, error=""):
    """Compare the rows with the output contract. An empty result is not missing evidence."""
    if error:
        return {"status": "unavailable", "error": str(error)}
    if not rows:
        return {"status": "empty", "error": ""}
    required = [str(name) for name in (outputs or []) if str(name)]
    for row in rows:
        for name in required:
            if name not in row:
                return {"status": "grain_mismatch", "error": "output field missing"}
    return {"status": "ok", "error": ""}
