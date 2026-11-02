"""Startup index hook. Documents live in PostgreSQL, which already keys each record."""


def ensure_content_tables():
    from booking_system.content_store import ensure_tables
    ensure_tables()
    return "PostgreSQL document store ready."
