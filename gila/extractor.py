"""
Gila County, AZ — Real Estate Lead Scraper — Core Extraction Engine
====================================================================
Platform  : Tyler Technologies EagleWeb  (no Cloudflare — pure requests)
Base URL  : https://selfservice.gilacountyaz.gov
Search ID : DOCSEARCH2242S1

Pipeline
--------
  1. acquire_session()           → JSESSIONID via GET /web/search/DOCSEARCH2242S1
  2. post_search()               → POST /web/searchPost/DOCSEARCH2242S1
  3. fetch_results_page()        → GET /web/searchResults/DOCSEARCH2242S1?page=N
  4. fetch_all_pages()           → iterate pages 2..N, deduplicate by documentId
  5. fetch_detail_page()         → GET /web/document/{docId}?search=DOCSEARCH2242S1
  6. discover_pdf_url()          → pdfjs viewer link → iframe src → real download URL
  7. download_pdf()              → GET real PDF URL (may return 403 — handled gracefully)
  8. extract_text_pdftotext()    → pdftotext subprocess
  9. ocr_with_tesseract()        → pdftoppm + tesseract (fallback for image-only PDFs)
 10. analyze_with_groq()         → Groq Llama-3.3 → address + principal amount
 11. enrich_record_with_ocr()    → orchestrate 6-10 for one record
 12. export_csv() / export_json()→ write enriched records to disk

PDF Discovery flow (CRITICAL — county requires 2-hop URL discovery):
  detail page → find pdfjs link → GET pdfjs page → parse <iframe src> → download URL

Note on PDF downloads:
  The Gila County portal restricts direct PDF downloads (returns 403 / empty body).
  discover_pdf_url() still finds and records the URL. download_pdf() captures the
  failure gracefully so all document metadata is still exported to CSV.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

try:
    import requests
    from requests import Session
except ImportError:
    sys.exit("requests not installed — run: pip install requests")

try:
    from playwright.sync_api import sync_playwright, BrowserContext
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────────────────────────

BASE_URL          = "https://selfservice.gilacountyaz.gov"
SEARCH_ID         = "DOCSEARCH2242S1"
SEARCH_URL        = f"{BASE_URL}/web/search/{SEARCH_ID}"
SEARCH_POST_URL   = f"{BASE_URL}/web/searchPost/{SEARCH_ID}"
SEARCH_RESULTS_URL = f"{BASE_URL}/web/searchResults/{SEARCH_ID}"

_UUID_RE = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"

OUTPUT_DIR = Path(__file__).parent / "output"
DOCS_DIR   = OUTPUT_DIR / "documents"

# Target distressed-property document types for Gila County
# These are sent as field_selfservice_documentTypes-searchInput (text search)
DEFAULT_DOCUMENT_TYPES: list[str] = [
    "LIS PENDENS",
    "TRUSTEES DEED",
    "SHERIFFS DEED",
    "NOTICE OF TRUSTEES SALE",
    "TREASURERS DEED",
    "AMENDED STATE LIEN",
    "STATE LIEN",
    "STATE TAX LIEN",
    "RELEASE STATE TAX LIEN",
    "COMPLETION OF FORECLOSURE",
    "CERTIFICATE OF SALE",
]

# Confirmed document-type code→name pairs captured from Gila County browser payload.
# Sent as field_selfservice_documentTypes-holderInput / holderValue pairs (separate
# mechanism from searchInput — targets specific confirmed Tyler EagleWeb type codes).
GILA_DOC_TYPE_HOLDERS: list[tuple[str, str]] = [
    ("AGR",   "Agreement For Sale"),
    ("ANCL",  "Amend Notice And Claim Of Lien"),
    ("CNTS",  "Corrected Notice Of Sale"),
    ("DOR",   "Deed Of Release"),
    ("DOT",   "Deed Of Trust"),
    ("LP",    "Lis Pendens"),
    ("NTS",   "Notice Of Trustee Sale"),
    ("NOTSS", "Notice of Sheriff"),
    ("NR",    "Notice Of Rescission"),
    ("NOC",   "Notice Of Completion"),
    ("COS",   "Certificate Of Sale"),
    ("CP",    "Certificate Of Purchase"),
    ("SCOS",  "Sheriffs Certificate Of Sale"),
    ("NTRST", "Notice Of Trust"),
]

# Client-side alias normalisation
# Maps server-returned doc type variants → canonical names (handles apostrophes,
# holder-code value names, and other server-side spelling differences).
_SERVER_ALIASES: dict[str, str] = {
    # Apostrophe / possessive variants
    "TRUSTEE'S DEED":                 "TRUSTEES DEED",
    "TRUSTEE'S DEED UPON SALE":       "TRUSTEES DEED",
    "TRUSTEES DEED UPON SALE":        "TRUSTEES DEED",
    "NOTICE OF TRUSTEE'S SALE":       "NOTICE OF TRUSTEES SALE",
    "NOTICE OF TRUSTEE SALE":         "NOTICE OF TRUSTEES SALE",
    "SHERIFF'S DEED":                 "SHERIFFS DEED",
    "TREASURER'S DEED":               "TREASURERS DEED",
    # Holder-value names returned when portal matches by code
    "LIS PENDENS":                    "LIS PENDENS",
    "AGREEMENT FOR SALE":             "AGREEMENT FOR SALE",
    "AMEND NOTICE AND CLAIM OF LIEN": "AMEND NOTICE AND CLAIM OF LIEN",
    "CORRECTED NOTICE OF SALE":       "CORRECTED NOTICE OF SALE",
    "DEED OF RELEASE":                "DEED OF RELEASE",
    "DEED OF TRUST":                  "DEED OF TRUST",
    "NOTICE OF SHERIFF":              "NOTICE OF SHERIFF",
    "NOTICE OF RESCISSION":           "NOTICE OF RESCISSION",
    "NOTICE OF COMPLETION":           "NOTICE OF COMPLETION",
    "CERTIFICATE OF SALE":            "CERTIFICATE OF SALE",
    "CERTIFICATE OF PURCHASE":        "CERTIFICATE OF PURCHASE",
    "SHERIFFS CERTIFICATE OF SALE":   "SHERIFFS CERTIFICATE OF SALE",
    "SHERIFF'S CERTIFICATE OF SALE":  "SHERIFFS CERTIFICATE OF SALE",
    "NOTICE OF TRUST":                "NOTICE OF TRUST",
    "COMPLETION OF FORECLOSURE":      "COMPLETION OF FORECLOSURE",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

GROQ_API_URL         = "https://api.groq.com/openai/v1/chat/completions"


def _normalize_groq_model(model: str) -> str:
    m = (model or "").strip()
    if m == "llama-3.3-70b":
        return "llama-3.3-70b-versatile"
    return m or "llama-3.3-70b-versatile"


GROQ_MODEL = _normalize_groq_model(os.environ.get("GROQ_MODEL", "llama-3.3-70b"))

# ── Data Model ────────────────────────────────────────────────────────────────

CSV_FIELDNAMES = [
    "documentId", "recordingNumber", "documentType", "recordingDate",
    "grantors", "grantees", "legalDescriptions",
    "propertyAddress", "principalAmount",
    "detailUrl", "documentUrl",
    "ocrMethod", "ocrTextPreview", "ocrTextPath",
    "usedGroq", "groqError", "documentAnalysisError",
]


def _empty_record(doc_id: str = "") -> dict:
    return {f: "" for f in CSV_FIELDNAMES} | {
        "documentId": doc_id,
        "usedGroq": False,
        "detailUrl": f"{BASE_URL}/web/document/{doc_id}?search={SEARCH_ID}" if doc_id else "",
    }


# ── Session Acquisition ───────────────────────────────────────────────────────

# Path for Playwright persistent browser state (survives restarts)
SESSION_STATE_PATH = OUTPUT_DIR / "session_state.json"


def _make_requests_session(cookie_str: str) -> Session:
    """
    Build a requests.Session pre-loaded with the cookies exported from Playwright.
    Used for all subsequent pagination / detail / PDF requests.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent":      USER_AGENT,
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.9",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
    })
    # Parse and inject cookies from the "name=val; name2=val2" string
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" in pair:
            name, _, val = pair.partition("=")
            session.cookies.set(name.strip(), val.strip(),
                                domain="selfservice.gilacountyaz.gov")
    return session


