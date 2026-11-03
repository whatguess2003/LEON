"""Local question pool and ask log. This sqlite file is not a main-server table."""

import sqlite3
import threading
from pathlib import Path

from sandbox_manager.config import ASK_INTERVAL_SECONDS, DAILY_CAP, TIMEZONE, local_day, local_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    norm TEXT NOT NULL UNIQUE,
    text TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asks (
    id INTEGER PRIMARY KEY,
    question_id INTEGER,
    local_day TEXT NOT NULL,
    asked_at TEXT NOT NULL,
    latency_ms INTEGER,
    verdict TEXT,
    recipe_id TEXT,
    recipe_version TEXT,
    auditor_triggered TEXT,
    published TEXT,
    published_catalog_id TEXT,
    published_version TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reasks (
    id INTEGER PRIMARY KEY,
    seed_key TEXT UNIQUE,
    asked_at TEXT NOT NULL,
    question TEXT NOT NULL,
    latency_ms INTEGER,
    recipe_id TEXT,
    recipe_version TEXT,
    verdict TEXT NOT NULL,
    sentence TEXT,
    sql_figure TEXT
);
"""


def _median(values):
    ordered = sorted(values)
    count = len(ordered)
    if not count:
        return None
    mid = count // 2
    if count % 2:
        return ordered[mid]
    return int(round((ordered[mid - 1] + ordered[mid]) / 2))


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(__file__).resolve().parent / "data" / "sandbox_manager.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._run(lambda conn: conn.executescript(_SCHEMA))
        self._seed_reasks()

    def _run(self, fn):
        with self._lock:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            try:
                result = fn(conn)
                conn.commit()
                return result
            finally:
                conn.close()

    def event(self, kind, note=""):
        at = local_now().isoformat()

        def run(conn):
            conn.execute(
                "INSERT INTO events (at, kind, note) VALUES (?, ?, ?)",
                (at, str(kind or ""), str(note or "")[:500]),
            )

        self._run(run)

    def set_meta(self, key, value):
        def run(conn):
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(key), str(value if value is not None else "")),
            )

        self._run(run)

    def meta(self, key):
        def run(conn):
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (str(key),)).fetchone()
            return str(row["value"]) if row else ""

        return self._run(run)

    def known_norms(self):
        def run(conn):
            return {str(row["norm"]) for row in conn.execute("SELECT norm FROM questions")}

        return self._run(run)

    def recent_texts(self, limit=150):
        def run(conn):
            rows = conn.execute(
                "SELECT text FROM questions ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [str(row["text"]) for row in rows]

        return self._run(run)

    def insert_question(self, text, source):
        clean = " ".join(str(text or "").split())
        norm = clean.casefold()
        if not clean or not norm:
            return 0
        created = local_now().isoformat()

        def run(conn):
            try:
                conn.execute(
                    "INSERT INTO questions (norm, text, source, status, created_at) VALUES (?, ?, ?, 'unused', ?)",
                    (norm, clean, str(source or "web"), created),
                )
            except sqlite3.IntegrityError:
                return 0
            return 1

        return self._run(run)

    def unused_count(self):
        def run(conn):
            row = conn.execute("SELECT COUNT(*) AS n FROM questions WHERE status = 'unused'").fetchone()
            return int(row["n"])

        return self._run(run)

    def next_unused(self):
        def run(conn):
            row = conn.execute(
                "SELECT id, text FROM questions WHERE status = 'unused' ORDER BY id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return {"id": int(row["id"]), "text": str(row["text"])}

        return self._run(run)

    def finish_ask(self, question_id, fields):
        asked_at = local_now().isoformat()
        day = local_day()

        def run(conn):
            conn.execute(
                """
                INSERT INTO asks (
                    question_id, local_day, asked_at, latency_ms, verdict, recipe_id, recipe_version,
                    auditor_triggered, published, published_catalog_id, published_version, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(question_id),
                    day,
                    asked_at,
                    fields.get("latency_ms"),
                    str(fields.get("verdict") or ""),
                    str(fields.get("recipe_id") or ""),
                    str(fields.get("recipe_version") or ""),
                    str(fields.get("auditor_triggered") or "no"),
                    str(fields.get("published") or "no"),
                    str(fields.get("published_catalog_id") or ""),
                    str(fields.get("published_version") or ""),
                    str(fields.get("note") or "")[:500],
                ),
            )
            conn.execute("UPDATE questions SET status = 'asked' WHERE id = ?", (int(question_id),))

        self._run(run)
        from sandbox_manager.report import write_report
        write_report(self)

    def _seed_reasks(self):
        """Store the verified manual re-asks once. Does not write reask.txt."""
        from sandbox_manager.reask import MANUAL

        def run(conn):
            for row in MANUAL:
                conn.execute(
                    """
                    INSERT INTO reasks (
                        seed_key, asked_at, question, latency_ms, recipe_id, recipe_version,
                        verdict, sentence, sql_figure
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(seed_key) DO NOTHING
                    """,
                    (
                        str(row.get("seed_key") or ""),
                        str(row.get("asked_at") or ""),
                        str(row.get("question") or ""),
                        row.get("latency_ms"),
                        str(row.get("recipe_id") or ""),
                        str(row.get("recipe_version") or ""),
                        str(row.get("verdict") or ""),
                        str(row.get("sentence") or ""),
                        str(row.get("sql_figure") or ""),
                    ),
                )

        self._run(run)

    def reask_rows(self):
        def run(conn):
            found = conn.execute(
                """
                SELECT asked_at, question, latency_ms, recipe_id, recipe_version,
                       verdict, sentence, sql_figure
                FROM reasks
                ORDER BY id
                """
            ).fetchall()
            return [dict(row) for row in found]

        return self._run(run)

    def record_reask(self, fields):
        """Store one re-ask outside the daily ask log, then flush the whole record."""
        from sandbox_manager.config import public_text
        from sandbox_manager.reask import write_reasks

        def plain(value):
            return " ".join(public_text(value).split())

        latency = fields.get("latency_ms")
        if isinstance(latency, bool) or not isinstance(latency, int):
            latency = None
        verdict = {"match": "match", "miss": "miss"}.get(plain(fields.get("verdict")), "miss")
        asked_at = local_now().isoformat()

        def run(conn):
            conn.execute(
                """
                INSERT INTO reasks (
                    seed_key, asked_at, question, latency_ms, recipe_id, recipe_version,
                    verdict, sentence, sql_figure
                ) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asked_at,
                    plain(fields.get("question")),
                    latency,
                    plain(fields.get("recipe_id")),
                    plain(fields.get("recipe_version")),
                    verdict,
                    plain(fields.get("sentence")),
                    plain(fields.get("sql_figure")),
                ),
            )

        self._run(run)
        write_reasks(self)

    def asked_today(self):
        day = local_day()

        def run(conn):
            row = conn.execute("SELECT COUNT(*) AS n FROM asks WHERE local_day = ?", (day,)).fetchone()
            return int(row["n"])

        return self._run(run)

    def last_asked_at(self):
        def run(conn):
            row = conn.execute("SELECT asked_at FROM asks ORDER BY id DESC LIMIT 1").fetchone()
            return str(row["asked_at"]) if row else ""

        return self._run(run)

    def seconds_until_next_ask(self):
        raw = self.last_asked_at()
        if not raw:
            return 0
        try:
            last = datetime_from(raw)
        except ValueError:
            return 0
        remaining = ASK_INTERVAL_SECONDS - (local_now() - last).total_seconds()
        if remaining > 0:
            return int(remaining)
        return 0

    def seconds_until_next_day(self):
        now = local_now()
        nxt = (now.replace(hour=0, minute=0, second=5, microsecond=0))
        from datetime import timedelta
        nxt = nxt + timedelta(days=1)
        return max(1, int((nxt - now).total_seconds()))

    def snapshot(self):
        day = local_day()

        def run(conn):
            rows = conn.execute(
                """
                SELECT a.asked_at, a.latency_ms, a.verdict, a.recipe_id, a.recipe_version,
                       a.auditor_triggered, a.published, a.published_catalog_id, a.published_version,
                       a.note, q.text AS question_text
                FROM asks a
                LEFT JOIN questions q ON q.id = a.question_id
                WHERE a.local_day = ?
                ORDER BY a.id
                """,
                (day,),
            ).fetchall()
            unused = conn.execute("SELECT COUNT(*) AS n FROM questions WHERE status = 'unused'").fetchone()
            meta = {str(row["key"]): str(row["value"]) for row in conn.execute("SELECT key, value FROM meta")}
            latest = conn.execute("SELECT kind, note, at FROM events ORDER BY id DESC LIMIT 1").fetchone()
            return rows, int(unused["n"]), meta, latest

        rows, unused, meta, latest = self._run(run)
        asks = []
        latencies = []
        for row in rows:
            latency = row["latency_ms"]
            if isinstance(latency, int):
                latencies.append(latency)
            asks.append({
                "asked_at": str(row["asked_at"]),
                "latency_ms": latency,
                "verdict": str(row["verdict"] or ""),
                "recipe_id": str(row["recipe_id"] or ""),
                "recipe_version": str(row["recipe_version"] or ""),
                "auditor_triggered": str(row["auditor_triggered"] or "no"),
                "published": str(row["published"] or "no"),
                "published_catalog_id": str(row["published_catalog_id"] or ""),
                "published_version": str(row["published_version"] or ""),
                "note": str(row["note"] or ""),
                "text": str(row["question_text"] or ""),
            })
        return {
            "today": day,
            "timezone": TIMEZONE,
            "asked_today": len(asks),
            "daily_cap": DAILY_CAP,
            "latency_last_ms": latencies[-1] if latencies else None,
            "latency_median_ms": _median(latencies),
            "mismatch_count": sum(1 for row in asks if row["verdict"] == "mismatch"),
            "unsaid_count": sum(1 for row in asks if row["verdict"] == "unsaid"),
            "auditor_trigger_count": sum(1 for row in asks if row["auditor_triggered"] == "yes"),
            "published_count": sum(1 for row in asks if row["published"] == "yes"),
            "unused_pool": unused,
            "desk": meta.get("desk") or "unknown",
            "phase": meta.get("phase") or "",
            "latest_event": {
                "kind": str(latest["kind"]) if latest else "",
                "note": str(latest["note"]) if latest else "",
                "at": str(latest["at"]) if latest else "",
            },
            "asks": asks,
        }


def datetime_from(value):
    from datetime import datetime
    return datetime.fromisoformat(str(value))
