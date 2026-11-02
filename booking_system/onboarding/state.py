"""Resumable onboarding state. A session (onb_) belongs to an anonymous visitor until their account exists, then to the person and,
once it exists, their hotel. The checklist is SECTIONS (data); each section's stored status is not_started / in_progress / skipped /
done, and sections whose completion the data proves (DERIVED) are done exactly when the data says so. Items are the open questions
(asked, with how often and when) and what was saved. The conversation is a document. Every write reads first."""

from datetime import datetime, timezone

from booking_system import db
from booking_system.content_store import client
from booking_system.hotel_registry import new_id

transcripts = client["leon_onboarding"]["transcript"]
SECTIONS = (
    {"section": "account", "label": "Your account", "skippable": False,
     "why": "Nothing you tell Leon can be kept until your account exists: just your name, mobile number and a password."},
    {"section": "hotel", "label": "Your hotel", "skippable": False,
     "why": "Everything else belongs to the hotel; its name, city, time zone and currency date and price everything."},
    {"section": "modules", "label": "What LEON runs", "skippable": True, "why": "Only the parts of the hotel LEON runs ask for their settings."},
    {"section": "settings", "label": "How the hotel operates", "skippable": True, "why": "Operations refuse until the rules they depend on are answered."},
    {"section": "definitions", "label": "Your definitions", "skippable": True, "why": "When you ask for arrivals or occupancy, Leon counts what you mean."},
    {"section": "routines", "label": "Your routine reports", "skippable": True, "why": "The figures you check every day, ready in one question."},
    {"section": "sops", "label": "Procedures (SOPs)", "skippable": True, "why": "Your staff's procedures, kept next to the setting they carry out."},
    {"section": "migration", "label": "Data from your old system", "skippable": True, "why": "Rooms, guests and upcoming reservations, so day one is not empty."},
)
SECTION_NAMES = tuple(item["section"] for item in SECTIONS)
SECTION_BY_NAME = {item["section"]: item for item in SECTIONS}
STATUSES = ("not_started", "in_progress", "skipped", "done")
TONES = ("warm", "brief", "formal", "playful")
PACES = ("one_at_a_time", "several_at_once")
RETURNING_HOURS = 6
STALE_DAYS = 2
_COLUMNS = "onboarding_id, user_id, hotel_id, status, language, tone, pace, role_note, turns, created_at, last_turn_at"


def _now():
    return datetime.now(timezone.utc)


# ---------- sessions ----------

def _session(cur, onboarding_id):
    cur.execute("SELECT " + _COLUMNS + " FROM onboarding_session WHERE onboarding_id = %s", (str(onboarding_id or ""),))
    return cur.fetchone()


def _create(cur, user_id, hotel_id):
    onboarding_id = new_id("onb")
    cur.execute("INSERT INTO onboarding_session (onboarding_id, user_id, hotel_id, status) VALUES (%s, %s, %s, 'active')",
                (onboarding_id, user_id, hotel_id))
    cur.execute("INSERT INTO onboarding_section (onboarding_id, section, status) SELECT %s, unnest(%s::varchar[]), 'not_started'",
                (onboarding_id, list(SECTION_NAMES)))
    return _session(cur, onboarding_id)


def open_session(onboarding_id, user_id, hotel_id):
    """READ the session the visitor holds, then the person's latest session at this hotel, then their latest one without a hotel
    (it takes this hotel); create one when none fits. A session bound to a person is never handed to anyone else."""
    def run(cur):
        held = _session(cur, onboarding_id) if onboarding_id else None
        fits = held and held["user_id"] == user_id and (not held["hotel_id"] or held["hotel_id"] == hotel_id)
        anonymous = held and not held["user_id"]
        if user_id and not fits:
            cur.execute("SELECT " + _COLUMNS + " FROM onboarding_session WHERE user_id = %s AND (hotel_id = %s OR hotel_id IS NULL) "
                        "ORDER BY (hotel_id IS NULL), last_turn_at DESC LIMIT 1", (user_id, hotel_id))
            held = cur.fetchone() or (held if anonymous else None)
        if not user_id and not anonymous:
            held = None
        held = held or _create(cur, user_id, hotel_id)
        if (user_id and not held["user_id"]) or (hotel_id and not held["hotel_id"]):
            cur.execute("UPDATE onboarding_session SET user_id = %s, hotel_id = %s WHERE onboarding_id = %s",
                        (user_id, hotel_id, held["onboarding_id"]))
            held = _session(cur, held["onboarding_id"])
        return dict(held)
    return db.transaction(run)


def read_session(onboarding_id):
    return db.transaction(lambda cur: _session(cur, onboarding_id))


