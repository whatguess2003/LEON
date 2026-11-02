"""Operating-settings DDL (core area hook). setting_catalog is the question catalog: a key already stored there keeps its
question, options and status, and a key that exists only in the table stays active. catalog.CATALOG inserts a key only when the
table has no row for it. hotel_setting keeps every answer as its own row with an effective_from date: rows are never updated, so
history is kept and the value in effect on a day is the latest effective_from on or before it (latest set_at breaks a tie).

SQL functions (also used by recipes):
  hotel_setting_at(hotel_id, key, day)        -> jsonb value in effect, or NULL
  hotel_setting_required(hotel_id, key, day)  -> jsonb value, or raises 22023 naming the setting and its question
  hotel_setting_accept(hotel_id, key, value)  -> canonical jsonb value, or raises 22023 (BEFORE INSERT applies it)
  hotel_module_state(hotel_id, module, day)   -> {"governed", "enabled", "missing": [{setting_key, question}]}
  hotel_local_date(hotel_id)                  -> the hotel's local calendar date"""

from psycopg2.extras import Json

from booking_system.settings.catalog import CATALOG

TABLES = (
    ("setting_catalog", """
        setting_key    VARCHAR(64)  PRIMARY KEY CHECK (setting_key = lower(btrim(setting_key)) AND setting_key <> ''),
        module_name    VARCHAR(32)  NOT NULL REFERENCES module (module_name),
        question       TEXT         NOT NULL CHECK (btrim(question) <> ''),
        explanation    TEXT         NOT NULL,
        value_type     VARCHAR(16)  NOT NULL CHECK (value_type IN ('boolean','choice','choices','time','amount','tax_codes')),
        options        JSONB        NOT NULL DEFAULT '[]',
        required       BOOLEAN      NOT NULL,
        required_when  JSONB        NOT NULL DEFAULT '{}',
        set_module     VARCHAR(32)  NOT NULL REFERENCES module (module_name),
        set_level      VARCHAR(16)  NOT NULL REFERENCES access_level (access_level),
        sop_prompt     TEXT         NOT NULL DEFAULT '',
        display_order  INTEGER      NOT NULL,
        effective_basis VARCHAR(16) NOT NULL CHECK (effective_basis IN ('local_date','business_date')),
        status         VARCHAR(16)  NOT NULL CHECK (status IN ('active','retired'))"""),
    ("hotel_setting", """
        hotel_setting_id VARCHAR(48) PRIMARY KEY CHECK (left(hotel_setting_id, 4) = 'hst_'),
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        setting_key     VARCHAR(64)  NOT NULL REFERENCES setting_catalog (setting_key),
        value           JSONB        NOT NULL,
        effective_from  DATE         NOT NULL,
        set_by_user_id  VARCHAR(48)  NOT NULL REFERENCES user_account (user_id),
        set_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
        source          VARCHAR(16)  NOT NULL CHECK (source IN ('onboarding','settings','desk','migration','fixture')),
        note            TEXT         NOT NULL DEFAULT ''"""),
)
# Columns added after a table first shipped: (table, column, definition). The catalog seed below fills them.
COLUMNS = (
    ("setting_catalog", "effective_basis", "VARCHAR(16) NOT NULL DEFAULT 'local_date' CHECK (effective_basis IN ('local_date','business_date'))"),
)
INDEXES = (
    ("hotel_setting_lookup_idx", "hotel_setting (hotel_id, setting_key, effective_from DESC, set_at DESC)"),
)
# CREATE OR REPLACE in one statement each startup: a changed body applies without a manual drop.
FUNCTIONS = """
CREATE OR REPLACE FUNCTION hotel_local_date(p_hotel VARCHAR) RETURNS DATE LANGUAGE sql STABLE AS $$
  SELECT (now() AT TIME ZONE timezone)::date FROM hotel WHERE hotel_id = p_hotel $$;

CREATE OR REPLACE FUNCTION hotel_setting_at(p_hotel VARCHAR, p_key VARCHAR, p_day DATE) RETURNS JSONB LANGUAGE sql STABLE AS $$
  SELECT value FROM hotel_setting WHERE hotel_id = p_hotel AND setting_key = p_key AND effective_from <= p_day
  ORDER BY effective_from DESC, set_at DESC LIMIT 1 $$;

CREATE OR REPLACE FUNCTION hotel_setting_required(p_hotel VARCHAR, p_key VARCHAR, p_day DATE) RETURNS JSONB LANGUAGE plpgsql STABLE AS $$
DECLARE found JSONB; asked TEXT;
BEGIN
  found := hotel_setting_at(p_hotel, p_key, p_day);
  IF found IS NULL THEN
    SELECT question INTO asked FROM setting_catalog WHERE setting_key = p_key;
    IF asked IS NULL THEN
      RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = p_key || ' is not in the setting catalog';
    END IF;
    RAISE EXCEPTION USING ERRCODE = '22023',
      MESSAGE = 'hotel setting ' || p_key || ' is not set for ' || p_day || ': ' || asked,
      HINT = 'Answer it in Settings or tell the desk; the operation is refused until then.';
  END IF;
  RETURN found;
END $$;

CREATE OR REPLACE FUNCTION hotel_module_state(p_hotel VARCHAR, p_module VARCHAR, p_day DATE) RETURNS JSONB LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'governed', EXISTS (SELECT 1 FROM setting_catalog WHERE setting_key = p_module || '.enabled' AND status = 'active'),
    'enabled', hotel_setting_at(p_hotel, p_module || '.enabled', p_day),
    'missing', COALESCE((
      SELECT jsonb_agg(jsonb_build_object('setting_key', c.setting_key, 'question', c.question) ORDER BY c.display_order)
      FROM setting_catalog c
      WHERE c.module_name = p_module AND c.status = 'active' AND c.required AND hotel_setting_at(p_hotel, c.setting_key, p_day) IS NULL
        AND NOT EXISTS (SELECT 1 FROM jsonb_each(c.required_when) w WHERE hotel_setting_at(p_hotel, w.key, p_day) IS DISTINCT FROM w.value)
    ), '[]'::jsonb)) $$;

CREATE OR REPLACE FUNCTION hr_worked_hours(p_hotel VARCHAR, p_day DATE, p_in TIMESTAMPTZ, p_out TIMESTAMPTZ, p_break_minutes INTEGER)
RETURNS NUMERIC LANGUAGE plpgsql STABLE AS $$
DECLARE breaks TEXT;
BEGIN
  IF p_out IS NULL THEN
    RETURN NULL;
  END IF;
  breaks := hotel_setting_required(p_hotel, 'hr.worked_hours_breaks', p_day) #>> '{}';
  RETURN round(greatest(0, extract(epoch FROM p_out - p_in) / 3600
    - CASE WHEN breaks = 'deduct_unpaid_breaks' THEN COALESCE(p_break_minutes, 0) / 60.0 ELSE 0 END)::numeric, 2);
END $$;

-- Leave days from p_start to p_end, counted by the rule in effect on p_rule_day (the leave's first day; a part of a leave clipped
-- to a period still counts by the rule of the whole leave).
CREATE OR REPLACE FUNCTION hr_leave_days(p_hotel VARCHAR, p_start DATE, p_end DATE, p_rule_day DATE) RETURNS INTEGER LANGUAGE plpgsql STABLE AS $$
DECLARE counting TEXT; weekdays JSONB;
BEGIN
  counting := hotel_setting_required(p_hotel, 'hr.leave_day_counting', p_rule_day) #>> '{}';
  IF counting = 'calendar_days' THEN
    RETURN greatest(0, p_end - p_start + 1);
  END IF;
  weekdays := hotel_setting_required(p_hotel, 'hr.working_weekdays', p_rule_day);
  RETURN (SELECT count(*)::int FROM generate_series(p_start, p_end, interval '1 day') g(d) WHERE weekdays ? lower(to_char(g.d, 'Dy')));
END $$;
DROP FUNCTION IF EXISTS hr_leave_days(VARCHAR, DATE, DATE);

CREATE OR REPLACE FUNCTION hotel_setting_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'hotel_setting rows are history; record a new value with its effective_from instead';
END $$;

-- The catalog row decides the shape. A writer stores the jsonb this returns; a bad value raises 22023 and is not stored.
CREATE OR REPLACE FUNCTION hotel_setting_accept(p_hotel VARCHAR, p_key VARCHAR, p_value JSONB) RETURNS JSONB LANGUAGE plpgsql STABLE AS $$
DECLARE
  kind TEXT;
  opts JSONB;
  allowed TEXT[];
  picked TEXT[];
  active TEXT[];
  clock TIME;
  amount NUMERIC;
  asked TEXT;
BEGIN
  SELECT value_type, options, question INTO kind, opts, asked
  FROM setting_catalog WHERE setting_key = p_key AND status = 'active';
  IF kind IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'unknown setting ' || COALESCE(p_key, '');
  END IF;

  IF kind = 'boolean' THEN
    IF jsonb_typeof(p_value) = 'boolean' THEN
      RETURN p_value;
    END IF;
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = p_key || ' is yes or no (true or false)';
  END IF;

  IF kind IN ('choice', 'choices') THEN
    SELECT COALESCE(array_agg(o->>'value' ORDER BY ord), ARRAY[]::text[]) INTO allowed
    FROM jsonb_array_elements(opts) WITH ORDINALITY AS e(o, ord);
  END IF;

  IF kind = 'choice' THEN
    IF jsonb_typeof(p_value) = 'string' AND (p_value #>> '{}') = ANY (allowed) THEN
      RETURN to_jsonb(p_value #>> '{}');
    END IF;
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = p_key || ' must be one of ' || array_to_string(allowed, ', ');
  END IF;

  IF kind = 'choices' THEN
    IF jsonb_typeof(p_value) = 'array' THEN
      SELECT COALESCE(array_agg(item), ARRAY[]::text[]) INTO picked FROM jsonb_array_elements_text(p_value) AS item;
    ELSE
      picked := ARRAY[]::text[];
    END IF;
    IF cardinality(picked) > 0 AND cardinality(picked) = (SELECT count(DISTINCT item) FROM unnest(picked) AS item)
       AND NOT EXISTS (SELECT 1 FROM unnest(picked) AS item WHERE NOT (item = ANY (allowed))) THEN
      RETURN (SELECT jsonb_agg(item ORDER BY ord) FROM unnest(allowed) WITH ORDINALITY AS u(item, ord) WHERE item = ANY (picked));
    END IF;
    RAISE EXCEPTION USING ERRCODE = '22023',
      MESSAGE = p_key || ' is a non-empty list of distinct values from ' || array_to_string(allowed, ', ');
  END IF;

  IF kind = 'time' THEN
    BEGIN
      clock := (p_value #>> '{}')::time;
    EXCEPTION WHEN OTHERS THEN
      clock := NULL;
    END;
    IF clock IS NOT NULL AND clock < time '24:00' THEN
      RETURN to_jsonb(to_char(clock, 'HH24:MI'));
    END IF;
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = p_key || ' is a local time HH:MM, not ' || COALESCE(p_value #>> '{}', p_value::text, '');
  END IF;

  IF kind = 'amount' THEN
    BEGIN
      amount := (p_value #>> '{}')::numeric;
    EXCEPTION WHEN OTHERS THEN
      RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = p_key || ' is an amount, not ' || COALESCE(p_value #>> '{}', p_value::text, '');
    END;
    IF amount < 0 OR amount <> round(amount, 2) THEN
      RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = p_key || ' is a non-negative amount with at most 2 decimals';
    END IF;
    RETURN to_jsonb(trim(to_char(amount, 'FM999999999990.00')));
  END IF;

  IF kind = 'tax_codes' THEN
    IF jsonb_typeof(p_value) = 'array' THEN
      SELECT COALESCE(array_agg(upper(btrim(item)) ORDER BY ord), ARRAY[]::text[]) INTO picked
      FROM jsonb_array_elements_text(p_value) WITH ORDINALITY AS e(item, ord);
    ELSE
      picked := ARRAY[]::text[];
    END IF;
    SELECT COALESCE(array_agg(tax_code ORDER BY tax_code), ARRAY[]::text[]) INTO active
    FROM tax_code WHERE hotel_id = p_hotel AND status = 'active';
    IF (SELECT COALESCE(array_agg(item ORDER BY item), ARRAY[]::text[]) FROM unnest(picked) AS item)
       = active THEN
      RETURN COALESCE((SELECT jsonb_agg(item ORDER BY ord) FROM unnest(picked) WITH ORDINALITY AS u(item, ord)), '[]'::jsonb);
    END IF;
    RAISE EXCEPTION USING ERRCODE = '22023',
      MESSAGE = p_key || ' must list every active tax code once, in the order they apply: ' || array_to_string(active, ', ');
  END IF;

  RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = p_key || ' (' || asked || ') has no value rule';
END $$;

CREATE OR REPLACE FUNCTION hotel_setting_apply() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.value := hotel_setting_accept(NEW.hotel_id, NEW.setting_key, NEW.value);
  RETURN NEW;
END $$;
"""
TRIGGERS = (
    ("hotel_setting_no_update", "hotel_setting", "BEFORE UPDATE", "hotel_setting_append_only"),
    ("hotel_setting_value", "hotel_setting", "BEFORE INSERT", "hotel_setting_apply"),
)


