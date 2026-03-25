#!/usr/bin/env python3
"""Monitor Coconino Supabase tables.

Prints a compact status summary from:
- public.conino_leads
- public.conino_pipeline_runs

Usage examples:
  python conino/db_monitor.py --days 14
  python conino/db_monitor.py --days 14 --show-leads 20 --watch --interval 30

Env:
- DATABASE_URL (or present in .env at repo root)
- DATABASE_URL_POOLER (optional fallback)
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from pathlib import Path

import psycopg


ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        os.environ.setdefault(k, v)


def _db_url_with_ssl(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if "sslmode=" in u.lower():
        return u
    return f"{u}{'&' if '?' in u else '?'}sslmode=require"


def _connect_db(database_url: str) -> psycopg.Connection:
    primary_url = _db_url_with_ssl(database_url)
    fallback_raw = (os.environ.get("DATABASE_URL_POOLER") or "").strip()
    fallback_url = _db_url_with_ssl(fallback_raw) if fallback_raw else ""

    last_exc: Exception | None = None
    for url in [u for u in [primary_url, fallback_url] if u]:
        try:
            return psycopg.connect(url, connect_timeout=12)
        except Exception as exc:
            last_exc = exc

    raise RuntimeError(f"DB connect failed: {last_exc}")


def _fetch_one(cur: psycopg.Cursor, sql: str, params: tuple = ()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def print_status(*, days: int, run_limit: int, doc_type_limit: int, show_leads: int) -> None:
    _load_env()
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is missing (set it or add to .env)")

    with _connect_db(db_url) as conn:
        with conn.cursor() as cur:
            total_leads = _fetch_one(cur, "select count(*) from public.conino_leads")
            total_runs = _fetch_one(cur, "select count(*) from public.conino_pipeline_runs")

            print(f"asof={datetime.now():%Y-%m-%d %H:%M:%S}")
            print(f"conino_leads_total={total_leads} conino_pipeline_runs_total={total_runs}")

            print("\nleads_by_run_date:")
            cur.execute(
                """
                select run_date, count(*)
                from public.conino_leads
                where run_date >= (current_date - %s::int)
                group by run_date
                order by run_date desc;
                """,
                (max(0, int(days)),),
            )
            rows = cur.fetchall()
            if not rows:
                print("(none)")
            else:
                for run_date, cnt in rows:
                    print(f"{run_date} count={cnt}")

            print("\nrecent_runs:")
            cur.execute(
                """
                select id, run_date, total_records, inserted_rows, updated_rows, llm_used_rows,
                       status, run_started_at, run_finished_at,
                       left(coalesce(error_message,''), 180)
                from public.conino_pipeline_runs
                order by id desc
                limit %s;
                """,
                (max(1, int(run_limit)),),
            )
            run_rows = cur.fetchall()
            if not run_rows:
                print("(none)")
            else:
                for (
                    run_id,
                    run_date,
                    total_records,
                    inserted_rows,
                    updated_rows,
                    llm_used_rows,
                    status,
                    started_at,
                    finished_at,
                    err_prefix,
                ) in run_rows:
                    finished = finished_at.isoformat(timespec="seconds") if finished_at else ""
                    print(
                        " ".join(
                            [
                                f"id={run_id}",
                                f"date={run_date}",
                                f"total={total_records}",
                                f"ins={inserted_rows}",
                                f"upd={updated_rows}",
                                f"llm={llm_used_rows}",
                                f"status={status}",
                                f"started={started_at.isoformat(timespec='seconds')}",
                                f"finished={finished}",
                                (f"err={err_prefix}" if err_prefix else ""),
                            ]
                        ).strip()
                    )

            print("\ndoc_types_last_window:")
            cur.execute(
                """
                select coalesce(nullif(trim(document_type), ''), '(blank)') as dt, count(*)
                from public.conino_leads
                where run_date >= (current_date - %s::int)
                group by dt
                order by count(*) desc
                limit %s;
                """,
                (max(0, int(days)), max(1, int(doc_type_limit))),
            )
            dt_rows = cur.fetchall()
            if not dt_rows:
                print("(none)")
            else:
                for dt, cnt in dt_rows:
                    print(f"{cnt:>5}  {dt}")

            if int(show_leads or 0) > 0:
                print("\nrecent_leads:")
                cur.execute(
                    """
                    select
                        document_id,
                        recording_number,
                        coalesce(nullif(trim(document_type), ''), '(blank)') as document_type,
                        run_date,
                        nullif(trim(recording_date), '') as recording_date,
                        nullif(trim(property_address), '') as property_address,
                        nullif(trim(detail_url), '') as detail_url
                    from public.conino_leads
                    where run_date >= (current_date - %s::int)
                    order by updated_at desc, created_at desc
                    limit %s;
                    """,
                    (max(0, int(days)), max(1, int(show_leads))),
                )
                lead_rows = cur.fetchall()
                if not lead_rows:
                    print("(none)")
                else:
                    for (
                        document_id,
                        recording_number,
                        document_type,
                        run_date,
                        recording_date,
                        property_address,
                        detail_url,
                    ) in lead_rows:
                        parts = [
                            f"doc={document_id}",
                            (f"rec={recording_number}" if recording_number else ""),
                            f"type={document_type}",
                            f"run_date={run_date}",
                            (f"rec_date={recording_date}" if recording_date else ""),
                            (f"addr={property_address}" if property_address else ""),
                            (f"url={detail_url}" if detail_url else ""),
                        ]
                        print(" ".join(p for p in parts if p))


def main() -> None:
    p = argparse.ArgumentParser(description="Monitor Coconino Supabase tables")
    p.add_argument("--days", type=int, default=14, help="Window size for grouped stats")
    p.add_argument("--runs", type=int, default=10, help="How many recent runs to print")
    p.add_argument("--doc-types", type=int, default=20, help="How many document types to list")
    p.add_argument("--show-leads", type=int, default=0, help="Print N most recent lead rows (0=off)")
    p.add_argument("--watch", action="store_true", help="Repeat printing status")
    p.add_argument("--interval", type=int, default=30, help="Seconds between refreshes when --watch")
    args = p.parse_args()

    while True:
        print_status(
            days=args.days,
            run_limit=args.runs,
            doc_type_limit=args.doc_types,
            show_leads=args.show_leads,
        )
        if not args.watch:
            break
        print("\n---")
        time.sleep(max(5, int(args.interval)))


if __name__ == "__main__":
    main()
