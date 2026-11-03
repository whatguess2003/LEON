"""Rewrite the watchable sandbox report after each asked question and when totals change."""

import json
import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_DIR = Path(__file__).resolve().parent
TEXT_PATH = _DIR / "report.txt"
JSON_PATH = _DIR / "report.json"


def _number(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _yes(value):
    return "yes" if str(value or "") == "yes" else "no"


def _one_line(value):
    return " ".join(str(value or "").split())


def _recipe(recipe_id, recipe_version):
    ids = [part.strip() for part in str(recipe_id or "").split(",") if part.strip()]
    versions = [part.strip() for part in str(recipe_version or "").split(",")]
    if not ids:
        return "-"
    labels = []
    for index, catalog_id in enumerate(ids):
        version = versions[index] if index < len(versions) else ""
        labels.append(catalog_id + " v" + version if version else catalog_id)
    return ", ".join(labels)


def _question_line(ask):
    return " | ".join([
        str(ask.get("asked_at") or ""),
        str(_number(ask.get("latency_ms"))) + " ms",
        str(ask.get("verdict") or ""),
        _recipe(ask.get("recipe_id"), ask.get("recipe_version")),
        "auditor " + _yes(ask.get("auditor_triggered")),
        "published " + _yes(ask.get("published")),
        _one_line(ask.get("text")),
    ])


def _questions(asks):
    rows = []
    for ask in asks or []:
        rows.append({
            "time": str(ask.get("asked_at") or ""),
            "latency_ms": _number(ask.get("latency_ms")),
            "verdict": str(ask.get("verdict") or ""),
            "recipe_id": str(ask.get("recipe_id") or ""),
            "recipe_version": str(ask.get("recipe_version") or ""),
            "auditor_triggered": _yes(ask.get("auditor_triggered")),
            "published": _yes(ask.get("published")),
            "text": _one_line(ask.get("text")),
        })
    return rows


def _totals(snapshot):
    asks = snapshot.get("asks") or []
    cap = _number(snapshot.get("daily_cap"))
    return {
        "date": str(snapshot.get("today") or ""),
        "timezone": str(snapshot.get("timezone") or "Asia/Kuala_Lumpur"),
        "asked": _number(snapshot.get("asked_today")),
        "daily_cap": cap,
        "last_latency_ms": _number(snapshot.get("latency_last_ms")),
        "median_latency_ms": _number(snapshot.get("latency_median_ms")),
        "match": sum(1 for row in asks if row.get("verdict") == "match"),
        "mismatch": _number(snapshot.get("mismatch_count")),
        "unsaid": _number(snapshot.get("unsaid_count")),
        "auditor_triggered": _number(snapshot.get("auditor_trigger_count")),
        "recipes_published": _number(snapshot.get("published_count")),
    }


def render(snapshot):
    totals = _totals(snapshot)
    lines = [
        "date: " + totals["date"],
        "timezone: " + totals["timezone"],
        "asked: " + str(totals["asked"]) + " / " + str(totals["daily_cap"]),
        "last latency ms: " + str(totals["last_latency_ms"]),
        "median latency ms: " + str(totals["median_latency_ms"]),
        "match: " + str(totals["match"]),
        "mismatch: " + str(totals["mismatch"]),
        "unsaid: " + str(totals["unsaid"]),
        "auditor triggered: " + str(totals["auditor_triggered"]),
        "recipes published: " + str(totals["recipes_published"]),
    ]
    for ask in snapshot.get("asks") or []:
        lines.append(_question_line(ask))
    return "\n".join(lines) + "\n"


def _payload(snapshot, text):
    totals = _totals(snapshot)
    totals["text"] = text
    totals["asked_toward_100"] = str(totals["asked"]) + " / " + str(totals["daily_cap"])
    totals["questions"] = _questions(snapshot.get("asks") or [])
    return totals


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


def write_report(store):
    """Rewrite report.txt and report.json from the current local-day totals."""
    with _LOCK:
        try:
            snapshot = store.snapshot()
            text = render(snapshot)
            body = json.dumps(_payload(snapshot, text), ensure_ascii=False, indent=2) + "\n"
            _rewrite(TEXT_PATH, text)
            _rewrite(JSON_PATH, body)
        except Exception as exc:
            from sandbox_manager.config import public_text
            try:
                store.event("report_write_failed", public_text(exc))
            except Exception:
                return
