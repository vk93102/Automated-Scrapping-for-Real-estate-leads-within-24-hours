from __future__ import annotations

import argparse
import os
import re
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


def _schema_name() -> str:
    raw = (os.environ.get("MARICOPA_DB_SCHEMA") or "maricopa").strip()
    if not raw:
        return "maricopa"
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", raw):
        raise SystemExit(f"Invalid MARICOPA_DB_SCHEMA: {raw!r}")
    return raw


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill Maricopa properties.document_url from recording_number")
    ap.add_argument("--dotenv", default=".env", help="Optional .env file (default: .env)")
    ap.add_argument("--db-url", default="", help="Postgres connection string (defaults from DATABASE_URL)")
    ap.add_argument(
        "--only-missing",
        action="store_true",
        help="Only set document_url where it's NULL/blank (default: true)",
    )
    args = ap.parse_args()

    _load_dotenv(REPO_ROOT / str(args.dotenv))
    db_url = (args.db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Missing --db-url (or DATABASE_URL)")

    schema = _schema_name()

    # Stable preview endpoint URL.
    url_prefix = "https://publicapi.recorder.maricopa.gov/preview/pdf?recordingNumber="
    url_suffix = "&suffix="

    where_clause = "and (p.document_url is null or btrim(p.document_url) = '')"

    batch_sql = f"""
    with to_update as (
        select p.id as prop_id, d.recording_number
        from properties p
        join documents d on d.id = p.document_id
        where 1=1
        {where_clause}
        limit %s
    )
    update properties p
    set document_url = %s || to_update.recording_number || %s
    from to_update
    where p.id = to_update.prop_id
    """

    updated_total = 0
    with psycopg.connect(db_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute(f"set search_path to {schema}, public;")
            # Supabase/project DBs often have aggressive default timeouts.
            cur.execute("set local statement_timeout = '5min';")

            while True:
                cur.execute(batch_sql, (500, url_prefix, url_suffix))
                n = int(cur.rowcount)
                updated_total += max(0, n)
                if n <= 0:
                    break
        conn.commit()

    print(f"schema={schema}")
    print(f"rows_updated={updated_total}")


if __name__ == "__main__":
    main()
