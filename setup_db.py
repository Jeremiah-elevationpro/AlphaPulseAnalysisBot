"""
AlphaPulse - Database Setup Script
====================================
Run this ONCE before starting the bot to verify the database is ready.

  python setup_db.py

- Supabase mode (USE_SUPABASE=true): verifies REST connection and tables exist.
- PostgreSQL mode (USE_SUPABASE=false): creates the database and all tables.
"""

import sys
import os
from dotenv import load_dotenv

load_dotenv()

USE_SUPABASE = os.getenv("USE_SUPABASE", "false").lower() == "true"

print("AlphaPulse - Database Setup")
print("=" * 40)

# ─────────────────────────────────────────────
# SUPABASE PATH — REST API, no PostgreSQL port
# ─────────────────────────────────────────────
if USE_SUPABASE:
    print("\n[Mode] Supabase REST API")
    print("\n[1/1] Connecting to Supabase and verifying tables...")

    try:
        from db.database import Database
        db = Database()
        db.init()
        print("    [OK] Supabase connected — all tables verified.")
        db.close()
    except RuntimeError as e:
        # Tables not created yet
        print(f"\n[ERROR] {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Supabase connection failed: {e}")
        print()
        print("Check your .env file:")
        print("  SUPABASE_URL=https://vhgyaeqttybwyxwvkytj.supabase.co")
        print("  SUPABASE_KEY=sb_secret_...")
        print("  USE_SUPABASE=true")
        sys.exit(1)

# ─────────────────────────────────────────────
# POSTGRESQL PATH — direct psycopg2 connection
# ─────────────────────────────────────────────
else:
    DB_HOST     = os.getenv("DB_HOST", "localhost")
    DB_PORT     = int(os.getenv("DB_PORT", 5432))
    DB_NAME     = os.getenv("DB_NAME", "alphapulse")
    DB_USER     = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")

    print(f"\n[Mode] PostgreSQL direct — {DB_HOST}:{DB_PORT}/{DB_NAME}")
    print("\n[1/2] Creating database if not exists...")

    try:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname="postgres",
            user=DB_USER, password=DB_PASSWORD, connect_timeout=10,
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{DB_NAME}"')
            print(f"    Database '{DB_NAME}' created.")
        else:
            print(f"    Database '{DB_NAME}' already exists.")
        cur.close()
        conn.close()

    except Exception as e:
        print(f"\n[ERROR] Could not connect to PostgreSQL: {e}")
        print()
        print("Make sure PostgreSQL is running:")
        print("  Right-click SETUP_POSTGRES_PASSWORD.bat -> Run as administrator")
        sys.exit(1)

    print("\n[2/2] Creating tables...")
    try:
        from db.database import Database
        db = Database()
        db.init()
        print("    [OK] All tables created.")
        db.close()
    except Exception as e:
        print(f"\n[ERROR] Table creation failed: {e}")
        sys.exit(1)

print()
print("=" * 40)
print("Setup complete! You can now run: python main.py")
