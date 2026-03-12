from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener
from urllib.error import HTTPError

BASE_URL = "https://eagleassessor.coconino.az.gov:8444"
SEARCH_URL = f"{BASE_URL}/web/search/DOCSEARCH1213S1"
RESULTS_URL = f"{BASE_URL}/web/searchResults/DOCSEARCH1213S1"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    cookie = os.environ.get("COCONINO_COOKIE", "").strip()
    if not cookie:
        raise RuntimeError("Set COCONINO_COOKIE before running this script")

    opener = build_opener(HTTPCookieProcessor())
    base_headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
        "Referer": SEARCH_URL,
        "Connection": "keep-alive",
        "Cookie": cookie,
        "Cache-Control": "max-age=0, no-cache",
        "Expires": "Thu, 12 Mar 2026 19:47:43 GMT",
    }
    ajax_headers = {
        **base_headers,
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-GB,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "ajaxrequest": "true",
        "sec-ch-ua": '"Not:A-Brand";v="99", "Brave";v="145", "Chromium";v="145"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "sec-gpc": "1",
    }

    summary = {"searchPage": None, "pages": []}

    with opener.open(Request(SEARCH_URL, headers=base_headers), timeout=60) as response:
        body = response.read().decode("utf-8", errors="ignore")
        path = OUTPUT_DIR / "session_search_page.html"
        path.write_text(body, encoding="utf-8")
        summary["searchPage"] = {"status": response.status, "path": str(path), "length": len(body)}

    for page in (1, 2, 3):
        try:
            request = Request(f"{RESULTS_URL}?page={page}", headers=ajax_headers)
            with opener.open(request, timeout=60) as response:
                body = response.read().decode("utf-8", errors="ignore")
                path = OUTPUT_DIR / f"session_results_probe_page_{page}.html"
                path.write_text(body, encoding="utf-8")
                summary["pages"].append({"page": page, "status": response.status, "path": str(path), "length": len(body)})
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            path = OUTPUT_DIR / f"session_results_probe_page_{page}_error.html"
            path.write_text(body, encoding="utf-8")
            summary["pages"].append({"page": page, "status": exc.code, "path": str(path), "length": len(body)})

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
