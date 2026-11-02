"""Authorized reads and catalog publication. Agents do not write the database."""


def run_read(statements, parameters, task_id, hotel_id, authorization_ref="", row_filter=None):
    """Execute a hotel-scoped read and store the outcome as evidence. A per-day recipe keeps only the requested day or period."""
    import time
    from booking_system.semantic.query import execute_reads, rows_for
    from booking_system.semantic.registry import save_evidence

    started = time.monotonic()
    parameters = dict(parameters or {})
    parameters["hotel_id"] = str(hotel_id or "")
    if row_filter:
        result = execute_reads(statements, parameters, None)
        if not result.get("error"):
            result["rows"] = rows_for(result.get("rows") or [], row_filter)
            if not result["rows"]:
                result["error"] = "no row for " + (row_filter.get("period") or row_filter.get("day")) + " in " + row_filter["column"]
    else:
        result = execute_reads(statements, parameters)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    payload = {
        "statements": list(result.get("statements") or []),
        "rows": list(result.get("rows") or []),
        "error": str(result.get("error") or ""),
        "elapsed_ms": elapsed_ms,
        "parameters": dict(parameters or {}),
    }
    stored = save_evidence(task_id, hotel_id, "sql_read", payload, str(authorization_ref or "query"))
    result["evidence_id"] = stored["evidence_id"]
    result["elapsed_ms"] = elapsed_ms
    return result


def run_composition(pages, task_id, hotel_id):
    """Run two or three compatible recipes. Each statement list is the recipe the agent stored."""
    groups = []
    statements = []
    rows = []
    error = ""
    for page in pages or []:
        result = run_read(
            page.get("statements") or [],
            page.get("parameters") or {"hotel_id": str(hotel_id or "")},
            task_id,
            hotel_id,
            "recipe",
            page.get("row_filter"),
        )
        groups.append({
            "catalog_id": page.get("catalog_id"),
            "version": page.get("version"),
            "rows": result.get("rows") or [],
            "error": result.get("error") or "",
        })
        statements.extend(result.get("statements") or [])
        rows.extend(result.get("rows") or [])
        if result.get("error") and not error:
            error = str(result["error"])
    return {"groups": groups, "statements": statements, "rows": rows, "error": error}


def run_steps(steps, datasets, hotel_id="", dates=None, task_id=None):
    """Run declared recipe calls, then the operators those steps name."""
    from booking_system.semantic.context_engine import parameter_names, parameter_values
    from booking_system.semantic.query import accepted_reads
    from booking_system.semantic.recipe import apply_operator
    from booking_system.semantic.registry import active_body

    produced = dict(datasets or {})
    statements = []
    for step in steps or []:
        invoke = str((step or {}).get("invoke") or "")
        if invoke:
            body = active_body(invoke) or {}
            template = str(((body.get("solution") or {}).get("sql_template")) or "").strip()
            values, missing = parameter_values(hotel_id, dates or [], parameter_names(body))
            kept = accepted_reads([template]) if template else None
            if missing or not kept:
                return {"rows": [], "error": "missing parameter " + " ".join(missing), "statements": statements}
            result = run_read(kept, values, task_id, hotel_id, "recipe")
            statements.extend(result.get("statements") or [])
            if result.get("error"):
                return {"rows": [], "error": result["error"], "statements": statements}
            produced[str(step.get("id") or invoke)] = result.get("rows") or []
            continue
        rows, problem = apply_operator(step, produced)
        if problem:
            return {"rows": [], "error": problem, "statements": statements}
        produced[str(step.get("id") or "")] = rows
    last = steps[-1] if steps else {}
    return {"rows": produced.get(str(last.get("id") or ""), []), "error": "", "statements": statements, "elapsed_ms": 0}


def _proposal_dates(trace_id):
    """Relative days are CURRENT_DATE in the hotel time zone. No calendar dates are copied."""
    return []


def publish_catalog(proposal_id, approval_ref=None):
    """Publish one page after the reviewer has approved that exact proposal."""
    from booking_system.semantic.library import validate_publication
    from booking_system.semantic.recipe import proposal_hash
    from booking_system.semantic.registry import (
        active_body,
        approve_proposal,
        close_investigation,
        load_proposal,
        load_recipe_review,
        promote_proposal,
        save_publication_decision,
        validation_for_hash,
    )
    from booking_system.semantic.review_flow import _same_procedure, publication_authorized
    from booking_system.semantic.definitions import trace_manager
    from booking_system.semantic.personal import note_user_recipe
    from booking_system.semantic.trace import load_trace

    proposal = load_proposal(proposal_id)
    if not proposal:
        return {"error": "proposal not found"}
    body = proposal.get("body") or {}
    current = proposal_hash(body)
    active = active_body(proposal.get("catalog_id"))
    alias_only = bool(active) and _same_procedure(active, body)
    ref = str(approval_ref or "").strip()
    if alias_only and not ref.startswith("reviewer:"):
        ref = "policy:alias"
    review = load_recipe_review(proposal_id)
    validation = validation_for_hash(proposal_id, current)
    reason = publication_authorized(proposal, review, validation, ref, alias_only=alias_only)
    trace_id = proposal.get("trace_id")
    catalog_id = proposal.get("catalog_id")
    if reason:
        return {"status": "rejected", "decision": "REJECT", "error": reason, "proposal_id": proposal_id}
    dates = _proposal_dates(trace_id)
    report = validate_publication(catalog_id, body, dates)
    if not report.get("ok"):
        reason = str(report.get("error") or "validation failed")
        return {"status": "rejected", "decision": "REJECT", "error": reason, "proposal_id": proposal_id}
    approved = approve_proposal(proposal_id, ref)
    if approved.get("status") != "approved":
        return approved
    trace = load_trace(trace_id) if trace_id else None
    owner = trace_manager(trace) if trace else ""
    promoted = promote_proposal(proposal_id, assisted=True, approval_ref=ref, owner=owner)
    if promoted.get("status") == "published":
        note_user_recipe(owner, promoted["catalog_id"], promoted["version"])
    if trace_id and promoted.get("status") == "published":
        close_investigation(trace_id, "resolved", "published")
        retrieval = (trace or {}).get("retrieval") if isinstance((trace or {}).get("retrieval"), dict) else {}
        if retrieval.get("source") == "missing_recipe":
            from booking_system import jobs
            jobs.enqueue(
                "desk_follow",
                {"trace_id": int(trace_id)},
                hotel_id=str(trace.get("hotel_id") or ""),
                dedupe_key="desk_follow/" + str(trace_id),
            )
    elif trace_id:
        promoted["error"] = promoted.get("error") or "publish refused"
    promoted["approval_ref"] = ref
    save_publication_decision(
        proposal_id,
        promoted.get("catalog_id") or catalog_id,
        promoted.get("status") or "",
        "",
        ref,
    )
    return promoted
