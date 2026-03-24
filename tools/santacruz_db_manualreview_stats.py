from __future__ import annotations

import argparse
from pathlib import Path

import psycopg


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db-url-file", default=".supabase_database_url")
    args = p.parse_args()

    db_url = Path(args.db_url_file).read_text(encoding="utf-8", errors="ignore").strip()
    if not db_url:
        raise SystemExit("Empty db url")

    if "sslmode=" not in db_url.lower():
        db_url = f"{db_url}{'&' if '?' in db_url else '?'}sslmode=require"

    with psycopg.connect(db_url, connect_timeout=12) as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) from santacruz_leads;")
            total = int(cur.fetchone()[0])
            cur.execute("select count(*) from santacruz_leads where coalesce(manual_review,false)=true;")
            flagged = int(cur.fetchone()[0])
            cur.execute(
                """
                select document_id, manual_review, manual_review_reasons
                from santacruz_leads
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
