#!/usr/bin/env python3
"""
Cochise County End-to-End Pipeline Runner (Last 20 Days)
Automatically fetches recording numbers, runs full extraction pipeline,
and stores results to database with comprehensive monitoring.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import json
from datetime import date, datetime, timedelta
from pathlib import Path

COUNTY_DIR = Path(__file__).resolve().parent
ROOT_DIR = COUNTY_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

from cochise.extractor import run_cochise_pipeline  # noqa: E402
from cochise.run_cochise_interval import (
    _connect_db,
    _db_url_with_ssl,
    _ensure_schema,
    _upsert_records,
    _log as _log_cochise,
)


def _log(msg: str) -> None:
    """Enhanced logging with timestamps."""
    log_dir = ROOT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    with (log_dir / "cochise_20day_production.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _load_env() -> None:
    """Load environment variables from .env file."""
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        _log("⚠️  .env file not found")
        return
    
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    
    _log("✅ Environment variables loaded from .env")


def _run_cochise_20day_pipeline(
    database_url: str | None = None,
    groq_model: str = "llama-3.3-70b-versatile",
    workers: int = 4,
    ocr_limit: int = 0,
    verbose: bool = True,
) -> dict:
    """
    Run Cochise County pipeline for last 20 days.
    
    Args:
        database_url: PostgreSQL connection URL (from DATABASE_URL env if not provided)
        groq_model: Groq model to use (default: llama-3.3-70b-versatile)
        workers: Number of parallel workers for OCR/LLM
        ocr_limit: Max documents to OCR (0 = no limit)
        verbose: Enable detailed logging
    
    Returns:
        dict with results including stats and file paths
    """
    
    _load_env()
    
    # Set Groq model
    os.environ["GROQ_MODEL"] = groq_model
    _log(f"🔧 Set GROQ_MODEL={groq_model}")
    
    # Calculate date range (last 20 days)
    # NOTE: System date is 2026-03-25 which is in the future relative to actual record dates
    # Use the MOST RECENT available data: 2026-03-18 is known to have data
    # So calculate: use 20 days ending at 2026-03-24 (yesterday)
    today_raw = date.today()  # 2026-03-25
    
    # For Cochise Recorder, use data from actual recent recordings
    # We know 2026-03-18 has valid data, so let's use a 20-day window ending a day or two ago
    end = today_raw - timedelta(days=1)  # Yesterday (2026-03-24)
    start = end - timedelta(days=20)      # 20 days ago (2026-03-03)
    
    _log(f"📅 Pipeline run date range: {start} to {end} (last 20 days)")
    _log(f"🔄 Starting Cochise County pipeline with {workers} workers")
    _log(f"📝 Verbose mode: {verbose}")
    
    # Define document types to scrape
    doc_types = [
        "NOTICE OF DEFAULT",
        "NOTICE OF TRUSTEE SALE",
        "LIS PENDENS",
        "DEED IN LIEU",
        "TREASURERS DEED",
        "NOTICE OF REINSTATEMENT",
    ]
    _log(f"📄 Document types: {', '.join(doc_types)}")
    
    # Run the pipeline
    _log("🚀 Running Cochise County scraper...")
    start_time = time.time()
    
    try:
        result = run_cochise_pipeline(
            start_date=start.strftime("%-m/%-d/%Y"),
            end_date=end.strftime("%-m/%-d/%Y"),
            doc_types=doc_types,
            max_pages=0,  # No page limit
            ocr_limit=ocr_limit,
            workers=workers,
            use_groq=True,  # Enable Groq LLM
            headless=True,  # Headless browser
            verbose=verbose,
        )
        
        if result is None:
            _log("❌ Pipeline returned None (likely no records found or browser failure)")
            return {"records": [], "error": "Pipeline returned None"}
        
        elapsed = time.time() - start_time
        records = result.get("records", [])
        
        _log(f"✅ Scraper completed in {elapsed:.1f}s")
        _log(f"📊 Found {len(records)} records")
        
        # Print sample record
        if records:
            sample = records[0]
            _log(f"📋 Sample record: {json.dumps(sample, indent=2)[:500]}...")
        
        # Database operations if URL provided
        if database_url:
            _log("🗄️  Connecting to database...")
            db_url = _db_url_with_ssl(database_url)
            
            try:
                conn = _connect_db(db_url, retries=3, sleep_s=2)
                _log("✅ Database connection established")
                
                _log("📋 Ensuring database schema...")
                _ensure_schema(conn)
                _log("✅ Schema ready")
                
                _log(f"💾 Upserting {len(records)} records...")
                inserted, updated, llm_used = _upsert_records(conn, records, end)
                _log(f"✅ Database upsert complete:")
                _log(f"   - Inserted: {inserted} records")
                _log(f"   - Updated: {updated} records")
                _log(f"   - LLM-extracted: {llm_used} records")
                
                conn.close()
                _log("✅ Database connection closed")
                
                result["db_inserted"] = inserted
                result["db_updated"] = updated
                result["db_llm_used"] = llm_used
                
            except Exception as e:
                _log(f"❌ Database operation failed: {e}")
                result["db_error"] = str(e)
        else:
            _log("⚠️  No database URL provided; skipping database operations")
        
        # Summary
        _log("=" * 70)
        _log("🎉 COCHISE COUNTY PIPELINE - RUN COMPLETE")
        _log("=" * 70)
        _log(f"📊 Statistics:")
        _log(f"   - Duration: {elapsed:.1f} seconds")
        _log(f"   - Records found: {len(records)}")
        _log(f"   - CSV output: {result.get('csv_path', 'N/A')}")
        _log(f"   - JSON output: {result.get('json_path', 'N/A')}")
        
        if database_url and "db_inserted" in result:
            _log(f"   - DB inserted: {result['db_inserted']}")
            _log(f"   - DB updated: {result['db_updated']}")
            _log(f"   - LLM extraction rate: {result['db_llm_used']}/{len(records)} ({100*result['db_llm_used']/max(1,len(records)):.1f}%)")
        
        _log("=" * 70)
        
        return result
        
    except Exception as e:
        elapsed = time.time() - start_time
        _log(f"❌ Pipeline failed after {elapsed:.1f}s: {e}")
        import traceback
        _log(f"Traceback:\n{traceback.format_exc()}")
        raise


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Cochise County End-to-End Pipeline (Last 20 Days)"
    )
    parser.add_argument(
        "--model",
        default="llama-3.3-70b-versatile",
        help="Groq model to use (default: llama-3.3-70b-versatile)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4)",
    )
    parser.add_argument(
        "--ocr-limit",
        type=int,
        default=0,
        help="Max documents to OCR (0 = no limit, default: 0)",
    )
    parser.add_argument(
        "--with-db",
        action="store_true",
        help="Enable database storage",
    )
    parser.add_argument(
        "--db-url",
        help="PostgreSQL connection URL (or use DATABASE_URL env)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    
    # Get database URL
    db_url = args.db_url or (os.environ.get("DATABASE_URL") if args.with_db else None)
    
    if args.with_db and not db_url:
        _log("❌ --with-db requested but no DATABASE_URL provided")
        return 1
    
    try:
        _run_cochise_20day_pipeline(
            database_url=db_url,
            groq_model=args.model,
            workers=args.workers,
            ocr_limit=args.ocr_limit,
            verbose=args.verbose,
        )
        return 0
    except Exception as e:
        _log(f"❌ Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
