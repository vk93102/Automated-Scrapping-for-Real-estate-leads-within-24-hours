"""
Gila County, AZ — Real Estate Lead Scraper — Pipeline Orchestrator
===================================================================
Usage
-----
  python live_pipeline.py [options]

Options
-------
  --start-date  MM/DD/YYYY   Recording date range start  (default: 30 days ago)
  --end-date    MM/DD/YYYY   Recording date range end    (default: today)
  --pages       N            Max result pages to fetch   (default: all)
  --ocr-limit   N            Max records to OCR+Groq     (default: 20)
  --no-groq                  Skip Groq, use regex only
  --csv-name    NAME         Output filename without path
  --doc-types   TYPE ...     Override document types
  --verbose                  Extra debug output

Stages
------
  1–3. PLAYWRIGHT — Launch Chromium, accept disclaimer, fill dates, inject
                    doc-type inputs, submit form, harvest page 1 + JSESSIONID
  4.   PAGINATE   — Fetch pages 2..N (requests, real-time streaming)
  5.   FILTER     — Client-side alias normalisation + target-type gate
  6.   DISPLAY    — Print live results table
  7.   DETAIL     — Fetch each document detail page (address, principal, pdfjs)
  8.   OCR+GROQ   — Discover PDF URL → download → OCR → Groq analysis
  9.   SAVE       — Write CSV + JSON to output/
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

# Allow running as `python gila/live_pipeline.py` from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg

from gila.extractor import (
    OUTPUT_DIR,
    DEFAULT_DOCUMENT_TYPES,
    GILA_DOC_TYPE_HOLDERS,
    _SERVER_ALIASES,
    playwright_search,
    _make_requests_session,
    fetch_all_pages,
    enrich_record_with_detail,
    enrich_record_with_ocr,
    export_csv,
    export_json,
)

# ── ANSI colours ──────────────────────────────────────────────────────────────
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[36m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_RESET  = "\033[0m"


def _c(text: str, colour: str) -> str:
    return f"{colour}{text}{_RESET}"


# ── Database helpers ──────────────────────────────────────────────────────────

def _load_env() -> None:
    """Load environment variables from .env file."""
    root_dir = Path(__file__).resolve().parent.parent
    env_file = root_dir / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


def _db_url_with_ssl(url: str) -> str:
    """Add sslmode=require to database URL if not already present."""
    u = (url or "").strip()
    if not u:
        return u
    if "sslmode=" in u.lower():
        return u
    return f"{u}{'&' if '?' in u else '?'}sslmode=require"


def _connect_db(database_url: str, retries: int = 3, sleep_s: int = 3) -> psycopg.Connection:
    """Connect to PostgreSQL database with retries."""
    last_err: Exception | None = None
    primary_url = _db_url_with_ssl(database_url)
    fallback_raw = (os.environ.get("DATABASE_URL_POOLER") or "").strip()
    fallback_url = _db_url_with_ssl(fallback_raw) if fallback_raw else ""

    host = (urlparse(primary_url).hostname or "").strip()
    if host:
        try:
            socket.getaddrinfo(host, 5432)
        except Exception as exc:
            if not fallback_url:
                pass

    for url in [u for u in [primary_url, fallback_url] if u]:
        for i in range(max(1, retries)):
            try:
                return psycopg.connect(url, connect_timeout=12)
            except Exception as exc:
                last_err = exc
                if i < retries - 1:
                    time.sleep(sleep_s)

    raise RuntimeError(f"DB connect failed after {retries} attempts: {last_err}")


def _ensure_schema(conn: psycopg.Connection) -> None:
    """Create gila_leads table if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute(
            """
            create table if not exists gila_leads (
              id               bigserial primary key,
              source_county    text not null default 'Gila',
              document_id      text not null,
              recording_number text,
              recording_date   text,
              document_type    text,
              grantors         text,
              grantees         text,
              trustor          text,
              trustee          text,
              beneficiary      text,
              principal_amount text,
              property_address text,
              detail_url       text,
              image_urls       text,
              ocr_method       text,
              ocr_chars        integer,
              used_groq        boolean,
              groq_model       text,
              groq_error       text,
              analysis_error   text,
              run_date         date,
              raw_record       jsonb not null default '{}'::jsonb,
              created_at       timestamptz not null default now(),
              updated_at       timestamptz not null default now(),
              unique (source_county, document_id)
            );
            """
        )
    conn.commit()


