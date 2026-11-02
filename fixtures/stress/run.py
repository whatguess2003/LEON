"""Turn-based desk stress run on SANDBOX HOTEL.

  python -m fixtures.stress.run --verify-truth              ground truth for every scenario, no desk, no LLM
  python -m fixtures.stress.run --selftest                  simulator and figure reader on canned text, no desk
  python -m fixtures.stress.run --only E01,I02 --poll-minutes 0 --label dryrun
  python -m fixtures.stress.run                             all 30 scenarios, then up to 20 minutes of catalog/reviewer polling

Writes _stress_sandbox_<timestamp>[_<label>].txt and .json in the repo root (a new pair per run, checkpointed after each scenario)."""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def _say(*parts):
    print(*parts, flush=True)


def _selected(scenarios, only, tier):
    chosen = [item for item in scenarios if (not only or item["id"] in only) and (not tier or item["tier"] == tier)]
    if only and len(chosen) != len(only):
        raise SystemExit("unknown scenario ids: " + ", ".join(sorted(set(only) - {item["id"] for item in chosen})))
    return chosen


def verify_truth(scenarios, hotel):
    from fixtures.stress import records
    params = {"h": hotel["hotel_id"], "guest": "STRESS GUEST CHECK", "started": hotel["now"]}
    failed = 0
    for scenario in scenarios:
        started = time.perf_counter()
        try:
            found = records.truth(scenario, params)
            missing = [figure["name"] for figure in scenario["figures"] if figure["name"] not in found]
            checked = records.checks(scenario, params)
            _say(scenario["id"], "ok" if not missing else "MISSING " + ",".join(missing), json.dumps(found, default=str),
                 ("checks " + json.dumps([item["got"] for item in checked], default=str)) if checked else "",
                 str(int((time.perf_counter() - started) * 1000)) + "ms")
            failed += bool(missing)
        except Exception as error:
            failed += 1
            _say(scenario["id"], "ERROR", str(error).splitlines()[0][:300])
    return failed


def selftest(scenarios, hotel):
    """Simulator opening lines and a canned reply through the reader, without the desk."""
    from fixtures.stress import agents, judge
    for scenario in scenarios[:2]:
        move = agents.manager_turn(scenario, [], 1, 8, "STRESS GUEST TEST")
        _say(scenario["id"], "simulator:", json.dumps(move, ensure_ascii=False))
        asked = agents.manager_turn(scenario, [{"manager": move["message"], "desk": "Which bookings should count for that?"}], 2, 8, "STRESS GUEST TEST")
        _say(scenario["id"], "answer to a clarification:", json.dumps(asked, ensure_ascii=False))
    canned = "Tonight 3 of 5 rooms are sold. Room revenue is RM 1,010.00 and the restaurant took RM171.20 net today; the open business date is 1 Nov."
    figures = [figure for scenario in scenarios if scenario["id"] in ("H01", "H10") for figure in scenario["figures"]]
    read = agents.read_figures(figures, "How are we doing today?", canned, hotel["today"])
    _say("reader:", json.dumps(read, ensure_ascii=False))
    expected = {"occupancy_pct": 60.0, "room_revenue_tonight": 1010.0, "fnb_net_sales": 171.2, "open_business_date": "2026-11-01"}
    kinds = {figure["name"]: figure["kind"] for figure in figures}
    ok = {name: bool(read.get(name)) and judge.matches(kinds[name], value, read[name]) for name, value in expected.items()}
    _say("reader matches:", ok, "unstated stayed unstated:", "combined_revenue" not in read and "tax_collected" not in read)
    _say("usage:", agents.USAGE)
    return 0 if all(ok.values()) else 1


