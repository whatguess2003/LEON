"""Area DDL that runs after the core tables, in this order. Each module's ensure(cur, seen) gets the autocommit DDL cursor and
core_schema.catalog(cur) as read before the core DDL (relations, functions, triggers, constraints, hotel_name_tables)."""

from booking_system import schema_bookings, schema_frontdesk, schema_guests, schema_operations, schema_rooms
from booking_system import contact_otp as schema_contact_otp
from booking_system.interactive import schema as schema_interactive
from booking_system.onboarding import schema as schema_onboarding
from booking_system.settings import schema as schema_settings

AREAS = (schema_rooms, schema_bookings, schema_frontdesk, schema_operations, schema_guests, schema_settings, schema_onboarding,
         schema_contact_otp, schema_interactive)


def ensure(cur, seen):
    for area in AREAS:
        area.ensure(cur, seen)


def ensure_departments():
    """Department modules on their own transactions, after the core tables exist: HR, Finance, then F&B (which posts into Finance)."""
    from booking_system.finance import schema as finance_schema
    from booking_system.fnb import schema as fnb_schema
    from booking_system.hr import schema as hr_schema
    return {"hr": hr_schema.ensure_schema(), "finance": finance_schema.ensure_schema(), "finance_links": finance_schema.ensure_links(),
            "fnb": fnb_schema.ensure_schema()}
