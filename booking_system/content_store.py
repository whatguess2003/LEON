"""PostgreSQL documents. Call sites keep collection queries."""

import json
import os
import re
import threading
from datetime import datetime

import psycopg2
from psycopg2.extras import Json

_LOCAL = threading.local()


def _database_url():
    from booking_system.db import database_url
    return database_url()


def _connection():
    """One connection per thread. The catalog agent does not share the desk connection."""
    conn = getattr(_LOCAL, "conn", None)
    if conn is None or conn.closed:
        conn = psycopg2.connect(_database_url(), keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3)
        conn.autocommit = True
        _LOCAL.conn = conn
    return conn


def with_settings(cur, settings, fn):
    """Run fn in one transaction whose settings end with it, so a pooled connection keeps no session state.
    BEGIN and the settings travel in one round trip."""
    pairs = list(settings)
    calls = ", ".join("set_config(%s, %s, true)" for _pair in pairs)
    cur.execute("BEGIN;" + (" SELECT " + calls if calls else ""), [part for name, value in pairs for part in (name, str(value))])
    try:
        result = fn(cur)
        cur.execute("COMMIT")
        return result
    except Exception:
        cur.execute("ROLLBACK")
        raise


def ensure_tables():
    def run(cur):
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS content_document (
                database_name VARCHAR NOT NULL,
                collection_name VARCHAR NOT NULL,
                document_key VARCHAR NOT NULL,
                body JSONB NOT NULL,
                PRIMARY KEY (database_name, collection_name, document_key)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS guest_media_file (
                source_ref VARCHAR NOT NULL PRIMARY KEY,
                mime VARCHAR,
                filename VARCHAR,
                data BYTEA NOT NULL
            )
            """
        )
    _with(run)


def _with(fn):
    conn = _connection()
    cur = conn.cursor()
    try:
        return fn(cur)
    except psycopg2.Error:
        try:
            conn.close()
        except Exception:
            pass
        _LOCAL.conn = None
        raise
    finally:
        cur.close()


DDL_LOCK_TIMEOUT = "5s"


def _present(cur, table, seen):
    """Column names of table: from the startup catalog snapshot when the caller has one, else one catalog read."""
    if seen is not None:
        return seen.columns(table)
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s", (table,))
    return {row[0] for row in cur.fetchall()}


def add_column(cur, table, column, definition, seen=None):
    """Add a column only when it is missing. ALTER TABLE takes an exclusive lock even when the column exists,
    so a startup that reads first takes no lock, and a needed ALTER waits at most DDL_LOCK_TIMEOUT."""
    if column in _present(cur, table, seen):
        return False
    cur.execute("SELECT current_setting('lock_timeout')")
    previous = cur.fetchone()[0]
    cur.execute("SET lock_timeout = '" + DDL_LOCK_TIMEOUT + "'")
    try:
        cur.execute("ALTER TABLE " + table + " ADD COLUMN IF NOT EXISTS " + column + " " + definition)
    finally:
        cur.execute("SELECT set_config('lock_timeout', %s, false)", (previous,))
    return True


def hotel_column(cur, table, key=(), seen=None):
    """A semantic or chat row names its hotel by hotel_id, a foreign key to hotel. A table still carrying hotel_name is switched once:
    its rows take the id of the hotel of that name, rows of an unregistered hotel are dropped, and key becomes the primary key."""
    present = _present(cur, table, seen) & {"hotel_id", "hotel_name"}
    if "hotel_name" not in present:
        return False
    cur.execute("SET lock_timeout = '" + DDL_LOCK_TIMEOUT + "'")
    if "hotel_id" not in present:
        cur.execute("ALTER TABLE " + table + " ADD COLUMN hotel_id VARCHAR(48)")
    cur.execute("UPDATE " + table + " t SET hotel_id = h.hotel_id FROM hotel h WHERE t.hotel_id IS NULL AND h.hotel_name = t.hotel_name")
    cur.execute("DELETE FROM " + table + " WHERE hotel_id IS NULL OR hotel_id NOT IN (SELECT hotel_id FROM hotel)")
    cur.execute("ALTER TABLE " + table + " DROP COLUMN hotel_name")
    cur.execute("ALTER TABLE " + table + " ALTER COLUMN hotel_id SET NOT NULL")
    cur.execute(
        "SELECT 1 FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'f' AND confrelid = 'hotel'::regclass",
        (table,),
    )
    if not cur.fetchone():
        cur.execute("ALTER TABLE " + table + " ADD FOREIGN KEY (hotel_id) REFERENCES hotel (hotel_id)")
    if key:
        cur.execute("ALTER TABLE " + table + " DROP CONSTRAINT IF EXISTS " + table + "_pkey")
        cur.execute("ALTER TABLE " + table + " ADD PRIMARY KEY (" + ", ".join(key) + ")")
    cur.execute("CREATE INDEX IF NOT EXISTS " + table + "_hotel_id_idx ON " + table + " (hotel_id)")
    return True


def _transaction(fn):
    """One connection that commits the page and its index together, or rolls both back."""
    conn = psycopg2.connect(_database_url())
    conn.autocommit = False
    cur = conn.cursor()
    try:
        result = fn(cur)
        conn.commit()
        return result
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        cur.close()
        conn.close()


def _plain(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def document_key(doc):
    """An explicit _id is the key; otherwise the natural-key fields the document carries."""
    body = doc or {}
    if body.get("_id") not in (None, ""):
        return str(body["_id"])
    parts = []
    for field in (
        "hotel_id", "hotel_name", "manager_phone", "user_id", "guest_id", "guest_phone", "booking_id", "room_type_id", "room_id", "room_type", "room_number",
        "policy_type", "keyword", "rate_plan", "pricing_name", "source_ref", "card", "day", "task_id",
    ):
        value = body.get(field)
        if value not in (None, ""):
            parts.append(field + "=" + str(value))
    return "|".join(parts)


def _path(doc, name):
    current = doc
    for part in str(name or "").split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _has(doc, name):
    current = doc
    parts = str(name or "").split(".")
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return isinstance(current, dict) and parts[-1] in current


def _set_path(doc, name, value):
    current = doc
    parts = str(name or "").split(".")
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    current[parts[-1]] = value


def _unset_path(doc, name):
    current = doc
    parts = str(name or "").split(".")
    for part in parts[:-1]:
        current = current.get(part) if isinstance(current, dict) else None
        if not isinstance(current, dict):
            return
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _compare(left, right):
    if isinstance(right, datetime) and not isinstance(left, datetime):
        right = right.isoformat()
    if isinstance(left, datetime) and not isinstance(right, datetime):
        left = left.isoformat()
    try:
        return (left > right) - (left < right)
    except TypeError:
        return (str(left) > str(right)) - (str(left) < str(right))


def _ops(value, expected, present):
    if "$in" in expected and value not in expected["$in"]:
        return False
    if "$nin" in expected and value in expected["$nin"]:
        return False
    if "$ne" in expected and value == expected["$ne"]:
        return False
    if "$exists" in expected and bool(present) != bool(expected["$exists"]):
        return False
    if "$gt" in expected and not (present and _compare(value, expected["$gt"]) > 0):
        return False
    if "$gte" in expected and not (present and _compare(value, expected["$gte"]) >= 0):
        return False
    if "$lt" in expected and not (present and _compare(value, expected["$lt"]) < 0):
        return False
    if "$lte" in expected and not (present and _compare(value, expected["$lte"]) <= 0):
        return False
    if "$regex" in expected:
        flags = re.IGNORECASE if "i" in str(expected.get("$options") or "") else 0
        if not present or re.search(str(expected["$regex"]), str(value), flags) is None:
            return False
    return True


def _match(doc, query):
    if not query:
        return True
    if "$and" in query:
        return all(_match(doc, part) for part in query["$and"])
    if "$or" in query:
        return any(_match(doc, part) for part in query["$or"])
    for key, expected in query.items():
        if str(key).startswith("$"):
            continue
        present = _has(doc, key)
        value = _path(doc, key) if present else None
        if isinstance(expected, dict) and any(str(item).startswith("$") for item in expected):
            if not _ops(value, expected, present):
                return False
        elif value != expected:
            return False
    return True


def _project(doc, projection):
    if not projection:
        return dict(doc)
    include = [key for key, flag in projection.items() if flag and key != "_id"]
    if include:
        out = {key: _path(doc, key) for key in include}
    else:
        out = dict(doc)
    if projection.get("_id") == 0:
        out.pop("_id", None)
    return out


def _group(docs, spec):
    grouped = {}
    id_field = spec.get("_id")
    for doc in docs:
        if id_field in (None, "null"):
            identity = None
        elif isinstance(id_field, str) and id_field.startswith("$"):
            identity = _path(doc, id_field[1:])
        else:
            identity = id_field
        row = grouped.setdefault(identity, {"_id": identity})
        for name, calc in spec.items():
            if name == "_id" or not isinstance(calc, dict):
                continue
            if "$sum" in calc:
                row[name] = row.get(name, 0) + (1 if calc["$sum"] == 1 else float(_path(doc, str(calc["$sum"])[1:]) or 0))
            if "$avg" in calc:
                bucket = row.setdefault("_avg_" + name, [])
                raw = str(calc["$avg"])
                number = _path(doc, raw[1:]) if raw.startswith("$") else calc["$avg"]
                if isinstance(number, (int, float)):
                    bucket.append(float(number))
                row[name] = sum(bucket) / len(bucket) if bucket else 0
    return list(grouped.values())


class _Result:
    def __init__(self, **fields):
        for key, value in fields.items():
            setattr(self, key, value)


class Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        pairs = key if isinstance(key, list) else [(key, direction)]
        for field, way in reversed(pairs):
            self._docs.sort(key=lambda doc: str(_path(doc, field) if _path(doc, field) is not None else ""), reverse=int(way) < 0)
        return self

    def limit(self, count):
        self._docs = self._docs[: int(count)]
        return self

    def __iter__(self):
        return iter(self._docs)


class Collection:
    def __init__(self, database_name, collection_name):
        self.database_name = database_name
        self.collection_name = collection_name

    def _rows(self):
        def run(cur):
            cur.execute(
                """
                SELECT document_key, body FROM content_document
                WHERE database_name = %s AND collection_name = %s
                """,
                (self.database_name, self.collection_name),
            )
            return cur.fetchall()
        return _with(run)

    def write_in(self, cur, key, body):
        """Write one document on the caller's cursor, so it commits with the caller's table writes."""
        cur.execute(
            """
            SELECT body FROM content_document
            WHERE database_name = %s AND collection_name = %s AND document_key = %s
            """,
            (self.database_name, self.collection_name, key),
        )
        if cur.fetchone():
            cur.execute(
                """
                UPDATE content_document SET body = %s
                WHERE database_name = %s AND collection_name = %s AND document_key = %s
                """,
                (Json(body), self.database_name, self.collection_name, key),
            )
        else:
            cur.execute(
                """
                INSERT INTO content_document (database_name, collection_name, document_key, body)
                VALUES (%s, %s, %s, %s)
                """,
                (self.database_name, self.collection_name, key, Json(body)),
            )

    def _write(self, key, body):
        _with(lambda cur: self.write_in(cur, key, body))

    def _delete_key(self, key):
        def run(cur):
            cur.execute(
                """
                DELETE FROM content_document
                WHERE database_name = %s AND collection_name = %s AND document_key = %s
                """,
                (self.database_name, self.collection_name, key),
            )
        _with(run)

    def _matched(self, query):
        found = []
        for key, body in self._rows():
            doc = body if isinstance(body, dict) else json.loads(body)
            doc["_document_key"] = key
            if _match(doc, query):
                found.append(doc)
        return found

    def find(self, query=None, projection=None):
        docs = []
        for doc in self._matched(query or {}):
            shown = _project(doc, projection)
            shown.pop("_document_key", None)
            docs.append(shown)
        return Cursor(docs)

    def find_one(self, query=None, projection=None):
        for doc in self.find(query or {}, projection):
            return doc
        return None

    def distinct(self, field, query=None):
        seen = []
        for doc in self._matched(query or {}):
            value = _path(doc, field)
            if value not in seen:
                seen.append(value)
        return seen

    def count_documents(self, query=None):
        return len(self._matched(query or {}))

    def estimated_document_count(self):
        return self.count_documents({})

    def insert_one(self, doc):
        body = _plain(dict(doc or {}))
        key = document_key(body)
        self._write(key, body)
        return _Result(inserted_id=body.get("_id") or key)

    def insert_many(self, docs):
        ids = []
        for doc in docs or []:
            ids.append(self.insert_one(doc).inserted_id)
        return _Result(inserted_ids=ids)

    def _apply(self, doc, update):
        body = dict(doc)
        body.pop("_document_key", None)
        for name, value in (update or {}).get("$set", {}).items():
            _set_path(body, name, _plain(value))
        for name in (update or {}).get("$unset", {}):
            _unset_path(body, name)
        for name, value in (update or {}).get("$inc", {}).items():
            current = _path(body, name) or 0
            _set_path(body, name, current + value)
        return body

    def staged_update(self, query, update, upsert=False):
        """The (key, body) update_one would write, without writing. None when nothing matches and upsert is off."""
        matched = self._matched(query or {})
        if matched:
            return matched[0]["_document_key"], self._apply(matched[0], update)
        if not upsert:
            return None
        body = {key: _plain(value) for key, value in (query or {}).items() if not str(key).startswith("$") and not isinstance(value, dict)}
        body = self._apply(body, update)
        return document_key(body), body

    def staged_insert(self, doc):
        body = _plain(dict(doc or {}))
        return document_key(body), body

    def update_one(self, query, update, upsert=False):
        matched = self._matched(query or {})
        if not matched:
            if not upsert:
                return _Result(matched_count=0, modified_count=0, upserted_id=None)
            body = {}
            for key, value in (query or {}).items():
                if not str(key).startswith("$") and not isinstance(value, dict):
                    body[key] = _plain(value)
            body = self._apply(body, update)
            key = document_key(body)
            self._write(key, body)
            return _Result(matched_count=0, modified_count=0, upserted_id=key)
        doc = matched[0]
        key = doc["_document_key"]
        body = self._apply(doc, update)
        self._write(key, body)
        return _Result(matched_count=1, modified_count=1, upserted_id=None)

    def update_many(self, query, update, upsert=False):
        matched = self._matched(query or {})
        if not matched:
            result = self.update_one(query, update, upsert=upsert)
            return result
        for doc in matched:
            self._write(doc["_document_key"], self._apply(doc, update))
        return _Result(matched_count=len(matched), modified_count=len(matched), upserted_id=None)

    def replace_one(self, query, doc, upsert=False):
        matched = self._matched(query or {})
        body = _plain(dict(doc or {}))
        if not matched:
            if not upsert:
                return _Result(matched_count=0, modified_count=0, upserted_id=None)
            key = document_key(body)
            self._write(key, body)
            return _Result(matched_count=0, modified_count=0, upserted_id=key)
        self._write(matched[0]["_document_key"], body)
        return _Result(matched_count=1, modified_count=1, upserted_id=None)

    def delete_one(self, query):
        matched = self._matched(query or {})
        if not matched:
            return _Result(deleted_count=0)
        self._delete_key(matched[0]["_document_key"])
        return _Result(deleted_count=1)

    def delete_many(self, query):
        matched = self._matched(query or {})
        for doc in matched:
            self._delete_key(doc["_document_key"])
        return _Result(deleted_count=len(matched))

    def aggregate(self, pipeline):
        docs = []
        for doc in self._matched({}):
            doc.pop("_document_key", None)
            docs.append(doc)
        for stage in pipeline or []:
            if "$match" in stage:
                docs = [doc for doc in docs if _match(doc, stage["$match"])]
            elif "$group" in stage:
                docs = _group(docs, stage["$group"])
            elif "$limit" in stage:
                docs = docs[: int(stage["$limit"])]
            elif "$sort" in stage:
                docs = list(Cursor(docs).sort(list(stage["$sort"].items())))
        return docs

    def create_index(self, *args, **kwargs):
        return None

    def list_indexes(self):
        return []


class Database:
    def __init__(self, name):
        self.name = name

    def __getitem__(self, collection_name):
        return Collection(self.name, collection_name)

    def __getattr__(self, collection_name):
        return self[collection_name]

    def get_collection(self, collection_name):
        return self[collection_name]

    def list_collection_names(self):
        def run(cur):
            cur.execute(
                "SELECT DISTINCT collection_name FROM content_document WHERE database_name = %s ORDER BY collection_name",
                (self.name,),
            )
            return [row[0] for row in cur.fetchall()]
        return _with(run)

    def command(self, name, *args, **kwargs):
        return {"ok": 1}


class _Admin:
    def command(self, name, *args, **kwargs):
        return {"ok": 1}


class Client:
    def __init__(self):
        self.admin = _Admin()

    def __getitem__(self, database_name):
        return Database(database_name)

    def __getattr__(self, database_name):
        return self[database_name]

    def get_database(self, database_name):
        return self[database_name]

    def close(self):
        return None


client = Client()
