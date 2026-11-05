"""Startup DDL: the hotel registry and core tables, the workflow tables, complaints, the department modules, then the world tables."""

from dotenv import load_dotenv

load_dotenv()


def setup_schema():
    from booking_system import core_schema, schema_areas
    from booking_system.content_store import _with, ensure_tables
    from booking_system.semantic.complaints import complaints_ddl
    from booking_system.semantic.registry import ensure_workflow_tables

    ensure_tables()
    core_schema.run()
    ensure_workflow_tables()
    _with(complaints_ddl)
    schema_areas.ensure_departments()
    from booking_system.world import schema as world_schema
    world_schema.ensure_schema()
    print("PostgreSQL schema setup complete.", flush=True)


if __name__ == "__main__":
    setup_schema()
