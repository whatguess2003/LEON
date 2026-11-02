"""The text and JSON report of a stress run."""

import json
from collections import Counter

from fixtures.stress.scenarios import TIERS

VERDICTS = ("correct", "partially_correct", "wrong", "no_answer", "unnecessary_clarification", "failed_to_clarify")


def _pct(values, share):
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(share * len(ordered) + 0.5)) - 1))]


def _cut(text, size):
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= size else flat[:size] + " ..."


def _summary(results):
    lines = ["SUMMARY", "======="]
    scenarios = results["scenarios"]
    lines.append("verdicts per tier (" + ", ".join(VERDICTS) + ")")
    for tier in TIERS:
        items = [item for item in scenarios if item["tier"] == tier]
        if not items:
            continue
        counts = Counter(item["judged"]["verdict"] for item in items)
        right = sum(item["judged"]["figures_right"] for item in items)
        total = sum(item["judged"]["figures_total"] for item in items)
        lines.append("  " + tier.ljust(13) + " n=" + str(len(items)) + "  accuracy " + str(round(100.0 * counts["correct"] / len(items))) + "%"
                     + "  figures " + str(right) + "/" + str(total) + "  " + "  ".join(name + "=" + str(counts[name]) for name in VERDICTS if counts[name]))
    lines.append("")
    lines.append("turn latency per tier (ms): wall p50 / p95 / max, first byte p50")
    for tier in TIERS:
        walls = [turn["wall_ms"] for item in scenarios if item["tier"] == tier for turn in item["turns"]]
        firsts = [turn["ttfb_ms"] for item in scenarios if item["tier"] == tier for turn in item["turns"]]
        if walls:
            lines.append("  " + tier.ljust(13) + " turns=" + str(len(walls)) + "  " + str(_pct(walls, 0.5)) + " / " + str(_pct(walls, 0.95))
                         + " / " + str(max(walls)) + "   ttfb p50 " + str(_pct(firsts, 0.5)))
    lines.append("")
    lines.append("turns per scenario: " + "  ".join(item["id"] + "=" + str(len(item["turns"])) for item in scenarios))
    for tier in TIERS:
        counts = [len(item["turns"]) for item in scenarios if item["tier"] == tier]
        if counts:
            lines.append("  " + tier.ljust(13) + " mean " + str(round(sum(counts) / len(counts), 1)) + "  max " + str(max(counts)))
    lines.append("endings: " + ", ".join(name + "=" + str(count) for name, count in Counter(item["ending"] for item in scenarios).items()))
    lines.append("")
    routes = Counter(turn["route"] for item in scenarios for turn in item["turns"])
    lines.append("route distribution (turns): " + ", ".join(name + "=" + str(count) for name, count in routes.most_common()))
    recipes = Counter(str(run["catalog_id"]) + " v" + str(run["version"]) for item in scenarios for turn in item["turns"]
                      for run in turn["records"]["execution_runs"])
    lines.append("recipes executed: " + (", ".join(name + " x" + str(count) for name, count in recipes.most_common()) or "none"))
    gates = Counter(gate["decision"] for item in scenarios for turn in item["turns"] for gate in turn["gate"])
    lines.append("gate decisions: " + (", ".join(name + "=" + str(count) for name, count in gates.items()) or "none"))
    complaints = sum(len(turn["records"]["complaints"]) for item in scenarios for turn in item["turns"])
    lines.append("answer complaints recorded: " + str(complaints))
    lines.append("")
    if results.get("catalog") is not None:
        lines += catalog_lines(results["catalog"])
    if results.get("learning"):
        lines += learning_lines(results["learning"])
    return _summary_tail(results, lines)


def _ask(ask):
    return (ask["kind"].ljust(8) + " route " + str(ask["route"]) + "  " + str(ask["wall_ms"]) + "ms  "
            + ("correct" if ask["correct"] else "not correct") + (" fast" if ask["fast"] else " slow")
            + ("  ran " + ",".join(ask["ran"]) if ask["ran"] else "") + ("  used published" if ask["used_published"] else "")
            + "  claims " + ("  ".join(str(n) + " " + str(s) + (" ok" if ok else " X truth " + str(e)) for n, s, e, ok in ask["claims"]) or "none"))


