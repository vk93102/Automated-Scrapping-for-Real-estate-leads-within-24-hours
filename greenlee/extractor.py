#!/usr/bin/env python3
"""Greenlee County, AZ — Real Estate Lead Scraper & Enrichment Pipeline."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageFile, ImageFilter, ImageEnhance

# Allow PIL to open truncated/incomplete JPEG files from the server
ImageFile.LOAD_TRUNCATED_IMAGES = True

try:
    import pytesseract
    # Explicitly set tesseract binary path (Homebrew on macOS)
    for _tess_bin in [
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
    ]:
        if Path(_tess_bin).exists():
            pytesseract.pytesseract.tesseract_cmd = _tess_bin
            break
except Exception:  # pragma: no cover
    pytesseract = None

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_OK = True
except Exception:  # pragma: no cover
    _PLAYWRIGHT_OK = False


BASE_URL = "https://www.thecountyrecorder.com"
SEARCH_URL = f"{BASE_URL}/Search.aspx"
RESULTS_URL = f"{BASE_URL}/Results.aspx"
DOCUMENT_URL = f"{BASE_URL}/Document.aspx"
IMAGE_HANDLER_URL = f"{BASE_URL}/ImageHandler.ashx"
COUNTY_LABEL = "GREENLEE"
COUNTY_DISPLAY = "Greenlee"

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

STORAGE_STATE_PATH = OUTPUT_DIR / "session_state.json"
ROOT_DIR = Path(__file__).resolve().parent.parent

DEFAULT_DOCUMENT_TYPES = [
    "NOTICE OF DEFAULT",
    "NOTICE OF TRUSTEE SALE",
    "LIS PENDENS",
    "DEED IN LIEU",
    "TREASURERS DEED",
    "NOTICE OF REINSTATEMENT",
]

CSV_FIELDS = [
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
    "imageUrls",
    "ocrMethod",
    "ocrChars",
    "usedGroq",
    "groqModel",
    "groqError",
    "sourceCounty",
    "analysisError",
]


def _normalise_date(date_str: str) -> str:
    s = (date_str or "").strip()
    for fmt in ("%m/%d/%Y", "%-m/%-d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%-m/%-d/%Y")
        except Exception:
            pass
    return s


def _cookie_header_from_cookies(cookies: list[dict]) -> str:
    vals = []
    for c in cookies:
        name = c.get("name", "")
        value = c.get("value", "")
        if name:
            vals.append(f"{name}={value}")
    return "; ".join(vals)


def _load_local_env() -> None:
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                os.environ[k] = v
    except Exception:
        return


def _make_session(cookie_header: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    for pair in cookie_header.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        k, _, v = pair.partition("=")
        s.cookies.set(k.strip(), v.strip(), domain="www.thecountyrecorder.com")
    return s


def _safe_text(node: Any) -> str:
    if not node:
        return ""
    if hasattr(node, "get_text"):
        return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
    return re.sub(r"\s+", " ", str(node)).strip()


def _extract_date(text: str) -> str:
    m = re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text)
    return m.group(0) if m else ""


def _extract_recording_number(text: str) -> str:
    for pat in [r"\b\d{4}-\d{5,}\b", r"\b\d{7,}\b"]:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    return ""


def _extract_value_by_label(soup: BeautifulSoup, labels: list[str]) -> str:
    for label in labels:
        tag = soup.find(string=re.compile(rf"\b{re.escape(label)}\b", re.I))
        if not tag:
            continue
        parent = tag.parent
        if parent:
            next_td = parent.find_next("td")
            if next_td and next_td is not parent:
                v = _safe_text(next_td)
                if v and label.lower() not in v.lower():
                    return v
        nxt = tag.find_next(string=True)
        if nxt:
            v = _safe_text(nxt)
            if v and label.lower() not in v.lower():
                return v
    return ""


def _value_by_id_contains(soup: BeautifulSoup, key: str) -> str:
    node = soup.select_one(f"input[id*='{key}'], textarea[id*='{key}']")
    if not node:
        return ""
    if node.name == "textarea":
        return _safe_text(node)
    return (node.get("value") or "").strip()


def _collect_detail_text(soup: BeautifulSoup) -> str:
    """Collect document-focused text blocks and avoid full-page nav noise."""
    blocks: list[str] = []
    selectors = [
        "table[id*='Table7']",
        "table[id*='DescriptionTable']",
        "table[id*='tableNameIndexingDetails']",
        "table[id*='tableRelatedDocumentDetails']",
        "span[id*='lblViewImage']",
    ]
    for sel in selectors:
        for node in soup.select(sel):
            txt = "\n".join(s.strip() for s in node.stripped_strings if s and s.strip())
            if txt:
                blocks.append(txt)

    if not blocks:
        for node in soup.select("table.Results"):
            txt = "\n".join(s.strip() for s in node.stripped_strings if s and s.strip())
            if txt:
                blocks.append(txt)

    if not blocks:
        return _safe_text(soup)

    uniq = list(dict.fromkeys(blocks))
    return "\n\n".join(uniq)


def _extract_named_rows_by_label(soup: BeautifulSoup, label: str) -> list[str]:
    out: list[str] = []
    target = (label or "").strip().upper()
    for tbl in soup.select("table.Results"):
        vals = [re.sub(r"\s+", " ", s).strip(" ,") for s in tbl.stripped_strings if s and s.strip()]
        if not vals:
            continue
        head = (vals[0] or "").upper()
        if head != target:
            continue
        for v in vals[1:]:
            if not v:
                continue
            if v.upper() in {"SHOW NAME INDEXING DETAILS", "HIDE NAME INDEXING DETAILS"}:
                continue
            out.append(v)
    return out


def _extract_image_like_urls(raw_html: str) -> list[str]:
    """Extract direct/indirect image URLs from detail page HTML/JS blobs."""
    if not raw_html:
        return []
    out: list[str] = []
    seen = set()

    patterns = [
        r"((?:ImageHandler|ViewImage)\.aspx?[^\"'\s)<>]+)",
        r"(ImageHandler\.ashx\?[^\"'\s)<>]+)",
    ]

    for pat in patterns:
        for m in re.finditer(pat, raw_html, re.I):
            cand = (m.group(1) or "").strip()
            if not cand:
                continue
            cand = cand.replace("&amp;", "&")
            full = urllib.parse.urljoin(BASE_URL + "/", cand)
            if full not in seen:
                seen.add(full)
                out.append(full)

    return out


def _select_option_containing(page: Any, sel: str, target: str, timeout: int = 5000) -> bool:
    """Select first option in <select> whose text contains target (case-insensitive)."""
    def _norm(s: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())

    try:
        locator = page.locator(sel).first
        opts = locator.evaluate(
            "el => Array.from(el.options).map(o => ({text: o.text.trim(), value: o.value}))"
        )
        tgt_upper = (target or "").upper()
        tgt_norm = _norm(target)
        tgt_tokens = [t for t in re.split(r"\W+", tgt_upper) if t]

        # Pass 0: exact match (raw or normalized) to prevent accidental partial hits.
        for opt in opts:
            txt = (opt.get("text") or "").strip()
            if not txt or txt.upper() in {"LOADING..."}:
                continue
            txt_up = txt.upper()
            txt_norm = _norm(txt)
            if (tgt_upper and txt_up == tgt_upper) or (tgt_norm and txt_norm == tgt_norm):
                locator.select_option(value=opt["value"], timeout=timeout)
                return True

        # Pass 1: direct contains
        for opt in opts:
            txt = (opt.get("text") or "")
            if tgt_upper and tgt_upper in txt.upper():
                locator.select_option(value=opt["value"], timeout=timeout)
                return True

        # Pass 2: normalized contains (handles apostrophes, extra spaces, punctuation)
        for opt in opts:
            txt = (opt.get("text") or "").strip()
            txt_norm = _norm(txt)
            if not txt_norm or txt.upper() in {"LOADING..."}:
                continue
            if tgt_norm and tgt_norm in txt_norm:
                locator.select_option(value=opt["value"], timeout=timeout)
                return True

        # Pass 3: token-based fuzzy match
        for opt in opts:
            txt_up = (opt.get("text") or "").upper()
            score = sum(1 for t in tgt_tokens if t and t in txt_up)
            if score >= max(2, len(tgt_tokens) - 1):
                locator.select_option(value=opt["value"], timeout=timeout)
                return True
    except Exception:
        pass
    return False


def _doc_type_candidates(doc_type: str) -> list[str]:
    """Return preferred aliases for counties that label document types differently."""
    dt = (doc_type or "").upper().strip()
    aliases: dict[str, list[str]] = {
        "NOTICE OF DEFAULT": [
            "NOTICE OF DEFAULT",
            "NOTICE OF ELECTION",
            "NOTICE OF BREACH",
            "NOD",
        ],
        "NOTICE OF TRUSTEE SALE": ["NOTICE OF TRUSTEE SALE", "TRUSTEE SALE"],
        "NOTICE OF REINSTATEMENT": ["NOTICE OF REINSTATEMENT", "REINSTATEMENT"],
        "LIS PENDENS": ["LIS PENDENS"],
        "DEED IN LIEU": ["DEED IN LIEU"],
        "TREASURERS DEED": ["TREASURERS DEED", "TREASURER'S DEED"],
    }
    return aliases.get(dt, [doc_type])


def parse_results_html(html: str, source_doc_type: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict] = []
    seen = set()
    for a in soup.select("a[href*='Document.aspx?DK=']"):
        href = a.get("href", "")
        m = re.search(r"DK=(\d+)", href)
        if not m:
            continue
        dk = m.group(1)
        if dk in seen:
            continue
        seen.add(dk)
        row = a.find_parent("tr") or a.find_parent("div") or a.parent
        row_text = _safe_text(row)
        rec = {
            "documentId": dk,
            "recordingNumber": _extract_recording_number(row_text),
            "recordingDate": _extract_date(row_text),
            "documentType": source_doc_type or "",
            "grantors": "",
            "grantees": "",
            "trustor": "",
            "trustee": "",
            "beneficiary": "",
            "principalAmount": "",
            "propertyAddress": "",
            "detailUrl": f"{DOCUMENT_URL}?DK={dk}",
            "imageUrls": "",
            "ocrMethod": "",
            "ocrChars": 0,
            "sourceCounty": COUNTY_DISPLAY,
            "analysisError": "",
        }
        if not rec["recordingDate"]:
            tds = row.find_all("td") if row else []
            for td in tds:
                d = _extract_date(_safe_text(td))
                if d:
                    rec["recordingDate"] = d
                    break
        if row:
            tds = row.find_all("td")
            for td in tds:
                tx = _safe_text(td).upper()
                if any(x in tx for x in ("DEED", "LIEN", "TRUSTEE", "PENDENS", "SALE")):
                    rec["documentType"] = tx
                    break
        records.append(rec)
    return records


def _goto_document_search(page: Any, verbose: bool = False) -> None:
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=120_000)
    
    # Step 1: Click Continue on Search.aspx
    for sel in ["#MainContent_Button1", "input[id*='Button1'][value*='Continue']"]:
        if page.locator(sel).count() > 0:
            try:
                page.locator(sel).first.click(timeout=8000)
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
                page.wait_for_timeout(1200)
                break
            except Exception:
                pass
    
    # Step 2: On Default.aspx, select State
    state_sel = "select[id*='cboStates'], select[name*='cboStates']"
    if page.locator(state_sel).count() > 0:
        try:
            page.locator(state_sel).first.select_option(label="ARIZONA")
            if verbose:
                print(f"  Select 'ARIZONA': ok")
        except Exception:
            if verbose:
                print(f"  Select 'ARIZONA': failed")
        page.wait_for_timeout(800)
    
    # Step 3: Select County (auto-navigates to Disclaimer.aspx)
    county_sel = "select[id*='cboCounties'], select[name*='cboCounties']"
    if page.locator(county_sel).count() > 0:
        try:
            page.locator(county_sel).first.select_option(label=COUNTY_LABEL)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
            except Exception:
                pass
            if verbose:
                print(f"  Select '{COUNTY_LABEL}': ok")
        except Exception:
            if verbose:
                print(f"  Select '{COUNTY_LABEL}': failed")
        page.wait_for_timeout(2000)
    
    # Step 4: Accept Disclaimer (on Disclaimer.aspx)
    for sel in ["input[id*='btnAccept']", "#MainContent_searchMainContent_ctl01_btnAccept"]:
        if page.locator(sel).count() > 0:
            try:
                page.locator(sel).first.click(timeout=8000)
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
                page.wait_for_timeout(1500)
                break
            except Exception:
                pass
    
    # Step 5: Navigate to search form (from Introduction.aspx)
    if "Search.aspx" not in (page.url or ""):
        for sel in ["a:has-text('Search Document')", "a#TreeView1t6", "a[href*='Search.aspx']"]:
            if page.locator(sel).count() > 0:
                try:
                    page.locator(sel).first.click(timeout=8000)
                    page.wait_for_load_state("domcontentloaded", timeout=30_000)
                    page.wait_for_timeout(1200)
                    break
                except Exception:
                    pass
    
    # Step 6: Final fallback to ensure we're on Search.aspx
    if "Search.aspx" not in (page.url or ""):
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=120_000)


def _execute_search_for_doc_type(
    page: Any,
    start_date: str,
    end_date: str,
    doc_type: str,
    verbose: bool = False,
) -> bool:
    sd = _normalise_date(start_date)
    ed = _normalise_date(end_date)
    date_ok = False
    for ssel, esel in [
        ("input[id*='tbDateStart']", "input[id*='tbDateEnd']"),
    ]:
        if page.locator(ssel).count() > 0 and page.locator(esel).count() > 0:
            page.locator(ssel).first.fill(sd)
            page.locator(esel).first.fill(ed)
            date_ok = True
            break
    type_ok = False
    group_sel = "select[id*='cboDocumentGroup']"
    type_sel = "select[id*='cboDocumentType']"
    load_types_btn = "input[id*='btnLoadDocumentTypes']"

    candidates = _doc_type_candidates(doc_type)

    # Fast path: on repeated searches, the document type list is often already loaded.
    if page.locator(type_sel).count() > 0:
        try:
            for cand in candidates:
                if _select_option_containing(page, type_sel, cand):
                    type_ok = True
                    break
        except Exception:
            type_ok = False

    if page.locator(group_sel).count() > 0:
        group_map = {
            "Notice": [
                "NOTICE OF DEFAULT",
                "NOTICE OF TRUSTEE SALE",
                "NOTICE OF REINSTATEMENT",
            ],
            "Court": ["LIS PENDENS"],
            "Deed": ["DEED IN LIEU", "TREASURERS DEED", "TRUSTEES DEED", "SHERIFFS DEED"],
            "Lien": ["STATE LIEN", "STATE TAX LIEN"],
        }
        group_order = ["Notice", "Court", "Deed", "Lien", "Release", "Other"]
        selected_group = ""
        for group, types in group_map.items():
            if any(t.lower() in doc_type.lower() or doc_type.lower() in t.lower() for t in types):
                selected_group = group
                break

        # If not mapped, still try common groups.
        groups_to_try = [selected_group] if selected_group else []
        groups_to_try.extend([g for g in group_order if g and g != selected_group])

        for group in groups_to_try:
            if type_ok:
                break
            if not group:
                continue
            if _select_option_containing(page, group_sel, group):
                page.wait_for_timeout(500)
                # Some sessions require explicit "Load" click to populate Document Type options.
                if page.locator(load_types_btn).count() > 0:
                    try:
                        page.locator(load_types_btn).first.click(timeout=8000)
                        page.wait_for_load_state("domcontentloaded", timeout=30_000)
                    except Exception:
                        pass
                # Also trigger onfocus loader used by the site JS, then wait for options.
                try:
                    if page.locator(type_sel).count() > 0:
                        page.locator(type_sel).first.focus()
                except Exception:
                    pass
                page.wait_for_timeout(1200)
                if page.locator(type_sel).count() > 0:
                    # Wait until placeholder "Loading..." is replaced (max ~5s)
                    for _ in range(10):
                        try:
                            opts = page.locator(type_sel).first.evaluate(
                                "el => Array.from(el.options).map(o => (o.text || '').trim())"
                            )
                            has_real = any(o and o.upper() != "LOADING..." for o in opts)
                            if has_real:
                                break
                        except Exception:
                            pass
                        page.wait_for_timeout(500)
                    for cand in candidates:
                        if _select_option_containing(page, type_sel, cand):
                            type_ok = True
                            break
                    # Validate that selected option is really the requested type.
                    if type_ok:
                        try:
                            selected_text = page.locator(type_sel).first.evaluate(
                                "el => (el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '').trim()"
                            )
                            wanted_list = [re.sub(r"[^A-Z0-9]+", "", c.upper()) for c in candidates]
                            got = re.sub(r"[^A-Z0-9]+", "", (selected_text or "").upper())
                            if not got or not any(w and w in got for w in wanted_list):
                                type_ok = False
                        except Exception:
                            type_ok = False
                if type_ok:
                    break
        page.wait_for_timeout(400)
    if verbose:
        type_status = "ok" if type_ok else "unavailable"
        print(f"  Search setup: dates={'ok' if date_ok else 'fail'} type={type_status}")
        if not type_ok and page.locator(type_sel).count() > 0:
            try:
                opts = page.locator(type_sel).first.evaluate(
                    "el => Array.from(el.options).map(o => (o.text || '').trim()).filter(Boolean)"
                )
                sample = [o for o in opts if "NOTICE" in o.upper()][:8]
                if sample:
                    print(f"  Available NOTICE types: {', '.join(sample)}")
            except Exception:
                pass
    if not type_ok:
        return False
    for sel in ["input[id*='btnSearchDocuments']", "input[type='submit'][value*='Execute Search']"]:
        if page.locator(sel).count() > 0:
            try:
                page.locator(sel).first.click(timeout=10_000)
                page.wait_for_load_state("domcontentloaded", timeout=45_000)
                page.wait_for_timeout(1200)
                return True
            except Exception:
                pass
    try:
        page.evaluate("() => { const form = document.querySelector('form'); if (form) form.submit(); }")
        page.wait_for_load_state("domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1200)
        return True
    except Exception:
        return False


def _collect_result_pages(page: Any, max_pages: int = 0, verbose: bool = False) -> list[str]:
    pages: list[str] = []
    page_no = 1
    visited_fingerprints: set[str] = set()
    while True:
        html = page.content()
        fingerprint = re.sub(r"\s+", "", page.url + (html[:1000] if html else ""))[:1200]
        if fingerprint in visited_fingerprints:
            break
        visited_fingerprints.add(fingerprint)
        pages.append(html)
        if verbose:
            print(f"    Collected results page {page_no}")
        if max_pages and page_no >= max_pages:
            break
        moved = False
        for sel in ["a:has-text('Next')", "a[title*='Next']"]:
            if page.locator(sel).count() > 0:
                try:
                    page.locator(sel).first.click(timeout=8000)
                    page.wait_for_load_state("domcontentloaded", timeout=20_000)
                    page.wait_for_timeout(1000)
                    moved = True
                    break
                except Exception:
                    pass
        if not moved:
            break
        page_no += 1
    return pages


def playwright_collect_results(
    start_date: str,
    end_date: str,
    doc_types: list[str],
    max_pages: int = 0,
    headless: bool = True,
    verbose: bool = False,
) -> tuple[str, list[dict]]:
    if not _PLAYWRIGHT_OK:
        raise RuntimeError("playwright not installed")
    all_records: list[dict] = []
    seen = set()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        # Start fresh each run. Reusing stale storage state often causes
        # "County Selection Missing" and breaks document type loading.
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()
        _goto_document_search(page, verbose=verbose)
        for dt in doc_types:
            if verbose:
                print(f"[{COUNTY_DISPLAY.upper()}] Searching doc type: {dt}")
            ok = _execute_search_for_doc_type(page, start_date, end_date, dt, verbose=verbose)
            if not ok:
                # Retry once after reloading search form (handles first-search timing glitches).
                _goto_document_search(page, verbose=False)
                ok = _execute_search_for_doc_type(page, start_date, end_date, dt, verbose=verbose)
            if not ok:
                _goto_document_search(page, verbose=False)
                continue
            html_pages = _collect_result_pages(page, max_pages=max_pages, verbose=verbose)
            for html in html_pages:
                recs = parse_results_html(html, source_doc_type=dt)
                for r in recs:
                    dk = r.get("documentId", "")
                    if dk and dk not in seen:
                        seen.add(dk)
                        all_records.append(r)
            _goto_document_search(page, verbose=False)
        context.storage_state(path=str(STORAGE_STATE_PATH))
        cookie_header = _cookie_header_from_cookies(context.cookies())
        browser.close()
    return cookie_header, all_records


def fetch_detail(dk: str, session: requests.Session, timeout: int = 30) -> dict:
    url = f"{DOCUMENT_URL}?DK={dk}"
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    text = _collect_detail_text(soup)
    detail = {
        "detailUrl": url,
        "recordingNumber": _value_by_id_contains(soup, "tbReceptionNo") or "",
        "recordingDate": _value_by_id_contains(soup, "tbReceptionDate") or "",
        "documentType": _value_by_id_contains(soup, "tbDocumentType") or "",
        "grantors": "",
        "grantees": "",
        "trustor": "",
        "trustee": "",
        "beneficiary": "",
        "principalAmount": "",
        "propertyAddress": "",
        "rawText": text,
        "imageUrls": [],
        "imageAccessNote": "",
    }
    grantor_names = _extract_named_rows_by_label(soup, "Grantor")
    grantee_names = _extract_named_rows_by_label(soup, "Grantee")

    if grantor_names:
        detail["grantors"] = " | ".join(grantor_names[:4])
    if grantee_names:
        detail["grantees"] = " | ".join(grantee_names[:4])

    # Fallback only when labeled blocks are missing.
    if not detail.get("grantors") and not detail.get("grantees"):
        name_rows = soup.select("table[id*='tableNameIndexingDetails'] tr")
        parsed_names: list[str] = []
        for tr in name_rows[1:]:
            tds = tr.find_all("td")
            if not tds:
                continue
            parts = [_safe_text(td) for td in tds[:4]]
            full = " ".join([p for p in parts if p]).strip()
            if full:
                parsed_names.append(full)
        if parsed_names and not detail.get("grantors"):
            detail["grantors"] = " | ".join(parsed_names[:1])
        if len(parsed_names) > 1 and not detail.get("grantees"):
            detail["grantees"] = " | ".join(parsed_names[1:2])
    desc = _value_by_id_contains(soup, "tbDescription")
    if desc:
        detail["rawText"] = (detail["rawText"] + "\n" + desc).strip()
    image_note = _safe_text(soup.select_one("span[id*='lblViewImage']"))
    if image_note:
        detail["imageAccessNote"] = image_note
    if not detail["recordingDate"]:
        detail["recordingDate"] = _extract_date(text)
    if not detail["recordingNumber"]:
        detail["recordingNumber"] = _extract_recording_number(text)
    found = set()
    for tag in soup.select("a[href*='ImageHandler.ashx']"):
        u = tag.get("href") or ""
        if u:
            full = urllib.parse.urljoin(BASE_URL + "/", u)
            if full not in found:
                found.add(full)
                detail["imageUrls"].append(full)

    # Many records expose image links in JS/onclick instead of plain anchors.
    for node in soup.select("*[onclick], a[href], img[src], iframe[src]"):
        for attr in ["onclick", "href", "src"]:
            raw = (node.get(attr) or "").strip()
            if not raw:
                continue
            for full in _extract_image_like_urls(raw):
                if full not in found:
                    found.add(full)
                    detail["imageUrls"].append(full)

    # Last-pass regex scan over full HTML for embedded URL strings.
    for full in _extract_image_like_urls(r.text):
        if full not in found:
            found.add(full)
            detail["imageUrls"].append(full)
    return detail


def discover_image_urls(
    dk: str,
    session: requests.Session,
    detail_image_urls: list[str] | None = None,
    max_probe_pages: int = 6,
) -> list[str]:
    urls: list[str] = []
    seen = set()
    for u in (detail_image_urls or []):
        if u not in seen:
            seen.add(u)
            urls.append(u)

    # Resolve image viewer pages that contain actual ImageHandler URLs.
    viewer_candidates = [
        f"{BASE_URL}/ViewImage.aspx?DK={dk}",
        f"{BASE_URL}/ViewImage.aspx?dk={dk}",
    ]
    for vu in viewer_candidates:
        try:
            rr = session.get(vu, timeout=15, allow_redirects=True)
            if rr.status_code != 200:
                continue
            for full in _extract_image_like_urls(rr.text):
                if full not in seen:
                    seen.add(full)
                    urls.append(full)
        except Exception:
            continue
    misses = 0
    for pn in range(1, max_probe_pages + 1):
        u = f"{IMAGE_HANDLER_URL}?DK={dk}&PN={pn}"
        try:
            head = session.head(u, timeout=10)
            ctype = (head.headers.get("Content-Type") or "").lower()
            cl = int(head.headers.get("Content-Length") or 0)
            if head.status_code == 200 and "image" in ctype and cl > 500:
                if u not in seen:
                    seen.add(u)
                    urls.append(u)
                misses = 0
                continue
        except Exception:
            pass

        # Many county-recorder sessions do not return useful HEAD metadata;
        # fall back to GET validation before declaring the page missing.
        try:
            rr = session.get(u, timeout=15, allow_redirects=True)
            ctype = (rr.headers.get("Content-Type") or "").lower()
            if rr.status_code == 200 and "image" in ctype and len(rr.content) > 500:
                if u not in seen:
                    seen.add(u)
                    urls.append(u)
                misses = 0
                continue
        except Exception:
            pass

        misses += 1
        if misses >= 2 and pn > 1:
            break
    return urls


def _preprocess_for_ocr(im: Image.Image) -> Image.Image:
    """Upscale 2x + sharpen + increase contrast for OCR."""
    im = im.convert("RGB")
    w, h = im.size
    if w < 1200:
        im = im.resize((w * 2, h * 2), Image.LANCZOS)
    im = im.filter(ImageFilter.SHARPEN)
    im = ImageEnhance.Contrast(im).enhance(1.8)
    return im


def _ocr_from_image_bytes(data: bytes) -> str:
    if not data or pytesseract is None:
        return ""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        im_proc = _preprocess_for_ocr(im)
        buf = io.BytesIO()
        im_proc.save(buf, format="PNG")
        buf.seek(0)
        im_clean = Image.open(buf)
        im_clean.load()
        return pytesseract.image_to_string(im_clean, config="--psm 6 --oem 3") or ""
    except Exception:
        return ""


def ocr_document_images(
    image_urls: list[str],
    session: requests.Session,
    timeout: int = 30,
    max_pages: int = 6,
) -> tuple[str, str]:
    texts: list[str] = []
    used = "none"
    for i, u in enumerate(image_urls[:max_pages], 1):
        try:
            rr = session.get(u, timeout=timeout)
            rr.raise_for_status()
            ctype = (rr.headers.get("Content-Type") or "").lower()
            if "image" not in ctype:
                continue
            txt = _ocr_from_image_bytes(rr.content)
            if txt.strip():
                used = "tesseract-image"
                texts.append(f"\n\n--- PAGE {i} ---\n{txt.strip()}")
        except Exception:
            continue
    return "\n".join(texts).strip(), used


def _regex_principal(text: str) -> str:
    def _format_money(raw: str) -> str:
        cleaned = re.sub(r"[^\d.]", "", raw or "")
        if not cleaned:
            return ""
        try:
            val = float(cleaned)
        except Exception:
            return ""
        # Avoid tiny fees/consideration values when extracting principal.
        if val < 1000:
            return ""
        return f"${val:,.2f}"

    pats = [
        r"(?:original\s+principal(?:\s+amount)?|principal\s+balance|unpaid\s+principal(?:\s+balance)?|loan\s+amount|amount\s+of\s+the\s+indebtedness|sum\s+of)[^\d\n]{0,80}(\$?\s*\d[\d,]*(?:\.\d{2})?)",
        r"(?:principal|indebtedness)[^\d\n]{0,40}(\$?\s*\d[\d,]*(?:\.\d{2})?)",
    ]
    for p in pats:
        m = re.search(p, text, re.I | re.S | re.M)
        if m:
            val = _format_money(m.group(1).strip())
            if val:
                return val

    for ln in (text or "").splitlines():
        line = ln.strip()
        if not line:
            continue
        u = line.upper()
        if not any(k in u for k in ["PRINCIPAL", "INDEBTEDNESS", "LOAN AMOUNT", "UNPAID BALANCE"]):
            continue
        amounts = re.findall(r"\$?\s*\d[\d,]*(?:\.\d{2})?", line)
        if not amounts:
            continue
        # Prefer the largest amount on the principal-indicative line.
        best = ""
        best_val = 0.0
        for amt in amounts:
            fm = _format_money(amt)
            if not fm:
                continue
            v = float(re.sub(r"[^\d.]", "", fm))
            if v > best_val:
                best_val = v
                best = fm
        if best:
            return best
    return ""


def _regex_address(text: str) -> str:
    exclude = [
        r"\bDEED OF TRUST\b",
        r"\bLegal Lot Block\b",
        r"Section.*Township",
        r"0\s+Items\s+in\s+Cart",
        r"\bSign In\b",
        r"County,\s*AZ\s*Record",
        r"theCountyRecorder\.com",
        r"County Selection Missing",
        r"Skip Navigation Links",
        r"Requested By",
        r"Recording Fee",
    ]

    def _clean_candidate(val: str) -> str:
        v = re.sub(r"\s+", " ", (val or "")).strip(" ,;:-")
        if len(v) < 8:
            return ""
        if any(re.search(e, v, re.I) for e in exclude):
            return ""
        return v

    # Prefer explicit property labels to avoid capturing party mailing addresses.
    label_pats = [
        r"(?:property\s+address|situs\s+address|premises\s+address|commonly\s+known\s+as|property\s+located\s+at|located\s+at)\s*[:\-]\s*(.+)",
        r"(?:property\s+address|situs\s+address|premises\s+address|commonly\s+known\s+as)\s+(.+)",
    ]
    lines = [ln.strip() for ln in (text or "").splitlines() if ln and ln.strip()]
    for idx, line in enumerate(lines):
        for lp in label_pats:
            m = re.search(lp, line, re.I)
            if not m:
                continue
            cand = m.group(1)
            if idx + 1 < len(lines) and len(cand) < 12:
                cand = f"{cand} {lines[idx + 1]}"
            out = _clean_candidate(cand)
            if out:
                return out

    pats = [
        r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Z0-9][A-Za-z0-9\s.,#\-']{3,90}\b(?:ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|LN|LANE|BLVD|BOULEVARD|CT|COURT|PL|PLACE|WAY|HWY|HIGHWAY|PKWY|PARKWAY|CIR|CIRCLE)\b(?:,\s*[A-Z][A-Za-z .'-]+,\s*AZ(?:\s+\d{5}(?:-\d{4})?)?)?",
    ]
    for p in pats:
        m = re.search(p, text, re.I | re.M)
        if m:
            val = m.group(1) if m.lastindex and m.group(1) else m.group(0)
            out = _clean_candidate(val)
            if out:
                return out
    return ""


def _regex_party(text: str, label: str) -> str:
    lines = [re.sub(r"\s+", " ", ln).strip(" |:;,-") for ln in (text or "").splitlines()]
    stop_terms = [
        "SHOW NAME INDEXING",
        "HIDE NAME INDEXING",
        "UNDER THIS SECURITY INSTRUMENT",
        "TO THE EXTENT OF",
        "REQUESTED BY",
    ]
    label_re = re.compile(rf"\b{re.escape(label)}\b", re.I)
    for i, line in enumerate(lines):
        if not label_re.search(line):
            continue
        cand = label_re.sub("", line).strip(" :-|,")
        if not cand and i + 1 < len(lines):
            cand = lines[i + 1]
        cand = re.sub(r"\s+", " ", cand).strip(" |:;,-")
        if not cand:
            continue
        if any(t in cand.upper() for t in stop_terms):
            continue
        if len(cand) < 4:
            continue
        if not re.search(r"[A-Za-z]", cand):
            continue
        return cand
    return ""


def _extract_party_block(text: str, role: str) -> str:
    if not text:
        return ""
    lines = [re.sub(r"\s+", " ", ln).strip(" |:;,-") for ln in text.splitlines() if ln and ln.strip()]
    label_patterns = [
        rf"name\s+and\s+address\s+of\s+(?:the\s+)?{role}",
        rf"\b{role}\b",
    ]
    stop_patterns = [
        r"name\s+and\s+address\s+of",
        r"recording\s+requested\s+by",
        r"when\s+recorded\s+mail\s+to",
        r"notice\s+of",
        r"apn\b",
    ]
    for i, line in enumerate(lines):
        if not any(re.search(lp, line, re.I) for lp in label_patterns):
            continue
        candidate_parts: list[str] = []
        after = line
        for lp in label_patterns:
            after = re.sub(lp, "", after, flags=re.I)
        after = after.strip(" :-|,")
        if after:
            candidate_parts.append(after)
        for j in range(i + 1, min(i + 4, len(lines))):
            nxt = lines[j]
            if any(re.search(sp, nxt, re.I) for sp in stop_patterns):
                break
            if nxt:
                candidate_parts.append(nxt)
            if len(" ".join(candidate_parts)) > 140:
                break
        cand = re.sub(r"\s+", " ", " ".join(candidate_parts)).strip(" |:;,-")
        if not cand:
            continue
        bad = [
            "UNDER THIS SECURITY INSTRUMENT",
            "TO THE EXTENT OF",
            "SHOW NAME INDEXING",
            "HIDE NAME INDEXING",
            "NAME AND ADDRESS",
        ]
        if any(b in cand.upper() for b in bad):
            continue
        if len(cand) < 5 or not re.search(r"[A-Za-z]", cand):
            continue
        return cand
    return ""


def _groq_request(messages: list[dict[str, str]], api_key: str, timeout_s: int = 60) -> tuple[dict, str]:
    model_candidates = [
        "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile",
        "llama3-70b-8192",
    ]
    last_err = ""
    for model in model_candidates:
        for use_response_format in (True, False):
            body = {
                "model": model,
                "temperature": 0,
                "messages": messages,
            }
            if use_response_format:
                body["response_format"] = {"type": "json_object"}
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=timeout_s,
                )
                resp.raise_for_status()
                payload = resp.json()
                content = (
                    payload.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip(), flags=re.I)
                data = json.loads(content) if content else {}
                if isinstance(data, dict):
                    return data, model
                last_err = "invalid JSON object"
            except Exception as exc:
                last_err = str(exc)
                continue
    raise RuntimeError(last_err or "Groq request failed")


def _normalise_party(v: str) -> str:
    return re.sub(r"\s+", " ", (v or "")).strip(" |:;,-")


def _first_party(parties: str) -> str:
    vals = [
        _normalise_party(x)
        for x in str(parties or "").split("|")
        if _normalise_party(x)
    ]
    return vals[0] if vals else ""


def _looks_bad_party(v: str) -> bool:
    u = (v or "").upper()
    if not u:
        return True
    bad = [
        "UNDER THIS SECURITY INSTRUMENT",
        "TO THE EXTENT OF",
        "SHOW NAME INDEXING",
        "HIDE NAME INDEXING",
        "NAME AND ADDRESS",
    ]
    return any(b in u for b in bad)


def _groq_extract_fields(
    *,
    document_id: str,
    recording_number: str,
    document_type: str,
    ocr_text: str,
    detail_text: str,
    api_key: str,
) -> tuple[dict, str]:
    system_prompt = (
        "Extract recorder document fields from OCR text. Return STRICT JSON with keys: "
        "trustor, trustee, beneficiary, principalAmount, propertyAddress, grantors, grantees, confidenceNote. "
        "grantors and grantees must be arrays of names. Do not invent values. "
        "If unknown, return empty string/empty array. principalAmount must be a dollar string like $123,456.78 when present."
    )
    user_payload = {
        "documentId": document_id,
        "recordingNumber": recording_number,
        "documentType": document_type,
        "ocrText": (ocr_text or "")[:6000],
        "detailText": (detail_text or "")[:2500],
    }
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    data, model = _groq_request(messages, api_key=api_key, timeout_s=70)
    return data, model


def enrich_record(
    record: dict,
    session: requests.Session,
    use_groq: bool = True,
    groq_api_key: str = "",
    max_image_pages: int = 6,
) -> dict:
    dk = record.get("documentId", "")
    if not dk:
        return record
    try:
        detail = fetch_detail(dk, session)
    except Exception as e:
        record["analysisError"] = f"detail fetch failed: {e}"
        return record
    for key in ["detailUrl", "recordingNumber", "recordingDate", "documentType", "grantors", "grantees"]:
        if detail.get(key):
            record[key] = detail[key]
    image_urls = discover_image_urls(dk, session, detail.get("imageUrls", []), max_probe_pages=max_image_pages)
    record["imageUrls"] = " | ".join(image_urls)
    ocr_text, ocr_method = ocr_document_images(image_urls, session, max_pages=max_image_pages)
    blocked_no_image = False
    if not image_urls and detail.get("imageAccessNote"):
        note = detail.get("imageAccessNote", "")
        note_l = note.lower()
        record["analysisError"] = note
        blocked_no_image = True
        if "unofficial images" in note_l:
            ocr_method = "unavailable-county-blocked"
        elif "not perfected" in note_l:
            ocr_method = "unavailable-not-perfected"
        else:
            ocr_method = "unavailable-no-images"
    record["ocrMethod"] = ocr_method
    record["ocrChars"] = len(ocr_text)
    record.setdefault("usedGroq", False)
    record.setdefault("groqModel", "")
    record.setdefault("groqError", "")
    merged = (ocr_text + "\n" + detail.get("rawText", "")).strip()

    if use_groq and groq_api_key and not blocked_no_image and (ocr_text.strip() or detail.get("rawText", "").strip()):
        try:
            llm, model = _groq_extract_fields(
                document_id=dk,
                recording_number=record.get("recordingNumber", ""),
                document_type=record.get("documentType", ""),
                ocr_text=ocr_text,
                detail_text=detail.get("rawText", ""),
                api_key=groq_api_key,
            )
            record["usedGroq"] = True
            record["groqModel"] = model

            # LLM-first mapping (no regex dependency when LLM is enabled).
            for key in ["trustor", "trustee", "beneficiary", "propertyAddress", "principalAmount"]:
                llm_val = (llm.get(key) or "").strip()
                if llm_val:
                    record[key] = llm_val

            llm_grantors = llm.get("grantors") or []
            llm_grantees = llm.get("grantees") or []
            if isinstance(llm_grantors, list) and llm_grantors:
                record["grantors"] = " | ".join(_normalise_party(x) for x in llm_grantors if str(x).strip())
            if isinstance(llm_grantees, list) and llm_grantees:
                record["grantees"] = " | ".join(_normalise_party(x) for x in llm_grantees if str(x).strip())

            # IMPORTANT: run deterministic fallback for fields still empty after LLM.
            if not record.get("principalAmount"):
                record["principalAmount"] = _regex_principal(merged)
            if not record.get("propertyAddress"):
                record["propertyAddress"] = _regex_address(merged)
            for label, key in [("trustor", "trustor"), ("trustee", "trustee"), ("beneficiary", "beneficiary")]:
                if not record.get(key):
                    record[key] = _extract_party_block(ocr_text, label)
                if not record.get(key):
                    record[key] = _extract_party_block(merged, label)
                if not record.get(key):
                    record[key] = _regex_party(merged, label)

            # Final practical fallback from indexed parties when document text is sparse.
            if not record.get("trustor"):
                record["trustor"] = _first_party(record.get("grantors", ""))
            if not record.get("beneficiary"):
                record["beneficiary"] = _first_party(record.get("grantees", ""))
            if not record.get("trustee"):
                gr_first = _first_party(record.get("grantees", ""))
                if re.search(r"TRUST|TRUSTEE", gr_first or "", re.I):
                    record["trustee"] = gr_first
        except Exception as e:
            record["groqError"] = str(e)
            # If LLM call fails, still execute deterministic extraction.
            if not record.get("principalAmount"):
                record["principalAmount"] = _regex_principal(merged)
            if not record.get("propertyAddress"):
                record["propertyAddress"] = _regex_address(merged)
            for label, key in [("trustor", "trustor"), ("trustee", "trustee"), ("beneficiary", "beneficiary")]:
                if not record.get(key):
                    record[key] = _extract_party_block(ocr_text, label)
                if not record.get(key):
                    record[key] = _extract_party_block(merged, label)
                if not record.get(key):
                    record[key] = _regex_party(merged, label)
            if not record.get("trustor"):
                record["trustor"] = _first_party(record.get("grantors", ""))
            if not record.get("beneficiary"):
                record["beneficiary"] = _first_party(record.get("grantees", ""))
    else:
        # Regex fallback path only when LLM is unavailable/disabled.
        if not record.get("principalAmount"):
            record["principalAmount"] = _regex_principal(merged)
        if not record.get("propertyAddress"):
            record["propertyAddress"] = _regex_address(merged)
        for label, key in [("trustor", "trustor"), ("trustee", "trustee"), ("beneficiary", "beneficiary")]:
            if not record.get(key):
                record[key] = _extract_party_block(ocr_text, label)
            if not record.get(key):
                record[key] = _extract_party_block(merged, label)
            if not record.get(key):
                record[key] = _regex_party(merged, label)
        if not record.get("trustor"):
            record["trustor"] = _first_party(record.get("grantors", ""))
        if not record.get("beneficiary"):
            record["beneficiary"] = _first_party(record.get("grantees", ""))
    return record


def export_csv(records: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def export_json(records: list[dict], out_path: Path, meta: dict | None = None) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": meta or {},
        "count": len(records),
        "records": [{k: r.get(k, "") for k in CSV_FIELDS} for r in records],
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_greenlee_pipeline(
    start_date: str,
    end_date: str,
    doc_types: list[str] | None = None,
    max_pages: int = 0,
    ocr_limit: int = 10,
    workers: int = 3,
    use_groq: bool = True,
    headless: bool = True,
    verbose: bool = False,
    write_output_files: bool | None = None,
) -> dict:
    doc_types = doc_types or DEFAULT_DOCUMENT_TYPES
    cookie_header, records = playwright_collect_results(
        start_date=start_date,
        end_date=end_date,
        doc_types=doc_types,
        max_pages=max_pages,
        headless=headless,
        verbose=verbose,
    )
    _load_local_env()
    session = _make_session(cookie_header)
    groq_key = os.getenv("GROQ_API_KEY", "")
    use_groq = bool(use_groq and groq_key)
    if ocr_limit < 0:
        enrich_count = 0
    elif ocr_limit == 0:
        enrich_count = len(records)
    else:
        enrich_count = min(ocr_limit, len(records))

    # Run OCR/enrichment in parallel workers (default: 3).
    if enrich_count > 0:
        max_workers = max(1, int(workers or 1))

        def _enrich_one(idx: int) -> tuple[int, dict]:
            rec = records[idx]
            if verbose:
                print(f"[ENRICH] {idx + 1}/{len(records)} DK={rec.get('documentId','')}")
            # Use one requests session per worker task to avoid cross-thread session mutation.
            local_session = _make_session(cookie_header)
            out = enrich_record(rec, local_session, use_groq=use_groq, groq_api_key=groq_key)
            return idx, out

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_enrich_one, i) for i in range(enrich_count)]
            for fut in as_completed(futures):
                idx, out = fut.result()
                records[idx] = out

    for i, rec in enumerate(records, 1):
        if i <= enrich_count:
            continue
        try:
            detail = fetch_detail(rec.get("documentId", ""), session)
            for key in ["detailUrl", "recordingNumber", "recordingDate", "documentType", "grantors", "grantees"]:
                if detail.get(key):
                    rec[key] = detail[key]
        except Exception as e:
            rec["analysisError"] = f"detail fetch failed: {e}"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / f"greenlee_leads_{ts}.csv"
    json_path = OUTPUT_DIR / f"greenlee_leads_{ts}.json"
    meta = {
        "county": "Greenlee County, AZ",
        "platform": "TheCountyRecorder (ASP.NET WebForms)",
        "baseUrl": BASE_URL,
        "startDate": _normalise_date(start_date),
        "endDate": _normalise_date(end_date),
        "documentTypes": doc_types,
        "recordsFound": len(records),
        "recordsOCR": enrich_count,
        "workers": max(1, int(workers or 1)),
        "usedGroq": use_groq,
        "timestamp": datetime.now().isoformat(),
    }

    if write_output_files is None:
        write_output_files = os.getenv("WRITE_OUTPUT_FILES", "true").strip().lower() == "true"

    if write_output_files:
        export_csv(records, csv_path)
        export_json(records, json_path, meta=meta)
    else:
        csv_path = Path("")
        json_path = Path("")

    return {
        "records": records,
        "csv_path": str(csv_path),
        "json_path": str(json_path),
        "summary": meta,
    }


# Backward-compat alias
run_lapaz_pipeline = run_greenlee_pipeline
