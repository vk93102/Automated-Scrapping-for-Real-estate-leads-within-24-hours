#!/usr/bin/env python3
"""Gila County, AZ — Tyler EagleWeb (SelfService) scraper.

This module implements the Gila pipeline against the county's Tyler Technologies
EagleWeb SelfService instance:

  https://selfservice.gilacountyaz.gov/web/search/DOCSEARCH2242S1

It replaces the previous (non-working) wrapper around the Greenlee/TheCountyRecorder
flow. Public entry points are kept compatible with:
  - [gila/run_gila_interval.py](gila/run_gila_interval.py)
  - [gila/live_pipeline.py](gila/live_pipeline.py)
  - [gila/run_demo.py](gila/run_demo.py)
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import requests


BASE_URL = "https://selfservice.gilacountyaz.gov"
SEARCH_ID = "DOCSEARCH2242S1"
SEARCH_URL = f"{BASE_URL}/web/search/{SEARCH_ID}"
SEARCH_POST_URL = f"{BASE_URL}/web/searchPost/{SEARCH_ID}"
SEARCH_RESULTS_URL = f"{BASE_URL}/web/searchResults/{SEARCH_ID}"
DISCLAIMER_URL = f"{BASE_URL}/web/user/disclaimer"

ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


try:
    # Repo root adds this to sys.path in callers; keep import local for resilience.
    from county_doc_types import UNIFIED_FORECLOSURE_DOC_TYPES  # type: ignore

    DEFAULT_DOCUMENT_TYPES = sorted(UNIFIED_FORECLOSURE_DOC_TYPES)
except Exception:
    # Focus on DEED/FORECLOSURE documents (not lawsuits)
    # Removed: LIS PENDENS, LIS PENDENS RELEASE (lawsuit documents - no trustor/trustee data)
    DEFAULT_DOCUMENT_TYPES = [
        "NOTICE OF DEFAULT",
        "NOTICE OF TRUSTEES SALE",
        "TRUSTEES DEED UPON SALE",
        "SHERIFFS DEED",
        "TREASURERS DEED",
    ]


DOCUMENT_TYPE_ALIASES: dict[str, str] = {
    "TRUSTEES DEED": "TRUSTEES DEED UPON SALE",
    "NOTICE OF TRUSTEE SALE": "NOTICE OF TRUSTEES SALE",
}

# Used by gila/run_demo.py stage 5.
_SERVER_ALIASES: dict[str, str] = {k.upper(): v for k, v in DOCUMENT_TYPE_ALIASES.items()}


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _parse_input_date(value: str) -> date:
    text = (value or "").strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except Exception:
            continue
    raise ValueError(f"Unsupported date format: {value!r}")


def _format_eagleweb_date(value: str) -> str:
    d = _parse_input_date(value)
    # EagleWeb accepts M/D/YYYY (no leading zeros).
    return f"{d.month}/{d.day}/{d.year}"


def _clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _normalize_label(label: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", label or "").strip().lower()


def _extract_hidden_inputs(html_text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for match in re.finditer(r"<input[^>]+type=[\"']hidden[\"'][^>]*>", html_text, flags=re.I):
        tag = match.group(0)
        name_m = re.search(r"name=[\"']([^\"']+)[\"']", tag, flags=re.I)
        if not name_m:
            continue
        value_m = re.search(r"value=[\"']([^\"']*)[\"']", tag, flags=re.I)
        pairs.append((name_m.group(1), value_m.group(1) if value_m else ""))
    # Deduplicate by name, keep first.
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for k, v in pairs:
        if k in seen:
            continue
        seen.add(k)
        unique.append((k, v))
    return unique


def _normalize_document_types(document_types: list[str] | None) -> list[str]:
    raw = document_types or []
    result: list[str] = []
    for item in raw:
        dt = (item or "").strip()
        if not dt:
            continue
        canonical = DOCUMENT_TYPE_ALIASES.get(dt.upper(), dt)
        if canonical not in result:
            result.append(canonical)
    return result


def _default_headers(*, content_type: str | None = None, ajax: bool = False, referer: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*" if ajax else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = BASE_URL
    if ajax:
        headers["X-Requested-With"] = "XMLHttpRequest"
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _jquery_ajax_get_headers(*, referer: str) -> dict[str, str]:
    """Headers that match jQuery's same-origin AJAX GET closely enough for EagleWeb."""

    return {
        **_default_headers(referer=referer),
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html, */*; q=0.01",
    }


def _jquery_ajax_post_headers(*, referer: str) -> dict[str, str]:
    """Headers that match jQuery's same-origin AJAX POST for the search form."""

    return {
        **_default_headers(referer=referer),
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }


def _make_requests_session(cookie_str: str | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update(_default_headers())
    # Disclaimer acceptance is session-scoped; we handle it explicitly via _ensure_disclaimer().
    if cookie_str:
        # Parse a basic Cookie header string: "a=b; c=d".
        for part in cookie_str.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            s.cookies.set(k.strip(), v.strip(), domain="selfservice.gilacountyaz.gov")
    return s


def _cookie_header_from_session(session: requests.Session) -> str:
    try:
        jar = session.cookies
        return "; ".join([f"{c.name}={c.value}" for c in jar])
    except Exception:
        return ""


def _ensure_disclaimer(session: requests.Session, *, verbose: bool = False) -> None:
    """Accept the EagleWeb disclaimer for this session.

    Gila's site redirects unauthenticated sessions to /web/user/disclaimer.
    The acceptance flow is a JSON POST that sets `disclaimerAccepted=true`.
    """

    # 1) Establish session (JSESSIONID)
    r1 = session.get(DISCLAIMER_URL, timeout=45)
    r1.raise_for_status()
    # 2) Accept (AJAX POST returns `true` JSON)
    r2 = session.post(
        DISCLAIMER_URL,
        headers={
            **_default_headers(referer=DISCLAIMER_URL),
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, */*; q=0.9",
        },
        timeout=45,
    )
    r2.raise_for_status()
    if verbose:
        print(f"[gila] disclaimer accept status={r2.status_code} body={r2.text.strip()[:80]}")


