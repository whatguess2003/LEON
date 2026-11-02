"""F&B outlet tables. Idempotent: each object is read from the catalog first and created only when missing; DDL waits at most LOCK_TIMEOUT.
Ids are prefix + uuid4. Names are unique attributes, never keys. Menu descriptions and images are 'fnb_menu_item' documents keyed by item_id.
Transaction codes, departments, taxes and postings are Finance's (booking_system/finance): an item points at a Finance charge code,
and check settlement posts to Finance folios."""

from booking_system import schema_catalog
from booking_system.db import connect

DOMAINS = (
    ("fnb_user_ref", "VARCHAR(48) CHECK (left(VALUE, 4) = 'usr_')"),
    ("fnb_booking_ref", "VARCHAR(48) CHECK (left(VALUE, 4) = 'bkg_')"),
    ("fnb_room_ref", "VARCHAR(48) CHECK (left(VALUE, 3) = 'rm_')"),
    ("fnb_code_ref", "VARCHAR(48) CHECK (left(VALUE, 4) = 'txc_')"),
)
# (table, columns, target table, target key): FKs to core and Finance tables, added by ensure_foreign_keys once the target key exists.
FOREIGN_KEYS = (
    ("fnb_menu_item", ("transaction_code_id",), "transaction_code", ("transaction_code_id",)),
    ("fnb_check_line", ("transaction_code_id",), "transaction_code", ("transaction_code_id",)),
    ("fnb_check", ("hotel_id", "booking_id"), "booking", ("hotel_id", "booking_id")),
    ("fnb_check", ("hotel_id", "room_id"), "room", ("hotel_id", "room_id")),
    ("fnb_check", ("opened_by_user_id",), "user_account", ("user_id",)),
    ("fnb_check", ("closed_by_user_id",), "user_account", ("user_id",)),
    ("fnb_check_line", ("added_by_user_id",), "user_account", ("user_id",)),
    ("fnb_check_line", ("voided_by_user_id",), "user_account", ("user_id",)),
)
LOCK_TIMEOUT = "5s"

