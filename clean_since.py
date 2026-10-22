import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cursor = conn.cursor()
cursor.execute("""
    SELECT column_name
    FROM information_schema.columns
    WHERE table_name = 'room_readiness_status'
    ORDER BY ordinal_position
""")
columns = cursor.fetchall()
print("room_readiness_status columns:")
for col in columns:
    print("-", col[0])
cursor.close()
conn.close()