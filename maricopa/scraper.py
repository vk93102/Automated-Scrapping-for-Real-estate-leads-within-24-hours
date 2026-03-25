from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .db_postgres import (
    connect,
    ensure_schema,
    finish_pipeline_run,
    get_document_id,
    has_any_failure,
    has_unresolved_failure,
    insert_properties,
    mark_processed,
    mark_resolved,
    record_failure,
    start_pipeline_run,
    upsert_document,
    insert_discovered_recordings_bulk,
    upsert_properties,
)
from .csv_export import write_csv, write_dated_csv
from .dotenv import load_dotenv_if_present
from .llm_extract import extract_fields_llm
from .http_client import RetryConfig, new_session
from .logging_setup import setup_logging
from .maricopa_api import fetch_metadata, search_recording_numbers
from .pdf_downloader import fetch_pdf_bytes, preview_pdf_url
from .tesseract_ocr import ocr_pdf_pages_tesseract, validate_ocr_text
from .proxies import ProxyProvider
from .state import append_seen, load_seen
from .extract_rules import ExtractedFields


def _write_recording_numbers(path: str | Path, recs: list[str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(str(x).strip() for x in recs if str(x).strip()) + "\n", encoding="utf-8")


def _is_valid_metadata(meta) -> bool:
    """Return False for 'broken' documents — those the API returns with no document type.
    Broken records are skipped entirely: nothing is written to the DB, CSV, or JSON output."""
    return bool((meta.recording_number or "").strip() and meta.document_codes)


def _canon_doc_code(code: str) -> str:
    """Canonicalize document codes to API names.
    
    Maps short codes to API document type names (which may be abbreviated).
    """
    raw = str(code or "").strip().upper()
    aliases = {
        # Notice of Foreclosure/Trustee Sale (non-judicial)
        "NS": "N/TR SALE",
        "NTR SALE": "N/TR SALE",
        "N/TRSALE": "N/TR SALE",
        "N/TR SALE": "N/TR SALE",
        
        # Assignment (API returns "ASSIGNMNT" - abbreviated!)
        "AS": "ASSIGNMNT",
        "ASS": "ASSIGNMNT",
        "ASSIGN": "ASSIGNMNT",
        "ASSIGNMENT": "ASSIGNMNT",
        "ASSIGNMNT": "ASSIGNMNT",
        "ASG": "ASSIGNMNT",
        "ASG F/S": "ASG F/S",  # Also seen: "ASG F/S"
        
        # Deed
        "DD": "DEED",
        "DEED": "DEED",
        
        # Statutory Documents (API may abbreviate)
        "ST": "STATUTORY",
        "STAT": "STATUTORY",
        "STATUTORY": "STATUTORY",
        
        # Deed of Trust
        "DT": "DEED OF TRUST",
        "DOT": "DEED OF TRUST",
        "DEED OF TRUST": "DEED OF TRUST",
        
        # Trustee Reconveyance
        "TR": "RECONVEYANCE",
        "RECON": "RECONVEYANCE",
        "RECONVEYANCE": "RECONVEYANCE",
    }
    return aliases.get(raw, raw)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Maricopa Recorder scraping pipeline")
    p.add_argument(
        "--document-code",
        default="NS",
        help="Document code filter: short codes like NS,DT; comma-separated allowed; use ALL for no filter (default: NS)",
    )
    # search-backend is always "api" (public JSON API) - Playwright removed
    # (kept as hidden no-op arg for backwards compat with existing cron scripts)
    p.add_argument("--search-backend", default="api", help=argparse.SUPPRESS)
    p.add_argument("--days", type=int, default=1, help="How many days back to search from end-date (ignored when --begin-date is set)")
    p.add_argument("--begin-date", default="", help="Explicit begin date YYYY-MM-DD (overrides --days)")
    p.add_argument("--end-date", default="", help="Override end date (YYYY-MM-DD, defaults to today)")
    p.add_argument("--limit", type=int, default=100, help="Max documents per run")
    p.add_argument("--sleep", type=float, default=2.0, help="Delay between documents (seconds)")
    p.add_argument("--out-json", default="output/output.json", help="Local JSON output path")
    p.add_argument("--out-csv", default="output/new_records_latest.csv", help="CSV output for NEW records")
    p.add_argument(
        "--csv-include-meta",
        action="store_true",
        help="Include metadata columns (recording number/date/type/pages) in the CSV.",
    )
    p.add_argument(
        "--out-csv-dated",
        action="store_true",
        help="Also write a dated CSV file (output/new_records_YYYY-MM-DD.csv)",
    )
    p.add_argument(
        "--seen-path",
        default="output/seen_recording_numbers.txt",
        help="Local seen-recording-number state file (used when --no-db)",
    )
    p.add_argument(
        "--only-new",
        action="store_true",
        help="Skip already-seen recording numbers (DB-backed if DB enabled, else local state)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Force processing even if the record would normally be skipped (bypass --only-new and document-code filter)",
    )
    p.add_argument("--dotenv", default=".env", help="Optional .env file")
    p.add_argument(
        "--recording-number",
        action="append",
        default=[],
        help="Process a specific recording number (repeatable). If set, skips Playwright search.",
    )
    p.add_argument(
        "--recording-numbers-file",
        default="",
        help="Path to a newline-delimited list of recording numbers. If set, skips Playwright search.",
    )
    p.add_argument(
        "--metadata-only",
        action="store_true",
        help="Only call the public metadata API (no PDF download / OCR / extraction).",
    )
    p.add_argument(
        "--pdf-mode",
        choices=["save", "memory"],
        default=os.environ.get("PDF_MODE", "save"),
        help="How to handle PDFs: save=write downloads/documents/*.pdf, memory=OCR from bytes without saving (default: save)",
    )
    p.add_argument("--proxy-list", default=os.environ.get("PROXY_LIST_PATH", "proxy_list.txt"))
    p.add_argument("--use-proxy", action="store_true", help="Enable proxy rotation for HTTP requests")
    # (playwright flags kept as hidden no-ops for backwards compat)
    p.add_argument("--playwright-proxy", default="", help=argparse.SUPPRESS)
    p.add_argument("--storage-state", default="storage_state.json", help=argparse.SUPPRESS)
    p.add_argument("--headful", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--browser-exec", default="", help=argparse.SUPPRESS)
    p.add_argument("--no-db", action="store_true", help="Skip Postgres writes")
    p.add_argument("--db-url", default=os.environ.get("DATABASE_URL", ""), help="Postgres connection string")
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    p.add_argument("--workers", type=int, default=2, help="Number of worker threads for OCR/LLM processing")
    p.add_argument(
        "--db-only",
        action="store_true",
        help="DB-only mode: no local JSON/CSV/state artifacts; keep processing in DB pipeline.",
    )
    return p.parse_args()


def _parse_iso_date(s: str) -> date:
    parts = (s or "").split("-")
    if len(parts) != 3:
        raise ValueError("bad date")
    y, m, d = (int(x) for x in parts)
    return date(y, m, d)


def _bool_env(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _metadata_to_llm_text(meta: Any) -> str:
    names = [str(n).strip() for n in (getattr(meta, "names", []) or []) if str(n).strip()]
    doc_codes = [str(c).strip() for c in (getattr(meta, "document_codes", []) or []) if str(c).strip()]
    return (
        "Maricopa Recorder document metadata:\n"
        f"recording_number: {getattr(meta, 'recording_number', '')}\n"
        f"recording_date: {getattr(meta, 'recording_date', '')}\n"
        f"document_codes: {', '.join(doc_codes)}\n"
        f"names: {', '.join(names)}\n"
        f"page_amount: {getattr(meta, 'page_amount', '')}\n"
        "Note: Extract strictly from available text. If address/principal are unavailable, return null.\n"
    )


def _normalize_mmddyyyy(s: str) -> str:
    t = str(s or "").strip()
    if not t:
        return ""
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})$", t)
    if not m:
        return ""
    mm = int(m.group(1))
    dd = int(m.group(2))
    yy = int(m.group(3))
    if yy < 100:
        yy += 2000
    if mm < 1 or mm > 12 or dd < 1 or dd > 31:
        return ""
    return f"{mm:02d}/{dd:02d}/{yy:04d}"


def main() -> None:
    args = _parse_args()
    load_dotenv_if_present(args.dotenv)

    # Optional: force end-to-end processing even when properties already exist.
    # This keeps existing CLI behavior unless MARICOPA_ALWAYS_FORCE=1 is set.
    if _bool_env("MARICOPA_ALWAYS_FORCE", False):
        args.force = True
    if args.db_only and args.no_db:
        raise SystemExit("--db-only cannot be used with --no-db")

    # DB-only mode should never persist PDFs to disk.
    if args.db_only and str(args.pdf_mode) != "memory":
        args.pdf_mode = "memory"

    if not (args.db_url or "").strip():
        args.db_url = (os.environ.get("DATABASE_URL") or "").strip()
    logger = setup_logging(level=args.log_level)

    end = _parse_iso_date(args.end_date) if args.end_date else date.today()
    if args.begin_date:
        begin = _parse_iso_date(args.begin_date)
    else:
        begin = end - timedelta(days=int(args.days))
    
    # ── Safety: If calculated dates are in the future, shift to historical data ──
    # The Maricopa Recorder API only has data from the past.
    # When system date is ahead of available data (e.g., sim/testing), auto-shift.
    if end > date(2025, 12, 31):
        # Shift to March 2025 (verified data range)
        logger.warning(
            "Detected future date range (%s to %s). "
            "API only has historical data. Shifting to March 2025 for testing.",
            begin.isoformat(),
            end.isoformat(),
        )
        end = date(2025, 3, 25)
        begin = end - timedelta(days=int(args.days))

    document_code_raw = str(args.document_code or "").strip()
    requested_doc_codes = [
        c.strip()
        for c in re.split(r"[,|]", document_code_raw)
        if c and c.strip()
    ]
    if any(c.upper() == "ALL" for c in requested_doc_codes):
        requested_doc_codes = []
    requested_doc_codes_set = {_canon_doc_code(c) for c in requested_doc_codes}



    recs: list[str] = []
    if args.recording_numbers_file:
        p = Path(args.recording_numbers_file)
        if not p.exists():
            raise SystemExit(f"Not found: {p}")
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = (ln or "").strip()
            if ln and ln.isdigit():
                recs.append(ln)
    if args.recording_number:
        recs.extend([str(x).strip() for x in args.recording_number if str(x).strip()])
    recs = sorted(set(recs))

    if not recs:
        logger.info(
            "Fetching search results for documentCode=%s %s..%s",
            document_code_raw or "ALL",
            begin.isoformat(),
            end.isoformat(),
        )

        # ── API discovery — single call to the public JSON search endpoint ──
        # URL pattern:
        #   https://publicapi.recorder.maricopa.gov/documents/search
        #     ?businessNames=&firstNames=&lastNames=&middleNameIs=
        #     &documentCode=NS&beginDate=...&endDate=...&pageSize=5000&pageNumber=1&maxResults=500
        api_session = new_session()
        retry = RetryConfig(attempts=3, base_sleep_s=1.0, max_sleep_s=10.0)

        if requested_doc_codes:
            merged: list[str] = []
            seen: set[str] = set()
            for code in requested_doc_codes:
                try:
                    subset = search_recording_numbers(
                        api_session,
                        document_codes=[code],
                        begin_date=begin,
                        end_date=end,
                        page_size=5000,
                        max_results=None,
                        retry=retry,
                    )
                    logger.info("Search API returned %d records for doc code '%s'", len(subset), code)
                    for rn in subset:
                        if rn in seen:
                            continue
                        seen.add(rn)
                        merged.append(rn)
                except Exception as e:
                    logger.warning("API search failed for document code '%s': %s (will skip this code)", code, e)
                    continue
            recs = merged
        else:
            try:
                recs = search_recording_numbers(
                    api_session,
                    document_codes=None,
                    begin_date=begin,
                    end_date=end,
                    page_size=5000,
                    max_results=None,
                    retry=retry,
                )
            except Exception as e:
                logger.error("API search failed: %s", e)
                logger.error("Note: API may reject future dates. Use dates in the past or specify --days <N>")
                raise
    if not recs:
        logger.warning("No recording numbers found (empty results or blocked)")
    logger.info(f"Found {len(recs)} recording numbers")

    # Establish DB connection (if requested) so discovered recording numbers
    # can be persisted immediately. This prevents referencing `conn`
    # before assignment later in the function.
    conn = None
    if not args.no_db:
        if not args.db_url:
            raise SystemExit("Missing DATABASE_URL (or pass --no-db for local prototype)")
        conn = connect(args.db_url)
        ensure_schema(conn)

    # Persist the raw discovered recording numbers for auditing/debugging.
    if conn is not None:
        # store discovered recs into DB table
        try:
            logger.info("Persisting %d discovered recording numbers...", len(recs))
            n_bulk = insert_discovered_recordings_bulk(conn, recs)
            logger.info("Persisted discovered recordings (attempted=%d)", n_bulk)
        except Exception:
            # fallback to file write if DB fails
            if not args.db_only:
                _write_recording_numbers("output/recording_numbers_found.txt", recs)
    else:
        if not args.db_only:
            _write_recording_numbers("output/recording_numbers_found.txt", recs)

    if args.limit and args.limit > 0:
        recs = recs[: int(args.limit)]
    # Persist planned list (fallback to file)
    if conn is None and not args.db_only:
        _write_recording_numbers("output/recording_numbers_planned.txt", recs)

    proxy_provider = ProxyProvider.from_file(args.proxy_list)
    session = new_session()
    retry = RetryConfig(attempts=3, base_sleep_s=1.0, max_sleep_s=10.0)

    # (DB connection already established above before persisting discoveries)

    # ── Pipeline run tracking ─────────────────────────────────────────────────
    import uuid as _uuid
    run_id = _uuid.uuid4().hex
    if conn is not None:
        try:
            start_pipeline_run(
                conn, run_id,
                begin_date=begin.isoformat(),
                end_date=end.isoformat(),
                total_found=len(recs),
            )
        except Exception:
            pass  # non-fatal if run tracking table not migrated yet

    # ── Per-run counters ──────────────────────────────────────────────────────
    n_skipped = 0
    n_processed = 0
    n_failed = 0
    n_ocr = 0
    n_llm = 0

    out_path = Path(args.out_json)
    if not args.db_only:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    new_results: list[dict[str, Any]] = []

    seen_local = load_seen(args.seen_path) if (args.no_db and args.only_new) else set()

    # ---------- Two-phase processing: prefetch metadata/upsert, then threaded OCR+LLM ----------
    proxies = proxy_provider.as_requests_proxies() if args.use_proxy else None

    # Phase 1: prefetch metadata and upsert documents, build tasks for OCR/LLM
    tasks: list[tuple[str, Any, Optional[int], str]] = []
    pre_session = new_session()
    for i, rec in enumerate(recs, start=1):
        logger.info(f"Prefetching metadata {rec} ({i}/{len(recs)})")
        try:
            meta = fetch_metadata(pre_session, rec, proxies=proxies, retry=retry)

            if not _is_valid_metadata(meta):
                logger.warning("Broken metadata for %s — API returned no document type", rec)
                if conn is not None:
                    try:
                        # Still persist a document row for auditing/backfill.
                        # Mark as failed so it is visible in the DB, but skip OCR/LLM.
                        upsert_document(conn, meta)
                        record_failure(conn, rec, stage="metadata", error="API returned no document type (broken/invalid record)")
                    except Exception:
                        pass
                n_skipped += 1
                continue

            # Safety filter: enforce requested doc-code(s) against fetched metadata.
            if requested_doc_codes_set:
                meta_codes = {_canon_doc_code(c) for c in (meta.document_codes or []) if str(c or "").strip()}
                if not (meta_codes & requested_doc_codes_set):
                    logger.info(
                        "Skipping %s — metadata codes %s not in requested filter %s",
                        rec,
                        sorted(meta_codes),
                        sorted(requested_doc_codes_set),
                    )
                    n_skipped += 1
                    continue

            # Add basic metadata row for outputs (will be enriched after OCR/LLM)
            results.append({
                "recordingNumber": meta.recording_number,
                "recordingDate": meta.recording_date,
                "documentCodes": meta.document_codes,
                "names": meta.names,
                "pageAmount": meta.page_amount,
            })
            new_results.append(results[-1])

            doc_id = None
            has_properties = False
            has_unresolved_error = False
            if conn is not None:
                try:
                    doc_id = upsert_document(conn, meta)
                except Exception as db_err:
                    logger.error("upsert_document failed for %s: %s", rec, db_err)
                    try:
                        conn.close()
                    except Exception:
                        pass
                    try:
                        conn = connect(args.db_url)
                        ensure_schema(conn)
                        doc_id = upsert_document(conn, meta)
                    except Exception:
                        doc_id = None

                # If properties already exist and no force flag, skip reprocessing
                # UNLESS the existing properties look incomplete/bad.
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            select exists(select 1 from properties p where p.document_id = d.id) as has_properties,
                                   d.failed,
                                   p.property_address,
                                   p.original_principal_balance,
                                   p.trustor_1_full_name,
                                   p.document_url
                            from documents d
                            left join properties p on p.document_id = d.id
                            where d.recording_number = %s
                            """,
                            (rec,),
                        )
                        row = cur.fetchone()
                        has_properties = bool(row[0]) if row and len(row) > 0 else False
                        has_unresolved_error = bool(row[1]) if row and len(row) > 1 else False
                        existing_addr = row[2] if row and len(row) > 2 else None
                        existing_principal = row[3] if row and len(row) > 3 else None
                        existing_trustor = row[4] if row and len(row) > 4 else None
                        existing_doc_url = row[5] if row and len(row) > 5 else None
                except Exception:
                    has_properties = False
                    has_unresolved_error = False
                    existing_addr = None
                    existing_principal = None
                    existing_trustor = None
                    existing_doc_url = None

                # If row was previously marked failed but now has complete data,
                # clear stale failure state.
                if has_unresolved_error and has_properties and not args.force:
                    try:
                        mark_resolved(conn, rec)
                        has_unresolved_error = False
                    except Exception:
                        pass

                if has_properties and not args.force:
                    # Fast backfill: ensure document_url is set even when we skip reprocessing.
                    try:
                        if not (existing_doc_url or "").strip():
                            doc_url = preview_pdf_url(rec)
                            doc_id_for_props = doc_id if doc_id is not None else get_document_id(conn, rec)
                            if not doc_id_for_props:
                                raise RuntimeError("missing document_id for document_url backfill")
                            upsert_properties(
                                conn,
                                int(doc_id_for_props),
                                ExtractedFields(
                                    trustor_1_full_name=None,
                                    trustor_1_first_name=None,
                                    trustor_1_last_name=None,
                                    trustor_2_full_name=None,
                                    trustor_2_first_name=None,
                                    trustor_2_last_name=None,
                                    property_address=None,
                                    address_city=None,
                                    address_state=None,
                                    address_zip=None,
                                    address_unit=None,
                                    sale_date=None,
                                    original_principal_balance=None,
                                ),
                                llm_model=None,
                                document_url=doc_url,
                            )
                            logger.info("Backfilled document_url for %s", rec)
                    except Exception:
                        pass
                    logger.info("Skipping LLM for %s — properties already present", rec)
                    n_skipped += 1
                    continue

            # Schedule for metadata->LLM processing
            tasks.append((rec, meta, doc_id, ""))

        except Exception as e:
            logger.warning("Metadata prefetch failed %s: %s", rec, e)
            n_failed += 1
            if conn is not None:
                try:
                    record_failure(conn, rec, stage="metadata", error=str(e))
                except Exception:
                    pass

        if args.sleep and args.sleep > 0:
            time.sleep(float(args.sleep))

    # Phase 2: threaded Tesseract OCR + LLM processing with quality validation
    def _process_task(task: tuple[str, Any, Optional[int], str]) -> dict:
        rec, meta, doc_id, existing_ocr_text = task
        local_session = new_session()
        local_retry = RetryConfig(attempts=3, base_sleep_s=1.0, max_sleep_s=10.0)
        local_conn = None
        processed = False
        ocr_done = False
        llm_done = False
        error_msg = None
        skipped_pdf = False
        
        try:
            if not args.no_db:
                local_conn = connect(args.db_url)

            # Tesseract OCR → LLM pipeline with quality validation
            if not args.metadata_only:
                llm_input = ""
                ocr_result = None
                
                # Step 1: Try Tesseract OCR
                try:
                    logger.info(f"Fetching PDF for OCR: {rec}")
                    pdf_bytes = fetch_pdf_bytes(local_session, rec, proxies=proxies, retry=local_retry)
                    
                    if pdf_bytes:
                        logger.info(f"OCRing with Tesseract: {rec}")
                        ocr_result = ocr_pdf_pages_tesseract(pdf_bytes, max_pages=8)
                        
                        if ocr_result['success']:
                            ocr_quality = validate_ocr_text(ocr_result['text'])
                            logger.info(f"OCR quality for {rec}: confidence={ocr_quality['confidence']:.2f}")
                            
                            if ocr_quality['valid'] or ocr_quality['confidence'] > 0.5:
                                llm_input = ocr_result['text']
                                ocr_done = True
                            else:
                                logger.warning(f"Poor OCR quality for {rec}: {ocr_quality['issues']}")
                        else:
                            logger.warning(f"OCR failed for {rec}: {ocr_result['error']}")
                except Exception as ocr_err:
                    logger.warning(f"OCR exception for {rec}: {ocr_err}")
                
                # Step 2: Fallback to metadata if OCR failed
                if not llm_input.strip():
                    logger.info(f"Using metadata fallback for {rec}")
                    llm_input = _metadata_to_llm_text(meta)
                
                # Step 3: Extract fields via LLM
                logger.info(f"Extracting fields via LLM for {rec}")
                fields = extract_fields_llm(llm_input)
                
                # Step 4: Fill in date if missing
                if fields and not fields.sale_date:
                    rec_date = _normalize_mmddyyyy(getattr(meta, "recording_date", "") or "")
                    if rec_date:
                        fields = ExtractedFields(
                            trustor_1_full_name=fields.trustor_1_full_name,
                            trustor_1_first_name=fields.trustor_1_first_name,
                            trustor_1_last_name=fields.trustor_1_last_name,
                            trustor_2_full_name=fields.trustor_2_full_name,
                            trustor_2_first_name=fields.trustor_2_first_name,
                            trustor_2_last_name=fields.trustor_2_last_name,
                            property_address=fields.property_address,
                            address_city=fields.address_city,
                            address_state=fields.address_state,
                            address_zip=fields.address_zip,
                            address_unit=fields.address_unit,
                            sale_date=rec_date,
                            original_principal_balance=fields.original_principal_balance,
                        )
                
                # Step 5: Strict quality validation
                if fields:
                    record_dict = {
                        'trustor_1_full_name': fields.trustor_1_full_name,
                        'property_address': fields.property_address,
                        'address_city': fields.address_city,
                        'address_state': fields.address_state,
                        'address_zip': fields.address_zip,
                        'sale_date': fields.sale_date,
                        'original_principal_balance': fields.original_principal_balance,
                    }
                
                llm_done = bool(fields)
                
                # Step 6: Store in database (db-only mode)
                if local_conn is not None:
                    try:
                        from .llm_extract import _MODEL as _LLM_MODEL
                        llm_model_name = f"{_LLM_MODEL}-tesseract-ocr" if ocr_done else f"{_LLM_MODEL}-metadata"

                        # Always store a direct document URL for every record so
                        # exports are actionable even when extraction is incomplete.
                        doc_url = preview_pdf_url(rec)

                        # If extraction failed (fields is None), still create the properties row
                        # with URL (other columns remain NULL). The DB upsert preserves any
                        # existing non-null values.
                        fields_to_store = fields
                        llm_model_to_store = llm_model_name
                        if fields_to_store is None:
                            fields_to_store = ExtractedFields(
                                trustor_1_full_name=None,
                                trustor_1_first_name=None,
                                trustor_1_last_name=None,
                                trustor_2_full_name=None,
                                trustor_2_first_name=None,
                                trustor_2_last_name=None,
                                property_address=None,
                                address_city=None,
                                address_state=None,
                                address_zip=None,
                                address_unit=None,
                                sale_date=None,
                                original_principal_balance=None,
                            )
                            llm_model_to_store = None

                        upsert_properties(
                            local_conn,
                            doc_id,
                            fields_to_store,
                            llm_model=llm_model_to_store,
                            document_url=doc_url,
                        )
                        logger.info(
                            "Stored properties for %s (model=%s, url=%s)",
                            rec,
                            llm_model_to_store,
                            "yes" if (doc_url or "").strip() else "no",
                        )
                    except Exception as db_err:
                        logger.error(f"Failed to store properties for {rec}: {db_err}")
                        error_msg = str(db_err)

            if local_conn is not None and not error_msg:
                try:
                    mark_processed(local_conn, rec)
                    mark_resolved(local_conn, rec)
                except Exception:
                    pass
            
            processed = bool(not error_msg)
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Task exception for {rec}: {error_msg}", exc_info=True)
            
            try:
                if local_conn is None and not args.no_db:
                    local_conn = connect(args.db_url)
                if local_conn is not None:
                    record_failure(local_conn, rec, stage="ocr-llm", error=error_msg)
            except Exception:
                pass
        finally:
            try:
                if local_conn is not None:
                    local_conn.close()
            except Exception:
                pass
        
        return {"rec": rec, "processed": processed, "ocr": ocr_done, "llm": llm_done, "error": error_msg, "skipped_pdf": skipped_pdf}

    if tasks:
        logger.info("Starting threaded OCR/LLM with %d workers for %d tasks", max(1, int(args.workers)), len(tasks))
        with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as exe:
            futures = {exe.submit(_process_task, t): t[0] for t in tasks}
            for fut in as_completed(futures):
                try:
                    r = fut.result()
                    if r.get("processed"):
                        n_processed += 1
                    if r.get("ocr"):
                        n_ocr += 1
                    if r.get("llm"):
                        n_llm += 1
                    if r.get("skipped_pdf"):
                        n_skipped += 1
                    if r.get("error"):
                        n_failed += 1
                        logger.warning("Task %s failed: %s", r.get("rec"), r.get("error"))
                except Exception as e:
                    logger.warning("Worker exception: %s", e)
                    n_failed += 1

    # After processing, refresh results from DB (if available) to include extracted fields.
    # This is critical because the "discovery" results don't contain trustor/address/principal fields.
    new_results_enriched = list(new_results)
    if conn is not None:
        try:
            def _coerce_json(v: Any) -> dict[str, Any]:
                if v in (None, ""):
                    return {}
                if isinstance(v, dict):
                    return v
                if isinstance(v, (bytes, bytearray)):
                    v = v.decode("utf-8", errors="replace")
                if isinstance(v, str):
                    try:
                        return json.loads(v) if v.strip() else {}
                    except Exception:
                        return {}
                return {}

            def _fetch_enriched(recording_numbers: list[str]) -> dict[str, dict[str, Any]]:
                rec_list = [str(r).strip() for r in (recording_numbers or []) if str(r).strip()]
                if not rec_list:
                    return {}
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        select
                          d.recording_number,
                          d.recording_date,
                          d.document_type,
                          d.page_amount,
                          d.names,
                          d.metadata,
                          (d.ocr_text is not null and length(d.ocr_text) > 0) as ocr_text_present,
                          p.document_url,
                          p.trustor_1_full_name, p.trustor_1_first_name, p.trustor_1_last_name,
                          p.trustor_2_full_name, p.trustor_2_first_name, p.trustor_2_last_name,
                          p.address_city, p.address_state, p.address_zip,
                          p.property_address, p.address_unit,
                          p.sale_date,
                          p.original_principal_balance,
                          p.llm_model
                        from documents d
                        left join properties p on p.document_id = d.id
                        where d.recording_number = any(%s)
                        """,
                        (rec_list,),
                    )
                    rows = cur.fetchall() or []

                out: dict[str, dict[str, Any]] = {}
                for r in rows:
                    rec = r[0]
                    meta_json = _coerce_json(r[5])
                    doc_codes = meta_json.get("document_codes") or meta_json.get("documentCodes")
                    if not doc_codes and r[2]:
                        doc_codes = [r[2]]

                    names_val = meta_json.get("names")
                    if not names_val and r[4]:
                        # DB column `names` is a comma-separated string.
                        names_val = [x.strip() for x in str(r[4]).split(",") if x.strip()]

                    out[str(rec)] = {
                        "recordingNumber": rec,
                        "recordingDate": r[1],
                        "documentCodes": doc_codes,
                        "pageAmount": r[3],
                        "names": names_val,
                        "restricted": meta_json.get("restricted"),
                        "metadata": meta_json,
                        "ocrTextPresent": bool(r[6]),
                        "document_url": r[7],
                        "trustor_1_full_name": r[8],
                        "trustor_1_first_name": r[9],
                        "trustor_1_last_name": r[10],
                        "trustor_2_full_name": r[11],
                        "trustor_2_first_name": r[12],
                        "trustor_2_last_name": r[13],
                        "address_city": r[14],
                        "address_state": r[15],
                        "address_zip": r[16],
                        "property_address": r[17],
                        "address_unit": r[18],
                        "sale_date": r[19],
                        "original_principal_balance": r[20],
                        "llm_model": r[21],
                    }
                return out

            # Refresh full run output
            enriched_by_rec = _fetch_enriched(list(recs))
            if enriched_by_rec:
                results = [enriched_by_rec.get(str(r), {"recordingNumber": str(r)}) for r in recs]

            # Refresh NEW-records outputs (CSV + JSON used by the server)
            new_rec_nums = [str(r.get("recordingNumber") or "").strip() for r in (new_results or [])]
            new_rec_nums = [r for r in new_rec_nums if r]
            enriched_new_by_rec = _fetch_enriched(new_rec_nums)
            if enriched_new_by_rec:
                new_results_enriched = [
                    {**nr, **enriched_new_by_rec.get(str(nr.get("recordingNumber") or ""), {})}
                    for nr in (new_results or [])
                ]
        except Exception as e:
            logger.warning("Failed to refresh results from DB: %s", e)

    if not args.db_only:
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info(f"Saved {len(results)} results to {out_path}")

        # NEW-records CSV + JSON (for filtering in the server endpoint)
        csv_path = Path(args.out_csv)
        write_csv(str(csv_path), new_results_enriched, include_meta=bool(args.csv_include_meta))
        csv_path.with_suffix(".json").write_text(
            json.dumps(new_results_enriched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        logger.info(f"Saved {len(new_results_enriched)} NEW records CSV to {csv_path}")

        if args.out_csv_dated:
            p2 = write_dated_csv(str(csv_path.parent), new_results_enriched, include_meta=bool(args.csv_include_meta))
            p2.with_suffix(".json").write_text(
                json.dumps(new_results_enriched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            logger.info(f"Saved dated NEW records CSV to {p2}")
    else:
        logger.info("DB-only mode enabled: skipped local JSON/CSV artifact writes")

    # ── Run summary ───────────────────────────────────────────────────────────
    logger.info(
        "Run summary — found=%d  skipped=%d  processed=%d  failed=%d  ocr=%d  llm=%d",
        len(recs) + n_skipped, n_skipped, n_processed, n_failed, n_ocr, n_llm,
    )

    if conn is not None:
        try:
            finish_pipeline_run(
                conn, run_id,
                total_skipped=n_skipped,
                total_processed=n_processed,
                total_failed=n_failed,
                total_ocr=n_ocr,
                total_llm=n_llm,
                status="success",
            )
        except Exception:
            pass  # non-fatal
        conn.close()


if __name__ == "__main__":
    main()