def playwright_search(
    start_date: str,
    end_date:   str,
    doc_types:  list[str],
    headless:   bool = True,
    verbose:    bool = True,
) -> tuple[str, list[dict], dict]:
    """
    Use Playwright (Chromium) to:
      1. Open the Gila County search page (JS-rendered — requests cannot see the form)
      2. Accept the disclaimer if shown
      3. Fill the date fields
      4. Inject one hidden input per document type
      5. Submit the search and wait for results to load
      6. Parse the first page of results
      7. Export the JSESSIONID cookie for reuse in requests pagination

    Returns
    -------
    cookie_str   : "JSESSIONID=xxx; disclaimerAccepted=true" string
    page1_records: list of parsed record dicts from page 1
    summary      : { pageCount, totalCount, filterDescription }
    """
    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "playwright not installed — run: "
            "pip install playwright && playwright install chromium"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        # Re-use existing browser state (JSESSIONID already valid from a prior run)
        storage_state = str(SESSION_STATE_PATH) if SESSION_STATE_PATH.exists() else None

        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            storage_state=storage_state,
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        if verbose:
            print(f"[PLAYWRIGHT] Navigating to search page …")
            print(f"  {'Reusing' if storage_state else 'Fresh'} session state")

        # ── Navigate ──────────────────────────────────────────────────────────
        page.goto(SEARCH_URL, timeout=120_000, wait_until="domcontentloaded")

        # ── Accept disclaimer ─────────────────────────────────────────────────
        for sel in [
            "button:has-text('I Accept')",
            "a:has-text('I Accept')",
            "input[value='I Accept']",
            "#btnAccept",
            ".disclaimer-accept",
        ]:
            try:
                if page.locator(sel).count() > 0:
                    page.locator(sel).first.click()
                    page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    if verbose:
                        print(f"  Disclaimer accepted via {sel}")
                    break
            except Exception:
                pass

        # ── Wait for search form ──────────────────────────────────────────────
        try:
            page.wait_for_selector(
                "[name='field_RecDateID_DOT_StartDate'], "
                "[id='field_RecDateID_DOT_StartDate'], "
                "input[id*='StartDate'], input[id*='RecDate']",
                timeout=15_000,
            )
        except Exception:
            # Fallback: just wait a bit for the JS to settle
            page.wait_for_timeout(4_000)

        # ── Fill date range ───────────────────────────────────────────────────
        start_fmt = _normalise_date(start_date)
        end_fmt   = _normalise_date(end_date)

        date_filled = False
        for start_sel, end_sel in [
            ("#field_RecDateID_DOT_StartDate", "#field_RecDateID_DOT_EndDate"),
            ("[name='field_RecDateID_DOT_StartDate']", "[name='field_RecDateID_DOT_EndDate']"),
            ("input[id*='StartDate']", "input[id*='EndDate']"),
        ]:
            try:
                if page.locator(start_sel).count() > 0:
                    page.fill(start_sel, start_fmt)
                    page.fill(end_sel, end_fmt)
                    date_filled = True
                    if verbose:
                        print(f"  Dates filled: {start_fmt} → {end_fmt}")
                    break
            except Exception:
                pass

        if not date_filled and verbose:
            print("  ⚠ Could not fill date fields — form selectors not found")

        # ── Inject document type hidden inputs ────────────────────────────────
        holders = [[code, name] for code, name in GILA_DOC_TYPE_HOLDERS]
        injected = page.evaluate("""
            ([docTypes, holders]) => {
                const form = document.querySelector('form');
                if (!form) return 0;

                // Remove any previously injected inputs
                form.querySelectorAll('[data-gila-doctype]').forEach(el => el.remove());

                // searchInput fields (one per text search term)
                docTypes.forEach(dt => {
                    const inp = document.createElement('input');
                    inp.type  = 'hidden';
                    inp.name  = 'field_selfservice_documentTypes-searchInput';
                    inp.value = dt;
                    inp.setAttribute('data-gila-doctype', '1');
                    form.appendChild(inp);
                });

                // holderInput / holderValue pairs (confirmed code→name entries)
                holders.forEach(([code, name]) => {
                    const hi = document.createElement('input');
                    hi.type  = 'hidden';
                    hi.name  = 'field_selfservice_documentTypes-holderInput';
                    hi.value = code;
                    hi.setAttribute('data-gila-doctype', '1');
                    form.appendChild(hi);

                    const hv = document.createElement('input');
                    hv.type  = 'hidden';
                    hv.name  = 'field_selfservice_documentTypes-holderValue';
                    hv.value = name;
                    hv.setAttribute('data-gila-doctype', '1');
                    form.appendChild(hv);
                });

                // Ensure operator field present
                let op = form.querySelector(
                    '[name="field_selfservice_documentTypes-containsInput"]'
                );
                if (!op) {
                    op = document.createElement('input');
                    op.type  = 'hidden';
                    op.name  = 'field_selfservice_documentTypes-containsInput';
                    op.setAttribute('data-gila-doctype', '1');
                    form.appendChild(op);
                }
                op.value = 'Contains Any';

                return docTypes.length + holders.length;
            }
        """, [doc_types, holders])
        if verbose:
            print(f"  Injected {injected} document-type inputs "
                  f"({len(doc_types)} search + {len(holders)} holder pairs)")

        # ── Maximize results per page ────────────────────────────────────────
        # Tyler EagleWeb portals often have a results-per-page select; try to
        # set it to the highest available value before submitting.
        try:
            rpp_sel = (
                "select[id*='resultsPerPage'], select[name*='resultsPerPage'], "
                "select[id*='pageSize'], select[name*='pageSize'], "
                "select[id*='RowsPerPage'], select[name*='RowsPerPage']"
            )
            if page.locator(rpp_sel).count() > 0:
                # Select the last (usually largest) option
                page.evaluate("""
                    const sel = document.querySelector(
                        'select[id*=resultsPerPage],select[name*=resultsPerPage],'
                        +'select[id*=pageSize],select[name*=pageSize],'
                        +'select[id*=RowsPerPage],select[name*=RowsPerPage]'
                    );
                    if (sel && sel.options.length > 0) {
                        sel.value = sel.options[sel.options.length - 1].value;
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                """)
                page.wait_for_timeout(1000)
                if verbose:
                    print("  Results-per-page set to maximum")
        except Exception:
            pass

        # ── Submit ───────────────────────────────────────────────────────────
        submitted = False
        for btn_sel in ["#searchButton", "button[type='submit']",
                        "input[type='submit']", "a:has-text('Search')"]:
            try:
                if page.locator(btn_sel).count() > 0:
                    page.locator(btn_sel).first.click()
                    submitted = True
                    if verbose:
                        print(f"  Search submitted via {btn_sel}")
                    break
            except Exception:
                pass

        if not submitted:
            if verbose:
                print("  Submitting via form.submit() fallback")
            page.evaluate("document.querySelector('form').submit()")

        # ── Wait for results ──────────────────────────────────────────────────
        try:
            page.wait_for_selector(
                ".ss-search-row, li.ss-search-row, [class*='ss-search-row']",
                timeout=25_000,
            )
        except Exception:
            page.wait_for_timeout(5_000)

        result_html  = page.content()
        page1_records, summary = parse_search_results_html(result_html)

        if verbose:
            print(f"  Page 1: {len(page1_records)} records  "
                  f"(server total: {summary.get('totalCount', '?')})")

        # ── Save browser state ────────────────────────────────────────────────
        context.storage_state(path=str(SESSION_STATE_PATH))

        # ── Build cookie string for requests ──────────────────────────────────
        cookies     = context.cookies()
        cookie_str  = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies
            if c["domain"] in ("selfservice.gilacountyaz.gov",
                               ".selfservice.gilacountyaz.gov")
        )
        # Always include disclaimer
        if "disclaimerAccepted" not in cookie_str:
            cookie_str += "; disclaimerAccepted=true"

        if verbose:
            jsid = next(
                (c["value"] for c in cookies if c["name"] == "JSESSIONID"), ""
            )
            masked = f"{jsid[:8]}…{jsid[-4:]}" if len(jsid) > 12 else jsid
            print(f"  Cookie string ready  ({len(cookie_str)} chars)")
            print(f"  JSESSIONID={masked}")

        browser.close()

    return cookie_str, page1_records, summary


# ── Search POST ───────────────────────────────────────────────────────────────

def _normalise_date(date_str: str) -> str:
    """Normalise to MM/DD/YYYY — the format Gila County's form expects."""
    s = date_str.strip()
    for fmt in ("%m/%d/%Y", "%-m/%-d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%m/%d/%Y")
        except ValueError:
            pass
    return s  # Return as-is and hope the server accepts it


def build_search_payload(
    start_date: str,
    end_date: str,
    doc_types: list[str],
) -> bytes:
    """
    Build the URL-encoded POST body for Gila County EagleWeb's search form.

    Field names are Gila-specific (different from Coconino):
      - Dates   : field_RecDateID_DOT_StartDate / EndDate  (MM/DD/YYYY format)
      - Names   : field_BothNamesID, field_GrantorID, field_GranteeID
      - Legal   : field_PlattedLegalID_DOT_*, field_PLSSLegalID_DOT_*
      - DocTypes: field_selfservice_documentTypes-searchInput (one per type)

    The server returns HTTP 500 on searchResults if any scaffold field is
    missing or if the date format is wrong.
    """
    start_fmt = _normalise_date(start_date)   # MM/DD/YYYY
    end_fmt   = _normalise_date(end_date)

    # Exact scaffold from Gila County browser capture (order matters)
    params: list[tuple[str, str]] = [
        ("field_BothNamesID-containsInput",                  "Contains Any"),
        ("field_BothNamesID",                                ""),
        ("field_GrantorID-containsInput",                    "Contains Any"),
        ("field_GrantorID",                                  ""),
        ("field_GranteeID-containsInput",                    "Contains Any"),
        ("field_GranteeID",                                  ""),
        ("field_RecDateID_DOT_StartDate",                    start_fmt),
        ("field_RecDateID_DOT_EndDate",                      end_fmt),
        ("field_DocNumID",                                   ""),
        ("field_BookPageID_DOT_Book",                        ""),
        ("field_BookPageID_DOT_Page",                        ""),
        ("field_PlattedLegalID_DOT_Subdivision-containsInput", "Contains Any"),
        ("field_PlattedLegalID_DOT_Subdivision",             ""),
        ("field_PlattedLegalID_DOT_Lot",                     ""),
        ("field_PlattedLegalID_DOT_Block",                   ""),
        ("field_PlattedLegalID_DOT_Tract",                   ""),
        ("field_PLSSLegalID_DOT_QuarterSection-containsInput", "Contains Any"),
        ("field_PLSSLegalID_DOT_QuarterSection",             ""),
        ("field_PLSSLegalID_DOT_Section",                    ""),
        ("field_PLSSLegalID_DOT_Township",                   ""),
        ("field_PLSSLegalID_DOT_Range",                      ""),
        ("field_ParcelID",                                   ""),
    ]

    # One entry per document type (the JS autocomplete widget POSTs like this)
    for dt in doc_types:
        params.append(("field_selfservice_documentTypes-searchInput", dt))

    # Holder pairs — confirmed document-type codes with their canonical names.
    # Sent exactly as captured from browser network trace (holderInput then holderValue).
    for code, name in GILA_DOC_TYPE_HOLDERS:
        params.append(("field_selfservice_documentTypes-holderInput", code))
        params.append(("field_selfservice_documentTypes-holderValue", name))

    params.extend([
        ("field_selfservice_documentTypes-containsInput",    "Contains Any"),
        ("field_selfservice_documentTypes",                  ""),
        ("field_UseAdvancedSearch",                          ""),
    ])

    return urllib.parse.urlencode(params).encode("utf-8")


