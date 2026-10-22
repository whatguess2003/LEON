"""
CHECK DATABASE TIMESTAMP SKEW

READ-ONLY UTILITY: Compares application wall clock with PostgreSQL and MongoDB server clocks.

Environment:
- DATABASE_URL (PostgreSQL)
- MONGO_URL (MongoDB) optional

Outputs:
- App local and UTC times
- PostgreSQL now()/statement_timestamp()/clock_timestamp() and timezone
- Epoch-based skew comparisons (db vs app)
- MongoDB serverStatus.localTime and skew (if available)
"""

import os
import sys
import time
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


def load_env_vars() -> None:
    if load_dotenv is not None:
        try:
            load_dotenv()
        except Exception:
            pass


def print_header(title: str) -> None:
    print("\n== {} ==".format(title))


def check_app_clock() -> None:
    print_header("APP CLOCK")
    app_local = datetime.now()
    app_utc = datetime.now(timezone.utc)
    print(f"APP_LOCAL: {app_local.isoformat(sep=' ', timespec='seconds')}")
    print(f"APP_UTC  : {app_utc.isoformat(sep=' ', timespec='seconds')}")


def check_postgres_clock(database_url: str) -> None:
    import psycopg2
    print_header("POSTGRES CLOCK")
    if not database_url:
        print("DATABASE_URL not set; skipping Postgres check")
        return
    conn = None
    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                now()                          AS txn_start_time,
                statement_timestamp()          AS statement_time,
                clock_timestamp()              AS wall_clock_time,
                clock_timestamp() - now()      AS skew_interval,
                current_setting('TimeZone')    AS timezone
            """
        )
        row = cur.fetchone()
        txn_start, stmt_time, wall_clock, skew_interval, tz = row
        print(f"PG_TIMEZONE       : {tz}")
        print(f"PG_TXN_START_TIME : {txn_start}")
        print(f"PG_STATEMENT_TIME : {stmt_time}")
        print(f"PG_WALL_CLOCK_TIME: {wall_clock}")
        print(f"PG_SKEW_INTERVAL  : {skew_interval}")

        # Epoch-based comparison to avoid tz ambiguity
        cur.execute(
            """
            SELECT
                EXTRACT(EPOCH FROM now())::float8 AS epoch_now,
                EXTRACT(EPOCH FROM statement_timestamp())::float8 AS epoch_stmt,
                EXTRACT(EPOCH FROM clock_timestamp())::float8 AS epoch_clock
            """
        )
        e_now, e_stmt, e_clock = cur.fetchone()
        app_epoch = time.time()
        print(f"PG_EPOCH_NOW      : {e_now:.3f}")
        print(f"PG_EPOCH_STMT     : {e_stmt:.3f}")
        print(f"PG_EPOCH_CLOCK    : {e_clock:.3f}")
        print(f"APP_EPOCH         : {app_epoch:.3f}")
        print(f"SKEW_PG_CLOCK-APP : {e_clock - app_epoch:+.3f} s")
        print(f"SKEW_PG_NOW-APP   : {e_now - app_epoch:+.3f} s  (remember: now() is fixed at txn start)")
    except Exception as e:
        print(f"❌ Postgres check failed: {e}")
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def check_mongo_clock(mongo_url: str) -> None:
    print_header("MONGODB CLOCK")
    if not mongo_url:
        print("MONGO_URL not set; skipping MongoDB check")
        return
    try:
        from pymongo import MongoClient
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=3000)
        admin = client.admin
        status = admin.command({"serverStatus": 1})
        local_time = status.get("localTime")
        app_utc = datetime.now(timezone.utc)
        print(f"MONGO_LOCAL_TIME  : {local_time}")
        print(f"APP_UTC           : {app_utc.isoformat(sep=' ', timespec='seconds')}")
        try:
            # Compute skew in seconds if local_time is datetime
            if isinstance(local_time, datetime):
                # Ensure tz-aware for delta computation
                if local_time.tzinfo is None:
                    local_time = local_time.replace(tzinfo=timezone.utc)
                skew = (local_time - app_utc).total_seconds()
                print(f"SKEW_MONGO-APP    : {skew:+.3f} s")
        except Exception:
            pass
        try:
            tz = status.get("process")  # not timezone, but helpful metadata
            print(f"MONGO_PROCESS     : {tz}")
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass
    except Exception as e:
        print(f"❌ MongoDB check failed: {e}")


def main() -> int:
    load_env_vars()
    database_url = os.getenv("DATABASE_URL", "").strip()
    mongo_url = os.getenv("MONGO_URL", "").strip()

    print_header("ENV SUMMARY")
    print(f"HAS_DATABASE_URL: {'YES' if database_url else 'NO'}")
    print(f"HAS_MONGO_URL   : {'YES' if mongo_url else 'NO'}")

    check_app_clock()
    check_postgres_clock(database_url)
    check_mongo_clock(mongo_url)

    print("\nTIP: For exact event timestamps in Postgres, prefer clock_timestamp() over CURRENT_TIMESTAMP when needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


