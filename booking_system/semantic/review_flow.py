"""Validator, reviewer, and publisher. The catalog agent does not approve its own recipe."""

_VALIDATOR_VERSION = "recipe_validator_v1"


def publication_authorized(proposal, review, validation, approval_ref, alias_only=False):
    """Refuse publication unless this exact body was validated and approved."""
    from booking_system.semantic.recipe import proposal_hash

    if not proposal:
        return "proposal not found"
    if str(proposal.get("action") or "") not in ("repair", "create_child"):
        return "not a recipe"
    current = proposal_hash(proposal.get("body") or {})
    stored = str(proposal.get("content_hash") or "")
    if not stored or stored != current:
        return "proposal changed after review"
    if not validation or not validation.get("candidate_ok") or str(validation.get("input_hash") or "") != current:
        return "validation missing or stale"
    unresolved = [str(item) for item in ((proposal.get("body") or {}).get("unresolved") or []) if str(item)]
    if unresolved:
        return "business definition required"
    ref = str(approval_ref or "")
    if alias_only and ref.startswith("policy:alias"):
        return ""
    if not ref.startswith("reviewer:"):
        return "reviewer approval required"
    if not review or str(review.get("decision") or "") != "APPROVE":
        return "reviewer approval required"
    if review.get("unresolved_questions"):
        return "business definition required"
    if str(review.get("proposal_hash") or "") != current:
        return "proposal changed after review"
    if str(review.get("review_id") or "") not in ref:
        return "reviewer approval required"
    return ""


def _same_procedure(active, body):
    active = active or {}
    body = body or {}
    return (
        str(((active.get("solution") or {}).get("sql_template")) or "")
        == str(((body.get("solution") or {}).get("sql_template")) or "")
        and list(active.get("steps") or []) == list(body.get("steps") or [])
        and str(active.get("output_grain") or "") == str(body.get("output_grain") or "")
        and list(active.get("outputs") or []) == list(body.get("outputs") or [])
    )


def platform_facts(hotel_name):
    """Facts the platform guarantees for every read. They are not business definitions."""
    from booking_system.timezone_utils import get_hotel_timezone_str

    zone = str(get_hotel_timezone_str(hotel_name) or "")
    return [
        "Every read runs in one transaction with the session TimeZone set to the hotel's configured time zone"
        + (" (" + zone + ")" if zone else "")
        + ". CURRENT_DATE is the hotel's local calendar date, and NOW() is the hotel's current instant.",
        "A read fails when the hotel has no configured time zone, so no read runs on another day boundary.",
        "Only hotel_name is bound. Every read must filter on %(hotel_name)s, and the validator checks isolation against another hotel.",
    ]


