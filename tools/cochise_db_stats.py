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
    ap = argparse.ArgumentParser(description="Show Cochise DB row counts and model distribution")
    ap.add_argument("--dotenv", default=".env", help="Optional .env file (default: .env)")
    ap.add_argument("--db-url", default="", help="Postgres connection string (defaults from DATABASE_URL)")
    args = ap.parse_args()

    _load_dotenv(REPO_ROOT / str(args.dotenv))
    db_url = (args.db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Missing --db-url (or DATABASE_URL)")

    with psycopg.connect(db_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) from public.cochise_leads;")
            leads = int(cur.fetchone()[0] or 0)
            cur.execute("select count(*) from public.cochise_pipeline_runs;")
            runs = int(cur.fetchone()[0] or 0)
            cur.execute("select count(*) from public.cochise_leads where coalesce(detail_url,'') <> '';")
            with_url = int(cur.fetchone()[0] or 0)
            cur.execute("select count(*) from public.cochise_leads where used_groq = true;")
            llm_used = int(cur.fetchone()[0] or 0)
            cur.execute(
                "select coalesce(groq_model,''), count(*) from public.cochise_leads group by groq_model order by count(*) desc;"
            )
            models = cur.fetchall() or []

    print(f"cochise_leads={leads}")
    print(f"pipeline_runs={runs}")
    print(f"detail_url_populated={with_url}")
    print(f"llm_used_rows={llm_used}")
    print("models=")
    for m, c in models:
        print(f"  {m or '(null)'}\t{int(c)}")


if __name__ == "__main__":
    main()
