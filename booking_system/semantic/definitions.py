"""What each manager means by a hotel phrase. Stored on the user's user_profile document by a stable key, not on the hotel.

Versioned: each entry in `definitions` is the version in effect (version_id dfv_<uuid>, effective_from). A changed meaning moves the
old entry to `definition_history` with superseded_at and saves a new version; nothing is overwritten. Answers and complaints record
the version_ids they used (versions_used)."""

import contextvars
import copy
from datetime import datetime, timezone

_TURN = contextvars.ContextVar("profile_turn", default=None)

STANDARD = (
    {
        "key": "arrivals",
        "label": "Arrivals",
        "question": "When you ask who is checking in or arriving today, which bookings should count?",
        "offer": "Bookings scheduled to arrive today, excluding cancelled",
        "chip": "scheduled today, not cancelled",
    },
    {
        "key": "in_house",
        "label": "In house",
        "question": "When you ask who is staying with us now, which guests should count?",
        "offer": "Everyone checked in and not yet checked out, counted by party size",
        "chip": "checked in, by party size",
    },
    {
        "key": "departures",
        "label": "Departures",
        "question": "When you ask who is checking out today, which bookings should count?",
        "offer": "Bookings whose last night was last night, excluding cancelled",
        "chip": "last night was last night",
    },
    {
        "key": "reservations",
        "label": "Reservations",
        "question": "When you ask how many reservations we have, which bookings should count?",
        "offer": "Confirmed bookings whose stay is current or still ahead",
        "chip": "confirmed, current or ahead",
    },
)
STYLE = (
    "A definition is one short statement of which bookings or guests count, written as the manager's own meaning, "
    "for example 'Bookings scheduled to arrive today, excluding cancelled'. "
    "It does not mention the user, the manager, or Leon."
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value):
    return " ".join(str(value or "").split())


def _key(value):
    return "_".join(str(value or "").lower().split())


def begin_turn():
    """Profile reads in this context share one read of each user's document until end_turn. Writes update it."""
    return _TURN.set({})


def end_turn(token):
    _TURN.reset(token)


def _read_profile(user_id):
    """The user_profile document of an existing user_account, in one round trip."""
    from booking_system.content_store import _with
    from booking_system.user_manager import profile_col

    def run(cur):
        cur.execute(
            """SELECT d.body FROM user_account a
               LEFT JOIN content_document d ON d.database_name = %s AND d.collection_name = %s AND d.body->>'user_id' = a.user_id
               WHERE a.user_id = %s LIMIT 1""",
            (profile_col.database_name, profile_col.collection_name, str(user_id)),
        )
        return cur.fetchone()

    found = _with(run) if user_id else None
    body = found[0] if found else None
    return {key: value for key, value in body.items() if key != "password_hash"} if body else None


def _profile(user_id):
    """The user_profile document of an existing user_account."""
    cache = _TURN.get()
    key = str(user_id or "")
    if cache is None:
        return _read_profile(key)
    if key not in cache:
        cache[key] = _read_profile(key)
    return copy.deepcopy(cache[key])


def _write(user_id, values):
    from booking_system.user_manager import profile_col
    profile_col.update_one({"user_id": str(user_id)}, {"$set": values})
    cache = _TURN.get()
    if cache is not None and cache.get(str(user_id)) is not None:
        cache[str(user_id)].update(copy.deepcopy(values))


def _entries(doc, field):
    return [item for item in (doc or {}).get(field) or [] if isinstance(item, dict) and _key(item.get("key"))]


def known_keys(user_id):
    """Every phrase this manager can define: the standard set, open requests, and custom definitions."""
    doc = _profile(user_id) or {}
    found = {item["key"]: {"key": item["key"], "label": item["label"], "question": item["question"]} for item in STANDARD}
    for field in ("definition_requests", "definitions"):
        for item in _entries(doc, field):
            key = _key(item.get("key"))
            if key not in found:
                found[key] = {"key": key, "label": _text(item.get("label")), "question": _text(item.get("question"))}
    return list(found.values())


def manager_definitions(user_id):
    """Answered definitions for this manager, one per key, with the recipe column each one already reads."""
    return [
        {
            "key": _key(item.get("key")),
            "phrase": _text(item.get("label")),
            "definition": _text(item.get("definition")),
            "version_id": str(item.get("version_id") or ""),
            "effective_from": str(item.get("effective_from") or ""),
            "aliases": [_text(alias) for alias in item.get("aliases") or [] if _text(alias)],
            "readings": [
                {"catalog_id": str(reading.get("catalog_id") or ""), "population": str(reading.get("population") or "")}
                for reading in item.get("readings") or [] if isinstance(reading, dict) and reading.get("population")
            ],
        }
        for item in _entries(_profile(user_id), "definitions")
        if _text(item.get("definition"))
    ]