def review_package(proposal, trace, validation, candidate_rows=None):
    """Evidence for the reviewer. The catalog agent's messages are not included."""
    from booking_system.semantic.definitions import manager_definitions, trace_manager
    from booking_system.semantic.registry import latest_problem_spec, list_active_catalogs, visible_to

    body = proposal.get("body") or {}
    question = str((trace or {}).get("question") or "")
    hotel_name = str((trace or {}).get("hotel_name") or "")
    owner = trace_manager(trace)
    spec = latest_problem_spec(hotel_name, question)
    grain = str(body.get("output_grain") or spec.get("output_grain") or "")
    related = []
    for row in list_active_catalogs():
        found = row.get("body") or {}
        if row["catalog_id"] == proposal.get("catalog_id") or not visible_to(row, owner) or not found.get("problem"):
            continue
        if grain and found.get("output_grain") and found.get("output_grain") != grain:
            continue
        related.append({
            "catalog_id": row["catalog_id"],
            "scope": row["scope"],
            "version": row["version"],
            "problem": str(found.get("problem") or ""),
            "output_grain": str(found.get("output_grain") or ""),
        })
        if len(related) >= 5:
            break
    solution = body.get("solution") or {}
    return {
        "proposal_id": proposal.get("proposal_id"),
        "proposal_version_hash": proposal.get("content_hash") or "",
        "original_question": question,
        "hotel_scope": hotel_name,
        "recipe_scope": {"scope": "user", "manager_name": owner},
        "canonical_problem": {
            "entity": str(spec.get("entity") or ""),
            "output_grain": grain,
            "conditions": [str(item.get("fact") or "") for item in (spec.get("constraints") or []) if isinstance(item, dict)],
            "output": list(body.get("outputs") or spec.get("output") or []),
            "problem": str(body.get("problem") or ""),
        },
        "recipe": {
            "catalog_id": proposal.get("catalog_id"),
            "action": proposal.get("action"),
            "problem": body.get("problem"),
            "preconditions": body.get("preconditions"),
            "limitations": body.get("limitations"),
            "inputs": solution.get("parameters") if solution.get("parameters") is not None else body.get("inputs"),
            "output_grain": body.get("output_grain"),
            "outputs": body.get("outputs"),
            "steps": body.get("steps") or [],
            "sql_template": str(solution.get("sql_template") or ""),
            "verification": body.get("verification"),
            "validation": body.get("validation") or {},
        },
        "validator_report": {
            "ok": bool((validation or {}).get("candidate_ok")),
            "input_hash": str((validation or {}).get("input_hash") or ""),
            "notes": str((validation or {}).get("notes") or ""),
            "validator_version": _VALIDATOR_VERSION,
        },
        "candidate_result_rows": list(candidate_rows or []),
        "live_desk_answer": {
            "rows_excerpt": str((trace or {}).get("reply_excerpt") or ""),
            "error": str((trace or {}).get("error") or ""),
        },
        "platform_facts": platform_facts(hotel_name),
        "manager_definitions": manager_definitions(owner),
        "related_recipe_refs": related,
    }


def _repair_finding(issue, required_fix=""):
    defect = str(issue or "The proposal was declined.")
    repair = str(required_fix or defect)
    return {
        "severity": "critical",
        "category": "review",
        "issue": defect,
        "required_fix": repair,
    }


_MAX_REVISIONS = 2


def _settled_reason(keys):
    if not keys:
        return "these questions are about how the data is stored, not business meaning; resolve them from the schema instead of asking the manager"
    return "the manager already defined " + ", ".join(keys) + "; apply manager_definitions instead of asking"


def _decline(proposal_id, catalog_id, trace_id, reason, review_id=None, findings=None, outcome="revise", next_action=""):
    """REVISE returns findings to the catalog. REJECT quarantines. BLOCKED waits for a missing definition or service."""
    from booking_system.semantic.registry import close_investigation, save_publication_decision, set_proposal_status

    kept = []
    for item in findings or []:
        if not isinstance(item, dict):
            continue
        kept.append(_repair_finding(item.get("issue"), item.get("required_fix")))
    if not kept:
        kept = [_repair_finding(reason, reason)]
    note = " ".join(item["issue"] + " Required fix: " + item["required_fix"] for item in kept)
    set_proposal_status(proposal_id, "rejected")
    ref = "reviewer:" + str(review_id) if review_id else ""
    save_publication_decision(proposal_id, catalog_id, outcome, note, ref)
    if outcome != "revise" and trace_id:
        close_investigation(trace_id, outcome, next_action or outcome)
    return {
        "proposal_id": proposal_id,
        "review_id": review_id,
        "decision": {"revise": "REVISE", "rejected": "REJECT"}.get(outcome, "BLOCKED"),
        "status": outcome,
        "error": note,
        "findings": kept,
        "reviewer_called": bool(review_id),
    }


