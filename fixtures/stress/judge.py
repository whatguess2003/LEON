"""Deterministic comparison of the figures a reply stated against ground truth computed right after that turn."""

TOLERANCE = {"count": 0.001, "money": 0.51, "pct": 0.6, "minutes": 0.6, "hours": 0.06}


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _words(value):
    return " ".join(str(value or "").casefold().split())


def matches(kind, expected, stated):
    if kind in ("text", "date"):
        want, got = _words(expected), _words(stated.get("text"))
        return bool(want) and bool(got) and (want == got or want in got or (kind == "text" and got in want and len(got) >= 3))
    want = _number(expected)
    got = _number(stated.get("number"))
    if got is None:
        return False
    if want is None:
        want = 0.0
    return abs(want - got) <= TOLERANCE[kind]


def claims(scenario, turn):
    """Every figure this turn stated, with the truth at that moment and whether it matched."""
    out = []
    for figure in scenario["figures"]:
        stated = (turn.get("read") or {}).get(figure["name"])
        if stated:
            expected = (turn.get("truth") or {}).get(figure["name"])
            out.append({"name": figure["name"], "kind": figure["kind"], "expected": expected,
                        "stated": stated.get("text") if figure["kind"] in ("text", "date") else stated.get("number"),
                        "ok": matches(figure["kind"], expected, stated)})
    return out


def asked_clarification(turn):
    """The desk held a question for this goal (not the tacked-on follow-up definition, which has no waiting question)."""
    held = turn.get("awaiting") or {}
    return turn.get("route") == "clarify" or bool(held.get("kind") and held.get("original_question"))


def verdict(scenario, turns, checks):
    final = {}
    wrong_claims = []
    for index, turn in enumerate(turns):
        for claim in claims(scenario, turn):
            final[claim["name"]] = dict(claim, turn=index + 1)
            if not claim["ok"]:
                wrong_claims.append(dict(claim, turn=index + 1))
    total = len(scenario["figures"])
    right = sum(1 for claim in final.values() if claim["ok"])
    if not final:
        label = "no_answer"
    elif right == total:
        label = "correct"
    elif right:
        label = "partially_correct"
    else:
        label = "wrong"
    flags = []
    asked = any(asked_clarification(turn) for turn in turns)
    if scenario["clarify"] == "required" and not asked:
        flags.append("no_clarification_asked")
        if label != "correct":
            label = "failed_to_clarify"
    if scenario["clarify"] == "never" and asked:
        flags.append("clarification_asked")
        if label == "correct":
            label = "unnecessary_clarification"
    failed_checks = [check for check in checks if not check["ok"]]
    if failed_checks:
        flags.append("action_state_check_failed")
        if label == "correct":
            label = "partially_correct"
    if scenario.get("expect_complaint") and not any(turn["records"]["complaints"] for turn in turns):
        flags.append("complaint_not_recorded")
    if wrong_claims:
        flags.append(str(len(wrong_claims)) + "_wrong_claims")
    return {"verdict": label, "flags": flags, "final": final, "wrong_claims": wrong_claims, "figures_right": right, "figures_total": total,
            "clarification_asked": asked}


def hints(scenario, turns, judged, ending):
    """Root-cause hints for anything that was not correct, read from the turn records."""
    out = []
    if ending in ("give_up", "max_turns"):
        out.append("conversation ended by " + ending)
    for index, turn in enumerate(turns):
        number = "turn " + str(index + 1) + ": "
        reply = turn["reply"]
        if turn["status"] != 200 or reply.startswith(("Backend error", "Error processing")):
            out.append(number + "server error: " + reply[:200])
        records = turn["records"]
        for run in records["execution_runs"]:
            if run["error"]:
                out.append(number + "recipe " + str(run["catalog_id"]) + " v" + str(run["version"]) + " failed: " + str(run["error"])[:200])
        for item in records["evidence"]:
            if item.get("error") or item.get("stall_reason"):
                out.append(number + "live SQL " + (item.get("stall_reason") or "") + " " + str(item.get("error") or "")[:200])
        for spec in records["specs"]:
            if spec["unresolved"]:
                out.append(number + "spec unresolved: " + str(spec["unresolved"]))
            if spec["route"] == "decline":
                out.append(number + "declined: " + str(spec["clarification"] or "")[:200])
        if turn["route"] == "no_spec" and not scenario.get("action"):
            out.append(number + "no problem spec written (desk-action path or spec failure): " + reply[:160])
    for claim in judged["wrong_claims"]:
        turn = turns[claim["turn"] - 1]
        recipes = ", ".join(str(run["catalog_id"]) + " v" + str(run["version"]) for run in turn["records"]["execution_runs"]) or "live/none"
        out.append("turn " + str(claim["turn"]) + ": " + claim["name"] + " stated " + str(claim["stated"]) + " vs truth " + str(claim["expected"])
                   + " (route " + turn["route"] + ", recipes " + recipes + ")")
    missing = [figure["name"] for figure in scenario["figures"] if figure["name"] not in judged["final"]]
    if missing and judged["final"]:
        out.append("never stated: " + ", ".join(missing))
    return out