TABLES = (
    ("outlet", """
        outlet_id   VARCHAR(48) PRIMARY KEY CHECK (left(outlet_id, 4) = 'out_'),
        hotel_id    VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
        outlet_name VARCHAR(64) NOT NULL CHECK (outlet_name = UPPER(btrim(outlet_name)) AND outlet_name <> ''),
        outlet_type VARCHAR(16) NOT NULL CHECK (outlet_type IN ('restaurant','bar','room_service','cafe','banquet','other')),
        status      VARCHAR(16) NOT NULL CHECK (status IN ('active','inactive')),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (hotel_id, outlet_name)"""),
    ("fnb_menu_item", """
        item_id             VARCHAR(48)   PRIMARY KEY CHECK (left(item_id, 4) = 'fmi_'),
        hotel_id            VARCHAR(48)   NOT NULL REFERENCES hotel (hotel_id),
        outlet_id           VARCHAR(48)   NOT NULL REFERENCES outlet (outlet_id),
        item_name           VARCHAR(128)  NOT NULL CHECK (item_name = UPPER(btrim(item_name)) AND item_name <> ''),
        price               NUMERIC(12,2) NOT NULL CHECK (price >= 0),
        transaction_code_id fnb_code_ref  NOT NULL,
        status              VARCHAR(16)   NOT NULL CHECK (status IN ('active','inactive')),
        UNIQUE (outlet_id, item_name)"""),
    ("fnb_check", """
        check_id          VARCHAR(48)   PRIMARY KEY CHECK (left(check_id, 4) = 'fck_'),
        hotel_id          VARCHAR(48)   NOT NULL REFERENCES hotel (hotel_id),
        outlet_id         VARCHAR(48)   NOT NULL REFERENCES outlet (outlet_id),
        business_date     DATE          NOT NULL,
        check_number      INTEGER       NOT NULL CHECK (check_number > 0),
        table_number      VARCHAR(16),
        covers            SMALLINT      NOT NULL CHECK (covers > 0),
        status            VARCHAR(8)    NOT NULL CHECK (status IN ('open','closed','void')),
        settlement        VARCHAR(8)    CHECK (settlement IN ('direct','room')),
        booking_id        fnb_booking_ref,
        room_id           fnb_room_ref,
        currency          CHAR(3)       NOT NULL,
        subtotal          NUMERIC(12,2),
        discount_total    NUMERIC(12,2),
        tax_total         NUMERIC(12,2),
        total             NUMERIC(12,2),
        void_reason       VARCHAR(255),
        opened_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
        opened_by_user_id fnb_user_ref  NOT NULL,
        closed_at         TIMESTAMPTZ,
        closed_by_user_id fnb_user_ref,
        UNIQUE (outlet_id, business_date, check_number),
        CHECK ((status = 'closed') = (settlement IS NOT NULL)),
        CHECK ((settlement = 'room') = (booking_id IS NOT NULL AND room_id IS NOT NULL)),
        CHECK ((status = 'open') = (closed_at IS NULL AND closed_by_user_id IS NULL)),
        CHECK ((status = 'void') = (void_reason IS NOT NULL)),
        CHECK ((status = 'closed') = (total IS NOT NULL)),
        settlement_round  SMALLINT      NOT NULL DEFAULT 0 CHECK (settlement_round >= 0)"""),
    ("fnb_check_correction", """
        correction_id        VARCHAR(48)   PRIMARY KEY CHECK (left(correction_id, 4) = 'fcc_'),
        hotel_id             VARCHAR(48)   NOT NULL REFERENCES hotel (hotel_id),
        check_id             VARCHAR(48)   NOT NULL REFERENCES fnb_check (check_id),
        method               VARCHAR(24)   NOT NULL CHECK (method IN ('folio_adjustment','reopen_with_approval')),
        status               VARCHAR(12)   NOT NULL CHECK (status IN ('applied','requested','approved','rejected')),
        reason               TEXT          NOT NULL CHECK (btrim(reason) <> ''),
        amount               NUMERIC(12,2),
        transaction_code_id  fnb_code_ref,
        settlement_round     SMALLINT      NOT NULL CHECK (settlement_round >= 0),
        requested_by_user_id fnb_user_ref  NOT NULL,
        requested_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
        decided_by_user_id   fnb_user_ref,
        decided_at           TIMESTAMPTZ,
        CHECK ((method = 'folio_adjustment') = (amount IS NOT NULL AND transaction_code_id IS NOT NULL)),
        CHECK ((method = 'folio_adjustment') = (status = 'applied')),
        CHECK ((status IN ('approved','rejected')) = (decided_by_user_id IS NOT NULL AND decided_at IS NOT NULL)),
        CHECK (decided_by_user_id IS NULL OR decided_by_user_id <> requested_by_user_id)"""),
    ("fnb_check_line", """
        line_id             VARCHAR(48)   PRIMARY KEY CHECK (left(line_id, 4) = 'fcl_'),
        check_id            VARCHAR(48)   NOT NULL REFERENCES fnb_check (check_id),
        line_seq            SMALLINT      NOT NULL CHECK (line_seq > 0),
        item_id             VARCHAR(48)   NOT NULL REFERENCES fnb_menu_item (item_id),
        transaction_code_id fnb_code_ref  NOT NULL,
        quantity            NUMERIC(8,2)  NOT NULL CHECK (quantity > 0),
        unit_price          NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
        discount_amount     NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0 AND discount_amount <= round(quantity * unit_price, 2)),
        discount_reason     VARCHAR(255),
        voided              BOOLEAN       NOT NULL DEFAULT false,
        void_reason         VARCHAR(255),
        voided_by_user_id   fnb_user_ref,
        voided_at           TIMESTAMPTZ,
        added_by_user_id    fnb_user_ref  NOT NULL,
        added_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
        UNIQUE (check_id, line_seq),
        CHECK ((discount_amount = 0) = (discount_reason IS NULL)),
        CHECK (voided = (void_reason IS NOT NULL)),
        CHECK (voided = (voided_by_user_id IS NOT NULL AND voided_at IS NOT NULL))"""),
)

# Columns added after a table was first created: (table, column, definition as in TABLES).
COLUMNS = (
    ("fnb_check", "settlement_round", "SMALLINT NOT NULL DEFAULT 0 CHECK (settlement_round >= 0)"),
)