def learning_lines(learning):
    """Per gap: round-1 answer (the learning signal), catalog/reviewer outcome, post-publication answers, class, timing, tokens."""
    gaps = learning["gaps"]
    counts = Counter(item["class"] for item in gaps)
    lines = ["LEARNING (a first-round miss is the learning signal; failure = published but the desk still answers wrong or slower than "
             + str(learning["fast_ms"]) + "ms)", "  classes: " + (", ".join(name + "=" + str(count) for name, count in counts.most_common()) or "no gaps")]
    labels = learning.get("labels")
    if labels:
        lines.append("")
        lines.append("  scenario labels vs baseline (" + str(len(labels["rows"])) + " scenarios; system flaw if published_but_wrong_or_unused > "
                     + str(labels["flaw_threshold"]) + "%)")
        lines.append("    " + "label".ljust(32) + "count  actual%  expected%")
        for label, expected in labels["baseline"].items():
            lines.append("    " + label.ljust(32) + str(labels["counts"][label]).rjust(5) + str(labels["share"][label]).rjust(9) + str(expected).rjust(11))
        lines.append("    verdict: " + ("SYSTEM FLAWED by this criterion" if labels["flawed"] else "not flawed by this criterion"))
        for row in labels["rows"]:
            lines.append("    " + row["id"] + " " + row["tier"].ljust(13) + row["label"].ljust(32) + _cut(row["cause"], 220))
        wrong = [row for row in labels["rows"] if row["label"] == "published_but_wrong_or_unused"]
        lines.append("  published_but_wrong_or_unused: " + ("; ".join(row["id"] + " (" + _cut(row["cause"], 200) + ")" for row in wrong) or "none"))
        stored = learning.get("stored") or []
        if stored:
            refused = [item for item in stored if item.get("status") != 200]
            lines.append("  labels stored: " + str(len(stored) - len(refused)) + "/" + str(len(stored))
                         + (("  failed " + ", ".join(item["id"] + " " + str(item.get("status")) + " " + _cut(item.get("detail"), 120) for item in refused)) if refused else ""))
        health = learning.get("health") or {}
        if health.get("window") is not None:
            system = health.get("system_health") or {}
            published = health.get("published_answers") or {}
            lines.append("  health API period=" + str(health.get("period")) + " flawed=" + str(system.get("flawed"))
                         + " flaw_rate_among_published_answers=" + str(published.get("flaw_rate"))
                         + " unlabeled_complaints=" + str(health.get("unlabeled_complaints")))
            lines.append("    " + "label".ljust(32) + "answers  actual%  expected%  over")
            for row in health["window"]:
                actual = row.get("actual_share")
                expected = row.get("expected_share")
                actual_pct = "" if actual is None else str(round(float(actual) * 100, 1))
                expected_pct = "" if expected is None else str(round(float(expected) * 100, 1))
                lines.append("    " + str(row.get("label")).ljust(32) + str(row.get("answers")).rjust(7) + actual_pct.rjust(9)
                             + expected_pct.rjust(11) + "  " + str(row.get("over_target")))
            if health.get("error"):
                lines.append("  health API error: " + _cut(health.get("detail") or health["error"], 200))
        elif health.get("error"):
            lines.append("  health API error: " + _cut(str(health.get("detail") or health["error"]), 200))
        lines.append("")
    learned = [item for item in gaps if item["class"] in ("learned", "learned_but_slow")]
    if learned:
        lines.append("  tokens per learned recipe: " + ", ".join(item["gap"].split("|")[-1] + " " + str(item["tokens"]) for item in learned)
                     + "  (mean " + str(round(sum(item["tokens"] for item in learned) / len(learned))) + ")")
    for item in gaps:
        timing = item["timing_s"]
        lines.append("")
        lines.append("  " + item["gap"] + "  [" + item["class"] + "]" + ("  " + item["reason"] if item["reason"] else ""))
        lines.append("    first asked " + item["scenario"] + " t" + str(item["turn"]) + " (" + str(item["touches"]) + " touches): " + _cut(item["round1"]["message"], 160))
        lines.append("    " + _ask(item["round1"]))
        outcome = item["outcome"]
        for row in outcome["investigations"]:
            lines.append("    investigation " + str(row["investigation_id"]) + " trace " + str(row["trace_id"]) + " " + str(row["status"]) + " step "
                         + str(row["current_step"]) + " next " + str(row["next_action"]) + " revisions " + str(row["revision_count"]) + " tokens " + str(row["tokens_used"]))
        for row in outcome["proposals"]:
            lines.append("    proposal " + str(row["proposal_id"]) + " " + str(row["action"]) + " " + str(row["catalog_id"]) + " " + str(row["status"]))
        for row in outcome["tests"]:
            lines.append("    test proposal " + str(row["proposal_id"]) + " baseline_ok " + str(row["baseline_ok"]) + " candidate_ok " + str(row["candidate_ok"]))
        for row in outcome["reviews"]:
            lines.append("    review proposal " + str(row["proposal_id"]) + " " + str(row["decision"]) + " (" + str(row["confidence"]) + ") unresolved "
                         + _cut(json.dumps(row["unresolved_questions"]), 160))
        for row in outcome["decisions"]:
            lines.append("    publication " + str(row["catalog_id"]) + " " + str(row["status"]) + " at " + str(row["created_at"]) + " | " + _cut(row["notes"], 160))
        for row in item["versions"]:
            lines.append("    version " + str(row["catalog_id"]) + " v" + str(row["version"]) + " scope " + str(row["scope"]) + " owner " + str(row["owner"] or "-"))
        for ask in item["asks"]:
            lines.append("    " + _ask(ask))
            lines.append("             asked: " + _cut(ask["message"], 160))
        lines.append("    timing s: question->publication " + str(timing["question_to_publication"]) + "  publication->first correct fast answer "
                     + str(timing["publication_to_first_good_answer"]) + "  question->first correct fast answer " + str(timing["question_to_first_good_answer"])
                     + "  tokens " + str(item["tokens"]))
    lines.append("")
    return lines