def pin_readings(user_id, measures):
    """Read first. The first population a definition reads in a recipe (from the recipe's declared readings) is kept on the
    definition, so the same definition counts the same bookings or guests whatever the wording. A changed definition is saved
    as a new entry without readings."""
    doc = _profile(user_id)
    if not doc:
        return {"error": "user not found"}
    kept = _entries(doc, "definitions")
    added = []
    for item in measures or []:
        key = _key(item.get("definition_key"))
        matched = [entry for entry in kept if _key(entry.get("key")) == key]
        if not key or not matched or not item.get("population"):
            continue
        readings = [reading for reading in matched[0].get("readings") or [] if isinstance(reading, dict) and reading.get("population")]
        catalog_id = str(item.get("catalog_id") or "")
        if catalog_id in {str(reading.get("catalog_id") or "") for reading in readings}:
            continue
        readings.append({"catalog_id": catalog_id, "population": str(item["population"]), "pinned_at": _now()})
        matched[0]["readings"] = readings
        added.append(key + ":" + catalog_id + ":" + str(item["population"]))
    if added:
        _write(user_id, {"definitions": kept})
    return {"pinned": added}


def save_definition(user_id, key, definition, source):
    """Read the profile first. A known key is updated; a key named by an open request or the standard set is created."""
    name = _key(key)
    answer = _text(definition)
    doc = _profile(user_id)
    if not doc:
        return {"error": "user not found"}
    if not name or not answer:
        return {"error": "key and definition are required"}
    known = {item["key"]: item for item in known_keys(user_id)}
    if name not in known:
        return {"error": "unknown definition key " + name, "known_keys": sorted(known)}
    from booking_system.hotel_registry import new_id
    kept = _entries(doc, "definitions")
    history = list(doc.get("definition_history") or [])
    matched = [index for index, item in enumerate(kept) if _key(item.get("key")) == name]
    current = kept[matched[0]] if matched else None
    changed = not current or _text(current.get("definition")) != answer
    now = _now()
    entry = {
        "key": name,
        "label": known[name]["label"],
        "question": known[name]["question"],
        "definition": answer,
        "source": str(source or ""),
        "answered_at": now,
        "version_id": new_id("dfv"),
        "effective_from": now,
        "supersedes": str((current or {}).get("version_id") or ""),
    }
    if current and changed:
        history.append(dict(current, superseded_at=now, superseded_by=entry["version_id"]))
        kept[matched[0]] = entry
    if not current:
        kept.append(entry)
    requests = _entries(doc, "definition_requests")
    closed = [item for item in requests if _key(item.get("key")) == name]
    remaining = [item for item in requests if _key(item.get("key")) != name]
    skipped = [item for item in doc.get("definition_skipped") or [] if _key(item) != name]
    _write(user_id, {"definitions": kept, "definition_history": history, "definition_requests": remaining, "definition_skipped": skipped})
    waiting = set()
    for item in remaining:
        waiting.update(int(trace) for trace in item.get("trace_ids") or [])
    ready = []
    for item in closed:
        for trace in item.get("trace_ids") or []:
            if int(trace) not in waiting and int(trace) not in ready:
                ready.append(int(trace))
    version = entry["version_id"] if changed else str(current.get("version_id") or "")
    return {"key": name, "status": "answered" if changed else "unchanged", "version_id": version, "ready_trace_ids": ready}


def versions_used(definitions, keys=None):
    """[{key, version_id, definition}] of the definitions an answer read: those named by keys, or all of them when keys is None
    (the live agent is given every definition)."""
    named = None if keys is None else {_key(key) for key in keys if _key(key)}
    return [{"key": item["key"], "version_id": item.get("version_id") or "", "definition": item["definition"]}
            for item in definitions or [] if named is None or item["key"] in named]


def definition_history(user_id, key):
    """Every version of one definition, newest first: the one in effect, then the superseded ones."""
    doc = _profile(user_id) or {}
    name = _key(key)
    found = [item for item in _entries(doc, "definitions") + list(doc.get("definition_history") or []) if _key(item.get("key")) == name]
    return sorted(({field: item.get(field) for field in ("version_id", "definition", "effective_from", "superseded_at", "source")}
                   for item in found), key=lambda item: str(item.get("effective_from") or ""), reverse=True)


def skip_definition(user_id, key):
    """The manager declined to define this phrase. The desk stops asking; it stays pending for onboarding and the API."""
    name = _key(key)
    doc = _profile(user_id)
    if not doc or not name:
        return {"error": "user and key are required"}
    skipped = [_key(item) for item in doc.get("definition_skipped") or []]
    if name not in skipped:
        skipped.append(name)
    _write(user_id, {"definition_skipped": skipped})
    return {"key": name, "status": "skipped"}


