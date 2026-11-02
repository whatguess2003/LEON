"""WhatsApp interactive-message catalog. The tables are the navigation of the PMS: each row is one Cloud API list or reply-button
message, and each choice opens the next template. catalog.py inserts a key only when the table has no row for it.

complete_sql and choice_sql are a single SELECT. They bind %(hotel_id)s and %(record_id)s. choice_sql returns row_id, title,
description, section_title. A list reply id is the template key, or template_key/record_id when the row came from a read."""

from psycopg2.extras import Json

TABLES = (
    ("wa_interactive_template", """
        template_key     VARCHAR(64)  PRIMARY KEY
            CHECK (template_key = lower(btrim(template_key)) AND template_key <> ''
                   AND translate(template_key, 'abcdefghijklmnopqrstuvwxyz0123456789._', '') = ''),
        module_name      VARCHAR(32)  NOT NULL REFERENCES module (module_name),
        purpose          VARCHAR(16)  NOT NULL CHECK (purpose IN ('navigate','read','input')),
        interactive_type VARCHAR(16)  NOT NULL CHECK (interactive_type IN ('list','button')),
        header_text      VARCHAR(60)  NOT NULL DEFAULT '',
        body_text        VARCHAR(1024) NOT NULL CHECK (btrim(body_text) <> ''),
        footer_text      VARCHAR(60)  NOT NULL DEFAULT '',
        button_text      VARCHAR(20)  NOT NULL DEFAULT '',
        page_route       VARCHAR(64)  NOT NULL DEFAULT '',
        complete_sql     TEXT         NOT NULL DEFAULT '',
        choice_sql       TEXT         NOT NULL DEFAULT '',
        back_template_key VARCHAR(64) REFERENCES wa_interactive_template (template_key),
        status           VARCHAR(16)  NOT NULL CHECK (status IN ('active','retired')),
        CHECK (interactive_type <> 'list' OR char_length(btrim(button_text)) BETWEEN 1 AND 20),
        CHECK (interactive_type <> 'button' OR btrim(button_text) = '')"""),
    ("wa_interactive_choice", """
        template_key      VARCHAR(64)  NOT NULL REFERENCES wa_interactive_template (template_key),
        choice_order      SMALLINT     NOT NULL CHECK (choice_order > 0),
        row_id            VARCHAR(200) NOT NULL CHECK (btrim(row_id) <> '' AND char_length(row_id) <= 200),
        title             VARCHAR(24)  NOT NULL CHECK (btrim(title) <> ''),
        description       VARCHAR(72)  NOT NULL DEFAULT '',
        section_title     VARCHAR(24)  NOT NULL CHECK (btrim(section_title) <> ''),
        choice_kind       VARCHAR(16)  NOT NULL CHECK (choice_kind IN ('navigate','read','input')),
        next_template_key VARCHAR(64)  NOT NULL REFERENCES wa_interactive_template (template_key),
        PRIMARY KEY (template_key, row_id),
        UNIQUE (template_key, choice_order)"""),
)
INDEXES = (
    ("wa_interactive_choice_order_idx", "wa_interactive_choice (template_key, choice_order)"),
)
FUNCTIONS = """
CREATE OR REPLACE FUNCTION wa_interactive_choice_limit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kind TEXT; n INT;
BEGIN
  SELECT interactive_type INTO kind FROM wa_interactive_template WHERE template_key = NEW.template_key;
  SELECT count(*) INTO n FROM wa_interactive_choice WHERE template_key = NEW.template_key;
  IF kind = 'list' AND n >= 10 THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'a WhatsApp list holds at most 10 rows';
  END IF;
  IF kind = 'button' AND n >= 3 THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'a WhatsApp reply button message holds at most 3 buttons';
  END IF;
  IF kind = 'button' AND char_length(btrim(NEW.title)) > 20 THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'a WhatsApp reply button title is at most 20 characters';
  END IF;
  RETURN NEW;
END $$;
"""
TRIGGERS = (
    ("wa_interactive_choice_limit", "wa_interactive_choice", "BEFORE INSERT", "wa_interactive_choice_limit"),
)


def _explain(cur, sql):
    text = str(sql or "").strip()
    if not text:
        return
    cur.execute("EXPLAIN " + text, {"hotel_id": "htl_00000000-0000-0000-0000-000000000000", "record_id": "none"})


def seed(cur):
    """Insert catalog keys that are not stored yet, then check every select still plans."""
    from booking_system.interactive.catalog import build
    templates, choices = build()
    for item in templates:
        _explain(cur, item["complete_sql"])
        _explain(cur, item["choice_sql"])
    cur.execute(
        """INSERT INTO wa_interactive_template
             (template_key, module_name, purpose, interactive_type, header_text, body_text, footer_text, button_text,
              page_route, complete_sql, choice_sql, back_template_key, status)
           SELECT template_key, module_name, purpose, interactive_type, header_text, body_text, footer_text, button_text,
                  page_route, complete_sql, choice_sql, NULL, status
           FROM jsonb_to_recordset(%s) AS r(template_key varchar, module_name varchar, purpose varchar, interactive_type varchar,
                header_text varchar, body_text varchar, footer_text varchar, button_text varchar, page_route varchar,
                complete_sql text, choice_sql text, back_template_key varchar, status varchar)
           ON CONFLICT (template_key) DO NOTHING""",
        (Json(templates),),
    )
    cur.execute(
        """UPDATE wa_interactive_template AS t SET back_template_key = v.back_template_key
           FROM jsonb_to_recordset(%s) AS v(template_key varchar, back_template_key varchar)
           WHERE t.template_key = v.template_key AND t.back_template_key IS NULL AND v.back_template_key <> ''""",
        (Json([{"template_key": item["template_key"], "back_template_key": item["back_template_key"]} for item in templates]),),
    )
    cur.execute(
        """INSERT INTO wa_interactive_choice
             (template_key, choice_order, row_id, title, description, section_title, choice_kind, next_template_key)
           SELECT template_key, choice_order, row_id, title, description, section_title, choice_kind, next_template_key
           FROM jsonb_to_recordset(%s) AS r(template_key varchar, choice_order smallint, row_id varchar, title varchar,
                description varchar, section_title varchar, choice_kind varchar, next_template_key varchar)
           ON CONFLICT (template_key, row_id) DO NOTHING""",
        (Json(choices),),
    )


def ensure(cur, seen):
    """Core area hook: autocommit DDL cursor and core_schema.catalog(cur)."""
    for name, body in TABLES:
        if name not in seen["relations"]:
            cur.execute("CREATE TABLE " + name + " (" + body + ")")
            seen["relations"].add(name)
    for name, target in INDEXES:
        if name not in seen["relations"]:
            cur.execute("CREATE INDEX " + name + " ON " + target)
            seen["relations"].add(name)
    cur.execute(FUNCTIONS)
    for name, table, timing, function in TRIGGERS:
        if name not in seen["triggers"]:
            cur.execute("CREATE TRIGGER " + name + " " + timing + " ON " + table + " FOR EACH ROW EXECUTE FUNCTION " + function + "()")
            seen["triggers"].add(name)
    seed(cur)
