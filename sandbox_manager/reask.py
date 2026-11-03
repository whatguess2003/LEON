"""Full record of one re-ask after each published recipe.

The file is the whole current record. Startup does not write it. A recorded
re-ask rewrites it from every stored re-ask and flushes. Re-asks are not
part of the 100 questions per day.
"""

import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_DIR = Path(__file__).resolve().parent
TEXT_PATH = _DIR / "reask.txt"

MANUAL = (
    {
        "seed_key": "qb0201-posted-room-revenue",
        "asked_at": "2026-11-02",
        "question": "posted room revenue for today's service date excluding taxes and fees",
        "latency_ms": 15726,
        "recipe_id": "qb0201_posted_room_revenue_ex_tax",
        "recipe_version": "2",
        "verdict": "match",
        "sentence": (
            "Leon said posted room revenue excluding taxes and fees is 0 MYR "
            "from 0 room ledger postings."
        ),
        "sql_figure": (
            "SQL: posted ROOM revenue for service date 2026-11-02 is 0.00. "
            "The only charges are food 31.04 and beverage 5.17, plus tax and a card payment."
        ),
    },
    {
        "seed_key": "qb0101-current-room-readiness",
        "asked_at": "2026-11-02",
        "question": "which rooms are currently dirty, clean, or inspected, split vacant and occupied",
        "latency_ms": 13430,
        "recipe_id": "qb0101_current_room_readiness",
        "recipe_version": "1",
        "verdict": "match",
        "sentence": (
            "Leon said vacant clean 102, 103, 202; occupied clean 101 and 201; "
            "none dirty; none inspected."
        ),
        "sql_figure": (
            "SQL agrees: all five rooms clean, 101 and 201 occupied, 102, 103, 202 vacant."
        ),
    },
)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _plain(value):
    from sandbox_manager.config import public_text

    return " ".join(public_text(value).replace("|", " ").split())


def _recipe(recipe_id, recipe_version):
    ids = [part.strip() for part in str(recipe_id or "").split(",") if part.strip()]
    versions = [part.strip() for part in str(recipe_version or "").split(",")]
    if not ids:
        return "-"
    labels = []
    for index, catalog_id in enumerate(ids):
        version = versions[index] if index < len(versions) else ""
        labels.append(catalog_id + {True: " version " + version, False: ""}[bool(version)])
    return ", ".join(labels)


def _line(row):
    return " | ".join([
        _plain(row.get("asked_at")),
        _plain(row.get("question")),
        str(_number(row.get("latency_ms"))) + " ms",
        _recipe(row.get("recipe_id"), row.get("recipe_version")),
        _plain(row.get("verdict")),
        _plain(row.get("sentence")),
        _plain(row.get("sql_figure")),
    ])


def render(rows):
    found = list(rows or [])
    match = sum(1 for row in found if row.get("verdict") == "match")
    miss = sum(1 for row in found if row.get("verdict") == "miss")
    lines = [
        "re-asks: " + str(len(found)),
        "match: " + str(match),
        "miss: " + str(miss),
    ]
    for row in found:
        lines.append(_line(row))
    return "\n".join(lines) + "\n"


def _rewrite(path, text):
    payload = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    with path.open("r+b") as handle:
        handle.seek(0)
        handle.write(payload)
        handle.flush()
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())


def write_rows(rows, path=None):
    """Rewrite the whole re-ask record and flush it."""
    target = Path(path) if path else TEXT_PATH
    with _LOCK:
        _rewrite(target, render(rows))


def write_reasks(store, path=None):
    """Rewrite the whole record from every stored re-ask and flush it."""
    with _LOCK:
        try:
            rows = store.reask_rows()
            target = Path(path) if path else TEXT_PATH
            _rewrite(target, render(rows))
        except Exception as exc:
            from sandbox_manager.config import public_text
            try:
                store.event("reask_write_failed", public_text(exc))
            except Exception:
                return
