#!/usr/bin/env python3
"""
Graham 30-Day Backfill with Full OCR/LLM Extraction
=====================================================

This script extracts the last 30 days of Graham county records with:
- Full OCR text extraction from document images
- Groq LLM parsing of trustor, trustee, address, principal amount, etc.
- Progress logging and quality validation
- Direct database insertion with verification

Usage:
  python graham/backfill_30days.py
"""

import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# Add parent directory to path
COUNTY_DIR = Path(__file__).resolve().parent
ROOT_DIR = COUNTY_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

import psycopg
from graham.extractor import run_graham_pipeline
from graham.run_graham_interval import (
    _load_env, _log, _db_url_with_ssl, _connect_db, 
    _ensure_schema, _upsert_records
)
from county_doc_types import UNIFIED_LEAD_DOC_TYPES


def backfill_30_days() -> dict:
    """
    Extract and store 30 days of Graham county records with full OCR/LLM extraction.
    
    Returns dict with:
      - total_docs: total documents found
      - inserted: new records added
      - updated: existing records updated
      - with_ocr: documents with OCR text extracted
      - with_groq: documents with Groq LLM extraction
      - with_trustor: documents with trustor field populated
    """
    # Load environment
    _load_env()
    
    # Calculate date range (last 30 days)
    today = date.today()
    start_date_obj = today - timedelta(days=29)
    start_date = start_date_obj.strftime("%-m/%-d/%Y")
    end_date = today.strftime("%-m/%-d/%Y")
    
    _log("")
    _log("="*70)
    _log("GRAHAM 30-DAY BACKFILL WITH FULL OCR/LLM EXTRACTION")
    _log("="*70)
    _log(f"Date Range: {start_date} to {end_date} (30 days)")
    _log(f"Expected: Full OCR + Groq LLM for all documents")
    _log(f"Target Fields: trustor, trustee, address, principal_amount, etc.")
    _log("="*70)
    
    # Verify DATABASE_URL
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL environment variable is required")
    
    # Ensure schema exists and get initial count
    _log("[1/4] Ensuring database schema...")
    try:
        conn = _connect_db(db_url)
        _ensure_schema(conn)
        
        # Get initial count
        with conn.cursor() as cur:
            cur.execute("select count(*) from graham_leads")
            initial_count = cur.fetchone()[0]
        
        conn.close()
        _log(f"      ✓ Schema ready, current count: {initial_count}")
    except Exception as e:
        _log(f"      ✗ Schema setup failed: {e}")
        raise
    
    # Run extraction with FULL OCR (ocr_limit=0 means process ALL documents)
    _log(f"\n[2/4] Running extraction pipeline (ocr_limit=0 = FULL OCR + Groq)...")
    _log(f"      Note: This may take 15-60 minutes for 30 days of documents")
    _log(f"      Progress will be logged as documents complete...\n")
    
    try:
        result = run_graham_pipeline(
            start_date=start_date,
            end_date=end_date,
            doc_types=UNIFIED_LEAD_DOC_TYPES,
            max_pages=0,
            ocr_limit=0,  # CRITICAL: 0 = process ALL docs with OCR + Groq
            workers=4,
            use_groq=True,
            headless=True,
            verbose=False,
            write_output_files=False,
        )
    except Exception as e:
        _log(f"      ✗ Extraction failed: {e}")
        raise
    
    records = result.get("records", [])
    _log(f"\n      ✓ Extraction complete: {len(records)} documents processed")
    
    if not records:
        _log("      ⚠ No documents found for this date range")
        return {
            "total_docs": 0,
            "inserted": 0,
            "updated": 0,
            "with_ocr": 0,
            "with_groq": 0,
            "with_trustor": 0,
        }
    
    # Validate extraction quality
    _log(f"\n[3/4] Validating extraction quality...")
    
    with_ocr = sum(1 for r in records if int(r.get("ocrChars", 0) or 0) > 0)
    with_groq = sum(1 for r in records if bool(r.get("usedGroq", False)))
    with_trustor = sum(1 for r in records if (r.get("trustor") or "").strip())
    with_trustee = sum(1 for r in records if (r.get("trustee") or "").strip())
    with_address = sum(1 for r in records if (r.get("propertyAddress") or "").strip())
    with_amount = sum(1 for r in records if (r.get("principalAmount") or "").strip())
    
    _log(f"      Documents with OCR text:        {with_ocr:4d} / {len(records)} ({with_ocr/len(records)*100:.1f}%)")
    _log(f"      Documents with Groq extraction: {with_groq:4d} / {len(records)} ({with_groq/len(records)*100:.1f}%)")
    _log(f"      Documents with trustor:        {with_trustor:4d} / {len(records)} ({with_trustor/len(records)*100:.1f}%)")
    _log(f"      Documents with trustee:        {with_trustee:4d} / {len(records)} ({with_trustee/len(records)*100:.1f}%)")
    _log(f"      Documents with address:        {with_address:4d} / {len(records)} ({with_address/len(records)*100:.1f}%)")
    _log(f"      Documents with amount:         {with_amount:4d} / {len(records)} ({with_amount/len(records)*100:.1f}%)")
    
    extraction_rate = (with_groq / len(records) * 100) if records else 0
    if extraction_rate < 50:
        _log(f"\n      ⚠ WARNING: LLM extraction rate is low ({extraction_rate:.1f}%)")
        _log(f"      This may indicate document access or OCR issues")
    else:
        _log(f"\n      ✓ Extraction quality acceptable ({extraction_rate:.1f}%)")
    
    # Insert/update records in database
    _log(f"\n[4/4] Storing records in database...")
    
    try:
        conn = _connect_db(db_url)
        inserted, updated, llm_used = _upsert_records(conn, records, today)
        
        # Log the run
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into graham_pipeline_runs
                  (run_date, run_finished_at, total_records, inserted_rows, updated_rows, llm_used_rows, status)
                values (%s, now(), %s, %s, %s, %s, 'success');
                """,
                (today, len(records), inserted, updated, llm_used),
            )
        conn.commit()
        
        # Verify final count
        with conn.cursor() as cur:
            cur.execute("select count(*) from graham_leads")
            final_count = cur.fetchone()[0]
        
        conn.close()
        
        _log(f"      ✓ Inserted: {inserted} new records")
        _log(f"      ✓ Updated:  {updated} existing records")
        _log(f"      ✓ LLM used: {llm_used} records")
        _log(f"      ✓ Database total: {final_count} records (was {initial_count}, +{final_count-initial_count})")
        
    except Exception as e:
        _log(f"      ✗ Database insertion failed: {e}")
        raise
    
    # Summary
    _log("")
    _log("="*70)
    _log("BACKFILL COMPLETE - SUMMARY")
    _log("="*70)
    _log(f"Total documents found:   {len(records)}")
    _log(f"New records inserted:    {inserted}")
    _log(f"Existing records updated:{updated}")
    _log(f"OCR success rate:        {with_ocr / len(records) * 100:.1f}%")
    _log(f"LLM success rate:        {with_groq / len(records) * 100:.1f}%")
    _log(f"Trustor extraction:      {with_trustor / len(records) * 100:.1f}%")
    _log("="*70)
    
    return {
        "total_docs": len(records),
        "inserted": inserted,
        "updated": updated,
        "with_ocr": with_ocr,
        "with_groq": with_groq,
        "with_trustor": with_trustor,
        "with_trustee": with_trustee,
        "with_address": with_address,
        "with_amount": with_amount,
    }


if __name__ == "__main__":
    try:
        backfill_30_days()
        sys.exit(0)
    except Exception as e:
        _log(f"❌ ERROR: {e}")
        sys.exit(1)
