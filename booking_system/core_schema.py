"""Core PMS tables keyed by '<prefix>_<uuid4>' ids. Names, room numbers, phones, emails and OTA references are unique,
indexed attributes, never keys. Run on a direct autocommit connection: each DDL waits at most DDL_LOCK_TIMEOUT, and a step
already in place only reads the catalog. Area schema hooks (schema_<area>.ensure) run after the core."""

from booking_system.schema_tools import drop_tables

DDL_LOCK_TIMEOUT = "5s"
E164 = "left({c},1) = '+' AND translate(substr({c},2), '0123456789', '') = '' AND char_length({c}) BETWEEN 8 AND 16"


def _id(column, prefix):
    return column + " VARCHAR(48) PRIMARY KEY CHECK (left(" + column + ", " + str(len(prefix) + 1) + ") = '" + prefix + "_')"


def _upper(column):
    return "CHECK (" + column + " = UPPER(btrim(" + column + ")) AND " + column + " <> '')"


# Superseded by the tables below. Dropped only while they still have the old hotel_name shape.
LEGACY = (
    "daily_pricing", "room_inventory_nightly", "room_number_assignments", "room_stay", "booking_stays", "booking_guest_counts",
    "payment_ledger", "payment_tracking", "room_bookings", "guest_profile", "room_readiness_status", "cleaning_status",
    "housekeeping_tasks", "maintenance_requests", "maintenance_schedules", "staff_duty_log", "hotel_room_code_mapping",
    "room_allotments", "booking_modification_log",
)
# Superseded tables nothing reads, dropped whatever their shape.
UNREAD = ("payment_tracking", "housekeeping_tasks")

