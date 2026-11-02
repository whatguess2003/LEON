"""Rooms, inventory, pricing, channel room mapping and Kite DDL beyond core_schema (owned by the rooms/inventory workstream)."""

from booking_system.schema_tools import create_tables

KITE_TABLES = (
    ("kite_hotel_setting", """
        hotel_id           VARCHAR(48)   PRIMARY KEY REFERENCES hotel (hotel_id),
        enabled            BOOLEAN       NOT NULL DEFAULT FALSE,
        sensitivity        NUMERIC(8,4)  NOT NULL CHECK (sensitivity >= 0),
        lower_limit        NUMERIC(12,2) NOT NULL CHECK (lower_limit >= 0),
        upper_limit        NUMERIC(12,2) NOT NULL CHECK (upper_limit >= lower_limit),
        updated_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
        updated_by_user_id VARCHAR(48)   REFERENCES user_account (user_id)"""),
    ("kite_push_day", """
        hotel_id     VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
        stay_date    DATE        NOT NULL,
        push_dynamic BOOLEAN     NOT NULL DEFAULT FALSE,
        pricing_name VARCHAR(255),
        PRIMARY KEY (hotel_id, stay_date)"""),
    ("kite_price_anchor", """
        hotel_id     VARCHAR(48) NOT NULL,
        room_type_id VARCHAR(48) NOT NULL,
        stay_date    DATE        NOT NULL,
        anchor_at    TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (room_type_id, stay_date),
        FOREIGN KEY (hotel_id, room_type_id) REFERENCES room_type (hotel_id, room_type_id) ON DELETE CASCADE"""),
)

# Rows of the hotel_name shape, in the new column order.
KITE_CARRIED = {
    "kite_hotel_setting": """SELECT h.hotel_id, k.enabled, k.sensitivity, k.lower_limit, k.upper_limit, k.updated_at, NULL::varchar
                             FROM kite_hotel_setting k JOIN hotel h ON h.hotel_name = k.hotel_name""",
    "kite_push_day": """SELECT h.hotel_id, k.stay_date, k.push_dynamic, k.pricing_name
                        FROM kite_push_day k JOIN hotel h ON h.hotel_name = k.hotel_name""",
    "kite_price_anchor": """SELECT t.hotel_id, t.room_type_id, k.stay_date, k.anchor_at
                            FROM kite_price_anchor k JOIN hotel h ON h.hotel_name = k.hotel_name
                            JOIN room_type t ON t.hotel_id = h.hotel_id AND t.room_type_name = k.room_type""",
}


def ensure(cur, seen):
    """Autocommit DDL cursor. Read the catalog first; create or reshape only what is missing."""
    create_tables(cur, seen, KITE_TABLES, KITE_CARRIED)
