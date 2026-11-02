"""Onboarding DDL (area hook). Computable state is in tables: the onboarding session (onb_), its section checklist, its items (open
questions and what was saved), SOP submissions with the setting keys they are linked to, and data-import batches with their files,
source rows, row errors and the rows each commit created (so a batch rolls back exactly what it wrote). Transcripts, SOP text and
uploaded file text are documents. An imported row keeps the old system's id as an external_ref (unique per hotel and entity,
indexed both ways), never as its LEON id. Staff accounts are imported as invited memberships; an invite carries only the hash of
its one-time link token.

Every import table carries hotel_id, so fixtures.sandbox.wipe removes a hotel's batches with the hotel."""

from booking_system.schema_tools import create_indexes, create_tables

SECTION_STATUS = "('not_started','in_progress','skipped','done')"

TABLES = (
    ("onboarding_session", """
        onboarding_id   VARCHAR(48)  PRIMARY KEY CHECK (left(onboarding_id, 4) = 'onb_'),
        user_id         VARCHAR(48)  REFERENCES user_account (user_id),
        hotel_id        VARCHAR(48)  REFERENCES hotel (hotel_id),
        status          VARCHAR(16)  NOT NULL CHECK (status IN ('active','finished')),
        language        VARCHAR(32)  NOT NULL DEFAULT '',
        tone            VARCHAR(16)  NOT NULL DEFAULT '' CHECK (tone IN ('','warm','brief','formal','playful')),
        pace            VARCHAR(16)  NOT NULL DEFAULT '' CHECK (pace IN ('','one_at_a_time','several_at_once')),
        role_note       TEXT         NOT NULL DEFAULT '',
        turns           INTEGER      NOT NULL DEFAULT 0,
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
        last_turn_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
        CHECK (hotel_id IS NULL OR user_id IS NOT NULL)"""),
    ("onboarding_section", """
        onboarding_id   VARCHAR(48)  NOT NULL REFERENCES onboarding_session (onboarding_id) ON DELETE CASCADE,
        section         VARCHAR(32)  NOT NULL,
        status          VARCHAR(16)  NOT NULL CHECK (status IN """ + SECTION_STATUS + """),
        note            TEXT         NOT NULL DEFAULT '',
        updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
        PRIMARY KEY (onboarding_id, section)"""),
    ("onboarding_item", """
        item_id         VARCHAR(48)  PRIMARY KEY CHECK (left(item_id, 4) = 'obi_'),
        onboarding_id   VARCHAR(48)  NOT NULL REFERENCES onboarding_session (onboarding_id) ON DELETE CASCADE,
        section         VARCHAR(32)  NOT NULL,
        kind            VARCHAR(8)   NOT NULL CHECK (kind IN ('open','saved')),
        item_key        VARCHAR(128) NOT NULL CHECK (btrim(item_key) <> ''),
        summary         TEXT         NOT NULL,
        status          VARCHAR(16)  NOT NULL CHECK (status IN ('open','answered','skipped','saved')),
        asked_count     SMALLINT     NOT NULL DEFAULT 0,
        last_asked_at   TIMESTAMPTZ,
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
        resolved_at     TIMESTAMPTZ,
        CHECK ((kind = 'saved') = (status = 'saved'))"""),
    ("onboarding_sop", """
        sop_id          VARCHAR(48)  PRIMARY KEY CHECK (left(sop_id, 4) = 'osp_'),
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        onboarding_id   VARCHAR(48)  REFERENCES onboarding_session (onboarding_id) ON DELETE SET NULL,
        user_id         VARCHAR(48)  NOT NULL REFERENCES user_account (user_id),
        title           VARCHAR(255) NOT NULL CHECK (btrim(title) <> ''),
        source          VARCHAR(8)   NOT NULL CHECK (source IN ('chat','upload')),
        file_name       VARCHAR(255) NOT NULL DEFAULT '',
        status          VARCHAR(16)  NOT NULL CHECK (status IN ('proposed','confirmed','rejected')),
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
        confirmed_at    TIMESTAMPTZ,
        CHECK ((status = 'confirmed') = (confirmed_at IS NOT NULL))"""),
    ("onboarding_sop_link", """
        sop_id          VARCHAR(48)  NOT NULL REFERENCES onboarding_sop (sop_id) ON DELETE CASCADE,
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        setting_key     VARCHAR(64)  NOT NULL REFERENCES setting_catalog (setting_key),
        summary         TEXT         NOT NULL,
        status          VARCHAR(16)  NOT NULL CHECK (status IN ('proposed','saved','waiting_value','failed')),
        detail          TEXT         NOT NULL DEFAULT '',
        saved_at        TIMESTAMPTZ,
        PRIMARY KEY (sop_id, setting_key)"""),
    ("import_batch", """
        batch_id        VARCHAR(48)  PRIMARY KEY CHECK (left(batch_id, 4) = 'imb_'),
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        user_id         VARCHAR(48)  NOT NULL REFERENCES user_account (user_id),
        onboarding_id   VARCHAR(48)  REFERENCES onboarding_session (onboarding_id) ON DELETE SET NULL,
        status          VARCHAR(16)  NOT NULL CHECK (status IN ('draft','validated','committed','rolled_back')),
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
        validated_at    TIMESTAMPTZ,
        committed_at    TIMESTAMPTZ,
        rolled_back_at  TIMESTAMPTZ,
        detail          TEXT         NOT NULL DEFAULT ''"""),
    ("import_file", """
        file_id         VARCHAR(48)  PRIMARY KEY CHECK (left(file_id, 4) = 'imf_'),
        batch_id        VARCHAR(48)  NOT NULL REFERENCES import_batch (batch_id) ON DELETE CASCADE,
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        file_name       VARCHAR(255) NOT NULL,
        entity          VARCHAR(32)  NOT NULL,
        headers         JSONB        NOT NULL,
        mapping         JSONB        NOT NULL,
        mapping_status  VARCHAR(16)  NOT NULL CHECK (mapping_status IN ('proposed','confirmed')),
        row_count       INTEGER      NOT NULL CHECK (row_count >= 0),
        valid_count     INTEGER,
        error_count     INTEGER,
        duplicate_count INTEGER,
        created_count   INTEGER,
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()"""),
    ("import_row", """
        file_id         VARCHAR(48)  NOT NULL REFERENCES import_file (file_id) ON DELETE CASCADE,
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        row_number      INTEGER      NOT NULL CHECK (row_number > 0),
        raw             JSONB        NOT NULL,
        PRIMARY KEY (file_id, row_number)"""),
    ("import_row_error", """
        error_id        VARCHAR(48)  PRIMARY KEY CHECK (left(error_id, 4) = 'ime_'),
        file_id         VARCHAR(48)  NOT NULL REFERENCES import_file (file_id) ON DELETE CASCADE,
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        row_number      INTEGER      NOT NULL,
        field           VARCHAR(64)  NOT NULL DEFAULT '',
        error_kind      VARCHAR(16)  NOT NULL CHECK (error_kind IN ('missing','invalid','duplicate','refused')),
        message         TEXT         NOT NULL,
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()"""),
    ("import_record", """
        batch_id        VARCHAR(48)  NOT NULL REFERENCES import_batch (batch_id) ON DELETE CASCADE,
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        seq             INTEGER      NOT NULL,
        entity_table    VARCHAR(64)  NOT NULL,
        entity_id       VARCHAR(255) NOT NULL,
        collection      VARCHAR(128) NOT NULL DEFAULT '',
        PRIMARY KEY (batch_id, seq)"""),
    ("import_external_ref", """
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        entity          VARCHAR(32)  NOT NULL,
        external_ref    VARCHAR(128) NOT NULL CHECK (external_ref = upper(btrim(external_ref)) AND external_ref <> ''),
        entity_id       VARCHAR(48)  NOT NULL,
        batch_id        VARCHAR(48)  NOT NULL REFERENCES import_batch (batch_id) ON DELETE CASCADE,
        PRIMARY KEY (hotel_id, entity, external_ref)"""),
    ("onboarding_invite", """
        invite_id       VARCHAR(48)  PRIMARY KEY CHECK (left(invite_id, 4) = 'inv_'),
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        user_id         VARCHAR(48)  NOT NULL REFERENCES user_account (user_id),
        batch_id        VARCHAR(48)  REFERENCES import_batch (batch_id) ON DELETE SET NULL,
        invited_by      VARCHAR(48)  NOT NULL REFERENCES user_account (user_id),
        token_hash      CHAR(64)     NOT NULL UNIQUE,
        status          VARCHAR(16)  NOT NULL CHECK (status IN ('pending','accepted','revoked')),
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
        expires_at      TIMESTAMPTZ  NOT NULL,
        accepted_at     TIMESTAMPTZ,
        CHECK ((status = 'accepted') = (accepted_at IS NOT NULL))"""),
    ("onboarding_sop_section", """
        sop_id          VARCHAR(48)  NOT NULL REFERENCES onboarding_sop (sop_id) ON DELETE CASCADE,
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        seq             SMALLINT     NOT NULL CHECK (seq > 0),
        title           VARCHAR(255) NOT NULL DEFAULT '',
        summary         TEXT         NOT NULL,
        setting_keys    JSONB        NOT NULL,
        PRIMARY KEY (sop_id, seq)"""),
)
INDEXES = (
    ("onboarding_session_user_idx", "onboarding_session (user_id, hotel_id, last_turn_at DESC)"),
    ("onboarding_item_session_idx", "onboarding_item (onboarding_id, kind, status)"),
    ("onboarding_sop_hotel_idx", "onboarding_sop (hotel_id, status)"),
    ("import_batch_hotel_idx", "import_batch (hotel_id, status)"),
    ("import_file_batch_idx", "import_file (batch_id)"),
    ("import_row_error_file_idx", "import_row_error (file_id, row_number)"),
    ("import_external_ref_entity_idx", "import_external_ref (hotel_id, entity, entity_id)"),
    ("import_external_ref_batch_idx", "import_external_ref (batch_id)"),
    ("onboarding_invite_user_idx", "onboarding_invite (hotel_id, user_id, status)"),
)


def ensure(cur, seen):
    """Area hook: autocommit DDL cursor and core_schema.catalog(cur). Runs after the settings area (setting_catalog)."""
    create_tables(cur, seen, TABLES)
    create_indexes(cur, seen, INDEXES)


def run():
    """Standalone: create the onboarding tables on a direct autocommit connection."""
    from booking_system.core_schema import catalog
    from booking_system.db import connect
    conn = connect(autocommit=True)
    try:
        cur = conn.cursor()
        ensure(cur, catalog(cur))
    finally:
        conn.close()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    run()
    print("onboarding schema ok")
