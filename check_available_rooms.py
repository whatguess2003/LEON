import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()
cur.execute(
    """
    SELECT hotel_name, room_number, stay_date, assignment_status
    FROM room_number_assignments
    WHERE hotel_name = %s
      AND stay_date = ANY(%s::date[])
      AND assignment_status = %s
    ORDER BY stay_date, room_number;
    """,
    ('AOE HOTEL', ['2025-08-06', '2025-08-07'], 'available')
)
rows = cur.fetchall()
print('Available rooms:')
for row in rows:
    print(row)
cur.close()
conn.close() 