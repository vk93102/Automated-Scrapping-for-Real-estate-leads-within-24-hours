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
    ap = argparse.ArgumentParser(description="Add properties.document_url column (idempotent)")
    ap.add_argument("--dotenv", default=".env")
    ap.add_argument("--db-url", default="")
    ap.add_argument("--connect-timeout", type=int, default=20)
    args = ap.parse_args()

    _load_dotenv(REPO_ROOT / str(args.dotenv))
    db_url = (args.db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Missing --db-url (or DATABASE_URL)")

    schema = _schema_name()

    print(f"schema={schema}", flush=True)

    try:
        with psycopg.connect(db_url, connect_timeout=int(args.connect_timeout)) as conn:
            with conn.cursor() as cur:
                cur.execute(f"set search_path to {schema}, public;")
                # Prevent indefinite waits on Supabase pooler / locks.
                cur.execute("set local lock_timeout = '2min';")
                cur.execute("set local statement_timeout = '5min';")
                print("alter_table_start=true", flush=True)
                cur.execute("alter table properties add column if not exists document_url text;")
                print("alter_table_done=true", flush=True)

                cur.execute(
                    """
                    select 1
                    from information_schema.columns
                    where table_schema = %s
                      and table_name = 'properties'
                      and column_name = 'document_url'
                    limit 1
                    """,
                    (schema,),
                )
                has_col = cur.fetchone() is not None
            conn.commit()
    except Exception as e:
        print(f"error_type={type(e).__name__}")
        print(f"error={e}")
        raise

    print(f"schema={schema}")
    print(f"has_document_url_column={str(bool(has_col)).lower()}")


if __name__ == "__main__":
    main()
