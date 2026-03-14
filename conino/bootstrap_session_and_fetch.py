from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://eagleassessor.coconino.az.gov:8444"
SEARCH_ID = "DOCSEARCH1213S1"
SEARCH_URL = f"{BASE_URL}/web/search/{SEARCH_ID}"
SEARCH_POST_URL = f"{BASE_URL}/web/searchPost/{SEARCH_ID}"
SEARCH_RESULTS_URL = f"{BASE_URL}/web/searchResults/{SEARCH_ID}"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
STATE_FILE = OUTPUT_DIR / "session_state.json"


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _headers(referer: str, ajax: bool = False) -> dict[str, str]:
    """Build request headers WITHOUT a Cookie field — requests.Session() manages the cookie jar."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
        "Referer": referer,
        "Connection": "keep-alive",
    }
    if ajax:
        headers.update({"Accept": "*/*", "X-Requested-With": "XMLHttpRequest", "ajaxrequest": "true"})
    else:
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    return headers


def bootstrap_with_playwright_manual(timeout_seconds: int = 180) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("Playwright is required. Install: python3 -m pip install playwright && python3 -m playwright install chromium") from exc

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(storage_state=str(STATE_FILE) if STATE_FILE.exists() else None)
        page = context.new_page()
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=120000)

        # Try clicking accept if enabled; if not, user may need to solve challenge first.
        try:
            if page.locator("#submitDisclaimerAccept").count() > 0:
                btn = page.locator("#submitDisclaimerAccept").first
                if btn.is_enabled():
                    btn.click(timeout=5000)
        except Exception:
            pass

        # Wait for user to complete any interactive checks and land on search page.
        while time.time() - started < timeout_seconds:
            url = page.url
            if "/web/search/" in url:
                break
            time.sleep(1)

        final_url = page.url
        html = page.content()
        cookies = context.cookies()
        context.storage_state(path=str(STATE_FILE))
        browser.close()

    cookie_header = "; ".join(
        f"{c.get('name','')}={c.get('value','')}"
        for c in cookies
        if c.get("name")
    )
    token_payload = {
        "finalUrl": final_url,
        "accepted": "/web/search/" in final_url,
        "cookieNames": sorted([str(c.get("name", "")) for c in cookies if c.get("name")]),
        "jsessionid": next((c.get("value") for c in cookies if c.get("name") == "JSESSIONID"), ""),
        "cf_clearance": next((c.get("value") for c in cookies if c.get("name") == "cf_clearance"), ""),
        "disclaimerAccepted": next((c.get("value") for c in cookies if c.get("name") == "disclaimerAccepted"), ""),
        "stateFile": str(STATE_FILE),
    }

    _save_text(OUTPUT_DIR / "manual_bootstrap_landing.html", html)
    _save_text(OUTPUT_DIR / "manual_bootstrap_tokens.json", json.dumps(token_payload, indent=2, ensure_ascii=False))

    return {"cookieHeader": cookie_header, "rawCookies": cookies, **token_payload}


def run_requests_session_flow(
    cookie_header: str,
    start_date: str,
    end_date: str,
    raw_cookies: list[dict] | None = None,
) -> dict[str, Any]:
    if not cookie_header.strip():
        raise RuntimeError("cookie header is empty after bootstrap")

    session = requests.Session()

    # Pre-seed the session cookie jar with every cookie Playwright collected.
    # This avoids manually setting a Cookie header (which would override the jar
    # and send stale values after the server issues a fresh JSESSIONID on step 1).
    for c in (raw_cookies or []):
        name = c.get("name", "")
        value = c.get("value", "")
        domain = c.get("domain", "").lstrip(".")
        path = c.get("path", "/")
        if name:
            session.cookies.set(name, value, domain=domain, path=path)

    # Step 1: Search page GET — server may issue a fresh JSESSIONID; session stores it automatically.
    r1 = session.get(SEARCH_URL, headers=_headers(referer=SEARCH_URL), timeout=60)
    r1.raise_for_status()
    _save_text(OUTPUT_DIR / "requests_search_get.html", r1.text)

    # Step 2: searchPost POST in same Session
    # Payload exactly mirrors the browser network request:
    #   - document types go into the dynamically-appended "-searchInput" hidden fields
    #   - every other form field is sent as empty (required by server-side validation)
    #   - "-containsInput" fields carry the operator value ("Contains Any")
    #   - the bare "field_selfservice_documentTypes" is sent empty at the end (the autocomplete text box)
    payload: list[tuple[str, str]] = [
        ("field_DocNum", ""),
        ("field_rdate_DOT_StartDate", start_date),
        ("field_rdate_DOT_EndDate", end_date),
        ("field_BothID-containsInput", "Contains Any"),
        ("field_BothID", ""),
        ("field_BookPageID_DOT_Book", ""),
        ("field_BookPageID_DOT_Page", ""),
        ("field_PlattedID_DOT_Subdivision-containsInput", "Contains Any"),
        ("field_PlattedID_DOT_Subdivision", ""),
        ("field_PlattedID_DOT_Lot", ""),
        ("field_PlattedID_DOT_Block", ""),
        ("field_PlattedID_DOT_Tract", ""),
        ("field_LegalCompID_DOT_QuarterSection-containsInput", "Contains Any"),
        ("field_LegalCompID_DOT_QuarterSection", ""),
        ("field_LegalCompID_DOT_Section", ""),
        ("field_LegalCompID_DOT_Township", ""),
        ("field_LegalCompID_DOT_Range", ""),
        # Document types: each selected item is posted as a separate "-searchInput" entry
        ("field_selfservice_documentTypes-searchInput", "LIS PENDENS"),
        ("field_selfservice_documentTypes-searchInput", "TRUSTEES DEED"),
        ("field_selfservice_documentTypes-searchInput", "SHERIFFS DEED"),
        ("field_selfservice_documentTypes-searchInput", "TREASURERS DEED"),
        ("field_selfservice_documentTypes-searchInput", "STATE LIEN"),
        ("field_selfservice_documentTypes-searchInput", "STATE TAX LIEN"),
        ("field_selfservice_documentTypes-searchInput", "RELEASE STATE TAX LIEN"),
        ("field_selfservice_documentTypes-containsInput", "Contains Any"),
        ("field_selfservice_documentTypes", ""),   # autocomplete text box — always empty on submit
    ]
    # POST needs AJAX headers so the server returns JSON ({totalPages, currentPage, validationMessages})
    # instead of the full HTML page. Also no Cookie header — the session jar sends the right cookies.
    headers_post = _headers(referer=SEARCH_URL, ajax=True)
    headers_post["Content-Type"] = "application/x-www-form-urlencoded"
    r2 = session.post(SEARCH_POST_URL, data=payload, headers=headers_post, timeout=90)
    r2.raise_for_status()
    _save_text(OUTPUT_DIR / "requests_search_post.json", r2.text)

    # Step 3: results GET in same Session
    ts = int(time.time() * 1000)
    results_url = f"{SEARCH_RESULTS_URL}?page=1&_={ts}"
    r3 = session.get(results_url, headers=_headers(referer=SEARCH_URL, ajax=True), timeout=90)
    r3.raise_for_status()
    _save_text(OUTPUT_DIR / "requests_search_results_page1.html", r3.text)

    return {
        "ok": True,
        "resultsUrl": results_url,
        "postResponsePreview": r2.text[:500],
        "resultsHtmlLength": len(r3.text),
        "saved": {
            "searchGetHtml": str((OUTPUT_DIR / "requests_search_get.html")),
            "searchPostJson": str((OUTPUT_DIR / "requests_search_post.json")),
            "searchResultsHtml": str((OUTPUT_DIR / "requests_search_results_page1.html")),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="12/13/2025")
    parser.add_argument("--end-date", default="3/13/2026")
    parser.add_argument("--manual-timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    bootstrap = bootstrap_with_playwright_manual(timeout_seconds=args.manual_timeout_seconds)
    flow = run_requests_session_flow(
        cookie_header=bootstrap.get("cookieHeader", ""),
        start_date=args.start_date,
        end_date=args.end_date,
        raw_cookies=bootstrap.get("rawCookies"),
    )

    out = {"bootstrap": bootstrap, "requestsFlow": flow}
    _save_text(OUTPUT_DIR / "manual_bootstrap_run_report.json", json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
