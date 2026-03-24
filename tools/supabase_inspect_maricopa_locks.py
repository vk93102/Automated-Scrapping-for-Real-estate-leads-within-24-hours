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
    schema = (os.environ.get("MARICOPA_DB_SCHEMA") or "maricopa").strip() or "maricopa"
    if not db_url:
        raise SystemExit("Missing DATABASE_URL")

    with psycopg.connect(db_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("select current_user, current_database()")
            print("whoami", cur.fetchone())

            cur.execute("select to_regclass(%s)", (f"{schema}.properties",))
            reg = cur.fetchone()[0]
            print("properties_regclass", reg)

            # Active sessions (may be restricted on some hosted DBs)
            try:
                cur.execute(
                    """
                    select pid, usename, state, wait_event_type, wait_event,
                           now() - query_start as age,
                           left(replace(query, '\n', ' '), 200) as query
                    from pg_stat_activity
                    where datname = current_database()
                      and state <> 'idle'
                    order by query_start asc
                    limit 25
                    """
                )
                rows = cur.fetchall() or []
                print("active_sessions", len(rows))
                for r in rows:
                    print(" ", r)
            except Exception as e:
                print("pg_stat_activity_error", type(e).__name__, str(e).split("\n", 1)[0])

            # Locks on the properties table
            try:
                cur.execute(
                    """
                    select l.pid, l.locktype, l.mode, l.granted,
                           a.state, a.wait_event_type, a.wait_event,
                           left(replace(a.query, '\n', ' '), 200) as query
                    from pg_locks l
                    left join pg_stat_activity a on a.pid = l.pid
                    where l.relation = to_regclass(%s)
                    order by l.granted desc, l.mode
                    """ ,
                    (f"{schema}.properties",),
                )
                rows = cur.fetchall() or []
                print("properties_locks", len(rows))
                for r in rows:
                    print(" ", r)
            except Exception as e:
                print("pg_locks_error", type(e).__name__, str(e).split("\n", 1)[0])


if __name__ == "__main__":
    main()