def play_turn(desk, scenario, hotel, params, message, held_before):
    """Send one manager message and collect the reply, the records the turn wrote, fresh ground truth and the figures stated."""
    from fixtures.stress import agents, judge, records

    before = records.usage(hotel["hotel_id"])
    sent = desk.send(message)
    after = records.usage(hotel["hotel_id"])
    found = records.turn_records(hotel["hotel_id"], hotel["user_id"], before["now"], after["now"])
    truth = records.truth(scenario, params)
    try:
        read = agents.read_figures(scenario["figures"], message, sent["reply"], hotel["today"]) if sent["reply"] else {}
    except Exception as error:
        read = {"_error": {"stated": False, "number": None, "text": str(error)[:200]}}
    turn = dict(sent, manager=message, at=before["now"], truth=truth, read={key: value for key, value in read.items() if not key.startswith("_")},
                records=found, awaiting=found["awaiting"], route=records.route_of(found, held_before), gate=records.gate_of(found),
                sql=records.sql_of(found), usage=records.usage_delta(before, after))
    turn["claims"] = judge.claims(scenario, turn)
    return turn


def converse(desk, scenario, hotel, max_turns, stamp):
    from fixtures.stress import agents, judge, records

    guest = "STRESS GUEST " + stamp
    desk.clear()
    opened = records.usage(hotel["hotel_id"])
    params = {"h": hotel["hotel_id"], "guest": guest, "started": opened["now"]}
    transcript, turns, ending = [], [], "max_turns"
    for number in range(1, max_turns + 1):
        try:
            move = agents.manager_turn(scenario, transcript, number, max_turns, guest)
        except Exception as error:
            ending = "simulator_error: " + str(error)[:200]
            break
        if move["status"] != "continue" or not move["message"].strip():
            ending = move["status"]
            break
        turn = play_turn(desk, scenario, hotel, params, move["message"], turns[-1]["awaiting"] if turns else None)
        turn.update(step=move["step"], simulator_reason=move["reason"])
        turns.append(turn)
        transcript.append({"manager": move["message"], "desk": turn["reply"]})
        _say("   ", scenario["id"], "t" + str(number), turn["route"], str(turn["wall_ms"]) + "ms",
             " ".join(claim["name"] + ("=" if claim["ok"] else "!=") + str(claim["stated"]) for claim in turn["claims"]))
    checks = records.checks(scenario, params)
    judged = judge.verdict(scenario, turns, checks)
    return {
        "id": scenario["id"], "tier": scenario["tier"], "goal": [step.replace("{guest}", guest) for step in scenario["goal"]],
        "persona": scenario["persona"], "clarify_expectation": scenario["clarify"], "ending": ending, "turns": turns, "checks": checks,
        "judged": judged, "hints": judge.hints(scenario, turns, judged, ending), "final_truth": records.truth(scenario, params),
        "started_db": opened["now"],
    }


def poll_catalog(trace_ids, since, minutes, interval_s):
    """Background Catalog Agent and reviewer outcomes. Stops when every investigation is closed and no catalog job is open."""
    from fixtures.stress import records
    deadline = time.monotonic() + minutes * 60
    snapshot = records.catalog_outcomes(trace_ids, since)
    polls = 1
    while trace_ids and time.monotonic() < deadline:
        open_jobs = [job for job in snapshot["jobs"] if job["job_status"] not in ("done", "completed", "failed", "dead", "cancelled")]
        open_investigations = [row for row in snapshot["investigations"] if not row["closed_at"]]
        _say("poll", polls, "investigations open", len(open_investigations), "jobs open", len(open_jobs),
             "proposals", len(snapshot["proposals"]), "reviews", len(snapshot["reviews"]), "decisions", len(snapshot["decisions"]))
        if not open_jobs and not open_investigations:
            break
        time.sleep(interval_s)
        snapshot = records.catalog_outcomes(trace_ids, since)
        polls += 1
    open_jobs = [job for job in snapshot["jobs"] if job["job_status"] not in ("done", "completed", "failed", "dead", "cancelled")]
    return dict(snapshot, polls=polls, finished=not open_jobs and all(row["closed_at"] for row in snapshot["investigations"]))


