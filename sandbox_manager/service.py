"""Ask the running desk, judge the reply, and serve this process's status page."""

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sandbox_manager.config import (
    DAILY_CAP,
    FLAG_SENTENCES,
    HOTEL_ID,
    HOTEL_NAME,
    USER_ID,
    hotel_clock,
    local_day,
    main_url,
    public_text,
    status_host,
    status_port,
)
from sandbox_manager.reads import (
    audit_triggered,
    latest_audit,
    public_tables,
    recipe_cards,
    recipe_turns,
    recipes_this_turn,
)
from sandbox_manager.reask import TEXT_PATH as REASK_PATH
from sandbox_manager.report import TEXT_PATH, write_report
from sandbox_manager.store import Store


def _packet(store):
    clock = hotel_clock()
    clock.update({
        "hotel_name": HOTEL_NAME,
        "operations": (
            "SANDBOX HOTEL is a small property with DELUXE and FAMILY rooms. "
            "Bookings, room nights, guests, payments, rates, and housekeeping rows exist around the hotel-local today. "
            "Questions must be answerable from this hotel's database."
        ),
        "tables": public_tables(),
        "already_stored": store.recent_texts(120),
    })
    return clock


def _session(store):
    from fixtures.stress.desk import Desk

    desk = Desk(main_url(), timeout_s=900)
    if not desk.up():
        store.event("desk_down", "desk did not answer")
        store.set_meta("desk", "down")
        return None, {}, {"main": "down", "waited": True}
    try:
        me = desk.sign_in(HOTEL_NAME)
    except Exception:
        store.event("desk_down", "sign-in failed")
        store.set_meta("desk", "down")
        return None, {}, {"main": "down", "waited": True}
    store.set_meta("desk", "up")
    return desk, me or {}, None


def _ensure_question(store):
    from sandbox_manager.sol import draft

    found = store.next_unused()
    if found:
        return found
    store.event("drafting", "unused pool empty")
    store.set_meta("phase", "drafting questions")
    print("drafting questions", flush=True)
    try:
        draft(store, _packet(store))
    except Exception as exc:
        store.event("web_search_failed", public_text(exc))
        return None
    return store.next_unused()


def _clear(desk, store):
    try:
        desk.clear()
    except Exception:
        store.event("clear_failed", "chat clear failed")


def _send(desk, message):
    try:
        return desk.send(message)
    except Exception:
        return None


def _accepted(sent):
    status = sent.get("status") if isinstance(sent, dict) else None
    return isinstance(status, int) and 200 <= status < 300


def _latency(sent):
    value = sent.get("wall_ms") if isinstance(sent, dict) else None
    if isinstance(value, int):
        return value
    return None


def _flag_sentence(verdict, chosen):
    """Sol already chose the verdict. A mismatch or unsaid sends one of the two flag sentences."""
    if not {"mismatch": True, "unsaid": True}.get(verdict):
        return ""
    text = " ".join(str(chosen or "").split())
    return {phrase: phrase for phrase in FLAG_SENTENCES}.get(text, FLAG_SENTENCES[0])


def _send_flag(desk, hotel_id, user_id, sentence, store):
    if not sentence:
        return "no"
    previous = latest_audit(hotel_id, user_id)
    sent = _send(desk, sentence)
    if sent is None or not _accepted(sent):
        store.event("desk_down", "flag request failed")
        return "no"
    return "yes" if audit_triggered(hotel_id, user_id, previous) else "no"


def _judgment(question, reply, cards, hotel_id):
    from sandbox_manager.sol import judge

    try:
        found = judge(question, reply, cards, hotel_id)
    except Exception as exc:
        return {"verdict": "", "recipe_flawed": False, "flag_sentence": "", "ground_figure": "", "note": public_text(exc)}
    if not isinstance(found, dict):
        return {"verdict": "", "recipe_flawed": False, "flag_sentence": "", "ground_figure": "", "note": ""}
    return found


def _maybe_publish(publish, judgment, question, reply, cards, hotel_id, ids):
    from sandbox_manager.sol import revise

    flawed = judgment.get("recipe_flawed") is True
    actions = {
        True: lambda: revise(question, reply, cards, judgment, hotel_id, ids),
        False: lambda: {"published": "no"},
    }
    try:
        outcome = actions[bool(flawed) and bool(publish)]()
    except Exception as exc:
        return {"published": "no", "error": public_text(exc)}
    return outcome if isinstance(outcome, dict) else {"published": "no"}