def post_search(
    session: Session,
    start_date: str,
    end_date: str,
    doc_types: list[str],
    verbose: bool = True,
) -> None:
    """
    Submit the document search.  Search parameters are stored server-side
    in the Java session bound to JSESSIONID — pagination must reuse the
    same session object.
    """
    payload = build_search_payload(start_date, end_date, doc_types)

    if verbose:
        print(f"[SEARCH] POST  {start_date} → {end_date}  "
              f"types={len(doc_types)}  payload={len(payload)}B")

    resp = session.post(
        SEARCH_POST_URL,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer":      SEARCH_URL,
            "Origin":       BASE_URL,
        },
        timeout=45,
        allow_redirects=True,
    )

    if resp.status_code == 500:
        raise RuntimeError(
            f"Search POST returned HTTP 500 — JSESSIONID may be stale.\n"
            f"Response: {resp.text[:400]}"
        )
    if verbose:
        print(f"[SEARCH] POST → HTTP {resp.status_code}")


# ── Results Parsing ───────────────────────────────────────────────────────────

def _ts() -> int:
    """Millisecond timestamp for cache-busting `_=` parameter."""
    return int(time.time() * 1000)


def _strip_tags(html: str) -> str:
    """Remove HTML tags and normalise whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _extract_names_from_row(row_html: str, label: str) -> str:
    """
    Extract one or more names following a label (Grantor / Grantee) in row HTML.
    Returns pipe-separated string.
    """
    # Pattern 1: label followed by its values in sibling elements
    pattern = (
        rf'{label}\s*</[^>]+>\s*'
        rf'((?:<[^>]+>[^<]*</[^>]+>\s*)*)'
    )
    m = re.search(pattern, row_html, re.IGNORECASE | re.DOTALL)
    if m:
        raw = _strip_tags(m.group(1))
        names = [n.strip() for n in raw.split("\n") if n.strip()]
        if names:
            return " | ".join(names)

    # Pattern 2: label in a class attribute
    cls_map = {
        "grantor": ["grantor", "seller", "trustor"],
        "grantee": ["grantee", "buyer", "trustee", "beneficiary"],
    }
    key = label.lower()
    for cls_hint in cls_map.get(key, [key]):
        m = re.search(
            rf'class="[^"]*{cls_hint}[^"]*"[^>]*>([^<]+)<',
            row_html, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()

    # Pattern 3: plain text after label
    m = re.search(
        rf'{label}[:\s]+([A-Z][A-Z0-9\s,\.&]+?)(?=(?:Grantor|Grantee|<|\Z))',
        row_html, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()

    return ""


def parse_search_results_html(html: str) -> tuple[list[dict], dict]:
    """
    Parse one EagleWeb search results HTML fragment.

    Returns
    -------
    records  : list of dicts, one per document row
    summary  : { pageCount, totalCount, filterDescription }
    """
    records: list[dict] = []

    # ── Total / page count ────────────────────────────────────────────────────
    # Patterns: "60 results", "Showing 1-20 of 26", "1 to 20 of 26 records"
    total = 0
    for pattern in [
        r"of\s+(\d[\d,]*)\s*(?:result|record|document)",  # "of 26 results"
        r"(\d[\d,]*)\s+(?:result|record|document)s?\s+found",  # "26 records found"
        r"(\d[\d,]*)\s+result",                                 # "26 results"
    ]:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            total = int(m.group(1).replace(",", ""))
            break

    pages_m   = re.search(r"[Pp]age\s+\d+\s+of\s+(\d+)", html)
    page_count = int(pages_m.group(1)) if pages_m else 1
    if not pages_m:
        pc_m = re.search(r'data-page-count=["\'](\d+)["\']', html)
        if pc_m:
            page_count = int(pc_m.group(1))

    # ── Filter description ────────────────────────────────────────────────────
    fd_m = re.search(
        r'class="[^"]*ss-search-criteria[^"]*"[^>]*>(.*?)</(?:div|p|span)',
        html, re.DOTALL | re.IGNORECASE,
    )
    filter_desc = _strip_tags(fd_m.group(1)) if fd_m else ""

    # ── Result rows ───────────────────────────────────────────────────────────
    row_re = re.compile(
        r'<li[^>]+class="[^"]*ss-search-row[^"]*"[^>]*>(.*?)</li>',
        re.DOTALL | re.IGNORECASE,
    )
    for rm in row_re.finditer(html):
        rec = _parse_row(rm.group(1))
        if rec.get("documentId"):
            records.append(rec)

    # Fallback: sometimes rows are <div> not <li>
    if not records:
        div_re = re.compile(
            r'<(?:div|tr)[^>]+class="[^"]*ss-search-row[^"]*"[^>]*>(.*?)</(?:div|tr)>',
            re.DOTALL | re.IGNORECASE,
        )
        for rm in div_re.finditer(html):
            rec = _parse_row(rm.group(1))
            if rec.get("documentId"):
                records.append(rec)

    summary = {
        "pageCount":         page_count,
        "totalCount":        total or len(records),
        "filterDescription": filter_desc,
    }
    return records, summary


def _parse_row(row_html: str) -> dict:
    """Extract all available fields from one search result row."""
    rec = _empty_record()

    # ── Document ID ───────────────────────────────────────────────────────────
    doc_m = re.search(r'/web/document/(DOC\w+)[?"\s]', row_html, re.IGNORECASE)
    if not doc_m:
        doc_m = re.search(r'data-(?:document-)?id=["\']([^"\']+)["\']', row_html)
    if not doc_m:
        # Sometimes embedded as text "DOC2352S262"
        doc_m = re.search(r'\b(DOC\d+S\d+)\b', row_html, re.IGNORECASE)
    if not doc_m:
        return rec
    rec["documentId"] = doc_m.group(1)
    rec["detailUrl"]  = (
        f"{BASE_URL}/web/document/{rec['documentId']}?search={SEARCH_ID}"
    )

    # ── Recording / Fee number ────────────────────────────────────────────────
    for pat in [
        r'(?:fee|recording|instrument)\s*(?:number|#|no\.?)\s*[:\s]*([0-9\-]+)',
        r'<[^>]+class="[^"]*(?:fee|recording)[^"]*"[^>]*>([^<]+)<',
        r'\b(\d{4}-\d{5,})\b',    # e.g. 2026-002408
        r'\b(\d{7,})\b',           # plain 7+ digit
    ]:
        m = re.search(pat, row_html, re.IGNORECASE)
        if m:
            rec["recordingNumber"] = m.group(1).strip()
            break

    # ── Document type ─────────────────────────────────────────────────────────
    for pat in [
        r'<[^>]+class="[^"]*(?:document-type|doc-type|doctype)[^"]*"[^>]*>([^<]+)<',
        r'<[^>]+class="[^"]*type[^"]*"[^>]*>([^<]+)<',
    ]:
        m = re.search(pat, row_html, re.IGNORECASE)
        if m:
            raw = m.group(1).strip().upper()
            rec["documentType"] = _SERVER_ALIASES.get(raw, raw)
            break
    if not rec["documentType"]:
        for dt in DEFAULT_DOCUMENT_TYPES:
            if dt.lower() in row_html.lower():
                rec["documentType"] = dt
                break

    # ── Recording date ────────────────────────────────────────────────────────
    date_m = re.search(
        r'(\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM))?)',
        row_html, re.IGNORECASE,
    )
    if date_m:
        rec["recordingDate"] = date_m.group(1).strip()

    # ── Grantor / Grantee ─────────────────────────────────────────────────────
    rec["grantors"] = _extract_names_from_row(row_html, "Grantor")
    rec["grantees"] = _extract_names_from_row(row_html, "Grantee")

    return rec


# ── Pagination ────────────────────────────────────────────────────────────────

def fetch_results_page(
    session: Session,
    page: int = 1,
    verbose: bool = False,
) -> tuple[list[dict], dict]:
    """Fetch one results page (AJAX endpoint)."""
    url = f"{SEARCH_RESULTS_URL}?page={page}&_={_ts()}"
    resp = session.get(
        url,
        headers={
            "Accept":            "*/*",
            "Referer":           SEARCH_URL,
            "X-Requested-With":  "XMLHttpRequest",
            "ajaxRequest":       "true",
        },
        timeout=45,
    )
    resp.raise_for_status()
    records, summary = parse_search_results_html(resp.text)
    if verbose:
        print(f"[PAGE {page:>3}]  {len(records):>3} records  "
              f"(total claimed: {summary['totalCount']})")
    return records, summary


def fetch_all_pages(
    session: Session,
    page1_records: list[dict],
    summary: dict,
    page_limit: int = 0,
    verbose: bool = True,
    on_record: Any = None,  # optional callback(record) for real-time streaming
) -> list[dict]:
    """
    Paginate all result pages after page 1.

    Args
    ----
    session        : Authenticated requests Session
    page1_records  : Records already fetched from page 1
    summary        : Summary dict from page 1 (pageCount, totalCount)
    page_limit     : Max pages to fetch (0 = all)
    verbose        : Print progress
    on_record      : Optional callback invoked for every new record as it arrives
    """
    all_records = list(page1_records)
    seen_ids    = {r["documentId"] for r in all_records}

    # Fire callback for page-1 records
    if on_record:
        for r in page1_records:
            on_record(r)

    # ── Greedy pagination ────────────────────────────────────────────────────
    # Do NOT trust pageCount from the server (it often reports 1 even when
    # there are more pages).  Instead keep fetching until we see 2 consecutive
    # empty pages — that is the true end of results.
    page              = 2
    consecutive_empty = 0
    MAX_EMPTY         = 2   # stop after 2 back-to-back empty pages

    if verbose:
        server_total = summary.get("totalCount", "?")
        print(f"[PAGINATE] Greedy mode — will fetch until empty  "
              f"(server reported {server_total} total)")

    while True:
        if page_limit and page_limit > 0 and page > page_limit:
            if verbose:
                print(f"[PAGINATE] Reached page_limit={page_limit}, stopping.")
            break

        time.sleep(0.4)  # polite delay between requests
        try:
            records, _ = fetch_results_page(session, page, verbose=False)
        except Exception as exc:
            if verbose:
                print(f"[PAGINATE] Page {page} fetch error: {exc} — stopping.")
            break

        new_count = 0
        for r in records:
            if r.get("documentId") and r["documentId"] not in seen_ids:
                all_records.append(r)
                seen_ids.add(r["documentId"])
                new_count += 1
                if on_record:
                    on_record(r)

        if verbose:
            print(f"[PAGINATE] Page {page}  +{new_count} new  "
                  f"total={len(all_records)}")

        if new_count == 0:
            consecutive_empty += 1
            if consecutive_empty >= MAX_EMPTY:
                if verbose:
                    print(f"[PAGINATE] {MAX_EMPTY} consecutive empty pages — done.")
                break
        else:
            consecutive_empty = 0

        page += 1

    return all_records


# ── Detail Page ───────────────────────────────────────────────────────────────

def fetch_detail_page(
    doc_id: str,
    session: Session,
    timeout: int = 30,
) -> dict:
    """
    GET /web/document/{doc_id}?search=DOCSEARCH2242S1 and parse all metadata.

    Returns dict with keys: recordingNumber, recordingDate, documentType,
    grantors, grantees, propertyAddress, principalAmount, legalDescriptions,
    pdfjs_href (raw href for the pdfjs viewer link, if found).
    """
    url  = f"{BASE_URL}/web/document/{doc_id}?search={SEARCH_ID}"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return _parse_detail_html(resp.text, doc_id)


def _parse_detail_html(html: str, doc_id: str) -> dict:
    """Parse the document detail page HTML into a structured dict."""
    result: dict[str, Any] = {
        "recordingNumber":  "",
        "recordingDate":    "",
        "documentType":     "",
        "grantors":         [],
        "grantees":         [],
        "legalDescriptions": [],
        "propertyAddress":  "",
        "principalAmount":  "",
        "pdfjs_href":       "",
    }

    def _label_val(label_pattern: str, html_chunk: str) -> str:
        """
        Find value after a bold label in Tyler EagleWeb detail pages.
        Actual structure: <strong>Label:</strong></div><div>VALUE</div>
        Allow any number of tags between </strong> and the text value.
        """
        m = re.search(
            rf'<strong[^>]*>\s*{label_pattern}\s*:?\s*</strong>'
            rf'(?:\s*<[^>]+>)*\s*([^<\n][^<]*)',
            html_chunk, re.IGNORECASE | re.DOTALL,
        )
        return m.group(1).strip() if m else ""

    def _label_list(label_pattern: str, html_chunk: str) -> list[str]:
        """
        Find list values after a bold label in Tyler EagleWeb detail pages.
        Actual structure:
          <strong>Label:</strong></div>
          <div [attrs]>
            <ul class="ui-unbulleted-list"><li>NAME1</li><li>NAME2</li></ul>
            OR: plain text
          </div>
        """
        # Capture sibling <div> content immediately after </strong></div>
        m = re.search(
            rf'<strong[^>]*>\s*{label_pattern}\s*:?\s*</strong>'
            rf'\s*(?:</div>)?\s*<div[^>]*>(.*?)</div>\s*</td>',
            html_chunk, re.IGNORECASE | re.DOTALL,
        )
        if not m:
            return []
        section = m.group(1)
        # Try <li> items first (multiple grantors, etc.)
        items = re.findall(r'<li[^>]*>\s*([^<]+)', section, re.IGNORECASE)
        if items:
            return [i.strip() for i in items if i.strip()]
        # Try <dd> items
        items = re.findall(r'<dd[^>]*>\s*([^<]+)', section, re.IGNORECASE)
        if items:
            return [i.strip() for i in items if i.strip()]
        # Fallback: strip tags, use plain text
        text = re.sub(r'<[^>]+>', '', section).strip()
        return [text] if text else []

    # ── Fee / Recording number ────────────────────────────────────────────────
    for pat in ["Fee(?:\s+Number)?", "Recording\s+Number", "Instrument\s+Number"]:
        v = _label_val(pat, html)
        if v:
            result["recordingNumber"] = v
            break
    if not result["recordingNumber"]:
        m = re.search(r'\b(\d{4}-\d{5,})\b', html)
        if m:
            result["recordingNumber"] = m.group(1)

    # ── Recording date ────────────────────────────────────────────────────────
    for pat in ["Recording\s+Date", "Filed\s+Date", "Date\s+Recorded"]:
        v = _label_val(pat, html)
        if v:
            result["recordingDate"] = v
            break

    # ── Document type ─────────────────────────────────────────────────────────
    for pat in ["Document\s+Type", "Instrument\s+Type"]:
        v = _label_val(pat, html)
        if v:
            raw = v.upper()
            result["documentType"] = _SERVER_ALIASES.get(raw, raw)
            break
    if not result["documentType"]:
        # Fallback: Tyler EagleWeb also shows doc type as a section-header <li>
        # pattern: "Document Type</li><li class="ui-li-static ...">Trustees Deed</li>"
        _dt_m = re.search(
            r'Document\s+Type\s*</li>\s*<li[^>]*>\s*([^<]+)',
            html, re.IGNORECASE | re.DOTALL,
        )
        if _dt_m:
            raw = _dt_m.group(1).strip().upper()
            result["documentType"] = _SERVER_ALIASES.get(raw, raw)

    # ── Grantors ──────────────────────────────────────────────────────────────
    result["grantors"] = _label_list("Grantor", html) or (
        [_label_val("Grantor", html)] if _label_val("Grantor", html) else []
    )

    # ── Grantees ──────────────────────────────────────────────────────────────
    result["grantees"] = _label_list("Grantee", html) or (
        [_label_val("Grantee", html)] if _label_val("Grantee", html) else []
    )

    # ── Legal descriptions ────────────────────────────────────────────────────
    # Tyler EagleWeb uses a "Legal" section-header <li>, not a <strong> label.
    # Structure: >Legal</li><li ...><table><tr><td><div></div><div><ul><li>...</li></ul></div>
    _legal_m = re.search(
        r'>Legal\s*</li>.*?<ul[^>]*>(.*?)</ul>',
        html, re.IGNORECASE | re.DOTALL,
    )
    if _legal_m:
        _li_items = re.findall(r'<li[^>]*>\s*([^<]+)', _legal_m.group(1), re.IGNORECASE)
        result["legalDescriptions"] = [x.strip() for x in _li_items if x.strip()]
    else:
        # fallback: try <strong>Legal Description</strong> pattern
        result["legalDescriptions"] = _label_list("Legal\s+Description", html) or (
            [_label_val("Legal\s+Description", html)] if _label_val("Legal\s+Description", html) else []
        )

    # ── Property address ──────────────────────────────────────────────────────
    for pat in ["Property\s+Address", "Site\s+Address", "Property\s+Location"]:
        v = _label_val(pat, html)
        if v:
            result["propertyAddress"] = v
            break

    # ── Principal / Loan amount ───────────────────────────────────────────────
    for pat in ["Principal\s+Amount", "Loan\s+Amount", "Amount", "Consideration"]:
        v = _label_val(pat, html)
        if v and "$" in v or re.search(r'\d{3,}', v or ""):
            result["principalAmount"] = v
            break

    # ── pdfjs link (for PDF URL discovery) ───────────────────────────────────
    pdfjs_m = re.search(
        rf'/web/document-image-pdfjs/[^"\s]+\.pdf(?:\?[^"\s]*)?',
        html, re.IGNORECASE,
    )
    if pdfjs_m:
        result["pdfjs_href"] = pdfjs_m.group(0)

    return result


# ── PDF URL Discovery ─────────────────────────────────────────────────────────

def discover_pdf_url(
    doc_id: str,
    session: Session,
    detail_data: dict | None = None,
    verbose: bool = False,
) -> str | None:
    """
    Discover the real PDF download URL for a document.

    Two-hop strategy:
      1. If pdfjs_href is available in detail_data, try direct URL transformation.
         pdfjs: /web/document-image-pdfjs/{docId}/{UUID}/{file}.pdf
         →  dl: /web/document-image-pdf/{docId}/{UUID}/{file}-1.pdf?index=1
      2. Fetch the pdfjs viewer page and parse the <iframe src>.

    Returns the full download URL or None if discovery fails.
    """
    pdfjs_href = (detail_data or {}).get("pdfjs_href", "")

    if not pdfjs_href:
        # Re-fetch detail page to find the pdfjs link
        try:
            detail_data = fetch_detail_page(doc_id, session)
            pdfjs_href  = detail_data.get("pdfjs_href", "")
        except Exception as e:
            if verbose:
                print(f"[PDF] detail fetch failed: {e}")
            return None

    if not pdfjs_href:
        if verbose:
            print(f"[PDF] No pdfjs link found in detail page for {doc_id}")
        return None

    # ── Strategy 1: direct URL transformation ─────────────────────────────────
    # /web/document-image-pdfjs/{docId}/{UUID}/{file}.pdf  →
    # /web/document-image-pdf/{docId}/{UUID}/{file}-1.pdf?index=1
    pdfjs_m = re.search(
        rf'(/web/document-image-pdfjs/[^/]+/({_UUID_RE})/([^?.]+))\.pdf',
        pdfjs_href, re.IGNORECASE,
    )
    if pdfjs_m:
        prefix   = pdfjs_m.group(1)          # /web/document-image-pdfjs/…/UUID/file
        filename = pdfjs_m.group(3)           # file (without .pdf)
        dl_path  = prefix.replace(
            "document-image-pdfjs", "document-image-pdf"
        ) + f"-1.pdf?index=1"
        url = BASE_URL + dl_path
        if verbose:
            print(f"[PDF] Strategy-1 URL: {url}")
        return url

    # ── Strategy 2: fetch pdfjs viewer page → parse <iframe src> ─────────────
    pdfjs_url = (BASE_URL + pdfjs_href) if pdfjs_href.startswith("/") else pdfjs_href
    try:
        resp = session.get(pdfjs_url, timeout=20)
        iframe_m = re.search(
            r'<iframe[^>]+src=["\'](/web/document-image-pdf/[^"\']+\.pdf[^"\']*)["\']',
            resp.text, re.IGNORECASE,
        )
        if iframe_m:
            url = BASE_URL + iframe_m.group(1)
            if verbose:
                print(f"[PDF] Strategy-2 (iframe) URL: {url}")
            return url
    except Exception as e:
        if verbose:
            print(f"[PDF] pdfjs fetch failed: {e}")

    return None


# ── PDF Download ──────────────────────────────────────────────────────────────

def download_pdf(
    url: str,
    session: Session,
    dest_path: Path,
    timeout: int = 60,
) -> tuple[bool, str]:
    """
    Attempt to download a PDF from the county portal.

    Returns (success: bool, error_message: str).

    Note: Gila County restricts direct PDF downloads (portal returns 403 or
    an HTML error page rather than PDF bytes). This function handles that
    gracefully so all other metadata is still exported to CSV.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = session.get(
            url,
            headers={"Referer": SEARCH_URL, "Accept": "application/pdf,*/*"},
            timeout=timeout,
            stream=True,
        )
        if resp.status_code in (401, 403):
            return False, f"HTTP {resp.status_code} — download not permitted by portal"
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"

        # Check that the response actually is a PDF
        content_type = resp.headers.get("Content-Type", "")
        if "pdf" not in content_type.lower() and "octet" not in content_type.lower():
            # Portal may return an HTML error page with 200 OK
            first_bytes = b""
            for chunk in resp.iter_content(256):
                first_bytes = chunk
                break
            if first_bytes and not first_bytes.startswith(b"%PDF"):
                return False, f"Response is not a PDF (Content-Type: {content_type})"

        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(8192):
                fh.write(chunk)

        size = dest_path.stat().st_size
        if size < 100:
            return False, f"Downloaded file too small ({size} bytes) — likely HTML error page"

        return True, ""

    except requests.exceptions.ConnectionError as e:
        return False, f"Connection error: {e}"
    except requests.exceptions.Timeout:
        return False, "Download timed out"
    except Exception as e:
        return False, str(e)


