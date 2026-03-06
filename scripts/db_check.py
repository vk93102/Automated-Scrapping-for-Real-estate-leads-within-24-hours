#!/usr/bin/env python3
import os, sys
from maricopa_scraper import db_postgres

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print('DATABASE_URL missing')
    sys.exit(1)
conn = db_postgres.connect(DATABASE_URL)
with conn.cursor() as cur:
    cur.execute("select count(*) from documents where recording_date = %s", ('2026-03-05',))
    print('documents with recording_date=2026-03-05:', cur.fetchone()[0])
    cur.execute("select count(*) from documents where created_at >= now() - interval '2 days'")
    print('documents created in last 2 days:', cur.fetchone()[0])
    cur.execute("select count(*) from properties where created_at >= now() - interval '2 days'")
    print('properties created in last 2 days:', cur.fetchone()[0])
    cur.execute("select run_id, status, total_found, total_processed, total_skipped, started_at from cron_jobs order by started_at desc limit 5")
    rows = cur.fetchall()
    print('recent cron_jobs (limit 5):')
    for r in rows:
        print(r)
conn.close()
