#!/usr/bin/env python3
"""
Cleanup script for Maricopa database - remove all existing records.
"""
import os
from pathlib import Path

# Load environment variables
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    with open(env_file, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                # Remove quotes if present
                if val.startswith('"') and val.endswith('"'):
                    val = val[1:-1]
                os.environ.setdefault(key, val)

db_url = os.environ.get("DATABASE_URL")
if not db_url:
    print("❌ DATABASE_URL not found in environment")
    exit(1)

import psycopg

try:
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            # Get count before
            cur.execute("SELECT COUNT(*) FROM documents;")
            before = int(cur.fetchone()[0])
            print(f"📊 Document records before cleanup: {before}")
            
            # Delete all records (cascade will handle properties)
            cur.execute("DELETE FROM documents;")
            
            # Verify
            cur.execute("SELECT COUNT(*) FROM documents;")
            after = int(cur.fetchone()[0])
            print(f"✅ Document records after cleanup: {after}")
            
            conn.commit()
            print("✅ Database cleanup complete!")
            
except Exception as e:
    print(f"❌ Cleanup failed: {e}")
    exit(1)
