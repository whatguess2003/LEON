"""Context packages for one agent invocation. Retrieval and evidence only."""

import json

_POLICY = {
    "desk": "desk_route_v1",
    "query": "query_fast_path_v1",
    "catalog": "catalog_investigation_v1",
}


def parameter_names(body):
    """Declared inputs, in the order the agent listed them. hotel_id is always required."""
    solution = (body or {}).get("solution") or {}
    raw = solution.get("parameters")
    if raw is None:
        raw = (body or {}).get("parameters")
    if isinstance(raw, dict):
        source = list(raw.keys())
    elif isinstance(raw, list):
        source = raw
    else:
        source = []
    names = []
    for item in source:
        name = str(item or "").strip()
        if name and name not in names:
            names.append(name)
    if "hotel_id" not in names:
        names.insert(0, "hotel_id")
    return names


def parameter_values(hotel_id, resolved_dates, names):
    """Bind hotel_id. A day is not chosen or bound here."""
    values = {}
    if "hotel_id" in (names or []):
        values["hotel_id"] = str(hotel_id or "")
    missing = [name for name in (names or []) if name not in values or values[name] in ("", None)]
    return values, missing


def capability_fingerprint(status, grain, entity, conditions=()):
    """One learning task per grain, entity, and set of conditions."""
    facts = sorted({" ".join(str(item or "").lower().split()) for item in conditions or [] if str(item or "").strip()})
    return str(status or "") + "|" + str(grain or "") + "|" + str(entity or "") + "|" + ";".join(facts)


def recipe_candidates(question, user_id, limit=8):
    """Published recipes whose index matches the question, with a complete contract, that this user may use:
    general recipes and the user's own recipes, the user's own first, each labelled with its scope."""
    import time
    from booking_system.semantic.recipe import recipe_contract
    from booking_system.semantic.registry import active_pages, search_knowledge, visible_to

    started = time.monotonic()
    shortlist = search_knowledge(question, limit * 2) or []
    by_id = active_pages([row.get("catalog_id") for row in shortlist])
    pages = []
    for row in shortlist:
        catalog_id = str(row.get("catalog_id") or "")
        page = by_id.get(catalog_id)
        if not page or not visible_to(page, user_id) or page["version"] != int(row.get("version") or 0):
            continue
        body = page.get("body") or {}
        if recipe_contract(body):
            continue
        pages.append({
            "catalog_id": catalog_id,
            "version": int(page.get("version") or 0),
            "body": body,
            "score": row.get("score"),
            "title": row.get("title"),
            "scope": page["scope"],
        })
    pages.sort(key=lambda page: page["scope"] != "user")
    return pages[:limit], int((time.monotonic() - started) * 1000)


def gap_fingerprint(status, considered_ids, question):
    """One gap for a retrieval outcome and the pages that were considered."""
    names = sorted({str(item) for item in (considered_ids or []) if str(item)})
    if names:
        return str(status or "") + "|" + ",".join(names)
    words = " ".join(str(question or "").lower().split())
    return str(status or "") + "|" + words


