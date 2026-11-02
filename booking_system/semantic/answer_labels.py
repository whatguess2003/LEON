"""Developer labels on flagged or sampled desk answers, and the learning health report built from them.
The label taxonomy, its causes, and the expected share of each label are rows; developers extend them through the admin API.
The flaw threshold is a row of its own. flawed means the share is above that threshold; still_fixing means it is above the expected share."""

import psycopg2
from psycopg2.extras import Json, execute_values

_KINDS = (
    ("learned", "Answered correctly by a recipe the system learned.", True, False, False, 1),
    ("learned_but_slow", "Answered correctly, but only after a slow learning path.", True, False, False, 0),
    ("published_but_wrong_or_unused", "A published recipe answered wrongly, or existed and was not used.", False, True, True, 0),
    ("still_learning", "No recipe yet; the question is in the learning path.", False, False, False, -1),
    ("not_learnable", "The question cannot be answered from the data the hotel keeps.", False, False, False, 0),
)
_CAUSES = (
    ("retrieval_miss", "The published recipe was not retrieved for the question."),
    ("spec_chose_other_recipe", "The problem spec chose a different recipe."),
    ("recipe_wrong", "The recipe body or its SQL is wrong."),
    ("reviewer_approved_wrong_recipe", "The reviewer approved a recipe that does not answer the question."),
    ("definition_pin_wrong", "The recipe read the wrong definition version."),
    ("reply_misstated", "The rows were right; the desk reply misstated them."),
    ("other", "Explained in the note."),
)
_TARGETS = (
    ("published_but_wrong_or_unused", "0.10"),
    ("learned", "0.25"),
    ("learned_but_slow", "0.25"),
    ("not_learnable", "0.10"),
    ("still_learning", "0.30"),
)
# Later expected-share versions. The initial row stays. A version that starts today moves that day's earlier row to the day before.
_TARGET_VERSIONS = (
    ("published_but_wrong_or_unused", "0.05", "fix target: system fixes continue until this share"),
)
_FLAW_THRESHOLDS = (
    ("published_but_wrong_or_unused", "0.10"),
)