def main():
    load_dotenv(ROOT / ".env")
    sys.path.insert(0, str(ROOT))
    from fixtures.stress import agents, learning, records, report
    from fixtures.stress.desk import Desk
    from fixtures.stress.scenarios import SCENARIOS

    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    parser.add_argument("--tier", default="", choices=["", "easy", "intermediate", "hard"])
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--poll-minutes", type=float, default=20)
    parser.add_argument("--poll-interval", type=int, default=60)
    parser.add_argument("--learning-minutes", type=float, default=25,
                        help="extra wait for publications, then re-ask each published gap verbatim and reworded (0 skips)")
    parser.add_argument("--label", default="")
    parser.add_argument("--verify-truth", action="store_true")
    parser.add_argument("--selftest", action="store_true", help="simulator and reader only, no desk")
    args = parser.parse_args()

    scenarios = _selected(SCENARIOS, [item.strip() for item in args.only.split(",") if item.strip()], args.tier)
    hotel = records.sandbox()
    if args.verify_truth:
        raise SystemExit(verify_truth(scenarios, hotel))
    if args.selftest:
        raise SystemExit(selftest([item for item in SCENARIOS if item["id"] in ("E01", "I10", "H01", "H10")], hotel))

    desk = Desk(args.base_url)
    if not desk.up():
        raise SystemExit("desk server is not reachable at " + args.base_url)
    me = desk.sign_in()
    if me["hotel_id"] != hotel["hotel_id"] or me["user_id"] != hotel["user_id"]:
        raise SystemExit("signed in to the wrong hotel or user")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / ("_stress_sandbox_" + stamp + ("_" + args.label if args.label else ""))
    started = records.usage(hotel["hotel_id"])
    results = {
        "started": stamp, "base_url": args.base_url, "hotel": {key: hotel[key] for key in ("hotel_id", "timezone", "currency", "today")},
        "manager": {key: me[key] for key in ("user_id", "manager_name", "role")}, "simulator_model": agents.MODEL + "/" + agents.EFFORT,
        "max_turns": args.max_turns, "definitions_before": desk.definitions(), "scenarios": [],
    }
    _say("run", stamp, "scenarios", len(scenarios), "->", out.name + ".txt")
    for scenario in scenarios:
        _say(scenario["id"], scenario["tier"], scenario["goal"][0][:90])
        try:
            done = converse(desk, scenario, hotel, args.max_turns, stamp[-6:])
        except Exception as error:
            done = {"id": scenario["id"], "tier": scenario["tier"], "goal": scenario["goal"], "persona": scenario["persona"],
                    "clarify_expectation": scenario["clarify"], "ending": "harness_error: " + str(error)[:300], "turns": [], "checks": [],
                    "judged": {"verdict": "no_answer", "flags": ["harness_error"], "final": {}, "wrong_claims": [], "figures_right": 0,
                               "figures_total": len(scenario["figures"]), "clarification_asked": False},
                    "hints": ["harness error: " + str(error)[:300]], "final_truth": {}}
        results["scenarios"].append(done)
        _say("  ->", done["judged"]["verdict"], done["ending"], "turns", len(done["turns"]))
        report.write(out, results)
    desk.clear()
    traces = sorted({trace["trace_id"] for item in results["scenarios"] for turn in item["turns"] for trace in turn["records"]["traces"]})
    results["catalog"] = poll_catalog(traces, started["now"], args.poll_minutes, args.poll_interval)
    if args.learning_minutes:
        results["learning"] = learning.run(desk, hotel, results["scenarios"], {item["id"]: item for item in scenarios},
                                           args.learning_minutes, args.poll_interval, _say)
        results["learning"]["labels"] = learning.label_scenarios(results["scenarios"], [], results["learning"])
    results["usage_run"] = records.usage_delta(started, records.usage(hotel["hotel_id"]))
    results["usage_harness"] = agents.USAGE
    results["definitions_after"] = desk.definitions()
    results["finished"] = datetime.now().strftime("%Y%m%d_%H%M%S")
    report.write(out, results)
    _say("wrote", out.name + ".txt", "and .json")


if __name__ == "__main__":
    main()