TABLES = (
    # ---------- people and access ----------
    ("user_account", _id("user_id", "usr") + """,
        user_name     VARCHAR(255) NOT NULL """ + _upper("user_name") + """,
        status        VARCHAR(16)  NOT NULL CHECK (status IN ('active','locked')),
        last_hotel_id VARCHAR(48)  REFERENCES hotel (hotel_id),
        created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()"""),
    ("user_identity", _id("identity_id", "uid") + """,
        user_id        VARCHAR(48)  NOT NULL REFERENCES user_account (user_id) ON DELETE CASCADE,
        identity_type  VARCHAR(16)  NOT NULL CHECK (identity_type IN ('phone','email','username')),
        identity_value VARCHAR(255) NOT NULL,
        created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
        UNIQUE (identity_type, identity_value),
        CHECK (identity_type <> 'phone' OR (""" + E164.format(c="identity_value") + """)),
        CHECK (identity_type <> 'email' OR (identity_value = lower(btrim(identity_value)) AND position('@' in identity_value) > 1)),
        CHECK (identity_type <> 'username' OR (identity_value = lower(btrim(identity_value)) AND identity_value <> ''
               AND position('@' in identity_value) = 0 AND left(identity_value, 1) <> '+'))"""),
    ("module", """module_name VARCHAR(32) PRIMARY KEY CHECK (module_name = lower(module_name) AND module_name <> '')"""),
    ("access_level", """access_level VARCHAR(8) PRIMARY KEY, level_rank SMALLINT NOT NULL UNIQUE"""),
    ("access_role", _id("role_id", "rol") + """,
        hotel_id                VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
        role_name               VARCHAR(64) NOT NULL """ + _upper("role_name") + """,
        daily_investigation_cap SMALLINT    NOT NULL CHECK (daily_investigation_cap >= 0),
        UNIQUE (hotel_id, role_name),
        UNIQUE (hotel_id, role_id)"""),
    ("role_module", """
        role_id      VARCHAR(48) NOT NULL REFERENCES access_role (role_id) ON DELETE CASCADE,
        module_name  VARCHAR(32) NOT NULL REFERENCES module (module_name),
        access_level VARCHAR(8)  NOT NULL REFERENCES access_level (access_level),
        PRIMARY KEY (role_id, module_name)"""),
    ("hotel_user", """
        hotel_id                         VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
        user_id                          VARCHAR(48) NOT NULL REFERENCES user_account (user_id),
        role_id                          VARCHAR(48) NOT NULL,
        job_title                        VARCHAR(64),
        status                           VARCHAR(16) NOT NULL CHECK (status IN ('invited','active','suspended','removed')),
        access_version                   INTEGER     NOT NULL DEFAULT 1,
        daily_investigation_cap_override SMALLINT    CHECK (daily_investigation_cap_override >= 0),
        invited_by_user_id               VARCHAR(48) REFERENCES user_account (user_id),
        invited_at                       TIMESTAMPTZ NOT NULL DEFAULT now(),
        activated_at                     TIMESTAMPTZ,
        PRIMARY KEY (hotel_id, user_id),
        FOREIGN KEY (hotel_id, role_id) REFERENCES access_role (hotel_id, role_id),
        CHECK (status <> 'active' OR activated_at IS NOT NULL)"""),
    ("hotel_user_module", """
        hotel_id           VARCHAR(48) NOT NULL,
        user_id            VARCHAR(48) NOT NULL,
        module_name        VARCHAR(32) NOT NULL REFERENCES module (module_name),
        access_level       VARCHAR(8)  NOT NULL REFERENCES access_level (access_level),
        granted_by_user_id VARCHAR(48) NOT NULL REFERENCES user_account (user_id),
        granted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (hotel_id, user_id, module_name),
        FOREIGN KEY (hotel_id, user_id) REFERENCES hotel_user (hotel_id, user_id) ON DELETE CASCADE"""),
    ("access_point", """
        access_kind       VARCHAR(16)  NOT NULL CHECK (access_kind IN ('api','desk_action','desk_read','tool','table')),
        access_point_name VARCHAR(255) NOT NULL,
        module_name       VARCHAR(32)  NOT NULL REFERENCES module (module_name),
        access_level      VARCHAR(8)   NOT NULL REFERENCES access_level (access_level) CHECK (access_level <> 'none'),
        PRIMARY KEY (access_kind, access_point_name, module_name)"""),
    ("public_route", """route_name VARCHAR(255) PRIMARY KEY"""),
    # ---------- departments (shared by HR, Finance, F&B; those modules only verify it) ----------
    ("department", _id("department_id", "dep") + """,
        hotel_id        VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
        department_name VARCHAR(64) NOT NULL """ + _upper("department_name") + """,
        department_type VARCHAR(16) NOT NULL CHECK (department_type IN ('operated','undistributed','fixed')),
        UNIQUE (hotel_id, department_name),
        UNIQUE (hotel_id, department_id)"""),
    # ---------- guests ----------
    ("guest", _id("guest_id", "gst") + """,
        hotel_id           VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        guest_name         VARCHAR(255) NOT NULL """ + _upper("guest_name") + """,
        nationality        VARCHAR(64),
        vip                BOOLEAN      NOT NULL DEFAULT FALSE,
        status             VARCHAR(16)  NOT NULL CHECK (status IN ('active','inactive')),
        created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
        created_by_user_id VARCHAR(48)  REFERENCES user_account (user_id),
        UNIQUE (hotel_id, guest_id)"""),
    ("guest_identity", _id("identity_id", "gid") + """,
        hotel_id       VARCHAR(48)  NOT NULL,
        guest_id       VARCHAR(48)  NOT NULL,
        identity_type  VARCHAR(16)  NOT NULL CHECK (identity_type IN ('phone','email')),
        identity_value VARCHAR(255) NOT NULL,
        UNIQUE (hotel_id, identity_type, identity_value),
        FOREIGN KEY (hotel_id, guest_id) REFERENCES guest (hotel_id, guest_id) ON DELETE CASCADE,
        CHECK (identity_type <> 'phone' OR (""" + E164.format(c="identity_value") + """)),
        CHECK (identity_type <> 'email' OR (identity_value = lower(btrim(identity_value)) AND position('@' in identity_value) > 1))"""),
    # ---------- rooms ----------
    ("room_type", _id("room_type_id", "rt") + """,
        hotel_id       VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        room_type_name VARCHAR(255) NOT NULL """ + _upper("room_type_name") + """,
        status         VARCHAR(16)  NOT NULL CHECK (status IN ('active','inactive')),
        created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
        UNIQUE (hotel_id, room_type_name),
        UNIQUE (hotel_id, room_type_id)"""),
    ("room", _id("room_id", "rm") + """,
        hotel_id     VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
        room_type_id VARCHAR(48) NOT NULL,
        room_number  VARCHAR(32) NOT NULL """ + _upper("room_number") + """,
        status       VARCHAR(16) NOT NULL CHECK (status IN ('active','inactive')),
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (hotel_id, room_number),
        UNIQUE (hotel_id, room_id),
        FOREIGN KEY (hotel_id, room_type_id) REFERENCES room_type (hotel_id, room_type_id)"""),
    ("room_inventory_nightly", """
        hotel_id           VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
        room_type_id       VARCHAR(48) NOT NULL,
        stay_date          DATE        NOT NULL,
        total_inventory    INTEGER     NOT NULL CHECK (total_inventory >= 0),
        blocked_quantity   INTEGER     NOT NULL CHECK (blocked_quantity >= 0),
        stop_sell          BOOLEAN     NOT NULL DEFAULT FALSE,
        min_stay           SMALLINT    CHECK (min_stay > 0),
        closed_to_arrival  BOOLEAN     NOT NULL DEFAULT FALSE,
        updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_by_user_id VARCHAR(48) REFERENCES user_account (user_id),
        PRIMARY KEY (room_type_id, stay_date),
        UNIQUE (hotel_id, room_type_id, stay_date),
        FOREIGN KEY (hotel_id, room_type_id) REFERENCES room_type (hotel_id, room_type_id) ON DELETE CASCADE"""),
    ("daily_pricing", """
        hotel_id           VARCHAR(48)   NOT NULL,
        room_type_id       VARCHAR(48)   NOT NULL,
        stay_date          DATE          NOT NULL,
        base_price         NUMERIC(12,2) NOT NULL CHECK (base_price >= 0),
        saved_price        NUMERIC(12,2) CHECK (saved_price >= 0),
        updated_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
        updated_by_user_id VARCHAR(48)   REFERENCES user_account (user_id),
        PRIMARY KEY (room_type_id, stay_date),
        FOREIGN KEY (hotel_id, room_type_id, stay_date) REFERENCES room_inventory_nightly (hotel_id, room_type_id, stay_date) ON DELETE CASCADE"""),
    ("room_allotments", """
        hotel_id           VARCHAR(48) NOT NULL,
        room_type_id       VARCHAR(48) NOT NULL,
        channel            VARCHAR(64) NOT NULL """ + _upper("channel") + """,
        stay_date          DATE        NOT NULL,
        rooms_allotted     INTEGER     NOT NULL CHECK (rooms_allotted >= 0),
        release_date       DATE,
        released_at        TIMESTAMPTZ,
        updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_by_user_id VARCHAR(48) REFERENCES user_account (user_id),
        PRIMARY KEY (room_type_id, channel, stay_date),
        FOREIGN KEY (hotel_id, room_type_id) REFERENCES room_type (hotel_id, room_type_id) ON DELETE CASCADE"""),
    ("hotel_room_code_mapping", """
        hotel_id          VARCHAR(48) NOT NULL,
        channel_room_code VARCHAR(64) NOT NULL CHECK (channel_room_code <> ''),
        room_type_id      VARCHAR(48) NOT NULL,
        PRIMARY KEY (hotel_id, channel_room_code),
        FOREIGN KEY (hotel_id, room_type_id) REFERENCES room_type (hotel_id, room_type_id) ON DELETE CASCADE"""),
    # ---------- bookings ----------
    ("booking", _id("booking_id", "bkg") + """,
        hotel_id            VARCHAR(48)   NOT NULL REFERENCES hotel (hotel_id),
        guest_id            VARCHAR(48)   NOT NULL,
        booking_status      VARCHAR(16)   NOT NULL CHECK (booking_status IN ('confirmed','checked_in','checked_out','cancelled','no_show')),
        channel             VARCHAR(64)   NOT NULL """ + _upper("channel") + """,
        channel_booking_ref VARCHAR(128)  CHECK (channel_booking_ref <> ''),
        group_name          VARCHAR(255),
        rate_plan           VARCHAR(64),
        adults              SMALLINT      CHECK (adults > 0),
        children            SMALLINT      CHECK (children >= 0),
        currency            CHAR(3)       CHECK (currency = UPPER(currency)),
        gross_amount        NUMERIC(12,2) CHECK (gross_amount >= 0),
        penalty_amount      NUMERIC(12,2) CHECK (penalty_amount >= 0),
        late_fee_amount     NUMERIC(12,2) CHECK (late_fee_amount >= 0),
        commission_amount   NUMERIC(12,2) CHECK (commission_amount >= 0),
        commission_rate     NUMERIC(5,2)  CHECK (commission_rate >= 0 AND commission_rate <= 100),
        booked_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
        created_by_user_id  VARCHAR(48)   REFERENCES user_account (user_id),
        updated_at          TIMESTAMPTZ,
        updated_by_user_id  VARCHAR(48)   REFERENCES user_account (user_id),
        cancelled_at        TIMESTAMPTZ,
        no_show_at          TIMESTAMPTZ,
        UNIQUE (hotel_id, booking_id),
        UNIQUE (hotel_id, channel, channel_booking_ref),
        FOREIGN KEY (hotel_id, guest_id) REFERENCES guest (hotel_id, guest_id),
        CHECK (gross_amount IS NULL OR currency IS NOT NULL),
        CHECK (commission_amount IS NULL OR currency IS NOT NULL),
        CHECK ((children IS NULL) OR (adults IS NOT NULL)),
        CHECK ((booking_status = 'cancelled') = (cancelled_at IS NOT NULL))"""),
    ("booking_room_night", _id("booking_night_id", "bkn") + """,
        hotel_id            VARCHAR(48)   NOT NULL,
        booking_id          VARCHAR(48)   NOT NULL,
        stay_date           DATE          NOT NULL,
        room_seq            SMALLINT      NOT NULL CHECK (room_seq > 0),
        room_type_id        VARCHAR(48)   NOT NULL,
        room_id             VARCHAR(48),
        night_status        VARCHAR(16)   NOT NULL CHECK (night_status IN ('booked','checked_in','checked_out','cancelled','released','no_show')),
        sell_rate           NUMERIC(12,2) CHECK (sell_rate >= 0),
        currency            CHAR(3),
        rate_source         VARCHAR(16)   CHECK (rate_source IN ('channel','desk','override','rate_plan')),
        assigned_at         TIMESTAMPTZ,
        assigned_by_user_id VARCHAR(48)   REFERENCES user_account (user_id),
        checked_in_at       TIMESTAMPTZ,
        checked_out_at      TIMESTAMPTZ,
        UNIQUE (booking_id, stay_date, room_seq),
        FOREIGN KEY (hotel_id, booking_id) REFERENCES booking (hotel_id, booking_id) ON DELETE CASCADE,
        FOREIGN KEY (hotel_id, room_type_id) REFERENCES room_type (hotel_id, room_type_id),
        FOREIGN KEY (hotel_id, room_id) REFERENCES room (hotel_id, room_id),
        CHECK ((sell_rate IS NULL) = (rate_source IS NULL)),
        CHECK (sell_rate IS NULL OR currency IS NOT NULL),
        CHECK ((room_id IS NULL) = (assigned_at IS NULL)),
        CHECK (night_status NOT IN ('checked_in','checked_out') OR (room_id IS NOT NULL AND checked_in_at IS NOT NULL)),
        CHECK (night_status <> 'checked_out' OR checked_out_at IS NOT NULL)"""),
    ("room_block", _id("room_block_id", "rbk") + """,
        hotel_id           VARCHAR(48) NOT NULL,
        room_id            VARCHAR(48) NOT NULL,
        block_type         VARCHAR(16) NOT NULL CHECK (block_type IN ('out_of_order','out_of_service')),
        start_date         DATE        NOT NULL,
        end_date           DATE        NOT NULL,
        reason             TEXT        NOT NULL CHECK (btrim(reason) <> ''),
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by_user_id VARCHAR(48) REFERENCES user_account (user_id),
        FOREIGN KEY (hotel_id, room_id) REFERENCES room (hotel_id, room_id) ON DELETE CASCADE,
        CHECK (end_date >= start_date)"""),
    ("booking_modification_log", _id("modification_id", "bkm") + """,
        hotel_id            VARCHAR(48) NOT NULL,
        booking_id          VARCHAR(48) NOT NULL,
        modified_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        modified_by_user_id VARCHAR(48) REFERENCES user_account (user_id),
        field_changed       VARCHAR(64) NOT NULL,
        old_value           TEXT,
        new_value           TEXT,
        change_reason       TEXT,
        FOREIGN KEY (hotel_id, booking_id) REFERENCES booking (hotel_id, booking_id) ON DELETE CASCADE"""),
    # ---------- money (the desk ledger; folios and transaction codes belong to finance) ----------
    ("payment", _id("payment_id", "pay") + """,
        hotel_id            VARCHAR(48)   NOT NULL,
        guest_id            VARCHAR(48)   NOT NULL,
        booking_id          VARCHAR(48),
        payment_kind        VARCHAR(8)    NOT NULL CHECK (payment_kind IN ('payment','refund')),
        amount              NUMERIC(12,2) NOT NULL CHECK (amount > 0),
        currency            CHAR(3)       NOT NULL CHECK (currency = UPPER(currency)),
        payment_method      VARCHAR(32)   NOT NULL """ + _upper("payment_method") + """,
        payment_status      VARCHAR(16)   NOT NULL CHECK (payment_status IN ('received','void')),
        reference           VARCHAR(128),
        paid_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
        recorded_by_user_id VARCHAR(48)   REFERENCES user_account (user_id),
        FOREIGN KEY (hotel_id, guest_id) REFERENCES guest (hotel_id, guest_id),
        FOREIGN KEY (hotel_id, booking_id) REFERENCES booking (hotel_id, booking_id)"""),
    # ---------- operations ----------
    ("room_readiness_status", """
        room_id            VARCHAR(48) PRIMARY KEY,
        hotel_id           VARCHAR(48) NOT NULL,
        readiness_status   VARCHAR(16) NOT NULL CHECK (readiness_status IN ('clean','dirty','cleaning','inspected')),
        changed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        changed_by_user_id VARCHAR(48) REFERENCES user_account (user_id),
        clean_since        TIMESTAMPTZ,
        auto_dirty_days    SMALLINT    NOT NULL CHECK (auto_dirty_days > 0),
        FOREIGN KEY (hotel_id, room_id) REFERENCES room (hotel_id, room_id) ON DELETE CASCADE"""),
    ("cleaning_task", _id("cleaning_task_id", "cln") + """,
        hotel_id           VARCHAR(48) NOT NULL,
        room_id            VARCHAR(48) NOT NULL,
        task_status        VARCHAR(16) NOT NULL CHECK (task_status IN ('pending','assigned','in_progress','completed','inspected','cancelled')),
        task_type          VARCHAR(32) NOT NULL,
        priority           VARCHAR(16) NOT NULL CHECK (priority IN ('low','normal','high','urgent')),
        assigned_staff_id  VARCHAR(48),
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by_user_id VARCHAR(48) REFERENCES user_account (user_id),
        assigned_at        TIMESTAMPTZ,
        started_at         TIMESTAMPTZ,
        completed_at       TIMESTAMPTZ,
        inspected_at       TIMESTAMPTZ,
        inspected_by_user_id VARCHAR(48) REFERENCES user_account (user_id),
        FOREIGN KEY (hotel_id, room_id) REFERENCES room (hotel_id, room_id) ON DELETE CASCADE"""),
    ("maintenance_schedule", _id("schedule_id", "mts") + """,
        hotel_id          VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        room_id           VARCHAR(48),
        area              VARCHAR(128),
        issue             VARCHAR(255) NOT NULL,
        urgency           VARCHAR(16)  NOT NULL CHECK (urgency IN ('low','normal','high','urgent')),
        interval_days     INTEGER      NOT NULL CHECK (interval_days > 0),
        start_date        DATE         NOT NULL,
        last_service_date DATE,
        next_due_date     DATE         NOT NULL,
        auto_out_of_order BOOLEAN      NOT NULL DEFAULT FALSE,
        active            BOOLEAN      NOT NULL DEFAULT TRUE,
        UNIQUE (hotel_id, schedule_id),
        FOREIGN KEY (hotel_id, room_id) REFERENCES room (hotel_id, room_id) ON DELETE CASCADE,
        CHECK ((room_id IS NULL) <> (area IS NULL))"""),
    ("maintenance_request", _id("request_id", "mtr") + """,
        hotel_id               VARCHAR(48)   NOT NULL REFERENCES hotel (hotel_id),
        room_id                VARCHAR(48),
        area                   VARCHAR(128),
        issue                  VARCHAR(255)  NOT NULL,
        urgency                VARCHAR(16)   NOT NULL CHECK (urgency IN ('low','normal','high','urgent')),
        request_status         VARCHAR(16)   NOT NULL CHECK (request_status IN ('reported','assigned','in_progress','completed','cancelled')),
        schedule_id            VARCHAR(48),
        due_date               DATE,
        reported_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
        reported_by_user_id    VARCHAR(48)   REFERENCES user_account (user_id),
        assigned_staff_id      VARCHAR(48),
        assigned_at            TIMESTAMPTZ,
        started_at             TIMESTAMPTZ,
        completed_at           TIMESTAMPTZ,
        actual_cost            NUMERIC(12,2) CHECK (actual_cost >= 0),
        FOREIGN KEY (hotel_id, room_id) REFERENCES room (hotel_id, room_id) ON DELETE CASCADE,
        FOREIGN KEY (hotel_id, schedule_id) REFERENCES maintenance_schedule (hotel_id, schedule_id),
        CHECK ((room_id IS NULL) <> (area IS NULL)),
        CHECK ((schedule_id IS NULL) = (due_date IS NULL))"""),
    # ---------- durable background work ----------
    ("job_queue", _id("job_id", "job") + """,
        job_kind     VARCHAR(64)  NOT NULL,
        dedupe_key   VARCHAR(160) UNIQUE,
        hotel_id     VARCHAR(48)  REFERENCES hotel (hotel_id),
        payload      JSONB        NOT NULL,
        job_status   VARCHAR(16)  NOT NULL CHECK (job_status IN ('queued','running','done','failed')),
        attempts     SMALLINT     NOT NULL DEFAULT 0,
        run_after    TIMESTAMPTZ  NOT NULL DEFAULT now(),
        lease_until  TIMESTAMPTZ,
        last_error   TEXT,
        created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
        updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()"""),
)