def skipped_keys(user_id):
    return [_key(item) for item in (_profile(user_id) or {}).get("definition_skipped") or []]


def map_questions(user_id, questions):
    """Haiku names the key each reviewer question is about: a known key, or a new one."""
    import json
    from booking_system.llm_utils import haiku_text

    asked = [_text(item) for item in questions or [] if _text(item)]
    if not asked:
        return []
    known = known_keys(user_id)
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "enum": asked},
                        "key": {"type": "string", "enum": [item["key"] for item in known] + ["new", "not_business"]},
                        "new_key": {"type": "string"},
                        "new_label": {"type": "string"},
                    },
                    "required": ["question", "key", "new_key", "new_label"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    parsed = json.loads(haiku_text(
        (
            "Each question asks what a hotel phrase means. key is the known phrase the question is about. "
            "key is not_business when the question is about how the system stores or records data, such as columns, tables, "
            "timestamps, time zones, or storage conventions, because a manager defines business meaning, not storage. "
            "When no known phrase fits, key is new, new_key is a short snake_case name for the phrase, "
            "and new_label is the phrase in two or three words. Leave new_key and new_label empty otherwise. "
            "Return one item for every question."
        ),
        json.dumps({"known": known, "questions": asked}),
        schema=schema,
        max_tokens=1200,
        timeout=90,
    ))
    return [
        {
            "question": _text(item.get("question")),
            "key": _key(item.get("new_key")) if item.get("key") == "new" else "" if item.get("key") == "not_business" else _key(item.get("key")),
            "label": _text(item.get("new_label")),
        }
        for item in parsed.get("items") or []
    ]


def request_definitions(user_id, questions, trace_id=None):
    """Queue reviewer questions this manager must answer, one request per key."""
    doc = _profile(user_id)
    if not doc:
        return {"error": "user not found"}
    answered = {item["key"] for item in manager_definitions(user_id)}
    labels = {item["key"]: item for item in known_keys(user_id)}
    requests = _entries(doc, "definition_requests")
    opened = []
    settled = []
    for item in map_questions(user_id, questions):
        key = item["key"]
        if not key:
            continue
        if key in answered:
            settled.append(key)
            continue
        opened.append(key)
        matched = [entry for entry in requests if _key(entry.get("key")) == key]
        if matched:
            traces = list(matched[0].get("trace_ids") or [])
            if trace_id and int(trace_id) not in traces:
                traces.append(int(trace_id))
            matched[0]["trace_ids"] = traces
            continue
        known = labels.get(key) or {}
        requests.append({
            "key": key,
            "label": known.get("label") or item["label"],
            "question": known.get("question") or item["question"],
            "trace_ids": [int(trace_id)] if trace_id else [],
            "requested_at": _now(),
        })
    _write(user_id, {"definition_requests": requests})
    return {"pending": len(requests), "opened": opened, "settled": settled}


def open_request(user_id, key, label, question):
    """Read first. A phrase the desk asks about that is not known yet becomes an open request under its own key."""
    name = _key(key)
    doc = _profile(user_id)
    if not doc:
        return {"error": "user not found"}
    if not name or not _text(label):
        return {"error": "key and label are required"}
    if name in {item["key"] for item in known_keys(user_id)}:
        return {"key": name, "status": "known"}
    requests = _entries(doc, "definition_requests")
    requests.append({"key": name, "label": _text(label), "question": _text(question), "trace_ids": [], "requested_at": _now()})
    _write(user_id, {"definition_requests": requests})
    return {"key": name, "status": "opened"}


def open_definitions(user_id):
    """Unanswered keys: reviewer requests first, then the standard set. Skipped keys stay listed and are flagged."""
    doc = _profile(user_id) or {}
    answered = {item["key"] for item in manager_definitions(user_id)}
    skipped = set(skipped_keys(user_id))
    found = []
    for item in _entries(doc, "definition_requests"):
        key = _key(item.get("key"))
        found.append({
            "key": key,
            "label": _text(item.get("label")),
            "question": _text(item.get("question")),
            "offer": "",
            "trace_ids": [int(trace) for trace in item.get("trace_ids") or []],
            "skipped": key in skipped,
        })
    listed = {item["key"] for item in found}
    for item in STANDARD:
        if item["key"] in answered or item["key"] in listed:
            continue
        found.append({
            "key": item["key"],
            "label": item["label"],
            "question": item["question"],
            "offer": item["offer"],
            "trace_ids": [],
            "skipped": item["key"] in skipped,
        })
    return found


def trace_manager(trace):
    return str((((trace or {}).get("retrieval")) or {}).get("manager") or "")
