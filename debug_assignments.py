import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def print_assignments(hotel_name, room_number, stay_dates):
    conn = psycopg2.connect(os.getenv('DATABASE_URL'))
    cursor = conn.cursor()
    for stay_date in stay_dates:
        cursor.execute("""
            SELECT hotel_name, room_number, stay_date, booking_id, assignment_status
            FROM room_number_assignments
            WHERE hotel_name = %s AND room_number = %s AND stay_date = %s
        """, (hotel_name, room_number, stay_date))
        rows = cursor.fetchall()
        print(f"Assignments for {hotel_name}, room {room_number}, date {stay_date}:")
        for row in rows:
            print(row)
    cursor.close()
    conn.close()

if __name__ == "__main__":
    # Example usage: change these values as needed
    hotel_name = "AOE HOTEL"
    room_number = "129"
    stay_dates = ["2025-08-05"]
    print_assignments(hotel_name, room_number, stay_dates)