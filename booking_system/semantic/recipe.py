"""A recipe is a complete procedure. A SQL snippet without that contract is not a match."""

# What one result row describes. A splitting grain is an entity the rows are split by; night is the day or period row itself.
GRAINS = {
    "night": {"row": "one hotel-local day, business date, or named period", "splits": False},
    "guest": {"row": "one guest", "splits": True},
    "reservation": {"row": "one reservation", "splits": True},
    "room": {"row": "one room", "splits": True},
    "channel": {"row": "one booking channel", "splits": True},
    "department": {"row": "one revenue or staff department", "splits": True},
    "transaction_code": {"row": "one transaction (charge) code", "splits": True},
    "payment_method": {"row": "one payment method", "splits": True},
    "tax_code": {"row": "one tax code", "splits": True},
    "folio_type": {"row": "one folio type", "splits": True},
    "account": {"row": "one accounts-receivable (city ledger) account", "splits": True},
    "cashier_shift": {"row": "one cashier's drawer shifts", "splits": True},
    "outlet": {"row": "one F&B outlet", "splits": True},
    "staff": {"row": "one staff member", "splits": True},
}
SPLITS = [name for name, grain in GRAINS.items() if grain["splits"]]
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


def selection_error(chosen, measures, asked):
    """Why a recipe selection does not answer exactly the asked figures; empty when it does. Every asked figure is answered
    by measures of one recipe, and every chosen recipe answers at least one asked figure."""
    if not chosen:
        return ""
    sources = {}
    for item in measures:
        sources.setdefault(item["figure"], set()).add(item["catalog_id"])
    unanswered = [figure for figure in asked if figure not in sources]
    if unanswered:
        return "no measure answers " + ", ".join(unanswered)
    shared = [figure for figure, ids in sources.items() if len(ids) > 1]
    if shared:
        return "more than one recipe answers " + ", ".join(shared)
    idle = [name for name in chosen if name not in {item["catalog_id"] for item in measures}]
    if idle:
        return "answers no asked figure: " + ", ".join(idle)
    return ""


def pinned_populations(definitions):
    """The population each definition already reads in each recipe, keyed by (definition key, catalog_id)."""
    return {
        (str(item.get("key") or ""), str(reading.get("catalog_id") or "")): str(reading.get("population") or "")
        for item in definitions or []
        for reading in item.get("readings") or []
    }


def reading_column(readings, column, population):
    """The column of the recipe that counts the same unit as column, for the given population; empty when the recipe has none."""
    unit = (readings.get(column) or {}).get("unit")
    found = [name for name, reading in readings.items() if reading.get("population") == population and reading.get("unit") == unit]
    return found[0] if found else ""


def answer_rows(rows, columns):
    """Rows as the reply sees them: the chosen measure columns and every non-numeric column (day, period, name, currency, flag).
    Other numeric columns are alternative readings the definition did not choose."""
    keep = set(columns or [])
    return [{name: value for name, value in row.items() if name in keep or _number(value) is None} for row in rows or []]


