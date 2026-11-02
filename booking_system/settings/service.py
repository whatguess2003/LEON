"""Hotel operating settings: read the catalog and the values in effect, record a new value (versioned, never overwritten), keep SOP
text as versioned documents, and report what a module still needs. Every write reads first; a value is checked against its catalog
row and the user's access; an unchanged value records nothing.

Results: {"ok": True, ...} or {"ok": False, "error": ...}."""

import json
from datetime import date, datetime, time, timezone

import psycopg2
from psycopg2.extras import Json

from booking_system import db, hotel_registry
from booking_system.content_store import client

sop_docs = client["leon_hotel_memory"]["hotel_sop"]
SOURCES = ("onboarding", "settings", "desk", "migration", "fixture")
# Sources that record history as it was (a migration of an existing value, a test fixture): any effective date, no access check.
_RECORDED = ("migration", "fixture")

_CATALOG_SQL = """SELECT setting_key, module_name, question, explanation, value_type, options, required, required_when, set_module, set_level,
                         sop_prompt, display_order, effective_basis FROM setting_catalog WHERE status = 'active'"""


# What a read must use where a hotel setting decides a figure (for the live SQL agent and the catalog agent).
SQL_FACTS = (
    "Some figures depend on the hotel's operating settings; read them through these functions, never a fixed rule. "
    "Worked hours: hr_worked_hours(hotel_id, work_date, clock_in, clock_out, break_minutes) with work_date the roster work_date "
    "(else the hotel-local clock-in date) and break_minutes from the rostered shift; it applies hr.worked_hours_breaks. "
    "Leave days: hr_leave_days(hotel_id, from_date, to_date, leave_request.start_date) applies hr.leave_day_counting.",
    "hotel_setting_at(hotel_id, setting_key, day) is a setting value (jsonb) in effect on a day; setting_catalog lists the keys and "
    "their option meanings. Finance postings are dated by business_date; amount is the net, and tax lines are child postings "
    "(parent_posting_id); finance.tax_pricing on the business date says whether the quoted price was net or net plus tax.",
)


class Refused(Exception):
    pass


def _run(fn):
    try:
        return dict({"ok": True}, **db.transaction(fn))
    except Refused as refusal:
        return {"ok": False, "error": str(refusal)}


def _plain(value):
    return value.isoformat() if isinstance(value, (date, time)) else value


# A stated value is accepted by hotel_setting_accept, which reads the catalog row and the hotel's tax codes.


def _listed(text):
    return [part.strip() for part in str(text).split(",") if part.strip()]


# A value as chat text (what an extraction step returns) and back, per value type; hotel_setting_accept then judges the value.
FROM_TEXT = {"boolean": json.loads, "choice": str, "choices": _listed, "time": str, "amount": str, "tax_codes": _listed}
TO_TEXT = {"boolean": json.dumps, "choice": str, "choices": ", ".join, "time": str, "amount": str, "tax_codes": ", ".join}


def text_property(row):
    """JSON-schema property for a value of this catalog row as text: a choice or yes/no is limited to its values (or empty)."""
    allowed = {"choice": [option["value"] for option in row["options"]], "boolean": ["true", "false"]}.get(row["value_type"])
    return {"type": "string", "enum": [""] + allowed} if allowed else {"type": "string"}


def describe_values(row):
    """One line naming the value form of a catalog row, for an extraction prompt."""
    options = "; ".join(option["value"] + " = " + option["meaning"] for option in row["options"])
    return row["setting_key"] + ": " + row["question"] + (" Values: " + options if options else " (" + row["value_type"] + ")")


# ---------- reads ----------

def operating_date(cur, hotel_id):
    """The earliest date an operation of the hotel can still be dated on: its local date, or the open business date when that lags."""
    cur.execute("""SELECT LEAST(hotel_local_date(%s), (SELECT business_date FROM business_day WHERE hotel_id = %s AND status = 'open')) AS day""",
                (hotel_id, hotel_id))
    return cur.fetchone()["day"]


