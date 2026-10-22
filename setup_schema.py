import psycopg2
import os
import sys
from dotenv import load_dotenv

load_dotenv()

def get_postgresql_connection():
    return psycopg2.connect(os.getenv('DATABASE_URL'))

def setup_schema():
    conn = get_postgresql_connection()
    cursor = conn.cursor()

    # --- DROP AND RECREATE room_number_assignments TABLE ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS room_number_assignments (
            hotel_name VARCHAR NOT NULL,
            room_type VARCHAR,
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
            created_by VARCHAR NULL,
            updated_by VARCHAR NULL,
            updated_at TIMESTAMP NULL,
            PRIMARY KEY (hotel_name, room_number, stay_date)
        );
    """)
    cursor.execute("ALTER TABLE room_number_assignments ADD COLUMN IF NOT EXISTS created_by VARCHAR NULL;")
    cursor.execute("ALTER TABLE room_number_assignments ADD COLUMN IF NOT EXISTS updated_by VARCHAR NULL;")
    cursor.execute("ALTER TABLE room_number_assignments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NULL;")

    # Nightly inventory must exist before daily_pricing, which references it.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS room_inventory_nightly (
            hotel_name VARCHAR(255) NOT NULL,
            room_type VARCHAR(255) NOT NULL,
            stay_date DATE NOT NULL,
            total_inventory INTEGER NOT NULL DEFAULT 0,
            blocked_quantity INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by VARCHAR NULL,
            updated_by VARCHAR NULL,
            PRIMARY KEY (hotel_name, room_type, stay_date)
        );
    """)
    cursor.execute("ALTER TABLE room_inventory_nightly ADD COLUMN IF NOT EXISTS created_by VARCHAR NULL;")
    cursor.execute("ALTER TABLE room_inventory_nightly ADD COLUMN IF NOT EXISTS updated_by VARCHAR NULL;")

    # Create daily_pricing table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_pricing (
            hotel_name VARCHAR(255) NOT NULL,
            room_type VARCHAR(255) NOT NULL,
            stay_date DATE NOT NULL,
            base_price DECIMAL(10,2) NOT NULL DEFAULT 0.00,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by VARCHAR NULL,
            updated_by VARCHAR NULL,
            PRIMARY KEY (hotel_name, room_type, stay_date),
            FOREIGN KEY (hotel_name, room_type, stay_date) REFERENCES room_inventory_nightly(hotel_name, room_type, stay_date)
        );
    """)
    cursor.execute("ALTER TABLE daily_pricing ADD COLUMN IF NOT EXISTS created_by VARCHAR NULL;")
    cursor.execute("ALTER TABLE daily_pricing ADD COLUMN IF NOT EXISTS updated_by VARCHAR NULL;")
    cursor.execute("ALTER TABLE daily_pricing ADD COLUMN IF NOT EXISTS saved_price DECIMAL(10,2);")
    cursor.execute("UPDATE daily_pricing SET saved_price = base_price WHERE saved_price IS NULL;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kite_hotel_setting (
            hotel_name VARCHAR(255) PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            sensitivity NUMERIC(8,4) NOT NULL,
            lower_limit NUMERIC(12,2) NOT NULL,
            upper_limit NUMERIC(12,2) NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_by VARCHAR NULL
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kite_push_day (
            hotel_name VARCHAR(255) NOT NULL,
            stay_date DATE NOT NULL,
            push_dynamic BOOLEAN NOT NULL DEFAULT FALSE,
            pricing_name VARCHAR NULL,
            PRIMARY KEY (hotel_name, stay_date)
        );
    """)
    cursor.execute("ALTER TABLE kite_push_day ADD COLUMN IF NOT EXISTS pricing_name VARCHAR;")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kite_price_anchor (
            hotel_name VARCHAR(255) NOT NULL,
            room_type VARCHAR(255) NOT NULL,
            stay_date DATE NOT NULL,
            anchor_at TIMESTAMP NOT NULL,
            PRIMARY KEY (hotel_name, room_type, stay_date)
        );
    """)
    
    # --- DROP AND RECREATE room_readiness_status TABLE ---

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS room_readiness_status (
            hotel_name VARCHAR NOT NULL,
            room_number VARCHAR NOT NULL,
            readiness_status VARCHAR NOT NULL, -- 'clean', 'dirty', 'cleaning', 'occupied'
            status_changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            changed_by VARCHAR,
            created_by VARCHAR,
            updated_by VARCHAR,
            notes TEXT,
            clean_since TIMESTAMP NULL,
            auto_dirty_threshold_days INTEGER DEFAULT 3,
            PRIMARY KEY (hotel_name, room_number)
        );
    """)

    # --- CREATE cleaning_status TABLE ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cleaning_status (
            cleaning_id VARCHAR PRIMARY KEY,
            hotel_name VARCHAR NOT NULL,
            room_number VARCHAR NOT NULL,
            cleaning_status VARCHAR DEFAULT 'pending', -- 'pending', 'assigned', 'in_progress', 'completed', 'quality_check'
            assigned_to VARCHAR,
            priority VARCHAR DEFAULT 'normal',
            task_type VARCHAR DEFAULT 'standard_cleaning',
            created_by VARCHAR NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            assigned_at TIMESTAMP NULL,
            started_at TIMESTAMP NULL,
            completed_at TIMESTAMP NULL,
            estimated_duration_minutes INTEGER DEFAULT 45,
            actual_duration_minutes INTEGER NULL,
            checklist_completed JSONB,
            supplies_used JSONB,
            notes TEXT,
            quality_check_passed BOOLEAN NULL,
            quality_checked_by VARCHAR NULL,
            quality_checked_at TIMESTAMP NULL,
            updated_by VARCHAR NULL,
            updated_at TIMESTAMP NULL
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_cleaning_status_hotel ON cleaning_status(hotel_name);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_cleaning_status_status ON cleaning_status(cleaning_status);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_cleaning_assigned_to ON cleaning_status(assigned_to) WHERE assigned_to IS NOT NULL;
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_cleaning_priority ON cleaning_status(priority);
    """)

    # --- HOUSEKEEPING: staff_duty_log ---
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS staff_duty_log (
            duty_id VARCHAR PRIMARY KEY,
            hotel_name VARCHAR NOT NULL,
            staff_phone VARCHAR NOT NULL,
            clock_in TIMESTAMP NOT NULL,
            clock_out TIMESTAMP NULL,
            shift_label VARCHAR NULL,
            created_by VARCHAR NULL,
            updated_by VARCHAR NULL
        );
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_staff_duty_open 
        ON staff_duty_log(hotel_name, staff_phone) WHERE clock_out IS NULL;
        """
    )

    # --- MIGRATION: Remove check_in_date and check_out_date from room_bookings if present ---
   
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_requests (
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
    # Indexes for maintenance_requests to optimize common filters/sorts
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mr_hotel_task ON maintenance_requests(hotel_name, task_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mr_hotel_status ON maintenance_requests(hotel_name, status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mr_hotel_urgency ON maintenance_requests(hotel_name, urgency);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mr_hotel_reported_at ON maintenance_requests(hotel_name, reported_at);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mr_assigned_to ON maintenance_requests(assigned_to) WHERE assigned_to IS NOT NULL;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mr_hotel_room ON maintenance_requests(hotel_name, room_number);")
    except Exception as e:
        print(f"Warning: Could not create maintenance_requests indexes: {e}")
    # --- Preventive Maintenance: schedules + request extensions ---
    try:
        # Extend maintenance_requests with PM metadata
        cursor.execute("ALTER TABLE maintenance_requests ADD COLUMN IF NOT EXISTS schedule_id VARCHAR NULL;")
        cursor.execute("ALTER TABLE maintenance_requests ADD COLUMN IF NOT EXISTS source VARCHAR NULL DEFAULT 'ADHOC';")
        cursor.execute("ALTER TABLE maintenance_requests ADD COLUMN IF NOT EXISTS due_date DATE NULL;")
        cursor.execute("ALTER TABLE maintenance_requests ADD COLUMN IF NOT EXISTS planned_at TIMESTAMP NULL;")
        # Helpful indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mr_hotel_source_status ON maintenance_requests(hotel_name, source, status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mr_hotel_due_date ON maintenance_requests(hotel_name, due_date);")
        # Idempotency for PM generation (room_number is NOT NULL; use 'N/A' for area scopes)
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_mr_pm_unique ON maintenance_requests(hotel_name, schedule_id, room_number, due_date) WHERE schedule_id IS NOT NULL AND due_date IS NOT NULL;")
    except Exception as e:
        print(f"Warning: Could not extend maintenance_requests for PM: {e}")

    # Create maintenance_schedules table (PostgreSQL only)
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS maintenance_schedules (
                hotel_name VARCHAR NOT NULL,
                schedule_id VARCHAR PRIMARY KEY,
                target_type VARCHAR NOT NULL, -- 'ROOM' | 'AREA'
                room_number VARCHAR NULL,
                area VARCHAR NULL,
                issue VARCHAR NOT NULL,
                urgency VARCHAR NOT NULL,
                interval_days INTEGER NOT NULL,
                start_date DATE NOT NULL,
                last_service_date DATE NULL,
                next_due_date DATE NOT NULL,
                preferred_window VARCHAR NULL,
                auto_out_of_order BOOLEAN NOT NULL DEFAULT FALSE,
                assignment_group VARCHAR NULL,
                timezone VARCHAR NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE
            );
        """)
        # Migration helper: add interval_days if coming from older schema
        cursor.execute("ALTER TABLE maintenance_schedules ADD COLUMN IF NOT EXISTS interval_days INTEGER;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ms_hotel_next_due ON maintenance_schedules(hotel_name, next_due_date) WHERE active = TRUE;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ms_hotel_sched ON maintenance_schedules(hotel_name, schedule_id);")
    except Exception as e:
        print(f"Warning: Could not create maintenance_schedules: {e}")
    # --- DROP AND RECREATE room_stay TABLE ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS room_stay (
            booking_id VARCHAR NOT NULL,
            stay_date DATE NOT NULL,
            stay_index INTEGER NOT NULL,  -- 1-based index for each room reserved for this night
            hotel_name VARCHAR NOT NULL,
            room_number VARCHAR,          -- nullable, filled in when assigned
            stay_status VARCHAR NOT NULL,  -- 'pending', 'assigned', 'occupied', 'checked_out', 'cancelled'
            assigned_by VARCHAR,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (booking_id, stay_date, stay_index)
        );
    """)
    # Ensure per-stay timestamps exist
    try:
        cursor.execute("ALTER TABLE room_stay ADD COLUMN IF NOT EXISTS checked_in_at TIMESTAMP NULL;")
        cursor.execute("ALTER TABLE room_stay ADD COLUMN IF NOT EXISTS checked_out_at TIMESTAMP NULL;")
    except Exception as e:
        print(f"Warning: Could not alter room_stay for timestamps: {e}")

    # --- BOOKINGS CORE TABLES ---
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS room_bookings (
            booking_id VARCHAR PRIMARY KEY,
            guest_phone VARCHAR NOT NULL,
            hotel_name VARCHAR(255) NOT NULL,
            room_type VARCHAR(255) NOT NULL,
            rooms_booked INTEGER NOT NULL DEFAULT 1,
            booking_status VARCHAR(32) NOT NULL,
            booking_reference VARCHAR(255) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NULL,
            updated_by VARCHAR NULL,
            nights INTEGER NULL,
            total_amount NUMERIC(12,2) NULL,
            booking_notes TEXT NULL,
            check_in_date DATE NULL
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS booking_stays (
            hotel_name VARCHAR(255) NOT NULL,
            room_type VARCHAR(255) NOT NULL,
            stay_date DATE NOT NULL,
            booking_id VARCHAR(255) NOT NULL,
            rooms_booked INTEGER NOT NULL DEFAULT 1,
            booking_status VARCHAR(32) NOT NULL,
            PRIMARY KEY (hotel_name, room_type, stay_date, booking_id)
        );
        """
    )

    # --- PAYMENTS TABLES ---
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_tracking (
            booking_id TEXT PRIMARY KEY,
            guest_phone_number TEXT,
            total_amount NUMERIC(12,2),
            amount_paid NUMERIC(12,2),
            remaining_balance NUMERIC(12,2),
            payment_status TEXT,
            payment_method TEXT,
            payment_date TIMESTAMP NULL,
            updated_at TIMESTAMP NULL
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_ledger (
            booking_id TEXT NOT NULL,
            payment_seq INTEGER NOT NULL,
            payment_ref TEXT NOT NULL,
            hotel_name TEXT,
            payment_amount NUMERIC(12,2) NOT NULL,
            payment_method TEXT,
            payment_status TEXT DEFAULT 'received',
            payment_date TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            recorded_by TEXT,
            note TEXT,
            PRIMARY KEY (booking_id, payment_seq)
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_ledger_booking ON payment_ledger(booking_id);")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS aiosell_buffer_room_nights (
            booking_id VARCHAR(64),
            hotel_name VARCHAR(255),
            room_type VARCHAR(255),
            stay_date DATE,
            status VARCHAR(32) DEFAULT 'pending',
            PRIMARY KEY (booking_id, hotel_name, room_type, stay_date)
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hotel_room_code_mapping (
            hotel_code VARCHAR(64),
            room_code VARCHAR(64),
            hotel_name VARCHAR(255),
            room_type VARCHAR(255),
            PRIMARY KEY (hotel_code, room_code)
        );
    """)

    # --- HOUSEKEEPING TASKS TABLE ---
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS housekeeping_tasks (
            task_id VARCHAR PRIMARY KEY,
            room_type_id VARCHAR NOT NULL,
            assigned_to VARCHAR NULL,
            task_type VARCHAR,
            priority VARCHAR,
            task_status VARCHAR,
            task_description TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            estimated_duration INTEGER NULL,
            due_date DATE NULL,
            started_at TIMESTAMP NULL,
            completed_at TIMESTAMP NULL
        );
        """
    )

    # Ensure clean_since column exists in room_readiness_status
    try:
        cursor.execute("""
            ALTER TABLE room_readiness_status ADD COLUMN IF NOT EXISTS clean_since TIMESTAMP NULL;
        """)
    except Exception as e:
        print(f"Warning: Could not add clean_since column: {e}")
    # Ensure auto_dirty_threshold_days column exists
    try:
        cursor.execute("ALTER TABLE room_readiness_status ADD COLUMN IF NOT EXISTS auto_dirty_threshold_days INTEGER DEFAULT 3;")
    except Exception as e:
        print(f"Warning: Could not add auto_dirty_threshold_days column: {e}")
    # Drop old prepared_since column if exists

    conn.commit()
    cursor.close()
    conn.close()
    print("✅ PostgreSQL schema setup complete.")

    # Apply SQL index manifest if present
    try:
        manifest_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'indexes', 'postgres_index_manifest.sql')
        if not os.path.exists(manifest_path):
            manifest_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'indexes', 'postgres_index_manifest.sql')
    except Exception:
        manifest_path = None
    try:
        if manifest_path and os.path.exists(manifest_path):
            conn2 = get_postgresql_connection(); cur2 = conn2.cursor()
            with open(manifest_path, 'r', encoding='utf-8') as f:
                sql = f.read()
            cur2.execute(sql)
            conn2.commit(); cur2.close(); conn2.close()
            print("✅ PostgreSQL index manifest applied.")
    except Exception as e:
        print(f"Warning: Could not apply PostgreSQL index manifest: {e}")

if __name__ == "__main__":
    # CLI helper: python setup_schema.py [refresh_collation]
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("refresh_collation", "refresh-collation"):
        try:
            # Perform collation/version maintenance safely
            def _refresh_db_collation():
                conn = get_postgresql_connection()
                try:
                    # REINDEX CONCURRENTLY requires autocommit
                    conn.set_session(autocommit=True)
                    cur = conn.cursor()
                    try:
                        # Determine current database and collation
                        cur.execute("SELECT current_database(), (SELECT datcollate FROM pg_database WHERE datname = current_database())")
                        row = cur.fetchone()
                        dbname = row[0]
                        datcollate = row[1]
                        print(f"ℹ️ Collation maintenance for database '{dbname}' (datcollate={datcollate})")
                    except Exception as e:
                        dbname = None; datcollate = None
                        print(f"⚠️ Could not read current_database/datcollate: {e}")
                    # Reindex database objects using default collation
                    try:
                        if dbname:
                            cur.execute(f'REINDEX (CONCURRENTLY, VERBOSE) DATABASE "{dbname}"')
                            print("✅ REINDEX CONCURRENTLY completed (database)")
                    except Exception as e:
                        print(f"⚠️ REINDEX warning: {e}")
                    # Refresh database collation version (PostgreSQL 13+)
                    try:
                        if dbname:
                            cur.execute(f'ALTER DATABASE "{dbname}" REFRESH COLLATION VERSION')
                            print("✅ ALTER DATABASE REFRESH COLLATION VERSION applied")
                    except Exception as e:
                        print(f"⚠️ ALTER DATABASE REFRESH warning: {e}")
                        # Attempt to refresh the underlying collation as a fallback
                        try:
                            if datcollate:
                                cur.execute(f'ALTER COLLATION "{datcollate}" REFRESH VERSION')
                                print("✅ ALTER COLLATION REFRESH VERSION applied")
                        except Exception as e2:
                            print(f"⚠️ ALTER COLLATION REFRESH warning: {e2}")
                    try:
                        cur.close()
                    except Exception:
                        pass
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
            _refresh_db_collation()
        except Exception as e:
            print(f"❌ Collation maintenance failed: {e}")
    else:
        setup_schema()