INDEXES = (
    ("user_identity_user_idx", "user_identity (user_id)"),
    ("hotel_user_user_idx", "hotel_user (user_id)"),
    ("guest_hotel_name_idx", "guest (hotel_id, guest_name)"),
    ("guest_identity_guest_idx", "guest_identity (guest_id)"),
    ("room_type_idx", "room (hotel_id, room_type_id)"),
    ("inventory_hotel_date_idx", "room_inventory_nightly (hotel_id, stay_date)"),
    ("pricing_hotel_date_idx", "daily_pricing (hotel_id, stay_date)"),
    ("booking_hotel_status_idx", "booking (hotel_id, booking_status)"),
    ("booking_guest_idx", "booking (hotel_id, guest_id)"),
    ("booking_booked_idx", "booking (hotel_id, booked_at)"),
    ("brn_hotel_date_idx", "booking_room_night (hotel_id, stay_date)"),
    ("brn_type_date_idx", "booking_room_night (room_type_id, stay_date)"),
    ("brn_booking_idx", "booking_room_night (booking_id)"),
    ("room_block_room_idx", "room_block (room_id, start_date, end_date)"),
    ("payment_booking_idx", "payment (hotel_id, booking_id)"),
    ("cleaning_task_room_idx", "cleaning_task (hotel_id, room_id, task_status)"),
    ("maintenance_request_hotel_idx", "maintenance_request (hotel_id, request_status)"),
    ("maintenance_schedule_due_idx", "maintenance_schedule (hotel_id, next_due_date) WHERE active"),
    ("job_queue_ready_idx", "job_queue (job_status, run_after)"),
)
UNIQUE_INDEXES = (
    # one booked or in-house night per room and date
    ("brn_room_night_uq", "booking_room_night (room_id, stay_date) WHERE room_id IS NOT NULL AND night_status IN ('booked','checked_in')"),
    ("maintenance_request_due_uq", "maintenance_request (schedule_id, due_date) WHERE schedule_id IS NOT NULL"),
)

