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


def _count(cur: psycopg.Cursor, table: str) -> int:
    cur.execute(f"select count(*) from {table};")
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def main() -> int:
    p = argparse.ArgumentParser(description="Purge La Paz tables from Supabase/Postgres")
    p.add_argument("--db-url-file", default=".supabase_database_url")
    p.add_argument("--apply", action="store_true", help="Actually delete rows (default is dry-run)")
    p.add_argument(
        "--keep-pipeline-runs",
        action="store_true",
        help="Do not clear lapaz_pipeline_runs (default clears it)",
    )
    args = p.parse_args()

    db_url = Path(args.db_url_file).read_text(encoding="utf-8", errors="ignore").strip()
    if not db_url:
        raise SystemExit("Empty db url")

    db_url = _db_url_with_ssl(db_url)

    with psycopg.connect(db_url, connect_timeout=12) as conn:
        with conn.cursor() as cur:
            leads_before = _count(cur, "lapaz_leads")
            runs_before = _count(cur, "lapaz_pipeline_runs")

            print(f"lapaz_leads_before={leads_before}")
            print(f"lapaz_pipeline_runs_before={runs_before}")
            print(f"mode={'APPLY' if args.apply else 'DRY_RUN'}")

            if not args.apply:
                return 0

            # Prefer TRUNCATE for speed + identity reset. Fall back to DELETE if permissions block TRUNCATE.
            try:
                if args.keep_pipeline_runs:
                    cur.execute("truncate table lapaz_leads restart identity;")
                else:
                    cur.execute("truncate table lapaz_leads, lapaz_pipeline_runs restart identity;")
            except Exception:
                cur.execute("delete from lapaz_leads;")
                if not args.keep_pipeline_runs:
                    cur.execute("delete from lapaz_pipeline_runs;")

        conn.commit()

        with conn.cursor() as cur:
            leads_after = _count(cur, "lapaz_leads")
            runs_after = _count(cur, "lapaz_pipeline_runs")

    print(f"lapaz_leads_after={leads_after}")
    print(f"lapaz_pipeline_runs_after={runs_after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
