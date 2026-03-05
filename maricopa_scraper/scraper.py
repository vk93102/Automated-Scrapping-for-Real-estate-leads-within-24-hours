from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

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
    p.add_argument(
        "--search-backend",
        choices=["api", "playwright"],
        default=os.environ.get("SEARCH_BACKEND", "api"),
        help="How to discover recording numbers: api (recommended) or playwright (Cloudflare HTML page)",
    )
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
    p.add_argument("--playwright-proxy", default="", help="Proxy server for Playwright, e.g. http://host:port")
    p.add_argument("--storage-state", default="storage_state.json", help="Playwright storage state path")
    p.add_argument("--headful", action="store_true", help="Run Playwright in visible mode (for Cloudflare)")
    p.add_argument("--no-db", action="store_true", help="Skip Postgres writes")
    p.add_argument("--db-url", default=os.environ.get("DATABASE_URL", ""), help="Postgres connection string")
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
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

    document_code_raw = str(args.document_code or "").strip()
    document_codes: list[str] = []
    if document_code_raw and document_code_raw.upper() != "ALL":
        document_codes = [c.strip() for c in document_code_raw.replace(" ", ",").split(",") if c.strip()]

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
            "Fetching search results via %s for %s %s..%s",
            args.search_backend,
            ("ALL" if not document_codes else ",".join(document_codes)),
            begin.isoformat(),
            end.isoformat(),
        )

        if args.search_backend == "api":
            session = new_session()
            retry = RetryConfig(attempts=3, base_sleep_s=1.0, max_sleep_s=10.0)
            recs = search_recording_numbers(
                session,
                document_codes=(document_codes or None),
                begin_date=begin,
                end_date=end,
                page_size=200,
                max_results=None,
                retry=retry,
            )
        else:
            if len(document_codes) != 1:
                raise SystemExit("Playwright search supports exactly one --document-code (not ALL / multiple)")
            # Lazy import — playwright is optional and not installed in production
            from .search_playwright import SearchParams, scrape_recording_numbers_with_playwright  # noqa: PLC0415
            params = SearchParams(document_code=document_codes[0], begin_date=begin, end_date=end)
            recs = scrape_recording_numbers_with_playwright(
                params=params,
                storage_state_path=args.storage_state,
                headful=bool(args.headful),
                proxy_server=(args.playwright_proxy or None),
            )
    if not recs:
        logger.warning("No recording numbers found (empty results or blocked)")
    logger.info(f"Found {len(recs)} recording numbers")

    # Persist the raw discovered recording numbers for auditing/debugging.
    _write_recording_numbers("output/recording_numbers_found.txt", recs)

    if args.limit and args.limit > 0:
        recs = recs[: int(args.limit)]
    _write_recording_numbers("output/recording_numbers_planned.txt", recs)

    proxy_provider = ProxyProvider.from_file(args.proxy_list)
    session = new_session()
    retry = RetryConfig(attempts=3, base_sleep_s=1.0, max_sleep_s=10.0)

    conn = None
    if not args.no_db:
        if not args.db_url:
            raise SystemExit("Missing DATABASE_URL (or pass --no-db for local prototype)")
        conn = connect(args.db_url)
        ensure_schema(conn)

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

    for i, rec in enumerate(recs, start=1):
        logger.info(f"Processing {rec} ({i}/{len(recs)})")
        proxies = proxy_provider.as_requests_proxies() if args.use_proxy else None

        if args.only_new:
            if conn is not None:
                doc_id0 = get_document_id(conn, rec)
                # Skip if (already in documents OR has any failure record) AND no active retry needed.
                # This prevents endless retries of permanently broken / non-200 records.
                already_seen = (doc_id0 is not None) or has_any_failure(conn, rec)
                if already_seen and not has_unresolved_failure(conn, rec):
                    logger.info(f"Skipping existing (DB): {rec}")
                    n_skipped += 1
                    continue
            else:
                if rec in seen_local:
                    logger.info(f"Skipping existing (local state): {rec}")
                    n_skipped += 1
                    continue

        stage = "start"
        doc_id: Optional[int] = None
        row: Optional[dict[str, Any]] = None
        try:
            stage = "metadata"
            meta = fetch_metadata(session, rec, proxies=proxies, retry=retry)

            # Gate: if the API returned a record with no document type it is 'broken'.
            # Nothing is stored — not in the DB, not in the CSV, not in the JSON output.
            if not _is_valid_metadata(meta):
                logger.warning("Skipping %s — API returned no document type (broken record)", rec)
                if conn is not None:
                    try:
                        record_failure(conn, rec, stage="metadata",
                                       error="API returned no document type (broken/invalid record)")
                        mark_resolved(conn, rec)  # permanently broken — don't retry
                    except Exception:
                        pass
                continue

            row = {
                "recordingNumber": meta.recording_number,
                "recordingDate": meta.recording_date,
                "documentCodes": meta.document_codes,
                "names": meta.names,
                "pageAmount": meta.page_amount,
            }

            # Valid record — add to outputs from this point forward.
            results.append(row)
            new_results.append(row)

            # Persist the existence of the document as soon as we have metadata.
            if conn is not None:
                try:
                    doc_id = upsert_document(conn, meta)
                except Exception as db_err:
                    logger.error("upsert_document failed for %s: %s — attempting reconnect", rec, db_err)
                    doc_id = None
                    # DB connection may have dropped while metadata API was slow.
                    # Reconnect and retry once.
                    try:
                        conn.close()
                    except Exception:
                        pass
                    try:
                        conn = connect(args.db_url)
                        ensure_schema(conn)
                        doc_id = upsert_document(conn, meta)
                        logger.info("upsert_document succeeded after reconnect for %s", rec)
                    except Exception as retry_err:
                        logger.error("upsert_document retry also failed for %s: %s", rec, retry_err)
                        doc_id = None

            fields = None
            if not args.metadata_only:
                # ---- PDF fetch → in-memory OCR (no disk write) ----
                # Use a separate inner try/except so a 404 PDF does NOT lose
                # the metadata row we already have. We still record the failure
                # in scrape_failures so it can be retried later.
                pdf_stage = "pdf"
                try:
                    if str(args.pdf_mode) == "memory":
                        pdf_bytes = fetch_pdf_bytes(session, rec, proxies=proxies, retry=retry)
                        pdf_stage = "ocr"
                        ocr_text = ocr_pdf_bytes_to_text(pdf_bytes)
                    else:
                        pdf_stage = "ocr"
                        pdf_path = download_pdf(session, rec, proxies=proxies, retry=retry)
                        ocr_text = ocr_pdf_to_text(pdf_path)

                    n_ocr += 1

                    # Store OCR text directly in the DB (no disk file written).
                    if conn is not None:
                        update_document_ocr_text(conn, rec, ocr_text)

                    # Extract structured fields from OCR text via LLM.
                    pdf_stage = "extract"
                    fields = extract_fields_llm(ocr_text)
                    n_llm += 1
                    row.update(asdict(fields))

                except Exception as pdf_err:
                    is_404 = "404" in str(pdf_err)
                    msg = f"PDF/OCR skipped for {rec} (stage={pdf_stage}): {pdf_err}" + (
                        " — PDF not available on legacy server (marking resolved, will not retry)" if is_404 else ""
                    )
                    logger.warning(msg)
                    row["pdfError"] = str(pdf_err)
                    if conn is not None:
                        try:
                            record_failure(conn, rec, stage=pdf_stage, error=str(pdf_err))
                            if is_404:
                                # 404 means the PDF simply does not exist on the legacy server.
                                # Mark resolved immediately so --only-new never retries this recording.
                                mark_resolved(conn, rec)
                        except Exception:
                            pass

            # Mark as processed (metadata stored; OCR may have been skipped)
            row.pop("error", None)
            row.pop("errorStage", None)

            if args.no_db and args.only_new:
                append_seen(args.seen_path, rec)
                seen_local.add(rec)

            if conn is not None:
                stage = "db"
                # doc_id set earlier at metadata stage; upsert again to be safe.
                if doc_id is None:
                    doc_id = upsert_document(conn, meta)
                if fields is not None:
                    from .llm_extract import _MODEL as _LLM_MODEL
                    upsert_properties(conn, doc_id, fields, llm_model=_LLM_MODEL)
                mark_processed(conn, rec)
                if not row.get("pdfError"):  # only resolve if fully clean
                    mark_resolved(conn, rec)

            n_processed += 1

            # If we got here without raising, the record is considered successfully processed.

        except Exception as e:
            logger.warning("Failed %s at stage=%s: %s", rec, stage, e)
            n_failed += 1
            # Record ALL failures (including non-200 metadata responses) into scrape_failures.
            # The main 'documents' table is never touched for failed records.
            # Non-200 metadata failures stay unresolved (can be retried).
            # 'start' stage means we never even issued a request — nothing to record.
            if conn is not None and stage != "start":
                try:
                    record_failure(conn, rec, stage=stage, error=str(e))
                except Exception:
                    pass
            # Do NOT append to results — keep CSV/JSON/DB output clean (valid docs only).

        if args.sleep and args.sleep > 0 and i < len(recs):
            time.sleep(float(args.sleep))

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
