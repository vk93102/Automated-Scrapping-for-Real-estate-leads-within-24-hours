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


def _schema_name() -> str:
    s = (os.environ.get("MARICOPA_DB_SCHEMA") or "maricopa").strip()
    return s or "maricopa"


def main() -> None:
    ap = argparse.ArgumentParser(description="List Maricopa documents missing a properties row")
    ap.add_argument("--dotenv", default=".env")
    ap.add_argument("--db-url", default="")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    _load_dotenv(REPO_ROOT / str(args.dotenv))
    db_url = (args.db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Missing DATABASE_URL")
    schema = _schema_name()

    with psycopg.connect(db_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute(f"set search_path to {schema}, public;")
            cur.execute(
                """
                select d.recording_number
                from documents d
                left join properties p on p.document_id = d.id
                where p.document_id is null
                order by d.recording_date desc nulls last
                limit %s
                """,
                (int(args.limit or 100),),
            )
            rows = cur.fetchall() or []

    print(f"schema={schema}")
    print(f"missing_properties_count={len(rows)}")
    for (rn,) in rows:
        if rn:
            print(str(rn).strip())


if __name__ == "__main__":
    main()
