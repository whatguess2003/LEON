"""HR tables. Idempotent startup DDL on a direct autocommit connection: each step reads the catalog first and waits at most LOCK_TIMEOUT.
ensure_schema needs hotel (hotel_id). User columns are usr_ ids; ensure_user_fks adds their FKs once user_account exists."""

from booking_system import schema_catalog
from booking_system.db import connect

LOCK_TIMEOUT = "5s"
REQUIRED = {
    "hotel": ("hotel_id", "timezone"),
    "user_account": ("user_id",),
    "department": ("department_id", "hotel_id", "department_name", "department_type"),
}
USER_COLUMNS = (
    ("staff", "user_id"),
    ("roster", "created_by_user_id"),
    ("staff_attendance", "clock_in_by_user_id"),
    ("staff_attendance", "clock_out_by_user_id"),
    ("staff_attendance", "corrected_by_user_id"),
    ("leave_request", "requested_by_user_id"),
    ("leave_request", "decided_by_user_id"),
    ("leave_request", "cancelled_by_user_id"),
)

TABLES = (
    ("staff_position", """
        CREATE TABLE IF NOT EXISTS staff_position (
            position_id     VARCHAR(48)  PRIMARY KEY CHECK (left(position_id, 4) = 'pos_'),
            hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
            department_id   VARCHAR(48)  NOT NULL,
            position_name   VARCHAR(64)  NOT NULL CHECK (position_name = UPPER(btrim(position_name)) AND position_name <> ''),
            UNIQUE (hotel_id, position_name),
            UNIQUE (hotel_id, position_id),
            FOREIGN KEY (hotel_id, department_id) REFERENCES department (hotel_id, department_id))"""),
    ("staff", """
        CREATE TABLE IF NOT EXISTS staff (
            staff_id        VARCHAR(48)  PRIMARY KEY CHECK (left(staff_id, 4) = 'stf_'),
            hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
            staff_name      VARCHAR(255) NOT NULL CHECK (staff_name = UPPER(btrim(staff_name)) AND staff_name <> ''),
            department_id   VARCHAR(48)  NOT NULL,
            position_id     VARCHAR(48),
            user_id         VARCHAR(48)  CHECK (left(user_id, 4) = 'usr_'),
            employment_type VARCHAR(16)  NOT NULL CHECK (employment_type IN ('full_time', 'part_time', 'casual', 'contract')),
            status          VARCHAR(16)  NOT NULL CHECK (status IN ('active', 'inactive', 'terminated')),
            hired_on        DATE,
            terminated_on   DATE,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            UNIQUE (hotel_id, staff_id),
            UNIQUE (hotel_id, user_id),
            CHECK ((status = 'terminated') = (terminated_on IS NOT NULL)),
            FOREIGN KEY (hotel_id, department_id) REFERENCES department (hotel_id, department_id),
            FOREIGN KEY (hotel_id, position_id) REFERENCES staff_position (hotel_id, position_id))"""),
    ("staff_identity", """
        CREATE TABLE IF NOT EXISTS staff_identity (
            staff_identity_id VARCHAR(48)  PRIMARY KEY CHECK (left(staff_identity_id, 4) = 'sid_'),
            hotel_id          VARCHAR(48)  NOT NULL,
            staff_id          VARCHAR(48)  NOT NULL,
            identity_type     VARCHAR(8)   NOT NULL CHECK (identity_type IN ('phone', 'email')),
            identity_value    VARCHAR(255) NOT NULL CHECK (
                (identity_type = 'phone' AND left(identity_value, 1) = '+' AND translate(substr(identity_value, 2), '0123456789', '') = ''
                 AND char_length(identity_value) BETWEEN 8 AND 16)
                OR (identity_type = 'email' AND identity_value = lower(btrim(identity_value)) AND position('@' IN identity_value) > 1)),
            UNIQUE (hotel_id, identity_type, identity_value),
            FOREIGN KEY (hotel_id, staff_id) REFERENCES staff (hotel_id, staff_id) ON DELETE CASCADE)"""),
    ("shift", """
        CREATE TABLE IF NOT EXISTS shift (
            shift_id        VARCHAR(48)  PRIMARY KEY CHECK (left(shift_id, 4) = 'shf_'),
            hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
            shift_name      VARCHAR(32)  NOT NULL CHECK (shift_name = UPPER(btrim(shift_name)) AND shift_name <> ''),
            starts_at       TIME         NOT NULL,
            ends_at         TIME         NOT NULL CHECK (ends_at <> starts_at),
            break_minutes   SMALLINT     NOT NULL CHECK (break_minutes >= 0),
            UNIQUE (hotel_id, shift_name),
            UNIQUE (hotel_id, shift_id))"""),
    ("roster", """
        CREATE TABLE IF NOT EXISTS roster (
            roster_id       VARCHAR(48)  PRIMARY KEY CHECK (left(roster_id, 4) = 'ros_'),
            hotel_id        VARCHAR(48)  NOT NULL,
            staff_id        VARCHAR(48)  NOT NULL,
            shift_id        VARCHAR(48)  NOT NULL,
            department_id   VARCHAR(48)  NOT NULL,
            work_date       DATE         NOT NULL,
            created_by_user_id VARCHAR(48) CHECK (left(created_by_user_id, 4) = 'usr_'),
            UNIQUE (staff_id, work_date, shift_id),
            UNIQUE (hotel_id, roster_id),
            FOREIGN KEY (hotel_id, staff_id) REFERENCES staff (hotel_id, staff_id),
            FOREIGN KEY (hotel_id, shift_id) REFERENCES shift (hotel_id, shift_id),
            FOREIGN KEY (hotel_id, department_id) REFERENCES department (hotel_id, department_id))"""),
    ("staff_attendance", """
        CREATE TABLE IF NOT EXISTS staff_attendance (
            attendance_id   VARCHAR(48)  PRIMARY KEY CHECK (left(attendance_id, 4) = 'att_'),
            hotel_id        VARCHAR(48)  NOT NULL,
            staff_id        VARCHAR(48)  NOT NULL,
            roster_id       VARCHAR(48),
            clock_in        TIMESTAMPTZ  NOT NULL,
            clock_out       TIMESTAMPTZ  CHECK (clock_out > clock_in),
            clock_in_by_user_id  VARCHAR(48) CHECK (left(clock_in_by_user_id, 4) = 'usr_'),
            clock_out_by_user_id VARCHAR(48) CHECK (left(clock_out_by_user_id, 4) = 'usr_'),
            corrected_by_user_id VARCHAR(48) CHECK (left(corrected_by_user_id, 4) = 'usr_'),
            corrected_at    TIMESTAMPTZ,
            FOREIGN KEY (hotel_id, staff_id) REFERENCES staff (hotel_id, staff_id),
            FOREIGN KEY (hotel_id, roster_id) REFERENCES roster (hotel_id, roster_id))"""),
    ("leave_type", """
        CREATE TABLE IF NOT EXISTS leave_type (
            leave_type_id   VARCHAR(48)  PRIMARY KEY CHECK (left(leave_type_id, 4) = 'lvt_'),
            hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
            leave_type_name VARCHAR(32)  NOT NULL CHECK (leave_type_name = UPPER(btrim(leave_type_name)) AND leave_type_name <> ''),
            paid            BOOLEAN      NOT NULL,
            UNIQUE (hotel_id, leave_type_name),
            UNIQUE (hotel_id, leave_type_id))"""),
    ("leave_request", """
        CREATE TABLE IF NOT EXISTS leave_request (
            leave_request_id VARCHAR(48) PRIMARY KEY CHECK (left(leave_request_id, 4) = 'lvr_'),
            hotel_id        VARCHAR(48)  NOT NULL,
            staff_id        VARCHAR(48)  NOT NULL,
            leave_type_id   VARCHAR(48)  NOT NULL,
            start_date      DATE         NOT NULL,
            end_date        DATE         NOT NULL CHECK (end_date >= start_date),
            status          VARCHAR(16)  NOT NULL CHECK (status IN ('requested', 'approved', 'rejected', 'cancelled')),
            requested_by_user_id VARCHAR(48) CHECK (left(requested_by_user_id, 4) = 'usr_'),
            requested_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
            decided_by_user_id VARCHAR(48) CHECK (left(decided_by_user_id, 4) = 'usr_'),
            decided_at      TIMESTAMPTZ,
            cancelled_by_user_id VARCHAR(48) CHECK (left(cancelled_by_user_id, 4) = 'usr_'),
            cancelled_at    TIMESTAMPTZ,
            CHECK (status NOT IN ('approved', 'rejected') OR decided_at IS NOT NULL),
            CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL)),
            FOREIGN KEY (hotel_id, staff_id) REFERENCES staff (hotel_id, staff_id),
            FOREIGN KEY (hotel_id, leave_type_id) REFERENCES leave_type (hotel_id, leave_type_id))"""),
)