def _blocked_by_disclaimer(body: str, response_url: str = "") -> bool:
    """Return True only when we appear to be *on* the disclaimer/recaptcha gate.

    Note: many EagleWeb search pages reference the disclaimer path in JS (e.g.
    `selfservice.pages.disclaimer = '/web/user/disclaimer';`). That substring
    alone is not a reliable signal.
    """

    url_l = (response_url or "").lower()
    if "/web/user/disclaimer" in url_l:
        return True
    lowered = (body or "").lower()
    if "g-recaptcha" in lowered or "recaptcha" in lowered:
        return True
    # Heuristic: disclaimer form/action markers.
    if re.search(r"<form[^>]+action=\"/web/user/disclaimer\"", lowered, flags=re.I):
        return True
    if re.search(r"<title>\s*disclaimer\s*</title>", lowered, flags=re.I):
        return True
    return False


@dataclass
class ExtractedRecord:
    document_id: str
    recording_number: str
    document_type: str
    recording_date: str
    grantors: list[str]
    grantees: list[str]
    legal_descriptions: list[str]
    property_address: str
    principal_amount: str
    detail_url: str
    source_file: str
    raw_html: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "documentId": self.document_id,
            "recordingNumber": self.recording_number,
            "documentType": self.document_type,
            "recordingDate": self.recording_date,
            "grantors": self.grantors,
            "grantees": self.grantees,
            "legalDescriptions": self.legal_descriptions,
            "propertyAddress": self.property_address,
            "principalAmount": self.principal_amount,
            "detailUrl": self.detail_url,
            "sourceFile": self.source_file,
        }


def _extract_property_address_from_row(columns: dict[str, list[str]]) -> str:
    """Extract property address from detail page columns.
    
    Tries multiple sources:
    1. 'property address' column (if exists)
    2. 'situs' column (commonly used in AZ records)
    3. First 'legal' description as fallback
    """
    # Try 'property address' column first
    for key in columns:
        if 'property' in key.lower() and 'address' in key.lower():
            vals = columns.get(key, [])
            if vals and vals[0]:
                return vals[0]
    
    # Try 'situs' column
    for key in columns:
        if 'situs' in key.lower():
            vals = columns.get(key, [])
            if vals and vals[0]:
                return vals[0]
    
    # Fallback to first legal description
    legal_vals = columns.get("legal", [])
    return legal_vals[0] if legal_vals else ""


