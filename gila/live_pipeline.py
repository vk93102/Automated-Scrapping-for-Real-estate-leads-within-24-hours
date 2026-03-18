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
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Allow running as `python gila/live_pipeline.py` from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

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
    write_output_files: bool = True,
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

    args = parser.parse_args()

    print(_c("\n══════════════════════════════════════════════════", _BOLD))
    print(_c("  GILA COUNTY AZ — REAL ESTATE LEAD SCRAPER", _BOLD))
    print(_c("  Tyler Technologies EagleWeb | DOCSEARCH2242S1", _DIM))
    print(_c("══════════════════════════════════════════════════", _BOLD))

    run_pipeline(
        start_date = args.start_date,
        end_date   = args.end_date,
        page_limit = args.pages,
        ocr_limit  = args.ocr_limit,
        use_groq   = not args.no_groq,
        csv_name   = args.csv_name,
        doc_types  = args.doc_types,
        verbose    = args.verbose,
    )


if __name__ == "__main__":
    main()
