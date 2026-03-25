#!/usr/bin/env python3
"""
Cochise County 20-Day Production Pipeline Monitor
Real-time monitoring and database storage for Cochise County leads
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

COUNTY_DIR = Path(__file__).resolve().parent
ROOT_DIR = COUNTY_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

# Import required modules
from greenlee.extractor import (
    playwright_collect_results,
    enrich_record,
    fetch_detail,
    _make_session,
    _load_local_env,
    _resolve_hosted_document_endpoint_url,
    export_csv,
    export_json,
    _normalise_date,
    _compute_manual_review,
    BASE_URL,
    DEFAULT_DOCUMENT_TYPES,
    CSV_FIELDS,
    OUTPUT_DIR,
)

try:
    import psycopg
except ImportError:
    psycopg = None


def _db_url_with_ssl(url: str) -> str:
    """Add SSL mode for remote databases."""
    u = (url or "").strip()
    if not u:
        return u
    host = (urlparse(u).hostname or "").strip().lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return u
    if "sslmode=" in u.lower():
        return u
    return f"{u}{'&' if '?' in u else '?'}sslmode=require"


def _connect_db(database_url: str, retries: int = 3) -> psycopg.Connection | None:
    """Connect to PostgreSQL database."""
    if not psycopg or not database_url:
        return None
    
    primary_url = _db_url_with_ssl(database_url)
    
    for i in range(retries):
        try:
            return psycopg.connect(primary_url, connect_timeout=12)
        except Exception as e:
            if i < retries - 1:
                time.sleep(2)
            continue
    
    return None


def _ensure_cochise_schema(conn: psycopg.Connection) -> None:
    """Ensure cochise_leads table exists."""
    if not conn:
        return
    
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cochise_leads (
              id               BIGSERIAL PRIMARY KEY,
              source_county    TEXT NOT NULL DEFAULT 'Cochise',
              document_id      TEXT NOT NULL,
              recording_number TEXT,
              recording_date   TEXT,
              document_type    TEXT,
              grantors         TEXT,
              grantees         TEXT,
              trustor          TEXT,
              trustee          TEXT,
              beneficiary      TEXT,
              principal_amount TEXT,
              property_address TEXT,
              detail_url       TEXT,
              image_urls       TEXT,
              manual_review         BOOLEAN,
              manual_review_reasons TEXT,
              manual_review_summary TEXT,
              manual_review_context TEXT,
              ocr_method       TEXT,
              ocr_chars        INTEGER,
              used_groq        BOOLEAN,
              groq_model       TEXT,
              groq_error       TEXT,
              analysis_error   TEXT,
              run_date         DATE,
              raw_record       JSONB NOT NULL DEFAULT '{}'::jsonb,
              created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              UNIQUE (source_county, document_id)
            );
        """)

        # Backfill columns on existing deployments
        cur.execute("alter table cochise_leads add column if not exists manual_review boolean;")
        cur.execute("alter table cochise_leads add column if not exists manual_review_reasons text;")
        cur.execute("alter table cochise_leads add column if not exists manual_review_summary text;")
        cur.execute("alter table cochise_leads add column if not exists manual_review_context text;")
    conn.commit()


