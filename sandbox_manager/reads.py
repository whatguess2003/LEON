"""Read-only hotel database access for the sandbox process.

Operational hotel tables are not written here. Recipe-library writes live in library.py.
The connection is the application's own database URL, read by booking_system.db.
"""

from sandbox_manager.config import public_text


def _with(fn):
    from booking_system.content_store import _with as run
    return run(fn)


def public_tables():
    def run(cur):
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )
        return [str(row[0]) for row in cur.fetchall()]

    try:
        return _with(run)
    except Exception:
        return []


def columns(table):
    name = str(table or "").strip()

    def run(cur):
        cur.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (name,),
        )
        return [str(column) + " " + str(data_type) for column, data_type in cur.fetchall()]

    try:
        found = _with(run)
    except Exception as exc:
        return {"table": name, "columns": [], "error": public_text(exc)}
    return {"table": name, "columns": found or []}


def search_sql(sql, start, end, hotel_id):
    """One hotel-scoped read. hotel_id is the session hotel, bound here."""
    try:
        from booking_system.semantic.query import execute_reads
        result = execute_reads(
            [str(sql or "")],
            {"hotel_id": str(hotel_id or ""), "start": str(start or ""), "end": str(end or "")},
            limit=15,
        )
    except Exception as exc:
        return {"rows": [], "error": public_text(exc)}
    return {"rows": result.get("rows") or [], "error": str(result.get("error") or "")}


def recipe_turns(hotel_id, user_id):
    def run(cur):
        cur.execute(
            """
            SELECT state FROM desk_session
            WHERE hotel_id = %s AND upper(manager_name) = upper(%s)
            """,
            (str(hotel_id or ""), str(user_id or "")),
        )
        row = cur.fetchone()
        return row[0] if row else {}

    try:
        state = _with(run) or {}
    except Exception:
        return []
    if not isinstance(state, dict):
        return []
    turns = state.get("recipe_turns")
    return list(turns) if isinstance(turns, list) else []


def recipes_this_turn(before, after):
    grown = len(after) > len(before)
    fresh = not before and bool(after)
    last = after[-1] if (grown or fresh) and after else []
    if not isinstance(last, list):
        return []
    found = []
    for item in last:
        text = str(item or "").strip()
        if text and text not in found:
            found.append(text)
    return found


def recipe_cards(catalog_ids):
    ids = [str(item) for item in catalog_ids or [] if str(item or "").strip()]
    if not ids:
        return []
    try:
        from booking_system.semantic.registry import active_pages
        pages = active_pages(ids) or {}
    except Exception:
        pages = {}
    cards = []
    for catalog_id in ids:
        page = pages.get(catalog_id) or {}
        body = page.get("body") if isinstance(page.get("body"), dict) else {}
        solution = body.get("solution") if isinstance(body.get("solution"), dict) else {}
        cards.append({
            "id": catalog_id,
            "version": page.get("version"),
            "problem": str(body.get("problem") or ""),
            "output_grain": str(body.get("output_grain") or ""),
            "outputs": [str(item) for item in body.get("outputs") or []],
            "sql_template": str(solution.get("sql_template") or ""),
        })
    return cards


def latest_audit(hotel_id, user_id):
    """The newest audit row for this desk session, or None when the log cannot be read."""
    def run(cur):
        cur.execute(
            """
            SELECT max(created_at) FROM audit_fixes
            WHERE hotel_id = %s AND upper(manager_name) = upper(%s)
            """,
            (str(hotel_id or ""), str(user_id or "")),
        )
        row = cur.fetchone()
        return row[0] if row else None

    try:
        return _with(run)
    except Exception:
        return None


def audit_triggered(hotel_id, user_id, previous):
    """True when a new audit_fixes row exists after the flag turn. The desk writes that row when it calls audit_answer."""
    current = latest_audit(hotel_id, user_id)
    if current is None:
        return False
    if previous is None:
        return True
    return current != previous
