#!/usr/bin/env python3
"""
Fetch paginated search results with complete browser headers.
Uses urllib with comprehensive header configuration.
"""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import ssl

BASE_URL = "https://eagleassessor.coconino.az.gov:8444"
SEARCH_ID = "DOCSEARCH1213S1"
SEARCH_URL = f"{BASE_URL}/web/search/{SEARCH_ID}"
RESULTS_ENDPOINT = f"{BASE_URL}/web/searchResults/{SEARCH_ID}"

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Complete headers from user's browser session
HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-GB,en;q=0.9",
    "Cache-Control": "max-age=0, no-cache",
    "Connection": "keep-alive",
    "Expires": "Thu, 12 Mar 2026 19:47:43 GMT",
    "Referer": SEARCH_URL,
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
    """Extract record count from HTML by looking for row elements."""
    try:
        count = html.count('<li class="ss-search-row">')
        return count
    except Exception as e:
        print(f"Error extracting count: {e}")
    return 0


def fetch_all_pages(cookie_str: str) -> dict:
    """Fetch all pages of results."""
    # Disable SSL verification for self-signed certificates
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    summary = {"pages": [], "total_records": 0}
    cookie_header = f"Cookie: {cookie_str}"

    for page_num in range(1, 4):
        print(f"\nFetching page {page_num}...")
        try:
            url = f"{RESULTS_ENDPOINT}?page={page_num}"
            print(f"  URL: {url}")
            
            req = Request(url)
            
            # Add all headers
            for key, value in HEADERS.items():
                req.add_header(key, value)
            
            # Add cookie header
            req.add_header("Cookie", cookie_str)
            
            print(f"  Sending request with {len(HEADERS)} headers + session cookie")
            
            with urlopen(req, context=ssl_context, timeout=30) as response:
                status = response.status
                html = response.read().decode("utf-8", errors="ignore")
                
                # Save successful response
                path = OUTPUT_DIR / f"page_{page_num}_proper_headers.html"
                path.write_text(html, encoding="utf-8")
                
                record_count = get_record_count(html)
                summary["pages"].append({
                    "page": page_num,
                    "status": status,
                    "path": str(path),
                    "length": len(html),
                    "records": record_count
                })
                print(f"  ✓ Status {status}, {record_count} rows found")
                
        except HTTPError as e:
            error_html = e.read().decode("utf-8", errors="ignore")
            path = OUTPUT_DIR / f"page_{page_num}_error_proper_headers.html"
            path.write_text(error_html, encoding="utf-8")
            print(f"  ✗ HTTP Error {e.code}")
            summary["pages"].append({
                "page": page_num,
                "status": e.code,
                "path": str(path),
                "length": len(error_html),
                "error": f"HTTP {e.code}"
            })
        except Exception as e:
            print(f"  ✗ Error: {e}")
            summary["pages"].append({
                "page": page_num,
                "error": str(e)
            })

    return summary


if __name__ == "__main__":
    cookie = os.environ.get("COCONINO_COOKIE", "").strip()
    if not cookie:
        print("Set COCONINO_COOKIE environment variable")
        exit(1)
    
    result = fetch_all_pages(cookie)
    print("\n" + "="*60)
    print(json.dumps(result, indent=2))