def labels_ddl(cur):
    """Taxonomy, causes, expected shares, flaw thresholds, and labels. Seeds only rows that are missing, so a restart changes nothing."""
    from booking_system.hotel_registry import new_id

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_label_kinds (
            label VARCHAR(64) PRIMARY KEY CHECK (label = lower(btrim(label)) AND label <> ''),
            description TEXT NOT NULL DEFAULT '',
            answer_correct BOOLEAN NOT NULL,
            system_flaw BOOLEAN NOT NULL DEFAULT FALSE,
            needs_cause BOOLEAN NOT NULL DEFAULT FALSE,
            desired_trend SMALLINT NOT NULL DEFAULT 0 CHECK (desired_trend IN (-1, 0, 1)),
            created_by VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_label_causes (
            label VARCHAR(64) NOT NULL REFERENCES answer_label_kinds (label),
            cause VARCHAR(64) NOT NULL CHECK (cause = lower(btrim(cause)) AND cause <> ''),
            description TEXT NOT NULL DEFAULT '',
            created_by VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (label, cause)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_label_targets (
            target_id VARCHAR(48) PRIMARY KEY CHECK (left(target_id, 4) = 'tgt_'),
            label VARCHAR(64) NOT NULL REFERENCES answer_label_kinds (label),
            expected_share NUMERIC(5, 4) NOT NULL CHECK (expected_share >= 0 AND expected_share <= 1),
            effective_from DATE NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            set_by VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (label, effective_from)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_label_flaw_thresholds (
            label VARCHAR(64) PRIMARY KEY REFERENCES answer_label_kinds (label),
            flaw_threshold NUMERIC(5, 4) NOT NULL CHECK (flaw_threshold >= 0 AND flaw_threshold <= 1),
            note TEXT NOT NULL DEFAULT '',
            set_by VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_labels (
            label_id VARCHAR(48) PRIMARY KEY CHECK (left(label_id, 4) = 'lbl_'),
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            complaint_id VARCHAR(48) REFERENCES answer_complaints (complaint_id) ON DELETE CASCADE,
            run_id BIGINT,
            task_id BIGINT,
            label VARCHAR(64) NOT NULL REFERENCES answer_label_kinds (label),
            cause VARCHAR(64),
            note TEXT NOT NULL DEFAULT '',
            labeler VARCHAR NOT NULL,
            answered_at TIMESTAMPTZ NOT NULL,
            links JSONB NOT NULL DEFAULT '[]'::jsonb,
            published BOOLEAN NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK (num_nonnulls(complaint_id, run_id, task_id) = 1),
            FOREIGN KEY (label, cause) REFERENCES answer_label_causes (label, cause)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS answer_labels_complaint_idx ON answer_labels (complaint_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS answer_labels_run_idx ON answer_labels (run_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS answer_labels_task_idx ON answer_labels (task_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS answer_labels_answered_idx ON answer_labels (answered_at)")
    execute_values(
        cur,
        """INSERT INTO answer_label_kinds (label, description, answer_correct, system_flaw, needs_cause, desired_trend, created_by)
           VALUES %s ON CONFLICT (label) DO NOTHING""",
        [kind + ("seed",) for kind in _KINDS],
    )
    flaw = [kind[0] for kind in _KINDS if kind[3]]
    execute_values(
        cur,
        "INSERT INTO answer_label_causes (label, cause, description, created_by) VALUES %s ON CONFLICT (label, cause) DO NOTHING",
        [(label, cause, text, "seed") for label in flaw for cause, text in _CAUSES],
    )
    for label, share in _TARGETS:
        cur.execute(
            """
            INSERT INTO answer_label_targets (target_id, label, expected_share, effective_from, note, set_by)
            SELECT %s, %s, %s, CURRENT_DATE, 'initial baseline: general recipes and onboarding only', 'seed'
            WHERE NOT EXISTS (SELECT 1 FROM answer_label_targets WHERE label = %s)
            """,
            (new_id("tgt"), label, share, label),
        )
    for label, share, note in _TARGET_VERSIONS:
        cur.execute(
            """
            UPDATE answer_label_targets
            SET effective_from = CURRENT_DATE - 1
            WHERE label = %s AND effective_from = CURRENT_DATE AND expected_share <> %s::numeric
            """,
            (label, share),
        )
        cur.execute(
            """
            INSERT INTO answer_label_targets (target_id, label, expected_share, effective_from, note, set_by)
            SELECT %s, %s, %s, CURRENT_DATE, %s, 'seed'
            WHERE NOT EXISTS (SELECT 1 FROM answer_label_targets WHERE label = %s AND expected_share = %s::numeric)
            """,
            (new_id("tgt"), label, share, note, label, share),
        )
    for label, threshold in _FLAW_THRESHOLDS:
        cur.execute(
            """
            INSERT INTO answer_label_flaw_thresholds (label, flaw_threshold, note, set_by)
            SELECT %s, %s, 'flaw line: a share above this means the system is flawed', 'seed'
            WHERE NOT EXISTS (SELECT 1 FROM answer_label_flaw_thresholds WHERE label = %s)
            """,
            (label, threshold, label),
        )


def _rows(cur):
    names = [item[0] for item in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _refused(error):
    return {"error": error.diag.message_primary or str(error)}


def taxonomy():
    """Labels with their causes and every version of their expected share."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT k.*, f.flaw_threshold,
                   COALESCE((SELECT jsonb_agg(to_jsonb(c) - 'label' ORDER BY c.cause) FROM answer_label_causes c WHERE c.label = k.label), '[]') AS causes,
                   COALESCE((SELECT jsonb_agg(to_jsonb(t) - 'label' ORDER BY t.effective_from DESC, t.created_at DESC)
                             FROM answer_label_targets t WHERE t.label = k.label), '[]') AS targets
            FROM answer_label_kinds k
            LEFT JOIN answer_label_flaw_thresholds f ON f.label = k.label
            ORDER BY k.created_at, k.label
            """
        )
        return _rows(cur)

    return _with(run)


def add_kind(label, description, answer_correct, system_flaw, needs_cause, desired_trend, admin):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute("SELECT 1 FROM answer_label_kinds WHERE label = %s", (label,))
        if cur.fetchone():
            return {"error": "label " + label + " exists"}
        cur.execute(
            """INSERT INTO answer_label_kinds (label, description, answer_correct, system_flaw, needs_cause, desired_trend, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (label, description, answer_correct, system_flaw, needs_cause, desired_trend, admin),
        )
        return _rows(cur)[0]

    try:
        return _with(run)
    except psycopg2.IntegrityError as error:
        return _refused(error)


def add_cause(label, cause, description, admin):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute("SELECT 1 FROM answer_label_kinds WHERE label = %s", (label,))
        if not cur.fetchone():
            return {"error": "no label " + label}
        cur.execute("SELECT 1 FROM answer_label_causes WHERE label = %s AND cause = %s", (label, cause))
        if cur.fetchone():
            return {"error": "cause " + cause + " exists for " + label}
        cur.execute(
            "INSERT INTO answer_label_causes (label, cause, description, created_by) VALUES (%s, %s, %s, %s) RETURNING *",
            (label, cause, description, admin),
        )
        return _rows(cur)[0]

    try:
        return _with(run)
    except psycopg2.IntegrityError as error:
        return _refused(error)


def set_target(label, expected_share, effective_from, note, admin):
    """A new version of a label's expected share. Earlier versions stay, so past periods keep the target they had."""
    from booking_system.content_store import _with
    from booking_system.hotel_registry import new_id

    def run(cur):
        cur.execute("SELECT 1 FROM answer_label_kinds WHERE label = %s", (label,))
        if not cur.fetchone():
            return {"error": "no label " + label}
        cur.execute("SELECT target_id FROM answer_label_targets WHERE label = %s AND effective_from = %s", (label, effective_from))
        if cur.fetchone():
            return {"error": "a target for " + label + " already starts on " + str(effective_from)}
        cur.execute(
            """INSERT INTO answer_label_targets (target_id, label, expected_share, effective_from, note, set_by)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (new_id("tgt"), label, expected_share, effective_from, note, admin),
        )
        return _rows(cur)[0]

    try:
        return _with(run)
    except (psycopg2.IntegrityError, psycopg2.DataError) as error:
        return _refused(error)


_DECISION = """
    LEFT JOIN catalogs c ON c.catalog_id = v.catalog_id
    LEFT JOIN LATERAL (
        SELECT p.decision_id, p.status, p.proposal_id FROM publication_decisions p
        WHERE p.catalog_id = v.catalog_id
          AND (p.proposal_id = (v.provenance->>'proposal_id')::bigint OR p.decision_id = (v.provenance->>'decision_id')::bigint)
        ORDER BY p.decision_id DESC LIMIT 1
    ) d ON TRUE
    LEFT JOIN LATERAL (
        SELECT decision FROM recipe_reviews WHERE proposal_id = d.proposal_id ORDER BY review_id DESC LIMIT 1
    ) rv ON TRUE
"""

_LINK = """
    jsonb_build_object('role', %s, 'run_id', {run}, 'catalog_id', {catalog}, 'version', {version}, 'scope', c.scope,
                       'decision_id', d.decision_id, 'decision_status', d.status, 'proposal_id', d.proposal_id,
                       'reviewer_decision', rv.decision, 'row_count', {rows}, 'elapsed_ms', {elapsed})
"""

_ANSWERED = (
    "SELECT " + _LINK.format(run="r.run_id", catalog="r.catalog_id", version="r.version", rows="r.row_count", elapsed="r.elapsed_ms")
    + " FROM execution_runs r LEFT JOIN catalog_versions v ON v.catalog_id = r.catalog_id AND v.version = r.version"
    + _DECISION + " WHERE r.run_id = ANY(%s) ORDER BY r.run_id"
)

_EXPECTED = (
    "SELECT " + _LINK.format(run="NULL", catalog="v.catalog_id", version="v.version", rows="NULL", elapsed="NULL")
    + " FROM catalog_active_versions a JOIN catalog_versions v ON v.catalog_id = a.catalog_id AND v.version = a.version"
    + _DECISION + " WHERE a.scope = 'global' AND a.catalog_id = %s"
)

# What a label can point at: the label column it fills, and the hotel, answer time, and recipe runs of that answer.
_SUBJECTS = {
    "complaint": ("complaint_id", """
        SELECT c.hotel_id, c.created_at, array_remove(array_agg(k.run_id ORDER BY k.run_id), NULL)
        FROM answer_complaints c LEFT JOIN answer_complaint_runs k ON k.complaint_id = c.complaint_id
        WHERE c.complaint_id = %s GROUP BY c.hotel_id, c.created_at
    """),
    "run": ("run_id", "SELECT hotel_id, created_at, ARRAY[run_id] FROM execution_runs WHERE run_id = %s"),
    "task": ("task_id", """
        SELECT t.hotel_id, t.created_at, array_remove(array_agg(r.run_id ORDER BY r.run_id), NULL)
        FROM tasks t LEFT JOIN execution_runs r ON r.task_id = t.task_id
        WHERE t.task_id = %s GROUP BY t.hotel_id, t.created_at
    """),
}
SUBJECTS = tuple(_SUBJECTS)


def label_answer(subject, key, label, cause, note, catalog_id, labeler):
    """Label one complaint, run, or task. The answering runs are frozen with their recipe version, scope, publication decision,
    and reviewer decision; catalog_id names a published recipe that should have answered when none did. Latest label wins."""
    from booking_system.content_store import _transaction
    from booking_system.hotel_registry import new_id

    column, read = _SUBJECTS[subject]

    def run(cur):
        cur.execute(read, (key,))
        found = cur.fetchone()
        if not found:
            return {"error": "no " + subject + " " + str(key)}
        cur.execute("SELECT needs_cause, system_flaw FROM answer_label_kinds WHERE label = %s", (label,))
        kind = cur.fetchone()
        if not kind:
            return {"error": "no label " + label}
        if kind[0] and not cause:
            return {"error": "label " + label + " needs a cause"}
        if cause:
            cur.execute("SELECT 1 FROM answer_label_causes WHERE label = %s AND cause = %s", (label, cause))
            if not cur.fetchone():
                return {"error": "cause " + cause + " is not defined for " + label}
        cur.execute(_ANSWERED, ("answered", list(found[2])))
        links = [row[0] for row in cur.fetchall()]
        if catalog_id:
            cur.execute(_EXPECTED, ("expected", catalog_id))
            expected = [row[0] for row in cur.fetchall()]
            if not expected:
                return {"error": "no active recipe " + catalog_id}
            links.extend(expected)
        published = any(link.get("decision_id") for link in links)
        if kind[1] and not published:
            return {"error": "label " + label + " needs a published recipe: none answered, so pass catalog_id of the one that should have"}
        cur.execute(
            "INSERT INTO answer_labels (label_id, hotel_id, " + column + """, label, cause, note, labeler, answered_at, links, published)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (new_id("lbl"), found[0], key, label, cause or None, str(note or ""), labeler, found[1], Json(links), published),
        )
        return _rows(cur)[0]

    try:
        return _transaction(run)
    except psycopg2.IntegrityError as error:
        return _refused(error)


def label_history(subject, key):
    from booking_system.content_store import _with

    column = _SUBJECTS[subject][0]

    def run(cur):
        cur.execute("SELECT * FROM answer_labels WHERE " + column + " = %s ORDER BY created_at DESC", (key,))
        return _rows(cur)

    return _with(run)


_LATEST = """
    latest AS (
        SELECT * FROM (
            SELECT DISTINCT ON (l.complaint_id, l.run_id, l.task_id) l.*, k.answer_correct, k.system_flaw
            FROM answer_labels l JOIN answer_label_kinds k ON k.label = l.label
            ORDER BY l.complaint_id, l.run_id, l.task_id, l.created_at DESC
        ) newest
        WHERE (%(since)s::timestamptz IS NULL OR newest.answered_at >= %(since)s::timestamptz)
          AND (%(until)s::timestamptz IS NULL OR newest.answered_at < %(until)s::timestamptz)
    )
"""

_LINKS = """CROSS JOIN LATERAL jsonb_to_recordset(l.links)
    AS e(role text, catalog_id text, version int, scope text, decision_id bigint, decision_status text, reviewer_decision text)"""

# name, grouping columns, how many leading columns form the group a share is taken within, whether the answer's recipe links are read.
_BREAKDOWNS = (
    ("by_label", (("label", "l.label"),), 0, False),
    ("by_cause", (("label", "l.label"), ("cause", "l.cause")), 1, False),
    ("by_period", (("period", "date_trunc(%(period)s, l.answered_at)"), ("label", "l.label")), 1, False),
    ("by_recipe", (("catalog_id", "e.catalog_id"), ("version", "e.version"), ("role", "e.role"), ("label", "l.label"), ("cause", "l.cause")), 3, True),
    ("by_reviewer_decision", (("decision_status", "e.decision_status"), ("reviewer_decision", "e.reviewer_decision"), ("label", "l.label")), 2, True),
    ("by_scope", (("scope", "e.scope"), ("label", "l.label")), 1, True),
)


def _breakdown(cur, params, columns, within, linked):
    keys = ", ".join(expression + " AS " + name for name, expression in columns)
    group = ", ".join(str(index + 1) for index in range(len(columns)))
    partition = ", ".join(expression for name, expression in columns[:within])
    cur.execute(
        "WITH " + _LATEST + "SELECT " + keys + """, count(DISTINCT l.label_id) AS answers,
               round(count(DISTINCT l.label_id)::numeric / sum(count(DISTINCT l.label_id)) OVER (PARTITION BY """
        + (partition or "NULL") + "), 4) AS share FROM latest l " + (_LINKS if linked else "")
        + " GROUP BY " + group + " ORDER BY " + group,
        params,
    )
    return _rows(cur)


_AGAINST_TARGET = """
    WITH """ + _LATEST + """,
    counted AS (SELECT date_trunc(%(period)s, answered_at) AS period, label, count(*) AS answers FROM latest GROUP BY 1, 2),
    periods AS (SELECT period, sum(answers) AS labeled FROM counted GROUP BY period)
    SELECT p.period, k.label, k.system_flaw, k.desired_trend, COALESCE(c.answers, 0) AS answers, p.labeled,
           round(COALESCE(c.answers, 0)::numeric / p.labeled, 4) AS actual_share, t.expected_share, t.effective_from, f.flaw_threshold,
           round(COALESCE(c.answers, 0)::numeric / p.labeled - t.expected_share, 4) AS gap
    FROM periods p CROSS JOIN answer_label_kinds k
    LEFT JOIN counted c ON c.period = p.period AND c.label = k.label
    LEFT JOIN answer_label_flaw_thresholds f ON f.label = k.label
    LEFT JOIN LATERAL (
        SELECT expected_share, effective_from FROM answer_label_targets
        WHERE label = k.label AND effective_from < p.period + ('1 ' || %(period)s)::interval
        ORDER BY effective_from DESC LIMIT 1
    ) t ON TRUE
    ORDER BY p.period, k.created_at, k.label
"""

_WINDOW = """
    WITH """ + _LATEST + """
    SELECT k.label, k.system_flaw, count(l.label_id) AS answers, n.labeled,
           round(count(l.label_id)::numeric / NULLIF(n.labeled, 0), 4) AS actual_share, t.expected_share, t.effective_from, f.flaw_threshold
    FROM answer_label_kinds k
    LEFT JOIN latest l ON l.label = k.label
    CROSS JOIN (SELECT count(*) AS labeled FROM latest) n
    LEFT JOIN answer_label_flaw_thresholds f ON f.label = k.label
    LEFT JOIN LATERAL (
        SELECT expected_share, effective_from FROM answer_label_targets
        WHERE label = k.label AND effective_from <= COALESCE(%(until)s::date, CURRENT_DATE)
        ORDER BY effective_from DESC LIMIT 1
    ) t ON TRUE
    GROUP BY k.label, k.system_flaw, k.created_at, n.labeled, t.expected_share, t.effective_from, f.flaw_threshold
    ORDER BY k.created_at, k.label
"""

_PUBLISHED = """
    WITH """ + _LATEST + """
    SELECT count(*) FILTER (WHERE published) AS published_answers,
           count(*) FILTER (WHERE published AND system_flaw) AS flawed_answers,
           round((count(*) FILTER (WHERE published AND system_flaw))::numeric / NULLIF(count(*) FILTER (WHERE published), 0), 4) AS flaw_rate
    FROM latest
"""

_TIMELINE = """
    WITH """ + _LATEST + """,
    produced AS (
        SELECT DISTINCT p.decision_id, p.catalog_id, p.proposal_id, p.created_at
        FROM publication_decisions p JOIN catalog_versions v ON v.catalog_id = p.catalog_id
         AND (p.proposal_id = (v.provenance->>'proposal_id')::bigint OR p.decision_id = (v.provenance->>'decision_id')::bigint)
        WHERE (%(since)s::timestamptz IS NULL OR p.created_at >= %(since)s::timestamptz)
          AND (%(until)s::timestamptz IS NULL OR p.created_at < %(until)s::timestamptz)
    ),
    marks AS (
        SELECT d.decision_id, d.catalog_id, d.created_at AS published_at,
               (SELECT LEAST(MIN(t.created_at), MIN(g.first_seen))
                FROM catalog_proposals cp JOIN investigation_traces t ON t.trace_id = cp.trace_id
                LEFT JOIN investigations i ON i.trace_id = t.trace_id
                LEFT JOIN gap_candidates g ON g.hotel_id = i.hotel_id AND g.gap_fingerprint = i.gap_fingerprint
                WHERE cp.proposal_id = d.proposal_id) AS first_question_at,
               (SELECT MIN(l.answered_at) FROM latest l CROSS JOIN LATERAL jsonb_to_recordset(l.links) AS e(role text, decision_id bigint)
                WHERE l.answer_correct AND e.role = 'answered' AND e.decision_id = d.decision_id AND l.answered_at >= d.created_at)
               AS first_correct_at
        FROM produced d
    )
    SELECT *, round((EXTRACT(EPOCH FROM published_at - first_question_at) / 3600)::numeric, 2) AS hours_question_to_publication,
              round((EXTRACT(EPOCH FROM first_correct_at - published_at) / 3600)::numeric, 2) AS hours_publication_to_first_correct
    FROM marks ORDER BY published_at
"""


def _trend(rows):
    """Per label, its share in each period, first-to-last change, and whether that change goes the way the label row wants."""
    series = {}
    for row in rows:
        series.setdefault(row["label"], {"label": row["label"], "desired_trend": row["desired_trend"], "system_flaw": row["system_flaw"],
                                         "shares": [], "within_target": []})
        series[row["label"]]["shares"].append({"period": row["period"], "share": row["actual_share"], "expected": row["expected_share"]})
        series[row["label"]]["within_target"].append(row["over_target"] is not True)
    for item in series.values():
        shares = [entry["share"] for entry in item["shares"]]
        item["change"] = shares[-1] - shares[0] if len(shares) > 1 else None
        item["on_track"] = item["change"] * item["desired_trend"] > 0 if item["change"] is not None and item["desired_trend"] else None
        item["within_target"] = all(item.pop("within_target"))
    return list(series.values())


def assess_share(actual_share, expected_share, flaw_threshold):
    """Flawed when the share is above the stored flaw threshold. Still fixing when it is above the expected share."""
    over_target = actual_share is not None and expected_share is not None and actual_share > expected_share
    above_flaw_threshold = actual_share is not None and flaw_threshold is not None and actual_share > flaw_threshold
    return {
        "over_target": over_target,
        "above_flaw_threshold": above_flaw_threshold,
        "flawed": above_flaw_threshold,
        "still_fixing": over_target,
        "within_target": actual_share is not None and expected_share is not None and not over_target,
    }


def _ratio(answers, labeled):
    from decimal import Decimal
    return Decimal(answers) / Decimal(labeled) if labeled else None


def _mark(rows):
    for row in rows:
        status = assess_share(_ratio(row["answers"], row["labeled"]), row["expected_share"], row["flaw_threshold"])
        row["over_target"] = status["over_target"]
        row["above_flaw_threshold"] = status["above_flaw_threshold"]


def health_report(period, since, until):
    """Label counts and shares by period, cause, recipe version, reviewer decision, and scope; actual share against the expected
    share in effect per period; the trend over periods; and question -> publication -> first correct answer times.
    system_health.flawed is a flaw label above its stored flaw threshold. system_health.still_fixing is that share above the expected share."""
    from booking_system.content_store import _with

    params = {"period": period, "since": since, "until": until}

    def run(cur):
        report = {"period": period, "since": since, "until": until}
        for name, columns, within, linked in _BREAKDOWNS:
            report[name] = _breakdown(cur, params, columns, within, linked)
        cur.execute(_AGAINST_TARGET, params)
        report["against_target"] = _rows(cur)
        cur.execute(_WINDOW, params)
        report["window"] = _rows(cur)
        cur.execute(_PUBLISHED, params)
        report["published_answers"] = _rows(cur)[0]
        cur.execute(_TIMELINE, params)
        report["timeline"] = _rows(cur)
        cur.execute(
            """SELECT count(*) FROM answer_complaints c
               WHERE NOT EXISTS (SELECT 1 FROM answer_labels l WHERE l.complaint_id = c.complaint_id)"""
        )
        report["unlabeled_complaints"] = cur.fetchone()[0]
        return report

    try:
        report = _with(run)
    except psycopg2.DataError as error:
        return _refused(error)
    _mark(report["against_target"])
    _mark(report["window"])
    report["trend"] = _trend(report["against_target"])
    flawed_periods = [row["period"] for row in report["against_target"] if row["system_flaw"] and row["above_flaw_threshold"]]
    window_flaws = [row for row in report["window"] if row["system_flaw"]]
    measured = [row["hours_question_to_publication"] for row in report["timeline"] if row["hours_question_to_publication"] is not None]
    corrected = [row["hours_publication_to_first_correct"] for row in report["timeline"] if row["hours_publication_to_first_correct"] is not None]
    report["timeline_summary"] = {
        "decisions": len(report["timeline"]),
        "avg_hours_question_to_publication": round(sum(measured) / len(measured), 2) if measured else None,
        "avg_hours_publication_to_first_correct": round(sum(corrected) / len(corrected), 2) if corrected else None,
        "with_first_correct": len(corrected),
    }
    report["system_health"] = {
        "flawed": any(row["above_flaw_threshold"] for row in window_flaws),
        "still_fixing": any(row["over_target"] for row in window_flaws),
        "flaw_labels": [{key: row[key] for key in ("label", "answers", "labeled", "actual_share", "expected_share", "flaw_threshold", "effective_from", "over_target", "above_flaw_threshold")}
                        for row in window_flaws],
        "untargeted_flaw_labels": [row["label"] for row in window_flaws if row["expected_share"] is None],
        "flawed_periods": flawed_periods,
        "flaw_rate_among_published_answers": report["published_answers"]["flaw_rate"],
    }
    return report


def study_complaints(status):
    """Each complaint with what a developer needs to judge it: question, desk reply, every answering run with its recipe body,
    parameters, rows, latency, definition versions, publication decision and reviewer output, the SQL, and its label history."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT c.complaint_id, c.hotel_id, h.hotel_name, c.user_id, c.manager_name, c.message, c.question, c.desk_reply, c.task_id,
                   c.sql_executed, c.definition_versions, c.status, c.resolution_note, c.resolver, c.created_at, c.resolved_at,
                   COALESCE(runs.items, '[]') AS runs, runs.rows_returned, runs.latency_ms,
                   COALESCE(labels.items, '[]') AS labels, labels.items->0 AS latest_label
            FROM answer_complaints c
            LEFT JOIN hotel h ON h.hotel_id = c.hotel_id
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(jsonb_build_object(
                           'run_id', k.run_id, 'catalog_id', k.catalog_id, 'version', k.version, 'scope', cat.scope, 'recipe', v.body,
                           'provenance', v.provenance, 'parameters', r.parameters, 'row_count', r.row_count, 'error', r.error,
                           'elapsed_ms', r.elapsed_ms, 'definition_versions', r.definition_versions, 'ran_at', r.created_at,
                           'decision_id', p.decision_id, 'proposal_id', p.proposal_id, 'decision_status', p.status,
                           'decision_notes', p.notes, 'approval_ref', p.approval_ref,
                           'reviewer', to_jsonb(rv)) ORDER BY k.run_id) AS items,
                       sum(r.row_count) AS rows_returned, sum(r.elapsed_ms) AS latency_ms
                FROM answer_complaint_runs k
                LEFT JOIN execution_runs r ON r.run_id = k.run_id
                LEFT JOIN catalog_versions v ON v.catalog_id = k.catalog_id AND v.version = k.version
                LEFT JOIN catalogs cat ON cat.catalog_id = k.catalog_id
                LEFT JOIN publication_decisions p ON p.decision_id = k.decision_id
                LEFT JOIN LATERAL (
                    SELECT decision, confidence, findings, unresolved_questions, model, created_at FROM recipe_reviews
                    WHERE proposal_id = p.proposal_id ORDER BY review_id DESC LIMIT 1
                ) rv ON TRUE
                WHERE k.complaint_id = c.complaint_id
            ) runs ON TRUE
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(to_jsonb(l) ORDER BY l.created_at DESC) AS items FROM answer_labels l WHERE l.complaint_id = c.complaint_id
            ) labels ON TRUE
            WHERE c.status = %s ORDER BY c.created_at
            """,
            (status,),
        )
        return _rows(cur)

    return _with(run)
