from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from extractor import (
    default_last_three_month_range,
    enrich_records_with_detail_fields,
    export_csv,
    fetch_document_ocr_and_analysis,
    parse_search_results_html,
)

BASE_URL = "https://eagleassessor.coconino.az.gov:8444/web/search/DOCSEARCH1213S1"
ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = OUTPUT_DIR / "session_state.json"


def _cookie_header_from_context(context: Any) -> str:
    cookies = context.cookies()
    if not cookies:
        return ""
    return "; ".join(f"{cookie.get('name','')}={cookie.get('value','')}" for cookie in cookies if cookie.get("name"))


def _parse_cookie_header(cookie_header: str) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    for part in (cookie_header or "").split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": "eagleassessor.coconino.az.gov",
                "path": "/",
                "httpOnly": False,
                "secure": True,
            }
        )
    return cookies


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _click_if_visible(page: Any, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() > 0 and locator.first.is_visible():
                locator.first.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


DEFAULT_DOCUMENT_TYPES = [
    "LIS PENDENS",
    "TRUSTEES DEED",
    "SHERIFFS DEED",
    "TREASURERS DEED",
    "STATE LIEN",
    "STATE TAX LIEN",
    "RELEASE STATE TAX LIEN",
]


def run_automation(
    start_date: str,
    end_date: str,
    csv_name: str,
    json_name: str,
    detail_max_records: int,
    ocr_principal_limit: int,
    headless: bool,
    use_env_cookie: bool = True,
    document_types: list[str] | None = None,
) -> dict[str, Any]:
    if document_types is None:
        document_types = DEFAULT_DOCUMENT_TYPES
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("Playwright is required. Install with: python3 -m pip install playwright && python3 -m playwright install chromium") from exc

    html_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_filename = f"playwright_results_{html_timestamp}.html"
    html_path = OUTPUT_DIR / html_filename

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)

        if STATE_FILE.exists():
            context = browser.new_context(storage_state=str(STATE_FILE))
            session_mode = "reused"
        else:
            context = browser.new_context()
            session_mode = "new"

        env_cookie = os.environ.get("COCONINO_COOKIE", "").strip() if use_env_cookie else ""
        if env_cookie:
            parsed = _parse_cookie_header(env_cookie)
            if parsed:
                context.add_cookies(parsed)

        page = context.new_page()
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=120000)

        _click_if_visible(
            page,
            [
                "text=I Accept",
                "text=Accept",
                "button:has-text('Accept')",
                "a:has-text('Accept')",
                "input[value='Accept']",
            ],
        )

        page.wait_for_timeout(1500)

        # Fill date range if fields are present
        if page.locator("#field_rdate_DOT_StartDate").count() > 0:
            page.fill("#field_rdate_DOT_StartDate", start_date)
        if page.locator("#field_rdate_DOT_EndDate").count() > 0:
            page.fill("#field_rdate_DOT_EndDate", end_date)

        # Inject document type filters via JS hidden inputs (mirrors what the autocomplete widget does).
        # The server reads repeated "field_selfservice_documentTypes-searchInput" entries.
        if document_types:
            js_types = json.dumps(document_types)
            page.evaluate(f"""
                (types) => {{
                    const form = document.querySelector('form');
                    if (!form) return;
                    // Remove any previously injected hidden inputs
                    form.querySelectorAll('input[data-injected-doctype]').forEach(el => el.remove());
                    types.forEach(t => {{
                        const h = document.createElement('input');
                        h.type = 'hidden';
                        h.name = 'field_selfservice_documentTypes-searchInput';
                        h.value = t;
                        h.setAttribute('data-injected-doctype', '1');
                        form.appendChild(h);
                    }});
                    // Also inject the -containsInput operator hidden field
                    const op = document.createElement('input');
                    op.type = 'hidden';
                    op.name = 'field_selfservice_documentTypes-containsInput';
                    op.value = 'Contains Any';
                    op.setAttribute('data-injected-doctype', '1');
                    form.appendChild(op);
                }}
            """, js_types)

        # Trigger search button when present
        if page.locator("#searchButton").count() > 0:
            page.click("#searchButton", timeout=20000)
        elif page.locator("a:has-text('Search')").count() > 0:
            page.locator("a:has-text('Search')").first.click(timeout=20000)

        html_content = ""
        try:
            page.wait_for_selector("li.ss-search-row", timeout=120000)
            page.wait_for_timeout(1000)
            html_content = page.content()
        except Exception:
            ajax_headers = {
                "Accept": "*/*",
                "Referer": BASE_URL,
                "X-Requested-With": "XMLHttpRequest",
                "ajaxrequest": "true",
                "User-Agent": "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
            }
            response = context.request.get(
                "https://eagleassessor.coconino.az.gov:8444/web/searchResults/DOCSEARCH1213S1?page=1",
                headers=ajax_headers,
                timeout=120000,
            )
            if not response.ok:
                raise RuntimeError(f"searchResults fallback failed with status {response.status}")
            html_content = response.text()

        page.wait_for_timeout(1000)

        _save_text(html_path, html_content)

        context.storage_state(path=str(STATE_FILE))
        cookie_header = _cookie_header_from_context(context)

        browser.close()

    parsed = parse_search_results_html(html_content, source_file=html_filename)
    records = list(parsed.get("records", []))

    max_records = None if detail_max_records <= 0 else detail_max_records
    records = enrich_records_with_detail_fields(records, cookie=cookie_header or None, max_records=max_records)

    if (cookie_header or "").strip() and ocr_principal_limit > 0:
        processed = 0
        for record in records:
            if processed >= ocr_principal_limit:
                break
            needs_principal = not str(record.get("principalAmount", "")).strip()
            needs_address = not str(record.get("propertyAddress", "")).strip()
            if not (needs_principal or needs_address):
                continue
            try:
                analysis = fetch_document_ocr_and_analysis(
                    document_id=str(record.get("documentId", "")),
                    recording_number=str(record.get("recordingNumber", "")),
                    index=1,
                    document_type=str(record.get("documentType", "")),
                    cookie=cookie_header,
                    use_groq=False,
                )
                if needs_principal:
                    principal_candidates = analysis.get("principalCandidates") or []
                    if principal_candidates:
                        record["principalAmount"] = principal_candidates[0]
                if needs_address:
                    address_candidates = analysis.get("addressCandidates") or []
                    if address_candidates:
                        record["propertyAddress"] = address_candidates[0]
                record["documentAnalysis"] = analysis
                processed += 1
            except Exception as exc:
                record["documentAnalysisError"] = str(exc)

    csv_path = export_csv(records, csv_name=csv_name)

    non_empty_address = sum(1 for r in records if str(r.get("propertyAddress", "")).strip())
    non_empty_principal = sum(1 for r in records if str(r.get("principalAmount", "")).strip())

    result = {
        "ok": True,
        "mode": "playwright-session-persistent",
        "sessionMode": session_mode,
        "stateFile": str(STATE_FILE),
        "baseUrl": BASE_URL,
        "requestedStartDate": start_date,
        "requestedEndDate": end_date,
        "htmlFile": html_filename,
        "htmlPath": str(html_path),
        "summary": parsed.get("summary", {}),
        "recordCount": len(records),
        "nonEmptyPropertyAddress": non_empty_address,
        "nonEmptyPrincipalAmount": non_empty_principal,
        "csvFile": csv_path.name,
        "csvPath": str(csv_path),
        "detailMaxRecords": detail_max_records,
        "ocrPrincipalLimit": ocr_principal_limit,
        "usedEnvCookieInjection": bool(use_env_cookie and os.environ.get("COCONINO_COOKIE", "").strip()),
    }

    json_path = OUTPUT_DIR / json_name
    _save_text(json_path, json.dumps(result, indent=2, ensure_ascii=False))
    result["jsonPath"] = str(json_path)

    return result


def main() -> None:
    default_start, default_end = default_last_three_month_range()

    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default=default_start)
    parser.add_argument("--end-date", default=default_end)
    parser.add_argument("--csv-name", default="coconino_realtime.csv")
    parser.add_argument("--json-name", default="coconino_realtime.json")
    parser.add_argument("--detail-max-records", type=int, default=100)
    parser.add_argument("--ocr-principal-limit", type=int, default=10)
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--no-env-cookie", action="store_true")
    parser.add_argument(
        "--document-types",
        nargs="+",
        default=None,
        metavar="TYPE",
        help="Document types to filter on (default: LIS PENDENS, TRUSTEES DEED, etc.)",
    )
    args = parser.parse_args()

    result = run_automation(
        start_date=args.start_date,
        end_date=args.end_date,
        csv_name=args.csv_name,
        json_name=args.json_name,
        detail_max_records=args.detail_max_records,
        ocr_principal_limit=args.ocr_principal_limit,
        headless=not args.headful,
        use_env_cookie=not args.no_env_cookie,
        document_types=args.document_types,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