def _upsert_records_to_db(
    conn: psycopg.Connection, records: list[dict], run_date: date
) -> tuple[int, int, int]:
    """Upsert records to gila_leads table.
    
    Returns (inserted_count, updated_count, llm_used_count).
    """
    inserted = 0
    updated = 0
    llm_used = 0
    with conn.cursor() as cur:
        for r in records:
            doc_id = str(r.get("documentId", "") or "").strip()
            if not doc_id:
                continue
            used_groq = bool(r.get("usedGroq", False))
            if used_groq:
                llm_used += 1
            payload = {
                "source_county": r.get("sourceCounty") or "Gila",
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
                "detail_url": r.get("detailUrl", "") or r.get("documentUrl", ""),
                "image_urls": r.get("imageUrls", ""),
                "ocr_method": r.get("ocrMethod", ""),
                "ocr_chars": int(r.get("ocrChars") or 0),
                "used_groq": used_groq,
                "groq_model": r.get("groqModel", ""),
                "groq_error": r.get("groqError", ""),
                "analysis_error": r.get("analysisError", ""),
                "run_date": run_date,
                "raw_record": psycopg.types.json.Jsonb(r),
            }
            cur.execute(
                """
                insert into gila_leads (
                  source_county, document_id, recording_number, recording_date, document_type,
                  grantors, grantees, trustor, trustee, beneficiary, principal_amount, property_address,
                  detail_url, image_urls, ocr_method, ocr_chars, used_groq, groq_model, groq_error,
                  analysis_error, run_date, raw_record
                ) values (
                  %(source_county)s, %(document_id)s, %(recording_number)s, %(recording_date)s, %(document_type)s,
                  %(grantors)s, %(grantees)s, %(trustor)s, %(trustee)s, %(beneficiary)s, %(principal_amount)s, %(property_address)s,
                  %(detail_url)s, %(image_urls)s, %(ocr_method)s, %(ocr_chars)s, %(used_groq)s, %(groq_model)s, %(groq_error)s,
                  %(analysis_error)s, %(run_date)s, %(raw_record)s
                )
                on conflict (source_county, document_id) do update set
                  recording_number = excluded.recording_number,
                  recording_date   = excluded.recording_date,
                  document_type    = excluded.document_type,
                  grantors         = excluded.grantors,
                  grantees         = excluded.grantees,
                  trustor          = excluded.trustor,
                  trustee          = excluded.trustee,
                  beneficiary      = excluded.beneficiary,
                  principal_amount = excluded.principal_amount,
                  property_address = excluded.property_address,
                  detail_url       = excluded.detail_url,
                  image_urls       = excluded.image_urls,
                  ocr_method       = excluded.ocr_method,
                  ocr_chars        = excluded.ocr_chars,
                  used_groq        = excluded.used_groq,
                  groq_model       = excluded.groq_model,
                  groq_error       = excluded.groq_error,
                  analysis_error   = excluded.analysis_error,
                  run_date         = excluded.run_date,
                  raw_record       = excluded.raw_record,
                  updated_at       = now()
                returning (xmax = 0) as inserted;
                """,
                payload,
            )
            row = cur.fetchone()
            if row and row[0]:
                inserted += 1
            else:
                updated += 1
    conn.commit()
    return inserted, updated, llm_used


# ── Display helpers ───────────────────────────────────────────────────────────

_COL_WIDTHS = {
    "num":   4,
    "fee":   16,
    "date":  22,
    "docid": 14,
    "type":  28,
    "names": 44,
}
_ROW_SEP = "─" * (sum(_COL_WIDTHS.values()) + len(_COL_WIDTHS))


def _fmt(val: str, width: int) -> str:
    val = (val or "").strip()
    return val[:width].ljust(width)


def _print_header() -> None:
    print()
    print(
        _c("  #", _BOLD) + "  "
        + _c(_fmt("FEE / REC #", _COL_WIDTHS["fee"]), _BOLD) + "  "
        + _c(_fmt("DATE", _COL_WIDTHS["date"]), _BOLD) + "  "
        + _c(_fmt("DOC ID", _COL_WIDTHS["docid"]), _BOLD) + "  "
        + _c(_fmt("TYPE", _COL_WIDTHS["type"]), _BOLD) + "  "
        + _c("GRANTOR → GRANTEE", _BOLD)
    )
    print(_c(_ROW_SEP, _DIM))


