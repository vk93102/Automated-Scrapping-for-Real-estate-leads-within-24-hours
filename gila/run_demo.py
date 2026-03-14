"""
Gila County — Live Real-Time Fetching Demo
==========================================
Runs the full pipeline for the last 30 days and shows every record
AS IT IS DISCOVERED — no buffering, no skipping.

This script is the live demonstration entry point.

Run
---
  python gila/run_demo.py

  # Custom date range:
  python gila/run_demo.py --start-date 1/1/2026 --end-date 3/14/2026

  # With PDF OCR (up to 5 docs):
  python gila/run_demo.py --ocr-limit 5

  # With Groq LLM extraction (requires GROQ_API_KEY in .env):
  python gila/run_demo.py --ocr-limit 5 --groq
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Load .env from gila/ directory if present
_gila_dir = Path(__file__).parent
_env_path = _gila_dir / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# Allow `python gila/run_demo.py` from repo root
sys.path.insert(0, str(_gila_dir.parent))

from gila.extractor import (
    BASE_URL,
    SEARCH_ID,
    DEFAULT_DOCUMENT_TYPES,
    playwright_search,
    _make_requests_session,
    fetch_all_pages,
    enrich_record_with_detail,
    enrich_record_with_ocr,
    export_csv,
    export_json,
    OUTPUT_DIR,
)

# ── ANSI ──────────────────────────────────────────────────────────────────────
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[36m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_RESET  = "\033[0m"
_BLUE   = "\033[34m"


def c(text: str, col: str) -> str:
    return f"{col}{text}{_RESET}"


# ── Real-time row printer ─────────────────────────────────────────────────────

_ROW_TMPL = "{num:>4}  {fee:<16}  {date:<22}  {docid:<14}  {dtype:<26}  {names}"
_HEADER   = _ROW_TMPL.format(
    num="  #", fee="FEE / REC #", date="DATE",
    docid="DOC ID", dtype="TYPE", names="GRANTOR → GRANTEE",
)
_SEP = "─" * len(_HEADER)

_counter = [0]  # mutable so closure can update it
_header_printed = [False]


def _stream_record(rec: dict) -> None:
    """Print one record row as soon as it is discovered."""
    if not _header_printed[0]:
        print()
        print(c(_HEADER, _BOLD))
        print(c(_SEP, _DIM))
        _header_printed[0] = True

    _counter[0] += 1
    grantor = (rec.get("grantors") or "—").split(" | ")[0][:22]
    grantee = (rec.get("grantees") or "—").split(" | ")[0][:22]
    names   = f"{grantor} → {grantee}"

    row = _ROW_TMPL.format(
        num   = _counter[0],
        fee   = (rec.get("recordingNumber") or "")[:16],
        date  = (rec.get("recordingDate")   or "")[:22],
        docid = (rec.get("documentId")      or "")[:14],
        dtype = (rec.get("documentType")    or "")[:26],
        names = names,
    )
    # Colour the doc ID and type
    row = row.replace(
        (rec.get("documentId") or "")[:14],
        c((rec.get("documentId") or "")[:14], _CYAN),
        1,
    ).replace(
        (rec.get("documentType") or "")[:26],
        c((rec.get("documentType") or "")[:26], _YELLOW),
        1,
    )
    print(row, flush=True)


# ── Demo runner ───────────────────────────────────────────────────────────────

def run_demo(
    start_date: str,
    end_date:   str,
    page_limit: int  = 0,
    ocr_limit:  int  = 0,
    use_groq:   bool = False,
) -> None:
    groq_key = os.getenv("GROQ_API_KEY", "")
    use_groq  = use_groq and bool(groq_key)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path  = OUTPUT_DIR / f"gila_demo_{ts}.csv"
    json_path = OUTPUT_DIR / f"gila_demo_{ts}.json"

    # ── BANNER ────────────────────────────────────────────────────────────────
    print()
    print(c("╔══════════════════════════════════════════════════════════════╗", _BOLD))
    print(c("║     GILA COUNTY AZ — REAL-TIME LEAD SCRAPING DEMO           ║", _BOLD))
    print(c("║     Tyler Technologies EagleWeb | DOCSEARCH2242S1            ║", _DIM))
    print(c("╚══════════════════════════════════════════════════════════════╝", _BOLD))
    print()
    print(f"  Base URL   : {c(BASE_URL, _CYAN)}")
    print(f"  Search ID  : {c(SEARCH_ID, _CYAN)}")
    print(f"  Date range : {c(start_date, _GREEN)} → {c(end_date, _GREEN)}")
    print(f"  Doc types  : {len(DEFAULT_DOCUMENT_TYPES)}")
    for dt in DEFAULT_DOCUMENT_TYPES:
        print(f"               • {dt}")
    print(f"  OCR limit  : {ocr_limit if ocr_limit > 0 else ('all' if ocr_limit == 0 else 'skipped')}")
    print(f"  Groq LLM   : {'✓ enabled' if use_groq else '✗ disabled (regex fallback)'}")
    print()
    time.sleep(0.5)  # brief pause before connecting

    # ── STAGES 1–3: Playwright (Session → Form → Page 1) ────────────────────
    print(c("━━━  STAGES 1–3: PLAYWRIGHT (Session → Form → Page 1)  ━━━", _BOLD))
    print("  Launching Chromium to handle the JS-rendered search form …")
    t0 = time.time()
    cookie_str, page1_records, summary = playwright_search(
        start_date = start_date,
        end_date   = end_date,
        doc_types  = DEFAULT_DOCUMENT_TYPES,
        headless   = True,
        verbose    = True,
    )
    session    = _make_requests_session(cookie_str)
    elapsed    = time.time() - t0
    total      = summary.get("totalCount", len(page1_records))
    page_count = summary.get("pageCount", 1)
    print(f"  Server reports : {c(str(total), _GREEN)} results  |  {page_count} pages")
    if summary.get("filterDescription"):
        print(f"  Server filter  : {summary['filterDescription'][:120]}")
    print(f"  Done in {elapsed:.1f}s")
    print()

    # ── STAGE 4: REAL-TIME STREAM ────────────────────────────────────────────
    print(c("━━━  STAGE 4: PAGINATING (records stream in real time ↓)  ━━━", _BOLD))
    t0 = time.time()

    # fetch_all_pages fires on_record for page-1 records first, then paginates
    all_records = fetch_all_pages(
        session, page1_records, summary,
        page_limit = page_limit,
        verbose    = True,
        on_record  = _stream_record,
    )
    elapsed = time.time() - t0
    print()
    print(f"  {c(str(len(all_records)), _GREEN)} documents collected in {elapsed:.1f}s")

    # ── STAGE 5: FILTER ───────────────────────────────────────────────────────
    from gila.extractor import _SERVER_ALIASES
    print()
    print(c("━━━  STAGE 5: CLIENT-SIDE FILTER  ━━━", _BOLD))
    target_set = set(DEFAULT_DOCUMENT_TYPES)
    filtered   = []
    for rec in all_records:
        raw = rec.get("documentType", "").upper()
        normalised = _SERVER_ALIASES.get(raw, raw)
        rec["documentType"] = normalised
        if normalised in target_set or raw in target_set:
            filtered.append(rec)
    removed = len(all_records) - len(filtered)
    print(f"  Kept {c(str(len(filtered)), _GREEN)} target docs  "
          f"(removed {removed} non-target)")
    all_records = filtered

    # ── STAGE 6: DETAIL ENRICHMENT ────────────────────────────────────────────
    print()
    print(c(f"━━━  STAGE 6: DETAIL PAGE ENRICHMENT ({len(all_records)} docs)  ━━━", _BOLD))
    print("  Fetching address, fee number, grantor/grantee from each detail page …")
    t0 = time.time()
    for i, rec in enumerate(all_records, 1):
        doc_id = rec.get("documentId", "?")
        print(f"  [{i:>3}/{len(all_records)}] {c(doc_id, _CYAN)} ", end="", flush=True)
        enrich_record_with_detail(rec, session, verbose=False)
        addr = rec.get("propertyAddress", "")
        amt  = rec.get("principalAmount",  "")
        print(
            f"addr={c('✓', _GREEN) if addr else c('—', _DIM)}  "
            f"amt={c('✓', _GREEN) if amt else c('—', _DIM)}"
        )
    print(f"  ✓ Done in {time.time()-t0:.1f}s")

    # ── STAGE 7: OCR + GROQ ───────────────────────────────────────────────────
    # ocr_limit == 0  → all docs   |  ocr_limit > 0  → cap at N   |  -1 → skip
    ocr_candidates = list(all_records)          # attempt PDF+OCR on every doc
    if ocr_limit == -1:
        ocr_run = []
    elif ocr_limit == 0:
        ocr_run = ocr_candidates                # unlimited — all of them
    else:
        ocr_run = ocr_candidates[:ocr_limit]    # capped

    print()
    lbl = "skipped" if ocr_limit == -1 else (f"{len(ocr_run)}/{len(all_records)}" if ocr_limit > 0 else f"all {len(ocr_run)}")
    print(c(f"━━━  STAGE 7: PDF DISCOVERY + OCR  ({lbl} docs)  ━━━", _BOLD))

    if ocr_limit == -1:
        print("  OCR skipped (pass --ocr-limit 0 to run on all docs)")
    else:
        for i, rec in enumerate(ocr_run, 1):
            doc_id = rec.get("documentId", "?")
            dtype  = rec.get("documentType", "")
            print()
            print(f"  [{i}/{len(ocr_run)}] {c(doc_id, _CYAN)}  {c(dtype, _YELLOW)}")
            enrich_record_with_ocr(
                rec, session,
                use_groq    = use_groq,
                groq_api_key = groq_key,
                verbose     = True,
            )
            url_found = bool(rec.get("documentUrl"))
            pdf_ok    = bool(rec.get("ocrMethod") and rec["ocrMethod"] != "none")
            err       = rec.get("documentAnalysisError", "")
            print(
                f"    PDF URL discovered : {c('✓', _GREEN) if url_found else c('✗', _RED)}"
                + (f"  {c(rec.get('documentUrl','')[:80], _DIM)}" if url_found else "")
            )
            if err:
                print(f"    {c('Download note', _YELLOW)}: {err}")
            if rec.get("propertyAddress"):
                print(f"    Address   : {c(rec['propertyAddress'], _GREEN)}")
            if rec.get("principalAmount"):
                print(f"    Principal : {c(rec['principalAmount'], _GREEN)}")

    # ── STAGE 8: SAVE ─────────────────────────────────────────────────────────
    print()
    print(c("━━━  STAGE 8: SAVING OUTPUT  ━━━", _BOLD))
    meta = {
        "county":     "Gila County, AZ",
        "baseUrl":    BASE_URL,
        "searchId":   SEARCH_ID,
        "startDate":  start_date,
        "endDate":    end_date,
        "docTypes":   DEFAULT_DOCUMENT_TYPES,
        "totalFound": len(all_records),
        "ocrRun":     len(ocr_run),
        "usedGroq":   use_groq,
        "timestamp":  datetime.now().isoformat(),
    }
    export_csv(all_records, csv_path)
    export_json(all_records, json_path, meta=meta)
    print(f"  {c('CSV ', _GREEN)} → {csv_path}")
    print(f"  {c('JSON', _GREEN)} → {json_path}")

    # ── FINAL TABLE ────────────────────────────────────────────────────────────
    print()
    print(c("╔══════════════════════════════════════════════════════════════╗", _BOLD))
    print(c(f"║  FINAL ENRICHED TABLE  ({len(all_records)} docs | {start_date} → {end_date})",  _BOLD))
    print(c("╚══════════════════════════════════════════════════════════════╝", _BOLD))
    print()
    print(c(_HEADER, _BOLD))
    print(c(_SEP, _DIM))
    for i, rec in enumerate(all_records, 1):
        grantor = (rec.get("grantors") or "—").split(" | ")[0][:22]
        grantee = (rec.get("grantees") or "—").split(" | ")[0][:22]
        row = _ROW_TMPL.format(
            num   = i,
            fee   = (rec.get("recordingNumber") or "")[:16],
            date  = (rec.get("recordingDate")   or "")[:22],
            docid = (rec.get("documentId")      or "")[:14],
            dtype = (rec.get("documentType")    or "")[:26],
            names = f"{grantor} → {grantee}",
        )
        print(row)
        addr = rec.get("propertyAddress", "")
        amt  = rec.get("principalAmount",  "")
        url  = rec.get("documentUrl",      "")
        if addr or amt or url:
            parts = []
            if addr:
                parts.append(f"address: {c(addr[:55], _GREEN)}")
            if amt:
                parts.append(f"principal: {c(amt, _GREEN)}")
            if url:
                parts.append(f"pdf: {c(url[-50:], _BLUE)}")
            print("       ↳ " + "   ".join(parts))

    print()
    with_addr = sum(1 for r in all_records if r.get("propertyAddress"))
    with_amt  = sum(1 for r in all_records if r.get("principalAmount"))
    with_url  = sum(1 for r in all_records if r.get("documentUrl"))
    with_ocr  = sum(1 for r in all_records if r.get("ocrMethod") and r["ocrMethod"] != "none")

    n = len(all_records)
    print(f"  Records with address   : {c(str(with_addr), _GREEN)}/{n}")
    print(f"  Records with principal : {c(str(with_amt),  _GREEN)}/{n}")
    print(f"  Records with PDF URL   : {c(str(with_url),  _GREEN)}/{n}")
    print(f"  Records OCR'd          : {c(str(with_ocr),  _GREEN)}/{n}")
    print()
    print(c("  Demo complete. ✓", _GREEN + _BOLD))
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    today = datetime.now()
    start = (today - timedelta(days=30)).strftime("%-m/%-d/%Y")
    end   = today.strftime("%-m/%-d/%Y")

    parser = argparse.ArgumentParser(
        description="Gila County AZ — Live Real-Time Fetching Demo"
    )
    parser.add_argument("--start-date", default=start)
    parser.add_argument("--end-date",   default=end)
    parser.add_argument("--pages",      type=int, default=0,
                        help="Max pages (0 = all)")
    parser.add_argument("--ocr-limit",  type=int, default=0,
                        help="Max docs to OCR (0 = all [default], -1 = skip, N = cap at N)")
    parser.add_argument("--groq",       action="store_true",
                        help="Enable Groq LLM (requires GROQ_API_KEY in .env)")
    args = parser.parse_args()

    run_demo(
        start_date = args.start_date,
        end_date   = args.end_date,
        page_limit = args.pages,
        ocr_limit  = args.ocr_limit,
        use_groq   = args.groq,
    )


if __name__ == "__main__":
    main()