LINKS = (
    ("cleaning_task_staff_fk", "cleaning_task", "assigned_staff_id", "staff (staff_id)"),
    ("maintenance_request_staff_fk", "maintenance_request", "assigned_staff_id", "staff (staff_id)"),
)

MODULES = ("front_desk", "reservations", "housekeeping", "maintenance", "reporting", "hr", "fnb", "finance", "admin")
LEVELS = (("none", 0), ("view", 1), ("operate", 2), ("manage", 3))

VIEWS = (
    ("user_module_access", """
        SELECT x.hotel_id, x.user_id, x.module_name, x.access_level, l.level_rank
        FROM (SELECT u.hotel_id, u.user_id, rm.module_name, COALESCE(o.access_level, rm.access_level) AS access_level
              FROM hotel_user u
              JOIN role_module rm ON rm.role_id = u.role_id
              LEFT JOIN hotel_user_module o ON o.hotel_id = u.hotel_id AND o.user_id = u.user_id AND o.module_name = rm.module_name
              WHERE u.status = 'active') x
        JOIN access_level l ON l.access_level = x.access_level"""),
    ("booking_summary", """
        SELECT b.hotel_id, b.booking_id, b.guest_id, b.booking_status, b.channel, b.channel_booking_ref, b.group_name,
               b.adults, b.children, b.currency, b.gross_amount, b.commission_amount, b.booked_at,
               MIN(n.stay_date) AS check_in_date, MAX(n.stay_date) + 1 AS check_out_date,
               COUNT(DISTINCT n.stay_date) AS nights, MAX(n.room_seq) AS rooms_booked
        FROM booking b JOIN booking_room_night n ON n.booking_id = b.booking_id
        GROUP BY b.booking_id"""),
    ("booking_balance", """
        SELECT b.hotel_id, b.booking_id, b.currency, b.gross_amount,
               COALESCE(SUM(p.amount) FILTER (WHERE p.payment_status = 'received' AND p.payment_kind = 'payment'), 0)
             - COALESCE(SUM(p.amount) FILTER (WHERE p.payment_status = 'received' AND p.payment_kind = 'refund'), 0) AS amount_paid,
               (CASE WHEN b.penalty_amount IS NOT NULL THEN b.penalty_amount
                     ELSE b.gross_amount + COALESCE(b.late_fee_amount, 0) END)
             - COALESCE(SUM(p.amount) FILTER (WHERE p.payment_status = 'received' AND p.payment_kind = 'payment'), 0)
             + COALESCE(SUM(p.amount) FILTER (WHERE p.payment_status = 'received' AND p.payment_kind = 'refund'), 0) AS balance_due
        FROM booking b LEFT JOIN payment p ON p.booking_id = b.booking_id
        GROUP BY b.booking_id"""),
    ("guest_account", """
        SELECT g.hotel_id, g.guest_id,
               COALESCE(SUM(p.amount) FILTER (WHERE p.payment_status = 'received' AND p.payment_kind = 'payment'), 0)
             - COALESCE(SUM(p.amount) FILTER (WHERE p.payment_status = 'received' AND p.payment_kind = 'refund'), 0) AS amount_held
        FROM guest g LEFT JOIN payment p ON p.hotel_id = g.hotel_id AND p.guest_id = g.guest_id
        GROUP BY g.hotel_id, g.guest_id"""),
)