def earliest_date(cur, hotel_id, basis):
    """The earliest effective date a change to a setting of this basis may take: the hotel's local date, or for business_date
    settings the open business date (a closed business date keeps the value its postings used). A hotel that does not run
    business dates yet dates by its local date."""
    cur.execute("""SELECT CASE %s WHEN 'business_date' THEN COALESCE((SELECT business_date FROM business_day WHERE hotel_id = %s
                                                                      AND status = 'open'), hotel_local_date(%s))
                                  WHEN 'local_date' THEN hotel_local_date(%s) END AS day""", (basis, hotel_id, hotel_id, hotel_id))
    return cur.fetchone()["day"]


def _catalog(cur, key=None, module=None):
    cur.execute(_CATALOG_SQL + " AND (%s::varchar IS NULL OR setting_key = %s) AND (%s::varchar IS NULL OR module_name = %s) "
                "ORDER BY display_order", (key, key, module, module))
    return cur.fetchall()


def _value_at(cur, hotel_id, key, day):
    cur.execute("""SELECT s.hotel_setting_id, s.value, s.effective_from, s.set_at, s.source, s.note, s.set_by_user_id, a.user_name AS set_by
                   FROM hotel_setting s JOIN user_account a ON a.user_id = s.set_by_user_id
                   WHERE s.hotel_id = %s AND s.setting_key = %s AND s.effective_from <= %s
                   ORDER BY s.effective_from DESC, s.set_at DESC LIMIT 1""", (hotel_id, key, day))
    return cur.fetchone()


def _meaning(row, value):
    return next((option["meaning"] for option in row["options"] if option["value"] == value), "")


def sop_at(hotel_id, key, day):
    """The SOP document version in effect on day, or None."""
    versions = [doc for doc in sop_docs.find({"hotel_id": hotel_id, "setting_key": key}) if str(doc.get("effective_from")) <= str(day)]
    return max(versions, key=lambda doc: (str(doc.get("effective_from")), str(doc.get("set_at")))) if versions else None


def read_settings(hotel_id, module=None, key=None, day=None):
    """Catalog rows with the value in effect on day (default: the hotel's operating date), who set it and when, the SOP in effect,
    and each module's state (enabled, missing required settings)."""
    def run(cur):
        on = day or operating_date(cur, hotel_id)
        rows = []
        for row in _catalog(cur, key, module):
            current = _value_at(cur, hotel_id, row["setting_key"], on)
            sop = sop_at(hotel_id, row["setting_key"], on) if row["sop_prompt"] else None
            rows.append(dict(row, value=current["value"] if current else None, meaning=_meaning(row, current["value"]) if current else "",
                             effective_from=_plain(current["effective_from"]) if current else None,
                             set_by=current["set_by"] if current else None, set_at=current["set_at"].isoformat() if current else None,
                             source=current["source"] if current else None, sop_text=sop["text"] if sop else None))
        modules = sorted({row["module_name"] for row in rows})
        return {"day": _plain(on), "settings": rows, "modules": {name: module_state(cur, hotel_id, name, on) for name in modules}}
    return _run(run)


def history(hotel_id, key):
    """Every recorded value of one setting, newest effective first, and every SOP version."""
    def run(cur):
        cur.execute("""SELECT s.hotel_setting_id, s.value, s.effective_from, s.set_at, s.source, s.note, a.user_name AS set_by
                       FROM hotel_setting s JOIN user_account a ON a.user_id = s.set_by_user_id
                       WHERE s.hotel_id = %s AND s.setting_key = %s ORDER BY s.effective_from DESC, s.set_at DESC""", (hotel_id, key))
        values = [dict(row, effective_from=_plain(row["effective_from"]), set_at=row["set_at"].isoformat()) for row in cur.fetchall()]
        sops = sorted(sop_docs.find({"hotel_id": hotel_id, "setting_key": key}), key=lambda doc: (str(doc.get("effective_from")), str(doc.get("set_at"))),
                      reverse=True)
        return {"setting_key": key, "values": values, "sop_versions": sops}
    return _run(run)


def module_state(cur, hotel_id, module, day):
    cur.execute("SELECT hotel_module_state(%s, %s, %s) AS state", (hotel_id, module, day))
    return cur.fetchone()["state"]


