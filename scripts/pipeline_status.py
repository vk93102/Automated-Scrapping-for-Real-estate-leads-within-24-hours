#!/usr/bin/env python3
"""
pipeline_status.py — Live dashboard for the Maricopa pipeline.

Usage:
    python scripts/pipeline_status.py              # today's summary
    python scripts/pipeline_status.py --days 7     # last 7 days
    python scripts/pipeline_status.py --runs 20    # last 20 cron runs
    python scripts/pipeline_status.py --failures   # show unresolved failures
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# ── Allow running from either the project root or the scripts/ folder ──────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from maricopa_scraper.dotenv import load_dotenv_if_present

load_dotenv_if_present(PROJECT_ROOT / ".env")

import psycopg


# ─────────────────────────────────────────────────────────────────────────────

def get_conn() -> psycopg.Connection:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set. Source .env first.")
    return psycopg.connect(db_url)


def _pct(n: int, total: int) -> str:
    if not total:
        return "  0.0%"
    return f"{100*n/total:5.1f}%"


def _bar(n: int, total: int, width: int = 20) -> str:
    if not total:
        return "[" + " " * width + "]"
    filled = int(width * n / total)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def cmd_summary(conn: psycopg.Connection, days: int) -> None:
    since = (date.today() - timedelta(days=days - 1)).isoformat()

    print(f"\n{'='*64}")
    print(f"  MARICOPA PIPELINE STATUS  —  last {days} day(s)  (since {since})")
    print(f"{'='*64}\n")

    # ── Document stats ────────────────────────────────────────────────────────
    row = conn.execute(
        """
        SELECT
          COUNT(*)                                             AS total_docs,
          COUNT(*) FILTER (WHERE ocr_text IS NOT NULL)        AS with_ocr,
          COUNT(*) FILTER (WHERE ocr_text IS NULL)            AS no_ocr,
          COUNT(*) FILTER (WHERE last_processed_at IS NOT NULL) AS processed
        FROM documents
        WHERE created_at >= %s::date
        """,
        (since,),
    ).fetchone()

    total_docs, with_ocr, no_ocr, processed = row
    print(f"  📄  Documents stored   : {total_docs:>6}")
    print(f"  🔍  With OCR text      : {with_ocr:>6}  {_pct(with_ocr, total_docs)}  {_bar(with_ocr, total_docs)}")
    print(f"  ❌  No OCR (404/skip)  : {no_ocr:>6}  {_pct(no_ocr, total_docs)}")
    print(f"  ✅  Processed          : {processed:>6}  {_pct(processed, total_docs)}\n")

    # ── Properties / LLM stats ────────────────────────────────────────────────
    row2 = conn.execute(
        """
        SELECT
          COUNT(*)                                                    AS total_props,
          COUNT(*) FILTER (WHERE trustor_1_full_name IS NOT NULL)     AS has_name,
          COUNT(*) FILTER (WHERE property_address IS NOT NULL)        AS has_addr,
          COUNT(*) FILTER (WHERE original_principal_balance IS NOT NULL) AS has_bal,
          COUNT(*) FILTER (WHERE llm_model IS NOT NULL)               AS via_llm
        FROM properties p
        JOIN documents d ON d.id = p.document_id
        WHERE d.created_at >= %s::date
        """,
        (since,),
    ).fetchone()

    total_props, has_name, has_addr, has_bal, via_llm = row2
    print(f"  🤖  LLM extracted rows : {total_props:>6}")
    if total_props:
        print(f"  🏠  Has trustor name   : {has_name:>6}  {_pct(has_name, total_props)}  {_bar(has_name, total_props)}")
        print(f"  📍  Has address        : {has_addr:>6}  {_pct(has_addr, total_props)}  {_bar(has_addr, total_props)}")
        print(f"  💰  Has loan balance   : {has_bal:>6}  {_pct(has_bal, total_props)}  {_bar(has_bal, total_props)}")
        print(f"  🔬  Via Groq LLM       : {via_llm:>6}  {_pct(via_llm, total_props)}")
    print()

    # ── Failure stats ─────────────────────────────────────────────────────────
    row3 = conn.execute(
        """
        SELECT
          COUNT(*)                                              AS total_failures,
          COUNT(*) FILTER (WHERE resolved = false)             AS unresolved,
          COUNT(*) FILTER (WHERE stage = 'pdf')                AS pdf_failures,
          COUNT(*) FILTER (WHERE stage = 'metadata')           AS meta_failures,
          COUNT(*) FILTER (WHERE stage = 'ocr')                AS ocr_failures
        FROM scrape_failures
        WHERE last_attempt_at >= %s::date
        """,
        (since,),
    ).fetchone()

    total_fail, unresolved, pdf_fail, meta_fail, ocr_fail = row3
    print(f"  ⚠️   Scrape failures    : {total_fail:>6}  (unresolved: {unresolved})")
    print(f"       PDF 404s          : {pdf_fail:>6}")
    print(f"       Metadata errors   : {meta_fail:>6}")
    print(f"       OCR errors        : {ocr_fail:>6}\n")

    # ── Document type breakdown ───────────────────────────────────────────────
    types = conn.execute(
        """
        SELECT document_type, COUNT(*) AS cnt
        FROM documents
        WHERE created_at >= %s::date AND document_type IS NOT NULL
        GROUP BY document_type
        ORDER BY cnt DESC
        LIMIT 10
        """,
        (since,),
    ).fetchall()

    if types:
        print(f"  📂  Top document types:")
        for doc_type, cnt in types:
            print(f"       {doc_type:<20} {cnt:>5}")
        print()


def cmd_runs(conn: psycopg.Connection, limit: int) -> None:
    # pipeline_runs table may not exist on older installs
    try:
        rows = conn.execute(
            """
            SELECT run_id, begin_date, end_date, status,
                   total_found, total_skipped, total_processed,
                   total_failed, total_ocr, total_llm,
                   started_at, finished_at
            FROM pipeline_runs
            ORDER BY started_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    except Exception:
        print("  ℹ️  pipeline_runs table not yet created (runs after next cron will populate it).")
        return

    if not rows:
        print("  ℹ️  No pipeline runs recorded yet.")
        return

    print(f"\n  {'RUN ID':>10}  {'DATE':^10}  {'STATUS':^9}  {'FOUND':>6}  {'SKIP':>5}  {'PROC':>5}  {'FAIL':>5}  {'OCR':>5}  {'LLM':>5}  DURATION")
    print("  " + "-" * 88)
    for row in rows:
        (run_id, bdate, edate, status,
         found, skipped, processed, failed, ocr, llm,
         started_at, finished_at) = row
        duration = ""
        if started_at and finished_at:
            secs = int((finished_at - started_at).total_seconds())
            duration = f"{secs//60}m{secs%60:02d}s"
        status_icon = {"success": "✅", "running": "🔄", "failed": "❌"}.get(status, "?")
        print(
            f"  {run_id[:10]:>10}  {str(bdate):^10}  {status_icon} {status:<7}  "
            f"{found or 0:>6}  {skipped or 0:>5}  {processed or 0:>5}  "
            f"{failed or 0:>5}  {ocr or 0:>5}  {llm or 0:>5}  {duration}"
        )
    print()


