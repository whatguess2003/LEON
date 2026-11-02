"""General recipes: the only data kept across fixture rebuilds. Run: python -m migrations.general_recipes export"""

import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

import psycopg2

from booking_system.content_store import _database_url

GENERAL = "SELECT catalog_id FROM catalogs WHERE scope = 'general'"
TABLES = {
    "catalogs": "scope = 'general'",
    "catalog_versions": "catalog_id IN (" + GENERAL + ")",
    "catalog_active_versions": "catalog_id IN (" + GENERAL + ")",
    "catalog_keyphrase": "catalog_id IN (" + GENERAL + ")",
    "knowledge_index": "catalog_id IN (" + GENERAL + ")",
    "knowledge_publications": "catalog_id IN (" + GENERAL + ")",
}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def export():
    conn = psycopg2.connect(_database_url())
    cur = conn.cursor()
    out = {}
    for table, where in TABLES.items():
        cur.execute("SELECT to_jsonb(t) FROM " + table + " t WHERE " + where)
        out[table] = [row[0] for row in cur.fetchall()]
    conn.close()
    path = os.path.join(ROOT, "_backups", "general_recipes_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1, default=str)
    print(path, {table: len(rows) for table, rows in out.items()})


if __name__ == "__main__":
    {"export": export}[sys.argv[1]]()
