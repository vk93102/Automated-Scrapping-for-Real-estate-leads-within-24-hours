from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from greenlee.extractor import _compute_manual_review


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
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    db_url = Path(args.db_url_file).read_text(encoding="utf-8", errors="ignore").strip()
    if not db_url:
        raise SystemExit("Empty db url")

    db_url = _db_url_with_ssl(db_url)

    updated = 0
    with psycopg.connect(db_url, connect_timeout=12) as conn:
        _ensure_schema(conn)

        with conn.cursor() as cur:
            cur.execute("select document_id, raw_record from lapaz_leads;")
            rows = cur.fetchall() or []

        with conn.cursor() as cur:
            for document_id, raw_record in rows:
                raw = raw_record or {}
                manual, reasons, summary, context = _compute_manual_review(raw, merged_text="")

                # Keep the API (which reads from raw_record) consistent with the
                # materialized columns.
                raw["manualReview"] = bool(manual)
                raw["manualReviewReasons"] = reasons
                raw["manualReviewSummary"] = summary
                raw["manualReviewContext"] = context

                if args.dry_run:
                    continue

                cur.execute(
                    """
                    update lapaz_leads
                    set manual_review=%s,
                        manual_review_reasons=%s,
                        manual_review_summary=%s,
                        manual_review_context=%s,
                        raw_record=%s,
                        updated_at=now()
                    where document_id=%s;
                    """,
                    (manual, reasons, summary, context, psycopg.types.json.Jsonb(raw), str(document_id)),
                )
                updated += 1

        if not args.dry_run:
            conn.commit()

    print(f"rows_total={len(rows)}")
    print(f"rows_updated={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