def _upsert_cochise_records(conn: psycopg.Connection, records: list[dict], run_date: date) -> tuple[int, int]:
    """Upsert records to cochise_leads table."""
    if not conn:
        return 0, 0
    
    inserted = 0
    updated = 0
    
    with conn.cursor() as cur:
        for r in records:
            doc_id = str(r.get("documentId", "") or "").strip()
            if not doc_id:
                continue
            
            payload = {
                "document_id": doc_id,
                "recording_number": r.get("recordingNumber", ""),
                "recording_date": r.get("recordingDate", ""),
                "document_type": r.get("documentType", ""),
                "grantors": r.get("grantors", ""),
                "grantees": r.get("grantees", ""),
                "trustor": r.get("trustor", ""),
                "trustee": r.get("trustee", ""),
                "beneficiary": r.get("beneficiary", ""),
                "principal_amount": r.get("principalAmount", ""),
                "property_address": r.get("propertyAddress", ""),
                "detail_url": r.get("detailUrl", ""),
                "image_urls": r.get("imageUrls", ""),
                "manual_review": bool(r.get("manualReview", False)),
                "manual_review_reasons": r.get("manualReviewReasons", ""),
                "manual_review_summary": r.get("manualReviewSummary", ""),
                "manual_review_context": r.get("manualReviewContext", ""),
                "ocr_method": r.get("ocrMethod", ""),
                "ocr_chars": int(r.get("ocrChars", 0) or 0),
                "used_groq": bool(r.get("usedGroq", False)),
                "groq_model": r.get("groqModel", ""),
                "groq_error": r.get("groqError", ""),
                "analysis_error": r.get("analysisError", ""),
                "run_date": run_date,
                "raw_record": json.dumps(r),
            }
            
            try:
                cur.execute("""
                    INSERT INTO cochise_leads 
                    (source_county, document_id, recording_number, recording_date, document_type,
                     grantors, grantees, trustor, trustee, beneficiary, principal_amount, 
                     property_address, detail_url, image_urls,
                     manual_review, manual_review_reasons, manual_review_summary, manual_review_context,
                     ocr_method, ocr_chars, used_groq, groq_model, groq_error, analysis_error, run_date, raw_record)
                    VALUES 
                    ('Cochise', %(document_id)s, %(recording_number)s, %(recording_date)s, %(document_type)s,
                     %(grantors)s, %(grantees)s, %(trustor)s, %(trustee)s, %(beneficiary)s, %(principal_amount)s,
                     %(property_address)s, %(detail_url)s, %(image_urls)s,
                     %(manual_review)s, %(manual_review_reasons)s, %(manual_review_summary)s, %(manual_review_context)s,
                     %(ocr_method)s, %(ocr_chars)s, %(used_groq)s, %(groq_model)s, %(groq_error)s, %(analysis_error)s,
                     %(run_date)s, %(raw_record)s)
                    ON CONFLICT (source_county, document_id) 
                    DO UPDATE SET
                        recording_number = EXCLUDED.recording_number,
                        recording_date = EXCLUDED.recording_date,
                        document_type = EXCLUDED.document_type,
                        grantors = EXCLUDED.grantors,
                        grantees = EXCLUDED.grantees,
                        trustor = EXCLUDED.trustor,
                        trustee = EXCLUDED.trustee,
                        beneficiary = EXCLUDED.beneficiary,
                        principal_amount = EXCLUDED.principal_amount,
                        property_address = EXCLUDED.property_address,
                        detail_url = EXCLUDED.detail_url,
                        image_urls = EXCLUDED.image_urls,
                        manual_review = EXCLUDED.manual_review,
                        manual_review_reasons = EXCLUDED.manual_review_reasons,
                        manual_review_summary = EXCLUDED.manual_review_summary,
                        manual_review_context = EXCLUDED.manual_review_context,
                        ocr_method = EXCLUDED.ocr_method,
                        ocr_chars = EXCLUDED.ocr_chars,
                        used_groq = EXCLUDED.used_groq,
                        groq_model = EXCLUDED.groq_model,
                        groq_error = EXCLUDED.groq_error,
                        analysis_error = EXCLUDED.analysis_error,
                        raw_record = EXCLUDED.raw_record,
                        updated_at = NOW()
                    RETURNING (xmax = 0) as is_insert
                """, payload)
                
                result = cur.fetchone()
                if result and result[0]:
                    inserted += 1
                else:
                    updated += 1
            except Exception as e:
                _log(f"   ⚠️  DB insert failed for doc {doc_id}: {str(e)[:80]}", "WARN")
    
    conn.commit()
    return inserted, updated