# Columns added after a table first shipped. CREATE TABLE above already includes them for a new database.
COLUMNS = (
    ("booking", "penalty_amount", "NUMERIC(12,2) CHECK (penalty_amount >= 0)"),
    ("booking", "late_fee_amount", "NUMERIC(12,2) CHECK (late_fee_amount >= 0)"),
    ("room_allotments", "released_at", "TIMESTAMPTZ"),
    ("payment", "guest_id", "VARCHAR(48)"),
)

FUNCTIONS = (
    ("bump_access_version", """() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'hotel_user' THEN
                NEW.access_version := OLD.access_version + 1;
                RETURN NEW;
            ELSIF TG_TABLE_NAME = 'hotel_user_module' THEN
                UPDATE hotel_user SET access_version = access_version + 1
                WHERE (hotel_id, user_id) IN ((COALESCE(NEW.hotel_id, OLD.hotel_id), COALESCE(NEW.user_id, OLD.user_id)));
            ELSE
                UPDATE hotel_user SET access_version = access_version + 1 WHERE role_id = COALESCE(NEW.role_id, OLD.role_id);
            END IF;
            RETURN NULL;
        END $$"""),
    ("keep_general_manager", """() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE hotel VARCHAR(48) := COALESCE(OLD.hotel_id, NEW.hotel_id);
        BEGIN
            IF EXISTS (SELECT 1 FROM hotel h WHERE h.hotel_id = hotel)
               AND NOT EXISTS (SELECT 1 FROM hotel_user u JOIN access_role r ON r.role_id = u.role_id
                               WHERE u.hotel_id = hotel AND u.status = 'active' AND r.role_name = 'GENERAL_MANAGER') THEN
                RAISE EXCEPTION 'hotel % must keep an active GENERAL_MANAGER', hotel;
            END IF;
            RETURN NULL;
        END $$"""),
)
TRIGGERS = (
    ("hotel_user_access_version", "BEFORE UPDATE OF role_id, status, daily_investigation_cap_override ON hotel_user", "bump_access_version", ""),
    ("hotel_user_module_access_version", "AFTER INSERT OR UPDATE OR DELETE ON hotel_user_module", "bump_access_version", ""),
    ("role_module_access_version", "AFTER UPDATE ON role_module", "bump_access_version", ""),
    ("hotel_user_keep_gm", "AFTER UPDATE OR DELETE ON hotel_user", "keep_general_manager", "CONSTRAINT"),
)


