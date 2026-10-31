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
            if "%(hotel_name)s" not in text:
                return None, "it does not filter by the %(hotel_name)s placeholder, so it is not limited to this hotel"
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
    """The rows of a per-day recipe for the requested hotel-local day, or for a named period the recipe returns."""
    column = row_filter["column"]
    if row_filter.get("period"):
        return [row for row in rows if str(row.get(column) or "") == row_filter["period"]]
    return [row for row in rows if str(row.get(column) or "")[:10] == row_filter["day"]]


def execute_reads(statements, params, limit=31):
    """Run hotel-scoped reads in the hotel time zone. CURRENT_DATE is that hotel's today. limit None keeps every row."""
    kept, refused = _checked(statements)
    if not kept:
        return {"statements": list(statements or []), "rows": [], "error": "The query was not a hotel-scoped read: " + refused + "."}
    from booking_system.content_store import _with
    from booking_system.timezone_utils import get_hotel_timezone_str

    hotel_name = str((params or {}).get("hotel_name") or "").strip()
    zone = get_hotel_timezone_str(hotel_name) if hotel_name else ""
    if not zone:
        return {"statements": kept, "rows": [], "error": "missing hotel timezone"}

    def query(cur):
        collected = []
        cur.execute("BEGIN")
        try:
            cur.execute("SELECT set_config('TimeZone', %s, true)", (zone,))
            for statement in kept:
                cur.execute(statement, params or {})
                if cur.description is None:
                    continue
                names = [str(item[0] or "").lower() for item in cur.description]
                fetched = cur.fetchall()
                for row in (fetched[:limit] if limit else fetched):
                    collected.append({name: _plain(row[index]) for index, name in enumerate(names)})
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        return collected

    try:
        rows = _with(query)
    except Exception as exc:
        return {"statements": kept, "rows": [], "error": str(exc)}
    return {"statements": kept, "rows": rows or [], "error": ""}