# ── OCR Pipeline ──────────────────────────────────────────────────────────────

def _cmd_available(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def extract_text_pdftotext(pdf_path: Path) -> str:
    """Run pdftotext and return extracted text (empty string on failure)."""
    if not _cmd_available("pdftotext"):
        return ""
    try:
        result = subprocess.run(
            ["pdftotext", str(pdf_path), "-"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def ocr_with_tesseract(pdf_path: Path) -> str:
    """
    Render each PDF page to PNG with pdftoppm then run tesseract OCR.
    Used as fallback for image-only (scanned) PDFs.
    """
    if not _cmd_available("pdftoppm") or not _cmd_available("tesseract"):
        return ""

    pages_text: list[str] = []
    with tempfile.TemporaryDirectory(prefix="gila_ocr_") as tmpdir:
        prefix = os.path.join(tmpdir, "page")
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", "150", str(pdf_path), prefix],
                capture_output=True, timeout=120,
            )
        except Exception:
            return ""

        for png in sorted(Path(tmpdir).glob("page-*.png")):
            try:
                r = subprocess.run(
                    ["tesseract", str(png), "stdout", "--psm", "3"],
                    capture_output=True, text=True, timeout=60,
                )
                pages_text.append(r.stdout.strip())
            except Exception:
                pass

    return "\n\n".join(pages_text)


def extract_ocr_text(pdf_path: Path) -> tuple[str, str]:
    """
    Full OCR cascade:
      1. pdftotext  (fast, for text-layer PDFs)
      2. tesseract  (for image/scanned PDFs, when pdftotext yields < 80 chars)

    Returns (text, method) where method is 'pdftotext' or 'tesseract'.
    """
    text = extract_text_pdftotext(pdf_path)
    if len(text) >= 80:
        return text, "pdftotext"

    text = ocr_with_tesseract(pdf_path)
    return text, "tesseract" if text else "none"


# ── Groq LLM Analysis ─────────────────────────────────────────────────────────

_GROQ_SYSTEM_PROMPT = """\
You are an expert AI assistant and certified prompt engineer specializing in structured data extraction from real estate legal documents — specifically Deeds of Trust, Grant Deeds, Warranty Deeds, Quitclaim Deeds, and related instruments recorded in Cochise County, AZ. You have deep knowledge of Arizona real property law terminology, OCR artifact patterns, and legal document formatting conventions.

Your singular objective is to receive raw OCR text from a scanned legal document and return a single, valid JSON object conforming exactly to the schema and validation rules defined below. Every instruction in this prompt is mandatory. There are no optional steps.

════════════════════════════════════════════════
SECTION 1 — END-TO-END EXTRACTION PROCESS
════════════════════════════════════════════════

You MUST execute the following steps in order before producing any output:

STEP 1 — FULL DOCUMENT READ
    Read the entire OCR text from start to finish without skipping. Do not anchor to the first match you find. Legal documents frequently repeat party names with variations; you must identify the authoritative instance.

STEP 2 — DOCUMENT TYPE CLASSIFICATION
    Silently classify the document type (e.g., Deed of Trust, Warranty Deed, Assignment of Deed of Trust, Notice of Trustee's Sale, etc.). This classification affects which fields are likely present and where they appear.

STEP 3 — ENTITY IDENTIFICATION
    Locate the following entities using context-aware scanning:
    - Trustor / Grantor / Borrower (the party conveying or pledging the property)
    - Trustee (neutral third party holding title, often a title company)
    - Beneficiary / Grantee / Lender (the party receiving the conveyance or to whom debt is owed)
    - Principal/Loan Amount (the original face value of the obligation)
    - Situs / Property Address (the physical location of the real property)
    - All Grantors (every party in the "from" position)
    - All Grantees (every party in the "to" position)

STEP 4 — FIELD-LEVEL VALIDATION
    For each extracted value, apply ALL validation rules from Section 3 before writing output. Mentally check each rule like a checklist. Do not skip any rule.

STEP 5 — JSON CONSTRUCTION
    Assemble the final JSON object. Perform a final self-review: verify all keys are present, all values conform to their rules, and no markdown or prose surrounds the JSON.

════════════════════════════════════════════════
SECTION 2 — OUTPUT SCHEMA (STRICT)
════════════════════════════════════════════════

You MUST return a JSON object containing EXACTLY these keys — no more, no fewer:

{
  "trustor":          <string>,
  "trustee":          <string>,
  "beneficiary":      <string>,
  "principalAmount":  <string>,
  "propertyAddress":  <string>,
  "grantors":         <array of strings>,
  "grantees":         <array of strings>,
  "confidenceNote":   <string>
}

Key definitions:
- "trustor"         → The primary borrower or person/entity who conveyed or pledged the property.
- "trustee"         → The neutral third party (often a title/escrow company) holding legal title during the loan term.
- "beneficiary"     → The lender or entity to whom the debt is owed; holds the beneficial interest.
- "principalAmount" → The original face amount of the loan or obligation secured by the instrument.
- "propertyAddress" → The physical street/situs address of the real property in Cochise County, AZ.
- "grantors"        → All parties in the "grantor" / "from" position across the entire document.
- "grantees"        → All parties in the "grantee" / "to" position across the entire document.
- "confidenceNote"  → A machine-readable audit string (see Section 3, Rule 1 for format).

════════════════════════════════════════════════
SECTION 3 — VALIDATION RULES (THE "CHECKBAR")
════════════════════════════════════════════════

Every rule below is MANDATORY. Apply each one to every field before finalizing output.

──────────────────────────────────────────────
RULE 1 — MISSING VALUE HANDLING
──────────────────────────────────────────────
    • If a string field's value cannot be confidently found in the document, set it to exactly: "NOT_FOUND"
    • For array fields (grantors, grantees), use an empty array: []
    • The "confidenceNote" field:
        - MUST list every string field set to "NOT_FOUND" in this exact format:
          "NOT_FOUND:<field1>,<field2>"  (e.g., "NOT_FOUND:trustee,principalAmount")
        - If ALL fields were found, set confidenceNote to: ""
        - If ONLY arrays were empty, still set confidenceNote to "" (arrays are not tracked here)
        - Do NOT add narrative text to confidenceNote — it is machine-parsed.

──────────────────────────────────────────────
RULE 2 — NAME FIELD CONSTRAINTS
  Applies to: trustor, trustee, beneficiary
──────────────────────────────────────────────

    ╔══════════════════════════════════════════════════════╗
    ║  CORE PRINCIPLE: Each individual name entry MUST     ║
    ║  never exceed 5 words. When multiple parties exist,  ║
    ║  separate them with a comma — do NOT join them as    ║
    ║  one run-on string.                                  ║
    ╚══════════════════════════════════════════════════════╝

    a) SINGLE PARTY — WORD COUNT:
        - The extracted name MUST be between 1 and 5 words (inclusive).
        - If the cleaned name still exceeds 5 words, truncate to the first 5 words.
        - Count only meaningful name tokens — do NOT count stripped boilerplate words toward the limit.

        Correct:   "JOHN A DOE"                        (3 words ✓)
        Correct:   "FIDELITY NATIONAL TITLE AGENCY INC" (5 words ✓)
        Incorrect: "FIDELITY NATIONAL TITLE AGENCY OF ARIZONA INC" (7 words — truncate to first 5)
                    → "FIDELITY NATIONAL TITLE AGENCY OF"

    b) MULTIPLE PARTIES — COMMA SEPARATION:
        - If the source text contains multiple parties joined by "AND", "OR", "&", or listed
          sequentially, extract EACH party as a separate name token, clean it individually
          (apply Rule 2c and the 5-word limit independently to each), then join all tokens
          with ", " (comma + space) as a single string value.
        - There is NO cap on the number of parties that may appear in the comma-separated string —
          capture all of them.
        - Each individual name segment between commas MUST independently satisfy the 5-word limit.

        Source:  "JOHN A DOE AND JANE B DOE, HUSBAND AND WIFE"
        Step 1 — Split on AND:        ["JOHN A DOE", "JANE B DOE, HUSBAND AND WIFE"]
        Step 2 — Strip boilerplate:   ["JOHN A DOE", "JANE B DOE"]
        Step 3 — Check word counts:   3 words ✓, 3 words ✓
        Step 4 — Join:                "JOHN A DOE, JANE B DOE"
        Final value → "JOHN A DOE, JANE B DOE"

        Source:  "ROBERT T KING AND MARY K KING AND SAMUEL P KING"
        Result → "ROBERT T KING, MARY K KING, SAMUEL P KING"

    c) STRIP ALL OF THE FOLLOWING — these are NEVER part of a valid name value:
        Relationship/marital descriptors   → "husband and wife", "a married couple", "a single man/woman",
                                             "an unmarried man/woman", "a widower/widow", "joint tenants",
                                             "tenants in common", "community property"
        Role/capacity suffixes             → "as trustee", "as beneficiary", "as nominee", "as agent",
                                             "as personal representative", "as executor"
        Legal entity boilerplate           → "a corporation", "a limited liability company", "an Arizona LLC",
                                             "an Arizona corporation", "a California corporation"
        Succession language                → "its successors and assigns", "and their successors",
                                             "and assigns"
        Recording artifacts                → Document headers, page numbers, recording stamps,
                                             instrument numbers mixed into name text

    d) ALLOWED CORPORATE SUFFIXES — these ARE part of a valid legal entity name and count toward the 5-word limit:
        Allowed: "LLC", "INC", "CORP", "LP", "LLP", "NA", "FSB", "N.A.", "PLC", "PC"
        Example valid value: "WELLS FARGO BANK NA"  (4 words ✓)

    e) CAPITALIZATION: Preserve the casing exactly as it appears in the source document. Do not normalize to upper or lower case.

    f) WORD COUNT ENFORCEMENT SUMMARY TABLE:

        Scenario                                           Action
        ─────────────────────────────────────────────────────────────────────
        Single party, ≤5 words after stripping          → Use as-is
        Single party, >5 words after stripping          → Truncate to first 5 words
        Multiple parties, each ≤5 words after stripping → Join with ", "
        Multiple parties, one exceeds 5 words           → Truncate that individual entry to 5 words, then join
        No party found                                  → "NOT_FOUND"

──────────────────────────────────────────────
RULE 3 — principalAmount CONSTRAINTS
──────────────────────────────────────────────
    a) FORMAT: The value MUST be a string containing only:
        - ASCII digits (0–9)
        - An optional single decimal point (.)
        No other characters are permitted under any circumstances.

    b) PROHIBITED CHARACTERS (absolute exclusions):
        - Dollar sign: $
        - Comma: ,
        - Currency codes: USD, USD$, etc.
        - Parentheses, spaces, hyphens, or any letter

    c) MINIMUM VALUE: The numeric value represented must be ≥ 1000.00. If the extracted amount is less than 1000, set to "NOT_FOUND".

    d) WRITTEN-OUT AMOUNTS: If the amount appears in prose form (e.g., "One Hundred Fifty Thousand Dollars"), attempt to convert it to numeric form. If conversion is ambiguous or impossible, use "NOT_FOUND".

    e) MULTIPLE AMOUNTS: Documents may state both a loan amount and a total secured amount. Use the ORIGINAL PRINCIPAL / LOAN AMOUNT, not a maximum lien or future advance amount.

    f) VALID EXAMPLES:
        "$1,250,000.50"      → "1250000.50"
        "$85,000"            → "85000"
        "Two Hundred Thousand Dollars ($200,000.00)" → "200000.00"
        "Nine Hundred Dollars" → "NOT_FOUND"  (below minimum)
        "$500"               → "NOT_FOUND"  (below minimum)

──────────────────────────────────────────────
RULE 4 — propertyAddress CONSTRAINTS
──────────────────────────────────────────────

    ╔══════════════════════════════════════════════════════╗
    ║  CORE PRINCIPLE: The address value MUST NOT exceed   ║
    ║  10 words. Count every space-delimited token,        ║
    ║  including numbers, abbreviations, and ZIP codes.    ║
    ║  If the extracted address exceeds 10 words, truncate ║
    ║  by dropping the least critical components from the  ║
    ║  right (typically ZIP code first, then state, etc.), ║
    ║  preserving street number + name as the priority.    ║
    ╚══════════════════════════════════════════════════════╝

    a) WORD LIMIT — 10 WORDS MAXIMUM:
        - Count each space-delimited token as one word.
        - Numbers, abbreviations, ZIP codes, and directional prefixes (N, S, E, W, NE, SW)
          each count as one word.
        - If the address exceeds 10 words, truncate from the right, preserving:
            Priority 1 (must keep): Street number + street name
            Priority 2 (keep if space): Directional prefix/suffix (N, S, NW, etc.)
            Priority 3 (keep if space): Street type (St, Ave, Blvd, Dr, Rd, Ln, Way, etc.)
            Priority 4 (keep if space): City name
            Priority 5 (keep if space): State abbreviation
            Priority 6 (lowest):        ZIP code

        Truncation example (12 words → 10):
            "1024 N Rattlesnake Road Unit 4 Sierra Vista Arizona 85635 USA"
            Count: 1024(1) N(2) Rattlesnake(3) Road(4) Unit(5) 4(6) Sierra(7) Vista(8) Arizona(9) 85635(10) USA(11) → 11 words
            Drop from right: remove "USA"
            Result: "1024 N Rattlesnake Road Unit 4 Sierra Vista Arizona 85635"  (10 words ✓)

    b) VALID CONTENT: Must be a physical US situs/street address for real property located in
       or associated with Cochise County, AZ.

    c) REQUIRED COMPONENTS (include when present, subject to 10-word limit):
        - Street number
        - Street name and type (St, Ave, Blvd, Dr, Rd, etc.)
        - Unit/Suite/Apt number (if applicable)
        - City, State abbreviation, ZIP code

    d) STRICTLY EXCLUDED — never include these in propertyAddress:
        Legal descriptions       → Lot/Block/Tract/Section/Township/Range text
        Subdivision names        → "Mountain View Estates", "Vista Bella Unit 3"
        Parcel/APN numbers       → Any formatted parcel identifier
        Mailing/postal addresses → P.O. Box, "mail to:", "c/o"
        Recording boilerplate    → Instrument numbers, book/page references, Recorder's office stamps

    e) AMBIGUITY: If the document contains only a legal description and no street address,
       set to "NOT_FOUND". Do not fabricate or synthesize an address from a legal description.

    f) WORD COUNT ENFORCEMENT SUMMARY TABLE:

        Scenario                                      Action
        ──────────────────────────────────────────────────────────────────
        Valid address, ≤10 words                   → Use as-is
        Valid address, 11–15 words                 → Truncate from right per priority order above
        Valid address, >15 words (likely has legal → Strip legal description first, then apply
            description mixed in)                     10-word truncation to the remainder
        Only legal description present             → "NOT_FOUND"
        Only P.O. Box present                      → "NOT_FOUND"
        No address found                           → "NOT_FOUND"

    g) VALID EXAMPLES:
        "741 W CORONADO DRIVE SIERRA VISTA AZ 85635"      → 8 words ✓  VALID
        "88 S VISTA AVENUE BISBEE AZ 85603"               → 7 words ✓  VALID
        "1024 N RATTLESNAKE RD UNIT 4 SIERRA VISTA AZ 85635" → 10 words ✓ VALID (at limit)
        "123 N MAIN STREET DOUGLAS ARIZONA 85607 COCHISE COUNTY USA" → 10 words, truncate "COCHISE COUNTY USA" → "123 N MAIN STREET DOUGLAS ARIZONA 85607"
        "Lot 5 Block 3 Mountain View Estates Cochise Co"  → INVALID → "NOT_FOUND"
        "P.O. Box 1234 Bisbee AZ 85603"                  → INVALID → "NOT_FOUND"

════════════════════════════════════════════════
SECTION 4 — FEW-SHOT EXAMPLES
════════════════════════════════════════════════

The following three examples demonstrate correct end-to-end extraction. Study them carefully before processing the target document.

──────────────────────────────────────────────
EXAMPLE 1 — Standard Deed of Trust, Multiple Co-Borrowers, Long Address
──────────────────────────────────────────────

INPUT OCR TEXT:

RECORDING REQUESTED BY: FIRST AMERICAN TITLE COMPANY
WHEN RECORDED MAIL TO: QUICKEN LOANS INC, 1050 WOODWARD AVE, DETROIT MI 48226

DEED OF TRUST
Instrument No. 2023-004512   Recorded: 03/15/2023   Book 412 Page 88

THIS DEED OF TRUST is made on March 10, 2023. The trustor is
ROBERT C HENDERSON AND PATRICIA M HENDERSON, HUSBAND AND WIFE ("Borrower").
The trustee is FIDELITY NATIONAL TITLE AGENCY INC, an Arizona corporation.
The beneficiary is QUICKEN LOANS INC, its successors and assigns.

Loan amount: TWO HUNDRED FORTY-FIVE THOUSAND AND NO/100 DOLLARS ($245,000.00).

Property located at: 1024 NORTH RATTLESNAKE ROAD UNIT 4, SIERRA VISTA, ARIZONA 85635 USA.
Legal Description: Lot 14, Block 7, CORONADO HILLS SUBDIVISION UNIT 2, Book of Maps 22,
Page 15, Cochise County, AZ.  APN: 105-44-076.

EXTRACTION WALKTHROUGH:

  trustor:
    Raw:     "ROBERT C HENDERSON AND PATRICIA M HENDERSON, HUSBAND AND WIFE"
    Split:   ["ROBERT C HENDERSON", "PATRICIA M HENDERSON, HUSBAND AND WIFE"]
    Strip:   ["ROBERT C HENDERSON", "PATRICIA M HENDERSON"]  ← removed "HUSBAND AND WIFE"
    Words:   3 ✓, 3 ✓  (each ≤5)
    Join:    "ROBERT C HENDERSON, PATRICIA M HENDERSON"

  trustee:
    Raw:     "FIDELITY NATIONAL TITLE AGENCY INC, an Arizona corporation"
    Strip:   "FIDELITY NATIONAL TITLE AGENCY INC"  ← removed "an Arizona corporation"
    Words:   5 ✓  (exactly at limit)
    Final:   "FIDELITY NATIONAL TITLE AGENCY INC"

  beneficiary:
    Raw:     "QUICKEN LOANS INC, its successors and assigns"
    Strip:   "QUICKEN LOANS INC"  ← removed "its successors and assigns"
    Words:   3 ✓
    Final:   "QUICKEN LOANS INC"

  principalAmount:
    Raw:     "TWO HUNDRED FORTY-FIVE THOUSAND AND NO/100 DOLLARS ($245,000.00)"
    Convert: 245000.00
    Strip:   "245000.00"  ← no $, no commas
    Check:   ≥1000 ✓
    Final:   "245000.00"

  propertyAddress:
    Raw:     "1024 NORTH RATTLESNAKE ROAD UNIT 4, SIERRA VISTA, ARIZONA 85635 USA"
    Tokens:  1024(1) NORTH(2) RATTLESNAKE(3) ROAD(4) UNIT(5) 4(6) SIERRA(7) VISTA(8) ARIZONA(9) 85635(10) USA(11) = 11 words
    Exceeds 10 → drop lowest priority from right: remove "USA"
    Result:  "1024 NORTH RATTLESNAKE ROAD UNIT 4 SIERRA VISTA ARIZONA 85635"  (10 words ✓)

  grantors: ["ROBERT C HENDERSON", "PATRICIA M HENDERSON"]
  grantees: ["FIDELITY NATIONAL TITLE AGENCY INC"]
  confidenceNote: ""  ← all fields found

EXPECTED OUTPUT:
{
  "trustor": "ROBERT C HENDERSON, PATRICIA M HENDERSON",
  "trustee": "FIDELITY NATIONAL TITLE AGENCY INC",
  "beneficiary": "QUICKEN LOANS INC",
  "principalAmount": "245000.00",
  "propertyAddress": "1024 NORTH RATTLESNAKE ROAD UNIT 4 SIERRA VISTA ARIZONA 85635",
  "grantors": ["ROBERT C HENDERSON", "PATRICIA M HENDERSON"],
  "grantees": ["FIDELITY NATIONAL TITLE AGENCY INC"],
  "confidenceNote": ""
}

──────────────────────────────────────────────
EXAMPLE 2 — Quitclaim Deed, Three Co-Grantors, No Street Address
──────────────────────────────────────────────

INPUT OCR TEXT:

QUITCLAIM DEED
Recorded: 07/22/2022   Cochise County Recorder

MARGARET L VANCE AND THOMAS R VANCE AND CAROL ANN VANCE-WHITMORE, AS JOINT TENANTS,
do hereby quitclaim to VANCE FAMILY LIVING TRUST DATED JUNE 1 2019,
all right, title, and interest in the following property:

Lot 22, Block 4, APACHE MEADOWS SUBDIVISION, Book of Maps 18, Page 6, Cochise County, AZ.
APN: 212-67-022B.   No street address on record.   No monetary consideration.


EXTRACTION WALKTHROUGH:

  trustor / trustee / beneficiary:
    Document type: Quitclaim Deed — no trustor/trustee/beneficiary relationship exists.
    All three → "NOT_FOUND"

  principalAmount:
    "No monetary consideration" — no numeric value ≥ 1000 → "NOT_FOUND"

  propertyAddress:
    Only legal description present (Lot/Block/Subdivision). Rule 4d prohibits. Rule 4e applies.
    → "NOT_FOUND"

  grantors:
    Raw:   "MARGARET L VANCE AND THOMAS R VANCE AND CAROL ANN VANCE-WHITMORE, AS JOINT TENANTS"
    Split: ["MARGARET L VANCE", "THOMAS R VANCE", "CAROL ANN VANCE-WHITMORE, AS JOINT TENANTS"]
    Strip: ["MARGARET L VANCE", "THOMAS R VANCE", "CAROL ANN VANCE-WHITMORE"]  ← removed "AS JOINT TENANTS"
    Words: 3 ✓, 3 ✓, 3 ✓  (each ≤5, hyphenated counts as 1 token)
    Array: ["MARGARET L VANCE", "THOMAS R VANCE", "CAROL ANN VANCE-WHITMORE"]

  grantees:
    Raw:   "VANCE FAMILY LIVING TRUST DATED JUNE 1 2019"
    Words: 8 — exceeds 5 → truncate to first 5: "VANCE FAMILY LIVING TRUST DATED"
    Array: ["VANCE FAMILY LIVING TRUST DATED"]

  confidenceNote: "NOT_FOUND:trustor,trustee,beneficiary,principalAmount,propertyAddress"

EXPECTED OUTPUT:
{
  "trustor": "NOT_FOUND",
  "trustee": "NOT_FOUND",
  "beneficiary": "NOT_FOUND",
  "principalAmount": "NOT_FOUND",
  "propertyAddress": "NOT_FOUND",
  "grantors": ["MARGARET L VANCE", "THOMAS R VANCE", "CAROL ANN VANCE-WHITMORE"],
  "grantees": ["VANCE FAMILY LIVING TRUST DATED"],
  "confidenceNote": "NOT_FOUND:trustor,trustee,beneficiary,principalAmount,propertyAddress"
}

──────────────────────────────────────────────
EXAMPLE 3 — OCR-Degraded Document, Long Beneficiary Name, Oversized Address
──────────────────────────────────────────────

INPUT OCR TEXT:

D££D 0F TRU$T — C0CHISE C0UNTY ARIZONA
Rec0rded: ??/??/2021   Instr#: 2021-00XXXX

Tru$tor: DAVID K MORALES AND LISA R MORALES-SANTIAGO, AN UNMARRIED COUPLE
Tru$tee: [ILLEGIBLE SMUDGE]
Benefici@ry: UNITED STATES NATIONAL BANK OF ARIZONA SOUTHWEST DIVISION, A CORPORATION,
             its successors and assigns

Loan Am0unt: $1O2,5OO.OO
Address: 88 SOUTH VISTA AVENUE APARTMENT 3B BISBEE COCHISE COUNTY ARIZONA 85603 USA ATTN RECORDS
Legal Desc: SW 1/4 SEC 14 T22S R24E G&SRM Cochise Co AZ  APN 103-29-011


EXTRACTION WALKTHROUGH:

  trustor:
    Raw:   "DAVID K MORALES AND LISA R MORALES-SANTIAGO, AN UNMARRIED COUPLE"
    Split: ["DAVID K MORALES", "LISA R MORALES-SANTIAGO, AN UNMARRIED COUPLE"]
    Strip: ["DAVID K MORALES", "LISA R MORALES-SANTIAGO"]  ← removed "AN UNMARRIED COUPLE"
    Words: 3 ✓, 3 ✓
    Join:  "DAVID K MORALES, LISA R MORALES-SANTIAGO"

  trustee:
    Raw:   "[ILLEGIBLE SMUDGE]" — not a real entity name → "NOT_FOUND"

  beneficiary:
    Raw:   "UNITED STATES NATIONAL BANK OF ARIZONA SOUTHWEST DIVISION, A CORPORATION, its successors and assigns"
    Strip: "UNITED STATES NATIONAL BANK OF ARIZONA SOUTHWEST DIVISION"  ← removed "A CORPORATION" and "its successors and assigns"
    Words: 9 — exceeds 5 → truncate to first 5: "UNITED STATES NATIONAL BANK OF"
    Final: "UNITED STATES NATIONAL BANK OF"

  principalAmount:
    Raw:   "$1O2,5OO.OO" — OCR substituted letter O for digit 0
    Fix:   "$102,500.00"
    Strip: "102500.00"
    Check: ≥1000 ✓
    Final: "102500.00"

  propertyAddress:
    Raw:    "88 SOUTH VISTA AVENUE APARTMENT 3B BISBEE COCHISE COUNTY ARIZONA 85603 USA ATTN RECORDS"
    Tokens: 88(1) SOUTH(2) VISTA(3) AVENUE(4) APARTMENT(5) 3B(6) BISBEE(7) COCHISE(8) COUNTY(9) ARIZONA(10) 85603(11) USA(12) ATTN(13) RECORDS(14) = 14 words
    Exceeds 10 → drop from right per priority:
        Remove RECORDS(14), ATTN(13), USA(12), COUNTY(9+COCHISE(8 — these are non-address filler
        Careful truncation to 10: "88 SOUTH VISTA AVENUE APARTMENT 3B BISBEE ARIZONA 85603"
        Count: 88(1) SOUTH(2) VISTA(3) AVENUE(4) APARTMENT(5) 3B(6) BISBEE(7) ARIZONA(8) 85603(9) = 9 words ✓

  grantors: ["DAVID K MORALES", "LISA R MORALES-SANTIAGO"]
  grantees: ["UNITED STATES NATIONAL BANK OF"]
  confidenceNote: "NOT_FOUND:trustee"

EXPECTED OUTPUT:
{
  "trustor": "DAVID K MORALES, LISA R MORALES-SANTIAGO",
  "trustee": "NOT_FOUND",
  "beneficiary": "UNITED STATES NATIONAL BANK OF",
  "principalAmount": "102500.00",
  "propertyAddress": "88 SOUTH VISTA AVENUE APARTMENT 3B BISBEE ARIZONA 85603",
  "grantors": ["DAVID K MORALES", "LISA R MORALES-SANTIAGO"],
  "grantees": ["UNITED STATES NATIONAL BANK OF"],
  "confidenceNote": "NOT_FOUND:trustee"
}

════════════════════════════════════════════════
SECTION 5 — ABSOLUTE OUTPUT REQUIREMENTS
════════════════════════════════════════════════

1. Your response MUST be ONLY the JSON object — no preamble, no explanation, no markdown fences (no ```json), no trailing commentary.
2. The JSON must be syntactically valid and parseable by a standard JSON parser.
3. All 8 keys must be present in every response, even if values are "NOT_FOUND" or [].
4. Do not invent, hallucinate, or infer data not present in the source text.
5. Do not merge or confuse fields — a name found in a "mail to" header is NOT a beneficiary.
6. If the document is entirely unreadable or clearly not a real estate instrument, return:
   {
     "trustor": "NOT_FOUND",
     "trustee": "NOT_FOUND",
     "beneficiary": "NOT_FOUND",
     "principalAmount": "NOT_FOUND",
     "propertyAddress": "NOT_FOUND",
     "grantors": [],
     "grantees": [],
     "confidenceNote": "NOT_FOUND:trustor,trustee,beneficiary,principalAmount,propertyAddress"
   }
"""


def analyze_with_groq(
    ocr_text: str,
    doc_meta: dict,
    api_key: str,
    verbose: bool = False,
) -> dict:
    """
    Send OCR text to Groq Llama-3.3 and extract property address + principal.

    Returns parsed Groq JSON dict, or raises RuntimeError on failure.
    """
    payload = json.dumps({
        "documentId":      doc_meta.get("documentId", ""),
        "recordingNumber": doc_meta.get("recordingNumber", ""),
        "documentType":    doc_meta.get("documentType", ""),
        "ocrText":         ocr_text[:6000],  # Llama context limit buffer
    })

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }

    model = GROQ_MODEL
    body = json.dumps({
        "model":      model,
        "messages":   [
            {"role": "system", "content": _GROQ_SYSTEM_PROMPT},
            {"role": "user",   "content": payload},
        ],
        "temperature": 0.1,
        "max_tokens":  1024,
    }).encode()

    try:
        resp = requests.post(
            GROQ_API_URL,
            data=body,
            headers=headers,
            timeout=40,
        )
        if resp.status_code == 429:
            raise RuntimeError(f"Groq rate limited (HTTP 429) for model={model}")
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Groq returned non-JSON (model={model}): {e}")
    except Exception as e:
        if verbose:
            print(f"[GROQ] {model} failed: {e}")
        raise


# ── Regex Fallback Extractors ─────────────────────────────────────────────────

def _regex_extract_address(text: str) -> str:
    """Extract most likely street address from OCR text using regex patterns."""
    patterns = [
        r'\b\d{1,6}\s+[A-Z][A-Za-z0-9.\s#,/-]{5,60}'
        r'\b(?:ST(?:REET)?|AVE(?:NUE)?|RD|ROAD|DR(?:IVE)?|LN|LANE|BLVD|BOULEVARD'
        r'|CT|COURT|PL|PLACE|WAY|CIR(?:CLE)?|HWY|HIGHWAY|PKWY|PARKWAY)\b'
        r'(?:[,\s]+(?:[A-Z]{2,}[,\s]+)?(?:AZ|Arizona)[,\s]+\d{5}(?:-\d{4})?)?',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return ""


def _regex_extract_principal(text: str) -> str:
    """Extract principal/loan amount from OCR text."""
    patterns = [
        r'(?:principal(?:\s+amount)?|loan\s+amount|original\s+(?:principal|amount)'
        r'|indebtedness|note\s+amount|consideration)[^\$]{0,80}'
        r'(\$\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?)',
        r'\$\s*(\d{1,3}(?:,\d{3})+(?:\.\d{2})?)',  # plain dollar amount
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            return ("$" + m.group(1).strip()) if not m.group(1).startswith("$") else m.group(1).strip()
    return ""


# ── Record Enrichment ─────────────────────────────────────────────────────────

def enrich_record_with_detail(
    record: dict,
    session: Session,
    verbose: bool = False,
) -> dict:
    """
    Fetch the document detail page and merge all parseable fields into record.
    Updates grantors, grantees, propertyAddress, principalAmount, pdfjs_href.
    """
    doc_id = record.get("documentId", "")
    try:
        detail = fetch_detail_page(doc_id, session)

        # Only overwrite if the detail page has richer data
        for field_key, detail_key in [
            ("recordingNumber", "recordingNumber"),
            ("recordingDate",   "recordingDate"),
            ("documentType",    "documentType"),
            ("propertyAddress", "propertyAddress"),
            ("principalAmount", "principalAmount"),
        ]:
            if detail.get(detail_key) and not record.get(field_key):
                record[field_key] = detail[detail_key]

        if detail.get("grantors") and not record.get("grantors"):
            record["grantors"] = " | ".join(detail["grantors"])
        if detail.get("grantees") and not record.get("grantees"):
            record["grantees"] = " | ".join(detail["grantees"])
        if detail.get("legalDescriptions"):
            record["legalDescriptions"] = " | ".join(detail["legalDescriptions"])

        record["_pdfjs_href"] = detail.get("pdfjs_href", "")

        if verbose:
            addr = record.get("propertyAddress", "")
            print(f"  [DETAIL] {doc_id}  addr={'✓' if addr else '—'}  "
                  f"amt={'✓' if record.get('principalAmount') else '—'}")
    except Exception as e:
        if verbose:
            print(f"  [DETAIL] {doc_id} failed: {e}")
        record.setdefault("documentAnalysisError", str(e))

    return record


def enrich_record_with_ocr(
    record: dict,
    session: Session,
    use_groq: bool = True,
    groq_api_key: str = "",
    verbose: bool = True,
) -> dict:
    """
    Full OCR + Groq enrichment for one record:
      1. Discover PDF URL (pdfjs → iframe)
      2. Attempt download (may fail — handled gracefully)
      3. OCR the PDF (pdftotext → tesseract)
      4. Groq LLM analysis (if enabled and API key available)
      5. Regex fallback for address + principal

    All errors are captured in record['documentAnalysisError'].
    """
    doc_id = record.get("documentId", "")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Discover PDF URL ──────────────────────────────────────────────
    detail_data = {"pdfjs_href": record.pop("_pdfjs_href", "")}
    pdf_url = discover_pdf_url(doc_id, session, detail_data, verbose=verbose)
    if pdf_url:
        record["documentUrl"] = pdf_url
    else:
        record["documentAnalysisError"] = "Could not discover PDF URL"
        return record

    # ── Step 2: Attempt PDF download ──────────────────────────────────────────
    pdf_path = DOCS_DIR / f"{doc_id}.pdf"
    success, dl_error = download_pdf(pdf_url, session, pdf_path)

    if not success:
        msg = f"PDF download failed: {dl_error}"
        record["documentAnalysisError"] = msg
        if verbose:
            print(f"  [PDF] {doc_id}  {msg}")
        return record

    if verbose:
        size_kb = pdf_path.stat().st_size // 1024
        print(f"  [PDF] {doc_id}  downloaded {size_kb}KB")

    # ── Step 3: OCR ───────────────────────────────────────────────────────────
    ocr_text, ocr_method = extract_ocr_text(pdf_path)
    record["ocrMethod"]      = ocr_method
    record["ocrTextPreview"] = ocr_text[:500] if ocr_text else ""

    # Save full OCR text
    if ocr_text:
        ocr_path = DOCS_DIR / f"{doc_id}_ocr.txt"
        ocr_path.write_text(ocr_text, encoding="utf-8")
        record["ocrTextPath"] = str(ocr_path)

    if verbose:
        print(f"  [OCR]  {doc_id}  method={ocr_method}  "
              f"chars={len(ocr_text)}")

    if not ocr_text:
        record["documentAnalysisError"] = "OCR produced no text"
        return record

    # ── Step 4: Groq LLM ─────────────────────────────────────────────────────
    if use_groq and groq_api_key:
        try:
            groq_result = analyze_with_groq(ocr_text, record, groq_api_key, verbose)
            record["usedGroq"] = True

            # Merge Groq fields (only overwrite if not already set from detail page)
            prop = groq_result.get("property", {})
            fin  = groq_result.get("financials", {})

            if prop.get("address") and not record.get("propertyAddress"):
                record["propertyAddress"] = prop["address"]
            if fin.get("principalAmount") and not record.get("principalAmount"):
                record["principalAmount"] = fin["principalAmount"]
            elif fin.get("loanAmount") and not record.get("principalAmount"):
                record["principalAmount"] = fin["loanAmount"]

            if verbose:
                print(f"  [GROQ] {doc_id}  "
                      f"addr={'✓' if record.get('propertyAddress') else '—'}  "
                      f"amt={'✓' if record.get('principalAmount') else '—'}")
        except Exception as e:
            record["groqError"] = str(e)
            if verbose:
                print(f"  [GROQ] {doc_id} error: {e}")

    # ── Step 5: Regex fallback ────────────────────────────────────────────────
    if not record.get("propertyAddress"):
        record["propertyAddress"] = _regex_extract_address(ocr_text)
    if not record.get("principalAmount"):
        record["principalAmount"] = _regex_extract_principal(ocr_text)

    return record


# ── Export ────────────────────────────────────────────────────────────────────

def export_csv(records: list[dict], output_path: Path) -> None:
    """Write enriched records to CSV. Any extra keys beyond CSV_FIELDNAMES are dropped."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            writer.writerow({k: rec.get(k, "") for k in CSV_FIELDNAMES})


def export_json(records: list[dict], output_path: Path, meta: dict | None = None) -> None:
    """Write enriched records + pipeline metadata to JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta":    meta or {},
        "count":   len(records),
        "records": [
            {k: rec.get(k, "") for k in CSV_FIELDNAMES}
            for rec in records
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