def _recipe_fields(cards):
    ids = []
    versions = []
    for card in cards or []:
        catalog_id = str(card.get("id") or "")
        if not catalog_id:
            continue
        ids.append(catalog_id)
        versions.append("" if card.get("version") is None else str(card.get("version")))
    return ",".join(ids), ",".join(versions)


def _reask_fields(question_text, latency, verdict, recipe_id, recipe_version, sentence, sql_figure):
    return {
        "question": question_text,
        "latency_ms": latency,
        "verdict": verdict,
        "recipe_id": recipe_id,
        "recipe_version": recipe_version,
        "sentence": sentence,
        "sql_figure": sql_figure,
    }


def _reask_judged(store, hotel_id, user_id, question_text, sent, before, latency):
    reply = str(sent.get("reply") or "")
    ids = recipes_this_turn(before, recipe_turns(hotel_id, user_id))
    cards = recipe_cards(ids)
    store.set_meta("phase", "judging the re-ask")
    print("judging the re-ask", flush=True)
    judgment = _judgment(question_text, reply, cards, hotel_id)
    named = {"match": "match", "mismatch": "mismatch", "unsaid": "unsaid"}.get(judgment.get("verdict"), "")
    figure = public_text(judgment.get("ground_figure") or "").strip()
    verdict = {"match": "match"}.get(named if figure else "", "miss")
    recipe_id, recipe_version = _recipe_fields(cards)
    said = " ".join(reply.split()) or "nothing."
    return _reask_fields(
        question_text,
        latency,
        verdict,
        recipe_id,
        recipe_version,
        "Leon said " + said,
        "SQL: " + figure,
    )


def _reask_turn(store, desk, hotel_id, user_id, question_text):
    """One fresh desk thread. No flag sentence and no recipe publish."""
    _clear(desk, store)
    before = recipe_turns(hotel_id, user_id)
    store.set_meta("phase", "re-asking the desk")
    print("re-asking the desk", flush=True)
    sent = _send(desk, question_text)
    latency = _latency(sent)
    finished = isinstance(sent, dict) and _accepted(sent) and sent.get("finished") is True
    paths = {
        True: lambda: _reask_judged(store, hotel_id, user_id, question_text, sent, before, latency),
        False: lambda: _reask_fields(question_text, latency, "miss", "", "", "Leon said nothing.", "SQL:"),
    }
    return paths[finished]()


def _reask(store, desk, hotel_id, user_id, question_text):
    fields = _reask_fields(question_text, None, "miss", "", "", "Leon said nothing.", "SQL:")
    try:
        fields = _reask_turn(store, desk, hotel_id, user_id, question_text)
    except Exception as exc:
        store.event("reask_failed", public_text(exc))
    store.record_reask(fields)