INDEXES = (
    ("fnb_menu_item_hotel_idx", "fnb_menu_item (hotel_id)"),
    ("fnb_check_hotel_date_idx", "fnb_check (hotel_id, business_date)"),
    ("fnb_check_booking_idx", "fnb_check (booking_id) WHERE booking_id IS NOT NULL"),
    ("fnb_check_line_check_idx", "fnb_check_line (check_id)"),
    ("fnb_check_correction_check_idx", "fnb_check_correction (check_id)"),
    ("fnb_check_line_item_idx", "fnb_check_line (item_id)"),
)

VIEWS = (
    ("fnb_check_line_amount", """
        SELECT l.line_id, l.check_id, l.line_seq, l.item_id, l.transaction_code_id,
               round(l.quantity * l.unit_price, 2) AS gross,
               l.discount_amount AS discount,
               round(l.quantity * l.unit_price, 2) - l.discount_amount AS net
        FROM fnb_check_line l WHERE NOT l.voided"""),
)


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return None if row is None else (list(row.values()) if isinstance(row, dict) else list(row))[0]


def _exists(cur, name):
    return _one(cur, "SELECT to_regclass(%s) IS NOT NULL", (name,))


def ensure_tables(cur, seen):
    """The objects created this call. Raises when the hotel table is missing."""
    if not seen.exists("hotel"):
        raise RuntimeError("core table hotel is missing")
    created = []
    for name, definition in DOMAINS:
        if name not in seen.types:
            cur.execute("CREATE DOMAIN " + name + " AS " + definition)
            created.append(name)
    for name, body in TABLES:
        if not seen.exists(name):
            cur.execute("CREATE TABLE " + name + " (" + body + ")")
            created.append(name)
    for table, column, definition in COLUMNS:
        if seen.exists(table) and column not in seen.columns(table):
            cur.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + definition)
            created.append(table + "." + column)
    for name, target in INDEXES:
        if not seen.exists(name):
            cur.execute("CREATE INDEX " + name + " ON " + target)
            created.append(name)
    for name, body in VIEWS:
        if not seen.exists(name):
            cur.execute("CREATE VIEW " + name + " AS " + body)
            created.append(name)
    return created


def ensure_foreign_keys(cur, seen):
    """Each FK once its target key exists: added NOT VALID, validated only when no row lacks its target.
    Returns {added, present, pending: [(fk, reason)]}; orphan rows are reported, never deleted."""
    out = {"added": [], "present": [], "pending": []}
    for table, columns, target, key in FOREIGN_KEYS:
        name = table + "_" + columns[-1] + "_fk"
        validated = seen.validated(name)
        if validated:
            out["present"].append(name)
            continue
        is_key = _one(
            cur,
            """SELECT 1 FROM pg_constraint c WHERE c.conrelid = to_regclass(%s) AND c.contype IN ('p','u')
               AND (SELECT array_agg(a.attname::text ORDER BY k.n) FROM unnest(c.conkey) WITH ORDINALITY k(attnum, n)
                    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum) = %s::text[]""",
            (target, list(key)),
        )
        if not is_key:
            out["pending"].append((name, target + " (" + ", ".join(key) + ") is not a key yet"))
            continue
        if validated is None:
            cur.execute("ALTER TABLE " + table + " ADD CONSTRAINT " + name + " FOREIGN KEY (" + ", ".join(columns) + ") REFERENCES "
                        + target + " (" + ", ".join(key) + ") NOT VALID")
        orphans = _one(
            cur,
            "SELECT COUNT(*) FROM " + table + " t WHERE t." + columns[-1] + " IS NOT NULL AND NOT EXISTS (SELECT 1 FROM " + target
            + " r WHERE " + " AND ".join("r." + k + " = t." + c for c, k in zip(columns, key)) + ")",
        )
        if orphans:
            out["pending"].append((name, str(orphans) + " " + table + " rows have no " + target + " row"))
            continue
        cur.execute("ALTER TABLE " + table + " VALIDATE CONSTRAINT " + name)
        out["added"].append(name)
    return out


def ensure(cur):
    """Area hook (schema_areas): autocommit DDL cursor."""
    seen = schema_catalog.snapshot(cur)
    return {"created": ensure_tables(cur, seen), "foreign_keys": ensure_foreign_keys(cur, seen)}


def ensure_schema():
    """Standalone: one transaction on a direct connection, DDL lock wait capped by SET LOCAL."""
    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '" + LOCK_TIMEOUT + "'")
            return ensure(cur)
    finally:
        conn.close()