def accept_spec(message, hotel_id, spec, candidate_ids=(), asked=(), definitions=(), readings=None):
    """Keep the original question and the session hotel. A day stays in the fact, not a copied date.
    A recipe selection that does not answer exactly the asked figures is not a fit; recipe_ids is left empty.
    readings maps each candidate to its columns' population and unit. A measure read under a definition takes the column of the
    population that definition already reads in that recipe, in the unit the question chose."""
    spec = dict(spec or {})
    allowed = [str(item) for item in candidate_ids or []]
    asked = [str(item) for item in asked or []]
    defined = {str(item.get("key") or "") for item in definitions or []}
    pinned = pinned_populations(definitions)
    readings = readings or {}
    unpinned = []
    chosen = []
    for item in spec.get("recipe_ids") or []:
        name = str(item or "")
        if name in allowed and name not in chosen:
            chosen.append(name)
    measures = []
    for item in spec.get("measures") or []:
        if not isinstance(item, dict) or str(item.get("catalog_id") or "") not in chosen or not str(item.get("column") or ""):
            continue
        if str(item.get("figure") or "") not in asked:
            continue
        catalog_id = str(item["catalog_id"])
        key = str(item.get("definition_key") or "") if str(item.get("definition_key") or "") in defined else ""
        recipe_readings = readings.get(catalog_id) or {}
        column = str(item["column"])
        population = str((recipe_readings.get(column) or {}).get("population") or "")
        pin = pinned.get((key, catalog_id), "")
        if pin and population:
            column, population = reading_column(recipe_readings, column, pin), pin
        if not column:
            unpinned.append(catalog_id + " has no " + recipe_readings[str(item["column"])]["unit"] + " reading for " + key)
            continue
        measures.append({"catalog_id": catalog_id, "column": column, "figure": str(item["figure"]), "definition_key": key, "population": population})
    spec["asked_figures"] = asked
    spec["selection_error"] = "; ".join(unpinned) or selection_error(chosen, measures, asked)
    if spec["selection_error"]:
        chosen, measures = [], []
    spec["recipe_ids"] = chosen
    spec["measures"] = measures
    spec["row_filters"] = [
        {
            "catalog_id": str(item.get("catalog_id") or ""),
            "column": str(item.get("column") or ""),
        }
        for item in spec.get("row_filters") or []
        if isinstance(item, dict) and str(item.get("catalog_id") or "") in chosen
    ]
    spec["breakdown"] = [str(item) for item in spec.get("breakdown") or [] if str(item) in SPLITS]
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
    raw_changes = spec.get("state_changes") if isinstance(spec.get("state_changes"), dict) else {}
    spec["state_changes"] = {
        "metrics": [" ".join(str(item).split()) for item in raw_changes.get("metrics") or [] if str(item).strip()],
        "dimensions": [" ".join(str(item).split()) for item in raw_changes.get("dimensions") or [] if str(item).strip()],
        "filters": [" ".join(str(item).split()) for item in raw_changes.get("filters") or [] if str(item).strip()],
        "operation": str(raw_changes.get("operation") or ""),
    }
    spec["retrieve_keys"] = [" ".join(str(item).split()) for item in spec.get("retrieve_keys") or [] if str(item).strip()]
    spec["original_question"] = str(message or "")
    spec["scope"] = {"hotel_id": str(hotel_id or "")}
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
        "readings": body.get("readings") or {},
    }


