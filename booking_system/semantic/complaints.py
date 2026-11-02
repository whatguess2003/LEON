"""A manager's complaint about one desk answer, kept with the runs, SQL, and publication decision behind it.
Reviewer approvals keep publishing on their own; the platform team reads these and fixes a recipe by hand."""

from psycopg2.extras import Json

STATUSES = ("open", "resolved", "dismissed")


def complaints_ddl(cur):
    """answer_complaints and one answer_complaint_runs row per recipe run that answered, then the developer label tables."""
    from booking_system.content_store import DDL_LOCK_TIMEOUT, add_column

    cur.execute("SET lock_timeout = '" + DDL_LOCK_TIMEOUT + "'")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_complaints (
            complaint_id VARCHAR(48) PRIMARY KEY CHECK (left(complaint_id, 4) = 'cmp_'),
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            manager_name VARCHAR NOT NULL,
            message TEXT NOT NULL,
            question TEXT NOT NULL DEFAULT '',
            desk_reply TEXT NOT NULL DEFAULT '',
            task_id BIGINT,
            sql_executed JSONB NOT NULL DEFAULT '[]'::jsonb,
            status VARCHAR(16) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'dismissed')),
            resolution_note TEXT NOT NULL DEFAULT '',
            resolver VARCHAR NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ
        )
        """
    )
    add_column(cur, "answer_complaints", "user_id", "VARCHAR(48) REFERENCES user_account (user_id)")
    add_column(cur, "answer_complaints", "definition_versions", "JSONB NOT NULL DEFAULT '[]'::jsonb")
    cur.execute("CREATE INDEX IF NOT EXISTS answer_complaints_status_idx ON answer_complaints (status, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS answer_complaints_hotel_id_idx ON answer_complaints (hotel_id)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_complaint_runs (
            complaint_id VARCHAR(48) NOT NULL REFERENCES answer_complaints (complaint_id) ON DELETE CASCADE,
            run_id BIGINT NOT NULL,
            catalog_id VARCHAR NOT NULL,
            version INTEGER,
            decision_id BIGINT,
            PRIMARY KEY (complaint_id, run_id)
        )
        """
    )
    from booking_system.semantic.answer_labels import labels_ddl
    labels_ddl(cur)


def ensure_complaint_tables():
    from booking_system.content_store import _with
    return _with(complaints_ddl)


def remember_answer(hotel_name, manager, question, reply, task_id, definition_versions=(), evidence=None):
    """The session keeps the last answered turn, so a complaint can find what answered it and which definition versions it read."""
    from booking_system.runtime_state import read_session, write_session

    state = read_session(hotel_name, manager)
    last = {
        "task_id": int(task_id) if task_id else None,
        "question": str(question or ""),
        "reply": str(reply or ""),
        "definition_versions": list(definition_versions or []),
    }
    for key, value in dict(evidence or {}).items():
        last[str(key)] = value
    state["last_answer"] = last
    write_session(hotel_name, manager, state)


_RUNS = """
    SELECT r.run_id, r.catalog_id, r.version, d.decision_id
    FROM execution_runs r
    LEFT JOIN catalog_versions v ON v.catalog_id = r.catalog_id AND v.version = r.version
    LEFT JOIN LATERAL (
        SELECT p.decision_id FROM publication_decisions p
        WHERE p.catalog_id = r.catalog_id
          AND (p.proposal_id = (v.provenance->>'proposal_id')::bigint OR p.decision_id = (v.provenance->>'decision_id')::bigint)
        ORDER BY p.decision_id DESC LIMIT 1
    ) d ON TRUE
    WHERE r.task_id = %s AND r.hotel_id = %s
    ORDER BY r.run_id
"""

_SQL = """
    SELECT payload->'statements' FROM evidence
    WHERE task_id = %s AND hotel_id = %s AND evidence_type = 'sql_read'
    ORDER BY evidence_id
"""


def answer_evidence(hotel_id, task_id):
    """Runs and SQL stored for one answered task. Empty when that task has no read."""
    from booking_system.content_store import _with

    if not task_id:
        return {"runs": [], "statements": []}

    def run(cur):
        cur.execute(_RUNS, (int(task_id), str(hotel_id or "")))
        runs = [{"run_id": row[0], "catalog_id": row[1], "version": row[2], "decision_id": row[3]} for row in cur.fetchall()]
        cur.execute(_SQL, (int(task_id), str(hotel_id or "")))
        statements = [text for row in cur.fetchall() for text in (row[0] or [])]
        return {"runs": runs, "statements": statements}

    return _with(run)