def catalog(cur):
    """One read of what exists: relations, functions, triggers, constraints (name -> validated), legacy-shaped tables."""
    cur.execute("SELECT relname FROM pg_class WHERE relnamespace = current_schema()::regnamespace")
    relations = {row[0] for row in cur.fetchall()}
    cur.execute("SELECT proname FROM pg_proc WHERE pronamespace = current_schema()::regnamespace")
    functions = {row[0] for row in cur.fetchall()}
    cur.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
    triggers = {row[0] for row in cur.fetchall()}
    cur.execute("SELECT conname, convalidated FROM pg_constraint WHERE connamespace = current_schema()::regnamespace")
    constraints = dict(cur.fetchall())
    cur.execute("SELECT table_name FROM information_schema.columns WHERE table_schema = current_schema() AND column_name = 'hotel_name'")
    named = {row[0] for row in cur.fetchall()}
    return {"relations": relations, "functions": functions, "triggers": triggers, "constraints": constraints, "hotel_name_tables": named}


def _link(cur, seen):
    """FKs to tables another module creates (HR staff). Added once the target exists: NOT VALID, then VALIDATE."""
    for name, table, column, target in LINKS:
        if seen["constraints"].get(name) or target.split(" ")[0] not in seen["relations"]:
            continue
        if name not in seen["constraints"]:
            cur.execute("ALTER TABLE " + table + " ADD CONSTRAINT " + name + " FOREIGN KEY (" + column + ") REFERENCES " + target + " NOT VALID")
        cur.execute("ALTER TABLE " + table + " VALIDATE CONSTRAINT " + name)


