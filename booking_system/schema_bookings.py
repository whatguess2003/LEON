"""Bookings, payments and channel reservation DDL beyond core_schema (owned by the bookings workstream)."""

from booking_system.schema_tools import create_indexes, create_tables, drop_tables

CHANNEL_TABLES = (
    # One row per channel reservation the channel manager listed: reservation_in (the PMS told the channel) or reservation_out
    # (the channel told the PMS). channel_booking_ref is the channel's id (the PMS booking id for reservation_in).
    ("channel_reservations", """
        reservation_id      VARCHAR(48)  PRIMARY KEY CHECK (left(reservation_id, 4) = 'chr_'),
        hotel_id            VARCHAR(48)  NOT NULL REFERENCES hotel (hotel_id),
        direction           VARCHAR(16)  NOT NULL CHECK (direction IN ('reservation_in','reservation_out')),
        channel_booking_ref VARCHAR(128) NOT NULL CHECK (channel_booking_ref <> ''),
        booking_id          VARCHAR(48)  REFERENCES booking (booking_id) ON DELETE SET NULL,
        source              VARCHAR(32)  NOT NULL,
        action              VARCHAR(32)  NOT NULL,
        guest_phone         VARCHAR(32),
        guest_name          VARCHAR(255),
        room_types          VARCHAR(255),
        check_in            DATE,
        check_out           DATE,
        booking_status      VARCHAR(16)  NOT NULL CHECK (booking_status IN ('confirmed','cancelled','rejected')),
        message             TEXT,
        updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
        UNIQUE (hotel_id, direction, channel_booking_ref)"""),
    # Each night of each room an Aiosell delivery holds, until the front desk imports it.
    ("aiosell_buffer_room_nights", """
        hotel_id            VARCHAR(48)  NOT NULL,
        channel_booking_ref VARCHAR(128) NOT NULL,
        room_type_id        VARCHAR(48)  NOT NULL,
        stay_date           DATE         NOT NULL,
        status              VARCHAR(16)  NOT NULL CHECK (status IN ('pending','imported')),
        PRIMARY KEY (hotel_id, channel_booking_ref, room_type_id, stay_date),
        FOREIGN KEY (hotel_id, room_type_id) REFERENCES room_type (hotel_id, room_type_id)"""),
)

CHANNEL_INDEXES = (
    ("aiosell_buffer_ref_idx", "aiosell_buffer_room_nights (channel_booking_ref)"),
)

# Written by nothing; its reader is gone.
UNREAD = ("channel_reservation_sync",)


def ensure(cur, seen):
    """Autocommit DDL cursor. Read the catalog first; create or reshape only what is missing."""
    drop_tables(cur, seen, UNREAD)
    create_tables(cur, seen, CHANNEL_TABLES)
    create_indexes(cur, seen, CHANNEL_INDEXES)
