"""Replay, verbatim, the turns of an earlier stress run that stopped on a limit (gate deferred on the daily token budget, or a
recursion limit in the live SQL agent). Each conversation is replayed in a fresh chat with the same manager messages up to its
last such turn, judged against fresh ground truth. Then the learning loop is scored (fixtures.stress.learning): the gaps these
turns raised (and those of the source conversations holding the watched traces) are polled until published or settled, every
published gap is asked again verbatim and once reworded, and each gap is classified.

  python -m fixtures.stress.replay --source _stress_sandbox_20261101_083137.json --watch-trace 471 --learning-minutes 45"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def limits_hit(turn):
    out = [gate["decision"] + ":" + str(gate.get("reason")) for gate in turn["gate"] if str(gate.get("decision", "")).startswith("deferred")]
    for item in turn["records"]["evidence"]:
        for value in (item.get("stall_reason"), item.get("error")):
            if value and ("limit" in str(value).lower() or "budget" in str(value).lower()):
                out.append(str(value)[:120])
    return out


def stopped_on_raised_limit(turn):
    """The limits raised for this replay: the gate's daily token budget and the SQL agent's recursion limit."""
    budget = any(gate.get("reason") == "deferred_token_budget" for gate in turn["gate"])
    recursion = any("recursion limit" in str(item.get(field) or "").lower() for item in turn["records"]["evidence"] for field in ("stall_reason", "error"))
    return budget or recursion


def targets(source):
    found = []
    for item in source["scenarios"]:
        picked = [index for index, turn in enumerate(item["turns"]) if stopped_on_raised_limit(turn)]
        if picked:
            found.append((item, picked))
    return found


def replay(desk, scenario, item, picked, hotel, label):
    from fixtures.stress import judge, records
    from fixtures.stress.run import _say, play_turn

    desk.clear()
    opened = records.usage(hotel["hotel_id"])
    params = {"h": hotel["hotel_id"], "guest": "STRESS GUEST REPLAY", "started": opened["now"]}
    turns = []
    for index in range(max(picked) + 1):
        turn = play_turn(desk, scenario, hotel, params, item["turns"][index]["manager"], turns[-1]["awaiting"] if turns else None)
        turn.update(step=index + 1, simulator_reason="verbatim replay of turn " + str(index + 1), target=index in picked)
        turns.append(turn)
        _say("   ", label, item["id"], "t" + str(index + 1), "*" if index in picked else " ", turn["route"], str(turn["wall_ms"]) + "ms",
             " ".join(claim["name"] + ("=" if claim["ok"] else "!=") + str(claim["stated"]) for claim in turn["claims"]), limits_hit(turn) or "")
    checks = records.checks(scenario, params)
    judged = judge.verdict(scenario, turns, checks)
    return {"id": item["id"], "tier": item["tier"], "goal": item["goal"], "persona": item["persona"] + " (" + label + ")",
            "clarify_expectation": item["clarify_expectation"], "ending": label, "turns": turns, "checks": checks, "judged": judged,
            "hints": judge.hints(scenario, turns, judged, label), "final_truth": records.truth(scenario, params), "started_db": opened["now"]}


def _side(turn):
    return {"route": turn["route"], "wall_ms": turn["wall_ms"], "reply": turn["reply"],
            "claims": [(claim["name"], claim["stated"], claim["expected"], claim["ok"]) for claim in turn["claims"]],
            "gate": turn["gate"], "limits": limits_hit(turn),
            "recipes": [str(run["catalog_id"]) + " v" + str(run["version"]) for run in turn["records"]["execution_runs"]]}


def _cut(text, size):
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= size else flat[:size] + " ..."


def write(out, results):
    from fixtures.stress import report

    out.with_suffix(".json").write_text(json.dumps(results, default=str, indent=1, ensure_ascii=False), encoding="utf-8")
    lines = ["LEON desk limit replay " + results["started"] + " of " + results["source"] + " on SANDBOX HOTEL as " + results["manager"],
             "desk " + results["base_url"] + ("  finished " + results["finished"] if results.get("finished") else "  (in progress)"), "",
             "BEFORE vs AFTER per replayed question (same message, same preceding turns, fresh chat, fresh ground truth)", ""]
    for row in results["compare"]:
        lines.append(row["id"] + " t" + str(row["turn"]) + " [" + row["phase"] + "] " + _cut(row["message"], 140))
        for name in ("before", "after"):
            side = row[name]
            lines.append("   " + name.ljust(6) + " route " + side["route"] + "  " + str(side["wall_ms"]) + "ms  recipes " + (", ".join(side["recipes"]) or "-")
                         + "  limits " + (", ".join(side["limits"]) or "none"))
            lines.append("          claims " + ("  ".join(str(n) + " " + str(s) + (" ok" if ok else " X truth " + str(e)) for n, s, e, ok in side["claims"]) or "none"))
            lines.append("          reply  " + _cut(side["reply"], 300))
        lines.append("")
    lines.append("scenario verdicts: " + "  ".join(item["id"] + " " + item["ending"] + " " + item["judged"]["verdict"]
                                                     + " (" + str(item["judged"]["figures_right"]) + "/" + str(item["judged"]["figures_total"]) + ")"
                                                     for item in results["scenarios"]))
    lines.append("before (original run): " + "  ".join(key + " " + value for key, value in results["before_verdicts"].items()))
    lines.append("")
    if results.get("catalog"):
        lines += report.catalog_lines(results["catalog"])
    if results.get("learning"):
        lines += report.learning_lines(results["learning"])
    lines.append("watched investigation(s) before replay: " + json.dumps(results.get("watched_before"), default=str))
    if results.get("usage_run") is not None:
        lines.append("agent_usage tokens during the replay (incl. background): " + (", ".join(
            name + " calls " + str(item["calls"]) + " in " + str(item["input_tokens"]) + " out " + str(item["output_tokens"])
            for name, item in results["usage_run"].items()) or "none"))
        lines.append("judge usage: " + json.dumps(results.get("usage_harness")))
    lines.append("limits still hit: " + (json.dumps(results.get("limits_still_hit"), default=str) if results.get("limits_still_hit") else "none"))
    for item in results["scenarios"]:
        lines += report._scenario(item)
    out.with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    load_dotenv(ROOT / ".env")
    sys.path.insert(0, str(ROOT))
    from fixtures.stress import agents, learning, records
    from fixtures.stress.desk import Desk
    from fixtures.stress.run import _say, poll_catalog
    from fixtures.stress.scenarios import SCENARIOS

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--resume", default="", help="an interrupted _replay.json whose finished conversations are kept, not replayed")
    parser.add_argument("--watch-trace", default="", help="comma-separated earlier trace ids to report alongside (not forced)")
    parser.add_argument("--poll-minutes", type=float, default=0, help="catalog poll over the replay traces before the learning step")
    parser.add_argument("--learning-minutes", type=float, default=45)
    parser.add_argument("--poll-interval", type=int, default=60)
    args = parser.parse_args()

    source = json.loads((ROOT / args.source).read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in SCENARIOS}
    hotel = records.sandbox()
    desk = Desk(args.base_url)
    if not desk.up():
        raise SystemExit("desk server is not reachable at " + args.base_url)
    me = desk.sign_in()
    if me["hotel_id"] != hotel["hotel_id"] or me["user_id"] != hotel["user_id"]:
        raise SystemExit("signed in to the wrong hotel or user")
    watched = [int(item) for item in args.watch_trace.split(",") if item.strip()]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / ("_stress_sandbox_" + stamp + "_replay")
    started = records.usage(hotel["hotel_id"])
    picked = targets(source)
    results = {"started": stamp, "source": args.source, "base_url": args.base_url, "manager": me["manager_name"], "scenarios": [], "compare": [],
               "targets": {item["id"]: [index + 1 for index in indexes] for item, indexes in picked},
               "before_verdicts": {item["id"]: item["judged"]["verdict"] for item, _indexes in picked},
               "watched_before": records.catalog_outcomes(watched, started["now"])["investigations"] if watched else []}
    _say("replay", stamp, "targets", json.dumps(results["targets"]), "->", out.name + ".txt")

    def compare(item, indexes, done, phase):
        for index in indexes:
            results["compare"].append({"id": item["id"], "turn": index + 1, "phase": phase, "message": item["turns"][index]["manager"],
                                       "before": _side(item["turns"][index]), "after": _side(done["turns"][index])})

    kept = {item["id"]: item for item in json.loads((ROOT / args.resume).read_text(encoding="utf-8"))["scenarios"]
            if item["ending"] == "replay"} if args.resume else {}
    if kept:
        results["resumed_from"] = {"file": args.resume, "scenarios": sorted(kept)}
    replayed = []
    for item, indexes in picked:
        done = kept.get(item["id"]) or replay(desk, by_id[item["id"]], item, indexes, hotel, "replay")
        results["scenarios"].append(done)
        replayed.append((item, indexes, done))
        compare(item, indexes, done, "replay")
        write(out, results)
    desk.clear()
    traces = sorted({trace["trace_id"] for _item, _indexes, done in replayed for turn in done["turns"] for trace in turn["records"]["traces"]} | set(watched))
    results["catalog"] = poll_catalog(traces, started["now"], args.poll_minutes, args.poll_interval)
    write(out, results)

    later = [done for _item, _indexes, done in replayed]

    def checkpoint(state):
        results["learning"] = state
        write(out, results)

    results["learning"] = learning.run(desk, hotel, source["scenarios"] + later, by_id, args.learning_minutes, args.poll_interval, _say, checkpoint)
    results["learning"]["labels"] = learning.label_scenarios(source["scenarios"], later, results["learning"])
    write(out, results)
    results["learning"]["stored"] = learning.store_labels(args.base_url, results["learning"]["labels"]["rows"])
    results["learning"]["health"] = learning.fetch_health(args.base_url)
    write(out, results)
    desk.clear()
    results["limits_still_hit"] = [{"id": done["id"], "turn": index + 1, "phase": done["ending"], "limits": limits_hit(turn)}
                                   for done in results["scenarios"] for index, turn in enumerate(done["turns"]) if limits_hit(turn)]
    results["catalog_final"] = records.catalog_outcomes(traces, started["now"])
    results["catalog"] = dict(results["catalog_final"], polls=results["catalog"]["polls"], finished=results["catalog"]["finished"])
    results["usage_run"] = records.usage_delta(started, records.usage(hotel["hotel_id"]))
    results["usage_harness"] = agents.USAGE
    results["finished"] = datetime.now().strftime("%Y%m%d_%H%M%S")
    write(out, results)
    _say("wrote", out.name + ".txt", "and .json")


if __name__ == "__main__":
    main()