def _seed(cur):
    cur.execute("INSERT INTO module (module_name) SELECT unnest(%s::text[]) ON CONFLICT DO NOTHING", (list(MODULES),))
    cur.execute("INSERT INTO access_level (access_level, level_rank) SELECT unnest(%s::text[]), unnest(%s::int[]) ON CONFLICT DO NOTHING",
                ([name for name, _rank in LEVELS], [rank for _name, rank in LEVELS]))


def _guest_payments(cur):
    """A payment belongs to the guest. The booking is optional and does not follow booking status."""
    cur.execute(
        """SELECT column_name, is_nullable FROM information_schema.columns
           WHERE table_schema = current_schema() AND table_name = 'payment' AND column_name IN ('guest_id', 'booking_id')"""
    )
    columns = {row[0]: row[1] for row in cur.fetchall()}
    if "guest_id" not in columns:
        return
    cur.execute(
        """UPDATE payment p SET guest_id = b.guest_id FROM booking b
           WHERE p.booking_id = b.booking_id AND p.guest_id IS NULL"""
    )
    if columns.get("booking_id") == "NO":
        cur.execute("ALTER TABLE payment ALTER COLUMN booking_id DROP NOT NULL")
    cur.execute("SELECT 1 FROM payment WHERE guest_id IS NULL")
    if cur.fetchone() is None and columns.get("guest_id") == "YES":
        cur.execute("ALTER TABLE payment ALTER COLUMN guest_id SET NOT NULL")
    cur.execute("SELECT 1 FROM pg_constraint WHERE conname = 'payment_guest_fk'")
    if cur.fetchone() is None:
        cur.execute(
            """ALTER TABLE payment ADD CONSTRAINT payment_guest_fk
               FOREIGN KEY (hotel_id, guest_id) REFERENCES guest (hotel_id, guest_id) NOT VALID"""
        )
        cur.execute("ALTER TABLE payment VALIDATE CONSTRAINT payment_guest_fk")
    cur.execute("CREATE INDEX IF NOT EXISTS payment_guest_idx ON payment (hotel_id, guest_id)")


