"""Schema columns and accepted SQL. The question text is not a key. Rows are read again on a hit."""

import hashlib
from datetime import datetime, timezone
READ_PARAMS = ("booking_id", "guest_phone", "guest_name", "room_type", "stay_dates", "room_number")
_SCHEMA = {}


def usable(parameters):
    """Keep stated operational parameters. An empty result skips the SQL cache."""
    found = {}
    pairs = []
    if isinstance(parameters, dict):
        pairs = list(parameters.items())
    else:
        for item in parameters or []:
            if isinstance(item, dict):
                pairs.append((item.get("name"), item.get("value")))
    for name, value in pairs:
        key = str(name or "")
        text = str(value or "").strip()
        if key in READ_PARAMS and text and key not in found:
            found[key] = text
    return found


def _versions(catalog_ids):
    from booking_system.semantic.catalog import ready_ids
    from booking_system.semantic.registry import list_active_catalogs
    ids = sorted(str(item or "") for item in (catalog_ids or []) if str(item or ""))
    by_id = {row["catalog_id"]: row for row in list_active_catalogs()}
    parts = []
    tables = []
    for map_id in ready_ids(ids):
        entry = by_id.get(map_id) or {}
        for concept in entry.get("concepts") or []:
            name = str(concept.get("name") or "")
            parts.append(name + ":" + str(concept.get("version") or ""))
            for table in concept.get("tables") or []:
                if table not in tables:
                    tables.append(table)
    return ids, "|".join(parts), tables


def _material(hotel_id, catalog_ids, parameters):
    ids, versions, _tables = _versions(catalog_ids)
    pairs = [name + "=" + parameters[name] for name in sorted(parameters)]
    return "|".join([
        str(hotel_id or ""),
        ",".join(ids),
        versions,
        ",".join(pairs),
    ])


def _key(hotel, catalog_ids, parameters):
    return hashlib.sha256(_material(hotel, catalog_ids, parameters).encode("utf-8")).hexdigest()


def schema_text(catalog_ids):
    """Column names and types for the catalog tables. Sample rows are not included."""
    _ids, versions, tables = _versions(catalog_ids)
    if not tables:
        return ""
    token = versions + "|" + ",".join(tables)
    cached = _SCHEMA.get(token)
    if cached:
        return cached
    loaded = _load_columns(tables)
    if not loaded:
        return ""
    _SCHEMA[token] = loaded
    return loaded


def _load_columns(tables):
    from booking_system.content_store import with_settings
    from booking_system.guest_manager import guest_manager

    def read(cur):
        cur.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ANY(%s)
            ORDER BY table_name, ordinal_position
            """,
            (list(tables),),
        )
        return cur.fetchall()

    try:
        rows = guest_manager._with_pg(lambda cur: with_settings(cur, (("statement_timeout", 8000),), read))
    except Exception:
        return ""
    grouped = {}
    for table, column, data_type in rows or []:
        grouped.setdefault(table, []).append(str(column) + " " + str(data_type))
    lines = []
    for table in tables:
        columns = grouped.get(table)
        if not columns:
            return ""
        lines.append(table + "(" + ", ".join(columns) + ")")
    return "\n".join(lines)


def get(hotel, catalog_ids, parameters):
    from booking_system.runtime_state import read_sql
    params = usable(parameters)
    if not params:
        return None
    payload = read_sql(hotel, _key(hotel, catalog_ids, params))
    if not isinstance(payload, dict) or not payload.get("statements"):
        return None
    return payload


def put(hotel, catalog_ids, parameters, statements):
    from booking_system.runtime_state import write_sql
    params = usable(parameters)
    kept = [str(item) for item in (statements or []) if str(item or "").strip()]
    if not params or not kept:
        return
    write_sql(hotel, _key(hotel, catalog_ids, params), {
        "statements": kept,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    })


def drop(hotel, catalog_ids, parameters):
    from booking_system.runtime_state import delete_sql
    params = usable(parameters)
    if not params:
        return
    delete_sql(hotel, _key(hotel, catalog_ids, params))


def invalidate(hotel_id):
    """Drop this hotel's accepted SQL. Other hotels stay."""
    from booking_system.runtime_state import delete_hotel_sql
    delete_hotel_sql(hotel_id)
