#!/usr/bin/env python3
"""live_pipeline.py — Full Coconino County document scraping pipeline.

Pipeline stages:
  1. AUTH+SEARCH — Playwright opens the search page, accepts the disclaimer,
                   fills date range, injects document-type hidden inputs, submits
                   the form, waits for page-1 results.  Playwright handles every
                   cookie / CSRF token automatically.
  2. PAGINATE   — requests paginate every remaining page via
                   /web/searchResults/DOCSEARCH1213S1?page=N using the JSESSIONID
                   that is already bound to the active search on the server.
  3. FILTER     — client-side filter: keep only target document types.
  4. DISPLAY    — real-time table: fee#, date, doc ID, type, grantor → grantee.
  5. DETAIL     — fetch each document detail page → propertyAddress + principalAmount.
  6. OCR        — download the real PDF (GUID-based URL from the detail page),
                   run pdftotext → fallback tesseract when text is sparse.
  7. GROQ       — pass OCR text to Llama-3 via Groq API → extract address + principal.
  8. SAVE       — write enriched CSV to output/coconino_pipeline_<timestamp>.csv.

Usage:
  python live_pipeline.py [--start-date MM/DD/YYYY] [--end-date MM/DD/YYYY]
                          [--pages N] [--ocr-limit N] [--headful] [--no-groq]
                          [--csv-name NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from extractor import (
    OUTPUT_DIR,
    enrich_records_with_detail_fields,
    export_csv,
    fetch_document_ocr_and_analysis,
    fetch_session_results_pages,
    load_env,
    parse_search_results_html,
    run_live_search,
)
import db_supabase

STATE_FILE = OUTPUT_DIR / "session_state.json"


def _cookie_from_storage_state(path: Path) -> str:
    """Build a Cookie header string from a Playwright storage_state JSON file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    cookies = data.get("cookies")
    if not isinstance(cookies, list):
        return ""
    parts: list[str] = []
    for c in cookies:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        value = str(c.get("value") or "")
        if name:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def _search_all_records(
    start_date: str,
    end_date: str,
    doc_types: list[str],
    headless: bool,
    page_limit: int | None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Return (cookie_header, records, summary) without hard-stopping on CAPTCHA."""
    try:
        cookie, page1_records, page1_summary = _playwright_search(
            start_date=start_date,
            end_date=end_date,
            doc_types=doc_types,
            headless=headless,
            page_limit=page_limit,
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "reCAPTCHA" not in msg:
            raise

        print(f"[WARN] {msg}")

        # 1) Best-effort: use already-saved cookies and run the requests-only flow.
        if STATE_FILE.exists():
            cookie_from_state = _cookie_from_storage_state(STATE_FILE)
            if cookie_from_state.strip():
                print("[AUTH] Trying requests-only live search with stored cookies …")
                res = run_live_search(
                    start_date=start_date,
                    end_date=end_date,
                    document_types=doc_types,
                    page_limit=page_limit,
                    cookie=cookie_from_state,
                    save_html=True,
                )
                records = list(res.get("records", []) or [])
                summary = dict(res.get("summary", {}) or {})
                if records:
                    print(f"[SEARCH] Requests-only search succeeded: {len(records)} records")
                    return cookie_from_state, records, summary
                print("[WARN] Stored-cookie search returned 0 records; cookies may be expired.")

        # 2) Optional: interactive retry to refresh cookies.
        allow_headful_retry = str(os.environ.get("COCONINO_CAPTCHA_HEADFUL_RETRY", "")).strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if headless and allow_headful_retry:
            print("[AUTH] Retrying Playwright in headful mode to refresh cookies …")
            cookie, page1_records, page1_summary = _playwright_search(
                start_date=start_date,
                end_date=end_date,
                doc_types=doc_types,
                headless=False,
                page_limit=page_limit,
            )
        else:
            raise RuntimeError(
                "Blocked by Coconino disclaimer reCAPTCHA and no valid stored-cookie session was available. "
                "Run once with `--headful` to accept the disclaimer/captcha (this will save cookies to output/session_state.json), "
                "or set `COCONINO_CAPTCHA_HEADFUL_RETRY=1` to auto-retry headful when headless is blocked."
            )

    if not cookie.strip():
        raise RuntimeError("[AUTH] Failed to extract session cookies.")

    # With a valid cookie, paginate remaining pages via requests.
    all_records, summary = _paginate_all_pages(cookie, page1_records, page1_summary, page_limit)
    return cookie, all_records, summary

# ─── Target document types ────────────────────────────────────────────────────

TARGET_DOC_TYPES: list[str] = [
    "LIS PENDENS",
    "LIS PENDENS RELEASE",
    "TRUSTEES DEED UPON SALE",
    "SHERIFFS DEED",
    "NOTICE OF TRUSTEES SALE",
    "TREASURERS DEED",
    "AMENDED STATE LIEN",
    "STATE LIEN",
    "STATE TAX LIEN",
    "RELEASE STATE TAX LIEN",
]

# Aliases: server-returned variants → canonical name
_SERVER_ALIASES: dict[str, str] = {
    "TRUSTEE'S DEED": "TRUSTEES DEED UPON SALE",
    "TRUSTEES DEED": "TRUSTEES DEED UPON SALE",
    "NOTICE OF TRUSTEE'S SALE": "NOTICE OF TRUSTEES SALE",
    "NOTICE OF TRUSTEE SALE": "NOTICE OF TRUSTEES SALE",
    "SHERIFF'S DEED": "SHERIFFS DEED",
    "TREASURER'S DEED": "TREASURERS DEED",
}

_TARGET_SET = {_SERVER_ALIASES.get(t.upper(), t.upper()) for t in TARGET_DOC_TYPES}


def _is_target(doc_type: str) -> bool:
    up = doc_type.strip().upper()
    return _SERVER_ALIASES.get(up, up) in _TARGET_SET


# ─── Display helpers ─────────────────────────────────────────────────────────

def _fmt_names(names: Any) -> str:
    if isinstance(names, list):
        return " | ".join(str(n) for n in names if str(n).strip())
    return str(names or "").strip()


def _trunc(text: str, width: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_header() -> None:
    print()
    print(
        f"{'#':>4}  "
        f"{'FEE / REC #':<18}  "
        f"{'DATE':<19}  "
        f"{'DOC ID':<14}  "
        f"{'TYPE':<26}  "
        f"GRANTOR → GRANTEE"
    )
    print("─" * 140)


def _print_row(idx: int, rec: dict[str, Any]) -> None:
    grantor = _trunc(_fmt_names(rec.get("grantors")), 30)
    grantee = _trunc(_fmt_names(rec.get("grantees")), 30)
    parties = f"{grantor} → {grantee}" if grantee else grantor
    print(
        f"{idx:>4}  "
        f"{_trunc(rec.get('recordingNumber',''), 18):<18}  "
        f"{_trunc(rec.get('recordingDate',''), 19):<19}  "
        f"{_trunc(rec.get('documentId',''), 14):<14}  "
        f"{_trunc(rec.get('documentType',''), 26):<26}  "
        f"{parties}"
    )


def _print_enriched(rec: dict[str, Any]) -> None:
    addr = _trunc(rec.get("propertyAddress", ""), 60) or "—"
    amt = _trunc(rec.get("principalAmount", ""), 20) or "—"
    print(f"{'':>4}  {'':18}  ↳ address: {addr}   principal: {amt}")


# ─── Stage 1: Playwright auth + form submit ──────────────────────────────────

def _playwright_search(
    start_date: str,
    end_date: str,
    doc_types: list[str],
    headless: bool = True,
    page_limit: int | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Open Playwright, submit the search form, return (cookie_header, page1_records, summary)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright is required. Run:\n"
            "  pip install playwright && python -m playwright install chromium"
        )

    BASE_SEARCH = "https://eagleassessor.coconino.az.gov:8444/web/search/DOCSEARCH1213S1"
    RESULTS_BASE = "https://eagleassessor.coconino.az.gov:8444/web/searchResults/DOCSEARCH1213S1"
    print("[AUTH] Launching Playwright …")

    page1_records: list[dict[str, Any]] = []
    page1_summary: dict[str, Any] = {}
    cookie_header = ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)

        ctx = (
            browser.new_context(storage_state=str(STATE_FILE))
            if STATE_FILE.exists()
            else browser.new_context()
        )
        print(f"[AUTH] Session: {'reused' if STATE_FILE.exists() else 'new'}")

        page = ctx.new_page()
        page.goto(BASE_SEARCH, wait_until="domcontentloaded", timeout=120_000)

        # Accept disclaimer if visible
        for sel in [
            "text=I Accept",
            "button:has-text('Accept')",
            "a:has-text('Accept')",
            "input[value='Accept']",
        ]:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=5_000)
                    page.wait_for_timeout(1_500)
                    print("[AUTH] Disclaimer accepted.")
                    break
            except Exception:
                continue

        # If we are still on disclaimer with reCAPTCHA, headless automation cannot proceed.
        if "/web/user/disclaimer" in page.url:
            has_recaptcha = page.locator(".g-recaptcha, iframe[src*='recaptcha']").count() > 0
            if has_recaptcha:
                if headless:
                    raise RuntimeError(
                        "Blocked by Coconino disclaimer reCAPTCHA. "
                        "Run in headful mode once and manually click 'I Accept', then re-run interval job."
                    )
                print("[AUTH] Waiting for manual disclaimer/captcha acceptance in headful mode …")
                # Allow plenty of time for manual interaction.
                wait_ms = int(os.environ.get("COCONINO_CAPTCHA_WAIT_MS", "1800000") or 1800000)
                page.wait_for_url("**/web/search/**", timeout=wait_ms, wait_until="domcontentloaded")

        # Fill date range
        if page.locator("#field_rdate_DOT_StartDate").count() > 0:
            page.fill("#field_rdate_DOT_StartDate", start_date)
            print(f"[FORM] Start date: {start_date}")
        if page.locator("#field_rdate_DOT_EndDate").count() > 0:
            page.fill("#field_rdate_DOT_EndDate", end_date)
            print(f"[FORM] End date:   {end_date}")

        # Inject document-type hidden inputs (mirrors what the autocomplete widget posts)
        # Server reads repeated field_selfservice_documentTypes-searchInput entries.
        if doc_types:
            injected = page.evaluate(
                """(types) => {
                    const form = document.querySelector('form');
                    if (!form) return 0;
                    form.querySelectorAll('input[data-injected-doctype]').forEach(el => el.remove());
                    types.forEach(t => {
                        const h = document.createElement('input');
                        h.type = 'hidden';
                        h.name = 'field_selfservice_documentTypes-searchInput';
                        h.value = t;
                        h.setAttribute('data-injected-doctype', '1');
                        form.appendChild(h);
                    });
                    const op = document.createElement('input');
                    op.type = 'hidden';
                    op.name = 'field_selfservice_documentTypes-containsInput';
                    op.value = 'Contains Any';
                    op.setAttribute('data-injected-doctype', '1');
                    form.appendChild(op);
                    return types.length;
                }""",
                doc_types,
            )
            print(f"[FORM] Injected {injected} document-type hidden inputs")

        # Submit search
        if page.locator("#searchButton").count() > 0:
            page.click("#searchButton", timeout=20_000)
        elif page.locator("a:has-text('Search')").count() > 0:
            page.locator("a:has-text('Search')").first.click(timeout=20_000)
        print("[FORM] Search submitted — waiting for results …")

        # Wait for page-1 results
        try:
            page.wait_for_selector("li.ss-search-row", timeout=120_000)
            page.wait_for_timeout(1_000)
        except Exception:
            print("[WARN] Timeout waiting for search rows; trying AJAX fallback …")
            ajax_headers = {
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
                "ajaxrequest": "true",
            }
            resp = ctx.request.get(
                f"{RESULTS_BASE}?page=1",
                headers=ajax_headers,
                timeout=90_000,
            )
            html_content = resp.text() if resp.ok else ""
            if html_content:
                parsed = parse_search_results_html(html_content, "ajax_fallback_page1.html")
                page1_records = list(parsed.get("records", []))
                page1_summary = parsed.get("summary", {})

        if not page1_records:
            # Parse page-1 HTML from the Playwright page
            html_content = page.content()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            html_path = OUTPUT_DIR / f"playwright_results_{ts}.html"
            html_path.write_text(html_content, encoding="utf-8")
            parsed = parse_search_results_html(html_content, html_path.name)
            page1_records = list(parsed.get("records", []))
            page1_summary = parsed.get("summary", {})
            print(f"[SEARCH] Page 1 via Playwright: {len(page1_records)} records")

        # Save session state so JSESSIONID stays fresh for pagination
        ctx.storage_state(path=str(STATE_FILE))

        # Build cookie header from the context that has the active search session
        cookies = ctx.cookies()
        cookie_header = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies if c.get("name")
        )
        print(f"[AUTH] Cookie extracted ({len(cookie_header)} chars, {len(cookies)} cookies)")

        browser.close()

    return cookie_header, page1_records, page1_summary


# ─── Stage 2: Paginate remaining pages via requests ──────────────────────────

def _paginate_all_pages(
    cookie: str,
    page1_records: list[dict[str, Any]],
    page1_summary: dict[str, Any],
    page_limit: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    total_pages = int(page1_summary.get("pageCount") or 1)
    total_results = page1_summary.get("totalResults", "?")
    filter_summary = page1_summary.get("filterSummary", "")
    print(
        f"[SEARCH] Server total: {total_results} results across {total_pages} pages"
    )
    if filter_summary:
        print(f"[SEARCH] Server filter: {filter_summary}")

    # Effective page limit
    max_page = total_pages
    if page_limit is not None:
        max_page = min(total_pages, page_limit)

    if max_page <= 1:
        return page1_records, page1_summary

    # Pages 2..max_page via requests (the JSESSIONID is tied to the active search)
    print(f"[PAGINATE] Fetching pages 2–{max_page} via requests …")
    extra = fetch_session_results_pages(cookie, page_limit=max_page, save_html=True)
    extra_records = list(extra.get("records", []))
    # fetch_session_results_pages starts from page 1 too; deduplicate by documentId
    combined_by_id: dict[str, dict[str, Any]] = {}
    for rec in page1_records + extra_records:
        key = str(rec.get("documentId", "")) or str(rec.get("recordingNumber", ""))
        if key and key not in combined_by_id:
            combined_by_id[key] = rec
    all_records = list(combined_by_id.values())
    summary = extra.get("summary", page1_summary)
    print(f"[PAGINATE] Total unique records: {len(all_records)}")
    return all_records, summary


# ─── Stage 3: Client-side filter ─────────────────────────────────────────────

def _apply_filter(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [r for r in records if _is_target(str(r.get("documentType", "")))]
    removed = len(records) - len(filtered)
    print(f"[FILTER] Kept {len(filtered)} target docs  (removed {removed} non-target)")
    return filtered


# ─── Main pipeline ────────────────────────────────────────────────────────────

def run_pipeline(
    start_date: str,
    end_date: str,
    page_limit: int | None = None,
    ocr_limit: int = 20,
    headless: bool = True,
    use_groq: bool = True,
    csv_name: str | None = None,
    doc_types: list[str] | None = None,
    write_output_files: bool = True,
) -> dict[str, Any]:
    load_env()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    effective_types = doc_types or TARGET_DOC_TYPES

    # ── Stage 1+2: Search + pagination (CAPTCHA-safe) ───────────────────────
    try:
        cookie, all_records, summary = _search_all_records(
            start_date=start_date,
            end_date=end_date,
            doc_types=effective_types,
            headless=headless,
            page_limit=page_limit,
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "reCAPTCHA" not in msg:
            raise
        print(f"[WARN] {msg}")
        return {
            "ok": False,
            "startDate": start_date,
            "endDate": end_date,
            "documentTypes": effective_types,
            "recordCount": 0,
            "records": [],
            "error": "captcha_blocked",
            "message": msg,
        }

    # ── Stage 3: Client-side filter ──────────────────────────────────────────
    records = _apply_filter(all_records)
    if not records:
        print("[WARN] No target documents found. Check document types and date range.")
        result = {"ok": True, "recordCount": 0, "csvFile": "", "csvPath": ""}
        if write_output_files:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = export_csv([], csv_name=csv_name or f"coconino_pipeline_{ts}.csv")
            result["csvFile"] = csv_path.name
            result["csvPath"] = str(csv_path)
        return result

    # ── Stage 4: Real-time display (initial pass) ────────────────────────────
    print(f"\n[DISPLAY] {len(records)} target documents found:")
    _print_header()
    for i, rec in enumerate(records, 1):
        _print_row(i, rec)
    print()

    # ── Stage 5: Detail enrichment → address + principal ────────────────────
    print(f"[DETAIL] Fetching document detail pages for {len(records)} records …")
    t0 = time.time()
    records = enrich_records_with_detail_fields(records, cookie=cookie, max_records=None)
    print(f"[DETAIL] Done in {time.time() - t0:.1f}s")

    # ── Stage 6+7: OCR + Groq for records still missing fields ──────────────
    needs_ocr = list(records)
    if ocr_limit == 0:
        ocr_count = len(needs_ocr)
    else:
        ocr_count = min(len(needs_ocr), max(0, ocr_limit))
    print(f"\n[OCR] {len(needs_ocr)} records still need OCR  (running on {ocr_count})")

    for idx, record in enumerate(needs_ocr[:ocr_count], 1):
        doc_id = str(record.get("documentId", ""))
        rec_num = str(record.get("recordingNumber", ""))
        doc_type = str(record.get("documentType", ""))
        print(f"[OCR {idx}/{ocr_count}] {doc_id}  {doc_type} …", end="", flush=True)
        try:
            analysis = fetch_document_ocr_and_analysis(
                document_id=doc_id,
                recording_number=rec_num,
                index=1,
                document_type=doc_type,
                cookie=cookie,
                use_groq=use_groq,
            )
            # Prefer Groq-extracted fields when available
            groq = analysis.get("groqAnalysis") or {}
            
            # Extract fields directly from Groq response (flat structure from system prompt)
            # The system prompt returns keys: trustor, trustee, beneficiary, principalAmount, propertyAddress
            
            if not record.get("trustor"):
                val = groq.get("trustor")
                if val and val != "NOT_FOUND":
                     record["trustor"] = val
            
            if not record.get("trustee"):
                val = groq.get("trustee")
                if val and val != "NOT_FOUND":
                     record["trustee"] = val
                     
            if not record.get("beneficiary"):
                val = groq.get("beneficiary")
                if val and val != "NOT_FOUND":
                     record["beneficiary"] = val

            if not record.get("propertyAddress"):
                # Fallback to nested 'property' if prompt structure changes, or direct key
                addr = groq.get("propertyAddress") or (groq.get("property") or {}).get("address") or (analysis.get("addressCandidates") or [None])[0]
                if addr and addr != "NOT_FOUND":
                    record["propertyAddress"] = str(addr).strip()

            if not record.get("principalAmount"):
                # Fallback to nested 'financials' if prompt structure changes, or direct key
                amt = (
                    groq.get("principalAmount")
                    or (groq.get("financials") or {}).get("amount")
                    or (groq.get("financials") or {}).get("loanAmount")
                    or (analysis.get("principalCandidates") or [None])[0]
                )
                if amt and amt != "NOT_FOUND":
                    record["principalAmount"] = str(amt).strip()
            
            # Map Grantors/Grantees from LLM if available and better
            llm_grantors = groq.get("grantors")
            if llm_grantors and isinstance(llm_grantors, list) and llm_grantors:
                record["grantors"] = llm_grantors
                
            llm_grantees = groq.get("grantees")
            if llm_grantees and isinstance(llm_grantees, list) and llm_grantees:
                record["grantees"] = llm_grantees

            record["documentUrl"] = analysis.get("documentUrl", "")
            record["ocrMethod"] = analysis.get("ocrMethod", "")
            record["ocrTextPreview"] = (analysis.get("ocrTextPreview") or "")[:500]
            record["ocrTextPath"] = analysis.get("ocrTextPath", "")
            record["usedGroq"] = analysis.get("usedGroq", False)
            record["groqError"] = analysis.get("groqError", "")
            size = analysis.get("pdfSize", 0)
            has_addr = bool(record.get("propertyAddress"))
            has_amt = bool(record.get("principalAmount"))
            print(f" ✓  PDF={size//1024}KB  addr={has_addr}  amt={has_amt}")
        except Exception as exc:
            record["documentAnalysisError"] = str(exc)
            print(f" ✗  {exc}")

    # ── Final enriched display ───────────────────────────────────────────────
    print(f"\n{'═' * 140}")
    print(f"  ENRICHED RESULTS  ({len(records)} documents  |  {start_date} → {end_date})")
    print(f"{'═' * 140}")
    _print_header()
    for i, rec in enumerate(records, 1):
        _print_row(i, rec)
        if rec.get("propertyAddress") or rec.get("principalAmount"):
            _print_enriched(rec)
    print("─" * 140)

    non_empty_addr = sum(1 for r in records if str(r.get("propertyAddress", "")).strip())
    non_empty_amt = sum(1 for r in records if str(r.get("principalAmount", "")).strip())
    print(f"  Records with address:   {non_empty_addr}/{len(records)}")
    print(f"  Records with principal: {non_empty_amt}/{len(records)}")
    print()

    csv_path = Path("")
    effective_csv = ""
    if write_output_files:
        # ── Stage 8: Save CSV ────────────────────────────────────────────────
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        effective_csv = csv_name or f"coconino_pipeline_{ts}.csv"
        csv_path = export_csv(records, csv_name=effective_csv)
        print(f"[CSV]  Saved → {csv_path}")

    # ── Stage 9: DB Upsert ──────────────────────────────────────────────────
    try:
        print("[DB] Connecting to Supabase...")
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            conn = db_supabase.connect_db(db_url)
            with conn:
                db_supabase.ensure_schema(conn)
                inserted, updated = db_supabase.upsert_records(conn, records)
                print(f"[DB] Upsert complete: {inserted} inserted, {updated} updated")
        else:
            print("[DB] DATABASE_URL not set, skipping DB upsert")
    except Exception as e:
        print(f"[DB] Error upserting records: {e}")

    result = {
        "ok": True,
        "startDate": start_date,
        "endDate": end_date,
        "documentTypes": effective_types,
        "totalServerResults": summary.get("totalResults"),
        "recordCount": len(records),
        "nonEmptyPropertyAddress": non_empty_addr,
        "nonEmptyPrincipalAmount": non_empty_amt,
        "ocrProcessed": ocr_count,
        "records": records,
        "csvFile": csv_path.name if csv_path else "",
        "csvPath": str(csv_path) if csv_path else "",
        "summary": summary,
    }

    if write_output_files and effective_csv:
        json_path = OUTPUT_DIR / effective_csv.replace(".csv", ".json")
        json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[JSON] Saved → {json_path}")
    return result


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _default_date_range() -> tuple[str, str]:
    today = datetime.now()
    # Last 3 days as per user requirement
    return (today - timedelta(days=3)).strftime("%m/%d/%Y"), today.strftime("%m/%d/%Y")


def main() -> None:
    default_start, default_end = _default_date_range()
    p = argparse.ArgumentParser(description="Coconino County real estate leads pipeline")
    p.add_argument("--start-date", default=default_start, metavar="MM/DD/YYYY")
    p.add_argument("--end-date",   default=default_end,   metavar="MM/DD/YYYY")
    p.add_argument("--pages",      type=int, default=None, metavar="N",
                   help="Max pages to fetch (default: all)")
    p.add_argument("--ocr-limit",  type=int, default=20,  metavar="N",
                   help="Max documents to OCR/Groq (default: 20)")
    p.add_argument("--headful",    action="store_true",  help="Show browser window")
    p.add_argument("--no-groq",    action="store_true",  help="Skip Groq LLM")
    p.add_argument("--csv-name",   default=None,         metavar="NAME")
    p.add_argument("--doc-types",  nargs="+", default=None, metavar="TYPE")
    args = p.parse_args()

    try:
        result = run_pipeline(
            start_date=args.start_date,
            end_date=args.end_date,
            page_limit=args.pages,
            ocr_limit=args.ocr_limit,
            headless=not args.headful,
            use_groq=not args.no_groq,
            csv_name=args.csv_name,
            doc_types=args.doc_types,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Pipeline stopped by user.")
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