def consider_proposal(proposal_id, verdict_fn=None):
    """Validate, then review. Publish only an approved hash. Unsafe SQL never reaches publish."""
    from booking_system.semantic.executor import publish_catalog
    from booking_system.semantic.library import validate_publication
    from booking_system.semantic.recipe import proposal_hash, recipe_contract
    from booking_system.semantic.registry import (
        active_body,
        load_proposal,
        record_test_run,
        save_recipe_review,
        validation_for_hash,
    )
    from booking_system.semantic.reviewer_agent import _REVIEW_MODEL, accept_verdict, review_once
    from booking_system.semantic.trace import load_trace
    from booking_system.semantic.executor import _proposal_dates
    from booking_system.semantic.gate import over_investigation_budget

    proposal = load_proposal(proposal_id)
    if not proposal:
        return {"error": "proposal not found", "decision": "REJECT", "reviewer_called": False}
    action = str(proposal.get("action") or "")
    trace_id = proposal.get("trace_id")
    catalog_id = proposal.get("catalog_id")
    if action == "schema":
        return _decline(proposal_id, catalog_id, trace_id, "A schema change needs a separate authorized change process.", outcome="blocked", next_action="schema_change")
    if action not in ("repair", "create_child"):
        return _decline(proposal_id, catalog_id, trace_id, action or "not a recipe")
    body = proposal.get("body") or {}
    current = proposal_hash(body)
    if str(proposal.get("content_hash") or "") != current:
        return _decline(proposal_id, catalog_id, trace_id, "proposal changed after review")
    missing = recipe_contract(body)
    unresolved = [str(item) for item in (body.get("unresolved") or []) if str(item)]
    dates = _proposal_dates(trace_id)
    if missing or unresolved:
        reason = "business definition required: " + "; ".join(unresolved) if unresolved else "recipe contract " + " ".join(missing)
        record_test_run(proposal_id, True, False, reason, current, {"contract": not missing, "business_definition": not unresolved}, _VALIDATOR_VERSION)
        if unresolved:
            from booking_system.semantic.definitions import request_definitions, trace_manager
            asked = request_definitions(trace_manager(load_trace(trace_id)), unresolved, trace_id)
            if not asked.get("error") and not asked.get("opened"):
                return _decline(proposal_id, catalog_id, trace_id, _settled_reason(asked["settled"]), outcome="revise")
            return _decline(proposal_id, catalog_id, trace_id, reason, outcome="blocked", next_action="business_definition")
        return _decline(proposal_id, catalog_id, trace_id, reason)
    report = validate_publication(catalog_id, body, dates)
    record_test_run(
        proposal_id,
        True,
        bool(report.get("ok")),
        str(report.get("error") or "gates passed"),
        current,
        {"executable": bool(report.get("ok"))},
        _VALIDATOR_VERSION,
    )
    if not report.get("ok"):
        return _decline(proposal_id, catalog_id, trace_id, str(report.get("error") or "validation failed"))
    active = active_body(catalog_id)
    if active and _same_procedure(active, body):
        promoted = publish_catalog(proposal_id, "policy:alias")
        if promoted.get("status") == "published":
            promoted["decision"] = "APPROVE"
            promoted["reviewer_called"] = False
            return promoted
        return _decline(proposal_id, catalog_id, trace_id, str(promoted.get("error") or "publish refused"))
    trace = load_trace(trace_id) if trace_id else None
    if not trace or not str(trace.get("question") or "").strip():
        return _decline(proposal_id, catalog_id, trace_id, "missing evidence", outcome="blocked", next_action="missing_evidence")
    if over_investigation_budget(trace_id):
        return _decline(proposal_id, catalog_id, trace_id, "investigation token budget spent", outcome="blocked", next_action="token_budget")
    validation = validation_for_hash(proposal_id, current)
    package = review_package(proposal, trace, validation, report.get("rows"))
    raw = verdict_fn(package) if verdict_fn else review_once(package, trace)
    verdict = accept_verdict(raw)
    review_id = save_recipe_review(
        proposal_id,
        current,
        verdict["decision"],
        verdict["confidence"],
        verdict["findings"],
        verdict["unresolved_questions"],
        _REVIEW_MODEL,
        package,
    )
    if verdict["unresolved_questions"]:
        from booking_system.semantic.definitions import request_definitions, trace_manager
        asked = request_definitions(trace_manager(trace), verdict["unresolved_questions"], trace_id)
        if not asked.get("error") and not asked.get("opened"):
            return _decline(proposal_id, catalog_id, trace_id, _settled_reason(asked["settled"]), review_id, verdict["findings"], outcome="revise")
        declined = _decline(
            proposal_id, catalog_id, trace_id, "business definition required: " + "; ".join(verdict["unresolved_questions"]),
            review_id, outcome="blocked", next_action="business_definition",
        )
        declined["unresolved_questions"] = verdict["unresolved_questions"]
        return declined
    if any(item.get("category") == "review_unavailable" for item in verdict["findings"]):
        return _decline(proposal_id, catalog_id, trace_id, "", review_id, verdict["findings"], outcome="blocked", next_action="review_failed")
    if verdict["decision"] == "REJECT":
        return _decline(proposal_id, catalog_id, trace_id, "", review_id, verdict["findings"], outcome="rejected", next_action="quarantined")
    if verdict["decision"] == "REVISE":
        return _decline(proposal_id, catalog_id, trace_id, "", review_id, verdict["findings"])
    promoted = publish_catalog(proposal_id, "reviewer:" + str(review_id))
    if promoted.get("status") == "published":
        promoted["decision"] = "APPROVE"
        promoted["review_id"] = review_id
        promoted["reviewer_called"] = True
        return promoted
    return _decline(proposal_id, catalog_id, trace_id, str(promoted.get("error") or "publish refused"), review_id)