def file_complaint(hotel_name, user_id, message):
    """Record the complaint against the session's last answer: its runs, recipe versions, SQL, and publication decisions."""
    from booking_system.content_store import _transaction
    from booking_system.hotel_registry import hotel_id, new_id
    from booking_system.runtime_state import read_session

    hotel = str(hotel_name or "").upper()
    hotel_key = hotel_id(hotel)
    owner = str(user_id or "")
    last = read_session(hotel, owner).get("last_answer") or {}
    task_id = last.get("task_id")
    complaint_id = new_id("cmp")

    def run(cur):
        cur.execute(
            """SELECT u.user_id, a.user_name FROM hotel_user u JOIN user_account a ON a.user_id = u.user_id
               WHERE u.hotel_id = %s AND u.user_id = %s AND u.status = 'active'""",
            (hotel_key, owner),
        )
        users = cur.fetchall()
        if len(users) != 1:
            return {"error": "no active user " + owner + " at " + hotel}
        runs, statements = [], []
        if task_id:
            cur.execute(_RUNS, (int(task_id), hotel_key))
            runs = cur.fetchall()
            cur.execute(_SQL, (int(task_id), hotel_key))
            statements = [text for row in cur.fetchall() for text in (row[0] or [])]
        cur.execute(
            """
            INSERT INTO answer_complaints (complaint_id, hotel_id, user_id, manager_name, message, question, desk_reply, task_id, sql_executed,
                                           definition_versions)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (complaint_id, hotel_key, users[0][0], users[0][1], str(message or ""), str(last.get("question") or ""),
             str(last.get("reply") or ""), task_id, Json(statements), Json(last.get("definition_versions") or [])),
        )
        for run_id, catalog_id, version, decision_id in runs:
            cur.execute(
                "INSERT INTO answer_complaint_runs (complaint_id, run_id, catalog_id, version, decision_id) VALUES (%s, %s, %s, %s, %s)",
                (complaint_id, run_id, catalog_id, version, decision_id),
            )
        return {"complaint_id": complaint_id, "task_id": task_id, "runs": len(runs), "statements": len(statements),
                "definition_versions": last.get("definition_versions") or []}

    return _transaction(run)


def list_complaints(status="open"):
    """Complaints with each answering recipe version's contract and the reviewer output behind its publication."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT c.complaint_id, c.hotel_id, h.hotel_name, c.user_id, c.manager_name, c.message, c.question, c.desk_reply,
                   c.task_id, c.sql_executed, c.definition_versions, c.status, c.resolution_note, c.resolver, c.created_at, c.resolved_at
            FROM answer_complaints c LEFT JOIN hotel h ON h.hotel_id = c.hotel_id
            WHERE c.status = %s ORDER BY c.created_at
            """,
            (str(status),),
        )
        names = [item[0] for item in cur.description]
        found = [dict(zip(names, row)) for row in cur.fetchall()]
        for item in found:
            cur.execute(
                """
                SELECT k.run_id, k.catalog_id, k.version, v.body AS recipe, v.provenance,
                       p.decision_id, p.proposal_id, p.status AS decision_status, p.notes AS decision_notes, p.approval_ref,
                       rv.decision AS reviewer_decision, rv.confidence AS reviewer_confidence,
                       rv.findings AS reviewer_findings, rv.unresolved_questions AS reviewer_unresolved, rv.model AS reviewer_model
                FROM answer_complaint_runs k
                LEFT JOIN catalog_versions v ON v.catalog_id = k.catalog_id AND v.version = k.version
                LEFT JOIN publication_decisions p ON p.decision_id = k.decision_id
                LEFT JOIN LATERAL (
                    SELECT decision, confidence, findings, unresolved_questions, model FROM recipe_reviews
                    WHERE proposal_id = p.proposal_id ORDER BY review_id DESC LIMIT 1
                ) rv ON TRUE
                WHERE k.complaint_id = %s ORDER BY k.run_id
                """,
                (item["complaint_id"],),
            )
            columns = [entry[0] for entry in cur.description]
            item["runs"] = [dict(zip(columns, row)) for row in cur.fetchall()]
        return found

    return _with(run)


def _complaint(cur, complaint_id):
    cur.execute("SELECT status FROM answer_complaints WHERE complaint_id = %s", (str(complaint_id),))
    row = cur.fetchone()
    if not row:
        return None, []
    cur.execute("SELECT catalog_id FROM answer_complaint_runs WHERE complaint_id = %s", (str(complaint_id),))
    return row[0], [item[0] for item in cur.fetchall()]


def republish(complaint_id, catalog_id, body, note, resolver):
    """Publish an edited version of a recipe that answered this complaint. The contract and validate_publication gate it;
    no catalog agent or reviewer runs. The decision row and the version provenance name the complaint."""
    from booking_system.content_store import _transaction, _with
    from booking_system.semantic.library import validate_publication
    from booking_system.semantic.recipe import recipe_contract
    from booking_system.semantic.registry import refresh_page_index

    status, answered = _with(lambda cur: _complaint(cur, complaint_id))
    if status is None:
        return {"error": "complaint not found"}
    if catalog_id not in answered:
        return {"error": "recipe did not answer this complaint"}
    body = dict(body or {})
    if str(body.get("catalog_id") or "") != catalog_id:
        return {"error": "body catalog_id must be " + catalog_id}
    missing = recipe_contract(body)
    if missing:
        return {"error": "recipe contract " + " ".join(missing)}
    report = validate_publication(catalog_id, body)
    if not report.get("ok"):
        return {"error": str(report.get("error") or "validation failed")}

    def run(cur):
        cur.execute("SELECT status FROM catalogs WHERE catalog_id = %s", (catalog_id,))
        page = cur.fetchone()
        if not page or page[0] != "active":
            return {"error": "catalog is not active"}
        cur.execute("SELECT version FROM catalog_active_versions WHERE scope = 'global' AND catalog_id = %s", (catalog_id,))
        prior = cur.fetchone()
        cur.execute(
            """
            INSERT INTO publication_decisions (proposal_id, catalog_id, status, notes, approval_ref)
            VALUES (NULL, %s, 'published', %s, %s) RETURNING decision_id
            """,
            (catalog_id, str(note or ""), "admin:" + str(resolver)),
        )
        decision_id = int(cur.fetchone()[0])
        cur.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM catalog_versions WHERE catalog_id = %s", (catalog_id,))
        version = int(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO catalog_versions (catalog_id, version, body, provenance) VALUES (%s, %s, %s, %s)",
            (catalog_id, version, Json(body), Json({
                "source": "complaint", "complaint_id": str(complaint_id), "decision_id": decision_id, "resolver": str(resolver),
            })),
        )
        cur.execute(
            """
            INSERT INTO catalog_active_versions (scope, catalog_id, version) VALUES ('global', %s, %s)
            ON CONFLICT (scope, catalog_id) DO UPDATE SET version = EXCLUDED.version, activated_at = NOW()
            """,
            (catalog_id, version),
        )
        cur.execute(
            "INSERT INTO knowledge_publications (catalog_id, version, previous_version) VALUES (%s, %s, %s)",
            (catalog_id, version, int(prior[0]) if prior else None),
        )
        refresh_page_index(cur, catalog_id)
        return {"catalog_id": catalog_id, "version": version, "previous_version": int(prior[0]) if prior else None, "decision_id": decision_id}

    return _transaction(run)


def retire(complaint_id, catalog_id, note, resolver):
    """Take a recipe that answered this complaint out of use, and record the decision."""
    from booking_system.content_store import _with
    from booking_system.semantic.registry import retire_catalog, save_publication_decision

    status, answered = _with(lambda cur: _complaint(cur, complaint_id))
    if status is None:
        return {"error": "complaint not found"}
    if catalog_id not in answered:
        return {"error": "recipe did not answer this complaint"}
    retired = retire_catalog(catalog_id)
    if retired.get("error"):
        return retired
    retired.update(save_publication_decision(None, catalog_id, "retired", str(note or "") + " complaint " + str(complaint_id), "admin:" + str(resolver)))
    return retired


def close(complaint_id, status, note, resolver):
    """Resolve or dismiss an open complaint with a note."""
    from booking_system.content_store import _with

    if status not in STATUSES[1:]:
        return {"error": "status must be resolved or dismissed"}

    def run(cur):
        cur.execute(
            """
            UPDATE answer_complaints SET status = %s, resolution_note = %s, resolver = %s, resolved_at = NOW()
            WHERE complaint_id = %s AND status = 'open' RETURNING complaint_id
            """,
            (status, str(note or ""), str(resolver), str(complaint_id)),
        )
        row = cur.fetchone()
        return {"complaint_id": row[0], "status": status} if row else {"error": "no open complaint " + str(complaint_id)}

    return _with(run)
