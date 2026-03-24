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
    ap = argparse.ArgumentParser()
    ap.add_argument("pid", type=int)
    ap.add_argument("--dotenv", default=".env")
    ap.add_argument("--terminate", action="store_true", help="Use pg_terminate_backend instead of pg_cancel_backend")
    args = ap.parse_args()

    _load_dotenv(REPO_ROOT / str(args.dotenv))
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Missing DATABASE_URL")

    with psycopg.connect(db_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("select current_user, current_database()")
            print("whoami", cur.fetchone())

            cur.execute("select pid, usename, state, wait_event_type, wait_event from pg_stat_activity where pid = %s", (args.pid,))
            before = cur.fetchone()
            print("before", before)

            if args.terminate:
                cur.execute("select pg_terminate_backend(%s)", (args.pid,))
                terminated = cur.fetchone()[0]
                print("terminated", bool(terminated))
            else:
                cur.execute("select pg_cancel_backend(%s)", (args.pid,))
                canceled = cur.fetchone()[0]
                print("canceled", bool(canceled))

            cur.execute("select pid, usename, state, wait_event_type, wait_event from pg_stat_activity where pid = %s", (args.pid,))
            after = cur.fetchone()
            print("after", after)


if __name__ == "__main__":
    main()
