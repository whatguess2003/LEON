"""The gateway runs a hotel-scoped SELECT. The LangGraph agent writes it."""


_FORBIDDEN = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "GRANT",
    "TRUNCATE", "COPY", "CALL", "EXECUTE", "DO", "INTO", "SET",
}

def _words(sql):
    word = []
    words = []
    for char in str(sql or ""):
        if char.isalnum() or char == "_":
            word.append(char)
        else:
            if word:
                words.append("".join(word).upper())
                word = []
    if word:
        words.append("".join(word).upper())
    return words


def _statements(sql):
    parts = []
    current = []
    quote = ""
    for char in str(sql or ""):
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            continue
        if char == ";":
            text = "".join(current).strip()
            if text:
                parts.append(text)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _checked(statements):
    """The statements kept, and the rule the first refused statement broke."""
    kept = []
    for statement in statements or []:
        for part in _statements(statement):
            text = part.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                if lines and lines[-1].strip() == "```":
                    lines = lines[1:-1]
                else:
                    lines = lines[1:]
                text = "\n".join(lines).strip()
            words = _words(text)
            if not words or words[0] not in ("SELECT", "WITH"):
                return None, "it does not start with SELECT or WITH"
            refused = sorted({word for word in words if word in _FORBIDDEN})
            if refused:
                return None, "it uses " + ", ".join(refused) + ", which a read may not use"
            if "%(hotel_id)s" not in text:
                return None, "it does not filter by the %(hotel_id)s placeholder, so it is not limited to this hotel"
            kept.append(text)
            if len(kept) > 6:
                return None, "it has more than 6 statements"
    if not kept:
        return None, "it is empty"
    return kept, ""


def accepted_reads(statements):
    """Keep a written statement only when it is one hotel-scoped SELECT."""
    return _checked(statements)[0]


def read_rejection(statements):
    """Why the gateway refuses these statements; empty when it accepts them."""
    return _checked(statements)[1]


def _plain(value):
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def rows_for(rows, row_filter):
    """Keep rows whose period column matches the desk's value. MM/DD/YYYY is compared as YYYY-MM-DD. A named key is compared as itself."""
    from booking_system.semantic.dates import recipe_period

    column = row_filter["column"]
    raw = str(row_filter.get("period") or row_filter.get("day") or "")
    if not raw:
        return list(rows or [])
    wanted = recipe_period(raw)
    return [row for row in rows or [] if str(row.get(column) or "") == wanted or str(row.get(column) or "")[:10] == wanted]


def _other_hotels(sql, hotel_id):
    """Another hotel's id written into the statement. The bound hotel id is the only one allowed."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute("SELECT hotel_id FROM hotel")
        return [str(row[0]) for row in cur.fetchall()]

    text = "\n".join(str(item or "") for item in sql or [])
    allowed = str(hotel_id or "")
    return [item for item in _with(run) if item and item != allowed and item in text]


def _named_placeholder_end(text, index):
    """Index after %(name)s. Zero when this percent is a literal, including %s and a percent inside a pattern."""
    cursor = index + 1
    if cursor >= len(text) or text[cursor] != "(":
        return 0
    cursor += 1
    start = cursor
    while cursor < len(text) and (text[cursor].isalnum() or text[cursor] == "_"):
        cursor += 1
    if cursor == start or not text.startswith(")s", cursor):
        return 0
    return cursor + 2


def _executed_sql(statement):
    """The statement cursor.execute receives. %(name)s stays so the parameter dict still binds.
    Any other percent, including a pattern such as '%date%' or a bare %s, is written as %%."""
    text = str(statement or "")
    pieces = []
    index = 0
    size = len(text)
    while index < size:
        if text[index] != "%":
            pieces.append(text[index])
            index += 1
            continue
        if text.startswith("%%", index):
            pieces.append("%%")
            index += 2
            continue
        name_end = _named_placeholder_end(text, index)
        if name_end:
            pieces.append(text[index:name_end])
            index = name_end
            continue
        pieces.append("%%")
        index += 1
    return "".join(pieces)


def execute_reads(statements, params, limit=31):
    """Run hotel-scoped reads in the hotel time zone. CURRENT_DATE is that hotel's today. limit None keeps every row.
    params carry the session hotel_id; the time zone is read from its hotel row.
    The parameter dict stays the argument to cursor.execute. A literal percent in the executed statement is %%."""
    kept, refused = _checked(statements)
    if not kept:
        return {"statements": list(statements or []), "rows": [], "error": "The query was not a hotel-scoped read: " + refused + "."}
    from booking_system.content_store import _with, with_settings
    from booking_system.semantic.hotel import hotel_row

    params = dict(params or {})
    hotel_id = str(params.get("hotel_id") or "")
    others = _other_hotels(kept, hotel_id)
    if others:
        return {"statements": kept, "rows": [], "error": "cross-hotel access is blocked"}
    hotel = hotel_row(hotel_id)
    if not hotel:
        return {"statements": kept, "rows": [], "error": "hotel is not registered"}
    zone = hotel["timezone"]
    params = dict(params)

    def query(cur):
        collected = []
        for statement in kept:
            cur.execute(_executed_sql(statement), params)
            if cur.description is None:
                continue
            names = [str(item[0] or "").lower() for item in cur.description]
            fetched = cur.fetchall()
            for row in (fetched[:limit] if limit else fetched):
                collected.append({name: _plain(row[index]) for index, name in enumerate(names)})
        return collected

    try:
        rows = _with(lambda cur: with_settings(cur, (("TimeZone", zone), ("statement_timeout", 8000)), query))
    except Exception as exc:
        return {"statements": kept, "rows": [], "error": str(exc)}
    return {"statements": kept, "rows": rows or [], "error": ""}
