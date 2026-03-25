#!/usr/bin/env python3
import os
import sys
sys.path.insert(0, "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours")

# Load environment
try:
    from cochise.run_cochise_20day_simple import _load_local_env
    _load_local_env()
except:
    from pathlib import Path
    env_file = Path("/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/.env")
    if env_file.exists():
        for line in env_file.read_text().split("\n"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

db_url = os.environ.get("DATABASE_URL", "").strip()
print(f"DATABASE_URL: {db_url or 'NOT SET'}\n")

if db_url:
    import psycopg
    try:
        conn = psycopg.connect(db_url, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cochise_leads")
            count = cur.fetchone()[0]
            print(f"✅ Connected successfully!")
            print(f"✅ cochise_leads table has {count} rows")
        conn.close()
    except Exception as e:
        print(f"❌ Failed: {e}")
