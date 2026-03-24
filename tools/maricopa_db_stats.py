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
    ap = argparse.ArgumentParser(description="Show Maricopa DB row counts and model distribution")
    ap.add_argument("--dotenv", default=".env", help="Optional .env file (default: .env)")
    ap.add_argument("--db-url", default="", help="Postgres connection string (defaults from DATABASE_URL)")
    args = ap.parse_args()

    _load_dotenv(REPO_ROOT / str(args.dotenv))
    db_url = (args.db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Missing --db-url (or DATABASE_URL)")

    schema = _schema_name()

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(f"set search_path to {schema}, public;")
            cur.execute("select count(*) from documents;")
            docs = int(cur.fetchone()[0])
            cur.execute("select count(*) from properties;")
            props = int(cur.fetchone()[0])
            cur.execute("select count(*) from discovered_recordings;")
            disc = int(cur.fetchone()[0])
            cur.execute("select count(*) from cron_jobs;")
            jobs = int(cur.fetchone()[0])

            cur.execute(
                "select count(*) from properties where document_url is not null and btrim(document_url) <> '';"
            )
            doc_urls = int(cur.fetchone()[0])

            cur.execute(
                """
                select count(*)
                from properties
                where (trustor_1_full_name is null or btrim(trustor_1_full_name) = '')
                  and (trustor_2_full_name is null or btrim(trustor_2_full_name) = '')
                  and (property_address is null or btrim(property_address) = '');
                """
            )
            missing_ta = int(cur.fetchone()[0])

            cur.execute(
                """
                select count(*)
                from properties
                where (trustor_1_full_name is null or btrim(trustor_1_full_name) = '')
                  and (trustor_2_full_name is null or btrim(trustor_2_full_name) = '')
                  and (property_address is null or btrim(property_address) = '')
                  and (document_url is not null and btrim(document_url) <> '');
                """
            )
            missing_ta_with_url = int(cur.fetchone()[0])

            cur.execute(
                """
                select count(*)
                from properties
                where (trustor_1_full_name is null or btrim(trustor_1_full_name) = '')
                  and (trustor_2_full_name is null or btrim(trustor_2_full_name) = '')
                  and (property_address is null or btrim(property_address) = '')
                  and (document_url is null or btrim(document_url) = '');
                """
            )
            missing_ta_without_url = int(cur.fetchone()[0])

            cur.execute(
                "select coalesce(llm_model, ''), count(*) from properties group by llm_model order by count(*) desc;"
            )
            models = cur.fetchall() or []

    print(f"schema={schema}")
    print(f"documents={docs}")
    print(f"properties={props}")
    print(f"discovered_recordings={disc}")
    print(f"cron_jobs={jobs}")
    print(f"document_urls_populated={doc_urls}")
    print(f"missing_trustors_and_address={missing_ta}")
    print(f"missing_trustors_and_address_with_document_url={missing_ta_with_url}")
    print(f"missing_trustors_and_address_without_document_url={missing_ta_without_url}")
    print("models=")
    for m, c in models:
        print(f"  {m or '(null)'}\t{c}")


if __name__ == "__main__":
    main()