def learn(trace_id, verdict_fn=None, propose_fn=None, start_proposal=None):
    """One proposal plus at most two REVISE cycles. REJECT quarantines. BLOCKED waits."""
    from booking_system.semantic.catalog_agent import improve_from_trace
    from booking_system.semantic.registry import (
        close_investigation,
        ensure_workflow_tables,
        note_revision,
        save_proposal,
        set_proposal_status,
    )

    ensure_workflow_tables()
    propose = propose_fn or improve_from_trace
    findings = []
    revisions = 0
    pending = start_proposal
    while True:
        if pending:
            set_proposal_status(pending, "candidate")
            outcome = {"proposal_id": pending}
            pending = None
        else:
            outcome = propose(trace_id, findings=findings) or {}
        if outcome.get("token_budget"):
            close_investigation(trace_id, "blocked", "token_budget")
            return {"status": "blocked", "error": "investigation token budget spent", "trace_id": trace_id}
        proposal_id = outcome.get("proposal_id")
        if not proposal_id and outcome.get("action") == "none":
            saved = save_proposal(trace_id, "none", "", "", {}, str(outcome.get("diagnosis") or ""))
            set_proposal_status(saved, "no_change")
            close_investigation(trace_id, "closed", "no_catalog_change")
            return outcome
        if proposal_id:
            outcome = consider_proposal(proposal_id, verdict_fn=verdict_fn)
            if outcome.get("status") != "revise":
                return outcome
            findings = list(outcome.get("findings") or [])
            note_revision(trace_id, proposal_id)
        else:
            findings = [_repair_finding(outcome.get("error") or "No proposal was submitted.", "Call submit_proposal once.")]
        revisions += 1
        if revisions > _MAX_REVISIONS:
            close_investigation(trace_id, "blocked", "revision_budget")
            return {"status": "blocked", "error": "revision budget exhausted", "findings": findings}


def resume(trace_id, verdict_fn=None):
    """Review the latest proposal again after a definition is answered or the reviewer was unavailable."""
    from booking_system.semantic.registry import latest_proposal_for_trace, reopen_investigation

    if not reopen_investigation(trace_id):
        return {"status": "not_blocked", "trace_id": trace_id}
    return learn(trace_id, verdict_fn=verdict_fn, start_proposal=latest_proposal_for_trace(trace_id))