def module_refusal(state):
    """'' when a module that is switched per hotel (it has an <module>.enabled setting) may operate, else the reason naming what is
    unanswered. A core module (front desk) is never refused whole: each of its operations refuses on the setting it reads."""
    if not state["governed"]:
        return ""
    if state["enabled"] is None:
        return "module is not set up: answer whether the hotel uses it first"
    if state["enabled"] is not True:
        return "module is turned off for this hotel"
    if state["missing"]:
        return "module needs these settings first: " + "; ".join(m["setting_key"] + " (" + m["question"] + ")" for m in state["missing"])
    return ""


def _answerable(cur, hotel_id):
    return ""


def _after_tax_codes(cur, hotel_id):
    cur.execute("SELECT EXISTS (SELECT 1 FROM tax_code WHERE hotel_id = %s AND status = 'active') AS ready", (hotel_id,))
    return "" if cur.fetchone()["ready"] else "the hotel's tax codes, which are added in Finance"


# What a value of each type needs to exist in the hotel before the question can be answered ('' = nothing).
WAITS_FOR = {"boolean": _answerable, "choice": _answerable, "choices": _answerable, "time": _answerable, "amount": _answerable,
             "tax_codes": _after_tax_codes}


def pending(hotel_id, modules=None):
    """For each module (default: every catalog module): its state on the operating date and the questions still to ask:
    the enablement question while unanswered, then the module's missing required settings while it is enabled.
    ask holds the questions answerable now; waiting holds missing ones that need hotel data first (waits_for names it).
    Both keep the module refused."""
    def run(cur):
        on = operating_date(cur, hotel_id)
        rows = {row["setting_key"]: row for row in _catalog(cur)}
        names = modules or sorted({row["module_name"] for row in rows.values()})
        cur.execute("SELECT m AS module_name, hotel_module_state(%s, m, %s) AS state FROM unnest(%s::varchar[]) m", (hotel_id, on, list(names)))
        out = {}
        for found in cur.fetchall():
            state = found["state"]
            keys = []
            if state["governed"] and state["enabled"] is None:
                keys = [found["module_name"] + ".enabled"]
            if state["enabled"] is True or not state["governed"]:
                keys = [m["setting_key"] for m in state["missing"]]
            needs = {key: WAITS_FOR[rows[key]["value_type"]](cur, hotel_id) for key in keys}
            out[found["module_name"]] = dict(state, ask=[dict(rows[key], value=None) for key in keys if not needs[key]],
                                             waiting=[dict(rows[key], value=None, waits_for=needs[key]) for key in keys if needs[key]])
        return {"day": _plain(on), "modules": out}
    return _run(run)


def waiting(hotel_id):
    """Missing settings that need hotel data before they can be asked: [{setting_key, question, waits_for}]."""
    found = pending(hotel_id)
    return [{"setting_key": row["setting_key"], "question": row["question"], "waits_for": row["waits_for"]}
            for state in found.get("modules", {}).values() for row in state["waiting"]]


def asks(hotel_id):
    """The setting keys still to ask the hotel, in catalog order across modules."""
    found = pending(hotel_id)
    rows = [row for state in found.get("modules", {}).values() for row in state["ask"]]
    return [row["setting_key"] for row in sorted(rows, key=lambda row: row["display_order"])]


def catalog_rows():
    """Every active catalog row, in display order."""
    return db.transaction(_catalog)


def values_in_effect(hotel_id):
    """{setting_key: value} in effect on the hotel's operating date, answered keys only."""
    def run(cur):
        on = operating_date(cur, hotel_id)
        cur.execute("SELECT setting_key, hotel_setting_at(%s, setting_key, %s) AS value FROM setting_catalog WHERE status = 'active'",
                    (hotel_id, on))
        return {row["setting_key"]: row["value"] for row in cur.fetchall() if row["value"] is not None}
    return db.transaction(run)


# ---------- writes ----------

def _level_ok(cur, hotel_id, user_id, module, level):
    cur.execute("""SELECT a.level_rank >= l.level_rank AS ok FROM user_module_access a JOIN access_level l ON l.access_level = %s
                   WHERE a.hotel_id = %s AND a.user_id = %s AND a.module_name = %s""", (level, hotel_id, user_id, module))
    found = cur.fetchone()
    return bool(found and found["ok"])