def _print_row(idx: int, rec: dict, *, show_enrichment: bool = False) -> None:
    num   = str(idx).rjust(_COL_WIDTHS["num"])
    fee   = _fmt(rec.get("recordingNumber", ""), _COL_WIDTHS["fee"])
    date  = _fmt(rec.get("recordingDate", ""),   _COL_WIDTHS["date"])
    docid = _fmt(rec.get("documentId", ""),      _COL_WIDTHS["docid"])
    dtype = _fmt(rec.get("documentType", ""),    _COL_WIDTHS["type"])

    grantor = (rec.get("grantors") or "").split(" | ")[0][:20]
    grantee = (rec.get("grantees") or "").split(" | ")[0][:20]
    names   = f"{grantor} → {grantee}" if (grantor or grantee) else ""

    line = f"{num}  {fee}  {date}  {_c(docid, _CYAN)}  {_c(dtype, _YELLOW)}  {names}"
    print(line)

    if show_enrichment:
        addr  = rec.get("propertyAddress", "")
        amt   = rec.get("principalAmount", "")
        parts = []
        if addr:
            parts.append(f"address: {_c(addr[:60], _GREEN)}")
        if amt:
            parts.append(f"principal: {_c(amt, _GREEN)}")
        if parts:
            print("  " + " " * _COL_WIDTHS["num"] + "  ↳ " + "   ".join(parts))


