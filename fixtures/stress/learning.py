"""Learning-loop scoring. A first-round miss on a recipe gap is the learning signal, not a failure. Per gap: the round-1 answer,
what the Catalog Agent and reviewer did, and, once a recipe is published, the same question asked again verbatim plus one
natural rewording, each in a fresh chat after the same preceding turns. Classes:

  learned               published; both post-publication answers on a recipe route, correct, within FAST_MS
  learned_but_slow      published; both correct, at least one slower than FAST_MS
  published_but_wrong_or_unused
                        published; a post-publication answer is wrong, unanswered or off-recipe (cause: retrieval miss, spec chose
                        another recipe, recipe wrong, reviewer approved a wrong recipe)
  still_learning        investigation open or sent back for revision
  not_learnable         never queued (one-off, single occurrence, deferred by a cap) or the investigation is blocked

label_scenarios gives every scenario exactly one of these labels and compares the shares with BASELINE. A scenario that raised
no gap is labelled from its own answers: correct within FAST_MS is learned (an existing recipe already knew it), correct but
slower is learned_but_slow, wrong on a recipe route is published_but_wrong_or_unused, wrong elsewhere is not_learnable."""

import time
from datetime import datetime

FAST_MS = 20000
RECIPE_ROUTES = ("general", "user", "personal")
WRONG = "published_but_wrong_or_unused"
LABELS = ("learned", "learned_but_slow", WRONG, "still_learning", "not_learnable")
BASELINE = {"learned": 25, "learned_but_slow": 25, WRONG: 10, "still_learning": 30, "not_learnable": 10}
FLAW_THRESHOLD = BASELINE[WRONG]


