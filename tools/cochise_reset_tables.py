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


def main() -> None:
    ap = argparse.ArgumentParser(description="Delete Cochise rows from DB (Supabase-safe)")
    ap.add_argument("--dotenv", default=".env", help="Optional .env file (default: .env)")
    ap.add_argument("--db-url", default="", help="Postgres connection string (defaults from DATABASE_URL)")
    ap.add_argument("--yes", action="store_true", help="Skip confirmation")
    args = ap.parse_args()

    _load_dotenv(REPO_ROOT / str(args.dotenv))
    db_url = (args.db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Missing --db-url (or DATABASE_URL)")

    if not args.yes:
        resp = input("This will TRUNCATE public.cochise_leads and public.cochise_pipeline_runs. Type 'COCHISE' to confirm: ").strip()
        if resp != "COCHISE":
            raise SystemExit("Cancelled")

    with psycopg.connect(db_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("set local lock_timeout = '30s';")
            cur.execute("set local statement_timeout = '2min';")
            cur.execute("truncate table public.cochise_leads restart identity;")
            cur.execute("truncate table public.cochise_pipeline_runs restart identity;")
        conn.commit()

    print("reset_ok=true")


if __name__ == "__main__":
    main()
