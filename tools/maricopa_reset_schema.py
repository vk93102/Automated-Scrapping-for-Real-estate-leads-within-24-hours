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
    ap = argparse.ArgumentParser(description="Drop + recreate Maricopa DB schema (safe for shared DB)")
    ap.add_argument("--dotenv", default=".env", help="Optional .env file (default: .env)")
    ap.add_argument("--db-url", default="", help="Postgres connection string (defaults from DATABASE_URL)")
    ap.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt (DANGEROUS: deletes schema)",
    )
    args = ap.parse_args()

    _load_dotenv(REPO_ROOT / str(args.dotenv))
    db_url = args.db_url or os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise SystemExit("Missing --db-url (or DATABASE_URL)")

    schema = _schema_name()

    if not args.yes:
        resp = input(f"This will DROP SCHEMA {schema} CASCADE. Type '{schema}' to confirm: ").strip()
        if resp != schema:
            raise SystemExit("Cancelled")

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(f"drop schema if exists {schema} cascade;")
            cur.execute(f"create schema {schema};")
        conn.commit()

    # Recreate tables/views inside the schema
    from maricopa.db_postgres import connect, ensure_schema

    conn2 = connect(db_url)
    try:
        ensure_schema(conn2)
    finally:
        conn2.close()

    print(f"reset_schema_ok={schema}")


if __name__ == "__main__":
    main()
