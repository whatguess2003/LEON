"""DDL helpers the area schema modules share. Each runs on the autocommit DDL cursor with core_schema.catalog(cur) as `seen`."""


def create_tables(cur, seen, tables, carried=None):
    """tables: ((name, body), ...). A missing table is created. A table still in its hotel_name shape is replaced in one transaction:
    carried[name] selects its rows in the new column order (joining hotel by hotel_name), the old table is dropped, the new one is
    created and the rows are inserted. A table with no carried query has no rows worth keeping."""
    for name, body in tables:
        if name in seen["hotel_name_tables"]:
            cur.execute("BEGIN")
            if (carried or {}).get(name):
                cur.execute("CREATE TEMP TABLE carried_rows ON COMMIT DROP AS " + carried[name])
            cur.execute("DROP TABLE " + name + " CASCADE")
            cur.execute("CREATE TABLE " + name + " (" + body + ")")
            if (carried or {}).get(name):
                cur.execute("INSERT INTO " + name + " SELECT * FROM carried_rows")
            cur.execute("COMMIT")
            seen["hotel_name_tables"].discard(name)
            seen["relations"].add(name)
        elif name not in seen["relations"]:
            cur.execute("CREATE TABLE " + name + " (" + body + ")")
            seen["relations"].add(name)


def create_indexes(cur, seen, indexes):
    """indexes: ((name, 'table (columns)'), ...), created when missing."""
    for name, target in indexes:
        if name not in seen["relations"]:
            cur.execute("CREATE INDEX " + name + " ON " + target)
            seen["relations"].add(name)


def drop_tables(cur, seen, names):
    """Tables nothing reads any more, dropped when present."""
    for name in names:
        if name in seen["relations"]:
            cur.execute("DROP TABLE IF EXISTS " + name + " CASCADE")
            seen["relations"].discard(name)
            seen["hotel_name_tables"].discard(name)