def cmd_failures(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        """
        SELECT recording_number, stage, error, attempts, resolved, last_attempt_at
        FROM scrape_failures
        WHERE resolved = false
        ORDER BY last_attempt_at DESC
        LIMIT 50
        """,
    ).fetchall()

    if not rows:
        print("\n  ✅  No unresolved failures.\n")
        return

    print(f"\n  UNRESOLVED FAILURES ({len(rows)} shown, max 50):\n")
    print(f"  {'RECORDING':>14}  {'STAGE':^10}  {'ATTEMPTS':>8}  {'LAST ATTEMPT':^20}  ERROR")
    print("  " + "-" * 90)
    for rec, stage, error, attempts, resolved, ts in rows:
        ts_str = str(ts)[:19] if ts else "unknown"
        error_snip = (error or "")[:50].replace("\n", " ")
        print(f"  {rec:>14}  {stage:^10}  {attempts:>8}  {ts_str:^20}  {error_snip}")
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="Maricopa pipeline status dashboard")
    p.add_argument("--days",     type=int, default=1, help="Days of history to show (default: 1 = today)")
    p.add_argument("--runs",     type=int, default=0, help="Show last N cron run records from pipeline_runs table")
    p.add_argument("--failures", action="store_true",  help="Show unresolved scrape failures")
    args = p.parse_args()

    conn = get_conn()
    try:
        cmd_summary(conn, args.days)
        if args.runs:
            cmd_runs(conn, args.runs)
        if args.failures:
            cmd_failures(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