def _log(msg: str, level: str = "INFO") -> None:
    """Enhanced logging."""
    log_dir = ROOT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    icon = {"INFO": "ℹ️ ", "OK": "✅", "ERR": "❌", "WARN": "⚠️ "}[level]
    line = f"[{timestamp}] {icon} {msg}"
    print(line, flush=True)
    with (log_dir / "cochise_20day_monitor.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_cochise_20day_manual(
    groq_model: str = "llama-3.3-70b-versatile",
    workers: int = 4,
    verbose: bool = True,
) -> dict:
    """
    Manually run Cochise pipeline for 20 days with full control.
    Based on greenlee pipeline but with direct error handling.
    """
    
    _log("=" * 80, "INFO")
    _log("🚀 COCHISE COUNTY 20-DAY PRODUCTION PIPELINE", "OK")
    _log("=" * 80, "INFO")
    
    # Set environment
    os.environ["GROQ_MODEL"] = groq_model
    _load_local_env()
    _log(f"🔧 Model: {groq_model}", "OK")
    
    # Date range
    today = date.today()  # 2026-03-25
    end = today - timedelta(days=1)
    start = end - timedelta(days=20)
    
    _log(f"📅 Date range: {start} to {end} (20 days)", "INFO")
    
    doc_types = [
        "NOTICE OF DEFAULT",
        "NOTICE OF TRUSTEE SALE",
        "LIS PENDENS",
        "DEED IN LIEU",
        "TREASURERS DEED",
        "NOTICE OF REINSTATEMENT",
    ]
    _log(f"📄 Document types: {len(doc_types)} types", "INFO")
    
    results = {
        "start_time": datetime.now().isoformat(),
        "records_found": 0,
        "records_enriched": 0,
        "csv_path": "",
        "json_path": "",
        "error": None,
    }
    
    try:
        # Step 1: Collect results via Playwright
        _log("🔍 Step 1: Fetching search results via Playwright...", "INFO")
        cookie_header, records = playwright_collect_results(
            start_date=start.strftime("%-m/%-d/%Y"),
            end_date=end.strftime("%-m/%-d/%Y"),
            doc_types=doc_types,
            max_pages=0,
            headless=True,
            verbose=verbose,
        )
        
        if records is None:
            records = []
        
        _log(f"✅ Found {len(records)} records", "OK")
        results["records_found"] = len(records)
        
        if len(records) == 0:
            _log("⚠️  No records found in date range", "WARN")
            return results
        
        # Step 2: Setup for enrichment
        _log("🔄 Step 2: Setting up LLM enrichment...", "INFO")
        session = _make_session(cookie_header)
        groq_key = os.getenv("GROQ_API_KEY", "")
        hosted_endpoint = _resolve_hosted_document_endpoint_url()
        use_groq = bool(groq_key or hosted_endpoint)
        
        _log(f"   - LLM enabled: {use_groq}", "INFO")
        _log(f"   - Workers: {workers}", "INFO")
        
        # Step 3: Enrich records (limited OCR for now)
        _log(f"🧠 Step 3: Enriching {len(records)} records with LLM...", "INFO")
        enrich_limit = min(len(records), 50)  # Limit to first 50 for testing
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        enriched_count = 0
        if enrich_limit > 0 and use_groq:
            def _enrich_one(idx: int) -> tuple[int, dict]:
                rec = records[idx]
                local_session = _make_session(cookie_header)
                try:
                    out = enrich_record(rec, local_session, use_groq=True, groq_api_key=groq_key)
                    return idx, out
                except Exception as e:
                    _log(f"   ⚠️  Enrich failed for doc {rec.get('documentId', '')}: {str(e)[:100]}", "WARN")
                    return idx, rec
            
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = [ex.submit(_enrich_one, i) for i in range(enrich_limit)]
                for fut in as_completed(futures):
                    try:
                        idx, out = fut.result()
                        records[idx] = out
                        enriched_count += 1
                        if enriched_count % 10 == 0:
                            _log(f"   ✅ Enriched {enriched_count}/{enrich_limit} records", "INFO")
                    except Exception as e:
                        _log(f"   ❌ Error in enrichment batch: {e}", "ERR")
        
        results["records_enriched"] = enriched_count
        _log(f"✅ Enrichment complete ({enriched_count} records)", "OK")
        
        # Step 4: Fetch details for remaining records
        _log(f"📝 Step 4: Fetching details for remaining records...", "INFO")
        for i, rec in enumerate(records):
            if i < enrich_limit:
                continue
            try:
                detail = fetch_detail(rec.get("documentId", ""), session)
                for key in ["detailUrl", "recordingNumber", "recordingDate", "documentType", "grantors", "grantees"]:
                    if detail.get(key):
                        rec[key] = detail[key]
                manual, reasons, summary, context = _compute_manual_review(rec)
                rec["manualReview"] = manual
                rec["manualReviewReasons"] = reasons
            except Exception as e:
                _log(f"   ⚠️  Detail fetch failed: {str(e)[:60]}", "WARN")
        
        # Step 5: Export to CSV and JSON
        _log("💾 Step 5: Exporting to CSV and JSON...", "INFO")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = OUTPUT_DIR / f"cochise_20day_{ts}.csv"
        json_path = OUTPUT_DIR / f"cochise_20day_{ts}.json"
        
        meta = {
            "county": "Cochise County, AZ",
            "startDate": _normalise_date(start.strftime("%-m/%-d/%Y")),
            "endDate": _normalise_date(end.strftime("%-m/%-d/%Y")),
            "documentTypes": doc_types,
            "recordsFound": len(records),
            "recordsEnriched": enriched_count,
            "workers": workers,
            "usedGroq": use_groq,
            "timestamp": datetime.now().isoformat(),
        }
        
        try:
            export_csv(records, csv_path)
            _log(f"✅ CSV exported: {csv_path.name}", "OK")
            results["csv_path"] = str(csv_path)
        except Exception as e:
            _log(f"❌ CSV export failed: {e}", "ERR")
        
        try:
            export_json(records, json_path, meta=meta)
            _log(f"✅ JSON exported: {json_path.name}", "OK")
            results["json_path"] = str(json_path)
        except Exception as e:
            _log(f"❌ JSON export failed: {e}", "ERR")
        
        # Step 6: Store to database
        _log("🗄️  Step 6: Storing to database...", "INFO")
        db_url = os.environ.get("DATABASE_URL", "").strip()
        if db_url:
            try:
                conn = _connect_db(db_url, retries=3)
                if conn:
                    _log("✅ Database connected", "OK")
                    _ensure_cochise_schema(conn)
                    _log("✅ Schema ready", "OK")
                    
                    inserted, updated = _upsert_cochise_records(conn, records, end)
                    _log(f"✅ Database upsert complete: {inserted} inserted, {updated} updated", "OK")
                    
                    conn.close()
                    results["db_inserted"] = inserted
                    results["db_updated"] = updated
                    results["db_url"] = "✅ cochise_leads table"
                else:
                    _log("❌ Database connection failed (no retries left)", "ERR")
                    results["db_error"] = "Connection failed"
            except Exception as e:
                _log(f"❌ Database operation failed: {e}", "ERR")
                import traceback
                _log(traceback.format_exc(), "ERR")
                results["db_error"] = str(e)
        else:
            _log("⚠️  DATABASE_URL not set - skipping database storage", "WARN")
        
        # Step 7: Summary
        _log("=" * 80, "INFO")
        _log("📊 PIPELINE SUMMARY", "OK")
        _log("=" * 80, "INFO")
        _log(f"Total records: {len(records)}", "OK")
        _log(f"Enriched with LLM: {enriched_count}", "OK")
        _log(f"CSV: {csv_path.name if csv_path.exists() else 'FAILED'}", "OK")
        _log(f"JSON: {json_path.name if json_path.exists() else 'FAILED'}", "OK")
        
        if "db_inserted" in results:
            _log(f"Database inserted: {results['db_inserted']} rows", "OK")
            _log(f"Database updated: {results['db_updated']} rows", "OK")
            _log(f"Table: cochise_leads", "OK")
        elif "db_error" in results:
            _log(f"Database error: {results['db_error']}", "ERR")
        else:
            _log("Database: Not configured", "WARN")
        
        _log("=" * 80, "INFO")
        
        return results
        
    except Exception as e:
        _log(f"❌ Pipeline error: {e}", "ERR")
        import traceback
        _log(traceback.format_exc(), "ERR")
        results["error"] = str(e)
        return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Cochise County 20-Day Production Pipeline")
    parser.add_argument("--model", default="llama-3.3-70b-versatile")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--db-url", help="PostgreSQL connection URL (or use DATABASE_URL env)")
    args = parser.parse_args()
    
    # Set database URL if provided
    if args.db_url:
        os.environ["DATABASE_URL"] = args.db_url
    
    result = run_cochise_20day_manual(
        groq_model=args.model,
        workers=args.workers,
        verbose=args.verbose,
    )
    
    return 0 if result.get("error") is None else 1


if __name__ == "__main__":
    sys.exit(main())