def _when(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _seconds(start, end):
    if not start or not end:
        return None
    return int((_when(end) - _when(start)).total_seconds())


def turn_gaps(turn):
    gaps = {str(gate["gap"]) for gate in turn["gate"] if gate.get("gap")}
    gaps |= {str(trace["gap_fingerprint"] or "").rsplit("|", 1)[0] for trace in turn["records"]["traces"] if trace.get("gap_fingerprint")}
    return gaps


def gap_questions(conversations):
    """Every gap the conversations touched, with each turn that touched it (first one first)."""
    found = {}
    for item in conversations:
        for index, turn in enumerate(item["turns"]):
            for gap in turn_gaps(turn):
                found.setdefault(gap, []).append({"id": item["id"], "index": index, "conversation": item})
    return found


def _settled(outcome):
    published = any(row["status"] == "published" for row in outcome["decisions"])
    open_jobs = [job for job in outcome["jobs"] if job["job_status"] not in ("done", "completed", "failed", "dead", "cancelled")]
    open_investigations = [row for row in outcome["investigations"] if not row["closed_at"] and row["status"] != "blocked"]
    return published or (not open_jobs and not open_investigations)


def _gap_outcomes(hotel, gaps, deadline, say):
    """One read of every gap. A connection the pool already retried and still lost is tried again until the deadline."""
    import psycopg2

    from fixtures.stress import records

    while True:
        try:
            return records.gap_outcomes(hotel["hotel_id"], hotel["user_id"], gaps)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as error:
            say("learning poll db retry", type(error).__name__, str(error).splitlines()[0][:160])
            if time.monotonic() >= deadline:
                raise
            time.sleep(5)


def poll(hotel, gaps, minutes, interval_s, say):
    deadline = time.monotonic() + minutes * 60
    outcomes = _gap_outcomes(hotel, gaps, deadline, say)
    polls = 1
    while time.monotonic() < deadline and not all(_settled(item) for item in outcomes.values()):
        say("learning poll", polls, " ".join(gap.split("|")[-1] + ":" + ",".join(sorted({row["status"] for row in item["investigations"]}) or ["-"])
                                             + "/" + ",".join(row["status"] for row in item["decisions"]) for gap, item in outcomes.items()))
        time.sleep(interval_s)
        outcomes = _gap_outcomes(hotel, gaps, deadline, say)
        polls += 1
    say("learning poll", polls, "settled" if all(_settled(item) for item in outcomes.values()) else "deadline",
        " ".join(gap.split("|")[-1] + ":" + ",".join(sorted({row["status"] for row in item["investigations"]}) or ["-"])
                 + "/" + ",".join(row["status"] for row in item["decisions"]) for gap, item in outcomes.items()))
    return outcomes, polls


def ask_after(desk, scenario, conversation, index, hotel, message, label, say):
    """A fresh chat with the conversation's turns before index replayed verbatim, then message. Returns the last turn."""
    from fixtures.stress import records
    from fixtures.stress.run import play_turn

    desk.clear()
    opened = records.usage(hotel["hotel_id"])
    params = {"h": hotel["hotel_id"], "guest": "STRESS GUEST LEARNING", "started": opened["now"]}
    held = None
    turn = None
    for position in range(index + 1):
        text = message if position == index else conversation["turns"][position]["manager"]
        turn = play_turn(desk, scenario, hotel, params, text, held)
        held = turn["awaiting"]
    say("   ", label, conversation["id"], "t" + str(index + 1), turn["route"], str(turn["wall_ms"]) + "ms",
        " ".join(claim["name"] + ("=" if claim["ok"] else "!=") + str(claim["stated"]) for claim in turn["claims"]))
    return turn


def score(turn, published_ids):
    claims = turn["claims"]
    ran = [str(run["catalog_id"]) for run in turn["records"]["execution_runs"]]
    picked = [item for spec in turn["records"]["specs"] for item in (spec["recipe_ids"] or [])]
    return {"route": turn["route"], "wall_ms": turn["wall_ms"], "at": turn.get("at"), "reply": turn["reply"][:600], "message": turn["manager"],
            "claims": [(claim["name"], claim["stated"], claim["expected"], claim["ok"]) for claim in claims],
            "correct": bool(claims) and all(claim["ok"] for claim in claims), "fast": turn["wall_ms"] <= FAST_MS,
            "on_recipe": turn["route"] in RECIPE_ROUTES, "ran": ran, "spec_recipes": picked,
            "used_published": bool(set(ran) & set(published_ids)),
            "runs": [run["run_id"] for run in turn["records"]["execution_runs"]], "tasks": [task["task_id"] for task in turn["records"]["tasks"]]}


def _why_wrong(asks, reviews):
    """(cause code from answer_label_causes, explanation) per post-publication answer that missed."""
    reasons = []
    for ask in asks:
        if ask["correct"] and ask["on_recipe"]:
            continue
        if ask["used_published"] and not ask["correct"]:
            approved = any(row["decision"] == "APPROVE" for row in reviews)
            reasons.append(("reviewer_approved_wrong_recipe" if approved else "recipe_wrong", ask["kind"] + " ran " + ",".join(ask["ran"])))
        elif ask["spec_recipes"]:
            reasons.append(("spec_chose_other_recipe", ask["kind"] + " spec chose " + ",".join(ask["spec_recipes"])))
        elif ask["route"] in ("live", "clarify", "decline", "no_spec"):
            reasons.append(("retrieval_miss", ask["kind"] + " route " + ask["route"]))
        else:
            reasons.append(("other", ask["kind"] + " stated no figure (route " + ask["route"] + ")"))
    return reasons


def classify(gap, touches, outcome, asks):
    decisions = [row["status"] for row in outcome["decisions"]]
    published = [row for row in outcome["decisions"] if row["status"] == "published"]
    gates = [gate for touch in touches for gate in touch["conversation"]["turns"][touch["index"]]["gate"]
             if gate.get("gap") == gap or gate.get("trace_id") in {row["trace_id"] for row in outcome["investigations"]}]
    if published:
        if asks and all(ask["correct"] and ask["on_recipe"] for ask in asks):
            return ("learned" if all(ask["fast"] for ask in asks) else "learned_but_slow"), ""
        reasons = _why_wrong(asks, outcome["reviews"]) or [("other", "not asked after publication")]
        return WRONG, "; ".join(code + ": " + text for code, text in reasons)
    if not outcome["investigations"]:
        seen = sorted({str(gate.get("decision")) + (":" + str(gate.get("reason")) if gate.get("reason") else "") for gate in gates})
        return "not_learnable", "never queued (" + ", ".join(seen) + ")"
    latest = outcome["investigations"][-1]
    if latest["status"] == "blocked":
        return "not_learnable", "blocked: " + str(latest["next_action"]) + (" after reviewer " + outcome["reviews"][-1]["decision"] if outcome["reviews"] else "")
    return "still_learning", "investigation " + str(latest["status"]) + " step " + str(latest["current_step"]) + (" decisions " + ",".join(decisions) if decisions else "")


def _failed_ask(kind, error):
    return {"kind": kind, "route": "error", "wall_ms": 0, "at": None, "reply": "", "message": "", "claims": [],
            "correct": False, "fast": False, "on_recipe": False, "ran": [], "spec_recipes": [], "used_published": False,
            "runs": [], "tasks": [], "error": str(error).splitlines()[0][:300]}


def _ask_retry(desk, scenario, conversation, index, hotel, message, label, say):
    caught = None
    for _attempt in range(3):
        try:
            return ask_after(desk, scenario, conversation, index, hotel, message, label, say), None
        except Exception as error:
            caught = error
            say("ask retry", label, conversation["id"], type(error).__name__, str(error).splitlines()[0][:160])
            time.sleep(5)
    return None, caught


def _reword(scenario, transcript, message, say):
    from fixtures.stress import agents

    caught = None
    for _attempt in range(3):
        try:
            return agents.reword(scenario, transcript, message), None
        except Exception as error:
            caught = error
            say("reword retry", scenario["id"], type(error).__name__, str(error).splitlines()[0][:160])
            time.sleep(5)
    return "", caught


def run(desk, hotel, conversations, scenarios_by_id, minutes, interval_s, say, on_update=None):
    """Poll the gaps the conversations raised, ask again after publication, and classify each gap."""
    touched = gap_questions(conversations)
    outcomes, polls = poll(hotel, sorted(touched), minutes, interval_s, say)
    state = {"fast_ms": FAST_MS, "polls": polls, "gaps": []}
    if on_update:
        on_update(state)
    try:
        for gap, touches in touched.items():
            outcome = outcomes[gap]
            published = [row for row in outcome["decisions"] if row["status"] == "published"]
            published_ids = [row["catalog_id"] for row in published]
            first = touches[0]
            conversation, index = first["conversation"], first["index"]
            scenario = scenarios_by_id[conversation["id"]]
            round1 = dict(score(conversation["turns"][index], published_ids), kind="round1")
            asks = []
            if published:
                message = conversation["turns"][index]["manager"]
                transcript = [{"manager": turn["manager"], "desk": turn["reply"]} for turn in conversation["turns"][:index]]
                asked, error = _ask_retry(desk, scenario, conversation, index, hotel, message, "exact", say)
                asks.append(dict(score(asked, published_ids), kind="exact") if asked else _failed_ask("exact", error))
                reworded, error = _reword(scenario, transcript, message, say)
                if reworded:
                    asked, error = _ask_retry(desk, scenario, conversation, index, hotel, reworded, "reworded", say)
                    asks.append(dict(score(asked, published_ids), kind="reworded") if asked else _failed_ask("reworded", error))
                else:
                    asks.append(_failed_ask("reworded", error))
            label, reason = classify(gap, touches, outcome, asks)
            asked = [touch["conversation"]["turns"][touch["index"]].get("at") or touch["conversation"].get("started_db") for touch in touches]
            asked_at = min([_when(value) for value in asked if value] + [_when(row["created_at"]) for row in outcome["investigations"]] or [None])
            published_at = min([_when(row["created_at"]) for row in published] or [None])
            good = [ask for ask in asks if ask["correct"] and ask["on_recipe"] and ask["fast"]]
            first_good = min([_when(ask["at"]) for ask in good if ask.get("at")] or [None])
            state["gaps"].append({
                "gap": gap, "class": label, "reason": reason, "scenario": conversation["id"], "turn": index + 1, "touches": len(touches),
                "round1": round1, "asks": asks, "outcome": outcome,
                "tokens": sum(int(row["tokens_used"] or 0) for row in outcome["investigations"]),
                "published": [{"catalog_id": row["catalog_id"], "at": row["created_at"]} for row in published],
                "versions": outcome["versions"],
                "timing_s": {"question_to_publication": _seconds(asked_at, published_at), "publication_to_first_good_answer": _seconds(published_at, first_good),
                             "question_to_first_good_answer": _seconds(asked_at, first_good)},
            })
            if on_update:
                on_update(state)
    finally:
        desk.clear()
    return state


def _from_answers(conversation):
    """(label, cause code, explanation, the turn that carries the label) for a scenario judged on its own answers."""
    answered = [turn for turn in conversation["turns"] if turn["claims"]]
    verdict = conversation["judged"]["verdict"]
    if verdict == "correct":
        slowest = max(answered or conversation["turns"], key=lambda turn: turn["wall_ms"])
        label = "learned" if slowest["wall_ms"] <= FAST_MS else "learned_but_slow"
        return label, None, "answered " + verdict + ", slowest answering turn " + str(slowest["wall_ms"]) + "ms", slowest
    wrong = [turn for turn in answered if not all(claim["ok"] for claim in turn["claims"])]
    on_recipe = [turn for turn in wrong if turn["route"] in RECIPE_ROUTES and turn["records"]["execution_runs"]]
    if on_recipe:
        ran = sorted({str(run["catalog_id"]) for turn in on_recipe for run in turn["records"]["execution_runs"]})
        return WRONG, "recipe_wrong", "existing recipe answered wrong (" + verdict + ", ran " + ",".join(ran) + ")", on_recipe[0]
    routes = sorted({turn["route"] for turn in conversation["turns"]})
    carrier = next((turn for turn in reversed(conversation["turns"]) if turn["records"]["tasks"]), conversation["turns"][-1] if conversation["turns"] else None)
    return "not_learnable", None, verdict + " with no gap queued (routes " + ",".join(routes) + ")", carrier


def _gap_carrier(item):
    """The answer that carries a gap's label, and the published recipe that should have answered when none of it ran."""
    asks = item.get("asks") or []
    missed = [ask for ask in asks if not (ask["correct"] and ask["on_recipe"])]
    published = [row["catalog_id"] for row in item.get("published") or []]
    if item["class"] == WRONG and missed:
        ask = missed[0]
        return ask, None if ask["used_published"] else (published[0] if published else None)
    if item["class"] in ("learned", "learned_but_slow") and asks:
        return max(asks, key=lambda ask: ask["wall_ms"]), None
    return item["round1"], published[0] if item["class"] == WRONG and published else None


_CAUSE_CODES = (
    "retrieval_miss", "spec_chose_other_recipe", "recipe_wrong", "reviewer_approved_wrong_recipe",
    "definition_pin_wrong", "reply_misstated", "other",
)
_SUBJECT_PATH = {"run": "runs", "task": "tasks"}


def _cause_code(text):
    for part in str(text or "").split("; "):
        head = part.split(": ", 1)[0].strip()
        if head in _CAUSE_CODES:
            return head
    return None


def _turn_subject(turn):
    if not turn:
        return None, None, []
    runs = list((turn.get("records") or {}).get("execution_runs") or [])
    tasks = list((turn.get("records") or {}).get("tasks") or [])
    catalogs = [str(run["catalog_id"]) for run in runs]
    if runs:
        return "run", runs[-1]["run_id"], catalogs
    if tasks:
        return "task", tasks[-1]["task_id"], catalogs
    return None, None, catalogs


def _scenario_subject(conversations, gap):
    """A run, or else a task, from this scenario. gap prefers the turn that raised it."""
    for conversation in reversed(conversations):
        turns = [turn for turn in conversation["turns"] if not gap or gap in turn_gaps(turn)]
        for turn in reversed(turns or conversation["turns"]):
            kind, key, catalogs = _turn_subject(turn)
            if key is not None:
                return kind, key, catalogs
    return None, None, []


def label_scenarios(earlier, later, learned):
    """One label per scenario id. later (a replay) replaces earlier for the same id; gap classes come from learned.
    Each row names the run or task the admin API should label, and catalog_id when a published recipe was missed."""
    by_gap = {item["gap"]: item for item in learned["gaps"]}
    latest = {item["id"]: item for item in earlier}
    latest.update({item["id"]: item for item in later})
    rows = []
    for scenario_id, conversation in latest.items():
        touched = [item for item in earlier + later if item["id"] == scenario_id]
        gaps = sorted({gap for item in touched for turn in item["turns"] for gap in turn_gaps(turn)} & set(by_gap))
        classes = {gap: by_gap[gap]["class"] for gap in gaps}
        found = set(classes.values())
        cause_code = None
        catalog_id = None
        focus = None
        carrier = None
        if WRONG in found:
            label = WRONG
            focus = next(gap for gap in gaps if classes[gap] == WRONG)
            cause = "; ".join(gap + ": " + by_gap[gap]["reason"] for gap in gaps if classes[gap] == WRONG)
            cause_code = _cause_code(by_gap[focus]["reason"]) or "other"
            _carrier, catalog_id = _gap_carrier(by_gap[focus])
            if not catalog_id:
                published = [row["catalog_id"] for row in by_gap[focus].get("published") or []]
                catalog_id = published[0] if published else None
        elif "still_learning" in found:
            label = "still_learning"
            focus = next(gap for gap in gaps if classes[gap] == "still_learning")
            cause = "; ".join(gap + ": " + by_gap[gap]["reason"] for gap in gaps if classes[gap] == "still_learning")
        elif found & {"learned", "learned_but_slow"}:
            label = "learned_but_slow" if "learned_but_slow" in found else "learned"
            focus = next((gap for gap in gaps if classes[gap] == label), gaps[0])
            cause = "gaps " + ", ".join(gap + "=" + classes[gap] for gap in gaps)
        else:
            label, cause_code, explanation, carrier = _from_answers(conversation)
            cause = (cause_code + ": " + explanation) if cause_code else explanation
            if gaps and label == "not_learnable":
                cause = "; ".join(gap + ": " + by_gap[gap]["reason"] for gap in gaps)
                cause_code = None
        if focus is not None:
            kind, key, ran = _scenario_subject(touched, focus)
        else:
            kind, key, ran = _turn_subject(carrier)
            if key is None:
                kind, key, ran = _scenario_subject(touched, None)
        rows.append({"id": scenario_id, "tier": conversation["tier"], "label": label, "cause": cause, "cause_code": cause_code,
                     "catalog_id": catalog_id, "subject": kind, "subject_id": key, "ran": ran, "gaps": classes,
                     "source": conversation.get("ending", "")})
    rows.sort(key=lambda row: row["id"])
    total = len(rows) or 1
    share = {label: round(100.0 * sum(row["label"] == label for row in rows) / total, 1) for label in LABELS}
    return {"rows": rows, "share": share, "baseline": BASELINE, "flaw_threshold": FLAW_THRESHOLD, "flawed": share[WRONG] > FLAW_THRESHOLD,
            "counts": {label: sum(row["label"] == label for row in rows) for label in LABELS}}


def _detail(response):
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(payload, dict):
        return str(payload.get("detail") or payload.get("error") or "")[:300]
    return str(payload)[:300]


def _post_label(http, headers, row):
    """One admin label. The token stays in the header and is never copied onto the result."""
    if not row.get("subject_id"):
        return {"id": row["id"], "label": row["label"], "status": 0, "detail": "no run or task"}
    path = "/api/admin/complaints/" + _SUBJECT_PATH[row["subject"]] + "/" + str(row["subject_id"]) + "/label"
    body = {"label": row["label"], "note": str(row.get("cause") or "")[:2000]}
    if row.get("cause_code"):
        body["cause"] = row["cause_code"]
    if row.get("catalog_id"):
        body["catalog_id"] = row["catalog_id"]
    response = http.post(path, json=body, headers=headers)
    detail = _detail(response)
    if response.status_code == 409 and "catalog_id" in detail and "catalog_id" not in body and row.get("ran"):
        body["catalog_id"] = row["ran"][0]
        response = http.post(path, json=body, headers=headers)
        detail = _detail(response)
    out = {"id": row["id"], "label": row["label"], "subject": row.get("subject"), "subject_id": row.get("subject_id"),
           "status": response.status_code}
    if response.status_code >= 400:
        out["detail"] = detail
        return out
    out["label_id"] = (response.json() or {}).get("label_id")
    return out


def store_labels(base_url, rows):
    """POST each scenario label. Returns statuses only; the admin token is never included."""
    import os

    import httpx

    token = os.environ.get("LEON_PLATFORM_ADMIN_TOKEN") or ""
    if not token:
        return [{"id": row["id"], "label": row["label"], "status": 0, "detail": "LEON_PLATFORM_ADMIN_TOKEN is not set"} for row in rows]
    headers = {"X-Leon-Admin-Token": token, "X-Leon-Admin-User": "stress-harness"}
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=60) as http:
        return [_post_label(http, headers, row) for row in rows]


def fetch_health(base_url):
    """GET /api/admin/complaints/health?period=day. The admin token is not copied into the result."""
    import os

    import httpx

    token = os.environ.get("LEON_PLATFORM_ADMIN_TOKEN") or ""
    if not token:
        return {"error": "LEON_PLATFORM_ADMIN_TOKEN is not set"}
    headers = {"X-Leon-Admin-Token": token, "X-Leon-Admin-User": "stress-harness"}
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=60) as http:
        response = http.get("/api/admin/complaints/health", params={"period": "day"}, headers=headers)
    if response.status_code >= 400:
        return {"error": response.status_code, "detail": response.text[:300]}
    return response.json()
