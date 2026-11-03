"""Versioned read catalogs in PostgreSQL. Prefer repairing an active section over creating a child."""

import json
import psycopg2
from psycopg2.extras import Json

from booking_system import schema_catalog
from booking_system.content_store import add_column, hotel_column

def initialize_catalog():
    """Schema, seed, and one full index rebuild. A chat turn does not call this."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS catalogs (
                catalog_id VARCHAR NOT NULL PRIMARY KEY,
                parent_id VARCHAR,
                domain VARCHAR NOT NULL,
                purpose TEXT NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'active',
                store VARCHAR NOT NULL DEFAULT 'postgresql'
            )
            """
        )
        add_column(cur, "catalogs", "store", "VARCHAR NOT NULL DEFAULT 'postgresql'")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_versions (
                catalog_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                body JSONB NOT NULL,
                provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (catalog_id, version)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_active_versions (
                scope VARCHAR NOT NULL DEFAULT 'global',
                catalog_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (scope, catalog_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_proposals (
                proposal_id BIGSERIAL PRIMARY KEY,
                trace_id BIGINT,
                action VARCHAR NOT NULL,
                catalog_id VARCHAR NOT NULL,
                parent_id VARCHAR,
                body JSONB NOT NULL,
                diagnosis TEXT NOT NULL DEFAULT '',
                status VARCHAR NOT NULL DEFAULT 'candidate',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS investigation_traces (
                trace_id BIGSERIAL PRIMARY KEY,
                hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
                question TEXT NOT NULL,
                catalog_ids JSONB NOT NULL,
                catalog_versions JSONB NOT NULL DEFAULT '[]'::jsonb,
                attempts JSONB NOT NULL DEFAULT '[]'::jsonb,
                stall_reason VARCHAR NOT NULL DEFAULT '',
                row_count INTEGER NOT NULL DEFAULT 0,
                elapsed_ms INTEGER NOT NULL DEFAULT 0,
                reply_excerpt TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_test_runs (
                run_id BIGSERIAL PRIMARY KEY,
                proposal_id BIGINT,
                baseline_ok BOOLEAN,
                candidate_ok BOOLEAN,
                notes TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_keyphrase (
                catalog_id VARCHAR NOT NULL,
                phrase TEXT NOT NULL,
                PRIMARY KEY (catalog_id, phrase)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS catalog_keyphrase_phrase_trgm
            ON catalog_keyphrase USING gin (phrase gin_trgm_ops)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_index (
                catalog_id VARCHAR NOT NULL PRIMARY KEY,
                version INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
                aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
                domains JSONB NOT NULL DEFAULT '[]'::jsonb,
                entities JSONB NOT NULL DEFAULT '[]'::jsonb,
                status VARCHAR NOT NULL DEFAULT 'published',
                search_vector tsvector
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS knowledge_index_search ON knowledge_index USING gin (search_vector)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_tests (
                catalog_id VARCHAR NOT NULL,
                hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
                expected_value TEXT NOT NULL,
                PRIMARY KEY (catalog_id, hotel_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_publications (
                publication_id BIGSERIAL PRIMARY KEY,
                catalog_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                previous_version INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        add_column(cur, "investigation_traces", "retrieval", "JSONB NOT NULL DEFAULT '{}'::jsonb")
        workflow_ddl(cur)

    _with(run)
    seed_from_python()
    publish_keyphrases()
    from booking_system.content_store import _transaction
    _transaction(rebuild_all_knowledge_indexes)
    from booking_system.semantic.library import ensure_confirmed_page
    ensure_confirmed_page()
    from booking_system.guest_manager import guest_manager
    guest_manager._ensure_guest_operations()


def seed_from_python():
    """One-time bootstrap from the Python seed dicts. Live reads use Postgres only."""
    from booking_system.content_store import _with
    from booking_system.semantic import catalog as seed

    def run(cur):
        for catalog_id, entry in seed.INDEX.items():
            parent = None
            for parent_id, children in seed.PARENTS.items():
                if catalog_id in children:
                    parent = parent_id
                    break
            domain = catalog_id[:3] if len(catalog_id) >= 3 and catalog_id[0] == "B" else catalog_id
            store = str(entry.get("store") or "postgresql")
            purpose = str(entry.get("description") or entry.get("name") or catalog_id)
            cur.execute("SELECT 1 FROM catalogs WHERE catalog_id = %s", (catalog_id,))
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO catalogs (catalog_id, parent_id, domain, purpose, status, store)
                    VALUES (%s, %s, %s, %s, 'active', %s)
                    """,
                    (catalog_id, parent, domain, purpose, store),
                )
            cur.execute(
                "SELECT 1 FROM catalog_active_versions WHERE scope = 'global' AND catalog_id = %s",
                (catalog_id,),
            )
            if cur.fetchone():
                continue
            concepts = []
            for name in entry.get("concepts") or ():
                concept = seed.CATALOG.get(name)
                if not concept:
                    continue
                concepts.append({
                    "name": name,
                    "tables": list(concept["tables"]),
                    "columns": {table: list(cols) for table, cols in concept["columns"].items()},
                    "natural_key": list(concept["natural_key"]),
                    "formula": concept["formula"],
                    "missing": concept["missing"],
                    "notes": list(concept["notes"]),
                    "version": str(concept.get("version") or "1"),
                })
            body = {
                "catalog_id": catalog_id,
                "parent": parent or "",
                "title": entry.get("name") or catalog_id,
                "purpose": purpose,
                "keyphrases": list(seed.KEYPHRASES.get(catalog_id) or ()),
                "concepts": concepts,
            }
            cur.execute(
                """
                INSERT INTO catalog_versions (catalog_id, version, body, provenance)
                VALUES (%s, 1, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (catalog_id, Json(body), Json({"source": "python_seed"})),
            )
            cur.execute(
                """
                INSERT INTO catalog_active_versions (scope, catalog_id, version)
                VALUES ('global', %s, 1)
                ON CONFLICT DO NOTHING
                """,
                (catalog_id,),
            )

    _with(run)


def publish_keyphrases():
    """Copy the live body forward once, adding section keyphrases. Formulas stay."""
    from booking_system.content_store import _with
    from booking_system.semantic import catalog as seed

    def run(cur):
        for catalog_id, words in seed.KEYPHRASES.items():
            cur.execute(
                """
                SELECT v.body
                FROM catalog_active_versions a
                JOIN catalog_versions v
                  ON v.catalog_id = a.catalog_id AND v.version = a.version
                WHERE a.scope = 'global' AND a.catalog_id = %s
                """,
                (catalog_id,),
            )
            row = cur.fetchone()
            if not row:
                continue
            body = row[0] if isinstance(row[0], dict) else {}
            current = [str(item or "").strip() for item in (body.get("keyphrases") or []) if str(item or "").strip()]
            if current:
                continue
            published = dict(body)
            published["keyphrases"] = list(words)
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM catalog_versions WHERE catalog_id = %s",
                (catalog_id,),
            )
            next_version = int(cur.fetchone()[0]) + 1
            cur.execute(
                """
                INSERT INTO catalog_versions (catalog_id, version, body, provenance)
                VALUES (%s, %s, %s, %s)
                """,
                (catalog_id, next_version, Json(published), Json({"source": "keyphrase_publish"})),
            )
            cur.execute(
                """
                INSERT INTO catalog_active_versions (scope, catalog_id, version)
                VALUES ('global', %s, %s)
                ON CONFLICT (scope, catalog_id) DO UPDATE
                SET version = EXCLUDED.version, activated_at = NOW()
                """,
                (catalog_id, next_version),
            )

    _with(run)


def sync_keyphrases():
    """Rebuild the similarity index from the active catalog phrases."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute("DELETE FROM catalog_keyphrase")
        cur.execute(
            """
            INSERT INTO catalog_keyphrase (catalog_id, phrase)
            SELECT a.catalog_id, stored.phrase
            FROM catalog_active_versions a
            JOIN catalog_versions v
              ON v.catalog_id = a.catalog_id AND v.version = a.version
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE
                    WHEN jsonb_typeof(v.body->'keyphrases') = 'array' THEN v.body->'keyphrases'
                    ELSE '[]'::jsonb
                END
            ) AS stored(phrase)
            WHERE a.scope = 'global' AND stored.phrase <> ''
            ON CONFLICT DO NOTHING
            """
        )

    _with(run)


def filter_catalogs(phrases):
    """Active catalogs whose keyphrases are the same phrase. No row cap."""
    from booking_system.content_store import _with, with_settings

    wanted = []
    for phrase in phrases or []:
        name = str(phrase or "").strip()
        if name and name not in wanted:
            wanted.append(name)
    if not wanted:
        return []

    def run(cur):
        cur.execute(
            """
            SELECT DISTINCT k.catalog_id
            FROM catalog_keyphrase k
            WHERE EXISTS (
                SELECT 1
                FROM unnest(%s::text[]) AS q(phrase)
                WHERE k.phrase %% q.phrase
            )
            ORDER BY k.catalog_id
            """,
            (wanted,),
        )
        return [row[0] for row in cur.fetchall()]

    return _with(lambda cur: with_settings(cur, (("pg_trgm.similarity_threshold", 0.6),), run)) or []


def _page_aliases(catalog_id, body):
    from booking_system.semantic import catalog as seed
    found = []
    problem = str(body.get("problem") or "").strip()
    if problem:
        found.append(problem)
    for source in (body.get("aliases"), seed.ALIASES.get(catalog_id), body.get("keyphrases")):
        for phrase in source or []:
            name = str(phrase or "").strip()
            if name and name not in found:
                found.append(name)
    return found


def _active_page(cur, catalog_id):
    cur.execute(
        """
        SELECT a.version, v.body, c.domain
        FROM catalog_active_versions a
        JOIN catalog_versions v
          ON v.catalog_id = a.catalog_id AND v.version = a.version
        JOIN catalogs c ON c.catalog_id = a.catalog_id
        WHERE a.scope = 'global' AND a.catalog_id = %s
        """,
        (str(catalog_id or ""),),
    )
    return cur.fetchone()


def upsert_knowledge_index(cur, catalog_id):
    """Write one published page's summary. The caller owns the transaction."""
    row = _active_page(cur, catalog_id)
    if not row:
        raise RuntimeError("catalog index is not initialized")
    version, body, domain = row
    body = body if isinstance(body, dict) else {}
    aliases = _page_aliases(catalog_id, body)
    keywords = [str(item) for item in (body.get("keyphrases") or [])]
    entities = []
    for concept in body.get("concepts") or []:
        for table in concept.get("tables") or []:
            if table not in entities:
                entities.append(table)
    title = str(body.get("title") or catalog_id)
    summary = str(body.get("problem") or body.get("purpose") or "")
    cur.execute(
        """
        INSERT INTO knowledge_index (
            catalog_id, version, title, summary, keywords, aliases, domains, entities, status, search_vector
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'published', to_tsvector('english', %s))
        ON CONFLICT (catalog_id) DO UPDATE SET
            version = EXCLUDED.version,
            title = EXCLUDED.title,
            summary = EXCLUDED.summary,
            keywords = EXCLUDED.keywords,
            aliases = EXCLUDED.aliases,
            domains = EXCLUDED.domains,
            entities = EXCLUDED.entities,
            status = EXCLUDED.status,
            search_vector = EXCLUDED.search_vector
        """,
        (
            str(catalog_id),
            int(version or 0),
            title,
            summary,
            Json(keywords),
            Json(aliases),
            Json([str(domain or "")] if domain else []),
            Json(entities),
            " ".join([title, summary, " ".join(keywords), " ".join(aliases)]),
        ),
    )


def replace_catalog_aliases(cur, catalog_id):
    """Replace one page's alias rows. The caller owns the transaction."""
    row = _active_page(cur, catalog_id)
    if not row:
        raise RuntimeError("catalog index is not initialized")
    body = row[1] if isinstance(row[1], dict) else {}
    cur.execute("DELETE FROM catalog_keyphrase WHERE catalog_id = %s", (str(catalog_id or ""),))
    for phrase in _page_aliases(catalog_id, body):
        cur.execute(
            """
            INSERT INTO catalog_keyphrase (catalog_id, phrase)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (str(catalog_id), phrase),
        )


def refresh_page_index(cur, catalog_id):
    """Point the index at one active page."""
    upsert_knowledge_index(cur, catalog_id)
    replace_catalog_aliases(cur, catalog_id)


def rebuild_all_knowledge_indexes(cur=None):
    """Replace every index row. Maintenance only."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute("DELETE FROM knowledge_index")
        cur.execute("DELETE FROM catalog_keyphrase")
        cur.execute("SELECT catalog_id FROM catalog_active_versions WHERE scope = 'global' ORDER BY catalog_id")
        for (catalog_id,) in cur.fetchall():
            refresh_page_index(cur, catalog_id)

    if cur is not None:
        return run(cur)
    return _with(run)


def verify_catalog_index():
    """Active version and alias list against the index. Maintenance only."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT a.catalog_id, a.version, v.body, i.version, i.aliases
            FROM catalog_active_versions a
            JOIN catalog_versions v
              ON v.catalog_id = a.catalog_id AND v.version = a.version
            LEFT JOIN knowledge_index i ON i.catalog_id = a.catalog_id
            WHERE a.scope = 'global'
            ORDER BY a.catalog_id
            """
        )
        mismatched = []
        for catalog_id, version, body, index_version, stored_aliases in cur.fetchall():
            body = body if isinstance(body, dict) else {}
            expected = _page_aliases(catalog_id, body)
            stored = list(stored_aliases or [])
            if index_version is None or int(index_version) != int(version or 0) or stored != expected:
                mismatched.append(str(catalog_id))
        return mismatched

    return _with(run) or []


_SEARCH = """
    WITH asked AS (
        SELECT plainto_tsquery('english', %(request)s) AS tsq,
               %(request)s::text AS raw
    ),
    by_index AS (
        SELECT i.catalog_id,
               ts_rank_cd(i.search_vector, asked.tsq)::float8 AS score
        FROM knowledge_index i
        CROSS JOIN asked
        WHERE i.status = 'published'
          AND i.search_vector @@ asked.tsq
    ),
    by_similarity AS (
        SELECT k.catalog_id,
               MAX(similarity(k.phrase, asked.raw))::float8 AS score
        FROM catalog_keyphrase k
        CROSS JOIN asked
        WHERE k.phrase %% asked.raw
        GROUP BY k.catalog_id
    )
    SELECT i.catalog_id, i.title, i.summary, i.version, MAX(h.score) AS score
    FROM (
        SELECT catalog_id, score FROM by_index
        UNION ALL
        SELECT catalog_id, score FROM by_similarity
    ) h
    JOIN knowledge_index i ON i.catalog_id = h.catalog_id AND i.status = 'published'
    GROUP BY i.catalog_id, i.title, i.summary, i.version
    ORDER BY score DESC, i.catalog_id
    LIMIT %(limit)s
"""


def search_knowledge(request, limit=8):
    """Short list from the knowledge index and phrase similarity. The tsvector GIN index and the trigram GIN index
    supply the hits. Summaries only; recipe bodies stay out of this read."""
    from booking_system.content_store import _with, with_settings

    def run(cur):
        cur.execute(_SEARCH, {"request": str(request or ""), "limit": int(limit)})
        return [
            {"catalog_id": catalog_id, "title": title, "summary": summary, "version": int(version or 0), "score": float(score or 0)}
            for catalog_id, title, summary, version, score in cur.fetchall()
        ]

    try:
        return _with(lambda cur: with_settings(cur, (("pg_trgm.similarity_threshold", "0.3"),), run)) or []
    except psycopg2.errors.UndefinedTable:
        raise RuntimeError("catalog index is not initialized")


def rollback_catalog(catalog_id):
    """Point the active page back at the previous published version and refresh the index."""
    from booking_system.content_store import _transaction

    def run(cur):
        cur.execute(
            """
            SELECT previous_version FROM knowledge_publications
            WHERE catalog_id = %s AND previous_version IS NOT NULL
            ORDER BY publication_id DESC
            LIMIT 1
            """,
            (str(catalog_id or ""),),
        )
        row = cur.fetchone()
        if not row:
            return {"error": "no previous version"}
        previous = int(row[0])
        cur.execute(
            """
            INSERT INTO catalog_active_versions (scope, catalog_id, version)
            VALUES ('global', %s, %s)
            ON CONFLICT (scope, catalog_id) DO UPDATE
            SET version = EXCLUDED.version, activated_at = NOW()
            """,
            (str(catalog_id or ""), previous),
        )
        refresh_page_index(cur, catalog_id)
        return {"catalog_id": catalog_id, "version": previous}

    return _transaction(run)


def list_active_catalogs():
    """Active catalog index rows from Postgres."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT c.catalog_id, c.parent_id, c.domain, c.purpose, c.status, c.store,
                   a.version, v.body, c.scope, c.user_id
            FROM catalogs c
            JOIN catalog_active_versions a
              ON a.catalog_id = c.catalog_id AND a.scope = 'global'
            JOIN catalog_versions v
              ON v.catalog_id = a.catalog_id AND v.version = a.version
            ORDER BY c.catalog_id
            """
        )
        rows = []
        for row in cur.fetchall():
            body = row[7] if isinstance(row[7], dict) else {}
            rows.append({
                "catalog_id": row[0],
                "parent_id": row[1],
                "domain": row[2],
                "purpose": row[3],
                "status": row[4],
                "store": row[5],
                "version": int(row[6] or 0),
                "title": str(body.get("title") or row[0]),
                "concepts": list(body.get("concepts") or []),
                "body": body,
                "scope": row[8],
                "user_id": row[9],
            })
        return rows

    return _with(run) or []


def retire_catalog(catalog_id):
    """Take a page out of use: no active version, no index entry. Its versions stay as history."""
    from booking_system.content_store import _transaction

    def run(cur):
        cur.execute("UPDATE catalogs SET status = 'retired' WHERE catalog_id = %s RETURNING catalog_id", (str(catalog_id),))
        if not cur.fetchone():
            return {"error": "catalog not found", "catalog_id": catalog_id}
        cur.execute("DELETE FROM catalog_active_versions WHERE catalog_id = %s", (str(catalog_id),))
        cur.execute("DELETE FROM knowledge_index WHERE catalog_id = %s", (str(catalog_id),))
        cur.execute("DELETE FROM catalog_keyphrase WHERE catalog_id = %s", (str(catalog_id),))
        return {"catalog_id": catalog_id, "status": "retired"}

    return _transaction(run)


def assign_user_scope(catalog_id, user_id):
    """Mark a learned page as one user's own recipe."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            "UPDATE catalogs SET scope = 'user', user_id = %s WHERE catalog_id = %s RETURNING catalog_id",
            (str(user_id or ""), str(catalog_id)),
        )
        return bool(cur.fetchone())

    return _with(run)


def visible_to(row, user_id):
    """General recipes are for every hotel and user. A user recipe is visible only to the user who owns it."""
    return row.get("scope") == "general" or (
        row.get("scope") == "user" and bool(user_id) and str(row.get("user_id") or "") == str(user_id)
    )


def catalog_scopes(catalog_ids):
    """Scope and owner of each catalog id, read from the catalog page."""
    from booking_system.content_store import _with

    wanted = [str(item) for item in catalog_ids or [] if str(item)]
    if not wanted:
        return {}

    def run(cur):
        cur.execute(
            "SELECT catalog_id, scope, user_id, status FROM catalogs WHERE catalog_id = ANY(%s)",
            (wanted,),
        )
        return {row[0]: {"scope": row[1], "user_id": row[2], "status": row[3]} for row in cur.fetchall()}

    return _with(run)


def parents_map():
    found = {}
    for row in list_active_catalogs():
        parent = str(row.get("parent_id") or "").strip()
        if not parent:
            continue
        found.setdefault(parent, [])
        if row["catalog_id"] not in found[parent]:
            found[parent].append(row["catalog_id"])
    return found


def concept_entry(name):
    """One concept from active Postgres bodies. Prefer a child section over a parent copy."""
    name = str(name or "")
    matches = []
    for row in list_active_catalogs():
        for concept in row.get("concepts") or []:
            if str(concept.get("name") or "") == name:
                matches.append((row, concept))
    if not matches:
        return None
    matches.sort(
        key=lambda pair: (
            0 if pair[0].get("parent_id") else 1,
            -int(pair[0].get("version") or 0),
            -int(str(pair[1].get("version") or "0").split(".")[0] or 0),
        )
    )
    return matches[0][1]


def catalog_exists(catalog_id):
    return active_body(catalog_id) is not None


def active_body(catalog_id):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT v.body
            FROM catalog_active_versions a
            JOIN catalog_versions v
              ON v.catalog_id = a.catalog_id AND v.version = a.version
            WHERE a.scope = 'global' AND a.catalog_id = %s
            """,
            (str(catalog_id or ""),),
        )
        row = cur.fetchone()
        return row[0] if row else None

    return _with(run)


def active_pages(catalog_ids):
    """Scope, owner, active version, and body of each id with an active version, in one read."""
    from booking_system.content_store import _with

    wanted = [str(item) for item in catalog_ids or [] if str(item or "")]
    if not wanted:
        return {}

    def run(cur):
        cur.execute(
            """
            SELECT c.catalog_id, c.scope, c.user_id, c.status, a.version, v.body
            FROM catalogs c
            JOIN catalog_active_versions a ON a.catalog_id = c.catalog_id AND a.scope = 'global'
            JOIN catalog_versions v ON v.catalog_id = a.catalog_id AND v.version = a.version
            WHERE c.catalog_id = ANY(%s)
            """,
            (wanted,),
        )
        return {
            row[0]: {"scope": row[1], "user_id": row[2], "status": row[3], "version": int(row[4] or 0), "body": row[5]}
            for row in cur.fetchall() if row[5]
        }

    return _with(run)


def active_versions(catalog_ids):
    """The active version and body of each id that has one, in the order asked, in one read."""
    from booking_system.content_store import _with

    wanted = [str(item or "") for item in catalog_ids or []]
    if not wanted:
        return []

    def run(cur):
        cur.execute(
            """
            SELECT a.catalog_id, a.version, v.body
            FROM catalog_active_versions a
            JOIN catalog_versions v ON v.catalog_id = a.catalog_id AND v.version = a.version
            WHERE a.scope = 'global' AND a.catalog_id = ANY(%s)
            """,
            (wanted,),
        )
        return {row[0]: (int(row[1] or 0), row[2]) for row in cur.fetchall()}

    loaded = _with(run)
    return [
        {"catalog_id": catalog_id, "version": loaded[catalog_id][0], "body": loaded[catalog_id][1]}
        for catalog_id in wanted if catalog_id in loaded and loaded[catalog_id][1]
    ]


def save_proposal(trace_id, action, catalog_id, parent_id, body, diagnosis):
    from booking_system.content_store import _with
    from booking_system.semantic.recipe import proposal_hash

    stored = body or {}
    digest = proposal_hash(stored)

    def run(cur):
        cur.execute(
            """
            INSERT INTO catalog_proposals (
                trace_id, action, catalog_id, parent_id, body, diagnosis, status, content_hash
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'candidate', %s)
            RETURNING proposal_id
            """,
            (
                trace_id,
                str(action or "repair"),
                str(catalog_id or ""),
                parent_id,
                Json(stored),
                str(diagnosis or ""),
                digest,
            ),
        )
        return cur.fetchone()[0]

    return _with(run)


def approve_proposal(proposal_id, approval_ref):
    """Record the publisher's authorization. Does not publish the page."""
    from booking_system.content_store import _with

    ref = str(approval_ref or "").strip()
    if not ref:
        return {"status": "awaiting_approval", "error": "reviewer approval required"}

    def run(cur):
        cur.execute(
            """
            UPDATE catalog_proposals
            SET status = 'approved'
            WHERE proposal_id = %s AND status IN ('candidate', 'approved')
            RETURNING proposal_id
            """,
            (int(proposal_id),),
        )
        row = cur.fetchone()
        if not row:
            return {"error": "proposal is not awaiting approval"}
        return {"proposal_id": int(row[0]), "status": "approved", "approval_ref": ref}

    return _with(run)


def set_proposal_status(proposal_id, status):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            UPDATE catalog_proposals
            SET status = %s
            WHERE proposal_id = %s
            RETURNING proposal_id
            """,
            (str(status or ""), int(proposal_id)),
        )
        row = cur.fetchone()
        return {"proposal_id": int(row[0]), "status": str(status or "")} if row else {"error": "proposal not found"}

    return _with(run)


def promote_proposal(proposal_id, assisted=True, approval_ref="", owner=""):
    """Activate a learned recipe as the owner's user recipe. Repair bumps the owner's own catalog_id; create_child inserts a new id.
    A general recipe is never changed by a learned proposal."""
    from booking_system.content_store import _transaction

    owner = str(owner or "")
    if not owner:
        return {"error": "a learned recipe needs the user who owns it"}

    def run(cur):
        cur.execute(
            "SELECT proposal_id, action, catalog_id, parent_id, body, status FROM catalog_proposals WHERE proposal_id = %s",
            (int(proposal_id),),
        )
        row = cur.fetchone()
        if not row:
            return {"error": "proposal not found"}
        _pid, action, catalog_id, parent_id, body, status = row
        if status != "approved":
            return {"status": "awaiting_approval", "error": "reviewer approval required"}
        action = str(action or "repair")
        catalog_id = str(catalog_id or "")
        body = body if isinstance(body, dict) else {}
        cur.execute(
            "SELECT version FROM catalog_active_versions WHERE scope = 'global' AND catalog_id = %s",
            (catalog_id,),
        )
        prior = cur.fetchone()
        previous = int(prior[0]) if prior else None
        if action == "repair":
            cur.execute("SELECT scope, user_id FROM catalogs WHERE catalog_id = %s", (catalog_id,))
            page = cur.fetchone()
            if not page:
                return {"error": "catalog not found; use create_child"}
            if page[0] != "user" or page[1] != owner:
                return {"error": "only the owner's user recipe can be repaired; " + catalog_id + " is " + page[0]}
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM catalog_versions WHERE catalog_id = %s",
                (catalog_id,),
            )
            next_version = int(cur.fetchone()[0]) + 1
            cur.execute(
                """
                INSERT INTO catalog_versions (catalog_id, version, body, provenance)
                VALUES (%s, %s, %s, %s)
                """,
                (catalog_id, next_version, Json(body), Json({
                    "proposal_id": int(proposal_id), "action": "repair", "approval_ref": str(approval_ref or ""),
                    "scope": "user", "user_id": owner,
                })),
            )
            cur.execute(
                """
                INSERT INTO catalog_active_versions (scope, catalog_id, version)
                VALUES ('global', %s, %s)
                ON CONFLICT (scope, catalog_id) DO UPDATE
                SET version = EXCLUDED.version, activated_at = NOW()
                """,
                (catalog_id, next_version),
            )
        elif action == "create_child":
            parent_id = str(parent_id or body.get("parent") or "B01")
            cur.execute("SELECT 1 FROM catalogs WHERE catalog_id = %s", (catalog_id,))
            if cur.fetchone():
                return {"error": "catalog already exists; use repair"}
            cur.execute(
                """
                INSERT INTO catalogs (catalog_id, parent_id, domain, purpose, status, store, scope, user_id)
                VALUES (%s, %s, %s, %s, 'active', %s, 'user', %s)
                """,
                (
                    catalog_id,
                    parent_id,
                    str(parent_id or "B01")[:3],
                    str(body.get("purpose") or body.get("title") or catalog_id),
                    "postgresql",
                    owner,
                ),
            )
            cur.execute(
                """
                INSERT INTO catalog_versions (catalog_id, version, body, provenance)
                VALUES (%s, 1, %s, %s)
                """,
                (catalog_id, Json(body), Json({
                    "proposal_id": int(proposal_id), "action": "create_child", "approval_ref": str(approval_ref or ""),
                    "scope": "user", "user_id": owner,
                })),
            )
            cur.execute(
                """
                INSERT INTO catalog_active_versions (scope, catalog_id, version)
                VALUES ('global', %s, 1)
                """,
                (catalog_id,),
            )
        else:
            return {"error": "unknown action " + action}
        cur.execute(
            "UPDATE catalog_proposals SET status = %s WHERE proposal_id = %s",
            ("published" if not assisted else "published", int(proposal_id)),
        )
        cur.execute(
            "SELECT version FROM catalog_active_versions WHERE scope = 'global' AND catalog_id = %s",
            (catalog_id,),
        )
        current = cur.fetchone()
        cur.execute(
            """
            INSERT INTO knowledge_publications (catalog_id, version, previous_version)
            VALUES (%s, %s, %s)
            """,
            (catalog_id, int(current[0]) if current else 1, previous),
        )
        refresh_page_index(cur, catalog_id)
        return {
            "catalog_id": catalog_id, "action": action, "status": "published", "previous_version": previous,
            "version": int(current[0]) if current else 1, "scope": "user", "user_id": owner,
        }

    return _transaction(run)


def record_test_run(proposal_id, baseline_ok, candidate_ok, notes, input_hash="", checks=None, validator_version=""):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            INSERT INTO catalog_test_runs (
                proposal_id, baseline_ok, candidate_ok, notes, input_hash, checks, validator_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING run_id
            """,
            (
                proposal_id,
                bool(baseline_ok),
                bool(candidate_ok),
                str(notes or ""),
                str(input_hash or ""),
                Json(checks or {}),
                str(validator_version or ""),
            ),
        )
        return cur.fetchone()[0]

    return _with(run)


def validation_for_hash(proposal_id, input_hash):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT run_id, candidate_ok, input_hash, notes
            FROM catalog_test_runs
            WHERE proposal_id = %s AND input_hash = %s AND candidate_ok = TRUE
            ORDER BY run_id DESC
            LIMIT 1
            """,
            (int(proposal_id), str(input_hash or "")),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"run_id": int(row[0]), "candidate_ok": bool(row[1]), "input_hash": row[2], "notes": row[3]}

    return _with(run)


def save_recipe_review(proposal_id, proposal_hash_value, decision, confidence, findings, unresolved, model, package):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            INSERT INTO recipe_reviews (
                proposal_id, proposal_hash, decision, confidence, findings,
                unresolved_questions, model, package
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING review_id
            """,
            (
                int(proposal_id),
                str(proposal_hash_value or ""),
                str(decision or ""),
                str(confidence or ""),
                Json(list(findings or [])),
                Json(list(unresolved or [])),
                str(model or ""),
                Json(package or {}),
            ),
        )
        return int(cur.fetchone()[0])

    return _with(run)


def load_recipe_review(proposal_id):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT review_id, proposal_hash, decision, confidence, findings, unresolved_questions
            FROM recipe_reviews
            WHERE proposal_id = %s
            ORDER BY review_id DESC
            LIMIT 1
            """,
            (int(proposal_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "review_id": int(row[0]),
            "proposal_hash": row[1],
            "decision": row[2],
            "confidence": row[3],
            "findings": row[4] if isinstance(row[4], list) else [],
            "unresolved_questions": row[5] if isinstance(row[5], list) else [],
        }

    return _with(run)


def active_recipe_summaries(limit=8):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT a.catalog_id, a.version, v.body
            FROM catalog_active_versions a
            JOIN catalog_versions v
              ON v.catalog_id = a.catalog_id AND v.version = a.version
            WHERE a.scope = 'global'
            ORDER BY a.catalog_id
            LIMIT %s
            """,
            (int(limit),),
        )
        found = []
        for catalog_id, version, body in cur.fetchall():
            body = body if isinstance(body, dict) else {}
            found.append({
                "catalog_id": catalog_id,
                "version": int(version),
                "problem": str(body.get("problem") or body.get("purpose") or ""),
                "output_grain": str(body.get("output_grain") or ""),
            })
        return found

    return _with(run)


def latest_problem_spec(hotel_id, question):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT body
            FROM problem_specs
            WHERE hotel_id = %s AND original_question = %s
            ORDER BY spec_id DESC
            LIMIT 1
            """,
            (str(hotel_id or ""), str(question or "")),
        )
        row = cur.fetchone()
        return row[0] if row and isinstance(row[0], dict) else {}

    return _with(run)


def _owner_column(cur, table, seen):
    """The owner of a learned recipe, investigation, or gap is the user id. A table still keyed by manager_name is renamed once."""
    present = seen.columns(table) & {"user_id", "manager_name"}
    if "user_id" in present:
        return
    if "manager_name" in present:
        cur.execute("ALTER TABLE " + table + " RENAME COLUMN manager_name TO user_id")
        return
    add_column(cur, table, "user_id", "VARCHAR NOT NULL DEFAULT ''", seen)


def workflow_ddl(cur):
    """Tables for events, tasks, context packages, evidence, and one open investigation per gap. The catalog is read once
    (schema_catalog) and the CREATE TABLE IF NOT EXISTS statements travel together, so a ready schema costs few round trips."""
    seen = schema_catalog.snapshot(cur)
    tables = []
    tables.append(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id BIGSERIAL PRIMARY KEY,
            event_type VARCHAR NOT NULL,
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            idempotency_key VARCHAR NOT NULL UNIQUE,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    tables.append(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id BIGSERIAL PRIMARY KEY,
            event_id BIGINT NOT NULL,
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            agent VARCHAR NOT NULL,
            purpose VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    tables.append(
        """
        CREATE TABLE IF NOT EXISTS context_packages (
            package_id BIGSERIAL PRIMARY KEY,
            task_id BIGINT NOT NULL,
            agent VARCHAR NOT NULL,
            purpose VARCHAR NOT NULL,
            policy_version VARCHAR NOT NULL,
            retrieval_status VARCHAR NOT NULL DEFAULT '',
            referenced_versions JSONB NOT NULL DEFAULT '[]'::jsonb,
            token_count INTEGER NOT NULL DEFAULT 0,
            body JSONB NOT NULL DEFAULT '{}'::jsonb,
            search_ms INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    tables.append(
        """
        CREATE TABLE IF NOT EXISTS evidence (
            evidence_id BIGSERIAL PRIMARY KEY,
            task_id BIGINT,
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            evidence_type VARCHAR NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            source VARCHAR NOT NULL DEFAULT '',
            integrity_hash VARCHAR NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    tables.append(
        """
        CREATE TABLE IF NOT EXISTS investigations (
            investigation_id BIGSERIAL PRIMARY KEY,
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            gap_fingerprint VARCHAR NOT NULL,
            trace_id BIGINT,
            status VARCHAR NOT NULL,
            current_step VARCHAR NOT NULL DEFAULT '',
            next_action VARCHAR NOT NULL DEFAULT '',
            evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            lease_until TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(";\n".join(tables))
    tables.clear()
    cur.execute(
        """
        SELECT indexdef FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = 'investigations_one_open_gap'
        """
    )
    defined = cur.fetchone()
    if not defined or "awaiting_approval" not in str(defined[0]):
        cur.execute("DROP INDEX IF EXISTS investigations_one_open_gap")
        cur.execute(
            """
            CREATE UNIQUE INDEX investigations_one_open_gap
            ON investigations (gap_fingerprint)
            WHERE status IN ('open', 'leased', 'awaiting_approval')
            """
        )
    tables.append(
        """
        CREATE TABLE IF NOT EXISTS problem_specs (
            spec_id BIGSERIAL PRIMARY KEY,
            event_id BIGINT,
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            original_question TEXT NOT NULL,
            body JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    tables.append(
        """
        CREATE TABLE IF NOT EXISTS execution_runs (
            run_id BIGSERIAL PRIMARY KEY,
            task_id BIGINT,
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            catalog_id VARCHAR NOT NULL DEFAULT '',
            version INTEGER,
            parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
            row_count INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            elapsed_ms INTEGER NOT NULL DEFAULT 0,
            definition_versions JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    tables.append(
        """
        CREATE TABLE IF NOT EXISTS composition_plans (
            plan_id BIGSERIAL PRIMARY KEY,
            task_id BIGINT,
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            body JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    tables.append(
        """
        CREATE TABLE IF NOT EXISTS publication_decisions (
            decision_id BIGSERIAL PRIMARY KEY,
            proposal_id BIGINT,
            catalog_id VARCHAR NOT NULL DEFAULT '',
            status VARCHAR NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            approval_ref VARCHAR NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    tables.append(
        """
        CREATE TABLE IF NOT EXISTS recipe_reviews (
            review_id BIGSERIAL PRIMARY KEY,
            proposal_id BIGINT NOT NULL,
            proposal_hash VARCHAR NOT NULL,
            decision VARCHAR NOT NULL,
            confidence VARCHAR NOT NULL DEFAULT '',
            findings JSONB NOT NULL DEFAULT '[]'::jsonb,
            unresolved_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
            model VARCHAR NOT NULL DEFAULT '',
            package JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    if seen.exists("catalog_proposals"):
        add_column(cur, "catalog_proposals", "content_hash", "VARCHAR NOT NULL DEFAULT ''", seen)
    if seen.exists("catalog_test_runs"):
        add_column(cur, "catalog_test_runs", "input_hash", "VARCHAR NOT NULL DEFAULT ''", seen)
        add_column(cur, "catalog_test_runs", "checks", "JSONB NOT NULL DEFAULT '{}'::jsonb", seen)
        add_column(cur, "catalog_test_runs", "validator_version", "VARCHAR NOT NULL DEFAULT ''", seen)
    add_column(cur, "investigations", "revision_count", "INTEGER NOT NULL DEFAULT 0", seen)
    add_column(cur, "investigations", "proposal_id", "BIGINT", seen)
    _owner_column(cur, "investigations", seen)
    add_column(cur, "investigations", "closed_at", "TIMESTAMPTZ", seen)
    add_column(cur, "investigations", "tokens_used", "BIGINT NOT NULL DEFAULT 0", seen)
    if seen.exists("catalogs"):
        add_column(cur, "catalogs", "scope", "VARCHAR NOT NULL DEFAULT 'general'", seen)
        _owner_column(cur, "catalogs", seen)
    cur.execute(
        """
        UPDATE investigations SET closed_at = created_at
        WHERE closed_at IS NULL AND status NOT IN ('open', 'leased', 'awaiting_approval')
        """
    )
    tables.append(
        """
        CREATE TABLE IF NOT EXISTS gap_candidates (
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            gap_fingerprint VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL,
            occurrences INTEGER NOT NULL DEFAULT 0,
            status VARCHAR NOT NULL DEFAULT 'candidate',
            reason VARCHAR NOT NULL DEFAULT '',
            last_question TEXT NOT NULL DEFAULT '',
            first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (hotel_id, gap_fingerprint, user_id)
        )
        """
    )
    tables.append(
        """
        CREATE TABLE IF NOT EXISTS agent_usage (
            hotel_id VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
            usage_date DATE NOT NULL,
            origin VARCHAR NOT NULL,
            agent VARCHAR NOT NULL,
            calls INTEGER NOT NULL DEFAULT 0,
            input_tokens BIGINT NOT NULL DEFAULT 0,
            output_tokens BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (hotel_id, usage_date, origin, agent)
        )
        """
    )
    cur.execute(";\n".join(tables))
    if seen.exists("execution_runs"):
        add_column(cur, "execution_runs", "definition_versions", "JSONB NOT NULL DEFAULT '[]'::jsonb", seen)
    _owner_column(cur, "gap_candidates", seen)
    for table in ("events", "tasks", "evidence", "investigations", "problem_specs", "execution_runs", "composition_plans"):
        hotel_column(cur, table, seen=seen)
    hotel_column(cur, "gap_candidates", ("hotel_id", "gap_fingerprint", "user_id"), seen)
    hotel_column(cur, "agent_usage", ("hotel_id", "usage_date", "origin", "agent"), seen)
    for table, key in (("investigation_traces", ()), ("knowledge_tests", ("catalog_id", "hotel_id")), ("complaints", ())):
        if seen.exists(table):
            hotel_column(cur, table, key, seen)
    from booking_system.runtime_state import chat_ddl
    chat_ddl(cur, seen)


_WORKFLOW_READY = []


def ensure_workflow_tables():
    """Create the workflow tables once per process. Does not seed catalogs or rebuild the index."""
    from booking_system.content_store import _with
    if not _WORKFLOW_READY:
        _with(workflow_ddl)
        _WORKFLOW_READY.append(True)


def save_problem_spec(event_id, hotel_id, spec):
    from booking_system.content_store import _with

    body = spec or {}

    def run(cur):
        cur.execute(
            """
            INSERT INTO problem_specs (event_id, hotel_id, original_question, body)
            VALUES (%s, %s, %s, %s)
            RETURNING spec_id
            """,
            (
                int(event_id) if event_id else None,
                str(hotel_id or ""),
                str(body.get("original_question") or ""),
                Json(body),
            ),
        )
        return {"spec_id": int(cur.fetchone()[0])}

    return _with(run)


def save_execution_run(task_id, hotel_id, catalog_id, version, parameters, row_count, error, elapsed_ms, definition_versions=()):
    """definition_versions: [{key, version_id, definition}] of the manager's definitions the run read (definitions.versions_used)."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            INSERT INTO execution_runs (
                task_id, hotel_id, catalog_id, version, parameters, row_count, error, elapsed_ms, definition_versions
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING run_id
            """,
            (
                int(task_id) if task_id else None,
                str(hotel_id or ""),
                str(catalog_id or ""),
                int(version) if version else None,
                Json(parameters or {}),
                int(row_count or 0),
                str(error or ""),
                int(elapsed_ms or 0),
                Json(list(definition_versions or [])),
            ),
        )
        return {"run_id": int(cur.fetchone()[0])}

    return _with(run)


def save_composition_plan(task_id, hotel_id, body):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            INSERT INTO composition_plans (task_id, hotel_id, body)
            VALUES (%s, %s, %s)
            RETURNING plan_id
            """,
            (int(task_id) if task_id else None, str(hotel_id or ""), Json(body or {})),
        )
        return {"plan_id": int(cur.fetchone()[0])}

    return _with(run)


def save_publication_decision(proposal_id, catalog_id, status, notes, approval_ref):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            INSERT INTO publication_decisions (proposal_id, catalog_id, status, notes, approval_ref)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING decision_id
            """,
            (
                int(proposal_id) if proposal_id else None,
                str(catalog_id or ""),
                str(status or ""),
                str(notes or ""),
                str(approval_ref or ""),
            ),
        )
        return {"decision_id": int(cur.fetchone()[0])}

    return _with(run)


def save_event(hotel_id, event_type, idempotency_key, payload):
    """One statement: a new key is inserted; a repeated key returns the event it already names."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            WITH added AS (
                INSERT INTO events (event_type, hotel_id, idempotency_key, payload)
                VALUES (%(type)s, %(hotel)s, %(key)s, %(payload)s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING event_id
            )
            SELECT event_id, TRUE FROM added
            UNION ALL
            SELECT event_id, FALSE FROM events WHERE idempotency_key = %(key)s AND NOT EXISTS (SELECT 1 FROM added)
            """,
            {"type": str(event_type or ""), "hotel": str(hotel_id or ""), "key": str(idempotency_key or ""), "payload": Json(payload or {})},
        )
        row = cur.fetchone()
        return {"event_id": int(row[0]), "created": bool(row[1])}

    return _with(run)


def save_task(event_id, hotel_id, agent, purpose, status):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            INSERT INTO tasks (event_id, hotel_id, agent, purpose, status)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING task_id
            """,
            (int(event_id), str(hotel_id or ""), str(agent or ""), str(purpose or ""), str(status or "")),
        )
        return {"task_id": int(cur.fetchone()[0])}

    return _with(run)


def finish_task(task_id, status):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute("UPDATE tasks SET status = %s WHERE task_id = %s", (str(status or ""), int(task_id)))
        return cur.rowcount

    return _with(run)


def save_context_package(task_id, package):
    from booking_system.content_store import _with

    body = package or {}
    versions = list(body.get("referenced_versions") or [])

    def run(cur):
        cur.execute(
            """
            INSERT INTO context_packages (
                task_id, agent, purpose, policy_version, retrieval_status,
                referenced_versions, token_count, body, search_ms
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING package_id
            """,
            (
                int(task_id),
                str(body.get("agent") or ""),
                str(body.get("purpose") or ""),
                str(body.get("policy_version") or ""),
                str((body.get("retrieval") or {}).get("status") or ""),
                Json(versions),
                int(body.get("token_count") or 0),
                Json(body),
                int(body.get("search_ms") or 0),
            ),
        )
        return {"package_id": int(cur.fetchone()[0])}

    return _with(run)


def save_evidence(task_id, hotel_id, evidence_type, payload, source):
    import hashlib
    from booking_system.content_store import _with

    encoded = json.dumps(payload or {}, sort_keys=True, default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def run(cur):
        cur.execute(
            """
            INSERT INTO evidence (task_id, hotel_id, evidence_type, payload, source, integrity_hash)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING evidence_id
            """,
            (
                int(task_id) if task_id else None,
                str(hotel_id or ""),
                str(evidence_type or ""),
                Json(payload or {}),
                str(source or ""),
                digest,
            ),
        )
        return {"evidence_id": int(cur.fetchone()[0]), "integrity_hash": digest}

    return _with(run)


def load_evidence(evidence_id):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT evidence_id, task_id, hotel_id, evidence_type, payload, source
            FROM evidence WHERE evidence_id = %s
            """,
            (int(evidence_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "evidence_id": row[0],
            "task_id": row[1],
            "hotel_id": row[2],
            "evidence_type": row[3],
            "payload": row[4] if isinstance(row[4], dict) else {},
            "source": row[5],
        }

    return _with(run)


def task_hotel(task_id):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute("SELECT hotel_id FROM tasks WHERE task_id = %s", (int(task_id),))
        row = cur.fetchone()
        return str(row[0] or "") if row else ""

    return _with(run)


_ATTACHABLE = """
    SELECT investigation_id, trace_id, status
    FROM investigations
    WHERE gap_fingerprint = %s
      AND (
        (status IN ('open', 'leased', 'awaiting_approval') AND (lease_until IS NULL OR lease_until > NOW()))
        OR (status = 'rejected' AND COALESCE(closed_at, created_at) > NOW() - INTERVAL '7 days')
        OR (status = 'blocked' AND next_action <> 'lease_expired')
      )
    ORDER BY investigation_id DESC
    LIMIT 1
"""


def attachable_investigation(gap_fingerprint):
    """The investigation a new trace for this gap would join for free: a live one, a blocked one, or one rejected within 7 days."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(_ATTACHABLE, (str(gap_fingerprint or ""),))
        row = cur.fetchone()
        return int(row[0]) if row else None

    return _with(run)


def open_investigation(hotel_id, gap_fingerprint, trace_id, user_id=""):
    """One leased investigation per gap. A live lease keeps the new trace as evidence."""
    from booking_system.content_store import _transaction

    def run(cur):
        cur.execute(_ATTACHABLE, (str(gap_fingerprint or ""),))
        row = cur.fetchone()
        attached = [int(trace_id)] if trace_id else []
        if row:
            cur.execute(
                """
                UPDATE investigations
                SET evidence_refs = evidence_refs || %s::jsonb
                WHERE investigation_id = %s
                """,
                (Json(attached), int(row[0])),
            )
            return {"investigation_id": int(row[0]), "trace_id": row[1], "attached": True}
        cur.execute(
            """
            UPDATE investigations
            SET status = 'blocked', next_action = 'lease_expired', closed_at = NOW()
            WHERE gap_fingerprint = %s AND status IN ('open', 'leased')
            """,
            (str(gap_fingerprint or ""),),
        )
        cur.execute(
            """
            INSERT INTO investigations (
                hotel_id, gap_fingerprint, trace_id, status, current_step, next_action, evidence_refs, lease_until, user_id
            )
            VALUES (%s, %s, %s, 'leased', 'catalog', 'investigate', %s, NOW() + INTERVAL '30 minutes', %s)
            RETURNING investigation_id
            """,
            (
                str(hotel_id or ""),
                str(gap_fingerprint or ""),
                int(trace_id) if trace_id else None,
                Json(attached),
                str(user_id or ""),
            ),
        )
        return {"investigation_id": int(cur.fetchone()[0]), "trace_id": trace_id, "attached": False}

    return _transaction(run)


def finish_investigation(trace_id, status):
    return close_investigation(trace_id, status, status)


def close_investigation(trace_id, status, next_action):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            UPDATE investigations
            SET status = %s, lease_until = NULL, next_action = %s, closed_at = NOW()
            WHERE trace_id = %s AND status IN ('open', 'leased', 'awaiting_approval')
            """,
            (str(status or ""), str(next_action or ""), int(trace_id)),
        )
        return cur.rowcount

    return _with(run)


def note_revision(trace_id, proposal_id):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            UPDATE investigations
            SET revision_count = revision_count + 1, proposal_id = %s
            WHERE trace_id = %s AND status IN ('open', 'leased', 'awaiting_approval')
            RETURNING revision_count
            """,
            (int(proposal_id) if proposal_id else None, int(trace_id)),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0

    return _with(run)


def reopen_investigation(trace_id):
    """A blocked investigation leases again once its definitions are answered."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            UPDATE investigations
            SET status = 'leased', next_action = 'review', lease_until = NOW() + INTERVAL '30 minutes'
            WHERE trace_id = %s AND status = 'blocked'
            RETURNING investigation_id
            """,
            (int(trace_id),),
        )
        return bool(cur.fetchone())

    return _with(run)


def latest_proposal_for_trace(trace_id):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT proposal_id FROM catalog_proposals
            WHERE trace_id = %s AND action IN ('repair', 'create_child')
            ORDER BY proposal_id DESC LIMIT 1
            """,
            (int(trace_id),),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None

    return _with(run)


def load_proposal(proposal_id):
    from booking_system.content_store import _with

    def run(cur):
        cur.execute(
            """
            SELECT proposal_id, trace_id, action, catalog_id, body, status, content_hash
            FROM catalog_proposals WHERE proposal_id = %s
            """,
            (int(proposal_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "proposal_id": row[0],
            "trace_id": row[1],
            "action": row[2],
            "catalog_id": row[3],
            "body": row[4] if isinstance(row[4], dict) else {},
            "status": row[5],
            "content_hash": row[6] or "",
        }

    return _with(run)