def _print_summary(records: list[dict], start_date: str, end_date: str) -> None:
    n        = len(records)
    with_addr = sum(1 for r in records if r.get("propertyAddress"))
    with_amt  = sum(1 for r in records if r.get("principalAmount"))
    with_pdf  = sum(1 for r in records if r.get("documentUrl"))

    print()
    print(_c("═" * 72, _BOLD))
    print(_c(f"  GILA COUNTY RESULTS  "
             f"({n} documents  |  {start_date} → {end_date})", _BOLD))
    print(_c("═" * 72, _BOLD))
    print()
    _print_header()
    for i, rec in enumerate(records, 1):
        _print_row(i, rec, show_enrichment=True)
    print()
    print(f"  Records with address   : {_c(str(with_addr), _GREEN)}/{n}")
    print(f"  Records with principal : {_c(str(with_amt), _GREEN)}/{n}")
    print(f"  Records with PDF URL   : {_c(str(with_pdf), _GREEN)}/{n}")
    print()


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(
    start_date: str,
    end_date: str,
    page_limit: int         = 0,
    ocr_limit: int          = 0,
    use_groq: bool          = True,
    csv_name: str           = "",
    doc_types: list[str]    = None,
    verbose: bool           = False,
    write_output_files: bool = False,
    on_record_discovered: callable = None,  # real-time callback
) -> dict:
    """
    Run the full Gila County scraping pipeline.

    Returns
    -------
    dict with keys: records, csv_path, json_path, summary
    """
    doc_types  = doc_types or DEFAULT_DOCUMENT_TYPES
    groq_key   = os.getenv("GROQ_API_KEY", "")
    use_groq   = use_groq and bool(groq_key)
    ts_str     = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_name   = csv_name or f"gila_leads_{ts_str}.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path  = OUTPUT_DIR / csv_name
    json_path = OUTPUT_DIR / csv_name.replace(".csv", ".json")

    # ── STAGES 1–3: PLAYWRIGHT (Session → Form → Page 1) ─────────────────────
    print(_c("\n[STAGES 1–3/8] Playwright — Session + Form Submit + Page 1", _BOLD))
    print(f"  Start date : {start_date}")
    print(f"  End date   : {end_date}")
    print(f"  Doc types  : {len(doc_types)}")
    for dt in doc_types:
        print(f"               • {dt}")
    print()

    cookie_str, page1_records, summary = playwright_search(
        start_date = start_date,
        end_date   = end_date,
        doc_types  = doc_types,
        headless   = True,
        verbose    = verbose,
    )
    session = _make_requests_session(cookie_str)

    total      = summary.get("totalCount", len(page1_records))
    page_count = summary.get("pageCount", 1)
    if page_limit and page_limit > 0:
        page_count = min(page_count, page_limit)

    print(f"  Server total : {total} results across {page_count} pages")
    if summary.get("filterDescription"):
        print(f"  Server filter: {summary['filterDescription'][:120]}")

    if not page1_records:
        print(_c("  ⚠  No records returned on page 1.", _YELLOW))
        print("     Check selectors in playwright_search() or run with verbose=True.")

    # ── STAGE 4: PAGINATE ────────────────────────────────────────────────────
    print(_c(f"\n[STAGE 4/8] Pagination  (pages 1\u2013{page_count}  |  {total} total)", _BOLD))

    # Real-time display: print header once then stream each new row
    live_idx = [0]

    def _on_record(rec: dict) -> None:
        live_idx[0] += 1
        if live_idx[0] == 1:
            _print_header()
        _print_row(live_idx[0], rec)
        if on_record_discovered:
            on_record_discovered(rec)

    all_records = fetch_all_pages(
        session, page1_records, summary,
        page_limit=page_limit,
        verbose=True,
        on_record=_on_record,
    )

    # ── STAGE 5: CLIENT-SIDE FILTER ──────────────────────────────────────────
    print(_c("\n[STAGE 5/8] Client-side Filter", _BOLD))  # noqa: STAGE5
    # The server pre-filtered by our requested types, so most records should pass.
    # Records with no documentType in the list HTML are allowed through — their type
    # will be set by detail enrichment (Stage 6). Only positively-identified
    # non-target types are removed.
    _doc_types_upper = {t.upper() for t in doc_types}
    _holder_names    = {name.upper() for _, name in GILA_DOC_TYPE_HOLDERS}
    filtered = []
    for rec in all_records:
        raw = rec.get("documentType", "").upper()
        normalised = _SERVER_ALIASES.get(raw, raw)
        rec["documentType"] = normalised
        if (not normalised                             # no type yet → let detail enrichment decide
                or normalised in _doc_types_upper
                or raw in _doc_types_upper
                or normalised in _holder_names
                or raw in _holder_names):
            filtered.append(rec)
    removed = len(all_records) - len(filtered)
    print(f"  Kept {len(filtered)} target docs  (removed {removed} non-target)")
    all_records = filtered

    # ── STAGE 6: DETAIL PAGE ENRICHMENT ──────────────────────────────────────
    print(_c(f"\n[STAGE 6/8] Detail Page Enrichment ({len(all_records)} records)", _BOLD))
    t0 = time.time()
    for i, rec in enumerate(all_records, 1):
        doc_id = rec.get("documentId", "?")
        if verbose:
            print(f"  [{i}/{len(all_records)}] {doc_id} …", end=" ", flush=True)
        enrich_record_with_detail(rec, session, verbose=verbose)
        if not verbose:
            # Compact progress bar
            pct = int(i / len(all_records) * 40)
            bar = "█" * pct + "░" * (40 - pct)
            print(f"\r  [{bar}] {i}/{len(all_records)}", end="", flush=True)

    elapsed = time.time() - t0
    print(f"\r  ✓ Detail pages done in {elapsed:.1f}s" + " " * 30)

    # ── STAGE 7: OCR + GROQ ──────────────────────────────────────────────────
    # ocr_limit == 0  → all docs   |  ocr_limit > 0  → cap at N   |  -1 → skip
    ocr_candidates = list(all_records)          # attempt PDF+OCR on every doc
    if ocr_limit == -1:
        ocr_run = []
    elif ocr_limit == 0:
        ocr_run = ocr_candidates
    else:
        ocr_run = ocr_candidates[:ocr_limit]

    lbl = "skipped" if ocr_limit == -1 else (f"{len(ocr_run)}/{len(all_records)}" if ocr_limit > 0 else f"all {len(ocr_run)}")
    print(_c(f"\n[STAGE 7/8] OCR + {'Groq' if use_groq else 'Regex'} ({lbl} records)", _BOLD))

    if ocr_limit == -1:
        print("  OCR skipped (pass --ocr-limit 0 to run on all docs)")
    elif ocr_limit > 0 and len(ocr_run) < len(all_records):
        skipped = len(all_records) - len(ocr_run)
        print(f"  OCR capped by --ocr-limit: processing {len(ocr_run)}, skipping {skipped}")

    elif not use_groq and groq_key:
        print("  (Groq disabled by --no-groq flag)")
    elif not groq_key:
        print("  (Groq disabled — GROQ_API_KEY not set, using regex fallback)")

    for i, rec in enumerate(ocr_run, 1):
        doc_id = rec.get("documentId", "?")
        dtype  = rec.get("documentType", "")
        print(f"  [{i}/{len(ocr_run)}] {_c(doc_id, _CYAN)}  {dtype} …", flush=True)
        enrich_record_with_ocr(
            rec, session,
            use_groq=use_groq,
            groq_api_key=groq_key,
            verbose=True,
        )

    with_pdf_url = sum(1 for r in all_records if r.get("documentUrl"))
    with_ocr = sum(1 for r in all_records if r.get("ocrTextPath"))
    with_llm = sum(1 for r in all_records if r.get("usedGroq"))
    print(
        f"  Stage-7 coverage: PDF {with_pdf_url}/{len(all_records)}"
        f"  | OCR {with_ocr}/{len(all_records)}"
        f"  | LLM {with_llm}/{len(all_records)}"
    )

    # ── STAGE 8: SAVE ────────────────────────────────────────────────────────
    print(_c("\n[STAGE 8/8] Saving Output", _BOLD))  # noqa: STAGE8

    meta = {
        "county":     "Gila County, AZ",
        "platform":   "Tyler Technologies EagleWeb",
        "searchId":   "DOCSEARCH2242S1",
        "startDate":  start_date,
        "endDate":    end_date,
        "docTypes":   doc_types,
        "totalFound": len(all_records),
        "ocrRun":     len(ocr_run),
        "usedGroq":   use_groq,
        "timestamp":  datetime.now().isoformat(),
    }

    if write_output_files:
        export_csv(all_records, csv_path)
        export_json(all_records, json_path, meta=meta)
        print(f"  {_c('CSV ', _GREEN)} → {csv_path}")
        print(f"  {_c('JSON', _GREEN)} → {json_path}")
    else:
        csv_path = Path("")
        json_path = Path("")
        print(f"  {_c('DB-ONLY', _GREEN)} mode active (CSV/JSON disabled)")

    # ── FINAL SUMMARY TABLE ───────────────────────────────────────────────────
    _print_summary(all_records, start_date, end_date)

    return {
        "records":   all_records,
        "csv_path":  str(csv_path),
        "json_path": str(json_path),
        "summary":   meta,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _default_dates() -> tuple[str, str]:
    today = datetime.now()
    start = today - timedelta(days=30)
    return start.strftime("%-m/%-d/%Y"), today.strftime("%-m/%-d/%Y")


def main() -> None:
    _load_env()
    default_start, default_end = _default_dates()

    parser = argparse.ArgumentParser(
        description="Gila County AZ — Real Estate Lead Scraper"
    )
    parser.add_argument("--start-date", default=default_start,
                        help=f"Recording date start (default: {default_start})")
    parser.add_argument("--end-date",   default=default_end,
                        help=f"Recording date end (default: {default_end})")
    parser.add_argument("--pages",      type=int, default=0,
                        help="Max pages to fetch (0 = all)")
    parser.add_argument("--ocr-limit",  type=int, default=0,
                        help="Max records to OCR (0 = all [default], -1 = skip, N = cap at N)")
    parser.add_argument("--no-groq",    action="store_true",
                        help="Disable Groq LLM, use regex only")
    parser.add_argument("--csv-name",   default="",
                        help="Output CSV filename (auto-timestamped if omitted)")
    parser.add_argument("--doc-types",  nargs="+",
                        default=DEFAULT_DOCUMENT_TYPES,
                        help="Document types to search for")
    parser.add_argument("--verbose",    action="store_true",
                        help="Extra debug output")
    parser.add_argument("--store-db",   action="store_true",
                        help="Store results to PostgreSQL database")
    parser.add_argument("--write-files", action="store_true",
                        help="Write output CSV/JSON files (default: disabled)")

    args = parser.parse_args()

    print(_c("\n══════════════════════════════════════════════════", _BOLD))
    print(_c("  GILA COUNTY AZ — REAL ESTATE LEAD SCRAPER", _BOLD))
    print(_c("  Tyler Technologies EagleWeb | DOCSEARCH2242S1", _DIM))
    print(_c("══════════════════════════════════════════════════", _BOLD))
    if args.store_db:
        print(_c("  Store DB: enabled", _GREEN))

    res = run_pipeline(
        start_date = args.start_date,
        end_date   = args.end_date,
        page_limit = args.pages,
        ocr_limit  = args.ocr_limit,
        use_groq   = not args.no_groq,
        csv_name   = args.csv_name,
        doc_types  = args.doc_types,
        verbose    = args.verbose,
        write_output_files = args.write_files,
    )

    records = res.get("records", [])

    # Store to database if requested
    if args.store_db:
        db_url = (os.environ.get("DATABASE_URL") or "").strip()
        if not db_url:
            print(_c(" ❌ DB Store: Failed (DATABASE_URL not set)", _RED))
        else:
            try:
                with _connect_db(db_url) as conn:
                    _ensure_schema(conn)
                    db_inserted, db_updated, db_llm_used = _upsert_records_to_db(conn, records, date.today())
                print(_c(f" ✓ DB Store: {db_inserted} inserted, {db_updated} updated, {db_llm_used} used LLM", _GREEN))
            except Exception as e:
                print(_c(f" ❌ DB Store: Failed ({e})", _RED))


if __name__ == "__main__":
    main()