def catalog_lines(catalog):
    lines = ["catalog / reviewer workflow (polled " + str(catalog.get("polls")) + "x, finished=" + str(catalog.get("finished")) + ")"]
    for row in catalog["investigations"]:
        lines.append("  investigation " + str(row["investigation_id"]) + " trace " + str(row["trace_id"]) + " status " + str(row["status"])
                     + " step " + str(row["current_step"]) + " next " + str(row["next_action"]) + " proposal " + str(row["proposal_id"])
                     + " revisions " + str(row["revision_count"]) + " tokens " + str(row["tokens_used"]) + " closed " + str(row["closed_at"]))
    for row in catalog["proposals"]:
        lines.append("  proposal " + str(row["proposal_id"]) + " trace " + str(row["trace_id"]) + " " + str(row["action"]) + " " + str(row["catalog_id"])
                     + " status " + str(row["status"]) + " | " + _cut(row["diagnosis"], 200))
    for row in catalog["test_runs"]:
        lines.append("  test run proposal " + str(row["proposal_id"]) + " baseline_ok " + str(row["baseline_ok"]) + " candidate_ok " + str(row["candidate_ok"])
                     + " | " + _cut(row["notes"], 160))
    for row in catalog["reviews"]:
        lines.append("  review proposal " + str(row["proposal_id"]) + " " + str(row["decision"]) + " (" + str(row["confidence"]) + ", " + str(row["model"])
                     + ", findings " + str(row["findings"]) + ") unresolved " + _cut(json.dumps(row["unresolved_questions"]), 200))
    for row in catalog["decisions"]:
        lines.append("  publication proposal " + str(row["proposal_id"]) + " " + str(row["catalog_id"]) + " " + str(row["status"]) + " | " + _cut(row["notes"], 200))
    for row in catalog["new_versions"]:
        lines.append("  new catalog version " + str(row["catalog_id"]) + " v" + str(row["version"]) + " scope " + str(row.get("scope"))
                     + " owner " + str(row.get("owner") or "-") + " status " + str(row.get("status")) + " at " + str(row["created_at"]))
    for row in catalog["jobs"]:
        lines.append("  job " + str(row["job_kind"]) + " trace " + str(row["trace_id"]) + " " + str(row["job_status"]) + " attempts " + str(row["attempts"])
                     + (" error " + _cut(row["last_error"], 160) if row["last_error"] else ""))
    if not catalog["investigations"] and not catalog["proposals"]:
        lines.append("  no catalog investigations were opened")
    lines.append("")
    return lines


def _summary_tail(results, lines):
    scenarios = results["scenarios"]
    if results.get("usage_run") is not None:
        lines.append("agent_usage tokens during the run (incl. background): " + (", ".join(
            name + " calls " + str(item["calls"]) + " in " + str(item["input_tokens"]) + " out " + str(item["output_tokens"])
            for name, item in results["usage_run"].items()) or "none"))
        lines.append("harness LLM usage (" + results["simulator_model"] + "): " + ", ".join(
            name + " calls " + str(item["calls"]) + " in " + str(item["input_tokens"]) + " out " + str(item["output_tokens"])
            for name, item in results["usage_harness"].items()))
        lines.append("")
    failures = [item for item in scenarios if item["judged"]["verdict"] != "correct" or item["judged"]["flags"]]
    lines.append("failures and flags with root-cause hints")
    for item in failures:
        lines.append("  " + item["id"] + " " + item["judged"]["verdict"] + " " + ",".join(item["judged"]["flags"]))
        for hint in item["hints"][:8]:
            lines.append("      - " + _cut(hint, 300))
    if not failures:
        lines.append("  none")
    return lines


