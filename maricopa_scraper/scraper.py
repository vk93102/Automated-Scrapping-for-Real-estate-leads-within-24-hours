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
    update_document_ocr_text,
    upsert_document,
    upsert_properties,
)
from .csv_export import write_csv, write_dated_csv
from .dotenv import load_dotenv_if_present
from .llm_extract import extract_fields_llm
from .http_client import RetryConfig, new_session
from .logging_setup import setup_logging
from .maricopa_api import fetch_metadata, search_recording_numbers
from .ocr_pipeline import ocr_pdf_bytes_to_text, ocr_pdf_to_text
from .pdf_downloader import download_pdf, fetch_pdf_bytes
from .proxies import ProxyProvider
from .state import append_seen, load_seen


def _write_recording_numbers(path: str | Path, recs: list[str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(str(x).strip() for x in recs if str(x).strip()) + "\n", encoding="utf-8")


def _is_valid_metadata(meta) -> bool:
    """Return False for 'broken' documents — those the API returns with no document type.
    Broken records are skipped entirely: nothing is written to the DB, CSV, or JSON output."""
    return bool((meta.recording_number or "").strip() and meta.document_codes)


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
    p.add_argument("--workers", type=int, default=4, help="Number of worker threads for OCR/LLM processing")
    return p.parse_args()


def _parse_iso_date(s: str) -> date:
    parts = (s or "").split("-")
    if len(parts) != 3:
        raise ValueError("bad date")
    y, m, d = (int(x) for x in parts)
    return date(y, m, d)


def main() -> None:
    args = _parse_args()
    load_dotenv_if_present(args.dotenv)
    logger = setup_logging(level=args.log_level)

    end = _parse_iso_date(args.end_date) if args.end_date else date.today()
    if args.begin_date:
        begin = _parse_iso_date(args.begin_date)
    else:
        begin = end - timedelta(days=int(args.days))

    # Pass the document code straight to the API — no splitting, no filtering.
    # The API accepts a single code string (e.g. "NS", "N/TR SALE").
    document_code_raw = str(args.document_code or "").strip()

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

        doc_codes_for_api = [document_code_raw] if document_code_raw and document_code_raw.upper() != "ALL" else None
        recs = search_recording_numbers(
            api_session,
            document_codes=doc_codes_for_api,
            begin_date=begin,
            end_date=end,
            page_size=5000,
            max_results=None,
            retry=retry,
        )
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
            for r in recs:
                try:
                    # do not attach metadata here; metadata will be fetched in prefetch phase
                    from .db_postgres import insert_discovered_recording

                    insert_discovered_recording(conn, r, metadata=None)
                except Exception:
                    pass
        except Exception:
            # fallback to file write if DB fails
            _write_recording_numbers("output/recording_numbers_found.txt", recs)
    else:
        _write_recording_numbers("output/recording_numbers_found.txt", recs)

    if args.limit and args.limit > 0:
        recs = recs[: int(args.limit)]
    # Persist planned list (fallback to file)
    if conn is None:
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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    new_results: list[dict[str, Any]] = []

    seen_local = load_seen(args.seen_path) if (args.no_db and args.only_new) else set()

    # ---------- Two-phase processing: prefetch metadata/upsert, then threaded OCR+LLM ----------
    proxies = proxy_provider.as_requests_proxies() if args.use_proxy else None

    # Phase 1: prefetch metadata and upsert documents, build tasks for OCR/LLM
    tasks: list[tuple[str, Any, Optional[int]]] = []
    pre_session = new_session()
    for i, rec in enumerate(recs, start=1):
        logger.info(f"Prefetching metadata {rec} ({i}/{len(recs)})")
        try:
            meta = fetch_metadata(pre_session, rec, proxies=proxies, retry=retry)

            if not _is_valid_metadata(meta):
                logger.warning("Skipping %s — API returned no document type (broken record)", rec)
                if conn is not None:
                    try:
                        record_failure(conn, rec, stage="metadata", error="API returned no document type (broken/invalid record)")
                        mark_resolved(conn, rec)
                    except Exception:
                        pass
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

                # If OCR text already exists and --force not set, skip OCR/LLM
                try:
                    with conn.cursor() as cur:
                        cur.execute("select ocr_text from documents where recording_number = %s", (rec,))
                        row = cur.fetchone()
                        ocr_text = (row[0] if row and row[0] else "")
                except Exception:
                    ocr_text = ""
                if ocr_text and not args.force:
                    logger.info("Skipping OCR/LLM for %s — ocr already present", rec)
                    n_skipped += 1
                    continue

            # Schedule for OCR/LLM processing
            tasks.append((rec, meta, doc_id))

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

    # Phase 2: threaded OCR + LLM processing
    def _process_task(task: tuple[str, Any, Optional[int]]) -> dict:
        rec, meta, doc_id = task
        local_session = new_session()
        local_retry = RetryConfig(attempts=3, base_sleep_s=1.0, max_sleep_s=10.0)
        local_conn = None
        processed = False
        ocr_done = False
        llm_done = False
        error_msg = None
        try:
            if not args.no_db:
                local_conn = connect(args.db_url)

            # Download PDF and OCR
            pdf_stage = "pdf"
            if str(args.pdf_mode) == "memory":
                pdf_bytes = fetch_pdf_bytes(local_session, rec, proxies=proxies, retry=local_retry)
                pdf_stage = "ocr"
                ocr_text = ocr_pdf_bytes_to_text(pdf_bytes)
            else:
                pdf_stage = "ocr"
                pdf_path = download_pdf(local_session, rec, proxies=proxies, retry=local_retry)
                ocr_text = ocr_pdf_to_text(pdf_path)
            ocr_done = True

            if local_conn is not None:
                try:
                    update_document_ocr_text(local_conn, rec, ocr_text)
                except Exception:
                    pass

            # LLM extraction
            if not args.metadata_only:
                fields = extract_fields_llm(ocr_text)
                llm_done = True
                if local_conn is not None and fields is not None:
                    try:
                        from .llm_extract import _MODEL as _LLM_MODEL

                        upsert_properties(local_conn, doc_id, fields, llm_model=_LLM_MODEL)
                    except Exception:
                        pass

            if local_conn is not None:
                try:
                    mark_processed(local_conn, rec)
                    mark_resolved(local_conn, rec)
                except Exception:
                    pass
            processed = True
        except Exception as e:
            error_msg = str(e)
            # record failure in DB if available
            try:
                if local_conn is None and not args.no_db:
                    local_conn = connect(args.db_url)
                if local_conn is not None:
                    record_failure(local_conn, rec, stage=(pdf_stage if 'pdf_stage' in locals() else 'pdf'), error=error_msg)
            except Exception:
                pass
        finally:
            try:
                if local_conn is not None:
                    local_conn.close()
            except Exception:
                pass
        return {"rec": rec, "processed": processed, "ocr": ocr_done, "llm": llm_done, "error": error_msg}

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
                    if r.get("error"):
                        n_failed += 1
                        logger.warning("Task %s failed: %s", r.get("rec"), r.get("error"))
                except Exception as e:
                    logger.warning("Worker exception: %s", e)
                    n_failed += 1

    # After processing, refresh results from DB (if available) to include extracted fields
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select d.recording_number, d.recording_date, d.metadata, d.ocr_text, p.trustor_1_full_name, p.trustor_2_full_name, p.property_address from documents d left join properties p on p.document_id = d.id where d.recording_number = any(%s)",
                    (recs,),
                )
                rows = cur.fetchall()
                results = []
                for r in rows:
                    meta_json = r[2] or {}
                    results.append({
                        "recordingNumber": r[0],
                        "recordingDate": r[1],
                        "metadata": meta_json,
                        "ocrTextPresent": bool(r[3]),
                        "trustor_1_full_name": r[4],
                        "trustor_2_full_name": r[5],
                        "property_address": r[6],
                    })
        except Exception:
            # Fall back to existing results
            pass

    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info(f"Saved {len(results)} results to {out_path}")

    # NEW-records CSV + JSON (for filtering in the server endpoint)
    csv_path = Path(args.out_csv)
    write_csv(str(csv_path), new_results, include_meta=bool(args.csv_include_meta))
    csv_path.with_suffix(".json").write_text(
        json.dumps(new_results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info(f"Saved {len(new_results)} NEW records CSV to {csv_path}")

    if args.out_csv_dated:
        p2 = write_dated_csv(str(csv_path.parent), new_results, include_meta=bool(args.csv_include_meta))
        p2.with_suffix(".json").write_text(
            json.dumps(new_results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        logger.info(f"Saved dated NEW records CSV to {p2}")

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
