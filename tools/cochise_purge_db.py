from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip()
        if v.startswith('"') and v.endswith('"') and len(v) >= 2:
            v = v[1:-1]
        os.environ.setdefault(k, v)


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
    ap = argparse.ArgumentParser(description="Purge Cochise tables from Supabase/Postgres")
    ap.add_argument("--dotenv", default=".env", help="Optional .env file (default: .env)")
    ap.add_argument("--db-url", default="", help="Override DATABASE_URL")
    ap.add_argument("--apply", action="store_true", help="Actually delete rows (default is dry-run)")
    ap.add_argument(
        "--keep-pipeline-runs",
        action="store_true",
        help="Do not clear cochise_pipeline_runs (default clears it)",
    )
    args = ap.parse_args()

    _load_dotenv(REPO_ROOT / str(args.dotenv))
    db_url = (args.db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Missing --db-url (or DATABASE_URL)")
    db_url = _db_url_with_ssl(db_url)

    with psycopg.connect(db_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            leads_before = _count(cur, "cochise_leads")
            runs_before = _count(cur, "cochise_pipeline_runs")

            print(f"cochise_leads_before={leads_before}")
            print(f"cochise_pipeline_runs_before={runs_before}")
            print(f"mode={'APPLY' if args.apply else 'DRY_RUN'}")

            if not args.apply:
                return 0

            cur.execute("set local lock_timeout = '30s';")
            cur.execute("set local statement_timeout = '2min';")

            try:
                if args.keep_pipeline_runs:
                    cur.execute("truncate table cochise_leads restart identity;")
                else:
                    cur.execute("truncate table cochise_leads, cochise_pipeline_runs restart identity;")
            except Exception:
                cur.execute("delete from cochise_leads;")
                if not args.keep_pipeline_runs:
                    cur.execute("delete from cochise_pipeline_runs;")

        conn.commit()

        with conn.cursor() as cur:
            leads_after = _count(cur, "cochise_leads")
            runs_after = _count(cur, "cochise_pipeline_runs")

    print(f"cochise_leads_after={leads_after}")
    print(f"cochise_pipeline_runs_after={runs_after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
