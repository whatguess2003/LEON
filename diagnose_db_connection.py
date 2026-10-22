import psycopg2
import os
from dotenv import load_dotenv
import random
import string

load_dotenv()

conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()

# Print connection parameters
dsn = conn.get_dsn_parameters()
print("Connected to database:", dsn.get('dbname'))
print("Connected as user:", dsn.get('user'))
cur.execute("SELECT current_schema();")
schema = cur.fetchone()[0]
print("Current schema:", schema)
print("DATABASE_URL:", os.getenv('DATABASE_URL'))

# Create a unique test table
unique_table = 'test_table_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
try:
    cur.execute(f"CREATE TABLE {unique_table} (id SERIAL PRIMARY KEY, note TEXT);")
    conn.commit()
    print(f"✅ Created table: {unique_table}")
except Exception as e:
    print(f"Warning: Could not create test table: {e}")

# List all tables in the current schema
cur.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = %s
    ORDER BY table_name
""", (schema,))
tables = cur.fetchall()
print("Tables in schema:")
for t in tables:
    print("-", t[0])

cur.close()
conn.close()
print("✅ Done.") 