def _ask_one(store, publish):
    desk, me, down = _session(store)
    if down:
        return down
    hotel_id = str(me.get("hotel_id") or HOTEL_ID)
    user_id = str(me.get("user_id") or USER_ID)
    question = _ensure_question(store)
    if question is None:
        return {"main": "up", "waited": True, "reason": "pool empty"}
    _clear(desk, store)
    before = recipe_turns(hotel_id, user_id)
    store.set_meta("phase", "asking the desk")
    print("asking the desk", flush=True)
    sent = _send(desk, question["text"])
    if sent is None or not _accepted(sent):
        store.event("desk_down", "question request failed")
        store.set_meta("desk", "down")
        return {"main": "down", "waited": True}
    store.set_meta("desk", "up")
    if sent.get("finished") is not True:
        store.set_meta("phase", "recording")
        store.finish_ask(question["id"], {
            "latency_ms": _latency(sent),
            "verdict": "incomplete",
            "recipe_id": "",
            "recipe_version": "",
            "auditor_triggered": "no",
            "published": "no",
            "published_catalog_id": "",
            "published_version": "",
            "note": "",
        })
        return {
            "main": "up",
            "waited": False,
            "latency_ms": _latency(sent),
            "verdict": "incomplete",
            "auditor_triggered": "no",
            "flag": "",
            "published": "no",
            "recipe_id": "",
            "recipe_version": "",
            "published_catalog_id": "",
            "published_version": "",
        }
    reply = str(sent.get("reply") or "")
    ids = recipes_this_turn(before, recipe_turns(hotel_id, user_id))
    cards = recipe_cards(ids)
    store.set_meta("phase", "judging")
    print("judging", flush=True)
    judgment = _judgment(question["text"], reply, cards, hotel_id)
    named = {"match": "match", "mismatch": "mismatch", "unsaid": "unsaid"}.get(judgment.get("verdict"), "")
    figure = public_text(judgment.get("ground_figure") or "").strip()
    verdict = named if figure else ""
    sentence = _flag_sentence(verdict, judgment.get("flag_sentence")) if figure else ""
    if sentence:
        store.set_meta("phase", "flagging the reply")
        print("flagging", flush=True)
    auditor = _send_flag(desk, hotel_id, user_id, sentence, store)
    store.set_meta("phase", "recording")
    outcome = _maybe_publish(bool(publish) and bool(figure), judgment, question["text"], reply, cards, hotel_id, ids)
    recipe_id, recipe_version = _recipe_fields(cards)
    published = "yes" if outcome.get("published") == "yes" else "no"
    note = public_text(judgment.get("note") or outcome.get("error") or outcome.get("note") or "")
    if figure and figure not in note:
        note = (note + " figure: " + figure)[:500]
    store.finish_ask(question["id"], {
        "latency_ms": _latency(sent),
        "verdict": verdict,
        "recipe_id": recipe_id,
        "recipe_version": recipe_version,
        "auditor_triggered": auditor,
        "published": published,
        "published_catalog_id": "" if published != "yes" else str(outcome.get("catalog_id") or ""),
        "published_version": "" if published != "yes" else str(outcome.get("version") or ""),
        "note": note,
    })
    result = {
        "main": "up",
        "waited": False,
        "latency_ms": _latency(sent),
        "verdict": verdict,
        "auditor_triggered": auditor,
        "flag": sentence,
        "published": published,
        "recipe_id": recipe_id,
        "recipe_version": recipe_version,
        "published_catalog_id": "" if published != "yes" else str(outcome.get("catalog_id") or ""),
        "published_version": "" if published != "yes" else str(outcome.get("version") or ""),
    }
    follow = {"yes": _reask}.get(published)
    if follow is not None:
        try:
            follow(store, desk, hotel_id, user_id, question["text"])
        except Exception as exc:
            store.event("reask_failed", public_text(exc))
    return result


def ask_one(store, publish):
    try:
        return _ask_one(store, publish)
    except Exception as exc:
        store.event("cycle_error", public_text(exc))
        return {"main": "down", "waited": True, "error": public_text(exc)}


def _cell(value):
    text = "—" if value is None or value == "" else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cards(snapshot):
    fields = (
        ("Asked today", str(snapshot["asked_today"]) + " / " + str(snapshot["daily_cap"])),
        ("Latency last", snapshot["latency_last_ms"]),
        ("Latency median", snapshot["latency_median_ms"]),
        ("Mismatch", snapshot["mismatch_count"]),
        ("Unsaid", snapshot["unsaid_count"]),
        ("Auditor triggered", snapshot["auditor_trigger_count"]),
    )
    parts = []
    for label, value in fields:
        shown = _cell(value)
        if label.startswith("Latency") and value is not None:
            shown = _cell(value) + " ms"
        parts.append("<div class=\"card\"><div>" + _cell(label) + "</div><div class=\"num\">" + shown + "</div></div>")
    return "".join(parts)


def _table(asks):
    rows = []
    for ask in asks or []:
        rows.append(
            "<tr><td>" + _cell(ask.get("asked_at"))
            + "</td><td>" + _cell(ask.get("latency_ms"))
            + "</td><td>" + _cell(ask.get("verdict"))
            + "</td><td>" + _cell(ask.get("recipe_id"))
            + "</td><td>" + _cell(ask.get("recipe_version"))
            + "</td><td>" + _cell(ask.get("auditor_triggered"))
            + "</td><td>" + _cell(ask.get("published"))
            + "</td></tr>"
        )
    if not rows:
        rows.append("<tr><td colspan=\"7\">No questions asked yet today.</td></tr>")
    return (
        "<table><thead><tr><th>Asked</th><th>Latency ms</th><th>Verdict</th>"
        "<th>Recipe</th><th>Version</th><th>Auditor</th><th>Published</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
    )