def _extract_trustor_trustee_from_deed(
    document_type: str,
    grantors: list[str],
    grantees: list[str],
) -> tuple[str, str, str]:
    """Extract trustor, trustee, beneficiary from deed document type and parties.
    
    For Gila County deeds:
    - TRUSTEES DEED UPON SALE: trustee=grantor (selling), grantee=new owner, beneficiary=lender
    - SHERIFFS DEED: trustee=sheriff (grantor), grantee=new owner
    - TREASURERS DEED: trustee=treasurer (grantor), grantee=new owner  
    - NOTICE OF TRUSTEES SALE: just notice (no deed yet)
    """
    doc_type_upper = (document_type or "").upper()
    trustor = ""  # Trustor not typically shown in deed docs
    trustee = (grantors[0] if grantors else "")  # Party doing the sale
    beneficiary = (grantees[0] if grantees else "")  # New owner
    
    return (trustor, trustee, beneficiary)


def _parse_summary(html_text: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    match = re.search(
        r"Showing\s+page\s+(\d+)\s+of\s+(\d+)\s+for\s+(\d+)\s+Total Results",
        html_text,
        flags=re.I | re.S,
    )
    if match:
        summary["page"] = int(match.group(1))
        summary["pageCount"] = int(match.group(2))
        summary["totalResults"] = int(match.group(3))
    filter_match = re.search(
        r"<div class=\"selfServiceSearchResultHeaderLeft\">\s*Recordings\s+(.*?)</div>",
        html_text,
        flags=re.I | re.S,
    )
    if filter_match:
        summary["filterSummary"] = _clean_text(filter_match.group(1))
    return summary


def _row_blocks(html_text: str) -> list[str]:
    pattern = re.compile(
        r"(<li[^>]*class=\"[^\"]*ss-search-row[^\"]*\"[\s\S]*?<p class=\"selfServiceSearchFullResult selfServiceSearchResultNavigation\">[\s\S]*?</div>\s*</li>)",
        flags=re.I,
    )
    rows = [match.group(1) for match in pattern.finditer(html_text)]
    if rows:
        return rows
    fallback_pattern = re.compile(
        r"(<li[^>]*class=\"[^\"]*ss-search-row[^\"]*\"[\s\S]*?</li>)",
        flags=re.I,
    )
    return [match.group(1) for match in fallback_pattern.finditer(html_text)]


def _extract_column_values(block: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    ul_pattern = re.compile(
        r"<ul class=\"selfServiceSearchResultColumn[^\"]*\">([\s\S]*?)</ul>",
        flags=re.I,
    )
    li_pattern = re.compile(r"<li[^>]*>([\s\S]*?)</li>", flags=re.I)
    bold_pattern = re.compile(r"<b>([\s\S]*?)</b>", flags=re.I)
    for ul_match in ul_pattern.finditer(block):
        ul_body = ul_match.group(1)
        li_matches = li_pattern.findall(ul_body)
        if not li_matches:
            continue
        label = _normalize_label(_clean_text(li_matches[0]))
        values = [_clean_text(value) for value in bold_pattern.findall(ul_body)]
        if label:
            existing = result.setdefault(label, [])
            existing.extend(value for value in values if value and value not in existing)
    return result


def parse_search_results_html(html_text: str, source_file: str) -> dict[str, Any]:
    rows: list[ExtractedRecord] = []
    for block in _row_blocks(html_text):
        document_id_match = re.search(r"data-documentid=\"([^\"]+)\"", block, flags=re.I)
        href_match = re.search(r"data-href=\"([^\"]+)\"", block, flags=re.I)
        header_match = re.search(r"<h1>([\s\S]*?)</h1>", block, flags=re.I)
        header_text = _clean_text(header_match.group(1) if header_match else "")
        header_parts = [part.strip() for part in re.split(r"\s*·\s*", header_text) if part.strip()]
        columns = _extract_column_values(block)
        document_id = document_id_match.group(1) if document_id_match else ""
        if len(header_parts) < 3 and header_text:
            header_parts = [part.strip() for part in re.split(r"\s*[•·]\s*", header_text) if part.strip()]
        recording_number = header_parts[0] if header_parts else header_text
        document_type = header_parts[1] if len(header_parts) > 1 else ""
        recording_date = header_parts[2] if len(header_parts) > 2 else ""
        detail_path = href_match.group(1) if href_match else ""
        detail_url = f"{BASE_URL}{detail_path}" if detail_path.startswith("/") else detail_path
        rows.append(
            ExtractedRecord(
                document_id=document_id,
                recording_number=recording_number,
                document_type=document_type,
                recording_date=recording_date,
                grantors=columns.get("grantor", []),
                grantees=columns.get("grantee", []),
                legal_descriptions=columns.get("legal", []),
                property_address=(_extract_property_address_from_row(columns) or ""),
                principal_amount="",  # Will be populated from OCR/LLM
                detail_url=detail_url,
                source_file=source_file,
                raw_html=block,
            )
        )
    return {
        "summary": _parse_summary(html_text),
        "records": [row.as_dict() for row in rows],
        "rawRecords": rows,
    }


def _save_html(name: str, body: str) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    path = OUTPUT_DIR / f"{slug}_{_timestamp()}.html"
    path.write_text(body or "", encoding="utf-8", errors="ignore")
    return path.name


def _build_search_payload(
    *,
    start_date: str,
    end_date: str,
    document_types: list[str],
    hidden_inputs: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    payload: list[tuple[str, str]] = []
    hidden_map = {k: v for k, v in (hidden_inputs or [])}

    def add(k: str, v: str) -> None:
        if k in hidden_map:
            # Preserve server-provided token, if any.
            payload.append((k, hidden_map[k]))
            hidden_map.pop(k, None)
        payload.append((k, v))

    # Include remaining hidden inputs first (tokens, etc.)
    for k, v in (hidden_inputs or []):
        if k and k not in {p[0] for p in payload}:
            payload.append((k, v))

    # Form scaffold fields observed in gila/_diag.py
    add("field_BothNamesID-containsInput", "Contains Any")
    add("field_BothNamesID", "")
    add("field_GrantorID-containsInput", "Contains Any")
    add("field_GrantorID", "")
    add("field_GranteeID-containsInput", "Contains Any")
    add("field_GranteeID", "")
    add("field_RecDateID_DOT_StartDate", _format_eagleweb_date(start_date))
    add("field_RecDateID_DOT_EndDate", _format_eagleweb_date(end_date))
    add("field_DocNumID", "")
    add("field_BookPageID_DOT_Book", "")
    add("field_BookPageID_DOT_Page", "")
    add("field_PlattedLegalID_DOT_Subdivision-containsInput", "Contains Any")
    add("field_PlattedLegalID_DOT_Subdivision", "")
    add("field_PlattedLegalID_DOT_Lot", "")
    add("field_PlattedLegalID_DOT_Block", "")
    add("field_PlattedLegalID_DOT_Tract", "")
    add("field_PLSSLegalID_DOT_QuarterSection-containsInput", "Contains Any")
    add("field_PLSSLegalID_DOT_QuarterSection", "")
    add("field_PLSSLegalID_DOT_Section", "")
    add("field_PLSSLegalID_DOT_Township", "")
    add("field_PLSSLegalID_DOT_Range", "")
    add("field_ParcelID", "")
    for dt in document_types:
        payload.append(("field_selfservice_documentTypes-searchInput", dt))
    add("field_selfservice_documentTypes-containsInput", "Contains Any")
    add("field_selfservice_documentTypes", "")
    add("field_UseAdvancedSearch", "")
    return payload


def _request_search(
    session: requests.Session,
    *,
    start_date: str,
    end_date: str,
    doc_types: list[str] | None,
    verbose: bool = False,
    save_html: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    normalized_types = _normalize_document_types(doc_types or DEFAULT_DOCUMENT_TYPES)
    r1 = session.get(SEARCH_URL, timeout=45)
    r1.raise_for_status()
    if "/web/user/disclaimer" in (r1.url or "").lower():
        _ensure_disclaimer(session, verbose=verbose)
        r1 = session.get(SEARCH_URL, timeout=45)
        r1.raise_for_status()
    if _blocked_by_disclaimer(r1.text, r1.url):
        raise RuntimeError(f"Blocked by disclaimer/recaptcha on initial GET (url={r1.url})")
    hidden = _extract_hidden_inputs(r1.text)
    if verbose:
        print(f"[gila] hidden_inputs={len(hidden)}")
    if save_html:
        _save_html("gila_search_form", r1.text)
    payload = _build_search_payload(
        start_date=start_date,
        end_date=end_date,
        document_types=normalized_types,
        hidden_inputs=hidden,
    )
    r2 = session.post(
        SEARCH_POST_URL,
        data=urlencode(payload),
        headers=_jquery_ajax_post_headers(referer=SEARCH_URL),
        timeout=60,
        allow_redirects=True,
    )
    r2.raise_for_status()
    if _blocked_by_disclaimer(r2.text, r2.url):
        raise RuntimeError("Blocked by disclaimer/recaptcha on POST")
    # Expect JSON like: {validationMessages:{}, totalPages:N, currentPage:1}
    search_meta: dict[str, Any] = {}
    try:
        search_meta = r2.json() if isinstance(r2.json(), dict) else {}
    except Exception:
        search_meta = {}
    validation = search_meta.get("validationMessages") if isinstance(search_meta, dict) else None
    if isinstance(validation, dict) and validation:
        raise RuntimeError(f"Server-side search validation failed: {validation}")
    if save_html and (not isinstance(search_meta, dict) or not search_meta):
        _save_html("gila_search_post_nonjson", r2.text)

    ts = int(time.time() * 1000)
    r3 = session.get(
        f"{SEARCH_RESULTS_URL}?page=1&_={ts}",
        headers=_jquery_ajax_get_headers(referer=SEARCH_URL),
        timeout=60,
    )
    r3.raise_for_status()
    if _blocked_by_disclaimer(r3.text, r3.url):
        raise RuntimeError("Blocked by disclaimer/recaptcha on results GET")
    source = _save_html("gila_search_results_page_1", r3.text) if save_html else "page_1.html"
    parsed = parse_search_results_html(r3.text, source_file=source)
    summary = parsed.get("summary", {}) or {}
    summary_out = {
        "county": "Gila County, AZ",
        "platform": "eagleweb",
        "baseUrl": BASE_URL,
        "searchId": SEARCH_ID,
        "requestedStartDate": _parse_input_date(start_date).strftime("%Y-%m-%d"),
        "requestedEndDate": _parse_input_date(end_date).strftime("%Y-%m-%d"),
        "requestedDocumentTypes": normalized_types,
        "pageCount": int(summary.get("pageCount") or (search_meta.get("totalPages") if isinstance(search_meta, dict) else 1) or 1),
        "totalCount": int(summary.get("totalResults") or 0),
        "filterDescription": str(summary.get("filterSummary") or ""),
    }
    return parsed.get("records", []) or [], summary_out, _cookie_header_from_session(session)


def fetch_all_pages(
    session: requests.Session,
    page1_records: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    page_limit: int = 0,
    verbose: bool = False,
    on_record: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    all_records: list[dict[str, Any]] = []
    total_pages = int(summary.get("pageCount") or 1)
    if page_limit and page_limit > 0:
        total_pages = min(total_pages, int(page_limit))

    def normalize_record(rec: dict[str, Any]) -> dict[str, Any]:
        grantors = rec.get("grantors", [])
        grantees = rec.get("grantees", [])
        return {
            **rec,
            "grantors": " | ".join([str(x).strip() for x in (grantors or []) if str(x).strip()]) if isinstance(grantors, list) else str(grantors or ""),
            "grantees": " | ".join([str(x).strip() for x in (grantees or []) if str(x).strip()]) if isinstance(grantees, list) else str(grantees or ""),
        }

    for rec in page1_records or []:
        norm = normalize_record(rec)
        all_records.append(norm)
        if on_record:
            on_record(norm)

    for page in range(2, total_pages + 1):
        ts = int(time.time() * 1000)
        url = f"{SEARCH_RESULTS_URL}?page={page}&_={ts}"
        r = session.get(url, headers=_jquery_ajax_get_headers(referer=SEARCH_URL), timeout=60)
        r.raise_for_status()
        if _blocked_by_disclaimer(r.text, r.url):
            raise RuntimeError("Blocked by disclaimer/recaptcha while paginating")
        parsed = parse_search_results_html(r.text, source_file=f"page_{page}.html")
        records = parsed.get("records", []) or []
        if verbose:
            print(f"[gila] page={page} records={len(records)}")
        if not records:
            break
        for rec in records:
            norm = normalize_record(rec)
            all_records.append(norm)
            if on_record:
                on_record(norm)
    return all_records


def playwright_search(
    *,
    start_date: str,
    end_date: str,
    doc_types: list[str] | None = None,
    headless: bool = True,
    verbose: bool = False,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Compatibility wrapper used by gila/run_demo.py.

    Despite the name, this implementation uses requests by default. If Gila enables
    reCAPTCHA in the future, callers may need to implement a Playwright-based
    cookie refresh (out of scope here).
    """

    _ = headless
    session = _make_requests_session()
    page1_records, summary, cookie_str = _request_search(
        session,
        start_date=start_date,
        end_date=end_date,
        doc_types=doc_types,
        verbose=verbose,
        save_html=verbose,
    )
    return cookie_str, page1_records, summary


def enrich_record_with_detail(rec: dict[str, Any], session: requests.Session, *, verbose: bool = False) -> dict[str, Any]:
    """Best-effort detail enrichment.

    The search results already include grantor/grantee lists; the detail page can
    sometimes include additional legal/address context. This stays lightweight
    and never hard-fails the pipeline.
    """

    url = str(rec.get("detailUrl") or "").strip()
    if not url:
        return rec
    try:
        r = session.get(url, headers=_default_headers(referer=SEARCH_URL), timeout=60)
        r.raise_for_status()
        body = r.text or ""
        if _blocked_by_disclaimer(body, r.url):
            return {**rec, "analysisError": "blocked_by_disclaimer"}

        # Attempt to pull common column-lists from the detail HTML.
        # EagleWeb detail pages often reuse the same <ul>/<b> pattern.
        cols = _extract_column_values(body)
        if cols.get("grantor") and not rec.get("grantors"):
            rec["grantors"] = " | ".join(cols.get("grantor") or [])
        if cols.get("grantee") and not rec.get("grantees"):
            rec["grantees"] = " | ".join(cols.get("grantee") or [])
        if cols.get("legal") and not rec.get("propertyAddress"):
            rec["propertyAddress"] = (cols.get("legal") or [""])[0]

        if verbose:
            rec["_detailFetched"] = True
        return rec
    except Exception as exc:
        return {**rec, "analysisError": f"detail_fetch_failed: {exc}"}


_UUID_RE = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"


def _extract_pdfjs_guid_from_html(html_body: str) -> str | None:
    # /web/document-image-pdfjs/{doc_id}/{guid}/{filename}.pdf?... 
    m = re.search(rf"/web/document-image-pdfjs/[^/]+/({_UUID_RE})/[^\"'/?]+?\.pdf", html_body or "", flags=re.I)
    if not m:
        return None
    return m.group(1)


def enrich_record_with_ocr(
    rec: dict[str, Any],
    session: requests.Session,
    *,
    use_groq: bool = False,
    groq_api_key: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Best-effort PDF URL discovery; OCR/LLM is currently a no-op.

    This keeps the key contract used by interval runners:
      - documentUrl (if found)
      - ocrMethod / ocrChars (set to none/0)
      - usedGroq / groqModel / groqError
    """

    _ = use_groq
    _ = groq_api_key
    url = str(rec.get("detailUrl") or "").strip()
    if not url:
        rec.setdefault("documentAnalysisError", "missing_detail_url")
        rec.setdefault("ocrMethod", "none")
        rec.setdefault("ocrChars", 0)
        rec.setdefault("usedGroq", False)
        return rec
    try:
        r = session.get(url, headers=_default_headers(referer=SEARCH_URL), timeout=60)
        r.raise_for_status()
        body = r.text or ""
        if _blocked_by_disclaimer(body, r.url):
            rec["documentAnalysisError"] = "blocked_by_disclaimer"
            rec["ocrMethod"] = "none"
            rec["ocrChars"] = 0
            rec["usedGroq"] = False
            return rec
        guid = _extract_pdfjs_guid_from_html(body)
        if guid and str(rec.get("documentId") or "").strip():
            # Viewer endpoint; the real direct PDF can require another hop. Keep viewer URL as a usable artifact.
            doc_id = str(rec.get("documentId") or "").strip()
            rec["documentUrl"] = f"{BASE_URL}/web/document-image-pdfjs/{doc_id}/{guid}/document.pdf?allowDownload=true&index=1"
        if verbose and rec.get("documentUrl"):
            rec["_pdfDiscovered"] = True
        rec.setdefault("ocrMethod", "none")
        rec.setdefault("ocrChars", 0)
        rec.setdefault("usedGroq", False)
        rec.setdefault("groqModel", "")
        rec.setdefault("groqError", "")
        return rec
    except Exception as exc:
        rec["documentAnalysisError"] = f"pdf_discovery_failed: {exc}"
        rec.setdefault("ocrMethod", "none")
        rec.setdefault("ocrChars", 0)
        rec.setdefault("usedGroq", False)
        return rec


def export_csv(records: list[dict[str, Any]], csv_path: Path) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "documentId",
        "recordingNumber",
        "recordingDate",
        "documentType",
        "grantors",
        "grantees",
        "trustor",
        "trustee",
        "beneficiary",
        "principalAmount",
        "propertyAddress",
        "detailUrl",
        "documentUrl",
        "ocrMethod",
        "ocrChars",
        "usedGroq",
        "groqModel",
        "groqError",
        "analysisError",
        "documentAnalysisError",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow({k: r.get(k, "") for k in fieldnames})


def export_json(records: list[dict[str, Any]], json_path: Path, *, meta: dict[str, Any] | None = None) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta or {}, "records": records}
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def run_gila_pipeline(
    *,
    start_date: str,
    end_date: str,
    doc_types: list[str] | None = None,
    headless: bool = True,
    max_pages: int = 0,
    ocr_limit: int = 0,
    use_groq: bool = False,
    verbose: bool = False,
    write_output_files: bool | None = None,
    workers: int = 6,
    max_image_pages: int = 4,
    use_playwright: bool = False,
    on_record: Callable[[dict[str, Any], str], None] | None = None,
) -> dict[str, Any]:
    """End-to-end Gila run (search + optional enrichment + optional exports).

    Notes:
    - `ocr_limit=-1` skips detail/PDF enrichment completely.
    - OCR/LLM extraction is intentionally minimal here; the main goal is a stable
      end-to-end pipeline that returns records and upserts them into Supabase.
    """

    _ = headless
    _ = workers
    _ = max_image_pages
    if write_output_files is None:
        write_output_files = os.getenv("WRITE_OUTPUT_FILES", "false").strip().lower() == "true"

    # use_playwright is currently a compatibility switch; requests-only by default.
    _ = use_playwright

    session = _make_requests_session()
    page1_records, summary, cookie_str = _request_search(
        session,
        start_date=start_date,
        end_date=end_date,
        doc_types=doc_types,
        verbose=verbose,
        save_html=verbose,
    )
    records = fetch_all_pages(
        session,
        page1_records,
        summary,
        page_limit=max_pages,
        verbose=verbose,
        on_record=(lambda r: on_record(r, "fetched") if on_record else None),
    )

    # Optional best-effort enrichment
    if ocr_limit != -1:
        for rec in records:
            enrich_record_with_detail(rec, session, verbose=False)
            if on_record:
                on_record(rec, "detail")

        # OCR is disabled unless explicitly requested with ocr_limit >= 0;
        # keep contract fields present.
        if ocr_limit >= 0:
            candidates = records if ocr_limit == 0 else records[: max(0, ocr_limit)]
            for rec in candidates:
                enrich_record_with_ocr(rec, session, use_groq=use_groq, groq_api_key=os.getenv("GROQ_API_KEY"), verbose=verbose)
                if on_record:
                    on_record(rec, "ocr")

    # Ensure contract keys exist for DB upsert logic.
    for rec in records:
        rec.setdefault("trustor", "")
        rec.setdefault("trustee", "")
        rec.setdefault("beneficiary", "")
        rec.setdefault("principalAmount", "")
        rec.setdefault("propertyAddress", rec.get("propertyAddress", "") or "")
        rec.setdefault("imageUrls", "")
        rec.setdefault("ocrMethod", "")
        rec.setdefault("ocrChars", 0)
        rec.setdefault("usedGroq", False)
        rec.setdefault("groqModel", "")
        rec.setdefault("groqError", "")
        rec.setdefault("analysisError", "")

    res: dict[str, Any] = {
        "summary": summary,
        "records": records,
        "cookie": cookie_str,
    }

    if write_output_files:
        ts = _timestamp()
        csv_path = OUTPUT_DIR / f"gila_leads_{ts}.csv"
        json_path = OUTPUT_DIR / f"gila_leads_{ts}.json"
        export_csv(records, csv_path)
        export_json(records, json_path, meta=summary)
        res["csv_path"] = str(csv_path)
        res["json_path"] = str(json_path)

    return res