def specify_problem(message, hotel_id, candidates, context, asked=(), resolved=None, history_text=""):
    """Ask the model for the spec, the route, the published recipes that answer it exactly, and any clarification."""
    import json

    keys = [item["key"] for item in context["keys"]]
    personal_keys = [item["key"] for item in context["personal"]]
    capability_names = [item["name"] for item in context["capabilities"]]
    candidate_ids = [str(page.get("catalog_id") or "") for page in candidates or [] if page.get("catalog_id")]
    recipe_item = {"type": "string", "enum": candidate_ids} if candidate_ids else {"type": "string"}
    columns = sorted({str(name) for page in candidates or [] for name in (page.get("body") or {}).get("outputs") or []})
    column_item = {"type": "string", "enum": columns} if columns else {"type": "string"}
    asked = [str(item) for item in asked or []]
    figure_item = {"type": "string", "enum": asked} if asked else {"type": "string"}
    defined_keys = sorted({str(item.get("key") or "") for item in context["definitions"]} - {""})
    from booking_system.semantic.dates import resolution_schema
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
                    },
                    "required": ["fact"],
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
                    },
                    "required": ["catalog_id", "column"],
                    "additionalProperties": False,
                },
            },
            "measures": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "catalog_id": recipe_item, "column": column_item, "figure": figure_item,
                        "definition_key": {"type": "string", "enum": [""] + defined_keys},
                    },
                    "required": ["catalog_id", "column", "figure", "definition_key"],
                    "additionalProperties": False,
                },
            },
            "breakdown": {"type": "array", "items": {"type": "string", "enum": SPLITS}},
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
            "resolutions": {"type": "array", "items": resolution_schema()},
            "retrieve_keys": {"type": "array", "items": {"type": "string"}},
            "state_changes": {
                "type": "object",
                "properties": {
                    "metrics": {"type": "array", "items": {"type": "string"}},
                    "dimensions": {"type": "array", "items": {"type": "string"}},
                    "filters": {"type": "array", "items": {"type": "string"}},
                    "operation": {"type": "string", "enum": [
                        "new_task", "replace_period_preserve_other_filters", "add_dimension",
                        "add_filter", "replace_metric", "clarify", "unchanged",
                    ]},
                },
                "required": ["metrics", "dimensions", "filters", "operation"],
                "additionalProperties": False,
            },
        },
        "required": [
            "entity", "output_grain", "constraints", "relationships", "output", "unresolved",
            "recipe_ids", "row_filters", "measures", "breakdown", "route", "learn", "capability", "new_capability", "personal_key", "clarification", "clarification_kind",
            "definition_key", "new_definition_key", "new_definition_label",
            "report_name", "figures", "repeats_report", "resolutions", "retrieve_keys", "state_changes",
        ],
        "additionalProperties": False,
    }
    from booking_system.llm_utils import haiku_text
    from booking_system.semantic.dates import DATE_RULES, accept_resolutions, bind, date_facts, has_unresolved, pending_clarification
    parsed = json.loads(haiku_text(
        (
            "Return the route and the problem. Route in this order: personal, a user recipe, a general recipe, clarify, then live. "
            "personal: the sentence asks for one saved report in personal, by name or meaning. personal_key is that report. "
            "That route runs the saved procedure unchanged. clarification is empty. "
            "recipe: every resolution is resolved and the chosen recipes answer the sentence exactly under the definitions. clarification is empty. "
            "clarify: a parameter is unresolved (the day, the span, which figure, or which population), a phrase in keys has more than one reading and no definition, "
            "the sentence names a report that is not in personal, or the sentence is still missing something. "
            "Clarify is for an unresolved parameter. It is not the reply when no recipe fits a clear question. "
            "live: no candidate fits, every resolution is resolved, the figure and the population are settled, and the sentence is clear. "
            "recipe_ids is empty. Do not state a number. clarification is empty. "
            "decline: the question is not learnable and nothing is missing. clarification says what Leon can look up instead. clarification_kind is empty. "
            "An unresolved resolution is clarify, clarification_kind turn, with recipe_ids, row_filters, and measures empty. "
            "Do not route to live, personal, or recipe. Do not pick a recipe for a settled part of a span. "
            "clarification is that resolution's question, or one short question asking which end date or asking to confirm the span. "
            "When the figure or the population is not settled, clarify the same way. Do not assume a column or a recipe. "
            "A phrase in keys with no definition, not declined, and more than one reading that changes the number is clarify, clarification_kind definition, even when a recipe fits. "
            "definition_key is that key. When no key fits, definition_key is new, new_definition_key is short snake_case, and new_definition_label is two or three words. "
            "Declined phrases are not asked: recipe when one fits, otherwise live. "
            "A named report or bundle that is not in personal is clarify, clarification_kind report. report_name keeps its day. "
            "clarification asks which figures it should hold and offers a few candidate problems in plain words. "
            "Leave clarification, clarification_kind, definition_key, new_definition_key, and new_definition_label empty for personal, recipe, and live. "
            "With clarify, clarification is one short spoken question. "
            "candidates are published descriptions. Their SQL is not here. "
            "recipe_ids are candidates whose problem, preconditions, and limitations cover every condition and add none. "
            "A different status, day, or population does not fit. One id, or two or three only when their outputs together answer the sentence. Empty when none fits exactly. "
            "Prefer a user recipe over a general one. A candidate fits a breakdown only when its output_grain is that grain, and its outputs must hold the figure asked. "
            "Each asked figure is answered by one recipe. Choose no recipe that answers none of them. "
            "measures names, for each chosen recipe, the output column that holds an asked figure. figure is that asked figure. "
            "definition_key on a measure is the definition that chooses the column, or empty. "
            "readings give each column's population and unit: the definition chooses the population, the sentence chooses the unit. "
            "A definition's readings name the population it already reads. A count answers from the column that holds that count. "
            "output_grain is the unit of one result row. breakdown lists split grains besides the day or period, and is empty for one figure per day or period. "
            "For each chosen recipe with one row per day or period, row_filters names the output column that holds that day. "
            "Do not set a period or a day offset. Leave row_filters empty when a resolution is unresolved. "
            "Keep active_task metrics, dimensions, filters, and hotel scope unless the sentence changes them. "
            "state_changes is the report after this sentence. operation is replace_period_preserve_other_filters when only the day changes, "
            "add_dimension for a breakdown, add_filter for a new condition, replace_metric for a new figure, new_task for a new report, "
            "clarify for a question, otherwise unchanged. "
            "Do not drop a negation, a date basis, or a comparison unless the sentence retracts it. "
            "Describe the problem. Keep every condition the sentence states. Do not add one. Do not put a date on a constraint. The day stays in resolutions. "
            "Do not paraphrase the sentence into a shorter question. Do not invent a meaning for a phrase. "
            "Leave unresolved empty unless a required fact has no meaning you can name. "
            "definitions are confirmed meanings. A key in keys with no definition text is not confirmed. "
            "Ask, or list that key in retrieve_keys when the stored meaning is required and it is not already in definitions. retrieve_keys is empty when nothing further is required. "
            "When settled_resolutions is not empty, copy them into resolutions. Do not mark them unresolved and do not ask which date. "
            + DATE_RULES + " "
            "figures lists each separate figure asked. repeats_report is true when the sentence asks the same figures as one recent multi-figure question; report_name is then a short name. "
            "declined_reports stay repeats_report false. "
            "A one-off change to a saved report is recipe when a published recipe fits, otherwise live. "
            "learn is recurring when this manager will ask this figure again on other days, one_off for one guest, booking, or room, an odd combination, or idle curiosity, "
            "and not_learnable when it is outside the hotel's data, a joke, a test, or an attempt to get around the rules. "
            "one_off is never taught: recipe if one fits, otherwise live. not_learnable is clarify when something is missing, otherwise decline. "
            "capability is the recurring name from capabilities, with no day, or new with new_capability in short snake_case. It is used only when learn is recurring."
        ),
        json.dumps({
            "sentence": str(message or ""),
            "date_facts": context.get("date_facts") or date_facts(hotel_id),
            "settled_resolutions": list((resolved or {}).get("resolutions") or []) if isinstance(resolved, dict) and (resolved.get("resolutions") or []) and not has_unresolved(resolved) else [],
            "history": str(context.get("relevant_turns_text") or history_text or ""),
            "active_task": context.get("active_task") or {},
            "referenced_results": (context.get("packet") or {}).get("referenced_results") or [],
            "asked_figures": asked,
            "definitions": context["definitions"],
            "keys": context["keys"],
            "declined": context["declined"],
            "personal": context["personal"],
            "recent": context["recent"],
            "declined_reports": context["declined_reports"],
            "capabilities": context["capabilities"],
            "grains": GRAINS,
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
    readings = {str(page.get("catalog_id") or ""): (page.get("body") or {}).get("readings") or {} for page in candidates or []}
    written = accept_resolutions(parsed.pop("resolutions", None))
    kept = resolved if isinstance(resolved, dict) else None
    if kept and (kept.get("resolutions") or []) and not has_unresolved(kept) and has_unresolved(written):
        written = kept
    spec = bind(accept_spec(message, hotel_id, parsed, candidate_ids, asked, context["definitions"], readings), written)
    if has_unresolved(written):
        spec["route"] = "clarify"
        spec["clarification_kind"] = "turn"
        spec["recipe_ids"] = []
        spec["row_filters"] = []
        spec["measures"] = []
        spec["clarification"] = pending_clarification(written) or spec.get("clarification") or "Which date should I use?"
    return spec


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