def _page(snapshot):
    event = snapshot.get("latest_event") or {}
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Sandbox manager</title>
<meta http-equiv="refresh" content="15">
<style>
body { font: 16px/1.45 system-ui, sans-serif; margin: 2rem; color: #1c1917; background: #fafaf9; }
h1 { font-size: 1.4rem; margin: 0 0 0.3rem; }
p { margin: 0.2rem 0 1rem; color: #44403c; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 0.8rem; margin: 0 0 1.4rem; }
.card { background: white; border: 1px solid #e7e5e4; border-radius: 8px; padding: 0.8rem 1rem; }
.num { font-size: 1.5rem; font-weight: 650; margin-top: 0.2rem; }
table { border-collapse: collapse; width: 100%; background: white; }
th, td { text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #e7e5e4; vertical-align: top; }
a { color: #1c1917; }
</style></head><body>
<h1>Sandbox manager</h1>
<p>""" + _cell(snapshot["today"]) + " · " + _cell(snapshot["timezone"]) + " · desk " + _cell(snapshot["desk"]) + " · " + _cell(snapshot["phase"]) + """</p>
<div class="grid">""" + _cards(snapshot) + """</div>
""" + _table(snapshot.get("asks")) + """
<p>Unused questions """ + _cell(snapshot["unused_pool"]) + " · published today " + _cell(snapshot["published_count"]) + " · " + _cell(event.get("kind")) + " " + _cell(event.get("note")) + """</p>
<p><a href="/status.json">JSON</a></p>
</body></html>"""


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    store = None

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        snapshot = self.store.snapshot()
        pages = {
            "/": lambda: self._body(200, _page(snapshot).encode("utf-8"), "text/html; charset=utf-8"),
            "/status.json": lambda: self._json(snapshot),
            "/api/status": lambda: self._json(snapshot),
        }
        call = pages.get(path)
        if call is None:
            self._body(404, b"not found", "text/plain; charset=utf-8")
            return
        call()

    def _json(self, snapshot):
        body = json.dumps(snapshot, default=str).encode("utf-8")
        self._body(200, body, "application/json; charset=utf-8")

    def _body(self, code, data, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


def start_http(store, host, port):
    _Handler.store = store
    try:
        server = _Server((host, port), _Handler)
    except OSError:
        raise SystemExit("status port " + str(port) + " is already in use")
    thread = threading.Thread(target=server.serve_forever, name="sandbox-status", daemon=True)
    thread.start()
    return server


def _wait_for_retry(result):
    return result.get("main") == "down" or result.get("reason") == "pool empty"


def _dry(store):
    store.set_meta("phase", "dry run")
    store.set_meta("publish", "no")
    result = ask_one(store, publish=False)
    if _wait_for_retry(result):
        store.set_meta("phase", "dry run waiting")
        time.sleep(20)
        result = ask_one(store, publish=False)
        result["waited"] = True
    print("SANDBOX_RESULT " + json.dumps(result, default=str), flush=True)
    store.set_meta("phase", "dry run finished")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        return


def _loop(store):
    store.set_meta("publish", "yes")
    shown_day = local_day()
    while True:
        current_day = local_day()
        if current_day != shown_day:
            shown_day = current_day
            write_report(store)
        if store.asked_today() >= DAILY_CAP:
            store.set_meta("phase", "daily cap reached")
            if store.meta("cap_day") != local_day():
                store.event("daily_cap", "100 asked questions; waiting for the next local day")
                store.set_meta("cap_day", local_day())
            time.sleep(min(60, store.seconds_until_next_day()))
            continue
        remaining = store.seconds_until_next_ask()
        if remaining > 0:
            store.set_meta("phase", "next question in " + str(remaining) + "s")
            time.sleep(min(remaining, 30))
            continue
        store.set_meta("phase", "asking")
        result = ask_one(store, publish=True)
        if _wait_for_retry(result):
            time.sleep(60)


def serve(dry_run):
    store = Store()
    write_report(store)
    host = status_host()
    port = status_port()
    start_http(store, host, port)
    print("sandbox status http://" + host + ":" + str(port) + "/", flush=True)
    print("sandbox report " + str(TEXT_PATH), flush=True)
    print("sandbox reask " + str(REASK_PATH), flush=True)
    if dry_run:
        _dry(store)
        return
    _loop(store)


def main(argv=None):
    from dotenv import load_dotenv

    load_dotenv()
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0].strip().casefold() if args else "start"
    commands = {
        "dry-run": lambda: serve(dry_run=True),
        "start": lambda: serve(dry_run=False),
    }
    call = commands.get(command)
    if call is None:
        raise SystemExit("use: python -m sandbox_manager dry-run   or   python -m sandbox_manager")
    call()
