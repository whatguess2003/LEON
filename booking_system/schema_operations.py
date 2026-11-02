"""Housekeeping and maintenance DDL beyond core_schema (owned by the operations workstream)."""


def ensure(cur, seen):
    """Autocommit DDL cursor. Read the catalog first; create or alter only what is missing."""
