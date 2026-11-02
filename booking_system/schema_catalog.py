"""One-round-trip read of the current schema's catalog for startup DDL. The database is remote, so each existence check that
asks the server costs a round trip; DDL modules read this snapshot once and record what they create in it."""

_SQL = """
SELECT jsonb_build_object(
  'relations', (SELECT COALESCE(jsonb_agg(c.relname), '[]') FROM pg_class c WHERE c.relnamespace = current_schema()::regnamespace),
  'columns', (SELECT COALESCE(jsonb_object_agg(t.relname, t.cols), '{}') FROM (
      SELECT c.relname, jsonb_agg(a.attname) AS cols FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
      WHERE c.relnamespace = current_schema()::regnamespace AND c.relkind IN ('r', 'p', 'v', 'm') AND a.attnum > 0 AND NOT a.attisdropped
      GROUP BY c.relname) t),
  'constraints', (SELECT COALESCE(jsonb_object_agg(conname, convalidated), '{}') FROM pg_constraint
      WHERE connamespace = current_schema()::regnamespace),
  'functions', (SELECT COALESCE(jsonb_agg(proname), '[]') FROM pg_proc WHERE pronamespace = current_schema()::regnamespace),
  'triggers', (SELECT COALESCE(jsonb_agg(tgname || ' ' || tgrelid::regclass::text), '[]') FROM pg_trigger WHERE NOT tgisinternal),
  'types', (SELECT COALESCE(jsonb_agg(typname), '[]') FROM pg_type WHERE typnamespace = current_schema()::regnamespace),
  'extensions', (SELECT COALESCE(jsonb_agg(extname), '[]') FROM pg_extension)
) AS snapshot
"""


class Snapshot:
    def __init__(self, data):
        self.relations = set(data["relations"])
        self.columns_of = {table: set(cols) for table, cols in data["columns"].items()}
        self.constraints = dict(data["constraints"])
        self.functions = set(data["functions"])
        self.triggers = set(data["triggers"])
        self.types = set(data["types"])
        self.extensions = set(data["extensions"])

    def exists(self, name):
        return name in self.relations

    def columns(self, table):
        return self.columns_of.get(table, set())

    def validated(self, constraint):
        """True or False for an existing constraint, None when it does not exist."""
        return self.constraints.get(constraint)

    def created(self, name):
        self.relations.add(name)


def snapshot(cur):
    cur.execute(_SQL)
    found = cur.fetchone()
    return Snapshot(found["snapshot"] if isinstance(found, dict) else found[0])
