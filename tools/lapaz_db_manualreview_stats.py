from __future__ import annotations

import argparse
from pathlib import Path

import psycopg


def _db_url_with_ssl(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if "sslmode=" in u.lower():
        return u
    return f"{u}{'&' if '?' in u else '?'}sslmode=require"


def _ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("alter table lapaz_leads add column if not exists manual_review boolean;")
        cur.execute("alter table lapaz_leads add column if not exists manual_review_reasons text;")
        cur.execute("alter table lapaz_leads add column if not exists manual_review_summary text;")
        cur.execute("alter table lapaz_leads add column if not exists manual_review_context text;")
    conn.commit()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db-url-file", default=".supabase_database_url")
    args = p.parse_args()

    db_url = Path(args.db_url_file).read_text(encoding="utf-8", errors="ignore").strip()
    if not db_url:
        raise SystemExit("Empty db url")

    db_url = _db_url_with_ssl(db_url)

    with psycopg.connect(db_url, connect_timeout=12) as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("select count(*) from lapaz_leads;")
            total = int(cur.fetchone()[0])
            cur.execute("select count(*) from lapaz_leads where coalesce(manual_review,false)=true;")
            flagged = int(cur.fetchone()[0])
            cur.execute(
                """
                select document_id, manual_review, manual_review_reasons
                from lapaz_leads
                where coalesce(manual_review,false)=true
                order by updated_at desc
                limit 1;
                """
            )
            sample = cur.fetchone()

    print(f"total={total}")
    print(f"manual_review_true={flagged}")
    if sample:
        print(f"sample_document_id={sample[0]}")
        print(f"sample_manual_review={sample[1]}")
        print(f"sample_reasons={sample[2]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