INDEXES = (
    ("staff_name_trgm_idx", "CREATE INDEX staff_name_trgm_idx ON staff USING gin (staff_name gin_trgm_ops)"),
    ("staff_department_idx", "CREATE INDEX staff_department_idx ON staff (hotel_id, department_id, status)"),
    ("staff_identity_staff_idx", "CREATE INDEX staff_identity_staff_idx ON staff_identity (staff_id)"),
    ("roster_hotel_date_idx", "CREATE INDEX roster_hotel_date_idx ON roster (hotel_id, work_date, department_id)"),
    ("staff_attendance_open_ux", "CREATE UNIQUE INDEX staff_attendance_open_ux ON staff_attendance (staff_id) WHERE clock_out IS NULL"),
    ("staff_attendance_hotel_in_idx", "CREATE INDEX staff_attendance_hotel_in_idx ON staff_attendance (hotel_id, clock_in)"),
    ("leave_request_staff_dates_idx", "CREATE INDEX leave_request_staff_dates_idx ON leave_request (staff_id, start_date, end_date)"),
)


def _require(seen, table):
    missing = [column for column in REQUIRED[table] if column not in seen.columns(table)]
    if missing:
        raise RuntimeError("HR schema needs " + table + "(" + ", ".join(missing) + ")")


def _ddl(fn):
    conn = connect(autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute("SET lock_timeout = '" + LOCK_TIMEOUT + "'")
        return fn(cur)
    finally:
        cur.close()
        conn.close()


def _ensure(cur, seen):
    """department is a core table (core_schema owns its DDL); HR only verifies its shape."""
    _require(seen, "hotel")
    _require(seen, "department")
    if "pg_trgm" not in seen.extensions:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for table, ddl in TABLES:
        if not seen.exists(table):
            cur.execute(ddl)
    for name, statement in INDEXES:
        if not seen.exists(name):
            cur.execute(statement)


def _user_fks(cur, seen=None):
    """{'status': 'pending'} until user_account(user_id) exists; then each FK is added NOT VALID and validated (a stray id raises)."""
    seen = seen or schema_catalog.snapshot(cur)
    if "user_id" not in seen.columns("user_account"):
        return {"status": "pending", "reason": "user_account(user_id) does not exist yet", "columns": [t + "." + c for t, c in USER_COLUMNS]}
    added = []
    for table, column in USER_COLUMNS:
        name = table + "_" + column + "_fk"
        state = seen.validated(name)
        if state:
            continue
        if state is None:
            cur.execute("ALTER TABLE " + table + " ADD CONSTRAINT " + name + " FOREIGN KEY (" + column + ") REFERENCES user_account (user_id) NOT VALID")
        cur.execute("ALTER TABLE " + table + " VALIDATE CONSTRAINT " + name)
        added.append(name)
    return {"status": "ok", "added": added}


def ensure(cur):
    """Area hook (schema_areas): the core's autocommit DDL cursor. Tables and indexes, then the user FKs; returns their status."""
    cur.execute("SET lock_timeout = '" + LOCK_TIMEOUT + "'")
    seen = schema_catalog.snapshot(cur)
    _ensure(cur, seen)
    return _user_fks(cur, seen)


def ensure_schema():
    return _ddl(ensure)


def ensure_user_fks():
    return _ddl(_user_fks)


def drop_schema():
    """Test data only. department is core-owned and stays."""
    def run(cur):
        for table, _ in reversed(TABLES):
            cur.execute("DROP TABLE IF EXISTS " + table)
    _ddl(run)