def bind(onboarding_id, user_id, hotel_id):
    """The account or hotel created in this session becomes its owner."""
    db.execute("UPDATE onboarding_session SET user_id = COALESCE(user_id, %s), hotel_id = COALESCE(hotel_id, %s) WHERE onboarding_id = %s",
               (user_id, hotel_id, onboarding_id))


def touch(onboarding_id):
    """Count a turn. Returns the previous last_turn_at."""
    def run(cur):
        cur.execute("SELECT last_turn_at, turns FROM onboarding_session WHERE onboarding_id = %s FOR UPDATE", (onboarding_id,))
        found = cur.fetchone()
        cur.execute("UPDATE onboarding_session SET turns = turns + 1, last_turn_at = now() WHERE onboarding_id = %s", (onboarding_id,))
        return found["last_turn_at"] if found and found["turns"] else None
    return db.transaction(run)


def save_style(onboarding_id, language, tone, pace, role_note):
    """READ the session; record only the values given (empty keeps what is stored)."""
    if tone and tone not in TONES:
        return {"ok": False, "error": "tone is one of " + ", ".join(TONES)}
    if pace and pace not in PACES:
        return {"ok": False, "error": "pace is one of " + ", ".join(PACES)}

    def run(cur):
        current = _session(cur, onboarding_id)
        wanted = {"language": language, "tone": tone, "pace": pace, "role_note": role_note}
        changed = {name: str(value).strip() for name, value in wanted.items() if str(value or "").strip() and str(value).strip() != current[name]}
        if changed:
            cur.execute("UPDATE onboarding_session SET " + ", ".join(name + " = %s" for name in changed) + " WHERE onboarding_id = %s",
                        tuple(changed.values()) + (onboarding_id,))
        return {"ok": True, "changed": changed}
    return db.transaction(run)


def finish(onboarding_id):
    db.execute("UPDATE onboarding_session SET status = 'finished' WHERE onboarding_id = %s AND status = 'active'", (onboarding_id,))


# ---------- sections ----------

def stored_sections(onboarding_id):
    rows = db.rows("SELECT section, status, note, updated_at FROM onboarding_section WHERE onboarding_id = %s", (onboarding_id,))
    return {row["section"]: row for row in rows}


def set_section(onboarding_id, section, status, note=""):
    """READ the stored status; write only a change."""
    if section not in SECTION_NAMES:
        return {"ok": False, "error": "section is one of " + ", ".join(SECTION_NAMES)}
    if status not in STATUSES:
        return {"ok": False, "error": "status is one of " + ", ".join(STATUSES)}
    if status == "skipped" and not SECTION_BY_NAME[section]["skippable"]:
        return {"ok": False, "error": SECTION_BY_NAME[section]["label"] + " cannot be skipped. " + SECTION_BY_NAME[section]["why"]}

    def run(cur):
        cur.execute("SELECT status, note FROM onboarding_section WHERE onboarding_id = %s AND section = %s FOR UPDATE", (onboarding_id, section))
        current = cur.fetchone()
        if current and current["status"] == status and (not note or current["note"] == note):
            return {"ok": True, "result": "unchanged", "section": section, "status": status}
        cur.execute("""INSERT INTO onboarding_section (onboarding_id, section, status, note) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (onboarding_id, section) DO UPDATE SET status = EXCLUDED.status,
                       note = CASE WHEN EXCLUDED.note = '' THEN onboarding_section.note ELSE EXCLUDED.note END, updated_at = now()""",
                    (onboarding_id, section, status, str(note or "")))
        return {"ok": True, "result": "saved", "section": section, "status": status}
    return db.transaction(run)


def begin_section(onboarding_id, section):
    """A section that is touched is in progress, unless it is already done."""
    db.execute("""UPDATE onboarding_section SET status = 'in_progress', updated_at = now()
                  WHERE onboarding_id = %s AND section = %s AND status IN ('not_started','skipped')""", (onboarding_id, section))


def checklist(onboarding_id, proven):
    """[{section, label, why, status, note}]: a section the data proves complete is done; a proven section the data no longer proves
    is in progress again (a newly required setting); every other section keeps its stored status."""
    stored = stored_sections(onboarding_id)
    rows = []
    for item in SECTIONS:
        name = item["section"]
        found = stored.get(name) or {"status": "not_started", "note": ""}
        status = found["status"]
        if name in proven:
            status = "done" if proven[name] else ("in_progress" if status == "done" else status)
        rows.append(dict(item, status=status, note=found["note"]))
    return rows


# ---------- items ----------

def _items(cur, onboarding_id):
    cur.execute("""SELECT item_id, section, kind, item_key, summary, status, asked_count, last_asked_at, created_at, resolved_at
                   FROM onboarding_item WHERE onboarding_id = %s ORDER BY created_at""", (onboarding_id,))
    return cur.fetchall()


