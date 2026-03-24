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


def main() -> None:
    _load_dotenv(REPO_ROOT / ".env")
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Missing DATABASE_URL")

    with psycopg.connect(db_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("select current_user, current_database()")
            print("whoami", cur.fetchone())

            cur.execute("select to_regclass('public.maricopa_properties')")
            reg = cur.fetchone()[0]
            print("regclass", reg)

            cur.execute(
                """
                select n.nspname, c.relname, c.relkind
                from pg_class c
                join pg_namespace n on n.oid = c.relnamespace
                where c.oid = to_regclass('public.maricopa_properties')
                """
            )
            print("kind", cur.fetchone())

            cur.execute(
                """
                select column_name
                from information_schema.columns
                where table_schema = 'public' and table_name = 'maricopa_properties'
                order by ordinal_position
                """
            )
            cols = [r[0] for r in (cur.fetchall() or [])]
            print("cols_count", len(cols))
            print("cols", cols)

            cur.execute("select pg_get_viewdef('public.maricopa_properties'::regclass, true)")
            viewdef = cur.fetchone()[0]
            print("viewdef")
            print(viewdef)


if __name__ == "__main__":
    main()
