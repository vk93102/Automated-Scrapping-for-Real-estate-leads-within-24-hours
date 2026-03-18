#!/usr/bin/env python3
"""Greenlee live pipeline cron runner - periodic execution with dynamic dates."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Allow running from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greenlee.live_pipeline import _load_env  # noqa: E402
from greenlee.extractor import run_greenlee_pipeline  # noqa: E402


def _log(msg: str) -> None:
    """Log to file and stdout."""
    root_dir = Path(__file__).resolve().parent.parent
    log_dir = root_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with (log_dir / "greenlee_live_cron.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    _load_env()
    
    parser = argparse.ArgumentParser(
        description="Greenlee live pipeline cron runner with dynamic date range"
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help="How many days back to fetch (default: 7)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="Parallel enrichment workers (default: 3)",
    )
    parser.add_argument(
        "--ocr-limit",
        type=int,
        default=0,
        help="OCR limit: -1 skip, 0 all, N first N (default: 0)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose extractor logs",
    )
    args = parser.parse_args()

    _log(
        f"starting greenlee live pipeline cron "
        f"lookback_days={args.lookback_days} workers={args.workers} "
        f"ocr_limit={args.ocr_limit} verbose={args.verbose}"
    )

    # Compute dynamic date range
    today = datetime.now()
    start_day = today - timedelta(days=args.lookback_days - 1)
    start_date = start_day.strftime("%-m/%-d/%Y")
    end_date = today.strftime("%-m/%-d/%Y")

    _log(f"fetching records from {start_date} to {end_date}")

    # Document types for live pipeline
    doc_types = [
        "NOTICE OF TRUSTEE SALE",
        "LIS PENDENS",
        "DEED IN LIEU",
        "TREASURERS DEED",
        "NOTICE OF REINSTATEMENT",
    ]

    try:
        res = run_greenlee_pipeline(
            start_date=start_date,
            end_date=end_date,
            doc_types=doc_types,
            max_pages=0,
            ocr_limit=args.ocr_limit,
            workers=args.workers,
            use_groq=True,
            headless=True,
            verbose=args.verbose,
            write_output_files=False,
        )

        records = res.get("records", [])
        _log(f"collected {len(records)} records from scraper")

        # Store to database
        if records:
            try:
                import psycopg
                from greenlee.live_pipeline import _connect_db, _ensure_schema, _upsert_records_to_db
                from datetime import date

                db_url = (os.environ.get("DATABASE_URL") or "").strip()
                if not db_url:
                    _log("ERROR: DATABASE_URL not set, skipping DB store")
                else:
                    with _connect_db(db_url) as conn:
                        _ensure_schema(conn)
                        inserted, updated, llm_used = _upsert_records_to_db(conn, records, date.today())
                        _log(f"upserted to DB: {inserted} inserted, {updated} updated, {llm_used} used LLM")
            except Exception as e:
                _log(f"ERROR: DB upsert failed: {e}")
        else:
            _log("no records found for this period")

        _log("greenlee live pipeline cron completed successfully")

    except Exception as e:
        _log(f"ERROR: pipeline failed: {e}")
        raise


if __name__ == "__main__":
    main()