def seed_catalog(cur):
    """Insert keys the table does not have yet. setting_catalog is the catalog: a stored row is left as it is, including a key this
    bootstrap list does not name."""
    rows = [dict(row, required_when=row.get("required_when", {}), status="active") for row in CATALOG]
    cur.execute(
        """INSERT INTO setting_catalog (setting_key, module_name, question, explanation, value_type, options, required, required_when,
                                        set_module, set_level, sop_prompt, display_order, effective_basis, status)
           SELECT setting_key, module_name, question, explanation, value_type, options, required, required_when, set_module, set_level,
                  sop_prompt, display_order, effective_basis, status
           FROM jsonb_to_recordset(%s) AS r(setting_key varchar, module_name varchar, question text, explanation text, value_type varchar,
                options jsonb, required boolean, required_when jsonb, set_module varchar, set_level varchar, sop_prompt text,
                display_order integer, effective_basis varchar, status varchar)
           ON CONFLICT (setting_key) DO NOTHING""",
        (Json(rows),),
    )


# hotel columns that held operating times before settings existed -> their setting. Moved once, then dropped.
MOVED_COLUMNS = {"standard_checkin_time": "front_desk.checkin_time", "standard_checkout_time": "front_desk.checkout_time",
                 "night_audit_time": "front_desk.night_audit_time"}