def ensure_schema(cur):
    """Autocommit cursor on a direct connection."""
    cur.execute("SET lock_timeout = '" + DDL_LOCK_TIMEOUT + "'")
    seen = catalog(cur)
    for table in LEGACY:
        if table in seen["hotel_name_tables"]:
            cur.execute("DROP TABLE IF EXISTS " + table + " CASCADE")
            seen["relations"].discard(table)
    drop_tables(cur, seen, UNREAD)
    for name, body in TABLES:
        if name not in seen["relations"]:
            cur.execute("CREATE TABLE " + name + " (" + body + ")")
    for name, target in INDEXES:
        if name not in seen["relations"]:
            cur.execute("CREATE INDEX " + name + " ON " + target)
    for name, target in UNIQUE_INDEXES:
        if name not in seen["relations"]:
            cur.execute("CREATE UNIQUE INDEX " + name + " ON " + target)
    for name, body in FUNCTIONS:
        if name not in seen["functions"]:
            cur.execute("CREATE FUNCTION " + name + body)
    for name, timing, function, kind in TRIGGERS:
        if name not in seen["triggers"]:
            deferred = " DEFERRABLE INITIALLY DEFERRED" if kind else ""
            cur.execute("CREATE " + (kind + " " if kind else "") + "TRIGGER " + name + " " + timing + deferred
                        + " FOR EACH ROW EXECUTE FUNCTION " + function + "()")
    from booking_system.content_store import add_column
    for table, column, definition in COLUMNS:
        add_column(cur, table, column, definition)
    for name, body in VIEWS:
        if name in ("booking_balance", "guest_account"):
            cur.execute("CREATE OR REPLACE VIEW " + name + " AS " + body)
        elif name not in seen["relations"]:
            cur.execute("CREATE VIEW " + name + " AS " + body)
    _guest_payments(cur)
    _seed(cur)
    _link(cur, seen)
    from booking_system import schema_areas
    schema_areas.ensure(cur, seen)


def run():
    from booking_system.db import connect
    from booking_system import hotel_registry
    conn = connect(autocommit=True)
    try:
        cur = conn.cursor()
        hotel_registry.ensure_schema(cur)
        ensure_schema(cur)
    finally:
        conn.close()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    run()
    print("core schema ok")
