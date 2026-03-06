#!/usr/bin/env python3
"""Print recent rows from cron_jobs for quick debugging."""
import os
import sys
import psycopg

from pathlib import Path

# Load .env if present
env = Path(__file__).resolve().parents[1] / '.env'
if env.exists():
    for line in env.read_text().splitlines():
        if '=' in line and not line.strip().startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print('DATABASE_URL missing; set it in environment or .env')
    sys.exit(1)

with psycopg.connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "select run_id, job_name, status, total_found, total_processed, total_skipped, total_failed, started_at, finished_at, last_updated_at from cron_jobs order by started_at desc limit 50"
        )
        rows = cur.fetchall()
        if not rows:
            print('No cron_jobs rows found')
            sys.exit(0)
        for r in rows:
            print(r)