def _move_hotel_times(cur):
    """Each hotel's stored time becomes its setting (source 'migration', set by the hotel's first general manager, effective from its
    first stay night or today), then the column is dropped. A hotel that already has the setting keeps it."""
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'hotel' "
                "AND column_name = ANY(%s)", (list(MOVED_COLUMNS),))
    for (column,) in cur.fetchall():
        cur.execute(
            """INSERT INTO hotel_setting (hotel_setting_id, hotel_id, setting_key, value, effective_from, set_by_user_id, source, note)
               SELECT 'hst_' || gen_random_uuid(), h.hotel_id, %s, to_jsonb(to_char(h.""" + column + """, 'HH24:MI')),
                      LEAST(hotel_local_date(h.hotel_id), (SELECT min(n.stay_date) FROM booking_room_night n WHERE n.hotel_id = h.hotel_id)),
                      gm.user_id, 'migration', %s
               FROM hotel h JOIN LATERAL (SELECT u.user_id FROM hotel_user u JOIN access_role r ON r.role_id = u.role_id
                                          WHERE u.hotel_id = h.hotel_id AND r.role_name = 'GENERAL_MANAGER' ORDER BY u.activated_at LIMIT 1) gm ON TRUE
               WHERE NOT EXISTS (SELECT 1 FROM hotel_setting s WHERE s.hotel_id = h.hotel_id AND s.setting_key = %s)""",
            (MOVED_COLUMNS[column], "moved from hotel." + column, MOVED_COLUMNS[column]))
        cur.execute("ALTER TABLE hotel DROP COLUMN " + column)


def ensure(cur, seen):
    """Core area hook: autocommit DDL cursor and core_schema.catalog(cur)."""
    for name, body in TABLES:
        if name not in seen["relations"]:
            cur.execute("CREATE TABLE " + name + " (" + body + ")")
    from booking_system.content_store import add_column
    for table, column, definition in COLUMNS:
        add_column(cur, table, column, definition)
    for name, target in INDEXES:
        if name not in seen["relations"]:
            cur.execute("CREATE INDEX " + name + " ON " + target)
    cur.execute(FUNCTIONS)
    for name, table, timing, function in TRIGGERS:
        if name not in seen["triggers"]:
            cur.execute("CREATE TRIGGER " + name + " " + timing + " ON " + table + " FOR EACH ROW EXECUTE FUNCTION " + function + "()")
    seed_catalog(cur)
    _move_hotel_times(cur)