def _accepted(cur, hotel_id, key, value):
    """The canonical value from hotel_setting_accept. A 22023 refusal rolls back to a savepoint so the caller's transaction can continue."""
    cur.execute("SAVEPOINT setting_value")
    try:
        cur.execute("SELECT hotel_setting_accept(%s, %s, %s) AS value", (hotel_id, key, Json(value)))
        accepted = cur.fetchone()["value"]
    except psycopg2.Error as err:
        cur.execute("ROLLBACK TO SAVEPOINT setting_value")
        if err.pgcode == "22023":
            raise Refused(err.diag.message_primary or "invalid setting value")
        raise
    cur.execute("RELEASE SAVEPOINT setting_value")
    return accepted


def write_setting(cur, hotel_id, user_id, key, value, effective_from, source, note="", sop_text=None):
    """On the caller's cursor. READ the catalog row, the user's access and the value in effect; hotel_setting_accept refuses a bad
    value; a missing access or a date before the operating date is refused; a new row is recorded only when the value changes.
    Returns the outcome with the module's new follow-ups."""
    rows = _catalog(cur, key)
    if not rows:
        raise Refused("unknown setting " + str(key))
    row = rows[0]
    if source not in SOURCES:
        raise Refused("source must be one of " + ", ".join(SOURCES))
    if source not in _RECORDED and not _level_ok(cur, hotel_id, user_id, row["set_module"], row["set_level"]):
        raise Refused(key + " can be changed only with " + row["set_level"] + " access to " + row["set_module"])
    stored = _accepted(cur, hotel_id, key, value)
    on = date.fromisoformat(str(effective_from)[:10])
    earliest = earliest_date(cur, hotel_id, row["effective_basis"])
    if source not in _RECORDED and on < earliest:
        raise Refused(key + " can change from " + earliest.isoformat() + " (" + row["effective_basis"].replace("_", " ")
                      + ") or later; earlier dates keep the value they used")
    current = _value_at(cur, hotel_id, key, on)
    outcome = "unchanged" if current and current["value"] == stored else "saved"
    if outcome == "saved":
        cur.execute("""INSERT INTO hotel_setting (hotel_setting_id, hotel_id, setting_key, value, effective_from, set_by_user_id, source, note)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (hotel_registry.new_id("hst"), hotel_id, key, Json(stored), on, user_id, source, str(note or "")))
    sop_saved = False
    if str(sop_text or "").strip():
        current_sop = sop_at(hotel_id, key, on)
        if not current_sop or current_sop["text"] != str(sop_text).strip():
            doc_id = hotel_registry.new_id("sop")
            sop_docs.write_in(cur, doc_id, {"_id": doc_id, "hotel_id": hotel_id, "setting_key": key, "text": str(sop_text).strip(),
                                            "effective_from": on.isoformat(), "set_by_user_id": user_id,
                                            "set_at": datetime.now(timezone.utc).isoformat(), "source": source})
            sop_saved = True
    state = module_state(cur, hotel_id, row["module_name"], on)
    return {"result": outcome, "setting_key": key, "value": stored, "meaning": _meaning(row, stored), "effective_from": on.isoformat(),
            "previous": current["value"] if current else None, "sop_saved": sop_saved, "module": row["module_name"], "module_state": state,
            "refusal": module_refusal(state)}


def set_setting(hotel_id, user_id, key, value, effective_from=None, source="settings", note="", sop_text=None):
    """One setting in its own transaction. effective_from defaults to nothing: the caller names the date (the desk and onboarding
    pass the operating date they showed the user)."""
    if effective_from is None:
        return {"ok": False, "error": "effective_from is required"}
    return _run(lambda cur: write_setting(cur, hotel_id, user_id, key, value, effective_from, source, note, sop_text))


def today(hotel_id):
    return db.transaction(lambda cur: operating_date(cur, hotel_id))


def earliest(hotel_id, basis):
    return db.transaction(lambda cur: earliest_date(cur, hotel_id, basis))
