"""World tables. Idempotent startup DDL. None of these duplicate PMS truth: they hold authentic hotel media bound to a room, room type
or facility by reference, and immutable scene manifest revisions with one active revision per scene (atomic publish and rollback)."""

from booking_system.db import connect

LOCK_TIMEOUT = "5s"

TABLES = (
    """CREATE TABLE IF NOT EXISTS world_media (
        media_id        VARCHAR(48)  PRIMARY KEY CHECK (left(media_id, 4) = 'wmd_'),
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        binding_kind    VARCHAR(16)  NOT NULL CHECK (binding_kind IN ('room', 'room_type', 'facility')),
        binding_ref     VARCHAR(64)  NOT NULL CHECK (binding_ref = UPPER(btrim(binding_ref)) AND binding_ref <> ''),
        media_role      VARCHAR(24)  NOT NULL DEFAULT 'authentic_photo' CHECK (media_role = 'authentic_photo'),
        caption         VARCHAR(160) NOT NULL DEFAULT '',
        mime            VARCHAR(24)  NOT NULL CHECK (mime IN ('image/jpeg', 'image/png', 'image/webp')),
        width           INTEGER      NOT NULL CHECK (width > 0),
        height          INTEGER      NOT NULL CHECK (height > 0),
        byte_size       INTEGER      NOT NULL CHECK (byte_size > 0),
        checksum        CHAR(64)     NOT NULL,
        data            BYTEA        NOT NULL,
        thumb_mime      VARCHAR(24)  CHECK (thumb_mime IS NULL OR thumb_mime IN ('image/jpeg', 'image/png', 'image/webp')),
        thumb_data      BYTEA,
        display_order   INTEGER      NOT NULL DEFAULT 0,
        status          VARCHAR(16)  NOT NULL DEFAULT 'approved' CHECK (status IN ('approved', 'retired')),
        attested_by_user_id VARCHAR(48) NOT NULL REFERENCES user_account (user_id),
        uploaded_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
        retired_at      TIMESTAMPTZ,
        UNIQUE (hotel_id, checksum))""",
    "CREATE INDEX IF NOT EXISTS world_media_binding ON world_media (hotel_id, binding_kind, binding_ref) WHERE status = 'approved'",
    """CREATE TABLE IF NOT EXISTS world_scene_version (
        hotel_id        VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        scene_id        VARCHAR(64)  NOT NULL,
        scene_version   INTEGER      NOT NULL CHECK (scene_version > 0),
        manifest        JSONB        NOT NULL,
        created_by_user_id VARCHAR(48) NOT NULL REFERENCES user_account (user_id),
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
        PRIMARY KEY (hotel_id, scene_id, scene_version))""",
    """CREATE TABLE IF NOT EXISTS world_scene_active (
        hotel_id        VARCHAR(48)  NOT NULL,
        scene_id        VARCHAR(64)  NOT NULL,
        scene_version   INTEGER      NOT NULL,
        published_by_user_id VARCHAR(48) NOT NULL REFERENCES user_account (user_id),
        published_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
        PRIMARY KEY (hotel_id, scene_id),
        FOREIGN KEY (hotel_id, scene_id, scene_version) REFERENCES world_scene_version (hotel_id, scene_id, scene_version))""",
)


def ensure_schema():
    conn = connect(autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute("SET lock_timeout = '" + LOCK_TIMEOUT + "'")
        for statement in TABLES:
            cur.execute(statement)
        return {"world": len(TABLES)}
    finally:
        conn.close()