def _tokens(payload):
    return max(1, len(json.dumps(payload, default=str)) // 4)


def compatible_recipes(pages, spec, hotel, dates):
    """Keep recipes whose contract is complete, whose outputs hold every measure the spec asked of them, whose rows are split
    by no grain other than the breakdown asked, and whose inputs bind. A per-period count answers whatever unit it counts."""
    from booking_system.semantic.query import accepted_reads
    from booking_system.semantic.recipe import GRAINS, recipe_contract

    asked = {}
    for item in (spec or {}).get("measures") or []:
        asked.setdefault(item["catalog_id"], []).append(item["column"])
    breakdown = set((spec or {}).get("breakdown") or [])
    compatible = []
    for page in pages or []:
        body = page.get("body") or {}
        if recipe_contract(body):
            continue
        grain = str(body.get("output_grain") or "")
        measures = asked.get(page.get("catalog_id")) or []
        allowed = {grain} if GRAINS[grain]["splits"] else set()
        if not measures or not set(measures) <= set(body.get("outputs") or []) or not breakdown <= allowed:
            continue
        template = str(((body.get("solution") or {}).get("sql_template")) or "").strip()
        values, missing = parameter_values(hotel, dates, parameter_names(body))
        kept = accepted_reads([template]) if template else None
        steps = list(body.get("steps") or []) if isinstance(body.get("steps"), list) else []
        if missing or (not kept and not steps):
            continue
        compatible.append({
            "catalog_id": page.get("catalog_id"),
            "version": int(page.get("version") or 0),
            "statements": kept or [],
            "parameters": values,
            "outputs": list(body.get("outputs") or []),
            "measures": measures,
            "output_grain": str(body.get("output_grain") or ""),
            "steps": steps,
            "score": page.get("score"),
            "title": page.get("title"),
        })
    return compatible


def _filtered(compatible, row_filters, hotel):
    """Keep the recipe row for the period the desk wrote. No business-date lookup and no day offset."""
    wanted = {item["catalog_id"]: item for item in row_filters}
    kept = []
    for item in compatible:
        chosen = wanted.get(item["catalog_id"])
        if not chosen:
            kept.append(item)
            continue
        if chosen["column"] not in item["outputs"]:
            continue
        period = str(chosen.get("period") or "")
        if not period:
            kept.append(item)
            continue
        kept.append(dict(item, row_filter={"column": chosen["column"], "day": "", "period": period}))
    return kept


class ContextEngine:
    def build(self, context_request):
        """Resolve and package task-specific, authorized context."""
        agent = str((context_request or {}).get("agent") or "")
        builders = {
            "desk": self._desk,
            "query": self._query,
            "catalog": self._catalog,
        }
        builder = builders.get(agent)
        if builder is None:
            return {
                "agent": agent,
                "retrieval": {"status": "retrieval_error", "execution": "agent"},
                "error": "unknown agent",
                "context_overflow": False,
                "search_ms": 0,
                "page_ids": [],
                "considered_ids": [],
            }
        return builder(context_request or {})

    def expand(self, task_id, references, budget, hotel_id="", user_id=""):
        """Return additional permitted evidence by stable reference. A page is permitted when it is general or the user's own."""
        from booking_system.semantic.registry import task_hotel

        self._manager = str(user_id or "")
        hotel = str(hotel_id or "")
        if task_id:
            hotel = task_hotel(task_id) or hotel
        blocks = []
        for ref in references or []:
            text = str(ref or "")
            kind, sep, ident = text.partition(":")
            if not sep or not ident:
                blocks.append({"reference": text, "error": "reference needs a type and an id"})
                continue
            blocks.append(self._expand_one(kind, ident, hotel, text))
        payload = {
            "task_id": task_id,
            "hotel_id": hotel,
            "references": blocks,
            "policy_version": "expand_v1",
        }
        payload["token_count"] = _tokens(payload)
        limit = int((budget or {}).get("max_tokens") or 3000)
        payload["context_overflow"] = payload["token_count"] > limit
        return payload

    def _desk(self, request):
        scope = request.get("scope") or {}
        entered = request.get("input") or {}
        package = {
            "agent": "desk",
            "purpose": str(request.get("purpose") or "route"),
            "scope": {"hotel_id": str(scope.get("hotel_id") or "")},
            "request": {
                "question": str(entered.get("question") or ""),
                "history": str(entered.get("history") or ""),
            },
            "retrieval": {"status": "matched", "execution": "agent"},
            "knowledge": [],
            "page_ids": [],
            "considered_ids": [],
            "evidence_refs": list(request.get("references") or []),
            "policy_version": _POLICY["desk"],
            "search_ms": 0,
        }
        package["token_count"] = _tokens(package)
        budget = int((request.get("budget") or {}).get("max_tokens") or 3000)
        package["context_overflow"] = package["token_count"] > budget
        return package

    def _query(self, request):
        scope = request.get("scope") or {}
        entered = request.get("input") or {}
        hotel = str(scope.get("hotel_id") or "")
        question = str(entered.get("question") or "")
        entered_dates = entered.get("resolved_dates")
        dates = entered_dates if isinstance(entered_dates, dict) else {"resolutions": []}
        spec = entered.get("spec") or {}
        budget = int((request.get("budget") or {}).get("max_tokens") or 3000)
        if not hotel:
            return self._query_package(request, hotel, question, dates, "access_denied", "agent", [], [], {}, 0, budget, "", spec=spec)
        shortlist = list(entered.get("candidates") or [])
        search_ms = int(entered.get("search_ms") or 0)
        considered = [str(row.get("catalog_id") or "") for row in shortlist if row.get("catalog_id")]
        chosen_ids = set(spec.get("recipe_ids") or [])
        loaded_pages = [page for page in shortlist if page.get("catalog_id") in chosen_ids]
        compatible = _filtered(compatible_recipes(loaded_pages, spec, hotel, dates), spec.get("row_filters") or [], hotel)
        if len(compatible) < len(chosen_ids):
            compatible = []
        status = "missing_recipe"
        execution = "agent"
        chosen = []
        bound = {}
        if len(compatible) == 1:
            status = "recipe_match"
            execution = "template"
            chosen = [compatible[0]["catalog_id"]]
            bound = compatible[0]
        elif 2 <= len(compatible) <= 3:
            status = "simple_composition_possible"
            execution = "composition"
            chosen = [item["catalog_id"] for item in compatible]
            bound = {"composition": compatible}
        elif len(compatible) > 3:
            status = "unsupported_composition"
            chosen = [item["catalog_id"] for item in compatible[:3]]
        return self._query_package(
            request, hotel, question, dates, status, execution, chosen, considered, bound, search_ms, budget, "", shortlist, spec
        )

    def _query_package(self, request, hotel, question, dates, status, execution, chosen, considered, bound, search_ms, budget, error, shortlist=None, spec=None):
        versions = []
        for row in shortlist or []:
            if row.get("catalog_id") in chosen:
                versions.append({"catalog_id": row["catalog_id"], "version": int(row.get("version") or 0)})
        composition = list(bound.get("composition") or [])
        package = {
            "agent": "query",
            "purpose": str(request.get("purpose") or "answer_database_question"),
            "scope": {"hotel_id": hotel},
            "request": {"question": question, "resolved_dates": dates, "spec": spec or {}},
            "retrieval": {"status": status, "execution": execution},
            "knowledge": versions,
            "referenced_versions": versions,
            "page_ids": chosen,
            "considered_ids": considered,
            "shortlist": [
                {"catalog_id": row.get("catalog_id"), "title": row.get("title"), "score": row.get("score")}
                for row in (shortlist or [])
            ],
            "statements": list(bound.get("statements") or []),
            "parameters": dict(bound.get("parameters") or {}),
            "outputs": list(bound.get("outputs") or []),
            "output_grain": str(bound.get("output_grain") or (spec or {}).get("output_grain") or ""),
            "steps": list(bound.get("steps") or []),
            "row_filter": dict(bound.get("row_filter") or {}),
            "composition": composition,
            "evidence_refs": [],
            "unresolved": list((spec or {}).get("unresolved") or []),
            "policy_version": "recipe_resolver_v1",
            "search_ms": int(search_ms or 0),
            "error": error,
        }
        package["token_count"] = _tokens(package)
        package["context_overflow"] = package["token_count"] > int(budget or 3000)
        return package

    def _catalog(self, request):
        from booking_system.semantic.trace import load_trace

        entered = request.get("input") or {}
        trace = load_trace(entered.get("trace_id")) if entered.get("trace_id") else None
        package = {
            "agent": "catalog",
            "purpose": str(request.get("purpose") or "investigate"),
            "scope": {"hotel_id": str((request.get("scope") or {}).get("hotel_id") or "")},
            "request": {"trace": trace or {}},
            "retrieval": {"status": "matched" if trace else "no_match", "execution": "agent"},
            "page_ids": list((trace or {}).get("catalog_ids") or []),
            "considered_ids": list(((trace or {}).get("retrieval") or {}).get("shortlist") or []),
            "policy_version": _POLICY["catalog"],
            "search_ms": 0,
        }
        package["token_count"] = _tokens(package)
        budget = int((request.get("budget") or {}).get("max_tokens") or 3000)
        package["context_overflow"] = package["token_count"] > budget
        return package

    def _expand_one(self, kind, ident, hotel, reference):
        loaders = {
            "schema": self._schema_ref,
            "page": self._page_ref,
            "evidence": self._evidence_ref,
        }
        loader = loaders.get(kind)
        if loader is None:
            return {"reference": reference, "error": "unknown reference"}
        return loader(ident, hotel, reference)

    def _schema_ref(self, ident, hotel, reference):
        from booking_system.content_store import _with

        def read(cur):
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
                """,
                (str(ident or ""),),
            )
            return [column + " " + data_type for column, data_type in cur.fetchall()]

        try:
            columns = _with(read)
        except Exception as exc:
            return {"reference": reference, "error": str(exc)}
        return {"reference": reference, "columns": columns or []}

    def _page_ref(self, ident, hotel, reference):
        from booking_system.semantic.registry import active_body, catalog_scopes, visible_to
        scope = catalog_scopes([ident]).get(ident) or {}
        body = active_body(ident) if visible_to(scope, self._manager) else None
        if not body:
            return {"reference": reference, "error": "catalog not found"}
        return {"reference": reference, "body": body}

    def _evidence_ref(self, ident, hotel, reference):
        from booking_system.semantic.registry import load_evidence
        try:
            row = load_evidence(ident)
        except Exception as exc:
            return {"reference": reference, "error": str(exc)}
        if not row:
            return {"reference": reference, "error": "evidence not found"}
        if hotel and str(row.get("hotel_id") or "") != hotel:
            return {"reference": reference, "error": "access_denied"}
        return {"reference": reference, "evidence": row}
