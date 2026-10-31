"""What each manager means by a hotel phrase. Stored on the manager profile by a stable key, not on the hotel."""

from datetime import datetime, timezone

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


def _profile(manager_name):
    from booking_system.hotel_context_manager import manager_col
    name = str(manager_name or "").upper()
    if not name or manager_col is None:
        return None
    return manager_col.find_one({"manager_name": name}, {"password": 0})


def _write(manager_name, values):
    from booking_system.hotel_context_manager import manager_col
    manager_col.update_one({"manager_name": str(manager_name or "").upper()}, {"$set": values})


def _entries(doc, field):
    return [item for item in (doc or {}).get(field) or [] if isinstance(item, dict) and _key(item.get("key"))]


def known_keys(manager_name):
    """Every phrase this manager can define: the standard set, open requests, and custom definitions."""
    doc = _profile(manager_name) or {}
    found = {item["key"]: {"key": item["key"], "label": item["label"], "question": item["question"]} for item in STANDARD}
    for field in ("definition_requests", "definitions"):
        for item in _entries(doc, field):
            key = _key(item.get("key"))
            if key not in found:
                found[key] = {"key": key, "label": _text(item.get("label")), "question": _text(item.get("question"))}
    return list(found.values())


def manager_definitions(manager_name):
    """Answered definitions for this manager, one per key."""
    return [
        {"key": _key(item.get("key")), "phrase": _text(item.get("label")), "definition": _text(item.get("definition"))}
        for item in _entries(_profile(manager_name), "definitions")
        if _text(item.get("definition"))
    ]


def save_definition(manager_name, key, definition, source):
    """Read the profile first. A known key is updated; a key named by an open request or the standard set is created."""
    name = _key(key)
    answer = _text(definition)
    doc = _profile(manager_name)
    if not doc:
        return {"error": "manager not found"}
    if not name or not answer:
        return {"error": "key and definition are required"}
    known = {item["key"]: item for item in known_keys(manager_name)}
    if name not in known:
        return {"error": "unknown definition key " + name, "known_keys": sorted(known)}
    kept = _entries(doc, "definitions")
    entry = {
        "key": name,
        "label": known[name]["label"],
        "question": known[name]["question"],
        "definition": answer,
        "source": str(source or ""),
        "answered_at": _now(),
    }
    matched = [index for index, item in enumerate(kept) if _key(item.get("key")) == name]
    if matched:
        kept[matched[0]] = entry
    else:
        kept.append(entry)
    requests = _entries(doc, "definition_requests")
    closed = [item for item in requests if _key(item.get("key")) == name]
    remaining = [item for item in requests if _key(item.get("key")) != name]
    skipped = [item for item in doc.get("definition_skipped") or [] if _key(item) != name]
    _write(manager_name, {"definitions": kept, "definition_requests": remaining, "definition_skipped": skipped})
    waiting = set()
    for item in remaining:
        waiting.update(int(trace) for trace in item.get("trace_ids") or [])
    ready = []
    for item in closed:
        for trace in item.get("trace_ids") or []:
            if int(trace) not in waiting and int(trace) not in ready:
                ready.append(int(trace))
    return {"key": name, "status": "answered", "ready_trace_ids": ready}


def skip_definition(manager_name, key):
    """The manager declined to define this phrase. The desk stops asking; it stays pending for onboarding and the API."""
    name = _key(key)
    doc = _profile(manager_name)
    if not doc or not name:
        return {"error": "manager and key are required"}
    skipped = [_key(item) for item in doc.get("definition_skipped") or []]
    if name not in skipped:
        skipped.append(name)
    _write(manager_name, {"definition_skipped": skipped})
    return {"key": name, "status": "skipped"}


def skipped_keys(manager_name):
    return [_key(item) for item in (_profile(manager_name) or {}).get("definition_skipped") or []]


def map_questions(manager_name, questions):
    """Haiku names the key each reviewer question is about: a known key, or a new one."""
    import json
    from booking_system.llm_utils import haiku_text

    asked = [_text(item) for item in questions or [] if _text(item)]
    if not asked:
        return []
    known = known_keys(manager_name)
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


def request_definitions(manager_name, questions, trace_id=None):
    """Queue reviewer questions this manager must answer, one request per key."""
    doc = _profile(manager_name)
    if not doc:
        return {"error": "manager not found"}
    answered = {item["key"] for item in manager_definitions(manager_name)}
    labels = {item["key"]: item for item in known_keys(manager_name)}
    requests = _entries(doc, "definition_requests")
    opened = []
    settled = []
    for item in map_questions(manager_name, questions):
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
    _write(manager_name, {"definition_requests": requests})
    return {"pending": len(requests), "opened": opened, "settled": settled}


def open_request(manager_name, key, label, question):
    """Read first. A phrase the desk asks about that is not known yet becomes an open request under its own key."""
    name = _key(key)
    doc = _profile(manager_name)
    if not doc:
        return {"error": "manager not found"}
    if not name or not _text(label):
        return {"error": "key and label are required"}
    if name in {item["key"] for item in known_keys(manager_name)}:
        return {"key": name, "status": "known"}
    requests = _entries(doc, "definition_requests")
    requests.append({"key": name, "label": _text(label), "question": _text(question), "trace_ids": [], "requested_at": _now()})
    _write(manager_name, {"definition_requests": requests})
    return {"key": name, "status": "opened"}


def open_definitions(manager_name):
    """Unanswered keys: reviewer requests first, then the standard set. Skipped keys stay listed and are flagged."""
    doc = _profile(manager_name) or {}
    answered = {item["key"] for item in manager_definitions(manager_name)}
    skipped = set(skipped_keys(manager_name))
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