def items(onboarding_id):
    return [dict(row) for row in db.transaction(lambda cur: _items(cur, onboarding_id))]


def note_saved(onboarding_id, section, key, summary):
    """What a write stored, and the open question it answers closed. The section is in progress at least."""
    def run(cur):
        cur.execute("""UPDATE onboarding_item SET status = 'answered', resolved_at = now()
                       WHERE onboarding_id = %s AND kind = 'open' AND item_key = %s AND status = 'open'""", (onboarding_id, key))
        cur.execute("""INSERT INTO onboarding_item (item_id, onboarding_id, section, kind, item_key, summary, status)
                       VALUES (%s, %s, %s, 'saved', %s, %s, 'saved')""", (new_id("obi"), onboarding_id, section, key, summary))
        cur.execute("""UPDATE onboarding_section SET status = 'in_progress', updated_at = now()
                       WHERE onboarding_id = %s AND section = %s AND status IN ('not_started','skipped')""", (onboarding_id, section))
    db.transaction(run)


def note_asked(onboarding_id, section, key, question):
    """READ the open item for this key: count the ask again, or open it."""
    def run(cur):
        cur.execute("SELECT item_id FROM onboarding_item WHERE onboarding_id = %s AND kind = 'open' AND item_key = %s AND status = 'open'",
                    (onboarding_id, key))
        found = cur.fetchone()
        if found:
            cur.execute("UPDATE onboarding_item SET asked_count = asked_count + 1, last_asked_at = now(), summary = %s WHERE item_id = %s",
                        (question, found["item_id"]))
            return
        cur.execute("""INSERT INTO onboarding_item (item_id, onboarding_id, section, kind, item_key, summary, status, asked_count, last_asked_at)
                       VALUES (%s, %s, %s, 'open', %s, %s, 'open', 1, now())""", (new_id("obi"), onboarding_id, section, key, question))
    db.transaction(run)


def close_item(onboarding_id, key, status):
    """An open question answered elsewhere, or set aside by the manager (skipped)."""
    if status not in ("answered", "skipped"):
        return {"ok": False, "error": "status is answered or skipped"}
    changed = db.execute("""UPDATE onboarding_item SET status = %s, resolved_at = now()
                            WHERE onboarding_id = %s AND kind = 'open' AND item_key = %s AND status = 'open'""", (status, onboarding_id, key))
    return {"ok": bool(changed), "result": status if changed else "no open question " + key}


def reopen_skipped(onboarding_id, key):
    db.execute("""UPDATE onboarding_item SET status = 'open', resolved_at = NULL
                  WHERE onboarding_id = %s AND kind = 'open' AND item_key = %s AND status = 'skipped'""", (onboarding_id, key))


# ---------- transcript ----------

def _doc(key):
    found = db.row("SELECT body FROM content_document WHERE database_name = %s AND collection_name = %s AND document_key = %s",
                   (transcripts.database_name, transcripts.collection_name, key))
    return found["body"] if found else None


def conversation(onboarding_id, last=None):
    turns = (_doc(onboarding_id) or {}).get("turns") or []
    return turns[-last:] if last else turns


def append(onboarding_id, entries, user_id, hotel_id):
    """Read the transcript, add the entries, write it back."""
    body = _doc(onboarding_id) or {"_id": onboarding_id, "onboarding_id": onboarding_id, "turns": []}
    stamp = _now().isoformat()
    body["turns"] = (list(body.get("turns") or []) + [dict(entry, at=stamp) for entry in entries])[-400:]
    body["user_id"] = user_id or body.get("user_id") or ""
    body["hotel_id"] = hotel_id or body.get("hotel_id") or ""
    db.transaction(lambda cur: transcripts.write_in(cur, onboarding_id, body))


# ---------- resume ----------

def away_hours(previous):
    return round((_now() - previous).total_seconds() / 3600, 1) if previous else 0.0


def is_stale(item):
    stamp = item.get("last_asked_at")
    return bool(stamp) and (_now() - stamp).days >= STALE_DAYS


def recap(sections, all_items):
    """What the banner and the returning greeting start from: done and skipped sections, the latest saves, the open questions."""
    saved = [item for item in all_items if item["kind"] == "saved"]
    still = [item for item in all_items if item["kind"] == "open" and item["status"] == "open"]
    return {
        "done": [row["label"] for row in sections if row["status"] == "done"],
        "skipped": [row["label"] for row in sections if row["status"] == "skipped"],
        "in_progress": [row["label"] for row in sections if row["status"] == "in_progress"],
        "last_saved": [item["summary"] for item in saved[-5:]],
        "open_questions": [{"key": item["item_key"], "question": item["summary"], "stale": is_stale(item)} for item in still],
    }
