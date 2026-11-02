"""Index search, page fetch, and publication gates. The Query Agent does not read the library."""

import json


FIXTURE_HOTEL = "SANDBOX HOTEL"
ISOLATION_HOTEL = "SANDBOX ANNEX"

_CONFIRMED_SQL = (
    "SELECT COUNT(*)::int AS confirmed_reservations "
    "FROM booking "
    "WHERE hotel_id = %(hotel_id)s "
    "AND booking_status = 'confirmed'"
)
_CANCELLED_SQL = (
    "SELECT COUNT(*)::int AS cancelled_reservations "
    "FROM booking "
    "WHERE hotel_id = %(hotel_id)s "
    "AND booking_status = 'cancelled'"
)


def validate_publication(catalog_id, body, resolved_dates=None):
    """Safety, fixture, hotel isolation, and related pages. A failed gate stays unpublished."""
    from booking_system.semantic.context_engine import parameter_names, parameter_values
    from booking_system.semantic.hotel import session_hotel_id
    from booking_system.semantic.query import accepted_reads, execute_reads, read_rejection

    aliases = [str(item or "").strip() for item in (body.get("aliases") or body.get("keyphrases") or []) if str(item or "").strip()]
    if not aliases:
        return {"ok": False, "error": "aliases required"}
    solution = body.get("solution") or {}
    sql = str(solution.get("sql_template") or "").strip()
    rows = []
    if sql:
        kept = accepted_reads([sql])
        if not kept:
            return {"ok": False, "error": "sql_template is not a hotel-scoped read: " + read_rejection([sql])}
        expected = str((body.get("validation") or {}).get("expected_value") or "")
        values, missing = parameter_values(session_hotel_id(FIXTURE_HOTEL), resolved_dates or [], parameter_names(body))
        if missing:
            return {"ok": False, "error": "missing parameter " + " ".join(missing)}
        primary = execute_reads(kept, values)
        if primary.get("error"):
            return {"ok": False, "error": primary["error"]}
        rows = primary.get("rows") or []
        if rows and expected and expected not in json.dumps(rows):
            return {"ok": False, "error": "fixture mismatch"}
        absent = [str(name) for name in body.get("outputs") or [] if rows and str(name) not in rows[0]]
        if absent:
            return {"ok": False, "error": "outputs not returned by the read: " + " ".join(absent)}
        other_values = dict(values)
        other_values["hotel_id"] = session_hotel_id(ISOLATION_HOTEL)
        other = execute_reads(kept, other_values)
        if other.get("error"):
            return {"ok": False, "error": other["error"]}
        same = primary.get("rows") and primary.get("rows") == other.get("rows")
        if same and not _zero_rows(primary.get("rows")):
            return {"ok": False, "error": "hotel isolation failed"}
    related = _related_pages(catalog_id)
    if not related.get("ok"):
        return related
    return {"ok": True, "error": "", "rows": rows[:20]}


def _zero_rows(rows):
    """A zero aggregate is the same number at every hotel. It is not a copied row."""
    for row in rows or []:
        for value in row.values():
            if value not in (None, "", 0, 0.0, False):
                return False
    return True


def _related_pages(skip_id):
    from booking_system.content_store import _with
    from booking_system.semantic.query import execute_reads
    from booking_system.semantic.registry import active_body

    def run(cur):
        cur.execute("SELECT catalog_id, hotel_id, expected_value FROM knowledge_tests")
        return cur.fetchall()

    try:
        tests = _with(run) or []
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    for catalog_id, hotel_id, expected in tests:
        if str(catalog_id) == str(skip_id or ""):
            continue
        body = active_body(catalog_id) or {}
        sql = str(((body.get("solution") or {}).get("sql_template")) or "").strip()
        if not sql:
            continue
        result = execute_reads([sql], {"hotel_id": hotel_id})
        if result.get("error") or str(expected) not in json.dumps(result.get("rows") or []):
            return {"ok": False, "error": "related page failed " + str(catalog_id)}
    return {"ok": True, "error": ""}


def _publish_template(catalog_id, sql, expected, method):
    """Activate one validated template. The previous version stays in the registry."""
    from booking_system.content_store import _transaction
    from booking_system.semantic.hotel import session_hotel_id
    from booking_system.semantic.registry import active_body, refresh_page_index
    from booking_system.semantic import catalog as seed
    from psycopg2.extras import Json

    body = active_body(catalog_id) or {}
    if str(((body.get("solution") or {}).get("sql_template")) or "").strip():
        return {"ok": True, "error": ""}
    published = dict(body)
    published["aliases"] = list(seed.ALIASES.get(catalog_id) or body.get("aliases") or [])
    published["solution"] = {"sql_template": sql, "method": method}
    published["validation"] = {"expected_value": str(expected)}
    report = validate_publication(catalog_id, published)
    if not report.get("ok"):
        return report

    def run(cur):
        cur.execute(
            "SELECT version FROM catalog_active_versions WHERE scope = 'global' AND catalog_id = %s",
            (catalog_id,),
        )
        prior = cur.fetchone()
        previous = int(prior[0]) if prior else None
        cur.execute("SELECT COALESCE(MAX(version), 0) FROM catalog_versions WHERE catalog_id = %s", (catalog_id,))
        nxt = int(cur.fetchone()[0]) + 1
        cur.execute(
            """
            INSERT INTO catalog_versions (catalog_id, version, body, provenance)
            VALUES (%s, %s, %s, %s)
            """,
            (catalog_id, nxt, Json(published), Json({"source": "fixture"})),
        )
        cur.execute(
            """
            INSERT INTO catalog_active_versions (scope, catalog_id, version)
            VALUES ('global', %s, %s)
            ON CONFLICT (scope, catalog_id) DO UPDATE
            SET version = EXCLUDED.version, activated_at = NOW()
            """,
            (catalog_id, nxt),
        )
        cur.execute(
            """
            INSERT INTO knowledge_tests (catalog_id, hotel_id, expected_value)
            VALUES (%s, %s, %s)
            ON CONFLICT (catalog_id, hotel_id) DO UPDATE SET expected_value = EXCLUDED.expected_value
            """,
            (catalog_id, session_hotel_id(FIXTURE_HOTEL), str(expected)),
        )
        cur.execute(
            """
            INSERT INTO knowledge_publications (catalog_id, version, previous_version)
            VALUES (%s, %s, %s)
            """,
            (catalog_id, nxt, previous),
        )
        refresh_page_index(cur, catalog_id)
        return {"ok": True, "version": nxt}

    return _transaction(run)


