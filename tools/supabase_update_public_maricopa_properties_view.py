from __future__ import annotations

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


def _schema_name() -> str:
    s = (os.environ.get("MARICOPA_DB_SCHEMA") or "maricopa").strip()
    return s or "maricopa"


def main() -> None:
    _load_dotenv(REPO_ROOT / ".env")
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Missing DATABASE_URL")

    schema = _schema_name()
    src = f"{schema}.properties"

    with psycopg.connect(db_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("select current_user, current_database()")
            print("whoami", cur.fetchone())

            # Keep DDL from hanging forever if something is odd.
            cur.execute("set local lock_timeout = '30s';")
            cur.execute("set local statement_timeout = '2min';")

            cur.execute("select to_regclass('public.maricopa_properties')")
            exists = cur.fetchone()[0] is not None
            print("public_view_exists", bool(exists))

            # Replace view to expose ALL columns from the canonical table (including document_url).
            # This keeps the source of truth in the county schema, while making public access easy.
            cur.execute(f"create or replace view public.maricopa_properties as select * from {src};")
            conn.commit()
            print("updated_view", True)

            cur.execute(
                """
                select count(*)
                from information_schema.columns
                where table_schema = 'public'
                  and table_name = 'maricopa_properties'
                  and column_name = 'document_url'
                """
            )
            has_col = int(cur.fetchone()[0] or 0) > 0
            print("has_document_url", bool(has_col))


if __name__ == "__main__":
    main()
