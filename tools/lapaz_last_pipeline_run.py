from __future__ import annotations

from pathlib import Path

import psycopg


def _db_url_with_ssl(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if "sslmode=" in u.lower():
        return u
    return f"{u}{'&' if '?' in u else '?'}sslmode=require"


def main() -> int:
    db_url = Path(".supabase_database_url").read_text(encoding="utf-8").strip()
    if not db_url:
        raise SystemExit("Empty db url")
    db_url = _db_url_with_ssl(db_url)

    cols = [
        "run_started_at",
        "run_finished_at",
        "run_date",
        "total_records",
        "records_missing_document_id",
        "records_with_ocr",
        "records_used_groq",
        "records_with_trustor",
        "records_with_groq_error",
        "manual_review_true",
        "inserted_rows",
        "updated_rows",
        "llm_used_rows",
        "lookback_days",
        "workers",
        "ocr_limit",
        "strict_llm",
        "sanitization_disabled",
        "strict_valuation_disabled",
        "status",
    ]

    with psycopg.connect(db_url, connect_timeout=12) as conn:
        with conn.cursor() as cur:
            cur.execute("select " + ",".join(cols) + " from lapaz_pipeline_runs order by id desc limit 1;")
            row = cur.fetchone()

    if not row:
        print("no lapaz_pipeline_runs rows")
        return 0

    print("latest_lapaz_pipeline_runs")
    for k, v in zip(cols, row):
        print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
