"""What the hotel can afford to learn. Haiku marks a gap recurring, one_off, or not_learnable; only a recurring gap
that comes back is queued, and only within the daily caps and token budget. Candidates cost no OpenAI tokens."""

import os

def _limits():
    return {
        "manager": int(os.getenv("LEON_CATALOG_MANAGER_DAILY", "5")),
        "hotel": int(os.getenv("LEON_CATALOG_HOTEL_DAILY", "15")),
        "tokens": int(os.getenv("LEON_CATALOG_DAILY_TOKENS", "300000")),
    }


def _local_day(hotel_name):
    from booking_system.timezone_utils import get_hotel_timezone_str, hotel_local_today

    return get_hotel_timezone_str(hotel_name), hotel_local_today(hotel_name)


def _note(hotel_name, fingerprint, manager, question):
    """Count this occurrence. Returns this manager's count and how many managers have asked this gap."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            INSERT INTO gap_candidates (hotel_name, gap_fingerprint, manager_name, occurrences, last_question)
            VALUES (%s, %s, %s, 1, %s)
            ON CONFLICT (hotel_name, gap_fingerprint, manager_name) DO UPDATE
            SET occurrences = gap_candidates.occurrences + 1, last_seen = NOW(), last_question = EXCLUDED.last_question
            RETURNING occurrences
            """,
            (hotel_name, fingerprint, manager, str(question or "")),
        )
        mine = int(cur.fetchone()[0])
        cur.execute(
            "SELECT COUNT(*) FROM gap_candidates WHERE hotel_name = %s AND gap_fingerprint = %s",
            (hotel_name, fingerprint),
        )
        return mine, int(cur.fetchone()[0])

    return _with(run)


def _mark(hotel_name, fingerprint, manager, status, reason):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            UPDATE gap_candidates SET status = %s, reason = %s
            WHERE hotel_name = %s AND gap_fingerprint = %s AND manager_name = %s
            """,
            (status, reason, hotel_name, fingerprint, manager),
        )

    _with(run)


def _spent(hotel_name, manager):
    """New investigations today for this manager and this hotel, and today's catalog and reviewer tokens from managers' gaps."""
    from booking_system.content_store import _with

    zone, today = _local_day(hotel_name)

    def run(cur):
        cur.execute(
            """
            SELECT COUNT(*) FILTER (WHERE manager_name = %s), COUNT(*) FILTER (WHERE manager_name <> '')
            FROM investigations
            WHERE hotel_name = %s AND (created_at AT TIME ZONE %s)::date = %s
            """,
            (manager, hotel_name, zone, today),
        )
        mine, hotel = cur.fetchone()
        cur.execute(
            """
            SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM agent_usage
            WHERE hotel_name = %s AND usage_date = %s AND origin = 'manager'
            """,
            (hotel_name, today),
        )
        return {"manager": int(mine), "hotel": int(hotel), "tokens": int(cur.fetchone()[0])}

    return _with(run)


def admit(hotel_name, manager, fingerprint, learn, question, free_only=False):
    """Returns the decision and the investigation fingerprint. A learned recipe is the asking manager's own,
    so each manager has their own investigation of a gap, and a REJECT cools down only that manager.
    attach: this manager's investigation already holds the gap, free and not counted. queue: open a new one.
    Anything else is the reason nothing is queued; the turn is still answered live."""
    from booking_system.semantic.registry import attachable_investigation

    hotel = str(hotel_name or "").upper()
    name = str(manager or "").upper()
    owned = fingerprint + "|" + name
    if learn != "recurring":
        return learn or "not_recurring", owned
    if attachable_investigation(owned):
        return "attach", owned
    mine, managers = _note(hotel, fingerprint, name, question)
    if free_only:
        _mark(hotel, fingerprint, name, "candidate", "existing_recipes_answered")
        return "candidate", owned
    if mine < 2 and managers < 2:
        return "candidate", owned
    limits = _limits()
    spent = _spent(hotel, name)
    over = [
        reason for reason, used, cap in (
            ("deferred_manager_cap", spent["manager"], limits["manager"]),
            ("deferred_hotel_cap", spent["hotel"], limits["hotel"]),
            ("deferred_token_budget", spent["tokens"], limits["tokens"]),
        )
        if used >= cap
    ]
    if over:
        _mark(hotel, fingerprint, name, "deferred", over[0])
        return over[0], owned
    _mark(hotel, fingerprint, name, "queued", "")
    return "queue", owned


def investigation_cap():
    return int(os.getenv("LEON_CATALOG_INVESTIGATION_TOKENS", "150000"))


def over_investigation_budget(trace_id):
    """True when the investigation for this trace has spent its token cap, or its manager's hotel has spent
    today's catalog and reviewer tokens. Checked before every catalog and reviewer call."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            "SELECT tokens_used, hotel_name, manager_name FROM investigations WHERE trace_id = %s ORDER BY investigation_id DESC LIMIT 1",
            (int(trace_id),),
        )
        return cur.fetchone()

    row = _with(run)
    if not row:
        return False
    if int(row[0]) >= investigation_cap():
        return True
    return bool(row[2]) and _spent(row[1], row[2])["tokens"] >= _limits()["tokens"]


def capability_key(name):
    return "capability|" + "_".join(str(name or "").lower().split())


def known_capabilities(hotel_name):
    """Recurring capabilities this hotel has asked for, newest first, so the spec reuses one name for the same gap."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT gap_fingerprint, MAX(last_question), MAX(last_seen) AS seen FROM gap_candidates
            WHERE hotel_name = %s AND gap_fingerprint LIKE 'capability|%%'
            GROUP BY gap_fingerprint ORDER BY seen DESC LIMIT 50
            """,
            (str(hotel_name or "").upper(),),
        )
        return [{"name": row[0].split("|", 1)[1], "example": row[1]} for row in cur.fetchall()]

    return _with(run)


def record_usage(trace, agent, input_tokens, output_tokens):
    """Catalog and reviewer tokens per hotel-local day. A trace with no manager is a system trace and does not spend the managers' budget."""
    from booking_system.content_store import _with
    from booking_system.semantic.definitions import trace_manager

    hotel = str((trace or {}).get("hotel_name") or "").upper()
    if not hotel:
        return
    _zone, today = _local_day(hotel)
    origin = "manager" if trace_manager(trace) else "system"

    def run(cur):
        cur.execute(
            """
            INSERT INTO agent_usage (hotel_name, usage_date, origin, agent, calls, input_tokens, output_tokens)
            VALUES (%s, %s, %s, %s, 1, %s, %s)
            ON CONFLICT (hotel_name, usage_date, origin, agent) DO UPDATE
            SET calls = agent_usage.calls + 1,
                input_tokens = agent_usage.input_tokens + EXCLUDED.input_tokens,
                output_tokens = agent_usage.output_tokens + EXCLUDED.output_tokens
            """,
            (hotel, today, origin, agent, int(input_tokens or 0), int(output_tokens or 0)),
        )
        cur.execute(
            "UPDATE investigations SET tokens_used = tokens_used + %s WHERE trace_id = %s",
            (int(input_tokens or 0) + int(output_tokens or 0), int(trace["trace_id"])),
        )

    _with(run)
