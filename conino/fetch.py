from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


URL = "https://eagleassessor.coconino.az.gov:8444/web/search/DOCSEARCH1213S1"
SEARCH_POST_URL = "https://eagleassessor.coconino.az.gov:8444/web/searchPost/DOCSEARCH1213S1"
SEARCH_RESULTS_URL = os.environ.get(
    "COCONINO_RESULTS_URL",
    "https://eagleassessor.coconino.az.gov:8444/web/searchResults/DOCSEARCH1213S1?page=1",
)
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def looks_like_login_or_block(html: str) -> str:
    text = (html or "").lower()
    if "public user login" in text or "registered user login" in text:
        return "login-page"
    if "access denied" in text or "forbidden" in text:
        return "blocked"
    if "search results" in text or "account" in text or "parcel" in text:
        return "meaningful-page"
    return "unknown"


def save_response(prefix: str, request_url: str, response: object, body: str) -> dict:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = OUTPUT_DIR / f"{prefix}_{timestamp}.html"
    meta_path = OUTPUT_DIR / f"{prefix}_{timestamp}.json"

    html_path.write_text(body, encoding="utf-8")
    meta = {
        "requestUrl": request_url,
        "finalUrl": response.geturl(),
        "statusCode": response.getcode(),
        "contentType": response.headers.get("content-type", ""),
        "contentLength": len(body or ""),
        "title": extract_title(body),
        "pageKind": looks_like_login_or_block(body),
        "savedHtml": str(html_path),
        "savedAt": timestamp,
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Referer": URL,
    "Connection": "keep-alive",
}

results_headers = {
    "User-Agent": os.environ.get(
        "COCONINO_USER_AGENT",
        "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
    ),
    "Accept": "*/*",
    "Referer": URL,
    "Connection": "keep-alive",
    "X-Requested-With": "XMLHttpRequest",
    "ajaxrequest": "true",
}

cookie = os.environ.get("COCONINO_COOKIE", "").strip()
if cookie:
    headers["Cookie"] = cookie
    results_headers["Cookie"] = cookie

try:
    opener = build_opener(HTTPCookieProcessor())
    request = Request(URL, headers=headers)
    response = opener.open(request, timeout=20)
    body = response.read().decode("utf-8", errors="ignore")
    meta = save_response("search_page", URL, response, body)
    print(json.dumps(meta, indent=2))

    results_request = Request(SEARCH_RESULTS_URL, headers=results_headers)
    results_response = opener.open(results_request, timeout=30)
    results_body = results_response.read().decode("utf-8", errors="ignore")
    results_meta = save_response("search_results_ajax", SEARCH_RESULTS_URL, results_response, results_body)
    print(json.dumps({"searchResults": results_meta}, indent=2))

    search_payload_raw = os.environ.get("COCONINO_SEARCH_PAYLOAD", "").strip()
    if search_payload_raw:
        payload = json.loads(search_payload_raw)
        if not isinstance(payload, dict):
            raise ValueError("COCONINO_SEARCH_PAYLOAD must be a JSON object")
        encoded = urlencode({str(k): "" if v is None else str(v) for k, v in payload.items()}).encode("utf-8")
        post_headers = dict(headers)
        post_headers["Content-Type"] = "application/x-www-form-urlencoded"
        post_headers["Referer"] = URL
        post_request = Request(SEARCH_POST_URL, data=encoded, headers=post_headers, method="POST")
        post_response = opener.open(post_request, timeout=30)
        post_body = post_response.read().decode("utf-8", errors="ignore")
        result_meta = save_response("search_post_results", SEARCH_POST_URL, post_response, post_body)
        print(json.dumps({"searchPost": result_meta}, indent=2))
except Exception as exc:
    print(json.dumps({"ok": False, "error": str(exc)}, indent=2))