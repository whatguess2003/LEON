import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def get_postgresql_connection():
    return psycopg2.connect(os.getenv('DATABASE_URL'))

def setup_schema():
    conn = get_postgresql_connection()
    cursor = conn.cursor()

    # --- MIGRATION: Rename price_date to stay_date if needed ---
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'daily_pricing' AND column_name = 'price_date';
    """)
    if cursor.fetchone():
        print("Renaming column price_date to stay_date in daily_pricing...")
        cursor.execute("ALTER TABLE daily_pricing RENAME COLUMN price_date TO stay_date;")
        conn.commit()

    # --- MIGRATION: Drop old foreign key if it exists ---
    cursor.execute("""
        SELECT constraint_name FROM information_schema.table_constraints
        WHERE table_name = 'daily_pricing' AND constraint_type = 'FOREIGN KEY';
    """)
    fk = cursor.fetchone()
    if fk:
        print(f"Dropping old foreign key constraint {fk[0]} on daily_pricing...")
        cursor.execute(f"ALTER TABLE daily_pricing DROP CONSTRAINT {fk[0]};")
        conn.commit()

    # Drop and recreate room_number_assignments with booking_id and assignment fields
    cursor.execute("DROP TABLE IF EXISTS room_number_assignments CASCADE;")
    cursor.execute("""
        CREATE TABLE room_number_assignments (
            hotel_name VARCHAR NOT NULL,
            room_type VARCHAR NOT NULL,
            room_number VARCHAR NOT NULL,
            stay_date DATE NOT NULL,
            assignment_status VARCHAR NOT NULL,
            booking_id VARCHAR NULL,
            guest_phone VARCHAR NULL,
            assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            assigned_by VARCHAR NULL,
            assignment_notes TEXT NULL,
            check_in_time TIMESTAMP NULL,
            check_out_time TIMESTAMP NULL,
            PRIMARY KEY (hotel_name, room_type, room_number, stay_date)
        );
    """)
    conn.commit()
    print("✅ Migrated room_number_assignments to composite PK (hotel_name, room_number, stay_date) and dropped assignment_id.")

    # Create daily_pricing table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_pricing (
            hotel_name VARCHAR(255) NOT NULL,
            room_type VARCHAR(255) NOT NULL,
            stay_date DATE NOT NULL,
            base_price DECIMAL(10,2) NOT NULL DEFAULT 0.00,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (hotel_name, room_type, stay_date),
            FOREIGN KEY (hotel_name, room_type, stay_date) REFERENCES room_inventory_nightly(hotel_name, room_type, stay_date)
        );
    """)

    # Create room_readiness_status table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS room_readiness_status (
            hotel_name VARCHAR NOT NULL,
            room_number VARCHAR NOT NULL,
            readiness_status VARCHAR DEFAULT 'unprepared',
            status_changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            changed_by VARCHAR,
            notes TEXT,
            prepared_since TIMESTAMP NULL,
            auto_unprepare_threshold_days INTEGER DEFAULT 3,
            PRIMARY KEY (hotel_name, room_number)
        );
    """)

    # --- MIGRATION: Remove active_inventory, add blocked_quantity if needed ---
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'room_inventory_nightly' AND column_name = 'active_inventory';
    """)
    if cursor.fetchone():
        print("Dropping column active_inventory from room_inventory_nightly...")
        cursor.execute("ALTER TABLE room_inventory_nightly DROP COLUMN active_inventory;")
        conn.commit()

    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'room_inventory_nightly' AND column_name = 'blocked_quantity';
    """)
    if not cursor.fetchone():
        print("Adding column blocked_quantity to room_inventory_nightly...")
        cursor.execute("ALTER TABLE room_inventory_nightly ADD COLUMN blocked_quantity INTEGER NOT NULL DEFAULT 0;")
        conn.commit()

    # Create room_inventory_nightly table (canonical per-night inventory)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS room_inventory_nightly (
            hotel_name VARCHAR(255) NOT NULL,
            room_type VARCHAR(255) NOT NULL,
            stay_date DATE NOT NULL,
            total_inventory INTEGER NOT NULL DEFAULT 0,
            blocked_quantity INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (hotel_name, room_type, stay_date)
        );
    """)

    # --- MIGRATION: Remove check_in_date and check_out_date from room_bookings if present ---
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'room_bookings' AND column_name = 'check_in_date';
    """)
    if cursor.fetchone():
        print("Dropping column check_in_date from room_bookings...")
        cursor.execute("ALTER TABLE room_bookings DROP COLUMN check_in_date;")
        conn.commit()

    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'room_bookings' AND column_name = 'check_out_date';
    """)
    if cursor.fetchone():
        print("Dropping column check_out_date from room_bookings...")
        cursor.execute("ALTER TABLE room_bookings DROP COLUMN check_out_date;")
        conn.commit()

    # Add modified_at column to room_bookings if it doesn't exist
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'room_bookings' AND column_name = 'modified_at';
    """)
    if not cursor.fetchone():
        print("Adding column modified_at to room_bookings...")
        cursor.execute("ALTER TABLE room_bookings ADD COLUMN modified_at TIMESTAMP;")
        conn.commit()

    # Create booking_modification_log table for audit trail of booking changes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS booking_modification_log (
            log_id SERIAL PRIMARY KEY,
            booking_id VARCHAR NOT NULL,
            modified_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            modified_by VARCHAR,
            field_changed VARCHAR,
            old_value TEXT,
            new_value TEXT,
            change_reason TEXT
        );
    """)

    # Drop the task_assignments table entirely (consolidate into maintenance_requests)
    cursor.execute("DROP TABLE IF EXISTS task_assignments CASCADE;")
    # Redefine maintenance_requests to include all assignment and progress tracking fields
    cursor.execute("DROP TABLE IF EXISTS maintenance_requests CASCADE;")
    cursor.execute("""
        CREATE TABLE maintenance_requests (
            task_id VARCHAR PRIMARY KEY,
            room_number VARCHAR NOT NULL,
            hotel_name VARCHAR NOT NULL,
            reported_by VARCHAR NOT NULL,
            assigned_to VARCHAR NULL,
            assigned_at TIMESTAMP NULL,
            started_at TIMESTAMP NULL,
            completed_at TIMESTAMP NULL,
            issue VARCHAR NOT NULL,
            urgency VARCHAR NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'reported',
            reported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolution_description TEXT NULL,
            parts_used TEXT NULL,
            actual_cost DECIMAL(10,2) NULL
            -- All assignment and progress tracking is now in this table
        );
    """)
    conn.commit()
    print("✅ Migrated maintenance_requests and task_assignments to use only task_id as unique identifier.")

    # Drop the unique constraint on (hotel_name, room_number) if it exists
    cursor.execute("""
        ALTER TABLE room_number_assignments
        DROP CONSTRAINT IF EXISTS unique_room_by_hotel;
    """)

    # Create indexes for fast lookups
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_assignments_hotel_date 
        ON room_number_assignments(hotel_name, stay_date);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_assignments_status 
        ON room_number_assignments(assignment_status);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_assignments_booking 
        ON room_number_assignments(booking_id) WHERE booking_id IS NOT NULL;
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_readiness_status 
        ON room_readiness_status(readiness_status);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_readiness_hotel 
        ON room_readiness_status(hotel_name);
    """)

    conn.commit()
    cursor.close()
    conn.close()
    print("✅ PostgreSQL schema setup complete.")

if __name__ == "__main__":
    setup_schema() 