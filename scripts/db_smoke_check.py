from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# Allow running from scripts/ directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from automation.maricopa_scraper.db_postgres import ensure_schema


def _get_db_url() -> str:
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("DATABASE_URL is not set")
    return db_url


def _q1(cur: psycopg.Cursor, sql: str, params: Optional[tuple] = None):
    cur.execute(sql, params or ())
    row = cur.fetchone()
    return row[0] if row else None


def main() -> None:
    db_url = _get_db_url()
    conn = psycopg.connect(db_url)
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            docs = _q1(cur, "select count(*) from documents")
            props = _q1(cur, "select count(*) from properties")
            fails = _q1(cur, "select count(*) from scrape_failures")
            processed = _q1(cur, "select count(*) from documents where last_processed_at is not null")

            print(f"documents_count={docs}")
            print(f"properties_count={props}")
            print(f"failures_count={fails}")
            print(f"processed_count={processed}")

            cur.execute(
                """
                select recording_number, recording_date, document_type, ocr_text_path, last_processed_at
                from documents
                order by created_at desc
                limit 5;
                """
            )
            rows = cur.fetchall() or []
            print("latest_documents=")
            for r in rows:
                print("  ", r)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
