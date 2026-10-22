import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cursor = conn.cursor()

# Drop the table if it exists

# Create the table with the canonical cleaning workflow schema
cursor.execute("""
CREATE TABLE IF NOT EXISTS room_stay (
    booking_id VARCHAR NOT NULL,
    stay_date DATE NOT NULL,
    stay_index INTEGER NOT NULL,  -- 1-based index for each room reserved for this night
    hotel_name VARCHAR NOT NULL,
    room_number VARCHAR,          -- nullable, filled in when assigned
    stay_status VARCHAR NOT NULL,  -- 'pending', 'assigned', 'occupied', 'checked_in', 'departed', 'cancelled'
    assigned_by VARCHAR,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (booking_id, stay_date, stay_index)
);
""")

conn.commit()
cursor.close()
conn.close()
print("✅ room_readiness_status table recreated with canonical schema.")