def _scenario(item):
    judged = item["judged"]
    lines = ["", "-" * 100, item["id"] + " [" + item["tier"] + "] verdict " + judged["verdict"] + "  flags " + (",".join(judged["flags"]) or "-")
             + "  figures " + str(judged["figures_right"]) + "/" + str(judged["figures_total"]) + "  turns " + str(len(item["turns"])) + "  ending " + item["ending"],
             "persona: " + item["persona"] + "   clarify expectation: " + item["clarify_expectation"]]
    lines += ["goal " + str(index + 1) + ": " + step for index, step in enumerate(item["goal"])]
    for index, turn in enumerate(item["turns"]):
        lines.append("")
        lines.append("  T" + str(index + 1) + " wall " + str(turn["wall_ms"]) + "ms ttfb " + str(turn["ttfb_ms"]) + "ms route " + turn["route"]
                     + " http " + str(turn["status"]) + (" refresh" if turn["refresh"] else "") + (" screen " + str(turn["screen"]) if turn["screen"] else ""))
        lines.append("    MANAGER: " + turn["manager"])
        lines.append("    DESK:    " + _cut(turn["reply"], 1500))
        records = turn["records"]
        for spec in records["specs"]:
            lines.append("    spec: route " + str(spec["route"]) + " recipes " + json.dumps(spec["recipe_ids"]) + " capability " + str(spec["capability"])
                         + " learn " + str(spec["learn"]) + (" clarify " + str(spec["clarification_kind"]) + "/" + str(spec["definition_key"]) if spec["clarification_kind"] else ""))
        for run in records["execution_runs"]:
            lines.append("    run: " + str(run["catalog_id"]) + " v" + str(run["version"]) + " rows " + str(run["row_count"]) + " " + str(run["elapsed_ms"]) + "ms"
                         + (" error " + _cut(run["error"], 200) if run["error"] else ""))
        for statement in turn["sql"][:3]:
            lines.append("    sql: " + _cut(statement, 600))
        if turn["gate"]:
            lines.append("    gate: " + json.dumps(turn["gate"], default=str))
        for row in records["investigations"]:
            lines.append("    investigation " + str(row["investigation_id"]) + " " + str(row["status"]) + (" (opened)" if row["opened_now"] else " (existing)"))
        for job in records["jobs"]:
            lines.append("    job: " + str(job["job_kind"]) + " " + str(job["job_status"]))
        for row in records["complaints"]:
            lines.append("    complaint " + str(row["complaint_id"]) + " " + str(row["status"]))
        if turn["awaiting"]:
            lines.append("    desk waiting on: " + str(turn["awaiting"].get("kind")) + " " + _cut(turn["awaiting"].get("question"), 200))
        if turn["usage"]:
            lines.append("    tokens: " + ", ".join(name + " " + str(value["input_tokens"]) + "/" + str(value["output_tokens"]) for name, value in turn["usage"].items()))
        if turn["claims"]:
            lines.append("    claims: " + "  ".join(claim["name"] + " " + str(claim["stated"]) + (" ok" if claim["ok"] else " X truth " + str(claim["expected"]))
                                               for claim in turn["claims"]))
    lines.append("")
    lines.append("  truth at end: " + json.dumps(item["final_truth"], default=str))
    for check in item["checks"]:
        lines.append("  state check " + ("ok" if check["ok"] else "FAILED") + ": " + check["label"] + " got " + json.dumps(check["got"], default=str)
                     + " expect " + json.dumps(check["expect"]))
    for hint in item["hints"]:
        lines.append("  hint: " + _cut(hint, 300))
    return lines


def write(out, results):
    out.with_suffix(".json").write_text(json.dumps(results, default=str, indent=1, ensure_ascii=False), encoding="utf-8")
    head = ["LEON desk stress run " + results["started"] + " on SANDBOX HOTEL (" + results["hotel"]["hotel_id"] + ") as " + results["manager"]["manager_name"],
            "desk " + results["base_url"] + "  simulator/judge " + results["simulator_model"] + "  max turns " + str(results["max_turns"])
            + "  scenarios done " + str(len(results["scenarios"])) + ("  finished " + results["finished"] if results.get("finished") else "  (in progress)"),
            "definitions on profile at start: " + ", ".join(item.get("key", "") + "=" + _cut(item.get("definition"), 80)
                                                            for item in (results["definitions_before"].get("answered") or [])), ""]
    body = head + _summary(results)
    for item in results["scenarios"]:
        body += _scenario(item)
    out.with_suffix(".txt").write_text("\n".join(body) + "\n", encoding="utf-8")