def ensure_confirmed_page(confirmed_expected="", cancelled_expected=""):
    """Publish validated templates for the reservation counts. The expected values are the fixture hotel's counts."""
    confirmed = _publish_template(
        "B01a",
        _CONFIRMED_SQL,
        confirmed_expected,
        "Count confirmed bookings for the hotel.",
    )
    cancelled = _publish_template(
        "B01c",
        _CANCELLED_SQL,
        cancelled_expected,
        "Count cancelled bookings for the hotel.",
    )
    return {"confirmed": confirmed, "cancelled": cancelled}


def prepare_read(question):
    """Search the index, then choose up to three pages. Summaries only."""
    import time
    from booking_system.semantic.registry import search_knowledge

    started = time.monotonic()
    shortlist = search_knowledge(question, 8)
    search_ms = int((time.monotonic() - started) * 1000)
    lines = []
    for row in shortlist:
        lines.append(row["catalog_id"] + " " + str(row.get("title") or "") + ". " + str(row.get("summary") or ""))
    schema = {
        "type": "object",
        "properties": {
            "use_desk": {"type": "boolean"},
            "page_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["use_desk", "page_ids"],
        "additionalProperties": False,
    }
    from booking_system.llm_utils import haiku_text
    raw = haiku_text(
        (
            "Choose up to three page ids whose summaries answer the sentence. "
            "use_desk is true for a greeting or a request to change a booking, take a payment, or assign a task. "
            "A factual question has use_desk false."
        ),
        "Pages\n" + ("\n".join(lines) if lines else "None") + "\nSentence\n" + str(question or ""),
        schema=schema,
        max_tokens=8000,
        timeout=90,
    )
    choice_ms = int((time.monotonic() - started) * 1000) - search_ms
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {}
    allowed = {row["catalog_id"] for row in shortlist}
    ids = []
    for item in parsed.get("page_ids") or []:
        name = str(item or "")
        if name in allowed and name not in ids and len(ids) < 3:
            ids.append(name)
    return {
        "use_desk": parsed.get("use_desk") is True,
        "page_ids": ids,
        "resolved_dates": {"resolutions": []},
        "shortlist": shortlist,
        "search_ms": search_ms,
        "choice_ms": choice_ms,
    }


def _rows_reply(rows, limitation=""):
    lines = []
    for row in rows or []:
        parts = [str(key) + " " + str(value) for key, value in row.items() if key != "hotel_name" and value is not None]
        if parts:
            lines.append(" ".join(parts))
    body = "\n".join(lines) if lines else "No rows returned"
    note = str(limitation or "").strip()
    return body + "\n" + note if note else body


def answer_prepared(hotel_id, question, prepared):
    """Run each chosen page's stored template. A page without one is not answered by writing SQL."""
    from booking_system.semantic.query import execute_reads
    from booking_system.semantic.registry import active_body
    import time
    hotel_id = str(hotel_id or "")
    ids = list((prepared or {}).get("page_ids") or [])
    shortlist = (prepared or {}).get("shortlist") or []
    parts = []
    rows = []
    error = ""
    fetch_ms = 0
    sql_ms = 0
    ran = False
    for page_id in ids:
        started = time.monotonic()
        body = active_body(page_id) or {}
        fetch_ms += int((time.monotonic() - started) * 1000)
        sql = str(((body.get("solution") or {}).get("sql_template")) or "").strip()
        if not sql:
            continue
        ran = True
        started = time.monotonic()
        result = execute_reads([sql], {"hotel_id": hotel_id})
        sql_ms += int((time.monotonic() - started) * 1000)
        error = error or result.get("error") or ""
        page_rows = result.get("rows") or []
        rows.extend(page_rows)
        parts.append(_rows_reply(page_rows))
    if not ran:
        return {
            "reply": "",
            "rows": [],
            "error": "no suitable recipe",
            "statements": [],
            "stall_reason": "",
            "trace_id": None,
            "page_ids": ids,
            "fetch_ms": fetch_ms,
            "sql_ms": sql_ms,
            "shortlist": shortlist,
        }
    reply = "\n".join(part for part in parts if part) or "No rows returned"
    return {
        "reply": reply,
        "rows": rows,
        "error": error,
        "stall_reason": "",
        "trace_id": None,
        "page_ids": ids,
        "fetch_ms": fetch_ms,
        "sql_ms": sql_ms,
    }
