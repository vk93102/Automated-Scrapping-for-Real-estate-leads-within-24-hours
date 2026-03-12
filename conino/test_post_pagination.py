#!/usr/bin/env python3
"""
Try POST-based pagination instead of GET.
"""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import ssl
import urllib.parse

BASE_URL = "https://eagleassessor.coconino.az.gov:8444"
SEARCH_ID = "DOCSEARCH1213S1"
RESULTS_ENDPOINT = f"{BASE_URL}/web/searchResults/{SEARCH_ID}"

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-GB,en;q=0.9",
    "Cache-Control": "max-age=0, no-cache",
    "Connection": "keep-alive",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": f"{BASE_URL}/web/search/{SEARCH_ID}",
    "Sec-Ch-Ua": '"Not:A-Brand";v="99", "Brave";v="145", "Chromium";v="145"',
    "Sec-Ch-Ua-Mobile": "?1",
    "Sec-Ch-Ua-Platform": '"Android"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Gpc": "1",
    "User-Agent": "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "ajaxrequest": "true",
}

def get_record_count(html: str) -> int:
    """Extract record count from HTML."""
    return html.count('<li class="ss-search-row">')


def test_post_pagination(cookie_str: str) -> dict:
    """Test POST-based pagination with various payload formats."""
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    summary = {"attempts": []}

    # Try different POST payload formats
    payloads = [
        {"name": "page_param_in_body", "data": "page=2"},
        {"name": "pageNum_param", "data": "pageNum=2"},
        {"name": "pageIndex_param", "data": "pageIndex=2"},
        {"name": "startIndex_param", "data": "startIndex=100"},  # If pagination is 100-at-a-time
        {"name": "offset_param", "data": "offset=100"},
        {"name": "empty_body", "data": ""},
    ]

    # Also try as query parameters with POST
    query_params = [
        ("?page=2", "empty"),
        ("?pageNum=2", "empty"),
        ("?pageIndex=2", "empty"),
    ]

    # Test with query params and GET first
    for query, payload_type in query_params:
        url = RESULTS_ENDPOINT + query
        print(f"\nTesting GET {query}...")
        try:
            req = Request(url)
            for key, value in HEADERS.items():
                req.add_header(key, value)
            req.add_header("Cookie", cookie_str)
            
            with urlopen(req, context=ssl_context, timeout=30) as response:
                html = response.read().decode("utf-8", errors="ignore")
                records = get_record_count(html)
                summary["attempts"].append({
                    "method": "GET",
                    "url": query,
                    "status": response.status,
                    "records": records
                })
                print(f"  ✓ Status {response.status}, {records} records")
                if records > 50:  # If we got good results, save it
                    path = OUTPUT_DIR / f"page_post_test_{query.strip('?')}.html"
                    path.write_text(html, encoding="utf-8")
        except HTTPError as e:
            print(f"  ✗ HTTP {e.code}")
            summary["attempts"].append({
                "method": "GET",
                "url": query,
                "status": e.code,
                "records": 0
            })
        except Exception as e:
            print(f"  ✗ Error: {e}")

    # Test with POST and different payloads
    for payload_info in payloads:
        url = RESULTS_ENDPOINT
        print(f"\nTesting POST with {payload_info['name']}: {payload_info['data'][:30]}")
        try:
            req = Request(url, data=payload_info['data'].encode('utf-8'), method="POST")
            for key, value in HEADERS.items():
                req.add_header(key, value)
            req.add_header("Cookie", cookie_str)
            
            with urlopen(req, context=ssl_context, timeout=30) as response:
                html = response.read().decode("utf-8", errors="ignore")
                records = get_record_count(html)
                summary["attempts"].append({
                    "method": "POST",
                    "payload": payload_info['name'],
                    "status": response.status,
                    "records": records
                })
                print(f"  ✓ Status {response.status}, {records} records")
        except HTTPError as e:
            print(f"  ✗ HTTP {e.code}")
            summary["attempts"].append({
                "method": "POST",
                "payload": payload_info['name'],
                "status": e.code
            })
        except Exception as e:
            print(f"  ✗ Error: {e}")

    return summary


if __name__ == "__main__":
    cookie = os.environ.get("COCONINO_COOKIE", "").strip()
    if not cookie:
        print("Set COCONINO_COOKIE env var")
        exit(1)
    
    result = test_post_pagination(cookie)
    print("\n" + "="*60)
    print(json.dumps(result, indent=2))
