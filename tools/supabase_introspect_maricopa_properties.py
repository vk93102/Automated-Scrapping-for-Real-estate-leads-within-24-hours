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


def _kind(cur: psycopg.Cursor, name: str) -> tuple[str, str, str] | None:
    cur.execute(
        """
        select n.nspname, c.relname, c.relkind
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        where c.oid = to_regclass(%s)
        """,
        (name,),
    )
    row = cur.fetchone()
    return tuple(row) if row else None


def _cols(cur: psycopg.Cursor, schema: str, table: str) -> list[str]:
    cur.execute(
        """
        select column_name
        from information_schema.columns
        where table_schema = %s and table_name = %s
        order by ordinal_position
        """,
        (schema, table),
    )
    return [r[0] for r in (cur.fetchall() or [])]


def main() -> None:
    _load_dotenv(REPO_ROOT / ".env")
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Missing DATABASE_URL")

    schema = _schema_name()
    names = [
        "public.maricopa_properties",
        f"{schema}.maricopa_properties",
        f"{schema}.properties",
    ]

    with psycopg.connect(db_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("select current_user, current_database()")
            print("whoami", cur.fetchone())

            for nm in names:
                reg = None
                cur.execute("select to_regclass(%s)", (nm,))
                reg = cur.fetchone()[0]
                print("regclass", nm, reg)
                print("kind", nm, _kind(cur, nm))

                sch, tbl = nm.split(".", 1)
                cols = _cols(cur, sch, tbl) if reg else []
                print("cols_count", nm, len(cols))
                print("has_document_url", nm, ("document_url" in cols))


if __name__ == "__main__":
    main()
