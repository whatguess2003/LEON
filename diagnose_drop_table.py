import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

db_url = os.getenv('DATABASE_URL')
print("DATABASE_URL:", db_url)

conn = psycopg2.connect(db_url)
cur = conn.cursor()

# Print connection info
dsn = conn.get_dsn_parameters()
print("Connected to database:", dsn.get('dbname'))
print("Connected as user:", dsn.get('user'))
cur.execute("SELECT current_schema();")
schema = cur.fetchone()[0]
print("Current schema:", schema)

# Attempt to drop the table and print any errors
try:
    cur.execute("DROP TABLE IF EXISTS room_readiness_status CASCADE;")
    conn.commit()
    print("✅ Drop command executed.")
except Exception as e:
    print("❌ Error dropping table:", e)

# List all tables named room_readiness_status in all schemas
cur.execute("""
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_name = 'room_readiness_status'
""")
tables = cur.fetchall()
if tables:
    print("room_readiness_status tables still present:")
    for row in tables:
        print("- schema:", row[0], "table:", row[1])
else:
    print("✅ No room_readiness_status table found in any schema.")

cur.close()
conn.close()
print("✅ Done.") 